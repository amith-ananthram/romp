// The viewer's paint bracket, run FOR REAL: openFileView and openUrlView mount against the tree-aware DOM
// stand-in file-view-notice.test.ts uses, a fake fetch lands the document, and the page's performance collector
// (perf-telemetry.ts, on a fake clock through PerfDeps) is read back for what the paint recorded. Every paint of
// the text body, the first when the bytes land, one more per Rendered/Raw toggle and one when the editor closes
// (Save, Cancel and Escape leave through the same exit), is one `fileview:paint` frame of the collector the
// page publishes as window.__rompPerf, so the cost of painting a large document shows in the hosting pane's
// minute row and in `romp perf client`. The loader, the editor's surfaces, a picture, a PDF and the SVG Source
// view are not paints of that kind and record nothing; a page with no collector, or one of another shape, paints
// exactly as before. The stand-in has no DOM the sanitizer can run on, so the rendered view takes mdBlock's
// bare-text fallback here; the bracket is what is under test, not the render.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { createPerfTelemetry, type PerfDeps } from "./perf-telemetry";

// ── a DOM stand-in with a tree: parentNode, insertBefore, remove, getElementById over it, and the query
// methods mdBlock's post-passes call (nothing to find: the fallback text carries no headings or fences) ──
class El {
  id = ""; title = ""; hidden = false; type = ""; disabled = false; tabIndex = -1; innerHTML = "";
  href = ""; target = ""; rel = ""; spellcheck = true; value = "";
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};
  parentNode: El | null = null;
  childNodes: Array<El | string> = [];
  replaceCalls = 0;                                  // swaps of this node's children: one per paint of the body
  private attrs = new Map<string, string>();
  private classes = new Set<string>();
  private listeners = new Map<string, Array<(ev: any) => void>>();
  classList = {
    add: (...c: string[]) => { for (const x of c) this.classes.add(x); },
    remove: (...c: string[]) => { for (const x of c) this.classes.delete(x); },
    toggle: (c: string, on?: boolean) => { if (on ?? !this.classes.has(c)) this.classes.add(c); else this.classes.delete(c); },
    contains: (c: string) => this.classes.has(c),
  };
  constructor(public tagName: string) {}
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  get textContent(): string { return this.childNodes.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set textContent(v: string) { this.replaceChildren(...(v === "" ? [] : [v])); }
  get children(): El[] { return this.childNodes.filter((c): c is El => c instanceof El); }
  get isConnected(): boolean { let n: El = this; while (n.parentNode) n = n.parentNode; return n === docBody; }
  private adopt(c: El | string): void { if (c instanceof El) { c.remove(); c.parentNode = this; } }
  appendChild<T extends El>(c: T): T { this.adopt(c); this.childNodes.push(c); return c; }
  prepend(...cs: Array<El | string>): void { for (const c of cs) this.adopt(c); this.childNodes.unshift(...cs); }
  insertBefore<T extends El>(c: T, ref: El | null): T {
    if (ref && !this.childNodes.includes(ref)) throw new Error("insertBefore: the reference node is not a child of this node");
    this.adopt(c);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    return c;
  }
  replaceChildren(...cs: Array<El | string>): void {
    this.replaceCalls++;
    for (const c of this.childNodes) if (c instanceof El) c.parentNode = null;
    this.childNodes = [];
    for (const c of cs) this.adopt(c);
    this.childNodes = [...cs];
  }
  remove(): void {
    const p = this.parentNode;
    if (!p) return;
    const i = p.childNodes.indexOf(this);
    if (i >= 0) p.childNodes.splice(i, 1);
    this.parentNode = null;
  }
  contains(n: El): boolean { let x: El | null = n; while (x) { if (x === this) return true; x = x.parentNode; } return false; }
  querySelectorAll(): El[] { return []; }
  querySelector(): El | null { return null; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  addEventListener(type: string, fn: (ev: any) => void): void {
    const l = this.listeners.get(type) || [];
    l.push(fn); this.listeners.set(type, l);
  }
  removeEventListener(type: string, fn: (ev: any) => void): void {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((f) => f !== fn));
  }
  focus(): void {}
  dispatch(type: string, ev: Record<string, unknown> = {}): void {
    for (const fn of [...(this.listeners.get(type) || [])]) fn({ type, target: this, preventDefault() {}, ...ev });
  }
  click(): void { this.dispatch("click"); }
}
const docBody = new El("body");
function findById(n: El, id: string): El | null {
  if (n.id === id) return n;
  for (const c of n.childNodes) if (c instanceof El) { const hit = findById(c, id); if (hit) return hit; }
  return null;
}
const win: any = new EventTarget();
win.parent = win;
(globalThis as any).window = win;
(globalThis as any).document = {
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => s,
  getElementById: (id: string) => findById(docBody, id),
  querySelectorAll: () => [],                   // no bundle <script src>: the editor chunk cannot load, so Edit takes the plain textarea
  addEventListener: () => {},
  removeEventListener: () => {},
  body: docBody,
};
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, String(v)); },
  removeItem: (k: string) => { store.delete(k); },
};

// ── fixtures: a notes-api world, synthetic throughout ──
const ROOT = "/tmp/notes-api";
const README = ROOT + "/docs/README.md";
const FIG = ROOT + "/docs/fig.svg";
const PAPER = ROOT + "/docs/paper.pdf";
const TEXT = "# Notes API\n\nThe service and its tests.\n";
const SVG_XML = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>';
const HREF = "http://notes-api.test/reports/run-1/evidence.md";
const URL_DOC = "# Evidence\n\nThe web session's report.\n";
type Served = { bytes: string | Uint8Array; type: string };
const disk: Record<string, Served> = {
  [README]: { bytes: TEXT, type: "text/plain; charset=utf-8" },
  [FIG]: { bytes: SVG_XML, type: "image/svg+xml" },
  [PAPER]: { bytes: new Uint8Array([0x25, 0x50, 0x44, 0x46]), type: "application/pdf" },
};
// The fetches the flows make: the kernel's /file (its Content-Type is the verdict: text, an SVG, a PDF; a text file
// carries the faithful-UTF-8 and ns-mtime headers, so Edit arms), its /version (editing already allowed, so no
// consent popup), and a same-origin document for the URL viewer, answered as a real Response so the streamed,
// capped read runs as it does in a browser.
(globalThis as any).fetch = (url: string) => {
  if (url.startsWith("/version")) return Promise.resolve({ json: () => Promise.resolve({ fileEditing: true }) });
  if (/^https?:/.test(url)) return Promise.resolve(new Response(URL_DOC, { status: 200, headers: { "Content-Type": "text/markdown; charset=utf-8" } }));
  const p = decodeURIComponent((/[?&]path=([^&]*)/.exec(url) || [])[1] || "");
  const f = disk[p];
  assert.ok(f, "a fetch for a fixture file: " + url);
  const headers = new Map([["Content-Type", f.type], ["X-Romp-Text-Utf8", "1"], ["X-Romp-Mtime-Ns", "1700000000000000000"]]);
  return Promise.resolve({
    ok: true, status: 200, headers: { get: (k: string) => headers.get(k) ?? null },
    text: () => Promise.resolve(String(f.bytes)),
    blob: () => Promise.resolve(new Blob([f.bytes as unknown as BlobPart], { type: f.type })),
  });
};
/** Let every pending promise chain run: the fetch settles, a blob decodes, the editor chunk's rejection reaches its catch. */
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

let bound: Promise<typeof import("./file-view")> | null = null;
function view(): Promise<typeof import("./file-view")> {
  if (!bound) bound = import("./file-view").then((fv) => { fv.initFileView(() => {}); return fv; });
  return bound;
}

/** A collector on a fake clock, as perf-telemetry.test.ts builds one, posting into `posted`. */
function collector(app: string, posted: any[]) {
  const clock = { t: 1000, wall: 1_700_000_000_000 };
  const deps: PerfDeps = {
    now: () => clock.t,
    wallNow: () => clock.wall,
    post: (m) => posted.push(m),
    raf: null, caf: null, setInterval: null, observer: null, supportedEntryTypes: [],
    heapBytes: () => null, domCount: () => null,
    visible: () => true, hiddenPane: () => false,
    ua: "chrome-desktop", pageUrl: "http://h:1/" + app,
    windowEvents: null, documentEvents: null,
  };
  return { perf: createPerfTelemetry(app, deps), clock };
}

type Card = { body: El; btn: (label: string) => El };
/** The card up now: its body and its title-bar buttons by label. */
function card(): Card {
  const wrap = docBody.children[docBody.children.length - 1];
  assert.equal(wrap.id, "romp-fileview");
  const root = wrap.children[0];
  const bar = root.children[0];
  const body = root.children[root.children.length - 1];
  assert.equal(body.className, "fileview-body");
  const acts = bar.children.find((c) => c.classList.contains("fileview-acts"))!;
  const btn = (label: string) => { const b = acts.children.find((c) => c.tagName === "button" && c.textContent === label); assert.ok(b, "the " + label + " button"); return b!; };
  return { body, btn };
}
/** Open the file and let the bytes land. Every open starts from the stored default: Rendered. */
async function openFile(p: string): Promise<Card> {
  const fv = await view();
  store.clear();
  fv.openFileView(p, null);
  await settle();
  return card();
}
/** Open the markdown URL and let the streamed read land. */
async function openUrl(): Promise<Card> {
  const fv = await view();
  store.clear();
  fv.openUrlView(HREF);
  await settle();
  return card();
}
const rows = (body: El) => body.children.map((c) => c.className);
type Perf = ReturnType<typeof createPerfTelemetry>;
const frames = (perf: Perf) => Object.keys(perf.snapshot().frames as object);
const paints = (perf: Perf) => ((perf.snapshot().frames as Record<string, any>)["fileview:paint"]?.n ?? 0) as number;
/** Every string a row carries, keys included, for the privacy walk. */
function strings(v: unknown, at: string, out: string[]): string[] {
  if (typeof v === "string") out.push(at + "=" + v);
  else if (Array.isArray(v)) v.forEach((x, i) => strings(x, at + "[" + i + "]", out));
  else if (v && typeof v === "object") for (const k of Object.keys(v as object)) { out.push(at + "." + k); strings((v as any)[k], at + "." + k, out); }
  return out;
}
/** The one minute row the pane posted after `perf.tick()` a minute on, under `app`, with the paint alone in it. */
function minuteRow(posted: any[], app: string, n: number): any {
  const minutes = posted.filter((m) => m.what === "minute");
  assert.equal(minutes.length, 1);
  assert.equal(minutes[0].type, "clientDiag");
  assert.equal(minutes[0].surface, "perf");
  assert.equal(minutes[0].data.app, app, "counted under the pane that hosts the viewer");
  assert.deepEqual(Object.keys(minutes[0].data.frames), ["fileview:paint"]);
  assert.equal(minutes[0].data.frames["fileview:paint"].n, n);
  return minutes[0].data;
}

test("a file: each paint of the text body is one fileview:paint frame of the page's collector, when the bytes land, per Rendered/Raw toggle, and when the editor closes", async () => {
  const posted: any[] = [];
  const { perf, clock } = collector("chat", posted);
  win.__rompPerf = perf;
  try {
    const c = await openFile(README);
    assert.deepEqual(rows(c.body), ["fileview-md"], "the markdown file opened Rendered");
    assert.deepEqual(frames(perf), ["fileview:paint"], "the paint, and nothing else, was timed");
    assert.equal(paints(perf), 1, "one paint: the loader that held the body before the bytes landed is not one");
    let swaps = c.body.replaceCalls;
    c.btn("Raw").click();
    assert.deepEqual(rows(c.body), ["fileview-code"], "Raw repainted the body as the code view");
    assert.equal(paints(perf), 2, "the toggle's paint counted too");
    assert.equal(c.body.replaceCalls, swaps + 1, "the bracket ran the pass once: one swap of the body per paint");
    swaps = c.body.replaceCalls;
    c.btn("Rendered").click();
    assert.deepEqual(rows(c.body), ["fileview-md"]);
    assert.equal(paints(perf), 3);
    assert.equal(c.body.replaceCalls, swaps + 1);
    // Edit takes the body (no editor chunk here, so the plain textarea): the loader and the editor are not paints
    c.btn("Edit").click();
    await settle();
    assert.deepEqual(rows(c.body), ["fileview-editor"], "the fallback editor holds the body");
    assert.equal(paints(perf), 3, "entering the editor painted no text body");
    swaps = c.body.replaceCalls;
    c.btn("Cancel").click();
    assert.deepEqual(rows(c.body), ["fileview-code"], "leaving the editor repaints the text body (Edit works on the Raw view)");
    assert.equal(paints(perf), 4, "the repaint on leaving the editor is a paint");
    assert.equal(c.body.replaceCalls, swaps + 1);
    // the minute row the pane posts carries the bracket under the pane's own app, and no file path
    clock.wall += 60_000;
    perf.tick();
    const d = minuteRow(posted, "chat", 4);
    for (const s of strings(d, "data", [])) {
      assert.ok(!s.includes("/"), "no slash: " + s);
      assert.ok(!s.includes("notes-api") && !s.includes("README"), "no path: " + s);
    }
  } finally {
    delete win.__rompPerf;
  }
});

test("a URL document: the same bracket, when the bytes land and per toggle, under the hosting pane, with no host or document name in the row", async () => {
  const posted: any[] = [];
  const { perf, clock } = collector("feed", posted);
  win.__rompPerf = perf;
  try {
    const c = await openUrl();
    assert.deepEqual(rows(c.body), ["fileview-md"], "the document opened Rendered");
    assert.deepEqual(frames(perf), ["fileview:paint"], "the paint, and nothing else, was timed");
    assert.equal(paints(perf), 1, "one paint: the loader that held the body before the bytes landed is not one");
    const swaps = c.body.replaceCalls;
    c.btn("Raw").click();
    assert.deepEqual(rows(c.body), ["fileview-code"]);
    assert.equal(paints(perf), 2, "the toggle's paint counted too");
    assert.equal(c.body.replaceCalls, swaps + 1, "one swap of the body per paint");
    clock.wall += 60_000;
    perf.tick();
    const d = minuteRow(posted, "feed", 2);
    for (const s of strings(d, "data", [])) {
      assert.ok(!s.includes("/"), "no slash: " + s);
      assert.ok(!s.includes("notes-api") && !s.includes("evidence"), "no host or document name: " + s);
    }
  } finally {
    delete win.__rompPerf;
  }
});

test("a picture, a PDF and the SVG Source view are not paints of the text body: the collector records no frame", async () => {
  const posted: any[] = [];
  const { perf } = collector("chat", posted);
  win.__rompPerf = perf;
  try {
    const svg = await openFile(FIG);
    assert.deepEqual(rows(svg.body), ["fileview-imgbox"], "an SVG is shown as a picture");
    assert.deepEqual(frames(perf), [], "a picture is not a paint of the text body");
    svg.btn("Source").click();
    await settle();
    assert.deepEqual(rows(svg.body), ["fileview-code"], "the Source view is the highlighted XML, from the fetched bytes");
    assert.deepEqual(frames(perf), [], "nor is the Source view");
    const pdf = await openFile(PAPER);
    assert.deepEqual(rows(pdf.body), ["fileview-frame"], "a PDF is the browser's own viewer in a frame");
    assert.deepEqual(frames(perf), [], "nor is a PDF");
  } finally {
    delete win.__rompPerf;
  }
});

test("no collector on the page, or one of another shape: the paint runs untimed and the body is the same", async () => {
  const posted: any[] = [];
  const { perf } = collector("feed", posted);
  win.__rompPerf = perf;
  let timed: Card;
  try { timed = await openFile(README); } finally { delete win.__rompPerf; }
  assert.equal(paints(perf), 1);
  const shown = { rows: rows(timed.body), text: timed.body.textContent };
  // nothing published
  const bare = await openFile(README);
  assert.deepEqual(rows(bare.body), shown.rows);
  assert.equal(bare.body.textContent, shown.text, "the same paint, untimed");
  bare.btn("Raw").click();
  assert.deepEqual(rows(bare.body), ["fileview-code"]);
  assert.equal(paints(perf), 1, "the detached collector saw none of it");
  // a slot holding something that is not the collector (another script's object): left alone, no throw
  win.__rompPerf = { snapshot: () => ({}) };
  try {
    const odd = await openFile(README);
    assert.deepEqual(rows(odd.body), shown.rows);
    assert.equal(odd.body.textContent, shown.text);
    const url = await openUrl();
    assert.deepEqual(rows(url.body), ["fileview-md"], "the URL view too");
  } finally {
    delete win.__rompPerf;
  }
});
