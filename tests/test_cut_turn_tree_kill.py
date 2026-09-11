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
import json
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
CLI, TOOL, LOOP, BYSTANDER, MANAGER, KERNEL, TERMINAL, LIVE = (P + 42, P + 50, P + 60, P + 300, P + 901, P + 90210, P + 556, P + 557)
# TERMINAL is someone's own claude in a terminal (no stream-json mark), never romp's: the reap must leave it alone


def _backend(d=None):
    d = d or tempfile.mkdtemp()
    # hosts OFF in this bare state dir (T348: on by default): the real _boot_reconcile below starts the sessions it
    # classes as cut, and with no file it would spawn a real bin/romp-session-host for each on a box with the SDK
    Path(d, "session-hosts").write_text("off")
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)


class _Sess:
    """The two attributes _session_cli_pid reads."""
    def __init__(self, sid):
        self.sid, self.name = sid, "s-" + sid[:4]


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
        # the session scopes first, then the per-session HOST scopes are listed too (T315; none here, so no stop)
        self.assertEqual(runs, [sb.SCOPE_LIST_ARGV, ["systemctl", "--user", "stop", "romp-session-11111111-%d-1757374800.scope" % CLI],
                                sb.HOST_SCOPE_LIST_ARGV])

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
              "  %d 1 claude --resume %s --name termsess\n"   # a terminal CLI: no stream-json mark, never romp's
              "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel\n"
              "  %d %d /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              ) % (CLI, SID, TOOL, CLI, LOOP, TOOL, TERMINAL, SID, KERNEL, LIVE, KERNEL, SID)
        listing = "romp-session-11111111-%d-1757374800.scope loaded active running claude\n" % CLI
        killed, runs = [], []
        def run(argv, **kw):
            runs.append(list(argv)); return mock.Mock(stdout=ps if argv == sb.PS_ARGV else (listing if argv == sb.SCOPE_LIST_ARGV else ""), returncode=0)
        with mock.patch.object(sb.subprocess, "run", side_effect=run), \
             mock.patch.object(sb.os, "kill", side_effect=lambda p, s: killed.append((p, s))), \
             mock.patch.object(sb.SdkBackend, "_pid_alive", lambda self, p: False):   # fake pids read as gone on every platform (no /proc on macOS → os.kill(pid, 0) would be this mock)
            be._boot_reconcile([sb.read_reg(Path(d), SID)])
        self.assertEqual([p for p, _ in killed], [TOOL, LOOP, CLI], "the orphan's tree, children first, then the CLI; the terminal CLI (no stream-json mark) and the live CLI untouched")
        self.assertEqual(runs[0], sb.PS_ARGV, "the listing is read first, with PS_ARGV")
        self.assertIn(sb.SCOPE_LIST_ARGV, runs, "…then our sessions' scopes are listed")
        self.assertIn(["systemctl", "--user", "stop", "romp-session-11111111-%d-1757374800.scope" % CLI], runs,
                      "the dead CLI's scope is stopped: its children live on in the cgroup even after the CLI is gone")

    def test_a_reparented_cli_with_a_valid_lease_survives_the_boot_and_a_stale_one_is_reaped(self):
        """Ownership by lease (T305): three orphan-SHAPED CLIs (parent pid 1, no kernel), each a different case.
        LEASED holds a valid lease (holder alive by pid and start time, fresh beat): it survives, its scope is
        left alone, its lease stays. STALE's lease names a holder that is gone (a crashed kernel): its tree is
        ended, its scope stopped, its lease dropped, and a holder-gone row is filed. A lease naming DEAD, a pid
        no process wears, is dropped with a no-live-process row. The start-time reader is the seam: fake pids
        above pid_max have no /proc entry, so identity comes from the fixture."""
        LEASED, STALE, DEAD, HOLDER = P + 70, P + 71, P + 72, P + 80
        d = tempfile.mkdtemp(); be = _backend(d)
        for sid in (SID, OTHER):
            _reg(d, sid)
        now = time.time()
        sb.write_lease(d, {"sid": SID, "fsid": SID, "pid": LEASED, "start": "1000", "holder": {"pid": HOLDER, "start": "500"},
                           "version": "", "t": now})
        sb.write_lease(d, {"sid": OTHER, "fsid": OTHER, "pid": STALE, "start": "1001", "holder": {"pid": P + 81, "start": "1"},
                           "version": "", "t": now})
        DEADSID = "33333333-cccc-0000-0000-00000000c305"
        _reg(d, DEADSID)
        sb.write_lease(d, {"sid": DEADSID, "fsid": DEADSID, "pid": DEAD, "start": "7", "holder": {"pid": HOLDER, "start": "500"},
                           "version": "", "t": now})
        starts = {LEASED: "1000", STALE: "1001", HOLDER: "500"}
        ps = ("  %d 1 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              "  %d 1 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              ) % (LEASED, SID, STALE, OTHER)
        listing = ("romp-session-%s-%d-1757374800.scope loaded active running claude\n"
                   "romp-session-%s-%d-1757374801.scope loaded active running claude\n") % (SID[:8], LEASED, OTHER[:8], STALE)
        killed, runs = [], []
        def run(argv, **kw):
            runs.append(list(argv)); return mock.Mock(stdout=ps if argv == sb.PS_ARGV else (listing if argv == sb.SCOPE_LIST_ARGV else ""), returncode=0)
        regs = [sb.read_reg(Path(d), s) for s in (SID, OTHER, DEADSID)]
        with mock.patch.object(sb.subprocess, "run", side_effect=run), \
             mock.patch.object(sb.os, "kill", side_effect=lambda p, s: killed.append((p, s))), \
             mock.patch.object(sb, "proc_start", lambda p, run=None: starts.get(p)), \
             mock.patch.object(sb.SdkBackend, "_pid_alive", lambda self, p: False):
            be._boot_reconcile(regs)
        self.assertEqual([p for p, _ in killed], [STALE], "only the CLI whose lease did not hold is signaled")
        stops = [a[-1] for a in runs if a[:3] == ["systemctl", "--user", "stop"]]
        self.assertEqual(stops, ["romp-session-%s-%d-1757374801.scope" % (OTHER[:8], STALE)], "the leased CLI's scope is spared")
        self.assertIsNotNone(sb.read_lease(d, SID), "the valid lease stays")
        self.assertIsNone(sb.read_lease(d, OTHER), "the lease that did not hold went with its CLI")
        self.assertIsNone(sb.read_lease(d, DEADSID), "a lease naming no live CLI is dropped")
        # the lease rows among the ledger's (the boot sweep files rows of its own there: reconcile.*)
        rows = [r for r in (json.loads(l) for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines())
                if r["kind"].startswith("lease.")]
        self.assertEqual(sorted(r["kind"] for r in rows), ["lease.holder-gone", "lease.no-live-process"])
        self.assertEqual({r["sid"] for r in rows}, {OTHER, DEADSID})
        self.assertEqual({r["cliPid"] for r in rows}, {STALE, DEAD})
        self.assertTrue(all(r["pid"] == os.getpid() for r in rows), "pid is the writing kernel's")
        self.assertTrue({r["text"] for r in rows} <= {p["text"] for p in be.problems()}, "the ring carries the prose alone")
        self.assertTrue(all(sb.PROBLEM_ROW_MARK not in p["text"] for p in be.problems()))


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
        ready = os.path.join(tempfile.mkdtemp(), "loop.ready")
        # the "CLI": a shell that starts a setsid'd loop (its own session + process group, like a tool's nohup child)
        # and then sits like a CLI mid-turn; the loop sleeps — no load. The loop ANNOUNCES itself: its first act is
        # to write its own pid and its parent's pid to the ready file, and every assertion below keys on that file,
        # never on an argv scan (T314, 2026-09-10: the pgrep-then-ps shape flaked twice on main in one day — a
        # pgrep match on the not-yet-exec'd child, then a ps snapshot that did not yet show the loop under the CLI).
        cli = subprocess.Popen(["bash", "-c", 'setsid bash -c \'echo "$$ $PPID" > "$1"; while :; do sleep 0.2; done\' "$0" "$1" & wait',
                                marker, ready],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self._kill_marker, marker)
        self.addCleanup(lambda: (cli.poll() is None) and os.killpg(cli.pid, signal.SIGKILL))
        # a bystander outside the tree
        by = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(by.wait, timeout=10); self.addCleanup(by.kill)
        # the loop's readiness is an EVENT: the file exists with both numbers in it (a bounded wait, since a runner may
        # be slow to schedule the child; the bound is generous and never the thing asserted)
        deadline = time.time() + 20
        pid = ppid = None
        while time.time() < deadline:
            try:
                parts = open(ready).read().split()
                if len(parts) == 2:
                    pid, ppid = int(parts[0]), int(parts[1])
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        self.assertIsNotNone(pid, "the detached loop announced itself before the cut (ready file %s)" % ready)
        self.assertEqual(ppid, cli.pid, "the loop's parent is the fake CLI: setsid exec'd in place, no extra fork")
        return cli, by, marker, pid

    @staticmethod
    def _marker_pids(marker):
        out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
        return [int(x) for x in out if x.isdigit() and int(x) != os.getpid()]

    def _kill_marker(self, marker):
        for p in self._marker_pids(marker):
            try: os.kill(p, signal.SIGKILL)
            except ProcessLookupError: pass

    def test_ending_the_cut_clis_tree_kills_the_detached_loop_and_spares_the_bystander(self):
        cli, by, marker, loop = self._tree()
        be = _backend()
        ps = subprocess.run(sb.PS_ARGV, capture_output=True, text=True, timeout=10).stdout.splitlines()
        # the loop announced its pid; the ps snapshot taken AFTER that announcement must show it under the CLI
        self.assertIn(loop, sb.descendants(ps, cli.pid),
                      "the setsid'd loop (pid %d) is the CLI's descendant by ppid: %r" % (loop, sb.descendants(ps, cli.pid)))
        out = be._end_cli_tree(cli.pid, ps, cgroup=lambda p: "")
        # Bounded poll for the loop to be GONE (the reaper's own poll shape: liveness, up to a few seconds): the
        # SIGKILL has been sent when _end_cli_tree returns, but a slow runner may not have taken the process off
        # the table yet. The bystander check stays immediate and strict. Liveness is the pid's /proc entry (a
        # killed loop's parent, the CLI, is killed too, so the subreaper reaps it and the entry goes).
        deadline = time.time() + 5
        while time.time() < deadline and (self._alive(loop) or cli.poll() is None):
            time.sleep(0.05)
        self.assertFalse(self._alive(loop), "the detached loop died with the cut turn")
        self.assertIsNotNone(cli.poll(), "the CLI is gone")
        self.assertIsNone(by.poll(), "the bystander outside the tree was never signaled")
        self.assertGreaterEqual(out["tree"], 1)

    def _fake_cli(self, sid):
        """A process that LOOKS like an SDK CLI of ours to the ps scan (the stream-json mark and the sid in its argv)
        and is a true ORPHAN shape: started by an intermediate shell that prints its pid and exits, so the fake CLI
        re-parents to the subreaper (the user manager, or pid 1), never to this test — a child of the tester would be
        the census's own-child case. Two commands in the fake CLI, so bash does not exec into the sleep and lose the
        argv; the sleep is its descendant. The intermediate leads a new process group that the fake CLI and its
        sleep inherit, so one killpg at cleanup ends whatever the reaper left. Returns (pid, pgid)."""
        inter = subprocess.Popen(["bash", "-c", 'bash -c "sleep 300; :" romp-t305-fake-cli --input-format stream-json --resume "$1" '
                                  '</dev/null >/dev/null 2>&1 & echo $!', "x", sid],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        out, _ = inter.communicate(timeout=10)
        pid = int(out.strip())
        def sweep():
            try:
                os.killpg(inter.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(sweep)
        return pid, inter.pid

    @staticmethod
    def _alive(pid):
        return os.path.exists("/proc/%d" % pid) and "zombie" not in open("/proc/%d/status" % pid).read().split("State:")[1][:20]

    def test_real_processes_a_leased_reparented_cli_survives_the_boot_and_a_stale_leased_one_is_reaped(self):
        """The acceptance shape of T305 with real processes: two orphan-shaped CLIs; the one whose lease this test
        HOLDS (the test process is the holder, alive by pid and start time) survives the boot reconcile, the one
        whose lease names a holder that is gone is ended. The cgroup seam is pinned empty (the fake CLIs inherit
        the tester's own scope, which the reaper would refuse anyway), the scope listing is empty, and every
        process this test starts is killed by it."""
        d = tempfile.mkdtemp(); be = _backend(d)
        # sids minted per run: the census is box-wide, so two checkouts running this test at once must never
        # see each other's fake CLIs as theirs (the T305 review)
        sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
        for sid in (sid_a, sid_b):
            _reg(d, sid)
        (live, live_pg), (stale, stale_pg) = self._fake_cli(sid_a), self._fake_cli(sid_b)
        now = time.time()
        sb.write_lease(d, {"sid": sid_a, "fsid": sid_a, "pid": live, "start": sb.proc_start(live),
                           "holder": {"pid": os.getpid(), "start": sb.proc_start(os.getpid())}, "version": "", "t": now})
        sb.write_lease(d, {"sid": sid_b, "fsid": sid_b, "pid": stale, "start": sb.proc_start(stale),
                           "holder": {"pid": P + 80, "start": "1"}, "version": "", "t": now})
        real_run = subprocess.run
        def run(argv, **kw):
            return mock.Mock(stdout="", returncode=0) if argv == sb.SCOPE_LIST_ARGV else real_run(argv, **kw)
        # the ps scan must see both before the census reads it
        deadline = time.time() + 5
        while time.time() < deadline:
            ps = real_run(sb.PS_ARGV, capture_output=True, text=True, timeout=10).stdout.splitlines()
            if set(sb.find_orphan_clis(ps, [sid_a, sid_b], os.getpid())) >= {live, stale}:
                break
            time.sleep(0.05)
        self.assertEqual(set(sb.find_orphan_clis(ps, [sid_a, sid_b], os.getpid())), {live, stale},
                         "by parentage alone both are orphans")
        with mock.patch.object(sb.subprocess, "run", side_effect=run), \
             mock.patch.object(sb, "_read_cgroup", lambda p: ""):
            be._boot_reconcile([sb.read_reg(Path(d), s) for s in (sid_a, sid_b)])
        deadline = time.time() + 5
        while time.time() < deadline and self._alive(stale):
            time.sleep(0.05)
        self.assertFalse(self._alive(stale), "the CLI whose lease did not hold is gone")
        self.assertTrue(self._alive(live), "the leased CLI survived the boot")
        self.assertIsNotNone(sb.read_lease(d, sid_a))
        self.assertIsNone(sb.read_lease(d, sid_b))
        rows = [r for r in (json.loads(l) for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines())
                if r["kind"].startswith("lease.")]
        self.assertEqual([(r["kind"], r["sid"], r["cliPid"]) for r in rows], [("lease.holder-gone", sid_b, stale)])
        # the escalation reaches the surviving, re-parented CLI through its lease
        self.assertEqual(be._session_cli_pid(_Sess(sid_a)), live)
        os.killpg(live_pg, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
