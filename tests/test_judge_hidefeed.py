#!/usr/bin/env python3
"""hideFromFeed takes a session OUT of task tracking (the user 2026-06-23): when the timeline's feed checkbox
is crossed out, the judge's PLANNER and CLOSER skip the session — so no goal backlog accumulates while it's
muted (toggling off→on then surfaces nothing). The captioner/archiver (run_index) is
deliberately NOT gated, so a muted session stays captioned/archived for the dashboard.

Synthetic only — placeholder UUIDs, hermetic temp STATE, no real session data.
"""
import json
import os
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

MUTED = "11111111-1111-1111-1111-111111111111"
VISIBLE = "22222222-2222-2222-2222-222222222222"


class HiddenFromFeed(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))   # rebind STATE *and* its derived dirs, not just STATE (avoid live-state leak)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _mute(self, sid, flag="hideFromFeed"):
        (jd.STATE / "session-flags.json").write_text(json.dumps({sid: {flag: True}}))

    # ── the flag reader ──
    def test_reader_reads_the_flag(self):
        self.assertFalse(jd._hidden_from_feed(MUTED), "no flags file → not hidden")
        self._mute(MUTED)
        self.assertTrue(jd._hidden_from_feed(MUTED))
        self.assertFalse(jd._hidden_from_feed(VISIBLE), "a different session is unaffected")

    def test_reader_fails_open_on_corruption(self):
        (jd.STATE / "session-flags.json").write_text("{not valid json")
        self.assertFalse(jd._hidden_from_feed(MUTED), "a corrupt flags file must NOT wedge the judge")

    def test_postaloff_does_not_stop_tracking(self):
        self._mute(MUTED, flag="postalServiceOff")
        self.assertFalse(jd._hidden_from_feed(MUTED), "postalServiceOff (mailbox) alone must not stop task tracking")

    # ── the planner/closer fleet gate ──
    def _fleet(self):
        return [(MUTED, "/tmp/m.jsonl", None, "muted"), (VISIBLE, "/tmp/v.jsonl", None, "visible")]

    def _run_collecting(self, runner_name, worker_name, worker_ret):
        seen = []
        saved_disc = jd.discover
        saved_worker = getattr(jd, worker_name)
        jd.discover = lambda now: self._fleet()
        setattr(jd, worker_name, lambda fsid, path, now: (seen.append(fsid), worker_ret)[1])
        try:
            getattr(jd, runner_name)(now=1700000000)
        finally:
            jd.discover = saved_disc
            setattr(jd, worker_name, saved_worker)
        return seen

    def test_planner_skips_a_muted_session(self):
        self._mute(MUTED)
        seen = self._run_collecting("run_plan", "_plan_session", 0)
        self.assertEqual(seen, [VISIBLE], "the planner plans only the visible session, skipping the muted one")

    def test_closer_skips_a_muted_session(self):
        self._mute(MUTED)
        seen = self._run_collecting("run_close", "_close_session", [])
        self.assertEqual(seen, [VISIBLE], "the closer skips the muted session")

    def test_unmuted_sessions_are_all_tracked(self):
        seen = self._run_collecting("run_plan", "_plan_session", 0)   # no flag set
        self.assertEqual(sorted(seen), sorted([MUTED, VISIBLE]), "with no flag, every session is planned")


class FastForwardOnUnmute(unittest.TestCase):
    """UN-muting must NOT backfill (the user 2026-06-25): the planner is gated off while muted, so segments
    pile up unplaced; re-enabling task tracking must resume from the PRESENT, not retro-create a burst of
    goals for the muted gap. fast_forward_placements seals every outstanding unit as processed-with-no-goal.
    Synthetic only."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    # two ended work segments (the gap) + one open prompt segment (in flight)
    UNITS = [("segA", "work", 1, "did A", True, None),
             ("segB", "work", 2, "did B", True, None),
             ("segC", "prompt", 3, "asking C", True, None)]

    def _run(self, fsid):
        saved_pu, saved_ps = jd.plan_units, jd.parsed_session
        jd.plan_units = lambda session, store=None: list(self.UNITS)
        jd.parsed_session = lambda fsid, paths, now: {"turns": []}
        try:
            return jd.fast_forward_placements(fsid, path="/x", now=1700000000)
        finally:
            jd.plan_units, jd.parsed_session = saved_pu, saved_ps

    def test_seals_every_outstanding_unit_with_no_goal(self):
        n = self._run(MUTED)
        p = jd.load_goals(MUTED)["placements"]
        self.assertIn("segA", p); self.assertIsNone(p["segA"], "ended work segment sealed, no goal")
        self.assertIn("segB", p); self.assertIsNone(p["segB"])
        self.assertIn("segC#p", p, "the open segment's prompt-run is sealed")
        self.assertIn("segC", p, "AND its FUTURE work-run, so the in-flight segment stays untracked too")
        self.assertEqual(n, 4, "segA, segB, segC#p, segC")
        self.assertEqual(jd.load_goals(MUTED)["nodes"], {}, "NO goals created — backfill suppressed")

    def test_leaves_already_placed_units_untouched(self):
        st = jd.load_goals(MUTED); st["placements"]["segA"] = "node-1"; jd.save_goals(MUTED, st)
        n = self._run(MUTED)
        p = jd.load_goals(MUTED)["placements"]
        self.assertEqual(p["segA"], "node-1", "an already-planned segment keeps its real placement")
        self.assertEqual(n, 3, "only segB, segC#p, segC are newly sealed")

    def test_missing_transcript_is_a_safe_noop(self):
        saved = jd.discover
        jd.discover = lambda now: []          # session not discoverable → no path
        try:
            self.assertEqual(jd.fast_forward_placements("nope", now=1700000000), 0)
        finally:
            jd.discover = saved


if __name__ == "__main__":
    unittest.main()
