#!/usr/bin/env python3
"""The kernel's spawned tmux session lands under the user's runtime directory, and a plain shell's client dials the
same socket (T325, 2026-09-11). A lab kernel (the postal trio, scopes off, its own port) whose XDG_RUNTIME_DIR is a
directory inside the lab creates a terminal session through POST /new (backend tmux → `romp new -t --detach`, the
same launch the dashboard's + runs); the session appears on the server at <XDG_RUNTIME_DIR>/romp/tmux-<uid>/default,
a client on any other socket directory finds nothing there, and `romp new -t --detach <name>` from a plain shell with
the lab's XDG_RUNTIME_DIR reports the session already running: server and clients pinned to agree. The server is
started the way the manager starts it (exit-empty off, so a pane's end never takes the server). The socket's root
follows tests/README.md: the private temp root when the socket path fits sun_path, else the directory the run was
handed (ROMP_TESTS_SYSTEM_TMPDIR), so the case runs on macOS and under a deep TMPDIR instead of skipping. The machine's
own tmux server (/tmp/tmux-<uid>) is never dialed. A fake `claude` on PATH answers --version and otherwise sleeps, so
the pane stays up without a real CLI. Synthetic names and directories only."""
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

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

FAKE_CLAUDE = """#!/usr/bin/env bash
# a stand-in for the CLI inside the lab's pane: a version the launcher's floor accepts, then it holds the pane open
if [ "$1" = "--version" ]; then echo "9.9.9 (Claude Code)"; exit 0; fi
exec sleep 600
"""
_SUN_PATH_MAX = 104 if sys.platform == "darwin" else 108   # NUL included; the socket is <root>/run/romp/tmux-<uid>/default


def _lab_root():
    """A lab whose tmux socket path fits sun_path: the private temp root when it does, else the directory the run was
    handed before the redirect (tests/README.md's one sanctioned exit from the root); removed by the caller."""
    tail = os.path.join("run", "romp", "tmux-%d" % os.getuid(), "default")
    lab = tempfile.mkdtemp(prefix="tmux-runtime-")
    if len(os.fsencode(os.path.join(lab, tail))) < _SUN_PATH_MAX:
        return lab
    os.rmdir(lab)
    handed = os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or tempfile.gettempdir()
    lab = tempfile.mkdtemp(prefix="tmux-runtime-", dir=handed)
    if len(os.fsencode(os.path.join(lab, tail))) >= _SUN_PATH_MAX:
        os.rmdir(lab)
        raise unittest.SkipTest("the temp dir this run was handed is itself too deep for a tmux socket path (%d-byte sun_path): %s" % (_SUN_PATH_MAX, handed))
    return lab


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class TmuxSocketUnderTheRuntimeDir(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("tmux"):
            raise unittest.SkipTest("tmux absent here; the terminal backend needs it")
        cls.lab = _lab_root()
        cls.addClassCleanup(shutil.rmtree, cls.lab, ignore_errors=True)
        cls.runtime = os.path.join(cls.lab, "run"); os.mkdir(cls.runtime, 0o700)
        cls.other = os.path.join(cls.lab, "other"); os.mkdir(cls.other, 0o700)   # a socket directory nobody serves
        fake = os.path.join(cls.lab, "fakebin"); os.mkdir(fake)
        with open(os.path.join(fake, "claude"), "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(os.path.join(fake, "claude"), 0o755)
        cls.proj = os.path.join(cls.lab, "proj"); os.mkdir(cls.proj)
        claude = os.path.join(cls.lab, "claude"); os.makedirs(os.path.join(claude, "projects"))
        dist = os.path.join(cls.lab, "dist"); os.mkdir(dist)   # no page is served here: the API is the whole exercise
        cls.port, cls.token = _free_port(), "testtok-tmuxrun"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token,
                              XDG_RUNTIME_DIR=cls.runtime, ROMP_CLI_SCOPE="0", HOME=cls.lab,
                              PATH=fake + os.pathsep + BIN + os.pathsep + os.environ.get("PATH", ""))
        for k in ("TMUX", "TMUX_PANE", "TMUX_TMPDIR", "ROMP_TMUX_SOCKET", "ROMP_TMUX_AVAILABLE"):
            env.pop(k, None)
        cls.env = env
        # the plain shell's environment: the lab's runtime dir and state root, the lab kernel's port (bin/romp compares
        # its socket directory with the kernel's /version before a terminal launch), no TMUX of any kind
        cls.client_env = {"PATH": env["PATH"], "HOME": cls.lab, "XDG_RUNTIME_DIR": cls.runtime,
                          "XDG_STATE_HOME": env["XDG_STATE_HOME"], "LANG": os.environ.get("LANG", "C.UTF-8"),
                          "ROMP_KERNEL_PORT": str(cls.port)}
        # the server the way the manager starts it (exit-empty off, so a session's end never takes the server), on the
        # runtime-dir socket the kernel will resolve; the pane command is `exec env … claude …` since the launcher fix,
        # which every shell runs, so the server's default shell needs no choosing
        cls.romp_dir = os.path.join(cls.runtime, "romp"); os.mkdir(cls.romp_dir, 0o700)
        cls.addClassCleanup(cls._kill_server)
        r = subprocess.run(["tmux", "start-server", ";", "set", "-g", "exit-empty", "off"],
                           env={**cls.client_env, "TMUX_TMPDIR": cls.romp_dir}, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise unittest.SkipTest("no tmux server could start under the lab's runtime dir: " + r.stderr.strip()[:200])
        cls.klog_path = os.path.join(cls.lab, "kernel.log")
        cls.klog = open(cls.klog_path, "w")
        cls.addClassCleanup(cls._kill_kernel)
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=cls.klog, stderr=subprocess.STDOUT,
                                      env=env, start_new_session=True)
        for _ in range(240):   # loop-ok: a bounded boot wait
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")   # the class cleanups stop what started

    # torn down by class cleanups armed right after each start (setUpClass's own failure or a Ctrl-C runs them
    # too), so neither the lab's tmux server nor the kernel outlives the test in the tester's session scope
    @classmethod
    def _kill_server(cls):
        subprocess.run(["tmux", "kill-server"], env={**cls.client_env, "TMUX_TMPDIR": os.path.join(cls.runtime, "romp")},
                       capture_output=True, timeout=20)   # the lab's server, with the fake CLI's pane inside it

    @classmethod
    def _kill_kernel(cls):
        k = getattr(cls, "kernel", None)
        if k:
            try:
                os.killpg(k.pid, signal.SIGTERM); k.wait(timeout=20)
            except Exception:
                try:
                    os.killpg(k.pid, signal.SIGKILL); k.wait(timeout=10)
                except Exception:
                    pass
        if getattr(cls, "klog", None):
            cls.klog.close()

    @classmethod
    def _stop(cls):
        cls._kill_server(); cls._kill_kernel()

    def _kernel(self, method, path, body=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Romp-Token": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                code, raw = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read().decode()
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw

    def _tmux(self, tmpdir, *args):
        return subprocess.run(["tmux", *args], env={**self.client_env, "TMUX_TMPDIR": tmpdir}, capture_output=True, text=True, timeout=20)

    def test_the_kernel_s_session_lands_under_the_runtime_dir_and_a_plain_shell_s_client_finds_it_there(self):
        klog = open(self.klog_path, encoding="utf-8", errors="replace").read()
        # an unmanaged kernel (no ROMP_MANAGER_PID in the lab) resolves for itself and says which rule fired
        self.assertIn("tmux socket dir: %s (the user's runtime directory)" % os.path.join(self.runtime, "romp"), klog,
                      "the kernel says where the socket lives at boot: %s" % klog[-1500:])
        code, res = self._kernel("POST", "/new", {"name": "web", "dir": self.proj, "backend": "tmux"})
        self.assertEqual(code, 200, res)
        self.assertTrue(res.get("ok"), "the create was accepted: %r" % res)
        romp_dir = os.path.join(self.runtime, "romp")
        end = time.time() + 90
        names = ""
        while time.time() < end:   # loop-ok: bounded by the deadline
            r = self._tmux(romp_dir, "list-sessions", "-F", "#{session_name}")
            names = r.stdout
            if r.returncode == 0 and "web" in names.split():
                break
            time.sleep(0.5)
        self.assertIn("web", names.split(), "the session the kernel created is on the runtime-dir server; kernel log tail: %s"
                      % open(self.klog_path, encoding="utf-8", errors="replace").read()[-2500:])
        sock = os.path.join(romp_dir, "tmux-%d" % os.getuid(), "default")
        self.assertTrue(os.path.exists(sock), "the socket is where the rule says: %s" % sock)
        # a client on any OTHER socket directory finds nothing there (the old /tmp location, stood in for by a lab
        # directory: the machine's own server is never dialed)
        r = self._tmux(self.other, "has-session", "-t", "=web")
        self.assertNotEqual(r.returncode, 0, "no server on another directory answers for it: %r" % r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.other, "tmux-%d" % os.getuid(), "default")),
                         "has-session starts no server there (tmux makes the tmux-<uid> directory, never a socket)")
        # the client side: bin/romp from a plain shell with the lab's runtime dir dials the same server
        r = subprocess.run([os.path.join(BIN, "romp-tmux-env")], env=self.client_env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.strip(), romp_dir, "the shell helper resolves the same directory")
        r = subprocess.run([os.path.join(BIN, "romp"), "new", "-t", "--detach", "web"], env=self.client_env,
                           capture_output=True, text=True, timeout=60)
        self.assertIn("already running", r.stdout + r.stderr,
                      "a plain shell's client sees the kernel's session: rc=%s out=%r err=%r" % (r.returncode, r.stdout[-600:], r.stderr[-600:]))
        # …and the kernel's own liveness read lists it as a terminal session
        end = time.time() + 30
        rows = {}
        while time.time() < end:   # loop-ok: bounded
            code, rows = self._kernel("GET", "/sessions")
            if code == 200 and any((s.get("name") == "web") for s in (rows.get("sessions") if isinstance(rows, dict) else rows or [])):
                break
            time.sleep(0.5)
        listed = rows.get("sessions") if isinstance(rows, dict) else rows
        web = next((s for s in (listed or []) if s.get("name") == "web"), None)
        self.assertIsNotNone(web, "the kernel lists the session it created: %r" % (listed if isinstance(listed, list) else rows))


if __name__ == "__main__":
    unittest.main()
