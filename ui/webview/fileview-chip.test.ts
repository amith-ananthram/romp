// The viewer's session chip (the user 2026-09-03), run FOR REAL: openFileView mounts through
// document.createElement against the same small DOM stand-in github-link.test.ts uses, so the chip's
// three outcomes — a registered identity, a sid the document cannot name, no sid at all — are read
// off the built title bar rather than pinned at source (file-view.test.ts keeps the pins). The fetch
// the viewer starts for the file's bytes never settles here: the chip is built before it, and a
// pending promise is exactly the wait a real open shows.
import { test } from "node:test";
import * as assert from "node:assert/strict";

// ── a DOM stand-in: the members openFileView touches while it builds the bar ──────────────────────
class El {
  id = ""; title = ""; hidden = false; type = ""; disabled = false; tabIndex = -1; innerHTML = "";
  href = ""; target = ""; rel = ""; spellcheck = true; isConnected = true;
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};      // the viewer's text size rides the root as data-fv-text (textSizeControl)
  childNodes: Array<El | string> = [];
  private attrs = new Map<string, string>();
  private classes = new Set<string>();
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
  set textContent(v: string) { this.childNodes = v === "" ? [] : [v]; }
  appendChild<T extends El>(c: T): T { this.childNodes.push(c); return c; }
  replaceChildren(...cs: Array<El | string>): void { this.childNodes = [...cs]; }
  remove(): void { this.isConnected = false; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  getAttribute(k: string): string | null { return this.attrs.get(k) ?? null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  addEventListener(): void {}
  removeEventListener(): void {}
}
const win: any = new EventTarget();
win.parent = win;
(globalThis as any).window = win;
const body = new El("body");
(globalThis as any).document = {
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => s,
  getElementById: () => null,               // no viewer up, no listing beneath, no composer
  addEventListener: () => {},
  removeEventListener: () => {},
  body,
};
const store = new Map<string, string>();
(globalThis as any).localStorage = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => { store.set(k, String(v)); },
  removeItem: (k: string) => { store.delete(k); },
};
(globalThis as any).fetch = () => new Promise(() => { /* the bytes never land — the bar is built before them */ });

const WEB = "11111111-2222-3333-4444-555555555555";
const API = "22222222-3333-4444-5555-666666666666";

/** Open `path` for `sid` and return the title bar's children by class (null where a part is absent). */
async function openBar(path: string, sid: string | null): Promise<{ bar: El; name: El; sess: El | null; acts: El }> {
  const fv = await import("./file-view");
  fv.initFileView(() => {});
  fv.openFileView(path, sid);
  const wrap = body.childNodes[body.childNodes.length - 1] as El;
  assert.equal(wrap.id, "romp-fileview");
  const box = wrap.childNodes[0] as El;
  const bar = box.childNodes[0] as El;
  assert.equal(bar.className, "fileview-bar");
  const byClass = (c: string) => bar.childNodes.find((n) => n instanceof El && n.classList.contains(c)) as El | undefined;
  const name = byClass("fileview-name"), sess = byClass("fileview-sess"), acts = byClass("fileview-acts");
  assert.ok(name && acts, "the path and the actions are always there");
  return { bar, name: name!, sess: sess ?? null, acts: acts! };
}

test("a registered identity puts the session's chip between the path and the actions, in its colour", async () => {
  const fv = await import("./file-view");
  fv.setFileViewIdentity((sid) => sid === API ? { name: "api", color: { bg: "#123456", fg: "#ffffff" } } : null);
  const { bar, name, sess, acts } = await openBar("/tmp/notes-api/app.py", API);
  assert.ok(sess, "the chip is built");
  assert.equal(sess!.tagName, "span");
  assert.equal(sess!.textContent, "api");
  assert.equal(sess!.title, "Opened from the api session");
  assert.equal(sess!.style.background, "#123456", "the identity colour rides inline, from the resolved row");
  assert.equal(sess!.style.color, "#ffffff");
  assert.ok(bar.childNodes.indexOf(name) < bar.childNodes.indexOf(sess!) && bar.childNodes.indexOf(sess!) < bar.childNodes.indexOf(acts),
    "path, then the chip, then the actions");
});

test("a remote session's chip renders its host: as a quiet token inside the pill", async () => {
  const fv = await import("./file-view");
  fv.setFileViewIdentity((sid) => sid === "TESTHOST:" + WEB ? { name: "TESTHOST:web", color: { bg: "#3a7bd5", fg: "#ffffff" } } : null);
  const { sess } = await openBar("/tmp/notes-api/app.py", "TESTHOST:" + WEB);
  assert.ok(sess);
  assert.equal(sess!.textContent, "TESTHOST:web");
  const host = sess!.childNodes[0] as El;
  assert.ok(host instanceof El && host.classList.contains("host-prefix"), "hostNameNodes marks the host: token");
  assert.equal(host.textContent, "TESTHOST:");
  assert.equal(sess!.childNodes[1], "web", "…and the name follows as plain text");
});

test("a sid the resolver cannot name falls to the resolver's own stub, uncoloured", async () => {
  const fv = await import("./file-view");
  // a resolver ends its ladder in hostStub — the same tail render.ts and feed.ts register
  fv.setFileViewIdentity((sid) => fv.hostStub(sid));
  const { sess } = await openBar("/tmp/notes-api/app.py", "44444444-5555-6666-7777-888888888888");
  assert.ok(sess);
  assert.equal(sess!.textContent, "44444444", "the kernel's 8-character stub");
  assert.equal(sess!.style.background, undefined, "no colour to wear — the sheet's neutral pill shows");
  assert.equal(sess!.style.color, undefined);
  assert.equal(sess!.title, "Opened from the 44444444 session");
});

test("no sid, or a resolver that names nothing, means no chip element at all", async () => {
  const fv = await import("./file-view");
  fv.setFileViewIdentity((sid) => sid === API ? { name: "api", color: null } : null);
  assert.equal((await openBar("/tmp/notes-api/app.py", null)).sess, null, "no sid → the resolver is not even asked");
  assert.equal((await openBar("/tmp/notes-api/app.py", "")).sess, null, "an empty sid is no sid");
  fv.setFileViewIdentity(() => null);
  assert.equal((await openBar("/tmp/notes-api/app.py", API)).sess, null, "a resolver with no answer → no chip, never a guess");
});
