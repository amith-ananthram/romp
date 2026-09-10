// THE SESSIONS & TAGS DIALOG WITH MANY TAGS (the user 2026-09-09, whose ten tags filled the dialog: each
// tag row carried the whole identity palette inline, twelve swatches in two rows, and "pane filters" was
// a five-pane matrix of every tag, so "the sessions", the working area, showed two rows under the fold).
// Now a tag row is one line (pill, delete, rename, ONE colour dot), the palette opens on demand as a
// popover anchored to the dot, the filters fold to one summary line per pane (open on the caption's
// caret, remembered for the page), the tag table caps its height and scrolls within itself, and the
// sessions table takes the rest. The review of 2026-09-09 added: the popover placed in the dot's own
// document and closing when its dot moves (a scroll under it, a resize) or when focus leaves it (Tab,
// focusout), the current swatch marked apart from the focus ring, the table's scroll surviving a repaint,
// a drag past the table's edge landing only on a visible row, the folded summary naming only tags that
// exist, [+ New tag] under the table rather than inside its scroll, a held dot taking no click, the
// pick re-resolving its tag by union key, a repaint that holds the popover's dot closing the popover, and
// the popover placed untranslated in the dialog's own document when the view runs in a frame. EXECUTED
// over the house fake-DOM shim with the real
// TimelinePanel: the dialog is opened, the dot clicked, a swatch picked, and the posted write is the one
// the inline swatches posted. Synthetic ids and names only.
import { test, beforeEach } from "node:test";
import * as assert from "node:assert/strict";
import * as path from "node:path";
import { createRequire } from "node:module";

// NEVER assert.equal / notEqual / deepEqual two shim nodes (or a node against null): on failure node's
// assert appends an actual/expected diff that walks the node graph, and the parentNode/children cycles
// make that walk grow without bound (a 30 GB stall and a nameless kill, review find 2026-09-09). Compare
// identity with `same(a, b, msg)` below, or compare a projection (keys, text, attributes).
// a node appended under another document's node is ADOPTED with its subtree, as a browser adopts it: its
// ownerDocument is the new parent's (the dialog is appended to the host document before it is built, and
// _menuHost reads the anchor's document to skip the frame translation for a node already living there)
function adopt(n: any, doc: any) { n._ownerDoc = doc; for (const c of n.children || []) adopt(c, doc); }
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, _text: "", parentNode: null, value: "",
    _ownerDoc: null,
    get ownerDocument() { return this._ownerDoc || g.document; },
    set ownerDocument(d: any) { this._ownerDoc = d; },
    get textContent() { return this._text; },
    set textContent(v: any) { this._text = v == null ? "" : String(v); for (const c of this.children) c.parentNode = null; this.children.length = 0; },
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) {
      if (c.parentNode) { const i = c.parentNode.children.indexOf(c); if (i >= 0) c.parentNode.children.splice(i, 1); }
      c.parentNode = n; this.children.push(c);
      if (c.ownerDocument !== n.ownerDocument) adopt(c, n.ownerDocument);
      return c;
    },
    insertBefore(c: any, ref: any) {
      c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c);
      if (c.ownerDocument !== n.ownerDocument) adopt(c, n.ownerDocument);
      return c;
    },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) { this.children.splice(i, 1); c.parentNode = null; } return c; },
    get firstChild() { return this.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    // `_listeners[t]` is the listener added LAST and still registered; removal is by function, so a listener
    // hung under another for a while (the drag's scroll re-rank under the popover's scroll closer) gives the
    // slot back to the one that stays when it comes down
    _listeners: {} as any, _stacks: {} as any,
    addEventListener(t: string, fn: any) { (n._stacks[t] = n._stacks[t] || []).push(fn); n._listeners[t] = fn; },
    removeEventListener(t: string, fn?: any) {
      const a = n._stacks[t] || []; const i = fn ? a.indexOf(fn) : a.length - 1;
      if (i >= 0) a.splice(i, 1);
      if (a.length) n._listeners[t] = a[a.length - 1]; else delete n._listeners[t];
    },
    setPointerCapture() {}, releasePointerCapture() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    // geometry is a test input: a node's own `_rect`, else the module-level `g.__rectOf(node)` hook (so a
    // test can move a dot or a row without holding the node the rebuild will create), else one flat rect
    getBoundingClientRect() { return n._rect || (g.__rectOf ? g.__rectOf(n) : null) || { width: 200, height: 20, left: 0, top: 0, right: 200, bottom: 20 }; },
    // scrollTop clamps to `g.__scrollMax` (Infinity unless a test says the box no longer scrolls), as a
    // browser clamps a write on a box whose content fits
    _scrollTop: 0,
    get scrollTop() { return this._scrollTop; },
    set scrollTop(v: any) { const max = g.__scrollMax == null ? Infinity : g.__scrollMax; this._scrollTop = Math.max(0, Math.min(Number(v) || 0, max)); },
    closest() { return null; },
    // focus is OBSERVABLE: it records the active element on the fake document (the popover focuses the
    // current swatch on open and the dot again on close)
    focus() { g.document.activeElement = n; }, select() {},
    setSelectionRange() {}, selectionStart: 0, selectionEnd: 0,
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  createTextNode(text: string) { const n = makeNode("#text"); n.textContent = text; return n; },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener() {}, removeEventListener() {},
  activeElement: null,
};
// a real store, so the feed row's echo (romp:feedTags-set) is readable back the way the dialog reads it
const stored: Record<string, string> = {};
g.localStorage = { getItem: (k: string) => (k in stored ? stored[k] : null), setItem: (k: string, v: any) => { stored[k] = String(v); }, removeItem: (k: string) => { delete stored[k]; } };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)", fontFamily: "sans-serif" });
g.requestAnimationFrame = () => 0;
g.setTimeout = (fn: any) => { try { fn(); } catch { /* focus on a fake node */ } return 0; };
// the window's listeners are RECORDED (the popover hangs a resize closer on its window and takes it down on close)
const winListeners: Record<string, any[]> = {};
g.addEventListener = (t: string, fn: any) => { (winListeners[t] = winListeners[t] || []).push(fn); };
g.removeEventListener = (t: string, fn: any) => { const a = winListeners[t] || []; const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); };
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 1300;

// the two host bridges, recording every post (the ack test's shape)
const posted: any[] = [];
g.__rompTimelineSetViews = (v: any, writeId: string, edited: string[]) => posted.push({ kind: "views", v: JSON.parse(JSON.stringify(v)), writeId, edited });
g.__rompTimelineTagEdit = (writeId: string, e: any) => posted.push({ kind: "tag", e: Object.assign({ writeId }, JSON.parse(JSON.stringify(e))) });

const VIEW_PATH = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel, viewTagUnion, lensSummary } = createRequire(__filename)(VIEW_PATH);

const now = 1_781_000_000;
// the twelve identity colours the kernel serves (any twelve distinct hexes; the dialog draws what it is given)
const PALETTE = ["#1EA1EB", "#54B204", "#4EA8A9", "#DD42FF", "#E87221", "#4EC9B0", "#E0AF68", "#F7768E", "#7AA2F7", "#9ECE6A", "#BB9AF7", "#FF9E64"];
const sess = (id: string, name: string, color: string) => ({
  id, name, color, state: "working", live: true, model: "Opus", effort: "high",
  context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
});
const copy = (v: any) => JSON.parse(JSON.stringify(v));
// three tags, each pane holding a different filter, so the folded summary has something to say
const THREE = {
  active: "all", at: 100, seq: 1000,
  actives: { chat: { tags: ["gamma", "alpha"] }, timeline: { all: true }, outline: { none: true } },
  tags: [
    { id: "g1", name: "alpha", color: "#DD42FF", members: ["s1"], mtime: 100 },
    { id: "g2", name: "beta", color: "#4EC9B0", members: ["s2"], mtime: 100 },
    { id: "g3", name: "gamma", color: "#E0AF68", members: [], mtime: 100 },
  ],
};
const THREE_SESSIONS = [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7"), sess("s3", "tests", "#9ece6a")];
// thirty tags and forty sessions: the scale every tag or session surface is checked against (CLAUDE.md)
const NAMES30 = Array.from({ length: 30 }, (_, i) => "t" + String(i + 1).padStart(2, "0"));
const THIRTY = {
  active: "all", at: 100, seq: 1000,
  actives: { chat: { tags: NAMES30.slice().reverse() }, timeline: { all: true }, outline: { none: true } },
  tags: NAMES30.map((name, i) => ({ id: "g" + (i + 1), name, color: PALETTE[i % PALETTE.length], members: i < 20 ? ["s" + (i + 1)] : [], mtime: 100 })),
};
const FORTY_SESSIONS = Array.from({ length: 40 }, (_, i) => sess("s" + (i + 1), "job-" + String(i + 1).padStart(2, "0"), PALETTE[i % PALETTE.length]));
// a remote half of "beta", homed on another kernel (tagorder-drag's fixture shape)
const REMOTE_BETA = { id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "beta", color: "#7aa2f7", members: ["m1"] };

function drawnPanel(views: any, sessions: any[]): any {
  posted.length = 0;
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({ now, sessions, turns: {}, messages: [], judging: [], views: copy(views), palette: PALETTE.slice() });
  panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: views.seq });
  return panel;
}
function walk(x: any, out: any[] = []): any[] { for (const c of x.children || []) { out.push(c); walk(c, out); } return out; }
const textOf = (n: any): string => (n.textContent || "") + (n.children || []).map(textOf).join("");
const styleOf = (n: any): string => String(n._attrs.style || "");
// node identity, never assert.equal (see the shim's note)
const same = (a: any, b: any, msg: string) => assert.ok(a === b, msg);
const dots = (panel: any) => walk(panel._viewsDialog).filter((n) => n.dataset.tagDot);
const dotFor = (panel: any, key: string) => dots(panel).find((n) => n.dataset.tagDot === key);
const swatches = (pop: any) => walk(pop).filter((n) => n._attrs.role === "radio");
// the popovers in the HOST document (the dialog's own body), beside the dialog rather than in it, by key
const popsInBody = () => g.document.body.children.filter((n: any) => n._attrs.role === "dialog");
const popKeys = () => popsInBody().map((p: any) => p._key);
const caption = (panel: any) => walk(panel._viewsDialog).find((n) => String(n.textContent).startsWith("pane filters"));
const TGRID_PRE = "display:grid;grid-template-columns:max-content max-content max-content 1fr;";
const tgridOf = (panel: any) => walk(panel._viewsDialog).find((n) => styleOf(n).startsWith(TGRID_PRE));
const cardOf = (panel: any) => panel._viewsDialog.children[0];
// the filter pills: the shared tag chip's signature (T321) plus the pointer they alone carry (the tag row's pill is the
// same chip without it; the lane chips diverge after the size)
const CHIP_PRE = "display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:9px;font-size:0.82em;border:1px solid ";
const chips = (panel: any) => walk(panel._viewsDialog).filter((n) => styleOf(n).startsWith(CHIP_PRE) && styleOf(n).includes("cursor:pointer;"));
// the folded summary: the pane label's row, [label, summary]
const paneLines = (panel: any) => {
  const out: Record<string, any> = {};
  for (const row of walk(panel._viewsDialog)) {
    const lb = row.children && row.children[0];
    if (lb && ["All surfaces", "Chat", "Sessions", "Outline", "Feed"].includes(lb.textContent) && styleOf(lb).includes("flex:0 0 88px")) out[lb.textContent] = row;
  }
  return out;
};
function tagOps() { return posted.filter((p) => p.kind === "tag").map((p) => p.e); }
function openDialog(views: any = THREE, sessions: any[] = THREE_SESSIONS) {
  const panel = drawnPanel(views, sessions);
  panel._openViewsDialog(null);
  assert.ok(panel._viewsDialog, "the dialog opened");
  return panel;
}

// one failure must not take the tests after it down with it (review find, 2026-09-09: a popover a failed
// assertion left in the shared body failed three later tests on popsInBody()): before every test the body is
// emptied, the window's listeners, the feed row's echo, the geometry hooks, the remote bridge, the frame
// stand-ins and the recorded posts are reset, and the pane filters' page-lifetime fold is put back to folded
// through a throwaway dialog (the module variable has no other door)
beforeEach(() => {
  for (const c of g.document.body.children) c.parentNode = null;
  g.document.body.children.length = 0;
  g.document.activeElement = null;
  for (const k of Object.keys(winListeners)) delete winListeners[k];
  delete stored["romp:feedTags-set"];
  g.__rectOf = null; g.__scrollMax = undefined;
  delete g.__rompTimelineEditTag;
  delete g.frameElement; delete g.parent;
  posted.length = 0;
  const p = drawnPanel(THREE, THREE_SESSIONS);
  p._openViewsDialog(null);
  if (caption(p).textContent.endsWith(" \u25BE")) caption(p)._listeners.click();
  p._closeViewsDialog();
});

test("executed: lensSummary says what a pane shows, in the user's tag order, only tags that exist, cut with a count when long", () => {
  const order = ["alpha", "beta", "gamma"];
  assert.equal(lensSummary({ all: true }, order), "All");
  assert.equal(lensSummary(null, order), "All", "no lens yet reads as All, like lensAll");
  assert.equal(lensSummary({ none: true }, order), "no tags");
  assert.equal(lensSummary({ tags: ["gamma", "alpha"] }, order), "alpha, gamma", "the USER'S order, not the order the chips were clicked in");
  assert.equal(lensSummary({ tags: ["beta"], none: true }, order), "beta, no tags", "'no tags' last, as lensLabel puts it");
  // a name the union no longer lists (a rename or a delete left it in the lens) is not claimed: the open
  // matrix draws no chip for it, so the two forms agree (review find, 2026-09-09)
  assert.equal(lensSummary({ tags: ["zeta", "alpha"] }, order), "alpha", "a name the union no longer lists is left out");
  assert.equal(lensSummary({ tags: ["zeta"] }, order), "none of the tags here", "a lens left with nothing says so: the pane shows nothing");
  assert.equal(lensSummary({ tags: ["zeta"], none: true }, order), "no tags", "'no tags' still counts");
  assert.equal(lensSummary({ tags: ["zeta", "alpha"] }), "zeta, alpha", "without an order there is nothing to filter by: every name, as given");
  assert.equal(lensSummary({ tags: NAMES30.slice().reverse() }, NAMES30), "t01, t02, t03, t04, t05, t06 +24 more", "six names, then the count");
  assert.equal(lensSummary({ tags: NAMES30.slice(0, 6) }, NAMES30), "t01, t02, t03, t04, t05, t06", "six is not long");
  assert.equal(lensSummary({ tags: NAMES30.slice(0, 7) }, NAMES30, 3), "t01, t02, t03 +4 more", "the cut is a parameter");
  assert.equal(lensSummary({ tags: NAMES30.slice().reverse() }, NAMES30, Infinity), NAMES30.join(", "), "Infinity: the full list (the hover)");
});

test("executed: a tag row is one line: the pill, delete, rename and ONE colour dot; no inline swatches anywhere; the height budget's boxes", () => {
  const panel = openDialog();
  const ds = dots(panel);
  assert.deepEqual(ds.map((d) => d.dataset.tagDot), ["g1", "g2", "g3"], "one dot per tag row, keyed by the tag");
  for (const [d, tg] of ds.map((d, i) => [d, THREE.tags[i]] as const)) {
    assert.ok(styleOf(d).includes("background:" + tg.color + ";"), "the dot wears the tag's own colour");
    assert.equal(d._attrs.role, "button");
    assert.equal(d._attrs.tabindex, "0", "reachable by keyboard");
    assert.equal(d._attrs["aria-haspopup"], "dialog");
    assert.equal(d._attrs["aria-expanded"], "false");
    assert.ok(String(d._attrs.title).includes(tg.name), "the hover names the tag");
  }
  assert.equal(walk(panel._viewsDialog).filter((n) => n._attrs.role === "radio").length, 0, "no swatch in any row");
  assert.equal(popsInBody().length, 0, "...and no popover until a dot is clicked");
  // the row still drags to reorder (the pill cell keeps its handle) and still carries delete and rename
  const cells = walk(panel._viewsDialog).filter((n) => n._tname);
  assert.deepEqual(cells.map((c) => c._tname), ["alpha", "beta", "gamma"]);
  assert.ok(cells.every((c) => c._listeners.pointerdown), "the reorder drag stays on the pill");
  assert.equal(walk(panel._viewsDialog).filter((n) => n.textContent === "delete").length, 3);
  assert.equal(walk(panel._viewsDialog).filter((n) => n.textContent === "rename").length, 3);
  // THE HEIGHT BUDGET (review, 2026-09-09): the table caps its share and scrolls within itself, gives way
  // only once the sessions are at their floor, and keeps a floor of its own (three rows here, so it does not
  // shrink at all: its natural height is its rows), with room inside the clip for the rings; the card
  // scrolls past the floors. The floors' arithmetic is the next test's.
  const tgrid = tgridOf(panel);
  assert.ok(tgrid, "the tag table");
  assert.ok(styleOf(tgrid).includes("padding:4px 0 4px 4px;margin:-2px 0 2px -4px;"), "room inside the scroll clip for the pills' and dots' rings, the layout unmoved");
  assert.ok(styleOf(tgrid).includes("flex:0 0 auto;max-height:30vh;overflow-y:auto;overflow-anchor:none;"), "capped at 30vh, scrolling within, three rows never shrinking, and out of scroll anchoring (the drag's cue lift moves 2px of content the browser must not scroll to follow; the browser legs drive the case, this pins the token where CI runs): " + styleOf(tgrid));
  assert.ok(!styleOf(tgrid).includes("min-height"), "three rows need no floor under them: their natural height is the floor");
  assert.ok(tgrid._listeners.scroll, "the table's scroll is listened to (it closes the popover when the dot moves)");
  const gridBox = walk(panel._viewsDialog).find((n) => styleOf(n).startsWith("flex:1 1000 auto;min-height:") && styleOf(n).endsWith("px;overflow-y:auto;"));
  assert.ok(gridBox, "the sessions box gives way first (the thousandfold shrink) down to a floor under its rows");
  const card = cardOf(panel);
  assert.ok(styleOf(card).includes("max-height:90vh;overflow-y:auto;overflow-x:hidden;"), "the card scrolls as a whole past the floors instead of clipping");
  assert.ok(card._listeners.scroll, "...and its scroll is listened to as well");
  // [+ New tag] is the card's own child UNDER the table, never a row inside its scroll (review find F6)
  const nt = walk(panel._viewsDialog).find((n) => n.textContent === "+ New tag");
  assert.ok(nt, "the New tag row");
  same(nt.parentNode.parentNode, card, "the row hangs on the card...");
  assert.ok(!walk(tgrid).includes(nt), "...not in the table");
  same(card.children[card.children.indexOf(tgrid) + 1], nt.parentNode, "directly under the table");
  assert.ok(styleOf(nt.parentNode).startsWith("flex:0 0 auto;"), "a fixed row of the card: it never scrolls away");
  panel._closeViewsDialog();
});

// the sessions grid, by its own style signature (the row cells' parent)
const GRID_PRE = "display:grid;grid-template-columns:max-content max-content 1fr;";
const gridOf = (panel: any) => walk(panel._viewsDialog).find((n) => styleOf(n).startsWith(GRID_PRE));
const gridBoxOf = (panel: any) => gridOf(panel).parentNode;
// a grid cell's row: the count of the row-leading cells among its siblings up to it (the `_tname` pill in the tag
// table, the `_sid` name in the sessions grid), so a test places rows by index without holding the nodes a
// rebuild replaces
const rowIdx = (n: any, lead: string) => { let k = 0; for (const c of n.parentNode.children) { if (c[lead]) k++; if (c === n) break; } return k - 1; };
// a tag row placed by its index: 24px tall at a 28px pitch (row-gap 4), every cell of the row in its track.
// A later review find: one rect for every cell of the table let a floor measured off the WHOLE table (a thirty-row
// floor, the table never shrinking) pass here, with only the browser legs, which skip on CI, to catch it
const tagRect = (i: number) => ({ left: 0, top: 100 + 28 * i, right: 200, bottom: 124 + 28 * i, width: 200, height: 24 });

test("executed: the floors are measured off the rendered rows: a table of at most three rows keeps its natural height, a taller one three rows' worth; the sessions box min(live, 4) rows, by the live count and not the search hits", () => {
  // review find, 2026-09-09: a constant per row (22px, 24px) is not the row height, which is the font's:
  // one tag showed 6px of blank under its row, three rows at the floor lost 7px of the third, one live
  // session sat in a 96px box. GEOMETRY IS A TEST INPUT here: tag rows lay out 24px tall (row-gap 4), placed
  // by their index (tagRect), session rows 18px (row-gap 3), every cell of a row seated in its track
  g.__rectOf = (n: any) => {
    const par = n.parentNode ? styleOf(n.parentNode) : "";
    if (par.startsWith(TGRID_PRE)) return tagRect(rowIdx(n, "_tname"));
    if (par.startsWith(GRID_PRE)) return { left: 0, top: 300, right: 200, bottom: 318, width: 200, height: 18 };
    return null;
  };
  try {
    // thirty tags: three rows' worth, 3 x 24 + 2 x 4, and the table may shrink to it
    const p30 = openDialog(THIRTY, FORTY_SESSIONS);
    assert.ok(styleOf(tgridOf(p30)).includes("flex:0 1 auto;min-height:80px;max-height:30vh;overflow-y:auto;overflow-anchor:none;"), "thirty rows: a floor of three at the rendered height, the table out of scroll anchoring: " + styleOf(tgridOf(p30)));
    // forty live sessions: four rows' worth, 4 x 18 + 3 x 3
    assert.equal(styleOf(gridBoxOf(p30)), "flex:1 1000 auto;min-height:81px;overflow-y:auto;", "forty live: a floor of four rows at the rendered height");
    // typing a query that hides most rows, or every row, leaves the box as it was: the floor follows the live
    // count, not the hits, so the box never resizes under the user's typing
    const q = walk(p30._viewsDialog).find((n) => n.tag === "input" && n.placeholder === "search name or host…");
    q.value = "job-01"; q._listeners.input();
    assert.equal(walk(p30._viewsDialog).filter((n) => n._sid).length, 1, "one hit");
    assert.equal(styleOf(gridBoxOf(p30)), "flex:1 1000 auto;min-height:81px;overflow-y:auto;", "one hit shown, the floor unchanged");
    q.value = "nothing-here"; q._listeners.input();
    assert.equal(walk(p30._viewsDialog).filter((n) => n._sid).length, 0, "no hit");
    assert.equal(styleOf(gridBoxOf(p30)), "flex:1 1000 auto;min-height:81px;overflow-y:auto;", "no hit shown, the floor unchanged");
    // a repaint with that query set has no row to measure: the row height the last build measured serves
    p30._viewsDialogBuild();
    assert.equal(walk(p30._viewsDialog).filter((n) => n._sid).length, 0, "the query survived the repaint, still no hit");
    assert.equal(styleOf(gridBoxOf(p30)), "flex:1 1000 auto;min-height:81px;overflow-y:auto;", "a repaint with every row hidden keeps the four-row floor");
    p30._closeViewsDialog();
    // four tags: the first count that can shrink, to three rows' worth
    const four = copy(THREE); four.tags = four.tags.concat([{ id: "g4", name: "delta", color: "#F7768E", members: [], mtime: 100 }]);
    const p4 = openDialog(four);
    assert.ok(styleOf(tgridOf(p4)).includes("flex:0 1 auto;min-height:80px;"), "four rows may shrink to three: " + styleOf(tgridOf(p4)));
    p4._closeViewsDialog();
    // three, one and no tags: the natural height, no floor to be off by; no tags, no box at all
    for (const [n, label] of [[3, "three"], [1, "one"]] as Array<[number, string]>) {
      const v = copy(THREE); v.tags = v.tags.slice(0, n);
      const p = openDialog(v);
      const st = styleOf(tgridOf(p));
      assert.ok(st.includes("padding:4px 0 4px 4px;margin:-2px 0 2px -4px;flex:0 0 auto;max-height:30vh;"), label + ": natural height, no shrink: " + st);
      assert.ok(!st.includes("min-height"), label + ": no floor");
      p._closeViewsDialog();
    }
    const none = copy(THREE); none.tags = []; none.actives = { chat: { all: true }, timeline: { all: true }, outline: { none: true } };
    const p0 = openDialog(none);
    assert.ok(styleOf(tgridOf(p0)).includes("padding:0;margin:0;flex:0 0 auto;"), "no tags: no padding, so no empty box between the caption and New tag: " + styleOf(tgridOf(p0)));
    assert.equal(walk(p0._viewsDialog).filter((n) => n._tname).length, 0);
    assert.ok(walk(p0._viewsDialog).some((n) => n.textContent === "+ New tag"), "New tag is still shown");
    p0._closeViewsDialog();
    // the sessions floor by the live count: none live, one, three, four, five
    const live = (k: number, total = k) => Array.from({ length: total }, (_, i) => Object.assign(sess("s" + (i + 1), "job-" + (i + 1), PALETTE[i]), { live: i < k }));
    const floors: Array<[number, number, string]> = [[0, 0, "no sessions"], [1, 18, "one live"], [3, 60, "three live"], [4, 81, "four live"], [5, 81, "five live: four rows, the rest scroll"]];
    for (const [k, px, label] of floors) {
      const p = openDialog(THREE, live(k));
      assert.equal(styleOf(gridBoxOf(p)), "flex:1 1000 auto;min-height:" + px + "px;overflow-y:auto;", label);
      assert.equal(walk(p._viewsDialog).filter((n) => n._sid).length, k, label + ": the rows");
      p._closeViewsDialog();
    }
    const pd = openDialog(THREE, live(0, 2));
    assert.equal(styleOf(gridBoxOf(pd)), "flex:1 1000 auto;min-height:0px;overflow-y:auto;", "two sessions, none live: no row, no floor");
    pd._closeViewsDialog();
    // live beside dead: the floor counts the LIVE sessions, two rows' worth for two live of six (a floor by
    // every session would hold four rows over two, 2 x 18 + 3 here, not 4 x 18 + 9)
    const pm = openDialog(THREE, live(2, 6));
    assert.equal(styleOf(gridBoxOf(pm)), "flex:1 1000 auto;min-height:39px;overflow-y:auto;", "two live of six: two rows' worth, not four");
    assert.equal(walk(pm._viewsDialog).filter((n) => n._sid).length, 2, "two live of six: the two rows");
    pm._closeViewsDialog();
  } finally { g.__rectOf = null; }
});

test("executed: the dot opens the palette as a popover beside the dialog, the current swatch marked apart from the focus ring and focused", () => {
  const panel = openDialog();
  const dot = dotFor(panel, "g2");
  dot._listeners.click();
  const pop = panel._tagColorPop;
  assert.ok(pop, "the popover is open");
  assert.deepEqual(popKeys(), ["g2"], "it lives in the host document beside the dialog, not inside the card");
  assert.ok(styleOf(pop).startsWith("position:fixed;z-index:1003;"), "one layer above the dialog's backdrop (1002)");
  assert.ok(styleOf(pop).includes("font:12px/1.4"), "the shared menu vocabulary");
  assert.equal(pop._key, "g2");
  assert.equal(dot._attrs["aria-expanded"], "true");
  const sws = swatches(pop);
  assert.equal(sws.length, 12, "the same twelve swatches the rows carried inline");
  assert.deepEqual(sws.map((s) => s._attrs["aria-label"]), PALETTE, "the kernel's palette, in its order");
  const cur = sws.filter((s) => s._attrs["aria-checked"] === "true");
  assert.equal(cur.length, 1, "exactly one current swatch");
  assert.ok(styleOf(cur[0]).includes("background:#4EC9B0;"), "...the tag's colour");
  // the current mark is an INNER ring (box-shadow), and no swatch sets an outline: the outline is the
  // browser's focus ring, so focus on open (which lands here) reads apart from "current" (review, 2026-09-09)
  assert.ok(styleOf(cur[0]).includes("box-shadow:inset 0 0 0 2px #4EC9B0,inset 0 0 0 4px "), "...ringed inside, in the menu text colour: " + styleOf(cur[0]));
  assert.ok(sws.every((s) => !styleOf(s).includes("outline")), "no swatch replaces the browser's focus outline");
  assert.ok(sws.filter((s) => s !== cur[0]).every((s) => !styleOf(s).includes("box-shadow")), "only the current swatch wears the ring");
  same(g.document.activeElement, cur[0], "...and focused on open");
  assert.deepEqual(sws.map((s) => s._attrs.tabindex), PALETTE.map((c) => (c === "#4EC9B0" ? "0" : "-1")), "one tab stop, on the current swatch");
  assert.ok(styleOf(walk(pop).find((n) => n._attrs.role === "radiogroup")).includes("grid-template-columns:repeat(6,18px)"), "twelve swatches in two balanced rows of six (T164)");
  // placed by the dot's rect in the dot's own document: below it (the flat shim rect: bottom 20, +4)
  assert.equal(pop.style.top, "24px"); assert.equal(pop.style.left, "6px");
  assert.deepEqual(pop._anchorAt, { left: 0, top: 0 }, "the dot's place is recorded, so a scroll can tell a moved dot from one that stayed");
  panel._closeViewsDialog();
});

test("executed: a pick posts the SAME recolor the inline swatch posted, closes the popover, repaints, and puts focus back on the dot", () => {
  const panel = openDialog();
  const dot = dotFor(panel, "g1");
  dot._listeners.click();
  const sw = swatches(panel._tagColorPop).find((s) => s._attrs["aria-label"] === "#54B204");
  sw._listeners.click();
  // the write: a targeted recolor by tid, no name, one writeId (what timeline-views-ack.test.ts asserts of _editTagUnion color)
  const ops = tagOps();
  assert.equal(ops.length, 1, "one post for the pick");
  assert.deepEqual([ops[0].op, ops[0].tid, ops[0].color, ops[0].name], ["recolor", "g1", "#54B204", undefined]);
  assert.ok(typeof ops[0].writeId === "string" && ops[0].writeId, "the write carries its writeId for the ack");
  assert.equal(posted.filter((p) => p.kind === "views").length, 0, "no whole-blob write");
  // the optimistic copy shows the colour at once, held until the ack
  assert.ok(panel._pendingViews, "the optimistic copy is pending");
  assert.equal(panel._curViews().tags.find((t: any) => t.id === "g1").color, "#54B204");
  same(panel._tagColorPop, null, "the popover closed on the pick");
  assert.equal(popsInBody().length, 0);
  // the dialog repainted: a fresh dot wearing the new colour, focused
  const dot2 = dotFor(panel, "g1");
  assert.ok(dot2 && dot2 !== dot, "the row was rebuilt");
  assert.ok(styleOf(dot2).includes("background:#54B204;"), "the dot wears the picked colour");
  same(g.document.activeElement, dot2, "focus is back on the dot, not lost in the removed popover");
  assert.equal(dot2._attrs["aria-expanded"], "false");
  // the ack settles it exactly as before
  const S1 = copy(THREE); S1.seq = 1001; S1.at = 113; S1.tags[0].color = "#54B204"; S1.tags[0].mtime = 113;
  panel.viewsAck({ type: "tagEditAck", writeId: ops[0].writeId, ok: true, seq: 1001, tid: "g1", views: copy(S1) });
  same(panel._pendingViews, null, "the ack clears the optimistic copy");
  assert.equal(panel._curViews().tags[0].color, "#54B204");
  // a REFUSED pick reverts and says why, in the dialog
  dotFor(panel, "g1")._listeners.click();
  swatches(panel._tagColorPop).find((s) => s._attrs["aria-label"] === "#E87221")._listeners.click();
  assert.equal(panel._curViews().tags[0].color, "#E87221", "optimistic");
  const why = "that tag no longer exists";
  panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[1].writeId, ok: false, error: why, tid: "g1", seq: 1001, views: copy(S1) });
  assert.equal(panel._curViews().tags[0].color, "#54B204", "the refused copy is dropped at once");
  assert.ok(styleOf(dotFor(panel, "g1")).includes("background:#54B204;"), "the dot shows the store's truth");
  const shown = walk(panel._viewsDialog).map(textOf).find((s) => s.startsWith("⚠ "));
  assert.ok(shown && shown.startsWith("⚠ " + why), "the refusal shows in the dialog");
  panel._closeViewsDialog();
});

test("executed: the pick re-resolves its tag by union key: a rename and a remote half landing while the popover is open are honoured by the pick", () => {
  // the popover holds the union object as it was on open; a repaint in between rebuilds the unions, and
  // a pick on the stale copy would skip a remote half that joined meanwhile (its fan-out reads g.remotes)
  const panel = openDialog();
  dotFor(panel, "g2")._listeners.click();
  assert.equal(panel._tagColorPop._key, "g2");
  const remoteCalls: any[] = [];
  g.__rompTimelineEditTag = (m: any) => remoteCalls.push(m);   // the remote bridge appears (the shell's)
  try {
    // the frame: beta renamed to beta2 AND homed on a second kernel too; the dialog repaints on it
    const F = copy(THREE); F.seq = 1001; F.tags[1].name = "beta2";
    F.remoteTags = [Object.assign({}, REMOTE_BETA, { name: "beta2", color: "#4EC9B0" })];
    panel.update({ now, sessions: THREE_SESSIONS, views: F });
    panel._viewsDialogBuild();
    assert.ok(panel._tagColorPop && panel._tagColorPop._key === "g2", "the popover survived the repaint on the same key");
    assert.deepEqual(viewTagUnion(panel._curViews()).find((u: any) => u.localId === "g2").remotes.map((r: any) => r.host), ["TESTHOST-A"], "the union now has a remote half");
    swatches(panel._tagColorPop).find((s) => s._attrs["aria-label"] === "#54B204")._listeners.click();
    // the remote half is recoloured through the bridge, under the CURRENT name...
    assert.equal(remoteCalls.length, 1, "the pick reached the half that joined while the popover was open (a stale copy would have skipped it)");
    assert.deepEqual([remoteCalls[0].host, remoteCalls[0].name, remoteCalls[0].color, remoteCalls[0].rename, remoteCalls[0].delete], ["TESTHOST-A", "beta2", "#54B204", undefined, false]);
    // ...and the local half by its tid, as before
    const ops = tagOps();
    assert.equal(ops.length, 1);
    assert.deepEqual([ops[0].op, ops[0].tid, ops[0].color], ["recolor", "g2", "#54B204"]);
    same(panel._tagColorPop, null, "closed on the pick");
  } finally { delete g.__rompTimelineEditTag; }
  panel._closeViewsDialog();
});

test("executed: a held dot (a remote half this host cannot reach, no bridge) wears the reason, is inert, and takes no click; its neighbour still opens", () => {
  // no __rompTimelineEditTag on the fake window: _remoteBridge() is false, as in an Obsidian panel
  assert.equal(typeof g.__rompTimelineEditTag, "undefined");
  const V = copy(THREE); V.remoteTags = [copy(REMOTE_BETA)];
  const panel = openDialog(V);
  assert.equal(panel._remoteBridge(), false);
  const held = dotFor(panel, "g2");
  assert.ok(held, "beta's dot renders (dim), keyed by its local id");
  assert.equal(held._attrs["aria-disabled"], "true");
  assert.equal(held._attrs.title, panel._unreachableText(["TESTHOST-A"], true), "the refusal is its tooltip");
  assert.equal(held._attrs.tabindex, undefined, "not a tab stop");
  assert.equal(held._attrs["aria-haspopup"], undefined);
  assert.ok(styleOf(held).includes("cursor:default;opacity:0.35;"), "dim, no pointer");
  assert.equal(held._listeners.click, undefined, "no click opener");
  assert.equal(held._listeners.keydown, undefined, "no keyboard opener");
  assert.equal(popsInBody().length, 0);
  // the same dialog's alpha (local only) is untouched by the held row
  const live = dotFor(panel, "g1");
  assert.equal(live._attrs.tabindex, "0");
  live._listeners.click();
  assert.deepEqual(popKeys(), ["g1"], "alpha's dot opens its popover");
  assert.equal(tagOps().length, 0, "nothing was posted by any of this");
  panel._closeViewsDialog();
  assert.equal(popsInBody().length, 0);
});

test("executed: keyboard: Enter or Space on the dot opens; Space on a swatch picks; the arrows move the tab stop; Tab leaves like Escape", () => {
  const panel = openDialog();
  const dot = dotFor(panel, "g3");
  let prevented = 0;
  dot._listeners.keydown({ key: "Enter", preventDefault() { prevented++; } });
  assert.ok(panel._tagColorPop, "Enter opens");
  assert.equal(prevented, 1);
  dot._listeners.keydown({ key: " ", preventDefault() { prevented++; } });
  same(panel._tagColorPop, null, "Space on the dot while its popover is open toggles it shut");
  dot._listeners.keydown({ key: " ", preventDefault() { prevented++; } });
  assert.ok(panel._tagColorPop, "...and opens it again");
  const sws = swatches(panel._tagColorPop);
  const curIdx = PALETTE.indexOf("#E0AF68");
  same(g.document.activeElement, sws[curIdx], "the current swatch has focus");
  sws[curIdx]._listeners.keydown({ key: "ArrowRight", preventDefault() {} });
  same(g.document.activeElement, sws[curIdx + 1], "ArrowRight moves focus");
  assert.equal(sws[curIdx + 1]._attrs.tabindex, "0"); assert.equal(sws[curIdx]._attrs.tabindex, "-1");
  sws[curIdx + 1]._listeners.keydown({ key: "ArrowDown", preventDefault() {} });
  same(g.document.activeElement, sws[Math.min(11, curIdx + 7)], "ArrowDown moves a row (six per row)");
  sws[curIdx + 1]._listeners.keydown({ key: "Home", preventDefault() { prevented = -1; } });
  assert.notEqual(prevented, -1, "other keys pass through");
  // Tab (either direction) leaves the palette the way Escape does: closed, focus back on the dot, which is
  // the tab stop before and after it; left alone it moved focus to the body with the palette still open
  // (review find, 2026-09-09)
  sws[curIdx + 1]._listeners.keydown({ key: "Tab", preventDefault() { prevented = -2; } });
  assert.equal(prevented, -2, "Tab is taken");
  same(panel._tagColorPop, null, "...the popover closed");
  same(g.document.activeElement, dot, "...and focus is on the dot");
  assert.equal(dot._attrs["aria-expanded"], "false");
  dot._listeners.keydown({ key: " ", preventDefault() {} });
  swatches(panel._tagColorPop)[1]._listeners.keydown({ key: " ", preventDefault() {} });
  const ops = tagOps();
  assert.deepEqual([ops[0].op, ops[0].tid, ops[0].color], ["recolor", "g3", PALETTE[1]], "Space picks");
  same(panel._tagColorPop, null, "closed on the pick");
  panel._closeViewsDialog();
});

test("executed: focus leaving the popover for another element closes it; a move within it, to its dot, or to nothing leaves it open", () => {
  const panel = openDialog();
  const dot = dotFor(panel, "g2");
  dot._listeners.click();
  const pop = panel._tagColorPop;
  assert.ok(pop._listeners.focusout, "the popover watches its focus");
  const sws = swatches(pop);
  pop._listeners.focusout({ relatedTarget: null });
  assert.ok(panel._tagColorPop, "focus to nothing (the window losing focus) leaves it open");
  pop._listeners.focusout({ relatedTarget: sws[3] });
  assert.ok(panel._tagColorPop, "a move within the popover (the arrows) leaves it open");
  pop._listeners.focusout({ relatedTarget: dot });
  assert.ok(panel._tagColorPop, "a move to its own dot leaves it open (the dot's click toggles)");
  const search = walk(panel._viewsDialog).find((n) => n.tag === "input" && n.placeholder === "search name or host…");
  pop._listeners.focusout({ relatedTarget: search });
  same(panel._tagColorPop, null, "focus that left for another element closes it");
  assert.equal(popsInBody().length, 0);
  assert.equal(dot._attrs["aria-expanded"], "false");
  same(g.document.activeElement, sws.find((s) => s._attrs["aria-checked"] === "true"), "...without pulling focus back (it went where the user sent it)");
  panel._closeViewsDialog();
});

test("executed: Escape closes the popover first (focus back on the dot) and the dialog only on the next press", () => {
  const panel = openDialog();
  const dot = dotFor(panel, "g1");
  dot._listeners.click();
  assert.ok(panel._tagColorPop);
  panel._viewsDialogKey.fn({ key: "Escape" });
  same(panel._tagColorPop, null, "the popover closed");
  assert.equal(popsInBody().length, 0);
  assert.ok(panel._viewsDialog, "the dialog is still open");
  same(g.document.activeElement, dot, "focus returned to the dot");
  assert.equal(dot._attrs["aria-expanded"], "false");
  assert.equal(tagOps().length, 0, "nothing was written");
  panel._viewsDialogKey.fn({ key: "Escape" });
  same(panel._viewsDialog, null, "the second Escape closes the dialog");
});

test("executed: a press anywhere on the dialog outside the popover closes it; a press on its own dot leaves the toggle to the click; the backdrop closes both", () => {
  const panel = openDialog();
  const back = panel._viewsDialog;
  const card = back.children[0];
  const dot = dotFor(panel, "g2");
  dot._listeners.click();
  assert.ok(panel._tagColorPop);
  back._listeners.pointerdown({ target: card });
  same(panel._tagColorPop, null, "a press on the card (outside the popover) closes it");
  assert.ok(panel._viewsDialog, "...and the dialog stays");
  // the dot's own press: the popover stays for the click, which toggles it shut (no close-then-reopen)
  dot._listeners.click();
  assert.ok(panel._tagColorPop);
  back._listeners.pointerdown({ target: dot });
  assert.ok(panel._tagColorPop, "the anchor's press does not close it");
  dot._listeners.click();
  same(panel._tagColorPop, null, "...the click does");
  // the backdrop: dialog and popover go together
  dot._listeners.click();
  back._listeners.pointerdown({ target: back });
  same(panel._viewsDialog, null, "the backdrop press closes the dialog");
  same(panel._tagColorPop, null, "...and the popover with it");
  assert.equal(popsInBody().length, 0, "nothing is left in the host document");
});

test("executed: one popover at a time; closing the dialog any other way removes it too", () => {
  const panel = openDialog();
  dotFor(panel, "g1")._listeners.click();
  const first = panel._tagColorPop;
  dotFor(panel, "g2")._listeners.click();
  assert.ok(panel._tagColorPop !== first, "the second dot's popover replaced the first");
  assert.equal(panel._tagColorPop._key, "g2");
  assert.deepEqual(popKeys(), ["g2"], "exactly one popover in the host document");
  assert.equal(dotFor(panel, "g1")._attrs["aria-expanded"], "false");
  assert.equal(dotFor(panel, "g2")._attrs["aria-expanded"], "true");
  panel._closeViewsDialog();
  same(panel._tagColorPop, null, "closed with the dialog");
  assert.equal(popsInBody().length, 0);
});

test("executed: a scroll under the popover that moves its dot closes it; one that moved nothing leaves it; a resize closes it and takes its listener down", () => {
  const panel = openDialog(THIRTY, FORTY_SESSIONS);
  const tgrid = tgridOf(panel);
  const resizeBefore = (winListeners.resize || []).length;
  dotFor(panel, "g2")._listeners.click();
  const pop = panel._tagColorPop;
  assert.ok(pop);
  const added = winListeners.resize.slice(resizeBefore);
  assert.equal(added.length, 1, "the popover hung ONE resize closer on its window");
  // the repaint's scroll restore fires a scroll event that moved nothing: the popover stays
  tgrid._listeners.scroll();
  same(panel._tagColorPop, pop, "a scroll event with the dot where it was leaves the popover");
  // the user scrolls the table: the dot moves under the popover, which closes rather than float
  g.__rectOf = (n: any) => (n.dataset && n.dataset.tagDot === "g2" ? { left: 0, top: -150, right: 14, bottom: -136, width: 14, height: 14 } : null);
  try {
    tgrid._listeners.scroll();
    same(panel._tagColorPop, null, "the dot moved: closed");
    assert.equal(popsInBody().length, 0);
    assert.ok(!winListeners.resize.includes(added[0]), "...and the resize closer came down with it");
    assert.equal(dotFor(panel, "g2")._attrs["aria-expanded"], "false");
  } finally { g.__rectOf = null; }
  // the card's own scroll (a short page scrolls the card as a whole) does the same
  dotFor(panel, "g5")._listeners.click();
  const card = cardOf(panel);
  g.__rectOf = (n: any) => (n.dataset && n.dataset.tagDot === "g5" ? { left: 0, top: 80, right: 14, bottom: 94, width: 14, height: 14 } : null);
  try { card._listeners.scroll(); same(panel._tagColorPop, null, "the card scrolled the dot away: closed"); } finally { g.__rectOf = null; }
  // a resize re-centres the card: the popover closes through its own listener, which then comes down
  dotFor(panel, "g7")._listeners.click();
  const fn = winListeners.resize[winListeners.resize.length - 1];
  assert.ok(fn && !added.includes(fn), "a fresh resize closer for the fresh popover");
  fn();
  same(panel._tagColorPop, null, "closed on resize");
  assert.ok(!winListeners.resize.includes(fn), "the listener is gone");
  assert.equal(winListeners.resize.length, resizeBefore, "no listener leaked across three opens");
  panel._closeViewsDialog();
});

test("executed: a repaint keeps the popover on its row, re-placed under the rebuilt dot, and closes it when the row is gone or scrolled out of the table", () => {
  const panel = openDialog();
  const dot = dotFor(panel, "g2");
  dot._listeners.click();
  const pop = panel._tagColorPop;
  assert.equal(pop.style.top, "24px");
  // a refusal for ANOTHER row repaints the dialog; the rows have moved (a notice sits above the table now):
  // the popover stays, hung on the rebuilt dot at its NEW place
  g.__rectOf = (n: any) => {
    if (n.dataset && n.dataset.tagDot === "g2") return { left: 100, top: 300, right: 114, bottom: 314, width: 14, height: 14 };
    if (styleOf(n).startsWith(TGRID_PRE)) return { left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400 };   // the table's box holds the row
    return null;
  };
  try {
    panel._editTagUnion(viewTagUnion(panel._curViews()).find((u: any) => u.name === "alpha"), { color: "#1EA1EB" });
    panel.viewsAck({ type: "tagEditAck", writeId: tagOps()[0].writeId, ok: false, error: "no", tid: "g1", seq: 1000, views: copy(THREE) });
    same(panel._tagColorPop, pop, "the popover survived the repaint");
    const dot2 = dotFor(panel, "g2");
    assert.ok(dot2 !== dot, "the dialog was rebuilt");
    same(panel._tagColorAnchor, dot2, "...and the popover hangs on the new dot");
    assert.equal(dot2._attrs["aria-expanded"], "true", "which knows it is open");
    assert.equal(pop.style.left, "100px"); assert.equal(pop.style.top, "318px");
    assert.deepEqual(pop._anchorAt, { left: 100, top: 300 }, "placed by the new dot's rect, which is now the recorded place");
  } finally { g.__rectOf = null; }
  // a repaint whose rebuilt dot lies outside the table's visible box (its row scrolled out) closes it
  g.__rectOf = (n: any) => {
    if (n.dataset && n.dataset.tagDot === "g2") return { left: 0, top: 900, right: 14, bottom: 914, width: 14, height: 14 };
    if (styleOf(n).startsWith(TGRID_PRE)) return { left: 0, top: 100, right: 800, bottom: 400, width: 800, height: 300 };
    return null;
  };
  try {
    panel._viewsDialogBuild();
    same(panel._tagColorPop, null, "the dot is not in the table's visible box: nothing to hang on, closed");
    assert.equal(popsInBody().length, 0);
  } finally { g.__rectOf = null; }
  // the tag deleted elsewhere: the frame that drops it, and the repaint that follows, close the popover with the row
  dotFor(panel, "g2")._listeners.click();
  assert.ok(panel._tagColorPop);
  const gone = copy(THREE); gone.seq = 1002; gone.tags.splice(1, 1);
  panel.update({ now, sessions: THREE_SESSIONS, views: gone });
  assert.equal(panel._curViews().tags.length, 2, "the frame was adopted");
  panel._viewsDialogBuild();
  same(panel._tagColorPop, null, "no row, no popover");
  panel._closeViewsDialog();
});

test("executed: a repaint that turns the popover's dot HELD (a remote half landed, no bridge to reach it) closes the popover rather than hang it on the last build's dot", () => {
  // review find, 2026-09-09: the held branch drew a dot with no opener and never re-pointed the anchor, so the
  // popover kept the detached dot of the last build, whose rect is all zeros; the re-place read those zeros
  // against the table's box and, with the card scrolled so the box straddled y 0, put the popover at the
  // viewport's corner. A pick from it could only be refused (the remote half is unreachable), so it closes
  assert.equal(typeof g.__rompTimelineEditTag, "undefined", "no bridge on this window");
  const panel = openDialog();
  const dot = dotFor(panel, "g2");
  dot._listeners.click();
  assert.ok(panel._tagColorPop, "beta's popover is open");
  const F = copy(THREE); F.seq = 1001; F.remoteTags = [copy(REMOTE_BETA)];   // beta gains a remote half
  panel.update({ now, sessions: THREE_SESSIONS, views: F });
  panel._viewsDialogBuild();
  const held = dotFor(panel, "g2");
  assert.ok(held && held !== dot, "the row was rebuilt");
  assert.equal(held._attrs["aria-disabled"], "true", "...with a held dot");
  same(panel._tagColorPop, null, "no live dot to hang on: the popover closed");
  same(panel._tagColorAnchor, null, "...and nothing is kept of the last build's dot");
  assert.equal(popsInBody().length, 0, "...and it left the document");
  assert.equal(tagOps().length, 0, "nothing was written");
  dotFor(panel, "g1")._listeners.click();
  assert.deepEqual(popKeys(), ["g1"], "alpha's dot, local only, still opens");
  panel._closeViewsDialog();
});

test("executed: in a framed shell the popover lives in the dialog's own document and is placed by the dot's rect there, untranslated; its resize closer is that window's", () => {
  // review find, 2026-09-09: the dialog is adopted into the top same-origin document when it opens, so its
  // dot already measures in that document's coordinates; _menuHost translating the rect a second time (the
  // frame walk every menu opened from the pane needs) put the popover an iframe offset from its dot, below
  // the viewport in a 200px band at the page's foot. Here the view runs in a frame offset 300x400 into the
  // host page: the frame walk WOULD translate (window.frameElement is set, its parent the host window), and
  // the popover must not be
  const hostWin: any = { innerWidth: 1600, innerHeight: 1300, _listeners: {} as Record<string, any[]>,
    addEventListener(t: string, fn: any) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
    removeEventListener(t: string, fn: any) { const a = this._listeners[t] || []; const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); } };
  const hostDoc: any = { body: makeNode("body"), defaultView: hostWin, addEventListener() {}, removeEventListener() {}, activeElement: null };
  hostDoc.body.ownerDocument = hostDoc;
  hostWin.document = hostDoc;
  const panel = drawnPanel(THREE, THREE_SESSIONS);
  panel._tipWin = hostWin;   // the topmost same-origin window, as the tooltip host resolves it
  g.frameElement = { getBoundingClientRect: () => ({ left: 300, top: 400, right: 1300, bottom: 1000, width: 1000, height: 600 }) };
  g.parent = hostWin;
  try {
    panel._openViewsDialog(null);
    same(panel._viewsDialog.parentNode, hostDoc.body, "the dialog hangs in the host document");
    const dot = dotFor(panel, "g2");
    same(dot.ownerDocument, hostDoc, "...and its dot was adopted there with it");
    dot._listeners.click();
    const pop = panel._tagColorPop;
    assert.ok(pop, "the popover is open");
    same(pop.parentNode, hostDoc.body, "it lives in the dialog's document, beside the dialog");
    assert.equal(popsInBody().length, 0, "...not in the pane's");
    assert.equal(pop.style.left, "6px", "placed by the dot's rect as measured, not the rect plus the frame's 300px offset");
    assert.equal(pop.style.top, "24px", "...under the dot at its 20px bottom plus 4, not 400px lower");
    assert.deepEqual(pop._anchorAt, { left: 0, top: 0 });
    // the dot's window is the host's: the resize closer hangs there and comes down with the popover
    const closers = hostWin._listeners.resize || [];
    assert.equal(closers.length, 1, "one resize closer on the host window");
    closers[0]();
    same(panel._tagColorPop, null, "the host window's resize closed the popover");
    assert.equal((hostWin._listeners.resize || []).length, 0, "...and took the closer down");
    assert.equal(hostDoc.body.children.length, 1, "the dialog alone is left in the host body");
    panel._closeViewsDialog();
    assert.equal(hostDoc.body.children.length, 0, "closed, the dialog left the host document");
  } finally { delete g.frameElement; delete g.parent; }
});

test("executed: the tag table's scroll survives a repaint, and is dropped when the table no longer scrolls", () => {
  const panel = openDialog(THIRTY, FORTY_SESSIONS);
  const tgrid = tgridOf(panel);
  tgrid.scrollTop = 150;
  assert.equal(tgrid.scrollTop, 150);
  // a repaint from anywhere (a delete, a rename toggle, an ack, a peer's edit): the new table starts where the old one was
  panel._viewsDialogBuild();
  const tgrid2 = tgridOf(panel);
  assert.ok(tgrid2 !== tgrid, "the table was rebuilt");
  assert.equal(tgrid2.scrollTop, 150, "...at the same scroll");
  // a colour pick repaints too: the offset holds through it
  dotFor(panel, "g20")._listeners.click();
  swatches(panel._tagColorPop)[0]._listeners.click();
  assert.equal(tgridOf(panel).scrollTop, 150, "a pick keeps the user where they were working");
  // the table no longer scrolls (a browser clamps the write): the offset is dropped
  g.__scrollMax = 0;
  try {
    panel._viewsDialogBuild();
    assert.equal(tgridOf(panel).scrollTop, 0, "nothing to scroll, nothing kept");
  } finally { g.__scrollMax = undefined; }
  panel._viewsDialogBuild();
  assert.equal(tgridOf(panel).scrollTop, 0, "and it stays dropped: the offset is read off the live table, not remembered elsewhere");
  panel._closeViewsDialog();
});

test("executed: a tag dragged past the table's edge drops on the last WHOLE row: a row cut by the clip, or without room for its cue, takes neither the cue nor the drop; a wheel mid-drag re-ranks", () => {
  // the drop math ranks rows by their live rects, and only rows whose whole box plus the 2px cue lies inside
  // the table's box are candidates (review 2026-09-09: the cue was drawn where the user could not see it and
  // the drop wrote it; then, with real pointer events: the centre rule still let a row whose bottom edge
  // was under the clip take the cue, which painted under the clip). Rows 24px tall at a 28px pitch (row i at
  // 28i..28i+24, centre 28i+12, i the row's place in the table as drawn), scrolled by `scroll`; the table's
  // box runs 0..`boxBottom`. THE CUE GROWS THE ROWS as the grid does (measured in both browsers): the
  // cue is a 2px border on the cued cell's edge, and the pill cell is the tallest of its row, so a cue on
  // EITHER edge grows that cell 2px at the BOTTOM and pushes every later row 2px down.
  const panel = openDialog(THIRTY, FORTY_SESSIONS);
  let scroll = 0, boxBottom = 200;
  g.__rectOf = (n: any) => {
    if (n._tname) {
      const sibs = n.parentNode.children.filter((c: any) => c._tname);
      const i = sibs.indexOf(n), cuedAt = sibs.findIndex((c: any) => c.style.borderTop || c.style.borderBottom);
      const top = i * 28 - scroll + (cuedAt >= 0 && i > cuedAt ? 2 : 0), bottom = i * 28 + 24 - scroll + (cuedAt >= 0 && i >= cuedAt ? 2 : 0);
      return { left: 0, top, right: 200, bottom, width: 200, height: bottom - top };
    }
    if (styleOf(n).startsWith(TGRID_PRE)) return { left: 0, top: 0, right: 800, bottom: boxBottom, width: 800, height: boxBottom };
    return null;
  };
  const cells = () => walk(panel._viewsDialog).filter((n) => n._tname);
  const order = () => cells().map((c) => c._tname);
  const cued = () => cells().map((c, i) => (c.style.borderTop || c.style.borderBottom ? i + (c.style.borderTop ? "top" : "bottom") : "")).filter(Boolean);
  const writes = () => posted.filter((p) => p.kind === "views");
  const lastWrite = () => writes()[writes().length - 1].v.tagOrder;
  // grab `name`, move through `ys`, run `mid` with the grab still held (the table and its scroll listener of
  // the moment in hand), drop; returns the cue after each move. Every drag hangs its own scroll listener on
  // the table and takes it down with the drop, leaving the popover's closer in place
  const drag = (name: string, ys: number[], mid?: (t: any) => void): string[][] => {
    const t = tgridOf(panel), closer = t._listeners.scroll;
    const from = cells().find((c) => c._tname === name);
    from._listeners.pointerdown({ preventDefault() {}, pointerId: 1, clientY: 0 });
    assert.ok(t._listeners.scroll !== closer, "the drag listens to the table's scroll while it lasts");
    const trace: string[][] = [];
    for (const y of ys) { from._listeners.pointermove({ clientY: y }); trace.push(cued()); }
    if (mid) mid(t);
    from._listeners.pointerup();
    same(t._listeners.scroll, closer, "the drag's scroll listener came down with the drop; the popover's closer stays");
    assert.deepEqual(cued(), [], "no cue left behind");
    return trace;
  };
  try {
    assert.equal(cells().length, 30);
    // box 0..200 holds rows 0..6 whole (row 6 at 168..192, its cue to 194): a pointer far below lands after row 6
    drag("t01", [500], () => assert.deepEqual(cued(), ["6bottom"], "the cue sits on the last whole row"));
    assert.equal(writes().length, 1, "one order write");
    assert.equal(lastWrite().indexOf("t01"), 6, "t01 landed after the last whole row, not at the table's end");
    assert.deepEqual(lastWrite().slice(0, 8), ["t02", "t03", "t04", "t05", "t06", "t07", "t01", "t08"]);
    assert.deepEqual(order().slice(0, 8), ["t02", "t03", "t04", "t05", "t06", "t07", "t01", "t08"], "the table repainted in the new order");
    // box 0..190: row 6 (168..192) straddles the bottom edge with its centre (180) inside. The old centre rule
    // gave it the cue, a border on its bottom edge, 2px under the clip. Held and dragged down past the edge, it
    // stays where it is (no write: the rows below are out of sight, and the whole row above is not what the
    // gesture said); dragged onto by another row, it takes neither the cue nor the drop, row 5 does
    boxBottom = 190;
    drag("t01", [500], () => assert.deepEqual(cued(), [], "the held row, cut by the edge, is its own place: no cue"));
    assert.equal(writes().length, 1, "…and no write");
    drag("t02", [500], () => assert.deepEqual(cued(), ["5bottom"], "the straddling row is no candidate: the cue sits on the last whole row"));
    assert.equal(writes().length, 2);
    assert.equal(lastWrite().indexOf("t02"), 5, "the drop lands where the cue was drawn");
    assert.deepEqual(order().slice(0, 8), ["t03", "t04", "t05", "t06", "t07", "t02", "t01", "t08"]);
    // box 0..193: row 6 is inside the box but the 2px its cue adds under its edge is not: row 5 again
    boxBottom = 193;
    drag("t03", [500], () => assert.deepEqual(cued(), ["5bottom"], "a row without room for its cue is no candidate"));
    assert.equal(writes().length, 3);
    assert.equal(lastWrite().indexOf("t03"), 5);
    assert.deepEqual(order().slice(0, 8), ["t04", "t05", "t06", "t07", "t02", "t03", "t01", "t08"]);
    // the top edge: the table scrolled 10px (row 0 at -10..14, cut by the clip): a pointer above the table
    // lands on row 1's top edge, whole, not on row 0's, under the clip
    boxBottom = 200; scroll = 10;
    drag("t07", [-80], () => assert.deepEqual(cued(), ["1top"], "the cue sits on the first whole row"));
    assert.equal(writes().length, 4);
    assert.equal(lastWrite().indexOf("t07"), 1, "…and the drop lands there");
    assert.deepEqual(order().slice(0, 8), ["t04", "t07", "t05", "t06", "t02", "t03", "t01", "t08"]);
    // a wheel mid-drag: a scroll fires no pointermove, so the table's scroll re-ranks with the last pointer y.
    // The pointer at y 100 ranks row 4 (centre 124, the first past 100); the table scrolls 200 and row 11
    // (centre 120 now) is the row under the pointer; the cue and the drop follow it
    scroll = 0;
    drag("t04", [100], (t) => {
      assert.deepEqual(cued(), ["4bottom"], "before the wheel: the row under the pointer");
      scroll = 200; t._listeners.scroll();
      assert.deepEqual(cued(), ["11bottom"], "after the wheel: the row now under the pointer");
    });
    assert.equal(writes().length, 5);
    assert.equal(lastWrite().indexOf("t04"), 11, "the drop follows the rows the wheel brought under the pointer");
    assert.equal(order()[11], "t04");
    // a scroll whose event has not landed by the drop (they arrive a frame late): the table's scrollTop says the
    // rows moved, so the drop re-ranks on the live rects
    scroll = 0;
    drag("t07", [100], (t) => {
      assert.deepEqual(cued(), ["4bottom"]);
      scroll = 200; t.scrollTop = 200;   // the rows moved, no scroll event yet
    });
    assert.equal(writes().length, 6);
    assert.equal(lastWrite().indexOf("t07"), 11, "the drop read the live rows");
    // a grab and a release with no move and no scroll writes nothing (a click on the pill)
    drag("t05", []);
    assert.equal(writes().length, 6, "no move, no scroll: no write");
    // THE CUE COMES OFF FOR THE MEASUREMENT (real pointer events in both browsers). Box 0..194: row 6
    // (168..192, its cue to 194) is the last whole row, with exactly 2px of room. A stepped drag far below puts
    // the cue on row 4 first (the row under the pointer at 100), which pushes row 6 to 170..194; measured with
    // that cue drawn, row 6 had no room for its own and the cue and the drop landed one row short
    scroll = 0; boxBottom = 194;
    const a = order()[0];
    assert.deepEqual(drag(a, [100, 500], () => assert.deepEqual(cued(), ["6bottom"], "the cue reaches the last whole row despite the cue it wore on the way")),
      [["4bottom"], ["6bottom"]], "the cue moves from the row under the pointer to the last whole row");
    assert.equal(writes().length, 7);
    assert.equal(lastWrite().indexOf(a), 6, "the drop lands on the last whole row");
    // a top cue: the held row scrolled under the bottom clip (row 12, none of it shows), the pointer in the upper
    // half of row 6 three times. A top cue grows its cell at the BOTTOM too; judged as if the border grew it
    // upward, row 6 lost its room on the second move and the cue settled one row above the pointer
    const b = order()[12];
    assert.deepEqual(drag(b, [172, 172, 172], () => assert.deepEqual(cued(), ["6top"])), [["6top"], ["6top"], ["6top"]],
      "a top cue on the last whole row holds across further moves at the same place");
    assert.equal(writes().length, 8);
    assert.equal(lastWrite().indexOf(b), 6, "the drop lands on the pointed row, not the one above");
    // THE HELD ROW'S OWN CANDIDACY: while any of it shows it counts, so cut in half by the top clip and dragged
    // above the table it stays where it is (no cue, no write); scrolled entirely out of the box it does not
    // count, and the visible rows decide: the first whole row above, the last whole row below
    scroll = 12;   // row 0 at -12..12, half under the top clip
    const c = order()[0];
    assert.deepEqual(drag(c, [-80]), [[]], "half shown, the held row is its own place: no cue");
    assert.equal(writes().length, 8, "...and no write");
    scroll = 30;   // row 0 at -30..-6 (none shows), row 1 at -2..22 (cut), row 2 at 26..50 the first whole row
    const d = order()[0];
    assert.deepEqual(drag(d, [-80]), [["2bottom"]], "none of the held row shows: the first whole row takes the cue");
    assert.equal(writes().length, 9);
    assert.equal(lastWrite().indexOf(d), 2, "...and the drop");
    scroll = 0;    // row 12 at 336..360, under the bottom clip; the pointer far below
    const e = order()[12];
    assert.deepEqual(drag(e, [500]), [["6top"]], "none of the held row shows: the last whole row takes the cue");
    assert.equal(writes().length, 10);
    assert.equal(lastWrite().indexOf(e), 6);
  } finally { g.__rectOf = null; }
  panel._closeViewsDialog();
});

test("executed: the floors' sources: four of the SMALLEST session rows when the first wraps, both floors kept through a build without layout, the first build in the host document", () => {
  // review find, 2026-09-09. Session rows placed by their index (rowIdx: a cell's row is the count of name
  // cells up to it in the grid); row 0 is 46.8px (its chips wrapped onto a second line), the rest 22.2px, 3px
  // gaps. Tag rows by their index too, 24px at a 28px pitch
  const sessRect = (i: number) => { const top = i === 0 ? 300 : 300 + 46.8 + 3 + (i - 1) * 25.2, h = i === 0 ? 46.8 : 22.2; return { left: 0, top, right: 200, bottom: top + h, width: 200, height: h }; };
  g.__rectOf = (n: any) => {
    const par = n.parentNode ? styleOf(n.parentNode) : "";
    if (par.startsWith(TGRID_PRE)) return tagRect(rowIdx(n, "_tname"));
    if (par.startsWith(GRID_PRE)) return sessRect(rowIdx(n, "_sid"));
    return null;
  };
  try {
    // (a) the sessions floor is four of the SMALLEST rows and their gaps (4 x 22.2 + 9), not four of the first:
    // a wrapped first row can cost a whole row in the box, never leave blank under the rows
    const p = openDialog(THIRTY, FORTY_SESSIONS);
    assert.equal(styleOf(gridBoxOf(p)), "flex:1 1000 auto;min-height:97.8px;overflow-y:auto;", "four of the smallest rows, not four of the wrapped first");
    // a query that leaves the wrapped row alone on screen does not move the floor (the rows are measured on
    // unfiltered builds and the height kept), nor does a repaint under it or clearing it
    const q = walk(p._viewsDialog).find((n) => n.tag === "input" && n.placeholder === "search name or host…");
    q.value = "job-01"; q._listeners.input();
    assert.equal(walk(p._viewsDialog).filter((n) => n._sid).length, 1, "one hit, the wrapped row");
    assert.equal(styleOf(gridBoxOf(p)), "flex:1 1000 auto;min-height:97.8px;overflow-y:auto;", "the wrapped row alone on screen: the floor stays");
    p._viewsDialogBuild();
    assert.equal(styleOf(gridBoxOf(p)), "flex:1 1000 auto;min-height:97.8px;overflow-y:auto;", "...through a repaint under the query");
    const q2 = walk(p._viewsDialog).find((n) => n.tag === "input" && n.placeholder === "search name or host…");   // the repaint's own input
    q2.value = ""; q2._listeners.input();
    assert.equal(walk(p._viewsDialog).filter((n) => n._sid).length, 40);
    assert.equal(styleOf(gridBoxOf(p)), "flex:1 1000 auto;min-height:97.8px;overflow-y:auto;", "...and once cleared");
    // (b) a build that reads no layout (the dialog's document not rendered: every rect 0) keeps BOTH floors the
    // last build measured; the tag table's dropped to none before (min-height:0px, one row showing on a short page)
    assert.ok(styleOf(tgridOf(p)).includes("min-height:80px;"), "the table's floor before: " + styleOf(tgridOf(p)));
    g.__rectOf = (n: any) => {
      const par = n.parentNode ? styleOf(n.parentNode) : "";
      return par.startsWith(TGRID_PRE) || par.startsWith(GRID_PRE) ? { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 } : null;
    };
    p._viewsDialogBuild();
    assert.ok(styleOf(tgridOf(p)).includes("flex:0 1 auto;min-height:80px;"), "no layout to read: the table keeps its floor: " + styleOf(tgridOf(p)));
    assert.equal(styleOf(gridBoxOf(p)), "flex:1 1000 auto;min-height:97.8px;overflow-y:auto;", "...and so does the sessions box");
    p._closeViewsDialog();
    // (c) the first build runs with the card already in its FINAL document (the host the dialog is adopted
    // into), so its floors are measured where the card is laid out: rows measure 24px only under the host
    // document's body here, 0 anywhere else, and the floor is set on the open, not on the first repaint
    const hostDoc: any = { body: makeNode("body"), addEventListener() {}, removeEventListener() {}, activeElement: null };
    const rootOf = (n: any) => { let r = n; while (r.parentNode) r = r.parentNode; return r; };
    g.__rectOf = (n: any) => {
      const par = n.parentNode ? styleOf(n.parentNode) : "";
      if (!par.startsWith(TGRID_PRE)) return null;
      return rootOf(n) === hostDoc.body ? tagRect(rowIdx(n, "_tname")) : { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    };
    const ph = drawnPanel(THIRTY, FORTY_SESSIONS);
    ph._tipWin = { document: hostDoc };   // the topmost same-origin window, as the tooltip host resolves it
    ph._openViewsDialog(null);
    same(ph._viewsDialog.parentNode, hostDoc.body, "the dialog hangs in the host document");
    assert.ok(styleOf(tgridOf(ph)).includes("flex:0 1 auto;min-height:80px;"), "the first build measured its rows in the host document: " + styleOf(tgridOf(ph)));
    ph._closeViewsDialog();
    assert.equal(hostDoc.body.children.length, 0, "closed, the dialog left the host document");
  } finally { g.__rectOf = null; }
});

test("executed: pane filters render FOLDED, one summary line per pane in the user's tag order; the caret opens the matrix in its own bounded cell; the choice holds for the page", () => {
  stored["romp:feedTags-set"] = JSON.stringify({ lens: { tags: ["beta"], none: true }, t: 1 });
  const panel = openDialog();
  const cap = caption(panel);
  assert.ok(cap, "the section caption");
  assert.ok(cap.textContent.endsWith(" ▸"), "folded: a right-pointing caret");
  assert.equal(cap._attrs["aria-expanded"], "false");
  assert.equal(cap._attrs.role, "button"); assert.equal(cap._attrs.tabindex, "0");
  let lines = paneLines(panel);
  assert.deepEqual(Object.keys(lines), ["Chat", "Sessions", "Outline", "Feed"], "one line per PANE; the bulk row is a control, not a pane");
  const summary = (k: string) => lines[k].children[1].textContent;
  assert.equal(summary("Chat"), "alpha, gamma", "the tags it shows, in the user's tag order (the lens lists gamma first)");
  assert.equal(summary("Sessions"), "All");
  assert.equal(summary("Outline"), "no tags");
  assert.equal(summary("Feed"), "beta, no tags", "the feed row reads its echo");
  assert.equal(lines.Chat.children[1]._attrs.title, "alpha, gamma", "the hover carries the full list");
  assert.equal(chips(panel).length, 0, "no chip while folded");
  const card = cardOf(panel);
  assert.ok(Object.values(lines).every((row: any) => row.parentNode === card), "folded, the lines are the card's own fixed rows");
  // the caret opens the matrix: five rows of All / no tags / every tag, in a cell of their own that keeps a
  // bounded share and scrolls within it (review find, 2026-09-09: with thirty tags the open matrix pushed
  // the sessions off the card at 800px)
  cap._listeners.click();
  const cap2 = caption(panel);
  assert.ok(cap2.textContent.endsWith(" ▾"), "open: a down-pointing caret");
  assert.equal(cap2._attrs["aria-expanded"], "true");
  lines = paneLines(panel);
  assert.deepEqual(Object.keys(lines), ["All surfaces", "Chat", "Sessions", "Outline", "Feed"]);
  const mbox = lines["All surfaces"].parentNode;
  assert.ok(Object.values(lines).every((row: any) => row.parentNode === mbox), "the five rows share one cell");
  assert.equal(mbox.children.length, 5);
  same(mbox.parentNode, cardOf(panel), "the cell is the card's flex child");
  assert.equal(styleOf(mbox), "flex:0 1 auto;min-height:52px;max-height:25vh;overflow-y:auto;overflow-x:hidden;", "bounded, scrolling within, giving way past the sessions' floor, keeping two lines");
  assert.equal(chips(panel).length, 5 * (2 + 3), "All, no tags and three tags on each of five rows");
  assert.ok(walk(panel._viewsDialog).some((n) => n.textContent === "(mixed)"), "the panes differ, so the bulk row says so");
  const cell = lines.Chat.children[1];
  assert.ok(styleOf(cell).includes("flex-wrap:wrap;flex:1 1 auto;min-width:0;"), "the chips wrap inside their own cell, under the first chip");
  assert.equal(cell.children.length, 5);
  // a chip still edits its pane (the matrix is the same control it was)
  const noTags = cell.children.find((n: any) => n.textContent === "no tags");
  noTags._listeners.click();
  const w = posted.filter((p) => p.kind === "views");
  assert.equal(w.length, 1, "one whole-blob lens write");
  assert.deepEqual(w[0].v.actives.chat, { none: true, tags: ["gamma", "alpha"] }, "toggled onto the chat lens");
  // the choice holds for the page: a fresh open finds the matrix open
  panel._closeViewsDialog();
  panel._openViewsDialog(null);
  assert.ok(caption(panel).textContent.endsWith(" ▾"), "still open on the next open");
  assert.equal(chips(panel).length, 25);
  // Enter on the caption folds it back (and leaves the module default for the tests that follow)
  caption(panel)._listeners.keydown({ key: "Enter", preventDefault() {} });
  assert.ok(caption(panel).textContent.endsWith(" ▸"));
  assert.equal(chips(panel).length, 0);
  panel._closeViewsDialog();
  delete stored["romp:feedTags-set"];
});

test("executed: the folded summary names only tags that exist; a lens left with none says so, and the hover names what it still carries", () => {
  // the kernel keeps unknown names in a lens by design, and a rename or a delete rewrites no lens: the
  // folded line used to list the old name as a live filter while the open matrix had no chip for it
  const V = copy(THREE);
  V.actives = { chat: { tags: ["zeta", "alpha"] }, timeline: { tags: ["zeta"] }, outline: { tags: ["zeta"], none: true } };
  const panel = openDialog(V);
  const lines = paneLines(panel);
  const line = (k: string) => lines[k].children[1];
  assert.equal(line("Chat").textContent, "alpha", "the gone name is left out");
  assert.equal(line("Chat")._attrs.title, "alpha; the filter also names zeta, and no tag here has that name now", "the hover says what the filter still carries");
  assert.equal(line("Sessions").textContent, "none of the tags here", "a lens naming only gone tags says so (the pane shows nothing)");
  assert.equal(line("Sessions")._attrs.title, "none of the tags here; the filter also names zeta, and no tag here has that name now");
  assert.equal(line("Outline").textContent, "no tags", "'no tags' still counts");
  assert.equal(line("Feed").textContent, "All", "an untouched pane");
  assert.equal(line("Feed")._attrs.title, "All", "...with nothing to add");
  // open, the matrix shows the same: no chip for zeta, alpha selected on Chat, nothing selected on Sessions
  caption(panel)._listeners.click();
  const open = paneLines(panel);
  const sel = (k: string) => open[k].children[1].children.filter((c: any) => !styleOf(c).includes("opacity:")).map((c: any) => c.textContent);   // selected = the full chip; unselected wears the shared fade (T321)
  assert.deepEqual(sel("Chat"), ["alpha"]);
  assert.deepEqual(sel("Sessions"), [], "nothing selected: the same emptiness the folded line reports");
  assert.ok(!walk(panel._viewsDialog).some((n) => n.textContent === "zeta"), "no chip anywhere for the gone name");
  caption(panel)._listeners.click();
  panel._closeViewsDialog();
});

test("executed: thirty tags and forty sessions: every row, every chip and the sessions section render; the summary cuts with a count", () => {
  const panel = openDialog(THIRTY, FORTY_SESSIONS);
  const dlg = panel._viewsDialog;
  assert.deepEqual(walk(dlg).filter((n) => n._tname).map((n) => n._tname), NAMES30, "thirty one-line tag rows, in order");
  assert.equal(dots(panel).length, 30, "one dot each");
  assert.equal(walk(dlg).filter((n) => n._attrs.role === "radio").length, 0, "and not a swatch among them (360 before)");
  const nt = walk(dlg).find((n) => n.textContent === "+ New tag");
  assert.ok(nt, "the New tag row");
  assert.ok(!walk(tgridOf(panel)).includes(nt), "...under the table, not inside its scroll");
  assert.ok(/flex:0 1 auto;min-height:\d+px;/.test(styleOf(tgridOf(panel))), "the table's floor stays three rows however many there are (the floors test has the arithmetic): " + styleOf(tgridOf(panel)));
  // folded filters: four lines, the long one cut
  const lines = paneLines(panel);
  assert.equal(lines.Chat.children[1].textContent, "t01, t02, t03, t04, t05, t06 +24 more");
  assert.equal(lines.Chat.children[1]._attrs.title, NAMES30.join(", "), "the hover has all thirty");
  assert.equal(lines.Sessions.children[1].textContent, "All");
  // open: five rows of thirty-two chips
  caption(panel)._listeners.click();
  assert.equal(chips(panel).length, 5 * 32, "All, no tags and thirty tags on each of five rows");
  caption(panel)._listeners.click();
  assert.equal(chips(panel).length, 0);
  // the sessions section: search, tag all, and every live session
  assert.ok(walk(dlg).some((n) => n.tag === "input" && n.placeholder === "search name or host…"), "the search box");
  assert.ok(walk(dlg).some((n) => n.textContent === "tag all"), "tag all");
  assert.equal(walk(dlg).filter((n) => n._sid).length, 40, "forty session rows");
  // the popover works on the last row too
  dotFor(panel, "g30")._listeners.click();
  assert.equal(swatches(panel._tagColorPop).length, 12);
  swatches(panel._tagColorPop)[0]._listeners.click();
  assert.deepEqual([tagOps()[0].op, tagOps()[0].tid, tagOps()[0].color], ["recolor", "g30", PALETTE[0]]);
  panel._closeViewsDialog();
});
