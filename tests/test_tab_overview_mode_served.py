#!/usr/bin/env python3
"""T322 (the user 2026-09-10): the tab strip grouped by tag, one tag per row, and a tag's overview, on the real /chat page
of a hermetic kernel with four sessions under two tags. Asserted: with a session active inside an expanded row, the
row's tag chip wears no underline; a click on the row shows the overview whose heading reads "Overview of", the tag's
ordinary chip and the count; while it shows the message box (the whole footer) is gone, no tab renders as selected
(the active tab's fill is transparent) and the row wears the selected tab's box (the tab's fill token, the identity
ring); selecting a tab clears it and brings the footer and the tab's fill back. T322b: a row's state words are the SHARED
status chip: the ui tag's session is idle awaiting three background agents (a Codex-backed session the Codex registry
lists, its wait the states overlay row), and its overview row wears `chip chip-awaitingBg` reading "Awaiting 3 agents" in the same
computed dress (fill, ink, weight,
spacing, radius, padding, line height, rendered height, the 0.7em rule) the bar under the transcript wore for it a moment earlier; no pill of the row's
own, the green pip beside it. With SNAP_SHOTS=<dir> the driver writes screenshots (the grouped strip with one row expanded
and a session active, dark and light; the overview shown, dark; the overview with the awaiting row, dark).
Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (the notes-api demo world)."""
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

SIDS = {"web": "aaaaaaaa-1111-2222-3333-444444444444", "api": "bbbbbbbb-1111-2222-3333-444444444444",
        "tests": "cccccccc-1111-2222-3333-444444444444", "docs": "dddddddd-1111-2222-3333-444444444444"}
COLORS = {"web": ("#9cd2ff", "#0c1a2e"), "api": ("#1EA1EB", "#ffffff"), "tests": ("#54B204", "#ffffff"), "docs": ("#c98cff", "#1a0c2e")}
TAGS = [{"id": "tag-infra", "name": "infra", "color": "#4EC9B0", "members": [SIDS["web"], SIDS["api"]]},
        {"id": "tag-ui", "name": "ui", "color": "#e5a50a", "members": [SIDS["tests"]]}]


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
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab-group-head", { timeout: 30000 });
await page.waitForTimeout(800);
const measure = () => page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll("#tabs .tab-group-head")).map((h) => {
    const chip = h.querySelector(".tab-group-chip"); const cs = getComputedStyle(h);
    return { group: h.dataset.group, folded: h.dataset.folded, holdsActive: h.classList.contains("holds-active"), shown: h.classList.contains("snap-shown"),
             chipUnderline: chip ? getComputedStyle(chip).textDecorationLine : null, bg: cs.backgroundColor, shadow: cs.boxShadow, color: cs.color,
             padding: cs.padding, border: cs.borderTopWidth + " " + cs.borderTopStyle + " " + cs.borderTopColor, chipBg: h.style.getPropertyValue("--chip-bg") };
  });
  const tabs = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => {
    const cs = getComputedStyle(t);
    return { id: t.dataset.id, active: t.classList.contains("active"), bg: cs.backgroundColor, shadow: cs.boxShadow, color: cs.color, name: (t.querySelector(".tab-label") || t).textContent.trim() };
  });
  const tab = document.querySelector("#tabs .tab.active"); const tcs = tab ? getComputedStyle(tab) : null;
  const probe = document.createElement("div"); probe.style.background = "var(--tab-active-bg)"; document.body.appendChild(probe);
  const activeFill = getComputedStyle(probe).backgroundColor; probe.remove();
  const footer = document.getElementById("footer"); const composer = document.getElementById("composer");
  const host = document.getElementById("tab-snapshot"); const head = host ? host.querySelector(".snap-head") : null;
  // a status chip's computed dress, and its parent's font size (the chip is 0.7em of whatever it sits in)
  const dress = (c) => { const cs = getComputedStyle(c); const pcs = getComputedStyle(c.parentElement);
    return { tag: c.tagName.toLowerCase(), cls: c.className, text: c.textContent.trim(), bg: cs.backgroundColor, color: cs.color, weight: cs.fontWeight,
             spacing: cs.letterSpacing, radius: cs.borderRadius, padding: cs.padding, size: cs.fontSize, parentSize: pcs.fontSize,
             lineHeight: cs.lineHeight, height: Math.round(c.getBoundingClientRect().height * 10) / 10 }; };
  const barChip = document.querySelector("#statusline .chip");
  const chips = host ? Array.from(host.querySelectorAll(".snap-row")).map((r) => { const c = r.querySelector(".chip"); const pip = r.querySelector(".snap-pip");
    return { id: r.dataset.id, pip: pip ? pip.className : null, flag: !!r.querySelector(".snap-flag"), chip: c ? dress(c) : null }; }) : [];
  const tip = document.querySelector(".tab-tip"); const tipShown = !!tip && getComputedStyle(tip).display !== "none" && tip.getBoundingClientRect().width > 0;   // T327: no tip stands after a pick
  return { body: document.body.className, rows, tabs, activeFill, bar: barChip ? dress(barChip) : null, chips, tipShown,
           footer: footer ? getComputedStyle(footer).display : null, composer: composer ? getComputedStyle(composer).display : null,
           composerVisible: composer ? composer.getBoundingClientRect().height > 0 : null,
           snap: host ? { display: getComputedStyle(host).display, heading: head ? Array.from(head.children).map((c) => ({ cls: c.className, text: c.textContent.trim(), style: c.getAttribute("style") || (c.firstElementChild && c.firstElementChild.getAttribute("style")) || "" })) : null,
                         rows: host.querySelectorAll(".snap-row").length, label: host.getAttribute("aria-label") } : null,
           theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
});
const out = {};
// the awaiting session read first: the bar under the transcript wears its Awaiting chip (the dress the row's must match)
await page.click('#tabs .tab[data-id="' + cfg.tests + '"]');   // the pick rebuilds the strip under the pointer; renderTabs hides the clicked tab's hover tip as it rebuilds (T327)
await page.mouse.move(700, 600);
await page.waitForTimeout(700);
out.barAwaiting = await measure();
// a session inside the infra row is active (the strip's default pick is the first tab; make it explicit)
await page.click('#tabs .tab[data-id="' + cfg.web + '"]');
await page.mouse.move(700, 600);   // off the strip: no tab tooltip in the shots
await page.waitForTimeout(500);
out.active = await measure();
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/romp_chat-tab-groups-active-dark.png", clip: { x: 0, y: 0, width: 1100, height: 260 } }); }
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(250);
out.activeLight = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "/romp_chat-tab-groups-active-light.png", clip: { x: 0, y: 0, width: 1100, height: 260 } });
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(250);
// the OTHER tag's row: the overview of ui, while web (in the still-expanded infra row) stays the active session — so the
// strip shows an active tab whose selected dress must be gone (a click on infra's own row would fold web away instead)
await page.click('#tabs .tab-group-head[data-group="ui"]');
await page.mouse.move(700, 600);
await page.waitForTimeout(600);
out.overview = await measure();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "/romp_chat-tab-overview-dark.png", fullPage: false });
if (cfg.shots) await page.screenshot({ path: cfg.shots + "/romp_chat-tab-overview-awaiting-dark.png", clip: { x: 0, y: 0, width: 1100, height: 360 } });   // T322b: the awaiting row's chip
// the cascade's two exceptions, probed in the mode: a hard-blocked active tab keeps its red fill (the class the tab
// state paints, added here since a hermetic kernel has no blocked session), and under the Yatharth theme the active
// tab wears that theme's resting wash with no selection border
const probe = (fn) => page.evaluate(fn, cfg.web);
await probe((id) => { document.querySelector('#tabs .tab[data-id="' + id + '"]').classList.add("tab-blocked"); });
await page.waitForTimeout(100);
out.blocked = await probe((id) => { const t = document.querySelector('#tabs .tab[data-id="' + id + '"]'); const cs = getComputedStyle(t); return { bg: cs.backgroundColor, active: t.classList.contains("active") }; });
await probe((id) => { document.querySelector('#tabs .tab[data-id="' + id + '"]').classList.remove("tab-blocked"); document.body.classList.add("chat-theme-yatharth"); });
await page.waitForTimeout(150);
out.yatharth = await probe((id) => { const t = document.querySelector('#tabs .tab[data-id="' + id + '"]'); const cs = getComputedStyle(t);
  const rest = Array.from(document.querySelectorAll('#tabs .tab.colored:not(.active)')).map((r) => getComputedStyle(r).backgroundColor);
  return { border: cs.borderTopColor, bg: cs.backgroundColor, restBgs: rest, snap: document.body.classList.contains("snap-mode") }; });
await probe(() => document.body.classList.remove("chat-theme-yatharth"));
await page.waitForTimeout(150);
// a tab pick clears the overview: the docs tab, in the untagged trail (the row's click folded infra's tabs away)
await page.click('#tabs .tab[data-id="' + cfg.docs + '"]');
await page.waitForTimeout(500);
out.picked = await measure();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedTabOverviewMode(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="tab-overview-")
        # SNAP_BEFORE_DIST=<dir>: a dist built from another tree (the screenshots' "before"); the test's assertions are
        # skipped for that run, since they describe the change
        before = os.environ.get("SNAP_BEFORE_DIST", "")
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
        for d in ("names", "sdk", "states", "codex"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        for i, (name, sid) in enumerate(SIDS.items()):
            bg, fg = COLORS[name]
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
            if name != "tests":   # tests is the Codex-backed session below: its registry row lists it, the SDK backend never does
                Path(state, "sdk", sid + ".json").write_text(json.dumps(
                    {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                     "model": "claude-opus-5", "liveModel": "Opus 5"}))
            recs = [{"type": "user", "timestamp": iso(t0 + i), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "what does the %s session do in notes-api?" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "It keeps the %s side of the notes-api tidy." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        # the tests session is idle awaiting background work it dispatched. Only a LIVE session can be awaiting (every
        # source of _session_awaiting is live evidence, and the kernel's audit lifts a durable stamp no live source
        # backs), and a lab has no running process, so tests is a Codex-backed session: the Codex backend reads its
        # registry at boot and lists every row not marked dead as a live, idle ("waiting") session with no process
        # behind it, and its wait is the states overlay row a Stop hook writes (source 1), carrying the kind and the
        # count the chips word. (An SDK registration would not do: the SDK backend heals an awaiting:true row of a
        # not-running session to false on its next look.)
        Path(state, "states", SIDS["tests"] + ".jsonl").write_text(json.dumps(
            {"t": t0 + 20, "awaiting": True, "why": "waiting on 3 background agents", "kind": "agents", "count": 3}) + "\n")
        Path(state, "codex", "registry.json").write_text(json.dumps({SIDS["tests"]: {
            "tid": "thread-" + SIDS["tests"][:8], "name": "tests", "cwd": cwd, "model": "gpt-5-codex", "effort": "high",
            "color": COLORS["tests"][0], "mode": "sandboxed"}}))
        # the tags: two, holding three of the four sessions; the fourth is the untagged trail
        Path(state, "timeline-views.json").write_text(json.dumps({"active": "all", "tags": TAGS}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-overview"
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

    def test_the_overview_is_a_mode_and_the_rows_are_not_tabs(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "web": SIDS["web"], "docs": SIDS["docs"], "tests": SIDS["tests"],
                       "shots": os.environ.get("SNAP_SHOTS", "")}, f)
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
        if os.environ.get("SNAP_BEFORE_DIST"):
            self.skipTest("a before-the-change dist: screenshots only, the assertions describe the change")
        a = r["active"]
        # T327: a tab pick rebuilds the strip under the pointer; the clicked tab's hover tip (shown by the pointer's arrival
        # for the click) is hidden by the rebuild itself, so no tip stands over the page after either pick
        self.assertFalse(r["barAwaiting"]["tipShown"], "no hover tip stands after the first pick (the strip rebuilt under the pointer)")
        self.assertFalse(a["tipShown"], "…nor after the second")
        infra = next(row for row in a["rows"] if row["group"] == "infra")
        self.assertTrue(infra["holdsActive"], "the web tab is active inside the infra row: %r" % a["rows"])
        # 1. no underline on the holding row's chip
        self.assertEqual(infra["chipUnderline"], "none", "the tag chip wears no underline for holding the active tab: %r" % infra)
        # 4. the row is not dressed as a tab: transparent, no ring; a tab's box of space
        self.assertEqual(infra["bg"], "rgba(0, 0, 0, 0)", "no wash on a row at rest: %r" % infra)
        self.assertEqual(infra["shadow"], "none")
        self.assertEqual(infra["padding"], "6px 7px", "a tab's padding: %r" % infra)
        self.assertIn("0px", infra["border"], "no border of its own: %r" % infra)
        self.assertEqual(infra["chipBg"].strip(), "#4EC9B0", "the tag's colour rides the row as --chip-bg for its selected box")
        active = next(t for t in a["tabs"] if t["active"])
        self.assertEqual(active["bg"], a["activeFill"], "the active tab wears the fill token: %r" % active)
        self.assertNotEqual(a["footer"], "none", "the message box shows while a session is read")
        self.assertTrue(a["composerVisible"])
        self.assertEqual(r["activeLight"]["theme"], "light")
        self.assertEqual(next(row for row in r["activeLight"]["rows"] if row["group"] == "infra")["chipUnderline"], "none", "light theme: no underline either")
        # 2, 3, 5, 6. the overview
        o = r["overview"]
        self.assertIn("snap-mode", o["body"].split(), "the mode class: %r" % o["body"])
        self.assertIsNotNone(o["snap"]); self.assertNotEqual(o["snap"]["display"], "none")
        heading = o["snap"]["heading"]
        self.assertEqual([h["cls"] for h in heading], ["snap-of", "snap-chip-slot", "snap-count"], heading)
        self.assertEqual(heading[0]["text"], "Overview of")
        self.assertEqual(heading[1]["text"], "ui", "the tag's chip carries the name: %r" % heading)
        self.assertIn("#e5a50a", heading[1]["style"], "…in the tag's colour, tagChip's pill: %r" % heading)
        self.assertEqual(heading[2]["text"], "1 session")
        self.assertEqual(o["snap"]["rows"], 1)
        self.assertEqual(o["snap"]["label"], "Overview of ui: 1 session; click one to open it")
        self.assertEqual(o["footer"], "none", "the message box disappears entirely: it is a mode, not a session")
        self.assertFalse(o["composerVisible"])
        shown = next(row for row in o["rows"] if row["group"] == "ui")
        self.assertTrue(shown["shown"])
        self.assertFalse(next(row for row in o["rows"] if row["group"] == "infra")["shown"], "one selection on the strip: the row whose overview shows")
        self.assertEqual(shown["bg"], o["activeFill"], "the row wears the selected tab's fill: %r" % shown)
        self.assertIn("inset", shown["shadow"], "…and the selected tab's inset identity ring: %r" % shown)
        for t in o["tabs"]:
            self.assertNotEqual(t["bg"], o["activeFill"], "no tab wears the selected fill while the overview shows: %r" % t)
            self.assertEqual(t["shadow"], "none", "no identity ring on any tab either: %r" % t)
        web = next(t for t in o["tabs"] if t["id"] == SIDS["web"])
        self.assertTrue(web["active"], "the active tab keeps its class (the way back), only its dress is neutralised: %r" % web)
        self.assertEqual(web["bg"], "rgba(0, 0, 0, 0)", "…transparent like a resting tab: %r" % web)
        # T322b: the row's state words are the SHARED status chip: the same class, words and computed dress the bar under the
        # transcript wore for the same session a moment earlier; no pill of the row's own; the green pip stays beside it
        bar = r["barAwaiting"]["bar"]
        self.assertIsNotNone(bar, "the bar wears a chip while the awaiting session is read: %r" % r["barAwaiting"])
        self.assertEqual((bar["cls"].split()[:2], bar["text"]), (["chip", "chip-awaitingBg"], "Awaiting 3 agents"), "the bar's chip: %r" % bar)
        trow = next(x for x in o["chips"] if x["id"] == SIDS["tests"])
        self.assertFalse(trow["flag"], "no pill of the row's own: %r" % trow)
        self.assertEqual(trow["pip"], "snap-pip waiting", "the green pip stays: %r" % trow)
        chip = trow["chip"]
        self.assertIsNotNone(chip, "the row wears the chip: %r" % trow)
        self.assertEqual((chip["tag"], chip["cls"], chip["text"]), ("span", "chip chip-awaitingBg", "Awaiting 3 agents"),
                         "the bar's class and words, as a span inside the row's button: %r" % chip)
        for k in ("bg", "color", "weight", "spacing", "radius", "padding", "lineHeight", "height"):   # height: a span chip beside a button chip
            self.assertEqual(chip[k], bar[k], "the chip's %s in the row equals the bar's: %r vs %r" % (k, chip, bar))
        self.assertEqual(chip["bg"], "rgb(84, 178, 4)", "await-green, the status token: %r" % chip)
        ratio = lambda d: round(float(d["size"].rstrip("px")) / float(d["parentSize"].rstrip("px")), 2)
        self.assertEqual((ratio(chip), ratio(bar)), (0.7, 0.7), "the chip's em rule holds in both: %r %r" % (chip, bar))
        # the two exceptions: a blocked active tab keeps the red fill; Yatharth's active tab wears the resting wash, no border
        b = r["blocked"]
        self.assertTrue(b["active"])
        self.assertTrue(b["bg"].startswith("rgba(229, 72, 77, 0."), "a hard-blocked active tab keeps its red fill in the mode (the active-blocked tier): %r" % b)
        y = r["yatharth"]
        self.assertTrue(y["snap"])
        self.assertEqual(y["border"], "rgba(0, 0, 0, 0)", "Yatharth: no 55%% selection border on the active tab in the mode: %r" % y)
        # the resting wash is each tab's own identity colour at the theme's resting alpha: the active tab's alpha matches its siblings'
        alpha = lambda c: c.rsplit("/", 1)[-1].strip(" )") if "/" in c else None
        self.assertEqual(alpha(y["bg"]), "0.09", "…and the theme's resting wash (its 9 percent tint, not the 22 percent selected one): %r" % y)
        self.assertTrue(y["restBgs"] and all(alpha(bg) == alpha(y["bg"]) for bg in y["restBgs"]), "…the same alpha as the other coloured tabs: %r" % y)
        # a tab pick clears the overview: the footer and the fill are back
        pk = r["picked"]
        self.assertNotIn("snap-mode", pk["body"].split())
        self.assertNotEqual(pk["footer"], "none")
        self.assertEqual(next(t for t in pk["tabs"] if t["active"])["id"], SIDS["docs"])
        self.assertEqual(next(t for t in pk["tabs"] if t["active"])["bg"], pk["activeFill"])
        self.assertFalse(any(row["shown"] for row in pk["rows"]))


if __name__ == "__main__":
    unittest.main()
