// Session views on the chat side (the user 2026-08-18; TAG model 2026-08-23; ALL default
// 2026-08-24): the kernel's views blob — echoed on every tabOrder push — gates which sessions get
// TABS. Two built-in sentinels: "all" — the default — shows LITERALLY EVERYTHING (the hidden set retired 2026-08-24);
// "untagged" keeps the old default's meaning (a tag marks a specialized session, excluded from the
// untagged view and shown under its tag views). A tagged session is a BACKGROUND session: still running, judged,
// carded; the + picker lists it under "Hidden — reveal" and the timeline's corner panel counts it,
// so nothing runs in secret (the 2026-08-11 rule this feature deliberately carves an exception
// into, keeping its spirit). Executed tests on the pure module + source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { viewVisible, viewsKey, revealIn, viewTagUnion } from "../../ui/webview/session-views";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const G = { id: "g1", name: "pool", color: "#DD42FF", members: ["s2"] };

test("executed: All shows literally everything (hidden retired 2026-08-24); untagged the tagless; a tag view its members", () => {
  assert.equal(viewVisible(null, "s1"), true);
  assert.equal(viewVisible({ active: "all", hidden: ["s1"] }, "s1"), true, "a legacy hidden entry is IGNORED — nothing hides from All (the user 2026-08-24)");
  assert.equal(viewVisible({ active: "all", tags: [G] }, "s2"), true, "TAGGED → All still shows it (2026-08-24)");
  assert.equal(viewVisible({ active: "all", tags: [G] }, "s1"), true, "untagged → shown");
  assert.equal(viewVisible({ active: "untagged", tags: [G] }, "s2"), false, "TAGGED → out of the untagged view");
  assert.equal(viewVisible({ active: "untagged", tags: [G] }, "s1"), true, "tagless → the untagged view shows it");
  assert.equal(viewVisible({ active: "untagged", hidden: ["s1"], tags: [G] }, "s1"), true, "…and a legacy hidden entry does not hide in untagged either — only membership excludes");
  assert.equal(viewVisible({ active: "g1", hidden: ["s2"], tags: [G] }, "s2"), true, "a tag view shows its members");
  assert.equal(viewVisible({ active: "g1", tags: [G] }, "s1"), false);
  assert.equal(viewVisible({ active: "gone", tags: [] }, "s1"), true, "an orphaned active fails open");
  // the pre-rename key an un-updated kernel still pushes reads identically
  assert.equal(viewVisible({ active: "untagged", groups: [G] }, "s2"), false, "legacy `groups` key honored");
  assert.equal(viewVisible({ active: "g1", groups: [G] }, "s2"), true);
});

test("executed: reveal ADDS to the chat lens, never mutates membership (per-surface, 2026-08-25)", () => {
  // the reveal keys on the CHAT surface's lens now: the minimal move is ADDITIVE — the holder tag
  // (or the none bucket) JOINS the selection, and what the user was looking at stays visible.
  // Membership is still never touched (2026-08-23 — a peek never strips a tag), and the legacy
  // scalar `active` is left alone entirely (old clients own it).
  const rev = revealIn({ active: "g1", tags: [G] }, "s1");
  assert.deepEqual(rev.actives!.chat, { none: true, tags: ["pool"] },
    "tagless from a tag view: the none bucket JOINS — the pool selection stays visible");
  assert.equal(rev.active, "g1", "the legacy scalar is never rewritten");
  const rev2 = revealIn({ active: "untagged", tags: [G] }, "s2");
  assert.deepEqual(rev2.actives!.chat, { none: true, tags: ["pool"] },
    "tagged: its holder tag joins the selection; the untagged pick stays");
  assert.deepEqual(rev2.tags![0].members, ["s2"], "membership untouched — the jump never strips a tag");
  const revAll = revealIn({ active: "all", tags: [G] }, "s2");
  assert.equal(revAll.actives?.chat, undefined, "everything visible under All: nothing changes at all");
  const rev4 = revealIn({ active: "g1", tags: [G] }, "s2");
  assert.equal(rev4.actives?.chat, undefined, "already visible in the active tag: nothing changes");
});

test("executed: the canonical key ignores list order AND which key the kernel used", () => {
  const a = { active: "g1", hidden: ["b", "a"], tags: [{ id: "g1", members: ["y", "x"] }] };
  const b = { active: "g1", hidden: ["a", "b"], groups: [{ id: "g1", members: ["x", "y"] }] };
  assert.equal(viewsKey(a), viewsKey(b), "a legacy-keyed echo still reconciles an optimistic edit");
  assert.notEqual(viewsKey(a), viewsKey({ active: "all", hidden: ["a", "b"], tags: [] }));
});

test("the tabOrder frame carries the blob and the strip filters on it, composing with #only", () => {
  // the frame's provenance rides along since T233 (captureViews still runs before the strip is applied; the
  // kernel's own name, selfHost, is adopted first of all — pr-links.test.ts pins that line)
  assert.match(RENDER, /else if \(m\.type === "tabOrder"\) \{\s*\n\s*if \(typeof m\.selfHost === "string" && m\.selfHost\) adoptSelfHost\(m\.selfHost\);[^\n]*\n\s*captureViews\(m\.views \|\| null\);\s*\n\s*applyTabOrder\(m\.order, m\.tabs, \{ reemit: m\.reemit === true, freshHost: typeof m\.freshHost === "string" \? m\.freshHost : undefined \}, m\.live\);\s*\n\s*\}/,
    "echo-less frames still reach captureViews — an older kernel must age out a pending edit");
  assert.match(RENDER, /const visibleIds = ids\.filter\(\(id\) => stripShows\(id, only\)\);/, "the view (tabInView, a peek counts) and the #only= filter through ONE predicate, stripShows (T357 later lows)");
  assert.match(RENDER, /function stripShows\(id: string, only: string \| null = onlyTag\(\)\): boolean \{\s*\n\s*if \(!tabInView\(id\)\) return false;\s*\n\s*return !only \|\| matchesOnly\(sessions\.get\(id\)\?\.name \?\? tabMeta\.get\(id\)\?\.name \?\? "", only\);/, "the predicate's body: the view first (a peek counts), then the #only= filter over the name ladder (T357 later lows)");
  // the active tab under BOTH filters (T357): a VIEW that excludes it is covered by the peek, asserted by
  // captureViews before applyTabOrder on every tabOrder frame (visibility is a pure function of the views blob); the
  // #only= filter is applied on top of tabInView and is no peek input, so an only-filtered active tab reaches the
  // check below and the pane goes UNFOCUSED naming it, never re-pointed at another session
  assert.match(RENDER, /if \(activeId && ids\.includes\(activeId\) && !visibleIds\.includes\(activeId\)\) \{/);
  assert.match(RENDER, /function tabInView\(id: string\): boolean \{ return id === peekId \|\| chatVisible\(id\); \}/);
  assert.match(RENDER, /captureViews\(m\.views \|\| null\);\s*\n\s*applyTabOrder\(/, "the peek is asserted before the strip is applied, on every tabOrder frame");
});

test("every cycling path walks the VISIBLE order — keyboard can never land on a hidden session", () => {
  const hits = RENDER.match(/const ord = visibleOrder\(\);\s*(?:\/\/[^\n]*)?\n/g) || [];
  assert.equal(hits.length, 3, "focused-tab arrows, window arrows, and the shell's cycleTab");
  assert.doesNotMatch(RENDER, /setActive\(order\[\(i \+ dir \+ order\.length\) % order\.length\]\)/,
    "no raw-order cycle survives");
  // …and a tab folded away under a collapsed section header (tab groups, 2026-09-04) is not visible
  // either: the visible order drops the folded ids, so ←/→ never land on a hidden node
  assert.match(RENDER, /function visibleOrder\(\): string\[\] \{ return order\.filter\(\(id\) => tabInView\(id\) && !collapsedTabIds\.has\(id\)\); \}/);
});

test("optimistic edits hold until the kernel echoes them exactly or acks the write — never a frame count (2026-09-05)", () => {
  // the three-frame yield that lived here dropped good edits and kept refused ones alike; the
  // write's ack (views-writes.test.ts) is the event that settles the copy
  assert.match(RENDER, /function captureViews\(v: SessionViews \| null\) \{[\s\S]{0,1300}viewsKey\(v\) === viewsKey\(pendingSessionViews\)/);
  assert.doesNotMatch(RENDER, /pendingViewsAge/);
  assert.match(RENDER, /function postViews\(v: SessionViews, edited: string\[\] = \[\]\) \{[\s\S]{0,600}setTimelineViews", views: v, writeId, edited/);
});

test("a view-filtered session keeps one visible home: the picker's other-view section, and picking jumps views", () => {
  // (the label was "Hidden — reveal" until the hidden set retired, the user 2026-08-24 — the
  // section now serves tag-filtered sessions, same no-secret rule from 2026-08-11)
  assert.match(RENDER, /isOpenTab\(it\.id\) && !tabInView\(it\.id\)/);
  assert.match(RENDER, /label\("In another view — open"\)/);
  assert.match(RENDER, /time\.textContent = "other view";/);
  assert.match(RENDER, /revealSession\(it\.id\);\s*\/\/ its tab already exists[\s\S]{0,120}setActive\(it\.id\);/);
});

test("the hide MECHANISM is fully retired (the user 2026-08-24) — reveal survives as the view jump", () => {
  assert.doesNotMatch(RENDER, /Hide from chat & timeline/);
  assert.doesNotMatch(RENDER, /hideIn\(/, "no hide gesture anywhere");
  assert.match(RENDER, /function revealSession\(id: string\) \{ const r = revealIn\(effViews\(\), id\); postLens\(\{ active: r\.active, actives: r\.actives \}\); \}/,
    "the reveal is a lens write on the store's blob (the 2026-09-05 review)");
});

test("federation carries the LOCAL kernel's views blob through merged tabOrder re-emits", () => {
  const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  // stored only when its write sequence is at least the stored one (or the one the local kernel's caps frame
  // announced), the last blob turned away is kept for that caps frame to adopt, and the announced slot is
  // re-derived from the stored blob before it moves — cleared only when the adoption changes it
  // (federation-views-seq.test.ts executes all four rules)
  assert.match(FED, /if \(host === LOCAL && m\.views && typeof m\.views === "object"\) \{\s*\n\s*if \(adoptViews\(this\.localViews, m\.views, this\.localViewsAnnounced\)\) \{ this\.localViewsAnnounced = announcedAfter\(this\.localViews, m\.views, this\.localViewsAnnounced\); this\.localViews = m\.views; this\.localViewsRejected = null; \}\s*\n\s*else this\.localViewsRejected = m\.views;\s*\n\s*\}/);
  // the merged frame also carries the LOCAL kernel's own name (selfHost), read from its tabOrder frame (pr-links)
  assert.match(FED, /\{ type: "tabOrder", order, tabs, live, views: this\.localViews \?\? undefined, selfHost: this\.localSelfHost \|\| undefined \}/,
    "without this the browser dashboard's chat never receives the blob at all");
});

test("view edits are INTENT ops — they survive a kernel-restart reconnect instead of dropping", () => {
  const PIPE = fs.readFileSync(path.resolve(process.cwd(), "src", "pipe-intent.ts"), "utf8");
  assert.match(PIPE, /"setTimelineViews"/);
});

test("hiding the last visible session blanks its transcript and the placeholder says why", () => {
  assert.match(RENDER, /const blank = !visibleIds\.length && ids\.length > 0 && !tabInView\(activeId\);/);
  assert.match(RENDER, /Every session is hidden from this view\./);
  assert.match(RENDER, /allHiddenBlanked = false;/, "…and the transcript restores when anything is visible again");
});

test("executed: a REMOTE tag is a view too — resolved, labeled by its host, gone-falls-open (federation v0)", () => {
  // the kernel joins attached kernels' tags into remoteTags (read-only, id = "host:tagid", members
  // already respelled viewer-relative); picking one filters exactly like a local tag
  const rt = { active: "alpha:g9", tags: [], remoteTags: [{ id: "alpha:g9", host: "alpha", members: ["alpha:rs1", "x"] }] };
  assert.equal(viewVisible(rt, "alpha:rs1"), true);
  assert.equal(viewVisible(rt, "y"), false);
  // its kernel detached → the view falls OPEN, never trapping the viewer in an empty view
  assert.equal(viewVisible({ active: "alpha:g9", tags: [] }, "y"), true);
  // the echo key EXCLUDES remoteTags: derived kernel state, not an edit — two blobs differing only
  // there must reconcile the same optimistic edit
  const a = { active: "all", hidden: [], tags: [] };
  const b = { active: "all", hidden: [], tags: [], remoteTags: [{ id: "alpha:g9" }] };
  assert.equal(viewsKey(a), viewsKey(b));
});

test("executed: a tag view is the NAME-KEYED union (user ruling 2026-08-24) — kernels are plumbing", () => {
  const both = { active: "gL",
                 tags: [{ id: "gL", name: "team", members: ["local1"] }],
                 remoteTags: [{ id: "TESTHOST-A:g1", host: "TESTHOST-A", name: "team", members: ["m1"] }] };
  assert.equal(viewVisible(both, "local1"), true);
  assert.equal(viewVisible(both, "m1"), true, "the remote store's member joins the local id's view");
  assert.equal(viewVisible(both, "other"), false);
  assert.equal(viewVisible({ ...both, active: "TESTHOST-A:g1" }, "local1"), true, "either id, same union");
});

test("executed: untagged excludes by the UNION — a remote-homed tag counts (the user 2026-08-24)", () => {
  // the reported bug: tagging from the chat left the session in the untagged view, because only
  // LOCAL tags excluded there. Held by any kernel's tag = tagged, on every surface.
  const v = { active: "untagged", hidden: [], tags: [],
              remoteTags: [{ id: "alpha:g1", host: "alpha", name: "workers", members: ["cards1"] }] };
  assert.equal(viewVisible(v, "cards1"), false, "remote-homed tag → out of untagged");
  assert.equal(viewVisible(v, "other"), true);
});


test("executed: the union renders in the USER'S order — tagOrder governs, remote-homed names hold position (the user 2026-08-25)", () => {
  const v = {
    active: "all",
    tags: [
      { id: "g1", name: "alpha", color: "#DD42FF", members: [] },
      { id: "g2", name: "beta", color: "#4EC9B0", members: [] },
    ],
    remoteTags: [{ id: "TESTHOST-A:r1", host: "TESTHOST-A", name: "remotepool", color: "#7aa2f7", members: [] }],
  };
  assert.deepEqual(viewTagUnion(v).map((u) => u.name), ["alpha", "beta", "remotepool"],
    "no order yet → natural (array, then remotes) order");
  assert.deepEqual(viewTagUnion({ ...v, tagOrder: ["remotepool", "alpha"] }).map((u) => u.name),
    ["remotepool", "alpha", "beta"],
    "the dragged order governs — the remote-homed name holds its position, viewer-side; unlisted names follow naturally");
  // the echo key carries the order, so an optimistic drag clears on the kernel's echo and never flaps
  assert.notEqual(viewsKey({ ...v, tagOrder: ["remotepool", "alpha"] }), viewsKey(v),
    "tagOrder is part of the canonical echo compare");
  // a user-typed name CAN be a prototype key (adversarial review 2026-08-25): the name-keyed
  // builder crashed on {}["constructor"] resolving to Function — null-prototype lookups now
  assert.deepEqual(viewTagUnion({ tags: [{ id: "p1", name: "constructor", members: [] }], tagOrder: ["constructor"] })
    .map((u) => u.name), ["constructor"], "a prototype-key tag name builds and orders cleanly");
});

test("executed: a union whose local tag wears a create's placeholder id is marked pending — it renders and takes no gesture (the 2026-09-05 review)", () => {
  const A = "11111111-2222-3333-4444-555555555501", B = "11111111-2222-3333-4444-555555555502";
  const v: any = { active: "all", tags: [{ id: "gA", name: "web", color: "#3b82f6", members: [A] }, { id: "pending-abc", name: "api", color: "#54B204", members: [B] }] };
  const u = viewTagUnion(v);
  assert.equal(u.find((g) => g.name === "web")!.pending, undefined);
  assert.equal(u.find((g) => g.name === "api")!.pending, true);
  assert.equal(u.find((g) => g.name === "api")!.localId, "pending-abc", "the placeholder still rides for rendering");
  assert.deepEqual(u.find((g) => g.name === "api")!.members, [B], "…and the row shows its members");
  // the ack's blob replaces the row with the kernel's id: no longer pending
  const after = viewTagUnion({ ...v, tags: [v.tags[0], { id: "g9", name: "api", color: "#54B204", members: [B] }] });
  assert.equal(after.find((g) => g.name === "api")!.pending, undefined);
  // a same-named REAL tag ahead of the placeholder owns the union: not pending
  const twin = viewTagUnion({ ...v, tags: [{ id: "g9", name: "api", color: "", members: [] }, v.tags[1]] });
  assert.equal(twin.find((g) => g.name === "api")!.pending, undefined);
  assert.equal(twin.find((g) => g.name === "api")!.localId, "g9");
});
