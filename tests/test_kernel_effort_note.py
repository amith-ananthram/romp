"""Durable "effort set to X" note (the user 2026-07-16): an /effort change reconnects the SDK session to apply
--effort and leaves NO transcript record, so the only trace was the synthesized /effort chip — which prunes
the instant the next message lands (stale_cmd). History then kept no record of WHEN effort changed, and the
change's disappearance read as a glitch. Mirror the api-retry recovery marker: the backend writes a durable
{"t":…,"effortApplied":X} line at the reconnect landing (the apply moment), and build_session interleaves a
persistent `effortApplied` note by time.

Behavioural tests of the marker (append_effort_applied) + the kernel reader/interleave (_effort_changes),
plus a source pin on the write site + the build interleave.
"""
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
km = load_source("romp_kernel_effort", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_effort", os.path.join(BIN, "romp_sdk_backend.py"))
BACKEND_SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


class EffortMarkerRoundTrip(unittest.TestCase):
    """append_effort_applied writes to states/<sid>.jsonl; _effort_changes reads it back — and the plain
    state / awaiting / recovery readers must SKIP it (each filters by its own key)."""
    def _states_dir(self):
        (km.jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        return km.jd.STATE

    def test_write_then_read_returns_changes_oldest_first(self):
        state_dir = self._states_dir()
        sid = "TESTHOST-effort-1"
        (km.jd.STATE / "states" / (sid + ".jsonl")).unlink(missing_ok=True)
        sb.append_effort_applied(state_dir, sid, "high", t=1000)
        sb.append_effort_applied(state_dir, sid, "low", t=2000)
        self.assertEqual(km._effort_changes(sid),
                         [{"t": 1000, "effort": "high"}, {"t": 2000, "effort": "low"}])

    def test_no_file_or_no_markers_is_empty(self):
        self.assertEqual(km._effort_changes("TESTHOST-nope-eff"), [])

    def test_effort_markers_do_not_disturb_the_other_readers(self):
        state_dir = self._states_dir()
        sid = "TESTHOST-effort-2"
        p = km.jd.STATE / "states" / (sid + ".jsonl")
        p.unlink(missing_ok=True)
        sb.append_state(state_dir, sid, "working", t=10)
        sb.append_effort_applied(state_dir, sid, "high", t=20)          # interleaved, later
        sb.append_retry_recovered(state_dir, sid, 2, t=30)
        self.assertEqual(km._last_state(sid)[0], "working", "the effort line has no 'state' key → skipped")
        self.assertEqual(km._retry_recoveries(sid), [{"t": 30, "retries": 2}], "recovery reader skips it too")
        self.assertEqual(km._effort_changes(sid), [{"t": 20, "effort": "high"}], "and it ignores the recovery line")
        # an empty value is not surfaced
        p.write_text(json.dumps({"t": 40, "effortApplied": ""}) + "\n")
        self.assertEqual(km._effort_changes(sid), [])


class EffortNoteSourcePins(unittest.TestCase):
    def test_backend_writes_the_marker_at_the_reconnect_landing(self):
        # written inside the `if self._effort_pending:` reconnect-clear block, so the timestamp is the moment
        # the new effort became REAL — not when it was requested (a busy session's in-flight turn ran the old
        # effort). append_effort_applied is called BEFORE the pending flag is cleared, so it still has the value.
        # self.backend.state_dir, not self.state_dir: a session has no state_dir of its own, and the typo
        # raised an AttributeError straight out of the connect path — killing the session thread on any
        # /effort switch that applied at reconnect (fixed 2026-07-28, found in the backend's crash log).
        self.assertIn("append_effort_applied(self.backend.state_dir, self.sid, self._effort_pending)", BACKEND_SRC)
        i = BACKEND_SRC.index("append_effort_applied(self.backend.state_dir")
        j = BACKEND_SRC.index('self._effort_pending = ""', i)
        self.assertLess(i, j, "the marker is written while _effort_pending still holds the applied value")

    def test_build_session_interleaves_the_durable_note_by_time(self):
        src = inspect.getsource(km.build_session)
        self.assertIn("efforts = _past_floor(_effort_changes(sid))", src)   # floored at the episode boundary since T131
        self.assertIn('events.append({"kind": "effortApplied", "effort": _e["effort"], "ts": iso(_e["t"]),', src)
        # flushed by the SAME time-gate as the recovery notes, so both stay ordered against the atoms
        self.assertIn("while _ei < len(efforts) and (upto is None or efforts[_ei][\"t\"] <= upto):", src)


if __name__ == "__main__":
    unittest.main()
