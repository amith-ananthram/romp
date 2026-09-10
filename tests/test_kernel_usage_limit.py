#!/usr/bin/env python3
"""Usage-limit banner + auto retry-pause (the user 2026-07-01): when a usage window (5h Session or 7d Weekly)
hits 100% and hasn't reset yet, _usage() flags `limited`, which (a) shows a top banner in the shell and (b)
auto-engages the global retry-pause so romp stops retrying into a rate-limited account (and pauses the judges,
which gate on the same flag). It auto-clears via _auto_resume_retry once a session serves a request again.
Synthetic fixtures only (placeholder ids / hostname TESTHOST)."""
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
km = load_source("romp_kernel_ulimit", os.path.join(BIN, "romp-kernel"))
jd = km.jd


class UsageLimitSignal(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def _write(self, five_pct, seven_pct, five_reset=None, seven_reset=None, fable_pct=None):
        fut = int(time.time()) + 3600
        (jd.STATE / "usage.json").write_text(json.dumps({
            "t": int(time.time()),
            "five_hour": {"pct": five_pct, "resets_at": five_reset if five_reset is not None else fut},
            "seven_day": {"pct": seven_pct, "resets_at": seven_reset if seven_reset is not None else fut},
            "fable": {"pct": fable_pct, "resets_at": fut} if fable_pct is not None else None}))

    # ── _retry_resume_at (the user 2026-07-13): the globalRetryPaused push carries WHEN a limit-driven
    # pause lifts — the earliest future account-window reset — so the chat's API-error card counts down
    # to the actual retry ("usage limit — retrying at HH:MM (in Nm)") instead of a mute paused label. ──
    def test_limited_pause_reports_the_earliest_account_reset(self):
        fut5, fut7 = int(time.time()) + 1800, int(time.time()) + 7200
        self._write(100, 100, five_reset=fut5, seven_reset=fut7)
        km._set_retry_paused(True)
        self.assertEqual(km._retry_resume_at(), fut5, "the earliest account-window reset wins")

    def test_manual_pause_has_no_schedule(self):
        self._write(10, 10)
        km._set_retry_paused(True)
        self.assertIsNone(km._retry_resume_at(), "no limited window → a manual pause carries no ETA")

    def test_unpaused_reports_no_resume_time(self):
        self._write(100, 10)
        km._set_retry_paused(False)
        self.assertIsNone(km._retry_resume_at())

    def test_a_fable_only_limit_reports_no_resume_time(self):
        # fable is model-scoped and never engages the pause; its reset must not masquerade as the ETA
        self._write(10, 10, fable_pct=100)
        km._set_retry_paused(True)
        self.assertIsNone(km._retry_resume_at())

    def test_the_push_carries_resume_at(self):
        self.assertIn('"resumeAt": _retry_resume_at()', Path(BIN, "romp-kernel").read_text(),
                      "the globalRetryPaused push carries the limit-reset ETA")

    def test_a_maxed_window_is_flagged_limited(self):
        self._write(100, 40)
        lim = km._usage()["limited"]
        self.assertEqual(lim, {"fiveHour": True, "sevenDay": False, "fable": False},
                         "the 5h Session window is at its limit")

    def test_both_windows_can_be_limited(self):
        self._write(100, 100)
        self.assertEqual(km._usage()["limited"], {"fiveHour": True, "sevenDay": True, "fable": False})

    def test_under_the_limit_is_not_flagged(self):
        self._write(90, 99)
        self.assertIsNone(km._usage()["limited"], "below 100% → no limit")

    def test_a_maxed_fable_window_is_flagged_limited(self):
        # the included Fable 5 weekly allowance (the user 2026-07-02) still flags `limited` at 100% so the
        # banner + the rail's third bar light up — but, unlike 5h/7d, it does NOT engage the retry-pause
        # (the user 2026-07-03; see AutoPauseOnLimit), because it's a MODEL-scoped limit, not account-wide
        self._write(10, 20, fable_pct=100)
        self.assertEqual(km._usage()["limited"], {"fiveHour": False, "sevenDay": False, "fable": True})

    def test_a_rolled_over_window_is_not_limited(self):
        # 100% but the reset is in the PAST → the window has rolled; the pct is stale, not a live limit
        self._write(100, 20, five_reset=int(time.time()) - 60)
        self.assertIsNone(km._usage()["limited"], "past resetsAt → rolled over, not limited")


class AutoPauseOnLimit(unittest.TestCase):
    """The flip's delivery: the tick job builds nothing inline. Its writer, _set_retry_paused, marks the
    views dirty and wakes the pusher, whose next cycle rebuilds and carries the flag in its
    globalRetryPaused frame; an inline _push_all is the regression the tripwire in setUp catches. The
    idempotent path (already paused) writes nothing, so it neither wakes nor dirties. _views_dirty is a
    module global shared across the suite, so each test records its own floor rather than asserting an
    absolute value."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        self._usage = km._usage
        self._push = km._push_all
        km._push_all = lambda *a, **k: self.fail("a tick job built a push inline; it should wake the pusher")
        self._was_set = km._pusher_wake.is_set()
        km._pusher_wake.clear()

    def tearDown(self):
        jd.STATE = self.saved
        km._usage = self._usage
        km._push_all = self._push
        if self._was_set:
            km._pusher_wake.set()
        else:
            km._pusher_wake.clear()
        self.td.cleanup()

    def test_the_flip_wakes_the_pusher_and_marks_the_views_dirty(self):
        km._usage = lambda: {"limited": {"fiveHour": True, "sevenDay": False, "fable": False}}
        floor = km._views_dirty[0]
        km._auto_pause_on_limit()
        self.assertTrue(km._retry_paused_on())
        self.assertTrue(km._pusher_wake.is_set(), "the flip wakes the pusher; the next cycle carries it")
        self.assertGreater(km._views_dirty[0], floor, "the writer's own mark: that cycle rebuilds past the sig")

    def test_the_idempotent_path_neither_wakes_nor_dirties(self):
        km._set_retry_paused(True)                       # already paused: the write is skipped
        km._pusher_wake.clear()                          # the pre-pause's own wake, not the tick's
        km._usage = lambda: {"limited": {"fiveHour": True, "sevenDay": False, "fable": False}}
        floor = km._views_dirty[0]
        km._auto_pause_on_limit()
        self.assertFalse(km._pusher_wake.is_set(), "nothing written, nothing to deliver")
        self.assertEqual(km._views_dirty[0], floor)

    def test_hitting_a_limit_engages_the_retry_pause(self):
        km._usage = lambda: {"limited": {"fiveHour": True, "sevenDay": False, "fable": False}}
        self.assertFalse(km._retry_paused_on())
        km._auto_pause_on_limit()
        self.assertTrue(km._retry_paused_on(), "a usage limit auto-engages the global retry-pause")

    def test_no_limit_leaves_retries_running(self):
        km._usage = lambda: {"limited": None}
        km._auto_pause_on_limit()
        self.assertFalse(km._retry_paused_on(), "under the limit → retries keep running")

    def test_a_fable_only_limit_does_not_engage_the_pause(self):
        # Fable-5 is MODEL-scoped (the user 2026-07-03): exhausting it doesn't stop the account from serving
        # Sonnet/Haiku (the judges) or Opus (sessions), so it must NOT engage the global pause. Doing so
        # flapped the judges — the account kept serving requests, so _auto_resume_retry cleared the pause each
        # tick and this re-engaged it, starving the distiller. fable=100% still lights the banner (above).
        km._usage = lambda: {"limited": {"fiveHour": False, "sevenDay": False, "fable": True}}
        km._auto_pause_on_limit()
        self.assertFalse(km._retry_paused_on(), "a model-scoped Fable limit must not pause the judges")

    def test_an_account_limit_still_engages_even_alongside_fable(self):
        # a genuine account-wide limit (5h/7d) engages regardless of the fable window's state
        km._usage = lambda: {"limited": {"fiveHour": True, "sevenDay": False, "fable": True}}
        km._auto_pause_on_limit()
        self.assertTrue(km._retry_paused_on(), "a real 5h/7d limit still engages the pause")

    def test_the_pause_latches_reason_limit_at_the_event(self):
        # the bottom bar's API health cell names the pause from the file's reason, never from _retry_resume_at's
        # clock comparison (which would flip the word at the reset instant with no event)
        fut = int(time.time()) + 3600
        km._usage = lambda: {"limited": {"fiveHour": True, "sevenDay": False, "fable": False},
                             "fiveHour": {"pct": 100, "resetsAt": fut}}
        km._auto_pause_on_limit()
        self.assertTrue(km._retry_paused_on())
        self.assertEqual(km._retry_pause_reason(), "limit")
        self.assertEqual(km._retry_resume_at(), fut, "the card's countdown is unchanged by the latched reason")
        self.assertGreater(km._retry_pause_ts(), 0, "the pause's own t is the frame's `since`")

    def test_an_already_paused_flag_keeps_its_reason(self):
        km._set_retry_paused(True, reason="spend")
        km._usage = lambda: {"limited": {"fiveHour": True, "sevenDay": False, "fable": False}}
        km._auto_pause_on_limit()
        self.assertEqual(km._retry_pause_reason(), "spend", "idempotent: the first cause stays on record")


class AutoPauseOnSpendLimit(unittest.TestCase):
    """A monthly SPEND cap auto-engages the global retry-pause — the SPEND twin of AutoPauseOnLimit (the
    user 2026-07-14). Unlike a 5h/7d RATE window (a known reset the card counts down to), a spend cap has
    no readable reset: retrying just re-fails until it's raised, so the 10s auto-retry storms forever
    without this. Detected from the transcript (_api_error.spendLimit), not the usage report. reason='spend'
    → the card says 'raise your cap', no countdown. Synthetic fixtures only."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, km._alive_sessions, km._api_error, km._push_all)
        jd.STATE = Path(self.td.name)
        km._push_all = lambda *a, **k: self.fail("a tick job built a push inline; it should wake the pusher")
        self._was_set = km._pusher_wake.is_set()
        km._pusher_wake.clear()

    def tearDown(self):
        jd.STATE, km._alive_sessions, km._api_error, km._push_all = self.saved
        if self._was_set:
            km._pusher_wake.set()
        else:
            km._pusher_wake.clear()
        self.td.cleanup()

    def test_the_flip_wakes_the_pusher_and_marks_the_views_dirty(self):
        # the same delivery as AutoPauseOnLimit: the writer's wake and dirty mark, no inline push
        self._sessions({"spendLimit": True, "text": "spend limit"})
        floor = km._views_dirty[0]
        km._auto_pause_on_spend_limit(0, {})
        self.assertTrue(km._retry_paused_on())
        self.assertTrue(km._pusher_wake.is_set(), "the flip wakes the pusher; the next cycle carries it")
        self.assertGreater(km._views_dirty[0], floor, "the writer's own mark: that cycle rebuilds past the sig")

    def test_the_no_op_paths_neither_wake_nor_dirty(self):
        floor = km._views_dirty[0]
        self._sessions(None, {"spendLimit": False, "text": "500"})   # no capped session
        km._auto_pause_on_spend_limit(0, {})
        self.assertFalse(km._pusher_wake.is_set())
        self.assertEqual(km._views_dirty[0], floor)
        km._set_retry_paused(True)                       # already paused: the write is skipped
        km._pusher_wake.clear()                          # the pre-pause's own wake, not the tick's
        floor = km._views_dirty[0]
        self._sessions({"spendLimit": True, "text": "spend limit"})
        km._auto_pause_on_spend_limit(0, {})
        self.assertFalse(km._pusher_wake.is_set(), "nothing written, nothing to deliver")
        self.assertEqual(km._views_dirty[0], floor)

    def _sessions(self, *errs):
        sess = [{"sid": "s%d" % i, "path": "/tmp/s%d.jsonl" % i} for i in range(len(errs))]
        emap = {s["path"]: e for s, e in zip(sess, errs)}
        km._alive_sessions = lambda now, tmux: sess
        km._api_error = lambda p: emap.get(p)

    def test_a_spend_cap_engages_the_pause_with_reason_spend(self):
        self._sessions({"spendLimit": True, "tooLong": False, "text": "monthly spend limit"})
        self.assertFalse(km._retry_paused_on())
        km._auto_pause_on_spend_limit(0, {})
        self.assertTrue(km._retry_paused_on(), "a spend cap auto-engages the global pause")
        self.assertEqual(km._retry_pause_reason(), "spend", "the pause names the spend-cap cause")
        self.assertIsNone(km._retry_resume_at(), "a spend cap has no reset to count down to")

    def test_only_the_capped_session_among_healthy_peers_triggers_it(self):
        self._sessions(None, {"spendLimit": True, "text": "spend limit"}, {"spendLimit": False, "text": "500"})
        km._auto_pause_on_spend_limit(0, {})
        self.assertTrue(km._retry_paused_on(), "one capped session is enough — the cap is account-wide")

    def test_a_transient_error_does_not_engage(self):
        self._sessions({"spendLimit": False, "tooLong": False, "text": "500 server error"})
        km._auto_pause_on_spend_limit(0, {})
        self.assertFalse(km._retry_paused_on(), "a transient API error keeps auto-retry running")

    def test_no_error_does_not_engage(self):
        self._sessions(None, None)
        km._auto_pause_on_spend_limit(0, {})
        self.assertFalse(km._retry_paused_on())

    def test_it_is_idempotent_and_never_clobbers_an_existing_pause_reason(self):
        km._set_retry_paused(True)                       # a manual / rate-window pause already on (no reason)
        self._sessions({"spendLimit": True, "text": "spend limit"})
        km._auto_pause_on_spend_limit(0, {})
        self.assertEqual(km._retry_pause_reason(), "", "an existing pause is left untouched")

    def test_the_push_carries_the_reason(self):
        self.assertIn('"reason": _retry_pause_reason()', Path(BIN, "romp-kernel").read_text(),
                      "the globalRetryPaused push carries the pause reason")


class LimitBannerWiring(unittest.TestCase):
    # 2026-07-27: the fixed top banner is GONE — a maxed window now logs ONE entry in the shell's
    # notification center (the bell; see _LANDING_ERRS_JS + test_error_center.py), still gated on the
    # limited-window signature so an episode logs exactly once and a NEW episode logs afresh.
    def test_the_limit_logs_to_the_notification_center_not_a_banner(self):
        land = km._landing()
        self.assertNotIn("<div id=romp-limit>", land, "the top banner element is gone")
        js = km._LANDING_USAGE_JS
        self.assertIn("window.__rompNotify('limit'", js)
        self.assertIn("usage limit reached", js)

    def test_the_entry_is_gated_on_the_limit_signature(self):
        js = km._LANDING_USAGE_JS
        # logging is keyed to WHICH windows are limited (a signature), persisted in localStorage
        self.assertIn("romp:limitDismiss", js)
        self.assertIn("sig!==_limGet()", js)                  # a stored signature suppresses re-logging
        self.assertIn("_limPut(sig)", js)                     # logging stores the signature
        self.assertIn("if(!on){_limPut('');}", js)            # a full clear forgets it
        # a NEW limited-window set has a different signature → a fresh entry (episode identity, not a timer)
        self.assertIn("(lim.fiveHour?'5':'')+(lim.sevenDay?'7':'')", js)


if __name__ == "__main__":
    unittest.main()


class FableBanner(unittest.TestCase):
    def test_the_top_banner_does_NOT_fire_on_a_fable_only_limit(self):
        # the user 2026-07-04: a maxed Fable-5 window is MODEL-scoped — it doesn't pause anything and isn't
        # actionable for someone not on Fable, so the proactive top banner (which popped every refresh for the
        # 7-day window) must NOT trigger on it. It's surfaced when you actually USE Fable (api-error → blocked)
        # and shown passively on the rail's third bar.
        js = km._LANDING_USAGE_JS
        self.assertIn("on=!!(lim&&(lim.fiveHour||lim.sevenDay))", js, "the banner fires only on account-wide 5h/7d")
        self.assertNotIn("lim.fiveHour||lim.sevenDay||lim.fable", js, "no longer triggers on the fable window")
        self.assertNotIn("names.push('Fable 5 (7d)')", js, "and no longer names it in the banner")

    def test_fable_is_still_flagged_limited_for_the_rail_bar(self):
        # the passive third rail bar still reflects the Fable window — only the proactive BANNER is suppressed
        self.assertIn("['fable',7*86400,'Fable 5']", km._LANDING_USAGE_JS, "the rail still has the Fable bar")


class JudgeFailureBanner(unittest.TestCase):
    """The judge-failure surfacing (the user 2026-07-03): a distiller/brief give-up stamps a card warn, and the
    kernel counts those warns fleet-wide into `judgeFailures` on the usage payload → a top banner like the
    usage-limit one. _usage_for_client() attaches the count; the landing renders + dismisses the banner."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = (jd.STATE, jd.GOALDIR)
        jd.STATE = Path(self.td.name)
        jd.GOALDIR = jd.STATE / "goals"
        self._u, self._jf = km._usage, km._judge_failures
        km._jf_cache["fp"] = None; km._jf_cache["val"] = None

    def tearDown(self):
        jd.STATE, jd.GOALDIR = self.saved
        km._usage, km._judge_failures = self._u, self._jf
        km._jf_cache["fp"] = None; km._jf_cache["val"] = None
        self.td.cleanup()

    def test_usage_for_client_attaches_judge_failures(self):
        km._usage = lambda: {"fiveHour": None, "limited": None}
        km._judge_failures = lambda: {"count": 2, "cause": "the summarizer kept hitting errors or timeouts",
                                      "ratelimited": False}
        u = km._usage_for_client()
        self.assertEqual(u["judgeFailures"]["count"], 2, "the fleet failure count rides the usage payload")

    def test_usage_for_client_omits_the_key_when_nothing_is_failing(self):
        km._usage = lambda: {"fiveHour": None, "limited": None}
        km._judge_failures = lambda: None
        self.assertNotIn("judgeFailures", km._usage_for_client(), "no failures → no key → banner stays down")

    def test_judge_failures_reads_the_scan_and_caches_on_the_goals_fingerprint(self):
        calls = []
        saved = jd.judge_failure_scan
        try:
            jd.judge_failure_scan = lambda: (calls.append(1),
                                             {"count": 1, "cause": "x", "ratelimited": False})[1]
            a = km._judge_failures(); b = km._judge_failures()      # goals dir absent → stable fingerprint
            self.assertEqual(a["count"], 1)
            self.assertEqual(len(calls), 1, "an unchanged goals-dir fingerprint serves the cache, no re-scan")
        finally:
            jd.judge_failure_scan = saved

    def test_a_judge_failure_logs_to_the_notification_center_not_a_banner(self):
        # 2026-07-27: the top banner is gone — the same situation logs an entry in the shell's
        # notification center, once per count+cause signature (a changed situation logs afresh)
        land = km._landing()
        self.assertNotIn("<div id=romp-judge-degraded>", land, "the top banner element is gone")
        js = km._LANDING_USAGE_JS
        self.assertIn("u.judgeFailures", js)                        # driven off the payload key
        self.assertIn("window.__rompNotify('judge'", js)
        self.assertIn("couldn't be summarized", js)                # names the count
        self.assertIn("romp:judgeDegradedDismiss", js)             # signature-keyed, like the limit one
        self.assertIn("jf.count+'|'+(jf.cause||'')", js)           # signature = count + cause → logs on change


if __name__ == "__main__":
    unittest.main()
