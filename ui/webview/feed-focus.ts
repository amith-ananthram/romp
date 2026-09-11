// The FOCUSED SESSION section's pure half (T347, the user 2026-09-11, who wanted the focused session's cards
// on top of the feed): WHICH of the board's entries the section shows. render() builds its three column
// buckets once; this picks, per column, the entries whose session is the chat's focused one, in the column's
// own order, and drops the by-session run headers — a section that shows ONE session needs no header between
// runs, its name is the section's own head. No DOM, no state: node --test runs it directly
// (feed-focus-section.test.ts), and feed.ts feeds it the real buckets.
import { FEED_COLUMNS, type FeedColumn } from "./feed-view-state";

/** An entry as the pick reads it: its kind, so run headers ("sess") can be dropped. Structural, so the
 *  tests pass plain objects and feed.ts passes its Entry union. */
export interface FocusEntry { kind: string }

/** The focused session's entries per column. `sidOf` names each entry's session. A null `sid` (no tab has
 *  focus in the chat) yields three empty columns, as does a session with no cards; each column keeps the
 *  order it arrived in, and the entries are the SAME objects (no copies), so the caller's gates hold. The
 *  input buckets are never touched. */
export function focusedEntries<E extends FocusEntry>(
  buckets: Record<FeedColumn, E[]>, sid: string | null, sidOf: (e: E) => string,
): Record<FeedColumn, E[]> {
  const out = { asks: [], needsInput: [], completed: [] } as Record<FeedColumn, E[]>;
  if (!sid) return out;
  for (const col of FEED_COLUMNS) out[col] = buckets[col].filter((e) => e.kind !== "sess" && sidOf(e) === sid);
  return out;
}

/** How many CARDS the picked entries hold across the three columns — the board's one counting rule
 *  (`cards` is feed.ts entryCards: a turn-group is worth its members). Zero is the "<name> has no cards"
 *  state. */
export function focusedCardCount<E>(entries: Record<FeedColumn, E[]>, cards: (e: E) => number): number {
  let n = 0;
  for (const col of FEED_COLUMNS) for (const e of entries[col]) n += cards(e);
  return n;
}
