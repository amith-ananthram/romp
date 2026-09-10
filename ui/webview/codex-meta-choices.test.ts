// A CODEX session's statusline menus speak Codex's vocabulary (docs/codex.md): the model/effort
// pickers read the /models payload's codex section, and its mode picker offers Sandboxed and
// Auto without exposing unsupported Claude modes. Source-pin over render.ts, the same style
// as picker-backend.test.ts; the rules for a Codex menu with no list are also lifted from
// render.ts and executed over a small DOM stand-in (below).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const TIMELINE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("the /models payload's codex section populates its own choice arrays (both surfaces)", () => {
  for (const src of [RENDER, TIMELINE]) {
    assert.match(src, /CODEX_MODEL_CHOICES/);
    assert.match(src, /CODEX_EFFORT_CHOICES/);
    assert.match(src, /d\.codex && Array\.isArray\(d\.codex\.models\)/);
    assert.match(src, /d\.codex && Array\.isArray\(d\.codex\.efforts\)/);
  }
});

test("menu construction picks the choice list by the session's backend", () => {
  assert.match(RENDER, /function metaChoices\(kind: MetaKind, st: Status\)/);
  assert.match(RENDER, /st\.backend === "codex"/);
  assert.match(RENDER, /const rows = metaChoices\(kind, s\.status\)\.filter\(/);
  assert.match(RENDER, /for \(const c of rows\) \{/);
  assert.match(TIMELINE, /s\.backend === 'codex'/);
  assert.match(TIMELINE, /\? \(kind === 'model' \? CODEX_MODEL_CHOICES : CODEX_EFFORT_CHOICES\)/);
});

test("Codex offers only its supported modes and opens the mode picker", () => {
  const choices = RENDER.match(/const CODEX_MODE_CHOICES: MetaChoice\[\] = \[([\s\S]*?)\n\];/)![1];
  assert.deepEqual([...choices.matchAll(/value: "([^"]+)"/g)].map(m => m[1]), ["sandboxed", "auto"]);
  assert.match(RENDER, /if \(kind === "mode"\) return CODEX_MODE_CHOICES;/);
  assert.doesNotMatch(RENDER, /if \(kind === "mode" && s\.status\.backend === "codex"\) return;/);
  assert.match(RENDER, /case "sandboxed": return "Sandboxed";/);
});

// A Codex session's model or effort menu opened BLANK when the kernel's codex section had `models: []`
// and no reason (the app-server client not up yet, a failed model list, no live Codex session when the
// tab loaded), with the session's default badge showing above it. The section now carries `error`, and
// a Codex menu with no list shows one non-clickable row naming it, re-reads /models on the open
// itself, and rebuilds when the list lands. Source-pinned here; the loader and the menu are EXECUTED below.
test("a Codex menu with no list says why and re-reads /models instead of opening blank", () => {
  assert.match(RENDER, /let CODEX_MODELS_ERROR = "";/);
  assert.match(RENDER, /if \(d\.codex\) CODEX_MODELS_ERROR = typeof d\.codex\.error === "string" \? d\.codex\.error : "";/);
  assert.match(RENDER, /if \(onModelChoicesLoaded\) onModelChoicesLoaded\(\);/);
  assert.match(RENDER, /if \(!rows\.length && s\.status\.backend === "codex" && \(kind === "model" \|\| kind === "effort"\)\) \{/);
  assert.match(RENDER, /el\("div", "meta-item meta-empty"\)/);
  assert.match(RENDER, /"No model list from Codex" : "No effort list from Codex"/);
  assert.match(RENDER, /sub\.textContent = CODEX_MODELS_ERROR \|\| "asking for the list now";/);
  // the open is the event: one fetch, and the hook rebuilds the SAME open menu when a list arrives
  const block = RENDER.slice(RENDER.indexOf("const empty = el(\"div\", \"meta-item meta-empty\")"), RENDER.indexOf("for (const c of rows) {"));
  assert.match(block, /onModelChoicesLoaded = \(\) => \{/);
  assert.match(block, /if \(metaMenuEl !== menu\) return;/);
  // a chat menu whose tab was dismissed under it closes on the landing (executed below): the chat's badges
  // carry no session, so the re-anchor could not tell its badge from the surviving tab's
  assert.match(block, /if \(!forThread && activeId !== opSid\) \{ closeMetaMenu\(\); return; \}/);
  assert.match(block, /loadModelChoices\(\);/);
  // the rebuild anchors on the badge as it stands NOW, never the one captured at open (executed below)
  assert.match(block, /closeMetaMenu\(\);\n\s+const anchor = metaAnchor\(kind, forSid, btn\);\n\s+if \(anchor\) toggleMetaMenu\(kind, anchor, forSid\);/);
  assert.doesNotMatch(block, /toggleMetaMenu\(kind, btn, forSid\)/, "the captured button is never the rebuild's anchor");
  assert.match(RENDER, /function metaAnchor\(kind: MetaKind, forSid: string \| null \| undefined, btn: HTMLElement\): HTMLElement \| null \{\n\s+if \(btn\.isConnected\) return btn;/);
  assert.match(RENDER, /if \(forSid\) btn\.dataset\.sid = forSid;/, "the popover's badges name their thread so the anchor resolves per session");
  // the wait wears the loader's dots beside its text, which the reason replaces
  assert.match(block, /if \(!CODEX_MODELS_ERROR\) sub\.appendChild\(metaDots\(\)\);/);
  assert.match(block, /if \(!now\.length\) \{ sub\.textContent = CODEX_MODELS_ERROR \|\| \(kind === "model" \? "no model list yet" : "no effort list yet"\); return; \}/);
  assert.doesNotMatch(block, /sent no list/, "the post-read fallback never attributes an answer to the app-server");
  // a FAILED re-read tells the waiting menu (fail loudly): the row would otherwise promise an answer forever
  assert.match(RENDER, /\}\)\.catch\(\(e\) => \{\n(?:[^\n]*\n){0,4}?\s+if \(!onModelChoicesLoaded\) return;\n\s+CODEX_MODELS_ERROR = "could not read the model list: " \+ /);
  // a non-2xx names its status instead of the parse message .json() gives an empty body
  assert.match(RENDER, /fetch\(kernelUrl\("\/models"\), \{ cache: "no-store" \}\)\.then\(\(r\) => \{ if \(!r\.ok\) throw new Error\("HTTP " \+ r\.status\); return r\.json\(\); \}\)/);
  // the hook dies with its menu, so a late response never rebuilds a menu the user closed
  assert.match(RENDER, /metaMenuEl = null;\n  onModelChoicesLoaded = null;/);
  // the row is a statement, not a choice: no pointer, no hover wash; its reason is a sentence, so it wraps
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.meta-item\.meta-empty \{ cursor: default; white-space: normal; max-width: 280px; \}/);
  assert.match(CSS, /\.meta-item\.meta-empty:hover \{ background: none; \}/);
  assert.match(CSS, /\.meta-item-sub \.meta-dots \{ margin-left: 5px;/);
});

// A slice of render.ts, by its anchors: `start` is the first line, `stop` the "\n}\n" that closes the
// function beginning at `fnAt` (or at `start`).
function slice(start: string, fnAt?: string): string {
  const a = RENDER.indexOf(start);
  const f = fnAt ? RENDER.indexOf(fnAt, a) : a;
  const stop = RENDER.indexOf("\n}\n", f) + 3;
  assert.ok(a > 0 && f >= a && stop > f, "anchors not found; render.ts moved " + start.slice(0, 40) + "; re-anchor");
  return RENDER.slice(a, stop);
}
const transpile = (ts: string): string => requireCjs("esbuild").transformSync(ts, { loader: "ts" }).code;

// The loader, lifted from render.ts and transpiled (the models-rev.test.ts idiom): the codex section's
// `error` lands in CODEX_MODELS_ERROR, an absent or non-string one clears it, and the completion hook
// fires once per applied read. The fetch stub answers a 200 by default (`pending`), a rejection
// (`failing`: the kernel unreachable) or a non-2xx with the empty body a kernel refusal carries (`bad`).
type FetchStub = { fetch: () => Promise<any>; pending: Array<(d: any) => void>; failing: Array<(e: any) => void>; bad: Array<(status: number) => void> };
function fetchStub(): FetchStub {
  const pending: Array<(d: any) => void> = [];
  const failing: Array<(e: any) => void> = [];
  const bad: Array<(status: number) => void> = [];
  const fetch = () => new Promise<any>((res, rej) => {
    pending.push((d: any) => res({ ok: true, status: 200, json: async () => d }));
    failing.push(rej);
    bad.push((status: number) => res({ ok: false, status, json: async () => { throw new SyntaxError("Unexpected end of JSON input"); } }));
  });
  return { fetch, pending, failing, bad };
}
function liftLoader() {
  const js = transpile(slice("const MODEL_CHOICES: {", "function loadModelChoices(): void {"));
  const stub = fetchStub();
  const fn = new Function("kernelUrl", "fetch", "adoptCommentDefaults",
    js + "\nreturn { loadModelChoices, CODEX_MODEL_CHOICES, get error() { return CODEX_MODELS_ERROR; }, set hook(f) { onModelChoicesLoaded = f; } };");
  return { api: fn((p: string) => p, stub.fetch, () => {}), pending: stub.pending, failing: stub.failing, bad: stub.bad };
}
const tick = () => new Promise((r) => setImmediate(r));
const REASON = "the Codex app-server client is unavailable: not started yet";

test("executed: the codex section's error reaches the picker and the completion hook fires once per applied read", async () => {
  const { api, pending } = liftLoader();
  let fired = 0;
  api.hook = () => { fired++; };
  api.loadModelChoices();
  pending[0]({ rev: 5, models: [], efforts: [], codex: { models: [], efforts: [], error: REASON } });
  await tick(); await tick();
  assert.equal(api.error, REASON);
  assert.deepEqual(api.CODEX_MODEL_CHOICES, []);
  assert.equal(fired, 1, "the open menu is told the read completed");
  api.loadModelChoices();
  pending[1]({ rev: 6, models: [], efforts: [], codex: { models: [{ value: "gpt-5-test", label: "GPT-5 Test" }], efforts: [], error: null } });
  await tick(); await tick();
  assert.equal(api.error, "", "a held list clears the reason");
  assert.deepEqual(api.CODEX_MODEL_CHOICES, [{ value: "gpt-5-test", label: "GPT-5 Test" }]);
  assert.equal(fired, 2);
  // a response older than one already applied is dropped whole: no list, no reason, no hook
  api.loadModelChoices();
  pending[2]({ rev: 4, models: [], efforts: [], codex: { models: [], efforts: [], error: REASON } });
  await tick(); await tick();
  assert.equal(api.error, "", "a stale response writes no reason");
  assert.deepEqual(api.CODEX_MODEL_CHOICES, [{ value: "gpt-5-test", label: "GPT-5 Test" }]);
  assert.equal(fired, 2, "and does not tell the menu anything landed");
});

// A read that FAILS (the kernel restarting or unreachable when the empty menu re-reads /models) used to
// be swallowed by the loader's catch, so the row kept saying it was asking, forever. Now a waiting menu
// hears the failure through the same hook and its row names it; with no menu waiting the catch stays
// quiet, as before.
test("executed: a failed re-read tells the waiting menu, and stays quiet with no menu waiting", async () => {
  const { api, pending, failing } = liftLoader();
  let fired = 0;
  api.loadModelChoices();                                  // the page-load read, no menu open
  failing[0](new Error("kernel unreachable"));
  await tick(); await tick();
  assert.equal(api.error, "", "no menu waiting: nothing recorded, nothing thrown");
  assert.equal(fired, 0);
  api.hook = () => { fired++; };
  api.loadModelChoices();                                  // the empty menu's own re-read
  failing[1](new Error("kernel unreachable"));
  await tick(); await tick();
  assert.equal(api.error, "could not read the model list: kernel unreachable");
  assert.equal(fired, 1, "the waiting menu hears that the read failed");
  api.loadModelChoices();
  pending[2]({ rev: 7, models: [], efforts: [], codex: { models: [{ value: "gpt-5-test", label: "GPT-5 Test" }], efforts: [], error: null } });
  await tick(); await tick();
  assert.equal(api.error, "", "the next read that lands clears the failure");
  assert.deepEqual(api.CODEX_MODEL_CHOICES, [{ value: "gpt-5-test", label: "GPT-5 Test" }]);
  assert.equal(fired, 2);
});

// A kernel that refuses the read (403 on a token it does not know; a 500) answers with an empty or text
// body, so .json() on it threw a parse message and the row said "Unexpected end of JSON input". The
// status is the fact; the row names it.
test("executed: a non-2xx answer is recorded as its HTTP status, not as a JSON parse message", async () => {
  const { api, bad } = liftLoader();
  let fired = 0;
  api.hook = () => { fired++; };
  api.loadModelChoices();
  bad[0](403);
  await tick(); await tick();
  assert.equal(api.error, "could not read the model list: HTTP 403");
  assert.equal(fired, 1);
});

// ── a DOM stand-in for the menu slice ─────────────────────────────────────────────────────────────
// What toggleMetaMenu, closeMetaMenu, metaButton and metaAnchor touch: elements with a class list, a
// dataset, children, textContent, listeners, a rect, and isConnected (a walk up to the body). A rect read
// on a DETACHED element is the bug under test (a browser answers all zeros there, which puts a fixed menu
// off-screen), so the stand-in records every such read for the assertion instead of guessing at pixels.
class FakeText { constructor(public textContent: string) {} parent: FakeEl | null = null; }
type Kid = FakeEl | FakeText;
const detachedRectReads: FakeEl[] = [];
const rectReads: FakeEl[] = [];   // every rect read, attached or not: a landing that positions nothing reads none
let BODY: FakeEl;
class FakeEl {
  tagName: string; className = ""; id = ""; title = ""; tabIndex = -1; dataset: Record<string, string> = {};
  style: Record<string, string> = {}; children: Kid[] = []; parent: FakeEl | null = null;
  listeners: Record<string, Array<(e: any) => void>> = {}; rect = { left: 0, top: 0, right: 0, bottom: 0 }; html = "";
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
  classes(): string[] { return this.className.split(/\s+/).filter(Boolean); }
  get classList() {
    const self = this;
    return {
      add: (...c: string[]) => { self.className = [...new Set([...self.classes(), ...c])].join(" "); },
      remove: (...c: string[]) => { self.className = self.classes().filter((x) => !c.includes(x)).join(" "); },
      toggle: (c: string, on?: boolean) => { const has = self.classes().includes(c); if (on ?? !has) self.classList.add(c); else self.classList.remove(c); },
      contains: (c: string) => self.classes().includes(c),
    };
  }
  appendChild<T extends Kid>(n: T): T { n.parent?.removeChild(n); n.parent = this; this.children.push(n); return n; }
  append(...ns: Array<Kid | string>): void { for (const n of ns) this.appendChild(typeof n === "string" ? new FakeText(n) : n); }
  removeChild(n: Kid): void { const i = this.children.indexOf(n); if (i >= 0) { this.children.splice(i, 1); n.parent = null; } }
  remove(): void { this.parent?.removeChild(this); }
  replaceChildren(...ns: Kid[]): void { for (const c of [...this.children]) this.removeChild(c); this.append(...ns); }
  get firstElementChild(): FakeEl | null { return (this.children.find((c) => c instanceof FakeEl) as FakeEl | undefined) ?? null; }
  get textContent(): string { return this.children.map((c) => c.textContent).join(""); }
  set textContent(v: string) { this.replaceChildren(); if (v) this.appendChild(new FakeText(v)); }
  get innerHTML(): string { return this.html; }
  set innerHTML(v: string) { this.html = v; this.replaceChildren(); }
  get isConnected(): boolean { let n: FakeEl | null = this; while (n) { if (n === BODY) return true; n = n.parent; } return false; }
  addEventListener(t: string, fn: (e: any) => void): void { (this.listeners[t] ||= []).push(fn); }
  focus(): void {}
  setAttribute(k: string, v: string): void { if (k === "class") this.className = v; }
  get offsetWidth(): number { return 0; }
  getBoundingClientRect() {
    rectReads.push(this);
    if (!this.isConnected) detachedRectReads.push(this);
    const r = this.isConnected ? this.rect : { left: 0, top: 0, right: 0, bottom: 0 };
    return { ...r, width: r.right - r.left, height: r.bottom - r.top };
  }
  descendants(): FakeEl[] { const out: FakeEl[] = []; for (const c of this.children) if (c instanceof FakeEl) { out.push(c, ...c.descendants()); } return out; }
  matches(sel: string): boolean {   // ".a.b", "[tabindex]", "[data-x]" and "tag.a": what the lifted slices ask for
    return sel.split(/(?=[.\[])/).every((part) => {
      if (part.startsWith(".")) return this.classes().includes(part.slice(1));
      if (part.startsWith("[")) {
        const attr = part.slice(1, -1);
        if (attr === "tabindex") return this.tabIndex >= 0;
        return attr.startsWith("data-") && attr.slice(5).replace(/-([a-z])/g, (_, c: string) => c.toUpperCase()) in this.dataset;
      }
      return this.tagName === part.toUpperCase();
    });
  }
  querySelectorAll(sel: string): FakeEl[] { return this.descendants().filter((d) => d.matches(sel)); }
  querySelector(sel: string): FakeEl | null { return this.querySelectorAll(sel)[0] ?? null; }
}

// The menu's world: the loader, `el`, `metaDots`, `metaButton`, `metaAnchor`, `closeMetaMenu` and
// `toggleMetaMenu` lifted from render.ts, over the stand-in and stubs for what they read of the rest of
// the module (the session map, the thread helpers, the pick memory, the vscode bridge, the tints).
// `thread`: an open comment thread's popover, as openCommentThread and threadMetaStatus report it; absent,
// no popover is open and a thread status read throws, as in render.ts. `liveSession` is passed for a
// status lookup that may read the active session through it rather than the map directly.
function liftMenu(opts: { thread?: { th: unknown; status: any } } = {}) {
  BODY = new FakeEl("body");
  detachedRectReads.length = 0;
  rectReads.length = 0;
  const doc = {
    body: BODY,
    createElement: (t: string) => new FakeEl(t),
    createTextNode: (s: string) => new FakeText(s),
    querySelectorAll: (sel: string) => BODY.querySelectorAll(sel),
    querySelector: (sel: string) => BODY.querySelector(sel),
    getElementById: (id: string) => BODY.descendants().find((d) => d.id === id) ?? null,
  };
  const win = { innerWidth: 1000, innerHeight: 800 };
  const js = transpile([
    "let activeId = null;",
    // metaChoices, as render.ts routes a Codex session: its own lists (the SDK lists are not lifted)
    "const metaChoices = (kind, st) => st.backend === 'codex' ? (kind === 'model' ? CODEX_MODEL_CHOICES : kind === 'effort' ? CODEX_EFFORT_CHOICES : []) : [];",
    slice("function el(tag: string, cls?: string): HTMLElement {"),
    slice("function metaDots(): HTMLElement {"),
    slice("const MODEL_CHOICES: {", "function loadModelChoices(): void {"),
    slice("function metaButton(kind: MetaKind, text: string, forSid?: string | null): HTMLElement {"),
    slice("let metaMenuEl: HTMLElement | null = null;", "function toggleMetaMenu(kind: MetaKind, btn: HTMLElement, forSid?: string | null) {"),
    "return { metaButton, toggleMetaMenu, closeMetaMenu, loadModelChoices, CODEX_MODEL_CHOICES,",
    "  get menu() { return metaMenuEl; }, set active(id) { activeId = id; }, get error() { return CODEX_MODELS_ERROR; } };",
  ].join("\n"));
  const stub = fetchStub();
  const sessions = new Map<string, any>();
  const fn = new Function("document", "window", "kernelUrl", "fetch", "adoptCommentDefaults", "sessions",
    "openCommentThread", "threadMetaStatus", "metaCurrent", "metaPending", "vscodeApi", "isCurrentMeta",
    "modeIconSvg", "riskyMode", "nonClassicChoiceTone", "setTip", "liveSession", js);
  const api = fn(doc, win, (p: string) => p, stub.fetch, () => {}, sessions,
    () => (opts.thread ? { th: opts.thread.th } : null),
    () => { if (!opts.thread) throw new Error("no thread here"); return opts.thread.status; },
    () => "", new Map(), null, () => false, () => "", () => false, () => undefined, () => {},
    (id: string) => sessions.get(id));
  return { api, sessions, body: BODY, win, pending: stub.pending, failing: stub.failing, rectReads };
}
const SID = "11111111-2222-4333-8444-555555555555";
const CODEX_READY = { state: "ready", sinceEpoch: null, backend: "codex", model: "gpt-5-test", effort: "medium" };
const LIST = { rev: 3, models: [], efforts: [], codex: { models: [{ value: "gpt-5-test", label: "GPT-5 Test" }], efforts: [], error: null } };
// a statusline holding a mode, a model and an effort badge, in the order syncMetaControls appends them,
// placed where a real one sits (bottom-right of a 1000x800 pane); `right` and `top` are the MODEL badge's,
// and each badge has its own rect, so a rebuild that anchored on a badge of another kind is positioned
// from the wrong rect
function statusline(api: any, right: number, top: number) {
  const sl = new FakeEl("div"); sl.id = "statusline";
  const meta = new FakeEl("span"); meta.className = "spinner-meta"; meta.id = "spinner-meta";
  const modeBtn = api.metaButton("mode", "Sandboxed") as FakeEl;
  modeBtn.rect = { left: right - 150, top, right: right - 70, bottom: top + 16 };
  const btn = api.metaButton("model", "gpt-5-test") as FakeEl;
  btn.rect = { left: right - 60, top, right, bottom: top + 16 };
  const effortBtn = api.metaButton("effort", "medium") as FakeEl;
  effortBtn.rect = { left: right + 10, top, right: right + 70, bottom: top + 16 };
  meta.append(modeBtn, btn, effortBtn); sl.appendChild(meta);
  return { sl, btn, effortBtn };
}
const rows = (menu: FakeEl) => menu.querySelectorAll(".meta-item").filter((r) => !r.classes().includes("meta-empty")).map((r) => r.textContent);

// The row's first job: the kernel's sentence, shown as it is. A reason held before the open (the page-load
// read landed with an empty list and a reason) is shown at once, with no dots; the open re-reads all the
// same, since a held reason may be stale. A reason landing on an open wait row replaces its text in place:
// the same menu, no rebuild, no rect read. A read with neither list nor reason leaves the fallback that
// names the kind.
test("executed: the row shows the kernel's reason, held at open or landing on the open menu, in place", async () => {
  // a reason held before the open
  {
    const { api, sessions, body, pending } = liftMenu();
    sessions.set(SID, { status: CODEX_READY }); api.active = SID;
    api.loadModelChoices();                                // the page-load read, no menu open
    pending[0]({ rev: 3, models: [], efforts: [], codex: { models: [], efforts: [], error: REASON } });
    await tick(); await tick();
    const sl = statusline(api, 700, 760);
    body.appendChild(sl.sl);
    api.toggleMetaMenu("model", sl.btn, null);
    const row = (api.menu as FakeEl).querySelector(".meta-empty")!;
    assert.equal(row.firstElementChild!.textContent, "No model list from Codex");
    const sub = row.querySelector(".meta-item-sub")!;
    assert.equal(sub.textContent, REASON, "the kernel's sentence, verbatim");
    assert.equal(sub.querySelector(".meta-dots"), null, "a reason is shown, not waited for");
    assert.equal(pending.length, 2, "the open re-reads all the same: a held reason may be stale");
  }
  // a reason landing on an open wait row, on the effort menu (its head names the effort list)
  {
    const { api, sessions, body, pending, rectReads } = liftMenu();
    sessions.set(SID, { status: CODEX_READY }); api.active = SID;
    const sl = statusline(api, 700, 760);
    body.appendChild(sl.sl);
    api.toggleMetaMenu("effort", sl.effortBtn, null);
    const waiting = api.menu as FakeEl;
    const row = waiting.querySelector(".meta-empty")!;
    assert.equal(row.firstElementChild!.textContent, "No effort list from Codex");
    const sub = row.querySelector(".meta-item-sub")!;
    assert.equal(sub.textContent, "asking for the list now");
    assert.ok(sub.querySelector(".meta-dots"));
    const reads = rectReads.length;
    pending[0]({ rev: 3, models: [], efforts: [], codex: { models: [], efforts: [], error: REASON } });
    await tick(); await tick(); await tick();
    assert.equal(api.menu, waiting, "the same menu: the reason is written in place, not rebuilt");
    assert.equal(sub.textContent, REASON);
    assert.equal(sub.querySelector(".meta-dots"), null, "the dots went with the wait");
    assert.equal(rectReads.length, reads, "nothing was positioned");
    // a later read (the kernel's models frame) with neither list nor reason: the fallback names the kind
    api.loadModelChoices();
    pending[1]({ rev: 4, models: [], efforts: [], codex: { models: [], efforts: [], error: null } });
    await tick(); await tick(); await tick();
    assert.equal(api.menu, waiting);
    assert.equal(sub.textContent, "no effort list yet");
    assert.deepEqual(detachedRectReads, []);
  }
});

// The row is a Codex menu's: an SDK session's menu with no rows (the stub's SDK lists are empty) opens on
// nothing and asks for nothing, as before.
test("executed: an SDK session's menu with no rows takes no reason row and issues no fetch", () => {
  const { api, sessions, body, pending } = liftMenu();
  sessions.set(SID, { status: { ...CODEX_READY, backend: "sdk" } }); api.active = SID;
  const sl = statusline(api, 700, 760);
  body.appendChild(sl.sl);
  api.toggleMetaMenu("model", sl.btn, null);
  const menu = api.menu as FakeEl;
  assert.ok(menu, "the menu opens");
  assert.equal(menu.querySelector(".meta-empty"), null, "no reason row");
  assert.equal(menu.querySelectorAll(".meta-item").length, 0);
  assert.equal(pending.length, 0, "and no re-read");
});

// updateStatusline() rebuilds the statusline on every kernel push, and the spawn that makes the list
// readable also pushes, so by the time the re-read lands the button captured at open is often detached: a
// rebuild from it read a rect of all zeros and the menu sat beyond the pane's left edge and above its top.
// The hook re-resolves the badge for the same kind and session instead.
test("executed: the list landing rebuilds the menu against the badge the statusline holds NOW, never a detached one", async () => {
  const { api, sessions, body, win, pending } = liftMenu();
  sessions.set(SID, { status: CODEX_READY }); api.active = SID;
  const first = statusline(api, 700, 760);
  body.appendChild(first.sl);
  api.toggleMetaMenu("model", first.btn, null);
  const waiting = api.menu as FakeEl;
  const row = waiting && waiting.querySelector(".meta-empty");
  assert.ok(row, "an empty Codex list opens the reason row");
  assert.equal(pending.length, 1, "the open re-reads /models");
  // the row is a statement, not a choice: it takes no focus and answers no click or key
  assert.equal(row!.tabIndex, -1);
  assert.deepEqual(Object.keys(row!.listeners), []);
  const sub = row!.querySelector(".meta-item-sub")!;
  assert.equal(sub.textContent, "asking for the list now");
  assert.ok(sub.querySelector(".meta-dots"), "the wait wears the loader's dots");
  assert.equal(waiting.style.right, (win.innerWidth - 700) + "px");
  assert.equal(waiting.style.bottom, (win.innerHeight - 760 + 6) + "px");
  // the kernel pushes: the statusline is rebuilt and the captured button is gone from the document
  first.sl.remove();
  const second = statusline(api, 720, 770);
  body.appendChild(second.sl);
  assert.equal(first.btn.isConnected, false);
  pending[0](LIST);
  await tick(); await tick(); await tick();
  const rebuilt = api.menu as FakeEl;
  assert.ok(rebuilt && rebuilt !== waiting, "the menu was rebuilt");
  assert.deepEqual(rows(rebuilt), ["GPT-5 Test"], "with the list that landed, and no reason row");
  assert.equal(rebuilt.querySelector(".meta-empty"), null);
  assert.equal(rebuilt.style.right, (win.innerWidth - 720) + "px", "anchored to the badge the rebuild put in place");
  assert.equal(rebuilt.style.bottom, (win.innerHeight - 770 + 6) + "px");
  assert.equal(body.querySelectorAll(".meta-menu").length, 1, "the waiting menu is gone; one menu on the page");
  assert.deepEqual(detachedRectReads, [], "no menu was ever positioned from a detached element's rect");
});

test("executed: with no badge to anchor to, the landing list leaves the menu closed and the next open shows it", async () => {
  const { api, sessions, body, pending, rectReads } = liftMenu();
  sessions.set(SID, { status: CODEX_READY }); api.active = SID;
  const first = statusline(api, 700, 760);
  body.appendChild(first.sl);
  api.toggleMetaMenu("model", first.btn, null);
  assert.ok(api.menu, "open on the reason row");
  first.sl.remove();                                       // the statusline emptied (a section view, a placeholder tab)
  const reads = rectReads.length;
  pending[0](LIST);
  await tick(); await tick(); await tick();
  assert.equal(api.menu, null, "no live badge for the kind and session: the menu stays closed");
  assert.equal(body.querySelectorAll(".meta-menu").length, 0);
  assert.equal(rectReads.length, reads, "nothing was positioned: no rect read from any element");
  assert.deepEqual(detachedRectReads, []);
  const next = statusline(api, 700, 760);
  body.appendChild(next.sl);
  api.toggleMetaMenu("model", next.btn, null);
  assert.deepEqual(rows(api.menu as FakeEl), ["GPT-5 Test"], "the next open reads the list that landed");
  assert.equal(pending.length, 1, "and asks for nothing more: the list is held");
});

test("executed: a badge still in the document stays the anchor (the sequential case is unchanged)", async () => {
  const { api, sessions, body, win, pending } = liftMenu();
  sessions.set(SID, { status: CODEX_READY }); api.active = SID;
  const only = statusline(api, 700, 760);
  body.appendChild(only.sl);
  api.toggleMetaMenu("model", only.btn, null);
  pending[0](LIST);
  await tick(); await tick(); await tick();
  const rebuilt = api.menu as FakeEl;
  assert.deepEqual(rows(rebuilt), ["GPT-5 Test"]);
  assert.equal(rebuilt.style.right, (win.innerWidth - 700) + "px");
  assert.equal(rebuilt.style.bottom, (win.innerHeight - 760 + 6) + "px");
  assert.deepEqual(detachedRectReads, []);
});

// The popover's badges name their thread (data-sid), so a rebuild there resolves the THREAD's badge and
// never the chat's badge of the same kind; the chat's badges name no session and resolve among themselves.
test("executed: the anchor resolves per session: the chat's badge never stands in for a thread's, nor the reverse", async () => {
  const { api, sessions, body, win, pending } = liftMenu();
  sessions.set(SID, { status: CODEX_READY }); api.active = SID;
  const chat = statusline(api, 700, 760);
  body.appendChild(chat.sl);
  const TID = "22222222-3333-4444-8555-666666666666";
  const threadBtn = api.metaButton("model", "gpt-5-test", TID) as FakeEl;
  assert.equal(threadBtn.dataset.sid, TID, "a popover badge carries its thread");
  assert.equal(chat.btn.dataset.sid, undefined, "a chat badge carries no session");
  api.toggleMetaMenu("model", chat.btn, null);
  chat.sl.remove();
  const pop = new FakeEl("div"); pop.className = "cmt-pop";
  threadBtn.rect = { left: 340, top: 500, right: 400, bottom: 516 };
  pop.appendChild(threadBtn); body.appendChild(pop);        // only a THREAD's model badge is on the page now
  pending[0](LIST);
  await tick(); await tick(); await tick();
  assert.equal(api.menu, null, "the chat's menu does not re-anchor on the thread's badge");
  assert.deepEqual(detachedRectReads, []);
  // and a fresh chat badge is found even with the thread's badge present
  const chat2 = statusline(api, 720, 770);
  body.appendChild(chat2.sl);
  api.CODEX_MODEL_CHOICES.length = 0;                      // an empty list again, so the open waits on a read
  api.toggleMetaMenu("model", chat2.btn, null);
  chat2.sl.remove();
  const chat3 = statusline(api, 730, 775);
  body.appendChild(chat3.sl);
  pending[1](LIST);
  await tick(); await tick(); await tick();
  assert.equal((api.menu as FakeEl).style.right, (win.innerWidth - 730) + "px", "the chat's own replacement badge, not the thread's");
  assert.deepEqual(detachedRectReads, []);
});

// The chat's badges carry no session, so the hook's re-anchor cannot tell the dismissed tab's badge from the
// surviving tab's: dismissSession moves activeId to the most recent survivor without setActive's
// closeMetaMenu (a kernel omission, an end, a host drop; only the in-page tab close reaches the document
// closer), and the landing then found a badge of the same kind on the survivor's tab, so a menu the user
// never opened there opened with the survivor's list, or the stale reason row was written over the
// survivor's tab. A chat menu whose session is no longer the active one closes on the landing instead:
// no re-anchor, no reason row, no rect read.
test("executed: the landing closes a chat menu whose tab was dismissed under it, with no reason row and no rect read", async () => {
  const { api, sessions, body, pending, rectReads } = liftMenu();
  const SURVIVOR = "33333333-4444-4555-8666-777777777777";
  const LAST = "44444444-5555-4666-8777-888888888888";
  sessions.set(SID, { status: CODEX_READY }); api.active = SID;
  const gone = statusline(api, 700, 760);
  body.appendChild(gone.sl);
  api.toggleMetaMenu("model", gone.btn, null);
  const reason = (api.menu as FakeEl).querySelector(".meta-item-sub")!;
  assert.equal(reason.textContent, "asking for the list now");
  assert.equal(pending.length, 1);
  // the kernel's push omits SID: dismissSession drops it, activeId moves to the survivor, the statusline repaints
  sessions.delete(SID); sessions.set(SURVIVOR, { status: CODEX_READY }); api.active = SURVIVOR;
  gone.sl.remove();
  const kept = statusline(api, 720, 770);
  body.appendChild(kept.sl);
  let reads = rectReads.length;
  pending[0]({ rev: 3, models: [], efforts: [], codex: { models: [], efforts: [], error: REASON } });
  await tick(); await tick(); await tick();
  assert.equal(api.menu, null, "the menu belonged to the dismissed tab: closed");
  assert.equal(body.querySelectorAll(".meta-menu").length, 0);
  assert.equal(reason.textContent, "asking for the list now", "the reason row was not written after the tab went");
  assert.equal(rectReads.length, reads, "nothing was positioned: no rect read from any element");
  // the survivor's own open is the user's gesture; the same dismissal under it, and this time a list lands
  api.toggleMetaMenu("model", kept.btn, null);
  assert.ok((api.menu as FakeEl).querySelector(".meta-empty"), "the survivor's menu waits on its own read");
  assert.equal(pending.length, 2);
  sessions.delete(SURVIVOR); sessions.set(LAST, { status: CODEX_READY }); api.active = LAST;
  kept.sl.remove();
  const last = statusline(api, 730, 775);
  body.appendChild(last.sl);
  reads = rectReads.length;
  pending[1](LIST);
  await tick(); await tick(); await tick();
  assert.equal(api.menu, null, "the list landed for a dismissed tab's menu: closed, never re-anchored on the survivor's badge");
  assert.equal(body.querySelectorAll(".meta-menu").length, 0);
  assert.equal(rectReads.length, reads, "no rect read from any element");
  assert.deepEqual(detachedRectReads, []);
  // and the survivor's badge opens the landed list on the user's own click, with no further fetch
  api.toggleMetaMenu("model", last.btn, null);
  assert.deepEqual(rows(api.menu as FakeEl), ["GPT-5 Test"]);
  assert.equal(pending.length, 2);
});

// A comment thread's popover menu names its sid (forSid, never the active tab), so the guard above does not
// touch it: the active tab may change under it and the landing still rebuilds against the thread's badge.
// A regression pin for a Codex thread: threadMetaStatus reports backend "sdk" for every thread today, so no
// thread menu is a Codex menu yet, and the stub hands the popover a status the real function cannot return.
test("executed: a thread popover's waiting menu still rebuilds on the landing whatever tab is active", async () => {
  const TID = "22222222-3333-4444-8555-666666666666";
  const OTHER = "33333333-4444-4555-8666-777777777777";
  const { api, sessions, body, win, pending } = liftMenu({ thread: { th: { tid: TID }, status: CODEX_READY } });
  sessions.set(SID, { status: CODEX_READY }); api.active = SID;
  const chat = statusline(api, 700, 760);
  body.appendChild(chat.sl);
  const pop = new FakeEl("div"); pop.className = "cmt-pop";
  const threadBtn = api.metaButton("model", "gpt-5-test", TID) as FakeEl;
  threadBtn.rect = { left: 340, top: 500, right: 400, bottom: 516 };
  pop.appendChild(threadBtn); body.appendChild(pop);
  api.toggleMetaMenu("model", threadBtn, TID);
  assert.ok((api.menu as FakeEl).querySelector(".meta-empty"), "the thread's Codex menu waits on the read");
  assert.equal(pending.length, 1);
  api.active = OTHER;                                     // the user switched tabs; the popover is still open
  threadBtn.remove();                                     // and the popover's statusline was refreshed under it
  const threadBtn2 = api.metaButton("model", "gpt-5-test", TID) as FakeEl;
  threadBtn2.rect = { left: 350, top: 510, right: 410, bottom: 526 };
  pop.appendChild(threadBtn2);
  pending[0](LIST);
  await tick(); await tick(); await tick();
  const rebuilt = api.menu as FakeEl;
  assert.ok(rebuilt, "the thread's menu was rebuilt");
  assert.deepEqual(rows(rebuilt), ["GPT-5 Test"]);
  assert.equal(rebuilt.style.right, (win.innerWidth - 410) + "px", "anchored to the thread's replacement badge");
  assert.equal(rebuilt.style.bottom, (win.innerHeight - 510 + 6) + "px");
  assert.equal(body.querySelectorAll(".meta-menu").length, 1);
  assert.deepEqual(detachedRectReads, []);
});

// The hook belongs to the menu that set it and goes with it: a menu the user closed before the read lands
// (Escape, a click elsewhere) must stay closed when it does, and a failed read after the close must not be
// recorded as if a menu were still waiting on it.
test("executed: a menu closed before the read lands stays closed, and a failure after the close is not recorded", async () => {
  const { api, sessions, body, pending, failing, rectReads } = liftMenu();
  sessions.set(SID, { status: CODEX_READY }); api.active = SID;
  const sl = statusline(api, 700, 760);
  body.appendChild(sl.sl);
  api.toggleMetaMenu("model", sl.btn, null);
  assert.ok((api.menu as FakeEl).querySelector(".meta-empty"), "open on the reason row");
  assert.equal(pending.length, 1);
  api.closeMetaMenu();                                    // the user closed it before the read landed
  assert.equal(api.menu, null);
  const reads = rectReads.length;
  failing[0](new Error("kernel unreachable"));
  await tick(); await tick(); await tick();
  assert.equal(api.menu, null, "no menu reopened on the landing");
  assert.equal(body.querySelectorAll(".meta-menu").length, 0);
  assert.equal(rectReads.length, reads, "nothing was positioned");
  assert.equal(api.error, "", "no menu was waiting, so the failure was not recorded");
  // the badge is still in the document, so a list landing now must not reopen the menu either
  api.toggleMetaMenu("model", sl.btn, null);                // the user's own open positions a menu: one rect read
  assert.equal(pending.length, 2);
  api.closeMetaMenu();
  const reads2 = rectReads.length;
  pending[1](LIST);
  await tick(); await tick(); await tick();
  assert.equal(api.menu, null, "the list landed for a menu the user had closed: it stays closed");
  assert.equal(body.querySelectorAll(".meta-menu").length, 0);
  assert.equal(rectReads.length, reads2, "nothing was positioned on the landing");
  assert.deepEqual(api.CODEX_MODEL_CHOICES, [{ value: "gpt-5-test", label: "GPT-5 Test" }], "the list itself is held for the next open");
});
