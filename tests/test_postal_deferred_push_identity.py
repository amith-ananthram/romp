#!/usr/bin/env python3
"""A deferred push must not give a message a SECOND identity (the user 2026-07-23).

A consuming drain is a CLAIM, not a delivery: _push drains the box, asks the kernel to inject, and
when the kernel can't inject safely it has to put the mail back. Putting it back by re-sending
through deliver() minted a NEW maildir id and logged a NEW "sent" event, so one message became
several — and since the timeline draws a message arc per "sent" event, every deferred attempt drew
another arc whose id no recipient transcript would ever carry. Clicking those arcs reported
"couldn't locate in the transcript", deterministically, forever.

These pin the rollback: same id, mail back in new/, the exec stamp retracted, headers preserved,
and one "sent" event no matter how many times the push defers.

Synthetic only — placeholder UUIDs, hermetic temp state dir, no real session data.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))

TO = "11111111-2222-3333-4444-555555555555"
FROM = "66666666-7777-8888-9999-aaaaaaaaaaaa"


def _log_events():
    p = pm.TLDIR / "messages.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


class DeferredPushKeepsOneIdentity(unittest.TestCase):
    def setUp(self):
        for box in (pm.MAILROOT / TO,):
            for d in ("new", "cur", "tmp"):
                p = box / d
                if p.is_dir():
                    for f in p.iterdir():
                        f.unlink()
        p = pm.TLDIR / "messages.jsonl"
        if p.exists():
            p.unlink()

    def test_restore_puts_it_back_under_the_same_id(self):
        mid = pm.deliver(TO, "api", FROM, "the migration is on staging", kind="coordinate")
        claimed = pm.read_box(TO, consume=True)
        self.assertEqual([m["id"] for m in claimed], [mid])
        self.assertFalse((pm.MAILROOT / TO / "new" / mid).exists(), "claimed → out of new/")

        self.assertTrue(pm.restore(TO, mid), "a claimed message can be put back")
        self.assertTrue((pm.MAILROOT / TO / "new" / mid).is_file(), "back in new/ under its own id")
        self.assertFalse((pm.MAILROOT / TO / "cur" / mid).exists(), "and no longer claimed")

        again = pm.read_box(TO, consume=True)
        self.assertEqual([m["id"] for m in again], [mid], "the SAME id drains next time, not a new one")

    def test_restore_preserves_the_original_headers(self):
        mid = pm.deliver(TO, "api", FROM, "handing off the failing spec", park=True, kind="delegate")
        got = pm.read_box(TO, consume=True)[0]
        pm.restore(TO, mid)
        back = pm.read_box(TO, consume=True)[0]
        for field in ("from", "from_id", "date", "body", "kind", "park"):
            self.assertEqual(back[field], got[field], "%s survives the rollback" % field)

    def test_a_deferred_push_logs_one_sent_not_two(self):
        # The regression itself: N deferred pushes used to log N+1 "sent" events (N+1 timeline arcs)
        # for a single message. A rollback is not a send.
        mid = pm.deliver(TO, "api", FROM, "rebased onto main", kind="coordinate")
        for _ in range(3):
            pm.read_box(TO, consume=True)
            pm.restore(TO, mid)
        sent = [e for e in _log_events() if e.get("ev") == "sent"]
        self.assertEqual([e["id"] for e in sent], [mid],
                         "one message, one sent event, one arc — however often the push defers")

    def test_rollback_retracts_the_exec_stamp(self):
        mid = pm.deliver(TO, "api", FROM, "coverage is green", kind="coordinate")
        pm.read_box(TO, consume=True)
        self.assertEqual(pm._sent_receipts(FROM)[0]["id"], mid)
        self.assertIsNotNone(pm._sent_receipts(FROM)[0]["exec"], "a claim stamps exec")
        pm.restore(TO, mid)
        self.assertIsNone(pm._sent_receipts(FROM)[0]["exec"],
                          "rolled back → the sender's receipt reads pending again, not delivered")
        pm.read_box(TO, consume=True)
        self.assertIsNotNone(pm._sent_receipts(FROM)[0]["exec"], "a later real read stamps it again")

    def test_restore_is_a_no_op_when_the_message_is_gone(self):
        # Recalled or swept while the push held it: nothing to put back, and the caller falls back
        # to a re-send so the mail is never lost.
        mid = pm.deliver(TO, "api", FROM, "never mind", kind="coordinate")
        pm.read_box(TO, consume=True)
        (pm.MAILROOT / TO / "cur" / mid).unlink()
        self.assertFalse(pm.restore(TO, mid))

    def test_restore_rejects_a_traversing_id(self):
        self.assertFalse(pm.restore(TO, "../../../../etc/passwd"))
        self.assertFalse(pm.restore("../../../../etc", "x"))


class DeferredPushEndToEnd(unittest.TestCase):
    """The regression at its own level: drive _push with a kernel that refuses to inject."""

    def setUp(self):
        for d in ("new", "cur", "tmp"):
            p = pm.MAILROOT / TO / d
            if p.is_dir():
                for f in p.iterdir():
                    f.unlink()
        p = pm.TLDIR / "messages.jsonl"
        if p.exists():
            p.unlink()
        self._disabled, self._post = pm._push_disabled, pm._kernel_post
        self._sessions_file = os.environ.pop("ROMP_SESSIONS_FILE", None)
        pm._push_disabled = lambda: False
        pm._kernel_post = lambda *a, **k: {"injected": False}   # kernel can't inject: a draft, a prompt

    def tearDown(self):
        pm._push_disabled, pm._kernel_post = self._disabled, self._post
        if self._sessions_file is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._sessions_file

    def test_push_that_cannot_inject_keeps_the_message_id(self):
        mid = pm.deliver(TO, "api", FROM, "staging is green, promoting", kind="coordinate")
        agent = {"id": TO, "state": "working", "backend": "tmux"}

        for _ in range(3):                       # three deferred pushes in a row
            self.assertFalse(pm._push(TO, agent), "not injected → push reports False")

        sent = [e for e in _log_events() if e.get("ev") == "sent"]
        self.assertEqual([e["id"] for e in sent], [mid],
                         "a deferred push is a rollback, not a re-send: still ONE message id")
        self.assertTrue((pm.MAILROOT / TO / "new" / mid).is_file(),
                        "and the mail is back in new/ for the drain backstop")
        self.assertEqual([m["id"] for m in pm.read_box(TO, consume=True)], [mid],
                         "the id that finally reaches the transcript is the id the arc carries")


if __name__ == "__main__":
    unittest.main()
