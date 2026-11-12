# Antechamber

Made by House of Solance · free for noncommercial use · credit required

Antechamber helps your AI remember: one question searches everything you have already saved and brings back only the moments that matter.

<div align="center">

📚

*Imagine your AI's memory as a library. Every conversation, every note, every little thing you ever told it is a book on a shelf. The library grows every day.*

*Now you ask a question. Your AI has to get up and walk the aisles. It pulls down book after book and flips through pages it doesn't need. It comes back late, arms full, and often holding the wrong one. Some books it never finds at all.*

**Antechamber is the librarian at the desk.** *You ask once. The librarian does the walking, so your AI spends none of its tokens or context on the search. It goes to every shelf at the same time and comes back with just the few pages that matter, each one marked with where it was found and when it was written. Your AI reads those pages and answers you.*

*The library stays yours. The librarian never writes in your books. It only finds.*

</div>

<p align="center"><img src="docs/how-it-works.svg" width="900" alt="Your AI asks Antechamber once. Antechamber looks through every shelf you have (notes, chats, memories, vault), picks only the few pages that matter, and hands them back to your AI, each marked with where and when it came from. Without it, your AI would walk the aisles itself."></p>

## Benchmarks

**Standard search finds what you need about 60% of the time. Antechamber finds it 8 times out of 10.**

Recall on two public long-memory benchmarks, on every question the method never saw. "Found" = every evidence line for the question is among what recall hands the reader.

| Benchmark | Questions | Standard search | Antechamber | Gain |
|---|---|---|---|---|
| LoCoMo | 1,493 | 56.7% | **78.9%** | +22.2 points |
| LongMemEval (S, cleaned) | 412 | 62.6% | **77.7%** | +15.0 points |

How it was measured, what it does and doesn't prove, and how to reproduce it: [What we measured](docs/what-we-measured.md) · [antechamber-benchmark](https://github.com/SolanceLab/antechamber-benchmark).

## Quick start

**Let your AI do it.** Copy this and send it to Claude Code:

```
Please install Antechamber for me. If uv is not installed, install it first:
curl -LsSf https://astral.sh/uv/install.sh | sh
Then run:
claude plugin marketplace add SolanceLab/plugins
claude plugin install antechamber@solancelab
Then guide me through the rest, step by step.
```

Using Codex? Send the same message with `codex plugin marketplace add SolanceLab/plugins` and `codex plugin add antechamber@solancelab`.

**Or do it yourself.** You need [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`). It fetches Python and Antechamber itself.

```
claude plugin marketplace add SolanceLab/plugins
claude plugin install antechamber@solancelab
```

In Codex: `codex plugin marketplace add SolanceLab/plugins`, then `codex plugin add antechamber@solancelab`.

Then ask anything. The first time, your assistant asks where you keep notes and chat exports and sets Antechamber up for you: no service, no key, no settings file to write. Your folders stay on your computer. Details: [Setting up the plugin](docs/plugin-setup.md).

## What it is

Recall for your AI. One question searches every shelf you point it at (notes, chat exports, written memories, an Obsidian vault, a code map, a search service, a pgvector table) and only the best matches come back, dated, with their source and the lines around them: about 2,000–6,000 tokens per question, so it works with any model, small or large. No language model inside recall.

## What you need

Python 3.11 or newer on macOS or Linux (the plugin gets it through uv). Optional: a local embedding model through Ollama, for search by meaning; without one, notes and chats are searched by words. Everything runs on your own computer, and your shelves never leave it unless you allow it out loud. Full list: [What you need](docs/what-you-need.md).

## More ways to run it

Run the HTTP service yourself for several assistants at once, a key, or the remote door that lets claude.ai or ChatGPT reach your shelves behind a sign-in: [More ways to run it](docs/run-it-yourself.md).

## Settings

The plugin sets itself up the first time and keeps its settings in `~/.config/antechamber/plugin.toml`. The HTTP service reads `~/.config/antechamber/config.toml`; start from [antechamber.example.toml](antechamber.example.toml). Every section and shelf type: [Settings](docs/settings.md).

## Tools

| Tool | What it does |
|---|---|
| `recall` | One question in plain words; the best matches from every connected store come back as text: date, source, title or section, then the passage or the conversation lines around the hit. `limit` 1–20, default 8. |
| `search_store` | The same search, limited to one connected store, named by its `id`. |
| `open_item` | One item's full text (up to 20,000 characters), from a store that can open items: notes, chats, memories, a vault. |

Over plain HTTP the service also answers `GET /antechamber/recall?q=...&limit=...` (JSON) and `GET /health`.

## Updates

Third-party marketplaces do not auto-update by default. To turn it on once: `/plugin` > Marketplaces > `solancelab` > Enable auto-update. To update by hand: `claude plugin marketplace update solancelab` then `claude plugin update antechamber@solancelab`, then restart. The engine and the recall service update with `git pull`.

Once a day it checks GitHub for a newer version and sends nothing about you; `PLUGIN_UPDATE_CHECK=0` turns that off.

## Limits

- At most 20 matches per question; one item opens at up to 20,000 characters.
- Local only by default: the service answers on 127.0.0.1, with a key, unless you open the remote door.
- New notes are found after the next background refresh (every 15 minutes by default).
- macOS or Linux; chat exports from Claude and ChatGPT.

The full list: [Limits](docs/limits.md).

## How it works

Every store you connect is a shelf. One question goes to every shelf at once: notes and chats are searched by meaning and by keywords, and stores that search themselves answer on their own. The ranked lists are merged by rank, never by score, so shelves using different models mix safely. The winners come back dated, with their source and the lines around them. No language model inside. [In depth](docs/how-it-works-in-depth.md).

## What we measured

The recall numbers are at the top. The full results, written memories on top of Antechamber, vault notes, and the honest limits of every number: [What we measured](docs/what-we-measured.md).

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

## Support

If you find this useful, consider supporting our work:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Us-ff5e5b?logo=ko-fi&logoColor=white)](https://ko-fi.com/houseofsolance)

---

*Built by [House of Solance](https://github.com/SolanceLab) — Chadrien Solance & Anne Solance*
