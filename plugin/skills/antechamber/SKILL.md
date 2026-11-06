---
name: antechamber
description: Use when the answer may already be written down in the user's own stores (notes, past conversations, memories, a vault): a person, a date, a past decision, something said before, "have we dealt with this before?". Says how to set Antechamber up on first run, how to ask it (recall, search_store, open_item), and how to report what it returns.
---

# Antechamber

## First run: set up the stores

When `recall` answers "Antechamber has no stores yet", set it up now, in this conversation:

1. Ask the user which folders to search: notes (a folder of Markdown or text files), chats (a Claude or ChatGPT export file or folder), memories (a folder of memory files). Ask for full paths.
2. Call `setup_stores` with `stores: [{kind, path, name?, format?}]`. Leave `embedder` out (words-only search) unless the user says Ollama runs on their computer; then `embedder: "ollama"` adds search by meaning.
3. Repeat the user's original question with `recall`. No restart is needed.

Once stores exist, `setup_stores` needs `overwrite: true` to change them (a `.bak` copy of the old settings is kept). Say plainly what it set up and where the settings file is.

## Reach for `recall`

`recall` is one call over every store the user connected to Antechamber: notes, chat exports, written memories, a vault, a search service or a database. It returns only the best matches, already ranked, each with its date, its source and (for conversations) the lines around the hit.

- Ask in plain words, the way the user would ask. A full question works better than a list of keywords.
- `limit` is 1–20, default 8. Raise it for "list everything" questions; keep it low otherwise.
- One question per call. For two unrelated things, make two calls.
- `search_store` searches one store by its id; `open_item` reads one result in full (give the `store` and `id` the result showed).

## Say where an answer came from

When your reply uses what recall returned, say so and name the source and date it came with, so the user can check it. Never pass a recalled fact off as something you simply know.

## When recall comes back empty or wrong

- `No matches for: ...` means nothing connected answered. Say so plainly; never fill the gap with a guess dressed as memory.
- `(not searched this time: ...)` names stores that failed for this question. Tell the user the answer may be incomplete.
- `recall failed: ...` is an error from a store. Report it; do not retry in a loop.
- If a recalled passage is wrong or out of date, believe the user over the record.

## Limits

- Recall only reads. It cannot write, edit or delete anything in the user's stores.
- It finds passages; it does not answer the question for you. Read what comes back and answer from it.
- New notes appear after the next index refresh (every 15 minutes by default), not instantly.
- Antechamber runs on the user's own computer, started by the app. If the tool is missing, `uv` is probably not installed (see the README Quick start).
