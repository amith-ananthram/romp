#!/usr/bin/env python3
"""Previously-attached hosts persist in the network popover (the user 2026-07-22: past hosts should be
listed as entries you can act on, not something to re-find in the ssh-config dropdown).

A host you attach is remembered; detaching keeps the memory (with the trust level you last chose), so
re-attaching restores that level instead of silently dropping back to `directed`. list_known() reports
only hosts NOT currently attached (an attached host already has a live row). Forget drops one.

Synthetic only — hermetic temp STATE, placeholder hostnames.
"""
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
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _row(host, trust="directed"):
    return {"host": host, "kernel_port": 29855, "local_port": 5000, "bus_port": 5001, "token": "t",
            "proc": None, "status": "up", "detail": "", "sids": [], "trust": trust}


class KnownHostMemory(unittest.TestCase):
    def setUp(self):
        km._remotes.clear()
        km._known.clear()

    def test_detach_remembers_the_host_and_its_trust(self):
        km._remotes["TESTHOST"] = _row("TESTHOST", trust="trusted")
        km.detach_remote("TESTHOST")
        self.assertNotIn("TESTHOST", km._remotes, "detach still removes the live row")
        self.assertEqual(km.known_trust("TESTHOST"), "trusted",
                         "the level you chose must survive the detach")

    def test_known_list_excludes_currently_attached(self):
        km._remotes["A"] = _row("A")
        km._known_note("A")
        km._known_note("B")
        hosts = [k["host"] for k in km.list_known()]
        self.assertIn("B", hosts)
        self.assertNotIn("A", hosts, "an attached host already has a live row; listing it twice is noise")

    def test_set_trust_updates_the_remembered_level(self):
        km._remotes["TESTHOST"] = _row("TESTHOST")
        km.set_trust("TESTHOST", "isolated")
        km.detach_remote("TESTHOST")
        self.assertEqual(km.known_trust("TESTHOST"), "isolated")

    def test_forget_drops_it(self):
        km._known_note("TESTHOST")
        self.assertTrue(km.known_forget("TESTHOST"))
        self.assertEqual([k["host"] for k in km.list_known()], [])
        self.assertFalse(km.known_forget("TESTHOST"), "forgetting twice is a no-op, not an error")

    def test_memory_survives_a_kernel_restart(self):
        km._known_note("TESTHOST", "trusted")
        km._known.clear()                       # simulate a fresh process
        km._known_load()
        self.assertEqual(km.known_trust("TESTHOST"), "trusted")

    def test_unknown_host_has_no_remembered_trust(self):
        self.assertIsNone(km.known_trust("NEVER-SEEN"))

    def test_newest_first(self):
        km._known_note("OLD")
        km._known["OLD"]["lastAttachedAt"] = 1
        km._known_note("NEW")
        self.assertEqual([k["host"] for k in km.list_known()][0], "NEW")


class KnownHostRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = json.dumps(body) if body is not None else None
        c.request(method, path, data, {"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        r = c.getresponse()
        out = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, out

    def test_tunnels_payload_carries_known(self):
        km._remotes.clear(); km._known.clear()
        km._known_note("PASTHOST", "trusted")
        code, d = self._req("GET", "/tunnels")
        self.assertEqual(code, 200)
        self.assertIn("known", d)
        self.assertEqual([k["host"] for k in d["known"]], ["PASTHOST"])
        self.assertEqual(d["known"][0]["trust"], "trusted")

    def test_forget_route(self):
        km._remotes.clear(); km._known.clear()
        km._known_note("PASTHOST")
        code, d = self._req("POST", "/tunnels/forget", {"host": "PASTHOST"})
        self.assertEqual(code, 200)
        self.assertTrue(d["forgotten"])
        _, d2 = self._req("GET", "/tunnels")
        self.assertEqual(d2["known"], [])

    def test_forget_requires_a_host(self):
        code, d = self._req("POST", "/tunnels/forget", {})
        self.assertEqual(code, 400)
        self.assertFalse(d["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
