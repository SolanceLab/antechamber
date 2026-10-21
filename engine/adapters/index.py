# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""A local search index for one adapter instance: one SQLite file, stdlib only (numpy used when present).

Each indexed file becomes passages; each passage keeps its text, a unit vector (when an embedder is set) and the
words it holds. A search ranks by meaning and by rare words, then fuses both lists by rank inside the adapter, so
every adapter casts exactly one list into recall.

Safety rules (review fixes 1, 2, 9, 10, 11, 12):
  - the index records a fingerprint (schema, adapter settings, root, splitter, embedder); any change rebuilds it;
  - a changed file is read, split and embedded first, then swapped in within ONE transaction; on any failure the
    last good version stays; a lock file stops two processes refreshing the same index at once;
  - vectors are checked (dimension, finite, non-zero) before they are written;
  - the sha256 of every file is checked on every refresh (a same-size, same-mtime edit is still caught);
  - when a file changes or goes, files that skipped one of its ids are re-read, so a shared id never vanishes;
  - every skip (too big, binary, too many passages) is logged.
"""
import fcntl
import hashlib
import itertools
import json
import logging
import math
import os
import re
import sqlite3
import threading

from .embedders import EmbedError, clean_vector, pack, unpack

try:                                       # optional speed-up
    import numpy as _np
except Exception:                          # pragma: no cover - depends on the machine
    _np = None

log = logging.getLogger("antechamber.adapters")

WORDS_K = 40
MEANING_K = 40
RRF_K = 60
SCHEMA_VERSION = 1
STOP = set("""the and for are but not you your with this that have has had was were will would could should
what when where which who whom why how did does doing done can about from into than then them they their there
these those over under very just also any all some our ours his her hers its it's i'm i've don't been being out
get got let lets one two too yes she him""".split())


def words(text):
    """Searchable words of a text: lower-case, three letters or more (or two-plus digits), no stop words."""
    return {w for w in re.findall(r"\w+", (text or "").lower())
            if (len(w) >= 3 or (w.isdigit() and len(w) >= 2)) and w not in STOP}


def default_dir():
    return os.path.join(os.path.expanduser("~"), ".cache", "antechamber")


class Index:
    """reader(path) -> passage dicts (a list or a generator; the reader opens the file itself, so a large
       export can be streamed):
         {"id", "text", optional "date", "source", "title", "section", "thread", "pos", "sender", "metadata"}
    candidates() -> {path: (mtime, size)} of the files that should be indexed now."""

    def __init__(self, path, fingerprint, embedder, candidates, reader, max_passages=2000, opener=None, max_bytes=None):
        self.path, self.embedder, self.candidates, self.reader = path, embedder, candidates, reader
        self.max_passages = max_passages
        self.opener = opener
        self.max_bytes = max_bytes
        self.fingerprint = hashlib.sha256(json.dumps(
            {"schema": SCHEMA_VERSION, **fingerprint,
             "embedder": embedder.fingerprint() if embedder else None}, sort_keys=True, default=str).encode()).hexdigest()
        self._mem_lock = threading.Lock()
        self._mem = None                  # (generation, ids, matrix or list of vectors)
        d = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(d, mode=0o700, exist_ok=True)   # the index holds people's private text: owner-only
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        with self._db() as db:
            self._schema(db)

    # ------------------------------------------------------------ storage
    def _db(self):
        old = os.umask(0o077)                       # the db, its WAL/SHM and the lock file are created 0600
        try:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        finally:
            os.umask(old)
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA journal_mode = WAL")
        db.row_factory = sqlite3.Row
        self._tighten()
        return _Closing(db)

    def _tighten(self):
        """Existing index files (db, WAL, SHM, lock) from an older, looser run are made owner-only too."""
        for suffix in ("", "-wal", "-shm", "-journal", ".lock"):
            try:
                os.chmod(self.path + suffix, 0o600)
            except OSError:
                pass

    def _schema(self, db):
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            row = db.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            if row is None or row[0] != self.fingerprint:
                if row is not None:
                    log.info("index %s: settings changed, rebuilding", self.path)
                for t in ("passages", "terms", "files"):
                    db.execute(f"DROP TABLE IF EXISTS {t}")
            db.execute("""CREATE TABLE IF NOT EXISTS passages(id TEXT PRIMARY KEY, path TEXT, source TEXT, title TEXT,
                          section TEXT, date TEXT, text TEXT, tsha TEXT, thread TEXT, pos INTEGER, sender TEXT,
                          metadata TEXT, dims INTEGER, vec BLOB)""")
            db.execute("CREATE INDEX IF NOT EXISTS passages_path ON passages(path)")
            db.execute("CREATE INDEX IF NOT EXISTS passages_thread ON passages(thread, pos)")
            db.execute("CREATE INDEX IF NOT EXISTS passages_tsha ON passages(tsha)")
            db.execute("CREATE TABLE IF NOT EXISTS terms(term TEXT, pid TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS terms_term ON terms(term)")
            db.execute("CREATE INDEX IF NOT EXISTS terms_pid ON terms(pid)")
            db.execute("""CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, sha256 TEXT,
                          embedded INTEGER, dups INTEGER)""")
            db.execute("INSERT OR REPLACE INTO meta VALUES('fingerprint', ?)", (self.fingerprint,))
            db.execute("INSERT OR IGNORE INTO meta VALUES('generation', '0')")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------ refresh
    def refresh(self):
        """Bring the index up to date with the files. Returns a summary dict, or {"skipped": "locked"} when
        another process is refreshing it. Never raises for one bad file; that file keeps its last good version."""
        lock = os.fdopen(os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600), "a+")
        self._tighten()
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return {"skipped": "locked"}
            return self._refresh()
        finally:
            lock.close()

    def _refresh(self):
        stats = {"added": 0, "changed": 0, "removed": 0, "unchanged": 0, "failed": 0, "embed_failed": 0, "retaken": 0}
        want = self.candidates()
        with self._db() as db:
            have = {r["path"]: r for r in db.execute("SELECT * FROM files")}
            moved = False
            for path in [p for p in have if p not in want]:
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._drop_file(db, path)
                    db.execute("COMMIT")
                    stats["removed"] += 1
                    moved = True
                except Exception:
                    db.execute("ROLLBACK")
                    raise
            for path, (mtime, size) in sorted(want.items()):
                old = have.get(path)
                sha = self._sha(path, stats)
                if sha is None:
                    continue
                if old is not None and old["sha256"] == sha and old["embedded"]:
                    if old["mtime"] != mtime or old["size"] != size:
                        db.execute("UPDATE files SET mtime=?, size=? WHERE path=?", (mtime, size, path))
                    stats["unchanged"] += 1
                    continue
                if self._try_index(db, path, mtime, size, sha, stats):
                    moved = True
                    stats["changed" if old is not None else "added"] += 1
            if moved:   # read from the files table AFTER the pass above, so files added in this refresh are included
                for (path,) in db.execute("SELECT path FROM files WHERE dups=1 ORDER BY path").fetchall():
                    if path not in want:
                        continue
                    sha = self._sha(path, stats)
                    if sha is not None and self._try_index(db, path, *want[path], sha, stats):
                        stats["retaken"] += 1
            if any(stats[k] for k in ("added", "changed", "removed", "retaken")):
                db.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key='generation'")
        return stats

    def _sha(self, path, stats):
        try:
            return file_sha256(path, self.opener, self.max_bytes)
        except OSError as e:
            log.warning("index %s: cannot read %s (%s); kept last version", self.path, path, e)
            stats["failed"] += 1
            return None

    def _try_index(self, db, path, mtime, size, sha, stats):
        """_index_file that never raises: a failure is logged and counted and the file keeps its last good version.
        True when the file was (re)written."""
        try:
            res = self._index_file(db, path, mtime, size, sha)
        except Exception as e:      # a bad file never stops the others
            log.warning("index %s: %s not indexed (%s); kept last version", self.path, path, e)
            stats["failed"] += 1
            return False
        if res == "embed_failed":
            stats["embed_failed"] += 1
        return True

    def _take_over(self, db, path, ids):
        """Ids this file keeps that a later path holds: the later file loses them and is marked dups, so the
        owner of a shared id is always the earliest path."""
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            args = [path] + chunk
            held = [r[0] for r in db.execute(f"SELECT DISTINCT path FROM passages WHERE path > ? AND id IN ({marks})", args)]
            if not held:
                continue
            db.execute(f"DELETE FROM terms WHERE pid IN (SELECT id FROM passages WHERE path > ? AND id IN ({marks}))", args)
            db.execute(f"DELETE FROM passages WHERE path > ? AND id IN ({marks})", args)
            db.executemany("UPDATE files SET dups=1 WHERE path=?", [(h,) for h in held])

    def _drop_file(self, db, path):
        db.execute("DELETE FROM terms WHERE pid IN (SELECT id FROM passages WHERE path=?)", (path,))
        db.execute("DELETE FROM passages WHERE path=?", (path,))
        db.execute("DELETE FROM files WHERE path=?", (path,))

    def _index_file(self, db, path, mtime, size, sha):
        it = iter(self.reader(path))
        try:    # never more than max_passages + 1 items are pulled from a streaming reader
            passages = list(itertools.islice(it, self.max_passages + 1))
        finally:
            close = getattr(it, "close", None)
            if close is not None:
                close()
        if len(passages) > self.max_passages:
            log.warning("index %s: %s has more than %d passages; only the first %d are indexed",
                        self.path, path, self.max_passages, self.max_passages)
            passages = passages[: self.max_passages]
        # duplicates: an id an EARLIER path (sorted) already holds is skipped (first file wins, by path, whatever the
        # order the files were indexed in); an id a LATER path holds is taken over below
        others = set()
        ids = [p["id"] for p in passages]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT id FROM passages WHERE path < ? AND id IN ({','.join('?' * len(chunk))})"
            others.update(r[0] for r in db.execute(q, [path] + chunk))
        seen, keep = set(), []
        for p in passages:
            if p["id"] in others or p["id"] in seen:
                continue
            seen.add(p["id"])
            keep.append(p)
        dups = int(len(keep) < len(passages))
        for p in keep:
            p["tsha"] = hashlib.sha256(p["text"].encode()).hexdigest()
        # vectors first, outside the write: reuse a stored vector for text we already embedded
        embedded, vecs = 1, [None] * len(keep)
        if self.embedder is not None and keep:
            dims = self.embedder.dims
            known = {}
            shas = list({p["tsha"] for p in keep})
            for i in range(0, len(shas), 500):
                chunk = shas[i:i + 500]
                for r in db.execute(f"SELECT tsha, vec FROM passages WHERE dims=? AND vec IS NOT NULL AND tsha IN "
                                    f"({','.join('?' * len(chunk))})", [dims] + chunk):
                    known.setdefault(r[0], r[1])
            need = [i for i, p in enumerate(keep) if p["tsha"] not in known]
            for i, p in enumerate(keep):
                if p["tsha"] in known:
                    vecs[i] = known[p["tsha"]]
            if need:
                try:
                    out = self.embedder.embed_batch([keep[i]["text"] for i in need])
                    for i, v in zip(need, out):
                        vecs[i] = pack(clean_vector(v, dims))
                except (EmbedError, OSError, ValueError) as e:
                    prev = db.execute("SELECT embedded FROM files WHERE path=?", (path,)).fetchone()
                    if prev is not None and prev[0]:
                        raise EmbedError(f"embedding failed ({e})")   # last good vectors stay
                    log.warning("index %s: embedding %s failed (%s); searchable by words until the next refresh",
                                self.path, path, e)
                    embedded, vecs = 0, [None] * len(keep)
        # then one swap
        db.execute("BEGIN IMMEDIATE")
        try:
            self._drop_file(db, path)
            self._take_over(db, path, [p["id"] for p in keep])
            for p, v in zip(keep, vecs):
                db.execute("INSERT INTO passages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (p["id"], path, p.get("source"), p.get("title"), p.get("section"), p.get("date"), p["text"],
                            p["tsha"], p.get("thread"), p.get("pos"), p.get("sender"),
                            json.dumps(p.get("metadata") or {}, ensure_ascii=False, default=str),
                            self.embedder.dims if v is not None else None, v))
                db.executemany("INSERT INTO terms VALUES(?,?)",
                               [(t, p["id"]) for t in words(" ".join(filter(None, (p.get("title"), p.get("section"),
                                                                                    p["text"]))))])
            db.execute("INSERT INTO files VALUES(?,?,?,?,?,?)", (path, mtime, size, sha, embedded, dups))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        return "ok" if embedded else "embed_failed"

    # ------------------------------------------------------------ search
    def _vectors(self, db):
        gen = db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0]
        with self._mem_lock:
            if self._mem and self._mem[0] == gen:
                return self._mem
            ids, vecs = [], []
            dims = self.embedder.dims if self.embedder else 0
            for r in db.execute("SELECT id, vec FROM passages WHERE vec IS NOT NULL AND dims=?", (dims,)):
                ids.append(r[0])
                vecs.append(unpack(r[1]))
            mat = _np.asarray(vecs, dtype=_np.float32) if (_np is not None and vecs) else vecs
            self._mem = (gen, ids, mat)
            return self._mem

    def meaning(self, qvec, k=MEANING_K):
        """Passage ids by cosine similarity to qvec, best first (stored vectors are unit length)."""
        n = math.sqrt(sum(x * x for x in qvec))
        if n == 0.0 or not all(math.isfinite(x) for x in qvec):
            return []
        q = [x / n for x in qvec]
        with self._db() as db:
            _, ids, mat = self._vectors(db)
        if not ids:
            return []
        if _np is not None and not isinstance(mat, list):
            sims = mat @ _np.asarray(q, dtype=_np.float32)
            order = _np.argsort(-sims, kind="stable")[:k]
            return [(ids[i], float(sims[i])) for i in order]
        sims = [(sum(a * b for a, b in zip(v, q)), i) for i, v in enumerate(mat)]
        sims.sort(key=lambda s: (-s[0], s[1]))
        return [(ids[i], s) for s, i in sims[:k]]

    def keyword(self, question, k=WORDS_K):
        """Passage ids holding the question's rarest words, best first."""
        qw = words(question)
        if not qw:
            return []
        with self._db() as db:
            total = db.execute("SELECT COUNT(*) FROM passages").fetchone()[0] or 1
            score = {}
            for w in qw:
                rows = db.execute("SELECT pid FROM terms WHERE term=?", (w,)).fetchall()
                if not rows:
                    continue
                idf = math.log(1 + total / len(rows))
                for (pid,) in rows:
                    score[pid] = score.get(pid, 0.0) + idf
        return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:k]

    def search(self, question, qvec, limit=20):
        """One ranked list of passage rows: meaning and words fused by rank (k=60). qvec None -> words only."""
        lists = [self.keyword(question)]
        if qvec is not None:
            lists.insert(0, self.meaning(qvec))
        score = {}
        for lst in lists:
            for rank, (pid, _) in enumerate(lst):
                score[pid] = score.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
        best = sorted(score, key=lambda p: (-score[p], p))[:limit]
        rows = self.rows(best)
        cover = self.coverage(question, [r["id"] for r in rows])
        for r in rows:
            r["_cover"] = cover.get(r["id"], 0.0)
        return rows

    def coverage(self, question, ids):
        """{id: share of the question's words (weighted by rarity) that the passage holds}, 0..1.

        Each adapter casts one ranked list, so the top of every list ties in rank fusion; this share is the
        adapter's tie-break (see local_shelf): among equally ranked tops, the passage holding the question's rare words goes
        another adapter's best guess. Words this index has never seen count at the highest rarity."""
        qw = sorted(words(question))
        if not qw or not ids:
            return {}
        with self._db() as db:
            total_n = db.execute("SELECT COUNT(*) FROM passages").fetchone()[0] or 1
            idf = {}
            for w in qw:
                df = db.execute("SELECT COUNT(*) FROM terms WHERE term=?", (w,)).fetchone()[0]
                idf[w] = math.log(1 + total_n / (df or 0.5))
            whole = sum(idf.values()) or 1.0
            out = {i: 0.0 for i in ids}
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                q = (f"SELECT pid, term FROM terms WHERE pid IN ({','.join('?' * len(chunk))}) "
                     f"AND term IN ({','.join('?' * len(qw))})")
                for pid, term in db.execute(q, chunk + qw):
                    out[pid] += idf[term] / whole
        return out

    def rows(self, ids):
        if not ids:
            return []
        with self._db() as db:
            got = {r["id"]: dict(r) for r in db.execute(
                f"SELECT id, path, source, title, section, date, text, thread, pos, sender, metadata FROM passages "
                f"WHERE id IN ({','.join('?' * len(ids))})", ids)}
        return [got[i] for i in ids if i in got]

    def window(self, thread, pos, before=2, after=3):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, sender, text, pos FROM passages WHERE thread=? AND pos BETWEEN ? AND ? ORDER BY pos",
                (thread, pos - before, pos + after))]

    def count(self):
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM passages").fetchone()[0]


def file_sha256(path, opener=None, max_bytes=None):
    """sha256 of the opened descriptor's content; with max_bytes it never reads more than max_bytes + 1 bytes
    and raises OSError for a file over the limit (the caller skips and logs it)."""
    h = hashlib.sha256()
    left = None if max_bytes is None else max_bytes + 1
    with (opener or (lambda p: open(p, "rb")))(path) as f:
        while left is None or left > 0:
            chunk = f.read(1 << 20 if left is None else min(1 << 20, left))
            if not chunk:
                break
            h.update(chunk)
            if left is not None:
                left -= len(chunk)
        if left is not None and left <= 0:
            raise OSError(f"{path} is over the {max_bytes}-byte limit")
    return h.hexdigest()


class _Closing:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *a):
        self.db.close()


def index_path(cfg, instance_id, base_dir):
    d = ((cfg or {}).get("index") or {}).get("dir") or default_dir()
    d = os.path.expanduser(d)
    if not os.path.isabs(d):
        d = os.path.join(base_dir, d)
    return os.path.join(d, instance_id + ".sqlite")


# ------------------------------------------------------------ shared by the adapters
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def instance_ids(inst, kind):
    """(id, name) of one adapter instance. id is immutable (it is the fusion key namespace and the index file
    name); name is only shown."""
    iid = inst.get("id")
    if not isinstance(iid, str) or not ID_RE.match(iid):
        raise ValueError(f"[[adapters.{kind}]] needs an id: lower-case letters, digits, - or _ (got {iid!r})")
    name = inst.get("name") or iid
    if not isinstance(name, str):
        raise ValueError(f"[[adapters.{kind}]] {iid}: name must be text")
    return iid, name


def mark_degraded(ctx, iid):
    """Tell recall this adapter answered by words only (its question embedding failed)."""
    try:
        ctx.__dict__.setdefault("degraded", set()).add(iid)
    except AttributeError:
        pass


def local_shelf(iid, index, embedder, to_item, limit=20):
    """One FnShelf over a local Index: meaning + words fused inside, one ranked list of items out."""
    from shelves import FnShelf

    def search(ctx):
        q = getattr(ctx, "q", "") or ""
        qvec = None
        if embedder is not None:
            try:
                qvec = embedder.query(q)
            except Exception as e:                    # words still answer; recall reports `degraded`
                log.warning("adapter %s: question embedding failed (%s); answering by words only", iid, e)
                mark_degraded(ctx, iid)
        return [to_item(r) for r in index.search(q, qvec, limit)]

    # Fusion is by RANK, never by score (a design rule of this engine). Coverage (share of the question's
    # rarity-weighted words in the passage) only BREAKS EXACT TIES: the nudge (< 1e-6 of a vote) can never reorder
    # anything two ranks apart, only which of two equally ranked tops goes first.
    shelf = FnShelf(iid, search, iid, "id", weight=lambda it, ctx: 1.0 + 1e-6 * min(1.0, it.get("_cover") or 0.0))
    shelf.index = index            # read by refresh_all / start_refresher; never inside a recall
    return shelf


def row_item(iid, r, default_source):
    meta = {}
    try:
        meta = json.loads(r.get("metadata") or "{}")
    except ValueError:
        pass
    return {"id": r["id"], "text": r["text"], "date": r.get("date"), "source": r.get("source") or default_source,
            "title": r.get("title"), "section": r.get("section"), "metadata": meta, "kind": "item", "_ns": iid,
            "_cover": r.get("_cover", 0.0)}
