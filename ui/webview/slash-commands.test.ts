// Slash-command autocomplete (the user 2026-06-29): typing "/" at the start of the composer opens a
// filterable, arrow-navigable menu of the session's slash commands (name + description + arg hint), sourced
// from the kernel's /commands (the Agent SDK's get_server_info — works for tmux + SDK alike). Enter/Tab/click
// FILLS "/name " so the user adds args then sends. Source-level pins (no jsdom for the chat renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("commands come from the kernel /commands endpoint, fetched per active session", () => {
  assert.match(RENDER, /interface SlashCmd \{ name: string; description\?: string; argumentHint\?: string; aliases\?: string\[\]; \}/);
  assert.match(RENDER, /fetch\(kernelUrl\("\/commands\?sid=" \+ encodeURIComponent\(sid\)\)/);
  // re-load when the active session changes; "" is the kernel-cwd fallback, distinct from the never-loaded null
  assert.match(RENDER, /let slashSid: string \| null = null;/);
  assert.match(RENDER, /const sid = activeId \|\| "";/);
  assert.match(RENDER, /if \(slashSid !== sid\) loadCmds\(sid, updateSlash\)/);
  // pre-warm the (slow) kernel probe on focus, before the user types "/"
  assert.match(RENDER, /ta\.addEventListener\("focus", \(\) => \{ if \(slashSid !== \(activeId \|\| ""\)\) loadCmds\(activeId \|\| ""\); \}\)/);
});

test("the menu is active ONLY while the box is a single leading \"/token\" (no space yet)", () => {
  assert.match(RENDER, /const slashQuery = \(\): string \| null => \(\/\^\\\/\\S\*\$\/\.test\(ta\.value\) \? ta\.value\.slice\(1\) : null\)/);
  // opening/refreshing happens from the composer's input handler
  assert.match(RENDER, /updateSlash\(\);\s*\/\/ open\/refresh\/close the slash-command menu/);
});

test("filtering ranks prefix over substring, across name + aliases", () => {
  assert.match(RENDER, /for \(const n of \[c\.name, \.\.\.\(c\.aliases \|\| \[\]\)\]\)/);
  assert.match(RENDER, /best = Math\.max\(best, ql === "" \? 0 : i === 0 \? 2 : i > 0 \? 1 : -1\)/);   // prefix>substring>miss
});

test("the menu OWNS ↑/↓/⏎/Tab/Esc while open, so they don't send / leave the box", () => {
  // the keydown handler consults slashKey FIRST and returns if it consumed the key
  assert.match(RENDER, /if \(slashKey\(e\)\) return;/);
  assert.match(RENDER, /if \(e\.key === "ArrowDown"\) \{ e\.preventDefault\(\); if \(items\.length\) \{ sel = \(sel \+ 1\) % items\.length/);
  assert.match(RENDER, /if \(e\.key === "ArrowUp"\)/);
  assert.match(RENDER, /if \(\(e\.key === "Enter" \|\| e\.key === "Tab"\) && items\.length\) \{ e\.preventDefault\(\); pickSlash\(items\[sel\]\); return true; \}/);
  assert.match(RENDER, /e\.preventDefault\(\); slashDismissed = true; closeSlash\(\); return true;/);   // Esc (list layer) dismisses
  // when the menu is closed, slashKey returns false so Enter still sends and Esc still leaves the box
  assert.match(RENDER, /const slashKey = \(e: KeyboardEvent\): boolean => \{\s*\n\s*if \(!pop\) return false;/);
});

test("Escape latches the menu DISMISSED — typing more of the same \"/token\" won't re-pop it; clearing the \"/\" re-arms (the user 2026-06-29)", () => {
  // Esc sets the latch; updateSlash refuses to reopen while latched
  assert.match(RENDER, /let slashDismissed = false;/);
  assert.match(RENDER, /if \(slashDismissed\) return;/);
  // the ONLY reset is the "/token" context going away (slashQuery → null): clear the "/" and start over
  assert.match(RENDER, /if \(q === null\) \{ slashDismissed = false; closeSlash\(\); return; \}/);
});

test("picking a command FILLS \"/name \" (does not send) so the user adds args + ⏎", () => {
  assert.match(RENDER, /const pickSlash = \(c: SlashCmd\) => \{\s*\n\s*ta\.value = "\/" \+ c\.name \+ " ";/);
  assert.match(RENDER, /ta\.setSelectionRange\(ta\.value\.length, ta\.value\.length\)/);
  // clicking a row picks it; mousedown (not click) keeps the textarea focused
  assert.match(RENDER, /row\.addEventListener\("mousedown", \(ev\) => \{ ev\.preventDefault\(\); pickSlash\(c\); \}\)/);
});

test("while the kernel warms its probe the menu shows the romp loader, not a blank/empty", () => {
  assert.match(RENDER, /if \(slashWarming && !slashCmds\.length\) \{/);
  assert.match(RENDER, /l\.className = "slash-loading"/);
  assert.match(RENDER, /s\.className = "slash-spin"/);
  // poll again while warming
  assert.match(RENDER, /slashPoll = window\.setTimeout\(\(\) => loadCmds\(sid, then\), 1500\)/);
});

test("the popup + selected-row accent + loader spin are styled", () => {
  assert.match(CSS, /\.slash-pop \{[\s\S]*?position: fixed/);
  assert.match(CSS, /\.slash-row\.sel \{ background: var\(--accent\); \}/);   // selected row = romp accent
  assert.match(CSS, /\.slash-spin \{[\s\S]*?url\(\.\.\/media\/romp-swirl-glyph\.svg\)/);
  assert.match(CSS, /@keyframes slash-spin \{ to \{ transform: rotate\(-360deg\); \} \}/);
  assert.match(CSS, /@media \(prefers-reduced-motion: reduce\) \{ \.slash-spin \{ animation: none; \} \}/);
});

test("rows are strictly ONE LINE: name capped + ellipsized, description ellipsized in the rest (the user 2026-08-13)", () => {
  // the first cut let the description wrap in place; .slash-name never shrank, so /code-review's long arg
  // hint squeezed the description to a one-letter-wide column hundreds of lines tall. The list is compact,
  // and the full text lives behind → instead.
  const name = CSS.match(/\.slash-name \{[\s\S]*?\}/)?.[0] ?? "";
  const desc = CSS.match(/\.slash-desc \{[\s\S]*?\}/)?.[0] ?? "";
  assert.match(name, /max-width: 68%/);
  assert.match(name, /text-overflow: ellipsis/);
  assert.match(desc, /white-space: nowrap/);
  assert.match(desc, /text-overflow: ellipsis/);
});

test("→ expands the selected row to its full wrapped text; ←/Esc return to the list (the user 2026-08-13)", () => {
  assert.match(RENDER, /let slashExpanded = false/);
  // → only with the caret at the END of the query, so arrow-editing the "/token" still works
  assert.match(RENDER, /e\.key === "ArrowRight" && items\.length && !slashExpanded\s*&& ta\.selectionStart === ta\.value\.length && ta\.selectionEnd === ta\.value\.length/);
  assert.match(RENDER, /if \(e\.key === "ArrowLeft" && slashExpanded\) \{ e\.preventDefault\(\); slashExpanded = false; paintSlash\(\); return true; \}/);
  // Esc peels one layer (full text → list → dismissed), and closing the menu resets the mode
  assert.match(RENDER, /if \(slashExpanded\) \{ e\.preventDefault\(\); slashExpanded = false; paintSlash\(\); return true; \}/);
  assert.match(RENDER, /slashExpanded = false; \};/);
  // the expanded row stacks name over description, both wrapping at the popup's width
  assert.match(RENDER, /const expanded = i === sel && slashExpanded;/);
  assert.match(RENDER, /\(expanded \? " expanded" : ""\)/);
  assert.match(CSS, /\.slash-row\.expanded \{ display: block; \}/);
  assert.match(CSS, /\.slash-row\.expanded \.slash-name, \.slash-row\.expanded \.slash-desc \{ display: block;[\s\S]*?white-space: normal/);
  // hover-select is frozen while expanded (repaints re-flow heights under the cursor and would flap the mode)
  assert.match(RENDER, /if \(!slashExpanded && sel !== i\)/);
});

test("the key hints ride the SELECTED row, never a below-the-fold footer (the user 2026-08-13, round 2)", () => {
  // round 1 put the hint at the popup's bottom — with a long list it sat below the fold of the scrolling
  // menu. The selected row is always scrolled into view, so the hint lives there, right-aligned and dim.
  assert.doesNotMatch(RENDER, /slash-hint/);
  assert.doesNotMatch(CSS, /\.slash-hint/);
  assert.match(RENDER, /if \(i === sel\) \{ const k = document\.createElement\("span"\); k\.className = "slash-key-hint"; k\.textContent = "→ expand"; row\.appendChild\(k\); \}/);
  assert.match(RENDER, /k\.textContent = "← all commands"/);
  assert.match(CSS, /\.slash-key-hint \{ flex: 0 0 auto; margin-left: auto; font-size: 0\.82em; opacity: 0\.6;/);   // menu sub-line size/opacity
  assert.match(CSS, /\.slash-row\.sel \.slash-key-hint \{ color: var\(--accent-fg\); \}/);   // legible on the accent row
});

test("the expanded view lists a multi-group arg hint one bracketed group per line (the user 2026-08-13, round 2)", () => {
  // "[--fix] [--comment]" → two lines; a free-form hint (anything not wholly bracketed groups) stays ONE
  // line rather than being split by a guess
  assert.match(RENDER, /const argLines = \(hint: string\): string\[\] => \{/);
  assert.match(RENDER, /const groups = hint\.match\(\/\\\[\[\^\\\]\]\*\\\]\/g\) \|\| \[\];/);
  assert.match(RENDER, /return groups\.length >= 2 && !residue \? groups : \[hint\.trim\(\)\];/);
  assert.match(RENDER, /args\.className = "slash-x-args"/);
  assert.match(CSS, /\.slash-x-args \{ margin-top: 2px; padding-left: 12px; \}/);
  assert.match(CSS, /\.slash-x-args \.slash-arg \{ display: block; white-space: normal;/);
  // the expanded head keeps /name and the ← hint on one line
  assert.match(RENDER, /head\.className = "slash-x-head"/);
  assert.match(CSS, /\.slash-x-head \{ display: flex; align-items: baseline; gap: 9px; \}/);
});

test("the composer placeholder hints that / opens commands (the user 2026-06-30)", () => {
  // the resting placeholder now comes from composerRestingPlaceholder(); its DESKTOP form carries the
  // full hint row — send, newline, stage, and the bare "/ for commands" (the user 2026-08-15: "type"
  // was filler) — while mobile keeps just the core prompt (see the composer-send mobile test)
  // the live box's resting form is written by setComposerAskMode (one owner with the answering tint, 2026-09-10); a
  // closed session's box says so instead
  assert.match(RENDER, /if \(closed\) \{ composer\.placeholder = "Session closed — read-only"; composer\.classList\.remove\("answering"\); syncComposerPh\(\); \}/);
  assert.match(RENDER, /function setComposerAskMode\(\) \{[\s\S]*?ta\.placeholder = composerRestingPlaceholder\(\);/);
  assert.match(RENDER, /"Message this session…  \(⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · \/ for commands\)"/);
});
