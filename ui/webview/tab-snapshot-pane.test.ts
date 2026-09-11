// A SECTION AT A GLANCE, THE PANE (render.ts): the view's block (snapView, the host, renderSnapshot, the exits),
// the strip's key handlers (onTabKey, focusActiveTab, unfoldSectionOf), the section header (makeGroupHead), the
// #tabs delegate's two header acts and the nav trail's deps are LIFTED out of render.ts and run over a small fake
// DOM, the way tab-strip-skip-exec.test.ts drives renderTabs: the rows paint from the model, a push that changes
// nothing ticks only the ago texts, a changed row keeps its node, the section gone from the strip ends the view,
// Escape leaves it in two phases, a row click opens its session only while the session is still on the strip,
// the folded-away active tab's header is its stand-in for focus, the arrows and Enter, a header click folds and
// shows the section and the way back is the same header's next click, a pick from the view records the reader's
// spot on the trail, and a pick of a folded-away tab opens its section. The wiring the slices cannot reach
// (showActive's branch, setActive, the strip's follow) is pinned at the source. The notes-api demo world.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { viewTagUnion } from "./session-views";
import { parseTabGroups, planStrip, setSectionCollapsed, homeSectionOf, neighborOfFolded, headWords, type StripItem, type TabSection } from "./tab-groups";
import { sectionPip, sectionPipMembers, sectionPipTitle } from "./tab-state";
import { snapshotModel, snapshotHeading, rowWords, type SnapModel } from "./tab-snapshot";
import { rowStillOpen, installSnapshotEscape, reconcileRows } from "./tab-snapshot-view";

const requireCjs = createRequire(__filename);
const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const CSS = ui("webview", "styles.css");
const SHOW_SIG = "function showActive(keep?: { uuid: string; y: number } | null) {";
const SHOW_AT = RENDER.indexOf(SHOW_SIG);
assert.ok(SHOW_AT >= 0, "render.ts: showActive's signature moved; re-anchor");
const SNAP_AT = RENDER.indexOf("let snapView: string | null = null;");
const SHOW = RENDER.slice(SHOW_AT, SHOW_AT + 7500);
const KEYS_AT = RENDER.indexOf("function onTabKey(e: KeyboardEvent) {");
const KEYS_END = RENDER.indexOf('// "Enter to start typing" lands on whatever', KEYS_AT);
const TABS = RENDER.slice(RENDER.indexOf("function renderTabs() {"), RENDER.indexOf("function dismissTabMenu() {"));
const HEAD = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("function sectionHeadOf("));
const ACTS_AT = RENDER.indexOf('"toggle-group": (el) => {');
const ACTS = RENDER.slice(ACTS_AT, RENDER.indexOf("close: (el) => {", ACTS_AT));   // the #tabs delegate's two header acts, up to the tab's close
const NAV = RENDER.slice(RENDER.indexOf("const navHist = new NavHistory({"), RENDER.indexOf("function setActive(id: string"));

// ── a fake DOM: enough of Element for the pane's paint, with a small selector engine for the queries it makes ──
type Compound = { scope: boolean; id: string | null; classes: string[]; attrs: Array<[string, string | null]> };
function parseCompound(s: string): Compound {
  const c: Compound = { scope: false, id: null, classes: [], attrs: [] };
  const re = /:scope|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g;
  for (const m of s.matchAll(re)) {
    if (m[0] === ":scope") c.scope = true;
    else if (m[1]) c.id = m[1];
    else if (m[2]) c.classes.push(m[2]);
    else if (m[3]) c.attrs.push([m[3], m[4] ?? null]);
  }
  return c;
}
/** right-to-left: [[compound, combinator to its LEFT neighbour], ...] */
function parseSelector(sel: string): Array<{ c: Compound; comb: ">" | " " | null }> {
  const parts = sel.trim().split(/\s*>\s*|\s+/);
  const combs = sel.trim().match(/\s*>\s*|\s+/g) || [];
  const out = parts.map((p, i) => ({ c: parseCompound(p), comb: i === 0 ? null : (combs[i - 1].includes(">") ? ">" : " ") as ">" | " " | null }));
  return out;
}
class FakeDoc { activeElement: FakeEl | null = null; body = new FakeEl("body"); }
const camel = (k: string) => k.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
class FakeEl {
  tag: string; id = ""; className: string; children: FakeEl[] = []; parent: FakeEl | null = null;
  dataset: Record<string, string> = {}; attrs: Record<string, string> = {}; listeners: Record<string, Function[]> = {};
  textContent = ""; title = ""; tabIndex = -1; type = ""; disabled = false; scrollTop = 0; draggable = false;
  style: Record<string, any> = { display: "", background: "", color: "", setProperty(k: string, v: string) { (this as Record<string, string>)[k] = v; } };   // setProperty: the row's --chip-bg (T322)
  classList: { add: (...c: string[]) => void; remove: (...c: string[]) => void; contains: (c: string) => boolean; toggle: (c: string, on?: boolean) => void };
  constructor(tag: string, cls = "") {
    this.tag = tag; this.className = cls;
    const self = this;
    this.classList = {
      add(...c) { for (const x of c) if (!self.has(x)) self.className = (self.className + " " + x).trim(); },
      remove(...c) { for (const x of c) self.className = self.className.split(/\s+/).filter((y) => y !== x).join(" "); },
      contains(x) { return self.has(x); },
      toggle(x, on) { if (on === undefined ? !self.has(x) : on) this.add(x); else this.remove(x); },
    };
  }
  has(x: string): boolean { return this.className.split(/\s+/).includes(x); }
  get childElementCount(): number { return this.children.length; }
  get firstElementChild(): FakeEl | null { return this.children[0] ?? null; }
  get lastElementChild(): FakeEl | null { return this.children[this.children.length - 1] ?? null; }
  appendChild(c: FakeEl): FakeEl { this.insertBefore(c, null); return c; }
  append(...cs: FakeEl[]): void { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs: FakeEl[]): void { for (const c of [...this.children]) this.removeChild(c); this.append(...cs); }
  insertBefore(c: FakeEl, ref: FakeEl | null): FakeEl {
    // the DOM's rule: a node already in a tree is detached first, and a detached focused node loses focus (the
    // browser's focus fixup), which is what the pane's refocus answers
    if (c.parent) { const p = c.parent; p.children = p.children.filter((x) => x !== c); c.parent = null; if (DOC.activeElement && c.contains(DOC.activeElement)) DOC.activeElement = DOC.body; }
    c.parent = this;
    const j = ref ? this.children.indexOf(ref) : -1;
    if (j < 0) this.children.push(c); else this.children.splice(j, 0, c);
    return c;
  }
  removeChild(c: FakeEl): FakeEl { this.children = this.children.filter((x) => x !== c); c.parent = null; if (DOC.activeElement && c.contains(DOC.activeElement)) DOC.activeElement = DOC.body; return c; }
  remove(): void { this.parent?.removeChild(this); }
  contains(n: unknown): boolean { return n === this || this.children.some((c) => c.contains(n)); }
  setAttribute(k: string, v: string): void { if (k.startsWith("data-")) this.dataset[camel(k.slice(5))] = v; else this.attrs[k] = v; }
  getAttribute(k: string): string | null { if (k.startsWith("data-")) return this.dataset[camel(k.slice(5))] ?? null; return this.attrs[k] ?? null; }
  addEventListener(t: string, f: Function): void { (this.listeners[t] ??= []).push(f); }
  fire(t: string, ev: any = {}): void { for (const f of this.listeners[t] ?? []) f(ev); }
  focus(): void { DOC.activeElement = this; }
  click(): void { CLICKS.push(this); }   // a header's Enter or Space presses it; the real click bubbles to the #tabs delegate (tabsActs below)
  matches1(c: Compound, scope: FakeEl): boolean {
    if (c.scope && this !== scope) return false;
    if (c.id !== null && this.id !== c.id) return false;
    if (!c.classes.every((x) => this.has(x))) return false;
    return c.attrs.every(([k, v]) => (v === null ? this.getAttribute(k) !== null : this.getAttribute(k) === v));
  }
  matchesChain(chain: ReturnType<typeof parseSelector>, i: number, scope: FakeEl): boolean {
    if (!this.matches1(chain[i].c, scope)) return false;
    if (i === 0) return true;
    const comb = chain[i].comb;
    if (comb === ">") return !!this.parent && this.parent.matchesChain(chain, i - 1, scope);
    for (let p = this.parent; p; p = p.parent) if (p.matchesChain(chain, i - 1, scope)) return true;
    return false;
  }
  *walk(): Generator<FakeEl> { for (const c of this.children) { yield c; yield* c.walk(); } }
  querySelectorAll(sel: string): FakeEl[] { const chain = parseSelector(sel); return [...this.walk()].filter((n) => n.matchesChain(chain, chain.length - 1, this)); }
  querySelector(sel: string): FakeEl | null { return this.querySelectorAll(sel)[0] ?? null; }
  closest(sel: string): FakeEl | null { const chain = parseSelector(sel); for (let n: FakeEl | null = this; n; n = n.parent) if (n.matchesChain(chain, chain.length - 1, n)) return n; return null; }
  byId(id: string): FakeEl | null { if (this.id === id) return this; for (const c of this.children) { const f = c.byId(id); if (f) return f; } return null; }
  text(): string { return this.children.length ? this.children.map((c) => c.text()).join("") : this.textContent; }
}
const DOC = new FakeDoc();
const CLICKS: FakeEl[] = [];

type Hooks = {
  content: FakeEl; bar: FakeEl; composer: FakeEl; sendBtn: FakeEl;
  sessions: Map<string, any>; ledgers: Map<string, any>; tabMeta: Map<string, any>; closingTabs: Map<string, number>; views: Map<string, any>;
  lastStripItems: StripItem[]; order: string[]; collapsed: Set<string>; nowMs: number; pickerOpen: boolean;
  calls: string[]; delegates: Array<{ root: FakeEl; handlers: Record<string, (node: FakeEl, ev: Event) => void> }>;
  winCap: Function[]; winBub: Function[]; writes: any[];
  FakeEl: typeof FakeEl; doc: FakeDoc;
  snapshotModel: typeof snapshotModel; snapshotHeading: typeof snapshotHeading; rowWords: typeof rowWords;
  rowStillOpen: typeof rowStillOpen; installSnapshotEscape: typeof installSnapshotEscape; reconcileRows: typeof reconcileRows;
  homeSectionOf: typeof homeSectionOf; neighborOfFolded: typeof neighborOfFolded; setSectionCollapsed: typeof setSectionCollapsed;
  headWords: typeof headWords; sectionPip: typeof sectionPip; sectionPipMembers: typeof sectionPipMembers; sectionPipTitle: typeof sectionPipTitle;
  groups: ReturnType<typeof parseTabGroups>;
  navDeps: { now: () => { sid: string; top: number } | null } | null;   // what render.ts hands NavHistory (the stub constructor keeps it)
};
type Api = {
  renderSnapshot: () => boolean; hideSnapshot: () => void; leaveSnapshot: () => void; snapshotHost: () => FakeEl | null;
  onTabKey: (e: any) => void; focusActiveTab: () => void; unfoldSectionOf: (id: string) => void;
  makeGroupHead: (sec: TabSection, collapsed: boolean, holdsActive: boolean, hidden: readonly string[]) => FakeEl;
  acts: { "toggle-group": (el: FakeEl) => void; "show-transcript": () => void };
  get: () => { snapView: string | null; snapModel: SnapModel | null; snapKeep: any; tabPointerHeld: boolean; renderPendingWhilePressed: boolean; activeId: string | null };
  set: (p: Record<string, unknown>) => void;
};

function lift(): (H: Hooks) => Api {
  assert.ok(SNAP_AT > 0 && SNAP_AT < SHOW_AT, "the pane block sits right above showActive: re-anchor");
  assert.ok(KEYS_AT > 0 && KEYS_END > KEYS_AT, "onTabKey .. focusActiveTab .. unfoldSectionOf run together above the composer-focus helper: re-anchor");
  assert.ok(HEAD.length > 0 && ACTS.includes('"show-transcript"') && NAV.includes("apply: (spot) => {"), "makeGroupHead, the #tabs delegate's header acts or the nav trail's deps moved: re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(SNAP_AT, SHOW_AT) + RENDER.slice(KEYS_AT, KEYS_END) + HEAD + NAV
    + "const tabsActs = {\n" + ACTS + "};\n", { loader: "ts" }).code;
  const prelude = `
    const H = HOOKS;
    let activeId = null, tabPointerHeld = false, renderPendingWhilePressed = false, draggedGroup = null;
    let ctxMenuEl = null, metaMenuEl = null, citePreviewEl = null, openCommentKey = null;
    const settings = { stripGroupRows: true };
    let order = H.order, lastStripItems = H.lastStripItems, collapsedTabIds = H.collapsed;
    const sessions = H.sessions, ledgers = H.ledgers, tabMeta = H.tabMeta, closingTabs = H.closingTabs, views = H.views;
    const el = (tag, cls) => new H.FakeEl(tag, cls);
    const document = {
      get activeElement() { return H.doc.activeElement; },
      get body() { return H.doc.body; },
      getElementById: (id) => id === "content" ? H.content : id === "tabs" ? H.bar : id === "composer-input" ? H.composer : id === "composer-send" ? H.sendBtn : H.content.byId(id),
      createElement: (tag) => new H.FakeEl(tag),
      createTextNode: (t) => { const n = new H.FakeEl("#text"); n.textContent = t; return n; },
      querySelector: (sel) => (sel === ".picker-overlay" && H.pickerOpen ? new H.FakeEl("div", "picker-overlay") : null),
    };
    const window = { addEventListener: (t, f, cap) => { (cap ? H.winCap : H.winBub).push(f); } };
    const Date = { now: () => H.nowMs };
    const delegate = (root, handlers) => { H.delegates.push({ root, handlers }); };
    const setActive = (id) => { H.calls.push("setActive:" + id); activeId = id; };   // recorded, and the pick lands: focusActiveTab then reads the new active
    const renderTabs = () => { H.calls.push("renderTabs"); };
    const showActive = () => { H.calls.push("showActive"); };
    const focusComposerOrAsk = () => { H.calls.push("focusComposerOrAsk"); return !H.composer.disabled; };
    const tabInAdjacentRow = () => null;
    const visibleOrder = () => order.filter((id) => !collapsedTabIds.has(id));
    const isTypingTarget = (t) => !!t && t.typing === true;
    const agehms = (s) => Math.floor(s) + "s";
    const ageColorReadable = (s) => "age-" + Math.floor(s);
    const hostNameNodes = (name) => [document.createTextNode(name)];
    const snapshotModel = H.snapshotModel, snapshotHeading = H.snapshotHeading, rowWords = H.rowWords;
    const rowStillOpen = H.rowStillOpen, installSnapshotEscape = H.installSnapshotEscape, reconcileRows = H.reconcileRows;
    const homeSectionOf = H.homeSectionOf, neighborOfFolded = H.neighborOfFolded, setSectionCollapsed = H.setSectionCollapsed;
    const tabGroups = () => H.groups;
    const writeTabGroups = (st) => { H.writes.push(st); };
    // the header's parts and gestures: the pure words and pip rules for real, the tag chip and the drag helpers stubs
    const headWords = H.headWords, sectionPip = H.sectionPip, sectionPipMembers = H.sectionPipMembers, sectionPipTitle = H.sectionPipTitle;
    const tagChip = (label) => { const c = el("span", "tag-chip"); c.textContent = label; return c; };
    const dragImageBlank = () => el("div"); const hideTabTip = () => {};
    // the nav trail: the class a stub that keeps the deps render.ts hands it; the landing's helpers inert
    class NavHistory { constructor(deps) { H.navDeps = deps; } }
    const whenChatVisible = (f) => f(); const writeScroll = () => {};
  `;
  const epilogue = `
    return { renderSnapshot, hideSnapshot, leaveSnapshot, snapshotHost, onTabKey, focusActiveTab, unfoldSectionOf, makeGroupHead, acts: tabsActs,
      get: () => ({ snapView, snapModel, snapKeep, tabPointerHeld, renderPendingWhilePressed, activeId }),
      set: (p) => { for (const k of Object.keys(p)) {
        if (k === "snapView") snapView = p[k]; else if (k === "snapKeep") snapKeep = p[k]; else if (k === "activeId") activeId = p[k];
        else if (k === "tabPointerHeld") tabPointerHeld = p[k]; else if (k === "lastStripItems") lastStripItems = p[k]; else if (k === "collapsedTabIds") collapsedTabIds = p[k];
        else if (k === "ctxMenuEl") ctxMenuEl = p[k]; else if (k === "order") order = p[k];
        else throw new Error("unknown knob " + k); } } };
  `;
  return new Function("HOOKS", prelude + js + epilogue) as (H: Hooks) => Api;
}

const T0 = 1781100000;
const iso = (t: number) => new Date(t * 1000).toISOString();
const V = { active: "all", tags: [{ id: "g1", name: "qa", color: "#DD42FF", members: ["tests", "api"] }, { id: "g2", name: "infra", color: "#4EC9B0", members: ["web", "api"] }] };
const unions = viewTagUnion(V);

/** The notes-api world: infra (web, api) and qa (api, tests), web active, infra's view about to show. */
function world(active = "web", groups = parseTabGroups(null), ids = ["web", "api", "tests"]) {
  DOC.activeElement = null; CLICKS.length = 0;
  const content = new FakeEl("div"); content.id = "content";
  const bar = new FakeEl("div"); bar.id = "tabs";
  const plan = planStrip(ids, unions, groups, active, false);
  for (const it of plan.items) {
    if ("head" in it) { const h = new FakeEl("div", "tab-group-head" + (it.folded ? " collapsed" : "")); h.dataset.group = String(it.head.name); h.tabIndex = 0; bar.appendChild(h); }
    else { const t = new FakeEl("div", "tab"); t.dataset.id = it.id; t.tabIndex = 0; bar.appendChild(t); }
  }
  const composer = new FakeEl("textarea"); composer.id = "composer-input";
  const sessions = new Map<string, any>([
    ["web", { name: "web", color: { bg: "#3a7bd5", fg: "#ffffff" }, status: { state: "working", sinceEpoch: (T0 - 30) * 1000 },
              events: [{ kind: "assistant", md: "Adding the **list** page.", ts: iso(T0 - 40) }] }],
    ["api", { name: "api", color: { bg: "#d53a3a", fg: "#ffffff" }, status: { state: "idle", sinceEpoch: (T0 - 600) * 1000 }, events: [] }],
  ]);
  const ledgers = new Map<string, any>([
    ["web", { summary: "Building the notes-api web pages", workingNote: "editing the list page", needsInput: false, tree: [{ text: "Add the notes list page", current: true }] }],
    ["api", { summary: "Designing the notes schema", needsInput: true, tree: [] }],
  ]);
  const H: Hooks = { content, bar, composer, sendBtn: new FakeEl("button"), sessions, ledgers, tabMeta: new Map([["tests", { name: "tests", color: null }]]),
    closingTabs: new Map(), views: new Map(), lastStripItems: plan.items, order: ids, collapsed: plan.folded, nowMs: T0 * 1000, pickerOpen: false,
    calls: [], delegates: [], winCap: [], winBub: [], writes: [], FakeEl, doc: DOC,
    snapshotModel, snapshotHeading, rowWords, rowStillOpen, installSnapshotEscape, reconcileRows, homeSectionOf, neighborOfFolded, setSectionCollapsed,
    headWords, sectionPip, sectionPipMembers, sectionPipTitle, groups, navDeps: null };
  const api = lift()(H);
  api.set({ activeId: active });
  return { H, api, content, bar, sessions, ledgers };
}
const rowsOf = (host: FakeEl) => host.querySelectorAll(".snap-item");
type Head = { head: TabSection; folded: boolean; active: boolean; hidden: string[] };
const headOf = (items: readonly StripItem[], name: string) => items.find((i) => "head" in i && i.head.name === name) as Head;
/** The header render.ts would paint for a section of the plan, replacing the world's stand-in node for it on the bar. */
function realHead(api: Api, bar: FakeEl, it: Head): FakeEl {
  const head = api.makeGroupHead(it.head, it.folded, it.active, it.hidden);
  const fake = bar.querySelector(`.tab-group-head[data-group="${it.head.name}"]`);
  if (fake) { const at = bar.children.indexOf(fake); bar.removeChild(fake); bar.insertBefore(head, bar.children[at] ?? null); }
  return head;
}
const keyOn = (node: FakeEl, key: string) => { const e: any = { key, prevented: false, preventDefault() { this.prevented = true; } }; node.fire("keydown", e); return e as { prevented: boolean }; };
const esc = (H: Hooks, target: any = null) => {
  const e: any = { key: "Escape", target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  for (const f of H.winCap) f(e);
  for (const f of H.winBub) f(e);
  return e;
};

test("executed: the view paints one row per member from the model: heading, keyed items, a real button per row with the pip, the name in its color, the words, the now line, the note and the time", () => {
  const { H, api, content } = world();
  api.set({ snapView: "infra" });
  assert.equal(api.renderSnapshot(), true);
  const host = content.byId("tab-snapshot")!;
  assert.ok(host, "the host hangs off #content");
  assert.equal(host.getAttribute("role"), "region"); assert.equal(host.style.display, "");
  assert.equal(host.getAttribute("aria-label"), "Overview of infra: 2 sessions; click one to open it");
  const head = host.children[0];
  // "Overview of <the tag's ordinary chip> <count>" (T322): the words, the chip slot holding tagChip's pill, the count
  assert.deepEqual([head.tag, head.className, head.children.map((c) => c.className)], ["h2", "snap-head", ["snap-of", "snap-chip-slot", "snap-count"]]);
  assert.equal(head.children[0].textContent, "Overview of");
  const chip = head.children[1].children[0];
  assert.ok(chip.has("snap-chip"), "the chip carries the view's class: " + chip.className); assert.equal(chip.text(), "infra", "the tag's name in the chip");
  assert.ok(chip.has("tag-chip"), "built by tagChip (the world's stub marks its pills): " + chip.className);
  assert.equal(head.children[2].textContent, "2 sessions");
  const items = rowsOf(host);
  assert.deepEqual(items.map((i) => [i.getAttribute("role"), i.dataset.id]), [["listitem", "web"], ["listitem", "api"]], "keyed by the session id, in strip order");
  const [web, api_] = items.map((i) => i.children[0]);
  assert.deepEqual([web.tag, web.type, web.dataset.act, web.dataset.id, web.className], ["button", "button", "open", "web", "snap-row"], "a real button: Tab reaches it, Enter opens; the stable host's delegate acts");
  assert.equal(web.getAttribute("aria-label"), "web; working; Add the notes list page; its note: editing the list page");
  assert.equal(web.title, "Last message: Adding the list page.\nClick to open this session.");
  const parts = web.children.map((c) => c.className);
  assert.deepEqual(parts, ["snap-pip working", "snap-sess", "snap-now", "snap-when", "snap-note"], "pip, name, now, when, the note last (the wrapping row puts it under the first line)");
  assert.equal(web.children[0].getAttribute("aria-hidden"), "true", "the pip is decoration: its phrase rides the label");
  assert.equal(web.children[1].text(), "web"); assert.equal(web.children[1].style.color, "#3a7bd5", "the identity color on the name");
  assert.equal(web.children[2].textContent, "Add the notes list page");
  assert.deepEqual([web.children[3].dataset.t, web.children[3].textContent, web.children[3].style.color], [String(T0 - 40), "40s ago", "age-40"], "the model carries the epoch; the renderer formats it");
  assert.equal(web.children[4].textContent, "editing the list page");
  assert.deepEqual(api_.children.map((c) => c.className), ["snap-pip", "snap-sess", "snap-flag needs", "snap-now", "snap-when"], "an idle session the feed files under needs-you: no pip, the word");
  assert.equal(api_.children[2].textContent, "needs you");
  assert.equal(H.delegates.length, 1, "one delegate, on the stable host, installed with it");
  assert.equal(H.delegates[0].root, host);
});

test("executed: a push that changes nothing a row shows moves nothing: the same nodes stand and only the ago texts tick; a changed row keeps its node, patched; a member gone or come adds or removes one node", () => {
  const { H, api, content, sessions } = world();
  api.set({ snapView: "infra" });
  api.renderSnapshot();
  const host = content.byId("tab-snapshot")!;
  const [webItem, apiItem] = rowsOf(host);
  const webBtn = webItem.children[0];
  const partsBefore = [...webBtn.children];
  // a fresh frame: new objects, the same content, the clock 10 s on
  sessions.set("web", JSON.parse(JSON.stringify(sessions.get("web"))));
  H.nowMs = (T0 + 10) * 1000;
  const m1 = api.get().snapModel;
  assert.equal(api.renderSnapshot(), true);
  assert.equal(api.get().snapModel, m1, "the same model object");
  assert.deepEqual(rowsOf(host), [webItem, apiItem], "the same nodes");
  assert.equal(webBtn.querySelector(".snap-when")!.textContent, "50s ago", "the time ticked in place");
  assert.ok(webBtn.children.length === partsBefore.length && webBtn.children.every((c, i) => c === partsBefore[i]),
    "the row's parts stand too (the same pip, name and time nodes): the tick re-texts, it does not refill the row, so a hover's title and a focused part are untouched");
  assert.equal(host.children[0], host.querySelector(".snap-head"), "the heading stands too");
  // new information: web's state changes; its node is kept and patched
  sessions.set("web", { ...sessions.get("web"), status: { state: "ready", sinceEpoch: T0 * 1000 } });
  api.renderSnapshot();
  assert.notEqual(api.get().snapModel, m1);
  assert.equal(rowsOf(host)[0], webItem, "the item node stands"); assert.equal(webItem.children[0], webBtn, "and so does its button");
  assert.equal(webBtn.children[0].className, "snap-pip", "with the new parts (no pip while ready)");
  assert.equal(webBtn.getAttribute("aria-label"), "web; Add the notes list page; its note: editing the list page");
  // a member gone from the section and one come: the standing node stays, one is removed, one made
  api.set({ lastStripItems: [{ head: { name: "infra", localId: "g2", color: "#4EC9B0", ids: ["web", "tests"] }, folded: false, active: true, hidden: [] }, { id: "web" }, { id: "tests" }] });
  api.renderSnapshot();
  const after = rowsOf(host);
  assert.deepEqual(after.map((i) => i.dataset.id), ["web", "tests"]);
  assert.equal(after[0], webItem, "web's node stands");
  assert.deepEqual([after[1].children[0].className, after[1].children[0].getAttribute("aria-label")], ["snap-row loading", "tests; opening"], "a placeholder tab (its meta alone) is a loading row");
  assert.equal(after[1].children[0].querySelector(".snap-now")!.textContent, "opening…");
  assert.equal(host.getAttribute("aria-label"), "Overview of infra: 2 sessions; click one to open it");
});

test("executed: focus survives the push that changes the rows: a moved row is re-focused on its own node, a removed row hands focus to the row in its place", () => {
  const { api, content } = world();
  api.set({ snapView: "infra" });
  api.renderSnapshot();
  const host = content.byId("tab-snapshot")!;
  const [webItem, apiItem] = rowsOf(host);
  apiItem.children[0].focus();
  // a reorder: api's node moves (insertBefore detaches it, which the DOM turns into a blur); the same node is focused again
  api.set({ lastStripItems: [{ head: { name: "infra", localId: "g2", color: "#4EC9B0", ids: ["api", "web"] }, folded: false, active: true, hidden: [] }, { id: "api" }, { id: "web" }] });
  api.renderSnapshot();
  assert.deepEqual(rowsOf(host), [apiItem, webItem], "moved, not remade");
  assert.equal(DOC.activeElement, apiItem.children[0], "focus back on the moved node");
  // the focused row's session leaves the section: the row now in its place takes focus (the last, when it was last)
  api.set({ lastStripItems: [{ head: { name: "infra", localId: "g2", color: "#4EC9B0", ids: ["web"] }, folded: false, active: true, hidden: [] }, { id: "web" }] });
  api.renderSnapshot();
  assert.deepEqual(rowsOf(host), [webItem]);
  assert.equal(DOC.activeElement, webItem.children[0], "not dropped to body");
});

test("executed: the section gone from the strip ends the view (the absence is the event): snapView clears, the host hides, and the caller is told to show the transcript; the held reading spot is written back", () => {
  const { api, content, H } = world();
  const v = { el: new FakeEl("div"), scrollTop: 1200, stick: false };
  H.views.set("web", v);
  api.set({ snapView: "infra", snapKeep: { v, scrollTop: 300, stick: true } });
  api.renderSnapshot();
  const host = content.byId("tab-snapshot")!;
  v.scrollTop = 40; v.stick = false;   // the scroll listener recorded the view's scrolls onto the active view meanwhile
  api.set({ lastStripItems: planStrip(["tests"], unions, parseTabGroups(null), "web", false).items });   // infra's last visible member gone: no infra header
  assert.equal(api.renderSnapshot(), false);
  assert.equal(api.get().snapView, null); assert.equal(api.get().snapModel, null);
  assert.equal(host.style.display, "none");
  assert.deepEqual([v.scrollTop, v.stick], [300, true], "the reader's place, from before the view, is back on the view");
  assert.equal(api.get().snapKeep, null);
  assert.equal(api.renderSnapshot(), false, "nothing to paint without a section");
});

test("executed: leaving puts the transcript back through the strip and showActive, and hands focus to the active tab only when a row held it", () => {
  const { api, content, H, bar } = world();
  api.set({ snapView: "infra" });
  api.renderSnapshot();
  const host = content.byId("tab-snapshot")!;
  H.calls.length = 0;
  bar.children[1].focus();   // focus on a tab of the strip, not on the view
  api.leaveSnapshot();
  assert.equal(api.get().snapView, null);
  assert.deepEqual(H.calls, ["renderTabs", "showActive"], "the strip drops the header's mark, showActive puts the transcript back; focus is left where it was");
  api.leaveSnapshot();
  assert.deepEqual(H.calls, ["renderTabs", "showActive"], "not showing: nothing to leave");
  api.set({ snapView: "infra" }); api.renderSnapshot(); H.calls.length = 0;
  rowsOf(host)[0].children[0].focus();
  api.leaveSnapshot();
  assert.equal(DOC.activeElement, bar.querySelector('.tab[data-id="web"]'), "focus was on a row: the active tab takes it (hiding the host would drop it to body)");
});

test("executed: Escape leaves the view in two phases on the window: armed at capture unless a layer of this page or a typing target owns it, decided at bubble unless something marked it in between", () => {
  const { api, H } = world();
  api.set({ snapView: "infra" }); api.renderSnapshot();
  assert.equal(H.winCap.length, 1); assert.equal(H.winBub.length, 1, "one listener per phase, installed with the module");
  H.calls.length = 0;
  let e = esc(H);
  assert.equal(api.get().snapView, null, "left"); assert.equal(e.defaultPrevented, true, "and marked as taken");
  assert.deepEqual(H.calls, ["renderTabs", "showActive"]);
  api.set({ snapView: "infra" }); api.renderSnapshot(); H.calls.length = 0;
  api.set({ ctxMenuEl: new FakeEl("div") });
  esc(H); assert.equal(api.get().snapView, "infra", "a menu open: its Escape, not ours");
  api.set({ ctxMenuEl: null }); H.pickerOpen = true;
  esc(H); assert.equal(api.get().snapView, "infra", "the picker or a confirm up: theirs");
  H.pickerOpen = false;
  esc(H, { typing: true }); assert.equal(api.get().snapView, "infra", "a field keeps its Escape");
  e = { key: "Escape", target: null, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  for (const f of H.winCap) f(e); e.defaultPrevented = true; for (const f of H.winBub) f(e);
  assert.equal(api.get().snapView, "infra", "marked between the phases (the shell's chain closed a panel): yielded");
  esc(H); assert.equal(api.get().snapView, null, "the next unclaimed Escape leaves");
});

test("executed: a row's click opens its session and focuses its tab, only while the session is still on the strip; a press on the view latches the strip's click-safe hold", () => {
  const { api, H, content } = world();
  api.set({ snapView: "infra" }); api.renderSnapshot();
  const host = content.byId("tab-snapshot")!;
  const open = H.delegates[0].handlers.open;
  H.calls.length = 0;
  open(rowsOf(host)[0].children[0], {} as Event);
  assert.deepEqual(H.calls, ["setActive:web"], "the pick, then the strip's own focus rule");
  assert.equal(DOC.activeElement, H.bar.querySelector('.tab[data-id="web"]'));
  H.calls.length = 0;
  H.sessions.delete("api"); H.tabMeta.set("api", { name: "api" });   // its closed frame arrived mid-press; the strip's meta lingers until the next tabOrder frame
  open(rowsOf(host)[1].children[0], {} as Event);
  assert.deepEqual(H.calls, [], "a session gone from under the row opens nothing");
  H.sessions.set("api", { name: "api", status: { state: "idle" } }); H.closingTabs.set("api", 1);
  open(rowsOf(host)[1].children[0], {} as Event);
  assert.deepEqual(H.calls, [], "a tab the user closed opens nothing, whatever the maps still hold");
  H.closingTabs.clear();
  const stray = new FakeEl("button"); open(stray, {} as Event);
  assert.deepEqual(H.calls, [], "no id, nothing");
  assert.equal(api.get().tabPointerHeld, false);
  host.fire("pointerdown");
  assert.equal(api.get().tabPointerHeld, true, "a press on a row holds the strip's rebuild until the release, as a press on the strip does");
  // a rebuild while pressed waits for the release: the times still tick, the DOM stands
  const before = rowsOf(host);
  H.sessions.set("web", { ...H.sessions.get("web"), status: { state: "ready", sinceEpoch: T0 * 1000 } });
  assert.equal(api.renderSnapshot(), true);
  assert.deepEqual(rowsOf(host), before); assert.equal(api.get().renderPendingWhilePressed, true);
});

test("executed: the folded-away active tab's header is its stand-in: focusActiveTab lands on it, the arrows step from its place, and a pick of a folded-away tab opens the first folded holder", () => {
  const folded = setSectionCollapsed(parseTabGroups(null), "infra", true);
  const { api, H, bar } = world("web", folded);   // qa: api, tests | infra(folded): web, api
  assert.ok(H.collapsed.has("web") && !bar.querySelector('.tab[data-id="web"]'), "web has no tab node: infra's header stands in for it");
  api.focusActiveTab();
  assert.equal(DOC.activeElement, bar.querySelector('.tab-group-head[data-group="infra"]'), "focus lands on the header");
  H.calls.length = 0;
  api.onTabKey({ key: "ArrowRight", preventDefault() {} });
  assert.deepEqual(H.calls, ["setActive:api"], "from the header's place (last on the strip) the step wraps to the first tab");
  assert.equal(DOC.activeElement, bar.querySelector('.tab[data-id="api"]'), "focus follows onto the tab");
  api.set({ activeId: "web" }); H.calls.length = 0;   // the folded-away tab read again: the header stands in again
  api.onTabKey({ key: "ArrowLeft", preventDefault() {} });
  assert.deepEqual(H.calls, ["setActive:tests"], "and from the header's place to the last tab before it");
  // a pick of the folded-away tab: its first folded holder opens (the store write; the event re-renders the strip)
  api.unfoldSectionOf("web");
  assert.equal(H.writes.length, 1);
  assert.deepEqual([H.writes[0].collapsed, H.writes[0].expanded], [[], []], "infra's fold is cleared (a name is stored only where it differs from the default)");
  api.unfoldSectionOf("tests");
  assert.equal(H.writes.length, 1, "a tab on screen opens nothing");
  // several holders, both folded: the first in the strip's order opens, the other stands
  const both = setSectionCollapsed(folded, "qa", true);
  const w2 = world("api", both);
  w2.api.unfoldSectionOf("api");
  assert.deepEqual(w2.H.writes[0].collapsed, ["infra"], "qa (first) opens; infra stays folded");
  // an open holder: the active tab has a node, focus goes there and the arrows walk the visible order
  const w3 = world("web");
  w3.api.focusActiveTab();
  assert.equal(DOC.activeElement, w3.bar.querySelector('.tab[data-id="web"]'));
  w3.api.onTabKey({ key: "ArrowRight", preventDefault() {} });
  assert.deepEqual(w3.H.calls, ["setActive:api"]);
});

test("executed: the stand-in header answers a tab's keys: the arrows step from its place with focus following, Enter drops into the message box while it takes input and presses the header otherwise, Space presses; an unmarked holder and any other header are plain buttons", () => {
  const folded = setSectionCollapsed(parseTabGroups(null), "infra", true);
  const { api, H, bar } = world("web", folded);   // qa: api, tests | infra(folded): web, api
  const inf = headOf(H.lastStripItems, "infra");
  assert.deepEqual([inf.folded, inf.active, inf.hidden], [true, true, ["web", "api"]]);
  const head = realHead(api, bar, inf);
  assert.deepEqual([head.dataset.act, head.dataset.folded, head.getAttribute("role"), head.getAttribute("aria-expanded"), head.getAttribute("aria-current"), head.tabIndex],
    ["toggle-group", "1", "button", "false", "true", 0], "the same fold button as the rest, marked current, a tab stop");
  assert.ok(head.has("collapsed") && head.has("holds-active") && !head.has("snap-shown"));
  assert.equal(head.title, headWords("infra", 2, 2, true, true).title, "the pure module's words");
  assert.equal(head.getAttribute("aria-label"), headWords("infra", 2, 2, true, true).label + "; " + sectionPipTitle("working", ["web"]),
    "the folded gist: web (hidden, working) puts the working pip on the header, and its phrase rides the spoken label");
  assert.deepEqual(head.children.map((c) => c.className), ["tag-chip tab-group-chip", "tab-group-caret", "tab-group-count", "tab-group-pip"], "chip, caret, count, then the pip");
  head.focus(); H.calls.length = 0;
  let e = keyOn(head, "ArrowRight");
  assert.equal(e.prevented, true);
  assert.deepEqual(H.calls, ["setActive:api"], "from the header's place (last on the strip) the step wraps to the first tab");
  assert.equal(DOC.activeElement, bar.querySelector('.tab[data-id="api"]'), "focus follows onto the tab (focusActiveTab)");
  api.set({ activeId: "web" }); head.focus(); H.calls.length = 0;
  e = keyOn(head, "ArrowLeft");
  assert.deepEqual([e.prevented, H.calls, DOC.activeElement], [true, ["setActive:tests"], bar.querySelector('.tab[data-id="tests"]')], "and to the last tab before it");
  // Enter: the message box while the transcript shows and the box takes input; the header's press otherwise
  api.set({ activeId: "web" }); head.focus(); H.calls.length = 0;
  e = keyOn(head, "Enter");
  assert.deepEqual([e.prevented, H.calls, CLICKS.length], [true, ["focusComposerOrAsk"], 0], "into the message box: the mirror of the box's Escape, which lands on this header");
  H.composer.disabled = true; H.calls.length = 0;
  e = keyOn(head, "Enter");
  assert.deepEqual([e.prevented, H.calls, CLICKS], [true, ["focusComposerOrAsk"], [head]], "the box disabled (a closed session): Enter presses the header, as Space does");
  H.composer.disabled = false; api.set({ snapView: "infra" }); H.calls.length = 0;
  keyOn(head, "Enter");
  assert.deepEqual([H.calls, CLICKS.length], [[], 2], "the view up: Enter presses (the fold click), the box is not asked");
  keyOn(head, " ");
  assert.equal(CLICKS.length, 3, "Space presses");
  api.set({ snapView: null });
  // any other header (qa's, open, not holding web): the arrows are left to bubble (the window's handler steps the
  // active tab, focus staying put), Enter presses
  const qh = api.makeGroupHead(headOf(H.lastStripItems, "qa").head, false, false, []);
  assert.deepEqual([qh.dataset.act, qh.getAttribute("aria-expanded"), qh.getAttribute("aria-current")], ["toggle-group", "true", null]);
  H.calls.length = 0; CLICKS.length = 0;
  e = keyOn(qh, "ArrowRight");
  assert.deepEqual([e.prevented, H.calls], [false, []], "not the stand-in: the arrows bubble");
  keyOn(qh, "Enter");
  assert.deepEqual([H.calls, CLICKS], [[], [qh]], "Enter presses a plain header");
  // a session under two folded tags: the holder planStrip marked (the first in tag order) is the stand-in; the other
  // holder's header, unmarked (no aria-current, no chip mark), is a plain fold button, so what the user sees marked
  // is what answers as the tab
  const both = setSectionCollapsed(folded, "qa", true);
  const w2 = world("api", both, ["web", "api", "tests", "loose"]);   // qa(folded, marked): api, tests | infra(folded): web, api | loose
  const qa2 = headOf(w2.H.lastStripItems, "qa"), inf2 = headOf(w2.H.lastStripItems, "infra");
  assert.deepEqual([qa2.active, inf2.active], [true, false]);
  const qaHead = realHead(w2.api, w2.bar, qa2), infHead = realHead(w2.api, w2.bar, inf2);
  assert.deepEqual([qaHead.getAttribute("aria-current"), infHead.getAttribute("aria-current")], ["true", null]);
  assert.deepEqual([qaHead.has("holds-active"), infHead.has("holds-active")], [true, false]);
  w2.H.calls.length = 0;
  e = keyOn(qaHead, "ArrowRight");
  assert.deepEqual([e.prevented, w2.H.calls], [true, ["setActive:loose"]], "the marked holder steps: over both folded headers and the trail to the one tab on screen");
  w2.api.set({ activeId: "api" }); w2.H.calls.length = 0;
  e = keyOn(infHead, "ArrowRight");
  assert.deepEqual([e.prevented, w2.H.calls], [false, []], "the unmarked holder does not: its arrows bubble like any other header's");
  keyOn(infHead, "Enter");
  assert.deepEqual([w2.H.calls, CLICKS.at(-1)], [[], infHead], "and its Enter presses the header, not the message box");
});

test("executed: a header click folds or opens the section AND shows it at a glance; the way back is that header's next click once the section is open and holds the tab being read; a click on another header swaps the view", () => {
  const { api, H, bar } = world("web");   // infra open (web, api), web active; qa open (api, tests)
  let plan = planStrip(["web", "api", "tests"], unions, H.groups, "web", false);
  let head = realHead(api, bar, headOf(plan.items, "infra"));
  assert.deepEqual([head.dataset.act, head.getAttribute("aria-expanded"), head.getAttribute("aria-current"), head.has("snap-shown")], ["toggle-group", "true", "true", false],
    "open and holding the tab being read: a fold button like the rest, marked current");
  assert.equal(head.title, headWords("infra", 2, 0, false, true).title, "the title promises the fold and the view");
  H.calls.length = 0;
  api.acts["toggle-group"](head);
  assert.equal(api.get().snapView, "infra", "the section shows in the pane");
  assert.deepEqual([H.writes.length, H.writes[0].collapsed], [1, ["infra"]], "and folds: the one fold write, from the state the header RENDERED");
  assert.deepEqual(H.calls, ["showActive"], "showActive paints the view; the write's event re-renders the strip");
  // the strip re-renders from the store: infra folded, shown, holding the tab being read
  H.groups = H.writes[0]; plan = planStrip(["web", "api", "tests"], unions, H.groups, "web", false); api.set({ lastStripItems: plan.items, collapsedTabIds: plan.folded });
  let inf = headOf(plan.items, "infra");
  assert.deepEqual([inf.folded, inf.active, inf.hidden], [true, true, ["web", "api"]]);
  head = realHead(api, bar, inf);
  assert.deepEqual([head.dataset.act, head.dataset.folded, head.getAttribute("aria-expanded"), head.has("snap-shown"), head.getAttribute("aria-current")], ["toggle-group", "1", "false", true, "true"],
    "folded and shown: the header wears the mark and its next click opens");
  assert.equal(head.title, headWords("infra", 2, 2, true, true, false, true).title, "the title names the open alone: the pane already shows the section");
  H.calls.length = 0;
  api.acts["toggle-group"](head);
  assert.deepEqual([H.writes.length, H.writes[1].collapsed, api.get().snapView, H.calls], [2, [], "infra", ["showActive"]], "opened; the view stays");
  H.groups = H.writes[1]; plan = planStrip(["web", "api", "tests"], unions, H.groups, "web", false); api.set({ lastStripItems: plan.items, collapsedTabIds: plan.folded });
  inf = headOf(plan.items, "infra");
  head = realHead(api, bar, inf);
  assert.deepEqual([head.dataset.act, head.getAttribute("aria-expanded"), head.has("snap-shown"), head.getAttribute("aria-current")], ["show-transcript", null, true, "true"],
    "open, shown and holding the tab being read: the way back, and not a disclosure (its press folds nothing)");
  assert.equal(head.getAttribute("aria-label"), headWords("infra", 2, 0, false, true, true, true).label, "the spoken label names the action");
  assert.equal(head.title, headWords("infra", 2, 0, false, true, true, true).title);
  H.calls.length = 0;
  api.acts["show-transcript"]();
  assert.deepEqual([api.get().snapView, H.calls, H.writes.length], [null, ["renderTabs", "showActive"], 2], "the transcript back; no fold write: the way back moves no fold");
  // the way back is only that header's: qa's header (open, not holding web) while infra shows folds qa and swaps the view to it
  api.set({ snapView: "infra" });
  const qh = realHead(api, bar, headOf(plan.items, "qa"));
  assert.deepEqual([qh.dataset.act, qh.getAttribute("aria-expanded"), qh.has("snap-shown")], ["toggle-group", "true", false]);
  api.acts["toggle-group"](qh);
  assert.deepEqual([api.get().snapView, H.writes[2].collapsed], ["qa", ["qa"]], "the view swaps to the clicked section, which folds");
  // the shown header that does NOT hold the tab being read is a fold button still, marked shown
  api.set({ snapView: "qa" });
  const qh2 = api.makeGroupHead(headOf(plan.items, "qa").head, false, false, []);
  assert.deepEqual([qh2.dataset.act, qh2.has("snap-shown"), qh2.title], ["toggle-group", true, headWords("qa", 2, 0, false, false, false, true).title], "shown, open, the tab being read elsewhere: the click folds");
  const nameless = new FakeEl("div", "tab-group-head");   // no data-group
  H.writes.length = 0; api.set({ snapView: null });
  api.acts["toggle-group"](nameless);
  assert.deepEqual([H.writes.length, api.get().snapView], [0, null], "no group name: nothing");
});

test("executed: the nav trail records the reader's spot while the view shows: the spot the view holds for the tab being read (snapKeep), not the list's scroll that #content carries under the view", () => {
  const { api, H } = world();
  const now = () => H.navDeps!.now();
  const v = { el: new FakeEl("div"), scrollTop: 1200, stick: false };
  H.views.set("web", v);
  H.content.scrollTop = 1200;
  assert.deepEqual(now(), { sid: "web", top: 1200 }, "the transcript showing: the pane's scroll is the reader's spot");
  // the header put the view up: showActive's branch held the reader's spot; the reveal's clamp and the list's own
  // scrolls then move #content (and, through followReader, the active view's saved spot)
  api.set({ snapView: "infra", snapKeep: { v, scrollTop: 1200, stick: false } });
  api.renderSnapshot();
  H.content.scrollTop = 0; v.scrollTop = 0;
  assert.deepEqual(now(), { sid: "web", top: 1200 }, "a pick from the view records the line being read: Ctrl+M walks back to it, not to the list's top");
  // the held spot names another view (the session being read closed under the view and the survivor is active): the
  // pane's scroll, as without the view; showActive's branch re-holds the survivor's spot on its next pass
  H.views.set("api", { el: new FakeEl("div"), scrollTop: 300, stick: true }); api.set({ activeId: "api" }); H.content.scrollTop = 7;
  assert.deepEqual(now(), { sid: "api", top: 7 });
  api.set({ activeId: null });
  assert.equal(now(), null, "no active tab: no spot");
  api.set({ activeId: "web" }); api.hideSnapshot();
  assert.deepEqual([api.get().snapKeep, now()], [null, { sid: "web", top: 7 }], "the view hidden (its spot written back to the view): the pane's scroll again");
});

test("pinned: the wiring the lifted slices cannot reach: showActive's branch, the exits, setActive's pick, the strip's follow", () => {
  // showActive: while a section shows, every transcript is hidden, the reader's place held, the composer disabled with a
  // placeholder that says what to do; the transcript path hides the host first
  assert.match(SHOW, /if \(snapView && renderSnapshot\(\)\) \{\s*\n\s*setSnapMode\(true\);[^\n]*\n\s*for \(const v of views\.values\(\)\) v\.el\.style\.display = "none";/, "the mode switch first (the message box goes, no tab selected), then the views hide");
  assert.match(SHOW, /const av = activeId \? views\.get\(activeId\) : null;\s*\n\s*if \(av && !snapKeep\) snapKeep = \{ v: av, scrollTop: av\.scrollTop, stick: av\.stick \};/, "the reader's place, once per visit");
  assert.match(SHOW, /if \(av && snapKeep && snapKeep\.v !== av\) \{\s*\n\s*snapKeep\.v\.scrollTop = snapKeep\.scrollTop; snapKeep\.v\.stick = snapKeep\.stick;\s*\n\s*snapKeep = \{ v: av, scrollTop: av\.scrollTop, stick: av\.stick \};\s*\n\s*\}/, "the active changed under the view (the session being read closed): the survivor's place is held instead");
  assert.match(SHOW, /if \(ta\) \{ ta\.disabled = true; ta\.placeholder = "Pick a session above to write to it"; \}/);
  assert.match(SHOW, /hideSnapshot\(\);\s*\n\s*const s = activeId \? liveSession\(activeId\) : null;/, "a transcript showing: the view hidden, its place written back");
  const loading = SHOW.slice(SHOW.indexOf("if (activeId && (tabMeta.has(activeId) || hostOf(activeId))) {"), SHOW.indexOf("} else if (!empty) {"));
  assert.match(loading, /if \(ta\) \{ ta\.disabled = false; ta\.placeholder = composerRestingPlaceholder\(\); \}/, "a pick that lands on a still-loading tab takes the box back from the view's disabled state");
  assert.match(RENDER, /if \(snapView\) \{ sl\.replaceChildren\(\); return; \}/, "no session's statusline chip under a section list");
  assert.match(RENDER, /if \(!activeId \|\| skeletonTabs\.ids\.has\(activeId\) \|\| !liveAsks\.has\(activeId\) \|\| snapView\) \{/, "no live ask card under it");
  assert.match(RENDER, /const s = activeId && !snapView \? liveSession\(activeId\) : null;/, "no background-task box under it");
  // setActive: a pick ends the view, opens a folded-away tab's section before the early return, and puts the transcript
  // back even when the pick is the tab already active
  assert.match(RENDER, /const leavingSnap = snapView !== null;\s*\n\s*snapView = null;\s*\n\s*if \(collapsedTabIds\.has\(id\)\) unfoldSectionOf\(id\);[^\n]*\n\s*if \(activeId === id && anchor == null && anchorT == null\) \{[^\n]*\n\s*if \(leavingSnap\) \{ renderTabs\(\); showActive\(\); \}[^\n]*\n\s*return;\s*\n\s*\}/);
  // the strip: the plan the view reads is the one just rendered; every push refreshes the view; the section gone puts the transcript back
  assert.match(TABS, /collapsedTabIds = plan\.folded;\s*\n\s*lastStripItems = plan\.items;/);
  assert.match(TABS, /const shown = snapView;\s*\n\s*const held = shown \? snapshotHoldsFocus\(\) : false;\s*\n\s*if \(snapView\) renderSnapshot\(\);\s*\n\s*if \(shown && !snapView\) \{ showActive\(\); if \(held\) focusActiveTab\(\); \}/);
  assert.equal((TABS.match(/stripAftermath\(visibleIds, ids\)/g) || []).length, 2, "on the skip path and the rebuild path alike");
  // the header and the #tabs delegate's two header acts run above (lifted); what the lift cannot show is that the acts
  // are the ones the strip's delegate installs, and that the no-op act is gone
  const TABS_DELEGATE_AT = RENDER.indexOf("delegate(tabs, {");
  assert.ok(TABS_DELEGATE_AT > 0 && ACTS_AT > TABS_DELEGATE_AT && !RENDER.slice(TABS_DELEGATE_AT + 1, ACTS_AT).includes("delegate("), "the header acts sit on the #tabs delegate");
  assert.ok(!RENDER.includes('"group-active"'), "the no-op act is gone: every header folds");
  // the tab menu's closer marks the Escape it consumed, so the view's Escape yields to it
  assert.match(RENDER, /window\.addEventListener\("keydown", \(e\) => \{ if \(e\.key === "Escape" && ctxMenuEl\) \{ dismissTabMenu\(\); e\.preventDefault\(\); \} \}, true\);/);
  // the window's arrows and the host's next/prev commands step from the header's place too
  assert.match(RENDER, /const nb = collapsedTabIds\.has\(activeId\) \? neighborOfFolded\(lastStripItems, activeId, dir\) : null;\s*\n\s*if \(nb\) \{ e\.preventDefault\(\); setActive\(nb\); \}/, "the window's arrows");
  assert.match(RENDER, /const nb = neighborOfFolded\(lastStripItems, activeId, dir > 0 \? 1 : -1\);\s*\n\s*if \(nb\) setActive\(nb\);/, "cycleTab (nextTab / prevTab)");
  // the client's Ledger type declares the two fields the rows read
  assert.match(RENDER, /^interface Ledger \{ summary: string; tree\?: LedgerTreeNode\[\]; current\?: \{ t\?: number \} \| null; recent\?: LedgerRecent\[\]; workingNote\?: string; needsInput\?: boolean \| null; \}/m);
});

test("pinned: the sheet: the shown header's wash and the stand-in's mark on the tab-group rules; the view's two sizes, tokens only, the tab's state colors on the pip", () => {
  assert.match(CSS, /\.tab-group-head\.snap-shown \{ color: var\(--fg\); background: var\(--tab-active-bg\); box-shadow: inset 0 0 0 1\.5px var\(--chip-bg, transparent\); \}/, "the shown section's row wears the SELECTED TAB's box (T322): its fill token and its inset identity ring");
  assert.doesNotMatch(CSS, /\.tab-group-head\.holds-active \{ cursor: default; \}/, "the header folds, so its cursor promises the click");
  assert.doesNotMatch(CSS, /\.tab-group-head\.holds-active \.tab-group-chip \{ text-decoration: underline/, "the stand-in wears no mark of its own (T322): the tab's highlight says which session is active");
  const block = CSS.slice(CSS.indexOf("#tab-snapshot {"), CSS.indexOf(".snap-when {") + 200);
  assert.ok(block.length > 200, "the sheet was found");
  assert.deepEqual([...new Set(block.match(/font-size: [^;]+/g))], ["font-size: 0.82em"], "one sub-line size, the header's; the rest inherit the body");
  assert.match(block, /\.snap-pip\.working \{ background: var\(--st-working-bg\); \}/);
  assert.match(block, /\.snap-pip\.blocked \{ background: var\(--st-blocked-bg\); \}/);
  assert.match(block, /\.snap-pip\.awaiting \{ background: var\(--st-awaiting-bg\); \}/);
  assert.match(block, /\.snap-pip\.waiting \{ background: var\(--st-awaitbg-bg\); \}/);
  assert.match(block, /\.snap-pip\.retrying \{ background: var\(--st-retrying-bg\); \}/, "the same status token the tab and the folded header's pip use");
  assert.match(block, /\.snap-row:hover \{ border-color: var\(--accent\); background: var\(--accent-wash\); \}/);
  assert.match(block, /\.snap-row:focus-visible \{ outline: 1px solid var\(--accent\); outline-offset: -1px; \}/);
  assert.match(block, /\.snap-flag\.needs \{ border-color: transparent; background: var\(--st-blocked-bg\); color: var\(--st-blocked-fg\); \}/, "needs you in the status red, not the accent");
  const stripped = block.replace(/\/\*[\s\S]*?\*\//g, "").replace(/var\([^)]*\)/g, "V");
  assert.equal(stripped.match(/#[0-9a-fA-F]{3,8}\b/g), null, "no raw color: the light theme needs no override");
});

test("executed: the guide describes the fold rule and the view", () => {
  const GUIDE = fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "guide.md"), "utf8");
  const prose = (t: string) => new RegExp(t.replace(/[.()*]/g, "\\$&").split(" ").join("\\s+"));   // the guide wraps its lines
  assert.match(GUIDE, prose("The section of the tab you are reading folds like any other; its header marks that it holds the tab (the tag's name is underlined), and folded, the header stands in for the tab: focus lands on it, and the left and right arrows step from there."));
  assert.doesNotMatch(GUIDE, prose("never folds (its header says so, and a click there changes nothing)"), "the old rule is gone");
  assert.match(GUIDE, prose("**A section at a glance.** Clicking a header also shows the section in the transcript's place"));
  assert.match(GUIDE, prose("The transcript comes back when you pick a session, press Escape, or click that header again while its section is open and holds the tab you are reading."));
  assert.match(GUIDE, prose("a session that has published a note of what it is working on shows the note as a quieter second line."));
});
