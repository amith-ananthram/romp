#!/usr/bin/env python3
"""_parked_handoffs (the user 2026-06-22): a send to a DEAD session parks in its maildir until the session
is revived — the kernel surfaces each as a needs-you 'revive to deliver, or dismiss' decision instead of
parking silently. DETERMINISTIC, no judging: a park:true 'sent' row in the postal log whose maildir file is
STILL in the recipient's new/ (unconsumed — the authoritative still-parked signal) AND whose recipient is
still dead. Self-contained: drives _parked_handoffs against a synthetic messages.jsonl + maildir. No real
session data.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_ph", os.path.join(BIN, "romp-kernel"))
jd = km.jd

A = "aaaaaaaa-0000-0000-0000-000000000001"   # sender (alive)
B = "bbbbbbbb-0000-0000-0000-000000000002"   # recipient (dead)
NOW = 1781100000


class ParkedHandoff(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, jd.MESSAGES, km._name_of)
        jd.STATE = Path(self.td.name)
        (jd.STATE / "timeline").mkdir(parents=True)
        jd.MESSAGES = jd.STATE / "timeline" / "messages.jsonl"
        km._name_of = lambda sid: {A: "alfa", B: "bravo"}.get(sid)

    def tearDown(self):
        (jd.STATE, jd.MESSAGES, km._name_of) = self.saved
        self.td.cleanup()

    def _log(self, rows):
        jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def _maildir(self, to_id, mid, present=True):
        d = jd.STATE / "postal" / "mail" / to_id / "new"
        d.mkdir(parents=True, exist_ok=True)
        if present:
            (d / mid).write_text("From: alfa\n\nDELEGATE: do the thing")

    def _sent(self, mid, frm=A, to=B, park=True, body="DELEGATE: do the export"):
        r = {"ev": "sent", "id": mid, "from_id": frm, "to_id": to, "body": body, "t": NOW - 100}
        if park:
            r["park"] = True
        return r

    def test_parked_to_dead_with_pending_maildir_is_surfaced(self):
        self._log([self._sent("m1.1")])
        self._maildir(B, "m1.1", present=True)
        out = km._parked_handoffs(NOW, {A})              # B is NOT alive
        self.assertEqual(len(out), 1, "a parked-to-dead handoff with a pending maildir file surfaces")
        self.assertEqual(out[0]["toId"], B)
        self.assertEqual((out[0]["fromName"], out[0]["toName"]), ("alfa", "bravo"))
        self.assertEqual(out[0]["msgId"], "m1.1")

    def test_recipient_alive_means_already_delivered(self):
        self._log([self._sent("m1.1")])
        self._maildir(B, "m1.1", present=True)
        self.assertEqual(km._parked_handoffs(NOW, {A, B}), [], "B revived/alive → the parked mail already delivered")

    def test_consumed_maildir_is_resolved(self):
        self._log([self._sent("m1.1")])
        self._maildir(B, "m1.1", present=False)          # file gone = consumed / recalled
        self.assertEqual(km._parked_handoffs(NOW, {A}), [], "no maildir file → the handoff is resolved, not surfaced")

    def test_a_normal_delivered_send_is_not_a_parked_handoff(self):
        self._log([self._sent("m1.1", park=False)])      # delivered live, no park flag
        self._maildir(B, "m1.1", present=True)
        self.assertEqual(km._parked_handoffs(NOW, {A}), [], "only park:true sends are handoffs awaiting a decision")

    def test_oldest_first_and_multiple(self):
        self._log([self._sent("m2.2", body="DELEGATE: second"),
                   {"ev": "sent", "id": "m1.1", "from_id": A, "to_id": B, "body": "DELEGATE: first",
                    "t": NOW - 500, "park": True}])
        self._maildir(B, "m1.1", present=True)
        self._maildir(B, "m2.2", present=True)
        out = km._parked_handoffs(NOW, {A})
        self.assertEqual([h["msgId"] for h in out], ["m1.1", "m2.2"], "oldest-first by send time")


if __name__ == "__main__":
    unittest.main()
