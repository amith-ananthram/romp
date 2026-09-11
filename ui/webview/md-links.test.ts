// The pure helpers behind "a markdown link opens in the file viewer" (the user 2026-09-06), EXECUTED:
// md-links.ts is string → string with no DOM, so these run the real functions rather than pinning
// them. Synthetic hosts and paths throughout (TESTHOST, the notes-api demo tree).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { isMarkdownUrl, resolveDocRelative, joinDocPath, urlTitleParts, headingSlug, uniqueSlugs } from "./md-links";

const ORIGIN = "https://TESTHOST";
const DOC = "https://TESTHOST/figs/run-1/evidence.md";

// ── isMarkdownUrl: the ONE interception condition — http(s), same origin, path ends .md/.markdown ──

test("isMarkdownUrl: a same-origin .md / .markdown URL, case-insensitive, query and fragment tolerated", () => {
  assert.equal(isMarkdownUrl(DOC, ORIGIN), true);
  assert.equal(isMarkdownUrl(DOC, "https://testhost"), true, "location.origin's normalised (lower-case) form");
  assert.equal(isMarkdownUrl(DOC, "https://TESTHOST:443"), true, "a hand-written origin normalises the same way");
  assert.equal(isMarkdownUrl("https://TESTHOST/figs/run-1/README.MD", ORIGIN), true, "case-insensitive extension");
  assert.equal(isMarkdownUrl("https://TESTHOST/notes/design.markdown", ORIGIN), true);
  assert.equal(isMarkdownUrl(DOC + "?v=3", ORIGIN), true, "a query string is allowed");
  assert.equal(isMarkdownUrl(DOC + "#results", ORIGIN), true, "a fragment is allowed");
  assert.equal(isMarkdownUrl(DOC + "?v=3#results", ORIGIN), true);
  assert.equal(isMarkdownUrl("http://TESTHOST:8765/x.md", "http://TESTHOST:8765"), true, "plain http with a port");
  assert.equal(isMarkdownUrl("https://TESTHOST:443/x.md", ORIGIN), true, "the default port normalises away");
  assert.equal(isMarkdownUrl("https://TESTHOST/a%20b/my%20doc.md", ORIGIN), true, "percent-encoded paths");
});

test("isMarkdownUrl: cross-origin is false — another host, scheme or port keeps the new tab", () => {
  assert.equal(isMarkdownUrl("https://OTHERHOST/figs/run-1/evidence.md", ORIGIN), false, "another host");
  assert.equal(isMarkdownUrl("http://TESTHOST/x.md", ORIGIN), false, "http vs https is a different origin");
  assert.equal(isMarkdownUrl("https://TESTHOST:8443/x.md", ORIGIN), false, "another port");
  assert.equal(isMarkdownUrl("https://github.com/notes-api/notes-api/blob/main/README.md", ORIGIN), false,
    "a GitHub blob page merely ends in .md");
});

test("isMarkdownUrl: not a markdown path, not http(s), not absolute, or not a URL at all → false, never a throw", () => {
  assert.equal(isMarkdownUrl("https://TESTHOST/figs/run-1/evidence.md.png", ORIGIN), false, ".md.png is an image");
  assert.equal(isMarkdownUrl("https://TESTHOST/figs/run-1/evidence.md/", ORIGIN), false, "a directory named like a file");
  assert.equal(isMarkdownUrl("https://TESTHOST/figs/run-1/evidence.md.bak", ORIGIN), false);
  assert.equal(isMarkdownUrl("https://TESTHOST/figs/run-1/plot.png", ORIGIN), false);
  assert.equal(isMarkdownUrl("https://TESTHOST/figs/run-1/", ORIGIN), false);
  assert.equal(isMarkdownUrl("mailto:someone@TESTHOST", ORIGIN), false);
  assert.equal(isMarkdownUrl("vscode://romp.romp-chat-view/open?path=x.md", ORIGIN), false);
  assert.equal(isMarkdownUrl("file:///srv/notes-api/docs/README.md", ORIGIN), false);
  assert.equal(isMarkdownUrl("javascript:alert(1)//x.md", ORIGIN), false);
  assert.equal(isMarkdownUrl("figs/run-1/evidence.md", ORIGIN), false, "relative — not an absolute URL");
  assert.equal(isMarkdownUrl("/figs/run-1/evidence.md", ORIGIN), false, "root-relative — same");
  assert.equal(isMarkdownUrl("//TESTHOST/x.md", ORIGIN), false, "protocol-relative has no scheme to parse");
  assert.equal(isMarkdownUrl("", ORIGIN), false);
  assert.equal(isMarkdownUrl("not a url at all.md", ORIGIN), false);
  assert.equal(isMarkdownUrl("https://", ORIGIN), false);
  assert.equal(isMarkdownUrl(DOC, ""), false, "no page origin → nothing can match");
  assert.equal(isMarkdownUrl(DOC, "null"), false, "an opaque origin (a sandboxed webview) never matches");
  assert.equal(isMarkdownUrl(undefined as unknown as string, ORIGIN), false, "non-string input");
  assert.equal(isMarkdownUrl(DOC, null as unknown as string), false);
});

// ── resolveDocRelative: a reference INSIDE a URL document resolves against the document ──

// (the URL parser lower-cases hosts — the resolved forms below read `testhost`)
test("resolveDocRelative: relative, ./, ../, root-relative and nested refs resolve against the document URL", () => {
  assert.equal(resolveDocRelative("fig.png", DOC), "https://testhost/figs/run-1/fig.png");
  assert.equal(resolveDocRelative("./fig.png", DOC), "https://testhost/figs/run-1/fig.png");
  assert.equal(resolveDocRelative("../fig.png", DOC), "https://testhost/figs/fig.png");
  assert.equal(resolveDocRelative("../../fig.png", DOC), "https://testhost/fig.png");
  assert.equal(resolveDocRelative("plots/a/b.svg", DOC), "https://testhost/figs/run-1/plots/a/b.svg");
  assert.equal(resolveDocRelative("/shared/logo.png", DOC), "https://testhost/shared/logo.png", "root-relative → the base origin");
  assert.equal(resolveDocRelative("other.md", DOC), "https://testhost/figs/run-1/other.md", "a sibling document, absolute now");
  assert.equal(resolveDocRelative("other.md#sec", DOC), "https://testhost/figs/run-1/other.md#sec", "its fragment rides along");
  assert.equal(resolveDocRelative("?v=2", DOC), "https://testhost/figs/run-1/evidence.md?v=2");
  assert.equal(resolveDocRelative("fig.png", DOC + "?v=3#top"), "https://testhost/figs/run-1/fig.png",
    "the base's own query/fragment do not leak into a sibling");
  assert.equal(resolveDocRelative("my fig.png", DOC), "https://testhost/figs/run-1/my%20fig.png", "a space is encoded");
  assert.equal(resolveDocRelative("my%20fig.png", DOC), "https://testhost/figs/run-1/my%20fig.png", "already-encoded stays");
  assert.equal(resolveDocRelative("//TESTHOST/x.png", DOC), "https://testhost/x.png", "protocol-relative takes the base scheme");
  // the resolved sibling is exactly what the anchor delegate then intercepts
  assert.equal(isMarkdownUrl(resolveDocRelative("other.md", DOC), ORIGIN), true);
  assert.equal(isMarkdownUrl(resolveDocRelative("fig.png", DOC), ORIGIN), false);
});

test("resolveDocRelative: refs with a scheme, bare fragments and empty refs come back untouched", () => {
  for (const ref of [
    "https://OTHERHOST/fig.png", "http://TESTHOST/fig.png", "data:image/png;base64,AAAA",
    "blob:https://TESTHOST/11111111-2222-3333-4444-555555555555", "mailto:someone@TESTHOST",
    "javascript:alert(1)", "vscode://romp.romp-chat-view/x", "#results", "#", "",
  ]) assert.equal(resolveDocRelative(ref, DOC), ref, ref + " is left alone");
});

test("resolveDocRelative: a base that is not a URL leaves the ref alone rather than throwing", () => {
  assert.equal(resolveDocRelative("fig.png", "/srv/notes-api/docs/README.md"), "fig.png", "a disk path is not a URL base");
  assert.equal(resolveDocRelative("fig.png", ""), "fig.png");
  assert.equal(resolveDocRelative("fig.png", "not a url"), "fig.png");
  assert.equal(resolveDocRelative("fig.png", undefined as unknown as string), "fig.png");
  assert.equal(resolveDocRelative(undefined as unknown as string, DOC), undefined, "a non-string ref is echoed");
});

// ── joinDocPath: a reference INSIDE a LOCAL document joins onto the document's directory ──

const LOCAL = "/srv/notes-api/docs/README.md";

test("joinDocPath: relative, ./ and ../ refs join onto the document's directory", () => {
  assert.equal(joinDocPath(LOCAL, "fig.png"), "/srv/notes-api/docs/fig.png");
  assert.equal(joinDocPath(LOCAL, "./fig.png"), "/srv/notes-api/docs/fig.png");
  assert.equal(joinDocPath(LOCAL, "../CHANGELOG.md"), "/srv/notes-api/CHANGELOG.md");
  assert.equal(joinDocPath(LOCAL, "../../other/x.md"), "/srv/other/x.md");
  assert.equal(joinDocPath(LOCAL, "plots/../a/b.png"), "/srv/notes-api/docs/a/b.png", "an interior .. collapses");
  assert.equal(joinDocPath(LOCAL, "a//b.png"), "/srv/notes-api/docs/a/b.png", "a doubled slash collapses");
  assert.equal(joinDocPath(LOCAL, "sub/"), "/srv/notes-api/docs/sub", "a trailing slash drops");
  assert.equal(joinDocPath("/a/b.md", "../../../x.png"), "/x.png", "climbing past the root stays at the root");
});

test("joinDocPath: absolute and ~-anchored refs, and refs with a scheme, are not joined", () => {
  assert.equal(joinDocPath(LOCAL, "/srv/shared/logo.png"), "/srv/shared/logo.png");
  assert.equal(joinDocPath(LOCAL, "~/notes/x.md"), "~/notes/x.md");
  assert.equal(joinDocPath(LOCAL, "~/a/../x.md"), "~/a/../x.md", "left alone as written — the kernel expands ~ and the filesystem resolves ..");
  assert.equal(joinDocPath(LOCAL, "/srv/a/../x.md"), "/srv/a/../x.md", "same for an absolute path");
  assert.equal(joinDocPath(LOCAL, "https://TESTHOST/x.md"), "https://TESTHOST/x.md");
  assert.equal(joinDocPath(LOCAL, "data:image/png;base64,AAAA"), "data:image/png;base64,AAAA");
  assert.equal(joinDocPath(LOCAL, "mailto:someone@TESTHOST"), "mailto:someone@TESTHOST");
});

test("joinDocPath: the link is a URL by convention but the kernel wants a path — fragment off, percent-decoded", () => {
  assert.equal(joinDocPath(LOCAL, "other.md#install"), "/srv/notes-api/docs/other.md", "the path is what the kernel wants; the fragment rides separately");
  assert.equal(joinDocPath(LOCAL, "my%20notes.md"), "/srv/notes-api/docs/my notes.md");
  assert.equal(joinDocPath(LOCAL, "%ZZ.md"), "/srv/notes-api/docs/%ZZ.md", "a stray % keeps the bytes as written, never throws");
  assert.equal(joinDocPath(LOCAL, "#install"), "#install", "a bare fragment has no path to join (callers skip it anyway)");
  assert.equal(joinDocPath(LOCAL, ""), "", "empty in, empty out");
  assert.equal(joinDocPath(LOCAL, undefined as unknown as string), undefined);
});

test("joinDocPath: a RELATIVE document (the kernel resolves it against the session's cwd) keeps its relativity", () => {
  assert.equal(joinDocPath("docs/README.md", "fig.png"), "docs/fig.png");
  assert.equal(joinDocPath("docs/README.md", "../x.md"), "x.md");
  assert.equal(joinDocPath("docs/README.md", "../../x.md"), "../x.md", "above the start: keeps climbing, for the kernel to resolve");
  assert.equal(joinDocPath("README.md", "fig.png"), "fig.png", "a bare filename has no directory");
  assert.equal(joinDocPath("README.md", "../fig.png"), "../fig.png");
  assert.equal(joinDocPath("README.md", "."), ".", "the document's own directory");
});

// ── urlTitleParts: the title bar's two halves for a URL document ──

test("urlTitleParts: host/dir/ then the basename, decoded for reading, port kept, query and fragment dropped", () => {
  assert.deepEqual(urlTitleParts("https://TESTHOST/figs/run-1/evidence.md?v=3#results"),
    { dir: "testhost/figs/run-1/", base: "evidence.md" });
  assert.deepEqual(urlTitleParts("https://TESTHOST:8443/x.md"), { dir: "testhost:8443/", base: "x.md" });
  assert.deepEqual(urlTitleParts("https://TESTHOST/x.md"), { dir: "testhost/", base: "x.md" }, "a root file: host and slash");
  assert.deepEqual(urlTitleParts("https://TESTHOST/a%20b/my%20doc.md"), { dir: "testhost/a b/", base: "my doc.md" });
  assert.deepEqual(urlTitleParts("not a url"), { dir: "", base: "not a url" }, "a non-URL still draws a bar");
});

// ── headingSlug / uniqueSlugs: the ids a rendered document's own `#fragment` links land on ──

test("headingSlug: GitHub's shape — lower-case, letters/digits/spaces/hyphens kept, the rest dropped, spaces → hyphens", () => {
  assert.equal(headingSlug("Evidence"), "evidence");
  assert.equal(headingSlug("Hello World"), "hello-world");
  assert.equal(headingSlug("Hello  World"), "hello-world", "a whitespace run is ONE hyphen");
  assert.equal(headingSlug("  Hello World  "), "hello-world", "outer whitespace trimmed");
  assert.equal(headingSlug("Results: arm A vs. arm B!"), "results-arm-a-vs-arm-b", "punctuation dropped");
  assert.equal(headingSlug("Run 1 — pooled estimate"), "run-1-pooled-estimate", "an em dash is not a hyphen; it drops");
  assert.equal(headingSlug("keep-the-hyphens"), "keep-the-hyphens");
  assert.equal(headingSlug("Ünïcödé Ωmega 日本語"), "ünïcödé-ωmega-日本語", "letters of any script are kept");
  assert.equal(headingSlug("v2.0 (final)"), "v20-final");
  assert.equal(headingSlug("`code` & <b>tags</b>"), "code-btagsb", "only the text — angle brackets and ampersands go");
});

test("headingSlug: idempotent (a slug slugs to itself), and empty → the stable fallback 'section'", () => {
  for (const s of ["evidence", "hello-world", "results-arm-a-vs-arm-b", "run-1-pooled-estimate"]) assert.equal(headingSlug(s), s);
  assert.equal(headingSlug(""), "section");
  assert.equal(headingSlug("   "), "section");
  assert.equal(headingSlug("!!! ??? ..."), "section", "nothing survives → the fallback, never an empty id");
  assert.equal(headingSlug(undefined as unknown as string), "section");
});

test("uniqueSlugs: duplicates are numbered -1, -2… in document order, skipping a suffix a heading already owns", () => {
  assert.deepEqual(uniqueSlugs(["x", "x", "x"]), ["x", "x-1", "x-2"]);
  assert.deepEqual(uniqueSlugs(["a", "b", "a", "c", "b"]), ["a", "b", "a-1", "c", "b-1"]);
  assert.deepEqual(uniqueSlugs(["x", "x-1", "x"]), ["x", "x-1", "x-2"], "the literal heading 'x-1' keeps its slug; the duplicate moves on");
  assert.deepEqual(uniqueSlugs(["section", "section"]), ["section", "section-1"], "two empty headings still get distinct ids");
  assert.deepEqual(uniqueSlugs([]), []);
});

test("the round trip a fragment link takes: heading text → id, fragment → the same id", () => {
  const ids = uniqueSlugs(["Evidence", "Results", "Results"].map(headingSlug));
  assert.deepEqual(ids, ["evidence", "results", "results-1"]);
  // the author wrote [top](#Evidence), [r](#results), [r2](#Results%20) — decoded, slugged, found
  assert.equal(headingSlug(decodeURIComponent("Evidence")), "evidence");
  assert.equal(headingSlug(decodeURIComponent("Results%20")), "results");
  assert.equal(headingSlug("results-1"), "results-1", "the numbered id is reachable as written");
});

test("uniqueSlugs is amortised linear: 20,000 identical headings dedupe in well under a second, all distinct, last = setup-19999", () => {
  // the regression: a per-duplicate restart at suffix 1 was quadratic — 12,000 repeated `## Setup`
  // headings in an 84 KB document (well under the cap) took 11 s to render and froze the page
  const slugs = new Array(20_000).fill("setup");
  const t0 = process.hrtime.bigint();
  const out = uniqueSlugs(slugs);
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.ok(ms < 500, "took " + ms.toFixed(1) + " ms");
  assert.equal(out.length, 20_000);
  assert.equal(new Set(out).size, 20_000, "all distinct");
  assert.equal(out[0], "setup");
  assert.equal(out[1], "setup-1");
  assert.equal(out[19_999], "setup-19999");
  // explicit numbered headings interleaved with duplicates still never collide, and the counter never restarts
  const mixed = uniqueSlugs(["setup", "setup-2", "setup", "setup", "setup", "setup-5", "setup"]);
  assert.deepEqual(mixed, ["setup", "setup-2", "setup-1", "setup-3", "setup-4", "setup-5", "setup-6"]);
  assert.equal(new Set(mixed).size, mixed.length);
});

// T351: the slug rule is shared with the kernel (kernel.py _heading_slug / _unique_slugs), which slices a `path#slug`
// link's section for the chat's file preview; the fixture pins both sides so the section the kernel slices is the
// heading the viewer lands on (tests/test_file_slice.py runs the same fixture through the Python port).
import * as fs from "node:fs";
import * as path from "node:path";
test("headingSlug and uniqueSlugs agree with the kernel's port over the shared fixture", () => {
  const fx = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "tests", "fixtures", "heading_slugs.json"), "utf8"));
  for (const [text, slug] of fx.slugs as [string, string][]) assert.equal(headingSlug(text), slug, JSON.stringify(text));
  for (const [inp, out] of fx.unique as [string[], string[]][]) assert.deepEqual(uniqueSlugs(inp), out, JSON.stringify(inp));
});
