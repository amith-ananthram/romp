// The glossary's client half, executed (T351 stage 2): the forms an entry links under, the matcher's rules (longest
// first, whole-word, case-insensitive, the skip list, the link modes), the term card's contract, over the same
// synthetic fixture the kernel's parser test reads; the wiring pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { pluralForms, linkForms, buildMatcher, scanTerms, termContent, TERM_SKIP_SELECTOR, type GlossaryIndex, type GlossaryEntry } from "./glossary-links";

const FIX = JSON.parse(fs.readFileSync(path.resolve(process.cwd(), "..", "tests", "fixtures", "glossary_grammar.json"), "utf8"));
const IX: GlossaryIndex = { group: "notes-api", path: "~/.claude/glossaries/notes-api.md", skip: FIX.expect.skip, terms: FIX.expect.terms };
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("plurals by the everyday rule; an entry's forms are the term, its aliases and their plurals, minus the skip list; off links nothing", () => {
  assert.deepEqual(pluralForms("fold"), ["folds"]); assert.deepEqual(pluralForms("lens"), ["lenses"]); assert.deepEqual(pluralForms("staircase"), ["staircases"]);
  assert.deepEqual(pluralForms("fold head"), ["fold heads"]); assert.deepEqual(pluralForms("query"), ["queries"]); assert.deepEqual(pluralForms("day"), ["days"]);
  const skip = new Set(IX.skip);
  const fold = IX.terms[0] as GlossaryEntry;
  assert.deepEqual(linkForms(fold, skip).sort(), ["fold", "fold head", "fold heads", "folded", "foldeds", "folds", "review fold", "review folds"].sort());
  assert.deepEqual(linkForms(IX.terms[2] as GlossaryEntry, skip), [], "link: off");
  assert.deepEqual(linkForms({ ...fold, term: "green", also: ["CI"] }, skip).sort(), ["greens", "cis"].sort(), "the skip list removes the exact words (their guessed plurals are not skip words)");
});

test("the matcher: longest form first, whole word, case-insensitive, Unicode-bounded; scanTerms honours all / first / off", () => {
  const m = buildMatcher(IX)!;
  assert.ok(m);
  const text = "I folded the fixes and pushed the fold head; the Fold is green, the lens and the lens again, pins pinned, unfolded, fold-ish, T351.";
  const seen = new Set<GlossaryEntry>();
  const spans = scanTerms(text, m, seen).map((s) => [text.slice(s.start, s.end), s.entry.term]);
  assert.deepEqual(spans, [["folded", "fold"], ["fold head", "fold head"], ["Fold", "fold"], ["lens", "lens"]],
    "fold head is one term (longest first), Fold matches case-insensitively, lens once (first), pins never (off), green never (skip), unfolded and fold-ish are not whole words");
  assert.deepEqual(scanTerms("the lens again", m, seen), [], "first: the message's set carries across its text nodes");
  assert.deepEqual(scanTerms("the lens again", m, new Set()).length, 1, "a new message starts over");
  assert.deepEqual(scanTerms("fold\u00e9 folds", m, new Set()).map((s) => text && s.start), [6], "a letter after the form is not a boundary; the plural is a form");
  assert.equal(buildMatcher({ ...IX, terms: [IX.terms[2]] }), null, "an index that links nothing has no matcher");
});

test("the term card fills the popover's contract from the index, no fetch; a retired term says so first; the cut is a note", () => {
  const fold = IX.terms[0] as GlossaryEntry;
  const c = termContent(fold, IX);
  assert.equal(c.kind, "term"); assert.equal(c.title, "fold");
  assert.equal(c.subtitle, "unconfirmed · registered 2026-09-11 by web · notes-api");
  assert.match(c.body.markdown!, /^The fixes from a review/); assert.match(c.body.markdown!, /\*plain words:\* the fixes from a review/); assert.match(c.body.markdown!, /\*scope:\* notes-api team mail/);
  assert.deepEqual(c.open, { label: "Open glossary", path: IX.path, frag: "fold" });
  const pin = termContent(IX.terms[2] as GlossaryEntry, IX);
  assert.match(pin.body.markdown!, /^\*Retired: say the plain phrase\.\*/);
  assert.equal(termContent(fold, { ...IX, truncated: 3 }).note, "3 entries beyond the index's byte cap are not linked");
  assert.match(TERM_SKIP_SELECTOR, /code, pre, a, \.file-uri-link, h1, h2, h3, h4, h5, h6, \.katex, svg, \.term-link, \.cmt-pop, \.file-preview-pop/);
});

test("the wiring: the frame per session, the matcher per index, links at the two chat grammars and the mail body, the card on the popover, the click to the viewer", () => {
  assert.match(RENDER, /else if \(m\.type === "glossary" && typeof m\.id === "string"\) \{[\s\S]{0,300}?glossaries\.set\(m\.id, m as GlossaryIndex\);\s*\n\s*relinkTerms\(m\.id\);/);
  assert.equal((RENDER.match(/\blinkTerms\((full|bubble|body)\)/g) || []).length, 4, "the nudge's full text, the user bubble, the assistant body, the mail body");
  assert.match(RENDER, /linkifyFileUris\(body, undefined, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);[^\n]*\n\s*linkTerms\(body\);/, "after the path links, so a path token is never split by a term");
  assert.match(RENDER, /s\.dataset\.path = m\.index\.path; s\.dataset\.frag = e\.slug;[\s\S]{0,200}?armFilePreview\(s\);/, "a term span is a path link's twin: the same hover road");
  assert.match(RENDER, /if \(a\.dataset\.term\) \{[\s\S]{0,600}?renderFilePreview\(p, termContent\(e, ix\), a\.dataset\.gsid \|\| activeId\);/, "the card from the index, no fetch");
  assert.match(RENDER, /closest\?\.\("span\.term-link"\)[\s\S]{0,300}?openPath\(s\.dataset\.path \|\| "", s\.dataset\.gsid \|\| activeId, e, s\.dataset\.frag \|\| null\);/, "a click opens the glossary at the heading");
  assert.match(RENDER, /function relinkTerms\(sid: string\): void \{[\s\S]{0,700}?querySelectorAll\("span\.term-link"\)/, "a new index unwraps and re-links the view");
  assert.match(CSS, /\.term-link \{ text-decoration: underline dotted;/); assert.match(CSS, /\.term-link\.term-retired \{ opacity: 0\.6; \}/);
  // the kernel: the frame on the pusher's cycle beside the comments frame, on its own slot; the route; the byte cap and its /perf note
  assert.match(KERNEL, /gfr = _glossary_frame\(s\["sid"\]\)[\s\S]{0,400}?_send_client\(c, \("glossary", s\["sid"\]\), gfr\)/);
  assert.match(KERNEL, /if p\.startswith\("\/glossary\/"\):[\s\S]{0,300}?_glossary_lookup\(\(q\.get\("sid"\) or \[None\]\)\[0\], unquote\(p\[len\("\/glossary\/"\):\]\)\)/, "the route sits in the GET router beside the file route");
  assert.match(KERNEL, /_GLOSSARY_INDEX_MAX_BYTES = 256 \* 1024/); assert.match(KERNEL, /"glossary": glossary_stats,/);
  assert.match(KERNEL, /a bullet\s*\n\s*describing a PATTERN \(a T followed by a number, say\) is just words the whole-word matcher will never meet/, "the skip list is words, no special case for a pattern (said in the parser's docstring)");
});
