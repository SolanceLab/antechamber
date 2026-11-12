# Settings

[← back to the README](../README.md)

The service reads one settings file: `~/.config/antechamber/config.toml` (the plugin uses `plugin.toml` beside it), or wherever `$ANTECHAMBER_CONFIG` points. Start from [antechamber.example.toml](../antechamber.example.toml).

| Section | What it sets |
|---|---|
| `[service]` | `port` (default 8790); `extensions = ["mcp_http:register"]` mounts the MCP endpoint |
| `[mcp]` | `key`: the bearer key for POST /mcp. Empty = MCP off |
| `[embedder]` | `backend` (`ollama` or `openai`), `url`, `model`, `dims`; `api_key_env` names an environment variable, never the key |
| `[index]` | `dir` for the local search indexes; `refresh_minutes` (default 15) |
| `[recall]` | `extra_seat_shelves`, `extra_seats` (default 3): reserved seats, below |
| `[[adapters.*]]` | one table per store: `notes`, `chats`, `memories`, `vaultmap`, `codemap`, `workmap`, `service`, `pgvector` (`codemap` reads a code map written by [Graphify](https://github.com/Graphify-Labs/graphify) with `graphify extract <dir> --code-only`: `graph = "path/to/graph.json"`; `workmap` reads the decisions, pipeline items, tasks and ships that [work-map](https://github.com/SolanceLab/work-map) indexes: `work_map = "path/to/work_map.py"`, `config = "path/to/work-map.json"`, optional `work_map_sha256`) |

Each adapter's file in `engine/adapters/` documents its full table. Ids are lower-case letters, digits, `-` or `_`, and never change once chosen.

**Reserved seats for a vault.** A vault's notes can be crowded out of the top results by chat lines. Give them their own seats:

```toml
[recall]
extra_seat_shelves = ["vault"]   # the id of your [[adapters.vaultmap]]
extra_seats = 3
```

Measured on our notes: vault notes in the final results went from 0 to 7 of 24 on a fresh question set, with no loss on 72 other questions.
