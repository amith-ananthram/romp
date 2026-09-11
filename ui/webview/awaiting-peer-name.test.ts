// Every peer-kind awaiting stamp names the ACTUAL session (the user 2026-08-26): 'a peer' is a bug
// to trace, not a style. The kernel now resolves identities through ONE ladder (_peer_identity:
// registry first — dormant sessions keep their names — then the cross-host pair, then the sid stub)
// and ships them on every arm; these pins hold the render half: the feed box's chips open the
// session, the chat box + chip name the peer in identity colour, and the fallback says WHY a name
// is missing instead of presenting 'peer' as a style.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const FEED = ui("webview", "feed.ts");
const RENDER = ui("webview", "render.ts");
const CSS = ui("webview", "styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the feed box's peer chips are the standard session chip — click opens the session", () => {
  const box = FEED.slice(FEED.indexOf("const awPeers ="), FEED.indexOf("a._awaitSpin.title"));
  assert.match(box, /hostPartsNodes\(p\.host, p\.name\)/, "quiet host: prefix, the ↪ from treatment");
  assert.match(box, /nm\.style\.color = p\.color\.bg/, "identity colour");
  assert.match(box, /postMessage\(\{ type: "openSession", id: p\.sid \}\)/, "the handoffTo click idiom");
  assert.match(box, /nm\.style\.cursor = "pointer"/);
});

test("the feed's nameless peer wait explains itself — honest fallback, not a style", () => {
  assert.match(FEED,
    /if \(awaitingBg && !awPeers\.length && it\.awaiting && it\.awaiting\.kind === "peer"\)\s*\n\s*a\._awaitSpin\.title \+=/,
    "peer-kind with no names → the tooltip says the record predates capture or an older kernel shipped it");
});

test("the chat pane names the peer: box in identity colour, pill with the colour dot", () => {
  assert.match(RENDER, /awaitingPeers\?: PeerIdent\[\] \| null;/, "the payload field beside awaitingKind");
  assert.match(RENDER, /type PeerIdent = \{ name: string; host\?: string; sid\?: string; color\?: \{ bg: string; fg: string \} \| null \};/,
    "named alias — the Status interface line stays brace-free for its other pins");
  const boxAt = RENDER.indexOf("const awPeers = s.status.awaitingPeers");   // narrowed `s` since the one-renderer cut (2026-09-06)
  const box = RENDER.slice(boxAt, RENDER.indexOf("head.appendChild(lab);", boxAt));
  assert.match(box, /el\("span", "bg-await-peer"\)/);
  assert.match(box, /\(pr\.host \? pr\.host \+ ":" : ""\) \+ pr\.name/, "host-prefixed when cross-host");
  assert.match(box, /nm\.style\.color = pr\.color\.bg/);
  // the bar's chip is the SHARED status chip since T322b (status-chip.ts): the peer name's treatment lives in its builder
  const CHIP = ui("webview", "status-chip.ts");
  const chip = CHIP.slice(CHIP.indexOf("export function statusChip("));
  assert.ok(!chip.includes("chip-peer-dot"), "the dot retired (the user 2026-08-26, round two) — the name wears the colour");
  assert.match(chip, /nm\.className = "chip-peer-name";/, "'Awaiting <name>' — the NAME itself in identity colour");
  assert.match(chip, /nm\.replaceChildren\(\.\.\.hostPartsNodes\(w\.peer\.host, w\.peer\.name, doc\)\)/,
    "the HOUSE idiom via the SHARED renderer (the user 2026-08-26, round three) — host in .host-prefix italic gray, never a restyled copy");
  assert.match(chip, /nm\.style\.color = w\.peer\.color\.bg/);
  assert.ok(!/nm\.textContent = .*host/.test(chip), "no one-string concatenation of host and name");
  assert.match(CHIP, /peers\.length \+ " peers"/, "several peers keep the one-line rule as a count");
  assert.match(RENDER, /const chip = statusChip\(chipWords\(s\.status\), "button"\) as HTMLButtonElement;/, "the bar builds from it");
  // the backing is ONE rule shared with the transcript's mention chip since 2026-09-10 (the user, who wanted a
  // typed @name to wear this chip's look); the peer name keeps its own #fff default beside it
  assert.match(CSS, /^\.chip-peer-name, \.mention-chip \{ background: rgba\(0, 0, 0, 0\.85\); border-radius: 7px; padding: 0 5px; \}/m,
    "the ~85% black backing — any colour reads on it, the chip hue still glows around it");
  assert.match(CSS, /^\.chip-peer-name \{ color: #fff;/m, "the no-identity name's default stays the peer chip's own");
  assert.ok(!/\.chip-peer-dot/.test(CSS), "the dot's CSS goes with it");
});

test("the kernel ships identities on every arm — the or-chain's hardcoded Nones are gone", () => {
  // …plus, since slice 2 (2026-09-05), the peers as ROWS in the sixth slot, so the pill lists them
  assert.match(KERNEL, /\(_stamp_why, _stamp_kind, _stamp_since, _stamp_peers, \(len\(_stamp_peers\) if _stamp_peers else None\), _awaiting_peer_items\(_stamp_peers\)\)/, "the judge-stamp arm (identities + their count, T228; rows, slice 2)");
  assert.match(KERNEL, /\(sess_awaiting_why, sess_awaiting_kind, sess_awaiting_since, sess_awaiting_peers, sess_awaiting_count, sess_awaiting_items\)/,
    "the session-snapshot arm (its rows in the sixth slot, slice 2)");
  assert.match(KERNEL, /"awaitingPeers": \(\(_aw or \{\}\)\.get\("peers"\) or None\)/, "the chat status payload");
  assert.match(KERNEL, /"awaitingPeers": \(\(_aw_bg or \{\}\)\.get\("peers"\) or None\)/, "the timeline sessions payload");
  assert.match(KERNEL, /def _peer_identity\(psid\):/, "the ONE identity ladder");
  assert.ok(!/_name_of\(p\) or "a peer"/.test(KERNEL),
    "the bare registry read that named every cross-host delegation 'a peer' is gone");
  assert.match(KERNEL, /_hnodes = \[x for x in _open_leaves\(nodes, nid\)/,
    "the handoff scan walks the same open set its gate proved non-empty");
});
