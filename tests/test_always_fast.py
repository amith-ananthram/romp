#!/usr/bin/env python3
"""Always fast (the user 2026-09-17): a kernel-side switch (Settings, Chat, Model; STATE/always-fast, "on"/"off") that runs
every session in Claude Code's fast mode whenever its model can — the Opus family. The SDK backend reads the store by path
at connect and on a model change (fast_effective), arms only the fastMode flag-settings opt-in (never the literal '/fast
on', which on a non-Opus session makes the CLI switch model), respects a session the user put on Slow (the reg's fastOff)
and a refusal the CLI answered with a reason (liveFastReason), and asks for a reconnect when a model that can run fast
arrives on a connection made without the flag. Hermetic state, synthetic sids, no CLI."""
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-000000000701"


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    return be


def _switch(be, name, value):
    Path(be.state_dir, name).write_text(value)


def _sess(be, **reg):
    r = {"sid": SID, "name": "web", "cwd": "/tmp"}
    r.update(reg)
    sb.write_reg(be.state_dir, SID, dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    be.sessions[SID] = s
    return s


class TheStore(unittest.TestCase):
    def test_off_unless_the_file_says_on(self):
        d = tempfile.mkdtemp()
        self.assertFalse(sb.always_fast_on(d), "absent reads off: the opt-in must be provable")
        Path(d, sb.ALWAYS_FAST_STORE).write_text("off\n")
        self.assertFalse(sb.always_fast_on(d))
        Path(d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        self.assertTrue(sb.always_fast_on(d), "the kernel's bare store, stripped")
        self.assertFalse(sb.always_fast_on(None), "no state dir (a test double's backend) is off, never a raise")
        self.assertFalse(sb.retry_upgrade_on(d), "the sibling store is its own file")

    def test_fast_capable_is_the_opus_family_in_any_spelling(self):
        for m in ("opus", "Opus 5", "claude-opus-5", "us.anthropic.claude-opus-4-8[1m]"):
            self.assertTrue(sb.fast_capable(m), m)
        for m in ("fable", "Fable 5.1", "claude-fable-5-1", "sonnet", "", None, "default"):
            self.assertFalse(sb.fast_capable(m), repr(m))


class FastEffective(unittest.TestCase):
    def test_the_switch_arms_the_flag_for_an_opus_session_and_nothing_else(self):
        be = _backend()
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        self.assertFalse(s.fast_effective(), "switch off, no ask: no flag")
        _switch(be, sb.ALWAYS_FAST_STORE, "on")
        self.assertTrue(s.fast_effective(), "switch on, the CLI's id is Opus")
        s2 = _sess(be, liveModel="Opus 5")
        self.assertTrue(s2.fast_effective(), "the pretty name alone decides when no id is known")
        s3 = _sess(be, model="opus")
        self.assertTrue(s3.fast_effective(), "before any turn, the pick decides")
        s4 = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", model="fable")
        self.assertFalse(s4.fast_effective(), "Fable cannot run fast mode: the flag stays off (the CLI would report off anyway; the rule arms only where it takes)")
        s5 = _sess(be)
        self.assertFalse(s5.fast_effective(), "no model known at all: off, never a guess")

    def test_the_sessions_own_ask_a_slow_pick_and_a_refusal_each_have_their_say(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModelId="claude-opus-5", fastOff=True)
        self.assertFalse(s.fast_effective(), "the user put this session on Slow: the switch leaves it alone")
        s = _sess(be, liveModelId="claude-opus-5", liveFastReason="extra_usage_disabled")
        self.assertFalse(s.fast_effective(), "the CLI refused fast mode for this session with a reason: re-arming would loop")
        s = _sess(be, liveModelId="claude-fable-5-1", fast=True)
        self.assertTrue(s.fast_effective(), "the session's own ask (the badge's On) stands whatever the switch or the model, as before")
        s = _sess(be, liveModelId="claude-opus-5", fast=True, fastOff=False)
        _switch(be, sb.ALWAYS_FAST_STORE, "off")
        self.assertTrue(s.fast_effective(), "…and needs no switch")

    def test_the_connect_and_the_snapshot_read_the_one_expression(self):
        self.assertIn("fast=sess.fast_effective()", inspect.getsource(sb.SdkBackend._options))
        self.assertIn("self._fast_unlocked = self.fast_effective()", inspect.getsource(sb.SdkSession._amain))
        self.assertNotIn('self.send(sid, "/fast on")', inspect.getsource(sb.SdkSession.fast_effective) + inspect.getsource(sb.SdkSession._after_model_change),
                         "the rule never sends the literal toggle: on a non-Opus session the CLI answers it by switching model")


class SetFastRemembersSlow(unittest.TestCase):
    def test_off_writes_fastOff_and_on_clears_it(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModelId="claude-opus-5")
        s._fast_unlocked = True
        sent = []
        be.send = lambda sid, text: (sent.append(text), True)[1]
        be._wake_push = lambda: None
        self.assertTrue(be.set_fast(SID, "off"))
        self.assertEqual(sent, ["/fast off"], "the unlocked connection takes the literal send, as before")
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg["fast"], reg["fastOff"]), (False, True), "the reg records an explicit Slow")
        self.assertTrue(s.fast_off); self.assertFalse(s.fast_effective(), "…so the next connect carries no flag despite the switch")
        self.assertTrue(be.set_fast(SID, "on"))
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg["fast"], reg["fastOff"]), (True, False), "an explicit On lifts the Slow")
        self.assertFalse(s.fast_off); self.assertTrue(s.fast_effective())


class AModelThatCanRunFastArrives(unittest.TestCase):
    def _learn(self, be, s, pm, raw):
        asks = []
        s.request_reconnect = lambda *a, **k: asks.append(a)
        s._learn_model(pm, raw=raw)
        return asks

    def test_a_locked_connection_asks_for_a_reconnect_once_the_switch_wants_the_flag(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1")
        s._fast_unlocked = False
        self.assertEqual(len(self._learn(be, s, "Opus 5", "claude-opus-5")), 1, "Opus arrived on a connection made without the flag: reconnect (at the turn's end if busy)")
        self.assertTrue(any("always fast (web)" in m and "Opus 5" in m for m in be._logs), be._logs)

    def test_nothing_when_the_flag_is_already_there_the_switch_is_off_or_the_session_is_on_slow(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1"); s._fast_unlocked = True
        self.assertEqual(self._learn(be, s, "Opus 5", "claude-opus-5"), [], "the connection already carries the flag")
        be = _backend()
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1")
        self.assertEqual(self._learn(be, s, "Opus 5", "claude-opus-5"), [], "switch off")
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", fastOff=True)
        self.assertEqual(self._learn(be, s, "Opus 5", "claude-opus-5"), [], "the user put this session on Slow")
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        self.assertEqual(self._learn(be, s, "Fable 5.1", "claude-fable-5-1"), [], "a model that cannot run fast: nothing to arm")

    def test_the_context_refresh_runs_the_same_hook(self):
        self.assertIn("self._after_model_change(old, pm)", inspect.getsource(sb.SdkSession._do_refresh_context),
                      "a pick to Opus lands through the control channel's refresh first")
        self.assertIn("self._after_model_change(old, pm)", inspect.getsource(sb.SdkSession._learn_model))


class TheCliRefusesTheRule(unittest.TestCase):
    def test_a_reason_on_a_rule_armed_connection_is_said_once_and_stops_the_re_arm_with_no_reconnect(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModelId="claude-opus-5")
        s._fast_unlocked = True                     # the rule armed this connection's flag; no ask of the session's own
        asks = []
        s.request_reconnect = lambda *a, **k: asks.append(a)
        self.assertTrue(s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"}))
        said = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
        self.assertEqual(len(said), 1, be._logs)
        self.assertEqual(asks, [], "no reconnect: the ask's refusal path (a toast and a flagless reconnect) is for the user's own ask")
        self.assertEqual(s.fast_reason, "extra_usage_disabled")
        self.assertFalse(s.fast_effective(), "the persisted reason keeps the next connect flagless: no refuse → reconnect → refuse loop")
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"})
        self.assertEqual(len([m for m in be._logs if m.startswith("always fast (web): the CLI refused")]), 1, "the same reason again is not said again")
        self.assertIn('refused_ask = bool(reason) and self.fast_opt and fast != "on"', inspect.getsource(sb.SdkSession._adopt_fast_state),
                      "the user's own ask keeps its refusal path unchanged")


if __name__ == "__main__":
    unittest.main()
