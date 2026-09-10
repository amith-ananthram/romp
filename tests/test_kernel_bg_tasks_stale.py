#!/usr/bin/env python3
"""The bg-tasks box drops tasks that died with a previous CLI (the user 2026-07-10): a task launched
before the session's CURRENT CLI spawned can never complete — its <task-notification> died with the old
process — so counting it as 'running' forever produced the ghost '25 background tasks' that read as a
wedged session (nimbus). _scan_bg_tasks stamps each launch with the record's epoch; _bg_tasks filters
still-running tasks older than reg spawnedAt (stamped by SdkSession._run on every fresh CLI spawn),
after the cache since spawnedAt changes without the transcript changing. Synthetic fixtures only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_bgstale", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


def launch(tid, iso, desc):
    return {"type": "assistant", "timestamp": iso,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Bash",
                 "input": {"command": "sleep 999", "run_in_background": True, "description": desc}}]}}


class BgTasksStale(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        (jd.STATE / "sdk").mkdir(parents=True)
        self.path = os.path.join(self.td.name, SID + ".jsonl")
        with open(self.path, "w") as f:
            f.write(json.dumps(launch("toolu_old", "2026-06-10T06:00:00Z", "old watcher")) + "\n")
            f.write(json.dumps(launch("toolu_new", "2026-06-10T08:00:00Z", "new watcher")) + "\n")

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def _epoch(self, iso):
        return km._msg_epoch({"timestamp": iso})

    def test_scan_stamps_each_launch_with_its_record_epoch(self):
        scan = km._scan_bg_tasks(self.path)
        self.assertEqual({tk["id"]: bool(tk.get("t")) for tk in scan},
                         {"toolu_old": True, "toolu_new": True})

    def test_no_spawned_at_keeps_everything(self):
        self.assertEqual(km._bg_tasks(self.path)["count"], 2,
                         "tmux / never-spawned sessions are unfiltered")

    def test_tasks_predating_the_live_cli_are_dropped(self):
        cutoff = self._epoch("2026-06-10T07:00:00Z")
        out = km._bg_tasks(self.path, spawned_at=cutoff)
        self.assertEqual([tk["id"] for tk in out["tasks"]], ["toolu_new"])
        self.assertEqual(out["count"], 1)

    def test_filter_applies_after_the_cache(self):
        self.assertEqual(km._bg_tasks(self.path)["count"], 2)          # primes the mtime cache
        cutoff = self._epoch("2026-06-10T09:00:00Z")
        self.assertEqual(km._bg_tasks(self.path, spawned_at=cutoff)["count"], 0,
                         "a cached scan still honors a newer spawnedAt")

    def test_spawned_at_reads_the_reg(self):
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"spawnedAt": 12345}))
        self.assertEqual(km._sdk_spawned_at(SID), 12345)
        self.assertIsNone(km._sdk_spawned_at("no-such-sid"))
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"spawnedAt": "junk"}))
        self.assertIsNone(km._sdk_spawned_at(SID))

    def test_build_session_wires_spawned_at(self):
        # spawned_at stays the tmux/no-snapshot fallback; an SDK session's box is gated by the backend's
        # LIVE task-lifecycle set (the user 2026-07-11) — both ride the same call. The live gate reads
        # the CALLER's snapshot, never a fresh _tmux_sessions() (the 2026-08-10 pusher CPU fix).
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('_bg_tasks(sess["path"], _sdk_spawned_at(sid),', src)
        self.assertIn('live=(tmux.get(str(sid)) or {}).get("bgTasks")', src)


if __name__ == "__main__":
    unittest.main()
