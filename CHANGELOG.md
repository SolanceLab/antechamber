# Changelog

## 1.3.0

- Added the `workmap` shelf: decisions, pipeline items, tasks and ships from work-map, searched by keyword.

## 1.2.1

- **The plugin no longer crashes when a service settings file exists.** 1.2.0 read `~/.config/antechamber/config.toml` for the plugin too, so a person who also runs the HTTP service (whose settings name extra modules under `[shelves] adapters` and `[service] extensions`) got `ModuleNotFoundError` at start.
- The plugin now keeps its own settings file, `~/.config/antechamber/plugin.toml`, so a service `config.toml` is never read or overwritten by it. `ANTECHAMBER_CONFIG` still wins when you set it. A `config.toml` that 1.2.0's setup wrote (it starts with the line "# Written by Antechamber setup_stores.") is still used when there is no `plugin.toml`, so an upgrade keeps your stores.
- The plugin ignores service-only settings (`[shelves] adapters`, `[service] extensions`): it never imports them and prints one line to stderr saying so. The HTTP service is unchanged.
- `setup_stores` replaces a settings file only if it wrote it; any other existing file is left untouched.
- `antechamber-mcp --help` prints a short usage and exits without starting the engine.
- New test `engine/test_installed_entry.py` installs the package into a clean virtual environment and runs the installed `antechamber-mcp`; the stranger test now keeps a service settings file in its home.

## 1.2.0

- **Plug and play.** Installing the plugin in Claude Code or Codex is enough: the app starts Antechamber itself (MCP over stdio, run with `uvx`), so there is no service to start, no key and no port. The only prerequisite is `uv`.
- **First-run setup.** With no stores configured, `recall` says so and the new `setup_stores` tool lets the assistant ask which folders to search, write the settings file (mode 0600) and load them at once. The tool works only until stores exist: once the settings name stores, `setup_stores` and `look_for_stores` leave the tool list and a call is refused, so the assistant cannot repoint Antechamber. Later changes are made by editing the settings file. A settings file with no stores yet may be replaced (a `.bak` copy is kept).
- **Folder rules.** Setup and `look_for_stores` accept only folders inside your home folder (or `ANTECHAMBER_HOME_ROOT`), after following links. They refuse the home folder itself and private folders such as `.ssh`, `.gnupg`, `.aws`, `.config`, `Library/Keychains` and `Library/Application Support`.
- `pip`/`uvx` packaging: `antechamber-mcp` console script.
- The HTTP service and the remote door are unchanged; claude.ai cannot run local programs, so it keeps using the remote door.

## 1.1.0

- **Use it from claude.ai or ChatGPT.** An opt-in remote door: behind a Cloudflare Tunnel and a Cloudflare Access sign-in (Managed OAuth), Antechamber answers `POST /mcp` on one named host, only with a valid sign-in pass whose signature it checks itself. Off unless `[remote]` is set; local use is unchanged. README: "Use it from claude.ai or ChatGPT". Tested with claude.ai and the ChatGPT desktop app.
- **Two more tools:** `search_store` (search one connected store) and `open_item` (one item's full text, from notes, chats, memories or a vault).
- **A code-map store:** the `codemap` adapter reads a Graphify `graph.json`, so one question can find functions and files beside your notes.
- **Codex plugin:** the plugin now carries Codex's own files, and this repo works as a Codex plugin marketplace.
- Tests no longer read your own settings file.

## 1.0.0

First public release: the recall engine, the public adapters (notes, chats, memories, vault-map, search service, pgvector), the local recall service with its MCP endpoint, and the Claude Code plugin. A new Claude Code session says in one line when a newer plugin is out (at most once a day, sends nothing about you, off with `PLUGIN_UPDATE_CHECK=0`); the README Updates section has the auto-update steps.
