#!/usr/bin/env python3
"""The warn modal's "Try again" (the user 2026-08-13): a card whose summary line GAVE UP (the ""
sentinel after DISTILL_FAIL_CAP consecutive failures) can be re-armed by hand — the redistill op
journals the flip (append_override) so a concurrent triage pass's last-writer save can't silently
erase the click, and the replay flips only the give-up sentinel: "" → None (owed). A line that has
since succeeded (non-empty) or is already owed (None) is untouched, so replays past a success never
clobber it. SYNTHETIC fixtures only (placeholder UUIDs, invented text)."""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_redistill", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
NID = SID + ":g1"


def _seed_store(summary="", block_summary=None, fails=2):
    store = jd.load_goals(SID)
    nd = {"text": "ship the api", "parentId": None, "mt": 1781100000,
          "summary": summary, "distillFails": fails,
          "warns": [{"kind": "summary-failed", "t": 1781100000,
                     "msg": "romp tried to generate this summary several times and each attempt failed",
                     "detail": "synthetic detail"}]}
    if block_summary is not None:
        nd["blockSummary"] = block_summary
    store["nodes"][NID] = jd.GuardedNode(nd)
    jd.save_goals(SID, store)
    return store


class RedistillOverrideReplay(unittest.TestCase):
    def tearDown(self):
        for d in (jd.GOALDIR, jd._overrides_dir()):
            for f in d.glob("*"):
                f.unlink()

    def test_replay_flips_the_giveup_sentinel_to_owed(self):
        _seed_store(summary="", block_summary="", fails=2)
        jd.append_override(SID, NID, "redistill", 1781100100)
        nd = jd.load_goals(SID).get("nodes", {}).get(NID)
        self.assertIsNone(nd.get("summary"), '"" (gave up) → None (owed): the next pass re-runs the distiller')
        self.assertIsNone(nd.get("blockSummary"), "the blocked line re-arms the same way")
        self.assertEqual(nd.get("distillFails"), 2,
                         "the fail counter SURVIVES replay (2026-08-18): the journal replays on every "
                         "load, so a per-load reset made DISTILL_FAIL_CAP unreachable — fails ping-ponged "
                         "0↔1 forever, one doomed model call per pass from a single click; a real click "
                         "only exists post-give-up anyway, where the give-up write already zeroed it")

    def test_a_later_giveup_supersedes_the_click(self):
        # the stand-down rule at the replay: the retry the click asked for ran and GAVE UP again — the
        # re-stamped warn (newer than the click) is the judges ruling on a newer world, so the historical
        # click must stop re-arming the card on every load (the eternal-click review finding)
        _seed_store(summary="", fails=0)
        jd.append_override(SID, NID, "redistill", 1781100100)
        nd = jd.load_goals(SID)["nodes"][NID]            # the click answers THIS give-up: applies
        self.assertIsNone(nd.get("summary"))
        st = jd.load_goals(SID)                          # the retry re-gives-up: sentinel + NEWER warn
        st["nodes"][NID]["summary"] = ""
        st["nodes"][NID]["warns"] = [{"kind": "summary-failed", "t": 1781100200,
                                      "msg": "synthetic msg", "detail": "synthetic detail"}]
        jd.save_goals(SID, st)
        nd = jd.load_goals(SID)["nodes"][NID]            # journal replays again on this load
        self.assertEqual(nd.get("summary"), "",
                         "a give-up stamped AFTER the click supersedes it — the click stands down")

    def test_replay_never_clobbers_a_line_that_succeeded_since(self):
        _seed_store(summary="", fails=0)
        jd.append_override(SID, NID, "redistill", 1781100100)
        store = jd.load_goals(SID)                       # replay applies: "" → None
        store["nodes"][NID]["summary"] = "All four endpoints shipped and tested."
        jd.save_goals(SID, store)
        nd = jd.load_goals(SID).get("nodes", {}).get(NID)   # journal replays again on this load
        self.assertEqual(nd.get("summary"), "All four endpoints shipped and tested.",
                         "the flip touches ONLY the give-up sentinel — a later success stands")

    def test_replay_is_idempotent_and_skips_missing_nodes(self):
        _seed_store(summary="")
        jd.append_override(SID, NID, "redistill", 1781100100)
        jd.append_override(SID, NID, "redistill", 1781100200)     # a second click — same shape
        jd.append_override(SID, SID + ":gone", "redistill", 1781100300)   # a cleared/compacted card
        nd = jd.load_goals(SID).get("nodes", {}).get(NID)
        self.assertIsNone(nd.get("summary"))
        self.assertIsNone(jd.load_goals(SID).get("nodes", {}).get(SID + ":gone"))


class RedistillReplayRecoveryExtensions(unittest.TestCase):
    """Try again reaches the give-ups the "" flip can't (2026-08-18): a give-up that KEPT an older real
    summary (a re-completion never blanks prior text) re-arms by clearing its event stamp — gated on the
    live "*-failed" warn, so replays past a success stay no-ops; and the stall line joins the family.
    The replay deliberately leaves nd["autoRearmed"] ALONE: the journal replays on every load forever,
    so a historical click erasing the era mark would defeat the one-auto-retry bound for good — the mark
    clears only on that line's landed summary or a discrete recovery event."""

    def tearDown(self):
        for d in (jd.GOALDIR, jd._overrides_dir()):
            for f in d.glob("*"):
                f.unlink()

    def _seed(self, nd):
        base = {"text": "ship the api", "parentId": None, "mt": 1781100000}
        base.update(nd)
        store = jd.load_goals(SID)
        store["nodes"][NID] = jd.GuardedNode(base)
        jd.save_goals(SID, store)

    def test_replay_rearms_a_kept_older_summary_by_stamp(self):
        self._seed({"summary": "Old takeaway.", "distilledMt": 1781100000,
                    "warns": [{"kind": "summary-failed", "t": 1781100000,
                               "msg": "synthetic msg", "detail": "synthetic detail"}]})
        jd.append_override(SID, NID, "redistill", 1781100100)
        nd = jd.load_goals(SID)["nodes"][NID]
        self.assertEqual(nd.get("summary"), "Old takeaway.", "prior text is never clobbered")
        self.assertIsNone(nd.get("distilledMt"), "stamp cleared → the done gate re-enters, prior intact")
        # the retry then succeeds: new text, new stamp, warn cleared → the journaled click goes no-op
        st = jd.load_goals(SID)
        st["nodes"][NID]["summary"] = "New takeaway."
        st["nodes"][NID]["distilledMt"] = 1781100500
        st["nodes"][NID]["warns"] = []
        jd.save_goals(SID, st)
        nd = jd.load_goals(SID)["nodes"][NID]            # journal replays again on this load
        self.assertEqual(nd.get("distilledMt"), 1781100500,
                         "no live warn → the stamp-clear never replays past a success")

    def test_replay_flips_the_stall_sentinel_and_leaves_the_era_mark(self):
        self._seed({"stallSummary": "", "stallFails": 2, "autoRearmed": True,
                    "warns": [{"kind": "stall-failed", "t": 1781100000,
                               "msg": "synthetic msg", "detail": "synthetic detail"}]})
        jd.append_override(SID, NID, "redistill", 1781100100)
        nd = jd.load_goals(SID)["nodes"][NID]
        self.assertIsNone(nd.get("stallSummary"), "the stall line re-arms like summary/blockSummary")
        self.assertEqual(nd.get("stallFails"), 2, "the fail counter survives replay (see the flip test)")
        self.assertTrue(nd.get("autoRearmed"),
                        "the era mark SURVIVES replay: the journal replays on every load forever, so a "
                        "historical click erasing it would defeat the one-auto-retry bound for good — "
                        "the mark clears only on a landed summary or a discrete recovery event")


class RedistillOpWiring(unittest.TestCase):
    """Source pins on the kernel handler — the ws plumbing is exercised end-to-end by the push
    responsiveness suite; here we pin the journal-first order and the loud ACK."""

    def test_handler_journals_first_then_saves_and_acks(self):
        import inspect
        km = load_source("romp_kernel_redistill", os.path.join(BIN, "romp-kernel"))
        src = inspect.getsource(km.Handler._dispatch_ws)
        # split on the CONDITION, not the bare op string — the journal call carries "redistill" too
        seg = src.split('msg.get("type") == "redistill"')[1].split("elif msg and")[0]
        self.assertLess(seg.index("jd.append_override(_dsid"), seg.index("jd.load_goals"),
                        "journal FIRST — the replay is what survives a concurrent pass's save")
        self.assertIn('jd.save_goals', seg)
        self.assertIn('"redistillResult"', seg, "the feed always hears back (fail loudly)")
        self.assertIn('_mark_views_dirty()', seg)


if __name__ == "__main__":
    unittest.main()
