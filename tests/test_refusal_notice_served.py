#!/usr/bin/env python3
"""T279: a safeguards refusal the CLI retried on a fallback model renders in the served chat pane as a
SOURCED notice through the one notice builder (render.ts notice()), never as the user's bubble.

The executed guard drives the real /chat page against a hermetic kernel whose one session's transcript
carries the refusal turn as the CLI writes it (the refused call's assistant record with a fallback block,
the fallback model's reply, the system/model_refusal_fallback record with the category, the API's
explanation and the scope). Asserted on the page: one notice card in the refusal variant with the
"safeguards" source label; a gist naming both models (prettified) and the category; the fold collapsed by default,
holding the API's explanation and the CLI's own line, opening on a head click; the notice's own rail dot;
the notice placed before the fallback model's reply; and the notice text in NO user bubble. Screenshots
when T279_SHOTS names a directory. Skips LOUDLY without the extension deps or a Playwright browser (CI
installs none). SYNTHETIC fixtures only."""
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
from datetime import datetime, timezone
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
NOTICE = ("The model's safeguards flagged this message. Switched to a fallback model. "
          "Send feedback with /feedback.")
CATEGORY = "synthetic-category"
EXPLANATION = "a synthetic explanation of the refusal"
REPLY = "The README covers install and usage."


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
const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
try { await page.waitForSelector(".turn.turn-user", { timeout: 20000 }); }
catch (e) {
  const st = await page.evaluate(() => ({ turns: Array.from(document.querySelectorAll(".turn")).map((t) => t.className.slice(5, 40)),
    text: (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 200) }));
  console.error("no user turn rendered: " + JSON.stringify(st)); process.exit(1);
}
try { await page.waitForSelector('.notice-head[data-nkey^="notice:mswap:"]', { timeout: 20000 }); }
catch (e) {
  const st = await page.evaluate(() => ({ turns: Array.from(document.querySelectorAll(".turn")).map((t) => t.className.slice(5, 60)),
    text: (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 400) }));
  console.error("no refusal notice card rendered: " + JSON.stringify(st)); process.exit(1);
}
await page.waitForTimeout(400);
const measure = () => page.evaluate((reply) => {
  // the notice is keyed by the record's uuid (openFolds "notice:mswap:<uuid>"): that key is its stable hook
  const cards = Array.from(document.querySelectorAll('.notice-head[data-nkey^="notice:mswap:"]')).map((h) => h.closest(".notice"));
  const card = cards[0];
  const turn = card.closest(".turn");
  const turns = Array.from(document.querySelectorAll(".turn"));
  const replyTurn = turns.find((t) => !t.classList.contains("turn-user") && (t.textContent || "").includes(reply)) || null;
  return {
    cards: cards.length,
    src: ((card.querySelector(".notice-src") || {}).textContent || "").trim(),
    head: ((card.querySelector(".notice-gist") || {}).textContent || "").trim(),
    open: card.classList.contains("notice-open"),
    collapsible: card.classList.contains("notice-collapsible"),
    body: ((card.querySelector(".notice-body") || {}).textContent || "").replace(/\s+/g, " ").trim(),
    bodyVisible: (() => { const b = card.querySelector(".notice-body"); return b ? b.getBoundingClientRect().height > 0 : false; })(),
    dot: !!turn.querySelector(".dot.notice-dot"),
    warn: turn.classList.contains("notice-sev-warn"),
    turnClasses: turn.className,
    idxNotice: turns.indexOf(turn), idxReply: replyTurn ? turns.indexOf(replyTurn) : -1,
    userTexts: Array.from(document.querySelectorAll(".turn.turn-user")).map((t) => (t.textContent || "").replace(/\s+/g, " ").trim()),
    slimLines: document.querySelectorAll(".modelswap-line, .turn-modelswap").length,
  };
}, cfg.reply);
const closed = await measure();
const shot = async (name) => { if (!cfg.shots) return; fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/t279-refusal-notice" + name, fullPage: false }); };
await shot(".png");
await page.click('.notice-head[data-nkey^="notice:mswap:"]');
await page.waitForTimeout(300);
const opened = await measure();
await shot("-open.png");
fs.writeSync(1, "RESULT:" + JSON.stringify({ closed, opened }) + "\n");
await browser.close();
process.exit(0);
"""


def refusal_turn_records(t0):
    """The refusal-turn shape as the CLI writes it (camelCase on disk): the refused call leaves an
    assistant record whose only content is a fallback block, the fallback model replies, and the system
    record, stamped with the retry start and parented onto the reply, carries the category, the API's
    explanation and the scope."""
    return [
        {"type": "user", "timestamp": iso(t0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
         "sessionId": SID, "message": {"role": "user", "content": "summarize the notes-api README"}},
        {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "afb", "parentUuid": "u1", "sessionId": SID,
         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                     "content": [{"type": "fallback", "from": {"model": "claude-fable-5"},
                                  "to": {"model": "claude-opus-5"}}]}},
        {"type": "assistant", "timestamp": iso(t0 + 45), "uuid": "a1", "parentUuid": "afb", "sessionId": SID,
         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": REPLY}]}},
        {"type": "system", "subtype": "model_refusal_fallback", "timestamp": iso(t0 + 5), "uuid": "sfb",
         "parentUuid": "a1", "sessionId": SID, "direction": "retry", "trigger": "refusal", "level": "warning",
         "scope": "session", "content": NOTICE, "originalModel": "claude-fable-5", "fallbackModel": "claude-opus-5",
         "requestId": "req_synthetic", "apiRefusalCategory": CATEGORY, "apiRefusalExplanation": EXPLANATION,
         "retractedMessageUuids": [], "refusedUserMessageUuid": "u1"},
    ]


class ServedRefusalNotice(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        # a cheap browser probe BEFORE the bundle and the kernel: a box with the deps but no browser (CI's
        # shape) skips here instead of paying a build and a boot to learn it at the driver
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="refusal-notice-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(cls.state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(cls.state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-fable-5", "liveModel": "Fable 5"}))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        # the kernel finds a session's transcript under Claude's project dir: EVERY non-alphanumeric char of the
        # realpath becomes '-' (jd._proj_dir)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        cls.transcript = os.path.join(proj, SID + ".jsonl")
        Path(cls.transcript).write_text("".join(json.dumps(r) + "\n" for r in refusal_turn_records(t0)))
        cls.port = _free_port()
        cls.token = "testtok-refusalnotice"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            cls.kernel.wait()
            shutil.rmtree(cls.lab, ignore_errors=True)
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_the_refusal_renders_as_a_sourced_notice_card_and_never_as_the_users_bubble(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "reply": REPLY,
                       "shots": os.environ.get("T279_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
                         + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        c, o = r["closed"], r["opened"]
        self.assertEqual(c["cards"], 1, "one notice card for the one record: %r" % c)
        self.assertEqual(c["src"], "safeguards", "the source label: %r" % c)
        for part in ("Fable 5", "Opus 5", "(%s)" % CATEGORY, "safeguards"):
            self.assertIn(part, c["head"], "the head: both models prettified and the category: %r" % c)
        self.assertNotIn("this reply came from", c["head"], "session scope: the session's model was swapped")
        self.assertTrue(c["collapsible"] and not c["open"], "collapsed by default: %r" % c)
        self.assertFalse(c["bodyVisible"], "the fold is hidden until the head is clicked: %r" % c)
        self.assertIn("Fable 5 → Opus 5", c["body"], "the fold restates the swap, so a truncated head is recoverable: %r" % c)
        self.assertIn(EXPLANATION, c["body"], "the fold holds the API's explanation: %r" % c)
        self.assertIn(NOTICE, c["body"], "and the CLI's own line, verbatim: %r" % c)
        self.assertTrue(c["dot"], "the notice's own rail dot: %r" % c)
        self.assertTrue(c["warn"], "the warn severity carries the heads-up amber (rail + dot): %r" % c)
        self.assertIn("turn-notice", c["turnClasses"])
        self.assertEqual(c["slimLines"], 0, "the retired slim rail line is gone")
        self.assertGreaterEqual(c["idxReply"], 0, "the fallback model's reply is on the page: %r" % c)
        self.assertLess(c["idxNotice"], c["idxReply"], "the notice reads BEFORE the fallback model's reply: %r" % c)
        for ut in c["userTexts"]:
            self.assertNotIn(NOTICE, ut, "the notice is never the user's bubble: %r" % c["userTexts"])
            self.assertNotIn(EXPLANATION, ut, "the explanation is never the user's bubble: %r" % c["userTexts"])
        self.assertEqual(len(c["userTexts"]), 1, "one user bubble: the prompt, once: %r" % c["userTexts"])
        self.assertTrue(o["open"] and o["bodyVisible"], "a head click opens the fold: %r" % o)


if __name__ == "__main__":
    unittest.main()
