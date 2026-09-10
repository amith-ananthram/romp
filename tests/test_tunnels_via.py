#!/usr/bin/env python3
"""The popover's remote-connection controls (the user 2026-08-11: manage what's connected to what
from ONE dashboard). Two kernel pieces carry it: tunnels_of() reads ONE attached host's own
/tunnels through its tunnel + serve token (pairs_snapshot's transport, full rows), and
_via_forward() relays a row action carrying {"via": <attached host>} to that machine's kernel —
the attach route's "from" forwarding, generalized. The via machine judges the action itself; its
answer bubbles back tagged. Every row-action route consults _via_forward right after parsing its
body (source-pinned below; the behavior is function-tested with a stubbed transport).

Synthetic hosts only; hermetic state dir; the transport is stubbed — no ssh, no sockets.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_via", os.path.join(BIN, "romp-kernel"))


class _Stubbed(unittest.TestCase):
    """Seeded _remotes + a stubbed _remote_kernel_call, restored after every test."""

    def setUp(self):
        self._rkc = km._remote_kernel_call
        self._rem = dict(km._remotes)
        km._remotes.clear()

    def tearDown(self):
        km._remote_kernel_call = self._rkc
        km._remotes.clear()
        km._remotes.update(self._rem)


class TunnelsOf(_Stubbed):
    def test_unattached_host_is_a_loud_error(self):
        d = km.tunnels_of("TESTHOST")
        self.assertFalse(d["ok"])
        self.assertIn("no attached tunnel", d["error"])

    def test_a_down_host_says_so_instead_of_an_empty_list(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "down"}
        d = km.tunnels_of("TESTHOST")
        self.assertFalse(d["ok"])
        self.assertIn("down", d["error"])

    def test_an_up_host_returns_its_full_rows_tagged_with_of(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "local_port": 1, "token": "t"}
        km._remote_kernel_call = lambda r, m, p, payload=None, timeout=8: (
            200, {"tunnels": [{"host": "third", "status": "up", "outOfDate": True, "behindBy": 2}],
                  "known": [], "local": {"sha": "abc"}}, None)
        d = km.tunnels_of("TESTHOST")
        self.assertTrue(d["ok"])
        self.assertEqual(d["of"], "TESTHOST")
        self.assertEqual(d["tunnels"][0]["host"], "third")
        self.assertEqual(d["tunnels"][0]["behindBy"], 2, "a well-formed row field passes through the whitelist")
        self.assertNotIn("local", d, "only `tunnels` is relayed — the peer's other sections stay behind (2026-09-08)")

    def test_a_failed_read_carries_the_transport_error(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "local_port": 1, "token": "t"}
        km._remote_kernel_call = lambda *a, **k: (None, None, "could not reach TESTHOST's kernel: boom")
        d = km.tunnels_of("TESTHOST")
        self.assertFalse(d["ok"])
        self.assertIn("boom", d["error"])


class ViaForward(_Stubbed):
    def test_no_via_means_local_handling_exactly_as_before(self):
        self.assertIsNone(km._via_forward({"host": "x"}, "/tunnels/detach"))
        self.assertIsNone(km._via_forward(None, "/tunnels/detach"))
        self.assertIsNone(km._via_forward({"host": "x", "via": ""}, "/tunnels/detach"))

    def test_an_unattached_via_is_a_loud_refusal(self):
        d = km._via_forward({"host": "third", "via": "TESTHOST"}, "/tunnels/detach")
        self.assertFalse(d["ok"])
        self.assertEqual(d["via"], "TESTHOST")
        self.assertIn("not an attached, connected host", d["error"])

    def test_forwards_the_body_minus_via_to_the_same_route(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "local_port": 1, "token": "t"}
        seen = {}

        def rkc(r, method, path, payload=None, timeout=8):
            seen.update(host=r["host"], method=method, path=path, payload=payload)
            return 200, {"ok": True, "tunnel": {"host": "third"}}, None

        km._remote_kernel_call = rkc
        d = km._via_forward({"host": "third", "trust": "trusted", "via": "TESTHOST"}, "/tunnels/trust")
        self.assertEqual((seen["host"], seen["method"], seen["path"]), ("TESTHOST", "POST", "/tunnels/trust"))
        self.assertEqual(seen["payload"], {"host": "third", "trust": "trusted"}, "via never crosses the wire")
        self.assertTrue(d["ok"])
        self.assertEqual(d["via"], "TESTHOST", "the answer names the machine that acted")

    def test_slow_actions_get_the_long_leash_quick_ones_do_not(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "local_port": 1, "token": "t"}
        timeouts = {}

        def rkc(r, m, p, payload=None, timeout=8):
            timeouts[p] = timeout
            return 200, {"ok": True}, None

        km._remote_kernel_call = rkc
        for p in ("/tunnels/update", "/tunnels/start", "/tunnels/detach", "/tunnels/trust"):
            km._via_forward({"host": "x", "via": "TESTHOST"}, p)
        self.assertGreaterEqual(timeouts["/tunnels/update"], 120, "a via push blocks on git + a restart")
        self.assertGreaterEqual(timeouts["/tunnels/start"], 60, "a via start waits out a boot")
        self.assertLessEqual(timeouts["/tunnels/detach"], 30)
        self.assertLessEqual(timeouts["/tunnels/trust"], 30)

    def test_a_transport_failure_is_loud_and_tagged(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "local_port": 1, "token": "t"}
        km._remote_kernel_call = lambda *a, **k: (None, None, "could not reach TESTHOST's kernel: boom")
        d = km._via_forward({"host": "x", "via": "TESTHOST"}, "/tunnels/start")
        self.assertFalse(d["ok"])
        self.assertIn("boom", d["error"])
        self.assertEqual(d["via"], "TESTHOST")

    def test_a_non_200_answer_without_ok_reads_as_failure(self):
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "local_port": 1, "token": "t"}
        km._remote_kernel_call = lambda *a, **k: (502, {"detail": "dirty tree"}, None)
        d = km._via_forward({"host": "x", "via": "TESTHOST"}, "/tunnels/update")
        self.assertFalse(d["ok"])
        self.assertEqual(d["detail"], "dirty tree", "the via machine's own refusal comes through verbatim")


class RouteWiring(unittest.TestCase):
    """Every row-action route consults _via_forward right after parsing its body, and the GET side
    serves /tunnels/of — pinned at the source (the handlers are inline methods; the behavior above
    is what the pins delegate to)."""

    def test_every_action_route_is_via_forwardable(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        for route in ("/tunnels/detach", "/tunnels/checkin", "/tunnels/autoupdate", "/tunnels/forget",
                      "/tunnels/trust", "/tunnels/update", "/tunnels/pull", "/tunnels/askpull",
                      "/tunnels/start"):
            self.assertIn('_via_forward(body, "%s")' % route, src, "route %s must forward via" % route)
        self.assertIn('if p == "/tunnels/of":', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
