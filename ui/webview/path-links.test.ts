// The chat's path matcher, executed (path-links.ts). It lived inline in render.ts, whose only tests are source
// pins; lifted into a module of its own it runs for real over a small DOM stand-in (no jsdom): the walk over a
// message body, the shape gates, the trailing-punctuation trim, the kernel's pathLinks verdict narrowing the
// links, and the span each hit is marked as. What a click does is the hosting document's (render.ts binds
// openPath per span; chat-path-links.test.ts and chat-relpath-link.test.ts pin that wiring at source).
//
// Three promises beyond the matches, since the file viewer runs the same walk (file-view-links.ts):
// 1. A link is a CONTROL from the keyboard too: the span is a tab stop announced as a link, Enter or Space clicks
//    it, and a mouse press does not focus it (so a click leaves focus where a click on plain text leaves it).
// 2. The walk costs time LINEAR in the text. CLICKABLE_PATH_RE run with the g flag restarts at every position
//    and rescans an unbroken word run to its end each time: one slash plus a 40K-character run cost seconds per
//    text node, and a viewer shows whole files. PathTokenScanner drives the same regex in linear time; the
//    trailing-punctuation trim, quadratic for the same reason on a long token of dots, scans backwards. The
//    regex's TEXT is the kernel's parity contract (tests/fixtures/path_token_parity.json), so what is pinned
//    here is that the scanner finds exactly what the regex finds, from every start position, over fuzzed and
//    adversarial texts.
// 3. The text is read a UNIT at a time when a surface asks (PathLinkOptions.unit): a highlight's spans cut a
//    line into several nodes, and a token is what the line says; a token a span cuts through is left as text.
// Synthetic fixtures only: the notes-api demo world, a placeholder session id.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const LINKS = fs.readFileSync(path.join(UI, "path-links.ts"), "utf8");

// ── a DOM stand-in: text nodes, elements with attributes, a small selector engine, fragments ──────────
type Compound = { tag: string | null; classes: string[]; attrs: Array<[string, string | null]> };
function parseSel(sel: string): Compound[][] {
  return sel.split(",").map((g) => g.trim()).filter(Boolean).map((g) => g.split(/\s+/).map((s) => {
    const m = /^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)((?:\[[\w-]+(?:="[^"]*")?\])*)$/.exec(s);
    if (!m) throw new Error("stand-in: unsupported selector " + s);
    const classes = (m[2].match(/\.[\w-]+/g) || []).map((c) => c.slice(1));
    const attrs: Array<[string, string | null]> = [];
    for (const a of m[3].match(/\[[^\]]+\]/g) || []) { const am = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(a)!; attrs.push([am[1], am[2] ?? null]); }
    return { tag: m[1] ? m[1].toUpperCase() : null, classes, attrs };
  }));
}
class Txt {
  nodeType = 3;
  parentNode: El | null = null;
  constructor(public data: string) {}
  get textContent(): string { return this.data; }
  get parentElement(): El | null { return this.parentNode; }
  replaceWith(n: El | Txt | Frag): void {
    const p = this.parentNode!;
    const i = p.childNodes.indexOf(this);
    const kids = n instanceof Frag ? n.childNodes.slice() : [n];
    for (const k of kids) { if (k.parentNode) k.parentNode.removeChild(k); k.parentNode = p; }
    p.childNodes.splice(i, 1, ...kids);
    this.parentNode = null;
  }
}
class Frag { childNodes: Array<El | Txt> = []; appendChild(c: El | Txt): void { this.childNodes.push(c); } }
const kebab = (k: string) => k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
class El {
  nodeType = 1;
  tagName: string;
  parentNode: El | null = null;
  childNodes: Array<El | Txt> = [];
  attrs = new Map<string, string>();
  role: string | null = null;
  onkeydown: ((e: unknown) => void) | null = null; onmousedown: ((e: unknown) => void) | null = null; onmouseup: ((e: unknown) => void) | null = null;
  onmouseleave: ((e: unknown) => void) | null = null; oncontextmenu: ((e: unknown) => void) | null = null; ondragstart: ((e: unknown) => void) | null = null;
  clicks = 0;
  dispatched: unknown[] = [];
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
  click(): void { this.clicks++; }
  dispatchEvent(e: unknown): boolean { this.dispatched.push(e); return true; }
  blur(): void { if (doc.activeElement === this) doc.activeElement = null; }
  get tabIndex(): number { return this.attrs.has("tabindex") ? Number(this.attrs.get("tabindex")) : -1; } set tabIndex(v: number) { this.attrs.set("tabindex", String(v)); }
  get parentElement(): El | null { return this.parentNode; }
  get className(): string { return this.attrs.get("class") || ""; } set className(v: string) { this.attrs.set("class", v); }
  get title(): string { return this.attrs.get("title") || ""; } set title(v: string) { this.attrs.set("title", v); }
  get classes(): string[] { return this.className.split(/\s+/).filter(Boolean); }
  dataset: Record<string, string> = new Proxy({} as Record<string, string>, {
    get: (_, k) => this.attrs.get("data-" + kebab(String(k))) as string,
    set: (_, k, v) => { this.attrs.set("data-" + kebab(String(k)), String(v)); return true; },
    has: (_, k) => this.attrs.has("data-" + kebab(String(k))),
  });
  get textContent(): string { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v: string) { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; if (v !== "") this.appendChild(new Txt(v)); }
  private detach(n: El | Txt): void { const p = n.parentNode; if (p) { const i = p.childNodes.indexOf(n); if (i >= 0) p.childNodes.splice(i, 1); n.parentNode = null; } }
  appendChild<T extends El | Txt>(n: T): T { this.detach(n); this.childNodes.push(n); n.parentNode = this; return n; }
  removeChild<T extends El | Txt>(n: T): T { this.detach(n); return n; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  getAttribute(k: string): string | null { return this.attrs.has(k) ? (this.attrs.get(k) as string) : null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  private fits(c: Compound): boolean {
    return (!c.tag || c.tag === this.tagName) && c.classes.every((k) => this.classes.includes(k))
      && c.attrs.every(([a, v]) => this.attrs.has(a) && (v === null || this.attrs.get(a) === v));
  }
  matches(sel: string): boolean {
    return parseSel(sel).some((chain) => {
      if (!this.fits(chain[chain.length - 1])) return false;
      let k = chain.length - 2;
      for (let a: El | null = this.parentNode; a && k >= 0; a = a.parentNode) if (a.fits(chain[k])) k--;
      return k < 0;
    });
  }
  closest(sel: string): El | null { for (let x: El | null = this; x; x = x.parentNode) if (x.matches(sel)) return x; return null; }
  querySelectorAll(sel: string): El[] {
    const out: El[] = [];
    const visit = (n: El) => { for (const c of n.childNodes) if (c instanceof El) { if (c.matches(sel)) out.push(c); visit(c); } };
    visit(this);
    return out;
  }
}
/** Document-order nodes under `root`, as a browser's tree walker answers them (SHOW_TEXT = 4, SHOW_ELEMENT = 1). */
function walkNodes(root: El, what: number): Array<El | Txt> {
  const out: Array<El | Txt> = [];
  const walk = (n: El) => { for (const c of n.childNodes) { if (c instanceof Txt) { if (what & 4) out.push(c); } else { if (what & 1) out.push(c); walk(c); } } };
  walk(root);
  return out;
}
function textNodesOf(root: El): Txt[] { return walkNodes(root, 4) as Txt[]; }
const doc = {
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => new Txt(s),
  createDocumentFragment: () => new Frag(),
  createTreeWalker: (root: El, what = 4) => { const nodes = walkNodes(root, what); let i = 0; return { nextNode: () => (i < nodes.length ? nodes[i++] : null) }; },
  activeElement: null as El | null,
};
(globalThis as any).NodeFilter = { SHOW_ELEMENT: 1, SHOW_TEXT: 4 };
(globalThis as any).document = doc;
(globalThis as any).MouseEvent = class { constructor(public type: string, public init: Record<string, unknown>) {} };
// a keydown as the browser would deliver it to the span's own handler
function press(a: El, key: string, mods: { metaKey?: boolean; ctrlKey?: boolean } = {}): boolean {
  let prevented = false;
  a.onkeydown!({ key, currentTarget: a, preventDefault: () => { prevented = true; }, ...mods });
  return prevented;
}

const el = (tag: string, cls?: string, ...kids: Array<El | Txt | string>): El => {
  const e = new El(tag); if (cls) e.className = cls;
  for (const k of kids) e.appendChild(typeof k === "string" ? new Txt(k) : k);
  return e;
};
const links = (root: El) => root.querySelectorAll(".file-uri-link");
const shape = (a: El) => [a.textContent, a.dataset.path, a.dataset.rel, a.title];

// ── the span ──────────────────────────────────────────────────────────────────────────────────────
test("openPathLink marks a span: the raw text as written, the target in data-path and the title, data-rel for a bare path; a file:// URI's link opens its decoded path and carries no data-rel", async () => {
  const { openPathLink, fileUriLink } = await import("./path-links");
  const a = openPathLink("design/foo.md", "design/foo.md", true) as unknown as El;
  assert.equal(a.tagName, "SPAN"); assert.equal(a.className, "file-uri-link");
  assert.deepEqual(shape(a), ["design/foo.md", "design/foo.md", "1", "Open design/foo.md"]);
  const fixed = openPathLink("render.js", "ui/webview/render.js", true) as unknown as El;
  assert.deepEqual(shape(fixed), ["render.js", "ui/webview/render.js", "1", "Open ui/webview/render.js"], "a shortened mention shows as written and opens the kernel's fixed target");
  const abs = openPathLink("/tmp/TESTHOST/a.md", "/tmp/TESTHOST/a.md") as unknown as El;
  assert.deepEqual(shape(abs), ["/tmp/TESTHOST/a.md", "/tmp/TESTHOST/a.md", undefined, "Open /tmp/TESTHOST/a.md"]);
  const u = fileUriLink("file:///tmp/TESTHOST/a%20b.pdf") as unknown as El;
  assert.deepEqual(shape(u), ["file:///tmp/TESTHOST/a%20b.pdf", "/tmp/TESTHOST/a b.pdf", undefined, "Open /tmp/TESTHOST/a b.pdf"]);
  // the module binds no ACTION: no click handler on the span (render.ts's bindPathLink adds the click); its own handlers are about focus
  assert.equal((a as any).onclick, undefined);
  assert.equal(a.tabIndex, 0, "a tab stop, like the <a> it stands in for"); assert.equal(a.role, "link");
  for (const k of ["onkeydown", "onmousedown", "onmouseup", "onmouseleave", "oncontextmenu", "ondragstart"] as const) assert.equal(typeof a[k], "function", k);
});

test("a path link is a control from the keyboard: Enter or Space clicks it (the host's click, whoever bound it), other keys are left alone, and a held Cmd/Ctrl rides into the click as the same modifier", async () => {
  const { openPathLink, fileUriLink } = await import("./path-links");
  const a = openPathLink("docs/design.md", "docs/design.md", true) as unknown as El;
  assert.equal(press(a, "Enter"), true, "Enter is consumed…");
  assert.equal(a.clicks, 1, "…and becomes this span's click, which bubbles to whatever the host bound");
  assert.equal(press(a, " "), true, "Space too, prevented so it does not also scroll the pane");
  assert.equal(a.clicks, 2);
  for (const k of ["Tab", "Escape", "a", "ArrowDown", "Shift"]) assert.equal(press(a, k), false, k + " is left to the browser");
  assert.equal(a.clicks, 2, "no other key activates");
  assert.equal(press(a, "Enter", { metaKey: true }), true);
  assert.equal(a.clicks, 2, "element.click() carries no modifiers, so a modified key dispatches the click itself…");
  assert.deepEqual(a.dispatched.map((e) => [(e as any).type, (e as any).init]), [["click", { bubbles: true, cancelable: true, metaKey: true, ctrlKey: undefined }]], "…with the modifier on it");
  const u = fileUriLink("file:///tmp/TESTHOST/a.pdf") as unknown as El;
  assert.equal(u.tabIndex, 0); assert.equal(u.role, "link"); press(u, "Enter"); assert.equal(u.clicks, 1, "every span the module mints takes the same route");
});

test("a mouse press does not focus a path link: the press drops the tab stop (and blurs a keyboard focus), the release brings it back, so a click leaves focus where a click on plain text leaves it", async () => {
  const { openPathLink } = await import("./path-links");
  const a = openPathLink("docs/a.md", "docs/a.md", true) as unknown as El;
  a.onmousedown!({ currentTarget: a });
  assert.equal(a.hasAttribute("tabindex"), false, "not focusable for the rest of this press: the browser's default focus finds no tab stop");
  a.onmouseup!({ currentTarget: a });
  assert.equal(a.tabIndex, 0, "back in the tab order once the press has ended on it");
  for (const end of ["onmouseleave", "oncontextmenu", "ondragstart"] as const) {
    a.onmousedown!({ currentTarget: a }); assert.equal(a.hasAttribute("tabindex"), false);
    a[end]!({ currentTarget: a }); assert.equal(a.tabIndex, 0, end + " ends the press too (a drag away, a menu, a native drag)");
  }
  doc.activeElement = a;                          // focused by Tab, then pressed: the press ends the keyboard focus too
  a.onmousedown!({ currentTarget: a });
  assert.equal(doc.activeElement, null, "blurred by the press itself, since the browser fires no focus for an already-focused element");
});

test("markPathLink dresses an element the caller already has (a Markdown link's own <a>): the class is appended, the title and class are written as attributes (an SVG <a> has no such properties), the data and the handlers are the span's", async () => {
  const { markPathLink } = await import("./path-links");
  const a = new El("a"); a.className = "fancy"; a.appendChild(new Txt("the app"));
  markPathLink(a as unknown as HTMLElement, "/tmp/TESTHOST/app.py", true);
  assert.equal(a.getAttribute("class"), "fancy file-uri-link"); assert.equal(a.getAttribute("title"), "Open /tmp/TESTHOST/app.py");
  assert.equal(a.dataset.path, "/tmp/TESTHOST/app.py"); assert.equal(a.dataset.rel, "1"); assert.equal(a.tabIndex, 0); assert.equal(a.role, "link");
  markPathLink(a as unknown as HTMLElement, "/tmp/TESTHOST/app.py", true);
  assert.equal(a.getAttribute("class"), "fancy file-uri-link", "marked twice wears the class once");
  assert.equal(a.textContent, "the app", "the label is untouched");
});

test("a file:// URI is local with an empty authority or localhost only: file://host/path names another machine, is prose to the walk, and comes back from fileUriToPath as written", async () => {
  const { isFileUri, fileUriToPath, linkifyPathTokens } = await import("./path-links");
  for (const u of ["file:///tmp/TESTHOST/a.md", "file://localhost/tmp/a.md", "FILE:///tmp/a.md", "file://LOCALHOST/x"]) assert.equal(isFileUri(u), true, u);
  for (const u of ["file://evil.invalid/share/x.md", "file://TESTHOST/x.md", "file://localhost", "file:/x.md", "files:///x", "file://localhostx/y"]) assert.equal(isFileUri(u), false, u);
  assert.equal(fileUriToPath("file://localhost/tmp/a%20b.md"), "/tmp/a b.md");
  assert.equal(fileUriToPath("file://evil.invalid/share/x.md"), "file://evil.invalid/share/x.md");
  const p = el("p", "", "see file://evil.invalid/share/x.md and file:///tmp/TESTHOST/y.md");
  assert.deepEqual(linkifyPathTokens(p as unknown as HTMLElement).map((h) => h.open), ["/tmp/TESTHOST/y.md"], "the far URI is not a relative path named after its host");
});

// ── the shape gates, on the real functions ────────────────────────────────────────────────────────
test("looksLikeFilePath and looksLikeBareFileName: anchored starts and slashed paths with an extension link, prose fractions and idioms do not; a bare filename needs a known extension", async () => {
  const { looksLikeFilePath, looksLikeBareFileName, fileUriToPath } = await import("./path-links");
  for (const p of ["design/foo.md", "/abs/path", "~/x", "./rel", "../up", "a/b/c.py", "ui/webview/render.ts"]) assert.equal(looksLikeFilePath(p), true, p);
  for (const p of ["and/or", "TCP/IP", "24/7", "read/write", "http://x/y", "a:b/c.md", "noslash.md", "src/lib"]) assert.equal(looksLikeFilePath(p), false, p);
  for (const p of ["power2_watts.pdf", "report.md", "data.csv", "notes.MD"]) assert.equal(looksLikeBareFileName(p), true, p);
  for (const p of ["np.array", "s.color", "0.4.293", ".md", "a/b.md", "x:y.md", "romp.kernelPort"]) assert.equal(looksLikeBareFileName(p), false, p);
  assert.equal(fileUriToPath("file:///tmp/TESTHOST/a%20b.md"), "/tmp/TESTHOST/a b.md");
  assert.equal(fileUriToPath("file:///tmp/TESTHOST/%zz.md"), "/tmp/TESTHOST/%zz.md", "a malformed escape is kept as written");
});

// ── the walk over a message body ──────────────────────────────────────────────────────────────────
test("the walk over a chat body: slashed paths and file:// URIs become spans, prose stays, a sentence's closing punctuation is left as text, and the body's text reads exactly as before", async () => {
  const { linkifyPathTokens } = await import("./path-links");
  const p = el("p", "", "see design/foo.md. Then and/or 24/7, TCP/IP and (file:///tmp/TESTHOST/a%20b.pdf), then ui/webview/render.ts!");
  const before = p.textContent;
  const hits = linkifyPathTokens(p as unknown as HTMLElement);
  assert.equal(p.textContent, before, "the pass adds elements around text and changes no character");
  assert.deepEqual(links(p).map(shape), [
    ["design/foo.md", "design/foo.md", "1", "Open design/foo.md"],
    ["file:///tmp/TESTHOST/a%20b.pdf", "/tmp/TESTHOST/a b.pdf", undefined, "Open /tmp/TESTHOST/a b.pdf"],
    ["ui/webview/render.ts", "ui/webview/render.ts", "1", "Open ui/webview/render.ts"],
  ]);
  assert.deepEqual(textNodesOf(p).map((t) => t.data), ["see ", "design/foo.md", ". Then and/or 24/7, TCP/IP and (", "file:///tmp/TESTHOST/a%20b.pdf", "), then ", "ui/webview/render.ts", "!"]);
  // the hits, in document order, name the span and what it opens; nothing here was kernel-verified (no map)
  assert.deepEqual(hits.map((h) => [h.open, h.verified, (h.el as unknown as El).textContent]), [["design/foo.md", false, "design/foo.md"], ["/tmp/TESTHOST/a b.pdf", false, "file:///tmp/TESTHOST/a%20b.pdf"], ["ui/webview/render.ts", false, "ui/webview/render.ts"]]);
});

test("inline code: a bare filename with a known extension links inside <code> only; a dotted identifier, a version and an unknown extension stay; prose never links a bare name", async () => {
  const { linkifyPathTokens } = await import("./path-links");
  const p = el("p", "", "wrote ", el("code", "", "power2_watts.pdf"), " and ", el("code", "", "np.array"), " and ", el("code", "", "0.4.293"), " and ", el("code", "", "out.xyz"), "; also report.md in prose");
  const before = p.textContent;
  linkifyPathTokens(p as unknown as HTMLElement);
  assert.equal(p.textContent, before);
  assert.deepEqual(links(p).map((a) => a.textContent), ["power2_watts.pdf"]);
  assert.equal(links(p)[0].parentNode!.tagName, "CODE", "the link sits inside the code span");
  assert.deepEqual(textNodesOf(p).map((t) => t.data).slice(-1), ["; also report.md in prose"], "a bare name in prose is not a link");
});

test("skipped text: inside an existing anchor, inside a span already linked, and inside a fenced <pre> block; a unit with no slash (and, in code, no dot) is not even scanned", async () => {
  const { linkifyPathTokens, openPathLink } = await import("./path-links");
  const already = openPathLink("docs/x.md", "docs/x.md", true) as unknown as El;
  const p = el("div", "",
    el("p", "", "a ", el("a", "", "docs/linked.md"), " b ", already, " c docs/free.md"),
    el("pre", "", el("code", "", "cat docs/fenced.md")),
    el("p", "", "no path here at all"),
  );
  const before = p.textContent;
  const hits = linkifyPathTokens(p as unknown as HTMLElement);
  assert.equal(p.textContent, before);
  assert.deepEqual(hits.map((h) => h.open), ["docs/free.md"], "the anchor's, the linked span's and the fence's text are left as they are");
  assert.equal(links(p).length, 2, "the span that was already a link, and the one new link");
});

test("the kernel's pathLinks verdict: with a map, a token links ONLY when it is a key and opens the map's value (verified); with no map, shape alone decides; a file:// URI is never gated on the map", async () => {
  const { linkifyPathTokens } = await import("./path-links");
  const text = "fix render.js and kernel/sub/deep.py, not a/dup.py; see file:///tmp/TESTHOST/z.md";
  const gated = el("p", "", el("code", "", "render.js"), " " + text);
  const hits = linkifyPathTokens(gated as unknown as HTMLElement, { "render.js": "ui/webview/render.js", "kernel/sub/deep.py": "kernel/sub/deep.py" });
  assert.deepEqual(hits.map((h) => [h.open, h.verified]), [["ui/webview/render.js", true], ["kernel/sub/deep.py", true], ["/tmp/TESTHOST/z.md", false]],
    "the backticked mention opens the fixed target (the bare name in prose fails the shape gate first: the map only ever narrows); a/dup.py, absent from the map (no such file, or several), stays prose; the URI rides regardless");
  assert.deepEqual(links(gated).map(shape)[0], ["render.js", "ui/webview/render.js", "1", "Open ui/webview/render.js"], "shown as written, opens the real file, hover names it");
  assert.equal(links(gated).length, 3);
  // no map at all (an old kernel, a cached payload): every shape-passing token links as written, none verified
  const free = el("p", "", text);
  const h2 = linkifyPathTokens(free as unknown as HTMLElement);
  assert.deepEqual(h2.map((h) => [h.open, h.verified]), [["kernel/sub/deep.py", false], ["a/dup.py", false], ["/tmp/TESTHOST/z.md", false]], "render.js has no slash and is not in code: prose either way");
  // an EMPTY map is a verdict too: nothing links but the URI
  const none = el("p", "", text);
  assert.deepEqual(linkifyPathTokens(none as unknown as HTMLElement, {}).map((h) => h.open), ["/tmp/TESTHOST/z.md"]);
});

test("the resume rule: after a linked token the scan resumes right after it, so a token's trimmed punctuation and the text after it are read again as prose; a token that stays prose is skipped whole", async () => {
  const { linkifyPathTokens } = await import("./path-links");
  const p = el("p", "", "a/b.md,c/d.md; and/or e/f.md");
  linkifyPathTokens(p as unknown as HTMLElement);
  assert.deepEqual(links(p).map((a) => a.textContent), ["a/b.md", "c/d.md", "e/f.md"]);
  assert.deepEqual(textNodesOf(p).map((t) => t.data), ["a/b.md", ",", "c/d.md", "; and/or ", "e/f.md"]);
});

// ── the module's contract with render.ts, at source ───────────────────────────────────────────────
test("source: the module marks and binds nothing; render.ts binds the click and the middle button per span off the span's data, and reads the hits for its figure pass", () => {
  const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
  assert.doesNotMatch(LINKS, /addEventListener|onclick|openPath\(|window\.open|postMessage|fetch\(/, "no action of its own: its handlers are about focus (keydown, the press)");
  assert.match(LINKS, /export function linkifyPathTokens\(root: HTMLElement, pathLinks\?: Record<string, string>, opts\?: PathLinkOptions\): PathLinkHit\[\] \{/);
  assert.match(LINKS, /export interface PathLinkHit \{ el: HTMLElement; open: string; verified: boolean; inPre: boolean \}/, "+ inPre: the chat skips its figure pass for a fenced hit (2026-09-12)");
  assert.match(RENDER, /import \{ openPathLink, linkifyPathTokens, selectionOpenIn, type PathLinkOptions \} from "\.\/path-links";/);
  assert.match(RENDER, /function bindPathLink\(a: HTMLElement\): HTMLElement \{\n\s*const open = a\.dataset\.path \|\| "", relative = a\.dataset\.rel === "1";\n\s*a\.addEventListener\("click", \(e\) => \{\n\s*e\.stopPropagation\(\);\n\s*filePreviewIntent\.cancel\(\);\n\s*openPath\(open, relative \? activeId : null, e, a\.dataset\.frag \|\| null\);[^\n]*\n\s*\}\);\n\s*onMiddleClick\(a, \(e\) => openPath\(open, relative \? activeId : null, e, a\.dataset\.frag \|\| null\)\);\n\s*armFilePreview\(a\);[^\n]*\n\s*return a;\n\}/);
  assert.match(RENDER, /const link = bindPathLink\(openPathLink\(tok, tok, true\)\);\n\s*armPreview\(link, tok, tok\);\n\s*code\.replaceChildren\(link\);/, "the kernel-verified spaced span takes the same binder");
  assert.match(RENDER, /for \(const \{ el: link, open, verified, inPre \} of linkifyPathTokens\(root, pathLinks, FENCE_WALK\)\) \{\n\s*bindPathLink\(link\);\n\s*armPreview\(link, link\.textContent \|\| "", open\);\n\s*absorbFragment\(link\);\n\s*if \(verified\) kernelVerified\.add\(open\);/,
    "the chat's walk carries its fenced-block options (2026-09-12); every hit is bound and previewable, a fenced one renders no figure");
  // the matcher lives in ONE place: render.ts no longer declares the regex or its gates
  for (const name of ["CLICKABLE_PATH_RE", "function looksLikeFilePath", "function looksLikeBareFileName", "const BARE_FILE_EXTS", "function fileUriToPath", "function openPathLink", "function fileUriLink"]) {
    assert.ok(!RENDER.includes(name), name + " is path-links.ts's alone");
    assert.ok(LINKS.includes(name), name + " in path-links.ts");
  }
});

// ── the linear driver for the regex ───────────────────────────────────────────────────────────────
/** What CLICKABLE_PATH_RE.exec finds from `from` with the g flag: the reference the scanner must match exactly. */
function regexNext(re: RegExp, text: string, from: number): [number, number] | null {
  re.lastIndex = from;
  const m = re.exec(text);
  return m ? [m.index, m.index + m[0].length] : null;
}
async function scannerAgrees(text: string): Promise<void> {
  const { PathTokenScanner, CLICKABLE_PATH_RE } = await import("./path-links");
  const re = new RegExp(CLICKABLE_PATH_RE.source, "gi");
  const scan = new PathTokenScanner(text);
  for (let from = 0; from <= text.length; from++) {
    assert.deepEqual(scan.next(from), regexNext(re, text, from), JSON.stringify(text.slice(0, 80)) + " from " + from);
  }
  // …and the walk's own use: successive calls that never move `from` backwards, resuming after each match
  const s2 = new PathTokenScanner(text);
  let from = 0, m: [number, number] | null;
  while ((m = s2.next(from))) { assert.deepEqual(m, regexNext(re, text, from)); from = m[1]; }
  assert.equal(regexNext(re, text, from), null);
}

test("the scanner's arms are cut from CLICKABLE_PATH_RE itself: three, sticky, the regex's own text", async () => {
  const { CLICKABLE_PATH_RE } = await import("./path-links");
  const arms = CLICKABLE_PATH_RE.source.split("|");
  assert.equal(arms.length, 3);
  assert.match(arms[0], /^file:/); assert.match(arms[1], /^\[~\.\\w\\-\]\*\\\//); assert.match(arms[2], /^\[\\w\\-\]/);
  assert.match(LINKS, /const \[URI_ARM, PATH_ARM, BARE_ARM\] = CLICKABLE_PATH_RE\.source\.split\("\|"\)\.map\(\(arm\) => new RegExp\(arm, "iy"\)\);/);
  assert.equal(CLICKABLE_PATH_RE.source, "file:\\/\\/\\/?[^\\s<>\"'`)]+|[~.\\w\\-]*\\/[~.\\w\\-/]*[\\w\\-]|[\\w\\-][\\w\\-.]*\\.[A-Za-z0-9]{1,8}", "the parity contract's text, unchanged by the lift");
});

test("the scanner finds exactly what the regex finds, from every start position: adversarial shapes", async () => {
  for (const text of [
    "", "/", "a/b.md", "see a/b.md and c/d.py.", "file:///tmp/x.md and file://h/y", "FILE:///X", "f", "ff", "fil", "file:",
    "~/x", "./a", "../b/c", "a//b", "//", "a.b.c", ".md", "a.", "-a/b-", "_x/_y.z", "x/y/z/", "a b/c d.md e",
    "1/2.5", "24/7", "and/or", "np.array", "0.4.293", "a.verylongextensionx", "a.b.c.d.e.f.g.h.i", "x/.hidden", "..", ".",
    "α/β.md", "a/ß.py", "日本語/x.md", "a\tb/c.md", "a\nb/c.md", "(a/b.md)", "\"a/b.md\"", "`a/b.md`", "<a/b.md>", "[a/b.md]",
    "x".repeat(50) + "/" + "y".repeat(50) + ".md", "-".repeat(30), ".".repeat(30), "/".repeat(30), "~".repeat(10) + "/x",
    "f".repeat(20) + "ile:///a", "file:" + "/".repeat(10) + "a", "a/b.md:12", "a/b.md#L3", "http://x.y/z.html", "git@h:u/r.git",
  ]) await scannerAgrees(text);
});

test("the scanner finds exactly what the regex finds, from every start position: fuzzed texts", async () => {
  const alphabet = "abfz09_-./~ :\n\"'`()<>ile";
  let seed = 12345;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let i = 0; i < 300; i++) {
    const n = 1 + Math.floor(rnd() * 40);
    let t = "";
    for (let j = 0; j < n; j++) t += alphabet[Math.floor(rnd() * alphabet.length)];
    await scannerAgrees(t);
  }
});

test("tokenizer parity: the scanner over the shared kernel fixture, on the walk's own loop (the trim, the resume after the trimmed token)", async () => {
  const { PathTokenScanner, TRAILING_PUNCT_RE } = await import("./path-links");
  const fixture = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "tests", "fixtures", "path_token_parity.json"), "utf8"));
  for (const c of fixture.cases as { text: string; tokens: string[] }[]) {
    const toks: string[] = [];
    const scan = new PathTokenScanner(c.text);
    let from = 0, m: [number, number] | null;
    while ((m = scan.next(from))) {
      const tok = c.text.slice(m[0], m[1]).replace(TRAILING_PUNCT_RE, "");
      from = Math.max(m[0] + tok.length, from + 1);
      if (tok && !toks.includes(tok)) toks.push(tok);
    }
    assert.deepEqual(toks, c.tokens, c.text);
  }
});

test("the trailing-punctuation set has one source: the regex is built from the characters the trim scans, and the walk trims as before", async () => {
  const { TRAILING_PUNCT, TRAILING_PUNCT_RE, linkifyPathTokens } = await import("./path-links");
  assert.equal(TRAILING_PUNCT, ".,;:!?)]}>\"'`");
  for (const ch of TRAILING_PUNCT) assert.match("a" + ch, TRAILING_PUNCT_RE, ch);
  for (const ch of "a/_-~") assert.doesNotMatch("a" + ch, TRAILING_PUNCT_RE, ch);
  assert.match(LINKS, /export const TRAILING_PUNCT_RE = new RegExp\("\[" \+ TRAILING_PUNCT\.replace\(\/\[\\\\\\\]\^-\]\/g, "\\\\\$&"\) \+ "\]\+\$"\);/);
  const p = el("p", "", "see a/b.md.), then c/d.md!?'\"`");
  linkifyPathTokens(p as unknown as HTMLElement);
  assert.deepEqual(textNodesOf(p).map((t) => t.data), ["see ", "a/b.md", ".), then ", "c/d.md", "!?'\"`"]);
});

test("pathological inputs finish and match: a slash before a 40K run of hex, a 40K token of dots, a 40K separator line, a minified dump", async () => {
  const { linkifyPathTokens, CLICKABLE_PATH_RE, PathTokenScanner } = await import("./path-links");
  const re = new RegExp(CLICKABLE_PATH_RE.source, "gi");
  for (const text of ["/" + "a1b2c3d4".repeat(5000), "/" + ".".repeat(40000), "-".repeat(40000) + "/x.md", "x/" + "_".repeat(40000), "var a=1;".repeat(5000) + "/a.b"]) {
    const scan = new PathTokenScanner(text);
    let from = 0, m: [number, number] | null;
    while ((m = scan.next(from))) { assert.deepEqual(m, regexNext(re, text, from)); from = m[1]; }
    assert.equal(regexNext(re, text, from), null);
    const p = el("p", "", text);
    linkifyPathTokens(p as unknown as HTMLElement);
    assert.equal(p.textContent, text, "the text reads as before");
  }
});

// ── the units: a line at a time, when a surface asks ──────────────────────────────────────────────
test("textUnits: with no unit selector every text node is a unit of its own (the chat's walk); under a selector the nodes sharing a unit ancestor join into one text; a <br> ends a unit; an empty unit element is an empty unit in its place; dead and inCode are read per node", async () => {
  const { textUnits, DEAD_TEXT } = await import("./path-links");
  const p = el("p", "", "a ", el("span", "x", "b"), " c");
  assert.deepEqual(textUnits(p as unknown as HTMLElement, undefined, DEAD_TEXT).map((u) => u.text), ["a ", "b", " c"], "no selector: node by node");
  const joined = textUnits(p as unknown as HTMLElement, "p", DEAD_TEXT);
  assert.equal(joined.length, 1); assert.equal(joined[0].text, "a b c");
  assert.deepEqual(joined[0].spans.map((s) => [s.start, s.end, s.dead, s.inCode]), [[0, 2, false, false], [2, 3, false, false], [3, 5, false, false]]);
  const rows = el("div", "", el("span", "fv-cl", el("span", "fv-ct", "one ", el("a", "", "docs/a.md"))), el("span", "fv-cl", el("span", "fv-ct")), el("span", "fv-cl", el("span", "fv-ct", el("code", "", "x.md"), " y", el("br"), "z")));
  const units = textUnits(rows as unknown as HTMLElement, ".fv-cl", DEAD_TEXT);
  assert.deepEqual(units.map((u) => u.text), ["one docs/a.md", "", "x.md y", "z"], "the second row is blank and stays a unit; the <br> cuts the third");
  assert.deepEqual(units[0].spans.map((s) => s.dead), [false, true], "the anchor's text is dead to marking but read");
  assert.deepEqual(units[2].spans.map((s) => s.inCode), [true, false]);
});

test("spanHolding and rewriteSpan: the span holding a range whole and open, null across an edge or in a dead span; a rewrite replaces one node with text and marks in order and changes no character", async () => {
  const { textUnits, spanHolding, rewriteSpan, DEAD_TEXT } = await import("./path-links");
  const p = el("p", "", "aa", el("a", "", "bb"), "ccdd");
  const u = textUnits(p as unknown as HTMLElement, "p", DEAD_TEXT)[0];
  assert.equal(u.text, "aabbccdd");
  assert.equal(spanHolding(u, 0, 2), u.spans[0]); assert.equal(spanHolding(u, 4, 8), u.spans[2]); assert.equal(spanHolding(u, 6, 8), u.spans[2]);
  assert.equal(spanHolding(u, 1, 3), null, "across an edge"); assert.equal(spanHolding(u, 2, 4), null, "in the link: dead"); assert.equal(spanHolding(u, 8, 9), null); assert.equal(spanHolding(u, -1, 1), null);
  const m1 = el("b", "", "c"), m2 = el("i", "", "d");
  rewriteSpan(u, u.spans[2], [{ start: 5, end: 6, el: m1 as unknown as Node }, { start: 7, end: 8, el: m2 as unknown as Node }]);
  assert.equal(p.textContent, "aabbccdd");
  assert.deepEqual(p.childNodes.map((c) => (c instanceof Txt ? c.data : (c as El).tagName + ":" + c.textContent)), ["aa", "A:bb", "c", "B:c", "d", "I:d"]);
});

test("the walk under the viewer's options: inPre reads a code body, unit joins a row's spans so a token the highlight cut through stays text while one held whole in a span links, accept and resolve are the surface's, lineSuffix rides a :12 into the link; without options the chat's walk is unchanged", async () => {
  const { linkifyPathTokens } = await import("./path-links");
  const row = (...kids: Array<El | string>) => el("span", "fv-cl", el("span", "fv-ct", ...kids));
  const c = el("pre", "", el("code", "hljs",
    row("cp ", el("span", "hljs-string", '"', el("span", "hljs-variable", "$HOME"), '/docs/a.md"')),
    row("x = ", el("span", "hljs-string", '"docs/b.md:12"'), " and/or docs/c.md"),
    row(el("span", "hljs-string", '"docs/'), el("span", "hljs-string", 'd.md"')),
  ));
  const seen: string[] = [];
  const hits = linkifyPathTokens(c as unknown as HTMLElement, undefined, {
    inPre: true, unit: ".fv-cl", lineSuffix: true,
    accept: (tok, ctx) => { seen.push(tok + "@" + ctx.at + "/" + ctx.text.length); return tok !== "docs/c.md"; },
    resolve: (tok) => "/tmp/TESTHOST/" + tok,
  });
  assert.deepEqual(seen, ["docs/b.md@5/35", "docs/c.md@26/35"],
    "the gate sees a LINE's token and its offset in the line; the first row's HOME/docs/a.md (what the line says, never the /docs/a.md the substitution's tail alone would read) is cut across two nodes and refused by the span test before any gate; and/or fails the shape gate");
  assert.deepEqual(hits.map((h) => h.open), ["/tmp/TESTHOST/docs/b.md"], "docs/c.md was refused by the surface's gate, the cut docs/d.md by the span test; the one link is resolved by the surface");
  // a substitution that leaves the path's tail whole in its own node: the token is `/notes/a.md`, and the gate is handed the line, where it is glued to the brace
  const glued = el("pre", "", el("code", "hljs", row("path: ", el("span", "hljs-string", el("span", "hljs-variable", "${HOME}"), "/notes/a.md"))));
  const seen2: Array<[string, string]> = [];
  linkifyPathTokens(glued as unknown as HTMLElement, undefined, { inPre: true, unit: ".fv-cl", accept: (tok, ctx) => { seen2.push([tok, ctx.text[ctx.at - 1]]); return false; } });
  assert.deepEqual(seen2, [["/notes/a.md", "}"]], "the surface's gate reads the character before the token off the LINE, not the node");
  const link = c.querySelectorAll(".file-uri-link")[0];
  assert.equal(link.textContent, "docs/b.md:12"); assert.equal(link.dataset.line, "12"); assert.equal(link.title, "Open /tmp/TESTHOST/docs/b.md:12");
  assert.equal(link.parentNode!.textContent, '"docs/b.md:12"', "inside the string span, the quotes outside");
  assert.equal(c.textContent, 'cp "$HOME/docs/a.md"x = "docs/b.md:12" and/or docs/c.md"docs/d.md"');
  // the chat's walk: no options, every node its own unit, <pre> skipped, no line suffix read
  const chat = el("div", "", el("p", "", "see docs/e.md:12 now"), el("pre", "", el("code", "", "docs/f.md")));
  const h2 = linkifyPathTokens(chat as unknown as HTMLElement);
  assert.deepEqual(h2.map((h) => h.open), ["docs/e.md"]);
  assert.deepEqual(textNodesOf(chat.childNodes[0] as El).map((t) => t.data), ["see ", "docs/e.md", ":12 now"], "the :12 stays prose in the chat");
});

test("the chat's fenced blocks (the user 2026-09-12): under inPre + preVerified a fenced token links ONLY on the kernel's verdict and under the surface's code gate; prose in the same body keeps the chat's rules; a fenced hit says so", async () => {
  const { linkifyPathTokens } = await import("./path-links");
  const { viewerPathGate } = await import("./file-view-links");
  const row = (...kids: Array<El | string>) => el("span", "cl", el("span", "ct", ...kids));   // code-block.ts's rows, as the chat's highlight leaves a fence
  const opts = { inPre: true, preVerified: true, unit: ".cl",
    accept: (tok: string, ctx: { text: string; at: number; inPre: boolean }) => !ctx.inPre || viewerPathGate(tok, ctx) };
  const body = () => el("div", "",
    el("p", "", "see docs/e.md and a/dup.md"),
    el("pre", "", el("code", "hljs",
      row("open ", el("span", "hljs-string", "'/tmp/TESTHOST/report/viewer.html'"), " now"),
      row("import fp from ", el("span", "hljs-string", "'lodash/fp.js'")),
      row("cat docs/e.md"))));
  // the kernel's map names the fenced path, the import's package and the prose path: the fenced path and both docs/e.md
  // link; the package stays text under the code gate, verdict or not; a/dup.md (no verdict) stays prose
  const map = { "/tmp/TESTHOST/report/viewer.html": "/tmp/TESTHOST/report/viewer.html", "lodash/fp.js": "node_modules/lodash/fp.js", "docs/e.md": "docs/e.md" };
  const b1 = body();
  assert.deepEqual(linkifyPathTokens(b1 as unknown as HTMLElement, map, opts).map((h) => [h.open, h.verified, h.inPre]),
    [["docs/e.md", true, false], ["/tmp/TESTHOST/report/viewer.html", true, true], ["docs/e.md", true, true]]);
  assert.deepEqual(links(b1).map((a) => a.textContent), ["docs/e.md", "/tmp/TESTHOST/report/viewer.html", "docs/e.md"], "the quotes around the fenced path stay text");
  // no map at all (an old kernel, a cached payload): prose links on shape as before; NOTHING in the fence does
  assert.deepEqual(linkifyPathTokens(body() as unknown as HTMLElement, undefined, opts).map((h) => [h.open, h.inPre]), [["docs/e.md", false], ["a/dup.md", false]]);
  // an empty map is a verdict of none: nothing links anywhere
  assert.deepEqual(linkifyPathTokens(body() as unknown as HTMLElement, {}, opts), []);
  // without the options the fence is dead text, as every other surface walked it before
  assert.deepEqual(linkifyPathTokens(body() as unknown as HTMLElement, map).map((h) => h.open), ["docs/e.md"]);
});
