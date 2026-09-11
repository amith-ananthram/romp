#!/usr/bin/env python3
"""build_feed card TIME = the card's CURRENT-STATE time, not when the goal was minted: a completed card
shows when it COMPLETED, a blocked card when it BLOCKED (the most-recent such node's mt). Keying the time
badge to the mint `t` made a goal opened hours ago but finished moments ago read as "done hours ago" (the
user 2026-06-19). Self-contained harness (own temp fixture) so it doesn't share test_kernel.py's setUp.
Synthetic fleet only: invented text, placeholder UUIDs.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_ct", os.path.join(BIN, "romp-kernel"))
jd = km.jd        # the SAME judge module the kernel uses → our fixture overrides reach discover()/build_feed

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


class CardTime(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []                            # isolate from any real recorded host-sleep
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        recs = [uline(T0, "do the thing", "u1"),
                aline(T0 + 20, "Done.", "a1", "u1", stop="end_turn")]
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._live_map)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        km._live_map = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._live_map) = self.saved
        self.td.cleanup()

    def _store(self, nodes, status):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": len(nodes), "lastNode": None,
             "nodes": nodes, "placements": {}, "status": status}))

    def _card(self, nid):
        return next(a for a in km.build_feed(NOW)["asks"] if a["itemId"] == nid)

    def test_completed_card_time_is_completion_not_mint(self):
        # A goal OPENED 8h ago but FINISHED 10m ago must read as completed 10m ago (the completing node's
        # mt), not 8h ago (the mint t). `created` keeps the true mint time for the record.
        top, sub = SID + ":top", SID + ":sub"
        self._store(
            {top: {"id": top, "text": "a long job", "parentId": None, "nodeComplete": True,
                   "blocked": False, "cleared": False, "trail": [], "t": NOW - 8 * 3600, "mt": NOW - 600},
             sub: {"id": sub, "text": "the finishing step", "parentId": top, "nodeComplete": True,
                   "blocked": False, "cleared": False, "trail": [], "t": NOW - 7 * 3600, "mt": NOW - 600}},
            {top: "completed"})
        card = self._card(top)
        self.assertEqual(card["t"], NOW - 600, "card time = COMPLETION (mt), not the 8h-ago mint")
        self.assertEqual(card["trgb"], list(km.cm.age_rgb(NOW - card["t"])),
                         "the recency tint follows completion, not mint")

    def test_completed_card_time_ignores_a_no_op_rejudge_touch_of_the_umbrella(self):
        # An hours-old completed card must NOT jump to "moments ago" when a later no-op re-judge re-touches
        # the UMBRELLA node's mt without changing the work (the user 2026-07-08: it read 3m ago while the
        # done children + settle time were hours old). The time stays the settle time / the done DESCENDANTS'
        # completion mt — both stable — never the umbrella's re-touched mt.
        top, sub = SID + ":top", SID + ":sub"
        self._store(
            {top: {"id": top, "text": "a shipped feature", "parentId": None, "nodeComplete": True,
                   "blocked": False, "cleared": False, "trail": [], "t": NOW - 8 * 3600,
                   "mt": NOW - 120, "settledAt": NOW - 7 * 3600},         # umbrella mt bumped to 2m ago (no-op re-judge)
             sub: {"id": sub, "text": "the finishing step", "parentId": top, "nodeComplete": True,
                   "blocked": False, "cleared": False, "trail": [], "t": NOW - 7 * 3600, "mt": NOW - 7 * 3600}},
            {top: "completed"})
        card = self._card(top)
        self.assertEqual(card["t"], NOW - 7 * 3600,
                         "completed card time = the stable settle time, NOT the umbrella's re-touched mt")

    def test_childless_completed_goal_without_settledat_falls_back_to_its_own_mt(self):
        # legacy/simple path: a completed goal with no children and no settledAt has no stable descendant or
        # settle signal, so its own completion mt is the only evidence (behavior unchanged).
        top = SID + ":top"
        self._store(
            {top: {"id": top, "text": "a one-shot fix", "parentId": None, "nodeComplete": True,
                   "blocked": False, "cleared": False, "trail": [], "t": NOW - 3 * 3600, "mt": NOW - 400}},
            {top: "completed"})
        card = self._card(top)
        self.assertEqual(card["t"], NOW - 400, "no children + no settledAt → the node's own completion mt")

    def test_blocked_card_time_is_when_it_blocked_not_mint(self):
        # Same for a BLOCKED card: it shows when the still-open block was set (the blocked node's mt).
        top = SID + ":top"
        self._store(
            {top: {"id": top, "text": "needs a decision", "parentId": None, "nodeComplete": False,
                   "blocked": True, "blockWhy": "which store?", "cleared": False, "trail": [],
                   "t": NOW - 5 * 3600, "mt": NOW - 300}},
            {top: "blocked"})
        card = self._card(top)
        self.assertEqual(card["t"], NOW - 300, "blocked card time = when it BLOCKED (mt), not the 5h-ago mint")

    def test_working_card_time_is_last_activity_not_mint(self):
        # A still-working card shows its LAST ACTIVITY (the newest mt anywhere in its subtree, _fsubmax),
        # not the mint time — a reply advances the goal's mt, so the card must freshen with it instead of
        # reading "15m ago" right after you replied (the user 2026-07-01, kernel 8180d8f). `created` keeps
        # the true mint time for the record.
        top = SID + ":top"
        self._store(
            {top: {"id": top, "text": "in progress", "parentId": None, "nodeComplete": False,
                   "blocked": False, "cleared": False, "trail": [], "t": NOW - 1800, "mt": NOW - 60}},
            {top: "working"})
        card = self._card(top)
        self.assertEqual(card["t"], NOW - 60, "a working card freshens to its last activity (mt)")


if __name__ == "__main__":
    unittest.main()
