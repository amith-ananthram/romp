// Dismiss-dialog action (the user 2026-07-16): a tmux session parked on the CLI's spend-cap modal can't
// be unblocked by Retry — the menu eats the injected "retry" as keystrokes. The chat's spend-cap error
// card therefore drops Retry and, on tmux, offers "Dismiss dialog" (→ dismissDialog drive op → the kernel
// verifies the menu is up, then sends Esc; cancel, never a billing change). An SDK spend-cap card shows
// no dead button, just the raise-your-cap line. render.ts has import-time DOM side effects → source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const R = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const K = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

// isolate renderApiError's body so the pins can't accidentally match elsewhere in the file
const apiErr = R.slice(R.indexOf("function renderApiError"), R.indexOf("// ── API-error auto-retry"));

test("a spend cap suppresses the Retry button entirely", () => {
  // a safeguards refusal joins the no-Retry classes (the user 2026-08-15) — see apierror-refusal.test.ts
  assert.match(apiErr, /const spendCap = !!st\?\.apiSpendLimit \|\| !!st\?\.apiModelLimit \|\| !!st\?\.apiAuthErr \|\| refusal;/);
  // 2026-09-08 (the notice-vocabulary pass): the Retry is a notice word button, pushed only off the spend-cap branch
  assert.match(apiErr, /if \(!spendCap\) \{\s*\n\s*acts\.push\(noticeAct\("Retry now", "apiRetryNow",/);
});

test("a tmux spend cap offers Dismiss dialog, posting the dismissDialog op", () => {
  // refusal-gated: the Esc-sender is for the CLI's spend-limit dialog — a refusal parks no menu
  assert.match(apiErr, /\} else if \(st\?\.backend === "tmux" && !refusal\) \{/);
  assert.match(apiErr, /acts\.push\(noticeAct\("Dismiss dialog", "dismissDialog",/);
  // the handler on the body delegate (2026-09-08), owner-scoped (the parity bundle, 2026-08-26)
  assert.match(R, /dismissDialog: \(el\) => \{[\s\S]{0,200}?vscodeApi\.postMessage\(\{ type: "dismissDialog", id: owningSidOf\(b\) \}\)/);
  // acknowledges the click at once (disable + "Dismissing…"), like every other card control
  assert.match(R, /dismissDialog: \(el\) => \{[\s\S]{0,400}?b\.textContent = "Dismissing…";/);
});

test("an SDK spend cap shows neither Retry nor a Dismiss button (raise-the-cap line only)", () => {
  // the Dismiss branch is tmux-gated, so an SDK spend cap falls through with no button appended
  assert.match(apiErr, /\} else if \(st\?\.backend === "tmux" && !refusal\) \{[\s\S]*?acts\.push\(noticeAct\("Dismiss dialog"[\s\S]*?\n  \}/);
});

test("the countdown reads the spend-cap message, never a fake retry countdown", () => {
  assert.match(apiErr, /else if \(spendCap\) countdown\.textContent = "spend limit reached — raise it at claude\.ai\/settings\/usage";/);
});

// kernel contract this card codes against (node-tests-pin-kernel-source precedent)
test("kernel: dismissDialog is a drive op backed by TmuxBackend.dismiss_dialog verifying the modal", () => {
  assert.match(K, /elif t == "dismissDialog":/);
  assert.match(K, /def dismiss_dialog\(self, sid\):/);
  assert.match(K, /def _spend_dialog_showing\(pane\):/);
  assert.match(K, /self\.send_keys\(name, "Escape"\)/);   // Esc, never Enter
});
