#!/usr/bin/env python3
"""A FRESH block must survive subtree-completion (the user 2026-07-15, the g78 TESTHOST card and the
g86 diagnosis card). The closer's one reply often pairs "block the TOP on the user's answer" with
"done its record subs" (born-done records like "Diagnosed …"), all anchored to the same turn. The
bottom-up all-children-complete path then made the card read complete, and two layers erased the
judges' verdict seconds after it landed:
  - any_blocked's is_complete short-circuit → status filed the card as done, not needs-you;
  - the raw-flag moot heal ("moot: the subtree is complete") cleared nd["blocked"] — and when its
    unblock event folded BEFORE the block it meant to clear, it re-appended every pass until
    LOG_CAP truncated the block verdict itself out of the diary (the g86 settle/unblock flood).
The rule: completion evidence moots a block only when it is genuinely NEWER. Subtree-done ties
favor the block (it sits on the node itself; same-turn dones are the records it arrived with);
an ANCESTOR's own top-down done discharges the subtree even on a tie (the roll-down rule).
Synthetic stores only."""
import os
import unittest
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_freshblock", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000
T1 = T0 + 100


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _node(g, text, parent=None, log=None):
    return {"id": SID + ":" + g, "text": text,
            "parentId": (SID + ":" + parent) if parent else None,
            "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
            "t": T0, "log": log or []}


def _row(kind, ev, src="closer", why=None):
    return {"ev_t": ev, "src": src, "kind": kind, **({"why": why} if why else {}), "at": ev}


def _ask_card(s, block_ev=T1, done_ev=T1):
    """The g78/g86 shape: a top the closer BLOCKED on the user, whose only child is a record sub
    the same reply closed as done."""
    s["nodes"] = {n["id"]: n for n in [
        _node("g1", "Resolve the TESTHOST push refusal",
              log=[_row("block", block_ev, why="say whether to also update the fallback")]),
        _node("g2", "Diagnosed stale leftovers as the cause", parent="g1",
              log=[_row("done", done_ev, why="cause identified and explained")]),
    ]}
    s["seq"] = 2


class FreshBlockSurvivesSubtreeComplete(unittest.TestCase):
    def test_ask_card_with_born_done_record_stays_blocked(self):
        s = _store()
        _ask_card(s)
        jd.rollup_status(s, session_closed=True)
        top = s["nodes"][SID + ":g1"]
        self.assertTrue(top.get("blocked"), "the same-turn block is the latest ruling — never moot")
        self.assertIn("blockWhy", top, "the ask the user still owes an answer to survives")
        self.assertEqual(s["status"][SID + ":g1"], "blocked",
                         "the card files as needs-you, not completed (the g86 'done while blocked on you')")
        self.assertFalse(top.get("settledDone"), "no settlement: the card must not enter Completed")
        kinds = [e.get("kind") for e in top["log"]]
        self.assertNotIn("unblock", kinds, "the moot heal never touches a fresh block")
        self.assertNotIn("settle", kinds)

    def test_no_diary_flood_across_passes(self):
        # the g86 flood: an ineffective heal re-appended settle/unblock pairs every pass until
        # LOG_CAP truncated the block verdict itself out of the diary
        s = _store()
        _ask_card(s)
        jd.rollup_status(s, session_closed=True)
        rows = len(s["nodes"][SID + ":g1"]["log"])
        for _ in range(5):
            jd.rollup_status(s, session_closed=True)
        self.assertEqual(len(s["nodes"][SID + ":g1"]["log"]), rows,
                         "repeat passes append nothing — the block verdict can never be truncated away")
        self.assertEqual(s["status"][SID + ":g1"], "blocked", "and the status never flaps")

    def test_strictly_newer_completion_still_moots_the_block(self):
        # the heal's original job is untouched: completion evidence newer than the block clears it.
        # Since the verdicts-only flip (2026-07-15) a child's done can't complete the parent, so the
        # newer completion here is an explicit done on the BLOCKED NODE'S PARENT — the roll-down then
        # folds the child, and the heal clears its older block in the same pass.
        s = _store()
        s["nodes"] = {n["id"]: n for n in [
            _node("g1", "the card", log=[_row("done", T1 + 60, why="whole ask discharged later")]),
            _node("g2", "a step that asked something earlier", parent="g1",
                  log=[_row("block", T1, why="want A or B?")]),
        ]}
        s["seq"] = 2
        jd.rollup_status(s, session_closed=True)
        kid = s["nodes"][SID + ":g2"]
        self.assertFalse(kid.get("blocked"), "newer completion evidence moots the older block")
        self.assertEqual(s["status"][SID + ":g1"], "completed")
        unblocks = [e for e in kid["log"] if e.get("kind") == "unblock"]
        self.assertEqual(len(unblocks), 1, "the heal lands in ONE event")
        self.assertGreaterEqual(unblocks[0].get("ev_t") or 0, T1,
                                "the unblock's evidence floors at the block's — it must fold AFTER it")
        before = len(kid["log"])
        jd.rollup_status(s, session_closed=True)
        self.assertEqual(len(s["nodes"][SID + ":g2"]["log"]), before, "idempotent: no re-append")

    def test_ancestor_done_tie_moots_a_child_block(self):
        # a top-down done on the ancestor discharges the whole subtree, same-turn child-blocks
        # included (the roll-down rule) — ancestor ties favor the DONE, unlike subtree ties
        s = _store()
        s["nodes"] = {n["id"]: n for n in [
            _node("g1", "the card", log=[_row("done", T1, why="whole ask discharged")]),
            _node("g2", "a trailing step that asked something", parent="g1",
                  log=[_row("block", T1, why="want A or B?")]),
        ]}
        s["seq"] = 2
        jd.rollup_status(s, session_closed=True)
        self.assertEqual(s["status"][SID + ":g1"], "completed")
        self.assertFalse(s["nodes"][SID + ":g2"].get("blocked"),
                         "the ancestor's own done moots the same-turn child block")


if __name__ == "__main__":
    unittest.main()
