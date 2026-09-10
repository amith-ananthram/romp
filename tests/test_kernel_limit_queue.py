#!/usr/bin/env python3
"""Input QUEUES while the account can't serve it — a usage limit or a monthly spend cap (the user
2026-07-24).

The failure this closes: on hitting a usage limit, every following gesture became its own little failure.
/compact came back refused ("this would take you over your limit"), the message typed after it went
straight out to the API and landed as a red API-error card, and the order the user meant to say things in
was lost. Nothing there needed a human — the account simply could not serve a request yet.

So a limit parks input in the SAME FIFO a compaction parks it in: messages AND slash commands, in press
order, delivered the moment the account can serve again. The limit is on the ACCOUNT, so /compact, /model
and /effort are exactly as un-servable as a message and hold their place in the sequence.

RELEASE is read from the event, never a timer romp invents:
- a rate window carries the API's own resetsAt, and _usage().limited goes false the moment that stamp
  passes, so the queue drains on the next producer pass;
- a spend cap has no readable reset, so the hold rides the retry-pause the spend-cap detector engages and
  lifts when that lifts.

SYNTHETIC fixtures only (placeholder ids, hostname TESTHOST).
"""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_limitqueue", os.path.join(BIN, "romp-kernel"))

# The tmux PROMPT HOLD (_hold_drain: a tmux-shaped delivery holds the sid for a moment, tested in
# tests/test_kernel_parked_ops_liveness.py) is a separate axis from the ACCOUNT gate this module covers:
# off here, so back-to-back _apply_pending_ops calls stand for successive cycles.
km._TMUX_PROMPT_HOLD_S = 0.0
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


class _FakeBackend:
    """An SDK-shaped backend: it forwards its own sends, so a drained run stays one send per message."""

    def __init__(self):
        self.calls = []

    def forwards_sends(self):
        return True

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True

    def set_effort(self, sid, value):
        self.calls.append(("effort", value))
        return True


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        self.be = _FakeBackend()
        self._saved = {n: getattr(km, n) for n in
                       ("_compacting_now", "_working_now", "_push_all", "_optimistic_echo",
                        "_mark_views_dirty", "_mark_compacting", "_mark_model_pending", "_path_of")}
        self._saved_backend = km.Sessions.backend_for
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: False
        km._push_all = lambda: None
        km._optimistic_echo = lambda sid, text, author="human": None
        km._mark_views_dirty = lambda *a, **k: None
        km._mark_compacting = lambda sid: None
        km._mark_model_pending = lambda sid, v: None
        km._path_of = lambda sid: None            # no transcript → the spend arm reads only the pause flag
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._pending_ops.clear()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._saved_backend
        km._pending_ops.clear()
        jd.STATE = self.saved_state
        self.td.cleanup()

    def _usage(self, five=10, seven=10, fable=None, five_reset=None, seven_reset=None):
        fut = int(time.time()) + 3600
        (jd.STATE / "usage.json").write_text(json.dumps({
            "t": int(time.time()),
            "five_hour": {"pct": five, "resets_at": fut if five_reset is None else five_reset},
            "seven_day": {"pct": seven, "resets_at": fut if seven_reset is None else seven_reset},
            "fable": None if fable is None else {"pct": fable, "resets_at": fut}}))


class LimitHold(_Base):
    """_limit_hold: what counts as 'the account can't serve this', and when it lets go."""

    def test_a_healthy_account_holds_nothing(self):
        self._usage(five=8, seven=13)
        self.assertIsNone(km._limit_hold(SID))

    def test_no_usage_report_holds_nothing(self):
        # never invent a hold from missing evidence — an unreadable report must not wedge every session
        self.assertIsNone(km._limit_hold(SID))

    def test_a_capped_window_holds_and_carries_its_reset(self):
        reset = int(time.time()) + 1800
        self._usage(five=100, seven=20, five_reset=reset)
        hold = km._limit_hold(SID)
        self.assertEqual(hold["reason"], "limit")
        self.assertEqual(hold["resetsAt"], reset, "the countdown rides the API's own stamp")

    def test_two_capped_windows_wait_for_the_later_reset(self):
        soon, late = int(time.time()) + 600, int(time.time()) + 7200
        self._usage(five=100, seven=100, five_reset=soon, seven_reset=late)
        self.assertEqual(km._limit_hold(SID)["resetsAt"], late,
                         "the queue can only move when the LAST capped window resets")

    def test_a_capped_window_with_no_stamp_promises_no_countdown(self):
        self._usage(five=100, seven=20, five_reset=0)
        hold = km._limit_hold(SID)
        # resets_at 0 is falsy in _lim's rolled-over check, so the window still reads limited; with no
        # readable stamp the hold says the reason and nothing about when.
        self.assertEqual(hold["reason"], "limit")
        self.assertIsNone(hold["resetsAt"])

    def test_the_fable_window_alone_is_not_an_account_hold(self):
        # fable is MODEL-scoped (the same carve-out _auto_pause_on_limit makes): a session on any other
        # model is still servable, so holding its input would be a false block.
        self._usage(five=10, seven=10, fable=100)
        self.assertIsNone(km._limit_hold(SID))

    def test_the_hold_releases_when_the_reset_stamp_passes(self):
        # THE release event: no timer, no poll — the window's own resetsAt going by is what lets go.
        past = int(time.time()) - 60
        self._usage(five=100, seven=20, five_reset=past)
        self.assertIsNone(km._limit_hold(SID),
                          "a window past its reset has rolled — the account can serve again")

    def test_a_spend_cap_holds_with_no_countdown(self):
        self._usage(five=10, seven=10)
        km._set_retry_paused(True, reason="spend")
        try:
            hold = km._limit_hold(SID)
            self.assertEqual(hold["reason"], "spend")
            self.assertIsNone(hold["resetsAt"], "a spend cap has no readable reset — promise no clock")
        finally:
            km._set_retry_paused(False)

    def test_a_plain_manual_retry_pause_is_not_a_spend_hold(self):
        self._usage(five=10, seven=10)
        km._set_retry_paused(True)
        try:
            self.assertIsNone(km._limit_hold(SID),
                              "pausing the retry storm is not the same as the account refusing work")
        finally:
            km._set_retry_paused(False)


class LimitParksEverything(_Base):
    def test_messages_and_slash_commands_park_in_press_order(self):
        self._usage(five=100, seven=20)
        km._send_or_park(self.be, SID, "first message", echo="human")
        km._set_model_or_park(self.be, SID, "opus")
        km._send_or_park(self.be, SID, "second message", echo="human")
        km._set_effort_or_park(self.be, SID, "high")
        self.assertEqual(self.be.calls, [], "nothing reaches the API while the account can't serve it")
        self.assertEqual(km._pending_ops.get(SID),
                         [("send", "first message", "human"), ("model", "opus"),
                          ("send", "second message", "human"), ("effort", "high")],
                         "ONE FIFO, in the order they were sent")

    def test_compact_parks_too(self):
        # the user's own repro: /compact while limited came back refused instead of waiting its turn
        self._usage(five=100, seven=20)
        self.assertTrue(km._ops_gate(SID), "the compact click parks on this gate")

    def test_an_sdk_backend_does_not_get_the_send_early(self):
        # a forwards_sends backend takes a send even mid-turn — so the limit arm has to sit AHEAD of that
        # check, or the message goes straight out and comes back an API error.
        self._usage(five=100, seven=20)
        km._working_now = lambda sid: True
        km._send_or_park(self.be, SID, "mid-turn message", echo="human")
        self.assertEqual(self.be.calls, [])
        self.assertEqual(len(km._pending_ops.get(SID) or []), 1)

    def test_nothing_parks_when_the_account_is_healthy(self):
        self._usage(five=8, seven=13)
        km._send_or_park(self.be, SID, "goes now", echo="human")
        self.assertEqual(self.be.calls, [("send", "goes now")])
        self.assertNotIn(SID, km._pending_ops)


class LimitDrain(_Base):
    def _park_a_sequence(self):
        km._send_or_park(self.be, SID, "one", echo="human")
        km._park_op(SID, ("compact",))
        km._send_or_park(self.be, SID, "two", echo="human")
        km._set_model_or_park(self.be, SID, "opus")

    def test_the_queue_holds_while_the_limit_holds(self):
        self._usage(five=100, seven=20)
        self._park_a_sequence()
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "a producer pass mid-limit delivers nothing")
        self.assertEqual(len(km._pending_ops.get(SID)), 4, "and drops nothing either")

    def test_it_drains_in_press_order_once_the_window_resets(self):
        self._usage(five=100, seven=20)
        self._park_a_sequence()
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [])
        # the reset lands: the window rolls over, and the next producer pass starts feeding the queue
        self._usage(five=100, seven=20, five_reset=int(time.time()) - 1)
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "one")],
                         "the leading send run goes first, and its turn must END before the next op")
        for _ in range(5):                # one pass per turn-ending op, as the real producer ticks
            km._apply_pending_ops()
        self.assertEqual(self.be.calls,
                         [("send", "one"), ("send", "/compact"), ("send", "two"), ("model", "opus")],
                         "everything sent during the limit lands after it, in the order it was typed")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_spend_cap_queue_drains_when_the_pause_lifts(self):
        self._usage(five=10, seven=10)
        km._set_retry_paused(True, reason="spend")
        try:
            km._send_or_park(self.be, SID, "held by the cap", echo="human")
            km._apply_pending_ops()
            self.assertEqual(self.be.calls, [])
            km._set_retry_paused(False)       # the user raised the cap / a session served a request
            km._apply_pending_ops()
            self.assertEqual(self.be.calls, [("send", "held by the cap")])
        finally:
            km._set_retry_paused(False)


class LimitThatBlocksTheLaunchItself(_Base):
    """The case usage.json structurally CANNOT see (the user 2026-07-28): usage.json is written from a
    RateLimitEvent the CLI streams once CONNECTED, so a limit that refuses the connect blocks its own
    reporting. With no other source the hold went blind on a fresh install — the message parked in the
    SDK queue with nothing to explain it and the session sat 'waiting'. The refused launch IS the event."""

    def _launch(self, rec):
        km._launch_error = lambda sid: rec

    def tearDown(self):
        km._launch_error = self._saved_launch
        super().tearDown()

    def setUp(self):
        super().setUp()
        self._saved_launch = km._launch_error

    def test_a_launch_the_limit_refused_holds_the_queue(self):
        self._launch({"text": "You've hit your session limit · resets 4:00pm", "at": 1, "limit": True})
        hold = km._limit_hold(SID)
        self.assertEqual(hold["reason"], "limit")
        self.assertIsNone(hold["resetsAt"], "the CLI reports a wall clock, not an epoch — promise no countdown")
        self.assertIn("session limit", hold["detail"], "the CLI's own words ride along one level deeper")

    def test_the_held_message_is_parked_not_sent(self):
        self._launch({"text": "You've hit your session limit · resets 4:00pm", "at": 1, "limit": True})
        km._send_or_park(self.be, SID, "pick the migration back up", echo="human")
        self.assertEqual(self.be.calls, [], "sending into an account that cannot serve just buys an error")
        self._launch(None)                     # the window reopened; the next connect cleared the record
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", "pick the migration back up")])

    def test_a_broken_install_is_not_a_hold(self):
        # a missing dependency is damage, not a wait — it gets the error card, and must not silently
        # park input forever behind a limit that does not exist
        self._launch({"text": "romp's Agent SDK backend isn't installed", "at": 1, "limit": False})
        self.assertIsNone(km._limit_hold(SID))


class HeldQueueIsVisible(unittest.TestCase):
    """A held queue must never be silent — the bubbles say what they're waiting for."""

    def test_the_queued_event_carries_the_hold(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn('events.append({"kind": "queued", "texts": qmsgs, **({"held": _hold} if _hold else {})})',
                      src,
                      "the hold rides the queued event — and the append literal stays intact for the "
                      "source-pinned ordering tests (compacting/retrying/reconnecting precede it)")


if __name__ == "__main__":
    unittest.main()
