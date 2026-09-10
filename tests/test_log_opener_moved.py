#!/usr/bin/env python3
"""The Log opens from the gear, not the bottom bar (T290, the user 2026-09-09: the bar keeps only its few
important controls). The desktop bar's action cluster no longer carries the Log's triangle; the settings panel's
Updates & debug section ends with an "Open log" button that closes the modal and asks the shell for the Log panel
({romp:'openLog'} → window.__rompOpenErrs), the same centered modal over the dimmed dashboard. The command
palette's log.open and the mobile bar's #merr are unchanged; the Log's own behaviour is untouched.

Two guards: SourcePins runs everywhere; ServedOpener boots the hermetic kernel, loads the dashboard, and drives
the gear's button in the settings iframe, the /settings page that hosts the gear since 2026-09-10 (skips loudly
without the extension deps or a Playwright browser).
All fixtures synthetic.
"""
import inspect
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from romp_load import load_source

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_logopener", os.path.join(BIN, "romp-kernel"))
GEAR = open(os.path.join(ROOT, "ui", "webview", "gear.js")).read()
PALETTE = open(os.path.join(ROOT, "ui", "webview", "palette-main.ts")).read()


class SourcePins(unittest.TestCase):
    def test_the_bar_no_longer_carries_the_opener_and_the_panel_still_stands(self):
        land = inspect.getsource(km._landing)
        self.assertNotIn("id=rail-errs", land, "the Log's triangle left the bottom bar's action cluster")
        for keep in ("id=rail-refresh", "id=rail-net", "id=rail-gear", "id=rerr-back", "id=rerr-list", "id=rerr-clear"):
            self.assertIn(keep, land, keep + " stays")
        self.assertIn("if(!back||!list)return;", km._LANDING_ERRS_JS, "the center's script no longer needs the bar icon")
        self.assertIn("if(icon)icon.addEventListener('click'", km._LANDING_ERRS_JS)
        self.assertIn("window.__rompOpenErrs=open;", km._LANDING_ERRS_JS)

    def test_the_gear_button_is_the_last_row_of_updates_and_debug_and_opens_the_shell_panel(self):
        self.assertIn("<button id=rs-log-open class=ra-openbtn hidden>Open log<span class=rs-log-n hidden></span></button>", GEAR, "the same button chrome as Token usage analytics")
        self.assertLess(GEAR.index("id=ra-open"), GEAR.index("id=rs-log-open"))
        self.assertLess(GEAR.index("id=rs-log-open"), GEAR.index("id=rsver"), "the section's last row, before the version block")
        self.assertIn("lg.onclick = function () { closeSettings(); try { window.parent.postMessage({ romp: 'openLog' }, '*'); }", GEAR,
                      "the modal closes first, then asks the shell (the panels never stack)")
        self.assertIn("lg.hidden = !web;", GEAR, "web shell only: VS Code's parent has no Log panel")
        self.assertIn("if(m.romp==='openLog'&&window.__rompOpenErrs)window.__rompOpenErrs();", km._LANDING_SETTINGS_JS)

    def test_the_unread_count_rides_the_open_log_button(self):
        # the bar's opener drew the unread count; with it gone the count travels to the gear's button (the manager's
        # review nit, 2026-09-09): the shell posts it on every repaint and on the panel's query, the button renders
        # "Open log · N" with the count in the triangle's red
        k = open(os.path.join(ROOT, "kernel", "kernel.py"), encoding="utf-8").read()
        g = open(os.path.join(ROOT, "ui", "webview", "gear.js"), encoding="utf-8").read()
        css = open(os.path.join(ROOT, "ui", "webview", "gear.css"), encoding="utf-8").read()
        self.assertIn("tell(n);if(!back.hidden)renderList();}", k, "paint() tells the settings iframe")
        self.assertIn("function tell(n){var f=document.getElementById('f-settings');", k, "…the document the gear lives in")
        self.assertIn("postMessage({romp:'logUnseen',n:(n===undefined?unseen():n)},'*')", k)
        self.assertIn("if(m&&m.romp==='logUnseenQuery')tell();", k, "…and answers the panel's query")
        self.assertIn("<button id=rs-log-open class=ra-openbtn hidden>Open log<span class=rs-log-n hidden></span></button>", g)
        self.assertIn("if (m && m.romp === 'logUnseen') window.__rompSetLogCount(m.n);", g)
        self.assertIn("lgn.textContent = n <= 0 ? '' : ' \\u00b7 ' + (n > 9 ? '9+' : String(n));", g)
        self.assertIn("window.parent.postMessage({ romp: 'logUnseenQuery' }, '*');", g, "the panel asks when it opens")
        self.assertIn(".rs-log-n { color: #ff6b6b; font-weight: 600; }", css, "the triangle's red, a status colour")

    def test_the_other_openers_are_unchanged(self):
        self.assertIn('registerCommand({ id: "log.open", title: "Open the log", run: () => { if (w.__rompOpenErrs) w.__rompOpenErrs(); } });', PALETTE)
        self.assertIn("data-act=errs data-keycmd=log.open", inspect.getsource(km._landing), "the mobile bar keeps its opener")


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
const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
await page.goto(cfg.url);
await page.waitForSelector("#rail-gear", { timeout: 20000 });
const feed = page.frames().find((f) => f.url().includes("/settings"));   // the settings iframe hosts the gear (the feed page mounts none)
if (!feed) { console.error("no settings frame"); process.exit(1); }
await feed.waitForSelector(".rs-vermenu-btn", { state: "attached", timeout: 20000 });
const feedPane = page.frames().find((f) => f.url().includes("/feed"));
const feedGear = feedPane ? await feedPane.evaluate(() => !!document.getElementById("rsettings")) : null;
const before = await page.evaluate(() => ({ railErrs: !!document.getElementById("rail-errs"), logHidden: document.getElementById("rerr-back").hidden,
  acts: Array.from(document.querySelectorAll(".rail-acts .rail-act")).map((e) => e.id) }));
await feed.evaluate(() => window.postMessage({ romp: "openSettings" }, "*"));
await feed.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 });
const btn = await feed.evaluate(() => { const b = document.getElementById("rs-log-open"); return { present: !!b, hidden: b ? b.hidden : null }; });
await feed.click("#rs-log-open");
await page.waitForFunction(() => !document.getElementById("rerr-back").hidden, null, { timeout: 8000 });
const after = await page.evaluate(() => ({ logHidden: document.getElementById("rerr-back").hidden, settingsOpen: document.body.classList.contains("settings-open") }));
const modal = await feed.evaluate(() => document.getElementById("rsettings").hidden);
fs.writeSync(1, "RESULT:" + JSON.stringify({ before, btn, after, settingsHidden: modal, feedGear }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedOpener(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="logopener-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        cls.port = _free_port()
        cls.token = "testtok-logopener"
        env = _lab.kernel_env(cls.lab, os.path.join(cls.lab, "claude"), dist, cls.port, cls.token)
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(os.path.join(cls.lab, "kernel.log"), "w"),
                                      stderr=subprocess.STDOUT, env=env)
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
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_the_bar_has_no_opener_and_the_gear_button_opens_the_panel(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (self.port, self.token)}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertFalse(r["before"]["railErrs"], "no Log opener in the bottom bar: %r" % r["before"])
        self.assertTrue(r["before"]["logHidden"], "the panel starts closed")
        self.assertEqual(r["before"]["acts"], ["rail-refresh", "rail-net", "rail-bell", "rail-gear"], "the bar's action cluster keeps its few controls (the bell is present, hidden until it has something): %r" % r["before"]["acts"])
        self.assertEqual(r["btn"], {"present": True, "hidden": False}, "the gear shows Open log in the web shell")
        self.assertIs(r["feedGear"], False, "the feed page mounts no gear of its own (it lives on /settings)")
        self.assertFalse(r["after"]["logHidden"], "the click opened the shell's Log panel")
        self.assertFalse(r["after"]["settingsOpen"], "the settings modal closed first: the panels never stack")
        self.assertTrue(r["settingsHidden"])


if __name__ == "__main__":
    unittest.main()
