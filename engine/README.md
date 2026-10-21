The room itself: the engine.

- `antechamber_recall.py`: recall (fusion by reciprocal rank, dates, ordering) and the adapter registry; `--check` validates a settings file.
- `shelves/`: the shelf port (`Shelf`, `FnShelf`, `Ctx`, `register_kind`), for writing your own store.
- `adapters/`: the public stores (notes, chats, memories, vault-map, search service, pgvector) and their local indexes.
- `recall_service.py`: the local HTTP service (`/antechamber/recall`, `/health`, extensions).
- `mcp_http.py`: the MCP endpoint (`POST /mcp`).
- `config.py`, `query.py`, `dates.py`, `embed.py`: settings, question cleaning, date resolution, embeddings.
- `test_*.py` and `tests/fixtures/`: the tests (`make test` from the repo root).
