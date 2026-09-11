// The interim progress line for the chat's reveal of a far-past anchor (the user 2026-09-10, who clicked a
// distilled summary far back in a long session, waited, and asked for at least a progress bar). On the index
// wire the reveal is a fetch-until-resident loop: scrollToAnchor asks loadOlder for the chunk before the
// resident tail, chatHead prepends it, the re-land finds the anchor still further back and asks again, until
// the anchor's event is resident. The loop itself is what the one-round-trip window (T323 stage 4b) retires;
// until then, this module says how far along it is, honestly.
//
// Pure: the fraction and the words. render.ts owns the state machine (revealProgressTick) and the DOM.
import { markerLabel } from "./time-marker";

export const REVEAL_LABEL = "Loading older messages…";

/** How much of the way back to the anchor the resident history already covers: the resident span over the
 *  span from the newest resident event back to the anchor's moment. Null when a time is missing, when the
 *  anchor is not older than the newest resident event, or when the resident span already reaches the anchor's
 *  moment and the loop is still running (the fraction would read 100% while the event is not resident, which
 *  is not honest; the count says what is known then). Otherwise in [0, 1). */
export function revealFraction(newestT: number | null, oldestT: number | null, anchorT: number | null): number | null {
  if (newestT == null || oldestT == null || anchorT == null) return null;
  const span = newestT - anchorT;
  if (!(span > 0)) return null;
  const f = (newestT - oldestT) / span;
  if (!(f >= 0) || f >= 1) return null;
  return f;
}

/** The oldest and newest resident moments, by the same reading the rail uses (`epochOf`, seconds), scanning
 *  in from each end past events with no time. */
export function residentSpan<E>(events: readonly E[], epochOf: (e: E) => number | null): { oldestT: number | null; newestT: number | null } {
  let oldestT: number | null = null, newestT: number | null = null;
  for (let i = 0; i < events.length && oldestT == null; i++) oldestT = epochOf(events[i]);
  for (let i = events.length - 1; i >= 0 && newestT == null; i--) newestT = epochOf(events[i]);
  return { oldestT, newestT };
}

/** The line's detail when no honest fraction exists: how many older messages the loop has brought in so far
 *  and how far back the resident history now reaches, in the rail's own clock words (the date when the moment
 *  is not today). Empty before the first chunk lands and when no resident event carries a time. */
export function revealCountWords(loaded: number, oldestT: number | null, nowMs: number): string {
  const parts: string[] = [];
  if (loaded > 0) parts.push(`${loaded} older ${loaded === 1 ? "message" : "messages"} loaded`);
  if (oldestT != null) {
    const m = markerLabel(oldestT, null, nowMs);
    parts.push("back to " + (m.date ? m.date + " " + m.hm : m.hm));
  }
  return parts.join(" · ");
}

/** The fraction the bar paints: floored to a tenth of a percent and held below 1, so rounding can never let the bar
 *  read complete while the event is still not resident (revealFraction already yields null at 1 and above). */
export function revealShownFraction(fraction: number): number {
  return Math.min(0.999, Math.floor(fraction * 1000) / 1000);
}

/** The bar's hover words: the fraction as a whole percentage, floored and held below 100 for the same reason. */
export function revealPercentWords(fraction: number): string {
  return `${Math.min(99, Math.floor(fraction * 100))}% of the way back`;
}

/** How many of a chunk's events are messages: the turns and postal cards a reader counts as messages, not the tool
 *  atoms, thinking blocks and notices between them. */
export function messageCount<E extends { kind?: string }>(events: readonly E[]): number {
  let n = 0;
  for (const e of events) if (e.kind === "user" || e.kind === "assistant" || e.kind === "postal-service") n++;
  return n;
}
