# More ways to run it

[← back to the README](../README.md)

### Advanced: the HTTP service

For several clients at once, a key, or the remote door, run the service yourself.

1. Get the code and make your settings file:

```
git clone https://github.com/SolanceLab/antechamber.git
cd antechamber
mkdir -p ~/.config/antechamber
cp antechamber.example.toml ~/.config/antechamber/config.toml
```

2. Edit the settings (point `[[adapters.notes]]` at your notes, set `[mcp] key`), check them, and start the service:

```
python3 engine/antechamber_recall.py --check
python3 engine/recall_service.py
```

3. Connect any MCP client to the service (see the next section). The plugin from the Quick start does not use the service; it runs its own copy.

### Connect other assistants

The HTTP service answers any MCP client running on the same computer, with the key.

- **Codex:** `codex mcp add antechamber --url http://127.0.0.1:8790/mcp --bearer-token-env-var ANTECHAMBER_MCP_KEY` (keep the key in that environment variable).
- **Any other MCP client:** an HTTP MCP server at `http://127.0.0.1:8790/mcp`, with the header `Authorization: Bearer <your key>`.

### Use it from claude.ai or ChatGPT (remote door)

Cloud apps cannot reach your computer, so Antechamber can open one door to the internet, locked by a sign-in. It stays closed until you set it up. You need a domain on Cloudflare (a free plan works) and `cloudflared` installed.

1. **A tunnel to your computer.** `cloudflared tunnel create antechamber`, then `cloudflared tunnel route dns antechamber antechamber.example.com`, and run it with a config that sends only `/mcp` to Antechamber:

   ```yaml
   tunnel: <tunnel id>
   credentials-file: /path/to/<tunnel id>.json
   ingress:
     - hostname: antechamber.example.com
       path: ^/mcp$
       service: http://127.0.0.1:8790
       originRequest:
         httpHostHeader: antechamber.example.com
     - service: http_status:404
   ```

2. **A sign-in in front of it.** In Cloudflare Zero Trust: add the **One-time PIN** login method, then add a **Self-hosted application** for `antechamber.example.com` with one **Allow** policy for your own email. In the application's advanced settings turn on **Managed OAuth** with dynamic client registration, and allow these return addresses: `https://claude.ai/api/mcp/auth_callback`, `https://claude.com/api/mcp/auth_callback`, plus "any localhost" and "any loopback" (the ChatGPT desktop app signs in through your own computer). Copy the application's **AUD tag**.
3. **Tell Antechamber about the door** in your settings file, then restart it:

   ```toml
   [remote]
   host = "antechamber.example.com"                    # exactly the tunnel's hostname
   access_team = "https://<your-team>.cloudflareaccess.com"
   access_aud = "<the application's AUD tag>"
   ```

   On that host Antechamber answers only `POST /mcp`, and only with a valid Cloudflare sign-in pass, whose signature it checks itself. Everything else stays local, and local use still needs your key.
4. **Connect the app.**
   - **claude.ai:** Settings, Connectors, Add custom connector, address `https://antechamber.example.com/mcp`. Leave the OAuth fields empty; it detects the sign-in.
   - **ChatGPT desktop:** Plugins, Add, Add MCP server, type Streamable HTTP, the same address, no token or headers.
   - Sign in with the email from step 2 and type the code it sends you promptly; a slow sign-in can expire. The sign-in lasts 24 hours.

Tested on 5 November 2026 with claude.ai and the ChatGPT desktop app. Grok's custom connectors use the same kind of sign-in but have not been tested.
