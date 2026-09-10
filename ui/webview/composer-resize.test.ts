// Drag-to-resize the message box (the user 2026-07-07): a #composer-resize handle straddles the top-edge
// divider between the composer and the transcript. Dragging it UP grows the box (to see a long message in
// full), DOWN shrinks it; a send snaps it back to one line. composerManualH raises the auto-grow cap while
// set and is cleared on send. Source-level pins (no jsdom for the chat renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const SKELETON = fs.readFileSync(path.resolve(process.cwd(), "src", "page-skeleton.ts"), "utf8");

test("the handle sits on the footer's top-edge divider, above the statusline", () => {
  assert.match(SKELETON, /<div id="composer-resize"[^>]*><\/div>\s*\n\s*<div id="statusline"/);
  assert.match(CSS, /#footer \{[^}]*position: relative;/);
  assert.match(CSS, /#composer-resize \{[^}]*top: -3px;[^}]*cursor: ns-resize;/);
});

test("growComposer uses the dragged height as the auto-grow cap (else the default 120)", () => {
  assert.match(RENDER, /let composerManualH: number \| null = null;/);
  const body = RENDER.slice(RENDER.indexOf("function growComposer"), RENDER.indexOf("function growComposer") + 260);
  assert.match(body, /const cap = composerManualH \?\? 120;/);
  assert.match(body, /Math\.min\(ta\.scrollHeight, cap\)/);
});

test("dragging sets composerManualH + the height, clamped between min and 60vh", () => {
  assert.match(RENDER, /const composerMaxH = \(\) => Math\.max\(120, Math\.round\(window\.innerHeight \* 0\.6\)\)/);
  assert.match(RENDER, /Math\.max\(COMPOSER_MIN_H, Math\.min\(composerMaxH\(\), startH \+ \(startY - e\.clientY\)\)\)/);
  assert.match(RENDER, /grip\.addEventListener\("pointerdown"/);
  // pointer capture via window-level move/up so the drag survives leaving the 7px handle
  assert.match(RENDER, /window\.addEventListener\("pointermove", onMove\)/);
  assert.match(RENDER, /window\.addEventListener\("pointerup", onUp\)/);
});

test("a send snaps the box back to one line (composerManualH cleared)", () => {
  // in sendComposer's deliver, through the composer's one clear path (clearBox resets the drag height with the text)
  assert.match(RENDER, /clearBox\(\);\s*\/\/ a drag-expanded box snaps back to one line after a send/);
  assert.match(RENDER, /const clearBox = \(\) => \{\s*\n\s*ta\.value = ""; composerManualH = null; ta\.style\.height = "";/);
});

test("a double-click on the handle resets to auto without sending", () => {
  assert.match(RENDER, /grip\.addEventListener\("dblclick", \(\) => \{ composerManualH = null; growComposer\(ta\); \}\)/);
});
