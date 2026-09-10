#!/usr/bin/env python3
"""POST /interrupt and /end on a session that lives on ANOTHER kernel (a remote attached over its -L
tunnel): the far kernel's answer is the answer, and /end's deferral crosses the wire.

The route's remote arm forwarded `{"id": sid}` and answered ok:true without reading the reply. Two
consequences from a hub: `romp end <remote-session>` against a box whose tunnel had just dropped
printed ok and exited 0 while the runaway kept running (the forward returns None on a dead tunnel,
the very signal the /send arm already turns into ok:false); and `romp end <remote-session>
--when-idle` killed the far session mid-turn, because `when: idle` never left the hub. The arm now
wears the /send arm's shape: carry `when`, answer ok:false naming the host when the far kernel does
not answer, pass a far ok:false back verbatim, otherwise pass the far reply back so its `deferred`
flag rides with it.

Scope: requests that address the session by its ID, the shape bin/romp sends for a sid and what the
forward carries. A remote session addressed by NAME still resolves locally: _sid_of reads the names
registry, the live sessions and the thread names, never the polled remote names, so `romp end
<remote-name>` takes the local arm before and after this change alike. That is the hub's
pre-existing addressing model, shared by /send and /compact, and a separate defect.

Real Handler on loopback (the tests/test_kernel_headless_ops.py pattern); only the two remote seams
are stubbed (_host_for_sid, _remote_forward), and the local backend is a tripwire — a remote session
must never reach it. SYNTHETIC fixtures only: host TESTHOST, an invented sid.
"""
import json
import os
import tempfile
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_remote_end", os.path.join(BIN, "romp-kernel"))

SID = "sid-r"
FAR = {"host": "TESTHOST", "local_port": 1, "token": "t"}


class RemoteEndInterrupt(unittest.TestCase):
    """POST /interrupt and /end for a sid _host_for_sid places on TESTHOST, over the REAL handler."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def _forward(self, path, body, far_reply):
        """POST with the remote seams stubbed: returns (code, reply, what crossed the wire)."""
        crossed = []

        def rec(r, p, b):
            crossed.append((r, p, b))
            return far_reply

        def no_local(sid):
            self.fail("a remote session's control must not touch the local backend")

        with mock.patch.object(km, "_host_for_sid", lambda sid: dict(FAR)), \
             mock.patch.object(km, "_remote_forward", rec), \
             mock.patch.object(km.Sessions, "backend_for", staticmethod(no_local)):
            code, resp = self._post(path, body)
        self.assertEqual(len(crossed), 1, "exactly one forward over the tunnel")
        return code, resp, crossed[0]

    def test_end_when_idle_crosses_the_wire_and_a_dead_tunnel_is_not_an_ok(self):
        code, resp, (r, path, body) = self._forward("/end", {"id": SID, "when": "idle"}, None)
        self.assertEqual((r["host"], path), ("TESTHOST", "/end"))
        self.assertEqual(body, {"id": SID, "when": "idle"},
                         "the deferral rides to the far kernel; a plain id would kill the session mid-turn")
        self.assertEqual(code, 200)
        self.assertIs(resp.get("ok"), False, "a dead tunnel is not an ok — the runaway is still running")
        self.assertIn("TESTHOST", resp.get("error", ""), "the reply names the host that isn't answering")
        self.assertIn("not ended", resp.get("error", ""))

    def test_interrupt_on_a_dead_tunnel_is_not_an_ok(self):
        code, resp, (r, path, body) = self._forward("/interrupt", {"id": SID}, None)
        self.assertEqual((path, body), ("/interrupt", {"id": SID}), "no deferral asked, none carried")
        self.assertEqual(code, 200)
        self.assertIs(resp.get("ok"), False)
        self.assertIn("TESTHOST", resp.get("error", ""))
        self.assertIn("not interrupted", resp.get("error", ""))

    def test_a_far_refusal_rides_back_verbatim(self):
        refusal = {"ok": False, "error": "x"}
        code, resp, _ = self._forward("/end", {"id": SID}, dict(refusal))
        self.assertEqual((code, resp), (200, refusal), "never rewritten into an ok")

    def test_the_far_deferred_flag_rides_back(self):
        code, resp, (_, _, body) = self._forward("/end", {"id": SID, "when": "idle"},
                                                 {"ok": True, "deferred": True})
        self.assertEqual(body, {"id": SID, "when": "idle"})
        self.assertEqual((code, resp), (200, {"ok": True, "deferred": True}),
                         "the caller reads the far kernel's answer, deferral and all")
        # a plain end carries no `when`, and the far kernel's plain ok comes back as itself
        code, resp, (_, _, body) = self._forward("/end", {"id": SID}, {"ok": True})
        self.assertEqual(body, {"id": SID})
        self.assertEqual((code, resp), (200, {"ok": True}))


if __name__ == "__main__":
    unittest.main()
