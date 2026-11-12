# What you need

[← back to the README](../README.md)

- Python 3.11 or newer, on macOS or Linux. The engine uses the standard library only.
- Optional: an embedding model, either a local Ollama (for example `nomic-embed-text`) or any OpenAI-compatible endpoint. Without one, notes and chats are searched by words only.
- Optional: `psycopg`, only if you connect a pgvector table.
- Optional: [Graphify](https://github.com/Graphify-Labs/graphify), only to build a code map for the `codemap` store (Antechamber only reads its `graph.json`).
- Optional: [vault-map](https://github.com/SolanceLab/vault-map), only if you connect an Obsidian vault.

Antechamber runs on your own computer. The service listens on 127.0.0.1 only, and your stores never leave your machine unless you point the embedder at a remote endpoint and allow it out loud (`allow_remote_data = true`).
