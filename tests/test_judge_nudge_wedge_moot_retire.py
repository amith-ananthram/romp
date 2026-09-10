#!/usr/bin/env python3
"""A MOOT prompt/live phase is RETIRED, not silently skipped — else auto-nudge wedges (the user 2026-07-27).

The incident: a session's card sat `working` with no chips and no nudge, forever. `plan_units` emits a
`#p` prompt-run for an ENDED turn that produced no assistant work (the 2026-07-25 API-error fix). When
that segment's BARE work key was already placed — the shape every store written BEFORE that fix carries —
`_plan_session` skipped the `#p` unit as moot with a plain `continue`, leaving the key ABSENT. auto-nudge's
placement gate (kernel `_auto_nudge_session`, `_unplanned`) asks `_placed_key` of every unit `plan_units`
yields, so that absent key read as PENDING on every tick and silenced nudges for the WHOLE session,
permanently. The moot skip now retires the key (`placements[key] = None`) exactly as the delegation
branch's "fyi" retire does, which heals the store in one pass and reopens the gate. SYNTHETIC fixtures only.
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path
import os

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_nudge_wedge", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-666666666666"
T0 = NOW - 3600
ASK = "Which retry backoff should the uploader use when the object store returns 503?"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, api_error=False):
    r = {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                     "stop_reason": "end_turn"}}
    if api_error:
        r["isApiErrorMessage"] = True
        r["apiErrorStatus"] = 529
    return r


# the ask's turn died on an API error (no assistant work); a later real turn ends it
RECORDS = [
    uline(T0, ASK, "u1"),
    aline(T0 + 360, "API Error: 529 Overloaded", "a1", "u1", api_error=True),
    uline(T0 + 400, "separately, bump the client version", "u2", "a1"),
    aline(T0 + 410, "Bumped it.", "a2", "u2"),
]

MINT = '{"ops":[{"why":"the ask is real","do":"mint","text":"Pick the uploader retry backoff"}]}'


class MootPromptRetire(unittest.TestCase):
    def _gate_is_open(self, store, path):
        """Mirror kernel `_auto_nudge_session`'s `_unplanned` check: every unit already placed?"""
        turns = jd.parsed_session(SID, [path], NOW)["turns"]
        live = {sg["id"] for tn in turns for sg in jd._segs(tn, store)}
        placements = store.get("placements") or {}
        return all(jd._placed_key(placements, jd._unit_key(u[0], u[1]), live)
                   for u in jd.plan_units({"turns": turns}, store))

    def test_moot_prompt_run_is_retired_so_the_nudge_gate_reopens(self):
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
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)

                # Rewrite the store into its PRE-2026-07-25 shape: the dead turn's work-run placed the
                # BARE key and no `#p` ever existed. This is the exact on-disk state of every long-lived
                # session that carries an API-error turn from before that fix.
                pkeys = [k for k in store["placements"] if k.endswith("#p")]
                self.assertTrue(pkeys, "the dead turn's ask was placed by a prompt-run")
                legacy = {}
                for k, v in store["placements"].items():
                    legacy[k[:-2] if k.endswith("#p") else k] = v
                store["placements"] = legacy
                jd.save_goals(SID, store)

                store = jd.load_goals(SID)
                self.assertFalse(self._gate_is_open(store, str(tpath)),
                                 "precondition: the legacy store leaves the moot #p unit unplaced")

                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW + 100)
                store = jd.load_goals(SID)

                for k in pkeys:
                    self.assertIn(k, store["placements"],
                                  "the moot prompt-run is RETIRED (recorded as processed), not skipped")
                    self.assertIsNone(store["placements"][k],
                                      "retired = marked processed with no node, like the 'fyi' #d retire")
                self.assertTrue(self._gate_is_open(store, str(tpath)),
                                "one pass heals the store and auto-nudge's placement gate reads planned")
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved

    def test_retiring_the_moot_phase_mints_no_second_card(self):
        """The retire is a RULING, not a placement: it must never re-plan the ask onto a new card."""
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
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
                before = len(store["nodes"])
                store["placements"] = {(k[:-2] if k.endswith("#p") else k): v
                                       for k, v in store["placements"].items()}
                jd.save_goals(SID, store)

                calls = []
                jd.plan_llm = jd.opener_llm = lambda *a, **k: (calls.append(1), MINT)[1]
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW + 100)
                store = jd.load_goals(SID)
                self.assertEqual(calls, [], "a moot phase is retired with NO planner call")
                self.assertEqual(len(store["nodes"]), before, "retiring mints nothing")
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved


if __name__ == "__main__":
    unittest.main()
