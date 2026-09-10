#!/usr/bin/env python3
"""User demand re-dials a downed tunnel NOW instead of waiting out the backoff ladder (2026-08-16).

On flaky wifi the ladder ratchets fast — 300s after ~5 dials, 900s after 12 — and nothing a user did
reset it: they sat tapping retry on a remote image while the dialer slept out its timer. Every data
path that hits a dead tunnel is now DEMAND, the event that clears the ladder and wakes the supervisor
(mirroring the network panel's own Try-now): the preview/download relays, the remote-WS splice, the
forwarded control ops, the postal bus parking mail (via POST /redial), and the composer's refused
send (via the redial WS op). Evidence-proportional: a REFUSED local port means the ssh is dead or
dying (a zombie is terminated so the fresh dial owns the ports); a TIMEOUT through an accepted
connection counts as one silent poll — the same evidence class the supervisor's own probe files — so
its immediate pass decides. A dial already in flight is left to finish: one fresh dial per demand,
never a storm. The relay's 502 now says "re-dialing now", which flows verbatim into the chat's
preview failure notes — the in-place indication. SYNTHETIC hosts only (TESTHOST)."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
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

TOKEN = os.environ["ROMP_SERVE_TOKEN"]


class _FakeProc:
    def __init__(self):
        self.terminated = 0
    def poll(self):
        return None                                    # alive
    def terminate(self):
        self.terminated += 1


def _keep_remotes(case):
    """Restore the kernel's `_remotes` map after `case`: the module is ONE object per process for every
    test file that loads it under this name, so a TESTHOST row planted here otherwise outlives the
    test — under xdist a whole-map reader in another file (test_kernel_trust's PairsSnapshot) found
    it (2026-09-02)."""
    saved = dict(km._remotes)
    case.addCleanup(lambda: (km._remotes.clear(), km._remotes.update(saved)))


class DemandRedial(unittest.TestCase):
    def setUp(self):
        _keep_remotes(self)
        km._remotes.clear()
        km._tunnel_wake.clear()

    def _row(self, **kw):
        r = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 1, "token": "",
             "status": "down", "fails": 7, "next_try": 9e12, "misses": 0, "proc": None}
        r.update(kw)
        km._remotes["TESTHOST"] = r
        return r

    def test_demand_clears_the_ladder_and_wakes_the_supervisor(self):
        r = self._row()
        km._demand_redial("TESTHOST", "timeout")
        self.assertEqual((r["fails"], r["next_try"]), (0, 0), "the demand IS the reset event")
        self.assertTrue(km._tunnel_wake.is_set(), "the supervisor dials now, not at its next pass")

    def test_a_refused_port_puts_a_zombie_ssh_down(self):
        proc = _FakeProc()
        r = self._row(proc=proc, status="up")
        km._demand_redial("TESTHOST", "refused")
        self.assertEqual(proc.terminated, 1, "the listener refusing means the ssh is dead or dying")
        self.assertEqual(r["fails"], 0)

    def test_a_timeout_counts_as_one_silent_poll_never_a_kill(self):
        proc = _FakeProc()
        r = self._row(proc=proc, status="up")
        km._demand_redial("TESTHOST", "timeout")
        self.assertEqual(proc.terminated, 0, "an accepted-but-starved connection may just be a slow remote")
        self.assertEqual(r["misses"], km.STALE_MISSES - 1,
                         "one more silent poll on the woken pass tears down; an answered one clears")

    def test_a_dial_in_flight_is_left_to_finish(self):
        r = self._row(proc=_FakeProc(), status="starting", fails=3, next_try=123.0)
        km._demand_redial("TESTHOST", "refused")
        self.assertEqual((r["fails"], r["next_try"]), (3, 123.0), "one fresh dial per demand, never a storm")
        self.assertFalse(km._tunnel_wake.is_set())

    def test_unknown_and_checkin_peer_hosts_are_quiet_no_ops(self):
        km._demand_redial("NOSUCH", "timeout")
        self.assertFalse(km._tunnel_wake.is_set())
        r = self._row(checkin_peer=True)
        km._demand_redial("TESTHOST", "refused")
        self.assertEqual(r["fails"], 7, "we own no proc for a checkin peer — nothing to dial")


class DemandDoors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _keep_remotes(self)
        km._remotes.clear()
        km._tunnel_wake.clear()
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 1,
                                   "token": "", "status": "down", "fails": 5, "next_try": 9e12,
                                   "misses": 0, "proc": None}

    def _req(self, path, method="GET", body=None):
        url = "http://127.0.0.1:%d%s%stoken=%s" % (self.port, path, "&" if "?" in path else "?", TOKEN)
        req = urllib.request.Request(url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_the_dead_relay_demands_and_says_so(self):
        # local_port 1 refuses instantly → the 502 names the plan, and the ladder is cleared
        code, out = self._req("/remote/TESTHOST/file?path=/tmp/x.png")
        self.assertEqual(code, 502)
        self.assertIn(b"re-dialing now", out, "the body flows verbatim into the chat's failure note")
        r = km._remotes["TESTHOST"]
        self.assertEqual((r["fails"], r["next_try"]), (0, 0))
        self.assertTrue(km._tunnel_wake.is_set())

    def test_the_redial_route_is_the_demand_door_for_external_consumers(self):
        code, out = self._req("/redial", method="POST", body={"host": "TESTHOST"})
        self.assertEqual(code, 200)
        self.assertEqual((km._remotes["TESTHOST"]["fails"], km._remotes["TESTHOST"]["next_try"]), (0, 0))
        self.assertTrue(km._tunnel_wake.is_set())
        code, _ = self._req("/redial", method="POST", body={"host": "NOSUCH"})
        self.assertEqual(code, 200, "an unknown host is a quiet no-op — never an error on top of a failure")

    def test_postal_parking_pokes_the_door(self):
        src = open(os.path.join(os.path.dirname(HERE), "postal", "postal_service.py")).read()
        self.assertIn('_kernel_post("/redial", {"host": phost})', src,
                      "parking mail for an unreachable host re-sends the connect signal")


if __name__ == "__main__":
    unittest.main()
