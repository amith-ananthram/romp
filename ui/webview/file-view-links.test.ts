// Links inside a file the viewer shows (file-view-links.ts): URLs become anchors that open a new tab, paths become
// the chat's path links resolved against the shown file, a Markdown link's target follows the same two rules, and the
// text of every row reads exactly as the file's after the pass. The grammar and the pass run for real over a small DOM
// stand-in (the path-links.test.ts idiom, grown to what the pass touches: elements with attributes, a selector engine
// for closest and querySelectorAll, fragments). The viewer's wiring (where the pass runs, the body's listener and its
// gesture, the line scroll, the sheets) is pinned at source; the browser leg, where the sanitizer, the highlighter and
// the pointer are real, is file-view-links-browser.test.ts. Synthetic fixtures only: the notes-api world under
// /tmp/TESTHOST, a placeholder session id, example.invalid addresses.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const VIEW = read("file-view.ts");
const LINKS = read("path-links.ts");
const MOD = read("file-view-links.ts");
const RENDER = read("render.ts");
const PREVIEW = read("preview.ts");
const CHAT_CSS = read("styles.css");
const FEED_CSS = read("feed.css");

const SID = "11111111-2222-3333-4444-555555555555";
const FILE = "/tmp/TESTHOST/notes-api/src/app.py";
const DIR = "/tmp/TESTHOST/notes-api/src/";

// ── a DOM stand-in: text nodes, elements with attributes, a selector engine, fragments ────────────
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
  get ownerDocument(): typeof doc { return doc; }
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
  onkeydown: unknown = null; onmousedown: unknown = null; onmouseup: unknown = null; onmouseleave: unknown = null; oncontextmenu: unknown = null; ondragstart: unknown = null;
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
  get ownerDocument(): typeof doc { return doc; }
  get parentElement(): El | null { return this.parentNode; }
  // reflected attributes, as the DOM has them
  get className(): string { return this.attrs.get("class") || ""; } set className(v: string) { this.attrs.set("class", v); }
  get title(): string { return this.attrs.get("title") || ""; } set title(v: string) { this.attrs.set("title", v); }
  get href(): string { return this.attrs.get("href") || ""; } set href(v: string) { this.attrs.set("href", v); }
  get target(): string { return this.attrs.get("target") || ""; } set target(v: string) { this.attrs.set("target", v); }
  get rel(): string { return this.attrs.get("rel") || ""; } set rel(v: string) { this.attrs.set("rel", v); }
  get tabIndex(): number { return this.attrs.has("tabindex") ? Number(this.attrs.get("tabindex")) : -1; } set tabIndex(v: number) { this.attrs.set("tabindex", String(v)); }
  get classes(): string[] { return this.className.split(/\s+/).filter(Boolean); }
  classList = { add: (...c: string[]) => { const s = new Set(this.classes); for (const x of c) s.add(x); this.className = [...s].join(" "); }, contains: (c: string) => this.classes.includes(c) };
  dataset: Record<string, string> = new Proxy({} as Record<string, string>, {
    get: (_, k) => this.attrs.get("data-" + kebab(String(k))) as string,
    set: (_, k, v) => { this.attrs.set("data-" + kebab(String(k)), String(v)); return true; },
    has: (_, k) => this.attrs.has("data-" + kebab(String(k))),
  });
  get textContent(): string { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v: string) { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; if (v !== "") this.appendChild(new Txt(v)); }
  private detach(n: El | Txt): void { const p = n.parentNode; if (p) { const i = p.childNodes.indexOf(n); if (i >= 0) p.childNodes.splice(i, 1); n.parentNode = null; } }
  appendChild<T extends El | Txt>(n: T): T { this.detach(n); this.childNodes.push(n); n.parentNode = this; return n; }
  insertBefore<T extends El | Txt>(n: T, ref: El | Txt | null): T {
    if (!ref) return this.appendChild(n);
    this.detach(n);
    const i = this.childNodes.indexOf(ref);
    this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, n); n.parentNode = this; return n;
  }
  removeChild<T extends El | Txt>(n: T): T { this.detach(n); return n; }
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  getAttribute(k: string): string | null { return this.attrs.has(k) ? (this.attrs.get(k) as string) : null; }
  hasAttribute(k: string): boolean { return this.attrs.has(k); }
  removeAttribute(k: string): void { this.attrs.delete(k); }
  contains(n: El | Txt | null): boolean { for (let x: El | Txt | null = n; x; x = x.parentNode) if (x === this) return true; return false; }
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
  querySelector(sel: string): El | null { return this.querySelectorAll(sel)[0] || null; }
}
function textNodesOf(root: El): Txt[] {
  const out: Txt[] = [];
  const walk = (n: El) => { for (const c of n.childNodes) { if (c instanceof Txt) out.push(c); else walk(c); } };
  walk(root);
  return out;
}
/** Document-order nodes under `root`, as a browser's tree walker answers: the text nodes, and the elements too when
 *  SHOW_ELEMENT was asked for (textUnits asks, to see a <br> between two text nodes). */
function walkNodes(root: El, what: number): Array<El | Txt> {
  const out: Array<El | Txt> = [];
  const walk = (n: El) => { for (const c of n.childNodes) { if (c instanceof Txt) { if (what & 4) out.push(c); } else { if (what & 1) out.push(c); walk(c); } } };
  walk(root);
  return out;
}
const doc = {
  createElement: (tag: string) => new El(tag),
  createTextNode: (s: string) => new Txt(s),
  createDocumentFragment: () => new Frag(),
  createTreeWalker: (root: El, what = 4) => { const nodes = walkNodes(root, what); let i = 0; return { nextNode: () => (i < nodes.length ? nodes[i++] : null) }; },
  activeElement: null as El | null,
};
(globalThis as any).NodeFilter = { SHOW_ELEMENT: 1, SHOW_TEXT: 4 };
(globalThis as any).document = doc;

// ── builders: what codeBlock's DOM looks like (one .fv-cl row per line, hljs spans inside) ─────────
const el = (tag: string, cls?: string, ...kids: Array<El | Txt | string>): El => {
  const e = new El(tag); if (cls) e.className = cls;
  for (const k of kids) e.appendChild(typeof k === "string" ? new Txt(k) : k);
  return e;
};
const row = (...kids: Array<El | Txt | string>): El => el("span", "fv-cl", el("span", "fv-ct", ...kids));
const code = (...rows: El[]): El => el("code", "hljs", ...rows);
const links = (root: El) => root.querySelectorAll(".file-uri-link");
const urls = (root: El) => root.querySelectorAll("a.fv-url");

// ── the URL grammar ───────────────────────────────────────────────────────────────────────────────
test("urlSegments: http and https URLs, trailing sentence punctuation left out, a paren balanced inside kept, one closing the sentence's not", async () => {
  const { urlSegments } = await import("./file-view-links");
  const hrefs = (s: string) => urlSegments(s).filter((x) => x.href).map((x) => x.href);
  assert.deepEqual(hrefs("see https://example.invalid/a/b?x=1&y=2 now"), ["https://example.invalid/a/b?x=1&y=2"]);
  assert.deepEqual(hrefs("plain http://example.invalid:8080/p too"), ["http://example.invalid:8080/p"]);
  assert.deepEqual(hrefs("HTTPS://EXAMPLE.invalid/X"), ["HTTPS://EXAMPLE.invalid/X"], "the scheme's case is the browser's business");
  assert.deepEqual(hrefs("ends https://example.invalid/a."), ["https://example.invalid/a"]);
  assert.deepEqual(hrefs("ends https://example.invalid/a, then"), ["https://example.invalid/a"]);
  assert.deepEqual(hrefs("ends https://example.invalid/a?q=1!?"), ["https://example.invalid/a?q=1"]);
  assert.deepEqual(hrefs("(see https://example.invalid/a/b)"), ["https://example.invalid/a/b"], "the sentence's paren");
  assert.deepEqual(hrefs("(see https://example.invalid/a/b)."), ["https://example.invalid/a/b"]);
  assert.deepEqual(hrefs("https://example.invalid/wiki/Foo_(bar) x"), ["https://example.invalid/wiki/Foo_(bar)"], "a paren opened inside the URL closes inside it");
  assert.deepEqual(hrefs("[https://example.invalid/a]"), ["https://example.invalid/a"]);
  assert.deepEqual(hrefs("<https://example.invalid/a>"), ["https://example.invalid/a"], "an autolink's angle brackets");
  assert.deepEqual(hrefs('url = "https://example.invalid/api/v1"'), ["https://example.invalid/api/v1"], "a quoted string in code");
  assert.deepEqual(hrefs("url = 'https://example.invalid/x'; y = `https://example.invalid/z`"), ["https://example.invalid/x", "https://example.invalid/z"]);
  // not URLs: a bare scheme, a scheme with no host, other schemes, a hostname alone
  for (const s of ["https://", "http:// x", "https:///nohost", "ftp://example.invalid/a", "example.invalid/a/b.html", "file:///tmp/TESTHOST/a.md"]) {
    assert.deepEqual(hrefs(s), [], s);
  }
  // the runs reassemble to the text, always
  for (const s of ["a https://example.invalid/x) b http://y.invalid/z. c", "no urls here", "", "https://example.invalid/(a)(b))"]) {
    assert.equal(urlSegments(s).map((x) => x.text).join(""), s, JSON.stringify(s));
  }
  assert.deepEqual(hrefs("https://example.invalid/(a)(b))"), ["https://example.invalid/(a)(b)"], "two balanced pairs kept, the extra closer left");
});

test("urlRanges: the [start, end) of each URL urlSegments would wrap, trailing punctuation left out; none in text without a scheme", async () => {
  const { urlRanges, urlSegments } = await import("./file-view-links");
  const t = "see https://example.invalid/a, then (https://example.invalid/b) and https://";
  assert.deepEqual(urlRanges(t), [[4, 29], [37, 62]]);
  for (const [s, e] of urlRanges(t)) assert.ok(urlSegments(t).some((x) => x.href === t.slice(s, e)), t.slice(s, e));
  assert.deepEqual(urlRanges("docs/a.md and x=/docs/b.md"), []);
});

// ── the path gate and the resolution ─────────────────────────────────────────────────────────────
test("viewerPathGate, the token alone: a slash and a letter-led extension on the last segment; anchored dotfiles; a hostname-shaped first segment and a `~user` home are refused", async () => {
  const { viewerPathGate } = await import("./file-view-links");
  for (const p of ["docs/a.md", "ui/webview/x.ts", "/tmp/TESTHOST/notes-api/a.py", "~/notes/plan.md", "./x.py", "../y.ts", "~/.zshrc", "./.env",
                   "file:///tmp/TESTHOST/a.pdf", "file://localhost/tmp/TESTHOST/a.pdf", "a/b.h5", "img/photo.jpeg", "data/out.tar.gz", "src/mod.d.ts",
                   "a.b/c.md", "notes.d/x.md"]) {
    assert.equal(viewerPathGate(p), true, p);
  }
  for (const p of ["./foo", "/api/users", "/usr/bin", "~/code", "1/2.5", "and/or", "TCP/IP", "config/.env", "kernel.py", "a/b.123",
                   "img/photo.jpeg2000x", "x/y", "react/jsx-runtime", "a/b.", "../", "./",
                   "www.example.org/docs/index.html", "WWW.example.org/x.html", "example.com/index.html", "docs.example.invalid/a.md", "sub.example.co.uk/a/b.md",
                   "~user/x.md", "~user/.zshrc"]) {
    assert.equal(viewerPathGate(p), false, p);
  }
});

test("viewerPathGate, with the line: a token glued to what stands before it (a substitution, a scope, a drive, a host) is not one; an unanchored token named by an import statement or a require call is not one, while the English from and export in prose name files; an opener (a quote, a bracket, whitespace of every kind, Markdown's * closed by a * after the path) or the line's start admits it; a glob's tail after a star, an operand after one and a hand-split import's specifier are refused", async () => {
  const { viewerPathGate } = await import("./file-view-links");
  const at = (text: string, tok: string) => ({ text, at: text.indexOf(tok) });
  for (const [text, tok] of [
    ['cp "$HOME/docs/a.md" ./out/', "HOME/docs/a.md"], ["cat ${ROOT}/src/x.py", "/src/x.py"], ["SRC = $(ROOT)/src/main.c", "/src/main.c"],
    ["const s = `${dir}/out.json`;", "/out.json"], ['f"{base}/out.csv"', "/out.csv"], ["path: ${HOME}/notes/a.md", "/notes/a.md"],
    ['import x from "@scope/pkg/dist/index.js";', "scope/pkg/dist/index.js"], ["C:/Users/x/file.txt", "/Users/x/file.txt"],
    ["git clone git@github.invalid:user/repo.git", "user/repo.git"], ["a%s/x.md", "s/x.md"], ["x#/docs/a.md", "/docs/a.md"],
    ['import b from "lodash/fp.js";', "lodash/fp.js"], ['const x = require("pkg/sub.js");', "pkg/sub.js"], ['export * from "pkg/sub.js";', "pkg/sub.js"],
    ['import "pkg/x.css";', "pkg/x.css"], ["const m = await import('pkg/m.mjs');", "pkg/m.mjs"],
    ['x = 1\nimport b from "lodash/fp.js";', "lodash/fp.js"], ['a\n  const m = require(  "pkg/m.js");', "pkg/m.js"], ['a\nexport * from\t"pkg/x.js";', "pkg/x.js"],
    ['$import("pkg/y.js")', "pkg/y.js"],   // a non-word character before the keyword is a word boundary
    // the statement forms: a keyword starting its line, a `from` whose line began with import or export, the `}` line of a multi-line import
    ['  import "pkg/x.css";', "pkg/x.css"], ['import type { X } from "pkg/t.js";', "pkg/t.js"], ['export { a as b } from "pkg/x.js";', "pkg/x.js"],
    ['} from "pkg/x.js";', "pkg/x.js"], ['import {\n  a,\n} from "pkg/x.js";', "pkg/x.js"], ['x = 1\n  } from "pkg/x.js";', "pkg/x.js"], ['require "pkg/x.rb"', "pkg/x.rb"],
    // a multi-line import whose closing line carries names, or whose `from` starts a continuation line
    ['import {\n  a, b } from "pkg/x.js";', "pkg/x.js"], ['import { a }\n  from "pkg/x.js";', "pkg/x.js"], ['export {\n  a,\n  b } from "pkg/x.js";', "pkg/x.js"],
    ['import type {\n  T } from "pkg/t.js";', "pkg/t.js"], ['x = 1\nimport {\n  a,\n  b as c,\n  d } from "pkg/x.js";', "pkg/x.js"],
    // a hand-split import whose `from` starts the next line, under a namespace, a default name alone, a star or a type list: the line is `from` and a specifier
    ['import * as ns\n  from "pkg/ns.js";', "pkg/ns.js"], ['import React\n  from "react/def.js";', "react/def.js"], ['export *\n  from "pkg/star.js";', "pkg/star.js"], ['export type { T }\n  from "pkg/t.js"', "pkg/t.js"],
    ["import * as ns\n  from 'pkg/ns.js'", "pkg/ns.js"], ['Copied\nthe text\nfrom "docs/a.md"', "docs/a.md"],   // the shape alone decides, so a prose line of exactly `from "…"` is the rule's one price
    // a glob's tail after a star, and an operand after one: `*` opens a token only when it is not `/`-led and a closing `*` follows
    ["find . -path '**/docs/a.md'", "/docs/a.md"], ["cp src/**/index.ts out/", "/index.ts"], ["ls packages/*/package.json", "/package.json"], ['"**/tsconfig.json"', "/tsconfig.json"],
    ["x = '*/docs/a.md*'", "/docs/a.md"], ["area = w*h/img.size", "h/img.size"], ["echo 2*docs/times.md", "docs/times.md"], ["*docs/a.md", "docs/a.md"], ["a*docs/b.md, c", "docs/b.md"],
    ["**/etc/hosts.txt**", "/etc/hosts.txt"],   // a `/`-led token after a star is a glob's tail whatever closes it
    // emphasis that holds more than the path: the path glued to the opening star has no closing star of its own (a known limit)
    ["**docs/a.md and docs/b.md**", "docs/a.md"], ["*docs/c.md (old)*", "docs/c.md"], ["*docs/a.md.*", "docs/a.md"],
  ] as const) {
    assert.equal(viewerPathGate(tok, at(text, tok)), false, text);
  }
  // glue the openers do not admit: a closing paren, a colon, a hash (the shapes above: `$(ROOT)/src/x.c`, `git@host:user/repo.git`, `x#/docs/a.md`)
  for (const [text, tok] of [[")docs/a.md", "docs/a.md"], ["x:docs/a.md", "docs/a.md"], ["#docs/a.md", "docs/a.md"], ["]docs/a.md", "docs/a.md"]] as const) {
    assert.equal(viewerPathGate(tok, at(text, tok)), false, JSON.stringify(text));
  }
  for (const [text, tok] of [
    ['cp "docs/a.md" ./out/', "docs/a.md"], ["see docs/a.md here", "docs/a.md"], ["docs/a.md", "docs/a.md"], ["x=/docs/a.md", "/docs/a.md"],
    ["(see /docs/a.md)", "/docs/a.md"], ["[docs/a.md]", "docs/a.md"], ['<img src="docs/a.png">', "docs/a.png"], ["a, docs/a.md", "docs/a.md"],
    ["\tdocs/a.md", "docs/a.md"], ["a|docs/a.md", "docs/a.md"], ["{docs/a.md}", "docs/a.md"],
    ['import s from "./app.css";', "./app.css"], ['import b from "../lib/util.js";', "../lib/util.js"], ['<script src="lib/x.js">', "lib/x.js"],
    ["open(~/notes/a.md)", "~/notes/a.md"], ["readme = 'file:///tmp/TESTHOST/README.md'", "file:///tmp/TESTHOST/README.md"],
    ["See the plan in\ndocs/plan.md", "docs/plan.md"], ["x = 1\ndocs/a.md", "docs/a.md"], ["a\r\ndocs/a.md", "docs/a.md"], ["then\u00a0docs/a.md", "docs/a.md"],   // a line break or a no-break space before it is whitespace
    ['reimport("pkg/x.js")', "pkg/x.js"],                        // no word boundary before `import`: not the keyword
    ['import b from\n"lodash/fp.js";', "lodash/fp.js"],          // the quote starts a new line: the look-behind is the line's, and no formatter writes this
    // the English words: a quote from a file, a stale export, a `require` mid-sentence, a `from` that starts a line with no import before it
    ['a quote from "docs/from.md" says', "docs/from.md"], ['the export "out/data.json" is stale', "out/data.json"], ['they require "docs/a.md" to exist', "docs/a.md"],
    ['from "pkg/sub.py" import x', "pkg/sub.py"], ['Copied the text\nfrom "docs/a.md" and kept it', "docs/a.md"], ['const x = 1; import y from "pkg/x.js"', "pkg/x.js"],
    // Markdown emphasis in the Raw view, and the spaces \s names beyond the ASCII ones, plus the zero-width space
    ["*docs/a.md*", "docs/a.md"], ["**docs/a.md**", "docs/a.md"], ["*docs/a.md:12* then", "docs/a.md"], ["see **docs/a.md**.", "docs/a.md"],
    // …around an anchored path too: no glob puts `./`, `../` or `~/` after its star
    ["**./scripts/setup.sh** runs first", "./scripts/setup.sh"], ["then *../src/app.py* is read", "../src/app.py"], ["*~/notes/a.md*", "~/notes/a.md"], ["**./docs/a.md:12**", "./docs/a.md"],
    // …and the second path of a span that holds two, opened by the space before it
    ["**docs/a.md and docs/b.md**", "docs/b.md"],
    // a whole statement above an English from in one unit (a fenced block): Python's import, a shell's export, a finished export
    ['import os\n# adapted from "docs/a.md"', "docs/a.md"], ['export DATA=/data\n# copied from "docs/c.md"', "docs/c.md"], ['export const z = 2;\n// from "docs/d.md"', "docs/d.md"],
    ['export function build() {\n  // ported from "docs/algo.md"', "docs/algo.md"], ['import numpy as np\nimport os\n# data from "data/raw.csv"', "data/raw.csv"],
    // the English from with more after its path: Python's, a sentence's
    ['x = 1\n\nfrom "pkg/sub.py" import y', "pkg/sub.py"], ['import "x.css";\nfrom "docs/a.md" we copied', "docs/a.md"], ['Copied\nthe text\nfrom "docs/a.md" too', "docs/a.md"],
    ["\u200bdocs/a.md", "docs/a.md"], ["\fdocs/a.md", "docs/a.md"], ["\u2003docs/a.md", "docs/a.md"], ["\u2028docs/a.md", "docs/a.md"], ["\ufeffdocs/a.md", "docs/a.md"], ["\u3000docs/a.md", "docs/a.md"],
  ] as const) {
    assert.equal(viewerPathGate(tok, at(text, tok)), true, JSON.stringify(text));
  }
  // `_` is not an opener: the matcher's path arm takes an underscore into the token, so the gate never sees one before a token.
  // `_docs/a.md_` is one token whose extension (`md_`) fails, and `_docs/a.md` names a folder called `_docs`
  const { CLICKABLE_PATH_RE } = await import("./path-links");
  assert.deepEqual("_docs/a.md_ and *docs/b.md*".match(CLICKABLE_PATH_RE), ["_docs/a.md_", "docs/b.md"], "the scanner's tokens");
  assert.equal(viewerPathGate("_docs/a.md_"), false); assert.equal(viewerPathGate("_docs/a.md"), true);
  assert.equal(viewerPathGate("docs/a.md", at("_docs/a.md_", "docs/a.md")), false, "and at the offset the walk never produces, the underscore is glue");
  // a code view's rows are units of their own, and the `from` line's own shape decides: `from` and a quoted specifier alone
  // (a `;` or not, either quote), or `} from "` with no quote before the brace; nothing above the line is read
  for (const [text, tok] of [
    ['  a, b } from "pkg/x.js";', "pkg/x.js"], ['  from "pkg/x.js";', "pkg/x.js"], ['  d } from "pkg/x.js";', "pkg/x.js"], ['} from "pkg/x.js";', "pkg/x.js"],
    ['  { a, b } from "pkg/x.js";', "pkg/x.js"], ['  b } from "pkg/x.js";', "pkg/x.js"], ['  from "pkg/t.js";', "pkg/t.js"], ["  from 'pkg/t.js'", "pkg/t.js"], ["from\t'pkg/t.js' ;", "pkg/t.js"],
    ["  h } from 'pkg/gap.js';", "pkg/gap.js"], ['  from "docs/a.md"', "docs/a.md"],   // wherever it stands: under a blank row, under nothing, in prose
  ] as const) {
    assert.equal(viewerPathGate(tok, at(text, tok)), false, text);
  }
  for (const [text, tok] of [
    ['  from "docs/a.md" and kept it', "docs/a.md"], ['# adapted from "docs/a.md"', "docs/a.md"], ['# data from "data/raw.csv"', "data/raw.csv"], ['# copied from "docs/c.md"', "docs/c.md"],
    ['// see from "docs/a.md"', "docs/a.md"], ['  // ported from "docs/algo.md"', "docs/algo.md"], ['# see from "docs/c.md"', "docs/c.md"], ['from "pkg/sub.py" import x', "pkg/sub.py"],
    ['x = "}"; } from "docs/a.md"', "docs/a.md"],   // a quote before the brace: not an import's closing line
  ] as const) {
    assert.equal(viewerPathGate(tok, at(text, tok)), true, text);
  }
  // …and through the real walk over rows: the fixture's code view (a .fv-cl per line), the pass over the whole code element
  const { linkifyFileText } = await import("./file-view-links");
  const c = el("code", "hljs", el("span", "fv-cl", "import {"), el("span", "fv-cl", '  a, b } from "pkg/x.js";'), el("span", "fv-cl", "import { g }"), el("span", "fv-cl", '  from "pkg/y.js";'), el("span", "fv-cl", 'x = "docs/a.md"'));
  linkifyFileText(c as unknown as HTMLElement, FILE);
  assert.deepEqual(links(c).map((l) => l.textContent), ["docs/a.md"], "the two hand-split imports' specifiers stay text over rows, the quoted path links");
  // …and over codeBlock's rows with BLANK rows among them (a .fv-cl whose .fv-ct holds no text node): each blank row is an
  // empty unit to the walk, in its place; a whole statement above an English from opens nothing, and a hand-split import's
  // specifier stays text past a blank row too
  const { textUnits } = await import("./path-links");
  const rowsWithBlanks = el("code", "hljs", row("import json"), row(), row('# see from "docs/c.md"'), row("import os"), row("import sys"), row('# adapted from "docs/a.md"'),
    row("export DATA=/data"), row('# copied from "docs/b.md"'), row("import {"), row(), row('  a, b } from "pkg/gap.js";'), row("import {"), row('  a, b } from "pkg/x.js";'),
    row("export const z = 2;"), row('// from "docs/d.md"'));
  assert.deepEqual(textUnits(rowsWithBlanks as unknown as HTMLElement, ".fv-cl", "a").map((u) => u.text).slice(0, 3), ["import json", "", '# see from "docs/c.md"'], "the blank row is an empty unit in its place");
  linkifyFileText(rowsWithBlanks as unknown as HTMLElement, FILE);
  assert.deepEqual(links(rowsWithBlanks).map((l) => l.textContent), ["docs/c.md", "docs/a.md", "docs/b.md", "docs/d.md"],
    "the prose paths link; both hand-split imports' specifiers stay text, a blank row between `import {` and its `} from` or not");
  assert.equal(rowsWithBlanks.querySelectorAll(".fv-cl").length, 15, "no row added or lost");
});

test("the gate's look-behind is the current line's and bounded by what stands right before the token; an 8000-line fence links every line's path, the look-behinds summed reading less than the text once", async () => {
  const { lookBehindStart, viewerPathGate, resolveViewerPath, LINE_UNITS } = await import("./file-view-links");
  const { linkifyPathTokens } = await import("./path-links");
  const lineStart = (text: string, at: number) => text.lastIndexOf("\n", at - 1) + 1;
  for (const [text, tok] of [
    ['import b from "lodash/fp.js";', "lodash/fp.js"], ['x = 1\nimport b from "lodash/fp.js";', "lodash/fp.js"], ['a\n  const m = require(  "pkg/m.js");', "pkg/m.js"],
    ['import b from\n"lodash/fp.js";', "lodash/fp.js"], ['reimport("pkg/x.js")', "pkg/x.js"], ["x = 1\n" + " ".repeat(500) + '"docs/a.md"', "docs/a.md"],
    ["docs/a.md", "docs/a.md"], ["\ndocs/a.md", "docs/a.md"], ['see "docs/a.md"', "docs/a.md"], ["x".repeat(3000) + '\n"docs/a.md"', "docs/a.md"],
    ["x".repeat(3000) + '\na quote from "docs/a.md"', "docs/a.md"],   // a keyword: the line is read to its start (the head decides), and the line before it is not
  ] as const) {
    const at = text.lastIndexOf(tok);
    const start = lookBehindStart(text, at);
    assert.ok(start >= lineStart(text, at) && start <= at, "the window [" + start + ", " + at + ") lies inside the line starting at " + lineStart(text, at) + ": " + JSON.stringify(text.slice(-60)));
    assert.ok(at - start <= 2 + 500 + 1 + 500 + 7, "bounded by the quote, the spaces, the paren and a keyword's width");
  }
  // one unit of 8000 lines (a fenced block, or the Raw view's one <code>), a path on each line in three shapes
  const lines: string[] = [];
  for (let i = 0; i < 8000; i++) lines.push(i % 3 === 0 ? "docs/f" + i + ".md" : i % 3 === 1 ? 'x = "docs/g' + i + '.md"' : 'y = require("./docs/h' + i + '.md")');
  const text = lines.join("\n") + "\n";
  const pre = el("pre", "", el("code", "language-python hljs", text));
  let read = 0, tokens = 0;
  linkifyPathTokens(pre as unknown as HTMLElement, undefined, {
    inPre: true, lineSuffix: true, unit: LINE_UNITS, resolve: (tok) => resolveViewerPath(tok, FILE),
    accept: (tok, ctx) => { tokens++; read += ctx.at - lookBehindStart(ctx.text, ctx.at); return viewerPathGate(tok, ctx); },
  });
  assert.equal(tokens, 8000, "every line's path reached the gate");
  assert.equal(links(pre).length, 8000, "and linked: a path starting a line, a quoted one, an anchored one in a require");
  assert.ok(read < text.length, "the look-behinds read " + read + " characters in all, over a text of " + text.length);
  assert.equal(pre.textContent, text);
  assert.equal(links(pre)[3].dataset.path, DIR + "docs/f3.md"); assert.equal(links(pre)[5].dataset.path, DIR + "docs/h5.md");
});

test("normalizePath and resolveViewerPath: `.` and `..` are resolved once, here; relative tokens join the shown file's directory; absolute, ~/ and local file:// pass through; a file with no directory joins nothing", async () => {
  const { normalizePath, resolveViewerPath } = await import("./file-view-links");
  for (const [p, want] of [["/a/b/../c.md", "/a/c.md"], ["/../x.md", "/x.md"], ["/a/./b//c.md", "/a/b/c.md"], ["~/a/../b.md", "~/b.md"], ["~/../x.md", "~/../x.md"],
                           ["docs/../src/app.py", "src/app.py"], ["../../x.md", "../../x.md"], ["a/../../x.md", "../x.md"], ["./x.py", "x.py"], ["a/b/", "a/b/"], ["/", "/"],
                           ["docs/a.md", "docs/a.md"], ["/tmp/TESTHOST/x.md", "/tmp/TESTHOST/x.md"], ["", ""]] as const) {
    assert.equal(normalizePath(p), want, p);
  }
  assert.equal(resolveViewerPath("docs/a.md", FILE), DIR + "docs/a.md");
  assert.equal(resolveViewerPath("./x.py", FILE), DIR + "x.py", "./ is resolved here");
  assert.equal(resolveViewerPath("../y.ts", FILE), "/tmp/TESTHOST/notes-api/y.ts", ".. is resolved here: the title bar and the folder link see one spelling");
  assert.equal(resolveViewerPath("/tmp/TESTHOST/other/../z.md", FILE), "/tmp/TESTHOST/z.md");
  assert.equal(resolveViewerPath("~/notes/plan.md", FILE), "~/notes/plan.md", "the kernel expands ~ on the session's machine");
  assert.equal(resolveViewerPath("file:///tmp/TESTHOST/a%20b.pdf", FILE), "/tmp/TESTHOST/a b.pdf", "a URI's own path, percent-decoded");
  assert.equal(resolveViewerPath("file://localhost/tmp/TESTHOST/a.pdf", FILE), "/tmp/TESTHOST/a.pdf", "localhost is this machine");
  assert.equal(resolveViewerPath("docs/a.md", "README.md"), "docs/a.md", "a file named without a directory: the token as written, for the session's cwd");
  assert.equal(resolveViewerPath("a.md", "docs/README.md"), "docs/a.md", "a relative shown file keeps its relative directory");
  assert.equal(resolveViewerPath("../a.md", "docs/README.md"), "a.md");
});

test("a file:// URI is a path only with an empty authority or localhost: file://host/path names another machine and is prose to the walk; a line after a URI is the line, not the path", async () => {
  const { isFileUri, fileUriToPath } = await import("./path-links");
  for (const u of ["file:///tmp/TESTHOST/a.md", "file://localhost/tmp/a.md", "FILE:///tmp/a.md", "file://LOCALHOST/x"]) assert.equal(isFileUri(u), true, u);
  for (const u of ["file://evil.invalid/share/x.md", "file://TESTHOST/x.md", "file://localhost", "file:/x.md", "files:///x", "file://localhostx/y"]) assert.equal(isFileUri(u), false, u);
  assert.equal(fileUriToPath("file://localhost/tmp/a%20b.md"), "/tmp/a b.md");
  assert.equal(fileUriToPath("file://evil.invalid/share/x.md"), "file://evil.invalid/share/x.md", "not a local path: returned as written, never as a relative path");
  const { linkifyFileText } = await import("./file-view-links");
  const c = code(row('y = "file://evil.invalid/share/x.md"'), row("z = 'file:///tmp/TESTHOST/x.md:12'"), row("w = 'file://localhost/tmp/TESTHOST/y.md#L7'"), row("v = file:///tmp/TESTHOST/z.md:3:4 end"),
    row("u = 'file:///tmp/TESTHOST/x/../y.md'"));
  const before = c.childNodes.map((r) => r.textContent);
  linkifyFileText(c as unknown as HTMLElement, FILE);
  assert.deepEqual(c.childNodes.map((r) => r.textContent), before);
  assert.deepEqual(links(c).map((x) => [x.textContent, x.dataset.path, x.dataset.line, x.title]), [
    ["file:///tmp/TESTHOST/x.md:12", "/tmp/TESTHOST/x.md", "12", "Open /tmp/TESTHOST/x.md:12"],
    ["file://localhost/tmp/TESTHOST/y.md#L7", "/tmp/TESTHOST/y.md", "7", "Open /tmp/TESTHOST/y.md:7"],
    ["file:///tmp/TESTHOST/z.md:3:4", "/tmp/TESTHOST/z.md", "3", "Open /tmp/TESTHOST/z.md:3"],
    ["file:///tmp/TESTHOST/x/../y.md", "/tmp/TESTHOST/y.md", undefined, "Open /tmp/TESTHOST/y.md"],
  ], "the host-bearing URI stays text; the line rides in the link and off the path; a URI's path is normalized once, as a written path is (the viewer's resolve)");
});

// ── the pass over a code body ─────────────────────────────────────────────────────────────────────
test("the pass, executed over codeBlock's rows: URLs in a comment become anchors, quoted paths become path links resolved against the file, :line rides in, prose and identifiers stay", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const c = code(
    row(el("span", "hljs-comment", "# see https://example.invalid/docs/setup.html, then docs/guide.md:12.")),
    row("cfg = open(", el("span", "hljs-string", '"data/config.json"'), ")"),
    row("from . import util  # and/or 24/7 x = 1/2.5 /api/users ./foo"),
    row("readme = ", el("span", "hljs-string", "'file:///tmp/TESTHOST/notes-api/README.md'")),
    row("see ", el("span", "hljs-string", '"ui/x.ts#L7"'), " and ", el("span", "hljs-string", '"ui/y.ts:12abc"')),
  );
  const before = c.childNodes.map((r) => r.textContent);
  linkifyFileText(c as unknown as HTMLElement, FILE);
  // every row's text reads exactly as before: the pass adds elements around text and changes no character
  assert.deepEqual(c.childNodes.map((r) => r.textContent), before);
  // the URL: an anchor that opens a new tab, its text the URL without the sentence's comma, not draggable (a drag on it selects)
  const a = urls(c);
  assert.equal(a.length, 1);
  assert.equal(a[0].textContent, "https://example.invalid/docs/setup.html");
  assert.equal(a[0].href, "https://example.invalid/docs/setup.html");
  assert.equal(a[0].target, "_blank"); assert.equal(a[0].rel, "noopener noreferrer");
  assert.equal(a[0].title, "https://example.invalid/docs/setup.html");
  assert.equal(a[0].getAttribute("draggable"), "false");
  assert.equal(a[0].closest(".hljs-comment")!.className, "hljs-comment", "inside the highlight's own span");
  assert.equal(links(a[0]).length, 0, "the URL's path-shaped tail is not a path link inside the anchor");
  // the paths: the chat's span, the resolved target, the line
  const l = links(c);
  assert.deepEqual(l.map((x) => x.textContent), ["docs/guide.md:12", "data/config.json", "file:///tmp/TESTHOST/notes-api/README.md", "ui/x.ts#L7", "ui/y.ts"]);
  assert.deepEqual(l.map((x) => x.dataset.path), [DIR + "docs/guide.md", DIR + "data/config.json", "/tmp/TESTHOST/notes-api/README.md", DIR + "ui/x.ts", DIR + "ui/y.ts"]);
  assert.deepEqual(l.map((x) => x.dataset.line), ["12", undefined, undefined, "7", undefined]);
  assert.equal(l[0].title, "Open " + DIR + "docs/guide.md:12");
  assert.equal(l[1].title, "Open " + DIR + "data/config.json");
  for (const x of l) { assert.equal(x.role, "link"); assert.equal(x.tabIndex, 0); assert.equal(typeof x.onmousedown, "function"); assert.equal(typeof x.onkeydown, "function"); }
  assert.equal(l[1].parentNode!.textContent, '"data/config.json"', "the string's quotes stay outside the link, as text");
  // `.` after :12 is the sentence's, left as text; `:12abc` is not a line, so ui/y.ts links alone and `:12abc"` stays text
  assert.equal(textNodesOf(c.childNodes[0] as El).map((t) => t.data).pop(), ".");
  assert.equal(textNodesOf(c.childNodes[4] as El).map((t) => t.data).pop(), ':12abc"');
});

test("the pass reads a line, not a node: a highlight's substitution span cannot turn a path's tail into an absolute link, a token the highlight cut through is left as it is, and so is a URL, whose path-shaped query value is the URL's and never a link", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  // what hljs makes of `cp "$HOME/docs/a.md" ./out/` (bash), `${ROOT}/src/x.py`, `$(ROOT)/src/main.c` (makefile), a TS template
  // literal, a Python f-string and a YAML value: the substitution in a span of its own, the path's tail in the node after it
  const c = code(
    row("cp ", el("span", "hljs-string", '"', el("span", "hljs-variable", "$HOME"), '/docs/a.md"'), " ./out/"),
    row("cat ", el("span", "hljs-variable", "${ROOT}"), "/src/x.py"),
    row("SRC = ", el("span", "hljs-variable", "$(ROOT)"), "/src/main.c"),
    row("const s = ", el("span", "hljs-string", "`", el("span", "hljs-subst", "${dir}"), "/out.json`"), ";"),
    row("p = ", el("span", "hljs-string", 'f"', el("span", "hljs-subst", "{base}"), '/out.csv"')),
    row("path: ", el("span", "hljs-string", el("span", "hljs-variable", "${HOME}"), "/notes/a.md")),
    row("x = ", el("span", "hljs-string", '"docs/'), el("span", "hljs-string", 'c.md"')),   // one token across two nodes: left as it is
    row("see ", el("span", "hljs-string", '"docs/a.md"'), " and ", el("span", "hljs-comment", "# https://example.invalid/x")),
    row("u = ", el("span", "hljs-string", "`https://example.invalid/", el("span", "hljs-subst", "${host}"), "/x`")),
    row("q = ", el("span", "hljs-string", '"docs/b.md'), ":", el("span", "hljs-number", "12"), '"'),   // the line reference cut off the path: the path links, the line stays text
    // a URL the substitution cuts, with an absolute path as a query value: bash's `$V` and a template's `${v}` in spans of their own
    row("curl https://example.invalid/q?x=/docs/a.md/", el("span", "hljs-variable", "$V")),
    row("const u = ", el("span", "hljs-string", "`https://example.invalid/q?x=/docs/a.md/", el("span", "hljs-subst", "${v}"), "`"), ";"),
    row("ok https://example.invalid/w?x=/docs/a.md/ and docs/d.md"),                                   // the same URL whole: wrapped, and the path after it links
  );
  const before = c.childNodes.map((r) => r.textContent);
  linkifyFileText(c as unknown as HTMLElement, FILE);
  assert.deepEqual(c.childNodes.map((r) => r.textContent), before);
  assert.deepEqual(links(c).map((x) => [x.textContent, x.dataset.path, x.dataset.line]), [["docs/a.md", DIR + "docs/a.md", undefined], ["docs/b.md", DIR + "docs/b.md", undefined], ["docs/d.md", DIR + "docs/d.md", undefined]],
    "the three real paths; no fabricated /docs/a.md, /src/x.py, /src/main.c, /out.json, /out.csv or /notes/a.md, the cut docs/c.md stays text, and the /docs/a.md inside the two cut URLs is the URL's");
  assert.deepEqual(urls(c).map((x) => x.href), ["https://example.invalid/x", "https://example.invalid/w?x=/docs/a.md/"], "a URL a substitution cuts through is not one; a whole one is");
});

test("the pass links nothing in text that is not a URL or a file: identifiers, imports, fractions, routes, folders, sites", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const c = code(
    row("from . import util"), row("import x from \"react/jsx-runtime\""), row("import y from \"lodash/fp.js\""), row("x = 1/2.5 + a/b"), row("app.get(\"/api/users\")"),
    row("cd /usr/bin && ls ~/code"), row("and/or read/write TCP/IP"), row("np.array(kernel.py)"), row("https://"),
    row("see www.example.org/docs/index.html or example.com/index.html"), row("git clone git@github.invalid:user/repo.git"), row("C:/Users/x/file.txt"), row("cat ~user/x.md"),
  );
  const before = c.textContent;
  linkifyFileText(c as unknown as HTMLElement, FILE);
  assert.equal(links(c).length, 0); assert.equal(urls(c).length, 0);
  assert.equal(c.textContent, before);
});

test("escaping: <, &, > and quotes around a link stay literal text; text inside an existing anchor is skipped; an empty body is a no-op", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const c = code(
    row('if (a < b && c > "docs/a.md") go(https://example.invalid/x?a=1&b=2)'),
    row(el("a", "", "docs/b.md and https://example.invalid/y")),      // an author's anchor: left as it is
  );
  const before = c.childNodes.map((r) => r.textContent);
  linkifyFileText(c as unknown as HTMLElement, FILE);
  assert.deepEqual(c.childNodes.map((r) => r.textContent), before);
  const first = c.childNodes[0] as El;
  const texts = textNodesOf(first).map((t) => t.data);
  assert.deepEqual(texts, ['if (a < b && c > "', "docs/a.md", '") go(', "https://example.invalid/x?a=1&b=2", ")"]);
  assert.equal(links(first).length, 1); assert.equal(urls(first).length, 1);
  assert.equal(urls(first)[0].href, "https://example.invalid/x?a=1&b=2", "the & is a character of the URL, not an entity");
  assert.equal(links(c.childNodes[1] as El).length + urls(c.childNodes[1] as El).length, 0, "the anchor's text is not re-linked");
  const empty = el("code", "hljs");
  linkifyFileText(empty as unknown as HTMLElement, FILE);
  assert.equal(empty.childNodes.length, 0);
});

// ── Markdown links ────────────────────────────────────────────────────────────────────────────────
test("viewerLinkTarget (marked's walkTokens, before the sanitizer): a same-directory `name.ext:12` and a local file:// URI take the form the sanitizer keeps; everything else is left as written; only link tokens are touched", async () => {
  const { viewerLinkTarget, viewerWalkTokens } = await import("./file-view-links");
  assert.equal(viewerLinkTarget("notes.md:7"), "./notes.md:7");
  assert.equal(viewerLinkTarget("README.md:12:3"), "./README.md:12:3");
  assert.equal(viewerLinkTarget("app.py:7#x"), "./app.py:7#x");
  assert.equal(viewerLinkTarget("app.test.ts:12"), "./app.test.ts:12", "three labels, the last no top-level domain: a file");
  assert.equal(viewerLinkTarget("archive.tar.gz:3"), "./archive.tar.gz:3");
  for (const h of ["app.component.vue:3", "styles.module.less:5", "report.final.docx:3", "init.el:12", "notes.v2.md:7"]) assert.equal(viewerLinkTarget(h), "./" + h, "a file whether or not the chat knows its extension: " + h);
  assert.equal(viewerLinkTarget("example.com:80"), "example.com:80", "a host by its top-level domain, whatever the label count: left as written, so the sanitizer removes it");
  assert.equal(viewerLinkTarget("file:///tmp/TESTHOST/a.md"), "/tmp/TESTHOST/a.md");
  assert.equal(viewerLinkTarget("file://localhost/tmp/TESTHOST/a.md"), "/tmp/TESTHOST/a.md");
  for (const h of ["docs/a.md:7", "./a.md:7", "../a.md:7", "a.md#L7", "https://example.invalid/x", "mailto:someone@example.invalid", "tel:12345",
                   "javascript:alert(1)", "file://evil.invalid/x.md", "Makefile:12", "#section", "?x=1", "notes.md", "",
                   "api.example.com:8443", "www.example.invalid:80", "sub.example.co.uk:443", "www.md:1"]) {   // a host with a port: left as written, so the sanitizer removes it and the anchor says why
    assert.equal(viewerLinkTarget(h), h, JSON.stringify(h));
  }
  const { isHostName } = await import("./file-view-links");
  // by shape, whatever the label count: `www.`, a generic or reserved top-level domain, or a country code under a registry's second-level label
  for (const n of ["api.example.com", "www.example.invalid", "sub.example.co.uk", "www.x", "a.b.dev", "example.com", "example.co", "docs.example.io", "x.internal", "bbc.co.uk", "example.com.au"]) assert.equal(isHostName(n), true, n);
  // a file, whether or not the chat's bare-name gate knows the extension: a last label that is no top-level domain by shape
  for (const n of ["notes.md", "app.test.ts", "archive.tar.gz", "jquery.min.js", "a_b.c.com", "x.y.py", "app.component.vue", "styles.module.less", "report.final.docx", "init.el", "notes.v2.md", "foo.uk", "a.b.uk"]) assert.equal(isHostName(n), false, n);
  const tok = { type: "link", href: "notes.md:7" }; viewerWalkTokens(tok); assert.equal(tok.href, "./notes.md:7");
  const img = { type: "image", href: "notes.md:7" }; viewerWalkTokens(img); assert.equal(img.href, "notes.md:7", "a figure's src is the viewer's figure resolver's business");
});

test("linkMarkdownAnchors: a URL target opens a tab, a file target becomes a path link on the anchor itself (label intact, path normalized), a query alone opens a tab, a fragment alone is the viewer's, a stripped target is a dead link that says why", async () => {
  const { linkMarkdownAnchors, DEAD_LINK_TITLE, HOST_PORT_TITLE, noSectionTitle } = await import("./file-view-links");
  const md = "/tmp/TESTHOST/notes-api/docs/guide.md";
  const A = (href: string, ...kids: Array<El | string>) => { const a = el("a", "", ...kids); a.setAttribute("href", href); return a; };
  const web = A("https://example.invalid/x", "web"), mail = A("mailto:someone@example.invalid", "mail"), proto = A("//example.invalid/p", "p");
  const rel = A("../src/app.py", el("strong", "", "the"), " app"), enc = A("my%20notes.md", "notes"), abs = A("/tmp/TESTHOST/other.md", "abs");
  const lineHash = A("../src/app.py#L12", "l12"), lineColon = A("../src/app.py:7", "l7"), same = A("./notes.md:7", "same"), query = A("a.md?x=1#top", "q");
  const qOnly = A("?x=1", "qo"), frag = A("#section", "here"), fragHit = A("#top", "top"), dead = el("a", "", "dead"), named = el("a", "", "");
  const sect = A("report.md#results", "sect"), ip = A("127.0.0.1:3000", "ip"), lh = A("localhost:8080", "lh");
  // after the shared sanitizer an author's id or name reaches the DOM under its user-content- prefix (md-sanitize.ts); a link to either spelling is live
  const pre = el("p", "", "Intro"); pre.setAttribute("id", "user-content-intro"); const preName = el("a", "", ""); preName.setAttribute("name", "user-content-usage");
  const fragPre = A("#intro", "intro"), fragPreName = A("#usage", "usage");
  named.setAttribute("name", "anchor");
  const fragName = A("#anchor", "to the name");
  const target = el("h2", "", "Top"); target.setAttribute("id", "top");
  const box = el("div", "fileview-md", target, pre, preName, el("p", "", web, mail, proto, rel, enc, abs, lineHash, lineColon, same, query, qOnly, frag, fragHit, dead, named, fragName, sect, ip, lh, fragPre, fragPreName));
  linkMarkdownAnchors(box as unknown as HTMLElement, md);
  for (const a of [web, mail, proto]) { assert.equal(a.getAttribute("target"), "_blank", a.href); assert.equal(a.getAttribute("rel"), "noopener"); assert.equal(a.dataset.path, undefined); }
  assert.equal(web.href, "https://example.invalid/x", "the href stays");
  for (const a of [rel, enc, abs, lineHash, lineColon, same, query, sect]) {
    assert.equal(a.getAttribute("href"), null, "the href comes off: the browser must not follow it");
    assert.ok(a.dataset.path, "a path to open"); assert.equal(a.role, "link"); assert.equal(a.tabIndex, 0);
    assert.ok(a.classes.includes("file-uri-link"), "the shared class on the anchor");
    assert.equal(a.target, "", "not a tab");
  }
  assert.equal(rel.dataset.path, "/tmp/TESTHOST/notes-api/src/app.py", "resolved against the shown file and normalized");
  assert.equal(rel.childNodes.length, 2); assert.equal((rel.childNodes[0] as El).tagName, "STRONG", "the label's nested formatting is kept");
  assert.equal(rel.textContent, "the app");
  assert.equal(enc.dataset.path, "/tmp/TESTHOST/notes-api/docs/my notes.md", "marked's percent-encoding undone");
  assert.equal(abs.dataset.path, "/tmp/TESTHOST/other.md");
  assert.equal(lineHash.dataset.path, "/tmp/TESTHOST/notes-api/src/app.py"); assert.equal(lineHash.dataset.line, "12");
  assert.equal(lineHash.getAttribute("title"), "Open /tmp/TESTHOST/notes-api/src/app.py:12");
  assert.equal(lineColon.dataset.line, "7");
  assert.equal(same.dataset.path, "/tmp/TESTHOST/notes-api/docs/notes.md", "the same-directory target the hook prefixed with ./, resolved and normalized");
  assert.equal(same.dataset.line, "7"); assert.equal(same.getAttribute("title"), "Open /tmp/TESTHOST/notes-api/docs/notes.md:7");
  assert.equal(query.dataset.path, "/tmp/TESTHOST/notes-api/docs/a.md", "the query is dropped from the path");
  assert.equal(query.dataset.line, undefined); assert.equal(query.dataset.frag, "top", "the fragment after the query is the section to land on"); assert.equal(query.getAttribute("title"), "Open /tmp/TESTHOST/notes-api/docs/a.md#top");
  // a sibling's own #fragment rides the path link (data-frag) and lands once the file is open (file-view.ts openFileView's frag); a #L12 is the line instead
  assert.equal(sect.dataset.path, "/tmp/TESTHOST/notes-api/docs/report.md"); assert.equal(sect.dataset.frag, "results"); assert.equal(sect.dataset.line, undefined);
  assert.equal(sect.getAttribute("title"), "Open /tmp/TESTHOST/notes-api/docs/report.md#results");
  assert.equal(lineHash.dataset.frag, undefined); assert.equal(same.dataset.frag, undefined);
  // a host with a port whose target the sanitizer lets stand (no letter-led scheme): a dead link that says so, never a file named 127.0.0.1 at line 3000
  for (const a of [ip, lh]) { assert.equal(a.getAttribute("href"), null, a.textContent); assert.equal(a.dataset.path, undefined); assert.ok(a.classes.includes("fv-dead"), a.className); assert.equal(a.getAttribute("title"), HOST_PORT_TITLE); }
  // a query alone: a web-style link to the page's own address, a new tab, never this document
  assert.equal(qOnly.getAttribute("href"), "?x=1"); assert.equal(qOnly.getAttribute("target"), "_blank"); assert.equal(qOnly.getAttribute("rel"), "noopener noreferrer"); assert.equal(qOnly.dataset.path, undefined);
  // a fragment alone: the viewer's click; with no element of that id the link is dead and says so
  assert.equal(frag.getAttribute("href"), "#section"); assert.ok(frag.classes.includes("fv-frag") && frag.classes.includes("fv-dead"), frag.className);
  assert.equal(frag.dataset.frag, undefined); assert.equal(frag.getAttribute("title"), noSectionTitle("section"));
  assert.ok(fragHit.classes.includes("fv-frag") && !fragHit.classes.includes("fv-dead"), fragHit.className);
  assert.equal(fragHit.dataset.frag, "top"); assert.equal(fragHit.getAttribute("title"), "Go to top");
  // a GitHub-style `<a name>` is a target too (the README idiom above a heading): live, by name
  assert.ok(fragName.classes.includes("fv-frag") && !fragName.classes.includes("fv-dead"), fragName.className);
  assert.equal(fragName.dataset.frag, "anchor"); assert.equal(fragName.getAttribute("title"), "Go to anchor");
  const { fragmentTarget } = await import("./file-view-links");
  assert.equal(fragmentTarget(box as unknown as HTMLElement, "anchor"), named); assert.equal(fragmentTarget(box as unknown as HTMLElement, "top"), target);
  for (const [a, id, hit] of [[fragPre, "intro", pre], [fragPreName, "usage", preName]] as const) {
    assert.ok(a.classes.includes("fv-frag") && !a.classes.includes("fv-dead"), id + ": live under the sanitizer's prefix: " + a.className);
    assert.equal(a.dataset.frag, id); assert.equal(fragmentTarget(box as unknown as HTMLElement, id), hit, id + " is found under user-content-" + id);
  }
  assert.equal(fragmentTarget(box as unknown as HTMLElement, "user-content-intro"), pre, "the prefix as typed finds it too");
  const both = el("div", "", el("a", "", ""), el("h2", "", "x")); (both.childNodes[0] as El).setAttribute("name", "dup"); (both.childNodes[1] as El).setAttribute("id", "dup");
  assert.equal(fragmentTarget(both as unknown as HTMLElement, "dup"), both.childNodes[1], "an id wins over a name, as the browser's fragment rule has it");
  assert.equal(fragmentTarget(box as unknown as HTMLElement, "nowhere"), undefined);
  // a heading: the viewer mints `md-` + its slug (mdBlock), and the lookup reads the slug of the id asked for
  const h2 = el("h2", "", "Evidence Results"); h2.setAttribute("id", "md-evidence-results");
  const withH = el("div", "", h2);
  for (const id of ["evidence-results", "Evidence Results"]) assert.equal(fragmentTarget(withH as unknown as HTMLElement, id), h2, id);
  assert.equal(fragmentTarget(withH as unknown as HTMLElement, "md-evidence-results"), h2, "the minted id itself, by the id arm");
  // an anchor the sanitizer stripped: dead, with the reason; a named target never was a link and is left alone
  assert.ok(dead.classes.includes("fv-dead")); assert.equal(dead.getAttribute("title"), DEAD_LINK_TITLE);
  assert.equal(named.getAttribute("class"), null); assert.equal(named.getAttribute("title"), null);
});

test("in a rendered body the prose's bare paths link under the viewer's gate, a fenced block's URL links, and inline code's bare filename does not", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const md = "/tmp/TESTHOST/notes-api/docs/guide.md";
  const marked = el("a", "", "https://example.invalid/auto"); marked.setAttribute("href", "https://example.invalid/auto");
  const box = el("div", "fileview-md",
    el("p", "", "Read ../src/app.py:3 and ", el("code", "", "setup.py"), " then ", marked, "."),
    el("pre", "", el("code", "language-bash hljs", "curl https://example.invalid/dl -o data/x.json")),
  );
  const before = box.textContent;
  linkifyFileText(box as unknown as HTMLElement, md);
  assert.equal(box.textContent, before);
  const l = links(box);
  assert.deepEqual(l.map((x) => x.textContent), ["../src/app.py:3", "data/x.json"], "the prose path with its line, the fenced block's path; not the backticked bare filename");
  assert.equal(l[0].dataset.path, "/tmp/TESTHOST/notes-api/src/app.py"); assert.equal(l[0].dataset.line, "3");
  assert.equal(l[1].dataset.path, "/tmp/TESTHOST/notes-api/docs/data/x.json");
  const u = urls(box);
  assert.deepEqual(u.map((x) => x.href), ["https://example.invalid/dl"], "the fenced block's URL; marked's own anchor is not wrapped twice");
});

test("a line break or a no-break space before a token is whitespace: a path starting a soft-broken line links, every line of a fenced block links, a list item's second line links, and the Raw view's one-unit code body links each line's path", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const md = "/tmp/TESTHOST/notes-api/docs/guide.md", MDDIR = "/tmp/TESTHOST/notes-api/docs/";
  const box = el("div", "fileview-md",
    el("p", "", "See the plan in\ndocs/plan.md and\r\ndocs/win.md, then\u00a0docs/nb.md."),
    el("pre", "", el("code", "language-python hljs", "x = 1\ndocs/a.md\n  docs/b.md\n")),
    el("ul", "", el("li", "", "first\ndocs/li.md")),
  );
  const before = box.textContent;
  linkifyFileText(box as unknown as HTMLElement, md);
  assert.equal(box.textContent, before);
  assert.deepEqual(links(box).map((x) => [x.textContent, x.dataset.path]), [
    ["docs/plan.md", MDDIR + "docs/plan.md"], ["docs/win.md", MDDIR + "docs/win.md"], ["docs/nb.md", MDDIR + "docs/nb.md"],
    ["docs/a.md", MDDIR + "docs/a.md"], ["docs/b.md", MDDIR + "docs/b.md"], ["docs/li.md", MDDIR + "docs/li.md"],
  ], "a path may start any line of a block, not only the first");
  // the Raw view's other shape (codeBlock's gutter branch): the whole highlighted body in one <code>, its lines split by \n
  const c = el("pre", "fileview-pre", el("code", "hljs", "x = 1\n", el("span", "hljs-string", "'docs/e.md'"), "\ndocs/f.md\n", el("span", "hljs-comment", "# see docs/g.md"), "\n"));
  const raw = c.textContent;
  linkifyFileText(c as unknown as HTMLElement, FILE);
  assert.equal(c.textContent, raw);
  assert.deepEqual(links(c).map((x) => [x.textContent, x.dataset.path]), [["docs/e.md", DIR + "docs/e.md"], ["docs/f.md", DIR + "docs/f.md"], ["docs/g.md", DIR + "docs/g.md"]]);
});

test("a <br> ends a unit: the path after it is read from its own line, never glued to the text before the break", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const { textUnits } = await import("./path-links");
  const md = "/tmp/TESTHOST/notes-api/docs/guide.md", MDDIR = "/tmp/TESTHOST/notes-api/docs/";
  const p1 = el("p", "", "a", el("br"), "docs/a.md");
  assert.deepEqual(textUnits(p1 as unknown as HTMLElement, "p", "a").map((u) => u.text), ["a", "docs/a.md"], "two units, cut at the break");
  const box = el("div", "fileview-md",
    p1,
    el("p", "", el("span", "", "x: docs/one.md"), el("br"), el("span", "", "docs/two.md"), el("br"), "https://example.invalid/z"),
  );
  const before = box.textContent;
  linkifyFileText(box as unknown as HTMLElement, md);
  assert.equal(box.textContent, before);
  assert.deepEqual(links(box).map((x) => [x.textContent, x.dataset.path]), [["docs/a.md", MDDIR + "docs/a.md"], ["docs/one.md", MDDIR + "docs/one.md"], ["docs/two.md", MDDIR + "docs/two.md"]],
    "read as one unit the first would be `adocs/a.md`, one token across two nodes, which the walk drops");
  assert.deepEqual(urls(box).map((x) => x.href), ["https://example.invalid/z"]);
});

test("spanHolding over many spans finds the one holding a token (a binary search over the unit's spans): a row of a thousand highlighted strings links each path inside its own span", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const { spanHolding, textUnits } = await import("./path-links");
  const kids: Array<El | string> = [];
  for (let i = 0; i < 1000; i++) { kids.push(el("span", "hljs-string", '"docs/s' + i + '.md"')); kids.push(", "); }
  const c = code(row(...kids));
  linkifyFileText(c as unknown as HTMLElement, FILE);
  const l = links(c);
  assert.equal(l.length, 1000);
  l.forEach((x, i) => { assert.equal(x.textContent, "docs/s" + i + ".md"); assert.equal(x.parentNode!.className, "hljs-string"); assert.equal(x.parentNode!.textContent, '"docs/s' + i + '.md"'); });
  // the search itself: the span holding the start, whole and open; null across an edge or in a dead span; nothing off the end
  const u = textUnits(el("p", "", "aa", el("a", "", "bb"), "cc", "dd") as unknown as HTMLElement, "p", "a")[0];
  assert.equal(u.text, "aabbccdd"); assert.equal(u.spans.length, 4);
  assert.equal(spanHolding(u, 0, 2), u.spans[0]); assert.equal(spanHolding(u, 4, 6), u.spans[2]); assert.equal(spanHolding(u, 7, 8), u.spans[3]);
  assert.equal(spanHolding(u, 1, 3), null, "across an edge"); assert.equal(spanHolding(u, 2, 4), null, "inside the link: dead"); assert.equal(spanHolding(u, 8, 9), null); assert.equal(spanHolding(u, -1, 1), null);
});

test("a Markdown link whose file target resolves to no path is a dead link that says why, with no href to follow and no path to open: `[up](../)` from `docs/plan.md` points above the file's folder, `[here](./)` from `README.md` names the folder the file is in", async () => {
  const { linkMarkdownAnchors, EMPTY_TARGET_TITLE, SELF_TARGET_TITLE, DEAD_LINK_TITLE, emptyTargetTitle, resolveViewerPath } = await import("./file-view-links");
  const A = (href: string, ...kids: Array<El | string>) => { const a = el("a", "", ...kids); a.setAttribute("href", href); return a; };
  const up = A("../", "up"), dot = A("..", "dot"), root = A("/", "root"), here = A("./", "here"), sib = A("../x.md", "sib");
  const box = el("div", "fileview-md", el("p", "", up, dot, root, here, sib));
  linkMarkdownAnchors(box as unknown as HTMLElement, "docs/plan.md");
  for (const a of [up, dot]) {
    assert.equal(a.getAttribute("href"), null, "the browser must not follow it"); assert.equal(a.dataset.path, undefined, "no silent click on an empty path");
    assert.ok(a.classes.includes("fv-dead") && !a.classes.includes("file-uri-link"), a.className); assert.equal(a.getAttribute("title"), EMPTY_TARGET_TITLE);
  }
  assert.notEqual(EMPTY_TARGET_TITLE, DEAD_LINK_TITLE, "its own reason");
  assert.equal(root.dataset.path, "/"); assert.equal(here.dataset.path, "docs/"); assert.equal(sib.dataset.path, "x.md", "a sibling folder's file above the shown one resolves fine");
  // from a file named without a directory, `./`, `.` and `docs/../` name the folder the file is in: dead too, with that reason, not "above"
  const selfHere = A("./", "here"), selfDot = A(".", "dot"), selfBack = A("docs/../", "back"), upRel = A("../", "up");
  linkMarkdownAnchors(el("div", "fileview-md", el("p", "", selfHere, selfDot, selfBack, upRel)) as unknown as HTMLElement, "README.md");
  for (const a of [selfHere, selfDot, selfBack]) {
    assert.equal(a.getAttribute("href"), null); assert.equal(a.dataset.path, undefined);
    assert.ok(a.classes.includes("fv-dead"), a.className); assert.equal(a.getAttribute("title"), SELF_TARGET_TITLE, a.textContent);
  }
  assert.notEqual(SELF_TARGET_TITLE, EMPTY_TARGET_TITLE); assert.match(SELF_TARGET_TITLE, /the folder this file is in/); assert.match(EMPTY_TARGET_TITLE, /above the folder/);
  assert.equal(upRel.dataset.path, "../", "climbing out of a relative name keeps the `..`: a live link the kernel resolves");
  // the rule behind the two titles: an empty resolution from a file with a directory can only have climbed above it; from one without, it is the folder itself
  for (const [tok, file] of [["./", "README.md"], [".", "README.md"], ["docs/../", "README.md"], ["a/b/../../", "README.md"], ["../", "docs/plan.md"], ["..", "docs/plan.md"], ["./..", "docs/plan.md"], ["docs/../..", "docs/plan.md"]] as const) {
    assert.equal(resolveViewerPath(tok, file), "", tok + " from " + file);
  }
  for (const [tok, file] of [["./", "docs/plan.md"], [".", "docs/plan.md"], ["docs/../", "docs/plan.md"], ["../", "README.md"], ["..", "README.md"]] as const) {
    assert.notEqual(resolveViewerPath(tok, file), "", tok + " from " + file + " has a path");
  }
  assert.equal(emptyTargetTitle("README.md"), SELF_TARGET_TITLE); assert.equal(emptyTargetTitle("docs/plan.md"), EMPTY_TARGET_TITLE); assert.equal(emptyTargetTitle("/tmp/TESTHOST/a.md"), EMPTY_TARGET_TITLE);
  const upAbs = A("../", "upAbs");
  linkMarkdownAnchors(el("div", "fileview-md", el("p", "", upAbs)) as unknown as HTMLElement, "/tmp/TESTHOST/notes-api/docs/guide.md");
  assert.equal(upAbs.dataset.path, "/tmp/TESTHOST/notes-api/", "from an absolute file the folder above has a name");
});

test("text inside an inline SVG is read but never marked: a path or a URL in a figure's label stays as written (an HTML element inserted into SVG text does not render), in the viewer's pass and in the chat's walk", async () => {
  const { linkifyFileText } = await import("./file-view-links");
  const { linkifyPathTokens } = await import("./path-links");
  const md = "/tmp/TESTHOST/notes-api/docs/guide.md";
  const label = el("text", "", "docs/a.md and https://example.invalid/x");
  const box = el("div", "fileview-md", el("p", "", "see ", el("svg", "", el("g", "", label)), " and docs/b.md https://example.invalid/y"));
  const before = box.textContent;
  linkifyFileText(box as unknown as HTMLElement, md);
  assert.equal(box.textContent, before);
  assert.deepEqual(links(box).map((x) => x.textContent), ["docs/b.md"]);
  assert.deepEqual(urls(box).map((x) => x.href), ["https://example.invalid/y"]);
  assert.equal(label.childNodes.length, 1); assert.ok(label.childNodes[0] instanceof Txt, "the label is still one text node");
  const chat = el("div", "md", el("p", "", el("svg", "", el("text", "", "docs/c.md")), " docs/d.md"));
  linkifyPathTokens(chat as unknown as HTMLElement);
  assert.deepEqual(links(chat).map((x) => [x.textContent, x.dataset.path]), [["docs/d.md", "docs/d.md"]], "the chat's walk skips the SVG too");
});

// ── the gesture helpers, executed ─────────────────────────────────────────────────────────────────
test("wantsOwnTab reads a Cmd/Ctrl-click or the middle button; openFileTab opens the kernel's /file URL in a tab with the opener severed, reports a blocked popup, and stands down off the web", async () => {
  const { wantsOwnTab, openFileTab, fileUrl } = await import("./preview");
  assert.equal(wantsOwnTab({ metaKey: true }), true); assert.equal(wantsOwnTab({ ctrlKey: true }), true); assert.equal(wantsOwnTab({ button: 1 }), true);
  assert.equal(wantsOwnTab({}), false); assert.equal(wantsOwnTab({ button: 0 }), false); assert.equal(wantsOwnTab(null), false); assert.equal(wantsOwnTab(undefined), false);
  const g = globalThis as any;
  const saved = { loc: g.location, win: g.window };
  try {
    const calls: unknown[][] = [];
    const handle: { opener: unknown } = { opener: { theDashboard: true } };
    g.location = { protocol: "https:" }; g.window = { open: (...a: unknown[]) => { calls.push(a); return handle; } };
    assert.equal(openFileTab("/tmp/TESTHOST/notes-api/docs/guide.md", SID), true);
    assert.deepEqual(calls, [[fileUrl("/tmp/TESTHOST/notes-api/docs/guide.md", SID), "_blank"]], "the same-origin file URL, a new tab, no noopener feature (it returns null even on success)");
    assert.equal(calls[0][0], "/file?path=%2Ftmp%2FTESTHOST%2Fnotes-api%2Fdocs%2Fguide.md&sid=" + SID);
    assert.equal(handle.opener, null, "severed on the handle: a link inside the opened file must not hold the dashboard");
    g.window = { open: () => null };
    assert.equal(openFileTab("/tmp/TESTHOST/x.md", SID), false, "blocked: the caller's viewer takes over");
    let tried = false;
    g.location = { protocol: "vscode-webview:" }; g.window = { open: () => { tried = true; return {}; } };
    assert.equal(openFileTab("/tmp/TESTHOST/x.md", SID), false); assert.equal(tried, false, "the webview has no tabs and no kernel origin: never tried");
  } finally { g.location = saved.loc; g.window = saved.win; }
});

// ── the viewer's wiring, at source ────────────────────────────────────────────────────────────────
test("source: codeBlock and mdBlock run the one pass on the DOM they built; the markdown anchors are sorted before the fenced-block highlight and the text after it; marked's parse carries the link-target hook per call; the fallback is left bare", () => {
  assert.match(VIEW, /import \{ linkifyFileText, linkMarkdownAnchors, viewerWalkTokens, fragmentTarget, URL_LINK_CLASS, FRAG_LINK_CLASS \} from "\.\/file-view-links";/);
  const codeFn = VIEW.split("function codeBlock(text: string, path: string, wrapLines: boolean): HTMLElement {")[1].split("\n}\n")[0];
  assert.match(codeFn, /code\.innerHTML = wrapNumberedHtml\(hl !== null \? hl : escapeHtml\(text\)\);\n\s*linkifyFileText\(code, path\);/, "the wrap branch: after the rows are in the DOM");
  assert.match(codeFn, /if \(hl !== null\) code\.innerHTML = hl; else code\.textContent = text;\n\s*linkifyFileText\(code, path\);/, "the gutter branch too");
  assert.doesNotMatch(codeFn, /linkifyFileText\(text|escapeHtml\(linkify/, "never over the HTML string");
  // the viewer always builds the wrap view (each line its own .fv-cl row), which is what scrollToLine reads
  const openFn = VIEW.split("export function openFileView(")[1].split("function offersDownload")[0];
  assert.ok((openFn.match(/codeBlock\([^)]*\)/g) || []).every((c) => /, true\)$/.test(c)), "every codeBlock call in the viewer asks for wrap mode: " + (openFn.match(/codeBlock\([^)]*\)/g) || []).join(" | "));
  const mdFn = VIEW.split("function mdBlock(text: string, doc?: MdDocLoc): HTMLElement {")[1].split("\n}\n")[0];
  assert.match(mdFn, /const base = marked\.defaults\.walkTokens;\n\s*const dirty = marked\.parse\(text, \{ walkTokens: \(t\) => \{\n(?:\s*if \(t\.type === "code"\)[^\n]*\n)?\s*if \(doc && doc\.kind === "file"\) viewerWalkTokens\(t\);\n\s*if \(base\) void base\.call\(marked, t\);\n\s*\} \}\) as string;/,
    "the hook rides on this parse alone (the per-call walkTokens that also collects the fences for Copy), and on the file kind's alone: the singleton is the chat's too, and a URL document has no directory for `notes.md:7`");
  const anchorsAt = mdFn.indexOf("if (rendered) linkMarkdownAnchors(box, doc.path);");
  const hlAt = mdFn.indexOf('box.querySelectorAll("pre code").forEach');
  const textAt = mdFn.indexOf('if (rendered && doc && doc.kind === "file") linkifyFileText(box, doc.path);');
  assert.ok(anchorsAt > 0 && hlAt > anchorsAt && textAt > hlAt && mdFn.indexOf("return box;") > textAt, "anchors → highlight → text, then return");
  assert.match(mdFn, /box\.textContent = text;[^\n]*\n\s*rendered = false;/, "the fallback's bare text takes no links");
  assert.ok(mdFn.indexOf('if (doc && doc.kind === "file") {') > 0 && mdFn.indexOf('if (doc && doc.kind === "file") {') < anchorsAt, "the file kind's anchors are sorted by the module");
  assert.equal((mdFn.match(/querySelectorAll\("a\[href\]"\)/g) || []).length, 2, "the two a[href] loops are the URL kind's (resolution against the URL) and the no-file arm's (a tab, or an in-document fv-anchor): neither runs over a file's anchors");
  assert.ok(mdFn.indexOf("sanitizeMd(") > 0 && mdFn.indexOf("sanitizeMd(") < anchorsAt, "marks are set AFTER the shared sanitizer ran (md-sanitize.ts, ALLOW_DATA_ATTR false): a document's own data-* never reaches the page");
});

test("source: the body's listener and its gesture: a drag-select opens nothing, a plain path click opens in the viewer and goes on to the document (a modified one stops and takes its own tab), a URL anchor is the browser's on a plain click, a section link scrolls the rendered document and never moves the page", () => {
  const d = VIEW.split('body.addEventListener("click", (ev) => {')[1].split("\n  });\n")[0];
  assert.match(d, /const x = linkOf\(ev\.target as Element \| null\);\n\s*if \(!x\) return;/);
  assert.match(d, /if \(selectionOpenIn\(box\)\) \{ ev\.preventDefault\(\); return; \}/, "a drag-select that ended on the link opens nothing");
  assert.ok(d.indexOf("selectionOpenIn(box)") < d.indexOf("openLink(x, ev)"), "the selection first, then the link");
  // the one selection test, in path-links.ts, read by the viewer's listener and by the chat's capture-phase opener (which runs first)
  assert.match(LINKS, /export function selectionOpenIn\(el: Node\): boolean \{\n\s*const sel = window\.getSelection\(\);\n\s*return !!sel && !sel\.isCollapsed && el\.contains\(sel\.anchorNode\);\n\}/);
  const opener = RENDER.slice(RENDER.indexOf('document.addEventListener("click", (e) => {\n  const a = (e.target as HTMLElement)?.closest?.("a[href]")'), RENDER.indexOf("}, true);", RENDER.indexOf('closest?.("a[href]")')));
  assert.match(opener, /if \(!a\) return;\n(?:\s*\/\/[^\n]*\n)*\s*if \(!a\.draggable && selectionOpenIn\(a\)\) \{ e\.preventDefault\(\); return; \}\n\s*const href = a\.getAttribute\("href"\) \|\| "";/,
    "the chat's opener: for a non-draggable anchor only, the selection open inside it (the click that ends a drag-select: cancelled, never opened; a chat anchor is draggable and a selection left around it by a triple-click is not a drag on it), then the href");
  assert.match(RENDER, /import \{ openPathLink, linkifyPathTokens, selectionOpenIn, type PathLinkOptions \} from "\.\/path-links";/);   // + the options type for the chat's fenced walk (2026-09-12)
  assert.match(VIEW, /import \{ selectionOpenIn \} from "\.\/path-links";/);
  assert.doesNotMatch(d, /getSelection|isCollapsed/, "no second spelling of the selection test in the listener");
  assert.match(VIEW, /const linkOf = \(t: Element \| null\): HTMLElement \| null => \{\n\s*const x = t && typeof t\.closest === "function" \? t\.closest\("\.file-uri-link, a\." \+ URL_LINK_CLASS \+ ", a\." \+ FRAG_LINK_CLASS\) as HTMLElement \| null : null;\n\s*return x && body\.contains\(x\) \? x : null;/);
  const o = VIEW.split("const openLink = (x: HTMLElement, ev: MouseEvent) => {")[1].split("\n  };\n")[0];
  assert.match(o, /const own = wantsOwnTab\(ev\);/);
  assert.match(o, /if \(x\.classList\.contains\(FRAG_LINK_CLASS\)\) \{[^\n]*\n\s*ev\.preventDefault\(\);\n(?:\s*\/\/[^\n]*\n)*\s*scrollToFragment\(body, x\.getAttribute\("href"\) \|\| ""\);\n\s*return;/,
    "a section link: the rendered document's own headings, ids and named anchors (the viewer's chrome has ids of its own), this document's scroll or nothing, never the page's location");
  assert.doesNotMatch(o, /querySelectorAll\("\[id\]"\)|getElementById/, "never the whole box, and never a lookup of its own: the one in file-view-links.ts");
  assert.match(VIEW, /const target = fragmentTarget\(box\.querySelector\("\.fileview-md"\) \|\| box, frag\);/, "scrollToFragment (both viewers land through it): the rendered box, through the one lookup");
  assert.match(MOD, /const hit = id \? fragmentTarget\(root, id\) : undefined;/, "mark time reads the same root, the .fileview-md box, through the one lookup");
  assert.match(MOD, /export function fragmentTarget\(root: ParentNode, id: string\): Element \| undefined \{\n\s*return userContentTarget\(root, id\)\n\s*\|\| root\.querySelector\('\[id="md-' \+ headingSlug\(id\) \+ '"\]'\) \|\| undefined;/,
    "an author's id or name, prefixed by the sanitizer or bare (md-sanitize.ts userContentTarget, the lookup the chat's # click reads too), then the heading whose slug it is (the viewer mints md-<slug> ids; md-url-view.test.ts)");
  assert.match(MOD, /import \{ userContentTarget \} from "\.\/md-sanitize";/);
  assert.match(o, /const p = x\.dataset\.path;\n\s*if \(!p\) \{[^\n]*\n\s*if \(!own\) return;[^\n]*\n\s*ev\.preventDefault\(\); ev\.stopPropagation\(\);[^\n]*\n\s*openUrlTab\(x\.getAttribute\("href"\) \|\| ""\);\n\s*return;/, "a URL anchor: plain is the browser's, modified is one tab from here");
  assert.match(o, /ev\.preventDefault\(\);\n\s*if \(own\) \{\n\s*ev\.stopPropagation\(\);[^\n]*\n\s*if \(openFileTab\(p, sid \|\| null\)\) return;[^\n]*\n\s*\}\n\s*const ln = Number\(x\.dataset\.line\);\n\s*openFileView\(p, sid \|\| null, \{ line: ln > 0 \? ln : null, frag: x\.dataset\.frag \|\| null \}\);/,
    "a path link: a modified click stops and takes its own tab (the viewer when the popup was blocked); a plain click opens in the viewer and is NOT stopped");
  assert.equal((o.match(/stopPropagation/g) || []).length, 2, "two stops in openLink, both on the modified gesture: the URL anchor's and the path link's; a plain click goes on to the document's listeners");
  assert.match(VIEW, /const openUrlTab = \(href: string\) => \{\n\s*if \(!href\) return;\n\s*if \(canPreview\(\)\) window\.open\(href, "_blank", "noopener,noreferrer"\);[^\n]*\n\s*else post\(\{ type: "openLink", href \}\);/, "render.ts's two openers, by host");
  assert.match(VIEW, /body\.addEventListener\("mousedown", \(ev\) => \{\n\s*const x = ev\.button === 1 \? linkOf\(ev\.target as Element \| null\) : null;\n\s*if \(x && x\.dataset\.path\) ev\.preventDefault\(\);\n\s*\}\);/, "the middle press on a path link starts no autoscroll");
  assert.match(VIEW, /body\.addEventListener\("auxclick", \(ev\) => \{\n\s*if \(ev\.button !== 1\) return;\n\s*const x = linkOf\(ev\.target as Element \| null\);\n\s*if \(x && \(x\.dataset\.path \|\| x\.classList\.contains\(FRAG_LINK_CLASS\)\)\) openLink\(x, ev\);\n\s*\}\);/,
    "the middle-click on a path link is its own tab; on a section link it is this document's scroll (openLink's frag branch cancels the browser's tab at the page's URL plus the id); a URL anchor's is the browser's");
  assert.match(VIEW, /import \{ openFileTab, canPreview \} from "\.\/preview";/, "on a line of its own beside the two preview imports file-view.test.ts pins");
  // one listener per open, installed before any render can run (click-safe across the Rendered ⇄ Raw swaps that rebuild the body's children)
  const openFn = VIEW.split("export function openFileView(")[1].split("function offersDownload")[0];
  assert.equal((openFn.match(/body\.addEventListener\("click"/g) || []).length, 1);
  assert.ok(openFn.indexOf('body.addEventListener("click"') < openFn.indexOf("const renderBody ="), "installed before any render can run");
  assert.doesNotMatch(openFn, /delegate\(body/, "the delegate that served the sibling links is gone: the one listener sorts every link kind");
  // a held Cmd/Ctrl rides from the keyboard into the click (path-links.ts)
  assert.match(LINKS, /if \(e\.metaKey \|\| e\.ctrlKey\) a\.dispatchEvent\(new MouseEvent\("click", \{ bubbles: true, cancelable: true, metaKey: e\.metaKey, ctrlKey: e\.ctrlKey \}\)\);\n\s*else a\.click\(\);/);
  // the gesture is the project's one rule, in preview.ts
  assert.match(PREVIEW, /export function wantsOwnTab\(ev\?: \{ metaKey\?: boolean; ctrlKey\?: boolean; button\?: number \} \| null\): boolean \{\n\s*return !!ev && \(!!ev\.metaKey \|\| !!ev\.ctrlKey \|\| ev\.button === 1\);\n\}/);
  assert.match(PREVIEW, /export function openFileTab\(path: string, sid\?: string \| null\): boolean \{\n\s*if \(!canPreview\(\)\) return false;\n\s*const w = window\.open\(fileUrl\(path, sid\), "_blank"\);\n\s*if \(!w\) return false;/);
  assert.match(PREVIEW, /export function openPdfTab\(path: string, sid\?: string \| null\): boolean \{\n\s*if \(previewKind\(path\) !== "pdf"\) return false;\n\s*return openFileTab\(path, sid\);\n\}/, "the PDF opener is the file opener with the kind check in front");
});

test("source: a link's line scrolls the code view's row once the text lands, spent once; a line past the end says so in the viewer's notice and lands on the last row; a markdown file takes its Raw view for that open without saving the preference", () => {
  assert.match(VIEW, /export function openFileView\(path: string, sid\?: string \| null, opts\?: \{ line\?: number \| null; frag\?: string \| null \}\): boolean \{/);
  assert.match(VIEW, /const scrollToLine = \(n: number\) => \{\n\s*const rows = body\.querySelectorAll\("code\.hljs \.fv-cl"\);\n\s*if \(!rows\.length\) return;\n\s*if \(n > rows\.length\) noteBar\("Line " \+ n \+ " is past the end of this file, which has " \+ rows\.length \+ \(rows\.length === 1 \? " line" : " lines"\) \+ "; showing the last line\."\);\n\s*\(rows\[Math\.min\(Math\.max\(0, n - 1\), rows\.length - 1\)\] as HTMLElement\)\.scrollIntoView\(\{ block: "center" \}\);/);
  assert.match(VIEW, /let pendingLine: number \| null = opts && typeof opts\.line === "number" && opts\.line > 0 \? Math\.floor\(opts\.line\) : null;/);
  assert.match(VIEW, /text = t;\n\s*\/\/[^\n]*\n\s*if \(pendingLine !== null && isMd && fmt\.md === "rendered"\) fmt\.md = "raw";\n\s*renderBody\(\);\n\s*if \(pendingLine !== null\) \{ scrollToLine\(pendingLine\); pendingLine = null; \}/);
  const landing = VIEW.split("text = t;\n")[1].split("}).catch(")[0];
  assert.doesNotMatch(landing, /saveFmt/, "the Raw view for this open only: the preference is not written");
  assert.match(VIEW, /let pendingFrag: string \| null = opts\?\.frag \|\| null;/, "a sibling link's #fragment lands after the first rendered paint, as before");
});

test("source: the shared walk's options, the line units and the anchor marker live in path-links.ts, defaults unchanged for the chat; the module writes attributes, never markup", () => {
  assert.match(LINKS, /export interface PathLinkOptions \{\n\s*inPre\?: boolean;\n\s*preVerified\?: boolean;\n\s*accept\?: \(tok: string, ctx: \{ text: string; at: number; inPre: boolean \}\) => boolean;\n\s*resolve\?: \(tok: string\) => string;\n\s*lineSuffix\?: boolean;\n\s*unit\?: string;\n\}/,
    "+ preVerified and the ctx's inPre for the chat's fenced blocks (2026-09-12); the viewer passes neither");
  assert.match(LINKS, /export function linkifyPathTokens\(root: HTMLElement, pathLinks\?: Record<string, string>, opts\?: PathLinkOptions\): PathLinkHit\[\] \{/);
  assert.match(LINKS, /export const DEAD_TEXT = "a, \.file-uri-link, svg";/, "a link, and an inline SVG (an element put inside SVG text does not render)");
  assert.match(LINKS, /const skip = opts && opts\.inPre \? DEAD_TEXT : DEAD_TEXT \+ ", pre";/, "the chat still skips fenced blocks");
  assert.match(LINKS, /const walker = document\.createTreeWalker\(root, NodeFilter\.SHOW_ELEMENT \| NodeFilter\.SHOW_TEXT\);/, "the walk sees elements, to cut a unit at a <br>");
  assert.match(LINKS, /if \(n\.nodeType === 1\) \{\n\s*const e = n as Element;\n\s*if \(\/\^br\$\/i\.test\(e\.tagName\)\) broke = true;\n\s*else if \(unit && e\.matches\(unit\) && e\.textContent === ""\) \{ units\.push\(\{ text: "", spans: \[\] \}\); cur = null; \}[^\n]*\n\s*continue;\n\s*\}/, "an element is a <br> (a unit ends) or an empty unit element (an empty unit, a code view's blank row); nothing else");
  assert.doesNotMatch(LINKS, /text\.slice\(start \+ tok\.length\)/, "the line suffix is read in place, never off a slice of the rest of the unit (quadratic)");
  assert.match(LINKS, /LINE_SUFFIX_AT_RE\.lastIndex = start \+ tok\.length; suffix = LINE_SUFFIX_AT_RE\.exec\(text\);/);
  assert.match(LINKS, /const LINE_SUFFIX_AT_RE = new RegExp\(LINE_SUFFIX_RE\.source\.replace\(\/\^\\\^\/, ""\), "y"\);/, "cut from the one source");
  assert.doesNotMatch(MOD, /ctx\.text\.slice\(0, ctx\.at\)/, "the gate never reads the unit's whole text before the token (quadratic over a fence)");
  assert.match(MOD, /if \(!anchored && importLookBehind\(ctx\.text, ctx\.at\)\.isImport\) return false;/);
  assert.match(MOD, /const OPENER_RE = \/\[\\s\\u200b"'`\(<\[\{=,;\|\*\\u201c\\u2018\\u00ab\]\/u;/, "every space \\s names, the zero-width space, and Markdown's asterisk are openers to the gate (the underscore never reaches it)");
  assert.match(MOD, /const STAR_CLOSE_RE = \/\(\?::\\d\+\(\?::\\d\+\)\?\|#L\\d\+\(\?:-L\?\\d\+\)\?\)\?\\\*\/y;/, "the closing star, read in place (sticky) past a line suffix");
  assert.match(MOD, /if \(before === "\*"\) \{\n(?:\s*\/\/[^\n]*\n)*\s*if \(tok\.startsWith\("\/"\)\) return false;\n\s*STAR_CLOSE_RE\.lastIndex = ctx\.at \+ tok\.length;\n\s*if \(!STAR_CLOSE_RE\.test\(ctx\.text\)\) return false;/, "a star opens a token that is not /-led and is closed by a star, and nothing else (an anchored ./, ../ or ~/ path is emphasis, not a glob's tail)");
  assert.match(MOD, /const FROM_LINE_RE = \/\^\\s\*from\\s\*\(\?:"\[\^"\]\*"\|'\[\^'\]\*'\)\\s\*;\?\\s\*\$\/;/, "a continuation line is `from` and a quoted specifier, a `;` or not");
  assert.match(MOD, /const CLOSING_FROM_RE = \/\^\[\^"'`\]\*\\\}\\s\*from\\s\*\["'\]\/;/, "or holds `} from \"` with no quote before the brace");
  assert.match(LINKS, /else if \(unit && e\.matches\(unit\) && e\.textContent === ""\) \{ units\.push\(\{ text: "", spans: \[\] \}\); cur = null; \}/, "a unit element with no text is an empty unit in its place: the units are the code view's rows, blank ones included");
  assert.doesNotMatch(MOD, /ctx\.text\.slice\(ctx\.at/, "the closer is read in place, never off a slice of the rest of the unit");
  // the import look-behind reads the line's head only after a keyword stands before the quote, so a quote with none costs the quote, the spaces and one word
  const lb = MOD.split("function importLookBehind(text: string, at: number)")[1].split("\n}\n")[0];
  assert.ok(lb.indexOf("return { start: s, isImport: false }") < lb.indexOf("lastIndexOf(\"\\n\", s - 1)"), "no keyword: return before the line is read back");
  assert.ok(lb.indexOf("if (call) return") < lb.indexOf("lastIndexOf(\"\\n\", s - 1)"), "a call: return before the line is read back");
  assert.match(lb, /const isImport = word === "from" \? STATEMENT_HEAD_RE\.test\(head\) \|\| continuesImport\(lineOf\(text, lineStart, at\)\) : \/\^\\s\*\$\/\.test\(head\);/);
  assert.match(MOD, /const lineEnd = text\.indexOf\("\\n", at\);\n\s*return text\.slice\(lineStart, lineEnd < 0 \? text\.length : lineEnd\);/, "a from's line is read forward to its break, bounded by the line as the look-behind is");
  assert.match(LINKS, /opts\.accept\(tok, \{ text, at: start, inPre: span\.inPre \}\)/, "the walk hands the gate the token's line and offset, and whether it sat in a fenced block (2026-09-12) — nothing above them");
  assert.match(MOD, /const STATEMENT_HEAD_RE = \/\^\\s\*\(\?:import\|export\)\\b\/;/);
  assert.match(LINKS, /for \(const u of textUnits\(root, opts && opts\.unit, skip\)\) \{/, "no unit: every node its own unit, the chat's walk as it was");
  assert.match(LINKS, /const span = spanHolding\(u, start, start \+ tok\.length\);\n\s*if \(!span\) continue;/, "a token across a node's edge is left as it is");
  assert.match(LINKS, /if \(!isUri && opts && opts\.accept && !opts\.accept\(tok, \{ text, at: start, inPre: span\.inPre \}\)\) continue;/);
  assert.match(LINKS, /if \(isUri && opts && opts\.lineSuffix\) \{ const tail = URI_LINE_TAIL_RE\.exec\(tok\); if \(tail\) tok = tok\.slice\(0, tail\.index\); \}/);
  assert.match(LINKS, /export const LINE_SUFFIX_RE = \/\^\(\?::\(\\d\+\)\(\?::\\d\+\)\?\|#L\(\\d\+\)\(\?:-L\?\\d\+\)\?\)\(\?!\[\\w\/\]\)\/;/);
  assert.match(LINKS, /export function markPathLink\(a: HTMLElement, open: string, relative = false\): HTMLElement \{\n\s*const cls = a\.getAttribute\("class"\) \|\| "";\n\s*if \(!\(" " \+ cls \+ " "\)\.includes\(" file-uri-link "\)\) a\.setAttribute\("class", \(cls \? cls \+ " " : ""\) \+ "file-uri-link"\);\n\s*a\.setAttribute\("title", "Open " \+ open\);/, "attributes, so an SVG <a> is marked too");
  assert.match(LINKS, /const a = el\("span", "file-uri-link"\);\n\s*a\.textContent = raw;[^\n]*\n\s*return markPathLink\(a, open, relative\);/, "openPathLink mints and marks");
  assert.match(MOD, /linkifyPathTokens\(root, undefined, \{\n\s*inPre: true,\n\s*accept: \(tok, ctx\) => !insideUrl\(ctx\) && viewerPathGate\(tok, ctx\),\n\s*resolve: \(tok\) => resolveViewerPath\(tok, filePath\),\n\s*lineSuffix: true,\n\s*unit: LINE_UNITS,\n\s*\}\);/, "the viewer runs the chat's walk under its own gate, a line at a time, and never inside a URL of the line");
  assert.match(MOD, /if \(ctx\.text !== lineText\) \{ lineText = ctx\.text; lineUrls = \/https\?:\\\/\\\/\/i\.test\(ctx\.text\) \? urlRanges\(ctx\.text\) : \[\]; \}/, "the line's URLs are found once per line, not per token");
  assert.match(MOD, /for \(const u of textUnits\(root, LINE_UNITS, DEAD_TEXT\)\) \{/, "the URL pass reads the same lines and skips the same text");
  assert.match(MOD, /a\.setAttribute\("target", "_blank"\);\n\s*a\.setAttribute\("rel", "noopener"\);/);
  assert.doesNotMatch(MOD, /innerHTML|outerHTML|insertAdjacentHTML/, "the module builds elements; it never writes markup");
});

test("the dress is light, and in both sheets: the token keeps its colour under a faint dotted underline, solid on hover; a Markdown file link keeps the link ink; a dead link says so", () => {
  for (const [name, css] of [["styles.css", CHAT_CSS], ["feed.css", FEED_CSS]] as const) {
    const rule = (head: string) => { const at = css.indexOf(head); assert.ok(at >= 0, name + ": " + head); return css.slice(at, css.indexOf("}", at) + 1); };
    const base = rule(".fileview-body .file-uri-link, .fileview-body .fv-url {");
    assert.match(base, /color: inherit;/, name + ": the highlight's colour stays");
    assert.match(base, /text-decoration: underline dotted;/); assert.match(base, /text-decoration-color: color-mix\(in srgb, currentColor 35%, transparent\);/);
    assert.match(base, /cursor: pointer;/);
    assert.match(base, /-webkit-user-drag: none; user-select: text;/, "not draggable, and selectable: a press-drag on a link selects the text under it");
    const hover = rule(".fileview-body .file-uri-link:hover, .fileview-body .fv-url:hover {");
    assert.match(hover, /text-decoration: underline;/); assert.match(hover, /text-decoration-color: currentColor;/);
    assert.match(rule(".fileview-md a.file-uri-link {"), /color: var\(--link\); text-decoration: none;/);
    assert.match(rule(".fileview-md a.file-uri-link:hover {"), /text-decoration: underline;/);
    assert.match(rule(".fileview-md a.fv-dead {"), /cursor: help;/);
    assert.doesNotMatch(base + hover, /font-size|--[a-z-]+: /, "no new font size, no new token");
  }
});
