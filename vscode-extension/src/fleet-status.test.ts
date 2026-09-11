// The status bar / needs-you notification decision core: derived from the
// kernel's {type:"feed"} frames only (no polling).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { deriveStatus, freshNeedsYou, needsYouAsks, renderStatusBar, statusTooltipLines } from "./fleet-status";

const ask = (o: any = {}) => ({
  itemId: "n1", sid: "s1", name: "alpha", text: "Which port?", column: "needs_input", live: true, ...o,
});

test("deriveStatus counts working names and distinct needs-you sessions", () => {
  const st = deriveStatus({
    type: "feed",
    working: ["alpha", "beta", "alpha"],
    asks: [ask(), ask({ itemId: "n2" }), ask({ itemId: "n3", sid: "s2", name: "beta" })],
  });
  assert.deepEqual(st, { working: 2, needsYou: 2 }); // two cards on s1 = ONE session needing you
});

test("deriveStatus ignores non-feed frames", () => {
  assert.equal(deriveStatus({ type: "session" }), null);
  assert.equal(deriveStatus(null), null);
});

test("needs-you excludes working-column cards and parked (live:false) hand-offs", () => {
  const frame = {
    type: "feed",
    asks: [ask(), ask({ itemId: "n2", column: "working" }), ask({ itemId: "n3", live: false })],
  };
  assert.deepEqual(needsYouAsks(frame).map((a) => a.itemId), ["n1"]);
});

test("freshNeedsYou baselines silently on the first frame after (re)connect", () => {
  const { seen, fresh } = freshNeedsYou(null, { type: "feed", asks: [ask()] });
  assert.deepEqual(fresh, []);
  assert.ok(seen.has("n1"));
});

test("freshNeedsYou reports only cards that appeared since the last frame", () => {
  const first = freshNeedsYou(null, { type: "feed", asks: [ask()] });
  const second = freshNeedsYou(first.seen, { type: "feed", asks: [ask(), ask({ itemId: "n2", sid: "s2" })] });
  assert.deepEqual(second.fresh.map((a) => a.itemId), ["n2"]);
});

test("a card that leaves the column and comes back is a fresh event again", () => {
  const a = freshNeedsYou(null, { type: "feed", asks: [ask()] });
  const b = freshNeedsYou(a.seen, { type: "feed", asks: [] });          // answered
  const c = freshNeedsYou(b.seen, { type: "feed", asks: [ask()] });     // re-blocked
  assert.deepEqual(c.fresh.map((x) => x.itemId), ["n1"]);
});

test("status bar face: idle / working / needs-you / offline", () => {
  assert.deepEqual(renderStatusBar(false, { working: 0, needsYou: 0 }), { text: "romp: idle", warn: false });
  assert.deepEqual(renderStatusBar(false, { working: 3, needsYou: 0 }), { text: "romp: 3 working", warn: false });
  assert.deepEqual(renderStatusBar(false, { working: 2, needsYou: 1 }),
    { text: "romp: 2 working · 1 needs you", warn: true });
  assert.deepEqual(renderStatusBar(false, { working: 0, needsYou: 2 }),
    { text: "romp: 2 need you", warn: true });
  assert.equal(renderStatusBar(true, { working: 5, needsYou: 5 }).text, "romp: offline");
  assert.equal(renderStatusBar(false, null).text, "romp: offline");
});

test("tooltip lines: working roster then one line per needs-you card, long text clipped", () => {
  const lines = statusTooltipLines({
    type: "feed",
    working: ["alpha"],
    asks: [ask({ text: "x".repeat(100) })],
  });
  assert.equal(lines[0], "Working: alpha");
  assert.ok(lines[1].startsWith("alpha: xxx"));
  assert.ok(lines[1].length <= 90);
});
