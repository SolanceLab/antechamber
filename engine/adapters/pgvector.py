# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""pgvector — an existing Postgres table with a pgvector column, searched by meaning.

    [[adapters.pgvector]]
    id = "papers"                 # immutable; the fusion key namespace
    name = "Paper notes"          # shown only, and the default source label
    dsn_env = "PAPERS_DSN"        # NAME of an environment variable holding the connection string
    schema = "public"
    table = "passages"
    id_col = "id"
    text_col = "body"
    vec_col = "embedding"
    date_col = "created_at"       # optional
    source_col = "origin"         # optional
    title_col = ""                # optional
    limit = 20
    statement_timeout_ms = 3000

Needs the psycopg package (pip install "psycopg[binary]") and the [embedder] table, whose model must be the one
that filled the column: the column's dimension is checked against [embedder] dims when the adapter loads.
Every identifier goes through psycopg.sql.Identifier, the vector and the limit are parameters, the query runs in
a READ ONLY transaction under a statement timeout, ordered by cosine distance (<=>) then id.
"""
import os
import re

from . import embedders, index as ix
from .item import TEXT_CAP, normalize_date

REQUIRED = ("dsn_env", "table", "id_col", "text_col", "vec_col")
OPTIONAL = ("date_col", "source_col", "title_col")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PgvectorError(RuntimeError):
    pass


def validate(inst):
    """Plain-language problems with one [[adapters.pgvector]] table (no database or psycopg needed)."""
    iid = inst.get("id", "?")
    probs = []
    for k in REQUIRED:
        if not inst.get(k):
            probs.append(f"[[adapters.pgvector]] {iid}: {k} is missing")
    for k in ("schema", "table", "id_col", "text_col", "vec_col") + OPTIONAL:
        v = inst.get(k)
        if v in (None, "") and k != "schema":
            continue
        if k == "schema" and v in (None, ""):
            v = "public"
        if not isinstance(v, str) or not IDENT.match(v):
            probs.append(f"[[adapters.pgvector]] {iid}: {k} must be a plain name (letters, digits, _), got {v!r}")
    return probs


def _psycopg():
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        raise embedders.ConfigError('the pgvector adapter needs the psycopg package: pip install "psycopg[binary]"') \
            from None
    return psycopg, sql


def build_query(inst, sql):
    """The search statement, composed from validated identifiers only."""
    cols = [sql.Identifier(inst["id_col"]), sql.Identifier(inst["text_col"])]
    for k in OPTIONAL:
        cols.append(sql.Identifier(inst[k]) if inst.get(k) else sql.SQL("NULL"))
    tbl = sql.Identifier(inst.get("schema") or "public", inst["table"])
    vec = sql.Identifier(inst["vec_col"])
    return sql.SQL("SELECT {cols} FROM {tbl} WHERE {vec} IS NOT NULL "
                   "ORDER BY {vec} <=> %s::vector, {id} LIMIT %s").format(
        cols=sql.SQL(", ").join(cols), tbl=tbl, vec=vec, id=sql.Identifier(inst["id_col"]))


def from_config(cfg, embedder=None):
    insts = ((cfg or {}).get("adapters") or {}).get("pgvector") or []
    if not insts:
        return []
    psycopg, sql = _psycopg()                      # refuses to load without psycopg, plainly
    from shelves import FnShelf
    if embedder is None:
        embedder = embedders.shared(cfg)
    if embedder is None:
        raise embedders.ConfigError("the pgvector adapter needs an [embedder] table")
    shelves = []
    for inst in insts:
        iid, name = ix.instance_ids(inst, "pgvector")
        probs = validate(inst)
        if probs:
            raise embedders.ConfigError("; ".join(probs))
        dsn = os.environ.get(inst["dsn_env"], "")
        if not dsn:
            raise embedders.ConfigError(f"[[adapters.pgvector]] {iid}: environment variable {inst['dsn_env']} is not set")
        timeout_ms = int(inst.get("statement_timeout_ms", 3000))
        limit = max(1, min(50, int(inst.get("limit", 20))))
        query = build_query(inst, sql)
        tbl = sql.Identifier(inst.get("schema") or "public", inst["table"])
        vec = sql.Identifier(inst["vec_col"])

        def run(stmt, params, dsn=dsn, timeout_ms=timeout_ms):
            with psycopg.connect(dsn, connect_timeout=max(1, timeout_ms // 1000)) as conn:
                with conn.transaction():
                    conn.execute("SET TRANSACTION READ ONLY")
                    conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
                    return conn.execute(stmt, params).fetchall()

        got = run(sql.SQL("SELECT vector_dims({vec}) FROM {tbl} WHERE {vec} IS NOT NULL LIMIT 1").format(
            vec=vec, tbl=tbl), ())
        if got and int(got[0][0]) != embedder.dims:
            raise embedders.ConfigError(f"[[adapters.pgvector]] {iid}: column {inst['vec_col']} holds {got[0][0]}-"
                                        f"dimension vectors but [embedder] dims is {embedder.dims}")

        def search(ctx, run=run, query=query, limit=limit, iid=iid, name=name):
            qvec = embedder.query(getattr(ctx, "q", ""))     # no fallback: a meaning-only store
            lit = "[" + ",".join(repr(float(x)) for x in qvec) + "]"
            out = []
            for r in run(query, (lit, limit)):
                out.append({"id": str(r[0]), "text": str(r[1] or "")[:TEXT_CAP], "date": normalize_date(r[2]),
                            "source": (str(r[3]) if r[3] is not None else name),
                            "title": (str(r[4]) if r[4] is not None else None), "section": None, "metadata": {},
                            "kind": "item", "_ns": iid})
            return out

        shelves.append(FnShelf(iid, search, iid, "id"))
    return shelves

