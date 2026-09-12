#!/usr/bin/env python3
"""The glossary on the real chat page (T351 stage 2, the user 2026-09-11): a synthetic group glossary for the lab's session
(its own name's file, the fallback when a session has no tag group) links the coinages in an assistant message and in the
user's own words, whole-word and longest-first, aliases and plurals included, never inside code or a path link; a
retired term greys; a `link: first` term links once per message; hovering a term shows the term card from the index
with no request; clicking opens the glossary in the viewer at the term's heading; a rewrite of the file changes the
links on the next push (the pusher stats the file on every cycle; the lab wakes one with a transcript record, the event
it wakes on in use); GET /glossary/<term> answers and 404s. Screenshots dark and light (PV_SHOTS names the folder).
Synthetic fixtures only: placeholder UUIDs, a hermetic state root, the invented notes-api world."""
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
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402

SID = "11111111-2222-3333-4444-555555555555"
FIX = json.loads(Path(HERE, "fixtures", "glossary_grammar.json").read_text())
REPLY = ("I tesselled the fixes from your review and pushed the tessel head; the quill was security, and the quill again. "
         "The spar on `tessel` stays as code, and docs/guide.md#tessel is a path, not a term. Two tessels landed. "
         "The unverified docs/widget/tessel.md and the host example.com/tessel/y stay plain too.")
USER = "Did the tessel cover the second quill?"


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


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
let requests = 0;
page.on("request", (r) => { if (/\/glossary\/|\/file\?/.test(r.url())) requests++; });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
await page.click('#tabs .tab[data-id="' + cfg.sid + '"]');
await page.waitForFunction(() => document.querySelectorAll("#content .term-link").length >= 5, null, { timeout: 20000 });
await page.waitForTimeout(300);
const links = () => page.evaluate(() => Array.from(document.querySelectorAll("#content .term-link")).map((s) => ({
  text: s.textContent, term: s.dataset.term, frag: s.dataset.frag, path: s.dataset.path, retired: s.classList.contains("term-retired"),
  inUser: !!s.closest(".turn-user, .user, .bubble-user, [class*=user]") })));
const out = { links: await links() };
out.codeLinks = await page.evaluate(() => document.querySelectorAll("#content code .term-link, #content a .term-link, #content .file-uri-link .term-link").length);
out.pathLink = await page.evaluate(() => { const a = document.querySelector('#content .file-uri-link[data-path="docs/guide.md"]'); return a ? { text: a.textContent, frag: a.dataset.frag } : null; });
// the term card: hover "tessel head" (the multi-word term), no request
const before = requests;
const shot = async (name) => { if (!cfg.shots) return; fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/" + name + ".png", clip: { x: 0, y: 60, width: 1100, height: 520 } }); };
await page.hover('#content .term-link[data-term="tessel-head"]');
await page.waitForFunction(() => { const p = document.getElementById("file-preview-pop"); return !!p && getComputedStyle(p).display !== "none" && !!p.querySelector(".fp-body"); }, null, { timeout: 5000 });
await page.waitForTimeout(150);
out.card = await page.evaluate(() => { const p = document.getElementById("file-preview-pop"); const b = p.querySelector(".fp-body");
  return { title: p.querySelector(".fp-title")?.textContent, sub: p.querySelector(".fp-sub")?.textContent, kind: b?.className, text: b?.textContent?.trim().slice(0, 300), open: p.querySelector(".fp-open")?.textContent }; });
out.cardRequests = requests - before;
await shot("romp_chat-glossary-dark");
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(200);
await shot("romp_chat-glossary-light");
await page.evaluate(() => document.body.classList.remove("theme-light"));
await page.mouse.move(900, 700); await page.waitForTimeout(400);
// a click opens the glossary in the viewer at the heading
await page.click('#content .term-link[data-term="tessel"]');
await page.waitForSelector("#romp-fileview", { timeout: 10000 });
await page.waitForFunction(() => !!document.querySelector("#romp-fileview #md-tessel, #romp-fileview [id='md-tessel']"), null, { timeout: 10000 });
out.viewer = await page.evaluate(() => ({ heading: document.querySelector("#romp-fileview #md-tessel")?.textContent, title: (document.querySelector("#romp-fileview .fv-title, #romp-fileview .fileview-title, #romp-fileview header") || {}).textContent }));
await page.keyboard.press("Escape"); await page.waitForTimeout(300);
// the route, by hand
out.route = await page.evaluate(async (sid) => {
  const get = async (t) => { const r = await fetch("/glossary/" + encodeURIComponent(t) + "?sid=" + encodeURIComponent(sid), { credentials: "same-origin" }); return { status: r.status, body: await r.json() }; };
  return { tessel: await get("Tessel"), alias: await get("review tessel"), missing: await get("nonesuch") };
}, cfg.sid);
// a rewrite of the file: a new term links on the next PUSH (the pusher stats the file on every cycle it runs; a
// glossary edit alone wakes no cycle, and nothing here polls, so the session speaks once more: a transcript record
// is the event the pusher wakes on, exactly as it would be in use)
fs.appendFileSync(cfg.glossary, "\n## bramblet\n\nAn invented noun, appended after the page loaded.\n\n- plain words: an appended entry\n- also: bramblets\n- status: unconfirmed\n- registered: 2026-09-11 by web\n");
const t = Date.now() / 1000 + 5; fs.utimesSync(cfg.glossary, t, t);
fs.appendFileSync(cfg.transcript, JSON.stringify({ type: "user", uuid: "u-2", parentUuid: "a-1", timestamp: new Date().toISOString(), sessionId: cfg.sid,
  message: { role: "user", content: "And the bramblet you appended, did it link?" } }) + "\n");
await page.waitForFunction(() => Array.from(document.querySelectorAll("#content .term-link")).some((s) => s.dataset.term === "bramblet"), null, { timeout: 20000 });
out.afterRewrite = await links();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedGlossary(unittest.TestCase):
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served lab needs them; CI's extension job has them and requires this file to run")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served lab needs one; CI's extension job installs Chromium and requires this file to run")
        cls.lab = tempfile.mkdtemp(prefix="glossary-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(os.path.join(cwd, "docs"), exist_ok=True)
        Path(cwd, "docs", "guide.md").write_text("# Guide\n\n## Tessel\n\nThe kernel's chat tessel.\n")
        claude = os.path.join(cls.lab, "claude")
        os.makedirs(os.path.join(claude, "glossaries"), exist_ok=True)
        cls.glossary = os.path.join(claude, "glossaries", "web.md")     # the session's own name: the fallback when it has no tag group
        cls.transcript = None
        Path(cls.glossary).write_text(FIX["text"])
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(state, "names", SID).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True, "model": "claude-opus-5", "liveModel": "Opus 5"}))
        cls.transcript = os.path.join(proj, SID + ".jsonl")
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": "u-1", "parentUuid": None, "timestamp": "2026-09-05T00:00:00.000Z", "sessionId": SID,
                        "message": {"role": "user", "content": USER}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": "a-1", "parentUuid": "u-1", "timestamp": "2026-09-05T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-opus-5", "content": [{"type": "text", "text": REPLY}], "stop_reason": "end_turn"}}) + "\n")
        cls.port, cls.token = _free_port(), "testtok-glossary"
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

    def test_terms_link_where_written_the_card_needs_no_request_and_a_click_lands_the_viewer_on_the_heading(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": SID, "glossary": self.glossary, "transcript": self.transcript,
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
        texts = [(l["text"], l["term"]) for l in r["links"]]
        # the assistant's words: tesselled (alias), tessel head (longest first: one term), quill once (first), tessels (plural);
        # the user's words: tessel and quill (its own message, its own first)
        self.assertIn(("tesselled", "tessel"), texts); self.assertIn(("tessel head", "tessel-head"), texts); self.assertIn(("tessels", "tessel"), texts)
        self.assertEqual(sum(1 for t, k in texts if k == "quill"), 2, "quill once per message, in two messages: %r" % texts)
        self.assertNotIn(("spar", "spar"), texts, "link: off links nothing"); self.assertFalse(any(t == "tessel" and False for t, _ in texts))
        self.assertEqual(r["codeLinks"], 0, "never inside code or a link")
        self.assertEqual(sum(1 for t, k in texts if k == "tessel"), 3, "the user's tessel, tesselled and tessels; none from inside the unverified path or the host: %r" % texts)
        self.assertEqual(r["pathLink"], {"text": "docs/guide.md#tessel", "frag": "tessel"}, "the path link's absorbed section is not a term's")
        for l in r["links"]:
            self.assertTrue(l["path"].endswith("glossaries/web.md")); self.assertEqual(l["frag"], l["term"])
        self.assertTrue(any(l["inUser"] for l in r["links"]), "the user's own words link too")
        # the card, from the index: no request
        c = r["card"]
        self.assertEqual((c["title"], c["open"]), ("tessel head", "Open glossary")); self.assertIn("fp-term", c["kind"] or "")
        self.assertIn("unconfirmed", c["sub"]); self.assertIn("web", c["sub"])
        self.assertIn("The head a review's fixes land on", c["text"]); self.assertIn("plain words:", c["text"])
        self.assertEqual(r["cardRequests"], 0, "the term card is filled from the index, no fetch")
        # the click: the viewer on the heading
        self.assertEqual((r["viewer"]["heading"] or "").strip().lower(), "tessel")
        # the route
        rt = r["route"]
        self.assertEqual((rt["tessel"]["status"], rt["tessel"]["body"]["title"], rt["tessel"]["body"]["anchor"]), (200, "tessel", "tessel"))
        self.assertTrue(rt["tessel"]["body"]["markdown"].startswith("## tessel"))
        self.assertEqual(rt["alias"]["body"]["title"], "tessel"); self.assertEqual(rt["missing"]["status"], 404); self.assertTrue(rt["missing"]["body"]["tried"])
        # the rewrite: the new term links on the next frame
        self.assertTrue(any(l["term"] == "bramblet" for l in r["afterRewrite"]), "a rewrite of the file reaches the page on the next push, without a reload or a poll: %r" % r["afterRewrite"])


if __name__ == "__main__":
    unittest.main()
