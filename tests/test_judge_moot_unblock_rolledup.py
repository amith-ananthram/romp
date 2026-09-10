#!/usr/bin/env python3
"""The moot-block heal must actually CLEAR a rolled-up node's flags (the user 2026-07-14, the demo-video
card flapping Blocked↔Working). rollup_status heals any blocked+complete node with an evented unblock —
but record_verdict deliberately skips materializing a ROLLED-UP node's flags (roll-down owns that display
cache), so the heal appended an unblock event every pass WITHOUT ever clearing nd['blocked']/'blockWhy'.
The loop re-fired forever: the diary filled with identical unblocks until LOG_CAP truncated the node's real
history out, and raw-flag readers (ledger, the feed's blocked rollup) kept showing ⏸ against a folded state
that said open — successive pushes filed the card in different columns, the visible flapping.

The heal now clears the display cache itself when record_verdict's materialization skipped it — it runs
inside rollup, the same owner as roll-down, so the write is the owner doing its job. Pinned here: one pass
clears the flags with exactly ONE diary event, and a second pass appends NOTHING (idempotent). Synthetic
store only."""
import os
import unittest
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_mootheal", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _g73_shape(s):
    """The reproduced live shape: a COMPLETED top whose child is nodeComplete via roll-down (rolledUp=True)
    but still carries a stale blocked flag + blockWhy from a block whose history is already gone."""
    s["seq"] = 1
    top = {"id": SID + ":g1", "text": "demo video", "parentId": None, "nodeComplete": True,
           "blocked": False, "cleared": False, "trail": [], "t": T0,
           "log": [{"ev_t": T0 + 10, "src": "closer", "kind": "done", "why": "shipped", "at": T0 + 10}]}
    child = {"id": SID + ":g2", "text": "prototype clip first?", "parentId": SID + ":g1",
             "nodeComplete": True, "rolledUp": True, "blocked": True,
             "blockWhy": "want a prototype clip first?", "cleared": False, "trail": [], "t": T0,
             "log": []}
    s["nodes"] = {top["id"]: top, child["id"]: child}
    s["seq"] = 2
    return top, child


class MootUnblockOnRolledUpNode(unittest.TestCase):
    def test_one_pass_clears_the_flags_with_one_event(self):
        s = _store()
        top, child = _g73_shape(s)
        jd.rollup_status(s, session_closed=True)
        nd = s["nodes"][child["id"]]
        self.assertFalse(nd.get("blocked"), "the heal must CLEAR the rolled-up node's raw blocked flag")
        self.assertNotIn("blockWhy", nd, "the stale question goes with the block")
        unblocks = [e for e in nd.get("log") or [] if e.get("kind") == "unblock"]
        self.assertEqual(len(unblocks), 1, "healed ONCE — the evented clear, not a per-pass spam")

    def test_second_pass_appends_nothing(self):
        # the live failure: 64 identical unblocks (LOG_CAP) truncated the node's real history out while the
        # flag stayed set — every subsequent pass must be a no-op once the first has healed.
        s = _store()
        top, child = _g73_shape(s)
        jd.rollup_status(s, session_closed=True)
        n_after_first = len(s["nodes"][child["id"]].get("log") or [])
        jd.rollup_status(s, session_closed=True)
        jd.rollup_status(s, session_closed=True)
        self.assertEqual(len(s["nodes"][child["id"]].get("log") or []), n_after_first,
                         "repeat rollups append no further events — the oscillation is gone")

    def test_non_rolledup_blocked_complete_node_still_heals_via_the_diary(self):
        # the pre-existing path (record_verdict materializes a NON-rolled-up node) keeps working unchanged
        s = _store()
        top, child = _g73_shape(s)
        child2 = {"id": SID + ":g3", "text": "plain stale block", "parentId": top["id"],
                  "nodeComplete": True, "blocked": True, "blockWhy": "answered elsewhere",
                  "cleared": False, "trail": [], "t": T0, "log": []}
        s["nodes"][child2["id"]] = child2
        jd.rollup_status(s, session_closed=True)
        self.assertFalse(s["nodes"][child2["id"]].get("blocked"))


if __name__ == "__main__":
    unittest.main()
