#!/usr/bin/env python3
"""T262h (the user 2026-09-08): the pending bubble's node must never leave the DOM between two paints while its
entry is pending. The user's laptop rows showed the reader, sitting at the bottom with a send pending, moved UP by
the bubble's height (64 px for a one-line bubble, 95 px for two) with no scroll write between the rows — the
browser clamping scrollTop to a scrollHeight without the bubble — and then following no more: atBottom false,
follow mode off, new content no longer pulling them down.

The executed guard drives the real /chat page against a hermetic kernel with a bottom reader and a pending send
(dropped at the socket, so the client's bubble is the only copy), then appends a transcript step every 0.5 s for
T262H_SECONDS seconds (default 60) — one kernel push each — and after every push checks: the distance from the
bottom is 0; the page filed no "scrollgesture" row (the page's own classification of a scroll no write explains:
an UNWRITTEN move) and no "tail-shrink" write (the observer correcting a clamp that already happened); the
`.turn-queued` node is the SAME element it was at the press (its harness mark survives) and no removal of a
`.turn-queued` node was observed. Then the same with two sends and a ✕ on the first. SYNTHETIC fixtures only;
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
SECONDS = float(os.environ.get("T262H_SECONDS", "60"))


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
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForSelector(".turn.turn-user", { timeout: 20000 });
await page.waitForTimeout(600);
// instrumentation: the page's own scroll rows (clientDiag frames on the socket), our sends dropped at the socket,
// removals of any .turn-queued node, and a mark on the pending node to prove identity across pushes
await page.evaluate(() => {
  const orig = WebSocket.prototype.send;
  window.__dropped = 0; window.__rows = []; window.__removals = 0; window.__frames = 0;
  window.addEventListener("message", (e) => { const m = e.data; if (m && (m.type === "session" || m.type === "update" || m.type === "chatTail")) window.__frames++; });
  WebSocket.prototype.send = function (d) {
    try {
      const m = JSON.parse(d);
      if (m && m.type === "sendMessage") { window.__dropped++; return; }
      if (m && m.type === "clientDiag" && m.surface === "chat" && /^scroll/.test(m.what || "")) window.__rows.push({ what: m.what, writer: m.data && m.data.writer, before: m.data && m.data.before, after: m.data && m.data.after, sh: m.data && m.data.sh, top: m.data && m.data.top });
    } catch (e) {}
    return orig.call(this, d);
  };
  const content = document.getElementById("content");
  // a removal counts only when the node is NOT re-added within the same mutation batch (a same-task move keeps it on the
  // page across the paint; a removal that survives the batch is a frame without the bubble)
  const mo = new MutationObserver((muts) => {
    const removed = new Set(); const added = new Set();
    for (const m of muts) { for (const n of m.removedNodes) removed.add(n); for (const n of m.addedNodes) added.add(n); }
    for (const n of removed) if (n instanceof HTMLElement && n.classList.contains("turn-queued") && !n.classList.contains("turn-queued-hidden") && !added.has(n) && !n.isConnected) window.__removals++;
  });
  mo.observe(content, { childList: true, subtree: true });
});
const measure = () => page.evaluate(() => {
  const content = document.getElementById("content");
  const g = document.querySelector(".turn-queued:not(.turn-queued-hidden)");
  return {
    dist: Math.round((content.scrollHeight - content.scrollTop - content.clientHeight) * 10) / 10,
    marked: g ? g.dataset.h262 === "1" : null, texts: g ? Array.from(g.querySelectorAll(".queued-bubble")).length : 0,
    units: document.querySelectorAll(".turn[data-unit]").length, removals: window.__removals,
    gestures: window.__rows.filter((r) => r.what === "scrollgesture").length,
    shrinks: window.__rows.filter((r) => r.what === "scrollwrite" && r.writer === "tail-shrink").length,
    rows: window.__rows.length,
  };
});
// a bottom reader
await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; });
await page.waitForTimeout(300);
await page.evaluate(() => { window.__rows = []; });
await page.fill("#composer-input", cfg.text);
await page.press("#composer-input", "Enter");
await page.waitForSelector(".turn-queued", { timeout: 10000 });
await page.evaluate(() => { const g = document.querySelector(".turn-queued:not(.turn-queued-hidden)"); if (g) g.dataset.h262 = "1"; });
await page.waitForTimeout(300);
const pressed = await measure();
// pushes every 0.5 s: one transcript step each, alternating a tool call and its result
const pushes = [];
let n = 0; const t1 = Date.now(); let parent = cfg.parent; let t = cfg.t0 + 60;
const step = (i) => {
  if (i % 2 === 0) { const r = { type: "assistant", timestamp: cfg.iso(t + i), uuid: "s" + i, parentUuid: parent, sessionId: cfg.sid,
      message: { role: "assistant", model: "claude-fable-5-1", stop_reason: "tool_use", content: [{ type: "tool_use", id: "tu_s" + i, name: "Bash", input: { command: "true # step " + i } }] } }; parent = "s" + i; return r; }
  const r = { type: "user", timestamp: cfg.iso(t + i), uuid: "s" + i, parentUuid: parent, sessionId: cfg.sid,
      message: { role: "user", content: [{ type: "tool_result", tool_use_id: "tu_s" + (i - 1), content: "ok" }] } }; parent = "s" + i; return r;
};
cfg.iso = (x) => new Date(x * 1000).toISOString().replace(/\.\d{3}Z$/, ".000Z");
while (Date.now() - t1 < cfg.seconds * 1000) {
  const before = await page.evaluate(() => window.__frames);
  fs.appendFileSync(cfg.transcript, JSON.stringify(step(n)) + "\n");
  n++;
  try { await page.waitForFunction((b) => window.__frames > b, before, { timeout: 5000 }); } catch (e) { pushes.push({ i: n, timeout: true }); }
  await page.waitForTimeout(500);
  pushes.push({ i: n, ...(await measure()) });
}
const one = { pressed, pushes, n };
// two sends and a ✕ on the first
await page.fill("#composer-input", cfg.text2);
await page.press("#composer-input", "Enter");
await page.waitForFunction((t2) => Array.from(document.querySelectorAll(".turn-queued")).some((g) => (g.textContent || "").includes(t2)), cfg.text2, { timeout: 10000 });
await page.waitForTimeout(300);
const two = await measure();
const twoPushes = [];
for (let k = 0; k < 6; k++) {
  const before = await page.evaluate(() => window.__frames);
  fs.appendFileSync(cfg.transcript, JSON.stringify(step(n)) + "\n"); n++;
  try { await page.waitForFunction((b) => window.__frames > b, before, { timeout: 5000 }); } catch (e) {}
  await page.waitForTimeout(500);
  twoPushes.push(await measure());
}
// the ✕ on the FIRST bubble
await page.evaluate(() => { const xs = Array.from(document.querySelectorAll(".turn-queued:not(.turn-queued-hidden) .queued-x")); if (xs[0]) xs[0].click(); });
await page.waitForTimeout(400);
const afterX = await measure();
for (let k = 0; k < 4; k++) {
  const before = await page.evaluate(() => window.__frames);
  fs.appendFileSync(cfg.transcript, JSON.stringify(step(n)) + "\n"); n++;
  try { await page.waitForFunction((b) => window.__frames > b, before, { timeout: 5000 }); } catch (e) {}
  await page.waitForTimeout(500);
  twoPushes.push(await measure());
}
const rows = await page.evaluate(() => window.__rows);
fs.writeSync(1, "RESULT:" + JSON.stringify({ one, two, twoPushes, afterX, rows: rows.slice(-40) }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedPendingBubbleStable(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="pending-stable-")
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
        cls.token = "testtok-pendingstable"
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


    def test_the_pending_bubble_keeps_its_node_and_the_bottom_reader_stays_at_the_bottom(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "text": TEXT, "text2": TEXT2,
                       "transcript": self.transcript, "sid": SID, "t0": self.t0, "parent": "tr1", "seconds": SECONDS}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=int(SECONDS) + 240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        one, pushes = r["one"], r["one"]["pushes"]
        print("T262H:", json.dumps({"pressed": one["pressed"], "dists": [p.get("dist") for p in pushes], "marked": [p.get("marked") for p in pushes],
                                     "removals": pushes[-1].get("removals") if pushes else None, "gestures": pushes[-1].get("gestures") if pushes else None,
                                     "shrinks": pushes[-1].get("shrinks") if pushes else None, "two": r["two"], "afterX": r["afterX"],
                                     "twoDists": [p.get("dist") for p in r["twoPushes"]], "rows": r["rows"][-30:]}))
        self.assertGreaterEqual(len(pushes), max(4, int(SECONDS // 4)), "enough pushes landed: %r" % len(pushes))
        self.assertEqual([p["i"] for p in pushes if p.get("timeout")], [], "every push repainted: %r" % pushes[:3])
        self.assertEqual(one["pressed"]["dist"], 0, "at the bottom with the bubble at the press: %r" % one["pressed"])
        worst = max(p["dist"] for p in pushes)
        self.assertLessEqual(worst, 2, "a bottom reader with a pending send stays at the bottom through every push (worst distance %r): %r" % (worst, [p["dist"] for p in pushes]))
        self.assertEqual(pushes[-1]["gestures"], 0, "no unwritten move (no scroll the page could not attribute to a write): %r" % r["rows"][-12:])
        self.assertEqual(pushes[-1]["shrinks"], 0, "no tail-shrink correction: nothing shrank the tail under the reader: %r" % r["rows"][-12:])
        self.assertTrue(all(p["marked"] is True for p in pushes), "the pending bubble's node is the SAME element after every push: %r" % [p["marked"] for p in pushes])
        self.assertEqual(pushes[-1]["removals"], 0, "no .turn-queued node was ever removed while pending")
        # two sends and a ✕
        two, tp, ax = r["two"], r["twoPushes"], r["afterX"]
        self.assertEqual(two["texts"], 2, "two pending sends in the one group: %r" % two)
        self.assertLessEqual(max(p["dist"] for p in tp), 2, "…and the reader stays at the bottom through pushes, before and after the ✕: %r" % [p["dist"] for p in tp])
        self.assertEqual(ax["texts"], 1, "the ✕ removed one bubble: %r" % ax)
        self.assertEqual(ax["dist"], 0, "the ✕ leaves the reader at the bottom: %r" % ax)
        self.assertEqual(tp[-1]["gestures"], 0, "no unwritten move across the second send and the ✕: %r" % r["rows"][-12:])


if __name__ == "__main__":
    unittest.main()
