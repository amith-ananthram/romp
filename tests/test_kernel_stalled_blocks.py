#!/usr/bin/env python3
"""A failed auto-nudge IS a block (the user 2026-07-07): romp asked once, the response didn't resolve
the goal, and per the anti-loop rule it never re-asks — so by definition the goal now needs the user.
_mark_nudge_failed records a block verdict in the goal's diary and the card moves to Needs-you through
the NORMAL ladder (previously only the FORK flavor floored read-side; the common case idled in Working
wearing a chip). XDG_STATE_HOME is pointed at a temp dir BEFORE the kernel loads (the standard kernel
test isolation). Synthetic fixtures only."""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
kern = load_source("romp_kernel_stalledblk", os.path.join(BIN, "romp-kernel"))
jd = kern.jd

# A SID of this file's own: the judge module is process-shared across every kernel test copy (its
# import is sys.modules-cached), so the append-only overrides journal is shared too — a same-SID,
# same-gid block journaled by another test file would replay into this one's loads (and vice versa).
SID = "11111111-2222-3333-4444-888888888888"
GID = SID + ":g1"
NOW = int(time.time())


class StalledBlocks(unittest.TestCase):
    def setUp(self):
        # each test re-seeds the store from scratch; the append-only overrides journal would replay a
        # previous test's journaled nudge block into the fresh store as a phantom row
        fp = jd._overrides_dir() / (SID + ".jsonl")
        if fp.exists():
            fp.unlink()

    def test_failed_nudge_records_a_block_and_the_card_goes_needs_you(self):
        store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
                 "nodes": {GID: {"id": GID, "text": "Ship the widget", "parentId": None,
                                 "nodeComplete": False, "blocked": False, "cleared": False,
                                 "trail": [], "t": NOW - 600, "mt": NOW - 300}}}
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        kern._write_auto_nudge({"nudged": {GID: {"count": 1, "lastTurnId": "tid1"}}})

        kern._mark_nudge_failed(GID)

        rec = kern._auto_nudge_data()["nudged"][GID]
        self.assertTrue(rec["failed"])
        st = jd.load_goals(SID)
        nd = st["nodes"][GID]
        self.assertTrue(nd["blocked"], "a failed nudge blocks the goal")
        self.assertEqual(st["status"][GID], "blocked", "→ the Needs-you column via the normal ladder")
        ev = [e for e in nd["log"] if e["kind"] == "block"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["src"], "nudge")
        self.assertIn("won't be re-asked", ev[0]["why"])

    def test_users_newer_reply_outranks_the_stalled_block(self):
        # the block is a judge-rank verdict, so the standard evidence floor applies: a follow-up the
        # user already sent (followupAt >= now) voids it — their reply owns the verdict
        store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
                 "nodes": {GID: {"id": GID, "text": "Ship the widget", "parentId": None,
                                 "nodeComplete": False, "blocked": False, "cleared": False,
                                 "followupAt": NOW + 3600, "trail": [], "t": NOW - 600, "mt": NOW - 300}}}
        jd.migrate_store(store)                        # legacy followupAt stamp → its synth reopen event
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        kern._write_auto_nudge({"nudged": {GID: {"count": 1, "lastTurnId": "tid1"}}})
        kern._mark_nudge_failed(GID)
        st = jd.load_goals(SID)
        self.assertFalse(st["nodes"][GID]["blocked"], "the user's newer action floors the stalled block")
        self.assertEqual(st["status"][GID], "working")


if __name__ == "__main__":
    unittest.main()
