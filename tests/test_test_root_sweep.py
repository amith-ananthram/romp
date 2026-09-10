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
import time
import unittest
from unittest import mock

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

    def test_a_stubborn_child_is_chmoded_and_removed(self):
        # A 000-mode directory inside the root (suite tests make one and restore it only in a
        # finally that os._exit skips): rmtree fails on it once, the sweep restores owner rwx and
        # retries, and the whole root goes.
        dead = _root(self.tmp, "romp-tests-stubborn", {"pid": _dead_pid()})
        locked = os.path.join(dead, "deep", "locked")
        os.makedirs(locked)
        with open(os.path.join(locked, "inner.txt"), "w") as fh:
            fh.write("x")
        os.chmod(locked, 0o000)
        try:
            self.assertEqual(sb.sweep_dead_test_roots(self.tmp), 1)
        finally:
            if os.path.isdir(locked):
                os.chmod(locked, 0o700)
        self.assertFalse(os.path.exists(dead))

    def test_a_leftover_tombstone_is_removed_without_a_marker(self):
        # A previous boot renamed the root and died before finishing: the tombstone is ours by name.
        tomb = _root(self.tmp, "romp-tests-old" + sb.TEST_ROOT_TOMBSTONE)
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp), 1)
        self.assertFalse(os.path.exists(tomb))

    def test_rename_first_so_a_failed_delete_leaves_a_tombstone_not_a_markerless_root(self):
        dead = _root(self.tmp, "romp-tests-fails", {"pid": _dead_pid()})
        logs = []
        with mock.patch.object(sb, "_rmtree_stubborn", side_effect=OSError("still busy")):
            self.assertEqual(sb.sweep_dead_test_roots(self.tmp, log=logs.append), 0)
        self.assertFalse(os.path.exists(dead), "the root was claimed by rename before the delete ran")
        tomb = dead + sb.TEST_ROOT_TOMBSTONE
        self.assertTrue(os.path.isdir(tomb))
        self.assertTrue(any("not removed" in l and tomb in l for l in logs), logs)
        # ...and the next sweep finishes it, marker or no marker, and says nothing.
        logs.clear()
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp, log=logs.append), 1)
        self.assertFalse(os.path.exists(tomb))
        self.assertEqual(logs, [])

    def test_the_per_boot_budget_leaves_the_rest_for_the_next_boot_and_says_so(self):
        for i in range(3):
            _root(self.tmp, "romp-tests-b%d" % i, {"pid": _dead_pid()})
        logs = []
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp, log=logs.append, budget_s=0), 0)
        self.assertEqual(sum(1 for n in os.listdir(self.tmp) if n.startswith("romp-tests-b")), 3)
        self.assertTrue(any("budget" in l and "3 dead root(s) left" in l for l in logs), logs)
        self.assertEqual(sb.sweep_dead_test_roots(self.tmp, log=logs.append), 3)


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
        # ...which is the pid the sweep would test, and it is alive, so a sweep would keep this root
        # (test_live_owner_root_stays pins that in a private arena; the real temp dir is never swept here).
        self.assertTrue(sb._pid_alive(m["pid"]))

    def test_marker_name_agrees_with_conftest(self):
        conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
        self.assertIsNotNone(conftest, "the loaded tests/conftest.py module")
        self.assertEqual(conftest.TEST_ROOT_OWNER_MARKER, sb.TEST_ROOT_OWNER_MARKER)


class BootReconcileCallsIt(unittest.TestCase):
    def test_boot_reconcile_starts_the_sweep_last_and_off_thread(self):
        # Order pinned in the source: the sweep starts AFTER the resume loop (the reap and the cut
        # sessions' queue re-delivery must never wait behind minutes of rmtree), and on a daemon
        # thread of its own, over the system temp dir, with the per-boot budget default.
        src = inspect.getsource(sb.SdkBackend._boot_reconcile)
        self.assertIn("self._start_test_root_sweep()", src)
        self.assertGreater(src.index("self._start_test_root_sweep()"), src.index("for sid in to_start"))
        starter = inspect.getsource(sb.SdkBackend._start_test_root_sweep)
        self.assertIn("sweep_dead_test_roots(tempfile.gettempdir()", starter)
        self.assertIn("daemon=True", starter)

    def test_the_starter_runs_the_sweep_on_a_thread_and_logs_the_count(self):
        arena = tempfile.mkdtemp(prefix="sweep-arena-")
        _root(arena, "romp-tests-t", {"pid": _dead_pid()})
        logs = []
        be = mock.Mock(spec=[])
        be._log = logs.append
        with mock.patch.object(sb.tempfile, "gettempdir", return_value=arena):
            sb.SdkBackend._start_test_root_sweep(be)
            deadline = time.monotonic() + 5
            while os.path.exists(os.path.join(arena, "romp-tests-t")) and time.monotonic() < deadline:  # loop-ok: bounded test wait
                time.sleep(0.02)
        self.assertFalse(os.path.exists(os.path.join(arena, "romp-tests-t")))
        deadline = time.monotonic() + 2
        while not logs and time.monotonic() < deadline:  # loop-ok: bounded test wait
            time.sleep(0.02)
        self.assertTrue(any("swept 1 dead test root(s)" in l for l in logs), logs)


if __name__ == "__main__":
    unittest.main()
