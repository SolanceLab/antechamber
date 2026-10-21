# Antechamber

Made by House of Solance · free for noncommercial use · credit required

## What it is

Antechamber is recall for your AI. One question in: every store you point it at (notes, chat exports, written memories, an Obsidian vault, a search service, a pgvector table) is searched at once, and only the best matches come back, dated, with their source and the lines around them.

On two public long-memory benchmarks it finds the evidence needed to answer far more often than standard vector search (numbers below). There is no language model inside recall: local embeddings, keyword shelves, fusion.

## What you need

- Python 3.11 or newer, on macOS or Linux. The engine uses the standard library only.
- Optional: an embedding model, either a local Ollama (for example `nomic-embed-text`) or any OpenAI-compatible endpoint. Without one, notes and chats are searched by words only.
- Optional: `psycopg`, only if you connect a pgvector table.
- Optional: [vault-map](https://github.com/SolanceLab/vault-map), only if you connect an Obsidian vault.

Antechamber runs on your own computer. The service listens on 127.0.0.1 only, and your stores never leave your machine unless you point the embedder at a remote endpoint and allow it out loud (`allow_remote_data = true`).

## Install

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

3. Install the plugin in Claude Code, one step:

```
claude plugin install antechamber@presence-app
```

Or straight from this repo: `claude plugin marketplace add SolanceLab/antechamber`, then `claude plugin install antechamber@antechamber`.

When the plugin asks, give it the server address `http://127.0.0.1:8790/mcp` (your port, if you changed it) and the key you set in `[mcp] key`.

## Settings

The plugin asks for two:

| Setting | What it is |
|---|---|
| Server address | Where your Antechamber server answers, e.g. `http://127.0.0.1:8790/mcp` |
| Key | The key your server expects (`[mcp] key` or `ANTECHAMBER_MCP_KEY`) |

The service reads one settings file: `~/.config/antechamber/config.toml`, or wherever `$ANTECHAMBER_CONFIG` points. Start from [antechamber.example.toml](antechamber.example.toml).

| Section | What it sets |
|---|---|
| `[service]` | `port` (default 8790); `extensions = ["mcp_http:register"]` mounts the MCP endpoint |
| `[mcp]` | `key`: the bearer key for POST /mcp. Empty = MCP off |
| `[embedder]` | `backend` (`ollama` or `openai`), `url`, `model`, `dims`; `api_key_env` names an environment variable, never the key |
| `[index]` | `dir` for the local search indexes; `refresh_minutes` (default 15) |
| `[recall]` | `extra_seat_shelves`, `extra_seats` (default 3): reserved seats, below |
| `[[adapters.*]]` | one table per store: `notes`, `chats`, `memories`, `vaultmap`, `service`, `pgvector` |

Each adapter's file in `engine/adapters/` documents its full table. Ids are lower-case letters, digits, `-` or `_`, and never change once chosen.

**Reserved seats for a vault.** A vault's notes can be crowded out of the top results by chat lines. Give them their own seats:

```toml
[recall]
extra_seat_shelves = ["vault"]   # the id of your [[adapters.vaultmap]]
extra_seats = 3
```

Measured on our notes: vault notes in the final results went from 0 to 7 of 24 on a fresh question set, with no loss on 72 other questions.

## Tools

| Tool | What it does |
|---|---|
| `recall` | One question in plain words; the best matches from every connected store come back as text: date, source, title or section, then the passage or the conversation lines around the hit. `limit` 1–20, default 8. |

Over plain HTTP the service also answers `GET /antechamber/recall?q=...&limit=...` (JSON) and `GET /health`.

## Limits

- One tool, `recall`, at most 20 matches per call (default 8).
- The service listens on 127.0.0.1 only and refuses requests whose Host or Origin is not local (extra names can be listed in `[service] allowed_hosts`). To reach it from another machine you need your own tunnel or proxy.
- MCP is off until a key is set; every request needs `Authorization: Bearer <key>`. Request bodies above 1 MB are refused.
- Notes files above 2,000,000 bytes and chat exports above 500,000,000 bytes are skipped (logged).
- A search service adapter accepts answers of at most 1 MB, 50 items, 4,000 characters per item.
- Indexes refresh in the background every 15 minutes by default; a note written a minute ago may not be found yet.
- A non-local embedder is refused unless `allow_remote_data = true`, because it receives the text of every passage indexed.
- Chat exports: Claude and ChatGPT formats.
- macOS or Linux (the index uses file locks that Windows does not have).

## How it works

Every connected store is a shelf. A question goes to every shelf at once: notes and chats are searched by meaning (embeddings) and by keywords in a local SQLite index, stores that search themselves (a service, pgvector) answer on their own. Each shelf returns its ranked list; the lists are fused by reciprocal rank, never by score, so shelves using different models mix safely. The winners come back with their dates resolved and, for conversations, the lines around the hit. A shelf that fails is named in `failed` and recall carries on with the others.

The engine is in `engine/`: `antechamber_recall.py` (fusion and the adapter registry), `shelves/` (the shelf port, for writing your own), `adapters/` (the stores above), `recall_service.py` (the HTTP service) and `mcp_http.py` (the MCP endpoint).

## What we measured

**Recall** (the headline, proven at full scale). Antechamber finds the evidence needed to answer far more often than standard vector search, on every question the method never saw:

| Benchmark | Questions | Standard search | Antechamber | Gain |
|---|---|---|---|---|
| LoCoMo | 1,493 | 56.7% | **78.9%** | +22.2 points |
| LongMemEval (S, cleaned) | 412 | 62.6% | **80.3%** | +17.7 points |

"Found" = every evidence line for the question is among what recall hands the reader. No language model inside recall: local embeddings, keyword shelves, fusion.

**With written memories** (optional supplement). On top of Antechamber, a memory writer plus People cards (our method) vs a plain generic memory writer, answered by three readers and judged by Grok with the official LongMemEval judging prompts (LoCoMo strict, 600 questions per set; a step counts only if it holds on two separate sets):

- Small local model (Qwen 3.5 9B): **+6.5 / +6.3** over generic memory, **+12** over conversation only. Holds.
- Large models (Claude Sonnet, GPT Sol): ties generic memory; Sol +3–5 over conversation only.
- LongMemEval: 30 questions per set, too few to separate the setups (Sonnet already 97% on validation with conversation only).

**Vault notes** (vault-map adapter; measured on our own build notes, not a public benchmark). The right note among the top results for a fresh question, 24 questions per set, three never-seen sets: vault-map v1.2.0 **3, 2, 2 of 24**; section search **20, 15, 18 of 24**. Inside Antechamber's final top 8 the section search lifts vault notes from 0–1 to 6–9 of 24. Demonstration on our notes, small samples.

**Honest limits of these numbers.**

- Answer accuracy with standard search was not measured; the recall gain is the claim.
- LongMemEval answering used 60 + 28 questions, not all 500.
- Judge: Grok, not GPT-4o; LoCoMo judged with LongMemEval's general prompt; F1 reported alongside.
- "Not mentioned" (trick) questions: every reader answers instead of abstaining (0–30%), memory or not.

**Benchmark data licences.** LoCoMo: CC BY-NC 4.0 (cite, do not redistribute its text commercially). LongMemEval: MIT. Neither dataset is included in this repo.

## Under development

No claims yet; listed so you know what is coming.

- **Valence and salience recall**: feeling tags shown with each memory; for unasked surfacing, valence-aware fading (grief fades faster than joy), per "Why the Past Should Whisper". Deliberate questions are never steered by feeling. Not yet measured.
- Decay, resonance boost, warming by use: built in parts, off by default, not yet measured.

## Credit and use

Antechamber is made by House of Solance. You're welcome to use it, change it and build on it for any noncommercial purpose, as long as our credit travels with it.

**Keep the credit.** Every copy, changed or not, must carry the licence and this line:
`Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)`
Removing or replacing it breaks the licence.

**Commercial use needs our permission.** Ask us first.

**We look for copies.** When we find our work without its credit, or used commercially without permission, we ask for it to be taken down.

## Licence

[PolyForm Noncommercial 1.0.0](LICENSE.md). See [NOTICE.md](NOTICE.md).
