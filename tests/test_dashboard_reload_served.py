#!/usr/bin/env python3
"""T265, the served leg: the dashboard reloads ITSELF on a newer bundle and on a kernel restart, never
mid-gesture, and the chat reader keeps their place (the user 2026-09-08, superseding their 2026-07-13
preference for a banner the reader clicks).

Executed against the REAL page: a hermetic kernel serves the dashboard from a private copy of dist; the
driver opens it, scrolls the chat transcript to mid-history, and
  1. holds a pointer button down over the chat pane, then bumps a dist bundle's mtime — the kernel's next
     keepalive carries a higher `dv` (the authoritative drift signal) — and asserts the page did NOT reload
     while the button was held (the reload is armed, waiting on the pointer);
  2. releases the button and asserts the page reloaded, landed the chat tab on the reader's saved position
     (not the bottom), and left one "Reloaded onto build …" line in the notification center;
  3. kills the kernel and relaunches it on the same port — a socket reopen against a NEW boot id — and asserts
     a second reload with the "kernel restarted" wording.
Skips LOUDLY when the extension deps or a playwright browser are absent (CI installs none); the decision code
itself runs in node in test_dashboard_auto_reload.py regardless. All fixtures synthetic."""
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
SID = "11111111-2222-4333-8444-000000000201"

import sys
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment and the cfg.relaunch stanza (the module,
#                                   not its classes: an imported TestCase would be collected here a second time)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _transcript(cwd, pairs):
    """`pairs` closed user/assistant turns (an OPEN turn would invite the boot reconcile to resume it)."""
    out, parent, t = [], None, 1_700_000_000
    filler = ["The ranking pass reads its weights from the notes-api config now.",
              "Tokenizer edge cases (hyphens, quotes) are covered by the new fixture set.",
              "Index rebuild time is dominated by the stemmer; caching its table halves it.",
              "The pagination cursor survives a re-sort because it encodes the sort key too."]
    for i in range(pairs):
        u = "11111111-2222-4333-8444-0000000a%04x" % i
        a = "11111111-2222-4333-8444-0000000b%04x" % i
        ts = lambda k: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t + i * 60 + k))
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": ts(0), "sessionId": SID, "cwd": cwd,
                    "message": {"role": "user", "content": "please keep going with the search module notes (part %d)" % (i + 1)}})
        body = "\n\n".join(["Note %d." % (i + 1)] + [filler[(i + k) % len(filler)] for k in range(3)])
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ts(5), "sessionId": SID, "cwd": cwd,
                    "message": {"id": "msg_lab_%04d" % i, "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                                "content": [{"type": "text", "text": body}], "stop_reason": "end_turn"}})
        parent = a
    return "\n".join(json.dumps(r) for r in out) + "\n"


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
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const out = {};
const die = async (why) => {
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n");
  await browser.close();
  process.exit(0);
};
const chatFrame = () => page.frames().find((f) => f.url().split("?")[0].endsWith("/chat"));
const waitChat = async () => {
  await page.waitForFunction(() => Array.from(document.querySelectorAll("iframe")).some((f) => (f.getAttribute("src") || "").startsWith("/chat")), null, { timeout: 20000 });
  let fr = null;
  for (let i = 0; i < 200 && !fr; i++) { fr = chatFrame(); if (!fr) await page.waitForTimeout(100); }
  if (!fr) await die("no /chat frame");
  await fr.waitForFunction(() => {
    const c = document.getElementById("content");
    return !!c && c.querySelectorAll(".turn").length > 20 && c.scrollHeight > c.clientHeight + 500;
  }, null, { timeout: 30000 }).catch(async () => { await die("chat never overflowed"); });
  return fr;
};
const info = (fr) => fr.evaluate(() => {
  const c = document.getElementById("content");
  const cr = c.getBoundingClientRect();
  const els = Array.from(c.querySelectorAll("[data-uuid]"));
  let anchor = null;
  for (const el of els) { const r = el.getBoundingClientRect(); if (r.bottom > cr.top) { anchor = { uuid: el.dataset.uuid, top: Math.round(r.top - cr.top) }; break; } }
  return { scrollTop: c.scrollTop, scrollHeight: c.scrollHeight, clientHeight: c.clientHeight, anchor,
           chipHidden: (document.getElementById("jump-bottom") || { hidden: true }).hidden };
});
const notices = () => page.evaluate(() => { try { return JSON.parse(localStorage.getItem("romp:notices") || "[]").filter((n) => n.kind === "reload").map((n) => n.text); } catch (e) { return []; } });
const probeSet = () => page.evaluate(() => { window.__probe = 1; return 1; });
const probeAlive = () => page.evaluate(() => window.__probe === 1).catch(() => false);
const shellWaiting = () => page.evaluate(() => window.__rompReload ? { waiting: window.__rompReload.waiting, owed: window.__rompReload.owed(), fired: window.__rompReload.fired() } : null).catch(() => null);
const bump = (n) => { const t = new Date(Date.now() + n * 60 * 1000); fs.utimesSync(cfg.bumpFile, t, t); };

// ---- load, scroll to mid-history ----
await page.goto(cfg.url);
let fr = await waitChat();
await page.waitForTimeout(800);
await fr.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = Math.round(c.scrollHeight * 0.45); });
await page.waitForTimeout(600);
const before = await info(fr);
out.before = before;
await probeSet();
out.noticesBefore = await notices();

// ---- 1. hold the pointer over the chat pane, then bump the bundle: armed, not fired ----
const box = await (await page.$('iframe[src^="/chat"]')).boundingBox();
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
await page.mouse.down();
bump(1);
// three keepalives (ROMP_WS_KEEPALIVE=2 in the lab env) — plenty for the higher dv to reach the page
await page.waitForTimeout(6500);
out.probeWhileHeld = await probeAlive();
out.shellWhileHeld = await shellWaiting();
// ---- 2. release: the reload fires; the chat tab lands where the reader was ----
await page.mouse.up();
const reloaded = await page.waitForFunction(() => window.__probe !== 1, null, { timeout: 15000 }).then(() => true).catch(() => false);
out.reloadedOnRelease = reloaded;
if (!reloaded) await die("no reload after the pointer was released");
fr = await waitChat();
// the land is a frame after the build; poll until the position settles off the bottom or 5 s pass
let after = null;
for (let i = 0; i < 25; i++) { after = await info(fr); if (after.scrollHeight - after.scrollTop - after.clientHeight > 2 && after.scrollTop > 0) break; await page.waitForTimeout(200); }
out.after = after;
out.noticesAfterBuild = await notices();
await probeSet();
// the fresh page must SETTLE: three more keepalives with no further reload (a page that reloaded onto the
// current build must not read the same build as drift again)
await page.waitForTimeout(7000);
out.settledAfterBuild = await probeAlive();

// ---- 3. a kernel restart: kill + relaunch on the same port → a reopen against a new boot id ----
process.kill(cfg.kernelPid, "SIGKILL");
await page.waitForTimeout(500);
const k2 = spawn(cfg.relaunch.cmd, [], { env: cfg.relaunch.env, detached: true,
  stdio: ["ignore", fs.openSync(cfg.relaunch.log, "a"), fs.openSync(cfg.relaunch.log, "a")] });
k2.unref();
fs.writeSync(1, "KPID:" + k2.pid + "\n");
const reloaded2 = await page.waitForFunction(() => window.__probe !== 1, null, { timeout: 60000 }).then(() => true).catch(() => false);
out.reloadedOnRestart = reloaded2;
if (reloaded2) { await waitChat().catch(() => {}); await page.waitForTimeout(1500); }
out.noticesAfterRestart = await notices();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedAutoReload(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="auto-reload-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.bump_file = os.path.join(dist, "render.js")
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        os.makedirs(os.path.join(state, "names"), exist_ok=True)
        os.makedirs(os.path.join(state, "sdk"), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        # one synthetic SDK session so the chat page has a tab (the test_ship_reship lab shape); its transcript
        # holds only CLOSED turns, so the boot reconcile never tries to resume it and no CLI is ever spawned
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, SID + ".jsonl").write_text(_transcript(cwd, 60))   # long: overflows the pane several times over
        cls.port = _free_port()
        cls.token = "testtok-autoreload"
        cls.env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token,
                                  ROMP_WS_KEEPALIVE="2")                       # the dv rides the keepalive: keep the wait short
        # a name outside the relaunch list, planted in the lab kernel's environment so the check on the cfg.json the
        # driver reads has something the file must not carry
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
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if getattr(cls, "kernel", None):
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_build_drift_reloads_after_the_gesture_ends_and_a_restart_reloads_too(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (self.port, self.token),
                       "kernelPid": self.kernel.pid, "bumpFile": self.bump_file,
                       "relaunch": _lab.relaunch_cfg(self.env, self.klog)}, f)
        # the file carries only what the driver and the relaunched kernel need: the probe the lab planted in its
        # kernel's environment (checked first, so the file check cannot pass without it) must not be in it (names
        # only in the report, never the values)
        self.assertIn("RUNNER_SECRET_PROBE", sorted(self.env), "the lab plants the probe in its kernel's environment")
        written = json.loads(Path(cfg).read_text(encoding="utf-8"))
        self.assertNotIn("RUNNER_SECRET_PROBE", sorted(written["relaunch"]["env"]),
                         "the lab's cfg.json carries a name of the lab kernel's environment outside the relaunch list")
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            so = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode()
            kpid = next((ln for ln in so.splitlines() if ln.startswith("KPID:")), None)
            if kpid:
                type(self).kernel2_pid = int(kpid.split(":", 1)[1])
            self.fail("driver timed out; partial output:\n%s" % so)
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
        b, a = r["before"], r["after"]
        self.assertGreater(b["scrollHeight"] - b["scrollTop"] - b["clientHeight"], 200, "the reader was scrolled up before: %r" % b)
        # 1. armed, not fired, while the pointer was held
        self.assertTrue(r["probeWhileHeld"], "the page must not reload mid-gesture: %r" % r)
        self.assertEqual((r["shellWhileHeld"] or {}).get("waiting"), "pointer", "the reload is armed on the held pointer: %r" % r["shellWhileHeld"])
        self.assertEqual(((r["shellWhileHeld"] or {}).get("owed") or {}).get("reason"), "build")
        # 2. the release fires it, and the chat tab lands where the reader was
        self.assertTrue(r["reloadedOnRelease"])
        self.assertLessEqual(abs(a["scrollTop"] - b["scrollTop"]), 60, "the reader's position survives the reload: %r → %r" % (b, a))
        self.assertGreater(a["scrollHeight"] - a["scrollTop"] - a["clientHeight"], 200, "…and is not the bottom: %r" % a)
        self.assertFalse(a["chipHidden"], "off the bottom, the go-to-bottom chip shows")
        self.assertEqual(len(r["noticesBefore"]), 0)
        self.assertEqual(len(r["noticesAfterBuild"]), 1, "one notification-center line per reload: %r" % r["noticesAfterBuild"])
        self.assertRegex(r["noticesAfterBuild"][0], r"^Reloaded onto build \d+ — a newer romp build was served\.$")
        self.assertTrue(r["settledAfterBuild"], "one reload per drift — the fresh page must not reload again: %r" % r)
        # 3. a kernel restart reloads too, with its own wording
        self.assertTrue(r["reloadedOnRestart"], "a socket reopen against a new boot id reloads the page: %r" % r)
        self.assertEqual(len(r["noticesAfterRestart"]), 2, r["noticesAfterRestart"])
        self.assertRegex(r["noticesAfterRestart"][1], r"^Reloaded onto build \d+ — the kernel restarted\.$")


if __name__ == "__main__":
    unittest.main()
