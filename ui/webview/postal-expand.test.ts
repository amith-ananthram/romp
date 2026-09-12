// Postal cards ALWAYS lead with a one-line summary and expand to the full message on click (the user
// 2026-06-16). Before, incoming mail showed the Haiku caption (full body only as a hover tooltip) while
// outgoing mail showed the whole body — inconsistent. Now both render a summary (the caption, or the
// first line for sent mail with no caption) that opens inline on click. The chat renderer has no jsdom
// harness, so — like render-postal-time.test.ts — pin it at the source level.
//
// 2026-07-25 (the user, from a real sent card): three more guarantees pinned here —
//   1. the expand is KEYED (openFolds), because the unkeyed toggle was silently re-collapsed by the
//      next kernel push (the user watched a card open and snap shut a moment later);
//   2. expanded shows the full message ALONE — the summary line was repeating the same words right
//      above the body;
//   3. the collapsed fallback is clamped by CSS to two full lines, not pre-truncated at 100 chars,
//      which parked the "…" mid-line and wasted the rest of the second line.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const GIST = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gist.ts"), "utf8");   // T294: the head rule lives here (postalHead)

test("a postal summary is the caption, or the CLIPPED first line of the body when there's none", () => {
  // T294 (2026-09-10): the rule is gist.ts postalHead: the caption, else gistOf's clip of the first line; an UNCLIPPED
  // first line equalled a one-paragraph message, which then had no fold and nothing past the head's ellipsis
  assert.match(GIST, /export function postalHead\(ev: \{ body\?: string; summary\?: string \}\): PostalHead \{/);
  assert.match(GIST, /const cap = ev\.summary && ev\.summary\.trim\(\);/);
  assert.match(GIST, /const gist = cap \|\| gistOf\(full\);/);
  assert.doesNotMatch(RENDER, /slice\(0, 99\)/, "the 100-char pre-truncation must be gone");
  assert.doesNotMatch(RENDER, /function postalServiceSummary/, "the unclipped first-line rule is gone from render.ts");
  // 2026-09-08 (the notice-vocabulary pass): the summary is the notice GIST — one nowrap line with an ellipsis, the
  // head grammar every notice shares (the two-line clamp went with the bespoke card)
  assert.match(RENDER, /const \{ gist: summaryText, body: fullMd \} = postalHead\(ev\);/);
  assert.match(CSS, /\.notice-gist \{[^}]*text-overflow: ellipsis/);
});

test("both directions render the summary + a click-to-expand full body (no hover tooltip)", () => {
  assert.match(GIST, /const body = !!full && collapseWs\(full\) !== collapseWs\(gist\) \? raw : null;/, "the fold holds the message as sent; the trim is the comparison's");
  // 2026-09-08: the full message is the notice BODY, markdown-rendered against the sender's repo
  assert.match(RENDER, /if \(fullMd\) \{ body = el\("div", "notice-md md"\); body\.innerHTML = md\(fullMd, postalRepoFor\(ev\)\); highlight\(body\); linkTerms\(body\); \}/);
  assert.doesNotMatch(RENDER, /body\.title = ev\.body/, "the old hover-tooltip full body must be gone");
  assert.doesNotMatch(RENDER, /caption \|\| ev\.body/, "no longer 'caption else whole body'");
});

test("the expand is KEYED so a kernel push can't silently re-collapse it (the user 2026-07-25)", () => {
  // 2026-09-08: the key rides the ONE builder (openFolds "notice:postal:<mid>"), toggled by the body delegate
  assert.match(RENDER, /key: "postal:" \+ \(ev\.mid \|\| ev\.uuid \|\| ""\), rail: ev\.color \? ev\.color\.bg : undefined,/);
  const start = RENDER.indexOf("function renderPostalService(");
  const end = RENDER.indexOf("\nfunction ", start + 10);
  assert.doesNotMatch(RENDER.slice(start, end), /classList\.toggle\("expanded"\)|addEventListener/,
    "no DOM-only toggle and no per-node listener — both died with the node on the next push");
});

test("the postal notice folds like every notice (body hidden until open; the head is the click target)", () => {
  assert.match(CSS, /\.notice-collapsible:not\(\.notice-open\) > \.notice-body \{ display: none; \}/);
  assert.match(CSS, /\.notice-collapsible > \.notice-head \{ cursor: pointer; user-select: none; \}/);
  assert.doesNotMatch(CSS, /\.postal-service-full|\.postal-service-summary|\.postal-service-expandable/);
});

test("an incoming QUESTION opens by default — a reply is owed; everything else folds (2026-09-08)", () => {
  // the notice-vocabulary pass: the head (summary) always stays visible above the body — the one fold rule; what
  // changed for postal is the DEFAULT: a peer asking a question is the human-is-the-bottleneck case
  assert.match(RENDER, /const owed = !!intent && intent\.cls === "question" && ev\.direction === "in";/);
  assert.match(RENDER, /body, open: owed,/);
});
