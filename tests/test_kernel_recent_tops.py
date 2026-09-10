#!/usr/bin/env python3
"""Tab-hover "Recent" = up-to-5 most recent TOP tasks across live + archive, ANY status (the user 2026-06-30).

A session whose top goals were all crossed off has a near-empty LIVE goal tree, so the tab hover showed just
a Summary and no Recent. `_archive_roots` surfaces the archived tops REGARDLESS of status (done / cleared /
blocked — unlike _fleet_archived_tops, which keeps only COMPLETED tops), and build_ledger merges them with the
live roots into `ledger.recent`. Synthetic fixtures only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_recent", os.path.join(BIN, "romp-kernel"))


class ArchiveRoots(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.GOALARCHDIR
        jd.GOALARCHDIR = Path(self.td.name)
        km._arch_roots_cache.clear()

    def tearDown(self):
        jd.GOALARCHDIR = self.saved
        self.td.cleanup()

    def _write(self, sid, nodes, status=None):
        (jd.GOALARCHDIR / (sid + ".json")).write_text(json.dumps({"nodes": nodes, "status": status or {}}))

    def test_returns_all_archived_ROOTS_regardless_of_status(self):
        sid = "s1"
        self._write(sid, {
            "g1": {"text": "usage refresh button", "parentId": None, "mt": 300, "nodeComplete": True},
            "g2": {"text": "remove emojis", "parentId": None, "mt": 200},                 # bare-cleared, no complete flag
            "g3": {"text": "a sub-step", "parentId": "g1", "mt": 250},                    # NOT a root → excluded
            "g4": {"text": "   ", "parentId": None, "mt": 500},                           # blank text → excluded
        }, status={"g2": "cleared"})
        roots = km._archive_roots(sid)
        texts = sorted(r["text"] for r in roots)
        self.assertEqual(texts, ["remove emojis", "usage refresh button"],
                         "roots only, blanks dropped, both statuses kept — a bare-cleared top is INCLUDED"
                         " (this is what _fleet_archived_tops drops); entries are {text,t} only, the"
                         " tooltip's exact needs (2026-07-07 payload audit)")

    def test_missing_archive_is_empty(self):
        self.assertEqual(km._archive_roots("nope"), [])

    def test_cached_on_archive_mtime(self):
        sid = "s2"
        self._write(sid, {"g1": {"text": "x", "parentId": None, "mt": 1}})
        first = km._archive_roots(sid)
        self.assertIs(km._archive_roots(sid), first, "unchanged archive → same cached list")


class LedgerEmitsRecent(unittest.TestCase):
    def test_build_ledger_merges_live_and_archive_roots_into_recent(self):
        # source pin: the ledger dict carries `recent`, built from live roots + _archive_roots, sorted, top-5
        src = Path(os.path.join(BIN, "romp-kernel")).read_text()
        self.assertIn('"recent": recent_tops', src)
        self.assertIn("_archive_roots(sid)", src)
        self.assertIn("recent_tops = sorted(_live_roots + _archive_roots(sid), key=lambda r: r[\"t\"] or 0, reverse=True)[:5]", src)
        # a muted session shows no Recent (out of task tracking)
        self.assertIn("tree, current, recent_tops = [], None, []", src)


if __name__ == "__main__":
    unittest.main()
