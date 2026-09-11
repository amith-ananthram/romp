// EXECUTES the DOM-key ↔ host-key bridge (./card-key). This existed as an implicit assumption rather
// than as code, and the assumption was wrong: the host answers "which cards cover these segments?" with
// bare goal-node ids while the feed keys its cards "a:<itemId>", the two were compared raw, and so
// hovering a timeline bar or a chat rail dot lit nothing at all. Everything else in those two directions
// was already built and routed (kernel _cards_for_segments → hoverCards → applyExtHover), which is what
// made it look like a missing feature instead of a one-line mismatch (the user 2026-07-23).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { extHoverMatches, cardKeyAliases } from "./card-key";

// Synthetic ids in the kernel's real shapes: "<sid>:g<N>" for a goal node, a uuid-ish turn id.
const SID = "11111111-2222-3333-4444-555555555555";
const GOAL = SID + ":g227";
const TURN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

test("THE BUG: a host-named goal id lights the ask card the feed keyed as a:<itemId>", () => {
  const keys = new Set([GOAL]);
  assert.equal(extHoverMatches("a:" + GOAL, keys), true,
    "this returned false before, so both reverse hover directions lit nothing");
});

test("a group card matches on its turn id, and a session header never false-matches", () => {
  assert.equal(extHoverMatches("g:" + TURN, new Set([TURN])), true);
  assert.equal(extHoverMatches("s:col-working:" + SID, new Set([GOAL])), false);
});

test("a host already speaking DOM keys still matches, so a future sender needs no change here", () => {
  assert.equal(extHoverMatches("a:" + GOAL, new Set(["a:" + GOAL])), true);
});

test("matching is EXACT after the prefix — one goal's id never lights a different card", () => {
  assert.equal(extHoverMatches("a:" + SID + ":g2270", new Set([GOAL])), false, "no prefix/substring match");
  assert.equal(extHoverMatches("a:" + GOAL, new Set([SID + ":g22"])), false);
  assert.equal(extHoverMatches("a:" + GOAL, new Set([SID])), false, "the session id alone lights nothing");
});

test("empty inputs are misses, never a match-everything", () => {
  assert.equal(extHoverMatches("a:" + GOAL, new Set()), false, "a hover CLEAR must unlight every card");
  assert.equal(extHoverMatches("", new Set([GOAL])), false);
  assert.equal(extHoverMatches(null, new Set([GOAL])), false);
  assert.equal(extHoverMatches(undefined, new Set([GOAL])), false);
});

test("cardKeyAliases offers the namespaced key first, then the bare domain id", () => {
  assert.deepEqual(cardKeyAliases("a:" + GOAL), ["a:" + GOAL, GOAL]);
  assert.deepEqual(cardKeyAliases(GOAL), [GOAL], "an unprefixed key has no second form");
});

// --- wiring: the rule is useless unbound ----------------------------------------------------------
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("applyExtHover routes through extHoverMatches and keeps no raw set lookup", () => {
  assert.match(FEED, /import \{ extHoverMatches \} from "\.\/card-key";/);
  assert.match(FEED, /c\.classList\.toggle\("dot-hl", extHoverMatches\(c\.dataset\.key, extHoverKeys\)\);/);
  assert.doesNotMatch(FEED, /extHoverKeys\.has\(c\.dataset\.key \|\| ""\)/, "the raw comparison is gone");
});

test("the ask card is still keyed a:<itemId>, which is what makes the bridge necessary", () => {
  // If this ever changes to a bare itemId the bridge becomes a no-op rather than wrong — but the test
  // should fail loudly so whoever changes it reads ./card-key first.
  assert.match(FEED, /card\.dataset\.key = "a:" \+ it\.itemId;/);
});

// --- the CLICK's landing in the feed: scroll there and pulse (the user 2026-07-23) -----------------
const FEEDCSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

test("revealCards scrolls to the first match and pulses every match, through the same bridge", () => {
  assert.match(FEED, /m\.type === "revealCards"/, "the feed accepts the click's landing");
  assert.match(FEED, /\.filter\(\(c\) => extHoverMatches\(c\.dataset\.key, keys\)\)/,
    "it lands on exactly the cards a hover would have outlined");
  assert.match(FEED, /hits\[0\]\.scrollIntoView\(\{ block: "center", behavior: "smooth" \}\)/);
  assert.match(FEED, /for \(const c of hits\) \{[\s\S]{0,200}?c\.classList\.add\("card-pulse"\)/,
    "all matching cards pulse, not just the one scrolled to");
});

test("the pulse restarts on a repeat click instead of silently doing nothing", () => {
  // Re-adding a class the element already has replays no animation, which is the "clicked it again and
  // nothing flashed" bug. Removing, forcing a reflow, then re-adding is what makes the second click land.
  const i = FEED.indexOf("function revealCards(");
  const body = FEED.slice(i, FEED.indexOf("\n}", i));
  assert.match(body, /classList\.remove\("card-pulse"\)[\s\S]*?void c\.offsetWidth;[\s\S]*?classList\.add\("card-pulse"\)/);
});

test("the pulse is accent chrome and survives reduced-motion as a static cue", () => {
  assert.match(FEEDCSS, /@keyframes romp-card-pulse/);
  assert.match(FEEDCSS, /outline-color: var\(--accent, #9cd2ff\)/, "accent blue, never a status colour");
  // the pulse IS the acknowledgement the click landed, so reduced-motion keeps the outline, not nothing
  assert.match(FEEDCSS, /@media \(prefers-reduced-motion: reduce\) \{\s*\.card-pulse \{ animation: none; outline-color: var\(--accent, #9cd2ff\); \}/);
});

test("the rail dot's click sends tlId, and its tooltip promises navigation rather than a modal", () => {
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /postMessage\(\{ type: "dotOpen", sid: activeId, uuid, t, tlId \}\)/);
  assert.match(RENDER, /dot\.title = "click: jump to this on the timeline \+ feed · hover: highlight there";/);
});

// --- the cross-pane hover LOOKS like a plain mouse hover (the user 2026-07-23) ---------------------
// Hovering a chat rail dot or a timeline bar means the same thing as putting the mouse on the card, so
// it must not wear a different look. It used to paint a neutral white outline that belonged to no card.

test("a cross-pane hover bolds the card's own session colour, sharing the .focused rule", () => {
  // Sharing the SELECTOR (not copying the declarations) is what stops the two drifting apart again.
  assert.match(FEEDCSS, /\.fitem\.ask\.focused, \.fitem\.ask\.dot-hl, \.fitem\.fgroup\.dot-hl \{/);
  const i = FEEDCSS.indexOf(".fitem.ask.focused, .fitem.ask.dot-hl");
  const rule = FEEDCSS.slice(i, FEEDCSS.indexOf("}", i));
  assert.match(rule, /border-color: rgb\(var\(--card-r/, "full-opacity session colour, as on mouse hover");
  // T270 (the user 2026-09-08): the bolding is PAINT ONLY — colour and shadow. The T221 cut grew the
  // border 2→3px with a compensating margin -1px, which kept the CSS boxes still but moved the RENDERED
  // text a device pixel on the user's display; no layout property may change with the hover.
  assert.doesNotMatch(rule, /border-width|margin|padding/, "no layout property changes with the hover");
  assert.match(rule, /box-shadow: 0 2px 7px/, "the lift shadow is the other half of the bolding");
  assert.doesNotMatch(rule, /box-shadow: 0 0 0 1px/, "…never a second ring paint laid against the border (T221's seam)");
});

test("the white outline no longer lands on a card, only on the modal rows that have no colour", () => {
  assert.match(FEEDCSS, /\.dot-hl:not\(\.fitem\) \{ outline: 1\.5px solid rgba\(255, 255, 255, 0\.9\)/);
  assert.doesNotMatch(FEEDCSS, /^\.dot-hl \{/m, "the unscoped white-outline rule is gone");
});

test("a pinned card still bolds to full when another pane hovers it", () => {
  // .pinned holds the same property at 0.85α and equal specificity, so only source order decides. The
  // shared rule has to stay BELOW it or a pinned card would ignore the hover entirely.
  assert.ok(FEEDCSS.indexOf(".fitem.ask.pinned") < FEEDCSS.indexOf(".fitem.ask.focused, .fitem.ask.dot-hl"),
    "the shared highlight must come after .pinned");
});

test("a focused-section copy (T347) answers to its board key and to the bare domain id, like the card below", () => {
  // the section renders a second element per card under "f:a:<itemId>" / "f:g:<turnId>": one more prefix over
  // the board's key, the same identity; a cross-pane hover or a reveal must reach the copy too
  assert.equal(extHoverMatches("f:a:" + GOAL, new Set([GOAL])), true, "the host's bare goal id lights the copy");
  assert.equal(extHoverMatches("f:a:" + GOAL, new Set(["a:" + GOAL])), true, "a host speaking DOM keys lights it too");
  assert.equal(extHoverMatches("f:g:" + TURN, new Set([TURN])), true, "a group copy matches on its turn id");
  assert.equal(extHoverMatches("f:a:" + GOAL, new Set([SID + ":g228"])), false, "exact after the prefixes, never a substring");
  assert.deepEqual(cardKeyAliases("f:a:" + GOAL), ["f:a:" + GOAL, "a:" + GOAL, GOAL], "most-specific first: the copy, the board's key, the bare id");
  assert.deepEqual(cardKeyAliases("a:" + GOAL), ["a:" + GOAL, GOAL], "a board key is unchanged by the bridge");
});
