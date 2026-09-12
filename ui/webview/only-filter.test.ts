// Demo/recording VIEW filter (the user 2026-07-14): `#only=<tag>` on the dashboard URL scopes every pane
// (chat tabs, feed, fleet, timeline) to sessions whose name starts with <tag>, so you get a clean frame for
// screencasts without a separate instance. Runtime-tests the pure helper; source-pins the four wire-ups.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { onlyTag, matchesOnly, onlyTags, onlyWindow } from "./only-filter";

const read = (p: string) => fs.readFileSync(path.resolve(process.cwd(), "..", p), "utf8");
const RENDER = read("ui/webview/render.ts");
const FEED = read("ui/webview/feed.ts");
const FLEET = read("ui/webview/fleet.ts");
const TL = read("ui/romp-timeline-view.js");
const ONLY = read("ui/webview/only-filter.ts");

test("matchesOnly: no tag passes everything; otherwise case-insensitive PREFIX", () => {
  assert.equal(matchesOnly("anything", null), true);
  assert.equal(matchesOnly("anything", ""), true);        // '' is falsy → no filter
  assert.equal(matchesOnly("demo-data", "demo"), true);
  assert.equal(matchesOnly("Demo-Data", "demo"), true);   // case-insensitive
  assert.equal(matchesOnly("bug", "demo"), false);
  assert.equal(matchesOnly("", "demo"), false);
  assert.equal(matchesOnly("predemo", "demo"), false);    // prefix, not substring
});

test("matchesOnly: a COMMA-SEPARATED tag keeps any session matching ANY prefix", () => {
  // demo sessions shouldn't have to wear a shared `demo-` prefix on camera (the user 2026-07-16)
  const tag = "api,tests,web";
  for (const n of ["api", "tests", "web", "API", "tests-e2e"]) assert.equal(matchesOnly(n, tag), true);
  for (const n of ["romp_docs", "nimbus", "biz", "webhook".slice(3)]) assert.equal(matchesOnly(n, tag), false);
  assert.equal(matchesOnly("web", "api, tests , web"), true);   // tolerates spaces around entries
  assert.equal(matchesOnly("api", "api"), true);                // a single tag still behaves as before
  assert.equal(matchesOnly("bug", "api,tests,web"), false);
  assert.equal(matchesOnly("anything", ",, ,"), false);         // all-empty list → nothing matches
});

test("onlyTags splits a tag into its prefixes", () => {
  assert.deepEqual(onlyTags("demo"), ["demo"]);
  assert.deepEqual(onlyTags("api, tests ,web"), ["api", "tests", "web"]);
  assert.deepEqual(onlyTags(" , "), []);
});

test("onlyTag reads #only= / ?only= from the shell URL (window.top), lowercased", () => {
  const g = global as any;
  const prev = g.window;
  const mk = (hash: string, search = "") => { const w: any = { location: { hash, search } }; w.top = w; g.window = w; };
  try {
    mk("#only=demo"); assert.equal(onlyTag(), "demo");
    mk("#only=Demo-Foo"); assert.equal(onlyTag(), "demo-foo");
    mk("", "?only=xyz"); assert.equal(onlyTag(), "xyz");
    mk("#other=1"); assert.equal(onlyTag(), null);         // unrelated hash → no filter
    mk("#only="); assert.equal(onlyTag(), null);           // empty tag → no filter
    mk(""); assert.equal(onlyTag(), null);
  } finally { g.window = prev; }
});

test("onlyWindow: the shell's window when readable (a same-origin frame), else this pane's own (a top-level page, a cross-origin top, a harness)", () => {
  const g = global as any;
  const prev = g.window;
  try {
    const top: any = { location: { hash: "#only=demo", search: "" } };
    const pane: any = { top, location: { hash: "", search: "" } };
    g.window = pane; assert.equal(onlyWindow(), top); assert.equal(onlyTag(), "demo", "the filter is read from the shell's URL, not the pane's");
    const cross: any = { get location() { throw new Error("SecurityError"); } };
    const pane2: any = { top: cross, location: { hash: "#only=own", search: "" } };
    g.window = pane2; assert.equal(onlyWindow(), pane2); assert.equal(onlyTag(), "own", "a cross-origin top throws on the read: the pane's own URL");
    const solo: any = { location: { hash: "", search: "" } }; solo.top = solo;
    g.window = solo; assert.equal(onlyWindow(), solo, "a top-level page is its own top");
    const noTop: any = { location: { hash: "", search: "" } };
    g.window = noTop; assert.equal(onlyWindow(), noTop, "no top at all (a harness window): the pane's own");
  } finally { g.window = prev; }
});

test("the helper: case-insensitive prefix + reads the shell URL via window.top", () => {
  assert.match(ONLY, /export function onlyTag\(\)/);
  assert.match(ONLY, /window\.top \|\| window/);
  assert.match(ONLY, /onlyTags\(tag\)\.some\(\(t\) => n\.startsWith\(t\)\)/);
});

test("the timeline's standalone helper splits the tag the same way", () => {
  assert.match(TL, /tag\.split\(","\)\.map\(\(t\) => t\.trim\(\)\)\.filter\(Boolean\)\.some\(\(t\) => n\.indexOf\(t\) === 0\)/);
});

test("chat tabs filter by the #only tag", () => {
  assert.match(RENDER, /import \{ onlyTag, matchesOnly, onlyWindow \} from "\.\/only-filter";/);
  assert.match(RENDER, /const onlyHashWindow = onlyWindow\(\);\s*\n\s*onlyHashWindow\.addEventListener\("hashchange", onOnlyHashChange\);/, "the strip's live-edit listener binds to the window the filter is read from (the shell's when framed)");
  assert.match(RENDER, /const visibleIds = ids\.filter\(\(id\) => stripShows\(id, only\)\);/, "the #only= filter rides the one predicate the deferred checks read too (stripShows: tabInView, then matchesOnly over the hash)");
  assert.match(RENDER, /return !only \|\| matchesOnly\(sessions\.get\(id\)\?\.name \?\? tabMeta\.get\(id\)\?\.name \?\? "", only\);/);
  // the filtered ids are what the strip plan renders (tab-groups.ts planStrip, since tab groups 2026-09-04)
  assert.match(RENDER, /const plan = planStrip\(visibleIds,/);
});

test("a filtered view blanks the CHAT BODY too: the active tab it hides goes unfocused, never re-pointed", () => {
  // the filter hid a non-matching TAB but left its transcript rendering — a real session's chat
  // (nimbus) sat in a `#only=api,tests,web` frame, statusline and all (the user 2026-07-16). The
  // whole point of the filter is a clean recording frame, so the selection must follow it.
  // the re-point covers BOTH filters since session views landed (2026-08-18): a hidden or
  // filtered-out active session must not keep its transcript on screen
  // (T357: the #only= filter is applied on top of tabInView and is no peek input, so an active tab it hides reaches
  // this check; the pane goes UNFOCUSED naming it (its transcript leaves the screen, the filter's clean frame holds)
  // and is never re-pointed at the first visible session; the fire-time check reads the predicate visibleIds uses)
  assert.match(RENDER, /if \(activeId && ids\.includes\(activeId\) && !visibleIds\.includes\(activeId\)\) \{\s*\n\s*const hid = activeId;\s*\n\s*setTimeout\(\(\) => \{ if \(activeId === hid && !stripShows\(hid\)\) unfocusHiddenByView\(hid\); \}, 0\);/);
  assert.doesNotMatch(RENDER, /setActive\(next\); \}, 0\);/, "no re-point");
  // the deferred bounce re-validates at FIRE time since the ephemeral peek (2026-08-24): an
  // activation between schedule and fire (a feed click opening a peek) makes the active tab
  // visible again — bouncing then would kick the user off the tab they just opened
});

test("feed cards filter by the #only tag; clear bookkeeping still uses the FULL payload", () => {
  assert.match(FEED, /import \{ onlyTag, matchesOnly \} from "\.\/only-filter";/);
  assert.match(FEED, /const visible = only \? incomingAsks\.filter\(\(a\) => matchesOnly\(a\.name, only\)\) : incomingAsks;/);
  assert.match(FEED, /asks = pendingCleared\.size \? visible\.filter/);
});

test("fleet sessions filter by the #only tag", () => {
  assert.match(FLEET, /import \{ onlyTag, matchesOnly \} from "\.\/only-filter";/);
  assert.match(FLEET, /if \(only && !matchesOnly\(s\.name, only\)\) continue;/);
});

test("the new-session picker seeds the name box with the tag prefix in a filtered view", () => {
  // launching from `#only=demo` prefills `demo-` so a new session stays in view (the user 2026-07-15);
  // only when creating is possible (create mode or pickAllowNew), and the cursor lands after the prefix
  assert.match(RENDER, /const only = \(!pick \|\| pickAllowNew\) \? onlyTag\(\) : null;/);
  assert.match(RENDER, /const seed = only && !only\.includes\(","\) \? only \+ "-" : "";/);
  assert.match(RENDER, /s\.value = seed;/);
  assert.match(RENDER, /if \(seed\) s\.setSelectionRange\(seed\.length, seed\.length\);/);
  assert.match(RENDER, /filterPicker\(seed\);/);
});

test("timeline lanes filter by the #only tag (self-contained helper in the standalone file)", () => {
  assert.match(TL, /function _rompOnlyTag\(\)/);
  assert.match(TL, /function _rompMatchesOnly\(name, tag\)/);
  assert.match(TL, /sessions: data\.sessions\.filter\(\(s\) => _rompMatchesOnly\(s\.name, _only\)\)/);
});

test("the hash listener is a named handler and comes off the shell's window on pagehide (a closed split column must not hold the pane alive)", () => {
  const a = RENDER.indexOf("const onOnlyHashChange = ");
  const p = RENDER.indexOf('window.addEventListener("pagehide", () => onlyHashWindow.removeEventListener', a);
  const b = RENDER.indexOf("\n", p);
  assert.ok(a > 0 && p > a && b > p, "anchors not found: the listener block moved; re-anchor");
  const js = RENDER.slice(a, b).replace(/\(\): void =>/g, "() =>");
  const shell: any = { added: [] as string[], removed: [] as string[], f: null,
    addEventListener(t: string, f: unknown) { this.added.push(t); this.f = f; },
    removeEventListener(t: string, f: unknown) { this.removed.push(t + (f === this.f ? ":same" : ":other")); } };
  const paneHandlers: Record<string, () => void> = {};
  const pane: any = { addEventListener(t: string, f: () => void) { paneHandlers[t] = f; } };
  let repaints = 0;
  new Function("onlyWindow", "renderTabs", "window", js)(() => shell, () => { repaints++; }, pane);
  assert.deepEqual(shell.added, ["hashchange"], "one listener on the window the filter is read from");
  assert.deepEqual(Object.keys(paneHandlers), ["pagehide"], "the pane's own window carries only the pagehide belt");
  shell.f(); assert.equal(repaints, 1, "the named handler repaints the strip");
  paneHandlers.pagehide(); assert.deepEqual(shell.removed, ["hashchange:same"], "the same handler comes off on pagehide");
});
