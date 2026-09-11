#!/usr/bin/env python3
"""Pins on the Claude Code CLI's control protocol that a session host (a process that owns the CLI
across kernel restarts, T295 stage 3 / T303, 2026-09-10) will rely on. Measured on Claude Code 2.1.257
with the probe described in docs/reference.md ("What survives a restart"):

  * a second `initialize` control request on the same stdin is accepted (subtype success, a
    `hooks_applied` field), not refused: a re-attaching kernel can send its own handshake;
  * the CLI answers `initialize` without any credential and without a turn, so this pin costs no
    model call;
  * stdin end-of-file with no turn in flight ends the process at once (measured 0.02 s);
  * `--bg` (a background session) and `--print` (the only mode that takes `--input-format
    stream-json`) are refused together, so a background session has no structured channel.

The pins in ControlProtocolPins run the REAL CLI, so the suite's standing rule applies
(tests/conftest.py floors ROMP_CLAUDE_BIN at /bin/false: no test reaches the live CLI unasked). They
run only when the developer opts in with ROMP_CLI_PROBE_LIVE=1 and a `claude` is on PATH (or named
in ROMP_CLI_PROBE_CLAUDE); otherwise they skip, on CI too. They never touch the developer's own
Claude Code state: a throwaway CLAUDE_CONFIG_DIR and XDG_STATE_HOME per test with an empty settings
file, and a child environment with every ANTHROPIC_*, CLAUDE_* and ROMP_* variable scrubbed, which
are the two channels the test controls (managed settings outside the config directory are not the
test's to control). The scrub is the module's own, so a bare
`python3 tests/test_cli_control_protocol_probe.py` run, which loads no conftest, hands the CLI no
key either. Every CLI the module starts runs in a process group of its own that tearDown kills, and
the one started without a stdin pipe reads /dev/null; a daemon that leaves its group is out of the
kill's reach, which is one more reason the child carries no credential. The two classes after the
pins start no CLI and run everywhere: they check the scrub and the runner over /bin/sh. Synthetic
fixtures only.
"""
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest import mock

LIVE = os.environ.get("ROMP_CLI_PROBE_LIVE") == "1"
CLAUDE = os.environ.get("ROMP_CLI_PROBE_CLAUDE") or shutil.which("claude") or ""
SKIP_WHY = ("set ROMP_CLI_PROBE_LIVE=1 to run against the installed Claude Code CLI"
            if not LIVE else "no `claude` on PATH (or ROMP_CLI_PROBE_CLAUDE)")

STREAM_ARGV = ["--print", "--output-format", "stream-json", "--verbose", "--input-format", "stream-json",
               "--permission-prompt-tool", "stdio", "--strict-mcp-config"]


def hermetic_env(root):
    """A child environment that reaches none of the developer's Claude Code or romp state, and carries no
    credential the test can see: every ANTHROPIC_* name goes (a key, a login token, a proxy base URL and
    the header that usually carries the proxy's credential), by prefix rather than by list, as the
    kernel's codex judge does, because the CLI grows names and the harmless ones may go too."""
    env = {k: v for k, v in os.environ.items()
           if not (k == "CLAUDECODE" or k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_")
                   or k.startswith("ROMP_") or k.startswith("XDG_"))}
    cfg = os.path.join(root, "cfg"); xdg = os.path.join(root, "xdg")
    os.makedirs(cfg); os.makedirs(xdg)
    with open(os.path.join(cfg, "settings.json"), "w") as f:
        f.write("{}")
    env.update({"CLAUDE_CONFIG_DIR": cfg, "XDG_STATE_HOME": xdg, "XDG_CONFIG_HOME": os.path.join(xdg, "config"),
                "XDG_CACHE_HOME": os.path.join(xdg, "cache"), "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_ENTRYPOINT": "sdk-py"})
    return cfg, env


def kill_group(p):
    """SIGKILL the process group `p` leads (start_new_session made it a leader), reap `p` and close its
    pipes. A group that is already empty is fine; a member that left the group (a daemon's setsid) is
    not reached, and it may still hold the pipes' write ends, so the read ends are closed, never drained."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass
    for f in (p.stdin, p.stdout, p.stderr):
        if f is not None:
            try:
                f.close()
            except Exception:
                pass


def run_probe(exe, argv, env, cwd, timeout, procs):
    """Run `exe` with `argv` to completion the way a probe needs: stdin at /dev/null (a CLI that wants a
    prompt from stdin sees end-of-file at once, never the test's own stdin), a process group of its
    own, and the Popen appended to `procs` BEFORE the wait, so tearDown's group kill reaches whatever
    the run left behind however it ended. Returns (returncode, stdout, stderr); a run still going at
    `timeout` seconds is killed with its group and TimeoutExpired re-raised."""
    p = subprocess.Popen([exe, *argv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env, cwd=cwd, start_new_session=True)
    procs.append(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(p)
        raise
    return p.returncode, out, err


class StreamCli:
    """The CLI over raw pipes, the way a host would hold it: JSON lines in, JSON lines out."""

    def __init__(self, argv, env, cwd):
        self.p = subprocess.Popen([CLAUDE, *STREAM_ARGV, *argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, cwd=cwd, start_new_session=True)
        self.buf = b""
        self.n = 0

    def send(self, obj):
        self.p.stdin.write((json.dumps(obj) + "\n").encode()); self.p.stdin.flush()

    def initialize(self, hooks):
        rid = "req_%d_%s" % (self.n, uuid.uuid4().hex[:8]); self.n += 1
        self.send({"type": "control_request", "request_id": rid, "request": {"subtype": "initialize", "hooks": hooks}})
        return rid

    def read(self, timeout):
        """One parsed stdout line, or None at the timeout, or 'EOF'."""
        end = time.monotonic() + timeout
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line, self.buf = self.buf[:i], self.buf[i + 1:]
                try:
                    return json.loads(line)
                except ValueError:
                    continue
            rem = end - time.monotonic()
            if rem <= 0:
                return None
            r, _, _ = select.select([self.p.stdout], [], [], min(rem, 0.5))
            if r:
                chunk = os.read(self.p.stdout.fileno(), 1 << 16)
                if not chunk:
                    return "EOF"
                self.buf += chunk

    def wait_control_response(self, rid, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            o = self.read(end - time.monotonic())
            if o is None or o == "EOF":
                return o
            if o.get("type") == "control_response" and (o.get("response") or {}).get("request_id") == rid:
                return o

    def close_stdin(self):
        self.p.stdin.close()

    def wait_exit(self, timeout):
        """Seconds until the process exited, draining stdout meanwhile; None if it is still alive."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.p.poll() is not None:
                return time.monotonic() - t0
            r, _, _ = select.select([self.p.stdout], [], [], 0.05)
            if r and not os.read(self.p.stdout.fileno(), 1 << 16):
                pass
        return None

    def kill(self):
        kill_group(self.p)


@unittest.skipUnless(LIVE and CLAUDE, SKIP_WHY)
class ControlProtocolPins(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="romp-cli-probe-")
        self.cfg, self.env = hermetic_env(self.root)
        self.cli = None
        self.procs = []

    def tearDown(self):
        if self.cli is not None:
            self.cli.kill()
        for p in self.procs:
            kill_group(p)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_second_initialize_is_accepted_and_idle_eof_exits_at_once(self):
        self.cli = StreamCli(["--permission-mode", "bypassPermissions"], self.env, self.cfg)
        first = self.cli.initialize({"PreToolUse": [{"matcher": "Bash", "hookCallbackIds": ["hook_0"]}]})
        r1 = self.cli.wait_control_response(first, 30)
        self.assertIsInstance(r1, dict, "the CLI answered no initialize within 30 s (stderr: %r)" % self._stderr())
        self.assertEqual(r1["response"]["subtype"], "success", r1)
        self.assertIn("hooks_applied", r1["response"]["response"], "the handshake no longer reports hooks_applied")
        # a second handshake on the same stdin: a re-attaching kernel's. Not refused, not an error.
        second = self.cli.initialize({"PreToolUse": [{"matcher": "Bash", "hookCallbackIds": ["hook_1"]}]})
        r2 = self.cli.wait_control_response(second, 30)
        self.assertIsInstance(r2, dict, "the CLI answered no second initialize within 30 s")
        self.assertEqual(r2["response"]["subtype"], "success", r2)
        self.assertIn("hooks_applied", r2["response"]["response"])
        self.assertIsNone(self.cli.p.poll(), "the CLI exited on a second initialize")
        # stdin end-of-file with nothing in flight: the process ends promptly (measured 0.02 s on 2.1.257).
        self.cli.close_stdin()
        dt = self.cli.wait_exit(10)
        self.assertIsNotNone(dt, "the CLI did not exit within 10 s of stdin end-of-file while idle")
        self.assertEqual(self.cli.p.returncode, 0, self._stderr())

    def test_background_session_refuses_print_and_so_has_no_stream_json_channel(self):
        # No prompt (the assertions read the exit status and stderr only), stdin at /dev/null, no
        # credential in the environment, and a process group tearDown kills: a CLI that accepted the pair
        # (the regression the first assertion exists to catch) has nothing to run and no key to run it
        # with, and its launcher and any daemon that stays in the group are killed.
        argv = ["--bg", "--print", "--input-format", "stream-json", "--output-format", "stream-json"]
        rc, _out, stderr = run_probe(CLAUDE, argv, self.env, self.cfg, 60, self.procs)
        self.assertNotEqual(rc, 0, "`--bg --print` was accepted: a background session may now take stream-json; re-measure")
        err = stderr.lower()
        self.assertIn("--bg", err, stderr)
        self.assertIn("--print", err, stderr)
        self.assertIn("conflict", err, stderr)

    def _stderr(self):
        """Whatever the CLI has written to stderr so far, without blocking on an open pipe (the message
        argument of an assertion is built before the assertion runs, so this must never wait)."""
        out = b""
        try:
            fd = self.cli.p.stderr.fileno()
            while select.select([fd], [], [], 0)[0]:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                out += chunk
        except Exception:
            pass
        return out[-2000:]


class HermeticEnvPins(unittest.TestCase):
    """What hermetic_env drops from the child's environment. No CLI is started, so these run on CI and
    under a bare unittest run alike. The scrub is the module's own: tests/conftest.py pops a fixed list of
    credential names, only under pytest, and a bare `python3 tests/test_cli_control_protocol_probe.py`
    run loads no conftest."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="romp-cli-probe-unit-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_drops_every_anthropic_claude_romp_and_xdg_name(self):
        # Planted values are three plain words and a loopback URL, all short, so nothing credential-shaped
        # sits in the repo or in a failure report; the assertions render names only, never the mapping.
        planted = {"ANTHROPIC_API_KEY": "planted-anthropic-api-key",
                   "ANTHROPIC_AUTH_TOKEN": "planted-anthropic-auth-token",
                   "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
                   "ANTHROPIC_CUSTOM_HEADERS": "planted-header",
                   "CLAUDECODE": "1", "CLAUDE_PLANTED": "1", "ROMP_PLANTED": "1", "XDG_DATA_HOME": "/nonexistent"}
        with mock.patch.dict(os.environ, planted):
            cfg, env = hermetic_env(self.root)
        self.assertEqual(sorted(k for k in env if k.startswith("ANTHROPIC_")), [],
                         "ANTHROPIC_* names reached the child's environment")
        self.assertEqual(sorted(k for k in env if k in planted and not k.startswith("ANTHROPIC_")), [])
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], cfg)
        self.assertTrue(cfg.startswith(self.root + os.sep), cfg)
        self.assertTrue(env["XDG_STATE_HOME"].startswith(self.root + os.sep), env["XDG_STATE_HOME"])
        self.assertTrue(os.path.isfile(os.path.join(cfg, "settings.json")))


def _gone(pid, timeout):
    """Whether `pid` is dead within `timeout` seconds: reaped (no such process), or a zombie its new
    parent has not collected yet (/proc/<pid>/stat state Z, which is the usual sight on Linux right
    after the kill; where /proc is absent only the reaped case counts)."""
    end = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            with open("/proc/%d/stat" % pid) as f:
                if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except OSError:
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


# A background sleep the sh leaves behind stands in for a daemon a CLI would leave behind. Its three
# descriptors are redirected on purpose: with the pipes inherited, communicate() would wait for their
# end-of-file until the sleep ended.
LEAVE_A_SLEEPER = "sleep 30 </dev/null >/dev/null 2>&1 & echo $!"


class ProbeRunPins(unittest.TestCase):
    """run_probe, kill_group and the class's own tearDown over /bin/sh, with no CLI: these run on CI."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="romp-cli-probe-unit-")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            kill_group(p)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_run_probe_takes_its_own_group_and_kill_group_reaches_an_orphan(self):
        rc, out, err = run_probe("/bin/sh", ["-c", LEAVE_A_SLEEPER], dict(os.environ), self.root, 10, self.procs)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self.procs), 1)
        sleeper = int(out.strip())
        # start_new_session made the sh the leader of a new group (pgid == its pid); a non-interactive sh
        # has no job control, so its background child stays in that group, which outlives the sh.
        self.assertEqual(os.getpgid(sleeper), self.procs[0].pid)
        kill_group(self.procs[0])
        self.assertTrue(_gone(sleeper, 5), "the orphaned sleeper survived the group kill")

    def test_run_probe_kills_the_group_when_the_timeout_expires(self):
        # The sh records its sleeper's pid, then waits on it, so the run outlives the timeout.
        with self.assertRaises(subprocess.TimeoutExpired):
            run_probe("/bin/sh", ["-c", "sleep 30 </dev/null >/dev/null 2>&1 & echo $! > pid; wait"],
                      dict(os.environ), self.root, 3, self.procs)
        self.assertEqual(len(self.procs), 1)
        p = self.procs[0]
        self.assertIsNotNone(p.returncode, "the sh outlived its timeout")
        self.assertTrue(p.stdout.closed and p.stderr.closed, "the timeout's group kill left the pipes open")
        pid_path = os.path.join(self.root, "pid")
        self.assertTrue(os.path.exists(pid_path), "the sh never recorded its sleeper")
        with open(pid_path) as f:
            sleeper = int(f.read().strip())
        self.assertTrue(_gone(sleeper, 5), "the sleeper survived the timeout's group kill")

    def test_teardown_kills_a_group_the_test_left_running(self):
        # Nothing here kills the sleeper. A cleanup runs after tearDown, so the check reads tearDown's kill.
        rc, out, err = run_probe("/bin/sh", ["-c", LEAVE_A_SLEEPER], dict(os.environ), self.root, 10, self.procs)
        self.assertEqual(rc, 0, err)
        self.addCleanup(self._assert_gone, int(out.strip()), "tearDown left the sleeper running")

    def _assert_gone(self, pid, why):
        self.assertTrue(_gone(pid, 5), why)

    def test_run_probe_reads_stdin_from_dev_null(self):
        # The test's own fd 0 carries bytes for the duration of the run: pytest's capture puts /dev/null
        # on fd 0 during a test, so a run_probe that inherited stdin would otherwise look the same as one
        # reading /dev/null. `cat` on an inherited stdin would echo the bytes before `eof`.
        r, w = os.pipe()
        os.write(w, b"inherited\n"); os.close(w)
        saved = os.dup(0)
        try:
            os.dup2(r, 0)
            rc, out, err = run_probe("/bin/sh", ["-c", "cat; echo eof"], dict(os.environ), self.root, 10, self.procs)
        finally:
            os.dup2(saved, 0); os.close(saved); os.close(r)
        self.assertEqual((rc, out.strip()), (0, "eof"), err)


if __name__ == "__main__":
    unittest.main()
