# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""The MCP endpoint: off without a key, refuses a wrong key, and answers initialize / tools/list / tools/call."""
import json
import os
import threading
import unittest
import urllib.request
import urllib.error

import antechamber_recall
import recall_service as R
import mcp_http as M

M.register(R)


def post(port, body, key=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if key is not None:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


class McpHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = R.ThreadingHTTPServer(("127.0.0.1", 0), R.H)   # a free port; make_server(0) falls back to the configured one
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        os.environ["ANTECHAMBER_MCP_KEY"] = "k1"
        self._recall = antechamber_recall.recall
        antechamber_recall.recall = lambda q, n=8, windows=False: {
            "query": q, "winners": [{"kind": "item", "source": "notes", "date": "2026-10-01", "title": "Backups",
                                     "section": "Decision", "text": "We rotate backups weekly."}]}

    def tearDown(self):
        antechamber_recall.recall = self._recall
        os.environ.pop("ANTECHAMBER_MCP_KEY", None)

    def test_off_without_key(self):
        os.environ.pop("ANTECHAMBER_MCP_KEY")
        code, _ = post(self.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, key="anything")
        self.assertEqual(code, 503)

    def test_wrong_or_missing_key(self):
        self.assertEqual(post(self.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, key="nope")[0], 401)
        self.assertEqual(post(self.port, {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 401)

    def test_initialize_and_list(self):
        code, r = post(self.port, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18"}}, key="k1")
        self.assertEqual(code, 200)
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        code, r = post(self.port, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, key="k1")
        self.assertEqual([t["name"] for t in r["result"]["tools"]], ["recall"])

    def test_notification_gets_202(self):
        code, r = post(self.port, {"jsonrpc": "2.0", "method": "notifications/initialized"}, key="k1")
        self.assertEqual((code, r), (202, None))

    def test_call_recall(self):
        code, r = post(self.port, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "recall", "arguments": {"query": "backup rotation"}}}, key="k1")
        text = r["result"]["content"][0]["text"]
        self.assertIn("We rotate backups weekly.", text)
        self.assertIn("2026-10-01 · notes · Backups · Decision", text)

    def test_empty_query_is_a_tool_error(self):
        _, r = post(self.port, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "recall", "arguments": {"query": " "}}}, key="k1")
        self.assertTrue(r["result"]["isError"])

    def test_recall_failure_is_a_tool_error(self):
        def boom(*a, **k):
            raise RuntimeError("store down")
        antechamber_recall.recall = boom
        _, r = post(self.port, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                "params": {"name": "recall", "arguments": {"query": "x"}}}, key="k1")
        self.assertTrue(r["result"]["isError"])
        self.assertIn("store down", r["result"]["content"][0]["text"])

    def test_unknown_method(self):
        _, r = post(self.port, {"jsonrpc": "2.0", "id": 6, "method": "nope"}, key="k1")
        self.assertEqual(r["error"]["code"], -32601)

    def test_bad_content_length(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", "/mcp"); c.putheader("Authorization", "Bearer k1"); c.putheader("Content-Length", "-1")
        c.endheaders(); self.assertEqual(c.getresponse().status, 400)

    def test_empty_batch(self):
        code, r = post(self.port, [], key="k1")
        self.assertEqual((code, r["error"]["code"]), (400, -32600))

    def test_foreign_host_or_origin_refused(self):
        import http.client
        for headers in ({"Host": "evil.example"}, {"Host": "127.0.0.1", "Origin": "https://evil.example"}):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("GET", "/antechamber/recall?q=x", headers=headers)
            self.assertEqual(c.getresponse().status, 403, headers)

    def test_local_origin_allowed(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/health", headers={"Origin": "http://localhost:3000"})
        self.assertEqual(c.getresponse().status, 200)


if __name__ == "__main__":
    unittest.main()
