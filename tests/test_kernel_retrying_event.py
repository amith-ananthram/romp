"""API-retry visibility in the chat (the user 2026-07-08): a session stalled on an api_retry backoff (the CLI
retrying a rate-limited / overloaded request) used to be visible ONLY as the amber tab border, with nothing
in the chat ("the border says retrying but the chat shows no sign"). Now build_session emits a transient
`retrying` element (with the live attempt count) while state=="retrying", and a persistent `retried`
("Recovered after N retries") note, anchored where output resumed, once the storm clears.

Source pins on build_session + the SDK backend, plus behavioral tests of the durable recovery marker
(append_retry_recovered) and the kernel reader/interleave (_retry_recoveries)."""
import inspect
import json
import os
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
km = load_source("romp_kernel_retrying", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_retrying", os.path.join(BIN, "romp_sdk_backend.py"))
BACKEND_SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


class RetryingEmit(unittest.TestCase):
    def test_build_session_emits_a_retrying_element_while_state_is_retrying(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('if (tm0 or {}).get("state") == "retrying":', src)
        self.assertIn('events.append({"kind": "retrying", "retries": int((tm0 or {}).get("retryCount") or 0),', src)
        self.assertIn('"info": (tm0 or {}).get("retryInfo") or None', src,
                      "the attempt's detail (attempt/max, error, next-attempt epoch) rides the event (the user 2026-07-10)")

    def test_the_retrying_notice_precedes_the_queued_bubble(self):
        # like the compacting / reconnecting elements, it sits ABOVE any queued/provisional message
        src = inspect.getsource(km.build_session)
        i_retry = src.index('events.append({"kind": "retrying"')
        i_queued = src.index('events.append({"kind": "queued"')
        self.assertGreater(i_retry, 0)
        self.assertGreater(i_queued, i_retry)

    def test_sessions_live_passes_retryCount_through_from_the_sdk_backend(self):
        src = inspect.getsource(km.Sessions.live)
        self.assertIn('"retryCount": int(st.get("retryCount") or 0),', src)

    def test_backend_snapshot_exposes_the_retry_count(self):
        self.assertIn('"retryCount": self.retry_count,', BACKEND_SRC)

    def test_backend_counts_each_api_retry_and_writes_a_recovery_marker(self):
        # one backoff attempt → +1; first real output after a storm → the durable marker, then reset
        self.assertIn('self.retry_count += 1', BACKEND_SRC)
        # (2026-07-25: an ERROR-stamped settle takes the gave-up branch first — recovery is the elif)
        self.assertIn('elif self.retrying and self.retry_count:   # first real output after a storm', BACKEND_SRC)
        self.assertIn('append_retry_gave_up(self.backend.state_dir, self.sid, self.retry_count,', BACKEND_SRC)
        self.assertIn('append_retry_recovered(self.backend.state_dir, self.sid, self.retry_count)', BACKEND_SRC)
        # reset on recovery AND on turn-end (a turn that errored out without recovering leaves NO note)
        self.assertIn('self.retry_count = 0', BACKEND_SRC)


class RecoveryMarkerRoundTrip(unittest.TestCase):
    """append_retry_recovered writes to states/<sid>.jsonl; _retry_recoveries reads it back — and the plain
    state/awaiting readers must SKIP the new record shape (they filter by their own keys)."""
    def _states_dir(self):
        d = km.jd.STATE / "states"
        d.mkdir(parents=True, exist_ok=True)
        return km.jd.STATE

    def test_write_then_read_returns_recoveries_oldest_first(self):
        state_dir = self._states_dir()
        sid = "TESTHOST-retry-1"
        (km.jd.STATE / "states" / (sid + ".jsonl")).unlink(missing_ok=True)
        sb.append_retry_recovered(state_dir, sid, 3, t=1000)
        sb.append_retry_recovered(state_dir, sid, 1, t=2000)
        got = km._retry_recoveries(sid)
        self.assertEqual(got, [{"t": 1000, "retries": 3}, {"t": 2000, "retries": 1}])

    def test_no_file_or_no_markers_is_empty(self):
        self.assertEqual(km._retry_recoveries("TESTHOST-nope-xyz"), [])

    def test_recovery_markers_do_not_disturb_the_plain_state_reader(self):
        # the recovery line has its own "retriesRecovered" key and NO "state" key → _last_state ignores it
        state_dir = self._states_dir()
        sid = "TESTHOST-retry-2"
        p = km.jd.STATE / "states" / (sid + ".jsonl")
        p.unlink(missing_ok=True)
        sb.append_state(state_dir, sid, "working", t=10)
        sb.append_retry_recovered(state_dir, sid, 2, t=20)   # interleaved, later — must not become "the state"
        self.assertEqual(km._last_state(sid)[0], "working")   # _last_state returns (value, t); the marker is skipped
        # a zero/absent count is not surfaced as a recovery
        p.write_text(json.dumps({"t": 30, "retriesRecovered": 0}) + "\n")
        self.assertEqual(km._retry_recoveries(sid), [])


if __name__ == "__main__":
    unittest.main()
