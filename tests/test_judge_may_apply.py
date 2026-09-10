#!/usr/bin/env python3
"""may_apply — THE arbitration gate (plan P1, the user 2026-07-06), plus the P2 placement-identity
migration. The authority ladder (user > agent > judges; a user action floors judge evidence; view-clear
seals) is stated and tested HERE, once — write sites just ask the gate. Includes the ratchet: a lint
test that fails if any code outside may_apply calls the staleness guards directly, so the ladder can't
quietly re-scatter. Synthetic fixtures only."""
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
jd = load_source("romp_judge_mayapply", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
G1 = SID + ":g1"
FA = 1781100000


def node(**kw):
    nd = {"id": G1, "text": "Ship it", "parentId": None, "nodeComplete": False,
          "blocked": False, "cleared": False, "trail": [], "t": FA - 500, "mt": FA - 100}
    nd.update(kw)
    return nd


class TheLadder(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        self.store = {"rompUuid": SID, "nodes": {G1: node(followupAt=FA)}, "placements": {}, "status": {}}

    def test_judge_done_floor_equality_lands(self):
        nd = self.store["nodes"][G1]
        self.assertFalse(jd.may_apply(self.store, nd, "judge", "done", FA - 1), "older evidence: void")
        self.assertTrue(jd.may_apply(self.store, nd, "judge", "done", FA), "the resolving turn shares the stamp: lands")
        self.assertTrue(jd.may_apply(self.store, nd, "judge", "done", FA + 1), "newer evidence: lands")

    def test_judge_block_floor_equality_voids(self):
        nd = self.store["nodes"][G1]
        self.assertFalse(jd.may_apply(self.store, nd, "judge", "block", FA - 1), "older evidence: void")
        self.assertFalse(jd.may_apply(self.store, nd, "judge", "block", FA), "computed from the answered ask: void")
        self.assertTrue(jd.may_apply(self.store, nd, "judge", "block", FA + 1), "a genuinely new ask: blocks")

    def test_judge_awaiting_floor_equality_lands(self):
        # the closer's ⏳ annotation rides the DONE-style floor: the turn that processes the user's
        # reply may itself dispatch async work and legitimately wait (equality = that turn's own audit)
        nd = self.store["nodes"][G1]
        self.assertFalse(jd.may_apply(self.store, nd, "closer", "awaiting", FA - 1), "pre-reply stamp: void")
        self.assertTrue(jd.may_apply(self.store, nd, "closer", "awaiting", FA), "the reply-triggered turn: lands")
        self.assertTrue(jd.may_apply(self.store, nd, "closer", "awaiting", FA + 1), "newer evidence: lands")

    def test_no_user_floor_means_judges_flow(self):
        nd = node()   # no followupAt
        self.assertTrue(jd.may_apply(self.store, nd, "judge", "done", FA - 999))
        self.assertTrue(jd.may_apply(self.store, nd, "judge", "block", FA - 999))

    def test_agent_verdicts_never_gated(self):
        nd = self.store["nodes"][G1]
        self.assertTrue(jd.may_apply(self.store, nd, "agent", "done", FA - 999),
                        "the agent's own to-do list outranks judge-evidence floors")

    def test_view_clear_seals_reopen_for_every_source(self):
        nd = self.store["nodes"][G1]
        self.assertTrue(jd.may_apply(self.store, nd, "user", "reopen"))
        (Path(self.td) / "romp").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "cleared.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "cleared.jsonl").write_text(json.dumps({"id": G1, "op": "clear"}) + "\n")
        self.assertFalse(jd.may_apply(self.store, nd, "user", "reopen"), "crossed off → sealed")
        self.assertFalse(jd.may_apply(self.store, nd, "judge", "reopen"), "sealed against ALL sources")


class LintNoScatteredGuards(unittest.TestCase):
    def test_staleness_guards_only_called_from_may_apply(self):
        # THE RATCHET: the ladder lives in may_apply and nowhere else. A new write site calling the
        # guards directly re-scatters the policy — fail it here.
        src = Path(os.path.join(BIN, "romp-judge")).read_text()
        offenders = []
        for i, line in enumerate(src.splitlines(), 1):
            s = line.strip()
            if s.startswith("def ") or s.startswith("#") or s.startswith('"'):
                continue
            if "_done_is_stale(" in s or "_block_is_stale(" in s:
                offenders.append((i, s))
        # the only permitted call lines are inside may_apply's body: done, block, and awaiting (the
        # closer's ⏳ annotation rides the done-style floor, 2026-07-22) — each a bare `return not` there
        self.assertEqual(len(offenders), 3, "guards called outside may_apply: %r" % offenders)
        for _, s in offenders:
            self.assertTrue(s.startswith("return not "), "unexpected guard call shape: %r" % s)


class PlacementsMigration(unittest.TestCase):
    def test_pre_versioning_store_seals_too(self):
        # Originally grandfathered (adopted without sealing) — no longer safe once the atom set grew
        # (2026-07-10): an unversioned store predates versioning itself, so a revive would replay every
        # newly-visible atom in its history as fresh goals. Sealed like any other version mismatch.
        store = {"rompUuid": SID, "nodes": {}, "placements": {SID + ":100:aa": SID + ":g1"}, "status": {}}
        changed = jd._migrate_placements(store, [SID + ":200:bb"], live={SID + ":200:bb"})
        self.assertTrue(changed)
        self.assertEqual(store["placementsV"], jd.PLACEMENTS_V)
        self.assertIsNone(store["placements"][SID + ":200:bb"], "sealed: revived history cannot replay")
        self.assertEqual(store["placements"][SID + ":100:aa"], SID + ":g1", "existing keys untouched")

    def test_fresh_empty_store_adopts_without_sealing(self):
        # an unversioned store with NOTHING recorded is a brand-new session, not a pre-versioning
        # dormant one — its first asks must plan, not seal (load_goals stamps new stores at birth)
        store = {"rompUuid": SID, "nodes": {}, "placements": {}, "status": {}}
        changed = jd._migrate_placements(store, [SID + ":200:bb"], live={SID + ":200:bb"})
        self.assertTrue(changed)
        self.assertEqual(store["placementsV"], jd.PLACEMENTS_V)
        self.assertNotIn(SID + ":200:bb", store["placements"], "a fresh session's first ask still plans")

    def test_version_mismatch_seals_ready_unplaced_units(self):
        store = {"rompUuid": SID, "placementsV": jd.PLACEMENTS_V - 1, "nodes": {},
                 "placements": {SID + ":100:aa": SID + ":g1"}, "status": {}}
        ready = [SID + ":200:bb", SID + ":300:cc", SID + ":100:aa"]
        jd._migrate_placements(store, ready, live=set(ready))
        self.assertEqual(store["placementsV"], jd.PLACEMENTS_V)
        self.assertIsNone(store["placements"][SID + ":200:bb"], "sealed: dormant history cannot replay")
        self.assertIsNone(store["placements"][SID + ":300:cc"])
        self.assertEqual(store["placements"][SID + ":100:aa"], SID + ":g1", "an exact-placed key is untouched")

    def test_current_version_is_a_noop(self):
        store = {"rompUuid": SID, "placementsV": jd.PLACEMENTS_V, "nodes": {}, "placements": {}, "status": {}}
        self.assertFalse(jd._migrate_placements(store, [SID + ":200:bb"], live=set()))
        self.assertEqual(store["placements"], {})


G2 = SID + ":g2"
G3 = SID + ":g3"


class TheSubtreeFloor(unittest.TestCase):
    """A user reply/move lands on the CARD — the rollup — never on individual sub-goals, and
    optimistic_followup/user_move already unblock the whole subtree on that gesture. The evidence floor
    must match: a judge verdict on a DESCENDANT computed from evidence at/before the user's reply is
    exactly as stale as the same verdict on the replied node itself. (2026-07-20: a closer pass
    re-blocked a just-answered sub-goal 35 seconds after the reply, from a pre-reply segment — the child
    carried no floor of its own, the per-node check waved the block through, and the card flashed back
    to needs-input with nothing in Working until the next pass healed it.) Synthetic fixtures only."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        self.store = {"rompUuid": SID, "placements": {}, "status": {}, "nodes": {
            G1: node(followupAt=FA),
            G2: {"id": G2, "text": "step", "parentId": G1, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": FA - 400, "mt": FA - 200},
            G3: {"id": G3, "text": "sub-step", "parentId": G2, "nodeComplete": False,
                 "blocked": False, "cleared": False, "trail": [], "t": FA - 400, "mt": FA - 200},
        }}

    def test_a_reply_on_the_card_floors_blocks_on_descendants(self):
        for nid in (G2, G3):
            nd = self.store["nodes"][nid]
            self.assertFalse(jd.may_apply(self.store, nd, "closer", "block", FA - 300),
                             "%s: pre-reply evidence re-imposes the ask the user just answered" % nid)
            self.assertFalse(jd.may_apply(self.store, nd, "closer", "block", FA),
                             "%s: computed from the answered ask itself — void" % nid)
            self.assertTrue(jd.may_apply(self.store, nd, "closer", "block", FA + 1),
                            "%s: a genuinely new ask still blocks" % nid)

    def test_the_done_asymmetry_holds_through_ancestors(self):
        nd = self.store["nodes"][G3]
        self.assertFalse(jd.may_apply(self.store, nd, "closer", "done", FA - 1),
                         "stale replay must not re-complete a replied thread's step")
        self.assertTrue(jd.may_apply(self.store, nd, "closer", "done", FA),
                        "the resolving turn shares the stamp: lands")

    def test_no_floor_anywhere_flows(self):
        self.store["nodes"][G1].pop("followupAt", None)
        nd = self.store["nodes"][G3]
        self.assertTrue(jd.may_apply(self.store, nd, "closer", "block", FA - 999))
        self.assertTrue(jd.may_apply(self.store, nd, "closer", "done", FA - 999))

    def test_a_parent_cycle_cannot_hang_the_gate(self):
        self.store["nodes"][G2]["parentId"] = G3          # G2 <-> G3, no floor on either
        self.store["nodes"][G1].pop("followupAt", None)
        self.assertTrue(jd.may_apply(self.store, self.store["nodes"][G3], "closer", "block", FA - 1))

    def test_the_incident_shape_refuses_at_the_record_seam(self):
        # Replica of the 12:28:09 stale re-block: the child was blocked, the user's reply lifted it
        # (user unblock, no floor stamped on the child), then a pass re-recorded the SAME block with
        # its pre-reply segment time. record_verdict must refuse — nothing appended, nothing flipped.
        nd = self.store["nodes"][G2]
        nd["log"] = [
            {"ev_t": FA - 300, "src": "closer", "kind": "block", "why": "approve the handshake", "at": FA - 250},
            {"ev_t": FA, "src": "user", "kind": "unblock", "why": "answered by the user's reply to the card", "at": FA},
        ]
        ok = jd.record_verdict(self.store, nd, "closer", "block", ev_t=FA - 300, why="approve the handshake")
        self.assertFalse(ok, "pre-reply evidence on a just-answered sub-goal must not re-enter the diary")
        self.assertEqual(len(nd["log"]), 2, "the refused verdict left no event behind")


if __name__ == "__main__":
    unittest.main()
