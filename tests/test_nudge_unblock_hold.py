#!/usr/bin/env python3
"""A repeated unblock is not a settlement: the nudge stands down until the closer's next word.

Five false status checks in one live weekend (2026-08-10/11) fired in the same window: a goal
blocked on the USER's decision was flipped to 'working' by a judge's unblock — the planner's
"new work filed on this branch", the unblocker's "answered in passing" — while the decision was
still outstanding; the nudge read the flip as a stall and status-checked an idle session that was
waiting on the user; the closer re-filed essentially the same block (or done) seconds-to-minutes
later. The card ping-ponged blocked→working→blocked with no user action — the cards-move rule's
named anti-pattern — and each 'working' window produced a nudge.

The existing ev_t guard can't catch it: those unblocks rule on turns romp HAS seen end, which the
2026-07-30 "considered verdict" reasoning deliberately treats as nudgeable — and for a goal's
FIRST unblock that reasoning is pinned doctrine (its own incident was a wrongly-gagged nudge,
tests/test_awaiting_same_trigger_wedge.py). The discriminator between the incidents is the diary:
every flap fire had a PRIOR judge unblock already on the goal. So the hold takes only a goal whose
newest judge row is an unblock with an earlier judge unblock on record — already ping-ponged once,
still unsettled — and holds it loudly (deferral record + backstop) until the closer's next word. A
USER's own unblock (an interrupt re-engage) never counts on either end.

Synthetic fixtures throughout (invented goal text, placeholder ids).
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_ubh", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_ubh", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
GID = SID + ":g4"
ARM_T = 1786400000          # the arm turn's trigger
BLOCK_T = ARM_T - 900       # the block predates the arm turn (its ask is old news to the ev_t guard)
UNBLOCK_T = ARM_T - 60      # ...and so does the unblock's evidence turn


def store(log, status="working"):
    return {"nodes": {GID: {"id": GID, "text": "Regroup the rows by speaker", "log": list(log)}},
            "status": {GID: status}}


BLOCK = {"ev_t": BLOCK_T, "src": "closer", "kind": "block", "at": BLOCK_T + 20,
         "why": "Two calls are yours: materialize the derived file, or contact the authors?"}
UNBLOCK_1 = {"ev_t": BLOCK_T - 300, "src": "planner", "kind": "unblock", "at": BLOCK_T - 290,
             "why": "new work filed on this branch"}
UNBLOCK_2 = {"ev_t": UNBLOCK_T, "src": "unblocker", "kind": "unblock", "at": UNBLOCK_T + 5,
             "why": "answered in passing: the thread pivoted"}
FLAP = [UNBLOCK_1, BLOCK, UNBLOCK_2]     # the live incident shape: already ping-ponged once, unsettled


class RepeatUnblockHoldsTheNudge(unittest.TestCase):
    def _run(self, log, status="working"):
        held = []
        keep = km._nudge_fire_list(store(log, status), [(GID, 1, False)],
                                   arm_t=ARM_T, seen_t=ARM_T, held=held)
        return keep, held

    def test_the_incident_shape_a_repeat_trailing_unblock_holds(self):
        keep, held = self._run(FLAP)
        self.assertEqual(keep, [])
        self.assertEqual([(f[0], why) for f, why, _ev in held], [(GID, jd.WHY_UNBLOCK_UNSETTLED)],
                         "the column already ping-ponged and the closer hasn't answered — never a stall")

    def test_a_goals_first_unblock_still_fires_the_pinned_doctrine(self):
        # the 2026-07-30 considered-verdict case (tests/test_awaiting_same_trigger_wedge.py): one
        # block, one unblock, goal working, session idle — a status check is exactly what is due
        keep, held = self._run([BLOCK, UNBLOCK_2])
        self.assertEqual([f[0] for f in keep], [GID])
        self.assertEqual(held, [])

    def test_the_closers_next_word_releases_it(self):
        # the closer answered and LEFT the goal working → adjudication settled → nudgeable again
        after = {"ev_t": UNBLOCK_T, "src": "closer", "kind": "awaiting", "at": UNBLOCK_T + 40, "lift": True}
        keep, held = self._run(FLAP + [after])
        self.assertEqual([f[0] for f in keep], [GID])
        self.assertEqual(held, [])

    def test_a_re_block_needs_no_hold_the_status_drop_already_covers_it(self):
        keep, held = self._run(FLAP + [dict(BLOCK, at=UNBLOCK_T + 90)], status="blocked")
        self.assertEqual((keep, held), ([], []), "resolved goals are dropped, never held (backstop safety)")

    def test_user_unblocks_never_count_on_either_end(self):
        # an interrupt re-engage is the event that re-ARMS the nudge; it neither holds as the trailing
        # row nor primes the repeat count as the earlier one
        user_unblock = {"ev_t": UNBLOCK_T, "src": "user", "kind": "unblock", "at": UNBLOCK_T + 5}
        keep, held = self._run([BLOCK, user_unblock])
        self.assertEqual([f[0] for f in keep], [GID])
        keep, held = self._run([dict(user_unblock, at=BLOCK_T - 200), BLOCK, UNBLOCK_2])
        self.assertEqual([f[0] for f in keep], [GID],
                         "a user unblock before the judge's first isn't a prior oscillation")
        self.assertEqual(held, [])

    def test_exempt_bookkeeping_after_the_unblock_does_not_release_the_hold(self):
        # a user gesture or romp row after the judge's unblock isn't the closer's word
        keep, held = self._run(FLAP + [
            {"ev_t": UNBLOCK_T + 10, "src": "user", "kind": "unblock", "at": UNBLOCK_T + 10},
            {"ev_t": UNBLOCK_T + 12, "src": "romp", "kind": "awaiting", "lift": True, "at": UNBLOCK_T + 12}])
        self.assertEqual(keep, [])
        self.assertEqual([why for _, why, _ev in held], [jd.WHY_UNBLOCK_UNSETTLED])

    def test_a_settled_diary_still_fires(self):
        keep, held = self._run(FLAP + [{"ev_t": UNBLOCK_T, "src": "planner", "kind": "done",
                                        "at": UNBLOCK_T + 30}])
        self.assertEqual([f[0] for f in keep], [GID], "a diary ending on a non-unblock is nudgeable")

    def test_the_hold_presents_as_in_flight_and_the_sweep_retires_it_on_the_closers_word(self):
        # stall_why_stands is gone (2026-08-13): every hold presents somewhere. Ours joins the
        # in-flight class — the Analyzing… swirl, never the stalled chip — and the sweep's own case
        # must sit ABOVE the class branch, whose no-judge-running event would retire it early.
        self.assertIn(jd.WHY_UNBLOCK_UNSETTLED, jd.WHY_IN_FLIGHT,
                      "the closer's next pass is romp's own review — never presented as a stall")
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py"), encoding="utf-8").read()
        sweep = src[src.index("def _deferral_sweep_tick"):]
        sweep = sweep[:sweep.index("\ndef ")]
        ours = sweep.index("why == jd.WHY_UNBLOCK_UNSETTLED")
        cls = sweep.index("why in jd.WHY_IN_FLIGHT")
        self.assertLess(ours, cls, "the specific retirement event must be checked before the class branch")
        self.assertIn('rows[-1].get("kind") != "unblock"', sweep,
                      "retired by the closer's next filed word, not by no-judge-running")

    def test_the_ev_t_hold_still_wins_first_and_carries_its_own_why(self):
        newer = {"ev_t": ARM_T + 50, "src": "closer", "kind": "block", "at": ARM_T + 60}
        keep, held = self._run(FLAP + [newer])
        self.assertEqual(keep, [])
        self.assertEqual([why for _, why, _ev in held], [jd.WHY_TURN_IN_FLIGHT])


class TheCallSiteDefersPerWhy(unittest.TestCase):
    def test_the_tick_records_each_held_goal_under_its_own_why(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py"), encoding="utf-8").read()
        self.assertIn("to_fire += [f for f, _why, _ev in held", src)
        self.assertIn("if _nudge_deferred_ok(f[0], _why, now, sid, ev_t=_ev or None)]", src)


if __name__ == "__main__":
    unittest.main()
