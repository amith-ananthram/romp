// A composer draft belongs to the session it was typed in, and a teardown that is NOT that session's end
// must not destroy it or hand the keyboard to another session unannounced (T236, the user 2026-09-03: an
// unsent draft typed into one session's box "ended up in a different session's box" after a remote host
// dropped off and the session list changed).
//
// The mechanism: federation's closeRemote dispatches a synthetic `closed` per session of a dropped host,
// and applyTabOrder dismisses any kernel-listed id a push omits — both ran dismissSession exactly like the
// user's own ✕: the draft was DELETED, and when the torn-down tab was the active one the composer silently
// re-bound to the most-recently-used survivor while focus stayed in the textarea. The user kept typing into
// what they believed was session A's box; the input handler filed it under the new active id. A's text was
// gone, and the continuation surfaced in B.
//
// The rule now: only a GENUINE end (the user's ✕/End, or the owning kernel's `closed` for a session that
// actually ended) clears a session's composer state. A host drop (`hostDrop: true` on the synthetic frame)
// or a kernel-order omission STASHES it under the session's stable id and it comes back with the session.
// And whenever the ACTIVE tab is torn down under the user, the composer is blurred and says so above the
// box — the next keystroke cannot land in another session unnoticed; an explicit tab switch is what binds
// the box to a new session.
//
// The executed model below mirrors render.ts's dismissSession / setActive / session-adoption / input paths;
// the source pins hold the wiring (no jsdom harness for this renderer — the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

type Why = "close" | "end" | "hostDrop" | "omitted";

function model() {
  const sessions = new Set<string>();
  const order: string[] = [];
  const mru: string[] = [];                 // front = most-recently-active
  const drafts = new Map<string, string>();
  const kernelListed = new Set<string>();
  let activeId: string | null = null;
  let composer = "";                        // #composer-input's value
  let focused = false;                      // document.activeElement === the textarea
  let note: { sid: string; why: Why } | null = null;   // the line above the box (#composer-note)
  let flashes = 0;                          // swallowed keystrokes that pulsed the note
  const touchMru = (id: string) => { const i = mru.indexOf(id); if (i >= 0) mru.splice(i, 1); mru.unshift(id); };
  // setActive's composer half: stash the leaving tab's text, show the entering tab's own
  const activate = (id: string) => {
    if (activeId === id) return;
    if (activeId) { if (composer) drafts.set(activeId, composer); else drafts.delete(activeId); }
    composer = drafts.get(id) ?? "";
    note = null;                            // an explicit switch is the event that re-binds the box
    activeId = id;
    touchMru(id);
  };
  const hostOf = (x: string) => { const i = x.indexOf(":"); return i > 0 ? x.slice(0, i) : ""; };
  // dismissSession(id, why, doomed)
  const dismiss = (id: string, why: Why, doomed?: ReadonlySet<string>) => {
    const wasActive = activeId === id;
    if (wasActive) { if (composer) drafts.set(id, composer); else drafts.delete(id); }   // stash FIRST
    sessions.delete(id);
    if (why === "close" || why === "end") drafts.delete(id);                          // a genuine end clears
    const oi = order.indexOf(id); if (oi >= 0) order.splice(oi, 1);
    const mi = mru.indexOf(id); if (mi >= 0) mru.splice(mi, 1);                       // never the fallback
    if (wasActive) {
      const home = hostOf(id);
      const goingToo = (x: string) => (doomed?.has(x) ?? false) || (why === "hostDrop" && !!home && hostOf(x) === home);
      activeId = mru.find((x) => order.includes(x) && !goingToo(x)) || order.find((x) => !goingToo(x)) || null;   // recency first, then the strip — staying either way
      composer = (activeId && drafts.get(activeId)) || "";
      if (why !== "close") { focused = false; note = { sid: id, why }; }             // blur + say so
    }
  };
  return {
    get activeId() { return activeId; },
    get composer() { return composer; },
    get focused() { return focused; },
    get note() { return note; },
    tabs() { return order.slice(); },
    hasDraft(id: string) { return drafts.has(id); },
    draft(id: string) { return drafts.get(id); },
    // a session frame arriving (render.ts's session handler)
    arrive(id: string) {
      sessions.add(id);
      if (!order.includes(id)) order.push(id);
      if (!activeId) { activeId = id; if (!composer) composer = drafts.get(id) ?? ""; touchMru(id); }   // the adoption goes through the draft load
      if (note && note.sid === id) activate(id);  // back while the note still holds → the user did nothing since → back on it, draft and all
    },
    activate,
    // the box gaining focus — by a click, Tab, Enter over a selection, a slash pick… — is the deliberate re-bind
    // that retires the note (render.ts: the composer's focus listener)
    focus() { focused = true; note = null; },
    clickBox() { this.focus(); },
    // the "type from anywhere" default: a printable key with no owner drops the cursor into the box and
    // inserts — unless the hand-over note holds the box, when the key is swallowed and the note flashes
    typeAnywhere(text: string) {
      if (focused) { this.type(text); return; }
      if (note) { flashes++; return; }
      focused = true;
      this.type(text);
    },
    get flashes() { return flashes; },
    // a keystroke: only a focused textarea receives one; the input handler files under the ACTIVE id
    type(text: string) {
      if (!focused) return;
      composer += text;
      if (activeId) { if (composer) drafts.set(activeId, composer); else drafts.delete(activeId); }
    },
    closeX(id: string) { dismiss(id, "close"); },                                        // the user's own ✕ / End
    closedFrame(id: string, hostDrop = false) { dismiss(id, hostDrop ? "hostDrop" : "end"); },
    hostDrop(ids: string[]) { for (const id of ids) dismiss(id, "hostDrop"); },        // federation closeRemote
    push(list: string[]) {                                                              // a kernel tabOrder push
      const omitted = new Set(order.filter((id) => kernelListed.has(id) && !list.includes(id)));
      for (const id of order.slice()) if (omitted.has(id)) dismiss(id, "omitted", omitted);
      for (const id of list) kernelListed.add(id);
    },
  };
}

test("(i) a draft under a remote session survives its host dropping, and is back when the session returns", () => {
  const m = model();
  m.arrive("hostA:web"); m.arrive("hostA:api"); m.arrive("hostB:tests");
  m.activate("hostA:web"); m.focus(); m.type("half a thought");
  m.hostDrop(["hostA:web", "hostA:api"]);
  assert.equal(m.hasDraft("hostA:web"), true, "a host drop is not a close — the draft is stashed, not deleted");
  assert.equal(m.draft("hostA:web"), "half a thought");
  assert.deepEqual(m.tabs(), ["hostB:tests"]);
  // the host comes back: its session frame re-adds the tab — and, the note still holding, re-activates it
  m.arrive("hostA:web");
  assert.equal(m.activeId, "hostA:web");
  assert.equal(m.composer, "half a thought", "the returning session's box holds what was typed into it");
  // …whereas a return AFTER the user moved on (they clicked into the survivor's box → the note is gone)
  // stays where they are; the draft waits on its tab
  m.hostDrop(["hostA:web"]); m.clickBox();
  m.arrive("hostA:web");
  assert.equal(m.activeId, "hostB:tests");
  m.activate("hostA:web");
  assert.equal(m.composer, "half a thought");
});

test("(ii) typing after an active-tab teardown never lands under another session without an explicit switch", () => {
  const m = model();
  m.arrive("hostB:tests"); m.arrive("hostB:api"); m.arrive("hostA:web");
  m.activate("hostB:api"); m.activate("hostA:web");      // mru: web, api, tests
  m.focus(); m.type("dear hostA");
  m.hostDrop(["hostA:web"]);
  assert.equal(m.activeId, "hostB:api", "the strip falls back to the previously-active tab");
  assert.equal(m.focused, false, "the box is BLURRED — the next keystroke cannot land anywhere unnoticed");
  assert.ok(m.note && m.note.sid === "hostA:web" && m.note.why === "hostDrop", "and the composer says whose box went away");
  m.type(" …and more");                                   // keystrokes with no focus go nowhere
  assert.equal(m.hasDraft("hostB:api"), false, "nothing was filed under the survivor");
  assert.equal(m.draft("hostA:web"), "dear hostA", "the original text is intact under its own id");
  // the harness caught the leak the blur alone left open: a printable key "nobody claimed" re-focused the
  // box and inserted. While the note holds, that default stands down — swallowed, the note flashes.
  m.typeAnywhere("c");
  assert.equal(m.flashes, 1);
  assert.equal(m.focused, false);
  assert.equal(m.hasDraft("hostB:api"), false, "still nothing under the survivor");
  assert.ok(m.note && m.note.sid === "hostA:web", "the note stays up until a deliberate act");
  // an explicit switch to another tab IS a deliberate act: the note retires and the box is that tab's
  m.activate("hostB:tests");
  assert.equal(m.note, null, "the switch retires the note");
  m.focus(); m.type("for tests");
  assert.equal(m.draft("hostB:tests"), "for tests", "typed AFTER an explicit switch → that tab's own draft");
  assert.equal(m.draft("hostA:web"), "dear hostA");
});

test("(ii-c) a click into the box — or any other route into it — ends the hold, and typing is the survivor's own", () => {
  const m = model();
  m.arrive("hostB:tests"); m.arrive("hostA:web");
  m.activate("hostA:web"); m.focus(); m.type("dear hostA");
  m.hostDrop(["hostA:web"]);
  m.typeAnywhere("x");
  assert.equal(m.flashes, 1);
  m.clickBox();
  assert.equal(m.note, null, "focus by the user's own hand retires the note");
  m.type("for tests");
  assert.equal(m.draft("hostB:tests"), "for tests");
  assert.equal(m.draft("hostA:web"), "dear hostA");
  m.typeAnywhere("!");                                    // no hold any more — the default is back
  assert.equal(m.draft("hostB:tests"), "for tests!");
});

test("(ii-b) an omission teardown of the active tab gets the same blur + note, and keeps the draft", () => {
  const m = model();
  m.arrive("web"); m.arrive("api");
  m.push(["web", "api"]);
  m.activate("web"); m.activate("api");
  m.focus(); m.type("typed into api");
  m.push(["web"]);                                        // the kernel's push stops carrying api
  assert.equal(m.activeId, "web");
  assert.equal(m.focused, false);
  assert.ok(m.note && m.note.sid === "api" && m.note.why === "omitted");
  assert.equal(m.draft("api"), "typed into api", "an omission is not the user's close — stash, don't delete");
  m.arrive("api");                                        // it is listed again (a boot-partial push caught up)
  assert.equal(m.note, null, "the note retires when the session is back…");
  assert.equal(m.activeId, "api", "…by putting the user back on it: nothing happened in between");
  assert.equal(m.composer, "typed into api", "with the kept draft in the box");
  m.typeAnywhere("!");                                    // the blind keystroke the harness typed → into ITS box
  assert.equal(m.draft("api"), "typed into api!");
  assert.equal(m.hasDraft("web"), false);
});

test("(iii) a genuine end still clears the draft — the user's ✕ and the owning kernel's closed frame alike", () => {
  const m = model();
  m.arrive("web"); m.arrive("api");
  m.activate("web"); m.focus(); m.type("web draft");
  m.activate("api"); m.type("api draft");
  m.closeX("web");                                        // End session / Close tab
  assert.equal(m.hasDraft("web"), false, "the ✕ drops the draft (the user 2026-08-04)");
  m.closedFrame("api");                                   // the session died on its own — the kernel's own word
  assert.equal(m.hasDraft("api"), false, "a real end clears it too");
  assert.equal(m.activeId, null);
  // …and a synthetic closed from a host drop is NOT that
  m.arrive("hostA:web"); m.activate("hostA:web"); m.focus(); m.type("kept");
  m.closedFrame("hostA:web", true);
  assert.equal(m.draft("hostA:web"), "kept");
});

test("(iii-b) the kernel's closed frame that CONFIRMS our own ✕ neither notes nor blurs (the tab is already gone)", () => {
  const m = model();
  m.arrive("web"); m.arrive("api");
  m.activate("web"); m.activate("api");
  m.closeX("api");
  assert.equal(m.activeId, "web");
  m.focus();
  m.closedFrame("api");                                   // the confirm lands a moment later
  assert.equal(m.focused, true, "not the active tab any more → no retarget happened → nothing to announce");
  assert.equal(m.note, null);
});

test("(iv) the fallback never selects the dismissed id, wherever it sat in the recency stack", () => {
  const m = model();
  m.arrive("web"); m.arrive("api"); m.arrive("tests");
  m.activate("tests"); m.activate("api"); m.activate("web");   // mru: web, api, tests
  m.activate("api");                                           // mru: api, web, tests — api active AND most recent
  m.closedFrame("api");
  assert.equal(m.activeId, "web", "the dismissed id is pruned before the fallback is read");
  m.hostDrop(["web"]);
  assert.equal(m.activeId, "tests");
  m.closedFrame("tests");
  assert.equal(m.activeId, null, "no survivor → no active tab, never the dead id");
  // no RECENCY left but tabs still on the strip → the first of them, not null (a strip with no active tab
  // read "No session open" and handed the box to the next arriving frame — the harness caught it)
  const n = model();
  n.arrive("hostA:web"); n.arrive("hostB:tests");        // only ever looked at web (adopted at arrival)
  n.hostDrop(["hostA:web"]);
  assert.equal(n.activeId, "hostB:tests");
});

test("(iv-b) the fallback skips what the same teardown takes next, so the note names the box the user was in", () => {
  const m = model();
  m.arrive("hostB:tests"); m.arrive("hostA:api"); m.arrive("hostA:web");
  m.activate("hostA:api"); m.activate("hostA:web");      // mru: web, api, tests — the sibling is the recency neighbor
  m.focus(); m.type("dear hostA");
  m.hostDrop(["hostA:web", "hostA:api"]);                // closeRemote runs the host's sids in a burst
  assert.equal(m.activeId, "hostB:tests", "straight to a survivor on another host, never via the doomed sibling");
  assert.ok(m.note && m.note.sid === "hostA:web", "ONE note, for the box the user was typing in");
  assert.equal(m.draft("hostA:web"), "dear hostA");
  // same rule for a push that omits several at once
  const k = model();
  k.arrive("web"); k.arrive("api"); k.arrive("tests");
  k.push(["web", "api", "tests"]);
  k.activate("api"); k.activate("web"); k.focus(); k.type("typed into web");
  k.push(["tests"]);                                     // web and api both dropped from the list
  assert.equal(k.activeId, "tests");
  assert.ok(k.note && k.note.sid === "web");
  assert.equal(k.draft("web"), "typed into web");
  assert.equal(k.hasDraft("api"), false, "api had no draft to keep; nothing invented");
});

test("a returning session adopted as the ONLY tab gets its stashed draft back in the box", () => {
  const m = model();
  m.arrive("hostA:web");
  m.activate("hostA:web"); m.focus(); m.type("only tab, half typed");
  m.hostDrop(["hostA:web"]);
  assert.equal(m.activeId, null);
  assert.equal(m.composer, "");
  m.arrive("hostA:web");                                  // the host is back; nothing else is open → adopted
  assert.equal(m.activeId, "hostA:web");
  assert.equal(m.composer, "only tab, half typed", "the `!activeId` adoption loads the draft like setActive does");
});

// ── source pins: the wiring in render.ts / federation.ts ─────────────────────────────────────────────────

test("federation stamps its synthetic closed frames as a host drop, not an end", () => {
  assert.match(FED, /private closeRemote\(host: string\): void \{[\s\S]*?data: \{ type: "closed", id: sid, hostDrop: true \}/);
  // the owning kernel's own closed frame (the T233 fold) is passed through UNSTAMPED — a genuine end
  assert.match(FED, /if \(m && m\.type === "closed" && typeof m\.id === "string"\) \{[\s\S]*?window\.dispatchEvent\(new MessageEvent\("message", \{ data: m \}\)\);/);
});

test("every teardown names its reason, and only a genuine end clears the composer state", () => {
  assert.match(RENDER, /type DismissWhy = "close" \| "end" \| "hostDrop" \| "omitted";/);
  assert.match(RENDER, /function dismissSession\(id: string, why: DismissWhy, doomed\?: ReadonlySet<string>\): void \{/);
  // the user's ✕ / End
  assert.match(RENDER, /function closeTabLocally\(id: string\): void \{[\s\S]*?dismissSession\(id, "close"\);\s*\n\s*closingTabs\.set\(id, Date\.now\(\)\);/);
  // the kernel's closed frame — or federation's stamped stand-in for a dropped host
  assert.match(RENDER, /else if \(m\.type === "closed"\) dismissSession\(m\.id, m\.hostDrop === true \? "hostDrop" : "end"\);/);
  // applyTabOrder's kernel-order omission
  assert.match(RENDER, /const omitted = new Set\(order\.filter\(\(id\) => kernelListed\.has\(id\) && !inKernel\.has\(id\) && !liveSet\.has\(id\)\)\);[^\n]*\n(?:.*\n){0,3}?\s*for \(const id of order\.slice\(\)\) \{\s*\n\s*if \(omitted\.has\(id\)\) dismissSession\(id, "omitted", omitted\);/);   // a live id is never omitted (T258)
  // the clear is gated: the existing one-liner (composer-draft-persist.test.ts pins it) now runs for an end only
  assert.match(RENDER, /if \(why === "close" \|\| why === "end"\) \{[\s\S]*?drafts\.delete\(id\); composerCitations\.delete\(id\); composerEdits\.delete\(id\); composerFiles\.delete\(id\); persistDrafts\(\);\s*\n\s*\} else \{\s*\n\s*persistDrafts\(\);/);
});

test("an active tab torn down under the user: stash first, prune the fallback, blur, and say so above the box", () => {
  const body = RENDER.slice(RENDER.indexOf("function dismissSession(id: string, why: DismissWhy, doomed?: ReadonlySet<string>)"));
  const fn = body.slice(0, body.indexOf("\n}\n") + 3);
  // the live text is stashed under ITS id before anything is cleared or re-bound
  assert.ok(fn.indexOf("stashActiveDraft(id)") >= 0 && fn.indexOf("stashActiveDraft(id)") < fn.indexOf("sessions.delete(id)"), "stash precedes the teardown");
  // the dismissed id leaves the recency stack BEFORE the fallback is read
  assert.ok(fn.indexOf("mru.splice(mi, 1)") < fn.indexOf("focusAfterDismiss(why, mru, order, goingToo)"), "pin (iv): fallback never the dismissed id");
  // …and never an id the strip no longer shows: the re-bound box loads the fallback's own draft through the shared loader
  // (T357: the fallback itself moved to pane-focus.ts focusAfterDismiss and runs for the user's own ✕ alone; every
  // other departure leaves the pane unfocused — pane-focus.test.ts executes both)
  assert.match(fn, /const home = hostOf\(id\);[^\n]*\n\s*const goingToo = \(x: string\) => \(doomed\?\.has\(x\) \?\? false\) \|\| \(why === "hostDrop" && !!home && hostOf\(x\) === home\);\s*\n\s*const next = focusAfterDismiss\(why, mru, order, goingToo\);\s*\n\s*activeId = next\.activeId;[\s\S]*?loadComposerFor\(activeId\);/);
  // …and, unless the user themself clicked ✕, the box is blurred and the note names what went away
  assert.match(fn, /if \(why !== "close"\) \{[\s\S]*?ta\.blur\(\);[\s\S]*?renderComposerNote\(id, why, name\);/);
});

test("while the note holds the box, the type-from-anywhere defaults stand down; a click into the box ends the hold", () => {
  // the printable-key default (and, since 2026-09-05, the paste default with it): the hold is the LAST
  // gate of their shared typeFromAnywhereTarget — no focus steal into the survivor's box + flash; both
  // handlers focus only a box that helper returned (the keydown stays preventDefault-free, as pinned by
  // composer-citation.test.ts)
  assert.match(RENDER, /if \(composerNoteHolds\(\)\) return null;[^\n]*\n\s*return ta;\s*\n\}/);
  assert.equal(RENDER.match(/const ta = typeFromAnywhereTarget\(e\);\s*\n\s*if \(!ta\) return;\s*\n(\s*e\.preventDefault\(\);\s*\n)?(?:[^\n]*\n)*?\s*ta\.focus\(\{ preventScroll: true \}\);/g)?.length, 2, "keydown and paste both focus through the shared gate");
  // bare-area Enter: stands down before focusComposerOrAsk
  assert.match(RENDER, /if \(ae && ae !== document\.body\) return;\s*\n\s*if \(composerNoteHolds\(\)\) return;/);
  assert.match(RENDER, /function composerNoteHolds\(\): boolean \{\s*\n\s*if \(!composerNoteSid\) return false;\s*\n\s*flashComposerNote\(\);\s*\n\s*return true;/);
  // the deliberate act: the box gaining focus, by any route
  assert.match(RENDER, /ta\.addEventListener\("focus", \(\) => \{ if \(composerNoteSid\) clearComposerNote\(\); \}\);/);
  // the flash is one-shot in every mode: the class leaves on animationend, and the sheet has no reduced-motion freeze
  assert.match(RENDER, /n\.addEventListener\("animationend", \(\) => n\.classList\.remove\("composer-note-flash"\), \{ once: true \}\);/);
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  assert.match(CSS, /\.composer-note-flash \{ animation: composer-note-flash 0\.7s ease-out; \}/);
  assert.doesNotMatch(CSS, /prefers-reduced-motion: reduce\) \{ \.composer-note-flash/);
  // the note says so
  assert.match(RENDER, /hint\.textContent = " Click the box to type here\.";/);
});

test("the note retires on the exact events: an explicit switch, the session's return, or its ✕", () => {
  // setActive: a real switch re-binds the box → the note is stale
  assert.match(RENDER, /function setActive\(id: string[\s\S]*?if \(ta && activeId !== id\) \{[\s\S]*?clearComposerNote\(\);/);
  // the session frame: the torn-down session is back while the note still holds → back on it (setActive retires the note)
  assert.match(RENDER, /if \(composerNoteSid === msg\.id\) setActive\(msg\.id\);/);
  // its own ✕
  assert.match(RENDER, /function renderComposerNote\(sid: string, why: DismissWhy, name: string\): void \{[\s\S]*?x\.dataset\.act = "composerNoteX";/);   // the ✕ rides the body delegate since 2026-09-08 (click-safe, no per-node listener)
  assert.match(RENDER, /composerNoteX: \(\) => clearComposerNote\(\),/);
});

test("the `!activeId` adoption loads the adopted session's draft (once-per-page restore is not enough)", () => {
  assert.match(RENDER, /const adopted = !activeId && !vanishedId && !wantActive;[^\n]*\n\s*if \(adopted\) \{ activeId = msg\.id; loadComposerFor\(msg\.id, true\); \}/, "…and never while the user's own tab is away, or awaited after a reload (T357)");
  // …and the adoption is a first SHOW even for a payload the page already held (the append path never re-reveals a hidden view)
  assert.match(RENDER, /if \(existed && !forked && !firstBuild && !adopted\) \{\s*\n\s*appendActive\(\);/);
  // the loader: box ← drafts.get(id), chips, thumbnails, staged stack — the same set setActive paints
  assert.match(RENDER, /function loadComposerFor\(id: string \| null, keepTyped = false\): void \{[\s\S]*?ta\.value = \(id && drafts\.get\(id\)\) \|\| "";[\s\S]*?renderComposerChips\(id\);[\s\S]*?renderComposerFiles\(id\);/);
});
