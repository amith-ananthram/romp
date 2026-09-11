// THE SESSION STATUS CHIP: one vocabulary (the words) and one dress (the classes) for every surface that says a
// session's state in a pill. The bar under the transcript wears it for the session you are reading (render.ts
// updateStatusline), the comment popover's statusline mirrors its anatomy (cmtStateChip), and the tag overview's
// rows wear it beside a session's name (fillSnapshotRow) for the two states a row says in words: on you (Blocked,
// the feed's column word; API error when that is the state) and awaiting background work ("Awaiting 3 agents",
// "Awaiting watch", the one peer's name). T322b (the user 2026-09-10, screenshot): the overview had grown a grey
// outlined pill of its own reading "waiting" while the bar said "Awaiting agents" in await-green for the same
// session: two renderers, two vocabularies. The words live here and nowhere else, and the classes are
// `chip chip-<state>` wherever a chip is assembled: the bar's plain states and the overview's rows build through
// statusChip; the bar's pulsing working chip, its interrupt flip and the comment popover's statusline assemble the
// same classes by hand around these words.
import { awaitWord, groupRows, type AwaitRow } from "./spin-caption";
import { hostPartsNodes } from "./host-prefix";

/** The kernel's chip states (its shared _session_chip derivation; render.ts imports the union from here since T322b, so
 *  the labels below are checked exhaustive over it at compile time). needsInput = a live permission/picker prompt (on
 *  YOU), renamed from the legacy "awaiting" (2026-08-15), which stays accepted for OLDER REMOTE KERNELS across
 *  federation; awaitingBg = idle main thread waiting on background work it dispatched (the user 2026-07-13). */
export type ChipState = "working" | "ready" | "needsInput" | "awaiting" | "awaitingBg" | "idle" | "closed" | "compacting" | "clearing" | "blocked" | "retrying" | "interrupting" | "opening";

/** The state words, sentence case (the user 2026-07-03: never ALL CAPS), one per chip state: a state added to the union
 *  without a word here fails to compile (the exhaustiveness main's render.ts map had, kept). */
export const CHIP_LABEL = {
  working: "Working", ready: "Ready", needsInput: "Blocked",
  awaiting: "Blocked",   // the legacy name for needsInput — an older remote kernel still sends it
  awaitingBg: "Awaiting",   // idle, waiting on background work it dispatched — the romp await-green, not working-yellow (the user 2026-07-13; recolored from straw 2026-07-22)
  idle: "Idle", closed: "Closed", compacting: "Compacting", clearing: "Clearing", blocked: "API error",
  retrying: "API retrying…",   // a live session stalled on an API rate-limit/overload auto-retry (api 2026-06-23)
  interrupting: "Interrupting…",   // stop sent, turn not yet settled (the user 2026-07-02) — clears to READY on its own
  opening: "Opening…",             // spawned, transcript not on disk yet — the first record clears it (the user 2026-08-05)
} satisfies Record<ChipState, string>;

/** A named peer behind a peer-kind wait (kernel _peer_identity, 2026-08-26): the name, its host when remote, its
 *  identity colour. Structural: render.ts's PeerIdent and any payload shape fit. */
export interface ChipPeer { name: string; host?: string | null; color?: { bg: string; fg?: string } | null }

/** The status fields the chip reads — structural, so the chat's Status, a snapshot row's frame and a test's plain
 *  object all fit. */
export interface ChipStatusLike {
  state?: string; awaitingKind?: string | null; awaitingCount?: number | null;
  awaitingItems?: readonly AwaitRow[] | null; awaitingPeers?: readonly ChipPeer[] | null;
}

/** The chip's content as data: its state (the class), the words, and the one named peer when the wait is on a
 *  single session (the name itself is the label then, in its identity colour). Pure, so a model can carry it and
 *  compare it; statusChip paints it. */
export interface ChipWords { state: string; text: string; peer: ChipPeer | null }

/** The words for a state: the map's, else the state's own name in sentence case (the user 2026-07-03). */
export function stateLabel(state: string | null | undefined): string {
  const st = state || "";
  return (CHIP_LABEL as Record<string, string>)[st] ?? (st ? st[0].toUpperCase() + st.slice(1).toLowerCase() : "");   // the one indexed read: a state the union lacks (an older remote kernel's) falls to its own name
}

/** The chip's words for a status. Every state but awaitingBg is its label alone. The Awaiting chip carries the KIND
 *  so a glance says WHAT is awaited (the user 2026-08-15; tooltips are dead on the touch PWA), by ONE rule
 *  (awaitWord, which agrees in number, T225): "Awaiting agent" / "Awaiting command" / "Awaiting watch" for one,
 *  "Awaiting 3 agents" for several of a kind, "Awaiting 4" when the kinds are mixed. When every awaited row is a
 *  peer and there is one, the chip names the actual session (the user 2026-08-26): `peer` carries it and `text`
 *  holds the spoken form; several peers keep the one-line rule as a count. A peer beside an agent is a mixed wait. */
export function chipWords(st: ChipStatusLike): ChipWords {
  const state = st.state || "";
  if (state !== "awaitingBg") return { state, text: stateLabel(state), peer: null };
  const items = st.awaitingItems || [];
  const peers = st.awaitingPeers || [];
  const word = awaitWord(st.awaitingKind, st.awaitingCount, items);
  const head = CHIP_LABEL.awaitingBg;
  if (peers.length && groupRows(items).every((g) => g.kind === "peer")) {
    if (peers.length === 1) return { state, text: head + " " + (peers[0].host ? peers[0].host + ":" : "") + peers[0].name, peer: peers[0] };
    return { state, text: head + " " + (word || peers.length + " peers"), peer: null };
  }
  return { state, text: head + (word ? " " + word : ""), peer: null };
}

/** The chip element: `chip chip-<state>` wearing the words. A single peer's NAME renders on its own node in the
 *  peer's identity colour on the always-on backing (.chip-peer-name) through the house session-reference renderer,
 *  so a remote "host:" wears .host-prefix (the user 2026-08-26, round three). `tag`: a span, or a button for the
 *  bar's clickable Awaiting chip (an overview row is itself a button, so its chip is a span). `doc` is the document
 *  to build in — a test's fake; the page's by default. */
export function statusChip(w: ChipWords, tag: "span" | "button" = "span", doc: Pick<Document, "createElement" | "createTextNode"> = document): HTMLElement {
  const chip = doc.createElement(tag);
  chip.className = "chip chip-" + w.state;
  if (w.peer) {
    chip.append(CHIP_LABEL.awaitingBg + " ");
    const nm = doc.createElement("span");
    nm.className = "chip-peer-name";
    nm.replaceChildren(...hostPartsNodes(w.peer.host, w.peer.name, doc));   // the same document: a fake's chip carries a fake's name node
    if (w.peer.color && w.peer.color.bg) nm.style.color = w.peer.color.bg;
    chip.appendChild(nm);
  } else chip.textContent = w.text;
  return chip;
}
