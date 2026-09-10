// Pending (queued) messages render the SAME way they will once they land (the user 2026-06-27): as markdown,
// with the romp goal-context quote + comment markers stripped server-side, and — when the message resumed a
// goal — a compact "↩ Follow-up · <goal>" header. The same header renders on the landed user turn, so
// pending and landed match. Source pins (no jsdom for the chat render).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("queued payload is per-message {md, followUp?, goal?, fuCtx?}, not raw strings", () => {
  // `optimistic` (romp's own unconfirmed echo) rides along at the end — see optimistic-send.test.ts
  assert.match(SRC, /kind: "queued"; texts: \{ md: string; followUp\?: boolean; goal\?: string; fuCtx\?: string; idx\?: number; park\?: number; cancelable\?: boolean; optimistic\?: boolean; romp\?: boolean; rompSystem\?: boolean; rompAuto\?: boolean; gist\?: string; imgPaths\?: string\[\]; lost\?: string; qts\?: number; qid\?: string; held\?: boolean; hiddenByPending\?: boolean; landing\?: boolean \}\[\]/);   // gist: a queued romp notice's user-facing head (2026-09-08)
});

test("renderQueued renders markdown (not raw text) + the follow-up header", () => {
  // end anchor: the first renderApiError* AFTER renderQueued (renderApiErrorNote lives earlier in the file)
  const qStart = SRC.indexOf("function renderQueued");
  const fn = SRC.slice(qStart, SRC.indexOf("function renderApiError", qStart));
  assert.ok(fn.length > 0, "found renderQueued");
  assert.match(fn, /if \(t\.followUp && !t\.romp\) turn\.appendChild\(followUpHeader\(t\.goal, t\.fuCtx, t\.idx !== undefined \? "q:" \+ t\.idx : undefined\)\);/);   // a romp entry never wears the header (T243)
  assert.match(fn, /bubble\.innerHTML = userMd\(t\.md\);/);   // markdown, through the user-text renderer (newlines kept)
  assert.doesNotMatch(fn, /textContent = t\b/, "no more raw textContent dump");
});

test("a shared followUpHeader builds a ↩ Follow-up label with the goal title", () => {
  assert.match(SRC, /function followUpHeader\(goal\?: string, ctx\?: string, key\?: string\): HTMLElement/);
  assert.match(SRC, /lbl\.textContent = "↩ Follow-up"/, "a styleable span — a bare text node wrapped into a 3-line column");
  assert.match(SRC, /"followup-goal"/);
});

test("the landed user turn shows the same follow-up header (typed follow-ups only, not romp nudges)", () => {
  assert.match(SRC, /if \(ev\.followUp && !romp\) turn\.appendChild\(followUpHeader\(ev\.goal, ev\.fuCtx, ev\.uuid \? "u:" \+ ev\.uuid : undefined\)\);/);
});

test("the follow-up header is styled (accent label + dim goal)", () => {
  assert.match(CSS, /\.followup-tag \{[^}]*color: var\(--accent\)/);
  assert.match(CSS, /\.followup-goal \{[^}]*color: var\(--dim\)/);
});
