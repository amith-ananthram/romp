#!/usr/bin/env python3
"""The index tier's caption readers and the re-plan's cleared context read their files once per file state
(2026-09-09): captions/<sid>.jsonl is parsed once and captioned_ids, _live_natoms and session_turn_captions
derive from that parse; goals-archive/<sid>.json is loaded once for readers (load_goal_archive_shared) while
every archiver keeps the fresh loader.

Measured on the maintainer's box (py-spy, the judge tier thread): the three caption readers decoded the whole
file three times per session per index pass (19% of the thread), the archive loader decoded per call (11%).
Pins: parsed once then served; the derived readers equal the direct derivations; an append re-derives, including
one the file clock cannot see; a write landing during the read is served no further than that call; an absent
file is empty and never cached, and so is an existing archive that cannot be read or parsed (the read marked the
running stage incomplete); a malformed line is skipped; the memos are bounded; the archive's writers get a
fresh object; the switched callers; a rebound root forgets; the counters; two fills at the cap on two
threads neither raise nor overflow it.

Synthetic ids under a private synthetic sid; a temp state root."""
import json
import os
import tempfile
import threading
import unittest
from romp_load import load_source
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_file_memos", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-666666666601"
T0 = 1781100000


class _Memo(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_root = jd.STATE
        jd._rebind_state(Path(self.td.name))
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        jd.GOALARCHDIR.mkdir(parents=True, exist_ok=True)
        self.cap = jd.CAPDIR / (SID + ".jsonl")
        self.arch = jd.GOALARCHDIR / (SID + ".json")
        self._reset()

    def tearDown(self):
        jd._rebind_state(self.saved_root)
        self._reset()
        self.td.cleanup()

    @staticmethod
    def _reset():
        jd._CAPTIONS_MEMO.clear(); jd._GOALARCH_MEMO.clear()
        for d in (jd._CAPTIONS_STATS, jd._GOALARCH_STATS):
            for k in d:
                d[k] = 0

    def append(self, *rows, mtime=None):
        with self.cap.open("a") as f:
            for r in rows:
                f.write((json.dumps(r) if isinstance(r, dict) else r) + "\n")
        if mtime is not None:
            os.utime(self.cap, (mtime, mtime))

    def direct(self):
        """The three answers computed the way the readers used to: three passes over the file."""
        rows = []
        for line in self.cap.read_text(errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        done = {o["id"] for o in rows if o.get("id") and not o.get("live")}
        live = {}
        for o in rows:
            if o.get("live") and o.get("id"):
                live[o["id"]] = o.get("natoms", 0)
        caps = sorted((o.get("t", 0), o["caption"]) for o in rows if o.get("grain") == "turn" and o.get("caption"))
        return done, live, [c for _, c in caps]

    def counting(self):
        reads, real = [], Path.read_text

        def spy(p, *a, **k):
            if p in (self.cap, self.arch):
                reads.append(p.name)
            return real(p, *a, **k)
        patcher = patch.object(Path, "read_text", spy)
        patcher.start(); self.addCleanup(patcher.stop)
        return reads


class CaptionsMemo(_Memo):
    ROWS = [{"id": SID + ":s1", "grain": "work", "t": T0, "caption": "wired the banner"},
            {"id": SID + ":t1", "grain": "turn", "t": T0 + 5, "caption": "The banner reconnects."},
            {"id": SID + ":s2", "grain": "work", "t": T0 + 20, "caption": "checking the cap", "live": True, "natoms": 8},
            {"id": SID + ":t0", "grain": "turn", "t": T0 - 100, "caption": "An earlier turn."}]

    def test_parsed_once_and_the_three_readers_equal_the_direct_derivations(self):
        self.append(*self.ROWS)
        expected = self.direct()                          # three passes over the file, the way the readers used to
        reads = self.counting()
        got = (jd.captioned_ids(SID), jd._live_natoms(SID), jd.session_turn_captions(SID))
        self.assertEqual(got, expected)
        self.assertEqual(got[0], {SID + ":s1", SID + ":t1", SID + ":t0"}, "live rows are not done")
        self.assertEqual(got[1], {SID + ":s2": 8})
        self.assertEqual(got[2], ["An earlier turn.", "The banner reconnects."], "oldest first")
        self.assertEqual(reads, [SID + ".jsonl"], "one read of the file for three readers")
        self.assertEqual(jd._CAPTIONS_STATS, {"served": 2, "parsed": 1, "unstatable": 0})

    def test_an_append_re_derives_even_when_the_file_clock_does_not_move(self):
        self.append(*self.ROWS[:2])
        self.assertEqual(jd.captioned_ids(SID), {SID + ":s1", SID + ":t1"})
        before = os.stat(self.cap)
        self.append({"id": SID + ":s2", "grain": "work", "t": T0 + 20, "caption": "checking the cap"}, mtime=before.st_mtime)
        self.assertEqual(os.stat(self.cap).st_mtime, before.st_mtime)
        self.assertIn(SID + ":s2", jd.captioned_ids(SID), "the size moved: parsed again")
        self.assertEqual(jd._CAPTIONS_STATS["parsed"], 2)

    def test_a_live_row_superseded_by_its_final_leaves_the_live_map(self):
        self.append({"id": SID + ":s2", "grain": "work", "t": T0, "caption": "working", "live": True, "natoms": 8})
        self.assertEqual((jd.captioned_ids(SID), jd._live_natoms(SID)), (set(), {SID + ":s2": 8}))
        jd.append_caption(SID, SID + ":s2", "work", T0 + 30, "checked the cap")      # the writer everyone uses
        self.assertEqual(jd.captioned_ids(SID), {SID + ":s2"}, "the final record is seen at once")
        self.assertEqual(jd._live_natoms(SID), {SID + ":s2": 8}, "the live row stays until the file says otherwise")

    def test_a_write_landing_during_the_read_is_served_no_further_than_that_call(self):
        # INTERLEAVED WRITE: a row appended while the file is being read. The key was taken before the read,
        # so whatever the first call saw is cached under a stat the file no longer has; the next call re-parses.
        self.append(*self.ROWS[:1])
        real = Path.read_text
        landed = []

        def read_then_write(p, *a, **k):
            data = real(p, *a, **k)
            if p == self.cap and not landed:
                landed.append(True)
                with p.open("a") as f:
                    f.write(json.dumps(self.ROWS[1]) + "\n")
            return data
        with patch.object(Path, "read_text", read_then_write):
            first = jd.captioned_ids(SID)
        self.assertEqual(first, {SID + ":s1"}, "what the read saw")
        self.assertTrue(landed)
        self.assertEqual(jd.captioned_ids(SID), {SID + ":s1", SID + ":t1"}, "the write moved the stat the second call took")
        self.assertEqual(jd._CAPTIONS_STATS, {"served": 0, "parsed": 2, "unstatable": 0})

    def test_an_absent_file_is_empty_and_never_cached(self):
        self.assertEqual((jd.captioned_ids(SID), jd._live_natoms(SID), jd.session_turn_captions(SID)), (set(), {}, []))
        self.assertEqual(jd._CAPTIONS_STATS, {"served": 0, "parsed": 0, "unstatable": 0})
        self.assertNotIn(SID, jd._CAPTIONS_MEMO)
        self.append(*self.ROWS[:1])
        self.assertEqual(jd.captioned_ids(SID), {SID + ":s1"}, "the first row is seen at once")

    def test_a_malformed_or_non_object_line_is_skipped(self):
        self.append(self.ROWS[0], "{not json", "[1, 2]", "42", self.ROWS[1])
        self.assertEqual(jd.captioned_ids(SID), {SID + ":s1", SID + ":t1"})

    def test_the_memo_is_bounded(self):
        saved = jd._FILE_MEMO_MAX
        jd._FILE_MEMO_MAX = 3
        try:
            for i in range(5):
                sid = "11111111-2222-3333-4444-6666666666%02d" % (10 + i)
                (jd.CAPDIR / (sid + ".jsonl")).write_text(json.dumps({"id": sid + ":s1", "grain": "work", "t": T0, "caption": "x"}) + "\n")
                jd.captioned_ids(sid)
            self.assertEqual(len(jd._CAPTIONS_MEMO), 3, "oldest-inserted out at the cap")
            self.assertNotIn("11111111-2222-3333-4444-666666666610", jd._CAPTIONS_MEMO)
        finally:
            jd._FILE_MEMO_MAX = saved


class GoalArchiveMemo(_Memo):
    def _write(self, nodes, mtime=None):
        self.arch.write_text(json.dumps({"rompUuid": SID, "nodes": nodes, "status": {k: "cleared" for k in nodes}}))
        if mtime is not None:
            os.utime(self.arch, (mtime, mtime))

    @staticmethod
    def _node(nid, text):
        return {"id": nid, "text": text, "parentId": None, "nodeComplete": False, "blocked": False,
                "cleared": True, "trail": [], "t": T0}

    def test_loaded_once_then_served_and_the_writers_get_a_fresh_object(self):
        self._write({SID + ":g1": self._node(SID + ":g1", "Ship the banner")})
        reads = self.counting()
        a = jd.load_goal_archive_shared(SID)
        b = jd.load_goal_archive_shared(SID)
        self.assertIs(a, b, "one object, served")
        self.assertEqual(reads, [SID + ".json"])
        self.assertEqual(set(a["nodes"]), {SID + ":g1"})
        self.assertEqual(jd._GOALARCH_STATS, {"served": 1, "loaded": 1})
        w1, w2 = jd.load_goal_archive(SID), jd.load_goal_archive(SID)
        self.assertIsNot(w1, w2); self.assertIsNot(w1, a)
        self.assertEqual(reads.count(SID + ".json"), 3, "an archiver's loader reads fresh every time, as before")

    def test_a_write_re_derives_even_when_the_file_clock_does_not_move(self):
        self._write({SID + ":g1": self._node(SID + ":g1", "Ship the banner")})
        jd.load_goal_archive_shared(SID)
        before = os.stat(self.arch)
        self._write({SID + ":g1": self._node(SID + ":g1", "Ship the banner"),
                     SID + ":g2": self._node(SID + ":g2", "Retire the cap")}, mtime=before.st_mtime)
        self.assertEqual(set(jd.load_goal_archive_shared(SID)["nodes"]), {SID + ":g1", SID + ":g2"})
        self.assertEqual(jd._GOALARCH_STATS["loaded"], 2)

    def test_a_write_landing_during_the_read_is_served_no_further_than_that_call(self):
        self._write({SID + ":g1": self._node(SID + ":g1", "Ship the banner")})
        real = Path.read_text
        landed = []

        def read_then_write(p, *a, **k):
            data = real(p, *a, **k)
            if p == self.arch and not landed:
                landed.append(True)
                self._write({SID + ":g1": self._node(SID + ":g1", "Ship the banner"),
                             SID + ":g2": self._node(SID + ":g2", "Retire the cap")})
            return data
        with patch.object(Path, "read_text", read_then_write):
            first = jd.load_goal_archive_shared(SID)
        self.assertEqual(set(first["nodes"]), {SID + ":g1"})
        self.assertTrue(landed)
        self.assertEqual(set(jd.load_goal_archive_shared(SID)["nodes"]), {SID + ":g1", SID + ":g2"})
        self.assertEqual(jd._GOALARCH_STATS, {"served": 0, "loaded": 2})

    def test_an_absent_archive_is_the_empty_shape_and_never_cached(self):
        a = jd.load_goal_archive_shared(SID)
        self.assertEqual((a["nodes"], a["status"]), ({}, {}))
        self.assertNotIn(SID, jd._GOALARCH_MEMO)
        self.assertEqual(jd._GOALARCH_STATS, {"served": 0, "loaded": 1})

    def _error_rows(self, err):
        try:
            rows = [json.loads(l) for l in jd.ERRORS.read_text().splitlines() if l.strip()]
        except FileNotFoundError:
            return []
        return [r for r in rows if r.get("err") == err]

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_archive_is_the_empty_shape_and_never_cached(self):
        # the evidence gate's rule: a read that failed marked the running stage incomplete, and a permission fix
        # moves no file key, so a cached failure would be served after the file reads again. Every call reads.
        self._write({SID + ":g1": self._node(SID + ":g1", "Ship the banner")})
        os.chmod(self.arch, 0)
        try:
            reads = self.counting()
            jd._judge_ctx.stage_incomplete = False
            a = jd.load_goal_archive_shared(SID)
            self.assertEqual((a["nodes"], a["status"]), ({}, {}))
            self.assertTrue(jd._judge_ctx.stage_incomplete, "the failed read marks the running stage incomplete")
            self.assertNotIn(SID, jd._GOALARCH_MEMO)
            b = jd.load_goal_archive_shared(SID)
            self.assertEqual((b["nodes"], b["status"]), ({}, {}))
            self.assertNotIn(SID, jd._GOALARCH_MEMO)
            self.assertEqual(reads, [SID + ".json", SID + ".json"], "each call reads the file again")
            self.assertEqual(jd._GOALARCH_STATS, {"served": 0, "loaded": 2})
            self.assertEqual(len(self._error_rows("archive-unreadable")), 1, "one row per failure episode")
        finally:
            os.chmod(self.arch, 0o644)
        c = jd.load_goal_archive_shared(SID)
        self.assertEqual(set(c["nodes"]), {SID + ":g1"}, "readable again with the same key: read, and cached now")
        self.assertIs(jd.load_goal_archive_shared(SID), c)
        self.assertEqual(jd._GOALARCH_STATS, {"served": 1, "loaded": 3})
        self.assertEqual(len(self._error_rows("archive-unreadable")), 1)

    def test_an_archive_that_does_not_parse_is_the_empty_shape_and_never_cached(self):
        self.arch.write_text("{not json")
        a = jd.load_goal_archive_shared(SID)
        self.assertEqual((a["nodes"], a["status"]), ({}, {}))
        self.assertNotIn(SID, jd._GOALARCH_MEMO)
        jd.load_goal_archive_shared(SID)
        self.assertEqual(jd._GOALARCH_STATS, {"served": 0, "loaded": 2})
        self.assertEqual(len(self._error_rows("archive-unreadable")), 1)

    def test_the_readers_take_the_shared_loader_and_the_archivers_keep_the_fresh_one(self):
        src = open(os.path.join(BIN, "romp-judge")).read()
        self.assertIn('arch = load_goal_archive_shared(fsid).get("nodes", {})', src, "the re-plan's cleared context")
        self.assertIn('arch_nodes = (load_goal_archive_shared(fsid) or {}).get("nodes", {})', src, "the override replay's restore membership")
        self.assertIn('r_nodes = dict(load_goal_archive_shared(h["peer"]).get("nodes") or {})', src)
        self.assertIn('snodes = dict(load_goal_archive_shared(o["peer"]).get("nodes") or {})', src)
        self.assertIn('a = archives[sid] = load_goal_archive_shared(sid)', src, "the propagate pass's per-pass archive map reads")
        for writer in ('        arch = load_goal_archive(fsid)\n        a_nodes = arch.setdefault("nodes", {})',
                       '            arch = load_goal_archive(fsid)\n            for nid in back:'):
            self.assertIn(writer, src, "an archiver loads fresh")

    def test_a_rebound_root_forgets_both_memos(self):
        self.append({"id": SID + ":s1", "grain": "work", "t": T0, "caption": "x"})
        self._write({SID + ":g1": self._node(SID + ":g1", "Ship the banner")})
        jd.captioned_ids(SID); jd.load_goal_archive_shared(SID)
        self.assertTrue(jd._CAPTIONS_MEMO and jd._GOALARCH_MEMO)
        other = tempfile.TemporaryDirectory()
        try:
            jd._rebind_state(Path(other.name))
            self.assertEqual((jd._CAPTIONS_MEMO, jd._GOALARCH_MEMO), ({}, {}))
        finally:
            jd._rebind_state(Path(self.td.name))
            other.cleanup()

    def test_the_counters_are_copies(self):
        for fn in (jd.captions_memo_stats, jd.goal_archive_memo_stats):
            s = fn(); k = next(iter(s)); s[k] = 99
            self.assertNotEqual(fn()[k], 99)


class _HookedMemo(dict):
    """A dict with an optional hook before dict.pop and one before dict.__setitem__; every other operation
    (len, in, get, iter, clear) is the plain dict's, and so are these two when no hook is set. The eviction
    tests rebind the module's archive memo to one so a thread can be held at a chosen point inside _memo_put:
    about to pop its chosen victim, or about to insert after its eviction."""
    before_pop = None
    before_set = None

    def pop(self, key, *default):
        if self.before_pop is not None:
            self.before_pop(key)
        return dict.pop(self, key, *default)

    def __setitem__(self, key, val):
        if self.before_set is not None:
            self.before_set(key)
        dict.__setitem__(self, key, val)


class MemoEviction(_Memo):
    """The eviction at the cap is filled from many threads (every load_goals that replays a restore row, the
    index tier, the planner workers). Two fills for new sids at the cap must neither raise nor overflow the
    cap. Both tests run real fills through load_goal_archive_shared with the cap patched to 4, and make the
    interleaving deterministic with hooks on the memo: the first holds both threads at the point where both
    have chosen the same victim (before the pop), the second holds one thread between its pop and its insert
    (before the insert). Without a lock around the eviction and the insert, the first raises KeyError out of
    the fill (both pop the same key) and the second leaves five entries; a lock around the pop alone still
    fails the second, since the other thread measures the size in the gap. Each hold is bounded by a timeout
    (about one second on the green path, where the lock keeps the other thread out until the hold expires)
    and every thread is joined under a wall-clock cap that fails loudly. Only the red side depends on timing:
    without the lock the second thread must reach its hold within the first's one-second wait, so a heavily
    loaded machine can let an unlocked judge pass; the green path holds under any load."""
    CAP = 4
    JOIN_S = 10.0
    HOLD_S = 1.0
    OLD = ["11111111-2222-3333-4444-6666666666%02d" % (20 + i) for i in range(4)]
    NEW1 = "11111111-2222-3333-4444-666666666631"
    NEW2 = "11111111-2222-3333-4444-666666666632"

    def setUp(self):
        super().setUp()
        self.memo = _HookedMemo()
        for sid in self.OLD:
            self.memo[sid] = ((1, 2, 3), {})            # placeholder entries: the memo is at the cap
        for sid in (self.NEW1, self.NEW2):              # real files, so _file_key is a tuple and _memo_put is reached
            (jd.GOALARCHDIR / (sid + ".json")).write_text(
                json.dumps({"rompUuid": sid, "nodes": {}, "status": {}}))
        self.saved = (jd._GOALARCH_MEMO, jd._FILE_MEMO_MAX)
        jd._GOALARCH_MEMO, jd._FILE_MEMO_MAX = self.memo, self.CAP

    def tearDown(self):
        jd._GOALARCH_MEMO, jd._FILE_MEMO_MAX = self.saved   # the module's own dict back before the rebind clears it
        super().tearDown()

    def _run(self, *targets):
        """Run each target on its own thread; the exceptions they raised, in the order the threads were started.
        A thread still alive at the cap is a failure, not a hang: the threads are daemons, so a fill that
        deadlocks fails the check below without then holding the process open at exit."""
        errors = [[] for _ in targets]

        def wrap(i, fn):
            def go():
                try:
                    fn()
                except BaseException as e:      # noqa: BLE001 - the test reports what the fill raised
                    errors[i].append(e)
            return go
        threads = [threading.Thread(target=wrap(i, fn), name="fill-%d" % i, daemon=True)
                   for i, fn in enumerate(targets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.JOIN_S)
        self.assertFalse([t.name for t in threads if t.is_alive()], "a fill did not finish within the cap")
        return [e for es in errors for e in es]

    def _check_shape(self):
        self.assertEqual(len(self.memo), self.CAP, "the cap holds")
        self.assertIn(self.NEW1, self.memo); self.assertIn(self.NEW2, self.memo)
        for ent in self.memo.values():
            self.assertIsInstance(ent, tuple); self.assertEqual(len(ent), 2)
        self.assertEqual(jd._GOALARCH_STATS["served"], 0, "both calls were fills")   # never incremented here, so
        #                                                   race-free. `loaded` is unlocked by design (a lost
        #                                                   increment under-counts a diagnostic) and is pinned
        #                                                   only where the two increments are ordered.

    def test_two_fills_at_the_cap_that_chose_the_same_victim_evict_without_raising(self):
        # Both threads compute next(iter(memo)) before either pops: the hook before dict.pop parks each at a
        # two-party barrier. Without the lock both arrive (the dict is unchanged while the first waits), the
        # barrier releases both, and the second dict.pop of the same key raises KeyError out of the fill. With
        # the lock the first holds it through the barrier's timeout, pops and inserts; the second then chooses
        # the next oldest key, its barrier call raises BrokenBarrierError at once (swallowed), and it evicts
        # and inserts in turn.
        barrier = threading.Barrier(2)

        def hold(_key):
            try:
                barrier.wait(timeout=self.HOLD_S)
            except threading.BrokenBarrierError:
                pass
        self.memo.before_pop = hold
        errors = self._run(lambda: jd.load_goal_archive_shared(self.NEW1),
                           lambda: jd.load_goal_archive_shared(self.NEW2))
        self.assertEqual(errors, [], "a fill raised out of the eviction: %r" % errors)
        self._check_shape()

    def test_a_fill_landing_between_another_fills_eviction_and_insert_does_not_overflow_the_cap(self):
        # The first thread is held after its pop and before its insert (the hook before dict.__setitem__, on
        # that thread only); the second fills while it waits. Without the lock the second sees three entries,
        # skips the eviction and inserts, and the first's insert then makes five. With the lock the second
        # blocks until the first's hold expires and it inserts, then evicts one itself. A lock around the pop
        # alone leaves the gap open and fails here too: the hold sits at the insert, outside such a lock.
        first = []
        evicted, other_done = threading.Event(), threading.Event()

        def gap(_key):
            if first and threading.get_ident() == first[0]:
                evicted.set()
                other_done.wait(timeout=self.HOLD_S)
        self.memo.before_set = gap

        def fill_first():
            first.append(threading.get_ident())
            jd.load_goal_archive_shared(self.NEW1)

        def fill_second():
            self.assertTrue(evicted.wait(timeout=self.JOIN_S), "the first fill never reached its insert")
            try:
                jd.load_goal_archive_shared(self.NEW2)
            finally:
                other_done.set()
        errors = self._run(fill_first, fill_second)
        self.assertEqual(errors, [], "a fill raised: %r" % errors)
        self.assertTrue(evicted.is_set(), "the first fill evicted and reached its insert")
        self._check_shape()
        self.assertEqual(jd._GOALARCH_STATS["loaded"], 2)   # exact here: the second fill starts after the first's
        #                                                     increment, so the unlocked counter is ordered


if __name__ == "__main__":
    unittest.main()
