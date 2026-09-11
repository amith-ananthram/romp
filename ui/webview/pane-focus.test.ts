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
  assert.deepEqual(emptyStateParts({ name: "web", why: "hidden", dialing: false }, true).tail, " is not shown by this tab view. Pick a tab, or change the view.");
  assert.deepEqual(emptyStateParts({ name: "web", why: "awaited", dialing: false }, false).head, "No sessions yet. ", "an empty strip invites no pick");
  for (const p of [emptyStateParts({ name: "web", why: "hostDrop", dialing: true }, true), emptyStateParts(null, true)]) {
    assert.doesNotMatch(p.head + p.tail, /\b(card|board|goal|column|nudge)\b/, "no romp nouns in the body's line");
  }
});

test("the wiring: the dismiss branch, the unfocused body, the composer, the restore on return, no adoption meanwhile", () => {
  assert.match(RENDER, /^let vanishedId: string \| null = null;\s*\nlet vanishedWhy: VanishWhy \| null = null;\s*\nlet vanishedName = "";/m);
  const dismiss = fn("dismissSession");
  assert.match(dismiss, /const next = focusAfterDismiss\(why, mru, order, goingToo\);\s*\n\s*activeId = next\.activeId;\s*\n\s*if \(next\.unfocused\) \{ vanishedId = id; vanishedWhy = why; vanishedName = name; \}/);
  assert.match(dismiss, /if \(why !== "close"\) \{[\s\S]*?ta\.blur\(\);[\s\S]*?renderComposerNote\(id, why, name\);/, "the T236 note above the box still says whose box went away");
  // the pick (and the restore) end the unfocused state
  assert.match(fn("setActive"), /activeId = id;\s*\n\s*vanishedId = null; vanishedWhy = null; vanishedName = ""; wantActive = null;/, "a pick ends the unfocused state AND the awaited tab");
  assert.match(fn("setActive"), /activeId: id, activeName: liveSession\(id\)\?\.name \|\| tabMeta\.get\(id\)\?\.name \|\| ""/, "the name persists beside the id for the reload's body");
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
  assert.match(RENDER, /if \(vanishedId === msg\.id\) setActive\(msg\.id\);\s*\n\s*const adopted = !activeId && !vanishedId && !wantActive;/);
  assert.match(fn("applyTabOrder"), /for \(const id of kernelOrder\) kernelListed\.add\(id\);[\s\S]{0,500}?const back = vanishedId \|\| wantActive;[^\n]*\n\s*if \(back && order\.includes\(back\)\) setActive\(back\);/);
  // the reload road (the review's HIGH): the persisted tab is awaited at boot, the body names it, nothing adopts
  assert.match(RENDER, /^let wantActiveName: string = /m);
  assert.match(fn("paintEmptyState"), /const awaited = !vanishedId && wantActive \? wantActive : null;/);
  assert.match(RENDER, /renderBgTasks\(\);\s*\n\s*\} else if \(!activeId\) \{[\s\S]{0,300}?showActive\(\);\s*\n\s*\}/, "a frame landing on an unfocused pane paints the body, adopting nothing");
  assert.match(fn("paintEmptyState"), /why: "awaited" as const, dialing: hostIsDialing\(awaited\)/);
  // the body repaints on the dial event; the view filter routes through the same rule; the keyboard picks the first tab
  assert.match(RENDER, /window\.addEventListener\("romp:hostDial", \(\) => \{ syncHostOfflineFoot\(\); repaintEmptyStateIfUnfocused\(\); \}\);/);
  assert.match(fn("repaintEmptyStateIfUnfocused"), /if \(activeId\) return;[\s\S]*?if \(e\) paintEmptyState\(e\);/);
  assert.match(fn("renderTabs"), /if \(activeId !== next && activeId && !tabInView\(activeId\)\) unfocusHiddenByView\(activeId\);/, "the view filter never re-points at another session");
  assert.match(fn("renderTabs"), /if \(!activeId && vanishedId && vanishedWhy === "hidden" && visibleIds\.includes\(vanishedId\)\)/, "…and restores the hidden tab when the view shows it again");
  assert.match(fn("unfocusHiddenByView"), /activeId = null; vanishedId = id; vanishedWhy = "hidden";/);
  assert.match(fn("cycleTab"), /if \(pickFirstVisibleTab\(\)\) return;/);
  assert.match(fn("onTabKey"), /if \(!activeId\) \{[^\n]*\n\s*if \(\(e\.key === "ArrowRight"[\s\S]{0,160}pickFirstVisibleTab\(\)\)/);
  assert.match(RENDER, /if \(!activeId\) \{ if \(pickFirstVisibleTab\(\)\) e\.preventDefault\(\); return; \}/, "the window arrow step");
  assert.doesNotMatch(RENDER, /setTimeout\(\(\) => \{ if \(activeId !== next && activeId && !tabInView\(activeId\)\) setActive\(next\); \}, 0\);/, "the old re-point is gone");
  // the feed relay: showActive announces the active tab (null included) on every branch it takes
  assert.match(show, /^\s*notifyActive\(\);/m);
  assert.match(fn("notifyActive"), /vscodeApi\.postMessage\(\{ type: "activeTab", id: activeId \}\)/, "null rides as null");
  assert.match(CSS, /\.empty-state\.unfocused \{/); assert.match(CSS, /\.empty-state-name \{ font-weight: 600; \}/);
  // the statusline says nothing with no active session: not the vanished session's chips (the served lab's screenshot found it)
  assert.match(fn("updateStatusline"), /if \(!s\) \{ sl\.replaceChildren\(\); return; \}/);
});
