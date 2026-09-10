#!/usr/bin/env python3
"""Clearing one side of a delegation propagates across the handoff↔origin link, so a handed-off piece is
curated ONCE (the user 2026-06-23): clearing the sender's umbrella takes the recipient's copy with it, and
clearing the recipient's card takes the sender's tracking node with it. One UndoClear restores both (they
ride the same batch). Drives _clear_all / _delegation_linked_ids against synthetic two-session stores.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_cp", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SENDER = "11111111-2222-3333-4444-555555555555"
RECIP = "66666666-7777-8888-9999-000000000000"
MID = "1782000000.1_2.TESTHOST"               # synthetic postal message id linking the two sides


def _node(sid, n, text, parent=None, complete=False, **extra):
    nd = {"id": "%s:g%d" % (sid, n), "text": text, "parentId": parent,
          "nodeComplete": complete, "blocked": False, "cleared": False, "t": 1, "mt": 1}
    nd.update(extra)
    return nd


class ClearPropagation(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))   # rebind STATE *and* GOALDIR/ERRORS/etc. — assigning jd.STATE
        jd.GOALDIR.mkdir(parents=True)          # alone left save_goals writing into LIVE ~/.local/state/romp/goals/
        self.saved_sessions = km._sessions
        km._sessions = lambda now: []                # no transcripts in the sandbox → session_closed=False
        # SENDER: umbrella g1 = own step g2 (done) + delegation g3 (handoff → RECIP, done)
        s = {"rompUuid": SENDER, "seq": 3, "placements": {}, "status": {}, "nodes": {
            "%s:g1" % SENDER: _node(SENDER, 1, "Color the tooltip labels"),
            "%s:g2" % SENDER: _node(SENDER, 2, "find hex values", parent="%s:g1" % SENDER, complete=True),
            "%s:g3" % SENDER: _node(SENDER, 3, "↪ delegated to recip: update render.ts",
                                    parent="%s:g1" % SENDER,                  # in-flight delegation (not yet done)
                                    handoff={"peer": RECIP, "msgId": MID})}}
        # RECIP: g1 = the delegated work (origin → SENDER:g3), top-level, done
        r = {"rompUuid": RECIP, "seq": 1, "placements": {}, "status": {}, "nodes": {
            "%s:g1" % RECIP: _node(RECIP, 1, "update render.ts",
                                   origin={"peer": SENDER, "goalId": "%s:g3" % SENDER, "msgId": MID})}}
        jd.save_goals(SENDER, s)
        jd.save_goals(RECIP, r)

    def tearDown(self):
        km._sessions = self.saved_sessions
        jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def _cleared(self):
        return set(km._cleared_ids().keys())

    def test_linked_ids_resolves_both_directions(self):
        self.assertEqual(km._delegation_linked_ids(["%s:g1" % SENDER]), {"%s:g1" % RECIP},
                         "sender umbrella subtree's handoff → the recipient node (matched by msgId)")
        self.assertEqual(km._delegation_linked_ids(["%s:g1" % RECIP]), {"%s:g3" % SENDER},
                         "recipient origin → the sender's tracking node (origin.goalId)")

    def test_clearing_sender_umbrella_clears_recipient_copy(self):
        km._clear_all(["%s:g1" % SENDER])
        c = self._cleared()
        self.assertIn("%s:g1" % SENDER, c, "the umbrella is cleared")
        self.assertIn("%s:g1" % RECIP, c, "the recipient's copy is cleared via the delegation link")
        self.assertTrue(jd.load_goals(RECIP)["nodes"]["%s:g1" % RECIP]["cleared"],
                        "and the durable node flag is set on the peer too")

    def test_clearing_recipient_card_clears_sender_tracking_node(self):
        km._clear_all(["%s:g1" % RECIP])
        c = self._cleared()
        self.assertIn("%s:g1" % RECIP, c)
        self.assertIn("%s:g3" % SENDER, c, "the sender's '↪ delegated to' tracking node clears too")

    def test_one_undo_restores_both_sides(self):
        km._clear_all(["%s:g1" % SENDER])
        self.assertTrue(self._cleared())
        km._undo_clear()                              # the linked id shares the batch timestamp → restored together
        self.assertEqual(self._cleared(), set(), "one UndoClear restores the umbrella AND the recipient copy")

    def test_a_non_delegation_clear_touches_only_itself(self):
        km._clear_all(["%s:g2" % SENDER])             # a plain own-work node, no handoff/origin
        self.assertEqual(self._cleared(), {"%s:g2" % SENDER}, "no spurious cross-session clears")

    # --- completion ("check off") propagates BOTH directions, so a delegated piece is resolved ONCE ---
    def test_resolving_recipient_checks_off_sender_tracking_node(self):
        self.assertTrue(km._resolve_node(RECIP, "%s:g1" % RECIP))
        self.assertTrue(jd.load_goals(SENDER)["nodes"]["%s:g3" % SENDER]["nodeComplete"],
                        "crossing off the recipient checks off the sender's '↪ delegated to' node")

    def test_resolving_sender_delegation_checks_off_recipient(self):
        self.assertTrue(km._resolve_node(SENDER, "%s:g3" % SENDER))
        self.assertTrue(jd.load_goals(RECIP)["nodes"]["%s:g1" % RECIP]["nodeComplete"],
                        "crossing off the sender's delegation checks off the recipient copy (immediate, no judge pass)")

    def test_resolving_a_plain_node_does_not_touch_the_peer(self):
        km._resolve_node(SENDER, "%s:g2" % SENDER)    # ui's own step, not a delegation
        self.assertFalse(jd.load_goals(RECIP)["nodes"]["%s:g1" % RECIP]["nodeComplete"],
                         "a non-delegation cross-off never reaches across sessions")

    # --- a blocked delegated piece surfaces needs-you on the RECIPIENT only, never twice ---
    def test_block_surfaces_only_on_the_recipient_not_the_sender(self):
        r = jd.load_goals(RECIP)
        jd.record_verdict(r, r["nodes"]["%s:g1" % RECIP], "closer", "block",
                          int(__import__("time").time()), why="pick a name")
        jd.rollup_status(r, False)
        self.assertEqual(r["status"]["%s:g1" % RECIP], "blocked", "the recipient shows needs-you")
        s = jd.load_goals(SENDER)
        jd.rollup_status(s, False)
        self.assertNotEqual(s["status"]["%s:g1" % SENDER], "blocked",
                            "the sender's umbrella does NOT inherit the block — no double needs-you")


if __name__ == "__main__":
    unittest.main()
