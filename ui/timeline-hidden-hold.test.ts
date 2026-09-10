// Paint only when the pane can be seen (the user 2026-09-07, who came back to the dashboard's browser tab
// after it sat in the background and found the page frozen). The timeline parsed AND fully drew every frame
// the kernel pushed while the tab was hidden — N frames, N whole-SVG rebuilds — so the return paid for all of
// them at once. Now update()/applyBars() still apply their STATE unconditionally (this.data, the loader
// latch, the live edge's clock) but hold the draw while the pane is out of sight, on the same _dirtyWhileTip
// path the tooltip and click holds use; the tab coming back (visibilitychange) or the pane coming into view
// (IntersectionObserver) paints ONE catch-up and re-arms the live tick, which stops while hidden instead of
// waking every 2 s into a forced layout. A bars frame that outruns its skeleton is parked, not dropped.
// Headless over the render test's DOM shim, with a recording document and a fake IntersectionObserver.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

// ---- minimal DOM shim (the timeline-render.test.ts shim: only what the view touches) ----
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) { c.parentNode = n; this.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    get firstChild() { return this.children[0] || null; },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 1400, height: 420, left: 0, top: 0, right: 1400, bottom: 420 }; },
    closest() { return null; }, focus() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
// The document RECORDS its listeners so a test can raise visibilitychange the way the browser does — every
// panel's handler runs, in order — and its visibilityState is a plain settable field.
const docListeners: Record<string, Array<(e: any) => void>> = {};
const g: any = global;
g.document = {
  visibilityState: "visible",
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener(t: string, fn: (e: any) => void) { (docListeners[t] ||= []).push(fn); },
  removeEventListener(t: string, fn: (e: any) => void) { const a = docListeners[t] || []; const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); },
};
// A fake IntersectionObserver: remembers its callback + targets so a test can report the pane coming into view.
const ios: FakeIO[] = [];
class FakeIO {
  cb: (entries: any[], io: any) => void; targets: any[] = []; disconnected = false;
  constructor(cb: (entries: any[], io: any) => void) { this.cb = cb; ios.push(this); }
  observe(t: any) { this.targets.push(t); }
  disconnect() { this.disconnected = true; }
  fire(isIntersecting: boolean) { this.cb([{ isIntersecting, target: this.targets[0] }], this); }
}
g.IntersectionObserver = FakeIO;
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)" });
const rafs: Array<() => void> = [];
g.requestAnimationFrame = (cb: () => void) => { rafs.push(cb); return rafs.length; };   // a real handle, never run: the tests read the arming, not the tick
g.cancelAnimationFrame = () => {};
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const SRC = fs.readFileSync(viewPath, "utf8");
const { TimelinePanel, expandBars } = createRequire(__filename)(viewPath);

// The render test's two-lane payload: live lanes with in-window turns, so a real draw() emits a populated SVG.
function synthData() {
  const now = 1_781_000_000;
  const turn = (id: string, dt0: number, dt1: number) => ({
    id, promptId: id + "#p", workId: id + "#w",
    start: now - dt0, end: now - dt1, prompt: "do the thing", src: "typed", mids: [],
    pending: false, summary: "did the thing", reply: "did it", tid: "fork-" + id, uuid: "u-" + id,
    workUuid: "w-" + id, replyUuid: "r-" + id,
  });
  const sess = (id: string, name: string) => ({
    id, name, color: "#7aa2f7", state: "working", live: true, model: "Opus 4.8", effort: "xhigh",
    context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
  });
  return {
    now,
    sessions: [sess("S1", "alpha"), sess("S2", "beta")],
    turns: { S1: [turn("S1:1:aa", 300, 60), turn("S1:2:bb", 50, 5)], S2: [turn("S2:1:cc", 200, 30)] },
    messages: [], activeChat: null, focus: null, hover: null, usage: null,
  };
}
// The wire shape: a {type:"data"} lanes skeleton (no turns) and the {type:"bars"} detail that follows it.
function skeletonOf(full: any, dNow = 0) {
  return { now: full.now + dNow, sessions: full.sessions, turns: {}, judging: {}, messages: [], nudges: [],
           activeChat: null, focus: null, hover: null, usage: null };
}
function barsOf(full: any, dNow = 0) {
  return { type: "bars", turns: full.turns, judging: {}, messages: [], nudges: [], now: full.now + dNow };
}

function mk() {
  const panel: any = new TimelinePanel(makeNode("div"));
  if (panel._loaderBackstop != null) { clearTimeout(panel._loaderBackstop); panel._loaderBackstop = null; }   // keep node from waiting 12 s on it
  const c = { draws: 0 };
  const real = panel.draw.bind(panel);
  panel.draw = () => { c.draws++; real(); };
  return { panel, c, io: ios[ios.length - 1] };
}
function setVisibility(state: "hidden" | "visible") {
  g.document.visibilityState = state;
  for (const fn of [...(docListeners.visibilitychange || [])]) fn({ type: "visibilitychange" });
}
// Unhook a finished test's panel so a later test's visibilitychange doesn't reach it.
function done(panel: any) { g.document.removeEventListener("visibilitychange", panel._onVis); g.document.visibilityState = "visible"; }

test("while the tab is hidden, update() and applyBars() apply their state but never draw", () => {
  const { panel, c } = mk();
  const full = synthData();
  setVisibility("hidden");
  panel.update(skeletonOf(full));
  assert.equal(c.draws, 0, "a skeleton landing on a hidden tab is not drawn");
  assert.equal(panel._dirtyWhileTip, true, "…it is buffered on the hold path the tooltip/click holds use");
  assert.equal(panel.data.sessions.length, 2, "…but the lanes ARE applied (state, not paint)");
  panel.applyBars(barsOf(full));
  assert.equal(c.draws, 0, "the bars frame is not drawn either");
  assert.deepEqual(panel.data.turns, expandBars(full.turns), "…yet the bars are merged into the live data");
  assert.equal(panel._barsLoaded, true, "…and the loader latch flipped (a return must not show the loader)");
  assert.equal(panel._newestNow, full.now, "…and the live edge's clock adopted the frame's now");
  for (let i = 1; i <= 10; i++) { panel.update(skeletonOf(full, i)); panel.applyBars(barsOf(full, i)); }
  assert.equal(c.draws, 0, "twenty more frames while hidden: still not one rebuild");
  assert.equal(panel.data.now, full.now + 10, "the state kept moving with every frame");
  done(panel);
});

test("coming back to the tab paints ONE catch-up and re-arms the live tick; a second return with nothing new paints nothing", () => {
  const { panel, c } = mk();
  const full = synthData();
  setVisibility("hidden");
  for (let i = 0; i < 6; i++) { panel.update(skeletonOf(full, i)); panel.applyBars(barsOf(full, i)); }
  assert.equal(c.draws, 0);
  assert.equal(panel._liveRAF, null, "the live tick was never armed for a hidden pane");
  setVisibility("visible");
  assert.equal(c.draws, 1, "exactly one draw for six held frames");
  assert.equal(panel._dirtyWhileTip, false, "the hold is released");
  assert.ok(panel.svg.children.length > 10, "the catch-up painted the bars (loader down, lanes + bars up)");
  assert.notEqual(panel._liveRAF, null, "the live tick is re-armed by the release, not left for the next frame");
  setVisibility("visible");
  assert.equal(c.draws, 1, "nothing new arrived → nothing to paint");
  panel.update(skeletonOf(full, 7));
  assert.equal(c.draws, 2, "visible again: a frame draws synchronously, as it always did");
  done(panel);
});

test("a bars frame that outruns its skeleton is parked, not dropped, and lands with the skeleton (the coalesced [bars1, data2] order)", () => {
  const { panel, c } = mk();
  const full = synthData();
  const early = barsOf(full);
  panel.applyBars(early);
  assert.equal(panel.data, null, "no skeleton yet: nothing to draw onto");
  assert.equal(panel._pendingBars, early, "…so the frame is parked");
  assert.equal(c.draws, 0);
  const newer = barsOf(full, 1);
  panel.applyBars(newer);
  assert.equal(panel._pendingBars, newer, "a second early frame supersedes the first (newest wins)");
  panel.update(skeletonOf(full, 2));
  assert.equal(panel._pendingBars, null, "the skeleton arriving is the event that lands the parked frame");
  assert.deepEqual(panel.data.turns, expandBars(full.turns), "the parked bars are merged into the skeleton's data");
  assert.equal(panel._barsLoaded, true, "the loader latch flipped with them");
  assert.equal(c.draws, 1, "one paint carries lanes AND bars — not a loader paint then a bars paint");
  assert.ok(panel.svg.children.length > 10, "…and it is a populated SVG, not the loader");
  assert.equal(panel.data.now, full.now + 2, "the newer skeleton's clock wins over the parked frame's");
  done(panel);
});

test("a full one-shot payload carrying its own turns outranks a frame parked before it", () => {
  const { panel } = mk();
  const full = synthData();
  const stale: any = barsOf(full); stale.turns = { S1: [] };
  panel.applyBars(stale);
  panel.update(full);
  assert.deepEqual(panel.data.turns, expandBars(full.turns), "the later, complete payload's bars stand");
  assert.equal(panel._pendingBars, null, "the parked frame is dropped, not kept for a later skeleton");
  done(panel);
});

test("a pane out of view on a visible tab (display:none → offsetParent null) holds too; the IntersectionObserver releases it", () => {
  const { panel, c, io } = mk();
  const full = synthData();
  assert.equal(io.targets[0], panel.wrap, "the constructor observes the wrap");
  panel.wrap.offsetParent = null;   // what a display:none iframe / hidden Obsidian leaf reports
  panel.update(skeletonOf(full)); panel.applyBars(barsOf(full));
  assert.equal(c.draws, 0, "nobody can see the pane: held");
  assert.equal(panel._dirtyWhileTip, true);
  setVisibility("visible");
  assert.equal(c.draws, 0, "the tab was visible all along — its event does not release a pane hidden by layout");
  panel.wrap.offsetParent = makeNode("div");
  io.fire(true);
  assert.equal(c.draws, 1, "the pane coming into view paints the one catch-up");
  assert.equal(panel._dirtyWhileTip, false);
  io.fire(true);
  assert.equal(c.draws, 1, "an observer callback with nothing held paints nothing");
  done(panel);
});

test("the release yields to a shown tooltip or a pressed pointer — their own releases repaint", () => {
  const { panel, c } = mk();
  const full = synthData();
  setVisibility("hidden");
  panel.update(skeletonOf(full)); panel.applyBars(barsOf(full));
  panel.tip.classList.add("show");
  setVisibility("visible");
  assert.equal(c.draws, 0, "a tooltip is up: its still-snapshot hold stands (hideTip paints the catch-up)");
  assert.equal(panel._dirtyWhileTip, true, "…and the frames stay buffered for it");
  panel.tip.classList.remove("show");
  panel._pointerHeld = true;
  setVisibility("visible");
  assert.equal(c.draws, 0, "a click in progress: click-safe hold stands (_release paints after the click)");
  panel._pointerHeld = false;
  setVisibility("visible");
  assert.equal(c.draws, 1, "no other hold left: the return paints");
  done(panel);
});

test("_tickLive stops while hidden instead of sleeping into a forced layout; the release re-arms it", () => {
  const { panel } = mk();
  panel.update(synthData());   // a full payload: bars present → the live tick is armed
  assert.notEqual(panel._liveRAF, null, "live-following and visible: armed");
  panel._liveRAF = null; panel._liveTO = null;   // the loop's own wake clears its handles before the look
  setVisibility("hidden");
  panel._tickLive();
  assert.equal(panel._liveTO, null, "hidden: no 2 s sleep is scheduled");
  assert.equal(panel._liveRAF, null, "…and no animation frame either — the loop is stopped");
  setVisibility("visible");
  assert.notEqual(panel._liveRAF, null, "the return re-arms it");
  done(panel);
});

test("without an IntersectionObserver (a bare host), a layout-hidden pane is not held — nothing could release it", () => {
  const saved = g.IntersectionObserver;
  g.IntersectionObserver = undefined;
  try {
    const { panel, c } = mk();
    assert.equal(panel._io, null);
    panel.wrap.offsetParent = null;
    panel.update(skeletonOf(synthData()));
    assert.equal(c.draws, 1, "paints as it always did: a hold with no release event would freeze the lanes on a stale frame");
    setVisibility("hidden");
    panel.update(skeletonOf(synthData(), 1));
    assert.equal(c.draws, 1, "the tab's own visibility still holds — visibilitychange releases that");
    done(panel);
  } finally { g.IntersectionObserver = saved; }
});

test("destroy() unhooks the visibility listener and the observer", () => {
  const { panel, io } = mk();
  assert.ok((docListeners.visibilitychange || []).includes(panel._onVis), "installed by the constructor");
  panel.destroy();
  assert.ok(!(docListeners.visibilitychange || []).includes(panel._onVis), "gone after destroy");
  assert.equal(io.disconnected, true);
  assert.equal(panel._io, null);
});

test("the pane publishes its hidden word for the kernel's pane shim on the hold's own events, and not before the observer has spoken", () => {
  // The shim gates its stale banner on paneHidden(): its zero-viewport probe OR a published word of true. The probe is
  // blind, in Chromium, to a pane hidden after a first show (the iframe keeps its size), so the panel publishes what
  // its two measures say (document hidden OR the observer's last word) as window.__rompPaneHidden; here window is g.
  delete g.__rompPaneHidden;
  const { panel, io } = mk();
  assert.equal(typeof g.__rompPaneHidden, "undefined", "boot: nothing published; the shim's probe decides");
  setVisibility("hidden"); setVisibility("visible");
  assert.equal(typeof g.__rompPaneHidden, "undefined", "a visibilitychange on either arm before the observer's first entry publishes nothing");
  io.fire(true);
  assert.equal(g.__rompPaneHidden, false, "the observer's first word: on screen");
  io.fire(false);
  assert.equal(g.__rompPaneHidden, true, "hidden after a first show: the case the probe misses");
  setVisibility("hidden"); assert.equal(g.__rompPaneHidden, true);
  setVisibility("visible"); assert.equal(g.__rompPaneHidden, true, "the tab's return is not a show for a display:none pane");
  io.fire(true); assert.equal(g.__rompPaneHidden, false, "the re-show publishes on the observer's callback");
  setVisibility("hidden"); assert.equal(g.__rompPaneHidden, true, "the tab hidden with the pane on screen");
  setVisibility("visible"); assert.equal(g.__rompPaneHidden, false, "the return publishes on visibilitychange");
  assert.equal(typeof g.__rompPaneHidden, "boolean", "a boolean, the type the shim tests for");
  done(panel);
  delete g.__rompPaneHidden;
});

test("without an IntersectionObserver the pane never publishes: the shim's probe stands for good", () => {
  const saved = g.IntersectionObserver;
  g.IntersectionObserver = undefined;
  delete g.__rompPaneHidden;
  try {
    const { panel } = mk();
    setVisibility("hidden"); setVisibility("visible");
    assert.equal(typeof g.__rompPaneHidden, "undefined", "a page that published document.hidden alone would have no observer to correct it");
    done(panel);
  } finally { g.IntersectionObserver = saved; delete g.__rompPaneHidden; }
});

test("source pins: the hidden branch of _tickLive returns; both hold sites key on _hiddenForPaint; the release is synchronous", () => {
  const tick = /  _tickLive\(\) \{([\s\S]*?)\n  \}/.exec(SRC)![1];
  assert.match(tick, /if \(!this\._isVisible\(\)\) return;/, "hidden: stop the loop");
  assert.doesNotMatch(tick, /_sleep\(2000\)/, "no long sleep re-entering _isVisible()'s forced offsetParent layout every 2 s");
  const hold = "|| this._hiddenForPaint() || this._pointerHeld) { this._dirtyWhileTip = true; return; }";
  assert.equal(SRC.split(hold).length - 1, 2, "update() and applyBars() share the one hold condition, pointer-held last (click-safe.test.ts pins that tail)");
  assert.match(SRC, /document\.addEventListener\('visibilitychange', this\._onVis\);/);
  assert.match(SRC, /if \(typeof IntersectionObserver !== 'undefined'\) \{[\s\S]*?this\._io\.observe\(this\.wrap\);/, "the observer is guarded — Obsidian / a bare host may lack it");
  assert.match(SRC, /document\.removeEventListener\('visibilitychange', this\._onVis\);/);
  assert.match(SRC, /this\._io\.disconnect\(\);/);
  assert.match(SRC, /_hiddenForPaint\(\) \{\s*\n\s*if \(this\._io\) return !this\._isVisible\(\);\s*\n\s*return typeof document !== 'undefined' && document\.visibilityState === 'hidden';/,
    "layout-hidden counts only when the observer exists to release it");
  const rel = /  _releasePaintHold\(\) \{([\s\S]*?)\n  \}/.exec(SRC)![1];
  assert.doesNotMatch(rel, /setTimeout|requestAnimationFrame/, "the catch-up paints synchronously in the release event — the compositor shows the cached frame on a tab switch, so this is the earliest fresh one");
  assert.match(rel, /this\._startLiveTick\(\);/);
  assert.match(SRC, /this\._paneIntersecting = null;/, "the observer's word starts null: nothing is published before it speaks");
  assert.match(SRC, /this\._onVis = \(\) => \{ this\._publishPaneHidden\(\); this\._releasePaintHold\(\); \};/, "visibilitychange publishes, then releases");
  assert.match(SRC, /_publishPaneHidden\(\) \{\n\s*if \(typeof window === 'undefined'\) return;\n\s*if \(this\._paneIntersecting === null\) return;/, "one method, silent on a bare host and until the observer speaks");
  assert.match(SRC, /window\.__rompPaneHidden = docHidden \|\| this\._paneIntersecting === false;/, "the union of both measures, under the name the shim reads");
  assert.match(SRC, /if \(!this\.data \|\| !this\.data\.sessions\) \{ this\._pendingBars = m; return; \}/, "bars ahead of the skeleton are parked");
  assert.match(SRC, /if \(this\._pendingBars\) \{ const pb = this\._pendingBars; this\._pendingBars = null; if \(!ownBars\) this\._mergeBars\(pb\); \}/, "…and update() lands them");
});
