#!/usr/bin/env python3
"""A live ssh proc is not proof of a live tunnel, and every dial leaves a record (the user 2026-07-29).

A morning was lost to a host that would not come back after the laptop moved from ethernet to Wi-Fi,
while plain `ssh <host>` worked the entire time. Two things in here made that undiagnosable and then
unrecoverable:

  1. _spawn_tunnel sent ssh's stdout AND stderr to DEVNULL. The one line naming the cause of a failed
     dial was destroyed as it was printed, so afterwards nothing on the machine could say why.
  2. The supervisor re-dials only a DEAD proc. ssh answers a local connect from its own listener, so an
     ssh whose transport is gone is indistinguishable from a healthy tunnel with no romp behind it —
     the row sat at 'no-kernel' forever with no re-dial and no reap, and the panel offered only "Start",
     which says to go restart the REMOTE kernel. Worse, Try now called attach_remote, which spawned only
     when the proc was dead — so the button meant to break this wedge declined to act on exactly it.

The fix keys on the probe's SHAPE, which separates the two exactly: a far side that refused SPOKE (the
tunnel carries traffic, romp is genuinely absent), one that timed out never did (the path is gone).

Synthetic only — hermetic temp STATE, placeholder hostnames/tokens, no real ssh.
"""
import json
import os
import socket
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_stale", os.path.join(BIN, "romp-kernel"))


class ProbeVerdict(unittest.TestCase):
    """_poll_remote_sids must say HOW it failed, not just that it did."""

    def _poll(self, port):
        r = {"host": "TESTHOST", "local_port": port, "token": ""}
        sids = km._poll_remote_sids(r)
        return sids, r.get("_probe")

    def test_a_far_side_that_refuses_is_recorded_as_refused_not_as_a_dead_path(self):
        # nothing listening → the connect is refused outright: this is what "no kernel over there" looks
        # like, and it must never be mistaken for a tunnel that stopped carrying traffic
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        sids, probe = self._poll(port)
        self.assertIsNone(sids)
        self.assertEqual(probe, "refused")

    def test_a_listener_that_never_answers_is_recorded_as_a_timeout(self):
        # accepts the connect, then says nothing — exactly how an ssh holding a forward over a dead
        # transport behaves, and the case that must trigger a re-dial
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            sids, probe = self._poll(port)
            self.assertIsNone(sids)
            self.assertEqual(probe, "timeout", "silence is a dead path, not a missing kernel")
        finally:
            srv.close()


class SupervisorActsOnTheVerdict(unittest.TestCase):
    """The kill is gated on the verdict, so a host whose romp is simply not running keeps its tunnel."""

    def setUp(self):
        import inspect as _i
        self.src = _i.getsource(km._tunnel_supervisor)

    def test_a_timed_out_no_kernel_row_is_killed_so_the_dead_proc_path_redials(self):
        # The verdict is unchanged; what changed on 2026-07-30 is that it now takes a RUN of them. A
        # single 4-second timeout used to condemn a link that had been carrying traffic milliseconds
        # earlier, which is what made a remote flap twice a minute for hours (see test_tunnel_flap.py).
        self.assertIn('silent = (st == "no-kernel" and r.get("_probe") == "timeout")', self.src,
                      "the kill still keys on the probe verdict, never on a timer")
        self.assertIn("if _note_poll(r, not silent):", self.src, "…and now on a run of them")
        self.assertIn('r["proc"].terminate()', self.src)
        self.assertIn("stale-tunnel", self.src, "and it goes on the record")

    def test_the_kill_is_never_reached_on_a_refusal(self):
        # A refusal proves the tunnel carries traffic — churning its ssh would be pure damage, and would
        # re-dial every 15s forever against a box whose romp is deliberately off. So the ONLY terminate()
        # in the supervisor must sit under the silence-qualified guard, not under the plain no-kernel
        # branch that writes the row's detail. `silent` is false for a refusal, so _note_poll is told the
        # link ANSWERED and the run resets — a refusing host can never accumulate its way to a kill.
        lines = self.src.splitlines()
        verdict = [i for i, ln in enumerate(lines) if 'silent = (st == "no-kernel"' in ln]
        self.assertEqual(len(verdict), 1, "one place decides what silence is")
        guard = [i for i, ln in enumerate(lines) if "if _note_poll(r, not silent):" in ln]
        self.assertEqual(len(guard), 1, "one guard")
        self.assertLess(verdict[0], guard[0], "the verdict is computed before it is counted")
        # the only kill in the STATUS-handling region belongs to that guard. (The route-change block near
        # the top of the loop drops tunnels too, and legitimately so — the network moved under all of them —
        # so this is scoped to the per-host status branch rather than counting the whole function.)
        region = [i for i, ln in enumerate(lines) if "terminate()" in ln and i > guard[0] - 20]
        self.assertEqual(len(region), 1, "one kill in the status branch")
        self.assertLess(guard[0], region[0], "the kill is inside the guard")
        self.assertLess(region[0] - guard[0], 8, "and directly under it, not in some later branch")
        plain = [i for i, ln in enumerate(lines) if 'elif st == "no-kernel"' in ln]
        self.assertTrue(plain and plain[0] > region[0],
                        "the detail-only no-kernel branch comes after, and kills nothing")

    def test_a_dead_dial_is_logged_once_with_what_ssh_printed(self):
        self.assertIn('not r.get("_death_logged")', self.src, "once per death, not once per pass")
        self.assertIn('_tunnel_log(r["host"], "died"', self.src)
        self.assertIn("_forward_bind_failed(err)", self.src,
                      "a bind failure repeats forever unless the ports move")


class ForwardBindFailure(unittest.TestCase):
    def test_ssh_s_own_words_for_a_port_it_cannot_bind_are_recognised(self):
        for line in ("bind: Address already in use",
                     "Error: remote port forwarding failed for listen port 52025",
                     "channel_setup_fwd_listener_tcpip: cannot listen to port: 57946"):
            self.assertTrue(km._forward_bind_failed(line), line)

    def test_an_unrelated_failure_is_not_mistaken_for_one(self):
        for line in ("", "Permission denied (publickey).", "ssh: Could not resolve hostname TESTHOST",
                     "Timeout, server TESTHOST not responding."):
            self.assertFalse(km._forward_bind_failed(line), line)

    def test_reminting_moves_every_forwarded_port_off_the_one_that_collided(self):
        r = {"host": "TESTHOST", "local_port": 51000, "bus_port": 51001, "_peer_notified": (True, "trusted")}
        km._remint_forward_ports(r)
        self.assertNotEqual(r["local_port"], 51000)
        self.assertNotEqual(r["bus_port"], 51001)
        self.assertIsNone(r["_peer_notified"], "the bus holds a stale endpoint until it is re-notified")

    def test_reminting_a_checked_in_row_also_moves_its_reverse_ports_and_re_handshakes(self):
        r = {"host": "TESTHOST", "local_port": 51000, "bus_port": 51001, "checkin": True,
             "rk_port": 52025, "rb_port": 52026, "_handshook": 999}
        km._remint_forward_ports(r)
        self.assertNotEqual(r["rk_port"], 52025)
        self.assertNotEqual(r["rb_port"], 52026)
        self.assertNotIn("_handshook", r, "the hub must be told the new ports")


    # ── T230a: the remint must ENFORCE "not the same doomed argv", never just make it likely ──
    # Linux bind(0) draws from ~14k ephemeral ports, so a bare _free_port() per slot hands the collided
    # number straight back about 1/14,000 draws (CI run 33696143887: `52025 == 52025`), and the live
    # supervisor then re-dials the identical argv with its backoff ladder thrown away. Both pins are
    # deterministic — a stubbed allocator, no OS draw — and restore the stub via addCleanup: this
    # module object (romp_kernel_stale) is shared with two sibling test files.

    def _stub_ports(self, seq):
        it = iter(seq)
        saved = km._free_port
        km._free_port = lambda: next(it)
        self.addCleanup(setattr, km, "_free_port", saved)

    def test_reminting_never_hands_back_any_old_port_and_keeps_the_new_ones_distinct(self):
        # the allocator returns the four OLD ports first, scrambled across slots, then fresh ones:
        # every slot must land on a fresh port — avoiding the whole old set, not just its own slot
        self._stub_ports([52025, 51001, 51000, 52026, 61001, 61002, 61003, 61004, 61005, 61006])
        r = {"host": "TESTHOST", "local_port": 51000, "bus_port": 51001, "checkin": True,
             "rk_port": 52025, "rb_port": 52026, "_handshook": 999, "_peer_notified": (True, "trusted")}
        km._remint_forward_ports(r)
        new = [r["local_port"], r["bus_port"], r["rk_port"], r["rb_port"]]
        self.assertFalse(set(new) & {51000, 51001, 52025, 52026}, "an old port came back: %r" % new)
        self.assertEqual(len(set(new)), 4, "two forwards on one port are a doomed argv too: %r" % new)
        self.assertNotIn("_handshook", r)
        self.assertIsNone(r["_peer_notified"])

    def test_an_allocator_that_cannot_move_the_ports_fails_loudly_and_leaves_the_row_intact(self):
        # a degenerate allocator (the same port forever) exhausts the DRAW bound: RuntimeError, the
        # old ports untouched (atomic), and a ports-remint-failed record appended to the dial log
        self._stub_ports([52025] * 200)
        before = len(km.TUNNEL_LOG.read_text().splitlines()) if km.TUNNEL_LOG.exists() else 0
        r = {"host": "TESTHOST", "local_port": 51000, "bus_port": 51001, "checkin": True,
             "rk_port": 52025, "rb_port": 52026, "_handshook": 999}
        with self.assertRaises(RuntimeError):
            km._remint_forward_ports(r)
        self.assertEqual((r["local_port"], r["bus_port"], r["rk_port"], r["rb_port"]),
                         (51000, 51001, 52025, 52026), "a failed remint changes nothing")
        self.assertEqual(r.get("_handshook"), 999, "no re-handshake was armed for ports that never moved")
        appended = [json.loads(x) for x in km.TUNNEL_LOG.read_text().splitlines()[before:] if x.strip()]
        self.assertTrue(any(x.get("event") == "ports-remint-failed" and x.get("host") == "TESTHOST"
                            for x in appended), appended)


    def test_an_allocator_error_is_a_failed_remint_too_logged_and_in_one_exception_class(self):
        # EMFILE/EADDRNOTAVAIL from bind(0) used to escape the supervisor's failure arm (RuntimeError
        # only), abort the rest of the pass and leave no dial-log trace of the failed remint
        def boom():
            raise OSError(24, "Too many open files")
        saved = km._free_port
        km._free_port = boom
        self.addCleanup(setattr, km, "_free_port", saved)
        before = len(km.TUNNEL_LOG.read_text().splitlines()) if km.TUNNEL_LOG.exists() else 0
        r = {"host": "TESTHOST", "local_port": 51000, "bus_port": 51001}
        with self.assertRaises(RuntimeError):
            km._remint_forward_ports(r)
        self.assertEqual((r["local_port"], r["bus_port"]), (51000, 51001))
        appended = [json.loads(x) for x in km.TUNNEL_LOG.read_text().splitlines()[before:] if x.strip()]
        self.assertTrue(any(x.get("event") == "ports-remint-failed" and "open files" in str(x.get("error"))
                            for x in appended), appended)

    def test_reminting_avoids_every_other_attached_rows_ports(self):
        # a peer waiting out its backoff has its -L port unbound — a bare draw could hand it over
        self._stub_ports([61001, 61002, 61003, 61004, 61005])
        peer = {"host": "PEERHOST", "local_port": 61001, "bus_port": 61003}
        km._remotes["PEERHOST"] = peer
        self.addCleanup(km._remotes.pop, "PEERHOST", None)
        r = {"host": "TESTHOST", "local_port": 51000, "bus_port": 51001}
        km._remint_forward_ports(r)
        self.assertEqual((r["local_port"], r["bus_port"]), (61002, 61004))

    def test_the_supervisor_claims_fresh_ports_only_after_it_holds_them(self):
        # the row's detail said "retrying on fresh ports" BEFORE the remint ran, so on the loud path
        # it claimed fresh ports while re-dialing old ones (T230a review); a failed remint must also
        # keep the backoff ladder — the next dial IS the same argv
        import inspect
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertLess(src.index("_remint_forward_ports(r)"), src.index("retrying on fresh ports"))
        self.assertIn("except RuntimeError", src, "the loud path is CONTAINED — one row never aborts the pass")
        self.assertIn("fresh ports could not be found", src)
        self.assertIn('r["status"] = "error"', src, "the row is marked, not left claiming a retry on fresh ports")
        detail = src.index("retrying on fresh ports")
        reset = src.index('r["fails"], r["next_try"] = 0, 0', detail)   # the reset that FOLLOWS the detail
        self.assertLess(reset - detail, 400, "the ladder reset sits in the success arm, right after the detail")
        self.assertNotIn('r["fails"], r["next_try"] = 0, 0',
                         src[src.index("except RuntimeError"):detail], "the failure arm keeps the ladder")


class DialLog(unittest.TestCase):
    def test_every_dial_and_death_is_appended_with_its_reason(self):
        km._tunnel_log("TESTHOST", "dial", pid=4242, fails=0, argv=["ssh", "-N", "--", "TESTHOST"])
        km._tunnel_log("TESTHOST", "died", code=255, stderr="bind: Address already in use", fails=1)
        rows = [json.loads(x) for x in km.TUNNEL_LOG.read_text().splitlines() if x.strip()]
        rows = [x for x in rows if x.get("host") == "TESTHOST"]
        self.assertEqual([x["event"] for x in rows][-2:], ["dial", "died"])
        self.assertEqual(rows[-1]["stderr"], "bind: Address already in use",
                         "the REASON is the whole point of the log")
        self.assertTrue(all("t" in x for x in rows), "each line is dated")

    def test_logging_never_raises_into_the_supervisor(self):
        # an unserialisable value must not take down the thread that is trying to reconnect you
        km._tunnel_log("TESTHOST", "dial", proc=object(), sock=socket.socket())

    def test_ssh_stderr_is_captured_to_a_file_rather_than_discarded(self):
        import inspect as _i
        src = _i.getsource(km._spawn_tunnel)
        self.assertIn("stderr=(errf or subprocess.DEVNULL)", src)
        self.assertNotIn("stderr=subprocess.DEVNULL", src,
                         "discarding it is what made the outage undiagnosable")

    def test_the_last_dial_s_words_are_readable_back_minus_ssh_s_advisory_chatter(self):
        km.TUNNEL_ERR_DIR.mkdir(parents=True, exist_ok=True)
        km._tunnel_err_path("TESTHOST").write_text(
            "Warning: Permanently added 'TESTHOST' to the list of known hosts.\n"
            "bind: Address already in use\n")
        err = km._tunnel_stderr("TESTHOST")
        self.assertIn("bind: Address already in use", err)
        self.assertNotIn("Permanently added", err, "advisories are not failure reasons")

    def test_a_host_name_can_never_escape_the_error_directory(self):
        p = km._tunnel_err_path("../../etc/passwd")
        self.assertEqual(p.parent, km.TUNNEL_ERR_DIR)


class TryNowActuallyDials(unittest.TestCase):
    def test_attach_redials_a_live_proc_whose_row_is_not_up(self):
        import inspect as _i
        src = _i.getsource(km.attach_remote)
        self.assertIn('elif r.get("status") != "up":', src,
                      "Try now on a wedged-but-alive tunnel used to do nothing at all")
        self.assertIn("forced-redial", src)
        self.assertIn('r["proc"].wait(timeout=3)', src,
                      "wait for the old ssh to exit, or the new dial dies on its listener")


class TheNetworkMoved(unittest.TestCase):
    """Pulling the cord is an EVENT, and both the dead tunnels and the ladder key on it.

    Waiting out a backoff that is counting down from an outage which has already ended is the exact shape
    the design rule warns about: a time window standing in for an event that exists. The event is the
    route changing, and _primary_addr reads it.
    """

    def test_the_route_probe_names_this_machine_s_source_address_without_sending_anything(self):
        addr = km._primary_addr()
        self.assertIsInstance(addr, str)
        if addr:                                   # "" is legitimate: a machine with no route at all
            self.assertRegex(addr, r"^\d+\.\d+\.\d+\.\d+$")

    def test_it_targets_reserved_documentation_space_so_it_can_never_touch_a_real_host(self):
        import inspect as _i
        src = _i.getsource(km._primary_addr)
        self.assertIn('s.connect(("192.0.2.1", 9))', src, "TEST-NET-1, RFC 5737 — never routed")
        self.assertIn("SOCK_DGRAM", src, "a UDP connect only consults the routing table")

    def test_it_is_stable_when_the_network_has_not_moved(self):
        # the whole mechanism rests on this: a re-read must not look like a change
        self.assertEqual(km._primary_addr(), km._primary_addr())

    def test_no_route_at_all_reads_as_empty_rather_than_raising(self):
        import inspect as _i
        src = _i.getsource(km._primary_addr)
        self.assertIn("except OSError:", src)
        self.assertIn('return ""', src, "unplugged is a value, not an exception")

    def test_a_route_change_drops_every_tunnel_and_clears_every_ladder(self):
        import inspect as _i
        src = _i.getsource(km._tunnel_supervisor)
        self.assertIn("if last_addr[0] is not None and addr != last_addr[0]:", src)
        self.assertIn('_tunnel_log("-", "network-changed"', src)
        i = src.index("network-changed")
        after = src[i:i + 900]
        self.assertIn('r["fails"], r["next_try"] = 0, 0', after,
                      "the ladder was backing off from something that is over")
        self.assertIn('r["proc"].terminate()', after,
                      "every live ssh is riding a transport that went with the old address")
        self.assertIn('r.get("checkin_peer")', after, "a checked-in peer owns no ssh here to drop")

    def test_the_first_pass_is_not_a_change(self):
        import inspect as _i
        src = _i.getsource(km._tunnel_supervisor)
        self.assertIn("last_addr = [None]", src)
        # None means "we have not looked yet" — boot already dials everything, and a spurious
        # network-changed at startup would log a move that never happened
        self.assertIn("last_addr[0] is not None", src)


class AskingWhetherSshWorks(unittest.TestCase):
    """The one fact that made this obvious to a human and invisible to romp."""

    def test_the_probe_carries_no_forwards_so_it_cannot_fail_for_the_tunnel_s_reason(self):
        import inspect as _i
        src = _i.getsource(km._host_reachable)
        self.assertIn('["--", host, "true"]', src)
        self.assertNotIn('"-L"', src)
        self.assertNotIn('"-R"', src)

    def test_a_host_ssh_could_parse_as_an_option_is_refused_before_it_is_run(self):
        ok, why = km._host_reachable("-oProxyCommand=touch /tmp/pwned")
        self.assertFalse(ok)
        self.assertEqual(why, "invalid host")

    def test_it_runs_on_a_DEATH_and_outside_the_registry_lock(self):
        import inspect as _i
        src = _i.getsource(km._tunnel_supervisor)
        self.assertIn("probe_ssh = err", src, "armed where the dial's death is logged")
        call = src.index("_host_reachable(r[\"host\"])")
        # the probe is a network round-trip; holding _remotes_lock across it would stall every route
        # that reads the remote registry
        guard = src.index("if probe_ssh is not None:")
        self.assertLess(guard, call)
        self.assertNotIn("with _remotes_lock", src[guard:call])

    def test_a_known_cause_does_not_get_asked_twice(self):
        import inspect as _i
        src = _i.getsource(km._tunnel_supervisor)
        self.assertIn("probe_ssh = None                   # cause already known", src,
                      "a bind failure already names itself; no ssh needed to confirm it")

    def test_the_verdict_reaches_the_row_so_the_panel_stops_blaming_the_far_end(self):
        import inspect as _i
        src = _i.getsource(km._tunnel_supervisor)
        self.assertIn("end, not the host's", src)
        self.assertIn('_tunnel_log(r["host"], "host-probe", sshOk=ok', src)


class StatusTimeline(unittest.TestCase):
    def test_a_status_change_is_logged_and_a_steady_row_is_not(self):
        import inspect as _i
        src = _i.getsource(km._tunnel_supervisor)
        self.assertIn('if st != r.get("status"):', src, "on the transition, never every pass")
        self.assertIn('_tunnel_log(r["host"], "status", was=r.get("status") or "(new)", now=st', src)
        self.assertIn("probe=r.get(\"_probe\")", src, "the timeline carries WHY, not just the flip")


if __name__ == "__main__":
    unittest.main()
