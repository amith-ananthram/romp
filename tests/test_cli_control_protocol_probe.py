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

Every test here runs the REAL CLI, so the suite's standing rule applies (tests/conftest.py floors
ROMP_CLAUDE_BIN at /bin/false: no test reaches the live CLI unasked). These run only when the
developer opts in with ROMP_CLI_PROBE_LIVE=1 and a `claude` is on PATH (or named in
ROMP_CLI_PROBE_CLAUDE); otherwise they skip, on CI too. They never touch the developer's own
Claude Code state: a throwaway CLAUDE_CONFIG_DIR and XDG_STATE_HOME per test, an empty settings
file, no key, every CLAUDE_* and ROMP_* variable scrubbed from the child's environment, and the
child killed with its process group in tearDown. Synthetic fixtures only.
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

LIVE = os.environ.get("ROMP_CLI_PROBE_LIVE") == "1"
CLAUDE = os.environ.get("ROMP_CLI_PROBE_CLAUDE") or shutil.which("claude") or ""
SKIP_WHY = ("set ROMP_CLI_PROBE_LIVE=1 to run against the installed Claude Code CLI"
            if not LIVE else "no `claude` on PATH (or ROMP_CLI_PROBE_CLAUDE)")

STREAM_ARGV = ["--print", "--output-format", "stream-json", "--verbose", "--input-format", "stream-json",
               "--permission-prompt-tool", "stdio", "--strict-mcp-config"]


def hermetic_env(root):
    """A child environment that reaches none of the developer's Claude Code or romp state."""
    env = {k: v for k, v in os.environ.items()
           if not (k == "CLAUDECODE" or k.startswith("CLAUDE_") or k.startswith("ROMP_") or k.startswith("XDG_"))}
    cfg = os.path.join(root, "cfg"); xdg = os.path.join(root, "xdg")
    os.makedirs(cfg); os.makedirs(xdg)
    with open(os.path.join(cfg, "settings.json"), "w") as f:
        f.write("{}")
    env.update({"CLAUDE_CONFIG_DIR": cfg, "XDG_STATE_HOME": xdg, "XDG_CONFIG_HOME": os.path.join(xdg, "config"),
                "XDG_CACHE_HOME": os.path.join(xdg, "cache"), "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_ENTRYPOINT": "sdk-py"})
    return cfg, env


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
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            pass


@unittest.skipUnless(LIVE and CLAUDE, SKIP_WHY)
class ControlProtocolPins(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="romp-cli-probe-")
        self.cfg, self.env = hermetic_env(self.root)
        self.cli = None

    def tearDown(self):
        if self.cli is not None:
            self.cli.kill()
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
        p = subprocess.run([CLAUDE, "--bg", "--print", "--input-format", "stream-json", "--output-format", "stream-json",
                            "Reply with the single word: ok"],
                           env=self.env, cwd=self.cfg, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(p.returncode, 0, "`--bg --print` was accepted: a background session may now take stream-json; re-measure")
        err = p.stderr.lower()
        self.assertIn("--bg", err, p.stderr)
        self.assertIn("--print", err, p.stderr)
        self.assertIn("conflict", err, p.stderr)

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


if __name__ == "__main__":
    unittest.main()
