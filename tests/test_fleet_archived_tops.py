#!/usr/bin/env python3
"""_fleet_archived_tops (bin/romp-kernel): the Fleet's "Show completed" surfaces the FULLY-COMPLETED top
tasks the compaction sweep archived out of the live goal tree — so a finished+archived session reappears
instead of vanishing (the user 2026-06-27). Synthetic stores only: placeholder UUIDs, no real data.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_fleetarch", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class FleetArchivedTops(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.arch_dir = Path(self.td.name) / "goals-archive"
        self.arch_dir.mkdir(parents=True)
        self._saved = km.jd.GOALARCHDIR
        km.jd.GOALARCHDIR = self.arch_dir
        km._arch_tops_cache.clear()

    def tearDown(self):
        km.jd.GOALARCHDIR = self._saved
        km._arch_tops_cache.clear()
        self.td.cleanup()

    def _write(self, sid, store):
        (self.arch_dir / (sid + ".json")).write_text(json.dumps(store))

    def test_no_archive_file_returns_empty(self):
        self.assertEqual(km._fleet_archived_tops("no-such-sid"), [])

    def test_surfaces_completed_tops_with_their_subtrees_not_bare_dismissals(self):
        # the user 2026-06-29: an archived completed top now carries its WHOLE SUBTREE (depth + children ids)
        # so the Fleet can EXPAND it — not a flat childless row. Bare dismissals (t2) are still excluded.
        self._write(SID, {"nodes": {
            "t1": {"text": "Ship feature", "parentId": None, "nodeComplete": True, "t": 100, "mt": 200},
            "t2": {"text": "Investigate idea", "parentId": None, "t": 110, "mt": 150},      # dismissed, never finished
            "t3": {"text": "Write doc", "parentId": None, "t": 120, "mt": 300, "summary": "wrote it"},  # done (takeaway)
            "c1": {"text": "a child", "parentId": "t1", "nodeComplete": True, "t": 130, "mt": 140},
            "c2": {"text": "a grandchild", "parentId": "c1", "nodeComplete": True, "t": 135, "mt": 138},
        }, "status": {"t2": "cleared"}})
        out = km._fleet_archived_tops(SID)
        # flat list: tops newest-first, each FOLLOWED by its descendants; no bare-dismissal t2
        self.assertEqual([n["id"] for n in out], ["t3", "t1", "c1", "c2"])
        by = {n["id"]: n for n in out}
        self.assertEqual(by["t3"]["depth"], 0); self.assertEqual(by["t3"]["children"], [])
        self.assertEqual(by["t1"]["depth"], 0); self.assertEqual(by["t1"]["children"], ["c1"])   # expandable now
        self.assertEqual(by["c1"]["depth"], 1); self.assertEqual(by["c1"]["children"], ["c2"])
        self.assertEqual(by["c2"]["depth"], 2)
        self.assertTrue(all(n["done"] and n["archived"] for n in out), "every archived node is done + tagged archived")

    def test_archived_nodes_carry_their_deep_link_anchors(self):
        # the user 2026-07-11: an archived row's text was a DEAD click — the projection carried no
        # anchors, so the fleet's nav had nothing to post. The exact uuids stamped on the node ride
        # along (mint prompt, distiller citation); null stays null (the client's time fallback covers it).
        self._write(SID, {"nodes": {
            "t1": {"text": "Ship feature", "parentId": None, "nodeComplete": True, "t": 100, "mt": 200,
                   "promptUuid": "u-mint-1", "summaryAnchor": "a-cite-1"},
            "c1": {"text": "a child", "parentId": "t1", "nodeComplete": True, "t": 130, "mt": 140},
        }, "status": {}})
        by = {n["id"]: n for n in km._fleet_archived_tops(SID)}
        self.assertEqual(by["t1"]["promptAnchorUuid"], "u-mint-1")
        self.assertEqual(by["t1"]["anchorUuid"], "a-cite-1")
        self.assertIsNone(by["c1"]["promptAnchorUuid"], "no stamp → null; the client falls back to t/mt time nav")
        self.assertIsNone(by["c1"]["anchorUuid"])

    def test_status_completed_counts_as_done(self):
        self._write(SID, {"nodes": {"t1": {"text": "x", "parentId": None, "t": 1, "mt": 2}},
                          "status": {"t1": "completed"}})
        self.assertEqual([n["id"] for n in km._fleet_archived_tops(SID)], ["t1"])

    def test_cap_limits_count(self):
        nodes = {("t%d" % i): {"text": str(i), "parentId": None, "nodeComplete": True, "t": i, "mt": i}
                 for i in range(30)}
        self._write(SID, {"nodes": nodes, "status": {}})
        self.assertEqual(len(km._fleet_archived_tops(SID, cap=5)), 5)

    def test_mtime_cached(self):
        self._write(SID, {"nodes": {"t1": {"text": "x", "parentId": None, "nodeComplete": True, "t": 1, "mt": 2}},
                          "status": {}})
        a = km._fleet_archived_tops(SID)
        b = km._fleet_archived_tops(SID)
        self.assertIs(a, b, "unchanged archive (same mtime) → cached, no re-projection on the feed hot path")


if __name__ == "__main__":
    unittest.main()
