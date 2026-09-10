#!/usr/bin/env python3
"""T262i (the user 2026-09-08): a queued copy of the kernel's — a romp mail card in the tail group (T243) — leaves the
queue when the CLI takes it and LANDS as an absorbed atom (T252d), and those two facts reach the pane in different
pushes: the queue state first, the transcript record later. Between the two the tail is SHORTER by the card, the
browser clamps a reader within that height of the bottom, and the landed card then grows the tail back below them:
the reader ends a card above the bottom, follow mode off, the jump chip shown — the flap the user saw on a session
that receives mail all day, with no pending send of their own.

The executed guard drives the real /chat page against a hermetic kernel and injects the two pushes as SEPARATE
frames (the page's own frame listener, window.postMessage, with the kernel's real frame as the base): a queue frame
adding the mail card, a queue frame without it (taken, not landed), then a transcript frame landing the atom that
carries the card's id. Asserted for a bottom reader: the distance from the bottom is 0 after every frame, the page
files no "scrollgesture" row (an unwritten move) and no "tail-shrink" write (a clamp corrected after the fact), the
jump chip never shows, and the card's slot is continuous (a queued or landing card is on the page between the two
frames). For an off-bottom reader: scrollTop never changes. Red on main at the queue-without-copy frame. Also the
id-less copy (an older kernel, the tmux route): held for one push by text, then dropped. SYNTHETIC fixtures only;
skips loudly without the extension deps or a Playwright browser.
"""
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
TEXT = "and also update the docstring"
TEXT2 = "then run the formatter"
MAIL = "<!-- romp-injected -->[romp mail from api] the fixtures batch is labeled; the export gzip is next <!-- romp-msg-id: 11111111.2222_33333.TESTHOST -->"


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
const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
// capture every kernel frame from the first byte (the FULL session frame arrives at load), and the page's scroll rows
await page.addInitScript(() => {
  window.__rows = []; window.__frames = [];
  window.addEventListener("message", (e) => { const m = e.data; if (m && (m.type === "session" || m.type === "update" || m.type === "chatTail")) window.__frames.push(m); });
  // while the synthetic sequence runs, the kernel's own frames for this session are held back (a status-only tail
  // would truncate the injected events): the shim's socket handler is wrapped at the prototype
  window.__quiet = false;
  const desc = Object.getOwnPropertyDescriptor(WebSocket.prototype, "onmessage");
  Object.defineProperty(WebSocket.prototype, "onmessage", { configurable: true, get() { return desc.get.call(this); },
    set(fn) { desc.set.call(this, (ev) => { if (window.__quiet) { try { const m = JSON.parse(ev.data); if (m && (m.type === "chatTail" || m.type === "update" || m.type === "session" || m.type === "status")) return; } catch (e) {} } return fn.call(this, ev); }); } });
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && m.type === "clientDiag" && m.surface === "chat" && /^scroll/.test(m.what || "")) window.__rows.push({ what: m.what, writer: m.data && m.data.writer, before: m.data && m.data.before, after: m.data && m.data.after, sh: m.data && m.data.sh }); } catch (e) {}
    return orig.call(this, d);
  };
});
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForSelector(".turn.turn-user", { timeout: 20000 });
await page.waitForTimeout(600);
// the FULL session frame is the base for every synthetic push (a chatTail carries only a suffix)
await page.waitForFunction(() => window.__frames.some((m) => m.type === "session" && Array.isArray(m.events) && m.events.length > 3), null, { timeout: 20000 });
const base = await page.evaluate(() => { const fr = window.__frames.filter((m) => m.type === "session" && Array.isArray(m.events)); return fr[fr.length - 1]; });
base.events = base.events.filter((e) => !(e.uuid || "").startsWith("optimistic:"));
const measure = () => page.evaluate(() => {
  const content = document.getElementById("content");
  const chip = document.getElementById("jump-bottom");
  const card = document.querySelector(".turn-queued:not(.turn-queued-hidden) .queued-bubble, .turn.turn-user .romp-bubble, .turn.turn-user .user-note");
  return {
    top: Math.round(content.scrollTop * 10) / 10, dist: Math.round((content.scrollHeight - content.scrollTop - content.clientHeight) * 10) / 10, sh: content.scrollHeight,
    chip: !!chip && chip.offsetParent !== null && getComputedStyle(chip).display !== "none" && getComputedStyle(chip).visibility !== "hidden" && !chip.hidden,
    queuedCards: document.querySelectorAll(".turn-queued:not(.turn-queued-hidden) .queued-bubble").length,
    landing: document.querySelectorAll(".turn-queued .queued-bubble.landing, .queued-landing").length,
    landedMail: Array.from(document.querySelectorAll(".turn.turn-user")).filter((t) => (t.textContent || "").includes("fixtures batch is labeled")).length,
    gestures: window.__rows.filter((r) => r.what === "scrollgesture").length,
    shrinks: window.__rows.filter((r) => r.what === "scrollwrite" && r.writer === "tail-shrink").length,
  };
});
// frames: the queue with the mail card; the queue without it (taken, not landed); the transcript landing it
const inject = (frame) => page.evaluate((f) => { window.postMessage(f, "*"); }, frame);
const withCard = (b, qid) => ({ ...b, type: "update", events: [...b.events.filter((e) => e.kind !== "queued"), { kind: "queued", texts: [{ md: cfg.mail, romp: true, cancelable: true, idx: 0, ...(qid ? { qid, qts: Date.now() } : {}) }] }] });
const without = (b) => ({ ...b, type: "update", events: b.events.filter((e) => e.kind !== "queued") });
const landed = (b, qid, uuid) => ({ ...b, type: "update", events: [...b.events.filter((e) => e.kind !== "queued"), { kind: "user", md: cfg.mail, uuid, ts: new Date().toISOString(), romp: true, absorbed: true, sentAt: Math.floor(Date.now() / 1000) - 5, ...(qid ? { qid } : {}) }] });
const run = async (label, qid, uuid) => {
  const out = { label };
  await inject(withCard(base, qid)); await page.waitForTimeout(400); out.card = await measure();
  await inject(without(base)); await page.waitForTimeout(400); out.taken = await measure();
  await inject(without(base)); await page.waitForTimeout(400); out.taken2 = await measure();   // another push carrying the (empty) queue
  await inject(landed(base, qid, uuid)); await page.waitForTimeout(400); out.landed = await measure();
  return out;
};
// a bottom reader; from here on only the synthetic frames reach the page
await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; window.__rows = []; window.__quiet = true; });
await page.waitForTimeout(300);
const start = await measure();
const idPath = await run("id", "echo:m1", "am1");
// the landed atom stays in the base for the next rounds
base.events = [...base.events.filter((e) => e.kind !== "queued"), { kind: "user", md: cfg.mail, uuid: "am1", ts: new Date().toISOString(), romp: true, absorbed: true, qid: "echo:m1" }];
await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; });
await page.waitForTimeout(300);
const textPath = await run("text", null, "am2");
base.events = [...base.events, { kind: "user", md: cfg.mail, uuid: "am2", ts: new Date().toISOString(), romp: true, absorbed: true }];
// an off-bottom reader: nothing may move
await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = Math.max(0, c.scrollHeight - c.clientHeight - 300); });
await page.waitForTimeout(300);
const offStart = await measure();
const off = await run("off", "echo:m3", "am3");
const rows = await page.evaluate(() => window.__rows);
fs.writeSync(1, "RESULT:" + JSON.stringify({ start, idPath, textPath, offStart, off, rows: rows.slice(-40), baseType: base.type, baseEvents: base.events.length }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedQueuedCopyHeld(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="queued-held-")
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
             "lastSid": SID, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        # the kernel finds a session's transcript under Claude's project dir: EVERY non-alphanumeric char of the
        # realpath becomes '-' (jd._proj_dir). A slashes-only munge missed the '_' pytest's temp root can carry,
        # so the kernel found no transcript and drew an API-error turn instead (the batch-only flake, 2026-09-08).
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # a running turn: the reply is tall enough to overflow the pane, then a tool call whose result is in
        t0 = int(time.time()) - 900
        cls.t0 = t0
        recs = [
            {"type": "user", "timestamp": iso(t0), "uuid": "u1", "parentUuid": None, "promptSource": "sdk", "sessionId": SID,
             "message": {"role": "user", "content": "tighten the notes-api search"}},
            {"type": "assistant", "timestamp": iso(t0 + 10), "uuid": "a1", "parentUuid": "u1", "sessionId": SID,
             "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "\n\n".join("Paragraph %d of the reply." % i for i in range(14))}]}},
            {"type": "user", "timestamp": iso(t0 + 39), "uuid": "u2", "parentUuid": "a1", "promptSource": "sdk", "sessionId": SID,
             "message": {"role": "user", "content": "drop the unused import"}},
            {"type": "assistant", "timestamp": iso(t0 + 41), "uuid": "a2", "parentUuid": "u2", "sessionId": SID,
             "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": "tu_a2_0", "name": "Bash", "input": {"command": "uv run pytest -q"}}]}},
            {"type": "user", "timestamp": iso(t0 + 50), "uuid": "tr1", "parentUuid": "a2", "sessionId": SID,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_a2_0", "content": "3 passed"}]}},
        ]
        cls.transcript = os.path.join(proj, SID + ".jsonl")
        Path(cls.transcript).write_text("".join(json.dumps(r) + "\n" for r in recs))
        # the steps the session runs while the send waits, and the splice the CLI writes when it takes it
        cls.steps = [
            {"type": "assistant", "timestamp": iso(t0 + 120), "uuid": "a3", "parentUuid": "tr1", "sessionId": SID,
             "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": "tu_a3_0", "name": "Bash", "input": {"command": "uv run pytest -q tests/test_search.py"}}]}},
            {"type": "user", "timestamp": iso(t0 + 121), "uuid": "tr2", "parentUuid": "a3", "sessionId": SID,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_a3_0", "content": "5 passed"}]}},
            {"type": "assistant", "timestamp": iso(t0 + 122), "uuid": "a4", "parentUuid": "tr2", "sessionId": SID,
             "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": "tu_a4_0", "name": "Bash", "input": {"command": "git diff --stat"}}]}},
            {"type": "user", "timestamp": iso(t0 + 125), "uuid": "tr3", "parentUuid": "a4", "sessionId": SID,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_a4_0", "content": "1 file changed"}]}},
        ]
        # sent at t0+55, taken at the t0+125 boundary (tr3, its file-order predecessor): 70 s later, so the landed
        # bubble's hover names the send time
        cls.landing = {"type": "attachment", "timestamp": iso(t0 + 55), "uuid": "att1", "parentUuid": "tr3", "isSidechain": False,
                       "sessionId": SID, "attachment": {"type": "queued_command", "prompt": TEXT}}
        cls.port = _free_port()
        cls.token = "testtok-queuedheld"
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
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)



    def test_a_taken_but_unlanded_copy_keeps_its_slot_so_the_reader_never_moves(self):
        cfg = os.path.join(self.lab, "cfg.json")
        step = {"type": "assistant", "timestamp": iso(self.t0 + 60), "uuid": "a3", "parentUuid": "tr1", "sessionId": SID,
                "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                            "content": [{"type": "text", "text": "Removed the import."}]}}
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "transcript": self.transcript,
                       "sid": SID, "step": step, "mail": MAIL}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        print("T262I:", json.dumps({k: r[k] for k in ("start", "idPath", "textPath", "offStart", "off", "baseType", "baseEvents")}), json.dumps(r["rows"][-20:]))
        idp, txt, off = r["idPath"], r["textPath"], r["off"]
        self.assertEqual(r["start"]["dist"], 0, "a bottom reader to begin with: %r" % r["start"])
        # the identified copy: the card, then the queue without it, then the landing — the reader never moves
        self.assertEqual(idp["card"]["queuedCards"], 1, "the mail card is queued at the tail: %r" % idp["card"])
        for k in ("card", "taken", "taken2", "landed"):
            self.assertEqual(idp[k]["dist"], 0, "at the bottom after the %s frame: %r" % (k, idp[k]))
            self.assertFalse(idp[k]["chip"], "the jump chip never shows (%s): %r" % (k, idp[k]))
        # the user's symptom: the tail shrinking under a bottom reader moves them UP (a clamp the page never wrote)
        self.assertGreaterEqual(idp["taken"]["top"], idp["card"]["top"], "the tail never shrinks under the reader between the queue frame and the landing: %r → %r" % (idp["card"], idp["taken"]))
        self.assertGreaterEqual(idp["taken"]["sh"], idp["card"]["sh"], "the transcript never loses the card's height while it is in flight: %r → %r" % (idp["card"], idp["taken"]))
        self.assertEqual(idp["taken"]["queuedCards"] + idp["taken"]["landedMail"], 1,
                         "between the queue frame and the transcript frame the card's slot is continuous: %r" % idp["taken"])
        self.assertEqual(idp["taken"]["landing"], 1, "…the held card is marked landing: %r" % idp["taken"])
        self.assertEqual(idp["landed"]["landedMail"], 1, "the landed atom took the slot: %r" % idp["landed"])
        self.assertEqual(idp["landed"]["queuedCards"], 0, "…and the held copy is gone with it: %r" % idp["landed"])
        self.assertEqual(idp["landed"]["gestures"], 0, "no unwritten move through the identified sequence: %r" % r["rows"][-12:])
        self.assertEqual(idp["landed"]["shrinks"], 0, "no tail-shrink correction: nothing shrank under the reader: %r" % r["rows"][-12:])
        # the id-less copy: held by text for one push, then dropped at the next queue frame (never a phantom)
        self.assertEqual((txt["taken"]["queuedCards"], txt["taken"]["landing"]), (1, 1), "an id-less copy is held for the push it vanished on: %r" % txt["taken"])
        self.assertGreaterEqual(txt["taken"]["top"], txt["card"]["top"], "an id-less copy in flight holds the tail too: %r → %r" % (txt["card"], txt["taken"]))
        self.assertEqual(txt["taken2"]["queuedCards"], 0, "…and dropped at the next push that carries the queue: %r" % txt["taken2"])
        self.assertEqual(txt["landed"]["landedMail"], 2, "its landing arrives on its own: %r" % txt["landed"])
        self.assertEqual(txt["landed"]["gestures"], idp["landed"]["gestures"], "no unwritten move through the id-less sequence: %r" % r["rows"][-12:])
        # an off-bottom reader: nothing moves at all
        for k in ("card", "taken", "taken2", "landed"):
            self.assertEqual(off[k]["top"], r["offStart"]["top"], "an off-bottom reader is never moved (%s): %r vs %r" % (k, off[k], r["offStart"]))


    def test_our_own_send_in_the_fed_gap_is_one_bubble(self):
        # the tail fix's review: with our copy gone from the queue (held by the pane) and the kernel's echo showing, the
        # echo cover skipped hiding the held copy — two bubbles for one message (three with the echo)
        # Also the wire's half of the copy's identity, executed on the served page: the send posts an id minted at the
        # press and our bubble's ✕ carries the same one. The kernel's frames here wear an id of the kernel's own (one
        # that took none from the press), so the copies are read by text; the frames under the pressed id are the
        # unit tests'.
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "text": "and also update the docstring"}, f)
        driver = os.path.join(self.lab, "driver-fed.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER_FED)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        print("T262M:", json.dumps(r))
        self.assertEqual(r["dropped"], 1, "the send was dropped at the socket: the pane's bubble is the only copy of ours")
        self.assertEqual((r["pressed"]["bubbles"], r["pressed"]["users"]), (1, 0), "our bubble at the press: %r" % r["pressed"])
        self.assertRegex(r["posted"] or "", r"^echo:[0-9a-f]{32}$", "the send posted the copy's id, minted at the press in the kernel's echo form: %r" % (r["posted"],))
        self.assertEqual(r["bubbleQid"], r["posted"], "our bubble's ✕ carries the id the send posted: %r" % ((r["bubbleQid"], r["posted"]),))
        self.assertEqual((r["queued"]["bubbles"], r["queued"]["users"]), (1, 0), "the kernel's queued copy under an id of its own hidden for ours, by text: %r" % r["queued"])
        self.assertEqual(r["fed"]["total"], 1, "the fed gap: the echo hidden, the held copy hidden, ours the one bubble: %r" % r["fed"])
        self.assertEqual(r["fed2"]["total"], 1, "…and on the next push too: %r" % r["fed2"])
        self.assertEqual((r["landed"]["bubbles"], r["landed"]["users"]), (0, 1), "the landing takes the slot: %r" % r["landed"])


DRIVER_FED = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
await page.addInitScript(() => {
  window.__frames = []; window.__quiet = false; window.__dropped = 0;
  window.addEventListener("message", (e) => { const m = e.data; if (m && (m.type === "session" || m.type === "update" || m.type === "chatTail")) window.__frames.push(m); });
  const desc = Object.getOwnPropertyDescriptor(WebSocket.prototype, "onmessage");
  Object.defineProperty(WebSocket.prototype, "onmessage", { configurable: true, get() { return desc.get.call(this); },
    set(fn) { desc.set.call(this, (ev) => { if (window.__quiet) { try { const m = JSON.parse(ev.data); if (m && (m.type === "chatTail" || m.type === "update" || m.type === "session" || m.type === "status")) return; } catch (e) {} } return fn.call(this, ev); }); } });
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { try { const m = JSON.parse(d); if (m && m.type === "sendMessage") { window.__dropped++; window.__posted = m.qid; return; } } catch (e) {} return orig.call(this, d); };
});
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForSelector(".turn.turn-user", { timeout: 20000 });
await page.waitForFunction(() => window.__frames.some((m) => m.type === "session" && Array.isArray(m.events) && m.events.length > 3), null, { timeout: 20000 });
const base = await page.evaluate(() => { const fr = window.__frames.filter((m) => m.type === "session" && Array.isArray(m.events)); return fr[fr.length - 1]; });
base.events = base.events.filter((e) => !(e.uuid || "").startsWith("optimistic:"));
const measure = () => page.evaluate((text) => {
  const vis = (n) => !!n && n.style.display !== "none" && !n.classList.contains("turn-echo-hidden") && !n.classList.contains("turn-queued-hidden");
  const bubbles = Array.from(document.querySelectorAll(".turn-queued .queued-bubble")).filter((b) => vis(b.closest(".turn-queued")) && (b.textContent || "").includes(text));
  const users = Array.from(document.querySelectorAll(".turn.turn-user")).filter((t) => vis(t) && (t.textContent || "").includes(text));
  return { bubbles: bubbles.length, users: users.length, total: bubbles.length + users.length,
           landingMarked: document.querySelectorAll(".turn-queued .queued-bubble.landing").length };
}, cfg.text);
const inject = (frame) => page.evaluate((f) => { window.postMessage(f, "*"); }, frame);
// our send (dropped at the socket): its bubble
await page.evaluate(() => { window.__quiet = true; });
await page.fill("#composer-input", cfg.text);
await page.press("#composer-input", "Enter");
await page.waitForSelector(".turn-queued", { timeout: 10000 });
await page.waitForTimeout(300);
const pressed = await measure();
// the id the send posted, and the id our bubble's ✕ carries: one id, minted at the press
const posted = await page.evaluate(() => window.__posted);
const bubbleQid = await page.evaluate(() => { const x = document.querySelector(".turn-queued .queued-x"); return x ? x.dataset.qid : null; });
// a kernel that minted its own id for the copy (it took none from the press): our copy hidden for ours, by text
await inject({ ...base, type: "update", events: [...base.events, { kind: "queued", texts: [{ md: cfg.text, qid: "echo:m9", qts: Date.now(), cancelable: true, idx: 0 }] }] });
await page.waitForTimeout(400);
const queued = await measure();
// the fed gap: the copy left the queue (held by the pane), the kernel's echo shows
await inject({ ...base, type: "update", events: [...base.events, { kind: "user", md: cfg.text, uuid: "echo:m9", ts: new Date().toISOString() }] });
await page.waitForTimeout(400);
const fed = await measure();
await inject({ ...base, type: "update", events: [...base.events, { kind: "user", md: cfg.text, uuid: "echo:m9", ts: new Date().toISOString() }] });
await page.waitForTimeout(400);
const fed2 = await measure();
// the landing takes the slot
await inject({ ...base, type: "update", events: [...base.events, { kind: "user", md: cfg.text, uuid: "am9", ts: new Date().toISOString(), qid: "echo:m9", human: true }] });
await page.waitForTimeout(400);
const landed = await measure();
fs.writeSync(1, "RESULT:" + JSON.stringify({ pressed, posted, bubbleQid, queued, fed, fed2, landed, dropped: await page.evaluate(() => window.__dropped) }) + "\n");
await browser.close();
process.exit(0);
"""



if __name__ == "__main__":
    unittest.main()
