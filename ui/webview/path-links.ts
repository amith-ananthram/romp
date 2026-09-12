// The file-path matcher the chat's transcript links are made from, as a module any surface can import. It
// lived inside render.ts, the chat's entry, which exports nothing, so the file viewer (file-view.ts), which
// shows text with paths in it, could reach the matcher only by copying it, and two copies drift. Lifted, not
// copied: one regex, one set of gates, one span shape, so the kernel's tokenizer parity
// (tests/fixtures/path_token_parity.json) and the chat's link behaviour keep a single source. The viewer runs
// the same walk with options (PathLinkOptions, below) rather than a matcher of its own.
//
// This module MATCHES and MARKS; it binds no action. Every link it emits is a `.file-uri-link` element
// carrying data-path (what a click opens: for a shortened mention the kernel's fixed target, not the token)
// and data-rel="1" when the token was a bare path rather than a file:// URI (so a resolver uses a session's
// cwd). What a click does is the hosting document's call: render.ts binds openPath per span (the editor in VS
// Code, the viewer on the web) and paints its figure previews from the hits; the file viewer reads the clicks
// on its body (file-view.ts). The listeners this module puts on a link are about focus, not the action: Enter
// or Space on a focused link clicks it, so the host's click handler is reached from the keyboard too
// (pathLinkKey), and a mouse press does not focus the link, so a click leaves focus where a click on plain
// text leaves it (pathLinkPress / pathLinkRelease). A document that cannot act on a click should not call
// this: a link that does nothing is worse than text.

function el(tag: string, cls?: string): HTMLElement { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

// A file:// URI that names a path on THIS machine: an empty authority (`file:///a/b`) or `localhost`
// (`file://localhost/a/b`), then the path. `file://host/a/b` names a file on another host and is not a local
// path: stripping its scheme used to leave `host/a/b`, a relative path the opener joined onto a session's cwd.
// Such a token is prose to the walk, and fileUriToPath returns it as written.
const FILE_URI_RE = /^file:\/\/(?:localhost)?(?=\/)/i;
export function isFileUri(tok: string): boolean { return FILE_URI_RE.test(tok); }
// A local file:// URI → its filesystem path: strip the scheme (and a `localhost`), percent-decode. file:///a/b → /a/b.
export function fileUriToPath(uri: string): string {
  if (!isFileUri(uri)) return uri;           // not a local URI: nothing to strip (the callers gate on isFileUri)
  let p = uri.replace(FILE_URI_RE, "");      // file:///Users/… → /Users/…
  try { p = decodeURIComponent(p); } catch { /* malformed %-escape — use verbatim */ }
  return p;
}
// A <span> is not a control: it has no tab stop and no activation key, so a keyboard user could reach a path
// link only by leaving for the pointer. Enter or Space on a focused link clicks it; the click is still the
// host's (render.ts's per-span binder, the viewer's body listener), this only gives the keyboard the route a
// pointer has. Both keys, as the dashboard's other keyboard-activated rows take them (render.ts); Space is
// prevented so it does not also scroll the pane. A Cmd/Ctrl held with the key rides into the click as the
// same modifier, so a host that reads a modified click as "in a tab of its own" (the file viewer) hears it
// from the keyboard too; element.click() carries no modifiers, so that case dispatches the click itself.
function pathLinkKey(e: KeyboardEvent): void {
  if (e.key !== "Enter" && e.key !== " ") return;
  e.preventDefault();
  const a = e.currentTarget as HTMLElement;
  if (e.metaKey || e.ctrlKey) a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, metaKey: e.metaKey, ctrlKey: e.ctrlKey }));
  else a.click();
}
// A tab stop is focusable by the pointer too, and that is the one thing the plain span it replaced was not:
// a mouse click left focus on the body, a click on a tabindex span leaves it on the link. The chat's keyboard
// model reads the difference (Enter from the bare transcript, nothing focused, drops the cursor into the
// message box; render.ts), and the viewer a click opens takes no focus, so after Escape closed it the next
// Enter would find the link still focused and open the file AGAIN instead of the message box; a selection
// dragged FROM a link would meet the same fate on Enter, and in Chromium the focus change itself ends that
// drag's selection before it begins. So a mouse press does not focus the link at all: the browser focuses the
// span as the mousedown's default action, run after its listeners, so the press drops the tabindex attribute
// (a span without one is not focusable) and focus lands where a click on plain text puts it, the body, with
// the click and the selection the press starts untouched. The attribute comes back on the events that end the
// press on this span: mouseup (a click), mouseleave (a drag that goes on elsewhere), contextmenu and dragstart
// (a menu or a native drag took the pointer, and the mouseup may never reach the page). Between them the link
// is out of the tab order, for the length of a press; a keyboard focus meets no press and stays, and Enter and
// Space then activate, as an <a> does. Not mousedown.preventDefault(), which would also keep a selection from
// starting on the link (the text is meant to be selectable in place); not a blur on the focus event, which in
// Chromium clears the selection the press made (a double-click's word). A span already focused (by Tab, then
// clicked) is blurred by the press itself, since the browser fires no focus for it.
function pathLinkPress(e: MouseEvent): void {
  const a = e.currentTarget as HTMLElement;
  a.removeAttribute("tabindex");                // not focusable for the rest of this press
  if (document.activeElement === a) a.blur();   // the press ends a keyboard focus too: the browser would not re-focus it
}
function pathLinkRelease(e: Event): void {
  const a = e.currentTarget as HTMLElement;
  if (!a.hasAttribute("tabindex")) a.tabIndex = 0;
}
// A VERBATIM file link, marked for whoever hosts it. `raw` is shown as written (selectable/copyable in
// place); `open` is what gets opened. A bare file:// can't be followed by the browser from the http
// dashboard (blocked scheme) and a VS Code editor won't render a PDF, so it is routed, never navigated.
// `relative` bare paths are resolved against a session's cwd by whoever opens them: a relative
// `design/foo.md` is relative to the repo the agent runs in, not the kernel's cwd (the user 2026-07-06).
export function openPathLink(raw: string, open: string, relative = false): HTMLElement {
  const a = el("span", "file-uri-link");
  a.textContent = raw;                       // shown exactly as written, selectable/copyable in place
  return markPathLink(a, open, relative);
}
// The link's SHAPE on an element the caller already has: the class, the title, the tab stop, the keyboard and
// pointer handlers, and the target's data. openPathLink mints a span and marks it; the file viewer marks a
// Markdown link's own <a> (its label keeps its nested formatting) when the link's target is a file
// (file-view-links.ts). The class is appended as a string so an element that already wears one keeps it. The
// class and the title are written as ATTRIBUTES: on an SVG <a> (inline SVG in rendered Markdown keeps its
// anchors through the sanitizer) `className` is a read-only animated string and `title` is no property at
// all, so property writes would be silent no-ops there; setAttribute reaches both element kinds.
export function markPathLink(a: HTMLElement, open: string, relative = false): HTMLElement {
  const cls = a.getAttribute("class") || "";
  if (!(" " + cls + " ").includes(" file-uri-link ")) a.setAttribute("class", (cls ? cls + " " : "") + "file-uri-link");
  a.setAttribute("title", "Open " + open);
  a.tabIndex = 0;                            // in the tab order, like the <a> it stands in for…
  a.role = "link";                           // …and announced as one (the ARIA IDL attribute)
  a.onkeydown = pathLinkKey;                 // Enter / Space → this span's click, whoever handles it
  a.onmousedown = pathLinkPress;             // a mouse press does not focus it: the tabindex is off for the press…
  a.onmouseup = a.onmouseleave = a.oncontextmenu = a.ondragstart = pathLinkRelease;   // …and back once the press has ended on this span
  a.dataset.path = open;
  if (relative) a.dataset.rel = "1";
  return a;
}
export function fileUriLink(uri: string): HTMLElement { return openPathLink(uri, fileUriToPath(uri)); }

/** A selection left open inside `el` when a click arrives: the click ends a press-drag-release that selected
 *  text (a press on text collapses the selection first, so a plain click never sees one), and the selection is
 *  what the person gets, not the link under it. Every opener a link's click can reach reads this and yields:
 *  the viewer's body listener over its box, and the chat's document-level anchor opener over the anchor itself,
 *  which runs first, at the capture phase. In the viewer a press-drag inside a path link (a span) selects its text,
 *  and so does a press held on a URL anchor before it drags (Chromium's rule for a link, which is not draggable
 *  there: an immediate drag on one selects nothing); the chat's own anchors are draggable and send no such click. */
export function selectionOpenIn(el: Node): boolean {
  const sel = window.getSelection();
  return !!sel && !sel.isCollapsed && el.contains(sel.anchorNode);
}
// Is this bare token (trailing punctuation already stripped) a file path worth linkifying? Requires a slash
// and EITHER an absolute/anchored start (/, ~/, ./, ../) OR a file extension on the final segment — so
// "and/or", "TCP/IP", "24/7", "read/write" stay as prose. URL-ish tokens (a ':' or '//') are rejected;
// http(s) links are already <a> (skipped) — this just guards a rare un-autolinked one.
export function looksLikeFilePath(tok: string): boolean {
  if (tok.includes(":") || tok.includes("//") || !tok.includes("/")) return false;
  if (/^(?:~\/|\.{1,2}\/|\/)/.test(tok)) return true;                        // absolute or anchored (/, ~/, ./, ../)
  return /\.[A-Za-z0-9]{1,8}$/.test(tok.slice(tok.lastIndexOf("/") + 1));    // relative → the last segment has an extension
}
// A BARE filename (no slash — `power2_watts.pdf`) is linkified ONLY inside inline <code> (the user
// 2026-07-17: a reply listing its output files wasn't clickable). Backticks are where agents put
// filenames, and the KNOWN-extension gate keeps backticked dotted identifiers (`np.array`, `s.color`,
// `romp.kernelPort`) and version numbers (`0.4.293`) reading as prose — an unknown extension stays text.
export const BARE_FILE_EXTS = new Set([
  "md", "txt", "rst", "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "json", "jsonl", "csv", "tsv",
  "pdf", "png", "jpg", "jpeg", "gif", "svg", "webp", "html", "htm", "css", "scss", "sh", "bash", "zsh",
  "bats", "yaml", "yml", "toml", "ini", "cfg", "conf", "xml", "ipynb", "rs", "go", "java", "c", "h",
  "cpp", "hpp", "cc", "rb", "php", "sql", "log", "lock", "tex", "bib", "zip", "tar", "gz", "tgz",
  "mp4", "mov", "mp3", "wav", "vsix", "plist", "diff", "patch",
]);
export function looksLikeBareFileName(tok: string): boolean {
  if (tok.includes("/") || tok.includes(":")) return false;
  const dot = tok.lastIndexOf(".");
  if (dot <= 0) return false;                                                // needs a name before the extension
  return BARE_FILE_EXTS.has(tok.slice(dot + 1).toLowerCase());
}
// One finder covers the file: scheme, the slashed-path alternative, and the bare-filename alternative.
// The kernel's _path_tokens is a port of it (kernel.py), and both sides run the same fixture: a token
// the kernel never saw is a verdict the client can never look up. Its TEXT is that contract; how it is
// run is PathTokenScanner's business (below), since the g-flag loop it was written for is quadratic.
export const CLICKABLE_PATH_RE = /file:\/\/\/?[^\s<>"'`)]+|[~.\w\-]*\/[~.\w\-/]*[\w\-]|[\w\-][\w\-.]*\.[A-Za-z0-9]{1,8}/gi;
// Trailing sentence punctuation is left out of a token, not swallowed by it ("see a/b.md." links a/b.md).
// The characters are the source; the regex is built from them.
export const TRAILING_PUNCT = ".,;:!?)]}>\"'`";
export const TRAILING_PUNCT_RE = new RegExp("[" + TRAILING_PUNCT.replace(/[\\\]^-]/g, "\\$&") + "]+$");
// The token's trailing punctuation, found from the END. TRAILING_PUNCT_RE's `+$` is tried from every position
// of the token and backs off one character at a time when the end is not there, which is quadratic on a long
// token made of punctuation: a token of 40K dots and a slash (the path arm matches it whole) costs over a
// second in the trim alone. Shaped like the regex's match, [the run] or null, because that is how the walk
// reads it.
function trailingPunct(tok: string): [string] | null {
  let i = tok.length;
  while (i > 0 && TRAILING_PUNCT.includes(tok[i - 1])) i--;
  return i < tok.length ? [tok.slice(i)] : null;
}

// ── a linear-time driver for CLICKABLE_PATH_RE ────────────────────────────────────────────────────
// Run with the g flag, the engine restarts at every position, and on an unbroken run of word characters both
// path arms scan to the run's end and back before failing: quadratic in the run's length. One slash plus a
// 40K-character run of hex, words or dashes (a hash, a separator line, a minified dump) costs seconds, per text
// node, on the main thread; the viewer shows whole files, where such lines are ordinary. The regex's text is
// the kernel's parity contract and stays; this drives it in linear time. It tries the three arms in the
// regex's own order at each position, as sticky regexes cut from the one source, and remembers what a failure
// PROVES about the positions ahead:
//   - the path arm `[~.\w\-]*\/[~.\w\-/]*[\w\-]` failing at i fails at every later position of the same run of
//     [~.\w\-/] characters: the first slash it can reach from there is the same one or a later one, and the
//     word character it needs after that slash is drawn from a smaller suffix of the run;
//   - the bare arm `[\w\-][\w\-.]*\.[A-Za-z0-9]{1,8}` failing at i fails at every later position of the same
//     run of [\w\-.] characters: the dot-then-alphanumeric it needs is drawn from a smaller suffix;
//   - the URI arm starts only at an f or F, and its `file:` cannot lie inside either run (a colon is in
//     neither class), so a dead run's positions are tried for it as cheaply as any other.
// Each arm therefore scans a run at most once beyond its matches in it, and the text costs time linear in
// its length. The matches are exactly the regex's: pinned by a differential test over the regex itself at
// every start position, and by the kernel parity fixture (path-links.test.ts).
const [URI_ARM, PATH_ARM, BARE_ARM] = CLICKABLE_PATH_RE.source.split("|").map((arm) => new RegExp(arm, "iy"));
const isWordCh = (c: number): boolean => (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || c === 95;   // \w
const isBareStartCh = (c: number): boolean => isWordCh(c) || c === 45;              // [\w\-]
const isBareCh = (c: number): boolean => isBareStartCh(c) || c === 46;              // [\w\-.]
const isPathCh = (c: number): boolean => isBareCh(c) || c === 126 || c === 47;      // [~.\w\-/]
function runEnd(text: string, i: number, inRun: (c: number) => boolean): number {
  while (i < text.length && inRun(text.charCodeAt(i))) i++;
  return i;
}
export class PathTokenScanner {
  private pathDead = 0;   // the path arm is proven to fail at every position before this one
  private bareDead = 0;   // the bare arm likewise
  constructor(private readonly text: string) {}
  /** The first match at or after `from`, as [start, end): what CLICKABLE_PATH_RE.exec finds with
   *  lastIndex = from, or null. Successive calls must not move `from` backwards. */
  next(from: number): [number, number] | null {
    const t = this.text, n = t.length;
    for (let i = from; i < n; i++) {
      const c = t.charCodeAt(i);
      if (c === 0x66 || c === 0x46) {                       // f / F: the only characters a URI can start at
        URI_ARM.lastIndex = i;
        if (URI_ARM.test(t)) return [i, URI_ARM.lastIndex];
      }
      if (!isPathCh(c)) continue;                           // neither path arm can start here
      if (i >= this.pathDead) {
        PATH_ARM.lastIndex = i;
        if (PATH_ARM.test(t)) return [i, PATH_ARM.lastIndex];
        this.pathDead = runEnd(t, i, isPathCh);
      }
      if (isBareStartCh(c) && i >= this.bareDead) {
        BARE_ARM.lastIndex = i;
        if (BARE_ARM.test(t)) return [i, BARE_ARM.lastIndex];
        this.bareDead = runEnd(t, i, isBareCh);
      }
    }
    return null;
  }
}

/** One link the walk emitted: the element, what it opens, and whether the kernel stat'd that target this
 *  build (a fixed mention). The chat's figure pass wants exactly these. */
export interface PathLinkHit { el: HTMLElement; open: string; verified: boolean; inPre: boolean }   // inPre: the token sat in a fenced block

// `opts` is how a surface whose text is not chat prose runs the same walk (the file viewer, file-view-links.ts).
// `inPre` walks the text inside <pre> too (the viewer's code body IS one); `accept` narrows every non-URI token
// that passed the shape gates (the kernel's map narrows a chat message the same way; a surface with no kernel
// verdict brings its own gate), and is handed the text the token was found in and the token's offset there, so
// a gate can read the token's line; `resolve` names what a token opens when the surface knows its own place
// (the viewer joins a relative token onto the shown file's directory; a file:// URI's own path, absolute, passes
// through it too); `lineSuffix` reads a `:12` (or GitHub's
// `#L12`) right after a token into the link (data-line) instead of leaving it as prose, off a file:// URI too
// (the URI arm's own grammar admits a colon, so the suffix rode inside the token there); `unit` names the
// element whose text nodes are scanned as ONE string (the viewer's row: a highlight's spans cut a line's text
// into several nodes, and a token is what the LINE says, never what one node says). Absent, the walk is
// exactly the chat's.
// `preVerified` (the chat, 2026-09-12): with `inPre` on, a token INSIDE a <pre> links only on the kernel's verdict — a
// fenced block is verbatim material, so a path there links when the kernel has stat'd the file, never on shape alone
// as prose may; `accept`'s ctx says whether the token sat in a <pre>, so a surface can gate its code differently.
export interface PathLinkOptions {
  inPre?: boolean;
  preVerified?: boolean;
  accept?: (tok: string, ctx: { text: string; at: number; inPre: boolean }) => boolean;
  resolve?: (tok: string) => string;
  lineSuffix?: boolean;
  unit?: string;
}
// A line reference written after a path: `path:12`, `path:12:4` (a column, dropped), `path#L12`, `path#L12-L20`
// (a range; its first line). Not followed by a word character or a slash, so `x/a.md:12abc` keeps its prose.
export const LINE_SUFFIX_RE = /^(?::(\d+)(?::\d+)?|#L(\d+)(?:-L?\d+)?)(?![\w/])/;
// The same reference at the END of a token (a file:// URI took it into itself): where it starts.
const URI_LINE_TAIL_RE = /(?::\d+(?::\d+)?|#L\d+(?:-L?\d+)?)$/;
// The same reference AT a position of the unit's text (sticky, lastIndex set): read in place, so the walk never
// slices the rest of the text per token, which is quadratic over a body that is one unit.
const LINE_SUFFIX_AT_RE = new RegExp(LINE_SUFFIX_RE.source.replace(/^\^/, ""), "y");

/** One text node's place in its unit's joined text. `dead`: inside a link (or, in the chat, a fenced block): the
 *  scan READS it, so a token glued to it is seen whole, but never marks in it. */
export interface TextSpan { tn: Text; start: number; end: number; dead: boolean; inCode: boolean; inPre: boolean }
export interface TextUnit { text: string; spans: TextSpan[] }
/** The ancestors whose text no walk marks in, whatever the surface: a link already made, and an inline SVG (an
 *  HTML span or anchor put inside SVG text does not render, so a mark there would make the token vanish from the
 *  figure). The text is still READ, so a token glued to it is seen whole. */
export const DEAD_TEXT = "a, .file-uri-link, svg";
/** The text under `root` cut into units: under `unit` (a selector), the consecutive text nodes sharing their
 *  nearest such ancestor, joined; a node under none, or with no selector, is a unit of its own. A `<br>` ends a
 *  unit too: it is a line break, and a token never crosses one (`a<br>docs/a.md` is not `adocs/a.md`). A unit
 *  element with no text under it is an EMPTY unit, in its place: a code view's blank row is
 *  `<span class=fv-cl><span class=fv-ct></span></span>`, and the units are the view's rows, blank ones included.
 *  `skip` names the ancestors whose text is dead to marking. */
export function textUnits(root: HTMLElement, unit: string | undefined, skip: string): TextUnit[] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  const units: TextUnit[] = [];
  let cur: Element | null = null;
  let broke = false;                                    // a <br> passed since the last text node
  let n: Node | null;
  while ((n = walker.nextNode())) {
    if (n.nodeType === 1) {
      const e = n as Element;
      if (/^br$/i.test(e.tagName)) broke = true;
      else if (unit && e.matches(unit) && e.textContent === "") { units.push({ text: "", spans: [] }); cur = null; }   // a blank line; the text after it starts a unit of its own
      continue;
    }
    const tn = n as Text;
    const p = tn.parentElement;
    const u = unit && p ? p.closest(unit) : null;
    let last = units[units.length - 1];
    if (!last || u === null || u !== cur || broke) { last = { text: "", spans: [] }; units.push(last); cur = u; }
    broke = false;
    const start = last.text.length;
    last.text += tn.data;
    last.spans.push({ tn, start, end: last.text.length, dead: !!p?.closest(skip), inCode: !!p?.closest("code"), inPre: !!p?.closest("pre") });
  }
  return units;
}
/** The span holding [start, end) whole and open to marking, else null: a match that crosses a node's edge (a
 *  highlight span cut through it) or lies in a link is left as it is, never re-read as its pieces. A binary
 *  search over the spans (they are in text order): a highlighted body is one unit of thousands of spans, and a
 *  walk over them per token would be quadratic. */
export function spanHolding(u: TextUnit, start: number, end: number): TextSpan | null {
  const spans = u.spans;
  let lo = 0, hi = spans.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1, s = spans[mid];
    if (start < s.start) hi = mid - 1;
    else if (start >= s.end) lo = mid + 1;
    else return !s.dead && end <= s.end ? s : null;
  }
  return null;
}
/** Replace `span`'s text node with its text, the `marks` (each an element standing for [start, end) of the
 *  unit's text, in order) in place of the ranges they cover. */
export function rewriteSpan(u: TextUnit, span: TextSpan, marks: Array<{ start: number; end: number; el: Node }>): void {
  const frag = document.createDocumentFragment();
  let last = span.start;
  for (const m of marks) {
    if (m.start > last) frag.appendChild(document.createTextNode(u.text.slice(last, m.start)));
    frag.appendChild(m.el);
    last = m.end;
  }
  if (last < span.end) frag.appendChild(document.createTextNode(u.text.slice(last, span.end)));
  span.tn.replaceWith(frag);
}

// Mark bare file:// URLs AND bare file paths inside `root`'s text, a relative `design/foo.md` too,
// resolved against a session's cwd by whoever opens it (the user 2026-07-06). marked doesn't autolink these
// and DOMPurify strips the file: scheme, so without this they read as dead text. Inside INLINE <code> a
// slash-less filename with a known extension marks too; only FENCED <pre> blocks, text already inside a link
// and text inside an inline SVG (DEAD_TEXT) are skipped. Text nodes only: an element the caller already made
// a link stays one. Trailing sentence punctuation is left out, not swallowed.
// `pathLinks` (the user 2026-08-09): the kernel's verdict on every path-shaped token in this text
// (build_session's _path_links — tier 1 exact stat, tiers 2/3 a unique repo-list match that FIXES a
// shortened mention to its real file). When the map is present, a token links ONLY if it's in the map,
// and it opens the map's value — so `render.js` in prose stops 404ing, and hover shows the real target.
// Every shape gate still applies; the map only ever narrows. No map at all (an old kernel, a cached
// payload, a surface the kernel never judged) keeps shape-only linking. file:// URIs are explicit absolute
// paths — never gated on the map. Returns the hits in document order, so a caller's "first mention" is the
// walk's first.
export function linkifyPathTokens(root: HTMLElement, pathLinks?: Record<string, string>, opts?: PathLinkOptions): PathLinkHit[] {
  const hits: PathLinkHit[] = [];
  const skip = opts && opts.inPre ? DEAD_TEXT : DEAD_TEXT + ", pre";   // a link, an SVG, or (a surface that asks for none) a fenced code block
  for (const u of textUnits(root, opts && opts.unit, skip)) {
    if (u.spans.every((s) => s.dead)) continue;
    const text = u.text;
    const anyCode = u.spans.some((s) => s.inCode && !s.dead);            // inline code: where bare filenames may link
    if (!text.includes("/") && !(anyCode && text.includes("."))) continue;   // cheap pre-filter: no slash (and, in code, no dot) → nothing here
    const scan = new PathTokenScanner(text);
    const marks = new Map<TextSpan, Array<{ start: number; end: number; el: Node }>>();
    let from = 0, m: [number, number] | null;
    while ((m = scan.next(from))) {
      const [start, end] = m;
      from = end;                                   // a token that stays prose: the scan resumes after all of it
      let tok = text.slice(start, end);
      const trail = trailingPunct(tok);             // don't grab a sentence's closing punctuation
      if (trail) tok = tok.slice(0, tok.length - trail[0].length);
      if (!tok) continue;
      const isUri = isFileUri(tok);
      // a line reference a URI token swallowed (`file:///a.md:12`) is the suffix, not the path, where the surface reads lines
      if (isUri && opts && opts.lineSuffix) { const tail = URI_LINE_TAIL_RE.exec(tok); if (tail) tok = tok.slice(0, tail.index); }
      const span = spanHolding(u, start, start + tok.length);
      if (!span) continue;                          // across a node's edge, or inside a link: as it is
      if (!isUri && !looksLikeFilePath(tok) && !(span.inCode && looksLikeBareFileName(tok))) continue;   // "and/or", `np.array` etc.: leave as prose
      if (!isUri && opts && opts.accept && !opts.accept(tok, { text, at: start, inPre: span.inPre })) continue;   // the surface's own gate (the viewer's: an extension on the file)
      const fixed = !isUri && pathLinks ? pathLinks[tok] : undefined;   // the kernel's verdict, when it rendered one
      if (!isUri && pathLinks && typeof fixed !== "string") continue;   // checked against the filesystem: no such file (or several) → prose
      if (!isUri && span.inPre && opts && opts.preVerified && typeof fixed !== "string") continue;   // fenced: the kernel's word or nothing (no map at all → nothing)
      // what the link opens: a URI's own path (absolute, percent-decoded), else the kernel's fixed target or the token;
      // placed by the surface's resolve when it has one (the viewer normalizes a URI's path there as it does any
      // absolute path, so `file:///a/b/../c.md` opens and is titled the same whichever way it was written)
      const target = isUri ? fileUriToPath(tok) : (fixed ?? tok);
      const open = opts && opts.resolve ? opts.resolve(target) : target;
      const link = openPathLink(tok, open, !isUri);
      // a line written after the token rides in the link when the surface reads lines (the viewer scrolls to it);
      // one the highlight cut into another node stays prose
      let suffix: RegExpExecArray | null = null;
      if (opts && opts.lineSuffix) { LINE_SUFFIX_AT_RE.lastIndex = start + tok.length; suffix = LINE_SUFFIX_AT_RE.exec(text); }
      if (suffix && start + tok.length + suffix[0].length > span.end) suffix = null;
      if (suffix) { link.textContent = tok + suffix[0]; link.dataset.line = suffix[1] || suffix[2]; link.setAttribute("title", link.getAttribute("title") + ":" + link.dataset.line); }
      const last = start + tok.length + (suffix ? suffix[0].length : 0);
      let list = marks.get(span);
      if (!list) { list = []; marks.set(span, list); }
      list.push({ start, end: last, el: link });
      hits.push({ el: link, open, verified: !isUri && typeof fixed === "string", inPre: span.inPre });   // the kernel stat'd a fixed one this build
      from = last;                                  // a linked token: resume right after what was linked; its trimmed tail is prose
    }
    for (const [span, list] of marks) rewriteSpan(u, span, list);
  }
  return hits;
}
