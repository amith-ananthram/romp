// The live tick as a transform write. While the timeline follows the live edge, every look of the paced
// live loop (_tickLive) advanced the clock by wiping and rebuilding the whole svg: about 30 ms a draw in the
// browser at a few dozen lanes, once every 0.1-2 s, the pane's largest cost while visible and outside every
// handler bracket. draw() now puts every time-positioned element in one plot group, and the tick writes one
// translate on that group plus a width on each element whose right edge rides the live now (an open bar, an
// open awaiting or compacting span, an open judging run); a full draw() stays only for what a translate cannot
// express. Headless, on the DOM stand-in timeline-render.test.ts uses: the stand-in counts element creation,
// so a rebuild is observable. (ui/timeline-live-tick.test.ts pins the loop's pacing; this file pins the look.)
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as path from "node:path";
import { createRequire } from "node:module";

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
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) { this.children.splice(i, 1); c.parentNode = null; } return c; },
    get firstChild() { return this.children[0] || null; },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 1400, height: 420, left: 0, top: 0, right: 1400, bottom: 420 }; },
    closest() { return null; }, focus() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
let created = 0;   // every element the view creates: a full draw() makes hundreds, a tick must make none
const g: any = global;
g.document = {
  createElement(t: string) { created++; return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { created++; return makeNode(t); },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener() {}, removeEventListener() {},
  elementFromPoint() { return null; },
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)" });
g.requestAnimationFrame = () => 0;
g.cancelAnimationFrame = () => {};
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel } = createRequire(__filename)(viewPath);

const NOW = 1_781_000_000;
const SID1 = "11111111-2222-3333-4444-aaaaaaaaaaa1", SID2 = "11111111-2222-3333-4444-aaaaaaaaaaa2";
function turn(id: string, start: number, end: number, extra: any = {}) {
  return { id, promptId: id + "#p", workId: id + "#w", start, end, prompt: "do the thing", src: "typed", mids: [],
           pending: false, summary: "did the thing", reply: "did it", tid: "fork-" + id, uuid: "u-" + id, workUuid: "w-" + id, replyUuid: "r-" + id, ...extra };
}
function sess(id: string, name: string, state: string, now: number) {
  return { id, name, color: "#7aa2f7", state, live: true, model: "Opus 4.8", effort: "xhigh", context: 40, since: now - 60,
           awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false };
}
/** Two lanes: web has an OPEN turn (its bar ends at the live edge), api a closed one. */
function liveData(now: number = NOW): any {
  return {
    now,
    sessions: [sess(SID1, "web", "working", now), sess(SID2, "api", "idle", now)],
    turns: { [SID1]: [turn("a", now - 900, now - 700), turn("b", now - 300, now, { open: true })], [SID2]: [turn("c", now - 600, now - 400)] },
    messages: [], judging: [], activeChat: null, focus: null, hover: null, usage: null,
  };
}
/** A panel following the live edge, built once; `advance` moves its clock by `sec` for the next tick. */
function livePanel(data: any, winSec: number): any {
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true;
  panel._winSec = winSec; panel.fitted = true;
  panel.update(data);
  assert.ok(panel._liveFollowing(), "the panel follows the live edge");
  assert.ok(panel._tickPlot, "a full build leaves the tick its handle");
  return panel;
}
const advance = (panel: any, sec: number) => { panel._nowBaseMs = performance.now() - sec * 1000; };
// one look of the paced loop; the sleep it arms for the next look is cleared so the test does not wait on it
const tick = (panel: any) => { panel._tickLive(); panel._stopLiveTick(); };
const plotOf = (panel: any) => panel._tickPlot.g;
const widthOf = (el: any) => Number(el.getAttribute("width"));

test("a tick moves the plot group by a transform and creates no element; the open bar's width rides the edge", () => {
  const panel = livePanel(liveData(), 3600);
  const tp = panel._tickPlot, geom0 = { ...panel._geom };
  const openBar = tp.riders.find((r: any) => r.el.tag === "rect" && r.attr === "width");
  assert.ok(openBar, "the open bar registered as a live-edge rider");
  const w0 = widthOf(openBar.el);
  const closedBar = plotOf(panel).children.find((c: any) => c.tag === "rect" && c._attrs.fill === "#7aa2f7" && widthOf(c) !== w0 && !tp.riders.some((r: any) => r.el === c));
  assert.ok(closedBar, "a closed bar is in the plot group and is not a rider");
  const cw0 = widthOf(closedBar);
  advance(panel, 10);   // a whole pixel and a half at this zoom: well past TICK_MIN_PX
  const before = created;
  tick(panel);
  assert.equal(created, before, "the tick created no element: no rebuild");
  // the build itself stood a few ms past data.now (its own interpolation), so the drift is 10 s less that
  const px = tp.applied, want = 10 / geom0.winSec * geom0.plotW;
  assert.ok(px >= 1 && Math.abs(px - want) < 0.01, "the edge moved about 10 s worth of pixels: " + px + " vs " + want);
  assert.equal(plotOf(panel).getAttribute("transform"), "translate(" + (-px) + " 0)", "one translate on the plot group");
  assert.equal(widthOf(openBar.el), w0 + px, "the open bar grew by the drift: its right edge stays at the live now");
  assert.equal(widthOf(closedBar), cw0, "a closed bar's geometry is untouched");
  assert.ok(Math.abs((panel._geom.t1 - geom0.t1) - px / tp.k) < 1e-6, "the window geometry the handlers read follows the move");
  assert.equal(panel._geom.plotW, geom0.plotW);
  assert.equal(panel._holdReal, panel._geom.t1);
});

test("a move under TICK_MIN_PX writes nothing; the translate is measured from the build, not the last tick", () => {
  const panel = livePanel(liveData(), 43200);   // 12 h: 0.1 s is a thousandth of a pixel, far under the guard
  advance(panel, 0.1);
  const before = created;
  tick(panel);
  assert.equal(created, before);
  assert.equal(plotOf(panel).getAttribute("transform"), undefined, "nothing to write yet");
  advance(panel, 120);   // well past the guard now, and inside the edge's glide cap
  tick(panel);
  const px = panel._tickPlot.applied, want = 120 / panel._geom.winSec * panel._geom.plotW;
  assert.ok(px >= 1 && Math.abs(px - want) < 0.01, "about 120 s worth: " + px + " vs " + want);
  assert.equal(plotOf(panel).getAttribute("transform"), "translate(" + (-px) + " 0)");
});

test("a message glyph riding the live edge leaves the tick its full draw (a translate cannot express it)", () => {
  const data = liveData();
  data.messages = [{ id: "m1", fromId: SID2, toId: SID1, from: "api", to: "web", sent: NOW - 100, exec: NOW - 100, pending: true, text: "please review" }];
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true; panel._winSec = 3600; panel.fitted = true;
  panel.update(data);
  assert.equal(panel._tickPlot, null, "the pending message's dot lands AT the live edge, so the build hands the tick no translate handle");
  advance(panel, 10);
  const before = created;
  tick(panel);
  assert.ok(created > before, "the tick fell back to a full draw");
});

test("a drift reaching the gutter gap hands the tick back to a full draw (no frame has rebuilt since the build)", () => {
  const panel = livePanel(liveData(), 60);   // 1 min window: 3 s is tens of pixels
  const tp = panel._tickPlot;
  assert.ok(tp.maxDrift >= 2 && tp.maxDrift < 10, "the cap is the gutter gap, a few px");
  advance(panel, 3);
  const before = created;
  tick(panel);
  assert.ok(created > before, "the drift would have poked clamped content into the battery column: a full draw instead");
  assert.ok(panel._tickPlot, "the rebuild leaves a fresh handle");
  assert.equal(panel._tickPlot.applied, 0);
});

test("inside a collapsed trailing gap the edge does not move on screen: the tick writes nothing and redraws nothing", () => {
  // every lane quiet for longer than GAP_MIN (20 min): _buildCompressMap appends a trailing gap whose compressed
  // width is fixed, so compress(now) is constant while now advances — a real-seconds guard would redraw anyway
  const data = liveData();
  data.sessions[0].state = "idle";
  data.turns = { [SID1]: [turn("a", NOW - 5000, NOW - 4000)], [SID2]: [turn("c", NOW - 4800, NOW - 4500)] };
  const panel = livePanel(data, 7200);
  assert.equal(panel._tickPlot.trailing, true, "the build saw the trailing gap");
  advance(panel, 5);
  const before = created;
  tick(panel);
  assert.equal(created, before, "no rebuild");
  assert.equal(plotOf(panel).getAttribute("transform"), undefined, "no translate: compressed movement is zero");
  // at this window 5 s is under a pixel of REAL movement too; 30 s (a few px) would be a translate under a
  // real-seconds guard and 60 s would pass the drift cap and rebuild — inside the gap neither is real movement
  for (const sec of [30, 60]) {
    advance(panel, sec);
    tick(panel);
    assert.equal(created, before, "no rebuild at " + sec + " s");
    assert.equal(plotOf(panel).getAttribute("transform"), undefined, "no translate at " + sec + " s");
    assert.equal(panel._tickPlot.applied, 0);
  }
});

test("an open awaiting span and an open judging run ride the edge too; the lane chrome stays out of the plot group", () => {
  const data = liveData();
  data.sessions[1].state = "needsInput"; data.sessions[1].since = NOW - 120;   // an open blocked stripe on api, to the live edge
  data.judging = [{ sid: SID1, judge: "closer", t: NOW - 30, t1: NOW - 30, open: true, kind: "k", text: "" }];
  const getItem = g.localStorage.getItem;
  g.localStorage.getItem = (k: string) => (k === "romp:settings" ? JSON.stringify({ showTriageJudges: true }) : null);   // the band is off by default
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true; panel._winSec = 3600; panel.fitted = true;
  try { panel.update(data); } finally { g.localStorage.getItem = getItem; }
  const tp = panel._tickPlot;
  assert.ok(tp, "no live-edge message: the handle exists");
  const kinds = tp.riders.map((r: any) => r.el.tag + ":" + r.attr + ":" + (r.el._attrs["data-judge"] || r.el._attrs.fill || ""));
  assert.ok(kinds.some((k: string) => k.startsWith("rect:width:url(#vault-await-hatch)")), "the awaiting stripe is a rider: " + kinds.join(", "));
  assert.ok(tp.riders.some((r: any) => r.el._attrs["data-judge"] === "closer"), "the open judging run is a rider");
  // the gutter chrome and the rows' hit rects are on the svg itself, not in the moving group
  const plotTags = new Set(plotOf(panel).children.map((c: any) => c.tag));
  const names = panel.svg.children.filter((c: any) => c.tag === "text" && (c.textContent === "web" || c.textContent === "api"));
  assert.equal(names.length, 2, "lane names are direct svg children (fixed)");
  assert.ok(!plotOf(panel).children.some((c: any) => c.tag === "text" && (c.textContent === "web" || c.textContent === "api")), "and not in the plot group");
  assert.ok(plotTags.has("rect") && plotTags.has("line"), "bars and axis gridlines are in the plot group");
  const idx = panel.svg.children.indexOf(plotOf(panel));
  const rowHit = panel.svg.children.findIndex((c: any) => c.tag === "rect" && c._attrs.fill === "transparent" && c._attrs.x === 0);
  assert.ok(rowHit >= 0 && rowHit < idx, "the plot group paints over the rows' hit rects (a bar must take the hover)");
  const rail = panel.svg.children.findIndex((c: any) => c.tag === "line" && c._attrs.stroke === "#C0392B");
  assert.ok(rail >= 0 && rail < idx, "the judge band's rail is under the plot group, where it painted before");
  const lock = panel.svg.children.findIndex((c: any) => c.tag === "g" && c.children.some((k: any) => k.tag === "rect" && k._attrs.x === -2 && k._attrs.width === 19));
  assert.ok(lock >= 0 && idx < lock, "the lock toggle paints over the plot group, where it painted before");
  // the axis gridlines moved INTO the group, over the row hit rects they used to sit under: they take no pointer,
  // or a hover along a gridline would lose the row (and the bar) beneath it
  const grid = plotOf(panel).children.filter((c: any) => c.tag === "line" && c._attrs.y1 === panel._geom.top && c._attrs.x1 === c._attrs.x2);
  assert.ok(grid.length >= 2, "the axis gridlines are in the plot group: " + grid.length);
  for (const l of grid) assert.equal(l._attrs["pointer-events"], "none", "a gridline over the row hits takes no pointer");
});

test("held back off the live edge, the jump button paints over the plot group too", () => {
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = false; panel._pinned = false; panel._winSec = 3600; panel.fitted = true;
  panel._offSec = 120; panel._offDirty = true;   // a pan: the window's right edge sits 2 min behind now
  panel.update(liveData());
  assert.equal(panel._pinned, false, "the pan held");
  const kids = panel.svg.children;
  const plot = kids.findIndex((c: any) => c.tag === "g" && c._attrs["data-tl-plot"]);
  const jump = kids.findIndex((c: any) => c.tag === "g" && c.children.some((k: any) => k.tag === "rect" && k._attrs.width === 16 && k._attrs.height === 46));
  const lock = kids.findIndex((c: any) => c.tag === "g" && c.children.some((k: any) => k.tag === "rect" && k._attrs.x === -2 && k._attrs.width === 19));
  assert.ok(plot >= 0 && jump >= 0 && lock >= 0, "the plot group, the jump button and the lock are all drawn: " + [plot, jump, lock]);
  assert.ok(plot < jump && jump < lock, "the plot group is under both: " + [plot, jump, lock]);
});

test("a glyph anchored near the left edge hides once its anchor crosses it (where a full draw culls it), so no dot drifts onto the battery column", () => {
  // a 10-minute window: about a pixel a second, a 9 px drift cap (the battery column's gap). The closed turn on api
  // starts about 3 s inside the left edge: its prompt dot (DOT_R 6) already overhangs the edge by about 3 px at
  // build time, and translated by the cap it would paint several px over the battery and take its /compact pointer.
  const data = liveData();
  data.turns = { [SID1]: [turn("a", NOW - 300, NOW - 100)], [SID2]: [turn("c", NOW - 596.8, NOW - 500)] };
  const panel = livePanel(data, 600);
  const tp = panel._tickPlot, g = panel._geom;
  const bat = panel.svg.children.find((c: any) => c.tag === "rect" && c._attrs.width === 48 && c._attrs.height === 14 && c._attrs.fill !== "transparent");
  assert.ok(bat, "the lanes carry a context battery, so the gap left of the plot is COLGAP");
  const batRight = bat._attrs.x + 48;
  assert.equal(tp.maxDrift, 9);
  assert.equal(tp.left, g.ml);
  const dots = plotOf(panel).children.filter((c: any) => c.tag === "circle");
  const edgeDot = dots.reduce((a: any, b: any) => (a._attrs.cx < b._attrs.cx ? a : b));
  const farDot = dots.reduce((a: any, b: any) => (a._attrs.cx > b._attrs.cx ? a : b));
  const inside = edgeDot._attrs.cx - g.ml;
  assert.ok(inside > 2 && inside < 4, "the edge dot's anchor sits about 3 s inside the plot: " + inside + " px");
  assert.ok(tp.edge.some((e: any) => e.el === edgeDot), "the build listed it as an edge glyph");
  assert.ok(!tp.edge.some((e: any) => e.el === farDot), "a dot well inside the window is not listed");
  const paintedLeft = (dot: any) => dot._attrs.cx - tp.applied - 6;   // the dot's left edge on screen: anchor, less the translate, less DOT_R
  // a small drift, the anchor still inside the plot: visible, and its overhang still short of the battery
  advance(panel, inside / 2 / tp.k);
  let before = created; tick(panel);
  assert.equal(created, before, "no rebuild");
  assert.ok(tp.applied >= 1 && tp.applied < inside, "applied " + tp.applied);
  assert.equal(edgeDot.getAttribute("visibility"), undefined, "anchor inside: visible");
  assert.ok(paintedLeft(edgeDot) >= batRight, "and off the battery column");
  // just under the cap: the anchor has crossed the edge, so the dot is hidden, exactly where a full draw drops it
  advance(panel, (tp.maxDrift - 0.3) / tp.k);
  before = created; tick(panel);
  assert.equal(created, before, "still no rebuild: the drift is under the cap");
  assert.ok(tp.applied > tp.maxDrift - 1 && tp.applied < tp.maxDrift, "applied " + tp.applied);
  assert.equal(edgeDot.getAttribute("visibility"), "hidden", "the dot whose anchor crossed the edge is hidden (and takes no pointer)");
  assert.ok(edgeDot.getAttribute("visibility") === "hidden" || paintedLeft(edgeDot) >= batRight, "its painted left edge never reaches the battery");
  assert.equal(farDot.getAttribute("visibility"), undefined, "a dot inside the window stays");
});

test("a rider's floor applies to the grown extent: a just-opened bar grows as a full draw draws it, and a narrow judging run stays centred only until it is wider than JMARK_MINW", () => {
  const data = liveData();
  data.turns[SID1] = [turn("a", NOW - 300, NOW - 100), turn("b", NOW, NOW, { open: true })];   // opened this instant: drawn at the 2 px floor
  data.judging = [{ sid: SID1, judge: "closer", t: NOW, t1: NOW, open: true, kind: "k", text: "" }];
  const getItem = g.localStorage.getItem;
  g.localStorage.getItem = (k: string) => (k === "romp:settings" ? JSON.stringify({ showTriageJudges: true }) : null);
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true; panel._winSec = 600; panel.fitted = true;
  try { panel.update(data); } finally { g.localStorage.getItem = getItem; }
  const tp = panel._tickPlot;
  assert.ok(tp, "the handle exists");
  const bar = tp.riders.find((r: any) => r.el.tag === "rect" && r.attr === "width" && r.el._attrs.fill === "#7aa2f7");
  assert.ok(bar && bar.min === 2 && bar.base >= 0 && bar.base < 0.1, "the open bar's rider carries its un-clamped extent and the floor: " + JSON.stringify({ base: bar && bar.base, min: bar && bar.min }));
  assert.equal(widthOf(bar.el), 2, "drawn at the floor");
  const run = tp.riders.find((r: any) => r.el._attrs["data-judge"] === "closer");
  assert.ok(run && run.fn && run.base >= 0 && run.base < 0.1, "the open run's rider re-derives its placement from the un-centred extent");
  assert.equal(widthOf(run.el), 6, "drawn centred at JMARK_MINW");
  assert.ok(Math.abs(run.el._attrs.x - (run.x0 + (run.base - 6) / 2)) < 1e-9, "the build's centring");
  // a drift under the floor: the bar stays at 2 px (a full draw would also draw max(2, 1.2)); the run stays centred and its centre moves by half the drift
  advance(panel, 1.2 / tp.k);
  tick(panel);
  assert.ok(tp.applied > 1 && tp.applied < 1.3, "applied " + tp.applied);
  assert.equal(widthOf(bar.el), 2, "the floor holds: not 2 + the drift");
  assert.equal(widthOf(run.el), 6, "still narrower than JMARK_MINW: still centred");
  assert.ok(Math.abs(run.el._attrs.x - (run.x0 + (run.base + tp.applied - 6) / 2)) < 1e-9, "centred on the grown extent, as a full draw places it");
  // past the floor: the true extent, as a full draw would draw it, and the run's centring is gone
  advance(panel, 7 / tp.k);
  tick(panel);
  assert.ok(tp.applied > 6.5 && tp.applied < 7.1 && tp.applied < tp.maxDrift, "applied " + tp.applied);
  assert.ok(Math.abs(widthOf(bar.el) - (bar.base + tp.applied)) < 1e-9, "the un-clamped extent plus the drift: " + widthOf(bar.el));
  assert.ok(widthOf(bar.el) < 2 + tp.applied - 1.5, "not the floor plus the drift");
  assert.ok(Math.abs(run.el._attrs.x - run.x0) < 1e-9 && Math.abs(widthOf(run.el) - (run.base + tp.applied)) < 1e-9, "the run starts at its own x and spans to the edge");
  const hit = tp.riders.find((r: any) => r.el === run.el);
  assert.ok(hit, "one rider carries both the run and its hit");
});

test("the focus pulse is drawn into the live plot group, in the group's frame, so it rides the tick with its target", () => {
  const panel = livePanel(liveData(), 3600);
  const plot = plotOf(panel);
  advance(panel, 10);
  tick(panel);
  assert.ok(panel._tickPlot.applied > 0, "the plot is translated");
  const n0 = plot.children.length;
  panel._pulseFocus(SID2, NOW - 600, null);   // api's closed turn c: a prompt focus rings its start dot
  const ring = plot.children[plot.children.length - 1];
  assert.equal(plot.children.length, n0 + 1, "the ring went into the plot group, not onto the svg");
  assert.ok(ring.tag === "circle" && ring._attrs.stroke === "#ffd166");
  const dotC = plot.children.find((c: any) => c.tag === "circle" && c !== ring && c._attrs.fill === "#7aa2f7" && Math.abs(c._attrs.cx - ring._attrs.cx) < 1e-6);
  assert.ok(dotC, "positioned in the build's frame: it sits on the prompt dot it rings (both then move by the translate)");
  panel._pulseFocus(SID1, NOW - 900, { t: NOW - 900, end: NOW - 700 });   // a work focus outlines the bar
  const box = plot.children[plot.children.length - 1];
  assert.ok(box.tag === "rect" && box._attrs.stroke === "#ffd166" && box.parentNode === plot);
  const barA = plot.children.filter((c: any) => c.tag === "rect" && c._attrs.fill === "#7aa2f7").reduce((a: any, b: any) => (a._attrs.x < b._attrs.x ? a : b));
  assert.ok(Math.abs((box._attrs.x + 3) - barA._attrs.x) < 1e-6, "the outline hugs the bar in the group's frame");
  // with no live group (the loader wiped the svg) the pulse goes on the svg as before
  panel.drawMessage("no lanes");
  panel._pulseFocus(SID2, NOW - 600, null);
  assert.equal(panel.svg.children[panel.svg.children.length - 1].parentNode, panel.svg);
});

test("a gridline entering the window gets its full draw: the tick translates until the clock reaches the next axis tick, then hands back", () => {
  // Nothing is pre-drawn outside the window, so a gridline (and its clock) entering at the right edge is a
  // rebuild. The build dates it — the next axis tick past the window's edge — and the tick hands back the moment
  // the clock reaches it, instead of waiting for the next kernel frame (a quiet board sees one every 60 s).
  // The window ends 20 s before a whole hour: every step this zoom can pick has a tick there.
  const NOW2 = (Math.floor(NOW / 3600) + 1) * 3600 - 20;
  const panel = livePanel(liveData(NOW2), 3600);
  const tp = panel._tickPlot;
  assert.ok(Math.abs(tp.nextTick - (NOW2 + 20)) < 1e-6, "the build recorded when the next gridline enters: " + (tp.nextTick - NOW2) + " s on");
  assert.ok(20 * tp.k < tp.maxDrift, "…which is before the drift cap would hand back: " + 20 * tp.k + " px of " + tp.maxDrift);
  assert.equal(panel._tickTranslate(NOW2 + 19), true, "just before: a translate");
  assert.ok(Math.abs(tp.applied - 19 * tp.k) < 0.01, "applied " + tp.applied);
  assert.equal(panel._tickTranslate(NOW2 + 20), false, "the gridline is due: a full draw");
  // through the loop: the look draws, and the fresh build dates the next gridline a whole interval on
  advance(panel, 20.5);
  const before = created;
  tick(panel);
  assert.ok(created > before, "the loop did the full draw");
  assert.ok(panel._tickPlot && panel._tickPlot.nextTick >= NOW2 + 20 + 60, "the rebuild's next gridline is at least a minute on: " + (panel._tickPlot.nextTick - NOW2));
  assert.equal(panel._tickPlot.applied, 0);
});

test("sub-pixel looks add up: the drift is measured from the build, not the last look, so the edge advances once they reach the guard", () => {
  // a 12 h window: a 5 s look is a sixteenth of a pixel, under the translate's guard (TICK_MIN_PX, 0.15 px) every
  // time. Re-basing the drift on each look would leave the edge stuck at any zoom where one look is under the
  // guard; measured from the build, the looks accumulate and the first whose drift since the last write reaches
  // the guard writes it.
  const GUARD = 0.15;
  const panel = livePanel(liveData(), 43200);
  const tp = panel._tickPlot, step = 5 * panel._geom.plotW / panel._geom.winSec;   // px per 5 s look
  assert.ok(step < GUARD, "one look is under the guard, so no single look writes: " + step);
  const expected: number[] = [];   // the writes a drift measured from the build makes: each the first look a guard's worth past the last write
  for (let i = 1, last = 0; i <= 20; i++) { const d = i * step; if (d - last >= GUARD - 1e-9) { expected.push(d); last = d; } }
  assert.ok(expected.length >= 3, "20 looks cross the guard several times at this zoom: " + expected.length);
  const before = created, writes: number[] = [];
  for (let i = 1; i <= 20; i++) {
    advance(panel, 5 * i);
    tick(panel);
    if (tp.applied !== (writes.length ? writes[writes.length - 1] : 0)) writes.push(tp.applied);
  }
  assert.equal(created, before, "no look rebuilt");
  assert.ok(tp.applied >= 1, "the looks added up and the edge moved a whole pixel: applied " + tp.applied);
  assert.equal(writes.length, expected.length, "writes: " + writes.join(", ") + " vs " + expected.join(", "));
  writes.forEach((w, i) => assert.ok(Math.abs(w - expected[i]) < 0.05, "write " + i + ": " + w + " vs " + expected[i]));
  assert.equal(plotOf(panel).getAttribute("transform"), "translate(" + (-tp.applied) + " 0)");
});

test("the hover re-arms after a tick: the content moved under a pointer that did not", () => {
  const panel = livePanel(liveData(), 3600);
  const calls: any[] = [];
  const target = { __tlHoverIn: (e: any) => calls.push(e), parentNode: null };   // what elementFromPoint finds under the tracked pointer
  panel._ptr = { x: 500, y: 40 };
  panel.svg.ownerDocument = { elementFromPoint: () => target };
  advance(panel, 10);
  const before = created;
  tick(panel);
  assert.equal(created, before, "a translate, not a rebuild");
  assert.equal(calls.length, 1, "the element now under the pointer got its hover-in, once");
  assert.equal(calls[0].clientX, 500); assert.equal(calls[0].currentTarget, target);
});

test("a pending prompt's dot at the live edge leaves the build no handle either", () => {
  const data = liveData();
  data.turns[SID2].push(turn("p", NOW, NOW, { pending: true, open: true }));   // queued, not yet started: its dot is drawn at startAt = the live now
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true; panel._winSec = 3600; panel.fitted = true;
  panel.update(data);
  assert.equal(panel._tickPlot, null, "a pending prompt's dot rides the live edge: the build hands the tick no translate handle");
  advance(panel, 10);
  const before = created;
  tick(panel);
  assert.ok(created > before, "the tick fell back to a full draw");
});

test("a look the build left no handle for still honours LIVE_MIN_PX: a sub-pixel move redraws nothing", () => {
  // The full-draw fallback is paced by the same guard the translate has (review find, 2026-09-08): without it a
  // board with a pending prompt (no handle) rebuilt the whole svg on every look, every 2 s at a wide window,
  // where the edge moves a fraction of a pixel between looks and the redraw shows nothing new.
  const data = liveData();
  data.turns[SID2].push(turn("p", NOW, NOW, { pending: true, open: true }));
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true; panel._winSec = 43200; panel.fitted = true;   // 12 h: 5 s is a fraction of a pixel
  panel.update(data);
  assert.equal(panel._tickPlot, null, "no handle: every look is a full draw or nothing");
  assert.ok(5 / panel._geom.winSec * panel._geom.plotW < 1, "5 s is under a pixel at this zoom");
  advance(panel, 5);
  let before = created;
  tick(panel);
  assert.equal(created, before, "a sub-pixel move: no rebuild");
  advance(panel, 120);   // a few pixels on
  before = created;
  tick(panel);
  assert.ok(created > before, "a whole pixel moved: the full draw");
});

test("an un-arrived stub to a hidden lane spans to the live edge, so the build leaves the tick no handle", () => {
  const HID = "11111111-2222-3333-4444-aaaaaaaaaaa3";
  const data = liveData();
  data.sessions.push(sess(HID, "pool", "idle", NOW));
  data.turns[HID] = [turn("h", NOW - 500, NOW - 400)];
  data.views = { active: "untagged", hidden: [], tags: [{ id: "gx", name: "pool", color: "", members: [HID] }] };   // tag-hidden
  data.messages = [{ id: "m2", fromId: SID1, toId: HID, from: "web", to: "pool", sent: NOW - 200, exec: NOW - 200, hasExec: false, pending: true, text: "please review" }];
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true; panel._winSec = 3600; panel.fitted = true;
  panel.update(data);
  assert.equal(panel._vis.length, 2, "the tagged lane is hidden");
  const plot = panel.svg.children.find((c: any) => c.tag === "g" && c._attrs["data-tl-plot"]);
  assert.ok(plot, "the plot group is drawn");
  assert.ok(plot.children.some((c: any) => c.tag === "path" && c._attrs["stroke-dasharray"] === "1 4"), "the in-flight stub is drawn, dashed, in the group");
  assert.equal(panel._tickPlot, null, "…and it ends at the live edge: no translate handle");
  advance(panel, 10);
  const before = created;
  tick(panel);
  assert.ok(created > before, "the tick fell back to a full draw");
});

test("an open compacting span rides the edge too", () => {
  const data = liveData();
  data.sessions[1].state = "compacting"; data.sessions[1].compacting = [[NOW - 50, NOW, true]];   // the kernel's open-interval shape: the end at the payload clock, the open mark third
  const panel: any = new TimelinePanel(makeNode("div"));
  panel._lockNow = true; panel._pinned = true; panel._winSec = 3600; panel.fitted = true;
  panel.update(data);
  const tp = panel._tickPlot;
  assert.ok(tp, "the handle exists");
  const hatch = tp.riders.filter((r: any) => r.el._attrs.fill === "url(#vault-compact-hatch)");
  assert.equal(hatch.length, 1, "the cross-hatch stripe is a rider");
  const w0 = widthOf(hatch[0].el);
  assert.ok(w0 > 2, "drawn wider than the floor: " + w0);
  advance(panel, 10);
  tick(panel);
  assert.ok(tp.applied >= 1, "applied " + tp.applied);
  assert.equal(widthOf(hatch[0].el), w0 + tp.applied, "…and grows by the drift: its right edge stays at the live now");
});

test("the focus pulse's step stops once a rebuild detached its group", () => {
  const panel = livePanel(liveData(), 3600);
  const frames: any[] = [];
  const raf = g.requestAnimationFrame;
  g.requestAnimationFrame = (cb: any) => { frames.push(cb); return 1; };
  try {
    panel._pulseFocus(SID2, NOW - 600, null);
    assert.equal(frames.length, 1, "the pulse armed its first frame");
    const plot = plotOf(panel), ring = plot.children[plot.children.length - 1];
    assert.ok(ring.tag === "circle" && ring._attrs.stroke === "#ffd166" && ring.parentNode === plot);
    panel.draw();   // the wipe detaches the old group; the ring is still that group's child, off the svg
    assert.equal(ring.parentNode, plot); assert.equal(plot.parentNode, null, "the ring's group left the svg");
    frames[0](performance.now() + 100);   // mid-pulse: a live ring would arm its next frame here
    assert.equal(frames.length, 1, "no further frame: the step saw its group gone");
  } finally {
    g.requestAnimationFrame = raf;
  }
});

// The glide: a translate look sleeps only until the edge could have moved TICK_MIN_PX (within a frame at a narrow
// window, so the next animation frame; the rebuild look keeps its whole-pixel sleep), and writes the frame once the
// edge has moved that far, so at a ten-minute window the edge moves about a dozen times a second by 0.15 px instead
// of stepping a whole pixel about twice a second. Two costs a look this frequent would otherwise pay are tested with
// it: the visibility check reads the observer's word, not layout, and the hover re-arm (a hit test) runs once per
// whole pixel of drift.
test("a translate look sleeps TICK_MIN_PX's worth: within a frame at a one-minute window, a fraction of a second at ten; a look the build left no handle for sleeps the whole pixel", () => {
  const waits: number[] = [];
  const panel = livePanel(liveData(), 600);
  panel._sleep = (ms: number) => { waits.push(ms); };
  advance(panel, 1.5);   // about 1.35 px at this zoom: past a whole pixel, well under the drift cap
  const before = created;
  panel._tickLive();
  assert.equal(created, before, "the look was a translate");
  assert.ok(panel._tickPlot.applied >= 1, "applied " + panel._tickPlot.applied);
  const want = Math.round(0.15 * panel._geom.winSec / panel._geom.plotW * 1000);   // ms per 0.15 px at this zoom
  assert.ok(want > 100 && want < 300, "a ten-minute window over this plot: about a sixth of a second per 0.15 px: " + want);
  assert.deepEqual(waits, [want], "the next look comes when the edge could have moved TICK_MIN_PX, not a whole pixel");
  assert.ok(want < panel._liveWaitMs(), "shorter than the rebuild's wait: " + panel._liveWaitMs());
  // a one-minute window: 0.15 px is under a frame, so the look is on the next animation frame
  const narrow = livePanel(liveData(), 60);
  const nwaits: number[] = []; narrow._sleep = (ms: number) => { nwaits.push(ms); };
  advance(narrow, 0.05);   // about half a pixel
  narrow._tickLive();
  assert.ok(narrow._tickPlot.applied > 0.15 && narrow._tickPlot.applied < 1, "a sub-pixel translate: " + narrow._tickPlot.applied);
  assert.equal(nwaits.length, 1);
  assert.ok(nwaits[0] <= 17, "within a frame: " + nwaits[0] + " ms");
  // a build that left no handle (a glyph rides the live edge): the look is a full draw or nothing, and the next
  // look waits a whole pixel's worth (_liveWaitMs), as before
  const data = liveData();
  data.messages = [{ id: "m1", fromId: SID2, toId: SID1, from: "api", to: "web", sent: NOW - 100, exec: NOW - 100, pending: true, text: "please review" }];
  const held: any = new TimelinePanel(makeNode("div"));
  held._lockNow = true; held._pinned = true; held._winSec = 600; held.fitted = true;
  held.update(data);
  assert.equal(held._tickPlot, null, "no handle");
  const hwaits: number[] = []; held._sleep = (ms: number) => { hwaits.push(ms); };
  advance(held, 1.5);
  held._tickLive();
  assert.deepEqual(hwaits, [held._liveWaitMs()], "the rebuild look sleeps the whole pixel");
  assert.ok(hwaits[0] > want, hwaits[0] + " vs " + want);
});

test("at a ten-minute window a fraction of a second is a visible move: the translate is written under a pixel", () => {
  const panel = livePanel(liveData(), 600);
  const tp = panel._tickPlot;
  advance(panel, 0.6);
  const want = 0.6 * panel._geom.plotW / panel._geom.winSec;
  assert.ok(want > 0.3 && want < 1, "the move is a fraction of a pixel: " + want);
  const before = created;
  tick(panel);
  assert.equal(created, before, "a translate, not a rebuild");
  assert.ok(tp.applied >= 0.15 && tp.applied < 1 && Math.abs(tp.applied - want) < 0.05, "written under a pixel: " + tp.applied + " vs " + want);
  assert.equal(plotOf(panel).getAttribute("transform"), "translate(" + (-tp.applied) + " 0)");
  // under the guard nothing is written: at a wide window the edge idles between the frames that move it
  const wide = livePanel(liveData(), 43200);   // 12 h: a 5 s look is about a sixteenth of a pixel
  advance(wide, 5);
  tick(wide);
  assert.equal(wide._tickPlot.applied, 0, "under TICK_MIN_PX: no write");
  assert.equal(plotOf(wide).getAttribute("transform"), undefined);
});

test("a translate look reads no layout once the pane's observer has spoken: its word gates the look and re-arms the loop", () => {
  const savedIO = g.IntersectionObserver, raf = g.requestAnimationFrame;
  let io: any = null;
  g.IntersectionObserver = class { cb: any; constructor(cb: any) { this.cb = cb; io = this; } observe() {} disconnect() {} };
  let armed = 0;
  try {
    const panel = livePanel(liveData(), 600);
    assert.ok(panel._io && io, "the wrap is observed");
    let reads = 0;
    Object.defineProperty(panel.wrap, "offsetParent", { get() { reads++; return {}; }, configurable: true });
    const sleeps: number[] = []; panel._sleep = (ms: number) => { sleeps.push(ms); };
    g.requestAnimationFrame = () => { armed++; return 1; };
    // before the observer's first word the look reads offsetParent, as it always did
    advance(panel, 0.6); panel._tickLive();
    assert.equal(reads, 1, "no word yet: the one read");
    assert.equal(sleeps.length, 1, "a translate: the next look is scheduled");
    // the observer speaks: from here a look reads no layout
    io.cb([{ isIntersecting: true }]);
    reads = 0; sleeps.length = 0;
    for (let i = 1; i <= 5; i++) { advance(panel, 0.6 + 0.3 * i); panel._tickLive(); }
    assert.equal(reads, 0, "five translate looks, no layout read");
    assert.equal(sleeps.length, 5, "each scheduled the next look");
    assert.ok(panel._tickPlot.applied > 1, "and the edge moved: " + panel._tickPlot.applied);
    // a hidden tab, the word still saying in view: the look stops without a read, as the hidden branch always did
    sleeps.length = 0; let applied = panel._tickPlot.applied;
    g.document.visibilityState = "hidden";
    advance(panel, 2.3); panel._tickLive();
    assert.equal(sleeps.length, 0, "hidden tab: no next look scheduled");
    assert.equal(panel._tickPlot.applied, applied, "nothing written for a tab nobody sees");
    assert.equal(reads, 0, "and no layout read to find that out");
    delete g.document.visibilityState;
    // out of view (display:none, scrolled away): the look stops without a read; the observer's next word re-arms it
    io.cb([{ isIntersecting: false }]);
    sleeps.length = 0; applied = panel._tickPlot.applied;
    advance(panel, 2.5); panel._tickLive();
    assert.equal(sleeps.length, 0, "stopped: no next look scheduled");
    assert.equal(panel._tickPlot.applied, applied, "nothing written for a pane nobody sees");
    assert.equal(reads, 0, "and no layout read to find that out");
    armed = 0;
    io.cb([{ isIntersecting: true }]);
    assert.equal(armed, 1, "back in view: the release re-armed the loop");
  } finally { g.IntersectionObserver = savedIO; g.requestAnimationFrame = raf; delete g.document.visibilityState; }
});

test("the hover re-arms once per whole pixel of drift, not on every sub-pixel write", () => {
  const panel = livePanel(liveData(), 600);
  const tp = panel._tickPlot;
  let hits = 0;
  panel._ptr = { x: 500, y: 40 };
  panel.svg.ownerDocument = { elementFromPoint: () => { hits++; return null; } };
  const step = 0.3, looks = 10;   // 3 s: about 2.7 px, under the drift cap
  assert.ok(looks * step * panel._geom.plotW / panel._geom.winSec < tp.maxDrift, "the run stays under the drift cap");
  let writes = 0, last = 0, crossings = 0;
  const before = created;
  for (let i = 1; i <= looks; i++) {
    advance(panel, step * i);
    tick(panel);
    if (tp.applied !== last) { writes++; if (Math.floor(tp.applied) !== Math.floor(last)) crossings++; last = tp.applied; }
  }
  assert.equal(created, before, "every look a translate");
  assert.equal(writes, looks, "every look wrote: each is about a quarter of a pixel, past TICK_MIN_PX");
  assert.ok(crossings >= 2, "the drift crossed two whole pixels: " + last);
  assert.equal(hits, crossings, "one hit test per whole pixel crossed, none for the sub-pixel writes between");
});

test("once the clock has reached the interpolation cap the edge stands still, and the translate look sleeps the rebuild's wait, not TICK_MIN_PX's", () => {
  const waits: number[] = [];
  const panel = livePanel(liveData(), 600);
  panel._sleep = (ms: number) => { waits.push(ms); };
  const glide = Math.round(0.15 * panel._geom.winSec / panel._geom.plotW * 1000);   // the translate's sleep while the edge moves
  advance(panel, 200);   // a kernel quiet for longer than the cap (150 s): the edge has stopped at it
  const before = created;
  panel._tickLive();     // the drift since the build is far past the drift cap: this look is a full draw
  assert.ok(created > before, "the look rebuilt");
  assert.deepEqual(waits, [panel._liveWaitMs()], "a still edge: the rebuild's wait, not " + glide);
  waits.length = 0;
  const applied = panel._tickPlot.applied, before2 = created;
  panel._tickLive();
  assert.equal(created, before2, "a translate look");
  assert.equal(panel._tickPlot.applied, applied, "nothing to write: the edge is at the cap");
  assert.deepEqual(waits, [panel._liveWaitMs()], "and it sleeps the rebuild's wait, not TICK_MIN_PX's worth (" + glide + ")");
  assert.ok(waits[0] > glide);
  // the next frame re-anchors the clock: the edge moves again and the look sleeps TICK_MIN_PX's worth
  panel.update(liveData(NOW + 200)); panel._stopLiveTick();
  waits.length = 0;
  advance(panel, 0.6); panel._tickLive();
  assert.ok(panel._tickPlot.applied > 0.15, "moving again: " + panel._tickPlot.applied);
  assert.deepEqual(waits, [glide], "the glide's sleep");
});
