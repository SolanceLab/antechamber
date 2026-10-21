# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The memories adapter: a writer's memory files searched like any shelf; a winning memory shows its source lines.
Run: python3 -m unittest test_memories_adapter -v   (from engine/)."""
import json
import logging
import os
import shutil
import tempfile
import unittest

from adapters import check as chk, embedders, item, memories
from shelves import Ctx

logging.getLogger("antechamber.adapters").setLevel(logging.CRITICAL)

EXPORT = [{
    "uuid": "conv-1", "name": "3:20 pm on 14 March, 2022", "created_at": "2022-03-14T15:20:00Z",
    "chat_messages": [
        {"uuid": "D1:1", "sender": "human", "created_at": "2022-03-14T15:20:00Z",
         "text": "Priya: I finally signed up for the pottery class at the community centre."},
        {"uuid": "D1:2", "sender": "assistant", "created_at": "2022-03-14T15:21:00Z",
         "text": "Tom: I am moving to Leeds in May for the new job."},
    ]}]
MEMS = {"conversation_id": "conv-1", "title": "3:20 pm on 14 March, 2022", "session_date": "2022-03-14",
        "memories": [
            {"topic": "Creative", "summary": "Priya signed up for a pottery class.",
             "excerpt": "I finally signed up for the pottery class", "source_line_ids": ["D1:1"],
             "salience": 0.7, "valence": 0.6},
            {"topic": "Work", "summary": "Tom is relocating to Leeds for a new job in May.",
             "source_line_ids": ["D1:2"]},                       # a generic memory: no excerpt, same plumbing
            {"topic": "Leisure", "excerpt": "no summary here"},   # skipped: a memory needs a summary
        ]}


def ctx(q):
    return Ctx(None, q, lambda: None)


class Memories(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.mkdir(os.path.join(self.tmp, "mem"))
        with open(os.path.join(self.tmp, "mem", "conv-1.json"), "w") as f:
            json.dump(MEMS, f)
        with open(os.path.join(self.tmp, "conversations.json"), "w") as f:
            json.dump(EXPORT, f)

    def shelf(self, export=True):
        inst = {"id": "m", "folder": "mem", "redundant_with": ["chats"]}
        if export:
            inst["export"] = "conversations.json"
        cfg = {"_config_dir": self.tmp, "index": {"dir": "idx"}, "adapters": {"memories": [inst]}}
        [s] = memories.from_config(cfg, embedders.HashEmbedder(64))
        s.index.refresh()
        return s

    def test_one_item_per_memory_with_summary_and_dated_by_session(self):
        s = self.shelf()
        self.assertEqual(s.index.count(), 2)
        self.assertEqual(s.redundant_with, ["chats"])
        hit = s.search(ctx("pottery class"))[0]
        self.assertTrue(hit["id"].startswith("conv-1/"))
        self.assertEqual(hit["date"], "2022-03-14")
        self.assertIn("Creative: Priya signed up for a pottery class.", hit["text"])
        self.assertEqual(hit["metadata"]["source_line_ids"], ["D1:1"])

    def test_a_winning_memory_shows_its_source_lines_word_for_word(self):
        hit = self.shelf().search(ctx("pottery class"))[0]
        self.assertEqual(hit["lines"], [{"id": "D1:1", "sender": "user", "hit": True,
                                         "message": EXPORT[0]["chat_messages"][0]["text"]}])
        shown = item.render_item(hit)
        self.assertIn("I finally signed up for the pottery class at the community centre.",
                      " ".join(ln["message"] for ln in shown["lines"]))

    def test_a_generic_memory_goes_through_the_same_path(self):
        hit = self.shelf().search(ctx("Leeds job"))[0]
        self.assertIn("Leeds", hit["text"])
        self.assertEqual(hit["lines"][0]["message"], EXPORT[0]["chat_messages"][1]["text"])

    def test_without_an_export_memories_are_still_searchable(self):
        hit = self.shelf(export=False).search(ctx("pottery class"))[0]
        self.assertIn("pottery", hit["text"])
        self.assertNotIn("lines", hit)

    def test_salience_and_valence_are_carried_not_ranked(self):
        s = self.shelf()
        hit = s.search(ctx("pottery class"))[0]
        self.assertEqual(hit["metadata"]["salience"], 0.7)
        self.assertLess(abs(s.weight(hit, ctx("pottery class")) - 1.0), 1e-5)   # only the exact-tie nudge

    def _write(self, name, obj):
        with open(os.path.join(self.tmp, name), "w") as f:
            json.dump(obj, f)

    def test_ids_do_not_move_when_a_memory_is_added_before_it(self):
        s = self.shelf()
        before = s.search(ctx("pottery class"))[0]["id"]
        doc = json.loads(json.dumps(MEMS))
        doc["memories"].insert(0, {"topic": "Home", "summary": "Priya repainted her kitchen.", "source_line_ids": []})
        self._write("mem/conv-1.json", doc)
        s.index.refresh()
        self.assertEqual(s.search(ctx("pottery class"))[0]["id"], before)

    def test_editing_the_export_refreshes_the_shown_lines(self):
        s = self.shelf()
        exp = json.loads(json.dumps(EXPORT))
        exp[0]["chat_messages"][0]["text"] = "Priya: I signed up for the evening pottery class after all."
        self._write("conversations.json", exp)
        os.utime(os.path.join(self.tmp, "conversations.json"), ns=(1, 2_000_000_000_000_000_000))
        s.index.refresh()
        hit = s.search(ctx("pottery class"))[0]
        self.assertIn("evening pottery class", hit["lines"][0]["message"])

    def test_an_export_folder_refreshes_when_a_file_inside_changes(self):
        os.mkdir(os.path.join(self.tmp, "exp"))
        self._write("exp/conversations.json", EXPORT)
        cfg = {"_config_dir": self.tmp, "index": {"dir": "idx2"},
               "adapters": {"memories": [{"id": "m2", "folder": "mem", "export": "exp"}]}}
        [s] = memories.from_config(cfg, embedders.HashEmbedder(64))
        s.index.refresh()
        exp = json.loads(json.dumps(EXPORT))
        exp[0]["chat_messages"][0]["text"] = "Priya: The pottery class moved to Fridays."
        self._write("exp/conversations.json", exp)
        os.utime(os.path.join(self.tmp, "exp", "conversations.json"), ns=(1, 2_000_000_000_000_000_000))
        s.index.refresh()
        self.assertIn("Fridays", s.search(ctx("pottery class"))[0]["lines"][0]["message"])

    def test_a_malformed_file_is_skipped_and_odd_fields_count_as_absent(self):
        self._write("mem/bad.json", {"conversation_id": "x", "memories": 7})
        self._write("mem/odd.json", {"conversation_id": "conv-1", "title": {"no": 1}, "memories": [
            {"summary": "Priya likes clay sculpture a lot.", "source_line_ids": "D1:1", "emotions": "happy"}]})
        s = self.shelf()
        hit = s.search(ctx("clay sculpture"))[0]
        self.assertEqual(hit["metadata"]["source_line_ids"], [])     # a string is not a list of ids
        self.assertEqual(hit["metadata"]["emotions"], [])
        self.assertNotIn("lines", hit)

    def test_an_exact_duplicate_is_dropped_and_bools_are_not_numbers(self):
        doc = json.loads(json.dumps(MEMS))
        doc["memories"].append(dict(doc["memories"][0]))
        doc["memories"][1]["salience"] = True
        self._write("mem/conv-1.json", doc)
        s = self.shelf()
        self.assertEqual(s.index.count(), 2)
        hit = s.search(ctx("Leeds job"))[0]
        self.assertIsNone(hit["metadata"]["salience"])

    def test_two_memories_that_differ_only_in_topic_are_both_kept(self):
        doc = json.loads(json.dumps(MEMS))
        doc["memories"].append(dict(doc["memories"][0], topic="Leisure"))
        self._write("mem/conv-1.json", doc)
        self.assertEqual(self.shelf().index.count(), 3)

    def test_a_half_read_export_file_adds_nothing(self):
        os.mkdir(os.path.join(self.tmp, "exp"))
        self._write("exp/conversations.json", EXPORT)
        wrong = json.loads(json.dumps(EXPORT))
        wrong[0]["chat_messages"][0]["text"] = "Priya: WRONG TEXT"
        with open(os.path.join(self.tmp, "exp", "conversations-a.json"), "w") as f:   # sorts first
            f.write(json.dumps(wrong)[:-1] + ', {"uuid": "x", "chat_messages": [')             # then truncated
        cfg = {"_config_dir": self.tmp, "index": {"dir": "idx5"},
               "adapters": {"memories": [{"id": "m5", "folder": "mem", "export": "exp"}]}}
        [s] = memories.from_config(cfg, embedders.HashEmbedder(64))
        s.index.refresh()
        self.assertNotIn("WRONG", s.search(ctx("pottery class"))[0]["lines"][0]["message"])

    def test_an_unreadable_export_file_does_not_block_the_others(self):
        os.mkdir(os.path.join(self.tmp, "exp"))
        self._write("exp/conversations.json", EXPORT)
        os.symlink("/etc/hosts", os.path.join(self.tmp, "exp", "conversations-link.json"))
        with open(os.path.join(self.tmp, "exp", "conversations-bad.json"), "w") as f:
            f.write("{not json")
        cfg = {"_config_dir": self.tmp, "index": {"dir": "idx3"},
               "adapters": {"memories": [{"id": "m3", "folder": "mem", "export": "exp"}]}}
        [s] = memories.from_config(cfg, embedders.HashEmbedder(64))
        s.index.refresh()
        self.assertIn("pottery", s.search(ctx("pottery class"))[0]["lines"][0]["message"])

    def test_the_export_size_limit_is_part_of_the_fingerprint(self):
        def shelf(limit):
            cfg = {"_config_dir": self.tmp, "index": {"dir": "idx4"}, "adapters": {"memories": [
                {"id": "m4", "folder": "mem", "export": "conversations.json", "max_export_bytes": limit}]}}
            [sh] = memories.from_config(cfg, embedders.HashEmbedder(64))
            sh.index.refresh()
            return sh
        self.assertNotIn("lines", shelf(10).search(ctx("pottery class"))[0])          # export too big to read
        self.assertIn("lines", shelf(10_000_000).search(ctx("pottery class"))[0])     # same files, bigger limit

    def test_a_card_gathering_many_conversations_keeps_every_real_line(self):
        # a People card names no single conversation; its lines are found across the export when the id is unique
        export = EXPORT + [
            {"uuid": "conv-2", "name": "2:00 pm on 2 May, 2022", "created_at": "2022-05-02T14:00:00Z",
             "chat_messages": [{"uuid": "D2:1", "sender": "human", "created_at": "2022-05-02T14:00:00Z",
                                "text": "Priya: My second pottery piece is a blue bowl for my sister."}]},
            {"uuid": "conv-3", "name": "1:00 pm on 3 May, 2022", "created_at": "2022-05-03T13:00:00Z",
             "chat_messages": [{"uuid": "D1:1", "sender": "human", "created_at": "2022-05-03T13:00:00Z",
                                "text": "A different line that reuses an id."}]}]
        with open(os.path.join(self.tmp, "conversations.json"), "w") as f:
            json.dump(export, f)
        card = {"conversation_id": "person:priya", "title": "Priya", "memories": [
            {"topic": "Priya", "summary": "Priya makes pottery: a class in March, a blue bowl for her sister in May.",
             "source_line_ids": ["D2:1", "D1:1"], "source_session_date": "2022-05-02"}]}
        with open(os.path.join(self.tmp, "mem", "person-priya.json"), "w") as f:
            json.dump(card, f)
        s = self.shelf()
        hit = next(h for h in s.search(ctx("Priya pottery blue bowl sister")) if h["id"].startswith("person:priya"))
        # D2:1 is unique across the export → shown; D1:1 now exists in two conversations → never guessed
        self.assertEqual([ln["id"] for ln in hit["lines"]], ["D2:1"])
        self.assertEqual(hit["lines"][0]["message"], export[1]["chat_messages"][0]["text"])
        # an ordinary memory still takes its line from its OWN conversation, even though D1:1 is now ambiguous
        own = next(h for h in s.search(ctx("pottery class")) if h["id"].startswith("conv-1/"))
        self.assertEqual(own["lines"][0]["message"], EXPORT[0]["chat_messages"][0]["text"])

    def test_a_real_conversation_never_borrows_a_line_and_a_partial_export_disables_the_fallback(self):
        export = EXPORT + [{"uuid": "conv-2", "name": "2 May", "created_at": "2022-05-02T14:00:00Z",
                            "chat_messages": [{"uuid": "D2:1", "sender": "human", "created_at": "2022-05-02T14:00:00Z",
                                               "text": "Priya: a blue bowl for my sister."}]}]
        os.remove(os.path.join(self.tmp, "conversations.json"))
        os.mkdir(os.path.join(self.tmp, "exp"))
        with open(os.path.join(self.tmp, "exp", "conversations-a.json"), "w") as f:
            json.dump(export, f)
        mems = dict(MEMS, memories=[{"topic": "Creative", "summary": "Priya made a bowl.",
                                     "source_line_ids": ["D1:1", "D2:1"]}])     # D2:1 is NOT in conv-1
        with open(os.path.join(self.tmp, "mem", "conv-1.json"), "w") as f:
            json.dump(mems, f)
        card = {"conversation_id": "person:priya", "memories": [
            {"topic": "Priya", "summary": "Priya made a blue bowl for her sister.", "source_line_ids": ["D2:1"]}]}
        with open(os.path.join(self.tmp, "mem", "person-priya.json"), "w") as f:
            json.dump(card, f)
        def shelf():
            inst = {"id": "m", "folder": "mem", "export": "exp"}
            cfg = {"_config_dir": self.tmp, "index": {"dir": "idx2"}, "adapters": {"memories": [inst]}}
            [s] = memories.from_config(cfg, embedders.HashEmbedder(64))
            s.index.refresh()
            return s
        hits = {h["id"].split("/")[0]: h for h in shelf().search(ctx("Priya bowl sister"))}
        self.assertEqual([ln["id"] for ln in hits["conv-1"]["lines"]], ["D1:1"])     # never borrowed from conv-2
        self.assertEqual([ln["id"] for ln in hits["person:priya"]["lines"]], ["D2:1"])  # the card still finds it
        with open(os.path.join(self.tmp, "exp", "conversations-b.json"), "w") as f:
            f.write("[{\"uuid\": \"conv-9\", \"chat_messages\": [")                  # an unreadable export file
        shutil.rmtree(os.path.join(self.tmp, "idx2"))
        hits = {h["id"].split("/")[0]: h for h in shelf().search(ctx("Priya bowl sister"))}
        self.assertNotIn("lines", hits["person:priya"])                             # uniqueness unprovable: none

    def test_check_reports_bad_config(self):
        probs = chk.check({"_config_dir": self.tmp, "index": {"dir": "idx"},
                           "adapters": {"memories": [{"id": "m", "folder": "nope", "redundant_with": "chats"}]}})
        text = " ".join(probs)
        self.assertIn("folder", text)
        self.assertIn("redundant_with", text)


if __name__ == "__main__":
    unittest.main()
