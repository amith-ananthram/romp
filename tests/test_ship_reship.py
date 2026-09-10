#!/usr/bin/env python3
"""T215: a kernel restart between an attachment's ship and its ack must not wedge the upload.

The wedge (verified on main before the fix): file bytes ride the ws as base64 dropFile and
complete on a droppedPath ack sent back on the SAME socket. pendingShips is in-memory and
nothing on romp:wsup ever re-shipped or failed it — so a kernel restart in the ship→ack window
left the chip pulsing forever, and a send held by the ship gate ("Wait for the upload") waited
on an ack that could no longer arrive: message plus attachment parked with no error.

The fix, both faces:
  * same-page reconnect (the served dashboard): every pending entry retains its encoded payload
    and re-ships on romp:wsup — the exact kernel-is-back event, never a timer. The ack/nack
    echoes the client's shipId, so a duplicate ack from a re-ship race retires exactly the chip
    that asked, and a stray twin is DROPPED instead of attached to the active tab.
  * reload (the VS Code pipe reloads its webview on kernel reconnect): the payload dies with
    the page, so the ship NAMES persist beside the drafts and the next load says LOUDLY what
    was lost — never a silent vanish.

Two guards here:
  * SourcePins — runs everywhere, CI included: the wiring above, pinned in the sources (the
    webview-side twins live in ui/webview/pending-attach.test.ts).
  * ServedWedge — the executed guard: boots the hermetic kernel, opens the real /chat page,
    SIGSTOPs the kernel so the dropFile is shipped on a live socket that will never answer,
    holds a send behind the ship gate, SIGKILLs and relaunches the kernel — and asserts the
    reconnect re-ships, the thumbnail lands, and the held send fires. Plus the regression leg:
    a normal ship+send against the restarted kernel behaves exactly as before. Skips LOUDLY
    when the extension deps or a playwright browser are absent (CI installs no browsers).

All fixtures synthetic.
"""
import base64
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")

RENDER = open(os.path.join(ROOT, "ui", "webview", "render.ts")).read()
KERNEL_SRC = open(os.path.join(BIN, "romp-kernel")).read()

SID = "aaaaaaaa-1111-2222-3333-444444444444"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")


class SourcePins(unittest.TestCase):
    def test_payload_retained_and_reshipped_on_the_reconnect_event(self):
        self.assertIn("interface PendingShip { name: string; shipId: string; b64?: string }", RENDER)
        self.assertIn("if (entry) entry.b64 = b64;", RENDER)
        # the listener grew a body (T246: the local active-tab re-arm rides the same open event); the re-ship
        # is still its first statement
        self.assertIn('window.addEventListener("romp:wsup", () => {\n  reshipPendingUploads();', RENDER)
        # …and the federated twin (review finding 2026-09-01): the relay's own (re)open re-ships THAT
        # host's entries — scoped by ack socket. The kernel-reported hostUp does NOT: it fires in the
        # tick federation re-dials the relay, before the socket is open (second review, same day)
        self.assertIn('window.addEventListener("romp:hostRelayUp", (e) => {', RENDER)
        self.assertNotIn("reshipPendingUploads(m.hosts", RENDER)

    def test_ack_echoes_shipid_and_a_stray_twin_is_dropped(self):
        self.assertIn('ack["shipId"] = str(msg["shipId"])', KERNEL_SRC)
        self.assertIn("if (ackShip && !shipOwner(ackShip)) return;", RENDER)

    def test_the_pane_holds_the_dashboards_reload_while_a_ship_or_a_held_send_is_in_flight(self):
        # T272 (2026-09-08): the dashboard reloads itself on a kernel restart (T265), and a reload costs an upload in
        # flight its bytes and a held send its release — the very wedge the reconnect re-ship heals in the same page.
        # So the chat pane answers the reload core's busy ask while either is pending; the shim's own reasons first.
        self.assertIn('(window as any).__rompPaneBusy = (): string => {', RENDER)
        self.assertIn('const shimBusy = (window as any).__rompPaneBusy as (() => string) | undefined;', RENDER)
        # …and only for ships whose ack can still arrive (reload-hold.ts, the review of the hold): a ship to a host whose
        # relay is down, or to a host no longer attached, does not hold the dashboard's reload
        self.assertIn('return reloadHoldReason([...pendingShips.keys()], shipGateSid, (window as any).__rompFed);', RENDER)
        self.assertNotIn('if (pendingShips.size) return "upload";', RENDER)

    def test_the_chat_page_loads_the_shim_before_the_bundle_so_the_busy_report_wraps_the_shims(self):
        # the wrapper above reads the shim's window.__rompPaneBusy first and replaces it; that only holds because the
        # chat page emits the shim inline BEFORE <script src=/dist/render.js> — the other order would let the shim's
        # own assignment overwrite the wrapper and drop the upload and held-send reasons with every pin above green
        src = open(os.path.join(ROOT, "kernel", "kernel.py"), encoding="utf-8").read()
        page = src[src.index("def _chat_page():"):src.index("\ndef ", src.index("def _chat_page():") + 10)]
        self.assertLess(page.index("<script>%s</script>"), page.index("/dist/render.js"), "the shim's script precedes the bundle in the chat page")
        self.assertIn('_shim("chat", v)', page)
        self.assertIn('window.__rompPaneBusy=function(){return (everConnected&&queue.length>queuedDiag)?"sends":"";};', src,
                      "the shim defines the hook the pane wraps")

    def test_a_reload_loss_is_loud_never_a_silent_vanish(self):
        self.assertIn("shipsInFlight: [...pendingShips.values()].flat().map((p) => p.name)", RENDER)
        self.assertIn("still uploading when this page reloaded, so it was NOT attached", RENDER)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
import { spawn } from "node:child_process";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
const out = { wedge: {}, regression: {} };
const die = async (why) => {
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n");
  await browser.close();
  process.exit(0);
};

// ---- wedge: SIGSTOP the kernel so the ship rides a live socket that will never answer ----
await page.goto(cfg.url);
await page.waitForSelector("#composer-input", { timeout: 20000 });
const tab = await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 }).catch(() => null);
if (!tab) await die("no session tab — the lab seed never reached the chat payload");
await page.waitForTimeout(500);   // let the shim's ws settle onto the live kernel
process.kill(cfg.kernelPid, "SIGSTOP");
await page.setInputFiles("body > input[type=file]", cfg.file);
await page.waitForSelector(".composer-file-pending", { timeout: 10000 }).catch(() => {});
out.wedge.chipUpAfterShip = await page.locator(".composer-file-pending").count();
await page.fill("#composer-input", cfg.msg);
await page.click("#composer-send");
// the ship gate: hold the send on the upload, the event this bug starved forever
await page.waitForSelector(".confirm-btn", { timeout: 10000 }).catch(() => {});
const waitBtn = page.locator(".confirm-btn", { hasText: "Wait for the upload" });
out.wedge.gateOffered = await waitBtn.count();
if (out.wedge.gateOffered) await waitBtn.click();
out.wedge.inputHeld = await page.inputValue("#composer-input");
out.wedge.chipStillPendingHeld = await page.locator(".composer-file-pending").count();
// ---- the restart: the old socket dies with the ack still owed; a fresh kernel takes the port ----
process.kill(cfg.kernelPid, "SIGKILL");
const k2 = spawn(cfg.relaunch.cmd, [], { env: cfg.relaunch.env, detached: true,
  stdio: ["ignore", fs.openSync(cfg.relaunch.log, "a"), fs.openSync(cfg.relaunch.log, "a")] });
k2.unref();   // the kernel outlives this driver — an un-unref'd child held node open past RESULT
fs.writeSync(1, "KPID:" + k2.pid + "\n");
// T272: the restart's reload request normally rides the next keepalive (its cadence, not this test's); it is raised
// HERE, while the ship is pending and the send held, so the hold is exercised on every run: on a pane without the
// busy report the page reloads at once and the wait below dies (reloadedEarly), which is the failure this pins. The
// probe marks THIS page: its disappearance is the reload firing on its own once the pane is idle again.
await page.evaluate(() => { window.__probe = 1; const R = window.__rompReload; if (R) R.request("restart", "forced-by-the-test"); }).catch(() => {});
// today (pre-fix) this wait dies: the chip pulses forever and the held send never fires.
// with the fix: romp:wsup re-ships, the ack retires the chip, and fireHeldSend sends the message.
// T272: the dashboard reloads itself on the restart (a new boot id, T265) — but only once this pane is no longer
// busy: the pending ship and the held send hold the reload (render.ts __rompPaneBusy) until the ack lands and the
// send fires, all on THIS page. A reload before that would take the upload's bytes with it (the loss toast) and
// leave the send unfired: an early navigation here is the failure this test exists for, so it is recorded, never
// swallowed.
let reloadedEarly = false;
// the heal is measured INSIDE the wait, on the page that healed: the reload the restart owes is let through the
// instant the pane is idle again (the last ack and the held send's release tell the core, endReloadHoldIfIdle), so a
// measurement taken a poll later may find a fresh page. The predicate also records that the reload was owed and
// WAITING while the pane was busy (window.__rompReload.owed() && !fired() with the chip still pending) — the held
// reload this fix is for.
const heal = await page.waitForFunction((msg) => {
  const R = window.__rompReload;
  const input = document.getElementById("composer-input");
  const pending = document.querySelectorAll(".composer-file-pending").length;
  const content = document.getElementById("content");
  if (R && R.owed() && !R.fired() && pending > 0) window.__t272HeldWhileBusy = String(R.waiting || "busy");
  const ok = pending === 0 && !!input && input.value === "" && !!content && content.textContent.includes(msg);
  return ok ? JSON.stringify({ pending, input: input.value, hasMsg: true, held: window.__t272HeldWhileBusy || null,
                               owedNow: !!(R && R.owed()), firedNow: !!(R && R.fired()) }) : false;
}, cfg.msg, { timeout: 45000 }).then(async (h) => JSON.parse(await h.jsonValue()))
  .catch((e) => { if (/context was destroyed|navigation/i.test(String(e))) reloadedEarly = true; return null; });
out.wedge.healedAfterRestart = !!heal;
out.wedge.reloadedEarly = reloadedEarly;
out.wedge.pendingAfterRestart = heal ? heal.pending : -1;
out.wedge.inputAfterRestart = heal ? heal.input : null;
out.wedge.contentHasMsg = heal ? heal.hasMsg : false;
out.wedge.reloadHeldWhileBusy = heal ? heal.held : null;
out.wedge.reloadOwedAfterHeal = heal ? heal.owedNow : null;
// now the pane is idle: the owed reload fires ON ITS OWN (the ending event, never a nudge from this driver) — wait for
// the page to go, then follow it
out.wedge.reloadFiredAfterHeal = await page.waitForFunction(() => window.__probe !== 1, null, { timeout: 20000 }).then(() => true).catch(() => false);
await page.waitForLoadState("load").catch(() => {});
await page.waitForSelector("#composer-input", { timeout: 20000 });
await page.waitForTimeout(500);
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-wedge.png" });

// ---- regression: a normal ship+send against the restarted kernel, untouched ----
await page.reload();
await page.waitForSelector("#composer-input", { timeout: 20000 });
await page.waitForTimeout(1000);
// the reload must NOT cry ship-loss: the wedge's ships all settled before it
out.regression.lossToast = await page.evaluate(
  () => (document.getElementById("warn-toasts")?.textContent || "").includes("still uploading"));
await page.setInputFiles("body > input[type=file]", cfg.file);
const acked = await page.waitForFunction(() =>
  document.querySelectorAll(".composer-file-pending").length === 0 &&
  document.querySelectorAll(".composer-file").length > 0, { timeout: 15000 })
  .then(() => true).catch(() => false);
out.regression.thumbnailLanded = acked;
await page.fill("#composer-input", cfg.msg2);
await page.click("#composer-send");
out.regression.gateOpened = await page.locator(".confirm-btn").count();   // no pending ships → no gate
const sent = await page.waitForFunction((msg) => {
  const input = document.getElementById("composer-input");
  return input && input.value === "" &&
         (document.getElementById("content")?.textContent || "").includes(msg);
}, cfg.msg2, { timeout: 15000 }).then(() => true).catch(() => false);
out.regression.sentClean = sent;
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-regression.png" });

fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");   // sync: exit must not truncate it
await browser.close();
process.exit(0);
"""


class _ShipLab(unittest.TestCase):
    """Shared hermetic lab: one kernel, one seeded SDK session, the real /chat page. Subclasses
    carry the choreography; each gets its own kernel (setUpClass per class)."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served wedge needs them")
        cls.lab = tempfile.mkdtemp(prefix="ship-reship-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        os.makedirs(os.path.join(cls.state, "names"), exist_ok=True)
        os.makedirs(os.path.join(cls.state, "sdk"), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        # one synthetic SDK session so the chat page has a tab and a composer to ship into
        Path(cls.state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto",
             "effort": "high", "lastSid": SID, "alive": True}))
        Path(cls.state, "usage.json").write_text(json.dumps(
            {"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))  # park sends: no CLI spawns in the lab
        claude = os.path.join(cls.lab, "claude")
        # the kernel finds a session's transcript under Claude's project dir: EVERY non-alphanumeric char of the
        # realpath becomes '-' (jd._proj_dir). A slashes-only munge missed the '_' pytest's temp root can carry,
        # so the kernel found no transcript and drew an API-error turn instead (the batch-only flake, 2026-09-08).
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # a CLOSED turn (user + replied assistant): an OPEN one would invite the boot
        # reconcile to resume it — this lab must never spawn a real CLI
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-555555555555",
                        "parentUuid": None, "timestamp": "2026-09-01T00:00:00.000Z",
                        "sessionId": SID,
                        "message": {"role": "user", "content": "hello there"}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": "22222222-3333-4444-5555-666666666666",
                        "parentUuid": "11111111-2222-3333-4444-555555555555",
                        "timestamp": "2026-09-01T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-sonnet-5",
                                    "content": [{"type": "text", "text": "hi from the lab"}],
                                    "stop_reason": "end_turn"}}) + "\n")
        cls.png = os.path.join(cls.lab, "shot.png")
        Path(cls.png).write_bytes(PNG)
        cls.port = _free_port()
        cls.token = "testtok-reship"
        cls.env = dict(os.environ,
                       XDG_STATE_HOME=os.path.join(cls.lab, "xdg"),
                       CLAUDE_CONFIG_DIR=claude,
                       ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1",
                       ROMP_SERVE_TOKEN=cls.token, ROMP_KERNEL_PORT=str(cls.port),
                       ROMP_DIST_DIR=dist,
                       ROMP_MODEL_CATALOG="off")   # hermetic: the T222 catalog fetch must never reach the network
        cls.env.pop("ROMP_STATE_DIR", None)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=cls.env)
        cls.kernel2_pid = None
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
        for pid in [getattr(cls, "kernel", None) and cls.kernel.pid, getattr(cls, "kernel2_pid", None)]:
            if pid:
                try:
                    os.kill(pid, signal.SIGCONT)   # a SIGSTOPped kernel ignores SIGTERM until resumed
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if getattr(cls, "kernel", None):
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _run_driver(self, driver_src, cfg_obj, timeout=300):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump(cfg_obj, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(driver_src)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=timeout,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            so = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            se = (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            # the driver may have relaunched the lab kernel before hanging — reap it via tearDownClass
            # (review finding 2026-09-01: this path leaked the detached replacement kernel)
            kpid = next((ln for ln in so.splitlines() if ln.startswith("KPID:")), None)
            if kpid:
                type(self).kernel2_pid = int(kpid.split(":", 1)[1])
            self.fail("driver timed out; partial output:\n%s\n%s" % (so, se))
            return None
        kpid = next((ln for ln in p.stdout.splitlines() if ln.startswith("KPID:")), None)
        if kpid:
            type(self).kernel2_pid = int(kpid.split(":", 1)[1])
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served leg needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertNotIn("died", r, "driver aborted early: %r" % r)
        return r


class ServedWedge(_ShipLab):
    def test_restart_between_ship_and_ack_reships_heals_and_releases_the_held_send(self):
        r = self._run_driver(DRIVER, {
            "url": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
            "kernelPid": self.kernel.pid,
            "relaunch": {"cmd": os.path.join(BIN, "romp-kernel"),
                         "env": {k: v for k, v in self.env.items()}, "log": self.klog},
            "file": self.png, "msg": "hold this message for the upload T215",
            "msg2": "a normal send after the restart T215",
            "shots": os.environ.get("SHIP_RESHIP_SHOTS", "")})
        w, reg = r["wedge"], r["regression"]
        # the ship went out on a live socket the stopped kernel will never answer
        self.assertEqual(w["chipUpAfterShip"], 1, "the pending chip must be up after the ship: %r" % w)
        self.assertEqual(w["gateOffered"], 1, "the ship gate must offer to wait for the upload: %r" % w)
        self.assertEqual(w["inputHeld"], "hold this message for the upload T215",
                         "the held send keeps the composer text until the upload settles: %r" % w)
        self.assertEqual(w["chipStillPendingHeld"], 1, "the chip is honestly pending while held: %r" % w)
        # the heart of T215: after the restart the reconnect re-ships, the ack lands, the send fires
        self.assertFalse(w.get("reloadedEarly"), "the dashboard's own reload on the restart must WAIT for the pending ship and the "
                                                  "held send (T272): a reload first would lose the upload's bytes and leave the send unfired: %r" % w)
        self.assertTrue(w["healedAfterRestart"],
                        "the reconnect must re-ship and release the held send — pre-fix this pulses "
                        "forever and the send never fires: %r (kernel log tail: %s)"
                        % (w, Path(self.klog).read_text()[-500:]))
        self.assertIn(w.get("reloadHeldWhileBusy"), ("upload", "held-send", "sends"),
                      "the restart's reload was owed and WAITING on this pane while the ship and the held send were in flight: %r" % w)
        self.assertTrue(w.get("reloadFiredAfterHeal"), "…and fired on its own once the ack landed and the send left (the ending event, "
                                                       "render.ts endReloadHoldIfIdle) — never waiting for the user's next click: %r" % w)
        self.assertEqual(w["pendingAfterRestart"], 0, "no chip may pulse over an upload that settled: %r" % w)
        self.assertEqual(w["inputAfterRestart"], "", "the held send must have fired: %r" % w)
        self.assertTrue(w["contentHasMsg"], "the sent message must be in the transcript view: %r" % w)
        # regression: the restarted kernel serves a NORMAL ship+send exactly as before
        self.assertFalse(reg["lossToast"], "a clean reload must not cry ship-loss: %r" % reg)
        self.assertTrue(reg["thumbnailLanded"], "normal ship: chip → thumbnail on the ack: %r" % reg)
        self.assertEqual(reg["gateOpened"], 0, "no pending ships → no gate: %r" % reg)
        self.assertTrue(reg["sentClean"], "a plain send still clears and lands: %r" % reg)


DRIVER_RELOAD = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
const out = {};
const die = async (why) => {
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n");
  await browser.close();
  process.exit(0);
};
await page.goto(cfg.url);
await page.waitForSelector("#composer-input", { timeout: 20000 });
const tab = await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 }).catch(() => null);
if (!tab) await die("no session tab — the lab seed never reached the chat payload");
await page.waitForTimeout(500);
// stop the kernel so the ship's ack cannot land, then WALK AWAY mid-flight: the loss shape.
// No restart needed — determinism comes from the page dying before any ack or re-ship can settle.
process.kill(cfg.kernelPid, "SIGSTOP");
await page.setInputFiles("body > input[type=file]", cfg.file);
await page.waitForSelector(".composer-file-pending", { timeout: 10000 }).catch(() => {});
out.chipUp = await page.locator(".composer-file-pending").count();
await page.goto("about:blank");            // the page dies with the ship pending — no client left to ack
process.kill(cfg.kernelPid, "SIGCONT");    // the buffered dropFile answers a dead socket, harmlessly
// load 1: the persisted names must warn LOUDLY — and clear
await page.goto(cfg.url);
await page.waitForSelector("#composer-input", { timeout: 20000 });
await page.waitForTimeout(800);
out.toastOnFirstLoad = await page.evaluate(
  () => (document.getElementById("warn-toasts")?.textContent || "").includes("still uploading"));
// load 2: silence — pre-fix, persistDrafts' swallowed TDZ throw left the record, re-toasting forever
await page.goto(cfg.url);
await page.waitForSelector("#composer-input", { timeout: 20000 });
await page.waitForTimeout(800);
out.toastOnSecondLoad = await page.evaluate(
  () => (document.getElementById("warn-toasts")?.textContent || "").includes("still uploading"));
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ReloadLossToast(_ShipLab):
    """The reload face, executed: a ship lost to a page death warns ONCE at the next load, then the
    record clears. Red on the pre-fix tree: the startup clear rode persistDrafts, whose stagedMsgs
    read sits below the restore block — the TDZ throw died in persistDrafts' own catch, the clear
    silently never ran, and the toast re-fired on every load (review finding 2026-09-01, verified
    on the emitted bundle)."""

    def test_reload_loss_toasts_once_then_clears(self):
        r = self._run_driver(DRIVER_RELOAD, {
            "url": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
            "kernelPid": self.kernel.pid, "file": self.png})
        self.assertEqual(r["chipUp"], 1, "the ship must be pending when the page dies: %r" % r)
        self.assertTrue(r["toastOnFirstLoad"],
                        "the lost upload must warn loudly on the next load — never a silent vanish: %r" % r)
        self.assertFalse(r["toastOnSecondLoad"],
                         "the loss record must clear with the toast — one warning, not one per load: %r" % r)


if __name__ == "__main__":
    unittest.main()
