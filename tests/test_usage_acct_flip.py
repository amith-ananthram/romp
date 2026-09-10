#!/usr/bin/env python3
"""Bars drop the INSTANT the login that produced them is gone (the user 2026-08-04, who logged out and
still saw the old bars: they always waited for the next reading, which after a logout never comes —
get_usage times out on API-key auth and RateLimitEvents stop). Snapshot writers stamp `acct` (whose
reading this is, the kernel\'s opaque login digest); _usage() compares it per push against the CURRENT
login from the credential store, the authority on login state. Synthetic fixtures only."""
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
km = load_source("romp_kernel_acctflip", os.path.join(BIN, "romp-kernel"))
jd = km.jd

WINDOWS = {"t": 1785898746, "acct": "aaaaaaaaaaaa",
           "five_hour": {"pct": 46, "resets_at": 1785907200},
           "seven_day": {"pct": 21, "resets_at": 1786388400}}


class AcctFlip(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state, jd.STATE = jd.STATE, Path(self.td.name)
        self.saved_acct = km._claude_account

    def tearDown(self):
        jd.STATE = self.saved_state
        km._claude_account = self.saved_acct
        self.td.cleanup()

    def _write(self, o):
        (jd.STATE / "usage.json").write_text(json.dumps(o))

    def test_same_login_keeps_its_bars(self):
        self._write(WINDOWS)
        km._claude_account = lambda: "aaaaaaaaaaaa"
        u = km._usage()
        self.assertIsNotNone(u)
        self.assertEqual(u["fiveHour"]["pct"], 46)

    def test_logout_drops_the_bars_immediately(self):
        self._write(WINDOWS)
        km._claude_account = lambda: ""             # the credential store says: nobody signed in
        self.assertIsNone(km._usage(), "the reading describes no present allowance — no bars, no wait")

    def test_a_different_login_drops_the_stale_readings_too(self):
        self._write(WINDOWS)
        km._claude_account = lambda: "bbbbbbbbbbbb"
        self.assertIsNone(km._usage(), "another account\'s fossil must not masquerade as this login\'s")

    def test_legacy_unstamped_file_with_a_login_keeps_showing(self):
        legacy = dict(WINDOWS); legacy.pop("acct")
        self._write(legacy)
        km._claude_account = lambda: "cccccccccccc"
        self.assertIsNotNone(km._usage(), "same-or-different is unknowable on a legacy file — the next reading stamps it")

    def test_legacy_unstamped_files_never_consult_the_live_login(self):
        # Deliberate: keying an UNSTAMPED file on the machine's real credential store would couple every
        # fixture and CI run to it. A pre-upgrade fossil on a logged-out machine still clears via the
        # #208 auth flip at its next session init; every new reading is stamped from here on.
        legacy = dict(WINDOWS); legacy.pop("acct")
        self._write(legacy)
        km._claude_account = lambda: ""
        self.assertIsNotNone(km._usage(), "unstamped → shown as before, regardless of login state")

    def test_logout_on_an_api_key_machine_falls_to_the_spend_chip(self):
        o = dict(WINDOWS); o["apiKey"] = True       # windows + marker (an auth flip raced a late reading)
        self._write(o)
        km._claude_account = lambda: ""
        u = km._usage()
        self.assertTrue(u and u.get("apiKey"), "the spend chip takes the slot, never the fossil bars")

    def test_both_writers_stamp_whose_reading_it_is(self):
        base = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        src = open(os.path.join(base, "kernel", "sdk_backend.py")).read()
        self.assertIn("def acct_digest() -> str:", src)
        self.assertEqual(src.count('"acct": acct_digest()'), 2,
                         "the get_usage snapshot writer AND the RateLimitEvent merge writer both stamp")


if __name__ == "__main__":
    unittest.main()
