// Executable geometry for the drag-flap fix (2026-08-28): the slot the provisional tab lands in
// is computed against a VIRTUAL wrap layout of the non-dragged tabs — boundaries that cannot move
// in response to the insert they cause, so monotone pointer motion gives monotone slots and the
// oscillation in the user's recording is impossible by construction.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { dragSlotIndex, type SlotBox } from "./dragslot";

const boxes = (...w: number[]): SlotBox[] => w.map((x, i) => ({ id: "t" + i, w: x }));

test("a smooth leftward sweep crosses each boundary once — slots are monotone, never oscillating", () => {
  // variable widths on a wrapping strip: 3 rows at 300px
  const b = boxes(120, 90, 140, 60, 110, 100, 80);
  let prev = Infinity;
  for (let x = 299; x >= 0; x -= 3) {
    const idx = dragSlotIndex(b, 300, 0, 30, x, 45);   // sweeping row 2 (y=45, rowH=30)
    assert.ok(idx <= prev, `monotone under monotone motion (x=${x}: ${idx} > ${prev})`);
    prev = idx;
  }
});

test("rows resolve by y band; within a row the midpoints decide", () => {
  const b = boxes(100, 100, 100, 100);   // 2 per 250px row
  assert.equal(dragSlotIndex(b, 250, 0, 30, 40, 10), 0, "row 1, left of t0's midpoint");
  assert.equal(dragSlotIndex(b, 250, 0, 30, 60, 10), 1, "row 1, past t0's midpoint");
  assert.equal(dragSlotIndex(b, 250, 0, 30, 40, 40), 2, "row 2 by y band");
  assert.equal(dragSlotIndex(b, 250, 0, 30, 160, 40), 4, "past row 2's last midpoint → the end");
});

test("past a row's tabs lands at the next row's head; beyond every row lands at the end", () => {
  const b = boxes(200, 200, 200);        // one per 250px row
  assert.equal(dragSlotIndex(b, 250, 0, 30, 240, 5), 1, "right of row 1's tab → row 2's head");
  assert.equal(dragSlotIndex(b, 250, 0, 30, 99, 900), 2, "y clamps to the last row");
  assert.equal(dragSlotIndex(b, 250, 0, 30, 240, 900), 3, "…and past its midline is the very end");
});

test("the gap participates in the layout (the Yatharth strip's 3px seam)", () => {
  const b = boxes(120, 120);             // 120+3+120 = 243 > 240 → wraps
  assert.equal(dragSlotIndex(b, 240, 3, 30, 10, 40), 1, "the second tab wrapped to row 2");
});

test("degenerate inputs stay sane", () => {
  assert.equal(dragSlotIndex([], 300, 0, 30, 50, 50), 0);
  assert.equal(dragSlotIndex(boxes(400), 300, 0, 30, 10, -20), 0, "negative y clamps to row 0");
});

test("a `br` box opens a row even when it would have fit — the line-per-group strip's headers (T264)", () => {
  // T264 (the user 2026-09-08): every tag group starts on its own line, so the real strip breaks
  // before each header whatever the remaining width; the simulation must wrap at the same places or
  // the pointer's slot would read against rows the strip does not have
  const boxes = [{ id: "a", w: 50 }, { id: "b", w: 50 }, { id: "\0head:api", w: 40, br: true }, { id: "c", w: 50 }];
  // row 0: a, b (100 of 400 used — b's row had room for the header, but the header opens row 1)
  assert.equal(dragSlotIndex(boxes, 400, 0, 20, 10, 5), 0, "row 0, left of a");
  assert.equal(dragSlotIndex(boxes, 400, 0, 20, 60, 5), 1, "row 0, between a and b");
  assert.equal(dragSlotIndex(boxes, 400, 0, 20, 300, 5), 2, "row 0, past b: the header's slot (the end of row 0)");
  assert.equal(dragSlotIndex(boxes, 400, 0, 20, 10, 25), 2, "row 1, left of the header: the same slot");
  assert.equal(dragSlotIndex(boxes, 400, 0, 20, 60, 25), 3, "row 1, between the header and c");
  assert.equal(dragSlotIndex(boxes, 400, 0, 20, 300, 25), 4, "row 1, past c: the end");
  // a zero-width br box (the untagged trail's break) is a row opener too: the slot past the previous
  // row's last tab and the slot before the trail's first tab stay two distinct slots
  const trail = [{ id: "a", w: 50 }, { id: "\0sep", w: 0, br: true }, { id: "c", w: 50 }];
  assert.equal(dragSlotIndex(trail, 400, 4, 20, 300, 5), 1, "past a: before the break (the end of a's row)");
  assert.equal(dragSlotIndex(trail, 400, 4, 20, 10, 25), 2, "row 1, left of c's middle: the head of the trail");
  // br on the FIRST box is a no-op (nothing to break from)
  assert.equal(dragSlotIndex([{ id: "\0head:web", w: 40, br: true }, { id: "a", w: 50 }], 400, 0, 20, 100, 5), 2);
});

test("a `br` box right after the zero-width break shares the break's row under a themed gap (the user 2026-09-11)", () => {
  // The strip's dragover used to mark the box AFTER any break as a row opener too, so the untagged trail's first TAB wore
  // `br` beside the break's own (it marks only a header after a break now, but the simulation must hold for two openers
  // in a row regardless). The break is a row opener with no width, and the guard that keeps a `br` box from opening an
  // EMPTY row reads the row's fill so far (`cx > 0`) — a fill the zero-width break used to add the column gap to. The
  // classic strip's gap is 0 and the guard held; the yatharth strip's is 3px (styles.css `body.chat-theme-yatharth #tabs
  // { gap: 0 3px }`), the fill was 3 after the break, the first tab opened a row of its own, and the break sat alone on
  // the row the pointer's y resolves the trail to, with one midpoint at 0: every x on the trail's row was past it and the
  // slot fell to the next row's head, the trail's first tab. The user (2026-09-11) could drag a trail tab to the first
  // slot and nowhere else. The boxes are the grouped strip's own: a header row (head, three tabs), the break, the trail
  // (its first tab br, as the strip marked it), gap 3, one tab row high.
  const strip = (gap: number) => [{ id: "\0head:infra", w: 81 }, { id: "g1", w: 86 }, { id: "g2", w: 70 }, { id: "g3", w: 62 },
                                  { id: "\0sep", w: 0, br: true }, { id: "u1", w: 89, br: true }, { id: "u2", w: 90 }, { id: "u3", w: 86 }, { id: "u4", w: 88 }];
  // the pointer on the trail's row (y inside the second row of 32px), over the right part of u3 (u1 at 3, u2 at 96, u3 at 189..275)
  assert.equal(dragSlotIndex(strip(0), 1884, 0, 32.1, 249, 48), 8, "classic (gap 0): after u3, the slot before u4");
  assert.equal(dragSlotIndex(strip(3), 1884, 3, 32.1, 249, 48), 8, "yatharth (gap 3): the same slot — not the trail's head");
  // …and the left part of u2 is the slot before u2 in both
  assert.equal(dragSlotIndex(strip(0), 1884, 0, 32.1, 110, 48), 6);
  assert.equal(dragSlotIndex(strip(3), 1884, 3, 32.1, 110, 48), 6, "gap 3: before u2, not the trail's head");
});
