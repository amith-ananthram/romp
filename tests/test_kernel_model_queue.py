#!/usr/bin/env python3
"""A model change requested WHILE a session compacts is PARKED, not applied (the user 2026-07-01:
switching the model mid-compaction broke the compaction — the wanted behavior is a queued command that
takes effect after). The kernel parks it in _pending_model, build_session renders it as a queued
'/model …' bubble, and _apply_pending_ops (producer tick) fires it the moment compaction ends —
event-corroborated via the same _compacting signal the chip uses (compact_boundary / resumed work /
the 180s optimistic cap), so a park can never stick forever. SYNTHETIC fixtures only."""
import os
import time
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
km = load_source("romp_kernel_modelq", os.path.join(BIN, "romp-kernel"))

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

# The tmux PROMPT HOLD (_hold_drain: a tmux-shaped delivery holds the sid for a moment, tested in
# tests/test_kernel_parked_ops_liveness.py) is a separate axis: off here, so back-to-back
# _apply_pending_ops calls stand for successive cycles.
km._TMUX_PROMPT_HOLD_S = 0.0

SID = "11111111-2222-3333-4444-555555555555"


class _FakeBackend:
    def __init__(self):
        self.calls = []

    def set_model(self, sid, value):
        self.calls.append((sid, value))
        return True

    def busy(self, sid):
        return None   # no authoritative signal → _working_now falls back to the (idle) cached parse, as before


class ParkOrApply(unittest.TestCase):
    def setUp(self):
        self.be = _FakeBackend()
        self._saved = (km._compacting_now, km.Sessions.backend_for, km._push_all)
        km._push_all = lambda: None
        km._pending_ops.clear()

    def tearDown(self):
        (km._compacting_now, km.Sessions.backend_for, km._push_all) = self._saved
        km._pending_ops.clear()

    def test_not_compacting_applies_immediately(self):
        km._compacting_now = lambda sid: False
        km._set_model_or_park(self.be, SID, "sonnet")
        self.assertEqual(self.be.calls, [(SID, "sonnet")], "no compaction → the switch fires now")
        self.assertNotIn(SID, km._pending_ops)

    def test_compacting_parks_instead_of_applying(self):
        km._compacting_now = lambda sid: True
        km._set_model_or_park(self.be, SID, "opus")
        self.assertEqual(self.be.calls, [], "mid-compaction the backend is NOT touched — that broke the compaction")
        self.assertEqual(km._pending_ops.get(SID), [("model", "opus")])

    def test_repeat_change_while_parked_keeps_only_the_latest(self):
        km._compacting_now = lambda sid: True
        km._set_model_or_park(self.be, SID, "opus")
        km._set_model_or_park(self.be, SID, "sonnet")
        self.assertEqual(km._pending_ops.get(SID), [("model", "sonnet")],
                         "last pick wins IN PLACE — one queued command, not a pile")

    def test_apply_fires_when_compaction_ends_and_not_before(self):
        km._pending_ops[SID] = [("model", "opus")]
        km.Sessions.backend_for = lambda sid: self.be
        km._compacting_now = lambda sid: True
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "still compacting → still parked")
        self.assertIn(SID, km._pending_ops)
        km._compacting_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [(SID, "opus")], "compaction over → the parked switch fires")
        self.assertNotIn(SID, km._pending_ops, "consumed — never re-fired")

    def test_dead_session_park_is_dropped_not_retried(self):
        km._pending_ops[SID] = [("model", "opus")]
        km._compacting_now = lambda sid: False

        def dead(sid):
            raise RuntimeError("no such session")
        km.Sessions.backend_for = dead
        km._apply_pending_ops()                         # must not raise
        self.assertNotIn(SID, km._pending_ops, "a dead session's park is dropped, never retried forever")

    def test_the_pusher_cycle_delivers_the_parked_queue_not_the_producer(self):
        # 2026-09-03: delivery moved OFF the judge producer's tail — a pass can run for hours (one session's
        # closer sweep, alarm-killed turn after turn) and held every parked op hostage. It rides the pusher
        # cycle now, woken by the settle itself, and runs FIRST so the delivered op's echo rides the push.
        import inspect
        self.assertNotIn("_apply_pending_ops()", inspect.getsource(km._producer),
                         "the judge pass no longer gates delivery")
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_apply_pending_ops()", src, "the pusher cycle delivers the parked queue")
        self.assertLess(src.index("_apply_pending_ops()"), src.index("_push_all("), "…ahead of the push")


class CompactingNowGate(unittest.TestCase):
    """_compacting_now composes the REAL _compacting corroboration from cheap parts (cached parse only)."""

    def setUp(self):
        self._saved = (km._tmux_sessions, km._path_of, km._parse_cached)
        km._path_of = lambda sid: "/tmp/x.jsonl"
        km._compact_clicked.clear()

    def tearDown(self):
        (km._tmux_sessions, km._path_of, km._parse_cached) = self._saved
        km._compact_clicked.clear()

    def test_optimistic_click_reads_compacting_until_the_boundary_lands(self):
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": None}}
        km._parse_cached = lambda p: {"turns": []}
        km._compact_clicked[SID] = time.time()          # the kernel itself just sent /compact
        self.assertTrue(km._compacting_now(SID), "the optimistic click reads compacting at once")
        boundary = {"type": "system", "subtype": "compact_boundary", "t": int(time.time()) + 1}
        km._parse_cached = lambda p: {"turns": [{"t": 0, "end": 1, "ended": True, "atoms": [boundary],
                                                 "trigger": None, "id": "t1"}]}
        self.assertFalse(km._compacting_now(SID), "the compact_boundary event ends it — the parked switch can fire")

    def test_no_signal_reads_not_compacting(self):
        km._tmux_sessions = lambda: {SID: {"state": "waiting", "since": None}}
        km._parse_cached = lambda p: {"turns": []}
        self.assertFalse(km._compacting_now(SID))


class QueuedBubble(unittest.TestCase):
    def test_build_session_appends_the_parked_model_as_a_queued_command(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn("pending_ops = _pending_ops.get(sid) or []", src)
        self.assertIn('{"md": _parked_md(op), "park": j, "cancelable": True, **(_queued_romp_flags(op[1]) if op[0] == "send" else {})}', src,
                      "a parked model/effort renders as its slash-command chip, in park order — "
                      "cancelable since 2026-07-08 (_parked_md is the shared body renderer)")
        self.assertIn("if queued or pending_ops:", src,
                      "the queued indicator shows even when a park is the only pending item")


if __name__ == "__main__":
    unittest.main()
