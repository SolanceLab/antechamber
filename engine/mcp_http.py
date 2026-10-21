# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Antechamber's own MCP endpoint: POST /mcp on the local recall service (MCP over HTTP, JSON responses).

One tool, `recall`: one question in, the winners from every configured store out, as readable text.
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
        parts.append(f"{i}. {head or w.get('kind', 'result')}\n{body}".rstrip())
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


def handle(msg):
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
        return _result(rid, {"tools": [TOOL]})
    if method == "tools/call":
        if params.get("name") != "recall":
            return _error(rid, -32602, f'unknown tool: {params.get("name")}')
        args = params.get("arguments") or {}
        q = str(args.get("query") or "").strip()
        if not q:
            return _result(rid, {"content": [{"type": "text", "text": "query is required"}], "isError": True})
        try:
            n = max(1, min(20, int(args.get("limit") or 8)))
        except (TypeError, ValueError):
            n = 8
        try:
            out = antechamber_recall.recall(q, n=n, windows=True)   # windows=True: how every published number was measured
        except Exception as e:  # a store failing must come back as a tool error, never a dropped connection
            return _result(rid, {"content": [{"type": "text", "text": f"recall failed: {str(e)[:200]}"}], "isError": True})
        return _result(rid, {"content": [{"type": "text", "text": format_winners(out)}]})
    return _error(rid, -32601, f"method not found: {method}")


def route_mcp(h):
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
        answers = [a for a in (handle(m) for m in body) if a is not None]
        return _reply(h, 202) if not answers else _reply(h, 200, answers)
    answer = handle(body)
    return _reply(h, 202) if answer is None else _reply(h, 200, answer)


def register(service):
    service.add_post("/mcp", route_mcp)
