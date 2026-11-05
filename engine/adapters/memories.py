# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""memories — the memory files a memory writer produced, searched by meaning and by words.

    [[adapters.memories]]
    id = "memories"               # immutable; names the index file and the fusion key
    name = "Memories"             # shown only
    folder = "~/memories"         # one JSON file per conversation, as the memory writer writes them
    export = "~/exports/claude"   # optional: the chat export the memories came from (Claude format); a winning
                                  # memory then carries its source lines, word for word
    redundant_with = ["chats"]    # declared: these memories repeat a searchable shelf (they never add seats here)
    limit = 20                    # items handed to recall per question
    max_file_bytes = 50000000     # a larger memory file is skipped
    max_export_bytes = 500000000  # a larger export file is not read for source lines

One memory is one item: its topic and summary plus its verbatim excerpt, dated by its session. Its metadata keeps
topic, salience, valence, emotions and `source_line_ids`. Salience and valence are carried, never used to rank:
how much they count is recall's setting, off by default.

Input: one JSON file per conversation (session). Every kind of memory uses this one format; the adapter treats them
all alike (a method's memories differ in their content, never in their plumbing):

    {
      "conversation_id": "<the conversation's id in the export>",      required
      "title": "<conversation title>",                                   optional
      "session_date": "2023-05-08",                                      optional, a day or RFC 3339 time
      "memories": [
        {
          "summary": "<what the memory says>",                           required (a memory without it is skipped)
          "excerpt": "<verbatim words from the source lines>",           optional
          "topic": "<a short topic>",                                    optional ("Personal" when absent)
          "source_line_ids": ["D1:3", "D1:5"],                           optional; message ids in the export
          "source_session_date": "2023-05-08",                           optional; else the file's session_date
          "salience": 0.7, "valence": 0.4, "emotions": ["proud"]         optional, carried, never ranked here
        }
      ]
    }

Other fields are ignored; a field of the wrong type counts as absent. A memory's id is derived from the whole memory
(every field), so it stays the same when other memories are added, removed or reordered; an
exact duplicate of a memory in the same file is dropped. When the export
changes, every memory re-reads its source lines on the next refresh. Shown to a reader when a memory wins: "<topic>: <summary>", the excerpt, and (with an
export) the full text of its source lines.
"""
import hashlib
import json
import logging
import os
import stat

from . import embedders, index as ix
from .chats import READERS, iter_json_array, open_nofollow
from .item import TEXT_CAP, normalize_date
from .notes import base_dir_of, resolve

log = logging.getLogger("antechamber.adapters")

READER_VERSION = 4   # 4: a source line id missing from the memory's own conversation is found across the export


def scan(folder, max_bytes):
    """{file: (mtime, size)} for the regular *.json files directly in the folder."""
    found = {}
    for n in sorted(os.listdir(folder)):
        if not n.endswith(".json") or n.startswith("."):
            continue
        p = os.path.join(folder, n)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            continue
        found[p] = (st.st_mtime, st.st_size)
    return found


def export_files(path):
    """The export's files: the file itself, or the conversations*.json files directly in a folder."""
    if not path or not os.path.exists(path):
        return []
    cands = ([os.path.join(path, n) for n in sorted(os.listdir(path))
              if n.startswith("conversations") and n.endswith(".json")] if os.path.isdir(path) else [path])
    out = []
    for p in cands:                                  # regular files only: links and folders are never read
        try:
            if stat.S_ISREG(os.lstat(p).st_mode):
                out.append(p)
        except OSError:
            continue
    return out


def export_stamp(path):
    """Path, mtime and size of every export file: changes whenever any of them is edited, added or removed."""
    parts = []
    for p in export_files(path):
        try:
            st = os.lstat(p)
        except OSError:
            continue
        parts.append(f"{p}|{st.st_mtime_ns}|{st.st_size}")
    return "\n".join(parts)


def export_lines(path, max_bytes, problems=None):
    """{conversation id: {line id: (sender, text)}} from a Claude-format export file or folder; {} when absent.
    When two export files hold the same conversation, the first file (by name) wins. `problems` (a list), when given,
    collects why any file or conversation was not fully read, and every line id seen twice inside one conversation."""
    out = {}
    files = export_files(path)
    for p in files:
        got = {}
        try:
            with open_nofollow(p) as f:
                for conv in iter_json_array(f, max_bytes=max_bytes):
                    if not isinstance(conv, dict):
                        continue
                    tid, _, msgs = READERS["claude"](conv)
                    if not tid and problems is not None:
                        problems.append(("no-id-conversation", p))
                    if tid and tid not in out and tid not in got:
                        got[tid] = {mid: (sender, text) for mid, sender, text, _ in msgs}
                        if problems is not None and len(got[tid]) != len(msgs):
                            seen = set()
                            problems += [("repeated-id", mid) for mid, _, _, _ in msgs if mid in seen or seen.add(mid)]
        except (OSError, ValueError) as e:            # one unreadable export file never blocks the others,
            log.warning("memories: export file %s not read (%s)", p, e)   # and a half-read one adds nothing
            if problems is not None:
                problems.append(("unread-file", p))
            continue
        out.update(got)
    return out


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def make_reader(export_path, max_bytes):
    cache = {"key": None, "lines": {}, "anywhere": {}}

    def _load():
        key = export_stamp(export_path) if export_path else None
        if key != cache["key"]:
            problems = []
            lines = export_lines(export_path, max_bytes, problems)
            anywhere, seen_twice = {}, {mid for kind, mid in problems if kind == "repeated-id"}
            if any(kind != "repeated-id" for kind, _ in problems):
                anywhere = {}       # part of the export was not read: uniqueness cannot be proven, so no fallback
            else:
                for conv in lines.values():
                    for lid, val in conv.items():
                        if lid in anywhere:
                            seen_twice.add(lid)
                        anywhere[lid] = val
                for lid in seen_twice:                   # an id used twice anywhere is never guessed
                    anywhere.pop(lid, None)
            cache["key"], cache["lines"], cache["anywhere"] = key, lines, anywhere

    def lines_for(conv_id):
        _load()
        return cache["lines"].get(conv_id, {})

    def line(cid, conv_lines, lid):
        """A memory's source line from its own conversation (as always). Only a memory whose conversation_id is NOT
        a conversation of the export (a People card gathering many conversations) looks the id up across the whole
        export, and only when that id is unique there and the whole export was read. A memory of a real
        conversation citing an id that is not in it keeps that id unresolved, as before (never borrowed)."""
        if lid in conv_lines:
            return conv_lines[lid]
        _load()
        if cid in cache["lines"]:
            return None
        return cache["anywhere"].get(lid)

    def read(path):
        with open_nofollow(path) as f:
            doc = json.load(f)
        if not isinstance(doc, dict) or not isinstance(doc.get("memories"), list):
            raise ValueError(f"{path}: not a memory file (needs a conversation_id and a memories list)")
        cid = doc.get("conversation_id")
        if not isinstance(cid, (str, int)) or str(cid) == "":
            raise ValueError(f"{path}: conversation_id is missing")
        cid = str(cid)
        title = doc["title"] if isinstance(doc.get("title"), str) and doc["title"] else "Untitled conversation"
        known = lines_for(cid)
        used = set()
        for n, m in enumerate(doc["memories"]):
            if not isinstance(m, dict) or not isinstance(m.get("summary"), str) or not m["summary"].strip():
                continue                                       # a memory needs a summary
            raw_ids = m.get("source_line_ids")
            ids = [str(i) for i in raw_ids if isinstance(i, (str, int)) and not isinstance(i, bool)] \
                if isinstance(raw_ids, list) else []
            topic = m["topic"] if isinstance(m.get("topic"), str) and m["topic"] else "Personal"
            excerpt = m["excerpt"] if isinstance(m.get("excerpt"), str) else ""
            text = f"{topic}: {m['summary']}\n{excerpt}".strip()[:TEXT_CAP]
            # stable id: what the memory says and where it comes from, never its position in the file
            digest = hashlib.sha1(json.dumps(m, ensure_ascii=False, sort_keys=True, default=str)
                                  .encode()).hexdigest()[:12]
            mid = f"{cid}/{digest}"
            if mid in used:                                    # an exact duplicate adds nothing
                continue
            used.add(mid)
            emotions = [str(e) for e in m["emotions"]] if isinstance(m.get("emotions"), list) else []
            meta = {"topic": topic, "salience": _num(m.get("salience")), "valence": _num(m.get("valence")),
                    "emotions": emotions, "source_line_ids": ids,
                    "lines": [{"id": i, "sender": v[0], "message": v[1]} for i in ids
                              for v in [line(cid, known, i)] if v is not None]}
            date = m.get("source_session_date") or doc.get("session_date")
            yield {"id": mid, "text": text, "date": normalize_date(date if isinstance(date, str) else None),
                   "source": title, "title": title, "thread": cid, "pos": n, "metadata": meta}
    return read


class MemoryIndex(ix.Index):
    """An Index whose change check also covers the export: a memory file counts as changed when the export it
    takes its source lines from changes, so a winning memory never shows stale lines."""

    def __init__(self, *args, export_path=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.export_path = export_path

    def _sha(self, path, stats):
        sha = super()._sha(path, stats)
        if sha is None or not self.export_path:
            return sha
        return hashlib.sha256(f"{sha}\n{export_stamp(self.export_path)}".encode()).hexdigest()


def make_to_item(iid, name):
    def to_item(r):
        it = ix.row_item(iid, r, name)
        lines = (it.get("metadata") or {}).get("lines") or []
        if lines:
            it["lines"] = [{"id": ln.get("id"), "sender": ln.get("sender"), "message": ln.get("message"), "hit": True}
                           for ln in lines]
        return it
    return to_item


def make_open(index):
    """id -> the memory's full text plus the conversation lines it points at (MCP open_item)."""
    def open_memory(item_id):
        rows = index.rows([str(item_id)])
        if not rows:
            raise ValueError("not found")
        try:
            meta = json.loads(rows[0].get("metadata") or "{}")
        except ValueError:
            meta = {}
        out = [rows[0].get("text") or ""]
        lines = meta.get("lines") or []
        if lines:
            out.append("Source lines:")
            out += [f'[{ln.get("sender") or "?"}]: {ln.get("message") or ""}' for ln in lines]
        return "\n".join(out)
    return open_memory


def from_config(cfg, embedder=None):
    """[[adapters.memories]] tables -> one shelf each. `embedder` overrides the [embedder] table (tests, demos)."""
    insts = ((cfg or {}).get("adapters") or {}).get("memories") or []
    if not insts:
        return []
    base = base_dir_of(cfg)
    if embedder is None:
        embedder = embedders.shared(cfg)
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "memories")
        if not inst.get("folder"):
            raise embedders.ConfigError(f"[[adapters.memories]] {iid}: folder is missing")
        folder = resolve(base, inst["folder"])
        if not os.path.isdir(folder):
            raise embedders.ConfigError(f"[[adapters.memories]] {iid}: folder {folder} does not exist")
        export = resolve(base, inst["export"]) if inst.get("export") else None
        if export and not os.path.exists(export):
            raise embedders.ConfigError(f"[[adapters.memories]] {iid}: export {export} does not exist")
        red = inst.get("redundant_with") or []
        if not isinstance(red, list) or not all(isinstance(x, str) for x in red):
            raise embedders.ConfigError(f"[[adapters.memories]] {iid}: redundant_with must be a list of adapter ids")
        mx = int(inst.get("max_file_bytes", 50_000_000))
        mex = int(inst.get("max_export_bytes", 500_000_000))
        fp = {"adapter": "memories", "folder": folder, "export": export, "reader": READER_VERSION,
              "max_file_bytes": mx, "max_export_bytes": mex}
        index = MemoryIndex(ix.index_path(cfg, iid, base), fp, embedder, lambda d=folder, m=mx: scan(d, m),
                            make_reader(export, mex), max_passages=int(inst.get("max_passages", 5000)),
                            max_bytes=mx, opener=open_nofollow, export_path=export)
        shelf = ix.local_shelf(iid, index, embedder, make_to_item(iid, name), int(inst.get("limit", 20)))
        shelf.open = make_open(index)   # read only by the MCP open_item tool
        shelf.redundant_with = list(red)   # declared for seating; recall does not act on it yet
        shelves.append(shelf)
    return shelves
