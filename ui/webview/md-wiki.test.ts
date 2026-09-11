// The two grammar additions for the lab team's glossary files (T351 stage 1): a [[wikilink]] renders as its plain
// text, and a callout blockquote keeps being a blockquote with the callout's kind as a small label. Executed with
// marked itself, the way chat-md.test.ts exercises the chat grammar; the sanitizer is the caller's, so the raw
// output is what is checked here.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { Marked } from "marked";
import { mdWikiExtensions, wikilinkText, calloutBlockquote } from "./md-wiki";
import { chatMdExtensions } from "./chat-md";

const m = new Marked({ gfm: true, breaks: false }, ...mdWikiExtensions);
const render = (src: string) => (m.parse(src) as string).trim();

test("[[Page Name]] renders as its text, [[Page|alias]] as the alias, never as a link", () => {
  assert.equal(render("see [[Fold Rules]] first"), "<p>see Fold Rules first</p>");
  assert.equal(render("see [[Fold Rules|the folds]] first"), "<p>see the folds first</p>");
  assert.doesNotMatch(render("[[Fold Rules]]"), /<a /, "a page in a vault the chat cannot open: words, not a dead link");
  assert.equal(render("a [[x]] and [[y|Y]] twice"), "<p>a x and Y twice</p>");
  assert.equal(render("`[[code]]` stays code"), "<p><code>[[code]]</code> stays code</p>", "inside a code span the brackets are code");
  assert.equal(render("one [ bracket [[ never closes"), "<p>one [ bracket [[ never closes</p>", "an unclosed pair is prose");
  assert.equal(render("[[<b>x</b>]]"), "<p>&lt;b&gt;x&lt;/b&gt;</p>", "the text is escaped");
});

test("a callout stays a blockquote, its marker becomes a small label naming the kind, the rest of the line stays", () => {
  const out = render("> [!NOTE] Read this first\n> and then the rest.");
  assert.match(out, /^<blockquote>\s*<div class="md-callout-label md-callout-note">Note<\/div>/, "the label leads the quote");
  assert.match(out, /<p>Read this first\nand then the rest\.<\/p>\s*<\/blockquote>$/, "the title and the body stay as the paragraph, marker gone");
  const bare = render("> [!WARNING]\n> Careful.");
  assert.match(bare, /md-callout-warning">Warning<\/div>\s*<p>Careful\.<\/p>/, "a marker alone on its line leaves only the body");
  const fold = render("> [!tip]- folded title\n> body");
  assert.match(fold, /md-callout-tip">Tip<\/div>\s*<p>folded title\nbody<\/p>/, "the fold mark is part of the marker");
  assert.equal(render("> plain quote"), "<blockquote>\n<p>plain quote</p>\n</blockquote>", "an ordinary quote is untouched");
  assert.equal(render("[!NOTE] not a quote"), "<p>[!NOTE] not a quote</p>", "the marker outside a blockquote is prose");
});

test("the chat grammar carries both, so the chat's messages and the viewer read a glossary file the same", () => {
  assert.ok(chatMdExtensions.includes(wikilinkText) && chatMdExtensions.includes(calloutBlockquote), "chat-md.ts spreads mdWikiExtensions");
  const chat = new Marked({ gfm: true, breaks: false }, ...chatMdExtensions);
  assert.equal((chat.parse("[[A|b]] ~~gone~~") as string).trim(), "<p>b <del>gone</del></p>", "beside the chat's own strikethrough rule");
});
