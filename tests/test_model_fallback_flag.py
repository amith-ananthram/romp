#!/usr/bin/env python3
"""The model picker's REQUESTED-model mark (the user 2026-09-17): while a session's live model sits a tier below its pick
(an automatic fallback), the kernel sends `modelFallback` on the session's status — the pick, the live model, the cause
once the CLI's refusal frame named it, and the upgrade retry's state — and the picker draws a yellow tick beside the
requested model with a tooltip saying why and whether romp is retrying. This pins the row (model_fallback_row), where the
backend puts it (the live snapshot and the dormant row), how the cause is learned and persisted, and the kernel's three
projection sites. Hermetic state, synthetic sids, no CLI."""
import inspect
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))
SID = "11111111-2222-4333-8444-000000000801"


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    return be


def _sess(be, **reg):
    r = {"sid": SID, "name": "web", "cwd": "/tmp", "model": "claude-fable-5-1", "liveModel": "Fable 5.1", "liveModelId": "claude-fable-5-1"}
    r.update(reg)
    sb.write_reg(be.state_dir, r["sid"], dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    be.sessions[r["sid"]] = s
    return s


class TheRow(unittest.TestCase):
    def test_a_live_model_below_the_pick_is_a_fallback_row_with_the_retry_state(self):
        now = 1_000_000.0
        row = sb.model_fallback_row("claude-fable-5-1", "Opus 5", "safeguards", {"next": now + 250, "attempts": 1}, True, now=now)
        self.assertEqual((row["pick"], row["pickValue"], row["live"], row["cause"]), ("Fable 5.1", "claude-fable-5-1", "Opus 5", "safeguards"))
        self.assertEqual(row["retry"], {"on": True, "everyMin": 10, "armed": True, "nextIn": 250, "attempts": 1})
        row = sb.model_fallback_row("fable", "Opus 5", "", None, False, now=now)
        self.assertEqual((row["pick"], row["pickValue"], row["cause"]), ("Fable", "fable", ""), "an alias pick keeps its alias for the picker's match")
        self.assertEqual(row["retry"], {"on": False, "everyMin": 10, "armed": False, "nextIn": None, "attempts": 0})

    def test_no_row_without_a_fallback(self):
        for why, pick, live in (("pick answers", "claude-fable-5-1", "Fable 5.1"), ("the user's own upgrade", "opus", "Fable 5.1"),
                                ("no pick", "", "Opus 5"), ("the default", "default", "Opus 5"), ("no live name yet", "fable", ""),
                                ("an unknown family", "claude-zephyr-9", "Opus 5")):
            self.assertIsNone(sb.model_fallback_row(pick, live, "", None, True), why)


class WhereTheBackendPutsIt(unittest.TestCase):
    def test_the_live_snapshot_carries_it_and_reads_the_switch_and_the_arm(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        s._upgrade_retry = {"from": "Fable 5.1", "to": "Opus 5", "next": time.time() + 600, "attempts": 0}
        s._fallback_cause = "safeguards"
        fb = s.snapshot()["modelFallback"]
        self.assertEqual((fb["pick"], fb["live"], fb["cause"], fb["retry"]["on"], fb["retry"]["armed"]), ("Fable 5.1", "Opus 5", "safeguards", True, True))
        self.assertTrue(0 < fb["retry"]["nextIn"] <= 600)
        Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off"); s._upgrade_retry = None
        fb = s.snapshot()["modelFallback"]
        self.assertEqual((fb["retry"]["on"], fb["retry"]["armed"], fb["retry"]["nextIn"]), (False, False, None))
        s2 = _sess(be, sid="11111111-2222-4333-8444-000000000802", liveModel="Fable 5.1")
        self.assertIsNone(s2.snapshot()["modelFallback"], "the pick answers: no mark")

    def test_the_dormant_row_carries_it_from_the_registry(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        sb.write_reg(be.state_dir, SID, {"sid": SID, "name": "web", "cwd": "/tmp", "model": "claude-fable-5-1", "liveModel": "Opus 5",
                                         "fallbackCause": "safeguards", "alive": True})
        rows = be.live_sessions()
        self.assertIn(SID, rows, "the alive reg is listed as a dormant row")
        fb = rows[SID]["modelFallback"]
        self.assertEqual((fb["pick"], fb["live"], fb["cause"], fb["retry"]["on"], fb["retry"]["armed"], fb["retry"]["nextIn"]),
                         ("Fable 5.1", "Opus 5", "safeguards", True, False, None), "a dormant row keeps its mark and carries no arm")
        # the served model outranks the live label when the reg has it, and a pending pick shows no mark
        sb.write_reg(be.state_dir, SID, {"sid": SID, "name": "web", "cwd": "/tmp", "model": "claude-fable-5-1", "liveModel": "Fable 5.1",
                                         "servedModel": "Opus 5", "alive": True})
        self.assertEqual(be.live_sessions()[SID]["modelFallback"]["live"], "Opus 5", "the init's configured name is not what serves")
        sb.write_reg(be.state_dir, SID, {"sid": SID, "name": "web", "cwd": "/tmp", "model": "claude-fable-5-1", "liveModel": "Opus 5",
                                         "modelPending": True, "alive": True})
        self.assertIsNone(be.live_sessions()[SID]["modelFallback"], "a pick still resolving is the badge's dots, never a fallback mark")

    def test_the_switch_is_read_once_per_listing_and_a_caller_may_hand_it_down(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        self.assertTrue(s.snapshot()["modelFallback"]["retry"]["on"], "no argument: the switch is read")
        self.assertFalse(s.snapshot(retry_on=False)["modelFallback"]["retry"]["on"], "a listing hands its one read down")
        src = inspect.getsource(sb.SdkBackend.live_sessions)
        self.assertIn("retry_on = retry_upgrade_on(self.state_dir)   # once per listing", src)
        self.assertIn("self._live_row(reg, sid, retry_on)", src)

    def test_the_comment_popover_gets_it_through_session_meta(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        self.assertEqual(be.session_meta(SID)["modelFallback"]["pick"], "Fable 5.1", "a live thread: from the snapshot")
        sid2 = "11111111-2222-4333-8444-000000000803"
        sb.write_reg(be.state_dir, sid2, {"sid": sid2, "name": "web", "cwd": "/tmp", "model": "fable", "liveModel": "Opus 5", "alive": False})
        self.assertEqual(be.session_meta(sid2)["modelFallback"]["pick"], "Fable", "a dormant thread: from its reg")
        self.assertEqual(be.session_meta("11111111-2222-4333-8444-000000000899"), {}, "no reg: nothing")

class HowTheCauseIsLearned(unittest.TestCase):
    def test_a_downgrade_starts_an_episode_of_unknown_cause_and_the_refusal_frame_names_it(self):
        be = _backend()
        s = _sess(be)
        s._fallback_cause = "safeguards"   # a stale cause from an earlier episode
        s._learn_model("Opus 5", raw="claude-opus-5", served=True)
        self.assertEqual(s._fallback_cause, "", "a new episode: unknown until the CLI's end-of-turn frame")
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("fallbackCause"), "", "persisted for the dormant row")
        self.assertEqual(s.snapshot()["modelFallback"]["cause"], "")
        sb.SdkBackend.on_model_refusal_fallback = staticmethod(lambda *a, **k: None)
        try:
            s._on_refusal_fallback({"original_model": "claude-fable-5-1", "fallback_model": "claude-opus-5", "scope": "session"})
        finally:
            del sb.SdkBackend.on_model_refusal_fallback
        self.assertEqual(s._fallback_cause, "safeguards")
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("fallbackCause"), "safeguards")
        self.assertEqual(s.snapshot()["modelFallback"]["cause"], "safeguards")
        # a 'local' refusal (a subagent's reply) never swapped the session's model, so it names no cause
        s._fallback_cause = ""
        sb.SdkBackend.on_model_refusal_fallback = staticmethod(lambda *a, **k: None)
        try:
            s._on_refusal_fallback({"original_model": "claude-fable-5-1", "fallback_model": "claude-opus-5", "scope": "local"})
        finally:
            del sb.SdkBackend.on_model_refusal_fallback
        self.assertEqual(s._fallback_cause, "")

    def test_a_fresh_session_seeds_the_cause_from_its_registry(self):
        be = _backend()
        s = _sess(be, liveModel="Opus 5", fallbackCause="safeguards")
        self.assertEqual(s._fallback_cause, "safeguards")


class TheMarkFollowsTheServedModel(unittest.TestCase):
    def test_the_init_reporting_the_pick_does_not_lift_the_mark_but_a_served_reply_on_it_does(self):
        be = _backend()
        s = _sess(be)
        s._learn_model("Opus 5", raw="claude-opus-5", served=True)          # the API served the fallback
        self.assertEqual((s._served_model, s.snapshot()["modelFallback"]["live"]), ("Opus 5", "Opus 5"))
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("servedModel"), "Opus 5", "persisted for the dormant row")
        s._learn_model("Fable 5.1", raw="claude-fable-5-1")                  # a reconnect's init: the CONFIGURED pick, nothing served
        self.assertEqual(s.model, "Fable 5.1")
        self.assertIsNotNone(s.snapshot()["modelFallback"], "the mark stands: no reply has been served on the pick yet")
        s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)     # a parent reply served on the pick
        self.assertIsNone(s.snapshot()["modelFallback"], "back on the pick: the mark lifts")

    def test_a_pick_of_the_users_own_ends_the_episode(self):
        be = _backend()
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        s._fallback_cause = "safeguards"; s._served_model = "Opus 5"
        s.set_model_live = lambda *a, **k: None
        be.set_model(SID, "opus")
        self.assertEqual((s._fallback_cause, s._served_model), ("", ""))
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg.get("fallbackCause"), reg.get("servedModel")), ("", ""))

    def test_a_provisional_refusal_frame_before_the_final_hops_reply_keeps_its_cause(self):
        be = _backend()
        s = _sess(be)
        sb.SdkBackend.on_model_refusal_fallback = staticmethod(lambda *a, **k: None)
        try:
            s._on_refusal_fallback({"original_model": "claude-fable-5-1", "fallback_model": "claude-opus-5", "scope": "session", "provisional": True})
        finally:
            del sb.SdkBackend.on_model_refusal_fallback
        self.assertTrue(s._refusal_this_turn)
        s._learn_model("Opus 5", raw="claude-opus-5", served=True)          # the final hop's reply, learned after the frame
        self.assertEqual(s._fallback_cause, "safeguards", "the frame's cause is not erased by the reply's learn")
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("fallbackCause"), "safeguards")
        src = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn("self._refusal_this_turn = False        # the turn's refusal frame", src, "the settle clears the latch")


class TheRetryReArmsAtAnAttach(unittest.TestCase):
    def test_a_session_below_its_pick_arms_when_the_switch_is_on(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5", servedModel="Opus 5")
        self.assertIsNone(s._upgrade_retry)
        self.assertTrue(s._arm_if_below_pick())
        self.assertEqual((s._upgrade_retry["from"], s._upgrade_retry["to"]), ("Fable 5.1", "Opus 5"))
        self.assertTrue(s.snapshot()["modelFallback"]["retry"]["armed"], "the tooltip's cadence is now a promise the tick keeps")
        self.assertFalse(s._arm_if_below_pick(), "already armed")
        Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
        self.assertFalse(s._arm_if_below_pick()); self.assertIsNone(s._upgrade_retry, "off ends the arm")
        s2 = _sess(be, sid="11111111-2222-4333-8444-000000000804", liveModel="Fable 5.1", servedModel="Fable 5.1")
        Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        self.assertFalse(s2._arm_if_below_pick(), "on the pick: nothing to arm")

    def test_the_attach_and_the_switch_flip_both_use_it(self):
        src = inspect.getsource(sb.SdkSession._amain)
        self.assertIn('if getattr(self, "_host_is_attach", False):', src)
        self.assertIn('self._arm_if_below_pick()   # an attach replays no init', src)
        self.assertIn("s._arm_if_below_pick()", inspect.getsource(sb.SdkBackend.apply_model_switches))


class TheCauseNamesItsCategory(unittest.TestCase):
    def test_the_frame_records_the_category_and_the_row_carries_it(self):
        be = _backend()
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5", servedModel="Opus 5")
        sb.SdkBackend.on_model_refusal_fallback = staticmethod(lambda *a, **k: None)
        try:
            s._on_refusal_fallback({"original_model": "claude-fable-5-1", "fallback_model": "claude-opus-5", "scope": "session", "api_refusal_category": "cyber"})
        finally:
            del sb.SdkBackend.on_model_refusal_fallback
        fb = s.snapshot()["modelFallback"]
        self.assertEqual((fb["cause"], fb["category"]), ("safeguards", "cyber"))
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg.get("fallbackCause"), reg.get("fallbackCategory")), ("safeguards", "cyber"))
        self.assertEqual(be.live_sessions()[SID]["modelFallback"]["category"] if SID in be.live_sessions() else "cyber", "cyber")


class AStandingFallbackReadsItsCauseOffTheTranscript(unittest.TestCase):
    """A fallback that happened under an earlier kernel: this one saw no frame, so at the attach the cause is read from the
    CLI's transcript, from the end. The refusal record follows the fallback marker within seconds (measured 2026-09-17)."""
    def _transcript(self, lines):
        root = tempfile.mkdtemp()
        os.environ["CLAUDE_CONFIG_DIR"] = root
        path = sb.transcript_path("/tmp/notes-api", SID)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text("".join(json.dumps(l) + "\n" for l in lines))
        return path

    def _sess(self):
        be = _backend()
        s = _sess(be, cwd="/tmp/notes-api", liveModel="Opus 5", liveModelId="claude-opus-5", servedModel="Opus 5")
        return be, s

    def tearDown(self):
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def test_a_refusal_record_after_the_last_marker_seeds_the_cause_and_category(self):
        be, s = self._sess()
        path = self._transcript([
            {"type": "user", "message": {"role": "user", "content": "x"}},
            {"type": "assistant", "message": {"model": "claude-opus-5", "content": [{"type": "fallback", "from": {"model": "claude-fable-5-1"}, "to": {"model": "claude-opus-5"}}]}},
            {"type": "system", "subtype": "model_refusal_fallback", "trigger": "refusal", "scope": "session", "originalModel": "claude-fable-5-1", "fallbackModel": "claude-opus-5", "apiRefusalCategory": "bio"},
            {"type": "assistant", "message": {"model": "claude-opus-5", "content": [{"type": "text", "text": "y"}]}},
        ])
        self.assertTrue(s._seed_fallback_cause_from_transcript(path, "Opus 5"))
        self.assertEqual((s._fallback_cause, s._fallback_category), ("safeguards", "bio"))
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg.get("fallbackCause"), reg.get("fallbackCategory")), ("safeguards", "bio"))
        self.assertEqual(s.snapshot()["modelFallback"]["category"], "bio")
        self.assertTrue(any("safeguards refusal (bio)" in m for m in be._logs), be._logs)

    def test_no_seed_when_the_last_swap_was_not_a_refusal_or_was_local_or_another_tier(self):
        be, s = self._sess()
        rec = {"type": "system", "subtype": "model_refusal_fallback", "trigger": "refusal", "scope": "session", "originalModel": "claude-fable-5-1", "fallbackModel": "claude-opus-5", "apiRefusalCategory": "cyber"}
        marker = {"type": "assistant", "message": {"model": "claude-opus-5", "content": [{"type": "fallback", "from": {"model": "claude-fable-5-1"}, "to": {"model": "claude-opus-5"}}]}}
        self.assertFalse(s._seed_fallback_cause_from_transcript(self._transcript([rec, marker]), "Opus 5"), "a later marker with no refusal record after it: another kind of swap")
        self.assertFalse(s._seed_fallback_cause_from_transcript(self._transcript([marker, dict(rec, scope="local")]), "Opus 5"), "a local refusal swapped one reply, not the session")
        self.assertFalse(s._seed_fallback_cause_from_transcript(self._transcript([marker, dict(rec, fallbackModel="claude-sonnet-5")]), "Opus 5"), "a record about another tier")
        self.assertFalse(s._seed_fallback_cause_from_transcript(self._transcript([marker]), "Opus 5"), "no record at all")
        self.assertFalse(s._seed_fallback_cause_from_transcript("/nonexistent/transcript.jsonl", "Opus 5"), "no file: nothing, no raise")
        self.assertEqual(s._fallback_cause, "")

    def test_the_attach_asks_only_for_a_standing_fallback_with_no_cause(self):
        be, s = self._sess()
        s2 = _sess(be, sid="11111111-2222-4333-8444-000000000805", cwd="/tmp/notes-api", liveModel="Fable 5.1", servedModel="Fable 5.1")
        s._fallback_cause = "safeguards"
        started = []
        import threading as _th
        real = _th.Thread
        _th.Thread = lambda *a, **k: started.append(k.get("name")) or real(target=lambda: None)
        try:
            s._maybe_seed_fallback_cause()
            self.assertEqual(started, [], "a cause on record: nothing to read")
            s._fallback_cause = ""
            s._maybe_seed_fallback_cause()
            self.assertEqual(started, ["sdk-fbcause:web"], "no cause and a standing fallback: the transcript is read off the loop thread")
            s2._maybe_seed_fallback_cause()
            self.assertEqual(len(started), 1, "on the pick: no fallback stands, nothing to read")
        finally:
            _th.Thread = real
        src = inspect.getsource(sb.SdkSession._amain)
        self.assertIn("self._maybe_seed_fallback_cause()", src)


class TheKernelProjectsIt(unittest.TestCase):
    def test_the_three_sites(self):
        src = Path(os.path.join(BIN, "romp-kernel")).read_text()
        self.assertIn('"modelFallback": st.get("modelFallback"),', src, "live(): the merged map carries it")
        self.assertIn('"modelFallback": tm.get("modelFallback"),', src, "the chat status")
        self.assertIn('"modelFallback": (tm.get("modelFallback") if tm else None),', src, "the sessions status")
        self.assertIn('"modelFallback": (meta.get("modelFallback") if meta.get("modelFallback") is not None', src, "the comments frame's thread row")


if __name__ == "__main__":
    unittest.main()
