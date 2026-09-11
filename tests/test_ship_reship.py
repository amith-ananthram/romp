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

The guards here:
  * SourcePins — runs everywhere, CI included: the wiring above, pinned in the sources (the
    webview-side twins live in ui/webview/pending-attach.test.ts).
  * ServedWedge — the executed guard: boots the hermetic kernel, opens the real /chat page,
    SIGSTOPs the kernel so the dropFile is shipped on a live socket that will never answer,
    holds a send behind the ship gate, SIGKILLs and relaunches the kernel — and asserts the
    reconnect re-ships, the thumbnail lands, and the held send fires. Plus the regression leg:
    a normal ship+send against the restarted kernel behaves exactly as before. Skips LOUDLY
    when the extension deps or a playwright browser are absent (CI installs no browsers).
  * NackNoticeSurvivesReload: the same wedge with the relaunched kernel unable to save the
    re-shipped file. Its nack retires the last pending ship, which lets the restart's held
    reload fire on the next task, and the notice the nack raised (the file was not saved, the
    held message not sent) must be shown again by the fresh page, once.
  * LabKernelEnv, which runs everywhere: a lab kernel's environment is built from names (kernel_env,
    which every lab that boots a kernel uses), never from a copy of the runner's: a live kernel's
    exports planted in the runner (ROMP_MANAGER_PID, ROMP_SERVE_HOST, its sid) are absent, the run's
    floor and the lab's own names present, and the lab kernel's postal bus is a port of its own that
    is never started.
  * RelaunchEnv, which runs everywhere: the relaunch stanza the lab writes to its cfg.json for the
    driver's relaunch of the kernel carries only the names the relaunched kernel needs (a name
    planted in the lab kernel's environment as a probe is absent, and so is a ROMP_TESTS_ name, the
    run's own, wherever it came from; a live session's identity and manager pid in the runner reach
    neither the lab kernel nor the file; the lab's own names, the run's private roots and its git
    isolation present); the served legs check the written file itself.

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
from unittest import mock

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

    def test_the_notices_on_screen_ride_the_cores_reload_and_are_shown_once(self):
        # the nack is raised in the same task as the reload hold's ending event, just after endReloadHoldIfIdle (the
        # dismissal of the last pending chip and the ack landing on another tab, just before it), one task before the
        # owed reload fires, so the toast was never read and the loss toast above has nothing to say (shipsInFlight is
        # already empty). The core's synchronous hook keeps the toasts on screen in this tab's sessionStorage and the
        # fresh page shows them once, after the loss toast (reload-notices.ts); pagehide keeps the scroll record alone,
        # so a load the user asks for replays nothing. NackNoticeSurvivesReload executes the nack's.
        self.assertIn("(window as any).__rompPersistForReload = persistForReload;", RENDER)
        self.assertIn("function persistForReload(): void { persistScrollForReload(); persistNoticesForReload(); }", RENDER)
        self.assertIn('window.addEventListener("pagehide", persistScrollForReload);', RENDER)
        self.assertIn('keepReloadNotices(sessionStorage, liveNotices(document.getElementById("warn-toasts")))', RENDER)
        self.assertIn("for (const text of takeReloadNotices(sessionStorage)) warnToast(text);", RENDER)
        self.assertLess(RENDER.index("shipsInFlight: [] });"), RENDER.index("takeReloadNotices(sessionStorage)"),
                        "the loss toast first, then what the last page was saying")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# A lab kernel's environment is built from names (kernel_env), never from a copy of the runner's. A run from a
# session on a machine running romp carries the live kernel's exports, and a lab kernel that inherited them exited
# when the live manager restarted (ROMP_MANAGER_PID, which kernel.py's _parent_watch reads), bound where the live
# kernel serves (ROMP_SERVE_HOST) and, with no ROMP_POSTAL_PORT of its own, dialled the machine's postal bus at boot
# (kernel.py's _ensure_postal_bus), or on a machine with none started a detached bus nothing stops. From the runner a
# lab kernel takes PATH (bin/romp-kernel runs under `env python3`) and HOME, the XDG_* names (kernel/credentials.py
# resolves the service.env default under XDG_CONFIG_HOME) and, of what tests/conftest.py sets for every child of the
# run, TMPDIR and TMUX_TMPDIR, the private roots conftest gives the run's temp files, GIT_CONFIG_GLOBAL and
# GIT_CONFIG_NOSYSTEM, which keep the kernel's boot-time git (the build sha, the release-tag probe) off the
# developer's git configuration, and the four ROMP_ names of its floor the lab does not set itself:
# ROMP_SERVICE_ENV_FILE and ROMP_SERVICE_ENV (no real service.env), ROMP_CLAUDE_BIN (no real claude CLI) and
# ROMP_CLI_SCOPE (no systemd-run). The lab's own names go over those, and a postal bus of its own: ROMP_POSTAL_PORT
# at a free port with ROMP_POSTAL_PEERS=0 and ROMP_POSTAL_CLIENT_ONLY=1, so the kernel's boot-time ensure starts
# nothing there (client-only applies in the legacy singleton scheme alone, postal_service.is_client_only).
# The environment the driver hands the kernel it relaunches rides the lab's cfg.json, a file, so it is narrowed once
# more (relaunch_env) to the ROMP_* and XDG_* names, CLAUDE_CONFIG_DIR, PATH and HOME and the four conftest names:
# never anything else a lab put in its kernel's environment, such as the probe the served legs plant, and never a
# ROMP_TESTS_* name, the prefix tests/conftest.py exports the run's own names under (ROMP_TESTS_SYSTEM_TMPDIR): no
# kernel reads one, so a lab kernel's environment carries one only if the lab put it there, and the file never does.
RELAUNCH_ENV_PREFIXES = ("ROMP_", "XDG_")
RELAUNCH_ENV_EXCLUDED_PREFIXES = ("ROMP_TESTS_",)
RELAUNCH_ENV_NAMES = frozenset(("CLAUDE_CONFIG_DIR", "PATH", "HOME", "TMPDIR", "TMUX_TMPDIR",
                                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"))
KERNEL_ENV_NAMES = RELAUNCH_ENV_NAMES | frozenset(("ROMP_SERVICE_ENV_FILE", "ROMP_SERVICE_ENV",
                                                    "ROMP_CLAUDE_BIN", "ROMP_CLI_SCOPE"))


def kernel_env(lab, claude, dist, port, token, **seams):
    """A lab kernel's environment: the runner's KERNEL_ENV_NAMES and XDG_* names; over them the lab's roots
    (XDG_STATE_HOME under `lab`, CLAUDE_CONFIG_DIR `claude`, ROMP_DIST_DIR `dist`), its serve `port` and `token`, the
    seams every lab kernel runs with, a postal bus of its own that is never started, and any `seams` the lab adds
    by name. The kernel the lab starts gets it by process; the driver's relaunch gets relaunch_env() of it, through
    the stanza relaunch_cfg() writes to the lab's cfg.json."""
    env = {k: v for k, v in os.environ.items() if k in KERNEL_ENV_NAMES or k.startswith("XDG_")}
    env.update(XDG_STATE_HOME=os.path.join(lab, "xdg"), CLAUDE_CONFIG_DIR=claude,
               ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1",
               ROMP_SERVE_TOKEN=token, ROMP_KERNEL_PORT=str(port),
               ROMP_DIST_DIR=dist,
               ROMP_MODEL_CATALOG="off",     # hermetic: the T222 catalog fetch must never reach the network
               ROMP_POSTAL_PORT=str(_free_port()), ROMP_POSTAL_PEERS="0", ROMP_POSTAL_CLIENT_ONLY="1",
               ROMP_POSTAL_HERMETIC="1")   # the port above is this run's own: the bus honours it under a test (2026-09-11)
    env.update(seams)
    return env


def relaunch_env(env):
    """The part of a lab kernel's environment `env` that a served lab writes to its cfg.json for the driver's
    relaunch of the kernel: the RELAUNCH_ENV_PREFIXES names and RELAUNCH_ENV_NAMES, less the ROMP_TESTS_* names
    (RELAUNCH_ENV_EXCLUDED_PREFIXES), which are the run's own and which no kernel reads."""
    return {k: v for k, v in env.items()
            if (k.startswith(RELAUNCH_ENV_PREFIXES) or k in RELAUNCH_ENV_NAMES)
            and not k.startswith(RELAUNCH_ENV_EXCLUDED_PREFIXES)}


def relaunch_cfg(env, klog):
    """cfg.relaunch, the stanza a served lab writes for the driver's relaunch of the kernel it kills: the command,
    relaunch_env() of the lab kernel's environment `env`, and the log `klog` the first kernel writes. Both served
    labs write their stanza through this, so RelaunchEnv covers what reaches the file."""
    return {"cmd": os.path.join(BIN, "romp-kernel"), "env": relaunch_env(env), "log": klog}


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
        cls.env = kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        # a name outside the relaunch list, planted in the lab kernel's environment so _run_driver's check on the
        # cfg.json the driver reads has something the file must not carry
        cls.env["RUNNER_SECRET_PROBE"] = "abc"
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
        # the file carries only what the driver and the relaunched kernel need: the probe the lab planted in its
        # kernel's environment (checked first, so the file check cannot pass without it) must not be in it (names
        # only in the report, never the values)
        self.assertIn("RUNNER_SECRET_PROBE", sorted(self.env), "the lab plants the probe in its kernel's environment")
        written = json.loads(Path(cfg).read_text(encoding="utf-8"))
        self.assertNotIn("RUNNER_SECRET_PROBE", sorted(written.get("relaunch", {}).get("env", {})),
                         "the lab's cfg.json carries a name of the lab kernel's environment outside the relaunch list")
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


class RelaunchEnv(unittest.TestCase):
    """The relaunch stanza a served lab writes to its cfg.json for the driver's relaunch of the kernel carries only
    the names the relaunched kernel needs, never anything else a lab put in its kernel's environment: the file
    holds it for the run. No kernel and no browser, so this runs everywhere; the served legs check the written file
    itself (_run_driver)."""

    def test_a_planted_name_never_reaches_the_relaunch_env_and_the_kernels_names_do(self):
        lab = os.path.join(os.sep, "lab")
        # the runner's shell: a live kernel's state export (it outranks the XDG root; kernel_env never takes it) and
        # the floor tests/conftest.py sets for the run's children, planted here so the test asserts on values it
        # chose rather than on conftest having set them
        runner = {"ROMP_STATE_DIR": os.path.join(lab, "live"),
                  "TMPDIR": os.path.join(lab, "tmp"), "TMUX_TMPDIR": os.path.join(lab, "tmux"),
                  "GIT_CONFIG_GLOBAL": os.path.join(lab, "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        with mock.patch.dict(os.environ, runner):
            env = kernel_env(lab, os.path.join(lab, "claude"), os.path.join(lab, "dist"), 4321, "testtok")
        env["RUNNER_SECRET_PROBE"] = "abc"       # a name outside the relaunch list, planted as the served labs do
        cfg = relaunch_cfg(env, os.path.join(lab, "kernel.log"))
        self.assertEqual(cfg["cmd"], os.path.join(BIN, "romp-kernel"))
        self.assertEqual(cfg["log"], os.path.join(lab, "kernel.log"))
        out = cfg["env"]
        names = sorted(out)
        self.assertNotIn("RUNNER_SECRET_PROBE", names, "the relaunch reads the file: a name outside the list must not be in it")
        self.assertNotIn("ROMP_STATE_DIR", names, "a live kernel's export outranks the XDG root; kernel_env never takes it")
        for name in ("XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "ROMP_MANAGER_PORT", "ROMP_KERNEL_NO_OPEN", "ROMP_SERVE_TOKEN",
                     "ROMP_KERNEL_PORT", "ROMP_DIST_DIR", "ROMP_MODEL_CATALOG", "PATH", "HOME"):
            self.assertIn(name, names, "the relaunched kernel needs %s" % name)
        self.assertEqual(out["XDG_STATE_HOME"], os.path.join(lab, "xdg"))
        self.assertEqual(out["ROMP_KERNEL_PORT"], "4321")
        for name in ("TMPDIR", "TMUX_TMPDIR", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"):
            self.assertEqual(out.get(name), runner[name], "the run's %s reaches the relaunched kernel" % name)

    def test_a_live_sessions_identity_and_the_runs_own_names_never_reach_the_relaunch_env(self):
        lab = os.path.join(os.sep, "lab")
        # the runner's shell on a machine running romp: the live manager's pid (kernel.py's _parent_watch exits the
        # kernel when it dies, so a relaunched lab kernel that carried it exited with a live manager restart), the
        # session's identity (ROMP_SID, ROMP_SESSION_NAME) and the serve chain's ROMP_BIN, and a name under the
        # prefix tests/conftest.py exports the run's own names under (ROMP_TESTS_SYSTEM_TMPDIR), which no kernel reads
        identity = {"ROMP_MANAGER_PID": "12345", "ROMP_SID": "cccccccc-1111-2222-3333-444444444444",
                    "ROMP_SESSION_NAME": "web", "ROMP_BIN": os.path.join(lab, "bin", "romp")}
        runner = dict(identity, ROMP_TESTS_PROBE=os.path.join(lab, "probe"))
        with mock.patch.dict(os.environ, runner):
            env = kernel_env(lab, os.path.join(lab, "claude"), os.path.join(lab, "dist"), 4321, "testtok")
        names = sorted(env)
        for name in runner:
            self.assertNotIn(name, names, "the runner's %s must never reach a lab kernel" % name)
        self.assertEqual([k for k in names if k.startswith("ROMP_TESTS_")], [],
                         "a ROMP_TESTS_ name of the run reached the lab kernel")
        # the same ROMP_TESTS_ name put in the lab kernel's environment by the lab itself, the way the served labs
        # plant their probe: it is the run's own, so the file the relaunch reads must not carry it either
        env["ROMP_TESTS_PROBE"] = os.path.join(lab, "probe")
        written = sorted(relaunch_cfg(env, os.path.join(lab, "kernel.log"))["env"])
        self.assertNotIn("ROMP_TESTS_PROBE", written,
                         "a ROMP_TESTS_ name is the run's own: the relaunched kernel reads none, the file must not carry one")
        for name in identity:
            self.assertNotIn(name, written, "the runner's %s must never reach the relaunched kernel" % name)
        for name in ("ROMP_KERNEL_PORT", "ROMP_SERVE_TOKEN", "ROMP_DIST_DIR", "ROMP_MODEL_CATALOG", "ROMP_POSTAL_PORT"):
            self.assertIn(name, written, "the lab's own %s still reaches the relaunched kernel" % name)


class LabKernelEnv(unittest.TestCase):
    """A lab kernel's environment is built from names (kernel_env), never from a copy of the runner's. A run from a
    session on a machine running romp carries the live kernel's exports, and a lab kernel that inherited them exited
    when the live manager restarted (ROMP_MANAGER_PID), bound where the live kernel serves (ROMP_SERVE_HOST) and,
    with no ROMP_POSTAL_PORT of its own, dialled the machine's postal bus at boot, or on a machine with none started
    a detached bus nothing stops. No kernel and no browser, so this runs everywhere; every lab that boots a kernel
    builds its environment through the same function."""

    LAB = os.path.join(os.sep, "lab")
    # the floor tests/conftest.py sets for the run's children, planted so the test asserts on values it chose
    # rather than on conftest having set them
    FLOOR = {"ROMP_SERVICE_ENV_FILE": os.path.join(LAB, "no-such-service.env"),
             "ROMP_SERVICE_ENV": os.path.join(LAB, "no-such-service.env"),
             "ROMP_CLAUDE_BIN": "/bin/false", "ROMP_CLI_SCOPE": "0",
             "TMPDIR": os.path.join(LAB, "tmp"), "TMUX_TMPDIR": os.path.join(LAB, "tmux"),
             "GIT_CONFIG_GLOBAL": os.path.join(LAB, "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
    # an XDG_ name of the runner's: a lab kernel takes the XDG_* names (kernel/credentials.py resolves the service.env
    # default under XDG_CONFIG_HOME); XDG_STATE_HOME is the one XDG_ name the lab sets itself
    XDG = {"XDG_CONFIG_HOME": os.path.join(LAB, "config")}
    # a live kernel's exports as a session's shell carries them, and a stand-in for a key the shell carries
    LIVE = {"ROMP_MANAGER_PID": "4242", "ROMP_SERVE_HOST": "0.0.0.0", "ROMP_STATE_DIR": os.path.join(LAB, "live"),
            "ROMP_SID": "cccccccc-1111-2222-3333-444444444444", "ROMP_SESSION_NAME": "web", "ROMP_SUPERVISED": "1",
            "RUNNER_SECRET_PROBE": "abc"}
    # what the lab itself puts in: its roots, the serve seams, and a postal bus of its own that is never started
    OWN = {"XDG_STATE_HOME": os.path.join(LAB, "xdg"), "CLAUDE_CONFIG_DIR": os.path.join(LAB, "claude"),
           "ROMP_MANAGER_PORT": "1", "ROMP_KERNEL_NO_OPEN": "1", "ROMP_SERVE_TOKEN": "testtok",
           "ROMP_KERNEL_PORT": "4321", "ROMP_DIST_DIR": os.path.join(LAB, "dist"), "ROMP_MODEL_CATALOG": "off",
           "ROMP_POSTAL_PEERS": "0", "ROMP_POSTAL_CLIENT_ONLY": "1", "ROMP_POSTAL_HERMETIC": "1"}
    # the port a kernel with no ROMP_POSTAL_PORT of its own dials: the machine's bus, when a session's shell names it
    MACHINE_BUS_PORT = "25302"

    def _env(self, runner, **seams):
        with mock.patch.dict(os.environ, runner):
            if "ROMP_POSTAL_PORT" not in runner:
                os.environ.pop("ROMP_POSTAL_PORT", None)
            return kernel_env(self.LAB, os.path.join(self.LAB, "claude"), os.path.join(self.LAB, "dist"), 4321,
                              "testtok", **seams)

    def test_a_live_kernels_exports_never_reach_a_lab_kernel_and_the_floor_and_its_own_names_do(self):
        env = self._env({**self.FLOOR, **self.XDG, **self.LIVE, "ROMP_POSTAL_PORT": self.MACHINE_BUS_PORT})
        names = sorted(env)
        for name in self.LIVE:
            self.assertNotIn(name, names, "a live kernel's %s must never reach a lab kernel" % name)
        for name, value in self.OWN.items():
            self.assertEqual(env.get(name), value, "the lab's own %s" % name)
        for name, value in {**self.FLOOR, **self.XDG}.items():
            self.assertEqual(env.get(name), value, "the runner's %s reaches the lab kernel" % name)
        for name in ("PATH", "HOME"):
            self.assertIn(name, names, "the lab kernel needs %s" % name)
        # every ROMP_ name is the lab's own or the run's floor: nothing else of the runner's
        self.assertEqual({k for k in env if k.startswith("ROMP_")} - set(self.OWN) - set(self.FLOOR), {"ROMP_POSTAL_PORT"},
                         "a ROMP_ name of the runner's reached the lab kernel")

    def test_the_lab_kernels_postal_bus_is_a_port_of_its_own_and_is_never_started(self):
        env = self._env({**self.FLOOR, "ROMP_POSTAL_PORT": self.MACHINE_BUS_PORT})
        self.assertTrue(env.get("ROMP_POSTAL_PORT", "").isdigit(), "a bus port of its own: %r" % env.get("ROMP_POSTAL_PORT"))
        self.assertNotEqual(env["ROMP_POSTAL_PORT"], self.MACHINE_BUS_PORT, "the machine's bus, never")
        env = self._env(self.FLOOR)      # the runner names no bus at all: the lab kernel still gets one of its own
        self.assertTrue(env.get("ROMP_POSTAL_PORT", "").isdigit(), "a bus port of its own: %r" % env.get("ROMP_POSTAL_PORT"))
        self.assertNotEqual(env["ROMP_POSTAL_PORT"], self.MACHINE_BUS_PORT, "the default is the machine's bus")
        # client-only applies in the legacy singleton scheme alone (postal_service.is_client_only), so both names go
        # in: the kernel's boot-time ensure then starts nothing on that port
        self.assertEqual((env["ROMP_POSTAL_PEERS"], env["ROMP_POSTAL_CLIENT_ONLY"]), ("0", "1"))

    def test_a_labs_own_seams_go_over_the_names(self):
        # names of the lab's own (ROMP_WS_KEEPALIVE, ROMP_HOST_NAME), one kernel_env sets for every lab
        # (ROMP_KERNEL_NO_OPEN) and one of the run's floor (ROMP_CLI_SCOPE): the seam wins each time
        env = self._env(self.FLOOR, ROMP_WS_KEEPALIVE="2", ROMP_HOST_NAME="TESTHOST", ROMP_KERNEL_NO_OPEN="0",
                        ROMP_CLI_SCOPE="1")
        self.assertEqual((env["ROMP_WS_KEEPALIVE"], env["ROMP_HOST_NAME"]), ("2", "TESTHOST"))
        self.assertEqual((env["ROMP_KERNEL_NO_OPEN"], env["ROMP_CLI_SCOPE"]), ("0", "1"),
                         "a seam goes over a name kernel_env sets for every lab and over the run's floor")


class ServedWedge(_ShipLab):
    def test_restart_between_ship_and_ack_reships_heals_and_releases_the_held_send(self):
        r = self._run_driver(DRIVER, {
            "url": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
            "kernelPid": self.kernel.pid, "relaunch": relaunch_cfg(self.env, self.klog),
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


DRIVER_NACK = r"""
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
const out = {};
const die = async (why) => {
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n");
  await browser.close();
  process.exit(0);
};
// a wait that hands back the page's answer, asked of whichever page is up: an evaluate mid-navigation throws and is
// asked again of the page that follows (waitForFunction can reject when the navigation destroys the context it polls)
const until = async (fn, arg, ms) => {
  const t0 = Date.now();
  for (;;) {
    let v = false;
    try { v = await page.evaluate(fn, arg); } catch (e) { /* mid-navigation */ }
    if (v) return v;
    if (Date.now() - t0 > ms) return false;
    await new Promise((r) => setTimeout(r, 50));
  }
};
await page.goto(cfg.url);
await page.waitForSelector("#composer-input", { timeout: 20000 });
const tab = await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 }).catch(() => null);
if (!tab) await die("no session tab: the lab seed never reached the chat payload");
await page.waitForTimeout(500);   // let the shim's ws settle onto the live kernel
// ServedWedge's stage: a ship on a live socket the stopped kernel never answers, the send held on the gate
process.kill(cfg.kernelPid, "SIGSTOP");
await page.setInputFiles("body > input[type=file]", cfg.file);
await page.waitForSelector(".composer-file-pending", { timeout: 10000 }).catch(() => {});
out.chipUpAfterShip = await page.locator(".composer-file-pending").count();
await page.fill("#composer-input", cfg.msg);
await page.click("#composer-send");
await page.waitForSelector(".confirm-btn", { timeout: 10000 }).catch(() => {});
const waitBtn = page.locator(".confirm-btn", { hasText: "Wait for the upload" });
out.gateOffered = await waitBtn.count();
if (out.gateOffered) await waitBtn.click();
// two things on the OLD page before the kernel goes. A toast wearing the ephemeral mark, on screen as the core takes the
// page: a stand-in with warnToast's shape (render.ts marks its own "Can't send yet" refusal the same way; this lab has
// no unreachable host to raise it), which the fresh page must not repeat. And the latch: this document records the nack
// toast the moment it appears, in sessionStorage, which survives the reload (the reload follows the nack by one task,
// so nothing read from outside after the fact could see the old page's toasts).
const bootBefore = await page.evaluate((probe) => {
  window.__probe = 1;
  let wt = document.getElementById("warn-toasts");
  if (!wt) { wt = document.createElement("div"); wt.id = "warn-toasts"; document.body.appendChild(wt); }
  const eph = document.createElement("div"); eph.className = "warn-toast"; eph.dataset.ephemeral = "1";
  const msg = document.createElement("span"); msg.className = "warn-toast-msg"; msg.textContent = probe;
  eph.appendChild(msg); wt.appendChild(eph);
  sessionStorage.removeItem("probe:nackSeen");
  const look = () => {
    if (sessionStorage.getItem("probe:nackSeen")) return;
    const toasts = document.getElementById("warn-toasts")?.textContent || "";
    if (toasts.includes("couldn't be saved"))
      sessionStorage.setItem("probe:nackSeen", JSON.stringify({ text: toasts,
        ephemeralOnScreen: !!document.querySelector("#warn-toasts .warn-toast[data-ephemeral]"),
        waiting: window.__rompReload ? window.__rompReload.waiting : null }));
  };
  new MutationObserver(look).observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
  setInterval(look, 20);
  return window.__rompReload ? window.__rompReload.boot : null;
}, cfg.probe);
out.bootBefore = bootBefore;
// the drops path becomes a regular file: the relaunched kernel's _save_dropped_file fails and it NACKs the re-ship
fs.rmSync(cfg.drops, { recursive: true, force: true });
fs.writeFileSync(cfg.drops, "not a directory");
process.kill(cfg.kernelPid, "SIGKILL");
const k2 = spawn(cfg.relaunch.cmd, [], { env: cfg.relaunch.env, detached: true,
  stdio: ["ignore", fs.openSync(cfg.relaunch.log, "a"), fs.openSync(cfg.relaunch.log, "a")] });
k2.unref();
fs.writeSync(1, "KPID:" + k2.pid + "\n");
// the OLD page saw the nack (the re-shipped file could not be saved); then the reload core's turn
out.nackSeen = await until(() => JSON.parse(sessionStorage.getItem("probe:nackSeen") || "null"), null, 45000) || null;
out.reloaded = await until((boot) => window.__probe !== 1 && !!window.__rompReload && window.__rompReload.boot !== boot, bootBefore, 30000);
await page.waitForSelector("#composer-input", { timeout: 20000 }).catch(() => {});
out.bootAfter = await page.evaluate(() => window.__rompReload ? window.__rompReload.boot : null).catch(() => null);
// the FRESH page: the notice again (the replay runs as the bundle loads; the toast lives 12 s, so read it at once),
// and every toast on it by itself, for the count
out.freshToasts = await until(() => {
  const t = document.getElementById("warn-toasts")?.textContent || "";
  return t.includes("couldn't be saved") ? t : false;
}, null, 5000) || (await page.evaluate(() => document.getElementById("warn-toasts")?.textContent || "").catch(() => null));
out.freshList = await page.evaluate(() => Array.from(document.querySelectorAll("#warn-toasts .warn-toast-msg"), (n) => n.textContent || "")).catch(() => null);
out.freshPending = await page.locator(".composer-file-pending").count();
out.freshFiles = await page.locator(".composer-file").count();
// one replay: the record leaves sessionStorage as the toasts are raised
out.recordAfterReplay = await page.evaluate(() => sessionStorage.getItem("romp:reloadNotices")).catch(() => "unread");
// a load the USER asks for, with the replayed toast still on screen: pagehide keeps the scroll record alone, so the
// page that follows says nothing
out.toastsBeforeNav = await page.locator("#warn-toasts .warn-toast").count();
await page.goto(cfg.url);
await page.waitForSelector("#composer-input", { timeout: 20000 });
await page.waitForTimeout(800);
out.secondLoadToasts = await page.evaluate(() => document.getElementById("warn-toasts")?.textContent || "");
// the draft comes back one time after a load, once the tab has landed (restoreActiveDraftOnce): a wait, not a read
out.inputAfterNav = await until(() => document.getElementById("composer-input")?.value || false, null, 15000);
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class NackNoticeSurvivesReload(_ShipLab):
    """The reload core's restart reload follows the LAST pending ship's retirement: retirePendingShip ends the hold
    (endReloadHoldIfIdle, __rompReload.ended()) and the core fires on the next task. The nack that retired the ship
    raises its toast in the same handler, after the hold ended, so the notice (the file was not saved, the held
    message NOT sent) was appended one task before the page went: never read, and the fresh page's loss toast had
    nothing to say (shipsInFlight was already empty). A failed attachment and an unsent message went unannounced.
    Now the core's synchronous hook keeps the toasts on screen in this tab's sessionStorage and the fresh page shows
    them again once. The drops path is made a regular file before the relaunch so the re-ship's save fails and the
    kernel nacks it (_save_dropped_file); the old page latches its toast in sessionStorage, which survives the
    reload. A toast wearing the ephemeral mark is on screen too (a stand-in with warnToast's shape, since the lab has
    no unreachable host for render.ts to raise its own), and the fresh page must not repeat it."""

    def test_the_nack_of_the_last_ship_is_shown_again_by_the_fresh_page_once(self):
        r = self._run_driver(DRIVER_NACK, {
            "url": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
            "kernelPid": self.kernel.pid, "relaunch": relaunch_cfg(self.env, self.klog),
            "file": self.png, "msg": "hold this message for the upload that will not save",
            "drops": os.path.join(self.state, "drops"),
            "probe": "probe: an ephemeral notice, which the fresh page must not repeat"})
        self.assertEqual(r["chipUpAfterShip"], 1, "the pending chip must be up after the ship: %r" % r)
        self.assertEqual(r["gateOffered"], 1, "the ship gate must offer to wait for the upload: %r" % r)
        # the old page: the re-ship was nacked, the toast named the file and the unsent message
        self.assertTrue(r["nackSeen"], "the relaunched kernel must NACK the re-ship (drops is a file): %r (kernel log tail: %s)"
                        % (r, Path(self.klog).read_text()[-500:]))
        self.assertIn("shot.png couldn't be saved", r["nackSeen"]["text"])
        self.assertIn("Your message was NOT sent", r["nackSeen"]["text"], "the held send did not fire without its file")
        self.assertTrue(r["nackSeen"].get("ephemeralOnScreen"), "the toast wearing the ephemeral mark was on screen as the core took the page: %r" % r)
        # then the reload the core owed (held while the ship awaited its answer)
        self.assertTrue(r["reloaded"], "the reload core reloads once the ships settled: %r" % r)
        self.assertNotEqual(r["bootAfter"], r["bootBefore"], "the fresh page carries the relaunched kernel's boot id: %r" % r)
        # the heart of it: the fresh page says it again, once, and only that
        self.assertIn("shot.png couldn't be saved", r["freshToasts"] or "",
                      "the nack the reload wiped must be shown again by the fresh page: %r" % r)
        self.assertIn("Your message was NOT sent", r["freshToasts"] or "", "with the unsent message named: %r" % r)
        self.assertEqual(len([t for t in (r["freshList"] or []) if "couldn't be saved" in t]), 1,
                         "shown again once, not once per raise: %r" % r)
        self.assertNotIn("still uploading", r["freshToasts"] or "", "no loss toast over a ship that settled: %r" % r)
        self.assertNotIn("probe:", r["freshToasts"] or "", "the toast wearing the ephemeral mark stays behind: %r" % r)
        self.assertEqual([r["freshPending"], r["freshFiles"]], [0, 0], "no chip and no attachment over a file that was not saved: %r" % r)
        # once: the record leaves sessionStorage with the replay, and a load the user asks for replays nothing
        self.assertIsNone(r["recordAfterReplay"], "the replay takes the record out of sessionStorage: %r" % r)
        self.assertGreaterEqual(r["toastsBeforeNav"], 1, "the replayed toast was on screen when the user navigated: %r" % r)
        self.assertNotIn("couldn't be saved", r["secondLoadToasts"], "pagehide keeps the scroll record alone: nothing replayed: %r" % r)
        # and the draft is still there to act on
        self.assertEqual(r["inputAfterNav"], "hold this message for the upload that will not save", "the draft survives as before: %r" % r)


if __name__ == "__main__":
    unittest.main()
