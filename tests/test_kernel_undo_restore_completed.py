"""UndoClear of a COMPLETED card must bring it back COMPLETED — not flicker through "working"/"⏳ awaiting"
and vanish (the user 2026-06-27). Since the diary owns state (2026-07-07) the mechanism is event-shaped:
the cross-off `clear` snapshots the state it displaces, the undo-reopen (undo:True) RESTORES that snapshot,
and _mark_nodes_cleared records a late `settle` for a completed top that had never settled (the ≈5% gap),
so the re-roll keeps it completed. SYNTHETIC fixtures only (placeholder ids)."""
import inspect
import os
import time
import unittest
from romp_load import load_source
from pathlib import Path
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
IID = SID + ":n1"
KID = SID + ":n2"
NOW = int(time.time())


def _run_undo(archive):
    """Drive the real undo pair (_restore_goal_archive → _mark_nodes_cleared False) with in-memory
    goal-store accessors; return the resulting live store."""
    jd = km.jd
    jd.migrate_store(archive)                          # archives are legacy-shaped: adopt their diaries
    live = {"rompUuid": SID, "nodes": {}, "placements": {}, "status": {}}
    orig = {n: getattr(jd, n) for n in ("load_goal_archive", "save_goal_archive", "load_goals", "save_goals")}
    jd.load_goal_archive = lambda sid: archive
    jd.save_goal_archive = lambda sid, a: None
    jd.load_goals = lambda sid: live
    jd.save_goals = lambda sid, s: None
    # The stores are in memory, but the undo pair still JOURNALS: the override rows and cleared.jsonl land under
    # the kernel's judge, one module object shared by every test module in the process, so at its import-bound
    # root they outlived this module and were replayed onto every later module's feed for the placeholder sid
    # (T281). A private root for the duration.
    saved_state = jd.STATE
    jd._rebind_state(Path(tempfile.mkdtemp()))
    try:
        km._restore_goal_archive([IID])
        km._mark_nodes_cleared([IID], False)
    finally:
        for n, fn in orig.items():
            setattr(jd, n, fn)
        jd._rebind_state(saved_state)
    return live


class UndoRestoreCompleted(unittest.TestCase):
    def test_the_undo_journal_does_not_outlive_the_module(self):
        # The residue pin (T281): the rows the undo pair journals stay under the private root; the run-wide root's
        # override journal and cleared.jsonl for the placeholder sid are exactly as they were.
        jd = km.jd
        shared = [jd.STATE / "overrides" / (SID + ".jsonl"), jd.STATE / "cleared.jsonl"]
        before = [(p.exists(), p.stat().st_size if p.exists() else None) for p in shared]
        _run_undo({"nodes": {IID: {"id": IID, "parentId": None, "nodeComplete": True, "cleared": True,
                                   "text": "x", "t": NOW - 900, "mt": NOW - 600}}, "status": {IID: "completed"}})
        self.assertEqual([(p.exists(), p.stat().st_size if p.exists() else None) for p in shared], before,
                         "the run-wide journal and cleared log are untouched by this module")

    def test_completed_top_comes_back_with_sticky_settledDone(self):
        # a roll-down-completed top that LACKS settledDone (the ≈5% gap) — the case that used to flicker:
        # the top's own nodeComplete is False; its rolled-up done child carries the completion bottom-up
        archive = {"nodes": {IID: {"id": IID, "parentId": None, "nodeComplete": False, "cleared": True,
                                   "text": "x", "t": NOW - 900, "mt": NOW - 600},
                             KID: {"id": KID, "parentId": IID, "nodeComplete": True, "cleared": False,
                                   "rolledUp": True, "text": "step", "t": NOW - 900, "mt": NOW - 700}},
                   "status": {IID: "completed"}}
        store = _run_undo(archive)
        self.assertIn(IID, store["nodes"], "the node is moved back to the live store")
        self.assertFalse(store["nodes"][IID].get("cleared"), "…un-cleared")
        self.assertTrue(store["nodes"][IID].get("settledDone"),
                        "a restored completed top gains its settle so the re-roll keeps it completed")
        self.assertEqual(store["status"].get(IID), "completed", "…and the re-roll agrees")

    def test_explicit_nodeComplete_top_is_also_stamped(self):
        archive = {"nodes": {IID: {"id": IID, "parentId": None, "nodeComplete": True, "cleared": True,
                                   "text": "x", "t": NOW - 900, "mt": NOW - 600}},
                   "status": {IID: "completed"}}
        store = _run_undo(archive)
        self.assertTrue(store["nodes"][IID].get("nodeComplete"),
                        "the undo-reopen restores the pre-clear DONE from its snapshot — never 'open'")
        self.assertTrue(store["nodes"][IID].get("settledDone"))
        self.assertEqual(store["status"].get(IID), "completed")

    def test_a_blocked_top_is_NOT_force_completed(self):
        archive = {"nodes": {IID: {"id": IID, "parentId": None, "nodeComplete": False, "blocked": True,
                                   "cleared": True, "blockWhy": "pick a name", "text": "x",
                                   "t": NOW - 900, "mt": NOW - 600}},
                   "status": {IID: "blocked"}}
        store = _run_undo(archive)
        self.assertFalse(store["nodes"][IID].get("settledDone"),
                         "a blocked/working top re-derives normally — never force-completed on restore")
        self.assertTrue(store["nodes"][IID].get("blocked"), "the pre-clear block is restored")

    def test_undo_still_restores_then_unclears_in_order(self):
        # the pair's ORDER carries the fix: restore first, so the un-clear pass finds the nodes
        src = inspect.getsource(km._undo_clear)
        self.assertIn("_restore_goal_archive(restored)", src)
        self.assertIn("_mark_nodes_cleared(restored, False)", src)
        self.assertLess(src.index("_restore_goal_archive"), src.index("_mark_nodes_cleared"),
                        "restore FIRST so the un-clear re-roll finds the nodes and holds completed")


if __name__ == "__main__":
    unittest.main()
