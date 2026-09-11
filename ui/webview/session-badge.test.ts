// The statusline's session badge (the user 2026-09-09): the session's name on its identity colour, text in
// black, before the state chip — per pane, so a split column badges ITS session. The spec is a pure rule
// (session-badge.ts), executed here; the statusline wiring in render.ts and the chip's styling are pinned at
// source (no jsdom for the renderer — the repo convention). Synthetic names and colours only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { badgeSpec } from "./session-badge";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a named session gets its name on its identity colour; no colour yet → the neutral fill (bg null)", () => {
  assert.deepEqual(badgeSpec({ name: "web", color: { bg: "#7fb3d5", fg: "#000000" } as any }), { text: "web", bg: "#7fb3d5" });
  assert.deepEqual(badgeSpec({ name: "api", color: null }), { text: "api", bg: null });
  assert.deepEqual(badgeSpec({ name: " tests " }), { text: "tests", bg: null }, "trimmed");
});

test("no name, no badge — the opening line covers a tab whose payload has not arrived", () => {
  assert.equal(badgeSpec({ name: "", color: { bg: "#7fb3d5" } }), null);
  assert.equal(badgeSpec({}), null);
  assert.equal(badgeSpec(null), null);
  assert.equal(badgeSpec(undefined), null);
});

test("render.ts: updateStatusline puts the badge first, before the state chip, on every state", () => {
  assert.match(RENDER, /import \{ badgeSpec \} from "\.\/session-badge";/);
  const fn = RENDER.slice(RENDER.indexOf("function updateStatusline() {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function updateStatusline() {")));
  const badgeAt = fn.indexOf("const bs = settings.showSessionBadge === true ? badgeSpec(s) : null;");
  assert.ok(badgeAt > 0, "the badge is built from the active session — when the setting opts in (off by default, the maintainers 2026-09-10)");
  assert.ok(badgeAt > fn.indexOf("sl.replaceChildren();"), "after the line is emptied");
  assert.ok(badgeAt > fn.indexOf('ro.textContent = "read-only · a subagent\'s transcript";'), "a subagent viewer, which is no session, gets none");
  assert.ok(badgeAt < fn.indexOf('if (s.status.state === "working") {'), "…and before the first state chip");
  assert.match(fn, /const b = el\("span", "chip chip-session"\); b\.textContent = bs\.text;/);
  assert.match(fn, /if \(bs\.bg\) b\.style\.background = bs\.bg;/, "the identity colour is the fill");
});

test("styles.css: the badge is a chip with black text on the session's colour, clipped to a short name", () => {
  assert.match(CSS, /\.chip-session \{ color: #000; background: var\(--box-border\); max-width: 14em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/);
});

// The badge is an OPT-IN (the maintainers via the user, 2026-09-10): the composer's placeholder names the session by default;
// the statusline badge is a setting, off by default, in the gear's Chat section right above Show git branch. Pinned the way
// statusline-branch.test.ts pins Show git branch: the type and its default, the gear's row/default/fill/save, the gate.
test("settings carry showSessionBadge, defaulting OFF, and the gear agrees", () => {
  const SETTINGS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "settings.ts"), "utf8");
  const GEAR = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "gear.js"), "utf8");
  assert.match(SETTINGS, /showSessionBadge: boolean;/);
  assert.match(SETTINGS, /DEFAULT_SETTINGS[^;]*showSessionBadge: false/);
  assert.doesNotMatch(GEAR, /showSessionBadge: true/);
  assert.equal((GEAR.match(/showSessionBadge: false/g) || []).length, 2, "both of the gear's default objects");
  assert.match(GEAR, /sbg = document\.getElementById\('rs-badge'\)/);
  assert.match(GEAR, /if \(sbg\) sbg\.checked = s\.showSessionBadge === true;/, "filled at open from the store, never assumed on");
  assert.match(GEAR, /if \(sbg\) sbg\.addEventListener\('change', function \(\) \{ var s = load\(\); s\.showSessionBadge = sbg\.checked; save\(s\); \}\);/);
  // the row: right above Show git branch, in the Chat section's checkbox dress, saying what it adds and that it is off
  const badgeRow = GEAR.indexOf("id=rs-badge"), branchRow = GEAR.indexOf("id=rs-branch"), denseRow = GEAR.indexOf("id=rs-dense");
  assert.ok(denseRow < badgeRow && badgeRow < branchRow, "between Compact tabs and agents and Show git branch");
  assert.match(GEAR, /<b>Show session badge<\/b>/);
  assert.match(GEAR, /The message box already names the session; this adds the name where its state reads\. Off by default\./);
});

test("render.ts: the badge is gated on the live setting, so a gear flip shows or hides it at the next statusline paint", () => {
  assert.match(RENDER, /const bs = settings\.showSessionBadge === true \? badgeSpec\(s\) : null;/);
  // a settings change re-renders the active view, whose showActive repaints the statusline: no extra wiring needed
  assert.match(RENDER, /onExternalSettingsChange\(\(s\) => \{ settings = s; applyChatScheme\(s\); renderTabs\(\); rerenderAll\(\);/);
});
