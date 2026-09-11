#!/usr/bin/env python3
"""T302 (the user 2026-09-10): the postal cards in the chat pane, rendered by the real /chat page of a hermetic
kernel over a synthetic chat with web (the viewed session), api and tests (its peers) — every state at once.

Asserted on the page: the interaction KIND is coloured text (Delegation / Coordination / Question, three positions
sampled evenly along one line in the accent's hue, T337), never a chip; the DELIVERY STATE is one icon per state at the
head's right edge with a worded title (delivered, read, parked, bounced, recalled, and sent for a message handed to the
relay; sent / delivered / read as the circled-check ladder, the read check cut out in the page colour, T337), from what the kernel
files (the send-time stamp, and the postal ledger's exec / relayed / bounced / recall rows joined by message id);
both ENDS wear their sessions' colours (the peer's chip, then this session's own chip); no card wears a background
wash; incoming cards are boxed, sent ones slim unless a fold boxes them; a sent card whose message has not landed wears
the pending send's own provisional dress (the queued bubble's class) at either density, slim or boxed, at the column's
full width. With POSTAL_SHOTS=<dir> the driver also writes
screenshots at 1000 px, 520 px, 340 px dark and 1000 px, 340 px light, named romp_chat-postal-cards-<theme>-<width>.png (the phone width: the head wraps, the ends first, the kind word and the icon on
that line or the next as one unit, the gist last on its own full-width line, T313) and the light theme. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC
fixtures only (the notes-api demo world: web / api / tests; host TESTHOST)."""
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
API = "bbbbbbbb-1111-2222-3333-444444444444"
TESTS = "cccccccc-1111-2222-3333-444444444444"
BAR = "#" * 44


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def hhmm(t):
    return datetime.fromtimestamp(t).strftime("%H:%M")


def banner(frm, kind, mid, body, t):
    """The delivered banner the postal service injects (bin/romp-postal-service format_push), as a user record."""
    return "\n".join([BAR, "## 📬 from %s · %s" % (frm, hhmm(t)), BAR, body, "<!-- romp-msg-id: %s -->" % mid,
                      "<!-- romp-msg-kind: %s -->" % kind, BAR,
                      "(to reply, only if substantive: romp mail send --kind delegate|coordinate|question %s \"...\")" % frm])


def user(t, uuid, parent, text):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "sdk", "sessionId": WEB,
            "message": {"role": "user", "content": text}}


def send_pair(t, uuid, parent, to, kind, body, result, is_error=False):
    tu = "tu_" + uuid
    return [
        {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": WEB,
         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
                     "content": [{"type": "tool_use", "id": tu, "name": "mcp__romp-postal-service__send_message",
                                  "input": ({"to": to, "body": body, "kind": kind} if kind else {"to": to, "body": body})}]}},
        {"type": "user", "timestamp": iso(t + 1), "uuid": uuid + "r", "parentUuid": uuid, "sessionId": WEB,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu, "content": result, "is_error": is_error}]}},
    ]


def sent_row(mid, frm, frm_id, to_id, body, t, kind, park=False):
    r = {"t": t, "ev": "sent", "id": mid, "from": frm, "from_id": frm_id, "to_id": to_id, "body": body, "kind": kind, "from_host": ""}
    if not kind:
        del r["kind"]                      # a legacy row: sent without --kind, before the marker existed
    if park:
        r["park"] = True
    return r


# a sent one-liner under the 90-char clip with NO kind (no declared kind, no leading token): the card has no kind word,
# only a delivery icon — the meta slot must still exist so the icon rides in it like every other (T313 review find)
KINDLESS = "Merged the fixtures branch; nothing else is pending on my side."


# a sent one-liner OVER the 90-char clip: its gist cannot carry the whole line, so the card has a fold and is boxed; parked,
# it must wear the same provisional dress as the slim parked card beside it (the box rule used to win its border and
# background, and the bubble's 72% cap its width)
LONG_PARKED = "Once the fixtures land, run the whole integration suite against notes-api and write every flaky case into the README."
# a sent card that LANDED and folds (a body past the head's clip): boxed, read, and so the one landed boxed sent card, whose
# ground must stay the plain box while the incoming cards wear the tint (T337c review, 2026-09-11)
LONG_READ = "Review the schema change before the rest: it touches the export path, and the retry budget now sits in the README beside the jitter cap of two minutes."


# The world: twelve cards, every kind and every state, among them one legacy card with no kind and one parked card with a
# fold. Bodies are invented notes-api chatter.
def world(t0):
    msgs = {
        "in-deleg": ("api", API, "delegate", "Take the retry-loop rewrite in notes-api: exponential backoff with jitter, cap at two minutes, tests included."),
        "in-coord": ("tests", TESTS, "coordinate", "Heads-up: the integration suite now needs the fixtures directory; nothing to do on your side."),
        "in-quest": ("api", API, "question", "Which cap do we want on the backoff, two minutes or five?"),
        "out-deliv": ("web", WEB, "coordinate", "The backoff branch is up; review when you have a moment."),
        "out-read": ("web", WEB, "question", "Did the fixtures land on your side yet?"),
        "out-parked": ("web", WEB, "delegate", "Please run the whole integration suite once the fixtures are in."),
        "out-parked-long": ("web", WEB, "delegate", LONG_PARKED),
        "out-queued": ("web", WEB, "coordinate", "The remote build is green; merging in an hour unless you object."),
        "out-bounced": ("web", WEB, "delegate", "Take the cap decision and write it down in the README."),
        "out-recalled": ("web", WEB, "coordinate", "Ignore my last note, wrong thread."),
        "out-nokind": ("web", WEB, None, KINDLESS),
        "out-long-read": ("web", WEB, "coordinate", LONG_READ),
    }
    log, recs = [], []
    t = t0
    parent = None
    # incoming: api delegates, tests coordinates (parked while web was offline), api asks
    for i, (mid, frm, fid, kind, body, park) in enumerate([
            ("in-deleg", "api", API, "delegate", msgs["in-deleg"][3], False),
            ("in-coord", "tests", TESTS, "coordinate", msgs["in-coord"][3], True),
            ("in-quest", "api", API, "question", msgs["in-quest"][3], False)]):
        t += 60
        log.append(sent_row(mid, frm, fid, WEB, body, t, kind, park=park))
        u = "u-in-%d" % i
        recs.append(user(t + 1, u, parent, banner(frm, kind, mid, body, t)))
        parent = u
    # outgoing, one per state
    outs = [
        ("out-deliv", "api", API, "Delivered to 'api'.", False, []),
        ("out-read", "api", API, "Delivered to 'api' as a question — you are now recorded as waiting on their answer.", False,
         [lambda t: {"t": t + 20, "ev": "exec", "id": "out-read"}]),
        ("out-parked", "tests", TESTS, "parked for tests (unreachable) — delivers on reconnect · id out-parked", False, []),
        ("out-parked-long", "tests", TESTS, "parked for tests · id out-parked-long", False, []),   # the plain park text; the state reads the word
        ("out-queued", "TESTHOST:api", "peer:TESTHOST", "Delivered to 'TESTHOST:api'.", False, []),
        ("out-bounced", "api", API, "Delivered to 'api' as a handoff — they own it now.", False,
         [lambda t: {"t": t + 30, "ev": "bounced", "id": "out-bounced", "why": "refused: the mailbox is isolated"}]),
        ("out-recalled", "tests", TESTS, "Delivered to 'tests'.", False,
         [lambda t: {"t": t + 40, "ev": "recall", "id": "out-recalled"}]),
        ("out-nokind", "api", API, "Delivered to 'api'.", False, []),   # a legacy send: no kind anywhere, an icon all the same
        ("out-long-read", "api", API, "Delivered to 'api'.", False,
         [lambda t: {"t": t + 20, "ev": "exec", "id": "out-long-read"}]),   # landed, read, and boxed by its fold
    ]
    for i, (mid, to_name, to_id, result, err, later) in enumerate(outs):
        t += 60
        _, _, kind, body = msgs[mid]
        log.append(sent_row(mid, "web", WEB, to_id, body, t, kind))
        for mk in later:
            log.append(mk(t))
        pair = send_pair(t + 1, "a-out-%d" % i, parent, to_name, kind, body, result, err)
        recs.extend(pair)
        parent = pair[-1]["uuid"]
    return log, recs


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const measure = () => page.evaluate(() => {
  const probe = document.createElement("div"); probe.style.background = "var(--box-bg)"; document.body.appendChild(probe);
  const boxBg = getComputedStyle(probe).backgroundColor; probe.remove();
  // an oklch() relative colour (the incoming card's tint, T337b) comes back as oklch(...) from getComputedStyle; the
  // contrast arithmetic below reads rgb, so a 1x1 canvas resolves it (an rgba() carrying alpha is left as it is)
  const asRGB = (css) => { if (!/^(oklch|oklab|color)\(/.test(css) || /\//.test(css)) return css;
    const cv = document.createElement("canvas"); cv.width = cv.height = 1; const ctx = cv.getContext("2d");
    ctx.fillStyle = css; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data;
    return "rgb(" + d[0] + ", " + d[1] + ", " + d[2] + ")"; };
  const cards = Array.from(document.querySelectorAll(".turn-postal-service")).map((t) => {
    const n = t.querySelector(".notice");
    const kind = t.querySelector(".postal-kind");
    const icon = t.querySelector(".postal-delivery");
    const self = t.querySelector(".notice-src-self");
    const peer = t.querySelector(".notice-src-chip:not(.notice-src-self)");
    const cs = getComputedStyle(n);
    const glyph = t.querySelector(".notice-glyph");
    const ends = t.querySelector(".notice-src-ends");
    // the kind WORD's own box (the meta element also holds the icon now), from a Range over its text node
    const kw = kind && kind.firstChild && kind.firstChild.nodeType === 3 ? (() => { const r = document.createRange(); r.selectNodeContents(kind.firstChild); return r.getBoundingClientRect(); })() : null;
    // the gist's real LINE boxes: the element is a blockified flex item and reports one rect, a Range over its text
    // reports one per line fragment — the count of distinct tops is the line count, the first row's span the first line
    const gl = (() => { const g = t.querySelector(".notice-gist"); if (!g) return { lines: null, first: null };
      const r = document.createRange(); r.selectNodeContents(g); const rs = Array.from(r.getClientRects()).filter((q) => q.width > 0);
      if (!rs.length) return { lines: 0, first: 0 };
      const tops = []; for (const q of rs) if (!tops.some((y) => Math.abs(y - q.top) < 4)) tops.push(q.top);
      const top = Math.min(...tops); const row = rs.filter((q) => Math.abs(q.top - top) < 4);
      return { lines: tops.length, first: Math.max(...row.map((q) => q.right)) - Math.min(...row.map((q) => q.left)) }; })();
    return {
      dir: t.classList.contains("postal-service-in") ? "in" : "out",
      boxed: t.classList.contains("notice-boxed"), slim: n.classList.contains("notice-slim"),
      kind: kind ? kind.textContent : null, kindColor: kind ? getComputedStyle(kind).color : null,
      kindClipped: kind ? kind.scrollWidth > kind.clientWidth + 1 : null,
      // the phone-width head (T313): the kind word and the icon inside the card, the gist on its own full-width line
      kindInside: kind && n ? Math.round(n.getBoundingClientRect().right - kind.getBoundingClientRect().right) : null,
      iconInside: icon && n ? Math.round(n.getBoundingClientRect().right - icon.getBoundingClientRect().right) : null,
      gistRatio: t.querySelector(".notice-gist") ? t.querySelector(".notice-gist").getBoundingClientRect().width / t.querySelector(".notice-head").getBoundingClientRect().width : null,
      headWidth: t.querySelector(".notice-head") ? t.querySelector(".notice-head").getBoundingClientRect().width : null,
      gistLines: gl.lines, gistFirstLine: gl.first,
      gistBelowEnds: (() => { const g = t.querySelector(".notice-gist"), e = t.querySelector(".notice-src-ends"); return g && e ? g.getBoundingClientRect().top >= e.getBoundingClientRect().bottom - 2 : null; })(),
      headWrap: t.querySelector(".notice-head") ? getComputedStyle(t.querySelector(".notice-head")).flexWrap : null,
      iconWithKind: icon && kw ? Math.abs(icon.getBoundingClientRect().top + icon.getBoundingClientRect().height / 2 - (kw.top + kw.height / 2)) < 8 : null,
      iconInMeta: icon ? !!icon.closest(".notice-meta") : null,
      iconGlyphDelta: icon && glyph ? Math.abs(icon.getBoundingClientRect().top - glyph.getBoundingClientRect().top) : null,
      kindWithOrBelowEnds: kw && ends ? kw.top >= ends.getBoundingClientRect().top - 2 : null,
      gistBelowKind: kw && t.querySelector(".notice-gist") ? t.querySelector(".notice-gist").getBoundingClientRect().top >= kw.bottom - 2 : null,
      chip: !!t.querySelector(".notice-chip, .postal-service-intent"),
      // the mark's drawing as the browser computes it (T337): the circled-check ladder, its box, its stroke, its colours
      mark: icon ? (() => { const svg = icon.querySelector("svg"), c = icon.querySelector("circle"), p = icon.querySelector("path");
        return { size: svg ? Math.round(svg.getBoundingClientRect().width) : null, strokeWidth: svg ? svg.getAttribute("stroke-width") : null,
                 circles: icon.querySelectorAll("circle").length, paths: icon.querySelectorAll("path").length,
                 circleFill: c ? getComputedStyle(c).fill : null, checkStroke: p ? getComputedStyle(p).stroke : null,
                 checkClass: p ? (p.getAttribute("class") || "") : null, colour: getComputedStyle(icon).color }; })() : null,
      state: icon ? icon.dataset.state : null, title: icon ? (icon.getAttribute("aria-label") || "") : null,
      iconRight: icon && n ? Math.round(n.getBoundingClientRect().right - icon.getBoundingClientRect().right) : null,
      peerText: peer ? peer.textContent : null, peerBg: peer ? getComputedStyle(peer).backgroundColor : null,
      selfText: self ? self.textContent : null, selfBg: self ? getComputedStyle(self).backgroundColor : null,
      selfWidth: self ? Math.round(self.getBoundingClientRect().width) : null,
      bg: asRGB(cs.backgroundColor), border: cs.borderTopStyle, provisional: n.classList.contains("queued-bubble"),
      opacity: cs.opacity,   // T337: the provisional dress fades by its colours, never by an element opacity
      // the provisional dress at either density: the card's max-width as computed, and the box it actually takes
      maxWidth: cs.maxWidth, width: Math.round(n.getBoundingClientRect().width),
      gist: (t.querySelector(".notice-gist") || {}).textContent || "",
      gistWrap: t.querySelector(".notice-gist") ? getComputedStyle(t.querySelector(".notice-gist")).whiteSpace : null,
      collapsible: n.classList.contains("notice-collapsible"),
      selfDot: !!(self && self.querySelector(".peer-dot")),
    };
  });
  // the page colour under the text, and the two kind colours, for a contrast check per theme
  const pg = document.createElement("div"); pg.style.background = "var(--bg)"; document.body.appendChild(pg);
  const pageBg = getComputedStyle(pg).backgroundColor; pg.remove();
  const kinds = {}; const kindWeight = {}; const kindBoxed = {};
  for (const k of ["delegate", "coordinate", "question"]) {
    const e = document.querySelector(".postal-kind-" + k);
    if (e) {
      kinds[k] = getComputedStyle(e).color; kindWeight[k] = getComputedStyle(e).fontWeight;
      const card = e.closest(".turn-postal-service");   // the FIRST word of each kind: measured against the card it sits on
      kindBoxed[k] = !!(card && card.classList.contains("notice-boxed"));
    }
  }
  return { boxBg, pageBg, kinds, kindWeight, kindBoxed, theme: document.body.classList.contains("theme-light") ? "light" : "dark", cards, pendingBubbleClass: !!document.querySelector(".queued-bubble") };
});
const contrast = (a, b) => {
  const lum = (css) => { const m = css.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number).map((v) => v / 255).map((c) => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)); return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]; };
  const la = lum(a), lb = lum(b); return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
};
// a boxed card paints --box-bg, an rgba WASH, over the page: the colour the word actually sits on is the composite (the
// review of 2026-09-10 found the light coordination step at 4.33:1 there while the page read 4.6:1)
const composite = (washCss, pageCss) => {
  // a wash is rgba(...) or, for a color-mix() the browser resolved, color(srgb r g b / a) with channels in 0..1
  const nums = (css) => { const m = css.match(/color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)/);
    if (m) return [255 * +m[1], 255 * +m[2], 255 * +m[3], m[4] === undefined ? 1 : +m[4]];
    const n = css.match(/\d+(\.\d+)?/g).map(Number); return [n[0], n[1], n[2], n.length > 3 ? n[3] : 1]; };
  const w = nums(washCss), p = nums(pageCss), a = w[3];
  return "rgb(" + [0, 1, 2].map((i) => Math.round(w[i] * a + p[i] * (1 - a))).join(", ") + ")";
};
const results = {};
let page;
for (const pass of [{ width: 1000, theme: "dark" }, { width: 520, theme: "dark" }, { width: 340, theme: "dark" }, { width: 1000, theme: "light" }, { width: 340, theme: "light" }]) {
  const width = pass.width;
  page = await browser.newPage({ viewport: { width, height: 1000 }, deviceScaleFactor: 2 });
  await page.goto(cfg.chat);
  await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
  try { await page.waitForFunction((n) => document.querySelectorAll(".turn-postal-service").length >= n, cfg.count, { timeout: 30000 }); }
  catch (e) {
    const st = await page.evaluate(() => ({ n: document.querySelectorAll(".turn-postal-service").length,
      turns: Array.from(document.querySelectorAll(".turn")).map((t) => t.className.slice(5, 50)).slice(0, 20) }));
    console.error("cards missing at " + width + ": " + JSON.stringify(st)); process.exit(1);
  }
  await page.waitForTimeout(500);
  if (pass.theme === "light") { await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(200); }
  // open one incoming card so the fold shows, then bring the tail into view for the shot
  const head = await page.$('.turn-postal-service.postal-service-in .notice-head[data-act="noticetoggle"]');
  if (head) { await head.click(); await page.waitForTimeout(200); }
  await page.evaluate(() => { const c = document.getElementById("content"); if (c) c.scrollTop = c.scrollHeight; });
  await page.waitForTimeout(300);
  const m = await measure();
  const boxOnPage = composite(m.boxBg, m.pageBg);
  // every kind word against the ground it actually sits on: the page, the box, or the provisional wash (T337)
  for (const c of m.cards) c.kindContrast = c.kind && c.kindColor ? contrast(c.kindColor, composite(c.bg, m.pageBg)) : null;
  m.contrast = {}; m.contrastOn = {}; m.contrastPage = {};
  for (const k of ["delegate", "coordinate", "question"]) {
    if (!m.kinds[k]) { m.contrast[k] = null; continue; }
    m.contrastOn[k] = m.kindBoxed[k] ? "box" : "page";
    m.contrast[k] = contrast(m.kinds[k], m.kindBoxed[k] ? boxOnPage : m.pageBg);   // against the card the word sits on
    m.contrastPage[k] = contrast(m.kinds[k], m.pageBg);
  }
  results[pass.theme === "light" ? (width === 1000 ? "light" : "light" + width) : String(width)] = m;
  if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/romp_chat-postal-cards-" + pass.theme + "-" + width + ".png", fullPage: false }); }
  await page.close();
}
fs.writeSync(1, "RESULT:" + JSON.stringify(results) + "\n");
await browser.close();
process.exit(0);
"""


class ServedPostalCards(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="postal-cards-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "timeline"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        # the viewed session and its two peers, each with an identity colour (the chip and the rail read it)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "names", API).write_text("api\t%s\t#1EA1EB\t#ffffff\n" % cwd)
        Path(state, "names", TESTS).write_text("tests\t%s\t#54B204\t#ffffff\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 3600
        log, recs = world(t0)
        Path(state, "timeline", "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in log))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.count = 12
        cls.port, cls.token = _free_port(), "testtok-postal"
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

    def test_every_kind_and_delivery_state_renders_as_ruled(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "count": self.count,
                       "shots": os.environ.get("POSTAL_SHOTS", "")}, f)
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
        wide, narrow, phone, light = r["1000"], r["520"], r["340"], r["light"]
        cards = wide["cards"]
        self.assertEqual(len(cards), 12, cards)
        by = {c["gist"][:24]: c for c in cards}
        def card(prefix):
            m = [c for c in cards if c["gist"].startswith(prefix)]
            self.assertEqual(len(m), 1, "one card starts with %r: %r" % (prefix, [c["gist"] for c in cards]))
            return m[0]
        # (1) the kind: coloured text, never a chip
        for c in cards:
            if c["gist"].startswith(KINDLESS[:20]):
                self.assertIsNone(c["kind"], "a legacy card with no kind shows no kind word: %r" % c)
            else:
                self.assertIn(c["kind"], ("Delegation", "Coordination", "Question"), c)
            self.assertFalse(c["chip"], "no chip: %r" % c)
        self.assertEqual(card("Take the retry-loop")["kindColor"], "rgb(124, 181, 227)", "delegation: the line's middle position (T320, re-sampled T337)")
        self.assertEqual(card("Heads-up")["kindColor"], "rgb(86, 150, 200)", "coordination: the line's start (T320, re-sampled T337)")
        self.assertEqual(card("Which cap")["kindColor"], "rgb(162, 212, 254)", "question: the line's end (T337)")
        # T337 (the review): the provisional dress fades by its colours, not by an element opacity that dimmed the kind
        # word too, so every kind word reads at 4.5:1 on the ground it sits on, the provisional wash included, in both themes
        for m, name in ((wide, "dark"), (light, "light")):
            for c in m["cards"]:
                if c["kind"]:
                    self.assertGreaterEqual(c["kindContrast"] or 0, 4.5, "%s: %s reads on its own ground (%s): %r" % (
                        name, c["kind"], "the provisional wash" if c["provisional"] else "the card", c))
                if c["provisional"]:
                    self.assertEqual(c["opacity"], "1", "no element opacity on the provisional card: %r" % c)
        self.assertTrue(any(c["provisional"] and c["kind"] for c in wide["cards"]), "a provisional card with a kind word is in the world")
        # (2) the delivery icon per state, at the head's right edge, with a worded title
        states = {c["gist"][:20]: c["state"] for c in cards}
        self.assertIsNone(card("Take the retry-loop")["state"], "an incoming message in hand: no icon")
        self.assertEqual(card("Heads-up")["state"], "parked", "waited while web was offline")
        self.assertIn("waited while you were offline", card("Heads-up")["title"], "in hand: the title says it waited, not that it will deliver")
        self.assertEqual(card("The backoff branch")["state"], "delivered")
        self.assertEqual(card("Did the fixtures")["state"], "read", "the ledger's exec row: the recipient consumed it")
        self.assertEqual(card("Please run the whole")["state"], "parked")
        self.assertEqual(card("Once the fixtures land")["state"], "parked")
        self.assertEqual(card("The remote build")["state"], "sent", "handed to the relay, no far-host ack yet")
        self.assertEqual(card("Take the cap decision")["state"], "bounced")
        self.assertEqual(card("Ignore my last note")["state"], "recalled")
        for c in cards:
            if c["state"]:
                self.assertTrue(c["iconInMeta"], "the icon rides in the meta slot (T313), a kind-less card's empty slot included: %r" % c)
                if c["kind"]:
                    self.assertTrue(c["iconWithKind"], "…beside the kind word, on its line: %r" % c)
                self.assertLess(c["iconGlyphDelta"], 8, "…on the head's first line, where the glyph is: %r" % c)
                self.assertTrue(c["title"] and c["title"].split(" ")[0] in ("Delivered", "Read", "Parked", "Sent", "Bounced", "Recalled"), c)
                self.assertLessEqual(c["iconRight"], 16, "the icon sits at the head's right edge: %r" % c)
        kindless = card(KINDLESS[:20])
        self.assertEqual(kindless["state"], "delivered", "the legacy card still shows its delivery state: %r" % kindless)
        # (2b) T337 (the user 2026-09-10, who wanted the circled check messaging apps draw): sent is the hollow circle,
        # delivered the circle with the check, read the filled circle with the check cut out in the page colour, all three
        # in the STATE colour (dim, dim, the accent), never the kind word's; one 14 px box, a 1.5 stroke, one circle
        sent, deliv, read = card("The remote build")["mark"], card("The backoff branch")["mark"], card("Did the fixtures")["mark"]
        for m in (sent, deliv, read):
            self.assertEqual((m["size"], m["strokeWidth"], m["circles"]), (14, "1.5", 1), "one circle in a 14 px box with a 1.5 stroke: %r" % m)
        self.assertEqual((sent["paths"], sent["circleFill"]), (0, "none"), "sent: the hollow circle alone: %r" % sent)
        self.assertEqual((deliv["paths"], deliv["circleFill"], deliv["checkClass"]), (1, "none", "postal-mark-check"), "delivered: the hollow circle with the check: %r" % deliv)
        self.assertEqual(deliv["checkStroke"], deliv["colour"], "delivered: the check in the mark's own colour: %r" % deliv)
        self.assertEqual(sent["colour"], deliv["colour"], "sent and delivered share the dim colour: %r %r" % (sent, deliv))
        self.assertEqual((read["paths"], read["checkClass"]), (1, "postal-mark-check"), "read: the check…: %r" % read)
        self.assertEqual(read["circleFill"], read["colour"], "…on the circle filled in the state colour: %r" % read)
        self.assertEqual(read["colour"], "rgb(156, 210, 255)", "the read colour is the accent, as before (the state colour, never the kind's): %r" % read)
        self.assertEqual(read["checkStroke"], wide["pageBg"], "read: the check cut out in the page colour: %r" % read)
        self.assertNotEqual(read["colour"], card("Did the fixtures")["kindColor"], "the mark's colour is not the kind word's")
        lread = next(c for c in light["cards"] if c["gist"].startswith("Did the fixtures"))["mark"]
        self.assertEqual(lread["circleFill"], "rgb(194, 65, 12)", "light: the read circle in the light accent: %r" % lread)
        self.assertEqual(lread["checkStroke"], light["pageBg"], "light: the check cut out in the cream page: %r" % lread)
        self.assertEqual((card("Please run the whole")["mark"]["circles"], card("Please run the whole")["mark"]["paths"]), (1, 1), "parked keeps the clock")
        self.assertEqual(card("Take the cap decision")["mark"]["paths"], 2, "bounced keeps the cross")
        self.assertEqual(card("Ignore my last note")["mark"]["paths"], 2, "recalled keeps the return arrow")
        self.assertIn("isolated", card("Take the cap decision")["title"], "a bounce carries its reason")
        # (3) both ends, each in its session's colour; the incoming card's tint in the peer's hue (the user 2026-09-11,
        # restoring what T302 removed the day before); boxed vs slim
        for c in cards:
            self.assertTrue(c["peerText"], c)
            self.assertTrue(c["selfText"] and "web" in c["selfText"], "this session's own end: %r" % c)
            self.assertEqual(c["selfBg"], "rgb(156, 210, 255)", "web's own colour on its chip: %r" % c)
            if c["dir"] == "in":
                self.assertTrue(c["boxed"] and not c["slim"], "incoming is boxed: %r" % c)
                self.assertNotEqual(c["bg"], wide["boxBg"], "the tint: the peer's hue on the ground, never the plain box: %r" % c)
                if not c["collapsible"]:
                    self.assertEqual(c["gistWrap"], "normal", "a boxed one-liner with nothing to fold wraps, never an ellipsis with nothing behind it: %r" % c)
            else:
                self.assertEqual(c["slim"], not c["collapsible"], "a sent card is slim unless it has a fold: %r" % c)
            self.assertFalse(c["selfDot"], "the own chip wears no working dot: %r" % c)
        self.assertEqual(card("Take the retry-loop")["peerBg"], "rgb(30, 161, 235)", "api's colour on its chip")
        self.assertEqual(card("Heads-up")["peerBg"], "rgb(84, 178, 4)", "tests' colour on its chip")
        # the tint is the PEER's hue: two peers' incoming cards wear two grounds, in both themes, and a landed sent card none
        for m, name in ((wide, "dark"), (light, "light")):
            grounds = {}
            landed_boxed = 0
            for c in m["cards"]:
                if c["dir"] == "in":
                    self.assertNotEqual(c["bg"], m["boxBg"], "%s: the incoming card is tinted: %r" % (name, c))
                    grounds.setdefault(c["peerText"], set()).add(c["bg"])
                elif c["boxed"] and not c["provisional"]:
                    landed_boxed += 1
                    self.assertEqual(c["bg"], m["boxBg"], "%s: a landed boxed sent card keeps the plain box: %r" % (name, c))
            self.assertGreaterEqual(landed_boxed, 1, "%s: the fixture holds a landed boxed sent card, so the branch above runs" % name)
            self.assertGreaterEqual(len(grounds), 2, "%s: two peers seen: %r" % (name, grounds))
            self.assertTrue(all(len(v) == 1 for v in grounds.values()), "%s: one ground per peer: %r" % (name, grounds))
            self.assertEqual(len({next(iter(v)) for v in grounds.values()}), len(grounds), "%s: each peer its own ground: %r" % (name, grounds))
        # (amendment) the provisional dress: the pending send's own class on the not-yet-landed sent cards only
        prov = {c["gist"][:20]: c["provisional"] for c in cards}
        self.assertTrue(card("Please run the whole")["provisional"], "parked → provisional")
        self.assertTrue(card("The remote build")["provisional"], "queued for relay → provisional")
        for pfx in ("The backoff branch", "Did the fixtures", "Take the cap decision", "Ignore my last note", "Take the retry-loop"):
            self.assertFalse(card(pfx)["provisional"], "landed, bounced, recalled and incoming are solid: %r" % pfx)
        self.assertEqual(card("Please run the whole")["border"], "dashed", "the bubble's dashed border by the shared rule")
        # the dress reaches a sent card of EITHER density: a parked delegation whose line outgrows the head has a fold, so it
        # is boxed, and it wears the dashed dress at the slim parked card's width. Before the shared rule named the boxed
        # card, the box rule won its border and background and the bubble's 72% cap its width, so it stood as a solid box
        # three-quarters of the column wide until the receipt landed, then snapped to the full width.
        long_parked, slim_parked = card("Once the fixtures land"), card("Please run the whole")
        self.assertTrue(long_parked["collapsible"] and not long_parked["slim"], "a sent card with a fold is boxed: %r" % long_parked)
        self.assertTrue(slim_parked["slim"] and not slim_parked["collapsible"], "the one-liner beside it is slim: %r" % slim_parked)
        self.assertTrue(long_parked["provisional"], "parked with a fold → provisional too")
        self.assertEqual(long_parked["border"], "dashed", "the boxed provisional card wears the bubble's dashed border, not the box's solid one: %r" % long_parked)
        self.assertEqual(long_parked["bg"], slim_parked["bg"], "…and the bubble's wash, not the box colour: %r" % long_parked)
        self.assertEqual(long_parked["maxWidth"], "none", "the width reset reaches the boxed card: %r" % long_parked)
        self.assertEqual(long_parked["width"], slim_parked["width"], "one width for the provisional card of either density: %r vs %r" % (long_parked["width"], slim_parked["width"]))
        # (narrow) both colours still show: this session's chip collapses to its dot
        for c in narrow["cards"]:
            self.assertTrue(c["selfBg"] == "rgb(156, 210, 255)" and c["selfWidth"] is not None and c["selfWidth"] <= 12,
                            "at 520 px the own chip is its coloured dot: %r" % c)
            if c["kind"]:
                self.assertIn(c["kind"], ("Delegation", "Coordination", "Question"), "the kind is never truncated: %r" % c)
        # (phone) under the 360 px container query the head WRAPS (T313): the two ends keep the first line; the kind word
        # and the icon stay on it when they fit and otherwise move to the next line as one unit; the gist comes last on its
        # own full-width line and breaks by words, never into a letter column — every card, slim, boxed and provisional
        self.assertEqual(len(phone["cards"]), 12, phone["cards"])
        for c in phone["cards"]:
            self.assertFalse(c["kindClipped"], "at 340 px the kind word is whole: %r" % c)
            self.assertEqual(c["headWrap"], "wrap", "the head wraps at phone width: %r" % c)
            if c["kind"]:
                self.assertGreaterEqual(c["kindInside"], 0, "the kind word's right edge sits inside the card: %r" % c)
            if c["state"]:
                self.assertGreaterEqual(c["iconInside"], 0, "the delivery icon sits inside the card: %r" % c)
                if c["kind"]:
                    self.assertTrue(c["iconWithKind"], "the icon shares the kind word's line: they wrap as one unit, never an icon alone on a line: %r" % c)
                else:
                    self.assertLess(c["iconGlyphDelta"], 8, "a kind-less card's icon sits on the first line with the glyph: %r" % c)
            if c["kind"]:
                self.assertTrue(c["kindWithOrBelowEnds"], "the kind word is on the ends' line or the next, never above: %r" % c)
                self.assertTrue(c["gistBelowKind"], "the gist comes after the kind word: %r" % c)
            self.assertTrue(c["gistBelowEnds"], "the gist sits on its own line under the ends: %r" % c)
            self.assertGreaterEqual(c["gistRatio"], 0.9, "the gist line spans the card's width: %r" % c)
            # word-wise breaks, never a letter column: a wrapped gist's FIRST line fills most of the head (a column two to
            # four letters wide filled a tenth of it), and a short gist is one line — measured on the text's line boxes
            self.assertTrue(c["gistLines"] == 1 or (c["gistFirstLine"] >= 0.6 * c["headWidth"] and c["gistFirstLine"] >= 150),
                            "the first gist line spans the head (at least 60%% of it, 150 px), no letter column: %r" % c)
        self.assertTrue(any(c["gistLines"] >= 2 for c in phone["cards"]), "at least one gist wraps at 340 px, so the wrapped branch is exercised: %r" % [c["gistLines"] for c in phone["cards"]])
        prov = [c for c in phone["cards"] if c["provisional"]]
        self.assertEqual(len(prov), 3, "the three provisional cards (parked, parked with a fold, relayed) are boxed at phone width too: %r" % prov)
        slim = [c for c in phone["cards"] if c["slim"]]
        self.assertGreaterEqual(len(slim), 3, "and slim cards are covered: %r" % [c["gist"][:20] for c in slim])
        # at 520 px and above the head is one line: the ends, the gist and the kind side by side (no wrap)
        for c in wide["cards"] + narrow["cards"]:
            self.assertEqual(c["headWrap"], "nowrap", "wider than the query the head stays one line: %r" % c)
        # the kind colours read at or above 4.5:1 on the card each word actually sits on, in both themes: a boxed
        # incoming card paints --box-bg over the page (the review of 2026-09-10 caught the light coordination step at
        # 4.33:1 there while the bare page read 4.6:1), a slim card is the page itself
        self.assertEqual(light["theme"], "light")
        for k in ("delegate", "coordinate", "question"):
            self.assertGreaterEqual(light["contrast"][k] or 0, 4.5, "%s reads on its light card (%s): %r" % (k, light["contrastOn"].get(k), light["contrast"]))
            self.assertGreaterEqual(wide["contrast"][k] or 0, 4.5, "%s reads on its dark card (%s): %r" % (k, wide["contrastOn"].get(k), wide["contrast"]))
            self.assertGreaterEqual(light["contrastPage"][k] or 0, 4.5, "%s reads on the bare light page too" % k)
        self.assertEqual(light["contrastOn"]["coordinate"], "box", "the first coordination word is on a boxed incoming card: the measurement that matters")
        # T320 (the user 2026-09-10): the kind word wears the prose weight, not bold, in both themes, and its three
        # colours are ONE sequential ramp ranked coordination < delegation < question: monotone in luminance against
        # the page (brighter with rank on the dark page, darker with rank on the light one), as the browser computes them
        for m in (wide, light, r["light340"]):
            self.assertEqual({k: m["kindWeight"][k] for k in ("delegate", "coordinate", "question")},
                             {"delegate": "400", "coordinate": "400", "question": "400"}, "prose weight: %r" % m["kindWeight"])
        def _lum(css):
            r, g, b = [int(x) for x in re.findall(r"\d+", css)[:3]]
            ch = lambda c: (c / 255) / 12.92 if (c / 255) <= 0.03928 else (((c / 255) + 0.055) / 1.055) ** 2.4
            return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
        d = [_lum(wide["kinds"][k]) for k in ("coordinate", "delegate", "question")]
        self.assertTrue(d[0] < d[1] < d[2], "dark page: the ramp brightens with rank: %r" % wide["kinds"])
        lt = [_lum(light["kinds"][k]) for k in ("coordinate", "delegate", "question")]
        self.assertTrue(lt[0] > lt[1] > lt[2], "light page: the ramp deepens with rank: %r" % light["kinds"])
        # the phone-width light pass reads too (the fourth screenshot the user looks at)
        for k in ("delegate", "coordinate", "question"):
            self.assertGreaterEqual(r["light340"]["contrast"][k] or 0, 4.5, "%s reads on the light page at 340 px" % k)


if __name__ == "__main__":
    unittest.main()
