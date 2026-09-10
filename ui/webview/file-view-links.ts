// Links inside a file the viewer shows (the user 2026-09-07): an http(s) URL in the text opens in a new tab, a file
// path opens in the viewer, and a Markdown link's target follows the same two rules. One pass over the TEXT NODES of
// a body the viewer has already built, never over its HTML string: hljs's markup and the sanitizer's verdicts stand,
// no raw file text is ever parsed as markup, and a row's text reads exactly as the file's after the pass (a selection
// over a line with a link in it, and the quote chip it seeds, see the same characters). The pass ADDS elements around
// runs of text and nothing else.
//
// The pass reads a LINE at a time, not a text node (path-links.ts textUnits, under LINE_UNITS: a code view's row, or a
// rendered block whose own line breaks are whitespace to the gate): the highlight wraps a shell or template
// substitution in a span of its own, so `"$HOME/docs/a.md"` reaches the DOM as three text nodes, and a walk over the
// third alone would read `/docs/a.md` as an absolute path that was never written. Over the line's joined text the
// token is `HOME/docs/a.md`, glued to the `$` before it, and the gate below refuses a token glued to anything but an
// opener. A token the highlight cut through (its text in two nodes) is left as it is, never re-read as its pieces, and
// so is a URL; a token inside a URL of its line is the URL's, wrapped or not (a cut URL stays text, and its query can
// carry a path). Text inside an inline SVG is read but never marked (path-links.ts DEAD_TEXT): an HTML element
// inserted into SVG text does not render.
//
// The path grammar is the chat's (path-links.ts: the one matcher, the one span), run with the walk's options for a
// surface that is code rather than prose, plus one gate of its own, viewerPathGate. The chat has the kernel's stat
// verdict to narrow its matches; this surface has no verdict and narrows by shape, and every false link is noise on
// a surface the person reads for hours. The gate's grammar, in full (docs/guide.md says the same in the user's words):
// a token links when it has a slash and its last segment has a letter-led extension of one to eight letters or digits
// (a dotfile only under an anchored start: `/`, `./`, `../`, `~/`); it is not glued to the character before it (the
// line's start, whitespace or an opener must precede it: a quote, a bracket, `=`, `,`, `;`, `|`, or Markdown's `*`
// when the path is the whole emphasised text), which is what cuts `$HOME/docs/a.md`, `${dir}/out.json`,
// `$(ROOT)/src/x.c`, `@scope/pkg/index.js`, `C:/Users/x/file.txt` and `git@host:user/repo.git` down to prose; it is
// not inside a web address on its line; an unanchored token's first segment does not read as a hostname (`www.` or
// dotted labels ending in two or more letters: `www.example.org/docs/index.html`, `example.com/index.html`); an
// unanchored token is not the specifier an import statement or a require call names (`import x from
// "lodash/fp.js"`, `require("pkg/sub.js")`; the English `from` in `Copied from "docs/a.md"` names a file); and a `~`
// start is `~/` (`~user/x.md` names a home the kernel does not expand). A local file:// URI (an empty authority, or
// `localhost`) links as written. A relative token resolves against the shown file's own directory (a file's mentions
// are written from where the file is), an absolute or `~/` one passes to the kernel as written (it expands `~` and
// reads the file's session's machine), and the resolved path is normalized once, here (`docs/../src/app.py` opens,
// and is titled, as `src/app.py`). A `:12` written after a path (or GitHub's `#L12`) rides in the link and the
// viewer scrolls to that line.
//
// What a click does is the viewer's (file-view.ts binds the body's listener: a path link opens the file in the
// viewer, a URL anchor opens itself, a modified click opens either in a tab of its own). This module marks; it binds
// no action.
import { linkifyPathTokens, markPathLink, fileUriToPath, isFileUri, LINE_SUFFIX_RE, DEAD_TEXT, textUnits, spanHolding, rewriteSpan, type TextSpan } from "./path-links";
import { headingSlug } from "./md-links";   // the slug the viewer mints heading ids from (`md-` + slug), so a section link finds its heading
import { userContentTarget } from "./md-sanitize";   // an author's id or name, under the sanitizer's user-content- prefix or bare: the lookup the chat's `#` click reads too

/** The URL anchors this module mints wear this class; the viewer's listener and the sheets key on it. */
export const URL_LINK_CLASS = "fv-url";
/** A Markdown link to a section of the shown document (`#install`): the viewer's listener scrolls to the target
 *  when the document has one, and never lets the click move the hosting document. */
export const FRAG_LINK_CLASS = "fv-frag";
/** An anchor with no target the viewer can follow (the sanitizer removed it; a section the document does not
 *  have): dressed as a dead link, with its title saying why (fail loudly, never a silent dead end). */
export const DEAD_LINK_CLASS = "fv-dead";
export const DEAD_LINK_TITLE = "Not a link the viewer can follow: its target is neither a web address nor a file on the session's machine";
/** A `name:port` target whose name reads as a host (an IPv4 address, `localhost`, a hostname by shape): a host with
 *  a port, which is neither a web address nor a file, said so in place of a file named after the host at a line
 *  numbered after the port. */
export const HOST_PORT_TITLE = "Not a link the viewer can follow: the target is a host with a port, not a file on the session's machine";
/** A relative target that resolves to no path at all. From a file named with a directory (`docs/plan.md`) that is
 *  `../` or `..`: a folder above the file's own, which the viewer cannot name and so cannot open. From a file named
 *  without one (`README.md`, as a chat-relayed relative path opens it) it is `./`, `.` or `docs/../`: the folder the
 *  file is in, which the name does not carry. The title says which. A target that climbs out of a relative name
 *  (`../` from `README.md`) keeps its `..` and is a live link the kernel resolves. */
export const EMPTY_TARGET_TITLE = "Not a link the viewer can follow: the target points above the folder this file is named in, so there is no path to open";
export const SELF_TARGET_TITLE = "Not a link the viewer can follow: the target is the folder this file is in, and the file's name carries no folder, so there is no path to open";
export const emptyTargetTitle = (filePath: string): string => (filePath.includes("/") ? EMPTY_TARGET_TITLE : SELF_TARGET_TITLE);
export const noSectionTitle = (id: string): string => "No heading or anchor named \u201c" + id + "\u201d in this document";

/** The elements whose text is one unit to the pass: a code view's row (.fv-cl), a rendered fenced block's row (.cl,
 *  code-block.ts wrapCodeLines: the rows drop the fence's newlines, so the <pre> alone would read its lines as one), a
 *  rendered block (a paragraph, a list item, a cell, a heading, a quote). Text under none of these is a unit of its own. */
export const LINE_UNITS = ".fv-cl, .cl, p, li, td, th, dt, dd, h1, h2, h3, h4, h5, h6, pre, blockquote, caption, figcaption, summary";

// An http(s) URL in running text: the scheme, then everything up to whitespace or a character no URL carries
// unescaped in prose or code (a quote, an angle bracket, a backtick).
const URL_RE = /https?:\/\/[^\s<>"'`]+/gi;
// Sentence punctuation a URL is followed by, not part of: trimmed from the end, then a closing bracket that has no
// opening partner inside the URL (`(see https://x.y/z)` ends before the paren; a Wikipedia-style
// `https://x.y/Foo_(bar)` keeps its own).
const URL_TRAIL = ".,;:!?'\"";
const PAIRS: Record<string, string> = { ")": "(", "]": "[", "}": "{" };
function trimUrl(u: string): string {
  for (;;) {
    const last = u[u.length - 1];
    if (URL_TRAIL.includes(last)) { u = u.slice(0, -1); continue; }
    const open = PAIRS[last];
    if (open) {
      let depth = 0;
      for (const c of u) { if (c === open) depth++; else if (c === last) depth--; }
      if (depth < 0) { u = u.slice(0, -1); continue; }   // one more closer than opener: it closes the sentence's bracket
    }
    return u;
  }
}
/** `text` cut into runs, each a URL (`href` set) or plain text; the URLs trimmed of trailing punctuation. */
export function urlSegments(text: string): Array<{ text: string; href?: string }> {
  const out: Array<{ text: string; href?: string }> = [];
  let last = 0;
  URL_RE.lastIndex = 0;
  for (let m: RegExpExecArray | null; (m = URL_RE.exec(text));) {
    const u = trimUrl(m[0]);
    if (!u || !/^https?:\/\/[^/?#]+/i.test(u)) { URL_RE.lastIndex = m.index + m[0].length; continue; }   // a bare scheme, no host
    if (m.index > last) out.push({ text: text.slice(last, m.index) });
    out.push({ text: u, href: u });
    last = m.index + u.length;
    URL_RE.lastIndex = last;
  }
  if (!out.length) return [{ text }];
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}
/** The URLs in `text` as [start, end) ranges of it: exactly what urlSegments would wrap. */
export function urlRanges(text: string): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let at = 0;
  for (const s of urlSegments(text)) { if (s.href) out.push([at, at + s.text.length]); at += s.text.length; }
  return out;
}

// The gate's pieces (the grammar is written out in the header). What may stand right before a token: nothing (the
// line's start), whitespace, or an opener; a token glued to anything else is the tail of something the matcher cut
// through. Whitespace is every character \s names, a line break and a no-break space among them (a rendered
// paragraph, list item or fenced block is one unit of text with its line breaks inside it, so a path may start any
// line of it), plus the zero-width space, which text pasted from a chat tool carries and \s leaves out. An asterisk
// is Markdown emphasis in the Raw view (`**docs/a.md**`), the view a `:line` link lands in, and it opens a token
// only when a closing asterisk follows the token and the token is not `/`-led (viewerPathGate, STAR_CLOSE_RE: a
// glob's `**/docs/a.md` and an operand's `w*h/img.size` have no closer, and a glob's tail after its star begins
// with `/`). Not `_`: the matcher's path arm takes an underscore into the token, so `_docs/a.md_` is one token the
// extension test refuses and `_docs/a.md` names a folder called `_docs`; the gate never sees `_` before a token.
// Not `)`, `]`, `:` or `#`: `$(ROOT)/src/x.c`, `git@host:user/repo.git` and `x#/docs/a.md` are glue.
const OPENER_RE = /[\s\u200b"'`(<[{=,;|*\u201c\u2018\u00ab]/u;
// The closing star emphasis puts after its path, read in place at the token's end (sticky); a `:12` or `#L12` may sit between.
const STAR_CLOSE_RE = /(?::\d+(?::\d+)?|#L\d+(?:-L?\d+)?)?\*/y;
const ANCHORED_RE = /^(?:~\/|\.{1,2}\/|\/)/;
// a first segment that reads as a hostname
const WWW_RE = /^www\./i;
const HOST_RE = /^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}$/i;
// The text before a bare specifier in an import: `import x from "`, `import "`, `export * from "`, `require("`,
// `import("`. Read BACKWARDS from the token: the quote, the line's spaces, one paren, more spaces, then the word
// before them, at most a keyword's width. A line break stops every step, so the window is the current line's, and
// its length is bounded by what stands right before the token, never by the unit (a regex over the unit's text
// before the token would slice the whole unit per token, quadratic over a fenced block). `from` and its quote on
// different lines do not read as an import, which no formatter writes. The word alone is not the verdict: `from`
// and `export` are English, and `Copied from "docs/a.md"` in a note names a file. The word counts when the
// statement around it is code: a call (`require("…")`, `import("…")`), a keyword that starts its line (`import
// "pkg/x.css"`), a `from` whose line began with `import` or `export` (`import x from "…"`, `export * from "…"`), or
// a `from` on a line shaped as the continuation of an import opened above it (continuesImport, below). Only then is
// the line read back to its start (and, for a `from`, forward to its end), so a quote with no keyword before it
// still costs the quote, the spaces and one word.
const IMPORT_WORDS = ["import", "export", "from", "require"];
const KEYWORD_MAX = 7;                                   // `require`
const STATEMENT_HEAD_RE = /^\s*(?:import|export)\b/;    // the line began an import or export statement
// A line that continues an import opened on a line above it, told by its own shape and nothing else: the whole line
// is `from` and a quoted specifier, a `;` after it or not (`  from "pkg/x.js";` under `import { a }`, `import * as
// ns`, `import React`, `export *` or `export type { T }`), or it holds `} from "` with no quote before the brace
// (`  a, b } from "pkg/x.js";` under `import {`; Prettier's own `} from "pkg/x.js";`). Reading the rows above such
// a line for the import that opened it finds a shape it missed each time (a blank row between them, a default name
// alone, `import * as ns`, `export *`): the line says enough by itself, and the one price is a prose line of exactly
// `from "docs/a.md"`, which stays text. A `from` with more after its specifier is English wherever it stands: `from
// "pkg/sub.py" import x`, `from "docs/a.md" and kept it`, `# adapted from "docs/a.md"` under `import os`.
const FROM_LINE_RE = /^\s*from\s*(?:"[^"]*"|'[^']*')\s*;?\s*$/;
const CLOSING_FROM_RE = /^[^"'`]*\}\s*from\s*["']/;
const continuesImport = (line: string): boolean => FROM_LINE_RE.test(line) || CLOSING_FROM_RE.test(line);
const isWordCh = (c: string): boolean => /[A-Za-z0-9_]/.test(c);
const isLineSpace = (c: string): boolean => c === " " || c === "\t";
function importLookBehind(text: string, at: number): { start: number; isImport: boolean } {
  let i = at - 1;
  if (i < 0 || !"\"'`".includes(text[i])) return { start: at, isImport: false };   // no quote before the token: nothing to read
  i--;
  while (i >= 0 && isLineSpace(text[i])) i--;
  let call = false;
  if (i >= 0 && text[i] === "(") { call = true; i--; while (i >= 0 && isLineSpace(text[i])) i--; }
  const end = i + 1;
  let s = end;
  while (s > 0 && end - s < KEYWORD_MAX && isWordCh(text[s - 1])) s--;
  const word = text.slice(s, end);
  if (!IMPORT_WORDS.includes(word) || (s > 0 && isWordCh(text[s - 1]))) return { start: s, isImport: false };   // no keyword, or the tail of a longer word
  if (call) return { start: s, isImport: word === "require" || word === "import" };                          // a call: code wherever it stands
  const lineStart = text.lastIndexOf("\n", s - 1) + 1;
  const head = text.slice(lineStart, s);                 // what the line holds before the keyword: an import clause, or prose
  const isImport = word === "from" ? STATEMENT_HEAD_RE.test(head) || continuesImport(lineOf(text, lineStart, at)) : /^\s*$/.test(head);
  return { start: lineStart, isImport };
}
// The line at `lineStart` through its break (or the text's end), read forward from `at`: bounded by the line, as the
// look-behind is, so the pass stays linear over a fenced block.
function lineOf(text: string, lineStart: number, at: number): string {
  const lineEnd = text.indexOf("\n", at);
  return text.slice(lineStart, lineEnd < 0 ? text.length : lineEnd);
}
/** Where the gate's look-behind for the token at `at` begins: the earliest index whose character it reads as text
 *  (one more before it is looked at only as the keyword's word boundary). Never before the line's start. */
export function lookBehindStart(text: string, at: number): number { return importLookBehind(text, at).start; }

/** The viewer's gate over a token the shared shape gates passed; `ctx` is the line's text and the token's offset in
 *  it (the walk hands both over), without which only the token's own shape is judged. */
export function viewerPathGate(tok: string, ctx?: { text: string; at: number }): boolean {
  if (isFileUri(tok)) return true;
  if (!tok.includes("/")) return false;
  if (tok.startsWith("~") && !tok.startsWith("~/")) return false;      // `~user/x.md`: a home the kernel does not expand
  const base = tok.slice(tok.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");
  if (dot < 0 || !/^[A-Za-z][A-Za-z0-9]{0,7}$/.test(base.slice(dot + 1))) return false;
  const anchored = ANCHORED_RE.test(tok);
  if (dot === 0 && !anchored) return false;                            // a dotfile links under an anchored start only
  if (!anchored) {
    const first = tok.slice(0, tok.indexOf("/"));
    if (WWW_RE.test(first) || HOST_RE.test(first)) return false;       // a site, not a folder
  }
  if (ctx) {
    const before = ctx.at > 0 ? ctx.text[ctx.at - 1] : "";
    if (before && !OPENER_RE.test(before)) return false;               // glued to a substitution, a scope, a drive, a host
    if (before === "*") {
      // Emphasis wraps its path in stars on both sides (`*docs/a.md*`, `**docs/a.md**`, `**./scripts/setup.sh**`, a
      // `:line` inside them too). A `/`-led token after a star is a glob's tail (`**/docs/a.md`, `src/*/index.ts`,
      // `packages/*/package.json`), and a token with no closing star is an operand (`w*h/img.size`, `2*docs/times.md`).
      // The glob test is the leading slash, not the anchor: a glob's tail after its star always begins with `/`, and
      // `*./`, `*../`, `*~/` are emphasis and nothing else.
      if (tok.startsWith("/")) return false;
      STAR_CLOSE_RE.lastIndex = ctx.at + tok.length;
      if (!STAR_CLOSE_RE.test(ctx.text)) return false;
    }
    if (!anchored && importLookBehind(ctx.text, ctx.at).isImport) return false;   // a package specifier
  }
  return true;
}

/** `p` with its `.` and `..` segments resolved and doubled slashes dropped, lexically: `/a/b/../c.md` is `/a/c.md`,
 *  `docs/../src/app.py` is `src/app.py`. A `..` above the root stays at the root; one above `~` or above a relative
 *  path's start is kept (`~/../x.md`, `../../x.md`: the kernel resolves those against the session). */
export function normalizePath(p: string): string {
  const abs = p.startsWith("/");
  const segs = p.split("/").filter((s, i, a) => s !== "." && (s !== "" || (i > 0 && i === a.length - 1)));   // a trailing "" keeps a directory's slash
  const out: string[] = [];
  for (const s of segs) {
    if (s === "..") {
      const top = out[out.length - 1];
      if (out.length && top !== ".." && top !== "~") { out.pop(); continue; }
      if (abs) continue;
    }
    out.push(s);
  }
  return (abs ? "/" : "") + out.join("/");
}

/** What a token in `filePath`'s text opens: a URI's own path; an absolute or `~/` path as written; a relative one
 *  joined onto the shown file's directory (`""` for a file named without one, which then resolves against the
 *  session's cwd on the kernel exactly as the file itself did). Normalized once, here. */
export function resolveViewerPath(tok: string, filePath: string): string {
  if (isFileUri(tok)) return normalizePath(fileUriToPath(tok));
  if (tok.startsWith("/") || tok.startsWith("~/")) return normalizePath(tok);
  return normalizePath(filePath.slice(0, filePath.lastIndexOf("/") + 1) + tok);
}

/** Mark the URLs in `root`'s text as anchors that open a new tab (target _blank, rel noopener noreferrer, the URL as
 *  the title), a line at a time (LINE_UNITS). Text already inside a link, and a URL the highlight cut into two
 *  nodes, are left alone. The anchors are not draggable (draggable=false here, `-webkit-user-drag: none` and
 *  `user-select: text` in the sheets): a press-drag that starts on one selects the text under it, as it does on a
 *  path link, instead of starting the browser's link drag; the click that ends the drag finds a selection open inside
 *  the anchor, and every opener it reaches yields to that (path-links.ts selectionOpenIn: the viewer's listener, and
 *  the chat's document-level opener, which runs first). Returns the anchors made. */
export function linkifyUrls(root: HTMLElement): HTMLAnchorElement[] {
  const made: HTMLAnchorElement[] = [];
  const doc = root.ownerDocument || document;
  for (const u of textUnits(root, LINE_UNITS, DEAD_TEXT)) {
    if (!/https?:\/\//i.test(u.text)) continue;
    const marks = new Map<TextSpan, Array<{ start: number; end: number; el: Node }>>();
    let at = 0;
    for (const s of urlSegments(u.text)) {
      const start = at;
      at += s.text.length;
      if (!s.href) continue;
      const span = spanHolding(u, start, at);
      if (!span) continue;
      const a = doc.createElement("a");
      a.className = URL_LINK_CLASS;
      a.href = s.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.title = s.href;
      a.setAttribute("draggable", "false");
      a.textContent = s.text;
      let list = marks.get(span);
      if (!list) { list = []; marks.set(span, list); }
      list.push({ start, end: at, el: a });
      made.push(a);
    }
    for (const [span, list] of marks) rewriteSpan(u, span, list);
  }
  return made;
}

/** Every text node under `root`, document order, through childNodes alone (a stand-in without a tree walker
 *  runs it as the browser does). */
function textNodesOf(root: Node): Text[] {
  const out: Text[] = [];
  const visit = (n: Node) => {
    for (const c of Array.from(n.childNodes || [])) { if (c.nodeType === 3) out.push(c as Text); else if (c.nodeType === 1) visit(c); }
  };
  visit(root);
  return out;
}

/** The whole pass over a text body the viewer built (the code view's <code>, a rendered markdown box): URLs first,
 *  then the shared path walk under the viewer's gate and resolution, both a line at a time. A wrapped URL is dead text
 *  to the walk; one the highlight cut across two nodes stays text, and the walk over the line would then read a
 *  path-shaped query value inside it as an absolute path (`https://x.y/q?x=/docs/a.md/$V`: hljs puts `$V` in a span
 *  of its own), so the gate also refuses a token inside any URL of its line, wrapped or not. The line's URLs are
 *  found once per line, not per token: the walk asks about a line's tokens in a row. A body with no text (a stand-in
 *  that parses no HTML, an empty file) is left as it is. */
export function linkifyFileText(root: HTMLElement, filePath: string): void {
  if (!textNodesOf(root).length) return;
  linkifyUrls(root);
  let lineText: string | null = null, lineUrls: Array<[number, number]> = [];
  const insideUrl = (ctx: { text: string; at: number }): boolean => {
    if (ctx.text !== lineText) { lineText = ctx.text; lineUrls = /https?:\/\//i.test(ctx.text) ? urlRanges(ctx.text) : []; }
    return lineUrls.some(([s, e]) => ctx.at >= s && ctx.at < e);
  };
  linkifyPathTokens(root, undefined, {
    inPre: true,
    accept: (tok, ctx) => !insideUrl(ctx) && viewerPathGate(tok, ctx),
    resolve: (tok) => resolveViewerPath(tok, filePath),
    lineSuffix: true,
    unit: LINE_UNITS,
  });
}

/** A Markdown link's destination, put in the form the sanitizer keeps and this module reads, BEFORE marked makes
 *  the HTML (mdBlock hands viewerWalkTokens to marked). DOMPurify drops an href whose first segment holds a colon
 *  (`notes.md:7` reads as a scheme) and one whose scheme is `file:`, and an anchor it stripped is a dead label the
 *  module can no longer sort. So a same-directory `name.ext:12` becomes `./name.ext:12`, and a local file:// URI
 *  becomes its path. Anything else is left as written: a real scheme is the browser's, and a URI on another host
 *  is not a path (path-links.ts isFileUri); those the sanitizer still removes render as dead links that say why. */
// `api.example.com:8443` has the `name.ext:N` shape and is a host with a port. Which names read as a host, by shape
// alone (whether the file exists is unknowable here): `www.` first; or dotted labels that read as a hostname (HOST_RE,
// the gate's own test) whose last label is a top-level domain, whatever the label count: one of the generic and
// reserved TLDs below (`example.com`, `docs.example.io`, `x.internal`), or a two-letter country code under a
// second-level label the registries use (`co.uk`, `com.au`, `ac.jp`: `sub.example.co.uk`). Everything else is a
// file, whether or not the chat's bare-name gate knows its extension: `notes.md`, `app.test.ts`, `archive.tar.gz`,
// `app.component.vue`, `styles.module.less`, `report.final.docx`, `init.el`. A host is left as written: the
// sanitizer removes the target, and the anchor renders dead with the reason.
const SAME_DIR_LINE_RE = /^([^/:?#]*\.[A-Za-z][A-Za-z0-9]{0,7}):\d+(?::\d+)?(?:[?#].*)?$/;
const TLD_LIKE = new Set(["com", "net", "org", "edu", "gov", "mil", "int", "io", "dev", "app", "ai", "co", "info", "biz", "xyz", "cloud", "online", "site", "tech",
                          "invalid", "test", "example", "local", "localhost", "internal", "arpa", "onion"]);
const SECOND_LEVEL = new Set(["co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go"]);
export function isHostName(name: string): boolean {
  if (WWW_RE.test(name)) return true;
  if (!HOST_RE.test(name)) return false;
  const labels = name.toLowerCase().split(".");
  const last = labels[labels.length - 1];
  return TLD_LIKE.has(last) || (last.length === 2 && labels.length >= 3 && SECOND_LEVEL.has(labels[labels.length - 2]));
}
const IPV4_RE = /^\d{1,3}(?:\.\d{1,3}){3}$/;
const isHostWithPort = (name: string): boolean => IPV4_RE.test(name) || name.toLowerCase() === "localhost" || isHostName(name);
export function viewerLinkTarget(href: string): string {
  if (isFileUri(href)) return fileUriToPath(href);
  const m = SAME_DIR_LINE_RE.exec(href);
  if (m && !isHostName(m[1])) return "./" + href;
  return href;
}
export function viewerWalkTokens(token: { type: string; href?: string | null }): void {
  if (token.type === "link" && typeof token.href === "string") token.href = viewerLinkTarget(token.href);
}

/** The element a section link's `id` names in `root`. An author's own id or name reaches the DOM under the sanitizer's
 *  `user-content-` prefix (md-sanitize.ts, the rule GitHub applies), so the lookup is the sanitizer's own: the first
 *  element whose id is the prefixed spelling or the bare one, else the first `<a name>` with either (the README idiom
 *  for a stable anchor, `<a name="install"></a>` above a heading, which marked passes through), in document order, as
 *  a browser's fragment rule has it (userContentTarget, which the chat's `#` click reads too). Else the heading whose
 *  slug it is: marked gives headings no ids, and the viewer mints one per heading as `md-` + its GitHub slug (mdBlock,
 *  after the sanitize, so never prefixed), so `#Evidence Results`, `#evidence-results` and the percent-encoded spelling
 *  all name md-evidence-results. Mark time and click time both ask here (file-view.ts scrollToFragment). */
export function fragmentTarget(root: ParentNode, id: string): Element | undefined {
  return userContentTarget(root, id)
    || root.querySelector('[id="md-' + headingSlug(id) + '"]') || undefined;   // the slug's alphabet needs no escaping
}

const withClass = (a: Element, cls: string): void => {
  const have = a.getAttribute("class") || "";
  if (!(" " + have + " ").includes(" " + cls + " ")) a.setAttribute("class", (have ? have + " " : "") + cls);
};

/** A rendered Markdown link's target, sorted the way the text is: a scheme (http:, https:, mailto:, …) or a
 *  protocol-relative `//host` opens a new tab, as the viewer always had it; a query alone (`?x=1`) is a web-style
 *  link to the page's own address and opens a new tab too (the viewer's document is never navigated); a fragment
 *  alone (`#section`) is the viewer's (FRAG_LINK_CLASS: the listener scrolls to the element with that id, or the
 *  `<a name>` of that name, or the heading of that slug, when the document holds one, else the title says there is
 *  none); anything else names a file, relative to the shown one, and the anchor becomes a path link (the shared shape
 *  on its own <a>, so `[**bold** text](docs/x.md)` keeps its label). marked percent-encodes a destination
 *  (`my%20notes.md`), so it is decoded first; a `#L12` or `:12` on the target is the line; any other `#fragment`
 *  (`report.md#results`) rides as the section to land on once the file is open (data-frag); a `?query` is dropped
 *  from the path. The href comes off a path link: a browser must not follow it, and the chat's document-level opener
 *  reads only anchors with one. An anchor the sanitizer left without an href (a scheme it refuses, a file on another
 *  host) is dressed dead with the reason in its title, unless it is a named target and never was a link; so is a
 *  file target that resolves to no path (`[up](../)` from a file named without a directory). Every attribute is set
 *  as one (an inline SVG's <a> has no target, rel, className or title property to write). */
export function linkMarkdownAnchors(root: HTMLElement, filePath: string): void {
  root.querySelectorAll("a").forEach((node) => {
    const a = node as HTMLElement;
    const href = a.getAttribute("href");
    if (href === null) {
      if (a.hasAttribute("name") || a.hasAttribute("id")) return;
      withClass(a, DEAD_LINK_CLASS);
      a.setAttribute("title", DEAD_LINK_TITLE);
      return;
    }
    if (href.startsWith("#")) {
      let id = href.slice(1);
      try { id = decodeURIComponent(id); } catch { /* a malformed escape: the spelling as written */ }
      const hit = id ? fragmentTarget(root, id) : undefined;
      withClass(a, FRAG_LINK_CLASS);
      if (hit) { a.dataset.frag = id; a.setAttribute("title", "Go to " + id); }
      else { withClass(a, DEAD_LINK_CLASS); a.setAttribute("title", noSectionTitle(id)); }
      return;
    }
    // `127.0.0.1:3000`, `localhost:8080`, `example.com:8443`: a host with a port, which reads as a scheme to the test
    // below (and to the sanitizer, which removes all but the digit-led one; the IPv4 address would reach the path arm
    // and link a file named 127.0.0.1 at line 3000). A hostname by shape, an IPv4 address or `localhost` before a
    // port is a dead link that says so.
    const hostPort = /^([^/?#:]+):\d+(?:[/?#]|$)/.exec(href);
    if (hostPort && isHostWithPort(hostPort[1])) {
      a.removeAttribute("href");
      withClass(a, DEAD_LINK_CLASS);
      a.setAttribute("title", HOST_PORT_TITLE);
      return;
    }
    if (href.startsWith("//") || /^[a-z][a-z0-9+.-]*:/i.test(href)) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener");
      return;
    }
    let dest = href;
    try { dest = decodeURI(href); } catch { /* a malformed escape: the spelling as written */ }
    const cut = dest.search(/[?#]/);
    const tail = cut >= 0 ? dest.slice(cut) : "";
    let pathPart = cut >= 0 ? dest.slice(0, cut) : dest;
    // a line on the path itself (`a.md:12`) or in the fragment (`a.md#L12`); any other fragment (`report.md#results`,
    // after a `?query` or not) is the section to land on once the file is open
    const hashAt = tail.indexOf("#");
    const hash = hashAt >= 0 ? tail.slice(hashAt) : "";
    let line: string | null = null, frag: string | null = null;
    const colon = /:(\d+)(?::\d+)?$/.exec(pathPart);
    if (colon) { line = colon[1]; pathPart = pathPart.slice(0, colon.index); }
    else if (hash) { const m = LINE_SUFFIX_RE.exec(hash); if (m && m[2]) line = m[2]; else if (hash.length > 1) frag = hash.slice(1); }
    if (!pathPart) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
      return;
    }
    a.removeAttribute("href");                          // the browser must not follow it, whichever way it is dressed
    const open = resolveViewerPath(pathPart, filePath);
    if (!open) {                                        // `[up](../)` from `docs/plan.md`, `[here](./)` from `README.md`: no path to open, said so, never a silent click
      withClass(a, DEAD_LINK_CLASS);
      a.setAttribute("title", emptyTargetTitle(filePath));
      return;
    }
    markPathLink(a, open, true);
    if (line) { a.dataset.line = line; a.setAttribute("title", a.getAttribute("title") + ":" + line); }
    else if (frag) { a.dataset.frag = frag; a.setAttribute("title", a.getAttribute("title") + "#" + frag); }
  });
}
