#!/usr/bin/env python3
"""T264 (the user 2026-09-08): in the tab strip's group-by-tag view every tag group starts on its own line —
the tag chip at the left edge, its tabs after it (wrapping onto further rows as they need), the next group
on a fresh line; the untagged trail opens its own line too, with no chip and no separator; a folded group
is its header row alone.

That layout is the gear's "One tag group per row in the tab strip" (`stripGroupRows`, on by default). The first
test drives the page as shipped (nothing written to the settings store) and checks T264's geometry; the second
writes the setting off before the page loads and checks the inline layout: no break element at all, each
group's header followed by its tabs with nothing between, and the untagged trail behind a visible 13px divider.

The served guard drives the real /chat page from a hermetic kernel: eight sessions under three tags of
mixed sizes (one tag wide enough to wrap at the viewport) plus one untagged, one group folded by a header
click. In the browser it reads the strip's geometry: every header is the first item of its row; the groups
occupy disjoint row sets in strip order; a group's tabs share its header's row or a row below (its wrapped
row starting at the left edge, never sharing a row with the next group); the folded header's row holds
nothing else; the trail's tabs start their own row; no visible separator remains. Screenshots dark + light
(the theme classes applyTheme sets) with NOTCH... no: TABROWS_SHOTS=<path-prefix>.

Skips LOUDLY without the extension deps or a Playwright browser (CI installs none); the CI-safe pins ride
ui/webview/tab-groups.test.ts and dragslot.test.ts. All fixtures synthetic (the notes-api demo world).
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

# name → (sid, tags) ; the web tag is wide enough to wrap at a 640px viewport; "archived" folds by default;
# web-search carries TWO tags and appears under both (T264b: tags are equivalent, no home tag)
SESSIONS = [
    ("web-frontend", "aaaaaaaa-1111-2222-3333-000000000001", "web"),
    ("web-backend", "aaaaaaaa-1111-2222-3333-000000000002", "web"),
    ("web-gateway", "aaaaaaaa-1111-2222-3333-000000000003", "web"),
    ("web-search", "aaaaaaaa-1111-2222-3333-000000000004", "web infra"),
    ("web-billing", "aaaaaaaa-1111-2222-3333-000000000005", "web"),
    ("infra-ci", "aaaaaaaa-1111-2222-3333-000000000006", "infra"),
    ("infra-deploy", "aaaaaaaa-1111-2222-3333-000000000007", "infra"),
    ("old-notes", "aaaaaaaa-1111-2222-3333-000000000008", "archived"),
    ("scratch", "aaaaaaaa-1111-2222-3333-000000000009", None),
]
TAGS = [("t-web", "web", "#1EA1EB"), ("t-infra", "infra", "#54B204"), ("t-archived", "archived", "#4EA8A9")]


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
const page = await browser.newPage({ viewport: { width: 640, height: 480 }, deviceScaleFactor: 2 });
// the inline drive turns the one-group-per-row setting off before any page script runs, so the first paint reads
// it; the default drive writes nothing and exercises the shipped default
if (cfg.rows === false) await page.addInitScript(() => { try { localStorage.setItem("romp:settings", JSON.stringify({ stripGroupRows: false })); } catch (e) {} });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
try { await page.waitForSelector("#tabs .tab-group-head", { timeout: 20000 }); }
catch (e) {
  const st = await page.evaluate(() => ({ tabs: document.querySelectorAll("#tabs .tab").length, html: document.getElementById("tabs")?.innerHTML.slice(0, 800) }));
  console.error("no section header rendered: " + JSON.stringify(st)); process.exit(1);
}
// wait for every VISIBLE session's tab (the archived group starts folded by default, so its member has none)
try { await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-placeholder)").length >= n
                                        && document.querySelectorAll("#tabs .tab-group-head").length === 3, cfg.visible, { timeout: 30000 }); }
catch (e) {
  const st = await page.evaluate(() => ({ tabs: document.querySelectorAll("#tabs .tab[data-id]").length, placeholders: document.querySelectorAll("#tabs .tab-placeholder").length,
    heads: Array.from(document.querySelectorAll("#tabs .tab-group-head")).map((h) => h.dataset.group) }));
  console.error("strip never settled: " + JSON.stringify(st)); process.exit(1);
}
await page.waitForTimeout(500);
const survey = () => page.evaluate(() => {
  const bar = document.getElementById("tabs");
  const kids = Array.from(bar.children);
  const item = (e) => {
    const r = e.getBoundingClientRect();
    return { cls: e.className, group: e.dataset.group || null, id: e.dataset.id || null,
             name: e.querySelector(".tab-label")?.textContent || e.querySelector(".tab-group-chip")?.textContent || null,
             top: Math.round(e.offsetTop), left: Math.round(e.offsetLeft), w: Math.round(r.width), h: Math.round(r.height) };
  };
  return {
    items: kids.map(item),
    active: Array.from(document.querySelectorAll("#tabs .tab.active[data-id]")).map((t) => ({ id: t.dataset.id, copy: t.dataset.copy })),
    dots: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => ({ id: t.dataset.id, copy: t.dataset.copy, dot: !!t.querySelector(".tab-dot"), close: t.querySelector(".tab-close")?.title || null })),
    barLeft: 0, barW: bar.clientWidth,
    theme: document.body.className,
    seps: Array.from(document.querySelectorAll("#tabs .tab-group-sep")).map((e) => ({ w: e.getBoundingClientRect().width, h: e.getBoundingClientRect().height })),
    breaks: document.querySelectorAll("#tabs .tab-group-break").length,
    lines: Array.from(document.querySelectorAll("#tabs .tab-row-line")).map((l) => parseFloat(l.style.top)),
    heads: Array.from(document.querySelectorAll("#tabs .tab-group-head")).map((h) => ({ group: h.dataset.group, act: h.dataset.act, folded: h.dataset.folded, count: h.querySelector(".tab-group-count")?.textContent,
      kids: Array.from(h.children).map((c) => Array.from(c.classList).find((k) => k.startsWith("tab-group-")) || c.className) })),   // T284: the child order
  };
});
const open = await survey();
// T264b: the two-tag session's copy under infra — click it, and the ONE session activates: both copies wear .active
await page.click(`#tabs .tab[data-id="${cfg.twoTag}"][data-copy="infra"]`);
await page.waitForFunction((id) => document.querySelectorAll(`#tabs .tab.active[data-id="${id}"]`).length === 2, cfg.twoTag, { timeout: 8000 });
await page.waitForTimeout(400);
const clicked = await survey();
await page.mouse.move(320, 400);   // off the strip, so no hover tip rides the screenshots
await page.waitForTimeout(200);
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-dark.png", clip: { x: 0, y: 0, width: 640, height: 130 } });
// LIGHT theme: the classes applyTheme sets for the light theme
await page.evaluate(() => document.body.classList.add("chat-theme-yatharth", "theme-light"));
await page.waitForTimeout(300);
const light = await survey();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-light.png", clip: { x: 0, y: 0, width: 640, height: 130 } });
fs.writeSync(1, "RESULT:" + JSON.stringify({ open, clicked, light }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedGroupsOnOwnLines(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="tabrows-")
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
            recs = [{"type": "user", "uuid": u, "parentUuid": None, "timestamp": "2026-09-07T10:%02d:00.000Z" % k, "sessionId": sid,
                     "message": {"role": "user", "content": "notes-api: check the %s service" % name}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        # the kernel's session-views blob: three tags, members by sid (the legacy spelling it reads losslessly)
        tags = [{"id": tid, "name": tname, "color": color, "members": [sid for (_n, sid, t) in SESSIONS if tname in (t or "").split()]}
                for (tid, tname, color) in TAGS]
        Path(cls.state, "timeline-views.json").write_text(json.dumps({"tags": tags, "tagOrder": [t[1] for t in TAGS]}))
        cls.port = _free_port()
        cls.token = "testtok-tabrows"
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

    def _drive(self, script, name, rows=True):
        """rows: True drives the shipped default (nothing written to the settings store); False writes the
        one-group-per-row setting off before the page loads."""
        cfg = os.path.join(self.lab, name + ".json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "rows": rows,
                       "visible": len([s for s in SESSIONS if s[2] != "archived"]) + 1,   # web-search has two copies
                       "twoTag": next(sid for (n, sid, _t) in SESSIONS if n == "web-search"),
                       "shots": os.environ.get("TABROWS_SHOTS", "")}, f)
        driver = os.path.join(self.lab, name + ".mjs")
        with open(driver, "w") as f:
            f.write(script)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    @staticmethod
    def _sections(items):
        """The strip in order, cut into sections: [(group or None, header item or None, [tab items])]."""
        out = []
        cur = None
        for it in items:
            cls = it["cls"].split()
            if "tab-group-head" in cls:
                cur = (it["group"], it, [])
                out.append(cur)
            elif "tab-group-sep" in cls:   # the trail's boundary: the break under the setting, the divider with it off
                cur = (None, None, [])
                out.append(cur)
            elif "tab" in cls and it["id"] and cur is not None:
                cur[2].append(it)
        return out

    def _check_rows(self, s, label):
        items = s["items"]
        # the row's left edge: the leftmost TAB or HEADER (the T134 hairlines bleed 8px past the strip; breaks have no box)
        strip = [it for it in items if it["w"] > 0 and it["h"] > 0 and ("tab-group-head" in it["cls"].split() or "tab" in it["cls"].split())]
        left = min(it["left"] for it in strip)
        secs = self._sections(items)
        self.assertEqual([g for g, _h, _t in secs], ["web", "infra", "archived", None], "%s: three groups in tag order, then the untagged trail: %r" % (label, secs))
        rows_of = lambda tabs: sorted({t["top"] for t in tabs})
        prev_max = -1
        for g, head, tabs in secs:
            if head is not None:
                self.assertEqual(head["left"], left, "%s: the %r chip sits at the row's left edge: %r" % (label, g, head))
                self.assertGreater(head["top"], prev_max, "%s: the %r header opens a row below every earlier group's rows: %r" % (label, g, head))
                first_row = head["top"]
            else:
                self.assertTrue(tabs, "%s: the untagged trail has its tab: %r" % (label, secs))
                self.assertGreater(tabs[0]["top"], prev_max, "%s: the trail opens its own row: %r" % (label, tabs[0]))
                self.assertEqual(tabs[0]["left"], left, "%s: the trail starts at the left edge, no separator ahead of it: %r" % (label, tabs[0]))
                first_row = tabs[0]["top"]
            rows = rows_of(tabs) or [first_row]
            if tabs and head is not None:
                self.assertEqual(tabs[0]["top"], head["top"], "%s: the %r group's first tab shares the chip's row: %r" % (label, g, tabs[0]))
            for r in rows[1:]:   # a wrapped row of the same group starts at the left edge
                first = min((t for t in tabs if t["top"] == r), key=lambda t: t["left"])
                self.assertEqual(first["left"], left, "%s: %r wraps onto a row starting at the left edge: %r" % (label, g, first))
            prev_max = max(rows)
        return secs

    def test_every_group_starts_on_its_own_line_and_a_fold_leaves_the_header_row_alone(self):
        # the shipped default: nothing written to the settings store, so this is what a fresh browser shows
        r = self._drive(DRIVER, "rows")
        o, clicked, l = r["open"], r["clicked"], r["light"]
        # the world: eight visible tabs (the archived group starts folded, its one member hidden), three headers,
        # no visible separator, one break per header after the first plus the trail's
        visible = [s for s in SESSIONS if s[2] != "archived"]
        self.assertEqual(len([i for i in o["items"] if i["id"]]), len(visible) + 1, "every visible session has its tab, the two-tag one twice: %r" % [i["name"] for i in o["items"]])
        self.assertEqual([h["group"] for h in o["heads"]], ["web", "infra", "archived"])
        self.assertEqual(o["breaks"], 3, "a break before infra, before archived, and the trail's: %r" % o["breaks"])
        for sp in o["seps"]:
            self.assertEqual(sp["h"], 0, "the untagged boundary has no height — no separator is drawn: %r" % o["seps"])
        secs = self._check_rows(o, "open")
        web = next(t for g, _h, t in secs if g == "web")
        self.assertGreater(len({t["top"] for t in web}), 1, "the web group wraps onto a further row at this width (the case under test): %r" % web)
        # FOLDED (the archived group, folded by default): its header's row holds nothing else, and the trail
        # below still opens its own line
        arch = next(h for h in o["heads"] if h["group"] == "archived")
        self.assertEqual((arch["folded"], arch["count"]), ("1", "1"), "archived is folded, its one member hidden: %r" % arch)
        arch_head = next(h for g, h, _t in secs if g == "archived")
        self.assertEqual(next(t for g, _h, t in secs if g == "archived"), [], "folded: no archived tab rendered")
        same_row = [i for i in o["items"] if i["top"] == arch_head["top"] and i["w"] > 0 and i["id"]]
        self.assertEqual(same_row, [], "folded: the header row alone: %r" % same_row)
        # T284 (the user 2026-09-09): every header opens chip, caret, count, the feed's grouped headers' order
        # (name at the left, caret and count together at the right); the folded header's member pip comes
        # last, so the folded and the open row share one shape and the caret is always the chip's neighbour
        for h in o["heads"]:
            self.assertEqual(h["kids"][:3], ["tab-group-chip", "tab-group-caret", "tab-group-count"], "%r: chip, caret, count: %r" % (h["group"], h["kids"]))
        self.assertEqual(arch["kids"], ["tab-group-chip", "tab-group-caret", "tab-group-count", "tab-group-pip"], "folded: the pip after the count")
        # the per-row hairlines (T134, kept): one under every row but the last, none at the strip's top edge
        self.assertNotIn(0.0, o["lines"], "no hairline at the top edge (a break is not a row): %r" % o["lines"])
        self.assertTrue(o["lines"], "rows are still grounded by hairlines: %r" % o["lines"])
        # T264b: the two-tag session appears under BOTH its groups, each copy a full tab (same status dot, a ✕ that
        # says it ends the one session); a click on the infra copy activates the ONE session — both copies highlight
        two = next(sid for (n, sid, _t) in SESSIONS if n == "web-search")
        copies = [d for d in o["dots"] if d["id"] == two]
        self.assertEqual(sorted(c["copy"] for c in copies), ["infra", "web"], "one copy per tag it carries: %r" % copies)
        self.assertEqual(len({c["dot"] for c in copies}), 1, "every copy wears the same state dot: %r" % copies)
        for c in copies:
            self.assertIn("one session", c["close"] or "", "the ✕ on a copy says it ends the one session: %r" % c)
        self.assertEqual([c["copy"] for c in clicked["active"] if c["id"] == two], ["web", "infra"], "the active highlight sits on every copy: %r" % clicked["active"])
        self.assertEqual(len(clicked["active"]), 2, "…and on nothing else: %r" % clicked["active"])
        infra_tabs = next(t for g, _h, t in self._sections(clicked["items"]) if g == "infra")
        self.assertIn(two, [t["id"] for t in infra_tabs], "the copy sits in the infra group: %r" % infra_tabs)
        self._check_rows(clicked, "clicked")
        # LIGHT theme: the same geometry
        self.assertIn("theme-light", l["theme"])
        self._check_rows(l, "light")

    def test_with_the_setting_off_the_strip_flows_inline_no_breaks_and_the_trail_behind_its_divider(self):
        # the gear's "One tag group per row in the tab strip" turned off (written to the settings store before the
        # page loads): the strip emits no row break, a group's header is followed by its tabs with nothing between,
        # and the untagged trail stands behind a visible 13px divider
        o = self._drive(DRIVER, "inline", rows=False)["open"]
        self.assertEqual(o["breaks"], 0, "no row break with the setting off: %r" % [i["cls"] for i in o["items"]])
        self.assertEqual(len(o["seps"]), 1, "one trail boundary: %r" % o["seps"])
        self.assertEqual(round(o["seps"][0]["w"]), 13, "the divider is a 13px box: %r" % o["seps"])
        self.assertGreater(o["seps"][0]["h"], 0, "…and visible: %r" % o["seps"])
        secs = self._sections(o["items"])
        self.assertEqual([g for g, _h, _t in secs], ["web", "infra", "archived", None], "three groups in tag order, then the trail: %r" % secs)
        by_name = {n: sid for (n, sid, _t) in SESSIONS}
        want = {"web": ["web-frontend", "web-backend", "web-gateway", "web-search", "web-billing"],
                "infra": ["web-search", "infra-ci", "infra-deploy"], "archived": [], None: ["scratch"]}
        for g, _h, tabs in secs:
            # membership, not order: within a group the strip orders tabs by the user's order and recency, and
            # _sections already proves contiguity (every tab between this header and the next belongs here)
            self.assertCountEqual([t["id"] for t in tabs], [by_name[n] for n in want[g]], "%r: its tabs, contiguous after its header, nothing else between: %r" % (g, tabs))
        # nothing of zero height sits in the strip besides the T134 hairlines (a break spans the row at zero height):
        # every item is a header, a tab or the divider
        zero = [i for i in o["items"] if i["h"] == 0 and "tab-row-line" not in i["cls"].split()]
        self.assertEqual(zero, [], "no zero-height item (a break) with the setting off: %r" % zero)


if __name__ == "__main__":
    unittest.main()
