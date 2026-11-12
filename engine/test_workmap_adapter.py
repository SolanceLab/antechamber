# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The workmap adapter: config, sha pin, items from synthetic records, search, reload on change, a missing config.
Uses a temporary copy of the work-map tool (WORK_MAP_SRC) and records written into a temp dir."""
import hashlib
import json
import os
import shutil
import tempfile
import time
import types
import unittest

from adapters import workmap, check

def _find_tool():
    """WORK_MAP_SRC, else work-map's plugin/scripts/work_map.py in a checkout beside this repo (found by walking up)."""
    if os.environ.get("WORK_MAP_SRC"):
        return os.environ["WORK_MAP_SRC"]
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        cand = os.path.join(here, "projects", "tools", "work-map", "plugin", "scripts", "work_map.py")
        if os.path.isfile(cand) or os.path.dirname(here) == here:
            return cand
        here = os.path.dirname(here)


SRC = _find_tool()

DECISIONS = """- 2026-11-01 · vault-split · A → keep the vault local · why: privacy · session: s1 · applies: house
- 2026-11-02 · ship-gate · B → publish only on green · why: safety · session: s2 · from: solance-house
"""
PIPELINE = "- **Build the porch** · project: hos-app · status: planned · session: s3 · a door into the house\n"


@unittest.skipUnless(os.path.isfile(SRC), "work-map tool not found (WORK_MAP_SRC)")
class WorkMapAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.tool = os.path.join(self.d, "work_map.py")
        shutil.copy(SRC, self.tool)
        self.dec = os.path.join(self.d, "DECISIONS.md")
        self.write(self.dec, DECISIONS)
        self.write(os.path.join(self.d, "PIPELINE.md"), PIPELINE)
        self.cfg = os.path.join(self.d, "wm.json")
        self.write(self.cfg, json.dumps({"decisions": ["DECISIONS.md"], "pipeline": ["PIPELINE.md"]}))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        with open(path, "w") as fh:
            fh.write(text)

    def shelf(self, sha="", **extra):
        inst = {"id": "work", "name": "Work", "work_map": self.tool, "config": self.cfg, "work_map_sha256": sha}
        inst.update(extra)
        return workmap.from_config({"adapters": {"workmap": [inst]}})[0]

    def ids(self, shelf, q):
        return [i["id"] for i in shelf.search(types.SimpleNamespace(q=q))]

    def test_items_have_ids_and_one_line_text(self):
        items = {i["id"]: i for i in self.shelf().search(types.SimpleNamespace(q="vault ship porch"))}
        self.assertEqual(set(items), {"decision:vault-split", "decision:ship-gate", "pipeline:hos-app/build-the-porch"})
        top = items["decision:vault-split"]
        self.assertTrue(top["text"].startswith("decision decision:vault-split · "))
        self.assertIn("source: DECISIONS.md:L1", top["text"])
        self.assertEqual((top["date"], top["source"], top["_ns"]), ("2026-11-01", "Work", "work"))

    def test_search_by_id_and_by_project(self):
        s = self.shelf()
        self.assertEqual(self.ids(s, "decision:ship-gate")[:1], ["decision:ship-gate"])
        self.assertEqual(self.ids(s, "ship gate")[0], "decision:ship-gate")
        self.assertEqual(self.ids(s, "hos-app"), ["pipeline:hos-app/build-the-porch"])
        self.assertEqual(self.ids(s, "zzzunknown"), [])

    def test_open_by_id(self):
        s = self.shelf()
        self.assertIn("publish only on green", s.open("decision:ship-gate"))
        with self.assertRaises(ValueError):
            s.open("decision:nope")

    def test_sha_pin_refuses_other_bytes(self):
        with open(self.tool, "rb") as fh:
            good = hashlib.sha256(fh.read()).hexdigest()
        self.shelf(good)
        with self.assertRaises(workmap.WorkMapError) as cm:
            self.shelf("0" * 64)
        self.assertIn("work", str(cm.exception))

    def test_config_must_name_both_files(self):
        with self.assertRaises(workmap.WorkMapError):
            workmap.from_config({"adapters": {"workmap": [{"id": "work", "work_map": self.tool}]}})
        self.assertTrue(check.check({"adapters": {"workmap": [{"id": "work", "work_map": self.tool}]}}))

    def test_duplicate_ids_ranked_first_do_not_starve_the_limit(self):
        dups = "".join(f"- 2026-10-0{n} · dup · A → zebra rule {n} · why: t · session: s{n} · from: x\n" for n in range(1, 5))
        other = "- 2026-10-09 · other · B → zebra other · why: t · session: s9 · from: x\n"
        self.write(self.dec, dups + other)
        got = [i["id"] for i in self.shelf().search(types.SimpleNamespace(q="zebra", pool=2))]
        self.assertEqual(sorted(got), ["decision:dup", "decision:other"])

    def test_reload_after_a_source_file_changes(self):
        s = self.shelf()
        s.index.min_interval = 0
        self.assertEqual(self.ids(s, "newcall"), [])
        time.sleep(0.01)
        self.write(self.dec, DECISIONS + "- 2026-11-03 · fresh · A → newcall adopted · why: test · session: s4 · from: solance-house\n")
        self.assertEqual(self.ids(s, "newcall"), ["decision:fresh"])
        self.assertFalse(s.index.refresh()["reloaded"])

    def test_not_rechecked_inside_a_minute(self):
        s = self.shelf()
        self.assertEqual(self.ids(s, "newcall"), [])
        self.write(self.dec, DECISIONS + "- 2026-11-03 · fresh · A → newcall adopted · why: t · session: s4 · from: x\n")
        self.assertEqual(self.ids(s, "newcall"), [])          # within the minute: the old records
        self.assertTrue(s.index.refresh()["reloaded"])
        self.assertEqual(self.ids(s, "newcall"), ["decision:fresh"])

    def test_missing_config_is_a_clear_error_and_later_loss_keeps_the_shelf(self):
        gone = os.path.join(self.d, "none.json")
        with self.assertRaises(workmap.WorkMapError) as cm:
            workmap.from_config({"adapters": {"workmap": [{"id": "work", "work_map": self.tool, "config": gone}]}})
        self.assertIn("[[adapters.workmap]] work", str(cm.exception))
        s = self.shelf()
        s.index.min_interval = 0
        os.remove(self.cfg)
        self.assertTrue(self.ids(s, "vault"))                 # search keeps the last good records
        with self.assertRaises(workmap.WorkMapError):
            s.index.refresh()                                 # the refresher reports it (refresh_all catches it)

    def test_load_configured_accepts_workmap_and_concurrent_shelves_do_not_mix_output(self):
        import threading
        import antechamber_recall as A
        cfg = {"adapters": {"workmap": [
            {"id": "work", "work_map": self.tool, "config": self.cfg},
            {"id": "work2", "work_map": self.tool, "config": self.cfg}]}}
        saved = (dict(A.SHELVES), list(A.PUBLIC_SHELVES))
        try:
            A.load_configured(specs=[], cfg=cfg)
            shelves = [A.SHELVES["work"], A.SHELVES["work2"]]
            errs = []

            def run(sh):
                try:
                    for _ in range(30):
                        if not self.ids(sh, "vault ship"):
                            errs.append("empty")
                except Exception as e:
                    errs.append(repr(e))
            ts = [threading.Thread(target=run, args=(sh,)) for sh in shelves * 2]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(errs, [])
        finally:
            A.SHELVES.clear(); A.SHELVES.update(saved[0]); A.PUBLIC_SHELVES[:] = saved[1]


if __name__ == "__main__":
    unittest.main()
