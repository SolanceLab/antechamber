# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The item every public adapter returns, and how a winning item is written out.

An item is a plain dict:

    id        required, stable within its adapter instance (the fusion key is (instance id, id))
    text      required, the passage itself
    date      optional, RFC 3339 ("2026-03-04T10:00:00Z") or a bare day ("2026-03-04"), else None
    source    optional, a human label: a file path, a conversation title, a service name
    title     optional
    section   optional (the heading a passage sits under)
    metadata  optional dict, free; unknown fields an adapter receives are kept here
    kind      always "item"

Adapters may add working fields (thread, lines) that the renderer uses; they never reach the fusion key.
"""
import datetime
import math
import re

RENDER_CAP = 1200         # what a winner shows of it
ITEM_PROTOCOL = 1
LINE_CAP = 600            # one chat line in a window
TEXT_CAP = 4000           # an item's text is never longer than this
KIND = "item"

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KNOWN = {"id", "text", "date", "source", "title", "section", "metadata", "kind"}


class ItemError(ValueError):
    pass


def normalize_date(value):
    """Any date an adapter meets -> RFC 3339 UTC ("...Z"), a bare day kept as "YYYY-MM-DD", or None.

    Numbers are epoch seconds. Naive date-times are taken as UTC. Malformed values give None, never an error."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                return None
            d = datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc)
            return d.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, datetime.datetime):
            d = value
        elif isinstance(value, datetime.date):
            return value.isoformat()
        else:
            s = str(value).strip()
            if _DAY.match(s):
                datetime.date.fromisoformat(s)          # validates the day itself
                return s
            if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", s):
                return None
            d = datetime.datetime.fromisoformat(s.replace("z", "Z"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def _opt_str(v, name):
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if not isinstance(v, str):
        raise ItemError(f"item field {name!r} must be text")
    return v


def make_item(raw):
    """Validate one raw item (dict) and return the clean item. Raises ItemError on a bad shape."""
    if not isinstance(raw, dict):
        raise ItemError("an item must be an object")
    iid = raw.get("id")
    if isinstance(iid, bool) or not isinstance(iid, (str, int)) or str(iid) == "":
        raise ItemError("an item needs an id (text or number)")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ItemError("an item needs text")
    meta = raw.get("metadata")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ItemError("item metadata must be an object")
    meta = dict(meta)
    for k, v in raw.items():
        if k not in _KNOWN:
            meta.setdefault(k, v)                     # unknown fields are kept, never dropped
    return {"id": str(iid), "text": text[:TEXT_CAP], "date": normalize_date(raw.get("date")),
            "source": _opt_str(raw.get("source"), "source"), "title": _opt_str(raw.get("title"), "title"),
            "section": _opt_str(raw.get("section"), "section"), "metadata": meta, "kind": KIND}


def render_item(it, rctx=None):
    """The `item` kind renderer: render(item, rctx) -> winner dict, or None when another winner's chat
    window already showed this line (rctx.covered, when the engine provides it)."""
    covered = getattr(rctx, "covered", None)
    lines = it.get("lines")
    if covered is not None:
        own = (it.get("_ns"), it.get("id"))
        if own in covered:
            return None
        if lines:
            covered.update((it.get("_ns"), ln.get("id")) for ln in lines)
        covered.add(own)
    w = {"kind": KIND, "source": it.get("source"), "date": it.get("date"), "title": it.get("title"),
         "section": it.get("section"), "text": (it.get("text") or "")[:RENDER_CAP]}
    if lines:
        w["lines"] = [{"sender": ln.get("sender"), "message": (ln.get("message") or "")[:LINE_CAP],
                       "hit": bool(ln.get("hit"))} for ln in lines]
    return w
