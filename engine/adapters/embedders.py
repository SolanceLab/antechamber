# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Embedders: turn text into vectors, through a local Ollama or any OpenAI-compatible endpoint.

    [embedder]
    backend = "ollama"            # or "openai" (any OpenAI-compatible /embeddings endpoint)
    url = "http://localhost:11434"
    model = "nomic-embed-text"
    dims = 768                    # required: every vector is checked against it
    api_key_env = ""              # NAME of an environment variable holding the key; the key never sits in the file
    allow_remote_data = false     # a non-local endpoint is refused unless this is true
    batch_size = 32
    timeout = 30                  # seconds, indexing; a question's embedding uses query_timeout (10)

An endpoint is local when its host is "localhost" or a loopback address; anything else is remote and receives
the text of every passage the adapters index, so it must be allowed out loud.
"""
import array
import hashlib
import http.client
import ipaddress
import json
import logging
import math
import os
import socket
import ssl
import threading
import time
import urllib.parse

log = logging.getLogger("antechamber.adapters")

MAX_RESPONSE = 1 << 20      # 1 MB: services and single-text embeddings


class ConfigError(ValueError):
    """A setting that cannot work; the message is written for the person who owns the config."""


class EmbedError(RuntimeError):
    pass


# ---------------------------------------------------------------- HTTP (shared with the service adapter)
def parse_url(url, what):
    """Split and check a URL: http/https only, a host, no user:password inside it."""
    if not isinstance(url, str) or not url:
        raise ConfigError(f"{what}: url is missing")
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https"):
        raise ConfigError(f"{what}: url must start with http:// or https:// (got {u.scheme or 'nothing'})")
    if u.username is not None or u.password is not None or "@" in u.netloc:
        raise ConfigError(f"{what}: url must not carry a user name or password; use api_key_env")
    if not u.hostname:
        raise ConfigError(f"{what}: url has no host")
    try:
        port = u.port
    except ValueError:
        raise ConfigError(f"{what}: url has a bad port") from None
    return u.scheme, u.hostname, port or (443 if u.scheme == "https" else 80), u.path or "", u.query


def host_is_local(host):
    """True for 'localhost' (and *.localhost) or a loopback address literal. Names are not resolved:
    an unknown name counts as remote, which is the safe side."""
    h = (host or "").strip("[]").lower()
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _blocked(ip):
    a = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast
            or a.is_unspecified or str(a) in ("169.254.169.254", "100.100.100.200")
            or a in ipaddress.ip_network("100.64.0.0/10"))


def resolve_checked(host, port, allow_local, allow_loopback=False):
    """Resolve once; refuse private / loopback / link-local / metadata addresses unless allow_local.
    Returns the address to connect to, so the check and the connection use the same answer."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise EmbedError(f"cannot resolve {host}: {e}") from None
    addrs = [i[4][0] for i in infos]
    if not addrs:
        raise EmbedError(f"cannot resolve {host}")
    if not allow_local:
        bad = [a for a in addrs if _blocked(a) and not (allow_loopback and ipaddress.ip_address(a).is_loopback)]
        if bad:
            raise EmbedError(f"{host} resolves to a private or local address ({bad[0]}); "
                             "set allow_local = true if that is intended")
    return addrs[0]


def post_json(url, body, headers=None, timeout=5.0, max_bytes=MAX_RESPONSE, allow_local=False, what="request", allow_loopback=False):
    """POST JSON, read at most max_bytes, no redirects followed, return the decoded JSON."""
    scheme, host, port, path, query = parse_url(url, what)
    ip = resolve_checked(host, port, allow_local, allow_loopback)
    if scheme == "https":
        ctx = ssl.create_default_context()
        raw = socket.create_connection((ip, port), timeout=timeout)
        sock = ctx.wrap_socket(raw, server_hostname=host)
    else:
        sock = socket.create_connection((ip, port), timeout=timeout)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = sock
    try:
        data = json.dumps(body).encode()
        hdrs = {"Content-Type": "application/json", "Accept": "application/json", "Content-Length": str(len(data))}
        hdrs.update(headers or {})
        conn.request("POST", (path or "/") + (("?" + query) if query else ""), body=data, headers=hdrs)
        resp = conn.getresponse()
        if resp.status != 200:
            raise EmbedError(f"{what}: {host} answered HTTP {resp.status}")    # 3xx included: never followed
        payload = resp.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise EmbedError(f"{what}: answer from {host} is larger than {max_bytes} bytes")
        try:
            return json.loads(payload)
        except ValueError:
            raise EmbedError(f"{what}: answer from {host} is not JSON") from None
    finally:
        conn.close()


def env_key(name, what):
    """The value of the environment variable `name`; '' when no name is configured."""
    if not name:
        return ""
    v = os.environ.get(name, "")
    if not v:
        raise ConfigError(f"{what}: environment variable {name} is not set")
    return v


# ---------------------------------------------------------------- vectors
def clean_vector(v, dims):
    """A list of floats of length dims, finite, non-zero -> unit-length list. Anything else -> EmbedError."""
    if not isinstance(v, list) or len(v) != dims:
        raise EmbedError(f"vector has {len(v) if isinstance(v, list) else 'no'} dimensions, expected {dims}")
    try:
        f = [float(x) for x in v]
    except (TypeError, ValueError):
        raise EmbedError("vector holds a non-number") from None
    if not all(math.isfinite(x) for x in f):
        raise EmbedError("vector holds NaN or infinity")
    n = math.sqrt(sum(x * x for x in f))
    if n == 0.0:
        raise EmbedError("vector is all zeros")
    return [x / n for x in f]


def pack(vec):
    return array.array("f", vec).tobytes()


def unpack(blob):
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()


# ---------------------------------------------------------------- embedders
class Embedder:
    """embed(text) -> unit vector; embed_batch(texts) -> unit vectors. query(text) adds a short cache so
    several adapters asking about the same question make one call."""
    backend = "none"
    model = ""
    dims = 0
    is_local = True
    host = ""
    batch_size = 32

    def __init__(self):
        self._qlock = threading.Lock()
        self._qcache = {}            # text -> (time, vector or exception)

    def embed(self, text, timeout=None):
        return self.embed_batch([text], timeout=timeout)[0]

    def embed_batch(self, texts, timeout=None):
        raise NotImplementedError

    def fingerprint(self):
        return {"backend": self.backend, "model": self.model, "dims": self.dims}

    def query(self, text, timeout=10.0):
        with self._qlock:            # one call per question; the others wait for its answer
            hit = self._qcache.get(text)
            now = time.monotonic()
            if hit and now - hit[0] < (300 if not isinstance(hit[1], Exception) else 10):
                if isinstance(hit[1], Exception):
                    raise hit[1]
                return hit[1]
            try:
                v = self.embed(text, timeout=timeout)
            except Exception as e:
                self._qcache[text] = (now, e)
                raise
            if len(self._qcache) > 64:
                self._qcache.clear()
            self._qcache[text] = (now, v)
            return v


class _HTTPEmbedder(Embedder):
    def __init__(self, cfg):
        super().__init__()
        self.url = cfg.get("url") or ""
        self.model = cfg.get("model") or ""
        self.dims = int(cfg.get("dims") or cfg.get("dimensions") or 0)
        self.timeout = float(cfg.get("timeout", 30))
        self.batch_size = max(1, int(cfg.get("batch_size", 32)))
        self.send_dims = bool(cfg.get("send_dimensions", False))
        if not self.model:
            raise ConfigError("[embedder] model is missing")
        if self.dims <= 0:
            raise ConfigError("[embedder] dims is missing (the length of the model's vectors)")
        _, self.host, _, _, _ = parse_url(self.url, "[embedder]")
        self.is_local = host_is_local(self.host)
        self.key = env_key(cfg.get("api_key_env") or "", "[embedder] api_key_env")
        # Room for a full batch of numbers written as JSON text, on top of the plain 1 MB cap.
        self.max_bytes = MAX_RESPONSE + self.batch_size * self.dims * 32
        # loopback (a model on this machine) is always fine; any other private / link-local / metadata address
        # needs allow_local = true — a config must not quietly turn the embedder into a probe of the network
        self.allow_local = bool(cfg.get("allow_local", False))

    def fingerprint(self):
        """Everything that changes what a vector means: backend, model, dims, send_dimensions and the normalized
        endpoint actually called (scheme, host, port, path, query). Timeouts, batch size and keys are left out."""
        scheme, host, port, path, query = parse_url(self.endpoint, "[embedder]")
        return {"backend": self.backend, "model": self.model, "dims": self.dims, "send_dimensions": self.send_dims,
                "endpoint": {"scheme": scheme.lower(), "host": host.lower(), "port": port,
                             "path": path.rstrip("/") or "/", "query": query}}

    def embed_batch(self, texts, timeout=None):
        out = []
        for i in range(0, len(texts), self.batch_size):
            chunk = list(texts[i:i + self.batch_size])
            vecs = self._call(chunk, timeout or self.timeout)
            if len(vecs) != len(chunk):
                raise EmbedError(f"embedder returned {len(vecs)} vectors for {len(chunk)} texts")
            out += [clean_vector(v, self.dims) for v in vecs]
        return out


class OllamaEmbedder(_HTTPEmbedder):
    backend = "ollama"

    def __init__(self, cfg):
        super().__init__(cfg)
        base = self.url.rstrip("/")
        for tail in ("/api/embed", "/api/embeddings"):
            if base.endswith(tail):
                base = base[: -len(tail)]
        self.endpoint = base + "/api/embed"

    def _call(self, texts, timeout):
        body = {"model": self.model, "input": texts}
        if self.send_dims:
            body["dimensions"] = self.dims
        hdr = {"Authorization": "Bearer " + self.key} if self.key else {}
        r = post_json(self.endpoint, body, hdr, timeout, self.max_bytes, allow_local=self.allow_local, what="embedder", allow_loopback=True)
        v = r.get("embeddings") if isinstance(r, dict) else None
        if not isinstance(v, list):
            raise EmbedError("embedder answer has no 'embeddings' list")
        return v


class OpenAIEmbedder(_HTTPEmbedder):
    backend = "openai"

    def __init__(self, cfg):
        super().__init__(cfg)
        base = self.url.rstrip("/")
        if base.endswith("/embeddings"):
            base = base[: -len("/embeddings")]
        self.endpoint = base + "/embeddings"

    def _call(self, texts, timeout):
        body = {"model": self.model, "input": texts}
        if self.send_dims:
            body["dimensions"] = self.dims
        hdr = {"Authorization": "Bearer " + self.key} if self.key else {}
        r = post_json(self.endpoint, body, hdr, timeout, self.max_bytes, allow_local=self.allow_local, what="embedder", allow_loopback=True)
        data = r.get("data") if isinstance(r, dict) else None
        if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
            raise EmbedError("embedder answer has no 'data' list")
        idx = [d.get("index") for d in data]
        if any(type(i) is not int for i in idx) or sorted(idx) != list(range(len(texts))):
            raise EmbedError("embedder answer's indices are not each of 0..n-1 exactly once")
        data = sorted(data, key=lambda d: d["index"])
        return [d.get("embedding") for d in data]


class HashEmbedder(Embedder):
    """Deterministic, offline, for tests and demos: each word is hashed onto a dimension, the counts are
    normalised. Texts sharing words land near each other. Not a model; never use for real recall."""
    backend = "hash"

    def __init__(self, dims=64):
        super().__init__()
        self.dims, self.model = dims, f"hash-{dims}"

    def embed_batch(self, texts, timeout=None):
        import re as _re
        out = []
        for t in texts:
            v = [0.0] * self.dims
            for w in _re.findall(r"\w{3,}", (t or "").lower()):
                h = int.from_bytes(hashlib.sha256(w.encode()).digest()[:4], "big")
                v[h % self.dims] += 1.0 if (h >> 31) & 1 == 0 else -1.0
            if not any(v):
                v[0] = 1.0
            out.append(clean_vector(v, self.dims))
        return out


BACKENDS = {"ollama": OllamaEmbedder, "openai": OpenAIEmbedder}


def from_config(cfg, users=()):
    """Build the embedder from the whole config dict ([embedder] table). None when no table is present.

    A remote endpoint without allow_remote_data = true is refused: the engine must not start. When allowed,
    the destination host and the adapters that send it data are logged."""
    e = (cfg or {}).get("embedder")
    if not e:
        return None
    backend = e.get("backend", "ollama")
    if backend not in BACKENDS:
        raise ConfigError(f"[embedder] backend must be one of {sorted(BACKENDS)} (got {backend!r})")
    emb = BACKENDS[backend](e)
    if not emb.is_local:
        if e.get("allow_remote_data") is not True:
            raise ConfigError(f"[embedder] {emb.host} is not on this machine; every indexed passage would be sent "
                              "there. Set allow_remote_data = true to allow it.")
        log.warning("embedder sends text to remote host %s (adapters: %s)", emb.host, ", ".join(users) or "none")
    return emb


_SHARED, _SHARED_LOCK = {}, threading.Lock()


def shared(cfg):
    """One embedder per [embedder] table, shared by every adapter built from the same settings (so a question
    is embedded once). None when no [embedder] table is present: adapters then search by words only."""
    e = (cfg or {}).get("embedder")
    if not e:
        return None
    a = (cfg or {}).get("adapters") or {}
    users = [i.get("id", "?") for k in ("notes", "chats", "pgvector") for i in (a.get(k) or [])]
    key = json.dumps(e, sort_keys=True, default=str)
    with _SHARED_LOCK:
        if key not in _SHARED:
            _SHARED[key] = from_config(cfg, users)
        return _SHARED[key]
