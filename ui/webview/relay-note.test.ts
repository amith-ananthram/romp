// A far host still holding a relayed question after its wait ended is said on the card and in the modal as
// its OWN line (relayNote), never as a paragraph appended to the brief (the feed maps briefParts onto the
// brief's paragraphs and allows exactly one extra, so a note paragraph on a briefed top node dropped every
// per-paragraph age stamp and citation) and never inside the distill element (the section logic sets that
// element's display to none without a brief, in collapsed mode, when another section is open and on a
// working-column card, so a note appended there showed nothing; the manager's verifier on this change). The
// card's wiring is pinned here at the source, as session-started-face.test.ts pins the face line it mirrors;
// the EFFECT (the note visible with no brief, in collapsed mode, on a working card, cleared on the next
// push) is RUN under the DOM stand-in in feed-render-incremental.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("the card item and the modal tree node both carry relayNote from the kernel", () => {
  assert.equal((FEED.match(/^  relayNote\?: string \| null;/gm) || []).length, 2);
});

test("the card's note is its own element beside the sections, outside them, set after the section logic", () => {
  const made = FEED.indexOf('rn = el("div", "fask-distill fask-relaynote");');
  assert.ok(made > 0, "the note has its own element (the distill line's look), created once");
  assert.ok(FEED.includes("anchor.parentNode!.insertBefore(rn, anchor.nextSibling);"), "inserted beside the sections, never inside them or the distill element");
  const sections = FEED.indexOf("applySections(a, it, !!distillShown);");
  assert.ok(sections > 0 && made > sections, "set after the section logic runs, so nothing re-hides it");
  const body = FEED.slice(FEED.indexOf("function applySections("), FEED.indexOf("\nfunction updateAskCard("));
  assert.ok(body.length > 1000 && !body.includes("_relayNote"), "the section logic never touches the note's element");
  assert.doesNotMatch(FEED, /\(a\._distill as HTMLElement\)\.append\(rn\)/, "never appended into the distill element again");
  assert.doesNotMatch(FEED, /blockSummary[^\n]*relayNote|relayNote[^\n]*blockSummary/, "never folded into the brief's text");
});

test("the modal tree shows the note under the node's brief, indented with the node", () => {
  assert.match(FEED, /if \(node\.relayNote\) \{\s*\n\s*const rn = el\("div", "ftree-relaynote"\);\s*\n\s*rn\.style\.paddingLeft = \(\(depth \+ 1\) \* TREE_INDENT_EM\) \+ "em";/);
});

test("both lines are styled dim, as a note beside the brief and not the brief", () => {
  assert.match(CSS, /\.fask-relaynote \{[^}]*color: var\(--dim\)/);
  assert.match(CSS, /\.ftree-relaynote \{[^}]*color: var\(--dim\)/);
});
