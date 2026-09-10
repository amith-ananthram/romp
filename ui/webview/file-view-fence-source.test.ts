// A fence's text as the file holds it, for the viewer's Copy button (fence-source.ts). marked's lexer turns every leading run
// of tabs into four spaces a tab before it tokenizes, so a code token's text, and the code element's textContent mdBlock
// copies from, carry spaces where the file has tabs: a Makefile recipe copied out of the rendered view pasted back with
// spaces. The module reads each token's lines back out of the file through the rewrite. Exercised over the REAL marked: the
// tokens come from marked.parse's walkTokens, the way mdBlock collects them, so the cases pin marked's own transformations
// (the expansion, a list item's indent slice, a quote's stripped `> `, an indented block's four columns, CRLF) beside the
// module's reading of them. Self-contained: marked and the module only. Synthetic documents throughout.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { marked } from "marked";
import { fenceSources, fenceCopyQueue, markedView, renderedFenceText, type Fence } from "./fence-source";

const F = "```";
/** The code tokens of `src` in document order, as mdBlock's walkTokens collects them. */
const tokens = (src: string): Fence[] => {
  const out: Fence[] = [];
  marked.parse(src, { walkTokens: (t) => { if (t.type === "code") { const c = t as { text: string; codeBlockStyle?: string }; out.push({ text: c.text, indented: c.codeBlockStyle === "indented" }); } } });
  return out;
};
/** marked's own preprocessing, as its Lexer spells it: line endings to LF, then a line's leading tabs to four spaces each. */
const markedOwn = (src: string): string => src.replace(/\r\n|\r/g, "\n").replace(/^( *)(\t+)/gm, (_, l: string, t: string) => l + "    ".repeat(t.length));

test("markedView is marked's own preprocessing, character for character, and maps every character back to its source index", () => {
  for (const src of ["plain\n", "\tone\n\t\ttwo\n", "  \tmixed\n", "a\tb\n", " \t \tafter a space\n", "crlf\r\n\tx\r\n", "lone\rcr\t\n", "", "\t", "x\n\n\t\n"]) {
    const v = markedView(src);
    assert.equal(v.text, markedOwn(src), JSON.stringify(src));
    assert.equal(v.at.length, v.text.length, "one source index per view character");
    for (let i = 0; i < v.text.length; i++) {
      const c = src[v.at[i]];
      assert.ok(v.text[i] === c || (v.text[i] === " " && c === "\t") || (v.text[i] === "\n" && c === "\r"), `${JSON.stringify(src)} at ${i}: ${JSON.stringify(v.text[i])} from ${JSON.stringify(c)}`);
    }
  }
});

test("a top-level fence: leading tabs come back as tabs, one and two deep; a mid-line tab was never touched; marked's own text has the spaces", () => {
  const src = `# Build\n\n${F}makefile\nall:\n\tgo build ./...\n\t\techo done\na\tb\tc\n${F}\n\nTail.\n`;
  const t = tokens(src);
  assert.equal(t.length, 1);
  assert.equal(t[0].text, "all:\n    go build ./...\n        echo done\na\tb\tc", "marked's text: four spaces a leading tab, the mid-line tabs kept");
  assert.deepEqual(fenceSources(src, t), ["all:\n\tgo build ./...\n\t\techo done\na\tb\tc\n"]);
});

test("a fence inside a list item: a tab the item's indent left whole is a tab; one the indent consumed part of is the spaces that remain (CommonMark's partial tab)", () => {
  // two-space indent, then a tab: marked slices the item's two columns off the six expanded spaces, the tab stands whole
  const whole = `- item one\n\n  ${F}python\n  \treturn 1\n  ${F}\n\n- item two\n`;
  assert.deepEqual(fenceSources(whole, tokens(whole)), ["\treturn 1\n"]);
  // a tab alone as the item's indent: the item's two columns eat half of it, and two spaces remain
  const partial = `- ${F}python\n\treturn 1\n\t${F}\n`;
  const t = tokens(partial);
  assert.equal(t[0].text, "  return 1", "marked: two of the tab's four spaces remain after the slice");
  assert.deepEqual(fenceSources(partial, t), ["  return 1\n"]);
  // an ordered item's three columns
  const ordered = `1. ${F}\n   \tx\n   ${F}\n`;
  assert.deepEqual(fenceSources(ordered, tokens(ordered)), ["\tx\n"]);
});

test("a fence inside a quote: a tab right after the `> ` was not leading to the lexer, the quote strips the `> `, the inner tokenization expands it; the source's tab is copied", () => {
  const src = `> Quoted.\n>\n> ${F}\n> \tfoo\n> ${F}\n`;
  const t = tokens(src);
  assert.equal(t[0].text, "    foo");
  assert.deepEqual(fenceSources(src, t), ["\tfoo\n"]);
  // nested: a fence opening on a list item's first line inside a quote
  const nested = `> - ${F}sh\n>   \techo hi\n>   ${F}\n`;
  assert.deepEqual(fenceSources(nested, tokens(nested)), ["\techo hi\n"]);
});

test("CRLF endings: the lines come back joined with LF, as the rendered text is, with the tabs restored", () => {
  const src = `${F}\r\nx\r\n\ty\r\n${F}\r\n`;
  assert.deepEqual(fenceSources(src, tokens(src)), ["x\n\ty\n"]);
});

test("an indented code block: the four columns the block's indent takes are gone (a whole tab, or four spaces), a second tab is a tab", () => {
  const src = `para\n\n\t\tfoo\n\tbar\n    baz\n\npara\n`;
  const t = tokens(src);
  assert.equal(t.length, 1); assert.equal(t[0].indented, true);
  assert.equal(t[0].text, "    foo\nbar\nbaz");
  assert.deepEqual(fenceSources(src, t), ["\tfoo\nbar\nbaz\n"]);
});

test("the renderer's shape: one trailing newline dropped and one appended, so the copy matches the rendered text but for the tabs; an empty fence is one newline", () => {
  assert.equal(renderedFenceText("foo\n"), "foo\n");
  assert.equal(renderedFenceText("foo"), "foo\n");
  assert.equal(renderedFenceText(""), "\n");
  const blank = `${F}\nfoo\n\n${F}\n`;
  assert.deepEqual(fenceSources(blank, tokens(blank)), ["foo\n"], "a blank last line is the renderer's dropped newline");
  const empty = `${F}\n${F}\n`;
  assert.deepEqual(fenceSources(empty, tokens(empty)), ["\n"]);
  // a fence whose leading whitespace is a space then a tab: the tab was leading (right after the spaces), and is whole
  const spaceTab = `${F}\n \tfoo\n${F}\n`;
  assert.equal(tokens(spaceTab)[0].text, "     foo");
  assert.deepEqual(fenceSources(spaceTab, tokens(spaceTab)), [" \tfoo\n"]);
});

test("fences are matched in document order: two fences rendering the same four spaces copy a tab and four spaces respectively; a token not in the file is null", () => {
  const src = `${F}\n\tx\n${F}\n\n${F}\n    x\n${F}\n`;
  const t = tokens(src);
  assert.equal(t[0].text, t[1].text, "the same rendered text");
  assert.deepEqual(fenceSources(src, t), ["\tx\n", "    x\n"]);
  assert.deepEqual(fenceSources(src, [{ text: "not in the file", indented: false }]), [null]);
  // a token whose lines exist but never after a fence opener is not taken for a fence
  assert.deepEqual(fenceSources("plain x\n", [{ text: "plain x", indented: false }]), [null]);
});

test("fenceCopyQueue: keyed by the rendered text, in document order, null for a fence not found; empty for a file without a tab or a CR (marked's view is the file)", () => {
  const src = `${F}\n\tx\n${F}\n\n${F}\n    x\n${F}\n\n${F}\nplain\n${F}\n`;
  const q = fenceCopyQueue(src, tokens(src));
  assert.deepEqual(Array.from(q.keys()), ["    x\n", "plain\n"]);
  assert.deepEqual(q.get("    x\n"), ["\tx\n", "    x\n"]);
  assert.deepEqual(q.get("plain\n"), ["plain\n"]);
  const clean = `${F}\n    x\n${F}\n`;
  assert.equal(fenceCopyQueue(clean, tokens(clean)).size, 0, "no tab, no CR: nothing to read back");
  assert.equal(fenceCopyQueue(src, []).size, 0, "no fences: nothing to queue");
});

test("an empty fence is matched as its opener and closer, so the fence after it is found: marked's text is empty for no line and for one blank line alike", () => {
  // the closer directly followed by a fence whose first line is blank and whose second holds a tab: read as one blank
  // line, the empty fence would match the py fence's blank first line, and the py fence, behind the cursor, would be null
  const adjacent = `${F}\n${F}\n${F}py\n\n\tx = 1\n${F}\n`;
  const t = tokens(adjacent);
  assert.equal(t[0].text, "", "marked: a fence of no lines has the empty text");
  assert.deepEqual(fenceSources(adjacent, t), ["\n", "\n\tx = 1\n"]);
  assert.deepEqual(fenceCopyQueue(adjacent, t).get("\n    x = 1\n"), ["\n\tx = 1\n"], "Copy on the py fence pastes the tab");
  // the closer directly followed by prose, the next fence in the usual blank-line style
  const prose = `${F}\n${F}\nText.\n\n${F}py\n\tx = 1\n${F}\n\nMore.\n`;
  assert.deepEqual(fenceSources(prose, tokens(prose)), ["\n", "\tx = 1\n"]);
  // one blank line as the fence's content: the same empty text, the blank line is the fence's own
  const blankLine = `${F}\n\n${F}\n${F}py\n\n\tx = 1\n${F}\n`;
  assert.equal(tokens(blankLine)[0].text, "", "marked: a fence of one blank line has the empty text too");
  assert.deepEqual(fenceSources(blankLine, tokens(blankLine)), ["\n", "\n\tx = 1\n"]);
  // without a trailing newline the lone empty fence is found by its closer, not by a blank line after it
  assert.deepEqual(fenceSources(`${F}\n${F}`, tokens(`${F}\n${F}`)), ["\n"]);
  // an opener the file ends on: an unclosed fence runs to the end, and its text is empty
  assert.deepEqual(fenceSources(`${F}\n`, tokens(`${F}\n`)), ["\n"]);
  // inside a quote, the blank line a bare `>`; directly after another fence's closer
  const quoted = `> ${F}\n>\n> ${F}\n> ${F}\n> \tq\n> ${F}\n`;
  assert.deepEqual(fenceSources(quoted, tokens(quoted)), ["\n", "\tq\n"]);
  const after = `${F}\nx\n${F}\n${F}\n${F}\n${F}py\n\n\ty\n${F}\n`;
  assert.deepEqual(fenceSources(after, tokens(after)), ["x\n", "\n", "\n\ty\n"]);
});
