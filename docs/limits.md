# Limits

[← back to the README](../README.md)

- Three tools: `recall` and `search_store` return at most 20 matches per call (default 8); `open_item` returns at most 20,000 characters.
- The service listens on 127.0.0.1 only and refuses requests whose Host or Origin is not local (extra names can be listed in `[service] allowed_hosts`). The one exception is the opt-in remote door (see [More ways to run it](run-it-yourself.md)): one named host, `POST /mcp` only, with a valid Cloudflare sign-in pass.
- MCP is off until a key is set; every request needs `Authorization: Bearer <key>`. Request bodies above 1 MB are refused.
- Notes files above 2,000,000 bytes and chat exports above 500,000,000 bytes are skipped (logged).
- A search service adapter accepts answers of at most 1 MB, 50 items, 4,000 characters per item.
- Indexes refresh in the background every 15 minutes by default; a note written a minute ago may not be found yet.
- A non-local embedder is refused unless `allow_remote_data = true`, because it receives the text of every passage indexed.
- Chat exports: Claude and ChatGPT formats.
- macOS or Linux (the index uses file locks that Windows does not have).
