#!/usr/bin/env python3
"""The feed card's "retrying since HH:MM" chip (the user 2026-07-09): an api-retry storm INSIDE an open
turn used to render as plain healthy Working — the API-error badge (_api_error) is gated on the session
being IDLE, and a storm keeps the turn open, so nimbus sat retrying for ~80 minutes with nothing on the
card and auto-nudge correctly silent. _session_retrying reads the live backend state ("retrying", the SDK
snapshot) plus the states log for the CURRENT stretch's start, and build_feed rides it onto the working
card. Synthetic fixtures only."""
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
km = load_source("romp_kernel_retry", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781100000


class SessionRetrying(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        (jd.STATE / "states").mkdir(parents=True)

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def _states(self, rows):
        p = jd.STATE / "states" / (SID + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_only_a_live_retrying_state_arms_the_chip(self):
        self._states([{"t": T0, "state": "retrying"}])
        self.assertIsNone(km._session_retrying(SID, None), "no live row (dormant) → no chip")
        self.assertIsNone(km._session_retrying(SID, {"state": "working"}), "an ordinary working turn → no chip")
        self.assertIsNone(km._session_retrying(SID, {"state": "waiting"}), "idle → the _api_error path owns it")

    def test_since_is_the_start_of_the_contiguous_stretch(self):
        self._states([{"t": T0, "state": "working"},
                      {"t": T0 + 100, "state": "retrying"},
                      {"t": T0 + 200, "state": "retrying"},
                      {"t": T0 + 300, "state": "retrying"}])
        got = km._session_retrying(SID, {"state": "retrying", "retryCount": 7})
        self.assertEqual({"since": got["since"], "count": got["count"]}, {"since": T0 + 100, "count": 7},
                         "the stretch dates from its FIRST retrying row, not the latest attempt")

    def test_a_recovery_ends_the_stretch_so_a_new_storm_dates_from_its_own_start(self):
        # the nimbus shape: storm → brief recovery (retriesRecovered + working) → storm again
        self._states([{"t": T0, "state": "retrying"},
                      {"t": T0 + 100, "state": "retrying"},
                      {"t": T0 + 200, "retriesRecovered": 12},
                      {"t": T0 + 200, "state": "working"},
                      {"t": T0 + 300, "state": "retrying"},
                      {"t": T0 + 400, "state": "retrying"}])
        got = km._session_retrying(SID, {"state": "retrying"})
        self.assertEqual(got["since"], T0 + 300,
                         "the working row ended the first stretch — since is the CURRENT storm's start")

    def test_overlay_rows_do_not_bound_a_stretch(self):
        # awaiting/recovery overlay records interleave with state rows and carry no "state" key
        self._states([{"t": T0, "state": "retrying"},
                      {"t": T0 + 50, "awaiting": False},
                      {"t": T0 + 60, "retriesRecovered": 3},
                      {"t": T0 + 100, "state": "retrying"}])
        got = km._session_retrying(SID, {"state": "retrying"})
        self.assertEqual(got["since"], T0, "overlay records are not state transitions — the stretch holds")

    def test_no_states_row_yet_renders_timeless(self):
        # the live snapshot can lead the log by a beat — the chip still shows, just without a time
        got = km._session_retrying(SID, {"state": "retrying", "retryCount": 2})
        self.assertEqual({"since": got["since"], "count": got["count"]}, {"since": None, "count": 2})

    def test_junk_retry_count_degrades_to_zero(self):
        self._states([{"t": T0, "state": "retrying"}])
        got = km._session_retrying(SID, {"state": "retrying", "retryCount": "lots"})
        self.assertEqual(got["count"], 0, "a malformed count never crashes the feed build")

    def test_the_storms_own_detail_rides_along(self):
        """The chip and the bell entry name WHAT is failing, not just that a storm exists (the user
        2026-07-29). Same retryInfo the chat's retrying element reads, so all three agree."""
        self._states([{"t": T0, "state": "retrying"}])
        got = km._session_retrying(SID, {"state": "retrying", "retryCount": 7,
                                         "retryInfo": {"max": 10, "status": 529, "networkDown": False,
                                                       "rateLimitType": None}})
        self.assertEqual((got["max"], got["status"]), (10, 529))
        self.assertIs(got["networkDown"], False)

    def test_a_snapshot_with_no_detail_still_builds(self):
        """A storm whose payload romp couldn't read must not break the chip — the detail is optional."""
        self._states([{"t": T0, "state": "retrying"}])
        got = km._session_retrying(SID, {"state": "retrying", "retryCount": 3, "retryInfo": None})
        self.assertEqual(got["count"], 3)
        self.assertIsNone(got["status"])


class FeedCardRetryingChip(unittest.TestCase):
    """build_feed computes the signal once per session and rides it ONLY on the working card — chip, not a
    column move (an api storm is in motion, not a block; mirrors the interrupting badge's source pin)."""

    def test_build_feed_computes_and_gates_the_retrying_chip(self):
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertIn("sess_retrying = _session_retrying(fsid, tm)", src,
                      "the signal comes from the live backend row — the same one the chat chip reads")
        self.assertIn('"retrying": (sess_retrying if column == "working" else None)', src,
                      "the chip rides the working card only; no column move")


if __name__ == "__main__":
    unittest.main()
