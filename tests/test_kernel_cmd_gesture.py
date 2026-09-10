"""Durable command-gesture chips (the user 2026-08-14): a /model-/effort-/auth-style pick synthesizes a live
chip on the user's side of the chat, but the chip lives only in the backend's in-memory _live tail and
prune_live's stale_cmd retires it on the next human turn — the user's own gesture then vanished from their
side of the history, while the left-rail "effort set to X" note (the applied moment) stayed. Mirror the
effortApplied mechanism at the REQUEST moment: the backend writes a durable {"t":…,"cmdGesture":"/effort
high"} line alongside the live chip (same t, same text), and build_session interleaves a persistent
`cmdGesture` event by time, DEDUP'd by (t, text) against a still-live chip so the gesture never doubles.
Right side keeps what you did; the applied note keeps that it happened.

Behavioural tests of the marker (append_cmd_gesture) + the kernel reader (_cmd_gestures), plus source pins on
the three write sites and the build interleave/dedup.
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
km = load_source("romp_kernel_cmdg", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_cmdg", os.path.join(BIN, "romp_sdk_backend.py"))
BACKEND_SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()


class CmdGestureMarkerRoundTrip(unittest.TestCase):
    """append_cmd_gesture writes to states/<sid>.jsonl; _cmd_gestures reads it back — and the plain
    state / awaiting / recovery / effort readers must SKIP it (each filters by its own key)."""
    def _states_dir(self):
        (km.jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        return km.jd.STATE

    def test_write_then_read_returns_gestures_oldest_first(self):
        state_dir = self._states_dir()
        sid = "TESTHOST-cmdg-1"
        (km.jd.STATE / "states" / (sid + ".jsonl")).unlink(missing_ok=True)
        sb.append_cmd_gesture(state_dir, sid, "/effort high", t=1000)
        sb.append_cmd_gesture(state_dir, sid, "/model sonnet", t=2000)
        self.assertEqual(km._cmd_gestures(sid),
                         [{"t": 1000, "cmd": "/effort high"}, {"t": 2000, "cmd": "/model sonnet"}])

    def test_no_file_or_no_markers_is_empty(self):
        self.assertEqual(km._cmd_gestures("TESTHOST-nope-cmdg"), [])

    def test_gesture_markers_do_not_disturb_the_other_readers(self):
        state_dir = self._states_dir()
        sid = "TESTHOST-cmdg-2"
        p = km.jd.STATE / "states" / (sid + ".jsonl")
        p.unlink(missing_ok=True)
        sb.append_state(state_dir, sid, "working", t=10)
        sb.append_cmd_gesture(state_dir, sid, "/effort high", t=20)      # interleaved, later
        sb.append_effort_applied(state_dir, sid, "high", t=30)
        self.assertEqual(km._last_state(sid)[0], "working", "the gesture line has no 'state' key → skipped")
        self.assertEqual(km._effort_changes(sid), [{"t": 30, "effort": "high"}], "effort reader skips it too")
        self.assertEqual(km._cmd_gestures(sid), [{"t": 20, "cmd": "/effort high"}], "and it ignores the effort line")
        # an empty value is not surfaced
        p.write_text(json.dumps({"t": 40, "cmdGesture": ""}) + "\n")
        self.assertEqual(km._cmd_gestures(sid), [])


class CmdGestureSourcePins(unittest.TestCase):
    def test_backend_writes_the_marker_beside_each_synthesized_live_chip(self):
        # every setter that synthesizes a live command chip (set_model / set_effort / set_auth) writes the
        # durable twin with the SAME t and disp, so build_session's (t, text) dedup holds while the chip is
        # live and the durable event takes over seamlessly once stale_cmd retires it. The three share ONE
        # builder (_ack_cmd_chip — so the chip fires on a dormant session too); the property is pinned on
        # the builder, and every setter must go through it.
        for cmd in ("/model", "/effort", "/auth"):
            self.assertEqual(BACKEND_SRC.count('self._ack_cmd_chip(sid, "%s", "%s " + value, ' % (cmd, cmd)), 1, cmd)
        i = BACKEND_SRC.index("def _ack_cmd_chip(")
        self.assertIn('uid = "cmd:%d:%s" % (t, command.lstrip("/"))', BACKEND_SRC[i:i + 3000])
        j = BACKEND_SRC.index("append_cmd_gesture(self.state_dir, sid, disp, t=t)", i)
        k = BACKEND_SRC.index("self._wake_push()", i)
        self.assertLess(j, k, "the marker is on disk before the push that rebuilds the chat")
        self.assertEqual(BACKEND_SRC.count("append_cmd_gesture(self.state_dir, sid, disp, t=t)"), 1, "one builder, no stray copies")

    def test_build_session_interleaves_and_dedups_against_the_live_chip(self):
        src = inspect.getsource(km.build_session)
        # the durable store is still the source; since T131 the live render floors it at the last
        # episode boundary so a /clear's fresh thread doesn't inherit the old episode's gestures
        self.assertIn("gestures = _past_floor(_cmd_gestures(sid))", src)
        self.assertIn('if (_cg["t"], _cg["cmd"]) in _live_cmd_keys:', src)
        self.assertIn('events.append({"kind": "cmdGesture", "cmd": _cg["cmd"], "ts": iso(_cg["t"]),', src)
        # flushed by the SAME time-gate as the other durable notes, and BEFORE the efforts loop, so a
        # same-second "/effort X" gesture renders above its "effort set to X" applied note
        gi = src.index('while _cgi < len(gestures) and (upto is None or gestures[_cgi]["t"] <= upto):')
        ei = src.index('while _ei < len(efforts) and (upto is None or efforts[_ei]["t"] <= upto):')
        self.assertLess(gi, ei, "gesture flush precedes the effort-applied flush")
        # the dedup keys come from the LIVE-MERGED atoms' (t, echo text) — the exact pair the setters stamp
        self.assertIn('_live_cmd_keys.add((int(_a.get("t") or 0), _a["_echo_text"]))', src)


if __name__ == "__main__":
    unittest.main()
