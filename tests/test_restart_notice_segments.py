#!/usr/bin/env python3
"""Every kernel-restart notice romp injects has identical text, so the segment-id derivation (which hashes
the segment's trigger text) gave every restart-notice segment in a session the same hash, and the kernel's
timestamp-invariant lookup key (_seg_key = session id plus that hash) treated them all as ONE segment
(T318, 2026-09-10: a card whose recorded segments included one restart-notice segment resolved, through
that key, to whichever restart-notice segment the parse saw last, so its summary click landed on an
assistant turn hours after the work the summary described). A romp system notice (or an auto-nudge)
carries no user content and has no composer echo to drift against, so its segment is keyed by its anchor
atom's uuid, exactly as a text-less seam already is; so is every other segment a machine wrote the trigger of
(any romp injection: the retry message, an auto-nudge; the CLI's stop record). Two cut turns, two identical
notices: distinct ids, distinct keys, and the card's anchors stay inside its own segments. SYNTHETIC fixtures
only: a private synthetic sid, invented text, placeholder uuids."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the loads (a bare run has no conftest floor)
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_restart_segs", os.path.join(BIN, "romp-kernel"))
jd = km.jd
em = km.em

NOW = 1_788_300_000
T0 = NOW - 7200
SID = "f318e001-1111-4222-8333-000000000001"    # private synthetic sid — never the shared placeholder
NOTICE = ("<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted and cut this session's "
          "in-flight turn; the session has been resumed with its history intact. Re-read the tail of the "
          "conversation and pick the work back up where it stopped, without asking whether to continue.")
ASK = "Please make the outline pane optional in the settings, and talk me through what turning it off breaks."
WORK1 = "Read through the settings model: the outline pane is drawn by its own module, so an off switch is feasible without touching the feed."
WORK2 = "Picking the settings work back up after the cut: the outline module has one entry point, and hiding it drops no state."
WORK3 = "Resumed again: wrote the talk-through of the outline split and listed the three calls that are yours to make."


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


RETRY = "retry\n\n<!-- romp-injected -->"                                        # the kernel's fixed retry message
AUTO = "<!-- romp-injected --><!-- romp-auto -->[romp] Where does the outline work stand? <!-- romp-goal-id: g1 -->"
RECORDS = [
    uline(T0, ASK, "u1"),
    aline(T0 + 60, WORK1, "a1", "u1"),
    uline(T0 + 600, "[Request interrupted by user]", "c1", "a1", ps="sdk"),   # the first cut
    uline(T0 + 610, NOTICE, "n1", "c1", ps="sdk"),                            # the restart notice, injected by romp
    aline(T0 + 700, WORK2, "a2", "n1"),
    uline(T0 + 1800, "[Request interrupted by user]", "c2", "a2", ps="sdk"),  # the second cut
    uline(T0 + 1810, NOTICE, "n2", "c2", ps="sdk"),                           # the SAME notice text again
    aline(T0 + 1900, WORK3, "a3", "n2"),
    # the other machine-written triggers, each worded identically twice: romp's retry and an auto-nudge
    uline(T0 + 2500, RETRY, "r1", "a3", ps="sdk"),
    aline(T0 + 2510, "Retrying the last step as asked; the outline module still builds clean.", "a4", "r1"),
    uline(T0 + 2600, RETRY, "r2", "a4", ps="sdk"),
    aline(T0 + 2610, "Retried once more; the same result, so the talk-through stands as written.", "a5", "r2"),
    uline(T0 + 2700, AUTO, "an1", "a5", ps="sdk"),
    aline(T0 + 2710, "The outline work stands where the talk-through left it: three calls are still yours.", "a6", "an1"),
    uline(T0 + 2800, AUTO, "an2", "a6", ps="sdk"),
    aline(T0 + 2810, "Still standing where it was; nothing new to build until you decide the three calls.", "a7", "an2"),
]


class RestartNoticeSegments(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"
        cdir.mkdir()
        proj = td / "projects"
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
        names = td / "names"
        names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm)
        jd.gist_llm = lambda p: ""
        km._autonudge_cache.clear()
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        jd._rebind_state(td)          # STATE and every dir derived from it (names, captions, archive, goals…), the house way
        jd.PROJECTS = proj
        km.NAMES = names
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None,
                                           "color": None}}
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        s = jd.parsed_session(SID, [str(self.tpath)], NOW)
        st0 = {"rompUuid": SID, "nodes": {}, "placements": {}, "status": {}}
        self.segs = [sg for turn in s["turns"] for sg in jd._segs(turn, st0)]

    def tearDown(self):
        state, jd.PROJECTS, km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD, jd.gist_llm = self.saved
        jd._rebind_state(state)
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _seg_of(self, uuid):
        return next(sg for sg in self.segs if any(a.get("uuid") == uuid for a in sg["atoms"]))

    def test_two_identical_restart_notices_open_two_segments_with_distinct_ids_and_keys(self):
        n1, n2 = self._seg_of("n1"), self._seg_of("n2")
        self.assertNotEqual(n1["id"], n2["id"])
        self.assertNotEqual(jd._seg_key(n1["id"]), jd._seg_key(n2["id"]),
                            "identical notice text must not collapse two segments into one lookup key: %s / %s" % (n1["id"], n2["id"]))
        # the ask keeps a content-keyed id (drift-invariant across a composer echo), as every typed prompt does
        u1 = self._seg_of("u1")
        self.assertEqual(u1["id"].rsplit(":", 1)[1], __import__("hashlib").sha1(ASK.encode()).hexdigest()[:8])
        # a notice segment is keyed by its anchor atom's uuid, like a text-less seam…
        h = lambda u: __import__("hashlib").sha1(u.encode()).hexdigest()[:8]
        self.assertEqual(n1["id"].rsplit(":", 1)[1], h("n1"))
        # …and so is the CLI's own stop record, worded identically at every cut (each opens its own segment: the
        # assistant turn before it had ended, and the record ends the turn it opens)
        c1, c2 = self._seg_of("c1"), self._seg_of("c2")
        self.assertEqual(c1["id"].rsplit(":", 1)[1], h("c1")); self.assertEqual(c2["id"].rsplit(":", 1)[1], h("c2"))
        self.assertNotEqual(jd._seg_key(c1["id"]), jd._seg_key(c2["id"]), "two identical stop records must not share a key")
        # …and every other trigger romp wrote: the fixed retry message and an auto-nudge, each sent twice
        for a, b in (("r1", "r2"), ("an1", "an2")):
            sa, sb = self._seg_of(a), self._seg_of(b)
            self.assertEqual(sa["id"].rsplit(":", 1)[1], h(a), "%s keys on its own uuid" % a)
            self.assertNotEqual(jd._seg_key(sa["id"]), jd._seg_key(sb["id"]), "%s and %s must not share a key" % (a, b))

    def test_the_cards_anchors_stay_inside_its_own_segments(self):
        u1, n1, n2 = self._seg_of("u1"), self._seg_of("n1"), self._seg_of("n2")
        g = SID + ":g1"
        # the card's recorded segments: the ask and the FIRST notice segment (the planner placed the work after the
        # first cut under it); no stored citation, so the summary click resolves through the segment walk
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": g,
            "nodes": {g: {"id": g, "text": "Outline pane optional in settings", "parentId": None,
                          "nodeComplete": False, "blocked": True, "cleared": False,
                          "trail": [u1["id"], n1["id"]], "promptUuid": "u1", "quote": ASK, "t": T0, "mt": NOW,
                          "blockSummary": "Three calls are yours before anything is built.",
                          "summaryAnchor": None, "briefedMt": NOW - 10, "log": []}},
            "placements": {}, "status": {g: "blocked"}}))
        km._parse(str(self.tpath), SID, NOW)           # warm the kernel parse cache, as the pusher would
        card = next(a for a in km.build_feed(NOW)["asks"] if a.get("itemId") == g)
        root = next(n for n in card["tree"] if n.get("id") == g)   # the root node carries the two deep-link uuids (feed.ts reads rootNode)
        self.assertEqual(root.get("promptAnchorUuid"), "u1", "the title click lands on the ask itself")
        self.assertEqual(root.get("anchorUuid"), "a2",
                         "the work anchor is the newest recorded segment's reply: the work right after the FIRST cut, never the later notice's turn (%r)" % root.get("anchorUuid"))
        self.assertIn(card.get("summaryAnchorUuid"), ("a1", "a2"),
                      "the summary click stays inside the card's own segments, never the later notice's turn: %r" % card.get("summaryAnchorUuid"))
        self.assertNotEqual(card.get("summaryAnchorUuid"), "a3", "the second notice's segment is not this card's")
        # the hover glow lights exactly the recorded segments' rows
        us = km._segment_atom_uuids(SID, [u1["id"], n1["id"]], NOW)
        self.assertIn("a2", us); self.assertIn("u1", us); self.assertNotIn("a3", us)
        self.assertEqual(km._segment_of_uuid(SID, "a3", NOW)[0], n2["id"], "the later turn belongs to the later notice's segment")


if __name__ == "__main__":
    unittest.main()
