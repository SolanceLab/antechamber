# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Public adapters: point Antechamber at a stranger's own stores — a notes folder, chat exports, a search
service, a pgvector table — with no code of ours naming anything private.

Config shape (TOML):

    [embedder]                     # optional; without it notes/chats search by words only
    backend = "ollama"             # or "openai"
    url = "http://localhost:11434"
    model = "nomic-embed-text"
    dims = 768

    [index]
    dir = "~/.cache/antechamber"   # one SQLite file per notes/chats instance
    refresh_minutes = 15

    [[adapters.notes]]             # one table per instance; several folders = several tables
    id = "notes"                   # immutable: fusion key namespace + index file name
    name = "My notes"              # shown only
    folder = "~/notes"             # relative paths resolve against the config file's folder

    [[adapters.chats]]
    id = "chats"
    path = "~/exports/claude"
    format = "claude"              # or "chatgpt"

    [[adapters.memories]]          # what a memory writer produced (see memories.py for the file format)
    id = "memories"
    folder = "~/memories"
    export = "~/exports/claude"    # optional: brings each winning memory's source lines

    [[adapters.service]]
    id = "wiki"
    url = "https://search.example.org/recall"

    [[adapters.vaultmap]]          # an Obsidian vault through vault-map (see vaultmap.py)
    id = "vault"
    folder = "~/Documents/Vault"
    vault_map = "~/tools/vault-map.py"

    [[adapters.codemap]]           # a code map written by Graphify (see codemap.py)
    id = "code"
    graph = "~/.cache/codemap/graph.json"

    [[adapters.workmap]]           # decisions, pipeline, tasks and ships through work-map (see workmap.py)
    id = "work"
    work_map = "~/tools/work_map.py"
    config = "~/.config/work-map/config.json"

    [[adapters.pgvector]]
    id = "papers"
    dsn_env = "PAPERS_DSN"
    table = "passages"             # + id_col, text_col, vec_col (see pgvector.py)

Every module documents its own table in full. `python3 -m adapters.check <config.toml>` validates one.

Engine hooks:
    load(cfg)                       -> list of Shelf (FnShelf); key namespace = each instance id, kind "item"
    register_kinds(shelves, register_kind)  registers render_item for every instance id
    refresh_all(shelves)            -> {id: summary}; at service start
    start_refresher(shelves, minutes) -> stop(); refreshes in a background thread, never inside a recall
    degraded(ctx)                   -> ids that answered by words only for this question
"""
import logging
import threading

from .item import ITEM_PROTOCOL, KIND, make_item, normalize_date, render_item  # noqa: F401
from .embedders import ConfigError  # noqa: F401
from .check import check as check_config, load_config  # noqa: F401

log = logging.getLogger("antechamber.adapters")


def load(cfg, embedder=None):
    """Every configured public adapter -> shelves, in config order by kind (notes, chats, memories, service, vaultmap, codemap, workmap, pgvector).
    The pgvector module (and psycopg) is only imported when a pgvector adapter is configured."""
    from . import notes, chats, service, memories, vaultmap, codemap, workmap
    a = (cfg or {}).get("adapters") or {}
    out = (notes.from_config(cfg, embedder) + chats.from_config(cfg, embedder) + memories.from_config(cfg, embedder)
           + service.from_config(cfg) + vaultmap.from_config(cfg) + codemap.from_config(cfg)
           + workmap.from_config(cfg))
    if a.get("pgvector"):
        from . import pgvector
        out += pgvector.from_config(cfg, embedder)
    return out


def register_kinds(shelves, register_kind):
    """Point every adapter instance's key namespace at the one public `item` renderer."""
    for s in shelves:
        register_kind(s.key_ns, render_item)


def refresh_all(shelves):
    out = {}
    for s in shelves:
        idx = getattr(s, "index", None)
        if idx is not None:
            try:
                out[s.name] = idx.refresh()
            except Exception as e:             # one broken index never stops the others
                log.warning("adapter %s: refresh failed (%s)", s.name, e)
                out[s.name] = {"error": str(e)}
    return out


def start_refresher(shelves, minutes=15, on_round=None):
    """Refresh now, then every `minutes`, in a daemon thread. `on_round(results)` (refresh_all's dict) is called
    after every round. Returns stop()."""
    stop = threading.Event()

    def loop():
        while True:
            res = refresh_all(shelves)
            if on_round is not None:
                try:
                    on_round(res)
                except Exception as e:     # a bad callback never stops the refreshing
                    log.warning("adapters: on_round failed (%s)", e)
            if stop.wait(max(1.0, float(minutes) * 60)):
                return

    threading.Thread(target=loop, name="adapters-refresh", daemon=True).start()
    return stop.set


def degraded(ctx):
    return sorted(getattr(ctx, "degraded", ()) or ())
