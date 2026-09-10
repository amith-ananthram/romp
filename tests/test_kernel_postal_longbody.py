"""A LONG outgoing postal message must still render as a chat card (the user 2026-06-26): a ~5400-char
handoff sent via the send_message tool never showed in the chat, while a short one did. Cause: build_session
caps every tool input at 4000 chars, which cut the send_message {to,body} JSON mid-string, so
_postal_out_card's json.loads failed and it fell back to a raw tool row. The send_message input is now kept
intact (it's dropped once _hydrate_postal swaps in the card, so no payload bloat). Both messages DELIVERED
fine all along — this was purely the chat visualization dropping long bodies.
"""
import inspect
import json
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class PostalLongBody(unittest.TestCase):
    def test_a_long_send_message_input_still_makes_an_outgoing_card(self):
        body = "x" * 6000                                  # well over the old 4000-char tool-input cap
        ev = {"kind": "tool", "name": "mcp__romp-postal__send_message",
              "input": json.dumps({"to": "ui3", "body": body}),   # INTACT JSON (no truncation)
              "output": "Delivered to 'ui3'.", "isError": False, "uuid": "u1", "ts": None}
        card = km._postal_out_card(ev)
        self.assertIsNotNone(card, "a long body must still produce an outgoing card, not None")
        self.assertEqual(card["direction"], "out")
        self.assertEqual(card["peer"], "ui3")
        self.assertEqual(card["body"], body, "the full body survives")
        self.assertEqual(card["status"], "delivered")

    def test_a_truncated_send_message_input_would_break_the_card_proving_the_cause(self):
        body = "x" * 6000
        truncated = json.dumps({"to": "ui3", "body": body})[:4000]   # the OLD behavior — cut mid-string
        self.assertIsNone(km._postal_out_card({"input": truncated, "output": "Delivered.", "isError": False}),
                          "truncated JSON fails to parse → None → no card (the regression)")

    def test_build_session_keeps_the_send_message_input_intact(self):
        src = inspect.getsource(km.build_session)
        self.assertIn("_SEND_TOOL_RE.search(b.get(\"name\"))", src, "send_message is special-cased")
        self.assertIn("json.dumps(inp) if _full else json.dumps(inp)[:4000]", src,
                      "send_message input is kept whole; other tools still capped at 4000")


if __name__ == "__main__":
    unittest.main()
