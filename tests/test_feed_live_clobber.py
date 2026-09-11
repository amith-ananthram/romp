"""build_feed's per-session `live` (the backend-alive bit every card and placeholder wears) must
survive the goal loop: the origin badge's own bool ("is the SENDER's linked goal still open?") is
a different fact and must never shadow it. Before the fix the badge block reused the name `live`,
so once one card carried a delegation-origin badge, every LATER card of the SAME session — and the
placeholders built after the loop — wore the badge's bool instead of session liveness: a live
session's cards dressed .dead and took the revive path; a dead session's offered Continue. Origin
badges persist for the card's life, so one absorbed badge poisoned the session's whole card tail
on every build. SYNTHETIC fixtures only: placeholder UUIDs, the notes-api demo world."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_liveclobber", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"     # the session under test
PEER = "22222222-3333-4444-5555-666666666666"    # the sender whose linked goal drives the badge
NOW = 1781200000


class _FeedWorld(unittest.TestCase):
    """One session with TWO tops: gA carries a delegation-origin badge (planted by PEER), gB is a
    plain local top built AFTER it — the card whose `live` bit the badge block used to clobber."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = (jd.STATE, jd.GOALDIR, jd.GOALARCHDIR, jd.NAMES)
        jd.STATE = td
        jd.GOALDIR = td / "goals"
        jd.GOALARCHDIR = td / "goals-archive"
        jd.NAMES = td / "names"
        jd.GOALDIR.mkdir(parents=True)
        jd.NAMES.mkdir(parents=True)
        self.sessions = [{"sid": SID, "name": "web", "path": "/nonexistent/%s.jsonl" % SID,
                          "anchor": 0, "mtime": 0}]
        self.patches = [
            mock.patch.object(km, "_alive_sessions", lambda now, live: list(self.sessions)),
            mock.patch.object(km, "_warm_fleet_bg", lambda now: None),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        jd.STATE, jd.GOALDIR, jd.GOALARCHDIR, jd.NAMES = self.saved
        self.td.cleanup()

    def _write_goals(self, sender_goal_open):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "nodes": {
                "gA": {"parentId": None, "t": NOW - 900, "text": "planted work",
                       "nodeComplete": True,
                       "origin": {"peer": PEER, "goalId": "gS"}},
                "gB": {"parentId": None, "t": NOW - 600, "text": "local work"},
            },
            "lastNode": "gB"}))
        (jd.GOALDIR / (PEER + ".json")).write_text(json.dumps({
            "nodes": {"gS": {"parentId": None, "t": NOW - 1000, "text": "handoff",
                             "nodeComplete": not sender_goal_open}}}))


class LiveSessionAfterAbsorbedBadge(_FeedWorld):
    """ALIVE session, ABSORBED badge (sender's goal closed → origin.live False): every card must
    still read live=True — the badge's bool is the badge's alone."""

    def test_later_cards_of_a_live_session_keep_live_true(self):
        self._write_goals(sender_goal_open=False)
        feed = km.build_feed(NOW, {SID: {"state": "ready"}})   # the live map lists the session → alive
        cards = {a["itemId"]: a for a in feed["asks"]}
        self.assertIn("gA", cards)
        self.assertIn("gB", cards)
        self.assertFalse(cards["gA"]["origin"]["live"], "the absorbed badge itself reads closed")
        self.assertTrue(cards["gB"]["live"],
                        "card B built after the origin badge must not inherit the badge's bool")
        self.assertTrue(cards["gA"]["live"],
                        "card A belongs to a LIVE session — its live bit is session liveness")

    def test_the_provisional_placeholder_after_an_absorbed_badge_keeps_live_true(self):
        # complete gB too — nodeComplete AND the judge's persisted rollup status, which is what
        # had_working actually reads — so no working card fronts the placeholder chain
        self._write_goals(sender_goal_open=False)
        st = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        st["nodes"]["gB"]["nodeComplete"] = True
        st["nodes"]["gB"]["blocked"] = False
        st["status"] = {"gA": "completed", "gB": "completed"}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(st))
        # a real settled transcript whose latest human segment the planner has NOT placed — no
        # placements written — which is exactly the segment _provisional_card surfaces
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        recs = [
            {"type": "user", "timestamp": iso(NOW - 500), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed", "message": {"role": "user", "content": "do the thing"}},
            {"type": "assistant", "timestamp": iso(NOW - 480), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}],
                         "stop_reason": "end_turn"}},
        ]
        tpath = Path(self.td.name) / (SID + ".jsonl")
        tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.sessions[0]["path"] = str(tpath)
        km._parse(str(tpath), SID, NOW)   # warm the cache: build_feed reads the parse cache-only,
        #                                   and the placeholder chain needs ps
        feed = km.build_feed(NOW, {SID: {"state": "ready"}})
        ph = next((a for a in feed["asks"] if a["itemId"] == "provisional:" + SID), None)
        self.assertIsNotNone(ph, "the unplaced settled prompt surfaces a provisional card — "
                                 "pre-fix the poisoned bit suppresses it entirely "
                                 "(_provisional_card gates on `if not live`)")
        self.assertTrue(ph["live"],
                        "the placeholder's live bit is the session's, not the last badge's")


class DeadSessionAfterLiveBadge(_FeedWorld):
    """The REVERSE clobber: DEAD session (absent from the live map), LIVE badge (sender's goal still open →
    origin.live True). Later cards must still read live=False — a dead session's cards must not
    offer Continue off the badge's bool."""

    def test_later_cards_of_a_dead_session_keep_live_false(self):
        self._write_goals(sender_goal_open=True)
        feed = km.build_feed(NOW, {})    # build_feed reads the card's live bit from live_map.get(sid): empty → dead
        cards = {a["itemId"]: a for a in feed["asks"]}
        self.assertIn("gA", cards)
        self.assertIn("gB", cards)
        self.assertTrue(cards["gA"]["origin"]["live"], "the badge itself reads open")
        self.assertFalse(cards["gB"]["live"],
                         "card B of a DEAD session must not inherit the live badge's bool")
        self.assertFalse(cards["gA"]["live"],
                         "card A's live bit is session liveness, badge or no badge")


if __name__ == "__main__":
    unittest.main()
