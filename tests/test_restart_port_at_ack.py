#!/usr/bin/env python3
"""POST /restart resolves ROMP_MANAGER_PORT BEFORE the ack goes out (2026-08-27).

The read used to sit inside _restart_this_kernel, which runs only after the handler's _send
returned — so a caller that installed a value for exactly the duration of the request (a test
suite fencing a live self-hosted deployment off its real manager does precisely this: set, POST,
restore in `finally`) raced its restore against the handler thread, and the RESTORED value could
be the one read. On a machine whose environment carries a live manager port, losing that race
restarts the real deployment. The contract pinned here: the value in force when the kernel ACKS
is the value acted on.

Deterministic by construction — the "restore after the ack" is not a sleep this test could lose,
it is an event sequenced in the handler's own thread: _send is wrapped so the moment the ack is
written, the env flips to a dead port. Pre-fix the post-ack read sees the flipped (dead) value and
the recorded manager hears nothing; post-fix the pre-ack resolution governs and the manager hop
lands. Synthetic only: the real Handler and a recording fake manager, both on ephemeral loopback
ports, invented token.
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# A dead manager port at module level — NEVER pop: a pop here executes at collection time and
# erases any suite-wide floor for the rest of the run, and an ABSENT var is the one unsafe state
# (_run_main_update maps absent to the DEFAULT port: a live manager, if one is running).
os.environ["ROMP_MANAGER_PORT"] = "1"
km = load_source("romp_kernel_port_ack", os.path.join(BIN, "romp-kernel"))


class _RecordingManager(BaseHTTPRequestHandler):
    """A fake manager: records every POST path, answers 200. What a real manager's /restart-all
    door looks like to the kernel, minus the SIGTERM."""
    hits = []

    def do_POST(self):
        type(self).hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class RestartPortResolvedAtAck(unittest.TestCase):
    def setUp(self):
        _RecordingManager.hits = []
        self.mgr = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingManager)
        self.mgr_port = self.mgr.server_address[1]
        threading.Thread(target=self.mgr.serve_forever, daemon=True).start()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self._saved_env = os.environ.get("ROMP_MANAGER_PORT")
        self._real_send = km.Handler._send

    def tearDown(self):
        km.Handler._send = self._real_send
        if self._saved_env is None:
            os.environ.pop("ROMP_MANAGER_PORT", None)
        else:
            os.environ["ROMP_MANAGER_PORT"] = self._saved_env
        self.srv.shutdown()
        self.srv.server_close()
        self.mgr.shutdown()
        self.mgr.server_close()

    def test_the_value_in_force_at_the_ack_governs_the_restart(self):
        # The fake manager's port is installed only for the request window. The restore-after-ack
        # is sequenced ON the ack itself: the wrapped _send writes the response, then flips the env
        # to a dead port — in the handler's own thread, so the pre-fix read (which follows _send)
        # ALWAYS sees the flipped value, and the post-fix read (which precedes it) never does.
        os.environ["ROMP_MANAGER_PORT"] = str(self.mgr_port)
        real_send = self._real_send

        def send_then_restore(handler, *a, **kw):
            r = real_send(handler, *a, **kw)
            os.environ["ROMP_MANAGER_PORT"] = "1"   # the caller's `finally` restore, as an event
            return r

        km.Handler._send = send_then_restore
        req = urllib.request.Request(
            "http://127.0.0.1:%d/restart" % self.port,
            data=json.dumps({"fleet": False}).encode(),
            headers={"X-Romp-Token": km.TOKEN, "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertTrue(json.loads(r.read()).get("restarting"))
        # the manager hop runs after the ack — wait (bounded) for the handler thread to finish it
        deadline = time.time() + 10
        while not _RecordingManager.hits and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(_RecordingManager.hits, ["/restart-all"],
                         "the port resolved at the ack, not the restored one, takes the hop")


if __name__ == "__main__":
    unittest.main()
