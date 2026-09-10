#!/usr/bin/env python3
"""T276 (the user 2026-09-08, from the devbox CPU hunt): when a kernel restart cuts a session's turn, that
turn's Bash-tool processes must die with it. The boot reaper used to SIGTERM the orphaned CLI pid alone, so
a tool shell's setsid children (a stress harness's 32 busy loops, a benchmark's 11) survived, re-parented
to the user manager once their shells died, and burned cores for over an hour.

Now the reaper ends the WHOLE tree before the resumed CLI launches:
  * the session's transient scope unit when the CLI runs in one (bin/romp-cli-scope's
    `romp-session-<sid8>-<pid>-<t>.scope`): systemd ends every process in the cgroup, setsid children and
    re-parented leftovers included — read from the CLI's own /proc/<pid>/cgroup;
  * always, the process tree the `ps` listing still shows under the CLI: each descendant's process group
    where it leads one (a setsid child), else the process; children before the CLI, SIGTERM then SIGKILL;
  * a sweep of OUR sessions' scopes whose CLI is not a live child of this kernel — a scope outlives its
    CLI when the tool's children keep running, which is exactly the loops the pid-only reap left behind.
Nothing outside the CLI's scope or tree is ever signaled: the walk is by ppid from the CLI, this kernel's
own group is never a target, and the scope sweep is filtered to our sids and skips this kernel's live
children.

Deterministic where it can be (pure helpers; scripted `run`/`kill`/`cgroup` seams), plus REAL processes on
Linux: a fake "CLI" whose child spawns a setsid'd sleep loop, and a bystander sleep outside the tree. The
loops here sleep — no synthetic load — and every process this file starts is killed by it. Synthetic
fixtures only.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the load
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"
sb = load_source("romp_sdk_backend_treekill", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-aaaa-0000-0000-00000000c276"
OTHER = "22222222-bbbb-0000-0000-00000000c276"
LINUX = sys.platform.startswith("linux") and shutil.which("ps") is not None and os.path.isdir("/proc")
# Fake pids sit ABOVE pid_max, so no real process can ever wear one: the reaper's liveness poll (procfs) then
# reads them as gone after the recorded SIGTERM, and never escalates to SIGKILL. A fake pid that happened to be a
# live process on the box (4242 on a CI runner, 2026-09-09) made the recorded kills differ run to run.
def _pid_max() -> int:
    try:
        return int(open("/proc/sys/kernel/pid_max").read().strip())
    except (OSError, ValueError):
        return 4194304
P = _pid_max()
CLI, TOOL, LOOP, BYSTANDER, MANAGER, KERNEL, TMUX, LIVE = (P + 42, P + 50, P + 60, P + 300, P + 901, P + 90210, P + 556, P + 557)


def _backend(d=None):
    return sb.SdkBackend(d or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)


def _reg(d, sid, **extra):
    r = {"sid": sid, "name": "s-" + sid[:4], "cwd": "/tmp", "alive": True, "lastSid": sid}
    r.update(extra)
    sb.write_reg(Path(d), sid, r)
    return r


class PureRules(unittest.TestCase):
    def test_scope_unit_of_reads_the_session_scope_from_a_cgroup_listing(self):
        v2 = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/romp-session-11111111-4242-1757374800.scope\n"
        self.assertEqual(sb.scope_unit_of(v2), "romp-session-11111111-4242-1757374800.scope")
        legacy = ("12:memory:/user.slice/romp-session-11111111-4242-1757374800.scope\n"
                  "1:name=systemd:/user.slice/romp-session-11111111-4242-1757374800.scope\n")
        self.assertEqual(sb.scope_unit_of(legacy), "romp-session-11111111-4242-1757374800.scope")
        self.assertIsNone(sb.scope_unit_of("0::/user.slice/user-1000.slice/user@1000.service/romp-manager.service\n"),
                          "the service cgroup is not a session scope — a CLI there has no unit of its own to stop")
        self.assertIsNone(sb.scope_unit_of(""))

    def test_scope_pid_and_our_sessions_units(self):
        self.assertEqual(sb.scope_pid("romp-session-11111111-4242-1757374800.scope"), 4242)
        self.assertIsNone(sb.scope_pid("romp-manager.service"))
        self.assertIsNone(sb.scope_pid("romp-session-garbage.scope"))
        listing = ("romp-session-11111111-4242-1757374800.scope loaded active running /x/claude\n"
                   "romp-session-22222222-4343-1757374801.scope loaded active running /x/claude\n"
                   "romp-session-11111111-4444-1757374802.scope loaded inactive dead /x/claude\n"
                   "run-r9b2.scope loaded active running something else\n")
        self.assertEqual(sb.session_scope_units(listing.splitlines(), [SID]),
                         ["romp-session-11111111-4242-1757374800.scope", "romp-session-11111111-4444-1757374802.scope"],
                         "our sid's units only, in listing order — another session's scope is never ours to stop")
        self.assertEqual(sb.session_scope_units(listing.splitlines(), [OTHER]), ["romp-session-22222222-4343-1757374801.scope"])
        self.assertEqual(sb.session_scope_units(listing.splitlines(), []), [])

    def test_descendants_walk_the_ppid_chain_and_nothing_else(self):
        ps = ("  100 1 /usr/lib/systemd/systemd --user\n"
              "  200 100 /x/claude --input-format stream-json --resume %s\n"
              "  201 200 bash -c tool\n"
              "  202 201 bash -c 'while :; do sleep 1; done' loop\n"     # setsid'd: still a child by ppid
              "  300 100 sleep 300\n"                                      # a bystander under the same manager
              "  400 1 python3 unrelated\n") % SID
        self.assertEqual(sorted(sb.descendants(ps.splitlines(), 200)), [201, 202])
        self.assertEqual(sb.descendants(ps.splitlines(), 300), [])
        self.assertEqual(sb.descendants(ps.splitlines(), 999), [], "an unknown root has no tree")
        # a leftover whose shell died has re-parented (ppid 100) and is NOT in the CLI's tree — the scope covers it
        ps2 = ps + "  203 100 bash -c 'while :; do sleep 1; done' orphaned-loop\n"
        self.assertNotIn(203, sb.descendants(ps2.splitlines(), 200))


class ScopePath(unittest.TestCase):
    def test_a_cli_in_a_session_scope_has_its_unit_stopped_and_only_its_own_pid_signaled(self):
        be = _backend()
        runs, killed = [], []
        def run(argv, **kw):
            runs.append(list(argv)); return mock.Mock(stdout="", returncode=0)
        unit = "romp-session-11111111-%d-1757374800.scope" % CLI
        cg = lambda pid: "0::/user.slice/user-1000.slice/user@1000.service/app.slice/%s\n" % unit
        ps = ("  %d %d /x/claude --input-format stream-json --resume %s\n"
              "  %d %d sleep 300\n") % (CLI, MANAGER, SID, BYSTANDER, MANAGER)
        out = be._end_cli_tree(CLI, ps.splitlines(), kill=lambda p, s: killed.append((p, s)), run=run, cgroup=cg)
        self.assertEqual(runs, [["systemctl", "--user", "stop", unit]],
                         "the scope is stopped — systemd ends the cgroup, re-parented leftovers included")
        self.assertEqual(killed, [(CLI, signal.SIGTERM)], "the tree walk still signals the CLI (belt and braces), nothing else")
        self.assertEqual(out["scope"], unit)
        self.assertEqual(out["tree"], 0)

    def test_an_inherited_scope_is_never_stopped_only_the_clis_own(self):
        # the lean review of T276 (2026-09-09): the real-process test's fake CLI sat in the cgroup of the romp
        # session running the tests, and the reaper stopped THAT session's scope. A scope is the CLI's own
        # only when the unit name carries the CLI's pid (bin/romp-cli-scope names it so and execs into it).
        be = _backend()
        runs, killed = [], []
        inherited = "romp-session-49985d8b-%d-1757374700.scope" % (CLI + 7)   # another pid: not this CLI's scope
        cg = lambda pid: "0::/user.slice/user-1000.slice/user@1000.service/app.slice/%s\n" % inherited
        ps = "  %d %d /x/claude --input-format stream-json --resume %s\n" % (CLI, MANAGER, SID)
        out = be._end_cli_tree(CLI, ps.splitlines(), kill=lambda p, s: killed.append((p, s)),
                               run=lambda *a, **k: runs.append(list(a[0])), cgroup=cg)
        self.assertEqual(runs, [], "no systemctl: the unit is not this CLI's, so stopping it would end someone else's session")
        self.assertEqual(killed, [(CLI, signal.SIGTERM)], "the tree walk alone")
        self.assertIsNone(out["scope"])

    def test_a_failed_stop_is_not_reported_as_stopped(self):
        be = _backend()
        unit = "romp-session-11111111-%d-1757374800.scope" % CLI
        cg = lambda pid: "0::/user.slice/user-1000.slice/user@1000.service/app.slice/%s\n" % unit
        run = lambda argv, **kw: mock.Mock(stdout="", stderr="Failed to stop: Unit not loaded.", returncode=5)
        out = be._end_cli_tree(CLI, ("  %d 1 /x/claude --input-format stream-json --resume %s\n" % (CLI, SID)).splitlines(),
                               kill=lambda p, s: None, run=run, cgroup=cg)
        self.assertIsNone(out["scope"], "a nonzero systemctl is not a stopped scope; the tree walk still ran")

    def test_a_cli_with_no_scope_falls_back_to_the_tree_children_first(self):
        be = _backend()
        runs, killed = [], []
        ps = ("  %d %d /x/claude --input-format stream-json --resume %s\n"
              "  %d %d bash -c tool\n"
              "  %d %d sleep 300\n"
              "  %d %d sleep 300\n") % (CLI, MANAGER, SID, TOOL, CLI, LOOP, TOOL, BYSTANDER, MANAGER)
        out = be._end_cli_tree(CLI, ps.splitlines(), kill=lambda p, s: killed.append((p, s)),
                               run=lambda *a, **k: runs.append(a), cgroup=lambda pid: "0::/user.slice/user-1000.slice/user@1000.service/romp-manager.service\n")
        self.assertEqual(runs, [], "no scope → no systemctl")
        self.assertEqual([p for p, _ in killed], [TOOL, LOOP, CLI], "descendants (parents first) then the CLI; the bystander untouched")
        self.assertTrue(all(s == signal.SIGTERM for _, s in killed), "fake pids are gone at once, so no SIGKILL escalation")
        self.assertIsNone(out["scope"])
        self.assertEqual(out["tree"], 2)

    def test_a_survivor_of_the_grace_gets_sigkill_on_a_fake_clock(self):
        # The escalation, pinned with no real process, pid or second behind it (T276c): the liveness poll,
        # the grace sleep and the clock are seams. The loop ignores its SIGTERM; the clock steps past the
        # grace in recorded sleeps; exactly the survivor is SIGKILLed, once, after every SIGTERM.
        be = _backend()
        killed, slept, clock = [], [], [0.0]
        ps = ("  %d %d /x/claude --input-format stream-json --resume %s\n"
              "  %d %d bash -c tool\n"
              "  %d %d sleep 300\n") % (CLI, MANAGER, SID, TOOL, CLI, LOOP, TOOL)
        def alive(p):                    # the loop outlives its SIGTERM until its SIGKILL is recorded
            return p == LOOP and (LOOP, signal.SIGKILL) not in killed
        def sleep(s):
            slept.append(s); clock[0] += s
        out = be._end_cli_tree(CLI, ps.splitlines(), kill=lambda p, s: killed.append((p, s)),
                               killpg=lambda g, s: killed.append(("pg", g, s)),   # a group kill would show up, not fire
                               run=lambda *a, **k: None, cgroup=lambda pid: "",
                               alive=alive, sleep=sleep, now=lambda: clock[0])
        self.assertEqual(killed, [(TOOL, signal.SIGTERM), (LOOP, signal.SIGTERM), (CLI, signal.SIGTERM),
                                  (LOOP, signal.SIGKILL)],
                         "SIGTERM to the tree children-first then the CLI; SIGKILL to the one survivor only")
        self.assertTrue(slept and all(s == 0.05 for s in slept), "the grace waits in short recorded sleeps")
        self.assertGreaterEqual(clock[0], sb.TREE_KILL_GRACE, "…until the fake clock passes the grace")
        self.assertEqual((out["signaled"], out["forced"], out["tree"]), (3, 1, 2))

    def test_the_leftover_scope_sweep_stops_our_dead_sessions_scopes_only(self):
        be = _backend()
        # a real child of THIS process stands in for "this kernel's live session": its unit is skipped
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(child.wait, timeout=10); self.addCleanup(child.kill)
        listing = ("romp-session-11111111-%d-1757374800.scope loaded active running claude\n"          # our sid, CLI gone → stop
                   "romp-session-11111111-%d-1757374801.scope loaded active running claude\n"          # our sid, our live child → keep
                   "romp-session-22222222-%d-1757374802.scope loaded active running claude\n"          # another session → never ours
                   ) % (CLI, child.pid, CLI + 1)
        runs = []
        def run(argv, **kw):
            runs.append(list(argv)); return mock.Mock(stdout=listing if argv == sb.SCOPE_LIST_ARGV else "", returncode=0)
        n = be._stop_leftover_scopes([SID], run=run)
        self.assertEqual(n, 1)
        self.assertEqual(runs, [sb.SCOPE_LIST_ARGV, ["systemctl", "--user", "stop", "romp-session-11111111-%d-1757374800.scope" % CLI]])

    def test_no_systemctl_means_nothing_to_sweep(self):
        be = _backend()
        def run(argv, **kw): raise FileNotFoundError("systemctl")
        self.assertEqual(be._stop_leftover_scopes([SID], run=run), 0)


class BootReconcileEndsTheTree(unittest.TestCase):
    def test_the_reap_signals_the_orphans_tree_and_sweeps_scopes_after_the_ps_read(self):
        d = tempfile.mkdtemp(); be = _backend(d)
        _reg(d, SID)
        sb.append_state(Path(d), SID, "working")
        ps = ("  %d 1 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              "  %d %d bash -c tool\n"
              "  %d %d sleep 300\n"
              "  %d 1 claude --resume %s --name termsess\n"
              "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel\n"
              "  %d %d /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              ) % (CLI, SID, TOOL, CLI, LOOP, TOOL, TMUX, SID, KERNEL, LIVE, KERNEL, SID)
        listing = "romp-session-11111111-%d-1757374800.scope loaded active running claude\n" % CLI
        killed, runs = [], []
        def run(argv, **kw):
            runs.append(list(argv)); return mock.Mock(stdout=ps if argv == sb.PS_ARGV else (listing if argv == sb.SCOPE_LIST_ARGV else ""), returncode=0)
        with mock.patch.object(sb.subprocess, "run", side_effect=run), \
             mock.patch.object(sb.os, "kill", side_effect=lambda p, s: killed.append((p, s))), \
             mock.patch.object(sb.SdkBackend, "_pid_alive", lambda self, p: False):   # fake pids read as gone on every platform (no /proc on macOS → os.kill(pid, 0) would be this mock)
            be._boot_reconcile([sb.read_reg(Path(d), SID)])
        self.assertEqual([p for p, _ in killed], [TOOL, LOOP, CLI], "the orphan's tree, children first, then the CLI; the tmux CLI and the live CLI untouched")
        self.assertEqual(runs[0], sb.PS_ARGV, "the listing is read first, with PS_ARGV")
        self.assertIn(sb.SCOPE_LIST_ARGV, runs, "…then our sessions' scopes are listed")
        self.assertIn(["systemctl", "--user", "stop", "romp-session-11111111-%d-1757374800.scope" % CLI], runs,
                      "the dead CLI's scope is stopped: its children live on in the cgroup even after the CLI is gone")


@unittest.skipUnless(LINUX, "real processes on Linux with procfs")
class RealProcessTree(unittest.TestCase):
    """The fixture the dispatch asked for: a 'session' (a fake CLI shell) whose child spawns a DETACHED sleep loop;
    after the simulated cut — the reaper ending the orphaned CLI's tree — the loop is gone, and a bystander outside
    the tree is untouched. The cgroup seam is pinned EMPTY: the fake CLI inherits the cgroup of whatever runs
    the tests (a romp session's own scope, when run from one — ROMP_CLI_SCOPE=0 only governs spawned CLIs), and
    the reaper's own pid check would already refuse that unit, but this class is about the process-group
    fallback, so it never consults the real cgroup at all."""

    def _tree(self):
        marker = "romp-t276-loop-" + uuid.uuid4().hex
        # the "CLI": a shell that starts a setsid'd loop (its own session + process group, like a tool's nohup child)
        # and then sits like a CLI mid-turn; the loop sleeps — no load
        cli = subprocess.Popen(["bash", "-c", 'setsid bash -c "while :; do sleep 0.2; done" "$0" & wait', marker],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self._kill_marker, marker)
        self.addCleanup(lambda: (cli.poll() is None) and os.killpg(cli.pid, signal.SIGKILL))
        # a bystander outside the tree
        by = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(by.wait, timeout=10); self.addCleanup(by.kill)
        # Wait for the LOOP itself, not for any process wearing the marker: the fake CLI's own argv carries it too,
        # so a poll on _marker_pids alone was satisfied the instant the shell existed, before its setsid'd child
        # had started; on a slow runner the descendant check below then found no loop (T276d, CI 3.13 once).
        deadline = time.time() + 5
        while time.time() < deadline and not self._loop_pids(marker, cli.pid):
            time.sleep(0.05)
        self.assertTrue(self._loop_pids(marker, cli.pid), "the detached loop itself is running before the cut")
        return cli, by, marker

    @staticmethod
    def _marker_pids(marker):
        out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
        return [int(x) for x in out if x.isdigit() and int(x) != os.getpid()]

    def _loop_pids(self, marker, cli_pid):
        """The setsid'd loop's pid(s): every process wearing the marker except the fake CLI shell (whose argv carries
        it too). pgrep reads /proc/<pid>/cmdline, which a killed-but-unreaped process no longer has, so a pid listed
        here is a live loop, never a zombie."""
        return [p for p in self._marker_pids(marker) if p != cli_pid]

    def _kill_marker(self, marker):
        for p in self._marker_pids(marker):
            try: os.kill(p, signal.SIGKILL)
            except ProcessLookupError: pass

    def test_ending_the_cut_clis_tree_kills_the_detached_loop_and_spares_the_bystander(self):
        cli, by, marker = self._tree()
        be = _backend()
        ps = subprocess.run(sb.PS_ARGV, capture_output=True, text=True, timeout=10).stdout.splitlines()
        loop_pids = self._loop_pids(marker, cli.pid)
        self.assertTrue(loop_pids and all(p in sb.descendants(ps, cli.pid) for p in loop_pids),
                        "the setsid'd loop is still the CLI's descendant by ppid: %r vs %r" % (loop_pids, sb.descendants(ps, cli.pid)))
        out = be._end_cli_tree(cli.pid, ps, cgroup=lambda p: "")
        # Bounded poll for the loop to be GONE (the reaper's own poll shape: liveness, up to a few seconds): the
        # SIGKILL has been sent when _end_cli_tree returns, but a slow runner may not have taken the process off
        # the table yet. The bystander check stays immediate and strict.
        deadline = time.time() + 5
        while time.time() < deadline and (self._loop_pids(marker, cli.pid) or cli.poll() is None):
            time.sleep(0.05)
        self.assertEqual(self._loop_pids(marker, cli.pid), [], "the detached loop died with the cut turn")
        self.assertIsNotNone(cli.poll(), "the CLI is gone")
        self.assertIsNone(by.poll(), "the bystander outside the tree was never signaled")
        self.assertGreaterEqual(out["tree"], 1)


if __name__ == "__main__":
    unittest.main()
