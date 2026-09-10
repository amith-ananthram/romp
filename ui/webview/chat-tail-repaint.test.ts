// The chat's ACTIVE-tab repaint after a tail is coalesced per animation frame (2026-09-07). On the thaw of a
// frozen tab the queued tails replay as a burst; before this, each tail ran appendActive() — a forced layout
// and a repaint of the same suffix — inside one long task (796 ms measured on a 45 s freeze). Now every tail
// still APPLIES at once and the paint happens once per frame from the earliest changed point. Source pins
// (render.ts has import-time DOM side effects, so no test imports it — the repo's standing pattern).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const tail = RENDER.slice(RENDER.indexOf("function chatTail(msg: any) {"), RENDER.indexOf("function chatHead("));

test("a tail for the active tab schedules ONE repaint per frame instead of painting inline", () => {
  assert.match(tail, /v\.rendered = Math\.min\(v\.rendered, from\);/, "the state still lowers the repaint point at once");
  assert.match(tail, /if \(shrank\) v\.stale = true;/);
  const active = tail.slice(tail.indexOf("if (msg.id === activeId) {"), tail.indexOf("} else {"));
  assert.match(active, /scheduleAppendActive\(\);/);
  assert.doesNotMatch(active, /\bappendActive\(\);/, "no inline appendActive on the tail path any more");
  assert.doesNotMatch(active, /\brenderLedger\(\);/, "the ledger paint rides the scheduled frame");
  // (2026-09-10: the render moved just past the active/inactive branch, as awaitChanged(sid) — the box for the
  // active session, and the subagent viewer's header when the active tab is a viewer into this one — so a viewer
  // tab, for which msg.id !== activeId, reaches it too; still keyed on THIS frame's status change)
  assert.match(tail, /\n  \}\n  if \(awaitKey\(s\.status\) !== before\) awaitChanged\(msg\.id\);/, "the awaited-agents box still keys on the SAME frame's status change");
  assert.doesNotMatch(active, /renderBgTasks\(\)/, "…and no inline box render on the active branch");
});

test("the scheduler mirrors scheduleRenderTabs: one pending frame, then appendActive + renderLedger", () => {
  assert.match(RENDER, /let appendRaf: number \| null = null;\nfunction scheduleAppendActive\(\): void \{\n  if \(appendRaf != null\) return;\n  appendRaf = requestAnimationFrame\(\(\) => \{ appendRaf = null; appendActive\(\); renderLedger\(\); \}\);\n\}/);
});

test("the full-session paths keep their synchronous paint (one frame each)", () => {
  const upsert = RENDER.slice(RENDER.indexOf("function upsert("), RENDER.indexOf("function chatTail(msg: any) {"));
  assert.match(upsert, /appendActive\(\);/, "a full frame paints at once as before");
});

// Executed replica of the coalescing: N tails in one task → one paint, from the LOWEST changed point.
test("replica: five tails in one task paint once, from the earliest changed index", () => {
  let rafCb: (() => void) | null = null;
  const raf = (cb: () => void) => { rafCb = cb; return 1; };
  let appendRaf: number | null = null; let paints: number[] = []; const v = { rendered: 100 };
  const appendActive = () => { paints.push(v.rendered); v.rendered = 120; };
  const schedule = () => { if (appendRaf != null) return; appendRaf = raf(() => { appendRaf = null; appendActive(); }); };
  for (const from of [90, 95, 70, 110, 80]) { v.rendered = Math.min(v.rendered, from); schedule(); }
  assert.equal(paints.length, 0, "nothing painted inside the task");
  rafCb!();
  assert.deepEqual(paints, [70], "one paint, from the lowest point any tail touched");
  assert.equal(appendRaf, null, "the frame is spent; the next tail schedules a new one");
});
