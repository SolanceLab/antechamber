# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Antechamber recall: one call, every shelf searched, the results fused, the winners only handed back.

The caller never sees the churn. No language model anywhere in here: the embedder turns the question into
numbers once, the rest is ranking arithmetic.

Shelves are adapters (shelves/contract.py), each returning its own top-pool, best first. Which adapters exist
is decided by the settings file ([shelves] adapters), loaded when this module is imported; tests and harnesses
can install their own with install().
The [[adapters.*]] tables (notes folders, chat exports, memories, services, pgvector, a vault-map vault, a Graphify code map: engine/adapters/) are installed
after the [shelves] adapters; `python3 antechamber_recall.py --check [config]` validates them without
connecting to anything.

Fusion: reciprocal-rank fusion — score = sum over shelves of weight/(K + rank). Items several shelves find
under the same key fuse into one. Pins (guaranteed seats) come first, in pin_order; then the fused ranking.
Winners = top N, each written out by the renderer registered for its kind.
"""
import json, re, os, sys, importlib


def check_main(argv):
    """python3 antechamber_recall.py --check [config.toml] (default: $ANTECHAMBER_CONFIG, else
    ~/.config/antechamber/config.toml, the same lookup as config.PATH): print every public-adapter problem and
    exit 1, or print ok and exit 0. Runs before config.py is imported, so a malformed settings file is
    reported here instead of crashing the import. Connects to nothing."""
    import adapters as public
    path = argv[0] if argv else (os.environ.get("ANTECHAMBER_CONFIG") or os.path.expanduser("~/.config/antechamber/config.toml"))
    try:
        cfg = public.load_config(path)
    except (OSError, ValueError) as e:     # tomllib.TOMLDecodeError is a ValueError
        print(f"cannot read {path}: {e}")
        return 1
    if cfg.get("adapters") is None:
        print("ok")                        # no public adapters configured: nothing of theirs to check
        return 0
    probs = public.check_config(cfg)
    for p in probs:
        print("- " + p)
    print("ok" if not probs else f"{len(probs)} problem(s)")
    return 1 if probs else 0


if __name__ == "__main__" and sys.argv[1:2] == ["--check"]:
    sys.exit(check_main(sys.argv[2:]))     # before `import config`: a broken settings file must not crash --check

import config as C   # machine settings (~/.config/antechamber/config.toml), never in code
from query import STOP, terms, clean_query, is_list_question, META_PATTERNS, _META_RE, _EMOJI_RE, _LIST_Q  # noqa: F401
from dates import resolve_relative, day_label, _day, _NUM, _WEEKDAYS, _REL  # noqa: F401
from embed import embed
qwen = embed            # the name tests and harnesses patch or import
from concurrent.futures import ThreadPoolExecutor
from shelves import Shelf, FnShelf, Ctx, KINDS, register_kind   # noqa: F401

EMBED_TIMEOUT = 3      # s — the caller gives the whole recall a few seconds; a hung embedder must not eat it
N_DEFAULT = 8
SPREAD_CAP = int(os.environ.get("ANTECHAMBER_SPREAD", "0"))   # 0 = off (test knob): at most this many winners per moment
RRF_K = 60
POOL = 20              # each shelf's pool size (ctx.pool)


class AllShelvesFailed(RuntimeError):
    """Every shelf threw. Raised so the caller falls back — an outage must never read as
    'nothing matched'."""


# ---- the adapter registry. Insertion order is the fusion tie order, so install() keeps it.
PUBLIC_SHELVES = []    # the public adapters' shelves (engine/adapters/), installed after the [shelves] ones; recall_service refreshes these
# Extra seats: shelves whose fusion namespace is listed here (summary-style shelves, e.g. written memory entries)
# never take one of the n regular seats; up to EXTRA_SEATS of them ride alongside. Off unless configured:
# [recall] extra_seat_shelves = ["my-entries"], extra_seats = 3   (env ANTECHAMBER_EXTRA_SEAT_NS / _SEATS override).
# Measured on a 1,540-question chat benchmark: summaries in regular seats 80.0% all-evidence, raw lines only 82.4%,
# summaries in 3 extra seats 83.5% (+~500 tokens per recall).
_env_ns = os.environ.get("ANTECHAMBER_EXTRA_SEAT_NS")
EXTRA_SEAT_NS = {x for x in (_env_ns.split(",") if _env_ns is not None else C.get("recall", "extra_seat_shelves", [])) if x}
_env_seats = os.environ.get("ANTECHAMBER_EXTRA_SEATS")
EXTRA_SEATS = (int(_env_seats) if _env_seats else 0) if _env_seats is not None else int(C.get("recall", "extra_seats", 3))
EXTRA_SEATS = EXTRA_SEATS if EXTRA_SEAT_NS else 0
SHELVES = {}
PUBLIC_KINDS = ("notes", "chats", "memories", "service", "pgvector", "vaultmap", "codemap", "workmap")   # = adapters/check.py KINDS


def install(adapters):
    """Add shelf adapters to the registry, in the order given (a later adapter with the same name replaces one)."""
    for a in adapters:
        if not isinstance(a, Shelf):
            raise TypeError(f"not a shelf adapter: {a!r}")
        SHELVES[a.name] = a


def default_shelves():
    """Every registered adapter that recall searches unless told otherwise, in registry order (= tie order)."""
    return tuple(s for s, a in SHELVES.items() if a.default)


def _import_root():
    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.expanduser(C.SHELF_IMPORT_ROOT)))
    if root not in sys.path:
        sys.path.append(root)            # appended, never prepended: the engine's own modules always win
    return root


def load_configured(specs=None, cfg=None, embedder=None):
    """Install the adapters the settings file names: first every [shelves] adapters entry ("module:attr" -> an
    ordered list of Shelf), then the public adapters of its [[adapters.notes|chats|memories|service|pgvector|vaultmap|codemap|workmap]] tables, in
    config order. `cfg` (default: the settings file, C.raw()) and `embedder` (default: its [embedder] table) are
    for tests and demos. The public set is replaced as a whole on every call: built and validated first, then
    swapped in. An import error or a bad adapter setting raises: a recall service without its shelves must
    crash, not answer empty."""
    specs = C.SHELF_ADAPTERS if specs is None else specs
    if specs:
        _import_root()
    for spec in specs:
        mod, _, attr = spec.partition(":")
        install(getattr(importlib.import_module(mod), attr or "SHELVES"))
    cfg = C.raw() if cfg is None else cfg
    if cfg.get("adapters") is not None or PUBLIC_SHELVES:
        _load_public(cfg, embedder)
    return list(SHELVES)


def _load_public(cfg, embedder):
    import adapters as public                 # engine/adapters/: notes folders, chat exports, services, pgvector
    from adapters import index as pindex
    Err = public.ConfigError
    table = cfg.get("adapters")
    table = {} if table is None else table
    if not isinstance(table, dict):
        raise Err("[adapters] must be a table of adapter lists ([[adapters.notes]] ...)")
    unknown = sorted(set(table) - set(PUBLIC_KINDS))
    if unknown:
        raise Err(f"[[adapters.{unknown[0]}]]: unknown adapter (known: {', '.join(PUBLIC_KINDS)})")
    mine = {s.name for s in PUBLIC_SHELVES if SHELVES.get(s.name) is s}   # by identity: a configured shelf that took a public name is not ours
    seen = {}                                 # every id is validated, and unique across kinds, before anything is built
    for k in PUBLIC_KINDS:
        insts = table.get(k)
        if insts is None:
            continue
        if not isinstance(insts, list):
            raise Err(f"[[adapters.{k}]] must be a list of tables")
        for inst in insts:
            if not isinstance(inst, dict):
                raise Err(f"[[adapters.{k}]] entries must be tables")
            try:
                iid, _ = pindex.instance_ids(inst, k)
            except ValueError as e:
                raise Err(str(e)) from None
            if iid in seen:
                raise Err(f"adapter id {iid!r} is used twice ({seen[iid]} and {k}); ids must be unique")
            seen[iid] = k
            if iid in SHELVES and iid not in mine:     # a public id must never replace a configured shelf or kind
                raise Err(f"adapter id {iid!r} is already the name of a configured shelf; pick another id")
            if iid in KINDS and iid not in mine:
                raise Err(f"adapter id {iid!r} is already a registered kind; pick another id")
    cfg = dict(cfg)
    cfg.setdefault("_config_dir", C.DIR)
    try:
        shelves = public.load(cfg, embedder=embedder)
    except Err:
        raise
    except (ValueError, AttributeError, TypeError) as e:
        raise Err(f"public adapters ({', '.join(seen) or 'none'}): {type(e).__name__}: {e}") from None
    if sorted(s.name for s in shelves) != sorted(seen) or any(s.key_ns != s.name for s in shelves):
        raise Err("public adapters built shelves that do not match their ids")      # backstop: cannot happen
    for s in PUBLIC_SHELVES:                  # the new set is complete: only now does the old one go
        if SHELVES.get(s.name) is s:
            del SHELVES[s.name]
            KINDS.pop(s.key_ns, None)      # only if the name is still ours: a configured shelf of that name keeps its renderer
    install(shelves)
    public.register_kinds(shelves, register_kind)
    PUBLIC_SHELVES[:] = shelves


# ---- rendering: a winner of each kind is written out by its registered renderer; unknown kinds as a note
class _RCtx:
    """What a renderer may read: the store handle, whether to expand context (windows), and the keys already
    covered by an earlier winner's context."""
    def __init__(self, db, windows):
        self.db, self.windows, self.covered = db, windows, set()


def _render_note(it, r):
    return {"kind": "note", "path": it.get("path"), "title": it.get("title"), "snippet": (it.get("snippet") or "")[:220]}


register_kind("note", _render_note)


# ---------- answer-shaping switches, each OFF unless its environment variable says "on" ----------
# WIDE: a question that asks for several things ("what activities…", "which places…", "how many times…")
#   gets WIDE_N slots instead of n (in testing, the missed multi-part pieces sat at ranks 9–30).
# DATES: "yesterday", "last Saturday", "two weeks ago" in a recalled line get the real date beside them,
#   counted by code from the day the line was said. Vague phrases ("the other day", "recently") get nothing.
# CHRONO: winners are handed back oldest → newest, so the newest version of a fact comes last.
DATES = os.environ.get("ANTECHAMBER_DATES") == "on"
WIDE = os.environ.get("ANTECHAMBER_WIDE") == "on"
CHRONO = os.environ.get("ANTECHAMBER_CHRONO") == "on"
WIDE_N = int(os.environ.get("ANTECHAMBER_WIDE_N", "16"))


def recall(q, db=None, n=N_DEFAULT, shelves=None, windows=True, links=None, solo=False):
    """Return {"query", "winners": [...], "shelves": {name: pool_size}, "failed": [names]}.

    Each winner is written out by the renderer registered for its kind (register_kind); an unregistered kind
    comes back as {"kind":"note","path","title","snippet"}. `windows` is passed to renderers (they may expand
    a hit with its surrounding context). Contract the engine relies on: a dated winner carries an ISO `date`
    (it gains `day`), and a {"kind":"message"} winner carries `lines` [{"message", ...}] (DATES notes them).
    `links` (default: ANTECHAMBER_LINKS=on) runs the shelves seeded by another shelf's results.
    `solo` searches exactly the shelves named: no riding shelves, no linked shelves (the MCP search_store tool).
    `"degraded": [ids]` appears only when a public adapter answered by words only.
    """
    db = db or C.PG_DB
    n = max(1, min(20, int(n)))
    if WIDE and is_list_question(q):      # several moments needed → more slots
        n = max(n, min(20, WIDE_N))
    q_asked = q
    q = clean_query(q)                    # instructions to the assistant and laughter are not what to find
    shelves = tuple(default_shelves() if shelves is None else shelves)
    for s, a in SHELVES.items():          # shelves that ride along with one being searched
        if not solo and a.rides_with and a.rides_with in shelves and s not in shelves and a.active():
            shelves += (s,)
    with ThreadPoolExecutor(len(shelves) + 1) as ex:
        # One bounded embed, started alongside the shelves that don't need it. Shelves that need the
        # fingerprint wait on it; the others never do. A failed or hung embed fails those shelves once —
        # it is not retried per shelf, and it cannot hold the keyword shelves hostage.
        emb = ex.submit(qwen, q, EMBED_TIMEOUT) if any(SHELVES[s].needs_vector for s in shelves) else None
        vec = lambda: "[" + ",".join(str(x) for x in emb.result()) + "]"
        ctx = Ctx(db, q, vec, q_raw=q_asked, pool=POOL)
        futs = {s: ex.submit(SHELVES[s].search, ctx) for s in shelves}
        pools, failed = {}, []
        for s, f in futs.items():
            try:
                pools[s] = f.result()
            except Exception:
                pools[s] = []       # one broken shelf never takes the others down
                failed.append(s)
    # Any failure that leaves NOTHING to hand back is an outage, not an empty answer (embedder down +
    # a question of only common words must not read as "nothing came back").
    # A shelf whose store is an out-of-date copy is reported, never served as current.
    stale_names = [s for s in shelves if SHELVES[s].is_stale(ctx)]
    if stale_names:
        failed += [s for s in stale_names if s not in failed and s + "_stale" not in failed]
        failed = [f"{s}_stale" if s in stale_names else s for s in failed]
    if failed and not any(pools.values()):
        raise AllShelvesFailed(",".join(failed))
    if solo:
        links = False
    elif links is None:
        links = os.environ.get("ANTECHAMBER_LINKS") == "on"
    if links:
        for s, a in SHELVES.items():      # shelves that start from another shelf's results
            if a.seeded_by and pools.get(a.seeded_by):
                try:
                    pools[s] = a.search_from(pools[a.seeded_by], ctx)
                except Exception:
                    pools[s] = []
                    failed.append(s)

    score, item = {}, {}
    for s, pool in pools.items():
        for rank, it in enumerate(pool):
            key = SHELVES[s].key(it)
            w = SHELVES[s].weight(it, ctx)
            score[key] = score.get(key, 0.0) + w / (RRF_K + rank + 1)
            item[key] = it

    # Pins: each shelf may give some items a guaranteed seat (a name asked for, a rare word's best hit) —
    # single-shelf items otherwise lose RRF to anything two shelves half-agree on. Lower pin_order first.
    pins = []
    for s_ in sorted(pools, key=lambda x: SHELVES[x].pin_order):
        pins += SHELVES[s_].pins(pools[s_], ctx)
    order = list(dict.fromkeys(pins + sorted(score, key=lambda k: -score[k])))
    if SPREAD_CAP > 0:
        # Test knob, off by default: at most SPREAD_CAP winners per moment (same day + same
        # conversation) before other moments get a turn; the rest wait at the back, so slots are never left
        # empty. Pins are exempt. Principle: a question about several occasions needs several occasions.
        def _moment(k):
            it = item[k]; d = it.get("said_at") or it.get("date") or it.get("created_at")
            return (str(d)[:10], it.get("thread") or "") if d else None
        seen_m, front, back = {}, [], []
        for k in order:
            m = None if (k in pins or k[0] in EXTRA_SEAT_NS) else _moment(k)   # pins and side seats are exempt
            if m is None or seen_m.get(m, 0) < SPREAD_CAP:
                front.append(k)
                if m is not None: seen_m[m] = seen_m.get(m, 0) + 1
            else:
                back.append(k)
        order = front + back
    # Extra seats (switch, off by default): winners whose fusion namespace is listed in EXTRA_SEAT_NS ride
    # alongside the n regular seats, up to EXTRA_SEATS of them, instead of taking a regular seat. Use case:
    # summary-style shelves that otherwise push the raw evidence out of the top n (measured on a chat benchmark).
    winners, extras, rctx = [], [], _RCtx(db, windows)
    for key in order:
        if len(winners) >= n and len(extras) >= EXTRA_SEATS:
            break
        side = key[0] in EXTRA_SEAT_NS            # side-seat kinds never take a regular seat
        if (side and len(extras) >= EXTRA_SEATS) or (not side and len(winners) >= n):
            continue
        w = KINDS.get(key[0], _render_note)(item[key], rctx)
        if w is not None:
            (extras if side else winners).append(w)
    n_regular = len(winners)
    winners += extras
    # Every dated winner also carries `day` (it measurably helped date questions),
    # the date as a person reads it ("Friday 2 October 2026"), so "yesterday"/"last Tuesday" are counted
    # from a visible weekday. `date` itself stays ISO: callers sort and window by it.
    for w in winners:
        w_day = _day(w.get("date"))
        if w_day:
            w["day"] = w_day
        if DATES and w.get("kind") == "message" and w.get("date"):
            for ln in w.get("lines") or []:
                notes = resolve_relative(ln.get("message"), w["date"])
                if notes:
                    ln["dates"] = notes          # shown beside the line, never replacing it
    if CHRONO:                                   # oldest → newest; undated keep their place; side seats stay after the regulars
        def _chrono(ws):
            dated = [w for w in ws if w.get("date")]
            return sorted(dated, key=lambda w: str(w["date"])) + [w for w in ws if not w.get("date")]
        winners = _chrono(winners[:n_regular]) + _chrono(winners[n_regular:])
    out = {"query": q_asked, "searched": q, "winners": winners, "shelves": {s: len(p) for s, p in pools.items()}, "failed": failed}
    if PUBLIC_SHELVES:                        # only installs with public adapters can be degraded; House output unchanged
        import adapters as public
        deg = public.degraded(ctx)
        if deg:
            out["degraded"] = deg             # adapters that answered by words only (their question embedding failed)
    return out


# Names that lived in this module before the shelves moved out to their adapters (14 Oct 2026). Reading or
# setting one here would silently do nothing, so it fails loudly instead.
_MOVED = frozenset("""BEFORE AFTER JOURNAL_CAP PSQL_TIMEOUT MSG_CAP EXCERPT_CAP PERSON_CAP _vaultmap _vault psql _rows
shelf_mem LINK_SEEDS LINK_WEIGHT LINK_TYPES shelf_lnk _ROOMCOL MSG_COLS _msg_cols MSG_CHUNKS MSG_EMB_COL BRIDGE
BRIDGE_WORDS BRIDGE_SIM BRIDGE_WEIGHT shelf_msg RARE_DF RARE_FRAC RARE_COVERAGE JRN_DOC DF_CAP MSG_DOC _idf _kw
shelf_kw shelf_jrn shelf_brg shelf_jkw shelf_ppl DOC_DOC shelf_doc shelf_dkw RULE_SIM shelf_rul RULE_PINS
RULE_PIN_SIM RULE_PIN_BY_SIM SHELVES_STALE_H DOC_WEIGHTS _doc_w _named shelves_stale VAULT_SKIP shelf_vault ROOM
_thread_window _window ALL_SHELVES _pin_rules _pin_rare _systems_scale _DOCW _STALE _render_msg _render_rule
SYSTEM_WORDS SYSTEMS_DOC_WEIGHT about_systems _TUNABLE""".split())


class _Guarded(type(sys)):
    def __getattr__(self, name):
        if name in _MOVED:
            raise AttributeError(f"antechamber_recall.{name} moved out of the engine (to the shelf adapters that use it)")
        raise AttributeError(f"module 'antechamber_recall' has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name in _MOVED:
            raise AttributeError(f"antechamber_recall.{name} moved out of the engine: setting it here would do nothing")
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _Guarded
load_configured()


if __name__ == "__main__":
    print(json.dumps(recall(" ".join(sys.argv[1:])), indent=1, ensure_ascii=False))
