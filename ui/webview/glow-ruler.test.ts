// Overview ruler (link_audit's #4 delegation, the user 2026-06-22): a thin fixed strip over the #content
// scrollbar gutter that bands the FULL-transcript location of whatever turns carry .ext-glow, so a
// cross-surface hover (timeline / feed card / chat dot) shows WHERE in the scroll the hovered thing sits
// even when it's scrolled off-screen. Hover-only; bands map content-space → ruler-space (scroll-independent).
// The chat renderer has no jsdom harness, so — like the other webview *.test.ts — pin it at the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("applyGlow repaints the ruler — the SINGLE glow source feeds it, so every hover direction is covered", () => {
  // link_audit's contract: applyGlow is the one place .ext-glow is added/removed; painting at its end means
  // the ruler is automatically right for timeline→chat, feed→chat, and chat-dot→same-segment hovers.
  assert.match(RENDER, /paintGlowRuler\(\);   \/\/ mirror the glow as bands on the overview ruler/);
  assert.match(RENDER, /function paintGlowRuler\(\): void/);
  assert.match(RENDER, /function ensureGlowRuler\(\): HTMLElement/);
  // a single lazily-created strip on <body>, hidden until there's a glow
  assert.match(RENDER, /glowRuler = el\("div", "glow-ruler"\);[\s\S]*?document\.body\.appendChild\(glowRuler\)/);
});

test("the ruler observes ONLY the active view's .ext-glow turns and hides when there are none", () => {
  // other views are display:none (zero rects), so only the active view's glows map onto its #content scroll
  assert.match(RENDER, /const v = activeId \? views\.get\(activeId\) : null;/);
  assert.match(RENDER, /v\.el\.querySelectorAll<HTMLElement>\("\.turn\.ext-glow"\)/);
  // …or history marks (T318b): a hover on turns outside the resident tail shows the strip with no glowing row
  assert.match(RENDER, /if \(!content \|\| \(!glows\.length && !hist\.length\)\) \{ ruler\.style\.display = "none"; ruler\.replaceChildren\(\); return; \}/);
});

test("bands are CONTENT-space (scroll-independent), mapped to ruler space by scrollHeight", () => {
  // per glowing turn: top = turn.top − content.top + scrollTop (absolute transcript position); the strip
  // spans the visible height (clientHeight); bandTop/bandH = span / scrollHeight * rulerH (link_audit Q2)
  assert.match(RENDER, /const top = turn\.getBoundingClientRect\(\)\.top - rect\.top \+ content\.scrollTop;/);
  assert.match(RENDER, /const rulerH = content\.clientHeight;/);
  assert.match(RENDER, /const scrollH = content\.scrollHeight \|\| 1;/);
  // the resident map sits under the history cap (0 px when nothing unloaded is hovered, T318b): capH + span / scrollH * mapH
  assert.match(RENDER, /band\.style\.top = \(capH \+ b\.top \/ scrollH \* mapH\) \+ "px";/);
  assert.match(RENDER, /band\.style\.height = Math\.max\(3, \(b\.bot - b\.top\) \/ scrollH \* mapH\) \+ "px";/);
  assert.match(RENDER, /const mapH = Math\.max\(1, rulerH - capH\);/);
});

test("contiguous glowing turns coalesce into ONE band; gaps make separate bands (link_audit spec)", () => {
  // sort by top, then merge a turn into the previous band when it touches/overlaps it (+3px slack); a
  // multi-segment goal hover therefore yields one band per contiguous run, not one per turn.
  assert.match(RENDER, /\.sort\(\(a, b\) => a\.top - b\.top\)/);
  assert.match(RENDER, /if \(last && s\.top <= last\.bot \+ 3\) last\.bot = Math\.max\(last\.bot, s\.bot\);\s*else bands\.push\(\{ top: s\.top, bot: s\.bot \}\);/);
});

test("the strip pins over the #content scrollbar gutter (width == the webkit scrollbar)", () => {
  // getBoundingClientRect → robust to the window-accent body border; left = right edge − RULER_W so the
  // 10px strip sits in the scrollbar gutter; RULER_W matches ::-webkit-scrollbar width
  assert.match(RENDER, /const RULER_W = 10;/);
  assert.match(CSS, /::-webkit-scrollbar \{ width: 10px;/);   // the width the strip mirrors
  assert.match(RENDER, /ruler\.style\.left = \(rect\.right - RULER_W\) \+ "px";/);
  assert.match(RENDER, /ruler\.style\.top = rect\.top \+ "px";/);
  assert.match(RENDER, /ruler\.style\.height = rulerH \+ "px";/);
});

test("the ruler repaints on glow change + window resize + #content relayout, NOT on plain scroll", () => {
  // bands are content-space → scroll-independent; only the viewport box / scrollHeight moving needs a repaint
  assert.match(RENDER, /window\.addEventListener\("resize", paintGlowRuler\);/);
  assert.match(RENDER, /const ro = new ResizeObserver\(\(\) => paintGlowRuler\(\)\);[\s\S]*?if \(c\) ro\.observe\(c\)/);
  assert.doesNotMatch(RENDER, /addEventListener\("scroll", paintGlowRuler\)/);   // no per-scroll recompute
});

test("the ruler is a pure indicator — fixed over the gutter, pointer-events:none so the scrollbar still works", () => {
  assert.match(CSS, /\.glow-ruler \{[^}]*position: fixed;[^}]*width: 10px;[^}]*pointer-events: none/);
  assert.match(CSS, /\.glow-ruler-band \{[^}]*position: absolute;[^}]*border-radius: 3px/);
});

test("bands take the active agent's identity colour at ~0.5 alpha, matching the chat's left rail line (the user 2026-06-22)", () => {
  // the left rail line is .turn::before { background: var(--active-accent, var(--rail)) }; the bands use the
  // SAME --active-accent (the active session's color.bg, set on <body> by showActive) at 50% via color-mix,
  // so the overview reads as "this agent's" colour. White fallback when no session colour is set.
  assert.match(CSS, /\.glow-ruler-band \{[^}]*background: color-mix\(in srgb, var\(--active-accent, #fff\) 50%, transparent\)/);
  // and the chat's left rail line it's matching, so the link between the two is pinned
  assert.match(CSS, /\.turn::before \{[^}]*background: var\(--active-accent, var\(--rail\)\)/);
});
