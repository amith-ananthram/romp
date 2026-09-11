// A far host still holding a relayed question after its wait ended is said on the card and in the modal as
// its OWN line (relayNote), never as a paragraph appended to the brief: the feed maps briefParts onto the
// brief's paragraphs and allows exactly one extra, so a note paragraph on a briefed top node dropped every
// per-paragraph age stamp and citation (the manager's fourth verdict on the peer wait). No jsdom for the feed
// renderer, so the wiring is pinned at the source, as feed-distill-state.test.ts pins the distiller line.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the card item and the modal tree node both carry relayNote from the kernel", () => {
  assert.equal((FEED.match(/^  relayNote\?: string \| null;/gm) || []).length, 2);
});

test("the card renders the note as its own element after the parts-split, never inside the brief's text", () => {
  const i = FEED.indexOf('el("div", "fsum-relaynote")');
  assert.ok(i > 0, "the note element exists");
  const split = FEED.indexOf("distillShown.split(/\\n\\s*\\n/)");
  assert.ok(split > 0 && split < i, "appended after the paragraph split, which rewrites the element");
  assert.match(FEED, /rn\.textContent = it\.relayNote;\s*\n\s*\(a\._distill as HTMLElement\)\.append\(rn\);\s*\n\s*\(a\._distill as HTMLElement\)\.style\.display = "";/,
               "appended to the distill element and shown even before a brief exists");
  // the brief's paragraph count is read from the TEXT the distiller line returned, so the note element never joins it
  assert.doesNotMatch(FEED, /blockSummary[^\n]*relayNote|relayNote[^\n]*blockSummary/, "the note is never folded into the brief's text");
});

test("the modal tree shows the note under the node's brief, indented with the node", () => {
  assert.match(FEED, /if \(node\.relayNote\) \{\s*\n\s*const rn = el\("div", "ftree-relaynote"\);\s*\n\s*rn\.style\.paddingLeft = \(\(depth \+ 1\) \* TREE_INDENT_EM\) \+ "em";/);
});

test("both lines are styled dim, as a note beside the brief and not the brief", () => {
  assert.match(CSS, /\.fsum-relaynote \{[^}]*color: var\(--dim\)/);
  assert.match(CSS, /\.ftree-relaynote \{[^}]*color: var\(--dim\)/);
});
