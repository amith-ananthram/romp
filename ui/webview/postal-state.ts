// The postal card's two WORDS and its DELIVERY STATE (T302, the user 2026-09-10, after seeing old and new
// renderings side by side): the interaction KIND is coloured text, never a chip (chips read as tags now), and
// the delivery state is an ICON the way messaging apps show sent / delivered / read, at the head's right edge.
// Side-effect-free, so the dashboard bundle and the node tests both load it.
//
// What the kernel actually files (kernel.py _postal_out_card, _cli_send_card, _hydrate_postal; the postal
// ledger's events in postal/postal_service.py): a SENT card carries `status` as stamped at send time —
// "delivered" (the send tool answered "Delivered to …"), "parked" (the recipient was unreachable or dead, the
// message waits), or nothing (the send errored) — plus, joined by message id from the ledger, a `receipt`:
// `read` (the recipient's inbox drain consumed it: a REAL read event, the ledger's `exec`), `relayed` (a far
// host's end-to-end delivery ack), `bounced` (+ `why`: refused, or undeliverable), `recalled` (the sender
// unsent it), `remote` (addressed across the peer bus). An INCOMING card is in hand by definition; it carries
// only `park` (it waited while this session was offline).

export type PostalDeliveryState = "sent" | "delivered" | "read" | "parked" | "bounced" | "recalled";

export interface PostalReceipt {
  read?: number;      // epoch s: the recipient consumed it
  relayed?: number;   // epoch s: the far host acked delivery
  bounced?: number;   // epoch s: returned
  why?: string;       // a refusal's reason (bounced)
  recalled?: number;  // epoch s: the sender unsent it
  remote?: boolean;   // addressed across the peer bus (queued for relay until relayed)
}

export interface PostalDelivery {
  state: PostalDeliveryState;
  word: string;      // the title's word, capitalised: "Delivered"
  t?: number;        // epoch s the state was reached, when the kernel knows it
  why?: string;      // bounced: the reason
  note?: string;     // a state's own tail for the title (an incoming parked message: it waited, it is in hand)
}

const KIND_WORDS: Record<string, string> = { delegate: "Delegation", coordinate: "Coordination", question: "Question" };

/** The kind as coloured TEXT: "Delegation" / "Coordination" / "Question" (first letter capitalised), "" for an
 *  unknown or absent kind. Takes the intent CLASS the render layer already resolves (delegate|coordinate|question). */
export function kindLabel(cls: string | null | undefined): string {
  return (cls && KIND_WORDS[cls]) || "";
}

/** The delivery state of a card, from what the kernel filed. null = no icon: an incoming message that is
 *  simply in hand, or a sent one whose send errored (the tool row itself says what happened). */
export function deliveryOf(ev: { direction: "in" | "out"; status?: string | null; park?: boolean; ts?: string; t?: number;
                                receipt?: PostalReceipt | null }): PostalDelivery | null {
  const sentAt = ev.t || (ev.ts ? Math.floor(Date.parse(ev.ts) / 1000) || undefined : undefined);
  if (ev.direction === "in") {
    // in hand by definition; `park` says it waited for this session while it was offline (the sent row's flag)
    return ev.park ? { state: "parked", word: "Parked", t: sentAt, note: "waited while you were offline" } : null;
  }
  // a send that ERRORED has no state: the tool row itself says what happened — and never a neighbour's
  // receipt (the body-keyed join can hand a refused send the outcomes of its retry with the same words)
  if (ev.status == null) return null;
  const r = ev.receipt || {};
  if (r.bounced) return { state: "bounced", word: "Bounced", t: r.bounced, why: r.why || undefined };
  if (r.recalled) return { state: "recalled", word: "Recalled", t: r.recalled };
  if (r.read) return { state: "read", word: "Read", t: r.read };
  if (r.relayed) return { state: "delivered", word: "Delivered", t: r.relayed };
  if (ev.status === "parked") return { state: "parked", word: "Parked", t: sentAt };
  if (ev.status === "delivered") {
    // across the peer bus the send tool's "delivered" means handed to the relay: sent, not yet delivered
    return r.remote ? { state: "sent", word: "Sent", t: sentAt } : { state: "delivered", word: "Delivered", t: sentAt };
  }
  return null;
}

/** The delivery mark's DRAWINGS, one per state: the inner SVG of a 16-unit box the renderer wraps (render.ts
 *  deliveryIcon: 14 px, a 1.5 stroke in currentColor, round caps and joins, no fill unless a drawing says so).
 *
 *  sent / delivered / read is the ladder messaging apps draw (T337, the user 2026-09-10, who liked the marks but found a
 *  bare check read as a line when zoomed in): a hollow circle = sent (handed to the relay), the circle with a check =
 *  delivered (in the recipient's inbox, or the far host's ack), the filled circle with the check knocked out = read (the
 *  recipient consumed it: the ledger's own exec event, never inferred). One circle, one three-point check: the three
 *  rungs are one mark changing state, and the read rung's check wears MARK_CHECK_CLASS so the sheet can cut it out in
 *  the page colour (a presentation attribute cannot carry a var()). The other three states keep their own glyphs: a
 *  clock = parked, a cross = bounced, a return arrow = recalled. Each mark carries a worded title (deliveryTitle).
 *
 *  An adaptation of Signal's ladder, not its rungs one for one: Signal draws sending as the hollow circle, sent as a
 *  circled check, delivered as a circled double check and read as the filled circle; the kernel files three states, so
 *  ours maps sent, delivered and read onto hollow, check and filled check.
 *
 *  Drawn at 14 px with a 1.5 stroke, heavier than the envelope glyph at the head's other end (12 px, 1.4): a ring with
 *  a check inside needs the two extra pixels for the check's arms to stay clear of the ring at device scale 1, and the
 *  1.5 stroke is the mark's own specification (the user's reference).
 *
 *  The marks keep the STATE colour (the sheet: dim for sent and delivered, the accent for read), never the kind's.
 *
 *  Licence: the ladder is drawn here from two primitives of our own, a circle and a three-point polyline, in this
 *  file's own coordinates. This repository is Apache-2.0 (LICENSE); Signal's own icon assets ship AGPL-3.0 with its
 *  apps and are therefore not copied into it. */
export const MARK_CHECK_CLASS = "postal-mark-check";
const CHECK = '<path class="' + MARK_CHECK_CLASS + '" d="M4.9 8.3 L7.1 10.5 L10.9 6.1"/>';   // one three-point check, its
                                                                                              // far tip clear of the ring
export const DELIVERY_GLYPHS: Record<PostalDeliveryState, string> = {
  sent: '<circle cx="8" cy="8" r="6.25"/>',
  delivered: '<circle cx="8" cy="8" r="6.25"/>' + CHECK,
  read: '<circle cx="8" cy="8" r="6.25" fill="currentColor"/>' + CHECK,
  parked: '<circle cx="8" cy="8" r="5.6"/><path d="M8 4.8 V8.2 L10.4 9.6"/>',
  bounced: '<path d="M4.5 4.5 L11.5 11.5"/><path d="M11.5 4.5 L4.5 11.5"/>',
  recalled: '<path d="M6.6 4.6 L3.2 8 L6.6 11.4"/><path d="M3.2 8 H10 A2.8 2.8 0 0 0 12.8 5.2"/>',
};

/** The icon's title: the word, the clock when known, the reason for a bounce. `clock` formats an epoch. */
export function deliveryTitle(d: PostalDelivery, clock: (epochS: number) => string): string {
  let s = d.word;
  if (d.t) s += " " + clock(d.t);
  if (d.note) s += " — " + d.note;
  else if (d.state === "parked") s += " — delivers when the session is reachable";
  else if (d.state === "sent") s += " — on its way across the relay";
  if (d.why) s += " — " + d.why;
  return s;
}
