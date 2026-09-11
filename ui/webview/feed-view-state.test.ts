// Feed disclosure state across a reload (the user 2026-07-24): a kernel restart reloads the page, which used
// to wipe every card section you had opened. The state now round-trips through localStorage and SELF-CLEANS
// against the kernel's live card set. SYNTHETIC ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import {
  emptyViewState, parseViewState, serializeViewState, pruneViewState, capViewState,
  keyIsLive, viewStateSize, VIEW_STATE_KEY, VIEW_STATE_CAP, type FeedViewState, threadKey, threadKeys, FEED_COLUMNS } from "./feed-view-state";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

function sample(): FeedViewState {
  return {
    v: 1,
    sec: { "card-a": "bg", "card-b": "summary" },
    tree: ["card-a:n1", "card-b:n2"],
    nodes: ["card-a:n3"],
    logs: ["card-b:n4"],
    asks: ["card-a"],
    threads: [],   // the card-prune tests below assert on CARD state; the thread exemption has its own
    cols: ["completed"], order: ["asks", "completed", "needsInput"],
    focused: false,
  };
}

test("the state round-trips through serialize/parse unchanged", () => {
  const s = sample();
  assert.deepEqual(parseViewState(serializeViewState(s)), s);
});

test("a corrupt, missing, or foreign-version blob reads as empty — never throws", () => {
  // losing your open sections is acceptable; taking the whole feed down with a JSON error is not
  assert.deepEqual(parseViewState("{not json"), emptyViewState());
  assert.deepEqual(parseViewState(null), emptyViewState());
  assert.deepEqual(parseViewState(""), emptyViewState());
  assert.deepEqual(parseViewState(JSON.stringify({ v: 99, sec: { a: "bg" } })), emptyViewState(),
    "a future/older schema is discarded rather than half-read");
  assert.deepEqual(parseViewState(JSON.stringify({ v: 1, sec: "nope", tree: 5 })), emptyViewState(),
    "wrong-typed fields degrade to empty collections");
});

test("non-string entries are filtered out rather than trusted", () => {
  const got = parseViewState(JSON.stringify({ v: 1, sec: { a: 1, b: "bg" }, tree: ["x", 7, null], nodes: [], logs: [], asks: [] }));
  assert.deepEqual(got.sec, { b: "bg" });
  assert.deepEqual(got.tree, ["x"]);
});

test("pruning drops entries whose card left the payload, and keeps the rest", () => {
  // the self-clean: a cleared/archived card's sections go with it, on the event, not on a timer
  const pruned = pruneViewState(sample(), new Set(["card-a"]));
  assert.deepEqual(pruned.sec, { "card-a": "bg" }, "card-b's section is gone with card-b");
  assert.deepEqual(pruned.tree, ["card-a:n1"]);
  assert.deepEqual(pruned.nodes, ["card-a:n3"]);
  assert.deepEqual(pruned.logs, [], "card-b's log expand is gone");
  assert.deepEqual(pruned.asks, ["card-a"]);
});

test("an itemId containing a colon is not mis-attributed by the prune", () => {
  // REAL itemIds carry colons: "provisional:<fsid>", "awaiting:<fsid>", "blocked:<fsid>". Splitting a key on
  // its FIRST colon would read this card as "blocked" and prune state that is very much live.
  const s: FeedViewState = {
    v: 1, sec: { "blocked:sess-7": "bg" }, tree: ["blocked:sess-7:n1"], nodes: [], logs: [], asks: [],
    threads: [], cols: [], order: [], focused: false,
  };
  const pruned = pruneViewState(s, new Set(["blocked:sess-7"]));
  assert.deepEqual(pruned.sec, { "blocked:sess-7": "bg" }, "the colon-bearing id survives");
  assert.deepEqual(pruned.tree, ["blocked:sess-7:n1"], "and so does its node key");
  // an unrelated live card never claims another card's keys
  assert.equal(keyIsLive("blocked:sess-7:n1", new Set(["blocked:sess-9"])), false);
  assert.equal(keyIsLive("blocked:sess-7:n1", new Set(["awaiting:sess-7"])), false);
});

test("where ownership is undecidable the bias is to KEEP, not to drop", () => {
  // A bare "blocked" is never itself a card (the kernel always emits "blocked:<fsid>"), so this is
  // theoretical. Pinned anyway to record the deliberate direction: a lingering entry costs bytes and is
  // bounded by the cap, while a wrong drop silently loses sections the user opened — the exact failure this
  // module exists to prevent.
  assert.equal(keyIsLive("blocked:sess-7:n1", new Set(["blocked"])), true);
});

test("pruning against an empty live set clears everything", () => {
  // correct in itself (no cards → no sections); feed.ts is what guarantees this only runs on a real payload
  assert.equal(viewStateSize(pruneViewState(sample(), new Set())), 0);
});

test("the cap is a backstop that trims cheap state first and section choices last", () => {
  const big: FeedViewState = {
    v: 1,
    sec: { a: "bg", b: "summary" },
    tree: Array.from({ length: 10 }, (_, i) => `a:t${i}`),
    nodes: Array.from({ length: 10 }, (_, i) => `a:n${i}`),
    logs: Array.from({ length: 10 }, (_, i) => `a:l${i}`),
    asks: ["a"],
    threads: ["sid-1"], cols: [], order: [], focused: false,
  };
  const capped = capViewState(big, 20);
  assert.equal(viewStateSize(capped), 20);
  assert.deepEqual(capped.sec, { a: "bg", b: "summary" }, "the per-card section is what you notice losing — trimmed last");
  assert.equal(capped.logs.length, 0, "per-node log expands are cheapest to re-open — trimmed first");
  assert.deepEqual(capped.threads, ["sid-1"], "a folded thread is one entry per session and outlives the cheap state");
});

// ── collapsed THREADS (the user 2026-07-31) ───────────────────────────────────────────────────────────
test("a folded thread SURVIVES the card prune — that is the whole point of it", () => {
  // Every other entry describes a card, so a vanished card makes its entry meaningless. A folded thread
  // describes a SESSION: it has to hold while that session has no cards on the board, or clearing the last
  // card would silently re-expand the thread and the next card would arrive unfolded.
  const s: FeedViewState = {
    v: 1, sec: { "card-a": "bg" }, tree: [], nodes: [], logs: [], asks: [], threads: ["sid-quiet"], cols: [], order: [],
    focused: false,
  };
  const pruned = pruneViewState(s, new Set<string>());   // no live cards at all
  assert.deepEqual(pruned.threads, ["sid-quiet"]);
  assert.deepEqual(pruned.sec, {}, "…while the card state is still pruned as before");
});

test("a stored blob from before threads existed reads as nothing folded, not as corrupt", () => {
  const old = JSON.stringify({ v: 1, sec: { a: "bg" }, tree: [], nodes: [], logs: [], asks: ["a"] });
  const s = parseViewState(old);
  assert.deepEqual(s.threads, []);
  assert.deepEqual(s.sec, { a: "bg" }, "the sections the user had open survive the upgrade");
});

test("a folded thread round-trips through serialize/parse", () => {
  const s = { ...sample(), threads: ["sid-1", "sid-2"] };
  assert.deepEqual(parseViewState(serializeViewState(s)).threads, ["sid-1", "sid-2"]);
});

test("a state under the cap is returned untouched", () => {
  const s = sample();
  assert.equal(capViewState(s, VIEW_STATE_CAP), s, "no copying when there is nothing to trim");
});

// ── wiring pins (no jsdom for feed.ts; repo convention) ──────────────────────────────────────────────
test("feed.ts hydrates every disclosure collection on load", () => {
  assert.match(FEED, /function hydrateViewState\(\)/);
  assert.match(FEED, /parseViewState\(localStorage\.getItem\(VIEW_STATE_KEY\)\)/);
  for (const c of ["secChoice.set", "cardTreeExpanded.add", "collapsedNodes.add", "nodeLogOpen.add",
                   "expandedAsks.add", "collapsedThreads.add"]) {
    assert.ok(FEED.includes(c), `hydrate restores ${c}`);
  }
});

test("it persists at the END of render, gated on the value actually changing", () => {
  // saving from render (not from each toggle handler) cannot miss a mutation site; the change-gate keeps a
  // per-kernel-push render from writing localStorage every time
  assert.match(FEED, /persistViewState\(\);\s+\/\/ whatever the user opened survives/);
  assert.match(FEED, /if \(json === lastViewWrite\) return;/);
  assert.match(FEED, /localStorage\.setItem\(VIEW_STATE_KEY, json\)/);
});

test("in-flight optimistic state and DOM caches are NOT persisted", () => {
  // restoring these would resurrect predictions made against a kernel that no longer exists
  const st = FEED.slice(FEED.indexOf("function currentViewState()"), FEED.indexOf("let lastViewWrite"));
  for (const bad of ["pendingCleared", "pendingMoveAck", "pendingDone", "pendingRestored", "askEls", "groupEls"]) {
    assert.ok(!st.includes(bad), `${bad} must not be persisted`);
  }
});

test("the self-clean prunes against the UNFILTERED payload, on the payload event", () => {
  // `#only=` hides cards without ending them — pruning against the filtered list would discard the hidden
  // cards' sections. So it must key on incomingAsks, before the `only` filter is applied.
  assert.match(FEED, /pruneViewStateTo\(new Set\(incomingAsks\.map\(\(a\) => a\.itemId\)\)\)/);
  const i = FEED.indexOf("pruneViewStateTo(");
  const j = FEED.indexOf("const only = onlyTag();");
  assert.ok(i > 0 && j > i, "the prune runs BEFORE the view filter is computed");
});

test("blocked/quota-limited storage never breaks the feed", () => {
  // private-browsing mode throws on both read and write; the feed must run without persistence, not die
  assert.match(FEED, /try \{ st = parseViewState\(localStorage\.getItem\(VIEW_STATE_KEY\)\); \} catch \{ return; \}/);
  assert.match(FEED, /try \{ localStorage\.setItem\(VIEW_STATE_KEY, json\); \} catch \{[^}]*\}/);
});

test("the storage key is namespaced alongside the feed's existing settings key", () => {
  assert.equal(VIEW_STATE_KEY, "romp:feedview");
  assert.ok(VIEW_STATE_KEY.startsWith("romp:"));
});

test("stacked-column state persists, tolerates old blobs, and gates on the three known keys", () => {
  // the user 2026-08-16: fold + drag order are LAYOUT state — prune-exempt like threads, and a
  // pre-upgrade blob (no cols/order) reads as nothing-folded, default order — never as corrupt
  const old = parseViewState(JSON.stringify({ v: 1, sec: {}, tree: [], nodes: [], logs: [], asks: [], threads: [] }));
  assert.deepEqual(old.cols, []);
  assert.deepEqual(old.order, []);
  const junk = parseViewState(JSON.stringify({ v: 1, sec: {}, tree: [], nodes: [], logs: [], asks: [],
                                               threads: [], cols: ["asks", "evil", 5], order: ["completed", "x"] }));
  assert.deepEqual(junk.cols, ["asks"], "unknown keys are dropped at the parse gate");
  assert.deepEqual(junk.order, ["completed"]);
  const pruned = pruneViewState(sample(), new Set<string>([]));
  assert.deepEqual(pruned.cols, ["completed"], "layout state survives a full card prune");
  assert.deepEqual(pruned.order, ["asks", "completed", "needsInput"]);
});

// ── T347 (the user 2026-09-11, who wanted the focused session's cards on top): the `focused` switch ────────
test("executed: the focused-session switch is OFF by default and round-trips both ways", () => {
  assert.equal(emptyViewState().focused, false, "off for everyone until the VIEW menu row is flipped");
  const on = { ...sample(), focused: true };
  assert.equal(parseViewState(serializeViewState(on)).focused, true);
  assert.deepEqual(parseViewState(serializeViewState(on)), on, "…and the rest of the state rides along");
  const off = { ...sample(), focused: false };
  assert.equal(parseViewState(serializeViewState(off)).focused, false);
  assert.ok(serializeViewState(on).includes('"focused":true'), "the key is written, not implied");
});

test("executed: a blob saved before `focused` existed reads OFF and keeps everything else (v stays 1)", () => {
  // the same shape as the `threads` and `cols`/`order` upgrades: a new key, no version bump, so yesterday's
  // saved sections survive rather than the whole blob reading as corrupt
  const old = JSON.stringify({ v: 1, sec: { a: "bg" }, tree: ["a:n1"], nodes: [], logs: [], asks: ["a"],
                               threads: ["sid-1"], cols: ["completed"], order: [] });
  const s = parseViewState(old);
  assert.equal(s.focused, false);
  assert.deepEqual(s.sec, { a: "bg" }, "the sections the user had open survive the upgrade");
  assert.deepEqual(s.threads, ["sid-1"]);
  assert.deepEqual(s.cols, ["completed"]);
  assert.equal(s.v, 1);
});

test("executed: only the literal true switches `focused` on; a wrong-typed value reads OFF", () => {
  const base = { v: 1, sec: {}, tree: [], nodes: [], logs: [], asks: [], threads: [], cols: [], order: [] };
  for (const junk of ["yes", "true", 1, 0, null, {}, [], "false"]) {
    assert.equal(parseViewState(JSON.stringify({ ...base, focused: junk })).focused, false,
      `focused=${JSON.stringify(junk)} is not the literal true`);
  }
  assert.equal(parseViewState(JSON.stringify({ ...base, focused: true })).focused, true);
  assert.equal(parseViewState(JSON.stringify({ ...base, focused: false })).focused, false);
});

test("executed: prune and cap leave `focused` untouched — it describes the view, not a card", () => {
  // prune-EXEMPT like cols/order: there is no card to prune it against, and a full card prune (no live
  // cards at all) must not flip the switch the user set
  for (const v of [true, false]) {
    const s = { ...sample(), focused: v };
    assert.equal(pruneViewState(s, new Set<string>()).focused, v, `prune keeps focused=${v}`);
    assert.equal(pruneViewState(s, new Set(["card-a"])).focused, v);
    assert.equal(capViewState(s, VIEW_STATE_CAP).focused, v, `an under-cap state keeps focused=${v}`);
    // an OVER-cap trim copies the state field by field; the switch must ride through the copy
    const big = { ...s, logs: Array.from({ length: 30 }, (_, i) => `card-a:l${i}`) };
    const capped = capViewState(big, 10);
    assert.equal(viewStateSize(capped), 10, "the trim happened");
    assert.equal(capped.focused, v, `a trimmed state keeps focused=${v}`);
  }
  // …and it is not an entry: the size the cap measures does not count it
  assert.equal(viewStateSize({ ...sample(), focused: true }), viewStateSize({ ...sample(), focused: false }));
});

// ── T263c (the user 2026-09-08): the fold key is (session, column) ─────────────────────────────────────────
test("executed: threadKey names one session's run in one column; a stored bare sid reads as every column", () => {
  const k = threadKey("sid-1", "needsInput");
  assert.notEqual(k, "sid-1", "a (session, column) key is not the bare sid");
  assert.ok(k.startsWith("sid-1"), "…but it carries the sid");
  assert.notEqual(threadKey("sid-1", "asks"), threadKey("sid-1", "completed"), "one key per column");
  assert.deepEqual(threadKeys(k), [k], "a keyed entry stands for itself");
  // a fold saved before T263c covered the session everywhere: it keeps doing so until a column is opened
  assert.deepEqual(threadKeys("sid-1"), FEED_COLUMNS.map((c) => threadKey("sid-1", c)));
  assert.deepEqual(FEED_COLUMNS, ["asks", "needsInput", "completed"]);
  // a remote sid spelled host:uuid is still one segment
  assert.deepEqual(threadKeys("TESTHOST:sid-2"), FEED_COLUMNS.map((c) => threadKey("TESTHOST:sid-2", c)));
  // the composite key round-trips through the persisted blob untouched
  const st = { ...sample(), threads: [k, threadKey("sid-3", "asks")] };
  assert.deepEqual(parseViewState(serializeViewState(st)).threads, [k, threadKey("sid-3", "asks")]);
  assert.deepEqual(pruneViewState(st, new Set()).threads, [k, threadKey("sid-3", "asks")], "prune-exempt like before");
});
