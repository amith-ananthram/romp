// The day-context label rides ABOVE the rail's TOP stamp (the user 2026-08-17; moved above the
// stamp 2026-08-18, with a video): whenever that stamp's day is not today, a small "Yesterday" /
// "3 days ago" sits just above it — anchored to the tracked turn's marker while it leads, to the
// first stamp below the line when nothing is tracked ("the next one"), and to the slot line once
// the sticky takes over, so the handoff lands at the same pixel and scrolling never jumps.
// Source pins (dayContext's behavior is tested in time-marker.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the label anchors to whichever stamp owns the top slot, and hands off at the same pixel", () => {
  assert.match(RENDER, /m\.dataset\.epoch = String\(epoch\);/, "markers carry their epoch for the day pass");
  assert.match(RENDER, /const anchorM = marker \|\| \(firstBelow \? firstBelow\[0\] : null\);/);
  assert.match(RENDER, /paintDay\(realLeads \? markerTop : \(firstBelow \? firstBelow\[1\] : cBottom \+ 1\)\);/,
    "a leading real stamp carries the label at its own position");
  assert.match(RENDER, /paintDay\(slotLine\);/, "the sticky carries it at the slot line — same pixel at handoff");
  // The slot line drops by the label's height when a label shows, so the label above the STICKY
  // stays inside the pane instead of bleeding into the tab bar (the 2026-08-17 first cut floated
  // it above the sticky without shifting the sticky down). Above a leading REAL stamp the room is
  // free: markers sit 10–15px below their turn's top, more than the label's ~10px height.
  assert.match(RENDER, /const slotLine = line \+ \(label \? dayH \+ 1 : 0\);/);
  assert.match(RENDER, /const realLeads = markerShown && markerTop >= slotLine;/,
    "the handoff threshold moves with the slot, so stamp and label swap on the same pixels");
  assert.match(RENDER, /stamp\.style\.top = slotLine \+ "px";/);
  assert.match(RENDER, /m\.style\.visibility = top < slotLine \+ g\.height \? "hidden" : "";/,
    "an incoming stamp hides before it can superimpose the sticky or the label riding above the slot");
  assert.match(RENDER, /if \(slotTop > cBottom\) \{ day\.style\.display = "none"; return; \}/,
    "an off-screen anchor paints nothing");
  assert.match(RENDER, /\} else day\.style\.display = "none";/, "today → the label hides, never lingers stale");
  assert.match(RENDER, /if \(!label\) return;/, "and paintDay never resurrects it");
  // One ORDERED block: text set → shown → measured → slot computed. Order is load-bearing — measuring
  // while display:none reads 0×0 and every downstream formula silently degenerates.
  assert.match(RENDER, new RegExp(
    String.raw`if \(day\.textContent !== label\) day\.textContent = label;\n` +
    String.raw`\s*day\.style\.display = "";.*\n` +
    String.raw`\s*const r = day\.getBoundingClientRect\(\); dayW = r\.width; dayH = r\.height;\n` +
    String.raw`\s*\} else day\.style\.display = "none";\n` +
    String.raw`\s*const slotLine = line \+ \(label \? dayH \+ 1 : 0\);`),
    "measure-while-visible feeds slotLine, in that order");
});

test("the label rides ABOVE the stamp and never clips at the pane's left edge", () => {
  // ABOVE (the user 2026-08-18, with a video): below the stamp, the next incoming stamp scrolled
  // straight through the label's spot and the label leapt over it at every handoff. Above it,
  // nothing crosses the label's path — and the clamp keeps it off the tab bar in every case.
  assert.match(RENDER, /day\.style\.top = Math\.max\(cTop \+ 1, slotTop - dayH - 4\) \+ "px";/);
  // Natural width, right edge on the gutter's right edge, left edge never past the pane's:
  // "2 days ago" at 0.66em is wider than the 47px gutter, and the old box pinned to the gutter's
  // left/width clipped its leading digit at the pane edge (the user 2026-08-18, with a screenshot).
  assert.match(RENDER, /day\.style\.left = Math\.max\(3, gRect!\.right - dayW \+ 1\) \+ "px";/);
  assert.doesNotMatch(RENDER, /day\.style\.width/, "no width pin — the box shrinkwraps its text");
});

test("the label is passive fixed chrome, smaller and dimmer than the stamp", () => {
  assert.match(CSS, /\.rail-day \{\s*\n\s*position: fixed; z-index: 3; pointer-events: none; white-space: nowrap; line-height: 1;/);
  assert.match(CSS, /font-size: 0\.66em; letter-spacing: 0\.03em; color: var\(--dim\); opacity: 0\.85;/,
    "context, not the time itself — smaller and dimmer than the stamp");
});

// ── executed replica of the placement decision ───────────────────────────────────────────────────
// Faithful to paintRailSticky's day pass (pinned above): slotLine = line + dayH + 1 when a label
// shows; the label rides at slotTop - dayH - 4 (clamped to cTop + 1; nudged 1px right / 3px up for
// breathing room, the user 2026-08-22), its left edge at max(3, gutterRight - labelWidth + 1); an
// anchor below the pane paints nothing. Same style as
// rail-sticky-stamp.test.ts's decideSticky: the pure math, executed.
const BUFFER = 6;
function placeDay(o: { label: string; dayW: number; dayH: number; slotTop: number;
                       cTop: number; cBottom: number; gutterRight: number }):
    { show: boolean; left?: number; top?: number; slotLine: number } {
  const line = o.cTop + BUFFER;
  const slotLine = line + (o.label ? o.dayH + 1 : 0);
  if (!o.label) return { show: false, slotLine };
  if (o.slotTop > o.cBottom) return { show: false, slotLine };
  return { show: true, slotLine,
    left: Math.max(3, o.gutterRight - o.dayW + 1),
    top: Math.max(o.cTop + 1, o.slotTop - o.dayH - 4) };
}
const G = { cTop: 100, cBottom: 700, gutterRight: 59, dayH: 9 };   // gutter per .thread's 56px pad + marker right at +3
const LINE = G.cTop + BUFFER;

test("executed: the 56px gutter fits every real label clear of the rail dots; the clamp survives as a backstop", () => {
  // the whole point of the 2026-08-22 widening: at the old 44px gutter the widest forms clamped left
  // and their tails ran into the rail dots as turns scrolled past. Dot column starts at
  // gutterRight + 3 (dot left = turn + 6, marker right = turn + 3).
  const dotLeft = G.gutterRight + 3;
  for (const [label, w] of [["2 days ago", 52], ["2 weeks ago", 57], ["Yesterday", 47]] as const) {
    const r = placeDay({ ...G, label, dayW: w, slotTop: 300 });
    assert.ok(r.left! >= 3, label + " never leaves the pane");
    assert.ok(r.left! + w <= dotLeft - 1, label + " stays clear of the dot column");
  }
  const extreme = placeDay({ ...G, label: "impossibly wide", dayW: 70, slotTop: 300 });
  assert.equal(extreme.left, 3, "the left clamp survives as the backstop for absurd widths");
});

test("executed: a label that fits keeps its right edge on the gutter's right edge, like the stamp's", () => {
  const r = placeDay({ ...G, label: "Last week", dayW: 40, slotTop: 300 });
  assert.equal(r.left! + 40, G.gutterRight + 1, "right edge sits 1px past the stamp's — the breathing-room nudge");
});

test("executed: above the stamp, and above the STICKY it stays inside the pane — no tab-bar bleed", () => {
  const marker = placeDay({ ...G, label: "Yesterday", dayW: 45, slotTop: 300 });
  assert.equal(marker.top, 300 - G.dayH - 4, "riding a real stamp: label bottom 4px above the stamp");
  const sticky = placeDay({ ...G, label: "Yesterday", dayW: 45, slotTop: LINE + G.dayH + 1 });
  assert.equal(sticky.top, LINE - 3, "riding the sticky at the slot line: 3px above the buffer line (the nudge)");
  assert.ok(sticky.top! >= G.cTop, "inside the pane — the slot shift is exactly the room the label needs");
});

test("executed: no label (today) leaves the slot line AT the line — today's layout is untouched", () => {
  assert.equal(placeDay({ ...G, label: "", dayW: 0, dayH: 0, slotTop: 300 }).slotLine, LINE);
  assert.equal(placeDay({ ...G, label: "Yesterday", dayW: 45, slotTop: 300 }).slotLine, LINE + G.dayH + 1);
});

test("executed: an anchor below the pane's bottom paints nothing", () => {
  assert.equal(placeDay({ ...G, label: "Yesterday", dayW: 45, slotTop: 701 }).show, false);
});

// T342 (the manager's review of T339): the day label read the top row's OWN epoch, so a stale echo at the top line (an
// undelivered notice the kernel merges into the last turn by its send time) said "2 days ago" between rows the divider
// walk keeps under one "Yesterday". The label now reads the WALK's day at that row: the high-water mark after the row's
// unit, stamped on the marker as data-day by the walk itself (appendItem, the normal-mode tail loop); the row's own epoch
// stays the fallback and the rail's HH:MM stays the row's own. DayWalk.pass returning the mark is executed in
// time-marker.test.ts on the two reported sequences.
test("the day label reads the walk's day at the top row (data-day), never the row's own moment (T342)", () => {
  assert.match(RENDER, /const ep = anchorM \? Number\(anchorM\.dataset\.day \|\| anchorM\.dataset\.epoch \|\| 0\) : 0;/, "the walk's day first, the row's own as the fallback");
  const st = RENDER.slice(RENDER.indexOf("function stampWalkDay("), RENDER.indexOf("function dayWalkBefore("));
  assert.match(st, /const m = node\.firstChild as HTMLElement \| null;/);
  assert.match(st, /if \(walk\.mark != null && m && m\.nodeType === 1 && m\.classList && m\.classList\.contains\("time-marker"\)\) m\.dataset\.day = String\(walk\.mark\);/, "the marker carries the mark; a divider or an untimed row is left alone");
  // stamped by both walks that paint the chat: the unit path after its exit passed, the tail loop after each row
  assert.match(RENDER, /walk\.pass\(unitExit\(s, it\)\);[^\n]*\n\s*for \(const n of nodes\) if \(!stamped\.has\(n\)\) stampWalkDay\(n, walk\);/);
  // an EXPANDED tool run: the head is timed by its first member and each row by its own, so each is stamped as the walk passes
  // it (a run spanning midnight put today's mark on yesterday's rows otherwise: the review's find); the exit stamp skips them
  const tg = RENDER.slice(RENDER.indexOf('if (it.kind === "toolgroup") {', RENDER.indexOf("function appendItem(")), RENDER.indexOf('} else if (it.kind === "noticegroup") {', RENDER.indexOf("function appendItem(")));
  assert.match(tg, /walk\.pass\(eventEpoch\(first\)\); stampWalkDay\(head, walk\); stamped\.add\(head\);/, "the head in the first member's day");
  assert.match(tg, /v\.el\.appendChild\(tag\(child\)\); adv\(i\);\s*\n\s*walk\.pass\(eventEpoch\(s\.events\[i\]\)\); stampWalkDay\(child, walk\); stamped\.add\(child\);/, "each row in its own");
  assert.match(RENDER, /const stamped = new Set<HTMLElement>\(\);/);
  assert.match(RENDER, /walk\.pass\(ep\);\s*\n\s*stampWalkDay\(node, walk\);/);
  assert.match(RENDER, /const tag = \(node: HTMLElement\): HTMLElement => \{ node\.dataset\.unit = String\(u\); nodes\.push\(node\); return node; \};/, "every node the unit appends is collected for the stamp");
  assert.match(RENDER, /m\.dataset\.epoch = String\(epoch\);/, "the row's own moment still rides the marker (deep links, hover, the fallback)");
});
