#!/usr/bin/env python3
"""The parked-op drain does its per-cycle work in cost order (2026-09-05).

Since #904 the drain runs inside the pusher loop, every 0.5 s. An account limit parks every session's
input for hours, and the drain then re-read usage.json (with its colormap ramps) and the retry-pause file
once PER HELD SESSION per cycle, and re-parsed a held tmux session's moved transcript for a busy() verdict
nothing read — 46 ms of a 0.5 s cycle on a seventeen-session board, measured on a replay. Now usage and
the pause are read once per drain and ride the cycle's scope for _limit_hold; each sid's gates run cheapest
first; and the parked-parse refresh runs only for a sid the holds let through — plus, first, for a sid whose
until-busy hold is about to read busy(). Synthetic sids; a fake backend; the limit-queue test's fixture
shape."""
import inspect
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_drainhoists", os.path.join(BIN, "romp-kernel"))

SIDS = ["11111111-2222-3333-4444-aaaaaaaaaa%02d" % i for i in range(1, 6)]


class _FakeBackend:
    corroborates_with_transcript = False
    def __init__(self): self.sent = []
    def send(self, sid, text, **kw): self.sent.append((sid, text)); return True
    def owns(self, sid): return True
    def busy(self, sid): return False
    def turn_seq(self, sid): return 0


class DrainHoists(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        self.be = _FakeBackend()
        self._saved = {n: getattr(km, n) for n in
                       ("_compacting_now", "_working_now", "_push_all", "_optimistic_echo", "_mark_views_dirty",
                        "_mark_compacting", "_mark_model_pending", "_path_of", "_usage", "_retry_paused_on",
                        "_retry_pause_reason", "_refresh_parked_parse", "_deliver_send_batch")}
        self._saved_backend = km.Sessions.backend_for
        km._compacting_now = lambda sid: False
        km._working_now = lambda sid: False
        km._push_all = lambda *a, **k: None
        km._optimistic_echo = lambda sid, text, author="human": None
        km._mark_views_dirty = lambda *a, **k: None
        km._mark_compacting = lambda sid: None
        km._mark_model_pending = lambda sid, v: None
        km._path_of = lambda sid, now=None: None
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.usage_calls = []
        self.capped = False
        def usage():
            self.usage_calls.append(1)
            return {"limited": {"fiveHour": True}, "fiveHour": {"resetsAt": 4102444800}} if self.capped else {"limited": None}
        km._usage = usage
        self.pause_calls = []
        km._retry_paused_on = lambda: (self.pause_calls.append(1), False)[1]
        km._retry_pause_reason = lambda: ""
        self.refreshed = []
        km._refresh_parked_parse = lambda sid, now: self.refreshed.append(sid)
        self.delivered = []                                       # what the (stubbed) send batch was handed
        km._deliver_send_batch = lambda be, sid, ops: (self.delivered.append((sid, [o[1] for o in ops])), ops.clear(), True)[2]
        km._pending_ops.clear(); km._drain_hold.clear(); km._moving.clear()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._saved_backend
        km._pending_ops.clear(); km._drain_hold.clear(); km._moving.clear()
        jd.STATE = self.saved_state

    def test_a_usage_and_the_pause_are_read_once_per_drain_however_many_sessions_are_held(self):
        self.capped = True
        for s in SIDS:
            km._pending_ops[s] = [("send", "queued while limited")]
        km._apply_pending_ops(1000)
        self.assertEqual(len(self.usage_calls), 1, "one usage.json reading for the whole drain")
        self.assertEqual(len(self.pause_calls), 1, "one retry-pause reading for the whole drain")
        self.assertEqual(sorted(km._pending_ops), sorted(SIDS), "every session stays held")
        self.assertEqual(self.refreshed, [], "a held session's transcript is not re-parsed for a verdict nothing reads")
        self.assertEqual(self.delivered, [])
        self.assertIs(getattr(km._live_scope, "usage", km._UNSET), km._UNSET, "the drain clears its scope on the way out")
        self.assertIsNone(getattr(km._live_scope, "spend_pause", None))

    def test_b_a_hoisted_reading_gives_the_same_verdict_as_a_fresh_one(self):
        for capped in (False, True):
            self.capped = capped
            fresh = km._limit_hold(SIDS[0])
            km._live_scope.usage = km._usage(); km._live_scope.spend_pause = False
            try:
                hoisted = km._limit_hold(SIDS[0])
            finally:
                km._live_scope.usage = km._UNSET; km._live_scope.spend_pause = None
            self.assertEqual(fresh, hoisted, "capped=%s" % capped)
        km._live_scope.usage = {"limited": None}; km._live_scope.spend_pause = True
        try:
            self.assertEqual(km._limit_hold(SIDS[0])["reason"], "spend")
        finally:
            km._live_scope.usage = km._UNSET; km._live_scope.spend_pause = None
        # an unreadable usage.json with the spend pause ON: the fresh read answers "no hold" (never invent one);
        # the hoisted reading must say the same, not read as an EMPTY usage that falls through to the spend arm
        # (review find 2026-09-05 — the WS thread's fresh _ops_gate and the drain would otherwise disagree)
        def boom(): raise ValueError("usage.json is not an object")
        km._usage = boom
        km._retry_paused_on = lambda: True; km._retry_pause_reason = lambda: "spend"
        self.assertIsNone(km._limit_hold(SIDS[0]), "fresh: unreadable usage is no hold, whatever the pause says")
        km._live_scope.usage = km._UNREADABLE; km._live_scope.spend_pause = True
        try:
            self.assertIsNone(km._limit_hold(SIDS[0]), "hoisted: the same verdict")
        finally:
            km._live_scope.usage = km._UNSET; km._live_scope.spend_pause = None
        km._pending_ops[SIDS[0]] = [("send", "go")]
        km._apply_pending_ops(1000)
        self.assertEqual(self.delivered, [(SIDS[0], ["go"])], "the drain fires it, as the per-sid read always did")

    def test_c_the_parse_refresh_runs_only_for_a_session_the_holds_let_through(self):
        self.capped = False
        km._pending_ops[SIDS[0]] = [("send", "go")]
        km._pending_ops[SIDS[1]] = [("send", "later")]
        km._moving.add(SIDS[1])                                   # a move in flight: skipped before any read
        km._apply_pending_ops(1000)
        self.assertEqual(self.refreshed, [SIDS[0]], "the refresh ran for the deliverable sid only")
        self.assertEqual(self.delivered, [(SIDS[0], ["go"])], "…and its send was delivered")
        self.assertNotIn(SIDS[0], km._pending_ops)

    def test_d_the_drain_still_answers_a_bare_call(self):
        km._pending_ops[SIDS[0]] = [("send", "go")]
        km._apply_pending_ops()                                   # tests and older callers pass nothing
        self.assertNotIn(SIDS[0], km._pending_ops)

    def test_e_the_pusher_runs_no_whole_set_refresh_ahead_of_the_drain(self):
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_apply_pending_ops()", src)
        self.assertNotIn("_refresh_parked_parses", src)
        self.assertFalse(hasattr(km, "_refresh_parked_parses"), "the whole-set pre-pass is gone, not parked")
        drain = inspect.getsource(km._apply_pending_ops)
        self.assertIn("_live_scope.usage = _usage()", drain)
        self.assertIn("_live_scope.usage = _UNSET", drain, "the scope is cleared even when the loop raises")
        held = drain.index("_limit_hold(sid)")
        refresh = drain.index("_refresh_parked_parse(sid, now)", held)
        self.assertLess(held, refresh, "the account hold is checked before the parse")
        self.assertLess(refresh, drain.index("_compacting_now(sid) or _working_now(sid)"),
                        "…and the parse before the gates that read it")
        self.assertLess(drain.index("_refresh_parked_parse(sid, now)"), drain.index("_drain_hold_open(sid, hold)"),
                        "an until-busy hold's busy() read sees a current parse")

    def test_f_the_scope_is_cleared_when_the_loop_raises(self):
        km._pending_ops[SIDS[0]] = [("send", "go")]
        def boom(sid, now): raise RuntimeError("parse refresh blew up")
        km._refresh_parked_parse = boom
        with self.assertRaises(RuntimeError):
            km._apply_pending_ops(1000)
        self.assertIs(getattr(km._live_scope, "usage", km._UNSET), km._UNSET)
        self.assertIsNone(getattr(km._live_scope, "spend_pause", None))
        self.assertIsNone(km._limit_hold(SIDS[0]), "…so the next fresh caller reads usage.json, not a stale scope")

    def test_g_an_until_busy_hold_reads_busy_against_a_current_parse(self):
        # the hold keyed on the tmux row's flip reads busy(), which a tmux backend corroborates against the
        # cached parse; before 2026-09-05 the whole-set pre-pass kept that cache current, so the drain must
        # refresh such a sid BEFORE the hold's read — once, not again below (review find 2026-09-05)
        km._pending_ops[SIDS[0]] = [("send", "behind the hold")]
        km._drain_hold[SIDS[0]] = (time.monotonic() + 1000, True)   # until_busy, fallback far away
        km._pending_ops[SIDS[1]] = [("send", "behind a clock")]
        km._drain_hold[SIDS[1]] = (time.monotonic() + 1000, False)  # a clock hold reads nothing
        km._apply_pending_ops(1000)
        self.assertEqual(self.refreshed, [SIDS[0]], "refreshed once, for the busy-reading hold only")
        self.assertEqual(sorted(km._pending_ops), sorted(SIDS[:2]), "both still held (busy() read False, deadlines far)")
        self.assertEqual(self.delivered, [])
        # the same sid with a hold whose window has closed on the clock: refreshed once for the read, once more
        # below would be a second parse of an unmoved file — the refresh is idempotent by its cache check, and
        # here the stub just counts: the drain calls it for the hold's read and again ahead of the gates
        km._drain_hold[SIDS[0]] = (time.monotonic() - 1, True)
        self.be.busy = lambda sid: False
        self.refreshed.clear()
        km._apply_pending_ops(1000)
        self.assertNotIn(SIDS[0], km._pending_ops, "the fallback deadline passed: delivered")
        self.assertEqual(self.refreshed, [SIDS[0], SIDS[0]])

    def test_h_the_refresh_and_the_gates_run_outside_the_queue_lock_and_a_yielded_cycle_reads_nothing(self):
        # the lock guards the queue's reads and pops only; a transcript parse (seconds on a large file) must
        # never run under it — a first rebase of this change put the refresh inside a locked walk and every
        # handler's park and cancel stalled behind it (rebase review find 2026-09-05)
        owned = []
        km._refresh_parked_parse = lambda sid, now: owned.append((sid, km._pending_ops_lock._is_owned()))
        km._pending_ops[SIDS[0]] = [("send", "go")]                       # a deliverable sid
        km._pending_ops[SIDS[1]] = [("send", "behind the hold")]
        km._drain_hold[SIDS[1]] = (time.monotonic() + 1000, True)          # an until-busy hold: its busy() read
        km._apply_pending_ops(1000)
        self.assertEqual(sorted(owned), sorted([(SIDS[0], False), (SIDS[1], False)]),
                         "both refreshes ran, neither with the lock held")
        self.assertFalse(km._pending_ops_lock._is_owned(), "…and the lock is not held on the way out")
        # a handler (another thread — the lock is re-entrant) holding the lock: the drain yields the cycle
        # before it reads anything — no usage read, no parse, no scope left behind
        owned.clear(); self.usage_calls.clear()
        km._pending_ops.pop(SIDS[1], None); km._drain_hold.pop(SIDS[1], None)
        km._pending_ops[SIDS[0]] = [("send", "again")]
        held, release = threading.Event(), threading.Event()
        def handler():
            with km._pending_ops_lock:
                held.set()
                release.wait(5)
        th = threading.Thread(target=handler, daemon=True); th.start()
        self.assertTrue(held.wait(5))
        try:
            km._apply_pending_ops(1000)
            self.assertIn(SIDS[0], km._pending_ops, "the walk yielded to the handler")
        finally:
            release.set(); th.join(5)
        self.assertEqual(owned, [], "a yielded cycle parses nothing")
        self.assertEqual(self.usage_calls, [], "…and reads no usage")
        self.assertIs(getattr(km._live_scope, "usage", km._UNSET), km._UNSET)


if __name__ == "__main__":
    unittest.main()
