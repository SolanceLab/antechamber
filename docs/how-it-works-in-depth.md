# How it works, in depth

[← back to the README](../README.md)

Every connected store is a shelf. A question goes to every shelf at once: notes and chats are searched by meaning (embeddings) and by keywords in a local SQLite index, stores that search themselves (a service, pgvector) answer on their own. Each shelf returns its ranked list; the lists are fused by reciprocal rank, never by score, so shelves using different models mix safely. The winners come back with their dates resolved and, for conversations, the lines around the hit. A shelf that fails is named in `failed` and recall carries on with the others.

The engine is in `engine/`: `antechamber_recall.py` (fusion and the adapter registry), `shelves/` (the shelf port, for writing your own), `adapters/` (the stores above), `recall_service.py` (the HTTP service) and `mcp_http.py` (the MCP endpoint).
