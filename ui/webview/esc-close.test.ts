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
const GEAR = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.js"), "utf8");

test("the shared Escape block wires the shell document AND every pane document", () => {
  assert.ok(ESC.includes("document.addEventListener('keydown',onEsc,true);"));
  assert.ok(ESC.includes("['f-chat','f-fleet','f-feed','f-files','f-timeline','f-settings'].forEach"), "the gear's document (the hidden settings iframe) is wired with the panes'");
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

test("the gear is the chain's last step: the shell asks the settings page's own close hook, which reports whether it took the press", () => {
  // the /settings page lives in the hidden #f-settings iframe, lifted full-window at z200 while the gear is open
  // (2026-09-10); the keyboard sits in that document, so the Escape is heard there and closes the gear
  const nt = ESC.indexOf("__rompCloseNet();closed=true;}");
  const st = ESC.indexOf("else if(document.body.classList.contains('settings-open')&&settingsClose()){closed=true;}");
  assert.ok(nt > -1 && st > nt, "after the net panel, gated on the shell's own settings-open class");
  assert.ok(ESC.includes("function settingsClose(){var f=document.getElementById('f-settings');"));
  assert.ok(ESC.includes("return !!(w&&w.__rompSettingsClose&&w.__rompSettingsClose());}catch(e){return false;}}"), "no page yet, or a cross-origin one: not closed");
  // gear.js: the hook closes the modal and says so, unless one of its own dialogs is up (they close one level at a time)
  assert.ok(GEAR.includes("window.__rompSettingsClose = function () { if (p.hidden || (lgM && !lgM.hidden) || openHousePick) return false; closeSettings(); return true; };"));
  assert.ok(GEAR.includes("if (e.key === 'Escape' && lgM && !lgM.hidden) lgModal(false);"), "the login card's own Escape stays");
  // closing hides the iframe that held the keyboard: the shell's settings bridge puts focus back in the chat — the
  // split's column last worked in, else the first (chat split, 2026-09-11)
  assert.ok(KERNEL.includes("if(!m.on){var fid=(window.__rompFocusedChatId&&window.__rompFocusedChatId())||'f-chat';var fc=document.getElementById(fid)||document.getElementById('f-chat');try{fc&&fc.contentWindow&&fc.contentWindow.focus();}catch(e){}}}"));
});
