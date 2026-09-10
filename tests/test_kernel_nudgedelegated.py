#!/usr/bin/env python3
"""Auto-nudge exemption for fully-delegated goals (the user 2026-06-22): a goal whose only OUTSTANDING work
is delegated to peers (handoff-tracking nodes, planted by the courier) must NOT be auto-nudged — this session
has nothing to do itself, it's waiting on peers. But if the goal has parts it's handling ITSELF, those stay
nudgeable. _all_outstanding_delegated decides it: True iff every open LEAF in the goal's subtree carries a
`handoff` marker. Self-contained synthetic stores; no real session data.
"""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_nd", os.path.join(BIN, "romp-kernel"))

TOP = "s:g1"


def node(nid, parent=None, complete=False, handoff=False, cleared=False):
    nd = {"id": nid, "parentId": parent, "nodeComplete": complete, "cleared": cleared}
    if handoff:
        nd["handoff"] = {"peer": "peerSid", "msgId": "m1"}
    return nd


def nodes(*ns):
    return {n["id"]: n for n in ns}


class OutstandingDelegated(unittest.TestCase):
    def test_bare_working_top_is_NOT_exempt(self):
        nd = nodes(node(TOP))                                     # a plain working goal, no children
        self.assertFalse(km._all_outstanding_delegated(nd, TOP), "an ordinary working goal stays nudgeable")

    def test_only_open_leaf_is_a_handoff_is_exempt(self):
        nd = nodes(node(TOP), node("s:g2", TOP, handoff=True))    # the goal's one open piece is delegated
        self.assertTrue(km._all_outstanding_delegated(nd, TOP), "all outstanding work delegated → exempt")

    def test_mixed_own_and_delegated_is_NOT_exempt(self):
        nd = nodes(node(TOP), node("s:g2", TOP, handoff=True), node("s:g3", TOP))   # one delegated, one its own
        self.assertFalse(km._all_outstanding_delegated(nd, TOP), "it handles a part itself → nudge on that part")

    def test_delegated_plus_a_DONE_own_part_is_exempt(self):
        # its own part is finished; the only thing left open is the delegated piece → exempt
        nd = nodes(node(TOP), node("s:g2", TOP, handoff=True), node("s:g3", TOP, complete=True))
        self.assertTrue(km._all_outstanding_delegated(nd, TOP), "a completed own part doesn't count as outstanding")

    def test_fully_delegated_top_node_is_exempt(self):
        nd = nodes(node(TOP, handoff=True))                      # the top itself is a handoff umbrella, no children
        self.assertTrue(km._all_outstanding_delegated(nd, TOP), "a fully-delegated top is exempt")

    def test_cleared_leaf_does_not_count(self):
        # the only non-handoff open node is cleared → not outstanding → the remaining open leaf is delegated
        nd = nodes(node(TOP), node("s:g2", TOP, handoff=True), node("s:g3", TOP, cleared=True))
        self.assertTrue(km._all_outstanding_delegated(nd, TOP), "a cleared part is not outstanding work")


if __name__ == "__main__":
    unittest.main()
