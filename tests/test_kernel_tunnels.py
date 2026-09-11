#!/usr/bin/env python3
"""Phase 2a — the kernel's SSH tunnel concierge for the federated dashboard. The kernel manages the
ssh -L/-R tunnels that let the browser reach a remote kernel (and remote sessions reach this bus),
reads ~/.ssh/config for the attach UI, and fetches the remote kernel's token. These tests drive the
real Handler with a MOCK ssh (no network): a `*serve-token*` command echoes a token; anything else
(the -N tunnel) blocks so the proc looks alive.

Synthetic only — hermetic temp STATE, placeholder hostnames/token, no real ssh.
"""
import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic STATE (so remotes.json + serve-token never touch real state) BEFORE import, and a token so
# _load_token() returns early. NO_OPEN so importing never launches a browser. Mirrors test_kernel_ws_auth.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# the postal trio (2026-09-10): this kernel runs IN-PROCESS, and an attach whose bus call is refused revives the bus;
# without these it started one on the machine's fixed port while the real bus was down for a restart. Client-only, so
# ensure starts nothing; its own port and no peers, read at load (the kernel binds them at import)
import socket as _socket
_s = _socket.socket(); _s.bind(("127.0.0.1", 0)); os.environ.setdefault("ROMP_POSTAL_PORT", str(_s.getsockname()[1])); _s.close()
os.environ["ROMP_POSTAL_PEERS"] = "0"
os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

FAKE_TOKEN = "FAKETOKEN-123"
MOCK_SSH = """#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    *serve-token*) echo "%s"; exit 0;;
    *dev/tcp*) echo UP; exit 0;;      # the attach bootstrap's port probe: kernel already running
  esac
done
sleep 20    # the -N tunnel: block so the proc looks alive
""" % FAKE_TOKEN

# A REMOTE WITH NO RUNNING KERNEL: stateful mock — serve-token/port-probe fail until the "start"
# command runs (which drops a marker file), then both succeed. Drives the attach bootstrap end to
# end: probe DOWN → start → wait → probe UP → token fetched.
MOCK_SSH_BOOT = """#!/usr/bin/env bash
MARK="%s"
for a in "$@"; do
  case "$a" in
    *serve-token*) if [ -f "$MARK" ]; then echo "%s"; else exit 1; fi; exit 0;;
    *dev/tcp*) if [ -f "$MARK" ]; then echo UP; else echo DOWN; fi; exit 0;;
    *romp-serve*) touch "$MARK"; echo "STARTED:$HOME/GitRepos/romp/bin/romp-serve"; exit 0;;
  esac
done
sleep 20
""" % ("%s", FAKE_TOKEN)

# A HOST WITHOUT ROMP: probes fail and the start command reports NOROMP.
MOCK_SSH_NOROMP = """#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    *serve-token*) exit 1;;
    *dev/tcp*) echo DOWN; exit 0;;
    *romp-serve*) echo NOROMP; exit 0;;
  esac
done
sleep 20
"""


def _req(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    headers["X-Romp-Token"] = km.TOKEN     # the serve token gates every route, loopback included
    c.request(method, path, data, headers)
    resp = c.getresponse()
    raw = resp.read()
    c.close()
    try:
        return resp.status, json.loads(raw.decode() or "{}")
    except Exception:
        return resp.status, raw.decode(errors="replace")


class TunnelConcierge(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        ssh = os.path.join(self.td, "mock-ssh")
        with open(ssh, "w") as f:
            f.write(MOCK_SSH)
        os.chmod(ssh, os.stat(ssh).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        cfg = os.path.join(self.td, "ssh_config")
        with open(cfg, "w") as f:
            f.write("Host alpha\n  HostName 10.0.0.1\n\nHost beta gamma\n  User x\n\nHost *\n  ForwardAgent yes\n")
        km.SSH_BIN = ssh
        km.SSH_CONFIG = km.Path(cfg)
        km._remotes.clear()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        for host in list(km._remotes):
            km.detach_remote(host)      # kills the mock proc
        km._remotes.clear()
        self.srv.shutdown()
        self.srv.server_close()

    def test_ssh_hosts_lists_concrete_config_aliases(self):
        status, body = _req(self.port, "GET", "/ssh-hosts")
        self.assertEqual(status, 200)
        hosts = body["hosts"]
        self.assertIn("alpha", hosts)
        self.assertIn("beta", hosts)
        self.assertIn("gamma", hosts)
        self.assertNotIn("*", hosts, "wildcard Host patterns are not connectable targets")

    def test_attach_fetches_token_and_spawns_tunnel(self):
        status, body = _req(self.port, "POST", "/tunnels", {"host": "testhost"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        t = body["tunnel"]
        self.assertEqual(t["host"], "testhost")
        self.assertNotIn("token", t, "the page is never handed the remote's credential (2026-09-08)")
        self.assertIs(t["hasToken"], True, "…only the fact that one was fetched")
        self.assertEqual(km._remotes["testhost"]["token"], FAKE_TOKEN, "the remote serve-token must be fetched over ssh")
        self.assertGreater(t["localPort"], 0, "a local -L port must be allocated for the browser")
        self.assertIn(t["status"], ("authorizing", "connecting", "starting", "up"))
        self.assertTrue(km._tunnel_proc_alive(km._remotes["testhost"]), "the ssh tunnel proc must be running")

    def test_list_tunnels_includes_attached_host(self):
        _req(self.port, "POST", "/tunnels", {"host": "testhost"})
        status, body = _req(self.port, "GET", "/tunnels")
        self.assertEqual(status, 200)
        hosts = {t["host"]: t for t in body["tunnels"]}
        self.assertIn("testhost", hosts)
        self.assertIs(hosts["testhost"]["hasToken"], True)
        self.assertNotIn(FAKE_TOKEN, json.dumps(body), "the token string never rides /tunnels (2026-09-08)")

    def test_detach_kills_and_forgets(self):
        _req(self.port, "POST", "/tunnels", {"host": "testhost"})
        proc = km._remotes["testhost"]["proc"]
        status, body = _req(self.port, "POST", "/tunnels/detach", {"host": "testhost"})
        self.assertEqual(status, 200)
        self.assertTrue(body["detached"])
        self.assertNotIn("testhost", km._remotes)
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll(), "the tunnel proc must be terminated on detach")
        _, body2 = _req(self.port, "GET", "/tunnels")
        self.assertNotIn("testhost", {t["host"] for t in body2["tunnels"]})

    def test_attach_requires_host(self):
        status, body = _req(self.port, "POST", "/tunnels", {})
        self.assertEqual(status, 400)

    def test_a_target_absent_from_ssh_config_attaches_and_is_remembered(self):
        # ssh takes any target you could type after `ssh`, so ~/.ssh/config supplies completions rather
        # than the set of reachable machines. The popover's box is free text on the strength of that, and
        # `known` is what makes a typed host stick: romp's own list of boxes, no config entry needed.
        target = "someone@198.51.100.7"       # TEST-NET-3, and absent from this test's ssh_config
        self.assertNotIn(target, km._ssh_config_hosts())
        status, body = _req(self.port, "POST", "/tunnels", {"host": target})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["tunnel"]["host"], target)
        _req(self.port, "POST", "/tunnels/detach", {"host": target})   # `known` lists past hosts, not live ones
        _, listing = _req(self.port, "GET", "/tunnels")
        self.assertIn(target, {k["host"] for k in listing["known"]},
                      "an attached host is remembered, so it need only ever be typed once")


class HostForSidMap(unittest.TestCase):
    """The host↔sid map the wake-router (Phase 3) reads. Populated by the supervisor's poll; here we set it
    directly to pin the lookup."""
    def setUp(self):
        km._remotes.clear()

    def tearDown(self):
        km._remotes.clear()

    def test_host_for_sid_resolves_remote_else_none(self):
        km._remotes["gpu1"] = {"host": "gpu1", "kernel_port": 29855, "local_port": 9001,
                               "token": "t", "proc": None, "status": "up",
                               "detail": "", "sids": ["aaaa-1111"]}
        self.assertIs(km._host_for_sid("aaaa-1111"), km._remotes["gpu1"])
        self.assertIsNone(km._host_for_sid("local-only-sid"))


class _StubRemoteKernel(BaseHTTPRequestHandler):
    """Stands in for the remote kernel at the far end of the -L tunnel: records the forwarded /deliver."""
    received = []  # class-level capture: [(path, body)]

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        _StubRemoteKernel.received.append((self.path, body))
        out = json.dumps({"ok": True, "injected": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class WakeRouter(unittest.TestCase):
    """Phase 3: the bus POSTs /deliver {id} to the LOCAL kernel; for a REMOTE session the kernel forwards the
    wake over that host's -L tunnel so the idle remote session starts immediately (not at its next turn)."""
    REMOTE_SID = "bbbb-2222-cccc-3333"

    def setUp(self):
        km._remotes.clear()
        _StubRemoteKernel.received = []
        # the "remote kernel" at the tunnel's far end
        self.remote = ThreadingHTTPServer(("127.0.0.1", 0), _StubRemoteKernel)
        self.remote_port = self.remote.server_address[1]
        threading.Thread(target=self.remote.serve_forever, daemon=True).start()
        # the local kernel under test
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        # register the remote, pointing its -L local_port at the stub, owning REMOTE_SID
        km._remotes["gpu1"] = {"host": "gpu1", "kernel_port": 29855, "local_port": self.remote_port,
                               "token": "", "proc": None, "status": "up", "detail": "",
                               "sids": [self.REMOTE_SID]}

    def tearDown(self):
        km._remotes.clear()
        self.srv.shutdown(); self.srv.server_close()
        self.remote.shutdown(); self.remote.server_close()

    def test_deliver_to_remote_sid_forwards_over_the_tunnel(self):
        status, body = _req(self.port, "POST", "/deliver", {"id": self.REMOTE_SID, "text": "DELEGATE: go"})
        self.assertEqual(status, 200)
        self.assertTrue(body["injected"], "the remote kernel's injected result must propagate back")
        self.assertEqual(len(_StubRemoteKernel.received), 1, "exactly one forwarded /deliver")
        path, fwd = _StubRemoteKernel.received[0]
        self.assertEqual(path, "/deliver")
        self.assertEqual(fwd, {"id": self.REMOTE_SID, "text": "DELEGATE: go"})

    def test_deliver_to_unknown_sid_does_not_forward(self):
        # a sid no remote owns is local: it must NOT be forwarded (it would inject locally — no session here,
        # but crucially the stub remote sees nothing).
        _req(self.port, "POST", "/deliver", {"id": "some-local-sid", "text": "hi"})
        self.assertEqual(_StubRemoteKernel.received, [], "a local sid must never forward to a remote kernel")


class BootstrapRemoteKernel(unittest.TestCase):
    """'Install romp normally, then attach just works' (the user 2026-07-03): attaching a host whose
    kernel isn't running STARTS it over ssh (romp-serve, nohup) and then fetches the fresh token; a
    host without romp at all gets a next-step detail in the popover instead of a dead tunnel."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        km._remotes.clear()
        self.saved_wait = km._BOOT_WAIT_S
        km._BOOT_WAIT_S = 3
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        for host in list(km._remotes):
            km.detach_remote(host)
        km._remotes.clear()
        km._BOOT_WAIT_S = self.saved_wait
        self.srv.shutdown()
        self.srv.server_close()

    def _mock(self, script):
        ssh = os.path.join(self.td, "mock-ssh")
        with open(ssh, "w") as f:
            f.write(script)
        os.chmod(ssh, os.stat(ssh).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        km.SSH_BIN = ssh

    def test_attach_starts_a_stopped_remote_kernel_and_fetches_its_token(self):
        marker = os.path.join(self.td, "kernel-started")
        self._mock(MOCK_SSH_BOOT % marker)
        status, body = _req(self.port, "POST", "/tunnels", {"host": "testhost"})
        self.assertEqual(status, 200)
        self.assertTrue(os.path.exists(marker), "the bootstrap ran the remote start command")
        self.assertIs(body["tunnel"]["hasToken"], True,
                      "the token is fetched AFTER the bootstrapped kernel comes up")
        self.assertEqual(km._remotes["testhost"]["token"], FAKE_TOKEN)

    def test_attach_without_romp_reports_the_next_step(self):
        self._mock(MOCK_SSH_NOROMP)
        status, body = _req(self.port, "POST", "/tunnels", {"host": "barehost"})
        self.assertEqual(status, 200)
        self.assertIs(body["tunnel"]["hasToken"], False, "no kernel to authorize against")
        self.assertIn("install.sh", body["tunnel"]["detail"],
                      "the popover tells the user the one command to run")


class ReapStrayTunnels(unittest.TestCase):
    """A kernel restart / re-attach used to leak a SECOND ssh -L tunnel (orphan reparented to init). Before
    spawning, _reap_stray_tunnels SIGTERMs orphans matching our exact signature for the host — and nothing
    else (not the user's own ssh, not a tunnel to another host, not ourselves)."""

    def test_kills_only_matching_orphan_tunnels(self):
        BUS = km.BUS_PORT
        fake_ps = "\n".join([
            "  12345 /usr/bin/ssh -N -T -L 50512:127.0.0.1:29855 -R %d:127.0.0.1:%d TESTHOST" % (BUS, BUS),  # orphan → kill
            "  22222 ssh -N -T -L 9:127.0.0.1:29855 -R %d:127.0.0.1:%d otherhost" % (BUS, BUS),           # other host → keep
            "  33333 ssh TESTHOST",                                                                          # user's own ssh → keep
            "  %d ssh -N -T -L 1:127.0.0.1:29855 -R %d:127.0.0.1:%d TESTHOST" % (os.getpid(), BUS, BUS),      # us → keep
        ])

        class _R:
            stdout = fake_ps
        killed = []
        saved_run, saved_kill = km.subprocess.run, km.os.kill
        km.subprocess.run = lambda *a, **k: _R()
        km.os.kill = lambda pid, sig: killed.append((pid, sig))
        try:
            km._reap_stray_tunnels("TESTHOST")
        finally:
            km.subprocess.run, km.os.kill = saved_run, saved_kill
        self.assertEqual(killed, [(12345, 15)], "only the TESTHOST orphan tunnel is SIGTERM'd")

    def test_kills_peer_bus_orphans_with_no_reverse_forward(self):
        # Peer-bus tunnels (the default) carry NO -R — their ours-only mark is the second -L, which
        # forwards to OUR postal bus port. An orphan from a dead kernel held the -L ports and every
        # respawn died on ExitOnForwardFailure, sending the row to 'down' with the budget exhausted
        # (a federated host, 2026-07-26). The reaper must match this shape too — and still spare the user's
        # own -N port-forward to the same host.
        BUS = km.BUS_PORT
        fake_ps = "\n".join([
            "  12345 ssh -N -T -o BatchMode=yes -L 63407:127.0.0.1:29855 -L 63408:127.0.0.1:%d -- TESTHOST" % BUS,  # peer-bus orphan → kill
            "  22222 ssh -N -T -L 63407:127.0.0.1:29855 -L 63408:127.0.0.1:%d -- otherhost" % BUS,   # other host → keep
            "  33333 ssh -N -L 8080:127.0.0.1:80 TESTHOST",                                          # user's own forward → keep
            "  %d ssh -N -T -L 1:127.0.0.1:29855 -L 2:127.0.0.1:%d -- TESTHOST" % (os.getpid(), BUS),  # us → keep
        ])

        class _R:
            stdout = fake_ps
        killed = []
        saved_run, saved_kill = km.subprocess.run, km.os.kill
        km.subprocess.run = lambda *a, **k: _R()
        km.os.kill = lambda pid, sig: killed.append((pid, sig))
        try:
            km._reap_stray_tunnels("TESTHOST")
        finally:
            km.subprocess.run, km.os.kill = saved_run, saved_kill
        self.assertEqual(killed, [(12345, 15)], "the peer-bus orphan is SIGTERM'd, nothing else")


class SshHostSafety(unittest.TestCase):
    """A remote `host` becomes ssh's first positional arg. A host beginning with `-` (e.g.
    `-oProxyCommand=<cmd>`) would be parsed by ssh as an option → local command execution. attach_remote
    must reject such hosts, and every ssh argv must carry a `--` guard right before the host (M2)."""

    def test_safe_ssh_host_accepts_real_targets(self):
        for ok in ("myhost", "web.example.com", "user@10.0.0.1", "h-1_2", "192.168.1.5", "[fe80::1]"):
            self.assertTrue(km._safe_ssh_host(ok), "should accept %r" % ok)

    def test_safe_ssh_host_rejects_option_injection(self):
        for bad in ("-oProxyCommand=touch /tmp/x", "-Fnone", "-", "", None, 5,
                    "a b", "a;b", "a$(id)", "a`id`", "a|b", "x" * 300):
            self.assertFalse(km._safe_ssh_host(bad), "should reject %r" % (bad,))

    def test_attach_remote_rejects_option_host(self):
        # Reaches validation before any ssh is spawned — a crafted host is a clean ValueError.
        with self.assertRaises(ValueError):
            km.attach_remote("-oProxyCommand=touch /tmp/romp-m2-pwned")

    def test_tunnel_argv_guards_host_with_dashdash(self):
        r = {"host": "myhost", "local_port": 5001, "kernel_port": 6001, "bus_port": 7001,
             "rk_port": 8001, "rb_port": 9001}
        argv = km._tunnel_argv(r)
        self.assertEqual(argv[-1], "myhost", "host is the trailing positional")
        self.assertEqual(argv[-2], "--", "a `--` separator immediately precedes the host")


if __name__ == "__main__":
    unittest.main(verbosity=2)
