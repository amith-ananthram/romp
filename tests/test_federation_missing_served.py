"""A kernel-served chat pane whose federation manager never came up fails LOUDLY and never writes the arrangement
(2026-09-10). The duplicate-id fix (adoptArrival) is a prerequisite for the driver to reach the federation legs: without it the
healthy leg's first drag writes a doubled arrangement and the wait for the reverse dies naming what was written. The real page in a real browser: federation.js is aborted for the chat frame, the pane retries once,
then shows the banner, refuses drags and writes nothing to the browser's arrangement (romp:vieworder), and the kernel's
client-diag.jsonl gains one federation-missing row carrying the load entry. With the bundle served the same page shows the
arrangement, not the kernel's seed, and a drag persists exactly one copy of every id (the duplicate-id fix). Skips LOUDLY
when the extension deps or a playwright browser are absent. The lab kernel's environment is kernel_env's list of
names, never a copy of the runner's; FedMissingLabKernelEnv pins that and runs everywhere. Synthetic sessions and text
only."""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    from tests.dist_copy import copy_dist
except ImportError:   # an older tree without the staging-aware copy: a plain copy serves the same
    copy_dist = shutil.copytree

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's
# Hermetic state for a bare unittest or script run, which has no conftest floor: the runner's own process must never
# resolve REAL state (the lab kernel's roots come from kernel_env below).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SID_A = "11111111-2222-4333-8444-000000000701"
SID_B = "11111111-2222-4333-8444-000000000702"
SID_C = "11111111-2222-4333-8444-000000000703"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def lab_kernel_env(lab, claude, dist, port, token):
    """The lab kernel's environment: kernel_env's list of names, never a copy of the runner's (a shell on a machine
    running romp carries the live kernel's exports, and the list carries none of the retired key names or 1Password
    names the kernel refuses to boot with either). With the list comes the postal trio kernel_env gives every lab
    kernel (ROMP_POSTAL_CLIENT_ONLY=1, its own ROMP_POSTAL_PORT, ROMP_POSTAL_PEERS=0), a bus of its own that is never
    started: without it this kernel's boot-time ensure started a detached bus on the machine's FIXED port, which
    outlived the kernel and held the shared bus port after a restart (2026-09-10);
    tests/test_hermetic_kernel_postal.py guards every spawn site for it."""
    return _lab.kernel_env(lab, claude, dist, port, token)


def _transcript(sid, cwd, pairs):
    """`pairs` closed user/assistant turns (an OPEN turn would invite the boot reconcile to resume it)."""
    out, parent, t = [], None, 1_700_000_000
    for i in range(pairs):
        u = "%s-a%04x" % (sid[:23], i)
        a = "%s-b%04x" % (sid[:23], i)
        ts = lambda k: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t + i * 60 + k))
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": ts(0), "sessionId": sid, "cwd": cwd,
                    "message": {"role": "user", "content": "please keep going with the notes-api search module (part %d)" % (i + 1)}})
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ts(5), "sessionId": sid, "cwd": cwd,
                    "message": {"id": "msg_lab_%s_%04d" % (sid[-3:], i), "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                                "content": [{"type": "text", "text": "Note %d: the tokenizer fixture set covers the hyphen cases now." % (i + 1)}],
                                "stop_reason": "end_turn"}})
        parent = a
    return "\n".join(json.dumps(r) for r in out) + "\n"


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const out = { t0: Date.now(), fedRequests: 0 };
const die = async (why) => {
  out.ms = Date.now() - out.t0;
  try { out.atDeath = await page.evaluate(() => { const w = document.getElementById("f-chat") && document.getElementById("f-chat").contentWindow;
    return { arrangement: localStorage.getItem("romp:vieworder"), sessionList: w && w.__rompSessionList ? w.__rompSessionList().map((r) => String(r.id)) : null }; }); } catch (e) { /* page gone */ }
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n"); await browser.close(); process.exit(0); };
const T = 20000;
const waitFn = async (fn, arg, why) => page.waitForFunction(fn, arg, { timeout: T }).catch(async (e) => { await die(why + " (" + String(e).split("\n")[0] + ")"); });
const chatDoc = () => document.getElementById("f-chat") && document.getElementById("f-chat").contentDocument;
const waitTabs = (sids) => waitFn((sids) => { const d = document.getElementById("f-chat") && document.getElementById("f-chat").contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id); return sids.every((s) => ids.includes(s)); }, sids, "the chat never showed tabs " + sids.join(","));
const strip = () => page.evaluate(() => Array.from(document.getElementById("f-chat").contentDocument.querySelectorAll("#tabs .tab[data-id]")).map((t) => ({ id: t.dataset.id, draggable: t.draggable })));
const paneState = () => page.evaluate(() => { const w = document.getElementById("f-chat").contentWindow; const d = w.document; const bar = d.getElementById("rfed");
  return { fed: !!w.__rompFed, banner: bar ? bar.textContent : null, bannerButton: !!(bar && bar.querySelector("button")), retryMarker: w.sessionStorage.getItem("romp:fed-retry") }; });
const arrangement = () => page.evaluate(() => JSON.parse(localStorage.getItem("romp:vieworder") || "null"));
const chatFrame = async () => { const h = await page.$("#f-chat"); return h.contentFrame(); };
// a real HTML5 drag of one tab onto another, inside the chat frame (the strip's dragstart/dragover/drop)
const dragTab = async (from, to) => { const fr = await chatFrame(); await fr.dragAndDrop('#tabs .tab[data-id="' + from + '"]', '#tabs .tab[data-id="' + to + '"]'); };
// a SYNTHETIC drag inside the frame: the strip's own dragstart / dragover / drop / dragend, dispatched on the tab nodes with
// one DataTransfer, while every write of the arrangement key is counted — so a refusal is measured at the guards themselves
// (a real pointer never starts a drag on an undraggable tab), and the same helper proves itself on a healthy pane
const syntheticDrag = (from, to) => page.evaluate(([from, to]) => {
  const w = document.getElementById("f-chat").contentWindow, d = w.document;
  const src = d.querySelector('#tabs .tab[data-id="' + from + '"]'), dst = d.querySelector('#tabs .tab[data-id="' + to + '"]');
  let writes = 0; const orig = w.Storage.prototype.setItem;
  w.Storage.prototype.setItem = function (k, v) { if (k === "romp:vieworder") writes++; return orig.call(this, k, v); };
  const dt = new w.DataTransfer(); const r = dst.getBoundingClientRect();
  const fire = (type, target, x) => target.dispatchEvent(new w.DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: r.top + r.height / 2 }));
  const started = fire("dragstart", src, src.getBoundingClientRect().left + 4);
  fire("dragover", dst, r.right - 2); fire("drop", dst, r.right - 2); fire("dragend", src, r.right - 2);
  w.Storage.prototype.setItem = orig;
  return { started, writes, order: w.__rompSessionList().map((x) => String(x.id)), strip: Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id) };
}, [from, to]);
const clickTab = async (sid) => { const fr = await chatFrame(); await fr.click('#tabs .tab[data-id="' + sid + '"]', { timeout: 5000 }); };
const activeTab = () => page.evaluate(() => { const t = document.getElementById("f-chat").contentDocument.querySelector("#tabs .tab.active[data-id]"); return t ? t.dataset.id : null; });
const hitAtTab = (sid) => page.evaluate((sid) => { const d = document.getElementById("f-chat").contentDocument; const t = d.querySelector('#tabs .tab[data-id="' + sid + '"]');
  const r = t.getBoundingClientRect(); const e = d.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!(e && t.contains(e)); }, sid);
// the first chat column's frame: /chat with no col (a split's later columns say ?col=N)
const isChatFrame = (route) => { try { const u = new URL(route.request().frame().url()); return u.pathname === "/chat" && !u.searchParams.has("col"); } catch (e) { return false; } };

// ---- 1. healthy: the kernel's order K shows; a drag flips it, and the arrangement persists the reverse ----
let abortFed = false, abortOnce = 0;   // the standing failure, and a one-shot one (the self-heal leg)
// the retry's bound is an EVENT: within one leg (one driver-initiated navigation) the chat frame asks for the bundle at
// most twice — the load and its single retry; a third request is a loop, named here, never a harness timeout
let legRequests = 0;
const newLeg = () => { legRequests = 0; };
await page.route("**/dist/federation.js*", (route) => {
  const chat = isChatFrame(route);
  if (chat) { out.fedRequests++; legRequests++; }
  if (chat && legRequests > 2) { route.abort("failed"); die("the retry looped: the chat frame asked for the bundle a third time in one leg"); return; }
  if (abortFed || (chat && abortOnce > 0)) { if (chat && abortOnce > 0) abortOnce--; route.abort("failed"); } else route.continue(); });
const reload = async () => { newLeg(); await page.reload({ waitUntil: "commit" }).catch(() => {}); };
await page.goto(cfg.url);
await waitTabs([cfg.sidA, cfg.sidB]);
await waitFn(() => { const w = document.getElementById("f-chat").contentWindow; return !!w.__rompFed; }, null, "the healthy pane never got its manager");
const K = (await strip()).map((t) => t.id);
const REV = K.slice().reverse();
out.healthy = { ...(await paneState()), K, arrangementBefore: await arrangement(),
                sessionList: await page.evaluate(() => document.getElementById("f-chat").contentWindow.__rompSessionList().map((r) => String(r.id))) };
out.healthy.drag = await syntheticDrag(K[0], K[1]);   // a tab dropped on another lands AFTER it: the first onto the second reverses the pair
await waitFn((rev) => JSON.stringify(JSON.parse(localStorage.getItem("romp:vieworder") || "[]")) === JSON.stringify(rev), REV, "the healthy pane's drag never persisted");
out.healthy.arrangement = await arrangement();
out.healthy.stripAfterDrag = (await strip()).map((t) => t.id);
// ---- 2. the manager's bundle stops arriving: a reload → the pane retries ONCE → the banner; the strip shows K, not the arrangement ----
abortFed = true;
const requestsBefore = out.fedRequests;
await reload();
const bannerUp = () => waitFn(() => { const d = document.getElementById("f-chat") && document.getElementById("f-chat").contentDocument; return !!(d && d.getElementById("rfed")); }, null, "the banner never came up after the one retry");
await bannerUp();
await waitTabs([cfg.sidA, cfg.sidB]);
out.broken = { ...(await paneState()), strip: await strip(), arrangementBefore: await arrangement(), fedRequests: out.fedRequests - requestsBefore,
               bannerAboveStrip: await page.evaluate(() => { const d = document.getElementById("f-chat").contentDocument; const b = d.getElementById("rfed"), t = d.getElementById("tabbar");
                 return { inFlow: getComputedStyle(b).position === "static", next: b.nextElementSibling && b.nextElementSibling.id, bBottom: b.getBoundingClientRect().bottom, tTop: t.getBoundingClientRect().top }; }) };
// the strip under the banner still WORKS: a plain click (no force) on the other tab selects it, and the pointer lands on the tab
const other = (await activeTab()) === K[0] ? K[1] : K[0];
out.broken.hitOnTab = await hitAtTab(other);
await clickTab(other);
await waitFn((sid) => { const t = document.getElementById("f-chat").contentDocument.querySelector("#tabs .tab.active[data-id]"); return !!t && t.dataset.id === sid; }, other, "a plain click on a tab under the banner never selected it");
out.broken.clickedActive = await activeTab();
// a drag in the manager-less pane: refused at the guards — no arrangement write, the pane's order and the strip exactly as they were
out.broken.drag = await syntheticDrag(K[0], K[1]);
out.broken.arrangementAfterDrag = await arrangement();
// the one path that used to re-arm a drag in this state: Rename, then Escape, restores the tab's draggable flag
const fr0 = await chatFrame();
await fr0.click('#tabs .tab[data-id="' + K[0] + '"]', { button: "right", timeout: 5000 });
await waitFn(() => { const d = document.getElementById("f-chat").contentDocument; return Array.from(d.querySelectorAll(".ctx-menu .ctx-item-label")).some((l) => l.textContent === "Rename"); }, null, "the tab menu never offered Rename");
await page.evaluate(() => { const d = document.getElementById("f-chat").contentDocument; Array.from(d.querySelectorAll(".ctx-menu .ctx-item")).find((i) => { const l = i.querySelector(".ctx-item-label"); return l && l.textContent === "Rename"; }).click(); });
await waitFn(() => !!document.getElementById("f-chat").contentDocument.querySelector("#tabs .tab input"), null, "the rename box never opened");
await page.keyboard.press("Escape");
await waitFn(() => !document.getElementById("f-chat").contentDocument.querySelector("#tabs .tab input"), null, "the rename box never closed");
out.broken.afterRename = { draggable: await page.evaluate((sid) => document.getElementById("f-chat").contentDocument.querySelector('#tabs .tab[data-id="' + sid + '"]').draggable, K[0]),
                           drag: await syntheticDrag(K[0], K[1]), arrangement: await arrangement() };
// a SECOND load with the bundle still failing: the marker was consumed with the first incident's row, so this load gets
// its own single retry and files its own row (a shell reload or a build drift must not inherit a spent retry)
const requestsBefore2 = out.fedRequests;
await reload();
await bannerUp();
out.broken.secondLoad = { fedRequests: out.fedRequests - requestsBefore2, ...(await paneState()) };
// ---- 3. the bundle is back: the banner's Reload heals the pane, which shows the arrangement again ----
abortFed = false;
newLeg();
await page.evaluate(() => { const b = document.getElementById("f-chat").contentDocument.getElementById("rfed"); b.querySelector("button").click(); });
await waitFn(() => { const f = document.getElementById("f-chat"); const w = f && f.contentWindow; return !!(w && w.__rompFed && w.document.querySelectorAll("#tabs .tab[data-id]").length >= 2); }, null, "the pane never came back with its manager");
out.healed = { ...(await paneState()), strip: (await strip()).map((t) => t.id), arrangement: await arrangement() };
// ---- 3b. the bundle fails ONCE: the pane's single retry gets it — no banner, the strip arranged — and the incident is still filed ----
abortOnce = 1;
const requestsBeforeOnce = out.fedRequests;
await reload();
await waitFn(() => { const f = document.getElementById("f-chat"); const w = f && f.contentWindow; return !!(w && w.__rompFed && w.document.querySelectorAll("#tabs .tab[data-id]").length >= 2); }, null, "the pane never came up after the one-shot failure");
await waitTabs([cfg.sidA, cfg.sidB]);
out.recovered = { ...(await paneState()), strip: (await strip()).map((t) => t.id), fedRequests: out.fedRequests - requestsBeforeOnce };
// ---- 4. a third session appears while the pane is open; a drag then persists ONE copy of every id ----
fs.writeFileSync(cfg.plant.names, cfg.plant.namesText);
fs.writeFileSync(cfg.plant.sdk, cfg.plant.sdkText);
fs.writeFileSync(cfg.plant.transcript, cfg.plant.transcriptText);
await waitTabs([cfg.sidA, cfg.sidB, cfg.sidC]);
await waitFn((c) => { const w = document.getElementById("f-chat").contentWindow; const ls = w.__rompSessionList ? w.__rompSessionList() : []; return ls.some((r) => String(r.id) === c); }, cfg.sidC, "the third session never landed in the pane");
out.third = { before: (await strip()).map((t) => t.id) };
await dragTab(cfg.sidC, REV[0]);   // the newcomer (last) dropped on the first tab: it lands second
await waitFn((c) => { const v = JSON.parse(localStorage.getItem("romp:vieworder") || "[]"); return v.includes(c) && v[v.length - 1] !== c; }, cfg.sidC, "the drag of the new tab never persisted");
out.third = { ...out.third, arrangement: await arrangement(), strip: (await strip()).map((t) => t.id),
              sessionList: await page.evaluate(() => { const w = document.getElementById("f-chat").contentWindow; return (w.__rompSessionList ? w.__rompSessionList() : []).map((r) => String(r.id)); }) };
out.ms = Date.now() - out.t0;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedFederationMissing(unittest.TestCase):
    """One kernel, one page, one driver run in setUpClass; each method asserts one part of the shared result."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="fed-missing-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        os.makedirs(os.path.join(state, "names"), exist_ok=True)
        os.makedirs(os.path.join(state, "sdk"), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # two synthetic SDK sessions with closed-turn transcripts (nothing is ever resumed or spawned); a third is
        # planted by the driver mid-run
        for sid, name in ((SID_A, "web"), (SID_B, "api")):
            Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, cwd, 3))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
        cls.plant = {"names": os.path.join(state, "names", SID_C), "namesText": "tests\t%s\t\t\n" % cwd,
                     "sdk": os.path.join(state, "sdk", SID_C + ".json"),
                     "sdkText": json.dumps({"sid": SID_C, "name": "tests", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID_C, "alive": True}),
                     "transcript": os.path.join(proj, SID_C + ".jsonl"), "transcriptText": _transcript(SID_C, cwd, 3)}
        cls.diag = os.path.join(state, "client-diag.jsonl")
        cls.port = _free_port()
        cls.token = "testtok-fedmissing"
        cls.env = lab_kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=cls.env)
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
        cls.result, cls.driver_error = None, None
        cls._drive()

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token), "sidA": SID_A, "sidB": SID_B, "sidC": SID_C,
                       "plant": cls.plant}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            so = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode()
            cls.driver_error = "driver timed out; partial output:\n%s" % so[-3000:]
            return
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served leg needs one (CI installs none)")
        if p.returncode != 0:
            cls.driver_error = "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
            return
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            cls.driver_error = "driver printed no result:\n" + p.stdout[-3000:]
            return
        r = json.loads(line[len("RESULT:"):])
        if "died" in r:
            cls.driver_error = "driver aborted early: %s\n%s" % (r["died"], json.dumps(r, indent=1)[-2500:])
            return
        cls.result = r

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            try:
                os.kill(k.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _r(self):
        if self.driver_error:
            tail = ""
            try:
                with open(self.klog) as fh:
                    tail = "\nkernel log tail:\n" + fh.read()[-2000:]
            except OSError:
                pass
            self.fail(self.driver_error + tail)
        return self.result

    def test_1_with_its_manager_the_pane_shows_and_persists_the_arrangement(self):
        h = self._r()["healthy"]
        self.assertTrue(h["fed"]); self.assertIsNone(h["banner"]); self.assertIsNone(h["retryMarker"])
        self.assertEqual(sorted(h["K"]), sorted([SID_A, SID_B]), "the kernel's order, whichever way it lists them: %r" % h["K"])
        self.assertEqual(h["sessionList"], h["K"], "the pane's own order holds each id once, the strip's order (the duplicate-id fix): %r" % h["sessionList"])
        self.assertIn(h["arrangementBefore"], (None, h["K"]), "before any drag the arrangement is the adopted seed (or nothing): the strip IS the kernel's order")
        self.assertTrue(h["drag"]["started"], "the synthetic drag's dragstart was not refused on a healthy pane")
        self.assertEqual(h["drag"]["writes"], 1, "…and its drop wrote the arrangement exactly once: %r" % h["drag"])
        self.assertEqual(h["drag"]["order"], list(reversed(h["K"])))
        self.assertEqual(h["arrangement"], list(reversed(h["K"])), "the drag persisted the reverse")
        self.assertEqual(h["stripAfterDrag"], list(reversed(h["K"])))

    def test_2_a_pane_without_its_manager_retries_once_then_says_so_and_shows_the_kernel_seed_undraggable(self):
        r = self._r(); b, K = r["broken"], r["healthy"]["K"]
        self.assertFalse(b["fed"], "no manager in the document: %r" % b)
        self.assertEqual(b["fedRequests"], 2, "the reload and the one retry each asked for the bundle, and nothing looped: %r" % b["fedRequests"])
        self.assertIsNone(b["retryMarker"], "the banner pass consumed the marker with its row, so the next load gets its own retry")
        self.assertIn("failed to load", b["banner"] or ""); self.assertIn("can't be dragged", b["banner"] or "")
        self.assertTrue(b["bannerButton"], "the reload is in hand on the banner")
        self.assertEqual([t["id"] for t in b["strip"]], K, "the strip shows the KERNEL's seed, not the browser's arrangement (the reverse): %r" % b["strip"])
        self.assertTrue(all(t["draggable"] is False for t in b["strip"]), "no tab offers a drag: %r" % b["strip"])
        a = b["bannerAboveStrip"]
        self.assertTrue(a["inFlow"], "the banner is in flow, not fixed over the strip: %r" % a)
        self.assertEqual(a["next"], "tabbar", "…placed right above the strip")
        self.assertLessEqual(a["bBottom"], a["tTop"] + 0.5, "…so the strip starts where the banner ends: %r" % a)
        self.assertTrue(b["hitOnTab"], "the pointer at a tab's centre lands on the tab, not on the banner")
        self.assertEqual(b["clickedActive"], K[1] if b["clickedActive"] == K[1] else K[0], "a plain click selected a tab under the banner")
        self.assertIn(b["clickedActive"], K)

    def test_3_a_drag_in_that_pane_is_refused_at_the_guards_and_never_writes_the_arrangement(self):
        r = self._r(); b, K = r["broken"], r["healthy"]["K"]
        self.assertEqual(b["arrangementBefore"], list(reversed(K)), "the arrangement the healthy pane persisted")
        d = b["drag"]
        self.assertFalse(d["started"], "dragstart is refused (preventDefault) on a page without its manager: %r" % d)
        self.assertEqual(d["writes"], 0, "no write of the arrangement: %r" % d)
        self.assertEqual(d["order"], K, "the pane's order is untouched"); self.assertEqual(d["strip"], K, "…and the strip did not drift locally either")
        self.assertEqual(b["arrangementAfterDrag"], list(reversed(K)), "…so the arrangement is exactly as the healthy pane left it")
        ar = b["afterRename"]
        self.assertFalse(ar["draggable"], "Rename then Escape used to re-arm the tab's drag: it stays refused")
        self.assertFalse(ar["drag"]["started"]); self.assertEqual(ar["drag"]["writes"], 0); self.assertEqual(ar["drag"]["order"], K)
        self.assertEqual(ar["arrangement"], list(reversed(K)))
        s2 = b["secondLoad"]
        self.assertEqual(s2["fedRequests"], 2, "a later load with the bundle still failing gets its OWN single retry (the marker was consumed): %r" % s2)
        self.assertFalse(s2["fed"]); self.assertIsNotNone(s2["banner"]); self.assertIsNone(s2["retryMarker"])

    def _diag_rows(self, want=0):
        """The federation-missing rows the kernel appended — polled up to a bound, since the socket that carried the last
        row may still be flushing when the driver exits (the count is asserted exactly after the poll)."""
        deadline = time.time() + 5
        while True:
            rows = []
            try:
                with open(self.diag) as fh:
                    for ln in fh:
                        try:
                            rec = json.loads(ln)
                        except ValueError:
                            continue
                        if rec.get("what") == "federation-missing":
                            rows.append(rec)
            except OSError:
                rows = []
            if len(rows) >= want or time.time() > deadline:
                return rows
            time.sleep(0.1)

    def test_4_the_kernel_recorded_one_federation_missing_row_per_incident_carrying_the_load_entries(self):
        self._r()
        rows = self._diag_rows(want=3)
        self.assertEqual(len(rows), 3, "one row per incident (two failed loads with the banner, then the self-heal), each filed by the pass that stayed up; the banner's Reload onto a served bundle files nothing: %r" % rows)
        for d in (rows[0]["data"], rows[1]["data"]):
            self.assertNotIn("recovered", d)
            self.assertIsNotNone(d["load"], "the retry's fetch, as the browser recorded it")
            self.assertIsNotNone(d["first"], "…and the first pass's, carried through the marker")
            self.assertEqual((d["load"]["transferSize"], d["first"]["transferSize"]), (0, 0), "an aborted fetch moved no bytes: %r" % d)
        r = rows[2]["data"]
        self.assertTrue(r.get("recovered"), "the self-heal leg's row says the retry fixed it: %r" % r)
        self.assertEqual(r["first"]["transferSize"], 0, "…and carries the failed first fetch: %r" % r)
        self.assertNotIn("load", r)

    def test_4b_a_bundle_that_fails_once_is_healed_by_the_single_retry_with_no_banner(self):
        r = self._r(); h, K = r["recovered"], r["healthy"]["K"]
        self.assertTrue(h["fed"], "the retry's load brought the manager: %r" % h)
        self.assertIsNone(h["banner"], "no banner: the retry fixed it")
        self.assertIsNone(h["retryMarker"], "the marker clears on the healthy boot, so the next failure gets its own retry")
        self.assertEqual(h["fedRequests"], 2, "the failed fetch and the retry's, and nothing more: %r" % h["fedRequests"])
        self.assertEqual(h["strip"], list(reversed(K)), "arranged, as a healthy pane is")

    def test_4c_the_banner_s_reload_onto_a_served_bundle_files_no_recovered_row(self):
        # the marker went with the banner's row, so the healthy boot that follows has no incident to report — rows are per
        # incident (test_4 counts exactly three); this method exists to name that rule
        self._r()
        self.assertEqual(sum(1 for r in self._diag_rows(want=3) if r["data"].get("recovered")), 1, "the one recovered row is the self-heal leg's")

    def test_5_the_banner_s_reload_heals_the_pane_which_shows_the_arrangement_again(self):
        r = self._r(); h, K = r["healed"], r["healthy"]["K"]
        self.assertTrue(h["fed"]); self.assertIsNone(h["banner"]); self.assertIsNone(h["retryMarker"], "a healthy boot clears the marker")
        self.assertEqual(h["strip"], list(reversed(K)), "the browser's arrangement, not the kernel's seed: %r" % h["strip"])
        self.assertEqual(h["arrangement"], list(reversed(K)))

    def test_6_a_session_that_arrives_while_the_pane_is_open_is_persisted_once_by_a_drag(self):
        t = self._r()["third"]
        self.assertEqual(t["before"][-1], SID_C, "the newcomer lands at the end of the strip: %r" % t["before"])
        self.assertEqual(sorted(t["arrangement"]), sorted([SID_A, SID_B, SID_C]), "every id once: %r" % t["arrangement"])
        self.assertNotEqual(t["arrangement"][-1], SID_C, "the drag moved it off the end")
        self.assertEqual(len(t["sessionList"]), len(set(t["sessionList"])), "the pane's own list holds each id once: %r" % t["sessionList"])
        self.assertEqual(t["strip"], t["arrangement"])


class FedMissingLabKernelEnv(unittest.TestCase):
    """The lab kernel's environment is built from a list of names (kernel_env in test_ship_reship.py, the function
    every lab that boots a kernel uses), never from a copy of the runner's. A run from a shell on a machine running
    romp carries the live kernel's exports, and a lab kernel that inherited them exited when the live manager
    restarted (ROMP_MANAGER_PID, the kernel's parent-death watchdog), bound where the live kernel serves
    (ROMP_SERVE_HOST) and, with no ROMP_POSTAL_PORT of its own, dialled the machine's postal bus. No kernel and no
    browser, so this runs everywhere the served leg above skips."""

    LAB = os.path.join(os.sep, "lab")
    # a live kernel's exports as a session's shell carries them, the run's own state root, an auth declaration and a
    # stand-in for a key the shell carries: planted here so the case asserts on names it chose rather than on what
    # the runner happened to export (conftest scrubs some of these before every test)
    LIVE = {"ROMP_MANAGER_PID": "4242", "ROMP_SERVE_HOST": "0.0.0.0",
            "ROMP_SID": "cccccccc-1111-2222-3333-444444444444", "ROMP_SESSION_NAME": "web",
            "ROMP_STATE_DIR": os.path.join(LAB, "live"), "ROMP_EXPECTED_AUTH": "key", "RUNNER_SECRET_PROBE": "abc"}
    # what the lab itself puts in: its roots, the serve seams, and a postal bus of its own
    OWN = {"XDG_STATE_HOME": os.path.join(LAB, "xdg"), "CLAUDE_CONFIG_DIR": os.path.join(LAB, "claude"),
           "ROMP_MANAGER_PORT": "1", "ROMP_KERNEL_NO_OPEN": "1", "ROMP_SERVE_TOKEN": "testtok",
           "ROMP_KERNEL_PORT": "4321", "ROMP_DIST_DIR": os.path.join(LAB, "dist"), "ROMP_MODEL_CATALOG": "off",
           "ROMP_POSTAL_PEERS": "0", "ROMP_POSTAL_CLIENT_ONLY": "1"}
    # the port a kernel with no ROMP_POSTAL_PORT of its own dials: the machine's bus
    MACHINE_BUS_PORT = "25302"

    def test_a_live_kernels_exports_never_reach_the_lab_kernel(self):
        with mock.patch.dict(os.environ, self.LIVE):
            os.environ.pop("ROMP_POSTAL_PORT", None)   # the runner names no bus: the lab must still get one of its own
            env = lab_kernel_env(self.LAB, os.path.join(self.LAB, "claude"), os.path.join(self.LAB, "dist"), 4321,
                                 "testtok")
        names = sorted(env)   # the names alone: a failure reports the leaked name, not the runner's values
        for name in self.LIVE:
            self.assertNotIn(name, names, "a live kernel's %s reached the lab kernel" % name)
        for name, value in self.OWN.items():
            self.assertEqual(env.get(name), value, "the lab's own %s" % name)
        bus = env.get("ROMP_POSTAL_PORT", "")
        self.assertTrue(bus.isdigit(), "a postal bus port of the lab's own: %r" % bus)
        self.assertNotEqual(bus, self.MACHINE_BUS_PORT, "never the machine's bus")
        for name in ("PATH", "HOME"):
            self.assertIn(name, names, "the runner's %s reaches the lab kernel" % name)


if __name__ == "__main__":
    unittest.main()
