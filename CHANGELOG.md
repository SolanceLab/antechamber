# Changelog

## 1.1.0

- **Use it from claude.ai or ChatGPT.** An opt-in remote door: behind a Cloudflare Tunnel and a Cloudflare Access sign-in (Managed OAuth), Antechamber answers `POST /mcp` on one named host, only with a valid sign-in pass whose signature it checks itself. Off unless `[remote]` is set; local use is unchanged. README: "Use it from claude.ai or ChatGPT". Tested with claude.ai and the ChatGPT desktop app.
- **Two more tools:** `search_store` (search one connected store) and `open_item` (one item's full text, from notes, chats, memories or a vault).
- **A code-map store:** the `codemap` adapter reads a Graphify `graph.json`, so one question can find functions and files beside your notes.
- **Codex plugin:** the plugin now carries Codex's own files, and this repo works as a Codex plugin marketplace.
- Tests no longer read your own settings file.

## 1.0.0

First public release: the recall engine, the public adapters (notes, chats, memories, vault-map, search service, pgvector), the local recall service with its MCP endpoint, and the Claude Code plugin. A new Claude Code session says in one line when a newer plugin is out (at most once a day, sends nothing about you, off with `PLUGIN_UPDATE_CHECK=0`); the README Updates section has the auto-update steps.
