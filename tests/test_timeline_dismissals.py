#!/usr/bin/env python3
"""Timeline dead-lane dismissals are DURABLE (the user 2026-08-14: cleared sessions must stay cleared
through kernel restarts and reconnects). The set persists to timeline-dismissed.json, hydrates at boot,
and a revived sid sheds its record (the un-dismiss event). Synthetic only — placeholder UUIDs, temp state.
"""
import inspect
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
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
SID2 = "66666666-7777-8888-9999-000000000000"


class DurableDismissals(unittest.TestCase):
    def setUp(self):
        km._dismissed_lanes.clear()
        try:
            km._dismissed_lanes_file().unlink()
        except OSError:
            pass

    def test_dismiss_persists_to_disk(self):
        km._dismiss_lane(SID)
        self.assertIn(SID, km._dismissed_lanes)
        on_disk = json.loads(km._dismissed_lanes_file().read_text())
        self.assertEqual(on_disk, [SID])

    def test_a_restart_remembers_the_cleared_lanes(self):
        # Boot hydration is `_dismissed_lanes = _load_dismissed_lanes()` at module level; a restart is a
        # fresh call of that loader over the same state dir, which is what this asserts. Deliberately NOT
        # a full module re-execution: under pytest the whole suite shares one process, and the loader's
        # name-reuse gives RELOAD semantics — the re-executed kernel re-resolves the judge state root
        # from the LIVE env (moved by the conftest floor since this file's module body ran), so it reads
        # a different dir than the one this test wrote. That was a real CI-only failure (2026-08-14).
        km._dismiss_lane(SID)
        km._dismiss_lane(SID2)
        self.assertEqual(km._load_dismissed_lanes(), {SID, SID2})
        self.assertIn("_dismissed_lanes = _load_dismissed_lanes()", inspect.getsource(km))

    def test_a_revived_sid_sheds_its_record(self):
        km._dismiss_lane(SID)
        km._dismiss_lane(SID2)
        km._undismiss_lanes([SID])                      # SID came back live; SID2 stays cleared
        self.assertEqual(km._dismissed_lanes, {SID2})
        self.assertEqual(json.loads(km._dismissed_lanes_file().read_text()), [SID2])
        km._undismiss_lanes([SID])                      # already shed: a no-op, no rewrite crash
        self.assertEqual(km._dismissed_lanes, {SID2})

    def test_corrupt_or_wrong_shape_file_hydrates_empty_never_crashes(self):
        km._dismissed_lanes_file().parent.mkdir(parents=True, exist_ok=True)
        km._dismissed_lanes_file().write_text("{not json")
        self.assertEqual(km._load_dismissed_lanes(), set())
        km._dismissed_lanes_file().write_text(json.dumps({"sid": True}))   # an object, not a list
        self.assertEqual(km._load_dismissed_lanes(), set())


if __name__ == "__main__":
    unittest.main()
