#!/usr/bin/env python3
"""A notification tap on a phone app alive in the BACKGROUND, executed in a real browser (2026-09-09).

The device round of 2026-09-09: with the Home Screen app force-quit, a tap opened the right session;
with the app alive in the background, the tap brought it forward and changed nothing. The worker's trail
said why — clients:0, tops:0, road 'open' — iOS lists no window client for a backgrounded Home Screen
app, so the worker took openWindow; iOS foregrounded the EXISTING page without a load (no deep link) and
without a client to message (no postMessage), then ended the worker and its in-memory kept tap (no
replay). The Node harness in tests/test_kernel_webpush.py pins the pieces; this is the executed guard
that the REAL shell, the REAL worker and the REAL kernel land the tap end to end, so the user is not the
test device (the user 2026-09-09, tired of being exactly that).

The reproduction: a hermetic kernel serves the shell and /sw.js; two synthetic sessions (`web`, `api`);
the page loads, the worker is registered the way the bell does and takes control; `web` is put in front.
Then, INSIDE the worker (Chromium exposes it), iOS's observed behaviour is installed — clients.matchAll
resolves [], clients.openWindow resolves null without navigating — a notification carrying the routing
block for `api` is dispatched at the worker's notificationclick handler, and the kept tap is cleared as
the ended worker would have lost it. (Headless Chromium reports Notification.permission 'denied'
whatever the context grants, so showNotification + a real NotificationEvent is refused there; the
driver then dispatches a plain event carrying the two fields the handler reads, .notification and
.waitUntil, at the same real handler, and records which road ran.) The app 'comes forward': the page
gets the window focus and pageshow events a resumed page produces (Chromium will not flip
document.hidden from a script). The tap must land: ONE /reveal via 'store', the chat pane's active
tab becomes `api`, the store entry is retired, the kernel log carries the [reveal] store line, and the
shell's tap-resume row is on file. Against the pre-fix sources (1e0fdc7b) this scenario fails on the
outcome the user saw — the tab still `web`, no /reveal — with the worker having taken the openWindow
road and kept the tap only in memory.

THE SECOND ROUND (2026-09-09, later — the app WARM this time): three taps, three 201s from the push service, and then
nothing. No [reveal] line, no sw-message row, tap-resume found:false on every resume, and each tap booting a fresh
page on the start URL with no link. The answer of that hour — a '/__romp/shown' record the push wrote, and a bottom-left
chip offering the session it named ("Open <name> · from the notification") — shipped and was removed the same day: on
the phone the chip named the WRONG session (the newest ledger row was another session's push, sent 40 s later), and the
user wants no chip and no prompt, ever. Its scenario is gone with it; the served shell carries no such element, and
the fourth scenario below pins that.

THE THIRD SCENARIO (2026-09-09, later still — the kernel as the meeting point, the ACK road): every session-addressed
push carries a `pid` the kernel issued for that device and files a ledger row; the worker acks 'shown' (before the
show), 'clicked' (the first thing the click handler does) and 'closed' by pid alone; the page asks GET /push/pending
for its own subscription when it comes forward and lands a clicked push via 'ack'. Here every OTHER road is cut, so the
ack road stands alone: INSIDE the worker its Cache Storage is replaced by one the page cannot see, matchAll lists no
client, openWindow hands back null; the REAL push handler and the REAL click handler run on the kernel's payload; the
page comes forward. The tap must land: ONE /reveal via 'ack', the tab on `api`, the row landed, the kernel log carrying
[push] ack stage=shown, [push] ack stage=clicked, [reveal] ack and [push] landed. (The round that built this read the
phone as a storage PARTITION between the fielding worker and the page; the next round corrected that — see the fourth
scenario. The ack road is right regardless: it is how a tap the worker DID see reaches a page none of the other roads
reached.) Against d5653088 this fails on the outcome: the worker has no ack, the kernel no ledger, the tab stays `web`.

THE FOURTH SCENARIO (2026-09-09, 23:30 UTC — the VANISHED notification, the finding read right): the worker's acks DO
reach the kernel ([push] test … 201, then [push] ack stage=shown a second later). What iOS withholds from a Home Screen
app that is already alive is the TAP: a tap on its notification foregrounds the app and dispatches NO notificationclick
to the worker — no ack, no message, no deep link, only the page's own visible/focus events (a killed app gets the click
and the link). The one thing the page can read is the screen: registration.getNotifications() lists what is still
displayed, and an unsettled push the worker acked shown whose notification is GONE, with no close on record, was tapped.
So the page asks for EVERY unsettled row to its device and holds the shown ones against the screen: exactly ONE vanished
lands, silently (via 'vanish'); anything else — two or more gone, everything displayed, sent-only, a screen it cannot
read — shows nothing. Here: the hermetic kernel subscribes a device and sends it three test pushes (`api`, `tests`,
`docs`; the push service refuses — the rows are filed before the send), each acked shown by pid; the page stubs
ServiceWorkerRegistration.prototype.getNotifications by data.pid and comes forward three times. Two of three displayed
→ exactly the missing one lands: ONE /reveal via 'vanish', the tab on `api`, /push/landed for that pid, [reveal] vanish
in the kernel log, and NO chip element anywhere. All three displayed → nothing. One displayed → two vanished → nothing
lands, nothing shows, and both rows are dropped (/push/dropped) so they never inflate a later count. Against 3b8b60f8
this fails on the outcome: /push/pending named the newest row alone, the chip appeared for it, and nothing landed.

Skips LOUDLY without the extension deps or a playwright browser (CI installs none). All fixtures
synthetic. Under ~45 s, no network.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"   # web: the session in front when the phone buzzes
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"   # api: the session that buzzed — where the tap must land
SID_C = "cccccccc-1111-2222-3333-444444444444"   # tests: buzzed too (the fourth scenario), its notification still on the screen
SID_D = "dddddddd-1111-2222-3333-444444444444"   # docs: likewise


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { reveals: [], sockets: [] };
const context = await browser.newContext({ viewport: { width: 1100, height: 720 } });
await context.grantPermissions(["notifications"], { origin: cfg.origin });
const page = await context.newPage();
page.on("request", (r) => { if (r.method() === "POST" && /\/reveal$/.test(r.url())) out.reveals.push(JSON.parse(r.postData() || "{}")); });
page.on("websocket", (ws) => { out.sockets.push(ws.url().replace(/^ws:\/\/[^/]+/, "")); ws.on("close", () => out.sockets.push("closed " + ws.url().replace(/^ws:\/\/[^/]+/, ""))); });   // each side's wid rides its connect URL
await page.goto(cfg.landing);
const chat = await (async () => { for (let i = 0; i < 200; i++) { const f = page.frames().find((f) => /\/chat/.test(f.url())); if (f) return f; await page.waitForTimeout(50); } return null; })();
if (!chat) { console.error("no chat iframe in the shell"); process.exit(1); }
await chat.waitForSelector('#tabs .tab[data-id="' + cfg.sidB + '"]', { timeout: 20000 });
// the session in front is `web`, so the tap has something to CHANGE
await chat.evaluate((sid) => { const t = document.querySelector('#tabs .tab[data-id="' + sid + '"]'); if (t) t.click(); }, cfg.sidA);
await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, cfg.sidA, { timeout: 10000 });
out.before = await chat.evaluate(() => (document.querySelector("#tabs .tab.active") || { dataset: {} }).dataset.id || null);
// the worker: registered the way the bell's opt-in does, then in control of this page (clients.claim on activate)
const swWait = context.waitForEvent("serviceworker", { timeout: 20000 }).catch(() => null);
await page.evaluate(() => navigator.serviceWorker.register("/sw.js"));
const sw = context.serviceWorkers()[0] || await swWait;
if (!sw) { console.error("no service worker registered"); process.exit(1); }
await page.evaluate(() => navigator.serviceWorker.ready.then(() => navigator.serviceWorker.controller ? null
  : new Promise((r) => navigator.serviceWorker.addEventListener("controllerchange", () => r(), { once: true }))));
out.controlled = await page.evaluate(() => !!navigator.serviceWorker.controller);
// the tap, as iOS ran it (the 2026-09-09 trail): no client listed for the backgrounded app; an openWindow that brings
// the existing page forward WITHOUT a load and hands nothing back; the worker ended right after the click
out.worker = await sw.evaluate(async (data) => {
  self.clients.matchAll = () => Promise.resolve([]);
  self.clients.openWindow = (u) => { self.__opened = u; return Promise.resolve(null); };
  const waited = [];
  ExtendableEvent.prototype.waitUntil = function (p) { waited.push(p); };   // a script-made event cannot extend the worker: capture the promise and await it here
  let road = "notification", ev;
  try {
    await self.registration.showNotification("romp: api", { body: "Needs you: which migration first?", data, tag: "romp:" + data.sid });
    const n = (await self.registration.getNotifications())[0];
    if (!n) throw new Error("no notification back");
    ev = new NotificationEvent("notificationclick", { notification: n });
  } catch (e) {
    road = "plain-event: " + ((e && e.message) || e);   // a browser that refuses the real one: the handler reads only .notification and .waitUntil
    ev = new Event("notificationclick"); ev.notification = { close() {}, data }; ev.waitUntil = (p) => waited.push(p);
  }
  self.dispatchEvent(ev);
  await Promise.all(waited);
  const kept = typeof self.pending === "undefined" ? "no-var" : (self.pending ? "kept" : "none");
  self.pending = null;   // the worker iOS ended keeps nothing for a replay
  const stored = await caches.open("romp-tap").then((c) => c.match("/__romp/tap")).then((r) => (r ? r.json() : null)).catch((e) => "err: " + e);
  return { road, waited: waited.length, opened: self.__opened || null, kept, stored };
}, cfg.data);
out.reveals_before_foreground = out.reveals.length;
// the app comes forward: no load, no message — only the events a resumed page produces
await page.evaluate(() => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })); });
out.landed = await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, cfg.sidB, { timeout: 8000 }).then(() => true).catch(() => false);
out.after = await chat.evaluate(() => (document.querySelector("#tabs .tab.active") || { dataset: {} }).dataset.id || null);
// the entry is retired once landed (the shell deletes it; the worker deletes it on the ack) — wait for that exact state, bounded
out.storeAfter = await (async () => { let s = null; for (let i = 0; i < 40; i++) {
  s = await sw.evaluate(() => caches.open("romp-tap").then((c) => c.match("/__romp/tap")).then((r) => (r ? r.json() : null)));
  if (s === null) break; await page.waitForTimeout(50); } return s; })();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""

# the ack road alone (2026-09-09, the third scenario): the REAL push and click handlers run in a worker whose Cache Storage is
# not the page's and which lists no client of the app; the page comes forward and lands the tap through the kernel alone
DRIVER_ACK_ROAD = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { reveals: [], ledger: [], pending: [] };
const context = await browser.newContext({ viewport: { width: 1100, height: 720 } });
await context.grantPermissions(["notifications"], { origin: cfg.origin });
// this device's push subscription, as the page reads it (pushManager.getSubscription on its registration): headless
// Chromium has no push service to subscribe with, so the registration answers with the endpoint the kernel has on file
await context.addInitScript((endpoint) => {
  Object.defineProperty(ServiceWorkerRegistration.prototype, "pushManager", { configurable: true,
    get() { return { getSubscription: () => Promise.resolve({ endpoint }) }; } });
}, cfg.endpoint);
const page = await context.newPage();
page.on("request", (r) => { const u = r.url();
  if (r.method() === "POST" && /\/reveal$/.test(u)) out.reveals.push(JSON.parse(r.postData() || "{}"));
  if (r.method() === "POST" && /\/push\/(landed|superseded|dropped)$/.test(u)) out.ledger.push([u.replace(/^.*\/push\//, ""), JSON.parse(r.postData() || "{}")]);
  if (/\/push\/pending\?/.test(u)) out.pending.push(decodeURIComponent(u.replace(/^.*endpoint=/, ""))); });
await page.goto(cfg.landing);
const chat = await (async () => { for (let i = 0; i < 200; i++) { const f = page.frames().find((f) => /\/chat/.test(f.url())); if (f) return f; await page.waitForTimeout(50); } return null; })();
if (!chat) { console.error("no chat iframe in the shell"); process.exit(1); }
await chat.waitForSelector('#tabs .tab[data-id="' + cfg.sidB + '"]', { timeout: 20000 });
await chat.evaluate((sid) => { const t = document.querySelector('#tabs .tab[data-id="' + sid + '"]'); if (t) t.click(); }, cfg.sidA);
await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, cfg.sidA, { timeout: 10000 });
const active = () => chat.evaluate(() => (document.querySelector("#tabs .tab.active") || { dataset: {} }).dataset.id || null);
out.before = await active();
const swWait = context.waitForEvent("serviceworker", { timeout: 20000 }).catch(() => null);
await page.evaluate(() => navigator.serviceWorker.register("/sw.js"));
const sw = context.serviceWorkers()[0] || await swWait;
if (!sw) { console.error("no service worker registered"); process.exit(1); }
await page.evaluate(() => navigator.serviceWorker.ready.then(() => navigator.serviceWorker.controller ? null
  : new Promise((r) => navigator.serviceWorker.addEventListener("controllerchange", () => r(), { once: true }))));
out.controlled = await page.evaluate(() => !!navigator.serviceWorker.controller);
out.reveals_before_push = out.reveals.length;
// EVERY OTHER ROAD CUT: the worker sees a Cache Storage of its own (nothing it writes reaches the page's store), lists no
// client of the app, and its openWindow brings the page forward without a load. Then the REAL push handler on the
// kernel's payload, and the REAL click handler on the notification it described: the kernel's row is the one hand-off
out.worker = await sw.evaluate(async (payload) => {
  const mem = new Map();
  const own = { put: (k, r) => { mem.set(String(k), r); return Promise.resolve(); },
                match: (k) => Promise.resolve(mem.has(String(k)) ? mem.get(String(k)).clone() : undefined),
                delete: (k) => Promise.resolve(mem.delete(String(k))) };
  self.caches.open = () => Promise.resolve(own);   // the worker holds the CacheStorage object; its open() is looked up per call
  self.clients.matchAll = () => Promise.resolve([]);
  self.clients.openWindow = (u) => { self.__opened = u; return Promise.resolve(null); };
  const waited = [];
  const push = new Event("push"); push.data = { json: () => payload }; push.waitUntil = (p) => waited.push(p);
  self.dispatchEvent(push);
  const pushOutcomes = (await Promise.allSettled(waited)).map((s) => s.status);   // the show rejects in headless Chromium; the rest settle
  waited.length = 0;
  const click = new Event("notificationclick"); click.notification = { close() {}, data: payload.data }; click.waitUntil = (p) => waited.push(p);
  self.dispatchEvent(click);
  await Promise.all(waited);
  self.pending = null;   // the worker iOS ended keeps nothing for a replay
  return { pushWaited: pushOutcomes.length, clickWaited: waited.length, opened: self.__opened || null, ownKeys: [...mem.keys()].sort() };
}, cfg.payload);
// the page's side: none of the worker's writes are visible here — the store road is cut
out.pageStore = await page.evaluate(() => caches.open("romp-tap").then((c) => c.match("/__romp/tap")).then((r) => !!r));
out.reveals_before_foreground = out.reveals.length;
// the app comes forward: the events a resumed page produces, and nothing else
await page.evaluate(() => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })); });
out.landed = await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, cfg.sidB, { timeout: 8000 }).then(() => true).catch(() => false);
out.after = await active();
for (let i = 0; i < 40 && !out.ledger.length; i++) await page.waitForTimeout(50);   // the settle rides after the /reveal; bounded
out.chipAbsent = await page.evaluate(() => document.getElementById("tap-offer") === null);
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


# the vanished notification (2026-09-09, the fourth scenario): three pushes the worker acked shown; the page reads the screen
# through a stubbed getNotifications and comes forward three times — two of three displayed, all three, one of three
DRIVER_VANISH = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { reveals: [], ledger: [], pending: 0, passes: [] };
const context = await browser.newContext({ viewport: { width: 1100, height: 720 } });
await context.grantPermissions(["notifications"], { origin: cfg.origin });
// this device's subscription (headless Chromium has no push service: the registration answers with the endpoint the kernel
// has on file), and THE SCREEN: getNotifications() lists a notification per pid in window.__displayed — every push's at
// first (nothing has vanished), then whatever each pass sets. The real method would list nothing here anyway: headless
// Chromium refuses showNotification, and no push is dispatched at the worker in this scenario
await context.addInitScript((c) => {
  Object.defineProperty(ServiceWorkerRegistration.prototype, "pushManager", { configurable: true,
    get() { return { getSubscription: () => Promise.resolve({ endpoint: c.endpoint }) }; } });
  window.__displayed = c.pids.slice();
  ServiceWorkerRegistration.prototype.getNotifications = function () { return Promise.resolve((window.__displayed || []).map((pid) => ({ data: { pid } }))); };
}, { endpoint: cfg.endpoint, pids: cfg.pids });
const page = await context.newPage();
page.on("request", (r) => { const u = r.url();
  if (r.method() === "POST" && /\/reveal$/.test(u)) out.reveals.push(JSON.parse(r.postData() || "{}"));
  if (r.method() === "POST" && /\/push\/(landed|superseded|dropped|dismissed)$/.test(u)) out.ledger.push([u.replace(/^.*\/push\//, ""), JSON.parse(r.postData() || "{}")]);
  if (/\/push\/pending\?/.test(u)) out.pending++; });
await page.goto(cfg.landing);
const chat = await (async () => { for (let i = 0; i < 200; i++) { const f = page.frames().find((f) => /\/chat/.test(f.url())); if (f) return f; await page.waitForTimeout(50); } return null; })();
if (!chat) { console.error("no chat iframe in the shell"); process.exit(1); }
await chat.waitForSelector('#tabs .tab[data-id="' + cfg.sidB + '"]', { timeout: 20000 });
await chat.evaluate((sid) => { const t = document.querySelector('#tabs .tab[data-id="' + sid + '"]'); if (t) t.click(); }, cfg.sidA);
await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, cfg.sidA, { timeout: 10000 });
const active = () => chat.evaluate(() => (document.querySelector("#tabs .tab.active") || { dataset: {} }).dataset.id || null);
out.before = await active();
const swWait = context.waitForEvent("serviceworker", { timeout: 20000 }).catch(() => null);
await page.evaluate(() => navigator.serviceWorker.register("/sw.js"));
const sw = context.serviceWorkers()[0] || await swWait;
if (!sw) { console.error("no service worker registered"); process.exit(1); }
await page.evaluate(() => navigator.serviceWorker.ready.then(() => navigator.serviceWorker.controller ? null
  : new Promise((r) => navigator.serviceWorker.addEventListener("controllerchange", () => r(), { once: true }))));
out.controlled = await page.evaluate(() => !!navigator.serviceWorker.controller);
// the shell's diag poster, wrapped: a pass waits for its two checks (focus + pageshow) to file their tap-pending rows — the
// decision is made by the time a row is filed — never for a timer
await page.evaluate(() => { window.__tp = []; const o = window.__rompShellDiag; window.__rompShellDiag = (w, d) => { if (w === "tap-pending") window.__tp.push(d); return o ? o(w, d) : undefined; }; });
const chipAbsent = () => page.evaluate(() => document.getElementById("tap-offer") === null);
out.chipAbsentBefore = await chipAbsent();
out.reveals_before = out.reveals.length;
async function pass(name, displayed, landsOn, settles) {
  const tp0 = await page.evaluate(() => window.__tp.length);
  const reveals0 = out.reveals.length, ledger0 = out.ledger.length;
  await page.evaluate((d) => { window.__displayed = d; }, displayed);
  // the app comes forward: the events a resumed page produces, and nothing else
  await page.evaluate(() => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })); });
  const decided = await page.waitForFunction((n) => window.__tp.length >= n + 2, tp0, { timeout: 8000 }).then(() => true).catch(() => false);
  const landed = landsOn ? await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, landsOn, { timeout: 8000 }).then(() => true).catch(() => false) : null;
  for (let i = 0; i < 40 && out.ledger.length < ledger0 + settles; i++) await page.waitForTimeout(50);   // the settles ride after the decision; bounded
  out.passes.push({ name, decided, landed, after: await active(), reveals: out.reveals.slice(reveals0), ledger: out.ledger.slice(ledger0),
                    rows: await page.evaluate((n) => window.__tp.slice(n), tp0), chipAbsent: await chipAbsent() });
}
await pass("twoOfThree", [cfg.pids[1], cfg.pids[2]], cfg.sidB, 1);   // api's notification is gone: the one tap — it lands
await pass("allThree", cfg.pids.slice(), null, 0);                    // everything on the screen: nothing
await pass("oneOfThree", [cfg.pids[0]], null, 2);                     // tests' and docs' gone at once: nothing lands, both rows dropped
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


def _transcript(sid, prompt, reply):
    return (json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-" + sid[:12], "parentUuid": None,
                        "timestamp": "2026-09-09T00:00:00.000Z", "sessionId": sid,
                        "message": {"role": "user", "content": prompt}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": "22222222-3333-4444-5555-" + sid[:12],
                        "parentUuid": "11111111-2222-3333-4444-" + sid[:12],
                        "timestamp": "2026-09-09T00:00:05.000Z", "sessionId": sid,
                        "message": {"role": "assistant", "model": "claude-fable-5-1",
                                    "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"}}) + "\n")


class ServedTapResume(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served tap needs them")
        cls.lab = tempfile.mkdtemp(prefix="tap-resume-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        shutil.copytree(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))   # jd._proj_dir's munge
        os.makedirs(proj, exist_ok=True)
        for sid, name, prompt, reply in (
                (SID_A, "web", "Where do we stand on the login flow?", "The login flow is done and every test passes."),
                (SID_B, "api", "Add the notes table migration.", "Two migrations could go first; which one do you want?"),
                (SID_C, "tests", "Cover the notes endpoint.", "Three cases are covered; the pagination one needs a fixture I cannot invent."),
                (SID_D, "docs", "Write the notes API page.", "The page is drafted; which auth flow should the examples assume?")):
            Path(cls.state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(cls.state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid,
                 "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, prompt, reply))   # a CLOSED turn: nothing to resume
        cls.port = _free_port()
        cls.token = "testtok-tapresume"
        env = dict(os.environ, XDG_STATE_HOME=os.path.join(cls.lab, "xdg"), CLAUDE_CONFIG_DIR=claude,
                   ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1", ROMP_SERVE_TOKEN=cls.token,
                   ROMP_KERNEL_PORT=str(cls.port), ROMP_DIST_DIR=dist,
                   ROMP_MODEL_CATALOG="off")   # hermetic: never reach the network
        env.pop("ROMP_STATE_DIR", None)
        cls.klog_path = os.path.join(cls.lab, "kernel.log")
        cls.klog = open(cls.klog_path, "w")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=cls.klog, stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        if getattr(cls, "klog", None):
            cls.klog.close()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    # the routing block a turn push for `api` carries (_push_payload's data): what a tap acts on, what the offer names
    DATA_B = {"sid": SID_B, "host": "", "kind": "turn", "cardId": "", "url": "/?push-reveal=" + SID_B, "name": "api"}
    # …and the whole payload the push service would deliver for it
    PAYLOAD_B = {"title": "api", "body": "Two migrations could go first; which one do you want?", "sid": SID_B,
                 "tag": "romp:" + SID_B, "data": DATA_B}

    def _drive(self, driver_src=DRIVER, **extra):
        base = "http://127.0.0.1:%d" % self.port
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump(dict({"origin": base, "landing": base + "/?token=" + self.token, "sidA": SID_A, "sidB": SID_B,
                            "data": self.DATA_B, "payload": self.PAYLOAD_B}, **extra), f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(driver_src)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served tap needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def _reveal_lines(self):
        """the kernel's own [reveal] trail: delivered or parked, and to which wid"""
        try:
            return " | ".join(ln.strip() for ln in open(self.klog_path, encoding="utf-8", errors="replace") if "[reveal]" in ln) or "(no [reveal] line)"
        except OSError as e:
            return "(kernel log unreadable: %s)" % e

    def _diag_rows(self, what, deadline_s=5.0):
        """the shell's client-diag rows of one kind, waited for (bounded): they ride the shell socket after the fetch"""
        fp = os.path.join(self.state, "client-diag.jsonl")
        end = time.time() + deadline_s
        while True:
            rows = []
            if os.path.exists(fp):
                for ln in open(fp, encoding="utf-8"):
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if r.get("what") == what:
                        rows.append(r)
            if rows or time.time() > end:
                return rows
            time.sleep(0.1)

    def test_a_tap_on_a_backgrounded_app_lands_from_the_store_when_the_page_comes_forward(self):
        out = self._drive()
        # the stage: `web` in front, the worker in control of the page
        self.assertEqual(out["before"], SID_A, "web is the session in front before the tap: %r" % out)
        self.assertTrue(out["controlled"], "the registered worker controls the page (clients.claim): %r" % out)
        # the tap ran the way iOS ran it: the openWindow road, the deep link never loaded, nothing kept in the worker
        w = out["worker"]
        self.assertEqual(w["opened"], "/?push-reveal=" + SID_B, "no client listed → the worker opened the deep link: %r" % w)
        self.assertEqual(w["kept"], "kept", "the tap was kept in memory — and then the worker ended: %r" % w)
        self.assertGreaterEqual(w["waited"], 1, "the tap rode waitUntil: %r" % w)
        self.assertEqual(out["reveals_before_foreground"], 0, "nothing landed while the app was in the background — iOS delivered no message and no load")
        # THE OUTCOME the user sees, first: the app comes forward and the chat pane is on the session that buzzed
        # (pre-fix: still `web`, and no /reveal ever left the page — the 2026-09-09 report)
        self.assertTrue(out["landed"], "the chat pane's active tab must become the session that buzzed; it is still %r and the page posted %d /reveal(s)\n  kernel: %s\n  sockets: %r\n  reveals: %r"
                        % (out["after"], len(out["reveals"]), self._reveal_lines(), out["sockets"], out["reveals"]))
        self.assertEqual(out["after"], SID_B)
        # …and how: the page read the store and landed the tap once, via 'store', on a live page (no boot flag)
        self.assertEqual(len(out["reveals"]), 1, "exactly one /reveal: %r" % out["reveals"])
        rv = out["reveals"][0]
        self.assertEqual((rv["sid"], rv["via"], rv.get("boot")), (SID_B, "store", None), "%r" % rv)
        self.assertTrue(rv.get("wid"), "aimed at this dashboard's wid: %r" % rv)
        # the store: written by the worker before the lookup that found no window, retired once landed
        self.assertIsInstance(w["stored"], dict, "the worker wrote the tap where the page can read it: %r" % w)
        self.assertEqual((w["stored"]["sid"], w["stored"]["kind"], w["stored"]["url"]), (SID_B, "turn", "/?push-reveal=" + SID_B))
        self.assertIsNone(out["storeAfter"], "the entry is retired once landed: %r" % out["storeAfter"])
        # the kernel's own line names the road, and the shell's trail says the resume ran and found the tap
        klog = open(self.klog_path, encoding="utf-8", errors="replace").read()
        self.assertRegex(klog, r"\[reveal\] store sid=%s wid=\S+: delivered" % re.escape(SID_B[:8]), "the kernel logged the store road: %s" % klog[-1500:])
        rows = self._diag_rows("tap-resume")
        found = [r for r in rows if (r.get("data") or {}).get("found") is True]
        self.assertTrue(found, "a tap-resume row with found:true is on file: %r" % rows)
        self.assertIn(found[0]["data"]["via"], ("focus", "pageshow"), "landed by a coming-forward event: %r" % found[0])
        self.assertEqual(found[0]["surface"], "shell")
        for r in rows:
            self.assertNotIn("sid", r.get("data") or {}, "structure only, never the session id: %r" % r)

    def _served_version(self):
        """the build string the kernel bakes into /sw.js (SWV) and the shell (PAGEV)"""
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/sw.js" % self.port, headers={"X-Romp-Token": self.token})
        js = urllib.request.urlopen(req, timeout=5).read().decode()
        m = re.search(r"SWV='([^']+)'", js)
        self.assertIsNotNone(m, "the served worker names its build: %s" % js[:200])
        return m.group(1)

    def _pending_empty(self, ep, tries=50):
        """GET /push/pending for `ep` until it lists nothing (the settles land after the reveal; bounded); the last answer"""
        from urllib.parse import quote
        after = None
        for _ in range(tries):
            _code, after = self._kernel("GET", "/push/pending?endpoint=" + quote(ep, safe=""))
            if after == {"rows": []}:
                break
            time.sleep(0.1)
        return after

    def _kernel(self, method, path, body=None):
        """one call to the hermetic kernel with the serve token: (status, parsed JSON or the text)"""
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Romp-Token": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                code, raw = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read().decode()
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw

    def test_a_tap_the_worker_acked_lands_through_the_kernel_when_no_other_road_reaches_the_page(self):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            raise unittest.SkipTest("python 'cryptography' absent here — the device's subscription needs a real P-256 key")
        import base64
        from urllib.parse import quote
        b64u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
        priv = ec.generate_private_key(ec.SECP256R1())
        p256dh = b64u(priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        # THE DEVICE: subscribed at an endpoint no push service answers. The push's delivery is not what this scenario is
        # about — the kernel files the ledger row BEFORE it sends, and a refused send keeps it (only 404/410 prune)
        ep = "https://127.0.0.1:%d/push/partitioned-device" % _free_port()
        code, res = self._kernel("POST", "/push/subscribe", {"endpoint": ep, "keys": {"p256dh": p256dh, "auth": b64u(os.urandom(16))}})
        self.assertEqual(code, 200, res)
        code, res = self._kernel("POST", "/push/test", {"endpoint": ep, "sid": SID_B, "host": ""})
        self.assertEqual(code, 200, res)
        self.assertEqual(res.get("sid"), SID_B, "the test push is addressed to api: %r" % res)
        code, pend = self._kernel("GET", "/push/pending?endpoint=" + quote(ep, safe=""))
        # against d5653088 there is no such route: the scenario then runs with a pid nothing issued and fails on the
        # OUTCOME below — the page never lands — exactly as the phone did
        rows = _pending_rows(code, pend)
        pid = rows[0]["pid"] if rows else ""
        if pid:
            self.assertEqual((rows[0]["sid"], rows[0]["name"], rows[0]["kind"], rows[0]["stage"]), (SID_B, "api", "test", "sent"), "%r" % pend)
        payload = {"title": "romp", "body": "Test notification — tap to come back to api.", "sid": SID_B, "tag": "romp:" + SID_B,
                   "data": {"sid": SID_B, "host": "", "kind": "test", "cardId": "", "url": "/?push-reveal=" + SID_B, "name": "api",
                            "pid": pid or "unissued-pid-0000000000"}}
        out = self._drive(DRIVER_ACK_ROAD, endpoint=ep, payload=payload)
        self.assertEqual(out["before"], SID_A, "web is the session in front: %r" % out)
        self.assertTrue(out["controlled"], "the registered worker controls the page: %r" % out)
        w = out["worker"]
        self.assertEqual(w["opened"], "/?push-reveal=" + SID_B, "no client listed → the worker took the openWindow road: %r" % w)
        self.assertEqual(out["pageStore"], False, "the store road is cut: nothing the worker wrote reaches the page's Cache Storage: %r" % out["pageStore"])
        self.assertEqual(out["reveals_before_foreground"], 0, "nothing landed while the app was in the background")
        # THE OUTCOME the user sees, first: the app comes forward and the chat pane is on the session that buzzed — with
        # no tap in the store, no message and no link, through the kernel alone
        self.assertTrue(out["landed"], "the chat pane's active tab must become the session that buzzed; it is %r, the page posted %d /reveal(s), the kernel knew pid=%r\n  kernel: %s\n  reveals: %r\n  pending asked: %r"
                        % (out["after"], len(out["reveals"]), bool(pid), self._reveal_lines(), out["reveals"], out["pending"]))
        self.assertEqual(out["after"], SID_B)
        # …and how: the page asked the kernel for its own endpoint, heard 'clicked', landed once via 'ack', settled the row
        self.assertTrue(pid, "the kernel issued the pid the page landed on")
        self.assertEqual(out["pending"][:1], [ep], "asked for THIS device's subscription: %r" % out["pending"])
        self.assertEqual(len(out["reveals"]), 1, "exactly one /reveal: %r" % out["reveals"])
        rv = out["reveals"][0]
        self.assertEqual((rv["sid"], rv["via"], rv.get("boot")), (SID_B, "ack", None), "%r" % rv)
        self.assertTrue(rv.get("wid"), "aimed at this dashboard's wid: %r" % rv)
        self.assertEqual(out["ledger"], [["landed", {"pid": pid}]], "the row is settled once landed")
        self.assertTrue(out["chipAbsent"], "a tap is a jump; there is no offer element in the shell at all")
        # the kernel's own trail, end to end: the worker's two acks (by pid alone), the reveal by the ack road, the settle
        ep_host = "127.0.0.1:" + ep.rsplit(":", 1)[1].split("/", 1)[0]
        klog = open(self.klog_path, encoding="utf-8", errors="replace").read()
        for line in (r"\[push\] ack stage=shown sid=%s endpoint=%s" % (re.escape(SID_B[:8]), re.escape(ep_host)),
                     r"\[push\] ack stage=clicked sid=%s endpoint=%s" % (re.escape(SID_B[:8]), re.escape(ep_host)),
                     r"\[reveal\] ack sid=%s wid=\S+: delivered" % re.escape(SID_B[:8]),
                     r"\[push\] landed sid=%s endpoint=%s" % (re.escape(SID_B[:8]), re.escape(ep_host))):
            self.assertRegex(klog, line, "the kernel logged it: %s" % klog[-2000:])
        self.assertLess(klog.index("[push] ack stage=shown"), klog.index("[push] ack stage=clicked"))
        self.assertLess(klog.index("[push] ack stage=clicked"), klog.index("[reveal] ack "))
        after = self._pending_empty(ep)
        self.assertEqual(after, {"rows": []}, "nothing pending for this device once the tap landed: %r" % after)
        # the shell's trail: the check ran for this subscription and heard a row, landed the clicked one for this session
        # (clipped); and its tap-resume row reads the worker's fingerprints — the page's own build wrote the store, and
        # that store saw no push and no click (the worker wrote to a store of its own here)
        rows = self._diag_rows("tap-pending")
        heard = [r for r in rows if (r.get("data") or {}).get("sub") is True and (r.get("data") or {}).get("rows", 0) >= 1]
        self.assertTrue(heard, "a tap-pending row heard the kernel's row: %r" % rows)
        self.assertEqual((heard[0]["data"]["getNotifications"], heard[0]["surface"]), (True, "shell"))
        self.assertIn(heard[0]["data"]["via"], ("focus", "pageshow"))
        lands = self._diag_rows("tap-pending-land")
        self.assertTrue([r for r in lands if (r.get("data") or {}).get("dup") is False and (r.get("data") or {}).get("sid") == SID_B[:8]], "…and landed it: %r" % lands)
        v = self._served_version()
        fps = [r["data"] for r in self._diag_rows("tap-resume") if (r.get("data") or {}).get("swVersion") == v]
        self.assertTrue(fps, "the tap-resume rows carry the page's own worker's fingerprint")
        self.assertEqual({k: fps[-1][k] for k in ("found", "swMatchesPage", "lastPushAgeS", "lastClickAgeS", "clicks")},
                         {"found": False, "swMatchesPage": True, "lastPushAgeS": -1, "lastClickAgeS": -1, "clicks": 0},
                         "the live evidence, reproduced: a current worker that saw neither the push nor the tap: %r" % fps[-1])


    def test_a_vanished_notification_lands_when_the_app_comes_forward_and_nothing_else_ever_shows(self):
        # the fourth scenario (the module docstring): three shown notifications, and the one gone from the screen is the tap
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            raise unittest.SkipTest("python 'cryptography' absent here — the device's subscription needs a real P-256 key")
        import base64
        from urllib.parse import quote
        b64u = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
        priv = ec.generate_private_key(ec.SECP256R1())
        p256dh = b64u(priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        ep = "https://127.0.0.1:%d/push/vanish-device" % _free_port()
        code, res = self._kernel("POST", "/push/subscribe", {"endpoint": ep, "keys": {"p256dh": p256dh, "auth": b64u(os.urandom(16))}})
        self.assertEqual(code, 200, res)
        for sid in (SID_B, SID_C, SID_D):   # three buzzes, three sessions (the per-session tag would collapse three for one)
            code, res = self._kernel("POST", "/push/test", {"endpoint": ep, "sid": sid, "host": ""})
            self.assertEqual(code, 200, res)
            self.assertEqual(res.get("sid"), sid)
        code, pend = self._kernel("GET", "/push/pending?endpoint=" + quote(ep, safe=""))
        rows = _pending_rows(code, pend)   # against 3b8b60f8 this names the newest row alone: the scenario runs on and fails on the OUTCOME below
        pid_of = {r["sid"]: r["pid"] for r in rows}
        for pid in pid_of.values():   # the worker's word, as the phone's worker gives it a second after the send: shown
            code, res = self._kernel("POST", "/push/ack", {"pid": pid, "stage": "shown", "v": "browser-test"})
            self.assertEqual(code, 200, res)
        pids = [pid_of.get(sid) or "unissued-pid-000000000%d" % i for i, sid in enumerate((SID_B, SID_C, SID_D))]
        out = self._drive(DRIVER_VANISH, endpoint=ep, pids=pids)
        self.assertEqual(out["before"], SID_A, "web is the session in front: %r" % out)
        self.assertTrue(out["controlled"], "the registered worker controls the page: %r" % out)
        self.assertEqual(out["reveals_before"], 0, "nothing landed on the way in: every notification was still on the screen")
        p1, p2, p3 = out["passes"]
        # THE OUTCOME the user sees, first: the app comes forward with api's notification gone from the screen, and the chat
        # pane is on api — no chip, no prompt, nothing to take. Against 3b8b60f8: the chip stood at bottom-left naming the
        # newest row's session, the tab stayed on web, and no /reveal left the page
        self.assertTrue(p1["landed"], "the chat pane's active tab must become the session whose notification vanished; it is %r, the page posted %d /reveal(s), the kernel knew %d pid(s)\n  kernel: %s\n  pass: %r"
                        % (p1["after"], len(p1["reveals"]), len(pid_of), self._reveal_lines(), p1))
        self.assertEqual(p1["after"], SID_B)
        self.assertTrue(out["chipAbsentBefore"] and all(p["chipAbsent"] for p in out["passes"]), "no offer element exists in the shell, ever: %r" % out["passes"])
        # …and how: three rows the kernel filed and the worker acked shown; the page asked for its own subscription, read the
        # screen, found exactly one gone, and landed it via 'vanish' — once across the two checks a coming-forward fires
        self.assertEqual(sorted(pid_of), sorted([SID_B, SID_C, SID_D]), "three rows, one per session: %r" % pend)
        self.assertGreaterEqual(out["pending"], 2, "asked on the coming-forward events: %r" % out["pending"])
        self.assertEqual(len(p1["reveals"]), 1, "exactly one /reveal: %r" % p1["reveals"])
        rv = p1["reveals"][0]
        self.assertEqual((rv["sid"], rv["via"], rv.get("boot")), (SID_B, "vanish", None), "%r" % rv)
        self.assertTrue(rv.get("wid"), "aimed at this dashboard's wid: %r" % rv)
        self.assertEqual(p1["ledger"], [["landed", {"pid": pids[0]}]], "the vanished row is landed; the displayed ones are untouched")
        self.assertTrue(p1["decided"], "both checks filed their tap-pending row: %r" % p1["rows"])
        self.assertEqual(sorted(r["vanished"] for r in p1["rows"]), [0, 1], "the first check found the one gone; the second saw it already landed: %r" % p1["rows"])
        for r in p1["rows"]:
            self.assertEqual((r["sub"], r["rows"], r["getNotifications"], r["displayed"]), (True, 3, True, 2), "%r" % r)
            self.assertIn(r["via"], ("focus", "pageshow"))
        # everything displayed: nothing — no reveal, no settle, the tab where the user left it
        self.assertEqual((p2["reveals"], p2["ledger"], p2["after"]), ([], [], SID_B), "%r" % p2)
        self.assertTrue(all(r["vanished"] == 0 and r["displayed"] == 3 for r in p2["rows"]), "%r" % p2["rows"])
        # two gone at once: the tap could have been on either — nothing lands, nothing shows; both rows are dropped
        self.assertEqual((p3["reveals"], p3["after"]), ([], SID_B), "%r" % p3)
        self.assertEqual(sorted(json.dumps(x) for x in p3["ledger"]), sorted(json.dumps(x) for x in [["dropped", {"pid": pids[1]}], ["dropped", {"pid": pids[2]}]]), "%r" % p3["ledger"])
        self.assertEqual(sorted(r["vanished"] for r in p3["rows"]), [0, 2], "%r" % p3["rows"])
        self.assertEqual(self._pending_empty(ep), {"rows": []}, "every row settled: nothing left to poison a later check")
        # the kernel's own trail: three shown acks, the vanish reveal, the landing, the two drops; no offer road, no dismissal
        ep_host = "127.0.0.1:" + ep.rsplit(":", 1)[1].split("/", 1)[0]
        klog = open(self.klog_path, encoding="utf-8", errors="replace").read()
        for line in ([r"\[push\] ack stage=shown sid=%s endpoint=%s" % (re.escape(sid[:8]), re.escape(ep_host)) for sid in (SID_B, SID_C, SID_D)] +
                     [r"\[reveal\] vanish sid=%s wid=\S+: delivered" % re.escape(SID_B[:8]),
                      r"\[push\] landed sid=%s endpoint=%s" % (re.escape(SID_B[:8]), re.escape(ep_host)),
                      r"\[push\] dropped sid=%s endpoint=%s" % (re.escape(SID_C[:8]), re.escape(ep_host)),
                      r"\[push\] dropped sid=%s endpoint=%s" % (re.escape(SID_D[:8]), re.escape(ep_host))]):
            self.assertRegex(klog, line, "the kernel logged it: %s" % klog[-2500:])
        self.assertNotIn("[reveal] offer", klog)
        self.assertNotIn("[push] dismissed", klog)
        self.assertEqual(klog.count("[reveal] vanish"), 1, "landed once across the two checks: %s" % klog[-2500:])
        # the shell's trail: the landing's own row names the session clipped; no offer row of any kind is on file
        lands = self._diag_rows("tap-vanish-land")
        self.assertEqual([r["data"] for r in lands], [{"sid8": SID_B[:8], "ageS": lands[0]["data"]["ageS"]}] if lands else [], "one tap-vanish-land row, clipped: %r" % lands)
        self.assertTrue(lands)
        self.assertEqual(lands[0]["surface"], "shell")
        self.assertEqual(self._diag_rows("tap-offer", deadline_s=0.0), [], "no offer row: the chip is gone")
        for r in self._diag_rows("tap-pending") + lands:
            self.assertNotIn(SID_B, json.dumps(r), "structure and clipped ids only, never the session id whole: %r" % r)


def _pending_rows(code, pend):
    """what GET /push/pending listed, as rows: {rows: [...]} from this build; a single row unwrapped from the build
    before it (3b8b60f8, so its fail-before runs on the outcome); nothing from a kernel without the route"""
    if code != 200 or not isinstance(pend, dict):
        return []
    if isinstance(pend.get("rows"), list):
        return [r for r in pend["rows"] if isinstance(r, dict) and r.get("pid")]
    return [pend] if pend.get("pid") else []


if __name__ == "__main__":
    unittest.main()
