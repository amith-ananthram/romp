// Closing a chat tab is OPTIMISTIC and STAYS that way (the user 2026-07-24): the tab goes the instant you
// confirm, the shutdown runs behind it, and only a close that genuinely didn't take says anything.
//
// The bug: dismissSession already dropped the tab on click, but nothing recorded the close locally. The
// kernel goes on listing the session for a push or two (closeTab flips a hidden flag, then a full rebuild),
// and applyTabOrder adopts the kernel's list VERBATIM — so the id came straight back. With its session
// already dropped client-side there was nothing to render but a tabs-first placeholder: the closed tab faded
// back in wearing the spinning romp swirl and sat there, reading as a shutdown you had to wait out.
//
// The executed model below is the whole close/ack/backstop cycle; the source pins hold the wiring in render.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

import { hostOf } from "./host-prefix";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const CLOSE_ACK_MS = 15_000;

// A stand-in for the client's tab state around a close: the kernel's last pushed list, the ids we know a
// session for, and the just-closed set. Mirrors render.ts — closeTabLocally records + drops, ackClosingTabs
// settles against each kernel push, and the strip skips anything still in `closing`.
function model(now = 0) {
  const kernelList: string[] = [];
  const known = new Set<string>();
  const closing = new Map<string, number>();
  const warned: string[] = [];
  let clock = now;
  return {
    warned,
    tick(ms: number) { clock += ms; },
    session(id: string) { known.add(id); if (!kernelList.includes(id)) kernelList.push(id); },
    // the ✕: post closeTab, record it, drop the session locally (dismissSession)
    close(id: string) { closing.set(id, clock); known.delete(id); },
    // a kernel tabOrder push — `list` is what the kernel currently believes is open. `report` is the
    // frame's provenance under federation (T233): a synthetic re-emit (`reemit`) or the one host whose
    // own push drove a fresh emission (`freshHost`); a frame straight from the kernel carries neither.
    push(list: string[], report?: { reemit?: boolean; freshHost?: string }) {
      for (const [id, ts] of Array.from(closing)) {
        if (!list.includes(id)) { closing.delete(id); continue; }        // kernel agreed → confirmed
        if (clock - ts < CLOSE_ACK_MS) continue;                          // still in flight
        // only a FRESH report from the OWNING kernel may call the close refused
        if (report && (report.reemit || (typeof report.freshHost === "string" && hostOf(id) !== report.freshHost))) continue;
        closing.delete(id);
        warned.push(id);                                                  // the close plainly didn't take
      }
      kernelList.length = 0;
      for (const id of list) kernelList.push(id);
    },
    // what the strip actually draws: the kernel's ids minus the ones we just closed. An id with no session
    // behind it is the PLACEHOLDER case — the swirl — so it's called out separately.
    tabs() { return kernelList.filter((id) => !closing.has(id)); },
    placeholders() { return kernelList.filter((id) => !closing.has(id) && !known.has(id)); },
  };
}

test("the closed tab does NOT come back as a swirling placeholder on the next push", () => {
  const m = model();
  m.session("web"); m.session("api"); m.session("tests");
  m.close("api");
  assert.deepEqual(m.tabs(), ["web", "tests"], "gone the instant it's confirmed");
  // the kernel hasn't caught up yet — this push still lists api. Before the fix this re-added it, and with
  // no session behind it the strip drew the loading placeholder.
  m.push(["web", "api", "tests"]);
  assert.deepEqual(m.placeholders(), [], "no romp-swirl placeholder for a tab we just closed");
  assert.deepEqual(m.tabs(), ["web", "tests"], "still gone while the shutdown runs behind us");
  // the kernel finally drops it → the close is acked and nothing is suppressed any more
  m.push(["web", "tests"]);
  assert.deepEqual(m.tabs(), ["web", "tests"]);
  assert.deepEqual(m.warned, [], "an ordinary close says nothing at all");
});

test("a close that never takes surfaces an error and lets the tab back", () => {
  const m = model();
  m.session("web"); m.session("api");
  m.close("api");
  m.tick(CLOSE_ACK_MS - 1);
  m.push(["web", "api"]);
  assert.deepEqual(m.warned, [], "still within the ack window — a slow kernel isn't a failure");
  assert.deepEqual(m.tabs(), ["web"]);
  m.tick(2);
  m.push(["web", "api"]);
  assert.deepEqual(m.warned, ["api"], "past the backstop the close plainly didn't take — say so");
  assert.deepEqual(m.tabs(), ["web", "api"], "…and the tab returns rather than hiding a live session");
});

test("the ack is the kernel DROPPING the id, not the elapsed time", () => {
  const m = model();
  m.session("web"); m.session("api");
  m.close("api");
  m.push(["web"]);                       // acked immediately, well inside the window
  m.tick(CLOSE_ACK_MS * 10);             // …so no amount of later time can raise a false alarm
  m.push(["web"]);
  assert.deepEqual(m.warned, []);
  // and a session the kernel re-opens for real (revive) is drawn again — the suppression was cleared on ack
  m.session("api");
  m.push(["web", "api"]);
  assert.deepEqual(m.tabs(), ["web", "api"]);
});

test("a session dying on its OWN is not recorded as a close of ours", () => {
  // dismissSession is shared by both paths; only the ✕ goes through closeTabLocally. If a death were
  // recorded as a close, the backstop could later warn about a session nobody asked to close.
  const m = model();
  m.session("web"); m.session("api");
  m.push(["web", "api"]);
  // the kernel's `closed` event: drop it, record nothing
  m.session("web");
  m.push(["web"]);
  m.tick(CLOSE_ACK_MS * 2);
  m.push(["web"]);
  assert.deepEqual(m.warned, []);
});

// ---- T233 (the user 2026-09-03): the false "Couldn't close X" toast ------------------------------------
// The kernel had killed the session within the same second; the toast fired anyway, because the ONLY
// confirmation the client accepts is a tabOrder without the id, and a STALE order still listing it
// reached the client past 15s: federation.ts re-emits a synthetic tabOrder from its STORED per-host
// slices on any view-order storage event / host attach or drop, and nothing updated that store between
// the kill and the host's next cycle push (20-40s on a loaded box). Now the kernel confirms off-cycle on
// the kill itself, the `closed` frame folds the id out of the store, and a re-emit can confirm a close
// (absence) but never call one refused.
test("a synthetic re-emit still listing the id past the backstop does NOT toast and does NOT restore the tab", () => {
  const m = model();
  m.session("web"); m.session("api");
  m.close("api");
  m.tick(CLOSE_ACK_MS + 1000);
  m.push(["web", "api"], { reemit: true });          // a stored slice re-served — not the kernel's word
  assert.deepEqual(m.warned, [], "a re-emit is never evidence that the kernel still has the tab");
  assert.deepEqual(m.tabs(), ["web"], "the suppression holds until the owner's own report");
  // …and the honest failure path stays: the owning kernel's FRESH order still listing it past the backstop
  m.push(["web", "api"], { freshHost: "" });
  assert.deepEqual(m.warned, ["api"], "the kernel's own fresh word past the backstop = the close did not take");
  assert.deepEqual(m.tabs(), ["web", "api"]);
});

test("another host's fresh push says nothing about this id's kernel; a re-emit still CONFIRMS by absence", () => {
  const m = model();
  m.session("web"); m.session("TESTHOST:api");
  m.close("TESTHOST:api");
  m.tick(CLOSE_ACK_MS + 1000);
  m.push(["web", "TESTHOST:api"], { freshHost: "" });   // the LOCAL kernel's push, TESTHOST's slice riding along from the store
  assert.deepEqual(m.warned, [], "not the owning host's report");
  assert.deepEqual(m.tabs(), ["web"]);
  m.push(["web"], { reemit: true });                    // the `closed` fold's re-emit: the id is gone from the store
  assert.deepEqual(m.tabs(), ["web"]);
  m.tick(CLOSE_ACK_MS * 10);
  m.push(["web", "TESTHOST:api"], { freshHost: "TESTHOST" });   // a genuine revive on TESTHOST later shows again
  assert.deepEqual(m.warned, [], "the suppression was retired by the absence, not by time — no false alarm");
  assert.deepEqual(m.tabs(), ["web", "TESTHOST:api"]);
});

test("the wiring: applyTabOrder hands the frame's provenance to the backstop, which toasts only on the owner's fresh word", () => {
  assert.match(RENDER, /type OrderReport = \{ reemit\?: boolean; freshHost\?: string \} \| undefined;/);
  assert.match(RENDER, /function ackClosingTabs\(kernelOrder: readonly string\[\], report\?: OrderReport\): void/);
  assert.match(RENDER, /if \(report && \(report\.reemit \|\| \(typeof report\.freshHost === "string" && hostOf\(id\) !== report\.freshHost\)\)\) continue;/);
  assert.match(RENDER, /applyTabOrder\(m\.order, m\.tabs, \{ reemit: m\.reemit === true, freshHost: typeof m\.freshHost === "string" \? m\.freshHost : undefined \}, m\.live\);/);   // + the frame\'s live set (T258)
  // confirm-on-absence is untouched: it acts BEFORE the provenance gate, on any order
  assert.match(RENDER, /if \(!live\.has\(id\)\) \{ closingTabs\.delete\(id\); continue; \}[\s\S]{0,120}?if \(now - ts < CLOSE_ACK_MS\) continue;[\s\S]{0,900}?if \(report && /);
});

// ---- the wiring in render.ts -------------------------------------------------------------------------

test("closeTabLocally drops the tab, THEN records the close — in that order", () => {
  // (2026-07-30: a PROVISIONAL tab short-circuits above this — there is no session to close, so the ✕
  // means "never mind" and cancels the spawn instead. A real tab's path is unchanged.)
  //
  // The order is the whole mechanism (the user 2026-08-02, closed tabs lingering as the spinning swirl):
  // this shipped as set-then-dismiss while dismissSession opened with closingTabs.delete(id), so the
  // record was erased the instant it was written — the executed model above passed while the composed
  // wiring was a no-op. Hence these pins hold the ORDER, and the next test holds dismissSession's hands.
  assert.match(RENDER, /return;\s*\n\s*\}\s*\n\s*dismissSession\(id, "close"\);\s*\n\s*closingTabs\.set\(id, Date\.now\(\)\);/,
    "the provisional short-circuit returns above; a real tab still dismisses THEN records");
  // declared beside tabMeta, NOT down by dismissSession: renderTabs reads it and can run before the module
  // finishes evaluating, which would make a `const` down there a temporal-dead-zone throw.
  assert.match(RENDER, /const tabMeta = new Map[\s\S]{0,900}?const closingTabs = new Map<string, number>\(\);/);
  assert.match(RENDER, /const CLOSE_ACK_MS = 15_000;/);
});

test("the strip skips a just-closed tab on BOTH passes (order AND the tabMeta placeholder pass)", () => {
  // the tabMeta pass is the one that drew the swirl: an id the kernel still lists with no session behind it
  assert.match(RENDER, /for \(const id of order\) \{ if \(!seen\.has\(id\) && stripLists\(id\)\)/);
  assert.match(RENDER, /for \(const id of tabMeta\.keys\(\)\) \{ if \(!seen\.has\(id\) && stripLists\(id\)\)/);
  assert.match(RENDER, /function stripLists\(id: string\): boolean \{\s*\n\s*return !closingTabs\.has\(id\) && \(order\.includes\(id\) \|\| tabMeta\.has\(id\)\);/, "the closing set is read through the strip's one membership rule (T357 fix)");
});

test("every close path is optimistic — the in-page ✕, a dead read-only tab, and the kernel's confirmClose", () => {
  assert.match(RENDER, /closeTab", id \}\);\s*\n\s*closeTabLocally\(id\);/, "the in-page ✕ confirm");
  // the dead-tab ✕: still optimistic, with the closeTab post skipped for a failed provisional id the
  // kernel never knew (2026-08-08 — the failed tab now lingers holding its text)
  assert.match(RENDER, /if \(el\.dataset\.dead === "1"\) \{\s*\n\s*if \(!isProvisionalId\(id\)\) vscodeApi\.postMessage\(\{ type: "closeTab", id \}\);\s*\n\s*closeTabLocally\(id\);\s*\n\s*return;/);
  // the kernel-driven confirmClose modal used to post and then just sit there waiting for the push
  assert.match(RENDER, /m\.type === "confirmClose"[\s\S]*?closeTabLocally\(m\.id\);/);
});

test("ackClosingTabs settles against the kernel's list on every tabOrder push", () => {
  assert.match(RENDER, /ackClosingTabs\(kernelOrder, report\);/);
  assert.match(RENDER, /function ackClosingTabs\(kernelOrder: readonly string\[\], report\?: OrderReport\): void/);
  assert.match(RENDER, /if \(!live\.has\(id\)\) \{ closingTabs\.delete\(id\); continue; \}/, "gone from the kernel = confirmed");
  assert.match(RENDER, /if \(now - ts < CLOSE_ACK_MS\) continue;/, "inside the window a slow kernel is not a failure");
  assert.match(RENDER, /warnToast\(`Couldn't close/);
});

test("dismissSession never touches the suppression — retiring belongs to ack, backstop, and reopen", () => {
  // dismissSession is the shared drop path: the ✕ runs through it microseconds after recording the close,
  // and under federation the kernel's `closed` event can predate stale merged frames that still list the
  // id. A closingTabs.delete in its body is what disarmed the whole optimistic close (see above).
  const body = RENDER.match(/function dismissSession\(id: string, why: DismissWhy, doomed\?: ReadonlySet<string>\): void \{[\s\S]*?\n\}/);
  assert.ok(body, "dismissSession not found");
  assert.doesNotMatch(body![0], /closingTabs\./, "dismissSession must not read or write closingTabs");
  // …and the one legitimate early retire: an explicit reveal (reopen from the picker inside the ack
  // window) must show the tab at once instead of waiting out the suppression
  assert.match(RENDER, /m\.type === "focus"\) \{\s*\n\s*revealSelfPane\(\);.*\n\s*closingTabs\.delete\(m\.id\);/);
});
