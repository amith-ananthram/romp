// A NEVER-DELIVERED send (the user 2026-07-29): an optimistic input echo whose CLI died holding it is
// durable BY DESIGN (the loss must show), but it used to render as an ordinary sent bubble — a two-day-old
// lost message kept resurfacing mid-chat, hopping turns as new ones landed, its stale timestamp reading as
// a glitch. The fix threads the backend's `dropped` marking through the kernel event (ev.undelivered) into
// an explicit "never delivered" treatment with restore/dismiss. The renderer has no jsdom harness, so pin
// the wiring at source (the romp-bubble.test.ts pattern); backend behavior lives in
// tests/test_sdk_echo_durability.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("a user ChatEvent can carry the undelivered flag and its restart-stable dismiss handle", () => {
  assert.match(RENDER, /kind: "user"; [^\n]*undelivered\?: boolean/);
  assert.match(RENDER, /kind: "user"; [^\n]*echoT\?: number/);
});

test("the kernel threads a dropped echo into ev.undelivered + echoT", () => {
  assert.match(KERNEL, /if a\.get\("dropped"\) and a\.get\("_echo_text"\):/);
  assert.match(KERNEL, /ev\["undelivered"\] = True/);
  assert.match(KERNEL, /ev\["echoT"\] = a\.get\("t"\)/);
});

test("the kernel exposes dismissEcho as a drive op routed to the backend", () => {
  assert.match(KERNEL, /"cancelQueued", "dismissEcho", "apiRetry"/, "dismissEcho joins ID_OPS");
  assert.match(KERNEL, /elif t == "dismissEcho" and hasattr\(be, "dismiss_echo"\):/);
  assert.match(KERNEL, /be\.dismiss_echo\(sid, uuid=/);
});

test("an undelivered bubble is marked and carries the note, not just a plain sent bubble", () => {
  assert.match(RENDER, /if \(ev\.undelivered\) \{/);
  assert.match(RENDER, /turn\.classList\.add\("undelivered"\)/);
  assert.match(RENDER, /bubble\.classList\.add\("undelivered-bubble"\)/);
  assert.match(RENDER, /el\("div", "undelivered-note"\)/);
  assert.match(RENDER, /label\.textContent = "never delivered"/);
});

test("both actions ride the body delegate (click-safe), never a per-render listener", () => {
  // the buttons carry data-act; the handlers live in the delegate map next to qx/nudgetoggle
  // 2026-09-08 (the notice-vocabulary pass): both are notice word buttons (noticeAct → data-act only)
  assert.match(RENDER, /const re = noticeAct\("copy to composer", "echorestore",/);
  assert.match(RENDER, /const dx = noticeAct\("dismiss", "echodismiss",/);
  assert.match(RENDER, /echorestore: \(el\) => \{/);
  assert.match(RENDER, /echodismiss: \(el\) => \{/);
});

test("restore hands the only surviving copy of the text back to the composer", () => {
  assert.match(RENDER, /echorestore: \(el\) => \{[\s\S]*?restoreToComposer\(t\)/);
});

test("dismiss posts dismissEcho with the restart-stable handle and acknowledges the click", () => {
  const m = RENDER.match(/echodismiss: \(el\) => \{[\s\S]*?\n    \},/);
  assert.ok(m, "the echodismiss handler exists");
  assert.match(m![0], /type: "dismissEcho", id: activeId/);
  assert.match(m![0], /msg\.uuid = el\.dataset\.euuid/);
  assert.match(m![0], /msg\.t = Number\(el\.dataset\.et\)/);
  // optimistic removal = the immediate acknowledgement; the kernel's dismiss is idempotent
  assert.match(m![0], /\.closest\(".turn-user"\)[\s\S]*?\.remove\(\)/);
});

test("restore is offered only for the user's own words, dismiss for every flavor", () => {
  assert.match(RENDER, /if \(!romp && !injected && ev\.md\) \{[\s\S]{0,400}?echorestore/);
});

test("the loss treatment exists in CSS: dashed red-edged bubble + always-visible note", () => {
  assert.match(CSS, /\.user-bubble\.undelivered-bubble, \.romp-bubble\.undelivered-bubble \{/);
  assert.match(CSS, /\.undelivered-note \{[^}]*align-self: flex-end/);
  // 2026-09-08: the label is the --err token (the light theme has its own); the buttons are .notice-act; the bubble
  // keeps its SOLID --you fill (the 10% wash put white text on a tint — unreadable on cream)
  assert.match(CSS, /\.undelivered-label \{[^}]*color: var\(--err\)/);
  assert.match(CSS, /\.notice-act \{/);
  assert.doesNotMatch(CSS, /\.undelivered-act|\.user-bubble\.undelivered-bubble \{ background/);
  // the note is NOT hover-gated like .msg-edit — a loss notice is not a hover secret
  assert.doesNotMatch(CSS, /\.undelivered-note \{[^}]*opacity: 0/);
});
