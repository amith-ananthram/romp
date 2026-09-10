#!/usr/bin/env python3
"""A session whose launch dir contains an underscore (or space, etc.) must still be DISCOVERED.

Regression (the user 2026-07-14, who reported the romp_demo session never got a card): Claude encodes a launch
dir into its ~/.claude/projects/ folder name by replacing EVERY non-alphanumeric char with '-', so
/…/romp_demo becomes -…-romp-demo. _proj_dir only rewrote '/' and '.', leaving the underscore intact,
so it scanned -…-romp_demo — a folder that never exists. discover() then found no transcript and the
session dropped out of the feed silently: its tab/status/chat worked (kernel-driven, by name) but the
judge never saw it, so no goal/caption/card was ever minted.

Synthetic only: placeholder UUID, hostname TESTHOST, hermetic temp STATE.
"""
import json
import os
import re
import shutil
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
NAME = "TESTHOST-romp_demo"


def _claude_encode(cdir):
    """Claude's real project-dir encoding: every non-alphanumeric char → '-'. Computed here WITHOUT
    reusing _proj_dir, so a regression (underscore left intact) files the transcript where the buggy
    _proj_dir can't look."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cdir))


class ProjDirUnderscoreTest(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache["fp"] = None                      # module-global → reset between tests
        jd._discover_cache["result"] = None
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        self.cdir = str(Path(self._td) / "GitRepos" / "romp_demo")   # the underscore that broke it

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def test_proj_dir_encodes_underscore_like_claude(self):
        got = jd._proj_dir(self.cdir).name
        self.assertEqual(got, _claude_encode(self.cdir))
        self.assertIn("romp-demo", got)          # underscore became a dash, matching Claude
        self.assertNotIn("romp_demo", got)       # ...no raw underscore survives to point at a ghost dir

    def test_underscore_dir_session_is_discovered(self):
        proj = jd.PROJECTS / _claude_encode(self.cdir)       # where Claude REALLY writes the transcript
        proj.mkdir(parents=True, exist_ok=True)
        (proj / (SID + ".jsonl")).write_text(json.dumps({"type": "user", "uuid": "u1"}) + "\n")
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, self.cdir))
        found = [t[0] for t in jd.discover(int(time.time()))]
        self.assertIn(SID, found, "a session in an underscore dir must be discovered (the no-card bug)")


if __name__ == "__main__":
    unittest.main()
