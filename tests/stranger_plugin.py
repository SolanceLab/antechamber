# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The stranger test: install the plugin the way a new person would, into empty Claude Code and Codex
homes, then talk to the server that install started and make it recall a sample note.

  python3 tests/stranger_plugin.py            (needs the claude, codex and uvx commands; takes a minute or two)

What it does, in throwaway folders (nothing of yours is read or changed):
  1. copies this checkout, with the plugin's `uvx --from` address pointed at the copy (the real tag may not exist yet);
  2. claude plugin marketplace add + plugin install, into an empty CLAUDE_CONFIG_DIR; same for codex in an empty CODEX_HOME;
  3. reads the server command each tool recorded, runs exactly that command, and speaks MCP over stdio to it:
     recall before setup (must say there are no stores), look_for_stores, setup_stores, recall (must quote the sample note);
  4. checks that setup_stores is gone once configured.
The step it cannot run is the assistant itself asking the two first-run questions; that needs a logged-in model.
Exit 0 only if every check passed."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

QUESTION = "which ferry leaves from Labuan Bajo at dawn"
SAMPLE = "The ferry to Komodo leaves from Labuan Bajo at dawn, and the tickets are sold at the harbour office."
PIN = "git+https://github.com/SolanceLab/antechamber@v"
REPO = Path(__file__).resolve().parent.parent
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    return ok


def run(cmd, env, cwd=None, timeout=600):
    p = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def copy_checkout(dest):
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "build", "*.egg-info", ".venv")
    shutil.copytree(REPO, dest, ignore=ignore)
    n = 0
    for p in dest.rglob("*.json"):
        t = p.read_text(encoding="utf-8")
        if PIN in t:
            # "git+https://github.com/SolanceLab/antechamber@v1.2.0" -> the local copy
            import re
            p.write_text(re.sub(re.escape(PIN) + r"\d+\.\d+\.\d+", str(dest), t), encoding="utf-8")
            n += 1
    return n


class Mcp:
    """One MCP stdio session with the server command an install recorded."""

    def __init__(self, command, args, env):
        self.p = subprocess.Popen([command, *args], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        self.n = 0
        self.call_raw("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "stranger-test", "version": "1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def call_raw(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        return json.loads(line) if line else {"error": "server closed"}

    def tools(self):
        return [t["name"] for t in self.call_raw("tools/list", {}).get("result", {}).get("tools", [])]

    def tool(self, name, **args):
        r = self.call_raw("tools/call", {"name": name, "arguments": args}).get("result", {})
        return "".join(c.get("text", "") for c in r.get("content", [])), bool(r.get("isError"))

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def server_from(json_files):
    """The (command, args) of the antechamber server in the first recorded .mcp.json / plugin.json found."""
    for f in json_files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        block = d.get("mcpServers", d)
        if isinstance(block, dict) and isinstance(block.get("antechamber"), dict) and "command" in block["antechamber"]:
            return block["antechamber"]["command"], block["antechamber"].get("args", [])
    return None, None


def drive(label, command, args, env, notes):
    """Walk the first-run path against the server an install recorded."""
    check(f"{label}: install recorded a command server", bool(command), "no antechamber command found")
    if not command:
        return
    m = Mcp(command, args, env)
    try:
        names = m.tools()
        check(f"{label}: tools listed (recall, search_store, open_item, setup_stores, look_for_stores)",
              {"recall", "search_store", "open_item", "setup_stores", "look_for_stores"} <= set(names), str(names))
        text, _ = m.tool("recall", query=QUESTION)
        check(f"{label}: recall before setup says there are no stores", "no stores yet" in text, text[:120])
        text, err = m.tool("look_for_stores", paths=[str(notes)])
        check(f"{label}: look_for_stores sees the notes folder", "notes" in text and not err, text[:160])
        text, err = m.tool("setup_stores", stores=[{"kind": "notes", "path": str(notes)}])
        check(f"{label}: setup_stores sets it up", "is set up" in text and not err, text[:200])
        text, err = m.tool("recall", query=QUESTION)
        check(f"{label}: recall quotes the sample note", "ferry to Komodo" in text and not err, text[:200])
        names = m.tools()
        check(f"{label}: setup_stores and look_for_stores are gone once configured",
              "setup_stores" not in names and "look_for_stores" not in names, str(names))
        text, err = m.tool("setup_stores", stores=[{"kind": "notes", "path": str(notes)}])
        check(f"{label}: a setup_stores call after setup is refused", err, text[:160])
    finally:
        m.close()


def main():
    for tool in ("claude", "codex", "uvx"):
        if not shutil.which(tool):
            sys.exit(f"stranger test needs {tool} on PATH")
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        mkt = td / "marketplace"
        pinned = copy_checkout(mkt)
        check("local marketplace copy: manifests point at the copy", pinned >= 2, f"{pinned} files rewritten")
        sample = td / "home" / "notes"
        sample.mkdir(parents=True)
        (sample / "travel.md").write_text("# Travel\n\n" + SAMPLE + "\n", encoding="utf-8")
        home = td / "home"
        base = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}
        (td / "tmp").mkdir()
        base["TMPDIR"] = str(td / "tmp")
        base["HOME"] = str(home)                                   # a stranger's empty home; also the policy root
        base["ANTECHAMBER_HOME_ROOT"] = str(home)
        base["XDG_CONFIG_HOME"] = str(td / "xdg")
        base["XDG_CACHE_HOME"] = str(td / "cache")
        base["UV_CACHE_DIR"] = str(td / "uvcache")
        base["ANTECHAMBER_CONFIG"] = str(td / "xdg" / "antechamber" / "config.toml")
        base["PLUGIN_UPDATE_CHECK"] = "0"

        # ---- Claude Code ----
        cenv = {**base, "CLAUDE_CONFIG_DIR": str(td / "claude")}
        (td / "claude").mkdir()
        rc, out = run(["claude", "plugin", "marketplace", "add", str(mkt)], cenv, td)
        check("claude: marketplace add", rc == 0, out[-300:])
        rc, out = run(["claude", "plugin", "install", "antechamber@antechamber"], cenv, td)
        check("claude: plugin install", rc == 0, out[-300:])
        rc, out = run(["claude", "plugin", "list"], cenv, td)
        check("claude: plugin list shows antechamber", rc == 0 and "antechamber" in out, out[-300:])
        installed = sorted((td / "claude").rglob("plugin.json")) + sorted((td / "claude").rglob(".mcp.json"))
        installed = [p for p in installed if "marketplaces" not in p.parts or "plugin" in p.parts]
        cmd, args = server_from(installed)
        drive("claude", cmd, args, {**cenv}, sample)
        shutil.rmtree(td / "xdg", ignore_errors=True)             # a fresh settings file for the Codex walk

        # ---- Codex ----
        xenv = {**base, "CODEX_HOME": str(td / "codex")}
        (td / "codex").mkdir()
        rc, out = run(["codex", "plugin", "marketplace", "add", str(mkt)], xenv, td)
        check("codex: marketplace add", rc == 0, out[-300:])
        rc, out = run(["codex", "plugin", "add", "antechamber@antechamber"], xenv, td)
        check("codex: plugin add", rc == 0, out[-300:])
        installed = sorted((td / "codex").rglob(".mcp.json")) + sorted((td / "codex").rglob("plugin.json"))
        cmd, args = server_from(installed)
        drive("codex", cmd, args, {**xenv}, sample)

    print(f"\n{sum(results)}/{len(results)} checks passed in {time.time() - t0:.0f}s", flush=True)
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
