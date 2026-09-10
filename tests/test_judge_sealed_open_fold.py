#!/usr/bin/env python3
"""An INTERIOR done verdict must fold its open descendants (the user 2026-07-14, the nimbus card:
16 "uncompleted tasks", four of them zombies). A landed done on a sub seals its subtree out of every
judge menu — open_menu (planner), _turn_menu (closer), _blocked_sub_candidates (unblocker) all skip
nodes under a complete ancestor — so a child left open at that moment could never be judged again: it
sat "open" on the card forever. rollup_status's roll-down only healed when the whole CARD resolved
(status completed/cleared); a done sub under a still-working card never propagated.

The heal extends the same roll-down (nodeComplete + rolledUp display cache, eventless, agentTask-open
subtrees exempt) to interior nodes with a landed done verdict. Interior-only: a complete-but-unsettled
TOP keeps the settle gate's timing (children fold when the card completes, as before). Synthetic store
only."""
import os
import unittest
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_sealedfold", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _node(g, text, parent=None, done=False, **extra):
    nd = {"id": SID + ":" + g, "text": text, "parentId": (SID + ":" + parent) if parent else None,
          "nodeComplete": done, "blocked": False, "cleared": False, "trail": [], "t": T0,
          "log": [{"ev_t": T0 + 10, "src": "closer", "kind": "done", "why": "finished",
                   "at": T0 + 10}] if done else []}
    nd.update(extra)
    return nd


def _nimbus_shape(s):
    """The live shape: an OPEN top card (held open by a genuinely open sibling sub, like the real
    card's connectivity work), a sub with a landed closer done, and an open child under that sub —
    the zombie ("Documented where the cache file lives" under a done "Assess firewall...")."""
    s["nodes"] = {n["id"]: n for n in [
        _node("g1", "Get the board connected"),
        _node("g2", "Assess firewall downsides and locate token", parent="g1", done=True),
        _node("g3", "Documented where the cache file lives", parent="g2"),
        _node("g8", "Fix connectivity between laptop and device", parent="g1"),
    ]}
    s["seq"] = 8


class InteriorDoneFoldsOpenDescendants(unittest.TestCase):
    def test_zombie_child_folds_while_the_card_stays_working(self):
        s = _store()
        _nimbus_shape(s)
        jd.rollup_status(s, session_closed=True)
        z = s["nodes"][SID + ":g3"]
        self.assertTrue(z.get("nodeComplete"), "the open child under a done sub folds with it")
        self.assertTrue(z.get("rolledUp"), "display-cache treatment, so _reopen can un-resolve it")
        self.assertEqual(s["status"][SID + ":g1"], "working", "the card itself is untouched")

    def test_fold_is_eventless_and_idempotent(self):
        # roll-down is cache maintenance: no diary events on the folded child, and repeat passes
        # change nothing (the moot-heal spam failure mode must not come back in this loop).
        s = _store()
        _nimbus_shape(s)
        jd.rollup_status(s, session_closed=True)
        z = s["nodes"][SID + ":g3"]
        self.assertEqual(z.get("log") or [], [], "the fold writes flags, never diary events")
        jd.rollup_status(s, session_closed=True)
        jd.rollup_status(s, session_closed=True)
        self.assertEqual(s["nodes"][SID + ":g3"].get("log") or [], [])
        self.assertTrue(s["nodes"][SID + ":g3"].get("nodeComplete"))

    def test_deep_zombies_fold_too(self):
        # the seal covers the WHOLE subtree, so the heal must reach grandchildren
        s = _store()
        _nimbus_shape(s)
        s["nodes"][SID + ":g4"] = _node("g4", "a grandchild left open", parent="g3")
        s["seq"] = 4
        jd.rollup_status(s, session_closed=True)
        self.assertTrue(s["nodes"][SID + ":g4"].get("nodeComplete"))

    def test_blocked_zombie_loses_its_stale_block_in_the_same_pass(self):
        # a folded child that also carried a stale blocked flag gets the evented moot-unblock heal
        # (the fold runs before the stale-block loop, so the flag clears in one pass — no flap window)
        s = _store()
        _nimbus_shape(s)
        z = s["nodes"][SID + ":g3"]
        z["blocked"] = True
        z["blockWhy"] = "want option A or B?"
        jd.rollup_status(s, session_closed=True)
        z = s["nodes"][SID + ":g3"]
        self.assertTrue(z.get("nodeComplete"))
        self.assertFalse(z.get("blocked"))
        self.assertNotIn("blockWhy", z)

    def test_open_child_under_an_open_parent_is_untouched(self):
        # the born-done population (open sub under an OPEN ancestor) is the closer's to judge, not
        # the fold's — only a landed done seals a subtree
        s = _store()
        _nimbus_shape(s)
        s["nodes"][SID + ":g5"] = _node("g5", "Explained root cause of the exposure", parent="g1")
        s["seq"] = 5
        jd.rollup_status(s, session_closed=True)
        self.assertFalse(s["nodes"][SID + ":g5"].get("nodeComplete"))

    def test_agenttask_open_subtree_is_exempt(self):
        # the authoritative tier: the agent's own to-do list saying "still open" outranks the done
        # verdict (is_complete already treats the parent as not-complete), so nothing under it folds
        s = _store()
        _nimbus_shape(s)
        s["nodes"][SID + ":g4"] = _node("g4", "live to-do item", parent="g2",
                                        agentTask={"key": "k1", "status": "open"})
        s["seq"] = 4
        jd.rollup_status(s, session_closed=True)
        self.assertFalse(s["nodes"][SID + ":g4"].get("nodeComplete"))
        self.assertFalse(s["nodes"][SID + ":g3"].get("nodeComplete"),
                         "the whole branch stays unfolded while the agent holds it open")

    def test_unsettled_top_keeps_the_settle_gates_timing(self):
        # a top with its own done verdict that is still the session FOCUS must not fold its children
        # early — the card completes (and rolls down) only at settlement, exactly as before
        s = _store()
        s["nodes"] = {n["id"]: n for n in [
            _node("g1", "the card", done=True),
            _node("g2", "a trailing open step", parent="g1"),
        ]}
        s["seq"] = 2
        s["lastNode"] = SID + ":g2"                    # focus is inside this card → unsettled
        jd.rollup_status(s, session_closed=False)
        self.assertEqual(s["status"][SID + ":g1"], "working")
        self.assertFalse(s["nodes"][SID + ":g2"].get("nodeComplete"),
                         "no early fold: the top's roll-down still waits for settlement")


if __name__ == "__main__":
    unittest.main()
