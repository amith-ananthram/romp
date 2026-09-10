#!/usr/bin/env python3
"""The kernel's per-rebuild git forks are cached on the events that change their answers (2026-08-16).

A busy federating kernel burned ~66% CPU with __fork at 68 samples/2s — not the transcript-cache
thrash (already fixed) but subprocess spawning, dominated by three call sites the rebuild loop pays
per session per pass:

- `_git_branch` keyed its cache on `getmtime(cwd/.git/HEAD)`, which RAISES for a worktree (its .git
  is a one-line 'gitdir:' pointer FILE) — so the cache was never read or written and every rebuild of
  every worktree-homed session forked 2-3 `git rev-parse`, forever. The convention here is one
  worktree per session. `_git_head_file` now resolves the real HEAD path once per cwd.
- `_repo_file_index` forked `git ls-files` on every rebuild whenever a transcript held one unresolved
  path-shaped token (the near-universal case): the per-build memo dies with the build, and unresolved
  tokens deliberately retry. Now cached per cwd on the (git index mtime, top-dir mtime) pair — the
  events every add/rm/commit/checkout and top-level drop touch.
- `_local_head`'s 2s TTL was shorter than the dashboard's 4s /tunnels poll, so every poll per open
  page re-forked two `git rev-parse`. The TTL now outlasts the poll.

Real git repos in temp dirs; fork counts observed by wrapping subprocess.run. SYNTHETIC only."""
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
from git_fixture import git, init_repo, forbid_background
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _git(*args, cwd):
    # Through the shared runner: `git commit` spawns `git maintenance run --auto`, which detaches and can still
    # be writing into .git while the TemporaryDirectory removes the repo (the CI flake "Directory not empty:
    # '.git'", tests/test_restart_classifier.py, 2026-09-10); the runner forbids that work on every call.
    git(cwd, *args, timeout=10)


def _mk_repo(td):
    repo = Path(td) / "repo"
    repo.mkdir()
    init_repo(repo, "-q", "-b", "main", timeout=10)
    _git("config", "user.email", "t@TESTHOST", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "a.txt").write_text("a\n")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-q", "-m", "seed", cwd=repo)
    return repo


class ForkCounter:
    """Wrap subprocess.run inside the kernel module and count git spawns."""
    def __init__(self):
        self.n = 0
        self._real = km.subprocess.run
    def __enter__(self):
        def counting(*a, **k):
            if a and isinstance(a[0], (list, tuple)) and a[0] and a[0][0] == "git":
                self.n += 1
            return self._real(*a, **k)
        km.subprocess.run = counting
        return self
    def __exit__(self, *exc):
        km.subprocess.run = self._real


class WorktreeBranchCache(unittest.TestCase):
    def test_a_worktree_branch_is_cached_and_refreshes_on_head_move(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _mk_repo(td)
            wt = Path(td) / "wt"
            _git("worktree", "add", "-q", "-b", "feature", str(wt), "HEAD", cwd=repo)
            forbid_background(wt)
            km._branch_cache.clear(); km._head_path_cache.clear()
            with ForkCounter() as fc:
                self.assertEqual(km._git_branch(str(wt)), "feature")
                first = fc.n
                self.assertEqual(km._git_branch(str(wt)), "feature")
                self.assertEqual(fc.n, first,
                                 "the second read is served from cache — the worktree burn (2026-08-16)")
            # the branch moves → the resolved HEAD file's mtime moves → the cache refreshes
            _git("checkout", "-q", "-b", "feature2", cwd=wt)
            self.assertEqual(km._git_branch(str(wt)), "feature2",
                             "a HEAD move is the refresh event, worktrees included")

    def test_a_plain_repo_keeps_its_existing_cache_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _mk_repo(td)
            km._branch_cache.clear(); km._head_path_cache.clear()
            with ForkCounter() as fc:
                self.assertEqual(km._git_branch(str(repo)), "main")
                first = fc.n
                self.assertEqual(km._git_branch(str(repo)), "main")
                self.assertEqual(fc.n, first)

    def test_head_file_resolves_both_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _mk_repo(td)
            wt = Path(td) / "wt2"
            _git("worktree", "add", "-q", "-b", "wtb", str(wt), "HEAD", cwd=repo)
            forbid_background(wt)
            km._head_path_cache.clear()
            self.assertTrue(km._git_head_file(str(repo)).endswith("/.git/HEAD"))
            hp = km._git_head_file(str(wt))
            self.assertTrue(hp.endswith("/HEAD") and os.path.isfile(hp),
                            "the worktree's HEAD lives in its private gitdir")
            self.assertEqual(km._git_head_file(str(td)), "", "no repo → no HEAD file")


class RepoIndexCache(unittest.TestCase):
    def test_the_file_index_is_cached_until_the_index_or_top_dir_moves(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _mk_repo(td)
            km._repo_index_cache.clear(); km._head_path_cache.clear(); km._tree_cache.clear()
            with ForkCounter() as fc:
                idx = km._repo_file_index(str(repo))
                self.assertIn("a.txt", idx)
                first = fc.n
                km._repo_file_index(str(repo))
                self.assertEqual(fc.n, first,
                                 "unchanged repo → no re-fork per rebuild (the ls-files burn, 2026-08-16)")
            (repo / "b.txt").write_text("b\n")       # a top-level drop moves the top dir's mtime
            idx2 = km._repo_file_index(str(repo))
            self.assertIn("b.txt", idx2, "the top-dir mtime move is a refresh event")
            _git("add", "b.txt", cwd=repo)
            _git("commit", "-q", "-m", "b", cwd=repo)
            (repo / "sub").mkdir()
            _git("mv", "a.txt", "sub/a.txt", cwd=repo)   # touches the git index
            idx3 = km._repo_file_index(str(repo))
            self.assertIn("sub/a.txt", idx3.get("a.txt", []), "an index move is a refresh event")


class HeadTtlOutlastsThePoll(unittest.TestCase):
    def test_the_ttl_covers_the_dashboard_poll_cadence(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('if now - _HEAD_CACHE["ts"] > 15:', src,
                      "the /tunnels poll is 4s; a shorter TTL re-forks git on every poll")


class FixtureReposForbidBackgroundGitWork(unittest.TestCase):
    def test_the_fixture_repos_forbid_background_git_work(self):
        # The kernel runs its OWN git against these repos (rev-parse, ls-files), which the runner's -c flags
        # cannot reach: the repo's local config is what keeps a detached maintenance child out of the
        # TemporaryDirectory teardown. The worktree shares the main repo's config.
        with tempfile.TemporaryDirectory() as td:
            repo = _mk_repo(td)
            wt = Path(td) / "wt"
            _git("worktree", "add", "-q", "-b", "feature", str(wt), "HEAD", cwd=repo)
            forbid_background(wt)
            for r in (repo, wt):
                self.assertEqual(git(r, "config", "--local", "--get", "maintenance.auto").stdout.strip(), "false")


if __name__ == "__main__":
    unittest.main()
