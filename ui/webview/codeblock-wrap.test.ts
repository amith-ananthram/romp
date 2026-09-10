// Long code/pre blocks in the chat wrap instead of scrolling sideways, with a subtle line-number gutter
// so a soft-wrap reads distinctly from a real newline (the user 2026-06-16). No jsdom harness here —
// like feed-dead.test.ts, pin the behaviour at the source level (regex over render.ts + styles.css).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const BLOCK = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "code-block.ts"), "utf8");   // the rows' home, shared with the file viewer
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("markdown code blocks wrap instead of scrolling sideways", () => {
  assert.match(CSS, /\.md pre \{[^}]*overflow-x: hidden/);          // the <pre> no longer scrolls horizontally
  assert.match(CSS, /\.md pre code \{[^}]*white-space: pre-wrap/);  // its code wraps
});

test("wrapped code carries a subtle (faint) line-number gutter", () => {
  assert.match(CSS, /pre code\.hljs \{[^}]*counter-reset: ln/);                 // a per-block line counter
  assert.match(CSS, /pre code \.cl::before \{[^}]*counter-increment: ln/);      // each .cl draws its number
  assert.match(CSS, /pre code \.cl::before \{[^}]*opacity: 0\.3/);              // "incognito" — faint
  assert.match(CSS, /pre code \.ct \{[^}]*white-space: pre-wrap/);             // the content wraps
  // the shared module splits each highlighted line into <span class=cl><span class=ct>…, re-opening straddling
  // spans (code-block.test.ts executes the walk); the chat calls it for every fence when line numbers are on
  assert.match(BLOCK, /export function wrapCodeLines/);
  assert.match(BLOCK, /class="cl"><span class="ct"/);
  assert.match(RENDER, /if \(lineNos\) wrapCodeLines\(code\);/);
  assert.doesNotMatch(RENDER, /function wrapCodeLines/, "render.ts keeps no copy of its own");
  // the gutter's basis grows with the fence's digit count (wrapCodeLines writes --ln-digits), its line box is never
  // taller than the text's, and a blank row keeps the line-height through the word joiner in its empty cell
  assert.match(CSS, /pre code \.cl::before \{[^}]*flex: 0 0 max\(2\.5em, calc\(var\(--ln-digits, 0\) \* 1ch \+ 0\.05em\)\)/);
  assert.match(CSS, /pre code \.cl::before \{[^}]*line-height: 1;/);
  assert.match(CSS, /pre code \.ct::before \{ content: "\\2060"; \}/);
});

test("Edit diffs render a two-column line-number gutter (the user 2026-06-29)", () => {
  // PREFER the kernel's real-line-number rows (structuredPatch); fall back to numberDiff's relative gutter
  assert.match(RENDER, /const rows: DiffRow\[\] = ev\.diffRows\?\.length \? ev\.diffRows : numberDiff\(ev\.diff \|\| ""\);/);
  assert.match(RENDER, /el\("span", "diff-gut diff-gut-old"\)/);
  assert.match(RENDER, /el\("span", "diff-gut diff-gut-new"\)/);
  // a per-row class: add / del / @@ hunk header / context
  assert.match(RENDER, /r\.sign === "@" \? "diff-hunk"/);
  // the gutter + add/del coloring are styled
  assert.match(CSS, /\.diff-fold \.diff-row \{[^}]*display: grid/);
  assert.match(CSS, /\.diff-gut \{[^}]*tabular-nums/);
});
