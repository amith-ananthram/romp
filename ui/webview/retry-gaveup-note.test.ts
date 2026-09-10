// A retry storm that EXHAUSTS must stay loudly visible in history (the user 2026-07-25, from the
// solar-battery incident: 10 attempts failed, and the chat's lasting trace was a bland seam line — and
// worse, the durable note said "Recovered after 10 retries", because the CLI settles a dead turn with an
// error-text AssistantMessage that the backend mistook for real output). Two durable elements now exist:
//   - kind "retryGaveUp": the red rail note "API errors — gave up after N retries" where the storm died
//     (twin of the muted "Recovered after N retries" note);
//   - kind "apiErrorNote": the transcript's isApiErrorMessage record worn as red api-error chrome, not
//     an agent bubble — buttonless, because the LIVE card (kind "apiError") owns Retry while the session
//     is still blocked on that very record (the kernel swaps one for the other, never both).
// No jsdom harness for the chat renderer — pinned at the source level like postal-expand.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("both durable kinds exist in the event union and are dispatched", () => {
  assert.match(RENDER, /kind: "retryGaveUp"; retries: number; errorKind\?: string/);
  assert.match(RENDER, /kind: "apiErrorNote"; md: string; status\?: number/);
  assert.match(RENDER, /if \(ev\.kind === "retryGaveUp"\) return renderRetryGaveUp\(ev\);/);
  assert.match(RENDER, /if \(ev\.kind === "apiErrorNote"\) return renderApiErrorNote\(ev\);/);
});

test("the gave-up note is the red twin of the recovered note, and can never read as a recovery", () => {
  const fn = RENDER.slice(RENDER.indexOf("function renderRetryGaveUp"),
                          RENDER.indexOf("function renderApiErrorNote"));
  // 2026-09-08 (the notice-vocabulary pass): the err severity puts the red on the rail + dot; the words stay --fg
  assert.match(fn, /sev: "err", gist: `gave up after \$\{n\}/);      // says what actually happened, in the error severity
  assert.doesNotMatch(fn, /recovered/i, "the failed storm's note must never borrow the recovery wording");
  assert.match(CSS, /\.notice-sev-err\s+\{ --notice-rail: var\(--st-blocked-bg\);\s+--notice-dot: var\(--st-blocked-bg\); \}/);
  assert.doesNotMatch(CSS, /\.gaveup-text/);
});

test("the durable error card wears the live card's chrome but carries NO buttons", () => {
  const fn = RENDER.slice(RENDER.indexOf("function renderApiErrorNote"),
                          RENDER.indexOf("function renderEffortApplied"));
  // 2026-09-08: the same API notice as the live card (src API · plug glyph · err severity), no actions
  assert.match(fn, /notice\(\{ src: "API", glyph: "api", sev: "err", gist: ev\.status \? `error \$\{ev\.status\}` : "error", body,/);
  assert.doesNotMatch(fn, /noticeAct|acts:/, "history is not actionable — Retry lives on the LIVE card only");
});
