# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Antechamber recall — engine behaviour with fake shelf adapters: failures (Codex review, 2026-09-29), fusion,
pins, the port's hooks and the moved-name guard. No database, no embedder: every adapter here is a fake.

Run: python3 -m unittest test_antechamber_recall -v   (from engine/)
"""
import time
import unittest
from unittest import mock
import antechamber_recall as A
from shelves import FnShelf, register_kind

register_kind("tst", lambda it, r: {"kind": "thing", "id": it["id"], "date": it.get("date"), "text": it.get("text")})
register_kind("tmsg", lambda it, r: {"kind": "message", "date": it["date"], "lines": [{"message": it["text"], "hit": True}]})

T1 = {"id": "t1", "date": "2026-09-18", "text": "one"}
T2 = {"id": "t2", "date": None, "text": "two"}
NOTE = {"path": "notes/x.md", "title": "Note", "snippet": "a note"}


def boom(*_a, **_k):
    raise RuntimeError("down")


def fakes(vec1=lambda c: [], vec2=lambda c: [], kw=lambda c: [], notes=lambda c: []):
    """Two shelves that need the fingerprint, two that do not — the shape of a real install."""
    reg = {"vec1": FnShelf("vec1", lambda c: (c.vec(), vec1(c))[1], "tst", needs_vector=True),
           "vec2": FnShelf("vec2", lambda c: (c.vec(), vec2(c))[1], "tst", needs_vector=True),
           "kw": FnShelf("kw", kw, "tst"),
           "notes": FnShelf("notes", notes, "note", "path")}
    return mock.patch.dict(A.SHELVES, reg, clear=True)


class Failures(unittest.TestCase):
    def test_embedder_down_keyword_and_notes_still_answer(self):
        with mock.patch.object(A, "qwen", lambda q, t=None: boom()), fakes(kw=lambda c: [T1], notes=lambda c: [NOTE]):
            out = A.recall("dark water", windows=False)
        self.assertEqual(sorted(out["failed"]), ["vec1", "vec2"])
        self.assertEqual([w["kind"] for w in out["winners"]], ["thing", "note"])

    def test_hung_embedder_is_bounded_and_does_not_hold_the_others_hostage(self):
        calls = []

        def hung(q, timeout):          # honours its timeout the way urlopen does, then fails
            calls.append(timeout)
            time.sleep(timeout)
            raise TimeoutError("embed hung")
        t0 = time.time()
        with mock.patch.object(A, "EMBED_TIMEOUT", 0.3), mock.patch.object(A, "qwen", hung), \
             fakes(kw=lambda c: [T1], notes=lambda c: [NOTE]):
            out = A.recall("dark water", windows=False)
        self.assertLess(time.time() - t0, 1.0)            # bounded by EMBED_TIMEOUT, not 30s
        self.assertEqual(calls, [0.3])                     # embedded ONCE, not retried per shelf
        self.assertEqual(sorted(out["failed"]), ["vec1", "vec2"])
        self.assertEqual([w["kind"] for w in out["winners"]], ["thing", "note"])

    def test_every_shelf_failing_raises_instead_of_reading_as_no_match(self):
        with mock.patch.object(A, "qwen", lambda q, t=None: boom()), fakes(kw=boom, notes=boom):
            with self.assertRaises(A.AllShelvesFailed):
                A.recall("dark water", windows=False)

    def test_embedder_down_and_the_rest_empty_is_an_outage_not_an_empty_answer(self):
        with mock.patch.object(A, "qwen", lambda q, t=None: boom()), fakes():
            with self.assertRaises(A.AllShelvesFailed):
                A.recall("what did she say", windows=False)

    def test_empty_but_healthy_shelves_are_an_honest_empty_result(self):
        with mock.patch.object(A, "qwen", lambda q, t=None: [0.0]), fakes():
            out = A.recall("nothing", windows=False)
        self.assertEqual(out["winners"], [])
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["shelves"], {"vec1": 0, "vec2": 0, "kw": 0, "notes": 0})

    def test_no_adapters_installed_answers_empty(self):
        with mock.patch.dict(A.SHELVES, {}, clear=True):
            out = A.recall("anything", windows=False)
        self.assertEqual((out["winners"], out["shelves"], out["failed"]), ([], {}, []))

    def test_no_degraded_key_without_public_adapters(self):
        with mock.patch.object(A, "qwen", lambda q, t=None: [0.0]), fakes(kw=lambda c: [T1]), mock.patch.object(A, "PUBLIC_SHELVES", []):
            out = A.recall("x", windows=False)
        self.assertEqual(list(out), ["query", "searched", "winners", "shelves", "failed"])

    def test_degraded_listed_only_when_non_empty(self):
        def marks(c):
            c.__dict__.setdefault("degraded", set()).add("p")
            return [T1]
        with mock.patch.object(A, "qwen", lambda q, t=None: [0.0]), \
             mock.patch.dict(A.SHELVES, {"p": FnShelf("p", marks, "tst")}, clear=True), mock.patch.object(A, "PUBLIC_SHELVES", [object()]):
            self.assertEqual(A.recall("x", windows=False)["degraded"], ["p"])
            A.SHELVES["p"] = FnShelf("p", lambda c: [T1], "tst")
            self.assertNotIn("degraded", A.recall("x", windows=False))

    def test_contract_reexports_the_item_protocol(self):
        from shelves import contract
        from adapters import item
        self.assertEqual(contract.ITEM_PROTOCOL, item.ITEM_PROTOCOL)
        with open(contract.__file__) as f:
            self.assertNotIn("ITEM_PROTOCOL =", f.read())


class Fusion(unittest.TestCase):
    def test_an_item_two_shelves_agree_on_fuses_and_ranks_first(self):
        with mock.patch.object(A, "qwen", lambda q, t=None: [0.0]), \
             fakes(vec1=lambda c: [T2, T1], kw=lambda c: [T1], notes=lambda c: [NOTE]):
            out = A.recall("one", windows=False)
        self.assertEqual(out["winners"][0]["id"], "t1")
        self.assertEqual(out["winners"][0]["day"], "Friday 18 September 2026")   # dated winners carry the weekday
        self.assertEqual(len(out["winners"]), 3)

    def test_pins_take_their_seats_first_in_pin_order(self):
        reg = {"a": FnShelf("a", lambda c: [T1, T2], "tst"),
               "p2": FnShelf("p2", lambda c: [NOTE], "note", "path", pin_order=2, pins=lambda pool, c: [("note", pool[0]["path"])]),
               "p1": FnShelf("p1", lambda c: [T2], "tst", pin_order=1, pins=lambda pool, c: [("tst", "t2")])}
        with mock.patch.dict(A.SHELVES, reg, clear=True):
            out = A.recall("x", windows=False)
        self.assertEqual([w.get("id") or w.get("path") for w in out["winners"]], ["t2", "notes/x.md", "t1"])

    def test_relative_dates_ride_beside_message_lines_when_switched_on(self):
        reg = {"m": FnShelf("m", lambda c: [{"id": "m1", "date": "2023-05-08", "text": "we went yesterday"}], "tmsg")}
        with mock.patch.dict(A.SHELVES, reg, clear=True), mock.patch.object(A, "DATES", True):
            out = A.recall("x", windows=False)
        self.assertEqual(out["winners"][0]["lines"][0]["dates"], ["yesterday = Sunday 7 May 2023"])

    def test_the_question_words_are_tsquery_safe(self):
        self.assertEqual(A.terms("Robin's 'thalassophobia' & | ! :* drop;--"), ["robin", "thalassophobia", "drop"])


class Registry(unittest.TestCase):
    def test_install_keeps_order_and_refuses_what_is_not_an_adapter(self):
        with mock.patch.dict(A.SHELVES, {}, clear=True):
            A.install([FnShelf("b", lambda c: [], "tst"), FnShelf("a", lambda c: [], "tst")])
            self.assertEqual(list(A.SHELVES), ["b", "a"])
            with self.assertRaises(TypeError):
                A.install(["not a shelf"])

    def test_configured_adapters_load_from_module_attr_specs(self):
        import sys, types
        m = types.ModuleType("fake_adapters_mod")
        m.SHELVES = [FnShelf("z", lambda c: [], "tst")]
        with mock.patch.dict(sys.modules, {"fake_adapters_mod": m}), mock.patch.dict(A.SHELVES, {}, clear=True):
            self.assertEqual(A.load_configured(["fake_adapters_mod:SHELVES"]), ["z"])
        with self.assertRaises(ImportError):
            A.load_configured(["no_such_adapters_module:SHELVES"])


class MovedNames(unittest.TestCase):
    """Names that moved out with the House adapters fail loudly here, never silently (split S4)."""
    def test_reading_or_setting_a_moved_name_raises(self):
        with self.assertRaises(AttributeError):
            A.shelf_kw
        with self.assertRaises(AttributeError):
            A.BRIDGE = False
        self.assertFalse(hasattr(A, "MSG_EMB_COL"))
        A.WIDE = A.WIDE                                 # engine names stay settable


class Hooks(unittest.TestCase):
    """The port's hooks (split S3): recall names no shelf; riding, seeding and freshness come from the adapters."""
    A1 = {"id": "a1", "path": "notes/a1.md", "title": "A1", "snippet": "one"}
    B1 = {"id": "b1", "path": "notes/b1.md", "title": "B1", "snippet": "two"}

    def _reg(self, **extra):
        from shelves import FnShelf
        reg = {"a": FnShelf("a", lambda c: [self.A1], "note", "path"),
               "b": FnShelf("b", lambda c: [self.B1], "note", "path", default=False)}
        reg.update(extra)
        return mock.patch.dict(A.SHELVES, reg, clear=True)

    def test_default_set_is_every_default_adapter_in_registry_order(self):
        with self._reg():
            self.assertEqual(A.default_shelves(), ("a",))
            out = A.recall("anything", windows=False)
        self.assertEqual(list(out["shelves"]), ["a"])

    def test_a_riding_shelf_joins_only_when_active(self):
        from shelves import FnShelf
        on = {"on": True}
        rider = FnShelf("r", lambda c: [self.B1], "note", "path", default=False, rides_with="a", active=lambda: on["on"])
        with self._reg(r=rider):
            self.assertEqual(list(A.recall("x", windows=False)["shelves"]), ["a", "r"])
            on["on"] = False
            self.assertEqual(list(A.recall("x", windows=False)["shelves"]), ["a"])
            self.assertEqual(list(A.recall("x", windows=False, shelves=("b",))["shelves"]), ["b"])

    def test_a_seeded_shelf_runs_after_from_its_seed_and_only_with_links(self):
        from shelves import FnShelf
        seen = []
        def grow(seed, c):
            seen.append([it["id"] for it in seed])
            return [self.B1]
        sd = FnShelf("s", None, "note", "path", default=False, seeded_by="a", search_from=grow)
        with self._reg(s=sd):
            self.assertEqual(list(A.recall("x", windows=False, links=False)["shelves"]), ["a"])
            out = A.recall("x", windows=False, links=True)
        self.assertEqual(list(out["shelves"]), ["a", "s"])
        self.assertEqual(seen, [["a1"]])

    def test_a_failing_seeded_shelf_is_reported(self):
        from shelves import FnShelf
        sd = FnShelf("s", None, "note", "path", default=False, seeded_by="a", search_from=boom)
        with self._reg(s=sd):
            out = A.recall("x", windows=False, links=True)
        self.assertEqual(out["failed"], ["s"])

    def test_a_shared_freshness_check_runs_once_and_marks_its_shelves(self):
        from shelves import FnShelf
        calls = []
        stale = lambda c: calls.append(1) or True
        reg = dict(a=FnShelf("a", lambda c: [self.A1], "note", "path", is_stale=stale),
                   c=FnShelf("c", lambda c: [self.B1], "note", "path", is_stale=stale))
        with self._reg(**reg):
            out = A.recall("x", windows=False, shelves=("a", "c"))
        self.assertEqual(calls, [1])
        self.assertEqual(out["failed"], ["a_stale", "c_stale"])


if __name__ == "__main__":
    unittest.main()


class ExtraSeats(unittest.TestCase):
    """Summary-style shelves in extra seats never take a regular seat (14 Oct)."""
    def _run(self, ns, seats, n=3):
        from shelves import FnShelf
        regular = [{"id": f"r{i}", "path": f"r{i}", "title": "", "snippet": f"raw {i}"} for i in range(5)]
        summaries = [{"id": f"s{i}", "path": f"s{i}", "title": "", "snippet": f"summary {i}"} for i in range(5)]
        fake = {"raw": FnShelf("raw", lambda c: regular, "rawns"), "sum": FnShelf("sum", lambda c: summaries, "sumns")}
        with mock.patch.dict(A.SHELVES, fake, clear=True), mock.patch.object(A, "EXTRA_SEAT_NS", ns), \
             mock.patch.object(A, "EXTRA_SEATS", seats):
            return [w.get("snippet") for w in A.recall("question words here", n=n, shelves=("raw", "sum"))["winners"]]

    def test_off_summaries_compete_for_regular_seats(self):
        got = self._run(set(), 0)
        self.assertEqual(len(got), 3)
        self.assertTrue(any(g.startswith("summary") for g in got))

    def test_on_regular_seats_hold_only_raw_and_summaries_ride_alongside(self):
        got = self._run({"sumns"}, 2)
        self.assertEqual([g for g in got if g.startswith("raw")], ["raw 0", "raw 1", "raw 2"])
        self.assertEqual([g for g in got if g.startswith("summary")], ["summary 0", "summary 1"])
        self.assertEqual(len(got), 5)

    def test_chrono_keeps_side_seats_after_regulars(self):
        from shelves import FnShelf
        regular = [{"id": f"r{i}", "path": f"r{i}", "title": "", "snippet": f"raw {i}"} for i in range(3)]
        summaries = [{"id": "s0", "path": "s0", "title": "", "snippet": "summary 0"}]
        fake = {"raw": FnShelf("raw", lambda c: regular, "rawns"), "sum": FnShelf("sum", lambda c: summaries, "sumns")}
        def render_dated(it, r):   # summaries are OLDER than every regular winner
            return {"kind": "note", "snippet": it["snippet"], "date": "2020-01-01" if it["id"].startswith("s") else "2026-01-01"}
        with mock.patch.dict(A.SHELVES, fake, clear=True), mock.patch.object(A, "EXTRA_SEAT_NS", {"sumns"}), \
             mock.patch.object(A, "EXTRA_SEATS", 1), mock.patch.object(A, "CHRONO", True), \
             mock.patch.dict(A.KINDS, {"rawns": render_dated, "sumns": render_dated}):
            got = [w["snippet"] for w in A.recall("question words here", n=3, shelves=("raw", "sum"))["winners"]]
        self.assertEqual(got[-1], "summary 0")
