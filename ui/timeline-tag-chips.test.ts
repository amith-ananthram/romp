// THE TIMELINE'S VIEWS MENU RENDERS EACH TAG AS ITS CHIP ACTING AS A TOGGLE (T283b, the user 2026-09-09: menus wear
// one vocabulary): the shared tag-lens menu (ui/webview/tag-menu.ts, T283) made each union tag the tag chip itself,
// aria-pressed on the chip, selected = full colour, unselected = faded at 0.45 with its colour kept, one tag per line
// with the chip at the left, All and (no tags) keeping the ✓ row grammar. This pane inlines its own copy of that
// menu (it may live in Obsidian's document and loads no module), so the copy mirrors the chips with the RESOLVED
// palette values it already carries. Executed over the house three-helper host (the tagbtn-click harness), plus
// drift pins between the two menus' chip and row shapes. Synthetic sessions and tags only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return k in this._attrs ? this._attrs[k] : null; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) { if (c.parentNode) { const i = c.parentNode.children.indexOf(c); if (i >= 0) c.parentNode.children.splice(i, 1); } c.parentNode = n; this.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    remove() { if (n.parentNode) n.parentNode.removeChild(n); },
    _listeners: {} as any,
    addEventListener(t: string, fn: any) { n._listeners[t] = fn; }, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 32, height: 18, left: 8, top: 400, right: 40, bottom: 418 }; },
    closest() { return null; }, focus() {},
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
  getElementById() { return null; }, addEventListener() {}, removeEventListener() {},
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)" });
g.requestAnimationFrame = () => 0;
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g; g.innerWidth = 1400; g.innerHeight = 800;

const viewPath = path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js");
const { TimelinePanel } = createRequire(__filename)(viewPath);
const SRC = fs.readFileSync(viewPath, "utf8");
const MENU = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "tag-menu.ts"), "utf8");

const TAGS: any[] = [{ id: "g1", name: "infra", color: "#DD42FF", members: ["s2"] }, { id: "g2", name: "qa", color: "#3355aa", members: ["s1"] }];
function panelWith(lens: any): any {
  const now = 1_781_000_000;
  const sess = (id: string, name: string, color: string) => ({ id, name, color, state: "working", live: true, model: "Opus", effort: "high",
    context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false });
  const panel = new TimelinePanel(makeNode("div"));
  panel.update({ now, sessions: [sess("s1", "web", "#f7768e"), sess("s2", "api", "#7aa2f7")],
    turns: { s1: [{ id: "t1", start: now - 400, end: now - 100, prompt: "do the thing", mids: [] }] }, messages: [], judging: {},
    views: { active: "all", tags: TAGS, actives: { timeline: lens } } });
  return panel;
}
const openMenu = (panel: any) => { panel._openViewsMenu(makeNode("button")); return panel._viewsMenu; };
const rows = (menu: any) => menu.children.filter((c: any) => c.tag === "div");
const text = (n: any): string => (n.tag === "#text" ? n.textContent : (n.textContent || "")) + (n.children || []).map(text).join("");
const chipOf = (row: any) => (row.children || []).find((c: any) => c.tag === "span" && "aria-pressed" in c._attrs);
const hasCheck = (row: any) => (row.children || []).some((c: any) => c.tag === "span" && c.textContent === "✓");

test("executed: each tag is its own chip acting as a toggle, the selected one full colour, the other faded with its colour kept", () => {
  const panel = panelWith({ tags: ["infra"] });
  const menu = openMenu(panel);
  const r = rows(menu);
  assert.equal(text(r[0]), "All"); assert.equal(text(r[1]), "(no tags)");
  const infra = chipOf(r[2]), qa = chipOf(r[3]);
  assert.ok(infra && qa, "the two tags render as chips, one per row, after All and (no tags)");
  assert.equal(infra.textContent, "infra"); assert.equal(qa.textContent, "qa");
  assert.equal(infra._attrs["aria-pressed"], "true"); assert.equal(infra._attrs.role, "button");
  assert.doesNotMatch(infra._attrs.style, /opacity/, "a selected chip stands at full opacity");
  assert.equal(infra.classList.contains("tag-chip-off"), false);
  assert.match(infra._attrs.style, /border:1px solid #DD42FF;color:#DD42FF;/, "the chip keeps the tag's own colour");
  assert.equal(qa._attrs["aria-pressed"], "false");
  assert.equal(qa.classList.contains("tag-chip-off"), true, "an unselected chip wears the faded class");
  assert.match(qa._attrs.style, /opacity:0\.45;/, "…and paints the same fade inline: this host loads no sheet");
  assert.match(qa._attrs.style, /border:1px solid #3355aa;color:#3355aa;/, "faded, not recoloured");
  for (const row of [r[2], r[3]]) assert.equal(hasCheck(row), false, "the tag rows carry no ✓: the chip's state IS the mark");
  assert.match(r[2]._attrs.style, /^padding:3px 8px;border-radius:4px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;$/, "the chip row's shape");
  panel._closeViewsMenu();
});

test("executed: All and (no tags) keep the row grammar with the ✓; an uncoloured tag falls back to the palette's muted text", () => {
  const panel = panelWith({ none: true, tags: ["infra"] });
  panel.update(Object.assign({}, panel.data, { views: { active: "all", tags: TAGS.concat([{ id: "g3", name: "plain", color: null, members: [] }]),
    actives: { timeline: { none: true, tags: ["infra"] } } } }));
  const menu = openMenu(panel);
  const r = rows(menu);
  assert.equal(hasCheck(r[1]), true, "(no tags) selected → its ✓");
  assert.equal(hasCheck(r[0]), false, "All not selected → no ✓");
  const plain = chipOf(r[4]);
  assert.ok(plain, "the uncoloured tag is a chip too");
  assert.match(plain._attrs.style, /border:1px solid #9aa0a6;color:#9aa0a6;/, "the dark palette's muted text, resolved (no var() in this host)");
  panel._closeViewsMenu();
});

test("executed: clicking a chip's row toggles that tag, the menu stays open and repaints in place", () => {
  const panel = panelWith({ tags: ["infra"] });
  const applied: any[] = [];
  panel._setLens = (blob: any) => { applied.push(blob); panel.data.views.actives = blob.actives; };
  const menu = openMenu(panel);
  const before = rows(menu).length;
  rows(menu)[3]._listeners.click();          // qa: off → on
  assert.deepEqual(applied[0].actives.timeline.tags, ["infra", "qa"]); assert.ok(!applied[0].actives.timeline.none);
  assert.equal(panel._viewsMenu, menu, "the menu stayed open");
  assert.equal(rows(menu).length, before, "repainted in place: the same rows");
  assert.equal(chipOf(rows(menu)[3])._attrs["aria-pressed"], "true", "the chip now reads selected");
  assert.equal(chipOf(rows(menu)[3]).classList.contains("tag-chip-off"), false);
  rows(menu)[2]._listeners.click();          // infra: on → off
  assert.equal(chipOf(rows(menu)[2])._attrs["aria-pressed"], "false");
  assert.equal(chipOf(rows(menu)[2]).classList.contains("tag-chip-off"), true);
  panel._closeViewsMenu();
});

test("drift pins: the inlined chip is the shared tagChip's pill up to the colour, and the fade and row shape match the shared menu", () => {
  const chipStyle = /const TAG_CHIP_STYLE = '([^']+)';/.exec(SRC)![1];
  const shared = /chip\.setAttribute\("style", "([^"]+)"\s*\n\s*\+ "border-radius:9px;" \+ \(opts && opts\.inheritSize \? "" : "font-size:0\.82em;"\)\s*\n\s*\+ "border:1px solid "/.exec(MENU);
  assert.ok(shared, "the shared tagChip's style is where the pin expects it");
  assert.equal(chipStyle, shared![1] + "border-radius:9px;font-size:0.82em;border:1px solid ", "the pill, byte for byte up to the colour");
  assert.match(SRC, /TAG_CHIP_STYLE \+ col \+ ';color:' \+ col \+ ';background:transparent;white-space:nowrap;font-weight:400;letter-spacing:normal;'/,
    "…and the tail after the colour carries the shared weight and tracking (T321: never bold)");
  assert.match(MENU, /\+ "font-weight:400;letter-spacing:normal;"/, "the shared pill's own tail, the same bytes");
  // the dialog's two other tag pills (its own signatures: the drag cell's pill, the per-pane filter pills): never bold either (T321)
  assert.match(SRC, /'border-radius:10px;border:1px solid ' \+ tc \+ ';color:' \+ tc \+ ';background:transparent;font-weight:400;'/, "the tag row's pill");
  assert.match(SRC, /'cursor:pointer;padding:1px 8px;border-radius:9px;font-size:0.82em;font-weight:400;'/, "the filter pills");
  assert.match(SRC, /\(selected \? 'background:' \+ SEL_BG \+ ';opacity:1;' : 'background:transparent;opacity:0\.6;'\)/, "selected is the wash and the full opacity, not a weight");
  assert.doesNotMatch(SRC, /font-weight:650;'\s*\n?[^\n]*(tc|c2)\b/, "no bold left on a tag-coloured pill");
  // the view's other inlined chips carry the same tail: the dialog rows' lane chips, the [+] join options, the corner filter chips
  assert.match(SRC, /'color:' \+ tc \+ ';border:1px solid ' \+ tc \+ ';background:transparent;font-weight:400;letter-spacing:normal;'\);\s+\/\/ the one tag chip \(T321\)\n/, "the lane chips");
  assert.match(SRC, /'display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:9px;font-size:0\.82em;cursor:pointer;white-space:nowrap;'\s*\n\s*\+ 'color:' \+ tc \+ ';border:1px solid ' \+ tc \+ ';background:transparent;font-weight:400;letter-spacing:normal;'\);\s+\/\/ the one tag chip \(T321\): the join option/, "the join options, the chip's shape");
  assert.match(SRC, /\.romp-tl-chip\{display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:9px;'\s*\n\s*\+ 'font-size:0\.82em;border:1px solid;background:transparent;white-space:nowrap;font-weight:400;letter-spacing:normal\}/, "the corner filter chips");
  assert.match(SRC, /const TAG_CHIP_OFF_OPACITY = '0\.45';/);
  assert.match(SRC, /const TAG_CHIP_OFF_CLASS = 'tag-chip-off';/);
  // the shared menu's own constants and chip row (T283, on main): each pin fails LOUDLY when its anchor moves,
  // never skips, so a reformat of the shared loop cannot leave a later padding or radius change uncaught
  const off = /export const TAG_CHIP_OFF_OPACITY = "([^"]+)";/.exec(MENU);
  assert.ok(off, "the shared fade constant is where the pin expects it");
  assert.equal(off![1], "0.45", "the shared fade");
  const cls = /export const TAG_CHIP_OFF_CLASS = "([^"]+)";/.exec(MENU);
  assert.ok(cls, "the shared state class is where the pin expects it");
  assert.equal(cls![1], "tag-chip-off", "the shared state class");
  const row = /r\.setAttribute\("style", "([^"]+)"\);\s*\n\s*const chip = tagChip\(u\.name/.exec(MENU);
  assert.ok(row, "the shared chip row is where the pin expects it");
  assert.equal(/const TAG_CHIP_ROW_STYLE = '([^']+)';/.exec(SRC)![1], row![1], "the chip row's shape");
  // the shared tag rows never carried a dot after T283; this copy's plain rows carry none either
  const views = SRC.slice(SRC.indexOf("  _openViewsMenu(anchorEl) {"), SRC.indexOf("  _openDisplayMenu(anchorEl) {"));
  assert.doesNotMatch(views, /border-radius:50%/, "no colour dot in the views menu");
  assert.match(views, /c\.setAttribute\('style', MENU_CHECK_STYLE\);/, "the ✓-in-circle stays for All and (no tags)");
});
