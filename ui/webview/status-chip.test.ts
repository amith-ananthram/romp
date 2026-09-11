// THE SESSION STATUS CHIP (status-chip.ts; T322b, the user 2026-09-10): one vocabulary and one dress for every surface
// that says a session's state in a pill. The user saw the tag overview's rows wearing a grey outlined pill of their own
// reading "waiting" beside the bar's await-green "Awaiting agents" for the same session. Executed here: the pure words
// (stateLabel, chipWords) and the builder over a small fake document. Pinned: the two consumers build from this module
// (the bar under the transcript, render.ts updateStatusline; the overview's rows, fillSnapshotRow), no second label map
// and no pill of the view's own survive, and the stylesheet dresses the chip once. Synthetic names only (the notes-api
// world, TESTHOST).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { CHIP_LABEL, stateLabel, chipWords, statusChip } from "./status-chip";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = read("render.ts");
const CSS = read("styles.css");
const SNAP = read("tab-snapshot.ts");

class FakeNode {
  tag: string; className = ""; textContent = ""; children: any[] = []; style: Record<string, string> = {};
  constructor(tag: string) { this.tag = tag; }
  append(...cs: any[]): void { for (const c of cs) this.children.push(typeof c === "string" ? { tag: "#text", textContent: c } : c); }
  appendChild(c: any): any { this.children.push(c); return c; }
  replaceChildren(...cs: any[]): void { this.children = []; this.append(...cs); }
  text(): string { return this.children.length ? this.children.map((c) => (c.text ? c.text() : c.textContent)).join("") : this.textContent; }
}
const doc = { createElement: (tag: string) => new FakeNode(tag), createTextNode: (t: string) => ({ tag: "#text", textContent: t }) } as unknown as Pick<Document, "createElement" | "createTextNode">;   // the builder's document: the fake stands in for both node makers

test("the label map is exhaustive over the chip states at compile time, and the union lives beside it", () => {
  const CHIP = read("status-chip.ts");
  assert.match(CHIP, /^export type ChipState = "working" \| "ready" \| "needsInput" \| "awaiting" \| "awaitingBg" \| "idle" \| "closed" \| "compacting" \| "clearing" \| "blocked" \| "retrying" \| "interrupting" \| "opening";/m);
  assert.match(CHIP, /\} satisfies Record<ChipState, string>;/, "a state added to the union without a word fails to compile (main's exhaustiveness, kept)");
  assert.doesNotMatch(RENDER, /^type ChipState =/m, "render.ts imports the union");
  assert.match(CHIP, /export function hostPartsNodes|hostPartsNodes\(w\.peer\.host, w\.peer\.name, doc\)/, "the peer's name node is built in the same document as the chip");
});

test("stateLabel: the map's word in sentence case, a state the map lacks in sentence case, nothing for none", () => {
  assert.deepEqual(["working", "ready", "needsInput", "awaiting", "awaitingBg", "blocked", "retrying", "closed"].map(stateLabel),
    ["Working", "Ready", "Blocked", "Blocked", "Awaiting", "API error", "API retrying…", "Closed"]);
  assert.equal(stateLabel("frobnicating"), "Frobnicating", "an unknown state: its own name, first letter up, the rest down");
  assert.equal(stateLabel("SHOUTING"), "Shouting"); assert.equal(stateLabel(""), ""); assert.equal(stateLabel(null), ""); assert.equal(stateLabel(undefined), "");
  assert.equal(CHIP_LABEL.awaitingBg, "Awaiting");
});

test("chipWords: every state but awaitingBg is its label; the Awaiting chip words WHAT is awaited by the one rule, and names a single peer", () => {
  assert.deepEqual(chipWords({ state: "working" }), { state: "working", text: "Working", peer: null });
  assert.deepEqual(chipWords({ state: "needsInput" }), { state: "needsInput", text: "Blocked", peer: null });
  assert.deepEqual(chipWords({ state: "blocked" }), { state: "blocked", text: "API error", peer: null });
  assert.deepEqual(chipWords({}), { state: "", text: "", peer: null }, "no state: an empty chip, never a throw");
  assert.deepEqual(chipWords({ state: "awaitingBg" }), { state: "awaitingBg", text: "Awaiting agents", peer: null }, "kindless, countless: the historic default word");
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "agents", awaitingCount: 1 }), { state: "awaitingBg", text: "Awaiting agent", peer: null }, "agrees in number (T225)");
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "agents", awaitingCount: 3 }), { state: "awaitingBg", text: "Awaiting 3 agents", peer: null });
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "task", awaitingCount: 2 }), { state: "awaitingBg", text: "Awaiting 2 commands", peer: null }, "the plain words: a task is a command");
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "job", awaitingCount: 1 }), { state: "awaitingBg", text: "Awaiting watch", peer: null }, "…a job is a watch");
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "mixed", awaitingCount: 4 }), { state: "awaitingBg", text: "Awaiting 4", peer: null }, "mixed kinds: the number alone");
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "mixed" }), { state: "awaitingBg", text: "Awaiting", peer: null }, "mixed with no count: the bare head");
  // the awaited ROWS decide before the legacy kind + count (slice 2)
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "agents", awaitingCount: 9, awaitingItems: [{ kind: "watches", id: "w1" }] }), { state: "awaitingBg", text: "Awaiting watch", peer: null });
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingItems: [{ kind: "agents", id: "a1" }, { kind: "commands", id: "c1" }] }), { state: "awaitingBg", text: "Awaiting 2", peer: null });
  // peers: one → the chip names it (the text is the spoken form, host-prefixed when remote); several → the count; a peer beside an agent is a mixed wait
  const api = { name: "api", host: "TESTHOST", color: { bg: "#d53a3a", fg: "#ffffff" } };
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "peer", awaitingCount: 1, awaitingPeers: [api] }), { state: "awaitingBg", text: "Awaiting TESTHOST:api", peer: api });
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "peer", awaitingCount: 1, awaitingPeers: [{ name: "web" }], awaitingItems: [{ kind: "peer", id: "p1" }] }), { state: "awaitingBg", text: "Awaiting web", peer: { name: "web" } }, "a local peer: the bare name; a peer row keeps the name path");
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingKind: "peer", awaitingCount: 2, awaitingPeers: [api, { name: "web" }] }), { state: "awaitingBg", text: "Awaiting 2 peers", peer: null });
  assert.deepEqual(chipWords({ state: "awaitingBg", awaitingPeers: [api], awaitingItems: [{ kind: "peer", id: "p1" }, { kind: "agents", id: "a1" }] }), { state: "awaitingBg", text: "Awaiting 2", peer: null }, "a peer beside an agent: mixed, no name");
});

test("statusChip: `chip chip-<state>` wearing the words; a span by default, a button on request; the one peer's name on its own coloured node through the shared host renderer", () => {
  const blocked = statusChip(chipWords({ state: "needsInput" }), "span", doc) as unknown as FakeNode;
  assert.deepEqual([blocked.tag, blocked.className, blocked.textContent, blocked.children.length], ["span", "chip chip-needsInput", "Blocked", 0]);
  const bar = statusChip(chipWords({ state: "awaitingBg", awaitingKind: "agents", awaitingCount: 3 }), "button", doc) as unknown as FakeNode;
  assert.deepEqual([bar.tag, bar.className, bar.textContent], ["button", "chip chip-awaitingBg", "Awaiting 3 agents"]);
  const plain = statusChip(chipWords({ state: "ready" }), undefined, doc) as unknown as FakeNode;
  assert.deepEqual([plain.tag, plain.className, plain.textContent], ["span", "chip chip-ready", "Ready"]);
  // the peer path builds the name through host-prefix.ts's renderer, in the SAME document the chip is built in (doc passes through)
  const named = statusChip(chipWords({ state: "awaitingBg", awaitingKind: "peer", awaitingCount: 1, awaitingPeers: [{ name: "api", host: "TESTHOST", color: { bg: "#d53a3a", fg: "#ffffff" } }] }), "span", doc) as unknown as FakeNode;
  assert.equal(named.className, "chip chip-awaitingBg");
  assert.equal(named.children.length, 2, "the head and the name node");
  assert.equal(named.children[0].textContent, "Awaiting ");
  const nm = named.children[1] as FakeNode;
  assert.deepEqual([nm.tag, nm.className, nm.style.color], ["span", "chip-peer-name", "#d53a3a"], "the NAME wears the identity colour on the backing");
  assert.deepEqual(nm.children.map((c) => [c.tag, c.className || "", c.textContent]), [["span", "host-prefix", "TESTHOST:"], ["#text", "", "api"]], "the host as quiet metadata, the shared renderer's shape");
  const local = statusChip(chipWords({ state: "awaitingBg", awaitingPeers: [{ name: "web" }] }), "span", doc) as unknown as FakeNode;
  assert.deepEqual([(local.children[1] as FakeNode).text(), (local.children[1] as FakeNode).style.color], ["web", undefined], "no host, no colour: the bare name in the backing's default ink");
});

test("pinned: the bar and the tag overview's rows both build from this module; no second map, no pill of the view's own", () => {
  assert.match(RENDER, /import \{ CHIP_LABEL, chipWords, statusChip, type ChipState \} from "\.\/status-chip";/);
  assert.doesNotMatch(RENDER, /const CHIP_LABEL/, "the map has one home");
  const bar = RENDER.split("function updateStatusline() {")[1].split("\nfunction ")[0];
  assert.match(bar, /const chip = statusChip\(chipWords\(s\.status\), "button"\) as HTMLButtonElement;/, "the Awaiting chip, as the bar's button");
  assert.match(bar, /sl\.appendChild\(statusChip\(chipWords\(s\.status\)\)\);/, "every plain state's chip");
  assert.doesNotMatch(bar, /el\("span", `chip chip-\$\{/, "no chip class assembled by hand");
  const row = RENDER.split("function fillSnapshotRow(")[1].split("\n}\n")[0];
  assert.match(row, /if \(r\.chip\) btn\.appendChild\(statusChip\(r\.chip\)\);/, "the overview row: the model's chip, painted by the shared builder, a span inside the row's button");
  const rowCode = row.split("\n").map((l) => l.replace(/\s*\/\/.*$/, "")).join("\n");   // the code alone: the comment recalls the old pill by name
  assert.doesNotMatch(rowCode, /snap-flag|"waiting"|"needs you"/, "the bespoke pill and its lowercase words are gone");
  assert.doesNotMatch(RENDER, /snap-flag/); assert.doesNotMatch(CSS, /snap-flag \{|snap-flag\.needs/, "…and its rules");
  // the model picks the chip: on you → the bar's word for the session's own state, else the feed's column word; awaiting → the status's kind, count, rows, peers
  assert.match(SNAP, /import \{ chipWords, type ChipStatusLike, type ChipWords \} from "\.\/status-chip";/);
  assert.match(SNAP, /const chip = \(feedBlock \|\| st\.needsYou\) \? chipWords\(\{ state: s\?\.status && tabStateClass\(s\.status\) === "tab-blocked" \? "blocked" : "needsInput" \}\)\s*\n\s*: st\.waiting \? chipWords\(s\?\.status \|\| \{\}\) : null;/, "API error only for the tab's own on-you API error (the flags), else the feed's column word");
  assert.match(SNAP, /if \(r\.chip && r\.chip\.text && stateWord !== r\.chip\.text\) parts\.push\(r\.chip\.text\);/, "the spoken label says the chip's words once");
  assert.match(CSS, /\.chip \{[^}]*line-height: normal;/s, "a span chip and a button chip stand the same height");
  assert.match(SNAP, /if \(s === "awaitingBg"\) return \{ pip: "waiting", state: chipWords\(st\)\.text, needsYou: false, waiting: true, closed: false \};/, "the spoken state is the chip's words");
  assert.match(SNAP, /&& a\.needsYou === b\.needsYou && a\.waiting === b\.waiting && sameChip\(a\.chip, b\.chip\) && a\.now === b\.now/, "a chip change is a model change");
  // the dress is the chip's, once: the size rule and the per-state fills in the one sheet
  assert.equal((CSS.match(/^\.chip \{/gm) || []).length, 1);
  assert.match(CSS, /\.chip-awaitingBg \{ background: var\(--st-awaitbg-bg\); color: var\(--st-awaitbg-fg\); \}/);
  assert.match(CSS, /\.chip-needsInput \{ background: var\(--st-awaiting-bg\); color: var\(--st-awaiting-fg\); \}/);
  assert.match(CSS, /\.chip-blocked \{ background: var\(--st-blocked-bg\); color: var\(--st-blocked-fg\); \}/);
  assert.doesNotMatch(CSS, /\.snap-row \.chip|\.snap-item \.chip|#tab-snapshot \.chip/, "the overview adds no rule of its own for the chip");
});
