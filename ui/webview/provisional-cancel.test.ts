// T244 (the user 2026-09-07, ~10:10 AM PT): ✕ on a message still showing "sending…" put the text back in the
// composer, but the provisional sending bubble did NOT disappear. Reproduced by reading (the served harness
// showed the parked-send path clean: ✕ → bubble gone, kernel cancelResult ok:true, nothing re-injected). The
// long-lived "sending…" is the PROVISIONAL tab's: a send into a session still being created goes into
// provisionalQueue and shows the bare group for the whole creation window (up to 90s). The ✕ dropped only the
// optimistic entry (pendingSent) and posted a cancel for a sid the kernel has never heard of; adoption then
// re-sent every queued text — the cut message included — and re-registered its bubble under the real tab,
// while the ✕'s restore had already put the same text in the composer: a double-send waiting to happen.
// Fix: a ✕ on a provisional-tab bubble forgets the text from provisionalQueue too and posts no kernel cancel
// (there is nothing there to cancel); adoption re-sends only what remains. Plus the evidence the report lacked:
// a client-diag row for every cancelResult ok:false and for a provisional-stage ✕, and a kernel log line for a
// body-only cancel that found nothing.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("a ✕ on a provisional-tab bubble forgets the send from provisionalQueue and posts no kernel cancel (T244)", () => {
  const qx = RENDER.split("    qx: (el) => {")[1].split("\n    },\n")[0];
  assert.match(RENDER, /function forgetProvisionalSend\(text: string\): boolean \{/);
  assert.match(qx, /const provisional = isProvisionalId\(sidQ\);/);
  assert.match(qx, /if \(provisional && qmd\) forgetProvisionalSend\(qmd\);/, "the text must not come back through adoption");
  assert.match(qx, /if \(!provisional\) vscodeApi\.postMessage\(msg\);/, "nothing at the kernel to cancel for a session that does not exist yet");
  // the restore to the composer stays — the message never left the client; no cancelResult will come, so no stash is kept
  assert.match(qx, /restoreToComposer\(edx && edx\.open && edx\.text\.trim\(\) \? edx\.text : qmd\);/);   // the edited words when a field was open (T306)
  assert.match(qx, /if \(!provisional\) pendingCancelRestores\.set\(activeId \+ " " \+ qmd,/);
  // adoption re-sends only what remains in the queue (unchanged: it reads provisionalQueue via dropProvisional)
  assert.match(RENDER, /for \(const text of queued\) \{\s*\n\s*const qid = mintQid\(\);[^\n]*\n\s*vscodeApi\?\.postMessage\(\{ type: "sendMessage", id: realId, text, qid \}\);\s*\n\s*registerOptimistic\(realId, text, undefined, qid\);/);
});

test("forgetProvisionalSend removes exactly one matching text and reports whether it did (executed)", () => {
  const src = "function forgetProvisionalSend(text) {" + RENDER.split("function forgetProvisionalSend(text: string): boolean {")[1].split("\n}")[0] + "\n}";
  const run = (queue: string[], text: string) => new Function("provisionalQueue", "text", src + "\nconst ok = forgetProvisionalSend(text); return { ok, queue: provisionalQueue };")(queue, text);
  assert.deepEqual(run(["a", "b", "a"], "a"), { ok: true, queue: ["b", "a"] }, "one press forgets one send");
  assert.deepEqual(run(["a", "b"], "zzz"), { ok: false, queue: ["a", "b"] });
  assert.deepEqual(run([], "a"), { ok: false, queue: [] });
});

test("every cancel that misses leaves evidence: a client-diag row and a kernel log line (T244)", () => {
  // client: cancelResult ok:false → a clientDiag breadcrumb (surface chat, what cancel-miss), body length only — never the text
  const cr = RENDER.split('else if (m.type === "cancelResult" && typeof m.id === "string") {')[1].split("\n  }\n")[0];
  assert.match(cr, /vscodeApi\?\.postMessage\(\{ type: "clientDiag", surface: "chat", what: "cancel-miss",/);
  assert.match(cr, /data: \{ sid: m\.id, mdLen: typeof m\.md === "string" \? m\.md\.length : -1, hadRestore: !!stash \} \}/,
    "sid, body LENGTH, restore flag — never the body, and never the kernel's refusal sentence (it quotes a slash-led body's first token)");
  assert.doesNotMatch(cr, /what: "cancel-miss",[\s\S]{0,300}?\bmd: m\.md\b|what: "cancel-miss",[\s\S]{0,300}?\btext: m\.text/, "no user content on a diag row");
  // client: a provisional-stage ✕ leaves its own breadcrumb, so the next report says which path it was
  const qx = RENDER.split("    qx: (el) => {")[1].split("\n    },\n")[0];
  assert.match(qx, /vscodeApi\.postMessage\(\{ type: "clientDiag", surface: "chat", what: "cancel-provisional",/);
  // kernel: a body-only cancel that found nothing is logged (sid only, never the body)
  const arm = KERNEL.split('elif t == "cancelQueued" and msg.get("md"):')[1].split("\n    elif ")[0];
  assert.match(arm, /sys\.stderr\.write\("queued-cancel miss: %s \(body-only\)\\n" % sid\)/);
});
