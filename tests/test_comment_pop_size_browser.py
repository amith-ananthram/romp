#!/usr/bin/env python3
"""The comment dialog remembers its size and has a maximize control, executed in a real browser (the user
2026-09-10, who kept enlarging the box by hand on every open).

The source pins in ui/webview/comment-pop-size.test.ts say the wiring exists; nothing there proves the box
the user sees behaves — that a ResizeObserver actually fires for a native-grip-style inline resize and for
the JS edge band, that the stored fraction survives a reload into BOTH dialogs, that a phone-sized window
keeps the 8px margins, and above all that a double-click on the title bar reaches the toggle at all: the
whole-box drag captures the pointer on every press, which retargets the click it ends in to the box, so this
is the executed guard the task asked for rather than an assumption. A hermetic kernel serves the real /chat
page with a synthetic session whose transcript carries one seeded thread (the comments store the kernel
itself writes), and the driver walks:
  fresh   — nothing stored: the thread dialog opens at 70% × 60% right-aligned at top 120 (today's geometry),
            the create dialog opens content-sized with NO inline width/height;
  resize  — an inline resize (what the native grip does) and a pull on the east edge band both write the
            fraction; after a reload both dialogs open at the remembered size, in bounds;
  tiny    — a full-size preference on a 480×360 window opens capped inside the 8px margins, reading Restore;
  toggle  — the button maximizes (cap, centered, Restore, data-max, remembered) and restores (the size it had,
            centered, Maximize); a double-click on the title does the same, twice; the create dialog's
            restore drops the inline size and the preference, and a double-click on its name box is exempt.
Skips LOUDLY without the extension deps or a playwright browser (CI installs none); it executes on any dev
box with the extension installed. All fixtures synthetic (the notes-api demo world, session web)."""
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

SID = "aaaaaaaa-1111-2222-3333-444444444444"
TID = "cccccccc-1111-2222-3333-444444444444"     # the seeded thread (tid == its own session id, as the kernel mints them)
U_UUID = "11111111-2222-3333-4444-555555555555"
A_UUID = "22222222-3333-4444-5555-666666666666"
REPLY = "The web session finished the notes-api login flow and every test passes now. Next up is the password reset path."
EXACT = "finished the notes-api login flow"      # the passage the seeded thread highlights
W, H = 1200, 800
TINY_W, TINY_H = 480, 360


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
const KEY = "romp:cmtPopSize";
const page = await browser.newPage({ viewport: { width: cfg.W, height: cfg.H } });

async function load() {
  await page.goto(cfg.chat);
  // 60 s, not 20: the highlight lands after the kernel's comments frame, which on a loaded runner follows the chat frame
  // by tens of seconds (2026-09-11: red twice on CI for a head that changed nothing on this road; under a 20% CPU quota
  // main itself misses 20 s two runs in three and lands by 90 s). The wait is still the event, only its ceiling moved.
  await page.waitForSelector("#content .turn p", { timeout: 60000 });
  await page.waitForSelector("mark.cmt-hl[data-tid]", { timeout: 60000 });   // the seeded thread's highlight has landed
  await page.waitForTimeout(300);
}
const geom = () => page.evaluate(() => {
  const pop = document.getElementById("cmt-pop");
  if (!pop) return null;
  const r = pop.getBoundingClientRect();
  const btn = pop.querySelector(".cmt-max"), x = pop.querySelector(".cmt-x");
  return { mode: pop.dataset.mode, w: pop.offsetWidth, h: pop.offsetHeight,
    left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom),
    styleW: pop.style.width, styleH: pop.style.height, sized: pop.classList.contains("sized"), max: pop.dataset.max || null,
    btn: btn ? { title: btn.title, aria: btn.getAttribute("aria-label"), inHead: !!btn.closest(".cmt-head"),
                 beforeX: btn.nextElementSibling === x, svg: /<svg [^>]*viewBox="0 0 16 16"/.test(btn.innerHTML),
                 frames: (btn.innerHTML.match(/<rect|<path/g) || []).length } : null,
    stored: localStorage.getItem("romp:cmtPopSize"), W: innerWidth, H: innerHeight };
});
async function openThread() {
  await page.click("mark.cmt-hl[data-tid]");
  await page.waitForSelector('#cmt-pop[data-mode="thread"]', { timeout: 10000 });
  await page.waitForTimeout(120);   // the observer's first delivery (a frame) before we read anything
  return geom();
}
async function openCreate() {
  const info = await page.evaluate((t) => {
    const turn = Array.from(document.querySelectorAll(".turn")).find((n) => (n.textContent || "").includes(t));
    const md = turn.querySelector(".md") || turn;
    const walker = document.createTreeWalker(md, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node && !(node.textContent || "").includes("password")) node = walker.nextNode();
    const i = node.textContent.indexOf("password");
    const sel = window.getSelection(); sel.removeAllRanges();
    const r = document.createRange(); r.setStart(node, i); r.setEnd(node, i + 8); sel.addRange(r);
    const box = md.getBoundingClientRect();
    md.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: box.left + 20, clientY: box.top + 10, button: 2 }));
    return { selected: sel.toString(), items: Array.from(document.querySelectorAll(".ctx-menu .ctx-item")).map((i) => i.textContent) };
  }, REPLY_PLACEHOLDER);
  const comment = page.locator(".ctx-menu .ctx-item", { hasText: "Comment" }).first();
  try { await comment.waitFor({ timeout: 5000 }); } catch (e) { console.error("no Comment item: " + JSON.stringify(info)); process.exit(1); }
  await comment.click();
  await page.waitForSelector('#cmt-pop[data-mode="create"]', { timeout: 10000 });
  await page.waitForTimeout(120);
  return geom();
}
async function closePop() {
  await page.click("#cmt-pop .cmt-x");
  await page.waitForSelector("#cmt-pop", { state: "detached", timeout: 5000 });
}
const storedChanged = async (before) => page.waitForFunction((b) => localStorage.getItem("romp:cmtPopSize") !== b, before, { timeout: 5000 });

const out = {};
// ── fresh: nothing stored ────────────────────────────────────────────────────────────────────────
await load();
out.freshThread = await openThread();
await closePop();
out.freshCreate = await openCreate();
await closePop();
// ── resize: the native grip's effect (an inline size) and the JS edge band both write the fraction ──
const g0 = await openThread();
await page.evaluate(() => { const p = document.getElementById("cmt-pop"); p.style.width = "600px"; p.style.height = "300px"; });
await storedChanged(g0.stored);
out.afterInline = await geom();
// the east edge band: 3px inside the right edge, mid-height (well clear of the native grip's corner), pull +100
const g1 = out.afterInline;
await page.mouse.move(g1.right - 3, g1.top + Math.round(g1.h / 2));
await page.mouse.down();
await page.mouse.move(g1.right + 40, g1.top + Math.round(g1.h / 2), { steps: 4 });
await page.mouse.move(g1.right + 97, g1.top + Math.round(g1.h / 2), { steps: 4 });
await page.mouse.up();
await storedChanged(g1.stored);
out.afterEdge = await geom();
await closePop();
// ── reload: both dialogs open at the remembered fraction ─────────────────────────────────────────
await load();
out.rememberedThread = await openThread();
await closePop();
out.rememberedCreate = await openCreate();
await closePop();
// ── tiny: a full-size preference on a phone-sized window stays inside the 8px margins ────────────
await page.evaluate(() => localStorage.setItem("romp:cmtPopSize", JSON.stringify({ w: 1, h: 1 })));
await page.setViewportSize({ width: cfg.TINY_W, height: cfg.TINY_H });
await load();
out.tinyThread = await openThread();
await closePop();
// ── toggle: the button, then the title bar's double-click ────────────────────────────────────────
await page.setViewportSize({ width: cfg.W, height: cfg.H });
await page.evaluate(() => localStorage.removeItem("romp:cmtPopSize"));
await load();
out.beforeMax = await openThread();
await page.click("#cmt-pop .cmt-max");
await page.waitForTimeout(80);
out.maxed = await geom();
await page.click("#cmt-pop .cmt-max");
await page.waitForTimeout(80);
out.restored = await geom();
await page.dblclick("#cmt-pop .cmt-title");
await page.waitForTimeout(80);
out.dblMaxed = await geom();
await page.dblclick("#cmt-pop .cmt-title");
await page.waitForTimeout(80);
out.dblRestored = await geom();
await closePop();
// the create dialog: content-sized → maximize → (a double-click on its name box is exempt) → restore drops the size
await page.evaluate(() => localStorage.removeItem("romp:cmtPopSize"));
out.createBefore = await openCreate();
await page.click("#cmt-pop .cmt-max");
await page.waitForTimeout(80);
out.createMaxed = await geom();
await page.dblclick("#cmt-pop .cmt-name");
await page.waitForTimeout(80);
out.createAfterNameDbl = await geom();
await page.click("#cmt-pop .cmt-max");
await page.waitForTimeout(80);
out.createRestored = await geom();
await closePop();

fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
""".replace("REPLY_PLACEHOLDER", json.dumps(REPLY))


class ServedCommentPopSize(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served dialog needs them")
        cls.lab = tempfile.mkdtemp(prefix="comment-pop-size-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            shutil.rmtree(cls.lab, ignore_errors=True)
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cls.state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        claude = os.path.join(cls.lab, "claude")
        # the kernel finds a session's transcript under Claude's project dir: EVERY non-alphanumeric char of the
        # realpath becomes '-' (jd._proj_dir)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # a CLOSED turn: an open one would invite the boot reconcile to resume it — no real CLI here
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": U_UUID, "parentUuid": None,
                        "timestamp": "2026-09-05T00:00:00.000Z", "sessionId": SID,
                        "message": {"role": "user", "content": "Where do we stand on the login flow?"}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": A_UUID, "parentUuid": U_UUID,
                        "timestamp": "2026-09-05T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-fable-5-1",
                                    "content": [{"type": "text", "text": REPLY}],
                                    "stop_reason": "end_turn"}}) + "\n")
        # ONE thread anchored on the reply — the store the kernel's own create writes (_comment_create's row shape),
        # so the chat paints its highlight and a click on it opens the THREAD dialog. Its conversation never existed
        # (no thread transcript here), which the kernel reports as an unreachable thread; the geometry is the same.
        now = int(time.time())
        Path(cls.state, "comments", SID + ".json").write_text(json.dumps({"threads": [
            {"tid": TID, "sid": TID, "anchorUuid": A_UUID, "cutUuid": A_UUID, "anchorT": now - 900, "exact": EXACT,
             "status": "open", "createdT": now - 600, "lastSeenT": now - 600, "name": "web-comment-1", "color": "#e8b220"}]}))
        cls.port = _free_port()
        cls.token = "testtok-cmtpopsize"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = open(os.path.join(cls.lab, "kernel.log"), "w")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=cls.klog, stderr=subprocess.STDOUT, env=env)
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

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
                       "W": W, "H": H, "TINY_W": TINY_W, "TINY_H": TINY_H}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served dialog needs one (CI installs none)")
        klog = open(os.path.join(self.lab, "kernel.log")).read()[-2000:]
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + klog)
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    @staticmethod
    def _frac(g):
        return json.loads(g["stored"]) if g["stored"] else None

    def _button(self, g, label):
        b = g["btn"]
        self.assertIsNotNone(b, "the maximize button exists: %r" % g)
        self.assertTrue(b["inHead"] and b["beforeX"], "in .cmt-head, right before the ×: %r" % b)
        self.assertTrue(b["svg"], "a 16-unit line glyph: %r" % b)
        self.assertEqual((b["title"], b["aria"]), (label, label), "its label reads %s: %r" % (label, b))
        self.assertEqual(b["frames"], 1 if label == "Maximize" else 2, "one frame = maximize, two = restore: %r" % b)

    def _inside(self, g, margin=8):
        self.assertGreaterEqual(g["left"], margin, "left margin: %r" % g)
        self.assertGreaterEqual(g["top"], margin, "top margin: %r" % g)
        self.assertLessEqual(g["right"], g["W"] - margin, "right margin: %r" % g)
        self.assertLessEqual(g["bottom"], g["H"] - margin, "bottom margin: %r" % g)

    def test_the_dialog_remembers_its_size_and_maximizes_from_the_button_and_the_title_bar(self):
        o = self._drive()
        # ── fresh: today's geometry, byte for byte ──
        g = o["freshThread"]
        self.assertEqual(g["mode"], "thread")
        self.assertIsNone(g["stored"], "nothing stored before any resize")
        self.assertEqual((g["styleW"], g["styleH"]), ("%dpx" % round(W * 0.7), "%dpx" % round(H * 0.6)), "70%% × 60%% inline: %r" % g)
        self.assertEqual((g["w"], g["h"]), (840, 480))
        self.assertEqual((g["left"], g["top"]), (W - 840 - 8, 120), "right-aligned at top 120: %r" % g)
        self.assertIsNone(g["max"]); self._button(g, "Maximize")
        g = o["freshCreate"]
        self.assertEqual(g["mode"], "create")
        self.assertEqual((g["styleW"], g["styleH"]), ("", ""), "the create dialog carries NO inline size: %r" % g)
        self.assertIsNone(g["stored"], "opening writes nothing")
        self.assertFalse(g["sized"], "content-sized: the quote clamp still applies")
        self.assertIsNone(g["max"]); self._button(g, "Maximize")
        # ── resize: both paths write the fraction of the window ──
        g = o["afterInline"]
        self.assertEqual((g["w"], g["h"]), (600, 300))
        self.assertEqual(self._frac(g), {"w": 0.5, "h": 0.375}, "an inline resize (the native grip's effect) is remembered: %r" % g)
        g = o["afterEdge"]
        self.assertEqual(g["h"], 300, "an east pull leaves the height: %r" % g)
        self.assertEqual(g["w"], 700, "the edge band pulled +100: %r" % g)
        self.assertEqual(g["left"], o["afterInline"]["left"], "east anchored west: %r" % g)
        self.assertTrue(g["sized"])
        f = self._frac(g)
        self.assertAlmostEqual(f["w"], 700 / W, places=4, msg="the edge band's pull is remembered too: %r" % g)
        self.assertAlmostEqual(f["h"], 0.375, places=4)
        # ── reload: both dialogs open at the remembered fraction, in bounds ──
        g = o["rememberedThread"]
        self.assertEqual((g["w"], g["h"]), (700, 300), "the thread dialog opens at the remembered size: %r" % g)
        self.assertEqual((g["left"], g["top"]), (W - 700 - 8, 120), "still right-aligned at top 120: %r" % g)
        self.assertTrue(g["sized"], "an applied preference is an expressed size")
        self._inside(g); self.assertIsNone(g["max"]); self._button(g, "Maximize")
        g = o["rememberedCreate"]
        self.assertEqual(g["mode"], "create")
        self.assertEqual((g["w"], g["h"]), (700, 300), "the create dialog shares the preference: %r" % g)
        self.assertTrue(g["sized"]); self._inside(g)
        # ── tiny: a full-size preference stays inside the 8px margins, and reads as maximized ──
        g = o["tinyThread"]
        self.assertEqual((g["W"], g["H"]), (TINY_W, TINY_H))
        self.assertEqual((g["w"], g["h"]), (round(TINY_W * 0.94), round(TINY_H * 0.9)), "capped at 94vw × 90vh: %r" % g)
        self._inside(g)
        self.assertEqual(g["max"], "1", "the cap IS maximized, computed at open: %r" % g)
        self._button(g, "Restore")
        # ── toggle: the button ──
        g = o["beforeMax"]
        self.assertEqual((g["w"], g["h"]), (840, 480), "the preference was cleared: today's default again: %r" % g)
        g = o["maxed"]
        self.assertEqual((g["w"], g["h"]), (1128, 720), "the cap: %r" % g)
        self.assertEqual((g["left"], g["top"]), (36, 40), "centered: %r" % g)
        self.assertEqual(g["max"], "1"); self.assertTrue(g["sized"]); self._button(g, "Restore")
        self.assertEqual(self._frac(g), {"w": 0.94, "h": 0.9}, "maximize is remembered like any resize: %r" % g)
        g = o["restored"]
        self.assertEqual((g["w"], g["h"]), (840, 480), "back to the size it had: %r" % g)
        self.assertEqual((g["left"], g["top"]), (180, 160), "kept centered: %r" % g)
        self.assertIsNone(g["max"]); self._button(g, "Maximize")
        self.assertEqual(self._frac(g), {"w": 0.7, "h": 0.6}, "the restored size is what is remembered: %r" % g)
        # ── toggle: the title bar's double-click reaches the same toggle (the drag's pointer capture notwithstanding) ──
        g = o["dblMaxed"]
        self.assertEqual((g["w"], g["h"], g["max"]), (1128, 720, "1"), "a double-click on the title maximizes: %r" % g)
        self._button(g, "Restore")
        g = o["dblRestored"]
        self.assertEqual((g["w"], g["h"], g["max"]), (840, 480, None), "a second double-click restores: %r" % g)
        self._button(g, "Maximize")
        # ── the create dialog: content-sized → cap → (name box exempt) → content-sized again, preference gone ──
        g = o["createBefore"]
        self.assertEqual((g["styleW"], g["styleH"]), ("", ""), "content-sized with the preference cleared: %r" % g)
        g = o["createMaxed"]
        self.assertEqual((g["w"], g["h"], g["max"]), (1128, 720, "1"), "the create dialog maximizes too: %r" % g)
        self.assertEqual((g["left"], g["top"]), (36, 40))
        g = o["createAfterNameDbl"]
        self.assertEqual((g["w"], g["h"], g["max"]), (1128, 720, "1"), "a double-click on the name box is exempt: %r" % g)
        g = o["createRestored"]
        self.assertEqual((g["styleW"], g["styleH"]), ("", ""), "restore drops the inline size — content-sized again: %r" % g)
        self.assertIsNone(g["stored"], "…and the preference with it: %r" % g)
        self.assertFalse(g["sized"]); self.assertIsNone(g["max"]); self._button(g, "Maximize")
        self._inside(g)


if __name__ == "__main__":
    unittest.main()
