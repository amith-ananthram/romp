#!/usr/bin/env python3
"""The per-pass / per-session fairness caps were REMOVED (the user 2026-06-30): they were a recurring source
of confusing starvation bugs (a goal/nudge stuck behind a full per-pass window) and never clearly needed.
PLAN_SESSIONS / PLAN_FAIRNESS / CLOSE_FAIRNESS are now None, so every `fleet[:cap]` / `units[:cap]` slice is
the whole list and the closer's `did >= cap` guard no-ops. This pins that they stay unbounded and that the
None guards don't crash. Synthetic transcript only — placeholder UUIDs, no real data.
"""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_nofair", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class NoFairnessCaps(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def test_caps_are_unbounded(self):
        # None → list[:None] is the whole list, so no per-pass/per-session throttle survives.
        self.assertIsNone(jd.PLAN_SESSIONS)
        self.assertFalse(hasattr(jd, "PLAN_FAIRNESS"), "cap constant deleted outright (P3.4 2026-07-07)")
        self.assertIsNone(jd.CLOSE_FAIRNESS)
        self.assertEqual(list(range(20))[:jd.PLAN_SESSIONS], list(range(20)))

    def test_close_session_handles_none_cap_over_many_ended_turns(self):
        # Many ended turns, NO open goals (empty store) → _close_turn returns [] with no LLM call, but the
        # loop still iterates every turn. The `cap is not None and did >= cap` guard must not crash on None
        # and must not stop early — all turns get visited.
        path = os.path.join(self.td, SID + ".jsonl")
        recs, base = [], int(time.time()) - 5000
        prev = None
        for i in range(25):                                    # 25 > the old CLOSE_FAIRNESS of 8
            u = "u%d" % i
            recs.append({"type": "user", "uuid": u, "parentUuid": prev,
                         "timestamp": _iso(base + i * 100), "promptSource": "typed",
                         "message": {"role": "user", "content": "msg %d" % i}})
            a = "a%d" % i
            recs.append({"type": "assistant", "uuid": a, "parentUuid": u,
                         "timestamp": _iso(base + i * 100 + 1),
                         "message": {"role": "assistant", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": "ok %d" % i}]}})
            prev = a
        open(path, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
        # empty goal store → no menu → no closer LLM call; we're exercising the loop + None guard only.
        jd.save_goals(SID, {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {}})
        newly = jd._close_session(SID, path, time.time())      # default cap=CLOSE_FAIRNESS (None)
        self.assertEqual(newly, [])                            # nothing to complete, but no crash / early stop
        # every ended turn was visited and recorded as closed (no per-pass cap left work behind)
        store = jd.load_goals(SID)
        self.assertGreaterEqual(len(jd._closed_turns(store)), 20)


if __name__ == "__main__":
    unittest.main()
