#!/usr/bin/env python3
"""The bell popover (2026-09-05): the bell's tap opens a small anchored card whose rows are the
switches ONE tap used to flip together, plus the turn-finished switch and a test button.

What these pin, by layer:

  * the store — "*turns" is a second reserved key in notify-cards.json: GET/POST /notify-turns
    read and flip it, its own shell push repaints every dashboard, and the prune that drops ids
    whose card left the feed keeps BOTH reserved keys.
  * the test button — POST /push/test sends ONE notification to the asking device's subscription
    and returns the push service's answer {ok, status, detail}; an unknown endpoint says so; a
    dead one (404/410) is pruned exactly like the fan-out prunes it; missing crypto is the same
    loud 500 the subscribe route gives. _push_post is the one HTTP path under both; _push_send_one
    keeps its prune semantics (False on 404/410 ONLY) on top of it. Since 2026-09-06 the test is
    ADDRESSED to the session the shell had in front (sid + host, the chat pane's active tab): the
    payload carries it under kind test so the tap comes back there like a turn's, the body names
    it, and the answer echoes sid + name for the popover's result line; no sid is the old probe.
  * the turn-finished push — _turn_notify_tick fires on a session's turn-end KEY moving (the Stop
    hook's lastStopAt, or a STOPPED states/ transition), only with the master AND the switch on
    and the session unmuted, with a silent first-sight baseline; the event travels to trusted
    peers the bell-event way. Since 2026-09-10 it fires for the HUMAN's turns only: the Stop hook
    stamps who opened the turn beside the settle (lastTurnOpener, sdk_backend) and an end the CLI
    or romp opened by itself (a subagent's task notification, a scheduled prompt, a peer's message,
    a nudge) is skipped without spending the buzz claim; a registry without the field reads human.
  * the one-buzz-per-turn-end rule — _buzz_claim: the bell event and the turn push both claim
    (sid, turn-end key); the first to file buzzes and the other yields; bell events never
    suppress each other; a sid-less event always passes.
  * the shell — the popover markup (four rows), the menu TOKENS with their dark fallbacks, the
    theme blocks that define them, the Escape chain, the WS repaint, and the bell glyph reading
    THIS device (master AND subscribed).

Synthetic only: placeholder sids, the notes-api demo sessions (web/api), invented reply text.
"""
import asyncio
import io
import json
import os
import threading
import time
import unittest
from unittest import mock
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads (the webpush test's 2026-08-08 lesson: a raw run must never
# touch the live push store or rotate the real VAPID key).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
from pathlib import Path
_STATE_TD = tempfile.TemporaryDirectory()
jd.STATE = Path(_STATE_TD.name)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_notify_popover", os.path.join(BIN, "romp-kernel"))
# the SDK backend, for the Stop hook's half of the turn-opener stamp (TurnOpenerStamp)
sb = load_source("romp_sdk_backend_turn_opener", os.path.join(BIN, "romp_sdk_backend.py"))

SID_WEB = "11111111-2222-4333-8444-555555555501"
SID_API = "11111111-2222-4333-8444-555555555502"


def _serve_get(path, headers=None):
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    h.headers = dict(headers or {})
    h.path = path
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.wfile = io.BytesIO()
    h.rfile = io.BytesIO()
    h.close_connection = True
    captured = {}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.log_message = lambda *a: None
    h.do_GET()
    return captured.get("status"), h.wfile.getvalue()


def _reset_store():
    for name in ("notify-cards.json", "push-subscriptions.json", "session-flags.json"):
        try:
            (jd.STATE / name).unlink()
        except OSError:
            pass
    km._notify_cards_cache.clear()
    km._TURN_PREV.clear()
    km._PUSH_BUZZED.clear()


class _LoopbackMixin:
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body, token=True, raw=None):
        import urllib.request, urllib.error
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = km.TOKEN
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()


class TurnSwitchStore(_LoopbackMixin, unittest.TestCase):
    """GET/POST /notify-turns — the turn-finished switch's kernel half, a second reserved key
    beside the master in notify-cards.json."""

    def setUp(self):
        _reset_store()

    def test_default_off_and_gated(self):
        status, _ = _serve_get("/notify-turns")
        self.assertEqual(status, 403, "behind the serve token like every page")
        status, body = _serve_get("/notify-turns", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual((status, json.loads(body)), (200, {"on": False}))
        self.assertFalse(km._notify_turns_on())

    def test_post_flips_persists_and_broadcasts_its_own_message(self):
        sent = []
        with mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            code, body = self._post("/notify-turns", {"on": True})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True, "on": True}))
        self.assertTrue(km._notify_turns_on())
        self.assertEqual(json.loads((jd.STATE / "notify-cards.json").read_text()), {"*turns": True},
                         "persisted beside the master, as its own key")
        self.assertIn(("shell", {"type": "notifyTurns", "on": True}), sent,
                      "every open dashboard's row repaints — a distinct message, so the master's stays as it was")
        # the master is untouched by the turn switch, in both directions
        self.assertFalse(km._notify_all_on())
        code, _ = self._post("/notify-turns", {"on": False})
        self.assertEqual(code, 200)
        self.assertFalse(km._notify_turns_on())
        self.assertEqual(km._notify_cards(), {}, "off deletes the key rather than pinning False")

    def test_post_requires_token_and_refuses_garbage(self):
        code, _ = self._post("/notify-turns", {"on": True}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/notify-turns", None, raw=b"not json")
        self.assertEqual(code, 400)
        self.assertFalse(km._notify_turns_on())

    def test_prune_keeps_both_reserved_keys(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        km._set_notify_card("%s:g1" % SID_WEB, False, SID_WEB)     # a mute, kept while its card lives
        km._prune_notify_cards({"%s:g1" % SID_WEB})
        self.assertEqual(km._notify_cards(), {"*": True, "*turns": True, "%s:g1" % SID_WEB: False})
        km._prune_notify_cards(set())                              # the card left the feed
        self.assertEqual(km._notify_cards(), {"*": True, "*turns": True},
                         "neither reserved key is a card; only card ids prune")

    def test_the_turn_key_never_reads_as_a_card(self):
        km._set_notify_turns(True)
        cards = km._notify_cards()
        # the master is off, so no card is effectively armed — the turn key must not leak into that
        self.assertFalse(km._notify_card_effective(cards, "%s:g1" % SID_WEB, SID_WEB))


class PushPost(unittest.TestCase):
    """_push_post against a real loopback push service double — status and detail as the caller
    would see them; _push_send_one's prune rule on top."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        cls.script = {"status": 201, "body": b""}

        class Svc(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self.send_response(cls.script["status"])
                self.send_header("Content-Length", str(len(cls.script["body"])))
                self.end_headers()
                self.wfile.write(cls.script["body"])

            def log_message(self, *a):
                pass

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Svc)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _sub(self):
        return {"endpoint": "http://127.0.0.1:%d/send/dev-1" % self.port,
                "keys": {"p256dh": "k", "auth": "a"}}

    def _post(self, status, body=b""):
        type(self).script = {"status": status, "body": body}
        with mock.patch.object(km, "_webpush_encrypt", return_value=b"ciphertext"), \
             mock.patch.object(km, "_vapid_auth", return_value="vapid t=x, k=y"):
            return km._push_post(self._sub(), b"{}")

    def test_accepted_reports_the_2xx(self):
        status, detail = self._post(201)
        self.assertEqual(status, 201)
        self.assertTrue(detail)

    def test_a_refusal_carries_status_and_the_services_reason(self):
        status, detail = self._post(403, b'{"reason":"BadJwtToken"}')
        self.assertEqual(status, 403)
        self.assertIn("BadJwtToken", detail, "the service's own reason reaches the user")

    def test_no_answer_is_status_zero_with_the_error(self):
        with mock.patch.object(km, "_webpush_encrypt", return_value=b"x"), \
             mock.patch.object(km, "_vapid_auth", return_value="vapid t=x, k=y"):
            status, detail = km._push_post({"endpoint": "http://127.0.0.1:1/x",
                                            "keys": {"p256dh": "k", "auth": "a"}}, b"{}")
        self.assertEqual(status, 0)
        self.assertTrue(detail)

    def test_send_one_prunes_on_404_and_410_only(self):
        # the fan-out's contract, unchanged by the refactor: False = dead subscription, and NOTHING
        # else — a 5xx, a 403, a timeout all keep the subscription
        outcomes = {404: False, 410: False, 201: True, 403: True, 500: True, 0: True}
        for status, keep in outcomes.items():
            with mock.patch.object(km, "_push_post", return_value=(status, "x")):
                self.assertEqual(km._push_send_one(self._sub(), b"{}"), keep, status)


class PushTestRoute(_LoopbackMixin, unittest.TestCase):
    """POST /push/test over the real handler: one notification to the asking device, the push
    service's answer back."""

    def setUp(self):
        _reset_store()
        self.ep = "https://push.example.net/send/dev-test"
        km._save_push_subs({self.ep: {"endpoint": self.ep, "keys": {"p256dh": "k", "auth": "a"}}})

    def _test(self, outcome):
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=outcome) as pp:
            code, body = self._post("/push/test", {"endpoint": self.ep})
        return code, json.loads(body), pp

    def test_accepted(self):
        code, res, pp = self._test((201, "Created"))
        self.assertEqual(code, 200)
        self.assertEqual(res, {"ok": True, "status": 201, "detail": "Created"})
        # ONE send, to THIS subscription, a plain title and one sentence
        (sub, payload), _ = pp.call_args
        self.assertEqual(sub["endpoint"], self.ep)
        d = json.loads(payload.decode())
        # ONE title rule (the user 2026-09-09): "Romp: <session>" with a session in front, a bare
        # "Romp" without one; the body stays what it was. Was the lowercase "romp" for both.
        self.assertEqual(d["title"], "Romp")
        self.assertEqual(d["body"], "Test notification — this device is set up.")
        # preview reconciliation with #940: the tap payload rides `data` (kind test, no sid) and a tag
        self.assertLessEqual(set(d), {"title", "body", "tag", "data", "sid"}, "a test carries no sid to jump to and no badge")
        self.assertFalse(d.get("sid"))
        self.assertEqual(d["data"]["kind"], "test")
        self.assertFalse(d["data"].get("sid"))
        self.assertNotIn("badge", d)

    def test_addressed_to_the_session_you_were_looking_at(self):
        # the user 2026-09-06: press the button on one session, switch away, tap, come back to it.
        # The shell sends the chat pane's active tab; the payload carries it under kind test, the
        # body names it, and the answer echoes sid + name for the popover's result line
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp, \
             mock.patch.object(km, "_name_of", side_effect=lambda s: "web" if s == SID_WEB else None):
            code, body = self._post("/push/test", {"endpoint": self.ep, "sid": SID_WEB, "host": ""})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"ok": True, "status": 201, "detail": "Created", "sid": SID_WEB, "name": "web"})
        (sub, payload), _ = pp.call_args
        self.assertEqual(sub["endpoint"], self.ep)
        d = json.loads(payload.decode())
        self.assertEqual(d["title"], "Romp: web", "the session the tap comes back to, in the title too")
        self.assertEqual(d["body"], "Test notification — tap to come back to web.")
        self.assertEqual(d["sid"], SID_WEB)
        self.assertEqual(d["tag"], "romp:" + SID_WEB)
        # a turn's routing shape under kind test: the shell POSTs /reveal for the sid, no card to scroll to
        pid = d["data"].pop("pid")   # the kernel's handle on this push to this device (2026-09-09, the ledger — test_kernel_webpush's PushLedger): minted per send, so checked by shape and against the row it filed
        self.assertRegex(pid, r"^[A-Za-z0-9_-]{22}$")
        self.assertEqual([r["sid"] for r in km._push_ledger() if r["pid"] == pid], [SID_WEB], "the row the pid names is this push's")
        self.assertEqual(d["data"], {"sid": SID_WEB, "host": "", "kind": "test", "cardId": "",
                                     "url": "/?push-reveal=%s&push-pid=%s" % (SID_WEB, pid),   # the deep link carries the pid (2026-09-10): on Apple the link IS the tap, and the page settles the row it lands
                                     "name": "web"})   # the same name the answer carries (2026-09-09: the ledger row files it, so the kernel's lines can name the session)
        self.assertNotIn("badge", d, "the count rides its own push")

    def test_every_test_push_leaves_a_line_in_the_kernel_log(self):
        # 2026-09-08: a phone's test tap brought romp forward and nothing more, and the journal could not say
        # whether the test had carried a session — this route logged nothing. One stderr line per test push: the
        # session clipped (none when the shell attached none; a federated one keeps its host prefix), the
        # endpoint's host only, and the push service's answer; an unsubscribed endpoint logs too
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), \
             mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=(201, "Created")), \
             mock.patch.object(km, "_name_of", return_value=None):
            self._post("/push/test", {"endpoint": self.ep})
            self._post("/push/test", {"endpoint": self.ep, "sid": SID_WEB, "host": ""})
            self._post("/push/test", {"endpoint": self.ep, "sid": "boxa:" + SID_API, "host": "boxa"})
            self._post("/push/test", {"endpoint": "https://push.example.net/send/nobody"})
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("[push] test")]
        self.assertEqual(lines, ["[push] test sid=none endpoint=push.example.net: 201",
                                 "[push] test sid=%s endpoint=push.example.net: 201" % SID_WEB[:8],
                                 "[push] test sid=boxa:%s endpoint=push.example.net: 201" % SID_API[:8],
                                 "[push] test sid=none endpoint=push.example.net: not subscribed"])
        self.assertNotIn(SID_WEB, buf.getvalue(), "ids clipped: enough to match rows, never the whole id")

    def test_a_federated_session_keeps_its_prefix_and_falls_back_to_the_short_id(self):
        # a remote session's name lives at its origin kernel; with no snapshot of that kernel's
        # list and no label from the shell, ours knows only the id and says so rather than
        # inventing one — the prefixed id and the host ride the routing block as-is
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp, \
             mock.patch.object(km, "_name_of", return_value=None):
            code, body = self._post("/push/test", {"endpoint": self.ep, "sid": "boxa:" + SID_API, "host": "boxa"})
        self.assertEqual(code, 200)
        res = json.loads(body)
        self.assertEqual((res["sid"], res["name"]), ("boxa:" + SID_API, SID_API[:8]))
        d = json.loads(pp.call_args[0][1].decode())
        self.assertEqual(d["body"], "Test notification — tap to come back to %s." % SID_API[:8])
        self.assertEqual(d["title"], "Romp: " + SID_API[:8], "the title wears the same stand-in the body does")
        self.assertEqual((d["data"]["sid"], d["data"]["host"], d["data"]["kind"]), ("boxa:" + SID_API, "boxa", "test"))

    def _snapshot(self, host, names):
        """Seed the supervisor's per-host snapshot the way its poll files it (sids + names)."""
        with km._remotes_lock:
            km._remotes[host] = {"host": host, "kernel_port": 1, "local_port": 1, "status": "up",
                                 "sids": list(names), "names": dict(names)}
        self.addCleanup(lambda: km._remotes.pop(host, None))

    def test_a_remote_sessions_name_comes_from_the_kernels_own_snapshot_of_that_host(self):
        # the user 2026-09-06, whose test notification for a session on another machine named its
        # short id: the kernel DOES know that name — the tunnel supervisor polls every attached
        # host's /sessions (id + name) for the wake-router's map — so the notification and the
        # popover's result line read it from there, host-prefixed the way the dashboard shows it.
        # The shell's label is display text of last resort and loses to the kernel's own copy.
        self._snapshot("boxa", {SID_API: "api"})
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp, \
             mock.patch.object(km, "_name_of", return_value=None):
            code, body = self._post("/push/test", {"endpoint": self.ep, "sid": "boxa:" + SID_API, "host": "boxa",
                                                   "label": "boxa:stale-label"})
        self.assertEqual(code, 200)
        res = json.loads(body)
        self.assertEqual((res["sid"], res["name"]), ("boxa:" + SID_API, "boxa:api"))
        d = json.loads(pp.call_args[0][1].decode())
        self.assertEqual(d["body"], "Test notification — tap to come back to boxa:api.")
        # the TITLE wears the session name alone (the user 2026-09-09: the host is not their
        # concern — the tap carries the routing in `data`); the body and the popover's result
        # line keep the host-prefixed name the merged dashboard shows
        self.assertEqual(d["title"], "Romp: api")
        self.assertNotIn("boxa", d["title"])
        self.assertEqual(d["data"]["name"], "boxa:api", "the routing block (#1157) carries the form the body and the popover echo wear")

    def test_the_shells_label_stands_in_when_the_kernel_has_no_name_for_the_id(self):
        # no snapshot for that host (never polled, or a kernel too old to file names): the tab's own
        # label — the user's UI text, display-only — beats a bare short id
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp, \
             mock.patch.object(km, "_name_of", return_value=None):
            code, body = self._post("/push/test", {"endpoint": self.ep, "sid": "boxa:" + SID_API, "host": "boxa",
                                                   "label": "  boxa:api\n(paused) "})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["name"], "boxa:api (paused)", "trimmed and flattened, otherwise verbatim")
        d = json.loads(pp.call_args[0][1].decode())
        self.assertEqual(d["body"], "Test notification — tap to come back to boxa:api (paused).")
        # the title takes the label WHOLE: it is the user's own tab text, not a host:name the kernel composed,
        # so there is no host to strip from it (only a snapshot-resolved remote name loses its prefix in the title)
        self.assertEqual(d["title"], "Romp: boxa:api (paused)")
        self.assertEqual(d["data"]["name"], "boxa:api (paused)", "the routing block carries the same stand-in")
        # a local session keeps the registry's name even when the label disagrees (the registry is authoritative)
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=(201, "Created")), \
             mock.patch.object(km, "_name_of", side_effect=lambda s: "web" if s == SID_WEB else None):
            code, body = self._post("/push/test", {"endpoint": self.ep, "sid": SID_WEB, "host": "", "label": "renamed"})
        self.assertEqual(json.loads(body)["name"], "web")

    def test_the_label_is_a_capped_string_or_a_400(self):
        with mock.patch.object(km, "_vapid_keys", return_value=(None, "pub")), \
             mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp, \
             mock.patch.object(km, "_name_of", return_value=None):
            code, _ = self._post("/push/test", {"endpoint": self.ep, "sid": "boxa:" + SID_API, "label": 5})
            self.assertEqual(code, 400)
            code, _ = self._post("/push/test", {"endpoint": self.ep, "sid": "boxa:" + SID_API, "label": ["x"]})
            self.assertEqual(code, 400)
            code, body = self._post("/push/test", {"endpoint": self.ep, "sid": "boxa:" + SID_API, "label": "x" * 500})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["name"], "x" * km.PUSH_LABEL_MAX, "long UI text is clipped, not refused")
        self.assertLessEqual(km.PUSH_LABEL_MAX, 80)
        # the title wears the SAME clipped stand-in (#1157's clip meets #1155's title rule through one lookup,
        # _push_session_names): an unclipped 500-char title over a clipped body would be two names for one session
        d = json.loads(pp.call_args[0][1].decode())
        self.assertEqual(d["title"], "Romp: " + "x" * km.PUSH_LABEL_MAX)
        self.assertEqual(d["data"]["name"], "x" * km.PUSH_LABEL_MAX, "the routing block carries the name the body wears")

    def test_without_a_sid_the_probe_is_what_it_was(self):
        code, res, pp = self._test((201, "Created"))
        self.assertEqual(res, {"ok": True, "status": 201, "detail": "Created"}, "no session, no sid or name echoed")
        d = json.loads(pp.call_args[0][1].decode())
        self.assertEqual(d["body"], "Test notification — this device is set up.")
        self.assertEqual(d["data"]["url"], "/", "nowhere to land: the tap just brings romp forward")

    def test_a_sid_or_host_that_is_not_a_string_is_a_400(self):
        code, _ = self._post("/push/test", {"endpoint": self.ep, "sid": 5})
        self.assertEqual(code, 400)
        code, _ = self._post("/push/test", {"endpoint": self.ep, "sid": SID_WEB, "host": ["boxa"]})
        self.assertEqual(code, 400)

    def test_a_refusal_comes_back_verbatim(self):
        code, res, _ = self._test((403, "Forbidden: {\"reason\":\"BadJwtToken\"}"))
        self.assertEqual(code, 200)
        self.assertEqual(res["ok"], False)
        self.assertEqual(res["status"], 403)
        self.assertIn("BadJwtToken", res["detail"])
        self.assertIn(self.ep, km._push_subs(), "a refusal short of dead keeps the subscription")

    def test_a_dead_subscription_is_pruned_and_says_so(self):
        code, res, _ = self._test((410, "Gone"))
        self.assertEqual((code, res["ok"], res["status"]), (200, False, 410))
        self.assertIn("removed", res["detail"])
        self.assertNotIn(self.ep, km._push_subs(), "the same prune the fan-out applies")

    def test_no_answer_is_status_zero(self):
        _, res, _ = self._test((0, "URLError: timed out"))
        self.assertEqual((res["ok"], res["status"]), (False, 0))
        self.assertIn("timed out", res["detail"])

    def test_an_unknown_endpoint_says_not_subscribed(self):
        with mock.patch.object(km, "_push_post") as pp:
            code, body = self._post("/push/test", {"endpoint": "https://push.example.net/send/other"})
        self.assertEqual(code, 200)
        res = json.loads(body)
        self.assertEqual((res["ok"], res["status"]), (False, 0))
        self.assertIn("isn't subscribed", res["detail"])
        pp.assert_not_called()

    def test_missing_crypto_is_the_loud_500(self):
        with mock.patch.object(km, "_PUSH_CRYPTO", [False]):
            code, body = self._post("/push/test", {"endpoint": self.ep})
        self.assertEqual(code, 500)
        self.assertIn("cryptography", body)

    def test_gated_and_validated(self):
        code, _ = self._post("/push/test", {"endpoint": self.ep}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/push/test", {})
        self.assertEqual(code, 400)
        code, _ = self._post("/push/test", None, raw=b"nope")
        self.assertEqual(code, 400)


class RemoteNames(unittest.TestCase):
    """Where the kernel's copy of a remote session's name comes from (2026-09-06): the tunnel
    supervisor already GETs each attached host's /sessions through the -L tunnel every pass for the
    wake-router's host↔sid map; the same rows carry `name`, so the poll files both beside each
    other on the host's row and _remote_name_of reads that snapshot."""

    def _serve_rows(self, rows):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        body = json.dumps(rows).encode()

        class Svc(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Svc)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_the_poll_returns_the_rows_and_the_ids_are_read_off_them(self):
        port = self._serve_rows([{"id": SID_API, "name": "api", "state": "ready"},
                                 {"id": SID_WEB, "name": "web"}, {"nope": 1}, "junk"])
        r = {"host": "boxa", "local_port": port, "token": ""}
        rows = km._poll_remote_sessions(r)
        self.assertEqual([x["id"] for x in rows], [SID_API, SID_WEB], "id-bearing dict rows only")
        self.assertEqual(r["_probe"], "ok")
        self.assertEqual(km._poll_remote_sids(r), [SID_API, SID_WEB], "the sid list is unchanged for its callers")
        self.assertEqual(km._remote_names(rows), {SID_API: "api", SID_WEB: "web"})
        self.assertEqual(km._remote_names([{"id": SID_API, "name": 7}, {"id": SID_WEB}]), {},
                         "a row without a string name files nothing — never a coined one")

    def test_the_supervisor_files_names_beside_sids(self):
        import inspect
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('r["sids"] = sids', src)
        self.assertIn('r["names"] = _remote_names(rows)', src, "same poll, same locked write as the sids")
        self.assertIn("rows = _poll_remote_sessions(r) if up else None", src, "ONE GET per pass, not a second one for names")

    def test_remote_name_of_reads_the_snapshot_and_says_nothing_otherwise(self):
        with km._remotes_lock:
            km._remotes["boxa"] = {"host": "boxa", "sids": [SID_API], "names": {SID_API: "api"}}
            km._remotes["boxb"] = {"host": "boxb", "sids": [SID_WEB]}          # an older row: no names filed
        self.addCleanup(lambda: (km._remotes.pop("boxa", None), km._remotes.pop("boxb", None)))
        self.assertEqual(km._remote_name_of("boxa", SID_API), "api")
        self.assertIsNone(km._remote_name_of("boxa", SID_WEB))
        self.assertIsNone(km._remote_name_of("boxb", SID_WEB))
        self.assertIsNone(km._remote_name_of("nohost", SID_WEB))


def _stamp_stop(sid, t, opener=None):
    """The Stop hook's ledger stamp — the SDK session's authoritative turn-end fact. `opener` is the
    lastTurnOpener the hook stamps beside it (2026-09-10); None leaves the field off, an older ledger's shape."""
    d = jd.STATE / "sdk"
    d.mkdir(parents=True, exist_ok=True)
    reg = {"lastStopAt": int(t)}
    if opener is not None:
        reg["lastTurnOpener"] = opener
    (d / (sid + ".json")).write_text(json.dumps(reg))


def _append_state(sid, state, t):
    d = jd.STATE / "states"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / (sid + ".jsonl"), "a") as f:
        f.write(json.dumps({"t": int(t), "state": state}) + "\n")


def _transcript(sid, text):
    p = jd.STATE / ("transcript-%s.jsonl" % sid[-2:])
    rec = {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    p.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n" + json.dumps(rec) + "\n")
    return str(p)


class TurnFinishedPush(unittest.TestCase):
    """_turn_notify_tick: a session's turn-end key moving is the event; both switches gate it."""

    def setUp(self):
        _reset_store()
        for p in (jd.STATE / "sdk", jd.STATE / "states"):
            for f in p.glob("*") if p.exists() else []:
                f.unlink()
        self.path = _transcript(SID_WEB, "Done: the login flow now redirects to the notes list.\n\nDetails below.")
        self.alive = [{"sid": SID_WEB, "name": "web", "path": self.path}]
        self.tmux = {SID_WEB: {"state": "waiting"}}

    def _tick(self):
        pushed, fwd = [], []
        with mock.patch.object(km, "_alive_sessions", return_value=self.alive), \
             mock.patch.object(km, "_push_notify", side_effect=lambda *a, **k: pushed.append((a, k))), \
             mock.patch.object(km, "_push_forward", side_effect=lambda evs: fwd.append(evs)):
            fired = km._turn_notify_tick(time.time(), self.tmux)
        return fired, pushed, fwd

    def test_first_sight_is_a_silent_baseline_then_a_new_end_fires(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _stamp_stop(SID_WEB, 1000)
        fired, pushed, fwd = self._tick()
        self.assertEqual((fired, pushed, fwd), ([], [], []), "existing state is status, not news")
        _stamp_stop(SID_WEB, 1001)
        fired, pushed, fwd = self._tick()
        # the title is "Romp: <session>" (the user 2026-09-09: one title rule for every kind; a turn end
        # is not a needs-you) — was the bare session name. The body stays the first line it said.
        self.assertEqual(fired, [{"title": "Romp: web", "body": "Done: the login flow now redirects to the notes list.",
                                  "sid": SID_WEB, "kind": "turn"}])   # preview reconciliation with #940: the kind rides to peers
        (args, kw), = pushed
        self.assertEqual(args, ("Romp: web", "Done: the login flow now redirects to the notes list.", SID_WEB))
        self.assertNotIn("badge", kw, "the count rides its own push")
        # the routing block's `name` (the ledger row files it, so the kernel's lines can name the session) is the session name
        # the title was built from — NOT the title: "Romp: web" is not a session
        self.assertEqual(kw, {"kind": "turn", "name": "web"})
        self.assertEqual(fwd, [fired], "the same event travels to trusted peers, the bell-event way")
        # the same key again is nothing new
        self.assertEqual(self._tick()[0], [])

    def test_both_switches_must_be_on(self):
        _stamp_stop(SID_WEB, 1000)
        self._tick()                                       # baseline
        for master, turns in ((False, False), (True, False), (False, True)):
            km._set_notify_all(master)
            km._set_notify_turns(turns)
            _stamp_stop(SID_WEB, 1001 + int(master) + 2 * int(turns))
            self.assertEqual(self._tick()[0], [], (master, turns))
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _stamp_stop(SID_WEB, 1010)
        self.assertEqual(len(self._tick()[0]), 1, "…and with both on the next end fires")

    def test_an_interrupt_settle_is_not_a_finished_turn(self):
        # a Stop press writes an idle row (romp's _record_idle, tagged by:interrupt) — the user's own
        # act, not a turn the session finished, so the fallback key must skip it (#937 fold). A session
        # with no lastStopAt in its ledger leaves the fallback to decide.
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _append_state(SID_WEB, "working", 1000)
        self._tick()                                       # baseline
        d = jd.STATE / "states"
        with open(d / (SID_WEB + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": 1001, "state": "idle", "by": "interrupt"}) + "\n")
        self.assertEqual(self._tick()[0], [], "the interrupt's settle is not a turn end")
        # a genuine stop the SESSION wrote (no `by`) does fire
        with open(d / (SID_WEB + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": 1002, "state": "waiting"}) + "\n")
        self.assertEqual(len(self._tick()[0]), 1, "a real stopped transition still fires")

    def test_a_states_log_that_has_not_moved_is_not_reread(self):
        # the tick asks _turn_end_key for every alive session every pusher cycle; a states log whose
        # (mtime, size) is unchanged is answered from the memo, not re-scanned (#937 fold)
        km._set_notify_all(True); km._set_notify_turns(True)
        _append_state(SID_WEB, "waiting", 1000)
        self._tick()
        opens = {"n": 0}
        import builtins
        real_open = builtins.open
        target = str(jd.STATE / "states" / (SID_WEB + ".jsonl"))
        def counting_open(f, *a, **k):
            if str(f) == target:
                opens["n"] += 1
            return real_open(f, *a, **k)
        with mock.patch.object(builtins, "open", counting_open):
            for _ in range(5):
                self._tick()
        self.assertEqual(opens["n"], 0, "the unchanged states log is served from the memo, never re-opened")

    def test_switching_on_later_never_replays_old_ends(self):
        _stamp_stop(SID_WEB, 1000)
        self._tick()
        _stamp_stop(SID_WEB, 1001)                          # an end that happened while off
        self._tick()
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.assertEqual(self._tick()[0], [], "the memo advanced while off; nothing to replay")

    def test_a_muted_session_stays_quiet(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        km._set_notify_session(SID_WEB, False)              # its own bell off = a mute under the master
        _stamp_stop(SID_WEB, 1000)
        self._tick()
        _stamp_stop(SID_WEB, 1001)
        self.assertEqual(self._tick()[0], [])

    def test_an_empty_reply_gets_a_plain_body(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.alive[0]["path"] = str(jd.STATE / "no-such-transcript.jsonl")
        _stamp_stop(SID_WEB, 1000)
        self._tick()
        _stamp_stop(SID_WEB, 1001)
        self.assertEqual(self._tick()[0][0]["body"], "finished a turn")

    def test_the_fallback_key_counts_only_stopped_transitions(self):
        # a session without a Stop-hook ledger: states/ is the record — and a turn STARTING (working)
        # must never read as an end
        km._set_notify_all(True)
        km._set_notify_turns(True)
        _append_state(SID_WEB, "waiting", 1000)
        self.assertEqual(km._turn_end_key(SID_WEB), 1000)
        self._tick()                                        # baseline
        _append_state(SID_WEB, "working", 1001)
        self.assertEqual(km._turn_end_key(SID_WEB), 0, "a start is not an end")
        self.assertEqual(self._tick()[0], [])
        _append_state(SID_WEB, "waiting", 1002)
        self.assertEqual(len(self._tick()[0]), 1, "the stop after it is")

    def test_wired_into_the_pusher_cycle_after_the_feed_build(self):
        import inspect
        src = inspect.getsource(km._pusher_cycle_jobs)
        self.assertIn("_turn_notify_tick(now, live_map)", src)
        self.assertLess(src.index("_push_all(live_map=live_map)"), src.index("_turn_notify_tick(now, live_map)"),
                        "the feed builds first, so a same-settle bell event files its buzz first")


class TurnOpenerGate(unittest.TestCase):
    """The turn-finished push buzzes for the HUMAN's turns only (2026-09-10). A session running background
    subagents gets a harness-injected user-role turn per completion (the task notification), reacts to it,
    and that reaction's Stop stamped lastStopAt like any other end: ten buzzes in fifty minutes from one
    coordinating session, none about anything the user had asked at that moment. The Stop hook now stamps
    WHO opened the turn beside the settle (lastTurnOpener, sdk_backend) and the tick skips an end whose
    opener is not the human — without spending the buzz claim, so a bell event that turn raises keeps its
    buzz. A registry without the field (an older ledger) reads as the human's: a missing
    fact never drops the user's buzz."""

    def setUp(self):
        _reset_store()
        for p in (jd.STATE / "sdk", jd.STATE / "states"):
            for f in p.glob("*") if p.exists() else []:
                f.unlink()
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.path = _transcript(SID_WEB, "The report is in; folding it into the plan.")
        self.alive = [{"sid": SID_WEB, "name": "web", "path": self.path}]

    def tearDown(self):
        km._set_notify_all(False)
        km._set_notify_turns(False)

    def _tick(self):
        pushed = []
        with mock.patch.object(km, "_alive_sessions", return_value=self.alive), \
             mock.patch.object(km, "_push_notify", side_effect=lambda *a, **k: pushed.append((a, k))), \
             mock.patch.object(km, "_push_forward"):
            fired = km._turn_notify_tick(time.time(), {SID_WEB: {"state": "waiting"}})
        return fired, pushed

    def test_a_turn_the_human_opened_buzzes_as_before(self):
        _stamp_stop(SID_WEB, 1000, opener="human")
        self._tick()                                                   # the baseline sighting
        _stamp_stop(SID_WEB, 1001, opener="human")
        fired, pushed = self._tick()
        self.assertEqual(len(fired), 1)
        self.assertEqual(pushed[0][0], ("Romp: web", "The report is in; folding it into the plan.", SID_WEB))

    def test_a_turn_a_task_notification_opened_is_silent_and_spends_no_claim(self):
        _stamp_stop(SID_WEB, 1000, opener="human")
        self._tick()
        _stamp_stop(SID_WEB, 1001, opener="injected")             # a subagent's completion, reacted to
        fired, pushed = self._tick()
        self.assertEqual((fired, pushed), ([], []), "nobody asked the user anything: no buzz")
        self.assertNotIn(SID_WEB, km._PUSH_BUZZED, "the claim is untouched…")
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "bell"), "…so a bell event that turn raises still buzzes")

    def test_the_memo_advances_past_silent_ends_and_the_next_human_end_fires_once(self):
        _stamp_stop(SID_WEB, 1000, opener="human")
        self._tick()
        for t in (1001, 1002, 1003):                                   # three completions, three reactions
            _stamp_stop(SID_WEB, t, opener="injected")
            self.assertEqual(self._tick()[0], [], t)
        _stamp_stop(SID_WEB, 1004, opener="human")
        self.assertEqual(len(self._tick()[0]), 1, "the human's next turn buzzes once; the silent ends never replay")
        self.assertEqual(self._tick()[0], [])

    def test_an_older_registry_without_the_field_is_the_humans(self):
        _stamp_stop(SID_WEB, 1000)
        self._tick()
        _stamp_stop(SID_WEB, 1001)                                     # a pre-field ledger: lastStopAt alone
        self.assertNotIn("lastTurnOpener", km._thread_reg(SID_WEB))
        self.assertEqual(len(self._tick()[0]), 1, "a missing fact never drops the user's buzz")

    def test_a_value_the_hook_never_writes_reads_as_the_humans(self):
        _stamp_stop(SID_WEB, 1000, opener="human")
        self._tick()
        _stamp_stop(SID_WEB, 1001, opener=7)
        self.assertEqual(len(self._tick()[0]), 1)
        self.assertEqual(km._turn_opener({"lastTurnOpener": "injected"}), "injected")
        self.assertEqual(km._turn_opener({"lastTurnOpener": "human"}), "human")
        for junk in ({}, {"lastTurnOpener": None}, {"lastTurnOpener": "peer"}, None):
            self.assertEqual(km._turn_opener(junk), "human", junk)

    def test_the_opener_and_the_settle_come_off_one_registry_read_and_the_gate_precedes_the_claim(self):
        # the pair is ONE Stop-hook write; reading the file twice could pair an older settle with a
        # newer turn's opener (a lost buzz on the race)
        import inspect
        src = inspect.getsource(km._turn_notify_tick)
        self.assertIn("reg = _thread_reg(sid)", src)
        self.assertIn("_turn_end_key(sid, reg)", src)
        self.assertLess(src.index("_turn_opener(reg)"), src.index('_buzz_claim(sid, key, "turn")'),
                        "the gate sits before the claim, so a silent end spends nothing")


class TurnOpenerStamp(unittest.TestCase):
    """The backend's half of the gate: who opened the turn is a fact the session learns at the two moments a
    turn can open — the feeder's POP (a fed text: the human's words, or romp's own marker-carrying nudge /
    follow-up / restart notice / relayed mail) and a user atom the CLI STREAMS WHILE IDLE (a turn the CLI
    opened by itself: a background task's notification, a scheduled prompt, a peer's channel message, told
    by its origin stamp; a fed text is never replayed on the stream). The Stop hook stamps it beside
    lastStopAt in the same write. Synthetic throughout: the notes-api demo session, invented prompt text."""

    NUDGE = "Where does this stand?\n\n<!-- romp-injected --><!-- romp-auto -->"
    NOTIF = "[SYSTEM NOTIFICATION - NOT USER INPUT]\n\n<task-notification>done</task-notification>"

    class _TextBlock:
        def __init__(self, text):
            self.text = text

    class _UserMessage:
        def __init__(self, content, origin=None, uuid="u-1"):
            self.content, self.uuid, self.tool_use_result, self.origin = content, uuid, None, origin

    _TextBlock.__name__ = "TextBlock"
    _UserMessage.__name__ = "UserMessage"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be, self.sess = self._session(self.d)

    def _session(self, state_dir):
        be = sb.SdkBackend(state_dir, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(Path(state_dir), SID_WEB, {"sid": SID_WEB, "name": "web", "cwd": "/tmp", "alive": True})
        return be, sb.SdkSession(be, sb.read_reg(Path(state_dir), SID_WEB))

    def _streamed(self, origin, text=NOTIF, be=None, sess=None):
        be, sess = be or self.be, sess or self.sess
        be._forward(sess, self._UserMessage([self._TextBlock(text)], origin=origin, uuid="u-%d" % time.time_ns()))

    def _stop(self):
        asyncio.run(self.sess._stop_hook({}, None, None))
        return sb.read_reg(Path(self.d), SID_WEB) or {}

    def _idle(self):
        self.sess.inflight = 0
        self.sess._cli_working = False

    def test_fed_text_opener_reads_the_marker_in_its_comment_form_only(self):
        self.assertEqual(sb.fed_text_opener("fix the login redirect"), "human")
        self.assertEqual(sb.fed_text_opener("/compact"), "human")                 # the user's own button
        self.assertEqual(sb.fed_text_opener(self.NUDGE), "injected")
        self.assertEqual(sb.fed_text_opener(sb.RENAME_PING_HEAD + " to x"), "injected")
        self.assertEqual(sb.fed_text_opener("a typed note that mentions romp-injected in prose"), "human",
                         "the comment form only, the rule send()'s echo authoring follows")
        self.assertEqual(sb.fed_text_opener(""), "human")

    def test_the_humans_text_opens_the_turn_as_theirs_and_the_stop_stamps_it_beside_the_settle(self):
        self.sess._note_turn_opener(sb.fed_text_opener("fix the login redirect"), True)
        reg = self._stop()
        self.assertEqual(reg["lastTurnOpener"], "human")
        self.assertAlmostEqual(reg["lastStopAt"], time.time(), delta=30)

    def test_a_nudge_fed_from_idle_opens_an_injected_turn(self):
        self.sess._note_turn_opener(sb.fed_text_opener(self.NUDGE), True)
        self.assertEqual(self._stop()["lastTurnOpener"], "injected")

    def test_a_nudge_folded_into_the_humans_turn_leaves_it_theirs(self):
        self.sess._note_turn_opener("human", True)
        self.sess._note_turn_opener(sb.fed_text_opener(self.NUDGE), False)     # mid-turn: the CLI splices it in
        self.assertEqual(self._stop()["lastTurnOpener"], "human")

    def test_the_humans_message_spliced_into_an_injected_turn_makes_it_theirs(self):
        # the person asked during it and is answered in it, so the turn's end IS news to them
        self.sess._note_turn_opener("injected", True)
        self.sess._note_turn_opener(sb.fed_text_opener("and the tests?"), False)
        self.assertEqual(self._stop()["lastTurnOpener"], "human")

    def test_a_task_notification_streamed_while_idle_opens_an_injected_turn(self):
        self.sess._note_turn_opener("human", True)      # the previous turn, the human's…
        self._idle()                                    # …settled
        self._streamed({"kind": "task-notification"})
        self.assertEqual(self.sess._turn_opener, "injected")
        self.assertTrue(self.sess._cli_working, "the atom still re-asserts working, as before")
        self.assertEqual(self._stop()["lastTurnOpener"], "injected")

    def test_a_peers_message_and_a_scheduled_prompt_streamed_while_idle_are_injected_too(self):
        for origin in ({"kind": "peer", "name": "api"},
                       {"kind": "task-notification", "subkind": "peer-send-message"},
                       {"kind": "task-notification", "subkind": "scheduled-trigger"},
                       {"kind": "auto-continuation"}):
            self.sess._turn_opener = "human"
            self._idle()
            self._streamed(origin)
            self.assertEqual(self.sess._turn_opener, "injected", origin)

    def test_a_notification_spliced_into_the_humans_turn_does_not_take_it(self):
        self.sess._note_turn_opener("human", True)
        self.sess.inflight = 1                           # the human's text is in flight
        self.sess._cli_working = True
        self._streamed({"kind": "task-notification"})
        self.assertEqual(self.sess._turn_opener, "human")

    def test_a_stamp_less_or_human_stamped_user_atom_says_nothing(self):
        self.sess._note_turn_opener("human", True)
        self._idle()
        self._streamed(None, text="hello there")
        self._streamed({"kind": "human"}, text="hello again")
        self.assertEqual(self.sess._turn_opener, "human")

    def test_no_open_seen_stamps_the_humans(self):
        # a fresh session object (a kernel restart mid-turn), or a CLI that stamps no origin: fail OPEN
        # on the buzz — a missing fact never drops the user's
        self.assertIsNone(self.sess._turn_opener)
        self.assertEqual(self._stop()["lastTurnOpener"], "human")

    def test_the_feeder_notes_the_opener_at_the_pop(self):
        import inspect
        src = inspect.getsource(sb.SdkSession._amain)
        self.assertIn("self._note_turn_opener(fed_text_opener(item), fresh)", src)
        self.assertLess(src.index("fresh = item is not None"), src.index("self._note_turn_opener("),
                        "fresh (from idle, not mid-turn) is decided under the lock first")

    def test_end_to_end_the_kernel_buzzes_for_the_humans_turn_and_not_the_nudged_or_notified_ones(self):
        # the backend's Stop hook writes the same STATE/sdk/<sid>.json the kernel's tick reads
        _reset_store()
        for p in (jd.STATE / "sdk", jd.STATE / "states"):
            for f in p.glob("*") if p.exists() else []:
                f.unlink()
        km._set_notify_all(True)
        km._set_notify_turns(True)
        self.addCleanup(lambda: (km._set_notify_all(False), km._set_notify_turns(False)))
        be, sess = self._session(str(jd.STATE))
        alive = [{"sid": SID_WEB, "name": "web", "path": _transcript(SID_WEB, "Folded the report in.")}]

        def stop_at(t):
            with mock.patch("time.time", return_value=float(t)):
                asyncio.run(sess._stop_hook({}, None, None))
            sess._mark("waiting")                                      # the settle that follows the Stop

        def tick():
            with mock.patch.object(km, "_alive_sessions", return_value=alive), \
                 mock.patch.object(km, "_push_notify"), mock.patch.object(km, "_push_forward"):
                return km._turn_notify_tick(time.time(), {SID_WEB: {"state": "waiting"}})

        sess._note_turn_opener(sb.fed_text_opener("start on the login flow"), True)
        stop_at(2000)
        tick()                                                         # the baseline sighting
        self.assertEqual(km._thread_reg(SID_WEB)["lastTurnOpener"], "human")
        # a subagent finished: the CLI opens the turn itself, the session reacts, the Stop stamps
        self._streamed({"kind": "task-notification"}, be=be, sess=sess)
        stop_at(2001)
        self.assertEqual(tick(), [], "the reaction's end is not news to the user")
        # romp's own nudge, fed from idle
        sess._note_turn_opener(sb.fed_text_opener(self.NUDGE), True)
        stop_at(2002)
        self.assertEqual(tick(), [])
        # the human's next message
        sess._note_turn_opener(sb.fed_text_opener("ship it"), True)
        stop_at(2003)
        self.assertEqual([f["body"] for f in tick()], ["Folded the report in."])


class FirstLine(unittest.TestCase):
    def test_first_non_empty_line_clipped(self):
        self.assertEqual(km._first_line("\n\n  Shipped it.  \nmore"), "Shipped it.")
        self.assertEqual(km._first_line(""), "")
        long = "x" * 200
        out = km._first_line(long)
        self.assertEqual(len(out), 120)
        self.assertTrue(out.endswith("…"))


class OneBuzzPerTurnEnd(unittest.TestCase):
    """The no-double-buzz rule, both orders."""

    def setUp(self):
        _reset_store()
        for f in (jd.STATE / "sdk").glob("*") if (jd.STATE / "sdk").exists() else []:
            f.unlink()

    def test_turn_first_then_the_bell_event_yields(self):
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "turn"))
        self.assertFalse(km._buzz_claim(SID_WEB, 1001, "bell"), "the phone already buzzed for this stop")
        self.assertTrue(km._buzz_claim(SID_WEB, 1002, "bell"), "a later turn end is new information")

    def test_bell_first_then_the_turn_push_yields(self):
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "bell"))
        self.assertFalse(km._buzz_claim(SID_WEB, 1001, "turn"))
        self.assertTrue(km._buzz_claim(SID_WEB, 1002, "turn"))

    def test_bell_events_never_suppress_each_other(self):
        # two cards of one session moving in one build buzz twice — exactly as before the rule
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "bell"))
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "bell"))
        self.assertFalse(km._buzz_claim(SID_WEB, 1001, "turn"), "…and the turn push still yields to them")

    def test_sessions_are_independent_and_sidless_always_passes(self):
        self.assertTrue(km._buzz_claim(SID_WEB, 1001, "turn"))
        self.assertTrue(km._buzz_claim(SID_API, 1001, "bell"))
        self.assertTrue(km._buzz_claim("", 0, "bell"))
        self.assertTrue(km._buzz_claim("", 0, "bell"))

    def test_the_tick_yields_to_a_bell_claim(self):
        km._set_notify_all(True)
        km._set_notify_turns(True)
        path = _transcript(SID_WEB, "Which migration should I keep?")
        alive = [{"sid": SID_WEB, "name": "web", "path": path}]
        with mock.patch.object(km, "_alive_sessions", return_value=alive), \
             mock.patch.object(km, "_push_notify") as pn, \
             mock.patch.object(km, "_push_forward") as pf:
            _stamp_stop(SID_WEB, 1000)
            km._turn_notify_tick(time.time(), {SID_WEB: {}})            # baseline
            _stamp_stop(SID_WEB, 1001)
            # the judges ruled inside the same cycle: the card moved and its bell event filed first
            self.assertTrue(km._buzz_claim(SID_WEB, km._turn_end_key(SID_WEB), "bell"))
            self.assertEqual(km._turn_notify_tick(time.time(), {SID_WEB: {}}), [])
            pn.assert_not_called()
            pf.assert_not_called()

    def test_the_yielding_bell_push_still_carries_the_badge_quietly(self):
        # a card push that yields the BUZZ to an already-fired turn push must still deliver the badge,
        # QUIET: a closed installed app learns the needs-you count only from a push (#937 fold)
        import inspect
        src = inspect.getsource(km._cached_feed)
        self.assertIn('_push_notify(_t, _b, _sid, _badge, kind="card", card_id=_iid, quiet=True)', src,
                      "the yielding branch still pushes, with the badge, quiet")
        self.assertLess(src.index('quiet=True'), src.index('_push_notify(_t, _b, _sid, _badge, kind="card", card_id=_iid)'),
                        "the quiet yield sits in the claim-failed branch, above the normal push")

    def test_the_feed_path_claims_before_it_pushes(self):
        import inspect
        src = inspect.getsource(km._cached_feed)
        self.assertIn('_buzz_claim(_sid, _turn_end_key(_sid), "bell")', src)
        self.assertLess(src.index("_buzz_claim("), src.index('_push_notify(_t, _b, _sid, _badge, kind="card", card_id=_iid)'))
        self.assertLess(src.index("_system_notify(_t, _b)"), src.index("_buzz_claim("),
                        "the desktop notice is not the buzz and never yields")


class RelayOfTurnEvents(unittest.TestCase):
    def setUp(self):
        km._set_notify_all(True)                 # the receiver's switches must be on for a relay to land (#937 fold)
        km._set_notify_turns(True)

    def tearDown(self):
        km._set_notify_all(False)
        km._set_notify_turns(False)

    def test_a_turn_shaped_event_mirrors_with_the_origin_on_its_sid(self):
        # the relay touches the SID only (the federation test's contract): the title passes as the
        # origin composed it, the sid gains the origin so a tap routes through the merged dashboard
        with km._remotes_lock:
            km._remotes["boxa"] = {"host": "boxa", "kernel_port": 1, "local_port": 1, "token": "tok",
                                   "proc": None, "status": "up", "trust": "trusted"}
        try:
            raw = json.dumps({"origin": "boxa", "events": [{"title": "Romp: web", "body": "Done: shipped.", "sid": SID_WEB}]}).encode()
            h = km.Handler.__new__(km.Handler)
            h.client_address = ("127.0.0.1", 0)
            h.headers = {"X-Romp-Token": km.TOKEN, "Content-Length": str(len(raw))}
            h.path = "/push/relay"
            h.command = "POST"
            h.request_version = "HTTP/1.1"
            h.wfile = io.BytesIO()
            h.rfile = io.BytesIO(raw)
            h.close_connection = True
            h.send_response = lambda code, *a: None
            h.send_header = lambda k, v: None
            h.end_headers = lambda: None
            h.log_message = lambda *a: None
            with mock.patch.object(km, "_push_notify") as pn:
                h.do_POST()
            pn.assert_called_once()   # preview reconciliation with #940: kind/host ride as kwargs
            args, kw = pn.call_args
            self.assertEqual(args, ("Romp: web", "Done: shipped.", "boxa:" + SID_WEB))
            self.assertEqual(kw.get("host"), "boxa")
            self.assertNotIn("boxa", args[0] + args[1], "the host rides the routing, never the words")
        finally:
            with km._remotes_lock:
                km._remotes.pop("boxa", None)


class ShellPopover(unittest.TestCase):
    """The rendered landing: the popover's markup and skin, the wiring around it."""

    @classmethod
    def setUpClass(cls):
        cls.html = km._landing()

    def test_the_four_rows(self):
        h = self.html
        self.assertIn("id=rbell-back hidden", h)
        self.assertIn("id=rbell-pop role=dialog", h)
        for act in ("data-act=all", "data-act=dev", "data-act=turns"):
            self.assertIn("class=rbp-row %s role=switch" % act, h)
        # the master is labelled as the master (was "All devices", which beside "This device" read as
        # a scope choice rather than the switch the other two sit under — the user 2026-09-05)
        self.assertIn(">Notifications<", h)
        self.assertNotIn(">All devices<", h)
        self.assertIn(">This device<", h)
        self.assertIn(">Also when a turn finishes<", h)
        self.assertIn("id=rbp-test data-act=test>Send a test notification<", h)
        # the "why" under each switch — the master's in two sentences: what off does, what the bells are
        self.assertIn("The main switch: off silences every device subscribed to this romp, and this desktop. "
                      "The bells on sessions and cards are mutes under it.", h)
        self.assertIn("Buzzes every time any session finishes a turn. With many sessions running, that is a lot of buzzing.", h)
        self.assertIn("id=rbp-test-out", h)

    def test_the_device_and_turn_rows_nest_under_the_master(self):
        h = self.html
        pop = h[h.index("<div id=rbell-pop"):h.index("<div class=rbp-div>")]
        # the nest opens right after the master row and closes right before the divider, holding
        # exactly the device and turns rows — a visible hierarchy, not three peers
        self.assertIn("</div></div><div class=rbp-nest><div class=rbp-row data-act=dev", pop)
        self.assertTrue(pop.endswith("</div></div></div>"), "the nest closes before the divider")
        self.assertEqual(pop.count("<div class=rbp-nest>"), 1)
        self.assertLess(pop.index("data-act=all"), pop.index("<div class=rbp-nest>"))
        self.assertLess(pop.index("<div class=rbp-nest>"), pop.index("data-act=dev"))
        self.assertLess(pop.index("data-act=dev"), pop.index("data-act=turns"))
        # one indent + one hairline, the popover's own hairline token (with its dark fallback, as
        # every token in this block carries — the token test below sweeps the same slice)
        self.assertIn(".rbp-nest{margin-left:10px;padding-left:4px;border-left:1px solid var(--menu-border,rgba(255,255,255,0.12))}", h)

    def test_master_off_dims_the_nested_rows_but_leaves_them_operable(self):
        js, h = km._LANDING_PUSH_JS, self.html
        # the class the JS toggles, from INSIDE paint() — the one function the boot fetch and the
        # kernel's notifyAll push both run, so the dim follows the master's live state with no
        # polling and no second source of truth
        paint = js[js.index("function paint(){"):js.index("window.__rompNotifyAllPaint=")]
        self.assertIn("pop.classList.toggle('master-off',!isOn)", paint)
        self.assertIn("window.__rompNotifyAllPaint=function(on){isOn=!!on;paint();}", js)
        # …and the rule it drives: the nested rows wear the wash .off and .busy already wear
        self.assertIn("#rbell-pop.master-off .rbp-nest>.rbp-row{opacity:.55}", h)
        self.assertIn(".rbp-row.off{opacity:.55;", h)
        self.assertIn(".rbp-row.busy{opacity:.55}", h)
        # dimmed is not disabled: no cursor or hover suppression on the dimmed rows, and no tap guard
        # reads master-off — a phone can be set up before the main switch goes on
        self.assertNotIn("master-off .rbp-nest>.rbp-row:hover", h)
        self.assertNotIn("master-off .rbp-nest>.rbp-row{opacity:.55;cursor", h)
        self.assertNotIn("master-off", js.replace("pop.classList.toggle('master-off',!isOn)", ""))
        # the device sub-line says the device is set up but nothing arrives — the devOn-and-master-off
        # branch, ahead of the plain devOn line so it wins while the master is off
        self.assertIn('else if(devOn&&!isOn)sub="This device is set up, but nothing arrives until the main switch is on.";', js)
        self.assertLess(js.index("else if(devOn&&!isOn)sub="), js.index('else if(devOn)sub="This browser gets a notification'))

    def test_the_test_result_adds_that_the_master_is_off(self):
        js = km._LANDING_PUSH_JS
        # the test ignores the switches on purpose (it asks "is this phone wired up?"); with the master
        # off, ONE sentence rides the result so a working test is not read as notifications working
        handler = js[js.index("if(act==='test')"):]
        line = "if(!isOn)testOut.textContent+=\" Real notifications won't arrive until the main switch is on.\";"
        self.assertIn(line, handler)
        self.assertEqual(js.count("Real notifications won't arrive"), 1, "one sentence, appended once")
        # appended AFTER the result line, inside the same then-handler, so every answer the push
        # service can give carries it
        self.assertLess(handler.index("'The push service accepted it.'"), handler.index(line))
        self.assertLess(handler.index(line), handler.index("},function(e){testOut.classList.add('bad')"))

    def test_the_test_is_addressed_to_the_session_in_front(self):
        js = km._LANDING_PUSH_JS
        # the shell reads the chat pane's ACTIVE TAB off the same-origin iframe's own DOM — the nodes
        # the mobile header's #mcur chip mirrors — rather than growing a second channel for one fact
        self.assertIn("function activeSession(){", js)
        self.assertIn("document.getElementById('f-chat')", js)
        self.assertIn("tb=d&&d.querySelector('#tabs')", js)
        self.assertIn("t=tb.querySelector('.tab.active[data-id]')", js)
        # a federated tab's id is host:sid, so the host is its prefix; a bare local id has none.
        # The tab's LABEL rides along too (2026-09-06): the kernel names the session from its own
        # registry or its snapshot of the owning host, and falls back to this — the user's own UI
        # text, display-only, clipped here as well as there
        self.assertIn("var i=id.indexOf(':');\nvar lab=t&&t.querySelector('.tab-label');\nreturn {sid:id,host:i>0?id.slice(0,i):'',label:", js)
        self.assertIn(".replace(/\\s+/g,' ').trim().slice(0,80),why:why,tabs:n}", js)   # flattened + clipped at the same cap the kernel applies; plus why an empty read is empty (2026-09-08)
        self.assertEqual(km.PUSH_LABEL_MAX, 80)
        # WHY an empty read is empty (2026-09-08): each miss has a name, and the chip's own first-tab
        # fallback is NOT taken here — the test attaches the session in front or none, never a guess
        for why in ("why='no-frame'", "why='no-doc'", "why='no-tabs'", "why='none-active'", "why='threw'"):
            self.assertIn(why, js)
        handler = js[js.index("if(act==='test')"):]
        # read AT the press, before the subscription lookup's await: the session you were looking
        # at, not the one you switch to while it sends — and filed at once as the trail's first row
        self.assertLess(handler.index("var at=activeSession();"), handler.index("sub().then("))
        self.assertLess(handler.index("diag('push-test',{sidAttached:!!at.sid,host:at.host,why:at.why,tabs:at.tabs});"), handler.index("sub().then("))
        self.assertIn("post('/push/test',{endpoint:s.endpoint,sid:at.sid,host:at.host,label:at.label})", handler)
        # on success with a session, ONE sentence says where the tap goes — in the kernel's words
        # (d.name, the name the notification body carries) — after the outcome, before the master-off note;
        # on success WITHOUT one, the line says so (2026-09-08): a plain probe must never pass for a landing test
        line = "if(ok&&d.name)testOut.textContent+=' Tapping it brings you back to '+d.name+'.';"
        none = "else if(ok)testOut.textContent+=' No session was attached — the tap will only bring romp forward.';"
        self.assertIn(line, handler)
        self.assertIn(none, handler)
        self.assertEqual(js.count("Tapping it brings you back to"), 1, "one sentence, appended once")
        self.assertEqual(js.count("No session was attached"), 1)
        self.assertLess(handler.index("'The push service accepted it.'"), handler.index(line))
        self.assertLess(handler.index(line), handler.index(none))
        self.assertLess(handler.index(none), handler.index("if(!isOn)testOut.textContent+="))

    def test_the_bell_opens_it_and_the_rows_are_the_switches(self):
        js = km._LANDING_PUSH_JS
        self.assertIn("open(bl)", js)
        self.assertNotIn("post('/notify-all',{on:want}).then(function(){\nisOn=want;paint();                       // the master flipped", js,
                         "the bell's own tap no longer flips the master")
        self.assertIn("if(act==='all')", js)
        self.assertIn("post('/notify-all',{on:want})", js)
        self.assertIn("if(act==='dev')", js)
        self.assertIn("Notification.requestPermission():null", js)      # still inside the tap's own stack
        self.assertIn("devSubscribe(perm0):devUnsubscribe()", js)
        self.assertIn("if(act==='turns')", js)
        self.assertIn("post('/notify-turns',{on:wantT})", js)
        self.assertIn("if(act==='test')", js)
        # the test is addressed to the session in front (2026-09-06): the endpoint AND the active
        # tab's sid/host ride the POST, so its tap comes back to that session — plus the tab's label,
        # the fallback name when the kernel holds none for the id
        self.assertIn("post('/push/test',{endpoint:s.endpoint,sid:at.sid,host:at.host,label:at.label})", js)
        # the test button acknowledges at once and self-restores; the answer lands under it
        self.assertIn("testBtn.disabled=true", js)
        self.assertIn("testBtn.textContent='Sending…'", js)
        self.assertIn("testBtn.disabled=false;testBtn.textContent=label", js)
        self.assertIn("'The push service accepted it.'", js)
        self.assertIn("This device isn't subscribed yet.", js)
        self.assertIn("'The push service refused it: '+d.status", js)

    def test_the_glyph_reflects_this_device_and_the_tooltip_names_the_off_half(self):
        js = km._LANDING_PUSH_JS
        self.assertIn("var lit=isOn&&(canPush?devOn:true)", js)
        self.assertIn("b.classList.toggle('on',lit)", js)
        self.assertIn("'Notifications off for all devices'", js)
        self.assertIn("'Notifications off on this device'", js)
        self.assertIn("'Notifications off — for all devices, and on this device'", js)
        # blocked / unavailable push is SAID, in the row, with the way back
        self.assertIn("perm()==='denied'", js)
        self.assertIn("Notifications are blocked for this site.", js)
        self.assertIn("add romp to the Home Screen first", js)
        self.assertIn("el.classList.contains('off'))return", js, "a blocked row does not fire the request")

    def test_dismissal_and_repaint_wiring(self):
        h = self.html
        self.assertIn("window.__rompCloseBellPop=close", h)
        self.assertIn("if(bp&&!bp.hidden&&window.__rompCloseBellPop){window.__rompCloseBellPop();closed=true;}", h,
                      "Escape closes it through the shell's shared chain")
        self.assertIn("if(e.target===back)close()", h, "an outside tap lands on the backdrop and closes")
        self.assertIn("m.type==='notifyTurns'&&window.__rompNotifyTurnsPaint", h)
        self.assertIn("fetch('/notify-turns')", h)
        self.assertIn("z-index:205", h)

    def test_the_menu_tokens_with_their_dark_fallbacks(self):
        h = self.html
        # the shell defines the tokens (it loads no sheet) — dark byte-equal to styles.css's :root,
        # the light block in the light palette
        self.assertIn(":root{--menu-bg:#252526;--menu-fg:#cccccc;--menu-border:rgba(255,255,255,0.12);"
                      "--menu-hover:rgba(255,255,255,0.09);--radius-menu:6px;--shadow-menu:0 4px 12px rgba(0,0,0,0.35);"
                      "--check-bg:#1EA1EB}", h)
        self.assertIn("body.theme-light{--menu-bg:#FBF6EF;--menu-fg:#1F1E1D;--menu-border:rgba(0,0,0,0.12);"
                      "--menu-hover:rgba(0,0,0,0.06);--shadow-menu:0 4px 12px rgba(31,26,20,0.16);--check-bg:#C2410C}", h)
        pop = h[h.index("#rbell-pop{"):h.index("#rbp-test-out{")]
        self.assertIn("background:var(--menu-bg,#252526)", pop)
        self.assertIn("color:var(--menu-fg,#cccccc)", pop)
        self.assertIn("border:1px solid var(--menu-border,rgba(255,255,255,0.12))", pop)
        self.assertIn("border-radius:var(--radius-menu,6px)", pop)
        self.assertIn("box-shadow:var(--shadow-menu,0 4px 12px rgba(0,0,0,0.35))", pop)
        self.assertIn("background:var(--menu-hover,rgba(255,255,255,0.09))", pop)
        self.assertIn("font:12px/1.45 'Inter'", pop)
        self.assertIn(".rbp-sub{font-size:0.82em;opacity:.6}", pop)
        # no raw dark literal outside a var() fallback slot
        import re
        bare = re.sub(r"var\((--[\w-]+)\s*,\s*(?:[^()]|\([^()]*\))*\)", r"var(\1)", pop)
        for lit in ("#252526", "#cccccc", "rgba(255,255,255", "rgba(0,0,0,0.35)", "#1EA1EB"):
            self.assertNotIn(lit, bare, lit)
        self.assertIn("background:var(--accent)", pop, "the on-state pill is accent chrome")


# The bell's script, EXECUTED (the test_error_center.py pattern): node runs _LANDING_PUSH_JS against a
# hand-rolled DOM — the popover's nodes, the bells, and a chat iframe whose document is swapped per
# scenario — and presses the test button on each shape the phone can present. Pins what the press
# ATTACHES and what it SAYS, not the words of the popover: the active tab's id when the strip has one;
# none — filed with why — when the strip is still the boot window's placeholders (none active yet:
# the mobile chip shows the first tab then, its own fallback, and the test must not guess that one),
# when the chat frame has no document yet, no frame, or no strip; and the result line then says so.
_PUSH_HARNESS = r"""
'use strict';
const DIAG = [], FETCHES = [], NOTES = [];
function node(attrs) {
  attrs = Object.assign({}, attrs || {});
  const n = { hidden: false, disabled: false, textContent: '', className: '', style: {}, _h: {}, _cls: new Set(), parentNode: null,
    classList: { toggle: (c, on) => { if (on === undefined) on = !n._cls.has(c); if (on) n._cls.add(c); else n._cls.delete(c); },
                 add: (c) => n._cls.add(c), remove: (c) => n._cls.delete(c), contains: (c) => n._cls.has(c) },
    setAttribute: (k, v) => { attrs[k] = String(v); }, getAttribute: (k) => (k in attrs ? attrs[k] : null),
    querySelector: (sel) => (n._q && sel in n._q ? n._q[sel] : null),
    querySelectorAll: (sel) => (n._qa && n._qa[sel]) || [],
    addEventListener: (k, f) => { n._h[k] = f; },
    getBoundingClientRect: () => ({ top: 700, right: 380 }) };
  return n;
}
const bell = node(), back = node(), pop = node(), devSub = node(), testBtn = node({ 'data-act': 'test' }), testOut = node();
testBtn.textContent = 'Send a test notification'; testBtn.parentNode = pop;
pop._q = { '[data-act=all]': node(), '[data-act=dev]': node(), '[data-act=turns]': node() };
const frame = { contentDocument: null };      // the chat iframe: its document is the scenario
let hasFrame = true;
global.window = global;
global.innerHeight = 800; global.innerWidth = 390;
global.document = {
  querySelectorAll: (sel) => (sel === '#mbell,#rail-bell' ? [bell] : []),
  getElementById: (id) => ({ 'rbell-back': back, 'rbell-pop': pop, 'rbp-dev-sub': devSub, 'rbp-test': testBtn,
                             'rbp-test-out': testOut, 'f-chat': hasFrame ? frame : null })[id] || null,
};
global.Notification = { permission: 'granted', requestPermission: () => Promise.resolve('granted') };
global.PushManager = function () {};
const SUB = { endpoint: 'https://push.example.net/send/dev-1' };
Object.defineProperty(global, 'navigator', { configurable: true,
  value: { serviceWorker: { getRegistration: () => Promise.resolve({ pushManager: { getSubscription: () => Promise.resolve(SUB) } }) } } });
global.fetch = (path, init) => {
  if (init && init.method === 'POST') {
    const b = JSON.parse(init.body); FETCHES.push([path, b]);
    const res = { ok: true, status: 201, detail: 'Created' };
    if (b.sid) { res.sid = b.sid; res.name = b.sid.indexOf(':') > 0 ? b.sid.split(':')[0] + ':api' : 'web'; }   // the kernel names every sid it is handed
    return Promise.resolve({ ok: true, json: () => Promise.resolve(res) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ on: true }) });   // /notify-all, /notify-turns: the master on, so no master-off sentence rides the result
};
global.__rompShellDiag = (what, data) => DIAG.push([what, data]);
global.__rompNotify = (kind, text) => NOTES.push([kind, text]);
"""
_PUSH_DRIVER = r"""
const tick = () => new Promise((r) => setTimeout(r, 0));
// a chat document whose strip holds `ids`, with the tab at `active` active (-1: none — the boot window)
function chatDoc(ids, active, label) {
  const tabs = ids.map((id, i) => { const t = node({ 'data-id': id });
    const lab = node(); lab.textContent = label || ('  s' + i + '  '); t._q = { '.tab-label': lab }; return t; });
  const strip = node(); strip._qa = { '.tab[data-id]': tabs }; strip._q = { '.tab.active[data-id]': active >= 0 ? tabs[active] : null };
  return { querySelector: (sel) => (sel === '#tabs' ? strip : null) };
}
async function press() {
  DIAG.length = 0; FETCHES.length = 0; NOTES.length = 0; testOut.textContent = ''; testOut._cls.clear();
  pop._h.click({ target: testBtn });
  const pressed = { disabled: testBtn.disabled, label: testBtn.textContent, diag: DIAG.slice(), fetches: FETCHES.length };
  await tick(); await tick();
  return { pressed, fetches: FETCHES.slice(), out: testOut.textContent, bad: testOut._cls.has('bad'), notes: NOTES.slice(),
           restored: { disabled: testBtn.disabled, label: testBtn.textContent } };
}
(async () => {
  await tick();                                                  // the boot fetches settle (master on, device subscribed)
  const out = {};
  frame.contentDocument = chatDoc(['S1', 'S2'], -1);            // the phone's boot window: two placeholders, none active yet
  out.noneActive = await press();
  frame.contentDocument = chatDoc(['S1', 'boxa:S2'], 1, ' boxa:api ');   // a federated tab in front, its label padded the way text nodes are
  out.active = await press();
  frame.contentDocument = chatDoc(['S1'], 0);
  out.local = await press();
  frame.contentDocument = null;                                  // a chat frame mid-load: no document yet
  out.noDoc = await press();
  hasFrame = false;
  out.noFrame = await press();
  hasFrame = true;
  frame.contentDocument = { querySelector: () => null };         // a chat page without a strip at all
  out.noTabs = await press();
  frame.contentDocument = { querySelector: () => { throw new Error('cross-origin'); } };   // a document the shell may not read
  out.threw = await press();
  console.log(JSON.stringify(out));
})();
"""


class LandingPushExecutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import subprocess
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_PUSH_HARNESS + km._LANDING_PUSH_JS + _PUSH_DRIVER)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the bell's script threw: " + r.stderr[:800]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    EP = "https://push.example.net/send/dev-1"

    def test_the_boot_windows_strip_attaches_no_session_and_the_line_says_so(self):
        # the phone (2026-09-08): the strip holds the kernel's placeholders before the first session payload
        # names an active one; the mobile chip shows the FIRST tab then (its own fallback), and the test must
        # attach none rather than guess that one — filed as a row with the why, said in the result line
        r = self.out["noneActive"]
        self.assertEqual(r["pressed"]["diag"], [["push-test", {"sidAttached": False, "host": "", "why": "none-active", "tabs": 2}]])
        self.assertEqual(r["pressed"]["fetches"], 0, "the row is filed at the press, before the subscription lookup")
        self.assertEqual(r["fetches"], [["/push/test", {"endpoint": self.EP, "sid": "", "host": "", "label": ""}]])
        self.assertEqual(r["out"], "The push service accepted it. No session was attached — the tap will only bring romp forward.")
        self.assertFalse(r["bad"])

    def test_the_session_in_front_is_attached_and_named(self):
        r = self.out["active"]
        self.assertEqual(r["pressed"]["diag"], [["push-test", {"sidAttached": True, "host": "boxa", "why": "", "tabs": 2}]])
        self.assertEqual(r["fetches"], [["/push/test", {"endpoint": self.EP, "sid": "boxa:S2", "host": "boxa", "label": "boxa:api"}]])
        self.assertEqual(r["out"], "The push service accepted it. Tapping it brings you back to boxa:api.")
        r = self.out["local"]
        self.assertEqual(r["pressed"]["diag"], [["push-test", {"sidAttached": True, "host": "", "why": "", "tabs": 1}]])
        self.assertEqual(r["fetches"][0][1]["sid"], "S1")
        self.assertEqual(r["out"], "The push service accepted it. Tapping it brings you back to web.")
        for k in ("noneActive", "active", "local"):
            self.assertNotIn("sid", self.out[k]["pressed"]["diag"][0][1], "the row is structure only: attached or not, never which")

    def test_every_other_empty_read_names_its_reason(self):
        # a frame mid-load (no document yet), no frame, a page without a strip, a document the shell may not
        # read: each attaches nothing, files its reason, and the line says no session was attached
        for key, why in (("noDoc", "no-doc"), ("noFrame", "no-frame"), ("noTabs", "no-tabs"), ("threw", "threw")):
            r = self.out[key]
            self.assertEqual(r["pressed"]["diag"], [["push-test", {"sidAttached": False, "host": "", "why": why, "tabs": 0}]], key)
            self.assertEqual(r["fetches"][0][1]["sid"], "", key)
            self.assertIn("No session was attached", r["out"], key)
            self.assertFalse(r["bad"], key)

    def test_the_button_acknowledges_and_restores(self):
        for key in ("noneActive", "active", "noFrame"):
            r = self.out[key]
            self.assertEqual((r["pressed"]["disabled"], r["pressed"]["label"]), (True, "Sending…"), key)
            self.assertEqual(r["restored"], {"disabled": False, "label": "Send a test notification"}, key)
            self.assertEqual(r["notes"], [], key)


if __name__ == "__main__":
    unittest.main()
