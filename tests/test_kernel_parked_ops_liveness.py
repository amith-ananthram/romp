#!/usr/bin/env python3
"""Parked ops deliver on the SETTLE event, independent of the judge pass (2026-09-03).

The kernel parks a user's input while its session is busy, compacting, or limit-held, and delivers the
queue FIFO once the session is quiet (_apply_pending_ops). Until this change the ONLY caller of that
function was the tail of the judge producer's pass, after an untimed join on the judge tiers — so every
parked op in every session waited for the judges to finish. A judge pass can run for hours: one session's
closer sweep, alarm-killed turn after turn, held a pass for 6h22m, and a typed /clear parked mid-turn sat
as a queued chip the whole time until the user cancelled it. The drain now rides the pusher cycle, woken
by the backends' turn-end poke (_wake_kernel), by /tick, by every queue mutation, and by its own 0.5 s
backstop; the producer never touches it.

SYNTHETIC fixtures only: a placeholder uuid, invented texts.
"""
import inspect
import io
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from unittest import mock
from romp_load import load_source

from tests.conftest import thread_census, wait_for_census

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_parkedlive", os.path.join(BIN, "romp-kernel"))

# The ACCOUNT gate is a separate axis (tests/test_kernel_limit_queue.py); pinned off so the real
# machine's usage.json can never make these tests park for a reason none of them is about.
km._limit_hold = lambda sid: None

SID = "11111111-2222-3333-4444-555555555555"

# Every pusher-cycle job except the drain, quieted so a cycle run here does exactly one thing.
_OTHER_JOBS = ("_push_all", "_lift_spent_awaiting", "_death_sweep_tick", "_end_on_idle_sweep",
               "_deferral_sweep_tick", "_auto_nudge_tick", "_interrupt_block_tick", "_auto_pause_on_limit",
               "_usage_poll_tick", "_auto_pause_on_spend_limit", "_auto_resume_retry",
               "_auto_resume_session_retry", "_auto_retry_tick", "_idle_queue_drive_tick",
               "_clear_done_working_notes")


class _FakeBackend:
    """A backend that cannot forward its own sends (the ABC's default), so a send parks while the turn is open."""

    def __init__(self):
        self.calls = []
        self.open = True

    def busy(self, sid):
        return self.open

    def forwards_sends(self):
        return False

    def send(self, sid, text):
        self.calls.append(("send", text))
        self.open = True                  # as SdkBackend.send: the turn is pending under the lock before send() returns
        return True

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True

    def turn_seq(self, sid):
        return 0


class DeliveryRidesTheSettle(unittest.TestCase):
    def setUp(self):
        self._census0 = thread_census()
        self.be = _FakeBackend()
        self._patches = [
            mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be)),
            mock.patch.object(km, "_compacting_now", lambda sid, **k: False),
            mock.patch.object(km, "_mark_compacting", lambda sid: None),
            mock.patch.object(km, "_names_snapshot", lambda: {}),
            mock.patch.object(km, "_live_map", lambda: {SID: {"state": "waiting", "backend": "sdk"}}),
        ] + [mock.patch.object(km, name, lambda *a, **k: None) for name in _OTHER_JOBS]
        for p in self._patches:
            p.start()
        km._pending_ops.clear()
        km._moving.clear()
        km._drain_hold.clear()
        km._pusher_wake.clear()
        km._producer_wake.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        km._pending_ops.clear()
        km._moving.clear()
        km._drain_hold.clear()
        producer = getattr(self, "producer", None)
        if producer is None or not producer.is_alive():
            km._LOOPS_STOP.clear()                   # a producer still alive keeps the stop set: it exits at its next check
        self.assertEqual(wait_for_census(self._census0), [], "no thread of this test outlives it (T282)")

    def test_parked_op_delivers_on_settle_while_a_judge_pass_is_stuck(self):
        gate = threading.Event()             # the judge tiers block here: a closer sweep that never returns
        ran_on = []                          # the threads that ran the drain
        real_apply = km._apply_pending_ops

        def counting_apply():
            ran_on.append(threading.current_thread().name)
            return real_apply()

        # The pass stays stuck for the whole test; at its end the stop seam is set FIRST and the gate released
        # SECOND, so the tiers return, the pass completes under these same stubs, and the producer ends at its
        # next turn of the wheel instead of outliving the module (T282).
        with mock.patch.object(km, "_run_tier", lambda fn: gate.wait()), \
             mock.patch.object(km, "_retry_paused_on", lambda: False), \
             mock.patch.object(km, "_episode_boundary_tick", lambda now: None), \
             mock.patch.object(km, "_begin_goals_pass", lambda: None), \
             mock.patch.object(km, "_end_goals_pass", lambda: None), \
             mock.patch.object(km, "_compact_goal_stores", lambda: 0), \
             mock.patch.object(km, "_sdk", lambda: None), \
             mock.patch.object(km.jd, "begin_pass_frame", lambda: False), \
             mock.patch.object(km.jd, "end_pass_frame", lambda f: None), \
             mock.patch.object(km.jd, "consume_judge_recovery", lambda: False), \
             mock.patch.object(km, "_apply_pending_ops", counting_apply):
            producer = self.producer = threading.Thread(target=km._producer, name="producer-under-test", daemon=True)
            producer.start()
            deadline = time.time() + 5
            while time.time() < deadline and not any(t.name == "triage" for t in threading.enumerate()):
                time.sleep(0.01)
            self.assertTrue(any(t.name == "triage" for t in threading.enumerate()),
                            "a judge pass is in flight, stuck on its tiers")
            # a typed slash command lands while the turn is open → parked (the standing rule)
            km._send_or_park(self.be, SID, "/frobnicate now")
            self.assertEqual(km._pending_ops[SID], [("command", "/frobnicate now", None)])
            self.assertEqual(self.be.calls, [])
            # the turn SETTLES: the backend pokes the kernel exactly as its ResultMessage path does. (On a
            # tree without _wake_kernel the poke is what the backends did before — both wakes by hand — so
            # this test reaches the delivery assertion there and fails on the bug, not on a missing name.)
            self.be.open = False
            km._pusher_wake.clear()
            poke = getattr(km, "_wake_kernel", None) or (lambda: (km._producer_wake.set(), km._pusher_wake.set()))
            poke()
            self.assertTrue(km._pusher_wake.is_set(), "the settle wakes the thread that delivers")
            self.assertTrue(km._producer_wake.is_set(), "…and the judges, as before")
            km._pusher_cycle()                           # ONE pusher cycle, while the judge pass is still stuck
            self.assertFalse(gate.is_set())
            self.assertTrue(producer.is_alive())
            self.assertEqual(self.be.calls, [("send", "/frobnicate now")], "delivered alone, as a fresh prompt")
            self.assertNotIn(SID, km._pending_ops)
            self.assertEqual(ran_on, [threading.current_thread().name],
                             "the drain ran on this cycle and never on the producer")
            km._LOOPS_STOP.set()                     # the loop ends at its next turn of the wheel...
            gate.set()                               # ...which comes once the stuck tiers return and the pass completes
            km._producer_wake.set()
            producer.join(5)
            self.assertFalse(producer.is_alive(), "the producer ended on the stop seam once its pass finished")

    def test_two_parks_drain_one_per_settle_in_park_order(self):
        self.assertTrue(km._send_or_park(self.be, SID, "one", echo="human"))   # cannot forward: a send parks mid-turn
        self.assertTrue(km._compact_or_park(self.be, SID), "the compact parks behind it (queued)")
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["send", "compact"])
        self.be.open = False                                                    # settle #1
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one")], "the send fired; the compact waits for ITS turn to end")
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["compact"])
        self.assertNotIn(SID, km._drain_hold, "send() closed the busy gate before it returned: nothing to hold for")
        self.assertTrue(self.be.open, "the delivered send's turn is in flight")
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one")], "nothing fires into an open turn")
        self.be.open = False                                                    # settle #2
        km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one"), ("send", "/compact")])
        self.assertNotIn(SID, km._pending_ops)
        self.assertNotIn(SID, km._drain_hold, "an authoritative busy() needs no hold between deliveries")

    def test_a_backend_that_cannot_say_busy_still_holds_for_the_fallback_window(self):
        # a backend with NO busy() at all (a test fake, a future backend): nothing can ever observe its gate
        # close, so the prompt hold arms and only its clock fallback (_PROMPT_HOLD_S) releases it —
        # the op behind never fires back-to-back into a turn that may be opening
        class _Mute(_FakeBackend):
            def busy(self, sid):
                return None
        self.be = _Mute()
        with mock.patch.object(km, "_working_now", lambda sid: True):             # the turn is open: both park
            km._send_or_park(self.be, SID, "one", echo="human")
            km._compact_or_park(self.be, SID)
        self.assertEqual([op[0] for op in km._pending_ops[SID]], ["send", "compact"])
        with mock.patch.object(km, "_PROMPT_HOLD_S", 3600.0):
            km._pusher_cycle()                                                    # the cached parse reads idle
            self.assertEqual(self.be.calls, [("send", "one")], "the send fired")
            self.assertIn(SID, km._drain_hold, "…and the sid is held while its prompt lands")
            km._pusher_cycle()                                                    # back-to-back, as a wake would
            self.assertEqual(self.be.calls, [("send", "one")], "the compact does NOT fire into the opening turn")
        far = time.monotonic() + 7200.0                                           # the fallback window passed
        with mock.patch.object(km.time, "monotonic", lambda: far), redirect_stderr(io.StringIO()):
            km._pusher_cycle()
        self.assertEqual(self.be.calls, [("send", "one"), ("send", "/compact")])

    def test_a_dead_sessions_queue_drop_takes_its_hold_with_it(self):
        # the hold check precedes delivery, so a hold present when send() raises can only have been armed
        # DURING the delivery (a concurrent writer — the move thread's re-park); the drop must not leave it behind
        class _Dead(_FakeBackend):
            def send(self, sid, text):
                km._hold_drain(sid, 60.0)
                raise RuntimeError("the session is gone")
        self.be = _Dead()
        self.be.open = False
        km._pending_ops[SID] = [("send", "one", "human")]
        with redirect_stderr(io.StringIO()):
            km._apply_pending_ops()
        self.assertNotIn(SID, km._pending_ops, "a dead session's queue is dropped")
        self.assertNotIn(SID, km._drain_hold, "…and its hold with it")

    def test_wake_kernel_sets_both_events_and_is_the_backends_poke(self):
        km._producer_wake.clear()
        km._pusher_wake.clear()
        km._wake_kernel()
        self.assertTrue(km._producer_wake.is_set() and km._pusher_wake.is_set())
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertEqual(src.count("poke=_wake_kernel"), 2, "both backend constructors (SDK + Codex) poke the kernel")
        self.assertNotIn("poke=_producer_wake.set", src, "no backend pokes only the judges anymore")

    def test_delivery_moved_from_the_producer_to_the_pusher_cycle(self):
        self.assertNotIn("_apply_pending_ops()", inspect.getsource(km._producer), "the judge pass no longer gates delivery")
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_apply_pending_ops()", src, "the pusher cycle delivers the parked queue")
        self.assertLess(src.index("_apply_pending_ops()"), src.index("_push_all("),
                        "delivery precedes the push, so the delivered op's echo and retired chip ride it")

    def test_drain_does_not_wake_the_pusher_when_nothing_changed(self):
        self.be.open = False
        d = tempfile.mkdtemp()
        km._pending_ops[SID] = [("cwd", d, km._MOVE_BUSY_RETRIES, 0)]    # waits on turn_seq: nothing to do yet
        km._pusher_wake.clear()
        km._apply_pending_ops()
        self.assertEqual(km._pending_ops[SID], [("cwd", d, km._MOVE_BUSY_RETRIES, 0)], "still parked")
        self.assertFalse(km._pusher_wake.is_set(),
                         "a cycle that delivered nothing must not re-wake the thread it runs on (a hot loop)")
        km._pending_ops[SID] = [("model", "opus")]
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("model", "opus")])
        self.assertTrue(km._pusher_wake.is_set(), "a real delivery wakes the push that retires the chip")

    def test_a_cycle_resolves_a_held_sids_path_once(self):
        # review find on #904: the drain's gates each resolved a held sid's transcript path (a discover
        # fingerprint) every cycle; inside a cycle the resolution is memoized on the cycle's scope, and a
        # caller outside a cycle (a WS handler) still resolves fresh
        calls = []
        with mock.patch.object(km, "_sessions", lambda now: (calls.append(1), [])[1]):
            km._path_of(SID)
            km._path_of(SID)
            self.assertEqual(len(calls), 2, "outside a cycle every ask resolves fresh")
            calls.clear()
            km._live_scope.paths = {}
            try:
                km._path_of(SID)
                km._path_of(SID)
            finally:
                km._live_scope.paths = None
            self.assertEqual(len(calls), 1, "inside a cycle the second ask is the memo")
        km._pusher_cycle()
        self.assertIsNone(getattr(km._live_scope, "paths", None), "the memo ends with the cycle")

    def test_cancel_parked_logs_sid_and_kind_only(self):
        km._pending_ops[SID] = [("send", "a private sentence", "human")]
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.assertIsNone(km._cancel_parked(SID, 0, ""))
        line = buf.getvalue()
        self.assertIn(SID, line)
        self.assertIn("send", line)
        self.assertNotIn("a private sentence", line, "the body is user text — never logged")
        self.assertNotIn(SID, km._pending_ops)


if __name__ == "__main__":
    unittest.main()
