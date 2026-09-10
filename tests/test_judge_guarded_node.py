#!/usr/bin/env python3
"""The write seam (the user 2026-07-07, who asked whether the architecture could make it impossible): every node
loaded from disk or minted is a GuardedNode — writing a diary-owned key outside the diary/cache layer
RAISES at the write site instead of silently corrupting state for materialize to re-fight. Also pins the
event-vocabulary semantics that replaced the last hand-managed states: dismiss (the pivot verdict)
restores what the provisional msg-reopen displaced, an undo-clear reopen restores what the cross-off
displaced, and both stay shuffle-invariant. Synthetic fixtures only."""
import json
import random
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
import os

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_guarded", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
G1 = SID + ":g1"
T = 1781097000


def node(**kw):
    nd = {"id": G1, "text": "Ship it", "parentId": None, "nodeComplete": False,
          "blocked": False, "cleared": False, "trail": [], "t": T - 500, "mt": T - 100, "log": []}
    nd.update(kw)
    return jd.GuardedNode(nd)


class TheSeam(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_stray_writes_raise_for_every_protected_key(self):
        nd = node()
        for key, val in (("nodeComplete", True), ("blocked", True), ("cleared", True),
                         ("blockWhy", "x"), ("doneWhy", "x"), ("followupPending", True),
                         ("followupAt", T), ("settledAt", T), ("settledDone", True),
                         ("deltaSince", T), ("rolledUp", True), ("log", [])):
            with self.assertRaises(TypeError, msg=key):
                nd[key] = val
        with self.assertRaises(TypeError):
            nd.pop("log")
        nd["text"] = "renamed"                        # unprotected keys stay freely writable
        nd["mt"] = T
        self.assertEqual(nd["text"], "renamed")

    def test_loaded_stores_are_guarded(self):
        store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
                 "nodes": {G1: {"id": G1, "text": "Ship it", "parentId": None, "nodeComplete": False,
                                "blocked": False, "cleared": False, "trail": [], "t": T, "log": []}}}
        jd.save_goals(SID, store)
        st = jd.load_goals(SID)
        with self.assertRaises(TypeError):
            st["nodes"][G1]["blocked"] = True
        # …while the sanctioned path both records and materializes
        self.assertTrue(jd.record_verdict(st, st["nodes"][G1], "closer", "block", T + 10, why="pick"))
        self.assertTrue(st["nodes"][G1]["blocked"])
        self.assertEqual(st["nodes"][G1]["blockWhy"], "pick")
        jd.save_goals(SID, st)                        # GuardedNode JSON-serializes like a plain dict
        self.assertTrue(jd.load_goals(SID)["nodes"][G1]["blocked"])

    def test_mints_are_guarded(self):
        store = {"rompUuid": SID, "seq": 0, "placements": {}, "status": {}, "nodes": {}}
        jd.apply_plan(store, "s1", T, [{"do": "mint", "why": "w", "text": "G"}], [])
        nd = store["nodes"][store["placements"]["s1"]]
        self.assertIsInstance(nd, jd.GuardedNode)
        self.assertEqual(nd["log"], [], "born with an empty diary — the diary-era marker")
        with self.assertRaises(TypeError):
            nd["nodeComplete"] = True


class TheNewKinds(unittest.TestCase):
    def _fold(self, log):
        return jd._fold_node(node(log=list(log)))

    def test_dismiss_restores_what_the_provisional_reopen_displaced(self):
        # a BLOCKED card: optimistic msg-reopen flips it working; the pivot verdict says "that reply
        # wasn't about this goal" → back to blocked, chip answered (the 2026-07-03 track-card bug shape)
        log = [{"ev_t": T, "src": "closer", "kind": "block", "why": "pick a name", "at": T},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "msg": True, "at": T + 10},
               {"ev_t": T + 20, "src": "planner", "kind": "dismiss", "at": T + 20}]
        f = self._fold(log)
        self.assertEqual(f["state"], "blocked")
        self.assertEqual(f["blockWhy"], "pick a name")
        self.assertFalse(f["pending"])
        # …and a COMPLETED card returns to Completed with its ORIGINAL settledAt (no column re-entry)
        log = [{"ev_t": T, "src": "closer", "kind": "done", "why": "shipped", "at": T},
               {"ev_t": T + 5, "src": "romp", "kind": "settle", "at": T + 5},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "msg": True, "at": T + 10},
               {"ev_t": T + 20, "src": "planner", "kind": "dismiss", "at": T + 20}]
        f = self._fold(log)
        self.assertEqual((f["state"], f["settledAt"]), ("done", T + 5))

    def test_undo_reopen_restores_what_the_clear_displaced(self):
        log = [{"ev_t": T, "src": "closer", "kind": "done", "why": "shipped", "at": T},
               {"ev_t": T + 5, "src": "user", "kind": "clear", "at": T + 5},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "undo": True, "at": T + 10}]
        f = self._fold(log)
        self.assertEqual(f["state"], "done", "a cleared COMPLETED card comes back completed, never 'open'")
        self.assertFalse(f["held"], "an undo asserts nothing about doneness — no held-open")

    def test_an_undo_reopen_with_nothing_to_restore_is_a_state_no_op(self):
        # an undo-reopen restores the state its clear displaced; one that finds no clear before it (a second
        # undo row for one clear after a lost re-clear, or an undo whose clear never landed) has nothing to
        # restore and must leave the state as it stands: a completed card stays done, its settle stamp
        # stands, and the user's floor still advances. Before, it fell through to "open" like a plain
        # reopen, and the completed top came back Working
        log = [{"ev_t": T, "src": "closer", "kind": "done", "why": "shipped", "at": T},
               {"ev_t": T + 2, "src": "romp", "kind": "settle", "at": T + 2},
               {"ev_t": T + 5, "src": "user", "kind": "clear", "at": T + 5},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "undo": True, "at": T + 10},
               {"ev_t": T + 20, "src": "user", "kind": "reopen", "undo": True, "at": T + 20}]
        f = self._fold(log)
        self.assertEqual(f["state"], "done", "the second undo finds nothing to restore and changes nothing")
        self.assertFalse(f["held"], "an undo asserts nothing about doneness, so no held-open")
        self.assertEqual(f["settledAt"], T + 2, "the settle the first undo restored still stands")
        self.assertEqual(f["floor"], T + 20, "the user's floor advances to the undo all the same")
        log = [{"ev_t": T, "src": "closer", "kind": "done", "why": "shipped", "at": T},
               {"ev_t": T + 2, "src": "romp", "kind": "settle", "at": T + 2},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "undo": True, "at": T + 10}]
        f = self._fold(log)
        self.assertEqual(f["state"], "done", "an undo whose clear never landed changes nothing")
        self.assertFalse(f["held"])
        self.assertEqual((f["settledAt"], f["floor"]), (T + 2, T + 10))
        # a done node never reports held (_fold_node masks it at its return), so the two cases above cannot
        # tell an undo that holds from one that does not. On a node no verdict has landed on the state is
        # "open" and held reaches the return unmasked: a clear, its undo and a second undo leave the node
        # open and NOT held, with the floor at the last undo (green before the fix too; it pins that the
        # no-op branch, like the restore, never turns an undo into a user's plain reopen)
        log = [{"ev_t": T + 5, "src": "user", "kind": "clear", "at": T + 5},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "undo": True, "at": T + 10},
               {"ev_t": T + 20, "src": "user", "kind": "reopen", "undo": True, "at": T + 20}]
        f = self._fold(log)
        self.assertEqual((f["state"], f["held"], f["floor"]), ("open", False, T + 20),
                         "an undo never holds the node open, whether or not it had a clear to restore")

    def test_any_judge_event_answers_held_and_pending(self):
        log = [{"ev_t": T, "src": "closer", "kind": "done", "at": T},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "msg": True, "at": T + 10}]
        f = self._fold(log)
        self.assertTrue(f["held"] and f["pending"], "an unanswered user msg-reopen holds + wears the chip")
        log.append({"ev_t": T + 20, "src": "planner", "kind": "unblock", "at": T + 20})
        f = self._fold(log)
        self.assertFalse(f["held"] or f["pending"], "any later judge event means the reply was processed")

    def test_shuffle_invariance_holds_with_the_new_kinds(self):
        log = [{"ev_t": T, "src": "closer", "kind": "block", "why": "b", "at": T},
               {"ev_t": T + 5, "src": "romp", "kind": "settle", "at": T + 5},
               {"ev_t": T + 10, "src": "user", "kind": "reopen", "msg": True, "at": T + 10},
               {"ev_t": T + 20, "src": "planner", "kind": "dismiss", "at": T + 20},
               {"ev_t": T + 30, "src": "user", "kind": "clear", "at": T + 30},
               {"ev_t": T + 40, "src": "user", "kind": "reopen", "undo": True, "at": T + 40}]
        want = self._fold(log)
        rng = random.Random(7)
        for _ in range(20):
            shuffled = list(log)
            rng.shuffle(shuffled)
            self.assertEqual(self._fold(shuffled), want, "the fold reconstructs order — shuffle never matters")


if __name__ == "__main__":
    unittest.main()
