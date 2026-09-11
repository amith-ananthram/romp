// "Retry now" on an API-error card (the user 2026-07-06): on the SDK backend it did NOTHING and gave no
// feedback — because the kernel's apiRetry gate (global pause / a thread the user interrupted) blocked the
// SAME message the AUTO-retry loop uses, and the button never acknowledged the click. Now the manual click
// posts manual:true (an explicit override the kernel fires past the gate) AND acknowledges at once (disabled
// + "Retrying…", self-restoring). render.ts has no jsdom harness → source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
// isolate the Retry button's click handler — since 2026-09-08 (the notice-vocabulary pass) it lives on the
// document.body delegate (apiRetryNow), not on a per-node listener the tail rebuild destroyed mid-press
const H = (RENDER.match(/apiRetryNow: \(el\) => \{[\s\S]*?\},\n    stopAllRetries:/) || [""])[0];   // the next delegate act (Dismiss dialog went with the terminal backend, T331)

test("Retry now posts an explicit MANUAL override so it fires even when auto-retry is paused/suppressed", () => {
  assert.ok(H, "found the Retry button block");
  assert.match(H, /vscodeApi\.postMessage\(\{ type: "apiRetry", id: own, manual: true \}\)/);   // owner-scoped: inside the popover the card acts on the THREAD (the parity bundle, 2026-08-26)
  // 2026-09-08 (the notice-vocabulary pass): the button is a data-act word button (noticeAct) and the handler
  // lives on the document.body delegate — the per-node listener the tail rebuild destroyed mid-press is gone
  assert.match(RENDER, /acts\.push\(noticeAct\("Retry now", "apiRetryNow",/);
});

test("Retry now acknowledges the click immediately (disabled + 'Retrying…'), then self-restores", () => {
  assert.match(H, /b\.disabled = true;/);
  assert.match(H, /b\.textContent = "Retrying…";/);
  assert.match(H, /setTimeout\(\(\) => \{ if \(b\.isConnected\) \{ b\.disabled = false; b\.textContent = "Retry now"; \} \}/);
});

test("the AUTO-retry tick stays a plain apiRetry (no manual) so the pause/suppression gate still holds it", () => {
  // only the button overrides; the 10s auto-loop must remain gated
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "apiRetry", id \}\)/);   // apiRetryTick's post — no manual flag
});

test("a usage-limit pause counts down to the window reset instead of a mute label (the user 2026-07-13)", () => {
  // the kernel's globalRetryPaused push carries resumeAt (the limiting window's reset epoch, seconds)
  assert.match(RENDER, /globalRetryResumeAt = typeof m\.resumeAt === "number" \? m\.resumeAt : null;/);
  assert.match(RENDER, /usage limit — retrying at \$\{hm\} \(in \$\{durLabel\(dt\)\}\)/);
  // the tick updates the countdown every second EVEN while paused (only the schedule loop is gated)
  assert.match(RENDER, /if \(globalRetryPaused\) \{\s*\n\s*cd\.textContent = retryPausedText\(\);/);
  // the LIVE (newest) error card ticks — older cards in the transcript are settled history
  assert.match(RENDER, /const cd = cds\.length \? \(cds\[cds\.length - 1\] as HTMLElement\) : null;/);
});

// ── the cadence is the KERNEL's (the user 2026-07-29) ───────────────────────────────────────────────
// A usage-limited thread was collecting a ridiculous number of attempts: one-per-episode stops retries
// stacking, but every failure writes a new error record, so a new episode, so the 10s tick retried for
// as long as the block lasted. The kernel now backs each outage off to at most half an hour and
// publishes when the next attempt may fire; this view follows that instead of its own tick.
test("the tick holds off while the kernel's deadline stands, and adopts it as the countdown", () => {
  assert.match(RENDER, /const kernelNext = sessions\.get\(id\)\?\.status\.retryNextAt;/);
  assert.match(RENDER, /apiRetryNext\.set\(id, kernelNext \* 1000\);/, "the countdown shows the real schedule");
  assert.match(RENDER, /if \(now < kernelNext \* 1000\) return;/, "no asking inside the window");
  // no deadline published (a fresh block, an older kernel) → the local 10s tick still makes the first ask
  assert.match(RENDER, /\} else if \(!apiRetryNext\.has\(id\)\) \{\s*\n\s*apiRetryNext\.set\(id, now \+ API_RETRY_MS\);/);
});

test("a long wait reads in minutes, with the attempt count explaining the gap", () => {
  // "retrying in 1750s" is not a readable wait, and a silent 30-minute gap reads as stuck rather than
  // as deliberate — the count is what says the backoff has stepped up
  // 2026-09-08: the ONE duration format (duration.ts durLabel — "2m 30s"), not a private minutes rule
  assert.match(RENDER, /cd\.textContent = `retrying in \$\{durLabel\(left\)\}`/);
  assert.match(RENDER, /tries > 1 \? ` · \$\{tries\} tries so far` : ""/);
});

test("the status carries the schedule", () => {
  assert.match(RENDER, /retryNextAt\?: number \| null; retryTries\?: number \| null;/);
});
