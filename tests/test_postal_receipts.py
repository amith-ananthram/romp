#!/usr/bin/env python3
"""T302: a SENT postal card carries the ledger's later outcomes for its message, so the chat's delivery icon
can move after the send. The kernel used to stamp a sent card only at send time (delivered | parked); the
postal ledger also records, per message id, `exec` (the recipient's inbox drain consumed it: a real read),
`unexec` (a claimed-then-rolled-back drain), `relayed` (a far host's end-to-end ack), `bounced` (+ why) and
`recall`. The postal index folds them onto the message's record and _hydrate_postal hands them to the sent
card as `receipt`, with `remote` when the send crossed the peer bus. SYNTHETIC fixtures."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

WEB = "aaaaaaaa-1111-2222-3333-444444444444"
API = "bbbbbbbb-1111-2222-3333-444444444444"
T0 = 1_789_000_000


def sent(mid, body, t, to=API, **extra):
    row = {"t": t, "ev": "sent", "id": mid, "from": "web", "from_id": WEB, "to_id": to, "body": body,
           "kind": "coordinate", "from_host": ""}
    row.update(extra)
    return row


class ReceiptsRideTheIndex(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        (jd.STATE / "timeline").mkdir(parents=True, exist_ok=True)
        self.log = jd.STATE / "timeline" / "messages.jsonl"
        km._postal_index_memo[0] = None
        self._saved_msgsum = km._msg_summaries
        km._msg_summaries = lambda: {}

    def tearDown(self):
        km._msg_summaries = self._saved_msgsum
        km._postal_index_memo[0] = None
        jd._rebind_state(self._saved)

    def _write(self, rows):
        self.log.write_text("".join(json.dumps(r) + "\n" for r in rows))
        km._postal_index_memo[0] = None

    def test_the_index_folds_every_later_outcome_onto_the_sent_record(self):
        self._write([
            sent("m1", "ship the notes-api docs", T0),
            {"t": T0 + 5, "ev": "exec", "id": "m1"},
            sent("m2", "review the retry loop", T0 + 10, to="peer:TESTHOST"),
            {"t": T0 + 12, "ev": "relayed", "id": "m2"},
            sent("m3", "and the cap?", T0 + 20, to="peer:TESTHOST"),
            {"t": T0 + 21, "ev": "bounced", "id": "m3", "why": "refused: the mailbox is isolated"},
            sent("m4", "never mind", T0 + 30),
            {"t": T0 + 31, "ev": "recall", "id": "m4"},
            sent("m5", "claimed then rolled back", T0 + 40),
            {"t": T0 + 41, "ev": "exec", "id": "m5"},
            {"t": T0 + 42, "ev": "unexec", "id": "m5"},
            {"t": T0 + 50, "ev": "exec", "id": "m-not-here"},      # an outcome for a message this log never sent
        ])
        idx = km._postal_index()
        self.assertEqual(idx["m1"]["read"], T0 + 5)
        self.assertEqual(idx["m2"]["relayed"], T0 + 12)
        self.assertEqual(idx["m3"]["bounced"], T0 + 21)
        self.assertEqual(idx["m3"]["bouncedWhy"], "refused: the mailbox is isolated")
        self.assertEqual(idx["m4"]["recalled"], T0 + 31)
        self.assertNotIn("read", idx["m5"], "a rolled-back drain is not a read")
        self.assertNotIn("m-not-here", idx)
        self.assertEqual(idx["m1"]["body"], "ship the notes-api docs", "the sent record's own fields are untouched")

    def test_an_outcome_logged_before_its_sent_row_still_folds(self):
        # deliver() publishes the file before it appends the sent row, and a drain can claim the file in that
        # instant and log exec first; the postal service's own reader joins the two regardless of order, and so
        # does the index (review, 2026-09-10)
        self._write([
            {"t": T0 + 1, "ev": "exec", "id": "m1"},
            sent("m1", "ship the notes-api docs", T0),
            {"t": T0 + 2, "ev": "exec", "id": "m2"},
            {"t": T0 + 3, "ev": "unexec", "id": "m2"},
            sent("m2", "rolled back", T0),
        ])
        idx = km._postal_index()
        self.assertEqual(idx["m1"]["read"], T0 + 1)
        self.assertNotIn("read", idx["m2"], "row order within an id still decides: exec then unexec is not read")

    def _sent_event(self, body, t, output="Delivered to 'api'."):
        return {"kind": "tool", "name": "mcp__romp-postal-service__send_message",
                "input": json.dumps({"to": "api", "body": body, "kind": "coordinate"}),
                "output": output, "ts": em_iso(t), "uuid": "u-" + str(t)}

    def test_a_sent_card_carries_the_receipt_and_knows_a_remote_send(self):
        self._write([
            sent("m1", "ship the notes-api docs", T0),
            {"t": T0 + 5, "ev": "exec", "id": "m1"},
            sent("m2", "review the retry loop", T0 + 10, to="peer:TESTHOST"),
            sent("m3", "and the cap?", T0 + 20, to="peer:TESTHOST"),
            {"t": T0 + 21, "ev": "bounced", "id": "m3", "why": "undeliverable"},
            sent("m6", "plain local send, nothing yet", T0 + 60),
        ])
        idx = km._postal_index()
        cards = km._hydrate_postal([self._sent_event("ship the notes-api docs", T0),
                                    self._sent_event("review the retry loop", T0 + 10),
                                    self._sent_event("and the cap?", T0 + 20, output="parked for TESTHOST (unreachable)"),
                                    self._sent_event("plain local send, nothing yet", T0 + 60)], idx)
        by = {c["mid"]: c for c in cards}
        self.assertEqual(by["m1"]["receipt"], {"read": T0 + 5}, "read: the recipient consumed it")
        self.assertEqual(by["m2"]["receipt"], {"remote": True}, "across the relay, nothing acked yet: the icon reads sent")
        self.assertEqual(by["m3"]["status"], "parked")
        self.assertEqual(by["m3"]["receipt"], {"bounced": T0 + 21, "why": "undeliverable", "remote": True})
        self.assertNotIn("receipt", by["m6"], "no outcome, no receipt field at all")
        self.assertEqual(by["m6"]["status"], "delivered")

    def test_an_errored_send_gets_no_receipt_even_when_a_same_body_row_exists(self):
        # a refused send retried with the same words seconds later: the body-keyed join would hand the errored
        # card the retry's outcomes; a message that never left carries none (review, 2026-09-10)
        self._write([sent("m1", "ship the notes-api docs", T0 + 5), {"t": T0 + 9, "ev": "exec", "id": "m1"}])
        idx = km._postal_index()
        ev = self._sent_event("ship the notes-api docs", T0, output="mailbox refused")
        ev["isError"] = True
        cards = km._hydrate_postal([ev, self._sent_event("ship the notes-api docs", T0 + 5)], idx)
        self.assertIsNone(cards[0]["status"])
        self.assertNotIn("receipt", cards[0])
        self.assertEqual(cards[1]["receipt"], {"read": T0 + 9})


def em_iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
