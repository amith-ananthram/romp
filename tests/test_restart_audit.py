#!/usr/bin/env python3
"""Every kernel-restart door writes WHO ASKED to restart-audit.jsonl (2026-07-31).

A run of anonymous SIGTERM respawns blinked every dashboard for an hour, and nothing on disk could
name the caller: the CLI path (bin/romp) audits itself, but the HTTP /restart route and the
kernel→manager hop were silent. Now the route records the requester (address/origin/user-agent) and
_restart_this_kernel records its reason, in the SAME file, so one log answers "who restarted the
kernel?" whatever door the request came through.

Synthetic only: the real Handler on an ephemeral port, no manager (ROMP_MANAGER_PORT unset → the
restart itself is a no-op), invented tokens.
"""
import json
import os
import time
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
os.environ["ROMP_MANAGER_PORT"] = "1"   # dead port → _restart_this_kernel audits, then its dial is
#   refused (its except: pass). NEVER pop: pytest imports this module at COLLECTION, so a pop here
#   would erase conftest's suite-wide floor before any test runs — and an ABSENT var is the one
#   unsafe state (_run_main_update maps absent to the DEFAULT port: the live manager).
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

def _audit_path():
    return jd.STATE / "restart-audit.jsonl"   # at CALL time — peer test modules rebind jd.STATE


def _read_audit():
    try:
        return [json.loads(x) for x in _audit_path().read_text().splitlines() if x.strip()]
    except OSError:
        return []


class RestartAudit(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        try:
            _audit_path().unlink()
        except OSError:
            pass

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_http_restart_route_records_the_requester(self):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/restart" % self.port,
            data=json.dumps({"fleet": False}).encode(),
            headers={"X-Romp-Token": km.TOKEN, "Origin": "http://127.0.0.1:9",
                     "User-Agent": "test-agent/1.0", "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
        self.assertTrue(body.get("restarting"))
        # the route ACKS FIRST and audits the manager hop after — poll briefly so the assertion
        # doesn't race the handler thread (CI hit this; the ack/restart order is the designed one)
        recs = []
        for _ in range(200):
            recs = _read_audit()
            if any(r.get("action") == "kernel-asks-manager-restart-all" for r in recs):
                break
            time.sleep(0.01)
        route = [r for r in recs if r.get("action") == "http-restart"]
        self.assertEqual(len(route), 1, "the HTTP door must write exactly one audit line: %r" % recs)
        self.assertEqual(route[0].get("addr"), "127.0.0.1")
        self.assertEqual(route[0].get("origin"), "http://127.0.0.1:9")
        self.assertEqual(route[0].get("ua"), "test-agent/1.0")
        self.assertNotIn("fleet", route[0], "fleet:false is omitted (falsy fields are dropped)")
        # the manager hop audits its reason too — the two lines together tell the order of events
        hop = [r for r in recs if r.get("action") == "kernel-asks-manager-restart-all"]
        self.assertEqual(len(hop), 1, "the kernel→manager hop must record its reason: %r" % recs)
        self.assertIn("http /restart", hop[0].get("reason") or "")

    def test_restart_this_kernel_audits_even_with_no_manager(self):
        km._restart_this_kernel("unit-test reason")
        recs = _read_audit()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["action"], "kernel-asks-manager-restart-all")
        self.assertEqual(recs[0]["reason"], "unit-test reason")
        self.assertEqual(recs[0]["pid"], os.getpid())

if __name__ == "__main__":
    unittest.main()
