// The reveal progress line (T336; the user 2026-09-10, who clicked a distilled summary far back in a long session,
// waited on a blank, and asked for at least a progress bar). On the index wire the reveal of a far-past anchor is a
// fetch-until-resident loop (scrollToAnchor kicks loadOlder, chatHead prepends the chunk, the re-land kicks again).
// While it runs, one quiet line says how far along it is: "Loading older messages…" with an HONEST fraction as a
// thin bar when the anchor turn's own moment makes one derivable (the resident span over the span back to that
// moment; the moment is the kernel's anchorEventT carried on the seek, never the card's time, which is the card's
// newest activity), else the count of older messages loaded (read off the state from the seek's headFrom at its arm,
// notices excluded) and the oldest loaded time, with the loader's pulsing dots. It hangs off the loop's start and end
// and nothing else, and never begins for a session without the index wire's headFrom count (the one-round-trip window
// of T323 stage 4b). The pure module is exercised directly; chatHead, fetchOlderForAnchor, showSeekNote and the state
// machine are lifted from render.ts and driven over a fake DOM with two chunk answers; source pins hold the seams.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import * as MOD from "./reveal-progress";
import { markerLabel } from "./time-marker";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

// ── the pure module ──────────────────────────────────────────────────────────────────────────────────────────

test("the fraction is the resident span over the span back to the anchor, or absent when it would not be honest", () => {
  assert.equal(MOD.revealFraction(10_000, 9_000, 5_000), 0.2);
  assert.equal(MOD.revealFraction(10_000, 10_000, 5_000), 0, "a one-moment tail has covered none of the way");
  assert.equal(MOD.revealFraction(10_000, 9_000, null), null, "no anchor time: no fraction");
  assert.equal(MOD.revealFraction(null, 9_000, 5_000), null);
  assert.equal(MOD.revealFraction(10_000, null, 5_000), null);
  assert.equal(MOD.revealFraction(10_000, 9_000, 10_000), null, "an anchor no older than the newest resident event: nothing to walk back through");
  assert.equal(MOD.revealFraction(10_000, 9_000, 12_000), null);
  assert.equal(MOD.revealFraction(10_000, 5_000, 5_000), null, "the span already reaches the anchor's moment while the event is not resident: 100% would lie, so the count speaks");
  assert.equal(MOD.revealFraction(10_000, 4_000, 5_000), null);
});

test("what the bar shows is floored and held below complete: rounding can never paint 100% before the event lands", () => {
  assert.equal(MOD.revealShownFraction(0.9996), 0.999);
  assert.equal(MOD.revealShownFraction(0.33749), 0.337);
  assert.equal(MOD.revealShownFraction(0), 0);
  assert.equal(MOD.revealPercentWords(0.9996), "99% of the way back");
  assert.equal(MOD.revealPercentWords(0.996), "99% of the way back");
  assert.equal(MOD.revealPercentWords(0.337), "33% of the way back", "floored, never rounded up");
  assert.equal(MOD.revealPercentWords(0), "0% of the way back");
});

test("the resident span skips timeless events at both ends", () => {
  const epoch = (e: { t?: number }) => (typeof e.t === "number" ? e.t : null);
  assert.deepEqual(MOD.residentSpan([{}, { t: 5 }, { t: 6 }, {}, { t: 9 }, {}], epoch), { oldestT: 5, newestT: 9 });
  assert.deepEqual(MOD.residentSpan([{}, {}], epoch), { oldestT: null, newestT: null });
  assert.deepEqual(MOD.residentSpan([], epoch), { oldestT: null, newestT: null });
});

test("the count is of messages: turns and postal cards, not tool atoms, thinking, or the injected notices compact mode folds", () => {
  const events = [
    { kind: "user", md: "a real prompt", human: true },
    { kind: "tool", name: "Bash" }, { kind: "thinking" },
    { kind: "assistant", md: "a reply" },
    { kind: "postal-service", mid: "m1" },
    { kind: "user", md: "<system-reminder>…</system-reminder>", source: "systemReminder", human: false },   // an injected notice on a user row
    { kind: "user", md: "[romp] the kernel restarted", rompSystem: true },                                  // a romp notice
    { kind: "user", interruptMarker: true },                                                               // an interrupt marker
    { kind: "assistant", interruptSettle: true },                                                          // its settle
    { kind: "retried", retries: 2 }, { kind: "effortApplied", effort: "high" }, { kind: "compact" },
    { kind: "user", md: "an undelivered injected line", source: "romp", human: false, undelivered: true },  // shown as its own row: counts
  ];
  assert.equal(MOD.messageCount(events), 4);
  assert.equal(MOD.messageCount([]), 0);
});

test("the count words: how many older messages landed and how far back the history now reaches, in the rail's clock words", () => {
  const now = Date.UTC(2026, 8, 11, 12, 0, 0);
  const today = Math.floor(now / 1000) - 3 * 3600;
  const words = (t: number) => { const m = markerLabel(t, null, now); return m.date ? m.date + " " + m.hm : m.hm; };   // the rail's own reading, whatever zone runs this
  assert.equal(MOD.revealCountWords(0, today, now), "back to " + words(today), "before the first chunk lands only the reach is known");
  assert.equal(MOD.revealCountWords(1, today, now), "1 older message loaded · back to " + words(today));
  assert.equal(MOD.revealCountWords(250, today, now), "250 older messages loaded · back to " + words(today));
  const lastWeek = today - 9 * 86400;
  assert.ok(markerLabel(lastWeek, null, now).date, "a moment nine days back carries a date word");
  assert.equal(MOD.revealCountWords(500, lastWeek, now), "500 older messages loaded · back to " + words(lastWeek));
  // a moment in another year names its year, the day context's form
  const lastYear = today - 400 * 86400;
  const ly = markerLabel(lastYear, null, now);
  assert.equal(MOD.revealCountWords(500, lastYear, now), `500 older messages loaded · back to ${ly.date} ${new Date(lastYear * 1000).getFullYear()} ${ly.hm}`);
  assert.equal(MOD.revealCountWords(0, null, now), "", "no resident event carries a time: nothing to say yet");
  assert.equal(MOD.REVEAL_LABEL, "Loading older messages…");
});

// ── the state machine and the DOM, lifted from render.ts ─────────────────────────────────────────────────────

class FakeEl {
  tagName: string; className = ""; id = ""; textContent = ""; title = ""; hidden = false;
  children: FakeEl[] = []; parent: FakeEl | null = null;
  style: Record<string, string> = {}; dataset: Record<string, string> = {};
  attrs = new Map<string, string>(); listeners = new Map<string, (e: any) => void>();
  classList = { add: (c: string) => { this.className = (this.className + " " + c).trim(); } };
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
  appendChild(c: FakeEl) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; } }
  setAttribute(k: string, v: string) { this.attrs.set(k, v); }
  addEventListener(k: string, f: (e: any) => void) { this.listeners.set(k, f); }
  find(pred: (e: FakeEl) => boolean): FakeEl | null {
    for (const c of this.children) { if (pred(c)) return c; const d = c.find(pred); if (d) return d; }
    return null;
  }
  querySelector(sel: string) { const cls = sel.slice(1); return this.find((e) => e.className.split(" ").includes(cls)); }
  set tabIndex(_v: number) { /* the seek note sets it */ }
}
function fakeDocument() {
  const body = new FakeEl("body");
  return { body, createElement: (t: string) => new FakeEl(t), getElementById: (id: string) => body.find((e) => e.id === id) };
}

function liftBetween(startAnchor: string, endAnchor: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
}

type Hooks = { posts: any[]; pillShown: number; pillHidden: number; shows: number; cancels: number };
type Api = {
  chatHead: (msg: any) => void;
  timeOnly: () => void;                          // a time-only navigation into the session mid-loop
  tick: (scrolled: boolean, attAnchor: string | null) => void;
  pass: (scrolled: boolean, attAnchor?: string | null) => void;   // landActive's order after the attempt: the tick, then the seek block
  kick: (uuid: string) => void;                                    // scrollToAnchor's older-tail branch, as the pinned source has it
  state: () => { sid: string; uuid: string; anchorT: number | null; from0: number } | null;
  set: (p: Record<string, any>) => void;
  get: (k: string) => any;
};

function liftWorld(): (hooks: Hooks, mod: typeof MOD, doc: ReturnType<typeof fakeDocument>) => Api {
  const note = liftBetween("function showSeekNote(): void {", "// ── reveal progress (T336)");
  const release = liftBetween("function releaseSeekFetch(sid: string): void {", "function cancelSeek(): void {");
  const region = liftBetween("// ── reveal progress (T336)", "// ── end reveal progress");
  const head = liftBetween("function chatHead(msg: any) {", "// Fetch the next older history chunk re-anchored on `uuid`");
  const fetch = liftBetween("function fetchOlderForAnchor(sid: string, uuid: string): boolean {", "// Ask the kernel for the chunk of history just before the resident tail.");
  const prelude = `
    let sessions = new Map(), views = new Map(), activeId = "A";
    let pendingAnchor = null, pendingAnchorIntent = null, pendingAnchorT = null, pendingAnchorKind = null, pendingAnchorKeepY = null, flashedAnchor = null;
    let anchorPendingOlder = false;
    let seek = null;
    const loadingOlder = new Set(), pendingOlderAnchor = new Map(), pendingOlderKeepY = new Map();
    const H = HOOKS;
    const vscodeApi = { postMessage: (m) => H.posts.push(m) };
    let loadingPillEl = null;
    const hideLoadingPill = () => { H.pillHidden++; };
    const showLoadingPill = () => { H.pillShown++; };
    const olderOnServer = (s) => s.proto === 2 ? !s.headKnown : (s.headFrom ?? 0) > 0;   // the guard's helper (T323 stage 4b), outside the lift
    const showActive = () => { H.shows++; };
    const cancelSeek = () => { H.cancels++; };
    const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
    const metaDots = () => { const d = el("span", "meta-dots"); d.appendChild(el("i")); return d; };
    const eventEpoch = (ev) => (typeof ev.t === "number" ? ev.t : null);
    const liveSession = (id) => (id ? sessions.get(id) : undefined);   // no skeleton tabs in this world
    const captureScrollAnchor = () => null;                             // nothing visible at the viewport top in this world
    const { REVEAL_LABEL, revealFraction, revealShownFraction, residentSpan, revealCountWords, revealPercentWords, messageCount } = MOD;
  `;
  const epilogue = `
    return {
      chatHead,
      tick: revealProgressTick,
      pass: (scrolled, attAnchor) => { const a = attAnchor === undefined ? pendingAnchor : attAnchor; revealProgressTick(scrolled, a);
        if (seek && a === seek.uuid) { if (scrolled) { seek = null; document.getElementById("seek-note")?.remove(); revealProgressEnd(); } else showSeekNote(); } },
      kick: (uuid) => {
        anchorPendingOlder = false;
        if (fetchOlderForAnchor(activeId, uuid) || loadingOlder.has(activeId)) {
          pendingOlderAnchor.set(activeId, uuid); pendingOlderKeepY.delete(activeId);
          pendingAnchor = uuid; anchorPendingOlder = true;
        }
      },
      // setActive's time-only branch, as the pinned source has it, then landActive's clearing of the pending fields
      timeOnly: () => { releaseSeekFetch(activeId); if (seek && seek.sid !== activeId) releaseSeekFetch(seek.sid); seek = null; document.getElementById("seek-note")?.remove(); revealProgressEnd(); pendingAnchor = null; },
      state: () => revealProgress,
      set: (p) => {
        if ("sessions" in p) sessions = p.sessions; if ("activeId" in p) activeId = p.activeId; if ("seek" in p) seek = p.seek;
        if ("anchorPendingOlder" in p) anchorPendingOlder = p.anchorPendingOlder; if ("pendingAnchor" in p) pendingAnchor = p.pendingAnchor;
        if ("clearInFlight" in p) { loadingOlder.clear(); pendingOlderAnchor.clear(); }
      },
      get: (k) => ({ pendingAnchor, anchorPendingOlder, loadingOlder, pendingOlderAnchor, seek })[k],
    };
  `;
  return new Function("HOOKS", "MOD", "document", prelude + release + note + region + head + fetch + epilogue) as any;
}

const BASE = 1_760_000_000;                                   // an epoch in seconds; one event a minute
const ev = (i: number) => ({ kind: i % 2 ? "assistant" : "user", uuid: "e" + i, t: BASE + i * 60 });
const range = (a: number, b: number) => Array.from({ length: b - a }, (_, k) => ev(a + k));
const anchorT = BASE + 10 * 60;                               // the anchor turn's own moment: the eleventh event, 490 events past the tail

function world(opts: { seekT: number | null | "none"; from0?: number | null }) {
  const H: Hooks = { posts: [], pillShown: 0, pillHidden: 0, shows: 0, cancels: 0 };
  const doc = fakeDocument();
  const api = liftWorld()(H, MOD, doc);
  const s: any = { id: "A", name: "web", events: range(500, 750), headFrom: 500, headTotal: 750, status: { state: "ready" } };
  // the seek the navigation armed: its t is the kernel's anchorEventT (the turn's own moment), or null when the kernel resolved
  // none; its from0 the session's headFrom at the arm (armSeek records it)
  const from0 = opts.from0 === undefined ? 500 : opts.from0;
  api.set({ sessions: new Map([["A", s]]), activeId: "A", seek: opts.seekT === "none" ? null : { sid: "A", uuid: "e10", kind: null, t: opts.seekT, from0 } });
  return { H, doc, api, s };
}
const line = (doc: ReturnType<typeof fakeDocument>) => doc.getElementById("reveal-progress");
const frac = (oldest: number) => ((749 - oldest) / (749 - 10));
const shown = (f: number) => Math.min(0.999, Math.floor(f * 1000) / 1000);

test("two chunk answers: the line appears with the honest fraction, grows with each chunk, and leaves the moment the anchor lands", () => {
  const { H, doc, api, s } = world({ seekT: anchorT });
  // the first landing pass: the anchor is past the tail, the loop kicks a loadOlder for the chunk before it
  api.kick("e10");
  assert.deepEqual(H.posts, [{ type: "loadOlder", id: "A", before: 500 }]);
  assert.equal(line(doc), null, "nothing until the pass reads its own start");
  api.pass(false);
  let n = line(doc)!;
  assert.ok(n, "the line begins on the pass that kicked the loop");
  assert.equal(n.attrs.get("role"), "status");
  assert.equal(n.querySelector(".rp-label")!.textContent, "Loading older messages…");
  assert.equal(n.dataset.fraction, shown(frac(500)).toFixed(3), "the resident tail's span over the span back to the anchor turn's moment");
  assert.equal(n.dataset.fraction, "0.336");
  assert.equal(n.querySelector(".rp-fill")!.style.width, "33.6%");
  assert.equal(n.querySelector(".rp-bar")!.hidden, false);
  assert.equal(n.querySelector(".rp-bar")!.title, "33% of the way back");
  assert.equal(n.querySelector(".rp-detail")!.textContent, "", "the bar says it; no count beside an honest fraction");
  assert.equal(n.querySelector(".rp-dots")!.hidden, true, "the still bar is the motion: no dots beside it");
  assert.equal(n.dataset.loaded, "0");
  assert.ok(n.querySelector(".rp-x"), "the seek's ✕ rides the line");
  assert.equal(H.pillHidden, 1, "the per-fetch pill yields: one message for the wait");
  assert.equal(doc.getElementById("seek-note"), null, "the seek note yields the slot");
  // chunk one lands: 250 older messages, the anchor still further back; the re-land pass kicks again
  api.chatHead({ type: "chatHead", id: "A", before: 500, from: 250, events: range(250, 500) });
  assert.equal(s.events.length, 500); assert.equal(s.headFrom, 250);
  assert.equal(api.get("pendingAnchor"), "e10", "the deep link waiting to land wins the re-anchor");
  api.kick("e10");
  assert.equal(H.posts.length, 2); assert.equal(H.posts[1].before, 250);
  api.pass(false);
  n = line(doc)!;
  assert.ok(n, "still the same line");
  assert.equal(n.dataset.fraction, shown(frac(250)).toFixed(3));
  assert.equal(n.dataset.fraction, "0.675");
  assert.equal(n.querySelector(".rp-fill")!.style.width, "67.5%");
  assert.equal(n.dataset.loaded, "250", "the count is read off the state: the messages between the seek's headFrom and the session's");
  assert.equal(doc.body.children.filter((c) => c.id === "reveal-progress").length, 1, "updated in place, never rebuilt");
  assert.equal(doc.getElementById("seek-note"), null);
  // chunk two lands the anchor's event: the landing pass ends the line
  api.chatHead({ type: "chatHead", id: "A", before: 250, from: 0, events: range(0, 250) });
  assert.equal(s.headFrom, 0);
  assert.ok(s.events.some((e: any) => e.uuid === "e10"), "the anchor's event is resident");
  api.set({ anchorPendingOlder: false });
  api.pass(true, "e10");
  assert.equal(line(doc), null, "gone the moment the anchor lands");
  assert.equal(api.state(), null);
  assert.equal(H.shows, 2, "each chunk repainted the active view");
});

test("the fraction reads the anchor turn's own moment from the seek, so a loop that begins on a later pass still gets its bar", () => {
  // the first pass missed (a transient miss the seek exists to survive): pendingAnchorT was cleared with the pass, and the
  // pass that finally kicks the loop still finds the moment on the seek record
  const { doc, api } = world({ seekT: anchorT });
  api.kick("e10"); api.tick(false, "e10");
  assert.equal(line(doc)!.dataset.fraction, "0.336");
});

test("the count survives a tab round trip: it is read from the seek's headFrom at its arm, never accumulated", () => {
  const { doc, api } = world({ seekT: null });
  api.kick("e10"); api.pass(false);
  api.chatHead({ type: "chatHead", id: "A", before: 500, from: 250, events: range(250, 500) });
  api.kick("e10"); api.pass(false);
  assert.equal(line(doc)!.dataset.loaded, "250");
  // away to another tab (the line ends) and back (the seek is still armed; the next pass kicks and begins again)
  api.set({ activeId: "B" }); api.tick(false, null);
  assert.equal(line(doc), null); assert.equal(api.state(), null);
  api.set({ activeId: "A" });
  api.chatHead({ type: "chatHead", id: "A", before: 250, from: 100, events: range(100, 250) });   // a chunk that landed meanwhile
  api.kick("e10"); api.pass(false);
  assert.equal(line(doc)!.dataset.loaded, "400", "the whole way since the arm, not since the return");
  // a seek armed on a skeleton (no headFrom yet) leaves the begin pass's own headFrom as the mark
  const w2 = world({ seekT: null, from0: null });
  w2.api.kick("e10"); w2.api.pass(false);
  assert.equal(line(w2.doc)!.dataset.loaded, "0");
  w2.api.chatHead({ type: "chatHead", id: "A", before: 500, from: 250, events: range(250, 500) });
  w2.api.kick("e10"); w2.api.pass(false);
  assert.equal(line(w2.doc)!.dataset.loaded, "250");
});

test("the ✕ on the line is the seek's cancel", () => {
  const { H, doc, api } = world({ seekT: anchorT });
  api.kick("e10"); api.pass(false);
  line(doc)!.querySelector(".rp-x")!.listeners.get("click")!({ stopPropagation() {} });
  assert.equal(H.cancels, 1);
});

test("no anchor moment from the kernel: the count, the oldest loaded time and the loader's dots instead of a bar (never the card's time)", () => {
  const { doc, api } = world({ seekT: null });
  api.kick("e10"); api.pass(false);
  const n = line(doc)!;
  assert.ok(n);
  assert.equal(n.dataset.fraction, undefined);
  assert.equal(n.querySelector(".rp-bar")!.hidden, true);
  assert.equal(n.querySelector(".rp-dots")!.hidden, false, "something moves while the count stands still (the loading rule)");
  assert.equal(n.querySelector(".rp-fill")!.style.width, "0%");
  const words = (t: number) => { const now = Date.now(), m = markerLabel(t, null, now), y = new Date(t * 1000).getFullYear();   // the module's own rule: the year when it differs
    const date = m.date && y !== new Date(now).getFullYear() ? m.date + " " + y : m.date; return date ? date + " " + m.hm : m.hm; };
  assert.equal(n.querySelector(".rp-detail")!.textContent, "back to " + words(BASE + 500 * 60));
  assert.ok(n.querySelector(".rp-x"), "the seek is live: its ✕ rides the line");
  api.chatHead({ type: "chatHead", id: "A", before: 500, from: 250, events: range(250, 500) });
  api.kick("e10"); api.pass(false);
  assert.equal(line(doc)!.querySelector(".rp-detail")!.textContent, "250 older messages loaded · back to " + words(BASE + 250 * 60));
  assert.equal(line(doc)!.dataset.fraction, undefined);
});

test("a navigation with no seek at all: count mode, no ✕", () => {
  const { doc, api } = world({ seekT: "none" });
  api.kick("e10"); api.tick(false, "e10");
  const n = line(doc)!;
  assert.ok(n);
  assert.equal(n.dataset.fraction, undefined);
  assert.equal(n.querySelector(".rp-x"), null, "no seek, no ✕");
});

test("an anchor moment the resident span already reaches falls back to the count: 100% while still loading would lie", () => {
  const { doc, api } = world({ seekT: BASE + 600 * 60 });   // inside the resident span, but not resident (a pruned or compacted event)
  api.kick("e10"); api.pass(false);
  const n = line(doc)!;
  assert.ok(n);
  assert.equal(n.dataset.fraction, undefined);
  assert.equal(n.querySelector(".rp-bar")!.hidden, true);
  assert.match(n.querySelector(".rp-detail")!.textContent, /^back to /);
});

test("the pass that ends the loop without landing gives the seek its note and ✕ back at once", () => {
  const { doc, api } = world({ seekT: anchorT });
  api.kick("e10"); api.pass(false);
  assert.ok(line(doc)); assert.equal(doc.getElementById("seek-note"), null);
  // headFrom reached 0 and the event was not there: the attempt neither kicked nor waits on a fetch for the anchor
  api.set({ anchorPendingOlder: false, clearInFlight: true });
  api.pass(false);
  assert.equal(line(doc), null, "the line is gone"); assert.equal(api.state(), null);
  assert.ok(doc.getElementById("seek-note"), "the seek note stands in the same pass: the seek keeps working toward its backstop, with its ✕");
});

test("another anchor landing in the same session mid-loop leaves the line alone; the loop's own anchor landing ends it", () => {
  const { doc, api } = world({ seekT: anchorT });
  api.kick("e10"); api.pass(false);
  assert.ok(line(doc));
  // a lane click landed a different, resident turn while the chunk for e10 is still on the wire
  api.set({ anchorPendingOlder: false });
  api.tick(true, "e600");
  assert.ok(line(doc), "the fetch for the anchor is still in flight: the line stays");
  api.tick(true, "e10");
  assert.equal(line(doc), null, "its own anchor landed");
});

test("a time-only navigation mid-loop supersedes the seek AND the loop's claim: the in-flight chunk re-pursues nothing and no line returns", () => {
  const { H, doc, api, s } = world({ seekT: anchorT });
  api.kick("e10"); api.pass(false);
  assert.ok(line(doc)); assert.equal(api.get("pendingOlderAnchor").get("A"), "e10", "the fetch on the wire is claimed for the anchor");
  api.timeOnly();                                             // a lane click by time, no anchor: the seek and the line go
  assert.equal(line(doc), null); assert.equal(api.get("seek"), null);
  assert.equal(api.get("pendingOlderAnchor").has("A"), false, "the claim is released: the arrival is a pure prepend");
  assert.equal(api.get("anchorPendingOlder"), false);
  api.chatHead({ type: "chatHead", id: "A", before: 500, from: 250, events: range(250, 500) });
  assert.equal(s.headFrom, 250, "the chunk still lands");
  assert.equal(api.get("pendingAnchor"), null, "nothing re-pursues the abandoned anchor");
  api.tick(true, null);                                       // the time-only moment lands on the next pass
  assert.equal(line(doc), null); assert.equal(api.state(), null);
  assert.equal(H.posts.length, 1, "no new fetch was kicked");
});

test("the line ends when the loop stops asking with no seek, and when the tab changes", () => {
  {
    const { doc, api } = world({ seekT: "none" });
    api.kick("e10"); api.tick(false, "e10");
    assert.ok(line(doc));
    api.set({ anchorPendingOlder: false, clearInFlight: true });
    api.tick(false, null);
    assert.equal(line(doc), null); assert.equal(api.state(), null);
  }
  {
    const { doc, api } = world({ seekT: anchorT });
    api.kick("e10"); api.tick(false, "e10");
    api.set({ activeId: "B" });
    api.tick(false, null);
    assert.equal(line(doc), null, "another tab's landing pass ends a loop whose chunks would be forgotten anyway");
    assert.equal(api.state(), null);
  }
});

test("a fetch already in flight for the anchor keeps the line alive across intermediate render passes", () => {
  const { doc, api } = world({ seekT: anchorT });
  api.kick("e10"); api.tick(false, "e10");
  // a status push mid-fetch: the attempt short-circuits on the in-flight fetch (anchorPendingOlder true again), or with no
  // seek there is no attempt at all and the flag keeps its value; either way the fetch for the anchor is still on the wire
  api.set({ anchorPendingOlder: false });
  api.tick(false, null);
  assert.ok(line(doc), "the in-flight fetch for the anchor holds the line");
});

test("a session without the index wire's headFrom count never begins a line: the one-round-trip window has its own landing", () => {
  const { doc, api, s } = world({ seekT: anchorT });
  s.headFrom = 0;                                             // a proto-2 session carries no count (T323 stage 4b: olderOnServer reads headKnown)
  api.set({ anchorPendingOlder: true, pendingAnchor: "e10" });
  api.tick(false, "e10");
  assert.equal(line(doc), null);
  assert.equal(api.state(), null);
});

// ── the seams in render.ts and the kernel, pinned ───────────────────────────────────────────────────────────

test("the seams: the tick sits on the landing pass BEFORE the seek block, the seek note and the pill yield, clearSeek ends it, nothing accumulates", () => {
  assert.match(RENDER, /revealProgressTick\(scrolled, att\.anchor\);[^\n]*\n[^\n]*\n\s*if \(seek && att\.anchor === seek\.uuid\) \{\n\s*if \(scrolled\) clearSeek\(\);/, "once per landing pass, right before the seek block");
  assert.match(RENDER, /else showSeekNote\(\);[^\n]*\n\s*\}\n(?:[^\n]*\n){0,12}?\s*pendingAnchor = null; pendingAnchorIntent = null; pendingAnchorT = null;/);
  assert.doesNotMatch(RENDER, /revealProgressChunk/, "no chunk hook in chatHead: the count is read off the state");
  assert.match(RENDER, /const from0 = seek && seek\.uuid === p\.uuid && seek\.from0 != null \? seek\.from0 : p\.from0;\n\s*const loaded = messageCount\(s\.events\.slice\(0, Math\.max\(0, from0 - \(s\.headFrom \?\? 0\)\)\)\);/);
  assert.match(RENDER, /if \(revealProgress && revealProgress\.uuid === seek\.uuid\) \{ existing\?\.remove\(\); return; \}/, "showSeekNote yields the slot");
  assert.match(RENDER, /document\.getElementById\("seek-note"\)\?\.remove\(\);\n\s*revealProgressEnd\(\);/, "every end of the seek ends the line");
  assert.match(RENDER, /function showLoadingPill\(\): void \{\n\s*if \(revealProgress\) return;/, "the per-fetch pill yields while the line shows");
  // the start guard reads the index wire's own count, never a version
  assert.match(RENDER, /if \(anchorPendingOlder && pendingAnchor && s && \(s\.headFrom \?\? 0\) > 0\) \{/);
  // the end: this loop's own anchor, the tab, or a loop that neither kicked nor waits
  assert.match(RENDER, /if \(\(scrolled && attAnchor === p\.uuid\) \|\| p\.sid !== activeId \|\| \(!anchorPendingOlder && !inFlight\)\) \{ revealProgressEnd\(\); return; \}/);
  // the moment the fraction reads is the seek's t (the kernel's anchorEventT), never att.t (the card's time)
  assert.match(RENDER, /revealProgressBegin\(activeId!, pendingAnchor, seek && seek\.uuid === pendingAnchor \? seek\.t : null, s\.headFrom \?\? 0\);/);
  assert.doesNotMatch(RENDER, /revealProgressTick\(scrolled, att\.t\)/);
  assert.match(RENDER, /let seek: \{ sid: string; uuid: string; kind: string \| null; t: number \| null; from0: number \| null \} \| null = null;/);
  assert.match(RENDER, /function armSeek\(sid: string, uuid: string, kind: string \| null, t: number \| null = null\): void \{/);
  assert.match(RENDER, /seek = \{ sid, uuid, kind, t, from0: sessions\.get\(sid\)\?\.headFrom \?\? null \};/);
  assert.match(RENDER, /if \(anchor\) armSeek\(id, anchor, anchorKind \?\? null, anchorEventT \?\? null\);[^\n]*\n\s*else if \(anchorT != null\) \{[^\n]*\n\s*releaseSeekFetch\(id\);[^\n]*\n\s*if \(seek && seek\.sid !== id\) releaseSeekFetch\(seek\.sid\);[^\n]*\n\s*clearSeek\(\);\n\s*\}/, "a time-only navigation supersedes the seek and releases the loop's fetch claim");
  assert.match(RENDER, /typeof m\.anchorEventT === "number" \? m\.anchorEventT : undefined\);/, "the frame handler hands the kernel's anchorEventT to setActive");
  // the bar paints the floored fraction; the dots stand in for count mode
  assert.match(RENDER, /const shown = revealShownFraction\(fraction\);[^\n]*\n\s*n\.dataset\.fraction = shown\.toFixed\(3\);\n\s*bar\.hidden = false; bar\.title = revealPercentWords\(fraction\);\n\s*fill\.style\.width = \(shown \* 100\)\.toFixed\(1\) \+ "%";\n\s*detail\.textContent = "";\n\s*dots\.hidden = true;/);
  assert.match(RENDER, /detail\.textContent = revealCountWords\(loaded, oldestT, Date\.now\(\)\);\n\s*dots\.hidden = false;/);
  // the ✕ is the seek's cancel, carried over
  assert.match(RENDER, /x\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); cancelSeek\(\); \}\);\n\s*n\.appendChild\(x\);\n\s*\}\n\s*document\.body\.appendChild\(n\);\n\s*\}\n\s*const bar = n\.querySelector/);
  // one reading of a notice, shared with compact mode
  assert.match(RENDER, /function isFoldableNotice\(ev: ChatEvent\): boolean \{ return isFoldableNoticeShape\(ev as any\); \}/);
});

test("the kernel resolves the anchor turn's own moment on the live branch alone, from the built chat or the cached parse, never a build", () => {
  assert.match(KERNEL, /def _anchor_event_t\(sid, anchor, now=None\):/);
  const fn = KERNEL.slice(KERNEL.indexOf("def _anchor_event_t("), KERNEL.indexOf("\ndef ", KERNEL.indexOf("def _anchor_event_t(") + 10));
  assert.doesNotMatch(fn, /build_session\(/, "never a build (the docstring may name the one it replaced)");
  assert.match(fn, /hit = _built_chat\.get\(sid\)/);
  assert.match(fn, /e\.get\("uuid"\) == anchor or e\.get\("mid"\) == anchor or e\.get\("resultUuid"\) == anchor\n\s*or anchor in \(e\.get\("mids"\) or \[\]\) or anchor in \(e\.get\("settleUuids"\) or \[\]\)/, "the chat's four selectors");
  assert.match(fn, /_parse\(sess\["path"\], sid, now\)/, "then the cached parse");
  assert.match(KERNEL, /if focus_msg\.get\("anchor"\) and "anchorEventT" not in focus_msg:\n\s*focus_msg = dict\(focus_msg, anchorEventT=_anchor_event_t\(sid, focus_msg\["anchor"\]\)\)\n\s*_reveal_chat_for\(client, focus_msg\)/, "resolved on the live branch of _reveal_or_confirm");
  assert.doesNotMatch(KERNEL, /_anchor_event_t\(msg\[/, "no caller resolves it eagerly");
});

test("the dress: the seek note's slot and surface, the composer placeholder's dim ink, a thin bar, the loader's dots only in count mode", () => {
  const at = CSS.indexOf("#reveal-progress {");
  assert.ok(at > 0);
  const rule = CSS.slice(at, CSS.indexOf("}", at));
  assert.ok(rule.includes("position: fixed; left: 50%; bottom: 86px;"), "the seek note's slot");
  for (const tok of ["var(--radius-toast)", "var(--shadow-toast)", "var(--menu-border)", "var(--vscode-menu-background, var(--surface-raised))", "color: var(--dim)"]) {
    assert.ok(rule.includes(tok), tok);
  }
  const block = CSS.slice(at, CSS.indexOf("#reveal-progress .rp-x:hover"));
  assert.doesNotMatch(block, /animation|@keyframes|transition/, "no motion of its own: the dots are the loader's (.meta-dots)");
  assert.match(CSS, /#reveal-progress \.rp-dots\[hidden\] \{ display: none; \}/);
  assert.match(CSS, /#reveal-progress \.rp-bar \{[^}]*height: 3px;/, "thin");
  assert.match(CSS, /#reveal-progress \.rp-fill \{[^}]*background: var\(--dim\);/);
  assert.match(CSS, /#composer-input::placeholder \{ color: var\(--dim\); \}/, "the ink it borrows");
});
