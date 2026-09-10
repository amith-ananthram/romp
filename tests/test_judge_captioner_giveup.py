#!/usr/bin/env python3
"""Captioner give-up: an empty capture on a CLOSED unit stops retrying forever (2026-08-18).

The audit that found it: 2,920 captioner calls in two hours produced 62 caption records — a
unit whose caption came back empty was never recorded, so captioned_ids re-selected the same
closed units on every pass, fleet-wide, ~24 calls a minute, and fed the API-capacity window
that broke a card's brief. The archiver got exactly this ladder on 2026-07-06 (ARCH_FAIL_CAP);
the captioner now has its per-unit sibling: CAPTION_FAIL_CAP consecutive empty captures write
an EMPTY tombstone caption record — captioned_ids dedups it, every reader filters on caption
truthiness so it never renders — and a success clears the strike count. Paused/rate-gated
skips never strike (the _judge_run paused contract, threaded out of the worker by
_caption_call's (caption, paused) return). Synthetic fixtures only."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_capgiveup", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-666666666666"
T0 = 1781200000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class CaptionerGiveUp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        (proj / munged).mkdir(parents=True)
        self.tpath = proj / munged / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, "fix the flicker", "u1"),
            aline(T0 + 30, "Fixed the flicker.", "a1", "u1")]) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.caption_llm, jd.archive_llm, jd.gist_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        self.calls = []
        jd.caption_llm = lambda text: self.calls.append(1) or ""       # EMPTY every call
        jd.gist_llm = lambda text, judge="gist": self.calls.append(1) or ""
        jd.archive_llm = lambda log: {"headline": "h", "abstract": "a"}

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.caption_llm, jd.archive_llm, jd.gist_llm) = self.saved
        self.td.cleanup()

    def _giveups(self):
        try:
            rows = [json.loads(l) for l in jd.ERRORS.read_text().splitlines()]
        except OSError:
            return []
        return [r for r in rows if r.get("err") == "give-up" and r.get("judge") == "captioner"]

    def _tombstones(self):
        try:
            rows = [json.loads(l) for l in (jd.CAPDIR / (SID + ".jsonl")).read_text().splitlines()]
        except OSError:
            return []
        return [r for r in rows if not r.get("caption") and not r.get("live")]

    def test_empty_captures_tombstone_at_the_cap_and_stop_costing_calls(self):
        now = T0 + 120
        g0 = len(self._giveups())
        for i in range(jd.CAPTION_FAIL_CAP):
            before = len(self.calls)
            jd.run_index(now=now)
            self.assertGreater(len(self.calls), before, "still selected while under the cap")
        self.assertTrue(self._tombstones(), "the cap writes empty tombstone records")
        self.assertEqual(len(self._giveups()) - g0, 1, "one give-up row at the transition")
        settled = len(self.calls)
        jd.run_index(now=now)
        jd.run_index(now=now)
        self.assertEqual(len(self.calls), settled, "tombstoned units never re-caption")
        # tombstones are invisible to every reader: no turn caption, no gist
        self.assertEqual(jd.session_turn_captions(SID), [])

    def test_a_success_clears_the_strikes(self):
        now = T0 + 120
        jd.run_index(now=now)                                          # strike 1
        fails = json.loads((jd.CAPDIR / (SID + ".fails.json")).read_text())
        self.assertTrue(all(v == 1 for v in fails.values()))
        jd.caption_llm = lambda text: "did the thing"                  # now the calls succeed
        jd.gist_llm = lambda text, judge="gist": "the thing"
        jd.run_index(now=now)
        self.assertFalse((jd.CAPDIR / (SID + ".fails.json")).exists(),
                         "success clears the ledger (only CONSECUTIVE empties tombstone)")
        self.assertFalse(self._tombstones())
        self.assertTrue(jd.session_turn_captions(SID))

    def test_paused_skips_never_strike(self):
        # a rate-gate/pause skip rides the paused flag out of the worker; it is not a verdict
        saved = jd._caption_call
        try:
            jd._caption_call = lambda task: ("", True)
            for _ in range(jd.CAPTION_FAIL_CAP + 1):
                jd.run_index(now=T0 + 120)
            self.assertFalse((jd.CAPDIR / (SID + ".fails.json")).exists(), "no strikes recorded")
            self.assertFalse(self._tombstones(), "no tombstones from paused passes")
        finally:
            jd._caption_call = saved


if __name__ == "__main__":
    unittest.main()
