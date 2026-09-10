// LIVE compaction indicator in the chat flow (the user 2026-07-06): while the session compacts, an ANIMATED
// inline element ("Compacting context…" + the compressing teal bar) renders in the transcript — not only in
// the statusline/tab. The kernel appends kind:"compacting" BEFORE kind:"queued", so a message sent
// mid-compaction stacks BELOW it instead of clobbering it; once the boundary lands, the {kind:"compact"}
// "Context compacted" notice card it visually becomes (via the shared teal) takes over. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a live compacting event has its own ChatEvent kind, dispatched to renderCompacting", () => {
  assert.match(RENDER, /kind: "compacting"; ts\?: string; uuid\?: string/);
  assert.match(RENDER, /ev\.kind === "compacting"\) return renderCompacting\(\)/);
});

test("the compacting dispatch is checked BEFORE the done 'compact' divider (a live signal, not a boundary)", () => {
  const live = RENDER.indexOf('ev.kind === "compacting") return renderCompacting()');
  const done = RENDER.indexOf('ev.kind === "compact") return renderCompact(ev)');
  assert.ok(live > 0 && done > 0, "both dispatch lines present");
  assert.ok(live < done, "the live compacting case must precede the compact-boundary case");
});

test("renderCompacting is a slim live COMPACTION notice with the animated bar in its glyph slot", () => {
  // 2026-09-08 (the notice-vocabulary pass): the bar keeps its motion; the row is the ONE notice, teal on the
  // rail + dot + bar and the words in --fg (teal TEXT sat at 1.96:1 on cream)
  assert.match(RENDER, /function renderCompacting\(\): HTMLElement/);
  assert.match(RENDER, /el\("span", "compacting-bar"\)/);
  assert.match(RENDER, /el\("span", "compacting-bar-fill"\)/);   // the animated compressing bar
  assert.match(RENDER, /notice\(\{ src: "compaction", glyph: noticeLiveGlyph\(bar\), sev: "compact", gist: "Compacting context…", live: true,/);
  assert.match(RENDER, /cls: "turn-compacting",/);
});

test("the animated bar reuses the statusline ctx-compress motion, in the compacting teal", () => {
  assert.match(CSS, /\.compacting-bar-fill \{[^}]*animation: ctx-compress/);
  // 2026-09-08: the teal is the severity (rail + dot), never the text
  assert.match(CSS, /\.notice-sev-compact \{ --notice-rail: var\(--st-compacting-bg\)/);
  assert.doesNotMatch(CSS, /\.compacting-inline|\.compacting-text/);
  assert.match(CSS, /@keyframes ctx-compress/);   // the reused keyframe still exists
});

test("the chat bar sweeps the context colormap like the statusline/tab bars (the user 2026-07-07)", () => {
  // renderCompacting hands its fill to applyCompactSweep (which sets --cmp0..4 the ctx-compress keyframes
  // read) so the bar changes colour through the map, not a flat teal — at the SAME 3200ms as the keyframe.
  const body = RENDER.slice(RENDER.indexOf("function renderCompacting"), RENDER.indexOf("function renderReconnecting"));
  assert.match(body, /applyCompactSweep\(fill, 3200\)/);
});

// ── the compaction boundary is a DEFAULT-COLLAPSED notice card showing the model's summary (the user 2026-07-07) ──
test("the compact ChatEvent carries the model's summary text", () => {
  assert.match(RENDER, /kind: "compact";[^}]*summary\?: string/);
});

test("renderCompact routes the summary through the ONE notice builder as its folded body", () => {
  const body = RENDER.split("function renderCompact(")[1].split("\nfunction ")[0];
  assert.match(body, /const summary = \(ev\.summary \|\| ""\)\.trim\(\)/);
  assert.match(body, /body\.innerHTML = md\(summary\)/);                            // the summary, markdown-rendered
  // 2026-09-08: notice(spec) — a body only when there is a summary to reveal (flat otherwise), keyed by the boundary
  assert.match(body, /notice\(\{ src: "compaction", glyph: "compaction", sev: "compact", gist: "Context compacted",/);
  assert.match(body, /body: summary \? body : null,\s*\n\s*key: uuid \? "compact:" \+ uuid : undefined/);
});

test("the card is DEFAULT COLLAPSED via the shared keyed fold — the bespoke Set + toggle are gone", () => {
  assert.doesNotMatch(RENDER, /compactExpanded/, "the bespoke open-state Set was replaced by the shared fold");
  assert.doesNotMatch(RENDER, /function toggleCompact/, "no bespoke toggle — the notice head IS the toggle");
  // 2026-09-08: the ONE builder folds every notice through openFolds ("notice:<key>"), collapsed unless remembered open
  const nc = RENDER.split("function notice(spec: NoticeSpec)")[1].split("\nfunction ")[0];
  assert.match(nc, /applyFold\(card, "notice-open", fkey\)/, "collapsed unless the key is remembered open");
});

test("the default window opens at the last compaction boundary (pre-compaction history scrubbed)", () => {
  assert.match(RENDER, /function lastCompactUnit\(s: Session, items: DisplayItem\[\]\): number/);
  assert.match(RENDER, /if \(s\.events\[i\]\.kind === "compact"\) \{ evIdx = i; break; \}/);
});

test("the card wears the compaction TEAL as its severity — a system event in the one notice family", () => {
  // 2026-09-08: severity = rail + dot; the retired chip/variant rules are gone
  assert.match(CSS, /\.notice-sev-compact \{ --notice-rail: var\(--st-compacting-bg\); --notice-dot: var\(--st-compacting-bg\); \}/);
  assert.doesNotMatch(CSS, /\.notice-card-compact|\.notice-chip-compact/);
});
