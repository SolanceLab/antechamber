# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The vaultmap adapter: section search when vault-map has it, the old note search when not, a sha pin, and
no results before the first background refresh. Uses the vault-map files named in VAULT_MAP_NEW / VAULT_MAP_OLD."""
import hashlib
import os
import tempfile
import types
import unittest

from adapters import vaultmap

NEW = os.environ.get("VAULT_MAP_NEW", os.path.join(os.path.dirname(__file__), "tests", "fixtures", "vault-map.py"))
OLD = os.environ.get("VAULT_MAP_OLD", "")

NOTE = """# 2026-07-14 Backup rotation decision

## Context
We kept losing old snapshots on the NAS.

## The decision
We rotate backups weekly and keep eight copies, because the NAS has room for eight.

## Follow-ups
Check the rotation in a month.
"""


class VaultMapAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name          # vault-map's section cache goes under this HOME
        self.vault = os.path.join(self.tmp.name, "vault")
        os.makedirs(self.vault)
        with open(os.path.join(self.vault, "backups.md"), "w") as fh:
            fh.write(NOTE)

    def tearDown(self):
        os.environ["HOME"] = self.home
        self.tmp.cleanup()

    def shelf(self, vm_path, sha=""):
        cfg = {"adapters": {"vaultmap": [{"id": "vault", "name": "My vault", "folder": self.vault,
                                          "vault_map": vm_path, "vault_map_sha256": sha}]}}
        return vaultmap.from_config(cfg)[0]

    @unittest.skipUnless(os.path.isfile(NEW), "no section-search vault-map available")
    def test_sections_answer_with_the_right_section(self):
        s = self.shelf(NEW)
        ctx = types.SimpleNamespace(q="how many backup copies do we keep in the rotation?")
        self.assertEqual(s.search(ctx), [], "nothing before the first refresh")
        s.index.refresh()
        items = s.search(ctx)
        self.assertTrue(items)
        top = items[0]
        self.assertEqual(top["section"], "The decision")
        self.assertIn("eight copies", top["text"])
        self.assertLessEqual(len(top["text"]), 160)
        self.assertEqual((top["source"], top["_ns"]), ("My vault", "vault"))

    @unittest.skipUnless(os.path.isfile(NEW), "no section-search vault-map available")
    def test_sha_pin(self):
        good = hashlib.sha256(open(NEW, "rb").read()).hexdigest()
        self.shelf(NEW, good)
        with self.assertRaises(vaultmap.VaultMapError):
            self.shelf(NEW, "0" * 64)

    @unittest.skipUnless(OLD and os.path.isfile(OLD), "no v1.2.0 vault-map given (VAULT_MAP_OLD)")
    def test_old_vault_map_falls_back_to_note_search(self):
        import subprocess, sys
        subprocess.run([sys.executable, OLD, "build", self.vault], check=True, capture_output=True)
        s = self.shelf(OLD)
        self.assertFalse(hasattr(s, "index"))
        items = s.search(types.SimpleNamespace(q="backup rotation"))
        self.assertTrue(items and items[0]["metadata"]["path"] == "backups.md")

    def test_shelf_carries_folder_and_engine_for_add_ons(self):
        vm = os.path.join(self.tmp.name, "vm.py")
        with open(vm, "w") as fh:
            fh.write("def search(terms, folder, n):\n    return None\n")
        with open(vm, "rb") as fh:
            pin = hashlib.sha256(fh.read()).hexdigest()
        def shelf(p):
            return vaultmap.from_config({"adapters": {"vaultmap": [{"id": "vault", "folder": self.vault, "vault_map": vm,
                                                                    "vault_map_sha256": p}]}})[0]
        s = shelf(pin)
        self.assertEqual((s.vault_folder, s.vault_map_sha256), (self.vault, pin))
        self.assertTrue(callable(s.vault_map.search))
        self.assertEqual(shelf("").vault_map_sha256, "")

    def test_missing_settings(self):
        with self.assertRaises(vaultmap.VaultMapError):
            vaultmap.from_config({"adapters": {"vaultmap": [{"id": "vault", "folder": self.vault}]}})



if __name__ == "__main__":
    unittest.main()
