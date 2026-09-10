#!/usr/bin/env python3
"""_echo_clear_targets: the parse-identity backfill's clear selection (the user 2026-07-26).

The one-time sweep re-mints goals for placements orphaned by the 435d9df segment-identity change;
minted nodes whose evidence is entirely older than the age cutoff are bookkeeping echoes and get
cleared at the LARGEST all-old-subtree granularity — a mixed-age top keeps its fresh outcomes and
only the all-old sub clears; pre-existing nodes are never touched. Synthetic stores only."""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_echobackfill", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1785200000
OLD = NOW - 3 * 86400          # evidence well past the 24h cutoff
FRESH = NOW - 3600             # evidence from this afternoon


def _store(nodes):
    return {"rompUuid": SID, "nodes": nodes, "status": {}, "placements": {}}


def _n(gid, t, parent=None, cleared=False):
    return {"id": gid, "text": "x", "parentId": parent, "t": t,
            "nodeComplete": False, "blocked": False, "cleared": cleared}


class EchoClearTargets(unittest.TestCase):
    def test_all_old_minted_top_clears_at_the_top(self):
        g, s1, s2 = SID + ":g1", SID + ":g2", SID + ":g3"
        store = _store({g: _n(g, OLD), s1: _n(s1, OLD, g), s2: _n(s2, OLD, g)})
        self.assertEqual(jd._echo_clear_targets(store, {g, s1, s2}, NOW), [g],
                         "largest granularity: one clear at the top, the subtree rides roll-down")

    def test_mixed_age_top_keeps_fresh_and_clears_only_the_old_sub(self):
        g, old, fresh = SID + ":g1", SID + ":g2", SID + ":g3"
        store = _store({g: _n(g, OLD), old: _n(old, OLD, g), fresh: _n(fresh, FRESH, g)})
        self.assertEqual(jd._echo_clear_targets(store, {g, old, fresh}, NOW), [old],
                         "a mixed-age top keeps its fresh outcomes; only the all-old sub clears")

    def test_old_minted_sub_under_a_pre_existing_top_clears_the_sub_only(self):
        top, sub = SID + ":g1", SID + ":g2"
        store = _store({top: _n(top, OLD), sub: _n(sub, OLD, top)})
        self.assertEqual(jd._echo_clear_targets(store, {sub}, NOW), [sub],
                         "a pre-existing top is never a target even when its evidence is old")

    def test_fresh_mints_and_untouched_old_nodes_yield_nothing(self):
        g, h = SID + ":g1", SID + ":g2"
        store = _store({g: _n(g, FRESH), h: _n(h, OLD)})
        self.assertEqual(jd._echo_clear_targets(store, {g}, NOW), [],
                         "fresh mints stay; old nodes the sweep did not mint stay")

    def test_already_cleared_descendant_lets_the_ancestor_clear_but_is_not_re_emitted(self):
        g, done = SID + ":g1", SID + ":g2"
        store = _store({g: _n(g, OLD), done: _n(done, OLD, g, cleared=True)})
        self.assertEqual(jd._echo_clear_targets(store, {g}, NOW), [g],
                         "an already-cleared sub is off the board — it neither blocks nor re-clears")


class ApplyEchoClears(unittest.TestCase):
    """The apply step (user-approved 2026-07-26): mute-path clears — cleared.jsonl rows on ONE shared
    batch t (a single Undo restores the whole sweep) + a romp-authored clear verdict per target — and
    deliberately NO clear-wrap notify (no message file is ever produced; bookkeeping, not dismissal)."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))
        jd.GOALDIR.mkdir(parents=True)

    def tearDown(self):
        jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def test_apply_writes_batch_rows_and_romp_clear_verdicts(self):
        import json
        g, s1 = SID + ":g1", SID + ":g2"
        store = jd.load_goals(SID)
        store["nodes"] = {g: jd.GuardedNode(dict(_n(g, OLD), log=[])),
                          s1: jd.GuardedNode(dict(_n(s1, OLD, g), log=[]))}
        jd.save_goals(SID, store)
        store = jd.load_goals(SID)
        n = jd._apply_echo_clears(SID, store, [g], 1785200000.5, NOW, "why text")
        self.assertEqual(n, 1)
        rows = [json.loads(l) for l in (jd.STATE / "cleared.jsonl").read_text().splitlines()]
        self.assertEqual(rows, [{"id": g, "t": 1785200000.5, "op": "clear"}],
                         "one row per target, all on the shared sweep batch t")
        nd = jd.load_goals(SID)["nodes"][g]
        ev = [e for e in nd.get("log", []) if e.get("kind") == "clear"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["src"], "romp", "a romp-authored clear, not a user dismissal")
        self.assertTrue(nd.get("cleared"), "the durable node flag lands with the verdict")

    def test_apply_of_nothing_touches_nothing(self):
        store = jd.load_goals(SID)
        self.assertEqual(jd._apply_echo_clears(SID, store, [], 1.0, NOW, "why"), 0)
        self.assertFalse((jd.STATE / "cleared.jsonl").exists(), "no targets → no rows, no save")


if __name__ == "__main__":
    unittest.main()
