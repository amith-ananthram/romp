#!/usr/bin/env python3
"""The auto converge moves the local `main` branch, never just a detached HEAD (the user 2026-09-08).

_run_main_update("pull") used to `git checkout --detach <target>` and leave the local `main` branch where
it was, so a maintainer's box sat detached at a fresh sha while `git checkout main` landed on a pointer
months old. Now: when main is an ANCESTOR of the target it is moved onto the target and checked out (a
fast-forward by construction); when main has commits the target lacks it is left alone, the target is
checked out detached as before, and a sync notice says so; with no main branch at all the checkout stays
detached, as before. Real temporary repositories, no network: a bare "remote", a clone as the shared
checkout, a second clone to advance the remote's main.

Hermetic state, synthetic content; the restart leg is stopped before the manager POST."""
import os
import subprocess
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TD = tempfile.TemporaryDirectory()
_PREV_STATE_DIR = os.environ.get("ROMP_STATE_DIR")
os.environ["ROMP_STATE_DIR"] = _STATE_TD.name
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
os.environ["ROMP_MANAGER_PORT"] = "1"
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_converge_main", os.path.join(BIN, "romp-kernel"))
if _PREV_STATE_DIR is None:
    os.environ.pop("ROMP_STATE_DIR", None)
else:
    os.environ["ROMP_STATE_DIR"] = _PREV_STATE_DIR


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError("git %s failed: %s%s" % (" ".join(args), r.stdout, r.stderr))
    return r.stdout.strip()


def commit(cwd, name):
    Path(cwd, name).write_text(name + "\n")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", "add " + name)
    return git(cwd, "rev-parse", "--short=8", "HEAD")


class ConvergeMovesMain(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.remote = root / "remote.git"
        seed = root / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "main")
        self.base = commit(seed, "one")
        git(seed, "init", "-q", "--bare", "-b", "main", str(self.remote))   # HEAD -> main, so clones start on main
        git(seed, "push", "-q", str(self.remote), "main")
        self.checkout = root / "checkout"
        git(root, "clone", "-q", str(self.remote), str(self.checkout))
        # advance the remote's main from a second clone: the target the verdict advertises
        other = root / "other"
        git(root, "clone", "-q", str(self.remote), str(other))
        commit(other, "two")
        self.target = commit(other, "three")
        git(other, "push", "-q", "origin", "main")
        self.saved_state = jd.STATE
        jd.STATE = root / "state"
        jd.STATE.mkdir()
        with km._SYNC_LOCK:
            del km._SYNC_NOTICES[:]
        km._MAIN_DRIFT[0] = km._MAIN_DRIFT[1] = ""

    def tearDown(self):
        jd.STATE = self.saved_state
        self.td.cleanup()

    def converge(self):
        with mock.patch.object(km, "ROOT", self.checkout), \
             mock.patch.object(km, "_release_remote", return_value="origin"), \
             mock.patch.object(km, "_kernel_code_changed", return_value=False), \
             mock.patch.object(km, "_in_place_converge", return_value=True):   # stop before the restart leg
            km._run_main_update("pull", target=self.target)

    def notices(self):
        with km._SYNC_LOCK:
            return list(km._SYNC_NOTICES)

    def head_branch(self):
        r = subprocess.run(["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=str(self.checkout),
                           capture_output=True, text=True)
        return r.stdout.strip()

    def test_main_that_is_an_ancestor_is_fast_forwarded_and_checked_out(self):
        # the maintainer's shape: an earlier converge left HEAD detached at an older sha; main sits
        # further back still, at the clone's first commit
        git(self.checkout, "checkout", "-q", "--detach", "HEAD")
        self.converge()
        self.assertEqual(self.head_branch(), "main", "on the branch, not a detached head")
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "main"), self.target)
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "HEAD"), self.target)
        self.assertEqual([n for n in self.notices() if not n["ok"]], [], "no refusal")
        self.assertEqual([n for n in self.notices() if "left where it is" in n["text"]], [])

    def test_main_already_checked_out_and_behind_moves_on_the_branch(self):
        self.assertEqual(self.head_branch(), "main")
        self.converge()
        self.assertEqual(self.head_branch(), "main")
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "HEAD"), self.target)

    def test_main_with_local_commits_is_left_alone_and_the_notice_says_so(self):
        # the branch has a commit the remote never saw, while HEAD sits detached at an earlier commit that
        # IS an ancestor of the target (an earlier converge left it there): the checkout may advance, the
        # branch may not
        local = commit(self.checkout, "mine")
        git(self.checkout, "checkout", "-q", "--detach", "main~1")
        self.converge()
        self.assertEqual(self.head_branch(), "", "detached at the target, as before")
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "HEAD"), self.target)
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "main"), local, "main untouched")
        said = [n for n in self.notices() if "left where it is" in n["text"]]
        self.assertEqual(len(said), 1, self.notices())
        self.assertTrue(said[0]["ok"], "the converge itself succeeded: an informational row, not a refusal")
        self.assertIn("origin/main", said[0]["text"])
        self.assertIn(self.target, said[0]["text"])

    def test_no_main_branch_stays_detached_as_before(self):
        git(self.checkout, "checkout", "-q", "--detach", "HEAD")
        git(self.checkout, "branch", "-q", "-D", "main")
        self.converge()
        self.assertEqual(self.head_branch(), "")
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "HEAD"), self.target)
        self.assertEqual([n for n in self.notices() if "left where it is" in n["text"]], [])
        self.assertEqual([n for n in self.notices() if not n["ok"]], [])

    def test_a_diverged_checkout_still_refuses_before_any_branch_move(self):
        # HEAD itself has a commit the target lacks: the existing refusal, and main is not touched either
        local = commit(self.checkout, "mine")
        git(self.checkout, "checkout", "-q", "--detach", "HEAD")
        self.converge()
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "HEAD"), local)
        self.assertEqual(git(self.checkout, "rev-parse", "--short=8", "main"), local)
        self.assertTrue(any("not a fast-forward" in n["text"] and not n["ok"] for n in self.notices()))


if __name__ == "__main__":
    unittest.main()
