# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""First-run setup for the stdio MCP server: the setup_stores tool.

The assistant asks the user which folders to search, then calls setup_stores. This writes the settings file
(config.PATH, mode 0600), loads the new stores in this process and builds their first index, so the next recall
finds the notes with no restart. Standard library only."""
import hashlib
import json
import os
import stat
import urllib.request

import config as C
import antechamber_recall
import mcp_http

NO_STORES = ("Antechamber has no stores yet. Ask the user which folders to search, "
             "then call setup_stores.")
MARKER = "# Written by Antechamber setup_stores."
SECTION = {"vault": "vaultmap"}      # the settings section a kind is written under (default: the kind itself)
VAULT_MAP_VERSION = "1.3.1"
KINDS = ("notes", "chats", "memories", "vault")
TOOL = {
    "name": "setup_stores",
    "description": ("Choose which stores Antechamber searches. Call it on first run (when recall says there are no "
                    "stores yet), after asking the user which folders to search. Each store is a kind (notes: a "
                    "folder of Markdown or text notes; chats: a Claude or ChatGPT export file or folder; memories: "
                    "a folder of memory files; vault: an Obsidian vault, a folder holding .obsidian, only when look_for_stores "
                    "found one) and an absolute path. It writes the settings file and loads the stores "
                    "at once. It works only until stores exist; after that the person edits the settings file."),
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
        },
        "required": ["stores"],
    },
}
VAULT_MAP_SHA256 = "fe401264f2f87803cf9d0d15f3df2400d534fe202370f150203e44fd09e39132"   # of exactly that URL
VAULT_MAP_URL = f"https://raw.githubusercontent.com/SolanceLab/vault-map/v{VAULT_MAP_VERSION}/vault-map.py"
VAULT_MAP_MAX_BYTES = 5_000_000
_stop = [None]


def configured():
    """True when the settings file names at least one store."""
    a = C.raw().get("adapters") or {}
    return any(isinstance(v, list) and v for v in a.values())


LOCKED = ("Antechamber is already set up. Later changes are made by the person editing the settings file "
          "({path}), not by the assistant.")


def locked_text():
    return LOCKED.format(path=C.PATH)


# ---- Path policy: ONE function, used by setup_stores (_check) and look_for_stores (_look) ----
# A path is allowed only when its real location (symlinks resolved) is inside the root and is not the root itself.
# The root is $ANTECHAMBER_HOME_ROOT when that is an absolute path, else the user's home folder.
# Inside the root, these are refused at any depth, with everything beneath them: the folders in SENSITIVE_DIRS, the credential
# files in SENSITIVE_FILES, and the macOS folders in SENSITIVE_LIBRARY (Library/Mobile Documents, where iCloud
# Obsidian vaults live, stays allowed). The folder Library itself is refused too, because a store reads everything beneath it. Names are compared ignoring case.
SENSITIVE_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config", ".local", ".cache", ".password-store")
SENSITIVE_LIBRARY = ("Keychains", "Cookies", "Application Support", "Preferences", "Mail", "Messages", "Safari")
SENSITIVE_FILES = (".netrc", "_netrc", ".pgpass", ".npmrc", ".git-credentials")


def home_root():
    env = os.environ.get("ANTECHAMBER_HOME_ROOT") or ""
    return os.path.realpath(env if os.path.isabs(env) else os.path.expanduser("~"))


def path_refusal(path):
    """-> None when the path is allowed, else a short plain reason naming the rule. Reads no file contents."""
    root = home_root()
    real = os.path.realpath(path)
    if real == root:
        return "not allowed: the home folder itself is too broad; name a folder inside it"
    if os.path.commonpath([real, root]) != root:
        return "not allowed: it is outside the home folder"
    parts = [x.casefold() for x in os.path.relpath(real, root).split(os.sep)]     # macOS names are case-insensitive
    bad = {x.casefold() for x in SENSITIVE_DIRS + SENSITIVE_FILES}
    for part in parts:                                                              # at any depth: Documents/.ssh too
        if part in bad:
            return f"not allowed: {part} holds settings or credentials"
    if parts[0] == "library":
        if len(parts) == 1:
            return "not allowed: Library holds private application data; name a folder inside it (such as Mobile Documents)"
        if parts[1] in {x.casefold() for x in SENSITIVE_LIBRARY}:
            return f"not allowed: Library/{parts[1]} holds private application data"
    return None


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
        why = path_refusal(path)
        if why:
            return None, f"store {i}: {why}"
        path = os.path.realpath(path)
        if s["kind"] == "vault" and not os.path.isdir(os.path.join(path, ".obsidian")):
            return None, f"store {i}: {path} has no .obsidian folder, so it is not an Obsidian vault (use kind notes)"
        ok = os.path.isdir(path) or (s["kind"] == "chats" and os.path.isfile(path))
        if not ok:
            return None, f"store {i}: {path} is not " + ("a file or folder" if s["kind"] == "chats" else "a folder")
        fmt = s.get("format") or "claude"
        if s["kind"] == "chats" and fmt not in ("claude", "chatgpt"):
            return None, f"store {i}: format must be claude or chatgpt"
        clean.append({"kind": s["kind"], "path": path, "format": fmt, "name": str(s.get("name") or "").strip()})
    return _ids(clean), None


def _ids(stores):
    """Give each store an id: its kind, then kind-2, kind-3 ... (and a default name)."""
    used = set()
    for s in stores:
        n, sid = 1, s["kind"]
        while sid in used:
            n += 1
            sid = f'{s["kind"]}-{n}'
        used.add(sid)
        s["id"] = sid
        s["name"] = s["name"] or sid.capitalize()
    return stores


def _download(url):
    """The bytes at an https URL, capped. Tests replace this."""
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read(VAULT_MAP_MAX_BYTES + 1)


def fetch_vault_map():
    """Put the pinned vault-map.py beside the settings file; -> (path, None) or (None, why). Nothing is run here."""
    dest = os.path.join(os.path.dirname(C.PATH), f"vault-map-{VAULT_MAP_VERSION}.py")
    try:
        if os.path.isfile(dest):
            with open(dest, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == VAULT_MAP_SHA256:
                    return dest, None
        data = _download(VAULT_MAP_URL)
        if len(data) > VAULT_MAP_MAX_BYTES or hashlib.sha256(data).hexdigest() != VAULT_MAP_SHA256:
            return None, "the downloaded vault-map did not match the pinned checksum"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644), "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        os.chmod(dest, 0o644)
        return dest, None
    except Exception as e:
        return None, f"vault-map could not be downloaded ({type(e).__name__})"


def _vaults_or_notes(stores):
    """Vault stores need vault-map. When it cannot be fetched and verified, they become plain notes stores.
    -> (stores, note for the answer)."""
    if not any(s["kind"] == "vault" for s in stores):
        return stores, ""
    path, why = fetch_vault_map()
    for s in stores:
        if s["kind"] == "vault":
            if path:
                s["vault_map"], s["vault_map_sha256"] = path, VAULT_MAP_SHA256
            else:
                s["kind"] = "notes"
    note = "" if path else f"\nNote: {why}, so the vault is searched as plain notes."
    return _ids(stores) if not path else stores, note


def _ollama_up():
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2).close()
        return True
    except Exception:
        return False


def render(stores, embedder):
    out = ["# Written by Antechamber setup_stores. Later changes: edit this file by hand.", ""]
    if embedder == "ollama":
        out += ["[embedder]", 'backend = "ollama"', 'url = "http://localhost:11434"',
                'model = "nomic-embed-text"', "dims = 768", ""]
    for s in stores:
        out += [f'[[adapters.{SECTION.get(s["kind"], s["kind"])}]]', f'id = {_toml(s["id"])}', f'name = {_toml(s["name"])}']
        out.append(f'{"path" if s["kind"] == "chats" else "folder"} = {_toml(s["path"])}')
        if s["kind"] == "chats":
            out.append(f'format = {_toml(s["format"])}')
        if s["kind"] == "vault":
            out += [f'vault_map = {_toml(s["vault_map"])}', f'vault_map_sha256 = {_toml(s["vault_map_sha256"])}']
        out.append("")
    return "\n".join(out)


def _text(text, error=False):
    return mcp_http._text(text, error)


def setup_stores(args):
    """The tool call -> an MCP tool result."""
    if configured():
        return _text(locked_text(), True)
    stores, err = _check(args.get("stores"))
    if err:
        return _text(err, True)
    embedder = args.get("embedder") or "none"
    if embedder not in ("none", "ollama"):
        return _text("embedder must be none or ollama", True)
    if embedder == "ollama" and not _ollama_up():
        return _text("Ollama is not answering on http://localhost:11434; start it or use embedder=none.", True)
    exists = os.path.exists(C.PATH) and os.path.getsize(C.PATH) > 0
    if exists:
        # Only a file this tool wrote may be replaced (a .bak is kept). Any other file (a service settings file,
        # a hand-written one) is never touched: the person adds the store tables by hand.
        try:
            with open(C.PATH, encoding="utf-8") as f:
                ours = f.readline().startswith(MARKER)
        except (OSError, UnicodeDecodeError):
            ours = False
        if not ours:
            return _text(f"The settings file {C.PATH} was not written by setup_stores, so it is left as it is. "
                         "The person can add a [[adapters.notes]] table to it by hand, or delete it and run setup again.", True)
    stores, vault_note = _vaults_or_notes(stores)
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
                 f"Stores:\n" + "\n".join(lines) + f"\nSettings: {C.PATH}. Recall now searches them." + vault_note)


# ---- look_for_stores: read-only, tells the assistant what a folder looks like before it offers anything ----
SCAN_MAX_DEPTH = 3
MAX_PATHS = 20
LOOK_TOOL = {
    "name": "look_for_stores",
    "description": ("Read-only. Call it on first run with the paths the user named, before setup_stores. For each "
                    "path it says what is there: notes, obsidian vault, scriptorium memories, claude export, "
                    "chatgpt export, unknown or missing. It counts files and peeks at the start of a few "
                    "JSON files on this computer to tell an export from a memory file; it reports only the kind, "
                    "never any text. At most 20 paths per call. Offer vault or memories to the user only when it finds one."),
    "inputSchema": {"type": "object", "properties": {"paths": {"type": "array", "minItems": 1, "maxItems": MAX_PATHS, "items": {"type": "string"}}},
                    "required": ["paths"]},
}
SCAN_MAX_ENTRIES = 2000
NOTE_EXT = (".md", ".txt")


def _sniff_export(path):
    """'claude' or 'chatgpt' when a JSON file starts like a chat export, else None (first 64 KB, never reported)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            head = f.read(65536).decode("utf-8", "ignore")
    except OSError:
        return None
    if '"chat_messages"' in head:
        return "claude"
    if '"mapping"' in head:
        return "chatgpt"
    return None


def _is_memory_file(path):
    try:
        if os.path.getsize(path) > 1_000_000:
            return False
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and isinstance(d.get("memories"), list) and "conversation_id" in d


def _look(path):
    """What one path looks like -> {path, looks_like, ...counts}. No writes, no symlinks followed."""
    p = os.path.expanduser(str(path).strip())
    if not p or not os.path.isabs(p):
        return {"path": str(path), "looks_like": "unknown", "note": "not an absolute path"}
    why = path_refusal(p)
    if why:
        return {"path": p, "looks_like": "not allowed", "note": why}
    try:
        st = os.lstat(p)
    except OSError:
        return {"path": p, "looks_like": "missing"}
    if stat.S_ISLNK(st.st_mode):
        return {"path": p, "looks_like": "unknown", "note": "a link; give the real path"}
    if stat.S_ISREG(st.st_mode):
        kind = _sniff_export(p) if p.endswith(".json") else None
        return {"path": p, "looks_like": f"{kind} export" if kind else "unknown", "files": 1}
    if not stat.S_ISDIR(st.st_mode):
        return {"path": p, "looks_like": "unknown"}
    seen = notes = 0
    capped = False
    top_json = []
    for root, dirs, files in os.walk(p):
        depth = root[len(p):].count(os.sep)
        dirs[:] = [d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(root, d))]
        if depth >= SCAN_MAX_DEPTH:
            dirs[:] = []
        for n in files:
            seen += 1
            if seen > SCAN_MAX_ENTRIES:
                capped = True
                break
            fp = os.path.join(root, n)
            if os.path.islink(fp):
                continue
            if n.lower().endswith(NOTE_EXT):
                notes += 1
            elif depth == 0 and n.lower().endswith(".json"):
                top_json.append(fp)
        if capped:
            break
    out = {"path": p, "notes": notes, "scan_capped": capped}
    if os.path.isdir(os.path.join(p, ".obsidian")) and not os.path.islink(os.path.join(p, ".obsidian")):
        out["looks_like"] = "obsidian vault"
        return out
    exports = sorted(f for f in top_json if os.path.basename(f).startswith("conversations"))
    kinds = {_sniff_export(f) for f in exports} - {None}
    if len(kinds) == 1:
        out["looks_like"] = f"{kinds.pop()} export"
    elif any(_is_memory_file(f) for f in top_json[:3]):
        out["looks_like"] = "scriptorium memories"
        out["files"] = len(top_json)
    else:
        out["looks_like"] = "notes" if notes else "unknown"
    return out


def look_for_stores(args):
    """The tool call -> an MCP tool result: one line per path, plus the same as JSON."""
    if configured():
        return _text(locked_text(), True)
    paths = args.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(x, str) for x in paths):
        return _text("paths must be a non-empty list of strings", True)
    if len(paths) > MAX_PATHS:
        return _text(f"too many paths ({len(paths)}); send at most {MAX_PATHS} per call", True)
    found = [_look(x) for x in paths]
    lines = []
    for f in found:
        extra = f", {f['notes']} notes" if f.get("notes") else ""
        extra += f' ({f["note"]})' if f["looks_like"] == "not allowed" else ""
        lines.append(f'{f["path"]}: {f["looks_like"]}{extra}' + (" (scan stopped at the cap)" if f.get("scan_capped") else ""))
    return _text("\n".join(lines) + "\n" + json.dumps(found, ensure_ascii=False))
