#!/usr/bin/env python3
"""Goal-card status UX (the user 2026-07-20): a giant card's stale sub-goals were unreadable and
unactionable — clicking a sub's title landed on the user's own bare retry (the mint segment's
trigger), and nothing said whether an open sub was still active. Read-side fixes under test:

- jd.junk_quote: a continuation stub ("retry", "continue", a bare slash command) never serves as a
  goal's deep-link anchor; the guard is READ-side so existing stores heal without a migration.
- kernel _node_anchor_uuids: the work anchor is the node's NEWEST trail segment (where it stands),
  not its mint; a junk mint quote ships promptAnchorUuid None (the render falls through to the work
  anchor, its existing null path).
- kernel _node_log_rows: the node's verdict log ships compacted with per-row anchors — the data the
  modal's per-item story renders.
- the "Drop" action (nodeOverride op:clear) = the user-authority clear verdict scoped to one sub.

XDG_STATE_HOME is pointed at a temp dir BEFORE the kernel loads. Synthetic fixtures only.
"""
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
kern = load_source("romp_kernel_statusux", os.path.join(BIN, "romp-kernel"))
jd = kern.jd

SID = "11111111-2222-3333-4444-555555555555"
NOW = int(time.time())


class JunkQuote(unittest.TestCase):
    def test_continuation_stubs_are_junk(self):
        for q in ("retry", "Retry.", "continue", "please continue", "go", "ok!", "yes",
                  "Continue from where you left off.", "keep going", "proceed"):
            self.assertTrue(jd.junk_quote(q), q)

    def test_bare_slash_commands_are_junk(self):
        self.assertTrue(jd.junk_quote("/compact"))
        self.assertTrue(jd.junk_quote("/clear"))

    def test_real_asks_are_not_junk(self):
        for q in ("fix the flaky auth test", "retry the deploy with the new token",
                  "i didnt mean to interrupt, that was a restart, keep going",
                  "/compact then summarize what changed"):
            self.assertFalse(jd.junk_quote(q), q)

    def test_missing_quote_is_unjudged(self):
        # pre-quote-era nodes keep their stored anchor — absence of evidence is not junk
        self.assertFalse(jd.junk_quote(None))
        self.assertFalse(jd.junk_quote(""))


class NodeAnchors(unittest.TestCase):
    S1 = SID + ":100:aaaa1111"
    S2 = SID + ":200:bbbb2222"

    def setUp(self):
        kern._node_anchor_last.clear()

    def maps(self):
        k = kern._seg_key
        return ({k(self.S1): "u-mint"}, {k(self.S1): "w-old", k(self.S2): "w-new"})

    def test_work_anchor_is_the_newest_trail_segment_even_for_open_nodes(self):
        # the regression the user hit: an open sub anchored its MINT forever — "where it was born,
        # never where it stands". The work anchor now keys on trail[-1].
        seg_trig, seg_work = self.maps()
        nd = {"id": SID + ":g1", "text": "port the parser", "quote": "port the old parser to the new event model",
              "promptUuid": "u-mint", "trail": [self.S1, self.S2]}
        prompt, work = kern._node_anchor_uuids(nd, seg_trig, seg_work)
        self.assertEqual(work, "w-new")
        self.assertEqual(prompt, "u-mint", "a genuine mint quote keeps its prompt anchor")

    def test_junk_mint_quote_ships_no_prompt_anchor(self):
        # romp_docs g242: quote "retry" → the title click landed on the user's own bare retry.
        # The junk guard ships None; the render's existing null path falls through to the work anchor.
        seg_trig, seg_work = self.maps()
        nd = {"id": SID + ":g2", "text": "first-session capture still shows the old flow",
              "quote": "retry", "promptUuid": "u-mint", "trail": [self.S1, self.S2]}
        prompt, work = kern._node_anchor_uuids(nd, seg_trig, seg_work)
        self.assertIsNone(prompt)
        self.assertEqual(work, "w-new")

    def test_cold_maps_fall_to_the_stored_summary_anchor(self):
        nd = {"id": SID + ":g3", "text": "wire the exporter", "quote": "wire the exporter",
              "promptUuid": "u-mint", "trail": [self.S2], "summaryAnchor": "w-cited"}
        prompt, work = kern._node_anchor_uuids(nd, {}, {})
        self.assertEqual(work, "w-cited")

    def test_warm_anchor_is_remembered_across_a_cold_beat(self):
        seg_trig, seg_work = self.maps()
        nd = {"id": SID + ":g4", "text": "index the archive", "quote": "index the archive",
              "promptUuid": "u-mint", "trail": [self.S1, self.S2]}
        kern._node_anchor_uuids(nd, seg_trig, seg_work)        # warm resolve remembered
        prompt, work = kern._node_anchor_uuids(nd, {}, {})     # cold beat
        self.assertEqual(work, "w-new")


class NodeLogRows(unittest.TestCase):
    def test_rows_compact_with_per_row_anchors_and_ev_time_fallback(self):
        seg = SID + ":300:cccc3333"
        seg_work = {kern._seg_key(seg): "w-block"}
        nd = {"log": [
            {"kind": "block", "src": "planner", "why": "needs your call on the format", "ev_t": 300, "seg": seg, "at": 301},
            {"kind": "unblock", "src": "user", "why": "answered by the user's reply to the card", "ev_t": 400, "at": 400},
        ]}
        rows = kern._node_log_rows(nd, seg_work)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["anchorUuid"], "w-block", "a row with a resolvable seg carries its exact anchor")
        self.assertIsNone(rows[1]["anchorUuid"], "a seg-less row ships null — the client falls to ev-time nav")
        self.assertEqual(rows[1]["evT"], 400)
        self.assertEqual(rows[0]["why"], "needs your call on the format")

    def test_capped_to_the_newest_eight(self):
        nd = {"log": [{"kind": "block", "src": "planner", "why": "w%d" % i, "ev_t": i, "at": i}
                      for i in range(12)]}
        rows = kern._node_log_rows(nd, {})
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[-1]["why"], "w11", "the newest rows survive the cap")

    def test_empty_log_ships_none(self):
        self.assertIsNone(kern._node_log_rows({}, {}))
        self.assertIsNone(kern._node_log_rows({"log": []}, {}))


class SubNodeDrop(unittest.TestCase):
    """The item-level "Drop" (nodeOverride op:clear): the same user-authority clear seam as a card
    Clear, scoped to ONE sub — checks it off as no-longer-needed without claiming completion."""

    def setUp(self):
        # the kernel's judge is one module object shared by every test module in the process, so a store saved at its import-bound GOALDIR outlives the module and reaches every later module's feed for the placeholder sid (T281/T282); a private root for the duration.
        self._td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd._rebind_state(Path(self._td.name))

    def tearDown(self):
        jd._rebind_state(self._state)
        self._td.cleanup()

    def test_the_store_this_module_saves_does_not_outlive_it(self):
        # The residue pin (T281/T282): a store saved through the shared judge lands under this test's root and
        # the run-wide root (what every later module's feed reads) is exactly as it was.
        shared = Path(self._state) / "goals" / (SID + ".json")
        before = (shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None)
        st = jd.load_goals(SID)
        st["nodes"][SID + ":t282"] = jd.GuardedNode({"id": SID + ":t282", "text": "a note", "parentId": None, "nodeComplete": False,
                                                        "blocked": False, "cleared": False, "trail": [], "t": 1, "mt": 1, "log": []})
        jd.save_goals(SID, st)
        self.assertTrue((Path(self._td.name) / "goals" / (SID + ".json")).exists(), "the store lives under this module's root")
        self.assertEqual((shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None), before,
                         "the run-wide goals directory is untouched by this module")


    TOP = SID + ":g10"
    SUB = SID + ":g11"

    def _store(self):
        return {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
                "nodes": {
                    self.TOP: {"id": self.TOP, "text": "docs overhaul", "parentId": None,
                               "nodeComplete": False, "blocked": False, "cleared": False,
                               "trail": [], "t": NOW - 900, "mt": NOW - 100},
                    self.SUB: {"id": self.SUB, "text": "re-record the first-run capture", "parentId": self.TOP,
                               "nodeComplete": False, "blocked": True, "blockWhy": "who records it?",
                               "cleared": False, "trail": [], "t": NOW - 800, "mt": NOW - 200}}}

    def test_drop_clears_only_the_sub_with_a_user_verdict(self):
        store = self._store()
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        kern._clear_all([self.SUB])

        st = jd.load_goals(SID)
        sub, top = st["nodes"][self.SUB], st["nodes"][self.TOP]
        self.assertTrue(sub["cleared"], "the dropped sub is checked off")
        self.assertFalse(top["cleared"], "the card itself stays")
        ev = [e for e in sub["log"] if e["kind"] == "clear"]
        self.assertEqual([e["src"] for e in ev], ["user"], "the drop is a USER clear verdict in the diary")
        self.assertFalse(sub["blocked"] and not sub["cleared"], "a dropped sub no longer reads as an open block")

    def test_the_judge_cannot_resurrect_a_dropped_sub(self):
        store = self._store()
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        kern._clear_all([self.SUB])
        st = jd.load_goals(SID)
        jd.rollup_status(st, False)                    # the next pass re-derives from the diary
        self.assertTrue(st["nodes"][self.SUB]["cleared"], "the clear holds through the rollup")


if __name__ == "__main__":
    unittest.main()
