#!/usr/bin/env python3
"""Web Push for the bell events (plans/ios-app.md proposal 2).

Covers the four layers separately, so a failure names its layer:

  * routes — /sw.js and /push/vapid-key are token-gated (they serve to the authed shell only);
    POST /push/subscribe validates, stores at 0600, and refuses loudly when the crypto
    dependency is missing; /push/unsubscribe prunes.
  * the worker — push + notificationclick ONLY (executed under node, every payload shape and every tap road). A fetch handler would fight the stale-bundle
    machinery (?v= cache-bust + the rstale banner), which assumes the network serves every load.
  * crypto — RFC 8291 aes128gcm round-trip: encrypt with the kernel's writer, decrypt with an
    independent receiver-side derivation from a browser keypair minted HERE, at run time (no
    credential-shaped literals in fixtures — repo rule). RFC 8292 VAPID: parse the header, verify
    the ES256 signature against the advertised key, check the claims.
  * the sink — _push_notify mirrors (title, body) to every subscription, sends the card gist and
    NOTHING more, prunes on the dead-subscription signal, and stands down silently when no
    device ever subscribed.

  * the ledger (2026-09-09) — every session-addressed push files a row per device with an unguessable pid
    the payload carries (in its routing block and its deep link); the worker acks 'shown' and 'clicked' by
    pid alone (POST /push/ack, no token: a worker's fetch carries none), the page settles the row it lands
    (POST /push/landed) and reads every unsettled row for its device (GET /push/pending); rows are read
    from disk on every op, so a restart loses nothing.
  * the declarative message (2026-09-10) — an Apple endpoint is sent Declarative Web Push, the tap as the
    OS's own callback for a KILLED app: iOS navigates the app to `navigate`, and the page lands the deep
    link at boot and on pageshow/popstate. The worker acks the `push` event the mutable message dispatches
    and shows nothing. The worker and the page infer nothing a tap did not say, pinned on their code lines here.
  * the vanished notification (2026-09-10) — a LIVE app gets no event from iOS, only a foregrounding, so the
    page holds the shown rows against registration.getNotifications():
    exactly one gone lands via 'vanish', silently; two or more gone are settled without landing
    (/push/dropped); a newer same-session notification on the screen supersedes an older row
    (/push/superseded, also at the kernel on the shown ack). A swipe-dismiss reads the same as a tap —
    the user accepted that on 2026-09-10 in exchange for background taps working.

The cryptography package is required here (CI installs it; the kernel treats it as a soft
dependency and fails loudly without it — test_subscribe_without_crypto_is_a_loud_500).
"""
import io
import json
import os
import time
import threading
import unittest
from unittest import mock
from romp_load import load_source
import tempfile

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import serialization
    HAVE_CRYPTO = True
except ImportError:                                   # pragma: no cover — CI installs it
    HAVE_CRYPTO = False

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
# Belt over conftest's suspenders. Under pytest, conftest.py rebinds XDG_STATE_HOME to a tempdir
# before any test module imports — but a RAW `python3 tests/test_kernel_webpush.py` skips conftest,
# and this file DELETES push state in _clear_push_state: on 2026-08-08 a raw run aimed that at the
# LIVE store, wiping the maintainer's phone subscription and rotating the real VAPID key (which
# orphans every subscription bound to it). Rebind the state root here, unconditionally, BEFORE the
# kernel module loads and captures jd.STATE into its path constants.
from pathlib import Path
_STATE_TD = tempfile.TemporaryDirectory()
jd.STATE = Path(_STATE_TD.name)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_webpush", os.path.join(BIN, "romp-kernel"))


def _b64u(b):
    import base64
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _mint_browser_keys():
    """What a real subscription carries, minted fresh per test run: a P-256 keypair (p256dh) and
    a 16-byte auth secret. Assembled at run time on purpose — a longhand fake key in a fixture
    would trip the very secret scanner that guards this repo."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962,
                                         serialization.PublicFormat.UncompressedPoint)
    return priv, _b64u(pub), _b64u(os.urandom(16))


def _serve_get(path, headers=None):
    """The real do_GET over a fake socket (the auth-hardening harness): (status, body_bytes)."""
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


def _clear_push_state():
    for name in ("push-subscriptions.json", "push-vapid.json", "push-ledger.json"):
        try:
            (jd.STATE / name).unlink()
        except OSError:
            pass




class ServiceWorkerRoute(unittest.TestCase):
    def test_sw_is_gated_and_push_only(self):
        status, _ = _serve_get("/sw.js")
        self.assertEqual(status, 403, "the worker serves to the authed shell only")
        status, body = _serve_get("/sw.js", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 200)
        js = body.decode()
        self.assertIn("addEventListener('push'", js)
        self.assertIn("addEventListener('notificationclick'", js)
        # NO fetch handler, ever: a caching worker would fight the stale-bundle detection,
        # which assumes the network serves every load (plans/ios-app.md)
        self.assertNotIn("'fetch'", js)
        self.assertNotIn("respondWith", js)
        # the ONE outbound fetch is the kernel ack (2026-09-09): POST /push/ack by pid — a call the worker MAKES,
        # never a request it intercepts; nothing else in the worker fetches
        self.assertEqual(js.count("fetch("), js.count("fetch('/push/ack'"), "no fetch but the ack")
        self.assertGreater(js.count("fetch('/push/ack'"), 0)
        # an UPDATED worker must take over immediately — this one owns no caches, so 'waiting'
        # only delays fixes (the sid-blind predecessor kept handling taps, the user 2026-08-08)
        self.assertIn("skipWaiting()", js)
        self.assertIn("clients.claim()", js)
        self.assertNotIn("setTimeout", js, "event-based end to end — no timers in the worker")

    def test_the_worker_infers_nothing_a_tap_did_not_say(self):
        # 2026-09-10, the user: no guessing. The worker acks what it SAW and lands the click it GOT — nothing kept
        # between events, nothing written for a page to find, no screen read, no close acked, no update asked. Pinned
        # on the code lines (the prose may name what the worker does not do)
        code = "\n".join(l for l in km._SW_JS.splitlines() if not l.lstrip().startswith("//"))   # the prose may name what went
        for word in ("caches", "/__romp/", "tapReplay", "tapLanded", "getNotifications", "notificationclose", "'closed'",
                     "registration.update", "addEventListener('message'", "pending=", "stamp(", "keep(", "mint("):
            self.assertNotIn(word, code, word)
        self.assertNotIn(".navigate(", km._SW_JS, "no reload road either: the worker never navigates a page")

    def test_sw_carries_this_builds_version_string(self):
        # the served worker bakes the kernel's build string into SWV and sends it in every ack (`v`), so the ledger
        # row says WHICH worker build acked: it costs a string, and the device trail reads it. The raw source keeps the
        # placeholder inside a string literal, so the node harness runs it unbaked; the string is sha + dist token, so
        # a deploy and a bundle rebuild both move it
        _, body = _serve_get("/sw.js", headers={"X-Romp-Token": km.TOKEN})
        js = body.decode()
        v = km._sw_version()
        self.assertTrue(v)
        self.assertRegex(v, r"^[A-Za-z0-9._+-]+$", "safe inside a JS string literal")
        self.assertIn(km._kernel_sha() or "nogit", v)
        self.assertIn(str(km._dist_ver()), v)
        self.assertIn("SWV='%s'" % v, js)
        self.assertNotIn("__ROMP_SWV__", js)
        self.assertIn("SWV='__ROMP_SWV__'", km._SW_JS, "the raw source is valid JS with the placeholder in a literal")
        self.assertNotIn("__ROMP_SWV__", km._landing(), "the string is the worker's alone: the page compares no builds")

    def test_sw_click_lands_on_the_session_that_fired(self):
        # the user 2026-08-08: the first real push opened the app on a DIFFERENT session. The
        # kernel's routing block rides the notification's data; a live window gets it over
        # postMessage, a cold start gets the kernel's deep link (ServiceWorkerExecutes runs it).
        _, body = _serve_get("/sw.js", headers={"X-Romp-Token": km.TOKEN})
        js = body.decode()
        self.assertIn("data:(n.data&&typeof n.data==='object')?n.data:{sid:d.sid||''}", js,
                      "the routing block verbatim; an older kernel's flat sid still lands")
        self.assertIn("if(n.tag){opts.tag=n.tag;opts.renotify=!quiet;}", js, "one notification per session, still audible unless it is the quiet card push that yields the buzz")
        self.assertIn("if(quiet)opts.silent=true", js, "a quiet push carries the badge without re-alerting")
        self.assertIn("romp:'notificationClick'", js)
        self.assertIn("clients.openWindow(url)", js)
        self.assertIn("/?push-reveal=", js)              # the fallback deep link for a data block without one
        # ...and the closed-app badge count comes from the payload — either shape's word for it
        self.assertIn("setAppBadge", js)
        self.assertIn("typeof d.app_badge==='number'", js)

    def test_the_declarative_push_is_acked_and_never_shown_twice(self):
        # 2026-09-10: Safari 18.4+ hands the worker the notification it parsed itself (e.notification, a `push` event
        # per the W3C draft and WebKit's ServiceWorkerThread — the browser displays it and NAVIGATES on a tap). The
        # worker acks 'shown' by the pid in its data and shows nothing: feature-detected on the event, never a UA sniff
        js = km._SW_JS
        self.assertIn("if(e.notification){", js)
        self.assertLess(js.index("if(e.notification){"), js.index("e.data?e.data.json()"), "the declarative branch is read first")
        self.assertIn("d.web_push===8030&&d.notification", js, "…and the declarative JSON reaching a browser that does not parse it is read off its notification block")
        self.assertNotIn("navigator.userAgent", js)
        self.assertNotIn("pushnotification", js, "the explainer's early event name never shipped: WebKit dispatches `push`")


# The worker, EXECUTED (the test_error_center.py pattern): node runs _SW_JS against stubs of the
# ServiceWorker globals and the driver replays pushes of every shape and taps of every road, logging every
# call in order. A source pin says the words are there; this says the sequence is right: ack → show for a
# push; ack → close → matchAll → focus + postMessage for a tap, openWindow only when no window exists or
# focus() refused, all under waitUntil.
_SW_HARNESS = r"""
'use strict';
const H = {};            // event name -> the worker's handler
const LOG = [];          // every call the worker makes, in order
const META = [];         // per posted message: the worker's diag block and the pid (2026-09-08/09), kept apart
                         // from LOG so the routing block still compares whole
function strip(m) { if (!m || typeof m !== 'object') return m; const c = Object.assign({}, m);
  META.push({ diag: c.diag, pid: c.pid }); delete c.diag; delete c.pid; return c; }
// the kernel ack: every fetch the worker makes, with its body and how far LOG had got — so a test can say the ack
// was started BEFORE the show (push) and before the close (click)
const FLOG = [];
global.fetch = (path, init) => { FLOG.push([path, init && init.body ? JSON.parse(init.body) : null, LOG.length, !!(init && init.keepalive), (init && init.method) || 'GET']);
  return Promise.resolve({ ok: true, status: 200 }); };
global.self = {
  addEventListener: (k, f) => { H[k] = f; },
  skipWaiting: () => {},
  registration: { showNotification: (title, opts) => { LOG.push(['show', title, opts]); return Promise.resolve(); } },
  navigator: {},         // no setAppBadge here: the badge has its own pin below
};
global.clients = {
  claim: () => Promise.resolve(),
  matchAll: (q) => { LOG.push(['matchAll', q]); return Promise.resolve([]); },
  openWindow: (u) => { LOG.push(['openWindow', u]); return Promise.resolve({}); },
};
"""
_SW_DRIVER = r"""
function win(focusOk) {
  const w = { frameType: 'top-level',
              focus: () => { LOG.push(['focus']); return focusOk ? Promise.resolve(w) : Promise.reject(new Error('refused')); },
              postMessage: (m) => LOG.push(['post', strip(m)]) };
  return w;
}
// a TAGGED client, for the dashboard's shape: the shell (top-level) plus its same-origin pane iframes,
// which the browser lists as window clients too (frameType 'nested'), most-recently-focused first
function frame(tag, frameType) {
  const w = { frameType,
              focus: () => { LOG.push(['focus', tag]); return Promise.resolve(w); },
              postMessage: (m) => LOG.push(['post', tag, m && strip(m)]) };
  return w;
}
// a top-level client with the state a real WindowClient reports: how visible it is after focus(), and a navigate
// method that LOGS if the worker ever calls it (the worker never navigates a page)
function stateful(o) {
  const w = { frameType: 'top-level', visibilityState: o.vis, url: o.url,
              focus: () => { LOG.push(['focus']); return Promise.resolve(w); },
              postMessage: (m) => LOG.push(['post', strip(m)]) };
  w.navigate = (u) => { LOG.push(['navigate', u]); return Promise.resolve(w); };
  return w;
}
async function push(payload, notification) {
  LOG.length = 0; FLOG.length = 0;
  const waited = [];
  const ev = { waitUntil: (p) => waited.push(p) };
  if (notification) ev.notification = notification;                        // the declarative event: a parsed Notification, no data
  else ev.data = { json: () => payload };
  H.push(ev);
  const sync = { log: LOG.map((x) => x[0]), fetches: FLOG.slice() };       // before the handler yields
  const outcomes = (await Promise.allSettled(waited)).map((s) => s.status);
  return { sync, log: LOG.slice(), fetches: FLOG.slice(), waited: waited.length, outcomes };
}
async function tap(data, windows) {
  LOG.length = 0; FLOG.length = 0; META.length = 0;
  const waited = [];
  global.clients.matchAll = (q) => { LOG.push(['matchAll', q]); return Promise.resolve(windows); };
  H.notificationclick({ notification: { close: () => LOG.push(['close']), data }, waitUntil: (p) => waited.push(p) });
  for (const p of waited) await p;
  return { log: LOG.slice(), fetches: FLOG.slice(), waited: waited.length, meta: META[META.length - 1] || null };
}
(async () => {
  const out = {};
  const url = '/?push-reveal=S1&push-card=S1%3Ag1&push-pid=PID-test-000000001';
  const data = { sid: 'S1', host: '', kind: 'card', cardId: 'S1:g1', url, name: 'web', pid: 'PID-test-000000001' };
  // THE IMPERATIVE SHAPE (FCM, Mozilla): the kernel's payload as _push_payload builds it
  out.imperative = await push({ title: 'romp: web', body: 'Needs you: x', sid: 'S1', tag: 'romp:S1', badge: 2, data });
  out.quiet = await push({ title: 'romp: web', body: 'Needs you: x', sid: 'S1', tag: 'romp:S1', badge: 2, quiet: true, data });
  out.legacy = await push({ title: 't', body: 'b', sid: 'S9' });                                   // an older kernel's flat payload: no data, no tag, no pid
  out.sidless = await push({ title: 'romp', body: 'Test notification', tag: 'romp:test', data: { sid: '', host: '', kind: 'test', cardId: '', url: '/', name: '', pid: '' } });
  // THE DECLARATIVE JSON at a browser that does not parse it (Safari before 18.4): read off its notification block
  const decl = { web_push: 8030, notification: { title: 'romp: web', body: 'Needs you: x', navigate: 'https://romp.test' + url, tag: 'romp:S1', data }, mutable: true, app_badge: 2 };
  out.declarativeData = await push(decl);
  out.declarativeSilent = await push({ web_push: 8030, notification: { title: 'romp: web', body: 'Needs you: x', navigate: 'https://romp.test' + url, tag: 'romp:S1', silent: true, data }, mutable: true });
  // THE DECLARATIVE EVENT (Safari 18.4+): the browser parsed and will display it; the worker gets the Notification
  out.declarativeEvent = await push(null, { title: 'romp: web', body: 'Needs you: x', tag: 'romp:S1', data });
  out.declarativeEventNoPid = await push(null, { title: 'romp', body: 'Test notification', tag: 'romp:test', data: { sid: '', kind: 'test', pid: '' } });
  out.declarativeEventNoData = await push(null, { title: 'romp', body: 'b' });
  // the badge: painted only where the SW can, and only from a number — either shape's word for it
  global.self.navigator.setAppBadge = (n) => { LOG.push(['badge', n]); return Promise.resolve(); };
  out.badgeImperative = await push({ title: 't', body: 'b', sid: 'S1', tag: 'romp:S1', badge: 3, data });
  out.badgeDeclarative = await push(Object.assign({}, decl, { app_badge: 4 }));
  out.badgeMirrored = await push({ title: 't', body: 'b', sid: 'boxa:S1', tag: 'romp:boxa:S1', data });   // a mirrored federated event: no badge, nothing repainted
  delete global.self.navigator.setAppBadge;
  // a show that FAILS (headless browsers refuse showNotification) still fails the push the way it always did, and the
  // ack went out first all the same
  const showOk = global.self.registration.showNotification;
  global.self.registration.showNotification = (t, o) => { LOG.push(['show', t, o]); return Promise.reject(new Error('denied')); };
  out.refusedShow = await push({ title: 'api', body: 'finished', sid: 'S2', tag: 'romp:S2', data: { sid: 'S2', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S2&push-pid=PID-test-000000002', name: 'api', pid: 'PID-test-000000002' } });
  global.self.registration.showNotification = showOk;
  // THE TAP, every road (a browser that dispatches notificationclick)
  out.live = await tap(data, [win(true)]);
  out.cold = await tap(data, []);
  out.refused = await tap(data, [win(false)]);
  out.test = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/', pid: '' }, []);
  const testSid = { sid: 'S5', host: '', kind: 'test', cardId: '', url: '/?push-reveal=S5&push-pid=PID-test-000000005', pid: 'PID-test-000000005' };
  out.testLive = await tap(testSid, [win(true)]);
  out.testCold = await tap(testSid, []);
  out.legacyTap = await tap({ sid: 'S7' }, []);            // a notification an older worker showed: flat sid, no url, no pid
  const fed = { sid: 'boxa:S8', host: 'boxa', kind: 'test', cardId: '', url: '/?push-reveal=boxa%3AS8', pid: '' };
  out.nested = await tap(fed, [frame('chat', 'nested'), frame('feed', 'nested'), frame('shell', 'top-level')]);
  out.nestedOnly = await tap(fed, [frame('chat', 'nested')]);   // a pane with no shell above it: nothing to post to
  out.untyped = await tap(fed, [frame('old', undefined)]);       // a browser that reports no frameType is a window
  const opened = { postMessage: (m) => LOG.push(['post', 'opened', strip(m)]) };
  const openWindow0 = global.clients.openWindow;
  global.clients.openWindow = (u) => { LOG.push(['openWindow', u]); return Promise.resolve(opened); };
  out.coldHanded = await tap(data, []);
  out.testHanded = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/', pid: '' }, []);   // nothing to land on: nothing posted
  out.refusedHanded = await tap(data, [win(false)]);               // focus refused, then the opened window is told
  global.clients.openWindow = (u) => { LOG.push(['openWindow', u]); return Promise.resolve(null); };
  out.coldNull = await tap(data, []);                              // no client back: the link alone, no throw
  global.clients.openWindow = openWindow0;
  out.hidden = await tap(data, [stateful({ vis: 'hidden', url: 'https://romp.test/' })]);
  console.log(JSON.stringify(out));
})();
"""


class ServiceWorkerExecutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import subprocess, tempfile as _tf
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_SW_HARNESS + km._SW_JS + _SW_DRIVER)
            path = f.name
        try:
            r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        assert r.returncode == 0, "the worker threw: " + r.stderr[:800]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    MATCH = ["matchAll", {"type": "window", "includeUncontrolled": True}]
    URL = "/?push-reveal=S1&push-card=S1%3Ag1&push-pid=PID-test-000000001"
    DATA = {"sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1", "url": URL, "name": "web", "pid": "PID-test-000000001"}
    MSG = {"romp": "notificationClick", "sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1"}
    SHOWN = ["/push/ack", {"pid": "PID-test-000000001", "stage": "shown", "v": "__ROMP_SWV__"}, 0, True, "POST"]

    def test_the_imperative_push_shows_the_gist_and_keeps_the_routing_block_verbatim(self):
        p = self.out["imperative"]
        show = p["log"][0]
        self.assertEqual(show[:2], ["show", "romp: web"])
        opts = show[2]
        self.assertEqual(opts["body"], "Needs you: x")
        self.assertEqual(opts["data"], self.DATA)
        self.assertEqual((opts["tag"], opts["renotify"]), ("romp:S1", True), "same session → replaces, still buzzes")
        self.assertNotIn("silent", opts)
        self.assertEqual(p["waited"], 1)
        self.assertEqual(p["outcomes"], ["fulfilled"])
        q = self.out["quiet"]["log"][0][2]
        self.assertEqual((q["renotify"], q["silent"]), (False, True), "the quiet card push replaces without re-alerting")
        # an older kernel's flat payload still lands on its sid, and wears no tag it did not send
        legacy = self.out["legacy"]["log"][0][2]
        self.assertEqual(legacy["data"], {"sid": "S9"})
        self.assertNotIn("tag", legacy)
        self.assertEqual(self.out["legacy"]["fetches"], [], "no pid, nothing to ack")
        self.assertEqual(self.out["sidless"]["fetches"], [], "a sid-less probe has no row")
        self.assertEqual(self.out["sidless"]["log"][0][:2], ["show", "romp"])

    def test_the_shown_ack_is_started_before_the_show_and_the_show_is_still_synchronous(self):
        # 2026-09-09: the kernel is the meeting point — the push carries a pid the kernel issued for THIS device, and
        # the worker tells the kernel it was shown, by that pid alone (a worker's fetch carries no token), keepalive
        # so a worker the platform ends early still gets it out, BEFORE the show is attempted
        p = self.out["imperative"]
        self.assertEqual(p["sync"]["fetches"], [self.SHOWN], "the ack is on its way with LOG still empty (before the show)")
        self.assertEqual(p["sync"]["log"], ["show"], "…and the show is the synchronous act of the handler")
        self.assertEqual(len(p["fetches"]), 1, "one ack per push")
        self.assertEqual(p["log"], [p["log"][0]], "nothing after the show: the handler shows and acks, and that is all")
        r = self.out["refusedShow"]
        self.assertEqual(r["outcomes"], ["rejected"], "a show that fails still fails the push the way it always did")
        self.assertEqual(r["fetches"][0][1]["stage"], "shown", "…and the ack went out first all the same")

    def test_the_declarative_json_at_a_browser_that_does_not_parse_it_is_shown_from_its_notification_block(self):
        # Safari before 18.4 on an Apple endpoint, or any browser handed the declarative JSON as e.data: the worker
        # shows title/body/tag/data off `notification`, acks by the pid in its data, reads the badge off app_badge
        d = self.out["declarativeData"]
        show = d["log"][0]
        self.assertEqual(show[:2], ["show", "romp: web"])
        self.assertEqual((show[2]["body"], show[2]["tag"], show[2]["renotify"], show[2]["data"]), ("Needs you: x", "romp:S1", True, self.DATA))
        self.assertEqual(d["fetches"], [self.SHOWN])
        s = self.out["declarativeSilent"]["log"][0][2]
        self.assertEqual((s["renotify"], s["silent"]), (False, True), "the declarative `silent` is the imperative `quiet`")

    def test_the_declarative_event_is_acked_and_nothing_is_shown(self):
        # Safari 18.4+ (the W3C draft, WebKit's ServiceWorkerThread): the browser parsed the message, will display the
        # notification itself and navigate on a tap; the mutable message hands the worker the Notification as
        # e.notification. The worker acks 'shown' by the pid in its data and shows NOTHING — a showNotification here
        # would replace what the system is displaying
        e = self.out["declarativeEvent"]
        self.assertEqual(e["log"], [], "nothing shown, nothing else called")
        self.assertEqual(e["fetches"], [self.SHOWN])
        self.assertEqual((e["waited"], e["outcomes"]), (1, ["fulfilled"]), "the ack rides the event's waitUntil")
        for k in ("declarativeEventNoPid", "declarativeEventNoData"):
            self.assertEqual((self.out[k]["log"], self.out[k]["fetches"], self.out[k]["waited"]), ([], [], 1), k + ": no pid, nothing to say, no throw")

    def test_the_badge_is_painted_from_either_shapes_number_and_never_from_its_absence(self):
        self.assertIn(["badge", 3], self.out["badgeImperative"]["log"])
        self.assertIn(["badge", 4], self.out["badgeDeclarative"]["log"], "app_badge is the declarative word for the count")
        self.assertEqual([x for x in self.out["badgeMirrored"]["log"] if x[0] == "badge"], [], "a mirrored event omits the badge: the origin's count is not ours, so nothing is repainted")

    def test_a_tap_acks_clicked_first_then_focuses_and_tells_the_live_window(self):
        live = self.out["live"]
        self.assertEqual(live["waited"], 1, "the whole tap rides one waitUntil")
        self.assertEqual(live["log"], [["close"], self.MATCH, ["focus"], ["post", self.MSG]])
        self.assertEqual(live["fetches"], [["/push/ack", {"pid": "PID-test-000000001", "stage": "clicked", "v": "__ROMP_SWV__"}, 0, True, "POST"]],
                         "the clicked ack is the FIRST thing the click handler does: before the close is logged")
        self.assertEqual(live["meta"]["pid"], "PID-test-000000001", "the message carries the pid, so the page settles the row and lands one push once")
        self.assertEqual(live["meta"]["diag"], {"clients": 1, "tops": 1, "road": "focus", "vis": ""})
        self.assertNotIn("id", self.out["live"]["log"][3][1], "no per-tap id: the pid is the dedupe key")

    def test_no_window_opens_the_deep_link_which_carries_the_pid(self):
        self.assertEqual(self.out["cold"]["log"], [["close"], self.MATCH, ["openWindow", self.URL]])
        self.assertEqual(self.out["cold"]["waited"], 1)
        self.assertEqual(self.out["cold"]["fetches"][0][1]["stage"], "clicked", "acked first on this road too")
        # the window openWindow hands back is ALSO given the routing block (2026-09-08): a message to a window whose page has
        # no listener yet is held by the browser until the shell adds one — the second road for a browser that opens the
        # app on its start URL; the page lands the pid once whichever arrives
        self.assertEqual(self.out["coldHanded"]["log"], [["close"], self.MATCH, ["openWindow", self.URL], ["post", "opened", self.MSG]])
        self.assertEqual(self.out["coldNull"]["log"], [["close"], self.MATCH, ["openWindow", self.URL]])
        self.assertEqual(self.out["testHanded"]["log"], [["close"], self.MATCH, ["openWindow", "/"]])

    def test_a_refused_focus_falls_through_to_open_window(self):
        # the installed-app case: focus() rejects — the tap must still land somewhere
        self.assertEqual(self.out["refused"]["log"], [["close"], self.MATCH, ["focus"], ["openWindow", self.URL]])
        self.assertEqual(self.out["refusedHanded"]["log"], [["close"], self.MATCH, ["focus"], ["openWindow", self.URL], ["post", "opened", self.MSG]])
        self.assertEqual(self.out["refusedHanded"]["meta"]["diag"]["road"], "open-after-refused")

    def test_a_test_notification_lands_on_its_session_or_just_opens_romp(self):
        # no session in front when the button was pressed: the tap can only open or focus romp, and nothing is acked
        self.assertEqual(self.out["test"]["log"], [["close"], self.MATCH, ["openWindow", "/"]])
        self.assertEqual(self.out["test"]["fetches"], [])
        # the user 2026-09-06: a test carries the session the button was pressed on. The worker does not branch on kind
        msg = {"romp": "notificationClick", "sid": "S5", "host": "", "kind": "test", "cardId": ""}
        self.assertEqual(self.out["testLive"]["log"], [["close"], self.MATCH, ["focus"], ["post", msg]])
        self.assertEqual(self.out["testCold"]["log"], [["close"], self.MATCH, ["openWindow", "/?push-reveal=S5&push-pid=PID-test-000000005"]])
        self.assertEqual(self.out["legacyTap"]["log"][-1], ["openWindow", "/?push-reveal=S7"], "a notification from the previous worker still lands")
        self.assertEqual(self.out["legacyTap"]["fetches"], [])

    def test_the_tap_is_posted_to_the_shell_never_into_a_pane_iframe(self):
        # the user 2026-09-06, on the phone: matchAll lists the dashboard's same-origin pane iframes as window clients
        # too, most-recently-focused first — the chat pane the user had just switched sessions in was first. Only the
        # top-level shell listens for the worker's message; posting into the pane dropped the tap on the floor
        msg = {"romp": "notificationClick", "sid": "boxa:S8", "host": "boxa", "kind": "test", "cardId": ""}
        self.assertEqual(self.out["nested"]["log"], [["close"], self.MATCH, ["focus", "shell"], ["post", "shell", msg]])
        self.assertEqual(self.out["nested"]["meta"]["diag"], {"clients": 3, "tops": 1, "road": "focus", "vis": ""})
        self.assertEqual(self.out["nestedOnly"]["log"], [["close"], self.MATCH, ["openWindow", "/?push-reveal=boxa%3AS8"]])
        self.assertEqual(self.out["untyped"]["log"][-1], ["post", "old", msg])

    def test_a_hidden_top_level_client_is_told_like_any_other_and_never_navigated(self):
        # review find (2026-09-09, on #1127): visibilityState is not a liveness test, and no road of the worker's loads
        # a page; what the worker saw still rides the trail
        self.assertEqual(self.out["hidden"]["log"], [["close"], self.MATCH, ["focus"], ["post", self.MSG]])
        self.assertEqual(self.out["hidden"]["meta"]["diag"]["vis"], "hidden")


@unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
class VapidKeys(unittest.TestCase):
    def setUp(self):
        _clear_push_state()

    def test_key_route_is_gated_and_stable(self):
        status, _ = _serve_get("/push/vapid-key")
        self.assertEqual(status, 403)
        status, body = _serve_get("/push/vapid-key", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 200)
        k1 = json.loads(body.decode())["key"]
        import base64
        raw = base64.urlsafe_b64decode(k1 + "=" * (-len(k1) % 4))
        self.assertEqual((len(raw), raw[0]), (65, 0x04), "uncompressed P-256 point")
        # stable across calls: a subscription is bound to the key it was minted with
        _, body2 = _serve_get("/push/vapid-key", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(json.loads(body2.decode())["key"], k1)

    def test_private_key_is_0600(self):
        km._vapid_keys()
        mode = (jd.STATE / "push-vapid.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


@unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
class Rfc8291Encryption(unittest.TestCase):
    def test_round_trip_against_an_independent_receiver(self):
        # decrypt with the RECEIVER's half of RFC 8291, derived here from first principles —
        # ua private key + auth secret → same IKM → cek/nonce → AESGCM open
        ua_priv, p256dh, auth_b64 = _mint_browser_keys()
        payload = json.dumps({"title": "romp: web", "body": "Needs you: pick a migration"}).encode()
        blob = km._webpush_encrypt(payload, p256dh, auth_b64)

        salt, rs, idlen = blob[:16], int.from_bytes(blob[16:20], "big"), blob[20]
        self.assertEqual((rs, idlen), (4096, 65), "RFC 8188 header: rs=4096, keyid=an EC point")
        as_pub_raw, ct = blob[21:21 + idlen], blob[21 + idlen:]
        as_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub_raw)
        ua_pub_raw = ua_priv.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

        import base64
        auth = base64.urlsafe_b64decode(auth_b64 + "=" * (-len(auth_b64) % 4))
        hkdf = lambda s, ikm, info, n: HKDF(algorithm=hashes.SHA256(), length=n,
                                            salt=s, info=info).derive(ikm)
        ikm = hkdf(auth, ua_priv.exchange(ec.ECDH(), as_pub),
                   b"WebPush: info\x00" + ua_pub_raw + as_pub_raw, 32)
        cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, ct, None)
        self.assertEqual(plain[-1:], b"\x02", "last-record delimiter")
        self.assertEqual(plain[:-1], payload)

    def test_seams_make_it_deterministic(self):
        # same salt + same ephemeral key → same bytes; fresh defaults → different bytes (real
        # sends never reuse a salt/key pair)
        _, p256dh, auth = _mint_browser_keys()
        eph = ec.generate_private_key(ec.SECP256R1())
        salt = os.urandom(16)
        a = km._webpush_encrypt(b"x", p256dh, auth, _salt=salt, _eph=eph)
        b = km._webpush_encrypt(b"x", p256dh, auth, _salt=salt, _eph=eph)
        c = km._webpush_encrypt(b"x", p256dh, auth)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


@unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
class VapidAuth(unittest.TestCase):
    def setUp(self):
        _clear_push_state()

    def test_header_verifies_and_claims_the_push_origin(self):
        import base64
        hdr = km._vapid_auth("https://push.example.net/send/abc123")
        self.assertTrue(hdr.startswith("vapid t="))
        jwt, key = hdr[len("vapid t="):].split(", k=")
        h64, c64, s64 = jwt.split(".")
        dec = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        self.assertEqual(json.loads(dec(h64)), {"alg": "ES256", "typ": "JWT"})
        claims = json.loads(dec(c64))
        # audience is the push SERVICE's origin (Apple's/Google's relay), never the full endpoint
        self.assertEqual(claims["aud"], "https://push.example.net")
        self.assertGreater(claims["exp"], time.time())
        self.assertTrue(claims["sub"].startswith("mailto:"))
        # signature verifies against the key the header itself advertises (k=)
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), dec(key))
        sig = dec(s64)
        der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        pub.verify(der, ("%s.%s" % (h64, c64)).encode(), ec.ECDSA(hashes.SHA256()))  # raises on mismatch


class SubscribeRoutes(unittest.TestCase):
    """POST /push/subscribe|unsubscribe over the real handler on loopback (the ServeSecurity
    pattern — a fake socket cannot exercise Content-Length body reads)."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _clear_push_state()

    def _post(self, path, body, token=True):
        import urllib.request, urllib.error
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = km.TOKEN
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def _sub_body(self):
        if HAVE_CRYPTO:
            _, p256dh, auth = _mint_browser_keys()
        else:
            p256dh, auth = _b64u(b"\x04" + os.urandom(64)), _b64u(os.urandom(16))
        return {"endpoint": "https://push.example.net/send/dev-" + _b64u(os.urandom(6)),
                "keys": {"p256dh": p256dh, "auth": auth}}

    @unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
    def test_subscribe_stores_at_0600_and_unsubscribe_prunes(self):
        sub = self._sub_body()
        code, _ = self._post("/push/subscribe", sub)
        self.assertEqual(code, 200)
        f = jd.STATE / "push-subscriptions.json"
        self.assertEqual(f.stat().st_mode & 0o777, 0o600,
                         "endpoints are capability URLs — the store gets the token treatment")
        self.assertIn(sub["endpoint"], km._push_subs())
        # same device re-subscribing overwrites, never duplicates
        code, _ = self._post("/push/subscribe", sub)
        self.assertEqual((code, len(km._push_subs())), (200, 1))
        code, _ = self._post("/push/unsubscribe", {"endpoint": sub["endpoint"]})
        self.assertEqual((code, km._push_subs()), (200, {}))

    def test_subscribe_requires_the_token(self):
        code, _ = self._post("/push/subscribe", self._sub_body(), token=False)
        self.assertEqual(code, 403)

    def test_garbage_is_a_400_not_a_stored_row(self):
        for bad in ({}, {"endpoint": "http://not-https", "keys": {"p256dh": "x", "auth": "y"}},
                    {"endpoint": "https://push.example.net/x", "keys": {}}):
            code, _ = self._post("/push/subscribe", bad)
            self.assertEqual(code, 400, bad)
        self.assertEqual(km._push_subs(), {})

    def test_subscribe_without_crypto_is_a_loud_500(self):
        # the fail-loudly rule: a subscription the kernel can never deliver to must be REFUSED
        # with the missing package named, not stored and silently starved
        with mock.patch.object(km, "_PUSH_CRYPTO", [False]):
            code, body = self._post("/push/subscribe", self._sub_body())
        self.assertEqual(code, 500)
        self.assertIn("cryptography", body)
        self.assertEqual(km._push_subs(), {})

    @unittest.skipUnless(HAVE_CRYPTO, "python 'cryptography' not installed")
    def test_subscribe_records_the_pages_origin_for_the_declarative_navigate(self):
        # 2026-09-10: an Apple endpoint's declarative message needs an ABSOLUTE navigate URL (WebKit parses it with no
        # base), and the page is the authority on where it runs — so the bell posts location.origin with the
        # subscription, the kernel keeps it with the row, and the request's Origin header stands in for an older shell
        sub = dict(self._sub_body(), origin="https://TESTHOST.example:8443")
        code, _ = self._post("/push/subscribe", sub)
        self.assertEqual(code, 200)
        self.assertEqual(km._push_subs()[sub["endpoint"]]["origin"], "https://TESTHOST.example:8443")
        sub2 = self._sub_body()
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/push/subscribe" % self.port, method="POST", data=json.dumps(sub2).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN, "Origin": "http://127.0.0.1:29855"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(km._push_subs()[sub2["endpoint"]]["origin"], "http://127.0.0.1:29855", "the header, when the body carries none")
        code, _ = self._post("/push/subscribe", dict(self._sub_body(), origin="javascript:alert(1)"))
        self.assertEqual(code, 400, "an origin that is not a scheme://host is refused before it can become a navigate URL")
        code, _ = self._post("/push/subscribe", dict(self._sub_body(), origin="https://TESTHOST.example/path?x=1"))
        self.assertEqual(code, 400)
        self.assertIn("j.origin=String(location.origin||'')", km._LANDING_PUSH_JS, "the bell posts the page's own origin")


class PushSink(unittest.TestCase):
    def setUp(self):
        _clear_push_state()

    def test_wired_beside_system_notify(self):
        # the sink hangs off the SAME loop as _system_notify — the armed-bell diff on fresh feed
        # builds — so it inherits the transition-event detection and the silent first-build
        # baseline by construction, rather than re-deriving either
        import inspect
        src = inspect.getsource(km._cached_feed)
        self.assertIn("_system_notify(_t, _b)", src)
        self.assertIn('_push_notify(_t, _b, _sid, _badge, kind="card", card_id=_iid)', src)
        self.assertIn("_badge_push(_badge)", src)

    def test_no_subscriptions_means_no_work(self):
        with mock.patch.object(km, "_push_send_one") as send:
            km._push_notify("romp: web", "Needs you")
        send.assert_not_called()

    def test_delivers_gist_only_and_prunes_dead_endpoints(self):
        km._save_push_subs({
            "https://push.example.net/send/live": {
                "endpoint": "https://push.example.net/send/live",
                "keys": {"p256dh": "k", "auth": "a"}},
            "https://push.example.net/send/dead": {
                "endpoint": "https://push.example.net/send/dead",
                "keys": {"p256dh": "k", "auth": "a"}},
        })
        seen = {}
        done = threading.Event()

        def fake_send(sub, payload):
            seen[sub["endpoint"]] = payload
            if len(seen) == 2:
                done.set()
            return not sub["endpoint"].endswith("/dead")

        with mock.patch.object(km, "_push_send_one", side_effect=fake_send), \
             mock.patch.object(km, "_push_crypto", return_value=True):
            km._push_notify("romp: web", "Needs you: pick a migration", "SID-web", 3)
            self.assertTrue(done.wait(5), "the send thread ran")
            # pruning happens after the sends; poll briefly for the store write
            for _ in range(100):
                if "https://push.example.net/send/dead" not in km._push_subs():
                    break
                time.sleep(0.05)
        self.assertEqual(set(km._push_subs()), {"https://push.example.net/send/live"},
                         "404/410 prunes; success stays")
        body = json.loads(list(seen.values())[0].decode())
        # the plan's privacy note, pinned: the payload is the card's gist (title + body) plus
        # ROUTING metadata — where the tap lands (sid, the data block), how it stacks (tag) and the
        # badge count — and nothing more (no brief, no transcript), even though the content is
        # E2E-encrypted. Every routing value is an id or a fixed word, never text.
        self.assertEqual(set(body), {"title", "body", "sid", "badge", "tag", "data"})
        self.assertEqual((body["title"], body["sid"], body["badge"]), ("romp: web", "SID-web", 3))
        self.assertEqual(set(body["data"]), {"sid", "host", "kind", "cardId", "url", "name", "pid"})   # name (2026-09-09): the session's display name, filed on the ledger row; pid: the kernel's handle on this push to this device (PushLedger)


class PushPayloadShape(unittest.TestCase):
    """_push_payload: the ONE builder every push kind goes through (the user 2026-09-06, who wants
    a tap to focus the romp already open and land on the session — and card — that buzzed)."""

    def test_a_card_push_carries_the_card_and_a_deep_link_the_shell_parses(self):
        d = km._push_payload("romp: web", "Needs you: pick one", "SID-web", 2, kind="card",
                             card_id="SID-web:g3")
        self.assertEqual(d["data"], {"sid": "SID-web", "host": "", "kind": "card", "cardId": "SID-web:g3",
                                     "url": "/?push-reveal=SID-web&push-card=SID-web%3Ag3",
                                     "name": "SID-web",   # no registry entry here: the short id, as _push_test always fell back
                                     "pid": ""})          # the builder carries the pid the caller minted per device (2026-09-09); none here
        self.assertEqual(d["tag"], "romp:SID-web", "one notification per session")
        self.assertEqual(d["sid"], "SID-web", "…and flat, for a worker of the previous build")
        self.assertEqual(d["badge"], 2)

    def test_the_deep_link_carries_the_pid_so_the_page_settles_the_row_it_lands(self):
        # 2026-09-10: on Apple the deep link IS the tap (the declarative message's navigate), and the page that lands it
        # has nothing but the URL — so the pid rides as push-pid, after the session and the card; no pid, no param
        d = km._push_payload("romp: web", "b", "SID-web", kind="card", card_id="SID-web:g3", pid="PID-x-0000000000000")
        self.assertEqual(d["data"]["pid"], "PID-x-0000000000000")
        self.assertEqual(d["data"]["url"], "/?push-reveal=SID-web&push-card=SID-web%3Ag3&push-pid=PID-x-0000000000000")
        self.assertEqual(km._push_payload("web", "b", "SID-web", kind="turn", pid="PID-y-0000000000000")["data"]["url"], "/?push-reveal=SID-web&push-pid=PID-y-0000000000000")
        self.assertEqual(km._push_payload("romp", "b", kind="test", pid="")["data"]["url"], "/", "no session, no pid: nowhere to land")

    def test_a_turn_push_names_its_kind_and_carries_no_card(self):
        d = km._push_payload("web", "finished a turn", "SID-web", kind="turn")
        self.assertEqual((d["data"]["kind"], d["data"]["cardId"], d["data"]["url"]),
                         ("turn", "", "/?push-reveal=SID-web"))
        self.assertNotIn("badge", d, "None omits the key — the worker leaves the count alone")

    def test_a_test_push_has_no_session_and_lands_on_romp_itself(self):
        # the popover's probe: kind "test", no sid, so the tap can only ever focus or open romp — and every test
        # collapses into one notification rather than stacking on the lock screen
        d = km._push_payload("romp", "Test notification", kind="test")
        self.assertEqual(d["data"], {"sid": "", "host": "", "kind": "test", "cardId": "", "url": "/", "name": "", "pid": ""})
        self.assertEqual(d["tag"], "romp:test")

    def test_the_payload_names_the_session_the_way_the_test_push_does(self):
        # 2026-09-09: the ledger row files the session's name off the payload (the kernel's lines name it from there), so
        # every push carries `name`, resolved by ONE helper in _push_test's order of authority — the names registry for a
        # local session, the tunnel supervisor's snapshot for a federated one (host-prefixed), then the caller's label,
        # then the short id — unless the leg passes its own (the turn leg's title IS the name)
        with mock.patch.object(km, "_name_of", side_effect=lambda s: {"SID-web": "web"}.get(s)), \
             mock.patch.object(km, "_remote_name_of", side_effect=lambda h, s: {("boxa", "SID-api"): "api"}.get((h, s))):
            self.assertEqual(km._push_payload("romp: web", "b", "SID-web")["data"]["name"], "web")
            self.assertEqual(km._push_payload("romp: boxa:api", "b", "boxa:SID-api", host="boxa")["data"]["name"], "boxa:api")
            self.assertEqual(km._push_payload("romp: boxb:?", "b", "boxb:SID-unknown")["data"]["name"], "SID-unkn", "no snapshot: the short id")
            self.assertEqual(km._push_payload("web", "finished", "SID-web", kind="turn", name="web (renamed)")["data"]["name"], "web (renamed)", "a leg's own name wins")
            self.assertEqual(km._push_session_name("SID-other", label="  the   tab  text  "), "the tab text", "the label, flattened, when the kernel has no name")
            self.assertEqual(km._push_session_name(""), "")
            self.assertEqual(km._push_session_name("SID-other", label="x" * 200), "x" * km.PUSH_LABEL_MAX, "clipped")
            # the same lookup as a (name, bare) pair — reconciling #1157 with #1155's title rule: the TITLE wears the
            # session's own name with no host in front, the body and routing block the host-prefixed form, and both
            # come from ONE lookup so a body and its title can never name two different sessions. The stand-ins
            # (label, short id) have no host to strip, so they are shared
            self.assertEqual(km._push_session_names("boxa:SID-api", label="ignored"), ("boxa:api", "api"))
            self.assertEqual(km._push_session_names("SID-web"), ("web", "web"))
            self.assertEqual(km._push_session_names("boxb:SID-unknown"), ("SID-unkn", "SID-unkn"))
            self.assertEqual(km._push_session_names("boxb:SID-unknown", label=" tab\ttext "), ("tab text", "tab text"))
            self.assertEqual(km._push_session_names("SID-other", label="x" * 200), ("x" * km.PUSH_LABEL_MAX,) * 2)
            self.assertEqual(km._push_session_names(""), ("", ""))
            self.assertEqual(km._push_session_name("boxa:SID-api"), km._push_session_names("boxa:SID-api")[0], "the singular is the pair's first")

    def test_a_relayed_push_keeps_the_origin_in_sid_and_host(self):
        # a federated event's sid already wears its host prefix (the merged dashboard's own tab
        # address); host is the courtesy copy, from the relay's origin or read off the prefix
        d = km._push_payload("romp: boxa:web", "b", "boxa:11111111-2222", host="boxa", card_id="11111111-2222:g1")
        self.assertEqual((d["data"]["sid"], d["data"]["host"]), ("boxa:11111111-2222", "boxa"))
        self.assertEqual(d["data"]["url"], "/?push-reveal=boxa%3A11111111-2222&push-card=11111111-2222%3Ag1")
        self.assertEqual(km._push_payload("t", "b", "boxb:S")["data"]["host"], "boxb")

    def test_missing_crypto_with_subscriptions_says_so(self):
        km._save_push_subs({"https://push.example.net/send/x": {
            "endpoint": "https://push.example.net/send/x",
            "keys": {"p256dh": "k", "auth": "a"}}})
        with mock.patch.object(km, "_PUSH_CRYPTO", [False]), \
             mock.patch.object(km.sys, "stderr", new=io.StringIO()) as err, \
             mock.patch.object(km, "_push_send_one") as send:
            km._push_notify("romp: web", "Needs you")
        send.assert_not_called()
        self.assertIn("cryptography", err.getvalue(), "a starving phone is never silent")


class DeclarativeWire(unittest.TestCase):
    """2026-09-10: the tap as the OS's own callback. An Apple endpoint (web.push.apple.com — Safari, an iOS Home Screen
    web app) is sent a Declarative Web Push message, verified member by member against the W3C Push API editor's draft
    and WebKit's NotificationJSONParser.cpp: `web_push: 8030`; `notification` with `title`, `body`, `navigate` (an
    ABSOLUTE deep link — WebKit parses it with no base), `tag`, `data` (the routing block, pid included) and `silent`
    for the quiet card push; `mutable: true` at the top level so the worker still gets the push event for its shown
    ack; `app_badge` at the top level only when the caller had a count. Every other endpoint keeps the imperative
    shape. Decided by the endpoint's host, never a user-agent guess."""
    APPLE = "https://web.push.apple.com/QOJ7example000000000000000000000000000"
    FCM = "https://fcm.googleapis.com/fcm/send/example-000000000000"
    MOZ = "https://updates.push.services.mozilla.com/wpush/v2/example000000"
    ORIGIN = "https://TESTHOST.example"

    def setUp(self):
        _clear_push_state()

    def test_the_endpoint_host_decides_the_shape(self):
        self.assertTrue(km._push_apple_endpoint(self.APPLE))
        self.assertTrue(km._push_apple_endpoint("https://WEB.PUSH.APPLE.COM/x"))
        self.assertTrue(km._push_apple_endpoint("https://region1.web.push.apple.com/x"))
        for ep in (self.FCM, self.MOZ, "https://web.push.apple.com.evil.example/x", "https://push.example.net/send/x", ""):
            self.assertFalse(km._push_apple_endpoint(ep), ep)

    def test_an_apple_endpoint_gets_the_declarative_message_with_the_deep_link_made_absolute(self):
        d = km._push_payload("romp: web", "Needs you: pick one", "SID-web", 2, kind="card", card_id="SID-web:g3", pid="PID-a-0000000000000")
        sub = {"endpoint": self.APPLE, "keys": {"p256dh": "k", "auth": "a"}, "origin": self.ORIGIN}
        w = json.loads(km._push_wire(d, sub).decode())
        self.assertEqual(w, {
            "web_push": 8030,
            "notification": {
                "title": "romp: web",
                "body": "Needs you: pick one",
                "navigate": self.ORIGIN + "/?push-reveal=SID-web&push-card=SID-web%3Ag3&push-pid=PID-a-0000000000000",
                "tag": "romp:SID-web",
                "data": {"sid": "SID-web", "host": "", "kind": "card", "cardId": "SID-web:g3",
                         "url": "/?push-reveal=SID-web&push-card=SID-web%3Ag3&push-pid=PID-a-0000000000000",
                         "name": "SID-web", "pid": "PID-a-0000000000000"}},
            "mutable": True,
            "app_badge": 2})
        self.assertEqual(json.loads(km._push_wire(d, dict(sub, origin=self.ORIGIN + "/")).decode())["notification"]["navigate"],
                         w["notification"]["navigate"], "a trailing slash on the origin does not double")
        # the same payload to an FCM or Mozilla endpoint is the imperative shape, byte for byte
        self.assertEqual(json.loads(km._push_wire(d, {"endpoint": self.FCM, "keys": {}}).decode()), d)
        self.assertEqual(json.loads(km._push_wire(d, {"endpoint": self.MOZ, "keys": {}}).decode()), d)

    def test_the_turn_and_test_legs_carry_no_badge_and_the_quiet_card_is_silent(self):
        sub = {"endpoint": self.APPLE, "keys": {}, "origin": self.ORIGIN}
        turn = json.loads(km._push_wire(km._push_payload("web", "finished a turn", "SID-web", kind="turn", name="web", pid="PID-t-0000000000000"), sub).decode())
        self.assertNotIn("app_badge", turn, "the count rides its own push: no badge, no app_badge")
        self.assertNotIn("silent", turn["notification"])
        self.assertEqual((turn["notification"]["title"], turn["notification"]["navigate"], turn["notification"]["data"]["kind"]),
                         ("web", self.ORIGIN + "/?push-reveal=SID-web&push-pid=PID-t-0000000000000", "turn"))
        test = json.loads(km._push_wire(km._push_payload("romp", "Test notification — tap to come back to web.", "SID-web", kind="test", name="web", pid="PID-s-0000000000000"), sub).decode())
        self.assertEqual((test["web_push"], test["mutable"], test["notification"]["data"]["kind"]), (8030, True, "test"))
        quiet = json.loads(km._push_wire(km._push_payload("romp: web", "b", "SID-web", 0, kind="card", card_id="SID-web:g1", quiet=True, pid="PID-q-0000000000000"), sub).decode())
        self.assertEqual((quiet["notification"]["silent"], quiet["app_badge"]), (True, 0), "the quiet card push: silent, and a zero count clears the badge")
        mirrored = json.loads(km._push_wire(km._push_payload("romp: boxa:web", "b", "boxa:SID-web", None, host="boxa", pid="PID-m-0000000000000"), sub).decode())
        self.assertNotIn("app_badge", mirrored, "a mirrored federated event has no count: the origin's is not ours")
        self.assertEqual(set(mirrored), {"web_push", "notification", "mutable"})
        self.assertEqual(set(mirrored["notification"]), {"title", "body", "navigate", "tag", "data"}, "only verified members, nothing guessed (no lang, dir, icon)")

    def test_an_apple_endpoint_without_an_origin_on_file_gets_the_imperative_shape_and_a_line(self):
        # a subscription from before the shell recorded its origin cannot carry a valid navigate (WebKit parses it with no
        # base and refuses a relative one — and then the whole message). The notification still shows, by the old shape,
        # and stderr names the fix; never a message the user agent would refuse whole, never silence
        d = km._push_payload("web", "b", "SID-web", kind="turn", pid="PID-o-0000000000000")
        with mock.patch.object(km.sys, "stderr", new=io.StringIO()) as err:
            w = json.loads(km._push_wire(d, {"endpoint": self.APPLE, "keys": {}}).decode())
        self.assertEqual(w, d)
        self.assertIn("web.push.apple.com", err.getvalue())
        # the line names what records the origin now — the device's own next request (OriginBackfill below), no toggle
        self.assertIn("next request", err.getvalue())
        self.assertNotIn("off and on", err.getvalue(), "no manual step is asked of the user (2026-09-10)")

    def test_the_fan_out_and_the_test_push_send_each_device_its_own_shape_with_its_own_pid(self):
        km._save_push_subs({self.APPLE: {"endpoint": self.APPLE, "keys": {"p256dh": "k", "auth": "a"}, "origin": self.ORIGIN},
                            self.FCM: {"endpoint": self.FCM, "keys": {"p256dh": "k", "auth": "a"}}})
        seen, done = {}, threading.Event()

        def fake_send(sub, payload):
            seen[sub["endpoint"]] = json.loads(payload.decode())
            if len(seen) == 2:
                done.set()
            return True

        with mock.patch.object(km, "_push_send_one", side_effect=fake_send), \
             mock.patch.object(km, "_push_crypto", return_value=True):
            km._push_notify("romp: web", "Needs you: pick one", "SID-web", 3, kind="card", card_id="SID-web:g1")
            self.assertTrue(done.wait(5), "the send thread ran")
        rows = {r["endpoint"]: r for r in km._push_ledger()}
        a, f = seen[self.APPLE], seen[self.FCM]
        self.assertEqual((a["web_push"], a["mutable"], a["app_badge"]), (8030, True, 3))
        self.assertEqual(a["notification"]["data"]["pid"], rows[self.APPLE]["pid"], "the declarative message carries THAT device's pid…")
        self.assertTrue(a["notification"]["navigate"].endswith("&push-pid=" + rows[self.APPLE]["pid"]), "…in its navigate too: " + a["notification"]["navigate"])
        self.assertEqual(f["data"]["pid"], rows[self.FCM]["pid"])
        self.assertEqual(set(f), {"title", "body", "sid", "badge", "tag", "data"}, "the FCM device gets the imperative shape")
        with mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp:
            res = km._push_test(self.APPLE, "SID-web", "", "web")
        self.assertTrue(res["ok"])
        t = json.loads(pp.call_args[0][1].decode())
        self.assertEqual((t["web_push"], t["notification"]["title"], t["notification"]["data"]["kind"]), (8030, "Romp: web", "test"),
                         "the test push wears _notify_title's shape (#1155): the wordmark over the bare session name")
        self.assertEqual(t["notification"]["data"]["pid"], [r["pid"] for r in km._push_ledger() if r["kind"] == "test"][0])


class OriginBackfill(unittest.TestCase):
    """2026-09-10, the follow-up: a subscription made BEFORE the bell posted its origin gains one from the requests the
    device already makes, with no user action — the page's GET /push/pending (its own endpoint in the query), the
    worker's POST /push/ack (the row's endpoint), the popover's POST /push/test. The origin is read off the request
    the way the browser states it: the Origin header (a same-origin POST carries one; a same-origin GET does not, per
    Fetch), else the Referer's origin (the page's or the worker script's URL), else a reverse proxy's X-Forwarded-Proto
    + X-Forwarded-Host/Host, else Host alone (a plain connection to the kernel's own socket). Only a MISSING origin is
    filled; a recorded one is never overwritten — a different one logs `[push] origin conflict` and the first stands.
    After the fill, the very next push to that endpoint is the declarative message."""
    APPLE = "https://web.push.apple.com/QOJ7backfill00000000000000000000000000"
    ORIGIN = "https://TESTHOST.example"

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _clear_push_state()
        # the pre-existing row: the shape the 2026-09-09 kernel stored — endpoint and keys, no origin key at all
        km._save_push_subs({self.APPLE: {"endpoint": self.APPLE, "keys": {"p256dh": "k", "auth": "a"}}})

    def _req(self, method, path, body=None, token=True, headers=None):
        import urllib.request, urllib.error
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Romp-Token"] = km.TOKEN
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method, data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def _pending(self, headers):
        import urllib.parse
        return self._req("GET", "/push/pending?endpoint=" + urllib.parse.quote(self.APPLE, safe=""), headers=headers)

    def _wire(self):
        """What the NEXT push to the Apple device would carry, with stderr captured: (decoded payload, stderr text)."""
        d = km._push_payload("web", "finished a turn", "SID-web", kind="turn", name="web", pid="PID-b-0000000000000")
        with mock.patch.object(km.sys, "stderr", new=io.StringIO()) as err:
            w = json.loads(km._push_wire(d, km._push_subs()[self.APPLE]).decode())
        return w, err.getvalue()

    def test_the_pages_pending_poll_records_the_origin_and_the_next_push_is_declarative(self):
        import contextlib
        w, err = self._wire()
        self.assertNotIn("web_push", w, "before any request: the imperative shape…")
        self.assertIn("no page origin on file", err, "…and the line")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._pending({"Origin": self.ORIGIN})
        self.assertEqual(code, 200)
        self.assertEqual(km._push_subs()[self.APPLE]["origin"], self.ORIGIN, "the row gained the request's origin")
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")],
                         ["[push] origin recorded endpoint=web.push.apple.com origin=" + self.ORIGIN])
        w, err = self._wire()
        self.assertEqual((w["web_push"], w["mutable"]), (8030, True), "the very next push is the declarative message")
        self.assertEqual(w["notification"]["navigate"], self.ORIGIN + "/?push-reveal=SID-web&push-pid=PID-b-0000000000000")
        self.assertEqual(err, "", "no fallback line once the origin is on file")

    def test_a_same_origin_get_carries_no_origin_header_so_the_referer_stands_in(self):
        # what a browser page's fetch('/push/pending?…') looks like from here: no Origin (Fetch appends it to POSTs and
        # CORS requests only), a Referer with the page's URL — token and all, which is why only its ORIGIN is kept
        code, _ = self._pending({"Referer": self.ORIGIN + "/?token=not-a-real-token&x=1"})
        self.assertEqual(code, 200)
        self.assertEqual(km._push_subs()[self.APPLE]["origin"], self.ORIGIN)

    def test_the_workers_ack_records_the_origin_by_the_rows_endpoint(self):
        import contextlib
        pid = km._push_ledger_add(self.APPLE, "SID-web", kind="turn", name="web")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            # the worker's fetch: no token, a same-origin POST, so the Origin header is the worker's own origin
            code, _ = self._req("POST", "/push/ack", {"pid": pid, "stage": "shown", "v": "abc.1"}, token=False, headers={"Origin": self.ORIGIN})
        self.assertEqual(code, 200)
        self.assertEqual(km._push_subs()[self.APPLE]["origin"], self.ORIGIN)
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")],
                         ["[push] ack stage=shown sid=SID-web endpoint=web.push.apple.com",
                          "[push] origin recorded endpoint=web.push.apple.com origin=" + self.ORIGIN])
        w, _ = self._wire()
        self.assertEqual(w["notification"]["navigate"], self.ORIGIN + "/?push-reveal=SID-web&push-pid=PID-b-0000000000000")
        # an ack for a pid this ledger never issued names no endpoint: nothing to fill, and the row is untouched
        km._save_push_subs({self.APPLE: {"endpoint": self.APPLE, "keys": {"p256dh": "k", "auth": "a"}}})
        code, _ = self._req("POST", "/push/ack", {"pid": "never-issued-pid-0001", "stage": "shown", "v": ""}, token=False, headers={"Origin": self.ORIGIN})
        self.assertEqual(code, 404)
        self.assertNotIn("origin", km._push_subs()[self.APPLE])

    def test_the_test_button_records_the_origin_before_it_sends_so_the_test_push_itself_is_declarative(self):
        with mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp:
            code, body = self._req("POST", "/push/test", {"endpoint": self.APPLE, "sid": "SID-web", "host": "", "label": "web"},
                                   headers={"Origin": self.ORIGIN})
        self.assertEqual((code, json.loads(body)["ok"]), (200, True))
        self.assertEqual(km._push_subs()[self.APPLE]["origin"], self.ORIGIN)
        t = json.loads(pp.call_args[0][1].decode())
        self.assertEqual(t["web_push"], 8030, "the test push went out declarative: the origin was on file before the send")
        self.assertTrue(t["notification"]["navigate"].startswith(self.ORIGIN + "/?push-reveal=SID-web&push-pid="), t["notification"]["navigate"])

    def test_a_recorded_origin_is_never_overwritten_and_a_different_one_logs_a_conflict(self):
        import contextlib
        km._save_push_subs({self.APPLE: {"endpoint": self.APPLE, "keys": {"p256dh": "k", "auth": "a"}, "origin": self.ORIGIN}})
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._pending({"Origin": "https://other.TESTHOST.example"})
            self.assertEqual(code, 200)
            code, _ = self._pending({"Origin": self.ORIGIN})       # the same origin again: nothing to say
            self.assertEqual(code, 200)
            pid = km._push_ledger_add(self.APPLE, "SID-web", kind="turn", name="web")
            self._req("POST", "/push/ack", {"pid": pid, "stage": "shown", "v": "abc.1"}, token=False, headers={"Origin": "http://127.0.0.1:29855"})
        self.assertEqual(km._push_subs()[self.APPLE]["origin"], self.ORIGIN, "the first recorded origin stands")
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push] origin")],
                         ["[push] origin conflict endpoint=web.push.apple.com kept=%s saw=https://other.TESTHOST.example" % self.ORIGIN,
                          "[push] origin conflict endpoint=web.push.apple.com kept=%s saw=http://127.0.0.1:29855" % self.ORIGIN],
                         "each conflicting sighting is a line; the matching one is silent")

    def test_an_endpoint_nobody_subscribed_writes_nothing(self):
        import urllib.parse
        before = (jd.STATE / "push-subscriptions.json").read_text()
        code, _ = self._req("GET", "/push/pending?endpoint=" + urllib.parse.quote("https://web.push.apple.com/unknown-000", safe=""),
                            headers={"Origin": self.ORIGIN})
        self.assertEqual(code, 200)
        self.assertEqual((jd.STATE / "push-subscriptions.json").read_text(), before, "no row minted for an endpoint nobody subscribed")
        self.assertEqual(km._push_backfill_origin(self.APPLE, ""), "none", "no origin derivable: nothing written")
        self.assertNotIn("origin", km._push_subs()[self.APPLE])

    def test_the_origin_is_read_off_the_request_in_the_order_the_browser_and_the_proxy_state_it(self):
        f = km._request_page_origin
        # the browser's own word first: the Origin header, then the Referer's origin (never its path or query)
        self.assertEqual(f({"Origin": self.ORIGIN, "Referer": "https://elsewhere.example/", "Host": "127.0.0.1:1"}), self.ORIGIN)
        self.assertEqual(f({"Origin": self.ORIGIN + "/", "Host": "x"}), self.ORIGIN, "a trailing slash is dropped")
        self.assertEqual(f({"Referer": self.ORIGIN + ":8443/?token=t#h", "Host": "127.0.0.1:1"}), self.ORIGIN + ":8443")
        self.assertEqual(f({"Origin": "null", "Referer": self.ORIGIN + "/sw.js"}), self.ORIGIN, "an opaque 'null' Origin is no origin; the worker script's Referer is")
        # then a reverse proxy's word: the browser-facing scheme and host it forwards (tailscale serve, Caddy)
        self.assertEqual(f({"X-Forwarded-Proto": "https", "X-Forwarded-Host": "TESTHOST.example", "Host": "127.0.0.1:8765"}), self.ORIGIN)
        self.assertEqual(f({"X-Forwarded-Proto": "https, http", "Host": "TESTHOST.example"}), self.ORIGIN, "the first hop's scheme; Host when the proxy forwards none")
        # then Host alone — a plain connection to the kernel's own socket. The Push API is [SecureContext]: a page holding
        # a subscription runs over https unless its host is loopback, the one place http is a secure context
        self.assertEqual(f({"Host": "127.0.0.1:8765"}), "http://127.0.0.1:8765")
        self.assertEqual(f({"Host": "localhost:8765"}), "http://localhost:8765")
        self.assertEqual(f({"Host": "[::1]:8765"}), "http://[::1]:8765")
        self.assertEqual(f({"Host": "TESTHOST.example"}), self.ORIGIN)
        # nothing usable → "" (never a guess written to the row)
        for h in ({}, {"Origin": "javascript:alert(1)"}, {"Referer": "not a url"}, {"Origin": "https://TESTHOST.example/path"},
                  {"X-Forwarded-Proto": "https"}, {"Host": "bad host with spaces"}):
            self.assertEqual(f(h), "", repr(h))


class PushLedger(unittest.TestCase):
    """The kernel's record of each push (2026-09-09; the ledger block above _push_ledger in the kernel has the finding):
    every session-addressed push files a row per device — {pid, endpoint, sid, host, kind, cardId, name, sentAt,
    shownAt, tappedAt, landedAt, supersededAt, droppedAt, swVersion} — and the payload to that device carries the
    row's pid, in its routing block and in the deep link. Rows are read from disk on every op (restart-proof), capped
    per endpoint, 0600, and go with their endpoint's subscription. /push/pending lists EVERY unsettled row, newest
    first, each wearing its stage (2026-09-10, the vanish road: the page holds the shown rows against the screen and
    lands the one that is gone)."""
    EP_A = "https://push.example.net/send/phone-a"
    EP_B = "https://push.example.net/send/phone-b"
    ROW_KEYS = {"pid", "endpoint", "sid", "host", "kind", "cardId", "name", "sentAt", "shownAt", "tappedAt", "landedAt", "supersededAt", "droppedAt", "swVersion"}

    def setUp(self):
        _clear_push_state()

    def _subscribe(self, *eps):
        km._save_push_subs({ep: {"endpoint": ep, "keys": {"p256dh": "k", "auth": "a"}} for ep in eps})

    def test_every_session_addressed_push_files_a_row_per_device_and_the_payload_carries_that_devices_pid(self):
        self._subscribe(self.EP_A, self.EP_B)
        seen, done = {}, threading.Event()

        def fake_send(sub, payload):
            seen[sub["endpoint"]] = json.loads(payload.decode())
            if len(seen) == 2:
                done.set()
            return True

        with mock.patch.object(km, "_push_send_one", side_effect=fake_send), \
             mock.patch.object(km, "_push_crypto", return_value=True):
            km._push_notify("romp: web", "Needs you: pick one", "SID-web", 3, kind="card", card_id="SID-web:g1")
            self.assertTrue(done.wait(5), "the send thread ran")
        rows = km._push_ledger()
        self.assertEqual(len(rows), 2, "one row per (push, device)")
        by_ep = {r["endpoint"]: r for r in rows}
        for ep in (self.EP_A, self.EP_B):
            r = by_ep[ep]
            self.assertEqual(set(r), self.ROW_KEYS)
            self.assertEqual(seen[ep]["data"]["pid"], r["pid"], "the payload to each device carries THAT device's pid")
            self.assertEqual(seen[ep]["data"]["url"], "/?push-reveal=SID-web&push-card=SID-web%3Ag1&push-pid=" + r["pid"], "…and its deep link does too")
            self.assertRegex(r["pid"], r"^[A-Za-z0-9_-]{22}$", "secrets.token_urlsafe(16)")
            self.assertEqual({k: r[k] for k in ("sid", "host", "kind", "cardId", "name")},
                             {"sid": "SID-web", "host": "", "kind": "card", "cardId": "SID-web:g1", "name": "SID-web"})
            self.assertGreater(r["sentAt"], 0)
            self.assertEqual((r["shownAt"], r["tappedAt"], r["landedAt"], r["supersededAt"], r["droppedAt"], r["swVersion"]), (0, 0, 0, 0, 0, ""))
        self.assertNotEqual(by_ep[self.EP_A]["pid"], by_ep[self.EP_B]["pid"])
        self.assertEqual(oct(os.stat(km._push_ledger_path()).st_mode & 0o777), "0o600", "endpoints are capability URLs")
        self.assertEqual((seen[self.EP_A]["title"], seen[self.EP_A]["body"]), ("romp: web", "Needs you: pick one"), "the gist is untouched")

    def test_a_sidless_push_files_no_row_and_carries_no_pid(self):
        self._subscribe(self.EP_A)
        seen, done = [], threading.Event()

        def fake_send(sub, payload):
            seen.append(json.loads(payload.decode()))
            done.set()
            return True

        with mock.patch.object(km, "_push_send_one", side_effect=fake_send), \
             mock.patch.object(km, "_push_crypto", return_value=True):
            km._push_notify("romp", "Test notification", "", kind="test")
            self.assertTrue(done.wait(5))
        self.assertEqual(km._push_ledger(), [], "nowhere to land, nothing to hand the page")
        self.assertEqual((seen[0]["data"]["pid"], seen[0]["data"]["url"]), ("", "/"))

    def test_the_test_push_files_a_row_too(self):
        self._subscribe(self.EP_A)
        with mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp:
            res = km._push_test(self.EP_A, "SID-web", "", "web")
        self.assertTrue(res["ok"])
        rows = km._push_ledger()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["endpoint"], rows[0]["sid"], rows[0]["kind"], rows[0]["name"]), (self.EP_A, "SID-web", "test", "web"))
        payload = json.loads(pp.call_args[0][1].decode())
        self.assertEqual(payload["data"]["pid"], rows[0]["pid"], "the pid rides the test push like any other")
        self.assertEqual(payload["data"]["url"], "/?push-reveal=SID-web&push-pid=" + rows[0]["pid"])
        with mock.patch.object(km, "_push_post", return_value=(201, "Created")) as pp:
            km._push_test(self.EP_A)                     # the sid-less probe: no row
        self.assertEqual(len(km._push_ledger()), 1)
        self.assertEqual(json.loads(pp.call_args[0][1].decode())["data"]["pid"], "")

    def test_the_cap_keeps_the_newest_rows_per_device(self):
        pids_a = [km._push_ledger_add(self.EP_A, "SID-web", kind="turn") for _ in range(km.PUSH_LEDGER_CAP + 5)]
        pids_b = [km._push_ledger_add(self.EP_B, "SID-web", kind="turn") for _ in range(3)]
        rows = km._push_ledger()
        self.assertEqual([r["pid"] for r in rows if r["endpoint"] == self.EP_A], pids_a[5:], "the newest CAP rows of the device, oldest first")
        self.assertEqual([r["pid"] for r in rows if r["endpoint"] == self.EP_B], pids_b, "another device's rows are untouched")

    @staticmethod
    def _pending(ep):
        """(pid, stage) per row, as /push/pending lists them: newest first"""
        return [(r["pid"], r["stage"]) for r in km._push_pending(ep)["rows"]]

    def test_pending_lists_every_unsettled_row_newest_first_and_names_each_stage(self):
        # the vanish road (2026-09-10): the page needs EVERY unsettled row to hold against the
        # screen, newest first (2026-09-09: the newest row alone named a push for ANOTHER session, sent 40 s after the
        # one the user tapped) — a clicked row lands, a shown row is compared with the screen, a sent row is left alone
        self.assertEqual(km._push_pending(self.EP_A), {"rows": []}, "nothing filed: nothing pending")
        p1 = km._push_ledger_add(self.EP_A, "SID-web", kind="turn", name="web")
        p2 = km._push_ledger_add(self.EP_A, "SID-api", kind="card", card_id="SID-api:g2", name="api")
        p3 = km._push_ledger_add(self.EP_A, "SID-tests", kind="turn", name="tests")
        pb = km._push_ledger_add(self.EP_B, "SID-web", kind="turn", name="web")
        rows = km._push_pending(self.EP_A)["rows"]
        self.assertEqual([r["pid"] for r in rows], [p3, p2, p1], "every unsettled row of the device, newest first")
        r = rows[1]
        self.assertEqual(set(r), {"pid", "sid", "host", "kind", "cardId", "name", "stage", "ageS"}, "what the page reads")
        self.assertEqual((r["sid"], r["kind"], r["cardId"], r["name"], r["stage"]), ("SID-api", "card", "SID-api:g2", "api", "sent"), "no ack at all is 'sent'")
        self.assertGreaterEqual(r["ageS"], 0)
        self.assertLessEqual(r["ageS"], 1)
        # the stages, strongest word first: clicked over shown over sent
        km._push_ledger_stamp(p2, "shown", "abc.123")
        km._push_ledger_stamp(p1, "shown", "abc.123")
        self.assertEqual(self._pending(self.EP_A), [(p3, "sent"), (p2, "shown"), (p1, "shown")])
        km._push_ledger_stamp(p2, "clicked")
        km._push_ledger_stamp(pb, "clicked")
        self.assertEqual(self._pending(self.EP_A), [(p3, "sent"), (p2, "clicked"), (p1, "shown")])
        self.assertEqual(self._pending(self.EP_B), [(pb, "clicked")], "another device's rows are its own")
        row = [r for r in km._push_ledger() if r["pid"] == p2][0]
        self.assertGreater(row["shownAt"], 0)
        self.assertGreater(row["tappedAt"], 0)
        self.assertEqual(row["swVersion"], "abc.123", "the acking worker's build, kept")
        t0 = row["shownAt"]
        km._push_ledger_stamp(p2, "shown")
        self.assertEqual([r for r in km._push_ledger() if r["pid"] == p2][0]["shownAt"], t0, "the first stamp stands: a repeated ack is idempotent")
        # the three settles each retire a row from the list: landed, superseded, dropped
        km._push_ledger_stamp(p2, "landed")
        self.assertEqual(self._pending(self.EP_A), [(p3, "sent"), (p1, "shown")], "a landed row is done with")
        km._push_ledger_stamp(p3, "superseded")
        self.assertEqual(self._pending(self.EP_A), [(p1, "shown")])
        km._push_ledger_stamp(p1, "dropped")
        self.assertEqual(km._push_pending(self.EP_A), {"rows": []})
        self.assertEqual((self._row(p3)["supersededAt"] > 0, self._row(p1)["droppedAt"] > 0, self._row(p3)["landedAt"], self._row(p1)["landedAt"]), (True, True, 0, 0),
                         "each settle stamps its own field, never landedAt")
        self.assertIsNone(km._push_ledger_stamp("never-issued-pid-0001", "shown"), "an unknown pid changes nothing")
        self.assertEqual(set(km._PUSH_STAGE_FIELD), set(km._PUSH_ACK_STAGES) | set(km._PUSH_SETTLE_STAGES), "every stage is an ack or a settle")
        self.assertEqual((km._PUSH_ACK_STAGES, km._PUSH_SETTLE_STAGES), (("shown", "clicked"), ("landed", "superseded", "dropped")),
                         "the acks are what the worker saw, the settles what the page decided: no 'closed', no 'dismissed'")
        for gone in ("closed", "dismissed"):
            self.assertNotIn(gone, km._PUSH_STAGE_FIELD)

    def _row(self, pid):
        return [r for r in km._push_ledger() if r["pid"] == pid][0]

    def test_a_shown_ack_supersedes_the_older_unsettled_rows_for_the_same_session_on_that_device(self):
        # the notification tag is per session: a newer push SHOWN for a session replaced the older one's notification on
        # that device's screen — gone without a tap. Settled at the shown ack (the event itself), so the page never reads
        # it as vanished; a clicked older row is a tap still waiting to land, never collapsed; other sessions and other
        # devices are untouched
        old_web = km._push_ledger_add(self.EP_A, "SID-web", kind="turn", name="web")
        old_api = km._push_ledger_add(self.EP_A, "SID-api", kind="turn", name="api")
        tapped_web = km._push_ledger_add(self.EP_A, "SID-web", kind="turn", name="web")
        other_dev = km._push_ledger_add(self.EP_B, "SID-web", kind="turn", name="web")
        new_web = km._push_ledger_add(self.EP_A, "SID-web", kind="card", card_id="SID-web:g1", name="web")
        km._push_ledger_stamp(old_web, "shown")
        km._push_ledger_stamp(tapped_web, "clicked")
        row = km._push_ledger_stamp(new_web, "shown")
        done = km._push_ledger_supersede(row)
        self.assertEqual([r["pid"] for r in done], [old_web], "the older unsettled, untapped row for that session on that device")
        self.assertGreater(self._row(old_web)["supersededAt"], 0)
        self.assertEqual(self._pending(self.EP_A), [(new_web, "shown"), (tapped_web, "clicked"), (old_api, "sent")])
        self.assertEqual(self._pending(self.EP_B), [(other_dev, "sent")])
        self.assertEqual(km._push_ledger_supersede(row), [], "nothing left to supersede: idempotent")
        self.assertEqual(km._push_ledger_supersede({"pid": "never-issued-pid-0001", "endpoint": self.EP_A, "sid": "SID-web"}), [])

    def test_the_ledger_is_read_from_disk_every_time_so_a_restart_loses_nothing(self):
        pid = km._push_ledger_add(self.EP_A, "SID-web", kind="turn", name="web")
        # another process (the kernel before a restart) stamped the row: this one sees it without any cache to invalidate
        d = json.loads(km._push_ledger_path().read_text())
        for r in d["rows"]:
            if r["pid"] == pid:
                r["tappedAt"] = 1234
        km._push_ledger_path().write_text(json.dumps(d))
        self.assertEqual([(r["pid"], r["stage"]) for r in km._push_pending(self.EP_A)["rows"]], [(pid, "clicked")])
        self.assertEqual(km._push_pending(self.EP_A)["rows"][0]["ageS"], 86400, "age is clipped, never a bare timestamp difference")
        km._push_ledger_path().write_text("not json")
        self.assertEqual(km._push_ledger(), [], "a damaged file reads as empty, never a throw")

    def test_a_device_that_unsubscribes_or_is_pruned_takes_its_rows_with_it(self):
        self._subscribe(self.EP_A, self.EP_B)
        km._push_ledger_add(self.EP_A, "SID-web", kind="turn")
        km._push_ledger_add(self.EP_B, "SID-web", kind="turn")
        km._del_push_sub(self.EP_A)
        self.assertEqual([r["endpoint"] for r in km._push_ledger()], [self.EP_B])
        self.assertEqual(km._push_pending(self.EP_A), {"rows": []})


def _fake_ws_client(app, wid):
    """Just enough of a _clients row for the reveal/badge paths: send() records the parsed JSON."""
    got = []
    return {"app": app, "wid": wid, "alive": True, "ready": True,   # ready: its bundle listens (the ready handler's stamp)
            "send": lambda s: got.append(json.loads(s))}, got


class RevealAiming(unittest.TestCase):
    """A push tap lands ON the session that fired (the user 2026-08-08). The cold-start half:
    POST /reveal parks the focus keyed by the asking window's wid, delivered on the exact event
    it waits for — that window's chat pane saying ready — and aimed at that pane alone."""

    def setUp(self):
        km._PENDING_REVEAL[0] = None
        self._added = []

    def tearDown(self):
        with km._clients_lock:
            for c in self._added:
                if c in km._clients:
                    km._clients.remove(c)
        km._PENDING_REVEAL[0] = None

    def _register(self, app, wid):
        c, got = _fake_ws_client(app, wid)
        with km._clients_lock:
            km._clients.append(c)
        self._added.append(c)
        return c, got

    def test_connected_pane_gets_it_now_dead_session_gets_revive(self):
        c, got = self._register("chat", "W1")
        with mock.patch.object(km, "_live_map", return_value={"SID-live": {}}):
            self.assertTrue(km._reveal_request("SID-live", "W1"))
        self.assertEqual(got, [{"type": "focus", "id": "SID-live", "live": True}])
        self.assertIsNone(km._PENDING_REVEAL[0], "delivered → nothing parked")
        # a DEAD session never silently reveals — the revive prompt instead (_reveal_or_confirm's split)
        got.clear()
        with mock.patch.object(km, "_live_map", return_value={}), \
             mock.patch.object(km, "_name_of", return_value="web"):
            km._reveal_request("SID-gone", "W1")
        self.assertEqual(got[0]["type"], "confirmRevive")

    def test_boot_race_parks_then_ready_consumes_aimed_by_wid(self):
        # the norm: the shell's fetch beats its chat iframe's WS, so nothing is connected yet
        with mock.patch.object(km, "_live_map", return_value={"SID-live": {}}):
            self.assertFalse(km._reveal_request("SID-live", "W-phone"))
            self.assertEqual(km._PENDING_REVEAL[0], {"sid": "SID-live", "wid": "W-phone"})
            # another dashboard's pane saying ready must NOT steal it (the 2026-07-29 rule)
            other, other_got = _fake_ws_client("chat", "W-desktop")
            km._consume_pending_reveal(other)
            self.assertEqual(other_got, [])
            self.assertIsNotNone(km._PENDING_REVEAL[0])
            # a non-chat pane of the RIGHT window doesn't take it either
            feed, feed_got = _fake_ws_client("feed", "W-phone")
            km._consume_pending_reveal(feed)
            self.assertEqual(feed_got, [])
            # the aimed pane arrives → delivered once, latch cleared
            mine, mine_got = _fake_ws_client("chat", "W-phone")
            km._consume_pending_reveal(mine)
            # `own` (split screen, 2026-09-08): ONE chat client consumes the parked tap, so the pane's column arbitration must not hand it elsewhere
            self.assertEqual(mine_got, [{"type": "focus", "id": "SID-live", "live": True, "own": True}])
            self.assertIsNone(km._PENDING_REVEAL[0])
            km._consume_pending_reveal(mine)
            self.assertEqual(len(mine_got), 1, "consumed means consumed")

    def test_a_boot_reveal_reaches_a_pane_whose_ready_beat_the_fetch_and_keeps_a_copy(self):
        """T312 (2026-09-10): on a slow machine the page's chat pane can say ready BEFORE the shell's boot fetch
        parks the reveal, and a park nobody consumes lands the tap on whichever session frame the pane adopted
        first. A boot reveal therefore goes to every same-wid chat pane like an unproven live tap: delivered
        AND kept parked (the same-wid socket may be the previous page's, dead), retired by the pane's answer
        or consumed by a new pane's ready. Before the fix a boot reveal was parked alone."""
        mine, mine_got = self._register("chat", "W-phone")   # ready already, no ping outstanding
        with mock.patch.object(km, "_live_map", return_value={"SID-live": {}}):
            self.assertTrue(km._reveal_request("SID-live", "W-phone", boot=True, via="link"), "delivered to the ready pane")
        self.assertEqual(mine_got, [{"type": "focus", "id": "SID-live", "live": True}])
        parked = km._PENDING_REVEAL[0]
        self.assertEqual((parked["sid"], parked["wid"]), ("SID-live", "W-phone"))
        self.assertEqual(len(parked.get("sent") or []), 1); self.assertIs(parked["sent"][0], mine, "…and a copy stays parked, tagged with who got it")
        # the pane answers (a pong, any message): the copy is retired, so a later ready never replays the tap
        km._reveal_proven(mine)
        self.assertIsNone(km._PENDING_REVEAL[0])

    def test_a_same_wid_socket_that_has_not_said_ready_is_no_target_and_its_ready_still_consumes(self):
        """The review find on T312: a chat socket exists from its handshake, but until its bundle posts `ready`
        it has no message listener (the ready handler's own paragraph: frames sent before it vanish), and the
        kernel counts that ready message as an answer (_note_ws_inbound → _reveal_proven). Delivering a boot
        reveal to such a socket lost the tap twice over: the frame vanished, and the ready retired the parked
        copy before the handler could consume it. So a not-yet-ready socket is no target on any road: the park
        stands, and the ready handler stamps the client and consumes."""
        booting, heard = self._register("chat", "W-boot")
        dropped = []
        booting["ready"] = False          # registered at its handshake; the bundle is still loading
        booting["send"] = lambda s: (heard if booting.get("ready") else dropped).append(json.loads(s))   # a frame before ready vanishes
        with mock.patch.object(km, "_live_map", return_value={"SID-live": {}}):
            self.assertFalse(km._reveal_request("SID-live", "W-boot", boot=True, via="link"), "parked: nothing can hear it yet")
            self.assertEqual(dropped, [], "nothing is sent to a pane that cannot listen")
            self.assertEqual(km._PENDING_REVEAL[0], {"sid": "SID-live", "wid": "W-boot"})
            # the pane's ready message arrives: _ws notes the inbound (an answer) BEFORE dispatching the handler…
            km._note_ws_inbound(booting)
            self.assertIsNotNone(km._PENDING_REVEAL[0], "…which must not retire a copy this pane never heard")
            # …then the ready handler stamps the client and consumes the park
            booting["ready"] = True
            km._consume_pending_reveal(booting)
        self.assertEqual(heard, [{"type": "focus", "id": "SID-live", "live": True, "own": True}])   # own: a consumed reveal is the receiving column's to take (the split, 2026-09-08)
        self.assertIsNone(km._PENDING_REVEAL[0])
        # a live (non-boot) tap to that same not-yet-ready socket parks too: the sw / ack / vanish roads had the
        # same hole once the shell saw the socket up but before the bundle listened
        with mock.patch.object(km, "_live_map", return_value={"SID-live": {}}):
            booting["ready"] = False
            self.assertFalse(km._reveal_request("SID-live", "W-boot", via="sw"))
            self.assertEqual(dropped, [])
            self.assertEqual(km._PENDING_REVEAL[0], {"sid": "SID-live", "wid": "W-boot"})


    def test_a_widless_park_matches_the_first_chat_pane(self):
        # sessionStorage blocked → the shell has no wid; better the first chat pane than a dropped tap
        with mock.patch.object(km, "_live_map", return_value={"S": {}}):
            km._reveal_request("S", "")
            c, got = _fake_ws_client("chat", "W-any")
            km._consume_pending_reveal(c)
        self.assertEqual(got[0]["id"], "S")

    def test_a_booting_page_reaches_a_same_wid_socket_and_still_parks_for_the_fresh_panes_ready(self):
        # the deep-link arrival (2026-09-06, the phone): the page is BOOTING, and a same-wid chat socket the
        # kernel still holds is usually the PREVIOUS page's (sessionStorage keeps the wid across a reload; a
        # suspended phone never sent its close, and the ping timeout has up to WS_DEAD_S to notice). From
        # 2026-09-06 to 2026-09-10 a boot reveal was therefore parked ALONE — until T312 found the other owner
        # of that wid: this page's own chat pane, whose ready beat the shell's fetch on a slow machine, so the
        # park had nothing left to consume it and the tap never landed. Now the boot reveal is delivered to
        # the same-wid socket like an unproven live tap AND a copy stays parked: a dead twin swallows its
        # frame and the fresh pane's ready consumes the copy; a live pane lands it and its answer retires it.
        twin, twin_got = self._register("chat", "W-phone")
        with mock.patch.object(km, "_live_map", return_value={"S": {}}):
            self.assertTrue(km._reveal_request("S", "W-phone", boot=True))
            self.assertEqual(twin_got, [{"type": "focus", "id": "S", "live": True}], "the same-wid socket is told: it may be the page's own pane")
            parked = km._PENDING_REVEAL[0]
            self.assertEqual((parked["sid"], parked["wid"], parked.get("sent")), ("S", "W-phone", [twin]), "…and the copy stays for the fresh pane")
            fresh, fresh_got = _fake_ws_client("chat", "W-phone")
            km._consume_pending_reveal(fresh)
        self.assertEqual(fresh_got, [{"type": "focus", "id": "S", "live": True, "own": True}])
        self.assertIsNone(km._PENDING_REVEAL[0])

    def test_a_live_tap_to_an_unproven_socket_keeps_a_copy_until_the_pong_or_the_redial(self):
        # the live half of the same hole: the pane's socket has a ping on the wire nobody has
        # answered yet (pingAt set — the peer is unproven since the last heartbeat). The focus goes
        # out as before, AND stays parked: the pong that proves the socket alive retires the copy
        # (the frame is ordered behind the ping it answers); a dead socket never pongs, the pane
        # redials, and the redial's first tab strip consumes the copy instead of finding nothing
        # (_resolve_reconnect stamps the client, its strip sender consumes; the redial posts no ready).
        c, got = self._register("chat", "W1")
        c["pingAt"] = 100.0
        with mock.patch.object(km, "_live_map", return_value={"S": {}}):
            self.assertTrue(km._reveal_request("S", "W1"))
        self.assertEqual(got, [{"type": "focus", "id": "S", "live": True}], "still delivered at once")
        self.assertEqual((km._PENDING_REVEAL[0] or {}).get("sid"), "S", "…and kept until the socket proves itself")
        # another window's pane pongs: not this tap's socket, the copy stays
        other, _ = self._register("chat", "W2")
        other["pingAt"] = 100.0
        km._note_ws_inbound(other, now=101.0)
        self.assertIsNotNone(km._PENDING_REVEAL[0])
        # the delivered-to socket pongs → proven → the copy is retired, and a later ready replays nothing
        km._note_ws_inbound(c, now=101.0)
        self.assertIsNone(km._PENDING_REVEAL[0])
        c["pingAt"] = None
        with mock.patch.object(km, "_live_map", return_value={"S": {}}):
            self.assertTrue(km._reveal_request("S", "W1"))
        self.assertIsNone(km._PENDING_REVEAL[0], "a socket with no ping outstanding is proven — nothing parked")
        # the dead case: never pongs; the redial's first strip takes the copy (its sender's consume, called here)
        c["pingAt"] = 100.0
        with mock.patch.object(km, "_live_map", return_value={"S": {}}):
            km._reveal_request("S", "W1")
            fresh, fresh_got = _fake_ws_client("chat", "W1")
            km._consume_pending_reveal(fresh)
        self.assertEqual(fresh_got, [{"type": "focus", "id": "S", "live": True, "own": True}])
        self.assertIsNone(km._PENDING_REVEAL[0])

    def test_a_redialed_pane_is_a_target_and_its_first_strip_consumes_the_park(self):
        """A pane whose socket died in the SAME kernel process redials (the shim's ?reconnect=1), and its
        bundle posts ready once per page life, so no ready ever arrives on the new socket: nothing stamped
        the redial, so it was never a target and nothing consumed a park aimed at its window. The tap after
        a phone suspend or a dropped link parked for good, until the page reloaded. The redial's first tab
        strip is the event that stands in for the ready: _resolve_reconnect stamps the client when it pops
        the flag and says so, and the strip sender consumes the park right behind the strip it just sent."""
        with mock.patch.object(km, "_live_map", return_value={"S": {}}):
            self.assertFalse(km._reveal_request("S", "W1", via="vanish"), "the socket died: the tap parks")
            self.assertEqual(km._PENDING_REVEAL[0], {"sid": "S", "wid": "W1"})
            c, got = self._register("chat", "W1")
            del c["ready"]                    # registered at its handshake; no ready will follow on this socket
            c["reconnect"] = True             # the shim's own statement: this page held the sessions before
            self.assertFalse(km._reveal_request("S", "W1", via="sw"), "before its first strip the redial is no target")
            self.assertEqual(got, [], "nothing is sent to a pane whose strip has not gone yet")
            self.assertEqual(km._PENDING_REVEAL[0], {"sid": "S", "wid": "W1"}, "the park stands")
            # the first strip sender pops the flag: the client is stamped, and the sender is told to consume
            self.assertTrue(km._resolve_reconnect(c, []), "a popped redial flag says so")
            self.assertIs(c.get("ready"), True, "the redial is stamped like a pane whose ready was heard")
            self.assertIsNone(c.get("reconnect"))
            km._consume_pending_reveal(c, why="the pane's redial")
            self.assertEqual(len(got), 1)
            self.assertEqual((got[0]["type"], got[0]["id"], got[0]["live"]), ("focus", "S", True))
            self.assertIsNone(km._PENDING_REVEAL[0], "consumed")
            # from here the redialed pane is an ordinary target: the next tap lands at once
            self.assertTrue(km._reveal_request("S", "W1", via="sw"))
            self.assertEqual((got[-1]["type"], got[-1]["id"]), ("focus", "S"))
            self.assertEqual(len(got), 2)
            # a client that declared no redial is left as it was: not stamped, and its sender told nothing
            fresh, fresh_got = self._register("chat", "W2")
            del fresh["ready"]
            self.assertFalse(km._resolve_reconnect(fresh, []))
            self.assertNotIn("ready", fresh)


class RevealRoute(unittest.TestCase):
    """POST /reveal over the real handler (the ServeSecurity pattern)."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        km._PENDING_REVEAL[0] = None

    def _post(self, path, body, token=True):
        import urllib.request, urllib.error
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Romp-Token"] = km.TOKEN
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_parks_for_the_named_wid(self):
        code, body = self._post("/reveal", {"sid": "SID-x", "wid": "W-x"})
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["delivered"])
        self.assertEqual(km._PENDING_REVEAL[0], {"sid": "SID-x", "wid": "W-x"})

    def test_requires_token_and_sid(self):
        code, _ = self._post("/reveal", {"sid": "S"}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/reveal", {"wid": "W"})
        self.assertEqual(code, 400)
        self.assertIsNone(km._PENDING_REVEAL[0])

    def test_a_boot_flagged_reveal_reaches_a_connected_same_wid_pane_and_keeps_a_copy_parked(self):
        # the deep-link arrival says it is booting; a connected same-wid pane may be the previous page's dead
        # socket OR this page's own pane whose ready beat the fetch (T312), so the route delivers to it AND keeps
        # the copy parked for the pane that is about to connect (RevealAiming has the why)
        twin, twin_got = _fake_ws_client("chat", "W-x")
        with km._clients_lock:
            km._clients.append(twin)
        try:
            with mock.patch.object(km, "_live_map", return_value={"SID-x": {}}):
                code, body = self._post("/reveal", {"sid": "SID-x", "wid": "W-x", "boot": True})
        finally:
            with km._clients_lock:
                km._clients.remove(twin)
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["delivered"])
        self.assertEqual(twin_got, [{"type": "focus", "id": "SID-x", "live": True}])
        parked = km._PENDING_REVEAL[0]
        self.assertEqual((parked["sid"], parked["wid"], parked.get("sent")), ("SID-x", "W-x", [twin]))

    def test_every_tap_leaves_a_line_in_the_kernel_log(self):
        # 2026-09-08: a phone's tap "did nothing" and nothing recorded whether it had reached the kernel.
        # The route logs the road the shell names, the flags and the outcome; the park's end logs too.
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._post("/reveal", {"sid": "SID-x", "wid": "W-x", "via": "link", "boot": True})
            pane, got = _fake_ws_client("chat", "W-x")
            km._consume_pending_reveal(pane)
        self.assertEqual(code, 200)
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("[reveal]")]
        self.assertEqual(lines, ["[reveal] link sid=SID-x wid=W-x boot: parked",
                                 "[reveal] sid=SID-x wid=W-x: consumed — the pane's ready"])
        self.assertEqual(len(got), 1)

    def test_an_unknown_via_is_logged_as_other_never_verbatim(self):
        # review find (2026-09-09, on #1127): `via` went from the request body straight into the stderr line, so a
        # body could write anything into the line-oriented journal, a forged line included. The route admits the
        # roads in _REVEAL_ROADS and logs any other word as 'other'; a shell of a build before the field sends none,
        # and that stays the bare line. A word from a shell of another build ('store', 'offer' here) is 'other' too
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._post("/reveal", {"sid": "SID-x", "wid": "W-x", "via": "sw\n[reveal] forged sid=SID-z: delivered"})
            self._post("/reveal", {"sid": "SID-y", "wid": "W-y", "via": "store"})
            self._post("/reveal", {"sid": "SID-v", "wid": "W-v", "via": "vanish"})
            self._post("/reveal", {"sid": "SID-u", "wid": "W-u", "via": "offer"})
            self._post("/reveal", {"sid": "SID-l", "wid": "W-l", "via": "link"})
            self._post("/reveal", {"sid": "SID-a", "wid": "W-a", "via": "ack"})
            self._post("/reveal", {"sid": "SID-w", "wid": "W-w"})
        self.assertEqual(code, 200)
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("[reveal]")]
        self.assertEqual(lines, ["[reveal] other sid=SID-x wid=W-x: parked",
                                 "[reveal] other sid=SID-y wid=W-y: parked",
                                 "[reveal] vanish sid=SID-v wid=W-v: parked",
                                 "[reveal] other sid=SID-u wid=W-u: parked",
                                 "[reveal] link sid=SID-l wid=W-l: parked",
                                 "[reveal] ack sid=SID-a wid=W-a: parked",
                                 "[reveal] shell sid=SID-w wid=W-w: parked"])
        self.assertNotIn("forged", buf.getvalue())
        self.assertEqual(km._REVEAL_ROADS, frozenset({"sw", "link", "ack", "vanish"}))   # the worker's message, the deep link (on Apple the OS's tap callback), the kernel's clicked row, the notification gone from the screen


class PushLedgerRoutes(unittest.TestCase):
    """The ledger's routes over the real handler. POST /push/ack is authenticated by the PID ALONE — no token, no
    cookie: a worker's fetch carries no token header (the ledger block above _push_ledger in the kernel). The pid is
    128 unguessable bits the kernel issued, good for two timestamps on one row and nothing else. The page's routes
    — GET /push/pending, POST /push/landed | /push/superseded | /push/dropped — ride the token like every page fetch."""
    EP = "https://push.example.net/send/phone-a"

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _clear_push_state()

    def _req(self, method, path, body=None, token=True, raw=None, headers=None):
        import urllib.request, urllib.error
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Romp-Token"] = km.TOKEN
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method, data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def _row(self, pid):
        return [r for r in km._push_ledger() if r["pid"] == pid][0]

    def test_the_worker_acks_by_pid_alone_and_each_ack_leaves_a_line(self):
        import contextlib
        pid = km._push_ledger_add(self.EP, "SID-api", kind="turn", name="api")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, body = self._req("POST", "/push/ack", {"pid": pid, "stage": "shown", "v": "abc.123"}, token=False)
            self.assertEqual((code, json.loads(body)), (200, {"ok": True, "stage": "shown"}))
            code, body = self._req("POST", "/push/ack", {"pid": pid, "stage": "clicked", "v": "abc.123"}, token=False)
            self.assertEqual((code, json.loads(body)), (200, {"ok": True, "stage": "clicked"}))
        r = self._row(pid)
        self.assertGreater(r["shownAt"], 0)
        self.assertGreater(r["tappedAt"], 0)
        self.assertEqual(r["swVersion"], "abc.123")
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")],
                         ["[push] ack stage=shown sid=SID-api endpoint=push.example.net",
                          "[push] ack stage=clicked sid=SID-api endpoint=push.example.net"])
        self.assertEqual([(x["pid"], x["stage"]) for x in km._push_pending(self.EP)["rows"]], [(pid, "clicked")])
        # a cross-site Origin with no token is what a partitioned worker looks like from here: still the pid decides
        code, _ = self._req("POST", "/push/ack", {"pid": pid, "stage": "shown", "v": "x"}, token=False, headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 200)

    def test_an_unknown_pid_is_a_404_and_a_line_and_a_bad_ack_buys_nothing(self):
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._req("POST", "/push/ack", {"pid": "never-issued-pid-0001", "stage": "shown", "v": ""}, token=False)
        self.assertEqual(code, 404)
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")], ["[push] ack stage=shown: unknown pid"])
        pid = km._push_ledger_add(self.EP, "SID-api", kind="turn")
        for body in ({"pid": pid, "stage": "landed"}, {"pid": pid, "stage": "closed"}, {"pid": pid, "stage": "superseded"}, {"pid": pid, "stage": "dropped"},
                     {"pid": pid, "stage": ""}, {"pid": "short", "stage": "shown"}, {"pid": pid + "\n[push] forged", "stage": "shown"}, {"stage": "shown"}, [pid]):
            code, _ = self._req("POST", "/push/ack", body, token=False)
            self.assertEqual(code, 400, repr(body))
        code, _ = self._req("POST", "/push/ack", raw=b"nope", token=False)
        self.assertEqual(code, 400)
        code, _ = self._req("POST", "/push/ack", raw=b"{" + b" " * km._PUSH_ACK_MAX_BYTES + b"}", token=False)
        self.assertEqual(code, 413, "capped far below the authenticated routes' limit: no token gates this read")
        self.assertEqual((self._row(pid)["shownAt"], self._row(pid)["tappedAt"]), (0, 0), "nothing stamped")
        self.assertEqual(len(km._push_ledger()), 1, "no row minted by a caller")
        self.assertNotIn("closedAt", self._row(pid), "no 'closed' stage")

    def test_the_pages_routes_ride_the_token_and_the_three_settles_are_the_only_ones(self):
        pid = km._push_ledger_add(self.EP, "SID-api", kind="turn")
        for method, path, body in (("GET", "/push/pending?endpoint=" + self.EP, None), ("POST", "/push/landed", {"pid": pid}),
                                   ("POST", "/push/superseded", {"pid": pid}), ("POST", "/push/dropped", {"pid": pid})):
            code, _ = self._req(method, path, body, token=False)
            self.assertEqual(code, 403, path)
        self.assertEqual((self._row(pid)["landedAt"], self._row(pid)["supersededAt"], self._row(pid)["droppedAt"]), (0, 0, 0))
        code, _ = self._req("GET", "/push/pending")
        self.assertEqual(code, 400, "no endpoint named")
        code, _ = self._req("POST", "/push/dismissed", {"pid": pid})
        self.assertNotEqual(code, 200, "no fourth settle: the page lands, supersedes or drops a row, nothing else")
        self.assertNotIn("dismissedAt", self._row(pid))

    def test_a_shown_ack_supersedes_the_older_rows_for_that_session_with_a_line_each(self):
        import contextlib, urllib.parse
        old_api = km._push_ledger_add(self.EP, "SID-api", kind="turn", name="api")
        web = km._push_ledger_add(self.EP, "SID-web", kind="turn", name="web")
        new_api = km._push_ledger_add(self.EP, "SID-api", kind="card", card_id="SID-api:g2", name="api")
        q = "/push/pending?endpoint=" + urllib.parse.quote(self.EP, safe="")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._req("POST", "/push/ack", {"pid": old_api, "stage": "shown", "v": "abc.1"}, token=False)
            self.assertEqual(code, 200)
            code, _ = self._req("POST", "/push/ack", {"pid": web, "stage": "shown", "v": "abc.1"}, token=False)
            self.assertEqual(code, 200)
            code, _ = self._req("POST", "/push/ack", {"pid": new_api, "stage": "shown", "v": "abc.1"}, token=False)   # the tag replaced old_api's notification
            self.assertEqual(code, 200)
        self.assertGreater(self._row(old_api)["supersededAt"], 0, "the older unsettled row for the same session on this device")
        self.assertEqual((self._row(new_api)["supersededAt"], self._row(web)["supersededAt"]), (0, 0), "another session's row is untouched")
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")],
                         ["[push] ack stage=shown sid=SID-api endpoint=push.example.net",
                          "[push] ack stage=shown sid=SID-web endpoint=push.example.net",
                          "[push] ack stage=shown sid=SID-api endpoint=push.example.net",
                          "[push] superseded sid=SID-api endpoint=push.example.net"])
        code, body = self._req("GET", q)
        self.assertEqual([(r["pid"], r["stage"]) for r in json.loads(body)["rows"]], [(new_api, "shown"), (web, "shown")],
                         "the superseded row is gone from the list; the two on the screen stay, wearing their stage")

    def test_pending_lists_every_unsettled_row_newest_first_and_the_three_settles_retire_them(self):
        import contextlib, urllib.parse
        p1 = km._push_ledger_add(self.EP, "SID-web", kind="turn", name="web")
        p2 = km._push_ledger_add(self.EP, "SID-api", kind="card", card_id="SID-api:g2", name="api")
        p3 = km._push_ledger_add(self.EP, "SID-tst", kind="turn", name="tests")
        q = "/push/pending?endpoint=" + urllib.parse.quote(self.EP, safe="")
        code, body = self._req("GET", q)
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(list(d), ["rows"])
        self.assertEqual([(r["pid"], r["stage"]) for r in d["rows"]], [(p3, "sent"), (p2, "sent"), (p1, "sent")], "every unsettled row, newest first, three in flight")
        self.assertEqual({k: d["rows"][1][k] for k in ("sid", "name", "kind", "cardId")}, {"sid": "SID-api", "name": "api", "kind": "card", "cardId": "SID-api:g2"})
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            for pid in (p1, p2, p3):
                self._req("POST", "/push/ack", {"pid": pid, "stage": "shown", "v": "abc.1"}, token=False)
            code, body = self._req("GET", q)
            self.assertEqual([(r["pid"], r["stage"]) for r in json.loads(body)["rows"]], [(p3, "shown"), (p2, "shown"), (p1, "shown")], "three notifications on a screen, for the page to hold against it")
            self._req("POST", "/push/ack", {"pid": p2, "stage": "clicked", "v": "abc.1"}, token=False)
            code, body = self._req("GET", q)
            self.assertEqual([(r["pid"], r["stage"]) for r in json.loads(body)["rows"]], [(p3, "shown"), (p2, "clicked"), (p1, "shown")])
            code, body = self._req("POST", "/push/landed", {"pid": p2})
            self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
            code, body = self._req("GET", q)
            self.assertEqual([r["pid"] for r in json.loads(body)["rows"]], [p3, p1], "the landed row is gone from the list")
            code, body = self._req("POST", "/push/superseded", {"pid": p3})   # a newer notification for that session was on the screen in its place
            self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
            code, body = self._req("POST", "/push/dropped", {"pid": p1})      # one of several vanished at once: spent, never a landing
            self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
            code, _ = self._req("POST", "/push/landed", {"pid": "never-issued-pid-0001"})
            self.assertEqual(code, 404)
            code, _ = self._req("POST", "/push/landed", {"pid": "x"})
            self.assertEqual(code, 400)
        code, body = self._req("GET", q)
        self.assertEqual(json.loads(body), {"rows": []}, "every row settled: nothing left to inflate a later count")
        self.assertEqual((self._row(p3)["supersededAt"] > 0, self._row(p1)["droppedAt"] > 0, self._row(p3)["landedAt"], self._row(p1)["landedAt"]), (True, True, 0, 0), "each settle stamps its own field, never landedAt")
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")],
                         ["[push] ack stage=shown sid=SID-web endpoint=push.example.net",
                          "[push] ack stage=shown sid=SID-api endpoint=push.example.net",
                          "[push] ack stage=shown sid=SID-tst endpoint=push.example.net",
                          "[push] ack stage=clicked sid=SID-api endpoint=push.example.net",
                          "[push] landed sid=SID-api endpoint=push.example.net",
                          "[push] superseded sid=SID-tst endpoint=push.example.net",
                          "[push] dropped sid=SID-web endpoint=push.example.net",
                          "[push] landed: unknown pid"], "three sessions, so no supersede at the shown acks; the page's three settles, one line each")


class Badge(unittest.TestCase):
    """Proposal 3: the app icon wears the needs-you count."""

    def setUp(self):
        km._BADGE_LAST[0] = None

    def test_counts_real_needs_input_cards_only(self):
        feed = {"asks": [
            {"itemId": "a", "column": "needs_input"},
            {"itemId": "b", "column": "needs_input", "provisional": True},   # placeholder churn
            {"itemId": "c", "column": "working"},
            {"itemId": "d", "column": "completed"},
        ]}
        self.assertEqual(km._needs_you_count(feed), 1)

    def test_pushes_to_shells_only_on_change(self):
        sent = []
        with mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            km._badge_push(2)
            km._badge_push(2)          # same number again — a re-send would be a pointless wake
            km._badge_push(0)          # dropping to zero IS a change: the icon must clear
        self.assertEqual(sent, [("shell", {"type": "badge", "n": 2}),
                                ("shell", {"type": "badge", "n": 0})])


class LandingRevealPins(unittest.TestCase):
    def test_shell_carries_the_four_roads_of_the_tap(self):
        html = km._landing()
        self.assertIn("m.romp==='notificationClick'", html)   # 'sw': the worker's message to a live window
        self.assertIn("searchParams.get('push-reveal')", html)  # 'link': the deep link's params…
        self.assertIn("searchParams.get('push-card')", html)
        self.assertIn("searchParams.get('push-pid')", html)     # …the pid included (2026-09-10), so the page settles the row it lands
        self.assertIn("searchParams['delete']('push-reveal')", html)   # …stripped once read
        self.assertIn("searchParams['delete']('push-pid')", html)
        self.assertIn("history.replaceState", html)
        self.assertIn("fetch('/push/pending?endpoint='", html)   # 'ack' and 'vanish': every unsettled row for this device…
        self.assertIn("r.getNotifications()", html)              # …held against the screen (2026-09-10, the user's call)
        self.assertIn("fetch('/push/'+what", html)               # the settles: 'landed' once per pid, 'superseded', 'dropped'
        for what in ("settle('landed',", "settle('superseded',", "settle('dropped',"):
            self.assertIn(what, km._LANDING_REVEAL_JS, what)
        self.assertIn("fetch('/reveal'", html)     # ONE activation path: the kernel aims the focus…
        self.assertIn("romp:wid", html)            # …at the shell's own per-window id
        self.assertIn("romp:'revealCard'", html)   # a card kind also scrolls the feed to the card…
        self.assertIn("m.romp==='ready'&&m.app==='feed'", html)   # …once the feed has its cards
        # the TAP's scripts post no focus straight into the chat iframe any more (the kernel aims it). The split
        # script (_LANDING_SPLIT_JS) posted one from 2026-09-08 to 2026-09-11 (the hand-over to a column the shell had
        # just made); since then it seeds the column's state blob instead and posts none either.
        taps = km._LANDING_REVEAL_JS + km._LANDING_PUSH_JS + km._LANDING_MOBILE_JS
        self.assertNotIn("type:'focus',id:sid", taps, "no focus posted straight into the chat iframe any more")
        self.assertNotIn("own:true", km._LANDING_SPLIT_JS, "the split's hand-over went with the seeded blob (2026-09-11); the plain focus a move into an open column posts wears no `own`")
        self.assertNotIn("type:'focus'", km._LANDING_REVEAL_JS)
        self.assertNotIn("setTimeout", km._LANDING_REVEAL_JS, "event-based: the feed's ready, never a timer")

    def test_the_link_is_read_at_boot_and_on_a_same_page_url_change(self):
        # 2026-09-10: on Apple the deep link IS the tap (the declarative message's navigate), and the user agent may navigate
        # the EXISTING window rather than open one — so the params are read at boot AND whenever the page shows again or
        # its history entry changes without a full load; pageshow/popstate registration does not depend on a worker existing
        js = km._LANDING_REVEAL_JS
        self.assertIn("fromLedger('boot',fromLink('boot'));", js)   # the link first, and the ledger check knows what it landed (the newer word)
        self.assertIn("window.addEventListener('pageshow',function(){fromLedger('pageshow',fromLink('pageshow'));});", js)
        self.assertIn("window.addEventListener('popstate',function(){fromLink('popstate');});", js)
        self.assertIn("document.addEventListener('visibilitychange',function(){if(document.visibilityState==='visible')fromLedger('visible');});", js)
        self.assertIn("window.addEventListener('focus',function(){fromLedger('focus');});", js)
        self.assertLess(js.index("if(swc&&swc.addEventListener)swc.addEventListener('message'"), js.index("window.addEventListener('pageshow'"))
        self.assertNotIn("if(swc&&swc.addEventListener){", js, "the link listeners are not gated on a worker")

    def test_the_page_has_no_road_but_the_four_and_no_element_of_its_own(self):
        # pinned absent from the script's code lines and the served shell: any store or replay, any build comparison, any
        # element the script would own, any word for /reveal but the four roads, any timer
        import re
        code = lambda src: "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))   # the prose may name what went; the code may not
        js = code(km._LANDING_REVEAL_JS)
        for word in ("caches", "/__romp/", "tapReplay", "tapLanded", "registration.update", "r.update()", "PAGEV", "__ROMP_SWV__",
                     "'store'", "'offer'", "'closed'", "tap-resume", "sw-stale", "sw-update", "tap-offer", "/push/dismissed", "setTimeout",
                     "fingerprint(", "resume(", "pushReveal"):
            self.assertNotIn(word, js, word)
        html = km._landing()
        for word in ("tap-offer", "from the notification", "/push/dismissed", "__ROMP_SWV__", "tapReplay", "/__romp/"):
            self.assertNotIn(word, html, word)
        self.assertEqual(set(re.findall(r"getElementById\('([^']+)'\)", js)), {"f-feed", "f-chat"}, "the two pane iframes are all the script looks up: no element of its own")
        self.assertEqual(km._REVEAL_ROADS, frozenset({"sw", "link", "ack", "vanish"}))

    def test_the_vanish_road_names_its_trade_off_at_the_landing_site(self):
        # the user's call (2026-09-10): a working background tap is worth an occasional wrong landing after a swipe. The
        # comment at the landing site names the conflation, its cause and the decision, so nobody reads the road as an
        # oversight and takes it out
        js = km._LANDING_REVEAL_JS
        self.assertIn("'vanish'", js)
        self.assertIn("function displayed()", js)
        for word in ("notificationclick", "notificationclose", "swipe", "2026-09-10", "accepted"):
            self.assertIn(word, js, "the landing site's comment names it: " + word)
        self.assertIn("vanish", km._push_pending.__doc__)

    def test_the_boot_flag_follows_the_chat_panes_own_socket(self):
        # review find (2026-09-09, on #1127): the two sides of the contract share the words. The pane's shim posts its
        # socket state to the shell on every open (netState); the reveal script latches the chat pane's up and sends
        # boot:true on every road until then. A drift here is a tap parked for a ready that never comes, or one
        # 'delivered' to the previous page's socket
        js = km._LANDING_REVEAL_JS
        self.assertIn("if(m&&m.romp==='wsState'&&m.app==='chat'&&m.state==='up')chatUp=true;", js)
        # …or the pane's rendered tabs (2026-09-09): the tabs come over that very socket, so an active tab in the chat
        # iframe's DOM proves it was up even when its message beat this script
        self.assertIn("boot=!!boot||!(chatUp||activeSid());", js)
        self.assertEqual(js.count("chatUp=false"), 1, "declared once, never reset: latched")
        self.assertIn('window.parent.postMessage({romp:"wsState",app:APP,state:s},"*")', km._shim("chat", 1))

    def test_shell_ws_trues_up_the_badge(self):
        html = km._landing()
        self.assertIn("{type:'ready'}", html)      # connect → the kernel answers with the current count
        self.assertIn("setAppBadge", html)
        self.assertIn("clearAppBadge", html)       # zero clears, never leaves a stale number

    def test_the_shell_files_its_own_diag_rows_over_a_socket_that_carries_its_wid(self):
        # 2026-09-08: the shell's scripts record what they saw as the clientDiag rows the panes already file
        # (surface 'shell'), through ONE poster the mobile script defines before the bell's and the landing
        # script parse; rows queue (capped) until the shell socket opens. The socket carries the shell's wid, so
        # its rows match this dashboard's pane rows — and _reveal_chat_for's shell line has a target
        js = km._LANDING_MOBILE_JS
        self.assertIn("var m={type:'clientDiag',surface:'shell',what:what,data:data};", js)
        self.assertIn("window.__rompShellDiag=shellDiag;", js)
        self.assertLess(js.index("window.__rompShellDiag=shellDiag;"), js.index("var bar=document.getElementById('mtabs');if(!bar)return;"),
                        "defined before the script's first early return")
        self.assertIn("else if(diagQ.length<DIAGQ_MAX)diagQ.push(m);", js)
        self.assertIn("'/ws?app=shell&wid='+encodeURIComponent(wid())", js)
        self.assertIn("shellSock=ws;var q=diagQ;diagQ=[];q.forEach(", js, "queued rows go out on open")
        self.assertIn("if(shellSock===ws)shellSock=null;", js)
        html = km._landing()
        self.assertLess(html.index("window.__rompShellDiag=shellDiag;"), html.index("function activeSession(){"))
        self.assertLess(html.index("window.__rompShellDiag=shellDiag;"), html.index("diag('deeplink'"))
        # the callers: the bell's press and the landing script's rows, each through the poster, and the
        # kernel already persists the type they post (the clientDiag branch of _dispatch_ws)
        self.assertIn("diag('push-test',{sidAttached:!!at.sid,host:at.host,why:at.why,tabs:at.tabs});", km._LANDING_PUSH_JS)
        for row in ("diag('deeplink',{via:via,hasSid:!!pr,hasCard:!!pc,hasPid:!!pp,dup:!first,controlled:", "diag('sw-message',{shape:m.romp,hasSid:!!m.sid,",
                    "diag('reveal-post',{status:r.status,via:via,boot:!!boot});", "diag('tap-pending',row)", "diag('tap-pending-land',{sid8:r.sid.slice(0,8),ageS:r.ageS,dup:!first})",
                    "diag('tap-vanish-land',{sid8:v.sid.slice(0,8),ageS:v.ageS})"):
            self.assertIn(row, km._LANDING_REVEAL_JS)
        import inspect
        self.assertIn('msg.get("type") == "clientDiag"', inspect.getsource(km.Handler._dispatch_ws))


# The shell's reveal script, EXECUTED (the test_error_center.py pattern): node runs _LANDING_REVEAL_JS against
# stubs of the few browser globals it touches, booting on a URL and then replaying the events a page produces.
# Pins the routing, not the words: /reveal is asked with the shell's wid, the URL is stripped, the card waits
# for the FEED's ready (not the timeline's), a turn kind reveals no card, a sid-less tap does nothing, a refused
# /reveal is loud, one pid lands once whichever road carries it.
_REVEAL_HARNESS = r"""
'use strict';
const FETCHES = [], POSTED = [], NOTES = [], REPLACED = [], WIN = [], SW = [], DOC = [], PAGESHOW = [], POPSTATE = [], FOCUS = [], DIAG = [], GETS = [], GETN = [];
let fetchOk = true, fetchFail = false;
// the kernel's ledger: what GET /push/pending answers, reassignable by a driver
let PENDING = process.env.ROMP_TEST_PENDING ? JSON.parse(process.env.ROMP_TEST_PENDING) : { rows: [] };
// the notifications still on this device's screen, as registration.getNotifications() lists them: the pids in
// ROMP_TEST_DISPLAYED, reassignable by a driver; getnFail: the call throws (a screen the page cannot read)
let DISPLAYED = process.env.ROMP_TEST_DISPLAYED ? JSON.parse(process.env.ROMP_TEST_DISPLAYED) : [], getnFail = false;
const feedWin = { postMessage: (m) => POSTED.push(m) };
global.window = global;
// the chat pane's active tab, as the same-origin iframe DOM the script reads: ROMP_TEST_ACTIVE at boot, reassignable
let activeSid = process.env.ROMP_TEST_ACTIVE || '';
const chatFrame = { contentDocument: { querySelector: () => (activeSid ? { getAttribute: () => activeSid } : null) } };
global.document = { getElementById: (id) => (id === 'f-feed' ? { contentWindow: feedWin } : id === 'f-chat' ? chatFrame : null),
  addEventListener: (k, f) => { if (k === 'visibilitychange') DOC.push(f); }, visibilityState: 'visible' };
global.sessionStorage = { getItem: (k) => (k === 'romp:wid' ? 'W-test' : null) };
global.addEventListener = (k, f) => { if (k === 'message') WIN.push(f); if (k === 'pageshow') PAGESHOW.push(f); if (k === 'popstate') POPSTATE.push(f); if (k === 'focus') FOCUS.push(f); };
// this page's registration and its pushManager: the subscription ROMP_TEST_ENDPOINT, or none (a device that never opted in)
// …and its getNotifications: the screen, as DISPLAYED above; ROMP_TEST_NO_GETN: a browser without the method
const REG = { pushManager: { getSubscription: () => Promise.resolve(process.env.ROMP_TEST_ENDPOINT ? { endpoint: process.env.ROMP_TEST_ENDPOINT } : null) } };
if (!process.env.ROMP_TEST_NO_GETN) REG.getNotifications = () => { GETN.push(DISPLAYED.slice()); return getnFail ? Promise.reject(new Error('no screen')) : Promise.resolve(DISPLAYED.map((pid) => ({ data: { pid } }))); };
if (!process.env.ROMP_TEST_NO_SW) Object.defineProperty(global, 'navigator', { configurable: true,   // a getter-only global in node 22
  value: { serviceWorker: { addEventListener: (k, f) => { if (k === 'message') SW.push(f); },
                            getRegistration: () => Promise.resolve(REG),
                            controller: { postMessage: () => {} } } } });   // the worker that controls this page
else Object.defineProperty(global, 'navigator', { configurable: true, value: {} });   // a browser with no worker at all
global.history = { replaceState: (s, t, u) => { REPLACED.push(u); global.location.href = 'http://localhost:7777' + u; } };
// the boot is env-driven so one harness plays every arrival: ROMP_TEST_HREF is the URL the page opened on (default: the deep link)
global.location = { href: process.env.ROMP_TEST_HREF || 'http://localhost:7777/?push-reveal=S1&push-card=S1%3Ag1&push-pid=PID-link-0000000001&keep=1#frag' };
global.fetch = (path, init) => {
  if (!init) { GETS.push(path); return fetchFail ? Promise.reject(new Error('down')) : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(PENDING) }); }   // the ledger's GET: no init, an answer with a body
  FETCHES.push([path, JSON.parse(init.body)]);
  return Promise.resolve(fetchOk ? { ok: true, status: 200 } : { ok: false, status: 400, text: () => Promise.resolve('missing sid') }); };
global.__rompNotify = (kind, text) => NOTES.push([kind, text]);
global.__rompShellDiag = (what, data) => DIAG.push([what, data]);   // _LANDING_MOBILE_JS's poster, stubbed: the rows this script files
// the head script's reader of the gear's Panes section, stubbed: ROMP_TEST_FEED_OFF boots with the Feed pane off in this
// browser; a driver flips FEED_OFF
let FEED_OFF = !!process.env.ROMP_TEST_FEED_OFF;
global.__rompPaneEnabled = (k) => !(k === 'feed' && FEED_OFF);
"""
_REVEAL_LIB = r"""
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
const swMsg = (m) => SW.forEach((f) => f({ data: m, source: {} }));
const winMsg = (m) => WIN.forEach((f) => f({ data: m }));
const flip = async (state) => { global.document.visibilityState = state; DOC.forEach((f) => f()); await settle(); };
const snap = () => ({ fetches: FETCHES.slice(), posted: POSTED.slice(), diag: DIAG.slice(), notes: NOTES.slice(), gets: GETS.slice(), getn: GETN.length, replaced: REPLACED.slice(), href: global.location.href });
const reset = () => { FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0; NOTES.length = 0; GETS.length = 0; GETN.length = 0; REPLACED.length = 0; };
"""
_REVEAL_DRIVER = _REVEAL_LIB + r"""
(async () => {
  const out = { boot: { fetches: FETCHES.slice(), replaced: REPLACED.slice(), postedBeforeReady: POSTED.length, diag: DIAG.slice(), swListeners: SW.length } };
  await settle();
  out.boot.diagAfter = DIAG.slice();                      // …plus /reveal's answer and the ledger check, once they land
  out.boot.gets = GETS.slice();
  winMsg({ romp: 'ready' });                              // the timeline's ready: not the feed's
  out.boot.postedAfterTimelineReady = POSTED.length;
  winMsg({ romp: 'ready', app: 'feed' });
  out.boot.postedAfterFeedReady = POSTED.slice();
  // the same tap's message, handed to the window openWindow opened on the link: the pid already landed by the link → a dup
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S1', host: '', kind: 'card', cardId: 'S1:g1', pid: 'PID-link-0000000001', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });
  await settle();
  out.handed = snap();
  // a tap reaching this page BEFORE its chat pane's socket is up (review find, 2026-09-09, on #1127): lands with boot:true,
  // so the kernel parks for THIS page's pane instead of aiming at the previous page's same-wid socket
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S0', host: '', kind: 'turn', cardId: '', pid: 'PID-early-000000001', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });
  await settle();
  out.earlySw = snap();
  winMsg({ romp: 'wsState', app: 'feed', state: 'up' });   // another pane's socket is not the chat pane's
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S0', host: '', kind: 'turn', cardId: '', pid: 'PID-early-000000002' });
  await settle();
  out.earlySwFeedUp = snap();
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });   // this page's chat pane connected: from here a tap is delivered live
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S2', host: '', kind: 'card', cardId: 'S2:g4', pid: 'PID-live-0000000001', diag: { clients: 3, tops: 1, road: 'focus', vis: 'hidden' } });
  await settle();
  out.live = snap();
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S2', host: '', kind: 'card', cardId: 'S2:g4', pid: 'PID-live-0000000001', diag: { clients: 3, tops: 1, road: 'focus', vis: 'hidden' } });   // the same tap again, by another road
  await settle();
  out.dup = snap();
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S3', host: '', kind: 'turn', cardId: '', pid: '' });   // no pid (an older kernel's push): lands, nothing to settle
  await settle();
  out.turn = snap();
  reset();
  swMsg({ romp: 'notificationClick', sid: '', host: '', kind: 'test', cardId: '', pid: '', diag: { clients: 1, tops: 1, road: 'focus', vis: '' } });
  await settle();
  out.test = snap();
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S5', host: '', kind: 'test', cardId: '', pid: 'PID-test-0000000005' });   // a test addressed to the session in front (2026-09-06)
  await settle();
  out.testSid = snap();
  reset();
  swMsg({ romp: 'pushReveal', sid: 'S6' });                                             // the shape of a worker two builds back: not a tap this shell reads
  await settle();
  out.legacy = snap();
  fetchOk = false; reset();
  swMsg({ romp: 'notificationClick', sid: 'S-bad', kind: 'turn', pid: '' });
  await settle();
  out.refused = snap();
  fetchOk = true;
  // the pane's socket dropping later does not re-arm the flag: the pane posts its ready once per page life
  winMsg({ romp: 'wsState', app: 'chat', state: 'down' });
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S30', host: '', kind: 'turn', cardId: '', pid: 'PID-late-0000000030' });
  await settle();
  out.afterDrop = snap();
  console.log(JSON.stringify(out));
})();
"""
# a page that booted on the plain start URL and later GAINS the deep link without a full load (2026-09-10: iOS may
# navigate the existing Home Screen window to the declarative message's navigate URL): pageshow and popstate read it
_LINK_LATER_DRIVER = _REVEAL_LIB + r"""
(async () => {
  const out = {};
  await settle();
  out.boot = snap();
  winMsg({ romp: 'ready', app: 'feed' });
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });
  reset();
  PAGESHOW.forEach((f) => f()); await settle();                                              // a plain pageshow: nothing to land
  out.plainShow = snap();
  reset();
  global.location.href = 'http://localhost:7777/?push-reveal=S40&push-card=S40%3Ag2&push-pid=PID-show-0000000040&t=1';
  PAGESHOW.forEach((f) => f()); await settle();
  out.pageshow = snap();
  reset();
  PAGESHOW.forEach((f) => f()); await settle();                                              // stripped: showing again lands nothing
  out.pageshowAgain = snap();
  reset();
  global.location.href = 'http://localhost:7777/?push-reveal=S41&push-pid=PID-pop-00000000041';
  POPSTATE.forEach((f) => f()); await settle();
  out.popstate = snap();
  reset();
  global.location.href = 'http://localhost:7777/?push-reveal=S41&push-pid=PID-pop-00000000041';   // the same link again (a history walk): the pid already landed
  POPSTATE.forEach((f) => f()); await settle();
  out.popstateDup = snap();
  reset();
  global.location.href = 'http://localhost:7777/?push-reveal=S42&push-card=bad%22id&push-pid=nope';   // a crafted card id and a pid of the wrong shape: dropped before they land
  PAGESHOW.forEach((f) => f()); await settle();
  out.crafted = snap();
  console.log(JSON.stringify(out));
})();
"""
# the ack and vanish roads: a page with a push subscription asks GET /push/pending on boot / visible / pageshow / focus,
# reads the screen, lands the clicked rows the kernel lists (once by pid) and the ONE shown row gone from the screen
_LEDGER_DRIVER = _REVEAL_LIB + r"""
const R = (pid, sid, stage, ageS, extra) => Object.assign({ pid, sid, host: '', kind: 'turn', cardId: '', name: 'web', stage, ageS }, extra || {});
(async () => {
  const out = {};
  await settle();
  out.boot = snap();                                   // PENDING is {rows: []} at boot: asked, nothing pending, the screen not read
  winMsg({ romp: 'ready', app: 'feed' });
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });
  reset();
  PENDING = { rows: [R('PID-clicked-000001', 'S50', 'clicked', 4, { kind: 'card', cardId: 'S50:g1', name: 'api' })] };
  await flip('visible');
  out.clicked = snap();
  reset();
  await flip('visible');                               // the kernel still says clicked (the landed POST in flight): the same pid is a dup
  out.clickedAgain = snap();
  reset();
  PENDING = { rows: [R('PID-two-00000000002', 'S52', 'clicked', 1), R('PID-two-00000000001', 'S51', 'clicked', 9)] };   // two taps the worker saw, neither landed: both land, newest first
  PAGESHOW.forEach((f) => f()); await settle();
  out.two = snap();
  reset();
  PENDING = { rows: [] };
  FOCUS.forEach((f) => f()); await settle();
  out.focus = snap();
  reset();
  await flip('hidden');
  out.hidden = snap();
  reset();
  // ONE VANISHED: two shown rows, one still on the screen — the other is gone: tapped, as far as the page can tell (the one
  // road a live iOS app leaves). It lands, silently
  PENDING = { rows: [R('PID-shown-00000002', 'S42', 'shown', 30, { name: 'tests' }), R('PID-shown-00000001', 'S41', 'shown', 45, { kind: 'card', cardId: 'S41:g3', name: 'api' })] };
  DISPLAYED = ['PID-shown-00000002'];
  await flip('visible');
  out.oneVanished = snap();
  reset();
  await flip('visible');                               // the kernel still lists both (the settle in flight): the landed pid is seen, nothing vanished
  out.oneVanishedAgain = snap();
  reset();
  // TWO VANISHED: the tap could have been on either — nothing lands, nothing shows; both rows are dropped, so they can
  // never inflate a later check's count
  PENDING = { rows: [R('PID-gone-000000002', 'S44', 'shown', 10), R('PID-gone-000000001', 'S43', 'shown', 20)] };
  DISPLAYED = [];
  PAGESHOW.forEach((f) => f()); await settle();
  out.twoVanished = snap();
  reset();
  // ALL DISPLAYED: the user has not touched them (a notification without a pid — an older kernel's — is nobody's)
  PENDING = { rows: [R('PID-up-0000000002', 'S46', 'shown', 5), R('PID-up-0000000001', 'S45', 'shown', 9)] };
  DISPLAYED = ['PID-up-0000000001', 'PID-up-0000000002', ''];
  FOCUS.forEach((f) => f()); await settle();
  out.allDisplayed = snap();
  reset();
  // SENT ONLY: never acked shown, so nothing is known to have been displayed — and nothing of it can have vanished
  PENDING = { rows: [R('PID-sent-000000001', 'S47', 'sent', 120)] };
  DISPLAYED = [];
  await flip('visible');
  out.sentOnly = snap();
  reset();
  // SUPERSEDED: a newer push for the same session is on the screen (its shown ack lost: 'sent'); the older row's
  // notification was replaced by the per-session tag, not tapped — settled as such. Another session's gone row is the
  // one vanished, and lands
  PENDING = { rows: [R('PID-newer-00000001', 'S50', 'sent', 2), R('PID-older-00000001', 'S50', 'shown', 60), R('PID-other-00000001', 'S51', 'shown', 61, { name: 'tests' })] };
  DISPLAYED = ['PID-newer-00000001'];
  await flip('visible');
  out.superseded = snap();
  reset();
  // THE SCREEN CANNOT BE READ: getNotifications throws — a notification gone cannot be told from one never shown; said
  // so, nothing lands, nothing settled
  getnFail = true;
  PENDING = { rows: [R('PID-blind-00000001', 'S52', 'shown', 7)] };
  await flip('visible');
  out.getnThrows = snap();
  getnFail = false;
  reset();
  // the worker's message carrying a pid settles the row too, and the ledger check never lands that push again
  PENDING = { rows: [R('PID-msg-0000000001', 'S54', 'clicked', 1)] };
  swMsg({ romp: 'notificationClick', sid: 'S54', host: '', kind: 'turn', cardId: '', pid: 'PID-msg-0000000001', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });
  await settle();
  out.msg = snap();
  reset();
  await flip('visible');
  out.msgThenLedger = snap();
  reset();
  // a clicked row beside a vanished one: the tap the worker saw lands; the vanished row is spent (dropped) — never a
  // second landing, and never left for the next check
  PENDING = { rows: [R('PID-both-clicked-01', 'S55', 'clicked', 2), R('PID-both-vanish-001', 'S56', 'shown', 9)] };
  DISPLAYED = [];
  await flip('visible');
  out.clickedBesideVanished = snap();
  reset();
  fetchFail = true;                                    // the kernel unreachable: said so, nothing lands, no throw
  await flip('visible');
  out.err = snap();
  fetchFail = false;
  reset();
  PENDING = { pid: 'PID-old-kernel-0001', sid: 'S58' };   // an answer without rows: nothing to act on, never a throw
  await flip('visible');
  out.noRows = snap();
  reset();
  PENDING = { rows: [R('', 'S59', 'shown', 1), R('PID-nosid-000000001', '', 'shown', 1), R('PID-nostage-0000001', 'S60', '', 1), 'junk', null] };   // rows without a pid, a sid or a stage are nobody's
  await flip('visible');
  out.junk = snap();
  console.log(JSON.stringify(out));
})();
"""
# a boot and nothing else: the rows the boot alone files
_BOOT_DRIVER = _REVEAL_LIB + r"""
(async () => { await settle(); console.log(JSON.stringify({ boot: snap() })); })();
"""
# a page whose chat pane shows tabs but whose wsState message this script never saw (2026-09-09): the tabs prove the
# socket was up, so a landing is delivered live, not parked
_TABS_DRIVER = _REVEAL_LIB + r"""
(async () => {
  const out = {};
  await settle();
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S60', host: '', kind: 'turn', cardId: '', pid: 'PID-tabs-000000060' });   // no tabs yet, no wsState: booting
  await settle();
  out.noTabs = snap();
  reset();
  activeSid = 'S1';                                    // the pane rendered its tabs — over the socket this script heard nothing about
  swMsg({ romp: 'notificationClick', sid: 'S61', host: '', kind: 'turn', cardId: '', pid: 'PID-tabs-000000061' });
  await settle();
  out.tabs = snap();
  console.log(JSON.stringify(out));
})();
"""


# the Feed pane off in this browser (the gear's Panes section, the user 2026-09-10): a card's landing does not wait on a feed
# that is never loaded; the /reveal that opened the session in the chat is the whole landing
_FEED_OFF_DRIVER = _REVEAL_LIB + r"""
(async () => {
  const out = { boot: { fetches: FETCHES.slice(), postedAtBoot: POSTED.length } };
  await settle();
  winMsg({ romp: 'ready', app: 'feed' });               // a feed's ready (another dashboard's pane cannot post here; a pane enabled later can)
  out.boot.postedAfterFeedReady = POSTED.slice();
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S2', host: '', kind: 'card', cardId: 'S2:g4', pid: 'PID-live-0000000001' });
  await settle();
  out.live = snap();
  FEED_OFF = false;                                      // the gear shows the pane again (its iframe loads and reports ready)
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S3', host: '', kind: 'card', cardId: 'S3:g1', pid: 'PID-live-0000000003' });
  await settle();
  out.backOn = snap();
  console.log(JSON.stringify(out));
})();
"""


def _run_reveal(driver, href=None, active=None, endpoint=None, pending=None, no_sw=False, displayed=None, no_getn=False, feed_off=False):
    """node runs the harness + the shell's reveal script + `driver`, booting on `href` (default: the deep link) — see the
    harness's env. `active`: the chat pane's active tab at boot; `endpoint`: this page's push subscription endpoint (none =
    a device that never opted in); `pending`: what the kernel's GET /push/pending answers at boot; `no_sw`: a browser with
    no service worker at all; `displayed`: the pids of the notifications still on the screen at boot, as
    registration.getNotifications() lists them; `no_getn`: a browser without that method."""
    import subprocess, tempfile as _tf
    env = dict(os.environ)
    if endpoint:
        env["ROMP_TEST_ENDPOINT"] = endpoint
    if pending is not None:
        env["ROMP_TEST_PENDING"] = json.dumps(pending)
    if displayed is not None:
        env["ROMP_TEST_DISPLAYED"] = json.dumps(displayed)
    if no_getn:
        env["ROMP_TEST_NO_GETN"] = "1"
    if href:
        env["ROMP_TEST_HREF"] = href
    if active:
        env["ROMP_TEST_ACTIVE"] = active
    if no_sw:
        env["ROMP_TEST_NO_SW"] = "1"
    if feed_off:
        env["ROMP_TEST_FEED_OFF"] = "1"
    with _tf.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_REVEAL_HARNESS + km._LANDING_REVEAL_JS + driver)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30, env=env)
    finally:
        os.unlink(path)
    assert r.returncode == 0, "the reveal script threw: " + r.stderr[:800]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _rows(snap, what):
    return [d for w, d in snap["diag"] if w == what]


class LandingRevealExecutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run_reveal(_REVEAL_DRIVER)
        cls.no_sw = _run_reveal(_BOOT_DRIVER, no_sw=True)

    def test_a_cold_start_asks_the_kernel_at_once_settles_the_row_and_strips_the_link(self):
        b = self.out["boot"]
        # boot:true — this page is booting, so its own chat pane is not connected yet; the kernel parks for it rather
        # than aiming at a same-wid socket the previous page left behind. The link's pid settles the kernel's row
        self.assertEqual(b["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}], ["/push/landed", {"pid": "PID-link-0000000001"}]])
        self.assertEqual(b["replaced"], ["/?keep=1#frag"], "only OUR params go; a reload must not replay the jump")
        self.assertEqual(b["diag"], [["deeplink", {"via": "boot", "hasSid": True, "hasCard": True, "hasPid": True, "dup": False, "controlled": True}]])
        self.assertIn(["reveal-post", {"status": 200, "via": "link", "boot": True}], b["diagAfter"])
        self.assertEqual(b["gets"], [], "no subscription on this device: the kernel's ledger is not asked")
        self.assertIn(["tap-pending", {"via": "boot", "sub": False, "rows": 0}], b["diagAfter"], "…and the boot says so")
        # a browser with no service worker at all still lands the link
        n = self.no_sw["boot"]
        self.assertEqual(n["fetches"][0], ["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}])
        self.assertEqual(_rows(n, "deeplink")[0]["controlled"], False)

    def test_the_card_waits_for_the_feeds_own_ready(self):
        b = self.out["boot"]
        self.assertEqual(b["postedBeforeReady"], 0, "no listener yet, nothing to scroll to")
        self.assertEqual(b["postedAfterTimelineReady"], 0, "another pane's ready is not the feed's")
        self.assertEqual(b["postedAfterFeedReady"], [{"romp": "revealCard", "itemId": "S1:g1", "sid": "S1"}])

    def test_the_message_for_a_tap_the_link_landed_is_a_dup_by_pid(self):
        # the cold start's two roads (the link, and the message handed to the opened window) carry one pid: ONE /reveal
        h = self.out["handed"]
        self.assertEqual((h["fetches"], h["posted"]), ([], []))
        self.assertEqual(_rows(h, "sw-message"), [{"shape": "notificationClick", "hasSid": True, "kind": "card", "dup": True, "sw": {"clients": 0, "tops": 0, "road": "open", "vis": ""}}])

    def test_a_live_tap_routes_the_same_way_and_settles_its_row(self):
        live = self.out["live"]
        self.assertEqual(live["fetches"], [["/reveal", {"sid": "S2", "wid": "W-test", "via": "sw"}], ["/push/landed", {"pid": "PID-live-0000000001"}]])
        self.assertEqual(live["posted"], [{"romp": "revealCard", "itemId": "S2:g4", "sid": "S2"}])
        self.assertEqual(live["diag"], [["sw-message", {"shape": "notificationClick", "hasSid": True, "kind": "card", "dup": False, "sw": {"clients": 3, "tops": 1, "road": "focus", "vis": "hidden"}}],
                                        ["reveal-post", {"status": 200, "via": "sw", "boot": False}]])
        d = self.out["dup"]
        self.assertEqual((d["fetches"], d["posted"]), ([], []), "the same pid again: no second /reveal, no second settle")
        self.assertEqual(_rows(d, "sw-message")[0]["dup"], True)
        for what, data in live["diag"]:
            self.assertNotIn("sid", data, "structure only: the row never carries the session id")

    def test_a_turn_focuses_without_a_card_and_a_sidless_test_lands_nowhere(self):
        self.assertEqual(self.out["turn"]["fetches"], [["/reveal", {"sid": "S3", "wid": "W-test", "via": "sw"}]], "no pid: lands, nothing to settle")
        self.assertEqual(self.out["turn"]["posted"], [])
        self.assertEqual((self.out["test"]["fetches"], self.out["test"]["posted"]), ([], []))
        self.assertEqual(_rows(self.out["test"], "sw-message"), [{"shape": "notificationClick", "hasSid": False, "kind": "test", "dup": False, "sw": {"clients": 1, "tops": 1, "road": "focus", "vis": ""}}],
                         "a sid-less tap: the row says so, and no /reveal follows")
        # the user 2026-09-06: ANY sid lands, whatever the kind; only a card adds the card scroll
        self.assertEqual(self.out["testSid"]["fetches"], [["/reveal", {"sid": "S5", "wid": "W-test", "via": "sw"}], ["/push/landed", {"pid": "PID-test-0000000005"}]])
        self.assertEqual(self.out["testSid"]["posted"], [])

    def test_an_unknown_message_shape_is_not_a_tap(self):
        # a message of another shape (here the one a worker of an older build posted) is not a tap: the shell lands what
        # the current worker says and infers nothing from an old shape
        self.assertEqual((self.out["legacy"]["fetches"], self.out["legacy"]["diag"]), ([], []))

    def test_a_refused_reveal_is_loud(self):
        r = self.out["refused"]
        self.assertEqual(r["notes"], [["error", "Could not open the session this notification was about: missing sid"]])
        self.assertEqual(r["diag"][-1], ["reveal-post", {"status": 400, "via": "sw", "boot": False}], "the refusal's status is on record beside the toast")

    def test_a_tap_before_this_pages_chat_pane_is_up_says_booting_whatever_road_brought_it(self):
        # review find (2026-09-09, on #1127): the flag follows this page's own chat pane's socket — booting until it is up,
        # so the kernel parks and the pane's ready delivers; live from then on, latched (a later drop must not re-arm a
        # park nothing would consume)
        e = self.out["earlySw"]
        self.assertEqual(e["fetches"][0], ["/reveal", {"sid": "S0", "wid": "W-test", "via": "sw", "boot": True}])
        self.assertEqual(self.out["earlySwFeedUp"]["fetches"][0], ["/reveal", {"sid": "S0", "wid": "W-test", "via": "sw", "boot": True}], "another pane's socket is not the chat pane's")
        self.assertNotIn("boot", self.out["live"]["fetches"][0][1], "once the chat pane is up, a tap is delivered live")
        self.assertEqual(self.out["afterDrop"]["fetches"][0], ["/reveal", {"sid": "S30", "wid": "W-test", "via": "sw"}], "a later drop does not re-arm the flag")


class LandingRevealWithTheFeedPaneOffHere(unittest.TestCase):
    """The user 2026-09-10: a browser with the Feed pane off in the gear's Panes section has no feed iframe loaded, so
    a card's scroll has no ready to wait for; latching it would park a card for good. The landing is the /reveal the
    script already posts, which puts the session in front in the chat. The kernel and the judges are not party to it."""
    @classmethod
    def setUpClass(cls):
        cls.out = _run_reveal(_FEED_OFF_DRIVER, feed_off=True)

    def test_a_cards_deep_link_opens_the_session_and_latches_no_card_for_a_feed_that_is_not_here(self):
        b = self.out["boot"]
        self.assertEqual(b["fetches"][0], ["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}], "the session lands in the chat as ever")
        self.assertEqual(b["postedAtBoot"], 0)
        self.assertEqual(b["postedAfterFeedReady"], [], "no card was latched: a feed's ready has nothing to flush")

    def test_a_live_card_tap_lands_the_session_and_posts_no_card(self):
        live = self.out["live"]
        self.assertEqual(live["fetches"], [["/reveal", {"sid": "S2", "wid": "W-test", "via": "sw"}], ["/push/landed", {"pid": "PID-live-0000000001"}]])
        self.assertEqual(live["posted"], [], "no revealCard into a pane that is not here")
        self.assertEqual(live["notes"], [], "and nothing to complain about: the landing succeeded")

    def test_the_pane_back_on_the_card_scroll_returns(self):
        # the feed reported ready earlier in this page's life (the driver's ready), so the scroll posts at once
        back = self.out["backOn"]
        self.assertEqual(back["posted"], [{"romp": "revealCard", "itemId": "S3:g1", "sid": "S3"}])
        self.assertIn("function revealCard(itemId,sid){if(window.__rompPaneEnabled&&!window.__rompPaneEnabled('feed'))return;", km._LANDING_REVEAL_JS)


class LandingRevealReadsTheLinkLater(unittest.TestCase):
    """2026-09-10: on Apple the deep link IS the tap — the declarative message's navigate — and iOS may navigate the EXISTING
    Home Screen window to it rather than open one. So the params are read not only at boot but whenever the page shows
    again (pageshow) or its history entry changes (popstate) with the params in the URL; landed live (the chat pane is up),
    settled by pid, stripped, and never landed twice."""
    @classmethod
    def setUpClass(cls):
        cls.out = _run_reveal(_LINK_LATER_DRIVER, href="http://localhost:7777/")

    def test_a_plain_boot_and_a_plain_pageshow_land_nothing(self):
        b = self.out["boot"]
        self.assertEqual(b["fetches"], [])
        self.assertEqual(_rows(b, "deeplink"), [{"via": "boot", "hasSid": False, "hasCard": False, "hasPid": False, "controlled": True}], "the boot row says: no link")
        p = self.out["plainShow"]
        self.assertEqual((p["fetches"], _rows(p, "deeplink"), p["replaced"]), ([], [], []), "no params: nothing filed, nothing stripped")

    def test_a_pageshow_with_the_params_lands_the_link_live_and_strips_it(self):
        s = self.out["pageshow"]
        self.assertEqual(s["fetches"], [["/reveal", {"sid": "S40", "wid": "W-test", "via": "link"}], ["/push/landed", {"pid": "PID-show-0000000040"}]],
                         "landed by the link road on a LIVE page: no boot flag; the row is settled")
        self.assertEqual(s["posted"], [{"romp": "revealCard", "itemId": "S40:g2", "sid": "S40"}], "a card kind scrolls the feed too")
        self.assertEqual(_rows(s, "deeplink"), [{"via": "pageshow", "hasSid": True, "hasCard": True, "hasPid": True, "dup": False, "controlled": True}])
        self.assertEqual(s["replaced"], ["/?t=1"], "our params stripped, the rest kept")
        a = self.out["pageshowAgain"]
        self.assertEqual((a["fetches"], _rows(a, "deeplink")), ([], []), "stripped: showing again finds nothing")

    def test_a_popstate_with_the_params_lands_too_and_a_history_walk_back_onto_it_is_a_dup(self):
        p = self.out["popstate"]
        self.assertEqual(p["fetches"], [["/reveal", {"sid": "S41", "wid": "W-test", "via": "link"}], ["/push/landed", {"pid": "PID-pop-00000000041"}]])
        self.assertEqual(_rows(p, "deeplink"), [{"via": "popstate", "hasSid": True, "hasCard": False, "hasPid": True, "dup": False, "controlled": True}])
        d = self.out["popstateDup"]
        self.assertEqual(d["fetches"], [], "the same pid again: nothing lands twice")
        self.assertEqual(_rows(d, "deeplink"), [{"via": "popstate", "hasSid": True, "hasCard": False, "hasPid": True, "dup": True, "controlled": True}])
        self.assertEqual(d["replaced"], ["/"], "…but the params are still stripped")

    def test_a_crafted_card_id_and_a_malformed_pid_are_dropped_before_they_land(self):
        c = self.out["crafted"]
        self.assertEqual(c["fetches"], [["/reveal", {"sid": "S42", "wid": "W-test", "via": "link"}]], "the session lands; the bad card id and pid do not ride")
        self.assertEqual(c["posted"], [])
        self.assertEqual(_rows(c, "deeplink"), [{"via": "pageshow", "hasSid": True, "hasCard": False, "hasPid": False, "dup": False, "controlled": True}])


class LandingRevealAsksTheLedger(unittest.TestCase):
    """The ack road (2026-09-09) and the vanish road (2026-09-10): a page with a push subscription
    asks GET /push/pending?endpoint=<its own> on boot / visible / pageshow / focus for EVERY unsettled push to this device,
    reads the screen (registration.getNotifications) and decides, per row: clicked → lands via 'ack', once by pid; shown
    and still displayed → untouched; shown and GONE → tapped, as far as the page can tell (iOS fires neither
    notificationclick nor notificationclose for a live app, so a swipe-dismiss reads the same — the trade-off the user
    accepted for a working background tap), and EXACTLY ONE such row lands via 'vanish', silently; two or more gone are
    dropped without landing; a newer same-session notification on the screen supersedes an older gone row; sent-only and a
    screen it cannot read decide nothing. No prompt, no timer. Rows go back as /push/landed, /push/superseded or
    /push/dropped, so none is left to inflate a later count."""
    EP = "https://push.example.net/send/this-device"
    LONG = "66666666-1111-2222-3333-444444444444"   # a uuid-shaped sid, so the clipped form differs from the whole
    PENDING_GET = "/push/pending?endpoint=" + "https%3A%2F%2Fpush.example.net%2Fsend%2Fthis-device"

    @classmethod
    def setUpClass(cls):
        cls.out = _run_reveal(_LEDGER_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending={"rows": []})
        clicked = {"rows": [{"pid": "PID-boot-000000001", "sid": cls.LONG, "host": "", "kind": "card", "cardId": "S60:g1", "name": "api", "stage": "clicked", "ageS": 9}]}
        cls.boot_clicked = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending=clicked)
        cls.link_clicked = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/?push-reveal=" + cls.LONG + "&push-card=S60%3Ag1&push-pid=PID-boot-000000001", endpoint=cls.EP, pending=clicked)
        shown2 = {"rows": [{"pid": "PID-bootv-000000002", "sid": "S62", "host": "", "kind": "turn", "cardId": "", "name": "tests", "stage": "shown", "ageS": 3},
                           {"pid": "PID-bootv-000000001", "sid": cls.LONG, "host": "", "kind": "turn", "cardId": "", "name": "api", "stage": "shown", "ageS": 12}]}
        cls.boot_vanished = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending=shown2, displayed=["PID-bootv-000000002"])
        cls.link_vanished = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/?push-reveal=S1&push-pid=PID-linkv-0000000001", endpoint=cls.EP, pending=shown2, displayed=["PID-bootv-000000002"])
        cls.no_getn = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending=shown2, no_getn=True)
        cls.tabs = _run_reveal(_TABS_DRIVER, href="http://localhost:7777/")

    def test_the_page_asks_for_its_own_endpoint_on_every_coming_back_and_says_what_it_heard(self):
        b = self.out["boot"]
        self.assertEqual(b["gets"], [self.PENDING_GET], "asked at boot, for THIS page's subscription")
        self.assertEqual(_rows(b, "tap-pending"), [{"via": "boot", "sub": True, "rows": 0}], "nothing pending: said so, and the screen is not read")
        self.assertEqual((b["fetches"], b["getn"]), ([], 0))
        self.assertEqual(self.out["clicked"]["gets"], [self.PENDING_GET], "…and on visible")
        self.assertEqual(self.out["two"]["gets"], [self.PENDING_GET], "…on pageshow")
        self.assertEqual(self.out["focus"]["gets"], [self.PENDING_GET], "…and on focus")
        self.assertEqual(_rows(self.out["focus"], "tap-pending"), [{"via": "focus", "sub": True, "rows": 0}])
        self.assertEqual((self.out["hidden"]["gets"], self.out["hidden"]["diag"]), ([], []), "going hidden asks nothing")
        self.assertNotIn(self.LONG, json.dumps(self.boot_clicked["boot"]["diag"]), "the session id is clipped to 8, never whole")
        self.assertNotIn(self.LONG, json.dumps(self.boot_vanished["boot"]["diag"]))

    def test_a_clicked_push_lands_by_the_ack_road_once_and_settles_the_row(self):
        c = self.out["clicked"]
        self.assertEqual(c["fetches"], [["/reveal", {"sid": "S50", "wid": "W-test", "via": "ack"}], ["/push/landed", {"pid": "PID-clicked-000001"}]],
                         "the user tapped: a jump by the same land() path, the road named; then the kernel's row is landed")
        self.assertEqual(c["posted"], [{"romp": "revealCard", "itemId": "S50:g1", "sid": "S50"}], "a card kind scrolls the feed too")
        self.assertEqual(_rows(c, "tap-pending"), [{"via": "visible", "sub": True, "rows": 1, "getNotifications": True, "displayed": 0, "vanished": 0}])
        self.assertEqual(_rows(c, "tap-pending-land"), [{"sid8": "S50", "ageS": 4, "dup": False}])
        self.assertIn(["reveal-post", {"status": 200, "via": "ack", "boot": False}], c["diag"])
        a = self.out["clickedAgain"]
        self.assertEqual(a["fetches"], [], "the same pid again is a dup: no second /reveal, no second settle")
        self.assertEqual(_rows(a, "tap-pending-land"), [{"sid8": "S50", "ageS": 4, "dup": True}])
        t = self.out["two"]
        self.assertEqual([f for f in t["fetches"] if f[0] == "/reveal"], [["/reveal", {"sid": "S52", "wid": "W-test", "via": "ack"}], ["/reveal", {"sid": "S51", "wid": "W-test", "via": "ack"}]],
                         "two taps the worker saw: both land, newest first (the kernel keeps the latest reveal)")
        self.assertEqual(sorted(f[1]["pid"] for f in t["fetches"] if f[0] == "/push/landed"), ["PID-two-00000000001", "PID-two-00000000002"])

    def test_exactly_one_vanished_notification_lands_silently_and_settles_its_row(self):
        # the one tap a live iOS app leaves for the page to see: the notification the worker showed is no longer on the screen
        v = self.out["oneVanished"]
        self.assertEqual(v["getn"], 1, "the screen is read once per check")
        self.assertEqual(v["fetches"], [["/reveal", {"sid": "S41", "wid": "W-test", "via": "vanish"}], ["/push/landed", {"pid": "PID-shown-00000001"}]],
                         "the one gone lands by the same land() path, the road named; the displayed one is untouched")
        self.assertEqual(v["posted"], [{"romp": "revealCard", "itemId": "S41:g3", "sid": "S41"}], "a card kind scrolls the feed too")
        self.assertEqual(_rows(v, "tap-pending"), [{"via": "visible", "sub": True, "rows": 2, "getNotifications": True, "displayed": 1, "vanished": 1}])
        self.assertEqual(_rows(v, "tap-vanish-land"), [{"sid8": "S41", "ageS": 45}])
        self.assertIn(["reveal-post", {"status": 200, "via": "vanish", "boot": False}], v["diag"])
        self.assertEqual(v["notes"], [], "nothing shown to the user but the landing itself")
        a = self.out["oneVanishedAgain"]
        self.assertEqual(a["fetches"], [], "the same pid again is seen: no second landing")
        self.assertEqual(_rows(a, "tap-pending"), [{"via": "visible", "sub": True, "rows": 2, "getNotifications": True, "displayed": 1, "vanished": 0}])
        self.assertEqual(_rows(a, "tap-vanish-land"), [])

    def test_two_vanished_are_settled_silently_and_all_displayed_or_sent_only_do_nothing(self):
        t = self.out["twoVanished"]
        self.assertEqual([f for f in t["fetches"] if f[0] == "/reveal"], [], "two gone at once: the tap could have been on either — nothing lands")
        self.assertEqual(t["fetches"], [["/push/dropped", {"pid": "PID-gone-000000002"}], ["/push/dropped", {"pid": "PID-gone-000000001"}]],
                         "…and both rows are spent, so they can never inflate the next check's count")
        self.assertEqual(_rows(t, "tap-pending"), [{"via": "pageshow", "sub": True, "rows": 2, "getNotifications": True, "displayed": 0, "vanished": 2}])
        self.assertEqual(_rows(t, "tap-vanish-land"), [])
        self.assertEqual((t["posted"], t["notes"]), ([], []))
        d = self.out["allDisplayed"]
        self.assertEqual(d["fetches"], [], "everything still on the screen: untouched, unsettled")
        self.assertEqual(_rows(d, "tap-pending"), [{"via": "focus", "sub": True, "rows": 2, "getNotifications": True, "displayed": 2, "vanished": 0}], "a notification without a pid is not counted")
        n = self.out["sentOnly"]
        self.assertEqual(n["fetches"], [], "never acked shown: nothing is known to have been displayed, so nothing vanished")
        self.assertEqual(_rows(n, "tap-pending"), [{"via": "visible", "sub": True, "rows": 1, "getNotifications": True, "displayed": 0, "vanished": 0}])

    def test_a_superseded_row_is_settled_as_such_and_never_read_as_a_tap(self):
        s = self.out["superseded"]
        self.assertEqual(s["fetches"], [["/push/superseded", {"pid": "PID-older-00000001"}],
                                        ["/reveal", {"sid": "S51", "wid": "W-test", "via": "vanish"}], ["/push/landed", {"pid": "PID-other-00000001"}]],
                         "the older row for the session whose newer notification is displayed was replaced, not tapped; the other session's gone row is the one tap")
        self.assertEqual(_rows(s, "tap-pending"), [{"via": "visible", "sub": True, "rows": 3, "getNotifications": True, "displayed": 1, "vanished": 1, "superseded": 1}])
        self.assertEqual(_rows(s, "tap-vanish-land"), [{"sid8": "S51", "ageS": 61}])

    def test_a_screen_the_page_cannot_read_lands_nothing_and_says_so(self):
        g = self.out["getnThrows"]
        self.assertEqual(g["fetches"], [], "getNotifications threw: a notification gone cannot be told from one never shown")
        self.assertEqual(_rows(g, "tap-pending"), [{"via": "visible", "sub": True, "rows": 1, "getNotifications": False, "displayed": -1, "vanished": 0}])
        m = self.no_getn["boot"]
        self.assertEqual(m["fetches"], [], "no getNotifications on this browser: the same")
        self.assertEqual(_rows(m, "tap-pending"), [{"via": "boot", "sub": True, "rows": 2, "getNotifications": False, "displayed": -1, "vanished": 0}])
        self.assertEqual(m["getn"], 0)

    def test_the_other_roads_settle_the_row_by_pid_so_the_ledger_never_lands_a_push_twice(self):
        m = self.out["msg"]
        self.assertEqual(m["fetches"], [["/reveal", {"sid": "S54", "wid": "W-test", "via": "sw"}], ["/push/landed", {"pid": "PID-msg-0000000001"}]], "the worker's message carries the pid: landed by the message, settled")
        self.assertEqual(self.out["msgThenLedger"]["fetches"], [])
        self.assertEqual(_rows(self.out["msgThenLedger"], "tap-pending-land"), [{"sid8": "S54", "ageS": 1, "dup": True}])
        l = self.link_clicked["boot"]
        self.assertEqual(l["fetches"], [["/reveal", {"sid": self.LONG, "wid": "W-test", "via": "link", "boot": True}], ["/push/landed", {"pid": "PID-boot-000000001"}]],
                         "the link landed the same pid first: the kernel's clicked row for it is a dup")
        self.assertEqual(_rows(l, "tap-pending-land"), [{"sid8": self.LONG[:8], "ageS": 9, "dup": True}])
        self.assertNotIn(self.LONG, json.dumps(l["diag"]), "the session id is clipped to 8, never whole")
        b = self.out["clickedBesideVanished"]
        self.assertEqual(b["fetches"], [["/reveal", {"sid": "S55", "wid": "W-test", "via": "ack"}], ["/push/landed", {"pid": "PID-both-clicked-01"}], ["/push/dropped", {"pid": "PID-both-vanish-001"}]],
                         "the tap the worker saw lands; what else vanished is spent, never a second landing")
        self.assertEqual(_rows(b, "tap-vanish-land"), [])

    def test_a_boot_lands_a_clicked_or_vanished_push_as_a_boot_and_a_deep_link_boot_outranks_the_vanish(self):
        b = self.boot_clicked["boot"]
        self.assertEqual(b["fetches"], [["/reveal", {"sid": self.LONG, "wid": "W-test", "via": "ack", "boot": True}], ["/push/landed", {"pid": "PID-boot-000000001"}]],
                         "a relaunch on the start URL: the kernel parks for this page's pane")
        self.assertEqual(_rows(b, "tap-pending"), [{"via": "boot", "sub": True, "rows": 1, "getNotifications": True, "displayed": 0, "vanished": 0}])
        self.assertEqual(_rows(b, "tap-pending-land"), [{"sid8": self.LONG[:8], "ageS": 9, "dup": False}])
        v = self.boot_vanished["boot"]
        self.assertEqual(v["fetches"], [["/reveal", {"sid": self.LONG, "wid": "W-test", "via": "vanish", "boot": True}], ["/push/landed", {"pid": "PID-bootv-000000001"}]],
                         "the one vanished lands as a boot too")
        self.assertEqual(_rows(v, "tap-vanish-land"), [{"sid8": self.LONG[:8], "ageS": 12}])
        lv = self.link_vanished["boot"]
        self.assertEqual(lv["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}], ["/push/landed", {"pid": "PID-linkv-0000000001"}], ["/push/dropped", {"pid": "PID-bootv-000000001"}]],
                         "the link is the newer word (a killed app, navigated by iOS): what else vanished is spent, never a second landing")
        self.assertEqual(_rows(lv, "tap-vanish-land"), [])
        self.assertEqual(_rows(lv, "tap-pending"), [{"via": "boot", "sub": True, "rows": 2, "getNotifications": True, "displayed": 1, "vanished": 1}])

    def test_an_unreachable_kernel_a_rowless_answer_and_junk_rows_land_nothing_and_never_throw(self):
        e = self.out["err"]
        self.assertEqual(e["fetches"], [])
        self.assertEqual(_rows(e, "tap-pending"), [{"via": "visible", "sub": True, "rows": 0, "err": True}])
        z = self.out["noRows"]
        self.assertEqual((z["fetches"], _rows(z, "tap-pending")), ([], [{"via": "visible", "sub": True, "rows": 0}]), "an answer without rows is nothing to act on")
        j = self.out["junk"]
        self.assertEqual((j["fetches"], _rows(j, "tap-pending")), ([], [{"via": "visible", "sub": True, "rows": 0}]), "a row without a pid, a sid or a stage is nobody's")

    def test_the_panes_rendered_tabs_prove_its_socket_up_when_its_message_was_missed(self):
        # 2026-09-09, the served leg of the browser test: the shell's parser yielded to the chat pane's wsState message before
        # this script existed, so every landing said booting and parked for a ready that had already come. The tabs come over
        # that very socket: an active tab in the pane's DOM is proof enough, and a landing is delivered live
        self.assertEqual(self.tabs["noTabs"]["fetches"][0], ["/reveal", {"sid": "S60", "wid": "W-test", "via": "sw", "boot": True}], "no tabs, no message: booting")
        self.assertEqual(self.tabs["tabs"]["fetches"][0], ["/reveal", {"sid": "S61", "wid": "W-test", "via": "sw"}], "tabs rendered: live, whatever this script heard")


class RailBell(unittest.TestCase):
    """The desktop rail carries the same bell as the mobile tab bar (the user 2026-08-08). Since
    2026-09-05 the pair OPENS THE POPOVER (tests/test_kernel_notify_popover.py) whose rows are the
    switches: the kernel-wide master (the user 2026-08-09's model: on = every task notifies unless
    its own bell mutes it), labelled "Notifications", and nested under it this device's push
    subscription — pulled apart so a phone turning itself off no longer silences every device, and
    nested so the master reads as the master, not as a scope beside "This device" (the user
    2026-09-05)."""

    def test_shell_serves_both_bells_and_one_flow_drives_them(self):
        status, body = _serve_get("/", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(status, 200)
        page = body.decode()
        self.assertIn("id=rail-bell hidden", page, "the bells ship hidden; the wiring reveals them at boot")
        self.assertIn("id=mbell hidden", page, "the mobile bell is unchanged")
        # ONE wiring drives the pair — reveal, paint and busy all iterate the same list — so the
        # two bells can never disagree about the master's state
        self.assertIn("querySelectorAll('#mbell,#rail-bell')", page)
        self.assertNotIn("getElementById('mbell')", page, "the single-bell wiring is gone")
        # the rail bell paints its states exactly like the mobile one
        self.assertIn(".rail-acts #rail-bell.on{color:var(--accent)}", page)
        self.assertIn(".rail-acts #rail-bell.busy{opacity:.45}", page)
        # the on-state must survive the light theme, whose .rail-act recolor outspecifies the bare
        # `.on` rule — with no light restatement on and off rendered pixel-identical there (the
        # user 2026-09-02)
        self.assertIn("body.theme-light .rail-act.on{color:var(--accent)}", page)
        # …and OFF reads as a slashed bell — the app's one bell-off idiom (feed card bell, timeline
        # lane bell) — never a color difference alone
        self.assertEqual(page.count("class='bell-slash'"), 2, "both bells carry the slash glyph")
        self.assertIn(".bell-slash{display:none}", page)
        self.assertIn("#rail-bell:not(.on) .bell-slash,#mbell:not(.on) .bell-slash{display:block}", page)

    def test_the_bell_opens_the_popover_whose_rows_are_the_switches(self):
        # (2026-09-05: was "the bell is the master switch, not a device toggle" — the tap now opens
        # the popover; the Notifications row is the master and This-device, nested under it, is the
        # subscription. The pin moved from "All devices" the same day: that label read as a scope
        # choice, not the switch the rest sit under)
        _, body = _serve_get("/", headers={"X-Romp-Token": km.TOKEN})
        page = body.decode()
        # kernel-authoritative paint of the master: GET /notify-all at boot and the shell WS push
        # on every toggle, so every dashboard's row agrees
        self.assertIn("fetch('/notify-all')", page)
        self.assertIn("window.__rompNotifyAllPaint", page)
        self.assertIn("m.type==='notifyAll'", page, "the shell WS repaints every open dashboard")
        self.assertIn("post('/notify-all',{on:want})", page)
        self.assertIn("id=rbell-pop", page)
        self.assertIn("data-act=all", page)
        self.assertIn("data-act=dev", page)
        # the bell shows everywhere — the master matters even where the Push API is missing (the
        # kernel box still gets osascript, other devices still buzz); only the device row gates
        self.assertNotIn("('Notification' in window))return", page,
                         "the old whole-bell capability bail is gone")
        self.assertIn("var canPush=", page)
        # the permission ask still runs in the tap's own stack (iOS voids the gesture across awaits)
        self.assertIn("Notification.requestPermission():null", page)
        # the glyph is THIS device's truth now: master AND subscribed (tests/test_kernel_notify_popover.py)
        self.assertIn("var lit=isOn&&(canPush?devOn:true)", page)


class MasterBellRoute(unittest.TestCase):
    """GET/POST /notify-all — the master bell's kernel half (the user 2026-08-09). Live server for
    the POST (the SubscribeRoutes pattern: a fake socket cannot exercise Content-Length reads)."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        try:
            (jd.STATE / "notify-cards.json").unlink()
        except OSError:
            pass
        km._notify_cards_cache.clear()

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

    def test_get_is_gated_and_reads_the_store(self):
        status, _ = _serve_get("/notify-all")
        self.assertEqual(status, 403, "the master state is behind the serve token like every page")
        status, body = _serve_get("/notify-all", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual((status, json.loads(body)), (200, {"on": False}))

    def test_post_flips_and_broadcasts(self):
        sent = []
        with mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            code, body = self._post("/notify-all", {"on": True})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True, "on": True}))
        self.assertTrue(km._notify_all_on())
        self.assertIn(("shell", {"type": "notifyAll", "on": True}), sent,
                      "every open dashboard's bell repaints on the toggle, not just the clicker's")
        _, body = _serve_get("/notify-all", headers={"X-Romp-Token": km.TOKEN})
        self.assertEqual(json.loads(body), {"on": True})
        code, _ = self._post("/notify-all", {"on": False})
        self.assertEqual(code, 200)
        self.assertFalse(km._notify_all_on())

    def test_post_requires_token_and_refuses_garbage(self):
        code, _ = self._post("/notify-all", {"on": True}, token=False)
        self.assertEqual(code, 403)
        code, _ = self._post("/notify-all", None, raw=b"not json")
        self.assertEqual(code, 400)
        self.assertFalse(km._notify_all_on(), "a refused body must not flip the master")


if __name__ == "__main__":
    unittest.main()
