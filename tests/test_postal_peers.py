#!/usr/bin/env python3
"""Peer-bus mode stage 1 (plans/postal-peer-buses.md): every machine runs its OWN bus — the
client-only special case is retired under the flag — and the kernel feeds the bus a peer table
over POST /peer on tunnel transitions. Synthetic only."""
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_peers", os.path.join(BIN, "romp-postal-service"))


class PeerMode(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        pm.PEERS.clear()

    def test_flag_retires_client_only(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"
        try:
            self.assertFalse(pm.is_client_only(),
                             "peer mode: every machine runs its own bus — client-only is retired")
        finally:
            os.environ.pop("ROMP_POSTAL_CLIENT_ONLY", None)

    def test_flag_off_client_only_unchanged(self):
        os.environ["ROMP_POSTAL_PEERS"] = "0"          # peer mode is the DEFAULT now; 0 = legacy scheme
        os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"
        try:
            self.assertTrue(pm.is_client_only(), "legacy mode: the singleton scheme is untouched")
        finally:
            os.environ.pop("ROMP_POSTAL_CLIENT_ONLY", None)

    def test_peers_on_is_the_default(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        self.assertTrue(pm.peers_on(), "peer-bus mode is the default (the user's activation, 2026-07-20)")
        os.environ["ROMP_POSTAL_PEERS"] = "0"
        self.assertFalse(pm.peers_on(), "explicit 0 selects the legacy scheme")

    def test_peer_update_and_snapshot(self):
        payload, status = pm.peer_update({"host": "TESTHOST", "port": 50002, "up": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["up"], 1)
        snap = pm.peers_snapshot()["peers"]["TESTHOST"]
        self.assertEqual((snap["port"], snap["up"]), (50002, True))
        payload, status = pm.peer_update({"host": "TESTHOST", "port": 50002, "up": False})
        self.assertEqual(pm.peers_snapshot()["peers"]["TESTHOST"]["up"], False,
                         "a down transition keeps the row for introspection, marked down")
        self.assertEqual(payload["up"], 0)

    def test_peer_update_refuses_a_non_boolean_up_and_records_nothing(self):
        # `up` used to be coerced with bool(), so a notify carrying the STRING "false" marked the peer UP
        for bad in ("false", "true", 1, 0, "up"):
            payload, status = pm.peer_update({"host": "TESTHOST", "port": 50002, "up": bad})
            self.assertEqual(status, 400, (bad, payload))
            self.assertEqual(payload["error"], "'up' must be true or false, got %s" % json.dumps(bad))
        self.assertEqual(pm.PEERS, {}, "a refused notify records no row")
        payload, status = pm.peer_update({"host": "TESTHOST", "port": 50002, "up": False})
        self.assertEqual((status, pm.PEERS["TESTHOST"]["up"]), (200, False), "a real false rides through as itself")

    def test_peer_update_reads_an_explicit_null_up_as_absent(self):
        # the rule _as_bool states (review find, 2026-09-08): null is the absent case spelled out, so it
        # takes the field's default (down), where a string or a number is refused
        payload, status = pm.peer_update({"host": "TESTHOST", "port": 50002, "up": None})
        self.assertEqual(status, 200, payload)
        self.assertEqual(pm.PEERS["TESTHOST"]["up"], False)

    def test_peer_update_validates(self):
        for bad in ({}, {"host": "", "port": 1}, {"host": "h"}, {"host": "h", "port": "x"},
                    {"host": "h", "port": 0}, {"host": "h", "port": True}):
            payload, status = pm.peer_update(bad)
            self.assertEqual(status, 400, "rejected: %r" % (bad,))
        self.assertEqual(pm.PEERS, {}, "nothing recorded from rejected notifies")

    def test_origin_only_row_stores_trust_without_a_port(self):
        # Trust-by-origin (the user 2026-07-25): a tier for a host with no tunnel here. Portless,
        # no dialer, judged at delivery by true origin.
        payload, status = pm.peer_update({"host": "FARBOX", "trust": "trusted", "originOnly": True})
        self.assertEqual(status, 200)
        self.assertTrue(payload["originOnly"])
        row = pm.peers_snapshot()["peers"]["FARBOX"]
        self.assertEqual((row["port"], row["up"], row["trust"], row.get("originOnly")),
                         (None, False, "trusted", True))
        # applied to a CONNECTED row it touches only the trust — port/up/token survive
        pm.peer_update({"host": "HUB", "port": 50007, "up": True, "token": "tk", "trust": "trusted"})
        pm.peer_update({"host": "HUB", "trust": "directed", "originOnly": True})
        row = pm.peers_snapshot()["peers"]["HUB"]
        self.assertEqual((row["port"], row["up"], row["token"], row["trust"], row.get("originOnly")),
                         (50007, True, "tk", "directed", None))

    def test_origin_only_validates(self):
        for bad in ({"originOnly": True}, {"host": "h", "originOnly": True},
                    {"host": "h", "trust": "bogus", "originOnly": True}):
            payload, status = pm.peer_update(bad)
            self.assertEqual(status, 400, "rejected: %r" % (bad,))

    def test_via_reach_summarizes_far_spokes(self):
        pm.PEER_STATE.clear()
        try:
            import time as _t
            pm.PEER_STATE["hub"] = {"presence": [
                {"name": "a", "id": "1"},                       # the hub's own session — not via
                {"name": "b", "id": "2", "via": "FARBOX"},
                {"name": "c", "id": "3", "via": "FARBOX"},
                {"name": "d", "id": "4", "via": "PEERED"},      # directly peered here → excluded
            ], "seenAt": int(_t.time())}
            pm.peer_update({"host": "PEERED", "port": 50008, "up": True})
            pm.peer_update({"host": "FARBOX", "trust": "isolated", "originOnly": True})
            rows = pm.via_reach()
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual((r["host"], r["via"], r["agents"], r["trust"]),
                             ("FARBOX", "hub", 2, "isolated"))
            self.assertEqual(pm.peers_snapshot()["viaReach"], rows,
                             "the snapshot carries the summary for the kernel's popover proxy")
        finally:
            pm.PEER_STATE.clear()

    def test_routes_are_wired(self):
        import inspect
        src = inspect.getsource(pm)
        self.assertIn('if u.path == "/peer":', src)
        self.assertIn('if u.path == "/peers":', src)


if __name__ == "__main__":
    unittest.main()


_B_STATE = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _B_STATE
pmb = load_source("romp_postal_peers_b", os.path.join(BIN, "romp-postal-service"))


class _TwoBusHarness(unittest.TestCase):
    """The two-bus harness (plans/postal-peer-buses.md): A and B are two module instances with
    separate state dirs; the "tunnel" is a direct call — A builds a request, B handles it, A applies
    the response. No tests of its own: TwoBusExchange and ExchangeRelaysAreBudgeted run on it."""

    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        self._saved = (pm.self_host, pmb.self_host, pm.local_agents, pmb.local_agents,
                       pm.local_agents_checked, pmb.local_agents_checked)
        pm.self_host = lambda: "hosta"
        pmb.self_host = lambda: "hostb"
        pm.local_agents = lambda threads=False: [{"name": "alpha", "id": "sid-a", "dir": ""}]
        pmb.local_agents = lambda threads=False: [{"name": "beta", "id": "sid-b", "dir": ""}]
        # _relay_in and fleet_presence rule from the CHECKED seam now (2026-08-31): same stub
        # rows, answered=True — the harness's world is authoritative
        pm.local_agents_checked = lambda threads=False: (pm.local_agents(), True)
        pmb.local_agents_checked = lambda threads=False: (pmb.local_agents(), True)
        for m in (pm, pmb):
            m.PEER_STATE.clear()
            m.PEERS.clear()
            m._peer_pending.clear()
            getattr(m, "_inflight", {}).clear()      # no exchange is carrying anything at the start of a test
            #                                          (getattr: the module also runs against a bus without the
            #                                          flight table, to show each new test red for its own reason)
            m._seen_ids = None
        import shutil
        for m in (pm, pmb):
            shutil.rmtree(m.OUTBOX, ignore_errors=True)
            shutil.rmtree(m.MAILROOT, ignore_errors=True)
            try:
                m.PEER_SEEN.unlink()
            except Exception:
                pass
            m.MAILROOT.mkdir(parents=True, exist_ok=True)
        # In production the kernel's /peer notify (peer_update) populates PEERS with each host's trust,
        # and the inbound gate HOLDS a directed peer's mail. These tests exercise the exchange/relay
        # MECHANICS, so mark the exchanged peers trusted (the gate keys on the relay's origin host: B sees
        # A's self_host "hosta"; A's dialer-apply uses the "srv" alias). Trust itself is covered in
        # test_postal_quarantine.py.
        pmb.PEERS["hosta"] = {"port": 1, "up": True, "trust": "trusted"}
        pm.PEERS["srv"] = {"port": 1, "up": True, "trust": "trusted"}

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        (pm.self_host, pmb.self_host, pm.local_agents, pmb.local_agents,
         pm.local_agents_checked, pmb.local_agents_checked) = self._saved

    def _exchange(self):
        req = pm.build_exchange_request("srv", wait=False)
        resp, status = pmb.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        pm.peer_exchange_apply("srv", req, resp)
        return resp


class TwoBusExchange(_TwoBusHarness):
    """Covers mail both directions, end-to-end acks, dedupe on a resent relay, bounce to the sender on a
    dead recipient, presence gossip, and the version handshake."""

    def test_quarantine_holds_cross_the_exchange_both_ways(self):
        # Slice 4 of the federation UI (the user 2026-07-25): each side's exchange payload carries a
        # summary of ITS held mail, so a hold is visible from the peer instead of only on the
        # holding machine's own dashboard.
        import shutil
        for m in (pm, pmb):
            shutil.rmtree(m.QUARANTINE, ignore_errors=True)
        try:
            pmb.QUARANTINE.mkdir(parents=True, exist_ok=True)
            (pmb.QUARANTINE / "h1.json").write_text(json.dumps(
                {"mid": "h1", "to": "beta", "frm": "api", "origin": "TESTHOST",
                 "body": "please review the parser fix before it merges", "at": 1000}))
            pm.QUARANTINE.mkdir(parents=True, exist_ok=True)
            (pm.QUARANTINE / "h2.json").write_text(json.dumps(
                {"mid": "h2", "to": "alpha", "frm": "web", "origin": "", "body": "ping", "at": 1001}))
            self._exchange()
            got = pm.PEER_STATE["srv"]["holds"]
            self.assertEqual([(h["mid"], h["frm"], h["to"], h["origin"]) for h in got],
                             [("h1", "api", "beta", "TESTHOST")], "B's hold arrived at A")
            self.assertIn("please review the parser fix", got[0]["gist"])
            self.assertEqual(pm.remote_holds()[0]["atHost"], "srv",
                             "stamped with the machine HOLDING it")
            got_b = pmb.PEER_STATE["hosta"]["holds"]
            self.assertEqual([h["mid"] for h in got_b], ["h2"], "A's hold rode the request to B")
        finally:
            for m in (pm, pmb):
                shutil.rmtree(m.QUARANTINE, ignore_errors=True)

    def test_mail_crosses_and_acks_clear_the_outbox(self):
        pm.outbox_put("srv", {"mid": "m1", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "hello over the wire", "kind": "question", "t": 1})
        self._exchange()
        box = pmb.read_box("sid-b", consume=True)
        self.assertEqual(len(box), 1, "the relay delivered on B")
        self.assertIn("hello over the wire", box[0]["body"])
        self.assertEqual(box[0]["kind"], "question", "the declared kind rides the relay")
        self.assertEqual(pm.outbox_list("srv"), [], "B's ack cleared A's outbox")
        self.assertEqual(pmb.PEER_STATE["hosta"]["presence"][0]["name"], "alpha", "presence gossiped A to B")
        self.assertEqual(pm.PEER_STATE["srv"]["presence"][0]["name"], "beta", "presence gossiped B to A")

    def test_resent_relay_delivers_exactly_once(self):
        pm.outbox_put("srv", {"mid": "m2", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "once", "kind": "", "t": 1})
        req = pm.build_exchange_request("srv", wait=False)
        r1, _ = pmb.peer_exchange_handle(req)
        r2, _ = pmb.peer_exchange_handle(req)          # the link flapped before the ack → A resent
        self.assertIn("m2", r1["acks"])
        self.assertIn("m2", r2["acks"], "the duplicate is re-acked, never re-delivered")
        self.assertEqual(len(pmb.read_box("sid-b", consume=True)), 1, "exactly one delivery")

    def test_dead_recipient_bounces_to_the_sender(self):
        pm.outbox_put("srv", {"mid": "m3", "to": "ghost", "frm": "alpha", "frm_id": "sid-a",
                              "body": "boo", "kind": "", "t": 1})
        self._exchange()
        self.assertEqual(pm.outbox_list("srv"), [], "a definitive refusal never stays parked")
        back = pm.read_box("sid-a", consume=True)
        self.assertEqual(len(back), 1, "the sender got the bounce note")
        self.assertIn("undeliverable to 'ghost'", back[0]["body"])
        self.assertEqual(back[0]["from"], "romp-postal", "bus-authored, clearly not a peer message")

    def test_return_mail_rides_the_response_and_acks_the_next_request(self):
        pmb.outbox_put("hosta", {"mid": "m4", "to": "alpha", "frm": "beta", "frm_id": "sid-b",
                                 "body": "reply", "kind": "", "t": 1})
        self._exchange()
        self.assertEqual(len(pm.read_box("sid-a", consume=True)), 1, "B-to-A mail rode the response")
        self.assertEqual(len(pmb.outbox_list("hosta")), 1, "B holds it until the end-to-end ack")
        self._exchange()
        self.assertEqual(pmb.outbox_list("hosta"), [], "the next request's ack cleared B's outbox")

    def test_version_drift_refuses_politely(self):
        req = pm.build_exchange_request("srv", wait=False)
        req["proto"] = 999
        resp, status = pmb.peer_exchange_handle(req)
        self.assertEqual(status, 409)
        self.assertIn("drift", resp["error"])

    def test_peer_route_resolves_and_disambiguates(self):
        pm.PEER_STATE["srv"] = {"presence": [{"name": "beta", "id": "sid-b"}], "seenAt": 1}
        pm.PEER_STATE["other"] = {"presence": [{"name": "beta", "id": "sid-c"}], "seenAt": 1}
        host, hits = pm.peer_route("beta")
        self.assertIsNone(host, "two hosts own 'beta' → ambiguous")
        self.assertEqual(len(hits), 2)
        host, hit = pm.peer_route("srv:beta")
        self.assertEqual(host, "srv", "host:name breaks the tie")
        self.assertEqual(hit["id"], "sid-b")


_C_STATE = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _C_STATE
pmc = load_source("romp_postal_peers_c", os.path.join(BIN, "romp-postal-service"))


class ThreeBusRelay(unittest.TestCase):
    """Spoke-to-spoke through a shared hub (plans/postal-peer-buses.md 3b): A and C each exchange only
    with hub B. Presence gossips one hop with a `via` label; a relay for a far spoke forwards ONE hop
    with end-to-end acks relayed backward, so the origin keeps mail parked until the FAR side delivers."""

    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        self._saved = (pm.self_host, pmb.self_host, pmc.self_host,
                       pm.local_agents, pmb.local_agents, pmc.local_agents,
                       pm.local_agents_checked, pmb.local_agents_checked, pmc.local_agents_checked)
        pm.self_host = lambda: "hosta"
        pmb.self_host = lambda: "hostb"
        pmc.self_host = lambda: "hostc"
        pm.local_agents = lambda threads=False: [{"name": "alpha", "id": "sid-a", "dir": ""}]
        pmb.local_agents = lambda threads=False: [{"name": "beta", "id": "sid-b", "dir": ""}]
        pmc.local_agents = lambda threads=False: [{"name": "carol", "id": "sid-c", "dir": ""}]
        # _relay_in and fleet_presence rule from the CHECKED seam now (2026-08-31)
        pm.local_agents_checked = lambda threads=False: (pm.local_agents(), True)
        pmb.local_agents_checked = lambda threads=False: (pmb.local_agents(), True)
        pmc.local_agents_checked = lambda threads=False: (pmc.local_agents(), True)
        import shutil
        for m in (pm, pmb, pmc):
            m.PEER_STATE.clear()
            m.PEERS.clear()
            m._peer_pending.clear()
            m._seen_ids = None
            shutil.rmtree(m.OUTBOX, ignore_errors=True)
            shutil.rmtree(m.MAILROOT, ignore_errors=True)
            try:
                m.PEER_SEEN.unlink()
            except Exception:
                pass
            m.MAILROOT.mkdir(parents=True, exist_ok=True)
        # Mechanics test → mark the origin trusted so the inbound gate delivers (see TwoBusExchange.setUp).
        # The gate keys on the relay's ORIGIN host: B delivers to beta and C delivers a forwarded message
        # whose origin is "hosta" (B stamps origin when it forwards). Trust itself: test_postal_quarantine.
        pmb.PEERS["hosta"] = {"port": 1, "up": True, "trust": "trusted"}
        pmc.PEERS["hosta"] = {"port": 1, "up": True, "trust": "trusted"}
        # A forwarded message is ALSO capped at the forwarder's own tier (2026-08-05: a relay must
        # not out-rank itself by stamping an origin — test_postal_quarantine owns that rule), so the
        # hub C actually exchanges with needs a tier of its own. A real deployment always has one:
        # the kernel notifies a row for every host you dial. Without it the hub reads as an unknown
        # host, i.e. directed, and the relayed mail is HELD — correct, but the trust tests' business,
        # not this one's, which is about relay mechanics and end-to-end acks.
        pmc.PEERS["hub"] = {"port": 1, "up": True, "trust": "trusted"}

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)
        (pm.self_host, pmb.self_host, pmc.self_host,
         pm.local_agents, pmb.local_agents, pmc.local_agents,
         pm.local_agents_checked, pmb.local_agents_checked, pmc.local_agents_checked) = self._saved

    def _xchg(self, dialer, dialed, alias):
        req = dialer.build_exchange_request(alias, wait=False)
        resp, status = dialed.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        dialer.peer_exchange_apply(alias, req, resp)

    def test_far_spoke_gossips_via_the_hub(self):
        self._xchg(pmc, pmb, "hub")                  # B learns carol
        self._xchg(pm, pmb, "hub")                   # A learns beta directly and carol via the hub
        names = {(a.get("name"), a.get("via")) for a in pm.PEER_STATE["hub"]["presence"]}
        self.assertIn(("beta", None), names)
        self.assertIn(("carol", "hostc"), names, "the far spoke arrives labeled via, one hop only")

    def test_far_holds_gossip_via_the_hub_one_hop_only(self):
        # A hold TWO machines away (on C) reaches A labeled via the hub — and never re-gossips
        # further (the same one-hop rule as presence).
        import shutil
        for m in (pm, pmb, pmc):
            shutil.rmtree(m.QUARANTINE, ignore_errors=True)
        try:
            pmc.QUARANTINE.mkdir(parents=True, exist_ok=True)
            (pmc.QUARANTINE / "h9.json").write_text(json.dumps(
                {"mid": "h9", "to": "carol", "frm": "ops", "origin": "RENTBOX",
                 "body": "held on the far spoke", "at": 1002}))
            self._xchg(pmc, pmb, "hub")              # B learns C's hold (direct, no via)
            self._xchg(pm, pmb, "hub")               # A learns it via the hub
            got = [h for h in pm.PEER_STATE["hub"]["holds"] if h["mid"] == "h9"]
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].get("via"), "hostc", "labeled with the machine holding it")
            self.assertEqual([r["atHost"] for r in pm.remote_holds() if r["mid"] == "h9"],
                             ["hostc"])
            self.assertNotIn("via", [k for h in pmb.PEER_STATE["hostc"]["holds"] for k in h
                                     if k == "via"], "the direct hop carries no via label")
            # A never re-gossips the via-labeled hold onward (one hop, like presence)
            self.assertEqual([h for h in pm.holds_payload("elsewhere") if h["mid"] == "h9"], [])
        finally:
            for m in (pm, pmb, pmc):
                shutil.rmtree(m.QUARANTINE, ignore_errors=True)

    def test_relay_hops_once_with_end_to_end_acks(self):
        self._xchg(pmc, pmb, "hub")
        self._xchg(pm, pmb, "hub")
        host, hit = pm.peer_route("carol")
        self.assertEqual(host, "hub", "A reaches carol through the peer it can dial")
        pm.outbox_put("hub", {"mid": "r1", "to": "carol", "frm": "alpha", "frm_id": "sid-a",
                              "body": "over the hub", "kind": "delegate", "t": 1})
        self._xchg(pm, pmb, "hub")                   # A→B: B forwards, does NOT ack yet
        self.assertEqual(len(pm.outbox_list("hub")), 1,
                         "the origin keeps it parked until the FAR side's ack (end-to-end)")
        self.assertEqual(len(pmb.outbox_list("hostc")), 1, "the hub holds it forwarded for C")
        self._xchg(pmc, pmb, "hub")                  # C→B: the response carries the relay → C delivers
        box = pmc.read_box("sid-c", consume=True)
        self.assertEqual(len(box), 1, "delivered on the far spoke")
        self.assertIn("over the hub", box[0]["body"])
        self._xchg(pmc, pmb, "hub")                  # C's next request acks → B routes it backward
        self.assertEqual(pmb.outbox_list("hostc"), [], "C's ack cleared the hub's forward")
        self._xchg(pm, pmb, "hub")                   # A's next exchange picks the relayed ack up
        self.assertEqual(pm.outbox_list("hub"), [], "the end-to-end ack finally clears the origin")

    def test_far_bounce_relays_backward_to_the_sender(self):
        self._xchg(pmc, pmb, "hub")
        self._xchg(pm, pmb, "hub")
        pm.outbox_put("hub", {"mid": "r2", "to": "carol", "frm": "alpha", "frm_id": "sid-a",
                              "body": "too late", "kind": "", "t": 1})
        self._xchg(pm, pmb, "hub")                   # forwarded
        pmc.local_agents = lambda threads=False: []                # carol died before delivery
        self._xchg(pmc, pmb, "hub")                  # C receives the relay → bounces it
        self._xchg(pmc, pmb, "hub")                  # C's bounce rides its next request → B routes backward
        self._xchg(pm, pmb, "hub")                   # A picks the bounce up → sender gets the note
        back = pm.read_box("sid-a", consume=True)
        self.assertEqual(len(back), 1, "the far refusal came all the way back")
        self.assertIn("undeliverable to 'carol'", back[0]["body"])
        self.assertEqual(pm.outbox_list("hub"), [], "nothing left parked after a definitive refusal")

    def test_a_hopped_message_never_hops_again(self):
        m = {"mid": "r3", "to": "nobody-anywhere", "frm": "alpha", "frm_id": "sid-a",
             "body": "x", "kind": "", "t": 1, "origin": "hosta"}
        pmb.PEER_STATE["hostc"] = {"presence": [{"name": "nobody-anywhere", "id": "sid-x"}], "seenAt": 1}
        verdict, bounce = pmb._relay_in("hosta", m)
        self.assertEqual(verdict, "bounce", "one hop max: an already-hopped message bounces, never re-forwards")
        self.assertIn("no live session", bounce["why"])


class ExchangeRelaysAreBudgeted(_TwoBusHarness):
    """One exchange carries the outbox's oldest-first prefix under _RELAY_BUDGET_BYTES (half the dialed
    bus's 1 MiB body cap); the rest ride the next round. The request used to carry the WHOLE outbox
    (review find, 2026-09-08): a backlog past the cap was 413'd by the dialed bus's _body gate and the
    dialer re-sent the identical request on every backoff, forever, so every message to that peer parked
    on a healthy link. A single relay over the budget is bounced to its sender instead of retried."""

    def _park(self, n, size, prefix="big"):
        for i in range(n):
            pm.outbox_put("srv", {"mid": "%s%02d" % (prefix, i), "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                                  "body": "R" * size, "kind": "coordinate", "t": i})

    def test_a_backlog_past_the_cap_drains_over_successive_exchanges(self):
        self._park(12, 100_000)
        self.assertGreater(len(json.dumps({"relays": pm.outbox_list("srv")}).encode()), pm._POST_MAX_BYTES,
                           "the whole outbox would not fit one request")
        rounds = 0
        while pm.outbox_list("srv") and rounds < 10:
            req = pm.build_exchange_request("srv", wait=False)
            self.assertLessEqual(len(json.dumps(req).encode("utf-8")), pm._POST_MAX_BYTES,
                                 "every request fits the dialed bus's cap")
            self.assertTrue(req["relays"], "progress every round")
            resp, status = pmb.peer_exchange_handle(req)
            self.assertEqual(status, 200)
            pm.peer_exchange_apply("srv", req, resp)
            rounds += 1
        self.assertEqual(pm.outbox_list("srv"), [], "the backlog drained")
        self.assertGreaterEqual(rounds, 2, "over more than one exchange")
        box = pmb.read_box("sid-b", consume=True)
        self.assertEqual(len(box), 12, "every message arrived exactly once")
        self.assertEqual(sorted(len(m["body"]) for m in box), [100_000] * 12)

    def test_a_name_collision_on_the_dialed_side_loses_no_message(self):
        """The drain test above failed once on CI with 11 of 12 (2026-09-08, on a PR that touched no postal
        code) and passed on either side of it. The dialed side names every message it lands by
        _unique() — the second, the pid and five random digits, 100k names per second per process — and
        deliver() published with rename(), which silently REPLACES a standing new/<name>. Two relays
        landing in one second with the same draw became one file: the first sender's message gone, both
        relays acked, both ledgers reading delivered (a drain of twelve loses one about once in 1500).
        Drives that collision exactly, through the mint the drain reads: the second mint returns the
        first's name. The standing message stays, the collided relay is refused rather than acked
        (silence on the wire, so the sender's outbox keeps it), and it rides the next exchange under a
        fresh name."""
        for i in range(12):
            pm.outbox_put("srv", {"mid": "big%02d" % i, "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                                  "body": ("%02d" % i).ljust(100_000, "R"), "kind": "coordinate", "t": i})
        real, minted = pmb._unique, []

        def collide_once():
            name = real()
            minted.append(name)
            return minted[0] if len(minted) == 2 else name    # the drain's second mint repeats its first

        pmb._unique = collide_once
        acked = []
        try:
            rounds = 0
            while pm.outbox_list("srv") and rounds < 10:
                req = pm.build_exchange_request("srv", wait=False)
                resp, status = pmb.peer_exchange_handle(req)
                self.assertEqual(status, 200)
                acked.append(resp["acks"])
                pm.peer_exchange_apply("srv", req, resp)
                rounds += 1
        finally:
            pmb._unique = real
        self.assertEqual(pm.outbox_list("srv"), [], "the backlog drained")
        box = pmb.read_box("sid-b", consume=True)
        self.assertEqual(len(box), 12, "every message arrived exactly once")
        self.assertEqual(sorted(m["body"][:2] for m in box), ["%02d" % i for i in range(12)],
                         "the collided message included, under its own name")
        self.assertEqual(len({m["id"] for m in box}), 12, "under twelve distinct names")
        self.assertNotIn("big01", acked[0], "the relay that hit the collision was not acked")
        self.assertIn("big01", acked[1], "it rode the next exchange and landed")
        self.assertEqual(len(minted), 13, "one extra mint: the refused relay was named afresh on its next ride")

    def test_a_refused_collision_leaves_the_standing_message_s_ledger_alone(self):
        """The first cut of the collision refusal wrote the row before the publish, and under this same
        forced collision it filed the refused message's `sent` row and then the refusal's `bounced` row
        under the colliding name — the STANDING message's id — so the dialed side's ledger (its timeline,
        its receipts, every reader that takes a bounced row on a sent id as terminal) showed a delivered
        message as bounced. The publish is the claim on the name now and the row follows it: the refusal
        writes nothing, the standing message keeps its one `sent` row, and the retry lands under a fresh
        id with a row of its own."""
        for i in range(3):
            pm.outbox_put("srv", {"mid": "m%d" % i, "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                                  "body": "note %d" % i, "kind": "coordinate", "t": i})
        ledger = pmb.TLDIR / "messages.jsonl"

        def rows():
            return [json.loads(l) for l in ledger.read_text().splitlines() if l] if ledger.exists() else []

        before = len(rows())
        real, minted = pmb._unique, []

        def collide_once():
            name = real()
            minted.append(name)
            return minted[0] if len(minted) == 2 else name    # the drain's second mint repeats its first

        pmb._unique = collide_once
        try:
            rounds = 0
            while pm.outbox_list("srv") and rounds < 10:
                self._exchange()
                rounds += 1
        finally:
            pmb._unique = real
        self.assertEqual(pm.outbox_list("srv"), [], "the backlog drained")
        self.assertEqual(len(minted), 4, "three lands, plus the refused relay's fresh name on its next ride")
        standing, fresh = minted[0], minted[-1]
        self.assertNotEqual(fresh, standing)
        added = rows()[before:]
        self.assertEqual([r["ev"] for r in added if r["id"] == standing], ["sent"],
                         "the standing message has exactly one sent row and no bounced row")
        self.assertEqual([r["ev"] for r in added], ["sent"] * 3, "three messages, three sent rows, nothing bounced")
        by_id = {r["id"]: r for r in added}
        self.assertEqual(len(by_id), 3, "three distinct ids: the refusal wrote no row under the standing id")
        self.assertEqual(by_id[standing]["originMid"], "m0", "the standing message's one row is its own")
        self.assertEqual(by_id[fresh]["originMid"], "m1", "the refused relay landed under a fresh id with its own row")
        # the receipts reader on the dialed side: nothing about the standing message reads bounced
        recs = {r["id"]: r for r in pmb._sent_receipts("sid-a")}
        self.assertIsNone(recs[standing]["bounced"], "the standing message's receipt is not bounced")
        self.assertIsNone(recs[fresh]["bounced"])
        self.assertEqual(recs[standing]["to"], "beta")

    def test_the_budget_holds_through_the_dialed_bus_s_own_body_gate(self):
        # the HTTP layer in the path: the dialed bus's _body reads a request only up to _POST_MAX_BYTES,
        # and a 413 would raise out of _peer_http here
        self._park(12, 100_000)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), pmb.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            rounds = 0
            while pm.outbox_list("srv") and rounds < 10:
                req = pm.build_exchange_request("srv", wait=False)
                resp = pm._peer_http(srv.server_address[1], req, token=pmb.SERVE_TOKEN)
                pm.peer_exchange_apply("srv", req, resp)
                rounds += 1
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(pm.outbox_list("srv"), [])
        self.assertEqual(len(pmb.read_box("sid-b", consume=True)), 12)

    def test_a_single_relay_over_the_budget_is_bounced_to_its_sender_naming_the_size(self):
        logged, saved = [], pm._log
        pm._log = lambda msg: logged.append(msg)
        try:
            pm.outbox_put("srv", {"mid": "huge", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                                  "body": "Z" * (pm._RELAY_BUDGET_BYTES + 1000), "kind": "coordinate", "t": 1})
            pm.outbox_put("srv", {"mid": "small", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                                  "body": "hi", "kind": "", "t": 2})
            req = pm.build_exchange_request("srv", wait=False)
        finally:
            pm._log = saved
        self.assertEqual([m["mid"] for m in req["relays"]], ["small"], "the oversize relay never rides")
        self.assertEqual([m["mid"] for m in pm.outbox_list("srv")], ["small"], "and left the outbox")
        back = pm.read_box("sid-a", consume=True)
        self.assertEqual(len(back), 1, "the sender got the bounce note")
        self.assertIn("undeliverable to 'beta' on srv", back[0]["body"])
        self.assertIn("exceeds the %d-byte relay limit" % pm._RELAY_BUDGET_BYTES, back[0]["body"])
        self.assertNotIn("ZZZZ", back[0]["body"], "the note names the size instead of repeating the body")
        self.assertEqual(back[0]["from"], "romp-postal")
        self.assertTrue(any("relay huge is" in l and "bounced to its sender" in l for l in logged), logged)

    def test_a_forwarded_relay_over_the_budget_bounces_backward_to_its_origin(self):
        pm.outbox_put("srv", {"mid": "fwd", "to": "beta", "frm": "gamma", "frm_id": "sid-c", "origin": "hostc",
                              "body": "Z" * (pm._RELAY_BUDGET_BYTES + 1000), "kind": "", "t": 1})
        req = pm.build_exchange_request("srv", wait=False)
        self.assertEqual(req["relays"], [])
        self.assertEqual(pm.outbox_list("srv"), [])
        b = pm._pending("hostc")["bounces"]
        self.assertEqual(len(b), 1, "the bounce rides back to the origin on its next exchange")
        self.assertEqual(b[0]["mid"], "fwd")
        self.assertIn("relay limit", b[0]["why"])
        self.assertTrue(b[0]["omitBody"])
        self.assertEqual(pm.read_box("sid-c", consume=True), [], "nothing lands locally for mail we only forwarded")

    def test_the_dialed_side_budgets_its_response_relays_too(self):
        for i in range(12):
            pmb.outbox_put("hosta", {"mid": "back%02d" % i, "to": "alpha", "frm": "beta", "frm_id": "sid-b",
                                     "body": "Q" * 100_000, "kind": "", "t": i})
        resp = self._exchange()
        self.assertLess(len(resp["relays"]), 12)
        self.assertLessEqual(len(json.dumps(resp["relays"]).encode()), pm._RELAY_BUDGET_BYTES)
        rounds = 1
        while pmb.outbox_list("hosta") and rounds < 10:   # each request acks the last response's relays
            self._exchange()
            rounds += 1
        self.assertEqual(pmb.outbox_list("hosta"), [])
        self.assertEqual(len(pm.read_box("sid-a", consume=True)), 12)


class RecallAndReceipts(unittest.TestCase):
    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        import shutil
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        try:
            (pm.TLDIR / "messages.jsonl").unlink()
        except Exception:
            pass

    def tearDown(self):
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def test_recall_reaches_the_outbox(self):
        pm.outbox_put("srv", {"mid": "q1", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "changed my mind", "kind": "", "t": 1})
        removed = pm._recall("sid-a", "", "q1")
        self.assertEqual([r["id"] for r in removed], ["q1"], "a recall that beats the truck wins")
        self.assertEqual(pm.outbox_list("srv"), [], "the parked message is gone")

    def test_recall_never_touches_forwarded_mail(self):
        pm.outbox_put("srv", {"mid": "q2", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "not mine to recall here", "kind": "", "t": 1, "origin": "hostz"})
        self.assertEqual(pm._recall("sid-a", "", "q2"), [], "forwarded mail belongs to the origin's sender")
        self.assertEqual(len(pm.outbox_list("srv")), 1)

    def test_receipts_show_parked_then_relayed(self):
        pm.outbox_put("srv", {"mid": "q3", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "hi", "kind": "", "t": 1})
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "q3", "from": "alpha",
                                         "from_id": "sid-a", "to_id": "peer:srv",
                                         "toName": "srv:beta", "body": "hi", "kind": ""})
        row = pm._sent_receipts("sid-a")[-1]
        self.assertEqual((row["to"], row["parked"]), ("srv:beta", "srv"), "parked shows, honestly")
        pm._ack_arrived("srv", "q3")                 # the end-to-end ack lands
        row = pm._sent_receipts("sid-a")[-1]
        self.assertEqual(row["parked"], None)
        self.assertTrue(row["relayed"], "delivery confirmation replaces parked")

    def test_a_parked_receipt_carries_the_link_state(self):
        # outbox residency alone is not unreachability (the user 2026-08-24): the receipt row now
        # rides the authoritative dial state the send path already branches on, so the client can
        # say "queued for relay" on a healthy link and "unreachable" only on a real dial failure
        self.addCleanup(lambda: pm.PEERS.pop("srv", None))   # a mid-test failure must not leak link state
        pm.outbox_put("srv", {"mid": "q9", "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": "hi", "kind": "", "t": 1})
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "q9", "from": "alpha",
                                         "from_id": "sid-a", "to_id": "peer:srv",
                                         "toName": "srv:beta", "body": "hi", "kind": ""})
        pm.PEERS["srv"] = {"up": True}
        self.assertTrue(pm._sent_receipts("sid-a")[-1]["parkedUp"], "healthy link -> queued, not lost")
        pm.PEERS["srv"] = {"up": False}
        self.assertFalse(pm._sent_receipts("sid-a")[-1]["parkedUp"], "down link -> honestly unreachable")
        pm.PEERS.pop("srv", None)
        self.assertFalse(pm._sent_receipts("sid-a")[-1]["parkedUp"],
                         "no tunnel record at all reads down, matching the send path's branch")
        row = pm._sent_receipts("sid-a")[-1]
        self.assertEqual(row["parked"], "srv", "the parked key keeps its host-string shape")


_SND = "11111111-2222-3333-4444-555555555555"
_RCP = "22222222-3333-4444-5555-666666666666"


class LedgerBeforeTheDelete(unittest.TestCase):
    """The accounting rows are the ONE record anyone reads (the sender's receipts, the timeline, the
    kernel's courier), so nothing irreversible stands without its row (2026-09-08): deliver()
    publishes first (the publish is the claim on the name) and writes the sent row second, and a row
    that cannot land takes the mail back out of new/ and refuses the send; _bounce_apply / _ack_arrived
    write the terminal row and only then delete the outbox record. On origin/main the row after the
    publish was best-effort and the delete came before its row: mail with no row anywhere, and records
    gone before their receipt existed. The two take-back outcomes that leave a delivered message with
    no row (a reader claimed it first; the file could not be removed) answer the id and are said to
    the user (a bell row), not only on stderr."""

    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        import shutil
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        shutil.rmtree(pm.MAILROOT, ignore_errors=True)
        shutil.rmtree(pm.MAILPENDING, ignore_errors=True)
        self._tl, self._log, self._post = pm.TLDIR, pm._log, pm._kernel_post
        self.logged, self.told = [], []
        pm._log = lambda m: self.logged.append(m)
        pm._kernel_post = lambda path, body, timeout=2: self.told.append((path, body)) or {"ok": True}
        try:
            (pm.TLDIR / "messages.jsonl").unlink()
        except OSError:
            pass
        if hasattr(pm, "_TL_FAULT"):
            pm._TL_FAULT[0] = False
        pm._DASHBOARD_MISSED[0] = False
        pm._REFUSAL_SAID.clear()

    def tearDown(self):
        pm.TLDIR, pm._log, pm._kernel_post = self._tl, self._log, self._post
        if hasattr(pm, "_TL_FAULT"):
            pm._TL_FAULT[0] = False
        pm._DASHBOARD_MISSED[0] = False
        pm._REFUSAL_SAID.clear()
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def _notices(self):
        return [b["text"] for p, b in self.told if p == "/postal-notice"]

    def _break_the_log(self):
        # TLDIR under a regular FILE: mkdir raises (ENOTDIR), so the REAL _tl_append fails the way a
        # full or read-only disk fails it — no stub stands in for the function under test
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(lambda: os.unlink(path))
        pm.TLDIR = type(pm.TLDIR)(path) / "timeline"

    def test_tl_append_reports_whether_the_row_landed(self):
        self.assertTrue(pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "r1"}))
        self._break_the_log()
        self.assertFalse(pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "r2"}))
        self.assertFalse(pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "r3"}))
        self.assertEqual(len([m for m in self.logged if "append failed" in m]), 1,
                         "one line per fault episode, not per call")

    def test_a_send_whose_row_cannot_land_is_refused_with_nothing_in_new(self):
        self._break_the_log()
        with self.assertRaises(pm.DeliveryNotRecorded) as cm:
            pm.deliver(_RCP, "web", _SND, "please review the schema", kind="question")
        self.assertIn("not delivered", str(cm.exception))
        self.assertIn("retry", str(cm.exception))
        newd, tmpd = pm.MAILROOT / _RCP / "new", pm.MAILROOT / _RCP / "tmp"
        self.assertEqual([p.name for p in newd.iterdir()] if newd.is_dir() else [], [],
                         "mail whose row cannot land is taken back out of new/")
        self.assertEqual([p.name for p in tmpd.iterdir()] if tmpd.is_dir() else [], [], "the temp is removed")
        self.assertFalse((pm.MAILPENDING / _RCP).exists(), "no pending marker for mail that never landed")

    def test_the_sent_row_follows_the_publish_it_names(self):
        # the order pin, executed: a recording _tl_append sees the mail ALREADY in new/ under the very
        # name the row carries. The publish is the claim on the name (link() refuses a standing one),
        # so a row is never written for a name that is not this message's — the first cut wrote the
        # row first and, under a collision, filed it under the standing message's id (the ledger pin
        # is in ExchangeRelaysAreBudgeted). A row that then fails takes the mail back (pinned above).
        seen = []
        saved = pm._tl_append
        newd = pm.MAILROOT / _RCP / "new"
        pm._tl_append = lambda f, o: seen.append(
            (o["ev"], o["id"], sorted(p.name for p in newd.iterdir()) if newd.is_dir() else [])) or True
        try:
            mid = pm.deliver(_RCP, "web", _SND, "hello")
        finally:
            pm._tl_append = saved
        self.assertEqual(seen, [("sent", mid, [mid])],
                         "the row is written once the mail stands in new/, under the name the row carries")

    def test_a_row_that_fails_after_a_reader_claimed_the_mail_answers_the_id(self):
        # the one interleaving the take-back cannot undo: read_box moved the file into cur/ in the
        # instant between the publish and the row. The message is in the recipient's hands, so the
        # answer is the id, not a refusal that would have the sender deliver it twice — and the gap
        # in the ledger is said out loud, by name.
        claimed = []
        saved = pm._tl_append

        def claim_then_fail(f, o):
            if o["ev"] == "sent":
                claimed.extend(m["id"] for m in pm.read_box(_RCP, consume=True))   # the reader beat the row
            return False

        pm._tl_append = claim_then_fail
        try:
            mid = pm.deliver(_RCP, "web", _SND, "hello")
        finally:
            pm._tl_append = saved
        self.assertEqual(claimed, [mid], "the reader took the message")
        self.assertTrue((pm.MAILROOT / _RCP / "cur" / mid).is_file(), "…and holds it")
        self.assertTrue(any(mid in m and "no record" in m for m in self.logged), "the missing row is said, by id")
        self.assertEqual(len([n for n in self._notices() if mid in n and "no record" in n]), 1,
                         "…and to the user, as one bell row (a delivered message the ledger will never carry)")

    def test_a_row_that_fails_when_the_mail_cannot_be_taken_back_answers_the_id_and_says_so(self):
        # the take-back's other failure: the row failed (the REAL _tl_append against a broken log) and
        # new/<id> stands, but its unlink is refused with something other than ENOENT (EROFS staged
        # on that one path; no chmod, so root runs it too). The message WILL be read, so the answer
        # is the id, the marker stands, no row was written, and the gap is said by name on stderr
        # AND as a bell row. Mutants: the arm removed (the error escapes deliver and the sender
        # delivers it twice); the arm returning without _mark_pending (mail in new/ with no marker).
        import errno
        import pathlib
        self._break_the_log()
        newd = pm.MAILROOT / _RCP / "new"
        real, refused = pathlib.Path.unlink, []

        def refuse_in_new(p, *a, **k):
            if p.parent == newd:
                refused.append(p.name)
                raise OSError(errno.EROFS, os.strerror(errno.EROFS), str(p))
            return real(p, *a, **k)

        pathlib.Path.unlink = refuse_in_new
        try:
            mid = pm.deliver(_RCP, "web", _SND, "hello")
        finally:
            pathlib.Path.unlink = real
        self.assertEqual(refused, [mid], "the take-back was attempted on the published name and refused")
        self.assertTrue((newd / mid).is_file(), "the message stands in new/: it could not be taken back")
        self.assertTrue((pm.MAILPENDING / _RCP).exists(), "the pending marker stands: the mail will be delivered")
        self.assertFalse((pm.TLDIR / "messages.jsonl").exists(), "no row was written")
        said = [m for m in self.logged if mid in m and "could not be taken back" in m]
        self.assertEqual(len(said), 1, "the failed take-back is said, by id, with the errno")
        self.assertIn("[Errno %d]" % errno.EROFS, said[0])
        self.assertEqual(len([n for n in self._notices() if mid in n and "could not be taken back" in n]), 1,
                         "…and as one bell row")

    def test_a_refused_publish_rings_the_bell_once_per_recipient_episode(self):
        # a refused publish writes no row, so the log says every refusal, and the USER hears it once
        # per recipient episode: the first refusal to a recipient is a bell row, the refusals that
        # follow it are log lines only, another recipient's refusal is its own episode, and a publish
        # to that recipient that lands re-arms it. Mutants: a bell per refusal (the dialer re-relays
        # a refused message every exchange, so a lasting fault would ring every few seconds); no
        # re-arm (the second episode is silent); one gate for every recipient.
        other = "33333333-4444-5555-6666-777777777777"
        first = pm.deliver(_RCP, "web", _SND, "the standing message")
        second = pm.deliver(other, "web", _SND, "another standing message")
        saved = pm._unique

        def collide(mid, to):
            pm._unique = lambda: mid                                   # the publish meets a standing name
            try:
                with self.assertRaises(pm.DeliveryNotRecorded):
                    pm.deliver(to, "web", _SND, "an impostor")
            finally:
                pm._unique = saved

        def bells():
            return [n for n in self._notices() if "refused, nothing recorded" in n]

        collide(first, _RCP)
        collide(first, _RCP)
        self.assertEqual(len([m for m in self.logged if "refused, nothing recorded" in m]), 2,
                         "the log says every refusal (no row does)")
        self.assertEqual(len(bells()), 1, "the user hears the recipient's episode once")
        self.assertIn(_RCP, bells()[0])
        self.assertIn("retries", bells()[0])
        collide(second, other)
        self.assertEqual(len(bells()), 2, "another recipient's refusal is its own episode")
        self.assertIn(other, bells()[1])
        collide(first, _RCP)
        self.assertEqual(len(bells()), 2, "the first recipient's episode is still open: no new bell")
        pm.deliver(_RCP, "web", _SND, "this one lands")               # re-arms that recipient's episode
        collide(first, _RCP)
        self.assertEqual(len(bells()), 3, "a refusal after a publish that landed is a new episode")
        self.assertEqual(len([m for m in self.logged if "refused, nothing recorded" in m]), 5)
        self.assertEqual(sorted(m["body"] for m in pm.read_box(_RCP, consume=False)),
                         ["the standing message", "this one lands"], "no impostor ever replaced the standing mail")

    def test_read_box_tolerates_a_file_the_take_back_removed_under_it(self):
        # the take-back is a deleter of new/ files on the live path (recall and the orphan sweep
        # already were): a reader that listed and read a file the instant before it vanished used to
        # raise out of the whole read at the rename into cur/, so /inbox and /drain answered nothing
        # for the rest of the box. The vanished message is nobody's to hand over (its sender was
        # refused and retries; a recalled one was unsent; a second reader has it), so it is dropped
        # from the listing with no exec row, and the rest of the box is served.
        kept = pm.deliver(_RCP, "web", _SND, "the one that stays")
        gone = pm.deliver(_RCP, "web", _SND, "the one taken back")
        newd = pm.MAILROOT / _RCP / "new"
        from pathlib import Path
        orig = Path.read_text

        def read_then_vanish(self_, *a, **k):
            text = orig(self_, *a, **k)
            if self_.name == gone and self_.parent.name == "new":
                os.unlink(self_)                                  # the take-back races in after the read
            return text

        Path.read_text = read_then_vanish
        try:
            got = pm.read_box(_RCP, consume=True)
        finally:
            Path.read_text = orig
        self.assertEqual([m["id"] for m in got], [kept], "the vanished message is not handed over; the rest is")
        curd = pm.MAILROOT / _RCP / "cur"
        self.assertEqual(sorted(p.name for p in curd.iterdir()), [kept])
        self.assertEqual(sorted(p.name for p in newd.iterdir()), [])
        rows = [json.loads(l) for l in (pm.TLDIR / "messages.jsonl").read_text().splitlines() if l]
        self.assertEqual([r["id"] for r in rows if r["ev"] == "exec"], [kept], "no exec row for a message nobody got")
        self.assertFalse((pm.MAILPENDING / _RCP).exists(), "the box is empty: the marker is dropped")

    def test_bounce_apply_writes_the_terminal_row_and_the_note_before_the_delete(self):
        pm.outbox_put("srv", {"mid": "b1", "to": "beta", "frm": "alpha", "frm_id": _SND,
                              "body": "ship it", "kind": "", "t": 1})
        calls = []
        saved = (pm._tl_append, pm.outbox_del, pm.deliver)
        pm._tl_append = lambda f, o: calls.append("row:" + o["ev"]) or True
        pm.outbox_del = lambda h, m: calls.append("del:" + m) or saved[1](h, m)
        pm.deliver = lambda *a, **k: calls.append("note") or "m-note"
        try:
            pm._bounce_apply("srv", {"mid": "b1", "why": "no live session named 'beta'"})
        finally:
            pm._tl_append, pm.outbox_del, pm.deliver = saved
        self.assertEqual(calls, ["row:bounced", "note", "del:b1"],
                         "terminal row, then the return note, then — only then — the delete")
        self.assertIsNone(pm.outbox_get("srv", "b1"), "the record does leave the outbox once accounted")

    def test_bounce_apply_keeps_the_record_when_the_row_cannot_land(self):
        pm.outbox_put("srv", {"mid": "b2", "to": "beta", "frm": "alpha", "frm_id": _SND,
                              "body": "ship it", "kind": "", "t": 1})
        self._break_the_log()
        pm._bounce_apply("srv", {"mid": "b2", "why": "no live session named 'beta'"})
        self.assertIsNotNone(pm.outbox_get("srv", "b2"),
                             "an unaccounted refusal keeps the record — the next exchange re-relays it")
        self.assertTrue(any("stays parked" in m for m in self.logged), "…and says so")
        newd = pm.MAILROOT / _SND / "new"
        self.assertFalse(newd.is_dir() and any(newd.iterdir()),
                         "no return note either: nothing is published without its row")

    def test_ack_arrived_writes_the_receipt_before_the_delete(self):
        pm.outbox_put("srv", {"mid": "a1", "to": "beta", "frm": "alpha", "frm_id": _SND,
                              "body": "hi", "kind": "", "t": 1})
        calls = []
        saved = (pm._tl_append, pm.outbox_del)
        pm._tl_append = lambda f, o: calls.append("row:" + o["ev"]) or True
        pm.outbox_del = lambda h, m: calls.append("del:" + m) or saved[1](h, m)
        try:
            pm._ack_arrived("srv", "a1")
        finally:
            pm._tl_append, pm.outbox_del = saved
        self.assertEqual(calls, ["row:relayed", "del:a1"], "the delivered receipt, then the delete")
        self._break_the_log()
        pm.outbox_put("srv", {"mid": "a2", "to": "beta", "frm": "alpha", "frm_id": _SND,
                              "body": "hi", "kind": "", "t": 1})
        pm._ack_arrived("srv", "a2")
        self.assertIsNotNone(pm.outbox_get("srv", "a2"), "a receipt that did not land keeps the record")


class StoresPublishAtomicallyAndQuarantineTornRecords(unittest.TestCase):
    """outbox_put / readbox_put publish through a same-directory temp + os.replace, so a reader never
    sees a half-written record; a record that still cannot be parsed is moved aside ONCE to
    `<name>.corrupt-<utc stamp>` with one log line, and the rest of the store is listed. On
    origin/main the put was a plain write_text and the list skipped an unparseable file silently on
    every pass, forever."""

    def setUp(self):
        import shutil
        shutil.rmtree(pm.OUTBOX, ignore_errors=True)
        shutil.rmtree(pm.READBOX, ignore_errors=True)
        self._log = pm._log
        self.logged = []
        pm._log = lambda m: self.logged.append(m)

    def tearDown(self):
        pm._log = self._log

    def test_puts_go_through_os_replace_and_leave_no_temp(self):
        replaced = []
        saved = os.replace
        os.replace = lambda a, b, *r, **k: replaced.append((str(a), str(b))) or saved(a, b, *r, **k)
        try:
            self.assertTrue(pm.outbox_put("srv", {"mid": "p1", "to": "beta", "body": "hi"}))
            self.assertTrue(pm.readbox_put("srv", {"mid": "p2", "t": 1}))
        finally:
            os.replace = saved
        self.assertEqual([os.path.basename(b) for _a, b in replaced], ["p1.json", "p2.json"],
                         "each record is published by an atomic replace of a finished temp")
        for a, b in replaced:
            self.assertEqual(os.path.dirname(a), os.path.dirname(b), "the temp lives in the store's own directory")
            self.assertFalse(a.endswith(".json"), "…under a name the *.json listing can never see")
        self.assertEqual([p.name for p in (pm.OUTBOX / "srv").iterdir()], ["p1.json"], "no temp left behind")
        self.assertEqual([p.name for p in (pm.READBOX / "srv").iterdir()], ["p2.json"])
        self.assertEqual([r["mid"] for r in pm.outbox_list("srv")], ["p1"])
        self.assertEqual([r["mid"] for r in pm.readbox_list("srv")], ["p2"])

    def test_a_torn_record_is_moved_aside_once_and_the_rest_is_listed(self):
        pm.outbox_put("srv", {"mid": "good", "to": "beta", "body": "hi"})
        (pm.OUTBOX / "srv" / "torn.json").write_text('{"mid": "torn", "to": "be')     # a half-written record
        self.assertEqual([r["mid"] for r in pm.outbox_list("srv")], ["good"], "the rest of the store is served")
        aside = sorted(p.name for p in (pm.OUTBOX / "srv").iterdir() if p.name.startswith("torn.json.corrupt-"))
        self.assertEqual(len(aside), 1, "the torn record is moved aside, kept as evidence")
        self.assertFalse((pm.OUTBOX / "srv" / "torn.json").exists())
        said = [m for m in self.logged if "torn.json" in m]
        self.assertEqual(len(said), 1, "one log line names it")
        self.assertIn(aside[0], said[0], "…and where it went")
        self.assertEqual([r["mid"] for r in pm.outbox_list("srv")], ["good"])
        self.assertEqual(sorted(p.name for p in (pm.OUTBOX / "srv").iterdir() if "corrupt" in p.name), aside,
                         "the second pass moves nothing again")
        self.assertEqual(len([m for m in self.logged if "torn.json" in m]), 1, "…and says nothing again")

    def test_readbox_shares_the_quarantine(self):
        pm.readbox_put("srv", {"mid": "good", "t": 1})
        (pm.READBOX / "srv" / "torn.json").write_text("[1, 2")
        self.assertEqual([r["mid"] for r in pm.readbox_list("srv")], ["good"])
        self.assertTrue(any(p.name.startswith("torn.json.corrupt-") for p in (pm.READBOX / "srv").iterdir()))

    def test_a_record_rewritten_under_the_read_is_left_for_the_next_pass(self):
        # the fingerprint guard: the parse fails because a writer REPLACED the file between the stat
        # and the read — a torn READ of a healthy record, never a torn record — and it must not be
        # moved aside. Pins the new mechanism against moving a live record; main had no move to guard.
        pm.outbox_put("srv", {"mid": "live", "to": "beta", "body": "v1"})
        target = pm.OUTBOX / "srv" / "live.json"
        real_json = pm.json

        class _Shim:
            dumps = staticmethod(real_json.dumps)
            fired = [False]

            @staticmethod
            def loads(text, *a, **k):
                if not _Shim.fired[0]:
                    _Shim.fired[0] = True
                    pm._atomic_json_put(target, {"mid": "live", "to": "beta", "body": "v2"})   # a concurrent rewrite
                    raise ValueError("torn read")
                return real_json.loads(text, *a, **k)

        pm.json = _Shim
        try:
            first = pm.outbox_list("srv")
        finally:
            pm.json = real_json
        self.assertEqual(first, [], "this pass skips the record it could not read whole")
        self.assertTrue(target.exists(), "…and leaves it in place")
        self.assertEqual([p.name for p in (pm.OUTBOX / "srv").iterdir() if "corrupt" in p.name], [],
                         "a rewritten record is never moved aside")
        self.assertEqual([r["body"] for r in pm.outbox_list("srv")], ["v2"], "the next pass lists the new bytes")
        self.assertEqual(self.logged, [], "nothing to say: no fault happened")


class RefusalArms(unittest.TestCase):
    """The arms the review found claimed but untested (2026-09-08), each named with the mutant it kills."""

    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        import shutil
        for d in (pm.OUTBOX, pm.READBOX, pm.MAILROOT):
            shutil.rmtree(d, ignore_errors=True)
        self._tl, self._log = pm.TLDIR, pm._log
        self.logged = []
        pm._log = lambda m: self.logged.append(m)
        try:
            (pm.TLDIR / "messages.jsonl").unlink()
        except OSError:
            pass
        pm._TL_FAULT[0] = False
        pm._peer_pending.clear()

    def tearDown(self):
        pm.TLDIR, pm._log = self._tl, self._log
        pm._TL_FAULT[0] = False
        pm._peer_pending.clear()
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def _break_the_log(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(lambda: os.unlink(path))
        pm.TLDIR = type(pm.TLDIR)(path) / "timeline"

    def _rows(self):
        p = self._tl / "messages.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l] if p.exists() else []

    def test_tl_append_says_once_when_the_log_writes_again(self):
        # mutant: the three recovery lines deleted → no "writes again" line and _TL_FAULT stays set
        self._break_the_log()
        self.assertFalse(pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "r1"}))
        self.assertTrue(pm._TL_FAULT[0])
        pm.TLDIR = self._tl
        self.assertTrue(pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "r2"}))
        self.assertTrue(pm._tl_append("messages.jsonl", {"t": 1, "ev": "sent", "id": "r3"}))
        self.assertEqual(len([m for m in self.logged if m == "timeline log messages.jsonl writes again"]), 1,
                         "exactly one recovery line (the fault line also ends in the phrase; pin the whole line)")
        self.assertFalse(pm._TL_FAULT[0], "the fault flag clears with it")

    def test_relay_in_answers_retry_when_the_local_delivery_is_refused(self):
        # mutant: no except → falls through to peer_seen_add + "ack": the mail is acked, marked seen, gone
        saved = (pm.local_agents_checked, pm._postal_off, dict(pm.PEERS), pm._seen_ids)
        pm.local_agents_checked = lambda threads=False: ([{"id": _RCP, "name": "api", "remote": False}], True)
        pm._postal_off = lambda sid: False
        pm.PEERS["TESTHOST"] = {"trust": "trusted", "up": True}
        pm._seen_ids = set()
        self._break_the_log()
        mid = "px-1700000000.1_" + "ab" * 16 + ".TESTHOST"
        try:
            verdict, bounce = pm._relay_in("TESTHOST", {"mid": mid, "to": "api", "frm": "web", "frm_id": _SND,
                                                        "body": "ship it", "kind": "coordinate"})
            seen = pm.peer_seen_check(mid)
        finally:
            pm.local_agents_checked, pm._postal_off, peers, pm._seen_ids = saved
            pm.PEERS.clear()
            pm.PEERS.update(peers)
        self.assertEqual((verdict, bounce), ("retry", None), "silence on the wire: the sender re-relays")
        self.assertFalse(seen, "not marked seen, so the re-relay is processed in full")
        newd = pm.MAILROOT / _RCP / "new"
        self.assertEqual([p.name for p in newd.iterdir()] if newd.is_dir() else [], [], "nothing landed")

    def test_bounce_arrived_queues_the_backward_bounce_before_the_delete(self):
        # mutant: delete first (main's order) → at the delete the backward queue is still empty
        pm.outbox_put("hub", {"mid": "f1", "to": "carol", "frm": "alpha", "frm_id": _SND,
                              "body": "hi", "kind": "", "t": 1, "origin": "originhost"})
        at_delete = []
        saved = pm.outbox_del
        pm.outbox_del = lambda h, m: at_delete.append(list(pm._pending("originhost")["bounces"])) or saved(h, m)
        b = {"mid": "f1", "why": "no live session named 'carol'"}
        try:
            pm._bounce_arrived("hub", b)
        finally:
            pm.outbox_del = saved
        self.assertEqual(at_delete, [[b]], "the backward bounce is already queued at the moment of the delete")
        self.assertIsNone(pm.outbox_get("hub", "f1"), "…and the forward does leave the outbox")

    def test_no_readable_record_means_no_delete(self):
        # mutant: main's get → del → check: the unparseable record is destroyed, evidence gone
        d = pm.OUTBOX / "srv"
        d.mkdir(parents=True, exist_ok=True)
        (d / "torn.json").write_text('{"mid": "torn", "to": "be')
        pm._bounce_apply("srv", {"mid": "torn", "why": "refused"})
        self.assertTrue((d / "torn.json").exists(), "a bounce for a record we cannot read deletes nothing")
        pm._ack_arrived("srv", "torn")
        self.assertTrue((d / "torn.json").exists(), "…nor does an ack")
        pm.outbox_list("srv")                                # the listing is what moves it aside
        self.assertTrue(any(p.name.startswith("torn.json.corrupt-") for p in d.iterdir()), "evidence kept")

    def test_a_torn_outbox_record_closes_its_ledger_and_the_receipt_says_refused(self):
        # mutant: no terminal row → check_sent reads "pending (not read yet)" forever
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "px-torn", "from": "alpha",
                                         "from_id": "sid-a", "to_id": "peer:srv",
                                         "toName": "srv:beta", "body": "hi", "kind": ""})
        d = pm.OUTBOX / "srv"
        d.mkdir(parents=True, exist_ok=True)
        (d / "px-torn.json").write_text("{torn")
        self.assertEqual(pm.outbox_list("srv"), [])
        term = [r for r in self._rows() if r.get("ev") == "bounced" and r.get("id") == "px-torn"]
        self.assertEqual(len(term), 1, "one terminal row for the mid the filename names")
        self.assertEqual((term[0]["host"], term[0]["why"]), ("srv", pm.WHY_OUTBOX_UNREADABLE))
        row = pm._sent_receipts("sid-a")[-1]
        self.assertTrue(row["bounced"])
        self.assertEqual(row["bouncedWhy"], pm.WHY_OUTBOX_UNREADABLE)
        txt = pm.format_receipts([row])
        self.assertIn("refused — " + pm.WHY_OUTBOX_UNREADABLE, txt)
        self.assertNotIn("returned to you", txt, "no return note exists for a refusal")
        pm.outbox_list("srv")
        self.assertEqual(len([r for r in self._rows() if r.get("ev") == "bounced"]), 1, "the second pass adds nothing")

    def test_a_torn_readbox_record_closes_no_ledger(self):
        pm.readbox_put("srv", {"mid": "good", "t": 1})
        (pm.READBOX / "srv" / "torn.json").write_text("[1, 2")
        self.assertEqual([r["mid"] for r in pm.readbox_list("srv")], ["good"])
        self.assertEqual([r for r in self._rows() if r.get("ev") == "bounced"], [], "a receipt is not a message")

    def test_a_refused_oversize_bounce_puts_the_claimed_message_back(self):
        # the rebase onto the oversize-bounce change (PR #1038) created this arm: _bounce_oversize drops a
        # claimed message and mails its local sender a note; with the note REFUSED the message must not
        # sit in cur/ with no note and no row. Mutant: no except → the refusal escapes into _push's
        # catch-all and the message is stranded.
        saved_name = pm._name_for_id
        pm._name_for_id = lambda sid: "api"
        try:
            mid = pm.deliver(_RCP, "web", _SND, "x" * 64, kind="coordinate")
            m = pm.read_box(_RCP, consume=True)[0]                # the drain claims it (new/ → cur/)
            self.assertEqual(m["id"], mid)
            self._break_the_log()
            pm._bounce_oversize(_RCP, m)                          # must not raise
            self.assertTrue((pm.MAILROOT / _RCP / "new" / mid).exists(), "put back for the next pass")
            self.assertEqual(pm.read_box(_SND, consume=False), [], "no note was published without its row")
            self.assertEqual(len([x for x in self.logged if "kept for the next pass" in x]), 1)
            pm.TLDIR = self._tl                                    # the log writes again: the next pass
            m = pm.read_box(_RCP, consume=True)[0]
            pm._bounce_oversize(_RCP, m)
            self.assertFalse((pm.MAILROOT / _RCP / "new" / mid).exists(), "…and the bounce completes")
            notes = pm.read_box(_SND, consume=False)
            self.assertEqual(len(notes), 1)
            self.assertIn("undeliverable to 'api'", notes[0]["body"])
            evs = [r["ev"] for r in self._rows() if r.get("id") == mid]
            self.assertEqual((evs[-1], evs.count("bounced")), ("bounced", 1),
                             "one terminal row, written only once the note had landed (the roll-back's unexec "
                             "could not land while the log was down — that window is what restore() is for)")
        finally:
            pm._name_for_id = saved_name


class _LoudBus(unittest.TestCase):
    """Fixture for the review fixes of 2026-09-08: clean stores, the log captured, the kernel leg of
    _refused_notice captured (`told`), the once-per-episode registries reset. No tests of its own."""

    def setUp(self):
        os.environ["ROMP_POSTAL_PEERS"] = "1"
        import shutil
        for d in (pm.OUTBOX, pm.READBOX, pm.MAILROOT, pm.MAILPENDING):
            shutil.rmtree(d, ignore_errors=True)
        self._saved = (pm.TLDIR, pm._log, pm._kernel_post, pm.local_agents, pm.local_agents_checked)
        self.logged, self.told = [], []
        pm._log = lambda m: self.logged.append(m)
        pm._kernel_post = lambda path, body, timeout=2: self.told.append((path, body)) or {"ok": True}
        pm.local_agents = lambda threads=False: []
        pm.local_agents_checked = lambda threads=False: ([], True)
        try:
            (pm.TLDIR / "messages.jsonl").unlink()
        except OSError:
            pass
        pm._TL_FAULT[0] = False
        pm._DASHBOARD_MISSED[0] = False
        pm._NOTE_FAILED_SAID.clear()
        pm._UNREADABLE_SAID.clear()
        pm._REFUSAL_SAID.clear()
        pm._peer_pending.clear()

    def tearDown(self):
        pm.TLDIR, pm._log, pm._kernel_post, pm.local_agents, pm.local_agents_checked = self._saved
        pm._TL_FAULT[0] = False
        pm._DASHBOARD_MISSED[0] = False
        pm._NOTE_FAILED_SAID.clear()
        pm._REFUSAL_SAID.clear()
        pm._peer_pending.clear()
        os.environ.pop("ROMP_POSTAL_PEERS", None)

    def _rows(self):
        p = pm.TLDIR / "messages.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l] if p.exists() else []

    def _notices(self):
        return [b["text"] for p, b in self.told if p == "/postal-notice"]


class OneBadRelayNeverAbortsTheExchange(_LoudBus):
    """_bounce_apply bounds the return note's failure (review find, 2026-09-08). With the record kept
    until it is accounted, a deliver() exception other than a refusal escaped the handler and aborted
    the WHOLE exchange; the peer re-bounced the still-parked record next exchange, so the abort
    recurred forever and every other relay, ack and receipt in those exchanges was lost with it.
    Mutant: the generic except removed (RuntimeError escapes peer_exchange_handle).

    And a sender with no mailbox gets no note (2026-09-10): a `--from <label>` script mails under the
    synthetic id `ext:<label>`, which _safe_id refuses, so the note's deliver() raises ValueError — the
    shape of the id, not a fault the next exchange clears. Counted as a fault, the record stayed parked
    and the peer re-bounced it every exchange, a `bounced` row per round. Mutant: the _safe_id gate on
    the sender id removed."""

    def _exchange(self, bounce="n1"):
        return pm.peer_exchange_handle({"host": "srv", "proto": pm.PEER_PROTO, "epoch": 1, "busId": "b" * 32,
                                        "presence": [], "holds": [], "relays": [], "acks": ["n2"],
                                        "bounces": [{"mid": bounce, "why": "no live session named 'beta'"}],
                                        "reads": [], "readAcks": []})

    def test_a_note_that_raises_keeps_the_record_says_once_and_the_exchange_completes(self):
        for mid in ("n1", "n2"):
            pm.outbox_put("srv", {"mid": mid, "to": "beta", "frm": "alpha", "frm_id": _SND,
                                  "body": "ship it", "kind": "", "t": 1})
        saved = pm.deliver
        pm.deliver = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mailbox on fire"))
        try:
            payload, status = self._exchange()                         # must not raise
            self.assertEqual(status, 200, "the exchange completes")
            self.assertIsNotNone(pm.outbox_get("srv", "n1"), "the bounced record stays parked for the next exchange")
            self.assertIsNone(pm.outbox_get("srv", "n2"), "the ack in the same exchange was processed")
            evs = {(r["ev"], r["id"]) for r in self._rows()}
            self.assertIn(("bounced", "n1"), evs, "the terminal row landed before the note was tried")
            self.assertIn(("relayed", "n2"), evs)
            said = [m for m in self.logged if "n1" in m and "could not be delivered" in m]
            self.assertEqual(len(said), 1)
            self.assertIn("RuntimeError: mailbox on fire", said[0], "the line names the fault")
            self.assertEqual(len(self._notices()), 1, "one bell row for a fault that would recur every exchange")
            self._exchange()                                           # the peer re-bounces it
            self.assertEqual((len([m for m in self.logged if "could not be delivered" in m]), len(self._notices())),
                             (1, 1), "said once per message, not per exchange")
        finally:
            pm.deliver = saved
        self._exchange()                                               # the note lands
        self.assertIsNone(pm.outbox_get("srv", "n1"), "and the record leaves once the note is delivered")
        self.assertEqual(len(pm.read_box(_SND, consume=False)), 1)

    def test_a_bounce_to_a_from_label_sender_retires_on_its_row_instead_of_re_relaying(self):
        pm.outbox_put("srv", {"mid": "e1", "to": "beta", "frm": "cron", "frm_id": "ext:cron",
                              "body": "nightly report attached", "kind": "", "t": 1})
        payload, status = self._exchange(bounce="e1")                  # the peer refuses it: no live 'beta'
        self.assertEqual(status, 200, "the exchange completes")
        self.assertIsNone(pm.outbox_get("srv", "e1"),
                          "there is no mailbox to note, so the record retires on its bounced row at once")
        self._exchange(bounce="e1")                                    # the peer bounces the same mid again
        self.assertEqual([r["ev"] for r in self._rows() if r.get("id") == "e1"], ["bounced"],
                         "one terminal row for the message, not one per exchange")
        said = [m for m in self.logged if "e1" in m and "no mailbox" in m]
        self.assertEqual(len(said), 1, "said once, in the log")
        self.assertIn("cron", said[0], "the line names the sender's label")
        self.assertIn("ext:cron", said[0], "and the id that is no mailbox")
        self.assertEqual(self._notices(), [], "not a fault: nothing to fix, so no bell row")
        self.assertFalse((pm.MAILROOT / "ext:cron").exists(), "and no mailbox was made for the label")


class NewFalseReturnsAreHonoured(_LoudBus):
    """The False the stores learned to return is read everywhere it was ignored (review find,
    2026-09-08). Mutants: outbox_put's False ignored at the relay forward ('hold' with nothing
    parked); the forwarded-ack order reverted (delete before the backward queue)."""

    def test_relay_in_answers_retry_when_the_forward_cannot_be_parked(self):
        saved = (pm.peer_route, pm.outbox_put)
        pm.peer_route = lambda to: ("farhost", {"name": "carol", "id": ""})
        pm.outbox_put = lambda h, m: False
        m = {"mid": "px-fwd", "to": "carol", "frm": "alpha", "frm_id": _SND, "body": "hi", "kind": ""}
        try:
            verdict = pm._relay_in("srv", m)
        finally:
            pm.peer_route, pm.outbox_put = saved
        self.assertEqual(verdict, ("retry", None), "silence on the wire: the sender re-relays")
        self.assertIsNone(pm.outbox_get("farhost", "px-fwd"))
        pm.peer_route = lambda to: ("farhost", {"name": "carol", "id": ""})
        try:
            self.assertEqual(pm._relay_in("srv", m), ("hold", None), "and a park that lands forwards as before")
        finally:
            pm.peer_route = saved[0]
        self.assertEqual(pm.outbox_get("farhost", "px-fwd")["origin"], "srv")

    def test_ack_arrived_queues_the_backward_ack_before_the_delete(self):
        pm.outbox_put("hub", {"mid": "fa1", "to": "carol", "frm": "alpha", "frm_id": _SND,
                              "body": "hi", "kind": "", "t": 1, "origin": "originhost"})
        at_delete = []
        saved = pm.outbox_del
        pm.outbox_del = lambda h, m: at_delete.append(list(pm._pending("originhost")["acks"])) or saved(h, m)
        try:
            pm._ack_arrived("hub", "fa1")
        finally:
            pm.outbox_del = saved
        self.assertEqual(at_delete, [["fa1"]], "the backward ack is already queued at the moment of the delete")
        self.assertIsNone(pm.outbox_get("hub", "fa1"), "and the forward does leave the outbox")


class StoreFaultsAreLoud(_LoudBus):
    """_atomic_json_put's failure path and _list_json_records' unreadable arm (review find,
    2026-09-08). An unreadable record is moved aside like a torn one, once, with its ledger closed
    and a bell row; the first cut skipped it in place on every exchange."""

    def test_a_failed_replace_raises_and_leaves_no_temp(self):
        import errno
        d = pm.OUTBOX / "srv"
        saved = os.replace
        os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "staged by the test"))
        try:
            with self.assertRaises(OSError):
                pm._atomic_json_put(d / "x.json", {"mid": "x"})
            self.assertEqual([p.name for p in d.iterdir()], [], "no temp and no record")
            self.assertFalse(pm.outbox_put("srv", {"mid": "x", "to": "beta", "body": "hi"}), "the put reports it")
        finally:
            os.replace = saved
        self.assertTrue(any("could not be written" in m for m in self.logged), "and says it")
        self.assertEqual([p.name for p in d.iterdir()], [])

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-0 file; the fault cannot be staged")
    def test_an_unreadable_record_is_moved_aside_once_and_closes_its_ledger(self):
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "px-locked", "from": "alpha",
                                         "from_id": _SND, "to_id": "peer:srv", "toName": "srv:beta",
                                         "body": "hi", "kind": "question"})
        pm.outbox_put("srv", {"mid": "px-locked", "to": "beta", "frm": "alpha", "frm_id": _SND, "body": "hi"})
        pm.outbox_put("srv", {"mid": "good", "to": "beta", "frm": "alpha", "frm_id": _SND, "body": "hi"})
        os.chmod(pm.OUTBOX / "srv" / "px-locked.json", 0)
        self.assertEqual([r["mid"] for r in pm.outbox_list("srv")], ["good"], "the rest of the store is served")
        self.assertFalse((pm.OUTBOX / "srv" / "px-locked.json").exists())
        aside = [p.name for p in (pm.OUTBOX / "srv").iterdir() if p.name.startswith("px-locked.json.corrupt-")]
        self.assertEqual(len(aside), 1, "moved aside, kept as evidence")
        term = [r for r in self._rows() if r.get("ev") == "bounced" and r.get("id") == "px-locked"]
        self.assertEqual([(r["host"], r["why"]) for r in term], [("srv", pm.WHY_OUTBOX_UNREADABLE)])
        self.assertEqual(len([m for m in self.logged if "px-locked.json" in m]), 1)
        self.assertEqual(len(self._notices()), 1, "one bell row")
        self.assertIn("could not be read", self._notices()[0])
        self.assertEqual([r["mid"] for r in pm.outbox_list("srv")], ["good"])
        self.assertEqual((len([m for m in self.logged if "px-locked.json" in m]), len(self._notices())), (1, 1),
                         "the second pass moves nothing and says nothing")
        self.assertIn("refused", pm.format_receipts([pm._sent_receipts(_SND)[-1]]))


class BusStartSweepsUnfinishedWrites(_LoudBus):
    """Bus start is the one moment no writer of ours runs, so the sweep reconciles what a crash left
    (review find, 2026-09-08): a temp is removed, and its id is closed as refused only when a sent
    row stands with the message nowhere (a phantom whichever order wrote it); a temp beside a message
    that stands in new/ or cur/ is the leftover of a publish that landed (deliver tolerates a temp
    it cannot remove), so the ledger is left alone; a message in new/ with no sent row is the crash
    window between the publish and the row, and gets its row now; each is said once per file and
    once as a bell row. Sidecars are evidence and stay."""

    def _rows_for(self, mid):
        return [r["ev"] for r in self._rows() if r.get("id") == mid]

    def test_a_temp_beside_a_standing_message_is_removed_and_its_ledger_left_alone(self):
        # the planted state: two delivered messages (one read, one not) whose publish could not remove
        # its temp, the only way this writer leaves a temp beside a sent row. Before the fix the sweep
        # closed both as refused: a message the recipient had READ read refused to its sender and
        # dropped out of the kernel's ask maps after every bus restart.
        mb = pm._mailbox(_RCP)
        for mid in ("m-read", "m-unread"):
            pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": mid, "from": "alpha", "from_id": _SND,
                                             "to_id": _RCP, "body": "hi", "kind": "question"})
            (mb / "tmp" / mid).write_text("From: alpha\n\nhi\n")
        pm._tl_append("messages.jsonl", {"t": 11, "ev": "exec", "id": "m-read"})
        (mb / "cur" / "m-read").write_text("From: alpha\n\nhi\n")
        (mb / "new" / "m-unread").write_text("From: alpha\n\nhi\n")
        pm._sweep_unfinished_writes()
        self.assertEqual([p.name for p in (mb / "tmp").iterdir()], [], "the temps are gone")
        self.assertTrue((mb / "cur" / "m-read").is_file() and (mb / "new" / "m-unread").is_file(), "the mail stands")
        self.assertEqual(self._rows_for("m-read"), ["sent", "exec"], "a read message stays read: no bounced row")
        self.assertEqual(self._rows_for("m-unread"), ["sent"], "an unread message stays pending: no bounced row")
        recs = {r["id"]: r for r in pm._sent_receipts(_SND)}
        self.assertEqual([recs["m-read"]["bounced"], recs["m-unread"]["bounced"]], [None, None])
        self.assertIn("read", pm.format_receipts([recs["m-read"]]))
        self.assertIn("pending", pm.format_receipts([recs["m-unread"]]))
        said = [m for m in self.logged if "removed at start" in m]
        self.assertEqual(len(said), 2, "one line per temp")
        self.assertTrue(all("reached the inbox" in m for m in said), "…each saying the message itself stands")
        self.assertEqual(len(self._notices()), 1)
        self.assertIn("2 unfinished mail write(s)", self._notices()[0])
        self.assertIn("0 sender receipt(s) now read refused", self._notices()[0])
        self.assertIn("2 of them the temp of a message that had reached the inbox", self._notices()[0])
        self.assertEqual([m["id"] for m in pm.read_box(_RCP, consume=False)], ["m-unread"], "still delivered")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod cannot refuse root the unlink")
    def test_a_temp_the_publish_could_not_remove_is_read_as_a_finished_publish(self):
        # the real path, no mock on unlink: tmp/ is made read-only the instant link() has placed the
        # message in new/, so _publish_new's tmp.unlink() meets a real EACCES and tolerates it (the
        # message is out), the sent row lands, the recipient reads it, and the bus restarts.
        import errno
        tmpd = pm._mailbox(_RCP) / "tmp"
        saved_link = os.link

        def link_then_lock(src, dst, *a, **k):
            saved_link(src, dst, *a, **k)
            os.chmod(tmpd, 0o555)

        os.link = link_then_lock
        try:
            mid = pm.deliver(_RCP, "web", _SND, "please review the schema", kind="question")
        finally:
            os.link = saved_link
            os.chmod(tmpd, 0o755)
        self.assertEqual([p.name for p in tmpd.iterdir()], [mid], "the temp lingered (its unlink was refused)")
        lingered = [m for m in self.logged if "its temp could not be removed" in m]
        self.assertEqual(len(lingered), 1)
        self.assertIn(os.strerror(errno.EACCES), lingered[0])
        self.assertEqual([m["id"] for m in pm.read_box(_RCP, consume=True)], [mid], "the recipient reads it")
        pm._sweep_unfinished_writes()
        self.assertEqual([p.name for p in tmpd.iterdir()], [], "the temp is gone")
        self.assertTrue((pm.MAILROOT / _RCP / "cur" / mid).is_file(), "the read message stands in cur/")
        self.assertEqual(self._rows_for(mid), ["sent", "exec"], "a finished publish is not closed as refused")
        rec = pm._sent_receipts(_SND)[0]
        self.assertEqual((rec["id"], rec["bounced"]), (mid, None))
        self.assertTrue(rec["exec"], "the sender's receipt still reads read")
        self.assertEqual(len(self._notices()), 1)
        self.assertIn("0 sender receipt(s) now read refused", self._notices()[0])

    def test_mail_in_new_with_no_sent_row_gets_its_row_at_start(self):
        # the crash window between the publish and the row: the message stands in new/ (it WILL be
        # delivered by the next read) and the ledger has never heard of it. The state is made by the
        # writer itself with the row's append swallowed: a local question, a parked handoff and a
        # relayed message, so every header deliver writes is recovered into the row.
        saved = pm._tl_append
        pm._tl_append = lambda f, o: True                         # the row "lands" nowhere: the crash window
        try:
            q = pm.deliver(_RCP, "web", _SND, "please review the schema", kind="question")
            h = pm.deliver(_RCP, "web", _SND, "take over the api tests", park=True, kind="delegate")
            r = pm.deliver(_RCP, "api", "33333333-4444-5555-6666-777777777777", "from afar", kind="coordinate",
                           from_host="TESTHOST", relay_mid="px-far-1", relay_via="TESTHOST")
        finally:
            pm._tl_append = saved
        ok = pm.deliver(_RCP, "web", _SND, "this one has its row")
        self.assertEqual(self._rows_for(q) + self._rows_for(h) + self._rows_for(r), [], "the ledger knows none of the three")
        pm._sweep_unfinished_writes()
        rows = {r_["id"]: r_ for r_ in self._rows() if r_["ev"] == "sent"}
        self.assertEqual(sorted(rows), sorted([q, h, r, ok]), "every message in new/ now has exactly one sent row")
        self.assertEqual(len([r_ for r_ in self._rows() if r_["ev"] == "sent" and r_["id"] == ok]), 1,
                         "a message with its row is not rowed again")
        got = rows[q]
        self.assertEqual((got["from"], got["from_id"], got["to_id"], got["body"], got["kind"], got["from_host"]),
                         ("web", _SND, _RCP, "please review the schema", "question", ""))
        self.assertTrue(got["recovered"], "the row says it was written at start, not by the send")
        self.assertTrue(abs(got["t"] - int(time.time())) < 120, "t comes from the message's Date header")
        self.assertTrue(rows[h]["park"] and rows[h]["kind"] == "delegate")
        self.assertNotIn("park", rows[q])
        self.assertEqual((rows[r]["from"], rows[r]["from_host"], rows[r]["originMid"], rows[r]["kind"]),
                         ("api", "TESTHOST", "px-far-1", "coordinate"))
        self.assertTrue(pm.peer_seen_check("px-far-1"),
                        "the relayed one's origin mid is marked seen: the dialer's re-relay is acked as a duplicate")
        self.assertNotIn("originMid", rows[q], "a local message has no origin mid")
        recs = {r_["id"]: r_ for r_ in pm._sent_receipts(_SND)}
        self.assertEqual(sorted(recs), sorted([q, h, ok]), "the sender's receipts now list them")
        self.assertIn("pending", pm.format_receipts([recs[q]]))
        self.assertEqual(len([m for m in self.logged if "no record" in m and "row written at start" in m]), 3,
                         "one stderr line per recovered message")
        self.assertEqual(len(self._notices()), 1, "one bell row for the sweep")
        self.assertIn("3 delivered message(s)", self._notices()[0])
        self.assertEqual(len(pm.read_box(_RCP, consume=False)), 4, "all four still stand for delivery")
        pm._sweep_unfinished_writes()
        self.assertEqual((len(self._rows()), len(self._notices())), (4, 1), "the next start finds nothing to do")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads through chmod 0")
    def test_mail_in_new_with_no_sent_row_that_cannot_be_read_is_moved_aside(self):
        # the unreadable branch: the file's fields cannot be recovered, so it goes the way read_box
        # sends an unreadable inbox file (aside, once, with a terminal row and a bell row), and it
        # counts as nothing recovered.
        mb = pm._mailbox(_RCP)
        bad = mb / "new" / "m-bad"
        bad.write_text("From: alpha\n\nhi\n")
        os.chmod(bad, 0o000)
        good = pm.deliver(_RCP, "web", _SND, "fine")
        pm._sweep_unfinished_writes()
        self.assertEqual([p.name for p in (mb / "new").iterdir()], [good], "the unreadable file is out of new/")
        aside = [p.name for p in mb.iterdir() if p.name.startswith("m-bad.corrupt-")]
        self.assertEqual(len(aside), 1, "…moved aside beside new/, never deleted")
        os.chmod(mb / aside[0], 0o644)
        self.assertEqual(self._rows_for("m-bad"), ["bounced"])
        self.assertEqual(self._rows_for(good), ["sent"])
        self.assertEqual(len(self._notices()), 1, "the move-aside's bell row, and no recovery row to report")
        self.assertIn("could not be read", self._notices()[0])
        self.assertNotIn("delivered message(s)", self._notices()[0])

    def test_a_row_that_cannot_be_written_at_start_leaves_the_mail_and_says_so(self):
        # the log is faulted at start too: the message stays in new/ (it is still delivered), nothing
        # claims a row was written, and the next start with a working log writes it.
        saved = pm._tl_append
        pm._tl_append = lambda f, o: True
        try:
            q = pm.deliver(_RCP, "web", _SND, "please review the schema", kind="question")
        finally:
            pm._tl_append = saved
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(lambda: os.unlink(path))
        good_tl = pm.TLDIR
        pm.TLDIR = type(pm.TLDIR)(path) / "timeline"                 # mkdir raises: the real _tl_append fails
        try:
            pm._sweep_unfinished_writes()
        finally:
            pm.TLDIR = good_tl
        self.assertEqual(self._rows_for(q), [], "no row landed")
        self.assertTrue((pm.MAILROOT / _RCP / "new" / q).is_file(), "the mail stands")
        self.assertEqual(len([m for m in self.logged if q in m and "could not be written" in m]), 1)
        self.assertEqual(self._notices(), [], "nothing claims a row was written")
        pm._sweep_unfinished_writes()
        self.assertEqual(self._rows_for(q), ["sent"], "the next start with a working log writes it")
        self.assertEqual(len(self._notices()), 1)

    def test_temps_are_removed_ledgers_closed_and_said_once_and_sidecars_kept(self):
        for mid, to in (("m-tmp", _RCP), ("m-done", _RCP), ("px-tmp", "peer:srv")):
            pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": mid, "from": "alpha", "from_id": _SND,
                                             "to_id": to, "body": "hi", "kind": "question"})
        pm._tl_append("messages.jsonl", {"t": 11, "ev": "bounced", "id": "m-done", "why": "already closed"})
        tmpd = pm.MAILROOT / _RCP / "tmp"
        tmpd.mkdir(parents=True)
        (tmpd / "m-tmp").write_text("From: alpha\n\nhalf")
        (tmpd / "m-done").write_text("From: alpha\n\nhalf")
        pm.outbox_put("srv", {"mid": "good", "to": "beta", "frm": "alpha", "frm_id": _SND, "body": "hi"})
        (pm.OUTBOX / "srv" / "px-tmp.json.tmp-1-abcd").write_text('{"mid": "px-t')
        (pm.OUTBOX / "srv" / "old.json.corrupt-20260101T000000Z").write_text("{torn")
        (pm.READBOX / "srv").mkdir(parents=True)
        (pm.READBOX / "srv" / "r1.json.tmp-2-beef").write_text("{")
        pm._sweep_unfinished_writes()
        self.assertEqual([p.name for p in tmpd.iterdir()], [], "the maildir temps are gone")
        self.assertEqual(sorted(p.name for p in (pm.OUTBOX / "srv").iterdir()),
                         ["good.json", "old.json.corrupt-20260101T000000Z"], "the store temp is gone; the record and the sidecar stay")
        self.assertEqual([p.name for p in (pm.READBOX / "srv").iterdir()], [])
        term = {r["id"]: r for r in self._rows() if r.get("ev") == "bounced"}
        self.assertEqual(term["m-tmp"]["why"], pm.WHY_STOPPED_BEFORE_PUBLISH)
        self.assertEqual(term["m-tmp"]["to_id"], _RCP)
        self.assertEqual((term["px-tmp"]["host"], term["px-tmp"]["why"]), ("srv", pm.WHY_STOPPED_BEFORE_PARK))
        self.assertEqual(len([r for r in self._rows() if r.get("ev") == "bounced" and r.get("id") == "m-done"]), 1,
                         "an id already closed is not closed again")
        self.assertEqual(len([m for m in self.logged if "removed at start" in m]), 4, "one line per file")
        self.assertEqual(len(self._notices()), 1, "one bell row for the sweep")
        self.assertIn("4 unfinished mail write(s)", self._notices()[0])
        self.assertIn("2 sender receipt(s) now read refused", self._notices()[0])
        recs = {r["id"]: r for r in pm._sent_receipts(_SND)}
        for mid in ("m-tmp", "px-tmp"):
            self.assertIn("refused", pm.format_receipts([recs[mid]]), mid)
        pm._sweep_unfinished_writes()
        self.assertEqual((len([m for m in self.logged if "removed at start" in m]), len(self._notices())), (4, 1),
                         "a second start with nothing to sweep says nothing")

    def test_serve_runs_the_sweep_before_it_binds(self):
        import inspect
        src = inspect.getsource(pm.serve)
        self.assertIn("_sweep_unfinished_writes()", src)
        self.assertLess(src.index("_sweep_unfinished_writes()"), src.index("ThreadingHTTPServer("),
                        "the sweep runs at start, before any writer of ours can run")


class RecallAfterTheCarry(_TwoBusHarness):
    """A recall reaches a parked cross-host record only until an exchange CARRIES it (2026-09-08). The
    record stays in the outbox until the END-TO-END ack — one round trip normally, a whole outage when
    a response was lost and the dialer re-relays under its backoff — and the far bus delivers on
    arrival, so on origin/main a recall in that window unlinked a message the recipient already held
    and filed a terminal recall row for it: the sender's receipts read withdrawn for a message that
    was read, and any reader that treats a recall as final would close a live ask on it.

    The carry is an exact mark on the record (`carried`, `carriedVia`), keyed on the exchange's
    OUTCOME, never on the request being built (a first cut marked at build and so refused a recall
    through every failed dial): the dialer marks on a response, or on a failure raised after the
    request went out; a refused status or a failed connect marks nothing; the dialed side marks once
    its response write returned. From the listing to that outcome the record is in flight, and a
    recall is told it is on its way. A carried record is refused with the reason (`kept`), stays, and
    writes no row; every recall row names its box (`new` / `outbox`); check_sent shows a carried
    record as left, awaiting confirmation, the same fact the recall refuses on. Two exchanges for one
    host run at once by design (our dialer, their dial of us): each listing is its own flight, and ending
    one releases nothing another still holds."""

    def setUp(self):
        super().setUp()
        for m in (pm, pmb):
            try:
                (m.TLDIR / "messages.jsonl").unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _open(m, host):
        """The mids every open flight for `host` holds on bus `m`."""
        return set().union(*((m._inflight.get(host) or {}).values()))

    def _rows(self, m=None):
        log = (m or pm).TLDIR / "messages.jsonl"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    def _recall_rows(self, m=None):
        return [r for r in self._rows(m) if r.get("ev") == "recall"]

    def _park(self, mid="c1", body="please review the notes-api branch"):
        pm.outbox_put("srv", {"mid": mid, "to": "beta", "frm": "alpha", "frm_id": "sid-a",
                              "body": body, "kind": "question", "t": 1})

    def _dial(self, fn):
        """One exchange from A's dialer with the HTTP leg replaced by fn(req) — return a response, or
        raise what urllib would. Returns (outcome, the request that went out)."""
        seen, saved = [], pm._peer_http

        def http(port, req, token=""):
            seen.append(req)
            return fn(req)
        pm._peer_http = http
        try:
            return pm._peer_exchange_once("srv", 1, ""), seen[0]
        finally:
            pm._peer_http = saved

    def _via_b(self, req):
        resp, status = pmb.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        return resp

    @staticmethod
    def _lost(req):
        import socket
        raise socket.timeout("timed out")             # raised by getresponse: AFTER the request went out

    # ── the rows and the uncarried case ─────────────────────────────────────────────────────────

    def test_an_uncarried_item_is_recalled_and_the_row_names_the_outbox(self):
        self._park()
        kept = []
        removed = pm._recall("sid-a", "", "c1", kept=kept)
        self.assertEqual([r["id"] for r in removed], ["c1"], "still parked here: the recall wins")
        self.assertEqual(kept, [])
        self.assertEqual(pm.outbox_list("srv"), [])
        self.assertEqual([(r["id"], r["box"], r["host"]) for r in self._recall_rows()], [("c1", "outbox", "srv")],
                         "the row says which box it left, by field")

    def test_a_maildir_recall_row_names_the_new_box(self):
        mid = pm.deliver(_RCP, "alpha", "sid-a", "a local ask", kind="question")
        removed = pm._recall("sid-a", "", mid)
        self.assertEqual([r["id"] for r in removed], [mid])
        self.assertEqual([(r["id"], r["box"]) for r in self._recall_rows()], [(mid, "new")])

    # ── the defect, end to end ──────────────────────────────────────────────────────────────────

    def test_a_message_the_far_side_already_holds_is_not_withdrawn(self):
        # the request carried it and B delivered on arrival; the end-to-end ack is not yet folded in on
        # A. Main unlinked the record and filed a recall row here — the recipient reading a message its
        # sender's receipts called withdrawn
        self._park()
        flight = []                                  # the dialer's shape: the build registers a flight it will end
        req = pm.build_exchange_request("srv", wait=False, flight=flight)
        resp, status = pmb.peer_exchange_handle(req)
        self.assertEqual(status, 200)
        self.assertEqual(len(pmb.read_box("sid-b", consume=False)), 1, "B holds it")
        self.assertEqual(pm._recall("sid-a", "", "c1"), [], "a message the far side already holds is not withdrawn")
        self.assertEqual(self._recall_rows(), [], "and no recall row says otherwise")
        pm.peer_exchange_apply("srv", req, resp, flight=flight)
        self.assertIsNone(pm.outbox_get("srv", "c1"), "the ack folds in: the record is gone")
        self.assertEqual([r["ev"] for r in self._rows() if r.get("id") == "c1"], ["relayed"], "the ledger says delivered")
        self.assertEqual(len(pmb.read_box("sid-b", consume=True)), 1)

    def test_the_dialed_sides_copy_of_a_message_the_far_side_already_holds_cannot_be_withdrawn(self):
        # The core defect through the real wire, in names main has: B's parked reply rides B's RESPONSE
        # (a real server over B's Handler, A dialing it with the plain request shape), A delivers it to
        # alpha, and the end-to-end ack is not back yet — B's copy is still in its outbox. On main B's
        # recall then unlinked that copy and filed a recall row: withdrawn on the ledger, read on A.
        pmb.outbox_put("hosta", {"mid": "r9", "to": "alpha", "frm": "beta", "frm_id": "sid-b",
                                 "body": "the schema is frozen, ship it", "kind": "coordinate", "t": 1})
        port = self._serve(pmb)
        req = pm.build_exchange_request("srv", wait=False)
        resp = pm._peer_http(port, req, token=pmb.SERVE_TOKEN)
        self.assertEqual([m["mid"] for m in resp["relays"]], ["r9"], "B's response carried it")
        pm.peer_exchange_apply("srv", req, resp)
        held = pm.read_box("sid-a", consume=False)
        self.assertEqual(len(held), 1, "alpha holds it on A")
        self.assertEqual(pmb._recall("sid-b", "", "r9"), [], "the far side already holds it: B's copy is not withdrawn")
        self.assertEqual(self._recall_rows(pmb), [], "and no recall row says otherwise")
        self.assertIsNotNone(pmb.outbox_get("hosta", "r9"), "B's copy stays for A's ack")
        self.assertIn("ship it", pm.read_box("sid-a", consume=True)[0]["body"], "alpha still has the message")

    # ── the dialer: the dial's outcome is the event ─────────────────────────────────────────────

    def test_building_the_request_marks_nothing_but_puts_the_record_in_flight(self):
        # the mark keys on the outcome, never on the build: a request built is not a request delivered.
        # Between the two the record is in flight, and a recall is told so instead of being granted —
        # main granted it, for bytes the dial was about to put on the wire
        self._park()
        flight = []
        req = pm.build_exchange_request("srv", wait=False, flight=flight)
        self.assertEqual([m["mid"] for m in req["relays"]], ["c1"])
        self.assertNotIn("carried", req["relays"][0], "the mark is this bus's bookkeeping, never on the wire")
        self.assertIsNone(json.loads((pm.OUTBOX / "srv" / "c1.json").read_text()).get("carried"),
                          "nothing durable is written at build")
        self.assertEqual(len(flight), 1, "the build registered one flight and handed its id back")
        self.assertEqual(self._open(pm, "srv"), {"c1"})
        kept = []
        self.assertEqual(pm._recall("sid-a", "", "c1", kept=kept), [], "in flight: not granted")
        self.assertEqual([(k["id"], k["why"], k["carried"]) for k in kept], [("c1", pm.WHY_IN_FLIGHT % "srv", None)])
        self.assertEqual(self._recall_rows(), [])
        pm._flight_done("srv", flight[0], carried=False)   # the test stands in for a dial that never connected
        self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1")], ["c1"], "freed: the recall wins")

    def test_a_protocol_drift_refusal_marks_nothing_and_the_recall_still_wins(self):
        # the 409 arm waits 60 s between dials; a mark at build would have refused the recall throughout,
        # for bytes the far bus refused before it read a relay
        import io
        import urllib.error
        self._park()

        def drift(req):
            raise urllib.error.HTTPError("http://127.0.0.1:1/peer-exchange", 409, "drift", {},
                                         io.BytesIO(b'{"error": "peer protocol drift"}'))
        outcome, req = self._dial(drift)
        self.assertEqual(outcome, "drift")
        self.assertEqual([m["mid"] for m in req["relays"]], ["c1"], "the request carried it…")
        self.assertIsNone(pm.outbox_get("srv", "c1").get("carried"), "…but nothing reached the far bus: no mark")
        kept = []
        self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1", kept=kept)], ["c1"], "recalled, as on main")
        self.assertEqual(kept, [])
        self.assertEqual(len(self._recall_rows()), 1)

    def test_a_refused_connection_marks_nothing(self):
        # urllib wraps every connect/send failure in URLError: the request never arrived
        import urllib.error
        self._park()

        def refused(req):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        outcome, _ = self._dial(refused)
        self.assertEqual(outcome, "unsent")
        self.assertIsNone(pm.outbox_get("srv", "c1").get("carried"))
        self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1")], ["c1"], "still here, still the sender's")

    def test_a_connection_closed_before_any_status_line_marks_nothing(self):
        # through the kernel's ssh -L forward a far bus that is not listening (its restart window, a
        # crash) looks like this: the local ssh listener accepts, the request goes out to it, ssh fails
        # the channel and closes the socket, and getresponse raises RAW (urllib wraps only the connect
        # and the send in URLError). The generic arm took it for a lost answer and marked the flight
        # carried, so through every far-bus restart the sender was refused a recall for a message still
        # in its own outbox (review find, 2026-09-09)
        import http.client
        self._park()

        def closed(req):
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        outcome, req = self._dial(closed)
        self.assertEqual(outcome, "unsent")
        self.assertEqual([m["mid"] for m in req["relays"]], ["c1"], "the request listed it…")
        self.assertIsNone(pm.outbox_get("srv", "c1").get("carried"), "…but no bus read it: no mark")
        self.assertEqual(self._open(pm, "srv"), set(), "the flight is closed, not left open")
        kept = []
        self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1", kept=kept)], ["c1"], "still here, still the sender's")
        self.assertEqual(kept, [])
        self.assertEqual(len(self._recall_rows()), 1)
        self.assertEqual(pm.outbox_list("srv"), [])

    def test_a_reset_connection_marks_nothing(self):
        # the same shape when the tunnel's close arrives as a reset rather than a clean EOF
        self._park()

        def reset(req):
            raise ConnectionResetError(104, "Connection reset by peer")
        outcome, _ = self._dial(reset)
        self.assertEqual(outcome, "unsent")
        self.assertIsNone(pm.outbox_get("srv", "c1").get("carried"))
        self.assertEqual(self._open(pm, "srv"), set())
        self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1")], ["c1"], "still here, still the sender's")

    def test_a_lost_response_marks_the_relays_carried(self):
        # a read timeout is raised after the request went out: the far bus may hold the message
        self._park()
        outcome, req = self._dial(self._lost)
        self.assertEqual(outcome, "lost")
        self.assertNotIn("carried", req["relays"][0])
        on_disk = json.loads((pm.OUTBOX / "srv" / "c1.json").read_text())   # from disk, not from memory
        self.assertTrue(on_disk.get("carried"), "the departure is recorded on the record itself")
        self.assertEqual(on_disk.get("carriedVia"), "srv")
        kept = []
        self.assertEqual(pm._recall("sid-a", "", "c1", kept=kept), [], "a carried item is not withdrawn")
        self.assertEqual([(k["id"], k["to"], k["host"]) for k in kept], [("c1", "srv:beta", "srv")])
        self.assertEqual(kept[0]["why"], "already left for srv and can no longer be withdrawn")
        self.assertEqual(kept[0]["carried"], on_disk["carried"])
        self.assertIn("notes-api", kept[0]["body"], "the sender can tell which message this was")
        self.assertEqual([m["mid"] for m in pm.outbox_list("srv")], ["c1"], "it stays parked for its ack")
        self.assertEqual(self._recall_rows(), [], "no recall row: nothing about the message changed")

    def test_a_recall_by_recipient_name_meets_the_same_refusal(self):
        # the `to` form the tool and `romp mail recall <to>` send; a caller passing no list still gets
        # nothing removed, only without the reason
        self._park()
        self._dial(self._lost)
        kept = []
        self.assertEqual(pm._recall("sid-a", "srv:beta", "", kept=kept), [])
        self.assertEqual([k["id"] for k in kept], ["c1"])
        self.assertEqual(pm._recall("sid-a", "srv:beta", ""), [])
        self.assertEqual(len(pm.outbox_list("srv")), 1)

    def test_a_response_whose_ack_covers_the_relay_clears_the_record_with_no_stray_temp(self):
        self._park()
        outcome, req = self._dial(self._via_b)
        self.assertEqual(outcome, "ok")
        self.assertIsNone(pm.outbox_get("srv", "c1"), "acked end to end: the record is gone")
        self.assertEqual([p.name for p in (pm.OUTBOX / "srv").iterdir()], [],
                         "no temp beside it: the mark is written after the acks, so a closed record is never rewritten")
        self.assertEqual([r["id"] for r in self._rows() if r.get("ev") == "relayed"], ["c1"])
        self.assertEqual(len(pmb.read_box("sid-b", consume=True)), 1)
        self.assertEqual(self._open(pm, "srv"), set(), "nothing left in flight")

    def test_a_response_that_does_not_ack_the_relay_marks_it_carried(self):
        # B's listing did not answer (its kernel mid-restart): _relay_in rules 'retry', silence on the
        # wire — yet the relay reached B, and B re-processes the re-relay in full next round
        self._park()
        pmb.local_agents_checked = lambda threads=False: ([], False)
        outcome, req = self._dial(self._via_b)
        self.assertEqual(outcome, "ok")
        rec = pm.outbox_get("srv", "c1")
        self.assertTrue(rec.get("carried"), "no ack, no bounce: the record stays, marked as left")
        self.assertEqual(rec.get("carriedVia"), "srv")
        kept = []
        self.assertEqual(pm._recall("sid-a", "", "c1", kept=kept), [])
        self.assertEqual(kept[0]["why"], pm.WHY_CARRIED % "srv")
        self.assertEqual(len(pm.outbox_list("srv")), 1)
        self.assertEqual(self._recall_rows(), [])

    def test_a_recall_during_the_dial_is_told_on_its_way_and_the_outcome_decides(self):
        # between the listing and the outcome the bytes are on the wire or about to be: a recall then is
        # neither granted (the recipient may already hold them) nor told they left (a refused dial means
        # they never will) — it is told to try again in a moment, and the outcome decides
        import urllib.error
        during = []

        def recall_now(mid):
            kept = []
            during.append((pm._recall("sid-a", "", mid, kept=kept), kept))

        def refused_after_a_recall(req):
            recall_now("c1")
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

        def lost_after_a_recall(req):
            recall_now("c2")
            self._lost(req)
        self._park("c1")
        self._dial(refused_after_a_recall)
        removed, kept = during[0]
        self.assertEqual(removed, [])
        self.assertEqual([(k["id"], k["why"], k["carried"]) for k in kept],
                         [("c1", pm.WHY_IN_FLIGHT % "srv", None)], "in flight: on its way, not left")
        self.assertEqual(self._recall_rows(), [], "no row was written for a refused recall")
        self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1")], ["c1"],
                         "the dial failed to connect: the recall wins after all")
        self._park("c2")
        self._dial(lost_after_a_recall)
        self.assertEqual(during[1][0], [])
        self.assertEqual(during[1][1][0]["why"], pm.WHY_IN_FLIGHT % "srv")
        kept = []
        self.assertEqual(pm._recall("sid-a", "", "c2", kept=kept), [])
        self.assertEqual(kept[0]["why"], pm.WHY_CARRIED % "srv", "the response was lost: it left for good")
        self.assertEqual(self._open(pm, "srv"), set(), "every outcome empties the flight")

    def test_a_recall_that_lands_between_the_listing_and_the_flight_keeps_the_message_off_the_wire(self):
        # the exchange carries only what it put in flight: a record the recall removed after the listing
        # read it never rides, so the recall it granted was honest
        self._park()
        saved = pm.outbox_list

        def listed_then_recalled(host):
            recs = saved(host)
            self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1")], ["c1"])
            return recs
        pm.outbox_list = listed_then_recalled
        try:
            req = pm.build_exchange_request("srv", wait=False)
        finally:
            pm.outbox_list = saved
        self.assertEqual(req["relays"], [], "nothing rides that the ledger has closed")
        self.assertEqual(len(self._recall_rows()), 1)
        self.assertEqual(self._open(pm, "srv"), set(), "an empty listing registers no flight")

    def test_a_re_relay_keeps_the_first_departure_and_the_ack_still_deletes(self):
        self._park()
        outcome, req1 = self._dial(self._lost)
        first = pm.outbox_get("srv", "c1")["carried"]
        writes, saved = [], pm._atomic_json_put
        pm._atomic_json_put = lambda p, o: writes.append(p) or saved(p, o)
        try:
            outcome, req2 = self._dial(self._lost)   # the link is bad; it rides again and is lost again
        finally:
            pm._atomic_json_put = saved
        self.assertEqual([m["mid"] for m in req2["relays"]], ["c1"], "it rides again")
        self.assertEqual(writes, [], "already marked: nothing is rewritten")
        self.assertEqual(pm.outbox_get("srv", "c1")["carried"], first, "the FIRST departure is the fact")
        self.assertEqual(json.dumps(req1["relays"]), json.dumps(req2["relays"]),
                         "the wire form is the same before and after the mark")
        outcome, req3 = self._dial(self._via_b)      # the link heals: B takes it and acks
        self.assertEqual(outcome, "ok")
        self.assertIsNone(pm.outbox_get("srv", "c1"), "the end-to-end ack still clears the record")
        self.assertEqual([r["id"] for r in self._rows() if r.get("ev") == "relayed"], ["c1"])
        self.assertEqual(len(pmb.read_box("sid-b", consume=True)), 1, "delivered exactly once")
        kept = []                                    # a recall that races the ack: the ack removed it first
        self.assertEqual(pm._recall("sid-a", "", "c1", kept=kept), [])
        self.assertEqual(kept, [])
        self.assertEqual(self._recall_rows(), [], "nothing to recall and no row: it was delivered, not withdrawn")

    def test_the_mark_survives_a_reload_of_the_bus(self):
        # the mark lives on the record: a bus restarted between the carry and the ack (a fresh module
        # instance over the same store) still refuses the recall
        self._park()
        self._dial(self._lost)
        env = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(pm.OUTBOX.parents[2])   # outbox → postal → romp → the XDG root
        try:
            fresh = load_source("romp_postal_peers_fresh", os.path.join(BIN, "romp-postal-service"))
        finally:
            os.environ["XDG_STATE_HOME"] = env
        self.assertEqual(fresh.OUTBOX, pm.OUTBOX, "the fresh bus reads the same store")
        kept = []
        self.assertEqual(fresh._recall("sid-a", "", "c1", kept=kept), [])
        self.assertEqual([(k["id"], k["host"]) for k in kept], [("c1", "srv")])
        self.assertEqual(len(pm.outbox_list("srv")), 1)

    def test_a_mark_that_cannot_be_written_is_said_and_leaves_the_record_recallable(self):
        # the record RODE; its departure could not be recorded, so a recall can still withdraw it — the
        # behaviour before the mark — and the log says so by id
        self._park()
        logged, saved = [], (pm._atomic_json_put, pm._log)

        def boom(p, o):
            raise OSError(28, "No space left on device")
        pm._atomic_json_put, pm._log = boom, lambda m: logged.append(m)
        try:
            outcome, _ = self._dial(self._lost)
        finally:
            pm._atomic_json_put, pm._log = saved
        self.assertEqual(outcome, "lost")
        self.assertIsNone(pm.outbox_get("srv", "c1").get("carried"))
        said = [m for m in logged if "c1" in m and "departure could not be recorded" in m]
        self.assertEqual(len(said), 1, logged)
        self.assertIn("a recall can still withdraw it", said[0])
        self.assertEqual(self._open(pm, "srv"), set(), "freed: no refusal outlives the outcome")
        self.assertEqual([r["id"] for r in pm._recall("sid-a", "", "c1")], ["c1"])

    # ── the dialed side: the response write is the event ───────────────────────────────────────

    def _serve(self, m):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
        srv.handle_error = lambda *a: None           # a handler that raises is the point of one test below
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def _outcome_event(self, m, want=None):
        """m._flight_done wrapped to set an Event (on any outcome, or only on one whose `carried` is `want`):
        the route records the outcome after the response is on the wire, so the client can hold the
        response before the handler thread has recorded it. The wrapper keeps _flight_done's parameter
        names: the route passes `carried=` by keyword."""
        done, saved = threading.Event(), m._flight_done

        def flight_done(host, flight_id, carried):
            saved(host, flight_id, carried)
            if want is None or carried == want:
                done.set()
        m._flight_done = flight_done
        self.addCleanup(setattr, m, "_flight_done", saved)
        return done

    def _inbound_from_srv(self):
        """A request as srv would dial A with: nothing to relay, so A's response carries A's outbox."""
        return {"host": "srv", "proto": pm.PEER_PROTO, "busId": "bus-srv", "epoch": 1, "presence": [], "holds": [],
                "relays": [], "acks": [], "bounces": [], "reads": [], "readAcks": [], "wait": False}

    def test_the_dialed_side_marks_its_relays_after_the_response_is_written_not_before(self):
        pmb.outbox_put("hosta", {"mid": "r1", "to": "alpha", "frm": "beta", "frm_id": "sid-b",
                                 "body": "the port is 8080", "kind": "coordinate", "t": 1})
        at_write, saved = [], pmb.Handler._send

        def send_then_note(handler, obj, code=200, close=False):
            if isinstance(obj, dict) and obj.get("relays"):
                at_write.append((pmb.outbox_get("hosta", "r1") or {}).get("carried"))
            return saved(handler, obj, code, close)
        pmb.Handler._send = send_then_note
        self.addCleanup(setattr, pmb.Handler, "_send", saved)
        done = self._outcome_event(pmb)
        port = self._serve(pmb)
        req = pm.build_exchange_request("srv", wait=False)     # A's outbox is empty: no flight on A's side
        resp = pm._peer_http(port, req, token=pmb.SERVE_TOKEN)
        self.assertEqual([m["mid"] for m in resp["relays"]], ["r1"], "the dialed side handed it out")
        self.assertNotIn("carried", resp["relays"][0])
        self.assertEqual(at_write, [None], "unmarked while the response was being written")
        self.assertTrue(done.wait(5), "the route recorded the outcome")
        rec = pmb.outbox_get("hosta", "r1")
        self.assertTrue(rec.get("carried"), "marked once the write returned")
        self.assertEqual(rec.get("carriedVia"), "hosta")
        kept = []
        self.assertEqual(pmb._recall("sid-b", "", "r1", kept=kept), [])
        self.assertEqual([(k["id"], k["host"], k["why"]) for k in kept], [("r1", "hosta", pmb.WHY_CARRIED % "hosta")])
        self.assertEqual(len(pmb.outbox_list("hosta")), 1)
        pm.peer_exchange_apply("srv", req, resp)
        self.assertEqual(len(pm.read_box("sid-a", consume=True)), 1, "A folded it in: delivered")
        self.assertEqual(self._open(pmb, "hosta"), set(), "the flight ended with the write")

    def test_a_response_that_cannot_be_written_leaves_the_relays_unmarked_to_ride_again(self):
        pmb.outbox_put("hosta", {"mid": "r2", "to": "alpha", "frm": "beta", "frm_id": "sid-b",
                                 "body": "the port is 8080", "kind": "coordinate", "t": 1})
        saved = pmb.Handler._send

        def dead_socket(handler, obj, code=200, close=False):
            if isinstance(obj, dict) and obj.get("relays"):
                raise BrokenPipeError(32, "Broken pipe")
            return saved(handler, obj, code, close)
        pmb.Handler._send = dead_socket
        self.addCleanup(setattr, pmb.Handler, "_send", saved)
        done = self._outcome_event(pmb)
        port = self._serve(pmb)
        req = pm.build_exchange_request("srv", wait=False)
        with self.assertRaises(Exception):           # the connection drops with no response
            pm._peer_http(port, req, token=pmb.SERVE_TOKEN)
        self.assertTrue(done.wait(5), "the route recorded the outcome")
        self.assertIsNone(pmb.outbox_get("hosta", "r2").get("carried"), "nothing left: no mark")
        self.assertEqual(self._open(pmb, "hosta"), set(), "freed to ride the next exchange")
        self.assertEqual([r["id"] for r in pmb._recall("sid-b", "", "r2")], ["r2"], "and still the sender's to recall")

    # ── two exchanges for one host at once ─────────────────────────────────────────────────────

    def test_a_flight_that_ends_unsent_releases_nothing_another_open_flight_holds(self):
        # srv dials A while A's own dialer is out: both list c1. The dialer's exchange never connects and
        # ends first; srv's response is still being written. With one set per host the first outcome
        # released c1 and a recall was granted for bytes on the wire (review find, 2026-09-08)
        import urllib.error
        self._park()
        gate, listed = threading.Event(), threading.Event()
        saved = pm.Handler._send

        def hold_then_send(handler, obj, code=200, close=False):
            if isinstance(obj, dict) and obj.get("relays"):
                listed.set()
                gate.wait(5)                         # the response to srv is mid-write while A's dialer runs
            return saved(handler, obj, code, close)
        pm.Handler._send = hold_then_send
        self.addCleanup(setattr, pm.Handler, "_send", saved)
        done = self._outcome_event(pm, want=True)
        port = self._serve(pm)
        got = []
        t = threading.Thread(target=lambda: got.append(pmb._peer_http(port, self._inbound_from_srv(), token=pm.SERVE_TOKEN)),
                             daemon=True)
        t.start()
        self.assertTrue(listed.wait(5), "srv's dial listed A's outbox into its flight")

        def refused(req):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        outcome, req = self._dial(refused)
        self.assertEqual(outcome, "unsent")
        self.assertEqual([m["mid"] for m in req["relays"]], ["c1"], "the second listing skips nothing another flight holds")
        kept = []
        self.assertEqual(pm._recall("sid-a", "", "c1", kept=kept), [], "srv's exchange still carries it: not granted")
        self.assertEqual([(k["id"], k["why"]) for k in kept], [("c1", pm.WHY_IN_FLIGHT % "srv")])
        gate.set()
        t.join(5)
        self.assertEqual([m["mid"] for m in got[0]["relays"]], ["c1"], "srv got it in the response")
        self.assertTrue(done.wait(5), "the route recorded srv's outcome")
        self.assertTrue(pm.outbox_get("srv", "c1").get("carried"), "the write returned: it left")
        kept = []
        self.assertEqual(pm._recall("sid-a", "", "c1", kept=kept), [])
        self.assertEqual(kept[0]["why"], pm.WHY_CARRIED % "srv")
        self.assertEqual(self._open(pm, "srv"), set())

    def test_a_flight_that_ends_carried_leaves_the_mark_when_the_other_ends_unsent(self):
        # the reverse order: srv's dial completes (carried) while A's dialer is out; A's dial then fails
        # to connect and ends unsent — releasing nothing, since the record is marked
        import urllib.error
        self._park()
        done = self._outcome_event(pm, want=True)
        port = self._serve(pm)

        def refused_after_srv_carried_it(req):
            resp = pmb._peer_http(port, self._inbound_from_srv(), token=pm.SERVE_TOKEN)
            self.assertEqual([m["mid"] for m in resp["relays"]], ["c1"])
            self.assertTrue(done.wait(5))
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        outcome, req = self._dial(refused_after_srv_carried_it)
        self.assertEqual(outcome, "unsent")
        self.assertEqual([m["mid"] for m in req["relays"]], ["c1"])
        self.assertTrue(pm.outbox_get("srv", "c1").get("carried"), "srv's write returned: it left")
        kept = []
        self.assertEqual(pm._recall("sid-a", "", "c1", kept=kept), [])
        self.assertEqual(kept[0]["why"], pm.WHY_CARRIED % "srv", "the unsent outcome released no mark")
        self.assertEqual(self._open(pm, "srv"), set())

    def test_ending_a_flight_twice_is_a_no_op_and_a_failed_fold_still_ends_it_carried(self):
        self._park()
        pmb.local_agents_checked = lambda threads=False: ([], False)   # B rules 'retry': no ack, the record stays
        flight = []
        req = pm.build_exchange_request("srv", wait=False, flight=flight)
        resp = self._via_b(req)
        pm.peer_exchange_apply("srv", req, resp, flight=flight)
        first = pm.outbox_get("srv", "c1")["carried"]
        self.assertTrue(first)
        self.assertEqual(self._open(pm, "srv"), set())
        pm._flight_done("srv", flight[0], carried=True)          # the fold-then-exception path's second call
        pm._flight_done("srv", flight[0], carried=False)
        pm._flight_done("srv", 10 ** 9, carried=False)           # an id nobody registered
        self.assertEqual(pm.outbox_get("srv", "c1")["carried"], first, "no-ops: the mark stands as written")
        self.assertEqual(self._open(pm, "srv"), set())
        # a fold that fails before it ends the flight: the dial answered, so the relays reached B
        self._park("c2")
        logged, saved = [], (pm._ack_arrived, pm._log)
        pmb.local_agents_checked = lambda threads=False: (pmb.local_agents(), True)   # B acks c2 this time

        def boom(host, mid):
            raise RuntimeError("the fold broke")
        pm._ack_arrived, pm._log = boom, lambda m: logged.append(m)
        try:
            outcome, req = self._dial(self._via_b)
        finally:
            pm._ack_arrived, pm._log = saved
        self.assertEqual(outcome, "ok")
        self.assertTrue(any("apply failed" in m and "the fold broke" in m for m in logged), logged)
        self.assertTrue(pm.outbox_get("srv", "c2").get("carried"), "ended carried by the once-function")
        self.assertEqual(self._open(pm, "srv"), set())

    # ── the surfaces ───────────────────────────────────────────────────────────────────────────

    def test_check_sent_shows_a_carried_record_as_left_awaiting_confirmation(self):
        # the receipt and the recall read the same record: before this the receipt said "queued for
        # relay" for the very id the recall refused as already left
        self._park()
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "c1", "from": "alpha", "from_id": "sid-a",
                                         "to_id": "peer:srv", "toName": "srv:beta", "body": "hi", "kind": "question"})
        row = pm._sent_receipts("sid-a")[-1]
        self.assertNotIn("carried", row)
        self.assertIn("queued for relay to srv", pm.format_receipts([row]), "parked, not yet left")
        self._dial(self._lost)
        row = pm._sent_receipts("sid-a")[-1]
        self.assertEqual(row["carried"], pm.outbox_get("srv", "c1")["carried"])
        line = pm.format_receipts([row])
        self.assertIn("left for srv %s — awaiting delivery confirmation · id c1" % pm._hhmm_epoch(row["carried"]), line)
        self.assertNotIn("queued for relay", line)
        kept = []
        pm._recall("sid-a", "", "c1", kept=kept)
        self.assertEqual(kept[0]["why"], pm.WHY_CARRIED % "srv", "the two surfaces agree on the same id")

    def test_the_route_answers_kept_beside_removed(self):
        self._park("c3")
        self._dial(self._lost)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:%d/recall" % srv.server_address[1],
                                         data=json.dumps({"from_id": "sid-a", "id": "c3"}).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "X-Romp-Token": pm.SERVE_TOKEN}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read())
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(res["removed"], [])
        self.assertEqual([(k["id"], k["host"]) for k in res["kept"]], [("c3", "srv")])
        self.assertEqual(res["kept"][0]["why"], pm.WHY_CARRIED % "srv")

    def test_the_tool_and_the_cli_say_it_in_plain_words(self):
        import contextlib
        import io
        kept = [{"to": "srv:beta", "id": "c1", "host": "srv", "carried": 5, "why": pm.WHY_CARRIED % "srv",
                 "body": "please review the notes-api branch"}]
        saved = (pm._http, pm._self_identity, pm._heartbeat, pm.ensure, pm.my_id)
        pm._http = lambda method, path, payload=None: {"ok": True, "removed": [], "kept": kept}
        pm._self_identity = lambda: ("sid-a", "alpha")
        pm._heartbeat = lambda *a, **k: None
        pm.ensure, pm.my_id = (lambda: True), (lambda: "sid-a")
        try:
            out, err = pm._mcp_call("recall_message", {"id": "c1"})
            self.assertFalse(err)
            self.assertIn('NOT recalled — to srv:beta (id c1): it already left for srv and can no longer be '
                          'withdrawn; they may already have read it. "please review the notes-api branch"', out,
                          "the gist is quoted: the sender's own imperative is not addressed to the reader")
            self.assertNotIn("Nothing to recall", out, "a refusal is not 'nothing matched'")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = pm.cli_recall(["srv:beta", "c1"])
            self.assertEqual(rc, 0)
            self.assertIn("[romp mail] not recalled: the message to 'srv:beta' (id c1) already left for srv and "
                          "can no longer be withdrawn; they may already have read it", buf.getvalue())
            self.assertNotIn("nothing to recall", buf.getvalue())
            pm._http = lambda method, path, payload=None: {"ok": True, "kept": kept,
                                                           "removed": [{"to": "srv:beta", "id": "c2", "body": "second"}]}
            out, err = pm._mcp_call("recall_message", {"to": "srv:beta"})
            self.assertIn("Recalled 1 message(s) before they were read:", out)
            self.assertIn("✕ to srv:beta: second", out)
            self.assertIn("NOT recalled — to srv:beta (id c1)", out, "one withdrawn, one refused: both are said")
            riding = [{"to": "srv:beta", "id": "c4", "host": "srv", "carried": None, "why": pm.WHY_IN_FLIGHT % "srv",
                       "body": "one more thing"}]
            pm._http = lambda method, path, payload=None: {"ok": True, "removed": [], "kept": riding}
            out, err = pm._mcp_call("recall_message", {"id": "c4"})
            self.assertIn('NOT recalled — to srv:beta (id c4): it is on its way to srv right now (try again in a '
                          'moment). "one more thing"', out, "in flight: try again — never 'too late' in the same breath")
            self.assertNotIn("may already have read it", out)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pm.cli_recall(["srv:beta", "c4"])
            self.assertIn("[romp mail] not recalled: the message to 'srv:beta' (id c4) is on its way to srv right now "
                          "(try again in a moment)\n", buf.getvalue())
            self.assertNotIn("may already have read it", buf.getvalue())
            pm._http = lambda method, path, payload=None: {"ok": True, "removed": []}   # an older bus: no `kept`
            out, err = pm._mcp_call("recall_message", {"to": "srv:beta"})
            self.assertIn("Nothing to recall", out)
            self.assertIn("already read or delivered", out, "delivered-and-unread on the far side is not 'read'")
        finally:
            (pm._http, pm._self_identity, pm._heartbeat, pm.ensure, pm.my_id) = saved
        desc = [t for t in pm.MCP_TOOLS if t["name"] == "recall_message"][0]["description"]
        self.assertIn("has not left for it yet", desc, "the tool says it withdraws mail still here")
        self.assertIn("check_sent shows which", desc)


class TheStartSweepLeavesAStandingOutboxRecordAlone(_LoudBus):
    """_mark_carried rewrites a STANDING record through _atomic_json_put, whose temp is
    <mid>.json.tmp-<pid>-<hex> beside it. A stop between the temp's open and its os.replace leaves that
    temp beside a valid <mid>.json. The start sweep's outbox arm used to remove every temp and close its
    id's ledger as never parked (WHY_STOPPED_BEFORE_PARK) — sound when outbox_put was the only temp
    writer, and a parked message read as refused to its sender once a rewrite could leave one
    (review find, 2026-09-08). The maildir arm's rule applies: a temp beside a record that stands is
    removed, said, and its ledger is left alone."""

    def test_a_temp_beside_a_standing_outbox_record_is_removed_and_its_ledger_left_alone(self):
        pm._tl_append("messages.jsonl", {"t": 10, "ev": "sent", "id": "px-mark", "from": "alpha", "from_id": _SND,
                                         "to_id": "peer:srv", "toName": "srv:beta", "body": "hi", "kind": "question"})
        pm.outbox_put("srv", {"mid": "px-mark", "to": "beta", "frm": "alpha", "frm_id": _SND,
                              "body": "hi", "kind": "question", "t": 1})
        (pm.OUTBOX / "srv" / "px-mark.json.tmp-1-abcd").write_text('{"mid": "px-mark", "carried": 5')
        pm._sweep_unfinished_writes()
        self.assertEqual(sorted(p.name for p in (pm.OUTBOX / "srv").iterdir()), ["px-mark.json"],
                         "the temp is gone, the record stands")
        self.assertEqual([r["ev"] for r in self._rows() if r.get("id") == "px-mark"], ["sent"],
                         "no bounced row: the message is parked and its ledger stays open")
        said = [m for m in self.logged if "px-mark.json.tmp-1-abcd" in m]
        self.assertEqual(len(said), 1, self.logged)
        self.assertIn("the record itself stands", said[0])
        self.assertIn("its ledger left alone", said[0])
        self.assertEqual(len(self._notices()), 1)
        self.assertIn("1 unfinished mail write(s)", self._notices()[0])
        self.assertIn("0 sender receipt(s) now read refused", self._notices()[0])
        self.assertIn("1 of them the temp of a record that stands", self._notices()[0],
                      "both stores run the standing check (readbox_put rewrites too), so the row names neither")
        rec = pm._sent_receipts(_SND)[0]
        self.assertEqual((rec["parked"], rec["bounced"]), ("srv", None))
        line = pm.format_receipts([rec])
        self.assertIn("id px-mark", line)
        self.assertNotIn("refused", line, "the receipt still reads parked, never refused")
