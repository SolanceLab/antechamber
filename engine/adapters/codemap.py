# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""codemap — a code map written by Graphify (`graphify extract <dir> --code-only`), searched by keyword.

It is read as data: this adapter never runs Graphify and needs only the standard library.

    [[adapters.codemap]]
    id = "code"                   # immutable; the fusion key namespace
    name = "My code"              # shown only, and the source label
    graph = "~/.cache/codemap/graph.json"   # Graphify's graph.json (nodes + links)
    limit = 0                     # optional cap on results (0 = recall's pool)

Each item is one node. text = "<label> — <file>:<location> · calls: a, b · called by: c", with up to 5
neighbours each, taken from edges whose relation is "calls". The edge list is read from "links" (or "edges");
an edge that carries both _src and _tgt uses them as its direction.

The graph is parsed and validated when the adapter loads, and a bad graph is an error there. The file is
re-checked by refresh() (mtime and size) in the background refresher, never inside a recall. A failed refresh
keeps the last good graph and reports the error, so /health shows it.
"""
import json
import logging
import os
import threading

from . import index as ix
from .item import make_item

log = logging.getLogger("antechamber.adapters")

NEIGHBOURS = 5
LABEL_HIT, FILE_HIT, ID_HIT = 5, 2, 1


class CodeMapError(RuntimeError):
    pass


def _key(v):
    """An id or endpoint as text, or None when it cannot be one (bool, empty, other types)."""
    if isinstance(v, bool) or not isinstance(v, (str, int)) or str(v) == "":
        return None
    return str(v)


def parse_graph(path):
    """Graphify graph.json -> {'nodes': [...], 'calls': {id: [label]}, 'called_by': {id: [label]}}. Raises CodeMapError."""
    try:
        with open(path, "rb") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        raise CodeMapError(f"codemap graph {path} could not be read: {e}") from None
    edges = None
    if isinstance(raw, dict):
        for k in ("links", "edges"):                     # links preferred when both are present
            if isinstance(raw.get(k), list):
                edges = raw[k]
                break
    if edges is None or not isinstance(raw.get("nodes"), list):
        raise CodeMapError(f"codemap graph {path} is not a Graphify graph.json (needs a nodes list and a links list)")
    by_id = {}
    for n in raw["nodes"]:
        nid = _key(n.get("id")) if isinstance(n, dict) else None
        if nid is None or nid in by_id:                  # first wins
            continue
        by_id[nid] = {"id": nid, "label": str(n.get("label") or nid), "norm": str(n.get("norm_label") or "").lower(),
                      "file": str(n.get("source_file") or ""), "loc": str(n.get("source_location") or "")}
    calls, called_by = {}, {}
    for e in edges:
        if not isinstance(e, dict) or e.get("relation") != "calls":
            continue
        both = "_src" in e and "_tgt" in e
        src, tgt = _key(e["_src"] if both else e.get("source")), _key(e["_tgt"] if both else e.get("target"))
        if src not in by_id or tgt not in by_id or src == tgt:
            continue
        for lst, owner, other in ((calls, src, tgt), (called_by, tgt, src)):
            names = lst.setdefault(owner, [])
            if by_id[other]["label"] not in names:
                names.append(by_id[other]["label"])
    return {"nodes": list(by_id.values()), "calls": calls, "called_by": called_by}


def _sig(path):
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


class CodeIndex:
    """Holds the parsed graph. refresh() re-reads it when the file changed (background refresher); current() is
    what a search reads. A failed refresh raises and keeps the last good graph."""

    def __init__(self, path):
        self.path, self.data, self.sig, self.lock = path, None, None, threading.Lock()

    def refresh(self):
        try:
            sig = _sig(self.path)
        except OSError:
            raise CodeMapError(f"codemap graph {self.path} is gone") from None
        if sig == self.sig and self.data is not None:
            return {"nodes": len(self.data["nodes"]), "reloaded": False}
        data = parse_graph(self.path)
        with self.lock:
            self.data, self.sig = data, sig
        return {"nodes": len(data["nodes"]), "reloaded": True}

    def current(self):
        with self.lock:
            return self.data


def score_nodes(data, t):
    scored = []
    for n in data["nodes"]:
        lab, f, i = n["label"].lower(), n["file"].lower(), n["id"].lower()
        s = 0
        for w in t:
            if w in lab or w in n["norm"]:
                s += LABEL_HIT
            if w in f:
                s += FILE_HIT
            if w in i:
                s += ID_HIT
        if s > 0:
            scored.append((-s, len(n["label"]), n["id"], n))
    scored.sort(key=lambda x: x[:3])
    return [x[3] for x in scored]


def node_text(node, data):
    head = node["label"]
    if node["file"]:
        head += " — " + node["file"] + (":" + node["loc"] if node["loc"] else "")
    parts = [head]
    c = data["calls"].get(node["id"], [])[:NEIGHBOURS]
    b = data["called_by"].get(node["id"], [])[:NEIGHBOURS]
    if c:
        parts.append("calls: " + ", ".join(c))
    if b:
        parts.append("called by: " + ", ".join(b))
    return " · ".join(parts)


def to_items(nodes, data, iid, name):
    return [dict(make_item({"id": n["id"], "text": node_text(n, data), "source": name, "title": n["label"],
                            "metadata": {"path": n["file"], "line": n["loc"]}}), _ns=iid) for n in nodes]


def from_config(cfg, embedder=None):
    insts = ((cfg or {}).get("adapters") or {}).get("codemap") or []
    if not insts:
        return []
    from shelves import FnShelf
    from query import terms
    base = (cfg or {}).get("_config_dir") or os.getcwd()
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "codemap")
        g = os.path.expanduser(str(inst.get("graph") or ""))
        if not g:
            raise CodeMapError(f"[[adapters.codemap]] {iid}: graph (the path to Graphify's graph.json) is required")
        g = g if os.path.isabs(g) else os.path.join(base, g)
        idx = CodeIndex(g)
        try:
            idx.refresh()                                # a missing or invalid graph stops startup, loudly
        except CodeMapError as e:
            raise CodeMapError(f"[[adapters.codemap]] {iid}: {e}") from None
        cap = int(inst.get("limit", 0) or 0)

        def search(ctx, idx=idx, cap=cap, iid=iid, name=name):
            pool = int(getattr(ctx, "pool", 0) or 20)
            limit = min(pool, cap) if cap > 0 else pool
            t = terms(getattr(ctx, "q", "") or "")
            if not t:
                return []
            data = idx.current()
            if data is None:
                return []
            return to_items(score_nodes(data, t)[:limit], data, iid, name)

        shelf = FnShelf(iid, search, iid, "id")
        shelf.index = idx
        shelves.append(shelf)
    return shelves
