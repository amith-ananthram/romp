// GitHub pull-request references in rendered text become links to the PR page of the repository the
// session works in (the user 2026-09-06, whose sessions' chat, feed cards and notes rendered their own
// PR numbers, `merged #12` say, as plain text). The kernel names the repository per session —
// `githubRepo` (owner/repo, or null) on the session frame and on the feed's session rows, derived from
// the session tree's origin remote by the same parser the file viewer's GitHub link uses — and this
// module does the text work in ONE place for every surface: the chat's markdown (render.ts md()), the
// feed's card titles, distiller lines, checklists, held-mail gists and modal (feed.ts), and the
// outline's goal rows (fleet.ts).
//
// Shapes that link — each needs a word boundary BEFORE it (start of text, whitespace, an opening
// bracket or quote, a comma/semicolon/colon, an em or en dash), so a `#` glued to letters, a path or a
// URL never does:
//   #123               → https://github.com/<session repo>/pull/123
//   PR #123 / pull #123 (any case, the space optional) → the same; the whole phrase is the link
//   owner/repo#123     → https://github.com/owner/repo/pull/123 — a reference into another repository
// GitHub answers /pull/<n> for an ISSUE number with a redirect to /issues/<n>, so one form covers both.
// The ASCII hyphen is deliberately NOT a boundary: it ends URL paths and identifiers
// (`https://example.com/a-#12` must stay plain); the dashes appear in neither.
//
// A slash-separated RUN of bare references (`#12/#13`, `PRs #12/#13/#14`) links each one — GitHub's own
// autolinker does (checked against its markdown renderer, 2026-09-06) — every reference in the run
// against the session's repository; `/` is still no boundary on its own, so a `/#13` links only as the
// continuation of a reference before it, never inside a URL, and a run that ends in a path (`#12/#13/x`)
// stays plain like `#12/x`. After a cross-repo reference a `/` refuses the whole (GitHub would read the
// `#13` in `owner/repo#12/#13` against the CONTEXT, a writer means owner/repo: two readings, no link).
//
// The cross-repo form follows GitHub's own name grammar. An owner (a user or organization login) is
// alphanumerics and single hyphens — never leading, trailing or doubled — at most 39 characters; a
// repository name may also carry `.` and `_`, at most 100 characters, and never ENDS in `.` (`.` and
// `..` are the path names, and GitHub serves no `owner/repo.` — a trailing dot is sentence punctuation
// glued to a `#`, as in `notes-api.#7`). So `my.org/repo#1` (a dotted owner), `a/..#12` and
// `owner/repo.#1` are not references. Two plain words around a slash
// (`src/lib#3`) ARE one — that is GitHub's syntax byte for byte, and a directory path never carries a
// bare numeric fragment (a line reference is `file.py#L12`, which fails the number shape) — so it
// links. The one refusal beyond the grammar is a "repo" that reads like a FILENAME (`docs/x.html#12`,
// `src/app.py#3` — an extension-shaped tail after a dot): that is a path with a fragment, and a wrong
// link is worse than none. The cost is a repository named like a file (`something.js`, `tool.dev`),
// which stays plain text in the cross-repo form — except GitHub Pages repos (any `*.github.io`, the one
// standard dotted repo name; a fork keeps the original's), which link. A session's OWN repo is never
// filtered this way: the
// kernel shipped it, so a bare `#12` links in a `user.github.io` or `something.js` checkout too.
//
// What never links: text inside code-like elements (<code>, <pre>, <kbd>, <samp>, <var>, <tt> — a
// color `#fff`, a CSS id, a shell comment, a quoted key or token), text already inside an <a> (a GitHub
// URL marked autolinked, a [text](url)), a path link (.file-uri-link), and any `#` that is part of a
// word, a URL fragment or a color literal — `#1EA1EB` and `#0c1a2e` fail the number shape (hex letters
// after the digits, or a leading zero; a PR number has neither). Typographic wrappers (<em>, <strong>,
// <sup>) are walked: a `#13` in them is still a reference.
//
// The boundary is judged over the RENDERED text, not per text node. The markdown renderer, or a pass that
// ran before this one, may cut one run of text into several nodes: inline code followed by a number
// (`docs/a.md`#12 in a message) renders as <code>docs/a.md</code> and then a text node that begins `#12`,
// and **docs/a.md**#12, [text](url)#12 and a path link (.file-uri-link) followed by its fragment have the
// same shape. The start of such a node is no boundary. So the walk carries the last character of the text
// before each node into it (a skipped element's text included), and a `#` at a node's head links only when
// that character is one of the boundary characters above, or when nothing precedes it in the run. The
// carry crosses INLINE elements only: an <img>, an empty <span>, an element with no text renders no
// boundary, so the text before it decides. It resets at a line or block edge (BLOCK_TAGS below: <br>,
// <hr>, <p>, <li>, a heading, a table cell, ...), where the rendered text has a boundary whether or not a
// whitespace node sits there: marked renders a GFM hard break (`done` with two trailing spaces, then `#12`
// on the next line) as `<p>done<br>#12</p>`, with no text between the <br> and the `#`, and `#12` links.
// An element the list does not name is treated as inline, the safe default: a missed edge costs a link,
// an invented one makes a wrong link.
//
// A session with no GitHub repository (`githubRepo` null: no repo, no origin, or an origin elsewhere)
// links NOTHING, the cross-repo form included — the honest rendering is the plain text, never a
// guessed host.

import { hostPrefix } from "./host-prefix";

/** One run of text; `href` marks a link, `label` its repo-qualified reference for the hover title. */
export interface PrRefSegment { text: string; href?: string; label?: string }

export const PR_LINK_CLASS = "pr-link";

// GitHub's own name grammar (see the header): the owner rule is GitHub's login rule verbatim — an
// alphanumeric, then up to 38 more of alphanumerics or a hyphen that is followed by an alphanumeric.
// A PR number has no leading zero and (with room to spare) at most seven digits.
const OWNER = "[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}";
const REPO = "[A-Za-z0-9_.-]{1,100}";
const NUM = "[1-9]\\d{0,6}";
const DOT_TAIL = /\.$/;                        // `.`, `..`, `repo.`: inside the character class, never a repo
// group 1 = the boundary character (re-emitted as text; empty at the start of the text)
// groups 2/3/4 = owner / repo / number of the cross-repo form
// group 5 = the number of a `PR #n` / `pull #n` phrase; group 6 = the number of a bare `#n`;
// group 7 = the slash-separated run of further bare references after either (`/#13/#14`, or empty)
// the trailing lookahead refuses a word character, another `#` or a `/` right after the last number,
// which is what keeps `#12abc`, `#1EA1EB` and a `#12/x` path fragment out — the run is greedy and the
// lookahead follows it, so `#12/#13/x` backtracks to nothing rather than to `#12`
const RUN = "((?:/#" + NUM + ")*)";
// the characters a reference may follow (the header's list); `^` stands for the start of the rendered text
const BOUNDARY = "\\s(\\[{<,;:\"'“‘«—–";
const BOUNDARY_RE = new RegExp("^[" + BOUNDARY + "]$");
const REF_SRC =
  "(^|[" + BOUNDARY + "])(?:" +
  "(" + OWNER + ")/(" + REPO + ")#(" + NUM + ")" +
  "|(?:(?:PR|pull)\\s?#(" + NUM + ")|#(" + NUM + "))" + RUN +
  ")(?![\\w#/])";

const REPO_SHAPE = new RegExp("^" + OWNER + "/" + REPO + "$");
const FILENAME_TAIL = /\.[A-Za-z0-9]{1,5}$/;   // `x.html`, `app.py`, `notes.md`: a path fragment, not a repo
const PAGES_REPO = /\.github\.io$/i;            // GitHub Pages: the one standard dotted repo name (any owner's)

/** The cross-repo form's refusal beyond the grammar: a repo that reads like a filename (see the header). */
function filenameShaped(repo: string): boolean {
  return FILENAME_TAIL.test(repo) && !PAGES_REPO.test(repo);
}

/** The session repo as the kernel ships it, or null when it is absent or not owner/repo-shaped by
 *  GitHub's grammar — a URL is only ever built from a value that passed this. */
export function validPrRepo(repo: string | null | undefined): string | null {
  if (typeof repo !== "string" || !REPO_SHAPE.test(repo)) return null;
  if (DOT_TAIL.test(repo)) return null;
  return repo;
}

export function prUrl(repo: string, n: string): string {
  return "https://github.com/" + repo + "/pull/" + n;
}

/** Split `text` into plain runs and PR-reference links against `repo`. Pure: no DOM. With no valid
 *  repo, or no reference in the text, the whole text comes back as one plain segment. `before` is the
 *  one character (a UTF-16 code unit, the DOM walk's `text.slice(-1)`) that precedes `text` in the run
 *  it was cut from, "" (the default) when `text` starts the run: a match at the start of `text` counts
 *  only when that character is a boundary (the header); a longer string never is one, and neither is
 *  the lone surrogate an emoji ends in, which the one-node regex refuses too. A refused match is scanned
 *  again from its second character, so a boundary inside it still counts (`d` then `PR #12` links the
 *  `#12`, as the one-node reading of `dPR #12` does) while a glued run (`d` then `#12/#13`) links
 *  nothing, `/` being no boundary. */
export function prRefSegments(text: string, repo: string | null | undefined, before = ""): PrRefSegment[] {
  const r = validPrRepo(repo);
  if (!r || !text || text.indexOf("#") < 0) return [{ text }];
  const re = new RegExp(REF_SRC, "gi");
  const out: PrRefSegment[] = [];
  let last = 0, m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const start = m.index + m[1].length;          // the boundary character stays plain text
    const end = m.index + m[0].length;
    if (start === 0 && before && !BOUNDARY_RE.test(before)) { re.lastIndex = 1; continue; }   // glued to the text before this node: no boundary
    let target: string, n: string;
    if (m[4]) {
      // `a/..#12` and `a/repo.#12` are no repo; `docs/x.html#12` is a path's fragment — the scan resumes after either
      if (DOT_TAIL.test(m[3]) || filenameShaped(m[3])) continue;
      target = m[2] + "/" + m[3]; n = m[4];
    }
    else { target = r; n = m[5] || m[6]; }
    if (start > last) out.push({ text: text.slice(last, start) });
    const run = m[7] || "";                        // `/#13/#14` after a bare or `PR #n` reference, else ""
    const first = end - run.length;
    out.push({ text: text.slice(start, first), href: prUrl(target, n), label: target + "#" + n });
    for (const more of run.split("/").slice(1)) {  // each `#n` of the run, the slashes between them plain
      out.push({ text: "/" });
      out.push({ text: more, href: prUrl(target, more.slice(1)), label: target + more });
    }
    last = end;
  }
  if (!out.length) return [{ text }];
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

// Elements whose text never links: an existing link, code-like text (inline or fenced code, a key, a
// sample, a variable, teletype), form controls, and the chat's own path links (a `docs/x.md#12`-shaped
// path must stay the path it is).
const CODE_LIKE = "a, code, pre, kbd, samp, var, tt";
const SKIP_TAGS = new Set(["A", "CODE", "PRE", "KBD", "SAMP", "VAR", "TT", "SCRIPT", "STYLE", "TEXTAREA", "INPUT", "BUTTON", "SELECT", "OPTION", "SVG"]);
const SKIP_CLASSES = ["file-uri-link", "url-code-link"];
// Elements whose edges are line or block boundaries in the rendered text: the run of text the walk carries
// ends at one and starts fresh after it (the header). These are HTML's block-level and line-breaking
// elements, listed by how they render rather than by what the chat's sanitizer admits (md-sanitize.ts
// strips form, fieldset, legend and dialog today and keeps their text), so the two lists move
// independently. Everything not listed is inline, and an inline element with no text passes the run
// through unchanged: the safe default, since a missed boundary costs a link and an invented one makes a
// wrong link.
const BLOCK_TAGS = new Set([
  "BR", "HR", "P", "PRE", "BLOCKQUOTE", "UL", "OL", "LI", "H1", "H2", "H3", "H4", "H5", "H6",
  "TABLE", "CAPTION", "THEAD", "TBODY", "TFOOT", "TR", "TD", "TH",
  "DIV", "DL", "DT", "DD", "SECTION", "ARTICLE", "HEADER", "FOOTER", "NAV", "ASIDE", "MAIN",
  "FIGURE", "FIGCAPTION", "DETAILS", "SUMMARY", "FORM", "FIELDSET", "ADDRESS",
  "CENTER", "LEGEND", "DIALOG", "DIR", "MENU", "HGROUP", "SEARCH",
]);

const tagOf = (e: Element): string => String(e.tagName || "").toUpperCase();

function skipElement(e: Element): boolean {
  if (SKIP_TAGS.has(tagOf(e))) return true;
  const cl = e.classList;
  return !!cl && SKIP_CLASSES.some((k) => cl.contains(k));
}

/** Turn the PR references in `root`'s text nodes into anchors (class pr-link, target _blank, rel
 *  noopener noreferrer, a title naming the repo-qualified reference). Skips code-like subtrees and
 *  existing anchors (SKIP_TAGS) and the chat's path links; a root that itself sits inside one of those
 *  is left alone. Returns the number of links made. A root whose whole text has no `#` — the common
 *  case — costs one native textContent read and no walk. Walks childNodes and edits through
 *  insertBefore/removeChild only, so a test's plain-object DOM stand-in runs it as the browser does.
 *  The walk carries the last character of the text before each node (a skipped element's text counts,
 *  an inline element with no text does not, and a line or block edge, BLOCK_TAGS, resets the run), so a
 *  node's start is a boundary only when the rendered text has one there (the header: `docs/a.md` and
 *  `#12` split across two nodes is still one path; `done<br>#12` is two lines). */
export function linkifyPrRefs(root: Node | null | undefined, repo: string | null | undefined): number {
  const r = validPrRepo(repo);
  if (!r || !root) return 0;
  if ((root.textContent || "").indexOf("#") < 0) return 0;
  const rootEl = root as Element;
  if (root.nodeType === 1 && typeof rootEl.closest === "function" && rootEl.closest(CODE_LIKE)) return 0;
  let made = 0;
  /** links the text under `node`, given the character before it; returns the character before whatever
   *  follows: "" after a line or block edge, the way the root starts */
  const visit = (node: Node, before: string): string => {
    const kids = Array.from(node.childNodes || []);   // snapshot: text children are replaced as we go
    for (const c of kids) {
      if (c.nodeType === 3) {
        const text = c.textContent || "";
        made += linkifyTextNode(c, r, before);
        if (text) before = text.slice(-1);
        continue;
      }
      if (c.nodeType !== 1) continue;                  // a comment renders nothing: the run is unbroken
      const e = c as Element;
      const edge = BLOCK_TAGS.has(tagOf(e));
      if (skipElement(e)) {
        const text = e.textContent || "";
        before = edge ? "" : text ? text.slice(-1) : before;
        continue;
      }
      if (edge) { visit(e, ""); before = ""; }         // its text starts a line or block; so does whatever follows it
      else before = visit(e, before);
    }
    return before;
  };
  visit(root, "");
  return made;
}

function linkifyTextNode(tn: Node, repo: string, before: string): number {
  const text = tn.textContent || "";
  if (text.indexOf("#") < 0) return 0;
  const segs = prRefSegments(text, repo, before);
  if (!segs.some((s) => s.href)) return 0;
  const parent = tn.parentNode;
  if (!parent) return 0;
  const doc: Document = tn.ownerDocument || document;
  let made = 0;
  for (const s of segs) {
    let node: Node;
    if (s.href) {
      const a = doc.createElement("a");
      a.className = PR_LINK_CLASS;
      a.href = s.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.title = s.label + " on GitHub";
      a.textContent = s.text;
      node = a;
      made++;
    } else {
      node = doc.createTextNode(s.text);
    }
    parent.insertBefore(node, tn);
  }
  parent.removeChild(tn);
  return made;
}

/** Set `el`'s text to `text` with its PR references linked — and only when (text, repo) differs from
 *  what `el` already shows, keyed on two data attributes the element carries. The feed updates a card
 *  IN PLACE on every kernel push; rewriting an unchanged title would replace its anchors under the
 *  pointer every half-second (the hover title never appears, a press lands on a node that is gone by
 *  the release). Keeping the node is the click-safety rule's keyed update: identity survives a push. */
export function setLinkedText(el: HTMLElement, text: string, repo: string | null | undefined): void {
  const r = validPrRepo(repo) || "";
  if (el.dataset.prText === text && el.dataset.prRepo === r) return;
  el.textContent = text;
  if (r) linkifyPrRefs(el, r);
  el.dataset.prText = text;
  el.dataset.prRepo = r;
}

/** A session row as the frame ships it: the feed's `sessions` rows and the chat's session map both
 *  carry a sid, a name and the kernel's githubRepo. A federated row wears its host on BOTH (`host:uuid`,
 *  `host:name`); a local row is bare. */
export interface SessionRepoRow { sid: string; name: string; githubRepo?: string | null }

/** The repository of the ONE session in `rows` that is the named sender of a message — the frame's own
 *  githubRepo for that session, never a guess (the reading session's repo is never substituted for the
 *  sender's; a wrong link is worse than none). `host` is the sender's host when the card names it
 *  ("" = the viewing kernel's own; the feed's held-mail cards carry `blocked.origin`, the chat's postal
 *  cards `peerHost` — postalSenderHost); undefined when it does not (a card from a kernel that predates
 *  the field), in which case every row is a candidate — a local one by its name, a federated one by
 *  its bare name — so a homonym on any attached host makes the sender ambiguous, and a homonym on a
 *  host NOT attached to this dashboard is invisible, which is why the host-named form exists. Zero or
 *  several candidates, or a candidate the kernel gave no repo → null: the text stays plain. */
export function senderPrRepo(rows: readonly SessionRepoRow[], sender: string, host?: string): string | null {
  if (!sender) return null;
  const hit = rows.filter((s) => {
    if (!s || typeof s.sid !== "string" || typeof s.name !== "string") return false;
    if (host === undefined) return (hostPrefix(s.name, s.sid)?.rest ?? s.name) === sender;
    if (host === "") return s.sid.indexOf(":") < 0 && s.name === sender;
    return s.sid.startsWith(host + ":") && s.name === host + ":" + sender;
  });
  return hit.length === 1 ? validPrRepo(hit[0].githubRepo) : null;
}

/** senderPrRepo's `host` for an inbound postal card, from the `peerHost` the kernel stamps on it — the
 *  sender's host as the CARD'S OWN kernel's message log recorded the delivery, so it is relative to THAT
 *  kernel: "" for mail from its own sessions, a peer's name for mail from that host. The chat shows the
 *  sessions of attached kernels too (their ids and names wear `host:`), and federation hands a remote
 *  session's events over as its kernel wrote them, so `cardHost` — the host of the session the card sits
 *  in, "" for a local one — anchors the reading: "" means the card's own host (its local rows for a local
 *  card, that host's federated rows for a remote one); a named host is that kernel's peer, which may be
 *  THIS dashboard's own kernel (its name, `selfHost`, folds to "" — the local rows) or a host the
 *  dashboard may or may not have attached (the same name federation prefixes that host's rows with; not
 *  attached → no row → plain text, never a local homonym's repo). Read as relative to the dashboard, a
 *  remote card from its own kernel's session resolved against the LOCAL rows by bare name (review find,
 *  2026-09-06). An unknown origin ("?") is undefined, as the feed reads `blocked.origin`; a card from a
 *  kernel that predates the field has no peerHost at all → undefined, the name-only resolution
 *  senderPrRepo keeps for it.
 *
 *  A peer's name is the card's kernel's OWN name for that host, not a name every kernel shares: the bus
 *  files each peer under the name the local side knows it by — the ssh alias it was attached as when
 *  there is one, else the name the peer declared at check-in (postal_service.py `_canon_peer_name`) —
 *  and no host id crosses the wire for the dashboard to match on instead (its rows wear the aliases IT
 *  attached each host as; the bus id never reaches a frame). So the fold and the row match hold when
 *  the two kernels agree on the name, which the common topology gives: the dashboard's kernel declares
 *  its own name (`selfHost`) at check-in and the remote files it under that, unless it attached this
 *  machine under an alias of its own. When the names disagree the stamp matches no row and the
 *  reference stays text — the safe direction, pinned by the tests; a WRONG link needs the card's kernel
 *  to use this dashboard's own name, or an attached host's alias, for a different machine — a collision
 *  in the user's own naming. */
export function postalSenderHost(peerHost: unknown, selfHost: string, cardHost = ""): string | undefined {
  if (typeof peerHost !== "string" || peerHost === "?") return undefined;
  if (peerHost === "") return cardHost;
  return peerHost === selfHost ? "" : peerHost;
}

/** Follow a PR link the way the chat follows its links (render.ts's a[href] delegate): on the web
 *  dashboard the viewer's own browser opens a tab; in a VS Code webview the href goes to the host,
 *  which openExternal()s it (view-routing.ts routes `openLink` for every pane; extension.ts consumes it
 *  for every panel). Installed ONCE per pane document on the CAPTURE phase, so the click never reaches
 *  the card or row handlers beneath the link — a link inside a feed card must open the PR, not also
 *  open the card's modal; one inside an outline row must not also jump the chat.
 *
 *  Click-safe across re-renders (ui/CLAUDE.md): the action hangs on the STABLE document, keyed off the
 *  anchor's `href` attribute — never on the anchor node, which every push rebuilds. A native `click`
 *  needs the press and the release on one node, so a push mid-press would drop it (or hand it to the
 *  card underneath). So the press is followed by attribute: `pointerdown` remembers the href under the
 *  primary button, `pointerup` on a pr-link with the SAME href opens it — the rebuilt twin counts, the
 *  node's identity does not — and the native click that may follow is spent, so the card's own handler
 *  never sees it. That click can land only on the released node or one of its ancestors (the anchor
 *  itself, or the common ancestor when the pressed node is gone), so only a click there is spent: one
 *  arriving anywhere else with no press behind it — the browser fired none, then a programmatic
 *  .click() or an assistive-technology activation came — passes as the ordinary click it is (the flag
 *  ate one such click before; review find, 2026-09-06). The click path itself stays for a keyboard
 *  activation (Enter on a focused link fires click alone). Every flag clears on the next press, key or
 *  click — an event, never a timer. The chat pane does NOT install this — its own delegate already opens
 *  every absolute-scheme anchor the same way. */
export function installPrLinkOpener(
  doc: { addEventListener(type: string, fn: (e: Event) => void, capture?: boolean): void },
  post: ((msg: { type: string; href: string }) => void) | undefined,
  env: { protocol: () => string; open: (href: string) => void } = {
    protocol: () => (typeof location !== "undefined" ? location.protocol : ""),
    open: (href) => { window.open(href, "_blank", "noopener,noreferrer"); },
  },
): void {
  /** the pr-link href under an event target, or null — only the hrefs this module writes */
  const hrefAt = (t: EventTarget | null): string | null => {
    const el = t as Element | null;
    const a = el && typeof el.closest === "function" ? (el.closest("a." + PR_LINK_CLASS + "[href]") as HTMLAnchorElement | null) : null;
    const href = a ? a.getAttribute("href") || "" : "";
    return /^https:\/\/github\.com\//.test(href) ? href : null;
  };
  const open = (href: string): void => {
    const p = env.protocol();
    if (p === "http:" || p === "https:") env.open(href);
    else if (post) post({ type: "openLink", href });
  };
  const primary = (e: Event): boolean => {
    const pe = e as PointerEvent;
    return (pe.button === undefined || pe.button === 0) && pe.isPrimary !== false;
  };
  /** is `t` `node` or one of its ancestors — the only targets the click after a release on `node` can have */
  const inclusiveAncestor = (t: EventTarget | null, node: EventTarget): boolean =>
    t === node || (!!t && typeof (t as Node).contains === "function" && (t as Node).contains(node as Node));
  let pressed: string | null = null;        // the pr-link href under the primary button since pointerdown
  let served: EventTarget | null = null;    // the node released on when pointerup opened a link: its click is already served
  doc.addEventListener("pointerdown", (e) => { served = null; pressed = primary(e) ? hrefAt(e.target) : null; }, true);
  doc.addEventListener("pointercancel", () => { pressed = null; }, true);
  doc.addEventListener("keydown", () => { served = null; pressed = null; }, true);
  doc.addEventListener("pointerup", (e) => {
    const was = pressed;
    pressed = null;
    if (!was || !primary(e) || hrefAt(e.target) !== was) return;   // released elsewhere: no click
    open(was);
    served = e.target;
  }, true);
  doc.addEventListener("click", (e) => {
    const node = served;
    served = null;
    if (node && inclusiveAncestor(e.target, node)) { e.preventDefault(); e.stopPropagation(); return; }
    const href = hrefAt(e.target);
    if (!href) return;
    e.preventDefault();
    e.stopPropagation();
    open(href);
  }, true);
}
