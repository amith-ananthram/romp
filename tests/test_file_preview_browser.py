#!/usr/bin/env python3
"""The file preview popover on the real chat page (T351 stage 1, the user 2026-09-11): hovering a local file link pops up
a card with the rendered head of the file, or the section a `path#slug` link names, near-instantly.

A hermetic kernel serves one synthetic session whose reply links six paths: a markdown guide (its head), the guide's
`#fold-rules` section (with a [[wikilink]] and a callout inside it), a `#no-such-section` anchor (the head with a one-line
note), a small PNG (the figure at its natural size, capped to the card), a Python file (a highlighted head), a `.env`
(a secrets-shaped name: text plus "open", no request), a markdown-named symlink to that `.env` (judged by its target:
text plus "open") and an absolute path outside the session's folder and the user's home (text plus "open", no request);
the route itself, asked by hand for a refused path, answers 403 with the reason and no text. The acceptance includes
latency: the card stamps the time from the dwell's end to its rendered content; a cached markdown slice (the guide,
warmed on the pusher's path) must render within 250 ms, and a cold one (a file whose time the test rewrites after the
build, so the hover misses the cache) is measured and reported (PV_LATENCY names a file for the numbers). The cold file
carries every remote-loading shape (img src and srcset, a picture's source, a video's poster and src, an audio, an SVG
image, backslash and tab spellings, a tab INSIDE the value, a markdown image): the card shows their alt text and nothing else, and the browser
makes no request to another origin during the whole run. Asserted, dark and light: nothing opens before the dwell and the card is up after
it; leaving closes it after the grace; Escape closes it; the keyboard's focus opens it; the kernel's verdict rides each
link as data-preview and the refused links make NO request; the section card shows the section alone with the grammar
additions rendered; the missing anchor falls back to the head with its note; the image, code and text-only cards have
their kinds; "open" on the section card lands the file viewer scrolled to that heading; GET /perf counts the slices
(the warmed guide a hit, the code a miss). With PV_SHOTS=<dir> the driver writes the five cards in both themes. Skips
LOUDLY without the extension deps or a Playwright browser; the CI extension job installs Chromium and runs served files
with ROMP_SERVED_TESTS_REQUIRE=1, which turns any skip into a failure there. SYNTHETIC fixtures only (session web, the
notes-api demo world, placeholder uuids, an invented guide)."""
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zlib
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID = "aaaaaaaa-1111-2222-3333-444444444444"
U_UUID = "11111111-2222-3333-4444-555555555555"
A_UUID = "22222222-3333-4444-5555-666666666666"
GUIDE = """# Guide

The guide's intro paragraph, the first thing a reader sees.

## Install

Run the installer, then restart the notes-api service.

## Fold Rules

A fold keeps the summary and hides the mechanics; see [[Glossary]] for the words.

> [!NOTE] Folds survive re-renders.

## Later

The part after the fold rules.
"""
APP_PY = "def main():\n    print('notes-api')\n\n\nif __name__ == '__main__':\n    main()\n"


def _png(w=2, h=2, rgb=(60, 120, 200)):
    """A tiny valid PNG (RGB, no filter) without a binary fixture."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


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
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
let fileRequests = 0;
page.on("request", (r) => { if (/\/file\?/.test(r.url())) fileRequests++; });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
await page.click('#tabs .tab[data-id="' + cfg.sid + '"]');
await page.waitForFunction(() => document.querySelectorAll("#content .file-uri-link").length >= 9, null, { timeout: 20000 });
await page.mouse.move(900, 720); await page.waitForTimeout(300);
const CARD = "#file-preview-pop";
const shown = () => page.evaluate(() => { const p = document.getElementById("file-preview-pop"); return !!p && getComputedStyle(p).display !== "none"; });
const links = await page.evaluate(() => Array.from(document.querySelectorAll("#content .file-uri-link")).map((a) => ({ text: a.textContent, path: a.dataset.path, frag: a.dataset.frag || null, preview: a.dataset.preview || null, rel: a.dataset.rel || null })));
const sel = (path, frag) => '#content .file-uri-link[data-path="' + path + '"]' + (frag ? '[data-frag="' + frag + '"]' : ":not([data-frag])");
const card = () => page.evaluate(() => {
  const p = document.getElementById("file-preview-pop"); if (!p || getComputedStyle(p).display === "none") return null;
  const body = p.querySelector(".fp-body"); const img = p.querySelector(".fp-img"); const code = p.querySelector(".fp-code code");
  const r = p.getBoundingClientRect();
  return { title: (p.querySelector(".fp-title") || {}).textContent ?? null, sub: (p.querySelector(".fp-sub") || {}).textContent ?? null,
           note: (p.querySelector(".fp-note") || {}).textContent ?? null, kind: body ? body.className : null, loader: !!p.querySelector(".rl-in"),
           text: body ? body.textContent.trim().slice(0, 400) : null, html: body ? body.innerHTML.slice(0, 2000) : null, hasOpen: !!p.querySelector(".fp-open"),
           img: img ? { src: img.getAttribute("src"), w: img.getBoundingClientRect().width, natural: img.naturalWidth, inCard: img.getBoundingClientRect().width <= r.width } : null,
           codeClass: code ? code.className : null, box: { w: r.width, h: r.height, top: r.top, left: r.left },
           renderMs: p.dataset.renderMs ? Number(p.dataset.renderMs) : null, sliceHit: p.dataset.sliceHit ?? null,
           theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
});
const offOrigin = [];   // every http(s) request the page makes to another origin: there must be none (a hover is not a beacon)
page.on("request", (r) => { const u = r.url(); if (/^https?:/.test(u) && !u.startsWith("http://127.0.0.1:")) offOrigin.push(u); });
const hoverCard = async (path, frag) => {
  const before = fileRequests;
  const t0 = Date.now();
  await page.hover(sel(path, frag));
  await page.waitForTimeout(150);
  const early = await shown();
  await page.waitForFunction(() => { const p = document.getElementById("file-preview-pop"); return !!p && getComputedStyle(p).display !== "none" && !!p.querySelector(".fp-body"); }, null, { timeout: 5000 });
  const ms = Date.now() - t0;
  await page.waitForTimeout(150);
  const c = await card();
  return { early, ms, requests: fileRequests - before, card: c };
};
const shot = async (name) => { if (!cfg.shots) return; fs.mkdirSync(cfg.shots, { recursive: true }); const b = await (await page.$(CARD)).boundingBox(); await page.screenshot({ path: cfg.shots + "/" + name + ".png", clip: { x: Math.max(0, b.x - 40), y: Math.max(0, b.y - 60), width: Math.min(1100, b.width + 80), height: Math.min(760, b.height + 100) } }); };
const leave = async () => { await page.mouse.move(900, 720); await page.waitForTimeout(400); return await shown(); };
// the cold read: the build warmed docs/cold.md, so its time is rewritten before each hover (a new mtime is a new cache key)
let coldBump = 0;
const bumpCold = () => { coldBump += 1; const s = Date.now() / 1000 + 5 * coldBump; fs.utimesSync(cfg.cold, s, s); };
const out = { links, themes: {} };
for (const theme of ["dark", "light"]) {
  if (theme === "light") { await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(200); }
  const t = {};
  t.head = await hoverCard("docs/guide.md", null); await shot("romp_chat-file-preview-markdown-" + theme); t.headHidden = await leave();
  t.section = await hoverCard("docs/guide.md", "fold-rules"); await shot("romp_chat-file-preview-section-" + theme); t.sectionHidden = await leave();
  t.missing = await hoverCard("docs/guide.md", "no-such-section"); t.missingHidden = await leave();
  t.image = await hoverCard("plots/figure.png", null); await shot("romp_chat-file-preview-image-" + theme); t.imageHidden = await leave();
  t.code = await hoverCard("src/app.py", null); await shot("romp_chat-file-preview-code-" + theme); t.codeHidden = await leave();
  bumpCold(); t.cold = await hoverCard("docs/cold.md", null); t.coldHidden = await leave();
  t.outside = await hoverCard(cfg.outside, null); await shot("romp_chat-file-preview-textonly-" + theme); t.outsideHidden = await leave();
  t.secret = await hoverCard("docs/.env", null); t.secretHidden = await leave();
  out.themes[theme] = t;
}
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(200);
// the keyboard's route: focus opens after the dwell; Escape closes
await page.focus(sel("docs/guide.md", null)); await page.waitForTimeout(150); out.focusEarly = await shown();
await page.waitForFunction(() => { const p = document.getElementById("file-preview-pop"); return !!p && getComputedStyle(p).display !== "none" && !!p.querySelector(".fp-body"); }, null, { timeout: 5000 });
out.focusCard = await card();
await page.keyboard.press("Escape"); await page.waitForTimeout(100); out.escapeHidden = !(await shown());
// "open" on the section card lands the viewer scrolled to the heading
await hoverCard("docs/guide.md", "fold-rules");
await page.click(CARD + " .fp-open");
await page.waitForSelector("#romp-fileview", { timeout: 10000 });
await page.waitForFunction(() => !!document.querySelector('#romp-fileview [id="md-fold-rules"]'), null, { timeout: 10000 });   // the viewer's heading ids wear md- (file-view.ts)
await page.waitForTimeout(400);
out.opened = await page.evaluate(() => {
  const box = document.querySelector("#romp-fileview .fileview"); const h = document.querySelector('#romp-fileview [id="md-fold-rules"]');
  const b = box.getBoundingClientRect(), r = h.getBoundingClientRect();
  return { heading: h.textContent.trim(), top: r.top, boxTop: b.top, boxBottom: b.bottom, inView: r.top >= b.top - 1 && r.bottom <= b.bottom + 1, cardHidden: !(document.getElementById("file-preview-pop") && getComputedStyle(document.getElementById("file-preview-pop")).display !== "none") };
});
out.offOrigin = offOrigin;
out.perf = await page.evaluate(async () => { const r = await fetch("/perf", { credentials: "same-origin" }); const j = await r.json(); return j.fileSlice || null; });
// the route by hand (the client never asks for these): a refused path answers 403 with why, and never the text
out.refused = await page.evaluate(async (paths) => {
  const out = {};
  for (const [name, p] of Object.entries(paths)) {
    const r = await fetch("/file?path=" + encodeURIComponent(p) + "&slice=1&sid=" + encodeURIComponent(document.querySelector("#tabs .tab.active[data-id]").dataset.id), { credentials: "same-origin" });
    let j = null; try { j = await r.json(); } catch (e) { j = null; }
    out[name] = { status: r.status, why: j && j.why, allowed: j && j.allowed, hasText: !!(j && j.text) };
  }
  return out;
}, { outside: cfg.outside, secret: "docs/.env", dressed: "docs/report.md", missing: "docs/nope.md" });
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


# nothing in this file may resolve on the network: the strip must remove every one of these before the browser sees them
COLD = ("# Cold\n\nThe later note: the build warmed it, and the test rewrites its time so the hover reads it cold.\n\n"
        '<img srcset="https://remote.invalid/a.png 1x" alt="srcset-only">\n\n'
        '<picture><source srcset="https://remote.invalid/b.png"><img src="plots/figure.png" alt="pic"></picture>\n\n'
        '<video poster="https://remote.invalid/p.png" src="https://remote.invalid/v.mp4"></video>\n\n'
        '<audio src="https://remote.invalid/a.mp3"></audio>\n\n'
        '<svg><image href="https://remote.invalid/s.png"/></svg>\n\n'
        '<img src="\\\\remote.invalid/c.png" alt="backslashes">\n\n'
        '<img src="\thttps://remote.invalid/d.png" alt="tabbed">\n\n'
        '<img src="/\t/remote.invalid/f.png" alt="tab-inside">\n\n'
        "![markdown-remote](https://remote.invalid/e.png)\n")


class ServedFilePreview(unittest.TestCase):
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served lab needs them; CI's extension job has them and requires this file to run")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served lab needs one; CI's extension job installs Chromium and requires this file to run")
        cls.lab = tempfile.mkdtemp(prefix="file-preview-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        for d in ("docs", "plots", "src"):
            os.makedirs(os.path.join(cwd, d), exist_ok=True)
        os.makedirs(os.path.join(cls.lab, "outside"), exist_ok=True)
        Path(cwd, "docs", "guide.md").write_text(GUIDE)
        Path(cwd, "docs", ".env").write_text("SETTING=not-a-real-value\n")
        Path(cwd, "docs", "cold.md").write_text(COLD)
        os.symlink(os.path.join(cwd, "docs", ".env"), os.path.join(cwd, "docs", "report.md"))   # an innocent name over a secret
        Path(cwd, "plots", "figure.png").write_bytes(_png())
        Path(cwd, "src", "app.py").write_text(APP_PY)
        cls.outside = os.path.join(cls.lab, "outside", "notes.md")
        cls.cold = os.path.join(cwd, "docs", "cold.md")
        Path(cls.outside).write_text("# Outside\n\nnot under the session's folder\n")
        Path(state, "names", SID).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        reply = ("Read docs/guide.md first, then the rule at docs/guide.md#fold-rules (docs/guide.md#no-such-section is not a section).\n\n"
                 "The plot is at plots/figure.png and the code at src/app.py; the secrets live in docs/.env (and docs/report.md is a link to them); "
                 "the later note is docs/cold.md; notes outside the project sit at %s." % cls.outside)
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": U_UUID, "parentUuid": None, "timestamp": "2026-09-05T00:00:00.000Z", "sessionId": SID,
                        "message": {"role": "user", "content": "Where do I start with the notes-api docs?"}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": A_UUID, "parentUuid": U_UUID, "timestamp": "2026-09-05T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-opus-5", "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"}}) + "\n")
        cls.port, cls.token = _free_port(), "testtok-filepreview"
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

    def test_a_hover_previews_the_file_or_its_section_after_the_dwell_and_a_refused_path_is_text_with_no_request(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": SID, "outside": self.outside, "cold": self.cold,
                       "shots": os.environ.get("PV_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served lab needs one; CI's extension job installs Chromium and requires this file to run")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        # the links, as the kernel's verdict dressed them
        by = {}
        for l in r["links"]:
            by[(l["path"], l["frag"])] = l
        self.assertEqual(by[("docs/guide.md", None)]["preview"], "markdown")
        self.assertEqual(by[("docs/guide.md", "fold-rules")]["preview"], "markdown", "the anchored link is the same file: %r" % by)
        self.assertEqual(by[("docs/guide.md", "fold-rules")]["text"], "docs/guide.md#fold-rules", "the slug moved into the link, the token reads whole")
        self.assertEqual(by[("plots/figure.png", None)]["preview"], "image")
        self.assertEqual(by[("src/app.py", None)]["preview"], "code")
        self.assertIsNone(by[("docs/.env", None)]["preview"], "a secrets-shaped name: no preview")
        self.assertIsNone(by[("docs/report.md", None)]["preview"], "a markdown name over a secret: judged by what it points at, no preview")
        self.assertIsNone(by[(self.outside, None)]["preview"], "outside the session's folder and home: no preview")
        self.assertEqual(by[("docs/cold.md", None)]["preview"], "markdown")
        latency = {"cached": [], "cold": [], "code": []}      # dwell end → rendered card, ms, per theme
        for theme in ("dark", "light"):
            t = r["themes"][theme]
            for name in ("head", "section", "missing", "image", "code", "cold", "outside", "secret"):
                h = t[name]
                self.assertFalse(h["early"], "%s/%s: nothing before the dwell" % (theme, name))
                self.assertGreaterEqual(h["ms"], 300, "%s/%s: the card came up only after the dwell: %r ms" % (theme, name, h["ms"]))
                self.assertIsNotNone(h["card"], "%s/%s: the card is up" % (theme, name))
                self.assertEqual(h["card"]["theme"], theme)
                self.assertTrue(h["card"]["hasOpen"], "%s/%s: the way to the full file" % (theme, name))
                self.assertGreaterEqual(h["card"]["box"]["w"], 300); self.assertGreaterEqual(h["card"]["box"]["h"], 120)
                self.assertFalse(t[name + "Hidden"], "%s/%s: leaving closes the card after the grace" % (theme, name))
            head, sec, miss = t["head"]["card"], t["section"]["card"], t["missing"]["card"]
            self.assertEqual((head["title"], head["sub"], head["note"]), ("guide.md", None, None))
            self.assertIn("fp-markdown", head["kind"]); self.assertIn("<h1", head["html"]); self.assertIn("intro paragraph", head["text"])
            self.assertEqual((sec["title"], sec["sub"]), ("guide.md", "#fold-rules"))
            self.assertIn("fp-section", sec["kind"]); self.assertIn("A fold keeps the summary", sec["text"])
            self.assertNotIn("intro paragraph", sec["text"], "the section alone"); self.assertNotIn("after the fold rules", sec["text"])
            self.assertIn("Glossary", sec["text"]); self.assertNotIn("[[", sec["text"], "the wikilink rendered as its text")
            self.assertIn("md-callout-label", sec["html"], "the callout's label rendered")
            # latency (the acceptance, the user 2026-09-11): the guide was warmed on the pusher's path so its slice is a hit,
            # and a cached markdown slice renders well inside the near-instant band; the cold one (its time rewritten after
            # the build) is a miss, measured and reported, not bounded (a disk read on a shared box is not ours to promise)
            cold = t["cold"]["card"]
            self.assertIn("fp-markdown", cold["kind"]); self.assertIn("later note", cold["text"])
            self.assertEqual(head["sliceHit"], "1", "%s: the guide's slice came from the cache" % theme)
            self.assertEqual(cold["sliceHit"], "0", "%s: the rewritten file was read cold" % theme)
            self.assertIsNotNone(head["renderMs"]); self.assertIsNotNone(cold["renderMs"])
            # the remote loads in the cold file: stripped on the inert DOM, so the card shows alt text and nothing else
            self.assertNotIn("remote.invalid", cold["html"], "%s: no remote URL reaches the card: %r" % (theme, cold["html"]))
            for gone in ("<video", "<audio", "<source", "<image"):
                self.assertNotIn(gone, cold["html"], "%s: %s stripped" % (theme, gone))
            for alt in ("srcset-only", "backslashes", "tabbed", "tab-inside", "markdown-remote"):
                self.assertIn(alt, cold["text"], "%s: the remote image reads as its alt text" % theme)
            self.assertIn('alt="pic"', cold["html"], "the picture's local img stays")
            self.assertLessEqual(head["renderMs"], 250, "%s: dwell end to rendered card, cached: %r ms" % (theme, head["renderMs"]))
            latency["cached"].append(head["renderMs"]); latency["cold"].append(cold["renderMs"]); latency["code"].append(t["code"]["card"]["renderMs"])
            self.assertIn("fp-markdown", miss["kind"]); self.assertEqual(miss["sub"], None)
            self.assertEqual(miss["note"], 'no section "no-such-section" in this file; its head instead'); self.assertIn("intro paragraph", miss["text"])
            img = t["image"]["card"]
            self.assertEqual(img["title"], "figure.png"); self.assertIn("fp-image", img["kind"])
            self.assertTrue(img["img"] and "/file?path=" in img["img"]["src"] and img["img"]["inCard"], "the figure from the bytes route, inside the card: %r" % img["img"])
            code = t["code"]["card"]
            self.assertEqual(code["title"], "app.py"); self.assertIn("fp-code", code["kind"]); self.assertIn("print", code["text"])
            self.assertIn("language-python", code["codeClass"] or "", "highlighted as Python: %r" % code["codeClass"])
            for name, expect_title in (("outside", "notes.md"), ("secret", ".env")):
                c = t[name]["card"]
                self.assertEqual(c["title"], expect_title); self.assertIn("fp-text", c["kind"])
                self.assertIn("shown as text", c["note"] or ""); self.assertEqual(t[name]["requests"], 0, "%s/%s: no request at all" % (theme, name))
            self.assertGreaterEqual(t["head"]["requests"], 1, "a text preview is one fetch")
        # the keyboard's route and Escape
        if os.environ.get("PV_LATENCY"):
            with open(os.environ["PV_LATENCY"], "w") as f:
                json.dump(latency, f)
        self.assertFalse(r["focusEarly"]); self.assertIsNotNone(r["focusCard"]); self.assertEqual(r["focusCard"]["title"], "guide.md")
        self.assertTrue(r["escapeHidden"], "Escape closes the card")
        # "open" lands the viewer on the section
        o = r["opened"]
        self.assertEqual(o["heading"], "Fold Rules"); self.assertTrue(o["inView"], "the heading is in the viewer's view: %r" % o)
        self.assertTrue(o["cardHidden"], "the card closed when the viewer opened")
        # the counters: the guide was warmed on the pusher's path (a hit at the first hover); the code file was a miss
        pf = r["perf"]
        self.assertIsNotNone(pf, "GET /perf carries fileSlice")
        self.assertGreaterEqual(pf["warm"], 1, "the markdown the reply linked was warmed ahead of the hover: %r" % pf)
        self.assertGreaterEqual(pf["hit"], 1, "…so its first slice was a hit: %r" % pf)
        self.assertGreaterEqual(pf["miss"], 1, "the code file is read on demand: %r" % pf)
        self.assertGreater(pf["bytes"], 0)
        # the route refuses by hand: 403 with why, no text
        self.assertEqual(r["offOrigin"], [], "no request left this kernel during the whole run (the review: a hover must not be a beacon)")
        rf = r["refused"]
        self.assertEqual((rf["outside"]["status"], rf["outside"]["why"], rf["outside"]["hasText"]), (403, "outside the session's folder and your home", False), rf)
        self.assertEqual((rf["secret"]["status"], rf["secret"]["why"]), (403, "a secrets-shaped name"), rf)
        self.assertEqual((rf["dressed"]["status"], rf["dressed"]["why"]), (403, "a secrets-shaped name"), "the symlink is judged by its target: %r" % rf)
        self.assertEqual((rf["missing"]["status"], rf["missing"]["why"]), (403, "not a file"), rf)


if __name__ == "__main__":
    unittest.main()
