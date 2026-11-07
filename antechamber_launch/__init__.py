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
The plugin never picks up a service settings file (config.toml) on its own; only an $ANTECHAMBER_CONFIG you set points it elsewhere.
  --where   print the bundled engine folder
  -h, --help  show this text"""


def default_config(home=None):
    """Plugin settings file (the engine's stdio_env owns the rule)."""
    sys.path.insert(0, engine_dir())
    import stdio_env
    return stdio_env.default_config(home)


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(USAGE)          # before the engine is imported
        return
    d = engine_dir()
    if "--where" in args:
        print(d)
        return
    sys.path.insert(0, d)
    import stdio_env
    stdio_env.apply()
    import mcp_stdio
    mcp_stdio.main()
