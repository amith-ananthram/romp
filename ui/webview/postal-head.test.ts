// T294 (the user 2026-09-10, from a sent card in their chat): a postal message must ALWAYS be reachable. Since the
// notice rewrite the card's head is one nowrap line with an ellipsis, and the fold under it exists only when the
// gist differs from the message. For mail without a recipient caption the gist was the message's first line,
// UNCLIPPED, so a one-paragraph message (most sent mail, and every inbound message the recipient's judge has not
// captioned yet, the inbound question included) compared equal to itself: no fold, no click, and everything past
// the ellipsis was gone. The pre-rewrite card rendered the whole body in exactly that case.
//
// The rule now (ui/webview/gist.ts postalHead): the gist is the caption, else the first line CLIPPED by gistOf;
// the fold holds the full message whenever the gist does not carry all of it. A short one-liner keeps no fold
// (nothing is hidden) and, as a head-only postal notice, wraps instead of truncating. The head's meta (the kind,
// the delivery state) never shrinks to a letter: the gist is the one flexible slot.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { postalHead, gistOf } from "./gist";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");

const PARAGRAPH = "Search endpoint is in: GET /notes?q= ranks title matches above body matches, ignores case, and returns "
  + "the same envelope as the unfiltered list, so the client needs no change beyond passing the parameter; the request "
  + "test covers an empty query, a title hit, a body-only hit and a query with no matches.";

test("a one-paragraph message without a caption: the gist is the clipped first line and the fold holds the whole message", () => {
  const h = postalHead({ body: PARAGRAPH });
  assert.equal(h.gist, gistOf(PARAGRAPH), "the head shows the clipped gist, not the unclipped paragraph");
  assert.ok(h.gist.endsWith("…") && h.gist.length <= 90, "clipped at the shared head width: " + JSON.stringify(h.gist));
  assert.equal(h.body, PARAGRAPH, "the whole message is one click away");
});

test("the same for inbound mail the recipient's judge has not captioned yet (the question that opens by default included)", () => {
  const ask = "Which pagination shape do you want on GET /notes: page/perPage query params, or a cursor token? The client "
    + "mock currently assumes page/perPage. Reply needed before I wire the route.";
  const h = postalHead({ body: ask, summary: "" });
  assert.notEqual(h.body, null, "an uncaptioned inbound message keeps its fold");
  assert.equal(h.body, ask);
});

test("a short one-liner hides nothing: the gist is the message and there is no fold", () => {
  const h = postalHead({ body: "Fixture move done; nothing else changed." });
  assert.equal(h.gist, "Fixture move done; nothing else changed.");
  assert.equal(h.body, null);
});

test("a multi-line message: the first line is the gist, the fold holds all of it", () => {
  const body = "Pagination decision: page/perPage, default 20, max 100.\n\n- the envelope gains total and page\n- an out-of-range page returns an empty list";
  const h = postalHead({ body });
  assert.equal(h.gist, "Pagination decision: page/perPage, default 20, max 100.");
  assert.equal(h.body, body);
});

test("a caption is the gist, the fold holds the message; a caption that IS the message leaves no fold", () => {
  const h = postalHead({ body: PARAGRAPH, summary: "search endpoint shipped with tests" });
  assert.equal(h.gist, "search endpoint shipped with tests");
  assert.equal(h.body, PARAGRAPH);
  const same = postalHead({ body: "ship it", summary: "  ship   it " });
  assert.equal(same.body, null, "whitespace-only differences hide nothing");
  assert.deepEqual(postalHead({ body: "" }), { gist: "", body: null });
});

test("the fold holds the message AS SENT: a leading indent (a markdown code block) survives, the comparison alone trims", () => {
  const body = "    GET /notes?q=x -> 200\n    GET /notes     -> 200\n\nboth cases pass";
  const h = postalHead({ body });
  assert.equal(h.gist, "GET /notes?q=x -> 200");
  assert.equal(h.body, body, "the four-space indent that opens the code block is still there");
  assert.equal(postalHead({ body: "  ship it  " }).body, null, "surrounding whitespace alone hides nothing");
});

test("render.ts builds the postal card's head through postalHead and the shared gist rule lives in gist.ts", () => {
  const RENDER = read("ui", "webview", "render.ts");
  assert.match(RENDER, /import \{ [^}]*\bpostalHead\b[^}]* \} from "\.\/gist";/);
  assert.doesNotMatch(RENDER, /\nfunction gistOf\(/, "one gist rule, in gist.ts");
  const start = RENDER.indexOf("function renderPostalService(");
  const fn = RENDER.slice(start, RENDER.indexOf("\nfunction ", start + 10));
  assert.match(fn, /const \{ gist: summaryText, body: fullMd \} = postalHead\(ev\);/);
  assert.match(fn, /if \(fullMd\) \{ body = el\("div", "notice-md md"\); body\.innerHTML = md\(fullMd, postalRepoFor\(ev\)\); highlight\(body\); linkTerms\(body\); \}/);
  assert.doesNotMatch(fn, /postalServiceSummary\(ev\)|collapseWs\(fullText\)/, "the old in-place rule is gone");
});

test("the head's meta never shrinks to a letter, and a head-only postal line wraps instead of truncating", () => {
  const CSS = read("ui", "webview", "styles.css");
  assert.match(CSS, /\.turn-postal-service \.notice \{ container-type: inline-size; \}\n(?:\/\*[\s\S]*?\*\/\n)?\.turn-postal-service \.notice-meta \{ flex: 1 0 auto; display: inline-flex; align-items: baseline; gap: 7px; min-width: 0; \}\n@container \(max-width: 360px\) \{\n  \.turn-postal-service \.notice-head \{ flex-wrap: wrap; \}\n  \.turn-postal-service \.notice-head > \.notice-gist \{ flex: 1 0 100%; order: 1; min-width: 0;/,
               "the CARD is the size container (a query resolves against ancestors: a rule on the head cannot query the head itself) and the postal meta is rigid at every width; under 360px the head wraps and the GIST takes its own full-width line, never a letter column beside the kind word (T313)");
  assert.doesNotMatch(CSS, /\.notice-head \{ container-type/, "never the head as its own container: its wrap rule would never match");
  // scoped to the postal card: every other notice head's meta still yields (an API-error meta is a sentence, a compact
  // group head's meta is a whole gist, and both sit beside actions that must stay on the pane)
  assert.match(CSS, /\n\.notice-meta \{ flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;/);
  // the case that keeps the base rule shrinkable: the compact-mode notice-group head puts the first notice's WHOLE
  // gist (up to 90 chars) in the meta slot while folded, and the system-context notice a model · dir · N CLAUDE.md
  // string; rigid, either would push the head past a phone-width pane with the gist collapsed to nothing
  const RENDER = read("ui", "webview", "render.ts");
  assert.match(RENDER, /gist: `\$\{evs\.length\} notices`, meta: open \? undefined : b\.gist,/, "renderNoticeGroup's meta is a whole gist");
  assert.doesNotMatch(CSS, /\n\.notice-meta \{ flex: 0 0 auto/, "the rigid meta is never the base rule");
  // and the gist keeps a legible floor beside that long meta on the group head and the postal card (measured at 390 px:
  // "2 notices" had shrunk to 20 px), but NOT as the base rule: a floor on the API-error head pushed its buttons off a
  // phone-width pane
  assert.match(CSS, /\n\.notice-gist \{ flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;/);
  assert.match(CSS, /\n\.notice-head\[data-gkey\] \.notice-gist, \.turn-postal-service \.notice-gist \{ min-width: min\(100%, 10ch\); \}/);
  assert.match(CSS, /\.turn-postal-service \.notice:not\(\.notice-collapsible\) \.notice-gist \{ white-space: normal; overflow: visible; text-overflow: clip; overflow-wrap: anywhere; \}/,
               "a bare link (no space to break at) wraps too instead of running off the head");
  // the wrapped head keeps its glyph on the first line: a centred glyph sank to the middle of a three-line block
  assert.match(CSS, /\.turn-postal-service \.notice:not\(\.notice-collapsible\) \.notice-glyph \{ align-self: flex-start; margin-top: 4px; \}/);   // keyed on the fold since T302 (a boxed incoming one-liner wraps too)
});
