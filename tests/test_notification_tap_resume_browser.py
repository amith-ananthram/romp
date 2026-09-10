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

THE SECOND SCENARIO (2026-09-09, later — the app WARM this time): three taps, three 201s from the push
service, and then nothing. No [reveal] line, no sw-message row, tap-resume found:false on every resume, and
each tap booting a fresh page on the start URL with no link. Either iOS handed the tap to the live app and the
worker's click handler never ran, or the phone still ran an older worker that never wrote the store; the
trail could not say. So the worker now writes '/__romp/shown' for every session-addressed notification it
displays, BEFORE attempting the show, and a page that comes forward with no tap stored but a shown record
OFFERS the session (a chip, not a jump — the user may have opened the app for another reason). Here: the
REAL push handler runs on a synthetic PushEvent carrying the kernel's payload shape; notificationclick is
never dispatched; the page comes forward; the chip appears at bottom-left naming `api`; clicking it lands the
reveal via 'offer' and retires the record. Headless Chromium refuses showNotification, so the show rejects —
and the record must already be there. Against b8d2a90b this fails on the outcome: no chip, no record, the
tab still `web`.

Skips LOUDLY without the extension deps or a playwright browser (CI installs none). All fixtures
synthetic. Under ~30 s, no network.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"   # web: the session in front when the phone buzzes
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"   # api: the session that buzzed — where the tap must land


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

# the warm-app scenario: the push shows (or tries to), NO click ever runs, the page comes forward, the offer is taken
DRIVER_OFFER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { reveals: [] };
const context = await browser.newContext({ viewport: { width: 1100, height: 720 } });
await context.grantPermissions(["notifications"], { origin: cfg.origin });
const page = await context.newPage();
page.on("request", (r) => { if (r.method() === "POST" && /\/reveal$/.test(r.url())) out.reveals.push(JSON.parse(r.postData() || "{}")); });
await page.goto(cfg.landing);
const chat = await (async () => { for (let i = 0; i < 200; i++) { const f = page.frames().find((f) => /\/chat/.test(f.url())); if (f) return f; await page.waitForTimeout(50); } return null; })();
if (!chat) { console.error("no chat iframe in the shell"); process.exit(1); }
await chat.waitForSelector('#tabs .tab[data-id="' + cfg.sidB + '"]', { timeout: 20000 });
await chat.evaluate((sid) => { const t = document.querySelector('#tabs .tab[data-id="' + sid + '"]'); if (t) t.click(); }, cfg.sidA);
await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, cfg.sidA, { timeout: 10000 });
out.before = await chat.evaluate(() => (document.querySelector("#tabs .tab.active") || { dataset: {} }).dataset.id || null);
const active = () => chat.evaluate(() => (document.querySelector("#tabs .tab.active") || { dataset: {} }).dataset.id || null);
const swWait = context.waitForEvent("serviceworker", { timeout: 20000 }).catch(() => null);
await page.evaluate(() => navigator.serviceWorker.register("/sw.js"));
const sw = context.serviceWorkers()[0] || await swWait;
if (!sw) { console.error("no service worker registered"); process.exit(1); }
await page.evaluate(() => navigator.serviceWorker.ready.then(() => navigator.serviceWorker.controller ? null
  : new Promise((r) => navigator.serviceWorker.addEventListener("controllerchange", () => r(), { once: true }))));
out.controlled = await page.evaluate(() => !!navigator.serviceWorker.controller);
out.chipBeforePush = await page.evaluate(() => { const e = document.getElementById("tap-offer"); return e ? e.hidden : "absent"; });
// THE PUSH, as the phone receives it: the REAL push handler on a synthetic PushEvent carrying the kernel's payload (a
// script cannot mint a real one; the handler reads only .data.json() and .waitUntil). Headless Chromium refuses
// showNotification, so the show REJECTS here — and the shown record must already be written: the offer depends on
// the show no more than on the click. No notificationclick is ever dispatched: the warm phone's trail had none
out.worker = await sw.evaluate(async (payload) => {
  const waited = [];
  const ev = new Event("push"); ev.data = { json: () => payload }; ev.waitUntil = (p) => waited.push(p);
  self.dispatchEvent(ev);
  const settled = await Promise.allSettled(waited);
  const read = (k) => caches.open("romp-tap").then((c) => c.match(k)).then((r) => (r ? r.json() : null)).catch((e) => "err: " + e);
  let shown = null;
  for (let i = 0; i < 40 && !shown; i++) { shown = await read("/__romp/shown"); if (!shown) await new Promise((r) => setTimeout(r, 25)); }
  return { waited: waited.length, outcomes: settled.map((s) => s.status + (s.status === "rejected" ? ": " + ((s.reason && s.reason.message) || String(s.reason)) : "")),
           shown, fp: await read("/__romp/sw"), tap: await read("/__romp/tap") };
}, cfg.payload);
out.reveals_before_foreground = out.reveals.length;
// the app comes forward: the events a resumed page produces, and nothing else
await page.evaluate(() => { window.dispatchEvent(new Event("focus")); window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })); });
// the offer: a chip, not a jump
out.chip = await page.waitForSelector("#tap-offer:not([hidden])", { timeout: 8000 }).then(() => true).catch(() => false);
out.chipText = await page.evaluate(() => { const e = document.getElementById("tap-offer-go"); return e ? e.textContent : null; });
out.chipBox = await page.evaluate(() => { const e = document.getElementById("tap-offer"); if (!e || e.hidden) return null;
  const r = e.getBoundingClientRect(); return { left: r.left, fromBottom: window.innerHeight - r.bottom, w: r.width, h: r.height }; });
out.stillBefore = await active();
out.reveals_before_click = out.reveals.length;
if (out.chip) await page.click("#tap-offer-go");
out.landed = await chat.waitForFunction((sid) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === sid, cfg.sidB, { timeout: 8000 }).then(() => true).catch(() => false);
out.after = await active();
out.chipHidden = await page.evaluate(() => { const e = document.getElementById("tap-offer"); return !e || e.hidden; });
out.shownAfter = await (async () => { let s = null; for (let i = 0; i < 40; i++) {
  s = await sw.evaluate(() => caches.open("romp-tap").then((c) => c.match("/__romp/shown")).then((r) => (r ? r.json() : null)));
  if (s === null) break; await page.waitForTimeout(50); } return s; })();
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
        copy_dist(os.path.join(EXT, "dist"), dist)
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
                (SID_B, "api", "Add the notes table migration.", "Two migrations could go first; which one do you want?")):
            Path(cls.state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(cls.state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid,
                 "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, prompt, reply))   # a CLOSED turn: nothing to resume
        cls.port = _free_port()
        cls.token = "testtok-tapresume"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
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

    def _drive(self, driver_src=DRIVER):
        base = "http://127.0.0.1:%d" % self.port
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"origin": base, "landing": base + "/?token=" + self.token, "sidA": SID_A, "sidB": SID_B,
                       "data": self.DATA_B, "payload": self.PAYLOAD_B}, f)
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

    def test_a_notification_shown_but_never_tapped_is_offered_when_the_app_comes_forward(self):
        out = self._drive(DRIVER_OFFER)
        self.assertEqual(out["before"], SID_A, "web is the session in front: %r" % out)
        self.assertTrue(out["controlled"], "the registered worker controls the page: %r" % out)
        w = out["worker"]
        self.assertGreaterEqual(w["waited"], 1, "the push rode waitUntil: %r" % w)
        self.assertEqual(out["reveals_before_foreground"], 0)
        # THE OUTCOME the user sees, first: the app comes forward, nothing jumps, and the offer is there — against
        # b8d2a90b: no chip (the element does not exist), no record, and the page sits on `web` exactly as the phone did
        self.assertTrue(out["chip"], "the offer chip must appear when the app comes forward with a shown-but-untapped notification; "
                                     "chip=%r text=%r tab=%r reveals=%r\n  worker: %r\n  kernel: %s"
                        % (out["chip"], out["chipText"], out["stillBefore"], out["reveals"], w, self._reveal_lines()))
        self.assertEqual(out["stillBefore"], SID_A, "an offer, not a jump: nothing moved until the user took it")
        self.assertEqual(out["reveals_before_click"], 0)
        self.assertEqual(out["chipText"], "Open api · from the notification", "named from the payload alone, no kernel round-trip")
        self.assertEqual(out["chipBeforePush"], True, "the chip exists in the shell and is hidden until there is something to offer")
        box = out["chipBox"]
        self.assertIsNotNone(box)
        self.assertLess(box["left"], 40, "bottom-LEFT, the jump chip's corner: %r" % box)
        self.assertGreaterEqual(box["fromBottom"], 30, "above the desktop rail (30px): %r" % box)
        # how the record got there: the real push handler wrote it BEFORE attempting the show — which headless
        # Chromium refuses, so the outcome is on record either way; no click ever ran, so no tap is stored
        self.assertIsInstance(w["shown"], dict, "the push wrote the shown record: %r" % w)
        self.assertEqual((w["shown"]["sid"], w["shown"]["kind"], w["shown"]["name"], w["shown"]["url"]), (SID_B, "turn", "api", "/?push-reveal=" + SID_B))
        self.assertIsNone(w["tap"], "no click ran: nothing in the tap store")
        v = self._served_version()
        self.assertIsInstance(w["fp"], dict, "the worker's fingerprint is on record: %r" % w)
        self.assertEqual(w["fp"]["version"], v, "written by the worker this kernel serves")
        self.assertGreater(w["fp"]["installedAt"], 0)
        self.assertGreater(w["fp"]["activatedAt"], 0)
        self.assertGreater(w["fp"]["lastPushAt"], 0)
        self.assertEqual((w["fp"]["clicks"], w["fp"]["lastClickAt"]), (0, 0), "no click, and the fingerprint says so")
        # taking the offer: the same landing path, the road named, the record retired, the chip gone
        self.assertTrue(out["landed"], "clicking the chip must put the chat pane on api; it is %r, reveals %r\n  kernel: %s" % (out["after"], out["reveals"], self._reveal_lines()))
        self.assertEqual(out["after"], SID_B)
        self.assertEqual(len(out["reveals"]), 1, "exactly one /reveal: %r" % out["reveals"])
        rv = out["reveals"][0]
        self.assertEqual((rv["sid"], rv["via"], rv.get("boot")), (SID_B, "offer", None), "%r" % rv)
        self.assertTrue(rv.get("wid"))
        self.assertTrue(out["chipHidden"])
        self.assertIsNone(out["shownAfter"], "the record is retired once taken: %r" % out["shownAfter"])
        klog = open(self.klog_path, encoding="utf-8", errors="replace").read()
        self.assertRegex(klog, r"\[reveal\] offer sid=%s wid=\S+: delivered" % re.escape(SID_B[:8]), "the kernel logged the offer road: %s" % klog[-1500:])
        offers = self._diag_rows("tap-offer")
        self.assertTrue([r for r in offers if (r.get("data") or {}).get("shown") is True], "a tap-offer row says the chip showed: %r" % offers)
        self.assertTrue(self._diag_rows("tap-offer-click"), "…and that it was taken")
        fps = [r for r in self._diag_rows("tap-resume") if (r.get("data") or {}).get("swVersion") == v]
        self.assertTrue(fps, "the tap-resume rows carry the worker's fingerprint")
        self.assertTrue(all(r["data"]["swMatchesPage"] is True for r in fps), "…and it matches the page's build: %r" % fps[:2])
        for r in offers + fps:
            for k in (r.get("data") or {}):
                self.assertNotIn("sid", k.lower(), "structure only, never the session id: %r" % r)


if __name__ == "__main__":
    unittest.main()
