# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Antechamber settings — one file per machine, never in git.

Read from $ANTECHAMBER_CONFIG, else ~/.config/antechamber/config.toml. Every machine-specific value lives
there (database, embedder, service, and any section an extension reads with get()), so the engine's code names no machine, folder or user. A missing
file leaves generic defaults and LOADED = False — the service reports itself unhealthy, so a deploy without its
settings rolls back instead of running against the wrong database. Example: ../antechamber.example.toml."""
import os
import tomllib

PATH = os.environ.get("ANTECHAMBER_CONFIG") or os.path.expanduser("~/.config/antechamber/config.toml")
_d = {}
if os.path.exists(PATH):
    with open(PATH, "rb") as f:
        _d = tomllib.load(f)


def get(section, key, default=None):
    """One setting from the file: [section] key, or `default`. Extensions read their own sections with this."""
    return (_d.get(section) or {}).get(key, default)


_get = get


SERVICE_PORT = int(_get("service", "port", 8790))
EMBED_URL = _get("embedder", "url", "http://localhost:11434/api/embed")
PG_USER = _get("postgres", "user", os.environ.get("USER", ""))
LOADED = bool(_d)
PG_DB = _get("postgres", "database", "antechamber")
EMBED_MODEL = _get("embedder", "model", "qwen3-embedding:4b")
PG_PORT = str(_get("postgres", "port", 5432))
PG_BIN = _get("postgres", "bin_dir", "")                       # '' = psql on PATH
EMBED_DIMS = int(_get("embedder", "dimensions", 2000))
PG_HOST = _get("postgres", "host", "localhost")
# Discovery (spec 2026-10-14, phase 2): which shelf adapters and service extensions this machine loads.
# "module:attr" strings, imported with SHELF_IMPORT_ROOT (relative to this engine folder) appended to sys.path.
SHELF_ADAPTERS = list(_get("shelves", "adapters", []))           # each names an ordered list of Shelf
SHELF_IMPORT_ROOT = _get("shelves", "import_root", "..")
SERVICE_EXTENSIONS = list(_get("service", "extensions", []))     # each names a register(service) function
INDEX_REFRESH_MINUTES = float(_get("index", "refresh_minutes", 15))   # public adapters' index refresh (recall_service)
DIR = os.path.dirname(os.path.abspath(PATH))                    # the settings file's folder: relative paths in it resolve here


def raw():
    """The whole settings file as read (a deep copy) plus `_config_dir` = its folder: what the public adapters
    (engine/adapters/) build themselves from. {"_config_dir": ...} alone when there is no file."""
    import copy
    d = copy.deepcopy(_d)
    d["_config_dir"] = DIR
    return d


def psql_cmd(db=None):
    """psql argv up to and including -d <db>; callers append their own flags."""
    return [os.path.join(PG_BIN, "psql") if PG_BIN else "psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", db or PG_DB]
