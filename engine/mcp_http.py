# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Antechamber's own MCP endpoint: POST /mcp on the local recall service (MCP over HTTP, JSON responses).

Tools: `recall` (one question, the winners from every configured store), `search_store` (the same, one store) and
`open_item` (one item in full, for stores that can open items), all as readable text.
Off unless a key is set: [mcp] key = "..." in the settings file, or the ANTECHAMBER_MCP_KEY environment
variable. Every request must carry `Authorization: Bearer <key>`. The service only listens on 127.0.0.1 and
refuses requests whose Host or Origin is not local (recall_service.local_request), against DNS rebinding.
A failing store's error text (which can name local paths) goes back to this keyed local client only.
Standard library only."""
import hmac
import json
import os

import config as C
import antechamber_recall

TOOL = {
    "name": "recall",
    "description": ("Search everything you have stored (notes, past conversations, memories, other connected stores) "
                    "with one question, and get back only the best matches, dated, with their source. Use it when "
                    "the answer may already be written down somewhere."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The question or topic to look for, in plain words."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "How many winners (default 8)."},
        },
        "required": ["query"],
    },
}
SERVER_INFO = {"name": "antechamber", "version": "1.0.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
OPEN_CAP = 20000
SEARCH_STORE = {
    "name": "search_store",
    "description": ("Search ONE of your stored collections (for example only your notes, or only your past "
                    "conversations) instead of everything. Give the collection's name as `store`. If the name is "
                    "wrong, the answer lists the names you can use."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "store": {"type": "string", "description": "The name of the collection to search."},
            "query": {"type": "string", "description": "The question or topic to look for, in plain words."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "How many winners (default 8)."},
        },
        "required": ["store", "query"],
    },
}
OPEN_ITEM = {
    "name": "open_item",
    "description": ("Read one item in full: the whole note, conversation or memory that a search result came from. "
                    "Give the collection's name as `store` and the item's `id` exactly as the search showed it. "
                    "Long items are cut at 20000 characters, and the answer says so."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "store": {"type": "string", "description": "The name of the collection the item is in."},
            "id": {"type": "string", "description": "The item's id, as given by a search result."},
        },
        "required": ["store", "id"],
    },
}
TOOLS = [TOOL, SEARCH_STORE, OPEN_ITEM]


def key():
    return os.environ.get("ANTECHAMBER_MCP_KEY") or C.get("mcp", "key", "")


def format_winners(out):
    """Winners as numbered plain text: date, source, title/section, then the text or the conversation lines."""
    ws = out.get("winners") or []
    if not ws:
        return f'No matches for: {out.get("query", "")}'
    parts = []
    for i, w in enumerate(ws, 1):
        head = " · ".join(x for x in (w.get("date"), w.get("source"), w.get("title"), w.get("section")) if x)
        body = w.get("text") or w.get("snippet") or w.get("summary") or ""
        lines = w.get("lines") or []
        if lines:
            body = "\n".join(f'{">>" if ln.get("hit") else "  "} [{ln.get("sender") or "?"}]: {ln.get("message") or ""}'
                             for ln in lines)
        ref = f'\n[store: {w["store"]} | id: {w["id"]}]' if w.get("store") and w.get("id") else ""
        parts.append(f"{i}. {head or w.get('kind', 'result')}{ref}\n{body}".rstrip())
    if out.get("failed"):
        parts.append("(not searched this time: " + ", ".join(out["failed"]) + ")")
    return "\n\n".join(parts)


def _reply(h, code, obj=None):
    if obj is None:
        h.send_response(code)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    h._send(code, obj)


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _text(text, error=False):
    out = {"content": [{"type": "text", "text": text}]}
    if error:
        out["isError"] = True
    return out


def _limit(args):
    try:
        return max(1, min(20, int(args.get("limit") or 8)))
    except (TypeError, ValueError):
        return 8


def _run_recall(q, n, remote, **kw):
    try:
        out = antechamber_recall.recall(q, n=n, windows=True, **kw)   # windows=True: how every published number was measured
    except Exception as e:  # a store failing must come back as a tool error, never a dropped connection
        return _text("recall failed" if remote else f"recall failed: {str(e)[:200]}", True)   # error text can name local paths
    return _text(format_winners(out))


def _tool_recall(args, remote):
    q = str(args.get("query") or "").strip()
    if not q:
        return _text("query is required", True)
    return _run_recall(q, _limit(args), remote)


def _tool_search_store(args, remote):
    store, q = str(args.get("store") or "").strip(), str(args.get("query") or "").strip()
    names = sorted(antechamber_recall.SHELVES)
    if store not in names:
        return _text(f'unknown store: {store[:80]!r}. Valid stores: {", ".join(names) or "(none)"}', True)
    if not q:
        return _text("query is required", True)
    return _run_recall(q, _limit(args), remote, shelves=[store], solo=True)


def _tool_open_item(args, remote):
    store, item_id = str(args.get("store") or "").strip(), str(args.get("id") or "").strip()
    names = sorted(antechamber_recall.SHELVES)
    if store not in names:
        return _text(f'unknown store: {store[:80]!r}. Valid stores: {", ".join(names) or "(none)"}', True)
    if not item_id:
        return _text("id is required", True)
    opener = getattr(antechamber_recall.SHELVES[store], "open", None)
    if not callable(opener):
        return _text("this store cannot open items; use recall", True)
    try:
        text = opener(item_id)
    except Exception as e:   # refused ids, missing items: generic text only, never a local path
        return _text("could not open that item" + ("" if remote or not isinstance(e, ValueError) else f": {e}"), True)
    if not isinstance(text, str) or not text.strip():
        return _text("could not open that item", True)
    if len(text) > OPEN_CAP:
        text = text[:OPEN_CAP] + f"\n\n[cut at {OPEN_CAP} characters]"
    return _text(text)


def handle(msg, remote=False):
    """One JSON-RPC message -> its response dict, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if rid is None:
        return None                      # notifications (e.g. notifications/initialized) get no answer
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(rid, {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                             "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        fn = {"recall": _tool_recall, "search_store": _tool_search_store, "open_item": _tool_open_item}.get(name)
        if fn is None:
            return _error(rid, -32602, f'unknown tool: {name}')
        return _result(rid, fn(params.get("arguments") or {}, remote))
    return _error(rid, -32601, f"method not found: {method}")


def route_mcp(h):
    if not getattr(h, "remote_identity", None):          # a verified Access request (the remote door) needs no key
        k = key()
        if not k:
            return _reply(h, 503, {"error": "MCP is off: set [mcp] key in the settings file or ANTECHAMBER_MCP_KEY"})
        got = h.headers.get("Authorization") or ""
        if not hmac.compare_digest(got.encode(), f"Bearer {k}".encode()):
            return _reply(h, 401, {"error": "unauthorized"})
    try:
        size = int(h.headers.get("Content-Length") or 0)
        if size < 0:
            return _reply(h, 400, _error(None, -32600, "bad Content-Length"))
        if size > 1_000_000:
            return _reply(h, 413, {"error": "too large"})
        body = json.loads(h.rfile.read(size) or b"null")
    except (ValueError, json.JSONDecodeError):
        return _reply(h, 400, _error(None, -32700, "parse error"))
    if isinstance(body, list):
        if not body:
            return _reply(h, 400, _error(None, -32600, "empty batch"))
        answers = [a for a in (handle(m, bool(getattr(h, "remote_identity", None))) for m in body) if a is not None]
        return _reply(h, 202) if not answers else _reply(h, 200, answers)
    answer = handle(body, bool(getattr(h, "remote_identity", None)))
    return _reply(h, 202) if answer is None else _reply(h, 200, answer)


def register(service):
    service.add_post("/mcp", route_mcp)
