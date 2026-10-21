The MCP server is part of the recall service: `POST /mcp` on your own Antechamber server ([`engine/mcp_http.py`](../engine/mcp_http.py)), MCP over HTTP with one tool, `recall`.

It is off until you set a key: `[mcp] key = "..."` in the settings file, or the `ANTECHAMBER_MCP_KEY` environment variable. Mount it with `[service] extensions = ["mcp_http:register"]`. Every request must carry `Authorization: Bearer <key>`; the service listens on 127.0.0.1 only.

Address and key are the plugin's two settings (`plugin/.claude-plugin/plugin.json`).
