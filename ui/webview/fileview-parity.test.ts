// The file VIEWER mounts in both documents (file-view.ts is one shared module), but the chat page
// loads styles.css alone and the feed page feed.css alone — so its dress is declared in BOTH sheets
// (the .romp-acted / filebrowse precedent). The copies had already drifted once (feed.css lacked the
// a.fileview-btn anchor rules, so the GitHub link rendered hrefless-underlined there, 2026-08-26).
// This pins the shared chrome byte-equal so it cannot drift again, and with it the rendered document's
// type scale (the .fileview-md heads below): every occurrence of a head, read at a line start, so a
// head two rules share (the heading list carries the scale and, later, the landing margin) is compared
// rule for rule, and a rule present in one sheet only is a failure, not a skip. The md body's root rule
// is among them for more than its typography: its `contain: layout` is what keeps a file's
// fixed-positioned element inside the note, and it has to hold in both documents, as do the width caps
// on the media a file draws itself (svg, canvas, video), which under containment would otherwise be
// clipped and unreachable, and the table rules that give a wide table a horizontal scroll of its own
// for the same reason. Rules that are deliberately pane-specific (wrap mode, load cue) are not pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const CHAT = read("styles.css");
const FEED = read("feed.css");

const RULES = [
  "#romp-fileview {", ".fileview {", "body.fileview-open {", ".fileview-bar {", ".fileview-name {",
  ".fileview-dir {", ".fileview-base {", ".fileview-sess {", ".fileview-sess .host-prefix {", ".fileview-acts {",
  ".fileview-bar .fileview-name {", ".fileview-bar .fileview-acts {",   // the bar's own wrap (scoped: the file browser's row wears .fileview-acts too)
  ".fileview-btn {", ".fileview-btn:hover {",
  "a.fileview-btn {", ".fileview-gh {", ".fileview-gh-why {", ".fileview-gh-dots {",
  // one disabled dress for every bar button: the GitHub unit's no-link state and the text-size control's ends
  '.fileview-btn:disabled, .fileview-btn[aria-disabled="true"] {', '.fileview-btn:disabled:hover, .fileview-btn[aria-disabled="true"]:hover {',
  '.fileview-btn:disabled:active, .fileview-btn[aria-disabled="true"]:active {', ".fileview-size-reset {", ".fileview-size-reset.fileview-size-default {",
  "a.fileview-gh-note {", ".fileview-body {",
  ".fileview-cm {", ".fileview-cm .cm-editor {", ".fileview-editor {", ".fileview > .fileview-err {",
  ".fileview-dir-link {", ".fileview-dir-link:hover {",
  ".fileview-imgbox {", ".fileview-img {", ".fileview-frame {",
  // the rendered document's type scale: the root (the face, the size, the leading, the measure), the heading ladder, the
  // list gutter, a task item, kbd, fenced code with its rows and Copy, a table's fill, striping, alignment and break-out,
  // and the caps on the media a file draws itself
  "@property --fv-body-w {",
  ".fileview-md {",
  ".fileview-md h1, .fileview-md h2, .fileview-md h3, .fileview-md h4, .fileview-md h5, .fileview-md h6 {",
  ".fileview-md h1 {", ".fileview-md h2 {", ".fileview-md h3 {", ".fileview-md h4 {", ".fileview-md h5, .fileview-md h6 {",
  ".fileview-md h1, .fileview-md h2 {",
  ".fileview-md ul, .fileview-md ol {", ".fileview-md li {",
  ".fileview-md li.task-list-item {", ".fileview-md li.task-list-item input {",
  '.fileview-md li.task-list-item > input[type="checkbox"], .fileview-md li.task-list-item > p:first-child > input[type="checkbox"] {',
  ".fileview-md kbd {", ".fileview-md :not(pre) > code {",
  ".fileview-md pre {", ".fileview-md pre code {",
  ".fileview-md pre code .cl {", ".fileview-md pre code .cl::before {", ".fileview-md pre code .ct {", ".fileview-md pre code .ct::before {",
  ".fileview-md pre.has-copy {", ".fileview-md .code-copy {", ".fileview-md pre.has-copy:hover .code-copy, .fileview-md .code-copy:focus-visible {",
  ".fileview-md .code-copy:hover {", ".fileview-md .code-copy.copied {",
  ".fileview-md table {", ".fileview-md > table {", ".fileview-md th, .fileview-md td {", ".fileview-md th {", ".fileview-md tbody tr:nth-child(even) {",
  '.fileview-md th[align="center"], .fileview-md td[align="center"] {', '.fileview-md th[align="right"], .fileview-md td[align="right"] {',
  '.fileview-md th[align="left"], .fileview-md td[align="left"] {',
  ".fileview-md img {",
  // the caps on the media a file draws itself (svg, canvas, video): under contain: layout anything wider than the root is
  // clipped and unreachable, so they have to hold in both documents
  ":where(.fileview-md) svg, :where(.fileview-md) canvas, :where(.fileview-md) video {",
  ':where(.fileview-md :is(svg, canvas)[width]:not([width$="%"])) {',
  // links inside a shown file (file-view-links.ts): the light dress on a URL anchor and a path link, the Markdown link that names a file, a dead link
  ".fileview-body .file-uri-link, .fileview-body .fv-url {", ".fileview-body .file-uri-link:hover, .fileview-body .fv-url:hover {",
  ".fileview-md a.file-uri-link {", ".fileview-md a.file-uri-link:hover {", ".fileview-md a.fv-dead {",
];

/** Every rule whose selector opens a line as `head`, in sheet order; at least one, or the head is missing. */
function rulesOf(css: string, head: string): string[] {
  const out: string[] = [];
  for (let at = css.indexOf("\n" + head); at >= 0; at = css.indexOf("\n" + head, at + 1)) out.push(css.slice(at + 1, css.indexOf("}", at) + 1));
  assert.ok(out.length > 0, head + " present");
  return out;
}
/** A block at-rule (`@media print {`), whole: from its head to the first close brace at a line start. */
function blockOf(css: string, head: string): string {
  const at = css.indexOf("\n" + head);
  assert.ok(at >= 0, head + " present");
  return css.slice(at + 1, css.indexOf("\n}", at) + 2);
}

test("the viewer's shared chrome and the document's type scale exist in BOTH sheets, byte-equal, every occurrence of every head", () => {
  for (const head of RULES) {
    assert.deepEqual(rulesOf(CHAT, head), rulesOf(FEED, head), head + " mirrors exactly");
  }
});

test("the print sheet is one block in both sheets, byte-equal: the file alone, black on white", () => {
  const print = blockOf(CHAT, "@media print {");
  assert.equal(print, blockOf(FEED, "@media print {"), "@media print mirrors exactly");
  assert.ok(print.length > 500, "the block with its rules");
  assert.equal((CHAT.match(/^@media print \{/gm) || []).length, 1, "one print block in styles.css");
  assert.equal((FEED.match(/^@media print \{/gm) || []).length, 1, "one print block in feed.css");
  // the file alone: the title bar and the Copy buttons leave, the page and the card go white on black
  assert.match(print, /\.fileview-bar, \.fileview > \.fileview-err, \.fileview-md \.code-copy \{ display: none; \}/);
  assert.match(print, /:root, body\.fileview-open \{ height: auto; overflow: visible; background: white; color: black; \}/);
  assert.match(print, /body\.fileview-open > :not\(#romp-fileview\) \{ display: none; \}/);
  // the task box is drawn in the light scheme whatever the page's theme, and a table is a table again
  assert.match(print, /\.fileview-md li\.task-list-item input\[type="checkbox"\] \{ color-scheme: light; \}/);
  assert.match(print, /\.fileview-md table \{ display: table; width: max-content; overflow: visible; overflow-wrap: anywhere; \}/);
  // the Raw view's gutter and tokens print in full black too
  assert.match(print, /\.fileview-gutter/);
  assert.match(print, /\.fileview-body code\.hljs, \.fileview-body code\.hljs span/);
});
