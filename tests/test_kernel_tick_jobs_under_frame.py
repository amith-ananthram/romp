#!/usr/bin/env python3
"""A pusher tick job that parses a session while a judge pass frame is open reads the LIVE world (review find,
2026-09-08). The frame is a module global in the judge, but its pin is scoped to the pass's own threads
(jd._pass_frame: the thread that opened or joined the frame, and the judge's pool workers). The kernel's tick
jobs (_clear_done_working_notes, _interrupt_block_tick, _closer_pending, ...) run on the pusher thread, which
never joins a frame, so a turn that ends mid-pass reaches them at the next cycle instead of after the tiers'
whole run, model calls included. Before the scoping, the warm-hit pin (2026-09-06) froze every tick job to the
pass-start world for as long as the tiers ran; before that pin, a cold first touch by a tick job did the same
for the session it parsed. The frame's own job is untouched: the judge stages share one world, and a pass
thread's touch pins what a tick job just read live.
SYNTHETIC fixtures only."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_tickframe", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781100000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


class TickJobUnderAFrame(unittest.TestCase):
    """_clear_done_working_notes lifts a session's working note once its last turn has ENDED with no open
    top goal; an open turn keeps it. That decision is the job's read of parsed_session, so it shows which
    world the job saw. Three threads, as in the kernel: the producer opens the frame, the pusher runs the
    tick job, and the test thread stands in for a tier (it joins the frame when a pass thread is needed)."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self._saved_state = jd.STATE                     # restored in tearDown: the shared judge's root is checked
        jd._rebind_state(self.td)
        self.path = self.td / (SID + ".jsonl")
        recs = [uline(T0, "start the work", "u1"),
                aline(T0 + 10, "Working on it now, first step underway.", "a1", "u1", stop="tool_use")]
        self.path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        jd.end_pass_frame(True)          # belt: never inherit a frame a crashed test left open
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()   # the cache-hit premise must not ride an earlier test's entry
        self.lifted = []
        self._saved = (km._working_notes, km._alive_sessions, km._open_top_goal, km._set_working_note,
                       km._suspended_after)
        km._working_notes = lambda: {SID: "owns the api worktree"}
        km._alive_sessions = lambda now, live: [{"sid": SID, "path": str(self.path)}]
        km._open_top_goal = lambda sid: False
        km._set_working_note = lambda sid, text: self.lifted.append((sid, text))
        km._suspended_after = lambda t: False

    def tearDown(self):
        jd.end_pass_frame(True)
        (km._working_notes, km._alive_sessions, km._open_top_goal, km._set_working_note,
         km._suspended_after) = self._saved
        jd._rebind_state(self._saved_state)

    def _append(self, rec):
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _on(self, name, fn, *a):
        """Run fn on a fresh thread called `name` and hand back its result (an exception re-raises here)."""
        out, err = [], []

        def run():
            try:
                out.append(fn(*a))
            except BaseException as e:
                err.append(e)
        t = threading.Thread(target=run, name=name)
        t.start(); t.join(10)
        self.assertFalse(t.is_alive(), "%s finished" % name)
        if err:
            raise err[0]
        return out[0]

    def _tick(self):
        return self._on("pusher", km._clear_done_working_notes, T0 + 100, {})

    def test_a_tick_job_reads_the_live_world_under_an_open_frame(self):
        warm = jd.parsed_session(SID, [str(self.path)], T0 + 100)     # frameless: fills the cache, turn open
        self.assertFalse(warm["turns"][-1]["ended"], "premise: the cached parse holds an open turn")
        self.assertTrue(self._on("producer", jd.begin_pass_frame), "the producer opens the pass frame")
        self._tick()                                                    # the pass's first touch of this sid: a cache HIT
        self.assertEqual(self.lifted, [], "premise: an open turn keeps the note")
        self.assertNotIn(SID, jd._frame["parses"], "a tick job is not a pass thread: it pins nothing")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        self._tick()
        self.assertEqual(self.lifted, [(SID, "")],
                         "mid-pass the job reads the live file: the ended turn lifts the claim at this cycle, not "
                         "after the tiers' run")
        self.assertNotIn(SID, jd._frame["parses"], "and still nothing pinned")

    def test_a_pass_thread_pins_what_the_tick_job_read_live_and_the_tick_job_keeps_reading_live(self):
        self.assertTrue(self._on("producer", jd.begin_pass_frame))
        self._tick()                                                    # live, unpinned: fills the cache
        self.assertEqual(self.lifted, [])
        self.assertFalse(jd.begin_pass_frame(), "this thread joins the frame: a tier's thread from here on")
        pinned = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(jd._frame["parses"].get(SID), pinned, "the pass thread's first touch pins the cached parse")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        self.assertIs(jd.parsed_session(SID, [str(self.path)], T0 + 100), pinned,
                      "the pass thread keeps the frozen world: the append is next pass's evidence for the judges")
        self.assertFalse(pinned["turns"][-1]["ended"])
        self._tick()
        self.assertEqual(self.lifted, [(SID, "")], "while the same append reaches the tick job now")
        self.assertIs(jd._frame["parses"].get(SID), pinned, "and the tick job's live read moved no pin")


if __name__ == "__main__":
    unittest.main()
