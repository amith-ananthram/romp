#!/usr/bin/env python3
"""_api_error reads a TAIL WINDOW and widens until it saw the deciding record, instead of re-reading the
whole transcript on every append.

Why it mattered: the pusher calls _api_error per session per push, and its (mtime,size) cache is busted by
every append — so a working session's whole transcript was re-read and re-json-decoded per push cycle. On a
live 11-session install with 20-116MB transcripts that was ~two thirds of the kernel's entire CPU (py-spy: 88%
of kernel CPU in Thread-5 (_pusher), 9 of 12 samples inside _api_error / raw_decode / read_text), which
starved the pusher and left every pane visibly stale. This is the same unamortized whole-file shape the
assembly fold retired for the event-model parse; _api_error was left behind on the same hot path.

The CLASSIFICATION is unchanged by construction — `git diff -w` on the commit shows no loop-body line
removed, only relocated scaffolding plus three `decided = True` markers. So these tests pin the part that IS
new: that the WINDOWED read returns exactly what the whole-file read returns, for every transcript shape,
including the ones that force a widen. Synthetic only — invented text, placeholder uuids, no real session
data."""
import json
import os
import tempfile
import unittest
from romp_load import load_source

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate: importing the kernel must not touch live state
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = load_source("romp_kernel_apierr_tail", os.path.join(BIN, "romp-kernel"))

U_PROMPT = "11111111-2222-3333-4444-000000000001"     # the refused/failed user message
U_OTHER = "11111111-2222-3333-4444-000000000002"


def _err_rec(parent=U_PROMPT, text="API Error: 500 server_error"):
    return {"type": "assistant", "uuid": "aaaaaaaa-0000-0000-0000-000000000001",
            "parentUuid": parent, "isApiErrorMessage": True, "apiErrorStatus": 500,
            "error": "server_error", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _out_rec():
    return {"type": "assistant", "uuid": "aaaaaaaa-0000-0000-0000-000000000002",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "back on track"}]}}


def _prompt_rec():
    return {"type": "user", "uuid": U_OTHER,
            "message": {"role": "user", "content": [{"type": "text", "text": "try that again"}]}}


def _tool_result_rec(i=0):
    return {"type": "user", "uuid": "bbbbbbbb-0000-0000-0000-%012d" % i,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_%d" % i,
                                                     "content": "ok"}]}}


def _refusal_rec(parent=U_PROMPT):
    return {"type": "system", "subtype": "model_refusal_no_fallback", "parentUuid": parent,
            "uuid": "cccccccc-0000-0000-0000-000000000001"}


def _filler(i=0):
    # a record kind _api_error ignores entirely (neither sets nor clears) — the padding that forces a widen
    return {"type": "queue-operation", "uuid": "dddddddd-0000-0000-0000-%012d" % i, "op": "noop"}


class ApiErrorTailWindow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.n = 0
        km._api_err_cache.clear()

    def _write(self, recs):
        """A fresh path per write, so the (mtime,size) cache can never mask a difference."""
        self.n += 1
        p = os.path.join(self.dir, "transcript-%d.jsonl" % self.n)
        with open(p, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return p

    def _both(self, recs, window=64):
        """(windowed answer, whole-file answer) for the same file — the differential this patch must hold."""
        p = self._write(recs)
        saved = km._API_ERR_TAIL_WINDOW
        try:
            km._API_ERR_TAIL_WINDOW = window
            km._api_err_cache.clear()
            windowed = km._api_error(p)
        finally:
            km._API_ERR_TAIL_WINDOW = saved
        whole, decided = km._api_error_scan(p, 0)
        return windowed, whole

    def _assert_agrees(self, recs, msg, window=64):
        w, f = self._both(recs, window)
        self.assertEqual(w, f, "windowed read disagrees with the whole-file read: " + msg)
        return w

    # ── the verdict itself ──
    def test_api_error_last_is_reported(self):
        err = self._assert_agrees([_prompt_rec(), _err_rec()], "error is the last deciding record")
        self.assertIsNotNone(err)
        self.assertEqual(err["status"], 500)

    def test_a_genuine_prompt_after_the_error_clears_it(self):
        self.assertIsNone(self._assert_agrees([_err_rec(), _prompt_rec()], "a retry clears the block"))

    def test_fresh_assistant_output_after_the_error_clears_it(self):
        self.assertIsNone(self._assert_agrees([_err_rec(), _out_rec()], "output means it recovered"))

    def test_tool_results_after_the_error_do_not_clear_it(self):
        recs = [_err_rec()] + [_tool_result_rec(i) for i in range(5)]
        self.assertIsNotNone(self._assert_agrees(recs, "a tool_result is not a genuine prompt"))

    def test_a_matching_refusal_record_marks_the_error(self):
        err = self._assert_agrees([_err_rec(), _filler(1), _refusal_rec()], "refusal rides the episode")
        self.assertTrue(err["refusal"], "the model_refusal_* record with the same parentUuid marks it")

    def test_a_nonmatching_refusal_record_does_not_mark_it(self):
        err = self._assert_agrees([_err_rec(), _refusal_rec(parent=U_OTHER)], "different episode")
        self.assertFalse(err["refusal"], "a refusal for another episode must not mark this error")

    # ── the widening itself: THE regression this patch is about ──
    def test_a_window_that_misses_the_error_widens_instead_of_answering_none(self):
        # the error sits far from EOF behind padding that neither sets nor clears the verdict, so the first
        # window sees no deciding record and MUST widen. Answering from the short window would report the
        # session as unblocked while it is blocked.
        recs = [_err_rec()] + [_filler(i) for i in range(400)]
        err = self._assert_agrees(recs, "widen past non-deciding padding", window=64)
        self.assertIsNotNone(err, "the error is still the last deciding record, however far back it sits")

    def test_the_whole_file_pass_runs_when_the_window_reaches_the_start(self):
        # a transcript with NO deciding record at all: every widen fails until start hits 0, and the
        # whole-file pass must actually execute (an early version nested the loop inside the seek guard,
        # so the start==0 pass silently scanned nothing).
        recs = [_filler(i) for i in range(200)]
        p = self._write(recs)
        saved = km._API_ERR_TAIL_WINDOW
        try:
            km._API_ERR_TAIL_WINDOW = 64
            km._api_err_cache.clear()
            self.assertIsNone(km._api_error(p), "no deciding record anywhere → None")
        finally:
            km._API_ERR_TAIL_WINDOW = saved
        # and the whole-file pass reports it saw nothing, rather than falsely claiming a verdict
        err, decided = km._api_error_scan(p, 0)
        self.assertIsNone(err)
        self.assertFalse(decided, "no assignment happened, so the pass must not claim it decided")

    def test_a_partial_first_line_is_dropped_not_misparsed(self):
        # a non-zero start lands mid-line; the half line must be discarded without disturbing the verdict
        recs = [_err_rec(), _filler(1)]
        p = self._write(recs)
        size = os.path.getsize(p)
        err, decided = km._api_error_scan(p, size // 2)   # guaranteed mid-line
        self.assertFalse(decided, "the deciding record is before this start → nothing assigned")
        self.assertIsNone(err)

    def test_decided_is_true_exactly_when_a_verdict_was_assigned(self):
        p = self._write([_err_rec()])
        self.assertTrue(km._api_error_scan(p, 0)[1])
        p2 = self._write([_filler(1)])
        self.assertFalse(km._api_error_scan(p2, 0)[1])

    # ── the READ itself is partial: the property every differential above is blind to ──
    def _windowed_starts(self, recs, window):
        """Run _api_error with `window` and return (the byte offset each scan pass started at, the answer,
        the path). The offsets are the only evidence that the read is partial: every differential assertion
        above also holds against a scan that always starts at byte 0, i.e. with the speedup silently gone."""
        p = self._write(recs)
        starts = []
        real = km._api_error_pass                       # the ONE pass both transcript readers share

        def recording(path, start):
            starts.append(start)
            if len(starts) > 64:
                raise AssertionError("the widen loop is not terminating: %r" % starts[:8])
            return real(path, start)
        saved = km._API_ERR_TAIL_WINDOW
        km._api_error_pass = recording
        try:
            km._API_ERR_TAIL_WINDOW = window
            km._api_err_cache.clear()
            km._api_last_failed_cache.clear()
            got = km._api_error(p)
        finally:
            km._api_error_pass = real
            km._API_ERR_TAIL_WINDOW = saved
        return starts, got, p

    def test_a_decided_tail_is_read_once_from_a_nonzero_offset(self):
        recs = [_filler(i) for i in range(200)] + [_err_rec()]
        starts, got, p = self._windowed_starts(recs, window=1024)
        self.assertEqual(len(starts), 1, "one pass must settle a decided tail: %r" % starts)
        self.assertGreater(starts[0], 0, "the pass must start inside the file, never at byte 0")
        self.assertIsNotNone(got)
        self.assertEqual(got, km._api_error_scan(p, 0)[0])

    def test_widening_reads_strictly_earlier_offsets_until_the_deciding_record(self):
        recs = [_err_rec()] + [_filler(i) for i in range(400)]
        starts, got, p = self._windowed_starts(recs, window=64)
        self.assertGreater(len(starts), 1, "the deciding record sits far from EOF, so the window must widen")
        self.assertEqual(starts, sorted(set(starts), reverse=True), "each widen must start strictly earlier")
        self.assertIsNotNone(got)
        self.assertEqual(got, km._api_error_scan(p, 0)[0])

    def test_a_verdict_from_a_mid_file_window_equals_the_whole_file(self):
        # the window covers just the last three records: a non-None answer, refusal included, from a start > 0
        recs = [_prompt_rec() for _ in range(50)] + [_err_rec(), _filler(1), _refusal_rec()]
        tail = sum(len(json.dumps(r)) + 1 for r in recs[-3:])
        starts, got, p = self._windowed_starts(recs, window=tail + 8)
        self.assertEqual(len(starts), 1, starts)
        self.assertGreater(starts[0], 0)
        self.assertIsNotNone(got)
        self.assertTrue(got["refusal"], "the refusal record inside the window still marks the episode")
        self.assertEqual(got, km._api_error_scan(p, 0)[0])

    def test_a_non_positive_window_knob_still_terminates_and_answers(self):
        # ROMP_API_ERR_TAIL_WINDOW set to 0 or less must not spin the pusher forever (0*4 is 0): the driver
        # clamps to one byte and widens from there. The recorder above turns a spin into a failure, not a hang.
        for bad in (0, -7):
            starts, got, p = self._windowed_starts([_prompt_rec(), _err_rec()], window=bad)
            self.assertGreater(len(starts), 1, "window %d must widen, not spin in place" % bad)
            self.assertIsNotNone(got, "window %d" % bad)
            self.assertEqual(got, km._api_error_scan(p, 0)[0])

    # ── unchanged edges ──
    def test_missing_file_is_none(self):
        self.assertIsNone(km._api_error(os.path.join(self.dir, "nope.jsonl")))

    def test_empty_file_is_none(self):
        self.assertIsNone(self._assert_agrees([], "empty transcript"))

    def test_the_cache_still_serves_an_unchanged_transcript(self):
        p = self._write([_err_rec()])
        first = km._api_error(p)
        self.assertIsNotNone(first)
        self.assertIn(p, km._api_err_cache, "an unchanged (mtime,size) must stay cached")
        self.assertEqual(km._api_error(p), first)


# ── _api_last_failed: the LATCH behind the bottom bar's API health cell ──────────────────────────────
from datetime import datetime, timezone

T_BASE = 1_700_000_000


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _ts_err_rec(t, status=500, category="server_error", text="API Error: 500 server_error"):
    o = {"type": "assistant", "timestamp": _iso(t), "uuid": "aaaaaaaa-0000-0000-0000-00000000%04d" % (t - T_BASE),
         "parentUuid": U_PROMPT, "isApiErrorMessage": True, "error": category,
         "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if status is not None:
        o["apiErrorStatus"] = status
    return o


def _ts_prompt_rec(t, text="try that again"):
    return {"type": "user", "timestamp": _iso(t), "uuid": U_OTHER,
            "message": {"role": "user", "content": text}}


def _ts_out_rec(t):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": "aaaaaaaa-0000-0000-0000-0000000000ff",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "back on track"}]}}


def _ts_tool_result_rec(t, i=0):
    return {"type": "user", "timestamp": _iso(t), "uuid": "bbbbbbbb-0000-0000-0000-%012d" % i,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_%d" % i,
                                                     "content": "ok"}]}}


class ApiLastFailedLatch(unittest.TestCase):
    """_api_last_failed is _api_error's latched sibling: an isApiErrorMessage record holds until ASSISTANT
    OUTPUT follows it, through romp's own injected RETRY_MSG and through a human prompt alike, both of which
    clear _api_error (a retry rightly un-blocks the session; it says nothing about the API). Same tail-first
    widening, its own (mtime, size) cache; the scan's default keyword leaves _api_error's verdicts untouched."""

    def setUp(self):
        km._api_err_cache.clear()
        km._api_last_failed_cache.clear()
        self.td = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.td.name, "t.jsonl")
        self._win = km._API_ERR_TAIL_WINDOW

    def tearDown(self):
        km._API_ERR_TAIL_WINDOW = self._win
        km._api_err_cache.clear()
        km._api_last_failed_cache.clear()
        self.td.cleanup()

    def _write(self, *recs):
        with open(self.p, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

    def _append(self, *recs):
        with open(self.p, "a") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

    def test_romp_s_own_retry_prompt_clears_api_error_but_not_the_latch(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5, status=529, category="overloaded"),
                    _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG))
        self.assertIsNone(km._api_error(self.p), "the retry un-blocks the session")
        e = km._api_last_failed(self.p)
        self.assertIsNotNone(e, "the API has not answered yet: the record holds")
        self.assertEqual((e["status"], e["category"], e["t"]), (529, "overloaded", T_BASE + 5))

    def test_a_human_prompt_after_the_error_splits_the_same_way(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5), _ts_prompt_rec(T_BASE + 60, "please continue"))
        self.assertIsNone(km._api_error(self.p))
        e = km._api_last_failed(self.p)
        self.assertEqual((e["status"], e["t"]), (500, T_BASE + 5))

    def test_fresh_assistant_output_clears_both(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5), _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG),
                    _ts_out_rec(T_BASE + 20))
        self.assertIsNone(km._api_error(self.p))
        self.assertIsNone(km._api_last_failed(self.p), "the API answered: the one event that clears the latch")

    def test_a_tool_result_record_clears_neither(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5), _ts_tool_result_rec(T_BASE + 6))
        self.assertIsNotNone(km._api_error(self.p))
        self.assertIsNotNone(km._api_last_failed(self.p))

    def test_a_newer_error_record_replaces_the_latched_one(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5, status=529, category="overloaded"),
                    _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG), _ts_err_rec(T_BASE + 12, status=429, category="rate_limit"))
        e = km._api_last_failed(self.p)
        self.assertEqual((e["status"], e["t"]), (429, T_BASE + 12), "a new failed attempt is new information")

    def test_the_on_you_flags_ride_the_latched_record(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5, status=400, category="invalid_request",
                                                          text="API Error: 400 prompt is too long"),
                    _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG))
        self.assertTrue(km._api_last_failed(self.p)["tooLong"], "the frame excludes it by this flag")

    def test_a_refusal_record_marks_the_latched_episode_too(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5, status=400, category="invalid_request"),
                    _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG), _filler(1), _refusal_rec())
        self.assertIsNone(km._api_error(self.p), "the prompt cleared the blocking verdict before the refusal record")
        self.assertTrue(km._api_last_failed(self.p)["refusal"], "the latched record is the one the refusal annotates")

    def test_the_cache_serves_an_unchanged_transcript_and_busts_on_a_size_change(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5))
        e = km._api_last_failed(self.p)
        self.assertEqual(e["status"], 500)
        scan = km._api_error_pass
        km._api_error_pass = lambda *a, **k: self.fail("an unchanged transcript is served from the cache")
        try:
            self.assertIs(km._api_last_failed(self.p), e)
        finally:
            km._api_error_pass = scan
        self._append(_ts_out_rec(T_BASE + 20))                # the size moved: a fresh scan, a fresh answer
        self.assertIsNone(km._api_last_failed(self.p))
        self.assertIsNone(km._api_error(self.p), "the sibling reads the same fresh answer")

    def test_the_two_caches_are_independent(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5), _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG))
        self.assertIsNone(km._api_error(self.p))
        self.assertIsNotNone(km._api_last_failed(self.p), "a cached None from _api_error is not this answer")
        self.assertIn(self.p, km._api_err_cache)
        self.assertIn(self.p, km._api_last_failed_cache)

    def test_the_latch_widens_past_a_tail_of_prompts(self):
        km._API_ERR_TAIL_WINDOW = 64                       # a window that ends inside the prompts
        pad = "x" * 200
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5, status=529, category="overloaded"),
                    *[_ts_prompt_rec(T_BASE + 10 + i, pad) for i in range(6)])
        self.assertIsNone(km._api_error(self.p), "the default scan decides at the newest prompt")
        e = km._api_last_failed(self.p)
        self.assertEqual((e["status"], e["t"]), (529, T_BASE + 5), "the latch widened back to the assistant record")

    def test_the_scan_stamps_the_record_time_and_reads_zero_without_one(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5))
        err, decided = km._api_error_scan(self.p, 0)
        self.assertEqual(err["t"], T_BASE + 5)
        self._write(_prompt_rec(), _err_rec())             # the module's stamp-less records
        err, _ = km._api_error_scan(self.p, 0)
        self.assertEqual(err["t"], 0)

    def test_the_default_keyword_is_the_old_scan(self):
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5), _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG))
        self.assertEqual(km._api_error_scan(self.p, 0), km._api_error_scan(self.p, 0, user_clears=True))
        self.assertEqual(km._api_error_scan(self.p, 0), (None, True))
        err, decided = km._api_error_scan(self.p, 0, user_clears=False)
        self.assertEqual((err["status"], decided), (500, True))

    def test_missing_file_is_none(self):
        self.assertIsNone(km._api_last_failed(os.path.join(self.td.name, "absent.jsonl")))

    # ── one pass answers both readers ──
    def _recording(self, passes):
        real = km._api_error_pass
        km._api_error_pass = lambda path, start: passes.append(start) or real(path, start)
        return real

    def test_one_cycle_reads_the_tail_once_for_both_verdicts(self):
        # Every pusher cycle asks _api_error (the spend-cap check, the feed build) and then _api_last_failed
        # (the health frame) about the same transcript: one pass over the tail must answer both, in either
        # order, or a streaming session pays two tail reads and two json parses per cycle on the one hot thread.
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5, status=529, category="overloaded"),
                    _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG))
        passes = []
        real = self._recording(passes)
        try:
            self.assertIsNone(km._api_error(self.p))
            self.assertEqual(km._api_last_failed(self.p)["status"], 529)
            self.assertEqual(len(passes), 1, "the first read decided both verdicts: %r" % passes)
            km._api_err_cache.clear()
            km._api_last_failed_cache.clear()
            del passes[:]
            self.assertEqual(km._api_last_failed(self.p)["status"], 529)
            self.assertIsNone(km._api_error(self.p))
            self.assertEqual(len(passes), 1, "the other order too: %r" % passes)
        finally:
            km._api_error_pass = real

    def test_a_read_that_did_not_reach_an_assistant_record_leaves_the_latch_to_its_own_widening(self):
        # _api_error's window may decide at a prompt without seeing an assistant record; then it must NOT
        # cache a latch verdict it never saw, and the latch's own read widens back to the assistant record
        km._API_ERR_TAIL_WINDOW = 64
        pad = "x" * 200
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5, status=529, category="overloaded"),
                    *[_ts_prompt_rec(T_BASE + 10 + i, pad) for i in range(6)])
        passes = []
        real = self._recording(passes)
        try:
            self.assertIsNone(km._api_error(self.p))
            self.assertNotIn(self.p, km._api_last_failed_cache, "no assistant record seen: no latch verdict cached")
            n = len(passes)
            self.assertEqual(km._api_last_failed(self.p)["status"], 529)
            self.assertGreater(len(passes), n, "the latch widened on its own")
            self.assertIsNone(km._api_error(self.p), "and _api_error's own cached verdict stands")
        finally:
            km._api_error_pass = real

    def test_the_newest_output_record_s_time_is_kept_beside_the_latch(self):
        # the spend pause's recovery signal (tests/test_retry_pause_autoresume.py): the time of the newest
        # ASSISTANT OUTPUT record when it is the newest assistant record, else 0
        self._write(_ts_prompt_rec(T_BASE), _ts_err_rec(T_BASE + 5), _ts_prompt_rec(T_BASE + 9, km.RETRY_MSG),
                    _ts_out_rec(T_BASE + 20))
        self.assertEqual(km._api_last_output_t(self.p), T_BASE + 20)
        self._append(_ts_prompt_rec(T_BASE + 30, "and now?"))
        self.assertEqual(km._api_last_output_t(self.p), T_BASE + 20, "a prompt is not output")
        self._append(_ts_tool_result_rec(T_BASE + 31))
        self.assertEqual(km._api_last_output_t(self.p), T_BASE + 20, "nor is a tool result")
        self._append(_ts_err_rec(T_BASE + 35))
        self.assertEqual(km._api_last_output_t(self.p), 0, "an error after it: no fresh output stands")
        self._write(_prompt_rec(), _out_rec())
        self.assertEqual(km._api_last_output_t(self.p), 0, "a record without a timestamp reads 0")
        self.assertEqual(km._api_last_output_t(os.path.join(self.td.name, "absent.jsonl")), 0)


if __name__ == "__main__":
    unittest.main()
