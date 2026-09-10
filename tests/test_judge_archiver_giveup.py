#!/usr/bin/env python3
"""Archiver give-up + distinct call/parse errors (plan P0a, the user 2026-07-06).

The 2026-07-06 account rate-limit window produced 1163 archiver failures in ~90 minutes: every
~3s index pass retried the same two sessions (the count-based trigger stayed open), and
archive_llm logged CALL failures (no output at all) as "parse", hiding the cause. Now: failures
bump a per-turn-set counter on the archive record; after ARCH_FAIL_CAP failures on the SAME turn
set the archiver goes quiet until the session gains a turn (the count changes — event-based
re-arm, no timer); and call vs parse failures are logged distinctly. Synthetic fixtures only."""
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
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_archgiveup", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781100000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class ArchiverGiveUp(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        (proj / munged).mkdir(parents=True)
        self.tpath = proj / munged / (SID + ".jsonl")
        self.records = [uline(T0, "fix the flicker", "u1"),
                        aline(T0 + 30, "Fixed the flicker.", "a1", "u1")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in self.records) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
                      jd.caption_llm, jd.archive_llm, jd.gist_llm)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.caption_llm = lambda text: "stub caption"
        jd.gist_llm = lambda text, judge="gist": "stub caption"
        self.calls = []
        jd.archive_llm = lambda log: self.calls.append(1) or None      # FAIL every call

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.PCACHE,
         jd.caption_llm, jd.archive_llm, jd.gist_llm) = self.saved
        self.td.cleanup()

    def _giveups(self):
        try:
            rows = [json.loads(l) for l in jd.ERRORS.read_text().splitlines()]
        except OSError:
            return []
        return [r for r in rows if r.get("err") == "give-up" and r.get("judge") == "archiver"]

    def test_fail_cap_quiets_until_a_new_turn_rearms(self):
        now = T0 + 120
        g0 = len(self._giveups())
        for i in range(1, jd.ARCH_FAIL_CAP + 1):
            jd.run_index(now=now)
            self.assertEqual(len(self.calls), i, "one archive attempt per pass while failing")
            arch = json.loads((jd.ARCHDIR / (SID + ".json")).read_text())
            self.assertEqual(arch["fails"], i)
            self.assertEqual(arch["failTurns"], 1)
        # cap reached → further passes make NO attempt (the rate-limit-window retry storm)
        jd.run_index(now=now)
        jd.run_index(now=now)
        self.assertEqual(len(self.calls), jd.ARCH_FAIL_CAP, "given up on this turn set")
        rows = self._giveups()[g0:]
        self.assertEqual(len(rows), 1, "the give-up transition logs exactly ONE row, not one per quiet pass")
        self.assertIn("until the session gains a turn", rows[0]["note"], "the note names the re-arm event")
        # the session gains a TURN → the count changes → re-armed (event-based, no timer)
        self.records += [uline(T0 + 200, "now add a toggle", "u2", "a1"),
                         aline(T0 + 230, "Added the toggle.", "a2", "u2")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in self.records) + "\n")
        jd.archive_llm = lambda log: {"headline": "stub headline", "abstract": "stub abstract"}
        r = jd.run_index(now=T0 + 300)
        self.assertEqual(r["archives"], 1, "re-armed by the new turn caption and succeeded")
        arch = json.loads((jd.ARCHDIR / (SID + ".json")).read_text())
        self.assertEqual(arch["turns"], 2)
        self.assertNotIn("fails", arch, "a fresh successful record drops the fail counters")

    def test_failure_keeps_serving_the_old_archive(self):
        # a prior GOOD archive must survive fail-counter updates (the TOC keeps its headline)
        jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (jd.ARCHDIR / (SID + ".json")).write_text(json.dumps(
            {"headline": "old headline", "abstract": "old abstract", "turns": 99, "t": T0}))
        jd.run_index(now=T0 + 120)
        arch = json.loads((jd.ARCHDIR / (SID + ".json")).read_text())
        self.assertEqual(arch["headline"], "old headline", "stale-but-good beats gone")
        self.assertEqual(arch["fails"], 1)


class CallVsParseLogging(unittest.TestCase):
    def test_archive_llm_logs_only_genuine_parse_rejects(self):
        # Since 2026-07-09 _judge_run owns ALL call-level logging (subprocess errors, error envelopes,
        # the rate gate), so an empty reply logs NOTHING here — the archiver's own "parse" row is only
        # for text the model actually wrote, with the tail as evidence.
        with tempfile.TemporaryDirectory() as td:
            jd._rebind_state(Path(td))
            jd._judge_ctx.fsid = SID
            saved = jd._judge_run
            try:
                jd._judge_run = lambda *a, **k: None                   # call failed → logged by _judge_run itself
                self.assertIsNone(jd.archive_llm("- did a thing"))
                jd._judge_run = lambda *a, **k: "no labeled lines"     # output the parser rejects → "parse"
                self.assertIsNone(jd.archive_llm("- did a thing"))
            finally:
                jd._judge_run = saved
            errs = ([json.loads(l) for l in jd.ERRORS.read_text().splitlines()]
                    if jd.ERRORS.exists() else [])
            self.assertEqual([e["err"] for e in errs], ["parse"],
                             "no duplicate 'call' row from the caller; one parse row for rejected text")
            self.assertIn("no labeled lines", errs[0]["note"], "the reply tail is the evidence")


if __name__ == "__main__":
    unittest.main()
