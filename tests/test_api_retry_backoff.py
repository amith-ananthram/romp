#!/usr/bin/env python3
"""Auto-retry backs OFF (the user 2026-07-29): a usage-limited thread was collecting a ridiculous number
of attempts.

One-retry-per-error-episode (2026-07-20) stopped retries STACKING, but it does not slow anything down
when every attempt fails instantly: each failure writes a new error record, which is a new episode, so a
blocked session was re-tried on the client's 10s tick for as long as the block lasted. A usage limit
lasts minutes to hours and hammering it changes nothing.

Attempts now climb 1s, 5s, 15s, 45s, 2m, 5m, 15m, 30m and stay at 30m: a blip still recovers in seconds,
a real limit costs a handful of attempts. The ladder lives in the KERNEL because the retry tick runs per
open view, so any client-side spacing would divide by the number of dashboards.

Synthetic ids only; no sessions, no sends.
"""
import inspect
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_retryb", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class Ladder(unittest.TestCase):
    def tearDown(self):
        km._auto_retry_state.clear()

    def test_it_starts_in_a_second_and_climbs(self):
        self.assertEqual(km._retry_backoff(1), 1, "a blip must recover in a second, not in a minute")
        waits = [km._retry_backoff(n) for n in range(1, 9)]
        self.assertEqual(waits, [1, 5, 15, 45, 120, 300, 900, 1800])

    def test_it_never_narrows_and_never_stops(self):
        waits = [km._retry_backoff(n) for n in range(1, 40)]
        self.assertEqual(waits, sorted(waits), "a longer outage never retries MORE often")
        for w in waits:
            self.assertGreater(w, 0, "there is no 'stop' rung: it stops hurrying, not trying")

    def test_it_settles_at_half_an_hour(self):
        self.assertEqual(km._retry_backoff(50), 1800)
        self.assertEqual(max(km.RETRY_BACKOFF), 1800)

    def test_the_half_hour_cadence_is_reached_in_well_under_an_hour(self):
        # the point of the early rungs: a real limit costs a handful of attempts, not hundreds, and the
        # thread is not left waiting half an hour for its second try
        to_cap = sum(km._retry_backoff(n) for n in range(1, len(km.RETRY_BACKOFF)))
        self.assertLess(to_cap, 30 * 60, "climbing to the cap must cost less than one capped wait")
        self.assertGreater(to_cap, 10 * 60)

    def test_an_attempt_records_the_next_deadline(self):
        km._note_retry_sent(SID)
        n, nxt = km._retry_gate_state(SID)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(nxt - time.time(), 1, delta=2)
        km._note_retry_sent(SID)
        n2, nxt2 = km._retry_gate_state(SID)
        self.assertEqual(n2, 2)
        self.assertAlmostEqual(nxt2 - time.time(), 5, delta=2, msg="the second failure steps up a rung")

    def test_a_manual_retry_resets_the_ladder_but_still_holds_the_auto_loop_off(self):
        for _ in range(6):
            km._note_retry_sent(SID)
        self.assertGreaterEqual(km._retry_gate_state(SID)[1] - time.time(), 100)
        km._note_retry_sent(SID, manual=True)
        n, nxt = km._retry_gate_state(SID)
        self.assertEqual(n, 0, "you asked for this one, so the next automatic attempt starts over")
        self.assertGreater(nxt, time.time(), "…but the auto loop cannot stack an attempt behind yours")
        self.assertLess(nxt - time.time(), 5)

    def test_recovery_resets_the_ladder(self):
        for _ in range(4):
            km._note_retry_sent(SID)
        km._clear_retry_backoff(SID)
        self.assertEqual(km._retry_gate_state(SID), (0, 0))
        km._note_retry_sent(SID)
        self.assertAlmostEqual(km._retry_gate_state(SID)[1] - time.time(), 1, delta=2,
                               msg="a later outage starts at the bottom rung")

    def test_the_map_cannot_grow_without_bound(self):
        for i in range(300):
            km._note_retry_sent("sid-%d" % i)
        self.assertLessEqual(len(km._auto_retry_state), 300)


class Wiring(unittest.TestCase):
    def test_the_route_refuses_an_auto_retry_inside_the_backoff_window(self):
        src = inspect.getsource(km._fire_api_retry)
        self.assertIn("if time.time() < _retry_gate_state(sid)[1]:", src)
        self.assertIn("_note_retry_sent(sid, manual=manual)", src)
        # …and the send happens BEFORE the stamp, so a refused send doesn't burn a rung
        self.assertLess(src.index("be.send(sid, RETRY_MSG)"), src.index("_note_retry_sent(sid"))

    def test_recovery_clears_the_ladder_on_the_next_tick(self):
        src = inspect.getsource(km._fire_api_retry)
        self.assertIn("_clear_retry_backoff(sid)", src)

    def test_a_manual_retry_is_never_gated_by_the_backoff(self):
        src = inspect.getsource(km._fire_api_retry)
        gate = src[src.index("if time.time() < _retry_gate_state(sid)[1]:")]
        self.assertTrue(gate)
        # the whole gate block sits under `if not manual:`
        head = src[:src.index("if time.time() < _retry_gate_state(sid)[1]:")]
        self.assertIn('if not manual:', head)

    def test_the_status_payload_publishes_the_schedule(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"retryNextAt": int(_retry_gate_state(sid)[1]) or None,', src)
        self.assertIn('"retryTries": _retry_gate_state(sid)[0] or None,', src)


if __name__ == "__main__":
    unittest.main()
