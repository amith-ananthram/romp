// COMPACT TABS AND AGENTS, measured. dense-chrome.test.ts pins the dense rules by their text; a pin cannot see a
// rule that keeps its text and loses in the cascade (a later default outranking the guarded list cap) or a row
// standing taller than its own rule says (#tabs stretches every item on a row to the tallest). So a real browser
// lays out the strip and the panel markup render.ts builds (planStrip, renderBgTasks, bgRow) against the real
// stylesheet, with body.dense-chrome off, on, on with a row's details open, and off again, and the numbers the
// stylesheet's block comment states are asserted here. Runs only where a Playwright browser exists (CI installs
// none) and SKIPS LOUDLY there, the queued-romp-layout.test.ts pattern; the CI-safe pins live in dense-chrome.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const CSS_PATH = path.resolve(process.cwd(), "..", "ui", "webview", "styles.css");

// the markup render.ts builds, reduced to the nodes the dense rules touch. The chip's inline style is tagChip's
// (tag-menu.ts, inheritSize), the tag button's is tagMenuButton's, the arrow is agentOpenButton's svg.
const ARROW = '<span class="tool-open-agent bg-open-agent" role="button"><svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M7 3.5H4a1 1 0 0 0-1 1V12a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1V9"/></svg></span>';
const CHIP = '<span class="tab-group-chip" style="display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:9px;border:1px solid var(--dim);color:var(--dim);background:transparent;white-space:nowrap;font-weight:400;letter-spacing:normal;">web</span>';
const TAGBTN = '<button type="button" style="background:transparent;border:1px solid #3c3c3c;border-radius:6px;padding:4px 6px;cursor:pointer;color:#9aa0a6;display:inline-flex;align-items:center;"><svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2 7.5 L7.5 2.5 H14 V9 L8.5 14 Z" stroke="currentColor" stroke-width="1.4"/></svg></button>';
const tab = (id: string, label: string, cls = "") =>
  `<div class="tab${cls}" id="${id}"><span class="tab-label">${label}</span><span class="tab-close">×</span></div>`;
// a row as bgRow builds it: dot, label, the arrow on an agent row, the elapsed time, the caption, the row's caret
const row = (id: string, status: string, caption: string, arrow: boolean) =>
  `<div class="bg-task bg-${status}" id="${id}"><div class="bg-head"><span class="bg-dot"></span><span class="bg-sum">${id} on the notes-api tests</span>` +
  `${arrow ? ARROW : ""}<span class="bg-since">· 2m</span><span class="bg-status">${caption}</span><span class="bg-caret">▸</span></div></div>`;
const html = `<body style="margin:0;width:900px">
<div id="tabbar"><div id="tabs">
  <div class="tab-group-head" id="gh">${CHIP}<span class="tab-group-caret">▸</span><span class="tab-group-count">3</span></div>
  ${tab("t1", "web", " active")}
  ${tab("t2", '<span class="host-prefix" id="hp">TESTHOST:</span>api')}
  <div class="tab-group-sep" id="sep"></div>
  ${tab("t3", "tests")}
  <div class="tab tab-add" id="add">+</div>
  <span class="tab-tagbox" id="tagbox">${TAGBTN}<span class="tab-tagchips" style="display:inline-flex;gap:5px;align-items:center;margin-left:2px;"></span></span>
</div></div>
<div id="bg-tasks">
  <div class="bg-fold-head open" id="bar"><span class="bg-caret">▾</span><span class="bg-dot"></span><span class="bg-fold-label">In the background · 5 agents · 1 command</span></div>
  <div class="bg-list" id="list">
    ${row("running", "running", "running", true)}
    ${row("armed", "armed", "armed", true)}
    ${row("completed", "completed", "completed", true)}
    ${row("failed", "failed", "failed", true)}
    ${row("timer", "waiting", "timer", false)}
    ${row("flat", "running", "running", false)}
  </div>
</div>
</body>`;

type Snap = {
  tab: number; tabPrefixed: number; head: number; add: number; tagbox: number; sepW: number; sepLine: number;
  hostPrefixFs: number; closeFs: number; countFs: number;
  panel: number; bar: number; list: number; listMax: string;
  rowArrow: number; rowFlat: number; arrow: number;
  status: Record<string, string>; sumFs: number; sinceFs: number;
};
type Measured = { off: Snap; on: Snap; onOpen: Snap; offAgain: Snap };

// The measurement runs in a standalone driver process (the T225 served-test pattern): the test bundle is
// CommonJS without top-level await, and esbuild must never try to bundle playwright itself.
const DRIVER = `
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
let chromium;
try { chromium = require("playwright").chromium; } catch (e) { process.exit(3); }
let browser;
try { browser = await chromium.launch(); } catch (e) { process.exit(3); }
const page = await browser.newPage({ viewport: { width: 900, height: 1100 } });
await page.setContent(fs.readFileSync(process.env.HTML_PATH, "utf8"));
await page.addStyleTag({ path: process.env.CSS_PATH });
const out = await page.evaluate(() => {
  const q = (sel) => document.querySelector(sel);
  const h = (el) => el.getBoundingClientRect().height;
  const cs = (el, prop) => getComputedStyle(el)[prop];
  const fs = (el) => parseFloat(cs(el, "fontSize"));
  const snap = () => {
    const sep = q("#sep");
    const status = {};
    for (const id of ["running", "armed", "completed", "failed", "timer"]) status[id] = cs(q("#" + id + " .bg-status"), "display");
    return {
      tab: h(q("#t1")), tabPrefixed: h(q("#t2")), head: h(q("#gh")), add: h(q("#add")), tagbox: h(q("#tagbox")),
      sepW: sep.getBoundingClientRect().width, sepLine: h(sep) - parseFloat(cs(sep, "paddingTop")) - parseFloat(cs(sep, "paddingBottom")),
      hostPrefixFs: fs(q("#hp")), closeFs: fs(q("#t1 .tab-close")), countFs: fs(q("#gh .tab-group-count")),
      panel: h(q("#bg-tasks")), bar: h(q("#bar")), list: h(q("#list")), listMax: cs(q("#list"), "maxHeight"),
      rowArrow: h(q("#running")), rowFlat: h(q("#flat")), arrow: h(q("#running .bg-open-agent")),
      status, sumFs: fs(q("#running .bg-sum")), sinceFs: fs(q("#running .bg-since")),
    };
  };
  const off = snap();
  document.body.classList.add("dense-chrome");
  const on = snap();
  // a row's details open: the class bgRow sets, and the detail block it appends under the head
  const opened = q("#completed");
  opened.classList.add("open");
  const det = document.createElement("div"); det.className = "bg-detail";
  const cmd = document.createElement("pre"); cmd.className = "bg-cmd"; cmd.textContent = "npm test"; det.appendChild(cmd);
  const outp = document.createElement("pre"); outp.className = "bg-out"; outp.textContent = Array.from({ length: 12 }, (_, i) => "line " + i).join("\\n"); det.appendChild(outp);
  opened.appendChild(det);
  const onOpen = snap();
  opened.classList.remove("open"); det.remove();
  document.body.classList.remove("dense-chrome");
  const offAgain = snap();
  return { off, on, onOpen, offAgain };
});
if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT, fullPage: true });
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\\n");
await browser.close();
process.exit(0);
`;

function measure(): Measured | null {
  const os = require("node:os");
  const cp = require("node:child_process");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dense-chrome-layout-"));
  const driver = path.join(dir, "driver.mjs"); fs.writeFileSync(driver, DRIVER);
  const htmlPath = path.join(dir, "page.html"); fs.writeFileSync(htmlPath, html);
  try {
    const p = cp.spawnSync(process.execPath, [driver], { encoding: "utf8", timeout: 120000,   // the running node, never PATH
      env: { ...process.env, EXT_PKG: path.resolve(process.cwd(), "package.json"), HTML_PATH: htmlPath, CSS_PATH: CSS_PATH,
             SHOT: process.env.DENSE_CHROME_LAYOUT_SHOT || "" } });
    if (p.status === 3) return null;                                  // no playwright / no browser here
    if (p.status !== 0) throw new Error("layout driver failed: " + String(p.stderr || p.stdout || p.error || "").slice(-800));
    const line = (p.stdout || "").split("\n").find((l: string) => l.startsWith("RESULT:"));
    if (!line) throw new Error("layout driver printed no result: " + (p.stdout || "").slice(-400));
    return JSON.parse(line.slice("RESULT:".length));
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

const m = measure();
const skip = m ? false : "no Playwright browser here: the layout measurement needs one (CI installs none); the CI-safe source pins live in dense-chrome.test.ts";
const near = (got: number, want: number, what: string, tol = 0.5) =>
  assert.ok(Math.abs(got - want) <= tol, what + ": " + got.toFixed(2) + "px, wanted about " + want + "px");

test("a tab is about 25px tall under the setting (32px by default); the + tab and the tag control follow it", { skip }, () => {
  const { off, on } = m!;
  near(off.tab, 32, "the default tab", 1);
  near(on.tab, 25, "the dense tab"); near(on.tabPrefixed, 25, "a federated tab");
  near(on.add, 25, "the + tab (its 18px line + 2 x 3px + 1px border)");
  near(on.tagbox, 25, "the tag control's floor is the + tab's height, so its row stands no taller than the tabs' rows");
});

test("a group header stretches to its row's tabs, so it is the tab's height in both states", { skip }, () => {
  // #tabs has align-items: stretch: on a row with tabs the header is as tall as the tabs, whatever its own
  // padding says (about 31px on its own by default, 25px dense, the chip's 1.2 line inside the 0.82em header)
  const { off, on } = m!;
  near(off.head, off.tab, "default: header and tab share a height", 0.1);
  near(on.head, on.tab, "dense: header and tab share a height", 0.1);
  assert.equal(on.countFs, off.countFs, "the header's count keeps its size: the header rule sets none");
});

test("the untagged trail's divider keeps its half-row line at the default's width", { skip }, () => {
  const { off, on } = m!;
  assert.equal(on.sepW, off.sepW, "the width the tab drag's virtual layout measures is unchanged");
  near(on.sepW, 13, "13px wide", 0.1);
  near(on.sepLine, 13, "the 1px line's height under 6px gutters in a 25px row", 0.6);
  near(off.sepLine, 16, "the default's line under 8px gutters in a 32px row", 1);
});

test("a federated tab's host prefix and the close glyph keep their rendered size under the smaller tab", { skip }, () => {
  const { off, on } = m!;
  near(on.hostPrefixFs, off.hostPrefixFs, "the host prefix renders at the default's size (0.92em inside 0.86em is the default 0.86em inside 0.92em)", 0.05);
  assert.ok(on.hostPrefixFs >= 10, "at or above the 10px floor: " + on.hostPrefixFs.toFixed(2) + "px");
  assert.ok(on.closeFs >= 10, "the close glyph stays above the floor: " + on.closeFs.toFixed(2) + "px");
});

test("the list is capped at 100px while every row is closed, and the cap lifts the moment a row's details open", { skip }, () => {
  const { off, on, onOpen } = m!;
  assert.equal(off.listMax, "none", "no cap by default: the panel's own min(50vh, 340px) is the bound");
  assert.equal(on.listMax, "100px");
  near(on.list, 100, "six dense rows scroll inside the cap");
  assert.ok(off.list > 100, "the same rows stand taller than the cap by default: " + off.list.toFixed(1) + "px");
  assert.equal(onOpen.listMax, "none", "an open row lifts the cap");
  assert.ok(onOpen.list > 100, "the open row's details show inside the panel's bound, not a second scroll: " + onOpen.list.toFixed(1) + "px");
  assert.ok(onOpen.panel <= 340, "the panel's own cap holds: " + onOpen.panel.toFixed(1) + "px");
});

test("a row is 24px with the open-transcript arrow, which keeps its 18px, and about 20px without", { skip }, () => {
  const { off, on } = m!;
  near(on.rowArrow, 24, "an agent row"); near(on.rowFlat, 20.3, "a row without the arrow");
  assert.equal(on.arrow, off.arrow, "the arrow is untouched"); near(on.arrow, 18, "the arrow's 18px", 0.1);
  assert.ok(off.rowArrow > 28 && off.rowFlat > 28, "the default rows: " + off.rowArrow.toFixed(1) + " / " + off.rowFlat.toFixed(1) + "px");
  assert.ok(on.bar < off.bar, "the header bar loses vertical slack too: " + on.bar.toFixed(1) + " from " + off.bar.toFixed(1) + "px");
});

test("the status word hides where the dot already says it, and stays on a timer row; the row's sizes are the panel's own", { skip }, () => {
  const { off, on } = m!;
  for (const st of ["running", "armed", "completed", "failed"]) {
    assert.equal(on.status[st], "none", st + ": the dot carries the status");
    assert.notEqual(off.status[st], "none", st + ": shown by default");
  }
  assert.notEqual(on.status.timer, "none", "a timer's caption stays: its dot says only waiting");
  assert.equal(on.sumFs, 11); near(off.sumFs, 11.96, "the default .bg-sum, 0.92em at the 13px base", 0.05);
  assert.equal(on.sinceFs, 10); near(off.sinceFs, 10.66, "the default .bg-since, 0.82em at the 13px base", 0.05);
});

test("off again, every measurement is what it was before the class went on", { skip }, () => {
  assert.deepEqual(m!.offAgain, m!.off, "the setting leaves no trace once off");
});
