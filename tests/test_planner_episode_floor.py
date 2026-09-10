#!/usr/bin/env python3
"""The planner never judges a unit from before the current episode (the user 2026-07-27).

A /clear moves the episode floor, but pre-clear segments can still reach the parse (the anchor
transcript is stitched behind a fork so resume chains keep their history). An old segment that had
LOST its placement was re-planned 40 seconds after a /clear and filed done-verdicts on three-day-old
cards, resurfacing them as freshly completed. The boundary settle usually masks this (verdicts on
cleared nodes stay dark), but the planner must not depend on it: a unit whose segment predates
episode_floor() is evidence from a conversation the agent can no longer see — RETIRED, not skipped,
so auto-nudge's `_unplanned` gate never reads it as forever-pending.

The floor exists only once a /clear BOUNDARY is recorded (the user 2026-07-30). The episodes log's
row 0 is a SEED — whatever episode was current when romp first observed the session — and a session
spawned WITH its prompt stamps that founding segment at the send moment, seconds before the CLI
boots and writes the transcript head that becomes the seed's t. Reading the seed as a floor
misclassified the founding ask of every spawned-with-prompt session as pre-episode and sealed it:
zero cards ever, silently, across nine sessions in the guard's first three days. A seed-only log
means no conversation was ever lost, so there is nothing to guard. Synthetic fixtures only."""
import json
import os
import shutil
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
jd = load_source("romp_judge_epifloor", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T_OLD = 1780000100          # a pre-clear turn
T_FLOOR = 1780005000        # the current episode's head
T_NEW = 1780006000          # a post-clear turn
NOW = T_NEW + 600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


RECS = [uline(T_OLD, "audit the old-era notification design", "u1"),
        aline(T_OLD + 30, "Audited it, all good.", "a1", "u1"),
        uline(T_NEW, "tidy the docs page", "u2", "a1"),
        aline(T_NEW + 30, "Done, tidied.", "a2", "u2")]


class PlannerEpisodeFloor(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        self.proj = Path(self._td) / "proj"
        self.proj.mkdir()
        self.calls = []
        self._saved = (jd.plan_llm, jd.opener_llm, jd._group_store)
        jd.plan_llm = lambda text, *a, **k: (self.calls.append(text) or
                                             '{"ops":[{"why":"finished","do":"mint","text":"Tidy the docs page"}]}')
        jd.opener_llm = lambda *a, **k: ""
        jd._group_store = lambda *a, **k: None

    def tearDown(self):
        (jd.plan_llm, jd.opener_llm, jd._group_store) = self._saved
        shutil.rmtree(self._td, ignore_errors=True)

    def _plan(self):
        tpath = self.proj / (SID + ".jsonl")
        tpath.write_text("\n".join(json.dumps(r) for r in RECS) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._plan_session(SID, str(tpath), NOW)
        return jd.load_goals(SID)

    def test_pre_floor_unit_is_retired_not_planned(self):
        jd.append_episode(SID, "seedhead", SID, T_OLD - 50)   # row 0: first observation (a seed)
        jd.append_episode(SID, "newhead", SID, T_FLOOR)       # row 1: the /clear boundary
        store = self._plan()
        self.assertEqual(len(self.calls), 1, "only the current-episode unit reaches the planner")
        self.assertIn("tidy the docs", self.calls[0].lower())
        self.assertFalse(any("old-era" in (nd.get("text") or "") for nd in store["nodes"].values()),
                         "no node is minted from the dead conversation")
        retired = [k for k, v in store["placements"].items() if v is None]
        self.assertTrue(any((":%d:" % T_OLD) in k for k in retired),
                        "the pre-floor unit is RETIRED (auto-nudge's gate must not read it as pending)")

    def test_without_an_episode_floor_the_guard_is_inert(self):
        store = self._plan()
        self.assertEqual(len(self.calls), 2, "no recorded episode -> every unit still plans")
        texts = " | ".join((nd.get("text") or "") for nd in store["nodes"].values())
        self.assertIn("old-era", texts.lower() + " | " + " ".join(self.calls).lower(),
                      "the old turn is judged normally when no floor exists")

    def test_a_seed_only_log_never_retires_the_founding_prompt(self):
        # A session spawned WITH its prompt: the founding segment is stamped at the send moment
        # (T_OLD), the CLI boots and writes the transcript head a second later, and that head's t
        # becomes the seed row. No /clear ever happened, so nothing is pre-episode.
        jd.append_episode(SID, "seedhead", SID, T_OLD + 1)
        store = self._plan()
        self.assertEqual(len(self.calls), 2, "a seed row is not a boundary -> every unit still plans")
        self.assertTrue(any("old-era" in c.lower() for c in self.calls),
                        "the founding ask reaches the planner")
        sealed = [k for k, v in store["placements"].items() if v is None and (":%d:" % T_OLD) in k]
        self.assertEqual(sealed, [], "the founding unit is placed, never sealed as pre-episode")


if __name__ == "__main__":
    unittest.main()
