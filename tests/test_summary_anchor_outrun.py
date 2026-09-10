#!/usr/bin/env python3
"""The summary click follows the freshest evidence (the user 2026-08-28, T153): the stored
citation names what the summary was WRITTEN FROM, but a reply that reopens the card adds
stretches the summary has never seen (no re-completion yet, so no re-distill event) — and the
click then landed in the stale FIRST stretch of a visibly two-stretch card. When the follow-up
stamp or any subtree trail segment postdates the summary's coverage stamp, the cited tier yields
to the most-current-substantive walk; the citation resumes authority once a re-distill lands.
Display-only. SYNTHETIC fixtures only; private synthetic sids."""
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
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_anchout", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1_788_200_000
T0 = NOW - 3600
SID = "f11e0001-1111-4222-8333-000000000001"    # private synthetic sid — never the shared placeholder
LONG1 = "The dark background now covers the chat pane end to end, verified against both themes."
LONG2 = "The feed pane background is unified too now, and the reply thread confirms both panes match."


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class OutrunYield(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"
        cdir.mkdir()
        proj = td / "projects"
        import re as _re
        pdir = proj / _re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        recs = [uline(T0, "one consistent dark backdrop please", "u1", ps="typed"),
                aline(T0 + 60, LONG1, "a1", "u1"),
                uline(T0 + 900, "the feed pane still looks lighter", "u2", "a1", ps="typed"),
                aline(T0 + 960, LONG2, "a2", "u2")]
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"
        names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm)
        jd.gist_llm = lambda p: ""
        km._autonudge_cache.clear()
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None,
                                           "color": None}}
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        s = jd.parsed_session(SID, [str(self.tpath)], NOW)
        st0 = {"rompUuid": SID, "nodes": {}, "placements": {}, "status": {}}
        self.segs = [sg["id"] for turn in s["turns"] for sg in jd._segs(turn, st0)]

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm) = self.saved
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _store(self, trail, distilled_mt):
        g = SID + ":g1"
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g,
            "nodes": {g: {"id": g, "text": "One consistent dark backdrop", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False,
                          "trail": trail, "t": T0, "mt": NOW,
                          "summary": "The backdrop is unified.",
                          "summaryAnchor": "a1", "distilledMt": distilled_mt, "log": []}},
            "placements": {}, "status": {g: "working"}}))
        return g

    def _card(self, g):
        km._parse(str(self.tpath), SID, NOW)           # warm the kernel parse cache — build_feed's
        #                                                anchor tiers read _parse_cached, never parse
        return next(a for a in km.build_feed(NOW)["asks"] if a.get("itemId") == g)

    def test_a_reply_stretch_outruns_the_citation(self):
        # summary grounded in stretch 1 (distilledMt between the stretches); the reply stretch is
        # newer → the click follows the freshest substantive evidence, not the stale grounding
        g = self._store(trail=list(self.segs), distilled_mt=T0 + 120)
        self.assertEqual(self._card(g)["summaryAnchorUuid"], "a2",
                         "the cited tier yields when the span outruns the coverage stamp")

    def test_a_current_summary_keeps_its_grounded_citation(self):
        # the summary covers everything (stamp past the last stretch) → the citation stands:
        # what the summary was written from is the better target than any recency pick
        g = self._store(trail=list(self.segs), distilled_mt=NOW - 10)
        self.assertEqual(self._card(g)["summaryAnchorUuid"], "a1",
                         "the reader that wrote the summary names what it read")


if __name__ == "__main__":
    unittest.main()
