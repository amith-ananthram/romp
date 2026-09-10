#!/usr/bin/env python3
"""A kept decision brief must not outlive the answers to its own asks (the user 2026-07-24).

The incident, all fixtures SYNTHETIC: a card's brief asked for a go-ahead; the user gave it minutes
later ("answered in passing", recorded as an unblock); a later PROCEDURAL block (a failed nudge) then
re-displayed the same brief under a fresh needs-you chip — a two-hour-old, already-answered ask
reported as new work. The don't-clobber rule ("keep a real brief from an earlier genuine block")
is right only while nothing has answered that brief's asks: an unblock/reopen landing after the
brief's own stamp is the exact event that makes the kept text stale, and the proc-only path must
then fall through to the staller's fresh where-this-stands note instead.
"""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_bsup", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
TOP, SUB = SID + ":g1", SID + ":g2"
T0 = 1781100000          # the brief's stamp (briefedMt)


def _nodes(sub_log):
    return {TOP: {"id": TOP, "parentId": None, "log": []},
            SUB: {"id": SUB, "parentId": TOP, "log": sub_log}}


class BriefSuperseded(unittest.TestCase):
    def test_an_unblock_after_the_brief_supersedes_it(self):
        nodes = _nodes([{"ev_t": T0 + 660, "src": "unblocker", "kind": "unblock",
                         "why": "answered in passing", "at": T0 + 900}])
        self.assertTrue(jd._brief_superseded(nodes, [TOP, SUB], T0),
                        "the ask was answered after the brief was written — the kept text is stale")

    def test_a_user_reopen_after_the_brief_supersedes_it(self):
        nodes = _nodes([{"ev_t": T0 + 300, "src": "user", "kind": "reopen", "at": T0 + 300}])
        self.assertTrue(jd._brief_superseded(nodes, [TOP, SUB], T0))

    def test_events_before_the_brief_do_not(self):
        # the normal shape: the episode's own unblocks predate the brief written at its end
        nodes = _nodes([{"ev_t": T0 - 500, "src": "unblocker", "kind": "unblock", "at": T0 - 400}])
        self.assertFalse(jd._brief_superseded(nodes, [TOP, SUB], T0),
                         "history the brief already accounts for never staleness it")

    def test_blocks_and_settles_are_not_answers(self):
        nodes = _nodes([{"ev_t": T0 + 100, "src": "closer", "kind": "block", "at": T0 + 100},
                        {"ev_t": T0 + 200, "src": "romp", "kind": "settle", "at": T0 + 200}])
        self.assertFalse(jd._brief_superseded(nodes, [TOP, SUB], T0),
                         "only an unblock/reopen answers an ask; more blocks make the brief MORE current")

    def test_a_never_briefed_card_has_nothing_to_supersede(self):
        nodes = _nodes([{"ev_t": T0 + 660, "src": "unblocker", "kind": "unblock", "at": T0 + 900}])
        self.assertFalse(jd._brief_superseded(nodes, [TOP, SUB], None))

    def test_legacy_rows_fall_back_to_arrival_time(self):
        nodes = _nodes([{"src": "unblocker", "kind": "unblock", "at": T0 + 50}])   # no ev_t
        self.assertTrue(jd._brief_superseded(nodes, [TOP, SUB], T0))

    def test_the_keep_branch_consults_the_guard(self):
        # wiring pin: the proc-only don't-clobber keep is exactly where the stale brief leaked out.
        import inspect
        src = inspect.getsource(jd)
        self.assertIn('and not _brief_superseded(nodes, sub, nodes[top].get("briefedMt"))', src)


class BlockSince(unittest.TestCase):
    """_block_since — the per-paragraph brief stamp (the user 2026-07-24): when each owed ask was
    actually ASKED, from the sub-goal's own diary."""

    def test_newest_block_event_wins(self):
        nd = {"log": [{"kind": "block", "ev_t": T0 + 10, "at": T0 + 11},
                      {"kind": "unblock", "ev_t": T0 + 50, "at": T0 + 50},
                      {"kind": "block", "ev_t": T0 + 90, "at": T0 + 95}], "mt": T0}
        self.assertEqual(jd._block_since(nd), T0 + 90)

    def test_legacy_rows_and_bare_nodes_fall_back(self):
        self.assertEqual(jd._block_since({"log": [{"kind": "block", "at": T0 + 7}]}), T0 + 7,
                         "no ev_t → arrival time")
        self.assertEqual(jd._block_since({"log": [], "mt": T0 + 3, "t": T0}), T0 + 3,
                         "no block rows → the node's own mt")


class DoneSince(unittest.TestCase):
    """_done_since — the takeaway's per-paragraph stamp twin: when each completed outcome landed."""

    def test_newest_done_event_wins(self):
        nd = {"log": [{"kind": "done", "ev_t": T0 + 5, "at": T0 + 6},
                      {"kind": "reopen", "ev_t": T0 + 40, "at": T0 + 40},
                      {"kind": "done", "ev_t": T0 + 80, "at": T0 + 81}], "mt": T0}
        self.assertEqual(jd._done_since(nd), T0 + 80)

    def test_fallbacks_mirror_block_since(self):
        self.assertEqual(jd._done_since({"log": [{"kind": "done", "at": T0 + 9}]}), T0 + 9)
        self.assertEqual(jd._done_since({"log": [], "mt": T0 + 2}), T0 + 2)


if __name__ == "__main__":
    unittest.main()
