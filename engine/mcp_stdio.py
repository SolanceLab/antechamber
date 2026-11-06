# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Antechamber's MCP server over stdio: the app starts it, so there is no service, key or port to set up.

One JSON-RPC message per line on stdin, one answer per line on stdout; logs go to stderr only. The tools are
mcp_http's (recall, search_store, open_item) plus setup_stores and look_for_stores, offered only until stores are set. Stores load from the
settings file at start; their indexes refresh in a background thread, never inside a call.
Standard library only."""
import json
import sys

import config as C
import antechamber_recall
import mcp_http
import setup_stores as S

HOLD = ("recall", "search_store", "open_item")


def handle(msg):
    """One message -> its answer, or None. mcp_http.handle does the protocol; this adds the setup tool and the
    first-run message."""
    if isinstance(msg, dict) and msg.get("jsonrpc") == "2.0" and msg.get("id") is not None:
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "tools/list":
            setup = [] if S.configured() else [S.TOOL, S.LOOK_TOOL]      # once set up, the assistant cannot change stores
            return mcp_http._result(msg["id"], {"tools": mcp_http.TOOLS + setup})
        if method == "tools/call" and isinstance(params, dict):
            name = params.get("name")
            if name in ("setup_stores", "look_for_stores"):      # both refuse themselves once the settings name stores
                fn = S.setup_stores if name == "setup_stores" else S.look_for_stores
                return mcp_http._result(msg["id"], fn(params.get("arguments") or {}))
            if name in HOLD and not S.configured():
                return mcp_http._result(msg["id"], mcp_http._text(S.NO_STORES))
    return mcp_http.handle(msg, remote=False)


def handle_line(line):
    try:
        body = json.loads(line)
    except ValueError:
        return mcp_http._error(None, -32700, "parse error")
    if isinstance(body, list):
        if not body:
            return mcp_http._error(None, -32600, "empty batch")
        answers = [a for a in (handle(m) for m in body) if a is not None]
        return answers or None
    return handle(body)


def serve(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            answer = handle_line(line)
        except Exception as e:            # one bad message never ends the session
            print(f"antechamber: {type(e).__name__}: {e}", file=sys.stderr)
            answer = mcp_http._error(None, -32603, "internal error")
        if answer is not None:
            stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            stdout.flush()


def main():
    print(f"antechamber stdio (config {C.PATH if C.LOADED else 'none yet'})", file=sys.stderr, flush=True)
    S.start_refresher()
    serve()


if __name__ == "__main__":
    main()
