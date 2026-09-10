#!/usr/bin/env python3
"""The nudge-phase planner can say "still progressing, nothing owed to you" (the user 2026-07-22).

Before this, a nudge reply reporting healthy progress ("in progress and self-driving, 11/203 done, I've
set a watcher that wakes me when the run finishes") produced NO op: the phase was marked processed, the
goal stayed 'working', and the kernel's nudge-failed stamp read that silence as "the response didn't
resolve this; it needs your direction" and blocked on the user. Undetermined was indistinguishable from
needs-you.

An `awaiting` op makes the progressing case sayable. It is the same ⏳ annotation the closer already
stamps (never a state), and _goal_awaiting_stamp already gates BOTH the auto-nudge fire path and
_mark_nudge_failed's own escape — so recording it suppresses the false interrupt through machinery that
already existed. All fixtures SYNTHETIC.
"""
import inspect
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


class ParseAwaiting(unittest.TestCase):
    def test_an_awaiting_op_on_a_menu_goal_parses(self):
        ops = jd._parse_plan('{"ops":[{"do":"awaiting","goal":1,"why":"the run is still going; a watcher is armed"}]}', 3)
        self.assertEqual(ops, [{"do": "awaiting", "why": "the run is still going; a watcher is armed",
                                "goal": 1}])

    def test_an_awaiting_op_off_the_menu_is_dropped(self):
        self.assertEqual(jd._parse_plan('{"ops":[{"do":"awaiting","goal":9,"why":"x"}]}', 3) or [], [])


class ApplyAwaiting(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))

    def tearDown(self):
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _store(self):
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Build the pipeline"}], [])
        return s, SID + ":g1"

    def test_awaiting_stamps_the_hourglass_without_changing_state(self):
        s, gid = self._store()
        why = "the run is still going; a watcher wakes the session when it finishes"
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "awaiting", "goal": 1, "why": why}], jd.open_menu(s))
        nd = s["nodes"][gid]
        self.assertEqual(nd.get("awaitingWhy"), why, "the awaiting why is stamped for the nudge gate")
        self.assertFalse(nd.get("blocked"), "awaiting is an annotation, never a block")
        self.assertFalse(nd.get("nodeComplete"), "and never a completion")
        jd.rollup_status(s, session_closed=False)
        self.assertEqual(s["status"][gid], "working", "the card stays working — it IS still working")

    def test_the_stamp_is_what_the_nudge_gate_reads(self):
        # the whole point: awaitingWhy is exactly what the kernel's _goal_awaiting_stamp reads, and that
        # already gates the auto-nudge fire path AND _mark_nudge_failed's own escape.
        s, gid = self._store()
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"))
        jd.apply_plan(s, "s2", T0 + 100,
                      [{"do": "awaiting", "goal": 1, "why": "a background task is still running"}],
                      jd.open_menu(s))
        self.assertEqual(s["nodes"][gid].get("awaitingWhy"), "a background task is still running")

    def test_a_later_closer_audit_lifts_it(self):
        s, gid = self._store()
        jd.apply_plan(s, "s2", T0 + 100, [{"do": "awaiting", "goal": 1, "why": "waiting on the run"}],
                      jd.open_menu(s))
        self.assertTrue(s["nodes"][gid].get("awaitingWhy"))
        jd.record_verdict(s, s["nodes"][gid], "closer", "awaiting", T0 + 200, lift=True)
        jd._materialize_node(s["nodes"][gid])
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"), "the wait ended → the nudge is free again")


class NudgePromptOffersAwaiting(unittest.TestCase):
    def test_the_nudge_note_asks_for_awaiting_rather_than_silence(self):
        src = inspect.getsource(jd.plan_llm)
        self.assertIn("**awaiting** on #1", src, "the nudge note offers the awaiting verdict")
        self.assertIn("silence is not enough", src,
                      "and says why: an unresolved nudge with no verdict reads as needing the user")


if __name__ == "__main__":
    unittest.main()
