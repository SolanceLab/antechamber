# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The engine names nothing of the House (split S9): no private words, no House tables, no import of the House
layer. The private-word rule is shared/release/release_check.py's own, applied to engine/ in CI.
Run: python3 -m unittest test_generic -v   (from engine/)"""
import os, re, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RELEASE_CHECK = os.path.join(HERE, "..", "..", "..", "shared", "release", "release_check.py")


def private_words():
    """The release check's own private-word rule, read from it (so this file never spells the words out).
    None where the release check is not beside the engine (a published copy)."""
    if not os.path.exists(RELEASE_CHECK):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_check", RELEASE_CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PRIVATE


HOUSE_IMPORT = re.compile(r"^\s*(import house\b|from house\b)", re.M)
HOUSE_TABLES = re.compile(r"\b(memory_nodes|memory_edges|conversation_embeddings|conversation_labels|message_chunks|"
                          r"journal_chunks|people_local|house_rules|doc_chunks|vocab_words|sync_meta|recall_log|"
                          r"recall_shown|import_ledger|local_recall)\b")


def engine_files():
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
        for f in files:
            if f.endswith(".py") and os.path.join(root, f) != os.path.abspath(__file__):   # this file names the tables it bans
                yield os.path.join(root, f)


class Generic(unittest.TestCase):
    def test_no_house_words_tables_or_imports_in_the_engine(self):
        private = private_words()
        rules = [(HOUSE_TABLES, "House table"), (HOUSE_IMPORT, "House import")] + ([(private, "private word")] if private else [])
        bad = []
        for path in engine_files():
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    for rx, what in rules:
                        m = rx.search(line)
                        if m:
                            bad.append(f"{os.path.relpath(path, HERE)}:{i}: {what} {m.group(0)!r}")
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_the_rule_catches_what_it_should(self):
        private = private_words()
        if private is None:
            self.skipTest("no release check beside this engine")
        first_word = re.search(r"\(([a-z]+)\|", private.pattern).group(1)   # the rule's own first private word
        self.assertTrue(private.search(f"ask {first_word.title()} about it"))
        self.assertFalse(private.search("channel"))
        self.assertTrue(HOUSE_TABLES.search("FROM sync_meta"))
        self.assertTrue(HOUSE_IMPORT.search("import house.adapters"))


if __name__ == "__main__":
    unittest.main()
