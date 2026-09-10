#!/usr/bin/env python3
"""Story time vs filing time: rulings ABOUT a turn always FILE after it starts (the user 2026-07-30).

The audited incident, all fixtures SYNTHETIC: the `web` session traced a missing template to a fleet
peer on TESTHOST, sent that session a postal question for the source, and went idle to wait. The
closer audited the follow-up turn and ruled the goal awaiting ("waiting on the TESTHOST session's
reply with the template source"). Four seconds later the planner, placing the SAME follow-up, filed
its bookkeeping "reopened (followup)" row. All three rows ride the follow-up's trigger (one ev_t);
only their filing times differ. Two independent mechanisms then failed the same way, because each
compared a row's FILING time (`at`) against a point in STORY time:

- the fold's reopen handler cleared the awaiting stamp ("the user spoke — the wait's story moved")
  because the planner's reopen SORTS after the closer's assert on the (ev_t, at) tie-break. But that
  reopen is the pipeline processing the very trigger the closer had already audited, not the user
  speaking again. With the stamp gone: no ⏳ chip, no awaiting nudge-exemption, no 6h backstop.
- _nudge_fire_list's arm guard then dropped every nudge for the goal, because the diary held rows
  FILED after the arm turn began — which is true of every row the judges file while auditing the arm
  turn itself. The session sat idle in Working indefinitely: no nudge, no failed-nudge escalation,
  no deferral record, nothing visible anywhere.

The discipline both fixes share: compare EVIDENCE to EVIDENCE. A reopen ends an awaiting stamp only
when its ev_t is STRICTLY newer than the stamp's; the arm guard stands down only for a verdict whose
ev_t is strictly newer than the arm turn. A ruling about the arm turn that leaves the goal working
IS the closer-gate's considered 'working' verdict — the definition of nudgeable, not staleness.
"""
import os
import random
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_sametrig", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_sametrig", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
G1 = SID + ":g1"
T = 1781100000                    # the follow-up turn's trigger — every row below is ABOUT this turn
WHY = "waiting on the TESTHOST session's reply with the template source"


def node(log):
    return {"id": G1, "text": "port the export template for the api session", "parentId": None,
            "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
            "t": T - 500, "mt": T - 100, "log": log}


def incident_log():
    """The audited shape verbatim (synthetic): user follow-up reopen, closer awaiting, planner reopen —
    one trigger, three filings."""
    return [
        {"ev_t": T, "src": "user", "kind": "reopen", "why": "reopened (optimistic)", "msg": True, "at": T},
        {"ev_t": T, "src": "closer", "kind": "awaiting", "why": WHY, "at": T + 150},
        {"ev_t": T, "src": "planner", "kind": "reopen", "why": "reopened (followup)", "at": T + 154},
    ]


class FoldSameTriggerReopen(unittest.TestCase):
    """A reopen clears the ⏳ stamp only when it is STRICTLY later in story time."""

    def test_the_pipelines_same_trigger_reopen_keeps_the_stamp(self):
        f = jd._fold_node(node(incident_log()))
        self.assertEqual(f["awaitingWhy"], WHY,
                         "the planner's bookkeeping reopen rides the trigger the closer audited — "
                         "it is not the user speaking, so the wait's story has not moved")
        self.assertEqual(f["awaitingAt"], T)
        self.assertEqual(f["state"], "open")

    def test_a_genuinely_later_reopen_still_clears(self):
        log = incident_log() + [{"ev_t": T + 600, "src": "user", "kind": "reopen",
                                 "why": "reopened (optimistic)", "msg": True, "at": T + 600}]
        f = jd._fold_node(node(log))
        self.assertIsNone(f["awaitingWhy"], "the user's NEXT message is the exact event that ends the wait")
        self.assertIsNone(f["awaitingAt"])

    def test_shuffle_invariance_survives_the_guard(self):
        want = jd._fold_node(node(incident_log()))
        log = incident_log()
        rng = random.Random(7)
        for _ in range(12):
            rng.shuffle(log)
            self.assertEqual(jd._fold_node(node(list(log))), want,
                             "ordering is reconstructed from (ev_t, at), never assumed")


class FireListArmEvidence(unittest.TestCase):
    """_nudge_fire_list stands down on newer EVIDENCE, not on newer FILINGS about the arm itself."""

    def _store(self, log):
        return {"rompUuid": SID, "seq": 1, "nodes": {G1: node(log)},
                "placements": {}, "status": {G1: "working"}}

    def test_rulings_about_the_arm_turn_keep_the_fire(self):
        # the sibling incident: the arm turn answered its own blocker in passing ("answered in the
        # thread"), the unblocker unblocked off that same turn, the goal stayed working and the
        # session idled — a status check is exactly what is due, yet the late-FILED rows gagged it
        log = [{"ev_t": T, "src": "planner", "kind": "block", "why": "which branch ships?", "at": T + 140},
               {"ev_t": T, "src": "unblocker", "kind": "unblock", "why": "answered in the thread", "at": T + 150}]
        self.assertEqual([f[0] for f in km._nudge_fire_list(self._store(log), [(G1, 1, False)], arm_t=T)],
                         [G1],
                         "a ruling about the arm turn that leaves the goal working IS the considered "
                         "'working' verdict — the definition of nudgeable")

    def test_the_incidents_awaiting_and_reopen_rows_keep_the_fire(self):
        # the wedged goal's actual diary tail; with the fold fixed the awaiting GATE holds upstream,
        # but the fire list itself must not read these same-trigger rows as a moved story either
        self.assertEqual([f[0] for f in km._nudge_fire_list(self._store(incident_log()),
                                                            [(G1, 1, False)], arm_t=T)], [G1])

    def test_evidence_from_a_newer_turn_still_drops(self):
        # the 2026-07-29 audited case, faithfully: the user's ANSWER opened a newer turn and the
        # unblock rode it — the "it looks stalled" read predates the answer, so the nudge stands down
        log = [{"ev_t": T - 600, "src": "planner", "kind": "block", "why": "which branch ships?", "at": T - 590},
               {"ev_t": T + 290, "src": "unblocker", "kind": "unblock", "why": "answered in the thread",
                "at": T + 300}]
        self.assertEqual(km._nudge_fire_list(self._store(log), [(G1, 1, False)], arm_t=T), [])


if __name__ == "__main__":
    unittest.main()
