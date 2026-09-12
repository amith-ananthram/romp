#!/usr/bin/env python3
"""An unread comment thread's cue on the real /chat page. T310 (the user 2026-09-10): ONE outline around the WHOLE
highlighted passage, never a box per line. The user 2026-09-12: the stroke is the tab strip's needs-you ring again
(dashed, --st-awaiting-bg), the cue follows the passage's LINE COUNT (one or two rows: the per-fragment ring on the
marks, no box; three or more: the box, no ring), and the rail tick's unread halo wears the same red while its fill
keeps the notch's own ink.

A hermetic kernel serves a synthetic chat (the notes-api demo world: session web) whose one answer carries three open
comment threads with a landed, unseen reply each: a passage that wraps over two lines, one over five and one on a
single line (on a 640 px page; the kernel ships a thread's exact text capped at 500 characters, so the mark covers at
most that), plus an incoming question card whose long body scrolls, with a fourth thread on its three-line last
sentence: that box exists only while its passage shows inside the body (cut to the scrolling container, repainted
when the body scrolls). Asserted on the page, in the dark theme and the light one: the five-line and the question
passages have EXACTLY one outline box each (when visible), positioned (the text under it never moves) and transparent
to the pointer, dashed, in the computed --st-awaiting-bg, covering every line fragment of the thread's marks, and no
fragment of theirs wears an outline of its own; the one- and two-line passages have NO box and every fragment ringed
(dashed, the same red, 1px off the glyphs); the red reads at 3:1 or better against the page; the unread tick's halo is
that red and its fill the notch's ink; narrowing the window carries the two-line passage past two rows and swaps its
ring for the box, widening swaps back (the existing resize hook, nothing added); opening a thread (a click on its mark)
removes ITS cue on the same pass and leaves the others'. With COMMENT_SHOTS=<dir> the driver also writes a dark and a
light screenshot. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only
(placeholder UUIDs, invented prose, host TESTHOST)."""
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
THREAD2 = "bbbbbbbb-1111-2222-3333-444444444444"   # the two-line passage's thread
THREAD5 = "cccccccc-1111-2222-3333-444444444444"   # the five-line passage's thread
THREAD1 = "99999999-1111-2222-3333-444444444444"   # the one-line passage's thread

# the answer: three paragraphs, each the exact text of one thread's highlight
P2 = ("Use exponential backoff with a jitter of ten percent on every retry of the notes-api sync loop, and cap the "
      "delay at two minutes.")
P5 = ("The retry budget itself should live in one place: a small table keyed by the failing endpoint, holding the "
      "attempt count, the next allowed time and the last error text, so the dashboard can show at a glance which "
      "endpoints are struggling and the operator can reset one row without touching the others. Persist that table "
      "with the notes themselves, never in memory alone, because a restart in the middle of an outage would otherwise "
      "forget every backoff and hammer the endpoint when it comes back.")
P1 = "Log every retry at debug level, nothing louder."
assert len(P5) <= 500, "the kernel ships a thread's exact text capped at 500 chars; the mark covers what ships"
THREADQ = "dddddddd-1111-2222-3333-444444444444"   # the thread on the question card's LAST sentence (a scrolled notice body)
API = "eeeeeeee-1111-2222-3333-444444444444"
BAR = "#" * 44
# three lines or more at 640 px, so the scrolled-container case stays a BOX case (a one- or two-row passage would ring)
PQ_LAST = ("Which of the two caps do you want written into the README, two minutes or five? The operators will read that "
           "number as a promise, so pick the one you can keep on the slowest endpoint we have, and say whether it applies "
           "to the notes-api alone or to every service the dashboard polls on the same schedule.")
# an incoming QUESTION card opens by default and its body scrolls at the notice body's 420 px max height: forty short
# paragraphs, then the sentence the third thread anchors to, so that passage starts scrolled OUT of the body's view
PQ = "\n\n".join("Point %d: the retry table needs a row for endpoint number %d, with its own attempt count and next allowed time." % (i, i)
                 for i in range(1, 41)) + "\n\n" + PQ_LAST


def banner(frm, kind, mid, body, t):
    """The delivered banner the postal service injects (bin/romp-postal-service format_push), as a user record."""
    hhmm = datetime.fromtimestamp(t).strftime("%H:%M")
    return "\n".join([BAR, "## \U0001F4EC from %s \u00b7 %s" % (frm, hhmm), BAR, body, "<!-- romp-msg-id: %s -->" % mid,
                      "<!-- romp-msg-kind: %s -->" % kind, BAR,
                      "(to reply, only if substantive: romp mail send --kind delegate|coordinate|question %s \"...\")" % frm])


def _contrast(a, b):
    """WCAG contrast of two "rgb(r, g, b)" strings."""
    def lum(c):
        ch = [int(x) / 255 for x in c[c.index("(") + 1:c.index(")")].split(",")[:3]]
        f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, uuid, parent, text, sid):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "sessionId": sid, "message": {"role": "user", "content": text}}


def agent(t, uuid, parent, text, sid):
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
const page = await browser.newPage({ viewport: { width: 640, height: 900 }, deviceScaleFactor: 2 });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForFunction((n) => document.querySelectorAll("mark.cmt-hl.unread").length >= n, cfg.minMarks, { timeout: 30000 });
await page.waitForTimeout(400);   // the rail's rAF pass after the marks landed
const measure = () => page.evaluate(() => {
  // the computed value of --st-awaiting-bg on THIS page in THIS theme: a probe painted with it (a custom property's own
  // computed value is its raw text, so it is read through a colour)
  const probe = document.createElement("span"); probe.style.color = "var(--st-awaiting-bg)"; document.body.appendChild(probe);
  const needsYou = getComputedStyle(probe).color; probe.remove();
  const tids = Array.from(new Set(Array.from(document.querySelectorAll("mark.cmt-hl")).map((m) => m.dataset.tid)));
  const threads = {};
  for (const tid of tids) {
    const marks = Array.from(document.querySelectorAll(`mark.cmt-hl[data-tid="${tid}"]`));
    const rects = marks.flatMap((m) => Array.from(m.getClientRects()).filter((q) => q.width || q.height))
      .map((q) => ({ l: q.left, t: q.top, r: q.right, b: q.bottom }));
    const lines = new Set(rects.map((q) => Math.round(q.t))).size;
    const boxes = Array.from(document.querySelectorAll(`.cmt-outline[data-tid="${tid}"]`)).map((box) => {
      const q = box.getBoundingClientRect();
      const cs = getComputedStyle(box);
      return { l: q.left, t: q.top, r: q.right, b: q.bottom, outline: cs.outlineStyle + " " + cs.outlineWidth + " " + cs.outlineColor,
               position: cs.position, pointer: cs.pointerEvents, parentIsTurn: box.parentElement.classList.contains("turn"),
               sameTurn: box.parentElement === marks[0].closest(".turn") };
    });
    const ringed = marks.filter((m) => getComputedStyle(m).outlineStyle !== "none");
    threads[tid] = { unread: marks.some((m) => m.classList.contains("unread")), fragments: rects.length, lines,
                     markOutlines: Array.from(new Set(marks.map((m) => getComputedStyle(m).outlineStyle))),
                     ringed: ringed.length, marks: marks.length,
                     ringColors: Array.from(new Set(ringed.map((m) => getComputedStyle(m).outlineColor))),
                     ringOffsets: Array.from(new Set(ringed.map((m) => getComputedStyle(m).outlineOffset))),
                     union: rects.length ? { l: Math.min(...rects.map((q) => q.l)), t: Math.min(...rects.map((q) => q.t)),
                                             r: Math.max(...rects.map((q) => q.r)), b: Math.max(...rects.map((q) => q.b)) } : null,
                     boxes };
  }
  const tick = document.querySelector(".cmt-tick.unread");
  const nb = document.querySelector(".turn-postal-service .notice-body");
  const nbr = nb ? nb.getBoundingClientRect() : null;
  const solid = (c) => c && !/^rgba\(.*,\s*0\)$/.test(c) && c !== "transparent";
  const pageBg = [document.getElementById("content"), document.body, document.documentElement]
    .map((n) => n && getComputedStyle(n).backgroundColor).find(solid) || "rgb(30, 30, 30)";
  return { threads, needsYou, tickColor: tick ? getComputedStyle(tick).backgroundColor : null,
           tickHalo: tick ? getComputedStyle(tick).boxShadow : null, pageBg,
           noticeBody: nb ? { l: nbr.left, t: nbr.top, r: nbr.right, b: nbr.bottom, scrollTop: nb.scrollTop, scrollHeight: nb.scrollHeight, clientHeight: nb.clientHeight } : null,
           boxCount: document.querySelectorAll(".cmt-outline").length,
           theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
});
const dark = await measure();
// the question card's body scrolls: its last sentence (the third thread) starts out of view — scroll the body to its end
// (a real scroll event inside the pane, which does not bubble) and let the rail's rAF pass repaint
await page.evaluate(() => { const nb = document.querySelector(".turn-postal-service .notice-body"); nb.scrollTop = nb.scrollHeight; });
await page.waitForTimeout(400);
const scrolled = await measure();
await page.evaluate(() => { const nb = document.querySelector(".turn-postal-service .notice-body"); nb.scrollTop = 0; });
await page.waitForTimeout(400);
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/romp_chat-comment-outline-dark.png", fullPage: false }); }
await page.evaluate(() => document.body.classList.add("theme-light"));
await page.waitForTimeout(300);
const light = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "/romp_chat-comment-outline-light.png", fullPage: false });
await page.evaluate(() => document.body.classList.remove("theme-light"));
await page.waitForTimeout(200);
// a narrower window carries the two-line passage past two rows: the existing window-resize hook repaints, and the ring
// swaps for the box; widening again swaps back
await page.setViewportSize({ width: 420, height: 900 });
await page.waitForTimeout(400);
const narrow = await measure();
await page.setViewportSize({ width: 640, height: 900 });
await page.waitForTimeout(400);
const widened = await measure();
// read the two-line thread: a click on its mark opens the popover, which drops the unread bit and re-runs the marks pass
await page.click(`mark.cmt-hl[data-tid="${cfg.read}"]`);
await page.waitForFunction((tid) => !document.querySelector(`.cmt-outline[data-tid="${tid}"]`), cfg.read, { timeout: 10000 }).catch(() => {});
await page.waitForTimeout(300);
const afterRead = await measure();
afterRead.popover = await page.evaluate(() => !!document.querySelector("#cmt-pop"));
fs.writeSync(1, "RESULT:" + JSON.stringify({ dark, scrolled, light, narrow, widened, afterRead }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentOutline(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-outline-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 1800
        Path(state, "names", API).write_text("api\t%s\t#1EA1EB\t#ffffff\n" % cwd)
        os.makedirs(os.path.join(state, "timeline"), exist_ok=True)
        Path(state, "timeline", "messages.jsonl").write_text(json.dumps(
            {"t": t0 + 30, "ev": "sent", "id": "q-cap", "from": "api", "from_id": API, "to_id": WEB, "body": PQ, "kind": "question", "from_host": ""}) + "\n")
        parent = [user(t0, "u1", None, "how should the notes-api retry loop back off, and where does its budget live?", WEB),
                  agent(t0 + 5, "a1", "u1", P2 + "\n\n" + P5 + "\n\n" + P1, WEB),
                  user(t0 + 31, "m1", "a1", banner("api", "question", "q-cap", PQ, t0 + 30), WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent))
        # four threads, each a fork cut at its anchor (the copied history, then the exchange), a reply landed and
        # never seen (no lastSeenT) — the kernel's unread bit; no names/ entry (a thread is never on the board)
        rows = []
        for i, (tsid, anchor, exact, ask, reply) in enumerate([
                (THREAD2, "a1", P2, "why jitter at all?", "Jitter spreads simultaneous retries apart so they do not arrive as one wave."),
                (THREAD5, "a1", P5, "why persist the budget?", "A restart mid-outage would otherwise forget every backoff and storm the endpoint."),
                (THREADQ, "m1", PQ_LAST, "is five too long?", "Five minutes is too long for a notes sync; two keeps the user waiting under a coffee."),
                (THREAD1, "a1", P1, "why debug level?", "Anything louder would page someone for a retry that succeeds on its own.")]):
            t = t0 + 60 * (i + 1)
            thread = [r for r in parent if r["uuid"] in ("u1", "a1") or anchor == "m1"]   # the copied history, up to the cut
            thread = [dict(r, sessionId=tsid) for r in thread]
            thread += [user(t, "c%d" % i, anchor, ask, tsid),
                       agent(t + 20, "r%d" % i, "c%d" % i, reply, tsid)]
            Path(proj, tsid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in thread))
            Path(state, "sdk", tsid + ".json").write_text(json.dumps(
                {"sid": tsid, "name": "web-comment-%d" % (i + 1), "cwd": cwd, "lastSid": tsid, "threadOf": WEB, "forkAt": anchor,
                 "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
            rows.append({"tid": "tid-%d" % (i + 1), "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor,
                         "cutUuid": anchor, "exact": exact, "status": "open", "createdT": t})
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-outline"
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

    def test_the_cue_by_row_count_dashed_in_the_needs_you_red_gone_once_read(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "minMarks": 4, "read": "tid-1",
                       "shots": os.environ.get("COMMENT_SHOTS", "")}, f)
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
        r = json.loads(line[len("RESULT:"):])
        RED = "rgb(192, 57, 43)"   # --st-awaiting-bg, the tab strip's needs-you red, the same in both themes

        def ringed(th, red, label):
            """every fragment of the passage wears the dashed ring in the needs-you red, 1px off the glyphs; no box."""
            self.assertTrue(th["unread"], th)
            self.assertEqual(th["boxes"], [], "%s: a one- or two-row passage has no box: %r" % (label, th))
            self.assertEqual(th["markOutlines"], ["dashed"], "%s: every fragment rings: %r" % (label, th))
            self.assertEqual(th["ringed"], th["marks"], "%s: every mark of the thread, not some: %r" % (label, th))
            self.assertEqual(th["ringColors"], [red], "%s: the ring in the needs-you red: %r" % (label, th))
            self.assertEqual(th["ringOffsets"], ["1px"], "%s: 1px clear of the glyphs: %r" % (label, th))

        def boxed(th, red, label):
            """ONE dashed box in the needs-you red covering every fragment, hugging their union; no ring on any fragment."""
            self.assertTrue(th["unread"], th)
            self.assertEqual(th["markOutlines"], ["none"], "%s: no fragment wears an outline of its own: %r" % (label, th))
            self.assertEqual(len(th["boxes"]), 1, "%s: ONE box for the whole passage: %r" % (label, th))
            box, u = th["boxes"][0], th["union"]
            style, width, colour = box["outline"].split(" ", 2)
            self.assertEqual((style, colour), ("dashed", red), "%s: dashed, in the needs-you red: %r" % (label, box))
            self.assertTrue(1 <= float(width.rstrip("px")) <= 2, "the 1.5px rule, as the engine snaps it: %r" % box)
            self.assertEqual(box["position"], "absolute", "positioned: the text never moves")
            self.assertEqual(box["pointer"], "none", "hover and click land on the marks beneath")
            self.assertTrue(box["parentIsTurn"] and box["sameTurn"], "a child of the anchor turn: %r" % box)
            # the box covers every fragment (1px clear of the glyphs) and hugs the union, not the whole line
            self.assertLessEqual(box["l"], u["l"] - 0.5); self.assertLessEqual(box["t"], u["t"] - 0.5)
            self.assertGreaterEqual(box["r"], u["r"] + 0.5); self.assertGreaterEqual(box["b"], u["b"] + 0.5)
            for side in ("l", "t", "r", "b"):
                self.assertLess(abs(box[side] - u[side]), 4, "%s: the box hugs the union on the %s side: %r vs %r" % (label, side, box, u))

        for theme, ink in (("dark", "rgb(255, 213, 74)"), ("light", "rgb(143, 106, 0)")):
            m = r[theme]
            self.assertEqual(m["theme"], theme)
            self.assertEqual(set(m["threads"]), {"tid-1", "tid-2", "tid-3", "tid-4"}, m)
            self.assertEqual(m["needsYou"], RED, "the token resolves to the tab strip's red in this theme: %r" % m["needsYou"])
            self.assertGreaterEqual(_contrast(RED, m["pageBg"]), 3.0, "the red reads as a LINE against the page: %r on %r" % (RED, m["pageBg"]))
            # the scroll notch keeps its OWN ink (the comment's yellow; amber on cream), and its unread halo — the outer 3px
            # ring past the 1.5px page-coloured gap — agrees with the box by colour
            self.assertEqual(m["tickColor"], ink, "the tick's fill is still the notch's ink in this theme: %r" % m)
            self.assertGreaterEqual(_contrast(ink, m["pageBg"]), 3.0, "the ink reads against the page: %r on %r" % (ink, m["pageBg"]))
            halo = re.search(r"(rgba?\([^)]*\)) 0px 0px 0px 3px", m["tickHalo"] or "")
            self.assertIsNotNone(halo, "the unread tick's outer ring: %r" % m["tickHalo"])
            self.assertEqual(halo.group(1), RED, "the halo in the needs-you red: %r" % m["tickHalo"])
            self.assertNotIn(ink, m["tickHalo"], "no yellow halo left: %r" % m["tickHalo"])
            # the fourth thread's passage (three lines) sits below the question card's scrolled body: unread, three rows or more,
            # so a BOX case — and no box until it shows (cut to the scrolling container); no ring either way
            q = m["threads"]["tid-3"]
            self.assertTrue(q["unread"], q)
            self.assertGreaterEqual(q["lines"], 3, "the question passage wraps over three lines or more: %r" % q)
            self.assertEqual(q["markOutlines"], ["none"], "three rows or more never ring: %r" % q)
            self.assertEqual(m["noticeBody"]["scrollTop"], 0, m["noticeBody"])
            self.assertGreater(m["noticeBody"]["scrollHeight"], m["noticeBody"]["clientHeight"] + 200, "the body scrolls: %r" % m["noticeBody"])
            self.assertGreater(q["union"]["t"], m["noticeBody"]["b"], "the passage starts scrolled out of the body's view: %r vs %r" % (q["union"], m["noticeBody"]))
            self.assertEqual(q["boxes"], [], "a fragment scrolled out of its container draws no box over the content below: %r" % q)
            two, five, one = m["threads"]["tid-1"], m["threads"]["tid-2"], m["threads"]["tid-4"]
            self.assertEqual(two["lines"], 2, "the first passage wraps over exactly two lines at 640px: %r" % two)
            self.assertGreaterEqual(five["lines"], 5, "the second passage wraps over at least five lines: %r" % five)
            self.assertEqual(one["lines"], 1, "the third passage sits on one line: %r" % one)
            ringed(two, RED, theme + " two-line")
            ringed(one, RED, theme + " one-line")
            boxed(five, RED, theme + " five-line")
            self.assertEqual(m["boxCount"], 1, "one box per VISIBLE unread thread of three rows or more: %r" % m)
        # scrolled to the body's end (a scroll inside the pane, captured by the rail scheduler): the question thread's box
        # appears on its passage, inside the body's rect, hugging the visible fragments
        sc = r["scrolled"]
        q, nb = sc["threads"]["tid-3"], sc["noticeBody"]
        self.assertGreater(nb["scrollTop"], 0, nb)
        boxed(q, RED, "scrolled question")
        box = q["boxes"][0]
        self.assertGreaterEqual(box["t"], nb["t"] - 1.5); self.assertLessEqual(box["b"], nb["b"] + 1.5)
        self.assertGreaterEqual(box["l"], nb["l"] - 1.5); self.assertLessEqual(box["r"], nb["r"] + 1.5)
        self.assertEqual(sc["boxCount"], 2)
        # a narrower window: the two-line passage now spans three rows or more, and the ring gives way to the box on the
        # existing resize hook; widened again, it is two rows and ringed. The five-line passage is a box either way.
        na, wi = r["narrow"], r["widened"]
        self.assertGreaterEqual(na["threads"]["tid-1"]["lines"], 3, "at 420px the first passage wraps past two rows: %r" % na["threads"]["tid-1"])
        boxed(na["threads"]["tid-1"], RED, "narrow two-line→box")
        boxed(na["threads"]["tid-2"], RED, "narrow five-line")
        ringed(na["threads"]["tid-4"], RED, "narrow one-line")
        self.assertEqual(wi["threads"]["tid-1"]["lines"], 2, wi["threads"]["tid-1"])
        ringed(wi["threads"]["tid-1"], RED, "widened two-line→ring")
        boxed(wi["threads"]["tid-2"], RED, "widened five-line")
        self.assertEqual(wi["boxCount"], 1, "back to the one visible box: %r" % wi)
        # read the two-line thread: the click drops its unread bit, and its ring goes on the same marks pass; the others keep theirs
        a = r["afterRead"]
        self.assertTrue(a["popover"], "the click opened the thread: %r" % a)
        self.assertFalse(a["threads"]["tid-1"]["unread"], "read: the unread bit dropped on the click")
        self.assertEqual(a["threads"]["tid-1"]["markOutlines"], ["none"], "…and its ring went with it: %r" % a["threads"]["tid-1"])
        self.assertEqual(a["threads"]["tid-1"]["boxes"], [], a["threads"]["tid-1"])
        boxed(a["threads"]["tid-2"], RED, "after read, five-line")
        ringed(a["threads"]["tid-4"], RED, "after read, one-line")
        self.assertEqual(a["boxCount"], 1, "the five-line box, the scrolled-out one still clipped away: %r" % a)

if __name__ == "__main__":
    unittest.main()
