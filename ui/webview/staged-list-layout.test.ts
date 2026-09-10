// The staged list's geometry, measured in a real browser against the real stylesheet: a staged item with
// one quote is 50px, so the list's 209px cap shows four whole items and a fifth starts the scroll; fewer
// than four and the list is its content's height; the head sits above the list, outside the scroll; an
// expanded label wraps an unbroken token instead of widening the list; the caret's hit target is the
// chip ✕'s, not the glyph's. Runs only where a Playwright browser exists (CI installs none) and SKIPS LOUDLY
// there; the CI-safe pins on the cap's inputs live in staged-list-cap.test.ts, which also executes the
// renderer whose markup this page mirrors (the last test here checks the mirror).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const CSS_PATH = path.resolve(process.cwd(), "..", "ui", "webview", "styles.css");
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const TOKEN = "a".repeat(400);   // one unbroken token wider than the list (a digest, a query string)
// one staged item as renderStagedStrip builds it: the quote as the composer's blue pill, then the row with
// the words, the hint and the discard ✕
const chip = (i: number, o: { quote?: boolean; open?: boolean; text?: string } = {}) =>
  `<div class="staged-chip${o.open ? " open" : ""}">` +
  (o.quote === false ? "" : `<div class="composer-chip staged-cite"><span class="composer-chip-mark">“</span><span class="composer-chip-label">quoted line ${i}</span><span class="staged-expand">(expand)</span></div>`) +
  `<div class="staged-row"><span class="composer-chip-mark">•</span><span class="composer-chip-label">${o.text ?? "comment " + i}</span>` +
  `<span class="staged-expand">${o.open ? "(collapse)" : "(click to expand)"}</span><button class="composer-chip-x" aria-label="Discard staged message">✕</button></div></div>`;
// six items: four with one quote each, a bare note, and an expanded item holding the token
const chips = [0, 1, 2, 3].map((i) => chip(i)).join("") + chip(4, { quote: false }) + chip(5, { open: true, text: TOKEN });
const html = `<body style="margin:0;width:700px">
<div id="composer-staged" style="display:flex" data-sid="x">
  <div class="staged-head"><button class="staged-caret" aria-expanded="true" aria-label="Hide the staged messages">▾</button><span class="staged-lbl">6 staged — sends with your next message</span><button class="staged-go">Send now</button></div>
  <div class="staged-list">${chips}</div>
</div>
</body>`;

type Box = { l: number; r: number; t: number; b: number; w: number; h: number };
type Measured = {
  chips: number[]; listClient: number; listScroll: number; listClientW: number; listScrollW: number;
  wholeVisible: number; head: Box; list: Box; caret: Box; x: Box; openLabelH: number;
  four: { client: number; scroll: number }; three: { client: number; scroll: number };
};

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
  const r = (el) => { const b = el.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, w: b.width, h: b.height }; };
  const list = document.querySelector(".staged-list");
  const all = [...document.querySelectorAll(".staged-chip")];
  const listTop = list.getBoundingClientRect().top;
  const res = {
    chips: all.map((c) => c.getBoundingClientRect().height),
    listClient: list.clientHeight, listScroll: list.scrollHeight, listClientW: list.clientWidth, listScrollW: list.scrollWidth,
    wholeVisible: all.filter((c) => c.getBoundingClientRect().bottom <= listTop + list.clientHeight + 0.01).length,
    head: r(document.querySelector(".staged-head")), list: r(list),
    caret: r(document.querySelector(".staged-caret")), x: r(document.querySelector(".composer-chip-x")),
    openLabelH: document.querySelector(".staged-chip.open .staged-row .composer-chip-label").getBoundingClientRect().height,
  };
  list.removeChild(all[5]); list.removeChild(all[4]);
  res.four = { client: list.clientHeight, scroll: list.scrollHeight };
  list.removeChild(all[3]);
  res.three = { client: list.clientHeight, scroll: list.scrollHeight };
  return res;
});
if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT, fullPage: true });
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\\n");
await browser.close();
process.exit(0);
`;

function measure(): Measured | null {
  const os = require("node:os");
  const cp = require("node:child_process");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "staged-layout-"));
  const driver = path.join(dir, "driver.mjs"); fs.writeFileSync(driver, DRIVER);
  const htmlPath = path.join(dir, "page.html"); fs.writeFileSync(htmlPath, html);
  try {
    const p = cp.spawnSync(process.execPath, [driver], { encoding: "utf8", timeout: 120000,   // the running node, never PATH
      env: { ...process.env, EXT_PKG: path.resolve(process.cwd(), "package.json"), HTML_PATH: htmlPath, CSS_PATH: CSS_PATH,
             SHOT: process.env.STAGED_LAYOUT_SHOT || "" } });
    if (p.status === 3) return null;                                  // no playwright / no browser here
    if (p.status !== 0) throw new Error("layout driver failed: " + String(p.stderr || p.stdout || p.error || "").slice(-800));
    const line = (p.stdout || "").split("\n").find((l: string) => l.startsWith("RESULT:"));
    if (!line) throw new Error("layout driver printed no result: " + (p.stdout || "").slice(-400));
    return JSON.parse(line.slice("RESULT:".length));
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}

const m = measure();
const skip = m ? false : "no Playwright browser here (CI installs none); the CI-safe pins on the cap's inputs live in staged-list-cap.test.ts";

test("a staged item with one quote is 50px, a bare note about half that, and the list's 209px cap shows exactly four whole items with the rest scrolling", { skip }, () => {
  for (const h of m!.chips.slice(0, 4)) assert.ok(Math.abs(h - 50) < 0.5, `a one-quote item is 50px (the arithmetic behind the cap), measured ${h}`);
  assert.ok(m!.chips[4] > 24 && m!.chips[4] < 27, `a bare note is the row alone, about 25px, measured ${m!.chips[4]}`);
  assert.equal(m!.listClient, 209, "the list's box is the cap");
  assert.ok(m!.listScroll > m!.listClient, `six items overflow the cap and scroll (${m!.listScroll} in ${m!.listClient})`);
  assert.equal(m!.wholeVisible, 4, "four whole items in view, the fifth cut at the edge");
});

test("under four items the list is its content's height: the cap is a ceiling, not a fixed height", { skip }, () => {
  assert.equal(m!.four.client, 209, "four one-quote items fill the cap exactly");
  assert.equal(m!.four.scroll, 209, "and nothing to scroll");
  assert.equal(m!.three.client, m!.three.scroll, "three items: no scroll either");
  assert.ok(m!.three.client < 209 && Math.abs(m!.three.client - 156) < 1, `three items and two gaps are 156px, measured ${m!.three.client}`);
});

test("the head with the count and Send now sits above the list, outside the scroll", { skip }, () => {
  assert.ok(m!.head.b <= m!.list.t + 0.01, `head bottom ${m!.head.b} above list top ${m!.list.t}`);
  assert.ok(m!.head.h < 30, `the head is one line, measured ${m!.head.h}`);
});

test("an expanded label wraps an unbroken token inside the list instead of widening it", { skip }, () => {
  assert.ok(m!.listScrollW <= m!.listClientW, `no sideways overflow (${m!.listScrollW} in ${m!.listClientW})`);
  assert.ok(m!.openLabelH > 40, `the 400-character token wrapped to several lines, measured ${m!.openLabelH}`);
});

test("the caret's hit target is the chip ✕'s, not the glyph's", { skip }, () => {
  assert.ok(m!.caret.h >= m!.x.h - 1, `caret ${m!.caret.h}px tall against the ✕'s ${m!.x.h}`);
  assert.ok(m!.caret.w >= 13, `caret ${m!.caret.w}px wide: the glyph plus the ✕'s side padding`);
});

test("the page mirrors the renderer's anatomy (CI-safe pin)", () => {
  const strip = RENDER.split("function renderStagedStrip(")[1].split("\nfunction ")[0];
  for (const mint of [`el("div", "staged-head")`, `el("button", "staged-caret")`, `el("span", "staged-lbl")`, `el("button", "staged-go")`, `el("div", "staged-list")`,
                      `el("div", "staged-chip")`, `el("div", "composer-chip staged-cite"`, `el("span", "composer-chip-mark")`, `el("span", "composer-chip-label")`,
                      `el("span", "staged-expand")`, `el("div", "staged-row")`, `el("button", "composer-chip-x")`])
    assert.ok(strip.includes(mint), "the renderer builds " + mint);
  assert.ok(strip.includes(`chip.classList.add("open")`), "an expanded item wears .open");
  assert.ok(strip.includes(`strip.style.display = "flex"`), "the strip shows as a flex column");
});
