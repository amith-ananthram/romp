#!/usr/bin/env python3
"""tests/git_fixture.py, the one git runner for throwaway-repo fixtures (T299): every invocation forbids
background work, init_repo and forbid_background write the same keys into the repo, and a commit or a
fetch through the runner spawns no maintenance or gc child, read off git's own trace. Synthetic content;
hermetic temp dirs; this module loads no romp code."""
import calendar
import os
import subprocess
import tempfile
import unittest

from git_fixture import GIT_C, GIT_NO_BACKGROUND, forbid_background, git, init_repo

IDENT = ("t@TESTHOST", "t")


def _traced(fn):
    """Run fn(env) with GIT_TRACE pointed at a fresh file; return the trace text."""
    with tempfile.NamedTemporaryFile("r", suffix=".log") as trace:
        fn(dict(os.environ, GIT_TRACE=trace.name))
        return trace.read()


class Keys(unittest.TestCase):
    def test_the_keys_forbid_auto_maintenance_gc_their_detach_and_the_fsmonitor_daemon(self):
        self.assertEqual(GIT_NO_BACKGROUND, {"maintenance.auto": "false", "maintenance.autoDetach": "false",
                                             "gc.auto": "0", "gc.autoDetach": "false", "core.fsmonitor": "false"})
        self.assertEqual(GIT_C, [x for k, v in GIT_NO_BACKGROUND.items() for x in ("-c", "%s=%s" % (k, v))])

    def test_every_invocation_carries_the_keys_even_against_a_repo_that_lacks_them(self):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["git", "init", "-q", td], check=True, capture_output=True)   # a plain init: no local keys
            for k, v in GIT_NO_BACKGROUND.items():
                self.assertEqual(git(td, "config", "--get", k).stdout.strip(), v, k + " rides the command line")
                self.assertNotEqual(git(td, "config", "--local", "--get", k, check=False).returncode, 0, "…and only there")


class Runner(unittest.TestCase):
    def test_a_failure_raises_calledprocesserror_with_the_captured_streams_and_check_false_returns_it(self):
        with tempfile.TemporaryDirectory() as td:
            init_repo(td, "-q")
            with self.assertRaises(subprocess.CalledProcessError) as cm:
                git(td, "rev-parse", "--verify", "no-such-ref")
            self.assertEqual(cm.exception.returncode, 128)
            self.assertIn("fatal", cm.exception.stderr)
            r = git(td, "rev-parse", "--verify", "no-such-ref", check=False)
            self.assertEqual(r.returncode, 128)
            self.assertIsInstance(git(td, "rev-parse", "--git-dir").stdout, str)
            self.assertIsInstance(git(td, "rev-parse", "--git-dir", text=False).stdout, bytes)

    def test_the_identity_env_and_init_args_pass_through(self):
        with tempfile.TemporaryDirectory() as td:
            init_repo(td, "-q", "-b", "main", ident=IDENT)
            self.assertEqual(git(td, "symbolic-ref", "--short", "HEAD").stdout.strip(), "main", "init args reach git init")
            self.assertEqual(git(td, "config", "--local", "--get", "user.email").stdout.strip(), IDENT[0], "the identity lands in the repo")
            open(os.path.join(td, "a"), "w").write("a\n")
            git(td, "add", "a")
            git(td, "commit", "-qm", "one")
            # a per-call identity rides the command line as -c flags (read back through config: under the
            # suite the conftest floor sets the author through GIT_AUTHOR_* variables, which git ranks above
            # every config, so the commit's author cannot show which config won)
            r = git(td, "config", "--get", "user.name", ident={"user.email": "other@TESTHOST", "user.name": "other"})
            self.assertEqual(r.stdout.strip(), "other", "a per-call identity (a mapping) reaches git")
            self.assertEqual(git(td, "config", "--get", "user.name", ident=("pair@TESTHOST", "pair")).stdout.strip(), "pair", "…and a pair does")
            env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-02T03:04:05+0000", GIT_COMMITTER_DATE="2026-01-02T03:04:05+0000")
            git(td, "commit", "-qm", "two", "--allow-empty", env=env)
            # compared as epoch seconds: git spells a zero offset in %aI as "+00:00" or "Z" depending on its version
            self.assertEqual(git(td, "log", "-1", "--format=%at").stdout.strip(), str(calendar.timegm((2026, 1, 2, 3, 4, 5, 0, 0, 0))), "the env reaches git")


class NoBackgroundWork(unittest.TestCase):
    def test_init_repo_writes_the_keys_into_the_repo_and_a_commit_through_the_runner_spawns_no_child(self):
        with tempfile.TemporaryDirectory() as td:
            init_repo(td, "-q", ident=IDENT)
            for k, v in GIT_NO_BACKGROUND.items():
                self.assertEqual(git(td, "config", "--local", "--get", k).stdout.strip(), v, k)
            open(os.path.join(td, "a"), "w").write("a\n")
            git(td, "add", "a")
            t = _traced(lambda env: git(td, "commit", "-qm", "traced", env=env))
        self.assertIn("built-in: git commit", t, "the trace is on")
        self.assertNotIn("maintenance", t, "no auto-maintenance child:\n" + t)
        self.assertNotIn("run_command: git gc", t, "no gc child")

    def test_a_clone_lacks_the_keys_until_forbid_background_and_then_a_fetch_spawns_no_child(self):
        # the kernel's update paths fetch and merge into clones the fixture did not init: forbid_background
        # is what covers a git the kernel runs there, and `fetch` is one of the commands that runs auto
        # maintenance afterwards
        with tempfile.TemporaryDirectory() as td:
            src, clone = os.path.join(td, "src"), os.path.join(td, "clone")
            os.makedirs(src)
            init_repo(src, "-q", "-b", "main", ident=IDENT)
            git(src, "commit", "-qm", "seed", "--allow-empty")
            subprocess.run(["git", "clone", "-q", src, clone], check=True, capture_output=True)   # a plain clone, as the kernel's would be
            self.assertNotEqual(git(clone, "config", "--local", "--get", "maintenance.auto", check=False).returncode, 0, "a clone starts without the keys")
            forbid_background(clone)
            for k, v in GIT_NO_BACKGROUND.items():
                self.assertEqual(git(clone, "config", "--local", "--get", k).stdout.strip(), v, k)
            git(src, "commit", "-qm", "more", "--allow-empty")
            # a PLAIN git (no -c flags: the kernel's own subprocess) fetching in the clone, traced
            t = _traced(lambda env: subprocess.run(["git", "-C", clone, "fetch", "-q", "origin"], check=True, capture_output=True, env=env))
        self.assertIn("built-in: git fetch", t)
        self.assertNotIn("maintenance", t, "the repo's config alone keeps a plain git from spawning maintenance:\n" + t)
        self.assertNotIn("run_command: git gc", t)


if __name__ == "__main__":
    unittest.main()
