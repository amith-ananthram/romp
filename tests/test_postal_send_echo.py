#!/usr/bin/env python3
"""send_message echoes what the DECLARATION did (the user 2026-07-26): a question or delegate records
the sender as waiting on the recipient — a real hold a mis-declared kind creates by accident (a
"question" whose prose said no reply was needed parked its sender for a day). The tool result reads
the cost back so the sender can self-correct while recall_message still works; a coordinate stays the
plain delivery line. Synthetic only — the bus call is stubbed, no live postal server."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ps = load_source("romp_postal_echo", os.path.join(BIN, "romp-postal-service"))


class SendEcho(unittest.TestCase):
    def setUp(self):
        self._saved = (ps._http, ps._self_identity, ps._heartbeat)
        self.posts = []
        ps._http = lambda method, path, payload=None: (self.posts.append((method, path, payload)) or {})
        ps._self_identity = lambda: ("id-web", "web")     # the one resolver every tool call reads (2026-09-06)
        ps._heartbeat = lambda *a, **k: None

    def tearDown(self):
        ps._http, ps._self_identity, ps._heartbeat = self._saved

    def _send(self, kind):
        out, err = ps._mcp_call("send_message", {"to": "api", "body": "the staging port?", "kind": kind})
        self.assertFalse(err)
        return out

    def test_a_question_reads_its_wait_back(self):
        out = self._send("question")
        self.assertIn("as a question", out)
        self.assertIn("waiting on their reply", out, "the sender learns the hold it just created")
        self.assertIn("recall this message and resend it as coordinate", out,
                      "…and the self-correction path while recall still works")
        self.assertEqual(self.posts[0][1], "/send", "the message itself still went out")

    def test_a_handoff_reads_the_ownership_transfer_back(self):
        # the user 2026-08-15 (reversing 2026-07-25): a delegate transfers ownership — the sender is
        # NOT recorded as waiting; a sender needing the report before proceeding asks with question
        out = self._send("delegate")
        self.assertIn("as a handoff", out)
        self.assertIn("they own it now", out)
        self.assertIn("NOT recorded as waiting", out)
        self.assertIn("send a question", out)

    def test_a_coordinate_stays_the_plain_delivery_line(self):
        self.assertEqual(self._send("coordinate"), "Delivered to 'api'.")


if __name__ == "__main__":
    unittest.main()
