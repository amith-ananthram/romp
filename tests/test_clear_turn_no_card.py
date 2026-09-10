#!/usr/bin/env python3
"""A /clear turn never mints a card, even through the SDK's orphan salvage (the user 2026-07-27).

An SDK /clear streams the CLI's "(no content)" placeholder as command feedback; the transcript keeps
it only as a system/local_command row, so the backend persisted it as an orphanReply marker and
synthesize_orphans resurrected it as a real assistant atom. That atom made _seg_command_worked read
the bare /clear turn as the MODEL having worked, and the planner minted a card literally titled from
the /clear invocation. The placeholder is now dropped at both ends: the backend never persists it
(test_sdk_orphan_reply) and the parse never synthesizes an atom from markers already written (here).
Synthetic fixtures only."""
import json
import os
import shutil
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
jd = load_source("romp_judge_clearturn", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000
NOW = T0 + 600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def clear_head(t):
    """The records Claude Code writes at the head of a post-/clear transcript: the standalone
    caveat, the <command-name> wrapper, and the system/local_command feedback row."""
    return [
        {"type": "user", "timestamp": iso(t), "uuid": "cv1", "parentUuid": None,
         "message": {"role": "user",
                     "content": "<local-command-caveat>Caveat: local commands</local-command-caveat>"}},
        {"type": "user", "timestamp": iso(t), "uuid": "cm1", "parentUuid": "cv1",
         "message": {"role": "user",
                     "content": "<command-name>/clear</command-name>\n<command-message>clear</command-message>"
                                "\n<command-args></command-args>"}},
        {"type": "system", "subtype": "local_command", "uuid": "sy1", "parentUuid": "cm1",
         "timestamp": iso(t), "level": "info", "isMeta": False,
         "content": "<local-command-stdout></local-command-stdout>"},
    ]


class ClearTurnNoCard(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        self.proj = Path(self._td) / "proj"
        self.proj.mkdir()

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def _parse(self, recs, markers):
        tpath = self.proj / (SID + ".jsonl")
        tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        (jd.STATESDIR / (SID + ".jsonl")).write_text(
            "\n".join(json.dumps(m) for m in markers) + ("\n" if markers else ""))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        return jd.parsed_session(SID, [str(tpath)], NOW)

    def test_no_content_marker_never_becomes_an_atom(self):
        session = self._parse(
            clear_head(T0) + [uline(T0 + 60, "tidy the docs page", "u2", "sy1"),
                              aline(T0 + 90, "Done, tidied.", "a2", "u2")],
            [{"t": T0, "orphanReply": {"uuid": "sy1", "text": "(no content)"}}])
        atoms = [a for turn in session["turns"] for a in turn["atoms"]]
        self.assertFalse(any(a.get("orphaned") for a in atoms),
                         "the placeholder marker synthesizes no atom")

    def test_clear_turn_is_a_bare_command_and_plans_no_unit(self):
        session = self._parse(
            clear_head(T0) + [uline(T0 + 60, "tidy the docs page", "u2", "sy1"),
                              aline(T0 + 90, "Done, tidied.", "a2", "u2")],
            [{"t": T0, "orphanReply": {"uuid": "sy1", "text": "(no content)"}}])
        cmd_segs = [seg for turn in session["turns"] for seg in jd.em.segments(turn)
                    if jd._seg_command(seg)]
        self.assertEqual(len(cmd_segs), 1, "the /clear invocation parses as a command turn")
        self.assertFalse(jd._seg_command_worked(cmd_segs[0]),
                         "a salvaged placeholder is not the model working")
        units = jd.plan_units(session)
        self.assertEqual(len(units), 1, "only the real ask yields a unit")
        self.assertIn("tidy the docs", units[0][3])

    def test_a_real_orphan_reply_still_synthesizes(self):
        # the guard drops ONLY the placeholder — a genuine salvaged reply keeps flowing to the judges
        session = self._parse(
            [uline(T0, "summarize the release notes", "u1")],
            [{"t": T0 + 30, "orphanReply": {"uuid": "or1", "text": "Here is the summary you asked for."}}])
        atoms = [a for turn in session["turns"] for a in turn["atoms"]]
        self.assertTrue(any(a.get("orphaned") for a in atoms), "real salvage still emits")


if __name__ == "__main__":
    unittest.main()
