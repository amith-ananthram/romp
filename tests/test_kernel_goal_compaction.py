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
from importlib.machinery import SourceFileLoader
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
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()
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

    def _completed_top(self, n, with_child=False):
        """A completed top the way a real one is: its completion is a done verdict in its log (rollup derives
        nodeComplete from the log, so a bare flag would not survive a load)."""
        nodes = {self.g(n): _node(self.g(n), None, t=100, mt=100)}
        if with_child:
            nodes[self.g(n + "a")] = _node(self.g(n + "a"), self.g(n), t=100, mt=100)
        store = jd._guard_nodes({"rompUuid": SID, "seq": 1, "lastNode": self.g(n), "nodes": nodes,
                                 "status": {}, "placements": {self.g(n) + "seg": self.g(n)}})
        for nid in list(nodes):
            jd.record_verdict(store, store["nodes"][nid], "closer", "done", 100, why="shipped")
        jd.rollup_status(store, True)
        jd.save_goals(SID, store)
        self.assertEqual(jd.load_goals(SID)["status"].get(self.g(n)), "completed", "premise: a completed top")
        return store

    def _fresh_process(self):
        """Every in-process memo the archive projection could serve from, dropped: what a restart drops."""
        km._arch_tops_cache.clear()
        km._CLEARED_MEMO["slot"] = None
        jd._GOALARCH_MEMO.clear()
        jd._shared_clear()

    def _clobber_with(self, snapshot):
        """A pass save from a pre-clear snapshot: the store file loses the verdict and the flag."""
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(snapshot))
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
        self.assertEqual(arch["status"][self.g("g4")], "completed", "the status is history, untouched")

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


if __name__ == "__main__":
    unittest.main()
