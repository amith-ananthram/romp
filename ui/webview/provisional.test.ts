// A new session shows its chat box immediately and starts behind it (the user 2026-07-30).
//
// Creating a session used to raise an "Opening session…" modal over the pane: the kernel resolved the
// directory, connected the session, and the first transcript poll came back — seconds you
// could do nothing with, watching three bouncing dots. Now the tab is there from the first click with a
// live composer; anything typed is HELD and flushed the moment the real session lands; and a create that
// fails says so in a dialog carrying the kernel's own words, instead of the cue silently timing out.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mintProvisionalId, isProvisionalId, provisionalName, adoptsProvisional, focusResolvesProvisional,
  PROVISIONAL_PREFIX } from "./provisional";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const PLACEHOLDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "pane-placeholder.ts"), "utf8");   // the empty pane's placeholder, by kind (T355)
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a provisional id carries NO colon — federation would read it as a host", () => {
  // this is the trap that swallowed follow-ups on remote cards: routing keys off id/sid and takes the
  // part before a colon as a host name, so a "pending:" form would address a machine called "pending"
  for (const seed of ["1a2b3c", "web:api", "2026-07-30T12:00:00.000Z", "…ünïcode…"]) {
    const id = mintProvisionalId(seed);
    assert.ok(!id.includes(":"), `${id} must not contain a colon`);
    assert.ok(id.startsWith(PROVISIONAL_PREFIX));
  }
});

test("distinct seeds mint distinct ids, and every one is recognisable as provisional", () => {
  const a = mintProvisionalId("aaa1"), b = mintProvisionalId("bbb2");
  assert.notEqual(a, b);
  assert.ok(isProvisionalId(a) && isProvisionalId(b));
});

test("a real session id is never mistaken for a provisional one", () => {
  assert.ok(!isProvisionalId("11111111-2222-3333-4444-555555555555"));
  assert.ok(!isProvisionalId("web:11111111-2222-3333-4444-555555555555"));
  assert.ok(!isProvisionalId(null));
  assert.ok(!isProvisionalId(undefined));
});

test("a remote create is matched against the HOST-PREFIXED name its tab arrives under", () => {
  assert.equal(provisionalName("web", "api"), "web:api");
  assert.equal(provisionalName("", "api"), "api", "a local one arrives bare");
});

test("only an unseen session under exactly the requested name adopts the tab", () => {
  assert.ok(adoptsProvisional(false, "api", "api"));
  assert.ok(!adoptsProvisional(true, "api", "api"), "a session we already had is not the one we asked for");
  assert.ok(!adoptsProvisional(false, "tests", "api"), "…and neither is somebody else's new session");
  assert.ok(!adoptsProvisional(false, "api", null), "nothing pending, nothing to adopt");
  assert.ok(!adoptsProvisional(false, "web:api", "api"), "a remote arrival is not the local one we asked for");
});

test("executed: a focus on a RUNNING session under the requested name resolves the create in flight", () => {
  // a create naming a running session is answered by focusing it, never by a new session — so no
  // session frame will ever adopt the tab (adoptsProvisional wants an unseen one); the focus is the
  // verdict. Before this the tab stayed pending: the warn that followed a tagged request became a
  // "Couldn't start api" dialog over a running api, and an untagged one waited 90 s for the same.
  const prov = mintProvisionalId("x1");
  assert.ok(focusResolvesProvisional("LIVE", "api", "api", prov));
  assert.ok(!focusResolvesProvisional("LIVE", "api", "api", null), "no create pending: an ordinary focus");
  assert.ok(!focusResolvesProvisional("LIVE", "web", "api", prov), "some other session focused mid-create (a feed click): the create is still pending");
  assert.ok(!focusResolvesProvisional("LIVE", undefined, "api", prov),
    "a session this client does not hold yet: its frame arrives unseen and adopts the tab the usual way");
  assert.ok(!focusResolvesProvisional(prov, "api", "api", prov), "the provisional id is never the kernel's to focus");
  assert.ok(!focusResolvesProvisional("LIVE", "web:api", "api", prov), "a remote namesake is not the local one asked for");
  assert.ok(focusResolvesProvisional("LIVE", "web:api", "web:api", prov), "…and a remote create resolves to its host-prefixed namesake");
});

// ── the wiring in render.ts ────────────────────────────────────────────────────────────────────────

test("the focus handler retires the provisional QUIETLY when the kernel answered the create by focusing a running session; a warn after that toasts", () => {
  const focus = RENDER.slice(RENDER.indexOf('else if (m.type === "focus") {'), RENDER.indexOf('else if (m.type === "dropCitation"'));
  assert.match(focus, /if \(focusResolvesProvisional\(m\.id, sessions\.get\(m\.id\)\?\.name, pendingNewSession, provisionalId\)\) resolveProvisionalToExisting\(m\.id\);/);
  assert.ok(focus.indexOf("resolveProvisionalToExisting(m.id)") < focus.indexOf("setActive(m.id"),
    "retired BEFORE the switch, so dropProvisional's reselect cannot outrank the focus and the real tab is what stays active");
  assert.ok(focus.indexOf("closingTabs.delete(m.id);") < focus.indexOf("resolveProvisionalToExisting(m.id)"),
    "…and AFTER the branch's reveal + close-suppression retire, which stay first (the per-viewer focus contract, peek-tab / tab-close-optimistic pins)");
  const res = RENDER.slice(RENDER.indexOf("function resolveProvisionalToExisting("), RENDER.indexOf("// A create that FAILED."));
  assert.match(res, /const \{ queued, draft \} = dropProvisional\(\);/, "the tab goes, and the 90 s backstop with it");
  assert.ok(!res.includes("showConfirm(") && !res.includes("failProvisional(") && !res.includes("warnToast("), "quietly: no dialog, no toast of its own");
  assert.ok(!res.includes("sendMessage"), "held text is kept as the running session's draft, never sent into a thread the user has not read");
  assert.match(res, /drafts\.set\(realId, \[drafts\.get\(realId\) \?\? "", held\]\.filter\(Boolean\)\.join\("\\n\\n"\)\);/,
    "…and never dropped: it joins that session's draft (nothing typed is ever just lost)");
  assert.match(res, /if \(activeId === realId && ta\) \{ ta\.value = drafts\.get\(realId\) \?\? ""; growComposer\(ta\); \}/,
    "the reselect may already sit on the real tab (setActive then early-returns): the box is filled here too");
  // the warn that follows a tagged request finds no create pending → the toast path, unchanged
  assert.match(RENDER, /if \(provisionalId\) failProvisional\(m\.text\); else warnToast\(m\.text\);/);
});

test("creating a session opens the provisional tab instead of a modal", () => {
  assert.match(RENDER, /openProvisional\(req\);/);
  assert.doesNotMatch(RENDER, /showOpeningModal/, "the modal is gone, not merely hidden");
  assert.doesNotMatch(RENDER, /hideOpeningModal/);
  // "opening", never "working": the working chip renders an elapsed timer off sinceEpoch, and a
  // provisional tab has no honest work clock — the old "working" seed (in SECONDS, at that) showed
  // "Working" + an epoch-sized number until the first kernel payload arrived (the user 2026-08-10).
  assert.match(RENDER, /status: \{ state: "opening", sinceEpoch: Date\.now\(\) \}/,
    "the chip says what this phase IS — the session is opening");
  assert.doesNotMatch(RENDER, /state: "working", sinceEpoch: Math\.floor/, "the broken-clock seed is gone");
  // …and the tab strip shows the accent loader dot for the opening state, so the starting tab has a cue
  // the opening dot comes from the one dot rule (tabDotClass, T262g)
  const TS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "tab-state.ts"), "utf8");
  assert.match(TS, /if \(st === "opening"\) return "tab-dot opening";/);
  assert.match(RENDER, /const dotCls = tabDotClass\(st\);/);
  assert.match(CSS, /\.tab-dot\.opening \{ background: var\(--accent\); animation: opening-line-pulse/);
  assert.match(RENDER, /order\.push\(id\);/, "the tab survives reconcileTabOrder as a not-yet-kernel-known extra");
});

test("a send on a provisional tab is HELD, not posted to a session that doesn't exist", () => {
  assert.match(RENDER, /provisionalQueue\.push\(text\);\s*\n\s*registerOptimistic\(sid, text, attached\.filter\(\(p\) => previewKind\(p\) === "img"\)\);/,
    "the dashed bubble goes up now — with its dragged-image thumbnails — romp has it, it is not delivered");
  // a FAILED tab has no pending spawn to queue onto: refuse loudly, the box keeps the only copy. The refusal reports
  // a state the page after a reload does not have, so it is ephemeral (executed in reload-notices.test.ts)
  assert.match(RENDER, /if \(sid !== provisionalId\) \{\s*\n\s*ephemeralWarnToast\("“" \+ \(sessions\.get\(sid\)\?\.name \|\| "this session"\)/);
});

test("adoption flushes the held messages FOR REAL and carries the draft across", () => {
  assert.match(RENDER, /if \(adoptsProvisional\(existed, msg\.name, pendingNewSession\)\) \{\s*\n\s*adoptProvisional\(msg\.id\);/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "sendMessage", id: realId, text, qid \}\);/);   // under the id the press minted, so the bubble carried over wears it too
  assert.match(RENDER, /registerOptimistic\(realId, text, undefined, qid\);/);
  // the draft must be set BEFORE the switch — setActive fills the box from `drafts`
  const adopt = RENDER.slice(RENDER.indexOf("function adoptProvisional"));
  assert.ok(adopt.indexOf("drafts.set(realId, draft)") < adopt.indexOf("setActive(realId)"),
    "setActive reads the draft map, so the carry has to be in it first");
});

test("a failed create says so in a dialog, in the kernel's own words — ON the failed thread", () => {
  assert.match(RENDER, /if \(provisionalId\) failProvisional\(m\.text\); else warnToast\(m\.text\);/);
  assert.match(RENDER, /showConfirm\("Couldn't start " \+ name,/);
  assert.match(RENDER, /What you typed is in this tab's message box\./,
    "losing the text would be the one unrecoverable part");
  // The failure JUMPS BACK to its own tab first, and the text goes into THAT tab's box (the user
  // 2026-08-08, whose held text landed in the draft of an unrelated thread they happened to be
  // reading). The tab stays — the dialog is on top of the thread it is about.
  const fail = RENDER.slice(RENDER.indexOf("function failProvisional"), RENDER.indexOf("function cancelProvisional"));
  assert.ok(fail.includes("setActive(id);"), "foreground the failed thread before saying anything");
  assert.ok(fail.includes("drafts.set(id, held); persistDrafts();"), "the text belongs to the failed tab, no other");
  assert.ok(fail.indexOf("setActive(id);") < fail.indexOf("showConfirm("), "jump first, dialog second");
  assert.ok(!fail.includes("= dropProvisional()"), "the tab is NOT torn down — it holds the text");
  assert.ok(fail.includes("failedProvisionals.add(id);"));
  // the failed tab's transcript says what happened (the starting loader would be a lie)…
  assert.match(PLACEHOLDER, /This session couldn't start\. What you typed is kept in the box below/);   // the placeholder by kind (pane-placeholder.ts, T355)
  // …the starting loader gives way to it because the placeholder's KIND changed (starting → start-failed), the rule that
  // replaced the stale-start special case: the failed create feeds the kind, and a kind change rebuilds
  assert.match(RENDER, /provisional: isProvisionalId\(id\), provisionalFailed: failedProvisionals\.has\(id\) \}\);/);
  assert.match(PLACEHOLDER, /if \(st\.provisional && st\.provisionalFailed\) return "start-failed";\s*\n\s*if \(st\.provisional\) return "starting";/);
  // …and its composer stays LIVE despite the closed-tab treatment, so the text is editable/copyable
  assert.match(RENDER, /const closed = s\.status\.state === "closed" && !failedProvisionals\.has\(activeId!\);/);
});

test("the silent-failure backstop is long, because it is no longer what you wait on", () => {
  const m = RENDER.match(/const PROVISIONAL_WAIT_MS = ([0-9_]+);/);
  assert.ok(m, "there is still a backstop for a spawn that dies saying nothing");
  assert.ok(Number(m![1].replace(/_/g, "")) >= 60_000, "well past the old 30s cue");
});

test("closing a provisional tab aborts the pending spawn; a FAILED one is a plain local discard", () => {
  assert.match(RENDER, /if \(id === provisionalId\) cancelProvisional\(\);\s*\n\s*else \{ failedProvisionals\.delete\(id\); dismissSession\(id, "close"\); \}/);
  assert.match(RENDER, /vscodeApi\.postMessage\(\{ type: "cancelCreate", name \}\)/);
  // the kernel never knew a provisional id — the dead-tab ✕ must not post closeTab for one
  assert.match(RENDER, /if \(!isProvisionalId\(id\)\) vscodeApi\.postMessage\(\{ type: "closeTab", id \}\);/);
});

test("the folder question retires the tab and holds what was typed for the retry", () => {
  assert.match(RENDER, /const held = dropProvisional\(\);/);
  assert.match(RENDER, /pendingCarry = \[\.\.\.held\.queued, held\.draft\]\.filter\(Boolean\)/);
  assert.match(RENDER, /if \(ta && pendingCarry\) \{ ta\.value = pendingCarry; growComposer\(ta\); \}/);
});

test("a starting tab shows the romp loader, not the 'No messages yet' placeholder", () => {
  assert.match(PLACEHOLDER, /case "starting": \{[\s\S]{0,400}?ph\.classList\.add\("tx-starting"\);/);   // the placeholder by kind (pane-placeholder.ts, T355)
  assert.match(RENDER, /romp-swirl-glyph\.svg/);
  assert.match(PLACEHOLDER, /"Starting " \+ ctx\.text\.sessionName \+ "… you can type now; romp sends it when it's up\."/);
  assert.match(RENDER, /sessionName: s\.name \},/);
  assert.match(CSS, /\.tx-starting-swirl \{[\s\S]*?animation: tx-starting-spin/);
  assert.match(CSS, /prefers-reduced-motion: reduce\) \{ \.tx-starting-swirl \{ animation: none/);
  assert.doesNotMatch(CSS, /opening-dots/, "the bouncing-dots modal CSS went with it");
});
