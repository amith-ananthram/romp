// The interrupt's whole arc is immediately responsive (the user 2026-07-02). Click the stop button →
// the CHIP flips to Interrupting… on the spot and the button + timer REMOVE themselves (the user
// 2026-07-05: the old ack stuffed the word "interrupting…" INTO the fixed-width button, overflowing
// onto the elapsed timer — the "4m 1interrupting…" screenshot; the chip, not the button, owns the
// state); the kernel's push then rebuilds the statusline in that same shape until the stop settles on
// disk and the chip reads READY again. The CLI's "[Request interrupted by user]" stop record renders
// as a slim rail marker in the compact-divider's language — never a person-blue bubble. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CHIP = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "status-chip.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the stop button acknowledges its click instantly: chip flips, button + timer vanish", () => {
  const fn = SRC.slice(SRC.indexOf("function stopButton"), SRC.indexOf("function updateStatusline"));
  assert.match(fn, /chip\.className = "chip chip-interrupting"; chip\.textContent = CHIP_LABEL\.interrupting;/,
               "the chip carries the state, optimistically, before the kernel push");
  assert.match(fn, /document\.getElementById\("work-timer"\)\?\.remove\(\);\s*\n\s*btn\.remove\(\);/,
               "the timer and the button remove themselves — nothing left to overflow or re-press");
  assert.doesNotMatch(fn, /stop-flash/, "the old 400ms flash (which left the button pressable) is gone");
  // the word never rides the fixed-width button again, and its dead styles are gone with it
  assert.doesNotMatch(fn, /textContent = "interrupting…"/, "no label is stuffed into the button");
  assert.doesNotMatch(SRC, /stop-busy/);
  assert.doesNotMatch(CSS, /\.stop-busy/);
  assert.doesNotMatch(CSS, /\.stop-btn:disabled/);
});

test("INTERRUPTING is a first-class chip state: labeled, styled, timerless, buttonless", () => {
  assert.match(CHIP, /"interrupting" \| "opening";/, "in the ChipState union (opening joined it 2026-08-05; the union lives in status-chip.ts since T322b)");
  assert.match(CHIP, /interrupting: "Interrupting…",/);   // CHIP_LABEL lives in status-chip.ts since T322b
  // the generic chip branch renders it; the stop button is drawn for working/compacting AND the stuck
  // retrying/blocked states — but NEVER for interrupting (the stop is already in flight)
  assert.match(SRC, /state === "working" \|\| s\.status\.state === "compacting"\s*\n\s*\|\| s\.status\.state === "retrying" \|\| s\.status\.state === "blocked"\) right\.appendChild\(stopButton\(s\.status\.state\)\)/);
  assert.doesNotMatch(SRC, /=== "interrupting"\) right\.appendChild\(stopButton/, "no stop button while interrupting");
  assert.match(CSS, /\.chip-interrupting \{ background: var\(--st-working-bg\);[^}]*opacity: 0\.75; \}/,
               "busy-yellow but dimmed + static — in flight, not still grinding");
});

test("the stop record renders as a slim SESSION notice, not a message bubble", () => {
  // 2026-09-08 (the notice-vocabulary pass): the ONE builder; the stop square is the notice's glyph (cause tied to
  // effect), the italic dropped — one information type, one weight
  assert.match(SRC, /if \(\(ev as any\)\.interruptMarker\) \{/);
  assert.match(SRC, /return notice\(\{ src: "session", glyph: "session", gist: interruptGist\(cause\), cls: "turn-interrupt",/);
  assert.match(SRC, /session: '<rect x="4" y="4" width="8" height="8" rx="1\.3"\/>'/, "the stop button's own glyph");
  assert.doesNotMatch(CSS, /\.interrupt-line|\.interrupt-square/);
  assert.match(CSS, /\.notice\.notice-slim \{/);
});
