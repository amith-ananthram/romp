// API-error auto-retry "Stop retrying" (the user 2026-06-24): the loop retried a blocked session every 10s
// with no off-switch. The card now has a Stop/Resume button that PAUSES this session's auto-retry (per
// instance) — it re-arms the moment the session recovers (no longer blocked). Source-level pin (the chat
// renderer has no jsdom harness).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the API-error card has a global Stop/Resume retry button that pauses retrying globally", () => {
  assert.match(R, /let globalRetryPaused = false/);
  // 2026-09-08 (the notice-vocabulary pass): a word button on the notice vocabulary, acting through the body delegate
  assert.match(R, /const stop = noticeAct\(paused \? "Resume all auto-retries" : "Stop all auto-retries", "stopAllRetries",/);
  assert.match(R, /stopAllRetries: \(\) => \{/);
  // clicking toggles the pause globally
  assert.match(R, /globalRetryPaused = !globalRetryPaused/);
  assert.match(R, /vscodeApi\.postMessage\(\{ type: "setGlobalRetryPaused", value: globalRetryPaused \}\)/);
});

test("the retry tick SKIPS all retries when paused globally", () => {
  // paused → the SCHEDULE loop is gated (the countdown text still ticks every second — a usage-limit
  // pause counts down to the window reset, the user 2026-07-13)
  assert.match(R, /if \(!globalRetryPaused\) \{/);
  assert.match(R, /if \(paused\) countdown\.textContent = retryPausedText\(\)/);
  assert.match(R, /return "auto-retry off \(global\)";/);   // a manual pause (no reset ETA) keeps the plain label
});

test("Stop retrying reads as a NEUTRAL action on the one notice button dress, never red", () => {
  // 2026-09-08: every notice action is .notice-act — dim rest, accent (pattern A) hover; no red buttons in the vocabulary
  assert.match(CSS, /\.notice-act \{[^}]*color: var\(--dim\)/);
  assert.match(CSS, /\.notice-act:hover:not\(:disabled\) \{ border-color: var\(--accent\); color: var\(--accent\); background: var\(--accent-wash\); \}/);
  assert.doesNotMatch(CSS, /\.apierror-stop|\.apierror-retry/);
});
