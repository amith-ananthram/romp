// T310 (the user 2026-09-10): an unread comment thread's cue is ONE solid outline in the scroll notch's yellow around
// the WHOLE highlighted area — never a box per line. Source pins for the rule, the painter and its hooks; the
// geometry (exactly one box per unread thread, covering every fragment; gone once read) is measured on the served
// page by tests/test_comment_outline_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const RULE = /\.cmt-outline \{ position: absolute; pointer-events: none; user-select: none; box-sizing: border-box;\s*\n\s*outline: 1\.5px solid var\(--cmt-hl\); border-radius: 3px; \}/;

test("one solid outline in the notch's yellow: the same token the tick wears, as an outline on a positioned box", () => {
  assert.match(CSS, RULE);
  const rule = CSS.match(RULE)![0];
  assert.match(rule, /outline: 1\.5px solid var\(--cmt-hl\)/, "solid, not dashed; the token, never a hex");
  assert.doesNotMatch(rule, /#[0-9a-fA-F]{3,6}\b|border(?!-radius)[-\w]*:|box-shadow|background/, "an outline on an empty box: nothing reflows, nothing tints");
  assert.match(rule, /position: absolute/, "positioned: the text under it never moves");
  assert.match(rule, /pointer-events: none/, "hover and click land on the marks beneath");
  // the notch for the same thread: .cmt-tick's fill is the very same token, in both themes
  assert.match(CSS, /\.cmt-tick \{[^}]*background: var\(--cmt-hl\);/s);
  const dark = CSS.split("body.theme-light {")[0];
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  assert.match(dark, /--cmt-hl: #ffd54a;/);
  assert.match(light, /--cmt-hl: #FFDF70;/);
});

test("the painter: one box per unread thread, a child of the turn, sized to the union of the fragments' client rects", () => {
  const at = RENDER.indexOf("function paintCommentOutlines(sid: string): void {");
  assert.ok(at > 0, "the painter exists");
  const fn = RENDER.slice(at, RENDER.indexOf("\n}\n", at));
  assert.match(fn, /querySelectorAll\("mark\.cmt-hl\.unread"\)/, "unread marks only");
  assert.match(fn, /want\.get\(tid\)/, "grouped by thread: one box per tid");
  assert.match(fn, /m\.getClientRects\(\)/, "every line fragment of every mark of the thread");
  assert.match(fn, /l = Math\.min\(l, q\.left\); t = Math\.min\(t, q\.top\); r = Math\.max\(r, q\.right\); b = Math\.max\(b, q\.bottom\);/, "the union");
  assert.match(fn, /marks\[0\]\.closest\("\.turn"\)/, "the box lives on the anchor turn (position: relative): it scrolls with the text");
  assert.match(fn, /box = el\("div", "cmt-outline"\); box\.dataset\.tid = tid; turn\.appendChild\(box\);/);
  assert.match(fn, /turn\.getBoundingClientRect\(\)/, "turn-relative coordinates");
  assert.match(fn, /if \(!marks \|\| marks\[0\]\.closest\("\.turn"\) !== box\.parentElement\) box\.remove\(\);/, "a box whose thread is read, resolved or gone is removed");
  assert.match(fn, /if \(!isFinite\(l\)\) \{ box\?\.remove\(\); continue; \}/, "no fragment has a box (hidden) → no outline");
  assert.match(fn, /if \(box\.style\[k\] !== css\[k\]\) box\.style\[k\] = css\[k\];/, "writes only what changed: the rAF path is a measure pass most frames");
  assert.match(CSS, /^\.turn \{ position: relative;/m, "the turn is the positioning context the box relies on");
});

test("the box follows the marks: repainted after every marks pass, on the rail's rAF scheduler and on window resize — no timers", () => {
  const at = RENDER.indexOf("function applyCommentMarks(sid: string): void {");
  const pass = RENDER.slice(at, RENDER.indexOf("\n}\n", at));
  assert.match(pass, /if \(!threads\.length\) \{ paintCommentOutlines\(sid\);/, "the last thread gone: its box goes on the same pass");
  assert.match(pass, /for \(const th of list\) ensureCommentMark\(turn, th\);[\s\S]*paintCommentOutlines\(sid\);/, "after the marks are placed");
  assert.match(RENDER, /requestAnimationFrame\(\(\) => \{ railStickyPending = false; paintRailSticky\(\); paintScrollMarks\(\); updateCommentRail\(\); if \(activeId\) paintCommentOutlines\(activeId\); \}\);/,
    "the notches' and ticks' own repaint path (a re-render, the view's resize observer)");
  assert.match(RENDER, /window\.addEventListener\("resize", \(\) => \{ updateCommentRail\(\); if \(activeId\) paintCommentOutlines\(activeId\); \}\);/);
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("function paintCommentOutlines"), RENDER.indexOf("\n}\n", RENDER.indexOf("function paintCommentOutlines"))), /setTimeout|setInterval/);
});
