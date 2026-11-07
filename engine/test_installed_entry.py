# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The installed `antechamber-mcp`, run from a clean virtual environment and never from the repo folder.

Installs a copy of this package into a throwaway venv, then runs the console script with an empty working folder,
no PYTHONPATH and a temporary HOME. Skips with a message when there is no installer or no network, unless
REQUIRE_CLEAN_VENV=1, which turns the skip into a failure."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SERVICE_CONFIG = ('[shelves]\nadapters = ["house.adapters:SHELVES"]\n\n'
                  '[service]\nextensions = ["house.service:register"]\n')
REQUIRED = os.environ.get("REQUIRE_CLEAN_VENV") == "1"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
RECALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "recall", "arguments": {"query": "zebra"}}}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstalledEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="antechamber-installed-")
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.exe = None
        try:
            cls.exe = cls._install()
        except Exception as e:
            if REQUIRED:
                raise AssertionError(f"REQUIRE_CLEAN_VENV=1 but the clean install failed: {e}")
            raise unittest.SkipTest(f"clean venv install not possible here ({e}); set REQUIRE_CLEAN_VENV=1 to make this fail")

    @classmethod
    def _install(cls):
        src = os.path.join(cls.tmp, "src")        # a copy, so building never writes build/ or egg-info into the repo
        shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "build", "*.egg-info", ".venv"))
        venv = os.path.join(cls.tmp, "venv")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        if shutil.which("uv"):
            steps = [["uv", "venv", venv], ["uv", "pip", "install", "--python", os.path.join(venv, "bin", "python"), src]]
        else:
            steps = [[sys.executable, "-m", "venv", venv],
                     [os.path.join(venv, "bin", "pip"), "install", "--no-build-isolation", src]]
        for cmd in steps:
            p = subprocess.run(cmd, env=env, cwd=cls.tmp, capture_output=True, text=True, timeout=600)
            if p.returncode:
                raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout)[-300:]}")
        exe = os.path.join(venv, "bin", "antechamber-mcp")
        if not os.path.isfile(exe):
            raise RuntimeError("the install made no antechamber-mcp command")
        return exe

    def home(self, config=None):
        home = tempfile.mkdtemp(dir=self.tmp)
        if config is not None:
            d = os.path.join(home, ".config", "antechamber")
            os.makedirs(d)
            with open(os.path.join(d, "config.toml"), "w") as f:
                f.write(config)
        return home

    def run_exe(self, home, args=(), messages=(), config=None):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "TMPDIR": self.tmp}   # no PYTHONPATH
        if config is not None:
            env["ANTECHAMBER_CONFIG"] = config
        p = subprocess.run([self.exe, *args], env=env, cwd=tempfile.mkdtemp(dir=self.tmp), capture_output=True,
                           text=True, timeout=60, input="".join(json.dumps(m) + "\n" for m in messages))
        return p, [json.loads(line) for line in p.stdout.splitlines() if messages and line.strip()]

    def test_help_exits_zero(self):
        for flag in ("--help", "-h"):
            p, _ = self.run_exe(self.home(), [flag])
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("usage: antechamber-mcp", p.stdout)

    def test_handshake_and_recall_before_setup(self):
        p, out = self.run_exe(self.home(), messages=[INIT, RECALL])
        self.assertIn("result", out[0], p.stderr)
        self.assertIn("no stores yet", json.dumps(out[1]))

    def test_service_config_is_ignored(self):
        home = self.home(SERVICE_CONFIG)
        p, out = self.run_exe(home, messages=[INIT, RECALL])
        self.assertIn("result", out[0], p.stderr)
        self.assertIn("no stores yet", json.dumps(out[1]))
        said = [line for line in p.stderr.splitlines() if "house" in line.lower()]
        self.assertEqual(said, [], said)
        with open(os.path.join(home, ".config", "antechamber", "config.toml")) as f:
            self.assertEqual(f.read(), SERVICE_CONFIG)      # the service file is left alone

    def test_running_the_engine_file_directly_is_isolated_too(self):
        home = self.home(SERVICE_CONFIG)
        script = None
        for root, _, files in os.walk(os.path.join(os.path.dirname(self.exe), os.pardir)):
            if os.path.basename(root) == "_engine" and "mcp_stdio.py" in files:
                script = os.path.join(root, "mcp_stdio.py")
        self.assertTrue(script, "installed engine not found")
        env = {"PATH": os.environ.get("PATH", ""), "HOME": home, "TMPDIR": self.tmp}
        p = subprocess.run([os.path.join(os.path.dirname(self.exe), "python"), script], env=env,
                           cwd=tempfile.mkdtemp(dir=self.tmp), capture_output=True, text=True, timeout=60,
                           input="".join(json.dumps(m) + "\n" for m in [INIT, RECALL]))
        out = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        self.assertIn("result", out[0], p.stderr)
        self.assertIn("no stores yet", json.dumps(out[1]))
        self.assertNotIn("ModuleNotFoundError", p.stderr)

    def test_empty_config_variable_counts_as_unset(self):
        home = self.home(SERVICE_CONFIG)
        p, out = self.run_exe(home, messages=[INIT, RECALL], config="")
        self.assertIn("result", out[0], p.stderr)
        self.assertNotIn("ignored in the plugin", p.stderr)     # the service file was never read

    def test_explicit_service_config_is_ignored_with_one_line(self):
        home = self.home(SERVICE_CONFIG)
        p, out = self.run_exe(home, messages=[INIT], config=os.path.join(home, ".config", "antechamber", "config.toml"))
        self.assertIn("result", out[0], p.stderr)
        said = [line for line in p.stderr.splitlines() if "house" in line.lower()]
        self.assertEqual(said, [], said)          # the one line names the settings, never the modules
        self.assertEqual(p.stderr.count("ignored in the plugin"), 1, p.stderr)


if __name__ == "__main__":
    unittest.main()
