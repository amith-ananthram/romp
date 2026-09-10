#!/usr/bin/env python3
"""The global retry-pause is an API-HEALTH flag, not a permanent switch. The user flips "stop all
auto-retries" to calm the auto-retry + judge storm during an API / usage-limit outage — but the judge
tier is gated on `not _retry_paused_on()`, so a pause that never clears silently kills EVERY judge for
hours (the user 2026-06-30, who noted none of the judges were running and called it an API problem that should
clear the second a successful non-API-error response arrives on any session). _auto_resume_retry
clears it event-based, one rule per reason: a MANUAL pause lifts on the first live session that is NOT
blocked on an API error AND wrote fresh output since the pause began (mtime past the pause floor); a LIMIT
pause lifts when the usage report stops naming an account-wide window at 100% (the same reading that engaged
it); a SPEND pause lifts on fresh assistant output from a session on the billing the cap is on. The lift
leaves the capped session's own record standing (only a human prompt clears a spend record), so the un-pause
records its instant and the spend engage stands down on records older than it; a new record engages again.
"""
import contextlib
import inspect
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_rp", os.path.join(BIN, "romp-kernel"))


class RetryPauseAutoResume(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self._orig_state = km.jd.STATE
        km.jd.STATE = self.dir                          # retry-paused.json lives under jd.STATE
        self._orig_alive = km._alive_sessions
        self._orig_apierr = km._api_error
        self._orig_push = km._push_all
        km._push_all = lambda *a, **k: self.fail("a tick job built a push inline; it should wake the pusher")
        self._orig_rearm = km.jd.rearm_failed_summaries
        km.jd.rearm_failed_summaries = lambda now, **k: 0   # no given-up cards unless a test says so
        self._was_set = km._pusher_wake.is_set()
        km._pusher_wake.clear()

    def tearDown(self):
        km.jd.STATE = self._orig_state
        km._alive_sessions = self._orig_alive
        km._api_error = self._orig_apierr
        km._push_all = self._orig_push
        km.jd.rearm_failed_summaries = self._orig_rearm
        if self._was_set:
            km._pusher_wake.set()
        else:
            km._pusher_wake.clear()
        self.td.cleanup()

    def _transcript(self, name, mtime):
        p = self.dir / name
        p.write_text("{}\n")
        os.utime(p, (mtime, mtime))
        return str(p)

    # --- the flag records its pause instant ---
    def test_pause_records_a_floor_timestamp(self):
        km._set_retry_paused(True)
        self.assertTrue(km._retry_paused_on())
        self.assertGreater(km._retry_pause_ts(), 0, "a pause records WHEN it began (the auto-resume floor)")
        km._set_retry_paused(False)
        self.assertFalse(km._retry_paused_on())
        self.assertEqual(km._retry_pause_ts(), 0.0, "un-pausing drops the floor")

    # --- recovery clears it ---
    def test_fresh_healthy_session_clears_the_pause(self):
        km._set_retry_paused(True)
        floor = km._retry_pause_ts()
        path = self._transcript("healthy.jsonl", floor + 5)   # wrote output AFTER the pause
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": path}]
        km._api_error = lambda p: None                        # not blocked on an API error
        km._auto_resume_retry(int(time.time()), {})
        self.assertFalse(km._retry_paused_on(), "a served request after the pause proves recovery → resume")

    # --- an api-errored session is NOT proof of recovery ---
    def test_still_errored_session_keeps_the_pause(self):
        km._set_retry_paused(True)
        floor = km._retry_pause_ts()
        path = self._transcript("errored.jsonl", floor + 5)   # fresh mtime, but the last record is an API error
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": path}]
        km._api_error = lambda p: {"text": "overloaded", "status": 529}
        km._auto_resume_retry(int(time.time()), {})
        self.assertTrue(km._retry_paused_on(), "a session still blocked on an API error must not clear the pause")

    # --- a stale success (from before the outage) is NOT proof ---
    def test_stale_session_keeps_the_pause(self):
        km._set_retry_paused(True)
        floor = km._retry_pause_ts()
        path = self._transcript("stale.jsonl", floor - 60)    # last wrote BEFORE the pause
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": path}]
        km._api_error = lambda p: None
        km._auto_resume_retry(int(time.time()), {})
        self.assertTrue(km._retry_paused_on(), "no fresh output since the pause → no evidence the API recovered")

    # --- no-op when not paused ---
    def test_noop_when_not_paused(self):
        km._set_retry_paused(False)
        called = []
        km._alive_sessions = lambda now, tmux: called.append(1) or []
        km._auto_resume_retry(int(time.time()), {})
        self.assertEqual(called, [], "not paused → the resume check does no work")

    # --- delivery: the writer's wake and dirty mark, a second mark for the re-arm's write, no inline push ---
    def _recovered(self):
        km._set_retry_paused(True)
        km._pusher_wake.clear()                          # the pre-pause's own wake, not the clear's
        floor = km._retry_pause_ts()
        path = self._transcript("healthy.jsonl", floor + 5)
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": path}]
        km._api_error = lambda p: None

    def test_the_clear_wakes_the_pusher_and_marks_the_views_dirty(self):
        self._recovered()
        floor = km._views_dirty[0]
        km._auto_resume_retry(int(time.time()), {})
        self.assertFalse(km._retry_paused_on())
        self.assertTrue(km._pusher_wake.is_set(), "the clear wakes the pusher; globalRetryPaused rides its push")
        self.assertGreater(km._views_dirty[0], floor, "the writer's own mark: that cycle rebuilds past the sig")

    def test_a_re_arm_marks_the_views_dirty_after_its_write(self):
        # the store write on this path the cards show (a given-up card's summary sentinel goes back to
        # None) lands after the flag's own stamp, so the branch stamps again once the write is made
        self._recovered()
        seen = []

        def rearm(now, **k):
            seen.append(km._views_dirty[0])              # the clear's stamp, already placed
            return 2
        km.jd.rearm_failed_summaries = rearm
        km._auto_resume_retry(int(time.time()), {})
        self.assertEqual(len(seen), 1)
        self.assertGreater(km._views_dirty[0], seen[0], "a second mark, after the write")
        self.assertTrue(km._pusher_wake.is_set())

    def test_the_no_op_paths_neither_wake_nor_dirty(self):
        km._set_retry_paused(False)
        km._pusher_wake.clear()                          # the test's own write, not the tick's
        floor = km._views_dirty[0]
        km._auto_resume_retry(int(time.time()), {})
        self.assertFalse(km._pusher_wake.is_set(), "not paused: nothing to deliver")
        self.assertEqual(km._views_dirty[0], floor)
        km._set_retry_paused(True)
        km._pusher_wake.clear()
        pfloor = km._retry_pause_ts()
        path = self._transcript("stale.jsonl", pfloor - 60)
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "path": path}]
        km._api_error = lambda p: None
        floor = km._views_dirty[0]
        km._auto_resume_retry(int(time.time()), {})
        self.assertTrue(km._retry_paused_on())
        self.assertFalse(km._pusher_wake.is_set(), "still paused: nothing to deliver")
        self.assertEqual(km._views_dirty[0], floor)

    # --- a spend record older than the last spend lift is already ruled on ---
    def test_the_capped_session_lookup_skips_records_older_than_the_lift_and_keeps_the_rest(self):
        sess = [{"sid": "s%d" % i, "path": str(self.dir / ("s%d.jsonl" % i))} for i in range(4)]
        errs = {sess[0]["path"]: {"spendLimit": True, "t": 1000},        # a second before the lift's evidence: stale
                sess[1]["path"]: {"spendLimit": False, "t": 1005},       # not a cap
                sess[2]["path"]: {"spendLimit": True, "t": 0},           # no readable time: not proof of staleness
                sess[3]["path"]: {"spendLimit": True, "t": 1001}}        # the evidence's own second: new
        km._alive_sessions = lambda now, tmux: sess
        km._api_error = lambda p: errs.get(p)
        self.assertEqual(km._spend_capped_session(0, {})["sid"], "s0", "no lift on record: the first capped session")
        # `after` is the lifting output's own stamp, whole seconds like the record's
        self.assertEqual(km._spend_capped_session(0, {}, after=1001)["sid"], "s2", "1000 < 1001 is stale, 0 is unknown")
        del errs[sess[2]["path"]]
        self.assertEqual(km._spend_capped_session(0, {}, after=1001)["sid"], "s3", "a record in the evidence's second counts")
        self.assertIsNone(km._spend_capped_session(0, {}, after=1001.7), "no truncation: 1001 is older than a Resume at 1001.7")
        del errs[sess[3]["path"]]
        self.assertIsNone(km._spend_capped_session(0, {}, after=1001), "every remaining record predates the lift")
        self.assertEqual(km._retry_pause_lifted_at(), 0.0, "no file: nothing on record")

    # --- the bottom bar's API health cell reads the pause and its lift off this same file ---
    def test_the_api_health_frame_reads_paused_limit_then_ok_after_the_clear(self):
        km._set_retry_paused(True, reason="limit")
        floor = km._retry_pause_ts()
        path = str(self.dir / "healthy.jsonl")
        with open(path, "w") as f:
            f.write(_out_line(floor + 5))                      # a login-billed session's answer after the pause
        live = {"s1": {"state": "idle", "auth": "login", "backend": "sdk"}}
        km._alive_sessions = lambda now, tmux: [{"sid": "s1", "name": "web", "path": path}]
        km._api_error = lambda p: None
        saved_backend, saved_usage = km.Sessions.__dict__["backend_for"], km._usage
        km.Sessions.backend_for = staticmethod(lambda sid: object())
        km._usage = lambda: {"limited": {"fiveHour": True}}
        km._api_last_failed_cache.clear()
        try:
            f = km._api_health_frame(int(time.time()), live)
            self.assertEqual((f["state"], f["reason"], f["text"]), ("paused", "limit", "paused · usage limit"))
            self.assertEqual(f["since"], int(floor), "the pause's own t, not the clock")
            km._auto_resume_retry(int(time.time()), live)
            self.assertTrue(km._retry_paused_on(), "the report still names the window: the output lifts nothing")
            km._usage = lambda: {"limited": {}}
            km._auto_resume_retry(int(time.time()), live)
            self.assertFalse(km._retry_paused_on())
            f = km._api_health_frame(int(time.time()), live)
            self.assertEqual((f["state"], f["reason"], f["text"], f["since"]), ("ok", "", "ok", 0))
        finally:
            km.Sessions.backend_for = saved_backend
            km._usage = saved_usage
            km._api_last_failed_cache.clear()


# ── the lift rules, per reason, over real synthetic transcripts ──────────────────────────────────────
def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _out_line(t):
    return json.dumps({"type": "assistant", "uuid": "aaaaaaaa-0000-0000-0000-%012d" % int(t), "timestamp": _iso(t),
                       "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}) + "\n"


def _prompt_line(t, text="please continue"):
    return json.dumps({"type": "user", "uuid": "bbbbbbbb-0000-0000-0000-%012d" % int(t), "timestamp": _iso(t),
                       "message": {"role": "user", "content": [{"type": "text", "text": text}]}}) + "\n"


SID_KEY = "88888888-aaaa-4bbb-8ccc-000000000001"     # a private synthetic family for this module
SID_LOGIN = "88888888-aaaa-4bbb-8ccc-000000000002"


def _err_line(t, text, status=400, category="billing_error"):
    return json.dumps({"type": "assistant", "uuid": "cccccccc-0000-0000-0000-%012d" % int(t), "timestamp": _iso(t),
                       "isApiErrorMessage": True, "apiErrorStatus": status, "error": category,
                       "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}) + "\n"


SPEND_TEXT = "API Error: 400 You have reached your monthly spend limit for this workspace."   # invented wording


class _KernelClock:
    """The kernel module's `time`, with time() reading no earlier than a floor the test sets: the pause floor
    (and a Resume's liftedAt) are wall-clock writes while the transcripts here carry synthetic times ahead of
    the clock, so a test that plays a second round (a new record, a second lift) moves the kernel's clock along
    with its timeline. A floor at a round second makes every read exact. Everything else delegates to the real
    module. At 0 (the default) the clock is the real one."""

    def __init__(self):
        self.floor = 0.0

    def time(self):
        return max(time.time(), self.floor)

    def __getattr__(self, name):
        return getattr(time, name)


class _PauseFixture(unittest.TestCase):
    """The pusher's pause jobs over real synthetic transcripts: _alive_sessions and the live map are the
    module's own, _usage is the patched report, every frame goes to self.sent."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self._orig = (km.jd.STATE, km._alive_sessions, km._push_all, km.jd.rearm_failed_summaries, km._usage,
                      km._send_to_app, km.Sessions.__dict__["backend_for"], km._auth_key_present, km.time)
        self.clock = _KernelClock()
        km.time = self.clock
        km.jd.STATE = self.dir
        km._push_all = lambda *a, **k: self.fail("a tick job built a push inline; it should wake the pusher")
        km.jd.rearm_failed_summaries = lambda now, **k: 0
        km._usage = lambda: {"limited": {"fiveHour": True}}    # the login account's window is at 100%
        self.sent = []
        km._send_to_app = lambda app, m: self.sent.append((app, m))
        km.Sessions.backend_for = staticmethod(lambda sid: object())
        km._auth_key_present = lambda: True                    # an apiKeyHelper is configured on this machine
        km._APIH_LAST[0] = None
        km._api_err_cache.clear()
        km._api_last_failed_cache.clear()
        self._was_set = km._pusher_wake.is_set()
        km._pusher_wake.clear()
        self.roster = []
        km._alive_sessions = lambda now, tmux: list(self.roster)

    def tearDown(self):
        (km.jd.STATE, km._alive_sessions, km._push_all, km.jd.rearm_failed_summaries, km._usage,
         km._send_to_app, km.Sessions.backend_for, km._auth_key_present, km.time) = self._orig
        km._APIH_LAST[0] = None
        km._api_err_cache.clear()
        km._api_last_failed_cache.clear()
        if self._was_set:
            km._pusher_wake.set()
        else:
            km._pusher_wake.clear()
        self.td.cleanup()

    def _session(self, sid, name, auth, *lines):
        """One alive session with a transcript of `lines`; returns (path, its live-map row)."""
        p = self.dir / (name + ".jsonl")
        p.write_text("".join(lines))
        self.roster = [s for s in self.roster if s["sid"] != sid] + [{"sid": sid, "name": name, "path": str(p)}]
        return str(p), {"state": "idle", "auth": auth, "authLive": auth, "backend": "sdk"}

    def _append(self, path, line, t):
        with open(path, "a") as f:
            f.write(line)
        os.utime(path, (t, t))

    def _cycle(self, now, live):
        # the pusher's jobs in their order: the limit engages, the spend cap engages, the resume check, the frame
        km._auto_pause_on_limit()
        km._auto_pause_on_spend_limit(now, live)
        km._auto_resume_retry(now, live)
        f = km._api_health_frame(now, live)
        km._api_health_push(f)
        return f["state"]

    def _states(self):
        return [m["state"] for a, m in self.sent]


class LimitPauseLift(_PauseFixture):
    """A usage-limit pause lifts on the same reading that engaged it: the usage report (_account_limited).
    The mtime rule lifted it on ANY alive session's transcript mtime past the floor (a key-billed session's
    streaming or a human prompt), and _auto_pause_on_limit re-engaged the next cycle while the window held:
    the pause file, and the bottom bar's API cell, flipped every cycle. A rule keyed on a login-billed
    session's fresh assistant output could never fire after the reset (the login sessions the limit blocked
    are the ones the pause gates), so the pause would hold until a human acted. One authority for both edges:
    no session's output lifts it while the report reads limited."""

    def test_a_key_billed_session_s_output_during_a_limit_leaves_the_pause_and_the_frame_stable(self):
        # a limited window, one key-billed session appending an answer every other cycle
        now = time.time()
        path, row = self._session(SID_KEY, "web", "key", _out_line(now - 60))
        live = {SID_KEY: row}
        states = []
        for i in range(6):
            if i % 2:
                self._append(path, _out_line(now + i), now + i)
            states.append(self._cycle(int(now) + i, live))
        self.assertEqual(states, ["paused"] * 6, "a key-billed session's output says nothing about the login limit")
        self.assertEqual(self._states(), ["paused"], "one frame: nothing about the API changed")
        self.assertEqual(km._retry_pause_reason(), "limit")

    def test_the_window_reset_lifts_it_once_with_no_login_output_while_a_key_session_streams(self):
        # after the window rolls, nothing but a human could lift an output-keyed rule: a login session blocked on
        # the limit cannot serve (its auto-retry is gated on this very pause), the key session is rightly skipped;
        # the report clearing by its own clock must be the lift
        now = time.time()
        api_path, api_row = self._session(SID_LOGIN, "api", "login",
                                          _out_line(now - 120), _err_line(now - 90, "API Error: 429 rate limited", 429, "rate_limit"))
        web_path, web_row = self._session(SID_KEY, "web", "key", _out_line(now - 60))
        live = {SID_LOGIN: api_row, SID_KEY: web_row}
        states = []
        for i in range(3):
            self._append(web_path, _out_line(now + i), now + i)
            states.append(self._cycle(int(now) + i, live))
        self.assertEqual(states, ["paused"] * 3)
        km._usage = lambda: {"limited": None}                  # the reset passed: the report no longer names a window
        for i in range(3, 8):
            self._append(web_path, _out_line(now + i), now + i)
            states.append(self._cycle(int(now) + i, live))
        self.assertEqual(states, ["paused"] * 3 + ["degraded"] * 5, "lifts the cycle the report clears; stays lifted")
        self.assertFalse(km._retry_paused_on())
        self.assertEqual(self._states(), ["paused", "degraded"], "each side of the lift sent once")
        self.assertEqual(self.sent[-1][1]["waiting"], 1, "the login session still sits on its record: the auto-retry's turn now")
        self.assertTrue(km._pusher_wake.is_set(), "the clear wakes the pusher; globalRetryPaused rides its push")

    def test_login_output_under_a_still_limited_report_holds_the_pause_and_the_frame(self):
        # with extra usage on, the login account is served at 100%: a lift on each output record would be
        # re-engaged the next cycle, the pause file flipping at the output cadence. One authority: the report.
        # A login turn's end refreshes it, and that is the edge that lifts.
        now = time.time()
        path, row = self._session(SID_LOGIN, "api", "login", _out_line(now - 60))
        live = {SID_LOGIN: row}
        states = []
        for i in range(8):
            self._append(path, _out_line(now + i), now + i)
            states.append(self._cycle(int(now) + i, live))
        self.assertEqual(states, ["paused"] * 8, "served output under a limited report is not the lift")
        self.assertEqual(self._states(), ["paused"], "one frame across eight output records")
        km._usage = lambda: {"limited": {}}                    # the refreshed report reads under 100%
        self.assertEqual(self._cycle(int(now) + 8, live), "ok")
        self.assertFalse(km._retry_paused_on())
        self.assertEqual(self._states(), ["paused", "ok"])

    def test_a_login_billed_session_s_fresh_assistant_output_lifts_it_once(self):
        # the login account serves a request and its usage report catches up (a login turn's end refreshes it);
        # the lift lands once, on the report
        now = time.time()
        path, row = self._session(SID_LOGIN, "api", "login", _out_line(now - 60))
        live = {SID_LOGIN: row}
        self.assertEqual(self._cycle(int(now), live), "paused")
        floor = km._retry_pause_ts()
        os.utime(path, (floor + 1, floor + 1))                 # a fresh mtime over OLD output
        km._auto_resume_retry(int(now), live)
        self.assertTrue(km._retry_paused_on(), "the report still reads limited: held")
        self._append(path, _prompt_line(floor + 2), floor + 2)
        km._auto_resume_retry(int(now), live)
        self.assertTrue(km._retry_paused_on(), "a prompt is not the API's answer, and the report still reads limited")
        self._append(path, _out_line(floor + 5), floor + 5)    # the login account served a request
        km._usage = lambda: {"limited": {}}                    # and its usage report caught up
        self.assertEqual(self._cycle(int(now) + 1, live), "ok")
        self.assertFalse(km._retry_paused_on())
        self.assertEqual(self._states(), ["paused", "ok"], "each side of the lift sent once")

    def test_an_unreadable_report_changes_nothing(self):
        now = time.time()
        path, row = self._session(SID_LOGIN, "api", "login", _out_line(now - 60))
        live = {SID_LOGIN: row}
        self.assertEqual(self._cycle(int(now), live), "paused")

        def boom():
            raise RuntimeError("no report")
        km._usage = boom
        km._pusher_wake.clear()                                # the engage above woke the pusher; the check below must not
        km._auto_resume_retry(int(now), live)
        self.assertTrue(km._retry_paused_on(), "no reading: no change, as the engage side would not engage")
        self.assertFalse(km._pusher_wake.is_set())

    def test_the_engage_and_the_lift_read_the_same_filter(self):
        km._usage = lambda: {"limited": {"fable": True}}
        self.assertEqual(km._account_limited(), [], "fable is model-scoped: never an account limit")
        km._usage = lambda: {"limited": {"fiveHour": False, "sevenDay": True, "fable": True}}
        self.assertEqual(km._account_limited(), ["sevenDay"])
        km._usage = lambda: None
        self.assertEqual(km._account_limited(), [], "no report at all (a pure API-key host) reads as not limited")
        src = inspect.getsource(km._auto_pause_on_limit) + inspect.getsource(km._auto_resume_retry)
        self.assertEqual(src.count("_account_limited()"), 2, "both edges call the one filter")

    def test_a_manual_pause_keeps_lifting_on_any_fresh_transcript(self):
        km._usage = lambda: {"limited": {}}
        now = time.time()
        path, row = self._session(SID_KEY, "web", "key", _out_line(now - 60))
        km._set_retry_paused(True)
        floor = km._retry_pause_ts()
        os.utime(path, (floor + 1, floor + 1))
        km._auto_resume_retry(int(now), {SID_KEY: row})
        self.assertFalse(km._retry_paused_on(), "the user's own stop: the mtime rule is unchanged")

    def test_a_session_of_unknown_auth_bills_the_login_only_when_this_machine_holds_no_key(self):
        km._auth_key_present = lambda: True
        self.assertFalse(km._bills_login({"state": "idle"}), "a key on the machine: this session may be billing it")
        self.assertFalse(km._bills_login(None))
        km._auth_key_present = lambda: False
        self.assertTrue(km._bills_login({"state": "idle"}), "no key anywhere: the login is the only account it can bill")
        self.assertTrue(km._bills_login({"auth": "key", "authLive": "login"}), "the CLI's own live report wins")
        self.assertFalse(km._bills_login({"auth": "key"}))


class _SpendWorld(_PauseFixture):
    """The spend classes' world: no usage window; 'web' bills the key and sits on its cap record, 'api' bills the
    login and streams. A base with no tests of its own, so each class below is collected once."""

    def setUp(self):
        super().setUp()
        km._usage = lambda: {"limited": None}                  # no usage window is involved
        self.now = time.time()
        self.web, web_row = self._session(SID_KEY, "web", "key", _out_line(self.now - 120), _err_line(self.now - 90, SPEND_TEXT))
        self.api, api_row = self._session(SID_LOGIN, "api", "login", _out_line(self.now - 60))
        self.live = {SID_KEY: web_row, SID_LOGIN: api_row}


class SpendPauseLift(_SpendWorld):
    """A spend pause lifts on fresh assistant output from a session on the billing the cap is on (recorded
    at the engage: _retry_pause_bills), never from the other billing. The mtime rule it replaces counted a
    session on the OTHER account streaming past the cap; _auto_pause_on_spend_limit re-engaged the next
    cycle while the capped session sat on its record, and the pause file, so the cell, flipped every cycle.
    The capped session itself qualifies once it serves again."""

    def test_the_other_billing_s_output_leaves_the_pause_and_the_frame_stable(self):
        states = []
        for i in range(8):
            self._append(self.api, _out_line(self.now + i), self.now + i)
            states.append(self._cycle(int(self.now) + i, self.live))
        self.assertEqual(states, ["paused"] * 8, "a login session's output says nothing about the key's cap")
        self.assertEqual([(m["state"], m["text"]) for a, m in self.sent],
                         [("paused", "paused · spend cap · 1 waiting")], "one frame across eight cycles")
        self.assertEqual((km._retry_pause_reason(), km._retry_pause_bills()), ("spend", "key"))

    def test_the_capped_session_s_own_output_lifts_it_once(self):
        self.assertEqual(self._cycle(int(self.now), self.live), "paused")
        floor = km._retry_pause_ts()
        self._append(self.api, _out_line(floor + 1), floor + 1)   # the other billing, still
        self.assertEqual(self._cycle(int(self.now) + 1, self.live), "paused")
        self._append(self.web, _prompt_line(floor + 2, "retry"), floor + 2)   # romp's own retry prompt: not the answer
        self.assertEqual(self._cycle(int(self.now) + 2, self.live), "paused")
        self._append(self.web, _out_line(floor + 5), floor + 5)   # the cap was raised: the capped session served
        self.assertEqual(self._cycle(int(self.now) + 3, self.live), "ok")
        self.assertFalse(km._retry_paused_on())
        self.assertEqual(self._states(), ["paused", "ok"], "each side of the lift sent once")

    def test_a_second_session_on_the_capped_billing_lifts_it(self):
        tests, tests_row = self._session("88888888-aaaa-4bbb-8ccc-000000000003", "tests", "key", _out_line(self.now - 60))
        self.live["88888888-aaaa-4bbb-8ccc-000000000003"] = tests_row
        self.assertEqual(self._cycle(int(self.now), self.live), "paused")
        floor = km._retry_pause_ts()
        self._append(tests, _out_line(floor + 3), floor + 3)   # the key account serves again, through another session
        km._auto_resume_retry(int(self.now) + 1, self.live)
        self.assertFalse(km._retry_paused_on(), "same billing as the cap: proof the account serves")

    def test_a_cap_on_the_login_holds_against_key_output_and_lifts_on_login_output(self):
        # roles swapped: the login session sits on the cap, the key session streams
        web, web_row = self._session(SID_KEY, "web", "key", _out_line(self.now - 60))
        api, api_row = self._session(SID_LOGIN, "api", "login", _out_line(self.now - 120), _err_line(self.now - 90, SPEND_TEXT))
        live = {SID_KEY: web_row, SID_LOGIN: api_row}
        states = []
        for i in range(4):
            self._append(web, _out_line(self.now + i), self.now + i)
            states.append(self._cycle(int(self.now) + i, live))
        self.assertEqual(states, ["paused"] * 4)
        self.assertEqual(km._retry_pause_bills(), "login")
        floor = km._retry_pause_ts()
        self._append(api, _out_line(floor + 9), floor + 9)
        self.assertEqual(self._cycle(int(self.now) + 4, live), "ok")

    def test_a_pause_file_with_no_billing_takes_any_session_s_fresh_output_but_not_a_bare_mtime(self):
        km._set_retry_paused(True, reason="spend")             # no billing recorded (a file another kernel wrote)
        floor = km._retry_pause_ts()
        os.utime(self.api, (floor + 1, floor + 1))
        km._auto_resume_retry(int(self.now), self.live)
        self.assertTrue(km._retry_paused_on(), "a fresh mtime over old output is not the API's answer")
        self._append(self.api, _out_line(floor + 2), floor + 2)
        km._auto_resume_retry(int(self.now), self.live)
        self.assertFalse(km._retry_paused_on(), "with no billing on file, any session's fresh output lifts")

    def test_the_pause_file_records_the_capped_billing_and_a_manual_pause_records_none(self):
        self._cycle(int(self.now), self.live)
        d = json.loads((self.dir / "retry-paused.json").read_text())
        self.assertEqual((d["paused"], d["reason"], d["bills"]), (True, "spend", "key"))
        self.assertIn("t", d)
        km._set_retry_paused(True)
        d = json.loads((self.dir / "retry-paused.json").read_text())
        self.assertEqual(set(d), {"paused", "t"})
        self.assertEqual(km._retry_pause_bills(), "")


class SpendPauseStandDown(_SpendWorld):
    """The lift leaves the capped session's own record standing: a spend cap is on-you, so romp sends it no
    retry and only a human prompt to that session clears _api_error. An engage that read that record again
    would put the pause back the cycle after every lift, so it alternated at the streaming session's output
    cadence and settled ON once it idled, gating the judges and the idle-queue drives until someone prompted
    the capped session. The un-pause of a spend pause records liftedAt and the floor it superseded, the memory
    rides every later write, and the engage skips records older than it."""

    SID_TESTS = "88888888-aaaa-4bbb-8ccc-000000000003"
    SID_DOCS = "88888888-aaaa-4bbb-8ccc-000000000004"

    def _tests_session(self):
        tests, row = self._session(self.SID_TESTS, "tests", "key", _out_line(self.now - 60))
        self.live[self.SID_TESTS] = row
        return tests

    def _file(self):
        return json.loads((self.dir / "retry-paused.json").read_text())

    def test_one_lift_no_re_engage_and_the_pause_off_once_the_streamer_idles(self):
        # 'web' (key) sits on its cap record, 'tests' (key) streams one answer per cycle for six cycles after the
        # lift, then idles for three. Without the stand-down the states alternated paused/degraded at the output
        # cadence (four engages, three lifts) and were paused at the end.
        tests = self._tests_session()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            states = [self._cycle(int(self.now), self.live)]
            floor = km._retry_pause_ts()
            for i in range(1, 7):
                self._append(tests, _out_line(floor + i), floor + i)
                states.append(self._cycle(int(self.now) + i, self.live))
            for i in range(7, 10):
                states.append(self._cycle(int(self.now) + i, self.live))
        self.assertEqual(states, ["paused"] + ["degraded"] * 9, "one lift, and the stale record engages nothing")
        self.assertFalse(km._retry_paused_on(), "OFF after the streamer idles")
        self.assertEqual(self._states(), ["paused", "degraded"], "two frames: the engage and the lift")
        self.assertEqual((err.getvalue().count("auto-engaged"), err.getvalue().count("auto-cleared")), (1, 1))
        self.assertTrue(km._api_error(self.web), "the capped session's record still stands: only a human prompt clears it")
        self.assertEqual(self.sent[-1][1]["waiting"], 1, "and the cell still counts it")

    def test_a_new_spend_record_after_the_lift_engages_once(self):
        tests = self._tests_session()
        self.assertEqual(self._cycle(int(self.now), self.live), "paused")
        floor = km._retry_pause_ts()
        self._append(tests, _out_line(floor + 1), floor + 1)
        self.assertEqual(self._cycle(int(self.now) + 1, self.live), "degraded")
        self.assertEqual(self._cycle(int(self.now) + 2, self.live), "degraded")
        # the user prompts 'web' and it fails on the cap again: a record written after the lift is new information
        self._append(self.web, _prompt_line(floor + 3, "try once more"), floor + 3)
        self.assertEqual(self._cycle(int(self.now) + 3, self.live), "degraded", "a prompt is not a new record")
        self._append(self.web, _err_line(floor + 5, SPEND_TEXT), floor + 5)
        self.clock.floor = floor + 5.5                         # the kernel's clock has reached the new record
        states = [self._cycle(int(self.now) + 4 + i, self.live) for i in range(3)]
        self.assertEqual(states, ["paused"] * 3, "engages once on the new record and holds")
        self.assertEqual(self._states(), ["paused", "degraded", "paused"])
        self.assertEqual((km._retry_pause_reason(), km._retry_pause_bills()), ("spend", "key"))
        self.assertGreater(km._retry_pause_ts(), floor, "a new floor")
        # a second round: the key account serves again, the new record is the stale one now
        self._append(tests, _out_line(floor + 7), floor + 7)
        self.clock.floor = floor + 7.5
        states = [self._cycle(int(self.now) + 7 + i, self.live) for i in range(3)]
        self.assertEqual(states, ["degraded"] * 3, "one lift again, and no re-engage on the second record either")
        self.assertEqual(self._states(), ["paused", "degraded", "paused", "degraded"])

    def test_a_second_session_s_record_from_the_second_before_the_lift_cycle_engages(self):
        # liftedAt is the lifting OUTPUT's own time, not the lift cycle's clock. The timeline: 'web' (key) sits on
        # its cap; 'tests' (key) serves at T+0.1 (the evidence); 'docs' (key) hits the cap at T+0.6; the pusher's
        # lift cycle runs at T+1.2. The CLI stamps both records T, and a liftedAt of T+1.2 read in whole seconds
        # (T+1) would skip docs' record, written after the evidence, until that session's next attempt.
        tests = self._tests_session()
        docs, docs_row = self._session(self.SID_DOCS, "docs", "key", _out_line(self.now - 60))
        self.live[self.SID_DOCS] = docs_row
        T = int(self.now) + 100                                # a round second ahead of the real clock: exact reads
        self.clock.floor = T - 10
        self.assertEqual(self._cycle(T - 10, self.live), "paused")
        self.assertEqual(km._retry_pause_ts(), T - 10)
        self._append(tests, _out_line(T + 0.1), T + 0.1)       # the key account serves again, stamped T
        self._append(docs, _err_line(T + 0.6, SPEND_TEXT), T + 0.6)   # a second session hits the cap, stamped T
        self.clock.floor = T + 1.2                             # the lift cycle runs in the next second
        self.assertEqual(self._cycle(T + 1, self.live), "degraded", "the lift: the engage ran first, over a paused file")
        self.assertEqual(self._file()["liftedAt"], T, "the evidence's own time, not the cycle's")
        self.assertEqual(self.sent[-1][1]["waiting"], 2, "both records stand")
        self.assertEqual(self._cycle(T + 1, self.live), "paused", "docs' record is not older than the evidence: new information")
        self.assertEqual((km._retry_pause_reason(), km._retry_pause_bills(), km._retry_pause_ts()), ("spend", "key", T + 1.2))
        self.assertEqual(self._cycle(T + 2, self.live), "paused", "and holds: tests' output predates the new floor")
        self.assertEqual(self._states(), ["paused", "degraded", "paused"])

    def test_the_memory_survives_a_limit_pause_that_engages_and_lifts_in_between(self):
        # a single-slot memory dropped at the next write would let the limit lift hand the stale record back
        tests = self._tests_session()
        self.assertEqual(self._cycle(int(self.now), self.live), "paused")
        floor = km._retry_pause_ts()
        self._append(tests, _out_line(floor + 1), floor + 1)
        self.assertEqual(self._cycle(int(self.now) + 1, self.live), "degraded")            # the spend lift
        lifted = self._file()["liftedAt"]
        km._usage = lambda: {"limited": {"fiveHour": True}}
        self.assertEqual(self._cycle(int(self.now) + 2, self.live), "paused")              # the login window
        self.assertEqual((km._retry_pause_reason(), self._file()["liftedAt"]), ("limit", lifted))
        km._usage = lambda: {"limited": None}
        states = [self._cycle(int(self.now) + 3 + i, self.live) for i in range(3)]
        self.assertEqual(states, ["degraded"] * 3, "the limit lifts; the stale spend record engages nothing")
        self.assertEqual(self._states(), ["paused", "degraded", "paused", "degraded"])
        self.assertEqual(self._file()["liftedAt"], lifted)

    def test_a_limit_lift_rules_on_no_spend_record(self):
        # the login window is at 100% first; 'web' hits its cap during that pause; the report clears. The limit
        # lift's evidence is the report, which says nothing about the cap, so the cap engages its own pause.
        km._usage = lambda: {"limited": {"fiveHour": True}}
        web, web_row = self._session(SID_KEY, "web", "key", _out_line(self.now - 120))    # not capped yet
        self.assertEqual(self._cycle(int(self.now), self.live), "paused")
        self.assertEqual(km._retry_pause_reason(), "limit")
        floor = km._retry_pause_ts()
        self._append(web, _err_line(floor + 1, SPEND_TEXT), floor + 1)
        self.assertEqual(self._cycle(int(self.now) + 1, self.live), "paused")
        km._usage = lambda: {"limited": None}
        self.assertEqual(self._cycle(int(self.now) + 2, self.live), "degraded", "the limit lifts")
        self.assertEqual(self._file(), {"paused": False}, "a limit lift records no spend ruling")
        self.assertEqual(self._cycle(int(self.now) + 3, self.live), "paused", "the cap, never ruled on, pauses")
        self.assertEqual((km._retry_pause_reason(), km._retry_pause_bills()), ("spend", "key"))

    def test_the_user_s_resume_over_a_spend_pause_stands_until_a_new_record(self):
        # the same stale record would undo the detail's Resume the next cycle (the setGlobalRetryPaused route
        # writes the same un-pause): a user gesture is new information too
        self.assertEqual(self._cycle(int(self.now), self.live), "paused")
        floor = km._retry_pause_ts()
        km._set_retry_paused(False)
        states = [self._cycle(int(self.now) + 1 + i, self.live) for i in range(3)]
        self.assertEqual(states, ["degraded"] * 3)
        self.assertEqual(self._file()["supersedes"], floor)

    def test_the_pause_file_remembers_the_spend_lift_across_later_writes(self):
        tests = self._tests_session()
        self._cycle(int(self.now), self.live)
        floor = km._retry_pause_ts()
        self._append(tests, _out_line(floor + 1), floor + 1)
        self._cycle(int(self.now) + 1, self.live)
        d = self._file()
        self.assertEqual(set(d), {"paused", "liftedAt", "supersedes"})
        self.assertEqual((d["paused"], d["supersedes"]), (False, floor), "supersedes: the floor of the pause it cleared")
        lifted = d["liftedAt"]
        self.assertEqual(lifted, int(floor + 1), "the lifting output's own time")
        self.assertEqual(km._retry_pause_lifted_at(), lifted)
        self.assertEqual((km._retry_pause_ts(), km._retry_pause_reason(), km._retry_pause_bills()), (0.0, "", ""))
        km._set_retry_paused(False)                            # a Resume over an unpaused file keeps what it finds
        self.assertEqual((self._file()["liftedAt"], self._file()["supersedes"]), (lifted, floor))
        km._set_retry_paused(True, reason="limit")             # a later pause carries it
        d = self._file()
        self.assertEqual(set(d), {"paused", "t", "reason", "liftedAt", "supersedes"})
        self.assertEqual(d["liftedAt"], lifted)
        km._set_retry_paused(False)                            # and so does its lift
        self.assertEqual(self._file()["liftedAt"], lifted)
        km._set_retry_paused(True)                             # a manual pause carries it, and its un-pause too
        km._set_retry_paused(False)
        self.assertEqual(self._file()["liftedAt"], lifted)
        (self.dir / "retry-paused.json").unlink()              # no memory on file: nothing is invented
        km._set_retry_paused(True)
        km._set_retry_paused(False)
        self.assertEqual(self._file(), {"paused": False})
        self.assertEqual(km._retry_pause_lifted_at(), 0.0)


class PauseWriteSeq(_PauseFixture):
    """The apiHealth frame's `seq` counts pause-file writes: the detail's pause button writes the file on a
    press, and the frame that follows must differ from every frame before it even when the cycle's auto-pause
    put the same state back, so the shell can clear its acknowledgment on the frame that answers the press
    and read the truth (paused again) instead of holding a disabled, mislabeled button for the rest of the
    window."""

    def test_a_write_that_puts_the_same_state_back_still_yields_a_new_frame(self):
        now = time.time()
        path, row = self._session(SID_LOGIN, "api", "login", _out_line(now - 60))
        live = {SID_LOGIN: row}
        self.assertEqual(self._cycle(int(now), live), "paused")
        seq0 = self.sent[-1][1]["seq"]
        self.assertEqual(self._cycle(int(now) + 1, live), "paused")
        self.assertEqual(len(self.sent), 1, "no write, no frame: two cycles over the same world are identical")
        km._set_retry_paused(False)                            # the user pressed Resume during the window
        self.assertEqual(self._cycle(int(now) + 2, live), "paused", "the limit re-engaged within the cycle")
        self.assertEqual(len(self.sent), 2, "same state, but the press and the re-engage wrote the file")
        self.assertEqual(self.sent[-1][1]["seq"], seq0 + 2)
        self.assertEqual(km._retry_pause_reason(), "limit")

    def test_seq_is_an_event_counter_not_a_clock(self):
        self.roster = []
        km._usage = lambda: {"limited": None}
        f1 = km._api_health_frame(10, {})
        f2 = km._api_health_frame(99_999, {})
        self.assertEqual(json.dumps(f1, sort_keys=True), json.dumps(f2, sort_keys=True))
        km._set_retry_paused(False)                            # a no-op write is still a write
        f3 = km._api_health_frame(10, {})
        self.assertEqual(f3["seq"], f1["seq"] + 1)
        self.assertEqual((f3["state"], f3["text"]), (f1["state"], f1["text"]))


if __name__ == "__main__":
    unittest.main()
