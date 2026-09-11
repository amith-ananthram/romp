#!/usr/bin/env python3
"""The producer loop's scheduling policy (the user 2026-06-19): the TRIAGE tier runs ALWAYS for any live
session — no browser-connected gate — exactly like the INDEX tier, and the backstop between event-pokes is
short (3s). Source-level pins: the loop is an infinite thread that drives the real fleet, so its policy is
asserted against the source rather than executed. (Behaviour: each judge still only makes an LLM call when
it has real new work, so an idle pass is filesystem stats, not model calls.)
"""
import os
import re
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
SRC = Path(os.path.join(os.path.dirname(HERE), "bin", "romp-kernel")).read_text()


class ProducerPolicy(unittest.TestCase):
    def _producer_body(self):
        # the body of def _producer() up to the next top-level def
        m = re.search(r"\ndef _producer\(\):\n(.*?)\ndef ", SRC, re.S)
        self.assertTrue(m, "found the _producer loop")
        return m.group(1)

    def test_triage_runs_always_for_a_live_session_not_browser_gated(self):
        body = self._producer_body()
        # both tiers are appended together under the live-session guard
        self.assertIn('args=(jd.run_index,), name="index"', body)
        self.assertIn('args=(jd.run_triage,), name="triage"', body)
        # the OLD browser+sig gate is gone — triage is no longer conditional on a connected browser
        self.assertNotIn("if browser and sig", body)
        self.assertNotIn("bool(_clients)", body, "the loop no longer branches on whether a browser is connected")

    def test_both_tiers_share_the_same_live_session_guard(self):
        body = self._producer_body()
        # index + triage appends sit inside one `if _live_map():` block (same guard for both)
        guard = re.search(r"if _live_map\(\) and not _retry_paused_on\(\):\n(.*?)\n            for t in tiers:", body, re.S)
        self.assertTrue(guard, "the two tiers are guarded by a single _live_map() and not _retry_paused_on() check")
        block = guard.group(1)
        self.assertIn('name="index"', block)
        self.assertIn('name="triage"', block)

    def test_backstop_is_short(self):
        body = self._producer_body()
        self.assertIn("_producer_wake.wait(3)", body, "the event-poke backstop is 3s")
        self.assertNotIn("_producer_wake.wait(20)", body, "the old 20s backstop is gone")


if __name__ == "__main__":
    unittest.main()
