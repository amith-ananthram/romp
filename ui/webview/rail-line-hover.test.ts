// The chat rail LINE is a nav handle like its dots (the user 2026-07-02): hovering exactly on a turn's
// rail segment lights it with the same expanding white ring the dots use (.dot-nav:hover) and drives the
// same timeline/feed cross-highlight (same dotHover payload, same 120ms intent debounce). The line is the
// turn's ::before pseudo — no pointer events — so render.ts overlays a slim .rail-hit strip; the dot's
// enlarged hit pad stacks above it, so the dot wins where they overlap. Source pins (no jsdom).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("every nav-able turn grows a rail hit strip, wired like the dot", () => {
  const fn = SRC.slice(SRC.indexOf("function wireTurnHover"), SRC.indexOf("function applyGlow"));
  assert.match(fn, /const rail = el\("div", "rail-hit"\);/);
  assert.match(fn, /turn\.appendChild\(rail\);/, "created inside wireTurnHover — only turns with nav data get one");
  // the SAME dotHover payload + the same 120ms intent debounce as the dot hover
  assert.match(fn, /railTimer = setTimeout\(\(\) => \{ railTimer = undefined; if \(activeId\) vscodeApi\?\.postMessage\(\{ type: "dotHover", sid: activeId, uuid, t, tlId \}\); \}, 120\);/);
  // instant feedback is a dot-anchored band too — never the old per-turn slice that flashed an
  // arbitrary chopped bit before the segment band landed (the user 2026-07-02 ×3); segment-wide with a
  // nearest-dot fallback since 2026-07-17
  assert.match(SRC, /railPromptDotAbove\(first, hostR\) \?\? railFirstDotIn/, "edges via the shared railBandEdges");
  assert.match(SRC, /drawRailBand\(host, hostR, turn, e\.top, e\.bottom, true\);/);
  assert.doesNotMatch(fn, /rail-glow/, "the box-bounded slice glow is gone");
});

test("the hover acknowledgement is ATOMIC on BOTH handles — dot and strip share instantLocalBand", () => {
  // the user 2026-07-17: hovering a dot lit the dot at once but the related lines lagged by the 120ms
  // intent debounce + a round-trip. The local band now draws SYNCHRONOUSLY in both mouseenter handlers,
  // BEFORE the debounced cross-surface message; the kernel's full-segment band still replaces it on
  // fan-back. mouseleave clears it through the shared helper too.
  const fn = SRC.slice(SRC.indexOf("function wireTurnHover"), SRC.indexOf("function applyGlow"));
  const enters = fn.match(/addEventListener\("mouseenter", \(\) => \{\s*\n\s*(?:\/\/[^\n]*\n\s*)*cancelHoverClear\(\);[^\n]*\n\s*instantLocalBand\(turn\);/g) || [];
  assert.equal(enters.length, 2, "dot AND rail strip mouseenter both cancel the pending clear, then draw the instant band");
  const leaves = fn.match(/addEventListener\("mouseleave", \(\) => \{\s*\n\s*clearLocalBand\(\);/g) || [];
  assert.equal(leaves.length, 2, "both mouseleave handlers clear through the shared helper");
  // the instant band precedes the debounce timer in source order within each enter handler
  assert.ok(fn.indexOf("instantLocalBand(turn);") < fn.indexOf("timer = setTimeout"), "local ack before the debounce");
});

test("the local band spans the hovered turn's WHOLE segment — prompt dot to next prompt dot", () => {
  // the user 2026-07-17 ×2 (video): the dot-to-dot subset lit first, the rest followed on fan-back —
  // not atomic. The local band now approximates the kernel's segment: nearest prompt dot at-or-above
  // (.dot.user / .dot.romp) down to the next prompt dot below, clamped to the transcript's last dot on
  // the live tail; a window cut with no prompt rendered falls back to the nearest-dot span.
  // Since 2026-07-23 that span is computed by railBandEdges, which the FAN-BACK uses too — the half of
  // this fix that was missing, and why the highlight still shrank a beat after landing.
  assert.match(SRC, /railPromptDotAbove\(first, hostR\) \?\? railFirstDotIn\(first, hostR\)/);
  assert.match(SRC, /railPromptDotBelow\(last, hostR\) \?\? railLastDotFrom\(last, hostR\)/);
  assert.match(SRC, /\.dot\.user, \.dot\.romp/, "prompt dots = human/answered-ask (user) + injected (romp)");
  // entering a target swaps the highlight atomically: any previous fan-back band + rings drop in the
  // same frame the new local band paints
  const ilb = SRC.slice(SRC.indexOf("function instantLocalBand"), SRC.indexOf("function clearLocalBand"));
  assert.match(ilb, /querySelectorAll\("\.rail-band"\)\.forEach\(\(n\) => n\.remove\(\)\);/);
  assert.match(ilb, /clearRailRings\(\);/);
});

test("the cross-surface clear is DEFERRED a beat and canceled by the next enter (no blank between glyphs)", () => {
  // the user 2026-07-17 ×2 (video): moving along the rail blanked the whole segment highlight between
  // adjacent glyphs then rebuilt it — large→small→large flicker. mouseleave now schedules the clear;
  // the next mouseenter cancels it, so a glyph→glyph handoff never passes through empty.
  assert.match(SRC, /function scheduleHoverClear\(\): void/);
  assert.match(SRC, /function cancelHoverClear\(\): void/);
  assert.match(SRC, /hoverClearTimer = setTimeout\(\(\) => \{ hoverClearTimer = undefined; vscodeApi\?\.postMessage\(\{ type: "dotHover" \}\); \}, 60\);/);
  const fn = SRC.slice(SRC.indexOf("function wireTurnHover"), SRC.indexOf("function applyGlow"));
  assert.equal((fn.match(/scheduleHoverClear\(\);/g) || []).length, 2, "both mouseleave handlers defer the clear");
  assert.doesNotMatch(fn, /addEventListener\("mouseleave"[\s\S]{0,200}?postMessage\(\{ type: "dotHover" \}\)/, "no immediate clear remains in the leave handlers");
});

test("one hover language: thicken/expand IN OWN COLOR — no white rings anywhere on the rail", () => {
  // the user 2026-07-17 (superseding the white-ring capsule): a hovered/lit dot EXPANDS with its own
  // background color; the band is the rail itself THICKENED in the rail color. No white box-shadows.
  assert.match(CSS, /\.dot\.dot-nav:hover \{ transform: scale\(1\.45\); z-index: 3; \}/);
  assert.match(CSS, /\.dot\.rail-ring \{ transform: scale\(1\.45\); z-index: 3; \}/);
  assert.match(CSS, /\.turn\.ext-glow \.dot \{ transform: scale\(1\.45\); z-index: 3; \}/);
  assert.match(CSS, /\.rail-band \{[^}]*background: var\(--active-accent, var\(--rail\)\)/);
  assert.doesNotMatch(CSS, /\.dot\.dot-nav:hover \{[^}]*box-shadow/);
  assert.doesNotMatch(CSS, /\.dot\.rail-ring \{[^}]*box-shadow/);
  assert.doesNotMatch(CSS, /\.rail-band \{[^}]*box-shadow/);
});

test("every band edge lands ON A DOT — and ONLY between dots (no lineless glow)", () => {
  // the user 2026-07-02 ×3 + 2026-07-03: box-boundary edges read as arbitrary cuts, and the box-bottom
  // fallback painted glow over the LINELESS stub after the last event. The band now exists solely
  // between dots: no bounding dot → clamp to the run's own dots; none at all → no band.
  assert.match(SRC, /function railDotAbove\(turn: HTMLElement, hostR: DOMRect\)/);
  assert.match(SRC, /function railDotBelow\(turn: HTMLElement, hostR: DOMRect\)/);
  const fn = SRC.slice(SRC.indexOf("function paintRailBand"), SRC.indexOf("function paintGlowRuler"));
  assert.match(fn, /const e = railBandEdges\(first, last, hostR\);/, "edges via the shared rule");
  assert.match(fn, /if \(!e\) continue;/, "no dots → no band, never a box edge");
  const edges = SRC.slice(SRC.indexOf("function railBandEdges"), SRC.indexOf("return { top, bottom };"));
  assert.match(edges, /railFirstDotIn\(first, hostR\)/, "dot-anchored fallback at the window's head");
  assert.match(edges, /railLastDotFrom\(last, hostR\) \?\? railDotAbove\(last, hostR\)/, "...and at its tail");
  assert.match(SRC, /paintRailBand\(\);\s*\/\/ one continuous measured band/, "painted with every glow application");
  assert.match(CSS, /\.rail-band \{ position: absolute; width: 4px;/, "the thickened-rail band (4px vs the rail's 2px)");
  assert.match(CSS, /\.rail-band \{[^}]*pointer-events: none/, "the band never intercepts the strip's hover");
  assert.doesNotMatch(CSS, /\.turn\.ext-glow::before|\.turn\.rail-glow::before/, "no per-turn slice glows remain");
});

test("band runs still break at each dot, and the dot GROWS to take over the joint", () => {
  // geometry kept from the capsule era (the user 2026-07-03: nothing may cross through a dot): runs stop
  // RAIL_DOT_CLEAR short of every dot in range; the dot, scaled 1.45, covers the clearance gap so the
  // thick line reads continuous into the grown disc.
  assert.match(SRC, /const RAIL_DOT_CLEAR = 7;/);
  assert.match(SRC, /function railDotsBetween\(host: HTMLElement, hostR: DOMRect, top: number, bottom: number\)/);
  assert.match(SRC, /const stops = \[top, \.\.\.dots\.map\(\(d\) => d\.y\), bottom\];/);
  assert.match(SRC, /d\.el\.classList\.add\("rail-ring"\);/, "every dot along the band grows via .rail-ring");
});

test("the hover strip exists only where the line does (the last turn's 16px stub)", () => {
  // "no .turn after me", never :last-child — appended .rail-band siblings broke that match mid-hover,
  // snapping a full-height line over the final text (the user 2026-07-03)
  assert.match(CSS, /\.turn:not\(:has\(~ \.turn\)\) \.rail-hit \{ bottom: auto; height: 16px; \}/);
  assert.match(CSS, /\.turn:not\(:has\(~ \.turn\)\)::before \{ bottom: auto; height: 16px; \}/);
  assert.doesNotMatch(CSS, /\.turn:last-child::before/, "the fragile :last-child stub is gone");
  // local hover past the last dot draws nothing — there is no complete inter-dot span there
  // the guard lives in railBandEdges now, so BOTH bands honour it rather than only the local one
  assert.match(SRC, /if \(top == null \|\| bottom == null \|\| bottom <= top\) return null;/);
  const ilb2 = SRC.slice(SRC.indexOf("function instantLocalBand"), SRC.indexOf("function clearLocalBand"));
  assert.match(ilb2, /if \(e\) drawRailBand\(host, hostR, turn, e\.top, e\.bottom, true\);/);
});

test("the strip hugs the line and never steals the dot's hover", () => {
  assert.match(CSS, /\.rail-hit \{ position: absolute; left: 7px; top: 0; bottom: 0; width: 9px; cursor: pointer; z-index: 0; \}/);
  assert.match(CSS, /\.dot\.dot-nav \{ cursor: pointer; z-index: 1; \}/, "the dot stacks above the strip");
});

// --- the band lands ONCE: local paint and fan-back must agree (the user 2026-07-23) ----------------
// Hovering the rail lit a whole segment and then visibly gave part of it back a moment later. The two
// paints computed their edges differently: the instant local one walked to the bounding PROMPT dots, the
// kernel's fan-back to the nearest dot of ANY kind around the turns it had glowed. That answer is never
// larger and usually smaller, so the second paint shrank the first. One shared rule is the fix.

test("both bands take their edges from railBandEdges, so neither can shrink the other", () => {
  const local = SRC.slice(SRC.indexOf("function instantLocalBand"), SRC.indexOf("function clearLocalBand"));
  assert.match(local, /const e = railBandEdges\(turn, turn, hostR\);/, "the instant local paint");
  const fan = SRC.slice(SRC.indexOf("function paintRailBand"), SRC.indexOf("// ---- overview ruler"));
  assert.match(fan, /const e = railBandEdges\(first, last, hostR\);/, "the kernel's fan-back");
  // and neither keeps a private edge calculation that could drift back apart
  assert.doesNotMatch(fan, /const top = railDotAbove\(first, hostR\)/, "the old any-dot walk is gone");
  assert.doesNotMatch(local, /const top = railPromptDotAbove\(turn, hostR\)/, "the old local walk is gone");
});

test("railBandEdges prefers PROMPT dots, since a segment is a prompt-to-prompt unit", () => {
  const fn = SRC.slice(SRC.indexOf("function railBandEdges"), SRC.indexOf("return { top, bottom };"));
  assert.match(fn, /railPromptDotAbove\(first, hostR\) \?\? railFirstDotIn/, "prompt first, then the window head");
  assert.match(fn, /railPromptDotBelow\(last, hostR\) \?\? railLastDotFrom/, "prompt first, then the window tail");
  // still dot-anchored at the ends: a band may never run past the last dot into the stubbed tail
  assert.match(fn, /railLastDotFrom\(last, hostR\)/);
  assert.match(fn, /if \(top == null \|\| bottom == null \|\| bottom <= top\) return null;/, "no inverted band");
});

// --- EVERY fallback must be invariant in its argument, or the rule is shared in name only -----------
// The two callers pass different turns for the same hover: the local paint knows only the turn under the
// pointer, the fan-back knows the whole glowed run. A walk measured RELATIVE to its argument therefore
// gives them different answers and the flicker returns. On the live tail, where no prompt dot follows,
// the bottom used to fall through railDotBelow — "the next dot below" — so the band landed short and grew
// a tick later (the user 2026-07-23, second recording).

test("no fallback in railBandEdges is measured relative to its own argument", () => {
  const fn = SRC.slice(SRC.indexOf("function railBandEdges"), SRC.indexOf("return { top, bottom };"));
  // railDotBelow(first) and railDotBelow(last) both answer "the next dot from HERE", which differs per
  // caller. Neither may appear; the fixed-point walks (railFirstDotIn / railLastDotFrom) replace them.
  assert.doesNotMatch(fn, /railDotBelow\(/, "a relative downward walk would reintroduce the tail flicker");
  assert.doesNotMatch(fn, /railDotAbove\(first, hostR\)/, "...and a relative upward walk, the head one");
});

test("railFirstDotIn is a fixed point: the window's first dot, not a walk from the turn", () => {
  const i = SRC.indexOf("function railFirstDotIn");
  const fn = SRC.slice(i, SRC.indexOf("\n}", i));
  assert.match(fn, /host\.querySelector<HTMLElement>\("\.turn \.dot"\)/, "scans the host from the top");
  assert.doesNotMatch(fn, /previousElementSibling|nextElementSibling/, "no sibling walk — that is what varies");
});

test("railLastDotFrom is the transcript's FINAL dot, so both callers land on it on the live tail", () => {
  const j = SRC.indexOf("function railLastDotFrom");
  const fn = SRC.slice(j, SRC.indexOf("\n}", j));
  // it keeps assigning as it walks and returns the LAST hit, so any starting turn in the tail agrees
  assert.match(fn, /for \(let n: Element \| null = turn; n; n = n\.nextElementSibling\)/);
  assert.match(fn, /y = r\.top \+ r\.height \/ 2 - hostR\.top;/, "assigns, never returns early");
  assert.match(fn, /return y;/);
});

// --- the LINE clicks like its dots (the user 2026-07-23) -------------------------------------------
// It already hovered like them and already wore cursor:pointer, so a click that did nothing read as
// broken rather than as read-only.

test("the rail strip navigates on click with the same payload as the dot", () => {
  const fn = SRC.slice(SRC.indexOf("function wireTurnHover"), SRC.indexOf("function applyGlow"));
  const clicks = fn.match(/postMessage\(\{ type: "dotOpen", sid: activeId, uuid, t, tlId \}\)/g) || [];
  assert.equal(clicks.length, 2, "the dot AND the line both open the same target");
  // both stop propagation so the click never doubles as a turn-body click
  const railBlock = fn.slice(fn.indexOf('el("div", "rail-hit")'));
  assert.match(railBlock, /addEventListener\("click", \(e\) => \{\s*\n\s*e\.stopPropagation\(\);/);
  assert.match(railBlock, /rail\.title = "click: jump to this on the timeline \+ feed · hover: highlight there";/,
    "the tooltip promises the click it now honours");
});

test("a romp-injected turn's rail dot wears the swirl, as the timeline draws it", () => {
  // one kind of event should look like itself on both surfaces; it was an anonymous gray dot before
  // 2026-09-08 (the notice-vocabulary pass): tokenised — the page's own ground + ink, so the light theme draws it
  assert.match(CSS, /\.dot\.romp \{ background: var\(--bg\); border: 1px solid var\(--fg\); \}/,
    "the ring is the page's ink; the timeline draws its own pale ring on its dark ground");
  assert.match(CSS, /\.dot\.romp::before \{[\s\S]*?background: url\(\.\.\/media\/romp-swirl-glyph\.svg\) center \/ contain no-repeat;/);
  assert.doesNotMatch(CSS, /\.dot\.romp \{ background: var\(--dim\)/, "the anonymous gray dot is gone");
  // relative, not absolute: an absolute /media 404s in the VS Code webview's synthetic origin
  assert.doesNotMatch(CSS, /\.dot\.romp::before \{[^}]*url\(\/media/);
});
