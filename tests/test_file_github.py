#!/usr/bin/env python3
"""_file_github_url + the fileGitLink WS op — the viewer's GitHub link (the user 2026-08-15).

An empty url is a VERDICT, not an error: untracked files, non-repo paths, and non-GitHub origins
honestly have no link — and since 2026-09-05 the verdict carries a REASON (_file_github_link returns
(url, reason)), so the viewer shows the button disabled with the reason instead of hiding it; a branch
that is not on origin keeps its url and carries a note. The ref is the current branch (what a human
expects to read), or the sha when HEAD is detached. Real temp git repos, synthetic names only
(TESTORG / notes-api — the demo world); the one network query (ls-remote) is served by a LOCAL bare
repo through a stand-in ssh, so no test reaches GitHub.
"""
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
from git_fixture import git, init_repo
km = load_source("romp_kernel_github", os.path.join(BIN, "romp-kernel"))


# The fixture repos ignore the developer's global and system git config: a global core.hooksPath or
# LFS filter would otherwise run THEIR hooks on the fixture push below (on one dev box, git-lfs asked
# the stand-in ssh for git-lfs-authenticate and failed the push). What the kernel itself runs is the
# process environment's git, as in production — and the tests whose kernel call reaches origin pin
# THAT environment the same way (_WithOrigin below). GIT_CONFIG_GLOBAL is honoured by git >= 2.32.
_GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
_IDENT = ("t@testhost", "t")


def _git_env():
    return dict(_GIT_ENV, GIT_SSH_COMMAND=os.environ.get("GIT_SSH_COMMAND", "ssh"))


def _git(*args, cwd):
    # Through the shared runner: `git commit`, fetch and merge spawn a detached `git maintenance run --auto`
    # that can still be writing into .git while the fixture tempdir is removed (the CI flake "Directory not
    # empty: '.git'", 2026-09-10); git_fixture forbids that work on every call and in every repo init_repo makes.
    git(cwd, *args, ident=_IDENT, env=_git_env(), text=False)


def _local_origin(repo):
    """Serve `git@github.com:TESTORG/notes-api.git` from a LOCAL bare repo, no network. git runs
    `$GIT_SSH_COMMAND [opts] git@github.com "git-upload-pack 'TESTORG/notes-api.git'"`; the stand-in
    ignores everything but that last argument and runs it in a root that holds the bare repo at that
    relative path — real git on both ends. The remote URL stays a GitHub one, so _GITHUB_REMOTE still
    matches (a url.insteadOf rewrite would not do: `remote get-url` expands it). Pushes `repo`'s main,
    so origin starts with that branch. Returns the root; the caller owns restoring GIT_SSH_COMMAND."""
    root = tempfile.mkdtemp()
    bare = os.path.join(root, "TESTORG", "notes-api.git")
    os.makedirs(bare)
    init_repo(bare, "-q", "--bare", ident=_IDENT, env=_git_env())
    sh = os.path.join(root, "stand-in-ssh")
    with open(sh, "w") as f:
        f.write('#!/bin/sh\nfor a in "$@"; do cmd=$a; done\ncd "%s" && eval "$cmd"\n' % root)
    os.chmod(sh, 0o755)
    os.environ["GIT_SSH_COMMAND"] = sh
    _git("push", "-q", "origin", "main", cwd=repo)
    return root


def _script(root, name, body):
    p = os.path.join(root, name)
    with open(p, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(p, 0o755)
    return p


def _restore_env_after(tc, *names):
    """Register a cleanup that puts `names` back to their values NOW — addCleanup, not tearDown, so a
    setUp that fails after changing one of them still restores it (a tearDown never runs then)."""
    saved = {n: os.environ.get(n) for n in names}

    def restore():
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v
    tc.addCleanup(restore)


def _git_version():
    out = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    return tuple(int(x) for x in out.split()[2].split(".")[:2])


class _Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_repo(self.tmp, "-q", "-b", "main", ident=_IDENT, env=_git_env())
        os.makedirs(os.path.join(self.tmp, "src", "deep dir"))
        self.fp = os.path.join(self.tmp, "src", "app.py")
        with open(self.fp, "w") as f:
            f.write("print('hi')\n")
        self.spaced = os.path.join(self.tmp, "src", "deep dir", "notes file.md")
        with open(self.spaced, "w") as f:
            f.write("# notes\n")
        with open(os.path.join(self.tmp, "loose.txt"), "w") as f:
            f.write("untracked\n")
        _git("add", "src", cwd=self.tmp)
        _git("commit", "-q", "-m", "init", cwd=self.tmp)


class _WithOrigin(_Repo):
    """A repo whose origin is _local_origin's stand-in. The KERNEL's git runs with the PROCESS
    environment (as in production), so for these tests that environment is pinned hermetic too:
    GIT_CONFIG_GLOBAL=/dev/null (honoured by git >= 2.32) and GIT_CONFIG_NOSYSTEM=1. Without the pin
    a developer's global `url."https://github.com/".insteadOf = git@github.com:` rewrote the fixture
    origin under the kernel's ls-remote and the tests reached real github.com (reproduced 2026-09-05;
    three went red). The kernel's memo of origin answers starts empty: every test begins unasked."""

    URL = "https://github.com/TESTORG/notes-api/blob/%s/src/app.py"

    def setUp(self):
        _restore_env_after(self, "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_SSH_COMMAND")
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        km._ORIGIN_MEMO.clear()
        km._ORIGIN_INFLIGHT.clear()
        super().setUp()
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.root = _local_origin(self.tmp)          # origin has main; the push also wrote the tracking ref
        self.ssh = os.path.join(self.root, "stand-in-ssh")

    def counting_ssh(self, before=""):
        """Point the kernel at a stand-in ssh that counts the queries that went out and runs `before`
        (shell) ahead of each; returns the count reader. Counts the git-upload-pack calls only: a
        GIT_SSH_COMMAND not named ssh also gets git's variant probe (`-G <host>`) before each one."""
        log = os.path.join(self.root, "asked.log")
        os.environ["GIT_SSH_COMMAND"] = _script(
            self.root, "counting-ssh",
            'case "$*" in *git-upload-pack*) echo x >> "%s";; esac\n%sexec "%s" "$@"\n'
            % (log, before, self.ssh))

        def asked():
            if not os.path.exists(log):
                return 0
            with open(log) as f:
                return f.read().count("x")
        return asked


class GitHubUrl(_Repo):
    def test_the_ssh_remote_spelling_builds_the_blob_url(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_the_https_remote_spelling_builds_the_same_url(self):
        _git("remote", "add", "origin", "https://github.com/TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_path_segments_are_quoted_but_slashes_survive(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.spaced, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/deep%20dir/notes%20file.md")

    def test_a_slashed_branch_name_stays_literal_like_githubs_own_urls(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        _git("checkout", "-q", "-b", "feat/deep-work", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/feat/deep-work/src/app.py")

    def test_a_detached_head_links_the_sha(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        sha = git(self.tmp, "rev-parse", "HEAD", check=False).stdout.strip()
        _git("checkout", "-q", sha, cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/%s/src/app.py" % sha)

    def test_a_tag_named_like_the_branch_leaves_the_ref_alone(self):
        # `rev-parse --abbrev-ref HEAD` disambiguates a branch/tag name clash as heads/main, and GitHub
        # 404s that spelling (reproduced 2026-09-05); the branch name itself is what the URL wants
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        _git("tag", "main", cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_a_symlinked_path_prefix_still_links(self):
        # executed repro: git reports the PHYSICAL toplevel, so a logical path through a symlink
        # escaped relpath and silently un-linked every tracked file behind one
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        outer = tempfile.mkdtemp()
        link = os.path.join(outer, "via-link")
        os.symlink(self.tmp, link)
        self.assertEqual(km._file_github_url(os.path.join(link, "src", "app.py"), None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")

    def test_dotdot_through_a_symlink_never_links_the_wrong_file(self):
        # executed repro: a LEXICAL '..' collapse linked a different file than the bytes the viewer
        # shows; realpath resolves the symlink first, and the escape gets the honest no-link
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        os.symlink(tempfile.mkdtemp(), os.path.join(self.tmp, "ext"))
        self.assertEqual(
            km._file_github_url(os.path.join(self.tmp, "ext", "..", "src", "app.py"), None), "",
            "the OS would read outside the repo — a wrong link is worse than none")

    def test_port_bearing_and_ssh_over_https_origins_link(self):
        # GitHub's own SSH-over-HTTPS doc writes ssh://git@ssh.github.com:443/OWNER/REPO.git
        for url in ("ssh://git@ssh.github.com:443/TESTORG/notes-api.git",
                    "ssh://git@github.com:22/TESTORG/notes-api.git",
                    "https://github.com:443/TESTORG/notes-api.git"):
            m = km._GITHUB_REMOTE.match(url)
            self.assertIsNotNone(m, url)
            self.assertEqual((m.group(1), m.group(2)), ("TESTORG", "notes-api"), url)
        for url in ("git@github.example.com:TESTORG/notes-api.git",
                    "https://github.com.evil.io/TESTORG/notes-api.git"):
            self.assertIsNone(km._GITHUB_REMOTE.match(url), url)

    def test_a_root_file_named_with_leading_dots_still_links(self):
        # the escape guard tests the path relation, never a name prefix
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        dd = os.path.join(self.tmp, "..cfg")
        with open(dd, "w") as f:
            f.write("k=v\n")
        _git("add", "--", "..cfg", cwd=self.tmp)
        _git("commit", "-q", "-m", "cfg", cwd=self.tmp)
        self.assertEqual(km._file_github_url(dd, None),
                         "https://github.com/TESTORG/notes-api/blob/main/..cfg")

    def test_no_link_verdicts_name_their_reason(self):
        # the user 2026-09-05 could not tell an uncommitted file from a broken link: every no-link
        # verdict now says which, in a plain phrase the viewer shows verbatim
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_link(os.path.join(self.tmp, "loose.txt"), None),
                         ("", "not committed (untracked file)"), "untracked file — no link to a thing not there")
        self.assertEqual(km._file_github_link(tempfile.mkdtemp(), None), ("", "not in a git repository"))
        _git("remote", "set-url", "origin", "git@gitlab.example.com:TESTORG/notes-api.git", cwd=self.tmp)
        self.assertEqual(km._file_github_link(self.fp, None), ("", "the origin remote is not on GitHub"))
        # the url-only caller keeps its "" verdict
        self.assertEqual(km._file_github_url(self.fp, None), "")

    def test_a_repo_with_no_origin_remote_says_that_not_not_on_github(self):
        # `remote get-url origin` fails when there is no such remote; that used to read as "the origin
        # remote is not on GitHub", which names a remote the repo does not have
        self.assertEqual(km._file_github_link(self.fp, None), ("", "no origin remote"))
        self.assertEqual(km._file_github_url(self.fp, None), "")

    def test_a_file_staged_on_no_commit_is_not_committed(self):
        # an unborn branch: ls-files sees the index entry, but HEAD names nothing to link
        fresh = tempfile.mkdtemp()
        init_repo(fresh, "-q", "-b", "main", ident=_IDENT, env=_git_env())
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=fresh)
        fp = os.path.join(fresh, "new.py")
        with open(fp, "w") as f:
            f.write("pass\n")
        _git("add", "new.py", cwd=fresh)
        self.assertEqual(km._file_github_link(fp, None), ("", "not committed (no commits yet)"))

    def test_a_file_staged_on_a_branch_with_commits_is_not_committed(self):
        # the unborn case's sibling: HEAD is real, ls-files sees the index entry — and the URL that used
        # to come back named a path GitHub has on no ref, so the viewer drew an enabled button that 404d
        # (found in review). Only the tree HEAD names decides what a push can put on GitHub.
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        fp = os.path.join(self.tmp, "src", "new.py")
        with open(fp, "w") as f:
            f.write("pass\n")
        _git("add", "src/new.py", cwd=self.tmp)
        self.assertEqual(km._file_github_link(fp, None), ("", "not committed (staged only)"))
        self.assertEqual(km._file_github_url(fp, None), "")
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py",
                         "its committed neighbour still links")
        _git("commit", "-q", "-m", "new", cwd=self.tmp)
        self.assertEqual(km._file_github_url(fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/new.py",
                         "the commit is the event: nothing else changed")

    def test_a_relative_path_resolves_against_the_sessions_cwd(self):
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        real = km._cwd_of
        km._cwd_of = lambda sid: self.tmp if sid == "11111111-2222-3333-4444-000000000001" else None
        try:
            self.assertEqual(km._file_github_url("src/app.py", "11111111-2222-3333-4444-000000000001"),
                             "https://github.com/TESTORG/notes-api/blob/main/src/app.py")
            self.assertEqual(km._file_github_link("src/app.py", None),
                             ("", "relative path with no session directory to resolve it against"),
                             "no sid, no base — no guess; and the verdict names THAT, not a repository "
                             "check the kernel never ran (found in review)")
        finally:
            km._cwd_of = real


    def test_the_branch_name_needs_no_modern_git(self):
        # `branch --show-current` arrived in git 2.22; below it the query fails, and a failed branch
        # query silently gave EVERY file a commit-sha URL where the branch URL was right (found in
        # review). The branch comes from `symbolic-ref -q HEAD` now, which every git has and which a
        # same-named tag cannot bend either. Reproduced with a git that rejects the option and hands
        # everything else on — first on PATH, which is where the kernel finds its git.
        _git("remote", "add", "origin", "git@github.com:TESTORG/notes-api.git", cwd=self.tmp)
        _git("tag", "main", cwd=self.tmp)
        real = shutil.which("git")
        _restore_env_after(self, "PATH")
        old = tempfile.mkdtemp()
        _script(old, "git",
                'for a in "$@"; do [ "$a" = "--show-current" ] && '
                '{ echo "error: unknown option \\`show-current\'" >&2; exit 129; }; done\n'
                'exec "%s" "$@"\n' % real)
        os.environ["PATH"] = old + os.pathsep + os.environ["PATH"]
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/main/src/app.py")
        sha = git(self.tmp, "rev-parse", "HEAD", check=False).stdout.strip()
        _git("checkout", "-q", sha, cwd=self.tmp)
        self.assertEqual(km._file_github_url(self.fp, None),
                         "https://github.com/TESTORG/notes-api/blob/%s/src/app.py" % sha,
                         "detached: the sha stays the only honest ref")


class BranchOnOrigin(_WithOrigin):
    """The branch note (the user 2026-09-05): a worktree branch never pushed 404s on GitHub, so the
    url comes back WITH a reason. The local tracking ref answers first and free; only its absence
    pays one ls-remote, served here by _local_origin's stand-in ssh — never the network."""

    def test_the_kernels_git_reads_no_global_config_here(self):
        # The fixture's pin, shown load-bearing (reproduced 2026-09-05 with a global insteadOf rewrite
        # that sent the kernel's ls-remote to real github.com). Offline twin: a rewrite to another
        # owner, which the stand-in has no repo for. With a developer's global config in force the
        # kernel would build the wrong URL and fail the check; under the fixture's pin it never reads it.
        if _git_version() < (2, 32):
            self.skipTest("GIT_CONFIG_GLOBAL needs git >= 2.32")
        cfg = os.path.join(self.root, "developer-gitconfig")
        with open(cfg, "w") as f:
            f.write('[url "git@github.com:OTHERORG/"]\n\tinsteadOf = git@github.com:TESTORG/\n')
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        os.environ["GIT_CONFIG_GLOBAL"] = cfg
        self.assertEqual(km._file_github_link(self.fp, None),
                         ("https://github.com/OTHERORG/notes-api/blob/wip/src/app.py",
                          "could not check whether branch wip is on origin"),
                         "the leak, live: the kernel's git honours whatever global config the environment names")
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull    # the fixture's pin
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "wip", "branch wip is not on origin"))

    def test_a_pushed_branch_carries_no_note_and_asks_origin_nothing(self):
        os.environ["GIT_SSH_COMMAND"] = "false"       # any network query would come back "unchecked"
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "main", ""))

    def test_a_missing_tracking_ref_falls_to_ls_remote(self):
        # a clone that never fetched the ref: the local answer is absent, origin itself has the branch
        _git("update-ref", "-d", "refs/remotes/origin/main", cwd=self.tmp)
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "main", ""))

    def test_a_branch_never_pushed_keeps_its_url_and_says_so(self):
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "wip", "branch wip is not on origin"))

    def test_a_tag_named_like_the_branch_never_bends_the_note(self):
        # the heads/<branch> spelling would also have asked origin for a branch nobody has and said
        # "branch heads/main is not on origin" about a branch that IS there (reproduced 2026-09-05)
        _git("tag", "main", cwd=self.tmp)
        os.environ["GIT_SSH_COMMAND"] = "false"       # the tracking ref answers; nothing goes out
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "main", ""))
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        _git("tag", "wip", cwd=self.tmp)
        os.environ["GIT_SSH_COMMAND"] = self.ssh
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "wip", "branch wip is not on origin"))

    def test_a_slashed_branch_is_matched_whole_not_by_tail(self):
        # ls-remote patterns match a ref's TAIL: `wip` alone would also match refs/heads/x/wip
        _git("checkout", "-q", "-b", "feat/wip", cwd=self.tmp)
        _git("push", "-q", "origin", "feat/wip:refs/heads/other/feat/wip", cwd=self.tmp)
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "feat/wip", "branch feat/wip is not on origin"))

    def test_an_unreachable_origin_is_unchecked_not_asserted(self):
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        os.environ["GIT_SSH_COMMAND"] = "false"
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "wip", "could not check whether branch wip is on origin"))

    def test_a_slow_origin_is_cut_off_because_the_viewer_is_waiting(self):
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        os.environ["GIT_SSH_COMMAND"] = _script(self.root, "slow-ssh", "sleep 5\n")
        real = km.GH_LS_REMOTE_S
        km.GH_LS_REMOTE_S = 0.3
        try:
            t0 = time.time()
            self.assertEqual(km._file_github_link(self.fp, None),
                             (self.URL % "wip", "could not check whether branch wip is on origin"))
            self.assertLess(time.time() - t0, 2.0, "the timeout, not the remote, ends the wait")
        finally:
            km.GH_LS_REMOTE_S = real

    def test_the_cut_kills_the_ssh_git_spawned_not_git_alone(self):
        # subprocess.run's timeout kill reached git alone; the ssh it had spawned sat in its TCP connect
        # for minutes after the viewer was told "could not check" (reproduced 2026-09-05). The query
        # runs in its own session now and the deadline kills the whole group. Pinned on the MECHANISM
        # (T276c): the earlier version raced a real git against a 0.3 s deadline and a stand-in ssh's
        # pidfile — on a slow runner git had not reached ssh by the cut, and the test flaked. Here a fake
        # Popen stands in for git: its first communicate times out, and the query must (1) have started
        # git in its own session (start_new_session, so the pid IS the group) and (2) kill THE GROUP by
        # that pid with SIGKILL, then reap — the two facts that reach the ssh under git.
        calls = {"popen": [], "killpg": [], "communicate": 0}
        class FakeGit:
            pid = 7_654_321                      # above pid_max: never a live process on the box
            def communicate(self, timeout=None):
                calls["communicate"] += 1
                if calls["communicate"] == 1:
                    raise subprocess.TimeoutExpired(cmd="git", timeout=timeout)
                return ("", "")
            def kill(self):
                calls.setdefault("kill", 0); calls["kill"] += 1
        def fake_popen(argv, **kw):
            calls["popen"].append((list(argv), kw)); return FakeGit()
        with mock.patch.object(km.subprocess, "Popen", side_effect=fake_popen), \
             mock.patch.object(km.os, "killpg", side_effect=lambda g, s: calls["killpg"].append((g, s))):
            out = km._git_net_out(["ls-remote", "--exit-code", "--heads", "origin", "wip"], self.tmp, 0.3, dict(os.environ))
        self.assertIsNone(out, "a timed-out query has no answer")
        self.assertEqual(len(calls["popen"]), 1)
        self.assertTrue(calls["popen"][0][1].get("start_new_session"), "git starts in its own session, so its pid names the whole group")
        self.assertEqual(calls["killpg"], [(FakeGit.pid, km.signal.SIGKILL)], "the deadline kills the GROUP under git's pid — the ssh with it")
        self.assertEqual(calls["communicate"], 2, "…and the dead group is reaped")

    def test_a_detached_sha_is_never_checked(self):
        sha = git(self.tmp, "rev-parse", "HEAD", check=False).stdout.strip()
        _git("checkout", "-q", sha, cwd=self.tmp)
        os.environ["GIT_SSH_COMMAND"] = "false"
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % sha, ""))

    def test_a_second_open_reuses_the_answer_instead_of_asking_origin_again(self):
        # a never-pushed branch paid one round trip per viewer open (2026-09-05); the answer now holds
        # until the local check changes its verdict — no clock on it
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        asked = self.counting_ssh()
        for _ in range(2):
            self.assertEqual(km._file_github_link(self.fp, None),
                             (self.URL % "wip", "branch wip is not on origin"))
        self.assertEqual(asked(), 1, "one ls-remote per (repo, branch)")
        _git("checkout", "-q", "-b", "other", cwd=self.tmp)
        self.assertEqual(km._file_github_link(self.fp, None)[1], "branch other is not on origin")
        self.assertEqual(asked(), 2, "another branch is another key")

    def test_the_tracking_ref_appearing_flips_the_verdict_for_free(self):
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        asked = self.counting_ssh()
        self.assertEqual(km._file_github_link(self.fp, None)[1], "branch wip is not on origin")
        # what a push from the session writes (the push itself would go through the counting ssh)
        _git("update-ref", "refs/remotes/origin/wip", "HEAD", cwd=self.tmp)
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "wip", ""))
        self.assertEqual(asked(), 1, "the tracking ref answered; no new ls-remote")
        # ...and its disappearance (a fetch --prune after a deletion on GitHub) asks origin afresh
        _git("update-ref", "-d", "refs/remotes/origin/wip", cwd=self.tmp)
        self.assertEqual(km._file_github_link(self.fp, None)[1], "branch wip is not on origin")
        self.assertEqual(asked(), 2, "the local verdict changed, so the memo was dropped")

    def test_a_branch_on_origin_that_this_clone_never_fetched_is_asked_each_time(self):
        # pushed from another clone: origin has it and this clone has no tracking ref. A True memoized
        # here would have no drop event — a deletion on GitHub writes nothing locally, and `fetch
        # --prune` has no ref to prune — and served a 404 link with no caption until the kernel
        # restarted (found in review). So each open asks, until a fetch writes the tracking ref and
        # the free check takes over.
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        _git("push", "-q", "origin", "wip", cwd=self.tmp)
        _git("update-ref", "-d", "refs/remotes/origin/wip", cwd=self.tmp)   # as a clone that never fetched it
        asked = self.counting_ssh()
        for _ in range(2):
            self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "wip", ""))
        self.assertEqual(asked(), 2, "a True with no tracking ref is not memoized: nothing local could contradict it")
        _git("update-ref", "-d", "refs/heads/wip", cwd=os.path.join(self.root, "TESTORG", "notes-api.git"))
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "wip", "branch wip is not on origin"),
                         "deleted on GitHub: the next open says so, no restart needed")

    def test_a_clone_whose_refspec_leaves_the_branch_untracked_is_asked_each_time(self):
        # `git clone --single-branch` writes +refs/heads/main:refs/remotes/origin/main, so a push of any
        # OTHER branch from this clone writes no tracking ref. A memoized False would have outlived the
        # push and captioned a working link as not on origin until restart (found in review). With no
        # local event able to contradict it, nothing is memoized here; each open asks.
        _git("config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main", cwd=self.tmp)
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        asked = self.counting_ssh()
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "wip", "branch wip is not on origin"))
        _git("push", "-q", "origin", "wip", cwd=self.tmp)
        self.assertIsNone(km._git_out(["rev-parse", "--verify", "--quiet", "refs/remotes/origin/wip"], self.tmp),
                          "the fixture reproduces the gap: this push wrote no tracking ref")
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "wip", ""),
                         "the push is seen on the next open, not after a restart")
        self.assertEqual(asked(), 2)

    def test_a_refspec_ahead_of_the_default_takes_the_push_as_git_does(self):
        # git updates the tracking ref of the FIRST fetch refspec that covers a pushed branch, and no
        # other: with a `refs/remotes/other/*` line ahead of the default, the push writes other's ref.
        # Reading any match as tracked memoized a False that push could not contradict, so the working
        # link stayed captioned as not on origin until the next fetch (found in review).
        _git("config", "--replace-all", "remote.origin.fetch", "+refs/heads/*:refs/remotes/other/*", cwd=self.tmp)
        _git("config", "--add", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", cwd=self.tmp)
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        asked = self.counting_ssh()
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "wip", "branch wip is not on origin"))
        _git("push", "-q", "origin", "wip", cwd=self.tmp)
        self.assertIsNone(km._git_out(["rev-parse", "--verify", "--quiet", "refs/remotes/origin/wip"], self.tmp),
                          "the fixture reproduces git's first-match rule: this push wrote no origin/wip")
        self.assertTrue(km._git_out(["rev-parse", "--verify", "--quiet", "refs/remotes/other/wip"], self.tmp),
                        "...it wrote other/wip, the first line's destination")
        self.assertEqual(km._file_github_link(self.fp, None), (self.URL % "wip", ""),
                         "the push is seen on the next open, not after the next fetch")
        self.assertEqual(asked(), 2)

    def test_the_fetch_refspec_decides_what_a_push_can_contradict(self):
        # the memo's precondition, on the refspec shapes git writes: the default wildcard, a
        # --single-branch exact ref (for that branch alone), a second remote's namespace, a negative
        # refspec that excludes the branch, GitHub's pull-request refspec riding beside the default, a
        # second namespace ahead of the default and behind it (the first positive match decides, as in
        # git's push), and a source-only line ahead of the default (git skips a refspec with no
        # destination; so does the read)
        for specs, tracked in ((["+refs/heads/*:refs/remotes/origin/*"], True),
                               (["+refs/heads/wip:refs/remotes/origin/wip"], True),
                               (["+refs/heads/main:refs/remotes/origin/main"], False),
                               (["+refs/heads/*:refs/remotes/upstream/*"], False),
                               (["+refs/heads/*:refs/remotes/origin/*", "^refs/heads/wip"], False),
                               (["^refs/heads/wip", "+refs/heads/*:refs/remotes/origin/*"], False),
                               (["+refs/heads/*:refs/remotes/origin/*",
                                 "+refs/pull/*/head:refs/remotes/origin/pr/*"], True),
                               (["+refs/heads/*:refs/remotes/other/*", "+refs/heads/*:refs/remotes/origin/*"], False),
                               (["+refs/heads/*:refs/remotes/origin/*", "+refs/heads/*:refs/remotes/other/*"], True),
                               (["refs/heads/wip", "+refs/heads/*:refs/remotes/origin/*"], True)):
            _git("config", "--replace-all", "remote.origin.fetch", specs[0], cwd=self.tmp)
            for extra in specs[1:]:
                _git("config", "--add", "remote.origin.fetch", extra, cwd=self.tmp)
            self.assertEqual(km._origin_tracks(self.tmp, "wip"), tracked, specs)
        _git("config", "--unset-all", "remote.origin.fetch", cwd=self.tmp)
        self.assertFalse(km._origin_tracks(self.tmp, "wip"), "no refspec at all: nothing can write the ref")

    def test_concurrent_opens_share_one_query(self):
        # rapid opens used to put as many ls-remotes in flight as opens; askers of one key now wait
        # for the one in flight and take its answer
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        asked = self.counting_ssh(before="sleep 0.3\n")
        got = []
        ts = [threading.Thread(target=lambda: got.append(km._file_github_link(self.fp, None)))
              for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(15)
        self.assertEqual(got, [(self.URL % "wip", "branch wip is not on origin")] * 6)
        self.assertEqual(asked(), 1)

    def test_an_unanswered_query_is_asked_again_next_time(self):
        # "could not check" is not an answer; memoizing it would make one blip permanent
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        os.environ["GIT_SSH_COMMAND"] = "false"
        self.assertEqual(km._file_github_link(self.fp, None)[1],
                         "could not check whether branch wip is on origin")
        asked = self.counting_ssh()
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "wip", "branch wip is not on origin"))
        self.assertEqual(asked(), 1)

    def test_the_url_only_caller_never_asks_origin(self):
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        marker = os.path.join(self.root, "asked")
        os.environ["GIT_SSH_COMMAND"] = _script(self.root, "probe-ssh", "touch '%s'\nexit 255\n" % marker)
        self.assertEqual(km._file_github_url(self.fp, None), self.URL % "wip")
        self.assertFalse(os.path.exists(marker), "_file_github_url pays no network query")


class NoPromptOnOrigin(_WithOrigin):
    """The origin check's git must never pop a prompt of any kind (the #947 review). GIT_TERMINAL_PROMPT=0
    closes the terminal route only: the child inherited GIT_ASKPASS, core.askPass and SSH_ASKPASS, so an
    https origin answering 401 asked for a username and a password through the first of those it found —
    with a GUI askpass exported in the kernel's shell, a prompt on every viewer open of a file under such
    an origin, then killed at the deadline. ssh's own SSH_ASKPASS use (DISPLAY set, no tty) was open too.
    The chosen behaviour: such an origin reads "could not check" rather than a verdict, and an askpass
    that would have answered silently for a signed-in user is refused with the rest."""

    def test_the_transport_child_sees_every_prompt_route_closed(self):
        # the environment git hands its transport child is the one its own askpass lookup read; the
        # stand-in ssh dumps it. Set-EMPTY GIT_ASKPASS is what overrides core.askPass and SSH_ASKPASS: git
        # tests the variable's presence before falling to either, so unset would not do — pinned as
        # `${GIT_ASKPASS+set}:` so a later cleanup to a pop() reads as the regression it is.
        _restore_env_after(self, "GIT_ASKPASS", "SSH_ASKPASS", "DISPLAY")
        gui = os.path.join(self.root, "gui-askpass")          # a path only; nothing runs it on the ssh path
        os.environ["GIT_ASKPASS"] = os.environ["SSH_ASKPASS"] = gui
        os.environ["DISPLAY"] = ":0"
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        dump = os.path.join(self.root, "child-env")
        os.environ["GIT_SSH_COMMAND"] = _script(
            self.root, "env-ssh",
            'printf "%%s\\n" "${GIT_ASKPASS+set}:$GIT_ASKPASS" "$SSH_ASKPASS_REQUIRE" "$GIT_TERMINAL_PROMPT" > "%s"\n'
            'exit 255\n' % dump)
        self.assertEqual(km._file_github_link(self.fp, None),
                         (self.URL % "wip", "could not check whether branch wip is on origin"),
                         "the stand-in refused, so the verdict is unchecked: the dump came from the kernel's query")
        with open(dump) as f:
            self.assertEqual(f.read().splitlines(), ["set:", "never", "0"],
                             "GIT_ASKPASS set-empty (never unset), SSH_ASKPASS_REQUIRE=never, GIT_TERMINAL_PROMPT=0")

    def test_a_credential_wanting_origin_pops_no_askpass_and_reads_unchecked(self):
        # A loopback origin answering 401 with a Basic challenge is the credential-wanting private origin;
        # a marker-writing askpass stands in for the GUI one. The control comes first: under
        # GIT_TERMINAL_PROMPT=0 alone git DOES run the askpass, so the stand-in elicits the prompt this
        # test is about. Then the kernel's query: no askpass run, and the verdict is unchecked.
        # _origin_has_branch is called directly because _file_github_link's URL check reads a loopback
        # origin as not GitHub before any ls-remote (and _local_origin's note says why an insteadOf
        # rewrite cannot stand in for the URL).
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()
        if not os.path.exists(os.path.join(exec_path, "git-remote-https")):
            self.skipTest("this git has no https transport")

        class Challenge(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="notes-api"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), Challenge)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)                        # cleanups run last-added first: shutdown, then close
        marker = os.path.join(self.root, "asked")
        _restore_env_after(self, "GIT_ASKPASS")
        os.environ["GIT_ASKPASS"] = _script(self.root, "askpass", 'echo asked >> "%s"\necho user\n' % marker)
        _git("remote", "set-url", "origin", "http://127.0.0.1:%d/TESTORG/notes-api.git" % srv.server_address[1],
             cwd=self.tmp)
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        git(self.tmp, "ls-remote", "--heads", "origin", "refs/heads/wip",
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), timeout=30, check=False, text=False)
        self.assertTrue(os.path.exists(marker), "control: under GIT_TERMINAL_PROMPT=0 alone git runs the askpass")
        os.remove(marker)
        self.assertIsNone(km._origin_has_branch(self.tmp, "wip"), "a credential-wanting origin reads as unchecked")
        self.assertFalse(os.path.exists(marker), "the kernel's query ran no askpass program")


class GitLinkWire(_WithOrigin):
    """The WS op through the real dispatcher. The op answers on a THREAD (three git subprocesses must
    not block the recv loop), so the harness waits for the reply instead of reading it synchronously."""

    def setUp(self):
        super().setUp()
        self.sent = []
        self.client = {"app": "feed", "alive": True,
                       "send": lambda s: self.sent.append(json.loads(s))}
        self.handler = object.__new__(km.Handler)

    def send_and_wait(self, msg, timeout=10):
        km.Handler._dispatch_ws(self.handler, msg, self.client)
        deadline = time.time() + timeout
        while not self.sent and time.time() < deadline:
            time.sleep(0.02)
        return self.sent[-1] if self.sent else None

    def test_the_reply_echoes_the_request_id_with_the_url(self):
        r = self.send_and_wait({"type": "fileGitLink", "path": self.fp, "reqId": 6})
        self.assertIsNotNone(r, "the threaded op must still always reply")
        self.assertEqual(r["type"], "fileGitLink")
        self.assertEqual(r["reqId"], 6, "echoed so a reply landing after a newer open is dropped")
        self.assertEqual(r["url"], "https://github.com/TESTORG/notes-api/blob/main/src/app.py")
        self.assertEqual(r["reason"], "", "a pushed branch has nothing to add")

    def test_the_no_link_verdict_still_replies_and_says_why(self):
        r = self.send_and_wait({"type": "fileGitLink", "path": os.path.join(self.tmp, "loose.txt"),
                                "reqId": 7})
        self.assertEqual(r["url"], "", "an empty url is the verdict, never a dropped reply")
        self.assertEqual(r["reason"], "not committed (untracked file)",
                         "the reason rides the reply — the viewer shows it instead of hiding the button")

    def test_a_branch_not_on_origin_keeps_the_url_and_carries_the_note(self):
        _git("checkout", "-q", "-b", "wip", cwd=self.tmp)
        r = self.send_and_wait({"type": "fileGitLink", "path": self.fp, "reqId": 8})
        self.assertEqual(r["url"], "https://github.com/TESTORG/notes-api/blob/wip/src/app.py")
        self.assertEqual(r["reason"], "branch wip is not on origin")


class FixtureReposForbidBackgroundGitWork(_WithOrigin):
    """The kernel runs its own git against these repos (rev-parse, ls-files, ls-remote through the stand-in
    ssh into the bare origin), so the no-background keys must sit in each repo's LOCAL config, not only ride
    the fixture runner's -c flags."""

    def test_the_fixture_repos_forbid_background_git_work(self):
        for repo in (self.tmp, os.path.join(self.root, "TESTORG", "notes-api.git")):
            self.assertEqual(git(repo, "config", "--local", "--get", "maintenance.auto").stdout.strip(),
                             "false", repo)


if __name__ == "__main__":
    unittest.main()
