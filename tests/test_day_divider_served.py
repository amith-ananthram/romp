#!/usr/bin/env python3
"""T339 (the user 2026-09-11, screenshot): the chat's day divider. Three faults on the real /chat page of a hermetic kernel:
(1) the rail's vertical line broke above and below the divider; (2) the date sat at the prose edge with the hairline to its
right alone; (3) a collapsed run of notices whose FIRST member carried yesterday's time among today's rows drew a
"Yesterday" divider inside today and wore yesterday's clock. The producer of (3), history now: a session's LIVE echo
atoms (the kernel's own copies of sent messages, kept until the transcript lands their text; persisted in the registry's
`echoes` mirror and reseeded at boot) were merged into the chat's LAST turn sorted by their own SEND time (kernel.py, the
live-atom merge), so a romp notice sent yesterday whose text never landed sat right after the previous turn's rows, among
today's, stamped yesterday, and two such echoes folded into one notice run timed by its first. T339 closed the walk's side
(a forward-only crossing against a high-water mark, time-marker.ts DayWalk); T344 closed the producer (2026-09-11): an
echo stamped before the last turn's start is placed by its send time, into the turn whose window holds it or into a
closed turn of its own in the gap where it was sent (one per gap, so echoes sent together stay a run), and only an echo at
or after the last turn's start still takes the tail. So the rows the chat reads are in time order and the walk needs no
special case; this lab reads that order off the served page.

Two synthetic sessions on one kernel. `web`: rows two days ago, yesterday and today (two turns), the registry mirroring two
echoes of romp notices, the first sent yesterday 09:47 (the gap between the two-days-ago turn and yesterday's), the second
today 00:12 (the gap between today's two turns). `api`: the same shape read a day later, rows all yesterday and both echoes
two days ago, before the first turn: one synthetic turn LEADING the transcript. Asserted, dark and light: every timed row
is in time order and each echo row wears its OWN local HH:MM; `web` shows two dividers only (the weekday over two days
ago, "Yesterday" whose next row is the 09:47 echo, now yesterday's first row, not the 10:00 row) and no collapsed run
at all (the two echoes are a day apart, no longer adjacent); `api` shows the weekday divider leading the transcript over
the notice run (whose head anchors on its latest member, 09:41, its previous sibling the divider), then exactly ONE
"Yesterday" over the first real row; each divider's label is centered in the column between two hairlines of equal width;
the divider's rail segment is painted on the turns' own line (the same page x) and reaches the neighbouring turns' boxes
above and below (painted geometry, not the rule text); a divider that leads the transcript (nothing on the rail above it:
the first child, or the one after the pinned system-context card, which sits off the rail) draws no segment and the turn
after it starts its rail where a first turn does. The top-of-view day-context label (render.ts paintRailSticky) reads the
day WALK's mark at the top row (its marker's data-day, stampWalkDay), not the row's own moment (T342: it used to read the
row's own epoch, so a stale echo among yesterday's rows said "2 days ago" under a "Yesterday" divider). Under the
placement `api`'s stale run LEADS the transcript, so the walk's mark at its head is the run's own anchor (nothing passed
before it): scrolled to the top line in a short viewport, the label reads the run's own day, "2 days ago", the day its
weekday divider opens, while the run's head keeps its own HH:MM (before T344's placement, with the echoes merged into the last turn, the run sat inside yesterday's turn,
the same label read "Yesterday", the walk's day there). The browser's clock is pinned to the epoch the fixture was stamped
from, so a run that crosses local midnight between the boot and the drive still agrees with itself.

With DD_SHOTS=<dir> the driver writes screenshots (dark and light, the `web` session); DD_BEFORE_DIST=<dist> serves another
tree's bundle for the before shots and skips the assertions, unless DD_BEFORE_ASSERT=1 keeps them (how T339 was proven
red against the bundle before its change: a third divider inside today above `web`'s notice run; and, against the branch
before the high-water mark, a second "Yesterday" in `api`). The T344 half is red against a KERNEL before the change, the
bundle as is: the echoes then sat in the last turn, so `web`'s rows stepped back in time and its "Yesterday" opened the
10:00 row (not the 09:47 echo), and `api` had no weekday divider leading the transcript (its stale run sat among
yesterday's rows, with nothing opening its day). Skips LOUDLY without the extension deps or a Playwright browser; the
extension CI job installs Chromium and runs served files with ROMP_SERVED_TESTS_REQUIRE=1, which turns any skip into a
failure there. SYNTHETIC fixtures only (sessions web and api, invented notice texts, the notes-api demo world)."""
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
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"   # web: rows across three days, an echo in two of the gaps
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"   # api: the same read a day later, both echoes before the first turn


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _local_day(days_ago, hour, minute, now):
    """An epoch on the LOCAL day `days_ago` days before `now` at hour:minute (the chat's day keys are the browser's local
    time, the same machine's as this runner's; the browser's clock is pinned to `now` too)."""
    lt = time.localtime(now)
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
    return int(base) - days_ago * 86400


NOTICE_MARKERS = "\n\n<!-- romp-injected --><!-- romp-system --><!-- romp-tag: watch -->"
NOTICE_1 = "[romp] The condition you asked romp to watch now HOLDS: the search suite's log has its verdict.<!-- romp-gist: the condition it was watching now holds -->" + NOTICE_MARKERS
NOTICE_2 = "[romp] The condition you asked romp to watch now HOLDS: the second log has its verdict.<!-- romp-gist: the condition it was watching now holds -->" + NOTICE_MARKERS


def _echoes(now, shift):
    """The registry's echo mirror: two romp notices the kernel sent and whose text never landed. Reseeded at boot as live
    atoms, each is older than the last turn's start and is placed by its send time (T344): a closed turn of its own in the
    gap where it was sent, one turn per gap. `shift` 0 (web): the first sent YESTERDAY 09:47 (the gap before yesterday's
    turn), the second today 00:12 (the gap between today's two turns). `shift` 1 (api): both two days ago, before the
    first turn, so they share one turn that leads the transcript."""
    if shift == 0:
        return [{"uuid": "echo:" + "a" * 32, "t": _local_day(1, 9, 47, now), "text": NOTICE_1, "author": "romp"},
                {"uuid": "echo:" + "b" * 32, "t": _local_day(0, 0, 12, now), "text": NOTICE_2, "author": "romp"}]
    return [{"uuid": "echo:" + "c" * 32, "t": _local_day(2, 9, 40, now), "text": NOTICE_1, "author": "romp"},
            {"uuid": "echo:" + "d" * 32, "t": _local_day(2, 9, 41, now), "text": NOTICE_2, "author": "romp"}]


def _records(sid, now, shift):
    """The transcript. `shift` 0 (web): a pair two days ago, a pair yesterday (a real day boundary), then today: two turns,
    the 00:12 echo in the gap between them, the 09:47 echo in the gap before yesterday's pair. `shift` 1 (api): two turns,
    all yesterday (the transcript read a day later), both echoes before the first."""
    def user(uuid, parent, t, text):
        return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed", "sessionId": sid,
                "message": {"role": "user", "content": text}}
    def asst(uuid, parent, t, text):
        return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": sid,
                "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}
    if shift == 0:
        d2, d1 = _local_day(2, 10, 0, now), _local_day(1, 10, 0, now)
        # today's rows early in the local day, so the fixture's "today" holds from 00:00 on
        t0 = _local_day(0, 0, 10, now)
        return [
            user("u1", None, d2, "how should the notes-api retry loop back off?"),
            asst("a1", "u1", d2 + 60, "Use exponential backoff with a jitter of ten percent."),
            user("u2", "a1", d1, "and the cap on retries?"),
            asst("a2", "u2", d1 + 60, "Five attempts, then surface the failure."),
            user("u3", "a2", t0, "please run the notes-api search suite"),
            asst("a3", "u3", t0 + 60, "Running the search suite now."),
            # the second turn of today; the 00:12 echo sits in the gap before its trigger (the 09:47 one before u2)
            user("u4", "a3", t0 + 180, "and the docs suite after it"),
            asst("a4", "u4", t0 + 240, "Both suites are green; the search module is done."),
        ]
    y0 = _local_day(1, 9, 5, now)
    return [
        user("u1", None, y0, "please run the notes-api search suite"),
        asst("a1", "u1", y0 + 60, "Running the search suite now."),
        # the second turn of yesterday; both echoes (two days ago) precede u1, one run leading the transcript
        user("u2", "a1", y0 + 300, "and the docs suite after it"),
        asst("a2", "u2", y0 + 360, "Both suites are green; the search module is done."),
    ]


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
await page.clock.setFixedTime(cfg.nowMs);   // the page's Date.now() is the epoch the fixture was stamped from; timers keep running
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
const show = async (sid, turns) => {
  await page.click('#tabs .tab[data-id="' + sid + '"]');
  await page.waitForFunction((n) => Array.from(document.querySelectorAll("#content .turn")).filter((t) => t.offsetParent !== null).length >= n, turns, { timeout: 30000 });
  await page.mouse.move(700, 600); await page.waitForTimeout(500);
};
const measure = () => page.evaluate(() => {
  const turn0 = Array.from(document.querySelectorAll("#content .turn")).find((t) => t.offsetParent !== null);
  const thread = turn0.parentElement;
  const kids = Array.from(thread.children);
  const rows = kids.map((n) => {
    const cs = getComputedStyle(n);
    const isDiv = n.classList.contains("day-divider");
    const marker = n.querySelector(":scope > .time-marker");
    const r = n.getBoundingClientRect();
    return { cls: n.className, unit: n.dataset.unit ?? null, t: n.dataset.t ?? null, marker: marker ? marker.textContent : null,
             markerEpoch: marker ? marker.dataset.epoch : null, label: isDiv ? n.querySelector(".day-divider-label").textContent : null,
             top: r.top, bottom: r.bottom, height: r.height, marginTop: cs.marginTop, marginBottom: cs.marginBottom };
  });
  const railX = (n) => { const b = getComputedStyle(n, "::before"); return n.getBoundingClientRect().left + parseFloat(b.left); };
  const divs = kids.filter((n) => n.classList.contains("day-divider")).map((n) => {
    const r = n.getBoundingClientRect(); const cs = getComputedStyle(n);
    const lbl = n.querySelector(".day-divider-label").getBoundingClientRect();
    const rules = Array.from(n.querySelectorAll(".day-divider-rule")).map((x) => { const b = x.getBoundingClientRect(); return { left: b.left, width: b.width, height: b.height }; });
    const before = getComputedStyle(n, "::before");
    const contentLeft = r.left + parseFloat(cs.paddingLeft);
    const prev = n.previousElementSibling, next = n.nextElementSibling;
    const box = (m) => m ? { cls: m.className, top: m.getBoundingClientRect().top, bottom: m.getBoundingClientRect().bottom, railX: m.classList.contains("turn") ? railX(m) : null,
                             railTop: m.classList.contains("turn") ? getComputedStyle(m, "::before").top : null,
                             t: m.dataset.t ?? null, markerEpoch: (m.querySelector(":scope > .time-marker") || {}).dataset?.epoch ?? null } : null;
    // leads: nothing rail-bearing above it (the system-context card sits off the rail with its line suppressed)
    return { label: n.querySelector(".day-divider-label").textContent, leads: kids.slice(0, kids.indexOf(n)).every((m) => m.classList.contains("turn-system")),
             labelCenter: (lbl.left + lbl.right) / 2, columnCenter: (contentLeft + r.right) / 2, top: r.top, bottom: r.bottom, rules,
             rail: { display: before.display, width: before.width, left: before.left, top: before.top, bottom: before.bottom, bg: before.backgroundColor, opacity: before.opacity, position: before.position,
                     x: railX(n), segTop: r.top + parseFloat(before.top), segBottom: r.bottom - parseFloat(before.bottom) },
             prev: box(prev), next: box(next) };
  });
  const tb = getComputedStyle(turn0, "::before");
  const head = Array.from(document.querySelectorAll("#content .turn-noticegroup")).find((t) => t.offsetParent !== null);
  return { rows, divs, turnRail: { width: tb.width, left: tb.left, bg: tb.backgroundColor, opacity: tb.opacity },
           head: head ? { marker: (head.querySelector(":scope > .time-marker") || {}).textContent ?? null, t: head.dataset.t ?? null,
                          prevIsDivider: !!(head.previousElementSibling && head.previousElementSibling.classList.contains("day-divider")),
                          gist: head.textContent.trim().slice(0, 80) } : null,
           theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
});
const out = { web: {}, api: {} };
await show(cfg.web, 8);
out.web.dark = await measure();
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/romp_chat-day-divider-dark" + (cfg.shotSuffix || "") + ".png", fullPage: false }); }
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
out.web.light = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "/romp_chat-day-divider-light" + (cfg.shotSuffix || "") + ".png", fullPage: false });
await show(cfg.api, 4);
out.api.light = await measure();
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(300);
out.api.dark = await measure();
// the stale run's head at the TOP LINE of a short viewport: the sticky day label names the walk's day there (T342)
await page.setViewportSize({ width: 1100, height: 330 });   // a short pane: the head must be able to reach the top line
await page.evaluate(() => {
  const h = Array.from(document.querySelectorAll("#content .turn-noticegroup")).find((t) => t.offsetParent !== null);
  const c = document.getElementById("content");
  c.style.paddingBottom = c.clientHeight + "px";   // room below the last row, so the scroll cannot clamp before the head reaches the top whatever sits under the run (the sticky reads tops only)
  h.scrollIntoView({ block: "start" });
  c.scrollTop += h.getBoundingClientRect().top - c.getBoundingClientRect().top;   // the head's top exactly at the pane's top, past the pane's own padding
});
await page.waitForTimeout(500);
out.api.sticky = await page.evaluate(() => {
  const h = Array.from(document.querySelectorAll("#content .turn-noticegroup")).find((t) => t.offsetParent !== null);
  const content = document.getElementById("content").getBoundingClientRect();
  const day = document.querySelector(".rail-day"), sticky = document.querySelector(".rail-sticky");
  const m = h.querySelector(":scope > .time-marker");
  const vis = (n) => !!n && getComputedStyle(n).display !== "none";
  const c = document.getElementById("content");
  return { headTop: h.getBoundingClientRect().top - content.top, headTracked: h.getBoundingClientRect().top <= content.top + 6, pane: [c.scrollTop, c.scrollHeight, c.clientHeight],
           headMarker: m ? m.textContent : null, headMarkerVisible: !!m && getComputedStyle(m).visibility !== "hidden",
           headDay: m ? m.dataset.day : null, headEpoch: m ? m.dataset.epoch : null,
           dayLabel: vis(day) ? day.textContent : null, stickyHm: vis(sticky) ? sticky.textContent : null };
});
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedDayDivider(unittest.TestCase):
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them; CI's extension job has them and requires this file to run")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        cls.lab = tempfile.mkdtemp(prefix="day-divider-")
        cls.before = os.environ.get("DD_BEFORE_DIST", "")
        if not cls.before:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(cls.before or os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        cls.now = time.time()   # ONE epoch for the fixture and the browser's clock: the run cannot straddle midnight against itself
        for sid, name, shift, colour in ((SID_A, "web", 0, ("#9cd2ff", "#0c1a2e")), (SID_B, "api", 1, ("#ffd29c", "#2e1a0c"))):
            cwd = os.path.join(cls.lab, "proj-" + name)
            os.makedirs(cwd, exist_ok=True)
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, colour[0], colour[1]))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5", "echoes": _echoes(cls.now, shift)}))
            proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
            os.makedirs(proj, exist_ok=True)
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in _records(sid, cls.now, shift)))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-daydivider"
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

    def _divider_shape(self, d, m, theme):
        """(2) the date centered over the column, a hairline either side; (1) the rail runs through: the segment is the
        turn's own line at the same page x, and reaches the neighbouring turns' boxes (painted geometry, not the rule)."""
        self.assertEqual(len(d["rules"]), 2, "two hairlines: %r" % d)
        self.assertTrue(all(x["width"] > 40 and abs(x["height"] - 1) < 0.6 for x in d["rules"]), "both rules have room and are hairlines: %r" % d["rules"])
        self.assertLess(abs(d["rules"][0]["width"] - d["rules"][1]["width"]), 2, "the two rules share the room equally: %r" % d["rules"])
        self.assertLess(abs(d["labelCenter"] - d["columnCenter"]), 2, "the label sits in the middle of the column: %r" % d)
        nxt = d["next"]
        self.assertIsNotNone(nxt, "a divider precedes the turn it opens: %r" % d)
        self.assertTrue(nxt["cls"].startswith("turn"), "…a turn: %r" % nxt)
        if d["leads"]:
            # a LEADING divider: no segment above the date; the turn it opens starts its rail at its first dot, as a first turn does
            self.assertEqual(d["rail"]["display"], "none", "a leading divider draws no segment in %s: %r" % (theme, d["rail"]))
            self.assertEqual(nxt["railTop"], "16px", "…and its turn starts its rail where a first turn does: %r" % nxt)
            return
        self.assertEqual((d["rail"]["position"], d["rail"]["width"]), ("absolute", m["turnRail"]["width"]), "a 2px segment: %r" % d["rail"])
        self.assertEqual((d["rail"]["bg"], d["rail"]["opacity"]), (m["turnRail"]["bg"], m["turnRail"]["opacity"]), "the turn's colour and opacity: %r vs %r" % (d["rail"], m["turnRail"]))
        self.assertLess(abs(d["rail"]["x"] - nxt["railX"]), 0.5, "painted on the turns' own line (the same page x): %r vs %r" % (d["rail"], nxt))
        prv = d["prev"]
        self.assertIsNotNone(prv, "a turn above: %r" % d)
        self.assertLessEqual(d["rail"]["segTop"], prv["bottom"] + 0.5, "the segment reaches the turn above: %r / %r" % (d["rail"], prv))
        self.assertGreaterEqual(d["rail"]["segBottom"], nxt["top"] - 0.5, "…and the turn below: %r / %r" % (d["rail"], nxt))

    def test_the_divider_opens_each_past_day_once_centered_with_the_rail_through_it_and_never_on_a_stale_row(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "web": SID_A, "api": SID_B,
                       "nowMs": int(self.now * 1000),
                       "shots": os.environ.get("DD_SHOTS", ""), "shotSuffix": "-before" if self.before else ""}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        if self.before and not os.environ.get("DD_BEFORE_ASSERT"):
            self.skipTest("a before-the-change dist: screenshots only, the assertions describe the change")
        now = self.now
        two_days_ago = time.strftime("%a", time.localtime(_local_day(2, 10, 0, now)))   # the marker's weekday label for a day within the week

        def hm(t):
            return time.strftime("%H:%M", time.localtime(t))

        def timed(m):
            return [int(row["t"]) for row in m["rows"] if row["t"] is not None]

        def echo_row(m, t):
            hits = [row for row in m["rows"] if row["t"] == str(t)]
            self.assertEqual(len(hits), 1, "one row at the echo's send time %s: %r" % (hm(t), [(row["cls"], row["t"]) for row in m["rows"]]))
            return hits[0]
        # web: two stale echoes, each in a gap of its own (T344): the rows are in time order and each echo wears its own clock
        yesterday_echo_t, today_echo_t = (e["t"] for e in _echoes(now, 0))
        for theme in ("dark", "light"):
            m = r["web"][theme]
            self.assertEqual(m["theme"], theme)
            ts = timed(m)
            self.assertEqual(ts, sorted(ts), "the rows the chat reads are in time order (a stale echo sits at its send time, never among later rows) in %s: %r" % (theme, ts))
            for t in (yesterday_echo_t, today_echo_t):
                row = echo_row(m, t)
                self.assertIn("turn-notice", row["cls"], "the echo is a notice row of its own: %r" % row)
                self.assertNotIn("turn-noticegroup", row["cls"], "…not a run (the two echoes are a day apart, no longer adjacent): %r" % row)
                self.assertEqual((row["marker"], row["markerEpoch"]), (hm(t), str(t)), "an echo row wears its OWN time: %r" % row)
            self.assertIsNone(m["head"], "no collapsed notice run in web (the echoes sit in different gaps): %r" % [row["cls"] for row in m["rows"]])
            # (3) two dividers only: the first row (two days ago) opens its day; "Yesterday" opens on the 09:47 echo, now
            # yesterday's first row (it sits in the gap before the 10:00 row); none inside today
            self.assertEqual([d["label"] for d in m["divs"]], [two_days_ago, "Yesterday"], "two dividers, the transcript's two past days, in %s: %r" % (theme, [d["label"] for d in m["divs"]]))
            self.assertTrue(m["divs"][0]["leads"], "the transcript leads with its first day's divider (after the system-context card): %r" % m["divs"][0])
            d = m["divs"][1]
            self.assertEqual((d["next"]["t"], d["next"]["markerEpoch"]), (str(yesterday_echo_t), str(yesterday_echo_t)),
                             "\"Yesterday\" opens on the 09:47 echo, yesterday's first row under the placement, not the 10:00 row: %r" % d["next"])
            self.assertIn("turn-notice", d["next"]["cls"], "…the echo's own notice row: %r" % d["next"])
            for d in m["divs"]:
                self._divider_shape(d, m, theme)
        # api: both echoes precede the first turn and share one synthetic turn that LEADS the transcript: the weekday
        # divider opens on the notice run, then exactly ONE "Yesterday" over the first real row
        b_first_t, b_later_t = _local_day(1, 9, 5, now), _echoes(now, 1)[1]["t"]
        for theme in ("dark", "light"):
            m = r["api"][theme]
            self.assertEqual(m["theme"], theme)
            ts = timed(m)
            self.assertEqual(ts, sorted(ts), "the rows are in time order (the stale run leads, it is not among yesterday's rows) in %s: %r" % (theme, ts))
            self.assertEqual([d["label"] for d in m["divs"]], [two_days_ago, "Yesterday"], "the weekday divider leads (the run's day, two days ago), then one \"Yesterday\", in %s: %r" % (theme, [d["label"] for d in m["divs"]]))
            self.assertIsNotNone(m["head"], "the two stale echoes, sent together, collapsed into one run: %r" % [row["cls"] for row in m["rows"]])
            self.assertTrue(m["head"]["prevIsDivider"], "the weekday divider sits right above the run: %r" % m["head"])
            self.assertEqual((m["head"]["t"], m["head"]["marker"]), (str(b_later_t), hm(b_later_t)), "the run's head anchors on its latest member and wears its own time, two days ago: %r" % m["head"])
            lead, yday = m["divs"]
            self.assertTrue(lead["leads"], "the weekday divider leads the transcript (after the system-context card): %r" % lead)
            self.assertIn("turn-noticegroup", lead["next"]["cls"], "…and opens on the notice run: %r" % lead["next"])
            self.assertEqual(lead["next"]["t"], str(b_later_t), "…anchored on its latest member: %r" % lead["next"])
            self.assertFalse(yday["leads"], "\"Yesterday\" has the run on the rail above it: %r" % yday)
            self.assertIn("turn-noticegroup", yday["prev"]["cls"], "\"Yesterday\" follows the run: %r" % yday["prev"])
            self.assertEqual(yday["next"]["markerEpoch"], str(b_first_t), "…and opens yesterday's first real row: %r" % yday["next"])
            for d in m["divs"]:
                self._divider_shape(d, m, theme)
        # T342 under the placement: the stale run's head scrolled to the top line. The day-context label names the WALK's
        # day at that row (the head marker's data-day, the mark after the run; stampWalkDay), never the row's own moment
        # re-derived. The run LEADS the transcript here (T344), so nothing passed before it and the walk's mark at its head
        # IS the run's own anchor: the label reads the run's own day, "2 days ago", the day its weekday divider opens (before
        # T344's placement the run sat inside yesterday's turn and the same label read "Yesterday", the walk's day there).
        # The rail's HH:MM over it stays the run's own (its stale anchor's clock). Here the walk's mark and the head's own
        # epoch COINCIDE, so this case cannot tell T342's read (the marker's data-day) from the row's own epoch: that
        # discrimination is ui/webview/rail-day.test.ts's source pins and time-marker.test.ts's DayWalk cases.
        st = r["api"]["sticky"]
        self.assertLessEqual(st["headTop"], 0.5, "the head sits exactly at the pane's top once the scroll cannot clamp: %r" % st)
        self.assertTrue(st["headTracked"], "…within the sticky's own tracking threshold (its 6px buffer), so the head is the tracked turn: %r" % st)
        self.assertEqual(st["dayLabel"], "2 days ago", "the day label over the LEADING stale run is the walk's day at its head, and with no row before it that is the run's own anchor's day, two days ago (the day its divider opens), not yesterday: %r" % st)
        self.assertIn(hm(b_later_t), (st["headMarker"] if st["headMarkerVisible"] else None, st["stickyHm"]), "the HH:MM at the top is the run's own, stale as it is: %r" % st)
        self.assertEqual(st["headDay"], str(b_later_t), "the head's marker carries the walk's mark after the run: nothing precedes the run, so the mark is its own anchor: %r" % st)
        self.assertEqual(st["headEpoch"], str(b_later_t), "…beside its own moment: %r" % st)


if __name__ == "__main__":
    unittest.main()
