#!/usr/bin/env python3
# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The local recall service: Antechamber recall over HTTP on localhost.

GET /antechamber/recall?q=...&limit=...   one call, every default shelf searched, winners only
GET /health                              ok (settings loaded, adapters installed, every extension loaded) + config
Everything else comes from extensions named in the settings file ([service] extensions = ["module:fn"]); each
is called with this module at startup and adds routes (add_get, add_post, add_verb) and hooks (add_hook).
Public adapters' indexes refresh in the background (start_refresher), never inside a recall."""
import json, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import config as C   # machine settings (~/.config/antechamber/config.toml), never in code
import antechamber_recall
import remote_access

LIVE_CHRONO=False    # True when an extension hands the CHRONO ordering to its caller: answers then say order=chrono

# ---- the generic recall service: routes, hooks, extensions
GET_ROUTES, POST_ROUTES = {}, {}
EXTENSIONS_LOADED = []
VERB_ROUTES = {}   # (method, path) -> fn(handler) for PUT/DELETE/PATCH; unregistered pairs keep the stdlib 501
HOOKS = {"before_recall": [], "after_recall": [], "on_error": [], "health_extra": []}

def add_get(path, fn):
    """fn(handler, qs) answers GET path (it calls handler._send(code, obj))."""
    GET_ROUTES[path] = fn

def add_post(path, fn):
    """fn(handler) answers POST path."""
    POST_ROUTES[path] = fn

def add_verb(method, path, fn):
    """fn(handler) answers method on path; POST goes to POST_ROUTES, PUT/DELETE/PATCH to VERB_ROUTES."""
    m = method.upper()
    if m == "POST":
        POST_ROUTES[path] = fn
    elif m in ("PUT", "DELETE", "PATCH"):
        VERB_ROUTES[(m, path)] = fn
    else:
        raise ValueError(m)

def add_hook(name, fn):
    """before_recall(q) -> (early answer or None, extra keys); after_recall(q, out); on_error(q, e) -> (code, obj);
    health_extra() -> extra /health keys. Hooks run in the order they were added; adding one twice is a no-op."""
    if fn not in HOOKS[name]:
        HOOKS[name].append(fn)

def route_recall(h, qs):
    q=(qs.get("q") or [""])[0]
    try: n=int((qs.get("limit") or ["8"])[0])
    except ValueError: n=8
    if not q.strip(): return h._send(400,{"error":"q required"})
    extras={}
    for hook in HOOKS["before_recall"]:
        early, more = hook(q)
        if early is not None: return h._send(200, early)
        extras.update(more or {})
    try:
        out=antechamber_recall.recall(q, n=n, windows=False)
        # v2.2 DATES: the resolved dates ride inside the line text, so every renderer shows them with no
        # change of its own: "...yesterday...  (yesterday = Sunday 7 May 2023)"
        for w in out["winners"]:
            for ln in w.get("lines") or []:
                notes=ln.pop("dates", None)
                if notes:
                    ln["message"]=f'{ln.get("message") or ""}  ({"; ".join(notes)})'
        if antechamber_recall.WIDE and antechamber_recall.is_list_question(q): out["wide"]=True
        if LIVE_CHRONO: out["order"]="chrono"
        for hook in HOOKS["after_recall"]:
            hook(q, out)
        out.update(extras)
        return h._send(200,out)
    except Exception as e:
        code, obj = HOOKS["on_error"][0](q, e) if HOOKS["on_error"] else (500, {"error": str(e)[:200]})
        return h._send(code, obj)

REFRESHED=set()   # names of public shelves whose index has refreshed without error at least once


def public_ready():
    """True when every installed public adapter's index has had one good refresh (vacuously true with none):
    shelves without an index (service, pgvector) have nothing to refresh and never block it; until then recall could answer empty, so /health must not say ok."""
    return all(s.name in REFRESHED for s in antechamber_recall.PUBLIC_SHELVES if getattr(s, "index", None) is not None)


def _note_round(results):
    for name, r in results.items():
        if isinstance(r, dict) and "skipped" in r:
            continue                  # another process held the index: nothing was refreshed — readiness unchanged
        if isinstance(r, dict) and "error" not in r:
            REFRESHED.add(name)
        else:
            REFRESHED.discard(name)


def route_health(h, qs):
    d={"ok":C.LOADED and bool(antechamber_recall.SHELVES) and public_ready() and len(EXTENSIONS_LOADED)==len(C.SERVICE_EXTENSIONS),
       "config":C.PATH if C.LOADED else "MISSING"}
    for hook in HOOKS["health_extra"]:
        d.update(hook())
    return h._send(200,d)

add_get("/antechamber/recall", route_recall)
add_get("/health", route_health)

# Requests must name a local host (and, from a browser, a local origin): a page on another site cannot reach this
# service through DNS rebinding. Extra host names (e.g. a tunnel's) go in [service] allowed_hosts.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def local_request(h):
    h.remote_identity = None                     # set only by remote_access.admit, for a verified Access request
    if remote_access.is_remote(h):               # the one opt-in public host: /mcp only, with a valid Access JWT
        return remote_access.admit(h, urllib.parse.urlparse(getattr(h, "path", "") or "").path, getattr(h, "command", ""))
    allowed = LOCAL_HOSTS | {str(x).lower() for x in (C.get("service", "allowed_hosts", []) or [])}
    hdrs = getattr(h, "headers", None) or {}     # a real request always has headers; in-process callers may not
    host = (hdrs.get("Host") or "").strip().lower()
    name = host[:host.index("]") + 1] if host.startswith("[") else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if host and name not in allowed:        # no Host at all (HTTP/1.0 tools) cannot be a browser's rebinding request
        return False
    origin = (hdrs.get("Origin") or "").strip().lower()
    if origin and origin != "null":
        o = urllib.parse.urlparse(origin).hostname or ""
        if o not in allowed and f"[{o}]" not in allowed:
            return False
    return True


class H(BaseHTTPRequestHandler):
    remote_identity = None
    def log_message(self,*a): pass
    def do_POST(self):
        if not local_request(self): return self._send(403,{"error":"not a local request"})
        fn=POST_ROUTES.get(urllib.parse.urlparse(self.path).path)
        if fn is None: return self._send(404,{"error":"not found"})
        return fn(self)
    def do_GET(self):
        if not local_request(self): return self._send(403,{"error":"not a local request"})
        u=urllib.parse.urlparse(self.path); qs=urllib.parse.parse_qs(u.query)
        fn=GET_ROUTES.get(u.path)
        if fn is None: return self._send(404,{"error":"not found"})
        return fn(self, qs)
    def _other(self):
        fn=VERB_ROUTES.get((self.command, urllib.parse.urlparse(self.path).path))
        if fn is None: return self.send_error(501, "Unsupported method (%r)" % self.command)
        if not local_request(self): return self._send(403,{"error":"not a local request"})
        return fn(self)
    do_PUT = do_DELETE = do_PATCH = _other
    def _send(self,code,obj):
        b=json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b)))
        self.end_headers(); self.wfile.write(b)


def load_extensions(specs=None):
    """Call each configured "module:fn" with this service. An import error raises: a service missing an
    extension it was told to load must crash (health would roll the deploy back), never run half-dressed."""
    import importlib
    specs=C.SERVICE_EXTENSIONS if specs is None else specs
    if specs: antechamber_recall._import_root()
    for spec in specs:
        if spec in EXTENSIONS_LOADED: continue
        mod,_,fn=spec.partition(":")
        getattr(importlib.import_module(mod), fn or "register")(sys.modules[__name__])
        EXTENSIONS_LOADED.append(spec)


load_extensions()


def start_refresher(minutes=None):
    """Refresh the public adapters' local indexes now and every [index] refresh_minutes (default 15) in a
    background thread, never inside a recall. /health reports ok only after each public index has refreshed once. Returns stop(), or None when no public adapter is installed."""
    shelves = list(antechamber_recall.PUBLIC_SHELVES)
    if not shelves:
        return None
    import adapters
    return adapters.start_refresher(shelves, C.INDEX_REFRESH_MINUTES if minutes is None else minutes, on_round=_note_round)


def make_server(port=None):
    return ThreadingHTTPServer(("127.0.0.1",port or C.SERVICE_PORT),H)


if __name__=="__main__":
    print(f"local recall service on 127.0.0.1:{C.SERVICE_PORT} (config {C.PATH if C.LOADED else 'MISSING'})",flush=True)
    start_refresher()
    make_server().serve_forever()
