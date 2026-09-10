#!/usr/bin/env python3
"""build_timeline's per-bar romp-injection tagging (the user 2026-06-22; slimmed 2026-07-07 payload
audit): the broad `nudge` flag (any romp injection) shipped but the view read only `nudgeAuto` (the
AUTO-nudge swirl-dot), so `nudge` was dropped — bars now carry nudgeAuto alone, keyed on the trigger's
rompAuto marker. Event-based, never a time heuristic. Self-contained harness; synthetic transcript.
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
km = load_source("romp_kernel_nb", os.path.join(BIN, "romp-kernel"))
jd = km.jd
em = km.em

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 1800


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


class NudgeBar(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        # turn 1: a genuine human prompt. turn 2: a romp NUDGE — the injected marker authors its trigger
        # 'romp', so its bar must come back nudge=True (note: promptSource stays 'typed'; the marker wins).
        recs = [uline(T0, "ship the export feature", "u1"),
                aline(T0 + 20, "Shipped it.", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "Status on the goal above? <!-- romp-injected -->", "u2", "a1"),
                aline(T0 + 120, "Already deployed and done.", "a2", "u2", stop="end_turn")]
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._tmux_sessions)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.CAPDIR.mkdir(parents=True)
        jd.GOALDIR.mkdir(parents=True)
        session = em.parse_session(str(self.tpath), rompuuid=SID, candidate_files=[str(self.tpath)], now=NOW)
        self.segs = [s for turn in session["turns"] for s in em.segments(turn)]

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._tmux_sessions) = self.saved
        self.td.cleanup()

    def _bars(self):
        bars = [km._expand_bar(b) for b in km.build_timeline(NOW)["turns"][SID]]   # the wire bars, long-named (T278c)
        return {b["id"]: b for b in bars}

    def test_bars_carry_only_the_auto_nudge_flag(self):
        bars = self._bars()
        human, nudge = self.segs[0], self.segs[1]
        self.assertNotIn("nudge", bars[human["id"]], "the broad injection flag no longer ships (unread)")
        self.assertNotIn("nudge", bars[nudge["id"]])
        self.assertFalse(bars[human["id"]].get("nudgeAuto"), "a genuine human prompt is never an auto-nudge")

    def test_bars_flag_any_romp_authored_prompt_so_its_dot_wears_the_logo(self):
        # the user 2026-07-16, who reported an auto-retry ("retry" + <!-- romp-injected -->) rendering as a user prompt
        # instead of a romp logo thing — it authors 'romp' but carries no romp-AUTO marker, so nudgeAuto
        # alone left every non-nudge romp message looking like something the human typed. The view keys its
        # swirl-dot on this flag; nudgeAuto still distinguishes the auto-nudge's own caption.
        bars = self._bars()
        human, injected = self.segs[0], self.segs[1]
        self.assertTrue(bars[injected["id"]]["romp"], "a romp-injected prompt is romp-authored → the logo")
        self.assertFalse(bars[injected["id"]]["nudgeAuto"], "…but it is NOT an auto-nudge (no romp-auto marker)")
        self.assertFalse(bars[human["id"]]["romp"], "a genuine human prompt is never romp-authored")


if __name__ == "__main__":
    unittest.main()
