# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""A stranger's install, with only a notes folder and a Claude chat export plus an offline deterministic embedder,
end to end through the REAL recall() and the HTTP service, with no House code.
Run: python3 -m unittest test_stranger -v   (from engine/)"""
import contextlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
import urllib.request
from unittest import mock

import antechamber_recall as A
import recall_service as R
import adapters
from adapters import check as chk
from adapters import embedders

logging.getLogger("antechamber.adapters").setLevel(logging.CRITICAL)
QUESTIONS = [
    ("When does Sam plant the heirloom tomatoes?", "notes", "Tomatoes"),
    ("Which train does Rae prefer for the coast?", "chats", "8:15"),
    ("How often does Rae turn the compost?", "notes", "Compost"),
]
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "tests", "fixtures")


def stranger_cfg(tmp):
    return {"_config_dir": tmp, "index": {"dir": "index"},
            "adapters": {"notes": [{"id": "my-notes", "name": "My notes", "folder": os.path.join(FIX, "notes")}],
                         "chats": [{"id": "my-chats", "name": "Assistant chats",
                                    "path": os.path.join(FIX, "claude", "conversations.json"), "format": "claude"}]}}


def stranger_toml(tmp, folder=None):
    """The same install as stranger_cfg, written as a settings file in `tmp`. Returns its path."""
    j = json.dumps
    path = os.path.join(tmp, "config.toml")
    with open(path, "w") as f:
        f.write(f'[index]\ndir = {j("index")}\n\n'
                f'[[adapters.notes]]\nid = {j("my-notes")}\nname = {j("My notes")}\nfolder = {j(folder or os.path.join(FIX, "notes"))}\n\n'
                f'[[adapters.chats]]\nid = {j("my-chats")}\nname = {j("Assistant chats")}\n'
                f'path = {j(os.path.join(FIX, "claude", "conversations.json"))}\nformat = {j("claude")}\n')
    return path


def found(winners, ns, want):
    if ns == "notes":
        return any(w["kind"] == "item" and w.get("section") == want for w in winners)
    return any(w["kind"] == "item" and w.get("source") == "Trip to the coast"
               and any(want in (ln.get("message") or "") for ln in w.get("lines") or []) for w in winners)


def isolated():
    st = contextlib.ExitStack()
    st.enter_context(mock.patch.dict(A.SHELVES, {}, clear=True))
    st.enter_context(mock.patch.dict(A.KINDS, {}))
    st.enter_context(mock.patch.object(A, "PUBLIC_SHELVES", []))
    return st


class FailingEmbedder(embedders.HashEmbedder):
    def __init__(self, dims=64):
        super().__init__(dims)
        self.fail = False

    def embed_batch(self, texts, timeout=None):
        if self.fail:
            raise embedders.EmbedError("embedder down")
        return super().embed_batch(texts, timeout)


def clean_env(config_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTECHAMBER_")}
    env["ANTECHAMBER_CONFIG"] = config_path
    return env


def run_check(args, env):
    return subprocess.run([sys.executable, "antechamber_recall.py", "--check", *args], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=60)


class StrangerRecall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        st = isolated()
        st.__enter__()
        self.addCleanup(st.close)

    def load(self, cfg, emb=None, specs=()):
        return A.load_configured(specs=list(specs), cfg=cfg, embedder=emb or embedders.HashEmbedder(64))

    def test_memories_adapter_loads_through_recall_with_source_lines(self):
        # the memories adapter shipped in adapters/ but recall's own kind list refused it, so no install could use
        # it (found by the benchmark, 17 Oct). Recall must accept every kind adapters/check.py accepts.
        self.assertEqual(set(A.PUBLIC_KINDS), set(chk.KINDS))
        mem = os.path.join(self.tmp, "memories")
        os.makedirs(mem)
        with open(os.path.join(mem, "c-trip-0001.json"), "w") as f:
            json.dump({"conversation_id": "c-trip-0001", "session_date": "2025-04-12", "memories": [
                {"summary": "Rae likes the early coastal train because it stops at the harbour.",
                 "excerpt": "Rae prefers the 8:15 train", "topic": "Travel", "source_line_ids": ["m-trip-3"]}]}, f)
        cfg = stranger_cfg(self.tmp)
        cfg["adapters"]["memories"] = [{"id": "my-memories", "name": "Memories", "folder": mem,
                                        "export": os.path.join(FIX, "claude", "conversations.json"),
                                        "redundant_with": ["my-chats"]}]
        self.load(cfg, embedders.HashEmbedder(256))
        adapters.refresh_all(A.PUBLIC_SHELVES)
        self.assertEqual(list(A.SHELVES), ["my-notes", "my-chats", "my-memories"])
        out = A.recall("early coastal train harbour", shelves=["my-memories"])
        w = [x for x in out["winners"] if "early coastal train" in (x.get("text") or "")]   # the memory itself
        self.assertTrue(w, out["winners"])
        self.assertTrue(any("8:15" in (ln.get("message") or "") for ln in w[0].get("lines") or []))

    def test_recall_finds_the_note_and_the_chat_lines(self):
        self.load(stranger_cfg(self.tmp), embedders.HashEmbedder(256))
        adapters.refresh_all(A.PUBLIC_SHELVES)
        self.assertEqual(list(A.SHELVES), ["my-notes", "my-chats"])
        self.assertEqual([s.name for s in A.PUBLIC_SHELVES], ["my-notes", "my-chats"])
        for q, ns, want in QUESTIONS:
            out = A.recall(q)
            self.assertTrue(found(out["winners"], ns, want), q)
            self.assertTrue(all(w["kind"] == "item" for w in out["winners"]))
            self.assertEqual(out["failed"], [])
            self.assertNotIn("degraded", out)

    def test_public_adapters_install_after_configured_shelves(self):
        m = types.ModuleType("fake_stranger_mod")
        m.SHELVES = [A.FnShelf("z", lambda c: [], "tst")]
        with mock.patch.dict(sys.modules, {"fake_stranger_mod": m}):
            names = self.load(stranger_cfg(self.tmp), specs=["fake_stranger_mod:SHELVES"])
        self.assertEqual(names, ["z", "my-notes", "my-chats"])

    def test_degraded_when_the_question_cannot_be_embedded(self):
        emb = FailingEmbedder(256)
        self.load(stranger_cfg(self.tmp), emb)
        adapters.refresh_all(A.PUBLIC_SHELVES)
        emb.fail = True
        out = A.recall(QUESTIONS[0][0])
        self.assertEqual(out["degraded"], ["my-chats", "my-notes"])
        self.assertTrue(found(out["winners"], "notes", "Tomatoes"))

    def test_bad_settings_stop_startup(self):
        notes = os.path.join(FIX, "notes")
        with self.assertRaises(adapters.ConfigError):                    # (a) missing folder
            self.load({"_config_dir": self.tmp, "adapters": {"notes": [{"id": "x", "folder": os.path.join(self.tmp, "missing")}]}})
        with self.assertRaises(adapters.ConfigError):                    # (b) unknown kind
            self.load({"_config_dir": self.tmp, "adapters": {"note": [{"id": "x", "folder": FIX}]}})
        with self.assertRaises(adapters.ConfigError) as cm:              # (c) duplicate id inside a kind
            self.load({"_config_dir": self.tmp, "adapters": {"notes": [{"id": "dup", "folder": notes}, {"id": "dup", "folder": notes}]}})
        self.assertIn("used twice", str(cm.exception))
        self.assertEqual((A.SHELVES, A.PUBLIC_SHELVES), ({}, []))
        cfg = stranger_cfg(self.tmp)                                     # (d) duplicate id across kinds
        cfg["adapters"]["chats"][0]["id"] = "my-notes"
        with self.assertRaises(adapters.ConfigError) as cm:
            self.load(cfg)
        self.assertIn("used twice", str(cm.exception))
        self.assertEqual((A.SHELVES, A.PUBLIC_SHELVES), ({}, []))
        A.install([A.FnShelf("my-notes", lambda c: [], "tst")])          # (e) clash with a configured shelf
        with self.assertRaises(adapters.ConfigError):
            self.load(stranger_cfg(self.tmp))

    def test_collisions_are_refused_before_anything_is_built(self):
        A.install([A.FnShelf("my-notes", lambda c: [], "tst")])
        with mock.patch.object(adapters, "load") as ld:
            with self.assertRaises(adapters.ConfigError):
                self.load(stranger_cfg(self.tmp))
            ld.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "index")))
        with mock.patch.dict(A.KINDS, {"my-chats": lambda it, r: None}), mock.patch.object(adapters, "load") as ld:
            cfg = stranger_cfg(self.tmp)
            cfg["adapters"].pop("notes")
            with self.assertRaises(adapters.ConfigError):
                self.load(cfg)
            ld.assert_not_called()

    def test_reload_refuses_a_configured_shelf_with_a_public_name(self):
        self.load(stranger_cfg(self.tmp))
        mine = A.SHELVES["my-notes"]
        A.install([A.FnShelf("my-notes", lambda c: [], "tst")])      # a configured shelf now owns that name
        configured = A.SHELVES["my-notes"]
        with self.assertRaises(adapters.ConfigError):
            self.load(stranger_cfg(self.tmp))
        self.assertIs(A.SHELVES["my-notes"], configured)             # the configured shelf survived
        self.assertIn(mine, A.PUBLIC_SHELVES)

    def test_reload_keeps_the_renderer_of_a_configured_shelf_that_took_a_public_name(self):
        self.load(stranger_cfg(self.tmp))
        A.install([A.FnShelf("my-notes", lambda c: [], "tst")])      # a configured shelf now owns that name
        configured = A.SHELVES["my-notes"]
        mine = A.KINDS["my-notes"]
        A.register_kind(configured.key_ns, mine)
        self.load({"_config_dir": self.tmp, "adapters": {}})         # reload without that public id
        self.assertIs(A.SHELVES["my-notes"], configured)
        self.assertIn(configured.key_ns, A.KINDS)                    # its renderer was not deleted

    def test_malformed_shapes_raise_config_error(self):
        notes = os.path.join(FIX, "notes")
        bad = [{"notes": "x"},
               {"notes": ["x"]},
               {"notes": [{"folder": notes}]},
               {"notes": [{"id": "Bad Id!", "folder": notes}]},
               {"notes": [{"id": "ok", "name": 5, "folder": notes}]}]
        for table in bad:
            with self.subTest(table=table):
                with self.assertRaises(adapters.ConfigError):
                    self.load({"_config_dir": self.tmp, "adapters": table})
        with self.assertRaises(adapters.ConfigError):
            self.load({"_config_dir": self.tmp, "adapters": "x"})
        self.assertEqual((A.SHELVES, A.PUBLIC_SHELVES), ({}, []))

    def test_construction_errors_become_config_errors_naming_the_adapter(self):
        with mock.patch.object(adapters, "load", side_effect=TypeError("boom")):
            with self.assertRaises(adapters.ConfigError) as cm:
                self.load(stranger_cfg(self.tmp))
        self.assertIn("my-notes", str(cm.exception))

    def test_a_failed_reload_keeps_the_previous_public_set(self):
        self.load(stranger_cfg(self.tmp))
        before = list(A.PUBLIC_SHELVES)
        with self.assertRaises(adapters.ConfigError):
            self.load({"_config_dir": self.tmp, "adapters": {"notes": [{"id": "x", "folder": os.path.join(self.tmp, "missing")}]}})
        self.assertEqual(A.PUBLIC_SHELVES, before)
        self.assertEqual(list(A.SHELVES), ["my-notes", "my-chats"])

    def test_reload_replaces_the_public_set_and_leaves_configured_shelves(self):
        m = types.ModuleType("fake_stranger_mod")
        m.SHELVES = [A.FnShelf("z", lambda c: [], "tst")]
        with mock.patch.dict(sys.modules, {"fake_stranger_mod": m}):
            self.load(stranger_cfg(self.tmp), specs=["fake_stranger_mod:SHELVES"])
        self.assertIn("my-notes", A.KINDS)
        self.load({"_config_dir": self.tmp, "adapters": {}})
        self.assertEqual(list(A.SHELVES), ["z"])
        self.assertEqual(A.PUBLIC_SHELVES, [])
        self.assertNotIn("my-notes", A.KINDS)
        self.assertNotIn("my-chats", A.KINDS)

    def test_no_adapters_table_installs_nothing(self):
        self.assertEqual(A.load_configured(specs=[], cfg={"_config_dir": self.tmp}), [])
        self.assertEqual(A.PUBLIC_SHELVES, [])


class StrangerProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_fresh_process_recalls_with_no_house_code(self):
        path = stranger_toml(self.tmp)
        code = ("import sys, json\n"
                "import antechamber_recall as A, adapters\n"
                "from adapters import embedders\n"
                "A.load_configured(embedder=embedders.HashEmbedder(256))\n"
                "adapters.refresh_all(A.PUBLIC_SHELVES)\n"
                "QS = json.loads(sys.argv[1])\n"
                "out = {q: A.recall(q)[\"winners\"] for q in QS}\n"
                "print(json.dumps({\"shelves\": list(A.SHELVES), \"out\": out, \"modules\": sorted(sys.modules)}))\n")
        r = subprocess.run([sys.executable, "-c", code, json.dumps([q for q, _, _ in QUESTIONS])], cwd=HERE,
                           env=clean_env(path), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(d["shelves"], ["my-notes", "my-chats"])
        for q, ns, want in QUESTIONS:
            self.assertTrue(found(d["out"][q], ns, want), q)
        self.assertFalse([m for m in d["modules"] if m == "house" or m.startswith("house.")])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "index")))   # the import-time load used _config_dir

    def test_check_ok_and_problems(self):
        none = clean_env(os.path.join(self.tmp, "none.toml"))
        good = stranger_toml(self.tmp)
        r = run_check([good], none)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "ok")
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        bad = stranger_toml(other, folder=os.path.join(self.tmp, "missing"))
        r = run_check([bad], none)
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not exist", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(run_check([], clean_env(bad)).returncode, 1)
        r = run_check([os.path.join(self.tmp, "nope.toml")], none)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot read", r.stdout)

    def test_check_reports_a_malformed_active_config(self):
        broken = os.path.join(self.tmp, "broken.toml")
        with open(broken, "w") as f:
            f.write("[adapters\nid = = 1\n")
        r = run_check([], clean_env(broken))
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot read", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        r = run_check([broken], clean_env(os.path.join(self.tmp, "none.toml")))
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot read", r.stdout)
        # control: the runtime still fails loudly on the same file
        r = subprocess.run([sys.executable, "-c", "import antechamber_recall"], cwd=HERE, env=clean_env(broken),
                           capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)


class StrangerService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        st = contextlib.ExitStack()
        self.addCleanup(st.close)
        st.enter_context(isolated())
        st.enter_context(mock.patch.multiple(
            R, GET_ROUTES={"/antechamber/recall": R.route_recall, "/health": R.route_health}, POST_ROUTES={},
            HOOKS={"before_recall": [], "after_recall": [], "on_error": [], "health_extra": []},
            LIVE_CHRONO=False, EXTENSIONS_LOADED=[]))
        st.enter_context(mock.patch.multiple(R.C, LOADED=True, SERVICE_EXTENSIONS=[]))
        st.enter_context(mock.patch.object(R, "REFRESHED", set()))
        A.load_configured(specs=[], cfg=stranger_cfg(self.tmp), embedder=embedders.HashEmbedder(256))
        adapters.refresh_all(A.PUBLIC_SHELVES)
        stop = R.start_refresher(60)
        self.assertTrue(callable(stop))
        self.addCleanup(stop)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        srv = R.make_server(self.port)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path):
        return json.loads(self.opener.open(f"http://127.0.0.1:{self.port}{path}", timeout=30).read())

    def test_http_recall_and_health(self):
        for q, ns, want in QUESTIONS:
            obj = self.get("/antechamber/recall?" + urllib.parse.urlencode({"q": q, "limit": 8}))
            self.assertTrue(found(obj["winners"], ns, want), q)
            self.assertTrue(all(w["kind"] == "item" for w in obj["winners"]))
        for _ in range(300):                      # ok only once the background refresher's first round is done
            if self.get("/health")["ok"]:
                break
            time.sleep(0.1)
        self.assertIs(self.get("/health")["ok"], True)
        with mock.patch.dict(A.SHELVES, {}, clear=True):
            self.assertIs(self.get("/health")["ok"], False)


if __name__ == "__main__":
    unittest.main()
