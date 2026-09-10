#!/usr/bin/env python3
"""Interrupted → Blocked (the user 2026-07-07, extending the stalled rule): the user stopped the session
mid-turn, nothing moves until they speak, so the focus goal records a block verdict (src "interrupt") and
reaches Needs-you via the normal ladder. Their next message lifts OUR block with an explicit unblock event
(the same event that re-arms auto-nudge); a REAL judge verdict recorded in between owns the card and
stays. XDG isolation before the kernel loads. Synthetic fixtures only."""
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
kern = load_source("romp_kernel_intrblk", os.path.join(BIN, "romp-kernel"))
jd = kern.jd

# A SID of this file's own: the judge module is process-shared across every kernel test copy (its
# import is sys.modules-cached), so the append-only overrides journal is shared too — a same-SID,
# same-gid block journaled by another test file would replay into this one's loads (and vice versa).
SID = "11111111-2222-3333-4444-777777777777"
GID = SID + ":g1"
NOW = int(time.time())
STOP_T = NOW - 120        # transcript time of the stop itself
REENGAGE_T = NOW - 60     # trigger of the turn the re-engagement opened


def _seed(status="working"):
    store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
             "lastNode": GID,
             "nodes": {GID: {"id": GID, "text": "Ship the widget", "parentId": None,
                             "nodeComplete": False, "blocked": False, "cleared": False,
                             "trail": [], "t": NOW - 600, "mt": NOW - 300}}}
    jd.rollup_status(store, False)
    jd.save_goals(SID, store)
    return store


class InterruptBlocks(unittest.TestCase):
    def setUp(self):
        kern._write_auto_nudge({"enabled": True, "nudged": {}})
        # each test re-seeds the store from scratch; the append-only overrides journal would replay a
        # previous test's journaled interrupt block into the fresh store as a phantom row
        fp = jd._overrides_dir() / (SID + ".jsonl")
        if fp.exists():
            fp.unlink()

    def test_interrupt_blocks_the_focus_goal_via_the_diary(self):
        _seed()
        gid = kern._record_interrupt_block(SID, STOP_T)
        self.assertEqual(gid, GID)
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][GID], "blocked", "the stopped focus goal needs the user")
        ev = [e for e in st["nodes"][GID]["log"] if e["kind"] == "block"]
        self.assertEqual([e["src"] for e in ev], ["interrupt"])
        self.assertIn("waiting on your next instruction", ev[0]["why"])

    def test_reengage_lifts_our_block_with_an_unblock_event(self):
        _seed()
        kern._record_interrupt_block(SID, STOP_T)
        kern._lift_interrupt_block(SID, GID, REENGAGE_T)
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][GID], "working", "the user spoke → the block lifts")
        kinds = [(e["src"], e["kind"]) for e in st["nodes"][GID]["log"]]
        self.assertIn(("user", "unblock"), kinds, "lifted by an explicit event — the diary stays the authority")

    def test_a_real_judge_block_recorded_since_stays(self):
        _seed()
        kern._record_interrupt_block(SID, STOP_T)
        st = jd.load_goals(SID)
        jd.record_verdict(st, st["nodes"][GID], "closer", "block", NOW + 10, why="pick a name")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        kern._lift_interrupt_block(SID, GID, REENGAGE_T)
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][GID], "blocked", "a genuine verdict owns the card; the lift is a no-op")

    def test_no_working_focus_means_nothing_to_block(self):
        store = _seed()
        jd.record_verdict(store, store["nodes"][GID], "closer", "done", NOW - 10, why="shipped")
        store["nodes"][GID]["nodeComplete"] = True   # (a hand-flip alone would be reverted by the fold)
        jd.rollup_status(store, True)
        jd.save_goals(SID, store)
        self.assertIsNone(kern._record_interrupt_block(SID, STOP_T))

    def test_marker_bookkeeping(self):
        kern._set_intr_blocked(SID, GID)
        self.assertEqual(kern._intr_blocked(SID), GID)
        kern._set_intr_blocked(SID, None)
        self.assertIsNone(kern._intr_blocked(SID))

    def test_the_marker_is_verified_against_the_store_not_trusted(self):
        # the marker means "this episode already surfaced the stop" — but the world moves under it
        # (the user 2026-08-08): _intr_block_stands is the tick's check that the block still HOLDS
        _seed()
        self.assertFalse(kern._intr_block_stands(SID, GID), "no block recorded → nothing stands")
        self.assertFalse(kern._intr_block_stands(SID, SID + ":g99"),
                         "a vanished node (cleared + archived by compaction) cannot stand")
        kern._record_interrupt_block(SID, STOP_T)
        self.assertTrue(kern._intr_block_stands(SID, GID))
        st = jd.load_goals(SID)
        jd.record_verdict(st, st["nodes"][GID], "unblocker", "unblock", STOP_T + 6,
                          why="answered in passing: picked the work back up")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        self.assertFalse(kern._intr_block_stands(SID, GID),
                         "the judges lifted it — the marker no longer holds a card")

    def test_record_stands_down_under_newer_judge_rows_without_appending(self):
        # a block stamped at the old stop would fold UNDER rows the judges filed off newer turns — a
        # silent no-op retried every push. It must refuse WITHOUT appending, then land the moment the
        # quiet's evidence (the newest settled turn) outranks the diary (the user 2026-08-08).
        _seed()
        st = jd.load_goals(SID)
        jd.record_verdict(st, st["nodes"][GID], "interrupt", "block", STOP_T, why=jd.INTERRUPT_BLOCK_WHY)
        jd.record_verdict(st, st["nodes"][GID], "unblocker", "unblock", STOP_T + 6,
                          why="answered in passing: picked the work back up")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        rows = len(jd.load_goals(SID)["nodes"][GID]["log"])
        self.assertIsNone(kern._record_interrupt_block(SID, STOP_T),
                          "evidence older than the judges' newest row — stand down")
        st = jd.load_goals(SID)
        self.assertEqual(len(st["nodes"][GID]["log"]), rows,
                         "refused WITHOUT appending — no diary growth at push cadence")
        self.assertEqual(st["status"][GID], "working")
        # an injected turn settled later with the user still silent → the quiet's evidence is newer
        self.assertEqual(kern._record_interrupt_block(SID, STOP_T + 300), GID)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "blocked")


if __name__ == "__main__":
    unittest.main()
