// Right-click a tab → a color picker in the context menu: the romp identity palette as circles, the session's
// current one ringed; clicking one recolors the session (the user 2026-06-29). Source pins against render.ts —
// the menu builds DOM at right-click time, so a behavioral jsdom run isn't needed to lock the shape.
// The T164 balanced split is ALSO the timeline's: its Sessions & tags dialog draws the same swatches, since
// 2026-09-09 in the colour popover a tag row's dot opens, and that half runs EXECUTED over the house fake-DOM
// shim with the real TimelinePanel (the shim as ui/timeline-tags-scale.test.ts carries it).
import { test } from "node:test";
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

// ---- the house fake-DOM shim (ui/timeline-tags-scale.test.ts), installed before the view loads ----
function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, _text: "", parentNode: null, value: "",
    get textContent() { return this._text; },
    set textContent(v: any) { this._text = v == null ? "" : String(v); for (const c of this.children) c.parentNode = null; this.children.length = 0; },
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) {
      if (c.parentNode) { const i = c.parentNode.children.indexOf(c); if (i >= 0) c.parentNode.children.splice(i, 1); }
      c.parentNode = n; this.children.push(c); return c;
    },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) { this.children.splice(i, 1); c.parentNode = null; } return c; },
    get firstChild() { return this.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    _listeners: {} as any,
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener(t: string) { delete n._listeners[t]; },
    setPointerCapture() {}, releasePointerCapture() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return n._rect || { width: 200, height: 20, left: 0, top: 0, right: 200, bottom: 20 }; },
    closest() { return null; },
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
const stored: Record<string, string> = {};
g.localStorage = { getItem: (k: string) => (k in stored ? stored[k] : null), setItem: (k: string, v: any) => { stored[k] = String(v); }, removeItem: (k: string) => { delete stored[k]; } };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)", fontFamily: "sans-serif" });
g.requestAnimationFrame = () => 0;
g.setTimeout = (fn: any) => { try { fn(); } catch { /* focus on a fake node */ } return 0; };
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 1300;
// the two host bridges: present, so the dialog's tag rows are live (a row with no bridge is held, its dot inert)
g.__rompTimelineSetViews = () => {};
g.__rompTimelineTagEdit = () => {};
const { TimelinePanel } = createRequire(__filename)(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"));
function walk(x: any, out: any[] = []): any[] { for (const c of x.children || []) { out.push(c); walk(c, out); } return out; }
const styleOf = (n: any): string => String(n._attrs.style || "");
const now = 1_781_000_000;

test("the palette is fetched once from the kernel /palette (the client doesn't own the palette list)", () => {
  assert.match(RENDER, /let paletteColors: string\[\] = \[\];/);
  assert.match(RENDER, /fetch\(kernelUrl\("\/palette"\)/);   // host-aware: VS Code webviews need the kernel base
  // the swatches are built from the fetched list, not a hard-coded array of hexes
  assert.match(RENDER, /for \(const bg of paletteColors\)/);
});

test("a palette switch (gear → Session colors) pushes the new swatch set to open tabs", () => {
  // the kernel re-broadcasts {type:"palette"} after setPalette; the menu offers the NEW set without a reload
  assert.match(RENDER, /m\.type === "palette" && Array\.isArray\(m\.colors\)\) paletteColors = m\.colors;/);
});

test("showTabMenu renders a swatch per palette color, ringing the session's current one", () => {
  // a swatch row, gated on the palette having loaded
  assert.match(RENDER, /if \(paletteColors\.length\) \{/);
  assert.match(RENDER, /for \(const bg of paletteColors\) \{/);
  // the current color gets the .sel ring (case-insensitive match against the session color)
  assert.match(RENDER, /"ctx-swatch" \+ \(bg\.toLowerCase\(\) === cur \? " sel" : ""\)/);
  assert.match(RENDER, /sw\.style\.background = bg;/);
  // clicking a swatch dismisses the menu and recolors
  assert.match(RENDER, /dismissTabMenu\(\); setSessionColor\(id, bg\);/);
});

test("setSessionColor optimistically repaints and posts setSessionColor to the kernel", () => {
  assert.match(RENDER, /function setSessionColor\(id: string, bg: string\)/);
  // optimistic: update the session + placeholder color, repaint now
  assert.match(RENDER, /const color: Color = \{ bg, fg: "#ffffff" \};/);
  assert.match(RENDER, /if \(s\) s\.color = color;/);
  assert.match(RENDER, /if \(meta\) meta\.color = color;/);
  assert.match(RENDER, /renderTabs\(\);/);
  // then tell the kernel (it persists + re-broadcasts)
  assert.match(RENDER, /postMessage\(\{ type: "setSessionColor", id, bg \}\)/);
});

test("swatch grids balance their rows: ceil-split over a six-per-row cap (T164)", () => {
  // for n swatches, the fewest rows that keep each within six, split ceil-evenly: 12 -> 6+6, 9 -> 5+4,
  // 13 -> 5+5+3 (three rows, since no row holds more than six), computed per render; the chat picker's
  // CSS keeps repeat(5) as the no-JS fallback
  assert.match(RENDER, /const swRows = Math\.ceil\(paletteColors\.length \/ 6\)/);
  assert.match(RENDER, /repeat\(" \+ Math\.ceil\(paletteColors\.length \/ swRows\) \+ ", 18px\)"/);
  // the timeline's Sessions & tags dialog draws the same split. Until 2026-09-09 every tag row carried the
  // swatches inline and two source pins read that code; the swatches moved into the colour popover a row's
  // dot opens, so the split is now read off the popover's grid, EXECUTED: one tag, a palette of n colours,
  // the dialog opened, the dot clicked, the grid's columns checked for the same counts as above plus the
  // counts around the cap (seven splits 4+3, six and five stay one row)
  const cases: Array<[number, number, string]> = [[12, 6, "6+6"], [9, 5, "5+4"], [13, 5, "5+5+3"], [7, 4, "4+3"], [6, 6, "one row of six"], [5, 5, "one row of five"]];
  for (const [n, cols, shape] of cases) {
    assert.equal(cols, Math.ceil(n / Math.ceil(n / 6)), n + " swatches: the expectation IS the ceil-split");
    const pal = Array.from({ length: n }, (_, i) => "#" + String(i + 1).padStart(2, "0").repeat(3));   // n distinct hexes
    const panel = new TimelinePanel(makeNode("div"));
    const views = {
      active: "all", at: 100, seq: 1000, actives: { chat: { all: true }, timeline: { all: true }, outline: { all: true } },
      tags: [{ id: "g1", name: "alpha", color: pal[0], members: ["s1"], mtime: 100 }],
    };
    const s1 = { id: "s1", name: "web", color: pal[0], state: "working", live: true, model: "Opus", effort: "high",
      context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false };
    panel.update({ now, sessions: [s1], turns: {}, messages: [], judging: [], views, palette: pal.slice() });
    panel.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 1000 });
    panel._openViewsDialog(null);
    const dot = walk(panel._viewsDialog).find((x) => x.dataset.tagDot === "g1");
    assert.ok(dot, n + " swatches: the tag row's colour dot");
    dot._listeners.click();
    const pop = panel._tagColorPop;
    assert.ok(pop, n + " swatches: the popover opened");
    assert.equal(walk(pop).filter((x) => x._attrs.role === "radio").length, n, n + " swatches drawn");
    const grid = walk(pop).find((x) => x._attrs.role === "radiogroup");
    assert.ok(grid, n + " swatches: the swatch grid");
    assert.ok(styleOf(grid).includes("grid-template-columns:repeat(" + cols + ",18px);"),
      n + " swatches read " + shape + " (repeat(" + cols + ")), got: " + styleOf(grid));
    panel._closeViewsDialog();
    assert.equal(g.document.body.children.filter((x: any) => x._attrs.role === "dialog").length, 0, "no popover left in the host document");
  }
});
