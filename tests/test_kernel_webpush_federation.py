#!/usr/bin/env python3
"""Federated push (plans/federated-push.md): one device subscription buzzes for every connected
kernel. Push scope = dashboard scope.

What these pin, by layer:

  * the forward — _push_forward ships this kernel's fired bell events to attached TRUSTED peers
    only (never directed/isolated, never a row without the pair channel), over _peer_call.
  * the relay route — POST /push/relay is token-gated, judges the ORIGIN's trust tier at delivery
    time (unknown fails safe, with the reason on stderr — fail loudly), mirrors capped, wears the
    origin on the SID only (the title passes as the origin composed it), and NEVER forwards
    onward (a relayed event is terminal, so attachment cycles cannot echo).
  * the payload — a mirrored event omits `badge` (the origin kernel's count is not this kernel's),
    _push_notify omits the key for badge=None, and the worker applies setAppBadge to numeric
    values only, so a mirrored event can never clear a real local count.
  * the reveal — a host-prefixed sid is handed to the merged dashboard's own routing, never a
    confirmRevive minted from the WRONG kernel's session list.

Synthetic only — invented host names (boxa/boxb/TESTHOST), placeholder ids, no real session data.
"""
import contextlib
import io
import json
import os
import threading
import unittest
from unittest import mock
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
# Belt over conftest's suspenders (the webpush test's 2026-08-08 lesson): a RAW run of this file
# must never see live state, so the state root is rebound BEFORE the kernel module loads and
# captures jd.STATE into its path constants.
from pathlib import Path
_STATE_TD = tempfile.TemporaryDirectory()
jd.STATE = Path(_STATE_TD.name)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_webpush_fed", os.path.join(BIN, "romp-kernel"))


def _serve_post(path, body=None, headers=None):
    """Drive the REAL do_POST dispatcher over a fake socket (the auth-hardening harness):
    (status, body_bytes)."""
    raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    hdrs = dict(headers or {})
    hdrs.setdefault("Content-Length", str(len(raw)))
    h.headers = hdrs
    h.path = path
    h.command = "POST"
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO(raw)
    h.close_connection = True
    captured = {}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_POST()
    return captured.get("status"), h.wfile.getvalue()


def _seed_remote(host, trust, local_port=1, token="tok"):
    with km._remotes_lock:
        km._remotes[host] = {"host": host, "kernel_port": 1, "local_port": local_port,
                             "token": token, "proc": None, "status": "up", "trust": trust}


def _clear_remotes():
    with km._remotes_lock:
        km._remotes.clear()
    with km._known_lock:
        km._known.clear()


class PushForward(unittest.TestCase):
    def setUp(self):
        _clear_remotes()

    def tearDown(self):
        _clear_remotes()

    def _run_forward(self, events):
        calls, done = [], threading.Event()

        def fake_peer_call(r, method, path, body=None, timeout=8):
            calls.append((r.get("host"), method, path, body))
            done.set()
            return 200, {"ok": True}

        with mock.patch.object(km, "_peer_call", side_effect=fake_peer_call):
            km._push_forward(events)
            # fire-and-forget thread; the event marks first delivery, the join grace the rest
            if km._remotes:
                done.wait(5)
        return calls

    def test_only_trusted_peers_with_a_pair_channel_hear_events(self):
        _seed_remote("boxa", "trusted")                       # hears it
        _seed_remote("boxb", "directed")                      # policy: never
        _seed_remote("boxc", "isolated")                      # policy: never
        _seed_remote("boxd", "trusted", token="")             # no channel to speak through
        _seed_remote("boxe", "trusted", local_port=0)         # no channel to speak through
        calls = self._run_forward([{"title": "Romp needs you: web", "body": "Needs you: x", "sid": "S1"}])
        self.assertEqual([c[0] for c in calls], ["boxa"])
        host, method, path, body = calls[0]
        self.assertEqual((method, path), ("POST", "/push/relay"))
        self.assertEqual(body["origin"], km._self_host())
        self.assertEqual(body["events"], [{"title": "Romp needs you: web", "body": "Needs you: x", "sid": "S1"}])

    def test_no_events_or_no_peers_is_silent(self):
        self.assertEqual(self._run_forward([]), [])           # nothing fired
        _seed_remote("boxb", "directed")
        calls = self._run_forward([{"title": "t", "body": "b", "sid": ""}])
        self.assertEqual(calls, [])                           # nobody trusted


class RelayRoute(unittest.TestCase):
    def setUp(self):
        _clear_remotes()
        km._set_notify_all(True)                # THIS kernel is authoritative for its devices (#937 fold):
        km._set_notify_turns(True)              # a relayed event reaches them only while its switches are on

    def tearDown(self):
        _clear_remotes()
        km._set_notify_all(False)
        km._set_notify_turns(False)

    def _relay(self, body, token=True):
        headers = {"X-Romp-Token": km.TOKEN} if token else {}
        with mock.patch.object(km, "_push_notify") as pn, \
             mock.patch.object(km, "_push_forward") as pf, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            status, raw = _serve_post("/push/relay", body, headers=headers)
        try:
            parsed = json.loads(raw.decode() or "{}")
        except ValueError:
            parsed = {}
        return status, parsed, pn, pf, err.getvalue()

    def test_gated_like_every_route(self):
        status, _, pn, _, _ = self._relay({"origin": "boxa", "events": []}, token=False)
        self.assertEqual(status, 403)
        pn.assert_not_called()

    def test_malformed_bodies_are_400(self):
        st, _ = _serve_post("/push/relay", b"not json", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(st, 400)
        for bad in ({"events": [{}]},                          # no origin
                    {"origin": "boxa"},                        # no events
                    {"origin": "boxa", "events": "x"}):        # events not a list
            status, _, pn, _, _ = self._relay(bad)
            self.assertEqual(status, 400)
            pn.assert_not_called()

    def test_the_receivers_switches_gate_a_relayed_event(self):
        # the subscriber's own kernel decides what reaches its devices, not the origin's switches
        # (#937 fold): master off drops everything, turn switch off drops the turn-kind events
        _seed_remote("boxa", "trusted")
        card = {"title": "Romp needs you: web", "body": "Needs you", "sid": "s", "kind": "card"}
        turn = {"title": "Romp: web", "body": "Done", "sid": "s", "kind": "turn"}
        km._set_notify_all(False)
        status, parsed, pn, _, err = self._relay({"origin": "boxa", "events": [card, turn]})
        self.assertEqual((status, parsed["mirrored"]), (200, 0), "master off holds both")
        pn.assert_not_called()
        self.assertIn("held 2 event(s) relayed from 'boxa'", err)
        km._set_notify_all(True)
        km._set_notify_turns(False)
        status, parsed, pn, _, err = self._relay({"origin": "boxa", "events": [card, turn]})
        self.assertEqual((status, parsed["mirrored"]), (200, 1), "turn switch off holds only the turn event")
        pn.assert_called_once_with("Romp needs you: web", "Needs you", "boxa:s", kind="card", card_id="", host="boxa")

    def test_a_trusted_origin_mirrors_with_the_origin_on_the_sid_only(self):
        _seed_remote("boxa", "trusted")
        ev = {"title": "Romp needs you: web", "body": "Needs you: fix the login flow", "sid": "11111111-2222"}
        status, parsed, pn, _, err = self._relay({"origin": "boxa", "events": [ev]})
        self.assertEqual(status, 200)
        self.assertEqual(parsed, {"ok": True, "mirrored": 1})
        # the origin rides the SID (so a tap routes through the merged dashboard's tabs) and the
        # `host` kwarg — never the words: the title reaches the phone exactly as the origin composed
        # it (the user 2026-09-09: the session name alone, no "romp: <host>:" decoration — which
        # kernel detected an event is romp's business). badge is OMITTED (positional call, default
        # None): the origin's count is not ours
        pn.assert_called_once_with("Romp needs you: web", "Needs you: fix the login flow",
                                   "boxa:11111111-2222", kind="card", card_id="", host="boxa")
        title, body = pn.call_args[0][0], pn.call_args[0][1]
        self.assertNotIn("boxa", title + body, "the host is not in the words the user reads")
        self.assertEqual(err, "")

    def test_sid_surgery_is_tolerant_and_no_title_is_rewritten(self):
        _seed_remote("boxa", "trusted")
        evs = [{"title": "Custom shape", "body": "b", "sid": "already:prefixed"},
               {"title": "romp: web", "body": "Needs you: x", "sid": "S1"},   # an older build's title: as-is, never grafted
               {"title": "", "body": "", "sid": "S"}]          # empty event: skipped, not an error
        status, parsed, pn, _, _ = self._relay({"origin": "boxa", "events": evs})
        self.assertEqual(status, 200)
        self.assertEqual(parsed["mirrored"], 2)
        self.assertEqual([c[0][:3] for c in pn.call_args_list],
                         [("Custom shape", "b", "already:prefixed"), ("romp: web", "Needs you: x", "boxa:S1")])

    def test_kind_and_card_pass_through_with_the_origin_as_host(self):
        # the card id is a goal id — globally unique, never host-prefixed (federation.ts) — so the
        # merged feed finds it as-is; the sid wears the prefix and host names the origin outright
        _seed_remote("boxa", "trusted")
        ev = {"title": "Romp needs you: web", "body": "Needs you: x", "sid": "11111111-2222",
              "kind": "card", "cardId": "11111111-2222:g1"}
        status, parsed, pn, _, _ = self._relay({"origin": "boxa", "events": [ev]})
        self.assertEqual(parsed["mirrored"], 1)
        pn.assert_called_once_with("Romp needs you: web", "Needs you: x", "boxa:11111111-2222",
                                   kind="card", card_id="11111111-2222:g1", host="boxa")
        # …and the payload that reaches the phone carries both, the deep link included
        d = km._push_payload("Romp needs you: web", "Needs you: x", "boxa:11111111-2222",
                             kind="card", card_id="11111111-2222:g1", host="boxa")["data"]
        self.assertEqual((d["sid"], d["host"], d["cardId"]), ("boxa:11111111-2222", "boxa", "11111111-2222:g1"))
        self.assertEqual(d["url"], "/?push-reveal=boxa%3A11111111-2222&push-card=11111111-2222%3Ag1")

    def test_a_remembered_trusted_host_counts(self):
        # trust is judged by origin, attached or not — the remembered table is the origin store
        km._known_note("boxb", "trusted")
        status, parsed, pn, _, _ = self._relay(
            {"origin": "boxb", "events": [{"title": "Romp: api", "body": "Completed: done", "sid": "S2"}]})
        self.assertEqual(parsed, {"ok": True, "mirrored": 1})
        pn.assert_called_once_with("Romp: api", "Completed: done", "boxb:S2", kind="card", card_id="", host="boxb")

    def test_below_trusted_drops_loudly_and_never_buzzes(self):
        _seed_remote("boxa", "directed")
        for origin, want_tier in (("boxa", "directed"), ("TESTHOST", "unknown")):
            status, parsed, pn, _, err = self._relay(
                {"origin": origin, "events": [{"title": "t", "body": "b", "sid": "S"}]})
            self.assertEqual(status, 200)
            self.assertEqual(parsed, {"ok": False, "mirrored": 0, "tier": want_tier})
            pn.assert_not_called()
            self.assertIn(origin, err)                        # the drop names its origin…
            self.assertIn(want_tier, err)                     # …its tier…
            self.assertIn("network panel", err)               # …and the remedy

    def test_a_relayed_event_is_terminal(self):
        # never forwarded onward — this is the whole cycle-safety argument, so it is pinned
        _seed_remote("boxa", "trusted")
        _seed_remote("boxb", "trusted")                       # a second trusted peer is listening…
        status, _, _, pf, _ = self._relay(
            {"origin": "boxa", "events": [{"title": "t", "body": "b", "sid": "S"}]})
        self.assertEqual(status, 200)
        pf.assert_not_called()                                # …and still hears nothing from a relay

    def test_the_fan_in_is_capped(self):
        _seed_remote("boxa", "trusted")
        evs = [{"title": "t%d" % i, "body": "b", "sid": "S%d" % i} for i in range(20)]
        status, parsed, pn, _, _ = self._relay({"origin": "boxa", "events": evs})
        self.assertEqual(parsed["mirrored"], 16)
        self.assertEqual(pn.call_count, 16)


class MirroredPayloadShape(unittest.TestCase):
    def test_badge_none_omits_the_key_and_numeric_keeps_it(self):
        sent, done = [], threading.Event()

        def fake_send(sub, payload):
            sent.append(json.loads(payload.decode()))
            done.set()
            return True

        km._PUSH_CRYPTO[0] = {"stub": True}                   # sink runs; no real crypto touched
        try:
            with mock.patch.object(km, "_push_subs", return_value={"ep": {"k": 1}}), \
                 mock.patch.object(km, "_push_send_one", side_effect=fake_send):
                km._push_notify("t", "b", "S")                # the mirrored-event shape
                self.assertTrue(done.wait(5))
                done.clear()
                km._push_notify("t", "b", "S", badge=3)       # the local shape
                self.assertTrue(done.wait(5))
        finally:
            km._PUSH_CRYPTO[0] = None
        self.assertNotIn("badge", sent[0])
        self.assertEqual(sent[1]["badge"], 3)

    def test_the_worker_paints_numeric_badges_only(self):
        # a mirrored event (no badge) must never CLEAR a real local count via `d.badge||0`
        self.assertIn("typeof d.badge==='number'", km._SW_JS)
        self.assertNotIn("d.badge||0", km._SW_JS)


class FederatedReveal(unittest.TestCase):
    def test_a_prefixed_sid_is_handed_to_the_merged_dashboard(self):
        # liveness is the ORIGIN kernel's truth: the local session list must not turn a remote
        # session's tap into a confirmRevive minted from the wrong world
        with mock.patch.object(km, "_live_map", return_value={}), \
             mock.patch.object(km, "_name_of", return_value="web"):
            self.assertEqual(km._reveal_msg("boxa:11111111-2222"),
                             {"type": "focus", "id": "boxa:11111111-2222", "live": True})
            # …while a bare unknown sid keeps the revive prompt it always had
            self.assertEqual(km._reveal_msg("11111111-2222")["type"], "confirmRevive")


class ForwardWiring(unittest.TestCase):
    def test_the_forward_rides_the_same_fired_events(self):
        # the one choke point: the forward consumes the SAME _feed_notifications result the bells
        # and local pushes consumed — never a second diff that could disagree. Since 2026-09-05
        # (the one-buzz-per-turn-end rule, tests/test_kernel_notify_popover.py) the forward carries
        # the events that BUZZED here — `_buzzed`, the fired list minus the ones that yielded to a
        # turn-finished push — so a peer's phone hears each turn end once, exactly like ours.
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("_fired = _feed_notifications(feed)", src)
        self.assertIn('_buzzed.append({"title": _t, "body": _b, "sid": _sid, "kind": "card", "cardId": _iid})', src)
        self.assertIn("_push_forward(_buzzed)", src)
        self.assertNotIn("_push_forward([{", src, "no second list is built from a second diff")


if __name__ == "__main__":
    unittest.main()
