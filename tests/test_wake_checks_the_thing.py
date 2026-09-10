#!/usr/bin/env python3
"""The 6h awaiting wake directs the session to CHECK the awaited thing, not report feelings.

The 2026-08-18 awaiting audit: for waits on EXTERNAL events (a PR review, a CI run, a peer who
must act) the wake was the only recheck — and its copy invited a status answer ("a one-line
status is enough"), so a session could answer "still waiting" from memory forever while the
awaited thing sat resolved or dead. The copy now leads with going and LOOKING at the named thing
and reserves the one-line status for a check performed just now. Voice rules hold: it speaks as
the person the agent works for, no romp vocabulary (test_injected_voice renders it)."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_wakecheck", os.path.join(BIN, "romp-kernel"))


class WakeChecksTheThing(unittest.TestCase):
    def test_the_wake_leads_with_looking_at_the_thing(self):
        self.assertIn("Go LOOK at the thing itself first", km.AWAITING_BACKSTOP_TEXT)
        self.assertIn("rather than", km.AWAITING_BACKSTOP_TEXT)
        self.assertIn("answering from memory", km.AWAITING_BACKSTOP_TEXT)

    def test_a_wait_on_someone_else_gets_chased_not_narrated(self):
        self.assertIn("chase it or route around it", km.AWAITING_BACKSTOP_TEXT)

    def test_the_status_answer_is_gated_on_a_fresh_check(self):
        self.assertIn("Only if you checked just now", km.AWAITING_BACKSTOP_TEXT)


if __name__ == "__main__":
    unittest.main()
