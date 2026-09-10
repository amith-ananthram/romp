"""Goal-store compaction: archive DISMISSED (cleared) cards out of the live goal tree (the user 2026-06-25).

build_feed re-derives the WHOLE goal store every push, for every session — and ~92% of all nodes ever created
are CLEARED (crossed off the feed), so the store grew monotonically and the feed got "slower and slower over
time". The kernel now MOVES each cleared top + its subtree into goals-archive/<sid>.json, keyed purely on
cleared (exactly the cards the feed already hides), so the live store stays ≈ what's on the board. The judge's
(segment-id, phase) dedup lives in store["placements"], which compaction LEAVES in the live store, so the judge
never re-mints an archived node. Undo-clear restores from the archive. Synthetic stores only (no live data).
"""
import errno
import json
import os
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


def _node(nid, parent, **kw):
    n = {"id": nid, "text": nid, "parentId": parent, "nodeComplete": False,
         "blocked": False, "cleared": False, "trail": [nid + "seg"], "t": 1, "mt": 1}
    n.update(kw)
    return n


class GoalCompactionTest(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        km._compact_seen.clear()
        # g1 = a DISMISSED (cleared) top + a done child; g2 = an ACTIVE (working) top with a done + an open child.
        g = lambda n: "%s:%s" % (SID, n)
        nodes = {
            g("g1"): _node(g("g1"), None, cleared=True),
            g("g1a"): _node(g("g1a"), g("g1"), cleared=True),
            g("g2"): _node(g("g2"), None),
            g("g2a"): _node(g("g2a"), g("g2"), nodeComplete=True),
            g("g2b"): _node(g("g2b"), g("g2")),
        }
        self.store = {
            "rompUuid": SID, "seq": 5, "lastNode": g("g2b"), "nodes": nodes,
            "status": {g("g1"): "cleared", g("g2"): "working"},
            # the judge's segment dedup — one key per placed segment; MUST survive compaction
            "placements": {g("g1") + "seg": g("g1"), g("g1a") + "seg": g("g1a"),
                           g("g2") + "seg": g("g2"), g("g2a") + "seg": g("g2a"), g("g2b") + "seg": g("g2b")},
        }
        jd.save_goals(SID, self.store)
        # the durable view-cleared seal (cleared.jsonl) for g1
        (jd.STATE).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "cleared.jsonl").write_text(json.dumps({"id": g("g1"), "t": 1, "op": "clear"}) + "\n")
        self.g = g

    def tearDown(self):
        jd._rebind_state(self._saved_state)   # the judge module is shared process-wide: never leave it on a removed dir
        shutil.rmtree(self._td, ignore_errors=True)

    def test_a_cleared_root_and_subtree_leave_the_live_store_for_the_archive(self):
        moved = km._compact_goal_store(SID)
        self.assertEqual(moved, 2, "g1 + its child g1a move out")
        live = jd.load_goals(SID)["nodes"]
        self.assertNotIn(self.g("g1"), live)
        self.assertNotIn(self.g("g1a"), live)
        arch = jd.load_goal_archive(SID)["nodes"]
        self.assertIn(self.g("g1"), arch, "the dismissed card is preserved in the archive, not deleted")
        self.assertIn(self.g("g1a"), arch)

    def test_b_an_active_root_with_a_done_child_is_NOT_archived(self):
        km._compact_goal_store(SID)
        live = jd.load_goals(SID)["nodes"]
        # g2 is working → its whole subtree (incl. the done g2a + the open g2b) stays live → roll-up intact
        for n in ("g2", "g2a", "g2b"):
            self.assertIn(self.g(n), live, "an active card never loses children to the archive")

    def test_c_the_judge_dedup_keys_survive_so_an_archived_node_is_never_re_minted(self):
        km._compact_goal_store(SID)
        store = jd.load_goals(SID)
        # the archived nodes' segments are STILL in placements → the judge's `if key in placements: continue`
        # skips them → it never re-creates an archived node (the no-re-bloat guarantee, by construction)
        self.assertIn(self.g("g1") + "seg", store["placements"])
        self.assertIn(self.g("g1a") + "seg", store["placements"])
        # a fresh roll-up over the compacted store does not resurrect the archived top
        jd.rollup_status(store, False)
        self.assertNotIn(self.g("g1"), store["nodes"])
        self.assertNotIn(self.g("g1"), store.get("status", {}))

    def test_d_undo_clear_restores_an_archived_card_from_the_archive(self):
        km._compact_goal_store(SID)
        self.assertNotIn(self.g("g1"), jd.load_goals(SID)["nodes"])
        km._restore_goal_archive([self.g("g1")])
        live = jd.load_goals(SID)["nodes"]
        self.assertIn(self.g("g1"), live, "undo pulls the top back into the live store")
        self.assertIn(self.g("g1a"), live, "...with its whole subtree")
        self.assertNotIn(self.g("g1"), jd.load_goal_archive(SID)["nodes"], "and out of the archive")

    def test_e_the_sweep_skips_a_store_whose_file_did_not_change(self):
        calls = []
        orig = km._compact_goal_store
        km._compact_goal_store = lambda f: calls.append(f) or orig(f)
        try:
            km._compact_goal_stores()                 # first sweep → visits the store (migrates g1)
            self.assertIn(SID, calls)
            calls.clear()
            km._compact_goal_stores()                 # nothing changed since → the mtime gate skips it
            self.assertNotIn(SID, calls, "an unchanged store is not re-swept (steady state is just stats)")
        finally:
            km._compact_goal_store = orig

    def test_g_an_unreadable_store_is_not_marked_seen_so_the_next_sweep_retries_it(self):
        """A store the sweep could not READ (EIO, EACCES) is skipped for that sweep only: recording it as
        swept would gate on an mtime that never moves (a permission fix changes no mtime), so it would
        never be looked at again. Private synthetic sid; its journal lives in this test's temp root."""
        other = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
        g = lambda n: "%s:%s" % (other, n)
        jd.save_goals(other, {"rompUuid": other, "seq": 1, "lastNode": g("g1"), "placements": {},
                              "nodes": {g("g1"): _node(g("g1"), None, cleared=True)},
                              "status": {g("g1"): "cleared"}})
        target, orig = jd.GOALDIR / (other + ".json"), Path.read_text

        def faulting(path, *a, **kw):
            if path == target:
                raise OSError(errno.EIO, "Input/output error", str(path))
            return orig(path, *a, **kw)
        with mock.patch.object(Path, "read_text", faulting):
            km._compact_goal_stores()
        self.assertNotIn(other, km._compact_seen, "an unreadable store is not recorded as swept...")
        self.assertFalse((jd.GOALARCHDIR / (other + ".json")).exists(), "...and nothing moved out of it")
        self.assertIn(SID, km._compact_seen, "the other store was swept as usual")
        km._compact_goal_stores()                     # the fault cleared; the file's mtime never moved
        self.assertIn(other, km._compact_seen, "...so the next sweep retries it")
        self.assertIn(g("g1"), json.loads((jd.GOALARCHDIR / (other + ".json")).read_text())["nodes"],
                      "and archives its cleared top")
    def test_g_the_sweep_evicts_the_disk_memo_entries_of_removed_stores(self):
        """save_goals' no-op check memoizes each store file's identity; the sweep's start is where entries
        for stores that no longer exist are dropped (2026-09-06)."""
        jd.save_goals(SID, jd.load_goals(SID))        # a no-op save fills the entry for this store
        gone = "11111111-2222-3333-4444-666666666666"
        jd.save_goals(gone, {"rompUuid": gone, "seq": 0, "nodes": {}, "placements": {}, "status": {}})
        jd.save_goals(gone, jd.load_goals(gone))
        self.assertIn(str(jd.GOALDIR / (gone + ".json")), jd._DISK_CONTENT)
        (jd.GOALDIR / (gone + ".json")).unlink()
        km._compact_goal_stores()
        self.assertNotIn(str(jd.GOALDIR / (gone + ".json")), jd._DISK_CONTENT, "the removed store's entry is gone")
        self.assertIn(str(jd.GOALDIR / (SID + ".json")), jd._DISK_CONTENT, "the live store's entry stays")

    def test_f_undo_clear_wires_in_the_archive_restore_before_unsetting_the_flag(self):
        import inspect
        body = inspect.getsource(km._undo_clear)
        self.assertIn("_restore_goal_archive(restored)", body)
        # restore must precede the flag un-set so _mark_nodes_cleared finds the nodes
        self.assertLess(body.index("_restore_goal_archive(restored)"),
                        body.index("_mark_nodes_cleared(restored, False)"))




class ClearedLedgerIsAuthoritativeAcrossTheCompaction(unittest.TestCase):
    """The user (2026-09-09): after a restart, a batch of cards they had cleared came back. A clear lived in the
    ledger (cleared.jsonl) and in the store's node verdict and flag; a triage pass that loaded the store before the
    clear and saved after it erased the verdict and the flag, the ledger kept hiding the live card so nothing
    showed, the boot compaction archived the node with cleared false, and the archive projection trusted the flag
    alone, so the card rendered completed and uncleared. Three fixes, each pinned: the clear is journaled and a
    clobbered clear heals on the next load; the compaction stamps the ledger's flag on the root it moves; the
    projection reads the ledger over the copied flag, after its cache, with the roll-down to the subtree."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        km._compact_seen.clear()
        km._arch_tops_cache.clear()
        km._CLEARED_MEMO["slot"] = None
        jd._GOALARCH_MEMO.clear()
        self.g = lambda n: "%s:%s" % (SID, n)
        (jd.STATE).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "cleared.jsonl").write_text("")

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        km._arch_tops_cache.clear()
        km._CLEARED_MEMO["slot"] = None
        shutil.rmtree(self._td, ignore_errors=True)

    def _completed_top(self, n, with_child=False, sid=SID):
        """A completed top the way a real one is: its completion is a done verdict in its log (rollup derives
        nodeComplete from the log, so a bare flag would not survive a load). `sid`: the session it lives in."""
        g = lambda x: "%s:%s" % (sid, x)
        nodes = {g(n): _node(g(n), None, t=100, mt=100)}
        if with_child:
            nodes[g(n + "a")] = _node(g(n + "a"), g(n), t=100, mt=100)
        store = jd._guard_nodes({"rompUuid": sid, "seq": 1, "lastNode": g(n), "nodes": nodes,
                                 "status": {}, "placements": {g(n) + "seg": g(n)}})
        for nid in list(nodes):
            jd.record_verdict(store, store["nodes"][nid], "closer", "done", 100, why="shipped")
        jd.rollup_status(store, True)
        jd.save_goals(sid, store)
        self.assertEqual(jd.load_goals(sid)["status"].get(g(n)), "completed", "premise: a completed top")
        return store

    def _fresh_process(self):
        """Every in-process memo the archive projection could serve from, dropped: what a restart drops."""
        km._arch_tops_cache.clear()
        km._CLEARED_MEMO["slot"] = None
        jd._GOALARCH_MEMO.clear()
        jd._shared_clear()

    def _clobber_with(self, snapshot, sid=SID):
        """A pass save from a pre-clear snapshot: the store file loses the verdict and the flag."""
        (jd.GOALDIR / (sid + ".json")).write_text(json.dumps(snapshot))
        jd._shared_clear()

    def test_the_users_sequence_clear_clobber_compact_restart_the_card_stays_cleared(self):
        self._completed_top("g3", with_child=True)
        snapshot = json.loads((jd.GOALDIR / (SID + ".json")).read_text())   # a pass holds this across its model call
        km._clear_all([self.g("g3")])                                       # the user's cross-off
        raw = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertTrue(raw["nodes"][self.g("g3")]["cleared"], "premise: the live write landed the flag")
        self._clobber_with(snapshot)                                        # ...and the pass's save erased it
        raw = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertFalse(raw["nodes"][self.g("g3")].get("cleared"), "premise: the flag is gone from the file")
        moved = km._compact_goal_store(SID)
        self.assertEqual(moved, 2, "the ledger-cleared root and its child leave the live store")
        arch = json.loads((jd.GOALARCHDIR / (SID + ".json")).read_text())
        self.assertTrue(arch["nodes"][self.g("g3")]["cleared"], "the archived copy carries the flag")
        self.assertTrue(any(e.get("kind") == "clear" for e in arch["nodes"][self.g("g3")].get("log") or []),
                        "...as a verdict in its log, not a bare flag")
        self._fresh_process()
        by_id = {n["id"]: n for n in km._fleet_archived_tops(SID)}
        for nid in (self.g("g3"), self.g("g3a")):
            n = by_id.get(nid)
            self.assertTrue(n is None or n["cleared"],
                            "after the restart the card is gone from Show completed (a cleared top is no longer a "
                            "completed one) or reads cleared; it never comes back as completed and uncleared")

    def test_a_clobbered_clear_heals_on_the_next_load_and_an_undo_still_wins(self):
        self._completed_top("g6")
        snapshot = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        km._clear_all([self.g("g6")])
        rows = [json.loads(l) for l in (jd._overrides_dir() / (SID + ".jsonl")).read_text().splitlines()]
        self.assertEqual([r["op"] for r in rows], ["clear"], "the clear is journaled, journal-first")
        self.assertEqual((rows[0]["node"], rows[0]["src"]), (self.g("g6"), "user"))
        self._clobber_with(snapshot)
        st = jd.load_goals(SID)
        self.assertTrue(st["nodes"][self.g("g6")].get("cleared"), "the replay re-seals the clobbered clear")
        self.assertTrue(any(e.get("kind") == "clear" for e in st["nodes"][self.g("g6")].get("log") or []))
        # the user undoes: the reopen at or after the clear outranks the replayed row from then on
        km._undo_clear()
        st = jd.load_goals(SID)
        self.assertFalse(st["nodes"][self.g("g6")].get("cleared"), "undone")
        jd._shared_clear()
        self.assertFalse(jd.load_goals(SID)["nodes"][self.g("g6")].get("cleared"), "...and it stays undone on a reload")

    def test_clear_undo_clear_inside_one_second_replays_to_the_last_row(self):
        # the review find (2026-09-09): the arms compared integer seconds, so three gestures in one second
        # left the LAST clear skipped on replay; the journal's row order decides now
        self._completed_top("g7")
        snapshot = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        km._clear_all([self.g("g7")])
        km._undo_clear()
        km._clear_all([self.g("g7")])
        rows = [json.loads(l)["op"] for l in (jd._overrides_dir() / (SID + ".jsonl")).read_text().splitlines()]
        self.assertEqual([r for r in rows if r in ("clear", "unclear")], ["clear", "unclear", "clear"])
        self._clobber_with(snapshot)
        self.assertTrue(jd.load_goals(SID)["nodes"][self.g("g7")].get("cleared"), "the last row, a clear, wins")
        # and the mirror: clear then undo in the same second ends unsealed
        self._completed_top("g8")
        snapshot = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        km._clear_all([self.g("g8")])
        km._undo_clear()
        self._clobber_with(snapshot)
        self.assertFalse(jd.load_goals(SID)["nodes"][self.g("g8")].get("cleared"), "the last row, an undo, wins")

    # The tests below put two or three gestures in one second and take the pass's snapshot where the log already
    # holds a write at the second a lost gesture replays into. They mint their goals under a sid of their own, so
    # the rows they journal and the goals they mint are theirs alone.
    SEAL_SID = "11111111-2222-3333-4444-999999999910"

    def _seal_rows(self, sid, n):
        """The node's clear/unclear journal rows, in order, as (op, t)."""
        g = "%s:%s" % (sid, n)
        rows = [json.loads(l) for l in (jd._overrides_dir() / (sid + ".jsonl")).read_text().splitlines()]
        return [(r["op"], r["t"]) for r in rows if r.get("node") == g and r["op"] in ("clear", "unclear")]

    def _user_seals(self, store, sid, n):
        """The node's user clear and undo-reopen verdicts, in log order, as kinds."""
        log = store["nodes"]["%s:%s" % (sid, n)].get("log") or []
        return [e["kind"] for e in log if e.get("src") == "user"
                and (e.get("kind") == "clear" or (e.get("kind") == "reopen" and e.get("undo")))]

    def test_a_same_second_re_clear_replays_over_a_snapshot_taken_after_the_undo(self):
        # the same-second test above takes its snapshot before the first clear, so the first row's replay
        # supplies the clear and the third row skips on the flag. A pass that loaded the store AFTER the undo
        # holds the first clear and its undo already, at the second all three gestures share: the re-clear's
        # row then found a user clear at its own second in the log (the first one, survived) and skipped as if
        # it were its own write, so the re-clear was lost and the store read the card uncleared while
        # cleared.jsonl hid it
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g9")
        self._completed_top("g9", sid=sid)
        with mock.patch("time.time", return_value=1_000_000.0):          # one second for all three gestures
            km._clear_all([g])
            km._undo_clear()
            snapshot = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
            km._clear_all([g])
        self.assertEqual([op for op, _ in self._seal_rows(sid, "g9")], ["clear", "unclear", "clear"],
                         "premise: three rows, journal order")
        self.assertEqual(len({t for _, t in self._seal_rows(sid, "g9")}), 1, "premise: one second")
        self.assertEqual(self._user_seals(snapshot, sid, "g9"), ["clear", "reopen"],
                         "premise: the snapshot holds the first clear and its undo")
        self.assertFalse(snapshot["nodes"][g].get("cleared"), "premise: the snapshot reads the card uncleared")
        self._clobber_with(snapshot, sid=sid)
        st = jd.load_goals(sid)
        self.assertTrue(st["nodes"][g].get("cleared"),
                        "the last row, a clear, wins over a snapshot that holds the first clear and its undo")
        self.assertEqual(self._user_seals(st, sid, "g9"), ["clear", "reopen", "clear"], "the re-clear is re-recorded once")
        # idempotent: a save and a reload add nothing
        jd.save_goals(sid, st)
        jd._shared_clear()
        again = jd.load_goals(sid)
        self.assertTrue(again["nodes"][g].get("cleared"))
        self.assertEqual(self._user_seals(again, sid, "g9"), ["clear", "reopen", "clear"], "one row added, not one per load")

    def test_a_same_second_re_undo_replays_over_a_snapshot_taken_after_the_re_clear(self):
        # the mirror: clear, then undo, clear and undo inside one second, with the pass's snapshot taken after
        # the re-clear. The last undo's row found an undo at its own second in the log (the first undo,
        # survived) and skipped; the flag stayed set while cleared.jsonl said undone, so the feed hid the card,
        # the compaction archived it flagged, and no Undo could reach it
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g10")
        self._completed_top("g10", sid=sid)
        with mock.patch("time.time", return_value=999_000.0):
            km._clear_all([g])
        with mock.patch("time.time", return_value=1_000_000.0):          # one second for the next three
            km._undo_clear()
            km._clear_all([g])
            snapshot = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
            km._undo_clear()
        self.assertEqual([op for op, _ in self._seal_rows(sid, "g10")], ["clear", "unclear", "clear", "unclear"],
                         "premise: four rows, journal order")
        self.assertEqual(len({t for _, t in self._seal_rows(sid, "g10")[1:]}), 1, "premise: the last three share a second")
        self.assertEqual(self._user_seals(snapshot, sid, "g10"), ["clear", "reopen", "clear"],
                         "premise: the snapshot holds the first clear, its undo and the re-clear")
        self.assertTrue(snapshot["nodes"][g].get("cleared"), "premise: the snapshot reads the card cleared")
        self.assertNotIn(g, km._cleared_ids(), "premise: cleared.jsonl says undone")
        self._clobber_with(snapshot, sid=sid)
        st = jd.load_goals(sid)
        self.assertFalse(st["nodes"][g].get("cleared"),
                         "the last row, an undo, wins over a snapshot that holds the re-clear")
        self.assertEqual(self._user_seals(st, sid, "g10"), ["clear", "reopen", "clear", "reopen"],
                         "the undo is re-recorded once")
        self.assertEqual(st["status"].get(g), "completed", "the card comes back to Completed, not Working")
        jd.save_goals(sid, st)
        jd._shared_clear()
        again = jd.load_goals(sid)
        self.assertFalse(again["nodes"][g].get("cleared"))
        self.assertEqual(self._user_seals(again, sid, "g10"), ["clear", "reopen", "clear", "reopen"],
                         "one row added, not one per load")

    def test_two_same_second_undos_around_a_skipped_re_clear_record_one_reopen(self):
        # the pass's snapshot taken after the FIRST clear this time, before the same-second undo, clear and
        # undo. The journal's last word for the node is the undo, so the re-clear between the two undos never
        # replays; both undo rows may act (the second's last word is an undo, their op), and the later one must
        # find the first's replayed reopen already standing for it and record nothing. A second reopen with no
        # clear before it would open the node, and the completed top would come back Working. (Green before and
        # after the arms changed: it pins the shape a same-second count of same-kind writes would break.)
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g11")
        self._completed_top("g11", sid=sid)
        with mock.patch("time.time", return_value=999_000.0):
            km._clear_all([g])
        snapshot = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        with mock.patch("time.time", return_value=1_000_000.0):
            km._undo_clear()
            km._clear_all([g])
            km._undo_clear()
        self.assertEqual([op for op, _ in self._seal_rows(sid, "g11")], ["clear", "unclear", "clear", "unclear"])
        self.assertEqual(self._user_seals(snapshot, sid, "g11"), ["clear"],
                         "premise: the snapshot holds the first clear only")
        self._clobber_with(snapshot, sid=sid)
        st = jd.load_goals(sid)
        self.assertFalse(st["nodes"][g].get("cleared"), "the last row, an undo, wins")
        self.assertEqual(self._user_seals(st, sid, "g11"), ["clear", "reopen"],
                         "one reopen restores what the clear displaced; the second undo adds none")
        self.assertEqual(st["status"].get(g), "completed", "the card comes back to Completed, not Working")

    def test_an_undo_a_second_after_a_same_second_undo_and_re_clear_replays_once(self):
        # the undo and the re-clear share a second, the pass's snapshot is taken after the re-clear, and the
        # last undo lands a second later. The shared second's undo row is superseded by the re-clear row that
        # follows it in the journal and must stand down whatever the log holds: read on its own it finds the
        # second's newest seal is a clear, not its op, and re-records a reopen the second did not end on; the
        # later undo's reopen then follows a reopen instead of a clear, finds nothing to restore, and the
        # completed top comes back Working
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g12")
        self._completed_top("g12", sid=sid)
        with mock.patch("time.time", return_value=999_000.0):
            km._clear_all([g])
        with mock.patch("time.time", return_value=1_000_000.0):
            km._undo_clear()
            km._clear_all([g])
        snapshot = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        with mock.patch("time.time", return_value=1_000_001.0):
            km._undo_clear()
        self.assertEqual(self._seal_rows(sid, "g12"),
                         [("clear", 999_000), ("unclear", 1_000_000), ("clear", 1_000_000), ("unclear", 1_000_001)])
        self.assertEqual(self._user_seals(snapshot, sid, "g12"), ["clear", "reopen", "clear"],
                         "premise: the snapshot holds every gesture but the last undo")
        self._clobber_with(snapshot, sid=sid)
        st = jd.load_goals(sid)
        self.assertFalse(st["nodes"][g].get("cleared"), "the last row, an undo, wins")
        self.assertEqual(self._user_seals(st, sid, "g12"), ["clear", "reopen", "clear", "reopen"],
                         "one reopen, for the undo the snapshot lacks; the superseded undo row adds none")
        self.assertEqual([e["ev_t"] for e in st["nodes"][g]["log"] if e.get("src") == "user" and e.get("undo")],
                         [1_000_000, 1_000_001], "the re-recorded reopen carries the last undo's second")
        self.assertEqual(st["status"].get(g), "completed", "the card comes back to Completed, not Working")

    def test_an_undo_a_second_after_a_same_second_undo_and_re_clear_over_a_snapshot_taken_before_them(self):
        # the same gestures with the snapshot taken after the first clear, so the shared second's undo and
        # re-clear are both lost. The re-clear never replays (the journal's last word is an undo), and the
        # shared second's undo row must stand down for the same reason as above: replayed, its reopen would pair
        # with the first clear and leave the later undo's reopen with nothing to restore
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g13")
        self._completed_top("g13", sid=sid)
        with mock.patch("time.time", return_value=999_000.0):
            km._clear_all([g])
        snapshot = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        with mock.patch("time.time", return_value=1_000_000.0):
            km._undo_clear()
            km._clear_all([g])
        with mock.patch("time.time", return_value=1_000_001.0):
            km._undo_clear()
        self.assertEqual([op for op, _ in self._seal_rows(sid, "g13")], ["clear", "unclear", "clear", "unclear"])
        self.assertEqual(self._user_seals(snapshot, sid, "g13"), ["clear"], "premise: the snapshot holds the first clear only")
        self._clobber_with(snapshot, sid=sid)
        st = jd.load_goals(sid)
        self.assertFalse(st["nodes"][g].get("cleared"), "the last row, an undo, wins")
        self.assertEqual(self._user_seals(st, sid, "g13"), ["clear", "reopen"], "one reopen restores what the clear displaced")
        self.assertEqual(st["status"].get(g), "completed", "the card comes back to Completed, not Working")

    def test_a_re_clear_a_second_after_a_same_second_clear_and_undo_carries_its_own_second(self):
        # the clear and the undo share a second, the snapshot is taken after the undo, and the re-clear lands a
        # second later. The first clear's row is superseded by the undo row of its second and must stand down:
        # read on its own it finds the second's newest seal is a reopen and re-records a clear stamped with the
        # first second, the re-clear's own row then skips on the flag, and the verdict carries the wrong evidence
        # time (a verdict filed between the undo and the re-clear would sort after it) as a second (ev_t, src,
        # kind) twin of the first clear
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g14")
        self._completed_top("g14", sid=sid)
        with mock.patch("time.time", return_value=1_000_000.0):
            km._clear_all([g])
            km._undo_clear()
        snapshot = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        with mock.patch("time.time", return_value=1_000_001.0):
            km._clear_all([g])
        self.assertEqual(self._seal_rows(sid, "g14"), [("clear", 1_000_000), ("unclear", 1_000_000), ("clear", 1_000_001)])
        self.assertEqual(self._user_seals(snapshot, sid, "g14"), ["clear", "reopen"],
                         "premise: the snapshot holds the first clear and its undo")
        self._clobber_with(snapshot, sid=sid)
        st = jd.load_goals(sid)
        self.assertTrue(st["nodes"][g].get("cleared"), "the last row, a clear, wins")
        self.assertEqual([e["ev_t"] for e in st["nodes"][g]["log"] if e.get("src") == "user" and e.get("kind") == "clear"],
                         [1_000_000, 1_000_001], "the re-recorded clear carries the re-clear's second, not the first clear's")

    def test_a_stale_pass_save_rebases_the_re_clear_away_and_the_next_load_re_seals(self):
        # the designed save path, not a raw overwrite: a pass that loaded the store after the undo publishes
        # after the re-clear, and save_goals rebases its copy onto the disk it did not write. The rebase keys
        # verdict identity on (ev_t, src, kind), so the re-clear, a second (T, user, clear), collapses into the
        # first clear and the published store reads the card uncleared; the replay re-seals it on the next load,
        # as it does over a clobbered store
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g15")
        self._completed_top("g15", sid=sid)
        with mock.patch("time.time", return_value=1_000_000.0):
            km._clear_all([g])
            km._undo_clear()
            stale = jd.load_goals(sid)                                  # the pass's copy, held across its model call
            km._clear_all([g])
        self.assertEqual([op for op, _ in self._seal_rows(sid, "g15")], ["clear", "unclear", "clear"])
        self.assertTrue(json.loads((jd.GOALDIR / (sid + ".json")).read_text())["nodes"][g].get("cleared"),
                        "premise: the live re-clear landed")
        jd.save_goals(sid, stale)                                       # the pass publishes: the revision moved, so it rebases
        raw = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        self.assertEqual(self._user_seals(raw, sid, "g15"), ["clear", "reopen"],
                         "premise: the rebase collapsed the re-clear into the first clear")
        self.assertFalse(raw["nodes"][g].get("cleared"), "premise: the published store reads the card uncleared")
        jd._shared_clear()
        st = jd.load_goals(sid)
        self.assertTrue(st["nodes"][g].get("cleared"), "the next load re-seals the card")
        self.assertEqual(self._user_seals(st, sid, "g15"), ["clear", "reopen", "clear"], "the re-clear is re-recorded once")

    def test_a_stale_pass_save_that_collapses_the_re_clear_leaves_the_top_completed(self):
        # the pass's copy is taken after the FIRST clear this time, and the user undoes, re-clears and undoes
        # again before it publishes. The rebase keeps the disk rows the copy lacks by (ev_t, src, kind): the
        # re-clear is a twin of the copy's clear and collapses, and both undo-reopens are kept, so the
        # published log holds clear, reopen, reopen. The last gesture is an undo, so the card is live
        # and uncleared, as it should be; but the second reopen finds no clear to restore, and _fold_node must
        # leave the completed top completed rather than open it into Working. The replay cannot heal it:
        # every row's word is in the log already, so nothing is re-recorded on the next load
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g16")
        self._completed_top("g16", sid=sid)
        with mock.patch("time.time", return_value=1_000_000.0):          # one second for all four gestures
            km._clear_all([g])
            stale = jd.load_goals(sid)                                  # the pass's copy: the first clear only
            km._undo_clear()
            km._clear_all([g])
            km._undo_clear()
        self.assertEqual([op for op, _ in self._seal_rows(sid, "g16")], ["clear", "unclear", "clear", "unclear"],
                         "premise: four rows, journal order")
        self.assertEqual(self._user_seals(stale, sid, "g16"), ["clear"], "premise: the copy holds the first clear only")
        live = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        self.assertFalse(live["nodes"][g].get("cleared"), "premise: the live store reads the card undone")
        self.assertEqual(live["status"].get(g), "completed", "premise: ...and the top completed")
        jd.save_goals(sid, stale)                                       # the pass publishes: the revision moved, so it rebases
        raw = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        self.assertEqual(self._user_seals(raw, sid, "g16"), ["clear", "reopen", "reopen"],
                         "premise: the rebase collapsed the re-clear into the first clear and kept both undos")
        self.assertFalse(raw["nodes"][g].get("cleared"), "the last gesture is an undo: the card is live")
        self.assertEqual(raw["status"].get(g), "completed",
                         "the second undo has nothing to restore and leaves the completed top completed")
        jd._shared_clear()
        st = jd.load_goals(sid)
        self.assertFalse(st["nodes"][g].get("cleared"))
        self.assertEqual(st["status"].get(g), "completed", "the next load reads it completed too")
        self.assertEqual(self._user_seals(st, sid, "g16"), ["clear", "reopen", "reopen"],
                         "the replay re-records nothing: every row's word is in the log")
        jd.save_goals(sid, st)
        jd._shared_clear()
        again = jd.load_goals(sid)
        self.assertEqual(again["status"].get(g), "completed")
        self.assertEqual(self._user_seals(again, sid, "g16"), ["clear", "reopen", "reopen"],
                         "a further save and load add no row")

    def test_an_undo_over_a_snapshot_taken_before_its_clear_leaves_the_top_completed(self):
        # a clear and its undo a second apart, both lost to a pass save from a snapshot taken before either.
        # The journal's last word is the undo, so the clear row never replays; the unclear row re-records its
        # reopen, which then stands with no clear before it. Nothing to restore: the completed top must stay
        # completed and uncleared, not come back Working
        sid, g = self.SEAL_SID, "%s:%s" % (self.SEAL_SID, "g17")
        self._completed_top("g17", sid=sid)
        snapshot = json.loads((jd.GOALDIR / (sid + ".json")).read_text())
        with mock.patch("time.time", return_value=1_000_000.0):
            km._clear_all([g])
        with mock.patch("time.time", return_value=1_000_001.0):
            km._undo_clear()
        self.assertEqual(self._seal_rows(sid, "g17"), [("clear", 1_000_000), ("unclear", 1_000_001)])
        self.assertEqual(self._user_seals(snapshot, sid, "g17"), [], "premise: the snapshot predates both gestures")
        self.assertEqual(snapshot["status"].get(g), "completed", "premise: a completed top")
        self._clobber_with(snapshot, sid=sid)
        st = jd.load_goals(sid)
        self.assertFalse(st["nodes"][g].get("cleared"), "the last row, an undo, wins")
        self.assertEqual(self._user_seals(st, sid, "g17"), ["reopen"],
                         "the clear never replays; the undo's reopen is re-recorded on its own")
        self.assertEqual(st["status"].get(g), "completed", "the card comes back to Completed, not Working")
        jd.save_goals(sid, st)
        jd._shared_clear()
        again = jd.load_goals(sid)
        self.assertEqual(again["status"].get(g), "completed")
        self.assertEqual(self._user_seals(again, sid, "g17"), ["reopen"], "a further save and load add no row")

    def test_the_feed_payload_carries_the_ledgers_foreign_ids_for_the_merged_board(self):
        # the viewer's ledger over remote rows (review find, 2026-09-09): ids the local ledger clears whose
        # session has no store and no archive here ride the payload, bare, for the client merge to apply
        # over that host's rows; local ids stay off it (the local kernel applied them itself); capped
        self._completed_top("g4")
        foreign_sid = "11111111-2222-3333-4444-999999999909"
        with (jd.STATE / "cleared.jsonl").open("a") as f:
            f.write(json.dumps({"id": self.g("g4"), "t": 200, "op": "clear"}) + "\n")
            f.write(json.dumps({"id": foreign_sid + ":g1", "t": 201, "op": "clear"}) + "\n")
            f.write(json.dumps({"id": foreign_sid + ":g2", "t": 202, "op": "clear"}) + "\n")
            f.write(json.dumps({"id": foreign_sid + ":g2", "t": 203, "op": "undo"}) + "\n")
        km._CLEARED_MEMO["slot"] = None
        self.assertEqual(km._cleared_foreign(km._cleared_ids()), [foreign_sid + ":g1"],
                         "the foreign clear rides; the local one and the undone one do not")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"clearedForeign": _cleared_foreign(cleared),', src, "on the feed payload beside dismissedCount")
        many = {"%s:g%d" % (foreign_sid, i): 1_000_000 + i for i in range(700)}
        got = km._cleared_foreign(many)
        self.assertEqual(len(got), 500, "capped")
        self.assertEqual(got[0], "%s:g699" % foreign_sid, "newest first under the cap (T287: yesterday's clears must ride)")
        self.assertNotIn("%s:g0" % foreign_sid, got, "the oldest fall off, not the newest")
        junk = {"g448": 5, "g7": 6, foreign_sid + ":g1": 7}
        self.assertEqual(km._cleared_foreign(junk), [foreign_sid + ":g1"], "a bare node id with no session rides nowhere")

    def test_the_foreign_id_list_skips_cleared_ids_that_name_no_session(self):
        # Five id families in cleared.jsonl name no session (review find, 2026-09-09): "parked:<msgId>" and
        # "quarantine:<mid>" key a message, and the "provisional:", "awaiting:" and "blocked:" + sid
        # placeholders are re-listed every build whatever the clear log holds. Clear-all writes every listed
        # card's id, so their rows arrive live, and the text before each id's last colon is a word, never a
        # goals/ stem, so each read as foreign and rode every frame, local session or not. None of them names
        # a session whose clear rows a kernel reads, so none rides; a remote session's "sid:gN" clear still does.
        self._completed_top("g4")                    # SID has a store here, so its node's clear is local
        foreign_sid = "11111111-2222-3333-4444-999999999909"
        parked_msg = "aaaaaaaa-bbbb-cccc-dddd-000000000001"
        quarantine_msg = "aaaaaaaa-bbbb-cccc-dddd-000000000002"
        rows = [("parked:" + parked_msg, 300), ("quarantine:" + quarantine_msg, 301),
                ("provisional:" + SID, 302), ("awaiting:" + SID, 303), ("blocked:" + SID, 304),
                ("blocked:" + foreign_sid, 305), (foreign_sid + ":g1", 306), (self.g("g4"), 307)]
        with (jd.STATE / "cleared.jsonl").open("a") as f:
            for iid, t in rows:
                f.write(json.dumps({"id": iid, "t": t, "op": "clear"}) + "\n")
        km._CLEARED_MEMO["slot"] = None
        self.assertEqual(km._cleared_foreign(km._cleared_ids()), [foreign_sid + ":g1"],
                         "only the remote session's node clear rides; the message-keyed and placeholder rows and "
                         "the local node stay off")
        direct = {"parked:m1": 5, "quarantine:m2": 6, "blocked:" + SID: 7, foreign_sid + ":g1": 8}
        self.assertEqual(km._cleared_foreign(direct), [foreign_sid + ":g1"],
                         "the same over a direct dict: the prefixed ids stay off, the remote node clear rides")
        many = {"blocked:11111111-2222-3333-4444-%012d" % i: 1_000 + i for i in range(600)}
        many[foreign_sid + ":g1"] = 1
        self.assertEqual(km._cleared_foreign(many), [foreign_sid + ":g1"],
                         "six hundred placeholder clears newer than the remote clear do not crowd it out of the cap")

    def test_the_compaction_stamps_a_root_only_the_ledger_clears(self):
        self._completed_top("g4")
        with (jd.STATE / "cleared.jsonl").open("a") as f:                  # the ledger alone: no flag, no journal
            f.write(json.dumps({"id": self.g("g4"), "t": 200, "op": "clear"}) + "\n")   # after its completion, as a cross-off is
        km._CLEARED_MEMO["slot"] = None
        self.assertEqual(km._compact_goal_store(SID), 1)
        arch = json.loads((jd.GOALARCHDIR / (SID + ".json")).read_text())
        self.assertTrue(arch["nodes"][self.g("g4")]["cleared"], "stamped before the copy")
        # the re-seal's rollup replaces the store's status dict, and the copy reads the replaced one, as it does
        # when the clear lands on the live card (a clear after a done rolls up to cleared): the archive gets the
        # rolled-up value, the live file keeps no entry for a node it no longer holds, and Show completed does
        # not list the top (the pre-reseal copy read completed, which kept it listed, struck through)
        self.assertEqual(arch["status"][self.g("g4")], "cleared", "the rolled-up status moves with the node")
        live = json.loads((jd.GOALDIR / (SID + ".json")).read_text())      # the raw file: a load's replay may re-roll
        self.assertNotIn(self.g("g4"), live.get("status", {}), "no status for a node the live store no longer holds")
        self._fresh_process()
        self.assertEqual([n["id"] for n in km._fleet_archived_tops(SID)], [],
                         "a cleared top is not a completed one: nothing for Show completed to list")

    def test_the_projection_reads_the_ledger_over_the_copied_flag_after_its_cache(self):
        jd.GOALARCHDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALARCHDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID,
            "nodes": {self.g("g5"): _node(self.g("g5"), None, nodeComplete=True, t=100, mt=100),
                      self.g("g5a"): _node(self.g("g5a"), self.g("g5"), nodeComplete=True, t=100, mt=100)},
            "status": {self.g("g5"): "completed"}}))
        first = {n["id"]: n for n in km._fleet_archived_tops(SID)}
        self.assertFalse(first[self.g("g5")]["cleared"], "no ledger row: the flag stands")
        with (jd.STATE / "cleared.jsonl").open("a") as f:                  # a clear lands after the archive's write
            f.write(json.dumps({"id": self.g("g5"), "t": 200, "op": "clear"}) + "\n")
        km._CLEARED_MEMO["slot"] = None
        second = {n["id"]: n for n in km._fleet_archived_tops(SID)}
        self.assertTrue(second[self.g("g5")]["cleared"], "the ledger applies over the cached projection")
        self.assertTrue(second[self.g("g5a")]["cleared"], "and rolls down to the subtree")
        self.assertFalse(first[self.g("g5")]["cleared"], "the cached rows themselves are not mutated")

    def test_an_archive_from_before_the_reseal_fix_does_not_list_a_status_only_root_the_ledger_clears(self):
        # the archive shape an older compaction wrote for a root only the ledger cleared: the re-seal's clear verdict
        # set the flag and left nodeComplete False, and the copy took the status dict bound before the rollup,
        # "completed" (the rollup gives a cleared flag precedence, so that pair is a stale copy by construction);
        # nothing rewrites an archived status, and the root's ledger row persists. g11 is the control: completed
        # by its own verdict, then cleared, the shape the previous test pins as listed and struck through.
        jd.GOALARCHDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALARCHDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID,
            "nodes": {self.g("g10"): _node(self.g("g10"), None, cleared=True, t=100, mt=100),
                      self.g("g10a"): _node(self.g("g10a"), self.g("g10"), cleared=True, t=100, mt=100),
                      self.g("g11"): _node(self.g("g11"), None, nodeComplete=True, t=100, mt=100)},
            "status": {self.g("g10"): "completed", self.g("g11"): "completed"}}))
        first = km._fleet_archived_tops(SID)
        self.assertEqual({n["id"] for n in first if n["depth"] == 0}, {self.g("g10"), self.g("g11")},
                         "premise: with an empty ledger both roots list on the copied values, and the cache is primed")
        with (jd.STATE / "cleared.jsonl").open("a") as f:                  # the rows every such root has
            for n in ("g10", "g11"):
                f.write(json.dumps({"id": self.g(n), "t": 200, "op": "clear"}) + "\n")
        km._CLEARED_MEMO["slot"] = None
        second = km._fleet_archived_tops(SID)
        self.assertEqual([n["id"] for n in second], [self.g("g11")],
                         "a root whose only completion is a copied status the ledger clears is a stale copy of a "
                         "clear: gone from Show completed, its subtree with it")
        self.assertTrue(second[0]["cleared"], "a root completed by its own verdict keeps its struck-through row")
        self.assertIn(self.g("g10"), {n["id"] for n in first}, "the cached rows themselves are not mutated")
        self._fresh_process()
        self.assertEqual([n["id"] for n in km._fleet_archived_tops(SID)], [self.g("g11")],
                         "the uncached projection agrees")

    def test_only_a_status_only_root_is_dropped_a_takeaway_or_its_own_verdict_keeps_the_row(self):
        # five shapes side by side, newest first in the flat list. g12: the flag a pass save erased, the copied
        # status "completed", the ledger row the clear left (the shape from before the compaction re-sealed such
        # a root), a whitespace summary (no takeaway, as the qualification reads it): status-only, dropped with
        # its child. g13: cleared, no status, a distiller takeaway written before the clear: it qualifies through
        # the summary and stays listed struck through, as it does when the clear lands on the live card. g14:
        # completed by its own verdict, nothing clears it: listed, not cleared, its child intact, so the drop
        # ends at the next root. g15: a copied "completed" and nothing clearing it, no flag, no row: listed with
        # its child while the ledger holds other rows (the drop is for CLEARED status-only roots). g16: the
        # copied flag on, a copied "completed", no ledger row: dropped with its child on the flag alone, and
        # the drop of the last root runs to the end of the list.
        jd.GOALARCHDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALARCHDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID,
            "nodes": {self.g("g12"): _node(self.g("g12"), None, summary="  ", t=300, mt=300),
                      self.g("g12a"): _node(self.g("g12a"), self.g("g12"), t=300, mt=300),
                      self.g("g13"): _node(self.g("g13"), None, cleared=True, summary="what shipped", t=200, mt=200),
                      self.g("g14"): _node(self.g("g14"), None, nodeComplete=True, t=100, mt=100),
                      self.g("g14a"): _node(self.g("g14a"), self.g("g14"), nodeComplete=True, t=100, mt=100),
                      self.g("g15"): _node(self.g("g15"), None, t=50, mt=50),
                      self.g("g15a"): _node(self.g("g15a"), self.g("g15"), t=50, mt=50),
                      self.g("g16"): _node(self.g("g16"), None, cleared=True, t=25, mt=25),
                      self.g("g16a"): _node(self.g("g16a"), self.g("g16"), cleared=True, t=25, mt=25)},
            "status": {self.g("g12"): "completed", self.g("g14"): "completed",
                       self.g("g15"): "completed", self.g("g16"): "completed"}}))
        with (jd.STATE / "cleared.jsonl").open("a") as f:                  # the rows the two clears left
            for n in ("g12", "g13"):
                f.write(json.dumps({"id": self.g(n), "t": 400, "op": "clear"}) + "\n")
        km._CLEARED_MEMO["slot"] = None
        rows = km._fleet_archived_tops(SID)
        self.assertEqual([n["id"] for n in rows],
                         [self.g("g13"), self.g("g14"), self.g("g14a"), self.g("g15"), self.g("g15a")],
                         "the cleared status-only roots and their children are gone; the takeaway root, the verdict "
                         "root and the uncleared status-only root stay")
        by = {n["id"]: n for n in rows}
        self.assertTrue(by[self.g("g13")]["cleared"], "a cleared root with a takeaway lists struck through")
        self.assertFalse(by[self.g("g14")]["cleared"] or by[self.g("g14a")]["cleared"],
                         "an uncleared root and its subtree are untouched by the drop before them")
        self.assertFalse(by[self.g("g15")]["cleared"] or by[self.g("g15a")]["cleared"],
                         "a status-only root nothing clears stays listed, not cleared, while the ledger holds other rows")


if __name__ == "__main__":
    unittest.main()
