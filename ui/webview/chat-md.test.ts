// The user's own words keep their line breaks in the chat (the user 2026-09-06): Shift+Enter in the
// composer inserts a newline, the text reaches the kernel and the transcript with it intact, and then the
// blue bubble rendered it through the shared `marked` singleton — `breaks: false`, right for assistant
// markdown where a lone newline is a soft wrap — so the lines ran together into one paragraph. The fix is
// a SECOND marked instance for user text (chat-md.ts `userMarked`, `breaks: true`) built from the same
// extensions the singleton gets, so only the newlines change. Executed here for real (marked + katex are
// plain JS); the render.ts wiring is source-pinned, since the webview has no jsdom harness.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { Marked } from "marked";
import { chatMdExtensions, userMdHtml } from "./chat-md";

// the singleton's configuration, rebuilt on a private instance: gfm, breaks OFF, the same extensions
const assistant = new Marked({ gfm: true, breaks: false }, ...chatMdExtensions);
const assistantHtml = (src: string) => assistant.parse(src) as string;

// --- executed: the user renderer keeps newlines, the assistant grammar does not ---

test("a single newline in user text becomes a hard line break; the assistant grammar keeps it a soft wrap", () => {
  const user = userMdHtml("line one\nline two");
  assert.match(user, /line one<br>\s*line two/, "the user's newline renders as <br>");
  const asst = assistantHtml("line one\nline two");
  assert.doesNotMatch(asst, /<br>/, "assistant markdown: a lone newline stays a soft wrap");
  assert.match(asst, /line one\nline two/);
});

test("a blank line is still a paragraph break, not a run of <br>", () => {
  const out = userMdHtml("para one\n\npara two");
  assert.equal((out.match(/<p>/g) || []).length, 2);
  assert.doesNotMatch(out, /<br>/);
});

test("a fenced code block keeps its literal newlines and gains no <br>", () => {
  const out = userMdHtml("```\nfirst\nsecond\n```");
  assert.match(out, /<pre><code>first\nsecond\n<\/code><\/pre>/);
  assert.doesNotMatch(out, /<br>/);
});

test("a list stays a list; an indented pasted snippet stays code", () => {
  const list = userMdHtml("- a\n- b");
  assert.equal((list.match(/<li>/g) || []).length, 2);
  assert.doesNotMatch(list, /<br>/);
  const snippet = userMdHtml("see:\n\n    x = 1\n    y = 2");
  assert.match(snippet, /<pre><code>x = 1\ny = 2\n<\/code><\/pre>/);
});

test("the chat grammar rides along: ~~double~~ strikes, a lone ~ stays literal", () => {
  assert.match(userMdHtml("this is ~~struck~~ out"), /<del>struck<\/del>/);
  const lone = userMdHtml("near the ~21 Wh budget, ~1.5 days");
  assert.doesNotMatch(lone, /<del>/);
  assert.match(lone, /~21/);
});

test("the chat grammar rides along: $x$ still becomes math, and a price stays a price", () => {
  // math.ts emits an inert placeholder carrying the TeX; KaTeX is rendered into it after the sanitizer, by
  // renderMathPlaceholders as a sanitizeMd post-pass this module registers at load (render.ts's userMd() calls
  // sanitizeMd and nothing of its own), so marked's own output holds the placeholder, never KaTeX's markup
  const out = userMdHtml("Euler: $e^{i\\pi}+1=0$\nnext line");
  assert.ok(out.includes('<span class="md-math-inline">e^{i\\pi}+1=0</span>'), "inline math becomes the placeholder: " + out);
  assert.match(out, /<br>\s*next line/, "…and the newline after it is still kept");
  const price = userMdHtml("costs $5 and $10 today");
  assert.ok(!price.includes("md-math"));
  assert.match(price, /\$5/);
});

test("the user renderer and the assistant grammar produce identical HTML when there is no newline", () => {
  for (const src of ["plain words", "**bold** and `code`", "a [link](https://example.test) here", "~~gone~~ $x$"]) {
    assert.equal(userMdHtml(src), assistantHtml(src), src);
  }
});

// --- source pins: the wiring in render.ts ---

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the singleton stays breaks:false — assistant rendering is unchanged", () => {
  assert.match(RENDER, /marked\.setOptions\(\{ gfm: true, breaks: false \}\);/);
  assert.match(RENDER, /marked\.use\(\.\.\.chatMdExtensions\);/, "…and takes the same grammar the user instance does");
});

test("userMd() renders through the breaks:true instance and the SAME sanitizer as md()", () => {
  // both renderers take the sanitized DOM back to link PR references before serializing (pr-links.ts); the
  // sanitizer is sanitizeMd (md-sanitize.ts), one call shared with the file viewer, which returns the sanitized
  // <body>; the profile is spelled there and nowhere in render.ts. Both signatures carry an optional repo
  // parameter for the PR links, so the match on them is loose.
  const fn = RENDER.match(/function userMd\(src: string[^\n]*?\): string \{[\s\S]*?\n\}/)?.[0] || "";
  assert.ok(fn, "userMd() must exist");
  assert.match(fn, /const clean = sanitizeMd\(userMdHtml\(src\)\);/);
  const mdFn = RENDER.match(/function md\(src: string[^\n]*?\): string \{[\s\S]*?\n\}/)?.[0] || "";
  assert.match(mdFn, /const clean = sanitizeMd\(dirty\);/, "md() sanitizes through the same shared call");
  assert.match(RENDER, /import \{[^}]*\bsanitizeMd\b[^}]*\} from "\.\/md-sanitize";/);
  assert.doesNotMatch(RENDER, /from "dompurify"|DOMPurify\.sanitize|MD_PURIFY/, "render.ts holds no sanitizer of its own");
  const SAN = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "md-sanitize.ts"), "utf8");
  assert.match(SAN, /USE_PROFILES: \{ html: true, svg: true \},\n\s*ADD_DATA_URI_TAGS: \["img"\],\n\s*ALLOW_DATA_ATTR: false,/);
  assert.doesNotMatch(fn + mdFn, /USE_PROFILES|ALLOW_DATA_ATTR/, "neither renderer spells its own profile: every option comes through md-sanitize.ts");
});

test("the user bubble renders the user's OWN words with userMd, harness notes and everything else with md", () => {
  // the landed bubble: kind "user" → userMd; the harness-injected note sharing the branch → md
  assert.match(RENDER, /bubble\.innerHTML = kind === "user" \? userMd\(ev\.md\) : md\(ev\.md\);\n\s*linkifyFileUris\(bubble, imgPaths, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);/);
  assert.doesNotMatch(RENDER, /bubble\.innerHTML = md\(ev\.md\);/, "no user bubble left on the soft-wrap grammar");
  // the queued / optimistic bubble is the same message a beat earlier — same renderer, so the swap to the
  // landed bubble changes nothing on screen
  assert.match(RENDER, /if \(!t\.romp && !isCmd\) bubble\.innerHTML = userMd\(t\.md\);/);
  assert.doesNotMatch(RENDER, /bubble\.innerHTML = md\(t\.md\);/);
  // romp-authored surfaces keep the assistant grammar: nudges, notices, the Continue gesture, tagged sends
  assert.match(RENDER, /full\.innerHTML = md\(raw\);/, "the romp nudge fold");
  assert.match(RENDER, /if \(more\) body\.innerHTML = md\(text\);[^\n]*\n\s*return notice\(\{ src: "romp", glyph: "romp", sev: "romp"/, "the romp system notice (a one-liner gets no body, T243; the one builder since 2026-09-08)");
});
