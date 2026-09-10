#!/usr/bin/env python3
"""T289 (the user 2026-09-09): a comment on a REMOTE session's transcript was refused as a create-by-name.

The comment dialog prefilled its name box from the viewer's DECORATED session name ("host:name", which
federation prefixes on every session-bearing frame) sanitized to "host-name-comment-N", and sent that
prefill as if the user had chosen it — so the owning kernel stored a thread named with the VIEWER's host
label, and the next dialog with the same count collided with it and was refused. The executed guard drives
the real /chat page of a hub kernel that has a SECOND hermetic kernel checked in as host TESTHOST (the same
handshake a mobile machine's reverse forward makes, so no ssh): the remote session's transcript is opened
from its host-prefixed tab, a passage is selected, the selection menu's Comment opens the dialog, and the
dialog's send is captured at the socket. Asserted: the prefilled name wears the BARE session name (no host
label) and the frame the dialog sends carries an EMPTY name (the owning kernel picks its default) on the
remote relay socket, addressed by the parent's sid. Red on main: the prefill and the frame both read
"TESTHOST-web-comment-1". The remote's own create door is not exercised to completion here (a hermetic
kernel has no SDK to fork the thread), so the refusal toast itself is pinned at the kernel unit level
(tests/test_comment_name_refusal_log.py). Skips LOUDLY without the extension deps or a Playwright browser.
SYNTHETIC fixtures only (host TESTHOST, session web, the notes-api demo world)."""
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
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)

SID = "aaaaaaaa-1111-2222-3333-444444444444"
REPLY = "Use exponential backoff with a jitter of ten percent."


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
const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
// capture every outbound frame at the socket, with the socket it left on; drop the create so nothing forks
await page.addInitScript(() => {
  const orig = WebSocket.prototype.send;
  window.__frames = [];
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && m.type === "commentCreate") { window.__frames.push({ url: this.url, msg: m }); return; } } catch (e) {}
    return orig.call(this, d);
  };
});
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
// the remote host's tab appears once the hub reports the check-in up and the relay socket is open
const remoteTab = page.locator("#tabs .tab", { hasText: "TESTHOST" }).first();
try { await remoteTab.waitFor({ timeout: 45000 }); }
catch (e) {
  const st = await page.evaluate(() => ({ tabs: (document.getElementById("tabs") || {}).textContent }));
  console.error("no remote tab: " + JSON.stringify(st)); process.exit(1);
}
await remoteTab.click();
await page.waitForFunction((t) => Array.from(document.querySelectorAll(".turn")).some((n) => (n.textContent || "").includes(t)), REPLY_PLACEHOLDER, { timeout: 30000 });
await page.waitForTimeout(500);
const info = await page.evaluate((t) => {
  const turn = Array.from(document.querySelectorAll(".turn")).find((n) => (n.textContent || "").includes(t));
  const md = turn.querySelector(".md") || turn;
  const walker = document.createTreeWalker(md, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes("exponential")) node = walker.nextNode();
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(node, 0); r.setEnd(node, Math.min(node.textContent.length, 24)); sel.addRange(r);
  const box = md.getBoundingClientRect();
  const ev = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: box.left + 20, clientY: box.top + 10, button: 2 });
  md.dispatchEvent(ev);
  const items = Array.from(document.querySelectorAll(".ctx-menu .ctx-item")).map((i) => i.textContent);
  return { selected: sel.toString(), items, tab: document.querySelector("#tabs .tab.active, #tabs .tab[aria-selected='true']")?.textContent || "" };
}, REPLY_PLACEHOLDER);
const comment = page.locator(".ctx-menu .ctx-item", { hasText: "Comment" }).first();
try { await comment.waitFor({ timeout: 5000 }); } catch (e) { console.error("no Comment item: " + JSON.stringify(info)); process.exit(1); }
await comment.click();
await page.waitForSelector("#cmt-pop .cmt-name", { timeout: 10000 });
const dialog = await page.evaluate(() => {
  const box = document.querySelector("#cmt-pop .cmt-name");
  return { value: box.value, prefill: box.dataset.prefill || null, title: (document.querySelector("#cmt-pop .cmt-title") || {}).textContent || "" };
});
await page.fill("#cmt-pop .cmt-input", "why jitter at all?");
await page.press("#cmt-pop .cmt-input", "Enter");
await page.waitForFunction(() => (window.__frames || []).length > 0, null, { timeout: 10000 });
const frames = await page.evaluate(() => window.__frames);
fs.writeSync(1, "RESULT:" + JSON.stringify({ info, dialog, frames }) + "\n");
await browser.close();
process.exit(0);
""".replace("REPLY_PLACEHOLDER", json.dumps(REPLY))


def _kernel(lab, name, port, token, records=None, sid=None):
    """Boot one hermetic kernel: its own state root, dist, and (optionally) one session with a transcript."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    cwd = os.path.join(lab, name, "proj")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
    if records is not None:
        Path(state, "names", sid).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", sid + ".json").write_text(json.dumps(
            {"sid": sid, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": sid, "alive": True, "model": "claude-opus-5", "liveModel": "Opus 5"}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    env = _lab.kernel_env(os.path.join(lab, name), claude, os.path.join(lab, "dist"), port, token,
                          ROMP_HOST_NAME=name.upper())
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, log
        except Exception:
            time.sleep(0.5)
    proc.kill(); proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


class ServedRemoteCommentName(unittest.TestCase):
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="remote-comment-name-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            shutil.rmtree(cls.lab, ignore_errors=True)
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.procs = []
        t0 = int(time.time()) - 900
        recs = [
            {"type": "user", "timestamp": iso(t0), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": SID,
             "message": {"role": "user", "content": "how should the notes-api retry loop back off?"}},
            {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": SID,
             "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": REPLY}]}},
        ]
        # the REMOTE kernel owns the session; the HUB has none of its own and shows the remote's through the relay
        cls.rport, cls.rtoken = _free_port(), "testtok-remote"
        cls.hport, cls.htoken = _free_port(), "testtok-hub"
        try:
            rp, cls.rlog = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, records=recs, sid=SID)
            cls.procs.append(rp)
            hp, cls.hlog = _kernel(cls.lab, "hub", cls.hport, cls.htoken)
            cls.procs.append(hp)
        except unittest.SkipTest:
            cls.tearDownClass()
            raise
        # the check-in handshake a mobile machine makes through its reverse forward: the hub records the peer
        # like an attached remote (no ssh of its own) and probes it on the port given — here the remote's own
        body = json.dumps({"host": "TESTHOST", "kernelPort": cls.rport, "busPort": _free_port(), "token": cls.rtoken}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/checkin?token=%s" % (cls.hport, cls.htoken), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ans = json.loads(resp.read().decode())
        if not ans.get("ok"):
            cls.tearDownClass()
            raise unittest.SkipTest("the hub refused the check-in: %r" % ans)
        # wait for the hub's supervisor to probe the peer and report it up (the browser dials only then)
        for _ in range(60):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (cls.hport, cls.htoken), timeout=3) as r2:
                    rows = json.loads(r2.read().decode()).get("tunnels") or []
            except Exception:
                rows = []
            row = next((t for t in rows if t.get("host") == "TESTHOST"), None)
            if row and row.get("status") == "up" and row.get("hasToken"):
                break
            time.sleep(0.5)
        else:
            cls.tearDownClass()
            raise unittest.SkipTest("the hub never reported the checked-in peer up: %r" % (rows,))

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, "procs", []):
            try:
                p.kill(); p.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_a_comment_on_a_remote_session_prefills_the_bare_name_and_sends_an_empty_one_to_the_owner(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.hport, self.htoken)}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
                         + "\nhub:\n" + open(self.hlog).read()[-1500:] + "\nremote:\n" + open(self.rlog).read()[-800:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        d, frames = r["dialog"], r["frames"]
        self.assertEqual(d["title"], "New comment:", "the dialog is the CREATE dialog: %r" % r["info"])
        self.assertEqual(d["value"], "web-comment-1", "the prefill wears the BARE session name, never the viewer's host label: %r" % d)
        self.assertNotIn("TESTHOST", d["value"])
        self.assertEqual(d["prefill"], d["value"], "the dialog remembers its prefill so an untouched one is sent as empty")
        self.assertEqual(len(frames), 1, "one create left the page: %r" % frames)
        fr = frames[0]
        self.assertIn("/remote/TESTHOST/ws", fr["url"], "addressed to the OWNING kernel through its relay: %r" % fr["url"])
        self.assertEqual(fr["msg"]["id"], SID, "the parent's sid, bare on the wire")
        self.assertEqual(fr["msg"]["name"], "", "an untouched prefill is sent as an EMPTY name: the owner picks its default")
        self.assertEqual(fr["msg"]["text"], "why jitter at all?")


if __name__ == "__main__":
    unittest.main()
