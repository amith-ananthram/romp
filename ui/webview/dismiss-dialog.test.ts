// The chat's spend-cap error card (the user 2026-07-16): a billing cap cannot be lifted by a retry, so the card drops
// Retry and names the fix (raise the cap) with no dead button. The "Dismiss dialog" button that once served a
// terminal session parked on the CLI's spend-limit menu went with the terminal backend (T331, the user 2026-09-10).
// render.ts has import-time DOM side effects → source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

// isolate renderApiError's body so the pins can't accidentally match elsewhere in the file
const apiErr = R.slice(R.indexOf("function renderApiError"), R.indexOf("// ── API-error auto-retry"));

test("a spend cap suppresses the Retry button entirely", () => {
  // a safeguards refusal joins the no-Retry classes (the user 2026-08-15) — see apierror-refusal.test.ts
  assert.match(apiErr, /const spendCap = !!st\?\.apiSpendLimit \|\| !!st\?\.apiModelLimit \|\| !!st\?\.apiAuthErr \|\| refusal;/);
  // 2026-09-08 (the notice-vocabulary pass): the Retry is a notice word button, pushed only off the spend-cap branch
  assert.match(apiErr, /if \(!spendCap\) \{\s*\n\s*acts\.push\(noticeAct\("Retry now", "apiRetryNow",/);
});

test("a spend cap shows neither Retry nor a Dismiss button: the raise-the-cap line alone", () => {
  assert.doesNotMatch(apiErr, /Dismiss dialog|dismissDialog|backend === "tmux"/, "no terminal-only branch");
  assert.doesNotMatch(R, /dismissDialog/, "no delegate handler and no op posted for it");
  // anchored with no wildcard (review find): the Retry push, its closing brace and the next comment are adjacent
  // lines, so a re-added `else if` arm under any label fails this pin
  assert.match(apiErr, /if \(!spendCap\) \{\n    acts\.push\(noticeAct\("Retry now", "apiRetryNow"[^\n]*\n  \}\n  \/\/ Global auto-retry pause/, "the spend-cap arm appends no button");
});

test("the countdown reads the spend-cap message, never a fake retry countdown", () => {
  assert.match(apiErr, /else if \(spendCap\) countdown\.textContent = "spend limit reached — raise it at claude\.ai\/settings\/usage";/);
});
