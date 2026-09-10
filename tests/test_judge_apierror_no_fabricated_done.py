#!/usr/bin/env python3
"""A turn that DIED on an API error must never be judged as if the work happened (the user 2026-07-25).

The incident: a detailed hardware question's turn exhausted its API retries — the only assistant record
was the error text (isApiErrorMessage) — yet the goal card completed with a fully CONFABULATED answer
summary. plan_units handed the planner a work unit whose text was just "USER ASKED: …" (the error atom is
excluded from _unit_text) framed as a finished stretch, and a capable planner answered the question from
its own knowledge and filed done. Now an ENDED segment with no real assistant work (_has_asst_work, the
captioner's own rule) never becomes a work/nudge/delegation unit: a human ask still gets PLACED (a
mint-only prompt-run, whose op filter cannot file done), so the card sits open — the truth — and a
romp-triggered stretch (an auto-retry) files nothing. SYNTHETIC fixtures only."""
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
jd = load_source("romp_judge_apierr_done", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
ASK = "How do I wire the widget sensor to the demo board — direct, or through a resistor divider?"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, author_romp=False):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "promptSource": "typed", "message": {"role": "user", "content": text}}
    if author_romp:
        r["message"]["content"] = text + "\n\n<!-- romp-injected -->"
    return r


def aline(t, text, uuid, parent=None, api_error=False, stop="end_turn"):
    r = {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                     "stop_reason": stop}}
    if api_error:
        r["isApiErrorMessage"] = True
        r["apiErrorStatus"] = 529
    return r


DONE_HAPPY = ('{"ops":[{"why":"asked about wiring","do":"mint","text":"Answer the wiring question"},'
              '{"why":"the divider is needed and the direct hookup works","do":"done","ref":1}]}')


class NoFabricatedDone(unittest.TestCase):
    def _run(self, records, llm_reply=DONE_HAPPY):
        calls = []
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"
            def fake(text, *a, **k):
                calls.append(text)
                return llm_reply
            jd.plan_llm = jd.opener_llm = fake
            jd._group_store = lambda *a, **k: None
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
            return calls, store

    def test_error_only_ended_turn_places_the_ask_but_cannot_complete_it(self):
        # the ask's turn died on the error; a later unrelated turn ends it
        records = [
            uline(T0, ASK, "u1"),
            aline(T0 + 360, "API Error: 529 Overloaded", "a1", "u1", api_error=True),
            uline(T0 + 400, "unrelated: also bump the version", "u2", "a1"),
            aline(T0 + 410, "Bumped.", "a2", "u2"),
        ]
        calls, store = self._run(records)
        asked = [nd for nd in store["nodes"].values() if "wiring" in (nd.get("text") or "").lower()]
        self.assertTrue(asked, "the unanswered ask still gets a card — it is real")
        self.assertFalse(asked[0].get("nodeComplete"),
                         "a done-happy planner reply must not complete a goal whose turn produced NOTHING")
        # and the planner never saw the DEAD stretch framed as finished work (the later, real turn
        # still gets its ordinary work unit — only the ask's dead segment is guarded)
        for c in calls:
            if ASK[:40] in c:
                self.assertNotIn("ASSISTANT SAID", c)

    def test_romp_retry_stretch_that_dies_files_nothing(self):
        # romp's auto-retry (romp-injected, not the user) also died → no unit at all, no junk card
        records = [
            uline(T0, "retry", "u1", author_romp=True),
            aline(T0 + 60, "API Error: 529 Overloaded", "a1", "u1", api_error=True),
            uline(T0 + 100, "unrelated: also bump the version", "u2", "a1"),
            aline(T0 + 110, "Bumped.", "a2", "u2"),
        ]
        calls, store = self._run(records)
        junk = [nd for nd in store["nodes"].values() if "retry" in (nd.get("text") or "").lower()]
        self.assertEqual(junk, [], "a dead romp-triggered stretch mints nothing")

    def test_real_work_then_error_still_gets_its_work_run(self):
        # the guard is exactly the captioner's: work BEFORE the error still judges normally
        records = [
            uline(T0, ASK, "u1"),
            aline(T0 + 60, "The divider is required; direct hookup risks the pin. Wired and verified.",
                  "a1", "u1"),
            aline(T0 + 360, "API Error: 529 Overloaded", "a2", "a1", api_error=True),
            uline(T0 + 400, "unrelated: also bump the version", "u2", "a2"),
            aline(T0 + 410, "Bumped.", "a3", "u2"),
        ]
        calls, store = self._run(records)
        self.assertTrue(any("ASSISTANT SAID" in c for c in calls),
                        "real pre-error work must still reach the planner as a work unit")
        asked = [nd for nd in store["nodes"].values() if "wiring" in (nd.get("text") or "").lower()]
        self.assertTrue(asked and asked[0].get("nodeComplete"),
                        "a genuinely delivered answer still completes")


if __name__ == "__main__":
    unittest.main()
