#!/usr/bin/env python3
"""The bottom bar's API health cell: one dot and one word beside the usage readout, answering 'is the API
serving my sessions'. The kernel builds one apiHealth frame per pusher cycle from the merged live map's
retrying state, each alive transcript's LATCHED newest API error (_api_last_failed) and the retry-pause
file, and pushes it to the shells only when it changed; the shell paints the cell from the frame and builds
the click detail from it, with no fetch and no timer.

The rule these tests pin: every field moves on one named event and never on a clock, so two computes over
the same world are byte-identical and send nothing. Synthetic fixtures only (a private synthetic sid family,
the notes-api demo's web / api / tests names, no paths or error text in any frame)."""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_apih", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_apih", os.path.join(BIN, "..", "kernel", "sdk_backend.py"))

# A PRIVATE synthetic sid family for this module (never the shared 11111111-2222 placeholder, never real).
SID = ["77777777-aaaa-4bbb-8ccc-00000000000%d" % i for i in range(1, 6)]
NAMES = ["web", "api", "tests", "docs", "build"]
T_STORM = 1_700_000_000          # a storm turn's start (SdkSession.since, once per fresh turn)
T_REC = 1_700_000_100            # an error record's own timestamp
MDOT = "·"


def _frame_keys():
    return {"type", "state", "cls", "reason", "text", "waiting", "retrying", "blocked", "since", "tmux", "sessions", "seq", "hosts", "quiet", "errs", "host"}


class Reference(unittest.TestCase):
    """docs/reference.md's subsection on the cell documents the frame and the pause file the code writes."""

    def _section(self):
        doc = Path(os.path.dirname(HERE), "docs", "reference.md").read_text()
        i = doc.index("### The bottom bar's indicator")
        return doc[i:doc.index("\n## ", i)]

    def test_the_documented_frame_has_the_code_s_keys(self):
        sec = self._section()
        block = sec[sec.index("```json\n") + len("```json\n"):sec.index("\n```", sec.index("```json\n"))]
        shape = json.loads(block)
        self.assertEqual(set(shape), _frame_keys(), "the reference's frame block and _api_health_frame drift")
        self.assertIn("`seq` counts the retry-pause file's writes", sec)

    def test_the_pause_file_paragraph_names_every_field_the_code_writes(self):
        sec = self._section()
        for k in ("`paused`", "`t`", "`reason`", "`bills`", "`liftedAt`", "`supersedes`"):
            self.assertIn(k, sec, k)
        src = inspect.getsource(km._set_retry_paused)
        for k in ("paused", "t", "reason", "bills", "liftedAt", "supersedes"):
            self.assertIn('"%s"' % k, src, "the paragraph names a field the code does not write: " + k)


class _Fixture(unittest.TestCase):
    """Fixture sessions: `self.sess` is the alive roster, `self.live` the merged live map, `self.errs` the
    latched error per transcript path. Everything the frame reads is patched at the module seam."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._state = km.jd.STATE
        km.jd.STATE = Path(self.td.name)
        self._alive = km._alive_sessions
        self._last = km._api_last_failed
        self._send = km._send_to_app
        self._backend_for = km.Sessions.__dict__["backend_for"]
        self._color = km._name_color
        self.sess, self.live, self.errs, self.tmux_sids, self.colors = [], {}, {}, set(), {}
        km._alive_sessions = lambda now, tmux: list(self.sess)
        km._api_last_failed = lambda p: self.errs.get(p)
        self.sent = []
        km._send_to_app = lambda app, m: self.sent.append((app, m))
        km.Sessions.backend_for = staticmethod(lambda sid: km._TMUX if sid in self.tmux_sids else object())
        km._name_color = lambda sid: self.colors.get(sid)
        # the frame's quiet and errs flags ask the SDK backend through km._sdk (T301): patched, so no test builds a
        # real SdkBackend in-process (its boot reconcile thread and catalog fetch); `self.backend` is what it answers
        self.backend, self.sdk_calls = None, []
        self._sdk = km._sdk
        km._sdk = lambda: (self.sdk_calls.append(1), self.backend)[1]
        self._self_host = km._self_host
        km._self_host = lambda: "TESTHOST"   # the frame names this kernel (T316); synthetic, never the machine's real name
        km._APIH_LAST[0] = None
        km._retry_suppress_cache.clear()

    def tearDown(self):
        km._sdk = self._sdk
        km._self_host = self._self_host
        km.jd.STATE = self._state
        km._alive_sessions = self._alive
        km._api_last_failed = self._last
        km._send_to_app = self._send
        km.Sessions.backend_for = self._backend_for
        km._name_color = self._color
        km._APIH_LAST[0] = None
        km._retry_suppress_cache.clear()
        self.td.cleanup()

    def add(self, i, state="waiting", retry=None, err=None, since=T_STORM):
        """One alive session: `retry` = a retryInfo dict puts it in the api_retry storm; `err` = the latched
        error record's fields (status / category / t / the on-you flags)."""
        path = os.path.join(self.td.name, NAMES[i] + ".jsonl")
        self.sess.append({"sid": SID[i], "name": NAMES[i], "path": path, "anchor": "", "mtime": 0})
        row = {"state": state, "since": since, "backend": "sdk"}
        if retry is not None:
            row["state"] = "retrying"
            row["retryInfo"] = retry
        self.live[SID[i]] = row
        if err is not None:
            e = {"status": None, "category": "unknown", "t": T_REC, "text": "", "uuid": "x",
                 "tooLong": False, "spendLimit": False, "modelLimit": False, "authErr": False, "refusal": False}
            e.update(err)
            self.errs[path] = e
        return SID[i]

    def reset(self):
        self.sess.clear()
        self.live.clear()
        self.errs.clear()

    def frame(self, now=10):
        return km._api_health_frame(now, self.live)


class States(_Fixture):
    def test_no_rows_and_no_pause_is_ok(self):
        self.add(0)
        f = self.frame()
        self.assertEqual((f["state"], f["cls"], f["reason"], f["text"]), ("ok", "", "", "ok"))
        self.assertEqual((f["waiting"], f["retrying"], f["blocked"], f["since"]), (0, 0, 0, 0))
        self.assertEqual(f["sessions"], [])

    def test_a_retrying_429_row_reads_rate_limited(self):
        self.add(0, retry={"status": 429, "attempt": 2, "max": 10})
        f = self.frame()
        self.assertEqual((f["state"], f["cls"], f["text"]), ("degraded", "429", "rate limited %s 1 waiting" % MDOT))
        self.assertEqual((f["waiting"], f["retrying"], f["blocked"]), (1, 1, 0))
        self.assertEqual(f["sessions"][0]["kind"], "retrying")

    def test_a_latched_529_record_reads_overloaded(self):
        self.add(0, err={"status": 529, "category": "overloaded"})
        f = self.frame()
        self.assertEqual((f["state"], f["cls"], f["text"]), ("degraded", "529", "overloaded %s 1 waiting" % MDOT))
        self.assertEqual((f["waiting"], f["retrying"], f["blocked"]), (1, 0, 1))
        self.assertEqual(f["sessions"][0]["kind"], "blocked")

    def test_network_down_with_no_status_reads_offline(self):
        self.add(0, retry={"status": None, "networkDown": True})
        f = self.frame()
        self.assertEqual((f["cls"], f["text"]), ("offline", "offline %s 1 waiting" % MDOT))

    def test_a_500_reads_errors(self):
        self.add(0, err={"status": 500, "category": "server_error"})
        f = self.frame()
        self.assertEqual((f["cls"], f["text"]), ("errors", "errors %s 1 waiting" % MDOT))

    def test_a_no_status_record_without_a_network_flag_reads_errors_not_offline(self):
        # only a retrying row can say offline: a transcript record carries no network flag
        self.add(0, err={"status": None, "category": "unknown"})
        self.assertEqual(self.frame()["cls"], "errors")

    def test_plurality_picks_the_most_common_class(self):
        self.add(0, retry={"status": 429})
        self.add(1, retry={"status": 429})
        self.add(2, err={"status": 529, "category": "overloaded"})
        f = self.frame()
        self.assertEqual((f["cls"], f["text"]), ("429", "rate limited %s 3 waiting" % MDOT))

    def test_ties_resolve_in_the_fixed_order(self):
        self.add(0, retry={"status": 429})
        self.add(1, err={"status": 529, "category": "overloaded"})
        self.assertEqual(self.frame()["cls"], "429", "429 before 529")
        self.reset()
        self.add(0, err={"status": 529, "category": "overloaded"})
        self.add(1, err={"status": 500, "category": "server_error"})
        self.assertEqual(self.frame()["cls"], "529", "529 before errors")
        self.reset()
        self.add(0, retry={"status": None, "networkDown": True})
        self.add(1, err={"status": 500, "category": "server_error"})
        self.assertEqual(self.frame()["cls"], "offline", "offline before errors")

    def test_on_you_failures_do_not_count(self):
        for flag in ("tooLong", "modelLimit", "authErr", "refusal"):
            self.reset()
            self.add(0, err={"status": 400, "category": "invalid_request", flag: True})
            f = self.frame()
            self.assertEqual((f["state"], f["waiting"]), ("ok", 0), flag + " is the session's own, not the API's")

    def test_a_spend_limit_record_counts(self):
        self.add(0, err={"status": 400, "category": "billing_error", "spendLimit": True})
        f = self.frame()
        self.assertEqual((f["state"], f["cls"], f["waiting"]), ("degraded", "errors", 1))

    def test_a_spend_pause_outranks_any_class(self):
        self.add(0, retry={"status": 429})
        self.add(1, retry={"status": 429})
        km._set_retry_paused(True, reason="spend")
        f = self.frame()
        self.assertEqual((f["state"], f["reason"], f["text"]),
                         ("paused", "spend", "paused %s spend cap %s 2 waiting" % (MDOT, MDOT)))
        self.assertEqual(f["cls"], "429", "the class still rides along for the detail")

    def test_a_limit_pause_names_the_usage_limit(self):
        self.add(0, retry={"status": 429})
        km._set_retry_paused(True, reason="limit")
        f = self.frame()
        self.assertEqual((f["state"], f["reason"], f["text"]),
                         ("paused", "limit", "paused %s usage limit %s 1 waiting" % (MDOT, MDOT)))

    def test_a_manual_pause_reads_paused_by_you(self):
        self.add(0, err={"status": 500, "category": "server_error"})
        km._set_retry_paused(True)
        f = self.frame()
        self.assertEqual((f["reason"], f["text"]), ("manual", "paused by you %s 1 waiting" % MDOT))

    def test_a_pause_with_nothing_waiting_drops_the_count(self):
        self.add(0)
        km._set_retry_paused(True, reason="limit")
        self.assertEqual(self.frame()["text"], "paused %s usage limit" % MDOT)
        km._set_retry_paused(True, reason="spend")
        self.assertEqual(self.frame()["text"], "paused %s spend cap" % MDOT)
        km._set_retry_paused(True)
        self.assertEqual(self.frame()["text"], "paused by you")

    def test_a_suppressed_row_carries_suppressed_true(self):
        sid = self.add(0, err={"status": 500, "category": "server_error"})
        self.add(1, retry={"status": 429})
        km._suppress_session_retry(sid)
        rows = {r["sid"]: r for r in self.frame()["sessions"]}
        self.assertTrue(rows[SID[0]]["suppressed"])
        self.assertFalse(rows[SID[1]]["suppressed"])
        self.assertEqual(self.frame()["waiting"], 2, "the interrupt says nothing about the API: still counted")

    def test_tmux_backed_sessions_are_counted_for_the_coverage_line(self):
        self.add(0)
        self.add(1)
        self.add(2, err={"status": 500, "category": "server_error"})
        self.tmux_sids = {SID[1], SID[2]}
        self.assertEqual(self.frame()["tmux"], 2)

    def test_rows_carry_name_and_color(self):
        self.colors[SID[0]] = {"bg": "#3366cc", "fg": "#ffffff"}
        self.add(0, retry={"status": 429})
        r = self.frame()["sessions"][0]
        self.assertEqual((r["name"], r["color"]), ("web", {"bg": "#3366cc", "fg": "#ffffff"}))

    def test_a_latched_row_reads_retrying_while_its_session_is_working(self):
        # The retry prompt (romp's own, or a human's) was accepted and the turn is open, no api_retry frame yet;
        # for a tmux session that is the whole internal retry. The word follows the live state; the latch only
        # keeps the row counted, and its since stays the record's time.
        self.add(0, state="working", err={"status": 529, "category": "overloaded"})
        f = self.frame()
        r = f["sessions"][0]
        self.assertEqual((r["kind"], r["cls"], r["status"], r["since"]), ("retrying", "529", 529, T_REC))
        self.assertEqual((f["waiting"], f["retrying"], f["blocked"], f["text"]),
                         (1, 1, 0, "overloaded %s 1 waiting" % MDOT))
        self.add(1, state="idle", err={"status": 529, "category": "overloaded"})
        f = self.frame()
        self.assertEqual([x["kind"] for x in f["sessions"]], ["retrying", "blocked"])
        self.assertEqual((f["waiting"], f["retrying"], f["blocked"]), (2, 1, 1))

    def test_a_string_status_on_the_wire_is_read_as_a_number(self):
        self.add(0, retry={"status": "529"})
        f = self.frame()
        self.assertEqual((f["cls"], f["sessions"][0]["status"]), ("529", 529))


class NoFlap(_Fixture):
    def test_two_computes_over_the_same_world_are_byte_identical_and_send_once(self):
        self.add(0, retry={"status": 429, "attempt": 3, "retryAt": 1_700_000_050.5})
        self.add(1, err={"status": 500, "category": "server_error"})
        f1 = self.frame(now=10)
        km._api_health_push(f1)
        f2 = self.frame(now=99_999)
        km._api_health_push(f2)
        self.assertEqual(json.dumps(f1, sort_keys=True), json.dumps(f2, sort_keys=True))
        self.assertEqual(len(self.sent), 1, "an unchanged world sends nothing")
        self.assertEqual(self.sent[0][0], "shell")

    def test_attempt_and_retry_at_changes_move_nothing(self):
        self.add(0, retry={"status": 429, "attempt": 3, "retryAt": 100.0, "error": "429 rate limited"})
        km._api_health_push(self.frame())
        self.live[SID[0]]["retryInfo"].update({"attempt": 4, "retryAt": 200.0, "error": "429 again"})
        km._api_health_push(self.frame())
        self.assertEqual(len(self.sent), 1, "per-attempt counters are not inputs")

    def test_a_row_flipping_retrying_to_blocked_with_the_same_count_does_send(self):
        self.add(0, retry={"status": 429})
        km._api_health_push(self.frame())
        self.live[SID[0]] = {"state": "waiting", "since": T_STORM}
        self.errs[self.sess[0]["path"]] = {"status": 429, "category": "rate_limit", "t": T_REC,
                                            "tooLong": False, "modelLimit": False, "authErr": False, "refusal": False}
        km._api_health_push(self.frame())
        self.assertEqual(len(self.sent), 2, "the storm gave up and left a record: new information")
        self.assertEqual(self.sent[1][1]["waiting"], 1)
        self.assertEqual((self.sent[1][1]["retrying"], self.sent[1][1]["blocked"]), (0, 1))

    def test_a_pause_set_and_lifted_each_send_once(self):
        self.add(0)
        km._api_health_push(self.frame())
        km._set_retry_paused(True, reason="limit")
        km._api_health_push(self.frame())
        km._api_health_push(self.frame())
        km._set_retry_paused(False)
        km._api_health_push(self.frame())
        self.assertEqual([m["state"] for _, m in self.sent], ["ok", "paused", "ok"])

    def test_since_is_the_event_stamp_never_the_clock(self):
        self.add(0, retry={"status": 429}, since=T_STORM)
        self.add(1, err={"status": 500, "category": "server_error", "t": T_REC})
        f = self.frame(now=5_000_000_000)
        self.assertEqual(f["since"], T_STORM, "the earliest affected event")
        rows = {r["sid"]: r for r in f["sessions"]}
        self.assertEqual(rows[SID[0]]["since"], T_STORM)
        self.assertEqual(rows[SID[1]]["since"], T_REC)
        km._set_retry_paused(True, reason="spend")
        self.assertEqual(self.frame()["since"], int(km._retry_pause_ts()), "paused: the pause's own t")

    def test_the_roster_order_cannot_reshuffle_an_unchanged_world(self):
        self.add(0, retry={"status": 429})
        self.add(1, err={"status": 500, "category": "server_error"})
        km._api_health_push(self.frame())
        self.sess.reverse()                              # a newer mtime moved a session up the roster
        km._api_health_push(self.frame())
        self.assertEqual(len(self.sent), 1)

    def test_seq_moves_on_every_pause_write_and_only_then(self):
        # a press on the detail's pause button writes the pause file; the frame after it must differ from every
        # frame before it even when the cycle's auto-pause put the same state back, so the shell can clear its
        # acknowledgment on the frame that answers the press
        self.add(0, retry={"status": 429})
        km._api_health_push(self.frame())
        km._api_health_push(self.frame(now=99))
        self.assertEqual(len(self.sent), 1, "no write: an unchanged world sends nothing")
        seq0 = self.sent[0][1]["seq"]
        km._set_retry_paused(False)                        # a Resume landing on an already-unpaused file
        km._api_health_push(self.frame())
        self.assertEqual(len(self.sent), 2, "the write is the event, whatever state it left")
        self.assertEqual(self.sent[1][1]["seq"], seq0 + 1)
        self.assertEqual((self.sent[1][1]["state"], self.sent[1][1]["text"]),
                         (self.sent[0][1]["state"], self.sent[0][1]["text"]))
        km._set_retry_paused(True, reason="limit")
        km._set_retry_paused(False)
        km._api_health_push(self.frame())
        self.assertEqual(self.sent[2][1]["seq"], seq0 + 3, "every write counts, including one lifted within the cycle")

    def test_the_ready_handler_resends_the_last_frame_to_a_shell_only(self):
        # recording sinks on every client: _apih_resend guards the send with a try/except, so a sink that raised
        # to fail the test would be swallowed by the very function under test
        self.add(0, retry={"status": 429})
        km._api_health_push(self.frame())
        got, got_chat, got_feed = [], [], []
        km._apih_resend({"app": "shell", "send": got.append})
        km._apih_resend({"app": "chat", "send": got_chat.append})
        km._apih_resend({"app": "feed", "send": got_feed.append})
        self.assertEqual(len(got), 1)
        self.assertEqual(json.loads(got[0]), self.sent[0][1], "verbatim: the client diffs state and text")
        self.assertEqual((got_chat, got_feed), ([], []), "a chat or pane client owns no rail")

    def test_nothing_to_resend_before_the_first_frame(self):
        got = []
        km._apih_resend({"app": "shell", "send": got.append})
        self.assertEqual(got, [], "nothing sent since boot")


class FrameShape(_Fixture):
    def test_the_frame_and_its_rows_carry_exactly_the_documented_keys(self):
        self.add(0, retry={"status": 429, "error": "the wire's text", "requestId": "req_x"})
        self.add(1, err={"status": 500, "category": "server_error", "text": "API Error: 500"})
        f = self.frame()
        self.assertEqual(set(f), _frame_keys())
        for r in f["sessions"]:
            self.assertEqual(set(r), {"sid", "name", "color", "kind", "cls", "status", "since", "suppressed"})
        s = json.dumps(f)
        self.assertNotIn(".jsonl", s, "no path in any frame")
        self.assertNotIn("API Error", s, "no error text in any frame")
        self.assertNotIn("req_x", s)
        self.assertNotIn("attempt", s)

    def test_apih_class_agrees_with_the_backend_on_shared_statuses(self):
        collapse = {"429": "429", "529": "529", "5xx": "errors", "other": "errors", "none": "errors"}
        for status, cat in ((429, ""), (529, ""), (500, ""), (400, ""), (None, "rate_limit"), (None, "overloaded")):
            self.assertEqual(km._apih_class(status, cat), collapse[sb.api_health_status_class(status, cat)],
                             "status=%r category=%r" % (status, cat))
        self.assertEqual(km._apih_class(None, "", True), "offline", "the one word the backend lacks")

    def test_the_text_table_is_the_rail_s_words(self):
        self.assertEqual(km._APIH_TEXT, {"ok": "ok", "429": "rate limited", "529": "overloaded", "offline": "offline",
                                         "errors": "errors", "limit": "paused %s usage limit" % MDOT,
                                         "spend": "paused %s spend cap" % MDOT, "manual": "paused by you"})
        for v in km._APIH_TEXT.values():
            self.assertNotIn("blocked", v)
        self.assertEqual(km._APIH_ORDER, ("429", "529", "offline", "errors"))


class Wiring(unittest.TestCase):
    # the cycle's other jobs, stubbed quiet when the jobs block runs here (tests/test_wire_once_per_build.py's list)
    OTHER_JOBS = ("_apply_pending_ops", "_push_all", "_turn_notify_tick", "_lift_spent_awaiting", "_death_sweep_tick",
                  "_end_on_idle_sweep", "_deferral_sweep_tick", "_auto_nudge_tick", "_interrupt_block_tick",
                  "_usage_poll_tick", "_auto_resume_session_retry", "_auto_retry_tick", "_idle_queue_drive_tick",
                  "_clear_done_working_notes")

    def test_the_frame_is_built_in_the_jobs_block_after_this_cycle_s_pause_decisions(self):
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_api_health_push(_api_health_frame(now, tmux))", src)
        self.assertLess(src.index("_auto_resume_retry(now, tmux)"), src.index("_api_health_push(_api_health_frame"))
        self.assertNotIn("_api_health", inspect.getsource(km._cached_feed), "not gated by the feed's sig / rebuild floor")

    def test_the_jobs_block_runs_the_frame_after_the_pause_decisions(self):
        # the executing twin of the pin above: a frame built before _auto_resume_retry would carry the pause the
        # same cycle lifts, so the cell would read paused one cycle late on every lift
        order = []
        quiet = {nm: (lambda *a, **k: None) for nm in self.OTHER_JOBS}
        with mock.patch.multiple(km, **quiet), \
                mock.patch.object(km, "_auto_pause_on_limit", side_effect=lambda: order.append("limit")), \
                mock.patch.object(km, "_auto_pause_on_spend_limit", side_effect=lambda now, tmux: order.append("spend")), \
                mock.patch.object(km, "_auto_resume_retry", side_effect=lambda now, tmux: order.append("resume")), \
                mock.patch.object(km, "_api_health_frame", side_effect=lambda now, tmux: order.append("frame") or {"type": "apiHealth"}), \
                mock.patch.object(km, "_api_health_push", side_effect=lambda f: order.append("push:" + f["type"])):
            km._pusher_cycle_jobs(T_STORM, {}, True)
        self.assertEqual(order, ["limit", "spend", "resume", "frame", "push:apiHealth"])

    def test_the_ready_handler_resends_beside_the_badge(self):
        src = Path(BIN, "romp-kernel").read_text()
        i = src.index('client["send"](json.dumps({"type": "badge", "n": _BADGE_LAST[0]}))')
        j = src.index("_apih_resend(client)", i)          # the CALL in the ready handler, after the def
        self.assertLess(j - i, 400, "right beside the badge re-send, in the same ready branch")
        self.assertIn("def _apih_resend(client):", src)

    def test_auto_pause_on_limit_latches_reason_limit(self):
        self.assertIn('_set_retry_paused(True, reason="limit")', inspect.getsource(km._auto_pause_on_limit))

    def test_the_global_retry_paused_frame_still_carries_the_reason(self):
        # the chat card's paused line reads `reason` off the globalRetryPaused frame (render.ts retryPausedText
        # checks spend first and otherwise renders the resumeAt countdown, so a latched "limit" reason draws the
        # countdown exactly as an unlabeled limit pause did); the latch changes the file, not the frame
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('"reason": _retry_pause_reason()})', src)



class Detail(unittest.TestCase):
    """The click detail's content, pinned at source (no jsdom for the shell page); the served behaviour is
    tests/test_api_health_browser.py."""

    def setUp(self):
        self.JS = km._LANDING_APIH_JS

    def test_the_plain_words_sentences_are_present_verbatim(self):
        for s in ("Auto-retry and the judges are paused until your usage limit resets.",
                  "Auto-retry and the judges are paused: you have reached the monthly spend limit. Raise it at claude.ai/settings/usage.",
                  "Auto-retry and the judges are paused: you stopped them.",
                  "API health", "Sessions waiting", "since "):
            self.assertIn(s, self.JS)
        # T301: the head reads what happened across every connected kernel; the old one-machine label and the ok
        # sentence are gone, and the state machine's word never reaches the user
        self.assertNotIn("API %s this machine" % MDOT, self.JS)
        self.assertNotIn("No session is waiting on the API.", self.JS)
        self.assertIn("['r429','429','rate limit: the API told us to slow down']", self.JS)   # T340: the token in its ink, the words beside it

    def test_the_pause_button_is_the_chat_card_s_and_acknowledges_before_the_round_trip(self):
        self.assertIn("'Resume all auto-retries'", self.JS)
        self.assertIn("'Stop all auto-retries'", self.JS)
        self.assertIn("{type:'setGlobalRetryPaused',value:v}", self.JS)
        press = self.JS[self.JS.index("if(act==='pause')"):self.JS.index("else if(act==='reveal')")]
        self.assertIn("t.disabled=true", press)
        self.assertIn("t.textContent=v?RESUME:STOP", press)
        self.assertIn("t.classList.add('romp-acted')", press)
        self.assertLess(press.index("t.classList.add('romp-acted')"), press.index("__rompShellSend("), "acknowledged first")
        self.assertIn("hint=NOTSENT", press, "a dead socket is said, not swallowed")
        self.assertIn("var NOTSENT='Not sent: the dashboard is disconnected. Try again.';", self.JS)

    def test_a_session_row_opens_that_session_the_way_the_feed_s_links_do(self):
        # feed.ts openOrReviveSession posts openSession for a live session; the row does the same on the shell
        # socket, toggles no pane and persists nothing about the layout
        row = self.JS[self.JS.index("else if(act==='reveal')"):self.JS.index("else if(act==='usage')")]
        self.assertIn("__rompShellSend({type:'openSession',id:sid})", row)
        self.assertNotIn("__rompPaneToggle", self.JS, "never a pane toggle from the card")
        self.assertNotIn("revealCard", self.JS)
        self.assertIn("data-act=reveal data-sid=", self.JS)
        self.assertIn("else{hint=NOTSENT;dirty=true;}", row, "a dead socket is said here too")

    def test_no_terminal_coverage_line_is_drawn(self):
        # T331 (the user 2026-09-10: the tmux backend is being removed): the popup no longer says how many terminal
        # sessions are seen through their transcripts; the frame's `tmux` count stays on the wire until the kernel
        # side goes (a federation field older peers keep sending)
        self.assertNotIn("m.tmux", self.JS)
        self.assertNotIn("seen through their transcripts only", self.JS)

    def test_the_detail_renders_from_the_last_frame_and_the_history_is_the_one_read(self):
        # the cell and the frame's reading render from the last frame only; the History section is the one fetch,
        # GET /api-health at show time, and there is still no timer anywhere (test_api_health_hover.py holds the
        # section's own pins)
        self.assertEqual(self.JS.count("fetch("), 1, "one read path: fetchDoc, the history's")
        self.assertIn("fetchDoc(h?'/remote/'+encodeURIComponent(h)+'/api-health':'/api-health')", self.JS)
        # T301: every attached host's document rides the same read, through the kernel's relay, kept per host
        self.assertIn("names.forEach(function(h){fetchDoc(h?", self.JS)
        self.assertIn("names.forEach(function(h){fetchDoc(h?'/remote/'+encodeURIComponent(h)+'/api-health':'/api-health')", self.JS)
        # the one timer is the read-age label's minute tick (T316 review): it re-words one span while the tip or the detail is open
        self.assertEqual(self.JS.count("setInterval("), 1)
        self.assertIn("setInterval(ageTick,60000)", self.JS)
        self.assertNotIn("fetch", self.JS[self.JS.index("function ageTick"):self.JS.index("function disarmAge")], "the tick reads nothing: nothing polls the history")
        self.assertNotIn("setTimeout", self.JS)
        self.assertIn("window.__rompApiHealth=function(m){", self.JS)
        self.assertIn("LAST=m;", self.JS)

    def test_the_cell_repaints_only_when_state_or_text_changed_and_shows_on_the_first_frame(self):
        self.assertIn("if(el.hidden)el.hidden=false;", self.JS)
        # T301: the cell is a dot alone, painted from the MERGED view (every connected kernel); the DOM is touched only
        # when the merged state or the description changed
        self.assertIn("paintCell();", self.JS)
        self.assertIn("if(el.getAttribute('data-dot')!==mg.dot)el.setAttribute('data-dot',mg.dot);", self.JS)
        self.assertIn("var lab='API health: '+DOTWORD[mg.dot]+(mg.n>1?' across '+mg.n+' machines':'');if(el.getAttribute('aria-label')!==lab)el.setAttribute('aria-label',lab);", self.JS)
        self.assertNotIn(".ah-text", self.JS)
        self.assertIn("if(tip.style.display!=='block')return;", self.JS)
        self.assertIn("if(held){dirty=true;return;}render();};", self.JS)

    def test_the_hover_renders_no_controls_and_the_pinned_detail_does(self):
        self.assertIn("function html(m,full)", self.JS)
        self.assertIn("if(full)h+=btnHTML(m);", self.JS)
        self.assertIn("(full?' role=button tabindex=0 data-act=reveal data-sid=\"'+esc(r.sid)+'\"':'')", self.JS)
        self.assertIn("if(full)h+='<div class=\"ru-tip-row ah-foot\">", self.JS)
        self.assertIn("tip.innerHTML=html(LAST,pinned);", self.JS, "pinned = full; the hover = the reading only")

    def test_a_frame_under_a_held_pointer_is_painted_on_release_never_under_the_press(self):
        # only a PRIMARY press arms the defer: no click follows a right or middle button, so a frame deferred
        # under one would stay unpainted until the next frame changed something
        self.assertIn("tip.addEventListener('pointerdown',function(ev){if(ev.button===0)held=true;});", self.JS)
        self.assertIn("document.addEventListener('pointerup',release);", self.JS)
        self.assertIn("document.addEventListener('pointercancel',release);", self.JS)
        self.assertIn("if(held){dirty=true;return;}render();", self.JS)
        self.assertIn("ev.button===0&&ev.target&&tip.contains(ev.target))return;flush();}", self.JS,
                      "a primary release inside waits for its click; any other release flushes")
        self.assertIn("tip.addEventListener('click',function(ev){var t=actOf(ev.target);if(t)run(t);flush();});", self.JS,
                      "the click handler flushes last")

    def test_the_acknowledgment_holds_until_the_frame_that_answers_the_press(self):
        # the frame's seq is the pause file's write count; the press writes it, so the frame after the press
        # carries a moved seq whatever state it brings (paused again, when a limit or spend pause re-engaged
        # within the cycle). A rule keyed on a matching state would leave a Resume during a usage-limit pause
        # disabled and mislabeled for the rest of the window.
        self.assertIn("pending=v?1:0;pendSeq=LAST?LAST.seq:null;", self.JS)
        self.assertIn("if(pending!==null)return '<button class=\"ah-btn romp-acted\" disabled data-act=pause", self.JS)
        self.assertIn("if(pending!==null&&(m.seq==null||m.seq!==pendSeq))pending=null;", self.JS)
        self.assertNotIn("(pending===1)===(m.state==='paused')", self.JS, "no state matching")

    def test_the_rows_and_footer_links_are_keyboard_buttons_inside_a_focus_trap(self):
        self.assertIn("<span class=ah-link role=button tabindex=0 data-act=usage>Usage and spend</span>", self.JS)
        self.assertIn("<span class=ah-link role=button tabindex=0 data-act=log>Log</span>", self.JS)
        self.assertIn("tip.setAttribute('aria-modal','true')", self.JS)
        self.assertIn("tip.addEventListener('keydown',function(ev){if(!pinned)return;", self.JS)
        self.assertIn("if(ev.key==='Tab'){var f=controls(),i=f.indexOf(document.activeElement);", self.JS)
        self.assertIn("if(ev.shiftKey){if(i<=0){ev.preventDefault();f[f.length-1].focus();}}", self.JS)
        self.assertIn("else if(i<0||i===f.length-1){ev.preventDefault();f[0].focus();}", self.JS)
        self.assertIn("if(ev.key!=='Enter'&&ev.key!==' ')return;var t=actOf(ev.target);if(!t||t.tagName==='BUTTON')return;", self.JS,
                      "Enter / Space run a row or link; the button's own keys click it natively")
        self.assertIn("ev.preventDefault();run(t);flush();});", self.JS)
        # a re-render keeps focus on the same control, else the card: the trap must survive a frame
        self.assertIn("key=(pinned&&a&&a!==tip&&tip.contains(a))?focusKey(a):null;", self.JS)
        self.assertIn("try{if(n)n.focus();if(!n||document.activeElement!==n)tip.focus();}catch(e){}", self.JS)

    def test_a_failed_send_restores_the_button_and_names_the_reason_under_it(self):
        press = self.JS[self.JS.index("if(act==='pause')"):self.JS.index("else if(act==='reveal')")]
        self.assertIn("{pending=null;hint=NOTSENT;dirty=true;}", press)
        self.assertIn("(hint?'<div class=ah-hint>'+esc(hint)+'</div>':'')", self.JS)
        self.assertIn("LAST=m;hint='';", self.JS, "a frame means the socket is alive: the notice retires")

    def test_a_press_the_socket_lost_clears_on_the_close_and_says_why(self):
        # the redial's ready re-sends the last frame verbatim, so a press the kernel never received would keep
        # the button disabled and relabeled across the reconnect until an unrelated pause write moved the seq;
        # the shell's onclose tells the detail, and the re-sent frame repaints the truth either way
        self.assertIn("window.__rompApiSocketLost=function(){if(pending===null)return;pending=null;pendSeq=null;hint=LOST;", self.JS)
        self.assertIn("var LOST='Connection lost before the answer arrived. When it is back, the button shows the current state.';", self.JS)
        self.assertIn("hint=LOST;\nif(tip.style.display!=='block')return;if(held){dirty=true;return;}render();};", self.JS,
                      "painted on release under a held pointer, like a frame")
        html = km._landing()
        self.assertIn("ws.onclose=function(){try{window.__rompApiSocketLost&&window.__rompApiSocketLost();}catch(e){}"
                      "if(shellSock===ws)shellSock=null;setTimeout(shellWS,2000);};", html,
                      "the shell socket's close tells the detail before the redial")

    def test_focus_moves_to_the_card_before_the_pressed_button_is_disabled(self):
        # a disabled element cannot hold focus: left on the button, focus fell to BODY, where the card's Tab trap
        # no longer saw the keys and a Shift+Tab left the aria-modal dialog
        press = self.JS[self.JS.index("if(act==='pause')"):self.JS.index("else if(act==='reveal')")]
        self.assertIn("try{tip.focus();}catch(e){}", press)
        self.assertLess(press.index("try{tip.focus();}catch(e){}"), press.index("t.disabled=true"), "focus first, then the disable")

    def test_the_hover_re_anchors_after_a_re_render(self):
        self.assertIn("tip.innerHTML=html(LAST,pinned);if(!pinned)anchor();", self.JS)
        # measured after a reset (left 0, the height cap), then placed: a fixed element's shrink-to-fit width is
        # taken against where it last sat, and the History rows make the tip wider than the frame's reading
        self.assertIn("var w=tip.offsetWidth,h=tip.offsetHeight;\ntip.style.left=Math.max(6,Math.min(window.innerWidth-w-6,x-w/2))+'px';\ntip.style.top=Math.max(6,r.top-h-8)+'px';}", self.JS)
        self.assertIn("lastX=(ev&&typeof ev.clientX==='number')?ev.clientX:null;", self.JS)

    def test_the_cell_is_a_keyboard_button_and_the_detail_takes_and_returns_focus(self):
        self.assertIn("el.addEventListener('keydown',function(ev){if(ev.key==='Escape')", self.JS)
        self.assertIn("if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();ev.stopPropagation();if(pinned)close();else open();}});", self.JS)
        self.assertIn("el.setAttribute('aria-label',lab)", self.JS)   # T301: the merged description ("API health: fine|errors|no traffic")
        self.assertIn("tip.setAttribute('role','dialog')", self.JS)
        self.assertIn("focusBack=document.activeElement;", self.JS)
        self.assertIn("try{tip.focus();}catch(e){}", self.JS)
        self.assertIn("(fb&&fb.focus?fb:el).focus()", self.JS)

    def test_each_modal_closes_the_other_first_so_the_shared_backdrop_serves_one(self):
        self.assertIn("function open(){if(!LAST)return;try{window.__rompUsageClose&&window.__rompUsageClose();}catch(e){}", self.JS)
        self.assertIn("try{window.__rompApiClose&&window.__rompApiClose();}catch(e){}", km._LANDING_USAGE_JS)

    def test_actions_are_delegated_on_the_stable_tip_node(self):
        self.assertIn("var el=document.getElementById('rail-api');", self.JS)
        self.assertIn("el.addEventListener('click',function(ev){if(ev&&ev.stopPropagation)ev.stopPropagation();if(pinned)close();else open();});", self.JS)
        self.assertNotIn("el.innerHTML", self.JS, "the frame handler writes the cell's children, never the cell")
        self.assertIn("tip.addEventListener('click',function(ev){", self.JS)
        self.assertEqual(self.JS.count("addEventListener('click'"), 2, "one on #rail-api, one on #ah-tip; none per row")
        self.assertEqual(self.JS.count("addEventListener('keydown'"), 2, "one on #rail-api, one on #ah-tip; none per row")
        self.assertEqual(self.JS.count("function run(t){var act=t.getAttribute('data-act');"), 1, "one action switch for click and key")
        self.assertNotIn(".onclick=function", self.JS, "no per-row inline handlers")

    def test_the_amber_state_is_never_called_blocked(self):
        self.assertNotIn("'blocked'", self.JS)
        self.assertIn("(r.kind==='retrying'?'retrying':'stopped')", self.JS, "the amber state is never called blocked")

    def test_the_footer_reaches_the_usage_modal_and_the_log(self):
        self.assertIn("data-act=usage>Usage and spend<", self.JS)
        self.assertIn("data-act=log>Log<", self.JS)
        self.assertIn("window.__rompUsagePanel&&window.__rompUsagePanel()", self.JS)
        self.assertIn("window.__rompOpenErrs&&window.__rompOpenErrs()", self.JS)

    def test_the_pinned_detail_wears_the_usage_modal_s_backdrop_and_escape_hook(self):
        self.assertIn("tip.classList.add('ru-modal')", self.JS)
        self.assertIn("back.classList.add('on')", self.JS)
        self.assertIn("window.__rompApiClose=close;back.onclick=close;", self.JS)
        self.assertIn("window.__rompApiClose=null;", self.JS)


if __name__ == "__main__":
    unittest.main()
