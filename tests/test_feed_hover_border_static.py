#!/usr/bin/env python3
"""T270 (the user 2026-09-08): hovering a card in the feed's group-by-session view bolds its coloured border
by colour and shadow, and that must NOT move the card's text, nor the card below. Their recording showed the hovered
card's text block shifting 1 px down and 1 px right and the card under it dropping 1 px.

The served guard drives the real /feed page from a hermetic kernel with a synthetic payload (one session,
two cards in the same column, grouped mode), hovers the first card, and compares the CONTENT boxes: the
hovered card's text block and the second card must sit exactly where they sat at rest, while the border
colour bolds and the shadow lifts (the hover cue itself stays). The regression pins are the computed-style
equalities (border width, margin, padding) and the card's OUTER box; the content-box rects are consistency
checks that also held under the earlier border-grow rule. Skips LOUDLY without the extension deps or a Playwright
browser (CI installs none). FEED_HOVER_SHOTS=<path-prefix> writes rest + hover screenshots. All fixtures
synthetic (the notes-api demo world).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)

SID = "aaaaaaaa-1111-2222-3333-888888888888"
TOP = "dddddddd-1111-2222-3333-000000000001"
BELOW = "dddddddd-1111-2222-3333-000000000002"


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
const page = await browser.newPage({ viewport: { width: 1100, height: 520 }, deviceScaleFactor: 2 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
await page.goto(cfg.feed);
await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
await page.waitForTimeout(600);
const now = Math.floor(Date.now() / 1000);
const ask = (itemId, text, t) => ({ itemId, sid: cfg.sid, name: "web", color: { bg: "#54B204", fg: "#ffffff" },
  text, t, live: true, turnId: "turn-" + itemId.slice(-1), trgb: [84, 178, 4], column: "working", tree: [] });
const payload = { type: "feed", asks: [ask(cfg.top, "notes-api: draft the search index", now - 120), ask(cfg.below, "notes-api: tune the retry curve", now - 60)],
                  sessions: [{ sid: cfg.sid, name: "web" }], order: [cfg.sid] };
await page.evaluate((m) => { window.dispatchEvent(new MessageEvent("message", { data: m })); }, payload);
await page.waitForSelector(`[data-key="a:${cfg.top}"]`, { timeout: 10000 });
await page.waitForSelector(`[data-key="a:${cfg.below}"]`, { timeout: 10000 });
await page.waitForTimeout(400);
const measure = () => page.evaluate((cfg) => {
  const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x * 100) / 100, y: Math.round(r.y * 100) / 100, w: Math.round(r.width * 100) / 100, h: Math.round(r.height * 100) / 100 }; };
  const top = document.querySelector(`[data-key="a:${cfg.top}"]`), below = document.querySelector(`[data-key="a:${cfg.below}"]`);
  const cs = getComputedStyle(top);
  return { topCard: rect(top), topMain: rect(top.querySelector(".fitem-main")), topText: rect(top.querySelector(".fitem-main *")),
           belowCard: rect(below), belowMain: rect(below.querySelector(".fitem-main")),
           border: cs.borderTopWidth + " " + cs.borderLeftWidth, margin: cs.marginTop + " " + cs.marginLeft, padding: cs.paddingTop + " " + cs.paddingLeft,
           color: cs.borderTopColor, boxShadow: cs.boxShadow, cls: top.className };
}, cfg);
await page.mouse.move(1000, 500);
await page.waitForTimeout(250);
const rest = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-rest.png", clip: { x: 0, y: 0, width: 420, height: 320 } });
await page.hover(`[data-key="a:${cfg.top}"] .fitem-main`);
await page.waitForSelector(`[data-key="a:${cfg.top}"].focused`, { timeout: 5000 });   // the class lands after the 120 ms hover-intent debounce (event-keyed wait)
await page.waitForTimeout(200);   // the box-shadow transition is 0.12 s
const hover = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-hover.png", clip: { x: 0, y: 0, width: 420, height: 320 } });
fs.writeSync(1, "RESULT:" + JSON.stringify({ rest, hover, errors }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedHoverKeepsTextStill(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="feedhover-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        os.makedirs(os.path.join(cls.lab, "xdg", "romp"), exist_ok=True)
        cls.port = _free_port()
        cls.token = "testtok-feedhover"
        env = _lab.kernel_env(cls.lab, os.path.join(cls.lab, "claude"), dist, cls.port, cls.token)
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(os.path.join(cls.lab, "kernel.log"), "w"), stderr=subprocess.STDOUT, env=env)
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

    def test_hovering_a_card_bolds_its_border_colour_without_moving_its_text_or_the_card_below(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"feed": "http://127.0.0.1:%d/feed?token=%s" % (self.port, self.token), "sid": SID,
                       "top": TOP, "below": BELOW, "shots": os.environ.get("FEED_HOVER_SHOTS", "")}, f)
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
        rest, hover = r["rest"], r["hover"]
        self.assertEqual(r.get("errors"), [], "the page threw nothing: %r" % r.get("errors"))
        # the hover cue is on: the border colour bolds to full alpha and the shadow lifts — paint only
        self.assertIn("focused", hover["cls"], "the hover class landed: %r" % hover["cls"])
        self.assertNotEqual(rest["color"], hover["color"], "the hover bolds the border colour: rest %r hover %r" % (rest["color"], hover["color"]))
        self.assertNotEqual(rest["boxShadow"], hover["boxShadow"], "…and lifts the shadow")
        # THE FIX (T270): no LAYOUT property changes with the hover — red on main, where the border grew 2→3px with a
        # compensating margin -1px and the rendered text still moved a device pixel on the user's display
        self.assertEqual(rest["border"], hover["border"], "border width unchanged: rest %r hover %r" % (rest["border"], hover["border"]))
        self.assertEqual(rest["margin"], hover["margin"], "margin unchanged: rest %r hover %r" % (rest["margin"], hover["margin"]))
        self.assertEqual(rest["padding"], hover["padding"], "padding unchanged: rest %r hover %r" % (rest["padding"], hover["padding"]))
        self.assertEqual(rest["topCard"], hover["topCard"], "the card's outer box is unchanged — paint only (red on the border-grow rule): rest %r hover %r" % (rest["topCard"], hover["topCard"]))
        # …and so nothing inside or below moved — the text block and the card below keep their exact boxes
        self.assertEqual(rest["topMain"], hover["topMain"], "the hovered card's content box did not move: rest %r hover %r" % (rest["topMain"], hover["topMain"]))
        self.assertEqual(rest["topText"], hover["topText"], "…nor its first text block: rest %r hover %r" % (rest["topText"], hover["topText"]))
        self.assertEqual(rest["belowCard"], hover["belowCard"], "the card below did not move: rest %r hover %r" % (rest["belowCard"], hover["belowCard"]))
        self.assertEqual(rest["belowMain"], hover["belowMain"], "…nor its content: rest %r hover %r" % (rest["belowMain"], hover["belowMain"]))


if __name__ == "__main__":
    unittest.main()
