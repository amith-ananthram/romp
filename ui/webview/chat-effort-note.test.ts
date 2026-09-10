// Durable "effort set to X" note in the chat (the user 2026-07-16): an /effort change reconnects the SDK
// session to apply --effort and leaves no transcript atom, so the synthesized /effort chip self-destructs on
// the next message and history kept no record of when effort changed. The kernel now interleaves a persistent
// `effortApplied` note by time; render.ts draws it with the same slim rail treatment as `retried`. Source
// pins (render.ts has import-time DOM side effects).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("effortApplied is a ChatEvent, dispatched to its own renderer", () => {
  assert.match(RENDER, /kind: "effortApplied"; effort: string; ts\?: string; uuid\?: string/);
  assert.match(RENDER, /ev\.kind === "effortApplied"\) return renderEffortApplied\(ev\)/);
});

test("renderEffortApplied is a slim SESSION notice — 'effort set to X', with the power glyph", () => {
  // 2026-09-08 (the notice-vocabulary pass): the ONE builder; head-only → slim (the rail-line density), the
  // explanation through the one tooltip (setTip), never a native title
  const body = RENDER.split("function renderEffortApplied(")[1].split("\nfunction ")[0];
  assert.match(body, /notice\(\{ src: "session", glyph: "power", gist: `effort set to \$\{ev\.effort\}`, cls: "turn-effort",/);
  assert.match(body, /tip: "reasoning effort is a connect-time setting/, "a tip explains the reconnect-to-apply + that this marks the apply moment");
  assert.doesNotMatch(body, /\.title = /);
});

test("the effort note and the retried note wear the ONE slim notice (one shared style, per the font rule)", () => {
  // 2026-09-08: .notice-slim is the head-only density every rail-line notice shares; the gist is the one 0.92em rung
  assert.match(CSS, /\.notice\.notice-slim \{ background: none; border: 0; padding: 2px 0; \}/);
  assert.match(CSS, /\.notice-gist \{[^}]*font-size: 0\.92em/);
  assert.doesNotMatch(CSS, /\.retried-line|\.effort-line/);
});
