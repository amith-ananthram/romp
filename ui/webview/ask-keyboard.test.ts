// Keyboard nav for the live AskUserQuestion picker (the user 2026-06-22): ↑/↓ STEP through options and the
// preview FOLLOWS (so you can view each one), Enter selects — like the terminal; and multi-select is fully
// arrow-drivable (↑/↓ move, Space toggles, Enter submits), not click-only. Source-level pins (no jsdom).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const TYPES = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "ask-types.ts"), "utf8");

test("an option can carry its own preview (the SDK backend sends one per option)", () => {
  assert.match(TYPES, /preview\?: string;/);
});

test("single-select ↑/↓ steps the preview to the focused option instantly: every option carries its own preview", () => {
  // paint moves the highlight AND re-renders the preview for the new focus
  assert.match(RENDER, /function paintLiveAskFocus\(\) \{[\s\S]*?renderAskPreview\(\);/);
  // no cursor nudge round trip (T331): the terminal scrape that needed one is gone, and nothing posts navAsk
  assert.doesNotMatch(RENDER, /navLiveAsk|navAsk|navTimer/, "no cursor-driven preview path");
  assert.doesNotMatch(RENDER, /answer it in the terminal|renderUnknownCard/, "no unreadable-screen card: the kernel pushes a typed ask or clears");
  // Enter still confirms; ↑/↓ never select
  assert.match(RENDER, /e\.key === "Enter"[^}]*answerLiveAsk\(opts\[liveAskFocus\]\.n\)/);
});

test("multi-select keyboard: ↑/↓ walk checkboxes + Submit/Cancel; Enter TOGGLES (the user 2026-06-27)", () => {
  // the card grabs keyboard focus + a keydown handler, like the single card
  assert.match(RENDER, /card\.addEventListener\("keydown", onMultiKey\);/);
  assert.match(RENDER, /function onMultiKey\(e: KeyboardEvent\)/);
  // arrows walk a COMBINED list = checkboxes + Submit + Cancel
  assert.match(RENDER, /const navCount = n \+ 2;/);
  assert.match(RENDER, /e\.key === "ArrowDown"[^}]*% navCount[^}]*paintMultiFocus\(\)/);
  // Space toggles the focused checkbox (only when the highlight is on one)
  assert.match(RENDER, /\(e\.key === " " \|\| e\.key === "Spacebar"\)[^}]*if \(liveAskFocus < n\) toggleLiveAsk\(opts\[liveAskFocus\]\.n\)/);
  // Enter TOGGLES the focused checkbox — submit only when the highlight is ON the Submit button
  assert.match(RENDER, /if \(liveAskFocus < n\) toggleLiveAsk\(opts\[liveAskFocus\]\.n\);\s*else if \(liveAskFocus === n\) submitLiveAsk\(\);\s*else cancelLiveAsk\(\);/);
  // the focused checkbox row AND the Submit/Cancel buttons carry the .focus highlight
  assert.match(RENDER, /"ask-check" \+ \(i === liveAskFocus \? " focus" : ""\)/);
  assert.match(RENDER, /btns\.forEach\(\(b, j\) => b\.classList\.toggle\("focus", nC \+ j === liveAskFocus\)\)/);
  assert.match(CSS, /\.ask-check\.focus \{[^}]*box-shadow: inset 2px 0 0/);
  assert.match(CSS, /\.ask-btn\.focus \{ outline: 2px solid var\(--accent\)/);
});

// The picker no longer TAKES OVER the message box (the user 2026-07-09): the footer (working chip /
// interrupt / model selector + composer) stays visible, the inline "add your own" field is gone, and the
// NORMAL composer doubles as that field.
test("a live picker keeps the footer/controls visible — it no longer hides the message box", () => {
  // renderLiveAsk always shows the footer now (no `footer.style.display = "none"`)
  assert.match(RENDER, /if \(footer\) footer\.style\.display = "";/);
  assert.doesNotMatch(RENDER, /footer\.style\.display = "none"/);
});

test("the inline custom-answer INPUT is gone — a static hint points at the message box instead", () => {
  // no more inline <input class="ask-custom-input"> built in the cards
  assert.doesNotMatch(RENDER, /className = "ask-custom-input"/);
  // both single + multi cards append the hint row when a Type-something slot exists
  assert.match(RENDER, /if \(ask\.options\.some\(\(o\) => isTypeSomething\(o\.label\)\)\) card\.appendChild\(customHintRow\(\)\);/);
  assert.match(RENDER, /function customHintRow\(\): HTMLElement/);
  assert.match(RENDER, /type it in the message box below/);
});

test("a typed composer message routes to the active picker (custom fills Type-something, text answers a raw prompt)", () => {
  assert.match(RENDER, /function composerAnswersAsk\(\): "custom" \| "text" \| null/);
  // AskUserQuestion with a Type-something slot → "custom"; a raw unknown prompt → "text"; else null (normal send)
  assert.match(RENDER, /ask\.options\.some\(\(o\) => isTypeSomething\(o\.label\)\)\) return "custom";/);
  // sendComposer consults it FIRST and routes instead of sending a normal message
  assert.match(RENDER, /const askRoute = typed \? composerAnswersAsk\(\) : null;\s*\n\s*if \(askRoute\) \{\s*\n\s*if \(askRoute === "custom"\) addCustomLiveAsk\(typed\); else sendTextLiveAsk\(typed\);/);
});

test("the composer shows it's in answer mode + the card never steals focus from a typing composer", () => {
  // placeholder + tint while a free-text picker is up
  assert.match(RENDER, /ta\.placeholder = "add your own answer…  \(⏎ submit\)";\s*\n\s*ta\.classList\.add\("answering"\);/);
  assert.match(CSS, /#composer-input\.answering \{ border-color: var\(--accent\)/);
  // the card only takes focus when the user is NOT typing in an input/textarea (never yanks the caret mid-word)
  assert.match(RENDER, /function focusCardUnlessTyping\(card: HTMLElement\) \{\s*\n\s*if \(!isTypingTarget\(document\.activeElement\)\) card\.focus/);
  assert.match(RENDER, /card\.addEventListener\("keydown", onSingleKey\);\s*\n\s*focusCardUnlessTyping\(card\);/);
});
