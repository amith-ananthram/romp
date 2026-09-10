#!/usr/bin/env python3
"""Bus code-staleness self-restart (the user 2026-06-29): the postal bus is a long-lived singleton keyed on
its port, so a process started before a code change keeps serving STALE in-memory code — which silently
stranded mail to SDK sessions (a bus from before the "deliver via the kernel, not by pasting into a tmux
pane" refactor couldn't reach a pane-less SDK recipient). Guard: the bus fingerprints its own source at boot
and the monitor re-execs into the new code the moment the on-disk file changes.

Synthetic only — no real session data.
"""
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))


class SourceFingerprint(unittest.TestCase):
    def test_fingerprint_is_stable_and_content_sensitive(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "src.py")
        Path(p).write_text("print('a')\n")
        fp1 = pm._source_fingerprint(p)
        self.assertTrue(fp1, "a readable file yields a non-empty fingerprint")
        self.assertEqual(fp1, pm._source_fingerprint(p), "same bytes → same fingerprint")
        Path(p).write_text("print('b')\n")
        self.assertNotEqual(fp1, pm._source_fingerprint(p), "changed bytes → changed fingerprint")

    def test_missing_file_fingerprints_empty(self):
        self.assertEqual(pm._source_fingerprint("/no/such/file/xyz"), "",
                         "an unreadable source yields '' so it can never be mistaken for a change")


class ShouldRestart(unittest.TestCase):
    def test_only_a_real_change_triggers(self):
        self.assertFalse(pm._should_restart_for_code("aaa", "aaa"), "unchanged → no restart")
        self.assertTrue(pm._should_restart_for_code("aaa", "bbb"), "changed → restart")

    def test_empty_fingerprint_fails_safe(self):
        # a transient read error (either side) must never restart — staying up beats churning the bus
        self.assertFalse(pm._should_restart_for_code("", "bbb"))
        self.assertFalse(pm._should_restart_for_code("aaa", ""))
        self.assertFalse(pm._should_restart_for_code("", ""))


class MaybeRestartIntegration(unittest.TestCase):
    def setUp(self):
        self._saved_src, self._saved_restart = pm._SRC, pm._restart_self
        self.calls = []
        pm._restart_self = lambda: self.calls.append(True)   # stub the execv so the test process survives

    def tearDown(self):
        pm._SRC, pm._restart_self = self._saved_src, self._saved_restart

    def test_monitor_tick_reexecs_when_the_on_disk_source_changes(self):
        d = tempfile.mkdtemp()
        pm._SRC = os.path.join(d, "romp-postal-service")
        Path(pm._SRC).write_text("# v1\n")
        boot_fp = pm._source_fingerprint()                   # what serve() captures at boot
        self.assertFalse(pm._maybe_restart_for_code(boot_fp), "unchanged source → tick does nothing")
        self.assertEqual(self.calls, [])
        Path(pm._SRC).write_text("# v2 — upgraded under the running bus\n")
        self.assertTrue(pm._maybe_restart_for_code(boot_fp), "the upgrade is detected on the next tick")
        self.assertEqual(self.calls, [True], "the bus re-execs exactly once into the new code")


if __name__ == "__main__":
    unittest.main()
