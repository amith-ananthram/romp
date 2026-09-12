#!/usr/bin/env python3
"""The kernel's side of handing fed-and-lost mail back to the bus: kernel._bus_restore_mail POSTs /restore to the
local bus with the session id and the message ids, behind the serve token, and returns the set of ids the bus put
back; a bus that could not be asked, or that refused, RAISES so the backend re-heads the banner instead of dropping
it (SdkSession._return_stranded_mail, tests/test_postal_stranded_putback.py). The hook is installed on the SDK
backend at kernel boot as `postal_restore`. Synthetic fixtures only; a fake bus stands in on a loopback port."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
MID1 = "1700000000.111111.TESTHOST"
MID2 = "1700000001.222222.TESTHOST"


class _FakeBus:
    """A loopback bus answering /restore with a canned status and body, recording what it was asked."""

    def __init__(self, status=200, body=None):
        self.seen = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.seen.append((self.path, dict(self.headers), json.loads(self.rfile.read(n) or b"{}")))
                data = json.dumps(body if body is not None else {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class BusRestoreMail(unittest.TestCase):
    def setUp(self):
        self._port = km.BUS_PORT

    def tearDown(self):
        km.BUS_PORT = self._port

    def test_posts_restore_behind_the_token_and_returns_what_the_bus_put_back(self):
        bus = _FakeBus(200, {"ok": True, "restored": [MID1], "missing": [MID2]})
        try:
            km.BUS_PORT = bus.port
            self.assertEqual(km._bus_restore_mail(SID, [MID1, MID2]), {MID1},
                             "the answer is the set the bus put back; the missing id is not invented into it")
        finally:
            bus.close()
        path, headers, body = bus.seen[0]
        self.assertEqual(path, "/restore")
        self.assertEqual(body, {"id": SID, "mids": [MID1, MID2]})
        self.assertEqual(headers.get("X-Romp-Token"), km.TOKEN, "the bus's serve-token gate is honoured")

    def test_a_refusal_raises_rather_than_reading_as_nothing_restored(self):
        for status, body in ((403, {"error": "token required"}), (400, {"ok": False, "error": "bad ask"}),
                             (200, {"ok": False}), (200, "not an object")):
            bus = _FakeBus(status, body)
            try:
                km.BUS_PORT = bus.port
                with self.assertRaises(Exception, msg=(status, body)):
                    km._bus_restore_mail(SID, [MID1])
            finally:
                bus.close()

    def test_an_unreachable_bus_raises(self):
        bus = _FakeBus()
        port = bus.port
        bus.close()                                       # nothing listens here now
        km.BUS_PORT = port
        with self.assertRaises(Exception):
            km._bus_restore_mail(SID, [MID1])

    def test_the_hook_is_what_boot_installs_on_the_backend(self):
        # The install line at boot: `_sdk_backend.postal_restore = _bus_restore_mail`. Read from the source rather
        # than booting a kernel: the boot path spawns real processes.
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("_sdk_backend.postal_restore = _bus_restore_mail", src)


if __name__ == "__main__":
    unittest.main()
