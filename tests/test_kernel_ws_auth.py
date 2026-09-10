#!/usr/bin/env python3
"""Regression guard for the romp-kernel serve-layer auth — the OpenClaw "ClawJacked"
class (CVE-2026-25253): a cross-site WebSocket upgrade MUST be rejected.

The kernel hand-rolls its WS server (bin/romp-kernel: do_GET -> _ws) and originally did
NO Origin validation, so any website the user opened in a browser could open
ws://127.0.0.1:<port>/ws and drive ROMP (inject prompts, switch model, interrupt, spawn
sessions). WebSockets are NOT covered by CORS, so the browser's same-origin policy does
not stop the SEND — the server itself must reject a foreign Origin.

These tests start the real Handler and drive raw WS handshakes:
  - foreign Origin (http://evil.example)          -> 403  (the guard; RED before the fix)
  - absent Origin (non-browser CLI/native client) -> 101  (must keep working)
  - same-origin Origin (http://127.0.0.1:<port>)  -> 101  (local web UI must keep working)

Synthetic only — no real session data; the upgrade decision is made before any session
state is touched.
"""
import base64
import os
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Mirror tests/test_kernel.py's load order. Set a token so _load_token() returns early
# (never touches the real state dir) and NO_OPEN so importing doesn't launch a browser.
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _ws_handshake(port, origin=None, host=None, path="/ws?app=chat", token=None, timeout=2.0):
    """Send one raw WebSocket upgrade; return the numeric HTTP status (e.g. 101, 403)."""
    if token is not None:
        path = path + ("&" if "?" in path else "?") + "token=" + token
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        "GET %s HTTP/1.1" % path,
        "Host: %s" % (host or ("127.0.0.1:%d" % port)),
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: %s" % key,
        "Sec-WebSocket-Version: 13",
    ]
    if origin is not None:
        lines.append("Origin: %s" % origin)
    req = ("\r\n".join(lines) + "\r\n\r\n").encode()
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(req)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        first = buf.split(b"\r\n", 1)[0].decode("latin-1")  # "HTTP/1.1 101 Switching Protocols"
        parts = first.split(" ", 2)
        return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
    finally:
        s.close()


class WsOriginGuard(unittest.TestCase):
    def setUp(self):
        # Ephemeral port; the per-request handler threads are daemon (ThreadingHTTPServer).
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_foreign_origin_rejected(self):
        # ClawJacked: victim's browser, local Host, but a cross-site Origin → must be refused.
        status = _ws_handshake(self.port, origin="http://evil.example")
        self.assertEqual(
            status, 403,
            "cross-site /ws upgrade must be rejected (got %s); see CVE-2026-25253" % status)

    def test_absent_origin_needs_token(self):
        # Non-browser client (CLI / native): the token is required even on loopback
        # (Jupyter's model — the 0600 token file, not the socket, is the same-user
        # boundary). Token-free → 403; with the token → upgrade.
        self.assertEqual(
            _ws_handshake(self.port, origin=None), 403,
            "a token-less loopback client must be refused (loopback is not a trust boundary)")
        self.assertEqual(
            _ws_handshake(self.port, origin=None, token=km.TOKEN), 101,
            "an absent-Origin client WITH the token must upgrade")

    def test_same_origin_accepted_with_token(self):
        # The local web UI: same-origin AND carrying the token (the served page always
        # has it — the page itself required ?token=/cookie to load) → allowed.
        origin = "http://127.0.0.1:%d" % self.port
        self.assertEqual(
            _ws_handshake(self.port, origin=origin), 403,
            "same-origin without a token must be refused")
        self.assertEqual(
            _ws_handshake(self.port, origin=origin, token=km.TOKEN), 101,
            "same-origin local UI with the token must upgrade")

    def test_valid_token_authorizes_foreign_origin(self):
        # FEDERATED dashboard: the browser is served by ANOTHER kernel, so its Origin is foreign
        # here, but it carries this kernel's token over the -L tunnel. The token IS the auth, so
        # it must upgrade despite the cross-site Origin.
        self.assertEqual(
            _ws_handshake(self.port, origin="http://localhost:9999", token=km.TOKEN), 101,
            "a valid token must authorize a tunnel'd /ws from a foreign-origin dashboard")

    def test_invalid_token_foreign_origin_rejected(self):
        # The bypass requires a VALID token — a wrong token + foreign Origin is still ClawJacked.
        self.assertEqual(
            _ws_handshake(self.port, origin="http://evil.example", token="wrong-token"), 403,
            "only a valid token bypasses the Origin gate; a wrong token must not")


if __name__ == "__main__":
    unittest.main(verbosity=2)
