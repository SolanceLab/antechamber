# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Antechamber's opt-in remote door: one public host, reachable only with a valid Cloudflare Access JWT.

Off unless [remote] host is set in the settings file. Then a request whose Host equals it may reach POST /mcp
and nothing else, and only with a `Cf-Access-Jwt-Assertion` header that verifies here: RS256 signature against the
team's JWKS, exact issuer, audience, and expiry. Anything that cannot be verified is refused (fail closed).
Tokens and keys are never logged. Standard library only."""
import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.request

import config as C

log = logging.getLogger("antechamber.remote")

SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")   # DigestInfo for SHA-256
REFETCH_EVERY = 60
MAX_AGE = 3600          # signing keys are re-read at least hourly, so a rotated-out key stops working within an hour
FETCH_TIMEOUT = 5
LEEWAY = 30
MAX_TOKEN = 8192


class RemoteAuthError(Exception):
    """A token that must be refused. The message names the reason, never the token."""


def settings():
    return {k: (C.get("remote", k, "") or "").strip() for k in ("host", "access_team", "access_aud")}


def host_of(h):
    """The request's Host, lowercased, port removed."""
    host = ((getattr(h, "headers", None) or {}).get("Host") or "").strip().lower()
    if host.startswith("["):
        return host[:host.index("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_remote(h):
    want = settings()["host"].lower()
    return bool(want) and host_of(h) == want


def _b64(s):
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except Exception:
        raise RemoteAuthError("bad base64")


def _json(s):
    try:
        v = json.loads(_b64(s))
    except ValueError:
        raise RemoteAuthError("bad json")
    if not isinstance(v, dict):
        raise RemoteAuthError("bad json")
    return v


def fetch_certs(team):
    """The team's JWKS document. HTTPS only, 5 s timeout."""
    if not team.startswith("https://"):
        raise RemoteAuthError("access_team must be https")
    with urllib.request.urlopen(team.rstrip("/") + "/cdn-cgi/access/certs", timeout=FETCH_TIMEOUT) as r:
        return json.loads(r.read(1_000_000))


class Jwks:
    """Keys by kid, replaced wholesale on every fetch (a retired key is dropped). Fetched when the cache is older
    than MAX_AGE (always), or when a kid is unknown (at most once per REFETCH_EVERY seconds). A cache older than
    MAX_AGE that cannot be refreshed is not trusted: fail closed."""

    def __init__(self, fetch=None):
        self.fetch, self.keys, self.last, self.fetched, self.lock = fetch or fetch_certs, {}, None, None, threading.Lock()

    def get(self, team, kid, now):
        with self.lock:
            stale = self.fetched is None or now - self.fetched >= MAX_AGE
            unknown = kid not in self.keys and (self.last is None or now - self.last >= REFETCH_EVERY)
            if stale or unknown:
                self.last = now
                try:
                    doc = self.fetch(team)
                    self.keys = {k["kid"]: k for k in doc.get("keys", []) if isinstance(k, dict) and k.get("kid")}
                    self.fetched = now
                except Exception as e:
                    log.warning("remote: JWKS fetch failed (%s)", type(e).__name__)
                    if stale:
                        self.keys = {}                      # too old to trust; refuse until a fetch succeeds
                    raise RemoteAuthError("jwks unavailable")
            k = self.keys.get(kid)
        if not k or k.get("kty") != "RSA" or not k.get("n") or not k.get("e"):
            raise RemoteAuthError("unknown kid")
        return int.from_bytes(_b64(k["n"]), "big"), int.from_bytes(_b64(k["e"]), "big")


JWKS = Jwks()


def _num(claims, name, now, future):
    v = claims.get(name)
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RemoteAuthError(f"bad {name}")
    if future and v > now + LEEWAY:
        raise RemoteAuthError(f"{name} in the future")


def verify(token, now=None, fetch=None):
    """Claims of a valid Access token, or RemoteAuthError."""
    s = settings()
    team, aud = s["access_team"].rstrip("/"), s["access_aud"]
    if not team or not aud:
        raise RemoteAuthError("remote not configured")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN:
        raise RemoteAuthError("missing or oversize token")
    parts = token.split(".")
    if len(parts) != 3:
        raise RemoteAuthError("not three parts")
    header, claims, sig = _json(parts[0]), _json(parts[1]), _b64(parts[2])
    if header.get("alg") != "RS256":
        raise RemoteAuthError("alg not RS256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise RemoteAuthError("no kid")
    now = time.time() if now is None else now
    jwks = JWKS if fetch is None else Jwks(fetch)
    n, e = jwks.get(team, kid, now)
    size = (n.bit_length() + 7) // 8
    if len(sig) != size:
        raise RemoteAuthError("bad signature")
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(size, "big")
    t = SHA256_PREFIX + hashlib.sha256((parts[0] + "." + parts[1]).encode()).digest()
    want = b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t
    if not hmac.compare_digest(em, want):
        raise RemoteAuthError("bad signature")
    iss = claims.get("iss")
    if not isinstance(iss, str) or iss.rstrip("/") != team:
        raise RemoteAuthError("wrong issuer")
    a = claims.get("aud")
    if aud not in ([a] if isinstance(a, str) else a if isinstance(a, list) else []):
        raise RemoteAuthError("wrong audience")
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= now - LEEWAY:
        raise RemoteAuthError("expired")
    _num(claims, "nbf", now, True)
    _num(claims, "iat", now, True)
    return claims


def admit(h, path, method):
    """True when this request on the remote host may proceed; sets h.remote_identity. Logs the reason when refused."""
    host = settings()["host"].lower()
    hdrs = getattr(h, "headers", None) or {}
    origin = (hdrs.get("Origin") or "").strip().lower()
    if path != "/mcp" or method != "POST" or (origin and origin != "https://" + host):
        log.warning("remote: refused %s %s", method, "path" if path != "/mcp" else "origin")
        return False
    try:
        claims = verify(hdrs.get("Cf-Access-Jwt-Assertion"))
    except RemoteAuthError as e:
        log.warning("remote: refused (%s)", e)
        return False
    ident = claims.get("email") or claims.get("sub")
    if not ident:
        log.warning("remote: refused (no identity)")
        return False
    h.remote_identity = ident
    return True
