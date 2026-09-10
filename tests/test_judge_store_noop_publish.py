#!/usr/bin/env python3
"""save_goals SKIPS a publish that would rewrite the file with identical content (the user 2026-07-22).

Callers save unconditionally by design: _plan_session ends every pass with a rollup + save whether or not
the pass placed anything. On an idle fleet that rewrote ~24 goal stores with byte-identical content roughly
ten times a second, with `rev` counters past 10,000 as the receipt.

The write itself is cheap; the damage is downstream. The kernel's _compact_goal_stores skips any store whose
mtime hasn't moved ("the steady state is just stats"), so a no-op republish moved every mtime every pass and
kept the sweep re-processing the whole live fleet forever. Skipping is safe BECAUSE nothing changed: a writer
with no events to contribute can neither lose its own work nor clobber a concurrent writer's.

All fixtures SYNTHETIC: placeholder UUID, invented goal text.
"""
import contextlib
import errno
import io
import json
import os
import tempfile
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

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


class NoOpPublish(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))

    def tearDown(self):
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _file(self):
        return jd.GOALDIR / (SID + ".json")

    def _seed(self):
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "A goal"}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)

    # ── the fix ─────────────────────────────────────────────────────────────
    def test_an_unchanged_store_is_not_republished(self):
        self._seed()
        rev, mt = jd._disk_rev(SID), os.stat(self._file()).st_mtime_ns
        jd.save_goals(SID, jd.load_goals(SID))
        self.assertEqual(jd._disk_rev(SID), rev, "a no-op publish does not advance the revision")
        self.assertEqual(os.stat(self._file()).st_mtime_ns, mt,
                         "...and does not touch the file, so the compaction sweep can skip it")

    def test_a_storm_of_no_op_passes_leaves_the_store_alone(self):
        """The reported symptom: pass after pass rewriting the same bytes."""
        self._seed()
        rev, mt = jd._disk_rev(SID), os.stat(self._file()).st_mtime_ns
        for _ in range(10):
            jd.save_goals(SID, jd.load_goals(SID))
        self.assertEqual(jd._disk_rev(SID), rev, "ten idle passes, zero publications")
        self.assertEqual(os.stat(self._file()).st_mtime_ns, mt)

    # ── what must still happen ──────────────────────────────────────────────
    def test_a_changed_store_is_published(self):
        self._seed()
        rev = jd._disk_rev(SID)
        s = jd.load_goals(SID)
        jd.apply_plan(s, "s2", T0 + 60, [{"do": "mint", "why": "y", "text": "A second goal"}],
                      jd.open_menu(s) if hasattr(jd, "open_menu") else [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(SID, s)
        self.assertGreater(jd._disk_rev(SID), rev, "a real change still advances the revision")
        self.assertEqual(len(json.loads(self._file().read_text())["nodes"]), 2,
                         "...and the new node is on disk")

    def test_a_first_publish_creates_the_file(self):
        self.assertFalse(self._file().exists())
        self._seed()
        self.assertTrue(self._file().exists(), "an absent file is a publish, never a no-op")

    def test_a_store_built_without_load_goals_still_publishes(self):
        """No _baseRev means no known base to compare against — keep the old unconditional behaviour."""
        self._seed()
        rev = jd._disk_rev(SID)
        raw = json.loads(self._file().read_text())      # hand-built store, never through load_goals
        self.assertNotIn("_baseRev", raw)
        jd.save_goals(SID, raw)
        self.assertGreater(jd._disk_rev(SID), rev)

    def test_a_store_built_without_load_goals_stays_unconditional_across_saves(self):
        """The post-publish re-stamp of `_baseRev` (2026-09-06) is for stores that carried a base: a save
        stamps none on a hand-built store, so its second save is still published unconditionally, not
        no-op-checked against a base it never had."""
        self._seed()
        raw = json.loads(self._file().read_text())      # hand-built store, never through load_goals
        jd.save_goals(SID, raw)
        rev = jd._disk_rev(SID)
        self.assertNotIn("_baseRev", raw, "a save stamps no base on a store that never carried one")
        jd.save_goals(SID, raw)                          # no change at all
        self.assertEqual(jd._disk_rev(SID), rev + 1,
                         "a hand-built store is still published unconditionally, not no-op-checked")

    def test_a_second_unchanged_save_of_the_same_object_is_a_no_op(self):
        """A loaded store keeps its base across saves (2026-09-06), so the holder's later saves reach the
        no-op check: the planner's rollup-and-save at the end of a pass that changed nothing after its
        earlier save rewrites no bytes."""
        self._seed()
        s = jd.load_goals(SID)
        gid = "%s:g1" % SID
        jd.record_verdict(s, s["nodes"][gid], "planner", "done", T0 + 30, why="shipped")
        jd.save_goals(SID, s)                            # a real publish
        rev, mt = jd._disk_rev(SID), os.stat(self._file()).st_mtime_ns
        jd.save_goals(SID, s)                            # the same object, unchanged since
        self.assertEqual(jd._disk_rev(SID), rev, "the second save is a no-op: no revision")
        self.assertEqual(os.stat(self._file()).st_mtime_ns, mt, "...and the file is untouched")

    def test_a_no_op_save_cannot_clobber_a_concurrent_writer(self):
        """Pass A loads and changes nothing; writer B publishes a real event meanwhile. The skip compares
        against DISK, and disk has moved, so A does NOT skip here — it takes the rebase path and folds B's
        event in. That is the correct outcome and the guarantee that matters: an empty-handed writer never
        rolls a concurrent one back. (The storm this fix targets is the single-writer case below, where disk
        is unchanged too.)"""
        self._seed()
        a = jd.load_goals(SID)                          # A's snapshot, held across its model call
        b = jd.load_goals(SID)
        gid = "%s:g1" % SID
        jd.record_verdict(b, b["nodes"][gid], "romp", "block", T0 + 30, why="needs a decision")
        jd.rollup_status(b, session_closed=False)
        jd.save_goals(SID, b)
        jd.save_goals(SID, a)                           # A has nothing of its own to add
        disk = json.loads(self._file().read_text())
        self.assertTrue(any(r.get("kind") == "block" for r in disk["nodes"][gid].get("log", [])),
                        "B's block survives A's save")

    def test_content_signature_ignores_rev_and_the_transient_base(self):
        s = {"rompUuid": SID, "nodes": {}, "rev": 7, "_baseRev": 7}
        t = {"nodes": {}, "rompUuid": SID, "rev": 999}
        self.assertEqual(jd._store_content(s), jd._store_content(t),
                         "same content, different revision + key order → the same publish")



class UnparseableStore(unittest.TestCase):
    """An UNPARSEABLE goals file is quarantined aside, and only then does the session start fresh.

    The old test here (`test_an_unreadable_file_falls_through_to_a_real_write`) asserted the defect: it
    wrote "{not json" over a published store and expected load_goals + save_goals to REPLACE the file with
    a fresh one. That replacement destroyed the only copy of whatever the file held, and the same branch
    fired for a torn write, a permission fault and an I/O error alike (see test_judge_store_cas.py for the
    fault half). Now the bad bytes are moved to <file>.corrupt-<stamp> first, the event is logged on both
    channels the repo uses, and the fresh store is published to the empty path.

    Mints goals, so it uses a PRIVATE synthetic sid (CLAUDE.md, 2026-08-24) and cleans that sid's override
    journal in tearDown."""
    QSID = "6a1b2c3d-4e5f-4071-8293-a4b5c6d7e8f9"

    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))

    def tearDown(self):
        (jd._overrides_dir() / (self.QSID + ".jsonl")).unlink(missing_ok=True)
        jd._rebind_state(self._saved)
        (jd._overrides_dir() / (self.QSID + ".jsonl")).unlink(missing_ok=True)
        self.td.cleanup()

    def _file(self):
        return jd.GOALDIR / (self.QSID + ".json")

    def _sidecars(self):
        return sorted(jd.GOALDIR.glob(self.QSID + ".json.corrupt-*"))

    def _error_rows(self):
        if not jd.ERRORS.exists():
            return []
        return [json.loads(l) for l in jd.ERRORS.read_text().splitlines()]

    def _seed(self):
        s = {"rompUuid": self.QSID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "A goal"}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(self.QSID, s)

    def test_an_unparseable_file_is_quarantined_aside_then_a_fresh_store_is_written(self):
        self._seed()
        self._file().write_text("{not json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = jd.load_goals(self.QSID)
        self.assertEqual(s["nodes"], {}, "the session starts fresh...")
        self.assertEqual(s["_baseRev"], 0)
        aside = self._sidecars()
        self.assertEqual(len(aside), 1, "...but ONLY after the bad bytes were moved aside")
        self.assertEqual(aside[0].read_text(), "{not json", "the sidecar holds the original bytes")
        self.assertFalse(self._file().exists(), "the live path is empty until the next publish")
        self.assertIn(str(self._file()), err.getvalue(), "one stderr line names the file...")
        self.assertIn("invalid JSON", err.getvalue(), "...and the reason")
        self.assertEqual([(r["err"], r["fsid"]) for r in self._error_rows()],
                         [("store-quarantined", self.QSID)], "and one judge-errors row carries it")
        jd.save_goals(self.QSID, s)
        self.assertEqual(json.loads(self._file().read_text())["rompUuid"], self.QSID,
                         "the fresh store is then published to the empty path")
        self.assertEqual(aside[0].read_text(), "{not json", "...without touching the sidecar")
        self.assertEqual(self._sidecars(), aside, "and quarantines nothing further")

    def test_every_unparseable_shape_is_quarantined_not_replaced(self):
        """A torn write, bytes that are not UTF-8, and valid JSON whose top level is not an object all
        used to read as 'no file'. Each is moved aside; same-second quarantines get distinct names."""
        self._seed()
        for i, (payload, reason) in enumerate([(b"{\"rompUuid\": \"", "invalid JSON"),
                                                (b"\xff\xfe{}", "not UTF-8"),
                                                (b"[1, 2]", "not an object")], start=1):
            with self.subTest(payload=payload):
                self._file().write_bytes(payload)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    s = jd.load_goals(self.QSID)
                self.assertEqual(s["nodes"], {})
                self.assertEqual(len(self._sidecars()), i, "one more sidecar per quarantine")
                held = [p for p in self._sidecars() if p.read_bytes() == payload]
                self.assertEqual(len(held), 1, "the bytes survive verbatim in exactly one sidecar")
                self.assertIn(reason, err.getvalue())
        self.assertEqual([r["err"] for r in self._error_rows()], ["store-quarantined"] * 3)

    def test_a_store_republished_between_the_read_and_the_rename_is_left_alone(self):
        """No lock guards the rename, so a peer can publish a VALID store to the path between the read that
        failed to parse and the move aside; moving what is there now would quarantine the peer's good store
        and start the session fresh over it. The quarantine re-checks the file's identity (inode, mtime,
        size) against the stat taken before the read and declines when it changed; the reader then reads
        what is there now (review find, 2026-09-08)."""
        self._seed()
        good = self._file().read_bytes()
        self._file().write_text("{not json")
        orig, fired, test = Path.read_text, [], self

        def read_then_a_peer_publishes(p, *a, **kw):
            raw = orig(p, *a, **kw)
            if p == test._file() and not fired:          # the first read sees the bad bytes; a peer's atomic
                fired.append(1)                          # publish then replaces the file before our rename
                tmp = p.with_name(p.name + ".peer")
                tmp.write_bytes(good)
                os.replace(tmp, p)
            return raw
        err = io.StringIO()
        with mock.patch.object(Path, "read_text", read_then_a_peer_publishes), contextlib.redirect_stderr(err):
            s = jd.load_goals(self.QSID)
        self.assertEqual(self._sidecars(), [], "the peer's valid store was not moved aside")
        self.assertEqual(self._file().read_bytes(), good, "and sits untouched at the path")
        self.assertEqual(self._error_rows(), [], "no row: nothing was quarantined")
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(len(s["nodes"]), 1, "the reader returns what is there now, not a fresh store")

    def test_an_absent_file_is_a_fresh_store_with_no_quarantine_and_no_error(self):
        self.assertFalse(self._file().exists())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            s = jd.load_goals(self.QSID)
        self.assertEqual(s["nodes"], {})
        self.assertEqual(s["_baseRev"], 0, "a writer that CREATES the file still trips the CAS")
        self.assertEqual(self._sidecars(), [], "nothing to move aside")
        self.assertEqual(err.getvalue(), "", "nothing to complain about")
        self.assertEqual(self._error_rows(), [])
        jd.save_goals(self.QSID, s)
        self.assertTrue(self._file().exists(), "an absent file is a create, exactly as before")


class DiskMemo(unittest.TestCase):
    """The disk side of the no-op check is memoized by file identity (2026-09-06).

    Every save asked "does the file already hold exactly this?" by re-reading and re-parsing the file and
    serializing both sides, then the CAS parsed the file twice more for its revision: about 20 ms per save
    of a large store, most saves being no-ops. Now _disk_entry keys the file's canonical-content hash on
    (inode, mtime_ns, size), taken from the same descriptor the bytes are read from; a save whose file
    identity still matches serializes only its own side. Every publish is a rename of a fresh temp, so a
    changed file is a new identity and the memo can never answer True where the full compare would answer
    False. The CAS's revision is read from the file, never the memo (the tests under "the CAS reads the
    file" say why). Synthetic fixtures only; a private sid, so no other module's journal reaches it.
    """
    SID = "0b400000-1111-2222-3333-444444444444"

    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        self.reads = []                                  # store paths the memo read
        self._orig_read = jd._disk_read
        self._orig_content = jd._store_content
        self.content_calls = [0]

        def counting_read(fd, path_s):
            if path_s.endswith(self.SID + ".json"):
                self.reads.append(path_s)
            return self._orig_read(fd, path_s)

        def counting_content(store):
            self.content_calls[0] += 1
            return self._orig_content(store)
        jd._disk_read = counting_read
        jd._store_content = counting_content

    def tearDown(self):
        jd._disk_read = self._orig_read
        jd._store_content = self._orig_content
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _file(self):
        return jd.GOALDIR / (self.SID + ".json")

    def _seed(self):
        s = {"rompUuid": self.SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "A goal"}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(self.SID, s)                        # no _baseRev: an unconditional publish, no memo entry
        jd._disk_forget(str(self._file()))

    def _foreign_replace(self, disk):
        """A writer outside this process publishes `disk` the way every writer does: a fresh temp renamed
        over the store. Also moves the mtime by a whole second, because a CI filesystem's timestamp tick
        can be coarser than the gap between two statements."""
        tmp = self._file().with_suffix(".json.foreign")
        tmp.write_text(json.dumps(disk))
        st = os.stat(self._file())
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        os.replace(tmp, self._file())

    def _ident(self, path):
        st = os.stat(path)
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    # ── the saving ───────────────────────────────────────────────────────────
    def test_a_warm_no_op_save_reads_nothing_and_serializes_only_its_own_side(self):
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # warm-up: one read and parse of the file
        self.assertEqual(len(self.reads), 1, "the first check after a foreign publish parses the file once")
        del self.reads[:]
        self.content_calls[0] = 0
        mt = os.stat(self._file()).st_mtime_ns
        for _ in range(5):
            jd.save_goals(self.SID, s)
        self.assertEqual(self.reads, [], "a warm no-op save performs zero reads of the store path")
        self.assertEqual(self.content_calls[0], 5, "one serialization per save: the in-memory side only")
        self.assertEqual(os.stat(self._file()).st_mtime_ns, mt, "and still no write")

    def test_a_real_publish_reads_the_file_once_for_the_cas(self):
        """The check is served by the memo when warm; the CAS reads the file every time (its revision must be
        the file's: test_a_stale_memo_identity_never_passes_the_cas). So a real publish on a warm memo is one
        read; on a cold memo two. Counted through the read seam: a _disk_rev served from the memo would show as
        no read, and one reading by a path of its own would show as no read at all."""
        self._seed()
        gid = "%s:g1" % self.SID
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # warm the memo (one miss)
        jd.record_verdict(s, s["nodes"][gid], "planner", "block", T0 + 30, why="needs a decision")
        jd.rollup_status(s, session_closed=False)
        del self.reads[:]
        jd.save_goals(self.SID, s)
        self.assertEqual(len(self.reads), 1, "warm: the check hit, the CAS read the file")
        self.assertEqual(json.loads(self._file().read_text())["rev"], 2, "and the publish landed")
        disk = json.loads(self._file().read_text())
        disk["nodes"][gid]["summary"] = "Written by another process."
        disk["rev"] = 5
        self._foreign_replace(disk)                       # the memo is cold again
        s = jd.load_goals(self.SID)
        jd.record_verdict(s, s["nodes"][gid], "closer", "done", T0 + 40, why="shipped")
        del self.reads[:]
        jd.save_goals(self.SID, s)
        self.assertEqual(len(self.reads), 2, "cold: the check missed and read, then the CAS read")
        self.assertEqual(json.loads(self._file().read_text())["rev"], 6)

    def test_a_publish_seeds_the_memo_so_the_next_check_reads_nothing(self):
        self._seed()
        s = jd.load_goals(self.SID)
        gid = "%s:g1" % self.SID
        jd.record_verdict(s, s["nodes"][gid], "planner", "block", T0 + 30, why="needs a decision")
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(self.SID, s)                        # a real publish, no rebase: seeds from its temp
        ent = jd._DISK_CONTENT[str(self._file())]
        self.assertEqual(ent[0], self._ident(self._file()),
                         "the temp's identity survives the rename: inode, mtime_ns and size all match the destination")
        del self.reads[:]
        jd.save_goals(self.SID, jd.load_goals(self.SID))  # the next no-op check
        self.assertEqual(self.reads, [], "served from the seed: no read")

    def test_the_seed_is_taken_from_the_temp_before_the_rename(self):
        """Pins the order _disk_seed's docstring states: the identity recorded is the TEMP's, read while the
        temp still exists and before it is renamed over the destination. A seed taken from the destination
        after the rename would record whatever file is there by then, a concurrent publisher's included, and
        pair it with our hash. The spy asserts at call time."""
        self._seed()
        dest = self._file()
        s = jd.load_goals(self.SID)
        gid = "%s:g1" % self.SID
        jd.record_verdict(s, s["nodes"][gid], "planner", "block", T0 + 30, why="needs a decision")
        jd.rollup_status(s, session_closed=False)
        dest_before = self._ident(dest)
        orig = jd._disk_seed
        calls = []

        def spy(path, tmp, canon_hash):
            calls.append(str(tmp))
            self.assertEqual(str(path), str(dest))
            self.assertNotEqual(str(tmp), str(dest), "seeded from the temp, not the destination")
            self.assertTrue(os.path.exists(tmp), "the temp still exists: it has not been renamed yet")
            self.assertEqual(self._ident(dest), dest_before, "the destination is still the old file")
            recorded = self._ident(tmp)
            self.assertNotEqual(recorded, dest_before)
            orig(path, tmp, canon_hash)
            self.assertEqual(jd._DISK_CONTENT[str(dest)][0], recorded, "what went into the memo is the temp's identity")
        jd._disk_seed = spy
        try:
            jd.save_goals(self.SID, s)
        finally:
            jd._disk_seed = orig
        self.assertEqual(len(calls), 1)
        self.assertEqual(jd._DISK_CONTENT[str(dest)][0], self._ident(dest), "which the rename carried to the destination")

    def test_a_rebased_publish_does_not_seed(self):
        """After a rebase the store no longer has the content the pre-rebase hash describes."""
        self._seed()
        gid = "%s:g1" % self.SID
        a, b = jd.load_goals(self.SID), jd.load_goals(self.SID)
        jd.record_verdict(b, b["nodes"][gid], "closer", "done", T0 + 60, why="b's done")
        jd.save_goals(self.SID, b)
        jd.record_verdict(a, a["nodes"][gid], "planner", "block", T0 + 50, why="a's block")
        orig, seeds = jd._disk_seed, []
        jd._disk_seed = lambda *args: seeds.append(args) or orig(*args)
        try:
            jd.save_goals(self.SID, a)                    # base moved: rebases, then publishes
        finally:
            jd._disk_seed = orig
        self.assertEqual(seeds, [], "no seed after a rebase")
        ent = jd._DISK_CONTENT.get(str(self._file()))
        self.assertTrue(ent is None or ent[0] != self._ident(self._file()),
                        "the memo holds no entry claiming the rebased file's identity")
        del self.reads[:]
        jd.save_goals(self.SID, jd.load_goals(self.SID))
        self.assertEqual(len(self.reads), 1, "so the next check parses the file (and finds a no-op)")

    # ── what a changed file must still do ────────────────────────────────────
    def test_a_foreign_atomic_replace_with_different_content_is_detected(self):
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # warm
        disk = json.loads(self._file().read_text())
        disk["nodes"]["%s:g1" % self.SID]["summary"] = "Written by another process."
        self._foreign_replace(disk)
        self.assertFalse(jd._matches_disk(self.SID, s), "a changed file is never mistaken for the memoized one")
        self.assertEqual(len(self.reads), 2, "the new identity misses and is parsed")

    def test_a_same_size_replace_inside_the_same_mtime_tick_is_told_apart_by_its_inode(self):
        """The inode carries the headline invariant. Every publish is a rename of a fresh temp, and on kernels
        with coarse file timestamps two publishes of a same-size store can share an mtime_ns; the new file is
        then a different inode and nothing else. Staged with the temp's mtime pinned to the destination's."""
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # warm
        dest = self._file()
        st = os.stat(dest)
        raw = dest.read_text()
        self.assertIn('"text": "A goal"', raw)
        tmp = dest.with_suffix(".json.foreign")
        tmp.write_text(raw.replace('"text": "A goal"', '"text": "B goal"'))    # same length
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))                   # same tick, staged
        os.replace(tmp, dest)
        new = os.stat(dest)
        self.assertEqual((new.st_mtime_ns, new.st_size), (st.st_mtime_ns, st.st_size), "only the inode differs")
        self.assertNotEqual(new.st_ino, st.st_ino)
        self.assertFalse(jd._matches_disk(self.SID, s), "a new inode is a new file")
        self.assertEqual(jd._DISK_CONTENT[str(dest)][0][0], new.st_ino, "and the memo now records it")
        self.assertNotEqual(jd._DISK_CONTENT[str(dest)][0][0], st.st_ino)

    def test_a_different_length_in_place_rewrite_inside_the_same_mtime_tick_is_told_apart_by_its_size(self):
        """The size twin: same inode, same mtime_ns, different length. No writer here rewrites a store in
        place, so this is defence in depth for the key's third component."""
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)
        dest = self._file()
        st = os.stat(dest)
        raw = dest.read_text()
        dest.write_text(raw.replace('"text": "A goal"', '"text": "A longer goal"'))   # in place: the inode stays
        os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))
        new = os.stat(dest)
        self.assertEqual((new.st_ino, new.st_mtime_ns), (st.st_ino, st.st_mtime_ns), "only the size differs")
        self.assertNotEqual(new.st_size, st.st_size)
        self.assertFalse(jd._matches_disk(self.SID, s))

    def test_a_same_size_in_place_rewrite_with_a_new_mtime_is_detected(self):
        """The mtime part of the key: same inode, same size, different bytes."""
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)
        raw = self._file().read_text()
        self.assertIn('"text": "A goal"', raw)
        changed = raw.replace('"text": "A goal"', '"text": "B goal"')     # same length
        self.assertEqual(len(changed), len(raw))
        st = os.stat(self._file())
        self._file().write_text(changed)                  # in place: the inode stays
        os.utime(self._file(), ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertFalse(jd._matches_disk(self.SID, s))

    def test_a_replace_landing_between_the_open_and_the_stat_cannot_pair_the_new_identity_with_the_old_bytes(self):
        """The identity is fstat of the descriptor the bytes are read from. A publisher landing between the
        open and the stat is the case a stat by path gets wrong: it records the NEW file's identity with the
        OLD file's content, and every later check of the new file hits that entry and answers for content the
        file does not hold. Staged inside the identity seam itself, so the order is open, replace, stat, read."""
        self._seed()
        s = jd.load_goals(self.SID)
        old_ident = self._ident(self._file())
        old_disk = json.loads(self._file().read_text())
        new_disk = json.loads(self._file().read_text())
        new_disk["nodes"]["%s:g1" % self.SID]["summary"] = "Landed between the open and the stat."
        inner = jd._disk_ident
        fired = []

        def racing_ident(fd):
            if not fired:
                fired.append(1)
                self._foreign_replace(new_disk)           # a new inode, mtime and size while the fd is open
            return inner(fd)
        jd._disk_ident = racing_ident
        try:
            ent = jd._disk_entry(self.SID)                # cold memo: open, the race, then stat and read
        finally:
            jd._disk_ident = inner
        self.assertEqual(len(fired), 1)
        new_ident = self._ident(self._file())
        self.assertNotEqual(old_ident, new_ident)
        self.assertEqual(ent[0], old_ident, "the identity is the opened file's")
        self.assertEqual(ent[1], jd._content_hash(jd._store_content(old_disk)), "and so are the bytes")
        self.assertFalse(jd._matches_disk(self.SID, s), "the next check misses on the new identity, parses, and says no")
        self.assertEqual(jd._DISK_CONTENT[str(self._file())][0], new_ident)

    def test_a_same_size_different_store_under_a_bare_goaldir_reassignment_answers_false(self):
        """Keyed on the full path: a test that reassigns jd.GOALDIR (not _rebind_state) must not be served
        the other root's entry."""
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # warm under root A
        raw = self._file().read_text()
        other = Path(self.td.name) / "other-goals"
        other.mkdir()
        (other / (self.SID + ".json")).write_text(raw.replace('"text": "A goal"', '"text": "B goal"'))
        st = os.stat(self._file())
        os.utime(other / (self.SID + ".json"), ns=(st.st_atime_ns, st.st_mtime_ns))   # same mtime and size
        saved = jd.GOALDIR
        try:
            jd.GOALDIR = other
            self.assertFalse(jd._matches_disk(self.SID, s))
        finally:
            jd.GOALDIR = saved
        self.assertTrue(jd._matches_disk(self.SID, s), "root A's entry still answers for root A")

    # ── the CAS reads the file, never the memo ───────────────────────────────
    def test_disk_rev_reads_the_file_even_when_the_memo_identity_matches(self):
        """The CAS's revision comes from the file. With a staged identity collision (same inode, size and
        mtime_ns, different bytes) the memo may still serve the no-op check, whose worst case is a skipped
        publish of content the file once held; _disk_rev must report what the file holds now."""
        self._seed()
        dest = self._file()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # warm: the memo describes revision 1
        raw = dest.read_text()
        self.assertEqual(raw.count('"rev": 1'), 1)
        st = os.stat(dest)
        dest.write_text(raw.replace('"rev": 1', '"rev": 7'))       # in place: same inode, same size
        os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))          # same tick: the collision, staged
        self.assertEqual(self._ident(dest), jd._DISK_CONTENT[str(dest)][0], "the memo's identity matches the file")
        self.assertEqual(jd._disk_rev(self.SID), 7, "and the CAS still sees the file's revision")

    def test_a_stale_memo_identity_never_passes_the_cas(self):
        """Found in review of an earlier draft that served the CAS from the memo (2026-09-06). Three publishes
        of one store inside one mtime tick can leave the
        memo's identity on a file the memo does not describe: K publishes and seeds; B and C, holding older
        snapshots, both check and pass the CAS against K's file, rebase, and publish in turn; C's temp is
        created after B's rename freed K's inode and gets it back, at K's size, in K's tick. The memo still
        says K; the file is C's. A writer E that loaded K's file now hits the memo, and a CAS served from the
        memo would see K's revision, skip the rebase and write over C's event. So the CAS reads the file.
        Staged here: the inode by a kept hard link, the tick by os.utime, the size by padding C's JSON with
        trailing spaces; the interleaving is real save_goals calls, B's run from inside C's _publish_tmp."""
        self._seed()                                      # revision 1
        gid = "%s:g1" % self.SID
        dest = self._file()
        b, c = jd.load_goals(self.SID), jd.load_goals(self.SID)      # revision-1 snapshots
        k = jd.load_goals(self.SID)
        k["nodes"][gid]["text"] = "A goal, restated at length " + "x" * 600
        jd.save_goals(self.SID, k)                        # revision 2; seeds the memo with K's temp identity
        k_st = os.stat(dest)
        k_ident = (k_st.st_ino, k_st.st_mtime_ns, k_st.st_size)
        self.assertEqual(jd._DISK_CONTENT[str(dest)][0], k_ident)
        e = jd.load_goals(self.SID)                       # E holds K's revision
        keep = jd.GOALDIR / (self.SID + ".keep")          # K's inode stays allocated across B's rename
        os.link(dest, keep)
        jd.record_verdict(b, b["nodes"][gid], "planner", "note", T0 + 50, why="b's note")
        jd.record_verdict(c, c["nodes"][gid], "nudge", "block", T0 + 60, why="c's block")
        orig = jd._publish_tmp
        fired = []

        class PinnedTemp(type(Path())):
            """C's temp: written in place into K's inode, padded to K's size, stamped with K's mtime."""
            def write_text(self_, data, *a, **kw):
                pad = k_st.st_size - len(data.encode("utf-8"))
                assert pad >= 0, "fixture: C's rebased store must not outgrow K's"
                n = super().write_text(data + " " * pad, *a, **kw)
                os.utime(self_, ns=(k_st.st_atime_ns, k_st.st_mtime_ns))
                return n

        def hooked(dirpath, fsid):
            jd._publish_tmp = orig
            jd.save_goals(self.SID, b)                    # B lands first: K's inode leaves the path
            tmp = PinnedTemp(orig(dirpath, fsid))
            os.rename(keep, tmp)                          # and is handed to C's temp
            fired.append(tmp)
            return tmp
        jd._publish_tmp = hooked
        try:
            jd.save_goals(self.SID, c)                    # C: check and CAS against K's file, then the hook
        finally:
            jd._publish_tmp = orig
        self.assertEqual(len(fired), 1)
        self.assertEqual(json.loads(dest.read_text())["rev"], 3)
        self.assertEqual(self._ident(dest), k_ident, "staged: C's file carries K's identity")
        self.assertEqual(jd._DISK_CONTENT[str(dest)][0], k_ident, "and the memo still describes K")
        jd.record_verdict(e, e["nodes"][gid], "planner", "note", T0 + 70, why="e's note")
        jd.save_goals(self.SID, e)                        # base 2; the file holds 3
        disk = json.loads(dest.read_text())
        kinds = {(x.get("src"), x.get("kind")) for x in disk["nodes"][gid].get("log") or []}
        self.assertIn(("nudge", "block"), kinds, "C's event survives E's publish")
        self.assertEqual(disk["rev"], 4, "E rebased onto the file's revision, not the memo's")

    def test_a_concurrent_publish_between_a_publish_and_a_save_is_detected_and_rebased(self):
        self._seed()
        gid = "%s:g1" % self.SID
        a = jd.load_goals(self.SID)
        jd.save_goals(self.SID, a)                        # A warms the memo with the seeded file
        b = jd.load_goals(self.SID)
        jd.record_verdict(b, b["nodes"][gid], "nudge", "block", T0 + 100, why="owed")
        jd.rollup_status(b, session_closed=False)
        jd.save_goals(self.SID, b)                        # the concurrent publish (rev 2)
        jd.record_verdict(a, a["nodes"][gid], "planner", "note", T0 + 110, why="a's own event")
        jd.save_goals(self.SID, a)                        # A: content differs, base 1 != disk 2 -> rebase
        disk = json.loads(self._file().read_text())
        kinds = {(e.get("src"), e.get("kind")) for e in disk["nodes"][gid].get("log") or []}
        self.assertIn(("nudge", "block"), kinds, "the concurrent writer's event survives")
        self.assertIn(("planner", "note"), kinds, "and so does A's")
        self.assertEqual(disk["rev"], 3)

    def test_disk_rev_follows_a_publish_and_a_foreign_write(self):
        self._seed()
        self.assertEqual(jd._disk_rev(self.SID), 1)
        s = jd.load_goals(self.SID)
        jd.record_verdict(s, s["nodes"]["%s:g1" % self.SID], "planner", "block", T0 + 30, why="x")
        jd.save_goals(self.SID, s)
        self.assertEqual(jd._disk_rev(self.SID), 2, "the revision after our own publish")
        disk = json.loads(self._file().read_text())
        disk["rev"] = 42
        self._foreign_replace(disk)
        self.assertEqual(jd._disk_rev(self.SID), 42, "the revision after a foreign write")
        self._file().unlink()
        self.assertEqual(jd._disk_rev(self.SID), 0, "absent: 0")
        self.assertIsNone(jd._disk_entry(self.SID), "and the no-op check finds nothing")
        self.assertNotIn(str(self._file()), jd._DISK_CONTENT, "the entry is gone")

    def test_a_corrupt_or_unreadable_file_drops_the_entry_and_raises(self):
        """The save path's readers never answer a file they could not read with the absent-file value
        (ReadFaultCas in test_judge_store_cas.py is the rule): a corrupt file raises ValueError, a read fault
        raises OSError, and either drops the entry, so nothing is remembered about bytes that are not a
        store. An absent file alone answers None / False (a create)."""
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)
        self.assertIn(str(self._file()), jd._DISK_CONTENT)
        good, st = self._file().read_bytes(), os.stat(self._file())
        self._file().write_text("{not json")
        os.utime(self._file(), ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertRaises(ValueError, jd._matches_disk, self.SID, s)
        self.assertNotIn(str(self._file()), jd._DISK_CONTENT, "nothing to remember about a non-store")
        self.assertRaises(ValueError, jd._disk_rev, self.SID)
        self._file().write_bytes(good)
        os.utime(self._file(), ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        jd.save_goals(self.SID, s)                       # a no-op: the check reads the restored file and re-fills
        self.assertIn(str(self._file()), jd._DISK_CONTENT)
        os.utime(self._file(), ns=(st.st_atime_ns, st.st_mtime_ns + 3_000_000_000))   # a miss, so the read runs

        def faulting(fd, path_s):
            raise OSError(errno.EIO, "Input/output error", path_s)
        with mock.patch.object(jd, "_disk_read", faulting):
            self.assertRaises(OSError, jd._matches_disk, self.SID, s)
            self.assertRaises(OSError, jd._disk_rev, self.SID)
        self.assertNotIn(str(self._file()), jd._DISK_CONTENT, "a fault is not a fact about the file")
        self._file().unlink()
        self.assertIsNone(jd._disk_entry(self.SID), "absent: a create")
        self.assertFalse(jd._matches_disk(self.SID, s))
        self.assertEqual(jd._disk_rev(self.SID), 0)

    def test_every_non_object_top_level_raises_value_error_from_the_save_readers(self):
        """_disk_parse's last check: a top-level JSON value that is not an object (a list, a string, null, a
        number) and bytes that are not UTF-8 all raise ValueError from both save-path readers, so the save
        neither publishes over the file nor escapes with an AttributeError from `.get` on a list, and nothing
        is remembered about the file."""
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # warm: the memo holds the healthy file
        st = os.stat(self._file())
        for i, shape in enumerate((b"[]", b'"a string"', b"null", b"42", b"\xff\xfe{"), start=1):
            self._file().write_bytes(shape)
            os.utime(self._file(), ns=(st.st_atime_ns, st.st_mtime_ns + i * 1_000_000_000))   # a moved key: the read runs
            with self.assertRaises(ValueError, msg=repr(shape)):
                jd._matches_disk(self.SID, s)
            self.assertNotIn(str(self._file()), jd._DISK_CONTENT, "%r: not a store, nothing to remember" % shape)
            with self.assertRaises(ValueError, msg=repr(shape)):
                jd._disk_rev(self.SID)
            self.assertEqual(self._file().read_bytes(), shape, "neither reader touched the file")

    def test_an_unserializable_store_is_not_swallowed_by_the_no_op_check(self):
        """_own_hash answers None for a store json.dumps cannot serialize; the no-op check then answers False
        and save_goals raises from its own json.dumps with the file untouched: a publish the check merely
        failed to understand is never skipped as a no-op."""
        self._seed()
        s = jd.load_goals(self.SID)
        jd.save_goals(self.SID, s)                        # a warm no-op
        before = self._file().read_bytes()
        s["nodes"][next(iter(s["nodes"]))]["summary"] = {1, 2}     # a set: not JSON
        with self.assertRaises(TypeError):
            jd.save_goals(self.SID, s)
        self.assertEqual(self._file().read_bytes(), before, "the publish raised on its own terms; the file is untouched")

    def test_the_eviction_sweep_holds_the_memo_lock_while_it_iterates(self):
        """_disk_memo_evict_absent iterates the memo under _DISK_CONTENT_LOCK, so a judge thread's _disk_seed
        or _disk_entry landing mid-sweep cannot change the dict under the iteration. Probed from inside the
        sweep's own os.path.exists calls: the lock is held at every probe. No threads, no clock."""
        self._seed()
        jd.save_goals(self.SID, jd.load_goals(self.SID))
        self.assertIn(str(self._file()), jd._DISK_CONTENT)
        probes, real_exists = [], os.path.exists

        def probing(p):
            got = jd._DISK_CONTENT_LOCK.acquire(blocking=False)
            if got:
                jd._DISK_CONTENT_LOCK.release()
            probes.append(got)
            return real_exists(p)
        with mock.patch.object(os.path, "exists", probing):
            jd._disk_memo_evict_absent()
        self.assertGreaterEqual(len(probes), 1, "the sweep probed the entry's path")
        self.assertEqual(set(probes), {False}, "the lock was held at every probe")

    # ── housekeeping ─────────────────────────────────────────────────────────
    def test_rebind_state_clears_the_memo(self):
        self._seed()
        jd.save_goals(self.SID, jd.load_goals(self.SID))
        self.assertTrue(jd._DISK_CONTENT)
        jd._rebind_state(Path(self.td.name) / "elsewhere")
        self.assertEqual(jd._DISK_CONTENT, {})

    def test_evict_absent_drops_removed_stores_and_keeps_present_ones(self):
        self._seed()
        jd.save_goals(self.SID, jd.load_goals(self.SID))
        other = "0b400000-aaaa-bbbb-cccc-dddddddddddd"
        s2 = {"rompUuid": other, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {}, "placements": {}, "status": {}}
        jd.save_goals(other, s2)
        jd.save_goals(other, jd.load_goals(other))
        self.assertEqual(set(jd._DISK_CONTENT), {str(self._file()), str(jd.GOALDIR / (other + ".json"))})
        (jd.GOALDIR / (other + ".json")).unlink()
        self.assertEqual(jd._disk_memo_evict_absent(), 1)
        self.assertEqual(set(jd._DISK_CONTENT), {str(self._file())})


if __name__ == "__main__":
    unittest.main()
