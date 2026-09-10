#!/usr/bin/env python3
"""The modal's user "Resolve" override must survive the diary flip (found 2026-07-07): _resolve_node
wrote nodeComplete/blocked flags with NO diary event, so the rollup_status call inside the very same
function re-derived the flags from the (unchanged) log and REVERTED the user's own action — the resolve
was a silent no-op from the moment the log became the authority. It now records a user done verdict
first. XDG_STATE_HOME is pointed at a temp dir BEFORE the kernel loads. Synthetic fixtures only."""
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
kern = load_source("romp_kernel_resolvenode", os.path.join(BIN, "romp-kernel"))
jd = kern.jd

SID = "11111111-2222-3333-4444-555555555555"
GID = SID + ":g1"
NOW = int(time.time())


class ResolveNode(unittest.TestCase):
    def setUp(self):
        # kern.jd is one module object shared by every test module in the process; the store this test saves and
        # the resolve it journals at the import-bound root outlived the module and were replayed onto every later
        # module's feed for the placeholder sid (T281). A private root for the duration.
        self._td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd._rebind_state(Path(self._td.name))

    def tearDown(self):
        jd._rebind_state(self._state)
        self._td.cleanup()

    def test_the_store_and_the_journal_do_not_outlive_the_module(self):
        # The residue pin (T281): after the resolve, this test's store and journal live under its own root; the
        # run-wide root (what every later module's feed reads) is exactly as it was.
        shared = [Path(self._state) / "goals" / (SID + ".json"), Path(self._state) / "overrides" / (SID + ".jsonl")]
        before = [(p.exists(), p.stat().st_size if p.exists() else None) for p in shared]
        jd.save_goals(SID, {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
                            "nodes": {GID: {"id": GID, "text": "Ship the widget", "parentId": None, "nodeComplete": False,
                                            "blocked": False, "cleared": False, "trail": [], "t": NOW - 600, "mt": NOW - 300}}})
        self.assertTrue(kern._resolve_node(SID, GID))
        self.assertTrue((Path(self._td.name) / "goals" / (SID + ".json")).exists(), "the store lives under this test's root")
        self.assertEqual([(p.exists(), p.stat().st_size if p.exists() else None) for p in shared], before,
                         "the run-wide store and journal for the placeholder sid are untouched by this module")

    def test_user_resolve_is_evented_and_survives_the_rollup(self):
        store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
                 "nodes": {GID: {"id": GID, "text": "Ship the widget", "parentId": None,
                                 "nodeComplete": False, "blocked": True, "blockWhy": "pick a name",
                                 "cleared": False, "trail": [], "t": NOW - 600, "mt": NOW - 300}}}
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        self.assertTrue(kern._resolve_node(SID, GID))

        st = jd.load_goals(SID)
        nd = st["nodes"][GID]
        self.assertTrue(nd["nodeComplete"], "the user's resolve holds through the in-call rollup")
        self.assertFalse(nd["blocked"])
        ev = [e for e in nd["log"] if e["kind"] == "done" and not e.get("synth")]
        self.assertEqual([e["src"] for e in ev], ["user"], "the resolve is a USER done verdict in the diary")
        jd.rollup_status(st, False)                    # and the NEXT pass cannot revert it either
        self.assertTrue(st["nodes"][GID]["nodeComplete"])


if __name__ == "__main__":
    unittest.main()
