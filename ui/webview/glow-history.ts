// The overview ruler's HISTORY STRIP (T318b, the user 2026-09-10): a hovered feed card's source turns can sit
// OUTSIDE the chat's resident tail (the kernel ships only the newest WIRE_TAIL events and streams older history
// in on scroll-back or a deep link), so applyGlow finds no .turn to light and the ruler, which maps the RESIDENT
// scroll height, has nowhere to band them. The glow's group therefore carries each uuid's GLOBAL event index
// (`idx`, read from the kernel's built chat payload) and the pane marks the unloaded ones in a short strip at the
// top of the ruler, positioned by index over the unloaded prefix [0, headFrom). A click on the card still lands
// there by the existing road (scrollToAnchor fetches older chunks until the uuid is resident). This module is the
// DOM-free half, so it runs for real under node; render.ts has import-time DOM side effects.
export const HIST_H = 14;      // px: the strip's height at the top of the ruler
export const HIST_GAP = 2;     // px: the hairline between the strip and the resident scroll map
export const HIST_MIN_H = 3;   // px: a mark's minimum height, so one turn still reads

/** Fractions in [0, 1) of the unloaded prefix for the hovered uuids with NO lit row and a known global index
 *  before the resident head. A lit row is banded by the ruler proper; a uuid at or past headFrom is resident (its
 *  row is folded or off the active path, not unloaded) and stays off the strip; an unknown uuid has no place. */
export function historyMarks(uuids: readonly string[], idx: Readonly<Record<string, number>> | undefined,
                             lit: { has(u: string): boolean }, headFrom: number): number[] {
  if (!(headFrom > 0) || !idx) return [];
  const out: number[] = [];
  for (const u of uuids) {
    if (lit.has(u)) continue;
    const i = idx[u];
    if (typeof i !== "number" || !(i >= 0) || i >= headFrom) continue;
    out.push(i / headFrom);
  }
  return out.sort((a, b) => a - b);
}

/** Marks → bands inside a strip `stripH` px tall: each mark is HIST_MIN_H tall, marks that touch coalesce (a
 *  segment's run of turns is one band, as on the ruler proper), and a mark at the very end stays inside. */
export function historyBands(marks: readonly number[], stripH: number): Array<{ top: number; height: number }> {
  const bands: Array<{ top: number; height: number }> = [];
  for (const f of marks) {
    const top = Math.min(Math.max(0, f) * stripH, Math.max(0, stripH - HIST_MIN_H));
    const last = bands[bands.length - 1];
    if (last && top <= last.top + last.height + 1) last.height = Math.max(last.height, top + HIST_MIN_H - last.top);
    else bands.push({ top, height: HIST_MIN_H });
  }
  return bands;
}
