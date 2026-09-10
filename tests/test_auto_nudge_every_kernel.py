#!/usr/bin/env python3
"""Auto Nudge is one switch for every attached machine, but each kernel stores its own copy.

The user 2026-08-14: Auto Nudge was switched off in the dashboard and the sessions running on the other
machine went on being nudged for days. Two halves to that, and this file covers the kernel's:

  ROUTING (ui/webview/federation.ts, pinned in multi-kernel-merge.test.ts) — setAutoNudge carries no
  session id, so it went to the local kernel alone and no remote ever heard it.

  DISPLAY (here) — the gear fills its box from /version, which answers for the kernel serving the page.
  A machine that disagreed was invisible: the box read unchecked while that kernel kept nudging. The
  /version poll the supervisor already runs per pass now carries each remote's own setting onto its
  /tunnels row, so the gear can say who differs instead of speaking for machines it cannot see.

An older remote kernel has no such field, and "didn't say" must not read as "off" — that would invent a
disagreement and, worse, invite a click that changes a setting nobody asked about. It stays None.

Synthetic only — placeholder host/token, a throwaway loopback server for the poll, no real state.
"""
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
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_autonudge_hosts", os.path.join(BIN, "romp-kernel"))


def _row(**kw):
    r = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 51000, "bus_port": 51001,
         "token": "tok", "trust": "trusted", "status": "up", "detail": "", "sids": [],
         "fails": 0, "next_try": 0, "kernel_sha": "abc1234", "proc": None}
    r.update(kw)
    return r


class _Fake(BaseHTTPRequestHandler):
    """A stand-in remote kernel that answers /version with whatever PAYLOAD holds."""
    PAYLOAD = {}

    def do_GET(self):
        body = json.dumps(self.PAYLOAD).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class RemoteRowCarriesItsOwnSetting(unittest.TestCase):
    def test_the_public_row_carries_the_remote_s_setting(self):
        for val in (True, False):
            with self.subTest(autoNudge=val):
                self.assertIs(km._remote_public(_row(auto_nudge=val))["autoNudge"], val,
                              "the gear reads each machine's own answer off its row")

    def test_a_remote_that_never_reported_reads_as_unknown_not_off(self):
        # An older kernel has no autoNudge in /version, and a row polled zero times has nothing cached.
        # Either way the honest answer is null: the gear leaves an unknown host out of the comparison
        # rather than inventing a disagreement (and a click) over a setting it never learned.
        self.assertIsNone(km._remote_public(_row())["autoNudge"])
        self.assertIsNone(km._remote_public(_row(auto_nudge=None))["autoNudge"])

    def test_a_non_boolean_never_reaches_the_gear(self):
        # /version is whatever the far side chose to send; the row must not pass a string or a number
        # through to a checkbox, where "0" or "off" would both read as truthy on the way in.
        for junk in ("false", 0, 1, [], {}):
            with self.subTest(value=junk):
                self.assertIsNone(km._remote_public(_row(auto_nudge=junk))["autoNudge"])


class ThePollReadsIt(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _poll(self, payload):
        _Fake.PAYLOAD = payload
        return km._poll_remote_version({"local_port": self.port, "token": "tok"})

    def test_the_version_poll_brings_the_setting_back_with_the_sha(self):
        got = self._poll({"kernel_sha": "abc1234", "kernel_ver": "v0.5.0", "autoNudge": False})
        self.assertEqual(got["sha"], "abc1234")
        self.assertIs(got["autoNudge"], False, "False is a real answer, not a missing one")
        self.assertIs(self._poll({"kernel_sha": "abc1234", "autoNudge": True})["autoNudge"], True)

    def test_an_older_kernel_that_omits_the_field_polls_as_unknown(self):
        got = self._poll({"kernel_sha": "abc1234", "kernel_ver": "v0.4.0"})
        self.assertEqual(got["sha"], "abc1234", "the sha still lands — the field is additive")
        self.assertIsNone(got["autoNudge"])


class ThisKernelPublishesIt(unittest.TestCase):
    def test_version_reports_this_kernel_s_own_setting(self):
        # The other half of the same wire: what a remote polls off US. /version is the gear's source for
        # the local box too, so both ends of the comparison come from one field.
        for val in (True, False):
            with self.subTest(enabled=val):
                km._set_auto_nudge(val)
                self.assertIs(km._version_info()["autoNudge"], val)


if __name__ == "__main__":
    unittest.main()
