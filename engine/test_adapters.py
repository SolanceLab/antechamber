# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Public adapters: a stranger's notes, chat exports, search service and pgvector table, with no private code.
Run: python3 -m unittest test_adapters -v   (from engine/). No network: fake servers listen on 127.0.0.1."""
import http.server
import json
import logging
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from adapters import chats, check as chk, embedders, index as ix, item, notes, pgvector, service
import adapters
from shelves import Ctx

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "tests", "fixtures")
logging.getLogger("antechamber.adapters").setLevel(logging.CRITICAL)


def ctx(q):
    return Ctx(None, q, lambda: None)


class FakeServer:
    """A JSON endpoint on 127.0.0.1. handler(path, body, headers) -> (status, payload or bytes, extra headers)."""

    def __init__(self, handler):
        outer = self
        self.calls = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.calls.append((self.path, body, dict(self.headers)))
                status, payload, extra = handler(self.path, body, self.headers)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FailingEmbedder(embedders.HashEmbedder):
    def __init__(self, dims=64):
        super().__init__(dims)
        self.fail = False

    def embed_batch(self, texts, timeout=None):
        if self.fail:
            raise embedders.EmbedError("embedder down")
        return super().embed_batch(texts, timeout)


def rrf(shelves, q, k=60):
    """A tiny local fusion, the same rule recall uses: sum of 1/(k + rank + 1) per fusion key."""
    c, score, items = ctx(q), {}, {}
    for s in shelves:
        for rank, it in enumerate(s.search(c)):
            key = s.key(it)
            score[key] = score.get(key, 0.0) + s.weight(it, c) / (k + rank + 1)
            items[key] = it
    return [items[key] for key in sorted(score, key=lambda x: -score[x])]


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)


# ---------------------------------------------------------------------------------------------- items
class Items(unittest.TestCase):
    def test_protocol_and_required_fields(self):
        self.assertEqual(item.ITEM_PROTOCOL, 1)
        with self.assertRaises(item.ItemError):
            item.make_item({"text": "no id"})
        with self.assertRaises(item.ItemError):
            item.make_item({"id": "a", "text": "   "})
        it = item.make_item({"id": 7, "text": "hello", "colour": "blue", "metadata": {"a": 1}})
        self.assertEqual(it["id"], "7")
        self.assertEqual(it["metadata"], {"a": 1, "colour": "blue"})     # unknown fields kept, not dropped
        self.assertEqual(it["kind"], "item")
        self.assertEqual(len(item.make_item({"id": "x", "text": "y" * 9000})["text"]), item.TEXT_CAP)

    def test_dates(self):
        n = item.normalize_date
        self.assertEqual(n("2025-04-12"), "2025-04-12")                    # a bare day stays a day
        self.assertEqual(n("2025-04-12T10:00:00+02:00"), "2025-04-12T08:00:00Z")
        self.assertEqual(n("2025-04-12T10:00:00Z"), "2025-04-12T10:00:00Z")
        self.assertEqual(n("2025-04-12 10:00"), "2025-04-12T10:00:00Z")   # naive -> UTC
        self.assertEqual(n(0), "1970-01-01T00:00:00Z")                     # epoch seconds
        for bad in ("yesterday", "2025-13-40", "2025-02-30", "", None, float("nan"), True, "12/04/2025"):
            self.assertIsNone(n(bad), bad)

    def test_renderer_and_window_coverage(self):
        class R:
            covered = set()
        it = {"id": "a", "text": "t" * 5000, "source": "s", "date": "2025-01-01", "title": None, "section": None,
              "_ns": "x", "lines": [{"id": "a", "sender": "user", "message": "hi", "hit": True},
                                    {"id": "b", "sender": "assistant", "message": "yo", "hit": False}]}
        w = item.render_item(it, R())
        self.assertEqual(w["kind"], "item")
        self.assertEqual(len(w["text"]), item.RENDER_CAP)
        self.assertEqual([ln["sender"] for ln in w["lines"]], ["user", "assistant"])
        r = R()
        item.render_item(it, r)
        self.assertIsNone(item.render_item(dict(it, id="b", lines=None), r))   # already shown in a window
        self.assertIsNotNone(item.render_item(dict(it, id="b", _ns="other", lines=None), r))


# ---------------------------------------------------------------------------------------------- notes
class Notes(TempDirCase):
    def test_passages_cut_at_spaces_under_headings(self):
        long = " ".join(["word%03d" % i for i in range(200)])
        ps = notes.passages("# Top\n\nintro paragraph long enough\n\n## Part\n" + long)
        self.assertEqual(ps[0], ("Top", "intro paragraph long enough"))
        self.assertTrue(all(h == "Part" for h, _ in ps[1:]))
        for _, body in ps:
            self.assertLessEqual(len(body), notes.PASSAGE_CHARS)
        self.assertEqual(" ".join(b for _, b in ps[1:]), long)            # nothing lost, nothing cut mid-word

    def test_glob(self):
        rx = notes.glob_regex("**/*.md")
        self.assertTrue(rx.match("a.md") and rx.match("x/y/a.md"))
        self.assertFalse(rx.match("a.txt"))
        self.assertFalse(notes.glob_regex("*.md").match("x/a.md"))

    def test_scan_reads_only_safe_regular_files(self):
        root = os.path.join(self.tmp, "n")
        shutil.copytree(os.path.join(FIX, "notes"), root)
        outside = os.path.join(self.tmp, "secret.md")
        with open(outside, "w") as f:
            f.write("# Secret\n\nThis file sits outside the folder and must never be read.\n")
        os.symlink(outside, os.path.join(root, "link.md"))
        os.symlink(self.tmp, os.path.join(root, "linkdir"))
        with open(os.path.join(root, "bin.md"), "wb") as f:
            f.write(b"# Bin\n\x00\x01\x02 binary bytes")
        with open(os.path.join(root, "big.md"), "w") as f:
            f.write("x" * 5000)
        found = notes.scan(os.path.realpath(root), "**/*.md", 4000)
        rel = sorted(os.path.relpath(p, os.path.realpath(root)) for p in found)
        self.assertEqual(rel, ["bikes/repair-log.md", "bin.md", "garden.md", "kitchen/soup.md"])
        self.assertEqual(notes.make_reader(os.path.realpath(root), 4000)(os.path.join(os.path.realpath(root), "bin.md")), [])

    def test_reader_dates_titles_bom(self):
        root = self.tmp
        p = os.path.join(root, "a.md")
        with open(p, "wb") as f:
            f.write("﻿# Heading one\n\nA paragraph that is long enough to keep.\n".encode("utf-8"))
        os.utime(p, (0, 86400))
        got = notes.make_reader(root, 10_000)(p)
        self.assertEqual(got[0]["title"], "Heading one")
        self.assertEqual(got[0]["date"], "1970-01-02T00:00:00Z")          # mtime, in UTC
        g = notes.make_reader(os.path.join(FIX, "notes"), 10_000)(os.path.join(FIX, "notes", "garden.md"))
        self.assertEqual({x["date"] for x in g}, {"2025-04-12"})          # front matter wins
        self.assertEqual(g[1]["section"], "Tomatoes")


# ---------------------------------------------------------------------------------------------- index
class LocalIndex(TempDirCase):
    def make(self, root, emb, fp=None):
        return ix.Index(os.path.join(self.tmp, "idx.sqlite"), fp or {"root": root}, emb,
                        lambda: notes.scan(root, "**/*.md", 100_000), notes.make_reader(root, 100_000))

    def write(self, path, text, mtime=None):
        with open(path, "w") as f:
            f.write(text)
        if mtime:
            os.utime(path, (mtime, mtime))

    def test_add_modify_delete_and_sha_is_the_truth(self):
        root = os.path.join(self.tmp, "n")
        os.mkdir(root)
        self.write(os.path.join(root, "a.md"), "# A\n\nApples grow on the old tree by the gate.\n", 1000)
        self.write(os.path.join(root, "b.md"), "# B\n\nBlue paint for the shed door, two coats.\n", 1000)
        emb = FailingEmbedder()
        idx = self.make(root, emb)
        self.assertEqual(idx.refresh()["added"], 2)
        self.assertEqual(idx.count(), 2)
        os.utime(os.path.join(root, "a.md"), (2000, 2000))                # touched, same bytes
        s = idx.refresh()
        self.assertEqual((s["changed"], s["unchanged"]), (0, 2))
        self.write(os.path.join(root, "a.md"), "# A\n\nPears grow on the young tree by the wall.\n", 3000)
        self.assertEqual(idx.refresh()["changed"], 1)
        self.assertEqual(idx.keyword("pears")[0][0].split("#")[0], "a.md")
        self.assertEqual(idx.keyword("apples"), [])
        os.remove(os.path.join(root, "b.md"))
        self.assertEqual(idx.refresh()["removed"], 1)
        self.assertEqual(idx.count(), 1)

    def test_fingerprint_change_rebuilds(self):
        root = os.path.join(self.tmp, "n")
        os.mkdir(root)
        self.write(os.path.join(root, "a.md"), "# A\n\nApples grow on the old tree by the gate.\n")
        idx = self.make(root, embedders.HashEmbedder(64))
        idx.refresh()
        idx2 = self.make(root, embedders.HashEmbedder(32))                # other embedder dims -> rebuild
        self.assertEqual(idx2.count(), 0)
        self.assertEqual(idx2.refresh()["added"], 1)

    def test_embed_failure_keeps_last_good_and_new_files_answer_by_words(self):
        root = os.path.join(self.tmp, "n")
        os.mkdir(root)
        self.write(os.path.join(root, "a.md"), "# A\n\nApples grow on the old tree by the gate.\n", 1000)
        emb = FailingEmbedder()
        idx = self.make(root, emb)
        idx.refresh()
        emb.fail = True
        self.write(os.path.join(root, "a.md"), "# A\n\nPears grow on the young tree by the wall.\n", 2000)
        self.write(os.path.join(root, "c.md"), "# C\n\nCherries ripen late in the shaded corner.\n", 2000)
        s = idx.refresh()
        self.assertEqual((s["failed"], s["embed_failed"]), (1, 1))
        self.assertTrue(idx.keyword("apples"))                            # last good version of a.md kept
        self.assertTrue(idx.keyword("cherries"))                          # new file searchable by words
        self.assertEqual(len(idx.meaning([1.0] + [0.0] * 63)), 1)
        emb.fail = False
        idx.refresh()
        self.assertTrue(idx.keyword("pears"))
        self.assertEqual(len(idx.meaning([1.0] + [0.0] * 63)), 2)          # c.md got its vectors on retry

    def test_lock_stops_a_second_refresh(self):
        import fcntl
        root = os.path.join(self.tmp, "n")
        os.mkdir(root)
        idx = self.make(root, None)
        with open(idx.path + ".lock", "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertEqual(idx.refresh(), {"skipped": "locked"})

    def test_vectors_are_checked(self):
        for bad in ([1.0, float("nan")], [0.0, 0.0], [1.0], [1.0, float("inf")], "x"):
            with self.assertRaises(embedders.EmbedError):
                embedders.clean_vector(bad, 2)
        v = embedders.clean_vector([3.0, 4.0], 2)
        self.assertAlmostEqual(math.hypot(*v), 1.0)

    def test_meaning_and_words_rank_and_zero_query(self):
        root = os.path.join(FIX, "notes")
        emb = embedders.HashEmbedder(256)
        idx = ix.Index(os.path.join(self.tmp, "i.sqlite"), {"r": root}, emb,
                       lambda: notes.scan(root, "**/*.md", 100_000), notes.make_reader(root, 100_000))
        idx.refresh()
        self.assertEqual(idx.meaning([0.0] * 256), [])                     # zero norm guarded
        top = idx.search("derailleur cable frayed", emb.embed("derailleur cable frayed"))[0]
        self.assertEqual((top["source"], top["section"]), ("bikes/repair-log.md", "Rear wheel"))
        self.assertEqual(idx.search("derailleur", None)[0]["section"], "Rear wheel")   # words only


# ---------------------------------------------------------------------------------------------- chats
class Chats(TempDirCase):
    def test_claude_reader_keeps_spoken_text_only(self):
        ps = list(chats.make_reader("claude")(os.path.join(FIX, "claude", "conversations.json")))
        texts = " ".join(p["text"] for p in ps)
        self.assertNotIn("secret reasoning", texts)
        self.assertNotIn("timetable\"", texts)
        self.assertEqual({p["sender"] for p in ps}, {"user", "assistant"})
        self.assertEqual(ps[0]["id"], "m-trip-1")
        self.assertEqual(ps[0]["date"], "2025-06-01T09:00:00Z")
        self.assertEqual([p["pos"] for p in ps if p["thread"] == "c-trip-0001"], [0, 1, 2, 3, 4])

    def test_chatgpt_follows_the_branch_the_person_saw(self):
        ps = list(chats.make_reader("chatgpt")(os.path.join(FIX, "chatgpt", "conversations.json")))
        self.assertEqual([p["id"] for p in ps], ["msg-ask", "msg-new"])
        self.assertIn("Pepper", ps[1]["text"])
        self.assertEqual(ps[0]["date"], "2025-01-01T10:00:00Z")

    def test_streaming_reader_with_tiny_chunks(self):
        p = os.path.join(FIX, "claude", "conversations.json")
        a = list(chats.iter_json_array(p, chunk=7))
        with open(p) as f:
            self.assertEqual(a, json.load(f))

    def test_duplicate_messages_across_exports_indexed_once_and_window(self):
        d = os.path.join(self.tmp, "exp")
        os.mkdir(d)
        for n in ("conversations.json", "conversations-copy.json"):
            shutil.copy(os.path.join(FIX, "claude", "conversations.json"), os.path.join(d, n))
        cfg = {"_config_dir": self.tmp, "index": {"dir": "idx"},
               "adapters": {"chats": [{"id": "c", "path": "exp", "format": "claude"}]}}
        [shelf] = chats.from_config(cfg, embedders.HashEmbedder(64))
        shelf.index.refresh()
        self.assertEqual(shelf.index.count(), 7)
        os.remove(os.path.join(d, "conversations.json"))                   # the copy takes over its messages
        shelf.index.refresh()
        self.assertEqual(shelf.index.count(), 7)
        hit = shelf.search(ctx("Harbour Point train"))[0]
        self.assertEqual(hit["id"], "m-trip-3")
        self.assertEqual([ln["hit"] for ln in hit["lines"]], [False, False, True, False, False])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "idx", "c.sqlite")))   # relative to the config


# ---------------------------------------------------------------------------------------------- embedders
class Embedders(unittest.TestCase):
    def serve(self, handler):
        s = FakeServer(handler)
        self.addCleanup(s.close)
        return s

    def test_ollama_backend(self):
        s = self.serve(lambda p, b, h: (200, {"embeddings": [[1.0, 0.0, 0.0]] * len(b["input"])}, None))
        e = embedders.from_config({"embedder": {"backend": "ollama", "url": s.url + "/api/embed", "model": "m",
                                                "dims": 3, "batch_size": 2}})
        self.assertTrue(e.is_local)
        self.assertEqual(len(e.embed_batch(["a", "b", "c"])), 3)
        self.assertEqual([c[0] for c in s.calls], ["/api/embed", "/api/embed"])   # batches of 2
        self.assertEqual(s.calls[0][1], {"model": "m", "input": ["a", "b"]})

    def test_openai_backend_with_key_and_order(self):
        os.environ["TEST_EMBED_KEY"] = "k-123"
        self.addCleanup(os.environ.pop, "TEST_EMBED_KEY")
        s = self.serve(lambda p, b, h: (200, {"data": [{"index": 1, "embedding": [0.0, 2.0]},
                                                       {"index": 0, "embedding": [2.0, 0.0]}]}, None))
        e = embedders.from_config({"embedder": {"backend": "openai", "url": s.url + "/v1", "model": "m", "dims": 2,
                                                "api_key_env": "TEST_EMBED_KEY"}})
        self.assertEqual(e.embed_batch(["a", "b"]), [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(s.calls[0][0], "/v1/embeddings")
        self.assertEqual(s.calls[0][2]["Authorization"], "Bearer k-123")

    def test_bad_answers_fail(self):
        for payload in ({"embeddings": [[1.0, 0.0]]}, {"embeddings": [[float("nan")] * 3]}, {"nope": 1},
                        b"x" * (embedders.MAX_RESPONSE + 64 * 3 * 32 + 10)):
            s = self.serve(lambda p, b, h, payload=payload: (200, payload, None))
            e = embedders.from_config({"embedder": {"url": s.url, "model": "m", "dims": 3, "batch_size": 64}})
            with self.assertRaises(embedders.EmbedError):
                e.embed("a")

    def test_redirects_are_not_followed(self):
        s = self.serve(lambda p, b, h: (302, {}, {"Location": "http://169.254.169.254/"}))
        e = embedders.from_config({"embedder": {"url": s.url, "model": "m", "dims": 3}})
        with self.assertRaises(embedders.EmbedError):
            e.embed("a")

    def test_remote_needs_allow_remote_data(self):
        cfg = {"embedder": {"backend": "openai", "url": "https://embed.example.com/v1", "model": "m", "dims": 8}}
        with self.assertRaises(embedders.ConfigError):
            embedders.from_config(cfg)
        cfg["embedder"]["allow_remote_data"] = True
        e = embedders.from_config(cfg)
        self.assertFalse(e.is_local)
        self.assertEqual(e.host, "embed.example.com")

    def test_url_rules(self):
        for url in ("ftp://localhost/x", "http://user:pw@localhost/x", "localhost:11434", ""):
            with self.assertRaises(embedders.ConfigError):
                embedders.from_config({"embedder": {"url": url, "model": "m", "dims": 3}})
        self.assertTrue(embedders.host_is_local("::1") and embedders.host_is_local("localhost"))
        self.assertFalse(embedders.host_is_local("192.168.1.4"))

    def test_query_is_cached(self):
        n = []
        s = self.serve(lambda p, b, h: (n.append(1), (200, {"embeddings": [[1.0, 0.0, 0.0]]}, None))[1])
        e = embedders.from_config({"embedder": {"url": s.url, "model": "m", "dims": 3}})
        e.query("same question")
        e.query("same question")
        self.assertEqual(len(n), 1)


# ---------------------------------------------------------------------------------------------- service
class Service(unittest.TestCase):
    def serve(self, handler):
        s = FakeServer(handler)
        self.addCleanup(s.close)
        return s

    def shelf(self, url, **kw):
        return service.from_config({"adapters": {"service": [dict({"id": "svc", "name": "Wiki", "url": url}, **kw)]}})[0]

    def test_loopback_refused_by_default_allowed_with_allow_local(self):
        s = self.serve(lambda p, b, h: (200, {"items": [{"id": 1, "text": "hello " + b["query"],
                                                          "date": "2025-02-03"}]}, None))
        with self.assertRaises(service.ServiceError):
            self.shelf(s.url + "/recall").search(ctx("q"))
        self.assertEqual(s.calls, [])                                       # refused before connecting
        got = self.shelf(s.url + "/recall", allow_local=True, limit=5).search(ctx("tea"))
        self.assertEqual(got[0]["id"], "1")
        self.assertEqual(got[0]["text"], "hello tea")
        self.assertEqual(got[0]["source"], "Wiki")
        self.assertEqual(s.calls[0][1], {"query": "tea", "limit": 5})
        self.assertEqual(self.shelf(s.url, allow_local=True).key(got[0]), ("svc", "1"))

    def test_strict_answers_and_caps(self):
        many = {"items": [{"id": i, "text": "t" * 5000} for i in range(80)]}
        s = self.serve(lambda p, b, h: (200, many, None))
        got = self.shelf(s.url, allow_local=True).search(ctx("q"))
        self.assertEqual(len(got), service.MAX_ITEMS)
        self.assertEqual(len(got[0]["text"]), item.TEXT_CAP)
        for bad in ({"items": "no"}, [1, 2], {"items": [{"id": "a"}]}, {"items": [{"id": "a", "text": "x",
                                                                                   "metadata": 3}]}):
            s2 = self.serve(lambda p, b, h, bad=bad: (200, bad, None))
            with self.assertRaises(service.ServiceError):
                self.shelf(s2.url, allow_local=True).search(ctx("q"))
        big = self.serve(lambda p, b, h: (200, b"[" + b" " * (service.MAX_BYTES + 5) + b"]", None))
        with self.assertRaises(service.ServiceError):
            self.shelf(big.url, allow_local=True).search(ctx("q"))
        redirect = self.serve(lambda p, b, h: (301, {}, {"Location": "http://example.com/"}))
        with self.assertRaises(service.ServiceError):
            self.shelf(redirect.url, allow_local=True).search(ctx("q"))

    def test_private_ranges_refused(self):
        for ip in ("10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.169.254", "127.0.0.1", "::1", "fe80::1",
                   "::ffff:127.0.0.1", "100.64.0.1", "0.0.0.0"):
            self.assertTrue(embedders._blocked(ip), ip)
        self.assertFalse(embedders._blocked("93.184.216.34"))

    def test_url_rules(self):
        for url in ("file:///etc/passwd", "http://u:p@example.com/"):
            with self.assertRaises(embedders.ConfigError):
                self.shelf(url)


# ---------------------------------------------------------------------------------------------- pgvector
class Pgvector(unittest.TestCase):
    def test_identifiers_are_validated(self):
        ok = {"id": "p", "dsn_env": "X", "table": "passages", "id_col": "id", "text_col": "body", "vec_col": "emb"}
        self.assertEqual(pgvector.validate(ok), [])
        for k, v in (("table", "a; drop table x"), ("schema", "public.x"), ("vec_col", "1abc"), ("date_col", "a b")):
            self.assertTrue(pgvector.validate(dict(ok, **{k: v})), k)
        self.assertTrue(pgvector.validate({"id": "p"}))                     # required fields named

    @unittest.skipIf(__import__("importlib").util.find_spec("psycopg") is not None, "psycopg is installed")
    def test_refuses_to_load_without_psycopg(self):
        cfg = {"adapters": {"pgvector": [{"id": "p"}]}}
        with self.assertRaises(embedders.ConfigError) as e:
            pgvector.from_config(cfg)
        self.assertIn("psycopg", str(e.exception))


# ---------------------------------------------------------------------------------------------- check
class Check(TempDirCase):
    def test_good_config_and_cli(self):
        os.makedirs(os.path.join(self.tmp, "notes"))
        p = os.path.join(self.tmp, "config.toml")
        with open(p, "w") as f:
            f.write('[embedder]\nurl = "http://localhost:11434"\nmodel = "m"\ndims = 8\n'
                    '[index]\ndir = "idx"\n'
                    '[[adapters.notes]]\nid = "notes"\nfolder = "notes"\n')     # relative to the config's folder
        self.assertEqual(chk.check(chk.load_config(p)), [])
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(chk.main([p]), 0)
        self.assertIn("ok", out.getvalue())

    def test_problems_are_named(self):
        ro = os.path.join(self.tmp, "ro")
        os.mkdir(ro)
        os.chmod(ro, 0o500)
        self.addCleanup(os.chmod, ro, 0o700)
        cfg = {"_config_dir": self.tmp,
               "embedder": {"backend": "openai", "url": "https://embed.example.com/v1", "model": "m", "dims": 8,
                            "api_key_env": "NO_SUCH_ENV_VAR_1"},
               "index": {"dir": ro},
               "adapters": {"notes": [{"id": "a", "folder": "missing"}],
                            "chats": [{"id": "a", "path": "x.json", "format": "slack"}],
                            "service": [{"id": "s", "url": "ftp://x", "api_key_env": "NO_SUCH_ENV_VAR_2"}],
                            "pgvector": [{"id": "Bad Id", "dsn_env": "NO_SUCH_ENV_VAR_3", "table": "t;x"}],
                            "mystery": [{}]}}
        probs = "\n".join(chk.check(cfg))
        for needle in ("used twice", "NO_SUCH_ENV_VAR_1", "NO_SUCH_ENV_VAR_2", "NO_SUCH_ENV_VAR_3",
                       "not writable", "allow_remote_data", "does not exist", "format must be", "http://",
                       "plain name", "unknown adapter", "needs an id"):
            self.assertIn(needle, probs)
        if __import__("importlib").util.find_spec("psycopg") is None:
            self.assertIn("psycopg", probs)
        self.assertIn(os.path.join(self.tmp, "missing"), probs)            # resolved against the config folder


# ---------------------------------------------------------------------------------------------- degraded
class Degraded(TempDirCase):
    def test_question_embedding_fails_words_still_answer(self):
        emb = FailingEmbedder()
        cfg = {"_config_dir": self.tmp, "index": {"dir": self.tmp},
               "adapters": {"notes": [{"id": "n", "folder": os.path.join(FIX, "notes")}]}}
        [shelf] = notes.from_config(cfg, emb)
        shelf.index.refresh()
        emb.fail = True
        c = ctx("derailleur cable")
        got = shelf.search(c)
        self.assertEqual(got[0]["section"], "Rear wheel")
        self.assertEqual(adapters.degraded(c), ["n"])


# ---------------------------------------------------------------------------------------------- stranger
class Stranger(TempDirCase):
    """Someone with only a notes folder and a chat export, no private code anywhere."""

    QUESTIONS = [
        ("When does Sam plant the heirloom tomatoes?", "notes", "Tomatoes"),
        ("Which train does Rae prefer for the coast?", "chats", "8:15"),
        ("How often does Rae turn the compost?", "notes", "Compost"),
    ]

    def test_recall_end_to_end(self):
        cfg = {"_config_dir": self.tmp, "index": {"dir": "index"},
               "adapters": {"notes": [{"id": "my-notes", "name": "My notes", "folder": os.path.join(FIX, "notes")}],
                            "chats": [{"id": "my-chats", "name": "Assistant chats",
                                       "path": os.path.join(FIX, "claude", "conversations.json"),
                                       "format": "claude"}]}}
        self.assertEqual(chk.check(cfg), [])
        shelves = adapters.load(cfg, embedder=embedders.HashEmbedder(256))
        self.assertEqual([s.name for s in shelves], ["my-notes", "my-chats"])
        summary = adapters.refresh_all(shelves)
        self.assertEqual(summary["my-notes"]["added"], 3)
        kinds = {}
        adapters.register_kinds(shelves, lambda ns, r: kinds.__setitem__(ns, r))
        self.assertEqual(set(kinds), {"my-notes", "my-chats"})
        for q, ns, want in self.QUESTIONS:
            top = rrf(shelves, q)[0]
            self.assertEqual(top["_ns"], ns.replace("notes", "my-notes").replace("chats", "my-chats"), q)
            if ns == "notes":
                self.assertEqual(top["section"], want, q)
                self.assertEqual(top["date"], "2025-04-12")
            else:
                # what recall SHOWS is the winner with its window — the answer line must be on screen
                # (same rule as the finding ruler); which neighbouring line leads is the adapter's own rank
                w = kinds["my-chats"](top, None)
                self.assertEqual(w["kind"], "item")
                self.assertTrue(any("8:15" in ln["message"] for ln in w["lines"]), q)
                self.assertEqual(w["source"], "Trip to the coast")
        # checked in a fresh interpreter: the shared test process may have imported anything
        code = ("import sys, json\n"
                "import adapters\n"
                "from adapters import embedders\n"
                f"cfg = {cfg!r}\n"
                "adapters.refresh_all(adapters.load(cfg, embedder=embedders.HashEmbedder(256)))\n"
                "print(json.dumps(sorted(sys.modules)))\n")
        out = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True, check=True)
        loaded = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertIn("adapters", loaded)
        self.assertFalse([m for m in loaded if m == "house" or m.startswith("house.")])
        self.assertNotIn("antechamber_recall", loaded)

    def test_refresher_runs_in_background(self):
        cfg = {"_config_dir": self.tmp, "index": {"dir": "index"},
               "adapters": {"notes": [{"id": "n", "folder": os.path.join(FIX, "notes")}]}}
        shelves = adapters.load(cfg, embedder=embedders.HashEmbedder(64))
        stop = adapters.start_refresher(shelves, minutes=60)
        self.addCleanup(stop)
        for _ in range(100):
            if shelves[0].index.count():
                break
            time.sleep(0.05)
        self.assertEqual(shelves[0].index.count(), 7)


class SecurityFixes(unittest.TestCase):
    """The three findings of the commit security review (14 Oct)."""
    def test_index_folder_and_files_are_owner_only(self):
        d = tempfile.mkdtemp()
        cfg = {"_config_dir": d, "index": {"dir": "idx"},
               "adapters": {"notes": [{"id": "n", "name": "N", "folder": os.path.join(FIX, "notes")}]}}
        shelves = adapters.load(cfg, embedder=embedders.HashEmbedder(64))
        adapters.refresh_all(shelves)
        idx = os.path.join(d, "idx")
        self.assertEqual(stat.S_IMODE(os.stat(idx).st_mode), 0o700)
        files = os.listdir(idx)
        self.assertTrue(files)
        for f in files:
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(idx, f)).st_mode) & 0o077, 0, f)

    def test_existing_loose_index_files_are_tightened(self):
        d = tempfile.mkdtemp()
        cfg = {"_config_dir": d, "index": {"dir": "idx"},
               "adapters": {"notes": [{"id": "n", "name": "N", "folder": os.path.join(FIX, "notes")}]}}
        shelves = adapters.load(cfg, embedder=embedders.HashEmbedder(64))
        adapters.refresh_all(shelves)
        idx = os.path.join(d, "idx")
        for f in os.listdir(idx):
            os.chmod(os.path.join(idx, f), 0o644)
        shelves = adapters.load(cfg, embedder=embedders.HashEmbedder(64))
        adapters.refresh_all(shelves)
        for f in os.listdir(idx):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(idx, f)).st_mode) & 0o077, 0, f)

    def test_chat_reader_and_hash_refuse_a_symlink_swapped_in_after_scan(self):
        from adapters import chats
        d = tempfile.mkdtemp(); outside = tempfile.mkdtemp()
        real = os.path.join(FIX, "claude", "conversations.json")
        target = os.path.join(outside, "other.json")
        shutil.copy(real, target)
        link = os.path.join(d, "conversations.json")
        os.symlink(target, link)
        with self.assertRaises(OSError):
            list(chats.make_reader("claude", 10_000_000)(link))
        with self.assertRaises(OSError):
            ix.file_sha256(link, chats.open_nofollow, 10_000_000)

    def test_notes_never_follow_a_symlinked_file(self):
        d = tempfile.mkdtemp(); outside = tempfile.mkdtemp()
        with open(os.path.join(outside, "secret.md"), "w") as f:
            f.write("# Secret\n\nThis text lives outside the notes folder and must never be read.\n")
        os.symlink(os.path.join(outside, "secret.md"), os.path.join(d, "link.md"))
        read = notes.make_reader(d, 2_000_000)
        self.assertEqual(read(os.path.join(d, "link.md")), [])

    def test_embedder_refuses_private_lan_but_allows_loopback(self):
        self.assertTrue(embedders._blocked("10.0.0.5"))
        with self.assertRaises(embedders.EmbedError):
            embedders.resolve_checked("10.0.0.5", 80, allow_local=False, allow_loopback=True)
        self.assertEqual(embedders.resolve_checked("127.0.0.1", 80, allow_local=False, allow_loopback=True), "127.0.0.1")


class ReviewFixes(TempDirCase):
    """The seven findings of the phase-3 code review (14 Oct)."""

    def serve(self, handler):
        s = FakeServer(handler)
        self.addCleanup(s.close)
        return s

    def write(self, path, text, mtime=None):
        with open(path, "w") as f:
            f.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))

    def test_same_size_same_mtime_edit_is_caught_by_sha(self):
        root = os.path.join(self.tmp, "n")
        os.mkdir(root)
        p = os.path.join(root, "a.md")
        self.write(p, "# A\n\nThe password for the shed is alpha, remember it.\n", 1000)
        idx = ix.Index(os.path.join(self.tmp, "i.sqlite"), {"root": root}, None,
                       lambda: notes.scan(root, "**/*.md", 100_000), notes.make_reader(root, 100_000))
        self.assertEqual(idx.refresh()["added"], 1)
        self.write(p, "# A\n\nThe password for the shed is bravo, remember it.\n", 1000)
        s = idx.refresh()
        self.assertEqual(s["changed"], 1)
        self.assertTrue(idx.keyword("bravo"))
        self.assertEqual(idx.keyword("alpha"), [])

    def test_changed_owner_hands_a_duplicate_id_to_the_next_file(self):
        d = os.path.join(self.tmp, "exp")
        os.mkdir(d)
        shared = {"uuid": "m-shared", "sender": "human", "text": "Shared note about the lighthouse keeper rota."}
        ma = {"uuid": "m-a", "sender": "assistant", "text": "Only export A mentions the harbour."}
        mb = {"uuid": "m-b", "sender": "assistant", "text": "Only export B mentions the orchard."}

        def export(path, msgs, mtime):
            with open(path, "w") as f:
                json.dump([{"uuid": "c-" + os.path.basename(path), "name": "T", "chat_messages": msgs}], f)
            os.utime(path, (mtime, mtime))
        a = os.path.join(d, "conversations-a.json")
        b = os.path.join(d, "conversations-b.json")
        export(a, [shared, ma], 1000)
        export(b, [shared, mb], 1000)
        cfg = {"_config_dir": self.tmp, "index": {"dir": "idx"},
               "adapters": {"chats": [{"id": "c", "path": "exp", "format": "claude"}]}}
        [shelf] = chats.from_config(cfg, None)
        idx = shelf.index
        idx.refresh()
        self.assertEqual(idx.rows(["m-shared"])[0]["path"], os.path.realpath(a))
        export(a, [ma], 2000)
        s = idx.refresh()
        self.assertEqual(s["changed"], 1)
        self.assertEqual([r["path"] for r in idx.rows(["m-shared"])], [os.path.realpath(b)])
        self.assertTrue(idx.keyword("lighthouse"))

    def test_fingerprint_names_the_whole_endpoint_and_settings(self):
        def fp(url, **kw):
            return embedders.OpenAIEmbedder(dict({"url": url, "model": "m", "dims": 4}, **kw)).fingerprint()
        base = fp("http://localhost:8080/v1")
        self.assertEqual(base, fp("http://LOCALHOST:8080/v1/"))
        for url in ("http://localhost:8080/other/v1", "http://localhost:9090/v1", "https://localhost:8080/v1"):
            self.assertNotEqual(fp(url), base, url)
        self.assertNotEqual(fp("http://localhost:8080/v1", send_dimensions=True), base)
        e1 = embedders.OpenAIEmbedder({"url": "http://localhost:8080/v1", "model": "m", "dims": 4})
        e2 = embedders.OpenAIEmbedder({"url": "http://localhost:8080/other/v1", "model": "m", "dims": 4})
        i1 = ix.Index(os.path.join(self.tmp, "a.sqlite"), {"r": 1}, e1, dict, lambda p: [])
        i2 = ix.Index(os.path.join(self.tmp, "b.sqlite"), {"r": 1}, e2, dict, lambda p: [])
        self.assertNotEqual(i1.fingerprint, i2.fingerprint)

    def test_reader_is_consumed_only_up_to_max_passages(self):
        p = os.path.join(self.tmp, "big.txt")
        self.write(p, "x")
        consumed = []

        def reader(path):
            for i in range(1000):
                consumed.append(i)
                yield {"id": f"p{i}", "text": f"passage number {i} about the lighthouse"}
        idx = ix.Index(os.path.join(self.tmp, "i.sqlite"), {"t": 1}, None, lambda: {p: (1.0, 1)}, reader,
                       max_passages=5)
        self.assertEqual(idx.refresh()["added"], 1)
        self.assertEqual(idx.count(), 5)
        self.assertLessEqual(len(consumed), 6)

    def test_chat_reader_enforces_the_byte_limit_on_the_opened_file(self):
        p = os.path.join(FIX, "claude", "conversations.json")
        size = os.path.getsize(p)
        with self.assertRaises(ValueError):
            list(chats.make_reader("claude", max_bytes=size - 1)(p))
        self.assertEqual(list(chats.make_reader("claude", max_bytes=size)(p)), list(chats.make_reader("claude")(p)))
        with open(p, "rb") as f:
            with self.assertRaises(ValueError):
                list(chats.iter_json_array(f, chunk=7, max_bytes=size - 1))

    def test_notes_reader_walks_every_folder_without_following_links(self):
        os.mkdir(os.path.join(self.tmp, "n"))
        root = os.path.realpath(os.path.join(self.tmp, "n"))
        outside = os.path.join(self.tmp, "outside")
        os.mkdir(outside)
        self.write(os.path.join(outside, "secret.md"),
                   "# Secret\n\nThis text lives outside the notes folder and must never be read.\n")
        os.symlink(outside, os.path.join(root, "sub"))
        read = notes.make_reader(root, 100_000)
        # the race: a folder is a link when the file is opened and looks real when checked
        with mock.patch.object(notes.os.path, "realpath", lambda p, *a, **k: p):
            self.assertEqual(read(os.path.join(root, "sub", "secret.md")), [])
        os.makedirs(os.path.join(root, "a", "b"))
        deep = os.path.join(root, "a", "b", "note.md")
        self.write(deep, "# Deep\n\nA real note two folders down, long enough to keep.\n")
        got = read(deep)
        self.assertEqual(got[0]["source"], "a/b/note.md")

    def test_openai_indices_must_be_each_position_once(self):
        a, b = [1.0, 0.0], [0.0, 1.0]
        bad = [
            [{"index": 0, "embedding": a}, {"index": 0, "embedding": b}],
            [{"index": 1, "embedding": a}, {"index": 2, "embedding": b}],
            [{"index": -1, "embedding": a}, {"index": 0, "embedding": b}],
            [{"embedding": a}, {"index": 1, "embedding": b}],
            [{"index": False, "embedding": a}, {"index": True, "embedding": b}],
            [{"index": "0", "embedding": a}, {"index": 1, "embedding": b}],
        ]
        for data in bad:
            s = self.serve(lambda p, b_, h, data=data: (200, {"data": data}, None))
            e = embedders.from_config({"embedder": {"backend": "openai", "url": s.url + "/v1", "model": "m",
                                                    "dims": 2}})
            with self.assertRaises(embedders.EmbedError):
                e.embed_batch(["a", "b"])

    def test_main_block_is_last(self):
        marker = "if __name__ == " + '"__main__":'
        with open(os.path.join(HERE, "test_adapters.py")) as f:
            src = f.read()
        self.assertEqual(src.count(marker), 1)
        tail = src[src.index(marker):]
        self.assertNotRegex(tail, r"\n(class |def |    def )")

    def test_refused_note_keeps_its_last_version(self):
        os.mkdir(os.path.join(self.tmp, "n"))
        root = os.path.realpath(os.path.join(self.tmp, "n"))
        outside = os.path.join(self.tmp, "outside")
        os.mkdir(outside)
        sec = os.path.join(outside, "secret.md")
        self.write(sec, "# S\n\nThe vault phrase is plover and must never be indexed.\n")
        p = os.path.join(root, "a.md")
        self.write(p, "# A\n\nThe orchard gate code is kestrel, keep it safe.\n")
        idx = ix.Index(os.path.join(self.tmp, "i.sqlite"), {"root": root}, None, lambda: {p: (1000.0, 10)},
                       notes.make_reader(root, 100_000, strict=True), opener=notes.make_opener(root))
        self.assertEqual(idx.refresh()["added"], 1)
        os.remove(p)
        os.symlink(sec, p)
        with self.assertRaises(OSError):
            notes.make_opener(root)(p)
        s = idx.refresh()
        self.assertEqual(s["failed"], 1)
        self.assertTrue(idx.keyword("kestrel"))
        self.assertEqual(idx.keyword("plover"), [])
        self.assertEqual(notes.make_reader(root, 100_000)(p), [])

    def test_sha_reads_at_most_the_limit_plus_one(self):
        p = os.path.join(self.tmp, "big.bin")
        with open(p, "wb") as f:
            f.write(b"x" * 100)
        reads = []

        class Spy:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def read(self, n=-1):
                data = self.f.read(n)
                reads.append(len(data))
                return data
        spy = lambda path: Spy(open(path, "rb"))
        with self.assertRaises(OSError):
            ix.file_sha256(p, spy, 10)
        self.assertLessEqual(sum(reads), 11)
        reads.clear()
        self.assertEqual(ix.file_sha256(p, spy, 100), ix.file_sha256(p))
        # an over-limit file is skipped, counted failed and logged, never hashed whole
        idx = ix.Index(os.path.join(self.tmp, "i.sqlite"), {"t": 1}, None, lambda: {p: (1.0, 100)},
                       lambda path: [{"id": "p1", "text": "a passage about the lighthouse"}], max_bytes=10)
        with self.assertLogs(ix.log, level="WARNING"):
            s = idx.refresh()
        self.assertEqual((s["failed"], s["added"]), (1, 0))

    def test_removed_owner_new_file_earliest_path_wins(self):
        d = os.path.join(self.tmp, "exp")
        os.mkdir(d)
        shared = {"uuid": "m-shared", "sender": "human", "text": "Shared note about the lighthouse keeper rota."}
        other = {"uuid": "m-x", "sender": "assistant", "text": "Filler message about something else entirely."}

        def export(name, msgs):
            path = os.path.join(d, name)
            with open(path, "w") as f:
                json.dump([{"uuid": "c-" + name, "name": "T", "chat_messages": msgs}], f)
            os.utime(path, (1000, 1000))
            return path
        a = export("conversations-a.json", [shared])
        b = export("conversations-b.json", [shared, other])
        cfg = {"_config_dir": self.tmp, "index": {"dir": "idx"},
               "adapters": {"chats": [{"id": "c", "path": "exp", "format": "claude"}]}}
        [shelf] = chats.from_config(cfg, None)
        idx = shelf.index
        idx.refresh()
        self.assertEqual(idx.rows(["m-shared"])[0]["path"], os.path.realpath(a))
        os.remove(a)
        c = export("conversations-c.json", [shared])
        idx.refresh()
        self.assertEqual([r["path"] for r in idx.rows(["m-shared"])], [os.path.realpath(b)])
        self.assertTrue(idx.keyword("lighthouse"))
        idx.refresh()   # and it stays put
        self.assertEqual([r["path"] for r in idx.rows(["m-shared"])], [os.path.realpath(b)])
        self.assertTrue(os.path.exists(c))


if __name__ == "__main__":
    unittest.main()
