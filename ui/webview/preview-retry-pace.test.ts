// A figure preview whose LINK is down never moves the transcript (T291, the user 2026-09-09). Two figures in a
// remote session's transcript re-attempted on every kernel push while the laptop's relay to their host was
// failing, and every attempt swapped the box between the one-line "fetching…" and the five-line failure note:
// the transcript above the reader moved by four lines, ten times a second, for the length of the streaming
// turn. previewFull is EXECUTED here over a minimal fake DOM and a stubbed fetch, driven through the same entry
// points render.ts calls: retryFailedPreviews on every kernel message, refreshSettledPreviews on a reconnect.
// Two rules under test: a link failure heals on the reconnect-class event only, never on a push; and an attempt
// never changes the box's child structure (only the note's words may change, and only when they do).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const PREVIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "preview.ts"), "utf8");
const SID = "TESTHOST:11111111-2222-4333-8444-000000000291";
const PATH = "/home/user/notes-api/plots/latency.png";

// ── a minimal DOM: what previewFull touches, nothing more ──────────────────────────────────────────────────
class FakeEl {
  tag: string; children: FakeEl[] = []; parent: FakeEl | null = null; attrs: Record<string, string> = {};
  style: Record<string, string> = {}; _cls = new Set<string>(); _text = ""; isConnected = true;
  onclick: any = null; onerror: any = null; onmousedown: any = null; onauxclick: any = null; title = ""; src = ""; alt = ""; loading = ""; decoding = "";
  listeners: Record<string, Function[]> = {};
  constructor(tag: string) { this.tag = tag; }
  get className(): string { return [...this._cls].join(" "); }
  set className(v: string) { this._cls = new Set(v.split(/\s+/).filter(Boolean)); }
  get classList() { const s = this._cls; return { add: (...c: string[]) => c.forEach((x) => s.add(x)), remove: (...c: string[]) => c.forEach((x) => s.delete(x)), contains: (c: string) => s.has(c), toggle: (c: string, on?: boolean) => { if (on === undefined) on = !s.has(c); on ? s.add(c) : s.delete(c); return on; } }; }
  get textContent(): string { return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text; }
  set textContent(v: string) { this.children.forEach((c) => (c.parent = null)); this.children = []; this._text = v; }
  appendChild(c: FakeEl): FakeEl { if (c.parent) c.parent.children = c.parent.children.filter((x) => x !== c); c.parent = this; this.children.push(c); return c; }
  append(...cs: FakeEl[]) { cs.forEach((c) => this.appendChild(c)); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; }
  get firstElementChild(): FakeEl | null { return this.children[0] || null; }
  querySelector(sel: string): FakeEl | null { const cls = sel.replace(/^\./, ""); const walk = (n: FakeEl): FakeEl | null => { for (const c of n.children) { if (c._cls.has(cls)) return c; const d = walk(c); if (d) return d; } return null; }; return walk(this); }
  closest() { return null; }
  addEventListener(t: string, fn: Function) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  setAttribute(k: string, v: string) { this.attrs[k] = v; }
  /** the structure a height depends on: tags, classes, text, display — one string to compare across attempts */
  /** the classes that shape the box; path-retry-flash is a one-shot pulse animation (no size), left out */
  cls(): string { return [...this._cls].filter((c) => c !== "path-retry-flash").sort().join("."); }
  shape(): string { return `${this.tag}.${this.cls()}[${this.style.display || ""}]{${this.children.length ? this.children.map((c) => c.shape()).join(",") : JSON.stringify(this._text)}}`; }
  /** the structure without the words: only the elements that are there */
  bones(): string { return `${this.tag}.${this.cls()}(${this.children.map((c) => c.bones()).join(",")})`; }
}
(globalThis as any).document = { createElement: (t: string) => new FakeEl(t), addEventListener: () => {}, querySelectorAll: () => [] };   // no parked markdown images here (md-img-park.test.ts drives those)
(globalThis as any).location = { protocol: "http:", origin: "http://127.0.0.1:1" };   // canPreview: the web dashboard
(globalThis as any).window = (globalThis as any).window || {};

let fetchCalls = 0;
let fetchAnswer: () => Promise<any> = () => Promise.reject(new Error("no answer set"));
(globalThis as any).fetch = (_url: string, _init?: any) => { fetchCalls++; return fetchAnswer(); };
const failWith = (status: number, body: string) => () => Promise.resolve({ status, ok: false, headers: { get: () => null }, text: async () => body });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const minted: FakeEl[] = [];
async function armedBox(file = PATH) {   // a path per test: a resolved picture is memoized by URL for the page life
  const P = await import("./preview");
  for (const b of minted) b.isConnected = false;   // an earlier test's box is a re-rendered turn's: the drains drop it without a fetch
  const box = P.previewFull(file, SID, true) as unknown as FakeEl;
  minted.push(box);
  assert.ok(box, "an image preview builds (verified: the kernel said the file exists)");
  // the happy path's <img> failed to load: the first failure, no message from the server
  const img = box.children.find((c) => c.tag === "img" && c._cls.has("path-full-img"))!;
  assert.ok(img && img.onerror, "the plain <img> carries the onerror that hands the box to the retry machinery");
  img.onerror();
  await sleep(10);   // failAfterBeat(0): no beat on the first attempt
  return { P, box };
}

test("a LINK failure heals on the reconnect-class event only: pushes make no attempt, and the box's structure never moves", async () => {
  fetchCalls = 0;
  const { P, box } = await armedBox();
  const s0 = box.shape();
  assert.match(s0, /path-full-wait/, "the wait persona is up after the first failure");
  assert.match(s0, /connection dropped — retrying · tap to retry now/, "the first failure has no server text");
  // the per-message heal makes ONE attempt (the budget's, this is not yet known to be a link failure)…
  fetchAnswer = failWith(502, "tunnel to TESTHOST is not answering; re-dialing");
  P.retryFailedPreviews();
  await sleep(20);
  assert.equal(fetchCalls, 1, "the attempt fetched once");
  const boneAfterStart = box.bones();
  await sleep(450);   // the retry beat (MIN_RETRY_SPIN_MS) before the failure is shown
  const s1 = box.shape();
  assert.equal(box.bones(), boneAfterStart, "the failed attempt kept every element: one wait, one swirl, one note");
  assert.match(s1, /tunnel to TESTHOST is not answering; re-dialing — retries when the link is back · tap to retry now/, "the note carries the link's reason and says what will happen");
  // …and now it IS a link failure: a streaming session pushing many messages makes NO further attempt and
  // leaves the box byte-identical (no height change is possible without a DOM change → zero unitchange rows)
  const before = fetchCalls;
  for (let i = 0; i < 60; i++) { P.retryFailedPreviews(); if (i % 10 === 0) await sleep(5); }
  await sleep(450);
  assert.equal(fetchCalls, before, "sixty kernel messages, no attempt: the link's heal is the reconnect event");
  assert.equal(box.shape(), s1, "the box did not change at all under the pushes");
  // the reconnect-class event (romp:wsup / hostUp → refreshSettledPreviews) makes exactly one attempt…
  P.refreshSettledPreviews();
  await sleep(20);
  assert.equal(fetchCalls, before + 1, "one attempt per reconnect");
  assert.equal(box.bones(), boneAfterStart, "the running attempt kept the wait box and its note (no one-line 'fetching…' swap)");
  assert.equal(box.shape(), s1, "the note's words stayed while the attempt ran");
  await sleep(450);
  assert.equal(box.shape(), s1, "the same failure again: nothing changed, not even the words");
  // …and another reconnect after which the link works: the picture lands (the one legitimate layout change)
  fetchAnswer = () => Promise.resolve({ status: 200, ok: true, headers: { get: (h: string) => (h === "Content-Length" ? "3" : null) },
    body: { getReader: () => { let done = false; return { read: async () => done ? { done: true, value: undefined } : (done = true, { done: false, value: new Uint8Array([1, 2, 3]) }) }; } } });
  (globalThis as any).URL = (globalThis as any).URL || {};
  (globalThis as any).URL.createObjectURL = () => "blob:fake";
  (globalThis as any).URL.revokeObjectURL = () => {};
  (globalThis as any).Blob = class { constructor(public parts: any[], public opts: any) {} };
  P.refreshSettledPreviews();
  await sleep(30);
  assert.match(box.shape(), /img\.path-full-img/, "the image replaced the wait box once the bytes arrived");
});

test("a NON-link failure keeps the bounded per-message budget, and even those attempts never change the structure", async () => {
  fetchCalls = 0;
  const { P, box } = await armedBox("/home/user/notes-api/plots/throughput.png");
  fetchAnswer = failWith(404, "not found: the file is gone");
  for (let round = 0; round < 3; round++) {
    const calls = fetchCalls;
    const before = box.shape();
    P.retryFailedPreviews();
    await sleep(20);
    if (fetchCalls === calls) break;                  // the budget is spent: the settled chip took over
    assert.equal(box.shape(), before, "attempt " + round + ": the wait box kept its elements AND its words while fetching (no one-line 'fetching…' swap)");
    await sleep(450);
  }
  assert.match(box.shape(), /span\.path-full-retry\[\]\{"⚠ not found: the file is gone — tap to retry"\}/, "the budget spent on real verdicts settles on the tap chip");
  // the settled chip's ONE new-evidence heal (the 2026-08-18 rule): pushes inside one beat make one attempt, and
  // the chip keeps its shape while that attempt runs (the chip carries the words)
  const settledBones = box.bones();
  const calls = fetchCalls;
  for (let i = 0; i < 30; i++) { P.retryFailedPreviews(); if (i % 10 === 0) await sleep(5); }
  assert.equal(fetchCalls, calls + 1, "thirty pushes inside one beat: the one new-evidence heal, never one per push");
  assert.equal(box.bones(), settledBones, "the chip kept its elements while the heal ran");
  await sleep(450);
  // that heal's failure is the last budgeted attempt: the chip carries the retrying words for one more cycle…
  assert.match(box.shape(), /path-full-retry\[\]\{"not found: the file is gone — retrying · tap to retry now"\}/, "the chip carries the words: no new element, no swap");
  P.retryFailedPreviews();
  await sleep(470);
  assert.equal(fetchCalls, calls + 2, "…which the next push spends");
  assert.match(box.shape(), /span\.path-full-retry\[\]\{"⚠ not found: the file is gone — tap to retry"\}/, "the same verdict settles the chip again");
  const settled = box.shape();
  for (let i = 0; i < 30; i++) P.retryFailedPreviews();
  await sleep(450);
  assert.equal(fetchCalls, calls + 2, "the same verdict again is no new evidence: no further attempt");
  assert.equal(box.shape(), settled, "and the chip does not move");
  // a TAP re-arms the budget and brings the loading persona back at once: swirl + note, inside the same wait
  const chip = box.querySelector("path-full-retry")!;
  fetchAnswer = () => new Promise(() => {});          // an attempt that stays in flight
  chip.onclick({ stopPropagation() {}, currentTarget: chip });
  await sleep(20);
  assert.match(box.shape(), /span\.path-full-wait\[\]\{img\.path-load-spin\[\]\{""\},span\.path-load-note\[\]\{"fetching…"\}\}/, "a tap shows the swirl and the note: the chip is the give-up state only");
});

test("a link failure while the tunnel row never left up heals once on the recovery counter's hostUp (T291b)", async () => {
  // the kernel bumps the row's upSeq when a row that had misses answers again; federation's poll turns the bump into
  // hostUp; render.ts drains the settled previews on hostUp — so the parked box makes exactly one attempt
  fetchCalls = 0;
  const { P, box } = await armedBox("/home/user/notes-api/plots/queue-depth.png");
  fetchAnswer = failWith(502, "tunnel to TESTHOST is not answering; re-dialing");
  P.retryFailedPreviews(); await sleep(20); await sleep(450);   // the budget's one attempt names the link
  const parked = box.shape();
  assert.match(parked, /retries when the link is back/);
  const before = fetchCalls;
  for (let i = 0; i < 30; i++) P.retryFailedPreviews();
  await sleep(450);
  assert.equal(fetchCalls, before, "the status stayed up and pushes kept coming: no attempt");
  P.refreshSettledPreviews();                                    // what the bump's hostUp runs (render.ts)
  await sleep(20);
  assert.equal(fetchCalls, before + 1, "the counter's hostUp: exactly one attempt");
  assert.equal(box.shape(), parked, "the box did not move for it");
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(FED, /if \(prev !== undefined && seq !== prev && !down\.has\(host\) && !recovered\.includes\(host\)\) recovered\.push\(host\);/, "a bump while up is a recovery");
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /if \(m\.type === "hostUp"\) \{ refreshSettledPreviews\(\); healPathImgs\(\); \}/, "hostUp drains the settled previews");
});

test("the source keeps the two rules where the behaviour lives", () => {
  assert.match(PREVIEW, /if \(transient\) settledPreviews\.set\(box, \(\) => build\(true\)\);\s*\n\s*else \{ autoRetries--; failedPreviews\.set\(box, \(\) => build\(true\)\); \}/,
    "a link failure registers for the reconnect-class heal only; a real verdict spends the budget on the per-message heal");
  assert.match(PREVIEW, /const waitBox = \(\): \{ wait: HTMLElement; note: HTMLElement \} => \{/, "one wait box reused across attempts");
  assert.match(PREVIEW, /if \(autoRetries <= 1\) return \{ wait, note: chip \};\s*\n\s*chip\.remove\(\);/, "the chip carries the words for the one new-evidence heal; a re-armed budget gets the loading persona back");
  // the PDF probe's 502 and a relay-URL markdown image wait for the reconnect-class heal too
  assert.match(PREVIEW, /\(r\.status === 502 \? settledPreviews : failedPreviews\)\.set\(box, probe\);/);
  assert.match(PREVIEW, /parkMdImg\(img, src\);/, "a markdown image that failed is parked, not registered for any per-message heal (T291c)");
  // a remote kernel's restart reopens the relay socket and fires neither romp:wsup nor hostUp: that event heals too
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /window\.addEventListener\("romp:hostRelayUp", \(e\) => \{[\s\S]{0,600}?refreshSettledPreviews\(\);/);
  assert.match(PREVIEW, /const \{ note \} = waitBox\(\);\s*\n\s*if \(!note\.textContent\) setNote\(note, got > 0 \? "resuming… "/, "an attempt keeps the note that is up");
  assert.match(PREVIEW, /const setNote = \(note: HTMLElement, text: string\) => \{ if \(note\.textContent !== text\) note\.textContent = text; \};/, "the words change only when they do");
});
