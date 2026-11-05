# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Tests for the codemap adapter (adapters/codemap.py): matching, graph contract, refresh, registration."""
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import adapters
from adapters import check as chk, codemap

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "codemap-graph.json")
RECEIPT = {"nodes": [{"id": "receipt_print", "label": "print_receipt()", "source_file": "billing/receipt.py",
                      "source_location": "L2"}], "links": []}


def nd(i, label, f="a.py", loc="L1"):
    return {"id": i, "label": label, "source_file": f, "source_location": loc}


class CodeMapAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.graph = os.path.join(self.tmp.name, "graph.json")
        shutil.copy(FIX, self.graph)

    def write(self, obj):
        with open(self.graph, "w") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))
        st = os.stat(self.graph)
        os.utime(self.graph, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))

    def shelf(self, **extra):
        cfg = {"adapters": {"codemap": [{"id": "code", "name": "My code", "graph": self.graph, **extra}]}}
        return codemap.from_config(cfg)[0]

    def q(self, s, text, pool=20):
        return s.search(types.SimpleNamespace(q=text, pool=pool))

    def test_fields_and_neighbours(self):
        items = self.q(self.shelf(), "build invoice")
        top = items[0]
        self.assertEqual((top["id"], top["title"], top["source"], top["_ns"]),
                         ("billing_invoice_build_invoice", "build_invoice()", "My code", "code"))
        self.assertEqual(top["metadata"]["path"], "billing/invoice.py")
        self.assertEqual(top["metadata"]["line"], "L12")
        self.assertEqual(top["text"], "build_invoice() — billing/invoice.py:L12 · calls: add_line(), compute_tax() "
                                      "· called by: send_invoice()")
        self.assertNotIn("garden_water_plants", [i["id"] for i in items])
        add = [i for i in items if i["id"] == "billing_invoice_add_line"] or self.q(self.shelf(), "add_line")
        self.assertTrue(add[0]["text"].endswith("called by: build_invoice()"))
        self.assertNotIn("calls:", add[0]["text"])

    def test_no_terms_no_match_and_caps(self):
        s = self.shelf()
        self.assertEqual(self.q(s, "the and"), [])
        self.assertEqual(self.q(s, "zebra"), [])
        self.assertEqual(len(self.q(self.shelf(limit=2), "billing")), 2)
        self.assertEqual(len(self.q(s, "billing", pool=3)), 3)

    def test_neighbours_capped_at_five(self):
        nodes = [nd("hub", "hub()")] + [nd(f"c{i}", f"c{i}()") for i in range(7)]
        links = [{"source": "hub", "target": f"c{i}", "relation": "calls"} for i in range(7)]
        self.write({"nodes": nodes, "links": links})
        self.assertEqual(self.q(self.shelf(), "hub")[0]["text"].count("()"), 6)

    def test_graph_contract_edges(self):
        nodes = [nd("x", "first()"), nd("x", "second()"), "junk", nd(True, "boolid()"),
                 {"id": "y", "label": "yonder()", "source_file": "", "source_location": 7}]
        links = [{"source": "x", "target": "nowhere", "relation": "calls"},
                 {"source": "x", "target": "x", "relation": "calls"}, "junk"]
        self.write({"nodes": nodes, "links": links})
        s = self.shelf()
        items = self.q(s, "first second")
        self.assertEqual([i["id"] for i in items], ["x"])
        self.assertEqual(items[0]["title"], "first()")
        self.assertNotIn("calls:", items[0]["text"])
        self.assertNotIn("called by:", items[0]["text"])
        self.assertEqual(self.q(s, "yonder")[0]["text"], "yonder()")
        self.assertEqual(self.q(s, "boolid"), [])
        self.write({"nodes": [], "links": []})
        self.assertEqual(self.q(self.shelf(), "invoice"), [])

    def test_edges_key_accepted(self):
        self.write({"nodes": [nd("a", "alpha()"), nd("b", "beta()")],
                    "edges": [{"source": "a", "target": "b", "relation": "calls"}]})
        self.assertIn("calls: beta()", self.q(self.shelf(), "alpha")[0]["text"])

    def test_src_tgt_direction_honoured(self):
        self.write({"nodes": [nd("a", "alpha()"), nd("b", "beta()")],
                    "links": [{"source": "a", "target": "b", "_src": "b", "_tgt": "a", "relation": "calls"}]})
        s = self.shelf()
        self.assertIn("calls: alpha()", self.q(s, "beta")[0]["text"])
        self.assertIn("called by: beta()", self.q(s, "alpha")[0]["text"])

    def test_refresh_reloads_when_changed(self):
        s = self.shelf()
        self.write(RECEIPT)
        self.assertEqual(s.index.refresh(), {"nodes": 1, "reloaded": True})
        self.assertEqual(self.q(s, "receipt")[0]["id"], "receipt_print")
        self.assertEqual(self.q(s, "invoice"), [])

    def test_refresh_unchanged_does_not_parse(self):
        s = self.shelf()
        with mock.patch.object(codemap, "parse_graph", wraps=codemap.parse_graph) as p:
            self.assertFalse(s.index.refresh()["reloaded"])
            self.assertEqual(p.call_count, 0)

    def test_malformed_replacement_keeps_last_good_then_recovers(self):
        s = self.shelf()
        self.write("not json")
        with self.assertRaises(codemap.CodeMapError):
            s.index.refresh()
        self.assertEqual(self.q(s, "build invoice")[0]["id"], "billing_invoice_build_invoice")
        self.write(RECEIPT)
        s.index.refresh()
        self.assertEqual(self.q(s, "receipt")[0]["id"], "receipt_print")

    def test_graph_gone_raises_and_keeps_answering(self):
        s = self.shelf()
        os.remove(self.graph)
        with self.assertRaises(codemap.CodeMapError) as cm:
            s.index.refresh()
        self.assertIn("is gone", str(cm.exception))
        self.assertEqual(self.q(s, "build invoice")[0]["id"], "billing_invoice_build_invoice")
        res = adapters.refresh_all([s])
        self.assertIn("error", res["code"])

    def test_missing_or_invalid_graph_is_a_clear_error(self):
        def build(inst):
            return codemap.from_config({"adapters": {"codemap": [inst]}})
        with self.assertRaises(codemap.CodeMapError) as cm:
            build({"id": "code", "graph": os.path.join(self.tmp.name, "nope.json")})
        self.assertIn("[[adapters.codemap]] code", str(cm.exception))
        with self.assertRaises(codemap.CodeMapError):
            build({"id": "code"})
        for bad in ("not json", '{"nodes": []}', "[]"):
            self.write(bad)
            with self.assertRaises(codemap.CodeMapError):
                build({"id": "code", "graph": self.graph})

    def test_check_validation(self):
        ok = {"_config_dir": self.tmp.name, "adapters": {"codemap": [{"id": "code", "graph": self.graph}]}}
        self.assertEqual(chk.check(ok), [])
        bad = {"_config_dir": self.tmp.name, "adapters": {"codemap": [{"id": "a"}, {"id": "b", "graph": "missing.json"}]}}
        joined = "\n".join(chk.check(bad))
        self.assertIn("graph (the path", joined)
        self.assertIn(os.path.join(self.tmp.name, "missing.json"), joined)
        self.assertNotIn("unknown adapter", joined)

    def test_registered_in_load(self):
        shelves = adapters.load({"adapters": {"codemap": [{"id": "code", "graph": self.graph}]}})
        self.assertEqual([x.name for x in shelves], ["code"])
        self.assertTrue(hasattr(shelves[0].index, "refresh") and hasattr(shelves[0].index, "current"))
        self.assertIn("codemap", chk.KINDS)


if __name__ == "__main__":
    unittest.main()
