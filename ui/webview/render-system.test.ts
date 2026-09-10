// The pinned "system context" card (the user 2026-06-19): a bordered BOX at the top of the transcript
// showing the CLAUDE.md instructions in effect + the session's model/cwd/branch/permission-mode/version.
// It looks complete even collapsed (⚙ header + one-line summary + caret), and its open/closed state — like
// every other collapsible — survives the re-render a send/turn triggers (persisted in `openFolds`, not the
// DOM). The chat renderer has no jsdom harness, so — like the other webview tests — this pins the wiring at
// the source level.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("there is a 'system' ChatEvent kind carrying the config meta + the CLAUDE.md docs", () => {
  assert.match(RENDER, /kind: "system";[\s\S]*?claudemd\?: \{ path: string; scope: string; text: string \}\[\]/);
});

test("renderEventInner dispatches the system kind to renderSystem", () => {
  assert.match(RENDER, /if \(ev\.kind === "system"\) return renderSystem\(ev\);/);
  assert.match(RENDER, /function renderSystem\(ev: Extract<ChatEvent, \{ kind: "system" \}>\)/);
});

test("the card is a SYSTEM notice — gear glyph, 'System context' gist, the summary as meta, a keyed fold (2026-09-08)", () => {
  // the notice-vocabulary pass: the bespoke .sys-card (9px, its own ⚙ text glyph and caret) is the ONE builder now;
  // it stays off the rail (nested, .turn-system hides the line) and complete when collapsed
  assert.match(RENDER, /notice\(\{ src: "system", glyph: "system", gist: "System context", meta: bits\.join\(" · "\) \|\| undefined,/);
  assert.match(RENDER, /body, key, nested: true, tip: "the CLAUDE\.md instructions \+ config this session is running under" \}\)/);
  assert.match(RENDER, /bits\.push\(prettyModel\(ev\.model\)\)/);
  assert.match(RENDER, /bits\.push\(`\$\{n\} CLAUDE\.md`\)/);
  assert.match(CSS, /\.notice \{[^}]*border: 1px solid var\(--box-border\)/);
  assert.match(CSS, /\.notice-collapsible:not\(\.notice-open\) > \.notice-body \{ display: none; \}/);
  assert.doesNotMatch(CSS, /\.sys-card/);
});

test("each CLAUDE.md doc renders as a raw, scrollable SUB-box with a scope badge + path", () => {
  assert.match(RENDER, /\(ev\.claudemd \|\| \[\]\)\.forEach\(\(doc, i\) =>/);   // indexed — each doc's scroll key needs its position
  assert.match(RENDER, /el\("span", "sys-doc-scope " \+ \(doc\.scope === "global" \? "global" : "project"\)\)/);
  assert.match(RENDER, /sec\.appendChild\(preEl\(doc\.text, key \? key \+ ":doc" \+ i : undefined\)\)/,
    "raw text in a .fold-pre box, not markdown-rendered — scroll keyed per doc (fold-scroll.test.ts)");
});

test("the card never claims to be the verbatim harness prompt — it says the base prompt isn't recorded", () => {
  assert.match(RENDER, /el\("div", "sys-note"\)/);
  assert.match(RENDER, /base harness prompt isn.t recorded in the transcript/);
});

test("the system card sits OFF the conversational rail (no dot/timeline wiring)", () => {
  assert.match(CSS, /\.turn-system::before \{ display: none; \}/);
  assert.match(CSS, /\.turn-system \{[^}]*padding-left/);
});
