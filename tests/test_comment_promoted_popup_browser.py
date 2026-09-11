#!/usr/bin/env python3
"""A comment thread that became its own session opens READABLE (the user 2026-09-10, with a screenshot).

Once a thread is broken out, its popup carries the head "Now its own session: <name>" and one action,
"Open the session" — and the kernel ships it with no messages and no events (the discussion lives in the
session now: _comments_frame's `[] if status == "promoted"`). On main that emptiness broke the layout three
ways: the ONE-LINE quote ran some three hundred pixels tall (the box opens at its fixed 70%×60% geometry
and the .sized quote rule handed the quote a share of the free room, with an empty list taking the rest);
the middle of the box was a void (the empty .cmt-msgs flexed to fill it, pinning the action to the bottom);
and the action wore the composer's SEND-glyph dress (.cmt-send: a ~36px square, 16px, no border), so
"Open the session" wrapped one word per line, in a font bigger than the popup's, at the bottom-left.

The executed guard: a hermetic kernel serves the real /chat page; a synthetic parent session carries one
PROMOTED thread and one OPEN thread in the kernel's OWN comment store (STATE/comments/<sid>.json — the file
_comments_frame projects onto the wire; never a hand-poked DOM), each thread's session registered the way
promote / create leave them; the driver clicks each thread's highlight and measures the popup as painted.
Asserted for the promoted thread: the quote stands within two line-heights of its text; the action is a
real <button> in .cmt-actions wearing the shared .cmt-act dress — one line, a box no narrower than its
text, a border, a font no bigger than the head's and exactly the open thread's action buttons'; the
content stacks from the top (the action follows the quote within a few lines, no void between); and the
box keeps its open geometry (never shrunk to content — comment-popover-parity's contract). The OPEN thread
is the control: its chat-parity list, its composer and its .cmt-act buttons render as before.

Skips LOUDLY without the extension deps or a Playwright browser (CI installs none); it executes on any
dev box with the extension installed. SYNTHETIC fixtures only: session `web`, the thread sessions `web-2`
and `web-comment-1`, the notes-api demo world, placeholder uuids."""
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

SID = "aaaaaaaa-1111-2222-3333-444444444444"          # the parent session, `web`
PROMOTED = "bbbbbbbb-1111-2222-3333-444444444444"     # a thread broken out into the session `web-2`
OPEN = "cccccccc-1111-2222-3333-444444444444"         # an ordinary open thread, `web-comment-1`
REPLY = ("Use exponential backoff with a jitter of ten percent. "
         "Cap the delay at two minutes so a stuck notes-api worker never sleeps through its shift.")
PROMOTED_EXACT = "Cap the delay at two minutes"
OPEN_EXACT = "exponential backoff"
VIEW_W, VIEW_H = 1400, 900


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, sid=SID):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "sessionId": sid, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, sid=SID):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": sid,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: cfg.w, height: cfg.h } });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
await page.locator(`#tabs .tab[data-id="${cfg.sid}"]`).first().click();   // the PARENT's transcript, where the highlights live
// the parent's transcript renders and the kernel's comments frame lands: the highlights wrap both passages
// (attached, not visible — every session's view stays in the DOM, hidden when not active; the click below
// auto-waits for the visible one)
// 60 s: the highlights land after the kernel's comments frame, tens of seconds behind the chat frame on a loaded runner
// (2026-09-11: red on CI at 30 s for a head that changed nothing on this road)
for (const tid of [cfg.promoted, cfg.open]) await page.waitForSelector(`mark.cmt-hl[data-tid="${tid}"]`, { state: "attached", timeout: 60000 });

// the popup as painted — rects and computed styles, read-only
const MEASURE = () => {
  const pop = document.getElementById("cmt-pop");
  if (!pop) return null;
  const cs = (n) => getComputedStyle(n);
  const rect = (n) => { if (!n) return null; const r = n.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, top: r.top, bottom: r.bottom }; };
  const textRange = (n) => { const r = document.createRange(); r.selectNodeContents(n); return r; };
  const lines = (n) => n ? textRange(n).getClientRects().length : 0;              // the line boxes its text occupies
  const textW = (n) => n ? textRange(n).getBoundingClientRect().width : 0;         // the width its text needs
  const btn = (b) => ({ text: b.textContent, tag: b.tagName, cls: b.className, inActions: !!b.closest(".cmt-actions"),
    fs: cs(b).fontSize, borderStyle: cs(b).borderTopStyle, borderW: cs(b).borderTopWidth, lines: lines(b), rect: rect(b), textW: textW(b) });
  const quote = pop.querySelector(":scope > .cmt-quote");
  const msgs = pop.querySelector(".cmt-msgs");
  const title = pop.querySelector(".cmt-title");
  const act = pop.querySelector('[data-act="cmtopensession"]');
  return {
    status: pop.dataset.status, sized: pop.classList.contains("sized"), pop: rect(pop),
    title: title ? title.textContent : "", titleFs: title ? cs(title).fontSize : "", popFs: cs(pop).fontSize,
    quote: quote ? { text: quote.textContent, rect: rect(quote), fs: cs(quote).fontSize, lh: cs(quote).lineHeight, lines: lines(quote), flexGrow: cs(quote).flexGrow } : null,
    msgs: msgs ? { rect: rect(msgs), kids: msgs.childElementCount, display: cs(msgs).display, hasTurn: !!msgs.querySelector(".turn") } : null,
    composer: !!pop.querySelector(".cmt-input"),
    act: act ? btn(act) : null,
    acts: Array.from(pop.querySelectorAll(".cmt-actions button")).map(btn),
    note: Array.from(pop.querySelectorAll(":scope > .cmt-note")).map((n) => n.textContent),
  };
};
async function openThread(tid, waitTurn) {
  await page.locator(`mark.cmt-hl[data-tid="${tid}"]`).first().click();
  await page.waitForFunction((t) => document.getElementById("cmt-pop")?.dataset.tid === t, tid, { timeout: 10000 });
  if (waitTurn) await page.waitForSelector("#cmt-pop .cmt-msgs .turn", { timeout: 30000 }).catch(() => {});   // the chat-parity render
  // the ResizeObserver's first delivery (the .sized class) and any frame in flight: two paints, then a beat
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  await page.waitForTimeout(300);
  return await page.evaluate(MEASURE);
}
const out = {};
out.promoted = await openThread(cfg.promoted, false);
await page.click("#cmt-pop .cmt-x");
await page.waitForSelector("#cmt-pop", { state: "detached", timeout: 5000 });
out.open = await openThread(cfg.open, true);
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


def px(v):
    """A computed length ("9.84px") as a float; "normal" line-height reads as 0 (callers fall back)."""
    m = re.match(r"^([0-9.]+)px$", str(v or ""))
    return float(m.group(1)) if m else 0.0


class ServedPromotedPopup(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served popup needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served popup needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="cmt-promoted-popup-")
        try:
            cls._boot()
        except BaseException:          # a skip OR a failure: never leave a kernel or a lab behind
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        # the kernel finds a transcript under Claude's project dir: every non-alphanumeric char of the realpath
        # becomes '-' (jd._proj_dir) — the paste test's lesson about pytest's temp roots
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        now = int(time.time())
        t0 = now - 900
        parent = [uline(t0, "how should the notes-api retry loop back off?", "u1"),
                  aline(t0 + 5, REPLY, "a1", parent="u1")]          # a CLOSED turn: nothing invites a resume

        def reg(sid, name, **more):
            row = {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid,
                   "alive": True, "model": "claude-opus-5", "liveModel": "Opus 5"}
            row.update(more)
            Path(state, "sdk", sid + ".json").write_text(json.dumps(row))

        def transcript(sid, records):
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))

        # the PARENT: a board session with the transcript the highlights anchor to
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        reg(SID, "web")
        transcript(SID, parent)
        # the PROMOTED thread's session, as _comment_promote_inner leaves it: a names/ entry (it is a board
        # session now), the registry alive with threadOf cleared, the fork's transcript plus the side talk
        Path(state, "names", PROMOTED).write_text("web-2\t%s\t\t\n" % cwd)
        reg(PROMOTED, "web-2")
        transcript(PROMOTED, parent + [
            uline(t0 + 100, "Why two minutes and not five?", "pu1", parent="a1", sid=PROMOTED),
            aline(t0 + 110, "Five would outlast the worker's own retry budget.", "pa1", parent="pu1", sid=PROMOTED)])
        # the OPEN thread's session, as _comment_create leaves it: no names/ entry (no tab of its own),
        # threadOf the parent, the fork's transcript plus the comment exchange after the cut
        reg(OPEN, "web-comment-1", threadOf=SID)
        transcript(OPEN, parent + [
            uline(t0 + 100, "Why jitter at all?", "ou1", parent="a1", sid=OPEN),
            aline(t0 + 110, "Jitter prevents thundering herds.", "oa1", parent="ou1", sid=OPEN)])
        # the kernel's OWN comment store for the parent — the rows _comments_frame reads (the promoted row's
        # status + promotedName are what _comment_update writes at the end of a promote)
        Path(state, "comments", SID + ".json").write_text(json.dumps({"threads": [
            {"tid": PROMOTED, "sid": PROMOTED, "anchorUuid": "a1", "cutUuid": "a1", "exact": PROMOTED_EXACT,
             "status": "promoted", "promotedName": "web-2", "name": "web-comment-2", "color": "",
             "createdT": t0 + 100, "lastSeenT": now},
            {"tid": OPEN, "sid": OPEN, "anchorUuid": "a1", "cutUuid": "a1", "exact": OPEN_EXACT,
             "status": "open", "promotedName": "", "name": "web-comment-1", "color": "",
             "createdT": t0 + 100, "lastSeenT": now}]}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port = _free_port()
        cls.token = "testtok-cmtpromoted"
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
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.kill()
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": SID,
                       "promoted": PROMOTED, "open": OPEN, "w": VIEW_W, "h": VIEW_H}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served popup needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
                         + "\nkernel:\n" + open(self.klog).read()[-2000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def test_a_promoted_threads_popup_reads_as_a_quote_a_line_and_one_button(self):
        out = self._drive()
        p, o = out["promoted"], out["open"]
        self.assertIsNotNone(p, "the promoted thread's highlight opens its popup")
        self.assertEqual(p["status"], "promoted", p)
        self.assertTrue(p["title"].startswith("Now its own session: web-2"), p["title"])
        # the box keeps its OPEN geometry — content never shrinks it (the parity contract); this fix is about
        # what sits inside the box, not its size
        self.assertGreater(p["pop"]["h"], 300, "the box is not shrunk to its content: %r" % p["pop"])
        self.assertGreater(p["pop"]["w"], 500, "the box is not shrunk to its content: %r" % p["pop"])
        # (1) the quote stands at its natural height: one line of text, a box within two line-heights of it
        q = p["quote"]
        self.assertIsNotNone(q, "the promoted popup still shows the passage it was about: %r" % p)
        self.assertEqual(q["text"], PROMOTED_EXACT)
        lh = px(q["lh"]) or px(q["fs"]) * 1.5
        self.assertEqual(q["lines"], 1, "a one-line quote at this width: %r" % q)
        self.assertLessEqual(q["rect"]["h"], 2.5 * lh,
                             "the quote's box hugs its text (it ran hundreds of pixels tall): %r" % q)
        # (2) no void: an empty messages area takes no room, and the action follows the quote within a few lines
        m = p["msgs"]
        self.assertTrue(m is None or m["display"] == "none" or m["rect"]["h"] <= 2 * lh,
                        "an empty messages area must not swallow the box: %r" % m)
        a = p["act"]
        self.assertIsNotNone(a, "the one action, Open the session: %r" % p)
        self.assertLessEqual(a["rect"]["top"] - q["rect"]["bottom"], 5 * lh,
                             "the action sits under the quote, not at the bottom of a void: quote %r action %r" % (q["rect"], a["rect"]))
        # (3) the action is a real button in the shared dress: one line, wide enough for its text, a border,
        #     and the popup's control size — never the send glyph's square
        self.assertEqual(a["text"], "Open the session")
        self.assertEqual(a["tag"], "BUTTON")
        self.assertTrue(a["inActions"], "the action lives in .cmt-actions: %r" % a)
        self.assertIn("cmt-act", a["cls"].split(), "the shared word-button dress: %r" % a["cls"])
        self.assertNotIn("cmt-send", a["cls"].split(), "never the composer's send-glyph square: %r" % a["cls"])
        self.assertEqual(a["lines"], 1, "the label reads on ONE line: %r" % a)
        self.assertGreaterEqual(a["rect"]["w"] + 0.5, a["textW"], "the button is no narrower than its text: %r" % a)
        self.assertEqual(a["borderStyle"], "solid", "visible button chrome: %r" % a)
        self.assertGreater(px(a["borderW"]), 0, "visible button chrome: %r" % a)
        self.assertLessEqual(px(a["fs"]), px(p["titleFs"]) + 0.01,
                             "the action's font is no bigger than the head's: %r vs %r" % (a["fs"], p["titleFs"]))
        self.assertFalse(p["composer"], "a promoted thread has no composer — the talk happens in the session")
        # the CONTROL: an ordinary open thread renders as before — the chat-parity list, the composer, and the
        # word-buttons in the same dress, at exactly the size the promoted action wears
        self.assertIsNotNone(o, "the open thread's highlight opens its popup")
        self.assertEqual(o["status"], "open", o)
        self.assertTrue(o["composer"], "the open thread keeps its composer: %r" % o)
        self.assertIsNotNone(o["msgs"], "the open thread keeps its messages area: %r" % o)
        self.assertNotEqual(o["msgs"]["display"], "none", "…visible: %r" % o["msgs"])
        self.assertTrue(o["msgs"]["hasTurn"], "the chat-parity render landed for the open thread: %r" % o["msgs"])
        labels = [b["text"] for b in o["acts"]]
        self.assertEqual(labels, ["Break out", "Relay", "Delete"], labels)
        for b in o["acts"]:
            self.assertIn("cmt-act", b["cls"].split(), b)
            self.assertEqual(b["lines"], 1, b)
            self.assertAlmostEqual(px(b["fs"]), px(a["fs"]), delta=0.05,
                                   msg="one control size across the popup's actions: %r vs promoted %r" % (b, a))


if __name__ == "__main__":
    unittest.main()
