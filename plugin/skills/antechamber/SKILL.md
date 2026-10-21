---
name: antechamber
description: Use when the answer may already be written down in the user's own stores (notes, past conversations, memories, a vault): a person, a date, a past decision, something said before, "have we dealt with this before?". Says how to ask Antechamber's one tool, recall, and how to report what it returns.
---

# Antechamber

## Reach for `recall`

`recall` is one call over every store the user connected to their Antechamber server: notes, chat exports, written memories, a vault, a search service or a database. It returns only the best matches, already ranked, each with its date, its source and (for conversations) the lines around the hit.

- Ask in plain words, the way the user would ask. A full question works better than a list of keywords.
- `limit` is 1–20, default 8. Raise it for "list everything" questions; keep it low otherwise.
- One question per call. For two unrelated things, make two calls.

## Say where an answer came from

When your reply uses what recall returned, say so and name the source and date it came with, so the user can check it. Never pass a recalled fact off as something you simply know.

## When recall comes back empty or wrong

- `No matches for: ...` means nothing connected answered. Say so plainly; never fill the gap with a guess dressed as memory.
- `(not searched this time: ...)` names stores that failed for this question. Tell the user the answer may be incomplete.
- `recall failed: ...` is a server error. Report it; do not retry in a loop.
- If a recalled passage is wrong or out of date, believe the user over the record.

## Limits

- Recall only reads. It cannot write, edit or delete anything in the user's stores.
- It finds passages; it does not answer the question for you. Read what comes back and answer from it.
- New notes appear after the next index refresh (every 15 minutes by default), not instantly.
- The server runs on the user's own computer; if the tool is unreachable, the service is probably not running.
