#!/usr/bin/env python3
"""The shared read-only goal-store cache (kernel/judge.py load_goals_shared).

The pusher thread's read-only sites used to parse a goal store on every load; load_goals_shared serves
them one deep-frozen parsed object per file version, verified on every call by the store's identity
(inode, mtime_ns, size) PLUS a byte compare, the override journal's identity and the goals-archive's.
Writers stay on load_goals. All fixtures SYNTHETIC; this module uses a PRIVATE synthetic sid and a
fresh state root per test, so another module's journaled overrides against a shared placeholder sid
cannot land on its nodes mid-test."""
import contextlib
import copy
import errno
import io
import json
import os
import pickle
import re
import tempfile
import threading
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "77777777-8888-4999-aaaa-bbbbbbbbbbbb"       # private to this module (synthetic)
NOW = 1781100000
T0 = NOW - 3600


def _norm(store):
    """A store's content as plain JSON with the forensic arrival stamps dropped: record_verdict stamps `at`
    from the clock at replay time, so two loads a second apart differ there and nowhere else."""
    out = json.loads(json.dumps({k: v for k, v in store.items() if k != "_baseRev"}))
    for nd in (out.get("nodes") or {}).values():
        for e in nd.get("log") or []:
            e.pop("at", None)
    return out


class SharedStoreCache(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))         # also clears the cache and lifts a previous test's off switch
        self.stats0 = jd.shared_store_stats()

    def tearDown(self):
        jp = jd._overrides_dir() / (SID + ".jsonl")
        if jp.exists():
            os.chmod(jp, 0o600)
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _nid(self, n):
        return "%s:g%d" % (SID, n)

    def _seed(self, text="A goal"):
        """One working top goal, published; returns the store path."""
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": text}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        return jd.GOALDIR / (SID + ".json")

    def _delta(self, key):
        return jd.shared_store_stats()[key] - self.stats0[key]

    def _errors(self):
        try:
            return [json.loads(l) for l in jd.ERRORS.read_text().splitlines() if l.strip()]
        except FileNotFoundError:
            return []

    # ── identity, invalidation ────────────────────────────────────────────────────────────────────────

    def test_two_loads_of_an_unchanged_store_return_one_object_and_parse_once(self):
        self._seed()
        fills, private = [], []
        o_freeze, o_load = jd._freeze_store, jd.load_goals
        jd._freeze_store = lambda store, fsid=None: (fills.append(1), o_freeze(store, fsid))[1]
        jd.load_goals = lambda fsid: (private.append(fsid), o_load(fsid))[1]
        try:
            a = jd.load_goals_shared(SID)
            b = jd.load_goals_shared(SID)
        finally:
            jd._freeze_store, jd.load_goals = o_freeze, o_load
        self.assertIs(a, b, "an unchanged file version is one shared object")
        self.assertEqual(len(fills), 1, "one parse for two loads")
        self.assertEqual((self._delta("miss"), self._delta("hit")), (1, 1))
        self.assertEqual(private, [], "the miss and the hit are both answered here; load_goals ran for neither")
        self.assertIsInstance(a, jd.FrozenStore)
        self.assertEqual(jd.shared_store_stats()["entries"], 1)
        self.assertEqual(jd.shared_store_stats()["bytes"], os.path.getsize(jd.GOALDIR / (SID + ".json")))

    def test_the_shared_view_is_the_writer_loaders_view(self):
        self._seed()
        shared, writer = jd.load_goals_shared(SID), jd.load_goals(SID)
        self.assertEqual(_norm(shared), _norm(writer), "same guard, same replay, same rollup")
        self.assertEqual(shared["_baseRev"], writer["_baseRev"], "the CAS base rides the shared view too, so a "
                         "shallow copy handed to save_goals still meets the CAS")
        self.assertEqual(json.dumps(shared), json.dumps(writer), "json.dumps sees a plain store")
        nid = self._nid(1)
        self.assertIsInstance(shared, dict)
        self.assertIsInstance(shared["nodes"][nid], jd.GuardedNode)
        self.assertIsInstance(shared["nodes"][nid]["log"], list)
        self.assertEqual(type(writer), dict, "the writer's loader hands back a plain, mutable store")
        writer["nodes"][nid]["text"] = "edited"        # and it takes a write

    def test_a_publish_of_a_changed_writer_copy_invalidates(self):
        self._seed()
        a = jd.load_goals_shared(SID)
        w = jd.load_goals(SID)
        jd.record_verdict(w, w["nodes"][self._nid(1)], "romp", "block", T0 + 30, why="needs a decision")
        jd.save_goals(SID, w)
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "save_goals pops its path after the rename")
        b = jd.load_goals_shared(SID)
        self.assertIsNot(a, b)
        self.assertEqual(b["rev"], a["rev"] + 1)
        self.assertTrue(b["nodes"][self._nid(1)]["blocked"])
        self.assertFalse(a["nodes"][self._nid(1)].get("blocked"), "the old object is untouched")

    def test_a_journal_append_replays_onto_the_view_and_the_writer_loader_agrees(self):
        self._seed()
        a = jd.load_goals_shared(SID)
        self.assertFalse(a["nodes"][self._nid(1)].get("nodeComplete"))
        jd.append_override(SID, self._nid(1), "resolve", T0 + 60)
        b = jd.load_goals_shared(SID)
        self.assertIsNot(a, b, "a journal write is a new key: a new object")
        self.assertTrue(b["nodes"][self._nid(1)]["nodeComplete"], "the journaled resolve is in the view")
        self.assertIn(self._nid(1), b.get("confirming") or [], "and the rollup followed it (done, awaiting settle)")
        self.assertEqual(_norm(b), _norm(jd.load_goals(SID)), "the writer's loader sees the same view")
        self.assertIs(jd.load_goals_shared(SID), b, "and the replayed view is memoized under the journal's key")

    def test_a_restore_row_then_an_archive_change_refill(self):
        self._seed()
        nid2 = self._nid(2)
        payload = {"id": nid2, "text": "Restored goal", "t": T0, "mt": T0, "parentId": None, "log": [], "why": "x"}
        jd.append_restore(SID, {nid2: payload}, {nid2: "working"}, T0 + 10)
        a = jd.load_goals_shared(SID)
        self.assertIn(nid2, a["nodes"], "the restore row re-inserts a node neither file has")
        # the node is re-cleared into the archive: the archive publish moves the archive key, and the replay
        # now defers to the archive (nothing lost) — the next load is a refill without the node
        jd.save_goal_archive(SID, {"rompUuid": SID, "nodes": {nid2: payload}, "status": {}})
        b = jd.load_goals_shared(SID)
        self.assertIsNot(a, b)
        self.assertNotIn(nid2, b["nodes"])
        self.assertEqual(self._delta("miss"), 2)

    def test_a_fill_under_a_moving_archive_is_served_but_not_published(self):
        self._seed()
        calls = []
        o_ak = jd._archive_key
        jd._archive_key = lambda fsid: (calls.append(1), None if len(calls) == 1 else ("moved",))[1]
        try:
            a = jd.load_goals_shared(SID)
        finally:
            jd._archive_key = o_ak
        self.assertIsInstance(a, jd.FrozenStore, "the caller gets a correct private view")
        self.assertEqual(self._delta("refuse"), 1)
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "not published: the next call refills")

    def test_an_equal_size_in_place_rewrite_with_a_pinned_mtime_is_re_parsed(self):
        # The identity blind spot: same inode, same size, same mtime_ns, different bytes. Nothing in romp
        # writes a store in place (save_goals renames), so this stands in for an equal-size republish
        # onto a recycled inode inside one clock tick. The byte compare catches it.
        p = self._seed()
        a = jd.load_goals_shared(SID)
        key0 = jd._file_key(str(p))
        st = os.stat(p)
        raw = p.read_bytes()
        self.assertIn(b'"text": "A goal"', raw)
        with open(p, "r+b") as f:                         # in place: the inode stays
            f.write(raw.replace(b'"text": "A goal"', b'"text": "B goal"'))
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # pin the stamp: the identity is unchanged
        self.assertEqual(jd._file_key(str(p)), key0, "the stat key did not move")
        b = jd.load_goals_shared(SID)
        self.assertIsNot(a, b)
        self.assertEqual(b["nodes"][self._nid(1)]["text"], "B goal")
        self.assertEqual(self._delta("compare_miss"), 1)
        self.assertIs(jd.load_goals_shared(SID), b, "the new bytes are memoized")

    def test_eight_concurrent_loaders_receive_one_object(self):
        """A smoke test over real threads: eight loaders of one unchanged store all receive the one published
        object, and the cache holds one entry. The scheduler decides how many of the eight fill; the dup path
        itself, with its exact counts, is forced in test_a_fill_that_loses_the_race_returns_the_published_object.

        The counts asserted here hold for every interleaving, from load_goals_shared's fill path. A call on an
        unchanged file counts exactly one of hit (an entry with its keys and bytes) or miss (no entry yet); a
        compare_miss needs other bytes and a refuse a moved archive, so neither occurs. Every miss reaches the
        publish step under the lock: the first publishes, and each later one finds that entry and counts a dup,
        so there is one publish and dup == miss - 1. A thread that reads the map after the publish hits while
        another is still mid-fill, so any split of hit and miss with miss >= 1 is legal. (The sum miss + hit +
        dup this test once asserted read 9 for eight loads whenever two fills overlapped: the 2026-09-07 CI
        failure, on a branch that predated the dup-aware arithmetic.) The counters are process-global; the
        loader is pointed at this test's private state root, where the only store is the one seeded here."""
        self._seed()
        n = 8
        bar = threading.Barrier(n)
        out, errs = [], []

        def go():
            try:
                bar.wait(30)                             # bounds on a hang, not a schedule: a loaded box can
                out.append(jd.load_goals_shared(SID))    # take seconds to start eight threads
            except Exception as e:                       # pragma: no cover — a failure here is the finding
                errs.append(e)
        ts = [threading.Thread(target=go) for _ in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        self.assertEqual(errs, [])
        self.assertEqual(len(out), n)
        self.assertIsInstance(out[0], jd.FrozenStore)
        for o in out[1:]:
            self.assertIs(o, out[0], "one object for every reader")
        self.assertEqual(jd.shared_store_stats()["entries"], 1, "one publish; a fill that lost the race published nothing")
        hit, miss, dup = self._delta("hit"), self._delta("miss"), self._delta("dup")
        self.assertGreaterEqual(miss, 1, "the first call found no entry")
        self.assertEqual(hit + miss, n, "each call counted once, as a hit or as a miss")
        self.assertEqual(dup, miss - 1, "one miss published; every other miss found its entry")
        self.assertEqual((self._delta("compare_miss"), self._delta("refuse")), (0, 0),
                         "neither is reachable on an unchanged file with an unmoved archive")

    def test_a_fill_that_loses_the_race_returns_the_published_object(self):
        # The dup path, forced: inside the first fill's freeze a second loader runs the whole fill and
        # publishes. The first then finds its own version published under equal keys and bytes, counts a
        # dup and hands back the published object rather than its private freeze.
        self._seed()
        inner = []
        o_freeze = jd._freeze_store

        def racing_freeze(store, fsid=None):
            jd._freeze_store = o_freeze                    # the inner fill freezes and publishes normally
            inner.append(jd.load_goals_shared(SID))
            return o_freeze(store, fsid)
        jd._freeze_store = racing_freeze
        try:
            outer = jd.load_goals_shared(SID)
        finally:
            jd._freeze_store = o_freeze
        self.assertEqual(len(inner), 1)
        self.assertIsInstance(inner[0], jd.FrozenStore)
        self.assertIs(outer, inner[0], "the fill that lost the race returns the winner's object")
        self.assertEqual((self._delta("miss"), self._delta("dup"), self._delta("hit")), (2, 1, 0))
        self.assertEqual(jd.shared_store_stats()["entries"], 1, "one entry: the loser published nothing")
        self.assertIs(jd.load_goals_shared(SID), inner[0], "and the next call hits it")

    def test_rebind_state_clears(self):
        self._seed()
        jd.load_goals_shared(SID)
        self.assertEqual(jd.shared_store_stats()["entries"], 1)
        jd._rebind_state(Path(self.td.name))
        self.assertEqual(jd.shared_store_stats()["entries"], 0)

    def test_migrate_all_stores_clears(self):
        self._seed()
        jd.load_goals_shared(SID)
        jd.migrate_all_stores()
        self.assertEqual(jd.shared_store_stats()["entries"], 0)

    def test_evict_absent_drops_a_removed_store(self):
        p = self._seed()
        jd.load_goals_shared(SID)
        os.unlink(p)
        self.assertEqual(jd._shared_evict_absent(), 1)
        self.assertEqual(jd.shared_store_stats()["entries"], 0)
        self.assertEqual(self._delta("evict"), 1)

    def test_a_removed_store_read_as_absent_drops_its_entry(self):
        # a store file gone at the read drops its stale entry at once (the compaction sweep would evict it
        # later, and a recycled inode is caught by the byte compare either way: dead weight, not a wrong answer)
        p = self._seed()
        jd.load_goals_shared(SID)
        self.assertEqual(jd.shared_store_stats()["entries"], 1)
        os.unlink(p)
        s = jd.load_goals_shared(SID)
        self.assertIs(type(s), dict, "the fresh store: private, nothing to share")
        self.assertEqual(self._delta("absent"), 1)
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "the removed store's entry went with it")

    def test_the_cache_exports_no_uncalled_key_reader(self):
        # store_key (a fresh stat of the store's identity) shipped with no caller: the two kernel view signatures
        # that stat the store carry (mtime, size) inside larger multi-file keys and are not its consumer, so it
        # is gone rather than kept ahead of a use (review find, 2026-09-08). _file_key stays: the cache's own.
        self.assertFalse(hasattr(jd, "store_key"))

    def test_the_two_kinds_this_cache_files_are_documented(self):
        # docs/judges.md lists the judge-errors kinds; the two the frozen guard files were missing from it
        # (review find, 2026-09-08)
        doc = (Path(BIN).parent / "docs" / "judges.md").read_text()
        for kind in ("frozen-store-write", "frozen-store-save"):
            self.assertIn(kind, doc, "%s is documented" % kind)

    # ── the degraded inputs ──────────────────────────────────────────────────────────────────────────

    def test_an_absent_store_is_the_fresh_store_and_is_not_memoized(self):
        private = []
        o_load = jd.load_goals
        jd.load_goals = lambda fsid: (private.append(fsid), o_load(fsid))[1]
        try:
            s = jd.load_goals_shared(SID)
        finally:
            jd.load_goals = o_load
        self.assertEqual(type(s), dict, "nothing to share: load_goals' private fresh store")
        self.assertEqual((s["nodes"], s["_baseRev"], s["rompUuid"]), ({}, 0, SID))
        self.assertEqual(self._delta("absent"), 1)
        self.assertEqual(jd.shared_store_stats()["entries"], 0)
        self.assertEqual(private, [SID], "handed to load_goals, exactly once: one read per call either way")

    def test_a_corrupt_store_is_handed_to_load_goals_and_quarantined_once(self):
        # The shared loader never keeps a fresh store for bytes the writer's loader would move aside: bytes
        # that do not parse go to load_goals, which quarantines the file (one stderr line, one
        # store-quarantined row) and answers the legitimate fresh store. The path then reads as absent, so
        # nothing is cached for it and the next call is the absent-store answer.
        p = self._seed()
        p.write_bytes(b"{not a store")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = jd.load_goals_shared(SID)
            b = jd.load_goals_shared(SID)
        self.assertEqual((a["nodes"], a["_baseRev"]), ({}, 0), "load_goals' fresh store, once the bad bytes are aside")
        self.assertEqual(type(a), dict)
        self.assertIsNot(a, b, "a fresh store each time (private, mutable)")
        self.assertNotIn("_unread", a, "legitimately fresh: the evidence is preserved aside")
        self.assertFalse(p.exists(), "the corrupt file was moved aside")
        self.assertEqual(len(list(jd.GOALDIR.glob(SID + ".json.corrupt-*"))), 1)
        rows = [r for r in self._errors() if r["err"] == "store-quarantined"]
        self.assertEqual([r["fsid"] for r in rows], [SID], "one row, load_goals' own")
        self.assertIn("could not be parsed", err.getvalue())
        self.assertEqual((self._delta("corrupt"), self._delta("absent")), (1, 1), "handed over once; then the path is absent")
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "nothing cached for a file that did not parse")

    def _eio_on_open(self, path):
        orig_open = os.open

        def faulting_open(p, *a, **kw):
            if os.fspath(p) == str(path):
                raise OSError(errno.EIO, "Input/output error", str(path))
            return orig_open(p, *a, **kw)
        return mock.patch.object(os, "open", faulting_open)

    def test_a_read_fault_raises_from_load_goals_shared_and_caches_nothing(self):
        # The shared loader raises a read fault exactly as load_goals does: no empty store, no stale view,
        # and the entry for the path is dropped. The pusher's builders take it through
        # load_goals_shared_or_fault, which files the fault once per episode (the next test).
        p = self._seed()
        self.assertIsInstance(jd.load_goals_shared(SID), jd.FrozenStore)
        self.assertEqual(jd.shared_store_stats()["entries"], 1)
        with self._eio_on_open(p):
            with self.assertRaises(OSError) as cm:
                jd.load_goals_shared(SID)
        self.assertEqual(cm.exception.errno, errno.EIO, "the fault itself, at the open")
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "nothing cached for a file that did not read")
        self.assertIsInstance(jd.load_goals_shared(SID), jd.FrozenStore, "reads again once the fault clears")
        orig_read = jd._disk_read

        def faulting_read(fd, path_s):
            if path_s == str(p):
                raise OSError(errno.EIO, "Input/output error", path_s)
            return orig_read(fd, path_s)
        with mock.patch.object(jd, "_disk_read", faulting_read):
            self.assertRaises(OSError, jd.load_goals_shared, SID)
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "...and at the read: a warm entry is re-read every call")
        self.assertEqual(self._delta("absent"), 0, "a fault is not an absent store")

    def test_load_goals_shared_or_fault_files_one_row_per_episode(self):
        p = self._seed()
        store, fault = jd.load_goals_shared_or_fault(SID)
        self.assertIsInstance(store, jd.FrozenStore)
        self.assertIsNone(fault)
        with self._eio_on_open(p):
            for _ in range(3):
                store, fault = jd.load_goals_shared_or_fault(SID)
                self.assertIsNone(store, "never an empty store")
                self.assertEqual(fault.errno, errno.EIO)
        rows = [r for r in self._errors() if r["err"] == "store-unreadable"]
        self.assertEqual([r["fsid"] for r in rows], [SID], "one row for the episode, not one per call")
        store, fault = jd.load_goals_shared_or_fault(SID)
        self.assertIsInstance(store, jd.FrozenStore, "the episode ends on the read")
        with self._eio_on_open(p):
            jd.load_goals_shared_or_fault(SID)
        self.assertEqual(len([r for r in self._errors() if r["err"] == "store-unreadable"]), 2, "a new episode files again")

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_journal_is_served_uncached_and_keeps_logging(self):
        self._seed()
        jd.append_override(SID, self._nid(1), "resolve", T0 + 60)
        jp = jd._overrides_dir() / (SID + ".jsonl")
        os.chmod(jp, 0)
        a = jd.load_goals_shared(SID)
        b = jd.load_goals_shared(SID)
        self.assertEqual(type(a), dict, "load_goals' own result, private")
        self.assertIsNot(a, b)
        self.assertEqual(a.get("_unread"), "journal", "load_goals' mark rides along: the view is not what the files say")
        self.assertEqual(self._delta("unreadable_journal"), 2)
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "never memoized while the journal cannot be read")
        rows = [r for r in self._errors() if r["err"] == "history-unreadable"]
        self.assertEqual(len(rows), 2, "the loud row is filed on every load, never silenced by a cache")
        os.chmod(jp, 0o600)
        c = jd.load_goals_shared(SID)
        self.assertIsInstance(c, jd.FrozenStore)
        self.assertTrue(c["nodes"][self._nid(1)]["nodeComplete"], "once it reads, the replayed view is shared")

    def test_a_store_the_replay_marked_unread_is_never_published(self):
        # The fill hands the journal's rows to _replay_overrides as `lines`, so the one path that marks a
        # store `_unread` inside the replay (the journal exists and cannot be read) is not reachable from
        # it — the fill's _journal_read raises first and load_goals answers. The guard after _finish_load
        # holds the invariant at the write moment anyway: a marked store is a fallback view, and a fallback
        # view is never published as the disk's content. Stand in for a marker with a stub replay.
        self._seed()

        def marking_replay(fsid, store, lines=None):
            store["_unread"] = "journal"
            return False
        o_rp = jd._replay_overrides
        jd._replay_overrides = marking_replay
        try:
            a = jd.load_goals_shared(SID)
        finally:
            jd._replay_overrides = o_rp
        self.assertEqual(type(a), dict, "served private and mutable, like load_goals' fallback")
        self.assertEqual(a.get("_unread"), "journal")
        self.assertEqual(jd.shared_store_stats()["entries"], 0, "not published")
        self.assertEqual(self._delta("unreadable_journal"), 1)
        self.assertIsInstance(jd.load_goals_shared(SID), jd.FrozenStore, "the real replay publishes")
        self.assertNotIn("_unread", jd.load_goals_shared(SID))

    def test_a_malformed_journal_row_raises_in_both_loaders(self):
        self._seed()
        jp = jd._overrides_dir() / (SID + ".jsonl")
        jp.parent.mkdir(parents=True, exist_ok=True)
        with jp.open("a") as f:
            f.write(json.dumps({"node": self._nid(1), "op": "resolve", "t": "not-a-time"}) + "\n")
        with self.assertRaises(ValueError):
            jd.load_goals(SID)
        with self.assertRaises(ValueError):
            jd.load_goals_shared(SID)
        self.assertEqual(jd.shared_store_stats()["entries"], 0)

    # ── the freeze ───────────────────────────────────────────────────────────────────────────────────

    def test_every_write_path_raises(self):
        self._seed()
        w = jd.load_goals(SID)                                # a diary row, so the log has an entry to write into
        jd.record_verdict(w, w["nodes"][self._nid(1)], "romp", "block", T0 + 30, why="needs a decision")
        jd.save_goals(SID, w)
        s = jd.load_goals_shared(SID)
        nid = self._nid(1)
        nd = s["nodes"][nid]
        self.assertTrue(nd["log"], "the fixture's log is non-empty")
        writes = {
            "top-level assignment": lambda: s.__setitem__("x", 1),
            "top-level deletion": lambda: s.__delitem__("seq"),
            "top-level setdefault": lambda: s.setdefault("seams", []),
            "top-level pop": lambda: s.pop("seq"),
            "top-level popitem": lambda: s.popitem(),
            "top-level update": lambda: s.update({"x": 1}),
            "top-level clear": lambda: s.clear(),
            "top-level |=": lambda: s.__ior__({"x": 1}),
            "nodes[nid] =": lambda: s["nodes"].__setitem__("n", {}),
            "del nodes[nid]": lambda: s["nodes"].__delitem__(nid),
            "node protected key": lambda: nd.__setitem__("blocked", True),
            "node unprotected key": lambda: nd.__setitem__("text", "y"),
            "node setdefault": lambda: nd.setdefault("log", []),
            "node pop": lambda: nd.pop("text"),
            "node update": lambda: nd.update(text="y"),
            "status[nid] =": lambda: s["status"].__setitem__(nid, "completed"),
            "placements write": lambda: s["placements"].__setitem__("seg", nid),
            "log append": lambda: nd["log"].append({"kind": "done"}),
            "log +=": lambda: nd["log"].__iadd__([1]),
            "log item assignment": lambda: nd["log"].__setitem__(0, {}),
            "log del": lambda: nd["log"].__delitem__(0),
            "log pop": lambda: nd["log"].pop(),
            "log clear": lambda: nd["log"].clear(),
            "log sort": lambda: nd["log"].sort(key=str),
            "log reverse": lambda: nd["log"].reverse(),
            "log insert": lambda: nd["log"].insert(0, {}),
            "log extend": lambda: nd["log"].extend([{}]),
            "log entry write": lambda: nd["log"][0].__setitem__("why", "y"),
            "rollup_status(shared)": lambda: jd.rollup_status(s, session_closed=False),
        }
        for name, fn in writes.items():
            with self.assertRaises(jd.FrozenStoreError, msg=name):
                fn()
            self.assertIsInstance(jd.FrozenStoreError("x"), TypeError)
        with jd._authority():                                # the diary layer's token does not unlock it
            with self.assertRaises(jd.FrozenStoreError):
                nd["log"].append({"kind": "done"})
            with self.assertRaises(jd.FrozenStoreError):
                jd.record_verdict(s, nd, "romp", "block", T0 + 30, why="x")
        self.assertEqual(_norm(s), _norm(jd.load_goals(SID)), "the shared object is exactly as loaded")

    def test_save_goals_refuses_the_shared_store_and_files_a_row(self):
        p = self._seed()
        s = jd.load_goals_shared(SID)
        st0 = os.stat(p)
        temps = []
        o_tmp = jd._publish_tmp
        jd._publish_tmp = lambda d, fsid: (temps.append(fsid), o_tmp(d, fsid))[1]
        try:
            with self.assertRaises(jd.FrozenStoreError):
                jd.save_goals(SID, s)
            with self.assertRaises(jd.FrozenStoreError):
                jd.save_goals(SID, dict(s))               # a shallow copy still carries the shared nodes map
        finally:
            jd._publish_tmp = o_tmp
        rows = [r for r in self._errors() if r["err"] == "frozen-store-save"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["fsid"], SID)
        st1 = os.stat(p)
        self.assertEqual((st1.st_ino, st1.st_mtime_ns, st1.st_size), (st0.st_ino, st0.st_mtime_ns, st0.st_size),
                         "nothing was published: the file's identity is unchanged across both refusals")
        self.assertEqual(temps, [], "the refusal comes before the publish path: no temp file was opened for either save")
        self.assertEqual(self._delta("poisoned"), 0, "a refused save is not a write to the shared object")
        self.assertEqual(jd.shared_store_stats()["off"], 0)
        self.assertTrue(p.exists())

    def test_a_write_attempt_switches_the_cache_off_loudly_and_readers_keep_reading(self):
        self._seed()
        s = jd.load_goals_shared(SID)
        with self.assertRaises(jd.FrozenStoreError):
            s["status"][self._nid(1)] = "completed"
        st = jd.shared_store_stats()
        self.assertEqual((st["off"], self._delta("poisoned")), (1, 1))
        rows = [r for r in self._errors() if r["err"] == "frozen-store-write"]
        self.assertEqual(len(rows), 1, "one row for the first attempt")
        self.assertIn("test_judge_store_cache.py", rows[0]["note"], "the row names the writing site")
        self.assertIn("assignment of", rows[0]["note"])
        with self.assertRaises(jd.FrozenStoreError):
            s["nodes"][self._nid(1)]["text"] = "again"
        self.assertEqual(len([r for r in self._errors() if r["err"] == "frozen-store-write"]), 1,
                         "later attempts count (poisoned) but do not repeat the row")
        self.assertEqual(self._delta("poisoned"), 2)
        f = jd.load_goals_shared(SID)
        self.assertEqual(type(f), dict, "with the cache off every reader takes its own writer-style load")
        self.assertEqual(self._delta("fallback"), 1)
        self.assertEqual(_norm(f), _norm(s), "and reads the same view")
        jd._shared_clear()
        self.assertIsInstance(jd.load_goals_shared(SID), jd.FrozenStore, "a clear lifts the switch")

    def test_the_write_guard_row_names_the_site_chain_and_the_sid(self):
        # The row is the only pointer to the misuse, so it must name the site to fix. A kernel-side reader
        # that hands the shared view to a judge helper which writes (rollup_status here) gets a two-frame
        # chain, outermost first: the frame outside the judge module, then the judge frame that wrote. The
        # sid comes from the FrozenStore (_fsid, set by _freeze_store) or a node's id; a nested map or list
        # carries no way back to its store and reports "".
        self._seed()
        nid = self._nid(1)
        here, judge = os.path.basename(__file__), os.path.basename(jd.__file__)

        def _kernel_side_reader(store):                    # stands in for a wired kernel site
            return jd.rollup_status(store, session_closed=False)
        with self.assertRaises(jd.FrozenStoreError):
            _kernel_side_reader(jd.load_goals_shared(SID))
        rows = [r for r in self._errors() if r["err"] == "frozen-store-write"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fsid"], SID, "the row names the store")
        m = re.search(r" at (\S+):(\d+) (\w+)\(\) -> (\S+):(\d+) (\w+)\(\) ", rows[0]["note"])
        self.assertIsNotNone(m, rows[0]["note"])
        self.assertEqual((m.group(1), m.group(3)), (here, "_kernel_side_reader"), "first the site to fix")
        self.assertEqual(m.group(4), judge, "then the judge helper that wrote")
        self.assertNotIn(m.group(6), jd._FROZEN_INTERNAL, "a real helper, not a frozen class's own method")
        # a node write from outside the judge module: one frame, and the sid read off the node's id
        jd._shared_clear()
        with self.assertRaises(jd.FrozenStoreError):
            jd.load_goals_shared(SID)["nodes"][nid]["text"] = "y"
        rows = [r for r in self._errors() if r["err"] == "frozen-store-write"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["fsid"], SID)
        self.assertNotIn(" -> ", rows[1]["note"], "the writer is outside the judge module: one frame is the site")
        self.assertRegex(rows[1]["note"], r" at %s:\d+ %s\(\) " % (re.escape(here), self._testMethodName))
        # a TOP-LEVEL write: the container is the store itself, so the sid is the FrozenStore's own _fsid
        jd._shared_clear()
        with self.assertRaises(jd.FrozenStoreError):
            jd.load_goals_shared(SID)["x"] = 1
        rows = [r for r in self._errors() if r["err"] == "frozen-store-write"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["fsid"], SID, "the store names itself")
        self.assertNotIn(" -> ", rows[2]["note"])
        # a nested map: the site, no sid
        jd._shared_clear()
        with self.assertRaises(jd.FrozenStoreError):
            jd.load_goals_shared(SID)["status"][nid] = "completed"
        rows = [r for r in self._errors() if r["err"] == "frozen-store-write"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[3]["fsid"], "")
        self.assertIn(" at %s:" % here, rows[3]["note"])

    def test_copies_of_the_shared_view_are_plain_and_writable(self):
        self._seed()
        s = jd.load_goals_shared(SID)
        nid = self._nid(1)
        c = copy.copy(s)
        self.assertEqual(type(c), dict)
        c["x"] = 1
        d = copy.deepcopy(s)
        self.assertEqual((type(d), type(d["nodes"]), type(d["nodes"][nid]), type(d["nodes"][nid]["log"])),
                         (dict, dict, dict, list))
        d["nodes"][nid]["log"].append({"kind": "done"})
        self.assertEqual(len(s["nodes"][nid]["log"]) + 1, len(d["nodes"][nid]["log"]), "a real copy")
        self.assertEqual(type(dict(s["nodes"])), dict)
        self.assertEqual(type(s["nodes"][nid]["log"] + [1]), list)
        self.assertEqual(type(s["nodes"][nid]["log"][:]), list)
        self.assertEqual(type(s["nodes"][nid]["log"].copy()), list)
        pk = pickle.loads(pickle.dumps(s))
        self.assertEqual((type(pk), type(pk["nodes"][nid])), (dict, dict))
        self.assertEqual(_norm(pk), _norm(s))
        self.assertEqual(self._delta("poisoned"), 0, "none of these is a write to the shared object")


if __name__ == "__main__":
    unittest.main()
