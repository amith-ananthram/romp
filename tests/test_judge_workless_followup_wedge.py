#!/usr/bin/env python3
"""A workless FOLLOW-UP segment must still be judged once its turn ends (the user 2026-08-08, the
beacon g10 card).

A card reply sets the fold's msg-reopen latch (followupPending): the card is pinned Working and the
nudge gate defers on "your reply is still being judged" until a judge verdict lands on the top. The
unit builder, though, skipped any ended segment with no assistant work ("files nothing" — the
anti-confabulation rule for API-error turns), and a follow-up is WORK-RUN ONLY — so a reply that
landed as its own turn while the response opened the NEXT turn left the reply's segment workless
forever. Nothing ever judged it, the latch's deciding event could never arrive, and the card sat
wedged: pinned Working on an idle session, the nudge gate frozen, the feed's Stalled section holding
it. Same law as the moot-#p wedge (PR #69): a unit the pipeline declines to judge must not leave a
latch waiting on that judgment.

The fix: a follow-up segment's work-run fires at TURN END (the event the has-work proxy was
approximating), workless or not — the planner's own reopen/dismiss row is the latch release. What a
workless segment cannot evidence is COMPLETION, so its done ops are stripped by the same guard that
strips spliced ones (_strip_unevidenced_dones); the turn-level closer keeps done authority.

SYNTHETIC fixtures only: placeholder UUIDs, the neutral notes-api demo domain.
"""
import json
import os
import tempfile
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
jd = load_source("romp_judge_workless_fu", os.path.join(BIN, "romp-judge"))

NOW = 1781200000
SID = "11111111-2222-3333-4444-777777777777"
GID = SID + ":g1"
T0 = NOW - 3600          # the original ask
T_R = NOW - 1800         # the user's card reply (its own turn, never answered in it)
T_W = NOW - 1700         # the later turn where the actual response work happened


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


REPLY = "Looks right — keep the single worker and ship the notes-api deploy as is."
SKIP = '{"ops":[{"why":"nothing to file","do":"skip"}]}'
FU_DONE = '{"ops":[{"why":"the reply says it is finished","do":"done","goal":1}]}'
FU_PIVOT = ('{"ops":[{"why":"the reply asks for something new","do":"mint",'
            '"text":"Add a health check to the notes-api deploy"}]}')


class WorklessFollowupWedge(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        # an OPEN top the user just replied to: the optimistic msg-reopen is the latch
        self.store = {"rompUuid": SID, "seq": 1, "placementsV": jd.PLACEMENTS_V,
                      "nodes": {GID: {"id": GID, "text": "Ship the notes-api deploy", "parentId": None,
                                      "nodeComplete": False, "blocked": False, "cleared": False,
                                      "trail": [], "t": T0, "mt": T0,
                                      "log": [{"ev_t": T_R, "src": "user", "kind": "reopen",
                                               "why": "reopened (optimistic)", "msg": True, "at": T_R}]}},
                      "placements": {}, "status": {}, "lastNode": GID}
        jd.save_goals(SID, self.store)
        self._saved = (jd.plan_llm, jd.opener_llm, jd._group_store)
        jd.opener_llm = lambda *a, **k: ""
        jd._group_store = lambda *a, **k: None
        self._set_followup_plan(SKIP)

    def tearDown(self):
        jd.plan_llm, jd.opener_llm, jd._group_store = self._saved

    def _set_followup_plan(self, fu_plan):
        """plan_llm stub: `fu_plan` for the follow-up unit, SKIP for everything else."""
        self.fu_calls = []
        def fake(*a, **k):
            if k.get("followup"):
                self.fu_calls.append(k)
                return fu_plan
            return SKIP
        jd.plan_llm = fake

    def _transcript(self, reply_answered=False):
        """Turn 1: the ask, worked and ended. The reply lands next with NO assistant response of its
        own (unless reply_answered); the actual follow-through happens under a later prompt — the
        beacon shape."""
        recs = [
            uline(T0, "Ship the notes-api deploy", "u1"),
            aline(T0 + 60, "Deployed to staging; single worker for now.", "a1", "u1"),
            uline(T_R, REPLY + "<!-- romp-goal-id: %s -->" % GID, "r1", "a1"),
        ]
        parent = "r1"
        if reply_answered:
            recs.append(aline(T_R + 20, "Shipping it as is now — done.", "ar1", "r1"))
            parent = "ar1"
        recs += [
            uline(T_W, "also bump the client pin", "u2", parent),
            aline(T_W + 60, "Pinned and pushed.", "a2", "u2"),
        ]
        path = os.path.join(self.td, SID + ".jsonl")
        open(path, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        return path

    def _fold(self):
        nodes = jd.load_goals(SID)["nodes"]
        return jd._fold_node(nodes[GID]), nodes

    def _reply_seg_placed(self, store):
        return any(("%d:" % T_R) in k for k in store["placements"])

    # ---- the wedge ----
    def test_workless_followup_is_judged_and_releases_the_latch(self):
        path = self._transcript()
        f, _ = self._fold()
        self.assertTrue(f["pending"], "the msg-reopen latch is armed before the pass")
        jd._plan_session(SID, path, NOW)
        store = jd.load_goals(SID)
        self.assertTrue(self._reply_seg_placed(store),
                        "the reply's segment is processed once its turn has ended")
        f, _ = self._fold()
        self.assertFalse(f["pending"],
                         "a planner row landed on the top — the latch's deciding event arrived")
        self.assertTrue(self.fu_calls, "…via the follow-up unit, not the free path")

    # ---- done authority stays with real work ----
    def test_workless_followup_done_is_stripped_but_still_releases(self):
        path = self._transcript()
        self._set_followup_plan(FU_DONE)
        jd._plan_session(SID, path, NOW)
        f, nodes = self._fold()
        self.assertFalse(nodes[GID].get("nodeComplete"),
                         "a workless reply can prove intent, never completion")
        self.assertFalse(f["pending"], "the strip still leaves the latch released")
        errs = (jd.STATE / "judge-errors.jsonl").read_text()
        self.assertIn("workless-done", errs, "the drop is loud, never silent")

    def test_answered_followup_done_still_carries_through(self):
        path = self._transcript(reply_answered=True)
        self._set_followup_plan(FU_DONE)
        jd._plan_session(SID, path, NOW)
        f, nodes = self._fold()
        self.assertTrue(nodes[GID].get("nodeComplete"),
                        "a reply answered in its own turn keeps the g142 carry-through")
        self.assertFalse(f["pending"])

    # ---- the pivot shape ----
    def test_workless_followup_pivot_dismisses_the_latch(self):
        path = self._transcript()
        self._set_followup_plan(FU_PIVOT)
        jd._plan_session(SID, path, NOW)
        f, nodes = self._fold()
        self.assertFalse(f["pending"], "the pivot's dismiss answers the msg-reopen")
        self.assertFalse(nodes[GID].get("nodeComplete"), "the cited goal is unchanged")
        self.assertTrue(any(n.get("parentId") is None and n["id"] != GID for n in nodes.values()),
                        "the reply's new thread got its own top")

    # ---- nudges keep their own design ----
    def test_workless_nudge_still_files_nothing(self):
        """An unanswered NUDGE stays re-nudgeable (its own machinery escalates it); the follow-up fix
        must not sweep nudge segments into judgment."""
        nudge = ("Where does this stand?<!-- romp-injected --><!-- romp-auto -->"
                 "<!-- romp-goal-id: %s -->" % GID)
        recs = [
            uline(T0, "Ship the notes-api deploy", "u1"),
            aline(T0 + 60, "Deployed to staging.", "a1", "u1"),
            uline(T_R, nudge, "n1", "a1"),
            uline(T_W, "also bump the client pin", "u2", "n1"),
            aline(T_W + 60, "Pinned and pushed.", "a2", "u2"),
        ]
        path = os.path.join(self.td, SID + ".jsonl")
        open(path, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._plan_session(SID, path, NOW)
        self.assertFalse(self.fu_calls, "a nudge is never a follow-up unit")
        store = jd.load_goals(SID)
        self.assertFalse(self._reply_seg_placed(store), "the unanswered nudge stays unfiled")


if __name__ == "__main__":
    unittest.main()
