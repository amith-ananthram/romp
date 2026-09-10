#!/usr/bin/env python3
"""_atomic_write publishes a state file via a UNIQUELY-named temp + os.replace. The kernel is heavily
threaded (pusher, producer, WS handlers), and several of them write the SAME state file (session-order,
hidden-tabs, session-flags, auto-nudge, names). With the old pid-only temp name two threads shared one temp
path: the loser renamed a temp the winner had already moved → FileNotFoundError crashed the push (the user
2026-06-23). This hammers the helper from many threads to prove concurrent writes never raise and leave no
orphaned temp behind.
"""
import json
import os
import tempfile
import threading
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
km = load_source("romp_kernel_aw", os.path.join(BIN, "romp-kernel"))


class AtomicWrite(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_creates_then_overwrites_last_writer_wins(self):
        p = self.dir / "x.json"
        km._atomic_write(p, json.dumps({"v": 1}))
        km._atomic_write(p, json.dumps({"v": 2}))
        self.assertEqual(json.loads(p.read_text()), {"v": 2})

    def test_makes_missing_parent_dirs(self):
        p = self.dir / "nested" / "deep" / "y.json"
        km._atomic_write(p, json.dumps([1, 2, 3]))
        self.assertEqual(json.loads(p.read_text()), [1, 2, 3])

    def test_concurrent_writes_to_one_path_never_raise_and_leave_no_temp(self):
        p = self.dir / "session-order.json"
        errors = []

        def hammer(tid):
            for i in range(150):
                try:
                    km._atomic_write(p, json.dumps(["sid-%d-%d" % (tid, i)]))
                except Exception as e:                   # the race showed up here as FileNotFoundError
                    errors.append(repr(e))

        threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], "concurrent writes to the same path must never raise")
        self.assertEqual(json.loads(p.read_text())[0][:4], "sid-", "the published file is intact JSON")
        leftover = [f.name for f in self.dir.iterdir() if ".tmp." in f.name]
        self.assertEqual(leftover, [], "no orphaned temp files left behind")


if __name__ == "__main__":
    unittest.main()
