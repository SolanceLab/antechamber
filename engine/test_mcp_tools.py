# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""search_store and open_item: tool list, one-store search, safe whole-item opening, remote-safe errors."""
import os
import tempfile
import unittest
from unittest import mock

import antechamber_recall
import mcp_http as M
from adapters.notes import make_open_note
from shelves import FnShelf


def call(name, args, remote=False):
    r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, remote)
    res = r["result"]
    return res.get("isError", False), res["content"][0]["text"]


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "sub"))
        with open(os.path.join(root, "sub", "a.md"), "w") as f:
            f.write("# A\n\nfull note text")
        with open(os.path.join(root, "project#1.md"), "w") as f:
            f.write("hash in the name")
        with open(os.path.join(root, "long.md"), "w") as f:
            f.write("x" * 30000)
        with open(os.path.join(root, "secret.txt"), "w") as f:
            f.write("not markdown")
        notes = FnShelf("notes", lambda ctx: [], "notes", "id")
        notes.open = make_open_note(root, "**/*.md", 2_000_000)
        plain = FnShelf("plain", lambda ctx: [], "plain", "id")
        p = mock.patch.dict(antechamber_recall.SHELVES, {"notes": notes, "plain": plain}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_list_has_three_tools_with_required(self):
        r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in r["result"]["tools"]}
        self.assertEqual(list(tools), ["recall", "search_store", "open_item"])
        self.assertEqual(tools["search_store"]["inputSchema"]["required"], ["store", "query"])
        self.assertEqual(tools["open_item"]["inputSchema"]["required"], ["store", "id"])

    def test_search_store_limits_to_one_shelf(self):
        with mock.patch.object(antechamber_recall, "recall", return_value={"query": "q", "winners": []}) as rc:
            err, text = call("search_store", {"store": "notes", "query": "q"})
        self.assertFalse(err)
        self.assertEqual(rc.call_args.kwargs["shelves"], ["notes"])

    def test_search_store_rejects_unknown_store(self):
        err, text = call("search_store", {"store": "nope", "query": "q"})
        self.assertTrue(err)
        self.assertIn("notes", text)
        self.assertIn("plain", text)

    def test_open_item_full_text(self):
        err, text = call("open_item", {"store": "notes", "id": "sub/a.md#0123456789ab"})
        self.assertFalse(err)
        self.assertIn("full note text", text)

    def test_open_item_file_name_with_hash(self):
        err, text = call("open_item", {"store": "notes", "id": "project#1.md#0123456789ab"})
        self.assertFalse(err)
        self.assertIn("hash in the name", text)
        err, _ = call("open_item", {"store": "notes", "id": "sub/a.md"})   # no digest suffix
        self.assertTrue(err)

    def test_chat_open_stops_reading_past_the_cap(self):
        from adapters import chats
        pages = []

        class Idx:
            def rows(self, ids):
                return [{"thread": 1, "title": "T"}]

            def window(self, thread, pos, before=2, after=3):
                pages.append(pos)
                return [{"sender": "a", "text": "y" * 1000, "pos": pos + i} for i in range(after + 1)]
        text = chats.make_open(Idx())("m1")
        self.assertLess(len(pages), 5)
        self.assertGreater(len(text), 20000)

    def test_open_item_refuses_escapes(self):
        for bad in ("../x.md", "/etc/passwd", "sub/../../x.md", "secret.txt", ".hidden/a.md", "sub//a.md"):
            err, text = call("open_item", {"store": "notes", "id": bad})
            self.assertTrue(err, bad)
            self.assertNotIn(self.tmp.name, text)

    def test_open_item_caps_long_text(self):
        err, text = call("open_item", {"store": "notes", "id": "long.md#0123456789ab"})
        self.assertFalse(err)
        self.assertTrue(text.endswith("[cut at 20000 characters]"))
        self.assertLess(len(text), 20100)

    def test_open_item_store_without_open(self):
        err, text = call("open_item", {"store": "plain", "id": "1"})
        self.assertTrue(err)
        self.assertIn("cannot open items", text)

    def test_remote_errors_hide_paths(self):
        boom = FnShelf("boom", lambda ctx: [], "boom", "id")
        boom.open = mock.Mock(side_effect=ValueError(f"{self.tmp.name}/x is private"))
        with mock.patch.dict(antechamber_recall.SHELVES, {"boom": boom}):
            _, remote_text = call("open_item", {"store": "boom", "id": "1"}, remote=True)
        self.assertNotIn(self.tmp.name, remote_text)
        with mock.patch.object(antechamber_recall, "recall", side_effect=RuntimeError(self.tmp.name)):
            _, t = call("search_store", {"store": "notes", "query": "q"}, remote=True)
        self.assertNotIn(self.tmp.name, t)

    def test_search_result_shows_store_and_id_and_opens(self):
        import re
        from adapters.item import make_item, render_item
        pid = "sub/a.md#" + "a" * 12
        it = dict(make_item({"id": pid, "text": "hit"}), _ns="notes")
        out = {"query": "q", "winners": [render_item(it)]}
        with mock.patch.object(antechamber_recall, "recall", return_value=out):
            _, text = call("search_store", {"store": "notes", "query": "q"})
        m = re.search(r"\[store: (\S+) \| id: (\S+)\]", text)
        self.assertEqual(m.groups(), ("notes", pid))
        err, full = call("open_item", {"store": m.group(1), "id": m.group(2)})
        self.assertFalse(err)
        self.assertIn("full note text", full)

    def test_open_duplicate_passage_id(self):
        err, text = call("open_item", {"store": "notes", "id": "sub/a.md#" + "b" * 12 + "-3"})
        self.assertFalse(err)
        self.assertIn("full note text", text)

    def test_open_vaultmap_style_id(self):
        v = FnShelf("vm", lambda ctx: [], "vm", "id")
        from adapters.vaultmap import split_vault_id
        v.open = make_open_note(self.tmp.name, "**/*.md", 2_000_000, split=split_vault_id)
        with mock.patch.dict(antechamber_recall.SHELVES, {"vm": v}):
            for i in ("sub/a.md#", "sub/a.md#Some heading", "project#1.md#Sec"):
                err, text = call("open_item", {"store": "vm", "id": i})
                self.assertFalse(err, i)
            err, _ = call("open_item", {"store": "vm", "id": "../x.md#s"})
            self.assertTrue(err)

    def test_vault_id_prefers_the_longest_file_name(self):
        # Codex review 5 Nov: with foo.md AND foo.md#bar.md present, 'foo.md#bar.md#Sec' must open foo.md#bar.md.
        from adapters.vaultmap import split_vault_id
        with open(os.path.join(self.tmp.name, "foo.md"), "w") as fh:
            fh.write("SHORT")
        with open(os.path.join(self.tmp.name, "foo.md#bar.md"), "w") as fh:
            fh.write("LONG")
        v = FnShelf("vm2", lambda ctx: [], "vm2", "id")
        v.open = make_open_note(self.tmp.name, "**/*.md", 2_000_000, split=split_vault_id)
        with mock.patch.dict(antechamber_recall.SHELVES, {"vm2": v}):
            err, text = call("open_item", {"store": "vm2", "id": "foo.md#bar.md#Sec"})
        self.assertFalse(err)
        self.assertIn("LONG", text)
        self.assertNotIn("SHORT", text)

    def test_search_store_is_solo(self):
        with mock.patch.object(antechamber_recall, "recall", return_value={"query": "q", "winners": []}) as rc:
            call("search_store", {"store": "notes", "query": "q"})
        self.assertTrue(rc.call_args.kwargs["solo"])

    def test_recall_solo_skips_riding_shelves(self):
        rider = FnShelf("r", lambda c: [], "note", "path", default=False, rides_with="notes", active=lambda: True)
        with mock.patch.dict(antechamber_recall.SHELVES, {"r": rider}):
            out = antechamber_recall.recall("anything here", shelves=["notes"], solo=True)
            self.assertEqual(list(out["shelves"]), ["notes"])


if __name__ == "__main__":
    unittest.main()
