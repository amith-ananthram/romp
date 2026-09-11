#!/usr/bin/env python3
"""T317 (the user 2026-09-10): clicking the dashboard's Files control opened the timeline in a side pane instead of
the Files pane. Reproduction and guard on the real shell page (`/`) of a hermetic kernel: the desktop rail's Files
toggle and the phone layout's Files tab open the Files pane once the gear's "Files control in the dashboard bar"
setting (T317 add-on) is on; OFF by default since T317b (the user 2026-09-10): a fresh store hides both, closes a pane
an earlier session left open and refuses a bring-forward, and the gear's write (heard through the storage event) shows
the control without a reload. With FILES_SHOTS=<dir> the driver writes screenshots (the control shown, and hidden; the
bottom bar with the control hidden by default and shown after the toggle, dark and light). Skips LOUDLY without
the extension deps or a Playwright browser. SYNTHETIC fixtures only (the notes-api demo world: session web)."""
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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's

WEB = "aaaaaaaa-1111-2222-3333-444444444444"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
// what is on screen: the body's pane classes, every pane's box and display, every pane iframe's src and title
const measure = () => page.evaluate(() => {
  const panes = {};
  for (const id of ["chat-pane", "fleet-pane", "feed-pane", "files-pane", "tl-pane"]) {
    const p = document.getElementById(id);
    if (!p) { panes[id] = null; continue; }
    const r = p.getBoundingClientRect(); const cs = getComputedStyle(p);
    const f = p.querySelector("iframe");
    let title = null, url = null;
    try { title = f && f.contentDocument ? f.contentDocument.title : null; url = f && f.contentWindow ? f.contentWindow.location.pathname : null; } catch (e) { title = "cross-origin"; }
    panes[id] = { display: cs.display, x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height),
                  iframe: f ? { id: f.id, src: f.getAttribute("src"), url, title, mOn: f.classList.contains("m-on") } : null };
  }
  const vis = (b) => { const cs = getComputedStyle(b); const r = b.getBoundingClientRect(); return cs.display !== "none" && r.width > 0; };
  const rail = Array.from(document.querySelectorAll(".rail-btn[data-pane]")).map((b) => ({ pane: b.dataset.pane, label: b.textContent, on: b.classList.contains("on"), shown: vis(b) }));
  const tabs = Array.from(document.querySelectorAll("#mtabs button[data-pane]")).map((b) => ({ pane: b.dataset.pane, label: b.textContent, on: b.classList.contains("on"), shown: vis(b) }));
  return { body: document.body.className, tab: document.body.getAttribute("data-tab"), mobile: !!(window.__rompMobileOn && window.__rompMobileOn()),
           panes, rail, tabs, panesStored: localStorage.getItem("romp-panes") };
});
const results = {};
let page;
for (const pass of cfg.passes) {
  page = await browser.newPage({ viewport: { width: pass.width, height: pass.height }, deviceScaleFactor: 2, hasTouch: !!pass.touch });
  // the seed stands for what an earlier visit left behind, so it is written ONCE, by the top document: an init script
  // re-runs in every child frame as it attaches or navigates, and the shell's panes load lazily AFTER its boot, so an
  // unguarded seed would replay the stale value over the boot's own save (the close of a hidden control's pane)
  if (pass.storage) await page.addInitScript((st) => { if (self !== top) return; for (const k in st) localStorage.setItem(k, st[k]); }, pass.storage);
  await page.goto(cfg.shell);
  await page.waitForSelector("#f-chat", { timeout: 20000 });
  await page.waitForTimeout(1500);   // the panes' boots
  const before = await measure();
  if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/romp_shell-files-" + pass.name + "-before.png", fullPage: false }); }
  let after = null;
  if (pass.toggle) {
    // the bottom bar, hidden by default: shots in both themes (the shell's light theme is body.theme-light), clipped to the bar
    const barClip = () => page.evaluate(() => { const rs = Array.from(document.querySelectorAll(".rail-btn")).map((b) => b.getBoundingClientRect()).filter((r) => r.width > 0);   // a hidden button's box is empty: not the bar's
      const top = Math.min(...rs.map((r) => r.top)), bottom = Math.max(...rs.map((r) => r.bottom)); return { x: 0, y: Math.max(0, Math.floor(top) - 8), width: 1400, height: Math.ceil(bottom - top) + 16 }; });
    if (cfg.shots) { const c = await barClip(); await page.screenshot({ path: cfg.shots + "/romp_shell-files-control-default-hidden-dark.png", clip: c });
      await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(200);
      await page.screenshot({ path: cfg.shots + "/romp_shell-files-control-default-hidden-light.png", clip: c });
      await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(100); }
    // the gear's write, from the feed iframe (the gear's own document): the shell hears it through its storage listener
    const feed = page.frames().find((f) => f.url().includes("/feed"));
    if (!feed) { console.error("no feed frame: " + page.frames().map((f) => f.url()).join(" ")); process.exit(1); }
    await feed.evaluate(() => { const s = JSON.parse(localStorage.getItem("romp:settings") || "{}"); s.showFilesControl = true; localStorage.setItem("romp:settings", JSON.stringify(s)); });
    await page.waitForTimeout(800);
    after = await measure();
    if (cfg.shots) { const c = await barClip(); await page.screenshot({ path: cfg.shots + "/romp_shell-files-control-shown-dark.png", clip: c });
      await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(200);
      await page.screenshot({ path: cfg.shots + "/romp_shell-files-control-shown-light.png", clip: c });
      await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(100); }
  } else if (pass.hidden) {
    // the control hidden by the gear's setting: nothing to click; the palette's command and a relay are refused
    await page.evaluate(() => { window.__rompPaneToggle && window.__rompPaneToggle("files", true); });
    await page.waitForTimeout(600);
    after = await measure();
  } else {
    // the click the user makes: the rail's Files toggle on desktop, the bottom bar's Files tab on a phone
    const sel = pass.mobile ? "#mtabs button[data-pane=files]" : ".rail-btn[data-pane=files]";
    const target = await page.$(sel);
    if (!target) { console.error("no Files control for " + sel + ": " + JSON.stringify(before)); process.exit(1); }
    await target.click();
    await page.waitForTimeout(1200);
    after = await measure();
  }
  if (cfg.shots) await page.screenshot({ path: cfg.shots + "/romp_shell-files-" + pass.name + "-after.png", fullPage: false });
  results[pass.name] = { before, after };
  await page.close();
}
fs.writeSync(1, "RESULT:" + JSON.stringify(results) + "\n");
await browser.close();
process.exit(0);
"""


class ServedFilesPaneToggle(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="files-pane-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cwd, "README.md").write_text("# notes-api\n\nA demo project for the Files pane.\n")
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 600
        recs = [{"type": "user", "timestamp": iso(t0), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": WEB,
                 "message": {"role": "user", "content": "how should the notes-api retry loop back off?"}},
                {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": WEB,
                 "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "Use exponential backoff with a jitter of ten percent."}]}}]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.port, cls.token = _free_port(), "testtok-files"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.kill(); k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"shell": "http://127.0.0.1:%d/?token=%s" % (self.port, self.token),
                       # the control is OFF by default (T317b): the two clicking passes turn it on through the gear's store key
                       "passes": [{"name": "desktop", "width": 1400, "height": 900, "mobile": False, "storage": {"romp:settings": json.dumps({"showFilesControl": True})}},
                                  {"name": "phone", "width": 390, "height": 844, "mobile": True, "touch": True, "storage": {"romp:settings": json.dumps({"showFilesControl": True})}},
                                  # the control hidden: a store the T317-era gear wrote (its whole-object save merged filesControl: true into any
                                  # profile that touched a setting; the key is never read), with the pane left OPEN by that earlier session
                                  {"name": "desktop-hidden", "width": 1400, "height": 900, "mobile": False, "hidden": True,
                                   "storage": {"romp:settings": json.dumps({"compact": True, "filesControl": True}),
                                               "romp-panes": json.dumps({"chat": True, "fleet": False, "feed": True, "timeline": True, "files": True})}},
                                  {"name": "phone-hidden", "width": 390, "height": 844, "mobile": True, "touch": True, "hidden": True,
                                   "storage": {"romp-mobile-tab": "files"}},
                                  # the default, then the gear's toggle: its write from the feed's document reaches the shell as a storage event
                                  {"name": "desktop-toggle", "width": 1400, "height": 900, "mobile": False, "toggle": True}],
                       "shots": os.environ.get("FILES_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def test_the_files_control_opens_the_files_pane(self):
        r = self._drive()
        if os.environ.get("FILES_SHOTS"):
            print("\nRESULT:" + json.dumps(r, indent=1)[:6000])
        d = r["desktop"]
        after = d["after"]
        self.assertIn("po-files", after["body"].split(), "the Files toggle turns the Files pane on: %r" % after["body"])
        fp = after["panes"]["files-pane"]
        self.assertIsNotNone(fp, "the shell has a Files pane")
        self.assertNotEqual(fp["display"], "none", "the Files pane shows: %r" % fp)
        self.assertGreater(fp["w"], 200, "the Files pane has a real width: %r" % fp)
        self.assertEqual(fp["iframe"]["src"], "/files", "the Files pane holds the Files page: %r" % fp)
        # the timeline band stays exactly as it was: the Files toggle never touches it
        self.assertEqual(after["panes"]["tl-pane"]["display"], d["before"]["panes"]["tl-pane"]["display"], "the Files toggle leaves the timeline as it was")
        self.assertEqual("po-timeline" in after["body"].split(), "po-timeline" in d["before"]["body"].split())
        # the control shown, by the gear's setting (on in these passes' store): both layouts offer it
        self.assertTrue(next(b for b in after["rail"] if b["pane"] == "files")["shown"], "the rail's Files toggle shows with the setting on: %r" % after["rail"])
        self.assertTrue(next(b for b in r["phone"]["after"]["tabs"] if b["pane"] == "files")["shown"], "the phone's Files tab shows with the setting on")
        # the control hidden (T317b): the desktop store is the T317-era gear's, filesControl: true merged in by a whole-object
        # save (never read); the phone store has no settings at all. The toggle and the tab are gone in both layouts, the pane an
        # earlier session left open is closed, a bring-forward is refused, and a phone left on the Files tab shows the chat
        h = r["desktop-hidden"]
        for k in ("before", "after"):
            self.assertFalse(next(b for b in h[k]["rail"] if b["pane"] == "files")["shown"], "the rail's Files toggle is hidden: %r" % h[k]["rail"])
            self.assertNotIn("po-files", h[k]["body"].split(), "the pane left open closes: %r" % h[k]["body"])
            self.assertEqual(h[k]["panes"]["files-pane"]["display"], "none")
            self.assertIn("no-files-control", h[k]["body"].split())
        self.assertEqual(json.loads(h["before"]["panesStored"])["files"], False, "the close is saved")
        self.assertEqual(len([b for b in h["after"]["rail"] if b["shown"]]), 4, "the four other toggles still show: %r" % h["after"]["rail"])
        ph = r["phone-hidden"]["after"]
        self.assertTrue(ph["mobile"])
        self.assertFalse(next(b for b in ph["tabs"] if b["pane"] == "files")["shown"], "the phone's Files tab is hidden: %r" % ph["tabs"])
        self.assertEqual(ph["tab"], "chat", "a stored Files tab falls to the chat: %r" % ph["tab"])
        self.assertTrue(ph["panes"]["chat-pane"]["iframe"]["mOn"] and not ph["panes"]["files-pane"]["iframe"]["mOn"])
        # the default, then the gear's toggle (T317b): a fresh shell hides the control; the gear's write from the feed's
        # document shows it without a reload, and the panes are told the pane is available again
        t = r["desktop-toggle"]
        self.assertIn("no-files-control", t["before"]["body"].split(), "a fresh store hides the control: %r" % t["before"]["body"])
        self.assertFalse(next(b for b in t["before"]["rail"] if b["pane"] == "files")["shown"], "the rail's Files toggle is hidden by default: %r" % t["before"]["rail"])
        self.assertEqual(len([b for b in t["before"]["rail"] if b["shown"]]), 4, "the four other toggles show: %r" % t["before"]["rail"])
        self.assertNotIn("no-files-control", t["after"]["body"].split(), "the gear's write shows the control: %r" % t["after"]["body"])
        self.assertTrue(next(b for b in t["after"]["rail"] if b["pane"] == "files")["shown"], "the rail's Files toggle appears after the toggle: %r" % t["after"]["rail"])
        self.assertEqual(len([b for b in t["after"]["rail"] if b["shown"]]), 5)
        m = r["phone"]["after"]
        self.assertTrue(m["mobile"], "the phone pass is the one-pane layout: %r" % m)
        self.assertEqual(m["tab"], "files", "the Files tab is the one showing: %r" % m)
        self.assertTrue(m["panes"]["files-pane"]["iframe"]["mOn"], "the Files iframe is the one on: %r" % m["panes"])
        self.assertFalse(m["panes"]["tl-pane"]["iframe"]["mOn"], "the timeline is not: %r" % m["panes"])


if __name__ == "__main__":
    unittest.main()
