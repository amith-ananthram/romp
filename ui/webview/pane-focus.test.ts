// The chat pane never jumps to another session on its own (T357, the user 2026-09-11): a tab that leaves the strip
// on its own leaves the pane UNFOCUSED, the same session takes focus back when its tab returns, and only the user's
// own ✕ keeps the recency fallback. The rule and the words are executed here (pane-focus.ts); the wiring is pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { focusAfterDismiss, emptyStateParts } from "./pane-focus";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const fn = (name: string) => { const i = RENDER.indexOf("function " + name + "("); const b = RENDER.slice(i); return b.slice(0, b.indexOf("\n}\n") + 3); };

test("focusAfterDismiss: the user's own ✕ keeps the recency fallback; every other departure leaves the pane unfocused", () => {
  const none = () => false;
  assert.deepEqual(focusAfterDismiss("close", ["b", "a"], ["a", "b", "c"], none), { activeId: "b", unfocused: false }, "MRU first");
  assert.deepEqual(focusAfterDismiss("close", ["x"], ["a", "b"], none), { activeId: "a", unfocused: false }, "no recency on the strip: the first tab");
  assert.deepEqual(focusAfterDismiss("close", ["b"], ["a", "b"], (id) => id === "b"), { activeId: "a", unfocused: false }, "never one the same teardown takes next");
  assert.deepEqual(focusAfterDismiss("close", [], [], none), { activeId: null, unfocused: false }, "nothing left: no tab, not the unfocused state");
  for (const why of ["hostDrop", "omitted", "end"] as const) {
    assert.deepEqual(focusAfterDismiss(why, ["b", "a"], ["a", "b", "c"], none), { activeId: null, unfocused: true },
                     why + ": no other session takes the box, whatever the recency says");
  }
});

test("emptyStateParts: a declined record's hidden line says the first arrival is hidden, name-free; the user's own hidden tab keeps the view's line", () => {
  const own = emptyStateParts({ name: "web", why: "hidden", dialing: false }, true);
  assert.deepEqual(own, { head: "This tab view shows no session. Change the view, or pick a tab.", name: null, tail: "" });
  const declined = emptyStateParts({ name: "api", why: "hidden", dialing: false, declined: true }, true);
  assert.deepEqual(declined, { head: "The first session to arrive is hidden by this view. Pick a tab, or change the view.", name: null, tail: "" }, "worded for the record's case, still name-free (a visible placeholder tab may be on the strip)");
  assert.equal(emptyStateParts({ name: "api", why: "hidden", dialing: false, declined: true }, false).name, null);
});

test("emptyStateParts: the body names the session that vanished and why; reconnecting when the host is dialing", () => {
  assert.deepEqual(emptyStateParts(null, false), { head: "No session open — click + to add one.", name: null, tail: "" });
  assert.deepEqual(emptyStateParts(null, true), { head: "No session selected. Pick a tab to start.", name: null, tail: "" });
  assert.deepEqual(emptyStateParts({ name: "web", why: "hostDrop", dialing: true }, true),
                   { head: "No session selected. Pick a tab to start. ", name: "web", tail: "’s host disconnected; reconnecting… It comes back here when the host does." });
  assert.deepEqual(emptyStateParts({ name: "web", why: "hostDrop", dialing: false }, true).tail, "’s host disconnected. It comes back here when the host does.");
  assert.deepEqual(emptyStateParts({ name: "web", why: "omitted", dialing: false }, true).tail, " is no longer listed by romp. It comes back here if it returns.");
  assert.deepEqual(emptyStateParts({ name: "web", why: "end", dialing: false }, true).tail, " ended.");
  assert.deepEqual(emptyStateParts({ name: "web", why: "awaited", dialing: false }, true),
                   { head: "No session selected. Pick a tab to start. ", name: "web", tail: " is not listed yet. It comes back here when its host does." });
  assert.deepEqual(emptyStateParts({ name: "web", why: "awaited", dialing: true }, true).tail, " is not listed yet; its host is reconnecting… It comes back here when the host does.");
  assert.deepEqual(emptyStateParts({ name: "web", why: "gone", dialing: false }, true).tail, " is no longer on the strip. Pick a tab.");
  assert.deepEqual(emptyStateParts({ name: "web", why: "hidden", dialing: false }, true), { head: "This tab view shows no session. Change the view, or pick a tab.", name: null, tail: "" }, "name-free: a clean recording frame, the pick said once");
  assert.deepEqual(emptyStateParts({ name: "web", why: "awaited", dialing: false }, false).head, "No sessions yet. ", "an empty strip invites no pick");
  for (const p of [emptyStateParts({ name: "web", why: "hostDrop", dialing: true }, true), emptyStateParts(null, true)]) {
    assert.doesNotMatch(p.head + p.tail, /\b(card|board|goal|column|nudge)\b/, "no romp nouns in the body's line");
  }
});

test("the wiring: the dismiss branch, the unfocused body, the composer, the restore on return, no adoption meanwhile", () => {
  assert.match(RENDER, /^let vanishedId: string \| null = null;\s*\nlet vanishedWhy: VanishWhy \| null = null;\s*\nlet vanishedName = "";/m);
  const dismiss = fn("dismissSession");
  assert.match(dismiss, /const next = focusAfterDismiss\(why, mru, order, goingToo\);\s*\n\s*activeId = next\.activeId;\s*\n\s*if \(next\.unfocused\) \{ vanishedId = id; vanishedWhy = why; vanishedName = name; vanishedByDecline = false; \}/);
  assert.match(dismiss, /if \(why !== "close"\) \{[\s\S]*?ta\.blur\(\);[\s\S]*?renderComposerNote\(id, why, name\);/, "the T236 note above the box still says whose box went away");
  // the pick (and the restore) end the unfocused state
  assert.match(fn("setActive"), /activeId = id;\s*\n\s*vanishedId = null; vanishedWhy = null; vanishedName = ""; wantActive = null; wantActiveGone = null;/, "a pick ends the unfocused state AND the awaited tab");
  assert.match(fn("setActive"), /persistActive\(id\);/, "the pick persists id and name");
  assert.match(fn("persistActive"), /activeId: id, activeName: liveSession\(id\)\?\.name \|\| tabMeta\.get\(id\)\?\.name \|\| ""/, "the name persists beside the id for the reload's body");
  assert.match(RENDER, /if \(adopted\) \{ activeId = msg\.id; assertPeekFor\(msg\.id\); loadComposerFor\(msg\.id, true\); persistActive\(msg\.id\); vanishedId = null; vanishedWhy = null; vanishedName = ""; wantActive = null; wantActiveGone = null; vanishedByDecline = false; \}/, "an adopted tab asserts its peek and is persisted like a pick (the review's lows)");
  // the empty body: pane-focus's words, the name dressed as the strip dresses it, the composer disabled and nameless
  const show = fn("showActive");
  assert.match(show, /paintEmptyState\(empty\);\s*\n\s*empty\.style\.display = "";\s*\n[\s\S]{0,200}?ta\.disabled = true; ta\.placeholder = order\.length \? "Pick a tab to start" : "Click \+ to add a session"; syncComposerPh\(\);/);
  assert.match(show, /if \(sendBtn\) sendBtn\.disabled = true;\s*\n\s*\}\s*\n\s*document\.body\.style\.removeProperty\("--active-accent"\);/, "no session colour on the window border either");
  const paint = fn("paintEmptyState");
  assert.match(paint, /dialing: hostIsDialing\(vanishedId\)/, "reconnecting is the host's dial state, never a timer");
  assert.match(paint, /b\.replaceChildren\(\.\.\.hostNameNodes\(parts\.name, named\)\);/, "the host prefix the tab wore (the vanished or the awaited id)");
  assert.match(paint, /empty\.classList\.toggle\("unfocused", !!v\);\s*\n\s*empty\.dataset\.vanished = named \|\| "";/, "the body names the vanished or the awaited id");
  assert.doesNotMatch(RENDER, /empty\.textContent = "No session open/, "the one writer of the empty body is paintEmptyState");
  // the return: the session frame, or the strip re-listing it; no other arrival adopts the box meanwhile
  assert.match(RENDER, /if \(vanishedId === msg\.id\) restoreIfShown\(msg\.id\);[^\n]*\n\s*const wouldAdopt = !activeId && \(!vanishedId \|\| vanishedByDecline\) && !wantActive && !wantActiveGone;[^\n]*\n\s*const adopted = wouldAdopt && stripShows\(msg\.id\);/, "an adoption reads the rule's visibility half: a first arrival the filter hides is not adopted (the review's low)");
  // a DECLINED adoption is recorded like restoreIfShown's hidden case, so the schedule restores it when the filter shows
  // it; the record yields to a later visible first arrival (nothing was chosen), and any activation clears the mark
  assert.match(RENDER, /else if \(wouldAdopt && !vanishedId\) \{ vanishedId = msg\.id; vanishedWhy = "hidden"; vanishedName = sessions\.get\(msg\.id\)\?\.name \|\| tabMeta\.get\(msg\.id\)\?\.name \|\| ""; vanishedByDecline = true; \}/, "…on FIRST sight only (the review's low: the last hidden arrival overwrote it)");
  // the adoption over a declined record ends the unfocused state as setActive does (the review's high: a record left beside
  // an active tab handed its session to applyTabOrder's restore when the filter lifted); the mark clears on every other unfocus
  assert.match(fn("unfocusHiddenByView"), /vanishedName = sessions\.get\(id\)\?\.name \|\| tabMeta\.get\(id\)\?\.name \|\| ""; vanishedByDecline = false;/);
  assert.match(fn("dismissSession"), /if \(next\.unfocused\) \{ vanishedId = id; vanishedWhy = why; vanishedName = name; vanishedByDecline = false; \}/);
  assert.match(fn("setActive"), /wantActiveGone = null; vanishedByDecline = false;/, "…cleared with the rest of the unfocused state (on the same line: chat-window.test.ts bounds the distance from the activation to the paused strip's re-evaluation)");
  assert.match(RENDER, /if \(composerNoteSid === msg\.id\) restoreIfShown\(msg\.id\);/, "the composer note's restore goes through the rule too");
  assert.equal((RENDER.match(/\bsetActive\(msg\.id\)/g) || []).length, 0, "no frame-reachable direct setActive(msg.id) is left in the arrival path");
  assert.ok(RENDER.indexOf("/** Does the strip show `id` right now:") > RENDER.indexOf("function restoreIfShown("), "stripShows's docstring sits above its own function, after restoreIfShown");
  assert.match(fn("applyTabOrder"), /for \(const id of kernelOrder\) kernelListed\.add\(id\);[\s\S]{0,500}?const back = vanishedId \|\| wantActive;[^\n]*\n\s*if \(back && restoreIfShown\(back\)\)/);
  // every restore reads ONE rule (the review's leak: applyTabOrder's had no visibility predicate, so a routine push
  // re-focused a filtered-out session for one frame): listed AND shown takes focus back; listed but hidden leaves the
  // pane unfocused as "hidden", for renderTabs's schedule to restore when the filter shows it
  assert.match(fn("restoreIfShown"), /if \(!stripLists\(id\)\) return false;\s*\n\s*if \(stripShows\(id\)\) \{ setActive\(id\); return true; \}\s*\n\s*if \(!activeId\) \{ vanishedId = id; vanishedWhy = "hidden";/);
  assert.match(fn("stripLists"), /return !closingTabs\.has\(id\) && \(order\.includes\(id\) \|\| tabMeta\.has\(id\)\);/, "the paint's membership rule, shared with every restore");
  assert.match(RENDER, /if \(wantActive && msg\.id === wantActive && stripLists\(msg\.id\)\) \{ wantActive = null; restoreIfShown\(msg\.id\); \}/, "the persisted tab's arrival restores only if shown");
  assert.equal((RENDER.match(/\bsetActive\(back\)/g) || []).length, 1, "the one direct setActive(back) left is renderTabs's own fire-time restore, behind stripLists and stripShows");
  // a hidden tab torn down while the pane is unfocused: the body's line follows the reason (the review's low)
  assert.match(fn("dismissSession"), /if \(!wasActive && vanishedId === id\) \{[\s\S]{0,700}?if \(vanishedByDecline\) \{ vanishedId = null; vanishedWhy = null; vanishedName = ""; vanishedByDecline = false; \}\s*\n\s*else \{ vanishedWhy = why; vanishedName = name; \}\s*\n\s*repaintEmptyStateIfUnfocused\(\);\s*\n\s*\}/, "the user's tab's teardown writes its reason; a declined record's takes the record with it, name-free (the review's medium)");
  // the reload road (the review's HIGH): the persisted tab is awaited at boot, the body names it, nothing adopts
  assert.match(RENDER, /^let wantActiveName: string = /m);
  assert.match(fn("paintEmptyState"), /const awaited = !vanishedId && wantActive \? wantActive : null;/);
  assert.match(RENDER, /renderBgTasks\(\);\s*\n\s*\} else if \(!activeId\) \{[\s\S]{0,300}?showActive\(\);\s*\n\s*\}/, "a frame landing on an unfocused pane paints the body, adopting nothing");
  assert.match(fn("paintEmptyState"), /why: "awaited" as const, dialing: hostIsDialing\(awaited\)/);
  assert.match(fn("paintEmptyState"), /const nameOf = \(id: string, carried = ""\) => carried \|\| wantActiveName \|\| tabMeta\.get\(id\)\?\.name \|\| sessions\.get\(id\)\?\.name \|\| "a session";/, "never a raw sid in the body, on any branch");
  assert.match(RENDER, /if \(wantActive && \(isSubId\(wantActive\) \|\| isProvisionalId\(wantActive\)\)\) \{ wantActiveGone = wantActive; wantActive = null; \}/, "an id that can never be listed again is not awaited");
  assert.match(fn("paintEmptyState"), /gone \? \{ name: nameOf\(gone\), why: "gone" as const, dialing: false \}/);
  assert.match(fn("applyTabOrder"), /if \(back && restoreIfShown\(back\)\) \{[^\n]*\}\s*\n\s*else if \(!activeId\) showActive\(\);/, "the strip changing under an unfocused pane repaints the body (an emptied strip, or the named tab listed but hidden)");
  // the body repaints on the dial event; the view filter routes through the same rule; the keyboard picks the first tab
  assert.match(RENDER, /window\.addEventListener\("romp:hostDial", \(\) => \{ syncHostOfflineFoot\(\); repaintEmptyStateIfUnfocused\(\); \}\);/);
  assert.match(fn("repaintEmptyStateIfUnfocused"), /if \(activeId\) return;[\s\S]*?if \(e\) paintEmptyState\(e\);/);
  // the strip's filters: a VIEW excluding the active tab is covered by the peek (captureViews asserts it before
  // applyTabOrder on every tabOrder frame; visibility is a pure function of the views blob), but the #only= filter is
  // applied on top of tabInView and is no peek input, so an only-filtered active tab goes UNFOCUSED here, never
  // re-pointed; the fire-time check reads the same predicate visibleIds is built from (stripShows)
  assert.match(fn("renderTabs"), /if \(activeId && ids\.includes\(activeId\) && !visibleIds\.includes\(activeId\)\) \{\s*\n\s*const hid = activeId;\s*\n\s*setTimeout\(\(\) => \{ if \(activeId === hid && !stripShows\(hid\)\) unfocusHiddenByView\(hid\); \}, 0\);/);
  assert.match(fn("renderTabs"), /if \(!activeId && vanishedId && vanishedWhy === "hidden" && visibleIds\.includes\(vanishedId\)\)[\s\S]{0,900}?if \(!activeId && vanishedId === back && vanishedWhy === "hidden" && stripLists\(back\) && stripShows\(back\)\) setActive\(back\);/, "…and comes back when the filter shows it again, the reason AND the strip's membership (stripLists, the paint's rule) re-read at fire time");
  assert.doesNotMatch(fn("renderTabs"), /const nameOf = /, "no second name ladder in renderTabs: stripShows carries the one (the review's low)");
  assert.match(fn("stripShows"), /function stripShows\(id: string, only: string \| null = onlyTag\(\)\): boolean \{\s*\n\s*if \(!tabInView\(id\)\) return false;\s*\n\s*return !only \|\| matchesOnly\(sessions\.get\(id\)\?\.name \?\? tabMeta\.get\(id\)\?\.name \?\? "", only\);/, "the one predicate");
  assert.match(fn("renderTabs"), /const visibleIds = ids\.filter\(\(id\) => stripShows\(id, only\)\);/, "visibleIds is built from it: no second copy");
  assert.match(RENDER, /const onlyHashWindow = onlyWindow\(\);\s*\n\s*onlyHashWindow\.addEventListener\("hashchange", onOnlyHashChange\);/, "a live edit of the #only= hash repaints at once, heard on the window that carries the filter (the shell's when framed), by a NAMED handler…");
  assert.match(RENDER, /window\.addEventListener\("pagehide", \(\) => onlyHashWindow\.removeEventListener\("hashchange", onOnlyHashChange\)\);/, "…taken off the shell's window on pagehide (a closed split column must not hold the detached pane alive)");
  assert.doesNotMatch(RENDER, /window\.addEventListener\("hashchange"/, "never the pane's own window alone: framed on the dashboard its hash never changes (the review's medium)");
  assert.doesNotMatch(RENDER, /visibleIds\.includes\(activeId\) && visibleIds\.length/, "no first-visible-tab RE-POINT");
  assert.match(fn("unfocusHiddenByView"), /activeId = null; vanishedId = id; vanishedWhy = "hidden";/);
  assert.match(fn("assertPeekFor"), /const next = chatVisible\(id\) \? null : id;/, "the peek rule, over the views blob alone");
  assert.match(RENDER, /if \(adopted\) \{ activeId = msg\.id; assertPeekFor\(msg\.id\); loadComposerFor\(msg\.id, true\); persistActive\(msg\.id\); vanishedId = null;/, "an adoption asserts the peek like a pick");
  assert.match(fn("paintEmptyState"), /name: nameOf\(vanishedId, vanishedName\), why: vanishedWhy/, "no raw sid on the dismissal branch either");
  assert.match(fn("dismissSession"), /const name = sessions\.get\(id\)\?\.name \|\| tabMeta\.get\(id\)\?\.name \|\| "a session";/);
  assert.match(fn("cycleTab"), /if \(pickFirstVisibleTab\(\)\) return;/);
  assert.match(fn("onTabKey"), /if \(!activeId\) \{[^\n]*\n\s*if \(\(e\.key === "ArrowRight"[\s\S]{0,160}pickFirstVisibleTab\(\)\)/);
  assert.match(RENDER, /if \(!activeId\) \{ if \(pickFirstVisibleTab\(\)\) e\.preventDefault\(\); return; \}/, "the window arrow step");
  assert.doesNotMatch(RENDER, /setActive\(next\); \}, 0\);/, "the old re-point is gone");
  // the feed relay: showActive announces the active tab (null included) on every branch it takes
  assert.match(show, /^\s*notifyActive\(\);/m);
  assert.match(fn("notifyActive"), /vscodeApi\.postMessage\(\{ type: "activeTab", id: activeId \}\)/, "null rides as null");
  assert.match(CSS, /\.empty-state\.unfocused \{/); assert.match(CSS, /\.empty-state-name \{ font-weight: 600; \}/);
  // the statusline says nothing with no active session: not the vanished session's chips (the served lab's screenshot found it)
  assert.match(fn("updateStatusline"), /if \(!s\) \{ sl\.replaceChildren\(\); return; \}/);
});
