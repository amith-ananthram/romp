#!/usr/bin/env python3
"""The /remote/<host>/ws relay — how an off-machine dashboard (the phone) sees federated hosts.

The federated dashboard merges hosts IN THE BROWSER: one WebSocket per kernel. The remote dial
used to go straight to 127.0.0.1:<ssh -L forwarded port>, an address that only exists on the
kernel's own machine — from a phone reading the dashboard through `tailscale serve`, that is the
phone's own loopback, so every remote host's sessions silently vanished with no disconnected mark
(2026-07-30). The kernel now relays: GET /remote/<host>/ws splices the browser's socket onto the
host's forwarded port byte-for-byte, after the normal local auth gate, and rewrites the remote
kernel's own token into the forwarded query so the per-host trust boundary is unchanged.

These tests run the real Handler against a fake "remote kernel" (a raw loopback socket that speaks
just enough of the WS handshake). Synthetic only: host name `gpu1`, invented tokens, no session
state touched.
"""
import base64
import os
import re
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source

from tests.conftest import thread_census, wait_for_census
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Mirror tests/test_kernel_ws_auth.py's load order. The token env keeps _load_token() away from
# the real state dir; NO_OPEN keeps the import from launching a browser.
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

REMOTE_TOKEN = "remote-token-DO-NOT-USE"


class _FakeRemoteKernel:
    """One-connection stand-in for an attached host's kernel behind the ssh -L port: accepts a WS
    upgrade (computing the accept from the FORWARDED key — the proof headers passed through),
    sends one server frame, then records whatever else arrives."""

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.request = b""          # the upgrade request the relay forwarded
        self.post_upgrade = b""     # client bytes that arrived after the upgrade
        self.got_client_bytes = threading.Event()
        self.done = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            conn, _ = self.srv.accept()
            conn.settimeout(5)
            while b"\r\n\r\n" not in self.request:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                self.request += chunk
            key = re.search(rb"Sec-WebSocket-Key: *(\S+)", self.request).group(1).decode()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % km._ws_accept(key)
            ).encode())
            conn.sendall(b"\x81\x05hello")          # one unmasked text frame, server → client
            head, _, tail = self.request.partition(b"\r\n\r\n")
            self.post_upgrade += tail               # client frames that rode in with the upgrade
            while not self.got_client_bytes.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    return
                if not chunk:
                    return
                self.post_upgrade += chunk
                self.got_client_bytes.set()
        except OSError:
            pass
        finally:
            self.done.set()

    def close(self):
        # End the serve thread (T282). A fake nobody dialed is parked in accept(): closing the listening socket
        # from another thread does not wake it (on Linux shutdown() does, on macOS/BSD neither does), so a
        # throwaway dial that ends at once wakes it portably: accept() returns, recv() sees the EOF, the thread
        # ends. Then shut down and close the listener and wait for the thread's exit, bounded.
        if not self.done.is_set():
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    pass
            except OSError:
                pass
        for op in (lambda: self.srv.shutdown(socket.SHUT_RDWR), self.srv.close):
            try:
                op()
            except OSError:
                pass
        self.done.wait(5)


class RemoteWsProxy(unittest.TestCase):
    def setUp(self):
        self._census0 = thread_census()
        self.addCleanup(lambda: self.assertEqual(wait_for_census(self._census0), [],
                                                 "no thread of this test outlives it (T282)"))   # runs LAST
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self._saved_remotes = dict(km._remotes)

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes.update(self._saved_remotes)
        self.srv.shutdown()
        self.srv.server_close()

    def _register(self, host, local_port, token=REMOTE_TOKEN):
        with km._remotes_lock:
            km._remotes[host] = {"host": host, "kernel_port": 29855, "local_port": local_port,
                                 "token": token, "status": "up"}

    def _upgrade(self, path, token=True, ws_headers=True):
        """Open a raw socket, send one upgrade request, return (socket, status, header block)."""
        key = base64.b64encode(os.urandom(16)).decode()
        lines = ["GET %s HTTP/1.1" % path, "Host: 127.0.0.1:%d" % self.port]
        if token:
            lines.append("X-Romp-Token: %s" % km.TOKEN)
        if ws_headers:
            lines += ["Upgrade: websocket", "Connection: Upgrade",
                      "Sec-WebSocket-Key: %s" % key, "Sec-WebSocket-Version: 13"]
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, tail = buf.partition(b"\r\n\r\n")
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = first.split(" ", 2)
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
        return s, status, head, tail, key

    def test_splices_both_directions_and_rewrites_the_token(self):
        fake = _FakeRemoteKernel()
        self.addCleanup(fake.close)
        self._register("gpu1", fake.port)
        try:
            s, status, head, tail, key = self._upgrade("/remote/gpu1/ws?app=chat&token=whatever-the-browser-sent")
            try:
                self.assertEqual(status, 101, "the remote's 101 must pass through the splice")
                # the accept was computed by the FAKE REMOTE from our key — headers passed through
                self.assertIn(km._ws_accept(key).encode(), head)
                # server → client: the remote's frame arrives verbatim
                buf = tail
                s.settimeout(5)
                while len(buf) < 7:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                self.assertEqual(buf[:7], b"\x81\x05hello")
                # client → server: bytes flow back through the same splice untouched
                s.sendall(b"\x81\x83\x01\x02\x03\x04abc")
                self.assertTrue(fake.got_client_bytes.wait(5), "client bytes must reach the remote")
                self.assertIn(b"\x81\x83\x01\x02\x03\x04abc", fake.post_upgrade)
                # the forwarded request: path /ws, the REMOTE's token (not what the browser sent),
                # and never the local serve token
                req = fake.request.split(b"\r\n", 1)[0].decode("latin-1")
                self.assertTrue(req.startswith("GET /ws?"), req)
                self.assertIn("token=" + REMOTE_TOKEN, req)
                self.assertNotIn("whatever-the-browser-sent", req)
                self.assertNotIn(km.TOKEN, fake.request.decode("latin-1"))
                self.assertIn("app=chat", req)
            finally:
                s.close()
        finally:
            fake.close()

    def _forwarded_query(self, host, browser_path):
        """Dial the relay for `host` with `browser_path` and return the query the FAR side received, parsed."""
        from urllib.parse import parse_qs, urlsplit
        fake = _FakeRemoteKernel()
        self.addCleanup(fake.close)
        try:
            self._register(host, fake.port, token=km._remotes[host]["token"] if host in km._remotes else REMOTE_TOKEN)
            s, status, _, _, _ = self._upgrade(browser_path)
            try:
                self.assertEqual(status, 101, "the relay must splice before the query can be judged")
                self.assertTrue(fake.done.wait(0.1) or fake.request, "the far side saw the request")
            finally:
                s.close()
            req = fake.request.split(b"\r\n", 1)[0].decode("latin-1")
            self.assertTrue(req.startswith("GET /ws?"), req)
            return parse_qs(urlsplit(req.split(" ")[1]).query)
        finally:
            fake.close()

    def test_a_row_without_a_stored_token_forwards_no_token_at_all(self):
        # The pop is unconditional (2026-09-08): with no credential of its own to inject, the relay used to
        # let whatever the browser sent ride through to the far kernel. Mutant this kills: delete the
        # `q.pop("token", None)` and `token=evil` reaches the far side.
        with km._remotes_lock:
            km._remotes["gpu2"] = {"host": "gpu2", "kernel_port": 29855, "local_port": 0, "token": "", "status": "up"}
        q = self._forwarded_query("gpu2", "/remote/gpu2/ws?app=chat&token=evil")
        self.assertNotIn("token", q, "nothing the browser sent may stand in for a credential: %r" % q)
        self.assertEqual(q.get("app"), ["chat"], "the rest of the query still travels")

    def test_a_row_with_a_token_forwards_exactly_that_one(self):
        q = self._forwarded_query("gpu1", "/remote/gpu1/ws?app=chat&token=evil&token=evil2")
        self.assertEqual(q.get("token"), [REMOTE_TOKEN], "exactly the stored credential, once — never the browser's")
        self.assertEqual(q.get("app"), ["chat"])

    def test_unknown_host_404s(self):
        s, status, _, _, _ = self._upgrade("/remote/nosuch/ws")
        s.close()
        self.assertEqual(status, 404)

    def test_unauthorized_403s_before_any_dial(self):
        fake = _FakeRemoteKernel()
        self.addCleanup(fake.close)
        self._register("gpu1", fake.port)
        try:
            s, status, _, _, _ = self._upgrade("/remote/gpu1/ws", token=False)
            s.close()
            self.assertEqual(status, 403, "the local auth gate must run before the relay")
            self.assertEqual(fake.request, b"", "an unauthorized request must never touch the tunnel")
        finally:
            fake.close()

    def test_dead_tunnel_502s(self):
        # a registered host whose forwarded port has no listener (the ssh died mid-flight)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        self._register("gpu1", dead_port)
        s, status, _, _, _ = self._upgrade("/remote/gpu1/ws")
        s.close()
        self.assertEqual(status, 502)

    def test_non_websocket_request_400s(self):
        fake = _FakeRemoteKernel()
        self.addCleanup(fake.close)
        self._register("gpu1", fake.port)
        try:
            s, status, _, _, _ = self._upgrade("/remote/gpu1/ws", ws_headers=False)
            s.close()
            self.assertEqual(status, 400)
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
