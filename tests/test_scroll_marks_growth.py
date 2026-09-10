#!/usr/bin/env python3
"""T245 (the user 2026-09-07): the chat scrollbar's blue user-message notches drifted once a turn with many
inline figures was on screen — a notch sat well below the thumb while the message it marked was in view.

Convicted at the source: contentOffsetFrame is a virtual prefix-sum over cached measured unit heights, and
the cache is refreshed only inside a paint (paintScrollMarks ← scheduleRailSticky's rAF, whose triggers are
scroll, resize and a few render-side callers). previewFull's figures are lazy, async-decoded images with
no intrinsic size before load, so every figure that loads after the paint grows its turn and the content's
scrollHeight with NO repaint: the native thumb moves to the new truth while the notches keep the stale,
smaller frame. Nothing keyed the paint on "a rendered unit changed height".

The executed guard here drives the real /chat page: after the first paint a rendered turn GROWS (a block
appended to the last turn — the same thing a late figure does), and the user message's notch must follow
the scrollbar's truth (red on main: the notch stays where the stale frame put it).

Second executed guard (review of the fix, 2026-09-08): with every unit rendered the frame reads the real
scrollbar, and that must hold when ONE UNIT OWNS SEVERAL .turn NODES — compact mode tags an expanded tool
group's child turns, and any absorbed cue, with their unit's data-unit. The first cut gated the exact frame
on the NODE count equalling the unit count, so opening a tool group silently dropped the frame back to the
virtual sum (measured 11px off on a five-unit page) and the notch changed basis on every expand/collapse.

Both skip LOUDLY without the extension deps or a Playwright browser (CI installs none); the CI-safe pins
ride ui/webview/scroll-marks.test.ts. All fixtures synthetic.
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
const page = await browser.newPage({ viewport: { width: 1000, height: 600 } });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
try { await page.waitForSelector(".scroll-marks .scroll-mark", { timeout: 20000 }); }
catch (e) {   // say what the page held instead of a bare timeout
  const st = await page.evaluate(() => ({ turns: document.querySelectorAll(".turn[data-unit]").length,
    box: (document.querySelector(".scroll-marks") || {}).outerHTML || null, sh: document.getElementById("content")?.scrollHeight }));
  console.error("no notch painted: " + JSON.stringify(st)); process.exit(1);
}
await page.waitForTimeout(600);
const measure = () => page.evaluate(() => {
  const content = document.getElementById("content");
  const c = content.getBoundingClientRect();
  const marks = Array.from(document.querySelectorAll(".scroll-marks .scroll-mark")).map((m) => parseFloat(m.style.top));
  // the FIRST user turn's slot middle in scroll space, as the scrollbar sees it
  const turn = document.querySelector(".turn.turn-user[data-unit]");
  const tr = turn.getBoundingClientRect();
  const mid = content.scrollTop + (tr.top - c.top) + tr.height / 2;
  const expected = Math.round((mid / content.scrollHeight) * (c.height - 4));
  const nodes = Array.from(document.querySelectorAll(".turn[data-unit]"));
  return { marks, expected, scrollHeight: content.scrollHeight, turns: nodes.length,
           units: new Set(nodes.map((n) => n.dataset.unit)).size, groups: document.querySelectorAll(".turn-toolgroup").length,
           groupOpen: !!document.querySelector(".turn-toolgroup.expanded") };
});
const before = await measure();
// a rendered unit GROWS after the paint — exactly what a late-loading figure does to its turn
await page.evaluate(() => {
  const turns = document.querySelectorAll(".turn[data-unit]");
  const last = turns[turns.length - 1];
  const block = document.createElement("div");
  block.style.height = "1500px"; block.textContent = "late figure stand-in";
  last.appendChild(block);
});
await page.waitForTimeout(400);            // several frames — the repaint is event-keyed, not timed; this is slack
const after = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-after-growth.png" });
fs.writeSync(1, "RESULT:" + JSON.stringify({ before, after }) + "\n");
await browser.close();
process.exit(0);
"""

# same page, same first paint; then the tool group is OPENED (compact mode is the default: its three child
# turns join the DOM tagged with the group's unit) and CLOSED again, the notch measured in each state
DRIVER_EXPAND = DRIVER.split("const before = await measure();")[0] + r"""
const collapsed = await measure();
await page.click(".turn-toolgroup .toolgroup-line");
await page.waitForSelector(".turn-toolgroup.expanded", { timeout: 10000 });
await page.waitForTimeout(400);
const expanded = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-group-open.png" });
await page.click(".turn-toolgroup .toolgroup-line");
await page.waitForSelector(".turn-toolgroup:not(.expanded)", { timeout: 10000 });
await page.waitForTimeout(400);
const closed = await measure();
fs.writeSync(1, "RESULT:" + JSON.stringify({ collapsed, expanded, closed }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedNotchFollowsGrowth(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="notch-growth-")
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
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        claude = os.path.join(cls.lab, "claude")
        # the kernel finds a session's transcript under Claude's project dir: EVERY non-alphanumeric char of the
        # realpath becomes '-' (jd._proj_dir). A slashes-only munge missed the '_' pytest's temp root can carry,
        # so the kernel found no transcript and drew an API-error turn instead (the batch-only flake, 2026-09-08).
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # a user message near the top, three consecutive tool calls (compact mode folds them into ONE tool
        # group — a unit that owns several .turn nodes once expanded), then a tall-ish assistant reply — the
        # notch marks the user turn
        t0 = "2026-09-07T00:00:%02d.000Z"
        recs = [{"type": "user", "uuid": "11111111-2222-3333-4444-555555555555", "parentUuid": None,
                 "timestamp": t0 % 0, "sessionId": SID, "message": {"role": "user", "content": "plot the retry curve"}}]
        prev = "11111111-2222-3333-4444-555555555555"
        for k in range(3):
            a = "33333333-4444-5555-6666-77777777777%d" % k
            r = "44444444-5555-6666-7777-88888888888%d" % k
            recs.append({"type": "assistant", "uuid": a, "parentUuid": prev, "timestamp": t0 % (1 + k), "sessionId": SID,
                         "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                                     "content": [{"type": "tool_use", "id": "tu_%d" % k, "name": "Bash",
                                                  "input": {"command": "uv run pytest -q tests/test_retry_%d.py" % k}}]}})
            recs.append({"type": "user", "uuid": r, "parentUuid": a, "timestamp": t0 % (1 + k), "sessionId": SID,
                         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_%d" % k, "content": "3 passed"}]}})
            prev = r
        recs.append({"type": "assistant", "uuid": "22222222-3333-4444-5555-666666666666",
                     "parentUuid": prev, "timestamp": t0 % 5, "sessionId": SID,
                     "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "\n\n".join("Paragraph %d of the reply." % i for i in range(12))}]}})
        Path(proj, SID + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.port = _free_port()
        cls.token = "testtok-notchgrowth"
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

    def _drive(self, script, name):
        cfg = os.path.join(self.lab, name + ".json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
                       "shots": os.environ.get("NOTCH_GROWTH_SHOTS", "")}, f)
        driver = os.path.join(self.lab, name + ".mjs")
        with open(driver, "w") as f:
            f.write(script)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def test_a_notch_follows_its_message_when_a_rendered_turn_grows_after_the_paint(self):
        r = self._drive(DRIVER, "growth")
        b, a = r["before"], r["after"]
        self.assertEqual(len(b["marks"]), 1, "one notch: the one user message (%r)" % b)
        self.assertGreater(a["scrollHeight"], b["scrollHeight"] + 1000, "the rendered turn did grow: %r → %r" % (b["scrollHeight"], a["scrollHeight"]))
        d_before = abs(b["marks"][0] - b["expected"])
        d_after = abs(a["marks"][0] - a["expected"])
        # THE FIX, two halves in one verdict so the evidence carries both numbers: (1) with every unit rendered
        # the frame is the scrollbar's own truth (on main the notch sat off its message by the turn gaps the
        # unit-height sum omits); (2) after a rendered unit grows — no scroll, no resize, no new event — the
        # notch follows (on main it stayed on the stale frame's position)
        self.assertTrue(d_before <= 3 and d_after <= 3,
                        "notch vs its message — before growth: notch %r expected %r (off by %d); after growth: notch %r expected %r (off by %d)"
                        % (b["marks"], b["expected"], d_before, a["marks"], a["expected"], d_after))

    def test_the_exact_frame_holds_when_one_unit_owns_several_turn_nodes(self):
        r = self._drive(DRIVER_EXPAND, "expand")
        c, e, z = r["collapsed"], r["expanded"], r["closed"]
        self.assertEqual(c["groups"], 1, "compact mode folded the three tool calls into one group: %r" % c)
        self.assertTrue(e["groupOpen"] and not c["groupOpen"] and not z["groupOpen"], "the group opened, then closed: %r" % r)
        # the expansion is the input under test: MORE .turn nodes than units, every unit still rendered, no spacer
        self.assertGreater(e["turns"], e["units"], "an open group's child turns carry the group's unit: %r" % e)
        self.assertEqual(e["units"], c["units"], "the unit set did not change: %r vs %r" % (c, e))
        self.assertEqual(len(c["marks"]), 1, "one notch: the one user message (%r)" % c)
        d = {k: abs(v["marks"][0] - v["expected"]) for k, v in (("collapsed", c), ("expanded", e), ("closed", z))}
        # red before the fix: collapsed and closed read the scrollbar's truth, expanded fell back to the unit-height
        # sum (off by the turn gaps it omits) — so the notch changed basis on every expand and collapse
        self.assertTrue(all(x <= 3 for x in d.values()),
                        "notch vs its message, off by %r px — collapsed: notch %r expected %r; expanded: notch %r expected %r; closed: notch %r expected %r"
                        % (d, c["marks"], c["expected"], e["marks"], e["expected"], z["marks"], z["expected"]))


if __name__ == "__main__":
    unittest.main()
