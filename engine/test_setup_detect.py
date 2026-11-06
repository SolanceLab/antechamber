# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""look_for_stores and setup_stores kind vault: detection is read-only, the vault-map fetch is checked."""
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import config as C
import setup_stores as S


def mk(root, rel, text="x"):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    return p


class Look(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.env = mock.patch.dict(os.environ, {"ANTECHAMBER_HOME_ROOT": self.t})
        self.env.start()
        self.unconfigured = mock.patch.object(S, "configured", return_value=False)    # other tests may leave stores loaded
        self.unconfigured.start()

    def tearDown(self):
        self.unconfigured.stop()
        self.env.stop()
        self.tmp.cleanup()

    def look(self, rel):
        return S._look(os.path.join(self.t, rel))

    def test_classifies(self):
        mk(self.t, "notes/a.md"); mk(self.t, "notes/b.txt")
        mk(self.t, "vault/.obsidian/app.json", "{}"); mk(self.t, "vault/n.md")
        mk(self.t, "mem/one.json", json.dumps({"conversation_id": "c", "memories": [{"summary": "s"}]}))
        mk(self.t, "claude/conversations.json", '[{"uuid": "1", "chat_messages": []}]')
        mk(self.t, "gpt/conversations-000.json", '[{"mapping": {}}]')
        mk(self.t, "empty/x.bin")
        self.assertEqual((self.look("notes")["looks_like"], self.look("notes")["notes"]), ("notes", 2))
        self.assertEqual(self.look("vault")["looks_like"], "obsidian vault")
        self.assertEqual(self.look("mem")["looks_like"], "scriptorium memories")
        self.assertEqual(self.look("claude")["looks_like"], "claude export")
        self.assertEqual(self.look("gpt")["looks_like"], "chatgpt export")
        self.assertEqual(self.look("claude/conversations.json")["looks_like"], "claude export")
        self.assertEqual(self.look("empty")["looks_like"], "unknown")
        self.assertEqual(self.look("nope")["looks_like"], "missing")

    def test_caps_and_never_follows_links_and_writes_nothing(self):
        for i in range(30):
            mk(self.t, f"big/{i}.md")
        mk(self.t, "outside/secret.md")
        os.symlink(os.path.join(self.t, "outside"), os.path.join(self.t, "big", "link"))
        with mock.patch.object(S, "SCAN_MAX_ENTRIES", 10):
            r = self.look("big")
        self.assertTrue(r["scan_capped"])
        self.assertLessEqual(r["notes"], 10)
        mk(self.t, "deep/a/b/c/d/deep.md")
        self.assertEqual(self.look("deep")["looks_like"], "unknown")        # depth 3 stops before d/
        before = sorted(os.listdir(self.t))
        res = S.look_for_stores({"paths": [self.t, "~/nope-xyz"]})
        self.assertFalse(res.get("isError"))
        self.assertEqual(before, sorted(os.listdir(self.t)))
        self.assertTrue(S.look_for_stores({"paths": []})["isError"])
        self.assertTrue(S.look_for_stores({"paths": ["/x"] * (S.MAX_PATHS + 1)})["isError"])   # no silent drop
        self.assertEqual(S.LOOK_TOOL["inputSchema"]["properties"]["paths"]["maxItems"], S.MAX_PATHS)


class Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.old = C.PATH
        C.PATH = os.path.join(self.t, "cfg", "c.toml")
        self.env = mock.patch.dict(os.environ, {"ANTECHAMBER_HOME_ROOT": self.t})
        self.env.start()
        mk(self.t, "vault/.obsidian/app.json", "{}")
        mk(self.t, "vault/n.md", "# Zebra\n\nzebra backups\n")
        self.body = b"# fake vault-map\n"
        self.sha = hashlib.sha256(self.body).hexdigest()

    def tearDown(self):
        self.env.stop()
        C.PATH = self.old
        self.tmp.cleanup()

    def stores(self):
        return [{"kind": "vault", "path": os.path.join(self.t, "vault")}]

    def test_vault_block_written_with_sha(self):
        with mock.patch.object(S, "VAULT_MAP_SHA256", self.sha), mock.patch.object(S, "_download", return_value=self.body):
            clean, err = S._check(self.stores())
            self.assertIsNone(err)
            clean, note = S._vaults_or_notes(clean)
        text = S.render(clean, "none")
        self.assertIn("[[adapters.vaultmap]]", text)
        self.assertIn(f'vault_map_sha256 = "{self.sha}"', text)
        self.assertEqual(note, "")
        path = clean[0]["vault_map"]
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertEqual(os.path.dirname(path), os.path.dirname(C.PATH))

    def test_sha_mismatch_falls_back_to_notes(self):
        with mock.patch.object(S, "_download", return_value=b"not the pinned bytes"):
            clean, _ = S._check(self.stores())
            clean, note = S._vaults_or_notes(clean)
        self.assertEqual(clean[0]["kind"], "notes")
        self.assertIn("plain notes", note)
        self.assertIn("[[adapters.notes]]", S.render(clean, "none"))
        self.assertEqual([f for f in os.listdir(os.path.dirname(C.PATH))] if os.path.isdir(os.path.dirname(C.PATH)) else [], [])

    def test_download_error_falls_back(self):
        with mock.patch.object(S, "_download", side_effect=OSError("offline")):
            clean, note = S._vaults_or_notes(S._check(self.stores())[0])
        self.assertEqual(clean[0]["kind"], "notes")
        self.assertIn("plain notes", note)

    def test_vault_without_obsidian_is_refused(self):
        mk(self.t, "plain/a.md")
        _, err = S._check([{"kind": "vault", "path": os.path.join(self.t, "plain")}])
        self.assertIn(".obsidian", err)


class Locked(unittest.TestCase):
    def test_configured_refuses_both_tools_in_process(self):
        with mock.patch.object(S, "configured", return_value=True):
            for r in (S.setup_stores({"stores": [{"kind": "notes", "path": "/x"}]}), S.look_for_stores({"paths": ["/x"]})):
                self.assertTrue(r["isError"])
                self.assertIn("editing the settings file", r["content"][0]["text"])


class Policy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = os.path.realpath(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"ANTECHAMBER_HOME_ROOT": self.t})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def refused(self, rel):
        p = os.path.join(self.t, rel)
        os.makedirs(p, exist_ok=True)
        _, err = S._check([{"kind": "notes", "path": p}])
        look = S._look(p)
        self.assertIsNotNone(err, rel)
        self.assertEqual(look["looks_like"], "not allowed", rel)
        return err

    def test_root_and_outside_refused(self):
        _, err = S._check([{"kind": "notes", "path": self.t}])
        self.assertIn("home folder itself", err)
        self.assertEqual(S._look(self.t)["looks_like"], "not allowed")
        with tempfile.TemporaryDirectory() as other:
            _, err = S._check([{"kind": "notes", "path": other}])
            self.assertIn("outside", err)
            self.assertEqual(S._look(other)["looks_like"], "not allowed")
        self.assertIn("outside", S.path_refusal("/"))

    def test_each_sensitive_folder_and_its_insides_refused(self):
        for d in S.SENSITIVE_DIRS:
            self.refused(d)
            self.refused(os.path.join(d, "sub"))
        for d in S.SENSITIVE_LIBRARY:
            self.refused(os.path.join("Library", d))
            self.refused(os.path.join("Library", d, "x"))

    def test_library_itself_and_other_letter_case_refused(self):
        self.refused("Library")
        self.refused(os.path.join("LIBRARY", "mail"))
        self.refused(".SSH")

    def test_sensitive_names_refused_at_any_depth(self):
        self.refused(os.path.join("Documents", ".ssh"))
        self.refused(os.path.join("work", ".aws", "x"))
        self.refused(os.path.join("work", ".netrc"))

    def test_symlink_to_a_sensitive_folder_refused(self):
        os.makedirs(os.path.join(self.t, ".ssh"))
        os.symlink(os.path.join(self.t, ".ssh"), os.path.join(self.t, "innocent"))
        _, err = S._check([{"kind": "notes", "path": os.path.join(self.t, "innocent")}])
        self.assertIn(".ssh", err)
        self.assertEqual(S._look(os.path.join(self.t, "innocent"))["looks_like"], "not allowed")

    def test_icloud_and_ordinary_folders_allowed(self):
        for rel in ("Library/Mobile Documents/iCloud~md~obsidian/Documents/Vault", "Documents/notes", "Library/Other"):
            p = os.path.join(self.t, rel)
            os.makedirs(p)
            self.assertIsNone(S.path_refusal(p), rel)
            clean, err = S._check([{"kind": "notes", "path": p}])
            self.assertIsNone(err)
            self.assertNotEqual(S._look(p)["looks_like"], "not allowed")

    def test_home_root_env_must_be_absolute(self):
        with mock.patch.dict(os.environ, {"ANTECHAMBER_HOME_ROOT": "relative"}):
            self.assertEqual(S.home_root(), os.path.realpath(os.path.expanduser("~")))


if __name__ == "__main__":
    unittest.main()
