// A context compaction renders as a NOTICE CARD in romp's system-event family (renderCompact → noticeCard,
// variant "compact") — the same boxed, chip-headed, default-collapsed treatment as the agent/romp/reminder
// notices, distinguished only by the COMPACTION TEAL accent. The head says WHY at a glance (trigger + token
// before→after); the model's summary is the collapsible body — never the raw Claude payload or hook stdout
// (both dropped kernel-side). The kernel emits a {kind:"compact"} event carrying the boundary metadata; this
// pins the card render + the token formatter (the user 2026-07-01, reworked 2026-07-07). Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("renderCompact builds a COMPACTION notice dispatched off kind:'compact'", () => {
  // 2026-09-08 (the notice-vocabulary pass): the ONE builder, notice(spec) — source "compaction", the chevrons-in
  // glyph, the compact severity on the rail; the gist is the plain fact, the meta carries trigger + token win
  assert.match(RENDER, /ev\.kind === "compact"\) return renderCompact\(ev\)/);
  assert.match(RENDER, /notice\(\{ src: "compaction", glyph: "compaction", sev: "compact", gist: "Context compacted",/);
  assert.match(RENDER, /kind: "compact"; trigger\?: string; preTokens\?: number; postTokens\?: number;/);
});

test("the head's meta names the trigger + the token win (before → after)", () => {
  assert.match(RENDER, /if \(ev\.trigger === "auto"\) bits\.push\("auto"\);/);
  assert.match(RENDER, /else if \(ev\.trigger === "manual"\) bits\.push\("manual"\);/);
  assert.match(RENDER, /\$\{compactTokens\(ev\.preTokens\)\} → \$\{compactTokens\(ev\.postTokens\)\}/);
  // 2026-09-08: the bits ride the builder's META slot (0.82em, tabular), no longer glued onto the head string
  assert.match(RENDER, /meta: bits\.join\(" · "\) \|\| undefined, body: summary \? body : null,/);
});

test("it wears the compaction TEAL as its severity — rail + dot, never text (2026-09-08)", () => {
  // the teal sits under 3:1 as text on the light theme's cream; the severity classes put it on the rail and dot only
  assert.match(CSS, /\.notice-sev-compact \{ --notice-rail: var\(--st-compacting-bg\); --notice-dot: var\(--st-compacting-bg\); \}/);
  assert.doesNotMatch(CSS, /\.notice-card-compact|\.notice-chip-compact/);
});

// executed: mirror compactTokens' logic to guard its intent (compact + human-readable)
test("the token formatter is compact + human-readable", () => {
  const compactTokens = (n: number): string => {
    if (n < 1000) return String(n);
    const k = n / 1000;
    return (k < 10 ? k.toFixed(1).replace(/\.0$/, "") : Math.round(k)) + "k";
  };
  assert.equal(compactTokens(900), "900");
  assert.equal(compactTokens(6514), "6.5k");
  assert.equal(compactTokens(795232), "795k");
  assert.equal(compactTokens(9000), "9k");
});
