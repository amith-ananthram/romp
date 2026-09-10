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

  * the ledger (2026-09-09, the partition round) — every session-addressed push files a row per device with
    an unguessable pid the payload carries; the worker acks 'shown' and 'clicked' by pid alone (POST
    /push/ack, no token: the worker that fields a push on iOS runs in a storage partition without the
    cookie), the page asks GET /push/pending for its own endpoint and settles the row (/push/landed,
    /push/dismissed); rows are read from disk on every op, so a restart loses nothing.

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
from importlib.machinery import SourceFileLoader
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
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
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
km = SourceFileLoader("romp_kernel_webpush", os.path.join(BIN, "romp-kernel")).load_module()


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
        # the ONE outbound fetch is the kernel ack (2026-09-09, the partition round): POST /push/ack by pid — a call the
        # worker MAKES, never a request it intercepts; nothing else in the worker fetches
        self.assertEqual(js.count("fetch("), js.count("fetch('/push/ack'"), "no fetch but the ack")
        self.assertGreater(js.count("fetch('/push/ack'"), 0)
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
  META.push({ id: c.id, diag: c.diag, pid: c.pid }); delete c.id; delete c.diag; delete c.pid; return c; }   // pid (2026-09-09): the kernel's handle, per push
// the kernel ack (2026-09-09, the partition round): every fetch the worker makes, with its body and how far LOG had got —
// so a test can say the ack was started BEFORE the show (push) and before the close (click)
const FLOG = [];
global.fetch = (path, init) => { FLOG.push([path, init && init.body ? JSON.parse(init.body) : null, LOG.length, !!(init && init.keepalive), (init && init.method) || 'GET']);
  return Promise.resolve({ ok: true, status: 200 }); };
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
  // THE FINGERPRINT (2026-09-09, the warm-app round): install writes a fresh record for this build over the previous
  // build's, activate its takeover; every push stamps its arrival; every click stamps itself FIRST and counts
  const fpRec = async () => (STORE.has('/__romp/sw') ? STORE.get('/__romp/sw').clone().json() : null);
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
  out.pushSync2 = LOG.map((x) => x[0]);
  await pushWait;
  out.fpPushed = await fpRec();
  out.storeKeysAfterPush = [...STORE.keys()].sort();   // the fingerprint alone: no '/__romp/shown' record since the offer went (2026-09-09)
  // a show that FAILS still leaves the stamp (headless browsers refuse showNotification)
  const showOk = global.self.registration.showNotification;
  global.self.registration.showNotification = (t, o) => { LOG.push(['show', t, o]); return Promise.reject(new Error('denied')); };
  SLOG.length = 0; LOG.length = 0;
  H.push({ data: { json: () => ({ title: 'api', body: 'finished', sid: 'S2', tag: 'romp:S2', data: { sid: 'S2', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S2', name: 'api' } }) }, waitUntil: (p) => { pushWait = p; } });
  out.pushRefused = await pushWait.then(() => 'resolved', (e) => 'rejected: ' + e.message);
  for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
  out.fpAfterRefused = await fpRec();
  global.self.registration.showNotification = showOk;
  // a sid-less push (a test with no session in front) stamps the arrival like any other
  SLOG.length = 0; LOG.length = 0;
  H.push({ data: { json: () => ({ title: 'romp', body: 'Test notification', tag: 'romp:test', data: { sid: '', host: '', kind: 'test', cardId: '', url: '/', name: '' } }) }, waitUntil: (p) => { pushWait = p; } });
  await pushWait;
  out.fpAfterSidless = await fpRec();
  SLOG.length = 0;
  out.clickSidless = await tap({ sid: '', host: '', kind: 'test', cardId: '', url: '/' }, []);
  out.fpAfterSidlessClick = await fpRec();
  SLOG.length = 0;
  out.clickStamp = await tap(data, [win(true)]);
  out.clickStampOps = SLOG.map((x) => x.slice(0, 3));
  out.fpAfterClick = await fpRec();
  // THE KERNEL ACK (2026-09-09, the partition round): a push whose routing block carries a pid is acked 'shown' to the
  // kernel before the show is attempted, and a tap on it 'clicked' as the FIRST thing the click handler does; the pid
  // rides the message and the stored tap; a push or a tap without a pid acks nothing
  const pidData = Object.assign({}, data, { pid: 'PID-test-000000001', name: 'web' });
  STORE.clear(); FLOG.length = 0; LOG.length = 0; SLOG.length = 0;
  H.push({ data: { json: () => ({ title: 'romp: web', body: 'Needs you: x', sid: 'S1', tag: 'romp:S1', data: pidData }) }, waitUntil: (p) => { pushWait = p; } });
  out.ackPushSync = FLOG.slice();                    // before the handler yields: the ack is already on its way
  out.ackPushSyncLog = LOG.map((x) => x[0]);
  await pushWait;
  out.ackPush = FLOG.slice();
  out.ackStoreKeys = [...STORE.keys()].sort();   // the fingerprint alone: the kernel's row is the one record of the show
  FLOG.length = 0; META.length = 0;
  out.ackClick = await tap(pidData, [win(true)]);
  out.ackClickFetches = FLOG.slice();
  out.ackClickMeta = META[0];
  out.ackClickRec = await rec();
  FLOG.length = 0; META.length = 0;
  out.ackClickCold = await tap(pidData, []);          // the phone's road: no client, openWindow — the ack goes out first all the same
  out.ackClickColdFetches = FLOG.slice();
  FLOG.length = 0; META.length = 0;
  await tap(data, [win(true)]);                       // no pid (an older kernel's push): nothing acked, the message carries an empty pid
  out.noPidClickFetches = FLOG.slice();
  out.noPidClickMeta = META[0];
  H.push({ data: { json: () => ({ title: 't', body: 'b', sid: 'S9', data }) }, waitUntil: (p) => { pushWait = p; } });
  await pushWait;
  out.noPidPushFetches = FLOG.slice();
  // THE CLOSE (2026-09-09): a swipe-dismiss, where the platform reports one, is acked 'closed' by pid alone, keepalive,
  // under the event's waitUntil — so the page never reads that notification's absence from the screen as a tap; a
  // notification without a pid has nothing to say
  const close = async (d) => { FLOG.length = 0; LOG.length = 0; const w = []; H.notificationclose({ notification: { data: d }, waitUntil: (p) => w.push(p) }); await Promise.all(w); return { fetches: FLOG.slice(), waited: w.length, log: LOG.slice() }; };
  out.close = await close(pidData);
  out.closeNoPid = await close(data);
  out.closeNoData = await close(undefined);
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
        # the fingerprint's stamp shares the cache since 2026-09-09 (its own test below); the TAP entry's ops are
        # what this pin is about
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
        self.assertEqual([x for x in o["storeSidlessOps"] if x[1] == "/__romp/tap"], [])
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
        self.assertEqual(o["storeKeysAfterPush"], ["/__romp/sw"], "the fingerprint is the one record a push leaves here: no '/__romp/shown' since the offer went (2026-09-09)")
        # a show that FAILS (headless browsers refuse showNotification) still fails the push the way it always did, and
        # the arrival is stamped all the same; a sid-less push (a test with no session in front) stamps too
        self.assertEqual(o["pushRefused"], "rejected: denied")
        self.assertEqual(o["fpAfterRefused"]["lastPushSid"], "S2")
        self.assertEqual(o["fpAfterSidless"]["lastPushSid"], "")
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

    def test_a_push_with_a_pid_is_acked_shown_before_the_show_and_its_tap_acked_clicked_first(self):
        # 2026-09-09: the kernel is the meeting point — the push carries a pid the kernel issued for THIS device, and the
        # worker tells the kernel what became of it, by that pid alone (a worker's fetch carries no token), keepalive so a
        # worker the platform ends early still gets it out. The page reads the kernel's row when it comes forward
        o = self.out
        self.assertEqual(o["ackPushSync"], [["/push/ack", {"pid": "PID-test-000000001", "stage": "shown", "v": "__ROMP_SWV__"}, 0, True, "POST"]],
                         "the shown ack is started before the show is logged (LOG.length 0), keepalive, with the worker's build string")
        self.assertEqual(o["ackPushSyncLog"], ["show"], "…and the show is still the synchronous act of the handler")
        self.assertEqual(len(o["ackPush"]), 1, "one ack per push")
        self.assertEqual(o["ackStoreKeys"], ["/__romp/sw"], "the kernel's row is the one record of the show: nothing else written for a page to read")
        c = o["ackClickFetches"]
        self.assertEqual(c, [["/push/ack", {"pid": "PID-test-000000001", "stage": "clicked", "v": "__ROMP_SWV__"}, 0, True, "POST"]],
                         "the clicked ack is the FIRST thing the click handler does: before the close is logged")
        self.assertEqual(o["ackClick"]["log"], [["close"], self.MATCH, ["focus"], ["post", self.MSG]], "the tap lands exactly as before")
        self.assertEqual(o["ackClick"]["waited"], 1, "the ack rides the tap's one waitUntil")
        self.assertEqual(o["ackClickMeta"]["pid"], "PID-test-000000001", "the message carries the pid")
        self.assertEqual(o["ackClickRec"]["pid"], "PID-test-000000001", "…and so does the stored tap")
        self.assertEqual(o["ackClickColdFetches"][0][1]["stage"], "clicked", "the phone's road (no client, openWindow): acked first all the same")
        self.assertEqual(o["ackClickColdFetches"][0][2], 0)
        self.assertEqual(o["ackClickCold"]["log"], [["close"], self.MATCH, ["openWindow", self.URL]])
        # no pid — an older kernel's push: nothing to ack, and the message says so with an empty pid
        self.assertEqual(o["noPidClickFetches"], [])
        self.assertEqual(o["noPidClickMeta"]["pid"], "")
        self.assertEqual(o["noPidPushFetches"], [])

    def test_a_swipe_dismiss_is_acked_closed_by_pid_alone(self):
        # 2026-09-09: the page reads a shown notification GONE from the screen as a tap (a live Home Screen app on iOS
        # gets no notificationclick), so a notification the user swiped away must say so where the platform reports the
        # close — acked 'closed' by pid, keepalive, under the event's waitUntil. No pid (an older kernel's push, or a
        # notification with no data at all): nothing to say, and no throw
        o = self.out
        self.assertEqual(o["close"]["fetches"], [["/push/ack", {"pid": "PID-test-000000001", "stage": "closed", "v": "__ROMP_SWV__"}, 0, True, "POST"]])
        self.assertEqual(o["close"]["waited"], 1, "the ack rides the close's waitUntil")
        self.assertEqual(o["close"]["log"], [], "a close opens nothing, focuses nothing, posts nothing")
        self.assertEqual((o["closeNoPid"]["fetches"], o["closeNoPid"]["waited"]), ([], 1))
        self.assertEqual((o["closeNoData"]["fetches"], o["closeNoData"]["waited"]), ([], 1))
        self.assertIn("addEventListener('notificationclose'", km._SW_JS)


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
        self.assertEqual(set(body["data"]), {"sid", "host", "kind", "cardId", "url", "name", "pid"})   # name (2026-09-09): the session's display name, for the offer chip; pid: the kernel's handle on this push to this device (PushLedger)


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
        self.assertEqual(km._push_payload("romp: web", "b", "SID-web", pid="PID-x")["data"]["pid"], "PID-x")
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
        self.assertEqual(d["data"], {"sid": "", "host": "", "kind": "test", "cardId": "", "url": "/", "name": "", "pid": ""})
        self.assertEqual(d["tag"], "romp:test")

    def test_the_payload_names_the_session_the_way_the_test_push_does(self):
        # 2026-09-09: the ledger row files the session's name off the payload (the kernel's lines and the page's rows
        # name it from there), so every push carries `name`, resolved by ONE helper in _push_test's order of authority — the names registry for
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


class PushLedger(unittest.TestCase):
    """The kernel as the meeting point (2026-09-09; the ledger block above _push_ledger in the kernel has the finding):
    every session-addressed push files a row per device — {pid, endpoint, sid, host, kind, cardId, name, sentAt,
    shownAt, tappedAt, closedAt, landedAt, supersededAt, droppedAt, swVersion} — and the payload to that device
    carries the row's pid. Rows are read from disk on every op (restart-proof), capped per endpoint, 0600, and go
    with their endpoint's subscription. This is also the seed of the delivery ledger the backlog names."""
    EP_A = "https://push.example.net/send/phone-a"
    EP_B = "https://push.example.net/send/phone-b"
    ROW_KEYS = {"pid", "endpoint", "sid", "host", "kind", "cardId", "name", "sentAt", "shownAt", "tappedAt", "closedAt", "landedAt", "supersededAt", "droppedAt", "swVersion"}

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
            self.assertRegex(r["pid"], r"^[A-Za-z0-9_-]{22}$", "secrets.token_urlsafe(16)")
            self.assertEqual({k: r[k] for k in ("sid", "host", "kind", "cardId", "name")},
                             {"sid": "SID-web", "host": "", "kind": "card", "cardId": "SID-web:g1", "name": "SID-web"})
            self.assertGreater(r["sentAt"], 0)
            self.assertEqual((r["shownAt"], r["tappedAt"], r["closedAt"], r["landedAt"], r["supersededAt"], r["droppedAt"], r["swVersion"]), (0, 0, 0, 0, 0, 0, ""))
        self.assertNotEqual(by_ep[self.EP_A]["pid"], by_ep[self.EP_B]["pid"])
        self.assertEqual(oct(os.stat(km._push_ledger_path()).st_mode & 0o777), "0o600", "endpoints are capability URLs")
        # the gist is untouched: title, body and the rest of the routing block are what they were
        self.assertEqual((seen[self.EP_A]["title"], seen[self.EP_A]["body"], seen[self.EP_A]["data"]["url"]),
                         ("romp: web", "Needs you: pick one", "/?push-reveal=SID-web&push-card=SID-web%3Ag1"))

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
        self.assertEqual(seen[0]["data"]["pid"], "")

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
        # 2026-09-09: the newest row alone named a push for ANOTHER session, sent 40 s after the one the user tapped; the
        # page needs every unsettled row to hold against the screen, newest first
        self.assertEqual(km._push_pending(self.EP_A), {"rows": []}, "nothing filed: nothing pending")
        p1 = km._push_ledger_add(self.EP_A, "SID-web", kind="turn", name="web")
        p2 = km._push_ledger_add(self.EP_A, "SID-api", kind="card", card_id="SID-api:g2", name="api")
        p3 = km._push_ledger_add(self.EP_A, "SID-tests", kind="turn", name="tests")
        km._push_ledger_add(self.EP_B, "SID-web", kind="turn", name="web")
        rows = km._push_pending(self.EP_A)["rows"]
        self.assertEqual([r["pid"] for r in rows], [p3, p2, p1], "every unsettled row of the device, newest first")
        r = rows[1]
        self.assertEqual(set(r), {"pid", "sid", "host", "kind", "cardId", "name", "stage", "ageS"}, "what the page reads")
        self.assertEqual((r["sid"], r["kind"], r["cardId"], r["name"], r["stage"]), ("SID-api", "card", "SID-api:g2", "api", "sent"), "no ack at all is 'sent'")
        self.assertGreaterEqual(r["ageS"], 0)
        self.assertLessEqual(r["ageS"], 1)
        # the stages, strongest word first: clicked over closed over shown over sent
        self.assertIsNotNone(km._push_ledger_stamp(p2, "shown", "abc.123"))
        self.assertEqual(self._pending(self.EP_A), [(p3, "sent"), (p2, "shown"), (p1, "sent")])
        self.assertIsNotNone(km._push_ledger_stamp(p1, "closed"))
        self.assertEqual(self._pending(self.EP_A), [(p3, "sent"), (p2, "shown"), (p1, "closed")], "a swipe-dismiss is on the row, and the row is still listed: the page leaves it alone by its stage")
        self.assertIsNotNone(km._push_ledger_stamp(p2, "clicked"))
        self.assertEqual(self._pending(self.EP_A)[1], (p2, "clicked"))
        row = [r for r in km._push_ledger() if r["pid"] == p2][0]
        self.assertGreater(row["shownAt"], 0)
        self.assertGreater(row["tappedAt"], 0)
        self.assertGreater([r for r in km._push_ledger() if r["pid"] == p1][0]["closedAt"], 0)
        self.assertEqual(row["swVersion"], "abc.123", "the acking worker's build, kept")
        t0 = row["shownAt"]
        km._push_ledger_stamp(p2, "shown")
        self.assertEqual([r for r in km._push_ledger() if r["pid"] == p2][0]["shownAt"], t0, "the first stamp stands: a repeated ack is idempotent")
        # the three settles each retire a row from the list: landed, superseded, dropped
        km._push_ledger_stamp(p2, "landed")
        self.assertEqual(self._pending(self.EP_A), [(p3, "sent"), (p1, "closed")], "settled rows are skipped")
        km._push_ledger_stamp(p3, "superseded")
        self.assertEqual(self._pending(self.EP_A), [(p1, "closed")])
        km._push_ledger_stamp(p1, "dropped")
        self.assertEqual(km._push_pending(self.EP_A), {"rows": []})
        self.assertEqual([r["sid"] for r in km._push_pending(self.EP_B)["rows"]], ["SID-web"], "another device's rows are its own")
        self.assertIsNone(km._push_ledger_stamp("never-issued-pid-0001", "shown"), "an unknown pid changes nothing")
        self.assertEqual(set(km._PUSH_STAGE_FIELD), set(km._PUSH_ACK_STAGES) | set(km._PUSH_SETTLE_STAGES), "every stage is an ack or a settle; 'dismissed' went with the chip")
        self.assertNotIn("dismissed", km._PUSH_STAGE_FIELD)

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
        self.assertGreater([r for r in km._push_ledger() if r["pid"] == old_web][0]["supersededAt"], 0)
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
        self.assertEqual(km._push_pending(self.EP_A)["rows"][0]["stage"], "clicked")
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
        # roads in _REVEAL_ROADS and logs any other word as 'other'; a shell of a build before the field sends none,
        # and that stays the bare line. 'offer' — the chip's road, admitted for a day — is refused since the chip
        # went (2026-09-09: the user wants no such offer): a stale shell naming it is logged as 'other' like any word
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self._post("/reveal", {"sid": "SID-x", "wid": "W-x", "via": "sw\n[reveal] forged sid=SID-z: delivered"})
            self._post("/reveal", {"sid": "SID-y", "wid": "W-y", "via": "store"})
            self._post("/reveal", {"sid": "SID-v", "wid": "W-v", "via": "vanish"})   # the vanished notification's road (2026-09-09): admitted by name
            self._post("/reveal", {"sid": "SID-u", "wid": "W-u", "via": "offer"})    # the retired chip's road: no longer a word the journal takes
            self._post("/reveal", {"sid": "SID-w", "wid": "W-w"})
        self.assertEqual(code, 200)
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("[reveal]")]
        self.assertEqual(lines, ["[reveal] other sid=SID-x wid=W-x: parked",
                                 "[reveal] store sid=SID-y wid=W-y: parked",
                                 "[reveal] vanish sid=SID-v wid=W-v: parked",
                                 "[reveal] other sid=SID-u wid=W-u: parked",
                                 "[reveal] shell sid=SID-w wid=W-w: parked"])
        self.assertNotIn("forged", buf.getvalue())
        self.assertEqual(km._REVEAL_ROADS, frozenset({"sw", "link", "store", "ack", "vanish"}))   # 'ack' and 'vanish' (2026-09-09): the kernel's ledger — a tap the worker acked, and a shown notification gone from the screen


class PushLedgerRoutes(unittest.TestCase):
    """The ledger's routes over the real handler. POST /push/ack is authenticated by the PID ALONE — no token, no
    cookie: a worker's fetch carries no token header (the ledger block above _push_ledger in the kernel). The pid is
    128 unguessable bits the kernel issued, good for three timestamps on one row and nothing else. The page's routes
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
        self.assertEqual(km._push_pending(self.EP)["rows"][0]["stage"], "clicked")
        # a cross-site Origin with no token is what the partition looks like from here: still the pid decides
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
        for body in ({"pid": pid, "stage": "landed"}, {"pid": pid, "stage": "superseded"}, {"pid": pid, "stage": "dropped"}, {"pid": pid, "stage": ""}, {"pid": "short", "stage": "shown"},
                     {"pid": pid + "\n[push] forged", "stage": "shown"}, {"stage": "shown"}, [pid]):
            code, _ = self._req("POST", "/push/ack", body, token=False)
            self.assertEqual(code, 400, repr(body))
        code, _ = self._req("POST", "/push/ack", raw=b"nope", token=False)
        self.assertEqual(code, 400)
        code, _ = self._req("POST", "/push/ack", raw=b"{" + b" " * km._PUSH_ACK_MAX_BYTES + b"}", token=False)
        self.assertEqual(code, 413, "capped far below the authenticated routes' limit: no token gates this read")
        self.assertEqual((self._row(pid)["shownAt"], self._row(pid)["tappedAt"]), (0, 0), "nothing stamped")
        self.assertEqual(len(km._push_ledger()), 1, "no row minted by a caller")

    def test_the_pages_routes_ride_the_token(self):
        pid = km._push_ledger_add(self.EP, "SID-api", kind="turn")
        for method, path, body in (("GET", "/push/pending?endpoint=" + self.EP, None), ("POST", "/push/landed", {"pid": pid}),
                                   ("POST", "/push/superseded", {"pid": pid}), ("POST", "/push/dropped", {"pid": pid})):
            code, _ = self._req(method, path, body, token=False)
            self.assertEqual(code, 403, path)
        self.assertEqual((self._row(pid)["landedAt"], self._row(pid)["supersededAt"], self._row(pid)["droppedAt"]), (0, 0, 0))
        code, _ = self._req("GET", "/push/pending")
        self.assertEqual(code, 400, "no endpoint named")
        code, _ = self._req("POST", "/push/dismissed", {"pid": pid})
        self.assertNotEqual(code, 200, "no such route since the chip went (2026-09-09): nothing the page could dismiss")
        self.assertEqual(self._row(pid).get("dismissedAt"), None)

    def test_a_closed_ack_is_recorded_and_lined_and_a_shown_ack_supersedes_the_older_rows_for_that_session(self):
        import contextlib, urllib.parse
        old_api = km._push_ledger_add(self.EP, "SID-api", kind="turn", name="api")
        web = km._push_ledger_add(self.EP, "SID-web", kind="turn", name="web")
        new_api = km._push_ledger_add(self.EP, "SID-api", kind="card", card_id="SID-api:g2", name="api")
        q = "/push/pending?endpoint=" + urllib.parse.quote(self.EP, safe="")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, body = self._req("POST", "/push/ack", {"pid": old_api, "stage": "shown", "v": "abc.1"}, token=False)
            self.assertEqual(code, 200)
            code, body = self._req("POST", "/push/ack", {"pid": web, "stage": "closed", "v": "abc.1"}, token=False)   # swiped away
            self.assertEqual((code, json.loads(body)), (200, {"ok": True, "stage": "closed"}))
            code, _ = self._req("POST", "/push/ack", {"pid": new_api, "stage": "shown", "v": "abc.1"}, token=False)   # the tag replaced old_api's notification
            self.assertEqual(code, 200)
        self.assertGreater(self._row(web)["closedAt"], 0)
        self.assertGreater(self._row(old_api)["supersededAt"], 0, "the older unsettled row for the same session on this device")
        self.assertEqual(self._row(new_api)["supersededAt"], 0)
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")],
                         ["[push] ack stage=shown sid=SID-api endpoint=push.example.net",
                          "[push] ack stage=closed sid=SID-web endpoint=push.example.net",
                          "[push] ack stage=shown sid=SID-api endpoint=push.example.net",
                          "[push] superseded sid=SID-api endpoint=push.example.net"])
        code, body = self._req("GET", q)
        self.assertEqual([(r["pid"], r["stage"]) for r in json.loads(body)["rows"]], [(new_api, "shown"), (web, "closed")],
                         "the superseded row is gone from the list; the closed one stays, wearing its stage, for the page to leave alone")

    def test_pending_lists_every_unsettled_push_newest_first_and_the_three_settles_retire_them(self):
        import contextlib, urllib.parse
        p1 = km._push_ledger_add(self.EP, "SID-web", kind="turn", name="web")
        p2 = km._push_ledger_add(self.EP, "SID-api", kind="card", card_id="SID-api:g2", name="api")
        p3 = km._push_ledger_add(self.EP, "SID-tst", kind="turn", name="tests")
        q = "/push/pending?endpoint=" + urllib.parse.quote(self.EP, safe="")
        code, body = self._req("GET", q)
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(list(d), ["rows"])
        self.assertEqual([r["pid"] for r in d["rows"]], [p3, p2, p1], "every unsettled row, newest first (2026-09-09: the newest alone named another session's push)")
        self.assertEqual({k: d["rows"][1][k] for k in ("sid", "name", "kind", "cardId", "stage")}, {"sid": "SID-api", "name": "api", "kind": "card", "cardId": "SID-api:g2", "stage": "sent"})
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
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
        self.assertEqual(json.loads(body), {"rows": []})
        self.assertEqual((self._row(p3)["supersededAt"] > 0, self._row(p1)["droppedAt"] > 0, self._row(p3)["landedAt"], self._row(p1)["landedAt"]), (True, True, 0, 0), "each settle stamps its own field, never landedAt")
        self.assertEqual([l for l in buf.getvalue().splitlines() if l.startswith("[push]")],
                         ["[push] landed sid=SID-api endpoint=push.example.net",
                          "[push] superseded sid=SID-tst endpoint=push.example.net",
                          "[push] dropped sid=SID-web endpoint=push.example.net",
                          "[push] landed: unknown pid"])


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
        # …or the pane's rendered tabs (2026-09-09, the served offer leg): the tabs come over that very socket, so an active
        # tab in the chat iframe's DOM proves it was up even when its message beat this script (LandingRevealAsksTheLedger)
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
const FETCHES = [], POSTED = [], NOTES = [], REPLACED = [], WIN = [], SW = [], DOC = [], PAGESHOW = [], FOCUS = [], CTRL = [], ACK = [], DIAG = [], UPD = [], GETS = [], GETN = [];
let fetchOk = true, fetchFail = false;
// the kernel's ledger (2026-09-09): what GET /push/pending answers, reassignable by a driver
let PENDING = process.env.ROMP_TEST_PENDING ? JSON.parse(process.env.ROMP_TEST_PENDING) : {};
// the notifications still on this device's screen (2026-09-09), as registration.getNotifications() lists them: the pids
// in ROMP_TEST_DISPLAYED, reassignable by a driver; getnFail: the call throws (a screen the page cannot read)
let DISPLAYED = process.env.ROMP_TEST_DISPLAYED ? JSON.parse(process.env.ROMP_TEST_DISPLAYED) : [], getnFail = false;
const feedWin = { postMessage: (m) => POSTED.push(m) };
global.window = global;
// the chat pane's active tab, as the same-origin iframe DOM the script reads: ROMP_TEST_ACTIVE at boot, reassignable
let activeSid = process.env.ROMP_TEST_ACTIVE || '';
const chatFrame = { contentDocument: { querySelector: () => (activeSid ? { getAttribute: () => activeSid } : null) } };
// no chip, no shell element of the script's own: the two pane iframes are all it ever looks up (the offer chip's three
// nodes lived here for a day, 2026-09-09)
global.document = { getElementById: (id) => (id === 'f-feed' ? { contentWindow: feedWin } : id === 'f-chat' ? chatFrame : null),
  addEventListener: (k, f) => { if (k === 'visibilitychange') DOC.push(f); }, visibilityState: 'visible' };
global.sessionStorage = { getItem: (k) => (k === 'romp:wid' ? 'W-test' : null) };
global.addEventListener = (k, f) => { if (k === 'message') WIN.push(f); if (k === 'pageshow') PAGESHOW.push(f); if (k === 'focus') FOCUS.push(f); };
// the registration the page asks to update() at boot and on every visible (2026-09-09); ROMP_TEST_NO_REG: none registered
// …its pushManager (2026-09-09): this page's own subscription, ROMP_TEST_ENDPOINT, or none (a device that never opted in)
// …and its getNotifications (2026-09-09): the screen, as DISPLAYED above; ROMP_TEST_NO_GETN: a browser without the method
const REG = { update: () => { UPD.push(1); return Promise.resolve(); },
              pushManager: { getSubscription: () => Promise.resolve(process.env.ROMP_TEST_ENDPOINT ? { endpoint: process.env.ROMP_TEST_ENDPOINT } : null) } };
if (!process.env.ROMP_TEST_NO_GETN) REG.getNotifications = () => { GETN.push(DISPLAYED.slice()); return getnFail ? Promise.reject(new Error('no screen')) : Promise.resolve(DISPLAYED.map((pid) => ({ data: { pid } }))); };
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
function seedK(k, v) { STORE.set(k, new Response(JSON.stringify(v))); }   // any entry: the fingerprint
if (process.env.ROMP_TEST_TAP) seed(JSON.parse(process.env.ROMP_TEST_TAP));
if (process.env.ROMP_TEST_SEED) { const s = JSON.parse(process.env.ROMP_TEST_SEED); for (const k in s) seedK(k, s[k]); }
global.fetch = (path, init) => {
  if (!init) { GETS.push(path); return fetchFail ? Promise.reject(new Error('down')) : Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(PENDING) }); }   // the ledger's GET (2026-09-09): no init, an answer with a body
  FETCHES.push([path, JSON.parse(init.body)]);
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


def _run_reveal(driver, href=None, tap=None, no_caches=False, seed=None, active=None, no_reg=False, endpoint=None, pending=None,
                displayed=None, no_getn=False):
    """node runs the harness + the shell's reveal script + `driver`, booting on `href` (default: the deep
    link) with `tap` already in the store (the worker wrote it before this page) — see the harness's env.
    `seed`: other entries already in the store ({key: record} — the fingerprint); `active`: the chat pane's
    active tab at boot; `no_reg`: no service worker registration to update; `endpoint`: this page's push
    subscription endpoint (none = a device that never opted in); `pending`: what the kernel's GET /push/pending
    answers at boot (2026-09-09); `displayed`: the pids of the notifications still on the screen at boot, as
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
                                                                        ["tap-pending", {"via": "boot", "sub": False, "rows": 0}],   # the kernel's ledger, asked after the store (2026-09-09): no subscription on this device, said so, nothing fetched
                                                                        ["sw-update", {"ok": True, "reg": True}]]))
        self.assertEqual(b.get("gets", []), [], "no subscription: the kernel is not asked")
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
const snap = () => ({ fetches: FETCHES.slice(), posted: POSTED.slice(), diag: DIAG.slice(), ack: ACK.slice(), ctrl: CTRL.slice(), keys: [...STORE.keys()], notes: NOTES.slice(), upd: UPD.length, gets: GETS.slice(), getn: GETN.length });
const reset = () => { FETCHES.length = 0; POSTED.length = 0; DIAG.length = 0; ACK.length = 0; CTRL.length = 0; NOTES.length = 0; GETS.length = 0; GETN.length = 0; };
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

# the fingerprint (2026-09-09): a page booting on the plain start URL with the worker's fingerprint in the store, coming
# back twice, then reading another build's record
_FINGERPRINT_DRIVER = _RESUME_LIB + r"""
(async () => {
  const out = {};
  await settle();
  out.boot = snap();
  winMsg({ romp: 'ready', app: 'feed' });
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });
  reset();
  await flip('visible');
  out.after = snap();
  reset();
  await flip('visible');
  out.second = snap();
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


class LandingRevealReadsTheFingerprint(unittest.TestCase):
    """2026-09-09, the phone with the app WARM: three taps, three 201s from the push service, and then nothing — no
    [reveal] line, no worker message, tap-resume found:false on every resume, each tap booting a fresh page on the
    start URL. The worker's FINGERPRINT (which build wrote the store, how long since its last push and click) is
    folded into every tap-resume row, with sw-stale on a mismatch and a registration.update() at boot and on every
    visible. (The OFFER chip that came with it — a "from the notification" chip for a shown-but-untapped notification
    — is gone: the user 2026-09-09, shown one naming the wrong session, wants no chip and no prompt, ever. Its place
    is taken by the kernel's ledger and the vanished notification — LandingRevealAsksTheLedger.)"""
    FP = {"version": "__ROMP_SWV__", "installedAt": 1, "activatedAt": 1, "lastPushSid": "S9", "lastClickAt": 0, "lastClickSid": "", "clicks": 0}

    @classmethod
    def setUpClass(cls):
        import time as _t
        now = lambda: int(_t.time() * 1000)   # read per spawn: node runs in a row drift a 5 s age to 6 under load
        cls.out = _run_reveal(_FINGERPRINT_DRIVER, href="http://localhost:7777/", seed={"/__romp/sw": dict(cls.FP, lastPushAt=now() - 5000)})
        cls.no_reg = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", no_reg=True)

    @staticmethod
    def _rows(snap, what):
        return [d for w, d in snap["diag"] if w == what]

    def test_the_fingerprint_rides_every_tap_resume_row(self):
        b = self.out["boot"]
        self.assertEqual(b["fetches"], [], "nothing stored, nothing pending: nothing lands")
        # this page's own build wrote the store, its last push seconds ago, never a click — the reading that separates
        # a worker that never ran from one that ran and lost the tap
        self.assertEqual(self._rows(b, "tap-resume"),
                         [_fp({"found": False, "via": "boot", "store": True}, swVersion="__ROMP_SWV__", swMatchesPage=True, lastPushAgeS=5, lastClickAgeS=-1, clicks=0)])
        self.assertEqual(self._rows(b, "sw-stale"), [], "the same build: nothing stale")
        for d in self._rows(b, "tap-resume"):
            for k in d:
                self.assertNotIn("sid", k.lower(), "ages and booleans only, never a session id: %r" % d)

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

    def test_the_shell_offers_nothing_from_a_notification_any_more(self):
        # the user 2026-09-09: a "from the notification" chip named the wrong session (the newest ledger row was another
        # session's push), and the answer is no chip and no prompt, ever — a tap lands, or nothing shows. Source pins
        # on the served shell and both scripts, so nothing of the offer can come back unnoticed
        import re
        code = lambda src: "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))   # the prose may name what went; the code may not
        html = km._landing()
        for word in ("tap-offer", "from the notification", "/push/dismissed"):
            self.assertNotIn(word, html, word)
        for word in ("/__romp/shown", "SHOWN", "putJson", "forgetShown"):
            self.assertNotIn(word, code(km._SW_JS), word)
        js = code(km._LANDING_REVEAL_JS)
        for word in ("tap-offer", "offerHide", "'offer'", "/push/dismissed", "/__romp/shown", "SHOWN", "retireShown", "dropShown"):
            self.assertNotIn(word, js, word)
        self.assertEqual(set(re.findall(r"getElementById\('([^']+)'\)", js)), {"f-feed", "f-chat"}, "the two pane iframes are all the script looks up: no element of its own")
        self.assertNotIn("offer", km._REVEAL_ROADS)


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
                                     tap={"id": "T-21", "pid": "PID-link-other-0001", "sid": "S3", "host": "", "kind": "turn", "cardId": "", "url": "/?push-reveal=S3", "t": 1})
        cls.link_same = _run_reveal(_RESUME_LINK_DRIVER, href="http://localhost:7777/?push-reveal=S1&push-card=S1%3Ag1",
                                    tap={"id": "T-22", "pid": "PID-link-same-00001", "sid": "S1", "host": "", "kind": "card", "cardId": "S1:g1", "url": "/?push-reveal=S1&push-card=S1%3Ag1", "t": 1})
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
        self.assertEqual(o["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}], ["/push/dropped", {"pid": "PID-link-other-0001"}]],
                         "the link alone lands; the tap's row at the kernel is spent (another session), never landed")
        rows = self._rows(o)
        self.assertEqual(len(rows), 1)
        self.assertEqual({k: rows[0][k] for k in ("found", "via", "dup", "dropped", "sameSid")}, {"found": True, "via": "boot", "dup": False, "dropped": True, "sameSid": False})
        self.assertEqual(self._acks(o), [{"romp": "tapLanded", "id": "T-21"}], "retired all the same")
        self.assertEqual(o["keys"], [])
        s = self.link_same["boot"]
        self.assertEqual(s["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}], ["/push/landed", {"pid": "PID-link-same-00001"}]],
                         "the same session: landed by the link, and the row says landed")
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


# THE KERNEL'S LEDGER, from the page's side (2026-09-09): a page with a push subscription asks GET /push/pending on every
# check the store comes up empty for, reads the screen (getNotifications), and acts on the table — a clicked push lands
# (via 'ack'); exactly one shown push gone from the screen lands (via 'vanish'); everything else is silent, and every
# row the page is done with goes back as /push/landed | /push/superseded | /push/dropped
_LEDGER_DRIVER = _RESUME_LIB + r"""
const R = (pid, sid, stage, ageS, extra) => Object.assign({ pid, sid, host: '', kind: 'turn', cardId: '', name: 'web', stage, ageS }, extra || {});
(async () => {
  const out = {};
  await settle();
  out.boot = snap();                                   // PENDING is {rows: []} at boot (env): asked, nothing pending
  winMsg({ romp: 'ready', app: 'feed' });
  winMsg({ romp: 'wsState', app: 'chat', state: 'up' });
  reset();
  // CLICKED: the worker acked a tap this page never saw by any other road — a jump, and the row is landed
  PENDING = { rows: [R('PID-clicked-000001', 'S40', 'clicked', 4, { kind: 'card', cardId: 'S40:g1', name: 'api' })] };
  await flip('visible');
  out.clicked = snap();
  reset();
  await flip('visible');                               // the kernel still says clicked (the landed POST in flight): the same pid is a dup
  out.clickedAgain = snap();
  reset();
  // ONE VANISHED: two shown rows, one still on the screen — the other is gone with no close on record: tapped (a live
  // iOS app gets no click). It lands, silently
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
  // CLOSED: the worker saw the swipe — left alone, unsettled; the other shown row, gone, is the one tap
  PENDING = { rows: [R('PID-closed-00000001', 'S48', 'closed', 3), R('PID-shown-00000003', 'S49', 'shown', 8, { name: 'api' })] };
  await flip('visible');
  out.closed = snap();
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
  // a stored tap WINS the check that finds it (the kernel is not asked); its pid settles the row; the kernel's clicked row
  // for the same push on the next check is a dup
  PENDING = { rows: [R('PID-store-000000001', 'S53', 'clicked', 1)] };
  seed({ id: 'T-53', pid: 'PID-store-000000001', sid: 'S53', host: '', kind: 'turn', cardId: '', url: '/?push-reveal=S53', t: Date.now() });
  await flip('visible');
  out.storeWins = snap();
  reset();
  await flip('visible');
  out.storeThenLedger = snap();
  reset();
  // the worker's message carrying a pid settles the row too, and the ledger check never lands that push again
  PENDING = { rows: [R('PID-msg-0000000001', 'S54', 'clicked', 1)] };
  swMsg({ romp: 'notificationClick', sid: 'S54', host: '', kind: 'turn', cardId: '', id: 'T-54', pid: 'PID-msg-0000000001', diag: { clients: 0, tops: 0, road: 'open', vis: '' } });
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
  // the kernel unreachable: said so, nothing lands, no throw
  fetchFail = true;
  PENDING = { rows: [R('PID-err-0000000001', 'S57', 'clicked', 1)] };
  await flip('visible');
  out.err = snap();
  fetchFail = false;
  reset();
  // an answer without rows (a kernel of the build before the list, or a malformed one): nothing to act on, never a throw
  PENDING = { pid: 'PID-old-kernel-0001', sid: 'S58', host: '', kind: 'turn', cardId: '', name: 'web', stage: 'clicked', ageS: 1 };
  await flip('visible');
  out.noRows = snap();
  console.log(JSON.stringify(out));
})();
"""
# a page whose chat pane shows tabs but whose wsState message this script never saw (2026-09-09, the served offer leg):
# the tabs prove the socket was up, so a landing is delivered live, not parked
_TABS_DRIVER = _RESUME_LIB + r"""
(async () => {
  const out = {};
  await settle();
  reset();
  swMsg({ romp: 'notificationClick', sid: 'S50', host: '', kind: 'turn', cardId: '', id: 'T-50' });   // no tabs yet, no wsState: booting
  await settle();
  out.noTabs = snap();
  reset();
  activeSid = 'S1';                                    // the pane rendered its tabs — over the socket this script heard nothing about
  swMsg({ romp: 'notificationClick', sid: 'S51', host: '', kind: 'turn', cardId: '', id: 'T-51' });
  await settle();
  out.tabs = snap();
  console.log(JSON.stringify(out));
})();
"""


class LandingRevealAsksTheLedger(unittest.TestCase):
    """2026-09-09. The live trail, read right at last: the worker's acks DO reach the kernel; what a live Home Screen
    app on iOS never gets is the notificationclick — a tap on its notification only foregrounds the app (no ack, no
    message, no link, only the page's own visible/focus events; a killed app gets the click and the link). So the page
    asks GET /push/pending?endpoint=<its own subscription> for EVERY unsettled push to this device, reads the screen
    (registration.getNotifications) and decides, per row: clicked → lands via 'ack'; shown and gone, no close → tapped,
    and EXACTLY ONE such row lands via 'vanish', silently; anything else shows nothing — two or more gone, everything
    displayed, sent-only, a screen it cannot read. No chip, no prompt (the user 2026-09-09). Rows go back as
    /push/landed, /push/superseded (a newer notification for the same session displayed in its place) or
    /push/dropped (spent without a landing)."""
    EP = "https://push.example.net/send/this-device"
    LONG = "66666666-1111-2222-3333-444444444444"   # a uuid-shaped sid, so the clipped form differs from the whole

    @classmethod
    def setUpClass(cls):
        cls.out = _run_reveal(_LEDGER_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending={"rows": []})
        clicked = {"rows": [{"pid": "PID-boot-000000001", "sid": cls.LONG, "host": "", "kind": "card", "cardId": "S60:g1", "name": "api", "stage": "clicked", "ageS": 9}]}
        cls.boot_clicked = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending=clicked)
        cls.link_clicked = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/?push-reveal=" + cls.LONG + "&push-card=S60%3Ag1", endpoint=cls.EP, pending=clicked)
        cls.link_other = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/?push-reveal=S1", endpoint=cls.EP, pending=clicked)
        cls.no_caches = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending=clicked, no_caches=True)
        shown2 = {"rows": [{"pid": "PID-bootv-000000002", "sid": "S62", "host": "", "kind": "turn", "cardId": "", "name": "tests", "stage": "shown", "ageS": 3},
                           {"pid": "PID-bootv-000000001", "sid": cls.LONG, "host": "", "kind": "turn", "cardId": "", "name": "api", "stage": "shown", "ageS": 12}]}
        cls.boot_vanished = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending=shown2, displayed=["PID-bootv-000000002"])
        cls.link_vanished = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/?push-reveal=S1", endpoint=cls.EP, pending=shown2, displayed=["PID-bootv-000000002"])
        cls.no_getn = _run_reveal(_BOOT_DRIVER, href="http://localhost:7777/", endpoint=cls.EP, pending=shown2, no_getn=True)
        cls.tabs = _run_reveal(_TABS_DRIVER, href="http://localhost:7777/")

    @staticmethod
    def _rows(snap, what):
        return [d for w, d in snap["diag"] if w == what]

    PENDING_GET = "/push/pending?endpoint=" + "https%3A%2F%2Fpush.example.net%2Fsend%2Fthis-device"

    def test_the_page_asks_for_its_own_endpoint_on_every_check_and_says_what_it_heard(self):
        b = self.out["boot"]
        self.assertEqual(b["gets"], [self.PENDING_GET], "asked at boot, for THIS page's subscription")
        self.assertEqual(self._rows(b, "tap-pending"), [{"via": "boot", "sub": True, "rows": 0}], "nothing pending: said so, and the screen is not read")
        self.assertEqual((b["fetches"], b["getn"]), ([], 0))
        c = self.out["clicked"]
        self.assertEqual(c["gets"], [self.PENDING_GET], "…and on every coming-back")
        rows = self._rows(self.boot_clicked["boot"], "tap-pending-land")
        self.assertEqual([r["sid"] for r in rows], [self.LONG[:8]], "the session id is clipped to 8, never whole: %r" % rows)
        self.assertNotIn(self.LONG, json.dumps(self.boot_clicked["boot"]["diag"]))
        self.assertNotIn(self.LONG, json.dumps(self.boot_vanished["boot"]["diag"]))

    def test_a_clicked_push_lands_by_the_ack_road_once_and_settles_the_row(self):
        c = self.out["clicked"]
        self.assertEqual(c["fetches"], [["/reveal", {"sid": "S40", "wid": "W-test", "via": "ack"}], ["/push/landed", {"pid": "PID-clicked-000001"}]],
                         "the user tapped: a jump by the same land() path, the road named; then the kernel's row is landed")
        self.assertEqual(c["posted"], [{"romp": "revealCard", "itemId": "S40:g1", "sid": "S40"}], "a card kind scrolls the feed too")
        self.assertEqual(self._rows(c, "tap-pending"), [{"via": "visible", "sub": True, "rows": 1, "getNotifications": True, "displayed": 0, "vanished": 0}])
        self.assertEqual(self._rows(c, "tap-pending-land"), [{"sid": "S40", "ageS": 4, "dup": False, "dropped": False, "sameSid": None}])
        self.assertIn(["reveal-post", {"status": 200, "via": "ack", "boot": False}], c["diag"])
        a = self.out["clickedAgain"]
        self.assertEqual(a["fetches"], [], "the same pid again is a dup: no second /reveal, no second settle")
        self.assertEqual(self._rows(a, "tap-pending-land"), [{"sid": "S40", "ageS": 4, "dup": True, "dropped": False, "sameSid": None}])

    def test_exactly_one_vanished_notification_lands_silently_and_settles_its_row(self):
        # the tap a live iOS app exposes: the notification the worker showed is no longer on the screen, no close on record
        v = self.out["oneVanished"]
        self.assertEqual(v["getn"], 1, "the screen is read once per check")
        self.assertEqual(v["fetches"], [["/reveal", {"sid": "S41", "wid": "W-test", "via": "vanish"}], ["/push/landed", {"pid": "PID-shown-00000001"}]],
                         "the one gone lands by the same land() path, the road named; the displayed one is untouched")
        self.assertEqual(v["posted"], [{"romp": "revealCard", "itemId": "S41:g3", "sid": "S41"}], "a card kind scrolls the feed too")
        self.assertEqual(self._rows(v, "tap-pending"), [{"via": "visible", "sub": True, "rows": 2, "getNotifications": True, "displayed": 1, "vanished": 1}])
        self.assertEqual(self._rows(v, "tap-vanish-land"), [{"sid8": "S41", "ageS": 45}])
        self.assertIn(["reveal-post", {"status": 200, "via": "vanish", "boot": False}], v["diag"])
        self.assertEqual(v["notes"], [], "nothing shown to the user but the landing itself")
        a = self.out["oneVanishedAgain"]
        self.assertEqual(a["fetches"], [], "the same pid again is seen: no second landing")
        self.assertEqual(self._rows(a, "tap-pending"), [{"via": "visible", "sub": True, "rows": 2, "getNotifications": True, "displayed": 1, "vanished": 0}])
        self.assertEqual(self._rows(a, "tap-vanish-land"), [])

    def test_two_vanished_all_displayed_or_sent_only_show_nothing(self):
        # the user 2026-09-09: no chip, no prompt — when the page cannot say which one was tapped, or nothing was, nothing happens
        t = self.out["twoVanished"]
        self.assertEqual([f for f in t["fetches"] if f[0] == "/reveal"], [], "two gone at once: the tap could have been on either — nothing lands")
        self.assertEqual(t["fetches"], [["/push/dropped", {"pid": "PID-gone-000000002"}], ["/push/dropped", {"pid": "PID-gone-000000001"}]],
                         "…and both rows are spent, so they can never inflate the next check's count")
        self.assertEqual(self._rows(t, "tap-pending"), [{"via": "pageshow", "sub": True, "rows": 2, "getNotifications": True, "displayed": 0, "vanished": 2}])
        self.assertEqual(self._rows(t, "tap-vanish-land"), [])
        self.assertEqual((t["posted"], t["notes"]), ([], []))
        d = self.out["allDisplayed"]
        self.assertEqual(d["fetches"], [], "everything still on the screen: untouched, unsettled")
        self.assertEqual(self._rows(d, "tap-pending"), [{"via": "focus", "sub": True, "rows": 2, "getNotifications": True, "displayed": 2, "vanished": 0}], "a notification without a pid is not counted")
        n = self.out["sentOnly"]
        self.assertEqual(n["fetches"], [], "never acked shown: nothing is known to have been displayed, so nothing vanished")
        self.assertEqual(self._rows(n, "tap-pending"), [{"via": "visible", "sub": True, "rows": 1, "getNotifications": True, "displayed": 0, "vanished": 0}])

    def test_a_closed_row_is_left_alone_and_a_superseded_row_is_settled_as_such(self):
        c = self.out["closed"]
        self.assertEqual(c["fetches"], [["/reveal", {"sid": "S49", "wid": "W-test", "via": "vanish"}], ["/push/landed", {"pid": "PID-shown-00000003"}]],
                         "the swiped-away one is neither a tap nor settled here; the other gone row is the one tap")
        self.assertEqual(self._rows(c, "tap-pending"), [{"via": "visible", "sub": True, "rows": 2, "getNotifications": True, "displayed": 0, "vanished": 1}])
        s = self.out["superseded"]
        self.assertEqual(s["fetches"], [["/push/superseded", {"pid": "PID-older-00000001"}],
                                        ["/reveal", {"sid": "S51", "wid": "W-test", "via": "vanish"}], ["/push/landed", {"pid": "PID-other-00000001"}]],
                         "the older row for the session whose newer notification is displayed was replaced, not tapped; the other session's gone row is the one tap")
        self.assertEqual(self._rows(s, "tap-pending"), [{"via": "visible", "sub": True, "rows": 3, "getNotifications": True, "displayed": 1, "vanished": 1, "superseded": 1}])
        self.assertEqual(self._rows(s, "tap-vanish-land"), [{"sid8": "S51", "ageS": 61}])

    def test_a_screen_the_page_cannot_read_lands_nothing_and_says_so(self):
        g = self.out["getnThrows"]
        self.assertEqual(g["fetches"], [], "getNotifications threw: a notification gone cannot be told from one never shown")
        self.assertEqual(self._rows(g, "tap-pending"), [{"via": "visible", "sub": True, "rows": 1, "getNotifications": False, "displayed": -1, "vanished": 0}])
        m = self.no_getn["boot"]
        self.assertEqual(m["fetches"], [], "no getNotifications on this browser: the same")
        self.assertEqual(self._rows(m, "tap-pending"), [{"via": "boot", "sub": True, "rows": 2, "getNotifications": False, "displayed": -1, "vanished": 0}])
        self.assertEqual(m["getn"], 0)

    def test_the_other_roads_settle_the_row_by_pid_so_the_ledger_never_lands_a_push_twice(self):
        w = self.out["storeWins"]
        self.assertEqual(w["fetches"], [["/reveal", {"sid": "S53", "wid": "W-test", "via": "store"}], ["/push/landed", {"pid": "PID-store-000000001"}]], "the stored tap lands, and its pid settles the kernel's row")
        self.assertEqual(w["gets"], [], "a check that found a tap does not ask the kernel")
        self.assertEqual(self._rows(w, "tap-pending"), [])
        a = self.out["storeThenLedger"]
        self.assertEqual(a["fetches"], [], "the kernel's clicked row for that push is a dup by pid")
        self.assertEqual(self._rows(a, "tap-pending-land"), [{"sid": "S53", "ageS": 1, "dup": True, "dropped": False, "sameSid": None}])
        m = self.out["msg"]
        self.assertEqual(m["fetches"], [["/reveal", {"sid": "S54", "wid": "W-test", "via": "sw"}], ["/push/landed", {"pid": "PID-msg-0000000001"}]], "the worker's message carries the pid: landed by the message, settled")
        self.assertEqual(m["ack"], [{"romp": "tapLanded", "id": "T-54"}])
        self.assertEqual(self.out["msgThenLedger"]["fetches"], [])
        self.assertEqual(self._rows(self.out["msgThenLedger"], "tap-pending-land"), [{"sid": "S54", "ageS": 1, "dup": True, "dropped": False, "sameSid": None}])
        b = self.out["clickedBesideVanished"]
        self.assertEqual(b["fetches"], [["/reveal", {"sid": "S55", "wid": "W-test", "via": "ack"}], ["/push/landed", {"pid": "PID-both-clicked-01"}], ["/push/dropped", {"pid": "PID-both-vanish-001"}]],
                         "the tap the worker saw lands; what else vanished is spent, never a second landing")
        self.assertEqual(self._rows(b, "tap-vanish-land"), [])
        e = self.out["err"]
        self.assertEqual(e["fetches"], [], "the kernel unreachable: nothing lands, no throw")
        self.assertEqual(self._rows(e, "tap-pending"), [{"via": "visible", "sub": True, "rows": 0, "err": True}])
        z = self.out["noRows"]
        self.assertEqual((z["fetches"], self._rows(z, "tap-pending")), ([], [{"via": "visible", "sub": True, "rows": 0}]), "an answer without rows is nothing to act on")

    def test_a_boot_lands_a_clicked_or_vanished_push_as_a_boot_and_a_deep_link_boot_outranks_both(self):
        b = self.boot_clicked["boot"]
        self.assertEqual(b["fetches"], [["/reveal", {"sid": self.LONG, "wid": "W-test", "via": "ack", "boot": True}], ["/push/landed", {"pid": "PID-boot-000000001"}]],
                         "a relaunch on the start URL: the kernel parks for this page's pane, as for a stored tap")
        self.assertEqual(self._rows(b, "tap-pending"), [{"via": "boot", "sub": True, "rows": 1, "getNotifications": True, "displayed": 0, "vanished": 0}])
        l = self.link_clicked["boot"]
        self.assertEqual(l["fetches"], [["/reveal", {"sid": self.LONG, "wid": "W-test", "via": "link", "boot": True}], ["/push/landed", {"pid": "PID-boot-000000001"}]],
                         "the link is the newer word: landed by the link alone — the same session, so the row is landed")
        self.assertEqual(self._rows(l, "tap-pending-land"), [{"sid": self.LONG[:8], "ageS": 9, "dup": False, "dropped": True, "sameSid": True}])
        o = self.link_other["boot"]
        self.assertEqual(o["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}], ["/push/dropped", {"pid": "PID-boot-000000001"}]],
                         "a link to ANOTHER session outranks the tap: the row is spent, never landed")
        self.assertEqual(self._rows(o, "tap-pending-land"), [{"sid": self.LONG[:8], "ageS": 9, "dup": False, "dropped": True, "sameSid": False}])
        n = self.no_caches["boot"]
        self.assertEqual(n["fetches"], [["/reveal", {"sid": self.LONG, "wid": "W-test", "via": "ack", "boot": True}], ["/push/landed", {"pid": "PID-boot-000000001"}]],
                         "no Cache API at all: the kernel's ledger is the road left, and it lands")
        self.assertEqual(self._rows(n, "tap-resume"), [_fp({"found": False, "via": "boot", "store": False})])
        v = self.boot_vanished["boot"]
        self.assertEqual(v["fetches"], [["/reveal", {"sid": self.LONG, "wid": "W-test", "via": "vanish", "boot": True}], ["/push/landed", {"pid": "PID-bootv-000000001"}]],
                         "the one vanished lands as a boot too")
        self.assertEqual(self._rows(v, "tap-vanish-land"), [{"sid8": self.LONG[:8], "ageS": 12}])
        lv = self.link_vanished["boot"]
        self.assertEqual(lv["fetches"], [["/reveal", {"sid": "S1", "wid": "W-test", "via": "link", "boot": True}], ["/push/dropped", {"pid": "PID-bootv-000000001"}]],
                         "the link is the newer word: what vanished is spent, never a second landing")
        self.assertEqual(self._rows(lv, "tap-vanish-land"), [])
        self.assertEqual(self._rows(lv, "tap-pending"), [{"via": "boot", "sub": True, "rows": 2, "getNotifications": True, "displayed": 1, "vanished": 1}])

    def test_the_panes_rendered_tabs_prove_its_socket_up_when_its_message_was_missed(self):
        # 2026-09-09, the served leg of the browser test: the shell's parser yielded to the chat pane's wsState message before
        # this script existed, so every landing said booting and parked for a ready that had already come. The tabs come over
        # that very socket: an active tab in the pane's DOM is proof enough, and a landing is delivered live
        self.assertEqual(self.tabs["noTabs"]["fetches"], [["/reveal", {"sid": "S50", "wid": "W-test", "via": "sw", "boot": True}]], "no tabs, no message: booting")
        self.assertEqual(self.tabs["tabs"]["fetches"], [["/reveal", {"sid": "S51", "wid": "W-test", "via": "sw"}]], "tabs rendered: live, whatever this script heard")


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
