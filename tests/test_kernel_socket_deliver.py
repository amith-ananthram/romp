#!/usr/bin/env python3
"""Postal mail's inbox-socket delivery leg (Claude Code >= 2.1.224). The CLI registers each
session's inbox socket in ~/.claude/sessions/<pid>.json; one JSON line posted there injects a
message without touching the composer. The kernel uses that leg ONLY for sessions tagged
@romp-inbound-accept — the tag bin/romp writes at the same launch that passes the CLI's
inbound-accept setting, so a held-then-dropped banner can never read as delivered (the socket
sends no ack). Untagged sessions must keep today's pane injection untouched. Synthetic
fixtures only."""
import errno
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from unittest.mock import patch

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_socketleg", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
DEAD_PID = 4999999                       # above every platform's default pid ceiling


def _registry_row(dirpath, pid, session_id, sock, started_at=1000):
    with open(os.path.join(dirpath, "%d.json" % pid), "w") as f:
        json.dump({"pid": pid, "sessionId": session_id, "messagingSocketPath": sock,
                   "startedAt": started_at, "cwd": "/tmp/x", "kind": "interactive"}, f)


# Where an inbox socket lives (2026-09-06): the run's private temp root — tempfile.gettempdir(),
# which tests/conftest.py points at a romp-tests-* directory it removes when the run ends — when the
# socket path fits in sun_path, and the system temp dir the run was handed before that redirect
# only when it would not. sun_path is 108 bytes on Linux and 104 on macOS, NUL included; an xdist
# worker's root nested under a macOS TMPDIR puts the socket at 116 bytes
# (/var/folders/../T/romp-tests-x/romp-tests-x/rompsockx/inbox.sock), the Linux equivalent under
# /tmp at 72 and under a TMPDIR of 60 bytes or more past 108. conftest records the handed dir once
# per run as ROMP_TESTS_SYSTEM_TMPDIR and a worker inherits the controller's record — not the
# controller's root, which under such a TMPDIR is itself too deep (recording that had four tests
# here failing at bind with "AF_UNIX path too long" under -n 2, 2026-09-06); without conftest,
# gettempdir() already is that dir. A handed dir too deep to hold the socket at all (about 80
# bytes) is refused before anything is minted there, loudly (ENAMETOOLONG naming the dir), as a bind
# under it would fail anyway; the refusal comes first because a caller registers close() only once
# the constructor returns, so a directory minted for a bind that then failed was one leaked
# rompsock* directory per test, per run (the round-2 review, 2026-09-06). Either way close()
# removes the directory. Before this the dir was pinned to "/tmp" and never removed: the pin
# bypassed the redirect and every run left three rompsock* directories behind.
_SUN_PATH_MAX = 104 if sys.platform == "darwin" else 108
_SOCK_NAME = "inbox.sock"


def _socket_dir():
    """The socket's directory: under the private root when the path fits sun_path, else under the
    dir the run was handed. Refuses (OSError, ENAMETOOLONG) BEFORE minting when the handed dir would
    not fit either, so a caller that never gets an inbox has nothing to remove."""
    d = tempfile.mkdtemp(prefix="rompsock")
    if len(os.fsencode(os.path.join(d, _SOCK_NAME))) < _SUN_PATH_MAX:
        return d
    os.rmdir(d)
    handed = os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or tempfile.gettempdir()
    if len(os.fsencode(os.path.join(handed, "rompsock" + "x" * 8, _SOCK_NAME))) >= _SUN_PATH_MAX:  # mkdtemp's suffix is 8 chars
        raise OSError(errno.ENAMETOOLONG, "the temp dir this run was handed is too deep for an AF_UNIX "
                      "socket path (%d-byte sun_path): %s" % (_SUN_PATH_MAX, handed))
    return tempfile.mkdtemp(prefix="rompsock", dir=handed)


class _OneShotInbox:
    """A real AF_UNIX listener capturing everything one client writes (the CLI parses per
    line and acks nothing for a plain send, so capture-and-close mirrors it exactly).
    close() removes the socket's directory; callers addCleanup it."""

    def __init__(self):
        self.dir = _socket_dir()
        self.path = os.path.join(self.dir, _SOCK_NAME)
        self.got = []
        self._srv = None
        try:
            self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._srv.bind(self.path)
            self._srv.listen(1)
            self._srv.settimeout(5.0)                  # can-never-trap backstop: a never-connected inbox's
            #                                            accept() returns instead of parking the thread
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        except BaseException:
            # Nothing after the mkdtemp may leave the directory standing: the caller registers
            # close() only once this returns, so a refused bind (a filesystem without AF_UNIX
            # support, a permission) or a thread that would not start had left one rompsock*
            # directory per test, per run, in the handed dir.
            if self._srv is not None:
                self._srv.close()
            shutil.rmtree(self.dir, ignore_errors=True)
            raise

    def _run(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:                                # includes the timeout (socket.timeout is an OSError)
            return
        conn.settimeout(5.0)
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        self.got.append(buf)
        conn.close()

    def wait(self, timeout=3.0):
        self._thread.join(timeout)
        return self.got

    def close(self):
        # shutdown THEN close, adjacent and in this order - load-bearing (T230c): on the timed-out
        # listener, shutdown alone makes poll() report the socket readable but accept4() returns
        # EAGAIN and CPython's accept loop keeps spinning until the timeout; it is the CLOSE that
        # ends it. Then join so no capture thread outlives its test (T230b hygiene).
        try:
            self._srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=1.0)
        shutil.rmtree(self.dir, ignore_errors=True)    # idempotent: a second close finds nothing


class InboxDir(unittest.TestCase):
    """_socket_dir's rule, and that an inbox leaves nothing behind."""

    def test_the_dir_is_under_the_private_root_when_the_path_fits_and_gone_after_close(self):
        inbox = _OneShotInbox()
        self.addCleanup(inbox.close)
        root = tempfile.gettempdir()
        self.assertLess(len(os.fsencode(inbox.path)), _SUN_PATH_MAX)
        fits_in_root = len(os.fsencode(os.path.join(root, os.path.basename(inbox.dir), _SOCK_NAME))) < _SUN_PATH_MAX
        self.assertEqual(os.path.dirname(inbox.dir),
                         root if fits_in_root else os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or root)
        self.assertTrue(os.path.exists(inbox.path))
        inbox.close()
        self.assertFalse(os.path.exists(inbox.dir), "close() removes the socket's directory")
        inbox.close()                                      # a second close is a no-op

    def test_a_path_too_long_for_the_private_root_falls_back_to_the_handed_dir(self):
        # "Handed" is the run's record, not this process's gettempdir(): in an xdist worker the
        # latter is the worker's root, nested in the controller's, which under a long TMPDIR is
        # itself too deep for the socket (122 bytes under a 52-byte TMPDIR, seen under -n 8).
        handed = os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or tempfile.gettempdir()
        if len(os.fsencode(os.path.join(handed, "rompsock" + "x" * 8, _SOCK_NAME))) >= _SUN_PATH_MAX:
            self.skipTest("the temp dir this run was handed is itself too deep for sun_path")
        deep = os.path.join(tempfile.gettempdir(), "d" * 120)   # deep + /rompsockXXXXXXXX/inbox.sock > 108
        os.mkdir(deep)
        self.addCleanup(shutil.rmtree, deep, ignore_errors=True)
        with patch.dict(os.environ, {"ROMP_TESTS_SYSTEM_TMPDIR": handed}), patch.object(tempfile, "tempdir", deep):
            d = _socket_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertEqual(os.path.dirname(d), handed)
        self.assertLess(len(os.fsencode(os.path.join(d, _SOCK_NAME))), _SUN_PATH_MAX)
        self.assertEqual(os.listdir(deep), [], "the too-long candidate was removed, not left behind")

    def test_a_handed_dir_too_deep_for_the_socket_is_refused_before_anything_is_minted(self):
        # Both candidates too deep. The constructor raises with the cause and the dir named, and
        # neither the root nor the handed dir holds a rompsock* entry: a caller that never got an
        # inbox has nothing to close, so a directory minted for a bind that then failed was one
        # leaked directory per test, per run.
        deep = os.path.join(tempfile.gettempdir(), "d" * 120)
        os.mkdir(deep)
        self.addCleanup(shutil.rmtree, deep, ignore_errors=True)
        with patch.dict(os.environ, {"ROMP_TESTS_SYSTEM_TMPDIR": deep}), patch.object(tempfile, "tempdir", deep):
            with self.assertRaises(OSError) as cm:
                _OneShotInbox()
        self.assertEqual(os.listdir(deep), [], "nothing is minted where no socket can bind")
        self.assertEqual(cm.exception.errno, errno.ENAMETOOLONG)
        self.assertIn(deep, str(cm.exception))

    def test_a_refused_bind_removes_the_minted_dir(self):
        # The length check above is the case the suite meets; a bind can be refused for other reasons
        # (a filesystem without AF_UNIX support, a permission). Stubbed here: the constructor
        # raises, and the directory _socket_dir minted for it is gone.
        handed = os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or tempfile.gettempdir()
        if len(os.fsencode(os.path.join(handed, "rompsock" + "x" * 8, _SOCK_NAME))) >= _SUN_PATH_MAX:
            self.skipTest("the temp dir this run was handed is itself too deep for sun_path")   # refused before any bind
        made = []
        real = _socket_dir

        def recording():
            made.append(real())
            return made[-1]
        with patch.object(sys.modules[__name__], "_socket_dir", recording), \
                patch.object(socket.socket, "bind", side_effect=OSError(errno.EACCES, "stub")):
            with self.assertRaises(OSError) as cm:
                _OneShotInbox()
        self.assertEqual(cm.exception.errno, errno.EACCES)
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]), "a refused bind leaves no rompsock* directory")

    def test_the_rule_is_exercised_under_the_limit_by_a_real_bind(self):
        # The constant is the platform's, not a guess: a socket at exactly _SUN_PATH_MAX - 1 bytes
        # binds and one at _SUN_PATH_MAX does not, which is what the length check encodes.
        with tempfile.TemporaryDirectory() as td:
            if len(os.fsencode(td)) + 2 >= _SUN_PATH_MAX:
                self.skipTest("the temp root itself is near sun_path's limit")
            for length, ok in ((_SUN_PATH_MAX - 1, True), (_SUN_PATH_MAX, False)):
                path = os.path.join(td, "s" * (length - len(td) - 1))
                self.assertEqual(len(os.fsencode(path)), length)
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    srv.bind(path)
                    bound = True
                except OSError:
                    bound = False
                finally:
                    srv.close()
                self.assertEqual(bound, ok, path)


class SocketRegistry(unittest.TestCase):
    """_messaging_socket_for: the ~/.claude/sessions registry join."""

    def setUp(self):
        self.reg = tempfile.mkdtemp(prefix="rompreg")
        os.environ["ROMP_CLAUDE_SESSIONS_DIR"] = self.reg

    def tearDown(self):
        os.environ.pop("ROMP_CLAUDE_SESSIONS_DIR", None)

    def test_matches_session_id(self):
        _registry_row(self.reg, 111, SID, "/tmp/cc-socks/111.sock")
        self.assertEqual(km._messaging_socket_for(SID), "/tmp/cc-socks/111.sock")

    def test_no_row_no_dir_and_malformed_rows(self):
        self.assertIsNone(km._messaging_socket_for(SID))            # empty registry
        with open(os.path.join(self.reg, "bad.json"), "w") as f:
            f.write("{not json")                                     # malformed → skipped
        _registry_row(self.reg, 222, "99999999-aaaa-bbbb-cccc-dddddddddddd", "/tmp/other.sock")
        self.assertIsNone(km._messaging_socket_for(SID))             # other session's row
        os.environ["ROMP_CLAUDE_SESSIONS_DIR"] = self.reg + "-missing"
        self.assertIsNone(km._messaging_socket_for(SID))             # registry dir absent

    def test_row_without_socket_is_not_a_hit(self):
        with open(os.path.join(self.reg, "333.json"), "w") as f:
            json.dump({"pid": 333, "sessionId": SID}, f)             # pre-2.1.224 CLI: no socket field
        self.assertIsNone(km._messaging_socket_for(SID))

    def test_prefers_live_pid_over_newer_dead_row(self):
        # A resumed conversation leaves the old (dead) pid's row behind under the same
        # session id; the join must pick the row whose pid is alive even when the stale
        # row looks newer.
        _registry_row(self.reg, os.getpid(), SID, "/tmp/cc-socks/alive.sock", started_at=1000)
        _registry_row(self.reg, DEAD_PID, SID, "/tmp/cc-socks/dead.sock", started_at=2000)
        self.assertEqual(km._messaging_socket_for(SID), "/tmp/cc-socks/alive.sock")


class SocketWrite(unittest.TestCase):
    """_socket_deliver: exactly one {"type":"user"} JSON line, and honest False on failure."""

    def test_writes_one_user_record(self):
        inbox = _OneShotInbox()
        self.addCleanup(inbox.close)
        self.assertTrue(km._socket_deliver(inbox.path, "mail body <!-- romp-msg-id: 1 -->"))
        got = inbox.wait()
        self.assertEqual(len(got), 1)
        rec = json.loads(got[0].decode("utf-8"))
        self.assertEqual(rec["type"], "user")
        self.assertEqual(rec["message"]["role"], "user")
        self.assertEqual(rec["message"]["content"], "mail body <!-- romp-msg-id: 1 -->")

    def test_false_when_socket_is_gone(self):
        self.assertFalse(km._socket_deliver("/tmp/rompsock-nonexistent/inbox.sock", "x"))


class _StubTmux(km.TmuxBackend):
    """TmuxBackend with every raw tmux touchpoint stubbed; records pane-path probes so the
    tests can assert which delivery leg ran."""

    def __init__(self, accept):
        self._accept = accept
        self.pane_calls = []

    def live_sessions(self):
        return {SID: {"state": "idle", "backend": "tmux"}}

    def show_var(self, name, var, t=2.5):
        return "1" if (var == "@romp-inbound-accept" and self._accept) else ""

    def pane_in_mode(self, name):
        self.pane_calls.append("pane_in_mode")
        return False

    def capture(self, name, colour=False):
        self.pane_calls.append("capture")
        return ""                                          # never a live ❯ prompt → pane path bails

    def send_keys(self, name, *keys):
        self.pane_calls.append("send_keys")


class DeliverGate(unittest.TestCase):
    """TmuxBackend.deliver: socket leg iff tagged; pane path otherwise and on socket failure."""

    def setUp(self):
        self.reg = tempfile.mkdtemp(prefix="rompreg")
        os.environ["ROMP_CLAUDE_SESSIONS_DIR"] = self.reg
        self._name_of = km._name_of
        km._name_of = lambda sid: "tsess"
        self._last_sid = km.jd._sdk_last_sid
        km.jd._sdk_last_sid = lambda sid: None             # tmux session: fsid IS the sid

    def tearDown(self):
        os.environ.pop("ROMP_CLAUDE_SESSIONS_DIR", None)
        km._name_of = self._name_of
        km.jd._sdk_last_sid = self._last_sid

    def test_tagged_session_delivers_down_the_socket(self):
        inbox = _OneShotInbox()
        self.addCleanup(inbox.close)
        _registry_row(self.reg, os.getpid(), SID, inbox.path)
        be = _StubTmux(accept=True)
        self.assertTrue(be.deliver(SID, "banner text"))
        got = inbox.wait()
        self.assertEqual(json.loads(got[0].decode("utf-8"))["message"]["content"], "banner text")
        self.assertEqual(be.pane_calls, [])                # the pane was never touched

    def test_untagged_session_keeps_the_pane_path(self):
        inbox = _OneShotInbox()
        self.addCleanup(inbox.close)
        _registry_row(self.reg, os.getpid(), SID, inbox.path)
        be = _StubTmux(accept=False)
        be.deliver(SID, "banner text")                     # pane stub has no ❯ prompt → undelivered
        time.sleep(0.2)
        self.assertEqual(inbox.got, [])                    # nothing reached the socket
        self.assertIn("capture", be.pane_calls)            # the pane path ran instead

    def test_tagged_but_dead_socket_falls_back_to_the_pane(self):
        _registry_row(self.reg, os.getpid(), SID, "/tmp/rompsock-nonexistent/inbox.sock")
        be = _StubTmux(accept=True)
        self.assertFalse(be.deliver(SID, "banner text"))   # pane stub can't inject either
        self.assertIn("capture", be.pane_calls)            # but it was TRIED — no silent drop


if __name__ == "__main__":
    unittest.main()
