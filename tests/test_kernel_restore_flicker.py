#!/usr/bin/env python3
"""A user gesture must never bounce off the judge pass's goal snapshot (the user 2026-07-23).

The incident: the user restored a dismissed completed card, replied to it 42 seconds later, and the
card flipped Working (optimistic) → Completed (a mid-pass payload) → Working (pass end). Root cause:
the undo-clear was not a first-class journaled gesture — the restore journal row carries only the
ARCHIVED node payload, still flag-cleared, so replaying the reply onto a snapshot taken BEFORE the
restore rebuilt the card sealed and refused the reopen; the un-clear verdict itself was never
journaled and never punched through the pass. Three sibling holes fixed together:
  - _mark_nodes_cleared(value=False) journals an `unclear` override + punches (_note_user_goal_write)
  - _replay_overrides handles `unclear`, and its supersede guard only yields to STRICTLY-later user
    gestures (an equal-time DIFFERENT gesture — restore and reply in the same second — must not eat
    the replay; the exact twin is matched by kind+flags instead)
  - _feed_goals stamps the punch done only on SUCCESS, so a failed replay retries instead of serving
    the pre-gesture card for the whole pass

XDG_STATE_HOME is pointed at a temp dir BEFORE the kernel loads (standard kernel test isolation);
the judge module is process-shared across kernel test copies, so this file uses its own SID and
wipes its journal per test. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
kern = load_source("romp_kernel_restoreflick", os.path.join(BIN, "romp-kernel"))
jd = kern.jd

SID = "11111111-2222-3333-4444-cccccccccccc"
GID = SID + ":g1"
NOW = int(time.time())


class RestoreReplyFlicker(unittest.TestCase):
    def setUp(self):
        for p in (jd._overrides_dir() / (SID + ".jsonl"),
                  jd.STATE / "cleared.jsonl",
                  jd.GOALDIR / (SID + ".json")):
            if p.exists():
                p.unlink()
        arch = jd.load_goal_archive(SID)
        if arch and arch.get("nodes"):
            jd.save_goal_archive(SID, {"nodes": {}, "status": {}})
        kern._end_goals_pass()                         # never inherit a stuck snapshot from a failed test
        kern._compact_seen.pop(SID, None)

    def tearDown(self):
        kern._end_goals_pass()

    def _seed_archived_completed(self):
        """A settled completed card the user then dismissed, compacted into the archive — the exact
        pre-state of the incident."""
        store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
                 "nodes": {GID: {"id": GID, "text": "Write the launch notes", "parentId": None,
                                 "nodeComplete": False, "blocked": False, "cleared": False,
                                 "trail": [], "t": NOW - 3600, "mt": NOW - 1800}}}
        nd = store["nodes"][GID]
        jd.record_verdict(store, nd, "closer", "done", NOW - 1700, why="finished it")
        jd.record_verdict(store, nd, "romp", "settle", NOW - 1600)
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        kern._clear_all([GID])
        kern._compact_goal_stores()
        self.assertNotIn(GID, jd.load_goals(SID).get("nodes", {}), "seed: compacted out of the live store")
        self.assertIn(GID, jd.load_goal_archive(SID).get("nodes", {}), "seed: parked in the archive")

    def _reply(self, now=None):
        """The ws followup op's kernel side: the optimistic reopen + the punch mark."""
        ok = jd.optimistic_followup(SID, GID, text="one more thing", now=now or int(time.time()))
        kern._note_user_goal_write(SID)
        return ok

    def test_reply_after_restore_punches_a_pre_restore_snapshot(self):
        # THE INCIDENT: the pass (and its snapshot) predate the restore; the reply lands mid-pass.
        self._seed_archived_completed()
        kern._begin_goals_pass()                       # snapshot: card still archived
        kern._undo_clear()                             # user restores the dismissed card…
        self.assertTrue(self._reply(now=int(time.time()) + 42), "…and replies 42s later")
        mid = kern._feed_goals(SID)
        self.assertIn(GID, mid.get("nodes", {}), "the restored card exists mid-pass")
        self.assertEqual(mid["status"].get(GID), "working",
                         "the reply shows mid-pass — never a bounce back to completed/cleared")
        kern._end_goals_pass()
        self.assertEqual(kern._feed_goals(SID)["status"].get(GID), "working", "and stays put after the pass")

    def test_a_restore_alone_shows_mid_pass(self):
        # The restore is a user gesture too: the card must come back AT ONCE, not wait out the pass.
        self._seed_archived_completed()
        kern._begin_goals_pass()
        kern._undo_clear()
        mid = kern._feed_goals(SID)
        self.assertIn(GID, mid.get("nodes", {}), "the restored card exists mid-pass")
        self.assertEqual(mid["status"].get(GID), "completed",
                         "a restored completed card returns to Completed, visible, not flag-cleared")

    def test_same_second_restore_and_reply_both_replay(self):
        # Two gestures in one second: the old >= supersede guard let the undo-reopen eat the reply's
        # replay, so the punched snapshot stayed completed. The twin match keeps both.
        self._seed_archived_completed()
        kern._undo_clear()                             # live (no pass yet)
        undo_ev = next(e["ev_t"] for e in reversed(jd.load_goals(SID)["nodes"][GID]["log"])
                       if e.get("src") == "user" and e.get("kind") == "reopen" and e.get("undo"))
        kern._begin_goals_pass()                       # snapshot: restored, completed
        self.assertTrue(self._reply(now=int(undo_ev)))  # the reply carries the SAME second
        mid = kern._feed_goals(SID)
        self.assertEqual(mid["status"].get(GID), "working",
                         "an equal-time different gesture never supersedes the reply's replay")

    def test_replay_is_idempotent_on_live_loads(self):
        # The journal now holds restore + unclear + followup rows; every load_goals replays them.
        # Survived twins must be recognized or the log gains duplicate rows per load.
        self._seed_archived_completed()
        kern._undo_clear()
        self.assertTrue(self._reply())
        n1 = len(jd.load_goals(SID)["nodes"][GID].get("log") or [])
        n2 = len(jd.load_goals(SID)["nodes"][GID].get("log") or [])
        self.assertEqual(n1, n2, "a second load replays the journal as pure no-ops")

    def test_a_failed_replay_does_not_burn_the_punch(self):
        # The punch marked itself done BEFORE replaying; one exception served the pre-gesture card
        # for the rest of the pass. Done is stamped on success only — the next read retries.
        # The retry lands on a fresh copy: the object the failed read served is a fixed value.
        self._seed_archived_completed()
        kern._undo_clear()                             # live restore (completed on the board)
        kern._begin_goals_pass()
        self.assertTrue(self._reply())
        real = jd._replay_overrides
        calls = {"n": 0}

        def boom(fsid, store):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("synthetic replay failure")
            return real(fsid, store)

        jd._replay_overrides = boom
        try:
            first = kern._feed_goals(SID)
            self.assertEqual(first["status"].get(GID), "completed",
                             "the failed punch honestly serves the snapshot this read")
            second = kern._feed_goals(SID)
            self.assertEqual(second["status"].get(GID), "working",
                             "the mark was not burned — the very next read retries and punches")
            self.assertIsNot(second, first, "the retry replays onto a fresh copy, not onto the served one")
            self.assertEqual(first["status"].get(GID), "completed",
                             "what the failed read served has not changed under its holder")
        finally:
            jd._replay_overrides = real


if __name__ == "__main__":
    unittest.main()
