"""Dead-owner sweep of the suite's `romp-tests-*` temp roots (2026-09-10).

tests/conftest.py mints one private temp root per run and removes it at run end, but a run that dies
without reaching that removal — pytest-timeout's os._exit, a kernel restart cutting the tool shell,
the cut-turn reaper's kill — leaves the whole root standing. On a shared machine those roots piled
into millions of files, and the next boot's /tmp cleanup spent 39 minutes deleting them while ssh
and every service waited behind it. Nothing in the dead run can clean up, so two pieces outside it
do: conftest writes an OWNER MARKER (the run's pid) into the root at mint time, and the kernel's boot
reconcile sweeps roots under the system temp dir whose marker names a dead pid.

Pinned: a root whose owner is dead goes; a root whose owner is alive stays (this process is the
owner); a root with no marker, an unreadable marker or a foreign name stays — refusing is the safe
direction; the running suite's own root carries a marker naming this process; the marker file name
agrees between conftest and the kernel; and _boot_reconcile calls the sweep. Everything is built
under this test's own temp dir (itself inside the run's root), never in the real system temp dir.
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the load
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"
sb = load_source("romp_sdk_backend_rootsweep", os.path.join(BIN, "romp_sdk_backend.py"))

MARKER = sb.TEST_ROOT_OWNER_MARKER


def _dead_pid() -> int:
    """A pid that certainly belonged to a process and is dead now: a child that has exited and been
    reaped. Reuse within the test's lifetime is not a concern the sweep must defend against here."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _root(tmpdir: str, name: str, marker=None, raw: str | None = None) -> str:
    d = os.path.join(tmpdir, name)
    os.makedirs(os.path.join(d, "deep", "er"))
    with open(os.path.join(d, "deep", "er", "file.txt"), "w") as fh:
        fh.write("x")
    if raw is not None:
        with open(os.path.join(d, MARKER), "w") as fh:
            fh.write(raw)
    elif marker is not None:
        with open(os.path.join(d, MARKER), "w") as fh:
            json.dump(marker, fh)
    return d


class DeadOwnerSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sweep-arena-")   # inside the run's root; the hook removes it

    def test_dead_owner_root_is_removed_and_counted(self):
        dead = _root(self.tmp, "romp-tests-dead1", {"pid": _dead_pid(), "started": 1.0})
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp), 1)
        self.assertFalse(os.path.exists(dead))

    def test_live_owner_root_stays(self):
        live = _root(self.tmp, "romp-tests-live", {"pid": os.getpid(), "started": 1.0})
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp), 0)
        self.assertTrue(os.path.isdir(live))

    def test_unmarked_unreadable_and_foreign_roots_stay(self):
        kept = [
            _root(self.tmp, "romp-tests-nomarker"),                                 # pre-marker or foreign
            _root(self.tmp, "romp-tests-badjson", raw="{not json"),                 # unreadable
            _root(self.tmp, "romp-tests-nopid", {"started": 1.0}),                  # marker without a pid
            _root(self.tmp, "romp-tests-strpid", {"pid": "abc"}),                   # pid not an int
            _root(self.tmp, "other-prefix-x", {"pid": _dead_pid()}),                # not the suite's prefix
        ]
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp), 0)
        for d in kept:
            self.assertTrue(os.path.isdir(d), d)

    def test_mixed_arena_removes_only_the_dead_ones(self):
        dead_a = _root(self.tmp, "romp-tests-a", {"pid": _dead_pid()})
        dead_b = _root(self.tmp, "romp-tests-state-b", {"pid": _dead_pid()})       # the package state dir shape
        live = _root(self.tmp, "romp-tests-c", {"pid": os.getpid()})
        bare = _root(self.tmp, "romp-tests-d")
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp), 2)
        self.assertFalse(os.path.exists(dead_a))
        self.assertFalse(os.path.exists(dead_b))
        self.assertTrue(os.path.isdir(live))
        self.assertTrue(os.path.isdir(bare))

    def test_symlink_named_like_a_root_is_not_followed(self):
        target = _root(self.tmp, "keep-me", {"pid": _dead_pid()})
        os.symlink(target, os.path.join(self.tmp, "romp-tests-link"))
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp), 0)
        self.assertTrue(os.path.isdir(target))

    def test_missing_tmpdir_is_a_no_op(self):
        self.assertEqual(sb.sweep_dead_test_roots(os.path.join(self.tmp, "absent")), 0)


@unittest.skipUnless(os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR"), "conftest not loaded (bare unittest run)")
class RunningSuiteIsMarked(unittest.TestCase):
    def test_this_runs_root_carries_a_marker_naming_this_process(self):
        # Under xdist each worker minted its own root (it imported conftest), so TMPDIR is this
        # process's root either way, and the marker's pid is ours.
        root = os.environ["TMPDIR"]
        self.assertTrue(os.path.basename(root).startswith(sb.TEST_ROOT_PREFIX), root)
        with open(os.path.join(root, MARKER)) as fh:
            m = json.load(fh)
        self.assertEqual(m["pid"], os.getpid())
        self.assertGreater(m["started"], 0)
        # ...and so the sweep, run over the dir that holds our root, leaves it alone.
        parent = os.path.dirname(root)
        before = set(os.listdir(parent))
        sb.sweep_dead_test_roots(parent)
        self.assertTrue(os.path.isdir(root))
        self.assertTrue(os.path.basename(root) in os.listdir(parent))
        self.assertLessEqual(set(os.listdir(parent)), before)   # it only ever removes, never adds

    def test_marker_name_agrees_with_conftest(self):
        conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
        self.assertIsNotNone(conftest, "the loaded tests/conftest.py module")
        self.assertEqual(conftest.TEST_ROOT_OWNER_MARKER, sb.TEST_ROOT_OWNER_MARKER)


class BootReconcileCallsIt(unittest.TestCase):
    def test_boot_reconcile_sweeps_the_system_temp_dir(self):
        src = inspect.getsource(sb.SdkBackend._boot_reconcile)
        self.assertIn("sweep_dead_test_roots(tempfile.gettempdir()", src)
        self.assertIn("swept %d dead test root(s)", src)


if __name__ == "__main__":
    unittest.main()
