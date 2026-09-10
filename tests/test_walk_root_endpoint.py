#!/usr/bin/env python3
"""The kernel side of send-time relay enrichment (the user 2026-08-27, T126): _walk_root_record
runs the chain walk where the evidence is local — start node = the sending session's active focus
chain (lastNode), the round it was serving when it dispatched — and returns the root record or
None (unenriched relay; T101's quiet-on-uncertainty). The /walk-root HTTP handler wraps exactly
this function for the stdlib-only bus. SYNTHETIC fixtures only; private synthetic sids."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_walkroot", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1_787_900_000
SID = "c77b0001-1111-4222-8333-000000000001"    # private synthetic sid — never the shared placeholder
ASK = "make the two graph views draw identically"


class WalkRootRecord(unittest.TestCase):
    def setUp(self):
        self._saved = (jd.load_goals, jd.discover, jd._delegate_user_rooted)

    def tearDown(self):
        jd.load_goals, jd.discover, jd._delegate_user_rooted = self._saved

    def test_the_focus_chain_resolves_to_the_record(self):
        jd.load_goals = lambda sid: {"rompUuid": SID, "nodes": {}, "lastNode": SID + ":g3"}
        jd.discover = lambda now, window=None, forks=True: [(SID, "/dev/null", SID, "web")]
        seen = {}
        jd._delegate_user_rooted = lambda sid, start, paths, now: (
            seen.update(sid=sid, start=start) or {"text": ASK, "sid": SID})
        rec = km._walk_root_record(SID)
        self.assertEqual(rec, {"text": ASK, "sid": SID})
        self.assertEqual(seen["start"], SID + ":g3", "the walk starts at the active focus chain")

    def test_no_focus_or_no_record_or_a_crash_enriches_nothing(self):
        jd.load_goals = lambda sid: {"rompUuid": SID, "nodes": {}}
        rec = km._walk_root_record(SID)
        self.assertIsNone(rec, "no lastNode → no start → unenriched")
        jd.load_goals = lambda sid: {"rompUuid": SID, "nodes": {}, "lastNode": SID + ":g3"}
        jd.discover = lambda now, window=None, forks=True: []
        jd._delegate_user_rooted = lambda *a: None
        self.assertIsNone(km._walk_root_record(SID))
        jd.load_goals = lambda sid: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertIsNone(km._walk_root_record(SID), "a crash degrades, never raises into the bus")


if __name__ == "__main__":
    unittest.main()
