#!/usr/bin/env python3
"""Tests for the distiller notes seam (kernel/judge.py): the user's standing style memory
($ROMP_DISTILLER_NOTES / ~/.config/romp/distiller-notes.md) rides every PROSE judge call's
system prompt — and only those. All fixtures are SYNTHETIC (invented text, TESTHOST)."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — the module resolves its state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
jd = load_source("romp_judge_notes", os.path.join(BIN, "romp-judge"))


class WithUserNotes(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.notes = Path(self.td) / "distiller-notes.md"
        os.environ["ROMP_DISTILLER_NOTES"] = str(self.notes)

    def tearDown(self):
        os.environ.pop("ROMP_DISTILLER_NOTES", None)

    def test_prose_judges_get_notes_and_placement_judges_never_do(self):
        self.notes.write_text("Never cite ticket numbers; say what the change does.")
        for j in sorted(jd._USER_NOTES_JUDGES):
            out = jd._with_user_notes("SYS RULES", j)
            self.assertTrue(out.startswith("SYS RULES"), j)
            self.assertIn("<user-notes>", out, j)
            self.assertIn("Never cite ticket numbers", out, j)
        # the verdict judges and the courier (agent-directed copy) stay bare — style notes about what
        # the user wants to READ must not leak into placement judgments or injected messages
        for j in ("planner", "opener", "placer", "grouper", "closer", "unblocker", "courier", None):
            self.assertEqual("SYS RULES", jd._with_user_notes("SYS RULES", j), str(j))

    def test_absent_empty_or_whitespace_file_means_the_bare_prompt(self):
        self.assertEqual("SYS", jd._with_user_notes("SYS", "distiller"))   # no file at all
        self.notes.write_text("   \n\t\n")                                 # whitespace only
        self.assertEqual("SYS", jd._with_user_notes("SYS", "distiller"))

    def test_notes_are_capped_so_a_runaway_file_cannot_bloat_prompts(self):
        self.notes.write_text("x" * (jd._USER_NOTES_CAP + 5000))
        out = jd._with_user_notes("SYS", "distiller")
        body = out.split("<user-notes>\n", 1)[1].rsplit("\n</user-notes>", 1)[0]
        self.assertEqual(len(body), jd._USER_NOTES_CAP)

    def test_transport_carries_notes_inside_the_distillers_system_prompt(self):
        self.notes.write_text("Speak plainly about TESTHOST changes.")
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = [str(c) for c in cmd]
            return SimpleNamespace(returncode=0, stderr="",
                                   stdout=json.dumps({"result": "BACKGROUND: b\nTAKEAWAY: t",
                                                      "usage": {"input_tokens": 1, "output_tokens": 1},
                                                      "total_cost_usd": 0}))

        orig = jd.subprocess.run
        jd.subprocess.run = fake_run
        try:
            out = jd.distill_llm("a synthetic goal", "some synthetic work")
        finally:
            jd.subprocess.run = orig
        self.assertIn("TAKEAWAY", out)
        joined = "\n".join(seen["cmd"])
        self.assertIn("Speak plainly about TESTHOST changes.", joined)
        self.assertIn("the note wins", joined)


if __name__ == "__main__":
    unittest.main()
