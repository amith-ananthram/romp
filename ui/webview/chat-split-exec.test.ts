// The page's half of the chat columns, RUN (the chat split, 2026-09-11; a review find: the partition's page-side rules had
// source-shape pins only). The functions render.ts wires into renderTabs, the focus gates and the create flow are lifted
// with esbuild (the skeleton-tabs-wiring.test.ts chipWorld pattern) over a fake parent window that plays the shell —
// __rompChatSets / __rompChatTarget / __rompClaimSession and a counting postMessage — and driven the way renderTabs and
// the message handler drive them: the emptiness post (once per emptiness, reset by a member listed again; never before
// the first strip, never for the first column, never over a create in flight), the stale-active fallback (the partition's
// only: a page with no sets boots as before; the first visible member after the timer; a wanted tab held elsewhere
// retired; re-checked at fire time), the hop to the owner (once, into the owner's frame, never this frame), the claim of a
// created session, the offer of orphaned state, and the adoption of a moved tab's state onto every slice. The pure
// partition (columnHolds), the id shapes and the staged stack are the real modules. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { columnHolds, type ColSets } from "./chat-columns";
import { isProvisionalId } from "./provisional";
import { isSubId } from "./subagent-view";
import { StagedStack } from "./staged-messages";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const fn = (name: string): string => {
  const i = RENDER.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} not found`);
  return RENDER.slice(i, RENDER.indexOf("\n}\n", i) + 3);
};
const line = (name: string): string => {   // a one-line function: to the end of its line
  const i = RENDER.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} not found`);
  return RENDER.slice(i, RENDER.indexOf("\n", i) + 1);
};

const WEB = "11111111-2222-3333-4444-555555555501", API = "11111111-2222-3333-4444-555555555502", TESTS = "11111111-2222-3333-4444-555555555503";
const PROV = "new-abc123", VIEWER = WEB + "/agent/a1";

type Hooks = { posts: Record<string, unknown>[]; forwarded: Record<string, unknown>[]; focused: number; claims: [string, string][];
               timers: (() => void)[]; activated: string[]; reads: number; persisted: number; loaded: string[] };
type Api = {
  render: (ids: string[], visibleIds?: string[]) => void;   // renderTabs' order: read the sets, the emptiness post, the orphan offer, the fallback
  forwardToOwner: (m: Record<string, unknown>) => boolean;
  claimSession: (id: string) => void;
  orphanStateSids: () => string[];
  adoptSessionState: (sid: unknown, state: unknown) => void;
  heldHere: (id: string) => boolean;
  state: () => { activeId: string | null; wantActive: string | null; colSets: ColSets | null; colEmptyPosted: boolean; tabOrderSeen: boolean };
  set: (p: { activeId?: string | null; wantActive?: string | null; provisionalId?: string | null; tabOrderSeen?: boolean; failed?: string[] }) => void;
  maps: { drafts: Map<string, string>; composerCitations: Map<string, unknown[]>; composerFiles: Map<string, string[]>; stagedMsgs: StagedStack };
};
type World = { api: Api; HOOKS: Hooks; W: { sets: ColSets | null; owner: ((sid: string) => unknown) | null }; me: { id: string }; other: { id: string; contentWindow: unknown } };

/** A column page: `col` is its number ("" for the first column), `sets` what the shell's __rompChatSets answers. */
function world(o: { col?: string; sets?: ColSets | null; tabOrderSeen?: boolean; activeId?: string | null; provisionalId?: string | null;
                    wantActive?: string | null; failed?: string[]; owner?: ((sid: string) => unknown) | null }): World {
  const HOOKS: Hooks = { posts: [], forwarded: [], focused: 0, claims: [], timers: [], activated: [], reads: 0, persisted: 0, loaded: [] };
  const me = { id: "f-chat-" + (o.col || "1") };
  const other = { id: "f-chat", contentWindow: { postMessage(m: Record<string, unknown>) { HOOKS.forwarded.push(m); }, focus() { HOOKS.focused++; } } };
  const W = { sets: o.sets === undefined ? {} : o.sets, owner: o.owner === undefined ? null : o.owner };
  const PARENT = {
    postMessage(m: Record<string, unknown>) { HOOKS.posts.push(m); },
    __rompChatSets: () => W.sets,
    __rompChatTarget: (sid: string) => (W.owner ? W.owner(sid) : null),
    __rompClaimSession: (sid: string, col: string) => { HOOKS.claims.push([sid, col]); return true; },
  };
  const win = { parent: PARENT, frameElement: me };
  const js = requireCjs("esbuild").transformSync(
    [line("heldHere"), line("tabInView"), fn("forwardToOwner"), fn("claimSession"), fn("noteColumnEmptiness"),
     fn("orphanStateSids"), fn("noteOrphanState"), fn("staleActiveFallback"), fn("adoptSessionState")].join("\n"), { loader: "ts" }).code;
  const prelude = `
    const { columnHolds, isProvisionalId, isSubId, StagedStack, HOOKS } = W;
    const COL = W.col;
    let colSets = W.sets, tabOrderSeen = W.tabOrderSeen, activeId = W.activeId, provisionalId = W.provisionalId, wantActive = W.wantActive;
    let vanishedId = null;   // T357's tab-that-left; never set in these worlds (the fallback yields to it, pinned in chat-split.test.ts)
    const failedProvisionals = new Set(W.failed || []);
    let colEmptyPosted = false;
    const readColSets = () => { HOOKS.reads++; return W.shell.sets; };
    const peekId = null; const chatVisible = () => true;
    const setTimeout = (f) => { HOOKS.timers.push(f); return HOOKS.timers.length; };
    const setActive = (id) => { HOOKS.activated.push(id); activeId = id; };
    const drafts = new Map(), composerCitations = new Map(), composerFiles = new Map(); const stagedMsgs = new StagedStack();
    const persistDrafts = () => { HOOKS.persisted++; }; const loadComposerFor = (sid) => { HOOKS.loaded.push(sid); };
  `;
  const epilogue = `
    return {
      render: (ids, visibleIds) => { colSets = readColSets(); noteColumnEmptiness(ids); noteOrphanState(); staleActiveFallback(ids, visibleIds || ids.filter(tabInView)); },
      forwardToOwner, claimSession, orphanStateSids, adoptSessionState, heldHere,
      state: () => ({ activeId, wantActive, colSets, colEmptyPosted, tabOrderSeen }),
      set: (p) => { if ("activeId" in p) activeId = p.activeId; if ("wantActive" in p) wantActive = p.wantActive; if ("provisionalId" in p) provisionalId = p.provisionalId;
                    if ("tabOrderSeen" in p) tabOrderSeen = p.tabOrderSeen; if ("failed" in p) { failedProvisionals.clear(); for (const f of p.failed) failedProvisionals.add(f); } },
      maps: { drafts, composerCitations, composerFiles, stagedMsgs },
    };
  `;
  const make = new Function("W", "window", prelude + js + epilogue) as (w: unknown, win: unknown) => Api;
  const api = make({ columnHolds, isProvisionalId, isSubId, StagedStack, HOOKS, col: o.col || "", sets: W.sets, shell: W,
                     tabOrderSeen: o.tabOrderSeen ?? true, activeId: o.activeId ?? null, provisionalId: o.provisionalId ?? null,
                     wantActive: o.wantActive ?? null, failed: o.failed || [] }, win);
  return { api, HOOKS, W, me, other };
}
const fire = (h: Hooks): void => { const t = h.timers.splice(0); for (const f of t) f(); };

test("the emptiness post: once per emptiness, reset by a member listed again; nothing before the first strip, for the first column, or with no sets", () => {
  const w = world({ col: "2", sets: { "2": [API, TESTS] }, activeId: API });
  w.api.render([WEB, API, TESTS]);
  assert.deepEqual(w.HOOKS.posts, [], "a member listed: nothing to say");
  w.api.render([WEB, TESTS]);
  assert.deepEqual(w.HOOKS.posts, [], "one member still listed: the column stands");
  w.api.render([WEB]);
  assert.deepEqual(w.HOOKS.posts, [{ romp: "colEmpty", gone: [API, TESTS] }], "none listed: the shell is told which ids are gone");
  w.api.render([WEB]); w.api.render([]);
  assert.equal(w.HOOKS.posts.length, 1, "said once per emptiness, not per render");
  w.api.render([WEB, TESTS]);
  assert.equal(w.HOOKS.posts.length, 1, "a member listed again resets the latch…");
  w.api.render([WEB]);
  assert.equal(w.HOOKS.posts.length, 2, "…so the next emptiness is said again");
  assert.deepEqual(w.HOOKS.posts[1], { romp: "colEmpty", gone: [API, TESTS] });
  // the gates
  const early = world({ col: "2", sets: { "2": [API] }, tabOrderSeen: false });
  early.api.render([WEB]);
  assert.deepEqual(early.HOOKS.posts, [], "before the kernel's first strip the page has not heard the board: it says nothing");
  early.api.set({ tabOrderSeen: true }); early.api.render([WEB]);
  assert.equal(early.HOOKS.posts.length, 1, "…and speaks once it has");
  const first = world({ col: "", sets: { "2": [API] } });
  first.api.render([]);
  assert.deepEqual(first.HOOKS.posts, [], "the first column has no entry and never empties");
  const bare = world({ col: "2", sets: null });
  bare.api.render([]);
  assert.deepEqual(bare.HOOKS.posts, [], "no sets (the phone, no shell): no partition to speak to");
  const noEntry = world({ col: "3", sets: { "2": [API] } });
  noEntry.api.render([WEB]);
  assert.deepEqual(noEntry.HOOKS.posts, [], "a column with no entry (already pruned) has nothing to report");
});

test("a create in flight, or a failed one still holding its text, keeps the column: no emptiness post while it stands", () => {
  const w = world({ col: "2", sets: { "2": [API] }, provisionalId: PROV, activeId: PROV });
  w.api.render([WEB]);
  assert.deepEqual(w.HOOKS.posts, [], "the create in flight is this column's own tab: the column would close under it and its queued text die");
  w.api.set({ provisionalId: null, failed: [PROV] }); w.api.render([WEB]);
  assert.deepEqual(w.HOOKS.posts, [], "a failed create holding its text keeps it too, until its ✕ discards it");
  w.api.set({ failed: [] }); w.api.render([WEB]);
  assert.deepEqual(w.HOOKS.posts, [{ romp: "colEmpty", gone: [API] }], "with the create gone the emptiness is said");
});

test("the stale-active fallback belongs to the partition: no sets, nothing scheduled; with sets, the first visible member after the timer, a wanted tab held elsewhere retired", () => {
  const bare = world({ col: "", sets: null, wantActive: WEB });
  bare.api.render([WEB, API]);
  assert.deepEqual(bare.HOOKS.timers, [], "standalone and the VS Code webview boot exactly as before: the first arriving frame is adopted, no timer");
  assert.equal(bare.api.state().wantActive, WEB, "…and the persisted tab still stands for the restore");
  const w = world({ col: "", sets: { "2": [API] }, wantActive: API });
  w.api.render([WEB, API, TESTS]);
  assert.equal(w.HOOKS.timers.length, 1, "the wanted tab is another column's now: a fallback is scheduled");
  assert.deepEqual(w.HOOKS.activated, [], "…deferred, like the hidden-active re-point");
  fire(w.HOOKS);
  assert.deepEqual(w.HOOKS.activated, [WEB], "the first visible member of this column");
  assert.equal(w.api.state().wantActive, null, "the want for a tab moved away is retired: a frame for it must not re-activate it");
  assert.equal(w.api.state().activeId, WEB);
  w.api.render([WEB, API, TESTS]);
  assert.deepEqual(w.HOOKS.timers, [], "with an active tab, nothing more");
  const gone = world({ col: "2", sets: { "2": [API, TESTS] }, wantActive: API });
  gone.api.render([WEB, TESTS]);
  assert.deepEqual(gone.HOOKS.timers, [], "a wanted tab this column HOLDS but the strip does not list (its session ended, or its host is away) is awaited, not replaced: the pane stays unfocused naming it (T357), and the column's other member is one click away");
  assert.equal(gone.api.state().wantActive, API, "…and the want stands for the restore");
});

test("the fallback yields to a wanted tab this column holds and lists, to a create in flight, to an active tab and to an empty strip; and it re-checks at fire time", () => {
  const want = world({ col: "2", sets: { "2": [API, TESTS] }, wantActive: API });
  want.api.render([WEB, API, TESTS]);
  assert.deepEqual(want.HOOKS.timers, [], "its frame is on the way: the restore takes it");
  const prov = world({ col: "2", sets: { "2": [API] }, provisionalId: PROV });
  prov.api.render([WEB, API]);
  assert.deepEqual(prov.HOOKS.timers, [], "a create in flight is what this column shows");
  const active = world({ col: "2", sets: { "2": [API] }, activeId: API });
  active.api.render([WEB, API]);
  assert.deepEqual(active.HOOKS.timers, [], "an active tab: nothing to fall back to");
  const empty = world({ col: "2", sets: { "2": [API] } });
  empty.api.render([WEB]);
  assert.deepEqual(empty.HOOKS.timers, [], "no visible member: nothing to activate (the emptiness post is what speaks)");
  const early = world({ col: "2", sets: { "2": [API] }, tabOrderSeen: false });
  early.api.render([API]);
  assert.deepEqual(early.HOOKS.timers, [], "before the first strip the board is unknown");
  // re-checked at fire time: an activation between the schedule and the timer wins (the no-flap rule)
  const race = world({ col: "2", sets: { "2": [API, TESTS] } });
  race.api.render([API, TESTS]);
  assert.equal(race.HOOKS.timers.length, 1);
  race.api.set({ activeId: TESTS }); fire(race.HOOKS);
  assert.deepEqual(race.HOOKS.activated, [], "a frame adopted meanwhile: the fallback stands down");
  const moved = world({ col: "2", sets: { "2": [API, TESTS] } });
  moved.api.render([API, TESTS]);
  moved.W.sets = { "2": [TESTS] };   // the shell moved the first member away before the timer…
  moved.api.render([API, TESTS]);    // …and its store write's storage event re-rendered (the sets re-read, a second timer)
  assert.equal(moved.HOOKS.timers.length, 2);
  fire(moved.HOOKS);
  assert.deepEqual(moved.HOOKS.activated, [TESTS], "the member picked at schedule time is no longer held by the sets the latest render read: the first timer does nothing, the second activates the member that is");
});

test("a message about a session another column holds is posted into the owner's frame once, with the keyboard; the owner being this frame, or no shell, means act locally", () => {
  const w = world({ col: "2", sets: { "2": [API] }, owner: (sid) => (sid === WEB ? w.other : sid === API ? w.me : null) });
  const m = { type: "focus", id: WEB, anchor: "u1" };
  assert.equal(w.api.forwardToOwner(m), true, "the owner is another frame: forwarded");
  assert.deepEqual(w.HOOKS.forwarded, [m], "the same message, into the owner's window");
  assert.equal(w.HOOKS.focused, 1, "the keyboard follows");
  assert.equal(w.api.forwardToOwner({ type: "focus", id: API }), false, "the owner is this frame: the caller acts locally");
  assert.equal(w.api.forwardToOwner({ type: "focus", id: TESTS }), false, "no frame named (the first column derives, here unknown to the fake): act locally");
  assert.equal(w.HOOKS.forwarded.length, 1);
  const alone = world({ col: "", sets: { "2": [API] }, owner: null });
  assert.equal(alone.api.forwardToOwner({ type: "focus", id: API }), false, "the shell names no frame: local");
});

test("a later column claims a created session on the shell and re-reads the sets; the first column claims nothing", () => {
  const w = world({ col: "2", sets: { "2": [API] } });
  w.api.render([WEB, API]);
  const reads = w.HOOKS.reads;
  w.W.sets = { "2": [API, TESTS] };   // what the shell's claim will answer
  w.api.claimSession(TESTS);
  assert.deepEqual(w.HOOKS.claims, [[TESTS, "2"]], "claimed for THIS column");
  assert.equal(w.HOOKS.reads, reads + 1, "and the sets re-read at once, so the switch that follows finds it held here");
  assert.deepEqual(w.api.state().colSets, { "2": [API, TESTS] });
  assert.equal(w.api.heldHere(TESTS), true);
  const first = world({ col: "", sets: { "2": [API] } });
  first.api.claimSession(TESTS);
  assert.deepEqual(first.HOOKS.claims, [], "the first column derives: a session no entry lists is already its own");
});

test("orphaned state: sids held for sessions this column does not show are offered once the board is heard; held, provisional and viewer ids never", () => {
  const w = world({ col: "2", sets: { "2": [API] } });
  w.api.maps.drafts.set(API, "mine"); w.api.maps.drafts.set(WEB, "a v1 blob's draft for a session the first column shows");
  w.api.maps.composerFiles.set(TESTS, ["/tmp/a.png"]); w.api.maps.composerCitations.set(WEB, [{ title: "a card" }]);
  w.api.maps.stagedMsgs.push(WEB + "9", { text: "s", cites: [] });
  w.api.maps.drafts.set(PROV, "typed into the create in flight"); w.api.maps.drafts.set(VIEWER, "a viewer's");
  assert.deepEqual(w.api.orphanStateSids().sort(), [WEB, TESTS, WEB + "9"].sort(), "every slice, each sid once; this column's member and its own tabs excluded");
  w.api.render([WEB, API, TESTS]);
  const offers = w.HOOKS.posts.filter((p) => p.romp === "orphanState");
  assert.equal(offers.length, 1);
  assert.deepEqual((offers[0].sids as string[]).sort(), [WEB, TESTS, WEB + "9"].sort());
  w.api.render([WEB, API, TESTS]);
  assert.equal(w.HOOKS.posts.filter((p) => p.romp === "orphanState").length, 2, "offered again on the next render while it remains (the shell takes it when the owner's page can hear)");
  w.api.maps.drafts.delete(WEB); w.api.maps.composerCitations.delete(WEB); w.api.maps.composerFiles.delete(TESTS); w.api.maps.stagedMsgs.takeAll(WEB + "9");
  w.api.render([WEB, API, TESTS]);
  assert.equal(w.HOOKS.posts.filter((p) => p.romp === "orphanState").length, 2, "taken: nothing more to offer");
  const early = world({ col: "2", sets: { "2": [API] }, tabOrderSeen: false });
  early.api.maps.drafts.set(WEB, "x");
  early.api.render([WEB, API]);
  assert.deepEqual(early.HOOKS.posts, [], "before the first strip nothing is offered");
  const bare = world({ col: "", sets: null });
  bare.api.maps.drafts.set(WEB, "x");
  bare.api.render([API]);
  assert.deepEqual(bare.HOOKS.posts, [], "no partition: everything is held here");
});

test("adoptSessionState joins every slice onto what is already here, persists once, and fills the box only for the active tab; junk is ignored", () => {
  const w = world({ col: "2", sets: { "2": [API] }, activeId: API });
  w.api.maps.drafts.set(API, "already here"); w.api.maps.composerFiles.set(API, ["/tmp/a.png"]); w.api.maps.stagedMsgs.push(API, { text: "first", cites: [] });
  w.api.adoptSessionState(API, { draft: "moved in", citations: [{ title: "a card" }], files: ["/tmp/b.png", 7, ""], staged: [{ text: "second", cites: [] }] });
  assert.equal(w.api.maps.drafts.get(API), "already here\n\nmoved in", "joined, never over");
  assert.deepEqual(w.api.maps.composerCitations.get(API), [{ title: "a card" }]);
  assert.deepEqual(w.api.maps.composerFiles.get(API), ["/tmp/a.png", "/tmp/b.png"], "strings only");
  assert.deepEqual(w.api.maps.stagedMsgs.list(API), [{ text: "first", cites: [] }, { text: "second", cites: [] }], "in order: what was here, then what arrived");
  assert.equal(w.HOOKS.persisted, 1);
  assert.deepEqual(w.HOOKS.loaded, [API], "the active tab's box is refilled");
  w.api.adoptSessionState(TESTS, { draft: "for another tab" });
  assert.equal(w.api.maps.drafts.get(TESTS), "for another tab");
  assert.deepEqual(w.HOOKS.loaded, [API], "a background tab's box is not touched");
  assert.equal(w.HOOKS.persisted, 2);
  w.api.adoptSessionState("", { draft: "x" }); w.api.adoptSessionState(API, null); w.api.adoptSessionState(API, "junk");
  assert.equal(w.HOOKS.persisted, 2, "junk changes nothing");
});
