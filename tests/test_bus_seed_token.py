#!/usr/bin/env python3
"""A restarted bus is re-told every peer, token included, by the kernel's next supervisor pass (review find,
2026-09-08).

GET /tunnels stopped carrying each peer's serve token on 2026-09-08 (the page reads that payload, and the
relay injects the credential itself). The bus's restart seed reads the same payload: after a bus-only
restart (a crash revived by _revive_postal_bus, the bus's own code-change re-exec, a manual restart) every
peer was seeded with an empty token, the dialer knocked on the peer's bus credential-less and was refused,
and the kernel never said the token again, since it re-notifies only when (up, trust) changes. Now the bus
names its process on GET /peers (busId, epoch); the kernel's supervisor asks once per pass, and a NEW
process voids every row's notify memo, so the pass re-runs _notify_bus_peer with the stored token
(kernel _note_bus_incarnation; postal peers_snapshot). The seed itself still learns port, up and trust.

The same one-pass supervisor drive covers a second finding on the same pass: last_ok, the stamp that dates
the row's remembered sha, no longer advances on a poll whose sha did not fit its shape (the row keeps the
last good sha, undated; _poll_remote_version's shaConfirmed).

Synthetic only: hostname TESTHOST, an invented token, a fake bus and a fake peer kernel on loopback.
"""
import contextlib
import io
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
km = load_source("romp_kernel_busseed", os.path.join(BIN, "romp-kernel"))
pm = load_source("romp_postal_busseed", os.path.join(BIN, "romp-postal-service"))

SECRET = "peer-secret-DO-NOT-USE"
IMG = "<img src=x onerror=alert(1)>"


class _Json(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _FakeBus(_Json):
    """The local bus as the kernel sees it: records every POST /peer body, answers GET /peers as a bus of
    the given incarnation would."""
    SNAP = {"peers": {}, "epoch": 1000}
    POSTED = []

    def do_GET(self):
        self._json(self.SNAP if self.path.startswith("/peers") else {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode() or "{}")
        if self.path.startswith("/peer"):
            self.POSTED.append(body)
        self._json({"ok": True})


class _FakeKernel(_Json):
    """The local kernel as the BUS sees it: GET /tunnels answers with the kernel's real listing."""
    SERVED = []

    def do_GET(self):
        body = km._tunnels_listing() if self.path.startswith("/tunnels") else {}
        self.SERVED.append(json.dumps(body))
        self._json(body)


class _FakeVersion(_Json):
    """A peer kernel's /version (every GET answers it; the other polls are stubbed out)."""
    PAYLOAD = {}

    def do_GET(self):
        self._json(self.PAYLOAD)


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _OnePass(Exception):
    pass


class _Wake:
    """Stands in for _tunnel_wake: the supervisor's end-of-pass wait raises, so one call runs one pass."""
    def clear(self):
        pass

    def set(self):
        pass

    def wait(self, timeout=None):
        raise _OnePass()


class _Harness(unittest.TestCase):
    """Two fakes on loopback (bus, peer /version), the kernel's remote polls stubbed, one supervisor pass on
    demand. Every module seam is restored on the way out."""
    STUBS = ("_port_open", "_poll_remote_sessions", "_poll_remote_version", "_poll_remote_usage",
             "_poll_remote_views", "_maybe_auto_push", "_adopt_peer_settings", "_pending_tag_rows",
             "_pr_watch_tick", "_watch_tick", "_conserve_tick", "_primary_addr", "_tunnel_wake", "BUS_PORT")

    def setUp(self):
        self._saved = {k: getattr(km, k) for k in self.STUBS}
        self._rem = dict(km._remotes)
        km._remotes.clear()
        with km._known_lock:
            self._known = dict(km._known)
            km._known.clear()
        self.bus, self.ver = _serve(_FakeBus), _serve(_FakeVersion)
        _FakeBus.POSTED = []
        _FakeBus.SNAP = {"peers": {}, "epoch": 1000}
        km.BUS_PORT = self.bus.server_address[1]
        km._tunnel_wake = _Wake()
        km._port_open = lambda port: True
        km._poll_remote_sessions = lambda r: []
        km._poll_remote_version = lambda r: None
        km._poll_remote_usage = lambda r: None
        km._poll_remote_views = lambda r: None
        km._maybe_auto_push = lambda r: None
        km._adopt_peer_settings = lambda host, rver: None
        km._pending_tag_rows = lambda: []
        km._pr_watch_tick = km._watch_tick = km._conserve_tick = lambda now: None
        km._primary_addr = lambda: "10.0.0.1"
        km._bus_seen[0] = None
        km._via_cache.update(t=0.0, snap={})
        km._origin_trust_pushed.clear()
        km._peer_shape_said.clear()
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(km, k, v)
        km._remotes.clear()
        km._remotes.update(self._rem)
        with km._known_lock:
            km._known.clear()
            km._known.update(self._known)
        km._bus_seen[0] = None
        km._via_cache.update(t=0.0, snap={})
        km._peer_shape_said.clear()
        for srv in (self.bus, self.ver):
            srv.shutdown()
            srv.server_close()

    def _row(self, **over):
        # a checked-in peer: no ssh of ours to supervise, so the pass goes straight to the polls
        r = {"host": "TESTHOST", "checkin_peer": True, "kernel_port": 29855,
             "local_port": self.ver.server_address[1], "bus_port": 50002, "token": SECRET,
             "status": "up", "trust": "directed"}
        r.update(over)
        km._remotes[r["host"]] = r
        return r

    def _pass(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                km._tunnel_supervisor()
            except _OnePass:
                pass
        self.assertNotIn("tunnel-supervisor:", err.getvalue(), "the pass must not have died: %s" % err.getvalue())
        return err.getvalue()


class TheBusNamesItsProcess(unittest.TestCase):
    def test_peers_carries_the_bus_id_and_boot_epoch(self):
        snap = pm.peers_snapshot()
        self.assertEqual((snap["busId"], snap["epoch"]), (pm.BUS_ID, pm.BUS_EPOCH))
        self.assertEqual(len(pm.BUS_ID), 32, "minted per process, so two restarts in one second still differ")


class ANewBusIsRetoldEveryPeer(_Harness):
    def test_one_notify_per_incarnation_each_carrying_the_stored_token(self):
        self._row()
        self._pass()
        self.assertEqual(len(_FakeBus.POSTED), 1, "a row the bus has not been told about: one notify")
        self.assertEqual(_FakeBus.POSTED[0], {"host": "TESTHOST", "port": 50002, "up": True,
                                              "token": SECRET, "trust": "directed"})
        self._pass()
        self.assertEqual(len(_FakeBus.POSTED), 1, "same bus, nothing changed: the memo holds")
        _FakeBus.SNAP = {"peers": {}, "epoch": 1001}           # the bus restarted: a new boot second
        self._pass()
        self.assertEqual(len(_FakeBus.POSTED), 2, "a NEW bus process is re-told in the same pass that noticed it")
        self.assertEqual(_FakeBus.POSTED[1]["token"], SECRET, "...token included, which its /tunnels seed never had")
        self.assertEqual(_FakeBus.POSTED[1], _FakeBus.POSTED[0])
        self._pass()
        self.assertEqual(len(_FakeBus.POSTED), 2, "same incarnation again: no third")

    def test_the_bus_id_alone_is_a_new_incarnation_too(self):
        # two restarts inside one second share an epoch; the per-process id still tells them apart
        _FakeBus.SNAP = {"peers": {}, "epoch": 1000, "busId": "a" * 32}
        self._row()
        self._pass()
        _FakeBus.SNAP = {"peers": {}, "epoch": 1000, "busId": "b" * 32}
        self._pass()
        self.assertEqual(len(_FakeBus.POSTED), 2)

    def test_a_bus_that_does_not_say_who_it_is_changes_nothing(self):
        # an older bus, or one that is down: no incarnation to compare, so the memo stands as before
        _FakeBus.SNAP = {"peers": {}}
        self._row()
        self._pass()
        self._pass()
        self.assertEqual(len(_FakeBus.POSTED), 1)
        self.assertIsNone(km._bus_seen[0])

    def test_noticing_a_new_bus_voids_the_memo_for_every_row(self):
        self._row()
        self._row(host="OTHERHOST", bus_port=50003)
        self._pass()
        self.assertEqual(sorted(p["host"] for p in _FakeBus.POSTED), ["OTHERHOST", "TESTHOST"])
        _FakeBus.SNAP = {"peers": {}, "epoch": 1001}
        self._pass()
        self.assertEqual(sorted(p["host"] for p in _FakeBus.POSTED[2:]), ["OTHERHOST", "TESTHOST"])
        self.assertTrue(all(p["token"] == SECRET for p in _FakeBus.POSTED))


class TheSeedLearnsNoTokenAndTheNotifySuppliesIt(_Harness):
    def setUp(self):
        super().setUp()
        self.kern = _serve(_FakeKernel)
        _FakeKernel.SERVED = []
        self._pm_saved = (pm.KERNEL_BASE, pm._peer_threads_reconcile, dict(pm.PEERS))
        pm.KERNEL_BASE = "http://127.0.0.1:%d" % self.kern.server_address[1]
        pm._peer_threads_reconcile = lambda host: None      # the dialer is not under test
        pm.PEERS.clear()

    def tearDown(self):
        pm.KERNEL_BASE, pm._peer_threads_reconcile, saved = self._pm_saved
        pm.PEERS.clear()
        pm.PEERS.update(saved)
        self.kern.shutdown()
        self.kern.server_close()
        super().tearDown()

    def test_the_tunnels_seed_lands_port_up_and_trust_but_no_token(self):
        # documents what the seed can and cannot learn now that /tunnels carries no token
        self._row(trust="trusted")
        pm._seed_peers_from_kernel()
        row = pm.PEERS["TESTHOST"]
        self.assertEqual((row["port"], row["up"], row["trust"]), (50002, True, "trusted"))
        self.assertEqual(row["token"], "", "the payload the seed reads has no token in it...")
        self.assertEqual(len(_FakeKernel.SERVED), 1)
        self.assertNotIn(SECRET, _FakeKernel.SERVED[0], "...because the page reads the same payload")

    def test_the_kernels_notify_fills_the_token_in_over_the_seeded_row(self):
        self._row(trust="trusted")
        pm._seed_peers_from_kernel()
        self.assertEqual(pm.PEERS["TESTHOST"]["token"], "")
        self._pass()                                          # the kernel notices the bus and re-tells it
        self.assertEqual(len(_FakeBus.POSTED), 1)
        body = _FakeBus.POSTED[0]
        self.assertEqual(body["token"], SECRET, "the notify carries what the seed could not")
        payload, status = pm.peer_update(body)                 # what the bus does with that body
        self.assertEqual(status, 200, payload)
        row = pm.PEERS["TESTHOST"]
        self.assertEqual((row["port"], row["up"], row["trust"], row["token"]), (50002, True, "trusted", SECRET),
                         "the seeded row keeps its port, up-state and trust and gains the token")


class AMalformedShaKeepsTheRowUndated(_Harness):
    """The second finding on the same pass: a peer whose /version sha does not fit its shape used to freeze
    the whole answer on the row while last_ok kept advancing, so a stale build read as freshly confirmed."""

    def setUp(self):
        super().setUp()
        km._poll_remote_version = self._saved["_poll_remote_version"]    # the real poll, against the fake /version
        _FakeVersion.PAYLOAD = {"kernel_sha": "abc1234", "kernel_ver": "v0.5.0"}

    def test_last_ok_dates_only_a_sha_this_pass_confirmed(self):
        r = self._row()
        self._pass()
        self.assertEqual((r["kernel_sha"], r["kernel_ver"]), ("abc1234", "v0.5.0"))
        self.assertGreater(r["last_ok"], 0, "a confirmed sha is dated")
        r["last_ok"] = 100.0                                   # an unmistakable old stamp
        _FakeVersion.PAYLOAD = {"kernel_sha": IMG, "kernel_ver": "v0.6.0", "autoNudge": True}
        said = self._pass()
        self.assertEqual(r["status"], "up", "the row itself is fine: it answered")
        self.assertEqual(r["kernel_sha"], "abc1234", "the row keeps the sha it last knew, never the bad one")
        self.assertEqual(r["last_ok"], 100.0, "...and is NOT re-dated: nothing confirmed that sha this pass")
        self.assertEqual((r["kernel_ver"], r["auto_nudge"]), ("v0.6.0", True), "the rest of the answer still lands")
        self.assertIn("TESTHOST reported a kernel_sha that is not one", said)
        _FakeVersion.PAYLOAD = {"kernel_sha": "def5678", "kernel_ver": "v0.6.0"}
        self._pass()
        self.assertEqual(r["kernel_sha"], "def5678")
        self.assertGreater(r["last_ok"], 100.0, "a confirmed sha dates the row again")

    def test_a_malformed_version_keeps_the_last_good_one_on_the_row(self):
        r = self._row()
        self._pass()
        _FakeVersion.PAYLOAD = {"kernel_sha": "abc1234", "kernel_ver": "v1" + IMG}
        before = r["last_ok"]
        r["last_ok"] = 100.0
        self._pass()
        self.assertEqual(r["kernel_ver"], "v0.5.0", "the release name the row last knew, not a blank")
        self.assertGreater(r["last_ok"], 100.0, "the sha itself was confirmed, so the row is dated")
        self.assertGreater(before, 0)


if __name__ == "__main__":
    unittest.main()
