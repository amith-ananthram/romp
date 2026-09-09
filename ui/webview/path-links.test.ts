// The chat's path matcher, executed (path-links.ts). It lived inline in render.ts, whose only tests are source
// pins; lifted into a module of its own it runs for real over a small DOM stand-in (no jsdom): the walk over a
// message body, the shape gates, the trailing-punctuation trim, the kernel's pathLinks verdict narrowing the
// links, and the span each hit is marked as. What a click does is the hosting document's (render.ts binds
// openPath per span; chat-path-links.test.ts and chat-relpath-link.test.ts pin that wiring at source).
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
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
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
};
(globalThis as any).NodeFilter = { SHOW_ELEMENT: 1, SHOW_TEXT: 4 };
(globalThis as any).document = doc;

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
  // the module binds nothing: no handler on the span (render.ts's bindPathLink adds the click)
  assert.equal((a as any).onclick, undefined);
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
  assert.doesNotMatch(LINKS, /addEventListener|onclick|openPath\(|window\.open|postMessage/, "no action of its own");
  assert.match(LINKS, /export function linkifyPathTokens\(root: HTMLElement, pathLinks\?: Record<string, string>\): PathLinkHit\[\] \{/);
  assert.match(LINKS, /export interface PathLinkHit \{ el: HTMLElement; open: string; verified: boolean \}/);
  assert.match(RENDER, /import \{ openPathLink, linkifyPathTokens \} from "\.\/path-links";/);
  assert.match(RENDER, /function bindPathLink\(a: HTMLElement\): HTMLElement \{\n\s*const open = a\.dataset\.path \|\| "", relative = a\.dataset\.rel === "1";\n\s*a\.addEventListener\("click", \(e\) => \{\n\s*e\.stopPropagation\(\);\n\s*openPath\(open, relative \? activeId : null, e\);\n\s*\}\);\n\s*onMiddleClick\(a, \(e\) => openPath\(open, relative \? activeId : null, e\)\);\n\s*return a;\n\}/);
  assert.match(RENDER, /const link = bindPathLink\(openPathLink\(tok, tok, true\)\);\n\s*code\.replaceChildren\(link\);/, "the kernel-verified spaced span takes the same binder");
  assert.match(RENDER, /for \(const \{ el: link, open, verified \} of linkifyPathTokens\(root, pathLinks\)\) \{\n\s*bindPathLink\(link\);\n\s*if \(verified\) kernelVerified\.add\(open\);/);
  // the matcher lives in ONE place: render.ts no longer declares the regex or its gates
  for (const name of ["CLICKABLE_PATH_RE", "function looksLikeFilePath", "function looksLikeBareFileName", "const BARE_FILE_EXTS", "function fileUriToPath", "function openPathLink", "function fileUriLink"]) {
    assert.ok(!RENDER.includes(name), name + " is path-links.ts's alone");
    assert.ok(LINKS.includes(name), name + " in path-links.ts");
  }
});
