// Pure slot geometry for the tab strip's live drag reorder (the drag-flap fix, 2026-08-28; the
// T127 surface). The first implementation hit-tested the LIVE layout — but inserting the
// variable-width provisional tab re-wraps the row, the tab under the cursor changes identity, the
// next hit-test lands on the other side, and the slot oscillates between distant positions while
// the cursor moves smoothly (the user's recording, frames f024–f035). The fix: the pointer is
// hit-tested against a VIRTUAL layout of the NON-dragged tabs only — widths captured once at
// dragstart, the strip's flex-wrap simulated deterministically — so the boundaries cannot move in
// response to the insert they cause. Monotone pointer motion crosses each boundary once by
// construction; no debounce, no heuristics.

export interface SlotBox {
  id: string; w: number;
  /** T264: this box STARTS A ROW — the real strip breaks the line before it (a tag group's header, or
   *  the untagged trail's boundary), so the simulation must too, even when the box would have fit. */
  br?: boolean;
}

/** Simulate the strip's wrap layout (row fill left-to-right up to `containerW`, `gapX` between
 *  tabs, uniform `rowH`; a `br` box always opens a row) over the non-dragged boxes, and return the
 *  insertion index for pointer (x, y) in the container's content space: 0..boxes.length, where
 *  boxes.length = the end. */
export function dragSlotIndex(boxes: readonly SlotBox[], containerW: number, gapX: number,
                              rowH: number, x: number, y: number): number {
  if (!boxes.length) return 0;
  // lay the boxes into rows exactly as flex-wrap would
  const rows: { start: number; mids: number[] }[] = [];
  let cx = 0, row: { start: number; mids: number[] } | null = null;
  boxes.forEach((b, i) => {
    const needsWrap = row !== null && cx > 0 && (b.br === true || cx + b.w > containerW);
    if (row === null || needsWrap) { row = { start: i, mids: [] }; rows.push(row); cx = 0; }
    row.mids.push(cx + b.w / 2);
    // A zero-width box (the untagged trail's row break, T264) fills nothing and takes no gap, so the guard above
    // still reads a row holding only such a box as EMPTY and a `br` box after it shares the row instead of opening
    // another. It used to add the gap alone: under a themed column gap (the yatharth strip's 3px seam) cx stood at 3
    // after the break, the trail's first tab (a row opener too, as the strip marked it) opened a row of its own, the
    // break sat alone on the row the pointer's y resolved the trail to, its one midpoint at 0, and every drop on the
    // untagged row fell past it to the next row's head — the user (2026-09-11) could drag a tab to that row's first
    // slot and nowhere else on it. The classic strip's gap of 0 kept cx at 0 and never showed it.
    cx += b.w ? b.w + gapX : 0;
  });
  const ri = Math.max(0, Math.min(rows.length - 1, Math.floor(y / Math.max(1, rowH))));
  const r = rows[ri];
  const within = r.mids.findIndex((m) => x < m);
  if (within >= 0) return r.start + within;
  return ri + 1 < rows.length ? rows[ri + 1].start : boxes.length;   // past the row's last tab → the next row's head, or the very end
}
