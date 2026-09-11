#!/usr/bin/env python3
"""Per-session auto-retry suppression (the user 2026-07-06): during a usage-limit storm the GLOBAL
"stop all auto-retries" is account-wide + flap-prone, and it only stops romp's own 10s "send retry" loop —
not the CLI's INTERNAL api_retry backoff. So the user wanted to just INTERRUPT the one stuck thread: the
interrupt aborts the in-flight CLI retry, and this suppression keeps romp from re-firing "retry" into that
thread until a SUCCESSFUL turn lands, then it re-arms. Mirrors how an interrupt already suppresses
auto-NUDGE (_interrupt_suppresses_nudge). Functional tests on the state machine + source-pins on the wiring.
"""
import contextlib
import errno
import io
import json
import os
import tempfile
import threading
import time
import types
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
SRC = open(os.path.join(BIN, "romp-kernel")).read()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_srs", os.path.join(BIN, "romp-kernel"))


def _human_turn(t):
    """A turn whose user atom is a GENUINE human message at time t (not an interrupt record)."""
    return {"atoms": [{"type": "user", "author": "human", "t": t, "text": "carry on"}]}


class SessionRetrySuppress(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self._orig = {k: getattr(km, k) for k in
                      ("_alive_sessions", "_parse_cached", "_session_chip", "_mark_views_dirty")}
        self._saved_state = km.jd.STATE
        km.jd.STATE = self.dir                          # retry-suppressed.json lives under jd.STATE
        km._retry_suppress_cache.clear()                # the file cache is process-global — reset per test
        km._mark_views_dirty = lambda *a, **k: None     # no clients in the test

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(km, k, v)
        km.jd.STATE = self._saved_state                 # km.jd is the shared judge module, whatever name the kernel loaded under
        self.td.cleanup()

    # --- arming ---
    def test_interrupt_arms_suppression_and_membership_reads_true(self):
        self.assertFalse(km._session_retry_suppressed("s1"))
        km._suppress_session_retry("s1")
        km._retry_suppress_cache.clear()                # force a fresh read of the file we just wrote
        self.assertTrue(km._session_retry_suppressed("s1"), "an interrupt arms this thread's suppression")
        self.assertFalse(km._session_retry_suppressed("s2"), "a DIFFERENT thread is untouched — per-session, not global")

    def test_arming_records_a_floor_timestamp(self):
        km._suppress_session_retry("s1")
        d = json.loads((self.dir / "retry-suppressed.json").read_text())
        self.assertIn("s1", d)
        self.assertGreater(d["s1"], 0, "the stamp is the re-arm floor (only a success AFTER it lifts it)")

    # --- re-arm: a successful re-engagement clears it ---
    def test_reengaged_and_settled_clean_rearms(self):
        km._suppress_session_retry("s1")
        floor = json.loads((self.dir / "retry-suppressed.json").read_text())["s1"]
        km._alive_sessions = lambda now, live_map: [{"sid": "s1", "path": "x"}]
        km._parse_cached = lambda p: {"turns": [_human_turn(floor + 5)]}   # user spoke AFTER the stop
        km._session_chip = lambda *a, **k: "ready"                          # and it settled clean → success
        km._auto_resume_session_retry(int(time.time()), {})
        km._retry_suppress_cache.clear()
        self.assertFalse(km._session_retry_suppressed("s1"),
                         "a successful user turn after the interrupt re-arms auto-retry")

    # --- a FAILED re-engagement (still blocked) stays suppressed ---
    def test_reengaged_but_still_blocked_stays_suppressed(self):
        km._suppress_session_retry("s1")
        floor = json.loads((self.dir / "retry-suppressed.json").read_text())["s1"]
        km._alive_sessions = lambda now, live_map: [{"sid": "s1", "path": "x"}]
        km._parse_cached = lambda p: {"turns": [_human_turn(floor + 5)]}   # user spoke...
        km._session_chip = lambda *a, **k: "blocked"                        # ...but the turn errored again
        km._auto_resume_session_retry(int(time.time()), {})
        km._retry_suppress_cache.clear()
        self.assertTrue(km._session_retry_suppressed("s1"),
                        "a re-engagement that failed with an API error must NOT re-arm — stay hands-off")

    # --- the user hasn't spoken since the interrupt → stays suppressed even if the chip looks clean ---
    def test_no_reengagement_stays_suppressed(self):
        km._suppress_session_retry("s1")
        floor = json.loads((self.dir / "retry-suppressed.json").read_text())["s1"]
        km._alive_sessions = lambda now, live_map: [{"sid": "s1", "path": "x"}]
        km._parse_cached = lambda p: {"turns": [_human_turn(floor - 60)]}  # last human message was BEFORE the stop
        km._session_chip = lambda *a, **k: "ready"
        km._auto_resume_session_retry(int(time.time()), {})
        km._retry_suppress_cache.clear()
        self.assertTrue(km._session_retry_suppressed("s1"),
                        "no message since the interrupt → the user is still at the controls, keep it off")

    def test_noop_when_nothing_suppressed(self):
        called = []
        km._alive_sessions = lambda now, live_map: called.append(1) or []
        km._auto_resume_session_retry(int(time.time()), {})
        self.assertEqual(called, [], "no suppressed sessions → the sweep does no work")


class SessionRetrySuppressWiring(unittest.TestCase):
    """Source-pins: the suppression is actually WIRED into the interrupt handler, the retry gate, the chat
    status, and the pusher — so a refactor that drops any leg fails here."""

    def test_interrupt_handler_arms_suppression(self):
        blk = SRC.split('elif t == "interrupt":', 1)[1].split("elif t ==", 1)[0]
        self.assertIn("_suppress_session_retry(sid)", blk,
                      "interrupting a thread arms its per-session retry-suppression")

    def test_apiretry_gate_checks_per_session_suppression(self):
        fn = SRC.split("def _fire_api_retry(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_session_retry_suppressed(sid)", fn,
                      "the retry decision skips a thread the user interrupted, not just the global pause")

    def test_status_carries_the_flag(self):
        self.assertIn('"retrySuppressed": _session_retry_suppressed(sid)', SRC,
                      "the chat status exposes retrySuppressed so the client retry loop + card can read it")

    def test_pusher_runs_the_per_session_resume_sweep(self):
        # (now, live_map) — the cycle's ONE liveness snapshot, not a per-job fresh read (2026-08-10 CPU fix)
        self.assertIn("_auto_resume_session_retry(now, live_map)", SRC,
                      "the pusher tick re-arms suppressed threads that land a clean turn")

    def test_the_interrupt_handler_toasts_a_stop_that_did_not_land(self):
        blk = SRC.split('elif t == "interrupt":', 1)[1].split("elif t ==", 1)[0]
        self.assertIn("err = _suppress_session_retry(sid)", blk, "the arm's verdict is read, not dropped")
        self.assertIn('"warn"', blk, "…and a refusal takes the rewind ops' warn-toast idiom")

    def test_arm_and_clear_hold_the_ledger_lock_across_their_read_modify_write(self):
        # the interrupt handler (a WS thread) and the pusher's re-arm sweep rewrite the same whole blob;
        # unlocked, the loser's snapshot erased the winner's key
        for fn in ("_suppress_session_retry", "_clear_session_retry_suppress"):
            body = SRC.split("def %s(" % fn, 1)[1].split("\ndef ", 1)[0]
            self.assertIn("with _RETRY_SUPPRESS_LOCK:", body, fn)


SID = "11111111-2222-3333-4444-555555555555"
EIO = OSError(errno.EIO, "Input/output error")


class LedgerFaultsNeverEraseSiblings(unittest.TestCase):
    """The ledger is never rewritten from a fabricated default after a read fault (2026-09-07). The reader
    answered ANY fault with {} and cached it under the file's real stat key; the next interrupt then wrote
    {that sid} over every sibling session's stop — a retry storm the user had explicitly halted, resumed.
    And the write itself had no OSError handling: an ENOSPC out of the interrupt handler reached the WS
    reader loop, which reads any OSError as a socket failure and tore the dashboard's connection down.
    Now only a MISSING file reads as {}; every other fault yields a snapshot tagged UNPROVED that the
    writer refuses (loud once per fault episode), a failed write is reported to the click that asked, and
    a non-numeric floor is read as no floor instead of a TypeError that killed the re-arm sweep. The
    shared reader contract is pinned in tests/test_ledger_unproved_reads.py."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self._saved_state = km.jd.STATE
        km.jd.STATE = self.dir
        self.p = self.dir / "retry-suppressed.json"
        self._orig = {k: getattr(km, k) for k in
                      ("_alive_sessions", "_parse_cached", "_session_chip", "_mark_views_dirty",
                       "_kernel_knows", "_atomic_write")}
        self._orig_backend = km.Sessions.backend_for
        km._mark_views_dirty = lambda *a, **k: None
        self._undo = []
        self._reset()

    def tearDown(self):
        for undo in reversed(self._undo):
            undo()
        for k, v in self._orig.items():
            setattr(km, k, v)
        km.Sessions.backend_for = self._orig_backend
        self._reset()
        km.jd.STATE = self._saved_state
        self.td.cleanup()

    @staticmethod
    def _reset():
        km._retry_suppress_cache.clear()
        for reg in ("_ledger_fault_warned", "_ledger_refusal_warned", "_ledger_write_failed", "_retry_floor_warned"):
            vars(km).get(reg, set()).clear()       # the once-only registries — absent on a kernel before the
            #                                        fix, so these tests fail there on the DEFECT, not in setUp

    def _seed(self, d=None):
        self.p.write_text(json.dumps(d if d is not None else {"s1": 100.0, "s2": 200.0}))
        km._retry_suppress_cache.clear()
        return self.p.read_bytes()

    def _fail_read(self, exc=EIO):
        real, target = Path.read_text, str(self.p)

        def failing(p, *a, **k):
            if str(p) == target:
                raise exc
            return real(p, *a, **k)
        Path.read_text = failing
        heal = lambda: setattr(Path, "read_text", real)
        self._undo.append(heal)
        return heal

    def _aside(self):
        return sorted(n for n in os.listdir(self.dir) if n.startswith("retry-suppressed.json.corrupt-"))

    @staticmethod
    def _full_disk():
        """A stand-in for km._atomic_write on a full disk: ENOSPC naming the TEMP path with a per-call
        sequence, the shape the real publish raises: a fault text built from str(e) would then differ on
        every call, and the once-per-episode assertions below would catch it (review find, 2026-09-08)."""
        calls = [0]

        def full(path, text, mode=None):
            calls[0] += 1
            raise OSError(errno.ENOSPC, "No space left on device", "%s.tmp.1.2.%d" % (path, calls[0]))
        return full

    def test_a_missing_file_reads_as_nobody_suppressed_with_no_log(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            d = km._retry_suppress_data()
        self.assertEqual(d, {})
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(os.listdir(self.dir), [], "absent: nothing moved aside, nothing created")
        self.assertEqual(km._retry_suppress_cache, {}, "absent is not cached — the old arm, byte for byte")

    def test_a_read_fault_never_rewrites_the_file_and_the_click_is_told(self):
        self._seed()
        self.assertTrue(km._session_retry_suppressed("s1"))      # a proved read: s1 and s2 are the last proved snapshot
        self.p.write_text(json.dumps({"s1": 100.0, "s2": 200.0}, indent=1))   # the file moves on (a new stat key)…
        before = self.p.read_bytes()
        self._fail_read()                                         # …and the new bytes cannot be read
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cleared = km._clear_session_retry_suppress("s1")      # the sweep's clear: s1 IS in the tagged copy…
        self.assertEqual(self.p.read_bytes(), before, "suppressed is the safe direction while the ledger cannot be read")
        self.assertFalse(cleared, "…so the clear reaches the writer, and the writer refuses")
        self.assertEqual(err.getvalue().count("refusing to write retry-suppressed.json"), 1,
                         "the refusal count rose: refused at the writer, not short-circuited before it")
        with contextlib.redirect_stderr(err):
            told = km._suppress_session_retry("s3")
        self.assertEqual(self.p.read_bytes(), before, "s1 and s2 keep their stops: the file is byte for byte what it was")
        self.assertIsInstance(told, str, "the arm reports a stop that did not land")
        self.assertIn("could not be recorded", told)
        self.assertIn("Input/output error", told, "…and names the fault")
        self.assertEqual(err.getvalue().count("refusing to write"), 1, "once per fault episode, not per write")
        st = os.stat(self.p)
        self.assertNotEqual(km._retry_suppress_cache[str(self.p)][0], (st.st_mtime_ns, st.st_size),
                            "nothing unproved is cached: the cache still holds the proved snapshot's key, not the file's")
        self.assertEqual(set(km._retry_suppress_cache[str(self.p)][1]), {"s1", "s2"})

    def test_the_snapshot_a_fault_leaves_is_the_last_proved_one(self):
        self._seed()
        self.assertTrue(km._session_retry_suppressed("s1"))      # a proved read fills the cache
        self.p.write_text(json.dumps({"s1": 100.0}))              # the file moves on (a different size)…
        self._fail_read()                                         # …and the new bytes cannot be read
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(km._session_retry_suppressed("s2"), "membership reads the last proved snapshot, never a fabricated {}")

    def test_enospc_on_the_write_is_reported_to_the_click_not_raised(self):
        before = self._seed()

        full = self._full_disk()
        km._atomic_write = full
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            told = km._suppress_session_retry("s3")               # raised straight into the WS reader loop before
            told2 = km._suppress_session_retry("s3")              # the user presses stop again on the same full disk
        self.assertIsInstance(told, str)
        self.assertIn("No space left on device", told)
        self.assertEqual(told2, told, "every click is answered")
        self.assertEqual(err.getvalue().count("write failed"), 1,
                         "…but the fault is said ONCE per episode, at the writer (the maintainer's fold on PR #1019)")
        self.assertEqual(self.p.read_bytes(), before)

    def test_a_full_disk_stop_toasts_every_click_and_the_client_survives_said_once_per_episode(self):
        # the incident shape through the real dispatcher, with the publish itself failing: the interrupt
        # happens, each click hears its own warn toast, the client is never dropped, and the fault is on
        # record once per episode; a landed write ends the episode and a fresh fault speaks again
        before = self._seed()
        cuts = []
        km._kernel_knows = lambda sid: True
        km.Sessions.backend_for = lambda sid: types.SimpleNamespace(interrupt=lambda sid: cuts.append(sid))
        real_write = km._atomic_write

        full = self._full_disk()
        km._atomic_write = full
        sent, err = [], io.StringIO()
        client = {"app": "chat", "alive": True, "send": lambda s: sent.append(json.loads(s))}
        with contextlib.redirect_stderr(err):
            for _ in range(2):
                km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "interrupt", "id": SID}, client)
        self.assertEqual(cuts, [SID, SID], "both interrupts happened")
        self.assertTrue(client["alive"])
        self.assertEqual([m["type"] for m in sent], ["warn", "warn"], "each stop that did not land is said to the click")
        self.assertIn("No space left on device", sent[1]["text"])
        self.assertEqual(err.getvalue().count("write failed"), 1, "the fault is said once per episode")
        self.assertEqual(self.p.read_bytes(), before, "the ledger is untouched")
        km._atomic_write = real_write
        sent.clear()
        km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "interrupt", "id": SID}, client)
        self.assertEqual(sent, [], "the disk heals: the stop lands and nothing is said")
        self.assertIn(SID, json.loads(self.p.read_text()))
        km._atomic_write = full
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsInstance(km._suppress_session_retry("s4"), str)
        self.assertEqual(err.getvalue().count("write failed"), 1, "a landed write ended the episode: said again")

    def test_the_sweep_s_clear_under_a_full_disk_returns_false_and_does_not_raise(self):
        # the clear-suppress path is the pusher's, not a gesture: a failed publish leaves the suppression
        # standing (the safe direction) and returns False instead of a traceback into the pusher's wrap
        # every tick; the writer says it once per episode
        before = self._seed()
        self.assertTrue(km._session_retry_suppressed("s1"))

        full = self._full_disk()
        km._atomic_write = full
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(km._clear_session_retry_suppress("s1"))       # raised OSError before
            self.assertFalse(km._clear_session_retry_suppress("s1"))
        self.assertEqual(self.p.read_bytes(), before, "s1 stays suppressed on disk")
        self.assertEqual(err.getvalue().count("write failed"), 1)

    def test_the_interrupt_handler_toasts_a_stop_that_did_not_land(self):
        # through the real dispatcher (_drive → the interrupt branch): the interrupt itself happens, the
        # stop's failure is a warn toast on the delivering socket, and a healthy ledger toasts nothing
        self._seed()
        cuts = []
        km._kernel_knows = lambda sid: True
        km.Sessions.backend_for = lambda sid: types.SimpleNamespace(interrupt=lambda sid: cuts.append(sid))
        sent = []
        client = {"app": "chat", "alive": True, "send": lambda s: sent.append(json.loads(s))}
        self._fail_read()
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "interrupt", "id": SID}, client)
        self.assertEqual(cuts, [SID], "the interrupt itself happened")
        self.assertEqual([m["type"] for m in sent], ["warn"], "the stop that did not land is said, not swallowed")
        self.assertIn("could not be recorded", sent[0]["text"])
        for undo in self._undo:
            undo()
        self._undo = []
        sent.clear()
        km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "interrupt", "id": SID}, client)
        self.assertEqual(sent, [], "a stop that landed says nothing — the chip's 'interrupting' is the acknowledgement")
        self.assertIn(SID, json.loads(self.p.read_text()))

    def test_a_non_numeric_floor_is_read_as_no_floor_and_said_once(self):
        self._seed({"s1": "yesterday", "s2": 200.0})
        km._alive_sessions = lambda now, live_map: [{"sid": "s1", "path": "x"}, {"sid": "s2", "path": "y"}]
        km._parse_cached = lambda p: {"turns": [_human_turn(500)]}      # both spoke after any numeric floor
        km._session_chip = lambda *a, **k: "ready"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._auto_resume_session_retry(1000, {})                    # a TypeError killed the whole sweep before
            km._auto_resume_session_retry(1001, {})
        self.assertEqual(json.loads(self.p.read_text()), {"s1": "yesterday"},
                         "s2 re-armed as ever; s1's entry is not a floor, so nothing re-arms it — and nothing invents one")
        self.assertEqual(err.getvalue().count("non-numeric floor"), 1, "said once per session, not per tick")

    def test_corrupt_bytes_are_moved_aside_and_the_next_stop_lands_on_a_fresh_ledger(self):
        self.p.write_text('{"s1": 100.0,')
        km._retry_suppress_cache.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(km._suppress_session_retry("s3"), "absent after the move is the fresh-install state")
        self.assertEqual(set(json.loads(self.p.read_text())), {"s3"})
        aside = self._aside()
        self.assertEqual(len(aside), 1, "evidence kept, never deleted")
        self.assertEqual((self.dir / aside[0]).read_text(), '{"s1": 100.0,')
        self.assertEqual(err.getvalue().count("moved aside"), 1)

    # ── the auto-retry gate reads this ledger: unknown is not "not stopped" ─────────────────────
    def _retry_rig(self):
        """_fire_api_retry's collaborators, stubbed down to the gate under test: an api-errored live session,
        no backoff due, no queued retry, and a backend that records what it was asked to send."""
        sent = []
        be = types.SimpleNamespace(pending_queued=lambda sid: [], send=lambda sid, body: sent.append(sid))
        for n, v in (("_retry_paused_on", lambda: False), ("_api_error", lambda p: {"uuid": "e1"}),
                     ("_path_of", lambda sid: "x"), ("_retry_gate_state", lambda sid: (0, 0)),
                     ("_note_retry_sent", lambda *a, **k: None)):
            orig = getattr(km, n)
            setattr(km, n, v)
            self._undo.append(lambda n=n, orig=orig: setattr(km, n, orig))
        km._auto_retried.clear()
        self.addCleanup(km._auto_retried.clear)
        vars(km).get("_retry_gate_held", [None])[0] = None    # the gate's once-per-episode latch (absent before the fix)
        return be, sent

    def _fire(self, be, *sids, manual=False):
        for sid in sids:
            km._fire_api_retry(sid, be, manual=manual)
            km._auto_retried.clear()                          # each ask is its own error episode here

    def test_an_unreadable_ledger_with_no_proved_copy_retries_nobody_until_it_reads_again(self):
        # the membership read answered `sid in {}` (every stopped session read as NOT stopped) and the
        # auto-retry walked back into the session the user had interrupted to end its storm (review find,
        # 2026-09-08). Unknown is not "not stopped": with no proved copy to fall back on, the auto path
        # stands down for every session, said once, until the file reads again; a manual Retry-now still fires.
        self._seed({"s1": 100.0})                             # s1 stopped; this process has proved nothing yet
        be, sent = self._retry_rig()
        heal = self._fail_read()
        err, rows = io.StringIO(), len(km._SDK_BOOT_PROBLEMS)
        with contextlib.redirect_stderr(err):
            self._fire(be, "s1", "s2")
            self._fire(be, "s1")
        self.assertEqual(sent, [], "no auto-retry into s1 (stopped), nor s2: the ledger cannot say who is stopped")
        self.assertEqual(err.getvalue().count("auto-retry stands down"), 1, "said once per fault episode, not per ask")
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), rows + 1, "…and once in the error center")
        with contextlib.redirect_stderr(err):
            self._fire(be, "s1", manual=True)
        self.assertEqual(sent, ["s1"], "a manual Retry-now is the user's explicit call: it fires")
        heal()
        del sent[:]
        with contextlib.redirect_stderr(err):
            self._fire(be, "s1", "s2")
        self.assertEqual(sent, ["s2"], "healed: the real answer applies; s1 stays stopped, s2 retries")
        self.assertIn("auto-retry resumed", err.getvalue())

    def test_with_a_proved_copy_behind_the_fault_the_copy_answers(self):
        self._seed({"s1": 100.0})
        self.assertTrue(km._session_retry_suppressed("s1"))   # a proved read: the copy this process holds
        self.p.write_text(json.dumps({"s1": 100.0}, indent=1))   # the file moves on…
        be, sent = self._retry_rig()
        self._fail_read()                                     # …and cannot be read
        with contextlib.redirect_stderr(io.StringIO()):
            self._fire(be, "s1", "s2")
        self.assertEqual(sent, ["s2"], "the last proved snapshot is the best answer this process has: s1 stays stopped, s2 retries")

    def test_the_arm_and_the_sweep_s_clear_cannot_erase_each_other_s_key(self):
        # the interrupt handler's arm (a WS thread) and the pusher's re-arm clear rewrite the same whole
        # blob. Interleaved deterministically: the arm's read returns and then STALLS until the clear has
        # run, a window that opens only if nothing holds the clear out (the wait has a timeout because,
        # with the lock held, it is a deadlock by design: the clear cannot run until the arm is done).
        self._seed({"s1": 100.0, "s2": 200.0})
        real, main = km._retry_suppress_data, threading.current_thread()
        arm_read, clear_done = threading.Event(), threading.Event()

        def stalling():
            d = real()
            if threading.current_thread() is not main:        # the arm's thread only
                arm_read.set()
                clear_done.wait(0.5)
            return d
        km._retry_suppress_data = stalling
        self._undo.append(lambda: setattr(km, "_retry_suppress_data", real))
        arm = threading.Thread(target=km._suppress_session_retry, args=("s3",))
        arm.start()
        self.assertTrue(arm_read.wait(5), "the arm read the ledger")
        cleared = km._clear_session_retry_suppress("s1")      # the sweep's clear, racing the arm's write
        clear_done.set()
        arm.join(5)
        km._retry_suppress_cache.clear()
        self.assertEqual(set(json.loads(self.p.read_text())), {"s2", "s3"},
                         "both land, s1's clear and s3's stop: neither snapshot erased the other's key")
        self.assertTrue(cleared)


if __name__ == "__main__":
    unittest.main()
