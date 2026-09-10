#!/usr/bin/env python3
"""The bottom bar's API health cell, driven in a real browser.

The shell page is kernel-served HTML with inline CSS and JS, so it has no jsdom harness: the source pins in
tests/test_api_health_rail.py and tests/test_kernel_pane_rail.py hold the SHAPE, and this module holds the
BEHAVIOR those pins approximate. A scratch copy of km._landing() is served from a temp directory over plain
HTTP with no kernel behind it (every fetch 404s, which is exactly the pre-frame world the cell's hidden rule
exists for), and playwright's chromium drives it: frames are handed to window.__rompApiHealth directly,
presses are dispatched as pointer events, and the driver reports what the DOM did. The page's WebSocket is a
shim installed before load: it never opens on its own, so the first two phases see the same never-connected
socket a refused connection gives, without the real socket's close-and-redial every two seconds; the third
phase opens it, feeds it frames, drops it and watches the redial, the way a kernel behind a dropped tunnel
would. Skips LOUDLY without the extension's node deps or a browser (CI installs none), the way
tests/test_awaiting_box_sync.py does.

Synthetic only: an invented sid family, the notes-api demo's session names, no real data."""
import functools
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from romp_load import load_source

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
EXT = os.path.join(os.path.dirname(HERE), "vscode-extension")
km = load_source("romp_kernel_apih_browser", os.path.join(BIN, "romp-kernel"))

SID = "77777777-aaaa-4bbb-8ccc-00000000000"     # + a digit: the rail test module's private synthetic family

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
page.on("pageerror", () => {});                       // the scratch page has no kernel: its sockets and fetches fail
// the WebSocket shim: every socket the page opens is recorded in window.__socks and does nothing until the driver
// opens (__open), feeds (__msg) or drops (__drop) it. readyState follows the real constants.
await page.addInitScript(() => {
  function Fake(url) { this.url = String(url); this.readyState = 0; this.sent = []; this.onopen = this.onmessage = this.onclose = this.onerror = null;
    (window.__socks = window.__socks || []).push(this); }
  Fake.prototype.send = function (d) { if (this.readyState !== 1) throw new Error("not open"); this.sent.push(String(d)); };
  Fake.prototype.close = function () { this.__drop(); };
  Fake.prototype.__open = function () { this.readyState = 1; if (this.onopen) this.onopen({}); };
  Fake.prototype.__msg = function (o) { if (this.onmessage) this.onmessage({ data: JSON.stringify(o) }); };
  Fake.prototype.__drop = function () { if (this.readyState === 3) return; this.readyState = 3; if (this.onclose) this.onclose({}); };
  Fake.CONNECTING = 0; Fake.OPEN = 1; Fake.CLOSING = 2; Fake.CLOSED = 3;
  window.WebSocket = Fake;
});
await page.goto(cfg.url);
await page.waitForFunction(() => typeof window.__rompApiHealth === "function", null, { timeout: 20000 });
const R = await page.evaluate((SID) => {
  const R = { err: {} };
  window.__realShellSend = window.__rompShellSend;             // the page's own binding, before the steps stub it
  const step = (name, fn) => { try { fn(); } catch (e) { R.err[name] = String(e && e.stack || e); } };
  const el = document.getElementById("rail-api"), txt = el.querySelector(".ah-text");
  const tip = () => document.getElementById("ah-tip");
  const back = document.getElementById("ru-back");
  const disp = (n) => getComputedStyle(n).display;
  const row = (i) => ({ sid: SID + i, name: ["web", "api", "tests", "docs"][i], color: null, kind: "blocked",
                        cls: "529", status: 529, since: 1700000000 + i, suppressed: false });
  const frame = (over) => Object.assign({ type: "apiHealth", state: "degraded", cls: "529", reason: "",
    text: "overloaded · 1 waiting", waiting: 1, retrying: 0, blocked: 1, since: 1700000000, tmux: 0,
    sessions: [row(1)], seq: 1 }, over || {});
  window.__frame = frame; window.__row = row;                 // the driver's later phases reuse them
  const bg = (n) => n ? getComputedStyle(n).backgroundColor : "";
  const btn = () => tip().querySelector("button[data-act=pause]");
  // 1. before any frame: the cell is not displayed (the hidden attribute must beat .ru-w's display rule)
  R.preFrameHidden = el.hidden;
  R.preFrameDisplay = disp(el);
  R.usageEmptyDisplay = disp(document.getElementById("rail-usage"));
  R.role = el.getAttribute("role"); R.tabindex = el.getAttribute("tabindex");
  // 2. the first frame reveals it and names the state for a screen reader
  window.__rompApiHealth(frame());
  R.firstFrameDisplay = disp(el); R.firstFrameText = txt.textContent; R.ariaLabel = el.getAttribute("aria-label");
  step('hover', () => {
  // 3. the hover surface is inert: no button, no data-act; a growing frame re-anchors it above the rail
  el.dispatchEvent(new MouseEvent("mouseenter", { bubbles: false, clientX: el.getBoundingClientRect().left + 10 }));
  R.hoverShown = tip().style.display === "block";
  R.hoverButtons = tip().querySelectorAll("button").length;
  R.hoverActs = tip().querySelectorAll("[data-act]").length;
  const railTop = el.getBoundingClientRect().top;
  const b1 = tip().getBoundingClientRect();
  window.__rompApiHealth(frame({ text: "overloaded · 3 waiting", waiting: 3, blocked: 3, sessions: [row(1), row(2), row(3)] }));
  const b2 = tip().getBoundingClientRect();
  R.reanchor = { railTop, before: { top: b1.top, bottom: b1.bottom, h: b1.height }, after: { top: b2.top, bottom: b2.bottom, h: b2.height } };
  el.dispatchEvent(new MouseEvent("mouseleave"));
  R.hoverHidden = tip().style.display === "none";
  });
  step('keyboard', () => {
  // 4. keyboard: Enter opens the pinned detail and moves focus into it; Escape closes and puts focus back
  el.focus();
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  R.kbOpened = tip().style.display === "block" && tip().classList.contains("ru-modal") && back.classList.contains("on");
  R.kbFocusInTip = tip().contains(document.activeElement);
  R.tipRole = tip().getAttribute("role");
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  R.kbClosed = tip().style.display === "none" && !back.classList.contains("on");
  R.kbFocusBack = document.activeElement === el;
  });
  step('usage', () => {
  // 5. an open usage modal is closed first, explicitly, so the shared backdrop never serves two modals
  let usageClosed = false;
  window.__rompUsageClose = () => { usageClosed = true; back.classList.remove("on"); window.__rompUsageClose = null; };
  back.classList.add("on");
  el.click();
  R.usageClosedFirst = usageClosed && tip().style.display === "block" && tip().classList.contains("ru-modal");
  });
  step('clicksafe', () => {
  // 6. click-safe across a frame: a press held on the button defers the re-render; the release flushes it
  const sent = []; window.__rompShellSend = (o) => { sent.push(o); return true; };
  const b0 = btn(); R.pinnedHasButton = !!b0;
  b0.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
  window.__rompApiHealth(frame({ text: "overloaded · 2 waiting", waiting: 2, blocked: 2, sessions: [row(1), row(2)] }));
  R.heldKeepsNode = document.contains(b0);
  R.heldKeepsText = /3 waiting/.test(tip().textContent) && !/2 waiting/.test(tip().textContent);   // the pinned frame stays up
  b0.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  b0.click();                                          // the click lands on the button that was pressed
  R.clickSent = sent.map((o) => o.type + ":" + String(o.value));
  R.flushedOnClick = /2 waiting/.test(tip().textContent);
  });
  step('ack', () => {
  // 7. the acknowledgment survives frames until one confirms the flip
  const b1n = btn();
  R.ack = { disabled: b1n.disabled, label: b1n.textContent, acted: b1n.classList.contains("romp-acted") };
  window.__rompApiHealth(frame({ text: "overloaded · 2 waiting", waiting: 2, blocked: 2, since: 1700000005, sessions: [row(1), row(2)] }));
  const b2n = btn();
  R.ackHeld = { disabled: b2n.disabled, label: b2n.textContent, acted: b2n.classList.contains("romp-acted") };
  window.__rompApiHealth(frame({ state: "paused", reason: "manual", text: "paused by you · 2 waiting", waiting: 2, blocked: 2, sessions: [row(1), row(2)], seq: 2 }));
  const b3n = btn();
  R.confirmed = { disabled: b3n.disabled, label: b3n.textContent, acted: b3n.classList.contains("romp-acted") };
  });
  step('resumeLimit', () => {
  // 7b. Resume during a usage-limit pause: the kernel lifts, the limit re-engages within the cycle, and the frame
  //     that answers is paused again with a new since and a moved seq. The button must read that truth (enabled
  //     Resume), not hold a disabled 'Stop' for the rest of the window.
  const sentR = []; window.__rompShellSend = (o) => { sentR.push(o); return true; };
  window.__rompApiHealth(frame({ state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000010, seq: 5 }));
  const br = btn(); R.resumeBefore = { disabled: br.disabled, label: br.textContent };
  br.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
  br.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  br.click();
  R.resumeSent = sentR.map((o) => o.type + ":" + String(o.value));
  const bp = btn(); R.resumePending = { disabled: bp.disabled, label: bp.textContent, acted: bp.classList.contains("romp-acted") };
  window.__rompApiHealth(frame({ state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000010, seq: 5 }));
  const bs = btn(); R.resumeSameSeq = { disabled: bs.disabled, label: bs.textContent, acted: bs.classList.contains("romp-acted") };
  window.__rompApiHealth(frame({ state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000020, seq: 7 }));
  const ba = btn(); R.resumeAfter = { disabled: ba.disabled, label: ba.textContent, acted: ba.classList.contains("romp-acted") };
  R.resumeLine = (tip().querySelector(".ah-line") || {}).textContent || "";
  // back to the paused world the ack step left, so the failed-send step below presses the same Resume it always did
  window.__rompApiHealth(frame({ state: "paused", reason: "manual", text: "paused by you · 2 waiting", waiting: 2, blocked: 2, sessions: [row(1), row(2)], seq: 7 }));
  });
  step('failed', () => {
  // 8. a failed send restores the label, drops the acted styling and says why
  window.__rompShellSend = () => false;
  const bf = btn();
  bf.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
  bf.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  bf.click();
  const b4n = btn();
  R.failed = { disabled: b4n.disabled, label: b4n.textContent, acted: b4n.classList.contains("romp-acted"),
               hint: (tip().querySelector(".ah-hint") || {}).textContent || "" };
  });
  step('row', () => {
  // 9. a session row opens that session the way the feed's own links do: openSession on the socket,
  //    no pane toggle, nothing persisted
  const sent2 = []; window.__rompShellSend = (o) => { sent2.push(o); return true; };
  let toggled = 0; window.__rompPaneToggle = () => { toggled++; };
  const r0 = tip().querySelector(".ah-row[data-act=reveal]");
  R.rowHasAct = !!r0;
  if (r0) { r0.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true })); r0.dispatchEvent(new PointerEvent("pointerup", { bubbles: true })); r0.click(); }
  R.rowSent = sent2; R.rowToggled = toggled; R.rowClosed = tip().style.display === "none";
  });
  step('rowDead', () => {
  // 9b. the same row through the page's OWN send while no socket is open (the shim never opened the one shellWS
  //     dialed at load): the row says so under the button and the detail stays open. A frame first: it clears the
  //     failed step's hint, so the hint read here is this row's own.
  window.__rompShellSend = window.__realShellSend;
  window.__rompApiHealth(frame({ state: "paused", reason: "manual", text: "paused by you · 2 waiting", waiting: 2, blocked: 2, sessions: [row(1), row(2)], seq: 7 }));
  el.click();                                              // pin the detail again (the row step closed it)
  const hintBefore = (tip().querySelector(".ah-hint") || {}).textContent || "";
  const rd = tip().querySelector(".ah-row[data-act=reveal]");
  rd.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true })); rd.dispatchEvent(new PointerEvent("pointerup", { bubbles: true })); rd.click();
  R.rowDead = { hintBefore, open: tip().style.display === "block", hint: (tip().querySelector(".ah-hint") || {}).textContent || "" };
  if (tip().style.display === "block") el.click();        // close it again (only if it is still open)
  });
  step('light', () => {
  // 10. the light theme's ok dot
  document.body.classList.add("theme-light");
  window.__rompApiHealth(frame({ state: "ok", cls: "", text: "ok", waiting: 0, blocked: 0, since: 0, sessions: [] }));
  R.lightDot = getComputedStyle(el.querySelector(".ah-dot")).backgroundColor;
  R.lightDotOpacity = getComputedStyle(el.querySelector(".ah-dot")).opacity;
  });
  step('lightState', () => {
  // 11. light theme, degraded and paused: the detail's headline dot wears the rail dot's color (a bare light rule
  //     would outrank the state rules and paint it the label gray)
  window.__rompApiHealth(frame({ seq: 8 }));
  el.click();                                              // pin the detail (the row step closed it)
  const head = () => tip().querySelector(".ah-head .ah-dot");
  R.lightDegraded = { rail: bg(el.querySelector(".ah-dot")), head: bg(head()), headOpacity: getComputedStyle(head()).opacity };
  window.__rompApiHealth(frame({ state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000010, seq: 8 }));
  R.lightPaused = { rail: bg(el.querySelector(".ah-dot")), head: bg(head()) };
  window.__rompApiHealth(frame({ state: "ok", cls: "", text: "ok", waiting: 0, blocked: 0, since: 0, sessions: [], seq: 8 }));
  R.lightOkHead = { head: bg(head()), headOpacity: getComputedStyle(head()).opacity };
  el.click();                                              // close it again
  document.body.classList.remove("theme-light");
  });
  return R;
}, cfg.sid);
// phase 2: a real keyboard and a real mouse. Synthetic key events do not move focus, and a synthetic right button
// fires no contextmenu, so these ride playwright's input instead of page.evaluate.
R.err2 = {};
const step2 = async (name, fn) => { try { await fn(); } catch (e) { R.err2[name] = String(e && e.stack || e); } };
const active = () => page.evaluate(() => { const a = document.activeElement, t = document.getElementById("ah-tip");
  return (a.tagName + (a.getAttribute("data-act") ? "." + a.getAttribute("data-act") : "") + (t.contains(a) ? "" : "!out")); });
await step2('tab', async () => {
  // 12. Tab cycles within the open dialog: button, row, Usage, Log, then the button again; Shift+Tab runs back
  await page.evaluate(() => { window.__sent2 = []; window.__rompShellSend = (o) => { window.__sent2.push(o); return true; };
    window.__usageOpened = 0; window.__rompUsagePanel = () => { window.__usageOpened++; };
    window.__rompApiHealth(window.__frame({ state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000010, seq: 9 }));
    document.getElementById("rail-api").focus(); });
  await page.keyboard.press("Enter");
  R.tabOpened = await page.evaluate(() => document.getElementById("ah-tip").classList.contains("ru-modal") && document.getElementById("ah-tip").contains(document.activeElement));
  R.tabAria = await page.evaluate(() => document.getElementById("ah-tip").getAttribute("aria-modal"));
  const seq = [];
  for (let i = 0; i < 6; i++) { await page.keyboard.press("Tab"); seq.push(await active()); }
  R.tabSeq = seq;
  const back = [];
  for (let i = 0; i < 3; i++) { await page.keyboard.press("Shift+Tab"); back.push(await active()); }
  R.tabBack = back;
});
await step2('enterRow', async () => {
  // 13. Enter on a focused row opens that session; the dialog closes
  await page.evaluate(() => { const r = document.querySelector("#ah-tip .ah-row[data-act=reveal]"); r.focus(); });
  R.rowFocused = await active();
  await page.keyboard.press("Enter");
  R.enterRowSent = await page.evaluate(() => window.__sent2.map((o) => o.type + ":" + o.id));
  R.enterRowClosed = await page.evaluate(() => document.getElementById("ah-tip").style.display === "none");
});
await step2('enterUsage', async () => {
  // 14. Space on the focused Usage link opens the usage modal; a frame while a row is focused keeps focus on it
  await page.evaluate(() => { document.getElementById("rail-api").focus(); });
  await page.keyboard.press("Enter");
  await page.evaluate(() => { document.querySelector("#ah-tip .ah-row[data-act=reveal]").focus();
    window.__rompApiHealth(window.__frame({ state: "paused", reason: "limit", text: "paused · usage limit · 2 waiting", waiting: 2, blocked: 2, since: 1700000010, seq: 9, sessions: [window.__row(1), window.__row(2)] })); });
  R.focusAfterFrame = await active();
  await page.evaluate(() => { document.querySelector("#ah-tip .ah-link[data-act=usage]").focus(); });
  await page.keyboard.press("Space");
  R.usageOpened = await page.evaluate(() => window.__usageOpened);
  R.usageClosedTip = await page.evaluate(() => document.getElementById("ah-tip").style.display === "none");
});
await step2('rightButton', async () => {
  // 15. a right-button press over the detail does not defer a frame (no click follows it). The scratch page has
  //     no kernel, so the boot splash (#romp-boot, a full-window overlay) never clears and would take every real
  //     mouse event: it goes first, and each press records that it landed on the card.
  await page.evaluate(() => { const b = document.getElementById("romp-boot"); if (b) b.remove();
    window.__rompApiHealth(window.__frame({ seq: 9 })); document.getElementById("rail-api").click(); });
  const head = () => page.evaluate(() => { const r = document.querySelector("#ah-tip .ah-head").getBoundingClientRect(); return { x: r.left + 8, y: r.top + r.height / 2 }; });
  const inside = (b) => page.evaluate(([x, y]) => document.getElementById("ah-tip").contains(document.elementFromPoint(x, y)), [b.x, b.y]);
  const box = await head();
  R.rightInside = await inside(box);
  await page.mouse.move(box.x, box.y);
  await page.mouse.down({ button: "right" });
  await page.evaluate(() => { window.__rompApiHealth(window.__frame({ text: "overloaded · 5 waiting", waiting: 5, blocked: 5, seq: 9,
    sessions: [1, 2, 3, 4].map(window.__row).concat([Object.assign(window.__row(1), { sid: "x5", name: "five" })]) })); });
  R.rightHeldPainted = await page.evaluate(() => /5 waiting/.test(document.getElementById("ah-tip").textContent));
  await page.mouse.up({ button: "right" });
  R.rightReleasedPainted = await page.evaluate(() => /5 waiting/.test(document.getElementById("ah-tip").textContent));
  // and the primary press still defers, then paints on release + click. The five-row frame grew the centered
  // card, so the head moved: measure again.
  const box2 = await head();
  R.primaryInside = await inside(box2);
  await page.mouse.move(box2.x, box2.y);
  await page.mouse.down();
  await page.evaluate(() => { window.__rompApiHealth(window.__frame({ text: "overloaded · 6 waiting", waiting: 6, blocked: 6, seq: 9,
    sessions: [1, 2, 3, 4].map(window.__row).concat([Object.assign(window.__row(1), { sid: "x5", name: "five" }), Object.assign(window.__row(2), { sid: "x6", name: "six" })]) })); });
  R.primaryHeldPainted = await page.evaluate(() => /6 waiting/.test(document.getElementById("ah-tip").textContent));
  await page.mouse.up();
  R.primaryReleasedPainted = await page.evaluate(() => /6 waiting/.test(document.getElementById("ah-tip").textContent));
});
await step2('pressFocus', async () => {
  // 16. a keyboard press on the pause button: the button is disabled at once, and a disabled element cannot hold
  //     focus, so focus would fall to BODY, where the card's Tab trap no longer sees the keys and a Shift+Tab walks
  //     out of the aria-modal dialog. Focus moves to the card before the disable; the trap holds.
  await page.evaluate(() => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    window.__sent3 = []; window.__rompShellSend = (o) => { window.__sent3.push(o); return true; };
    window.__rompApiHealth(window.__frame({ state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000010, seq: 12 }));
    document.getElementById("rail-api").focus(); });
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  R.pressFocusBefore = await active();
  await page.keyboard.press("Space");
  R.pressSent = await page.evaluate(() => window.__sent3.map((o) => o.type + ":" + String(o.value)));
  R.pressFocusAfter = await active();
  R.pressButton = await page.evaluate(() => { const b = document.querySelector("#ah-tip button[data-act=pause]"); return { disabled: b.disabled, label: b.textContent }; });
  await page.keyboard.press("Shift+Tab");
  R.pressShiftTab = await active();
  await page.keyboard.press("Tab");
  R.pressTabBack = await active();
  await page.evaluate(() => { document.getElementById("ah-tip").focus();
    window.__rompApiHealth(window.__frame({ state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000020, seq: 13 })); });
  R.pressAnswerFocus = await active();
  await page.keyboard.press("Tab");
  R.pressAnswerTab = await active();
  await page.keyboard.press("Escape");
});
// phase 3: the shell socket itself. The shim stands in for the kernel's end: the driver opens the socket shellWS
// dialed at load, feeds it frames, presses through the REAL __rompShellSend, drops the socket and waits for the
// redial. The shell's own client-diag rows may ride the same socket; only the frames the cell cares about are read.
await step2('socket', async () => {
  // 17. a press acknowledged on a socket that then dies: the redial's ready re-sends the last frame verbatim (same
  //     seq), so nothing else would clear the acknowledgment; the close clears it and says why, the re-sent frame
  //     repaints the truth, and the next press rides the new socket
  const sock = (i) => page.evaluate((i) => { const s = window.__socks[i]; return s ? { url: s.url, state: s.readyState,
    sent: s.sent.filter((d) => !/"clientDiag"/.test(d)) } : null; }, i);
  const open = (i) => page.evaluate((i) => { window.__socks[i].__open(); }, i);
  const drop = (i) => page.evaluate((i) => { window.__socks[i].__drop(); }, i);
  const feed = (i, over) => page.evaluate(([i, over]) => { window.__socks[i].__msg(window.__frame(over)); }, [i, over]);
  const redial = (n) => page.waitForFunction((n) => window.__socks.length >= n, n, { timeout: 8000 });
  const press = () => page.evaluate(() => { const b = document.querySelector("#ah-tip button[data-act=pause]");
    b.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true })); b.dispatchEvent(new PointerEvent("pointerup", { bubbles: true })); b.click(); });
  const btnState = () => page.evaluate(() => { const b = document.querySelector("#ah-tip button[data-act=pause]"); const h = document.querySelector("#ah-tip .ah-hint");
    return { disabled: b.disabled, label: b.textContent, acted: b.classList.contains("romp-acted"), hint: h ? h.textContent : "" }; });
  const PAUSED = { state: "paused", reason: "limit", text: "paused · usage limit · 1 waiting", since: 1700000010, seq: 20 };
  R.sockCount = await page.evaluate(() => window.__socks.length);
  R.sock0 = await sock(0);
  // the steps above replaced window.__rompShellSend with stubs; the page's own binding sends on the live shell socket
  await page.evaluate(() => { window.__rompShellSend = window.__realShellSend; });
  R.shellSendRestored = await page.evaluate(() => typeof window.__rompShellSend);
  await drop(0);
  await redial(2);
  R.sockRedialed = await page.evaluate(() => window.__socks.length);
  await open(1);
  R.sock1Ready = (await sock(1)).sent;
  await feed(1, PAUSED);
  R.sockPainted = await page.evaluate(() => document.getElementById("rail-api").querySelector(".ah-text").textContent);
  await page.evaluate(() => { document.getElementById("rail-api").click(); });
  await press();
  R.sockPressSent = (await sock(1)).sent;
  R.sockAcked = await btnState();
  await drop(1);
  R.sockDropped = await btnState();
  await press();                                           // no socket is open now: the page's own send refuses
  R.sockDeadPress = await btnState();
  await redial(3);
  R.sock2 = await sock(2);
  await open(2);
  R.sock2Ready = (await sock(2)).sent;
  await feed(2, PAUSED);                                   // the redial's ready re-sends the last frame verbatim
  R.sockResent = await btnState();
  await press();
  R.sock2Sent = (await sock(2)).sent;
  R.sock1After = (await sock(1)).sent;
  // and the other outcome: the kernel DID take the press before the socket died, so the re-sent frame's seq has moved
  await drop(2);
  await redial(4);
  await open(3);
  await feed(3, { text: "overloaded · 1 waiting", seq: 21 });
  R.sockMoved = await btnState();
  await page.evaluate(() => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
});
if (cfg.shots) await page.screenshot({ path: cfg.shots });
fs.writeSync(1, "RESULT:" + JSON.stringify(R) + "\n");
await browser.close();
process.exit(0);
"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class ServedCell(unittest.TestCase):
    """One browser run over the scratch page; each test reads one facet of what it reported."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served cell needs a browser")
        cls.lab = tempfile.mkdtemp(prefix="apih-browser-")
        # class cleanups run when setUpClass raises too (a failed driver), where tearDownClass would not
        cls.addClassCleanup(shutil.rmtree, cls.lab, ignore_errors=True)
        html = km._landing()
        with open(os.path.join(cls.lab, "index.html"), "w") as f:
            f.write(html if isinstance(html, str) else html.decode("utf-8"))
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=cls.lab))
        cls.addClassCleanup(cls.srv.server_close)
        cls.thr = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thr.start()
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/" % cls.srv.server_address[1], "sid": SID,
                       "shots": os.environ.get("APIH_BROWSER_SHOT", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=180,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        finally:
            cls.srv.shutdown()
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this machine: the served cell needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        cls.R = json.loads(line[len("RESULT:"):])

    def test_the_cell_is_not_displayed_before_the_first_frame(self):
        # the UA's [hidden]{display:none} loses to the author rule .ru-w{display:flex}, so without an author
        # [hidden] rule the rail would show a gray 'API ok' from page load, and forever on a kernel that sends no frame
        self.assertTrue(self.R["preFrameHidden"], "the markup ships the attribute")
        self.assertEqual(self.R["preFrameDisplay"], "none", "and the attribute must actually hide it: %r" % self.R)
        self.assertEqual(self.R["firstFrameDisplay"], "flex", "the first frame reveals the cell")
        self.assertEqual(self.R["firstFrameText"], "overloaded · 1 waiting")

    def test_the_driver_hit_no_script_error(self):
        self.assertEqual(self.R["err"], {})

    def test_the_light_theme_gives_the_ok_dot_the_label_color(self):
        # .ah-dot's dark label gray at .55 blends into the light rail (about 1.4:1), so the ok glyph would vanish
        self.assertEqual(self.R["lightDot"], "rgb(93, 87, 78)", "errors: %r" % self.R.get("err"))
        self.assertEqual(self.R["lightDotOpacity"], "0.55")

    def test_an_emptied_usage_cell_takes_no_gap(self):
        # renderRows empties #rail-usage on a login-only machine; as a zero-width flex item it would still pay the
        # scroll group's gap on both sides, so the API cell sat 28px from the pane buttons instead of 16px
        self.assertEqual(self.R["usageEmptyDisplay"], "none")

    def test_the_cell_is_a_keyboard_reachable_button_that_names_its_state(self):
        self.assertEqual((self.R["role"], self.R["tabindex"]), ("button", "0"))
        self.assertEqual(self.R["ariaLabel"], "API overloaded · 1 waiting")
        self.assertTrue(self.R["kbOpened"], "Enter opens the pinned detail: %r" % self.R.get("err"))
        self.assertTrue(self.R["kbFocusInTip"], "focus moves into the detail")
        self.assertEqual(self.R["tipRole"], "dialog")
        self.assertTrue(self.R["kbClosed"], "Escape closes it")
        self.assertTrue(self.R["kbFocusBack"], "and focus returns to the cell")

    def test_the_hover_tip_is_inert_and_re_anchors_when_a_frame_grows_it(self):
        self.assertTrue(self.R["hoverShown"])
        self.assertEqual((self.R["hoverButtons"], self.R["hoverActs"]), (0, 0), "no controls under pointer-events:none")
        ra = self.R["reanchor"]
        self.assertGreater(ra["after"]["h"], ra["before"]["h"], "three rows are taller than one: %r" % ra)
        self.assertLessEqual(ra["after"]["bottom"], ra["railTop"] + 1, "the grown tip still hangs above the rail: %r" % ra)
        self.assertTrue(self.R["hoverHidden"])

    def test_an_open_usage_modal_is_closed_before_the_detail_opens(self):
        self.assertTrue(self.R["usageClosedFirst"], "errors: %r" % self.R.get("err"))

    def test_a_press_held_across_a_frame_still_lands_and_the_release_flushes_the_frame(self):
        self.assertTrue(self.R["pinnedHasButton"])
        self.assertTrue(self.R["heldKeepsNode"], "the pressed button must survive the frame: %r" % self.R.get("err"))
        self.assertTrue(self.R["heldKeepsText"], "the re-render is deferred while the pointer is down")
        self.assertEqual(self.R["clickSent"], ["setGlobalRetryPaused:true"], "the click landed on the pressed button")
        self.assertTrue(self.R["flushedOnClick"], "the deferred frame is painted once the press is over")

    def test_the_acknowledgment_holds_until_the_frame_that_answers_the_press(self):
        acked = {"disabled": True, "label": "Resume all auto-retries", "acted": True}
        self.assertEqual(self.R["ack"], acked)
        self.assertEqual(self.R["ackHeld"], acked, "a frame from before the press (same seq) must not repaint an enabled Stop")
        self.assertEqual(self.R["confirmed"], {"disabled": False, "label": "Resume all auto-retries", "acted": False})

    def test_a_resume_during_a_usage_limit_pause_reads_the_truth_when_the_pause_re_engages(self):
        # a rule that cleared the acknowledgment only on a frame whose state matched the press would leave the
        # button disabled and reading 'Stop all auto-retries' for the rest of the window, since a limit pause
        # re-engages within the cycle
        self.assertEqual(self.R["resumeBefore"], {"disabled": False, "label": "Resume all auto-retries"}, "errors: %r" % self.R.get("err"))
        self.assertEqual(self.R["resumeSent"], ["setGlobalRetryPaused:false"])
        self.assertEqual(self.R["resumePending"], {"disabled": True, "label": "Stop all auto-retries", "acted": True})
        self.assertEqual(self.R["resumeSameSeq"], {"disabled": True, "label": "Stop all auto-retries", "acted": True},
                         "a frame carrying the seq we pressed on predates the press")
        self.assertEqual(self.R["resumeAfter"], {"disabled": False, "label": "Resume all auto-retries", "acted": False},
                         "the frame that answers (seq moved) re-enables the button with the truth: paused again")
        self.assertEqual(self.R["resumeLine"], "Auto-retry and the judges are paused until your usage limit resets.")

    def test_a_failed_send_restores_the_label_drops_the_acted_styling_and_says_why(self):
        f = self.R["failed"]
        self.assertEqual((f["disabled"], f["label"], f["acted"]), (False, "Resume all auto-retries", False), repr(f))
        self.assertIn("Not sent", f["hint"])

    def test_a_session_row_opens_that_session_the_way_the_feed_s_links_do(self):
        self.assertTrue(self.R["rowHasAct"])
        self.assertEqual(self.R["rowSent"], [{"type": "openSession", "id": SID + "1"}])
        self.assertEqual(self.R["rowToggled"], 0, "no pane toggle, nothing persisted")
        self.assertTrue(self.R["rowClosed"])

    def test_a_row_on_a_dead_socket_says_so_and_keeps_the_detail_open(self):
        # the page's own __rompShellSend, not a stub: no socket was ever opened, so it refuses and the row says why
        self.assertEqual(self.R["rowDead"], {"hintBefore": "", "open": True, "hint": "Not sent: the dashboard is disconnected. Try again."},
                         "errors: %r" % self.R.get("err"))

    def test_the_light_theme_keeps_the_head_dot_s_state_colors(self):
        # a bare light rule on the dot would outrank the state rules, so the detail's headline dot would read the
        # label gray while the rail's id-scoped dot kept amber and red
        amber, red, gray = "rgb(230, 126, 34)", "rgb(229, 72, 77)", "rgb(93, 87, 78)"
        self.assertEqual(self.R["lightDegraded"], {"rail": amber, "head": amber, "headOpacity": "1"}, "errors: %r" % self.R.get("err"))
        self.assertEqual(self.R["lightPaused"], {"rail": red, "head": red})
        self.assertEqual(self.R["lightOkHead"], {"head": gray, "headOpacity": "0.55"})

    def test_the_driver_s_second_phase_hit_no_error(self):
        self.assertEqual(self.R["err2"], {})

    def test_tab_cycles_within_the_open_dialog(self):
        self.assertTrue(self.R["tabOpened"], "errors: %r" % self.R.get("err2"))
        self.assertEqual(self.R["tabAria"], "true")
        self.assertEqual(self.R["tabSeq"], ["BUTTON.pause", "DIV.reveal", "SPAN.usage", "SPAN.log", "BUTTON.pause", "DIV.reveal"])
        self.assertEqual(self.R["tabBack"], ["BUTTON.pause", "SPAN.log", "SPAN.usage"])

    def test_enter_on_a_row_opens_its_session_and_space_on_a_footer_link_runs_it(self):
        self.assertEqual(self.R["rowFocused"], "DIV.reveal", "errors: %r" % self.R.get("err2"))
        self.assertEqual(self.R["enterRowSent"], ["openSession:" + SID + "1"])
        self.assertTrue(self.R["enterRowClosed"])
        self.assertEqual(self.R["focusAfterFrame"], "DIV.reveal", "a frame re-renders the card; focus stays on the same row")
        self.assertEqual(self.R["usageOpened"], 1)
        self.assertTrue(self.R["usageClosedTip"])

    def test_a_keyboard_press_on_the_pause_button_keeps_focus_inside_the_dialog(self):
        self.assertEqual(self.R["pressFocusBefore"], "BUTTON.pause", "errors: %r" % self.R.get("err2"))
        self.assertEqual(self.R["pressSent"], ["setGlobalRetryPaused:false"], "Space ran the button once")
        self.assertEqual(self.R["pressButton"], {"disabled": True, "label": "Stop all auto-retries"}, "acknowledged")
        self.assertEqual(self.R["pressFocusAfter"], "DIV", "focus is on the card, inside the dialog, not on BODY")
        self.assertEqual(self.R["pressShiftTab"], "SPAN.log", "the trap still applies: Shift+Tab wraps to the last control")
        self.assertEqual(self.R["pressTabBack"], "DIV.reveal", "Tab from the last control wraps to the first enabled one (the disabled button is skipped)")
        self.assertEqual(self.R["pressAnswerFocus"], "DIV", "the answering frame's re-render leaves focus on the card")
        self.assertEqual(self.R["pressAnswerTab"], "BUTTON.pause", "and the first Tab reaches the re-enabled button")

    def test_the_driver_s_socket_phase_hit_no_error(self):
        self.assertNotIn("socket", self.R["err2"], self.R["err2"].get("socket"))

    def test_a_press_the_socket_lost_is_cleared_on_the_close_and_the_redial_repaints_the_truth(self):
        # pending is cleared by a failed send or a frame with a moved seq; the redial's ready re-sends the last
        # frame verbatim, so a press the kernel never received would keep the button disabled and relabeled across
        # the reconnect until an unrelated pause write moved the seq
        R = self.R
        ready, pressed = '{"type":"ready"}', '{"type":"setGlobalRetryPaused","value":false}'
        self.assertEqual(R["sockCount"], 1, "shellWS dialed once at load, and the shim let it stay unopened: %r" % R.get("err2"))
        self.assertIn("/ws?app=shell", R["sock0"]["url"])
        self.assertEqual(R["shellSendRestored"], "function", "the shell's own send survives the driver's stubs")
        self.assertEqual(R["sockRedialed"], 2, "a close redials")
        self.assertEqual(R["sock1Ready"], [ready], "the open sends ready")
        self.assertEqual(R["sockPainted"], "paused · usage limit · 1 waiting", "a frame on the socket paints the cell")
        self.assertEqual(R["sockPressSent"], [ready, pressed], "the press rode the real socket")
        self.assertEqual(R["sockAcked"], {"disabled": True, "label": "Stop all auto-retries", "acted": True, "hint": ""})
        self.assertEqual(R["sockDropped"], {"disabled": False, "label": "Resume all auto-retries", "acted": False,
                                            "hint": "Connection lost before the answer arrived. When it is back, the button shows the current state."},
                         "the close clears the acknowledgment and says why")
        self.assertEqual(R["sockDeadPress"], {"disabled": False, "label": "Resume all auto-retries", "acted": False,
                                              "hint": "Not sent: the dashboard is disconnected. Try again."},
                         "a press while no socket is open: the shell's own send refuses (shellSock is null until the redial opens)")
        self.assertIn("/ws?app=shell", R["sock2"]["url"])
        self.assertEqual(R["sock2Ready"], [ready], "the redial sends ready")
        self.assertEqual(R["sockResent"], {"disabled": False, "label": "Resume all auto-retries", "acted": False, "hint": ""},
                         "the re-sent frame (same seq) repaints the truth and drops the hint")
        self.assertEqual(R["sock2Sent"], [ready, pressed], "the next press rides the new socket")
        self.assertEqual(R["sock1After"], [ready, pressed], "and nothing more reached the dead one")
        self.assertEqual(R["sockMoved"], {"disabled": False, "label": "Stop all auto-retries", "acted": False, "hint": ""},
                         "a re-sent frame with a moved seq (the kernel took the press) repaints that truth")

    def test_only_a_primary_press_defers_a_frame(self):
        self.assertTrue(self.R["rightInside"], "the right press landed on the card; errors: %r" % self.R.get("err2"))
        self.assertTrue(self.R["rightHeldPainted"], "a right button arms nothing: the frame paints at once")
        self.assertTrue(self.R["rightReleasedPainted"])
        self.assertTrue(self.R["primaryInside"], "the primary press landed on the card")
        self.assertFalse(self.R["primaryHeldPainted"], "the primary press still defers")
        self.assertTrue(self.R["primaryReleasedPainted"], "and the release (outside any button, no click to wait for) paints it")


if __name__ == "__main__":
    unittest.main()
