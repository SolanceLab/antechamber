# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The port — the one shape every shelf adapter fits (spec specs/2026-10-14-antechamber-shelf-adapters.md).

A shelf adapter turns one store into a ranked list of items for a question. Antechamber fuses those lists by
rank (RRF), pins, dates and renders the winners; it never needs to know where an item came from.

    name          the shelf's short name
    key_ns        the fusion namespace its items live in (several shelves may share one)
    needs_vector  True when search uses the question's fingerprint (the engine embeds once, shared)
    default       True when recall searches this shelf unless the caller names its own set
    search(ctx)   → items, best first
    key(item)     → the fusion key: items from different shelves with the same key are the same thing
    weight(it, ctx) → this shelf's vote multiplier for that item (default 1.0)
    pins(pool, ctx) → keys that get a guaranteed seat; default none
    pin_order       where this shelf's pins go in line (lower first)
    is_stale(ctx) → True when the store behind the shelf is an out-of-date copy: its results are still used,
                    and the shelf is reported in `failed` as "<name>_stale" (default False)
    rides_with + active() → a shelf that joins automatically whenever the shelf named by rides_with is searched
                    and active() says yes (an optional extra signal)
    seeded_by + search_from(seed_pool, ctx) → a shelf that runs after the others, starting from the results of
                    the shelf named by seeded_by (only when recall is asked for links)

Rendering belongs to KINDS, not shelves: several shelves can find the same kind of thing (meaning and keyword
both find the same records), so a winner is written out by the renderer registered for its key namespace
(`register_kind`). Unregistered namespaces fall back to a plain note.
"""
from adapters.item import ITEM_PROTOCOL  # noqa: F401  (adapters.item owns the item protocol version; re-exported for shelf authors)

KINDS = {}


def register_kind(ns, render):
    """render(item, rctx) → winner dict, or None to skip (e.g. already covered by another winner's window)."""
    KINDS[ns] = render


class Ctx:
    """What a shelf may read: the store handle, the cleaned question (and the question as asked), its content
    words, the shared fingerprint (lazy), the pool size each shelf should return, and a per-recall memo."""
    def __init__(self, db, q, vec, q_raw=None, words=None, pool=20):
        self.db, self.q, self._vec, self.q_raw = db, q, vec, q_raw or q
        self._words, self.pool, self.memo = words, pool, {}

    def vec(self):
        return self._vec()

    @property
    def words(self):
        if self._words is None:
            from query import terms
            self._words = terms(self.q)
        return self._words


class Shelf:
    name = ""
    key_ns = ""            # fusion namespace
    id_field = "id"
    needs_vector = False
    default = True
    rides_with = None
    seeded_by = None
    pin_order = 100

    def search(self, ctx):
        raise NotImplementedError

    def search_from(self, seed_pool, ctx):
        raise NotImplementedError

    def key(self, it):
        return (self.key_ns, it[self.id_field]) if self.id_field != "path" else (self.key_ns, it.get("path"))

    def weight(self, it, ctx):
        return 1.0

    def pins(self, pool, ctx):
        return []

    def is_stale(self, ctx):
        return False

    def active(self):
        return True


class FnShelf(Shelf):
    """An adapter made from plain functions. A freshness check shared by several shelves runs once per recall
    (its answer is kept in ctx.memo, keyed by the function)."""
    def __init__(self, name, fn, key_ns, id_field="id", needs_vector=False, weight=None, pins=None, pin_order=100,
                 default=True, is_stale=None, rides_with=None, active=None, seeded_by=None, search_from=None):
        self.name, self._fn, self.key_ns, self.id_field = name, fn, key_ns, id_field
        self.needs_vector, self._weight = needs_vector, weight
        self._pins, self.pin_order = pins, pin_order
        self.default, self._is_stale = default, is_stale
        self.rides_with, self._active = rides_with, active
        self.seeded_by, self._search_from = seeded_by, search_from

    def search(self, ctx):
        return self._fn(ctx)

    def search_from(self, seed_pool, ctx):
        return self._search_from(seed_pool, ctx)

    def weight(self, it, ctx):
        return self._weight(it, ctx) if self._weight else 1.0

    def pins(self, pool, ctx):
        return self._pins(pool, ctx) if self._pins else []

    def is_stale(self, ctx):
        if self._is_stale is None:
            return False
        k = ("is_stale", self._is_stale)
        if k not in ctx.memo:
            ctx.memo[k] = bool(self._is_stale(ctx))
        return ctx.memo[k]

    def active(self):
        return self._active() if self._active else True
