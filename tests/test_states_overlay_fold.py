#!/usr/bin/env python3
"""The awaiting overlay reads states/<sid>.jsonl through the shared append-incremental fold, and the
interrupt tick retires its entries with the interrupt-marks memo's.

`_states_awaiting_overlay` walked every parsed row of the states log on every call. Its one caller,
`_session_awaiting`, runs from the nudge tick, the timeline lane build, build_session, the feed, the
background-work read and GET /sessions: per idle session, per site, per cycle, on a file that did not
change. The reader now folds the log through em.fold_records, the reader `_state_intervals` and
`_last_machine_cut` already use on the same file: an unchanged file steps no row, an appended row steps
once, and the interrupt tick drops a session's entry when the session leaves the alive set, the event
that already retires its interrupt-marks entries. Both memos report under GET /perf (`memos.intrMarks`,
`memos.statesOverlay`).

Every equality test here compares the fold against a private copy of the row walk it replaced, on the
newline-terminated row shapes the states writers produce; the one row the two read differently, a complete
final record still waiting for its newline, has its own test. Synthetic fixtures only: placeholder UUIDs,
invented text. The
tick test seeds a names entry and a transcript, so the sids are private to this module (the goal-store
fixtures rule)."""
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_sov", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge_sov", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_sov", os.path.join(BIN, "romp-kernel"))
jd = km.jd

# Sids of this module's own (the goal-store fixtures rule): the override journal is per sid and shared by
# every kernel test copy in the process.
SID = "77777777-8888-9999-aaaa-111111111111"
SID2 = "77777777-8888-9999-aaaa-222222222222"
DEAD = "77777777-8888-9999-aaaa-333333333333"
NOW = 1781100000
T0 = NOW - 3600


def ref_overlay(sid):
    """The reader as it stood before the fold: every parsed row of the states log walked on every call. The
    fold answers what this walk answers on every newline-terminated row; the one difference is a complete
    final record still waiting for its newline, which the fold reads provisionally and this walk skipped
    (test_a_final_row_without_its_newline_is_read_provisionally)."""
    last = None
    working_after = False
    for o in em._read_jsonl_incremental(jd.STATE / "states" / ("%s.jsonl" % sid)):
        if not isinstance(o, dict):
            continue
        if "awaiting" in o:
            last, working_after = o, False
        elif o.get("state") == "working":
            working_after = True
    if last is not None and last.get("awaiting") and working_after:
        return {"awaiting": False, "why": None}
    return last


def _reset(stats):
    for k in list(stats):
        stats[k] = 0


class _State(unittest.TestCase):
    """A fresh state root per test and the fold's memo emptied, so no test reads another's entries."""

    def setUp(self):
        km._states_overlay_cache.clear()          # the memos first: a setUp that fails here leaves the state
        _reset(km._states_overlay_stats)          # root where it was (tearDown does not run after a failed setUp)
        km._states_overlay_failed.clear()
        self.saved_state = jd.STATE
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        jd._rebind_state(self.saved_state)
        shutil.rmtree(self.td, ignore_errors=True)

    def states_path(self, sid=SID):
        return jd.STATE / "states" / (sid + ".jsonl")

    def write_states(self, rows, sid=SID):
        p = self.states_path(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def append_state(self, row, sid=SID):
        with open(self.states_path(sid), "a") as f:
            f.write(json.dumps(row) + "\n")


class OverlayFold(_State):
    """`_states_awaiting_overlay` equals the row walk it replaced, and steps only what changed."""

    def check(self, rows, expect, sid=SID):
        self.write_states(rows, sid)
        got = km._states_awaiting_overlay(sid)
        self.assertEqual(got, ref_overlay(sid), "the fold equals the walk")
        self.assertEqual(got, expect)
        return got

    def test_awaiting_then_idle_stays_awaiting(self):
        self.check([{"t": 100, "awaiting": True, "why": "two jobs"}, {"t": 200, "state": "idle"}],
                   {"t": 100, "awaiting": True, "why": "two jobs"})

    def test_a_later_work_turn_supersedes_a_stale_true(self):
        self.check([{"t": 100, "awaiting": True, "why": "two jobs"}, {"t": 200, "state": "idle"},
                    {"t": 300, "state": "working"}, {"t": 400, "state": "idle"}],
                   {"awaiting": False, "why": None})

    def test_a_work_turn_before_the_overlay_row_does_not_supersede(self):
        self.check([{"t": 100, "state": "working"}, {"t": 200, "awaiting": True, "why": "a build"},
                    {"t": 300, "state": "waiting"}],
                   {"t": 200, "awaiting": True, "why": "a build"})

    def test_a_row_with_both_keys_is_an_overlay_row(self):
        # awaiting first, the walk's if/elif order: such a row resets the supersede flag and never sets it
        self.check([{"t": 100, "awaiting": True, "why": "x"},
                    {"t": 200, "awaiting": True, "state": "working", "why": "y"}],
                   {"t": 200, "awaiting": True, "state": "working", "why": "y"})

    def test_state_rows_with_extra_keys_count_by_their_state(self):
        self.check([{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "picker", "tier": "strict"}],
                   {"t": 100, "awaiting": True, "why": "x"})
        self.check([{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "working", "tier": "loose"}],
                   {"awaiting": False, "why": None}, sid=SID2)

    def test_the_marker_rows_every_other_writer_appends_are_ignored(self):
        rows = [{"t": 100, "awaiting": True, "why": "x", "kind": "task", "count": 2},
                {"t": 110, "retriesGaveUp": 3, "errorKind": "server_error"},
                {"t": 120, "retriesRecovered": 2},
                {"t": 130, "orphanReply": {"uuid": "a9", "text": "working on it"}},
                {"t": 140, "cmdGesture": "/model working"},
                {"t": 150, "machineCut": "restart"},
                {"t": 160, "resumeFork": {"from": SID2, "to": DEAD}},
                {"t": 170, "effortApplied": "high"},
                {"t": 180, "supersededBy": SID2}]
        self.check(rows, rows[0])

    def test_a_trailing_false_row_is_returned_as_is(self):
        self.check([{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "working"},
                    {"t": 300, "awaiting": False, "why": ""}],
                   {"t": 300, "awaiting": False, "why": ""})

    def test_no_overlay_rows_and_no_file_are_none(self):
        self.assertIsNone(km._states_awaiting_overlay(SID), "no file: no overlay")
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID))
        self.assertEqual(km._states_overlay_report()["fail"], 0, "an absent file is a state, not a failure")
        self.check([{"t": 100, "state": "working"}, {"t": 200, "state": "idle"}], None)

    def test_unparseable_lines_are_skipped(self):
        p = self.states_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"t": 100, "awaiting": true, "why": "x"}\nnot json\n{"t": 200, "state": "idle"}\n')
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID))
        self.assertEqual(km._states_awaiting_overlay(SID)["awaiting"], True)

    def _counting_step(self):
        real = km._states_overlay_step
        calls = []

        def step(state, o):
            calls.append(o)
            return real(state, o)
        km._states_overlay_step = step
        self.addCleanup(setattr, km, "_states_overlay_step", real)
        return calls

    def test_an_appended_row_folds_without_re_parsing_the_earlier_records(self):
        calls = self._counting_step()
        rows = [{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "idle"}, {"t": 300, "state": "waiting"}]
        self.check(rows, rows[0])
        self.assertEqual(len(calls), 3, "the first fold steps every record")
        del calls[:]
        self.assertEqual(km._states_awaiting_overlay(SID), rows[0])
        self.assertEqual(calls, [], "unchanged: a hit steps nothing")
        self.append_state({"t": 400, "state": "working"})
        self.assertEqual(km._states_awaiting_overlay(SID), {"awaiting": False, "why": None})
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID))
        self.assertEqual(len(calls), 1, "the append folds only the new row")
        st = km._states_overlay_report()
        self.assertEqual((st["refold"], st["hit"], st["append"], st["entries"]), (1, 2, 1, 1),
                         "one first fold, the unchanged read and the post-append read as hits, one append")

    def test_a_truncated_file_refolds_from_the_start(self):
        calls = self._counting_step()
        rows = [{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "working"}, {"t": 300, "state": "idle"}]
        self.check(rows, {"awaiting": False, "why": None})
        del calls[:]
        self.write_states(rows[:1])                              # shrank: the work turn is gone
        self.assertEqual(km._states_awaiting_overlay(SID), rows[0])
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID))
        self.assertEqual(len(calls), 1, "re-folded from record 0 over the one record left")
        self.assertEqual(km._states_overlay_report()["refold"], 2)

    def test_a_same_size_rewrite_with_a_new_mtime_refolds(self):
        rows = [{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "waiting"}]
        self.check(rows, rows[0])
        p = self.states_path()
        st0 = os.stat(p)
        rows2 = [{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "working"}]   # same byte length
        self.write_states(rows2)
        self.assertEqual(os.stat(p).st_size, st0.st_size, "fixture invariant: the rewrite keeps the size")
        os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns + 2_000_000_000))
        self.assertEqual(km._states_awaiting_overlay(SID), {"awaiting": False, "why": None})
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID))
        self.assertEqual(km._states_overlay_report()["refold"], 2)

    def test_a_read_failure_on_a_file_that_exists_is_counted_logged_once_and_not_memoized(self):
        rows = [{"t": 100, "awaiting": True, "why": "x"}, {"t": 200, "state": "idle"}]
        self.check(rows, rows[0])
        self.assertEqual(km._states_overlay_report()["entries"], 1)
        # the shared reader serves an UNCHANGED file's records on an identity hit without opening it, so a
        # permission flip alone is not a read attempt: the file grows first, then becomes unreadable
        self.append_state({"t": 300, "state": "working"})
        os.chmod(self.states_path(), 0)
        try:
            if os.access(self.states_path(), os.R_OK):
                self.skipTest("this user reads through mode 000 (root)")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertIsNone(km._states_awaiting_overlay(SID), "the walk answered None on an OSError")
                self.assertIsNone(km._states_awaiting_overlay(SID))
            st = km._states_overlay_report()
            self.assertEqual((st["fail"], st["entries"]), (2, 0), "counted per call, memoized never")
            self.assertEqual(err.getvalue().count("unreadable"), 1, "one stderr line per failure episode")
            self.assertIn(os.path.basename(str(self.states_path())), err.getvalue())
            self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID))
        finally:
            os.chmod(self.states_path(), 0o644)
        self.assertEqual(km._states_awaiting_overlay(SID), {"awaiting": False, "why": None},
                         "readable again: folded from record 0 over the three rows")
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID))
        st = km._states_overlay_report()
        self.assertEqual((st["entries"], st["refold"]), (1, 2))
        self.assertNotIn(str(self.states_path()), km._states_overlay_failed, "a good read ends the episode")

    def test_forget_releases_entries_for_sessions_outside_the_alive_set(self):
        for s in (SID, SID2):
            self.check([{"t": 100, "awaiting": True, "why": "x"}], {"t": 100, "awaiting": True, "why": "x"}, sid=s)
        self.assertEqual(km._states_overlay_report()["entries"], 2)
        km._states_overlay_forget({SID})
        st = km._states_overlay_report()
        self.assertEqual((st["entries"], st["evict"]), (1, 1))
        self.assertIn(str(self.states_path(SID)), km._states_overlay_cache)
        self.assertNotIn(str(self.states_path(SID2)), km._states_overlay_cache)
        km._states_overlay_forget({SID})
        self.assertEqual(km._states_overlay_report()["evict"], 1, "nothing left to drop: no count")

    def test_a_departed_sids_open_episode_survives_the_forget_and_is_named_once(self):
        # A fail pops the cache entry (fold_records), so a sid whose read failed sits only in
        # _states_overlay_failed. Its file is still read after the sid leaves the alive set: build_session
        # serves a dead session kept open as a read-only tab and the scroll-back handler for any sid, and
        # GET /classify answers for any sid, none of them gated on liveness. The stderr line is one per
        # episode for every reader, so the forget must not reset the latch; only a good read ends the episode.
        self.check([{"t": 100, "awaiting": True, "why": "x"}], {"t": 100, "awaiting": True, "why": "x"})
        # the shared reader serves an UNCHANGED file's records on an identity hit without opening it, so a
        # permission flip alone is not a read attempt: the file grows first, then becomes unreadable
        self.append_state({"t": 200, "state": "idle"})
        os.chmod(self.states_path(), 0)
        try:
            if os.access(self.states_path(), os.R_OK):
                self.skipTest("this user reads through mode 000 (root)")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertIsNone(km._states_awaiting_overlay(SID), "the read failed: no overlay")
                km._states_overlay_forget(set())              # SID leaves the alive set with the episode open
                self.assertIsNone(km._states_awaiting_overlay(SID), "a kept-open tab's rebuild reads it again")
                km._states_overlay_forget(set())              # the next tick's forget
                self.assertIsNone(km._states_awaiting_overlay(SID))
            self.assertEqual(err.getvalue().count("unreadable"), 1,
                             "one stderr line per episode, whatever the forget did between the reads")
            st = km._states_overlay_report()
            self.assertEqual((st["fail"], st["entries"]), (3, 0), "every read counted, none memoized")
            self.assertIn(str(self.states_path()), km._states_overlay_failed, "the forget leaves the open episode alone")
        finally:
            os.chmod(self.states_path(), 0o644)
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID), "readable again: the walk's answer")
        self.assertNotIn(str(self.states_path()), km._states_overlay_failed, "a good read ends the episode")

    def test_the_failed_set_is_cleared_whole_above_its_cap(self):
        # The forget leaves a departed sid's path in the set, so the set is bounded the way the fold cache is:
        # cleared whole above 256 paths (fold_records), never per path. At 256 nothing is cleared.
        seeded = {os.path.join(self.td, "states", "cap-%d.jsonl" % i) for i in range(256)}
        with km._STATES_OVERLAY_LOCK:
            km._states_overlay_failed.update(seeded)
        self.check([{"t": 100, "awaiting": True, "why": "x"}], {"t": 100, "awaiting": True, "why": "x"})
        self.append_state({"t": 200, "state": "idle"})
        os.chmod(self.states_path(), 0)
        try:
            if os.access(self.states_path(), os.R_OK):
                self.skipTest("this user reads through mode 000 (root)")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertIsNone(km._states_awaiting_overlay(SID))
            self.assertEqual(km._states_overlay_failed, seeded | {str(self.states_path())},
                             "256 paths is the cap, not above it: nothing cleared, the failing path enters")
            self.assertEqual(err.getvalue().count("unreadable"), 1)
            # 257 paths now: the next fail clears the set whole, and a clear ends every open episode at once,
            # so the same unreadable file is named a second time on that read, as the fold cache re-folds
            # after its own clear
            with redirect_stderr(err):
                self.assertIsNone(km._states_awaiting_overlay(SID))
            self.assertEqual(km._states_overlay_failed, {str(self.states_path())},
                             "above the cap the set is cleared whole, then the failing path re-enters")
            self.assertEqual(err.getvalue().count("unreadable"), 2, "the clear ended the episode; the next fail opens one")
        finally:
            os.chmod(self.states_path(), 0o644)

    def test_the_report_has_the_documented_shape(self):
        st = km._states_overlay_report()
        self.assertEqual(set(st), {"hit", "append", "refold", "fail", "evict", "entries"})
        for k, v in st.items():
            self.assertIsInstance(v, int, k)
        st["hit"] = -1
        self.assertNotEqual(km._states_overlay_stats["hit"], -1, "the report is a copy")

    def test_a_final_row_without_its_newline_is_read_provisionally(self):
        # The one row the fold and the walk read differently. The incremental reader leaves a complete final
        # record without its newline unconsumed (a writer caught mid-append must not enter the cache torn), so
        # the walk over it never saw such a row until the newline landed; the fold reads it provisionally
        # onto a copy of the carried state, as _state_intervals and _last_machine_cut already do on this file,
        # and for good once the newline lands. Every states writer puts the row and its newline in one write,
        # so the case is a write caught between the two.
        p = self.states_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"t": 100, "state": "working"}) + "\n"
                     + json.dumps({"t": 200, "awaiting": True, "why": "a build"}))    # no trailing newline
        self.assertIsNone(ref_overlay(SID), "the walk did not see the row until its newline landed")
        self.assertEqual(km._states_awaiting_overlay(SID), {"t": 200, "awaiting": True, "why": "a build"},
                         "the fold reads the complete final row provisionally")
        self.assertEqual(km._states_overlay_cache[str(p)][0], 1, "provisional: the row is not in the carried state")
        with open(p, "a") as f:
            f.write("\n")
        self.assertEqual(km._states_awaiting_overlay(SID), {"t": 200, "awaiting": True, "why": "a build"},
                         "the newline lands: the answer does not change")
        self.assertEqual(km._states_awaiting_overlay(SID), ref_overlay(SID), "and the walk agrees from here on")
        st = km._states_overlay_report()
        self.assertEqual((st["refold"], st["append"], st["hit"]), (1, 1, 1),
                         "the first read, the row stepped for good as an append, the walk-parity read as a hit")
        self.assertEqual(km._states_overlay_cache[str(p)][0], 2)
        # the same for a work turn caught before its newline: it supersedes a stale true provisionally
        p2 = self.states_path(SID2)
        p2.write_text(json.dumps({"t": 100, "awaiting": True, "why": "x"}) + "\n" + json.dumps({"t": 200, "state": "working"}))
        self.assertEqual(ref_overlay(SID2), {"t": 100, "awaiting": True, "why": "x"})
        self.assertEqual(km._states_awaiting_overlay(SID2), {"awaiting": False, "why": None})
        with open(p2, "a") as f:
            f.write("\n")
        self.assertEqual(km._states_awaiting_overlay(SID2), {"awaiting": False, "why": None})
        self.assertEqual(km._states_awaiting_overlay(SID2), ref_overlay(SID2))


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class InterruptTickRetires(unittest.TestCase):
    """The interrupt tick's tail retires the fold's entries with the cycle's alive set, the same event that
    retires the interrupt-marks memo's: a real transcript, a names entry and a rebound state root, so the
    tick resolves its alive set through discover rather than a stub."""

    def setUp(self):
        self._reset()                 # the memos first: a setUp that fails here leaves the state root and the
        self.td = tempfile.TemporaryDirectory()   # names/projects paths where they were (no tearDown after a failed setUp)
        td = Path(self.td.name)
        cdir = td / "launchdir"
        cdir.mkdir()
        proj = td / "projects"
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        names = td / "names"
        names.mkdir()
        (names / SID).write_text("api\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.STATE, jd.NAMES, jd.PROJECTS, km.NAMES)
        jd._rebind_state(td)
        jd.NAMES, jd.PROJECTS = names, proj
        km.NAMES = names
        (td / "states").mkdir()
        km._write_auto_nudge({"enabled": True, "nudged": {}, "intrBlocked": {}})
        recs = [{"type": "user", "timestamp": _iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
                 "message": {"role": "user", "content": "wire up the reconnect banner"}},
                {"type": "assistant", "timestamp": _iso(T0 + 20), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
                             "stop_reason": "end_turn"}}]
        (pdir / (SID + ".jsonl")).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.tmux = {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None}}

    def _reset(self):
        km._parse_cache.clear()
        km._machine_cut_cache.clear()
        km._autonudge_cache.clear()
        km._intr_marks_memo.clear()
        km._states_overlay_cache.clear()
        _reset(km._states_overlay_stats)
        km._states_overlay_failed.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()   # the two key on one identity; a test clears both together

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        jd.STATE, jd.NAMES, jd.PROJECTS, km.NAMES = self.saved
        self._reset()
        self.td.cleanup()

    def _write_states(self, sid, rows):
        (jd.STATE / "states" / (sid + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_a_sid_leaving_the_alive_set_loses_its_entry(self):
        self.assertEqual({s["sid"] for s in km._alive_sessions(NOW, self.tmux)}, {SID},
                         "fixture invariant: the alive set holds the live session alone")
        for sid in (SID, DEAD):
            self._write_states(sid, [{"t": T0, "awaiting": True, "why": "a build"}, {"t": T0 + 1, "state": "idle"}])
            self.assertEqual(km._states_awaiting_overlay(sid), {"t": T0, "awaiting": True, "why": "a build"})
        live_key = str(jd.STATE / "states" / (SID + ".jsonl"))
        dead_key = str(jd.STATE / "states" / (DEAD + ".jsonl"))
        self.assertEqual(set(km._states_overlay_cache), {live_key, dead_key})
        s0 = km._states_overlay_report()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertNotIn(dead_key, km._states_overlay_cache, "a sid outside the alive set is dropped")
        self.assertIn(live_key, km._states_overlay_cache, "the alive one stays")
        s1 = km._states_overlay_report()
        self.assertEqual((s1["evict"] - s0["evict"], s1["entries"]), (1, 1))
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertIn(live_key, km._states_overlay_cache, "a second tick keeps the alive entry")
        self.assertEqual(km._states_overlay_report()["evict"], s1["evict"], "nothing to drop: no count")
        # an EMPTY alive set (every session gone; not headless) retires the last entry too
        saved = km._has_tmux
        km._has_tmux = lambda: True
        try:
            km._interrupt_block_tick(NOW, {})
        finally:
            km._has_tmux = saved
        self.assertEqual(km._states_overlay_cache, {}, "the live session left: its entry goes")
        self.assertEqual(km._states_overlay_report()["entries"], 0)

    def test_the_tick_leaves_a_departed_sids_open_fail_episode_alone(self):
        # A dead session's states file is still read after the tick drops its fold entry: build_session
        # serves a dead session kept open as a read-only tab and the scroll-back handler for any sid, and
        # GET /classify answers for any sid, none of them gated on liveness. The stderr line is one per
        # episode for every reader, so the tick's forget must not reset the latch for a sid outside its alive
        # set; a good read is what ends the episode.
        self._write_states(DEAD, [{"t": T0, "awaiting": True, "why": "a build"}])
        self.assertEqual(km._states_awaiting_overlay(DEAD), {"t": T0, "awaiting": True, "why": "a build"})
        dead_path = jd.STATE / "states" / (DEAD + ".jsonl")
        # the shared reader serves an UNCHANGED file's records on an identity hit without opening it, so a
        # permission flip alone is not a read attempt: the file grows first, then becomes unreadable
        with open(dead_path, "a") as f:
            f.write(json.dumps({"t": T0 + 1, "state": "idle"}) + "\n")
        os.chmod(dead_path, 0)
        try:
            if os.access(dead_path, os.R_OK):
                self.skipTest("this user reads through mode 000 (root)")
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertIsNone(km._states_awaiting_overlay(DEAD), "the read failed: no overlay")
            self.assertEqual(err.getvalue().count("unreadable"), 1, "the episode opens with one line")
            km._interrupt_block_tick(NOW, self.tmux)                    # DEAD is outside the tick's alive set
            self.assertIn(str(dead_path), km._states_overlay_failed, "the forget leaves the open episode alone")
            with redirect_stderr(err):
                self.assertIsNone(km._states_awaiting_overlay(DEAD), "a kept-open tab's rebuild reads it again")
            self.assertEqual(err.getvalue().count("unreadable"), 1, "the second read names nothing: the episode is open")
        finally:
            os.chmod(dead_path, 0o644)
        self.assertEqual(km._states_awaiting_overlay(DEAD), {"t": T0, "awaiting": True, "why": "a build"},
                         "readable again: folded from record 0")
        self.assertNotIn(str(dead_path), km._states_overlay_failed, "a good read ends the episode")


class IntrMarksReport(unittest.TestCase):
    """The interrupt-marks memo reports its counters and occupancy the way the states-overlay fold does."""

    def test_the_report_copies_the_counters_and_counts_the_entries(self):
        saved = dict(km._intr_marks_memo_stats), dict(km._intr_marks_memo)
        try:
            km._intr_marks_memo.clear()
            key = (DEAD, "judge")
            km._intr_marks_memo[key] = ([], (0.0, ""), (0, 0))
            rep = km._intr_marks_memo_report()
            self.assertEqual(set(rep), {"hit", "miss", "evict", "entries"})
            self.assertEqual(rep["entries"], 1)
            for k in ("hit", "miss", "evict"):
                self.assertEqual(rep[k], km._intr_marks_memo_stats[k])
                self.assertIsInstance(rep[k], int)
            rep["hit"] = -1
            self.assertNotEqual(km._intr_marks_memo_stats["hit"], -1, "the report is a copy")
            km._intr_marks_forget(set())
            rep2 = km._intr_marks_memo_report()
            self.assertEqual((rep2["entries"], rep2["evict"]), (0, rep["evict"] + 1))
        finally:
            km._intr_marks_memo.clear()
            km._intr_marks_memo.update(saved[1])
            with km._INTR_MARKS_STATS_LOCK:
                km._intr_marks_memo_stats.clear()
                km._intr_marks_memo_stats.update(saved[0])


if __name__ == "__main__":
    unittest.main()
