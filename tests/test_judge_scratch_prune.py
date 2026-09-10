#!/usr/bin/env python3
"""Judge scratch transcript isolation + pruning (the user 2026-07-20). Every one-shot `claude -p`
judge call writes a transcript under its CWD's project dir; with cwd=/tmp those piled into the
SHARED -private-tmp project dir (~4,600/day, 51k files) mixed with anything else ever run from
/tmp — a growing scan/fseventsd tax that romp couldn't prune without touching data it doesn't own.
Now judges run from their own JUDGE_SCRATCH cwd, so prune_judge_scratch can sweep that project dir
wholesale by age, and ONLY that dir. Synthetic fixtures only."""
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_scratch", os.path.join(BIN, "romp-judge"))


class JudgeCallsRunFromScratchCwd(unittest.TestCase):
    def test_judge_run_uses_the_romp_owned_scratch_cwd(self):
        td = tempfile.mkdtemp()
        jd._rebind_state(Path(td))
        seen = {}

        def fake_run(cmd, **kw):
            seen["cwd"] = kw.get("cwd")
            return mock.Mock(stdout='{"result": "ok"}', stderr="")

        with mock.patch.object(jd.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(jd, "JUDGE_SCRATCH", os.path.join(td, "scratch")):
            out = jd._judge_run("m", "sys", "user")
        self.assertEqual(out, "ok")
        self.assertEqual(seen["cwd"], os.path.join(td, "scratch"),
                         "judge transcripts must land in romp's OWN project dir, never -private-tmp")
        self.assertTrue(os.path.isdir(seen["cwd"]), "the scratch cwd is created before the call")


class PruneJudgeScratch(unittest.TestCase):
    def _setup(self):
        td = tempfile.mkdtemp()
        scratch = os.path.join(td, "judge-scratch")
        projects = Path(td) / "projects"
        proj = projects / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(scratch)).lstrip("/").replace("/", "-")
        # build the project dir exactly as _proj_dir will derive it, so the test never drifts
        # from the real encoding
        with mock.patch.object(jd, "PROJECTS", projects), \
             mock.patch.object(jd, "JUDGE_SCRATCH", scratch):
            proj = jd._proj_dir(scratch)
        proj.mkdir(parents=True)
        return td, scratch, projects, proj

    def test_prunes_old_keeps_fresh_and_non_jsonl(self):
        td, scratch, projects, proj = self._setup()
        now = time.time()
        old = proj / "11111111-2222-3333-4444-555555555555.jsonl"
        fresh = proj / "66666666-7777-8888-9999-000000000000.jsonl"
        other = proj / "notes.txt"
        for p in (old, fresh, other):
            p.write_text("{}")
        os.utime(old, (now - 25 * 3600, now - 25 * 3600))
        os.utime(other, (now - 25 * 3600, now - 25 * 3600))
        with mock.patch.object(jd, "PROJECTS", projects), \
             mock.patch.object(jd, "JUDGE_SCRATCH", scratch):
            n = jd.prune_judge_scratch(now=now)
        self.assertEqual(n, 1)
        self.assertFalse(old.exists(), "a day-old one-shot judge transcript is junk")
        self.assertTrue(fresh.exists(), "recent transcripts stay (a call could still be in flight)")
        self.assertTrue(other.exists(), "only .jsonl transcripts are romp's to delete")

    def test_never_touches_other_project_dirs(self):
        td, scratch, projects, proj = self._setup()
        now = time.time()
        foreign = projects / "-private-tmp"
        foreign.mkdir(parents=True)
        theirs = foreign / "11111111-2222-3333-4444-aaaaaaaaaaaa.jsonl"
        theirs.write_text("{}")
        os.utime(theirs, (now - 90 * 24 * 3600, now - 90 * 24 * 3600))
        with mock.patch.object(jd, "PROJECTS", projects), \
             mock.patch.object(jd, "JUDGE_SCRATCH", scratch):
            jd.prune_judge_scratch(now=now)
        self.assertTrue(theirs.exists(),
                        "the shared -private-tmp dir holds data romp does NOT own — never sweep it")

    def test_missing_project_dir_is_quietly_zero(self):
        td, scratch, projects, proj = self._setup()
        proj.rmdir()
        with mock.patch.object(jd, "PROJECTS", projects), \
             mock.patch.object(jd, "JUDGE_SCRATCH", scratch):
            self.assertEqual(jd.prune_judge_scratch(), 0)


if __name__ == "__main__":
    unittest.main()
