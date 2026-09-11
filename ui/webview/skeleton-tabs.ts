// Skeleton tabs (2026-09-07): the client half of the reconnect diet. When a pane redials after its socket
// died (a laptop lid, a long freeze, a network change), the kernel used to serve the fresh socket as a client
// that holds nothing: seventeen full `session` frames, ~9 MB, for ONE tab on screen. Now a redial declares
// itself (`reconnect=1`), the kernel sends the tab strip with a `skeleton` list (every background tab it
// is withholding, cheapest first), the ACTIVE tab in full, and a ~400 B `status` frame per withheld tab. This
// module owns the client's copy of that set — which tabs are resting, what the kernel last said about each,
// and which one to fetch next in idle — as a pure, DOM-free state machine (the prebuild.ts / tab-meta.ts
// pattern: executable in node, so the policy is pinned by skeleton-tabs.test.ts while render.ts only wires it).
// A redial declares itself only once the kernel's caps frame has answered the bundle's ready; before that, with
// the ready still queued for the open, or after a socket that died before its caps frame came back, the shim dials
// as a fresh page and the kernel sends everything whole (2026-09-10).
//
// The page HELD every session before the outage, and render.ts deliberately keeps those `sessions` entries
// (the eventual full then takes upsert's append path, so the DOM and the reader's scroll survive). What a
// skeleton id must never do is DISPLAY that stale copy as current — so render.ts asks this set before every
// active-tab display path (`liveSession`) and before drawing a tab (`renderKind`).

export interface SkeletonState {
  ids: Set<string>;            // the tabs this page holds no CURRENT copy of (the kernel is withholding them)
  order: string[];             // the kernel's list order — ascending transcript size, the prefetch order
  status: Map<string, any>;    // the last kernel-sent status per skeleton id (the chip reads ONLY this)
  loaded: Set<string>;         // ids whose full `session` frame arrived on THIS socket (see applyTabOrderSkeleton)
}

export function newSkeletonState(): SkeletonState {
  return { ids: new Set(), order: [], status: new Map(), loaded: new Set() };
}

/** A tabOrder frame landed. An ARRAY is the kernel's authoritative set → REPLACE (a second reconnect while
 *  skeletons are outstanding re-lists tabs that had loaded — they are stale again, the honest state; once a set
 *  has existed the kernel attaches the key on EVERY strip, an empty list after it empties). NOT an array (an
 *  older kernel, which never sends the key) → KEEP what we hold, pruned to the ids still on the strip. Status entries for ids no longer held are dropped. Returns whether anything
 *  changed, so the caller can key a repaint / re-show on it.
 *
 *  One exception to "array = authoritative": an id whose FULL frame already arrived on this socket is never
 *  re-adopted from an array. The kernel releases a sid the moment it sends its full and enqueues both under
 *  one lock, so a tabOrder that still names X can only be AHEAD of X's full on the wire, never behind it — a
 *  list naming X after X's full is therefore a stale relay, not new information. The federation merge is
 *  exactly such a relay: it keeps a per-host copy of the list and re-attaches it to every merged strip, and
 *  a merge can re-present a copy taken ahead of the full that released the last id (review find 2026-09-08:
 *  the kernel does send an empty list once the set empties, so the copy is eventually told; the filter is
 *  about ORDER, not about a missing key). Without this filter the last skeleton on the strip re-skeletoned on every push, asked for its full, loaded,
 *  and re-skeletoned again — a flap the user's eye follows forever. `onSocketUp` clears the record: a new
 *  socket's list IS new information about every tab. */
export function applyTabOrderSkeleton(st: SkeletonState, skeleton: unknown, order: readonly string[]): boolean {
  const before = st.ids;
  const beforeOrder = st.order;
  let next: string[];
  const keep = new Set(order);   // the strip is the authority on what exists: an id it does not list is gone
  if (Array.isArray(skeleton)) {
    const seen = new Set<string>();
    next = [];
    for (const id of skeleton) {
      // …so a listed skeleton the order lacks (a session that ended before it loaded) is never adopted — the idle
      // prefetch would otherwise ask the kernel for a session it can no longer send (review find 2026-09-07)
      if (typeof id === "string" && id && keep.has(id) && !seen.has(id) && !st.loaded.has(id)) { seen.add(id); next.push(id); }
    }
  } else {
    next = beforeOrder.filter((id) => keep.has(id) && before.has(id));
  }
  st.ids = new Set(next);
  st.order = next;
  for (const id of Array.from(st.status.keys())) if (!st.ids.has(id)) st.status.delete(id);
  if (before.size !== st.ids.size) return true;
  for (let i = 0; i < next.length; i++) if (beforeOrder[i] !== next[i]) return true;
  for (const id of next) if (!before.has(id)) return true;
  return false;
}

/** A {type:"status"} frame. For a skeleton id the status is STORED for the chip and the caller schedules one
 *  strip repaint; for any other id the caller runs today's path (apply to the session, or the no-base repair —
 *  which for a skeleton id would ask for the full and defeat the whole set within a cycle). */
export function onStatus(st: SkeletonState, id: string, status: unknown): "skeleton" | "not-skeleton" {
  if (!st.ids.has(id)) return "not-skeleton";
  st.status.set(id, status);
  return "skeleton";
}

/** A {type:"status"} frame for an id the set does not list (yet) and the page holds NO session for. The kernel sends a
 *  status frame for a sid it holds as a skeleton and for no other (_send_chat_or_status), so such a frame is a skeleton
 *  tab's whose strip this page has not applied: the pane shim's dispatch FIFO carries a newer whole-state frame (a
 *  second tabOrder) to the END of the burst, so the statuses that arrived between two strips are delivered ahead of
 *  the strip that names their set (a later chat column's open sends two: the pusher cycle its handshake woke and the
 *  ready arm's connect push). Held for that strip: applyTabOrderSkeleton keeps the entry when the array lists the id
 *  and drops it when it does not, and the chip reads it only for a skeleton id. Never the no-base ask, which asked for
 *  every withheld tab's full and loaded the whole board into a column opened as a view of one session (2026-09-11). */
export function holdStatus(st: SkeletonState, id: string, status: unknown): void {
  st.status.set(id, status);
}

/** A full `session` frame landed (upsert): the id is loaded, its skeleton entry and stored status go.
 *  Returns whether it WAS held — a just-loaded skeleton that is the active tab must re-show its view, not
 *  append to the hidden one. Also records the id as loaded on this socket (see applyTabOrderSkeleton). */
export function onFull(st: SkeletonState, id: string): boolean {
  st.loaded.add(id);
  const held = st.ids.delete(id);
  if (!held) return false;
  st.order = st.order.filter((x) => x !== id);
  st.status.delete(id);
  return true;
}

/** The tab left the strip (a close, the kernel's omission, a host drop): nothing to load any more. */
export function onDismiss(st: SkeletonState, id: string): void {
  // its view and session left the page with the tab, so it is no longer LOADED here: a later re-listing (a host
  // re-attach, a relay redial) names it a skeleton again and the pane asks for its frame — the way the unfocused
  // pane gets the session the user was on back (T357); before, a once-loaded id could never be a skeleton again on
  // the same socket, so the re-listed tab sat with an "opening…" loader and no ask
  st.loaded.delete(id);
  if (!st.ids.delete(id)) return;
  st.order = st.order.filter((x) => x !== id);
  st.status.delete(id);
}

/** A new socket opened: every earlier full was delivered on a socket that is gone, so the next list the
 *  kernel sends may legitimately re-list those tabs (they are stale after the outage). */
export function onSocketUp(st: SkeletonState): void {
  st.loaded.clear();
}

/** The one skeleton to fetch in this idle callback, or null. Null while the page is hidden (bytes and work
 *  nobody sees — the very regime this exists to spare), while ANY skeleton id is already in flight (one at a
 *  time: a 1 MB full ahead of the active tab's 2 KB tail on a slow link delays that tail; one bounds it), and
 *  when nothing is left. Otherwise the first id of the kernel's order still held, not the active tab (its own
 *  click path asks), and in the current view (a view-hidden tab loads on click instead). */
export function nextPrefetch(st: SkeletonState, activeId: string | null, inFlight: ReadonlySet<string>,
                             hidden: boolean, inView: (id: string) => boolean): string | null {
  if (hidden) return null;
  for (const id of st.ids) if (inFlight.has(id)) return null;
  for (const id of st.order) {
    if (!st.ids.has(id) || id === activeId) continue;
    if (inView(id)) return id;
  }
  return null;
}

/** How the strip draws a tab. A skeleton id draws as a skeleton even when a (stale) session entry exists —
 *  that entry is kept for the eventual append, never used to paint. A placeholder is "the kernel is building
 *  this, it is coming" (swirl); a skeleton is "resting, loads on your click" (honest dot, muted label). */
export function renderKind(st: SkeletonState, id: string, hasSession: boolean): "skeleton" | "placeholder" | "loaded" {
  if (st.ids.has(id)) return "skeleton";
  return hasSession ? "loaded" : "placeholder";
}
