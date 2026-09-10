#!/usr/bin/env python3
"""Web Push for the bell events (plans/ios-app.md proposal 2).

Covers the four layers separately, so a failure names its layer:

  * routes — /sw.js and /push/vapid-key are token-gated (they serve to the authed shell only);
    POST /push/subscribe validates, stores at 0600, and refuses loudly when the crypto
    dependency is missing; /push/unsubscribe prunes.
  * the worker — push + notificationclick ONLY. A fetch handler would fight the stale-bundle
    machinery (?v= cache-bust + the rstale banner), which assumes the network serves every load.
  * crypto — RFC 8291 aes128gcm round-trip: encrypt with the kernel's writer, decrypt with an
    independent receiver-side derivation from a browser keypair minted HERE, at run time (no
    credential-shaped literals in fixtures — repo rule). RFC 8292 VAPID: parse the header, verify
    the ES256 signature against the advertised key, check the claims.
  * the sink — _push_notify mirrors (title, body) to every subscription, sends the card gist and
    NOTHING more, prunes on the dead-subscription signal, and stands down silently when no
    device ever subscribed.

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
    for name in ("push-subscriptions.json", "push-vapid.json"):
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
        self.assertNotIn("fetch(", js)
        # the Cache API appears since 2026-09-09, but as a one-slot STORE for the last tap (the 'romp-tap'
        # cache, written by the worker and read by the page), never as a cache of anything the page loads:
        # the global is aliased once, and every use opens that one named cache
        code = "\n".join(l for l in js.splitlines() if not l.lstrip().startswith("//"))   # the prose may name it
        self.assertIn("cs=(typeof caches!=='undefined')?caches:null", code)
        self.assertNotIn("caches", code.replace("cs=(typeof caches!=='undefined')?caches:null", ""))
        self.assertNotIn("addAll", code)
        self.assertEqual(code.count("cs.open("), code.count("cs.open(TAPC)"), "no cache but the tap store")
        self.assertGreater(code.count("cs.open(TAPC)"), 0)
        # an UPDATED worker must take over immediately — this one owns no caches, so 'waiting'
        # only delays fixes (the sid-blind predecessor kept handling taps, the user 2026-08-08)
        self.assertIn("skipWaiting()", js)
        self.assertIn("clients.claim()", js)

    def test_sw_carries_this_builds_fingerprint_string(self):
        # 2026-09-09, the warm-app round: the served worker bakes the kernel's build string into SWV — the SAME
        # string the shell page carries as PAGEV — so a page can say whether the worker on this device is its own
        # build (the trail could not: a tap that ran an older worker and a tap that never reached the worker
        # looked identical). The raw source keeps a placeholder inside a string literal, so the node harness runs
        # it unbaked; the string is sha + dist token, so a deploy and a bundle rebuild both move it
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
        self.assertIn("PAGEV='%s'" % v, km._landing(), "the page bakes the same string")
        self.assertIn("PAGEV='__ROMP_SWV__'", km._LANDING_REVEAL_JS)

    def test_sw_click_lands_on_the_session_that_fired(self):
        # the user 2026-08-08: the first real push opened the app on a DIFFERENT session. The
        # kernel's routing block rides the notification's data; a live window gets it over
        # postMessage, a cold start gets the kernel's deep link (ServiceWorkerExecutes runs it).
        _, body = _serve_get("/sw.js", headers={"X-Romp-Token": km.TOKEN})
        js = body.decode()
        self.assertIn("data:(d.data&&typeof d.data==='object')?d.data:{sid:d.sid||''}", js,
                      "the routing block verbatim; an older kernel's flat sid still lands")
        self.assertIn("opts.tag=d.tag;opts.renotify=!d.quiet", js, "one notification per session, still audible unless it is the quiet card push that yields the buzz")
        self.assertIn("if(d.quiet)opts.silent=true", js, "a quiet push carries the badge without re-alerting")
        self.assertIn("romp:'notificationClick'", js)
        self.assertIn("clients.openWindow(url)", js)
        self.assertIn("/?push-reveal=", js)              # the fallback deep link for a data block without one
        self.assertNotIn("setTimeout", js)                # event-based end to end — no timers in the worker
        # ...and the closed-app badge count comes from the payload
        self.assertIn("setAppBadge", js)


# The worker, EXECUTED (the test_error_center.py pattern): node runs _SW_JS against stubs of the
# ServiceWorker globals and the driver replays a push and four taps, logging every call in order.
# A source pin says the words are there; this says the sequence is right: close → matchAll →
# focus + postMessage, openWindow only when no window exists or focus() refused, all under waitUntil.
_SW_HARNESS = r"""
'use strict';
const H = {};            // event name -> the worker's handler
const LOG = [];          // every call the worker makes, in order
const META = [];         // per posted message: the tap id and the worker's diag block (2026-09-08), kept apart
                         // from LOG so the routing block still compares whole — the id is minted per tap
function strip(m) { if (!m || typeof m !== 'object') return m; const c = Object.assign({}, m);
  META.push({ id: c.id, diag: c.diag }); delete c.id; delete c.diag; return c; }
global.self = {
  addEventListener: (k, f) => { H[k] = f; },
  skipWaiting: () => {},
  registration: { showNotification: (title, opts) => { LOG.push(['show', title, opts]); return Promise.resolve(); },
                  update: () => { LOG.push(['update']); return Promise.resolve(); } },
  navigator: {},         // no setAppBadge here: the numeric-only badge rule has its own pin
};
global.clients = {
  claim: () => Promise.resolve(),
  matchAll: (q) => { LOG.push(['matchAll', q]); return Promise.resolve([]); },
  openWindow: (u) => { LOG.push(['openWindow', u]); return Promise.resolve({}); },
};
// the tap store (2026-09-09): the Cache API both the worker and the window can read, as an in-memory Map
// keyed by request URL. SLOG records each op beside how far LOG had got, so a test can say the write came
// BEFORE the matchAll — kept out of LOG so the road sequences above still compare whole. match() hands back
// a clone, as the real API does (a Response body reads once). cacheFail: a storage that refuses to open.
const STORE = new Map(), SLOG = [];
let cacheFail = false;
const cacheObj = {
  put: (k, r) => { SLOG.push(['put', String(k), LOG.length]); STORE.set(String(k), r); return Promise.resolve(); },
  match: (k) => { SLOG.push(['match', String(k), LOG.length]); const r = STORE.get(String(k)); return Promise.resolve(r ? r.clone() : undefined); },
  delete: (k) => { SLOG.push(['delete', String(k), LOG.length]); return Promise.resolve(STORE.delete(String(k))); },
};
global.caches = { open: (n) => { SLOG.push(['open', n, LOG.length]); return cacheFail ? Promise.reject(new Error('no storage')) : Promise.resolve(cacheObj); },
                  match: (k) => cacheObj.match(k) };
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
// a top-level client with the state a real WindowClient reports (2026-09-08): how visible it is after
// focus(), its creation URL, and a navigate method that LOGS if the worker ever calls it (the reload road it
// served was removed 2026-09-09; nav:false is a browser without the method)
function stateful(o) {
  const w = { frameType: 'top-level', visibilityState: o.vis, url: o.url,
              focus: () => { LOG.push(['focus']); return Promise.resolve(w); },
              postMessage: (m) => LOG.push(['post', strip(m)]) };
  if (o.nav !== false) w.navigate = (u) => { LOG.push(['navigate', u]); return Promise.resolve(w); };
  return w;
}
async function tap(data, windows) {
  LOG.length = 0;
  const waited = [];
  global.clients.matchAll = (q) => { LOG.push(['matchAll', q]); return Promise.resolve(windows); };
  H.notificationclick({ notification: { close: () => LOG.push(['close']), data }, waitUntil: (p) => waited.push(p) });
  for (const p of waited) await p;
  return { log: LOG.slice(), waited: waited.length };
}
(async () => {
  const out = {};
  const data = { sid: 'S1', host: '', kind: 'card', cardId: 'S1:g1', url: '/?push-reveal=S1&push-card=S1%3Ag1' };
  let pushWait = null;
  H.push({ data: { json: () => ({ title: 'romp: web', body: 'Needs you: x', sid: 'S1', tag: 'romp:S1', badge: 2, data }) },
           waitUntil: (p) => { out.pushWaited = true; pushWait = p; } });
  out.pushSync = LOG.slice();          // before the handler yields: the show, and nothing racing it
  await pushWait;                      // the whole push, as the browser waits for it
  out.push = LOG.slice();
  LOG.length = 0;
  H.push({ data: { json: () => ({ title: 't', body: 'b', sid: 'S9' }) }, waitUntil: (p) => { pushWait = p; } });   // an older kernel's flat payload
  out.pushLegacy = LOG.slice();
  await pushWait;                      // its update lands here, not inside the next tap's log
  out.live = await tap(data, [win(true)]);
  out.cold = await tap(data, []);
  out.refused = await tap(data, [win(false)]);
  out.test = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/' }, []);
  const testSid = { sid: 'S5', host: '', kind: 'test', cardId: '', url: '/?push-reveal=S5' };   // a test addressed to the session in front (2026-09-06)
  out.testLive = await tap(testSid, [win(true)]);
  out.testCold = await tap(testSid, []);
  out.legacyTap = await tap({ sid: 'S7' }, []);            // a notification an older worker showed: flat sid, no url
  // the phone (2026-09-06): the user last tapped INSIDE the chat pane (the mobile session picker), so the
  // chat iframe is the most recently focused client — ahead of the shell that carries the reveal listener
  const fed = { sid: 'boxa:S8', host: 'boxa', kind: 'test', cardId: '', url: '/?push-reveal=boxa%3AS8' };
  out.nested = await tap(fed, [frame('chat', 'nested'), frame('feed', 'nested'), frame('shell', 'top-level')]);
  out.nestedOnly = await tap(fed, [frame('chat', 'nested')]);   // a pane with no shell above it: nothing to post to
  out.untyped = await tap(fed, [frame('old', undefined)]);       // a browser that reports no frameType is a window
  // the cold start's second road (2026-09-08): the window openWindow hands back is given the routing block too
  const opened = { postMessage: (m) => LOG.push(['post', 'opened', strip(m)]) };
  const openWindow0 = global.clients.openWindow;
  global.clients.openWindow = (u) => { LOG.push(['openWindow', u]); return Promise.resolve(opened); };
  out.coldHanded = await tap(data, []);
  out.coldHandedMeta = META[META.length - 1];
  out.testHanded = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/' }, []);   // nothing to land on: nothing posted
  out.refusedHanded = await tap(data, [win(false)]);               // focus refused, then the opened window is told
  out.refusedHandedMeta = META[META.length - 1];
  global.clients.openWindow = (u) => { LOG.push(['openWindow', u]); return Promise.resolve(null); };
  out.coldNull = await tap(data, []);                              // no client back: the link alone, no throw
  global.clients.openWindow = openWindow0;
  // the worker's own trail rides each message (2026-09-08): what it saw and which road it took
  META.length = 0;
  out.live2 = await tap(data, [win(true)]);
  out.liveMeta = META[0];
  META.length = 0;
  out.nested2 = await tap(fed, [frame('chat', 'nested'), frame('feed', 'nested'), frame('shell', 'top-level')]);
  out.nestedMeta = META[0];
  // no reload road (review find, 2026-09-09, on #1127; the 'last resort' of 2026-09-08 set a hidden focused client's
  // URL to the deep link): a top-level client that still reports hidden after focus() is TOLD like any other and
  // never navigated, whatever its creation URL, with or without the method, sid or no sid
  out.hidden = await tap(data, [stateful({ vis: 'hidden', url: 'https://romp.test/' })]);
  out.hiddenLinked = await tap(data, [stateful({ vis: 'hidden', url: 'https://romp.test/?push-reveal=S1' })]);
  out.visible = await tap(data, [stateful({ vis: 'visible', url: 'https://romp.test/' })]);
  out.hiddenNoNav = await tap(data, [stateful({ vis: 'hidden', url: 'https://romp.test/', nav: false })]);
  out.hiddenNoSid = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/' }, [stateful({ vis: 'hidden', url: 'https://romp.test/' })]);
  META.length = 0;
  await tap(data, [stateful({ vis: 'hidden', url: 'https://romp.test/' })]);
  out.hiddenMeta = META[0];
  // the kept tap (2026-09-08): a shell that boots or comes back asks for it; the tap stays until a shell
  // says THAT tap landed; a sid-less tap keeps nothing
  META.length = 0;
  await tap(testSid, [win(true)]);
  const keptId = META[0].id;
  const src = { postMessage: (m) => LOG.push(['replay', strip(m)]) };
  function ask(m) { LOG.length = 0; H.message({ data: m, source: src }); return LOG.slice(); }
  out.replay = ask({ romp: 'tapReplay' });
  out.replayWrongAck = (ask({ romp: 'tapLanded', id: 'someone-else' }), ask({ romp: 'tapReplay' }));
  out.replayAfterAck = (ask({ romp: 'tapLanded', id: keptId }), ask({ romp: 'tapReplay' }));
  await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/' }, [win(true)]);
  out.replaySidless = ask({ romp: 'tapReplay' });
  out.replayNoSource = (LOG.length = 0, H.message({ data: { romp: 'tapReplay' }, source: null }), LOG.slice());   // a message with no sender: nothing to answer, no throw
  out.keptId = keptId;
  // the stored tap (2026-09-09): written where the PAGE can read it, BEFORE any focus or open, whatever road
  // the tap then takes; a sid-less tap is not stored (nowhere to land); the ack retires the entry only when
  // it names the stored tap; a storage that refuses still lets the tap land
  const rec = async () => (STORE.has('/__romp/tap') ? STORE.get('/__romp/tap').clone().json() : null);
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));   // the acks above were fired without waiting: let their store work finish
  STORE.clear(); SLOG.length = 0; META.length = 0;
  out.storeCold = await tap(data, []);
  out.storeColdOps = SLOG.slice();
  out.storeColdRec = await rec();
  STORE.clear(); SLOG.length = 0; META.length = 0;
  out.storeLive = await tap(data, [win(true)]);
  out.storeLiveOps = SLOG.slice();
  out.storeLiveRec = await rec();
  out.storeLiveId = META[0].id;
  STORE.clear(); SLOG.length = 0;
  out.storeSidless = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/' }, []);
  out.storeSidlessKeys = [...STORE.keys()];
  out.storeSidlessOps = SLOG.slice();
  STORE.clear(); META.length = 0;
  await tap(testSid, []);
  const storedId = (await rec()).id;
  const ackWaits = [];
  const ackMsg = (m) => { ackWaits.length = 0; H.message({ data: m, source: src, waitUntil: (p) => ackWaits.push(p) }); return Promise.all(ackWaits); };
  await ackMsg({ romp: 'tapLanded', id: 'someone-else' });
  out.ackOtherKeeps = [...STORE.keys()];
  await ackMsg({ romp: 'tapLanded', id: storedId });
  out.ackOwnDeletes = [...STORE.keys()];
  out.ackWaited = ackWaits.length;
  STORE.clear(); cacheFail = true;
  out.storeFail = await tap(data, []);
  cacheFail = false;
  // THE FINGERPRINT AND THE SHOWN RECORD (2026-09-09, the warm-app round): install writes a fresh record for this
  // build over the previous build's, activate its takeover; every push stamps its arrival and writes the notification
  // it shows to '/__romp/shown' BEFORE the show is attempted; every click stamps itself FIRST, counts, and a
  // session-addressed one retires the shown record (the tap wins); the ack naming the shown id retires it too
  const fpRec = async () => (STORE.has('/__romp/sw') ? STORE.get('/__romp/sw').clone().json() : null);
  const shownRec = async () => (STORE.has('/__romp/shown') ? STORE.get('/__romp/shown').clone().json() : null);
  const life = async (k) => { const w = []; H[k]({ waitUntil: (p) => w.push(p) }); await Promise.all(w); return w.length; };
  STORE.clear(); SLOG.length = 0; LOG.length = 0;
  STORE.set('/__romp/sw', new Response(JSON.stringify({ version: 'older', installedAt: 1, activatedAt: 2, lastPushAt: 3, lastPushSid: 'S0', lastClickAt: 4, lastClickSid: 'S0', clicks: 7 })));
  out.installWaited = await life('install');
  out.fpInstalled = await fpRec();
  out.activateWaited = await life('activate');
  out.fpActivated = await fpRec();
  SLOG.length = 0; LOG.length = 0;
  const named = Object.assign({ name: 'web' }, data);
  H.push({ data: { json: () => ({ title: 'romp: web', body: 'Needs you: x', sid: 'S1', tag: 'romp:S1', badge: 2, data: named }) }, waitUntil: (p) => { pushWait = p; } });
  out.pushOpsBeforeShow = SLOG.filter((x) => x[2] === 0).map((x) => x.slice(0, 2));   // LOG.length 0: started before the show was logged
  out.pushSync2 = LOG.map((x) => x[0]);
  await pushWait;
  out.fpPushed = await fpRec();
  out.shownAfterPush = await shownRec();
  // a show that FAILS still leaves the record and the stamp (headless browsers refuse showNotification)
  const showOk = global.self.registration.showNotification;
  global.self.registration.showNotification = (t, o) => { LOG.push(['show', t, o]); return Promise.reject(new Error('denied')); };
  SLOG.length = 0; LOG.length = 0;
  H.push({ data: { json: () => ({ title: 'api', body: 'finished', sid: 'S2', tag: 'romp:S2', data: { sid: 'S2', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S2', name: 'api' } }) }, waitUntil: (p) => { pushWait = p; } });
  out.pushRefused = await pushWait.then(() => 'resolved', (e) => 'rejected: ' + e.message);
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
  out.shownAfterRefused = await shownRec();
  out.fpAfterRefused = await fpRec();
  global.self.registration.showNotification = showOk;
  // a sid-less push (a test with no session in front) stamps the arrival but has nothing to offer
  SLOG.length = 0; LOG.length = 0;
  H.push({ data: { json: () => ({ title: 'romp', body: 'Test notification', tag: 'romp:test', data: { sid: '', host: '', kind: 'test', cardId: '', url: '/', name: '' } }) }, waitUntil: (p) => { pushWait = p; } });
  await pushWait;
  out.shownAfterSidless = await shownRec();
  out.fpAfterSidless = await fpRec();
  STORE.set('/__romp/shown', new Response(JSON.stringify({ id: 'N-1', sid: 'S1', host: '', kind: 'card', cardId: 'S1:g1', url: data.url, name: 'web', t: 1 })));
  SLOG.length = 0;
  out.clickSidless = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/' }, []);
  out.clickSidlessOps = SLOG.map((x) => x.slice(0, 3));
  out.shownAfterSidlessClick = await shownRec();
  out.fpAfterSidlessClick = await fpRec();
  SLOG.length = 0;
  out.clickStamp = await tap(data, [win(true)]);
  out.clickStampOps = SLOG.map((x) => x.slice(0, 3));
  out.shownAfterClick = await shownRec();
  out.fpAfterClick = await fpRec();
  STORE.set('/__romp/shown', new Response(JSON.stringify({ id: 'N-2', sid: 'S3', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S3', name: 'tests', t: 1 })));
  await ackMsg({ romp: 'tapLanded', id: 'someone-else' });
  out.shownAckOther = await shownRec();
  await ackMsg({ romp: 'tapLanded', id: 'N-2' });
  out.shownAckOwn = await shownRec();
  out.ackShownWaited = ackWaits.length;
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
    MSG = {"romp": "notificationClick", "sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1"}
    URL = "/?push-reveal=S1&push-card=S1%3Ag1"

    def test_push_shows_the_gist_and_keeps_the_routing_block_verbatim(self):
        show = self.out["push"][0]
        self.assertEqual(show[:2], ["show", "romp: web"])
        opts = show[2]
        self.assertEqual(opts["body"], "Needs you: x")
        self.assertEqual(opts["data"], {"sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1", "url": self.URL})
        self.assertEqual((opts["tag"], opts["renotify"]), ("romp:S1", True), "same session → replaces, still buzzes")
        self.assertTrue(self.out["pushWaited"])
        # an older kernel's flat payload still lands on its sid, and wears no tag it did not send
        legacy = self.out["pushLegacy"][0][2]
        self.assertEqual(legacy["data"], {"sid": "S9"})
        self.assertNotIn("tag", legacy)

    def test_the_push_refreshes_the_worker_once_the_notification_shows(self):
        # the phone (2026-09-08): an installed app left in the background checks for a new worker only on
        # a navigation, so every tap after a deploy ran the OLD handler. The push itself now asks for the
        # update — after the show, so the notification a push must produce is never raced by the takeover
        self.assertEqual([x[0] for x in self.out["pushSync"]], ["show"])
        self.assertEqual([x[0] for x in self.out["push"]], ["show", "update"])
        self.assertEqual([x[0] for x in self.out["pushLegacy"]], ["show"], "the legacy payload's push refreshes too, after its show")

    def test_a_cold_start_also_hands_the_opened_window_the_routing_block(self):
        # a message to a window whose page has no listener yet is held by the browser until the shell adds
        # one — the second road for a browser that opens the app on its start URL instead of the link
        self.assertEqual(self.out["coldHanded"]["log"], [["close"], self.MATCH, ["openWindow", self.URL], ["post", "opened", self.MSG]])
        self.assertEqual(self.out["coldNull"]["log"], [["close"], self.MATCH, ["openWindow", self.URL]])
        self.assertEqual(self.out["testHanded"]["log"], [["close"], self.MATCH, ["openWindow", "/"]])

    def test_a_live_window_is_focused_and_told_never_reopened(self):
        live = self.out["live"]
        self.assertEqual(live["waited"], 1, "the whole tap rides one waitUntil")
        self.assertEqual(live["log"], [["close"], self.MATCH, ["focus"], ["post", self.MSG]])

    def test_no_window_opens_the_deep_link(self):
        self.assertEqual(self.out["cold"]["log"], [["close"], self.MATCH, ["openWindow", self.URL]])
        self.assertEqual(self.out["cold"]["waited"], 1)

    def test_a_refused_focus_falls_through_to_open_window(self):
        # the installed-app case: focus() rejects — the tap must still land somewhere
        self.assertEqual(self.out["refused"]["log"], [["close"], self.MATCH, ["focus"], ["openWindow", self.URL]])

    def test_a_test_notification_just_opens_romp(self):
        # …when it carries no session: the button was pressed with no session in front
        self.assertEqual(self.out["test"]["log"], [["close"], self.MATCH, ["openWindow", "/"]])

    def test_a_test_addressed_to_a_session_lands_on_it_like_a_turn(self):
        # the user 2026-09-06: the test carries the session the button was pressed on. The worker
        # does not branch on kind — the same focus + routing block live, the same deep link cold
        msg = {"romp": "notificationClick", "sid": "S5", "host": "", "kind": "test", "cardId": ""}
        self.assertEqual(self.out["testLive"]["log"], [["close"], self.MATCH, ["focus"], ["post", msg]])
        self.assertEqual(self.out["testCold"]["log"], [["close"], self.MATCH, ["openWindow", "/?push-reveal=S5"]])

    def test_a_notification_from_the_previous_worker_still_lands(self):
        self.assertEqual(self.out["legacyTap"]["log"][-1], ["openWindow", "/?push-reveal=S7"])

    def test_the_tap_is_posted_to_the_shell_never_into_a_pane_iframe(self):
        # the user 2026-09-06, on the phone: the tap did nothing. matchAll lists the dashboard's
        # same-origin pane iframes as window clients too, most-recently-focused first — and the
        # chat pane the user had just switched sessions in was first. Only the top-level shell
        # listens for the worker's message; posting into the pane dropped the tap on the floor.
        msg = {"romp": "notificationClick", "sid": "boxa:S8", "host": "boxa", "kind": "test", "cardId": ""}
        self.assertEqual(self.out["nested"]["log"],
                         [["close"], self.MATCH, ["focus", "shell"], ["post", "shell", msg]])
        # no top-level client at all → the deep link, exactly as with no window
        self.assertEqual(self.out["nestedOnly"]["log"], [["close"], self.MATCH, ["openWindow", "/?push-reveal=boxa%3AS8"]])
        # a client that reports no frameType is treated as a window, never dropped
        self.assertEqual(self.out["untyped"]["log"][-1], ["post", "old", msg])

    def test_every_tap_carries_an_id_and_the_workers_own_trail(self):
        # 2026-09-08: the app came forward, no /reveal left the phone, and nothing said what the worker had
        # seen. Each message now wears a per-tap id (the shell lands a tap once whichever roads deliver it)
        # and a diag block the shell files beside its own rows: clients seen, top-level among them, the road
        # taken, the target's visibility (the stubs report none)
        live, nested = self.out["liveMeta"], self.out["nestedMeta"]
        self.assertTrue(live["id"] and isinstance(live["id"], str))
        self.assertEqual(live["diag"], {"clients": 1, "tops": 1, "road": "focus", "vis": ""})
        self.assertEqual(nested["diag"], {"clients": 3, "tops": 1, "road": "focus", "vis": ""})
        self.assertNotEqual(live["id"], nested["id"], "minted per tap")
        self.assertEqual(self.out["coldHandedMeta"]["diag"], {"clients": 0, "tops": 0, "road": "open", "vis": ""})
        self.assertEqual(self.out["refusedHanded"]["log"], [["close"], self.MATCH, ["focus"], ["openWindow", self.URL], ["post", "opened", self.MSG]])
        self.assertEqual(self.out["refusedHandedMeta"]["diag"]["road"], "open-after-refused")

    def test_a_hidden_top_level_client_is_told_like_any_other_and_never_navigated(self):
        # review find (2026-09-09, on #1127): the 'last resort' of 2026-09-08 set a focused client's URL to the deep
        # link when it STILL reported hidden after focus(), a visibility heuristic standing in for liveness. A live
        # dashboard can report hidden in the very frame focus() resolves (the flip to visible lands after), and the
        # road was a full page load of it, every pane's state gone, on every tap in that state. Gone: the client is
        # told, its visibility rides the trail, and the roads for a page that missed the message are the replay
        # and the stored tap. No road of the worker's loads a page
        told = [["close"], self.MATCH, ["focus"], ["post", self.MSG]]
        for k in ("hidden", "hiddenLinked", "visible", "hiddenNoNav"):
            self.assertEqual(self.out[k]["log"], told, k)
            self.assertEqual(self.out[k]["waited"], 1, k)
        self.assertEqual([x[0] for x in self.out["hiddenNoSid"]["log"]], ["close", "matchAll", "focus", "post"])
        self.assertEqual(self.out["hiddenMeta"]["diag"], {"clients": 1, "tops": 1, "road": "focus", "vis": "hidden"}, "what the worker saw is still on the trail")
        self.assertNotIn(".navigate(", km._SW_JS)

    def test_the_kept_tap_replays_until_a_shell_says_it_landed(self):
        # a page suspended in the background can miss a message posted before it resumed; a page the browser
        # evicted and relaunched never saw one. The worker keeps the last session-addressed tap; a shell that
        # asks (at boot, on becoming visible) gets it; only an ack naming THAT tap retires it
        msg = {"romp": "notificationClick", "sid": "S5", "host": "", "kind": "test", "cardId": ""}
        self.assertEqual(self.out["replay"], [["replay", msg]])
        self.assertEqual(self.out["replayWrongAck"], [["replay", msg]], "an ack for another tap changes nothing")
        self.assertEqual(self.out["replayAfterAck"], [], "acked → nothing left to replay")
        self.assertEqual(self.out["replaySidless"], [], "a sid-less tap keeps nothing: nowhere to land")
        self.assertEqual(self.out["replayNoSource"], [])
        self.assertTrue(self.out["keptId"])

    def test_the_tap_is_written_where_the_page_can_read_it_before_any_focus_or_open(self):
        # 2026-09-09, the phone with the app alive in the BACKGROUND: the worker saw no client at all (iOS lists
        # no window for a backgrounded Home Screen app), took the openWindow road, iOS brought the EXISTING page
        # forward without a load — no link, no message — and ended the worker, `pending` with it, before the
        # page asked for the replay. So the tap is also written to the Cache API the page shares: one entry,
        # '/__romp/tap' in 'romp-tap', {id, sid, host, kind, cardId, url, t} — written and AWAITED before the
        # matchAll, so the write completes before iOS moves on, whatever road the tap then takes
        o = self.out
        self.assertEqual(o["storeCold"]["log"], [["close"], self.MATCH, ["openWindow", self.URL]], "the tap still opens as before")
        self.assertEqual(o["storeCold"]["waited"], 1, "one waitUntil carries write and tap")
        # the fingerprint's stamp and the shown record's retirement share the cache since 2026-09-09 (their own
        # test below); the TAP entry's ops are what this pin is about
        tap_ops = [x for x in o["storeColdOps"] if x[1] == "/__romp/tap"]
        self.assertEqual([x[:2] for x in tap_ops], [["put", "/__romp/tap"]])
        self.assertEqual(o["storeColdOps"][0][:2], ["open", "romp-tap"])
        self.assertTrue(all(x[2] == 1 for x in tap_ops), "written while LOG held only the close: before the matchAll — " + repr(o["storeColdOps"]))
        r = o["storeColdRec"]
        self.assertEqual({k: r[k] for k in ("sid", "host", "kind", "cardId", "url")},
                         {"sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1", "url": self.URL})
        self.assertRegex(r["id"], r"^\d+-[a-z0-9]+$", "the per-tap id the message would carry")
        self.assertIsInstance(r["t"], (int, float))
        # a live window is told AND the entry is written: a page the browser suspended can miss the message
        self.assertEqual(o["storeLive"]["log"], [["close"], self.MATCH, ["focus"], ["post", self.MSG]])
        self.assertEqual(o["storeLiveRec"]["id"], o["storeLiveId"])
        self.assertTrue(all(x[2] == 1 for x in o["storeLiveOps"] if x[1] == "/__romp/tap"), "before the matchAll here too")
        # a sid-less tap has nowhere to land: no tap written, the tap entry untouched (the click's own fingerprint
        # stamp is the one op it makes — every click leaves that trace, by design)
        self.assertEqual([k for k in o["storeSidlessKeys"] if k != "/__romp/sw"], [])
        self.assertEqual([x for x in o["storeSidlessOps"] if x[1] in ("/__romp/tap", "/__romp/shown")], [])
        self.assertEqual(o["storeSidless"]["log"], [["close"], self.MATCH, ["openWindow", "/"]])

    def test_the_ack_retires_the_entry_only_for_the_tap_it_names_and_a_refusing_store_never_blocks_the_tap(self):
        o = self.out
        self.assertEqual([k for k in o["ackOtherKeeps"] if k != "/__romp/sw"], ["/__romp/tap"], "an ack for another tap leaves the entry")
        self.assertEqual([k for k in o["ackOwnDeletes"] if k != "/__romp/sw"], [], "the ack naming the stored tap deletes it")
        self.assertEqual(o["ackWaited"], 1, "the delete rides the message event's waitUntil")
        self.assertEqual(o["storeFail"]["log"], [["close"], self.MATCH, ["openWindow", self.URL]], "storage refused: the tap lands by the other roads")
        self.assertEqual(o["storeFail"]["waited"], 1)

    def test_the_worker_leaves_a_fingerprint_the_page_can_read(self):
        # 2026-09-09, the warm-app round: three taps, no [reveal], no worker message, an empty store on every
        # resume — and no way to tell an older worker that never wrote the store from a tap iOS delivered past
        # the worker altogether. So the worker writes WHICH worker ran and WHAT it saw: '/__romp/sw' beside the
        # tap, {version (baked at serve time), installedAt, activatedAt, lastPushAt, lastPushSid, lastClickAt,
        # lastClickSid, clicks}. install starts a fresh record for the new build over the old one's
        o = self.out
        self.assertEqual(o["installWaited"], 1, "the install's write rides its waitUntil")
        fi = o["fpInstalled"]
        self.assertEqual(fi["version"], "__ROMP_SWV__", "the harness runs the unbaked source: the placeholder IS the version here")
        self.assertGreater(fi["installedAt"], 1)
        self.assertEqual((fi["activatedAt"], fi["lastPushAt"], fi["lastClickAt"], fi["clicks"], fi["lastPushSid"], fi["lastClickSid"]),
                         (0, 0, 0, 0, "", ""), "a fresh record: the previous build's counters do not carry over")
        self.assertEqual(o["activateWaited"], 1)
        fa = o["fpActivated"]
        self.assertGreater(fa["activatedAt"], 1)
        self.assertEqual(fa["installedAt"], fi["installedAt"], "merged, not replaced")
        # every push stamps its arrival — and the show is still the synchronous first act of the handler
        fp = o["fpPushed"]
        self.assertGreater(fp["lastPushAt"], 1)
        self.assertEqual(fp["lastPushSid"], "S1")
        self.assertEqual((fp["installedAt"], fp["activatedAt"], fp["clicks"]), (fi["installedAt"], fa["activatedAt"], 0))
        self.assertEqual(o["pushSync2"], ["show"])
        # every click stamps itself FIRST — the cache is opened before even the notification's close — and counts
        ops = o["clickStampOps"]
        self.assertEqual(ops[0], ["open", "romp-tap", 0], "the first thing the click handler does, before the close is logged: " + repr(ops[:3]))
        self.assertIn(["put", "/__romp/sw"], [x[:2] for x in ops])
        fc = o["fpAfterClick"]
        self.assertGreater(fc["lastClickAt"], 1)
        self.assertEqual(fc["lastClickSid"], "S1")
        self.assertEqual(fc["clicks"], 2, "the sid-less test tap before it counted too: every click is a click")
        self.assertEqual(o["fpAfterSidlessClick"]["clicks"], 1)
        self.assertEqual(o["fpAfterSidlessClick"]["lastClickSid"], "")
        self.assertEqual(o["clickStamp"]["log"], [["close"], self.MATCH, ["focus"], ["post", self.MSG]], "the tap lands exactly as before")
        self.assertEqual(o["clickStamp"]["waited"], 1, "the stamp rides the tap's one waitUntil")

    def test_the_push_writes_the_notification_it_shows_before_attempting_the_show(self):
        # the offer's source (2026-09-09): a page that comes forward with no tap stored — the click handler is a
        # road it cannot count on any more — but a shown record can OFFER the session the notification named. So
        # every session-addressed push writes '/__romp/shown' {id, sid, host, kind, cardId, url, name, t} BEFORE
        # the show is attempted: a show that fails (headless browsers refuse it) still leaves the record
        o = self.out
        self.assertEqual(o["pushOpsBeforeShow"], [["open", "romp-tap"], ["open", "romp-tap"]], "both writes are started before the show is logged")
        s = o["shownAfterPush"]
        self.assertEqual({k: s[k] for k in ("sid", "host", "kind", "cardId", "url", "name")},
                         {"sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1", "url": self.URL, "name": "web"})
        self.assertRegex(s["id"], r"^\d+-[a-z0-9]+$")
        self.assertIsInstance(s["t"], (int, float))
        self.assertEqual(o["pushRefused"], "rejected: denied", "the push still fails the way it always did when the show is refused")
        r = o["shownAfterRefused"]
        self.assertEqual((r["sid"], r["kind"], r["name"]), ("S2", "turn", "api"), "…but the record is there: one slot, latest wins")
        self.assertEqual(o["fpAfterRefused"]["lastPushSid"], "S2", "and the push was stamped")
        # a sid-less push has nothing to offer: the slot keeps what it held; the arrival is still stamped
        self.assertEqual(o["shownAfterSidless"]["sid"], "S2")
        self.assertEqual(o["fpAfterSidless"]["lastPushSid"], "")
        # a session-addressed click retires the record (the tap wins); a sid-less one leaves it
        self.assertEqual(o["shownAfterSidlessClick"]["id"], "N-1")
        self.assertIsNone(o["shownAfterClick"], "the tap on a session's notification spends the offer")
        # the shell's ack, naming the shown id (an offer taken or dismissed), retires it; another id leaves it
        self.assertEqual(o["shownAckOther"]["id"], "N-2")
        self.assertIsNone(o["shownAckOwn"])
        self.assertEqual(o["ackShownWaited"], 1, "one waitUntil carries both retirements")


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
        self.assertEqual(set(body["data"]), {"sid", "host", "kind", "cardId", "url", "name"})   # name (2026-09-09): the session's display name, for the offer chip


class PushPayloadShape(unittest.TestCase):
    """_push_payload: the ONE builder every push kind goes through (the user 2026-09-06, who wants
    a tap to focus the romp already open and land on the session — and card — that buzzed)."""

    def test_a_card_push_carries_the_card_and_a_deep_link_the_shell_parses(self):
        d = km._push_payload("romp: web", "Needs you: pick one", "SID-web", 2, kind="card",
                             card_id="SID-web:g3")
        self.assertEqual(d["data"], {"sid": "SID-web", "host": "", "kind": "card", "cardId": "SID-web:g3",
                                     "url": "/?push-reveal=SID-web&push-card=SID-web%3Ag3",
                                     "name": "SID-web"})   # no registry entry here: the short id, as _push_test always fell back
        self.assertEqual(d["tag"], "romp:SID-web", "one notification per session")
        self.assertEqual(d["sid"], "SID-web", "…and flat, for a worker of the previous build")
        self.assertEqual(d["badge"], 2)

    def test_a_turn_push_names_its_kind_and_carries_no_card(self):
        d = km._push_payload("web", "finished a turn", "SID-web", kind="turn")
        self.assertEqual((d["data"]["kind"], d["data"]["cardId"], d["data"]["url"]),
                         ("turn", "", "/?push-reveal=SID-web"))
        self.assertNotIn("badge", d, "None omits the key — the worker leaves the count alone")

    def test_a_test_push_has_no_session_and_lands_on_romp_itself(self):
        # the popover's probe (PR #937's _push_test builds its payload here once it lands): kind
        # "test", no sid, so the tap can only ever focus or open romp — and every test collapses
        # into one notification rather than stacking on the lock screen
        d = km._push_payload("romp", "Test notification", kind="test")
        self.assertEqual(d["data"], {"sid": "", "host": "", "kind": "test", "cardId": "", "url": "/", "name": ""})
        self.assertEqual(d["tag"], "romp:test")

    def test_the_payload_names_the_session_the_way_the_test_push_does(self):
        # 2026-09-09: the shell's "from the notification" offer names the session from the payload alone, so every
        # push carries `name`, resolved by ONE helper in _push_test's order of authority — the names registry for
        # a local session, the tunnel supervisor's snapshot for a federated one (host-prefixed), then the caller's
        # label, then the short id — unless the leg passes its own (the turn leg's title IS the name)
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


def _fake_ws_client(app, wid):
    """Just enough of a _clients row for the reveal/badge paths: send() records the parsed JSON."""
    got = []
    return {"app": app, "wid": wid, "alive": True,
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
        with mock.patch.object(km, "_tmux_sessions", return_value={"SID-live": {}}):
            self.assertTrue(km._reveal_request("SID-live", "W1"))
        self.assertEqual(got, [{"type": "focus", "id": "SID-live", "live": True}])
        self.assertIsNone(km._PENDING_REVEAL[0], "delivered → nothing parked")
        # a DEAD session never silently reveals — the revive prompt instead (_reveal_or_confirm's split)
        got.clear()
        with mock.patch.object(km, "_tmux_sessions", return_value={}), \
             mock.patch.object(km, "_name_of", return_value="web"):
            km._reveal_request("SID-gone", "W1")
        self.assertEqual(got[0]["type"], "confirmRevive")

    def test_boot_race_parks_then_ready_consumes_aimed_by_wid(self):
        # the norm: the shell's fetch beats its chat iframe's WS, so nothing is connected yet
        with mock.patch.object(km, "_tmux_sessions", return_value={"SID-live": {}}):
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
            self.assertEqual(mine_got, [{"type": "focus", "id": "SID-live", "live": True}])
            self.assertIsNone(km._PENDING_REVEAL[0])
            km._consume_pending_reveal(mine)
            self.assertEqual(len(mine_got), 1, "consumed means consumed")

    def test_a_widless_park_matches_the_first_chat_pane(self):
        # sessionStorage blocked → the shell has no wid; better the first chat pane than a dropped tap
        with mock.patch.object(km, "_tmux_sessions", return_value={"S": {}}):
            km._reveal_request("S", "")
            c, got = _fake_ws_client("chat", "W-any")
            km._consume_pending_reveal(c)
        self.assertEqual(got[0]["id"], "S")

    def test_a_booting_page_parks_past_the_previous_pages_socket(self):
        # the deep-link arrival (2026-09-06, the phone): the page is BOOTING, so its own chat pane
        # cannot be connected yet — a same-wid chat socket the kernel still holds is the PREVIOUS
        # page's (sessionStorage keeps the wid across a reload; a suspended phone never sent its
        # close, and the ping timeout has up to WS_DEAD_S to notice). "Delivering" there parked
        # nothing, and the new pane's ready found nothing to consume.
        twin, twin_got = self._register("chat", "W-phone")
        with mock.patch.object(km, "_tmux_sessions", return_value={"S": {}}):
            self.assertFalse(km._reveal_request("S", "W-phone", boot=True))
            self.assertEqual(twin_got, [], "a booting page's tap is never aimed at a socket that predates it")
            self.assertEqual(km._PENDING_REVEAL[0], {"sid": "S", "wid": "W-phone"})
            fresh, fresh_got = _fake_ws_client("chat", "W-phone")
            km._consume_pending_reveal(fresh)
        self.assertEqual(fresh_got, [{"type": "focus", "id": "S", "live": True}])
        self.assertIsNone(km._PENDING_REVEAL[0])

    def test_a_live_tap_to_an_unproven_socket_keeps_a_copy_until_the_pong_or_the_redial(self):
        # the live half of the same hole: the pane's socket has a ping on the wire nobody has
        # answered yet (pingAt set — the peer is unproven since the last heartbeat). The focus goes
        # out as before, AND stays parked: the pong that proves the socket alive retires the copy
        # (the frame is ordered behind the ping it answers); a dead socket never pongs, the pane
        # redials, and its ready consumes the copy instead of finding nothing.
        c, got = self._register("chat", "W1")
        c["pingAt"] = 100.0
        with mock.patch.object(km, "_tmux_sessions", return_value={"S": {}}):
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
        with mock.patch.object(km, "_tmux_sessions", return_value={"S": {}}):
            self.assertTrue(km._reveal_request("S", "W1"))
        self.assertIsNone(km._PENDING_REVEAL[0], "a socket with no ping outstanding is proven — nothing parked")
        # the dead case: never pongs; the pane's redial says ready and takes the copy
        c["pingAt"] = 100.0
        with mock.patch.object(km, "_tmux_sessions", return_value={"S": {}}):
            km._reveal_request("S", "W1")
            fresh, fresh_got = _fake_ws_client("chat", "W1")
            km._consume_pending_reveal(fresh)
        self.assertEqual(fresh_got, [{"type": "focus", "id": "S", "live": True}])
        self.assertIsNone(km._PENDING_REVEAL[0])


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

    def test_a_boot_flagged_reveal_parks_even_past_a_connected_same_wid_pane(self):
        # the deep-link arrival says it is booting; the kernel parks for the pane that is about to
        # connect and never counts the previous page's socket as delivery (RevealAiming has the why)
        twin, twin_got = _fake_ws_client("chat", "W-x")
        with km._clients_lock:
            km._clients.append(twin)
        try:
            code, body = self._post("/reveal", {"sid": "SID-x", "wid": "W-x", "boot": True})
        finally:
            with km._clients_lock:
                km._clients.remove(twin)
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)["delivered"])
        self.assertEqual(twin_got, [])
        self.assertEqual(km._PENDING_REVEAL[0], {"sid": "SID-x", "wid": "W-x"})

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
        # four roads (_REVEAL_ROADS — 'offer' joined later that day, the shell's chip for a notification shown but
        # never tapped through) and logs any other word as 'other'; a shell of a build before the field sends none,
        # and that stays the bare line
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._post("/reveal", {"sid": "SID-x", "wid": "W-x", "via": "sw\n[reveal] forged sid=SID-z: delivered"})
            self._post("/reveal", {"sid": "SID-y", "wid": "W-y", "via": "store"})
            self._post("/reveal", {"sid": "SID-v", "wid": "W-v", "via": "offer"})   # the chip's road: admitted by name, never 'other' (review find, 2026-09-09, on #1157)
            self._post("/reveal", {"sid": "SID-w", "wid": "W-w"})
        self.assertEqual(code, 200)
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("[reveal]")]
        self.assertEqual(lines, ["[reveal] other sid=SID-x wid=W-x: parked",
                                 "[reveal] store sid=SID-y wid=W-y: parked",
                                 "[reveal] offer sid=SID-v wid=W-v: parked",
                                 "[reveal] shell sid=SID-w wid=W-w: parked"])
        self.assertNotIn("forged", buf.getvalue())
        self.assertEqual(km._REVEAL_ROADS, frozenset({"sw", "link", "store", "offer"}))


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
    def test_shell_carries_both_halves_of_the_tap(self):
        html = km._landing()
        self.assertIn("m.romp==='notificationClick'", html)   # live window: the SW's message
        self.assertIn("m.romp==='pushReveal'", html)          # …or the shape a worker of an older build posts (2026-09-08)
        self.assertIn("searchParams.get('push-reveal')", html)  # cold start: the deep link's params…
        self.assertIn("searchParams.get('push-card')", html)
        self.assertIn("searchParams['delete']('push-reveal')", html)   # …stripped once read
        self.assertIn("history.replaceState", html)
        self.assertIn("fetch('/reveal'", html)     # ONE activation path: the kernel aims the focus…
        self.assertIn("romp:wid", html)            # …at the shell's own per-window id
        self.assertIn("romp:'revealCard'", html)   # a card kind also scrolls the feed to the card…
        self.assertIn("m.romp==='ready'&&m.app==='feed'", html)   # …once the feed has its cards
        self.assertNotIn("type:'focus',id:sid", html, "no focus posted straight into the chat iframe any more")
        self.assertNotIn("setTimeout", km._LANDING_REVEAL_JS, "event-based: the feed's ready, never a timer")

    def test_the_worker_and_the_shell_share_the_stored_taps_key(self):
        # 2026-09-09: the worker writes the tap to the Cache API before it tries to focus or open anything; the
        # shell reads the same entry on boot, visible, pageshow and focus. One name for the cache, one for the
        # entry, in both sources — a drift here is a tap that is written and never read
        for src in (km._SW_JS, km._LANDING_REVEAL_JS):
            self.assertIn("var TAP='/__romp/tap',TAPC='romp-tap'", src)
        self.assertIn("c.put(TAP,new Response(JSON.stringify(tap)))", km._SW_JS)
        self.assertLess(km._SW_JS.index("keep(tap)"), km._SW_JS.index("clients.matchAll({type:'window',includeUncontrolled:true})"),
                        "written before the worker looks for a window")
        js = km._LANDING_REVEAL_JS
        self.assertIn("resume('boot',pr||'')", js)
        self.assertIn("if(document.visibilityState==='visible'){askReplay();refreshWorker();resume('visible');}", js)   # + the worker re-check (2026-09-09)
        self.assertIn("window.addEventListener('pageshow',function(){askReplay();resume('pageshow');});", js)
        self.assertIn("window.addEventListener('focus',function(){askReplay();resume('focus');});", js)
        self.assertIn("diag('tap-resume',", js)
        self.assertNotIn("setTimeout", js)

    def test_the_boot_flag_follows_the_chat_panes_own_socket(self):
        # review find (2026-09-09, on #1127): the two sides of the contract share the words. The pane's shim posts its
        # socket state to the shell on every open (netState); the reveal script latches the chat pane's up and sends
        # boot:true on every road until then. A drift here is a tap parked for a ready that never comes, or one
        # 'delivered' to the previous page's socket
        js = km._LANDING_REVEAL_JS
        self.assertIn("if(m&&m.romp==='wsState'&&m.app==='chat'&&m.state==='up')chatUp=true;", js)
        self.assertIn("boot=!!boot||!chatUp;", js)
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
        # script parse; rows queue (capped) until the shell socket opens. The socket now carries the shell's
        # wid, so its rows match this dashboard's pane rows — and _reveal_chat_for's shell line has a target
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
        # the callers: the bell's press and the landing script's three steps, each through the poster, and the
        # kernel already persists the type they post (the clientDiag branch of _dispatch_ws)
        self.assertIn("diag('push-test',{sidAttached:!!at.sid,host:at.host,why:at.why,tabs:at.tabs});", km._LANDING_PUSH_JS)
        for row in ("diag('deeplink',{hasSid:!!pr,hasCard:!!pc,controlled:", "diag('sw-message',{shape:m.romp,hasSid:!!m.sid,", "diag('reveal-post',{status:r.status,via:via,boot:!!boot});"):
            self.assertIn(row, km._LANDING_REVEAL_JS)
        import inspect
        self.assertIn('msg.get("type") == "clientDiag"', inspect.getsource(km.Handler._dispatch_ws))


# The shell's reveal script, EXECUTED (the test_error_center.py pattern): node runs
# _LANDING_REVEAL_JS against stubs of the few browser globals it touches, booting on a deep link
# and then replaying the worker's messages. Pins the routing, not the words: /reveal is asked
# with the shell's wid, the URL is stripped, the card waits for the FEED's ready (not the
# timeline's), a turn kind reveals no card, a test addressed to a session reveals it the same way,
# a sid-less tap does nothing, a refused /reveal is loud.
_REVEAL_HARNESS = r"""
'use strict';
const FETCHES = [], POSTED = [], NOTES = [], REPLACED = [], WIN = [], SW = [], DOC = [], PAGESHOW = [], FOCUS = [], CTRL = [], ACK = [], DIAG = [], UPD = [];
let fetchOk = true;
const feedWin = { postMessage: (m) => POSTED.push(m) };
global.window = global;
// the offer chip's three stable shell elements (2026-09-09), as minimal nodes: hidden/disabled/textContent (a set
// clears appended children, as the DOM's does), a classList, listeners fired by click()
function el(id) { const L = {}; let text = ''; const kids = [];
  const e = { id, hidden: true, disabled: false,
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); } },
    appendChild(k) { kids.push(k); }, addEventListener(k, f) { (L[k] = L[k] || []).push(f); }, click() { (L.click || []).forEach((f) => f()); },
    get textContent() { return text + kids.map((k) => k.textContent).join(''); }, set textContent(v) { text = String(v); kids.length = 0; } };
  return e; }
const CHIP = { 'tap-offer': el('tap-offer'), 'tap-offer-go': el('tap-offer-go'), 'tap-offer-x': el('tap-offer-x') };
const chipState = () => ({ hidden: CHIP['tap-offer'].hidden, text: CHIP['tap-offer-go'].textContent, acted: CHIP['tap-offer'].classList.contains('acted'), disabled: CHIP['tap-offer-go'].disabled });
// the chat pane's active tab, as the same-origin iframe DOM the script reads: ROMP_TEST_ACTIVE at boot, reassignable
let activeSid = process.env.ROMP_TEST_ACTIVE || '';
const chatFrame = { contentDocument: { querySelector: () => (activeSid ? { getAttribute: () => activeSid } : null) } };
global.document = { getElementById: (id) => (id === 'f-feed' ? { contentWindow: feedWin } : id === 'f-chat' ? chatFrame : (CHIP[id] || null)),
  createElement: (tag) => ({ tag, className: '', textContent: '' }),
  addEventListener: (k, f) => { if (k === 'visibilitychange') DOC.push(f); }, visibilityState: 'visible' };
global.sessionStorage = { getItem: (k) => (k === 'romp:wid' ? 'W-test' : null) };
global.addEventListener = (k, f) => { if (k === 'message') WIN.push(f); if (k === 'pageshow') PAGESHOW.push(f); if (k === 'focus') FOCUS.push(f); };
// the registration the page asks to update() at boot and on every visible (2026-09-09); ROMP_TEST_NO_REG: none registered
const REG = { update: () => { UPD.push(1); return Promise.resolve(); } };
Object.defineProperty(global, 'navigator', { configurable: true,   // a getter-only global in node 22
  value: { serviceWorker: { addEventListener: (k, f) => { if (k === 'message') SW.push(f); },
                            getRegistration: () => Promise.resolve(process.env.ROMP_TEST_NO_REG ? undefined : REG),
                            controller: { postMessage: (m) => CTRL.push(m) } } } });   // the worker that controls this page
global.history = { replaceState: (s, t, u) => REPLACED.push(u) };
// the boot is env-driven (2026-09-09) so one harness plays every arrival: ROMP_TEST_HREF is the URL the page
// opened on (default: the deep link), ROMP_TEST_TAP a tap the worker had stored before this page booted,
// ROMP_TEST_NO_CACHES a window with no Cache API at all (an insecure context)
global.location = { href: process.env.ROMP_TEST_HREF || 'http://localhost:7777/?push-reveal=S1&push-card=S1%3Ag1&keep=1#frag' };
// the tap store: the Cache API the worker writes and this page reads, as a Map keyed by request URL; match()
// hands back a clone, as the real API does (a Response body reads once)
const STORE = new Map();
const cacheObj = {
  put: (k, r) => { STORE.set(String(k), r); return Promise.resolve(); },
  match: (k) => { const r = STORE.get(String(k)); return Promise.resolve(r ? r.clone() : undefined); },
  delete: (k) => Promise.resolve(STORE.delete(String(k))),
};
if (!process.env.ROMP_TEST_NO_CACHES) global.caches = { open: () => Promise.resolve(cacheObj), match: (k) => cacheObj.match(k) };
function seed(tap) { STORE.set('/__romp/tap', new Response(JSON.stringify(tap))); }
function seedK(k, v) { STORE.set(k, new Response(JSON.stringify(v))); }   // any entry: the fingerprint, the shown record
if (process.env.ROMP_TEST_TAP) seed(JSON.parse(process.env.ROMP_TEST_TAP));
if (process.env.ROMP_TEST_SEED) { const s = JSON.parse(process.env.ROMP_TEST_SEED); for (const k in s) seedK(k, s[k]); }
global.fetch = (path, init) => { FETCHES.push([path, JSON.parse(init.body)]);
  return Promise.resolve(fetchOk ? { ok: true, status: 200 } : { ok: false, status: 400, text: () => Promise.resolve('missing sid') }); };
global.__rompNotify = (kind, text) => NOTES.push([kind, text]);
global.__rompShellDiag = (what, data) => DIAG.push([what, data]);   // _LANDING_MOBILE_JS's poster, stubbed: the rows this script files
"""
_REVEAL_DRIVER = r"""
const tick = () => new Promise((r) => setTimeout(r, 0));
const swSrc = { postMessage: (m) => ACK.push(m) };            // ev.source: the worker that posted
const swMsg = (m) => SW.forEach((f) => f({ data: m, source: swSrc }));
const winMsg = (m) => WIN.forEach((f) => f({ data: m }));
(async () => {
  const out = { boot: { fetches: FETCHES.slice(), replaced: REPLACED.slice(), postedBeforeReady: POSTED.length,
                        diag: DIAG.slice(), ctrl: CTRL.slice(), swListeners: SW.length } };
  await tick();
  out.boot.diagAfter = DIAG.slice();                      // …plus /reveal's answer, once it lands
  winMsg({ romp: 'ready' });                              // the timeline's ready: not the feed's
  out.boot.postedAfterTimelineReady = POSTED.length;
  winMsg({ romp: 'ready', app: 'feed' });
  out.boot.postedAfterFeedReady = POSTED.slice();
  // a tap reaching this page BEFORE its chat pane's socket is up (review find, 2026-09-09, on #1127): the message
  // openWindow handed a window that booted on its start URL, or the replay answered at parse time. It lands with
  // boot:true, so the kernel parks for THIS page's pane instead of aiming at the previous page's same-wid socket;
  // another pane's socket coming up is not the chat pane's
  FETCHES.length = 0; DIAG.length = 0; ACK.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S0', host: '', kind: 'turn', cardId: '', id: 'T-0', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });
  await tick();
  out.earlySw = { fetches: FETCHES.slice(), diag: DIAG.slice(), ack: ACK.slice() };
  winMsg({ romp: 'wsState', app: 'feed', state: 'up' });
  FETCHES.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S0', host: '', kind: 'turn', cardId: '', id: 'T-0b' });
  await tick();
  out.earlySwFeedUp = { fetches: FETCHES.slice() };
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });   // this page's chat pane connected: from here a tap is delivered live
  FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0; ACK.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S2', host: '', kind: 'card', cardId: 'S2:g4', id: 'T-1', diag: { clients: 3, tops: 1, road: 'focus', vis: 'hidden' } });
  await tick();
  out.live = { fetches: FETCHES.slice(), posted: POSTED.slice(), diag: DIAG.slice(), ack: ACK.slice() };
  FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0; ACK.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S2', host: '', kind: 'card', cardId: 'S2:g4', id: 'T-1', diag: { clients: 3, tops: 1, road: 'focus', vis: 'hidden' } });   // the same tap again, by another road
  await tick();
  out.dup = { fetches: FETCHES.slice(), posted: POSTED.slice(), diag: DIAG.slice(), ack: ACK.slice() };
  FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0; ACK.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S3', host: '', kind: 'turn', cardId: '' });
  out.turn = { fetches: FETCHES.slice(), posted: POSTED.slice() };
  await tick();                                                                          // its /reveal answers before the next scenario's snapshot
  FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0;
  swMsg({ romp: 'notificationClick', sid: '', host: '', kind: 'test', cardId: '', id: 'T-2', diag: { clients: 1, tops: 1, road: 'focus', vis: '' } });
  await tick();
  out.test = { fetches: FETCHES.slice(), posted: POSTED.slice(), diag: DIAG.slice() };
  FETCHES.length = 0; POSTED.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S5', host: '', kind: 'test', cardId: '' });   // a test addressed to the session in front (2026-09-06)
  out.testSid = { fetches: FETCHES.slice(), posted: POSTED.slice() };
  await tick();
  FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0; ACK.length = 0;
  swMsg({ romp: 'pushReveal', sid: 'S6' });                                             // the worker of builds before 2026-09-06, still installed on a phone
  await tick();
  out.legacy = { fetches: FETCHES.slice(), posted: POSTED.slice(), diag: DIAG.slice(), ack: ACK.slice() };
  fetchOk = false; FETCHES.length = 0; DIAG.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S-bad', kind: 'turn' });
  await tick(); await tick();
  out.refused = { notes: NOTES.slice(), diag: DIAG.slice() };
  // the replay asks (2026-09-08): at boot (above), and every time the page becomes visible again
  CTRL.length = 0;
  global.document.visibilityState = 'hidden'; DOC.forEach((f) => f());
  out.hiddenAsks = CTRL.slice();
  global.document.visibilityState = 'visible'; DOC.forEach((f) => f());
  out.visibleAsks = CTRL.slice();
  CTRL.length = 0; PAGESHOW.forEach((f) => f());
  out.pageshowAsks = CTRL.slice();
  // the pane's socket dropping later does not re-arm the flag: the pane posts its ready once per page life, so a
  // park made now would wait for a ready that never comes; the redial is the kernel's business (superseded by iid)
  winMsg({ romp: 'wsState', app: 'chat', state: 'down' });
  fetchOk = true; FETCHES.length = 0;
  swMsg({ romp: 'notificationClick', sid: 'S30', host: '', kind: 'turn', cardId: '', id: 'T-30' });
  await tick();
  out.afterDrop = { fetches: FETCHES.slice() };
  console.log(JSON.stringify(out));
})();
"""


# the fingerprint keys every tap-resume row carries since 2026-09-09, as a page reads them with NO '/__romp/sw'
# entry in the store (no worker ever wrote one): nothing known, said plainly
NO_FP = {"swVersion": None, "swMatchesPage": None, "lastPushAgeS": -1, "lastClickAgeS": -1, "clicks": 0}


def _fp(row, **fp):
    """a tap-resume row as filed: `row` plus the fingerprint keys (NO_FP unless overridden)"""
    d = dict(row)
    d.update(NO_FP)
    d.update(fp)
    return d


def _run_reveal(driver, href=None, tap=None, no_caches=False, seed=None, active=None, no_reg=False):
    """node runs the harness + the shell's reveal script + `driver`, booting on `href` (default: the deep
    link) with `tap` already in the store (the worker wrote it before this page) — see the harness's env.
    `seed`: other entries already in the store ({key: record} — the fingerprint, the shown record); `active`:
    the chat pane's active tab at boot; `no_reg`: no service worker registration to update."""
    import subprocess, tempfile as _tf
    env = dict(os.environ)
    if href:
        env["ROMP_TEST_HREF"] = href
    if tap is not None:
        env["ROMP_TEST_TAP"] = json.dumps(tap)
    if no_caches:
        env["ROMP_TEST_NO_CACHES"] = "1"
    if seed:
        env["ROMP_TEST_SEED"] = json.dumps(seed)
    if active:
        env["ROMP_TEST_ACTIVE"] = active
    if no_reg:
        env["ROMP_TEST_NO_REG"] = "1"
    with _tf.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_REVEAL_HARNESS + km._LANDING_REVEAL_JS + driver)
        path = f.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, timeout=30, env=env)
    finally:
        os.unlink(path)
    assert r.returncode == 0, "the reveal script threw: " + r.stderr[:800]
    return json.loads(r.stdout.strip().splitlines()[-1])


class LandingRevealExecutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run_reveal(_REVEAL_DRIVER)

    def test_a_cold_start_asks_the_kernel_at_once_and_strips_the_link(self):
        b = self.out["boot"]
        # boot:true — this page is booting, so its own chat pane is not connected yet; the kernel
        # parks for it rather than aiming at a same-wid socket the previous page left behind
        self.assertEqual(b["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}]])
        self.assertEqual(b["replaced"], ["/?keep=1#frag"], "only OUR params go; a reload must not replay the jump")

    def test_the_card_waits_for_the_feeds_own_ready(self):
        b = self.out["boot"]
        self.assertEqual(b["postedBeforeReady"], 0, "no listener yet, nothing to scroll to")
        self.assertEqual(b["postedAfterTimelineReady"], 0, "another pane's ready is not the feed's")
        self.assertEqual(b["postedAfterFeedReady"], [{"romp": "revealCard", "itemId": "S1:g1", "sid": "S1"}])

    def test_a_live_tap_routes_the_same_way(self):
        live = self.out["live"]
        self.assertEqual(live["fetches"], [["/reveal", {"sid": "S2", "wid": "W-test", "via": "sw"}]])
        self.assertEqual(live["posted"], [{"romp": "revealCard", "itemId": "S2:g4", "sid": "S2"}])

    def test_a_turn_focuses_without_a_card_and_a_sidless_test_lands_nowhere(self):
        self.assertEqual(len(self.out["turn"]["fetches"]), 1)
        self.assertEqual(self.out["turn"]["posted"], [])
        self.assertEqual((self.out["test"]["fetches"], self.out["test"]["posted"]), ([], []))

    def test_a_test_addressed_to_a_session_reveals_it_like_a_turn(self):
        # the user 2026-09-06: ANY sid lands, whatever the kind; only a card adds the card scroll
        self.assertEqual(self.out["testSid"], {"fetches": [["/reveal", {"sid": "S5", "wid": "W-test", "via": "sw"}]], "posted": []})

    def test_a_stale_workers_tap_still_lands(self):
        # the worker of builds before 2026-09-06 posts {romp:'pushReveal', sid}; a phone keeps running it
        # until a navigation refreshes it (2026-09-08) — the shell reads that shape too, never a silent miss
        self.assertEqual((self.out["legacy"]["fetches"], self.out["legacy"]["posted"]),
                         ([["/reveal", {"sid": "S6", "wid": "W-test", "via": "sw"}]], []))

    def test_a_refused_reveal_is_loud(self):
        self.assertEqual(self.out["refused"]["notes"],
                         [["error", "Could not open the session this notification was about: missing sid"]])
        self.assertEqual(self.out["refused"]["diag"][-1], ["reveal-post", {"status": 400, "via": "sw", "boot": False}],
                         "the refusal's status is on record beside the toast")

    def test_every_step_files_a_shell_diag_row(self):
        # 2026-09-08: the app came forward, the session did not change, the journal held no /reveal line —
        # so the request never left the phone, and nothing said where it had stopped. Each step now files a
        # client-diag row through the shell socket's poster: the boot (link or not; a worker in control),
        # the worker's message (its shape, whether it carries a session, the worker's own trail), /reveal's
        # status. Structure only — no id, no name, no text
        b = self.out["boot"]
        self.assertEqual(b["diag"], [["deeplink", {"hasSid": True, "hasCard": True, "controlled": True}]])
        # …then /reveal's answer, the stored-tap check (2026-09-09: an empty store, said so — with the worker's
        # fingerprint, none written here) and the worker re-check, in whatever order the promises settle
        self.assertEqual(sorted(json.dumps(x, sort_keys=True) for x in b["diagAfter"][1:]),
                         sorted(json.dumps(x, sort_keys=True) for x in [["reveal-post", {"status": 200, "via": "link", "boot": True}],
                                                                        ["tap-resume", _fp({"found": False, "via": "boot", "store": True})],
                                                                        ["sw-update", {"ok": True, "reg": True}]]))
        self.assertEqual(self.out["live"]["diag"],
                         [["sw-message", {"shape": "notificationClick", "hasSid": True, "kind": "card", "dup": False,
                                          "sw": {"clients": 3, "tops": 1, "road": "focus", "vis": "hidden"}}],
                          ["reveal-post", {"status": 200, "via": "sw", "boot": False}]])
        self.assertEqual(self.out["test"]["diag"],
                         [["sw-message", {"shape": "notificationClick", "hasSid": False, "kind": "test", "dup": False,
                                          "sw": {"clients": 1, "tops": 1, "road": "focus", "vis": ""}}]],
                         "a sid-less tap: the row says so, and no /reveal follows")
        self.assertEqual(self.out["legacy"]["diag"][0],
                         ["sw-message", {"shape": "pushReveal", "hasSid": True, "kind": "", "dup": False, "sw": None}])
        for what, data in b["diag"] + self.out["live"]["diag"]:
            self.assertNotIn("sid", data, "structure only: the row never carries the session id")

    def test_a_tap_lands_once_however_many_roads_deliver_it_and_is_acked(self):
        # the worker posts the tap, replays it to a shell that asks, and the deep link can carry it too; the
        # id dedupes: one /reveal, one card scroll — and the shell tells the worker that tap landed so the
        # worker retires it. A worker of an older build sends no id: nothing to dedupe on, nothing to ack
        self.assertEqual(self.out["live"]["ack"], [{"romp": "tapLanded", "id": "T-1"}])
        d = self.out["dup"]
        self.assertEqual((d["fetches"], d["posted"], d["ack"]), ([], [], []))
        self.assertEqual(d["diag"][0][1]["dup"], True, "the second arrival is filed as such, not landed again")
        self.assertEqual(self.out["legacy"]["ack"], [])

    def test_a_tap_before_this_pages_chat_pane_is_up_says_booting_whatever_road_brought_it(self):
        # review find (2026-09-09, on #1127): the worker's message and its replay posted no boot flag even when they
        # reached a page whose chat pane had not connected (the message handed to the window openWindow opened on
        # its start URL; the replay answered at parse time), so the kernel counted the previous page's same-wid
        # socket as delivery, logged 'delivered', and the tap was lost. The flag now follows this page's own chat
        # pane's socket ({romp:'wsState',app:'chat',state:'up'}, the shim's message to the shell): booting until it
        # is up, so the kernel parks and the pane's ready delivers; live from then on, latched (the pane's ready
        # comes once per page life, so a later drop must not re-arm a park nothing would consume)
        e = self.out["earlySw"]
        self.assertEqual(e["fetches"], [["/reveal", {"sid": "S0", "wid": "W-test", "via": "sw", "boot": True}]])
        self.assertEqual(e["diag"][-1], ["reveal-post", {"status": 200, "via": "sw", "boot": True}], "the row says what was sent")
        self.assertEqual(e["ack"], [{"romp": "tapLanded", "id": "T-0"}], "handed to the kernel: acked like any landing")
        self.assertEqual(self.out["earlySwFeedUp"]["fetches"], [["/reveal", {"sid": "S0", "wid": "W-test", "via": "sw", "boot": True}]], "another pane's socket is not the chat pane's")
        self.assertNotIn("boot", self.out["live"]["fetches"][0][1], "once the chat pane is up, a tap is delivered live")
        self.assertEqual(self.out["afterDrop"]["fetches"], [["/reveal", {"sid": "S30", "wid": "W-test", "via": "sw"}]], "a later drop does not re-arm the flag")

    def test_the_shell_asks_the_worker_for_a_kept_tap_at_boot_and_on_coming_back(self):
        # the events a tap that brought the app forward produces: this page booting (a relaunched app), or
        # becoming visible again (a resumed one). Each asks the controlling worker; hidden asks nothing
        b = self.out["boot"]
        self.assertEqual(b["swListeners"], 1)
        self.assertEqual(b["ctrl"], [{"romp": "tapReplay"}], "asked at parse time, after the listener is in place")
        self.assertEqual(self.out["hiddenAsks"], [])
        self.assertEqual(self.out["visibleAsks"], [{"romp": "tapReplay"}])
        self.assertEqual(self.out["pageshowAsks"], [{"romp": "tapReplay"}])


# The stored tap, from the page's side (2026-09-09). Shared driver prelude: the events a page produces, and a
# snapshot of everything the script did since the last reset. `flip` waits a few ticks: the store read is a
# promise chain, and the row/fetch/ack land after it settles.
_RESUME_LIB = r"""
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };
const swSrc = { postMessage: (m) => ACK.push(m) };
const swMsg = (m) => SW.forEach((f) => f({ data: m, source: swSrc }));
const winMsg = (m) => WIN.forEach((f) => f({ data: m }));
const flip = async (state) => { global.document.visibilityState = state; DOC.forEach((f) => f()); await settle(); };
const snap = () => ({ fetches: FETCHES.slice(), posted: POSTED.slice(), diag: DIAG.slice(), ack: ACK.slice(), ctrl: CTRL.slice(), keys: [...STORE.keys()], notes: NOTES.slice(), chip: chipState(), upd: UPD.length });
const reset = () => { FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0; ACK.length = 0; CTRL.length = 0; NOTES.length = 0; };
"""
# a page that booted on the plain start URL with nothing stored, then came back with a tap in the store
_RESUME_WARM_DRIVER = _RESUME_LIB + r"""
(async () => {
  const out = {};
  await settle();
  out.boot = snap();
  winMsg({ romp: 'ready', app: 'feed' });
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });   // this page's chat pane connected (review find 2026-09-09 on #1127: the boot flag follows it)
  reset();
  // the phone's warm case: the worker stored the tap and iOS brought this suspended page forward — no load,
  // no message, no worker left to replay. The page becomes visible → reads the store → lands it once
  seed({ id: 'T-9', sid: 'S9', host: '', kind: 'card', cardId: 'S9:g2', url: '/?push-reveal=S9&push-card=S9%3Ag2', t: Date.now() - 5000 });
  await flip('hidden');
  out.hidden = snap();
  reset();
  await flip('visible');
  out.visible = snap();
  reset();
  await flip('visible');
  out.again = snap();
  reset();
  seed({ id: 'T-11', sid: 'S11', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S11', t: Date.now() - 400000 });   // long ago: still a tap
  PAGESHOW.forEach((f) => f()); await settle();
  out.pageshow = snap();
  reset();
  seed({ id: 'T-12', sid: 'boxa:S12', host: 'boxa', kind: 'test', cardId: '', url: '/?push-reveal=boxa%3AS12' });   // no t at all
  FOCUS.forEach((f) => f()); await settle();
  out.focus = snap();
  reset();
  // the same tap by two roads: the worker's message first (a resumed page that DID get it), the store after
  seed({ id: 'T-13', sid: 'S13', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S13', t: Date.now() });
  swMsg({ romp: 'notificationClick', sid: 'S13', host: '', kind: 'turn', cardId: '', id: 'T-13', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });
  await settle();
  out.swFirst = snap();
  reset();
  await flip('visible');
  out.swThenStore = snap();
  reset();
  // …and the store first, the message (a replay) after
  seed({ id: 'T-14', sid: 'S14', host: '', kind: 'card', cardId: 'S14:g1', url: '/?push-reveal=S14&push-card=S14%3Ag1', t: Date.now() });
  await flip('visible');
  out.storeFirst = snap();
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S14', host: '', kind: 'card', cardId: 'S14:g1', id: 'T-14', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });
  await settle();
  out.storeThenSw = snap();
  reset();
  // two checks in flight at once (visible and pageshow fire together on a resume): one lands, the other is a dup
  seed({ id: 'T-16', sid: 'S16', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S16', t: Date.now() });
  global.document.visibilityState = 'visible'; DOC.forEach((f) => f()); PAGESHOW.forEach((f) => f());
  await settle();
  out.race = snap();
  reset();
  // a record with no session is not a tap: nothing lands, and it is cleared
  seed({ id: 'T-15', sid: '', host: '', kind: 'test', cardId: '', url: '/', t: Date.now() });
  await flip('visible');
  out.sidless = snap();
  console.log(JSON.stringify(out));
})();
"""
# a page booting with a tap already in the store: iOS relaunched the app on its start URL (no link)
_RESUME_BOOT_DRIVER = _RESUME_LIB + r"""
(async () => {
  const out = {};
  await settle();
  out.boot = snap();
  winMsg({ romp: 'ready', app: 'feed' });
  out.postedAfterFeedReady = POSTED.slice();
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });   // the chat pane connects after the boot's park
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S20', host: '', kind: 'card', cardId: 'S20:g3', id: 'T-20', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });   // the same tap, handed to the opened window
  await settle();
  out.handed = snap();
  console.log(JSON.stringify(out));
})();
"""
# a page booting on a deep link while the store holds a tap: the link is landed, the stored tap dropped
_RESUME_LINK_DRIVER = _RESUME_LIB + r"""
(async () => {
  const out = {};
  await settle();
  out.boot = snap();
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S1', host: '', kind: 'card', cardId: 'S1:g1', id: 'T-22', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });   // the stored tap's own message arrives after
  await settle();
  out.handed = snap();
  console.log(JSON.stringify(out));
})();
"""

# the offer (2026-09-09): a page booting on the plain start URL with a shown record and the worker's fingerprint in the
# store — no tap — then every turn the chip can take
_OFFER_DRIVER = _RESUME_LIB + r"""
(async () => {
  const out = {};
  await settle();
  out.boot = snap();                                   // the chip is up: a shown record, no tap, no session in front yet
  winMsg({ romp: 'ready', app: 'feed' });
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });   // this page's chat pane connected: from here a landing is delivered live, no boot flag
  reset();
  CHIP['tap-offer-go'].click();                        // the user takes the offer
  out.clickedSync = chipState();                       // acknowledged before anything settles
  await settle();
  out.taken = snap();
  reset();
  await flip('visible');                               // nothing left to offer
  out.after = snap();
  reset();
  seedK('/__romp/shown', { id: 'N-2', sid: 'S10', host: '', kind: 'card', cardId: 'S10:g1', url: '/?push-reveal=S10&push-card=S10%3Ag1', name: 'tests', t: Date.now() - 60000 });
  await flip('visible');
  out.second = snap();
  reset();
  CHIP['tap-offer-x'].click();                         // …and this one dismissed
  await settle();
  out.dismissed = snap();
  reset();
  // a shown record AND a stored tap: the tap wins, the offer never shows and its record is spent
  seedK('/__romp/shown', { id: 'N-3', sid: 'S11', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S11', name: 'web', t: Date.now() });
  seed({ id: 'T-30', sid: 'S12', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S12', t: Date.now() });
  await flip('visible');
  out.tapWins = snap();
  reset();
  // the session in front already IS the one named: nothing to offer, the record is spent
  seedK('/__romp/shown', { id: 'N-4', sid: 'S13', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S13', name: 'api', t: Date.now() - 2000 });
  activeSid = 'S13';
  await flip('visible');
  out.already = snap();
  activeSid = '';
  reset();
  // a record with no session is not an offer: cleared, nothing filed
  seedK('/__romp/shown', { id: 'N-5', sid: '', host: '', kind: 'test', cardId: '', url: '/', name: '', t: Date.now() });
  await flip('visible');
  out.sidless = snap();
  reset();
  // the fingerprint of ANOTHER build: the row says so, and sw-stale is filed beside it
  seedK('/__romp/sw', { version: 'other-build', installedAt: Date.now() - 900000, activatedAt: Date.now() - 900000, lastPushAt: Date.now() - 30000, lastPushSid: 'S9', lastClickAt: Date.now() - 20000, lastClickSid: 'S9', clicks: 3 });
  await flip('visible');
  out.stale = snap();
  console.log(JSON.stringify(out));
})();
"""
# a boot and nothing else: the rows the boot alone files
_BOOT_DRIVER = _RESUME_LIB + r"""
(async () => { await settle(); console.log(JSON.stringify({ boot: snap() })); })();
"""
# the offer taken at once, BEFORE this page's chat pane has reported its socket up: the relaunch iOS makes on the
# start URL, the chip up at boot, the user tapping it straight away (review find, 2026-09-09, on #1157: the road
# the offer exists for, and the one the boot latch has to cover)
_OFFER_EARLY_DRIVER = _RESUME_LIB + r"""
(async () => { await settle(); CHIP['tap-offer-go'].click(); await settle(); console.log(JSON.stringify({ early: snap() })); })();
"""


class LandingRevealOffers(unittest.TestCase):
    """2026-09-09, the phone with the app WARM: three taps, three 201s from the push service, and then nothing
    — no [reveal] line, no worker message, tap-resume found:false on every resume, each tap booting a fresh
    page on the start URL. Whether iOS handed the tap to the live app past the worker or an older worker took
    it, the click handler is a road the page cannot count on. Two answers, both read here: the worker's
    FINGERPRINT (which build wrote the store, how long since its last push and click) folded into every
    tap-resume row, with sw-stale on a mismatch and a registration.update() at boot and on every visible; and
    the OFFER — a chip, not a jump, for the notification the worker showed but nobody tapped through."""
    SHOWN = {"id": "N-1", "sid": "S9", "host": "", "kind": "card", "cardId": "S9:g2", "url": "/?push-reveal=S9&push-card=S9%3Ag2", "name": "api"}
    FP = {"version": "__ROMP_SWV__", "installedAt": 1, "activatedAt": 1, "lastPushSid": "S9", "lastClickAt": 0, "lastClickSid": "", "clicks": 0}

    @classmethod
    def setUpClass(cls):
        import time as _t
        now = int(_t.time() * 1000)
        cls.out = _run_reveal(_OFFER_DRIVER, href="http://localhost:7777/",
                              seed={"/__romp/shown": dict(cls.SHOWN, t=now - 5000), "/__romp/sw": dict(cls.FP, lastPushAt=now - 5000)})
        cls.link = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/?push-reveal=S1",
                               seed={"/__romp/shown": dict(cls.SHOWN, t=now - 5000)})
        cls.no_reg = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", no_reg=True)
        cls.active_at_boot = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", active="S9",
                                         seed={"/__romp/shown": dict(cls.SHOWN, t=now - 5000)})
        cls.early = _run_reveal(_OFFER_EARLY_DRIVER, href="http://localhost:7777/",
                                seed={"/__romp/shown": dict(cls.SHOWN, t=now - 5000)})

    @staticmethod
    def _rows(snap, what):
        return [d for w, d in snap["diag"] if w == what]

    def test_a_shown_but_untapped_notification_is_offered_not_jumped_to(self):
        b = self.out["boot"]
        self.assertEqual(b["fetches"], [], "no jump: the user may have opened the app for another reason")
        self.assertEqual(b["chip"], {"hidden": False, "text": "Open api · from the notification", "acted": False, "disabled": False})
        self.assertEqual(self._rows(b, "tap-offer"), [{"shown": True, "via": "boot", "ageS": 5, "why": ""}])
        # the fingerprint rides the tap-resume row: this page's own build wrote the store, its last push seconds ago,
        # never a click — the reading that separates a worker that never ran from one that ran and lost the tap
        self.assertEqual(self._rows(b, "tap-resume"),
                         [_fp({"found": False, "via": "boot", "store": True}, swVersion="__ROMP_SWV__", swMatchesPage=True, lastPushAgeS=5, lastClickAgeS=-1, clicks=0)])
        self.assertEqual(self._rows(b, "sw-stale"), [], "the same build: nothing stale")
        for d in self._rows(b, "tap-offer") + self._rows(b, "tap-resume"):
            for k in d:
                self.assertNotIn("sid", k.lower(), "ages and booleans only, never a session id: %r" % d)

    def test_taking_the_offer_lands_by_the_same_path_and_retires_the_record(self):
        self.assertEqual(self.out["clickedSync"], {"hidden": True, "text": "Open api · from the notification", "acted": True, "disabled": True},
                         "acknowledged in the click's own stack: pressed look, disabled, gone")
        t = self.out["taken"]
        self.assertEqual(t["fetches"], [["/reveal", {"sid": "S9", "wid": "W-test", "via": "offer"}]], "the same land() path, the road named")
        self.assertEqual(t["posted"], [{"romp": "revealCard", "itemId": "S9:g2", "sid": "S9"}], "a card kind scrolls the feed too")
        self.assertIn(["tap-offer-click", {"ageS": 5}], t["diag"])
        self.assertIn(["reveal-post", {"status": 200, "via": "offer", "boot": False}], t["diag"])
        self.assertIn({"romp": "tapLanded", "id": "N-1"}, t["ctrl"], "the worker is told, so its copy goes too")
        self.assertNotIn("/__romp/shown", t["keys"], "the record is retired here as well")
        self.assertTrue(t["chip"]["hidden"])
        a = self.out["after"]
        self.assertEqual((a["fetches"], self._rows(a, "tap-offer"), a["chip"]["hidden"]), ([], [], True), "nothing left to offer on the next coming-back")

    def test_an_offer_taken_before_this_pages_chat_pane_is_up_says_booting(self):
        # review find (2026-09-09, on #1157): the offer's own scenario is a relaunch on the start URL with the chip up
        # at boot, and the user taking it before the chat pane's socket has reported up. The road goes through land(),
        # so the #1127 latch applies: boot:true until {romp:'wsState',app:'chat',state:'up'}, and the kernel parks for
        # this page's pane instead of aiming at a same-wid socket the previous page left behind
        e = self.early["early"]
        self.assertEqual(e["fetches"], [["/reveal", {"sid": "S9", "wid": "W-test", "via": "offer", "boot": True}]])
        self.assertIn(["reveal-post", {"status": 200, "via": "offer", "boot": True}], e["diag"])
        self.assertIn({"romp": "tapLanded", "id": "N-1"}, e["ctrl"], "taken: the record is retired the same way")
        self.assertNotIn("/__romp/shown", e["keys"])
        self.assertTrue(e["chip"]["hidden"])

    def test_dismissing_retires_the_record_without_landing(self):
        s = self.out["second"]
        self.assertEqual(s["chip"], {"hidden": False, "text": "Open tests · from the notification", "acted": False, "disabled": False})
        self.assertEqual(self._rows(s, "tap-offer"), [{"shown": True, "via": "visible", "ageS": 60, "why": ""}])
        d = self.out["dismissed"]
        self.assertEqual(d["fetches"], [], "dismissed: nothing lands")
        self.assertEqual(d["posted"], [])
        self.assertIn(["tap-offer-dismiss", {"ageS": 60}], d["diag"])
        self.assertIn({"romp": "tapLanded", "id": "N-2"}, d["ctrl"])
        self.assertNotIn("/__romp/shown", d["keys"])
        self.assertTrue(d["chip"]["hidden"])

    def test_a_stored_tap_outranks_the_offer(self):
        w = self.out["tapWins"]
        self.assertEqual(w["fetches"], [["/reveal", {"sid": "S12", "wid": "W-test", "via": "store"}]], "the tap lands")
        self.assertEqual(self._rows(w, "tap-offer"), [], "the offer never shows")
        self.assertTrue(w["chip"]["hidden"])
        self.assertNotIn("/__romp/tap", w["keys"])
        self.assertNotIn("/__romp/shown", w["keys"], "…and its record is spent: the user tapped")

    def test_no_offer_for_the_session_already_in_front(self):
        a = self.out["already"]
        self.assertEqual(a["fetches"], [])
        self.assertTrue(a["chip"]["hidden"])
        self.assertEqual(self._rows(a, "tap-offer"), [{"shown": False, "via": "visible", "ageS": 2, "why": "active"}])
        self.assertIn({"romp": "tapLanded", "id": "N-4"}, a["ctrl"], "the notification's purpose is met: retired")
        self.assertNotIn("/__romp/shown", a["keys"])
        # …and at boot too, when the pane already shows that session
        b = self.active_at_boot["boot"]
        self.assertEqual(self._rows(b, "tap-offer"), [{"shown": False, "via": "boot", "ageS": 5, "why": "active"}])
        self.assertTrue(b["chip"]["hidden"])
        z = self.out["sidless"]
        self.assertEqual((z["fetches"], self._rows(z, "tap-offer"), z["chip"]["hidden"]), ([], [], True), "a record without a session is not an offer")
        self.assertNotIn("/__romp/shown", z["keys"])

    def test_a_deep_link_boot_outranks_the_offer(self):
        b = self.link["boot"]
        self.assertEqual(b["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}]], "the link alone lands")
        self.assertEqual(self._rows(b, "tap-offer"), [{"shown": False, "via": "boot", "ageS": 5, "why": "link"}])
        self.assertTrue(b["chip"]["hidden"])
        self.assertIn({"romp": "tapLanded", "id": "N-1"}, b["ctrl"])
        self.assertNotIn("/__romp/shown", b["keys"])

    def test_a_foreign_builds_fingerprint_files_sw_stale(self):
        s = self.out["stale"]
        self.assertEqual(self._rows(s, "tap-resume"),
                         [_fp({"found": False, "via": "visible", "store": True}, swVersion="other-build", swMatchesPage=False, lastPushAgeS=30, lastClickAgeS=20, clicks=3)])
        self.assertEqual(self._rows(s, "sw-stale"), [{"swVersion": "other-build", "pageVersion": "__ROMP_SWV__"}])

    def test_the_worker_is_asked_to_update_at_boot_and_on_every_visible(self):
        # a Home Screen app may not re-check its worker on relaunch — so the page asks, on the events such an app
        # produces, and files whether there was a registration to ask
        b = self.out["boot"]
        self.assertEqual(b["upd"], 1)
        self.assertEqual(self._rows(b, "sw-update"), [{"ok": True, "reg": True}])
        self.assertEqual(self.out["after"]["upd"], 2, "one more per visible: " + repr(self.out["after"]["upd"]))
        self.assertEqual(self._rows(self.out["after"], "sw-update"), [{"ok": True, "reg": True}])
        self.assertEqual(self.out["second"]["upd"], 3, "…and again on the next")
        n = self.no_reg["boot"]
        self.assertEqual(n["upd"], 0)
        self.assertEqual(self._rows(n, "sw-update"), [{"ok": False, "reg": False}], "no registration: said so, never a throw")


class LandingRevealResumesFromStore(unittest.TestCase):
    """2026-09-09, the phone with the app alive in the background: the tap brought the app forward and
    changed nothing, while the same tap after a force-quit landed. The worker had seen no client (iOS lists
    no window for a backgrounded Home Screen app), so it opened the deep link; iOS brought the EXISTING page
    forward without a load (no link) and without a client to message (no message), then ended the worker
    with its kept tap (no replay). The worker now writes the tap to the Cache API before it tries anything,
    and the page reads it on the events a resumed page produces — boot, visible, pageshow, focus — landing
    it once by id, retiring the entry, acking the worker, and filing a `tap-resume` row on every check."""
    @classmethod
    def setUpClass(cls):
        cls.warm = _run_reveal(_RESUME_WARM_DRIVER, href="http://localhost:7777/")
        cls.relaunch = _run_reveal(_RESUME_BOOT_DRIVER, href="http://localhost:7777/",
                                   tap={"id": "T-20", "sid": "S20", "host": "", "kind": "card", "cardId": "S20:g3", "url": "/?push-reveal=S20&push-card=S20%3Ag3", "t": 1})
        cls.link_other = _run_reveal(_RESUME_LINK_DRIVER, href="http://localhost:7777/?push-reveal=S1&push-card=S1%3Ag1",
                                     tap={"id": "T-21", "sid": "S3", "host": "", "kind": "turn", "cardId": "", "url": "/?push-reveal=S3", "t": 1})
        cls.link_same = _run_reveal(_RESUME_LINK_DRIVER, href="http://localhost:7777/?push-reveal=S1&push-card=S1%3Ag1",
                                    tap={"id": "T-22", "sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1", "url": "/?push-reveal=S1&push-card=S1%3Ag1", "t": 1})
        cls.no_store = _run_reveal(_RESUME_WARM_DRIVER, href="http://localhost:7777/", no_caches=True)

    @staticmethod
    def _rows(snap, what="tap-resume"):
        return [d for w, d in snap["diag"] if w == what]

    @staticmethod
    def _acks(snap):
        """the tapLanded acks, whichever worker they went to: the one that posted (ev.source — the message
        road) or the one in control (the store road has no sender to answer)"""
        return [m for m in snap["ack"] + snap["ctrl"] if m.get("romp") == "tapLanded"]

    def test_a_page_that_comes_back_lands_the_stored_tap_once_and_retires_it(self):
        w = self.warm
        self.assertEqual(w["boot"]["fetches"], [], "a plain start with nothing stored lands nothing")
        self.assertEqual(self._rows(w["boot"]), [_fp({"found": False, "via": "boot", "store": True})], "…and says the check ran")
        self.assertEqual(w["hidden"]["fetches"], [], "going hidden reads nothing")
        self.assertEqual(self._rows(w["hidden"]), [])
        v = w["visible"]
        self.assertEqual(v["fetches"], [["/reveal", {"sid": "S9", "wid": "W-test", "via": "store"}]], "a live page: no boot flag; the road is named")
        self.assertEqual(v["posted"], [{"romp": "revealCard", "itemId": "S9:g2", "sid": "S9"}], "a card kind scrolls the feed too")
        self.assertEqual(v["ctrl"], [{"romp": "tapReplay"}, {"romp": "tapLanded", "id": "T-9"}],
                         "the replay ask still goes out first; then the controlling worker is told the tap landed, so its kept copy and the entry go")
        self.assertEqual(v["keys"], [], "the page deletes the entry itself as well")
        self.assertEqual(self._rows(v), [_fp({"found": True, "via": "visible", "ageS": 5, "dup": False, "dropped": False, "sameSid": None})])
        self.assertIn(["reveal-post", {"status": 200, "via": "store", "boot": False}], v["diag"])
        for d in self._rows(v):
            self.assertNotIn("sid", d, "structure only")
        a = w["again"]
        self.assertEqual((a["fetches"], self._acks(a), a["posted"]), ([], [], []), "a second coming-back finds nothing")
        self.assertEqual(self._rows(a), [_fp({"found": False, "via": "visible", "store": True})])

    def test_pageshow_and_focus_are_roads_too_and_age_is_clipped_never_a_reason_to_drop(self):
        w = self.warm
        p = w["pageshow"]
        self.assertEqual(p["fetches"], [["/reveal", {"sid": "S11", "wid": "W-test", "via": "store"}]])
        self.assertEqual(p["posted"], [], "a turn: no card")
        self.assertEqual(self._rows(p), [_fp({"found": True, "via": "pageshow", "ageS": 400, "dup": False, "dropped": False, "sameSid": None})], "minutes old is still the user's tap")
        self.assertEqual(self._acks(p), [{"romp": "tapLanded", "id": "T-11"}])
        f = w["focus"]
        self.assertEqual(f["fetches"], [["/reveal", {"sid": "boxa:S12", "wid": "W-test", "via": "store"}]], "a federated sid lands as-is")
        self.assertEqual(self._rows(f), [_fp({"found": True, "via": "focus", "ageS": -1, "dup": False, "dropped": False, "sameSid": None})], "no timestamp: -1, never a throw")
        self.assertEqual(f["ctrl"][0], {"romp": "tapReplay"}, "focus asks the worker for its kept copy as well")
        self.assertEqual(f["keys"], [])

    def test_one_tap_by_two_roads_lands_once_whichever_comes_first(self):
        w = self.warm
        s1 = w["swFirst"]
        self.assertEqual(s1["fetches"], [["/reveal", {"sid": "S13", "wid": "W-test", "via": "sw"}]])
        self.assertEqual(s1["keys"], [], "landing by the message retires the stored copy of the same tap too")
        self.assertEqual(s1["ack"], [{"romp": "tapLanded", "id": "T-13"}], "acked to the worker that posted")
        self.assertEqual((w["swThenStore"]["fetches"], self._rows(w["swThenStore"])), ([], [_fp({"found": False, "via": "visible", "store": True})]))
        s2 = w["storeFirst"]
        self.assertEqual(s2["fetches"], [["/reveal", {"sid": "S14", "wid": "W-test", "via": "store"}]])
        self.assertEqual(s2["posted"], [{"romp": "revealCard", "itemId": "S14:g1", "sid": "S14"}])
        after = w["storeThenSw"]
        self.assertEqual((after["fetches"], after["posted"], self._acks(after)), ([], [], []), "the message for a tap the store landed is a dup")
        self.assertEqual(after["diag"][0][1]["dup"], True)
        # two checks in flight at once: one lands, the other files itself as a dup and retires too
        r = w["race"]
        self.assertEqual(r["fetches"], [["/reveal", {"sid": "S16", "wid": "W-test", "via": "store"}]])
        rows = self._rows(r)
        self.assertEqual(sorted((x["via"], x["dup"]) for x in rows), [("pageshow", True), ("visible", False)], "the first read lands, the second is a dup")
        self.assertEqual(r["keys"], [])
        # a record with no session is not a tap: nothing lands, the entry is cleared, the row says nothing was found
        z = w["sidless"]
        self.assertEqual((z["fetches"], self._acks(z), z["keys"]), ([], [], []))
        self.assertEqual(self._rows(z), [_fp({"found": False, "via": "visible", "store": True})])

    def test_a_relaunch_on_the_start_url_lands_the_stored_tap_as_a_boot(self):
        # iOS reopens the installed app on its start URL, not the link: the page boots with the tap in the store.
        # Its chat pane is not connected yet, so /reveal carries boot:true and the kernel parks for this wid
        b = self.relaunch["boot"]
        self.assertEqual(b["fetches"], [["/reveal", {"sid": "S20", "wid": "W-test", "via": "store", "boot": True}]])
        self.assertEqual(b["diag"][0], ["deeplink", {"hasSid": False, "hasCard": False, "controlled": True}])
        rows = self._rows(b)
        self.assertEqual(len(rows), 1)
        self.assertEqual({k: rows[0][k] for k in ("found", "via", "dup", "dropped", "sameSid")}, {"found": True, "via": "boot", "dup": False, "dropped": False, "sameSid": None})
        self.assertEqual(rows[0]["ageS"], 86400, "age is clipped, never a bare timestamp difference")
        self.assertEqual(self._acks(b), [{"romp": "tapLanded", "id": "T-20"}])
        self.assertEqual(b["keys"], [])
        self.assertEqual(b["posted"], [], "the card waits for the feed")
        self.assertEqual(self.relaunch["postedAfterFeedReady"], [{"romp": "revealCard", "itemId": "S20:g3", "sid": "S20"}])
        h = self.relaunch["handed"]
        self.assertEqual((h["fetches"], h["posted"]), ([], []), "the message handed to the opened window is the same tap: a dup")
        self.assertEqual(h["diag"][0][1]["dup"], True)

    def test_a_boot_on_the_deep_link_lands_the_link_and_drops_the_stored_tap(self):
        # the worker opened THIS page on the link, so the link is the newest word: a stored tap is either the same
        # one (landing by the link already) or an older one the link outranks — dropped either way, never a second
        # /reveal, and the row says whether the two agreed
        o = self.link_other["boot"]
        self.assertEqual(o["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}]], "the link alone lands")
        rows = self._rows(o)
        self.assertEqual(len(rows), 1)
        self.assertEqual({k: rows[0][k] for k in ("found", "via", "dup", "dropped", "sameSid")}, {"found": True, "via": "boot", "dup": False, "dropped": True, "sameSid": False})
        self.assertEqual(self._acks(o), [{"romp": "tapLanded", "id": "T-21"}], "retired all the same")
        self.assertEqual(o["keys"], [])
        s = self.link_same["boot"]
        self.assertEqual(s["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}]])
        self.assertEqual(self._rows(s)[0]["sameSid"], True)
        self.assertEqual(s["keys"], [])
        h = self.link_same["handed"]
        self.assertEqual(h["fetches"], [], "the same tap's message, by id: a dup, so the cold start makes ONE /reveal now")
        self.assertEqual(h["diag"][0][1]["dup"], True)

    def test_a_window_without_a_cache_api_says_so_and_never_throws(self):
        n = self.no_store
        self.assertEqual(self._rows(n["boot"]), [_fp({"found": False, "via": "boot", "store": False})])
        self.assertEqual(self._rows(n["visible"]), [_fp({"found": False, "via": "visible", "store": False})])
        self.assertEqual(n["visible"]["fetches"], [])
        self.assertEqual(n["visible"]["ctrl"], [{"romp": "tapReplay"}], "the worker is still asked")



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
