# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The stdio MCP server end to end in a subprocess: first run, setup_stores, recall, and the launcher."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)


def rpc(i, method, **params):
    return {"jsonrpc": "2.0", "id": i, "method": method, "params": params}


def call(i, name, **args):
    return rpc(i, "tools/call", name=name, arguments=args)


class Stdio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.cfg = os.path.join(self.t, "cfg", "c.toml")
        self.notes = os.path.join(self.t, "notes")
        os.makedirs(self.notes)
        with open(os.path.join(self.notes, "a.md"), "w") as f:
            f.write("# Backups\n\nWe rotate the zebra backups every Thursday evening without fail.\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_server(self, msgs, module=("mcp_stdio.py",)):
        env = dict(os.environ, ANTECHAMBER_CONFIG=self.cfg, HOME=self.t, ANTECHAMBER_HOME_ROOT=self.t)
        p = subprocess.run([sys.executable, os.path.join(ENGINE, *module)], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, env=env, timeout=60)
        lines = p.stdout.splitlines()
        out = {}
        for ln in lines:
            a = json.loads(ln)                      # stdout carries JSON-RPC lines only
            self.assertEqual(a["jsonrpc"], "2.0")
            out[a["id"]] = a
        return out

    @staticmethod
    def text(a):
        return a["result"]["content"][0]["text"]

    def test_first_run_then_setup_then_recall(self):
        a = self.run_server([rpc(1, "initialize", protocolVersion="2025-06-18"), rpc(2, "tools/list"),
                             call(3, "recall", query="zebra"),
                             call(4, "setup_stores", stores=[{"kind": "notes", "path": self.notes}]),
                             call(5, "recall", query="zebra backups"),
                             call(6, "setup_stores", stores=[{"kind": "notes", "path": self.notes}])])
        self.assertIn("setup_stores", [t["name"] for t in a[2]["result"]["tools"]])
        self.assertIn("no stores yet", self.text(a[3]))
        self.assertIn("Antechamber is set up", self.text(a[4]))
        self.assertIn("zebra backups", self.text(a[5]))
        self.assertTrue(a[6]["result"].get("isError"))                     # configured: the assistant cannot change stores
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg).st_mode), 0o600)

    def test_refuses_bad_paths_and_writes_nothing(self):
        a = self.run_server([call(1, "setup_stores", stores=[{"kind": "notes", "path": "relative/notes"}]),
                             call(2, "setup_stores", stores=[{"kind": "notes", "path": os.path.join(self.t, "missing")}]),
                             call(3, "setup_stores", stores=[{"kind": "bogus", "path": self.notes}])])
        for i in (1, 2, 3):
            self.assertTrue(a[i]["result"].get("isError"))
        self.assertFalse(os.path.exists(self.cfg))

    def test_configured_hides_setup_tools_and_refuses_calls(self):
        self.run_server([call(1, "setup_stores", stores=[{"kind": "notes", "path": self.notes}])])
        other = os.path.join(self.t, "more")
        os.makedirs(other)
        a = self.run_server([rpc(1, "tools/list"),
                             call(2, "setup_stores", stores=[{"kind": "notes", "path": other}]),
                             call(3, "look_for_stores", paths=[other]),
                             call(4, "recall", query="zebra")])
        names = [t["name"] for t in a[1]["result"]["tools"]]
        self.assertNotIn("setup_stores", names)
        self.assertNotIn("look_for_stores", names)
        for i in (2, 3):
            self.assertTrue(a[i]["result"].get("isError"))
            self.assertIn("editing the settings file", self.text(a[i]))
            self.assertIn(self.cfg, self.text(a[i]))
        self.assertNotIn("overwrite", self.text(a[2]))
        self.assertNotIn("no stores yet", self.text(a[4]))
        with open(self.cfg) as f:
            self.assertNotIn("more", f.read())                             # the refused call changed nothing

    def test_unconfigured_existing_file_is_replaced_with_a_backup(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, "w") as f:
            f.write('[embedder]\nbackend = "none"\n')
        a = self.run_server([call(1, "setup_stores", stores=[{"kind": "notes", "path": self.notes}]),
                             call(2, "recall", query="zebra")])
        self.assertIn("Antechamber is set up", self.text(a[1]))
        with open(self.cfg + ".bak") as f:
            self.assertIn("[embedder]", f.read())
        b = self.run_server([call(1, "recall", query="zebra")])             # a fresh start reads the file
        self.assertNotIn("no stores yet", self.text(b[1]))

    def test_launcher_finds_the_bundled_engine(self):
        p = subprocess.run([sys.executable, "-m", "antechamber_launch", "--where"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(p.stdout.strip(), "mcp_stdio.py")))


if __name__ == "__main__":
    unittest.main()
