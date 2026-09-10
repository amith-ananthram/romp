// Escape closes the TOPMOST open shell modal (the user 2026-08-09: the usage/network/Log panels could
// only be clicked away — Escape did nothing whenever focus sat inside a pane iframe, because a keydown
// never crosses the iframe boundary). The fix is one shared handler with the palette's own dual
// wiring: capture on the shell document AND on every same-origin pane document, re-attached per
// iframe (re)load. Each panel exposes its OWN close (state cleanup lives in those closures); the
// shared block only decides which panel Escape means, topmost first. No jsdom harness → source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const ESC = KERNEL.split('_LANDING_ESC_JS = """')[1].split('"""')[0];

test("the shared Escape block wires the shell document AND every pane document", () => {
  assert.ok(ESC.includes("document.addEventListener('keydown',onEsc,true);"));
  assert.ok(ESC.includes("['f-chat','f-fleet','f-feed','f-files','f-timeline'].forEach"));
  assert.ok(ESC.includes("f.contentDocument.addEventListener('keydown',onEsc,true);"));
  assert.ok(ESC.includes("f.addEventListener('load',wire);wire();"), "re-attached on every iframe (re)load");
  // and the block is actually spliced into the landing page
  assert.ok(KERNEL.includes('"<script>" + _LANDING_ESC_JS + "</script>"'));
});

test("topmost first, and only when a shell modal is actually open", () => {
  // the shortcuts dialog (z300, whose close() first cancels an in-progress recording) beats the
  // usage modal (z300, opened from elsewhere) beats the Log (z210) beats the net panel (z200);
  // with nothing open the handler touches nothing, so pane-local Escapes keep working
  const ky = ESC.indexOf("__rompKeysClose");
  const ru = ESC.indexOf("__rompUsageClose");
  const er = ESC.indexOf("__rompCloseErrs");
  const nt = ESC.indexOf("__rompCloseNet");
  assert.ok(ky > -1 && ru > ky && er > ru && nt > er, "priority order: shortcuts, usage, Log, net");
  assert.ok(ESC.includes("window.__rompKeysClose&&window.__rompKeysClose()"), "the dialog reports whether it consumed the press");
  assert.ok(ESC.includes("ru.classList.contains('on')"));
  assert.ok(ESC.includes("!er.hidden"));
  assert.ok(ESC.includes("!nt.hidden"));
  assert.ok(ESC.includes("if(closed){e.preventDefault();e.stopPropagation();}"));
});

test("each panel exposes its real close — cleanup stays in the owning closure", () => {
  assert.ok(KERNEL.includes("window.__rompCloseErrs=close;"));
  assert.ok(KERNEL.includes("window.__rompCloseNet=close;"));
  assert.ok(KERNEL.includes("window.__rompUsageClose=off;"));
  assert.ok(KERNEL.includes("window.__rompUsageClose=null;"), "the usage close disarms when the modal shuts");
  // the panels' own shell-only Escape listeners are gone — deaf-with-iframe-focus was the bug
  assert.ok(!KERNEL.includes("if(e.key==='Escape'&&!back.hidden)close()"), "net's old shell-only listener removed");
  assert.ok(!KERNEL.includes("var esc2=function(e){if(e.key==='Escape')off();};"), "usage's old shell-only listener removed");
});
