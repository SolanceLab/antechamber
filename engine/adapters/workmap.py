# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""workmap — the decisions, pipeline items, tasks and ships that work-map indexes
(SolanceLab/work-map, plugin/scripts/work_map.py), shelved one record per item and searched by keyword.

    [[adapters.workmap]]
    id = "work"                   # immutable; the fusion key namespace
    name = "Work"                 # shown only, and the source label
    work_map = "~/tools/work_map.py"            # the work-map tool file to use
    work_map_sha256 = ""          # optional: refuse any other work-map bytes (pin a release)
    config = "~/.config/work-map/config.json"   # work-map's own config: record files listed by kind
    limit = 0                     # optional cap on results handed to recall (0 = recall's pool)

The tool does the reading, linking and matching (load_config, load_all, build_graph, one_line, cmd_query,
cmd_open); this adapter only loads it from the configured file (the same bytes that were checked are the
ones run) and shelves what it returns. Each record is one item: id = the work-map node id (decision:<slug>),
text = the tool's one-line form, date = the record date, source = the shelf name.

Search asks the tool's query once per question word and ranks by how many words a record matched, so a
record that matches every word comes first. Opening an item id prints the tool's `open` view for it.

The record files are re-checked by modification time at most once a minute (search) or on every refresh()
(the background refresher). A failed reload keeps the last good records and reports the error, so /health
shows it; a bad config at startup is an error that names the adapter.
"""
import contextlib
import glob
import hashlib
import io
import json
import logging
import os
import threading
import time
import types

from . import index as ix
from .item import make_item

log = logging.getLogger("antechamber.adapters")

_CAPTURE_LOCK = threading.Lock()   # redirect_stdout is process-wide: one capture at a time across every shelf and thread

CHECK_EVERY = 60.0          # seconds between mtime checks made from a search


class WorkMapError(RuntimeError):
    pass


def load_workmap(path, sha256=""):
    """work-map as a module, from exactly the bytes read (and checked, when a sha256 is given)."""
    try:
        with open(path, "rb") as fh:
            src = fh.read()
    except OSError as e:
        raise WorkMapError(f"work-map at {path} could not be read: {e.strerror or e}") from None
    if sha256 and hashlib.sha256(src).hexdigest() != sha256.lower():
        raise WorkMapError(f"work-map at {path} does not match work_map_sha256: refusing to run it")
    mod = types.ModuleType("workmap_adapter_wm")
    mod.__file__ = path
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


def _capture(fn, *a):
    """Run a work-map command that prints; return (exit code, printed text)."""
    buf = io.StringIO()
    with _CAPTURE_LOCK, contextlib.redirect_stdout(buf):
        rc = fn(*a)
    return rc or 0, buf.getvalue()


class WorkIndex:
    """Holds the parsed records. refresh() reloads when a source file changed; current() is what a search reads."""

    def __init__(self, wm, config_path):
        self.wm, self.config_path = wm, config_path
        self.data, self.sig, self.checked = None, None, 0.0
        self.min_interval = CHECK_EVERY
        self.lock, self.cmd_lock = threading.Lock(), threading.Lock()

    def _sources(self):
        try:
            sources, base = self.wm.load_config(self.config_path)
        except self.wm.ConfigError as e:
            raise WorkMapError(str(e)) from None
        return sources, base

    def _signature(self, sources, base):
        files = [self.config_path]
        for kind in self.wm.KINDS:
            for pat in sources[kind]:
                files += sorted(glob.glob(pat if os.path.isabs(pat) else os.path.join(base, pat), recursive=True))
        sig = []
        for f in files:
            try:
                st = os.stat(f)
                sig.append((f, st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((f, None, None))
        return tuple(sig)

    def refresh(self):
        sources, base = self._sources()
        sig = self._signature(sources, base)
        self.checked = time.monotonic()
        if sig == self.sig and self.data is not None:
            return {"records": len(self.data["records"]), "reloaded": False}
        records, files, excluded = self.wm.load_all(sources, base)
        graph = self.wm.build_graph(records)
        data = {"records": records, "files": files, "excluded": excluded, "graph": graph,
                "by_pos": {(r["source"][0], r["source"][1], r["id"]): r for r in records}}
        with self.lock:
            self.data, self.sig = data, sig
        return {"records": len(records), "reloaded": True, "excluded": len(excluded)}

    def current(self):
        """The records now; re-checks the files first when a minute has passed. A failed check keeps the old records."""
        if self.data is None or time.monotonic() - self.checked >= self.min_interval:
            try:
                self.refresh()
            except Exception as e:
                self.checked = time.monotonic()
                log.warning("workmap: reload failed (%s); keeping the last good records", e)
        with self.lock:
            return self.data

    def query(self, words):
        """Records ranked by how many of the words the tool's own query matched."""
        data = self.current()
        if data is None:
            return []
        score = {}
        for w in words:
            with self.cmd_lock:
                _, out = _capture(self.wm.cmd_query, types.SimpleNamespace(text=w, json=True), (data["records"],))
            for p in json.loads(out):
                r = data["by_pos"].get((p["source"]["file"], p["source"]["line"], p["id"]))
                if r is not None:
                    score[id(r)] = (score.get(id(r), (0, r))[0] + 1, r)
        ranked = sorted(score.values(), key=lambda s: (-s[0], s[1]["seq"]))
        return [r for _, r in ranked]

    def open_text(self, item_id):
        data = self.current()
        if data is None:
            raise ValueError("not ready")
        with self.cmd_lock:
            rc, out = _capture(self.wm.cmd_open, types.SimpleNamespace(id=str(item_id), json=False),
                               (data["records"], data["files"], data["excluded"], data["graph"]))
        if rc != 0:
            raise ValueError("not found")
        return out


def to_items(records, wm, iid, name):
    out, seen = [], set()
    for r in records:
        if r["id"] in seen:                 # shortcut: the first of two records sharing an id wins; work-map's inventory lists them
            continue
        seen.add(r["id"])
        out.append(dict(make_item({"id": r["id"], "text": wm.one_line(r), "date": r.get("date") or None,
                                   "source": name, "title": r["gist"] or r["id"],
                                   "metadata": {"kind": r["kind"], "project": r["project"], "status": r["status"],
                                                "file": r["source"][0], "line": r["source"][1]}}), _ns=iid))
    return out


def from_config(cfg, embedder=None):
    insts = ((cfg or {}).get("adapters") or {}).get("workmap") or []
    if not insts:
        return []
    from shelves import FnShelf
    from query import terms
    base = (cfg or {}).get("_config_dir") or os.getcwd()
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "workmap")
        wm_path = os.path.expanduser(str(inst.get("work_map") or ""))
        wm_cfg = os.path.expanduser(str(inst.get("config") or ""))
        if not wm_path or not wm_cfg:
            raise WorkMapError(f"[[adapters.workmap]] {iid}: work_map and config are both required")
        wm_path = wm_path if os.path.isabs(wm_path) else os.path.join(base, wm_path)
        wm_cfg = wm_cfg if os.path.isabs(wm_cfg) else os.path.join(base, wm_cfg)
        try:
            wm = load_workmap(wm_path, str(inst.get("work_map_sha256") or ""))
            idx = WorkIndex(wm, wm_cfg)
            idx.refresh()                                # a missing or invalid config stops startup, loudly
        except WorkMapError as e:
            raise WorkMapError(f"[[adapters.workmap]] {iid}: {e}") from None
        cap = int(inst.get("limit", 0) or 0)

        def search(ctx, idx=idx, wm=wm, cap=cap, iid=iid, name=name):
            pool = int(getattr(ctx, "pool", 0) or 20)
            limit = min(pool, cap) if cap > 0 else pool
            t = terms(getattr(ctx, "q", "") or "")
            if not t:
                return []
            return to_items(idx.query(t), wm, iid, name)[:limit]   # dedup the whole ranking first, then cut: duplicate ids must not starve the limit

        shelf = FnShelf(iid, search, iid, "id")
        shelf.index = idx
        shelf.open = idx.open_text       # read only by the MCP open_item tool
        shelves.append(shelf)
    return shelves
