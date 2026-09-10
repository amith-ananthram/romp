#!/usr/bin/env python3
"""A same-second identical-prompt burst must plan ONCE, not once per copy (the user 2026-07-06).

plan_units yields one unit per TURN; a burst of byte-identical prompts landing within the same
second (an auto-retry storm during an API-error stretch) produces many turns whose segments share
ONE seg id (same t + same text hash). Before the fix, every copy passed the pass-start placement
check (nothing placed yet), so each got its own planner LLM call and filed its own duplicate node —
a single burst minted 200+ junk nodes and flooded the feed with duplicates of already-cleared work.
The collection loop now dedups unit keys within the pass, and the apply loop re-checks placements
before each unit so a key recorded mid-pass never re-plans. Synthetic fixtures only."""
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
jd = load_source("romp_judge_retry_burst", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


class RetryBurstPlansOnce(unittest.TestCase):
    def _run(self, records, llm):
        calls = []
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"
            jd.plan_llm = jd.opener_llm = lambda *a, **k: (calls.append(1), llm())[1]
            jd._group_store = lambda *a, **k: None    # don't fire the real grouper model after a placement
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
            return calls, store

    def test_same_second_identical_turns_plan_once(self):
        # 5 ended turns, all triggered by the SAME text at the SAME second → one seg id repeated 5×.
        records = []
        for i in range(5):
            records.append(uline(T0, "retry", "u%d" % i, parent=("a%d" % (i - 1)) if i else None))
            records.append(aline(T0, "API error, nothing done.", "a%d" % i, "u%d" % i))
        calls, store = self._run(records, lambda: '{"ops":[{"why":"retried","do":"mint","text":"Retry the request"}]}')
        burst_tops = [nd for nd in store["nodes"].values()
                      if nd.get("parentId") is None and nd.get("text") == "Retry the request"]
        self.assertEqual(len(burst_tops), 1,
                         "a same-second identical-prompt burst mints ONE node, not one per copy")
        self.assertEqual(len(calls), 1, "the burst costs one planner call, not one per copy")

    def test_distinct_second_twins_still_plan_each(self):
        # the 4cdbe44 intent stays intact: byte-identical prompts in DIFFERENT seconds are distinct
        # segments (crash-heal resumes) — each plans, none is swallowed as a drift of the first.
        records = []
        for i in range(3):
            records.append(uline(T0 + i * 100, "kernel restarted, resume", "u%d" % i,
                                 parent=("a%d" % (i - 1)) if i else None))
            records.append(aline(T0 + i * 100 + 30, "Resumed work step %d." % i, "a%d" % i, "u%d" % i))
        seq = iter(range(100))
        calls, store = self._run(
            records, lambda: '{"ops":[{"why":"resumed","do":"mint","text":"Resume %d"}]}' % next(seq))
        resumed = [nd for nd in store["nodes"].values()
                   if nd.get("parentId") is None and (nd.get("text") or "").startswith("Resume ")]
        self.assertEqual(len(resumed), 3, "distinct-second identical twins each plan (the live-twin guard)")


if __name__ == "__main__":
    unittest.main()
