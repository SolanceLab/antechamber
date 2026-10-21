# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""chats — the official data exports of chat apps, searched by meaning and by words.

    [[adapters.chats]]
    id = "assistant-chats"        # immutable; names the index file and the fusion key
    name = "Assistant chats"      # shown only
    path = "~/exports/claude"     # an export file, or a folder holding conversations*.json
    format = "claude"             # "claude" (conversations.json) or "chatgpt" (conversations*.json)
    max_export_bytes = 500000000  # larger exports are skipped (logged)
    limit = 20

A passage is one message (a long message is cut into parts). Only the spoken text is kept: no thinking, tool
calls or hidden system messages. Senders are written "user" and "assistant". A ChatGPT conversation is read along
current_node back to its root: the branch the person actually saw. The export's own message id is the item id, so
the same message in two exports is indexed once. A winning message is shown with its neighbours (2 before,
3 after) as lines.
"""
import codecs
import json
import logging
import os
import stat

from . import embedders, index as ix
from .item import normalize_date
from .notes import resolve, base_dir_of

log = logging.getLogger("antechamber.adapters")

PART_CHARS = 2000
FORMATS = ("claude", "chatgpt")
READER_VERSION = 1


def iter_json_array(source, chunk=1 << 20, max_bytes=None):
    """Yield the elements of a top-level JSON array one at a time, without loading the whole file. source is a path
    or a binary file already open; reading more than max_bytes bytes raises ValueError (the file grew)."""
    if isinstance(source, (str, bytes, os.PathLike)):
        with open(source, "rb") as f:
            yield from _iter_json_array(f, chunk, max_bytes)
    else:
        yield from _iter_json_array(source, chunk, max_bytes)


def _iter_json_array(f, chunk, max_bytes):
    dec = json.JSONDecoder()
    text = codecs.getincrementaldecoder("utf-8-sig")(errors="replace")
    buf, pos, eof, total = "", 0, False, 0

    def more():
        nonlocal buf, pos, eof, total
        while True:
            data = f.read(chunk)
            if not data:
                eof = True
                s = text.decode(b"", final=True)
                if not s:
                    return False
            else:
                total += len(data)
                if max_bytes is not None and total > max_bytes:
                    raise ValueError(f"export is larger than {max_bytes} bytes")
                s = text.decode(data)
                if not s:
                    continue
            buf = buf[pos:] + s
            pos = 0
            return True

    def skip_ws():
        nonlocal pos
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n":
                pos += 1
            if pos < len(buf) or not more():
                return

    skip_ws()
    if pos >= len(buf) or buf[pos] != "[":
        raise ValueError("export is not a JSON list of conversations")
    pos += 1
    while True:
        skip_ws()
        if pos >= len(buf):
            raise ValueError("export ends early")
        if buf[pos] == "]":
            return
        if buf[pos] == ",":
            pos += 1
            continue
        while True:
            try:
                obj, end = dec.raw_decode(buf, pos)
                break
            except json.JSONDecodeError:
                if eof or not more():
                    raise ValueError("export holds broken JSON") from None
        pos = end
        yield obj


def _parts(text):
    out = []
    while text:
        if len(text) <= PART_CHARS:
            out.append(text)
            break
        cut = text.rfind(" ", 0, PART_CHARS)
        cut = cut if cut > PART_CHARS // 2 else PART_CHARS
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return out


def claude_messages(conv):
    """(thread id, title, [(message id, sender, text, date)]) for one Claude export conversation."""
    tid = str(conv.get("uuid") or conv.get("id") or "")
    title = conv.get("name") or conv.get("title") or "Untitled conversation"
    out = []
    for m in conv.get("chat_messages") or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, list) and content:
            text = "\n".join(p.get("text") or "" for p in content
                             if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
        else:
            text = m.get("text") if isinstance(m.get("text"), str) else ""
        text = text.strip()
        sender = {"human": "user", "user": "user", "assistant": "assistant"}.get(m.get("sender"))
        if not text or not sender or not m.get("uuid"):
            continue
        out.append((str(m["uuid"]), sender, text, m.get("created_at")))
    return tid, title, out


def chatgpt_messages(conv):
    """The branch the person saw: follow current_node up to the root, then read it top-down."""
    tid = str(conv.get("conversation_id") or conv.get("id") or "")
    title = conv.get("title") or "Untitled conversation"
    mapping = conv.get("mapping") or {}
    node, chain, seen = conv.get("current_node"), [], set()
    while node and node in mapping and node not in seen:
        seen.add(node)
        chain.append(mapping[node])
        node = (mapping[node] or {}).get("parent")
    out = []
    for n in reversed(chain):
        m = (n or {}).get("message")
        if not isinstance(m, dict):
            continue
        role = (m.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        meta = m.get("metadata") or {}
        if meta.get("is_visually_hidden_from_conversation"):
            continue
        c = m.get("content") or {}
        if c.get("content_type") not in ("text", "multimodal_text"):
            continue
        text = "\n".join(p for p in (c.get("parts") or []) if isinstance(p, str)).strip()
        mid = m.get("id") or n.get("id")
        if not text or not mid:
            continue
        out.append((str(mid), role, text, m.get("create_time")))
    return tid, title, out


READERS = {"claude": claude_messages, "chatgpt": chatgpt_messages}


def open_nofollow(path):
    """Binary file object for a regular file, never following a symbolic link at the last step (a link swapped in
    after the scan is refused with OSError). Used for both hashing and reading."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def make_reader(fmt, max_bytes=None):
    read_conv = READERS[fmt]

    def read(path):
        with open_nofollow(path) as f:
            st = os.fstat(f.fileno())
            if max_bytes is not None and st.st_size > max_bytes:
                raise ValueError(f"{path} is {st.st_size} bytes (limit {max_bytes})")
            for conv in iter_json_array(f, max_bytes=max_bytes):
                if not isinstance(conv, dict):
                    continue
                tid, title, msgs = read_conv(conv)
                if not tid:
                    continue
                pos = 0
                for mid, sender, text, date in msgs:
                    for k, part in enumerate(_parts(text)):
                        yield {"id": mid if k == 0 else f"{mid}#{k + 1}", "text": part, "date": normalize_date(date),
                               "source": title, "title": title, "thread": tid, "pos": pos, "sender": sender,
                               "metadata": {"sender": sender}}
                        pos += 1
    return read


def scan(path, max_bytes):
    """{file: (mtime, size)}: the export file itself, or the conversations*.json files in a folder."""
    if os.path.isdir(path):
        names = sorted(n for n in os.listdir(path) if n.startswith("conversations") and n.endswith(".json"))
        cands = [os.path.join(path, n) for n in names]
    else:
        cands = [path]
    found = {}
    for p in cands:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_size > max_bytes:
            log.warning("chats: %s is %d bytes (limit %d); skipped", p, st.st_size, max_bytes)
            continue
        found[p] = (st.st_mtime, st.st_size)
    return found


def make_to_item(iid, name, index):
    def to_item(r):
        it = ix.row_item(iid, r, name)
        it["thread"] = r.get("thread")
        if r.get("thread") is not None and r.get("pos") is not None:
            it["lines"] = [{"id": w["id"], "sender": w["sender"], "message": w["text"], "hit": w["id"] == r["id"]}
                           for w in index.window(r["thread"], r["pos"])]
        return it
    return to_item


def from_config(cfg, embedder=None):
    """[[adapters.chats]] tables -> one shelf each. `embedder` overrides the [embedder] table (tests, demos)."""
    insts = ((cfg or {}).get("adapters") or {}).get("chats") or []
    if not insts:
        return []
    base = base_dir_of(cfg)
    if embedder is None:
        embedder = embedders.shared(cfg)
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "chats")
        fmt = inst.get("format")
        if fmt not in FORMATS:
            raise embedders.ConfigError(f"[[adapters.chats]] {iid}: format must be one of {list(FORMATS)}")
        if not inst.get("path"):
            raise embedders.ConfigError(f"[[adapters.chats]] {iid}: path is missing")
        path = resolve(base, inst["path"])
        if not os.path.exists(path):
            raise embedders.ConfigError(f"[[adapters.chats]] {iid}: {path} does not exist")
        mx = int(inst.get("max_export_bytes", 500_000_000))
        fp = {"adapter": "chats", "path": path, "format": fmt, "max_export_bytes": mx, "reader": READER_VERSION,
              "part_chars": PART_CHARS}
        index = ix.Index(ix.index_path(cfg, iid, base), fp, embedder, lambda p=path, m=mx: scan(p, m),
                         make_reader(fmt, mx), max_passages=int(inst.get("max_passages", 2_000_000)), max_bytes=mx,
                         opener=open_nofollow)
        shelves.append(ix.local_shelf(iid, index, embedder, make_to_item(iid, name, index), int(inst.get("limit", 20))))
    return shelves
