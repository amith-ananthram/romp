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
