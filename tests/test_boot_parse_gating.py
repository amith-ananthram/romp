#!/usr/bin/env python3
"""T323 stage 1 (the user 2026-09-10): a kernel boot must not read every live transcript whole for nobody. The boot
warm parses nothing (it warms discover() only), the feed-only warm parses only sessions that moved since this boot or
are working now, the judges' passes go newest-first and yield between sessions, the two event-keyed tick jobs skip a
session whose transcript and state log are unchanged since their last look (the boot being the first baseline), and
/perf counts every cold parse so the effect is measurable. Hermetic: synthetic files under a temp root, the kernel and
judge loaded against a temp state directory, threads joined explicitly."""
import inspect
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_t323s1", os.path.join(BIN, "romp-kernel"))

SID_OLD = "11111111-2222-4333-8444-000000000001"
SID_NEW = "22222222-2222-4333-8444-000000000002"
SID_WORK = "33333333-2222-4333-8444-000000000003"


def _threads():
    return set(threading.enumerate())


def _join_new(before, timeout=10):
    for t in _threads() - before:
        t.join(timeout)


def _row(d, sid, old):
    """A session row whose transcript (and state log) predate or postdate this kernel's start."""
    p = Path(d) / (sid + ".jsonl")
    p.write_text(json.dumps({"type": "user", "uuid": sid[:8], "timestamp": "2026-09-10T00:00:00Z", "message": {"role": "user", "content": "x"}}) + "\n")
    t = km._STARTED - 600 if old else time.time() + 1
    os.utime(p, (t, t))
    return {"sid": sid, "path": str(p), "name": sid[:8], "mtime": t}


class BootWarmParsesNothing(unittest.TestCase):
    def test_discover_only(self):
        calls = {"discover": 0, "parse": 0}
        before = _threads()
        with mock.patch.object(km.jd, "discover", side_effect=lambda now, **k: calls.__setitem__("discover", calls["discover"] + 1) or []), \
             mock.patch.object(km, "_parse", side_effect=lambda *a, **k: calls.__setitem__("parse", calls["parse"] + 1)), \
             mock.patch.object(km, "_alive_sessions", side_effect=lambda now, tmux: [{"sid": SID_OLD, "path": "/nonexistent", "name": "web"}]):
            km._boot_warm()
            _join_new(before)
        self.assertEqual(calls["discover"], 1, "the shared discover cache is still warmed")
        self.assertEqual(calls["parse"], 0, "no transcript is parsed for nobody (the redial road parses the active tab itself)")
        src = inspect.getsource(km._boot_warm)
        self.assertNotIn("_parse(", src, "the warm's body calls no parse")
        self.assertIn("PARSES NOTHING", src)


class FeedWarmParsesOnlyWhatMoved(unittest.TestCase):
    def test_moved_or_working_only(self):
        d = tempfile.mkdtemp()
        rows = [_row(d, SID_OLD, old=True), _row(d, SID_NEW, old=False), _row(d, SID_WORK, old=True)]
        parsed = []
        before = _threads()
        km._warming[0] = False
        saved = list(km._clients)
        with km._clients_lock:                                   # the warm is a no-op with nobody connected: a feed-only window
            km._clients[:] = [{"app": "feed", "send": lambda s: None, "sent": {}, "alive": True}]
        self.addCleanup(lambda: km._clients.__setitem__(slice(None), saved))
        with mock.patch.object(km, "_alive_sessions", side_effect=lambda now, tmux: rows), \
             mock.patch.object(km, "_tmux_sessions", side_effect=lambda: {SID_WORK: {"state": "working"}}), \
             mock.patch.object(km, "_has_parsing_client", side_effect=lambda: False), \
             mock.patch.object(km, "_parse", side_effect=lambda path, sid, now: parsed.append(sid)):
            km._warm_fleet_bg(int(time.time()))
            _join_new(before)
        self.assertEqual(sorted(parsed), sorted([SID_NEW, SID_WORK]),
                         "the session that moved since boot and the one working now; the untouched one waits for a client")

    def test_moved_since_boot_reads_either_file(self):
        d = tempfile.mkdtemp()
        r = _row(d, SID_OLD, old=True)
        self.assertFalse(km._session_moved_since_boot(r))
        states = km.jd.STATE / "states"
        states.mkdir(parents=True, exist_ok=True)
        (states / (SID_OLD + ".jsonl")).write_text(json.dumps({"t": int(time.time()), "state": "waiting"}) + "\n")
        self.assertTrue(km._session_moved_since_boot(r), "a state-log append after the boot counts as movement")
        (states / (SID_OLD + ".jsonl")).unlink()


class TickJobsKeyOnAChange(unittest.TestCase):
    def setUp(self):
        km._TICK_SEEN.clear()

    def test_unchanged_since_boot_is_skipped_and_a_change_is_evaluated_once(self):
        d = tempfile.mkdtemp()
        r = _row(d, SID_OLD, old=True)
        self.assertTrue(km._tick_job_skips("interrupt-block", r), "first look: unchanged since boot → the store's verdict stands, no parse")
        self.assertTrue(km._tick_job_skips("interrupt-block", r), "still unchanged")
        with open(r["path"], "a") as f:
            f.write(json.dumps({"type": "assistant", "uuid": "a1"}) + "\n")
        os.utime(r["path"], None)
        skip, st = km._tick_job_check("interrupt-block", r)
        self.assertFalse(skip, "an appended record is the event: evaluate")
        self.assertFalse(km._tick_job_skips("interrupt-block", r), "not yet marked done (a fault mid-tick): the next tick evaluates again")
        km._tick_job_done("interrupt-block", r, st)
        self.assertTrue(km._tick_job_skips("interrupt-block", r), "once the evaluation completed, the same files skip again")

    def test_a_session_that_moved_before_this_boot_read_it_is_evaluated_at_first_look(self):
        d = tempfile.mkdtemp()
        r = _row(d, SID_NEW, old=False)          # mtime after _STARTED: it changed under the previous kernel's death or since
        skip, st = km._tick_job_check("working-notes", r)
        self.assertFalse(skip)
        km._tick_job_done("working-notes", r, st)
        self.assertTrue(km._tick_job_skips("working-notes", r))

    def test_a_goal_store_write_is_an_event_too(self):
        """A judge can clear or complete the goal a marker points at without a transcript change; the interrupt
        tick must re-evaluate on that alone (its stale-marker rule)."""
        d = tempfile.mkdtemp()
        r = _row(d, SID_OLD, old=True)
        self.assertTrue(km._tick_job_skips("interrupt-block", r))
        km.jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.GOALDIR / (SID_OLD + ".json")).write_text("{}")
        try:
            skip, st = km._tick_job_check("interrupt-block", r)
            self.assertFalse(skip, "the store moved: evaluate")
            km._tick_job_done("interrupt-block", r, st)
            self.assertTrue(km._tick_job_skips("interrupt-block", r))
        finally:
            (km.jd.GOALDIR / (SID_OLD + ".json")).unlink()

    def test_a_missing_transcript_is_unknown_never_unchanged(self):
        r = {"sid": SID_OLD, "path": "/nonexistent-t323.jsonl", "name": "web"}
        skip, st = km._tick_job_check("interrupt-block", r)
        self.assertFalse(skip, "a row whose transcript cannot be read is evaluated (the fault-boundary tests build such rows)")
        km._tick_job_done("interrupt-block", r, st)
        self.assertFalse(km._tick_job_skips("interrupt-block", r), "and stays evaluated every tick until a file exists")

    def test_jobs_keep_separate_memos(self):
        d = tempfile.mkdtemp()
        r = _row(d, SID_NEW, old=False)
        self.assertFalse(km._tick_job_skips("interrupt-block", r))
        self.assertFalse(km._tick_job_skips("working-notes", r), "another job's first look is its own")

    def test_the_two_event_keyed_ticks_gate_before_their_parse_and_the_nudge_does_not(self):
        for fn, job in ((km._interrupt_block_tick, "interrupt-block"), (km._clear_done_working_notes, "working-notes")):
            src = inspect.getsource(fn)
            gate, parse = src.index('_tick_job_check("%s", s)' % job), src.index("jd.parsed_session(sid, [s[\"path\"]], now)")
            self.assertLess(gate, parse, "%s: the gate sits before the parse" % job)
            self.assertIn('_tick_job_done("%s", s, files_st)' % job, src, "%s: a completed evaluation is marked done" % job)
        self.assertEqual(inspect.getsource(km._interrupt_block_tick).count('_tick_job_done("interrupt-block", s, files_st)'), 4,
                         "done on the four landed outcomes (filed, standing, lifted, nothing to lift); never on a refused write or an unproved ledger")
        self.assertNotIn("_tick_job_skips", inspect.getsource(km._auto_nudge_session),
                         "the nudge has wall-clock timers, so it keeps its per-cycle evaluation (documented in _tick_job_skips)")


class PerfCountsColdParses(unittest.TestCase):
    def test_counter_and_snapshot(self):
        km._PERF_STATS.reset()
        km._PERF_STATS.parse(SID_OLD, 1234)
        km._PERF_STATS.parse(SID_OLD, 10)
        km._PERF_STATS.parse(SID_NEW, 5)
        snap = km._PERF_STATS.snapshot()
        self.assertEqual((snap["parses"]["total"], snap["parses"]["bytes"]), (3, 1249))
        self.assertEqual(snap["parses"]["bySid"], {SID_OLD[:8]: 2, SID_NEW[:8]: 1})
        self.assertIsInstance(snap["parses"]["judge"], int)

    def test_kernel_parse_counts_a_miss_not_a_hit(self):
        d = tempfile.mkdtemp()
        r = _row(d, SID_NEW, old=False)
        km._PERF_STATS.reset()
        km._parse_cache.pop(r["path"], None)
        now = int(time.time())
        km._parse(r["path"], SID_NEW, now)
        km._parse(r["path"], SID_NEW, now)
        self.assertEqual(km._PERF_STATS.snapshot()["parses"]["total"], 1, "the second call is a cache hit")

    def test_judge_parse_misses(self):
        d = tempfile.mkdtemp()
        r = _row(d, SID_WORK, old=False)
        before = jd.parse_misses()
        jd._PARSE_CACHE.pop(SID_WORK, None)
        now = int(time.time())
        jd.parsed_session(SID_WORK, [r["path"]], now)
        jd.parsed_session(SID_WORK, [r["path"]], now)
        self.assertEqual(jd.parse_misses() - before, 1)


class JudgesGoNewestFirst(unittest.TestCase):
    def test_by_recency_orders_by_transcript_mtime(self):
        d = tempfile.mkdtemp()
        rows = []
        for i, sid in enumerate((SID_OLD, SID_NEW, SID_WORK)):
            p = Path(d) / (sid + ".jsonl"); p.write_text("{}\n")
            os.utime(p, (1000 + i * 100, 1000 + i * 100))
            rows.append((sid, str(p), sid, sid[:8]))
        rows.append(("missing", str(Path(d) / "missing.jsonl"), "missing", "m"))
        out = jd.by_recency(rows)
        self.assertEqual([r[0] for r in out], [SID_WORK, SID_NEW, SID_OLD, "missing"], "newest first, a stat failure last")
        self.assertEqual([r[0] for r in rows][0], SID_OLD, "the input is not reordered in place")

    def test_passes_walk_by_recency_and_yield(self):
        src = inspect.getsource(jd._run_index)
        self.assertIn("fleet = by_recency(discover(now))", src)
        self.assertIn("yield_between_sessions()", src)
        self.assertEqual(inspect.getsource(jd).count("by_recency(discover(now))"), 1,
                         "the FIRST pass (the index) orders by recency; the courier and the capped passes keep discover()'s order, which their tests pin")


if __name__ == "__main__":
    unittest.main()
