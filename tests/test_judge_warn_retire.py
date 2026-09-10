#!/usr/bin/env python3
"""Surface-bound warns retire with the state they annotate (the user 2026-07-16, quartz).

A decision-brief warn (cite-miss / brief-failed / brief-unreadable) annotates the BRIEF, which is the
card's surface only while it sits in Needs-you. Once the card unblocks, no new brief is ever written to
clear the warn — the old rule ("clears the next time the source is recorded") required the card to
block AGAIN — so a healthy Working card wore the yellow "warning" chip indefinitely. Same shape for the
summary family on a reopened completed card. rollup_status (the status owner) now retires each warn
with the state that shows its surface; a re-block / re-completion writes a fresh brief/summary, which
re-warns if it fails again. All fixtures SYNTHETIC (invented text, placeholder UUIDs).
"""
import os
import shutil
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
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
TOP = SID + ":g1"
T0 = 1781100000


def _store(rows, warns):
    """One top-level goal with the given diary rows + live warns, in the pre-load (plain dict) shape."""
    return {"rompUuid": SID, "seq": 1, "lastNode": None, "placements": {}, "status": {},
            "nodes": {TOP: {"id": TOP, "text": "arm the field campaign", "parentId": None,
                            "trail": [], "t": T0, "mt": T0 + 100, "log": rows, "warns": warns}}}


BLOCK_ROW = {"ev_t": T0 + 50, "src": "closer", "kind": "block",
             "why": "waiting on the user's go-ahead", "at": T0 + 50}
UNBLOCK_ROW = {"ev_t": T0 + 80, "src": "user", "kind": "unblock",
               "why": "answered by the user's reply to the card", "at": T0 + 80}
DONE_ROW = {"ev_t": T0 + 50, "src": "closer", "kind": "done", "why": "shipped", "at": T0 + 50}
REOPEN_ROW = {"ev_t": T0 + 80, "src": "user", "kind": "reopen",
              "why": "reopened (optimistic)", "at": T0 + 80}

BRIEF_MISS = {"kind": "cite-miss", "t": T0 + 60, "msg": "This decision brief's link may jump to the wrong message.",
              "detail": "d", "surface": "brief"}
SUMMARY_MISS = {"kind": "cite-miss", "t": T0 + 60, "msg": "This summary's link may jump to the wrong message.",
                "detail": "d", "surface": "summary"}
LEGACY_BRIEF_MISS = {"kind": "cite-miss", "t": T0 + 60,
                     "msg": "This decision brief's link may jump to the wrong message.", "detail": "d"}
BRIEF_FAILED = {"kind": "brief-failed", "t": T0 + 60, "msg": "m", "detail": "d"}
SUMMARY_FAILED = {"kind": "summary-failed", "t": T0 + 60, "msg": "m", "detail": "d"}
OTHER_WARN = {"kind": "task-store", "t": T0 + 60, "msg": "m", "detail": "d"}


class WarnRetire(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = Path(tempfile.mkdtemp())
        jd._rebind_state(self._td)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _rolled(self, rows, warns, closed=False):
        jd.save_goals(SID, _store(rows, warns))
        store = jd.load_goals(SID)
        jd.rollup_status(store, closed)
        return store

    def test_a_brief_warn_lives_while_the_card_is_blocked(self):
        store = self._rolled([BLOCK_ROW], [BRIEF_MISS])
        self.assertEqual(store["status"][TOP], "blocked")
        self.assertEqual([w["kind"] for w in store["nodes"][TOP]["warns"]], ["cite-miss"],
                         "the brief is the live surface — its warn stays")

    def test_a_brief_warn_retires_when_the_card_unblocks(self):
        # the quartz shape: interrupt-blocked, brief written (cite-miss), user re-engaged —
        # the card is Working again and the brief isn't shown; the warn must not outlive it
        store = self._rolled([BLOCK_ROW, UNBLOCK_ROW], [BRIEF_MISS])
        self.assertEqual(store["status"][TOP], "working")
        self.assertNotIn("warns", store["nodes"][TOP], "the brief's surface retired — so does its warn")

    def test_a_brief_giveup_warn_retires_when_the_card_unblocks(self):
        store = self._rolled([BLOCK_ROW, UNBLOCK_ROW], [BRIEF_FAILED])
        self.assertNotIn("warns", store["nodes"][TOP])

    def test_a_legacy_brief_cite_miss_is_classified_by_its_msg(self):
        store = self._rolled([BLOCK_ROW, UNBLOCK_ROW], [LEGACY_BRIEF_MISS])
        self.assertNotIn("warns", store["nodes"][TOP],
                         "a pre-surface-field record still retires (msg names the decision brief)")

    def test_a_summary_warn_lives_while_the_card_is_completed(self):
        store = self._rolled([DONE_ROW], [SUMMARY_MISS], closed=True)
        self.assertEqual(store["status"][TOP], "completed")
        self.assertEqual([w["kind"] for w in store["nodes"][TOP]["warns"]], ["cite-miss"])

    def test_a_summary_warn_retires_when_the_card_reopens(self):
        store = self._rolled([DONE_ROW, REOPEN_ROW], [SUMMARY_FAILED], closed=True)
        self.assertEqual(store["status"][TOP], "working")
        self.assertNotIn("warns", store["nodes"][TOP])

    def test_a_non_surface_warn_is_untouched(self):
        store = self._rolled([BLOCK_ROW, UNBLOCK_ROW], [OTHER_WARN, BRIEF_MISS])
        self.assertEqual([w["kind"] for w in store["nodes"][TOP]["warns"]], ["task-store"],
                         "only surface-bound warns retire; anomaly kinds stay")


if __name__ == "__main__":
    unittest.main()
