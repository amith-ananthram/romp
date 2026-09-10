#!/usr/bin/env python3
"""The card-reply bulk-unblock leak (the user 2026-07-20): a reply to a card optimistically clears
EVERY block in its subtree (_unblock_subtree via optimistic_followup), but a reply answering one of
three asks does not answer the other two — they silently lost their needs-you status and became quiet
open subs nothing re-surfaced. The fix makes the lift PROVISIONAL: the followup planner pass receives
the still-dangling lifted asks (_lifted_by_reply) and re-asserts the ones the reply did not answer
(_reassert_blocks), with the reply segment as fresh evidence clamped strictly past the floor (the
integer-second clock can make the reply share the floor's stamp, and equality voids a block).
XDG_STATE_HOME is pointed at a temp dir BEFORE the judge loads. Synthetic fixtures only."""
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_bulkunblock", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
TOP = SID + ":g1"
SUBS = [SID + ":g2", SID + ":g3", SID + ":g4"]
ASKS = ["which format do you want?", "who records the capture?", "keep or drop the legacy page?"]
T0 = int(time.time()) - 600


def _store():
    nodes = {TOP: {"id": TOP, "text": "docs overhaul", "parentId": None, "nodeComplete": False,
                   "blocked": False, "cleared": False, "trail": [], "t": T0 - 100, "mt": T0}}
    for nid, ask in zip(SUBS, ASKS):
        nodes[nid] = {"id": nid, "text": "piece " + nid[-2:], "parentId": TOP, "nodeComplete": False,
                      "blocked": False, "cleared": False, "trail": [], "t": T0 - 50, "mt": T0}
    store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": nodes}
    for nid, ask in zip(SUBS, ASKS):
        assert jd.record_verdict(store, nodes[nid], "planner", "block", T0, why=ask, seg="s0")
    return store


class LiftAndDangle(unittest.TestCase):
    def test_a_card_reply_lifts_every_block_and_they_dangle(self):
        store = _store()
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        T1 = T0 + 300
        self.assertTrue(jd.optimistic_followup(SID, TOP, text="use markdown for the format", now=T1))
        st = jd.load_goals(SID)
        for nid in SUBS:
            self.assertFalse(st["nodes"][nid]["blocked"], "the reply's bulk lift cleared %s" % nid)
        lifted = jd._lifted_by_reply(st, TOP)
        self.assertEqual([n for (n, _a, _t) in lifted], SUBS, "all three dangle, oldest-first")
        self.assertEqual([a for (_n, a, _t) in lifted], ASKS, "each carries its own pending ask")

    def test_a_ruled_on_sub_stops_dangling(self):
        store = _store()
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        jd.optimistic_followup(SID, TOP, text="answering one of them", now=T0 + 300)
        st = jd.load_goals(SID)
        # a later done verdict on g2 (fresh evidence) rules it — no longer dangling
        self.assertTrue(jd.record_verdict(st, st["nodes"][SUBS[0]], "planner", "done",
                                          T0 + 400, why="delivered"))
        self.assertEqual([n for (n, _a, _t) in jd._lifted_by_reply(st, TOP)], SUBS[1:])


class Reassert(unittest.TestCase):
    def test_reassert_lands_even_at_floor_equality(self):
        # the reply segment can share the floor's very second; a plain planner block at that ev_t is
        # VOID by design — the reassert clamps strictly past the floor, so the judged "not answered"
        # ruling sticks.
        store = _store()
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        T1 = T0 + 300
        jd.optimistic_followup(SID, TOP, text="partial answer", now=T1)
        st = jd.load_goals(SID)
        nd = st["nodes"][SUBS[1]]
        self.assertFalse(jd.record_verdict(st, nd, "planner", "block", T1, why=ASKS[1]),
                         "a plain catch-up block at the floor stays voided (the existing guard)")
        jd._reassert_blocks(st, "s9", T1, [(SUBS[1], ASKS[1])])
        self.assertTrue(st["nodes"][SUBS[1]]["blocked"], "the reassert lands past the floor")
        jd.rollup_status(st, False)
        self.assertTrue(st["nodes"][SUBS[1]]["blocked"], "and survives the fold re-roll")
        self.assertEqual([n for (n, _a, _t) in jd._lifted_by_reply(st, TOP)],
                         [SUBS[0], SUBS[2]], "a reasserted sub no longer dangles")

    def test_reassert_skips_resolved_or_recleared_nodes(self):
        store = _store()
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        T1 = T0 + 300
        jd.optimistic_followup(SID, TOP, text="reply", now=T1)
        st = jd.load_goals(SID)
        self.assertTrue(jd.record_verdict(st, st["nodes"][SUBS[0]], "planner", "done",
                                          T1 + 50, why="shipped meanwhile"))
        jd._reassert_blocks(st, "s9", T1, [(SUBS[0], ASKS[0])])
        self.assertFalse(st["nodes"][SUBS[0]]["blocked"], "a since-resolved sub is never re-blocked")


class WiringPins(unittest.TestCase):
    SRC = open(os.path.join(BIN, "romp-judge")).read()
    KSRC = open(os.path.join(BIN, "romp-kernel")).read()

    def test_the_followup_planner_is_told_about_the_lifted_asks(self):
        # the asks themselves ride a MARKED content section, so their judge-written whys are no longer
        # inlined in the note's own instruction prose
        self.assertIn('_sec("lifted-asks", lifted, mk)', self.SRC)
        self.assertIn("optimistically cleared the earlier pending asks listed", self.SRC)
        self.assertIn("Never re-assert an ask the reply answered.", self.SRC)
        self.assertIn("lifted_blocks=[(i, a) for i, (_n, a) in sorted(lifted_by_num.items())]", self.SRC)

    def test_continuation_applies_the_models_reasserts_and_pivot_restores_mechanically(self):
        self.assertIn('if o.get("do") == "block" and o.get("goal") in lifted_by_num', self.SRC)
        # a PIVOT ("this goal is unchanged") restores the blocks THIS gesture's send lifted
        self.assertIn("if lt and floor_now and lt >= floor_now", self.SRC)

    def test_the_lift_why_is_one_shared_constant(self):
        self.assertEqual(jd.REPLY_UNBLOCK_WHY, "answered by the user's reply to the card")
        self.assertIn("_unblock_subtree(store, gid, now, REPLY_UNBLOCK_WHY)", self.SRC)

    def test_nudge_prompts_ask_per_piece_and_accept_obsolete(self):
        # judge side: the nudge planner resolves each reported piece on its own item, obsolete included
        self.assertIn("also resolve each reported piece on its **own** listed item", self.SRC)
        # kernel side: both nudge bodies invite the drop shape, so the reply can actually say it
        self.assertIn("If one is no longer needed, just say so", self.KSRC)
        self.assertIn("If one is no longer needed, just say so.", self.KSRC)


if __name__ == "__main__":
    unittest.main()
