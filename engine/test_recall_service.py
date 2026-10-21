# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The generic recall service: route registry, hooks, extensions, /health and 404 — no House routes here.
Run: python3 -m unittest test_recall_service -v   (from engine/)"""
import types, unittest
from unittest import mock
import recall_service as R


def call(method, path, routes=None):
    import io
    h = R.H.__new__(R.H)
    h.path = path
    h.headers, h.rfile = {"Content-Length": "0"}, io.BytesIO(b"")
    out = {}
    h._send = lambda code, obj: out.update(code=code, obj=obj)
    getattr(h, "do_" + method)()
    return out


def clean():
    """An engine service with only its own two routes and no hooks (whatever this machine's settings loaded)."""
    return mock.patch.multiple(R, GET_ROUTES={"/antechamber/recall": R.route_recall, "/health": R.route_health},
                               POST_ROUTES={}, HOOKS={"before_recall": [], "after_recall": [], "on_error": [], "health_extra": []},
                               LIVE_CHRONO=False)


class Routes(unittest.TestCase):
    def test_unknown_paths_are_404_for_get_and_post(self):
        with clean():
            self.assertEqual(call("GET", "/nowhere")["code"], 404)
            self.assertEqual(call("POST", "/antechamber/recall-seen")["code"], 404)

    def test_an_empty_question_is_400(self):
        with clean():
            self.assertEqual(call("GET", "/antechamber/recall?q=")["code"], 400)

    def test_an_added_route_answers(self):
        with clean():
            R.add_get("/x", lambda h, qs: h._send(200, {"x": qs.get("a")}))
            R.add_post("/y", lambda h: h._send(201, {}))
            self.assertEqual(call("GET", "/x?a=1"), {"code": 200, "obj": {"x": ["1"]}})
            self.assertEqual(call("POST", "/y")["code"], 201)


class Recall(unittest.TestCase):
    OUT = {"query": "q", "searched": "q", "winners": [{"kind": "message", "date": "2023-05-08",
           "lines": [{"message": "we went yesterday", "dates": ["yesterday = Sunday 7 May 2023"]}]}], "shelves": {}, "failed": []}

    def _recall(self, **kw):
        import copy
        return mock.patch.object(R.antechamber_recall, "recall", lambda q, n, windows: copy.deepcopy(self.OUT))

    def test_dates_fold_into_the_line_text_and_hooks_run_in_order(self):
        seen = []
        with clean(), self._recall():
            R.add_hook("before_recall", lambda q: (None, {"extra": 1}))
            R.add_hook("after_recall", lambda q, out: seen.append(list(out)) or out.update(after=True))
            out = call("GET", "/antechamber/recall?q=q")["obj"]
        self.assertEqual(out["winners"][0]["lines"][0], {"message": "we went yesterday  (yesterday = Sunday 7 May 2023)"})
        self.assertEqual(list(out), ["query", "searched", "winners", "shelves", "failed", "after", "extra"])
        self.assertEqual(seen, [["query", "searched", "winners", "shelves", "failed"]])

    def test_a_before_hook_can_answer_early(self):
        with clean(), self._recall():
            R.add_hook("before_recall", lambda q: ({"early": q}, {}))
            self.assertEqual(call("GET", "/antechamber/recall?q=hi")["obj"], {"early": "hi"})

    def test_errors_are_500_unless_a_hook_shapes_them(self):
        def boom(*a, **k): raise RuntimeError("down")
        with clean(), mock.patch.object(R.antechamber_recall, "recall", boom):
            self.assertEqual(call("GET", "/antechamber/recall?q=q"), {"code": 500, "obj": {"error": "down"}})
            R.add_hook("on_error", lambda q, e: (200, {"fallback": str(e)}))
            self.assertEqual(call("GET", "/antechamber/recall?q=q"), {"code": 200, "obj": {"fallback": "down"}})

    def test_wide_and_caller_chrono_flags(self):
        with clean(), self._recall(), mock.patch.object(R.antechamber_recall, "WIDE", True):
            R.LIVE_CHRONO = True
            out = call("GET", "/antechamber/recall?q=how many times")["obj"]
        self.assertEqual((out["wide"], out["order"]), (True, "chrono"))


class Health(unittest.TestCase):
    def test_ok_needs_settings_adapters_and_every_extension(self):
        with clean(), mock.patch.object(R.C, "LOADED", True), mock.patch.dict(R.antechamber_recall.SHELVES, {"a": object()}, clear=True), \
             mock.patch.object(R.C, "SERVICE_EXTENSIONS", []), mock.patch.object(R, "EXTENSIONS_LOADED", []):
            self.assertIs(call("GET", "/health")["obj"]["ok"], True)
            with mock.patch.object(R.C, "SERVICE_EXTENSIONS", ["missing:register"]):
                self.assertIs(call("GET", "/health")["obj"]["ok"], False)
            with mock.patch.dict(R.antechamber_recall.SHELVES, {}, clear=True):
                self.assertIs(call("GET", "/health")["obj"]["ok"], False)
            R.add_hook("health_extra", lambda: {"extra": 1})
            self.assertEqual(list(call("GET", "/health")["obj"]), ["ok", "config", "extra"])

    def test_a_missing_extension_fails_loudly(self):
        with mock.patch.object(R, "EXTENSIONS_LOADED", []):
            with self.assertRaises(ImportError):
                R.load_extensions(["no_such_extension_module:register"])


class Refresher(unittest.TestCase):
    def test_none_without_public_adapters(self):
        with mock.patch.object(R.antechamber_recall, "PUBLIC_SHELVES", []):
            self.assertIsNone(R.start_refresher())

    def test_starts_the_adapters_refresher_with_the_configured_minutes(self):
        fake = [object()]
        with mock.patch.object(R.antechamber_recall, "PUBLIC_SHELVES", fake), mock.patch.object(R.C, "INDEX_REFRESH_MINUTES", 15.0), \
             mock.patch("adapters.start_refresher", return_value="stop") as sr:
            self.assertEqual(R.start_refresher(), "stop")
            sr.assert_called_once_with(fake, 15.0, on_round=R._note_round)
            R.start_refresher(2)
            self.assertEqual(sr.call_args, mock.call(fake, 2, on_round=R._note_round))


    def test_health_waits_for_the_first_good_refresh(self):
        shelf = types.SimpleNamespace(name="p", index=object())
        with mock.patch.object(R.antechamber_recall, "PUBLIC_SHELVES", [shelf]), mock.patch.object(R, "REFRESHED", set()):
            self.assertFalse(R.public_ready())
            R._note_round({"p": {"error": "boom"}})
            self.assertFalse(R.public_ready())            # a failed first refresh never reads as ready
            R._note_round({"p": {"added": 1}})
            self.assertTrue(R.public_ready())
            R._note_round({"p": {"error": "boom"}})
            self.assertFalse(R.public_ready())
        with mock.patch.object(R.antechamber_recall, "PUBLIC_SHELVES", []):
            self.assertTrue(R.public_ready())

    def test_health_ignores_shelves_without_an_index(self):
        svc = types.SimpleNamespace(name="svc")                       # service / pgvector shelves have no index
        idx = types.SimpleNamespace(name="n", index=object())
        with mock.patch.object(R.antechamber_recall, "PUBLIC_SHELVES", [svc]), mock.patch.object(R, "REFRESHED", set()):
            self.assertTrue(R.public_ready())
        with mock.patch.object(R.antechamber_recall, "PUBLIC_SHELVES", [svc, idx]), mock.patch.object(R, "REFRESHED", set()):
            self.assertFalse(R.public_ready())                        # an indexed shelf still gates it
            R._note_round({"n": {"added": 1}})
            self.assertTrue(R.public_ready())

if __name__ == "__main__":
    unittest.main()



class RefreshReadiness(unittest.TestCase):
    """A refresh skipped because another process held the index lock is NOT a refresh (Codex review, 14 Oct)."""
    def setUp(self):
        self.saved = set(R.REFRESHED); R.REFRESHED.clear()

    def tearDown(self):
        R.REFRESHED.clear(); R.REFRESHED.update(self.saved)

    def test_locked_skip_never_marks_ready(self):
        R._note_round({"notes-a": {"skipped": "locked"}})
        self.assertNotIn("notes-a", R.REFRESHED)

    def test_locked_skip_keeps_earlier_readiness(self):
        R._note_round({"notes-a": {"added": 3}})
        R._note_round({"notes-a": {"skipped": "locked"}})
        self.assertIn("notes-a", R.REFRESHED)

    def test_an_error_clears_readiness(self):
        R._note_round({"notes-a": {"added": 3}})
        R._note_round({"notes-a": {"error": "boom"}})
        self.assertNotIn("notes-a", R.REFRESHED)
