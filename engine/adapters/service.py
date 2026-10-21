# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""service — a store that searches itself, behind one HTTP endpoint.

    [[adapters.service]]
    id = "team-wiki"              # immutable; the fusion key namespace
    name = "Team wiki"            # shown only, and the default source label
    url = "https://search.example.org/recall"
    api_key_env = "WIKI_KEY"      # NAME of an environment variable; sent as "Authorization: Bearer <key>"
    timeout = 5                   # seconds
    limit = 20                    # at most 50
    allow_local = false           # private, loopback and link-local addresses are refused unless true

Request:  POST {"query": "...", "limit": 20}
Answer:   {"items": [{"id", "text", "date"?, "source"?, "title"?, "section"?, "metadata"?}, ...]}

Rules: http/https only, no redirects, no user:password in the URL; the host is resolved once and the check and
the connection use the same address; the answer is at most 1 MB, at most 50 items, each text at most 4,000
characters. A broken answer fails the shelf for that question (recall carries on with the others).
"""
import logging

from . import embedders, index as ix
from .item import ItemError, make_item

log = logging.getLogger("antechamber.adapters")

MAX_ITEMS = 50
MAX_BYTES = 1 << 20


class ServiceError(RuntimeError):
    pass


def parse_answer(payload, iid, name):
    """Strict reading of a service answer -> items."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ServiceError(f"service {iid}: answer must be an object with an 'items' list")
    raw = payload["items"]
    if len(raw) > MAX_ITEMS:
        log.warning("service %s: %d items returned; only the first %d are used", iid, len(raw), MAX_ITEMS)
        raw = raw[:MAX_ITEMS]
    out = []
    for r in raw:
        try:
            it = make_item(r)
        except ItemError as e:
            raise ServiceError(f"service {iid}: {e}") from None
        it["source"] = it["source"] or name
        it["_ns"] = iid
        out.append(it)
    return out


def from_config(cfg):
    insts = ((cfg or {}).get("adapters") or {}).get("service") or []
    from shelves import FnShelf
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "service")
        url = inst.get("url")
        embedders.parse_url(url, f"[[adapters.service]] {iid}")
        key = embedders.env_key(inst.get("api_key_env") or "", f"[[adapters.service]] {iid} api_key_env")
        timeout = float(inst.get("timeout", 5))
        limit = max(1, min(MAX_ITEMS, int(inst.get("limit", 20))))
        allow_local = inst.get("allow_local") is True

        def search(ctx, url=url, key=key, timeout=timeout, limit=limit, allow_local=allow_local, iid=iid, name=name):
            hdr = {"Authorization": "Bearer " + key} if key else {}
            try:
                payload = embedders.post_json(url, {"query": getattr(ctx, "q", ""), "limit": limit}, hdr, timeout,
                                              MAX_BYTES, allow_local=allow_local, what=f"service {iid}")
            except embedders.EmbedError as e:
                raise ServiceError(str(e)) from None
            return parse_answer(payload, iid, name)

        shelves.append(FnShelf(iid, search, iid, "id"))
    return shelves
