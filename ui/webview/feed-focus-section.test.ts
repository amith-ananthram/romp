// The FOCUSED SESSION section (T347, the user 2026-09-11, who wanted the focused session's cards on top): when a
// tab has focus in the chat pane, the feed shows that session's cards ABOVE a divider — the board's three columns,
// a miniature of the feed for one session, headed by its name — while the board below stays exactly as it is.
// OFF by default; the View menu's fourth row switches it; the kernel relays the chat's active tab to the feed as
// {type:"activeChat", id}. This file pins the wiring in feed.ts and feed.css at the source (feed.ts has no jsdom
// harness, the repo convention); the pure pick (feed-focus.ts) runs directly in feed-focus-entries.test.ts, and the
// switch's persistence (FeedViewState.focused) in feed-view-state.test.ts. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

// ── the switch: the View menu's fourth row ───────────────────────────────────────────────────────────
test("the fourth row reads 'Show focused session', a ✓ row after Group by session, and flips the persisted switch", () => {
  const groupAt = FEED.indexOf('set(2, "Group by session');
  const focusAt = FEED.indexOf('set(3, "Show focused session"');
  assert.ok(groupAt > 0 && focusAt > groupAt, "fourth, after the grouping row");
  assert.match(FEED, /if \(rows\.length !== 4\) return;/, "paintViewMenu syncs four rows");
  assert.match(FEED, /mk\(true, \(\) => \{ showFocused = !showFocused; persistViewState\(\); render\(\); \}\);/,
    "a menuitemcheckbox row (mk(true)) that flips the switch, persists and re-renders — no shared pref");
  assert.match(FEED, /set\(3, "Show focused session", \{\s*\n\s*current: showFocused,/);
  assert.doesNotMatch(FEED, /setViewPref\("focused"/, "never romp:settings: the gear and the other panes have nothing to read");
});

test("the switch is the feed's own view state under `focused`: OFF unless a blob saved it on", () => {
  assert.match(FEED, /let showFocused = false;/);
  assert.match(FEED, /showFocused = st\.focused;/, "hydrated with the rest of the view state");
  assert.match(FEED, /order: colOrder\.slice\(\), focused: showFocused \};/, "currentViewState carries it, so persistViewState writes it");
});

// ── the event: the kernel's activeChat frame ─────────────────────────────────────────────────────────
test("the activeChat frame sets the focused sid and re-renders; nothing in the section runs on a clock", () => {
  const at = FEED.indexOf('} else if (m.type === "activeChat") {');
  assert.ok(at > 0, "the feed handles the kernel's relay of the chat pane's active tab");
  const branch = FEED.slice(at, FEED.indexOf('} else if (m.type === "hoverCards") {', at));
  assert.match(branch, /focusedSid = typeof m\.id === "string" && m\.id \? m\.id : null;/, "the frame's id, or null when no tab has focus");
  assert.match(branch, /if \(!showFocused\) return;\s*\n\s*if \(freezeKey \|\| tabScopeKey\) \{ focusStale = true; return; \}\s*\n\s*render\(\);/,
    "off: nothing to paint; held under the pointer: paint on the release; else the frame IS the render");
  assert.doesNotMatch(branch, /setTimeout|setInterval|requestAnimationFrame/);
  const section = FEED.slice(FEED.indexOf("function ensureFocusSection("), FEED.indexOf("// ── FLIP: animate a card FLYING"));
  assert.ok(section.includes("function removeFocusSection(): void {"), "the slice spans the whole section code");
  assert.doesNotMatch(section, /setTimeout|setInterval|requestAnimationFrame|Date\.now/, "event-based: no timer, no clock");
});

test("a tab switch that lands while a card is held under the pointer paints on the release (the hover-freeze contract)", () => {
  assert.match(FEED, /let focusStale = false;/);
  const flush = FEED.slice(FEED.indexOf("function flushFreeze(): void {"), FEED.indexOf('window.addEventListener("blur", () => { releaseTabScope();'));
  assert.match(flush, /if \(m\) applyFeedPayload\(m\);[^\n]*\n\s*else if \(focusStale\) render\(\);[^\n]*\n\s*focusStale = false;/,
    "the release renders the section when no payload was queued; a queued payload's render covers it");
});

// ── the section: its own elements, above the board, the board untouched ─────────────────────────────
test("#feed-focus sits directly before #feed-cols: head, empty line, the three columns, the rule", () => {
  assert.match(FEED, /sec\.id = "feed-focus";/);
  assert.match(FEED, /if \(board && sec\.nextSibling !== board\) \{ list\.insertBefore\(sec, board\); applyColStack\(\); \}/,
    "ensure-once, kept right above #feed-cols across renders; a fresh section takes the board's column order");
  for (const mint of ['el("div", "feed-focus-head")', 'el("a", "fname")', 'el("span", "feed-focus-cap")', 'el("div", "feed-focus-empty")',
                      'el("div", "feed-cols feed-focus-cols")', 'el("hr", "feed-focus-divider")']) {
    assert.ok(FEED.includes(mint), "builds " + mint);
  }
  assert.match(FEED, /el\("span", "feed-col-name fcol-chip fcol-chip-" \+ chip\); name\.textContent = label;\s*\n\s*const count = el\("span", "feed-col-count"\);\s*\n\s*h\.append\(name, count\);/,
    "the board's chips and count, NO fold caret and NO drag on the section's heads");
  // in render(): the pick is taken before grouping (a folded thread below must not empty the section), the
  // section is painted before the board's reconcile, and the board's own reconcile is what it always was
  const pickAt = FEED.indexOf("const focusBuckets = showFocused ? focusedEntries(buckets, focusedSid, entrySid) : null;");
  const groupAt = FEED.indexOf("if (feedPrefs().grouped) {", pickAt);
  const callAt = FEED.indexOf("if (focusBuckets) renderFocusSection(list, focusBuckets, gate); else removeFocusSection();");
  const flipAt = FEED.indexOf("const flipFirst = needFlip ? captureCardRects(cols) : new Map<string, FlipState>();");
  const boardAt = FEED.indexOf("reconcileCol(cols.asks, buckets.asks, desired, gate);");
  assert.ok(pickAt > 0 && groupAt > pickAt && callAt > groupAt && flipAt > callAt && boardAt > flipAt,
    "pick → grouping → section → the board's FLIP capture → the board's reconcile");
  // the section sits ABOVE the board, so it must have settled before the board's First rects are read: a capture
  // taken before it would have every card below glide by the section's height change on top of its own move
  assert.ok(FEED.indexOf("const gate: GateEnv = {") < flipAt, "the gate the section needs is built before the capture");
});

test("the section's cards are SECOND elements: its own caches under 'f:' keys, the board's caches untouched", () => {
  const sec = FEED.slice(FEED.indexOf("function ensureFocusSection("), FEED.indexOf("// ── FLIP: animate a card FLYING"));
  assert.match(sec, /key = "f:a:" \+ e\.ask\.itemId;/);
  assert.match(sec, /key = "f:g:" \+ e\.group\.turnId;/);
  assert.match(sec, /card = fsAskEls\.get\(e\.ask\.itemId\) \|\| makeAskCard\(e\.ask\);\s*\n\s*card\.dataset\.key = key;/, "the builder's bare key is re-stamped with the section's");
  assert.match(sec, /card = fsGroupEls\.get\(e\.group\.turnId\) \|\| makeGroupCard\(e\.group\);\s*\n\s*card\.dataset\.key = key;/);
  assert.doesNotMatch(sec, /\baskEls\b|\bgroupEls\b/, "never the board's caches — no card below moves because of the section");
  // the same update gate as the board (feed-card-gate.ts)
  assert.match(sec, /const ik = cardInputsKey\(e\.ask, gate\);\s*\n\s*if \(cardNeedsUpdate\(card as any, e\.ask, ik\)\) \{ updateAskCard\(card, e\.ask\); \(card as any\)\._ik = ik; \}/);
  assert.match(sec, /function removeFocusSection\(\): void \{\s*\n\s*document\.getElementById\("feed-focus"\)\?\.remove\(\);\s*\n\s*fsAskEls\.clear\(\); fsGroupEls\.clear\(\);/);
  // both copies of a card light together, hold the same latches, and tick the same ages
  assert.match(FEED, /for \(const \[id, card\] of fsAskEls\) card\.classList\.toggle\("focused", id === eff\);/);
  assert.equal((FEED.match(/for \(const card of \[\.\.\.askEls\.values\(\), \.\.\.fsAskEls\.values\(\)\]\)/g) || []).length, 2, "rearmLatches and livePass walk both");
});

test("the board's own column lookups are scoped to #feed-cols now that the section carries the same classes", () => {
  assert.match(FEED, /document\.querySelector<HTMLElement>\("#feed-cols \.feed-col\.col-" \+ key\)/, "applyColStack folds the board's column");
  assert.match(FEED, /const twin = document\.querySelector<HTMLElement>\("#feed-focus \.feed-col\.col-" \+ key\);/, "…and writes the dragged order to the section's twin column too");
  assert.match(FEED, /else col\.style\.removeProperty\("--col-order"\);/, "the board's own statement stands as feed-col-fold.test.ts pins it");
  assert.match(FEED, /document\.querySelector<HTMLElement>\("#feed-cols \.feed-col\.col-" \+ k\)/, "the drag's FLIP reads the board");
  assert.match(FEED, /document\.querySelector<HTMLElement>\("#feed-cols \.feed-col\.col-" \+ other\)/);
  assert.match(FEED, /put\(document\.querySelector\("#feed-cols \.feed-col\.col-" \+ key \+ " \.feed-col-head"\), d\.cols\[key\]\);/, "the freeze badges land on the board's heads");
  assert.doesNotMatch(FEED, /querySelector<HTMLElement>\("\.feed-col\.col-"/, "no bare column query survives to land on the section's copy first");
  // a copy's key reads as the card's own identity for the hover-freeze heal (a hovered copy holds the gate)
  assert.match(FEED, /if \(key\.startsWith\("f:"\)\) key = key\.slice\(2\);/);
  // …and the keyboard cursor walks the BOARD's cards: a walk over both would interleave a copy with its card below
  assert.match(FEED, /querySelectorAll<HTMLElement>\("#feed-cols \.fitem:not\(\.dismissing\)"\)/, "kbCardEls reads #feed-cols");
  assert.doesNotMatch(FEED, /querySelectorAll<HTMLElement>\("\.feed-cols \.fitem:not\(\.dismissing\)"\)/);
});

test("the head: the session's dot and name, its identity colour, opening the session like a run header does", () => {
  assert.match(FEED, /nm\.replaceChildren\(\.\.\.hostNameNodes\(who\.name, sid\)\)/, "a remote session's host: prefix stays quiet metadata");
  assert.match(FEED, /nm\.style\.color = who\.color \? who\.color\.bg : "";/);
  assert.match(FEED, /nm\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openOrReviveSession\(sid, who\.live, who\.name\); \};/);
  assert.match(FEED, /setWorkDot\(nm, dotFor\(who\.name\)\);/);
  assert.match(FEED, /const s = sessionsMeta\.find\(\(x\) => x\.sid === sid\);\s*\n\s*const a = asks\.find\(\(x\) => x\.sid === sid\);/,
    "the name from this pane's session list, else a card, else the kernel's stub");
});

test("the two quiet states, only while the switch is on: no tab focused; a focused session with no cards", () => {
  assert.match(FEED, /setText\(empty, "No session is focused in the chat"\);/);
  assert.match(FEED, /setText\(empty, who\.name \+ " has no cards"\);/);
  assert.match(FEED, /head\.style\.display = sid \? "" : "none";\s*\n\s*empty\.style\.display = sid && total \? "none" : "";\s*\n\s*cols\.style\.display = total \? "" : "none";/,
    "no sid: the line stands in for the head; no cards: head and line; the rule stays in both");
  assert.match(FEED, /removeFocusSection\(\);   \/\/ an empty board is the wordmark alone/, "an empty board shows the wordmark, not the section");
});

// ── feed.css: the section's rules, through the variables ─────────────────────────────────────────────
test("feed.css: #feed-focus, the head, the caption, the rule and the empty line exist, var() only", () => {
  assert.match(CSS, /#feed-focus \{ display: flex; flex-direction: column; gap: 8px; \}/);
  assert.match(CSS, /\.feed-focus-head \{[^}]*font-weight: 600;[^}]*\}/, "the session headers' weight");
  assert.match(CSS, /\.feed-focus-head \.fname \{ font-size: inherit; \}/, "the name at the card titles' size, as .feed-sess-head does");
  assert.match(CSS, /\.feed-focus-cap \{[^}]*font-size: 0\.72em;[^}]*color: var\(--dim\);[^}]*\}/, "the column head's 0.72em, dim — no new size");
  assert.match(CSS, /\.feed-focus-cols \.feed-col-head \.fcol-chip \{ cursor: default; \}/, "no grab cursor where nothing drags");
  assert.match(CSS, /\.feed-focus-divider \{ border: 0; border-top: 1px solid var\(--menu-border, rgba\(255, 255, 255, 0\.12\)\); margin: 6px 0 2px; \}/);
  assert.match(CSS, /\.feed-focus-empty \{ color: var\(--dim\); font-size: 0\.82em; \}/);
  // every colour in the section's declarations is a var(); the one literal is that var()'s fallback
  const block = CSS.slice(CSS.indexOf("#feed-focus {"), CSS.indexOf(".feed-focus-empty {") + ".feed-focus-empty { color: var(--dim); font-size: 0.82em; }".length);
  const decls = (block.match(/\{[^}]*\}/g) || []).join("\n").replace(/var\([^)]*\)/g, "");
  assert.doesNotMatch(decls, /#[0-9a-fA-F]{3,8}\b|rgba?\(/, "no hex or rgb outside a var() fallback");
  assert.ok(/--menu-border\s*:/.test(CSS) && /--dim\s*:/.test(CSS), "the variables it reads are defined in this sheet (both themes)");
});
