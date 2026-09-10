"""Undo-clear (and Clear-all) must not re-discover every session per restored card (the user 2026-06-24).

_mark_nodes_cleared rolls up each touched session's goal status, which needs the session's transcript
path to compute `closed`. It used to fetch that with `_sessions(now)` — a full jd.discover() filesystem
enumeration — INSIDE the per-session loop, so a Clear-all → Undo-clear across N sessions did N back-to-back
discoveries on the single-threaded kernel (the slow undo). The discovery is hoisted to ONE call; the loop
reads a prebuilt sid→path map. Source-level pin against the live kernel.
"""
import inspect
import os
import re
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class ClearPerfTest(unittest.TestCase):
    def setUp(self):
        self.body = inspect.getsource(km._mark_nodes_cleared)

    def test_session_discovery_is_hoisted_out_of_the_loop(self):
        # one discovery for the whole batch, into a sid->path map
        self.assertIn('sess_paths = {s["sid"]: s["path"] for s in _sessions(now)}', self.body)
        # the loop reads the prebuilt map, NOT a fresh discovery
        self.assertIn("path = sess_paths.get(sid)", self.body)

    def test_no_per_session_rediscovery(self):
        # the O(N) inner `next(... for s in _sessions(now) ...)` discovery is gone, and the only real CALL to
        # _sessions(now) is the hoisted one (ignore mentions in comment lines)
        self.assertNotRegex(self.body, r"for s in _sessions\(now\) if s\[")
        code = [ln for ln in self.body.splitlines() if not ln.lstrip().startswith("#")]
        self.assertEqual(sum(ln.count("_sessions(now)") for ln in code), 1)


if __name__ == "__main__":
    unittest.main()
