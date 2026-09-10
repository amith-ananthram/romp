#!/usr/bin/env python3
"""_github_repo_of and the frames that carry it — the repository a session's PR references link to
(the user 2026-09-06: `#123` / `PR #123` in a session's chat, cards and notes should link to the PR
page of the repository the session works in). The kernel names that repository per session from the
authoritative source, the session tree's origin remote, through the SAME parser the file viewer's
GitHub link uses (_GITHUB_REMOTE). None is a VERDICT, not an error: no repo, no origin, or an origin
elsewhere names no GitHub repository, and the clients then link nothing rather than guess.

Throwaway temp repos with fabricated origins (example-org/notes-api — the demo world); nothing here
reaches a network (no fetch, no ls-remote — `remote get-url` reads config). The registry-keyed check
uses a PRIVATE synthetic sid (the goal-store fixture rule, applied to the names registry too).

Also here: _tree_of's evidence-keyed verdict (a directory that becomes a repo after its first build is
found without a kernel restart; a `.git` git rejects — or walks past — is asked about once, then costs
stats), the pointer memos of a tree that dies or changes shape under a live session (_pointer_mtime,
and _vouch_tree forgetting a reshaped tree's memos — or unvouched ones, after its record's own bound; a
pointer re-made within one mtime tick; a symlinked cwd re-pointed at another repository), what a
memoized call costs in stats, the branch
of a BARE repository, the inbound postal card's `peerHost` (the chat resolves a sender's repo by host
and name; a row from before the log stamped a host carries none), the session frame's `selfHost`,
_git_config_file's hand-built layouts (a relative gitdir, a gitdir with no commondir), and
_session_cwd — the one derivation of a session's directory the chat frame and the feed's session
rows share, so a never-registered session links the same repo on both.
"""
import builtins
import inspect
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source
from git_fixture import git, init_repo, forbid_background

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load — the kernel resolves its state root at import time, and only pytest
# runs conftest's floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_github_repo", os.path.join(BIN, "romp-kernel"))

HTTPS_ORIGIN = "https://github.com/example-org/notes-api.git"
SSH_ORIGIN = "git@github.com:example-org/notes-api.git"
ELSEWHERE_ORIGIN = "https://gitlab.example.com/example-org/notes-api.git"
REPO = "example-org/notes-api"
# a private synthetic sid: never the shared 11111111-2222-… placeholder (another module's journaled
# state can land on that one), never a real session's
PRIVATE_SID = "7a7a7a7a-1a1a-4b4b-8c8c-9d9d9d9d9d9d"


IDENT = ("t@TESTHOST", "t")   # the fixtures' synthetic author, on every invocation and in every repo's config


def _git(*args, cwd):
    # through the suite's shared runner (tests/git_fixture.py, T299), which forbids git's BACKGROUND work:
    # `git commit`, fetch and merge spawn `git maintenance run --auto`, which on recent git detaches from its
    # parent and can still be writing into .git while the temp dir is removed (the CI flake "Directory not
    # empty: '.git'", tests/test_restart_classifier.py, 2026-09-10). Bytes output and check=True, as before.
    git(cwd, *args, ident=IDENT, text=False)


def _repo(root, name, origin=None):
    """A one-commit repo on branch main under `root`, with `origin` set when given."""
    d = os.path.join(root, name)
    os.makedirs(d)
    init_repo(d, "-q", "-b", "main", ident=IDENT)
    with open(os.path.join(d, "f.txt"), "w") as f:
        f.write("x\n")
    _git("add", "f.txt", cwd=d)
    _git("commit", "-q", "-m", "seed", cwd=d)
    if origin:
        _git("remote", "add", "origin", origin, cwd=d)
    return d


def _bump_mtime(path):
    """Move a file's mtime forward by a whole second, so a rewrite within the same clock tick still
    reads as a change — the memo keys on mtime, and the test must not depend on filesystem resolution."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 2))


class _Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # the kernel's own git runs in the process environment (as in production): pin it to read no
        # global or system config, so a developer's url.insteadOf rewrite cannot bend a fixture origin
        cls._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        cls.root = tempfile.mkdtemp()
        cls.https = _repo(cls.root, "https-repo", HTTPS_ORIGIN)
        cls.sub = os.path.join(cls.https, "src", "deep")
        os.makedirs(cls.sub)
        cls.wt = os.path.join(cls.root, "https-repo-feature")
        _git("worktree", "add", "-q", "-b", "feature", cls.wt, "HEAD", cwd=cls.https)
        forbid_background(cls.wt)
        cls.ssh = _repo(cls.root, "ssh-repo", SSH_ORIGIN)
        cls.elsewhere = _repo(cls.root, "elsewhere-repo", ELSEWHERE_ORIGIN)
        cls.noorigin = _repo(cls.root, "no-origin-repo")
        cls.plain = os.path.join(cls.root, "not-a-repo")
        os.makedirs(cls.plain)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class GitHubRepoOf(_Fixtures):
    def test_an_https_origin_names_owner_and_repo_without_the_git_suffix(self):
        self.assertEqual(km._github_repo_of(self.https), REPO)

    def test_the_ssh_origin_spelling_names_the_same_repo(self):
        self.assertEqual(km._github_repo_of(self.ssh), REPO)

    def test_a_subdirectory_of_the_tree_resolves_to_the_trees_repo(self):
        self.assertEqual(km._github_repo_of(self.sub), REPO)

    def test_a_worktree_reads_the_shared_config_through_its_commondir(self):
        self.assertEqual(km._github_repo_of(self.wt), REPO)
        cfg = km._git_config_file(os.path.realpath(self.wt))
        self.assertEqual(os.path.realpath(cfg), os.path.realpath(os.path.join(self.https, ".git", "config")),
                         "the worktree's remotes live in the main tree's .git/config")

    def test_an_origin_elsewhere_is_no_github_repo(self):
        self.assertIsNone(km._github_repo_of(self.elsewhere))

    def test_no_origin_remote_is_no_github_repo(self):
        self.assertIsNone(km._github_repo_of(self.noorigin))

    def test_no_repository_is_no_github_repo(self):
        self.assertIsNone(km._github_repo_of(self.plain))
        self.assertIsNone(km._github_repo_of(""))
        self.assertIsNone(km._github_repo_of(None))

    def test_a_tilde_path_is_expanded_like_every_other_cwd(self):
        home = os.path.expanduser("~")
        if not self.https.startswith(home + os.sep):
            self.skipTest("the fixture root is not under $HOME")
        self.assertEqual(km._github_repo_of("~" + self.https[len(home):]), REPO)

    def test_the_parser_is_the_file_viewers(self):
        src = inspect.getsource(km._github_repo_of)
        self.assertIn("_GITHUB_REMOTE.match(remote)", src, "one spelling of what counts as GitHub")
        self.assertIn('_git_out(["remote", "get-url", "origin"], top)', src, "the same authoritative read")

    def test_the_fixture_repos_forbid_background_git_work(self):
        # the kernel's own git runs against these through its subprocess, not _git: the keys live in each
        # repo's local config (a worktree reads its clone's), so that git obeys them too
        for repo in (self.https, self.wt, self.ssh, self.elsewhere, self.noorigin):
            self.assertEqual(git(repo, "config", "--local", "--get", "maintenance.auto").stdout.strip(), "false", repo)


class Memo(unittest.TestCase):
    """The answer is memoized per tree on the config file's mtime: a second build forks no git, and a
    rewritten remote (the file's mtime moving) is re-read."""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.root = tempfile.mkdtemp()
        self.repo = _repo(self.root, "memo-repo", HTTPS_ORIGIN)
        self.cfg = os.path.join(self.repo, ".git", "config")
        self._real_git_out = km._git_out
        self.calls = []

        def counting(args, cwd, *a, **kw):
            self.calls.append(list(args))
            return self._real_git_out(args, cwd, *a, **kw)
        km._git_out = counting

    def tearDown(self):
        km._git_out = self._real_git_out
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _remote_reads(self):
        return [c for c in self.calls if c[:2] == ["remote", "get-url"]]

    def test_a_second_call_reads_the_memo_not_git(self):
        self.assertEqual(km._github_repo_of(self.repo), REPO)
        self.assertEqual(len(self._remote_reads()), 1)
        self.assertEqual(km._github_repo_of(self.repo), REPO)
        self.assertEqual(len(self._remote_reads()), 1, "an unchanged config is not re-read")

    def test_a_rewritten_remote_is_re_read(self):
        self.assertEqual(km._github_repo_of(self.repo), REPO)
        _git("remote", "set-url", "origin", "https://github.com/other-org/other-repo.git", cwd=self.repo)
        _bump_mtime(self.cfg)
        self.assertEqual(km._github_repo_of(self.repo), "other-org/other-repo")
        self.assertEqual(len(self._remote_reads()), 2)

    def test_losing_the_origin_is_a_real_change_to_none(self):
        self.assertEqual(km._github_repo_of(self.repo), REPO)
        _git("remote", "remove", "origin", cwd=self.repo)
        _bump_mtime(self.cfg)
        self.assertIsNone(km._github_repo_of(self.repo), "a null verdict replaces the memoized repo")


class LateRepo(unittest.TestCase):
    """A directory that becomes a repo AFTER its first build (`git init`, then a GitHub origin) is found on
    the next call, with no kernel restart: _tree_of trusts a cached "not a repo" verdict only while no
    `.git` exists on the chain git's discovery walks. Before the re-validation the verdict was cached
    forever, so gitBranch (re-derived per build) showed while githubRepo stayed null — silent degradation
    (review find, 2026-09-06). A directory that stays outside any repo is NOT re-asked: one fork, then
    stats only."""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.root = tempfile.mkdtemp()
        if km._dotgit_on_chain(self.root):
            self.skipTest("the temp root sits inside a repository; these need a directory outside every tree")
        self.proj = os.path.join(self.root, "proj")
        self.sub = os.path.join(self.proj, "src")
        os.makedirs(self.sub)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _init_with_origin(self, d):
        """What `git init` + a first commit + `gh repo create --push` leave behind (an unborn branch — init
        with no commit — reads as no branch, like a detached HEAD; the repo is still found)."""
        init_repo(d, "-q", "-b", "main", ident=IDENT)
        with open(os.path.join(d, "f.txt"), "w") as f:
            f.write("x\n")
        _git("add", "f.txt", cwd=d)
        _git("commit", "-q", "-m", "seed", cwd=d)
        _git("remote", "add", "origin", HTTPS_ORIGIN, cwd=d)

    def test_a_directory_that_becomes_a_repo_is_found_without_a_restart(self):
        self.assertIsNone(km._github_repo_of(self.proj))
        self.assertEqual(km._tree_of(self.proj), ("", ""))
        self.assertEqual(km._tree_cache.get(self.proj), ("", None, False), "the non-repo verdict is cached, with its evidence: no .git on the chain")
        self._init_with_origin(self.proj)
        self.assertEqual(km._github_repo_of(self.proj), REPO, "the next call sees the new tree")
        top, br = km._tree_of(self.proj)
        self.assertEqual(os.path.realpath(top), os.path.realpath(self.proj))
        self.assertEqual(br, "main")

    def test_a_repo_appearing_at_an_ancestor_is_found_from_a_subdirectory(self):
        # the edit-derived tree (lastEditPath's directory) and a session whose cwd is a subdirectory
        # of the project both resolve upward, as git does
        self.assertIsNone(km._github_repo_of(self.sub))
        self._init_with_origin(self.proj)
        self.assertEqual(km._github_repo_of(self.sub), REPO)
        self.assertEqual(os.path.realpath(km._tree_of(self.sub)[0]), os.path.realpath(self.proj))

    def test_a_directory_still_outside_any_repo_is_not_re_asked(self):
        real_run = km.subprocess.run
        asks = []

        def counting(*a, **k):
            argv = a[0] if a else k.get("args")
            if isinstance(argv, (list, tuple)) and "--show-toplevel" in argv:
                asks.append(list(argv))
            return real_run(*a, **k)
        km.subprocess.run = counting
        try:
            self.assertEqual(km._tree_of(self.proj), ("", ""))
            self.assertEqual(len(asks), 1, "the first call asks git")
            self.assertEqual(km._tree_of(self.proj), ("", ""))
            self.assertEqual(km._tree_of(self.sub), ("", ""))
            self.assertEqual(km._tree_of(self.proj), ("", ""))
            self.assertEqual(len(asks), 2, "one ask per directory; a still-non-repo verdict costs stats, not forks")
        finally:
            km.subprocess.run = real_run

    def test_the_chain_check_is_a_stat_walk_up_to_the_root(self):
        self.assertIsNone(km._dotgit_on_chain(self.sub))
        dotgit = os.path.join(self.proj, ".git")
        os.mkdir(dotgit)                                   # any .git on the chain, a directory here
        ev = km._dotgit_on_chain(self.sub)
        self.assertEqual((ev[0], ev[2]), (dotgit, False), "the evidence: the entry's path, and that it is a directory")
        st = os.stat(dotgit)
        self.assertEqual(ev[1], st.st_mtime)
        self.assertEqual((ev[3], ev[4]), (st.st_ino, st.st_dev), "…and its identity, off the same stat: a verdict key may need it")
        self.assertEqual(km._dotgit_on_chain(self.proj), ev)
        self.assertIsNone(km._dotgit_on_chain(self.root), "an ancestor of the .git is not on its chain")


def _count_git(store):
    """Patch km.subprocess.run to tally git queries by their distinguishing word; returns the restore."""
    real_run = km.subprocess.run

    def counting(*a, **k):
        argv = a[0] if a else k.get("args")
        if isinstance(argv, (list, tuple)):
            for key in ("--show-toplevel", "--abbrev-ref", "get-url"):
                if key in argv:
                    store[key] = store.get(key, 0) + 1
        return real_run(*a, **k)
    km.subprocess.run = counting
    return lambda: setattr(km.subprocess, "run", real_run)


class RejectedDotGit(unittest.TestCase):
    """A `.git` on the chain that git REJECTS — an empty directory; a worktree pointer whose main clone is
    gone (this repo's own worktree convention produces it whenever the clone is removed or re-cloned) —
    is asked about ONCE. The stat walk is a superset of git's discovery, so re-validating a not-a-repo
    verdict by the entry's mere presence forked `git rev-parse` on every _tree_of call for the life of
    the kernel, three per chat build and one per session per feed build (review find, 2026-09-06). The
    verdict is filed on the evidence itself — the entry's path and mtime, and for a pointer whether its
    gitdir exists — so only a change to that evidence re-asks git."""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.root = tempfile.mkdtemp()
        if km._dotgit_on_chain(self.root):
            self.skipTest("the temp root sits inside a repository; these need a directory outside every tree")
        self.forks = {}
        self.addCleanup(_count_git(self.forks))

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _build(self, d, n=3):
        """What the builds cost: n rounds of the two scwd calls build_session makes plus the feed's one."""
        out = []
        for _ in range(n):
            out.append((km._tree_of(d), km._github_repo_of(d), km._github_repo_of(d)))
        return out

    def test_an_empty_dot_git_directory_is_asked_once_then_costs_stats_until_git_init_fills_it(self):
        proj = os.path.join(self.root, "proj")
        sub = os.path.join(proj, "src")
        os.makedirs(sub)
        dotgit = os.path.join(proj, ".git")
        os.mkdir(dotgit)
        os.utime(dotgit, (1_600_000_000, 1_600_000_000))   # an old mtime: the init below is a definite change
        self.assertEqual(self._build(sub), [(("", ""), None, None)] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1}, "git rejects the empty .git once; no build after it forks")
        # `git init` populates the directory (its mtime moves): the next call asks git again and finds the tree
        init_repo(proj, "-q", "-b", "main", ident=IDENT)
        _git("remote", "add", "origin", HTTPS_ORIGIN, cwd=proj)
        self.assertEqual(km._github_repo_of(sub), REPO)
        self.assertEqual(self.forks["--show-toplevel"], 2)

    def test_an_empty_dot_git_below_a_repo_is_walked_past_by_git_and_re_asked_when_git_init_fills_it(self):
        # git's discovery skips an invalid `.git` directory and continues upward, so this cwd is FOUND —
        # with the parent's toplevel — through an entry that is not that toplevel's own. Keyed on its path
        # alone, as the toplevel's own directory is, `git init` filling it changed nothing in the key and
        # the parent's toplevel, branch and repo were served forever (review find, 2026-09-06)
        plain = _repo(self.root, "plain", HTTPS_ORIGIN)
        svc = os.path.join(plain, "svc")
        os.makedirs(os.path.join(svc, ".git"))
        os.utime(os.path.join(svc, ".git"), (1_600_000_000, 1_600_000_000))
        top = os.path.realpath(plain)
        self.assertEqual(self._build(svc), [((top, "main"), REPO, REPO)] * 3, "found: the PARENT's tree, as git says")
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1}, "asked once, then stats")
        init_repo(svc, "-q", "-b", "svc", ident=IDENT)
        with open(os.path.join(svc, "g.txt"), "w") as f:
            f.write("y\n")
        _git("add", "g.txt", cwd=svc)
        _git("commit", "-q", "-m", "nested", cwd=svc)
        _git("remote", "add", "origin", "https://github.com/example-org/notes-svc.git", cwd=svc)
        self.forks.clear()
        self.assertEqual(self._build(svc), [((os.path.realpath(svc), "svc"), "example-org/notes-svc", "example-org/notes-svc")] * 3,
                         "the entry git walked past is a repository now: its own toplevel, branch and repo")
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1},
                         "the filled directory's mtime is the evidence that moved: one re-ask, then stats")

    def test_the_toplevels_own_dot_git_directory_is_keyed_on_its_path_and_identity_never_its_mtime_through_a_symlink_too(self):
        # the evidence path is lexical (abspath), git's toplevel physical: through a symlinked cwd the two
        # spell the same `.git` differently, and a rule that kept the mtime there would re-fork on every
        # index write (a review caveat, 2026-09-06); an index write moves the directory's mtime. The
        # inode and device ARE in the key — they never move for a live repository, and a symlink re-pointed
        # at another one stats a different directory (the re-point test in DeadOrReshapedTree)
        plain = _repo(self.root, "real", HTTPS_ORIGIN)
        link = os.path.join(self.root, "link")
        os.symlink(plain, link)
        top = os.path.realpath(plain)
        st = os.stat(os.path.join(plain, ".git"))
        for d in (plain, link):
            self.assertEqual(self._build(d)[0][0], (top, "main"))
            self.assertEqual(km._tree_cache[d][1], (os.path.join(d, ".git"), st.st_ino, st.st_dev),
                             "keyed on the path and the directory's identity, no mtime (%s)" % d)
            self.assertIs(km._tree_cache[d][2], True, "…as the toplevel's own entry (%s)" % d)
            self.forks.clear()
            _bump_mtime(os.path.join(d, ".git"))
            self.assertEqual(self._build(d)[0][0], (top, "main"))
            self.assertEqual(self.forks, {}, "the toplevel's own .git directory: its mtime churn is no evidence (%s)" % d)

    def test_a_worktree_whose_main_clone_was_removed_is_asked_once_and_found_again_when_the_clone_is_back(self):
        main = _repo(self.root, "main", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-x")
        _git("worktree", "add", "-q", "-b", "x", wt, "HEAD", cwd=main)
        forbid_background(wt)
        self.assertEqual(km._github_repo_of(wt), REPO, "a live worktree names the clone's repo")
        backup = os.path.join(self.root, "backup")
        shutil.copytree(main, backup, symlinks=True)
        shutil.rmtree(main)                              # the pointer file in wt/.git now names a gitdir that is gone
        self.forks.clear()
        self.assertEqual(self._build(wt), [(("", ""), None, None)] * 3,
                         "a dangling pointer is no tree — the found verdict does not outlive the clone it names")
        self.assertEqual(self.forks, {"--show-toplevel": 1}, "asked once: the pointer has not changed since git rejected it")
        # the clone restored from a backup at the same path — the pointer file untouched — makes its gitdir exist
        # again, which is part of the evidence: the next call asks git again
        shutil.move(backup, main)
        self.assertEqual(km._github_repo_of(wt), REPO)
        self.assertEqual(self.forks["--show-toplevel"], 2)
        self.assertEqual(km._github_repo_of(wt), REPO)
        self.assertEqual(self.forks["--show-toplevel"], 2, "and the found verdict holds")

    def test_a_rewritten_pointer_re_asks_git(self):
        # `git worktree repair` rewrites the worktree's .git file in place; the mtime is the evidence that moved
        main = _repo(self.root, "main", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-y")
        _git("worktree", "add", "-q", "-b", "y", wt, "HEAD", cwd=main)
        forbid_background(wt)
        pointer = os.path.join(wt, ".git")
        good = open(pointer).read()
        with open(pointer, "w") as f:
            f.write("gitdir: %s\n" % os.path.join(self.root, "nowhere", ".git", "worktrees", "y"))
        os.utime(pointer, (1_600_000_000, 1_600_000_000))
        self.assertEqual(self._build(wt), [(("", ""), None, None)] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1})
        with open(pointer, "w") as f:
            f.write(good)
        self.assertEqual(km._github_repo_of(wt), REPO, "the repaired pointer is new evidence")
        self.assertEqual(self.forks["--show-toplevel"], 2)

    def test_a_git_failure_that_is_no_verdict_is_not_cached(self):
        # a timeout (or no git binary) is not git's answer: the next call asks again instead of serving ""
        proj = _repo(self.root, "proj", HTTPS_ORIGIN)
        real_run = km.subprocess.run
        state = {"fail": True}

        def flaky(*a, **k):
            argv = a[0] if a else k.get("args")
            if state["fail"] and isinstance(argv, (list, tuple)) and "--show-toplevel" in argv:
                state["fail"] = False
                raise subprocess.TimeoutExpired(argv, 2)
            return real_run(*a, **k)
        km.subprocess.run = flaky
        try:
            self.assertEqual(km._tree_of(proj), ("", ""), "this build shows nothing")
            self.assertNotIn(proj, km._tree_cache, "and nothing is cached")
            top, br = km._tree_of(proj)
            self.assertEqual((os.path.realpath(top), br), (os.path.realpath(proj), "main"), "the next build asks again")
        finally:
            km.subprocess.run = real_run


class DeadOrReshapedTree(unittest.TestCase):
    """The pointer memos (_head_path_cache, _config_path_cache) name files inside a tree that can die or
    change shape under a live session — the checkout deleted, a plain repo replaced by a worktree at the
    same path. Memoized forever, the dead path made every build fork `rev-parse --abbrev-ref` and
    `remote get-url` (review find, 2026-09-06). Now the reader evicts a path that stops resolving and
    resolves once more; a tree with no `.git` left is not a tree (_tree_of's evidence sees the entry go)."""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.root = tempfile.mkdtemp()
        if km._dotgit_on_chain(self.root):
            self.skipTest("the temp root sits inside a repository; these need a directory outside every tree")
        self.forks = {}
        self.addCleanup(_count_git(self.forks))

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _builds(self, d, n=3):
        return [(km._tree_of(d)[1], km._github_repo_of(d)) for _ in range(n)]

    def test_a_checkout_deleted_under_a_live_session_costs_one_re_ask_then_stats(self):
        d = _repo(self.root, "web", HTTPS_ORIGIN)
        self.assertEqual(self._builds(d), [("main", REPO)] * 3)
        self.forks.clear()
        shutil.rmtree(d)
        self.assertEqual(self._builds(d), [("", None)] * 3, "a deleted tree shows nothing — never a stale branch or repo")
        self.assertEqual(self.forks, {"--show-toplevel": 1},
                         "the .git entry is gone: one re-ask of the toplevel, then no fork of any kind")
        # the dead tree's pointer memos are never consulted again (the toplevel verdict is "" now); a tree
        # re-created at that path re-asks the toplevel first, and a memo path that no longer resolves is
        # evicted by its reader then (the reshape test below) — nothing here needs a cache sweep

    def test_a_plain_repo_replaced_by_a_worktree_at_the_same_path_re_resolves_its_pointers_once(self):
        solo = _repo(self.root, "solo", HTTPS_ORIGIN)
        self.assertEqual(self._builds(solo), [("main", REPO)] * 3)
        other = _repo(self.root, "other", "https://github.com/other-org/other-repo.git")
        shutil.rmtree(solo)
        _git("worktree", "add", "-q", "-b", "reshaped", solo, "HEAD", cwd=other)   # .git is a FILE now
        forbid_background(solo)
        self.forks.clear()
        self.assertEqual(self._builds(solo), [("reshaped", "other-org/other-repo")] * 3,
                         "the new shape's branch and repo, from its pointer file")
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1},
                         "the .git entry changed kind (new evidence: one re-ask of the toplevel) and each pointer "
                         "re-resolved once, then everything is memoized")
        top = os.path.realpath(solo)
        self.assertEqual(os.path.realpath(km._config_path_cache[top]), os.path.realpath(os.path.join(other, ".git", "config")))

    def test_a_worktree_replaced_by_a_plain_repo_at_the_same_path_forgets_the_old_clones_memos(self):
        # rm -rf, not `git worktree remove`: the clone keeps .git/worktrees/<name>, so the memoized HEAD
        # and config paths inside it still resolve — and, trusted, served the dead worktree's branch and
        # the old clone's repo for the kernel's life (`git worktree prune` leaves the clone's own config
        # in place, so it did not repair the repo half either; review find, 2026-09-06)
        main = _repo(self.root, "main", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-web")
        _git("worktree", "add", "-q", "-b", "web", wt, "HEAD", cwd=main)
        forbid_background(wt)
        self.assertEqual(self._builds(wt), [("web", REPO)] * 3)
        shutil.rmtree(wt)
        _repo(self.root, "main-web", "https://github.com/other-org/fresh.git")   # a plain clone where the worktree was
        self.assertTrue(os.path.isdir(os.path.join(main, ".git", "worktrees", "main-web")),
                        "the old clone still lists the dead worktree: its HEAD and config paths still resolve")
        self.forks.clear()
        self.assertEqual(self._builds(wt), [("main", "other-org/fresh")] * 3, "the plain repo's own branch and origin")
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1},
                         "the .git entry changed kind: one re-ask, the tree's memos forgotten and re-derived once")
        top = os.path.realpath(wt)
        self.assertEqual(os.path.realpath(km._head_path_cache[top]), os.path.realpath(os.path.join(wt, ".git", "HEAD")))
        self.assertEqual(os.path.realpath(km._config_path_cache[top]), os.path.realpath(os.path.join(wt, ".git", "config")))

    def test_one_clones_worktree_replaced_by_anothers_at_the_same_path_re_resolves_once(self):
        # the same kind (a pointer file) but another clone's: the pointer's mtime is the shape that moved
        a = _repo(self.root, "a", HTTPS_ORIGIN)
        b = _repo(self.root, "b", "https://github.com/other-org/fork.git")
        wt = os.path.join(self.root, "shared-name")
        _git("worktree", "add", "-q", "-b", "web", wt, "HEAD", cwd=a)
        forbid_background(wt)
        self.assertEqual(self._builds(wt), [("web", REPO)] * 3)
        shutil.rmtree(wt)
        _git("worktree", "add", "-q", "-b", "feature", wt, "HEAD", cwd=b)
        forbid_background(wt)
        _bump_mtime(os.path.join(wt, ".git"))   # a new pointer; its mtime must differ even within one clock tick
        self.forks.clear()
        self.assertEqual(self._builds(wt), [("feature", "other-org/fork")] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1})

    def test_one_clones_worktree_replaced_by_anothers_within_one_mtime_tick_is_re_asked(self):
        # the same replacement as above with the pointer's mtime NOT moving — what a filesystem with a
        # one-second (HFS+) or two-second (FAT) mtime tick reports when the rm -rf and the add land in
        # the same tick: keyed on (path, mtime) alone the verdict hit, _vouch_tree never ran, and the
        # first clone's branch and origin were served for the kernel's life (review find, 2026-09-06).
        # The re-made pointer is a new FILE — its inode is in the key. A hard link keeps the old file
        # allocated across the rm -rf, so the filesystem cannot hand the new pointer the freed inode
        # number back and the test does not depend on the allocator's mood.
        a = _repo(self.root, "a", HTTPS_ORIGIN)
        b = _repo(self.root, "b", "https://github.com/other-org/fork.git")
        wt = os.path.join(self.root, "shared-name")
        _git("worktree", "add", "-q", "-b", "web", wt, "HEAD", cwd=a)
        forbid_background(wt)
        self.assertEqual(self._builds(wt), [("web", REPO)] * 3)
        pointer = os.path.join(wt, ".git")
        old = os.stat(pointer)
        os.link(pointer, os.path.join(self.root, "keep-the-old-pointer-allocated"))
        shutil.rmtree(wt)
        _git("worktree", "add", "-q", "-b", "feature", wt, "HEAD", cwd=b)
        forbid_background(wt)
        os.utime(pointer, ns=(old.st_atime_ns, old.st_mtime_ns))   # the same tick, as a coarse filesystem reports it
        new = os.stat(pointer)
        self.assertEqual(new.st_mtime, old.st_mtime, "the mtime says nothing changed")
        self.assertNotEqual(new.st_ino, old.st_ino, "the inode says a new file (the old one is still allocated)")
        self.forks.clear()
        self.assertEqual(self._builds(wt), [("feature", "other-org/fork")] * 3, "the second clone's branch and origin")
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1},
                         "new evidence (the inode): one re-ask, the tree's memos forgotten and re-derived once")
        self.assertEqual(os.path.realpath(km._pointer_cache[os.path.abspath(pointer)][1]),
                         os.path.realpath(os.path.join(b, ".git", "worktrees", "shared-name")),
                         "the pointer memo names the second clone's private dir")

    def test_a_symlinked_cwd_re_pointed_at_another_repository_is_re_asked(self):
        # a session registered at ~/proj/current where current -> release-3; the user re-points the link at
        # release-4, a different repository. The chain stats the same lexical `current/.git`, so a key on
        # the path alone hit and served release-3's toplevel, branch and repo for the kernel's life (review
        # find, 2026-09-06; predates the evidence-keyed verdict — a found toplevel was trusted for life
        # before it). The directory's inode is in the key: the re-pointed link resolves to another one.
        ra = _repo(self.root, "release-3", HTTPS_ORIGIN)
        rb = _repo(self.root, "release-4", "https://github.com/other-org/fresh.git")
        _git("checkout", "-q", "-b", "bee", cwd=rb)
        link = os.path.join(self.root, "current")
        os.symlink(ra, link)
        self.assertEqual(self._builds(link), [("main", REPO)] * 3)
        self.assertEqual(os.path.realpath(km._tree_of(link)[0]), os.path.realpath(ra))
        os.remove(link)
        os.symlink(rb, link)
        self.forks.clear()
        self.assertEqual(self._builds(link), [("bee", "other-org/fresh")] * 3, "the repository the link points at now")
        self.assertEqual(os.path.realpath(km._tree_of(link)[0]), os.path.realpath(rb))
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1},
                         "a different `.git` under the same path: one re-ask; the new toplevel's own memos, derived once")
        st = os.stat(os.path.join(rb, ".git"))
        self.assertEqual(km._tree_cache[link][1], (os.path.join(link, ".git"), st.st_ino, st.st_dev),
                         "keyed on the lexical path and the directory it resolves to")

    def test_the_shape_records_own_bound_leaves_no_tree_with_unvouched_memos(self):
        # _top_shape is bounded at 512 toplevels and clears the four memos with itself — but a live tree's
        # memos are then refilled by _tree_of's HIT path with no record vouching for them, and a re-ask
        # that found no record treated the current shape as the recorded one: a reshape whose re-ask was
        # the tree's first after the bound served the dead worktree's branch and the old clone's repo for
        # the kernel's life — the bug _vouch_tree closes, re-opened for every tree at once (review find,
        # 2026-09-06). The bound is tripped through the kernel's own vouching: phantom toplevels outside
        # every tree (a real repository per record would cost seconds of git init for nothing more).
        main = _repo(self.root, "main", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-web")
        _git("worktree", "add", "-q", "-b", "web", wt, "HEAD", cwd=main)
        forbid_background(wt)
        self.assertEqual(self._builds(wt), [("web", REPO)] * 3)
        top = os.path.realpath(wt)
        self.assertIn(top, km._top_shape)
        i = 0
        while len(km._top_shape) <= 512:
            km._vouch_tree(os.path.join(self.root, "phantom-%d" % i))
            i += 1
        other = _repo(self.root, "other", "https://github.com/other-org/other-repo.git")
        self.assertEqual(self._builds(other), [("main", "other-org/other-repo")] * 3, "a new tree's first ask trips the bound")
        self.assertNotIn(top, km._top_shape, "the bound cleared the worktree's record…")
        self.assertNotIn(top, km._head_path_cache, "…and its memos")
        self.assertEqual(self._builds(wt), [("web", REPO)] * 3)
        self.assertIn(top, km._head_path_cache, "the hit path refilled the memos…")
        self.assertNotIn(top, km._top_shape, "…with no record vouching for them")
        shutil.rmtree(wt)
        _repo(self.root, "main-web", "https://github.com/other-org/fresh.git")   # a plain clone where the worktree was
        self.forks.clear()
        self.assertEqual(self._builds(wt), [("main", "other-org/fresh")] * 3,
                         "no record is no vouch: the re-ask forgets the unvouched memos, and the plain repo's own branch and origin show")
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1, "get-url": 1})
        self.assertIn(top, km._top_shape, "the tree is vouched again")

    def test_a_subdirectory_asked_about_for_the_first_time_forgets_nothing_of_its_memoized_tree(self):
        # forgetting is keyed on the tree's own .git changing shape, not on the re-ask alone — a first ask
        # for a new edit directory under a known tree (every new lastEditPath directory) must cost no fork
        d = _repo(self.root, "deep", HTTPS_ORIGIN)
        sub = os.path.join(d, "src", "pkg")
        os.makedirs(sub)
        self.assertEqual(self._builds(d), [("main", REPO)] * 3)
        self.forks.clear()
        self.assertEqual(self._builds(sub), [("main", REPO)] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1}, "the tree's shape is unchanged: its branch and repo memos stand")

    def test_a_removed_sibling_worktree_costs_no_fork_after_its_re_ask(self):
        # the repo convention: registered on the clone, working on a sibling worktree named by lastEditPath;
        # `git worktree remove` when finished deletes the worktree dir and its private gitdir
        main = _repo(self.root, "main", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-web")
        _git("worktree", "add", "-q", "-b", "web", wt, "HEAD", cwd=main)
        forbid_background(wt)
        self.assertEqual(self._builds(wt), [("web", REPO)] * 3)
        self.assertEqual(self._builds(main), [("main", REPO)] * 3)
        self.forks.clear()
        _git("worktree", "remove", "--force", wt, cwd=main)
        self.assertEqual(self._builds(wt), [("", None)] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1})
        self.assertEqual(self._builds(main), [("main", REPO)] * 3, "the clone is untouched")
        self.assertEqual(self.forks, {"--show-toplevel": 1}, "…and still served from its memos")

    def test_a_subdirectory_cwd_is_memoized_like_the_toplevel(self):
        # _git_branch(scwd) for a session whose cwd is a subdirectory of its tree forked per build before:
        # no .git of its own to key on. Through _tree_of it rides the toplevel's HEAD memo.
        d = _repo(self.root, "deep", HTTPS_ORIGIN)
        sub = os.path.join(d, "src", "pkg")
        os.makedirs(sub)
        self.assertEqual([km._git_branch(sub) for _ in range(3)], ["main"] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1})
        _git("checkout", "-q", "-b", "topic", cwd=d)
        self.assertEqual(km._git_branch(sub), "topic", "a HEAD move is still the refresh event")


class PerCallCost(unittest.TestCase):
    """What a memoized verdict costs per call, counted: two stats for a plain toplevel, three for a
    worktree's and NO read of its pointer file — the pointer's target is memoized on the file's mtime
    (_pointer_gitdir) and the entry's kind comes off the one stat the chain walk already made. Deriving
    both per call made a worktree cwd — this repo's own convention, so the shape most builds pay — cost
    five stats and a file read per call, some 40 us against a plain toplevel's 9 (review find,
    2026-09-06)."""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.root = tempfile.mkdtemp()
        if km._dotgit_on_chain(self.root):
            self.skipTest("the temp root sits inside a repository; these need a directory outside every tree")
        self.forks = {}
        self.addCleanup(_count_git(self.forks))

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _cost(self, fn):
        """(stats, file opens) of one call once every memo is warm — the steady state every build pays."""
        fn(); fn()
        n = {"stat": 0, "open": 0}
        real_stat, real_open = os.stat, builtins.open

        def counting_stat(*a, **k):
            n["stat"] += 1
            return real_stat(*a, **k)

        def counting_open(*a, **k):
            n["open"] += 1
            return real_open(*a, **k)
        os.stat, builtins.open = counting_stat, counting_open
        try:
            fn()
        finally:
            os.stat, builtins.open = real_stat, real_open
        return n["stat"], n["open"]

    def test_a_plain_toplevel_costs_two_stats_and_a_worktrees_three_with_no_read(self):
        main = _repo(self.root, "main", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-web")
        _git("worktree", "add", "-q", "-b", "web", wt, "HEAD", cwd=main)
        forbid_background(wt)
        self.assertEqual(self._cost(lambda: km._tree_of(main)), (2, 0), "the chain's stat and HEAD's")
        self.assertEqual(self._cost(lambda: km._tree_of(wt)), (3, 0), "…plus the pointer target's; the pointer itself is not read")
        self.assertEqual(self._cost(lambda: km._github_repo_of(wt)), (4, 0), "…plus the config file's")
        sub = os.path.join(wt, "a", "b")
        os.makedirs(sub)
        self.assertEqual(self._cost(lambda: km._tree_of(sub)), (5, 0), "one more per directory between the cwd and its .git")
        self.assertEqual(km._tree_of(wt), (os.path.realpath(wt), "web"))
        self.assertEqual(km._github_repo_of(wt), REPO)

    def test_the_pointer_is_read_once_more_only_when_its_mtime_moves(self):
        main = _repo(self.root, "main", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-x")
        _git("worktree", "add", "-q", "-b", "x", wt, "HEAD", cwd=main)
        forbid_background(wt)
        self.assertEqual(self._cost(lambda: km._tree_of(wt)), (3, 0))
        pointer = os.path.join(wt, ".git")
        with open(pointer) as f:
            line = f.read()
        with open(pointer, "w") as f:                     # `git worktree repair` rewrites it in place
            f.write(line)
        _bump_mtime(pointer)
        self.forks.clear()
        self.assertEqual(km._tree_of(wt), (os.path.realpath(wt), "x"))
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1},
                         "new evidence: one re-ask — and a rewritten pointer may name another clone (_vouch_tree), "
                         "so the tree's branch memo is re-derived once")
        self.assertEqual(self._cost(lambda: km._tree_of(wt)), (3, 0), "…and the rewritten pointer is memoized again")


class BareRepository(unittest.TestCase):
    """A session registered in a BARE clone (a bare-plus-worktrees layout) has no work tree for
    `rev-parse --show-toplevel` to name, so _tree_of says not-a-tree — and _git_branch, derived through
    it, showed no branch where the direct `--abbrev-ref HEAD` query used to (it answers in a bare repo,
    and forked per build there: nothing memoized it). The bare shape is recognized by git's own test —
    HEAD, objects/ and refs/ at the directory — and its branch rides the HEAD-mtime memo like a tree's
    (review find, 2026-09-06)."""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.root = tempfile.mkdtemp()
        if km._dotgit_on_chain(self.root):
            self.skipTest("the temp root sits inside a repository; these need a directory outside every tree")
        self.forks = {}
        self.addCleanup(_count_git(self.forks))

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_a_bare_repositorys_branch_is_the_one_its_head_names_memoized_on_head(self):
        src = _repo(self.root, "src", HTTPS_ORIGIN)
        bare = os.path.join(self.root, "bare.git")
        _git("clone", "-q", "--bare", src, bare, cwd=self.root)
        forbid_background(bare)
        self.assertEqual([km._git_branch(bare) for _ in range(3)], ["main"] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 1}, "asked once each, then memoized")
        self.assertIsNone(km._github_repo_of(bare), "no work tree, no tree-derived repo — as before")
        _git("branch", "other", cwd=bare)
        _git("symbolic-ref", "HEAD", "refs/heads/other", cwd=bare)
        _bump_mtime(os.path.join(bare, "HEAD"))
        self.assertEqual(km._git_branch(bare), "other", "HEAD moving is the refresh event, in a bare clone too")
        self.assertEqual(self.forks, {"--show-toplevel": 1, "--abbrev-ref": 2})

    def test_a_worktree_beside_the_bare_clone_is_an_ordinary_tree(self):
        src = _repo(self.root, "src", HTTPS_ORIGIN)
        bare = os.path.join(self.root, "bare.git")
        _git("clone", "-q", "--bare", src, bare, cwd=self.root)
        forbid_background(bare)
        wt = os.path.join(self.root, "topic")
        _git("worktree", "add", "-q", "-b", "topic", wt, "main", cwd=bare)
        forbid_background(wt)
        self.assertEqual(km._git_branch(wt), "topic")
        self.assertEqual(km._github_repo_of(wt), None, "the bare clone has no origin remote")

    def test_a_directory_outside_every_repository_is_still_no_branch_and_no_fork(self):
        d = os.path.join(self.root, "notes")
        os.makedirs(d)
        self.assertEqual([km._git_branch(d) for _ in range(3)], [""] * 3)
        self.assertEqual(self.forks, {"--show-toplevel": 1}, "not a tree, not bare: no branch query at all")


class PostalCardSenderHost(unittest.TestCase):
    """The inbound postal card carries the sender's HOST (`peerHost`, the message log's from_host: "" for
    this kernel's own sessions, a peer's name otherwise) beside the sender's name, so the chat links the
    body's `#123` against exactly the sender's session — the name alone let a remote homonym borrow a
    local session's repo when its host was not attached to the dashboard (review find, 2026-09-06). A
    log row with NO from_host at all — one from before the log stamped the field on every row — puts no
    peerHost on the card: read as "", a pre-field REMOTE sender was presented as this kernel's own
    (review find, 2026-09-06); the chat resolves such a card by the name alone, as before the field.
    Synthetic records only (invented sessions, placeholder ids, TESTHOST)."""
    ME = "11111111-2222-3333-4444-555555555555"
    SENDER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    MID = "1700000000.11111_22222.TESTHOST"

    def _card(self, **rec):
        row = {"id": self.MID, "from": "api", "fromId": self.SENDER, "fromHost": "", "toId": self.ME,
               "body": "see #12", "kind": "coordinate", "t": 1700000000, "park": False}
        row.update(rec)
        ev = {"kind": "user", "md": "<!-- romp-msg-id: %s -->" % self.MID, "uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
              "ts": "x", "human": False}
        saved_sum, saved_err = km._msg_summaries, km.sys.stderr
        km._msg_summaries, km.sys.stderr = (lambda: {}), io.StringIO()
        try:
            out = km._hydrate_postal([ev], {self.MID: row}, self.ME)
        finally:
            km._msg_summaries, km.sys.stderr = saved_sum, saved_err
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        return out[0]

    def test_a_local_senders_card_names_this_kernels_own_host_as_the_empty_string(self):
        card = self._card()
        self.assertEqual((card["direction"], card["peer"], card["peerHost"]), ("in", "api", ""))

    def test_a_remote_senders_card_names_the_host_the_log_stamped(self):
        card = self._card(fromHost="TESTHOST")
        self.assertEqual((card["peer"], card["peerHost"]), ("api", "TESTHOST"))

    def test_a_nameless_remote_sender_keeps_its_host_stub_name_and_the_host_field(self):
        card = self._card(**{"from": "?", "fromHost": "TESTHOST", "fromId": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"})
        self.assertEqual((card["peer"], card["peerHost"]), ("TESTHOST:77777777", "TESTHOST"))

    def test_a_row_from_before_the_log_stamped_a_host_puts_no_host_on_the_card(self):
        card = self._card(fromHost=None)
        self.assertEqual((card["direction"], card["peer"]), ("in", "api"))
        self.assertNotIn("peerHost", card, "absent, not '': the sender could be anyone's session")

    def test_the_index_tells_a_row_without_the_field_from_a_local_one(self):
        log = km.jd.STATE / "timeline" / "messages.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"t": 1700000000, "ev": "sent", "id": "1700000000.1_1.TESTHOST", "from": "api", "from_id": self.SENDER,
                 "to_id": self.ME, "body": "see #12"},                                        # before the field
                {"t": 1700000001, "ev": "sent", "id": "1700000001.1_1.TESTHOST", "from": "api", "from_id": self.SENDER,
                 "to_id": self.ME, "body": "see #13", "from_host": ""},                       # local, stamped so
                {"t": 1700000002, "ev": "sent", "id": "1700000002.1_1.TESTHOST", "from": "api", "from_id": self.SENDER,
                 "to_id": self.ME, "body": "see #14", "from_host": "TESTHOST"}]               # relayed
        saved = log.read_text() if log.exists() else None
        km._postal_index_memo[0] = None
        try:
            log.write_text("".join(json.dumps(r) + "\n" for r in rows))
            idx = km._postal_index()
            self.assertEqual([idx[r["id"]]["fromHost"] for r in rows], [None, "", "TESTHOST"])
        finally:
            km._postal_index_memo[0] = None
            if saved is None:
                log.unlink()
            else:
                log.write_text(saved)


class ConfigFile(unittest.TestCase):
    """_git_config_file's layouts, built by hand under a private temp dir: the file is the memo's mtime
    source, so a layout it cannot resolve forks `git remote get-url` on every build. The live-worktree
    test above covers an absolute gitdir with a `..`-relative commondir; these are the other branches."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _layout(self, name, gitdir_line):
        top = os.path.join(self.root, name)
        os.makedirs(top)
        with open(os.path.join(top, ".git"), "w") as f:
            f.write("gitdir: %s\n" % gitdir_line)
        return top

    def test_a_relative_gitdir_resolves_against_the_tree(self):
        gd = os.path.join(self.root, "main", ".git", "worktrees", "wt")
        os.makedirs(gd)
        with open(os.path.join(gd, "commondir"), "w") as f:
            f.write("../..\n")
        top = self._layout("wt", "../main/.git/worktrees/wt")
        self.assertEqual(km._git_config_file(top), os.path.join(self.root, "main", ".git", "config"))

    def test_a_gitdir_with_no_commondir_holds_its_own_config(self):
        # the submodule shape: .git is a file naming <super>/.git/modules/<name>, which has no commondir
        # and carries the submodule's own remotes
        gd = os.path.join(self.root, "super", ".git", "modules", "lib")
        os.makedirs(gd)
        top = self._layout("lib", gd)
        self.assertEqual(km._git_config_file(top), os.path.join(gd, "config"))

    def test_a_relative_gitdir_with_no_commondir_resolves_then_holds_its_own_config(self):
        gd = os.path.join(self.root, "store", "gd")
        os.makedirs(gd)
        top = self._layout("tree", "../store/gd")
        self.assertEqual(km._git_config_file(top), os.path.join(gd, "config"))

    def test_an_absolute_commondir_is_taken_as_is(self):
        gd = os.path.join(self.root, "private")
        shared = os.path.join(self.root, "shared")
        os.makedirs(gd)
        os.makedirs(shared)
        with open(os.path.join(gd, "commondir"), "w") as f:
            f.write(shared + "\n")
        top = self._layout("abs-common", gd)
        self.assertEqual(km._git_config_file(top), os.path.join(shared, "config"))

    def test_no_dot_git_at_all_is_no_config_file(self):
        top = os.path.join(self.root, "bare-dir")
        os.makedirs(top)
        self.assertEqual(km._git_config_file(top), "")

    def test_a_live_worktree_with_a_relative_gitdir_keeps_its_memo(self):
        # a hand-relativized worktree (the .git pointer rewritten relative, as some tooling does): the
        # repo is named AND a second call forks no `remote get-url` — the config file resolved, so the
        # mtime memo holds
        self._saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in self._saved_env.items()])
        main = _repo(self.root, "main-repo", HTTPS_ORIGIN)
        wt = os.path.join(self.root, "main-repo-wt")
        _git("worktree", "add", "-q", "-b", "wtb", wt, "HEAD", cwd=main)
        forbid_background(wt)
        with open(os.path.join(wt, ".git")) as f:
            gd = f.readline().strip()[len("gitdir:"):].strip()
        self.assertTrue(os.path.isabs(gd), "git writes the pointer absolute")
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: %s\n" % os.path.relpath(gd, wt))
        real = km._git_out
        reads = []

        def counting(args, cwd, *a, **kw):
            if list(args[:2]) == ["remote", "get-url"]:
                reads.append(cwd)
            return real(args, cwd, *a, **kw)
        km._git_out = counting
        try:
            self.assertEqual(km._github_repo_of(wt), REPO)
            self.assertEqual(km._github_repo_of(wt), REPO)
        finally:
            km._git_out = real
        self.assertEqual(len(reads), 1, "resolved config file → one read, then the memo")
        self.assertEqual(os.path.realpath(km._git_config_file(os.path.realpath(wt))),
                         os.path.realpath(os.path.join(main, ".git", "config")))


UNREGISTERED_SID = "00000000-0000-4000-8000-000000000000"   # never written to the names registry


class RegistryPath(_Fixtures):
    """Both frames derive the repo from ONE directory, _session_cwd: the names registry's cwd first, the
    transcript's cwd stamp for a session romp never registered. The chat frame passes the meta it has in
    hand, the feed's session rows pass the transcript path; a session with no registry entry must link the
    same repo on both — before the helper the feed read _cwd_of alone and linked nothing for it (review
    find, 2026-09-06). A PRIVATE synthetic sid, cleaned up after."""

    def setUp(self):
        km.NAMES.mkdir(parents=True, exist_ok=True)
        (km.NAMES / PRIVATE_SID).write_text("web\t%s\t#123456\t#ffffff\n" % self.https)
        self.transcript = os.path.join(tempfile.mkdtemp(), "transcript.jsonl")

    def tearDown(self):
        try:
            (km.NAMES / PRIVATE_SID).unlink()
        except FileNotFoundError:
            pass

    def _stamp(self, cwd):
        """A synthetic transcript whose records stamp `cwd`, the way the CLI stamps every record."""
        with open(self.transcript, "w") as f:
            f.write(json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-555555555555", "cwd": cwd,
                                "version": "2.1.0", "gitBranch": "main",
                                "message": {"role": "user", "content": "hello"}}) + "\n")
        return self.transcript

    def test_the_registered_cwd_names_the_sessions_repo(self):
        self.assertEqual(km._cwd_of(PRIVATE_SID), self.https)
        self.assertEqual(km._github_repo_of(km._session_cwd(PRIVATE_SID)), REPO)

    def test_the_registry_outranks_the_transcripts_stamp(self):
        # a shell `cd` inside a Bash call moves the CLI's tracked cwd, and the stamp with it — the
        # registry is the project directory whenever it exists
        path = self._stamp(self.elsewhere)
        self.assertEqual(km._session_cwd(PRIVATE_SID, path), self.https)
        self.assertEqual(km._session_cwd(PRIVATE_SID, meta={"cwd": self.elsewhere}), self.https)

    def test_a_never_registered_session_takes_the_transcripts_stamp_on_both_frames(self):
        path = self._stamp(self.https)
        self.assertEqual(km._cwd_of(UNREGISTERED_SID), "", "no registry entry")
        meta = km._session_meta(path)
        chat_frame = km._session_cwd(UNREGISTERED_SID, meta=meta)        # build_session: the meta in hand
        feed_row = km._session_cwd(UNREGISTERED_SID, path)               # build_feed: the transcript path
        self.assertEqual(chat_frame, self.https)
        self.assertEqual(feed_row, chat_frame, "the chat frame and the feed's session row name one directory")
        self.assertEqual(km._github_repo_of(feed_row), REPO)

    def test_no_registry_entry_and_no_transcript_is_no_directory_and_no_repo(self):
        self.assertEqual(km._session_cwd(UNREGISTERED_SID), "")
        self.assertEqual(km._session_cwd(UNREGISTERED_SID, None, None), "")
        self.assertIsNone(km._github_repo_of(km._session_cwd(UNREGISTERED_SID)))


class FrameWiring(unittest.TestCase):
    """build_session's and build_feed's wiring, pinned by source (the build_* test pattern)."""

    def test_the_session_frame_carries_the_repo_top_level(self):
        src = inspect.getsource(km.build_session)
        self.assertIn("scwd = _session_cwd(sid, meta=meta)", src, "the shared derivation, with the meta in hand")
        self.assertIn('"githubRepo": _github_repo_of(scwd),', src,
                      "top-level like gitBranch — never windowed off the wire")
        self.assertLess(src.index('"gitBranch": sysinfo["gitBranch"]'), src.index('"githubRepo": _github_repo_of(scwd)'))

    def test_the_session_frame_names_this_kernels_own_host(self):
        # the chat reads a postal card's sender host against the viewing kernel's own name; it learned
        # that name only from the + picker's reply before, so the reading was inert until the picker opened
        src = inspect.getsource(km.build_session)
        self.assertIn('"selfHost": _self_host(),', src, "top-level on the frame the chat receives for every session")
        feed = inspect.getsource(km.build_feed)
        self.assertIn('"selfHost": _self_host(),', feed, "the same name the feed frame carries")
        # …and the tabOrder frame, which every chat receives first of all: a dashboard whose kernel runs no
        # local session has no session frame to learn the name from (tests/test_kernel_tabs_first.py runs it)
        self.assertEqual(km._tab_order_frame([], [], [])["selfHost"], km._self_host())

    def test_the_feed_session_rows_carry_the_repo(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('"githubRepo": _github_repo_of(_session_cwd(s["sid"], s.get("path")))', src,
                      "each tab-strip session row names its repo from the SAME directory the chat frame uses")
        self.assertNotIn('_github_repo_of(_cwd_of(', src, "never the registry alone — a transcript-only session has none")


if __name__ == "__main__":
    unittest.main()
