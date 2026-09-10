// PR references link to GitHub (the user 2026-09-06): `#123`, `PR #123` and `owner/repo#123` in a
// session's chat, its feed cards and the outline's goal rows become links to the PR page
// of the repository the session works in — the kernel's `githubRepo` (owner/repo, or null). The text
// rule and the DOM applier are EXECUTED here (pr-links.ts is pure but for createElement/createTextNode,
// which a small plain-object DOM stand-in provides); the wiring into render.ts's md(), feed.ts and
// fleet.ts is pinned at the source, the way the other webview tests pin the chat renderer.
// The chat's own markdown shapes come from marked, read into the same stand-in.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { Marked } from "marked";

// ── a DOM stand-in: text and element nodes with the handful of members the applier touches ─────────
class T {
  nodeType = 3;
  parentNode: E | null = null;
  constructor(public textContent: string) {}
}
class E {
  nodeType = 1;
  parentNode: E | null = null;
  childNodes: Array<E | T> = [];
  href = ""; target = ""; rel = ""; title = ""; className = "";
  dataset: Record<string, string | undefined> = {};
  constructor(public tagName: string) {}
  get classList() { const cs = this.className.split(/\s+/).filter(Boolean); return { contains: (c: string) => cs.includes(c) }; }
  get textContent(): string { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v: string) { for (const c of this.childNodes) c.parentNode = null; this.childNodes = []; if (v) this.appendChild(new T(v)); }
  appendChild<N extends E | T>(c: N): N { c.parentNode = this; this.childNodes.push(c); return c; }
  insertBefore<N extends E | T>(n: N, ref: E | T | null): N {
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    n.parentNode = this;
    this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, n);
    return n;
  }
  removeChild(c: E | T): E | T { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  closest(sel: string): E | null {
    const tags = sel.split(",").map((s) => s.trim().toUpperCase());
    for (let n: E | null = this; n; n = n.parentNode) if (tags.includes(n.tagName.toUpperCase())) return n;
    return null;
  }
  /** a compact serialization for assertions: tags lower-case, anchors with their href only */
  html(): string {
    const inner = this.childNodes.map((c) => c instanceof T ? c.textContent : c.html()).join("");
    const t = this.tagName.toLowerCase();
    const attrs = (this.className ? ` class="${this.className}"` : "") + (this.href ? ` href="${this.href}"` : "");
    return `<${t}${attrs}>${inner}</${t}>`;
  }
}
(globalThis as any).document = {
  createElement: (tag: string) => new E(tag.toUpperCase()),
  createTextNode: (s: string) => new T(s),
};
/** build a tree from a tiny tag language: el("p", "text", el("code", "#12")) */
function el(tag: string, ...kids: Array<string | E>): E {
  const e = new E(tag.toUpperCase());
  for (const k of kids) e.appendChild(typeof k === "string" ? new T(k) : k);
  return e;
}
function cls(e: E, c: string): E { e.className = c; return e; }
const anchors = (root: E): E[] => {
  const out: E[] = [];
  const walk = (n: E) => { for (const c of n.childNodes) if (c instanceof E) { if (c.tagName === "A") out.push(c); walk(c); } };
  walk(root);
  return out;
};

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { prRefSegments, linkifyPrRefs, setLinkedText, senderPrRepo, postalSenderHost, installPrLinkOpener, validPrRepo, PR_LINK_CLASS } = require("./pr-links") as typeof import("./pr-links");

const REPO = "example-org/notes-api";
const url = (n: number | string, repo = REPO) => `https://github.com/${repo}/pull/${n}`;
const links = (text: string, repo: string | null = REPO) => prRefSegments(text, repo).filter((s) => s.href);

// ── the text rule ────────────────────────────────────────────────────────────────────────────────

test("a bare #123 links to the session repo's PR page, the surrounding text untouched", () => {
  assert.deepEqual(prRefSegments("merged #123 today", REPO), [
    { text: "merged " },
    { text: "#123", href: url(123), label: REPO + "#123" },
    { text: " today" },
  ]);
});

test("PR #123 and pull #123 link as a whole phrase, any case, the space optional", () => {
  for (const t of ["PR #123", "pr #123", "Pr#123", "pull #123", "Pull #123", "PULL#123"]) {
    const segs = prRefSegments("see " + t + ".", REPO);
    assert.deepEqual(segs, [{ text: "see " }, { text: t, href: url(123), label: REPO + "#123" }, { text: "." }], t);
  }
});

test("owner/repo#123 links into THAT repository", () => {
  const segs = prRefSegments("see example-org/other-tool#42 for the fix", REPO);
  assert.deepEqual(segs, [
    { text: "see " },
    { text: "example-org/other-tool#42", href: url(42, "example-org/other-tool"), label: "example-org/other-tool#42" },
    { text: " for the fix" },
  ]);
  // a repo name may carry dots and underscores; an owner may not
  assert.equal(links("x my-org/my.repo_v2#7")[0].href, url(7, "my-org/my.repo_v2"));
});

test("several references in one run each link, in order", () => {
  const ls = links("#1, #2 and example-org/other#3; PR #4");
  assert.deepEqual(ls.map((s) => s.text), ["#1", "#2", "example-org/other#3", "PR #4"]);
  assert.deepEqual(ls.map((s) => s.href), [url(1), url(2), url(3, "example-org/other"), url(4)]);
});

test("a word boundary before the reference: start, whitespace, brackets, quotes, punctuation, dashes", () => {
  for (const t of ["#5", " #5", "(#5)", "[#5]", "{#5}", "\"#5\"", "'#5'", ",#5", ";#5", "re:#5", "<#5>", "“#5”", "‘#5’", "«#5»", "—#5", "–#5"]) {
    assert.equal(links(t).length, 1, JSON.stringify(t));
    assert.equal(links(t)[0].text, "#5", JSON.stringify(t));
  }
});

test("an em or en dash glued to the reference is a boundary (prose habitually writes one); the ASCII hyphen is not", () => {
  // the dashes never end a URL path or an identifier, so nothing is lost by admitting them
  assert.deepEqual(links("Two PRs are up—#12 and #13.").map((s) => s.text), ["#12", "#13"]);
  assert.deepEqual(links("landed–#123 is merged").map((s) => s.text), ["#123"]);
  assert.deepEqual(links("merged—example-org/other#3").map((s) => s.href), [url(3, "example-org/other")]);
  // a hyphen does end them: `https://example.com/a-#12` is a URL with a fragment, `foo-#12` an identifier
  for (const t of ["https://example.com/a-#12", "x -#12", "foo-#12", "-#12"]) assert.equal(links(t).length, 0, t);
});

test("a # glued to a word, a path or a URL is not a reference", () => {
  for (const t of ["foo#12", "docs/x.html#12", "https://example.com/page#12", "github.com/example-org/notes-api#12",
                   "a/b/c#12", "&#12;", "C#12", "v1.2#3"]) {
    assert.equal(links(t).length, 0, JSON.stringify(t));
  }
});

test("the cross-repo form follows GitHub's name grammar: owner = alphanumerics and single inner hyphens, repo may add . and _", () => {
  // valid owners link, into the repository named
  for (const [t, repo] of [["a-b/c-d#1", "a-b/c-d"], ["a1/b_2-v3#1", "a1/b_2-v3"], ["x/y#1", "x/y"], ["ab/c.d.e-f#1", "ab/c.d.e-f"]] as const)
    assert.equal(links("see " + t)[0]?.href, url(1, repo), t);
  // (a dotted repo whose tail is extension-shaped — `b_2.3`, `c.d.e` — is the filename refusal below, not the grammar)
  // two plain words around a slash ARE GitHub's syntax (a directory path never carries a bare numeric
  // fragment — a line reference is `file.py#L12`), so they link; pinned as the documented reading
  assert.equal(links("see src/lib#3")[0]?.href, url(3, "src/lib"));
  assert.equal(links("see kernel/kernel#12")[0]?.href, url(12, "kernel/kernel"));
  // owners GitHub does not allow never link — and the number after them does not fall through to a bare #n
  for (const t of ["my.org/repo#1", "-owner/repo#1", "owner-/repo#1", "ow--ner/repo#1", "own_er/repo#1", "a/b/c#1"])
    assert.equal(links("see " + t).length, 0, t);
  // an owner longer than GitHub's 39 characters is no owner
  assert.equal(links("see " + "a".repeat(40) + "/repo#1").length, 0);
  assert.equal(links("see " + "a".repeat(39) + "/repo#1").length, 1);
});

test("a repo name never ends in a dot: a/..#12, a/.#12 and owner/repo.#1 stay plain, in the text rule and in validPrRepo", () => {
  for (const t of ["a/..#12", "a/.#12", "example-org/..#7", "owner/repo.#1", "owner/repo..#1", "example-org/notes-api.#7"])
    assert.equal(links("see " + t).length, 0, t);
  // the number after a refused name does not fall through to a bare #n (the scan resumes after the whole)
  assert.deepEqual(prRefSegments("see example-org/notes-api.#7", REPO), [{ text: "see example-org/notes-api.#7" }]);
  // with the space prose actually has, the sentence ends and the bare #7 links against the session repo
  assert.deepEqual(links("see example-org/notes-api. #7").map((s) => s.href), [url(7)]);
  for (const bad of ["a/..", "a/.", "a/...", "owner/repo.", "owner/repo.."]) assert.equal(validPrRepo(bad), null, bad);
  assert.equal(validPrRepo("a/.b"), "a/.b", "a leading dot is fine (dotfile-named repos exist)");
  assert.equal(validPrRepo("a/b.c"), "a/b.c");
});

test("a cross-repo form whose repo reads like a filename is a path fragment, not a reference — except a GitHub Pages repo", () => {
  for (const t of ["docs/x.html#12", "src/app.py#3", "notes/todo.md#7", "a/b.c#1"]) assert.equal(links("see " + t).length, 0, t);
  // the documented cost: a repository literally named like a file stays plain in the cross-repo form
  for (const t of ["org/tool.dev#4", "org/something.js#4"]) assert.equal(links("see " + t).length, 0, t);
  // …but a GitHub Pages repo (`*.github.io`) is the one standard dotted repo name, and it links (any case,
  // any owner — a fork keeps the original's name)
  assert.equal(links("see org/org.github.io#4")[0]?.href, url(4, "org/org.github.io"));
  assert.equal(links("see Org/Org.GitHub.IO#4")[0]?.href, url(4, "Org/Org.GitHub.IO"));
  assert.equal(links("see me/them.github.io#4")[0]?.href, url(4, "me/them.github.io"));
  assert.equal(links("see x/y.github.io.js#4").length, 0, "only as the tail");
  // a dotted repo name whose tail is not extension-shaped still links; and the scan resumes after a refused one
  assert.equal(links("see my-org/my.repo_v2#7")[0].href, url(7, "my-org/my.repo_v2"));
  assert.deepEqual(links("docs/x.html#12 then #13").map((s) => s.text), ["#13"]);
});

test("a session's OWN repo is never filename-filtered: a Pages or file-named checkout links its bare #n", () => {
  assert.equal(links("merged #4", "user/user.github.io")[0]?.href, url(4, "user/user.github.io"));
  assert.equal(links("merged #4", "org/something.js")[0]?.href, url(4, "org/something.js"));
});

test("a GitHub URL with a fragment stays as it is (its #issuecomment is no number)", () => {
  assert.equal(links("https://github.com/example-org/notes-api/pull/12#issuecomment-99").length, 0);
});

test("color literals never link: hex letters after the digits, or a leading zero, fail the number shape", () => {
  for (const t of ["#fff", "#FFF", "#000", "#1EA1EB", "#0c1a2e", "#9cd2ff", "#12ab", "#12abcd", "#0", "#01"]) {
    assert.equal(links("color " + t).length, 0, t);
  }
  // the documented cost: an all-decimal six-digit color is indistinguishable from a PR number in prose
  // (in code or a code-like element it never links — see the applier tests)
  assert.equal(links("color #123456")[0]?.text, "#123456");
});

test("a number glued to letters, another # or a slash after it is not a reference", () => {
  for (const t of ["#12abc", "#12_x", "#12#13", "#12/x"]) assert.equal(links("x " + t).length, 0, t);
  // sentence punctuation after it is fine
  for (const t of ["#12.", "#12,", "#12)", "#12;", "#12!", "#12?"]) assert.equal(links("x " + t)[0]?.text, "#12", t);
});

test("a slash-separated run of references links each one, as GitHub's own autolinker does", () => {
  // the exact segments: the slashes between the references stay plain text
  assert.deepEqual(prRefSegments("landed #12/#13.", REPO), [
    { text: "landed " },
    { text: "#12", href: url(12), label: REPO + "#12" },
    { text: "/" },
    { text: "#13", href: url(13), label: REPO + "#13" },
    { text: "." },
  ]);
  assert.deepEqual(links("PRs #12/#13/#14 are up").map((s) => s.text), ["#12", "#13", "#14"]);
  assert.deepEqual(links("(#12/#13)").map((s) => s.href), [url(12), url(13)]);
  // after a `PR #n` phrase the run continues the same way; the phrase stays the first link's text
  assert.deepEqual(links("PR #12/#13").map((s) => s.text), ["PR #12", "#13"]);
  assert.deepEqual(links("PR #12/#13").map((s) => s.href), [url(12), url(13)]);
  // a run that ends in a path is a path, like `#12/x` — nothing links, not even the first
  for (const t of ["#12/#13/x", "#12/#13abc", "#12/#13#14"]) assert.equal(links("x " + t).length, 0, t);
  // `/` alone is still no boundary: a URL's fragment never starts a run, and a space breaks one
  assert.equal(links("https://example.com/y/#12/#13").length, 0);
  assert.deepEqual(links("#12/ #13").map((s) => s.text), ["#13"]);
  // after a cross-repo reference a slash refuses the whole: GitHub reads the second against the context, a
  // writer means the named repo — two readings, no link
  assert.equal(links("see example-org/other#12/#13").length, 0);
  // the applier renders the run as sibling anchors with plain slashes between
  const p = el("p", "landed #12/#13");
  assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 2);
  assert.equal(p.html(), `<p>landed <a class="pr-link" href="${url(12)}">#12</a>/<a class="pr-link" href="${url(13)}">#13</a></p>`);
});

test("without a GitHub repo for the session NOTHING links — the cross-repo form included", () => {
  for (const repo of [null, undefined, "", "not a repo", "a/b/c", "https://github.com/x/y"]) {
    assert.deepEqual(prRefSegments("merged #1 and example-org/other#2", repo as any), [{ text: "merged #1 and example-org/other#2" }], String(repo));
  }
  assert.equal(validPrRepo("example-org/notes-api"), "example-org/notes-api");
  assert.equal(validPrRepo("owner/repo.js"), "owner/repo.js");
  assert.equal(validPrRepo("user/user.github.io"), "user/user.github.io");
  assert.equal(validPrRepo("-owner/repo"), null, "an owner cannot start with a hyphen");
  assert.equal(validPrRepo("owner-/repo"), null, "or end with one");
  assert.equal(validPrRepo("ow--ner/repo"), null, "or double one");
  assert.equal(validPrRepo("my.org/repo"), null, "or carry a dot");
  assert.equal(validPrRepo("owner/re po"), null);
});

test("text with no # at all comes back as one plain segment, fast", () => {
  assert.deepEqual(prRefSegments("nothing to see", REPO), [{ text: "nothing to see" }]);
  assert.deepEqual(prRefSegments("", REPO), [{ text: "" }]);
});

// ── the DOM applier ──────────────────────────────────────────────────────────────────────────────

test("a text node's references become pr-link anchors with href, target, rel and a repo-qualified title", () => {
  const p = el("p", "merged #12 today");
  assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 1);
  assert.equal(p.html(), `<p>merged <a class="${PR_LINK_CLASS}" href="${url(12)}">#12</a> today</p>`);
  const a = anchors(p)[0];
  assert.equal(a.target, "_blank");
  assert.equal(a.rel, "noopener noreferrer");
  assert.equal(a.title, REPO + "#12 on GitHub");
  assert.equal(p.textContent, "merged #12 today", "the visible text is unchanged");
});

test("text inside <code> and <pre> is skipped; the prose around it still links", () => {
  const p = el("p", "use ", el("code", "#12"), " and #13");
  assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 1);
  assert.equal(p.html(), `<p>use <code>#12</code> and <a class="pr-link" href="${url(13)}">#13</a></p>`);
  const pre = el("pre", el("code", "git log #12"));
  assert.equal(linkifyPrRefs(pre as unknown as Node, REPO), 0);
  assert.equal(pre.html(), "<pre><code>git log #12</code></pre>");
});

test("the other code-like elements are opaque too: kbd, samp, var, tt — a quoted key or token is not a reference", () => {
  for (const tag of ["kbd", "samp", "var", "tt"]) {
    const p = el("p", "see ", el(tag, "#12"), " and #13");
    assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 1, tag);
    assert.equal(anchors(p).length, 1, tag);
    assert.equal(anchors(p)[0].textContent, "#13", tag);
  }
  // typographic wrappers are walked: a superscripted or emphasized #n is still a reference
  for (const tag of ["sup", "sub", "em", "strong", "span", "small"]) {
    const p = el("p", "see ", el(tag, "#12"));
    assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 1, tag);
  }
});

test("text already inside a link is never wrapped again", () => {
  const a = el("a", "#12"); a.href = "https://github.com/example-org/notes-api/pull/12";
  const p = el("p", "see ", a);
  assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 0);
  assert.equal(anchors(p).length, 1);
});

test("the chat's path links are skipped, so a docs/x.md#12-shaped path stays the path it is", () => {
  const p = el("p", cls(el("span", "docs/x.md#12"), "file-uri-link"), " and ", cls(el("span", el("code", "https://x/#1")), "url-code-link"));
  assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 0);
});

test("the boundary is judged over the rendered text: a reference at the head of a text node follows the character before that node", () => {
  // the pure rule, given the character that precedes the text in the run it was cut from: a word character
  // or a slash glues, a boundary does not, and no character at all is the start of the run
  const after = (text: string, before: string) => prRefSegments(text, REPO, before).filter((s) => s.href).map((s) => s.text);
  assert.deepEqual(prRefSegments("#12 next", REPO, "d"), [{ text: "#12 next" }]);
  assert.deepEqual(prRefSegments("#12 next", REPO, "/"), [{ text: "#12 next" }], "a slash is no boundary on its own");
  assert.deepEqual(prRefSegments("#12 next", REPO, "-"), [{ text: "#12 next" }], "the ASCII hyphen ends a URL path or an identifier");
  for (const b of [" ", "\n", "\t", "(", "[", "{", "<", ",", ";", ":", "\"", "'", "“", "‘", "«", "—", "–"]) assert.deepEqual(after("#12 next", b), ["#12"], JSON.stringify(b));
  assert.deepEqual(after("#12 next", ""), ["#12"], "an empty before is the start of the run");
  // `before` is one UTF-16 code unit: an emoji cut off at the node edge leaves a lone surrogate, no boundary;
  // the one-node reading of the same text refuses it too, so the two agree
  assert.deepEqual(prRefSegments("#12 next", REPO, "🎉".slice(-1)), [{ text: "#12 next" }], "a lone surrogate is no boundary");
  assert.deepEqual(after("🎉#12 next", ""), [], "the one-node reading agrees");
  assert.deepEqual(prRefSegments("#12 next", REPO, " a"), [{ text: "#12 next" }], "a longer string is never a boundary: the caller passes one character");
  assert.deepEqual(prRefSegments("#12 next", REPO).filter((s) => s.href).map((s) => s.text), ["#12"], "the two-argument call is unchanged");
  assert.deepEqual(after("PR #12 next", "d"), ["#12"], "a glued phrase: the bare reference inside it still follows a space, as the one-node reading of `dPR #12` does");
  assert.deepEqual(after("example-org/other#4 and #5", "d"), ["#5"], "a glued cross-repo form is refused; the scan resumes after it");
  assert.deepEqual(after("#12 and #13", "d"), ["#13"], "the scan resumes after the refused match");
  assert.deepEqual(after("#12/#13 then #9", "d"), ["#9"], "a glued run links nothing: `/` is no boundary, so its later members cannot start a reference");
  assert.deepEqual(after("#12", "x"), []);
  assert.deepEqual(prRefSegments("#12", REPO, "x"), [{ text: "#12" }], "a refused text comes back whole");
  // through the walk: a path link followed by its fragment in the next node, which is what a path pass that
  // runs before this one leaves behind (the span stands in for that pass's output)
  const split = el("p", "see ", cls(el("span", "docs/a.md"), "file-uri-link"), "#12 and #13");
  assert.equal(linkifyPrRefs(split as unknown as Node, REPO), 1);
  assert.deepEqual(anchors(split).map((a) => a.textContent), ["#13"]);
  assert.equal(split.textContent, "see docs/a.md#12 and #13", "the text is unchanged");
  const run = el("p", "check ", cls(el("span", "notes/plan.md"), "file-uri-link"), "#7/#8 then #9");
  assert.equal(linkifyPrRefs(run as unknown as Node, REPO), 1);
  assert.deepEqual(anchors(run).map((a) => a.textContent), ["#9"]);
});

test("an element's text ends the run too: #12 glued to <code>x</code> is no reference, #12 after a space is; an inline element with no text carries the run", () => {
  // marked renders `docs/a.md`#12, **docs/a.md**#12 and [text](url)#12 as an element followed by a text
  // node that begins `#12`; the start of that node is no boundary
  const glued = el("p", "use ", el("code", "x"), "#12");
  assert.equal(linkifyPrRefs(glued as unknown as Node, REPO), 0);
  assert.equal(glued.html(), "<p>use <code>x</code>#12</p>");
  const spaced = el("p", "use ", el("code", "x"), " #12");
  assert.equal(linkifyPrRefs(spaced as unknown as Node, REPO), 1);
  const a = el("a", "text"); a.href = "https://x/y";
  const afterLink = el("p", "see ", a, "#12");
  assert.equal(linkifyPrRefs(afterLink as unknown as Node, REPO), 0, "[text](url)#12");
  const afterStrong = el("p", "read ", el("strong", "docs/a.md"), "#12 now");
  assert.equal(linkifyPrRefs(afterStrong as unknown as Node, REPO), 0, "**docs/a.md**#12");
  assert.equal(afterStrong.html(), "<p>read <strong>docs/a.md</strong>#12 now</p>");
  // an empty INLINE element between (an <img>, an empty <span>) does not reset the run: the text before it decides
  const img = el("p", "see ", cls(el("span", "docs/a.md"), "file-uri-link"), el("img"), "#12");
  assert.equal(linkifyPrRefs(img as unknown as Node, REPO), 0);
  const imgSpaced = el("p", "see docs ", el("img"), "#12");
  assert.equal(linkifyPrRefs(imgSpaced as unknown as Node, REPO), 1);
  const emptySpan = el("p", "docs", el("span"), "#12");
  assert.equal(linkifyPrRefs(emptySpan as unknown as Node, REPO), 0);
  // a SKIPPED element with no text (an empty <code>, a form control) carries the run the same way
  for (const tag of ["code", "input"]) {
    assert.equal(linkifyPrRefs(el("p", "docs", el(tag), "#12") as unknown as Node, REPO), 0, `an empty <${tag}> carries the run`);
    assert.equal(linkifyPrRefs(el("p", "docs ", el(tag), "#12") as unknown as Node, REPO), 1, `an empty <${tag}> after a space`);
  }
  // a walked wrapper's text carries into the node after it, and the run before it carries into its first node
  const wrapped = el("p", el("em", "see"), " #8 and ", el("strong", "ok"), "#9 and ", el("strong", "x"), " ", el("em", "#10"));
  assert.equal(linkifyPrRefs(wrapped as unknown as Node, REPO), 2);
  assert.deepEqual(anchors(wrapped).map((a) => a.textContent), ["#8", "#10"]);
  const intoWrapper = el("p", "docs/a.md", el("em", "#12"));
  assert.equal(linkifyPrRefs(intoWrapper as unknown as Node, REPO), 0, "a fragment inside a wrapper is glued to the text before the wrapper");
  const nested = el("p", "see ", el("em", el("strong", "docs/a.md")), "#12 and ", el("em", el("strong", "x")), " #13");
  assert.equal(linkifyPrRefs(nested as unknown as Node, REPO), 1, "the carry comes back out of nested wrappers");
  assert.deepEqual(anchors(nested).map((a) => a.textContent), ["#13"]);
  // a node that is neither text nor an element (a comment) renders nothing: the run is unbroken across it
  class C { nodeType = 8; parentNode: E | null = null; constructor(public textContent: string) {} }
  const commented = el("p", "docs", new C(" a note ") as unknown as E, "#12");
  assert.equal(linkifyPrRefs(commented as unknown as Node, REPO), 0);
  const commentedSpaced = el("p", "docs ", new C(" a note ") as unknown as E, "#12");
  assert.equal(linkifyPrRefs(commentedSpaced as unknown as Node, REPO), 1);
  // what FOLLOWS a reference is still judged per node: a `#12` that ends a wrapper links although the next
  // node begins with letters, where the one-node reading of the same text refuses; outside this change
  const trailing = el("p", "see ", el("em", "#12"), "abc");
  assert.equal(linkifyPrRefs(trailing as unknown as Node, REPO), 1);
  assert.deepEqual(prRefSegments("see #12abc", REPO), [{ text: "see #12abc" }]);
});

// ── marked's HTML read into the stand-in, so the chat's own markdown shapes run through the walk ─────
const VOID_TAGS = new Set(["BR", "HR", "IMG", "INPUT", "WBR"]);
const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: "\"", "#39": "'" };
const unescapeHtml = (s: string): string => s.replace(/&(amp|lt|gt|quot|#39);/g, (_, k: string) => ENTITIES[k]);
/** tags and text only, the class attribute kept: enough for what marked emits */
function fromHtml(html: string): E {
  const root = new E("DIV");
  let cur = root;
  const re = /<\/([A-Za-z][\w-]*)\s*>|<([A-Za-z][\w-]*)([^>]*)>|([^<]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    if (m[1]) { cur = cur.parentNode || root; continue; }
    if (m[2]) {
      const e = cur.appendChild(new E(m[2].toUpperCase()));
      const c = /class="([^"]*)"/.exec(m[3] || "");
      if (c) e.className = c[1];
      if (!VOID_TAGS.has(e.tagName) && !/\/\s*$/.test(m[3] || "")) cur = e;
      continue;
    }
    cur.appendChild(new T(unescapeHtml(m[4])));
  }
  return root;
}
/** a node list as tags and text: ["done", "BR", "#12 next"] */
const shape = (e: E): string[] => e.childNodes.map((c) => (c instanceof T ? c.textContent : c.tagName));
/** the links this module made, in order; an anchor marked wrote (`[text](url)`) is not one */
const linked = (root: E): string[] => anchors(root).filter((a) => a.classList.contains(PR_LINK_CLASS)).map((a) => a.textContent);
// the chat's options (render.ts md()); its extensions and the sanitizer play no part in the shapes read here,
// and an instance of our own leaves the shared singleton as the other tests find it
const chatMarked = new Marked({ gfm: true, breaks: false });
const md = (src: string): E => fromHtml(chatMarked.parse(src) as string);

test("a line or block edge is a boundary with no whitespace node: marked's hard-break <br>, abutting paragraphs, list items, table cells; an inline element with no text is not", () => {
  // a GFM hard break (two trailing spaces, or a backslash): <br> with NO text node between it and the `#`
  let root = md("done  \n#12 next");
  assert.deepEqual(shape(root.childNodes[0] as E), ["done", "BR", "#12 next"], "marked's shape: no whitespace after the <br>");
  assert.equal(linkifyPrRefs(root as unknown as Node, REPO), 1);
  assert.deepEqual(linked(root), ["#12"]);
  root = md("done\\\n#12 next");
  assert.deepEqual((linkifyPrRefs(root as unknown as Node, REPO), linked(root)), ["#12"], "the backslash form");
  root = md("done  \nPR #12 next");
  assert.deepEqual((linkifyPrRefs(root as unknown as Node, REPO), linked(root)), ["PR #12"]);
  root = md("done  \nexample-org/other#4 next");
  assert.deepEqual((linkifyPrRefs(root as unknown as Node, REPO), linked(root)), ["example-org/other#4"]);
  root = md("done  \n#12/#13 next");
  assert.deepEqual((linkifyPrRefs(root as unknown as Node, REPO), linked(root)), ["#12", "#13"], "a run at the head of the line");
  // the block shapes marked itself emits: a paragraph, a list item, a table cell, a heading, a quote, a fence, a rule
  for (const src of ["done\n\n#12", "- fixed\n- #12", "| a | b |\n|---|---|\n| fix | #12 |", "## Status\n#12", "> #12", "```\nx\n```\n#12", "---\n#12"]) {
    root = md(src);
    assert.deepEqual((linkifyPrRefs(root as unknown as Node, REPO), linked(root)), ["#12"], JSON.stringify(src));
  }
  // the inline shapes marked cuts before a reference, from the chat's own markdown: inline code, emphasis, an explicit link
  for (const src of ["`docs/a.md`#12", "**docs/a.md**#12", "*docs/a.md*#12", "[text](https://x/y)#12", "a <span>x</span>#12"]) {
    root = md(src);
    assert.equal(linkifyPrRefs(root as unknown as Node, REPO), 0, src);
  }
  for (const src of ["`docs/a.md` #12", "**done** #12", "[text](https://x/y) #12", "(**done**) #12"]) {
    root = md(src);
    assert.deepEqual((linkifyPrRefs(root as unknown as Node, REPO), linked(root)), ["#12"], src);
  }
  // marked separates blocks with a "\n" text node; a DOM whose blocks abut has the same boundary
  const abutting: E[] = [
    el("div", el("p", "done"), el("p", "#12")),
    el("ul", el("li", "fixed"), el("li", "#12")),
    el("li", "#12"),
    el("tr", el("td", "fix"), el("td", "#12")),
    el("div", el("h2", "Status"), el("p", "#12")),
    el("div", el("p", "note"), el("blockquote", "#12")),
    el("div", el("pre", "x"), "#12"),
    el("div", "done", el("hr"), "#12"),
    el("div", el("p", "done"), "#12"),
    el("p", el("span", "docs"), el("br"), "#12"),
    el("div", el("p", "x"), el("div", "#12")),
    // block-display tags a message's raw HTML may carry: the carry stops at them too
    el("li", "done", el("center", "#12")),
    el("div", "done", el("legend", "#12")),
    el("div", "done", el("dialog", "#12")),
    el("div", el("center", "done"), "#12"),
    el("div", "done", el("hgroup", "#12")),
    el("div", el("details", el("summary", "more")), "#12"),
  ];
  for (const r of abutting) assert.deepEqual((linkifyPrRefs(r as unknown as Node, REPO), linked(r)), ["#12"], r.html());
  // every tag on the module's block list (copied here, so a tag dropped from it fails its own row) is an edge on
  // both sides: a `#12` at its head follows glued text, and text right after it starts a run of its own; a
  // <pre> is code-like, so only its far side is checked
  const blockTags = [
    "br", "hr", "p", "pre", "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "td", "th",
    "div", "dl", "dt", "dd", "section", "article", "header", "footer", "nav", "aside", "main",
    "figure", "figcaption", "details", "summary", "form", "fieldset", "address",
    "center", "legend", "dialog", "dir", "menu", "hgroup", "search",
  ];
  assert.equal(blockTags.length, 47);
  for (const tag of blockTags) {
    if (tag === "br" || tag === "hr") {
      const r = el("div", "done", el(tag), "#12");
      assert.deepEqual((linkifyPrRefs(r as unknown as Node, REPO), linked(r)), ["#12"], `<${tag}> between`);
      continue;
    }
    if (tag !== "pre") {
      const head = el("div", "done", el(tag, "#12"));
      assert.deepEqual((linkifyPrRefs(head as unknown as Node, REPO), linked(head)), ["#12"], `<${tag}> starts a run`);
    }
    const tail = el("div", el(tag, "done"), "#12");
    assert.deepEqual((linkifyPrRefs(tail as unknown as Node, REPO), linked(tail)), ["#12"], `<${tag}> ends a run`);
  }
  // a tag name is read case-insensitively, as the skip list always was
  const lower = el("div", "done", new E("br"), "#12");
  assert.deepEqual((linkifyPrRefs(lower as unknown as Node, REPO), linked(lower)), ["#12"]);
  // the glue protections stay: a path span, an empty inline element, an empty walked wrapper, a fragment inside one
  for (const r of [
    el("p", "see ", cls(el("span", "docs/a.md"), "file-uri-link"), "#12"),
    el("p", "docs", el("span"), "#12"),
    el("p", "docs", el("img"), "#12"),
    el("p", "docs", el("em", ""), "#12"),
    el("p", "see docs/a.md", el("em", "#12")),
  ]) assert.equal(linkifyPrRefs(r as unknown as Node, REPO), 0, r.html());
});

test("references inside emphasis and list items link (only anchors and code-like elements are opaque)", () => {
  const li = el("li", el("strong", "PR #7"), " shipped; see ", el("em", "#8"));
  assert.equal(linkifyPrRefs(li as unknown as Node, REPO), 2);
  assert.equal(li.html(), `<li><strong><a class="pr-link" href="${url(7)}">PR #7</a></strong> shipped; see <em><a class="pr-link" href="${url(8)}">#8</a></em></li>`);
});

test("a root that itself sits inside code, a code-like element or a link is left alone", () => {
  for (const tag of ["code", "pre", "kbd", "samp", "var", "tt", "a"]) {
    const inner = el("span", "#12");
    el(tag, inner);
    assert.equal(linkifyPrRefs(inner as unknown as Node, REPO), 0, tag);
  }
});

test("a root whose whole text has no # is not walked at all — one textContent read, no childNodes access", () => {
  const p = el("p", "a long body ", el("em", "with markup"), " but no reference");
  let reads = 0;
  const kids = p.childNodes;
  // the stand-in's own textContent getter walks childNodes; a browser's is one native read, so stand it in too
  Object.defineProperty(p, "textContent", { get() { return "a long body with markup but no reference"; } });
  Object.defineProperty(p, "childNodes", { get() { reads++; return kids; } });
  assert.equal(linkifyPrRefs(p as unknown as Node, REPO), 0);
  assert.equal(reads, 0, "the walk was skipped");
  // …and one WITH a reference is walked as before
  const q = el("p", "a body ", el("em", "with #12"));
  assert.equal(linkifyPrRefs(q as unknown as Node, REPO), 1);
});

test("no repo → the DOM is not touched at all", () => {
  const p = el("p", "merged #12 and example-org/other#3");
  assert.equal(linkifyPrRefs(p as unknown as Node, null), 0);
  assert.equal(p.html(), "<p>merged #12 and example-org/other#3</p>");
  assert.equal(linkifyPrRefs(null, REPO), 0);
});

// ── setLinkedText: the keyed in-place update the feed's card titles use ─────────────────────────

test("setLinkedText writes the text with its links once, and leaves the SAME anchor nodes in place while (text, repo) is unchanged", () => {
  const title = el("div");
  setLinkedText(title as unknown as HTMLElement, "merged #12 today", REPO);
  assert.equal(title.html(), `<div>merged <a class="pr-link" href="${url(12)}">#12</a> today</div>`);
  const first = anchors(title)[0];
  setLinkedText(title as unknown as HTMLElement, "merged #12 today", REPO);   // the next push, nothing changed
  setLinkedText(title as unknown as HTMLElement, "merged #12 today", REPO);
  assert.equal(anchors(title)[0], first, "identity survives the pushes — the press and the release meet one node");
  assert.deepEqual(title.dataset, { prText: "merged #12 today", prRepo: REPO });
});

test("setLinkedText re-renders when the text or the repo changes, and renders plain text with no repo", () => {
  const title = el("div");
  setLinkedText(title as unknown as HTMLElement, "merged #12", REPO);
  const first = anchors(title)[0];
  setLinkedText(title as unknown as HTMLElement, "merged #12 and #13", REPO);
  assert.equal(anchors(title).length, 2);
  assert.notEqual(anchors(title)[0], first, "new text, new nodes");
  setLinkedText(title as unknown as HTMLElement, "merged #12 and #13", "example-org/other");
  assert.equal(anchors(title)[0].href, url(12, "example-org/other"), "a repo change re-links");
  setLinkedText(title as unknown as HTMLElement, "merged #12 and #13", null);
  assert.equal(title.html(), "<div>merged #12 and #13</div>");
  assert.equal(title.dataset.prRepo, "");
  setLinkedText(title as unknown as HTMLElement, "merged #12 and #13", "not a repo");
  assert.equal(title.html(), "<div>merged #12 and #13</div>", "an invalid repo is no repo");
});

// ── senderPrRepo: a message links against its SENDER's frame-known repo, never a guess ──────────

const U = "11111111-2222-3333-4444-555555555555";
const V = "99999999-8888-7777-6666-555555555555";
const rows = [
  { sid: U, name: "web", githubRepo: "example-org/notes-web" },
  { sid: V, name: "api", githubRepo: "example-org/notes-api" },
  { sid: "TESTHOST:" + U, name: "TESTHOST:tests", githubRepo: "example-org/notes-tests" },
  { sid: "TESTHOST:" + V, name: "TESTHOST:api", githubRepo: "other-org/api" },
  { sid: "22222222-2222-3333-4444-555555555555", name: "norepo", githubRepo: null },
];

test("with no host named (the chat's postal cards): the one session answering to the name, local or federated by its bare name", () => {
  assert.equal(senderPrRepo(rows, "web"), "example-org/notes-web");
  assert.equal(senderPrRepo(rows, "tests"), "example-org/notes-tests", "a federated row matches on its bare name");
  assert.equal(senderPrRepo(rows, "api"), null, "a homonym on another attached host makes the sender ambiguous: no guess");
  assert.equal(senderPrRepo(rows, "norepo"), null, "a sender the kernel gave no repo links nothing");
  assert.equal(senderPrRepo(rows, "nobody"), null, "an unknown sender links nothing — the reader's repo is never substituted");
  assert.equal(senderPrRepo(rows, ""), null);
  assert.equal(senderPrRepo(rows, "TESTHOST:api"), null, "the kernel writes the bare name; a prefixed one matches no row");
});

test("with the sender's host named (the feed's held-mail cards): exactly that host's row", () => {
  assert.equal(senderPrRepo(rows, "api", ""), "example-org/notes-api", "the viewing kernel's own host → the local row");
  assert.equal(senderPrRepo(rows, "api", "TESTHOST"), "other-org/api", "another host → its federated row");
  assert.equal(senderPrRepo(rows, "tests", ""), null, "no local row of that name");
  assert.equal(senderPrRepo(rows, "web", "OTHERHOST"), null, "a host not in the frame");
});

test("postalSenderHost: the chat's inbound card names the sender's host, and the sender resolves to exactly one session or none", () => {
  const SELF = "SELFHOST";
  assert.equal(postalSenderHost("", SELF), "", "mail from this kernel's own sessions → the local rows");
  assert.equal(postalSenderHost("TESTHOST", SELF), "TESTHOST", "mail from an attached peer → that host's federated rows");
  assert.equal(postalSenderHost(SELF, SELF), "", "relayed mail stamped with this kernel's own name → the local rows");
  assert.equal(postalSenderHost(undefined, SELF), undefined, "a card from a kernel that predates the field → the name-only fallback");
  assert.equal(postalSenderHost("?", SELF), undefined, "an unknown origin → the name-only fallback, as the feed reads blocked.origin");
  assert.equal(postalSenderHost(null, SELF), undefined);
  assert.equal(postalSenderHost(7, SELF), undefined);
  // through senderPrRepo: the local `api` is no longer shadowed by the attached homonym, and a sender on a
  // host NOT in the frame is plain text — never the local homonym's repo, which the name alone produced
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("", SELF)), "example-org/notes-api");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("TESTHOST", SELF)), "other-org/api");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("UNATTACHED", SELF)), null, "the sender's host is not in the frame: no guess");
  assert.equal(senderPrRepo(rows, "web", postalSenderHost("UNATTACHED", SELF)), null);
  assert.equal(senderPrRepo(rows, "api", postalSenderHost(undefined, SELF)), null, "legacy card: the homonym keeps it plain");
  assert.equal(senderPrRepo(rows, "web", postalSenderHost(undefined, SELF)), "example-org/notes-web", "legacy card: one session answers to the name");
  assert.equal(senderPrRepo(rows, "TESTHOST:aaaaaaaa", postalSenderHost("TESTHOST", SELF)), null, "the kernel's host:sid stub for a nameless sender matches no row");
});

test("postalSenderHost reads peerHost relative to the CARD's kernel: a federated session's card from its own kernel's sender resolves on that host, never against the local rows", () => {
  const SELF = "SELFHOST";
  // the card sits in TESTHOST:tests; TESTHOST's log stamped "" for mail from its own `api`
  assert.equal(postalSenderHost("", SELF, "TESTHOST"), "TESTHOST", "'' means the card's own host");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("", SELF, "TESTHOST")), "other-org/api",
    "TESTHOST's own api, not the local homonym's repo (which relative-to-the-dashboard reading produced)");
  // the same card's kernel names THIS dashboard's kernel as the sender's host: the local rows
  assert.equal(postalSenderHost(SELF, SELF, "TESTHOST"), "", "the dashboard's own name folds to the local rows, whatever session the card sits in");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost(SELF, SELF, "TESTHOST")), "example-org/notes-api");
  // a third host: matched by name among the frame's rows — attached, its row; not attached, plain text
  assert.equal(postalSenderHost("OTHERHOST", SELF, "TESTHOST"), "OTHERHOST");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("OTHERHOST", SELF, "TESTHOST")), null, "a host this dashboard has not attached: no guess");
  assert.equal(postalSenderHost("TESTHOST", SELF, "TESTHOST"), "TESTHOST", "a kernel naming itself reads the same as ''");
  // the legacy and unknown-origin forms are unchanged by the card's host
  assert.equal(postalSenderHost(undefined, SELF, "TESTHOST"), undefined);
  assert.equal(postalSenderHost("?", SELF, "TESTHOST"), undefined);
  // a LOCAL card (cardHost "" — the default) reads exactly as before
  assert.equal(postalSenderHost("", SELF, ""), "");
  assert.equal(postalSenderHost("", SELF), "");
  assert.equal(postalSenderHost("TESTHOST", SELF, ""), "TESTHOST");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("", SELF, "")), "example-org/notes-api", "a local card from a local sender: the local row");
});

test("postalSenderHost with the dashboard's own name not yet known (selfHost ''): the card-host and peer readings hold, and nothing folds by accident", () => {
  assert.equal(postalSenderHost("", "", ""), "", "a local card, local sender");
  assert.equal(postalSenderHost("", "", "TESTHOST"), "TESTHOST", "a federated card, its kernel's own sender");
  assert.equal(postalSenderHost("TESTHOST", "", ""), "TESTHOST", "a named peer stays named — '' as selfHost matches no name");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("TESTHOST", "", "")), "other-org/api");
});

test("peerHost is the card's kernel's OWN name for the sender's host: when the two kernels' names for a host disagree the reference stays text, never a wrong link", () => {
  // the dashboard's kernel calls itself SELFHOST and declared that at check-in; the card's kernel (TESTHOST)
  // had attached this machine under an alias of its own, HUBALIAS, so its log stamps mail from here with
  // that name (the bus files a peer under the local dialable alias when one exists) — the fold does not apply…
  assert.equal(postalSenderHost("HUBALIAS", "SELFHOST", "TESTHOST"), "HUBALIAS");
  // …and the alias names no row this dashboard holds (its rows wear the aliases IT attached hosts as), so the
  // sender is plain text — not the local api's repo, not TESTHOST's api's
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("HUBALIAS", "SELFHOST", "TESTHOST")), null);
  assert.equal(senderPrRepo(rows, "web", postalSenderHost("HUBALIAS", "SELFHOST", "TESTHOST")), null);
  // the same disagreement over a third host: TESTHOST's name for it is not the one this dashboard attached it as
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("OTHERBOX", "SELFHOST", "TESTHOST")), null);
  // agreement — the common topology — folds to the local row; the stamp is compared to selfHost exactly
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("SELFHOST", "SELFHOST", "TESTHOST")), "example-org/notes-api");
  assert.equal(senderPrRepo(rows, "api", postalSenderHost("selfhost", "SELFHOST", "TESTHOST")), null, "no case folding: a near-name is another name");
  // the name is all there is to match on: no host id rides the card or the rows
  assert.doesNotMatch(RENDER, /busId|hostId/, "the chat matches hosts by name alone, as the frames key them");
});

test("senderPrRepo tolerates malformed rows and an invalid repo value", () => {
  assert.equal(senderPrRepo([{ sid: U, name: "web", githubRepo: "https://github.com/x/y" }], "web"), null);
  assert.equal(senderPrRepo([null as any, { sid: 5 as any, name: "web" }, { sid: U, name: "web", githubRepo: "x/y" }], "web"), "x/y");
});

// ── the opener (feed + outline panes) ───────────────────────────────────────────────────────────

/** a document stand-in recording one capture-phase listener per event type */
function fakeDoc() {
  const handlers = new Map<string, (e: Event) => void>();
  const captures = new Map<string, boolean | undefined>();
  return {
    doc: { addEventListener: (type: string, fn: (e: Event) => void, cap?: boolean) => { handlers.set(type, fn); captures.set(type, cap); } },
    /** dispatch one event; returns the default-prevention calls it made */
    fire(type: string, target: any, extra: Record<string, unknown> = {}) {
      const calls: string[] = [];
      const ev: any = { target, button: 0, isPrimary: true, ...extra,
        preventDefault: () => calls.push("preventDefault"), stopPropagation: () => calls.push("stopPropagation") };
      const h = handlers.get(type);
      assert.ok(h, "a listener for " + type);
      h!(ev);
      return calls;
    },
    types: () => Array.from(handlers.keys()).sort(),
    capture: (type: string) => captures.get(type),
  };
}
/** a fresh node standing for a pr-link anchor with `href` — a new object each call, as a rebuilt node is */
const prAnchor = (href: string) => {
  const node = { closest: (sel: string) => sel.startsWith("a.pr-link") ? { getAttribute: (k: string) => k === "href" ? href : null } : null, contains: (n: unknown) => n === node };
  return node;
};
/** a node that is not a link and contains nothing (a button, a card body far from the pressed link) */
const plain = { closest: () => null, contains: () => false };
/** a node that is not a link and contains exactly `kids` — the common ancestor a native click lands on */
const ancestorOf = (...kids: unknown[]) => ({ closest: () => null, contains: (n: unknown) => kids.includes(n) });
const SPENT = ["preventDefault", "stopPropagation"];

function install(protocol = "https:") {
  const f = fakeDoc(); const opened: string[] = []; const posted: any[] = [];
  installPrLinkOpener(f.doc, (m) => posted.push(m), { protocol: () => protocol, open: (h) => opened.push(h) });
  return { f, opened, posted };
}

test("the opener hangs on the stable document, capture phase, for the press, the release, the click and the clearing events", () => {
  const { f } = install();
  assert.deepEqual(f.types(), ["click", "keydown", "pointercancel", "pointerdown", "pointerup"]);
  for (const t of f.types()) assert.equal(f.capture(t), true, t + " — the card's own handler under the link must not fire");
});

test("press and release on a pr-link with the same href open it ONCE — through a rebuilt node — and the click that follows is spent", () => {
  const { f, opened, posted } = install();
  assert.deepEqual(f.fire("pointerdown", prAnchor(url(12))), []);
  assert.deepEqual(opened, [], "nothing opens on the press");
  const twin = prAnchor(url(12));                    // a DIFFERENT node object: the push rebuilt the anchor
  f.fire("pointerup", twin);
  assert.deepEqual(opened, [url(12)]);
  // the native click (here on the common ancestor, the pressed node being gone) is spent: the card's modal never opens
  assert.deepEqual(f.fire("click", ancestorOf(twin)), SPENT);
  assert.deepEqual(opened, [url(12)], "no second open");
  assert.deepEqual(posted, []);
  // the next click is an ordinary click again
  assert.deepEqual(f.fire("click", ancestorOf(twin)), []);
});

test("the spent click is the one on the released node or its ancestors; a click anywhere else passes untouched and clears the flag", () => {
  const { f, opened } = install();
  const twin = prAnchor(url(12));
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", twin);
  assert.deepEqual(opened, [url(12)]);
  // the browser fired no click (the pressed node was detached); the next click has no press behind it — a
  // programmatic .click() from a capture-phase key handler, an assistive-technology activation — and it
  // lands on a card button: it is that button's click, not the link's
  assert.deepEqual(f.fire("click", plain), [], "an unrelated click is never eaten");
  assert.deepEqual(f.fire("click", ancestorOf(twin)), [], "and the flag is gone: the released node's ancestor is an ordinary click now");
  // the same stale state, then an activation of ANOTHER pr-link with no press: it opens
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", prAnchor(url(12)));
  assert.deepEqual(f.fire("click", prAnchor(url(13))), SPENT, "the click path serves it");
  assert.deepEqual(opened, [url(12), url(12), url(13)]);
  // a click on a node that is not an ancestor, even though it reports contains() for others, passes
  f.fire("pointerdown", prAnchor(url(12))); const up = prAnchor(url(12)); f.fire("pointerup", up);
  assert.deepEqual(f.fire("click", ancestorOf(prAnchor(url(12)))), [], "an ancestor of some OTHER node is unrelated");
});

test("when the anchor survives, the same press/release/click sequence still opens exactly once", () => {
  const { f, opened } = install();
  const a = prAnchor(url(12));
  f.fire("pointerdown", a); f.fire("pointerup", a);
  assert.deepEqual(f.fire("click", a), SPENT);
  assert.deepEqual(opened, [url(12)]);
});

test("a release elsewhere, a release on a different link, or a press elsewhere opens nothing — and the click is not touched", () => {
  const { f, opened } = install();
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", plain);
  assert.deepEqual(f.fire("click", plain), [], "the card's click stands");
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", prAnchor(url(13)));
  assert.deepEqual(f.fire("click", plain), []);
  f.fire("pointerdown", plain); f.fire("pointerup", prAnchor(url(12)));
  assert.deepEqual(opened, []);
});

test("a click on a pr-link with no pointer press behind it (a keyboard activation) opens it through the click path", () => {
  const { f, opened } = install();
  assert.deepEqual(f.fire("click", prAnchor(url(12))), SPENT);
  assert.deepEqual(opened, [url(12)]);
});

test("the spent flag never eats an unrelated click: a new press or a key clears it when no click followed the open", () => {
  const { f, opened } = install();
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", prAnchor(url(12)));
  assert.deepEqual(opened, [url(12)]);
  // the browser fired no click (the pressed node was detached); the user presses a card next
  f.fire("pointerdown", plain); f.fire("pointerup", plain);
  assert.deepEqual(f.fire("click", plain), [], "the card's click lands");
  // …or activates something by keyboard
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", prAnchor(url(12)));
  f.fire("keydown", plain);
  assert.deepEqual(f.fire("click", plain), []);
  assert.deepEqual(opened, [url(12), url(12)]);
});

test("a cancelled pointer, a secondary button or a non-primary pointer never opens", () => {
  const { f, opened } = install();
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointercancel", prAnchor(url(12))); f.fire("pointerup", prAnchor(url(12)));
  f.fire("pointerdown", prAnchor(url(12)), { button: 2 }); f.fire("pointerup", prAnchor(url(12)), { button: 2 });
  f.fire("pointerdown", prAnchor(url(12)), { isPrimary: false }); f.fire("pointerup", prAnchor(url(12)), { isPrimary: false });
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", prAnchor(url(12)), { button: 1 });
  assert.deepEqual(opened, []);
});

test("in a VS Code webview the href goes to the host as openLink, from the release and from a keyboard click alike", () => {
  const { f, opened, posted } = install("vscode-webview:");
  f.fire("pointerdown", prAnchor(url(12))); f.fire("pointerup", prAnchor(url(12))); f.fire("click", plain);
  f.fire("click", prAnchor(url(13)));
  assert.deepEqual(posted, [{ type: "openLink", href: url(12) }, { type: "openLink", href: url(13) }]);
  assert.deepEqual(opened, []);
});

test("targets that are not a pr-link, or whose href is not a GitHub URL, are left alone at every phase", () => {
  const f = fakeDoc(); const opened: string[] = [];
  installPrLinkOpener(f.doc, undefined, { protocol: () => "https:", open: (h) => opened.push(h) });
  for (const t of [plain, prAnchor("javascript:alert(1)"), prAnchor("https://example.com/x"), null]) {
    f.fire("pointerdown", t); f.fire("pointerup", t);
    assert.deepEqual(f.fire("click", t), [], String(t && (t as any).closest?.("a.pr-link")?.getAttribute("href")));
  }
  assert.deepEqual(opened, []);
});

// ── wiring pins ──────────────────────────────────────────────────────────────────────────────────

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = read("render.ts");
const FEED = read("feed.ts");
const FED = read("federation.ts");
const OUTLINE = read("fleet.ts");

test("the chat links inside md(): the sanitized tree is walked before it serializes, against the owning session's repo", () => {
  assert.match(RENDER, /import \{ linkifyPrRefs, senderPrRepo, postalSenderHost \} from "\.\/pr-links";/);
  assert.match(RENDER, /function md\(src: string, repo: string \| null = prRepoFor\(\)\): string \{/);
  const mdFn = RENDER.match(/function md\(src: string[^\n]*?\): string \{[\s\S]*?\n\}/)?.[0] || "";
  // sanitizeMd (md-sanitize.ts) hands back the sanitized <body>; the walk runs on it before our own serialization
  assert.match(mdFn, /const clean = sanitizeMd\(dirty\);[^\n]*\n\s*linkifyPrRefs\(clean, repo\);\s*\n\s*mdImgPostPass\(clean\);[^\n]*\n\s*return clean\.innerHTML;/,
    "the sanitizer's own serialization is replaced by ours, after the walk: the sanitizer's verdicts stand");
  assert.match(RENDER, /const id = sid \?\? renderingOwnerSid \?\? renderingSid \?\? activeId;/, "the owning session, as relative paths resolve");
  assert.doesNotMatch(RENDER, /installPrLinkOpener/, "the chat's own a[href] delegate already opens every absolute-scheme anchor");
});

test("the session frame's githubRepo rides the Session and survives a chatTail delta; a null from the kernel is honored", () => {
  assert.match(RENDER, /interface Session \{[^\n]*githubRepo\?: string \| null;/);
  assert.match(RENDER, /githubRepo: \("githubRepo" in msg\) \? \(msg\.githubRepo \?\? null\) : \(prev \? prev\.githubRepo : null\),/);
});

test("postal bodies link against the SENDER's frame-known repo only: outbound = the writer's own, inbound = senderPrRepo over the session map by the card's host and name, never the reader's as a fallback", () => {
  // 2026-09-08 (the notice-vocabulary pass): ONE render path — the full message is the notice body (the summary is the head)
  assert.match(RENDER, /body\.innerHTML = md\(fullMd, postalRepoFor\(ev\)\);/);   // T294: the fold's markdown is postalHead's body (the message when the gist does not carry all of it)
  const fn = RENDER.match(/function postalRepoFor\([\s\S]*?\n\}/)?.[0] || "";
  assert.match(fn, /if \(ev\.direction === "out"\) return prRepoFor\(\);/);
  assert.match(fn, /const cardHost = hostOf\(renderingOwnerSid \?\? renderingSid \?\? activeId \?\? ""\);/,
    "the card's own kernel is the host of the session it sits in — the chain prRepoFor picks the session by");
  assert.match(fn, /return senderPrRepo\(Array\.from\(sessions\.values\(\), \(s\) => \(\{ sid: s\.id, name: s\.name, githubRepo: s\.githubRepo \}\)\), ev\.peer, postalSenderHost\(ev\.peerHost, localSelfHost, cardHost\)\);/,
    "the sender's host rides the card (peerHost), read relative to the card's kernel and against this kernel's own name");
  assert.equal((fn.match(/prRepoFor\(/g) || []).length, 1, "the reading session's repo is used for OUTBOUND mail only");
  // this dashboard's own name is learned from every LOCAL session frame, not only from the + picker's reply
  assert.match(RENDER, /if \(typeof msg\.selfHost === "string" && msg\.selfHost && !hostOf\(msg\.id\)\) adoptSelfHost\(msg\.selfHost\);/,
    "a remote kernel's frame names itself; only a frame with no host prefix is this kernel's");
  const upsertFn = RENDER.match(/function upsert\(msg: any\) \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(upsertFn, /adoptSelfHost\(msg\.selfHost\);/, "adopted in upsert, the session frame's one ingest point");
  assert.match(RENDER, /kind: "postal-service";\s*\n\s*direction: "in" \| "out";\s*\n\s*peer: string;\s*\n(?:\s*\/\/[^\n]*\n)*\s*peerHost\?: string;/, "the event type carries the optional host");
});

test("the chat learns its kernel's own name from every tabOrder frame too, through one adopter that re-renders built views when the name changes; federation carries the LOCAL kernel's name on the merged frame", () => {
  // tabOrder is the frame every chat receives, first of all on connect; a dashboard whose kernel runs no
  // local session has no session frame to learn the name from and learned it only when the + picker opened
  // (review find, 2026-09-06)
  assert.match(RENDER, /else if \(m\.type === "tabOrder"\) \{\s*\n\s*if \(typeof m\.selfHost === "string" && m\.selfHost\) adoptSelfHost\(m\.selfHost\);[^\n]*\n\s*captureViews\(m\.views \|\| null\);/,
    "adopted before the strip is applied");
  const adopt = RENDER.match(/function adoptSelfHost\(name: string\): void \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(adopt, /if \(name === localSelfHost\) return;/, "the same name again is no event");
  assert.match(adopt, /localSelfHost = name;\s*\n\s*if \(views\.size\) rerenderAll\(\);/,
    "a postal card rendered before the name was known is re-read against it; nothing built → nothing to redo");
  // the picker's reply goes through the same adopter, and the name is assigned in exactly one place
  assert.match(RENDER, /if \(typeof m\.selfHost === "string" && m\.selfHost && !from\) \{\s*\n\s*adoptSelfHost\(m\.selfHost\);/);
  assert.equal((RENDER.match(/\blocalSelfHost = /g) || []).length, 2, "the declaration and the adopter");
  // federation re-emits a MERGED tabOrder frame in place of every host's own: the local kernel's name must
  // ride it, and a remote kernel's — which names itself — must not (multi-kernel-merge.test.ts runs it)
  assert.match(FED, /if \(host === LOCAL && typeof m\.selfHost === "string" && m\.selfHost\) this\.localSelfHost = m\.selfHost;/);
  assert.match(FED, /\{ type: "tabOrder", order, tabs, live, views: this\.localViews \?\? undefined, selfHost: this\.localSelfHost \|\| undefined \}/);
});

test("the feed links card titles (keyed), distiller lines, held-mail gists (by sender), group cards, checklists and the modal — per the card's session", () => {
  assert.match(FEED, /import \{ linkifyPrRefs, setLinkedText, senderPrRepo, installPrLinkOpener \} from "\.\/pr-links";/);
  assert.match(FEED, /githubRepo\?: string \| null \}\[\] = \[\];/, "the session rows carry the repo");
  assert.match(FEED, /function prRepoOf\(sid: string \| undefined\): string \| null \{\s*\n\s*return \(sid && sessionsMeta\.find\(\(s\) => s\.sid === sid\)\?\.githubRepo\) \|\| null;/);
  // the two in-place titles are KEYED: an unchanged title keeps its anchor nodes across pushes
  assert.match(FEED, /setLinkedText\(a\._title, it\.text, prRepoOf\(it\.sid\)\);/);
  assert.match(FEED, /setLinkedText\(a\._title, g\.title, prRepoOf\(g\.sid\)\);/);
  assert.doesNotMatch(FEED, /a\._title\.textContent = /, "no per-push rewrite of a title");
  assert.match(FEED, /if \(distillShown\) linkifyPrRefs\(a\._distill as HTMLElement, prRepoOf\(it\.sid\)\);/);
  // the held message's gist links against its SENDER's repo (blocked.frm on blocked.origin), not the recipient card's
  assert.match(FEED, /linkifyPrRefsIn\(Object\.assign\(el\("div", "fq-gist"\)[^\n]*prRepoOfSender\(it\.blocked\.frm, it\.blocked\.origin\)\)\);/);
  const sender = FEED.match(/function prRepoOfSender\([\s\S]*?\n\}/)?.[0] || "";
  assert.match(sender, /const host = !origin \|\| origin === "\?" \? undefined : origin === feedSelfHost \? "" : origin;/);
  assert.match(sender, /return senderPrRepo\(sessionsMeta, frm \|\| "", host\);/);
  assert.match(FEED, /txt\.textContent = m\.text; linkifyPrRefs\(txt, prRepoOf\(m\.sid \|\| g\.sid\)\);/);
  assert.match(FEED, /el\("span", "fcheck-text"\); txt\.textContent = s\.text; linkifyPrRefs\(txt, prRepoOf\(it\.sid\)\);/);
  assert.match(FEED, /el\("span", "ftree-text"\); txt\.textContent = node\.text \|\| "\(node\)"; linkifyPrRefs\(txt, prRepoOf\(it\.sid\)\);/);
  assert.match(FEED, /bb\.textContent = modalBg; linkifyPrRefs\(bb, prRepoOf\(it\.sid\)\);/);
  assert.match(FEED, /sum\.textContent = nodeDistill;\s*\n\s*linkifyPrRefs\(sum, prRepoOf\(it\.sid\)\);/);
  assert.match(FEED, /installPrLinkOpener\(document, vscodeApi \? \(m\) => vscodeApi\.postMessage\(m\) : undefined\);/);
});

test("the outline links goal rows per session and opens them itself", () => {
  assert.match(OUTLINE, /import \{ linkifyPrRefs, installPrLinkOpener \} from "\.\/pr-links";/);
  assert.match(OUTLINE, /repoBySid = new Map\(m\.sessions\.filter/);
  assert.match(OUTLINE, /highlightInto\(txt, n\.text, curSearch\);[^\n]*\n\s*linkifyPrRefs\(txt, repoBySid\.get\(s\.sid\) \|\| null\);/);
  assert.match(OUTLINE, /installPrLinkOpener\(document, vscodeApi \? \(m\) => vscodeApi\.postMessage\(m\) : undefined\);/);
});

test("the pr-link anchor wears the hyperlink ink in both sheets", () => {
  for (const f of ["styles.css", "feed.css"]) {
    const css = read(f);
    assert.match(css, /\.pr-link \{ color: var\(--link\); text-decoration: none; \}/, f);
    assert.match(css, /\.pr-link:hover \{ text-decoration: underline; \}/, f);
  }
});
