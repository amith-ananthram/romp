// The ruler's history strip (T318b, the user 2026-09-10): a hovered feed card whose source turns sit outside the
// chat's resident tail used to light nothing (applyGlow paints rendered rows only, and the ruler maps the resident
// scroll height). Now the glow group carries each uuid's global event index and the pane marks the unloaded turns
// in a strip at the top of the ruler. The pure half executes for real here; the DOM half is pinned to source, as
// the other webview tests do (render.ts has import-time DOM side effects).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { historyMarks, historyBands, HIST_H, HIST_GAP, HIST_MIN_H } from "./glow-history";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a hover on turns outside the resident tail marks them on the strip by global index, and lights no row", () => {
  // the chat holds [400, 650) of 650 events; the card's source turns are events 100, 101 and 380 — none resident
  const idx = { u100: 100, u101: 101, u380: 380 };
  const lit = new Set<string>();                   // applyGlow found no .turn for any of them
  const marks = historyMarks(["u100", "u101", "u380"], idx, lit, 400);
  assert.deepEqual(marks, [0.25, 0.2525, 0.95]);
  assert.equal(lit.size, 0, "nothing lit: the rows are not on the page to light");
});

test("a resident turn is the ruler proper's, never the strip's; a lit row and an unknown uuid stay off it too", () => {
  const idx = { u100: 100, u450: 450, u500: 500 };
  const lit = new Set(["u500"]);                    // its row is on the page and already glows
  assert.deepEqual(historyMarks(["u100", "u450", "u500", "u999"], idx, lit, 400), [0.25],
    "u450 is resident (at or past headFrom) even without a lit row: folded or off the active path, not unloaded");
  assert.deepEqual(historyMarks(["u100"], idx, lit, 0), [], "headFrom 0: the whole transcript is resident, no strip");
  assert.deepEqual(historyMarks(["u100"], undefined, lit, 400), [], "a group without positions (no built payload) marks nothing");
});

test("marks are sorted, touching marks coalesce into one band, and the last mark stays inside the strip", () => {
  assert.deepEqual(historyMarks(["b", "a"], { a: 10, b: 200 }, new Set(), 400), [0.025, 0.5]);
  const bands = historyBands([0.25, 0.2525, 0.95], HIST_H);
  assert.equal(bands.length, 2, "the two adjacent turns are one band, the far one another");
  assert.equal(bands[0].top, 0.25 * HIST_H);
  assert.ok(bands[0].height >= HIST_MIN_H && bands[0].height < 2 * HIST_MIN_H);
  assert.equal(bands[1].top, Math.min(0.95 * HIST_H, HIST_H - HIST_MIN_H), "clamped so the band ends inside the strip");
  assert.deepEqual(historyBands([1], HIST_H), [{ top: HIST_H - HIST_MIN_H, height: HIST_MIN_H }]);
  assert.deepEqual(historyBands([], HIST_H), []);
});

test("applyGlow records which uuids lit a row and hands the rest to the strip, for the active view only", () => {
  // the lit set is filled inside the ONE query that adds .ext-glow, so a uuid with no rendered row can never glow
  assert.match(RENDER, /const lit = new Set<string>\(\);\s*v\.el\.querySelectorAll<HTMLElement>\("\.turn\[data-uuid\]"\)\.forEach\(\(n\) => \{\s*const u = n\.dataset\.uuid \|\| "";\s*if \(uset\.has\(u\)\) \{ n\.classList\.add\("ext-glow"\); lit\.add\(u\); \}/);
  assert.match(RENDER, /if \(g\.sid === activeId\) glowHistory = historyMarks\(g\.uuids \|\| \[\], g\.idx, lit, liveSession\(g\.sid\)\?\.headFrom \?\? 0\);/);
  assert.match(RENDER, /glowHistory = \[\];\s*const midSet = new Set\(mids\);/, "a fresh hover (or a clear) starts with no history marks");
  assert.match(RENDER, /import \{ historyMarks, historyBands, HIST_H, HIST_GAP \} from "\.\/glow-history";/);
});

test("the ruler shows for history marks alone, caps its top with the strip, and maps the resident scroll below it", () => {
  assert.match(RENDER, /const hist = \(content && v\) \? glowHistory : \[\];/);
  assert.match(RENDER, /if \(!content \|\| \(!glows\.length && !hist\.length\)\) \{ ruler\.style\.display = "none"; ruler\.replaceChildren\(\); return; \}/);
  assert.match(RENDER, /const capH = hist\.length \? HIST_H \+ HIST_GAP : 0;/);
  assert.match(RENDER, /const mapH = Math\.max\(1, rulerH - capH\);/);
  assert.match(RENDER, /band\.style\.top = \(capH \+ b\.top \/ scrollH \* mapH\) \+ "px";/);
  assert.match(RENDER, /const strip = el\("div", "glow-ruler-hist"\);[\s\S]*?for \(const hb of historyBands\(hist, HIST_H\)\) \{[\s\S]*?band\.classList\.add\("hist"\);[\s\S]*?strip\.appendChild\(band\);[\s\S]*?ruler\.appendChild\(strip\);/);
  assert.equal(HIST_GAP, 2);
});

test("the strip wears the same ink as the bands, with a hairline under it, and stays a pure indicator", () => {
  assert.match(CSS, /\.glow-ruler-hist \{[^}]*position: absolute;[^}]*top: 0;[^}]*border-bottom: 1px solid color-mix\(in srgb, var\(--active-accent, #fff\) 35%, transparent\)/);
  assert.match(CSS, /\.glow-ruler-band\.hist \{[^}]*border-radius: 2px/);
  assert.match(CSS, /\.glow-ruler \{[^}]*pointer-events: none/, "the strip inherits it: no click is swallowed over the scrollbar");
});

test("a click on the card still lands on an unloaded turn: the reveal fetches older chunks until the uuid is resident", () => {
  assert.match(RENDER, /if \(fetchOlderForAnchor\(activeId, uuid\) \|\| loadingOlder\.has\(activeId\)\) \{/);
  assert.match(RENDER, /pendingAnchor = uuid; anchorPendingOlder = true; landTrail\.push\("pointer-fetch-older"\); return false;/);
});
