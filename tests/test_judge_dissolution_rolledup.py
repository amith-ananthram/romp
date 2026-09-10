#!/usr/bin/env python3
"""A dissolved container's rolled-up child is judged by its own diary, and appends no settle row without a
done verdict of its own (review 2026-09-06). rollup_status's roll-down folds a resolved top's open children
into an eventless display cache (nodeComplete plus the rolledUp marker); _materialize_from_log and
record_verdict both skip a rolledUp node, because an ANCESTOR's resolution owns those flags. Umbrella
dissolution (T101) re-parented such a child to top level without dropping the marker, so the promoted top
kept a done flag nothing ever re-derived: is_complete read it, the settle branch never found settledDone
(the settle row it appended never materialized), and every rollup appended one more settle row plus one
more seam. One live node reached LOG_CAP (64 settle rows, logTrunc) with its store's seams at cap,
republishing about 1 MB per pass. An unroll that stopped at the promoted child would leave the child's own
rolled-up descendants done under a now-open top; and a healed top whose diary holds the bug's settle rows
would fold to settledDone on an open node, which reads Working but is sealed for every judge.

Pins: (1) the dissolving rollup drops the marker on the child AND its rolled-up descendants, and each
node's own diary decides (no row: open; a rolled-away block: blocked), with no settle row, no seam and no
change on later rollups; (2) a promoted child with its OWN done verdict settles exactly once, and a
descendant under it stays folded; (3) a child re-parented under a still-resolved solid ancestor is
re-folded by the same rollup's roll-down; (4) a store already published with the stale marker, on the
top or on a descendant, is repaired on its next rollup (one publish) and later rollups publish nothing;
(5) a top whose diary holds the bug's settle rows is reopened once, by a romp reopen row plus one
judge-errors row, and is unsealed. SYNTHETIC fixtures only; a private synthetic sid (goal-store tests
never share the placeholder sid)."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_dissolve_rolledup", os.path.join(BIN, "romp-judge"))

SID = "d1550001-1111-4222-8333-000000000001"    # private synthetic sid, never the shared placeholder
T = 1781100000
UMB, CHILD, OTHER, TOP, GRAND = (SID + ":g%d" % i for i in (1, 2, 3, 4, 5))


def _row(kind, ev, src="closer", why=None):
    return {"ev_t": ev, "src": src, "kind": kind, **({"why": why} if why else {}), "at": ev}


def _node(nid, text, parent, t=T, **kw):
    nd = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False,
          "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    nd.update(kw)
    return nd


def _rolled(nid, text, parent, log=None):
    """A child the roll-down folded: display-cache done plus the marker, no done verdict of its own."""
    return _node(nid, text, parent, nodeComplete=True, rolledUp=True, log=list(log or []))


def _settles(nd):
    return [e for e in (nd.get("log") or []) if e.get("kind") == "settle"]


def _reopens(nd):
    return [e for e in (nd.get("log") or []) if e.get("kind") == "reopen"]


def _menu_ids(st):
    return {nd["id"] for nd in jd.open_menu(st)}


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))

    def tearDown(self):
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _store(self, nodes):
        st = {"rompUuid": SID, "seq": len(nodes) + 1, "placements": {}, "status": {},
              "placementsV": jd.PLACEMENTS_V, "lastNode": OTHER, "nodes": {}}
        for nd in nodes:
            st["nodes"][nd["id"]] = nd
        st["nodes"][OTHER] = _node(OTHER, "tests for the api", None, t=T + 10)   # the focus: unrelated, open
        return jd._guard_nodes(st)

    def _umbrella_world(self, child_log=None, grand=False):
        """The live shape: a closer-ruled-done container over one child the roll-down folded (and, with
        `grand`, one folded sub under that child)."""
        nodes = [_node(UMB, "round of notes-api work", None, umbrella=True, nodeComplete=True,
                       log=[_row("done", T, why="the round shipped")]),
                 _rolled(CHILD, "add the web pane", UMB, child_log)]
        if grand:
            nodes.append(_rolled(GRAND, "wire the pane's route", CHILD))
        return self._store(nodes)

    def _error_rows(self):
        if not jd.ERRORS.exists():
            return []
        return [json.loads(line) for line in jd.ERRORS.read_text().splitlines() if line.strip()]


class DissolutionDropsTheMarker(_Base):
    def test_the_promoted_child_reads_its_own_diary_and_appends_no_settle_row(self):
        st = self._umbrella_world()
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertNotIn(UMB, st["nodes"], "premise: the container dissolved")
        self.assertIsNone(c.get("parentId"), "premise: the child is a top now")
        self.assertNotIn("rolledUp", c, "the marker is dropped with the container whose resolution it mirrored")
        self.assertFalse(c.get("nodeComplete"), "no verdict of its own: its diary says open")
        self.assertEqual(st["status"][CHILD], "working")
        self.assertEqual(_settles(c), [], "nothing completed, so nothing settles: no settle row")
        self.assertEqual(st.get("seams") or [], [], "and no seam")
        self.assertNotIn("settledDone", c)
        before = jd._store_content(st)
        jd.rollup_status(st, True)
        self.assertEqual(jd._store_content(st), before, "a second rollup changes nothing")

    def test_a_rolled_away_block_is_blocked_again_on_the_promoted_child(self):
        st = self._umbrella_world(child_log=[_row("block", T - 5, why="which pane layout?")])
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertNotIn("rolledUp", c)
        self.assertTrue(c.get("blocked"), "its own diary decides: the block the roll-down hid is blocked again")
        self.assertEqual(st["status"][CHILD], "blocked")
        self.assertEqual(_settles(c), [])

    def test_the_promoted_childs_rolled_up_descendants_are_unrolled_with_it(self):
        # the live shape one level deeper: the container's roll-down marked the child AND the child's own
        # sub. An unroll that stopped at the child would leave the sub done under an open top
        st = self._umbrella_world(grand=True)
        jd.rollup_status(st, True)
        g = st["nodes"][GRAND]
        self.assertEqual(g.get("parentId"), CHILD, "premise: the sub stays under the promoted child")
        self.assertNotIn("rolledUp", g, "the marker mirrored the container, which is gone")
        self.assertFalse(g.get("nodeComplete"), "its own diary has no done row")
        self.assertEqual(st["status"][CHILD], "working")
        self.assertIn(CHILD, _menu_ids(st))
        self.assertIn(GRAND, _menu_ids(st), "an open sub under an open top is in the planner's menu")
        self.assertEqual(_settles(st["nodes"][CHILD]), [])
        before = jd._store_content(st)
        jd.rollup_status(st, True)
        self.assertEqual(jd._store_content(st), before, "a second rollup changes nothing")

    def test_a_promoted_child_with_its_own_done_verdict_settles_exactly_once(self):
        # the roll-down never marks a child that is already complete, so this child carries no marker:
        # the legitimately-done sibling settles ONCE (one row, one seam, settledDone) and then holds
        st = self._store([
            _node(UMB, "round of notes-api work", None, umbrella=True),
            _node(CHILD, "add the web pane", UMB, nodeComplete=True,
                  log=[_row("done", T, why="the pane shipped")])])
        jd.rollup_status(st, True)
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertIsNone(c.get("parentId"))
        self.assertEqual(st["status"][CHILD], "completed")
        self.assertTrue(c.get("settledDone"), "the settle row materialized on the promoted top")
        self.assertEqual(len(_settles(c)), 1, "one settle row across two rollups")
        self.assertEqual(len(st.get("seams") or []), 1, "one seam across two rollups")
        before = jd._store_content(st)
        jd.rollup_status(st, True)
        self.assertEqual(jd._store_content(st), before)

    def test_a_descendant_under_a_still_done_intermediate_stays_rolled(self):
        # the child has a done verdict of its own (so the roll-down never marked it) and its sub was
        # marked. Promoted, the child is still done by its own diary, so its interior roll-down keeps the
        # sub folded: the sub is not in the menu, and the child settles once as a top
        st = self._store([
            _node(UMB, "round of notes-api work", None, umbrella=True, nodeComplete=True,
                  log=[_row("done", T, why="the round shipped")]),
            _node(CHILD, "add the web pane", UMB, nodeComplete=True,
                  log=[_row("done", T - 1, why="the pane shipped")]),
            _rolled(GRAND, "wire the pane's route", CHILD)])
        jd.rollup_status(st, True)
        jd.rollup_status(st, True)
        g = st["nodes"][GRAND]
        self.assertTrue(g.get("rolledUp") and g.get("nodeComplete"),
                        "a done ancestor is still above it: the roll-down re-marks it")
        self.assertNotIn(GRAND, _menu_ids(st))
        self.assertEqual(st["status"][CHILD], "completed")
        self.assertEqual(len(_settles(st["nodes"][CHILD])), 1, "the promoted child settles once")
        self.assertEqual(_settles(g), [], "a sub never settles")

    def test_a_child_under_a_still_resolved_solid_ancestor_is_refolded_by_the_same_rollup(self):
        # nested: a solid done top over a container over a folded child. The child re-parents to the
        # top, whose own roll-down re-derives the cache in this very rollup (the marker is a mirror of
        # the nearest resolved ancestor, and that ancestor is still resolved)
        st = self._store([
            _node(TOP, "ship the notes-api release", None, nodeComplete=True,
                  log=[_row("done", T, why="released")]),
            _node(UMB, "round of notes-api work", TOP, umbrella=True, nodeComplete=True, rolledUp=True),
            _rolled(CHILD, "add the web pane", UMB)])
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertEqual(c.get("parentId"), TOP, "re-parented to the first solid ancestor")
        self.assertTrue(c.get("rolledUp") and c.get("nodeComplete"),
                        "a resolved ancestor is still above it: the roll-down re-folds it")
        self.assertEqual(_settles(c), [], "a sub never settles")
        self.assertEqual(len(_settles(st["nodes"][TOP])), 1, "the top settles once")

    def test_a_rolled_child_with_no_diary_key_unrolls_to_open(self):
        # a pre-migration straggler: the roll-down marked a child that has no diary key at all. The unroll
        # resets the three flags BEFORE the fold asks, so the node reads open; without the reset, the
        # unmigrated-node guard meets a done flag with no history, freezes it (an unroll no later pass
        # repairs, the marker being gone) and files a row
        st = self._umbrella_world()
        with jd._authority():
            del st["nodes"][CHILD]["log"]
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertIsNone(c.get("parentId"), "premise: promoted")
        self.assertNotIn("rolledUp", c)
        self.assertFalse(c.get("nodeComplete"), "no diary, no marker: open, not frozen done")
        self.assertEqual(st["status"][CHILD], "working")
        self.assertEqual(self._error_rows(), [], "no unmigrated-node row: the flags were reset before the fold")


class StaleMarkerHeals(_Base):
    def test_a_store_published_with_the_stale_marker_is_repaired_on_its_next_rollup(self):
        # the shape the bug left on disk: the container already gone, the child a top still marked, its
        # diary holding the settle rows earlier rollups appended, seams stamped alongside
        spam = [_row("settle", T + 10, src="romp") for _ in range(3)]
        st = self._store([_rolled(CHILD, "add the web pane", None, spam)])
        st["seams"] = [{"t": T + 100 + i, "top": CHILD, "text": "add the web pane", "segs": []}
                       for i in range(3)]
        jd.save_goals(SID, st)
        rev0 = jd._disk_rev(SID)
        s1 = jd.load_goals(SID)
        jd.rollup_status(s1, True)
        jd.save_goals(SID, s1)                                   # the repair is one publish
        c = s1["nodes"][CHILD]
        self.assertNotIn("rolledUp", c, "a top has no ancestor to mirror: the marker is dropped")
        self.assertFalse(c.get("nodeComplete"), "its own diary has no done row")
        self.assertEqual(s1["status"][CHILD], "working")
        self.assertEqual(len(_settles(c)), 3, "no settle row appended")
        self.assertEqual(len(s1["seams"]), 3, "no seam appended")
        rev1 = jd._disk_rev(SID)
        self.assertGreater(rev1, rev0, "the repair is a real change: one publish")
        s2 = jd.load_goals(SID)
        jd.rollup_status(s2, True)
        jd.save_goals(SID, s2)
        self.assertEqual(jd._disk_rev(SID), rev1, "later passes republish nothing")

    def test_a_stale_descendant_under_a_stale_top_is_unrolled_through_the_file(self):
        # both marked, no settle rows (the top's rows are the next test's subject). The sub is inserted
        # FIRST so the repair meets it before its ancestor: the ancestor check must not read a rolled-up
        # ancestor's stale done flag as a resolution, or the sub would need a second pass
        st = self._store([_rolled(GRAND, "wire the pane's route", CHILD),
                          _rolled(CHILD, "add the web pane", None)])
        jd.save_goals(SID, st)
        rev0 = jd._disk_rev(SID)
        s1 = jd.load_goals(SID)
        jd.rollup_status(s1, True)
        jd.save_goals(SID, s1)
        c, g = s1["nodes"][CHILD], s1["nodes"][GRAND]
        self.assertNotIn("rolledUp", c)
        self.assertNotIn("rolledUp", g, "no resolved ancestor is left: the sub's marker is dropped too")
        self.assertFalse(g.get("nodeComplete"))
        self.assertEqual(s1["status"][CHILD], "working")
        self.assertEqual(_menu_ids(s1) & {CHILD, GRAND}, {CHILD, GRAND})
        self.assertEqual(self._error_rows(), [], "no settle rows: nothing to reopen, nothing to report")
        rev1 = jd._disk_rev(SID)
        self.assertGreater(rev1, rev0, "the repair is one publish")
        s2 = jd.load_goals(SID)
        jd.rollup_status(s2, True)
        jd.save_goals(SID, s2)
        self.assertEqual(jd._disk_rev(SID), rev1, "later passes republish nothing")

    def test_a_stale_top_with_the_bugs_settle_rows_is_reopened_once_and_unsealed(self):
        # the live store's shape: settle rows appended while the node was rolled up, no done row. With
        # the marker gone the fold made those rows settledDone on an open node: Working on the board,
        # sealed for every judge (open_menu, the candidate filters), with nothing saying so
        spam = [_row("settle", T + 10, src="romp") for _ in range(3)]
        st = self._store([_rolled(CHILD, "add the web pane", None, spam)])
        jd.save_goals(SID, st)
        s1 = jd.load_goals(SID)
        jd.rollup_status(s1, True)
        jd.save_goals(SID, s1)
        c = s1["nodes"][CHILD]
        self.assertEqual(len(_reopens(c)), 1, "one reopen row ends the settled episode")
        self.assertEqual(_reopens(c)[0].get("src"), "romp")
        self.assertEqual(len(_settles(c)), 3, "the settle rows stay: the diary is append-only")
        self.assertNotIn("settledDone", c, "the reopen ended the settle in the fold")
        self.assertEqual(c.get("deltaSince"), T + 10, "the ended settle is the delta boundary")
        self.assertEqual(s1["status"][CHILD], "working")
        self.assertIn(CHILD, _menu_ids(s1), "unsealed: the planner's menu lists it")
        rows = self._error_rows()
        self.assertEqual([r.get("err") for r in rows], ["unroll-heal"], "one visible row names the heal")
        self.assertEqual(rows[0].get("goal"), CHILD)
        self.assertEqual(rows[0].get("fsid"), SID)
        rev1 = jd._disk_rev(SID)
        s2 = jd.load_goals(SID)
        jd.rollup_status(s2, True)
        jd.save_goals(SID, s2)
        self.assertEqual(len(_reopens(s2["nodes"][CHILD])), 1, "a second pass appends nothing")
        self.assertEqual(len(self._error_rows()), 1, "and reports nothing")
        self.assertEqual(jd._disk_rev(SID), rev1, "and republishes nothing")

    def test_the_dissolving_rollup_reopens_a_promoted_top_whose_diary_holds_the_rows(self):
        # the same rows reached through the dissolution site: a stale save-rebase republished the child's
        # old parentId after it had been promoted and settled by the bug, so the sweep re-dissolves the
        # container and promotes a child that already carries the rows
        spam = [_row("settle", T + 10, src="romp") for _ in range(2)]
        st = self._umbrella_world(child_log=spam)
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertIsNone(c.get("parentId"))
        self.assertEqual(len(_reopens(c)), 1)
        self.assertNotIn("settledDone", c)
        self.assertEqual(st["status"][CHILD], "working")
        self.assertIn(CHILD, _menu_ids(st))
        self.assertEqual([r.get("err") for r in self._error_rows()], ["unroll-heal"])
        before = jd._store_content(st)
        jd.rollup_status(st, True)
        self.assertEqual(jd._store_content(st), before, "a second rollup changes nothing")

    def test_a_rolled_away_block_under_the_rows_is_reopened_too(self):
        # blocked plus the rows is one unblock away from the sealed-Working shape (the unblocker's
        # candidate filter does not read settledDone, and a lift's unblock row leaves the settle in the
        # fold), so the heal reopens it as well: the block row stays in the diary, the state is open
        log = [_row("block", T - 5, why="which pane layout?"), _row("settle", T + 10, src="romp")]
        st = self._store([_rolled(CHILD, "add the web pane", None, log)])
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertFalse(c.get("blocked"))
        self.assertNotIn("settledDone", c)
        self.assertEqual(st["status"][CHILD], "working")
        self.assertTrue(any(e.get("kind") == "block" for e in c["log"]), "the diary keeps the block row")
        self.assertIn(CHILD, _menu_ids(st))
        self.assertEqual([r.get("err") for r in self._error_rows()], ["unroll-heal"])

    def test_a_promoted_child_without_the_rows_is_not_reopened(self):
        # no settle rows: the plain unroll is the whole repair; no reopen row, no judge-errors row
        st = self._umbrella_world(grand=True)
        jd.rollup_status(st, True)
        self.assertEqual(_reopens(st["nodes"][CHILD]), [])
        self.assertEqual(_reopens(st["nodes"][GRAND]), [])
        self.assertEqual(self._error_rows(), [])

    def test_the_heal_reopen_is_at_least_as_new_as_the_settle_it_ends(self):
        # the store's newest mt belonged to the dissolved container, now gone: every remaining node is
        # older than the settle rows. The reopen's ev_t has the settle's own moment as its floor, so the
        # row folds AFTER the settles it ends; a reopen dated by the store's latest node alone would sort
        # before them and the node would stay sealed
        spam = [_row("settle", T + 500, src="romp") for _ in range(2)]
        st = self._store([_rolled(CHILD, "add the web pane", None, spam)])
        self.assertLessEqual(max(max(n["t"], n["mt"]) for n in st["nodes"].values()), T + 10, "premise")
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertEqual(len(_reopens(c)), 1)
        self.assertGreaterEqual(_reopens(c)[0]["ev_t"], T + 500, "the reopen is at least as new as the settle")
        self.assertNotIn("settledDone", c)
        self.assertEqual(st["status"][CHILD], "working")
        self.assertIn(CHILD, _menu_ids(st))

    def test_the_heal_reopen_carries_the_stores_latest_evidence_moment(self):
        # when the store holds newer evidence than the settle, the reopen is dated by it: the row lands
        # where the store's history has reached, not back at the settle's moment
        spam = [_row("settle", T + 10, src="romp") for _ in range(2)]
        st = self._store([_rolled(CHILD, "add the web pane", None, spam)])
        st["nodes"][OTHER]["t"] = st["nodes"][OTHER]["mt"] = T + 200
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertEqual(len(_reopens(c)), 1)
        self.assertEqual(_reopens(c)[0]["ev_t"], T + 200, "the store's latest evidence moment")

    def test_a_view_cleared_stale_top_stays_sealed_and_reports_nothing(self):
        # the user crossed the card off the feed: may_apply refuses a reopen from any source, so the heal
        # drops the marker and leaves the node as the user left it, with nothing to report
        (jd.STATE / "cleared.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "cleared.jsonl").write_text(json.dumps({"id": CHILD, "op": "clear"}) + "\n")
        spam = [_row("settle", T + 10, src="romp") for _ in range(2)]
        st = self._store([_rolled(CHILD, "add the web pane", None, spam)])
        jd.rollup_status(st, True)
        c = st["nodes"][CHILD]
        self.assertNotIn("rolledUp", c, "the marker is still dropped")
        self.assertEqual(_reopens(c), [], "no reopen: the seal the user set holds")
        self.assertTrue(c.get("settledDone"), "sealed as the user left it")
        self.assertNotIn(CHILD, _menu_ids(st))
        self.assertEqual(self._error_rows(), [], "a refused heal reports nothing")

    def test_a_solid_ancestors_stale_done_flag_is_rewritten_before_the_ancestor_check(self):
        # the sub is inserted BEFORE its solid parent, whose done flag has no history behind it (the fold
        # demotes it to open on every rollup). The repair materializes the solid nodes first, so the
        # ancestor check reads the parent's fold-derived state, not the stale flag: the sub is unrolled in
        # this pass, not left done under an open top
        st = self._store([_rolled(GRAND, "wire the pane's route", TOP),
                          _node(TOP, "ship the notes-api release", None, nodeComplete=True, log=[])])
        jd.rollup_status(st, True)
        self.assertFalse(st["nodes"][TOP].get("nodeComplete"), "premise: the fold demoted the flag")
        g = st["nodes"][GRAND]
        self.assertNotIn("rolledUp", g, "checked against the rewritten ancestor, not its stale flag")
        self.assertFalse(g.get("nodeComplete"))
        self.assertIn(GRAND, _menu_ids(st))


if __name__ == "__main__":
    unittest.main()
