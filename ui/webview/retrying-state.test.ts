// The 'retrying' chip state (api 2026-06-23): a live SDK session stalled on an API rate-limit/overload
// auto-retry publishes status.state='retrying'. It renders as a SOFT 'blocked on the API' — amber/orange,
// distinct from working-yellow and blocked-red — on BOTH the chat (chip + tab) and the timeline lane badge.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("chat: 'retrying' is a ChipState with an 'API retrying…' label, an amber chip, and a tab ring", () => {
  assert.match(fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "status-chip.ts"), "utf8"), /type ChipState =[^;]*\| "retrying"/);   // the union lives in status-chip.ts since T322b
  assert.match(fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "status-chip.ts"), "utf8"), /retrying: "API retrying…"/);   // CHIP_LABEL lives in status-chip.ts since T322b
  // the state → class rule lives in tab-state.ts since tab groups (2026-09-04); render.ts wears its result
  const S = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "tab-state.ts"), "utf8");
  assert.match(S, /if \(st === "retrying"\) return "tab-retrying";/);
  assert.match(RENDER, /const stateCls = tabStateClass\(s\.status\);\s*\n\s*if \(stateCls\) tab\.classList\.add\(stateCls\);/);
  // amber chip, distinct from working-yellow / blocked-red
  // 2026-09-08: the amber is a TOKEN now (--st-retrying-bg/-fg, both themes) — it sat raw in seven rules
  assert.match(CSS, /--st-retrying-bg: #e67e22; --st-retrying-fg: #2a1500;/);
  assert.match(CSS, /\.chip-retrying \{ background: var\(--st-retrying-bg\); color: var\(--st-retrying-fg\); \}/);
  // amber dashed tab ring (same dashed treatment as awaiting, but amber, no fill)
  assert.match(CSS, /\.tab\.tab-retrying \{ --state: var\(--st-retrying-bg\); \}/);
  assert.match(CSS, /\.tab\.tab-awaiting, \.tab\.tab-blocked, \.tab\.tab-retrying \{ outline: 2px dashed/);
});

test("timeline: a retrying lane shows an amber Retrying badge (its own BADGE kind, not red attention)", () => {
  assert.match(TL, /else if \(s\.state === 'retrying'\) m = \{ label: 'Retrying', kind: 'retrying' \}/);
  assert.match(TL, /retrying: \{ bg: '#e67e22', fg: '#2a1500' \}/);
});
