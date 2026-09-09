// The file-path matcher the chat's transcript links are made from, as a module any surface can import. It
// lived inside render.ts, the chat's entry, which exports nothing, so a second surface that shows text with
// paths in it could reach the matcher only by copying it, and two copies drift. Lifted, not copied: one regex,
// one set of gates, one span shape, so the kernel's tokenizer parity (tests/fixtures/path_token_parity.json)
// and the chat's link behaviour keep a single source.
//
// This module MATCHES and MARKS; it binds no action. Every link it emits is a `.file-uri-link` span carrying
// data-path (what a click opens: for a shortened mention the kernel's fixed target, not the token) and
// data-rel="1" when the token was a bare path rather than a file:// URI (so a resolver uses a session's cwd).
// What a click does is the hosting document's call: render.ts binds openPath per span (the editor in VS Code,
// the viewer on the web) and paints its figure previews from the hits. A document that cannot act on a click
// should not call this: a link that does nothing is worse than text.

function el(tag: string, cls?: string): HTMLElement { const e = document.createElement(tag); if (cls) e.className = cls; return e; }

// A file:// URI → its local filesystem path: strip the scheme, percent-decode. file:///a/b → /a/b.
export function fileUriToPath(uri: string): string {
  let p = uri.replace(/^file:\/\//i, "");   // file:///Users/… → /Users/… (host is empty for file:///)
  try { p = decodeURIComponent(p); } catch { /* malformed %-escape — use verbatim */ }
  return p;
}
// A VERBATIM file link, marked for whoever hosts it. `raw` is shown as written (selectable/copyable in
// place); `open` is what gets opened. A bare file:// can't be followed by the browser from the http
// dashboard (blocked scheme) and a VS Code editor won't render a PDF, so it is routed, never navigated.
// `relative` bare paths are resolved against a session's cwd by whoever opens them: a relative
// `design/foo.md` is relative to the repo the agent runs in, not the kernel's cwd (the user 2026-07-06).
export function openPathLink(raw: string, open: string, relative = false): HTMLElement {
  const a = el("span", "file-uri-link");
  a.textContent = raw;                       // shown exactly as written, selectable/copyable in place
  a.title = "Open " + open;
  a.dataset.path = open;
  if (relative) a.dataset.rel = "1";
  return a;
}
export function fileUriLink(uri: string): HTMLElement { return openPathLink(uri, fileUriToPath(uri)); }
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
// the kernel never saw is a verdict the client can never look up. Its TEXT is that contract.
export const CLICKABLE_PATH_RE = /file:\/\/\/?[^\s<>"'`)]+|[~.\w\-]*\/[~.\w\-/]*[\w\-]|[\w\-][\w\-.]*\.[A-Za-z0-9]{1,8}/gi;

/** One link the walk emitted: the span, what it opens, and whether the kernel stat'd that target this
 *  build (a fixed mention). The chat's figure pass wants exactly these. */
export interface PathLinkHit { el: HTMLElement; open: string; verified: boolean }

// Mark bare file:// URLs AND bare file paths inside `root`'s text, a relative `design/foo.md` too,
// resolved against a session's cwd by whoever opens it (the user 2026-07-06). marked doesn't autolink these
// and DOMPurify strips the file: scheme, so without this they read as dead text. Inside INLINE <code> a
// slash-less filename with a known extension marks too; only FENCED <pre> blocks and text already inside a
// link are skipped. Text nodes only: an element the caller already made a link stays one. Trailing sentence
// punctuation is left out, not swallowed.
// `pathLinks` (the user 2026-08-09): the kernel's verdict on every path-shaped token in this text
// (build_session's _path_links — tier 1 exact stat, tiers 2/3 a unique repo-list match that FIXES a
// shortened mention to its real file). When the map is present, a token links ONLY if it's in the map,
// and it opens the map's value — so `render.js` in prose stops 404ing, and hover shows the real target.
// Every shape gate still applies; the map only ever narrows. No map at all (an old kernel, a cached
// payload) keeps shape-only linking. file:// URIs are explicit absolute paths — never gated on the map.
// Returns the hits in document order, so a caller's "first mention" is the walk's first.
export function linkifyPathTokens(root: HTMLElement, pathLinks?: Record<string, string>): PathLinkHit[] {
  const hits: PathLinkHit[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let n: Node | null;
  while ((n = walker.nextNode())) nodes.push(n as Text);
  for (const tn of nodes) {
    if (tn.parentElement?.closest("a, .file-uri-link, pre")) continue;   // already a link, or a fenced code block
    const inCode = !!tn.parentElement?.closest("code");                  // inline code — where bare filenames may link
    const text = tn.data;
    if (!text.includes("/") && !(inCode && text.includes("."))) continue;   // cheap pre-filter: no slash (and, in code, no dot) → nothing here
    const re = new RegExp(CLICKABLE_PATH_RE.source, "gi");
    const frag = document.createDocumentFragment();
    let last = 0, any = false, m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      let tok = m[0];
      const trail = tok.match(/[.,;:!?)\]}>"'`]+$/);   // don't grab a sentence's closing punctuation
      if (trail) tok = tok.slice(0, tok.length - trail[0].length);
      if (!tok) continue;
      const isUri = /^file:\/\//i.test(tok);
      if (!isUri && !looksLikeFilePath(tok) && !(inCode && looksLikeBareFileName(tok))) continue;   // "and/or", `np.array` etc. — leave as prose
      const fixed = !isUri && pathLinks ? pathLinks[tok] : undefined;   // the kernel's verdict, when it rendered one
      if (!isUri && pathLinks && typeof fixed !== "string") continue;   // checked against the filesystem: no such file (or several) → prose
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const open = isUri ? fileUriToPath(tok) : (fixed ?? tok);
      const link = isUri ? fileUriLink(tok) : openPathLink(tok, open, true);
      frag.appendChild(link);
      hits.push({ el: link, open, verified: !isUri && typeof fixed === "string" });   // the kernel stat'd a fixed one this build
      last = m.index + tok.length;
      re.lastIndex = last;
      any = true;
    }
    if (!any) continue;
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    tn.replaceWith(frag);
  }
  return hits;
}
