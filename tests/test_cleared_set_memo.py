#!/usr/bin/env python3
"""The feed's clear set is parsed once per state of cleared.jsonl and served while the file stands
(2026-09-09), and the nudge pass hands every session it walks the one parsed set.

Measured on the maintainer's box (py-spy over the live kernel, 60 s, after the placement gate was memoized):
the nudge tick was 73% of the pusher's jobs stage and _cleared_ids() 60% of the tick, a replay of the whole
append-only log (two thousand rows) for every session on every cycle. Pins: parsed once then served; an
append re-derives, including one the file clock cannot see (same mtime, larger size); an undo row removes;
a set read before a write is served no further than that call (the key is the stat taken before the read);
an absent file is empty and never cached; the key carries the path, so a rebound state root is a new key;
a chmod of the log re-derives once (the shared stat key carries ctime_ns); the pass hoists one set and the
walk takes it; the counters ride /perf.

Synthetic ids under a private synthetic sid; a temp state root; nothing real is read."""
import inspect
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source
from fs_clock import move_ctime as _move_ctime   # noqa: E402  the shared test helper, on the path the line above put there
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_cleared_memo", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-888888888802"
G1, G2, G3 = SID + ":g1", SID + ":g2", SID + ":g3"
NOW = 1_800_000_000


class _Memo(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_root = jd.STATE
        jd._rebind_state(Path(self.td.name))
        self.path = jd.STATE / "cleared.jsonl"
        self._reset()

    def tearDown(self):
        jd._rebind_state(self.saved_root)
        self._reset()
        self.td.cleanup()

    def _reset(self):
        km._CLEARED_MEMO["slot"] = None
        for k in km._CLEARED_STATS:
            km._CLEARED_STATS[k] = 0

    def _append(self, *rows):
        with self.path.open("a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    @staticmethod
    def _clear(iid, t):
        return {"id": iid, "t": t}

    @staticmethod
    def _undo(iid, t):
        return {"id": iid, "op": "undo", "t": t}


class ClearSetMemo(_Memo):
    def test_parsed_once_then_served_while_the_file_stands(self):
        self._append(self._clear(G1, NOW - 100), self._clear(G2, NOW - 50))
        real, reads = Path.read_text, []                 # a plain wrapper: an autospec'd wrap does not call through on 3.10

        def counting(p, *a, **k):
            if p == self.path:
                reads.append(p)
            return real(p, *a, **k)
        with patch.object(Path, "read_text", counting):
            a = km._cleared_ids()
            b = km._cleared_ids()
            c = km._cleared_ids()
        self.assertEqual(a, {G1: NOW - 100, G2: NOW - 50})
        self.assertIs(b, a); self.assertIs(c, a)
        self.assertEqual(len(reads), 1, "one read of the log over three calls")
        self.assertEqual(km._CLEARED_STATS, {"served": 2, "derived": 1})

    def test_an_append_re_derives_even_when_the_file_clock_does_not_move(self):
        self._append(self._clear(G1, NOW - 100))
        self.assertEqual(km._cleared_ids(), {G1: NOW - 100})
        before = os.stat(self.path)
        self._append(self._clear(G2, NOW - 50))
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))   # the kernel's coarse file clock: same tick
        self.assertEqual(os.stat(self.path).st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(km._cleared_ids(), {G1: NOW - 100, G2: NOW - 50}, "the size moved: derived again")
        self.assertEqual(km._CLEARED_STATS, {"served": 0, "derived": 2})

    def test_an_undo_row_removes_the_id(self):
        self._append(self._clear(G1, NOW - 100), self._clear(G2, NOW - 100))
        self.assertEqual(set(km._cleared_ids()), {G1, G2})
        self._append(self._undo(G2, NOW))
        self.assertEqual(km._cleared_ids(), {G1: NOW - 100})
        self.assertEqual(km._CLEARED_STATS["derived"], 2)

    def test_a_set_read_before_a_write_is_served_no_further_than_that_call(self):
        # INTERLEAVED WRITE: a row lands while the log is being read. The key was taken before the read,
        # so the set the first call returns (without the row) is cached under a stat the file no longer
        # has; the next call's stat differs and derives again, with the row.
        self._append(self._clear(G1, NOW - 100))
        real = Path.read_text
        landed = []

        def read_then_write(p, *a, **k):
            data = real(p, *a, **k)
            if p == self.path and not landed:
                landed.append(True)
                with p.open("a") as f:
                    f.write(json.dumps(self._clear(G2, NOW)) + "\n")
            return data
        with patch.object(Path, "read_text", read_then_write):
            first = km._cleared_ids()
        self.assertEqual(first, {G1: NOW - 100}, "what the read saw")
        self.assertTrue(landed)
        second = km._cleared_ids()
        self.assertEqual(second, {G1: NOW - 100, G2: NOW}, "the write moved the stat the second call took")
        self.assertEqual(km._CLEARED_STATS, {"served": 0, "derived": 2})
        self.assertIs(km._cleared_ids(), second, "and the newer set is what serves from here")

    def test_an_absent_file_is_empty_and_never_cached(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(km._cleared_ids(), {})
        self.assertEqual(km._cleared_ids(), {})
        self.assertEqual(km._CLEARED_STATS, {"served": 0, "derived": 2})
        self.assertIsNone(km._CLEARED_MEMO["slot"])
        self._append(self._clear(G3, NOW))
        self.assertEqual(km._cleared_ids(), {G3: NOW}, "the first row is seen at once")

    def test_the_key_carries_the_path_so_a_rebound_root_is_a_new_key(self):
        self._append(self._clear(G1, NOW - 100))
        km._cleared_ids()
        key = km._CLEARED_MEMO["slot"][0]
        self.assertEqual(key[0], str(self.path))
        self.assertEqual(len(key), 5, "path, then the stat's mtime_ns, size, inode and ctime_ns")
        other = tempfile.TemporaryDirectory()
        try:
            jd._rebind_state(Path(other.name))
            (jd.STATE / "cleared.jsonl").write_text(json.dumps(self._clear(G2, NOW)) + "\n")
            self.assertEqual(km._cleared_ids(), {G2: NOW}, "the other root's log, not the cached set")
        finally:
            jd._rebind_state(Path(self.td.name))
            other.cleanup()

    def test_a_chmod_of_the_log_re_derives_the_set_once(self):
        """_stat_key is shared with the dead-lane memo and carries ctime_ns, which a chmod or chown moves while
        mtime, size and inode stand: the set is derived again once and then served as before. No kernel path
        changes this log's metadata without appending, so that one read is the whole cost of the member."""
        self._append(self._clear(G1, NOW - 100))
        real, reads = Path.read_text, []

        def counting(p, *a, **k):
            if p == self.path:
                reads.append(p)
            return real(p, *a, **k)
        with patch.object(Path, "read_text", counting):
            a = km._cleared_ids()
            self.assertIs(km._cleared_ids(), a)
            key0 = km._CLEARED_MEMO["slot"][0]
            _move_ctime(self.path)
            b = km._cleared_ids()
            self.assertIs(km._cleared_ids(), b, "served again once re-derived")
        self.assertEqual(b, a, "the same rows")
        self.assertEqual(len(reads), 2, "one extra read: the chmod moved the ctime")
        key1 = km._CLEARED_MEMO["slot"][0]
        self.assertEqual(key1[:4], key0[:4], "path, mtime_ns, size and inode stood")
        self.assertNotEqual(key1[4], key0[4], "ctime_ns moved")
        self.assertEqual(km._CLEARED_STATS, {"served": 2, "derived": 2})

    def test_a_malformed_row_is_skipped_as_before(self):
        self._append(self._clear(G1, NOW - 100))
        with self.path.open("a") as f:
            f.write("{not json\n")
        self._append({"t": NOW}, self._clear(G2, NOW))
        self.assertEqual(km._cleared_ids(), {G1: NOW - 100, G2: NOW})


class PassHoist(_Memo):
    def test_the_pass_parses_once_and_hands_every_session_the_same_set(self):
        # BEHAVIOURAL, not a source pin: two alive sessions, the real pass, the walk a recorder. The clear
        # log is parsed once for the pass and both walks receive the very same dict.
        self._append(self._clear(G1, NOW - 100))
        A, B = SID, "11111111-2222-3333-4444-888888888803"
        seen, calls = [], []
        real = km._cleared_ids

        def counting():
            calls.append(1)
            return real()

        def walk(s, now, live, nudged, waitfor, alive_ids=None, wake_only=False, cleared=None):
            seen.append(cleared)
            return False
        saved = {n: getattr(km, n) for n in
                 ("_cleared_ids", "_alive_sessions", "_wait_for_graph", "_auto_nudge_session", "_compact_suggest_tick",
                  "_debt_backstop_tick", "_dead_wait_sweep", "_awaiting_wake_outcomes", "_push_soon",
                  "_pop_walk_gate", "_put_walk_gate")}
        km._cleared_ids = counting
        km._alive_sessions = lambda now, live: [{"sid": A, "path": "/nonexistent-a.jsonl"},
                                                {"sid": B, "path": "/nonexistent-b.jsonl"}]
        km._wait_for_graph = lambda now, alive_ids: {}
        km._auto_nudge_session = walk
        km._compact_suggest_tick = lambda sid, tm, now: False
        km._debt_backstop_tick = lambda now: None
        km._dead_wait_sweep = lambda alive_ids, nudged, now: None
        km._awaiting_wake_outcomes = lambda now, alive_ids: False
        km._push_soon = lambda: None
        km._pop_walk_gate = lambda sid: None
        km._put_walk_gate = lambda sid, gate, now: None
        try:
            km._auto_nudge_pass(NOW, {}, run_dead_wait=False)
        finally:
            for n, v in saved.items():
                setattr(km, n, v)
        self.assertEqual(len(seen), 2, "both sessions walked")
        self.assertEqual(calls, [1], "the clear log parsed once for the pass")
        self.assertIs(seen[0], seen[1], "one set, handed to every session")
        self.assertEqual(seen[0], {G1: NOW - 100})

    def test_a_walk_without_the_set_derives_it_as_before(self):
        params = inspect.signature(km._auto_nudge_session).parameters
        self.assertIn("cleared", params)
        self.assertIsNone(params["cleared"].default)

    def test_the_counters_ride_the_perf_snapshot(self):
        snap = km._PERF_STATS.snapshot()
        self.assertEqual(set(snap["memos"]["cleared"]), {"served", "derived"})
        self.assertEqual(snap["memos"]["cleared"], dict(km._CLEARED_STATS))


if __name__ == "__main__":
    unittest.main()
