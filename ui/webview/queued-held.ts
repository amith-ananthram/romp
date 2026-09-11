// A kernel queued copy that LEFT the queue frame before its landed record arrived (T262i, the user 2026-09-08):
// the CLI takes a copy off the queue (a romp mail card, a nudge, a follow-up — the T243 gray card in the tail
// group) and later writes its record, and the two facts reach the pane in different pushes — the kernel's queue
// state first, the transcript read later. Between them the tail is SHORTER by the card, the browser clamps a
// reader within that height of the bottom, and the landed card then grows the tail back below them: a card above
// the bottom, follow mode off, the jump chip shown. So a vanished copy is HELD in place, marked landing, until the
// atom that carries its identity arrives (T252c: the queued copy's `qid` is the landed atom's `qid`, or one of its
// `qids`), and the atom then takes the copy's slot in the same frame. A copy with no id (an older kernel
// route, a copy the backend queued itself) is held by TEXT for the one push it vanished on, and dropped at the
// next push that carries the queue if nothing claimed it — never a phantom. A held identified copy is dropped the
// moment a LATER landing shows the CLI has passed it (the queue is first-in-first-out: had the copy landed, its
// record would precede that one), or when the kernel lists it queued again (it came back; nothing to hold).
// Pure and DOM-free so node --test executes it; render.ts owns the per-session memory and the splice.

export type HeldCopy = {
  md: string;
  qid?: string;
  qts?: number;
  romp?: boolean;
  rompSystem?: boolean;
  rompAuto?: boolean;
  followUp?: boolean;
  goal?: string;
  fuCtx?: string;
  imgPaths?: string[];
  since: string | null;   // the uuid of the last kernel event on the push the copy vanished: a landing AFTER it that is
                          //   not this copy's is a later landing (the CLI passed this copy: drop the hold)
  pushes: number;         // pushes the copy has been held for (an id-less copy is dropped past the first)
};

export type HeldEvent = { kind: string; uuid?: string; md?: string; qid?: string; qids?: (string | null)[]; blocks?: string[]; undelivered?: boolean };
export type HeldQueued = { md?: string; qid?: string; qts?: number; hiddenByPending?: boolean; optimistic?: boolean; landing?: boolean; romp?: boolean; rompSystem?: boolean; rompAuto?: boolean; followUp?: boolean; goal?: string; fuCtx?: string; imgPaths?: string[] };

const sameText = (a: string, b: string): boolean => a.trim() === b.trim();
const isEcho = (u?: string): boolean => !!u && u.startsWith("echo:");
const isOptimistic = (u?: string): boolean => !!u && u.startsWith("optimistic:");

/** Whether a landed user event (a real record, never the kernel's echo nor our own bubble) carries this copy — by
 *  identity when the copy has one, by text otherwise (its md, or one of its blocks). */
export function landsCopy(e: HeldEvent, c: { md: string; qid?: string }): boolean {
  if (e.kind !== "user" || isEcho(e.uuid) || isOptimistic(e.uuid)) return false;
  if (c.qid) return e.qid === c.qid || (Array.isArray(e.qids) && e.qids.includes(c.qid));
  if (typeof e.md === "string" && sameText(e.md, c.md)) return true;
  return Array.isArray(e.blocks) && e.blocks.some((b) => typeof b === "string" && sameText(b, c.md));
}

/** The uuid of the last KERNEL event that carries one (not our own bubble, not a queued group, not a uuid-less
 *  compacting/clearing marker), or null: the anchor a hold is judged against. */
export function lastKernelUuid(events: HeldEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.kind === "queued" || !e.uuid || isOptimistic(e.uuid)) continue;
    return e.uuid;
  }
  return null;
}

/** The per-session memory between pushes: the kernel's queued copies on the previous push and the anchor of
 *  that push (its last kernel uuid) — a copy that vanished on THIS push is judged against landings after the
 *  PREVIOUS push's tail, so a copy that vanishes and lands in one push is released, never held. */
export type HeldMemory = { prev: HeldQueued[]; anchor: string | null; held: HeldCopy[] };

/** What this push knows besides its events: the copies the user cancelled here (the ✕: the kernel will drop
 *  them and nothing lands — never held), and whether the session has SETTLED (not working: a copy the queue no
 *  longer lists on a settled session was cancelled or dropped, since a taken copy starts a turn). */
export type HeldContext = { cancelled?: (c: { md: string; qid?: string }) => boolean; settled?: boolean };

/** One push's decision. `prev` = the kernel's queued copies on the previous push (its tail group, our own bubble
 *  and hidden copies excluded); `held` = the copies held so far; `events` = this push's KERNEL events (our
 *  injections stripped); `cur` = this push's queued copies. Returns the copies to keep holding — each rendered
 *  in the tail group marked `landing` — and the previous-copies memory for the next push. */
export function reconcileHeld(mem: HeldMemory, events: HeldEvent[], cur: HeldQueued[], ctx: HeldContext = {}): HeldMemory {
  const kernelCopies = cur.filter((t) => typeof t.md === "string" && !t.optimistic && !t.landing);
  const prevCopies = mem.prev.filter((p) => typeof p.md === "string" && !p.optimistic && !p.landing && !p.hiddenByPending);
  // copies are counted per identity, else per TEXT: two identical id-less copies with one taken leave one listed,
  // and the one that left is the one to hold; a key listed MORE than on the previous push has a held copy back
  const keyOf = (c: { md?: string; qid?: string }): string => c.qid || "text:" + (c.md as string).trim();
  const countBy = (list: { md?: string; qid?: string }[]): Map<string, number> => {
    const m = new Map<string, number>();
    for (const c of list) m.set(keyOf(c), (m.get(keyOf(c)) || 0) + 1);
    return m;
  };
  const prevN = countBy(prevCopies), curN = countBy(kernelCopies);
  const candidates: HeldCopy[] = [];
  const keys = new Set<string>([...prevN.keys(), ...mem.held.map(keyOf)]);
  for (const k of keys) {
    const carried = mem.held.filter((h) => keyOf(h) === k).map((h) => ({ ...h, pushes: h.pushes + 1 }));
    const relisted = Math.max(0, (curN.get(k) || 0) - (prevN.get(k) || 0));   // came back to the queue: not held
    candidates.push(...carried.slice(relisted));
    const vanished = Math.max(0, (prevN.get(k) || 0) - (curN.get(k) || 0));
    for (const p of prevCopies.filter((c) => keyOf(c) === k).slice(0, vanished))
      candidates.push({ md: p.md as string, qid: p.qid, qts: p.qts, romp: p.romp, rompSystem: p.rompSystem, rompAuto: p.rompAuto,
                        followUp: p.followUp, goal: p.goal, fuCtx: p.fuCtx, imgPaths: p.imgPaths, since: mem.anchor, pushes: 0 });
  }
  // the landings after each candidate's anchor; a landing that RELEASES a held copy is that copy's, not a "later"
  // one for its siblings (two copies taken at one boundary land one record at a time)
  const afterOf = (c: HeldCopy): HeldEvent[] => {
    const from = c.since === null ? -1 : events.findIndex((e) => e.uuid === c.since);
    return c.since === null || from < 0 ? events : events.slice(from + 1);
  };
  const anchorGone = (c: HeldCopy): boolean => c.since !== null && !events.some((e) => e.uuid === c.since);
  const releasing = new Set<HeldEvent>();
  for (const c of candidates) { const hit = afterOf(c).find((e) => landsCopy(e, c)); if (hit) releasing.add(hit); }
  const out: HeldCopy[] = [];
  for (const c of candidates) {
    if (ctx.cancelled && ctx.cancelled(c)) continue;                   // the user cancelled it here: nothing will land
    const after = afterOf(c);
    if (after.some((e) => landsCopy(e, c))) continue;                   // its atom is here: it takes the slot
    if (!c.qid && c.pushes >= 1) continue;                              // id-less: one push by text, then never a phantom
    if (ctx.settled) continue;                                          // a settled session took nothing: cancelled or dropped
    if (c.qid && c.since !== null) {                                    // a later landing means the CLI passed it
      const later = after.some((e) => e.kind === "user" && !isEcho(e.uuid) && !isOptimistic(e.uuid) && !e.undelivered && !releasing.has(e));
      if (anchorGone(c) || later) continue;                             // (its anchor left the window: the same reading)
    }
    out.push(c);
  }
  return { held: out, prev: kernelCopies.map((t) => ({ ...t })), anchor: lastKernelUuid(events) };
}

/** The queued copies a held copy renders as: the same card, marked `landing`, never cancelable (the kernel no
 *  longer holds it — a ✕ would ask it to cancel a message it has already taken). */
export function heldAsQueued(h: HeldCopy): HeldQueued {
  const { since, pushes, ...copy } = h;
  return { ...copy, landing: true };
}
