// The uuid-anchored chat wire's list operations (T323 stage 4b, proto 2), pure over a resident list of events so
// the node tests execute them for real (render.ts wires them to the frames). A proto-2 client holds a contiguous
// run of the transcript's events, not necessarily its tail: `first`/`last` are the run's oldest and newest kernel
// uuids, `headKnown` says the head has been reached (until then no count exists and the page shows none), and a
// window whose newer side has more (a loadAround deep into history) leaves the client DETACHED: the kernel sends it
// no delta until it walks forward to the tail (loadNewer, more false) or asks for a full frame.
export interface Ev { uuid?: string; key?: string; [k: string]: unknown; }

/** An event's wire KEY: its uuid, or `key` (uuid#n) when it is the second event built from one record (a text and a
 *  tool call): the uuid stays the record's for deep links, the key is what the anchors and the merge compare. */
export function keyOf(e: Ev | null | undefined): string | undefined {
  return e ? (e.key ?? e.uuid) : undefined;
}

/** The index of the event keyed `uuid` among `events`, or -1. */
export function indexOfUuid(events: readonly Ev[], uuid: string | null | undefined): number {
  if (!uuid) return -1;
  for (let i = 0; i < events.length; i++) if (keyOf(events[i]) === uuid) return i;
  return -1;
}

/** A chatTail by uuid: truncate after `afterUuid` and append. null = the anchor is not resident: a gap, ask for a full. */
export function applyTailAfter(events: Ev[], afterUuid: string | null | undefined, incoming: readonly Ev[]): Ev[] | null {
  const at = indexOfUuid(events, afterUuid);
  if (at < 0) return null;
  const out = events.slice(0, at + 1);
  for (const e of incoming) out.push(e);
  return out;
}

/** A chatHead by uuid: the reply's beforeUuid must be the resident oldest; prepend. null = stale, ignore. */
export function prependHead(events: Ev[], beforeUuid: string | null | undefined, older: readonly Ev[]): Ev[] | null {
  if (!events.length || keyOf(events[0]) !== beforeUuid) return null;
  return older.length ? older.concat(events) : events.slice();
}

/** A chatMore by uuid: the reply's afterUuid must be the resident newest; append. null = stale, ignore. */
export function appendMore(events: Ev[], afterUuid: string | null | undefined, newer: readonly Ev[]): Ev[] | null {
  if (!events.length || keyOf(events[events.length - 1]) !== afterUuid) return null;
  return newer.length ? events.concat(newer) : events.slice();
}

/** A chatWindow: MERGE when it overlaps the resident run (the union, the window's order first, the resident events
 *  it does not hold after it, in their order, so a contiguous run stays contiguous); REPLACE when it does not
 *  overlap (the reader jumped somewhere else; the old run is dropped). Returns the new list and which happened. */
export function mergeWindow(events: readonly Ev[], window: readonly Ev[]): { events: Ev[]; mode: "merge" | "replace" } {
  const inWin = new Set<string>();
  for (const e of window) { const k = keyOf(e); if (k) inWin.add(k); }
  let overlap = false;
  for (const e of events) { const k = keyOf(e); if (k && inWin.has(k)) { overlap = true; break; } }
  if (!overlap) return { events: window.slice(), mode: "replace" };
  // the window sits before, inside or after the resident run; keep transcript order: whichever run holds the
  // earliest event goes first. The window's first event resident → the window starts inside the run.
  const winFirstAt = indexOfUuid(events, keyOf(window[0]));
  const out: Ev[] = [];
  const seen = new Set<string>();
  const push = (e: Ev) => { const k = keyOf(e); if (k && seen.has(k)) return; if (k) seen.add(k); out.push(e); };
  if (winFirstAt > 0) { for (let i = 0; i < winFirstAt; i++) push(events[i]); }   // the run's part before the window
  for (const e of window) push(e);
  for (const e of events) push(e);                                                   // the run's part after the window
  return { events: out, mode: "merge" };
}

/** The history strip's label while the head is unknown: no number, ever. `total` is used only once the head is known. */
export function historyLabel(headKnown: boolean, resident: number, total: number | null): string {
  if (!headKnown || total == null) return "older history";
  const older = Math.max(0, total - resident);
  return older ? older + " older" : "";
}

/** Whether a proto-2 client is DETACHED after a chatWindow landed (round 2, item 2): a window with more after it leaves the
 *  client detached unless the kernel said the window reached the held run (`connected`), or the window merged into a run
 *  that was ATTACHED before it and whose newest event is still the live tail the page held. The merge clause never
 *  re-attaches a detached client: its heldLast is then an older window's last, not the live tail, while the kernel keeps
 *  sending it nothing. */
export function windowDetached(moreAfter: boolean, connected: boolean, wasDetached: boolean, mergeMode: "merge" | "replace",
                               heldLast: string | null | undefined, newLast: string | null | undefined): boolean {
  return !!moreAfter && !connected && !(mergeMode === "merge" && !wasDetached && heldLast != null && newLast === heldLast);
}

/** Whether a full session frame MERGES into the resident run instead of replacing it (round 2, item 3): only when it
 *  answers this client's own re-attach ask, so the pages the reader walked stay resident. A change-driven full frame (a
 *  tool output filling an earlier card, a floor move) replaces: a merge would keep the client's stale copies of the
 *  in-list events the kernel just reported changed, and no later delta refreshes them. */
export function fullFrameMerges(pendingWhy: string | null | undefined): boolean {
  return pendingWhy === "reattach";
}

/** The client's state after a chatMore landed: detached while more is after it; back at the live tail, the count is the
 *  resident run when the head is known (else none). */
export function afterMore(more: boolean, headKnown: boolean, resident: number): { detached: boolean; headTotal: number | null } {
  return { detached: !!more, headTotal: !more && headKnown ? resident : null };
}
