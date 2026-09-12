#!/usr/bin/env python3
"""Reordering a tab by drag lands it where the cursor was released (the user 2026-09-11, who could drag a
tab to the FIRST slot of a row but nowhere else: every other release put the tab somewhere other than under
the cursor). Their strip wraps onto two rows, a tag group on the first and the untagged trail on the second,
and the trail's row is where the drop lands wrong; a drag within the group's row still works.

The strip's drag is HTML5 drag-and-drop (render.ts wireTabDrag: dragstart / the #tabs dragover / drop). The
dragover hit-tests the pointer against a VIRTUAL wrap layout of the non-dragged items (dragslot.ts
dragSlotIndex): widths snapshotted at dragstart, the strip's flex-wrap simulated, the pointer's row read as
floor(y / rowH), and the slot as the first simulated midpoint right of x. With the tab groups on, the
untagged trail sits behind a zero-height ROW BREAK (makeRowBreak, .tab-group-break.tab-group-sep), which the
dragover maps to a zero-width box that opens a virtual row (`br`, T264), and it marks the box AFTER any
break as a row opener too (`br: isBreak(t) || isBreak(before(t))`): the trail's first TAB wears `br`. The
simulation's guard against opening an empty row is `cx > 0` (dragslot.ts), where cx is the row's fill so
far, and a zero-width box still adds the strip's column gap to it. Under the yatharth theme the strip has a
3px column gap (styles.css `body.chat-theme-yatharth #tabs { gap: 0 3px }`), so after the break cx is 3, the
guard fails, and the trail's first tab opens a THIRD virtual row: the break sits alone on the row that
floor(y / rowH) resolves the trail's pointer to, with a single midpoint at 0, every x on the row is past it,
and the slot falls to the next row's head, the trail's first tab. Every drop on the untagged row lands at
its first position, which is why a drag to the first slot is the one that works. Under the classic theme
the gap is 0, cx stays 0 after the break, and the row holds the trail's tabs as the strip does, so the
classic strip (and T264's own tests) never showed it. Present since T264 (74412c04, one group per row, the
layout in which the trail has a row of its own); a fix in the simulation (a zero-width row opener adding no
gap) was tried and reverted here, and it lands every one of these drops under the cursor.

This lab drives the real /chat page of a hermetic kernel: twenty sessions, three under one tag (the group's
row) and seventeen in no tag (the trail's row), REAL mouse drags (page.mouse down, a run of moves across the
strip, up over the target tab), the tab order read from the DOM and from the persisted arrangement
(romp:vieworder). The classic theme is the control; the yatharth theme is set the way the user sets it, the
`theme` key of the romp:settings store (theme.ts and the kernel's inline reader turn it into the body class).
Each case records the row layout (every tab's left/top/width), the release point and the landing rect, and
expects the tab to sit where the pointer was released: after every other tab on the release row whose
midpoint is left of the cursor once the dragged tab is taken out of the row (the pre-drag layout, the strip's
own virtual model), before the rest. The log each case keeps (every drag event's target and acceptance, the
strip's rebuilds) says, when a case fails, whether a drop reached the strip at all. Skips LOUDLY without the
extension deps or a Playwright browser. SYNTHETIC fixtures only (the notes-api demo world, host TESTHOST,
placeholder sids). TAB_DRAG_DIST=<dir> serves another tree's UI bundle (the bisect's before);
TAB_DRAG_DUMP=<path> writes the whole measurement."""
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

# the notes-api demo world: twenty sessions, so the untagged trail is a long row of its own
NAMES = ["web", "api", "deploy", "tests", "docs", "auth", "search", "cache", "ingest", "export", "billing", "mailer",
         "worker", "scheduler", "metrics", "backup", "importer", "notifier", "gateway", "indexer"]
SIDS = {n: "%s-1111-2222-3333-444444444444" % (chr(ord("a") + i) * 8) for i, n in enumerate(NAMES)}
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#1EA1EB", "#ffffff"), ("#54B204", "#ffffff"), ("#c98cff", "#1a0c2e"),
           ("#e5a50a", "#1a1200"), ("#4EC9B0", "#00201a")]
TAGGED = ["web", "api", "deploy"]   # the infra group: the strip's first row
TAGS = [{"id": "tag-infra", "name": "infra", "color": "#4EC9B0", "members": [SIDS[n] for n in TAGGED]}]


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
const page = await browser.newPage({ viewport: { width: 1900, height: 760 } });   // wide: the trail's seventeen tabs on one row
// the gesture's log: every drag event with its target and whether the page accepted it (defaultPrevented, read on the
// window, after the strip's own listener), plus every wholesale rebuild of #tabs (a burst of child removals), so a case
// that moved nothing says whether a drag happened, whether a drop reached the strip, and what stood under the pointer
await page.addInitScript(() => {
  window.__log = [];
  const L = (k, e) => window.__log.push({ k, t: Math.round(performance.now()), x: e ? Math.round(e.clientX) : null, y: e ? Math.round(e.clientY) : null,
    tgt: e && e.target ? String(e.target.className || e.target.nodeName) + (e.target.dataset && e.target.dataset.id ? "#" + e.target.dataset.id.slice(0, 8) : "") : null,
    prevented: e ? e.defaultPrevented : null });
  for (const k of ["dragstart", "dragover", "dragenter", "dragleave", "drop", "dragend", "pointercancel"]) window.addEventListener(k, (e) => L(k, e));
  // the kernel's frames, watched for a marker (a renamed session's new name): the event that says a push has LANDED on the
  // page, whatever the strip does with it
  window.__wsMarker = null; window.__wsMarkerSeen = false;
  const OrigWS = window.WebSocket;
  window.WebSocket = function (...a) { const ws = new OrigWS(...a);
    ws.addEventListener("message", (m) => { if (window.__wsMarker && typeof m.data === "string" && m.data.includes(window.__wsMarker)) { window.__wsMarkerSeen = true; L("push:" + window.__wsMarker, null); } });
    return ws; };
  window.WebSocket.prototype = OrigWS.prototype; Object.assign(window.WebSocket, OrigWS);
  const watch = () => { const bar = document.getElementById("tabs"); if (!bar) { setTimeout(watch, 50); return; }
    new MutationObserver((muts) => { const removed = muts.reduce((n, m) => n + m.removedNodes.length, 0);
      if (removed > 2) L("rebuild:-" + removed, null); }).observe(bar, { childList: true }); };
  watch();
});
const settle = async () => {
  await page.waitForSelector("#tabs .tab[data-id]", { timeout: 30000 });
  await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  await page.waitForTimeout(800);
};
await page.goto(cfg.chat);
await settle();
const r1 = (v) => Math.round(v * 10) / 10;
// the strip's layout: every #tabs child with its rect in the bar's own space, the tabs grouped into rows by top
const layout = () => page.evaluate(() => {
  const bar = document.getElementById("tabs"); const b = bar.getBoundingClientRect();
  const r1 = (v) => Math.round(v * 10) / 10;
  const items = Array.from(bar.children).map((el) => { const r = el.getBoundingClientRect();
    return { cls: el.className, id: el.dataset.id || null, group: el.dataset.group || null,
             name: el.dataset.id ? (el.querySelector(".tab-label") || el).textContent.trim() : el.className,
             top: r1(r.top - b.top), left: r1(r.left - b.left), w: r1(r.width), h: r1(r.height), draggable: !!el.draggable }; });
  const tabs = items.filter((i) => i.id);
  const tops = [...new Set(tabs.map((t) => t.top))].sort((a, b) => a - b);
  const rows = tops.map((top) => tabs.filter((t) => t.top === top));
  let stored = null; try { stored = JSON.parse(localStorage.getItem("romp:vieworder")); } catch (e) {}
  return { bar: { left: b.left, top: b.top, w: r1(b.width), h: r1(b.height), clientWidth: bar.clientWidth, gap: getComputedStyle(bar).columnGap },
           theme: document.body.classList.contains("chat-theme-yatharth") ? "yatharth" : "classic",
           items, tabs, rows, order: tabs.map((t) => t.id), stored };
});
const cases = [];
const fmt = (rows) => rows.map((r) => r.map((t) => t.name + "@" + t.left + "," + t.top + "+" + t.w + "x" + t.h));
// measure a finished gesture: where the cursor is, by the strip's own rule, is after every other tab whose midpoint is
// left of the cursor on the cursor's row, and after every tab on the rows above it — the other tabs as they stand with
// the dragged one taken out of its row (the PRE-drag layout, each tab right of it on its row moved left by its width and
// the gap: the strip's own virtual model). Never the settled layout: a wrong landing reshapes that, and an expectation
// read from it moves with the mistake.
async function record(label, pre, src, tgt, tx, ty) {
  const log = await page.evaluate(() => window.__log);
  const post = await layout();
  const nameOf = (id) => (pre.tabs.find((t) => t.id === id) || { name: id }).name;
  post.tabs.forEach((t) => { t.name = nameOf(t.id); });   // one name per tab across the gesture, by id (a push may have renamed one)
  const cx = r1(tx - pre.bar.left), cy = r1(ty - pre.bar.top);
  const gap = parseFloat(pre.bar.gap) || 0;
  const others = pre.tabs.filter((t) => t.id !== src.id);
  const onRow = (t) => t.top <= cy && cy < t.top + t.h;
  const leftOf = (t) => (t.top === src.top && t.left > src.left) ? t.left - src.w - gap : t.left;
  const expected = others.filter((t) => t.top + t.h <= cy || (onRow(t) && leftOf(t) + t.w / 2 < cx)).length;
  const actual = post.order.indexOf(src.id);
  const landed = post.tabs.find((t) => t.id === src.id);
  const count = (k) => log.filter((e) => e.k === k).length;
  cases.push({ label, theme: post.theme, gap: post.bar.gap,
               from: { name: src.name, index: pre.order.indexOf(src.id), draggable: src.draggable },
               over: tgt ? { name: tgt.name, left: tgt.left, w: tgt.w, top: tgt.top } : null,
               release: { x: cx, y: cy }, expected, actual, ok: expected === actual,
               landed: landed ? { left: landed.left, right: r1(landed.left + landed.w), top: landed.top,
                                  cursorInside: landed.left <= cx && cx <= landed.left + landed.w && landed.top <= cy && cy < landed.top + landed.h } : null,
               preRows: fmt(pre.rows), postRows: fmt(post.rows), preOrder: pre.order.map(nameOf), postOrder: post.order.map(nameOf),
               stored: Array.isArray(post.stored) ? post.stored.map(nameOf) : post.stored,
               ev: { dragstart: count("dragstart"), dragover: count("dragover"), drop: count("drop"), dragend: count("dragend") },
               tail: log.filter((e) => e.k !== "dragenter" && e.k !== "dragleave" && e.k !== "pointercancel").slice(-6).map((e) => e.k + "@" + e.x + (e.tgt ? " on " + e.tgt : "") + (e.prevented ? " accepted" : "")),
               items: post.items.map((i) => (i.id ? i.name : "[" + i.cls + "]") + "@" + i.left + "," + i.top + "+" + i.w + "x" + i.h) });
}
// drag the tab at rows[fromRow][fromIdx] and release over rows[toRow][toIdx] at `frac` of that tab's width (0.25 = its
// left part, so the tab should land BEFORE it; 0.75 = its right part, AFTER it): a run of moves across the strip, a rest
// over the target, the release
async function drag(label, fromRow, fromIdx, toRow, toIdx, frac) {
  const pre = await layout();
  const src = pre.rows[fromRow] && pre.rows[fromRow][fromIdx], tgt = pre.rows[toRow] && pre.rows[toRow][toIdx];
  if (!src || !tgt) { cases.push({ label, skipped: "no such tab: rows=" + JSON.stringify(pre.rows.map((r) => r.map((t) => t.name))) }); return; }
  const sx = pre.bar.left + src.left + src.w / 2, sy = pre.bar.top + src.top + src.h / 2;
  const tx = pre.bar.left + tgt.left + tgt.w * frac, ty = pre.bar.top + tgt.top + tgt.h / 2;
  await page.evaluate(() => { window.__log = []; });
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(sx + 4, sy + 1, { steps: 2 });   // the drag starts on the first moves after the press
  await page.mouse.move(tx, ty, { steps: 16 });          // a run of dragover ticks across the strip
  // a rest over the target: the hand stops before it lets go. THREE still ticks, not one: the last moving tick's hop slides
  // a new element under the pointer, Chromium answers the next tick with dragenter (which the strip does not cancel: no
  // dragenter listener, so that tick's drop operation is none) and only the tick after with an accepted dragover; a release
  // straight after the hop is refused by the browser (no drop, dragend cancels, the tab snaps home). That refusal is a
  // hazard of its own, seen here as the intermittent drop=0; this test is about where an ACCEPTED drop lands.
  for (let i = 0; i < 3; i++) await page.mouse.move(tx, ty);
  await page.mouse.up();
  await page.waitForTimeout(500);
  await page.mouse.move(900, 700);                        // off the strip: no hover tip in the next measurement
  await record(label, pre, src, tgt, tx, ty);
}
// the tab at rows[fromRow][fromIdx] dragged to rows[toRow][toIdx] with a REAL kernel push landing mid-drag: partway across,
// the driver renames another session through the kernel's headless POST /rename (the names file is what the pusher
// watches, so the new name re-pushes to the page), waits for the frame carrying the new name to reach the page, then
// finishes the gesture. The browser fires pointercancel at dragstart (a drag cancels the pointer); the strip's click-safe
// hold used to release on it, so the push rebuilt #tabs under the drag, detached the dragged node, and the drop moved
// nothing. Held through the drag, the push's render waits for dragend: no rebuild between dragstart and drop, the drop
// lands under the cursor, and the new name shows once the gesture is over.
async function dragAcrossPush(label, fromRow, fromIdx, toRow, toIdx, frac, renameFrom, renameTo) {
  const pre = await layout();
  const src = pre.rows[fromRow] && pre.rows[fromRow][fromIdx], tgt = pre.rows[toRow] && pre.rows[toRow][toIdx];
  if (!src || !tgt) { cases.push({ label, skipped: "no such tab: rows=" + JSON.stringify(pre.rows.map((r) => r.map((t) => t.name))) }); return; }
  const sx = pre.bar.left + src.left + src.w / 2, sy = pre.bar.top + src.top + src.h / 2;
  const tx = pre.bar.left + tgt.left + tgt.w * frac, ty = pre.bar.top + tgt.top + tgt.h / 2;
  const mx = (sx + tx) / 2;
  await page.evaluate((m) => { window.__log = []; window.__wsMarker = m; window.__wsMarkerSeen = false; }, renameTo);
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(sx + 4, sy + 1, { steps: 2 });
  await page.mouse.move(mx, ty, { steps: 8 });            // halfway: the drag is live
  const resp = await fetch(cfg.rename, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ target: renameFrom, name: renameTo }) });
  const ans = await resp.json();
  const pushed = await page.waitForFunction(() => window.__wsMarkerSeen, null, { timeout: 15000 }).then(() => true).catch(() => false);
  await page.waitForTimeout(300);                          // whatever the page does with the frame has had its turn
  const shownDuring = await page.evaluate((n) => Array.from(document.querySelectorAll("#tabs .tab-label")).some((l) => l.textContent.trim() === n), renameTo).catch(() => null);
  await page.mouse.move(tx, ty, { steps: 8 });            // …and the gesture goes on to the target
  for (let i = 0; i < 3; i++) await page.mouse.move(tx, ty);
  await page.mouse.up();
  await page.waitForTimeout(500);
  await page.mouse.move(900, 700);
  const shownAfter = await page.waitForFunction((n) => Array.from(document.querySelectorAll("#tabs .tab-label")).some((l) => l.textContent.trim() === n), renameTo, { timeout: 10000 })
    .then(() => true).catch(() => false);
  await record(label, pre, src, tgt, tx, ty);
  const log = await page.evaluate(() => window.__log);
  const t0 = (log.find((e) => e.k === "dragstart") || {}).t, t1 = (log.find((e) => e.k === "drop" || e.k === "dragend") || {}).t;
  const rebuiltMidDrag = log.some((e) => e.k.startsWith("rebuild") && e.t > t0 && e.t < t1);
  Object.assign(cases[cases.length - 1], { rename: ans, pushed, shownDuring, shownAfter, rebuiltMidDrag });
}
const out = { grouped: null, yatharth: null };
// 1. the CLASSIC theme, the control: row 0 = the infra group, the last row = the untagged trail
let L = await layout();
out.grouped = { theme: L.theme, gap: L.bar.gap, rows: L.rows.map((r) => r.map((t) => t.name)), bar: L.bar,
                items: L.items.map((i) => (i.id ? i.name : "[" + i.cls + "]") + "@" + i.left + "," + i.top + "+" + i.w + "x" + i.h) };
let trail = L.rows.length - 1;
await drag("classic: trail, 3rd tab to the FIRST slot (left part of the 1st)", trail, 2, trail, 0, 0.25);
await drag("classic: trail, 1st tab to the 3rd slot (right part of the 3rd)", trail, 0, trail, 2, 0.75);
await drag("classic: trail, 4th tab to the 2nd slot (left part of the 2nd)", trail, 3, trail, 1, 0.25);
await drag("classic: group row, 1st tab to the 3rd slot (right part of the 3rd)", 0, 0, 0, 2, 0.75);
// 2. the YATHARTH theme, set as the user sets it: the theme key of this browser's settings store, read at boot
await page.evaluate(() => { let s = {}; try { s = JSON.parse(localStorage.getItem("romp:settings") || "{}") || {}; } catch (e) {}
                            s.theme = "yatharth"; localStorage.setItem("romp:settings", JSON.stringify(s)); });
await page.reload();
await settle();
L = await layout();
out.yatharth = { theme: L.theme, gap: L.bar.gap, rows: L.rows.map((r) => r.map((t) => t.name)), bar: L.bar,
                 items: L.items.map((i) => (i.id ? i.name : "[" + i.cls + "]") + "@" + i.left + "," + i.top + "+" + i.w + "x" + i.h) };
trail = L.rows.length - 1;
await drag("yatharth: trail, 3rd tab to the FIRST slot (left part of the 1st)", trail, 2, trail, 0, 0.25);
await drag("yatharth: trail, 1st tab to the 3rd slot (right part of the 3rd)", trail, 0, trail, 2, 0.75);
await drag("yatharth: trail, 4th tab to the 2nd slot (left part of the 2nd)", trail, 3, trail, 1, 0.25);
await drag("yatharth: trail, 2nd tab to the 5th slot (right part of the 5th)", trail, 1, trail, 4, 0.75);
await drag("yatharth: group row, 1st tab to the 3rd slot (right part of the 3rd)", 0, 0, 0, 2, 0.75);
// 3. a kernel push mid-drag (the trail's last tab is renamed while the trail's 2nd tab is on its way to the 6th slot)
L = await layout();
const lastName = L.rows[trail][L.rows[trail].length - 1].name;
await dragAcrossPush("push mid-drag: trail, 2nd tab to the 6th slot (right part of the 6th)", trail, 1, trail, 5, 0.75, lastName, lastName + "-renamed");
out.cases = cases;
fs.writeFileSync(cfg.out, JSON.stringify(out));   // a file, not stdout: the per-case logs outgrow one pipe write
fs.writeSync(1, "RESULT:" + cfg.out + "\n");
await browser.close();
process.exit(0);
"""


class ServedTabDragReorder(unittest.TestCase):
    maxDiff = None
    result = None

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
        cls.lab = tempfile.mkdtemp(prefix="tab-drag-")
        # TAB_DRAG_DIST=<dir>: a UI bundle built from another tree, served by this kernel (the bisect's "before": the
        # strip is the bundle's, the kernel's wire the same); by default this tree's own build
        before = os.environ.get("TAB_DRAG_DIST", "")
        if before:
            src = before
        else:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
            src = os.path.join(EXT, "dist")
        dist = os.path.join(cls.lab, "dist")
        copy_dist(src, dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        for i, name in enumerate(NAMES):
            sid = SIDS[name]
            bg, fg = PALETTE[i % len(PALETTE)]
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            recs = [{"type": "user", "timestamp": iso(t0 + i), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "what does the %s session do in notes-api?" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "It keeps the %s side of the notes-api tidy." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        # one tag holding three sessions: the strip groups by tag, the group on its own row, the other seventeen in the trail
        Path(state, "timeline-views.json").write_text(json.dumps({"active": "all", "tags": TAGS}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-tabdrag"
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

    @classmethod
    def _run(cls):
        """One driver run for every case (the drags are sequential on one page); its result, or its failure, is shared by
        the tests (a failed run is not repeated per test)."""
        if cls.result is not None:
            if isinstance(cls.result, BaseException):
                raise cls.result
            return cls.result
        try:
            cls.result = cls._drive()
        except BaseException as e:
            cls.result = e
            raise
        return cls.result

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": len(NAMES), "out": out,
                       "rename": "http://127.0.0.1:%d/rename?token=%s" % (cls.port, cls.token)}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None or not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        result = json.loads(Path(out).read_text())
        if os.environ.get("TAB_DRAG_DUMP"):   # a path: the whole measurement, for reading the cases side by side
            Path(os.environ["TAB_DRAG_DUMP"]).write_text(json.dumps(result, indent=1) + "\n")
        return result

    def _case(self, prefix):
        r = self._run()
        c = next((c for c in r["cases"] if c["label"].startswith(prefix)), None)
        self.assertIsNotNone(c, "no such case %r among %r" % (prefix, [c["label"] for c in r["cases"]]))
        if c.get("skipped"):
            self.skipTest(c["skipped"])
        return c

    @staticmethod
    def _table(c):
        return ("\n  theme %(theme)s (column gap %(gap)s)\n  from %(from)s\n  released over %(over)s at strip x=%(rx)s y=%(ry)s"
                "\n  rows before %(pre)s\n  rows after  %(post)s\n  landed rect %(landed)s\n  order after %(order)s"
                "\n  stored (romp:vieworder) %(stored)s\n  drag events %(ev)s\n  the gesture's tail %(tail)s\n  #tabs children %(items)s"
                % dict(c, rx=c["release"]["x"], ry=c["release"]["y"], pre=c["preRows"], post=c["postRows"], order=c["postOrder"]))

    def _assert_landed_under_cursor(self, c):
        table = self._table(c)
        self.assertTrue(c["from"]["draggable"], "the tab must be draggable (a page without its federation manager offers no drag)" + table)
        self.assertGreater(c["ev"]["dragstart"], 0, "no dragstart reached the strip: the mouse gesture began no drag" + table)
        self.assertGreater(c["ev"]["drop"], 0, "%s: no drop reached the strip; the browser refused the release and the drag was cancelled" % c["label"] + table)
        self.assertEqual(c["actual"], c["expected"],
                         "%s: the tab landed at index %d, the cursor was over slot %d" % (c["label"], c["actual"], c["expected"]) + table)
        # the persisted arrangement (the global order) carries the order the strip shows within every row: the strip
        # sections it by tag, so the rows are compared one by one
        self.assertIsInstance(c["stored"], list, "the drop persisted an arrangement" + table)
        for row in c["postRows"]:
            names = [t.split("@")[0] for t in row]
            self.assertEqual([n for n in c["stored"] if n in names], names, "the stored order and the strip's row disagree" + table)

    # ── the layouts ──
    def test_both_themes_show_the_group_row_above_the_trail_row(self):
        r = self._run()
        for key, theme, gap in (("grouped", "classic", "0px"), ("yatharth", "yatharth", "3px")):
            g = r[key]
            self.assertEqual(g["theme"], theme, "%s: the body wears the theme the settings store names: %r" % (key, g))
            self.assertEqual(g["gap"], gap, "%s: the strip's column gap under that theme: %r" % (key, g["bar"]))
            rows = g["rows"]
            self.assertGreaterEqual(len(rows), 2, "%s: the group and the trail wrap onto separate rows: %r" % (key, g))
            self.assertEqual(sorted(rows[0]), sorted(TAGGED), "%s: row 0 is the infra group: %r" % (key, rows))
            self.assertEqual(sorted(rows[-1]), sorted(n for n in NAMES if n not in TAGGED), "%s: the last row is the trail: %r" % (key, rows))

    # ── the classic theme: the control ──
    def test_classic_trail_row_drag_to_the_first_slot(self):
        self._assert_landed_under_cursor(self._case("classic: trail, 3rd tab to the FIRST"))

    def test_classic_trail_row_drag_1st_to_3rd(self):
        self._assert_landed_under_cursor(self._case("classic: trail, 1st tab to the 3rd"))

    def test_classic_trail_row_drag_4th_to_2nd(self):
        self._assert_landed_under_cursor(self._case("classic: trail, 4th tab to the 2nd"))

    def test_classic_group_row_drag_1st_to_3rd(self):
        self._assert_landed_under_cursor(self._case("classic: group row, 1st tab to the 3rd"))

    # ── the yatharth theme (the user's): the trail's row lands wrong, its first slot and the group's row still right ──
    def test_yatharth_trail_row_drag_to_the_first_slot(self):
        self._assert_landed_under_cursor(self._case("yatharth: trail, 3rd tab to the FIRST"))

    def test_yatharth_trail_row_drag_1st_to_3rd(self):
        self._assert_landed_under_cursor(self._case("yatharth: trail, 1st tab to the 3rd"))

    def test_yatharth_trail_row_drag_4th_to_2nd(self):
        self._assert_landed_under_cursor(self._case("yatharth: trail, 4th tab to the 2nd"))

    def test_yatharth_trail_row_drag_2nd_to_5th(self):
        self._assert_landed_under_cursor(self._case("yatharth: trail, 2nd tab to the 5th"))

    def test_yatharth_group_row_drag_1st_to_3rd(self):
        self._assert_landed_under_cursor(self._case("yatharth: group row, 1st tab to the 3rd"))

    # ── a kernel push mid-drag: the strip's hold must outlive the browser's pointercancel at dragstart ──
    def test_a_drag_survives_a_kernel_push_that_lands_mid_drag(self):
        c = self._case("push mid-drag: trail, 2nd tab to the 6th")
        table = self._table(c)
        self.assertTrue(c["rename"].get("ok"), "the kernel accepted the headless rename: %r" % c["rename"])
        self.assertTrue(c["pushed"], "the frame carrying the new name reached the page while the drag was in flight" + table)
        self.assertFalse(c["rebuiltMidDrag"], "the strip was rebuilt under the drag: the hold did not outlive the browser's pointercancel" + table)
        self._assert_landed_under_cursor(c)
        self.assertTrue(c["shownAfter"], "the push's render, held through the drag, lands once the gesture is over: the new name shows" + table)


if __name__ == "__main__":
    unittest.main()
