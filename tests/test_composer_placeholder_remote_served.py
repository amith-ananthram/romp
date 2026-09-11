#!/usr/bin/env python3
"""T328 (the user 2026-09-10): the composer's resting placeholder painted a REMOTE session's whole "host:name" bold in the
identity colour, where every other surface shows the host as the quiet .host-prefix span (host-prefix.ts). On the real
/chat page of a hub kernel with a SECOND hermetic kernel checked in as host TESTHOST (the mobile handshake, no ssh),
the remote session's tab is picked and the composer's overlay is read: it says "Message TESTHOST:api…" with the host
in a .host-prefix span whose computed dress (class, italic, colour, weight) equals the tab label's own host span, and
only the name bold in the identity colour; the host span is not bold. Red with the split removed: the overlay holds no
.host-prefix span and the bold name reads the whole "TESTHOST:api". With PH_SHOTS=<dir> the driver writes screenshots
of the message box beside its tab, dark and light. Skips LOUDLY without the extension deps or a Playwright browser.
SYNTHETIC fixtures only (host TESTHOST, session api, the notes-api demo world)."""
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
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID = "bbbbbbbb-1111-2222-3333-444444444444"
COLOR = ("#64b5f6", "#0c1a2e")


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
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
// the remote host's tab appears once the hub reports the check-in up and the relay socket is open
const remoteTab = page.locator("#tabs .tab", { hasText: "TESTHOST" }).first();
try { await remoteTab.waitFor({ timeout: 45000 }); }
catch (e) {
  const st = await page.evaluate(() => ({ tabs: (document.getElementById("tabs") || {}).textContent }));
  console.error("no remote tab: " + JSON.stringify(st)); process.exit(1);
}
await remoteTab.click();
await page.waitForFunction(() => { const ph = document.getElementById("composer-ph"); return !!(ph && getComputedStyle(ph).display !== "none" && ph.querySelector(".composer-ph-name")); }, null, { timeout: 30000 });
await page.mouse.move(700, 400);   // off the strip: no tab tooltip in the shots
await page.waitForTimeout(600);
// the dress of a host span: the class, the italic, the ink, the weight (the four the tab label's rule sets), and its words
// (defined inside the page's evaluate, where getComputedStyle lives)
const measure = () => page.evaluate(() => {
  const dress = (n) => { if (!n) return null; const cs = getComputedStyle(n); return { cls: n.className, text: n.textContent, style: cs.fontStyle, color: cs.color, weight: cs.fontWeight, size: cs.fontSize }; };
  const tab = document.querySelector("#tabs .tab.active[data-id]");
  const ph = document.getElementById("composer-ph"); const nm = ph && ph.querySelector(".composer-ph-name");
  const ta = document.getElementById("composer-input");
  return { active: tab ? tab.dataset.id : null, tabLabel: tab ? (tab.querySelector(".tab-label") || tab).textContent : null,
           tabHost: dress(tab && tab.querySelector(".tab-label .host-prefix")), tabBg: tab ? tab.style.getPropertyValue("--chip-bg") : null,
           phText: ph ? ph.textContent : null, phHost: dress(ph && ph.querySelector(".host-prefix")), phHostInsideName: !!(nm && nm.querySelector(".host-prefix")),
           phName: dress(nm), phNameColor: nm ? nm.style.color : null, phFaded: !!ph && ph.classList.contains("name-faded"),
           bodyBg: getComputedStyle(document.body).backgroundColor, nativePlaceholder: ta ? ta.placeholder : null,
           theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
});
const out = {};
out.dark = await measure();
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/romp_chat-composer-remote-host-dark.png", fullPage: false }); }
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
// the overlay's name colour is computed against the page background at paint time (T335): a keystroke and its undo repaint
// it under the light theme, as any edit of the box would
await page.focus("#composer-input"); await page.keyboard.type("x"); await page.keyboard.press("Backspace"); await page.waitForTimeout(300);
out.light = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "/romp_chat-composer-remote-host-light.png", fullPage: false });
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


def _kernel(lab, name, port, token, records=None, sid=None):
    """Boot one hermetic kernel: its own state root, dist, and (optionally) one session with a transcript and a colour."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    cwd = os.path.join(lab, name, "proj")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
    if records is not None:
        Path(state, "names", sid).write_text("api\t%s\t%s\t%s\n" % (cwd, COLOR[0], COLOR[1]))
        Path(state, "sdk", sid + ".json").write_text(json.dumps(
            {"sid": sid, "name": "api", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": sid, "alive": True, "model": "claude-opus-5", "liveModel": "Opus 5"}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    env = _lab.kernel_env(os.path.join(lab, name), claude, os.path.join(lab, "dist"), port, token,
                          ROMP_HOST_NAME=name.upper())
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, log
        except Exception:
            time.sleep(0.5)
    proc.kill(); proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


class ServedComposerPlaceholderRemoteHost(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:          # a skip OR a failure: never leave a kernel or a lab behind
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
        cls.lab = tempfile.mkdtemp(prefix="composer-ph-remote-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            shutil.rmtree(cls.lab, ignore_errors=True)
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.procs = []
        t0 = int(time.time()) - 900
        recs = [
            {"type": "user", "timestamp": iso(t0), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": SID,
             "message": {"role": "user", "content": "how should the notes-api retry loop back off?"}},
            {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": SID,
             "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "Use exponential backoff with a jitter of ten percent."}]}},
        ]
        # the REMOTE kernel owns the session; the HUB has none of its own and shows the remote's through the relay
        cls.rport, cls.rtoken = _free_port(), "testtok-remote-ph"
        cls.hport, cls.htoken = _free_port(), "testtok-hub-ph"
        try:
            rp, cls.rlog = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, records=recs, sid=SID)
            cls.procs.append(rp)
            hp, cls.hlog = _kernel(cls.lab, "hub", cls.hport, cls.htoken)
            cls.procs.append(hp)
        except unittest.SkipTest:
            cls.tearDownClass()
            raise
        # the check-in handshake a mobile machine makes through its reverse forward: the hub records the peer like an
        # attached remote (no ssh of its own) and probes it on the port given, here the remote's own
        body = json.dumps({"host": "TESTHOST", "kernelPort": cls.rport, "busPort": _free_port(), "token": cls.rtoken}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/checkin?token=%s" % (cls.hport, cls.htoken), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ans = json.loads(resp.read().decode())
        if not ans.get("ok"):
            cls.tearDownClass()
            raise unittest.SkipTest("the hub refused the check-in: %r" % ans)
        for _ in range(60):   # the hub's supervisor probes the peer and reports it up; the browser dials only then
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (cls.hport, cls.htoken), timeout=3) as r2:
                    rows = json.loads(r2.read().decode()).get("tunnels") or []
            except Exception:
                rows = []
            row = next((t for t in rows if t.get("host") == "TESTHOST"), None)
            if row and row.get("status") == "up" and row.get("hasToken"):
                break
            time.sleep(0.5)
        else:
            cls.tearDownClass()
            raise unittest.SkipTest("the hub never reported the checked-in peer up: %r" % (rows,))

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, "procs", []):
            try:
                p.kill(); p.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_the_placeholder_shows_the_remote_host_as_the_tab_label_does(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.hport, self.htoken), "shots": os.environ.get("PH_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
                         + "\nhub:\n" + open(self.hlog).read()[-1500:] + "\nremote:\n" + open(self.rlog).read()[-800:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        for theme in ("dark", "light"):
            m = r[theme]
            self.assertEqual(m["theme"], theme)
            self.assertEqual(m["active"], "TESTHOST:" + SID, "the remote session's tab is the active one: %r" % m)
            self.assertEqual(m["tabLabel"], "TESTHOST:api", "the tab label reads host:name: %r" % m)
            self.assertIsNotNone(m["tabHost"], "the tab label's host is its own span: %r" % m)
            self.assertEqual(m["tabHost"]["text"], "TESTHOST:")
            # the overlay: "Message TESTHOST:api…", the host as the SAME quiet span the tab wears, the name alone bold in the colour
            self.assertTrue((m["phText"] or "").startswith("Message TESTHOST:api"), "the overlay names the session with its host: %r" % m["phText"])
            self.assertIsNotNone(m["phHost"], "the overlay's host is a .host-prefix span (the split): %r" % m)
            self.assertEqual(m["phHost"]["text"], "TESTHOST:")
            self.assertFalse(m["phHostInsideName"], "the host span is a sibling of the bold name, never inside it")
            for k in ("cls", "style", "color", "weight"):
                self.assertEqual(m["phHost"][k], m["tabHost"][k], "the overlay's host wears the tab label's %s in %s: %r vs %r" % (k, theme, m["phHost"], m["tabHost"]))
            self.assertEqual(m["phHost"]["style"], "italic"); self.assertEqual(m["phHost"]["weight"], "400", "the host is quiet, not bold")
            self.assertEqual(m["phName"]["text"], "api", "only the name is the bold run: %r" % m["phName"])
            self.assertEqual(m["phName"]["weight"], "600")
            # …in the session's identity colour at an at-rest tab label's fade (T335): every channel moved from the identity
            # colour toward the page background, never the full colour, and the overlay carries the strip's at-rest class so
            # the host span fades with the name (the exact level is proven against a real at-rest label in
            # tests/test_session_name_served.py)
            rgb = lambda c: tuple(int(x) for x in re.findall(r"\d+", c or "")[:3])
            ident, name, bg = (100, 181, 246), rgb(m["phNameColor"]), rgb(m["bodyBg"])
            if theme == "dark":
                self.assertNotEqual(name, ident, "faded, not the full identity colour: %r" % m["phNameColor"])
            else:
                self.assertEqual(name, ident, "the light theme: the strip's fade is a no-op on a light page, so the name keeps its colour: %r" % m["phNameColor"])
            for i in range(3):
                lo, hi = sorted((ident[i], bg[i]))
                self.assertTrue(lo <= name[i] <= hi, "channel %d of the name lies between the identity colour and the page background in %s: %r %r %r" % (i, theme, ident, name, bg))
            self.assertTrue(m["phFaded"], "the overlay carries the strip's at-rest class")
            self.assertNotEqual(m["phHost"]["color"], m["phNameColor"], "the host does not wear the identity colour")
            self.assertTrue((m["nativePlaceholder"] or "").startswith("Message this session"), "the native placeholder beneath stays the plain resting text for assistive tech")


if __name__ == "__main__":
    unittest.main()
