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


def main():
    d = engine_dir()
    if "--where" in sys.argv[1:]:
        print(d)
        return
    sys.path.insert(0, d)
    import mcp_stdio
    mcp_stdio.main()
