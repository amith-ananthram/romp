#!/usr/bin/env python3
"""T321 (the user 2026-09-10): tags render the same everywhere, and never in bold. One renderer (tagChip in
ui/webview/tag-menu.ts) builds every tag chip: the tab strip's group rows and its filter chips at the strip's right,
the feed's and the outline's filter chips, the tag-lens menu, the tab menu's Tags flyout, the feed's session dialog,
and the new-session picker's Tags row, where each tag shows as the chip (a thin border in the tag's own colour) and on
versus off by the visual the tag toggles already use: the faded chip (TAG_CHIP_OFF_CLASS at 0.45). No identity dot. The
picker's off chip and the tag-lens menu's off chip are read from the live page and must render identically (T321c, the
user reversing, the same day, a diagonal drawn through the picker's off tag: the same class, the same computed opacity,
no pseudo-element on either).

The strip guard reads the LIVE computed style of a group row's chip and of the same tag's filter chip at the strip's
right (a chat lens seeded with two tags, so both rows and both filter chips show) and asserts they are one rendering:
the same font size, weight 400, normal tracking, the same border width and radius, the same padding, and the tag's
colour on both border and text.

The served guard drives the real /chat page from a hermetic kernel with two tagged sessions and a third tag nobody
holds, opens the picker with the strip's +, and reads the Tags row: one option per tag, each holding one chip whose
border wears the tag's colour; the tags the ACTIVE tab holds are selected (the full chip), the rest unselected (the
faded chip); no dot anywhere. A click flips one option: the state class the create reads (`sel`) and the chip's off
class move together, and a second click puts them back. No backend pick greys the row (T331)
(the off chip's fade is not stacked with the row's).

Skips LOUDLY without the extension deps or a Playwright browser (CI installs none); the CI-safe pins ride
ui/webview/tag-chip-everywhere.test.ts, picker-tag-chips.test.ts and tab-groups.test.ts. All fixtures synthetic (the
notes-api demo world)."""
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
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

# name → (sid, tags): the active tab (web) holds one tag; a second session holds another; a third tag has no member
SESSIONS = [
    ("web", "aaaaaaaa-1111-2222-3333-000000000001", "web"),
    ("api", "aaaaaaaa-1111-2222-3333-000000000002", "infra"),
]
TAGS = [("t-web", "web", "#1EA1EB"), ("t-infra", "infra", "#54B204"), ("t-docs", "docs", "#B9770E")]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _rgb(hex6):
    h = hex6.lstrip("#")
    return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
// the web tab active: its tag is what the picker pre-selects
await page.click(`#tabs .tab[data-id="${cfg.activeSid}"]`);
await page.waitForFunction((id) => (document.querySelector("#tabs .tab.active") || {}).dataset?.id === id, cfg.activeSid, { timeout: 8000 });
await page.waitForSelector("#tabs .tab-tagchips > span", { timeout: 10000 });
await page.waitForTimeout(200);
const look = (e) => { const cs = getComputedStyle(e); return { text: (e.childNodes[0] && e.childNodes[0].textContent) || "", fontSize: cs.fontSize, fontWeight: cs.fontWeight, fontFamily: cs.fontFamily,
  letterSpacing: cs.letterSpacing, borderW: cs.borderTopWidth, borderColor: cs.borderTopColor, color: cs.color, radius: cs.borderTopLeftRadius,
  padding: cs.paddingTop + " " + cs.paddingLeft, bg: cs.backgroundColor, display: cs.display }; };
const strip = await page.evaluate((lookSrc) => {
  const look = eval("(" + lookSrc + ")");
  return { rows: Array.from(document.querySelectorAll("#tabs .tab-group-chip")).map(look),
           filters: Array.from(document.querySelectorAll("#tabs .tab-tagchips > span")).map(look),
           names: Array.from(document.querySelectorAll("#tabs .tab-label")).map((e) => getComputedStyle(e).fontWeight) };
}, look.toString());
// the tag-lens menu from the strip's filter button: its unselected rows wear the off chip the picker must match
await page.click('#tabs button[title="filter these tabs by tag"]');
await page.waitForSelector('[data-tag-menu] span[aria-pressed="false"]', { timeout: 10000 });
const lensMenu = await page.evaluate(() => {
  const read = (e) => { const cs = getComputedStyle(e); return { text: e.textContent, cls: e.getAttribute("class") || "", opacity: cs.opacity, after: getComputedStyle(e, "::after").content }; };
  return { off: Array.from(document.querySelectorAll('[data-tag-menu] span[aria-pressed="false"]')).map(read),
           on: Array.from(document.querySelectorAll('[data-tag-menu] span[aria-pressed="true"]')).map(read) };
});
// close it before the picker opens: Escape, else the strip's own filter button toggles it shut; the wait is not swallowed
await page.keyboard.press("Escape");
if (await page.$("[data-tag-menu]")) await page.click('#tabs button[title="filter these tabs by tag"]');
await page.waitForFunction(() => !document.querySelector("[data-tag-menu]"), null, { timeout: 5000 });
await page.click("#tabs .tab-add");
await page.waitForSelector("#picker .picker-tags .picker-be-opt", { timeout: 10000 });
await page.waitForTimeout(200);
const survey = () => page.evaluate(() => {
  const row = document.querySelector("#picker .picker-tags");
  const opts = Array.from(row.querySelectorAll(".picker-be-opt"));
  return {
    rowDisabled: row.classList.contains("disabled"),
    rowOpacity: getComputedStyle(row).opacity,
    dots: document.querySelectorAll("#picker .picker-tag-dot").length,
    opts: opts.map((b) => {
      const chip = b.firstElementChild;
      const cs = chip ? getComputedStyle(chip) : null;
      const bs = getComputedStyle(b);
      return { tag: b.dataset.tag, sel: b.classList.contains("sel"), disabled: b.disabled,
               children: b.children.length, chipTag: chip ? chip.tagName : null, chipClass: chip ? chip.getAttribute("class") || "" : null,
               chipText: chip ? chip.textContent : null,
               chipBorder: cs ? cs.borderTopColor : null, chipBorderW: cs ? cs.borderTopWidth : null, chipColor: cs ? cs.color : null,
               chipOpacity: cs ? cs.opacity : null, chipBg: cs ? cs.backgroundColor : null, chipFont: cs ? cs.fontFamily : null,
               chipAfter: chip ? getComputedStyle(chip, "::after").content : null,
               btnBg: bs.backgroundColor, btnBorder: bs.borderTopStyle, btnOpacity: bs.opacity, btnFilter: bs.filter, btnPad: bs.paddingLeft };
    }),
  };
});
const open = await survey();
// flip the unselected infra tag on, then off again
await page.click('#picker .picker-tags .picker-be-opt[data-tag="infra"]');
await page.waitForTimeout(100);
const on = await survey();
await page.click('#picker .picker-tags .picker-be-opt[data-tag="infra"]');
await page.waitForTimeout(100);
const off = await survey();
// the light theme: the chips keep the tag's colour on border and text (a theme rule must never recolour a tag)
await page.evaluate(() => document.body.classList.add("chat-theme-yatharth", "theme-light"));
await page.waitForTimeout(150);
const light = await survey();
await page.evaluate(() => document.body.classList.remove("chat-theme-yatharth", "theme-light"));
await page.waitForTimeout(100);
// T331: no backend pick disables the row any more (the terminal backend is no longer offered); the picker's toggles
const backends = await page.evaluate(() => Array.from(document.querySelectorAll('#picker .picker-be-opt:not([data-tag])')).map((b) => b.getAttribute('data-be')).filter(Boolean));
fs.writeSync(1, "RESULT:" + JSON.stringify({ strip, lensMenu, open, on, off, light, backends }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedPickerTagChips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps not installed here (npm ci in vscode-extension)")
        cls.lab = tempfile.mkdtemp(prefix="picker-tag-chips-")
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
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        for k, (name, sid, _tag) in enumerate(SESSIONS):
            Path(cls.state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(cls.state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high",
                 "lastSid": sid, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
            u = "11111111-2222-3333-4444-%012d" % k
            recs = [{"type": "user", "uuid": u, "parentUuid": None, "timestamp": "2026-09-10T10:%02d:00.000Z" % k, "sessionId": sid,
                     "message": {"role": "user", "content": "notes-api: check the %s service" % name}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        tags = [{"id": tid, "name": tname, "color": color, "members": [sid for (_n, sid, t) in SESSIONS if tname in (t or "").split()]}
                for (tid, tname, color) in TAGS]
        Path(cls.state, "timeline-views.json").write_text(json.dumps({"tags": tags, "tagOrder": [t[1] for t in TAGS],
                                                                     "actives": {"chat": {"tags": ["web", "infra"]}}}))
        cls.port = _free_port()
        cls.token = "testtok-pickertags"
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

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "activeSid": SESSIONS[0][1]}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box, and the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    _out = None

    def _once(self):
        if type(self)._out is None:
            type(self)._out = self._drive()
        return type(self)._out

    def test_the_strip_s_group_row_chip_and_its_filter_chip_are_one_rendering_and_never_bold(self):
        strip = self._once()["strip"]
        rows = {r["text"]: r for r in strip["rows"]}
        filters = {f["text"]: f for f in strip["filters"]}
        self.assertEqual(sorted(rows), ["infra", "web"], "two group rows: %r" % strip["rows"])
        self.assertEqual(sorted(filters), ["infra", "web"], "two filter chips at the strip's right: %r" % strip["filters"])
        colors = {name: color for (_i, name, color) in TAGS}
        for name in ("web", "infra"):
            r, f = rows[name], filters[name]
            for key in ("fontSize", "fontWeight", "fontFamily", "letterSpacing", "borderW", "radius", "padding", "bg", "display"):
                self.assertEqual(r[key], f[key], "%s: the row chip and the filter chip differ in %s: %r vs %r" % (name, key, r, f))
            self.assertEqual(r["fontWeight"], "400", "never bold: %r" % r)
            self.assertEqual(r["letterSpacing"], "normal", "the header's tracking does not reach the chip: %r" % r)
            self.assertEqual((r["borderColor"], r["color"]), (_rgb(colors[name]), _rgb(colors[name])), "the tag's colour on border and text: %r" % r)
            self.assertEqual((f["borderColor"], f["color"]), (_rgb(colors[name]), _rgb(colors[name])), "…on the filter chip too: %r" % f)
            self.assertEqual(r["borderW"], "1px")

    def test_each_tag_is_the_shared_chip_and_a_click_flips_the_faded_look_with_the_state_class(self):
        out = self._once()
        o = out["open"]
        by = {x["tag"]: x for x in o["opts"]}
        self.assertEqual(sorted(by), sorted(t[1] for t in TAGS), "one option per tag: %r" % o)
        self.assertEqual(o["dots"], 0, "no identity dot in the row")
        colors = {name: color for (_i, name, color) in TAGS}
        for name, x in by.items():
            self.assertEqual((x["children"], x["chipTag"], x["chipText"]), (1, "SPAN", name), "one chip per option, the tag's name as its text: %r" % x)
            self.assertEqual(x["chipBorder"], _rgb(colors[name]), "the chip's border is the tag's colour: %r" % x)
            self.assertEqual(x["chipColor"], _rgb(colors[name]), "…and so is its text")
            self.assertEqual(x["chipBorderW"], "1px", "a thin border")
            self.assertEqual(x["chipBg"], "rgba(0, 0, 0, 0)", "the chip stays transparent: no fill says selected")
            self.assertEqual(x["btnBg"], "rgba(0, 0, 0, 0)", "no button chrome behind the chip, selected or not: %r" % x)
            self.assertEqual(x["btnBorder"], "none", "no button border around the chip's own")
            self.assertEqual(x["btnPad"], "0px", "the chip's own padding is the whole footprint")
            self.assertEqual(x["chipFont"], out["strip"]["rows"][0]["fontFamily"], "the page's typeface, as the strip's chip: a bare button wears the browser's control face (review find): %r" % x)
        # the active tab's tag is selected: the full chip; the others unselected: the faded chip
        self.assertTrue(by["web"]["sel"]); self.assertFalse(by["infra"]["sel"]); self.assertFalse(by["docs"]["sel"])
        self.assertEqual((by["web"]["chipClass"], by["web"]["chipOpacity"], by["web"]["chipAfter"]), ("", "1", "none"), "selected = the full chip")
        for name in ("infra", "docs"):
            self.assertEqual((by[name]["chipClass"], by[name]["chipOpacity"], by[name]["chipAfter"]), ("tag-chip-off", "0.45", "none"),
                             "unselected = the off chip (the tag toggles' look), nothing drawn over it: %r" % by[name])
        # …identical to the tag-lens menu's off chip, read from the same page (T321c): the one off look
        menu = out["lensMenu"]
        self.assertTrue(menu["off"], "the seeded lens leaves a tag unselected in the menu: %r" % menu)
        for m in menu["off"]:
            self.assertEqual((m["cls"], m["opacity"], m["after"]), ("tag-chip-off", "0.45", "none"), "the menu's off chip: %r" % m)
            self.assertEqual((m["cls"], m["opacity"], m["after"]), (by["infra"]["chipClass"], by["infra"]["chipOpacity"], by["infra"]["chipAfter"]),
                             "the picker's off chip renders as the menu's: %r vs %r" % (m, by["infra"]))
        for m in menu["on"]:
            self.assertEqual((m["cls"], m["opacity"], m["after"]), ("", "1", "none"), "the menu's selected chip is the full chip, as the picker's: %r" % m)
        # the flip: the state class and the chip's look move together, and back
        on = {x["tag"]: x for x in out["on"]["opts"]}
        self.assertTrue(on["infra"]["sel"])
        self.assertEqual((on["infra"]["chipClass"], on["infra"]["chipOpacity"]), ("", "1"), "clicked on: the full chip")
        self.assertTrue(on["web"]["sel"], "multi-select: the other stays")
        light = {x["tag"]: x for x in out["light"]["opts"]}
        for name in ("web", "infra", "docs"):
            self.assertEqual((light[name]["chipBorder"], light[name]["chipColor"]), (_rgb(colors[name]), _rgb(colors[name])), "theme parity: %r" % light[name])
        self.assertEqual((light["infra"]["chipClass"], light["infra"]["chipOpacity"]), ("tag-chip-off", "0.45"), "the off look holds on the light theme")
        off = {x["tag"]: x for x in out["off"]["opts"]}
        self.assertFalse(off["infra"]["sel"])
        self.assertEqual((off["infra"]["chipClass"], off["infra"]["chipOpacity"]), ("tag-chip-off", "0.45"), "clicked again: the off chip")
        # T331: the picker offers Claude Code and Codex, both of whose creates take tags: no pick greys the row
        self.assertEqual(out["backends"], ["sdk", "codex"], "the terminal backend is no longer offered")
        self.assertFalse(out["on"]["rowDisabled"], "the row is never disabled")


if __name__ == "__main__":
    unittest.main()
