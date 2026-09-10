#!/usr/bin/env python3
"""build_timeline carries TWO captions per segment (the user 2026-06-19): the WORK caption (`summary`, the
bar) and the MESSAGE caption (`msgCaption`, the dot) read from the captioner's '<segid>#p' record. The view
shows msgCaption on the dot (falling back to the raw prompt until it lands) and the work summary on the bar.
Self-contained build_timeline harness; synthetic transcript only.
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
km = load_source("romp_kernel_mc", os.path.join(BIN, "romp-kernel"))
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


class MsgCaption(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        recs = [uline(T0, "make the empty space below the cards smaller", "u1"),
                aline(T0 + 20, "Trimmed it.", "a1", "u1", stop="end_turn")]
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
        self.seg = em.segments(session["turns"][0])[0]

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._tmux_sessions) = self.saved
        self.td.cleanup()

    def _cap(self, uid, grain, caption):
        with open(jd.CAPDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"id": uid, "grain": grain, "t": int(self.seg["t"]), "caption": caption}) + "\n")

    def _bar(self):
        bars = [km._expand_bar(b) for b in km.build_timeline(NOW)["turns"][SID]]   # the wire bars, long-named (T278c)
        return next(b for b in bars if b["id"] == self.seg["id"])

    def test_bar_carries_separate_message_and_work_captions(self):
        self._cap(self.seg["id"], "segment", "Trimmed the empty space")                    # WORK caption (bar)
        self._cap(self.seg["id"] + "#p", "prompt", "the empty space below the cards")       # MESSAGE caption (dot)
        b = self._bar()
        self.assertEqual(b["msgCaption"], "the empty space below the cards", "the dot gets the MESSAGE caption")
        self.assertEqual(b["summary"], "Trimmed the empty space", "the bar keeps the WORK caption")

    def test_msgCaption_is_empty_until_its_caption_lands(self):
        self._cap(self.seg["id"], "segment", "Trimmed the empty space")                    # only the work caption so far
        b = self._bar()
        self.assertEqual(b["msgCaption"], "", "no message caption yet → empty, so the view falls back to the raw prompt")
        self.assertEqual(b["summary"], "Trimmed the empty space")

    def _drift(self, seg_id, secs=11):
        p = seg_id.split(":")
        return "%s:%d:%s" % (p[0], int(p[1]) - secs, p[2])

    def test_drifted_work_caption_still_reaches_the_bar(self):
        # The captioner records under the JUDGE's parse id, whose middle t sits at send/idle time (the
        # states overlay leads the turn); the kernel's render id carries the transcript atom's process
        # time. The exact join missed, so the bar hover fell back to 'request: <prompt>' instead of the
        # work gist (the user 2026-07-21 via romp_docs). The bar must resolve the drifted id.
        self._cap(self._drift(self.seg["id"]), "segment", "Trimmed the empty space")
        self._cap(self._drift(self.seg["id"] + "#p"), "prompt", "the empty space below the cards")
        b = self._bar()
        self.assertEqual(b["summary"], "Trimmed the empty space",
                         "a timestamp-drifted WORK caption still lands on the bar")
        self.assertEqual(b["msgCaption"], "the empty space below the cards",
                         "the drifted MESSAGE caption keeps landing on the dot")

    def test_drifted_turn_row_is_not_a_work_caption(self):
        # A turn-grain row shares the seg-id family (same trigger-text hash); grain filtering keeps it
        # off the bar, whose caption is the SEGMENT's own.
        self._cap(self._drift(self.seg["id"]), "turn", "Turn-level rollup line")
        self.assertEqual(self._bar()["summary"], "",
                         "no segment-grain caption yet → empty, the view falls back to the prompt")


if __name__ == "__main__":
    unittest.main()
