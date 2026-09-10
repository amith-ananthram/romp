#!/usr/bin/env python3
"""A sender-less postal delivery must never yield a '#d' delegation unit — nothing can place one, and one
unplaceable unit silences auto-nudge for the WHOLE session (2026-08-16).

The incident: an external tool posted a delegate-kind message into a session through the kernel's send
route with no session identity, so the delivered marker's id resolved to no sender in the postal index
(author.peer None). `plan_units` treated ANY postal segment as a delegation work-run and yielded a '#d'
unit; `run_courier` — the only placer of '#d' units — requires a KNOWN sender (it files under the
sender's goal and plants the sender-side tracking node), so it skipped the segment every pass, without
retiring it. auto-nudge's `_unplanned` gate reads every unplaced unit as "judges still pending", so the
session's whole escalation ladder (status nudges, awaiting wakes, debt reminders) went dead: two Working
cards sat untouched on an idle session for two days, with no chip and no block.

The partition contract now: the courier places exactly the peer segments whose sender it can resolve,
and `plan_units` yields a '#d' unit for exactly those. A sender-less postal segment falls through to the
planner's plain WORK unit — placeable like any non-human stretch, so the gate can never wedge on it.
SYNTHETIC fixtures only: placeholder UUIDs, invented message bodies.
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
jd = load_source("romp_judge_senderless_deleg", os.path.join(BIN, "romp-judge"))

NOW = 1781200000
SID = "11111111-2222-3333-4444-777777777777"
SENDER = "11111111-2222-3333-4444-888888888888"
MID = "1781199000.000001_11111.TESTHOST"
T0 = NOW - 3600
BODY = ("Compute request: run the standard comparison suite over the five staged runs and report "
        "the embedding drift.\n<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: delegate -->" % MID)

MINT = '{"ops":[{"why":"real work landed","do":"mint","text":"Run the staged comparison suite"}]}'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "sdk", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


RECORDS = [
    uline(T0, BODY, "u1"),
    aline(T0 + 60, "Suite dispatched; drift summary written to the shared report.", "a1", "u1"),
]


class SenderlessDelegation(unittest.TestCase):
    def _units(self, path, store=None):
        turns = jd.parsed_session(SID, [path], NOW)["turns"]
        return [(u[0], u[1]) for u in jd.plan_units({"turns": turns}, store)]

    def _gate_is_open(self, store, path):
        """Mirror kernel `_auto_nudge_session`'s `_unplanned` check: every unit already placed?"""
        turns = jd.parsed_session(SID, [path], NOW)["turns"]
        live = {sg["id"] for tn in turns for sg in jd._segs(tn, store)}
        placements = store.get("placements") or {}
        return all(jd._placed_key(placements, jd._unit_key(u[0], u[1]), live)
                   for u in jd.plan_units({"turns": turns}, store))

    def test_senderless_postal_segment_yields_a_plain_work_unit(self):
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td) / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
            phases = [ph for _sg, ph in self._units(str(tpath))]
            self.assertNotIn("delegation", phases,
                             "no sender resolved → the courier can never place a '#d'; none may be yielded")
            self.assertIn("work", phases, "the delivered ask files through the planner's plain work-run")

    def test_a_known_sender_still_yields_the_delegation_unit(self):
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td) / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
            saved = jd.MESSAGES
            jd.MESSAGES = Path(td) / "messages.jsonl"
            jd.MESSAGES.write_text(json.dumps(
                {"t": T0 - 1, "ev": "sent", "id": MID, "from_id": SENDER, "to_id": SID}) + "\n")
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                phases = [ph for _sg, ph in self._units(str(tpath))]
                self.assertIn("delegation", phases, "a resolvable sender keeps the courier's '#d' work-run")
                self.assertNotIn("work", phases, "and the planner does not double-place it")
            finally:
                jd.MESSAGES = saved
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def test_one_planner_pass_opens_the_nudge_gate(self):
        """The systemic claim: with the sender-less segment owned by the planner, one ordinary pass
        places it and auto-nudge's placement gate reads planned — no unit is left forever pending."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"
            jd.plan_llm = jd.opener_llm = lambda *a, **k: MINT
            jd._group_store = lambda *a, **k: None
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                store = jd.load_goals(SID)
                self.assertFalse(self._gate_is_open(store, str(tpath)),
                                 "precondition: the fresh unit starts unplaced (gate closed)")
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
                self.assertTrue(self._gate_is_open(store, str(tpath)),
                                "the planner places the sender-less segment; the gate reopens")
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved


if __name__ == "__main__":
    unittest.main()
