#!/usr/bin/env python3
"""Attach-on-behalf (the user 2026-07-25): POST /tunnels with {"from": <attached host>} forwards the
attach to THAT machine's kernel over its -L tunnel, so an always-on box can OWN a tunnel while you sit
at the laptop. The far kernel judges ssh reachability itself; this side only relays and bubbles the
answer, loudly when the from-host is not attached/up."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_aob", os.path.join(BIN, "romp-kernel"))


def _row(host, **extra):
    r = {"host": host, "kernel_port": 29855, "local_port": 5000, "bus_port": 5001, "token": "t",
         "proc": None, "status": "up", "detail": "", "sids": [], "trust": "directed"}
    r.update(extra)
    return r


class AttachOnBehalf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        km._remotes.clear()

    def _post(self, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/tunnels", json.dumps(body),
                  {"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        r = c.getresponse()
        data = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, data

    def test_forwards_to_the_owning_kernel_and_bubbles_its_answer(self):
        km._remotes["HUB"] = _row("HUB")
        calls = []
        saved = km._remote_forward
        km._remote_forward = (lambda r, path, body:
                              calls.append((r["host"], path, dict(body))) or
                              {"ok": True, "tunnel": {"host": body["host"], "status": "starting"}})
        try:
            code, data = self._post({"host": "FAR", "from": "HUB"})
        finally:
            km._remote_forward = saved
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["via"], "HUB", "the answer names which machine owns the tunnel")
        self.assertEqual(data["tunnel"]["host"], "FAR")
        self.assertEqual(calls, [("HUB", "/tunnels", {"host": "FAR"})],
                         "exactly one relay, to the from-host's kernel, attach body only")

    def test_from_host_must_be_attached_and_up(self):
        code, data = self._post({"host": "FAR", "from": "GHOST"})
        self.assertEqual(code, 200)
        self.assertFalse(data["ok"])
        self.assertIn("not an attached, connected host", data["error"])
        km._remotes["HUB"] = _row("HUB", status="down")
        code, data = self._post({"host": "FAR", "from": "HUB"})
        self.assertFalse(data["ok"], "a down from-host is refused loudly, never queued")

    def test_far_kernel_silence_is_loud(self):
        km._remotes["HUB"] = _row("HUB")
        saved = km._remote_forward
        km._remote_forward = lambda r, path, body: None
        try:
            code, data = self._post({"host": "FAR", "from": "HUB"})
        finally:
            km._remote_forward = saved
        self.assertEqual(code, 200)
        self.assertFalse(data["ok"])
        self.assertIn("didn't answer", data["error"])


if __name__ == "__main__":
    unittest.main()
