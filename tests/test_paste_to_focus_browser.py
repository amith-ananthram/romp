#!/usr/bin/env python3
"""Paste-to-focus, executed in a real browser (the user 2026-09-05/06): after selecting transcript text,
a paste with nothing editable focused lands in the message box.

The source pins in ui/webview/composer-paste-focus.test.ts say the window listener exists and reads the
right gates; nothing there proves a TRUSTED paste on the bare page reaches it — the paste event's target
is the element holding the selection start, not the body, and the shell installs capture keydown handlers
into every pane document, either of which could have swallowed it unseen. This is the executed guard:
a hermetic kernel serves the real /chat page and the real shell, a synthetic session renders a real
transcript, the driver drag-selects inside a .turn (activeElement = body, the reply chip seeded), copies
text the trusted way, presses Ctrl+V, and the box must hold the text with focus and the chip intact —
on the bare chat page AND inside the shell's chat iframe. The type-to-focus sibling (a printable key
from anywhere) is checked in the same breath, since both ride one gate list.

Skips LOUDLY without the extension deps or a playwright browser (CI installs none); it executes on any
dev box with the extension installed, which is where ships are gated. All fixtures synthetic.
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
REPLY = "The web session finished the notes-api login flow and every test passes now. Next up is the password reset path."


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

// runs inside the CHAT document: probes on the paste, read-only
const PROBE = () => {
  const w = window; w.__p = [];
  w.addEventListener("paste", (e) => w.__p.push({ phase: "capture", inTurn: !!(e.target && e.target.closest && e.target.closest("#content .turn")),
    target: e.target && (e.target.id || e.target.tagName), trusted: e.isTrusted, text: e.clipboardData ? e.clipboardData.getData("text/plain") : null }), true);
  w.addEventListener("paste", (e) => w.__p.push({ phase: "bubble-late", defaultPrevented: e.defaultPrevented }));
};
const STATE = () => ({ active: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null,
  value: document.getElementById("composer-input").value, sel: String(getSelection()).slice(0, 30),
  chip: (document.getElementById("composer-chips")?.textContent || "").slice(0, 60), pastes: window.__p });
// the trusted way onto the clipboard: a temp box + execCommand("copy"), removed again so focus falls back to body
const COPY = (t) => { const x = document.createElement("textarea"); document.body.appendChild(x); x.value = t; x.focus(); x.select();
  const ok = document.execCommand("copy"); x.remove(); return ok; };
const RESET = () => { const ta = document.getElementById("composer-input"); ta.value = ""; ta.blur(); getSelection().removeAllRanges(); window.__p = []; };

async function selectReply(frame, page) {
  // the user's gesture: a mouse drag across the assistant's sentence — focus goes to the body, selectionchange seeds the chip
  const p = frame.locator("#content .turn p", { hasText: "finished the notes-api" }).first();   // the assistant's line, not the prompt that also says "login flow"
  const b = await p.boundingBox();
  await page.mouse.move(b.x + 4, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + Math.min(180, b.width - 4), b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(80);
}
async function run(label, frame, page) {
  await frame.waitForSelector("#content .turn p", { timeout: 20000 });
  await frame.waitForTimeout(300);
  await frame.evaluate(PROBE);
  const copied = await frame.evaluate(COPY, "dictated after selecting");
  await selectReply(frame, page);
  const selected = await frame.evaluate(STATE);
  await page.keyboard.press("Control+v");
  await page.waitForTimeout(150);
  const pasted = await frame.evaluate(STATE);
  // the sibling default: a printable key from the bare area lands in the box too
  await frame.evaluate(RESET);
  await selectReply(frame, page);
  await page.keyboard.type("ok");
  await page.waitForTimeout(80);
  const typed = await frame.evaluate(STATE);
  return { label, copied, selected, pasted, typed };
}

const out = {};
const page = await browser.newPage({ viewport: { width: 1100, height: 720 } });
await page.goto(cfg.chat);
out.bare = await run("bare chat page", page.mainFrame(), page);

const shell = await browser.newPage({ viewport: { width: 1400, height: 800 } });
await shell.goto(cfg.landing);
const chat = await (async () => { for (let i = 0; i < 200; i++) { const f = shell.frames().find((f) => /\/chat/.test(f.url())); if (f) return f; await shell.waitForTimeout(50); } return null; })();
if (!chat) { console.error("no chat iframe in the shell"); process.exit(1); }
await chat.waitForLoadState();
await shell.waitForFunction(() => { const b = document.getElementById("romp-boot"); return !b || b.classList.contains("gone") || getComputedStyle(b).opacity === "0" || getComputedStyle(b).display === "none"; }, null, { timeout: 20000 }).catch(() => {});
await shell.evaluate(() => { document.getElementById("romp-boot")?.remove(); });   // belt and braces: the splash must not eat the drag
out.shell = await run("chat iframe in the shell", chat, shell);

fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedPaste(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served paste needs them")
        cls.lab = tempfile.mkdtemp(prefix="paste-to-focus-")
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
        claude = os.path.join(cls.lab, "claude")
        # the kernel finds a session's transcript under Claude's project dir: EVERY non-alphanumeric char of the
        # realpath becomes '-' (jd._proj_dir). A slashes-only munge missed the '_' pytest's temp root can carry,
        # so the kernel found no transcript and drew an API-error turn instead (the batch-only flake, 2026-09-08).
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # a CLOSED turn: an open one would invite the boot reconcile to resume it — no real CLI here
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-555555555555", "parentUuid": None,
                        "timestamp": "2026-09-05T00:00:00.000Z", "sessionId": SID,
                        "message": {"role": "user", "content": "Where do we stand on the login flow?"}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": "22222222-3333-4444-5555-666666666666",
                        "parentUuid": "11111111-2222-3333-4444-555555555555",
                        "timestamp": "2026-09-05T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-fable-5-1",
                                    "content": [{"type": "text", "text": REPLY}],
                                    "stop_reason": "end_turn"}}) + "\n")
        cls.port = _free_port()
        cls.token = "testtok-pastefocus"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = open(os.path.join(cls.lab, "kernel.log"), "w")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=cls.klog, stderr=subprocess.STDOUT, env=env)
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
        if getattr(cls, "klog", None):
            cls.klog.close()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        base = "http://127.0.0.1:%d" % self.port
        with open(cfg, "w") as f:
            json.dump({"chat": base + "/chat?token=" + self.token, "landing": base + "/?token=" + self.token}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served paste needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def _check(self, r):
        label = r["label"]
        self.assertTrue(r["copied"], "%s: the trusted copy must land on the clipboard: %r" % (label, r))
        # (a) the gesture: transcript text selected, nothing editable focused, the reply chip seeded
        self.assertEqual(r["selected"]["active"], "BODY", "%s: the drag must leave focus on the body: %r" % (label, r["selected"]))
        self.assertTrue(r["selected"]["sel"].startswith("The web session"), "%s: the drag must select the reply: %r" % (label, r["selected"]))
        self.assertIn("The web session", r["selected"]["chip"], "%s: the selection seeds the reply chip: %r" % (label, r["selected"]))
        self.assertEqual(r["selected"]["value"], "", "%s: nothing in the box before the paste: %r" % (label, r["selected"]))
        # (b) the trusted paste: fired at the selection's element (inside a .turn), claimed by the window listener
        pastes = r["pasted"]["pastes"]
        cap = next((x for x in pastes if x["phase"] == "capture"), None)
        self.assertIsNotNone(cap, "%s: a trusted Ctrl+V must dispatch a paste on the page: %r" % (label, r["pasted"]))
        self.assertTrue(cap["trusted"] and cap["inTurn"], "%s: the paste targets the transcript node holding the selection: %r" % (label, cap))
        self.assertEqual(cap["text"], "dictated after selecting", "%s: clipboardData carries the text: %r" % (label, cap))
        late = next((x for x in pastes if x["phase"] == "bubble-late"), None)
        self.assertTrue(late and late["defaultPrevented"], "%s: the window listener claims the paste (preventDefault): %r" % (label, pastes))
        # (c) the outcome the user sees: text in the box, caret there, chip still armed for the send
        self.assertEqual(r["pasted"]["value"], "dictated after selecting", "%s: the box holds the pasted text: %r" % (label, r["pasted"]))
        self.assertEqual(r["pasted"]["active"], "composer-input", "%s: focus moved into the box: %r" % (label, r["pasted"]))
        self.assertIn("The web session", r["pasted"]["chip"], "%s: the chip survives the paste: %r" % (label, r["pasted"]))
        # the sibling default rides the same gates: a printable key from the bare area types into the box
        self.assertEqual(r["typed"]["value"], "ok", "%s: type-to-focus still lands: %r" % (label, r["typed"]))
        self.assertEqual(r["typed"]["active"], "composer-input", "%s: type-to-focus focuses the box: %r" % (label, r["typed"]))

    def test_a_trusted_paste_after_selecting_transcript_text_lands_in_the_box(self):
        out = self._drive()
        self._check(out["bare"])     # the standalone /chat page
        self._check(out["shell"])    # the same page as the shell's chat iframe, under the shell's capture chords


if __name__ == "__main__":
    unittest.main()
