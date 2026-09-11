// The file PREVIEW popover's pure half (T351 stage 1, the user 2026-09-11): hovering a local file link in the chat
// pops up a card with the rendered head of the file (or the section a `path#slug` link names), near-instantly. What
// lives here needs no DOM and is executed in file-preview.test.ts: the link's path and anchor, which kind a link may
// preview (the kernel's verdict, shipped as pathPreview beside pathLinks; a link absent from it is shown as text plus
// "open" with NO request), the slice route's URL, the ONE content shape every provider fills (stage 2's glossary
// lookup included), and the hover's timing (a dwell before it opens, a grace to cross into the card). render.ts owns
// the card itself: the element, the fetch, the rendering per kind, the placement.

export const PREVIEW_DWELL_MS = 350;   // a hover shorter than this is a pass-through, not a question
export const PREVIEW_GRACE_MS = 150;   // leaving the link toward the card must not close it on the way

/** The kinds a preview shows. `text` is the text-only card (a path the popover may not fetch: outside the session's
 *  folder and the user's home, unverified, a secrets-shaped name, not a kind it renders, over the caps). `term` is
 *  stage 2's glossary entry, filled by the lab team's lookup through the same shape. */
export type PreviewKind = "markdown" | "section" | "image" | "code" | "pdf" | "text" | "term";

/** THE content contract (docs/reference.md, "The file preview popover"): one shape, whoever fills it. */
export interface PreviewContent {
  kind: PreviewKind;
  title: string;                       // the file's name, or the term
  subtitle?: string;                   // "#slug" for a section, the source path for a term
  body: { markdown?: string; html?: string; text?: string; url?: string; lang?: string };
  note?: string;                       // one line the card says above the body (a missing anchor, why a path is text-only)
  open?: { label: string; path: string; frag?: string };   // the affordance to the full view
}

/** `path#slug` → the path and the anchor; a `#` inside a file name is not an anchor unless what follows reads as a
 *  slug (lower-case letters, digits, hyphens). No anchor → "". */
export function parsePreviewLink(open: string): { path: string; anchor: string } {
  const i = open.lastIndexOf("#");
  if (i <= 0) return { path: open, anchor: "" };
  const a = open.slice(i + 1);
  if (!/^[a-z0-9][a-z0-9-]*$/.test(a)) return { path: open, anchor: "" };
  return { path: open.slice(0, i), anchor: a };
}

/** The kind the kernel allows a token to preview, or null (text-only, no request). */
export function previewKindOf(token: string, pathPreview?: Record<string, string> | null): string | null {
  if (!pathPreview) return null;
  const k = pathPreview[token] || pathPreview[parsePreviewLink(token).path];
  return k && /^(markdown|image|code|pdf)$/.test(k) ? k : null;
}

/** GET /file?slice=1: the one fetch behind a text preview (the kernel slices the file's cached text). */
export function sliceUrl(path: string, sid: string | null, anchor: string): string {
  let u = "/file?path=" + encodeURIComponent(path) + "&slice=1";
  if (sid) u += "&sid=" + encodeURIComponent(sid);
  if (anchor) u += "&anchor=" + encodeURIComponent(anchor);
  return u;
}
/** GET /file: the bytes behind an image or a PDF preview (the route the figures already use). */
export function fileUrl(path: string, sid: string | null): string {
  return "/file?path=" + encodeURIComponent(path) + (sid ? "&sid=" + encodeURIComponent(sid) : "");
}

const LANG_BY_EXT: Record<string, string> = {
  sh: "bash", bash: "bash", zsh: "bash", py: "python", pyi: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
  jsx: "javascript", ts: "typescript", tsx: "typescript", json: "json", jsonc: "json", json5: "json", xml: "xml", html: "xml",
  htm: "xml", svg: "xml", css: "css", md: "markdown", markdown: "markdown", diff: "diff", patch: "diff", yaml: "yaml", yml: "yaml",
};
/** The highlight language for a code head, among the grammars the chat bundle already loads; "" for plain text. */
export function langOf(path: string): string {
  const m = /\.([A-Za-z0-9]+)$/.exec(path);
  return (m && LANG_BY_EXT[m[1].toLowerCase()]) || "";
}

export function baseName(path: string): string {
  const s = path.replace(/[\\/]+$/, "");
  const i = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"));
  return i >= 0 ? s.slice(i + 1) : s;
}

/** The slice route's answer, as the JSON it sends. */
export interface SliceAnswer {
  kind?: string | null; title?: string; anchor?: string; allowed?: boolean; why?: string;
  text?: string; found?: boolean; truncated?: boolean; size?: number; hit?: boolean;
  heading?: { level: number; text: string; slug: string; line: number } | null;
}

/** Attributes whose value the browser fetches the moment the element sits in a live document. */
const LOAD_ATTRS = ["src", "srcset", "poster", "data"];
/** SVG elements whose href IS a fetch (an <a href> is a link, not a load). */
const HREF_LOADERS = new Set(["image", "use", "feimage"]);

/** Does `value` (a src, a poster, an href; a srcset when `srcset`) name anything off `origin`? The value is read the
 *  way the browser reads it: TAB, LF and CR are deleted from ANYWHERE in it first (the WHATWG parser's own rule, so
 *  `/<TAB>/host/x` is `//host/x`, remote; the review: a split on whitespace before the parse read it as two local
 *  tokens and the browser fetched it), a srcset is split on commas into candidates whose first whitespace-separated
 *  token is the URL, and each URL is run through the URL parser against `base` and its origin compared, so a
 *  protocol-relative `//host`, a backslash spelling (`\\host`, `/\host`) and a leading space all resolve as the
 *  browser resolves them. `data:` loads nothing remote, and a `blob:` of this origin is this origin's. An unparsable
 *  URL counts as remote: alt text over a guess. */
export function remoteLoad(value: string, origin: string, base: string, srcset = false): boolean {
  const v = value.replace(/[\t\n\r]/g, "");
  const urls = srcset ? v.split(",").map((c) => c.trim().split(/\s+/)[0] || "") : [v.trim()];
  for (const tok of urls) {
    if (!tok) continue;
    let u: URL;
    try { u = new URL(tok, base); } catch { return true; }
    if (u.protocol === "data:") continue;
    if (u.origin === origin) continue;
    return true;
  }
  return false;
}

/** Strip every element of `root` that would load off `origin` when it joins a live document: an <img> becomes its alt
 *  text; anything else (a <picture>'s <source>, a <video> with a poster or a src, an <audio>, a <track>, an SVG
 *  <image>) goes. Runs on the sanitizer's INERT DOM before the nodes are adopted into the page, so no request ever
 *  starts (the review: a strip after innerHTML raced the browser's fetch and lost). Returns how many it stripped. */
export function stripRemoteLoads(root: ParentNode, origin: string, base: string): number {
  let n = 0;
  for (const el of Array.from(root.querySelectorAll("*"))) {
    const name = el.localName.toLowerCase();
    let remote = false;
    for (const attr of LOAD_ATTRS) {
      const v = el.getAttribute(attr);
      if (v != null && remoteLoad(v, origin, base, attr === "srcset")) { remote = true; break; }
    }
    if (!remote && HREF_LOADERS.has(name)) {
      for (const attr of ["href", "xlink:href"]) {
        const v = el.getAttribute(attr);
        if (v != null && remoteLoad(v, origin, base)) { remote = true; break; }
      }
    }
    if (!remote) continue;
    n++;
    if (name === "img") el.replaceWith(el.ownerDocument.createTextNode(el.getAttribute("alt") || ""));
    else el.remove();
  }
  return n;
}

/** The text-only card: the path as words and the way to the full view, nothing fetched. */
export function textOnlyContent(path: string, anchor: string, why?: string): PreviewContent {
  return { kind: "text", title: baseName(path), subtitle: anchor ? "#" + anchor : undefined,
           body: { text: path }, note: why, open: { label: "open", path, frag: anchor || undefined } };
}

/** The card's content for a kind the kernel allowed, from the slice route's answer (text kinds) or from the path
 *  alone (an image or a PDF, whose bytes ride the plain route). */
export function contentFor(path: string, anchor: string, kind: string, sid: string | null, answer: SliceAnswer | null): PreviewContent {
  const open = { label: "open", path, frag: anchor || undefined };
  const title = (answer && answer.title) || baseName(path);
  if (kind === "image") return { kind: "image", title, body: { url: fileUrl(path, sid) }, open };
  if (kind === "pdf") return { kind: "pdf", title, subtitle: "first page", body: { url: fileUrl(path, sid) }, open };
  if (!answer || answer.allowed === false) return textOnlyContent(path, anchor, answer && answer.why ? answer.why : undefined);
  const text = answer.text || "";
  if (kind === "code") {
    return { kind: "code", title, body: { text, lang: langOf(path) }, note: answer.truncated ? "the head of the file; open for the rest" : undefined, open };
  }
  const found = answer.found !== false;
  const note = anchor && !found ? 'no section "' + anchor + '" in this file; its head instead'
             : answer.truncated ? (anchor ? "the head of the section; open for the rest" : "the head of the file; open for the rest") : undefined;
  return { kind: anchor && found ? "section" : "markdown", title, subtitle: anchor && found ? "#" + anchor : undefined,
           body: { markdown: text }, note, open };
}

/** The hover's timing, with the timers injected so the tests run it without a clock: `enter(link)` starts the dwell
 *  (a second link restarts it), `leave()` starts the grace after which the card closes unless the pointer is inside
 *  it (`pin`/`unpin`), `cancel()` drops everything (a tab pick, Escape, a scroll). */
export class HoverIntent<T> {
  private dwell: ReturnType<typeof setTimeout> | null = null;
  private grace: ReturnType<typeof setTimeout> | null = null;
  private pinned = false;
  open: T | null = null;
  // the timers are wrapped, not passed bare: a browser's setTimeout called through a field (this.setT) runs with the
  // instance as `this` and throws "Illegal invocation"; the tests hand in fakes through the same seam
  constructor(private dwellMs: number, private graceMs: number,
              private onOpen: (target: T) => void, private onClose: () => void,
              private setT: (fn: () => void, ms: number) => ReturnType<typeof setTimeout> = (fn, ms) => setTimeout(fn, ms),
              private clearT: (h: ReturnType<typeof setTimeout>) => void = (h) => clearTimeout(h)) {}
  enter(target: T): void {
    if (this.grace) { this.clearT(this.grace); this.grace = null; }
    if (this.open === target) return;              // back onto the link the card already shows: nothing to do
    if (this.dwell) this.clearT(this.dwell);
    this.dwell = this.setT(() => { this.dwell = null; this.open = target; this.onOpen(target); }, this.dwellMs);
  }
  leave(): void {
    if (this.dwell) { this.clearT(this.dwell); this.dwell = null; }
    if (this.open == null || this.pinned) return;
    if (this.grace) this.clearT(this.grace);
    this.grace = this.setT(() => { this.grace = null; if (!this.pinned) this.close(); }, this.graceMs);
  }
  pin(): void { this.pinned = true; if (this.grace) { this.clearT(this.grace); this.grace = null; } }
  unpin(): void { this.pinned = false; this.leave(); }
  cancel(): void {
    if (this.dwell) { this.clearT(this.dwell); this.dwell = null; }
    if (this.grace) { this.clearT(this.grace); this.grace = null; }
    this.pinned = false;
    if (this.open != null) this.close();
  }
  private close(): void { this.open = null; this.onClose(); }
}
