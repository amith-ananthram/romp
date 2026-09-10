// Bidirectional virtualization of the chat transcript (the user 2026-06-25). A long session renders one row
// per event (or per folded compact item) — thousands of nodes — which made switching to / scrolling a big
// session slow. Both modes now render a bounded window of UNITS [winStart, winEnd) with a TOP spacer for the
// hidden head and a BOTTOM spacer for the hidden tail; on scroll we re-render AROUND wherever the viewport
// lands, so random-access jumps work, not just contiguous scroll-back. Source-level pins (no jsdom for the
// renderer), mirroring the other render.ts tests.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("windowing constants are sane: a tail, a render radius and a re-window margin, a switch cap", () => {
  // (the trailing re-check window, TAIL_RECHECK, is gone: the tail path re-renders exactly from the kernel's
  // first changed event — chat-exact-tail.test.ts)
  const tail = Number(/const WINDOW_TAIL = (\d+);/.exec(RENDER)?.[1]);
  const radius = Number(/const WINDOW_RADIUS = (\d+);/.exec(RENDER)?.[1]);
  const margin = Number(/const REVIRT_MARGIN = (\d+);/.exec(RENDER)?.[1]);
  const cap = Number(/const WINDOW_CAP = (\d+);/.exec(RENDER)?.[1]);
  assert.ok(tail > 0, "a tail window");
  assert.ok(radius > margin && margin > 0, "radius > margin > 0");
  assert.ok(cap > tail, "cap > tail");
});

test("units unify both modes: one per event (normal) or the folded compactDisplay stream (compact)", () => {
  assert.match(RENDER, /function displayItems\(s: Session\): DisplayItem\[\]/);
  assert.match(RENDER, /if \(!settings\.compact\) \{[\s\S]*?out\.push\(\{ kind: "event", index: i \}\);/);
  assert.match(RENDER, /return compactDisplay\(/);
});

test("every rendered row is tagged data-unit, so the scroll↔unit map can locate it", () => {
  assert.match(RENDER, /node\.dataset\.unit = String\(u\);/);          // appendItem (window build)
  assert.match(RENDER, /node\.dataset\.unit = String\(i\);\s*\/\/ unit === event/); // normal incremental append
});

test("renderWindowItems renders [unitStart, unitEnd) with a TOP and a BOTTOM spacer", () => {
  assert.match(RENDER, /function renderWindowItems\(v: View, s: Session, items: DisplayItem\[\], unitStart: number, unitEnd: number, working: boolean\): void/);
  assert.match(RENDER, /if \(unitStart > 0\) v\.el\.appendChild\(el\("div", "tx-spacer tx-spacer-top"\)\);/);
  assert.match(RENDER, /if \(unitEnd < total\) v\.el\.appendChild\(el\("div", "tx-spacer tx-spacer-bot"\)\);/);
  assert.match(RENDER, /v\.winStart = unitStart; v\.winEnd = unitEnd;/);
  assert.match(RENDER, /v\.spacerCount = unitStart; v\.spacerCountBot = total - unitEnd;/);
});

test("sizeSpacers sizes BOTH spacers by hidden-unit count × avg, caching only a visible measurement", () => {
  assert.match(RENDER, /function sizeSpacers\(v: View\): void/);
  assert.match(RENDER, /const topAfter = top \? Math\.max\(0, Math\.round\(\(v\.spacerCount \?\? 0\) \* avg\)\) : 0, botAfter = bot \? Math\.max\(0, Math\.round\(\(v\.spacerCountBot \?\? 0\) \* avg\)\) : 0;/);
  assert.match(RENDER, /if \(top\) top\.style\.height = topAfter \+ "px";\s*\n\s*if \(bot\) bot\.style\.height = botAfter \+ "px";/);
  assert.match(RENDER, /if \(h > 0 && n > 0\) v\.avgTurnH = h \/ n;/);   // don't cache a display:none 0
});

test("unitAtScroll maps a spacer by avg height and a rendered row by its data-unit", () => {
  assert.match(RENDER, /function unitAtScroll\(v: View, content: HTMLElement\): number/);
  assert.match(RENDER, /if \(st < topH\) return Math\.max\(0, Math\.floor\(st \/ avg\)\);/);   // in the top spacer
  assert.match(RENDER, /if \(st < t0 \+ c\.offsetHeight\) return lastUnit;/);                  // straddling a rendered row
  assert.match(RENDER, /return \(v\.winEnd \?\? 0\) \+ Math\.floor\(\(st - bTop\) \/ avg\);/);  // in the bottom spacer
});

test("scroll re-windows around the viewport (steady scroll OR jump) when near a rendered edge", () => {
  assert.match(RENDER, /function virtualizeToViewport\(\): void/);
  // CHEAP px pre-check on every scroll; the precise unit walk only runs when near a rendered edge
  assert.match(RENDER, /const nearTopEdge = \(v\.winStart \?\? 0\) > 0 && st < topH \+ edgePx;/);
  assert.match(RENDER, /const nearBotEdge = \(v\.winEnd \?\? total\) < total && st \+ vh > renderedBottom - edgePx;/);
  assert.match(RENDER, /if \(!nearTopEdge && !nearBotEdge\) return;/);
  assert.match(RENDER, /const idx = unitAtScroll\(v, content\);/);
  assert.match(RENDER, /renderWindowItems\(v, s, items, Math\.max\(0, c - WINDOW_RADIUS\), Math\.min\(items\.length, c \+ WINDOW_RADIUS\), working\);/);
  // it re-anchors the focus unit so it doesn't jump, and shows a loading cue, coalesced to one frame
  assert.match(RENDER, /writeScroll\(content, yNow - beforeY, "rewindow"\);/);   // (T262: every #content write rides writeScroll)
  assert.match(RENDER, /showLoadingPill\(\);/);
  assert.match(RENDER, /c\.addEventListener\("scroll", virtualizeToViewport, \{ passive: true \}\);/);
});

test("a loading pill shows while history renders, pinned top-center of the chat pane", () => {
  assert.match(RENDER, /function showLoadingPill\(\): void/);
  assert.match(RENDER, /loadingPillEl\.textContent = "Loading earlier messages…";/);
  assert.match(CSS, /\.tx-loading-pill \{[\s\S]*position: fixed[\s\S]*\}/);
});

test("syncView: a fresh build / rewind renders the TAIL window, clamped to the last compaction boundary", () => {
  // the default window opens AT (never below) the newest compaction — pre-compaction history is scrubbed
  // from the default view (the user 2026-07-07); lastCompactUnit floors the window start.
  assert.match(RENDER, /if \(firstBuild \|\| rewind\) \{\s*\n\s*const start = Math\.max\(0, total - WINDOW_TAIL, lastCompactUnit\(s, items\)\);\s*\n\s*renderWindowItems\(v, s, items, start, total, working\);/);
});

test("syncView: a pure tab switch is a NO-OP render (reveal the cached DOM)", () => {
  assert.match(RENDER, /if \(v\.rendered === len && !v\.stale && v\.el\.childNodes\.length > 0\) return v;/);
});

test("syncView: compact / an in-place change re-renders the CURRENT window; a browse append just grows the bottom spacer", () => {
  // compact mode and any stale (tool-group toggle, off-screen update) re-render where the user is
  assert.match(RENDER, /if \(settings\.compact \|\| v\.stale\) \{[\s\S]*?renderWindowItems\(v, s, items, ws, we, working\);/);
  // browsing history away from the tail: appended events land below the window → grow the bottom spacer only
  assert.match(RENDER, /if \(!wasAtTail\) \{\s*\n\s*v\.spacerCountBot = total - \(v\.winEnd \?\? total\);/);
});

test("a new message while scrolled UP keeps the viewport put (no backwards jump)", () => {
  // appendActive: at the bottom → follow it; scrolled up → restore ANCHOR-relative after the sync (the
  // turn at the viewport top keeps its exact offset — raw scrollTop only when the anchor was evicted; the
  // user 2026-07-05, subagent report cards growing ABOVE the viewport moved the raw offset's meaning), and
  // tell syncView atBottom=stick so a compact append KEEPS winStart (content above the viewport unchanged)
  // instead of evicting the top — which (with the compact full-rebuild that resets scrollTop) was jumping
  // the view "backwards" when messages arrived (the user 2026-06-25).
  assert.match(RENDER, /const before = content\.scrollTop;/);
  assert.match(RENDER, /syncView\(activeId, stick\);/);
  assert.match(RENDER, /else if \(!\(v && restoreScrollAnchor\(content, v, anchor\)\)\) writeScroll\(content, before, "append-raw"\);/);
  // the compact branch keeps winStart on a scrolled-up append
  assert.match(RENDER, /const keepTop = wasAtTail && atBottom === false;/);
  assert.match(RENDER, /const ws = keepTop \? \(v\.winStart \?\? 0\)/);
});

test("an oversized view (window grew past the cap) re-collapses to the tail on switch", () => {
  // `!reshow &&` leads since T249: the re-collapse is a SWITCH rule, never applied to a re-show of the view on screen
  assert.match(RENDER, /if \(!reshow && !pendingAnchor && pendingAnchorT == null\s*\n?\s*&& v\.el\.querySelectorAll\("\.turn"\)\.length > WINDOW_CAP\) \{/);
  assert.match(RENDER, /v\.rendered = 0; v\.winStart = 0; v\.avgTurnH = undefined; v\.stick = true;/);
});

test("a deep-link off the current window renders a fresh window AROUND the target unit, then lands", () => {
  assert.match(RENDER, /let u = items\.findIndex\(\(it\) => it\.kind === "toolgroup" \|\| it\.kind === "noticegroup" \? it\.indices\.includes\(idx\) : it\.index === idx\);/);   // noticegroup since 2026-09-08
  assert.match(RENDER, /renderWindowItems\(v, s, items, Math\.max\(0, u - WINDOW_RADIUS\), Math\.min\(items\.length, u \+ WINDOW_RADIUS\), working\);/);
});

test("the spacer is invisible, non-interactive vertical space", () => {
  assert.match(CSS, /\.tx-spacer \{ width: 100%; pointer-events: none; \}/);
});
