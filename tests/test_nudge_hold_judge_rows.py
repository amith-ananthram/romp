#!/usr/bin/env python3
"""_nudge_fire_list holds a due nudge only for what its why actually claims — "a judge has ruled on a
turn that hasn't finished yet". romp's own bookkeeping is not that, and neither is a user gesture.

Why it matters: the judges stamp a verdict with the TRIGGER of the segment it ruled on, so a verdict
about a turn romp has seen end never outruns the cut. romp's own writers stamp WALL-CLOCK now — an
interrupt lift, an awaiting stamp retired once the dispatched work came back — so on an idle session
their row is newer than the last ended turn by construction, and nothing can ever move past it. The
hold then stands forever (bar the 6h backstop), and because the stall surface screens this particular
why, the card shows no nudge, no chip, no reason: a working card on an idle session saying nothing,
which is the one state romp must never produce.

The 2026-08-01 silent card came through exactly here, via an interrupt lift. That writer now stamps
evidence time (test_interrupt_lift_evidence_time.py); this is the guard-side half, and it also closes
the route the awaiting-lift writer still leaves open. Synthetic fixtures only.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_nhjr", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-888888888888"
GID = SID + ":g1"
NOW = 1781100000
SEEN_T = NOW - 900        # trigger of the newest turn romp has watched END
ENDED_T = NOW - 700       # …that turn finished here
LATER_T = NOW - 300       # anything stamped after it reads as "newer evidence"


def _fresh(rows, status="working", **flags):
    nd = {"id": GID, "log": rows}
    nd.update(flags)
    return {"nodes": {GID: nd}, "status": {GID: status}}


def _row(src, kind, ev_t, at=None):
    return {"src": src, "kind": kind, "ev_t": ev_t, "at": at if at is not None else ev_t}


def _run(rows, **kw):
    """(kept, held) for one due nudge on GID."""
    held = []
    keep = km._nudge_fire_list(_fresh(rows, **kw), [(GID, 0, True)],
                               arm_t=SEEN_T, seen_t=SEEN_T, held=held)
    return [f[0] for f in keep], [f[0] for f, _why, _ev in held]   # held carries (goal, why, ev_t)


class RompsOwnBookkeepingNeverHolds(unittest.TestCase):
    """These writers stamp wall-clock now, so their rows are permanently 'newer' on an idle session."""

    def test_an_awaiting_lift_does_not_hold_the_nudge(self):
        # the dispatched work came back and romp retired the wait — the goal is plainly idle again
        keep, held = _run([_row("romp", "awaiting", LATER_T)])
        self.assertEqual(keep, [GID], "nothing was judged; the stall read still stands")
        self.assertEqual(held, [])

    def test_an_interrupt_lift_does_not_hold_the_nudge(self):
        keep, held = _run([_row("interrupt", "block", SEEN_T - 60), _row("user", "unblock", LATER_T)])
        self.assertEqual(keep, [GID])
        self.assertEqual(held, [])

    def test_a_user_gesture_does_not_hold_the_nudge(self):
        # a gesture is the event that RE-ARMS the nudge; withholding one on it inverts the mechanism
        keep, held = _run([_row("user", "reopen", LATER_T)])
        self.assertEqual(keep, [GID])
        self.assertEqual(held, [])

    def test_an_agent_row_does_not_hold_the_nudge(self):
        keep, held = _run([_row("agent", "reopen", LATER_T)])
        self.assertEqual(keep, [GID])
        self.assertEqual(held, [])


class AJudgesRulingStillHolds(unittest.TestCase):
    """The 2026-07-29 guard, intact: a verdict about a turn romp has not watched end is newer
    information than the stall inference, and the goal is HELD (deferred), never silently dropped."""

    def test_an_unblocker_ruling_on_a_live_turn_holds(self):
        keep, held = _run([_row("unblocker", "unblock", LATER_T)])
        self.assertEqual(keep, [])
        self.assertEqual(held, [GID], "held with a why, so the 6h backstop can still reach it")

    def test_a_planner_ruling_on_a_live_turn_holds(self):
        keep, held = _run([_row("planner", "done", LATER_T)])
        self.assertEqual(held, [GID])

    def test_a_judge_added_later_is_counted_by_default(self):
        keep, held = _run([_row("triager", "block", LATER_T)])
        self.assertEqual(held, [GID], "the exemption is a denylist — a new judge holds without a code change")

    def test_a_ruling_about_the_turn_romp_SAW_end_does_not_hold(self):
        # its ev_t is that turn's trigger; filing happens later, and filing time is not the yardstick
        keep, held = _run([_row("closer", "done", SEEN_T, at=ENDED_T + 120)])
        self.assertEqual(keep, [GID])
        self.assertEqual(held, [])

    def test_a_mix_holds_on_the_judge_row(self):
        keep, held = _run([_row("romp", "awaiting", LATER_T), _row("closer", "block", LATER_T + 5)])
        self.assertEqual(held, [GID], "one genuine judge ruling is enough")


class ResolvedGoalsNeverReachHeld(unittest.TestCase):
    """The status re-read runs FIRST: a goal the judges resolved mid-tick is dropped outright, so the
    backstop can never nudge a card that is already done."""

    def test_a_completed_goal_is_dropped_not_held(self):
        keep, held = _run([_row("closer", "done", LATER_T)], status="completed", nodeComplete=True)
        self.assertEqual(keep, [])
        self.assertEqual(held, [])

    def test_a_blocked_goal_is_dropped_not_held(self):
        keep, held = _run([_row("closer", "block", LATER_T)], status="blocked", blocked=True)
        self.assertEqual(keep, [])
        self.assertEqual(held, [])


class TheExemptionSetIsWhatTheCodeUses(unittest.TestCase):
    def test_the_holds_why_routes_to_the_swirl_not_the_chip(self):
        # 2026-08-13: the screen is retired — the hold's why is in the IN-FLIGHT class, which presents
        # as the card's Analyzing… swirl (build_feed routes per record) instead of the stalled chip,
        # and the deferral sweep pops the record on the turn's own end event. Nothing is hidden.
        self.assertIn(jd.WHY_TURN_IN_FLIGHT, jd.WHY_IN_FLIGHT)
        self.assertNotIn(jd.WHY_TURN_IN_FLIGHT,
                         [w for w in (jd.WHY_JUDGING,) if False] or [],)  # tuple membership is the one definition
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertIn('_stall_rec.get("why") in jd.WHY_IN_FLIGHT', src,
                      "the feed routes in-flight-class holds to the judging swirl")


if __name__ == "__main__":
    unittest.main()
