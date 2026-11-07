# Antechamber contract

## Doors it offers

All on 127.0.0.1 only (requests with a non-local Host or Origin are refused).

| Door | What it answers |
|---|---|
| `GET /antechamber/recall?q=...&limit=...` | One question; the winners from every configured store as JSON (`query`, `winners`, `shelves`, `failed`). `limit` default 8. |
| `GET /health` | `ok` (settings loaded, adapters installed, every extension loaded, each public index refreshed once) and the settings file in use. |
| `POST /mcp` | MCP over HTTP (JSON responses), one tool: `recall`. Mounted by `[service] extensions = ["mcp_http:register"]`; answers 503 until a key is set, 401 without `Authorization: Bearer <key>`. |

## Data it owns

| Table / bucket / file | What is in it |
|---|---|
| `[index] dir` (default `~/.cache/antechamber`), one SQLite file per notes / chats / memories instance | The local search index for that store: passages, keywords, embeddings. Rebuilt from your files; safe to delete. |

Your notes, exports, vault and databases are read, never written.

## Settings and secrets it needs

Named here, never valued.

| Name | What it is |
|---|---|
| `ANTECHAMBER_CONFIG` | Path of the settings file (default `~/.config/antechamber/config.toml`; the plugin's stdio server defaults to `~/.config/antechamber/plugin.toml`) |
| `[mcp] key` or `ANTECHAMBER_MCP_KEY` | The bearer key for `POST /mcp` |
| `[embedder] api_key_env` | Names the environment variable holding a remote embedder's key |
| `[[adapters.service]] api_key_env` | Names the environment variable holding a search service's key |
| `[[adapters.pgvector]] dsn_env` | Names the environment variable holding a pgvector connection string |
