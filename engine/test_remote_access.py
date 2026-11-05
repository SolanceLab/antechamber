# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The remote door: Access JWT verification and the local_request / mcp_http rules around it.
The test RSA key is generated at test time with openssl (never committed); without openssl the signature tests skip.
Run: python3 -m unittest test_remote_access -v   (from engine/)"""
import base64
import hashlib
import io
import json
import re
import subprocess
import time
import unittest
from unittest import mock

import config as C
import mcp_http as M
import recall_service as R
import remote_access as RA

AUD = "aud-tag-1"
TEAM = "https://myteam.cloudflareaccess.com"
HOST = "antechamber.example.com"
CFG = {"remote": {"host": HOST, "access_team": TEAM, "access_aud": AUD}}


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_key():
    pem = subprocess.run(["openssl", "genrsa", "2048"], capture_output=True, check=True).stdout
    txt = subprocess.run(["openssl", "rsa", "-text", "-noout"], input=pem, capture_output=True, check=True).stdout.decode()
    def field(name):
        m = re.search(name + r":\s*((?:\s+[0-9a-f:]+\n?)+)", txt)
        return int(re.sub(r"[^0-9a-f]", "", m.group(1)), 16)
    m = re.search(r"modulus:\s*((?:\s+[0-9a-f:]+\n?)+)", txt)
    n = int(re.sub(r"[^0-9a-f]", "", m.group(1)), 16)
    e = int(re.search(r"publicExponent:\s*(\d+)", txt).group(1))
    d = field("privateExponent")
    return n, e, d


def sign(n, d, signing_input):
    size = (n.bit_length() + 7) // 8
    t = RA.SHA256_PREFIX + hashlib.sha256(signing_input.encode()).digest()
    em = b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t
    return b64(pow(int.from_bytes(em, "big"), d, n).to_bytes(size, "big"))


class Door(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.n, cls.e, cls.d = make_key()
        except (OSError, subprocess.CalledProcessError, AttributeError) as ex:
            raise unittest.SkipTest(f"openssl not usable here, signature tests skipped ({type(ex).__name__})")
        size = (cls.n.bit_length() + 7) // 8
        cls.jwks = {"keys": [{"kid": "k1", "kty": "RSA", "n": b64(cls.n.to_bytes(size, "big")),
                              "e": b64(cls.e.to_bytes(4, "big").lstrip(b"\0"))}]}

    def setUp(self):
        p = mock.patch.dict(C._d, CFG)
        p.start()
        self.addCleanup(p.stop)
        self.now = 1_800_000_000
        self.fetch = lambda team: self.jwks

    def token(self, header=None, claims=None, key=None, tamper=False):
        h = {"alg": "RS256", "kid": "k1", "typ": "JWT"}
        h.update(header or {})
        c = {"iss": TEAM, "aud": [AUD], "exp": self.now + 600, "iat": self.now - 5, "email": "a@example.com"}
        c.update(claims or {})
        for k in [k for k, v in c.items() if v is None]:
            del c[k]
        hp, cp = b64(json.dumps(h).encode()), b64(json.dumps(c).encode())
        sig = sign(self.n, self.d, hp + "." + cp)
        if tamper:
            cp = b64(json.dumps(dict(c, email="evil@example.com")).encode())
        return f"{hp}.{cp}.{sig}"

    def check(self, tok):
        return RA.verify(tok, now=self.now, fetch=self.fetch)

    def refused(self, tok):
        with self.assertRaises(RA.RemoteAuthError):
            self.check(tok)

    def test_valid_token_gives_claims(self):
        self.assertEqual(self.check(self.token())["email"], "a@example.com")
        self.assertEqual(self.check(self.token(claims={"aud": AUD, "iss": TEAM + "/"}))["aud"], AUD)

    def test_forged_tokens_are_refused(self):
        self.refused(self.token(tamper=True))
        self.refused(self.token(header={"kid": "other"}))
        self.refused(self.token(header={"kid": None}))
        self.refused(self.token(claims={"exp": self.now - 31}))
        self.refused(self.token(claims={"exp": None}))
        self.refused(self.token(claims={"nbf": self.now + 31}))
        self.refused(self.token(claims={"iss": "https://evil.cloudflareaccess.com"}))
        self.refused(self.token(claims={"aud": ["someone-else"]}))
        self.refused(self.token(header={"alg": "none"}))
        self.refused(self.token(header={"alg": "HS256"}))
        self.refused("x" * (RA.MAX_TOKEN + 1))
        self.refused("")
        self.refused(None)
        self.refused("a.b")

    def test_leeway_accepts_just_expired(self):
        self.check(self.token(claims={"exp": self.now - 10, "nbf": self.now + 10}))

    def test_fetch_failure_fails_closed(self):
        def boom(team):
            raise OSError("down")
        with self.assertRaises(RA.RemoteAuthError):
            RA.verify(self.token(), now=self.now, fetch=boom)

    def test_unknown_kid_refetches_at_most_once_a_minute(self):
        calls = []
        j = RA.Jwks(lambda t: calls.append(1) or self.jwks)
        j.get(TEAM, "k1", 1000)
        for _ in range(3):
            with self.assertRaises(RA.RemoteAuthError):
                j.get(TEAM, "nope", 1010)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(RA.RemoteAuthError):
            j.get(TEAM, "nope", 1061)
        self.assertEqual(len(calls), 2)

    def test_known_key_is_refreshed_and_retired_key_dropped(self):
        # Codex review 5 Nov: a cached key must not stay trusted forever after Cloudflare rotates it out.
        docs = [self.jwks, {"keys": [dict(self.jwks["keys"][0], kid="k2")]}]
        calls = []
        j = RA.Jwks(lambda t: calls.append(1) or docs[min(len(calls) - 1, 1)])
        j.get(TEAM, "k1", 1000)
        j.get(TEAM, "k1", 1000 + RA.MAX_AGE - 1)             # still fresh: no refetch
        self.assertEqual(len(calls), 1)
        with self.assertRaises(RA.RemoteAuthError):          # past MAX_AGE: refreshed, k1 gone
            j.get(TEAM, "k1", 1000 + RA.MAX_AGE)
        self.assertEqual(len(calls), 2)
        j.get(TEAM, "k2", 1000 + RA.MAX_AGE + 1)

    def test_stale_cache_with_failing_fetch_fails_closed(self):
        state = {"down": False}

        def fetch(team):
            if state["down"]:
                raise OSError("down")
            return self.jwks
        j = RA.Jwks(fetch)
        j.get(TEAM, "k1", 1000)
        state["down"] = True
        with self.assertRaises(RA.RemoteAuthError):
            j.get(TEAM, "k1", 1000 + RA.MAX_AGE)
        with self.assertRaises(RA.RemoteAuthError):          # the old key is not trusted again later either
            j.get(TEAM, "k1", 1000 + RA.MAX_AGE + 1)

    def test_non_https_team_is_refused(self):
        with self.assertRaises(RA.RemoteAuthError):
            RA.fetch_certs("http://myteam.cloudflareaccess.com")

    # ---- the request rules, through local_request and the MCP route
    def handler(self, host=HOST, path="/mcp", method="POST", jwt=None, origin=None, body=b'{"jsonrpc":"2.0","id":1,"method":"ping"}'):
        h = R.H.__new__(R.H)
        h.path, h.command = path, method
        h.headers = {"Host": host, "Content-Length": str(len(body))}
        if jwt:
            h.headers["Cf-Access-Jwt-Assertion"] = jwt
        if origin:
            h.headers["Origin"] = origin
        h.rfile = io.BytesIO(body)
        h.out = {}
        h._send = lambda code, obj: h.out.update(code=code, obj=obj)
        return h

    def door(self, **kw):
        h = self.handler(**kw)
        with mock.patch.object(RA, "JWKS", RA.Jwks(self.fetch)), mock.patch.object(RA.time, "time", return_value=self.now):
            return h, R.local_request(h)

    def test_remote_host_with_valid_jwt_is_admitted_and_skips_the_key(self):
        h, ok = self.door(jwt=self.token())
        self.assertTrue(ok)
        self.assertEqual(h.remote_identity, "a@example.com")
        with mock.patch.dict("os.environ", {}, clear=False), mock.patch.object(M, "key", return_value=""):
            M.route_mcp(h)                      # no key configured at all: Access is the lock
        self.assertEqual(h.out["code"], 200)

    def test_remote_host_refusals(self):
        tok = self.token()
        for kw in ({}, {"jwt": "junk"}, {"jwt": tok, "path": "/health"}, {"jwt": tok, "path": "/mcp2"},
                   {"jwt": tok, "method": "GET"}, {"jwt": tok, "origin": "https://evil.example"},
                   {"jwt": self.token(tamper=True)}):
            self.assertFalse(self.door(**kw)[1], kw)
        self.assertTrue(self.door(jwt=tok, origin="https://" + HOST)[1])
        self.assertTrue(self.door(jwt=tok, path="/mcp?x=1")[1])

    def test_local_host_is_unchanged_and_still_needs_the_key(self):
        h, ok = self.door(host="127.0.0.1:8790", jwt=self.token())
        self.assertTrue(ok)
        self.assertIsNone(h.remote_identity)
        with mock.patch.object(M, "key", return_value="k1"):
            M.route_mcp(h)
        self.assertEqual(h.out["code"], 401)

    def test_remote_absent_means_the_host_is_refused_as_today(self):
        with mock.patch.dict(C._d):
            C._d.pop("remote")
            h = self.handler(jwt=self.token())
            self.assertFalse(R.local_request(h))

    def test_remote_errors_carry_no_local_paths(self):
        with mock.patch.object(M.antechamber_recall, "recall", side_effect=RuntimeError("/home/someone/secret.db")):
            local = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "recall", "arguments": {"query": "q"}}})
            remote = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "recall", "arguments": {"query": "q"}}}, remote=True)
        self.assertIn("/home/someone", json.dumps(local))
        self.assertNotIn("/Users", json.dumps(remote))


if __name__ == "__main__":
    unittest.main()
