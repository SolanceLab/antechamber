# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Launcher for `antechamber-mcp`: puts the bundled engine on the path and runs the stdio MCP server."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def engine_dir():
    """The bundled engine (installed: _engine next to this file; from a checkout: the repo's engine folder)."""
    for d in (os.path.join(HERE, "_engine"), os.path.join(os.path.dirname(HERE), "engine")):
        if os.path.isfile(os.path.join(d, "mcp_stdio.py")):
            return d
    raise SystemExit("antechamber: the bundled engine is missing")


USAGE = """usage: antechamber-mcp [--help] [--where]

Antechamber's stdio MCP server: the app starts it, it speaks MCP on stdin and stdout.
Settings file: ~/.config/antechamber/plugin.toml (or $ANTECHAMBER_CONFIG).
A service settings file (config.toml) is never read by the plugin.
  --where   print the bundled engine folder
  -h, --help  show this text"""


def default_config(home=None):
    """Plugin settings file: plugin.toml; a 1.2.0 plugin-only config.toml (no service-only settings) is still used."""
    base = os.path.join(home or os.path.expanduser("~"), ".config", "antechamber")
    new, old = os.path.join(base, "plugin.toml"), os.path.join(base, "config.toml")
    if os.path.exists(new) or not os.path.isfile(old):
        return new
    # A config.toml that setup_stores itself wrote (1.2.0 plugin) keeps working, extra hand-added sections included.
    # Anything else may be a service file: leave it alone.
    try:
        with open(old, encoding="utf-8") as f:
            first = f.readline()
    except (OSError, UnicodeDecodeError):
        return new
    return old if first.startswith("# Written by Antechamber setup_stores") else new


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(USAGE)          # before the engine is imported
        return
    d = engine_dir()
    if "--where" in args:
        print(d)
        return
    os.environ["ANTECHAMBER_STDIO"] = "1"      # the engine skips service-only settings (shelf adapters, extensions)
    if not os.environ.get("ANTECHAMBER_CONFIG"):   # missing or empty: the engine treats both as unset
        os.environ["ANTECHAMBER_CONFIG"] = default_config()
    sys.path.insert(0, d)
    import mcp_stdio
    mcp_stdio.main()
