# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Check an adapters config before starting: python3 -m adapters.check <config.toml>

check(cfg) -> list of plain-language problems ([] = ready). It never connects to anything and never sends data:
it reads the settings, the environment, the folders and the installed packages.
"""
import importlib.util
import os
import sys
import tempfile
import tomllib

from . import embedders, index as ix
from .chats import FORMATS
from .pgvector import validate as pg_validate

KNOWN_TOP = {"embedder", "index", "adapters", "_config_dir"}
KINDS = ("notes", "chats", "memories", "service", "pgvector", "vaultmap", "codemap", "workmap")


def load_config(path):
    """Read a TOML config; relative paths inside it are later resolved against its folder."""
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_config_dir"] = os.path.dirname(os.path.abspath(path))
    return cfg


def _path(base, p):
    p = os.path.expanduser(str(p))
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p))


def check(cfg):
    probs = []
    cfg = cfg or {}
    base = cfg.get("_config_dir") or os.getcwd()
    adapters = cfg.get("adapters") or {}
    if not isinstance(adapters, dict):
        return ["[adapters] must be a table of adapter lists ([[adapters.notes]] ...)"]
    for k in adapters:
        if k not in KINDS:
            probs.append(f"[[adapters.{k}]]: unknown adapter (known: {', '.join(KINDS)})")
    insts = [(k, i) for k in KINDS for i in (adapters.get(k) or [])]
    if not insts:
        probs.append("no adapters configured: add at least one [[adapters.notes]], [[adapters.chats]], "
                     "[[adapters.service]], [[adapters.pgvector]], [[adapters.vaultmap]], [[adapters.codemap]] or [[adapters.workmap]] table")

    # ids: present, well-formed, unique across every adapter
    seen = {}
    for k, inst in insts:
        if not isinstance(inst, dict):
            probs.append(f"[[adapters.{k}]] entries must be tables")
            continue
        try:
            iid, _ = ix.instance_ids(inst, k)
        except ValueError as e:
            probs.append(str(e))
            continue
        if iid in seen:
            probs.append(f"adapter id {iid!r} is used twice ({seen[iid]} and {k}); ids must be unique")
        seen.setdefault(iid, k)

    # embedder
    e = cfg.get("embedder")
    needs_vec = any(k in ("pgvector",) for k, _ in insts)
    if e:
        backend = e.get("backend", "ollama")
        if backend not in embedders.BACKENDS:
            probs.append(f"[embedder] backend must be one of {sorted(embedders.BACKENDS)} (got {backend!r})")
        if not e.get("model"):
            probs.append("[embedder] model is missing")
        if not (e.get("dims") or e.get("dimensions")):
            probs.append("[embedder] dims is missing (the length of the model's vectors)")
        try:
            _, host, _, _, _ = embedders.parse_url(e.get("url"), "[embedder]")
            if not embedders.host_is_local(host) and e.get("allow_remote_data") is not True:
                probs.append(f"[embedder] {host} is not on this machine; set allow_remote_data = true to send "
                             "your passages there, or use a local embedder")
        except embedders.ConfigError as err:
            probs.append(str(err))
        if e.get("api_key_env") and not os.environ.get(e["api_key_env"]):
            probs.append(f"[embedder] environment variable {e['api_key_env']} is not set")
    elif needs_vec:
        probs.append("the pgvector adapter needs an [embedder] table")

    # index dir: only needed by notes/chats/memories
    if any(k in ("notes", "chats", "memories") for k, _ in insts):
        d = _path(base, ((cfg.get("index") or {}).get("dir")) or ix.default_dir())
        try:
            os.makedirs(d, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=d):
                pass
        except OSError as err:
            probs.append(f"[index] dir {d} is not writable ({err.strerror or err})")

    for k, inst in insts:
        if not isinstance(inst, dict):
            continue
        iid = inst.get("id", "?")
        where = f"[[adapters.{k}]] {iid}"
        if k == "notes":
            if not inst.get("folder"):
                probs.append(f"{where}: folder is missing")
            elif not os.path.isdir(_path(base, inst["folder"])):
                probs.append(f"{where}: folder {_path(base, inst['folder'])} does not exist")
        elif k == "chats":
            if inst.get("format") not in FORMATS:
                probs.append(f"{where}: format must be one of {list(FORMATS)} (got {inst.get('format')!r})")
            if not inst.get("path"):
                probs.append(f"{where}: path is missing")
            elif not os.path.exists(_path(base, inst["path"])):
                probs.append(f"{where}: {_path(base, inst['path'])} does not exist")
        elif k == "memories":
            if not inst.get("folder"):
                probs.append(f"{where}: folder is missing")
            elif not os.path.isdir(_path(base, inst["folder"])):
                probs.append(f"{where}: folder {_path(base, inst['folder'])} does not exist")
            if inst.get("export") and not os.path.exists(_path(base, inst["export"])):
                probs.append(f"{where}: export {_path(base, inst['export'])} does not exist")
            red = inst.get("redundant_with", [])
            if not isinstance(red, list) or not all(isinstance(x, str) for x in red):
                probs.append(f"{where}: redundant_with must be a list of adapter ids")
        elif k == "service":
            try:
                embedders.parse_url(inst.get("url"), where)
            except embedders.ConfigError as err:
                probs.append(str(err))
            if inst.get("api_key_env") and not os.environ.get(inst["api_key_env"]):
                probs.append(f"{where}: environment variable {inst['api_key_env']} is not set")
        elif k == "vaultmap":
            if not inst.get("folder"):
                probs.append(f"{where}: folder is missing")
            elif not os.path.isdir(_path(base, inst["folder"])):
                probs.append(f"{where}: folder {_path(base, inst['folder'])} does not exist")
            if not inst.get("vault_map"):
                probs.append(f"{where}: vault_map (the path to vault-map.py) is missing")
            elif not os.path.isfile(_path(base, inst["vault_map"])):
                probs.append(f"{where}: vault_map {_path(base, inst['vault_map'])} is not a file")
        elif k == "codemap":
            if not inst.get("graph"):
                probs.append(f"{where}: graph (the path to Graphify's graph.json) is missing")
            elif not os.path.isfile(_path(base, inst["graph"])):
                probs.append(f"{where}: graph {_path(base, inst['graph'])} is not a file")
        elif k == "workmap":
            for key, what in (("work_map", "the path to work_map.py"), ("config", "the path to work-map's config")):
                if not inst.get(key):
                    probs.append(f"{where}: {key} ({what}) is missing")
                elif not os.path.isfile(_path(base, inst[key])):
                    probs.append(f"{where}: {key} {_path(base, inst[key])} is not a file")
        elif k == "pgvector":
            probs += pg_validate(inst)
            if inst.get("dsn_env") and not os.environ.get(inst["dsn_env"]):
                probs.append(f"{where}: environment variable {inst['dsn_env']} is not set")
            if importlib.util.find_spec("psycopg") is None:
                probs.append(f'{where}: needs the psycopg package (pip install "psycopg[binary]")')
    return probs


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python3 -m adapters.check <config.toml>", file=sys.stderr)
        return 2
    try:
        cfg = load_config(argv[0])
    except (OSError, tomllib.TOMLDecodeError) as err:
        print(f"cannot read {argv[0]}: {err}")
        return 1
    probs = check(cfg)
    for p in probs:
        print("- " + p)
    print("ok: ready" if not probs else f"{len(probs)} problem(s)")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
