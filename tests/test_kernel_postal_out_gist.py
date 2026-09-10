"""An OUTGOING postal card gets the judge's gist too (the user 2026-07-25): the sender's chat card used
to show a raw body prefix while the timeline showed a real gist for the same message. The caption exists
— the RECIPIENT session's judge captions the message it received, keyed by msg id — but the out-card
never joined it (the send tool's output carries no id). _hydrate_postal now joins the sent card to the
postal log row wearing the same body (closest in time when the same text went out twice) and fills
`mid` + `summary`; with no caption yet (the recipient hasn't landed/judged the message), the card keeps
no summary and the client clamps the raw body to two lines until a later render fills the gist in.
SYNTHETIC fixtures only."""
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
km = load_source("romp_kernel_outgist", os.path.join(BIN, "romp-kernel"))

BODY = "Please pick up the notes-api deploy: tests are green, only the changelog is left."


def _send_ev(ts=None, uuid="u1"):
    return {"kind": "tool", "name": "mcp__romp-postal__send_message",
            "input": json.dumps({"to": "api", "body": BODY}),
            "output": "Delivered to 'api'.", "isError": False, "uuid": uuid, "ts": ts}


def _row(mid, t, body=BODY):
    return {"id": mid, "from": "web", "fromId": "", "toId": "", "body": body,
            "kind": "coordinate", "t": t, "park": False}


class OutgoingGist(unittest.TestCase):
    def _hydrate(self, events, index, summaries):
        saved = km._msg_summaries
        km._msg_summaries = lambda: summaries
        try:
            return km._hydrate_postal(events, index)
        finally:
            km._msg_summaries = saved

    def test_out_card_joins_the_log_row_and_wears_the_caption(self):
        out = self._hydrate([_send_ev()], {"m1": _row("m1", 100)},
                            {"m1": "hand off the deploy; changelog still owed"})
        self.assertEqual(len(out), 1)
        card = out[0]
        self.assertEqual(card["kind"], "postal-service")
        self.assertEqual(card["mid"], "m1")
        self.assertEqual(card["summary"], "hand off the deploy; changelog still owed")

    def test_no_caption_yet_leaves_summary_unset_but_still_joins_the_mid(self):
        out = self._hydrate([_send_ev()], {"m1": _row("m1", 100)}, {})
        self.assertEqual(out[0]["mid"], "m1")
        self.assertNotIn("summary", out[0], "no gist yet → the client's two-line clamp fallback")

    def test_same_body_sent_twice_joins_the_row_closest_in_time(self):
        idx = {"m1": _row("m1", 100), "m2": _row("m2", 5000)}
        ts = "1970-01-01T01:23:00.000Z"                   # epoch 4980 — nearest m2
        out = self._hydrate([_send_ev(ts=ts)], idx, {"m2": "the later send's gist"})
        self.assertEqual(out[0]["mid"], "m2")
        self.assertEqual(out[0]["summary"], "the later send's gist")

    def test_no_matching_row_leaves_the_card_plain(self):
        out = self._hydrate([_send_ev()], {"m9": _row("m9", 100, body="a different message")}, {})
        self.assertNotIn("mid", out[0])
        self.assertNotIn("summary", out[0])


if __name__ == "__main__":
    unittest.main()
