// T310 (the user 2026-09-10) gave an unread comment thread ONE box around the WHOLE highlighted area, never a box per
// line. On 2026-09-12 the user asked for two things on top of that geometry: the stroke is the tab strip's needs-you
// ring again (dashed, in --st-awaiting-bg, the idiom for "something here waits on you", which the 2026-09-10 change
// dropped when it matched the scroll notch's ink), and the cue follows the passage's LINE COUNT: one or two rows wear
// the pre-T310 ring on each fragment (it hugs the text; a box over two lines encloses the first line's un-highlighted
// head and the second's tail), three or more the single box (a stack of rings reads as many things; one box as one).
// The rail's unread tick agrees by colour: its halo is the same red, its fill still the notch's ink. Source pins for the
// rules, the painter and its hooks; the geometry (one box per unread thread of three rows or more, covering every
// fragment; a ring per fragment under that; both gone once read) is measured on the served page by
// tests/test_comment_outline_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const RULE = /\.cmt-outline \{ position: absolute; pointer-events: none; user-select: none; box-sizing: border-box;\s*\n\s*outline: 1\.5px dashed var\(--st-awaiting-bg\); border-radius: 3px; \}/;
const RING = /mark\.cmt-hl\.unread\.cmt-ring \{ outline: 1\.5px dashed var\(--st-awaiting-bg\); outline-offset: 1px; \}/;
const painter = () => {
  const at = RENDER.indexOf("function paintCommentOutlines(sid: string): void {");
  assert.ok(at > 0, "the painter exists");
  return RENDER.slice(at, RENDER.indexOf("\n}\n", at));
};

test("the box: one DASHED outline in the needs-you red — the tab strip's idiom — as an outline on a positioned box", () => {
  assert.match(CSS, RULE);
  const rule = CSS.match(RULE)![0];
  assert.match(rule, /outline: 1\.5px dashed var\(--st-awaiting-bg\)/, "dashed, in the token the tab strip's needs-you ring wears; never a hex, never solid");
  assert.doesNotMatch(rule, /#[0-9a-fA-F]{3,6}\b|border(?!-radius)[-\w]*:|box-shadow|background|cmt-hl/, "an outline on an empty box: nothing reflows, nothing tints, none of the notch's ink");
  assert.match(rule, /position: absolute/, "positioned: the text under it never moves");
  assert.match(rule, /pointer-events: none/, "hover and click land on the marks beneath");
  // the idiom it borrows: a session tab that waits on you
  assert.match(CSS, /\.tab\.tab-awaiting \{ --state: var\(--st-awaiting-bg\); \}/);
  assert.match(CSS, /\.tab\.tab-awaiting, \.tab\.tab-blocked, \.tab\.tab-retrying \{ outline: 2px dashed var\(--state\);/);
  // the token is the same red in both themes, and theme-parity holds it to 3:1 against the page: it is a LINE now
  const dark = CSS.split("body.theme-light {")[0];
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  assert.match(dark, /--st-awaiting-bg: #c0392b;/);
  assert.match(light, /--st-awaiting-bg: #c0392b;/);
  assert.match(fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "theme-parity.test.ts"), "utf8"), /\["--st-awaiting-bg", "--bg", 3\]/);
});

test("the ring: the unread marks of a one- or two-row passage wear the per-fragment dashed ring — the only mark rule with an outline", () => {
  assert.match(CSS, RING);
  const marks = CSS.match(/^mark\.cmt-hl[^{]*\{[^}]*\}/gms) || [];
  assert.ok(marks.length >= 7, "the mark rules are found");
  assert.deepEqual(marks.filter((r) => /outline/.test(r)).map((r) => r.split(" {")[0]), ["mark.cmt-hl.unread.cmt-ring"],
    "plain .unread carries none: which cue a thread wears is the painter's call, by row count");
});

test("the notch keeps its own ink; its UNREAD halo agrees with the box by colour (a 10×6 tick cannot show a dash)", () => {
  assert.match(CSS, /\.cmt-tick \{[^}]*background: var\(--cmt-hl-outline\);/s, "the fill stays the comment ink: 'a comment here'");
  assert.match(CSS, /\.cmt-tick\.unread \{[^}]*box-shadow: 0 0 0 1\.5px var\(--bg\), 0 0 0 3px var\(--st-awaiting-bg\); \}/s, "the halo: 'waits on you', the box's red, past the same 1.5px gap");
  assert.doesNotMatch(CSS.match(/\.cmt-tick\.unread \{[^}]*\}/s)![0], /cmt-hl-outline|color-mix/, "no yellow halo left");
  const dark = CSS.split("body.theme-light {")[0];
  const light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  assert.match(dark, /--cmt-hl-outline: #ffd54a;/, "dark: the notch's ink IS the fill's yellow");
  assert.match(light, /--cmt-hl-outline: #8f6a00;/, "light: amber ink, 4.2:1 on the cream page (theme-parity pins the ratio)");
  assert.match(fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "theme-parity.test.ts"), "utf8"), /\["--cmt-hl-outline", "--bg", 3\]/);
});

test("a fragment inside a scrolling container counts only where it shows: cut to every scrolling ancestor, and the pane's scroll listener captures inner scrolls", () => {
  const fn = painter();
  assert.match(fn, /for \(let a = m\.parentElement; a && a !== turn; a = a\.parentElement\) \{\s*\n\s*const cs = getComputedStyle\(a\);\s*\n\s*if \(cs\.overflowX === "visible" && cs\.overflowY === "visible"\) continue;/, "every ancestor that clips, up to the turn");
  assert.match(fn, /cl = Math\.max\(cl, ar\.left\); ct = Math\.max\(ct, ar\.top\); cr = Math\.min\(cr, ar\.right\); cb = Math\.min\(cb, ar\.bottom\);/, "the intersection of their rects");
  assert.match(fn, /if \(qr <= ql \|\| qb <= qt\) continue;\s*\/\/ clipped away by a scrolling ancestor/, "a fragment scrolled out adds nothing to the box");
  // a notice body is such a container; so is a wide display formula
  assert.match(CSS, /\.notice-body \{ margin-top: 7px; max-height: 420px; overflow: auto;/);
  assert.match(CSS, /\.katex-display \{ overflow-x: auto; overflow-y: hidden; \}/);
  // scroll events do not bubble: the rail scheduler's listener on #content captures them, so the boxes follow
  assert.match(RENDER, /c\.addEventListener\("scroll", scheduleRailSticky, \{ passive: true, capture: true \}\);/);
});

test("the painter: one box per unread thread of three rows or more, a child of the turn, sized to the union of the fragments' client rects", () => {
  const fn = painter();
  assert.match(fn, /querySelectorAll\("mark\.cmt-hl\.unread"\)/, "unread marks only");
  assert.match(fn, /want\.get\(tid\)/, "grouped by thread: one box per tid");
  assert.match(fn, /m\.getClientRects\(\)/, "every line fragment of every mark of the thread");
  assert.match(fn, /l = Math\.min\(l, ql\); t = Math\.min\(t, qt\); r = Math\.max\(r, qr\); b = Math\.max\(b, qb\);/, "the union of the CLIPPED fragments");
  assert.match(fn, /marks\[0\]\.closest\("\.turn"\)/, "the box lives on the anchor turn (position: relative): it scrolls with the text");
  assert.match(fn, /box = el\("div", "cmt-outline"\); box\.dataset\.tid = tid; turn\.appendChild\(box\);/);
  assert.match(fn, /turn\.getBoundingClientRect\(\)/, "turn-relative coordinates");
  assert.match(fn, /if \(!marks \|\| marks\[0\]\.closest\("\.turn"\) !== box\.parentElement\) box\.remove\(\);/, "a box whose thread is read, resolved or gone is removed");
  assert.match(fn, /if \(ring \|\| !isFinite\(l\)\) \{ box\?\.remove\(\); continue; \}/, "a ringed thread has no box; nor has one with no visible fragment (hidden, or all scrolled out)");
  assert.match(fn, /if \(box\.style\[k\] !== css\[k\]\) box\.style\[k\] = css\[k\];/, "writes only what changed: the rAF path is a measure pass most frames");
  assert.match(CSS, /^\.turn \{ position: relative;/m, "the turn is the positioning context the box relies on");
});

test("the row rule: line rows counted from the rects the painter already reads; one or two → the ring class on the fragments, three or more → the box", () => {
  const fn = painter();
  // a fragment opens a new row when its top sits at least half the shorter height from the current row's: fragments split
  // across an inline-code or KaTeX host share a line (tops a padding apart), consecutive lines sit a line-height apart —
  // a fraction of the rect's own height, never a magic pixel count
  assert.match(fn, /let rows = 0, rowTop = 0, rowH = 0;/);
  assert.match(fn, /if \(!rows \|\| Math\.abs\(q\.top - rowTop\) >= Math\.min\(q\.height, rowH\) \/ 2\) \{ rows\+\+; rowTop = q\.top; rowH = q\.height; \}/);
  // counted on every fragment BEFORE the clip: the passage's shape is a fact of its layout, not of a container's scroll
  const count = fn.indexOf("rows++"), clip = fn.indexOf("clipped away by a scrolling ancestor");
  assert.ok(count > 0 && clip > count, "the row count precedes the clip");
  assert.match(fn, /const ring = rows > 0 && rows <= 2;/, "one or two rows: the ring; three or more: the box; none visible: neither");
  assert.match(fn, /for \(const m of marks\) m\.classList\.toggle\("cmt-ring", ring\);/, "the class on every fragment of the thread, on or off");
  // zero new machinery (the user 2026-09-12): the rule rides the painter's three hooks and its existing reads — getClientRects
  // once per mark, getBoundingClientRect once per clipping ancestor and once per turn; the painter registers nothing
  assert.equal((fn.match(/getClientRects\(\)/g) || []).length, 1, "one rects read per mark: the rows and the union come from the same call");
  assert.equal((fn.match(/getBoundingClientRect\(\)/g) || []).length, 2, "no per-mark layout read beyond the clip ancestors' and the turn's");
  assert.doesNotMatch(fn, /ResizeObserver|MutationObserver|IntersectionObserver|addEventListener|requestAnimationFrame|setTimeout|setInterval/);
  assert.equal((RENDER.match(/paintCommentOutlines\(/g) || []).length, 5, "the definition and its four call sites: the marks pass (twice), the rail's rAF scheduler, the window resize hook");
  // the ring goes with the unread bit exactly where the box does: styleCommentMark drops it on the pass that drops unread
  // (the popover opening, a resolve); a deleted thread's or a windowed-out turn's marks leave the DOM, class and all
  assert.match(RENDER, /m\.classList\.toggle\("unread", !!th\.unread && th\.status === "open"\);\s*\n\s*if \(!\(th\.unread && th\.status === "open"\)\) m\.classList\.toggle\("cmt-ring", false\);/);
});

test("the box follows the marks: repainted after every marks pass, on the rail's rAF scheduler and on window resize — no timers", () => {
  const at = RENDER.indexOf("function applyCommentMarks(sid: string): void {");
  const pass = RENDER.slice(at, RENDER.indexOf("\n}\n", at));
  assert.match(pass, /if \(!threads\.length\) \{ paintCommentOutlines\(sid\);/, "the last thread gone: its box goes on the same pass");
  assert.match(pass, /for \(const th of list\) ensureCommentMark\(turn, th\);[\s\S]*paintCommentOutlines\(sid\);/, "after the marks are placed");
  assert.match(RENDER, /requestAnimationFrame\(\(\) => \{ railStickyPending = false; paintRailSticky\(\); paintScrollMarks\(\); updateCommentRail\(\); if \(activeId && hasUnreadOpenThread\(activeId\)\) paintCommentOutlines\(activeId\); \}\);/,
    "the notches' and ticks' own repaint path (a re-render, the view's resize observer, every scroll frame) — gated");
  assert.match(RENDER, /window\.addEventListener\("resize", \(\) => \{ updateCommentRail\(\); if \(activeId && hasUnreadOpenThread\(activeId\)\) paintCommentOutlines\(activeId\); \}\);/,
    "a resize can carry a passage across the two-row line; this hook is what flips it");
  // the gate is a store read, never a DOM walk: a scroll frame on a session with nothing unread costs a Map lookup
  assert.match(RENDER, /function hasUnreadOpenThread\(sid: string\): boolean \{\s*\n\s*return \(commentThreads\.get\(sid\) \|\| \[\]\)\.some\(\(t\) => !!t\.unread && t\.status === "open"\);\s*\n\}/);
  // …while the marks pass stays ungated: it is the removal path (the last unread thread gone takes its box with it)
  assert.doesNotMatch(pass, /hasUnreadOpenThread/);
  assert.doesNotMatch(painter(), /setTimeout|setInterval/);
});
