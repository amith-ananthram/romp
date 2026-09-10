// Sending from the composer while scrolled UP must not move the scroll position at all: the message
// sends, the optimistic bubble lands below the fold, and the reader stays exactly where they were
// (the user 2026-08-30, yanked to the bottom mid-read by the old unconditional snap). Sending while
// at — or within nearBottomForSend's 80px of — the bottom keeps the old behavior: the view follows the
// new bubble (the 2026-08-09 always-reveal rule, now scoped to the tail). That band is the send's alone:
// follow mode itself reads the true bottom (T262c, atBottom). The gate is a nearBottomForSend
// read taken BEFORE appendActive lands the bubble, because the append grows scrollHeight and a
// post-append read would misclassify a tail-sitter as scrolled-up.
//
// render.ts has import-time DOM side effects → source pins + an executed replica of the decision
// (optimistic-send.test.ts / user-img-dedup.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

// ── executed replica ──────────────────────────────────────────────────────────────────────────────
// Mirrors registerOptimistic's active-tab arm + appendActive's stick rule, both pinned to source
// below so the replica can't drift silently.
type Box = { scrollHeight: number; clientHeight: number; scrollTop: number };
const nearBottomForSend = (c: Box) => c.scrollHeight - c.scrollTop - c.clientHeight < 80;   // render.ts nearBottomForSend (the send band)
const atBottom = (c: Box) => c.scrollHeight - c.scrollTop - c.clientHeight <= 2;               // render.ts atBottom (follow mode, T262c)
const maxScroll = (c: Box) => Math.max(0, c.scrollHeight - c.clientHeight);          // DOM clamps scrollTop
function sendOwnMessage(c: Box, bubbleGrowth: number): void {
  const wasAtBottom = nearBottomForSend(c);                            // measured BEFORE the append
  // appendActive: stick only when overflowing AND at the true bottom; otherwise restore the viewport
  const stick = c.scrollHeight > c.clientHeight + 2 && atBottom(c);
  const before = c.scrollTop;
  c.scrollHeight += bubbleGrowth;                                      // the bubble lands
  c.scrollTop = stick ? maxScroll(c) : before;                         // stick or anchor/before restore
  if (wasAtBottom) c.scrollTop = maxScroll(c);                         // the gated snap
}

test("scrolled-up send preserves scrollTop exactly — mid-history", () => {
  const c: Box = { scrollHeight: 5000, clientHeight: 600, scrollTop: 1000 };
  sendOwnMessage(c, 200);
  assert.equal(c.scrollTop, 1000);
});

test("scrolled-up send preserves scrollTop exactly — just above the 80px stick threshold", () => {
  const c: Box = { scrollHeight: 5000, clientHeight: 600, scrollTop: 5000 - 600 - 100 };   // 100px up
  sendOwnMessage(c, 200);
  assert.equal(c.scrollTop, 5000 - 600 - 100);
});

test("at-bottom send still follows the new bubble", () => {
  const c: Box = { scrollHeight: 5000, clientHeight: 600, scrollTop: 5000 - 600 };
  sendOwnMessage(c, 200);
  assert.equal(c.scrollTop, 5200 - 600);
});

test("within-80px send still follows — the send band treats a reader a few lines up as meaning the tail", () => {
  const c: Box = { scrollHeight: 5000, clientHeight: 600, scrollTop: 5000 - 600 - 40 };    // 40px up
  sendOwnMessage(c, 200);
  assert.equal(c.scrollTop, 5200 - 600);
});

test("a send that first overflows the pane still reveals itself — nearBottomForSend is trivially true", () => {
  const c: Box = { scrollHeight: 500, clientHeight: 600, scrollTop: 0 };
  sendOwnMessage(c, 400);   // 500 → 900: crosses the overflow boundary
  assert.equal(c.scrollTop, 900 - 600);
});

// ── source pins: the replica models the real code ─────────────────────────────────────────────────

test("registerOptimistic gates the snap on a pre-append nearBottomForSend read", () => {
  assert.match(RENDER, /const wasAtBottom = !!content && nearBottomForSend\(content\);[^\n]*\n\s*appendActive\(\);\s*\n\s*if \(content && wasAtBottom\) writeScroll\(content, content\.scrollHeight, "optimistic-send", true\);/);
  // nearBottomForSend's 80px threshold — the replica's constant
  assert.match(RENDER, /function nearBottomForSend\(c: HTMLElement\): boolean \{\s*\n\s*return c\.scrollHeight - c\.scrollTop - c\.clientHeight < 80;/);
  // appendActive's stick rule — overflow gate + the TRUE bottom (T262c), restore otherwise
  assert.match(RENDER, /const stick = content\.scrollHeight > content\.clientHeight \+ 2 && atBottom\(content\);/);
});

test("every composer-shaped send rides the same gate — staged flush and provisional adoption included", () => {
  // the staged flush releases each post of the run (one message, plus any item that goes alone) through routeUserMessage…
  assert.match(RENDER, /function flushStaged\(sid: string, typed\?: \{ text: string; cites\?: Citation\[\]; imgPaths\?: string\[\] \}\): number \{\s*\n\s*const run = stagedMsgs\.takeAll\(sid\);\s*\n\s*for \(const p of stagedPosts\(run, typed\)\) routeUserMessage\(sid, p\.text, p\.cites as Citation\[\] \| undefined, p\.imgPaths\);/);
  // …whose every branch registers the optimistic bubble (2026-08-23), so the gate covers them all
  assert.match(RENDER, /if \(goalCite\?\.itemId\) \{ [^\n]*registerOptimistic\(sid, text, imgPaths, qid\); \}/);
  assert.match(RENDER, /else if \(quoteCites\.length\) \{ [^\n]*registerOptimistic\(sid, body, imgPaths, qid\); \}/);
  assert.match(RENDER, /else \{ [^\n]*registerOptimistic\(sid, text, imgPaths, qid\); \}/);
  // provisional adoption re-sends through registerOptimistic too
  assert.match(RENDER, /registerOptimistic\(realId, text, undefined, qid\);/);
});
