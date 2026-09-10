#!/usr/bin/env python3
"""The _placed_key live-twin guard (the user 2026-07-06): byte-identical prompts in DIFFERENT turns hash
to the same timestamp-invariant _seg_key — three crash-heal "kernel restarted" resume prompts — so the
first placed twin used to swallow every later twin's work-run as a "t drift" and whole turns of real work
never reached the goal tree (the stuck 'drag' card). With the current parse's live seg-id set, a fuzzy hit
counts only when the recorded key is ORPHANED (a genuine drift); a recorded key that is itself another
live segment never dedups its twins. Synthetic ids only."""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_twins", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
A = SID + ":1000:aabbccdd"          # placed earlier; identical text ⇒ same hash as B and C
B = SID + ":2000:aabbccdd"          # a genuine TWIN turn (same text, later)
C = SID + ":3000:aabbccdd"          # another twin
DRIFT = SID + ":1003:aabbccdd"      # the SAME segment as A after a t-shift re-parse (A orphaned)


class PlacedKeyTwins(unittest.TestCase):
    def test_exact_hit_always_dedups(self):
        self.assertTrue(jd._placed_key({A: SID + ":g1"}, A, live={A, B}))

    def test_live_twin_is_not_swallowed(self):
        # A is placed and still LIVE in this parse → B/C are different segments, not drifts of A
        pl = {A: SID + ":g1"}
        self.assertFalse(jd._placed_key(pl, B, live={A, B, C}))
        self.assertFalse(jd._placed_key(pl, C, live={A, B, C}))

    def test_orphaned_key_still_dedups_drift(self):
        # A's placement recorded, then a re-parse shifts its t → A gone from the live set, DRIFT present:
        # the fuzzy hit must hold (the original double-mint protection)
        pl = {A: SID + ":g1"}
        self.assertTrue(jd._placed_key(pl, DRIFT, live={DRIFT, B}))

    def test_no_live_set_keeps_legacy_behavior(self):
        # callers that don't thread `live` (courier, closer lookups) keep the old fuzzy dedup verbatim
        self.assertTrue(jd._placed_key({A: SID + ":g1"}, B))

    def test_suffixed_phases_ride_along(self):
        # a #p prompt-run key dedups against its own recorded #p, but not against a live twin's
        pl = {A + "#p": SID + ":g1"}
        self.assertTrue(jd._placed_key(pl, A + "#p", live={A}))
        self.assertFalse(jd._placed_key(pl, B + "#p", live={A, B}))

    def test_placement_of_respects_the_live_guard(self):
        pl = {A: SID + ":g1"}
        self.assertEqual(jd._placement_of(pl, A, live={A, B}), SID + ":g1")
        self.assertIsNone(jd._placement_of(pl, B, live={A, B}))
        self.assertEqual(jd._placement_of(pl, DRIFT, live={DRIFT}), SID + ":g1")


if __name__ == "__main__":
    unittest.main()
