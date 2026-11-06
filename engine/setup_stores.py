# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""First-run setup for the stdio MCP server: the setup_stores tool.

The assistant asks the user which folders to search, then calls setup_stores. This writes the settings file
(config.PATH, mode 0600), loads the new stores in this process and builds their first index, so the next recall
finds the notes with no restart. Standard library only."""
import json
import os
import urllib.request

import config as C
import antechamber_recall
import mcp_http

KINDS = ("notes", "chats", "memories")
NO_STORES = ("Antechamber has no stores yet. Ask the user which folders to search, "
             "then call setup_stores.")
TOOL = {
    "name": "setup_stores",
    "description": ("Choose which stores Antechamber searches. Call it on first run (when recall says there are no "
                    "stores yet), after asking the user which folders to search. Each store is a kind (notes: a "
                    "folder of Markdown or text notes; chats: a Claude or ChatGPT export file or folder; memories: "
                    "a folder of memory files) and an absolute path. It writes the settings file and loads the stores "
                    "at once. Once stores exist, changing them needs overwrite=true."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "stores": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": list(KINDS)},
                "path": {"type": "string", "description": "Absolute path (a leading ~ is fine) to the folder or file."},
                "name": {"type": "string", "description": "A short name shown in answers (optional)."},
                "format": {"type": "string", "enum": ["claude", "chatgpt"], "description": "Chats only (default claude)."},
            }, "required": ["kind", "path"]}},
            "embedder": {"type": "string", "enum": ["none", "ollama"],
                         "description": "none (default): search by words. ollama: also by meaning, only if Ollama runs on this computer."},
            "overwrite": {"type": "boolean", "description": "Replace the existing settings (a .bak copy is kept)."},
        },
        "required": ["stores"],
    },
}
_stop = [None]


def configured():
    """True when the settings file names at least one store."""
    a = C.raw().get("adapters") or {}
    return any(isinstance(v, list) and v for v in a.values())


def start_refresher():
    """(Re)start the background index refresh over the current stores; never inside a recall."""
    if _stop[0]:
        _stop[0]()
        _stop[0] = None
    shelves = list(antechamber_recall.PUBLIC_SHELVES)
    if shelves:
        import adapters
        _stop[0] = adapters.start_refresher(shelves, C.INDEX_REFRESH_MINUTES)


def _toml(s):
    return json.dumps(s, ensure_ascii=False)       # a JSON string is a valid TOML basic string


def _check(stores):
    """-> (clean stores, error text or None)."""
    if not isinstance(stores, list) or not stores:
        return None, "stores must be a non-empty list"
    clean, used = [], set()
    for i, s in enumerate(stores, 1):
        if not isinstance(s, dict) or s.get("kind") not in KINDS:
            return None, f"store {i}: kind must be one of {', '.join(KINDS)}"
        raw = str(s.get("path") or "").strip()
        path = os.path.expanduser(raw)
        if not raw or not os.path.isabs(path):
            return None, f"store {i}: path must be absolute (got {raw!r})"
        ok = os.path.isdir(path) or (s["kind"] == "chats" and os.path.isfile(path))
        if not ok:
            return None, f"store {i}: {path} is not " + ("a file or folder" if s["kind"] == "chats" else "a folder")
        fmt = s.get("format") or "claude"
        if s["kind"] == "chats" and fmt not in ("claude", "chatgpt"):
            return None, f"store {i}: format must be claude or chatgpt"
        n, sid = 1, s["kind"]
        while sid in used:
            n += 1
            sid = f'{s["kind"]}-{n}'
        used.add(sid)
        clean.append({"kind": s["kind"], "id": sid, "path": path, "format": fmt,
                      "name": str(s.get("name") or "").strip() or sid.capitalize()})
    return clean, None


def _ollama_up():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2).close()
        return True
    except Exception:
        return False


def render(stores, embedder):
    out = ["# Written by Antechamber setup_stores. Edit by hand or call setup_stores again.", ""]
    if embedder == "ollama":
        out += ["[embedder]", 'backend = "ollama"', 'url = "http://localhost:11434"',
                'model = "nomic-embed-text"', "dims = 768", ""]
    for s in stores:
        out += [f'[[adapters.{s["kind"]}]]', f'id = {_toml(s["id"])}', f'name = {_toml(s["name"])}']
        out.append(f'{"path" if s["kind"] == "chats" else "folder"} = {_toml(s["path"])}')
        if s["kind"] == "chats":
            out.append(f'format = {_toml(s["format"])}')
        out.append("")
    return "\n".join(out)


def _text(text, error=False):
    return mcp_http._text(text, error)


def setup_stores(args):
    """The tool call -> an MCP tool result."""
    stores, err = _check(args.get("stores"))
    if err:
        return _text(err, True)
    embedder = args.get("embedder") or "none"
    if embedder not in ("none", "ollama"):
        return _text("embedder must be none or ollama", True)
    if embedder == "ollama" and not _ollama_up():
        return _text("Ollama is not answering on http://localhost:11434; start it or use embedder=none.", True)
    exists = os.path.exists(C.PATH) and os.path.getsize(C.PATH) > 0
    if exists and not args.get("overwrite"):
        return _text(f"Settings already exist at {C.PATH}. Call setup_stores again with overwrite=true to replace "
                     "them (a .bak copy is kept).", True)
    os.makedirs(os.path.dirname(C.PATH), exist_ok=True)
    if exists:
        with open(C.PATH, "rb") as f, open(os.open(C.PATH + ".bak", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as g:
            g.write(f.read())
    tmp = C.PATH + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(render(stores, embedder))
    os.replace(tmp, C.PATH)
    os.chmod(C.PATH, 0o600)
    C.reload()
    try:
        antechamber_recall.load_configured()
        import adapters
        adapters.refresh_all(antechamber_recall.PUBLIC_SHELVES)    # first index now, so the next recall finds the notes
    except Exception as e:
        return _text(f"Settings were written to {C.PATH} but the stores did not load: {str(e)[:300]}", True)
    start_refresher()
    lines = [f'  {s["id"]}: {s["kind"]} at {s["path"]}' for s in stores]
    return _text(f"Antechamber is set up ({'meaning + words' if embedder == 'ollama' else 'words only'}). "
                 f"Stores:\n" + "\n".join(lines) + f"\nSettings: {C.PATH}. Recall now searches them.")
