#!/usr/bin/env python3
"""The session's REAL working location surfaces, and detached HEAD stops posing as a branch
(the user 2026-08-13).

Two audit findings, both pinned here:
  * The branch shown above the chat / on the tab tooltip is a property of the session's REGISTERED
    directory — by design (2026-06-24) — but the repo convention does real work on per-session
    worktrees beside the registered clone, so every surface read 'main' forever and the actual
    working location showed nowhere. The kernel now derives the WORKTREE from the newest write-tool
    file_path in the transcript (the edit event is the proof of where work happens; no declared
    source exists) and ships it only when that tree differs from the registered dir's own.
  * The transcript's gitBranch stamp passes the literal string 'HEAD' through on a detached
    checkout — "HEAD isn't a branch". The folder-derived path already normalized detached to '';
    _norm_branch applies the same rule to the transcript path at the one merge point.

Synthetic transcripts only (invented text, placeholder UUIDs); the git fixtures are throwaway tmp
repos with fixture identities.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_worktree", os.path.join(BIN, "romp-kernel"))


def _git(*args, cwd=None):
    return subprocess.run(["git", "-c", "user.email=t@TESTHOST", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, text=True, check=True)


class NormBranch(unittest.TestCase):
    def test_detached_head_is_not_a_branch(self):
        self.assertEqual(km._norm_branch("HEAD"), "")
        self.assertEqual(km._norm_branch(" HEAD "), "")

    def test_real_branches_pass_through(self):
        self.assertEqual(km._norm_branch("main"), "main")
        self.assertEqual(km._norm_branch("feature/x"), "feature/x")
        self.assertEqual(km._norm_branch(None), "")
        self.assertEqual(km._norm_branch(""), "")


class LastEditPath(unittest.TestCase):
    def _transcript(self, rows):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.jsonl")
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def _arow(self, blocks):
        return {"type": "assistant", "uuid": "11111111-2222-3333-4444-555555555555",
                "cwd": "/home/u/proj", "version": "9.9.9",
                "message": {"role": "assistant", "content": blocks}}

    def test_the_newest_write_tool_path_wins_and_reads_ignore(self):
        p = self._transcript([
            self._arow([{"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/a/one.py"}}]),
            self._arow([{"type": "tool_use", "name": "Read", "input": {"file_path": "/elsewhere/x.py"}}]),
            self._arow([{"type": "tool_use", "name": "Write", "input": {"file_path": "/w/b/two.py"}}]),
        ])
        meta = km._session_meta(p)
        self.assertEqual(meta["lastEditPath"], "/w/b/two.py",
                         "the newest EDIT event names the tree; a Read is not work landing anywhere")

    def test_no_edits_no_path(self):
        p = self._transcript([self._arow([{"type": "text", "text": "just words"}])])
        self.assertEqual(km._session_meta(p)["lastEditPath"], "")


class TreeOf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.main = os.path.join(cls.root, "repo")
        os.makedirs(cls.main)
        _git("init", "-q", "-b", "main", cwd=cls.main)
        open(os.path.join(cls.main, "f.txt"), "w").write("x\n")
        _git("add", "f.txt", cwd=cls.main)
        _git("commit", "-q", "-m", "seed", cwd=cls.main)
        cls.wt = os.path.join(cls.root, "repo-feature")
        _git("worktree", "add", "-q", "-b", "feature", cls.wt, "HEAD", cwd=cls.main)
        os.makedirs(os.path.join(cls.wt, "sub"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_a_worktree_path_resolves_to_its_own_tree_and_branch(self):
        top, br = km._tree_of(os.path.join(self.wt, "sub"))
        self.assertEqual(os.path.realpath(top), os.path.realpath(self.wt))
        self.assertEqual(br, "feature")

    def test_the_registered_clone_resolves_to_itself(self):
        top, br = km._tree_of(self.main)
        self.assertEqual(os.path.realpath(top), os.path.realpath(self.main))
        self.assertEqual(br, "main")

    def test_a_non_repo_dir_is_no_tree(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(km._tree_of(d), ("", ""))
        self.assertEqual(km._tree_of(""), ("", ""))


class PayloadWiring(unittest.TestCase):
    """build_session's wiring, pinned by source (the build_* test pattern)."""

    def test_the_worktree_ships_only_when_it_differs_from_the_registered_tree(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn('_wt_top, _wt_br = _tree_of(os.path.dirname(meta.get("lastEditPath") or "") or "")', src)
        self.assertIn('if _wt_top and os.path.realpath(_wt_top) != os.path.realpath(_reg_top or "/nonexistent")', src)
        self.assertIn('"workTree": sysinfo["workTree"]', src, "top-level like gitBranch — never windowed off the wire")

    def test_the_transcript_branch_is_normalized_at_the_merge_point(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn('_norm_branch(meta.get("gitBranch")) or _git_branch(scwd)', src)


if __name__ == "__main__":
    unittest.main()
