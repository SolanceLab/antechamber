# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The plugin's own environment, set BEFORE the settings module is imported: plugin mode (service-only settings are
never imported) and the plugin's own default settings file. Used by the launcher and by `python3 mcp_stdio.py`.
Standard library only."""
import os


def default_config(home=None):
    """Plugin settings file: plugin.toml; a config.toml that setup_stores itself wrote (1.2.0) is still used."""
    base = os.path.join(home or os.path.expanduser("~"), ".config", "antechamber")
    new, old = os.path.join(base, "plugin.toml"), os.path.join(base, "config.toml")
    if os.path.exists(new) or not os.path.isfile(old):
        return new
    # Extra hand-added sections after setup are fine. Anything not written by setup_stores may be a service file: leave it alone.
    try:
        with open(old, encoding="utf-8") as f:
            first = f.readline()
    except (OSError, UnicodeDecodeError):
        return new
    return old if first.startswith("# Written by Antechamber setup_stores") else new


def apply():
    os.environ["ANTECHAMBER_STDIO"] = "1"             # the engine skips service-only settings (shelf adapters, extensions)
    if not os.environ.get("ANTECHAMBER_CONFIG"):      # missing or empty: the engine treats both as unset
        os.environ["ANTECHAMBER_CONFIG"] = default_config()
