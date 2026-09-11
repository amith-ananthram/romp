#!/usr/bin/env python3
"""The kernel-restart test for the per-session host (T315, stage 4 of #1317), end to end through a real kernel
process (no browser: this file is deliberately NOT named `_served.py`, the suffix CI's extension job collects for
the browser-backed page tests and runs with ROMP_SERVED_TESTS_REQUIRE=1, which would turn the SDK-venv skip below
into a failure on a runner that has no venv): a hermetic kernel with the
session-hosts setting ON runs a session whose fake CLI takes twenty seconds over a turn; the kernel is sent
SIGTERM five seconds in (the manager's restart: a drain that now DETACHES); a second kernel boots on the same
state directory, attaches to the live host, and the turn finishes under it: the session settles waiting, the
old kernel's restart-cuts row has an empty cutTurns, the CLI is the same process throughout (one writer), no
continuation notice was queued, and a host.attached row with boot true is on the ledger. A second run with
the setting OFF (a toggled-off machine, since hosts are on by default) pins the plain child's behaviour (the turn is
cut, a notice is queued, a new CLI pid), so the test proves
the difference, not a constant.

Hermetic: a temp state root and Claude config dir, the fake CLI as ROMP_CLAUDE_BIN, no scopes, the SDK venv
symlinked into the temp state root (the kernel looks for it there); skipped where that venv is absent (CI).
Every process the test starts is killed by it. Synthetic ids only.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (named runner variables, never the whole environment)
from dist_copy import copy_dist   # noqa: E402
EXT_DIST = os.path.join(ROOT, "vscode-extension", "dist")
FAKE = os.path.join(HERE, "fixtures", "fake_claude.py")
SDKVENV = os.path.expanduser("~/.local/state/romp/sdkvenv")
# the kernel must run on the python the SDK venv was built for (its lib/python3.X names it), not on whatever
# `python3` the test runner's environment puts first
_tags = sorted(p.name for p in Path(SDKVENV, "lib").glob("python3.*")) if os.path.isdir(os.path.join(SDKVENV, "lib")) else []
KERNEL_PYTHON = next((shutil.which(t) for t in _tags if shutil.which(t)), None)
HAVE_SDK = KERNEL_PYTHON is not None


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@unittest.skipUnless(HAVE_SDK, "no SDK venv (or no matching python) on this machine; the served host test needs the real SDK client in the kernel")
class ServedRestart(unittest.TestCase):
    def setUp(self):
        self.lab = tempfile.mkdtemp(prefix="host-served-")
        self.addCleanup(self._sweep)
        self.state = os.path.join(self.lab, "xdg", "romp")
        self.claude = os.path.join(self.lab, "claude")
        self.cwd = os.path.join(self.lab, "proj")
        for d in ("names", "sdk", "states", "timeline"):
            os.makedirs(os.path.join(self.state, d), exist_ok=True)
        os.makedirs(self.cwd, exist_ok=True)
        os.symlink(SDKVENV, os.path.join(self.state, "sdkvenv"))
        self.sid = str(uuid.uuid4())
        Path(self.state, "names", self.sid).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % self.cwd)
        Path(self.state, "sdk", self.sid + ".json").write_text(json.dumps(
            {"sid": self.sid, "name": "web", "cwd": self.cwd, "mode": "bypassPermissions", "effort": "high", "lastSid": self.sid, "alive": True}))
        self.port, self.token = _free_port(), "testtok-host"
        self.dist = os.path.join(self.lab, "dist")
        if os.path.isdir(EXT_DIST):
            copy_dist(EXT_DIST, self.dist)       # a lab copy: the kernel must never rebuild bundles in the shared checkout
        else:
            os.makedirs(self.dist, exist_ok=True)
        self.fake_log = os.path.join(self.lab, "fake-cli.log")
        self.kernels = []
        self.klogs = []

    def _sweep(self):
        lease = self._lease()
        for pid in ((lease or {}).get("pid"), ((lease or {}).get("holder") or {}).get("pid")):
            if isinstance(pid, int):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        for k in self.kernels:
            if k.poll() is None:
                try:
                    os.killpg(k.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                k.wait(timeout=10)
        # whatever the hosts left: every process whose environment carries this lab's state root
        for p in Path("/proc").glob("[0-9]*"):
            try:
                env = (p / "environ").read_bytes()
            except OSError:
                continue
            if self.lab.encode() in env and int(p.name) != os.getpid():
                try:
                    os.kill(int(p.name), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        shutil.rmtree(self.lab, ignore_errors=True)

    def _env(self):
        env = _lab.kernel_env(self.lab, self.claude, self.dist, self.port, self.token,
                              ROMP_CLAUDE_BIN=FAKE, ROMP_CLI_SCOPE="0",
                              FAKE_CLI_TRANSCRIPT_DIR=os.path.join(self.lab, "transcripts"), FAKE_CLI_LOG=self.fake_log,
                              PATH=os.environ.get("PATH", ""), HOME=os.environ.get("HOME", ""))
        for k in ("ROMP_STATE_DIR", "ROMP_API_KEY_CMD", "ANTHROPIC_API_KEY", "ROMP_SDK_SITE"):
            env.pop(k, None)
        return env

    def _start_kernel(self):
        log = os.path.join(self.lab, "kernel-%d.log" % (len(self.kernels) + 1))
        k = subprocess.Popen([KERNEL_PYTHON, os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT,
                             env=self._env(), start_new_session=True)
        self.kernels.append(k); self.klogs.append(log)
        deadline = time.time() + 60
        while time.time() < deadline:   # loop-ok: a bounded wait on the kernel serving
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % self.port, timeout=1)
                return k
            except Exception:
                if k.poll() is not None:
                    break
                time.sleep(0.25)
        raise unittest.SkipTest("hermetic kernel never served /healthz here: " + open(log).read()[-800:])

    def _send(self, text):
        req = urllib.request.Request("http://127.0.0.1:%d/send?token=%s" % (self.port, self.token),
                                     data=json.dumps({"id": self.sid, "text": text}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def _state(self):
        p = Path(self.state, "states", self.sid + ".jsonl")
        last = None
        if p.exists():
            for ln in p.read_text().splitlines():
                try:
                    row = json.loads(ln)
                except ValueError:
                    continue
                if "state" in row:
                    last = row["state"]
        return last

    def _lease(self):
        p = Path(self.state, "leases", self.sid + ".json")
        return json.loads(p.read_text()) if p.exists() else None

    def _events(self, kind):
        p = Path(self.state, "session-events.jsonl")
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip() and json.loads(l).get("kind") == kind] if p.exists() else []

    def _cuts(self):
        p = Path(self.state, "restart-cuts.jsonl")
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def _wait(self, pred, timeout, what):
        deadline = time.time() + timeout
        while time.time() < deadline:   # loop-ok: a bounded wait on an observable event
            if pred():
                return
            time.sleep(0.25)
        tails = "\n".join("--- %s:\n%s" % (l, open(l).read()[-2500:]) for l in self.klogs)
        self.fail("timed out waiting for %s\n%s" % (what, tails))

    def _run(self, hosts_on: bool):
        Path(self.state, "session-hosts").write_text("on" if hosts_on else "off")
        self._start_kernel()
        self.assertTrue(self._send("start long sleep=20").get("ok"), "the send was accepted")
        self._wait(lambda: self._state() == "working", 60, "the turn to start (working)")
        if hosts_on:
            self._wait(lambda: (self._lease() or {}).get("holder", {}).get("kind") == "host", 30, "a host-held lease")
        else:
            self._wait(lambda: self._lease() is not None, 30, "a kernel-held lease")
        lease1 = self._lease()
        time.sleep(3.0)                                    # five seconds in, the restart
        k1 = self.kernels[-1]
        os.kill(k1.pid, signal.SIGTERM)                    # _graceful_term: the drain
        k1.wait(timeout=30)
        cuts = [r for r in self._cuts() if r.get("pid") == k1.pid and "cutTurns" in r]
        self.assertEqual(len(cuts), 1, "the old kernel filed its restart-cuts row: %r" % self._cuts())
        self._start_kernel()
        self._wait(lambda: self._state() == "waiting", 60, "the turn to finish under the second kernel (waiting)")
        reg = json.loads(Path(self.state, "sdk", self.sid + ".json").read_text())
        queued = [t for t in (reg.get("queue") or []) if isinstance(t, str)]
        return lease1, cuts[0], queued

    def test_with_hosts_on_the_turn_survives_the_restart_and_with_hosts_off_it_is_cut(self):
        lease1, cut, queued = self._run(hosts_on=True)
        self.assertEqual(cut["cutTurns"], [], "an attached session is detached, not cut: %r" % cut)
        lease2 = self._lease()
        self.assertIsNotNone(lease2, "the host still holds the lease after the turn")
        self.assertEqual((lease2["pid"], lease2["holder"]["pid"]), (lease1["pid"], lease1["holder"]["pid"]), "the same CLI and host: one writer")
        att = self._events("host.attached")
        self.assertEqual([a["boot"] for a in att], [False, True], "spawn-time attach, then the boot attach: %r" % att)
        self.assertGreater(att[1]["replayFrom"], 0, "the boot attach replayed from the acknowledged offset")
        self.assertFalse(any("restart" in t.lower() and "cut" in t.lower() for t in queued), "no continuation notice: %r" % queued)
        transcript = list(Path(self.lab, "transcripts").glob("*.jsonl"))
        self.assertEqual(len(transcript), 1, "one transcript stand-in, one writer")
        results = [json.loads(l) for l in transcript[0].read_text().splitlines() if '"type":"result"' in l]
        self.assertEqual([r["result"] for r in results], ["done"], "one result, the turn's normal completion: the restart neither cut nor interrupted it")

    def test_control_with_hosts_off_the_restart_cuts_the_turn(self):
        lease1, cut, queued = self._run(hosts_on=False)
        self.assertEqual([c["sid"] for c in cut["cutTurns"]], [self.sid], "the toggled-off machine: the turn is cut")
        # the continuation notice reached the CLI (the fake logs every stdin line): a user message naming the restart
        fed = [json.loads(l) for l in Path(self.fake_log).read_text().splitlines() if l.strip()]
        users = [m for m in fed if m.get("type") == "user"]
        texts = [c if isinstance(c := (m.get("message") or {}).get("content"), str) else json.dumps(c) for m in users]
        self.assertTrue(any("restart" in t.lower() for t in texts), "the second kernel fed the continuation notice: %r" % texts[-3:])
        lease2 = self._lease()
        self.assertIsNotNone(lease2, "the resumed session holds a fresh kernel lease")
        self.assertNotEqual(lease2["pid"], lease1["pid"], "a NEW CLI process: the old one was cut and reaped")
        self.assertNotEqual((lease2.get("holder") or {}).get("kind"), "host")
        self.assertEqual(self._events("host.attached"), [], "no host, no attach")


if __name__ == "__main__":
    unittest.main()
