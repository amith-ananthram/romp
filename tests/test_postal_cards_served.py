#!/usr/bin/env python3
"""T302 (the user 2026-09-10): the postal cards in the chat pane, rendered by the real /chat page of a hermetic
kernel over a synthetic chat with web (the viewed session), api and tests (its peers) — every state at once.

Asserted on the page: the interaction KIND is coloured text (Delegation / Coordination / Question in the old chip
colours), never a chip; the DELIVERY STATE is one icon per state at the head's right edge with a worded title
(delivered, read, parked, bounced, recalled, and sent for a message handed to the relay), from what the kernel
files (the send-time stamp, and the postal ledger's exec / relayed / bounced / recall rows joined by message id);
both ENDS wear their sessions' colours (the peer's chip, then this session's own chip); no card wears a background
wash; incoming cards are boxed, sent ones slim; a sent card whose message has not landed wears the pending
send's own provisional dress (the queued bubble's class). With POSTAL_SHOTS=<dir> the driver also writes
screenshots at 1000 px and 520 px. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC
fixtures only (the notes-api demo world: web / api / tests; host TESTHOST)."""
import json
import os
import re
import shutil
import socket
import subprocess
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
                                  "input": {"to": to, "body": body, "kind": kind}}]}},
        {"type": "user", "timestamp": iso(t + 1), "uuid": uuid + "r", "parentUuid": uuid, "sessionId": WEB,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu, "content": result, "is_error": is_error}]}},
    ]


def sent_row(mid, frm, frm_id, to_id, body, t, kind, park=False):
    r = {"t": t, "ev": "sent", "id": mid, "from": frm, "from_id": frm_id, "to_id": to_id, "body": body, "kind": kind, "from_host": ""}
    if park:
        r["park"] = True
    return r


# The world: nine cards, every kind and every state. Bodies are invented notes-api chatter.
def world(t0):
    msgs = {
        "in-deleg": ("api", API, "delegate", "Take the retry-loop rewrite in notes-api: exponential backoff with jitter, cap at two minutes, tests included."),
        "in-coord": ("tests", TESTS, "coordinate", "Heads-up: the integration suite now needs the fixtures directory; nothing to do on your side."),
        "in-quest": ("api", API, "question", "Which cap do we want on the backoff, two minutes or five?"),
        "out-deliv": ("web", WEB, "coordinate", "The backoff branch is up; review when you have a moment."),
        "out-read": ("web", WEB, "question", "Did the fixtures land on your side yet?"),
        "out-parked": ("web", WEB, "delegate", "Please run the whole integration suite once the fixtures are in."),
        "out-queued": ("web", WEB, "coordinate", "The remote build is green; merging in an hour unless you object."),
        "out-bounced": ("web", WEB, "delegate", "Take the cap decision and write it down in the README."),
        "out-recalled": ("web", WEB, "coordinate", "Ignore my last note, wrong thread."),
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
        ("out-queued", "TESTHOST:api", "peer:TESTHOST", "Delivered to 'TESTHOST:api'.", False, []),
        ("out-bounced", "api", API, "Delivered to 'api' as a handoff — they own it now.", False,
         [lambda t: {"t": t + 30, "ev": "bounced", "id": "out-bounced", "why": "refused: the mailbox is isolated"}]),
        ("out-recalled", "tests", TESTS, "Delivered to 'tests'.", False,
         [lambda t: {"t": t + 40, "ev": "recall", "id": "out-recalled"}]),
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
  const cards = Array.from(document.querySelectorAll(".turn-postal-service")).map((t) => {
    const n = t.querySelector(".notice");
    const kind = t.querySelector(".postal-kind");
    const icon = t.querySelector(".postal-delivery");
    const self = t.querySelector(".notice-src-self");
    const peer = t.querySelector(".notice-src-chip:not(.notice-src-self)");
    const cs = getComputedStyle(n);
    return {
      dir: t.classList.contains("postal-service-in") ? "in" : "out",
      boxed: t.classList.contains("notice-boxed"), slim: n.classList.contains("notice-slim"),
      kind: kind ? kind.textContent : null, kindColor: kind ? getComputedStyle(kind).color : null,
      kindClipped: kind ? kind.scrollWidth > kind.clientWidth + 1 : null,
      chip: !!t.querySelector(".notice-chip, .postal-service-intent"),
      state: icon ? icon.dataset.state : null, title: icon ? (icon.getAttribute("aria-label") || "") : null,
      iconRight: icon && n ? Math.round(n.getBoundingClientRect().right - icon.getBoundingClientRect().right) : null,
      peerText: peer ? peer.textContent : null, peerBg: peer ? getComputedStyle(peer).backgroundColor : null,
      selfText: self ? self.textContent : null, selfBg: self ? getComputedStyle(self).backgroundColor : null,
      selfWidth: self ? Math.round(self.getBoundingClientRect().width) : null,
      bg: cs.backgroundColor, border: cs.borderTopStyle, provisional: n.classList.contains("queued-bubble"),
      gist: (t.querySelector(".notice-gist") || {}).textContent || "",
      gistWrap: t.querySelector(".notice-gist") ? getComputedStyle(t.querySelector(".notice-gist")).whiteSpace : null,
      collapsible: n.classList.contains("notice-collapsible"),
      selfDot: !!(self && self.querySelector(".peer-dot")),
    };
  });
  // the page colour under the text, and the two kind colours, for a contrast check per theme
  const pg = document.createElement("div"); pg.style.background = "var(--bg)"; document.body.appendChild(pg);
  const pageBg = getComputedStyle(pg).backgroundColor; pg.remove();
  const kinds = {};
  for (const k of ["delegate", "coordinate", "question"]) { const e = document.querySelector(".postal-kind-" + k); if (e) kinds[k] = getComputedStyle(e).color; }
  return { boxBg, pageBg, kinds, theme: document.body.classList.contains("theme-light") ? "light" : "dark", cards, pendingBubbleClass: !!document.querySelector(".queued-bubble") };
});
const contrast = (a, b) => {
  const lum = (css) => { const m = css.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number).map((v) => v / 255).map((c) => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)); return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]; };
  const la = lum(a), lb = lum(b); return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
};
const results = {};
let page;
for (const pass of [{ width: 1000, theme: "dark" }, { width: 520, theme: "dark" }, { width: 340, theme: "dark" }, { width: 1000, theme: "light" }]) {
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
  m.contrast = { delegate: m.kinds.delegate ? contrast(m.kinds.delegate, m.pageBg) : null, coordinate: m.kinds.coordinate ? contrast(m.kinds.coordinate, m.pageBg) : null, question: m.kinds.question ? contrast(m.kinds.question, m.pageBg) : null };
  results[pass.theme === "light" ? "light" : String(width)] = m;
  if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/romp_chat-postal-cards-" + (pass.theme === "light" ? "light" : width) + ".png", fullPage: false }); }
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
        cls.count = 9
        cls.port, cls.token = _free_port(), "testtok-postal"
        env = dict(os.environ, XDG_STATE_HOME=os.path.join(cls.lab, "xdg"), CLAUDE_CONFIG_DIR=claude,
                   ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1", ROMP_SERVE_TOKEN=cls.token,
                   ROMP_KERNEL_PORT=str(cls.port), ROMP_DIST_DIR=dist, ROMP_MODEL_CATALOG="off",
                   ROMP_POSTAL_PORT=str(_free_port()), ROMP_POSTAL_PEERS="0", ROMP_POSTAL_CLIENT_ONLY="1")
        for k in ("ROMP_STATE_DIR", "ROMP_API_KEY_CMD", "ANTHROPIC_API_KEY"):
            env.pop(k, None)
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
        self.assertEqual(len(cards), 9, cards)
        by = {c["gist"][:24]: c for c in cards}
        def card(prefix):
            m = [c for c in cards if c["gist"].startswith(prefix)]
            self.assertEqual(len(m), 1, "one card starts with %r: %r" % (prefix, [c["gist"] for c in cards]))
            return m[0]
        # (1) the kind: coloured text, never a chip
        for c in cards:
            self.assertIn(c["kind"], ("Delegation", "Coordination", "Question"), c)
            self.assertFalse(c["chip"], "no chip: %r" % c)
        self.assertEqual(card("Take the retry-loop")["kindColor"], "rgb(176, 140, 255)", "delegation violet")
        self.assertEqual(card("Heads-up")["kindColor"], "rgb(20, 184, 166)", "coordination teal")
        # (2) the delivery icon per state, at the head's right edge, with a worded title
        states = {c["gist"][:20]: c["state"] for c in cards}
        self.assertIsNone(card("Take the retry-loop")["state"], "an incoming message in hand: no icon")
        self.assertEqual(card("Heads-up")["state"], "parked", "waited while web was offline")
        self.assertIn("waited while you were offline", card("Heads-up")["title"], "in hand: the title says it waited, not that it will deliver")
        self.assertEqual(card("The backoff branch")["state"], "delivered")
        self.assertEqual(card("Did the fixtures")["state"], "read", "the ledger's exec row: the recipient consumed it")
        self.assertEqual(card("Please run the whole")["state"], "parked")
        self.assertEqual(card("The remote build")["state"], "sent", "handed to the relay, no far-host ack yet")
        self.assertEqual(card("Take the cap decision")["state"], "bounced")
        self.assertEqual(card("Ignore my last note")["state"], "recalled")
        for c in cards:
            if c["state"]:
                self.assertTrue(c["title"] and c["title"].split(" ")[0] in ("Delivered", "Read", "Parked", "Sent", "Bounced", "Recalled"), c)
                self.assertLessEqual(c["iconRight"], 16, "the icon sits at the head's right edge: %r" % c)
        self.assertIn("isolated", card("Take the cap decision")["title"], "a bounce carries its reason")
        # (3) both ends, each in its session's colour; no wash; boxed vs slim
        for c in cards:
            self.assertTrue(c["peerText"], c)
            self.assertTrue(c["selfText"] and "web" in c["selfText"], "this session's own end: %r" % c)
            self.assertEqual(c["selfBg"], "rgb(156, 210, 255)", "web's own colour on its chip: %r" % c)
            if c["dir"] == "in":
                self.assertTrue(c["boxed"] and not c["slim"], "incoming is boxed: %r" % c)
                self.assertEqual(c["bg"], wide["boxBg"], "no wash: the box is the plain box colour: %r" % c)
                if not c["collapsible"]:
                    self.assertEqual(c["gistWrap"], "normal", "a boxed one-liner with nothing to fold wraps, never an ellipsis with nothing behind it: %r" % c)
            else:
                self.assertTrue(c["slim"], "sent stays slim: %r" % c)
            self.assertFalse(c["selfDot"], "the own chip wears no working dot: %r" % c)
        self.assertEqual(card("Take the retry-loop")["peerBg"], "rgb(30, 161, 235)", "api's colour on its chip")
        self.assertEqual(card("Heads-up")["peerBg"], "rgb(84, 178, 4)", "tests' colour on its chip")
        # (amendment) the provisional dress: the pending send's own class on the not-yet-landed sent cards only
        prov = {c["gist"][:20]: c["provisional"] for c in cards}
        self.assertTrue(card("Please run the whole")["provisional"], "parked → provisional")
        self.assertTrue(card("The remote build")["provisional"], "queued for relay → provisional")
        for pfx in ("The backoff branch", "Did the fixtures", "Take the cap decision", "Ignore my last note", "Take the retry-loop"):
            self.assertFalse(card(pfx)["provisional"], "landed, bounced, recalled and incoming are solid: %r" % pfx)
        self.assertEqual(card("Please run the whole")["border"], "dashed", "the bubble's dashed border by the shared rule")
        # (narrow) both colours still show: this session's chip collapses to its dot
        for c in narrow["cards"]:
            self.assertTrue(c["selfBg"] == "rgb(156, 210, 255)" and c["selfWidth"] is not None and c["selfWidth"] <= 12,
                            "at 520 px the own chip is its coloured dot: %r" % c)
            self.assertIn(c["kind"], ("Delegation", "Coordination", "Question"), "the kind is never truncated: %r" % c)
        # (phone) under the 360 px container query the GIST yields; the kind word is never clipped
        for c in phone["cards"]:
            self.assertFalse(c["kindClipped"], "at 340 px the kind word is whole: %r" % c)
        # (light theme) the two raw kind colours re-ink for the cream page: text contrast at or above 4.5:1
        self.assertEqual(light["theme"], "light")
        for k in ("delegate", "coordinate", "question"):
            self.assertGreaterEqual(light["contrast"][k] or 0, 4.5, "%s reads on the light page: %r" % (k, light["contrast"]))
            self.assertGreaterEqual(wide["contrast"][k] or 0, 4.5, "%s reads on the dark page: %r" % (k, wide["contrast"]))


if __name__ == "__main__":
    unittest.main()
