// Skeleton tabs — the client's copy of the set the kernel withholds after a redial (2026-09-07). The pure state
// machine in skeleton-tabs.ts is run here directly (the prebuild.ts / tab-meta.ts pattern); how render.ts wires
// it is pinned at the source in skeleton-tabs-wiring.test.ts. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { newSkeletonState, applyTabOrderSkeleton, onStatus, onFull, onDismiss, onSocketUp, nextPrefetch,
         renderKind, type SkeletonState } from "./skeleton-tabs";

const A = "11111111-2222-3333-4444-aaaaaaaaaaaa";
const B = "11111111-2222-3333-4444-bbbbbbbbbbbb";
const C = "11111111-2222-3333-4444-cccccccccccc";
const D = "11111111-2222-3333-4444-dddddddddddd";
const all = () => true;
const none = new Set<string>();

const held = (st: SkeletonState) => [...st.ids].sort();

test("the spec's story: a reconnect strip lists [B, C]; a stale session never makes a skeleton read as loaded", () => {
  const st = newSkeletonState();
  assert.equal(applyTabOrderSkeleton(st, [B, C], [A, B, C]), true, "a set landed → changed");
  assert.deepEqual(st.order, [B, C], "the kernel's order is kept verbatim — it is the prefetch order");
  // the page HELD B before the outage (hasSession = true) — that stale entry must not paint it as loaded
  assert.equal(renderKind(st, B, true), "skeleton");
  assert.equal(renderKind(st, A, true), "loaded", "the active tab, sent in full, is a plain loaded tab");
  assert.equal(renderKind(st, D, false), "placeholder", "an unknown, session-less id is still the building placeholder");
  // status frames: stored for a skeleton (the chip reads ONLY these), passed through for anyone else
  assert.equal(onStatus(st, B, { state: "working", sinceEpoch: 1 }), "skeleton");
  assert.deepEqual(st.status.get(B), { state: "working", sinceEpoch: 1 });
  assert.equal(onStatus(st, A, { state: "idle" }), "not-skeleton");
  assert.equal(st.status.has(A), false, "a non-skeleton status is the caller's business");
  // the user clicks B → render.ts asks (pinned in the wiring test) and B sits in awaitingFull: the prefetch
  // holds while ANY skeleton is in flight, even one the user asked for (one at a time)
  assert.equal(nextPrefetch(st, B, new Set([B]), false, all), null, "one in flight → nothing else is fetched");
  // B's full lands
  assert.equal(onFull(st, B), true, "it WAS held → the caller re-shows the view instead of appending");
  assert.deepEqual(held(st), [C]);
  assert.equal(st.status.has(B), false, "its stored status goes with it");
  assert.deepEqual(st.order, [C]);
  assert.equal(onFull(st, A), false, "a full for a loaded tab is not a skeleton release");
  // a later strip WITHOUT the key (an older kernel; the set emptied so the key was dropped): keep, pruned
  assert.equal(applyTabOrderSkeleton(st, undefined, [A, C]), false, "C kept, nothing changed");
  assert.deepEqual(held(st), [C]);
  assert.equal(applyTabOrderSkeleton(st, undefined, [A]), true, "C left the strip → pruned");
  assert.deepEqual(held(st), []);
});

test("absent key = keep (pruned to the strip); an array = replace — the kernel's list is authoritative", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [B, C, D], [A, B, C, D]);
  assert.equal(applyTabOrderSkeleton(st, undefined, [A, C]), true);
  assert.deepEqual(held(st), [C], "B and D are no longer on the strip → pruned; C kept (absent = keep)");
  assert.deepEqual(st.order, [C]);
  // a second reconnect while C is outstanding: the new socket's strip names [A] — A had loaded, but it is
  // stale after the second outage, and C (not listed) is what the kernel now sends in full
  onSocketUp(st);
  assert.equal(applyTabOrderSkeleton(st, [A], [A, C]), true);
  assert.deepEqual(held(st), [A], "replaced, not merged");
  assert.deepEqual(st.order, [A]);
});

test("an array is cleaned: strings only, deduped; status entries for ids that left are dropped", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [B, C], [A, B, C]);
  onStatus(st, B, { state: "idle" });
  onStatus(st, C, { state: "idle" });
  assert.equal(applyTabOrderSkeleton(st, [C, 7, null, C, ""], [A, B, C]), true);
  assert.deepEqual(held(st), [C]);
  assert.deepEqual(st.order, [C]);
  assert.equal(st.status.has(B), false, "B left the set → its status is gone");
  assert.equal(st.status.has(C), true);
  assert.equal(applyTabOrderSkeleton(st, [C], [A, B, C]), false, "the same list again → unchanged (no repaint)");
  assert.equal(applyTabOrderSkeleton(st, [], [A, B, C]), true, "an empty array empties the set");
  assert.deepEqual(held(st), []);
});

test("a list that names a tab whose full already arrived on THIS socket is a stale relay, never re-adopted", () => {
  // The kernel releases a sid as it sends its full and enqueues both under one lock, so a strip naming X can only
  // be ahead of X's full — never behind it. The federation merge re-attaches its per-host copy of the list to
  // every merged strip and is never told the last id loaded (the kernel's key just disappears): without this
  // rule the last skeleton re-skeletoned on every push, asked, loaded, and re-skeletoned again — a flap.
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [C], [A, C]);
  assert.equal(onFull(st, C), true);
  assert.equal(applyTabOrderSkeleton(st, [C], [A, C]), false, "the stale relay of [C] is ignored");
  assert.deepEqual(held(st), []);
  // a NEW socket is new information about every tab: the same list is adopted after the socket reopens
  onSocketUp(st);
  assert.equal(applyTabOrderSkeleton(st, [C], [A, C]), true);
  assert.deepEqual(held(st), [C]);
});

test("nextPrefetch: null when hidden, null while one is in flight, else the first held id in order that is in view and not active", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [C, B, D], [A, B, C, D]);   // the kernel's order: ascending transcript size
  assert.equal(nextPrefetch(st, A, none, true, all), null, "hidden → nothing (bytes nobody sees)");
  assert.equal(nextPrefetch(st, A, new Set([D]), false, all), null, "a skeleton in flight → wait for its upsert");
  assert.equal(nextPrefetch(st, A, new Set([A]), false, all), C, "a non-skeleton in flight does not hold the chain");
  assert.equal(nextPrefetch(st, A, none, false, all), C, "the first of the kernel's order");
  assert.equal(nextPrefetch(st, C, none, false, all), B, "…skipping the active tab (its own click path asks)");
  assert.equal(nextPrefetch(st, A, none, false, (id) => id !== C), B, "…and a view-hidden tab (it loads on click)");
  onFull(st, C); onFull(st, B);
  assert.equal(nextPrefetch(st, A, none, false, all), D, "the chain walks the list one upsert at a time");
  onFull(st, D);
  assert.equal(nextPrefetch(st, A, none, false, all), null, "nothing left");
  assert.equal(nextPrefetch(newSkeletonState(), A, none, false, all), null, "a fresh page has no set");
});

test("onDismiss: a tab that left the strip has nothing left to load", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, [C], [A, C]);
  onStatus(st, C, { state: "working" });
  onDismiss(st, C);
  assert.deepEqual(held(st), []);
  assert.deepEqual(st.order, []);
  assert.equal(st.status.size, 0);
  onDismiss(st, C);   // idempotent
  assert.deepEqual(held(st), []);
  assert.equal(nextPrefetch(st, A, none, false, all), null);
});

test("renderKind with a stale session present: skeleton wins; without the set the old two states are unchanged", () => {
  const st = newSkeletonState();
  assert.equal(renderKind(st, B, true), "loaded");
  assert.equal(renderKind(st, B, false), "placeholder");
  applyTabOrderSkeleton(st, [B], [A, B]);
  assert.equal(renderKind(st, B, true), "skeleton", "the stale pre-outage entry is kept for the append, never painted");
  assert.equal(renderKind(st, B, false), "skeleton", "a page that opened a socket and received nothing: still a skeleton, loads on click/idle");
});

test("an array never adopts an id the strip does not list: a session that ended before it loaded is gone", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, ["B", "Z"], ["A", "B"]);
  assert.deepEqual(st.order, ["B"], "Z is not on the strip → not a skeleton either (the prefetch must never ask for it)");
  assert.equal(st.ids.has("Z"), false);
});

test("a dismissed tab leaves the loaded set, so its re-listing is a skeleton again and the pane asks for its frame (T357)", () => {
  const st = newSkeletonState();
  applyTabOrderSkeleton(st, ["A"], ["A", "B"]);
  onFull(st, "A");                                    // its frame landed on this socket
  assert.ok(st.loaded.has("A")); assert.ok(!st.ids.has("A"));
  applyTabOrderSkeleton(st, ["A"], ["A", "B"]);       // re-listed as a skeleton while loaded: refused, the page has it
  assert.ok(!st.ids.has("A"), "a loaded id is never re-skeletoned on the same socket");
  onDismiss(st, "A");                                 // its tab left the strip (a host drop, an omission): the view went with it
  assert.ok(!st.loaded.has("A"), "no longer loaded here");
  applyTabOrderSkeleton(st, ["A"], ["A", "B"]);       // the host re-attached: the strip names it a skeleton again
  assert.ok(st.ids.has("A"), "…and now it is one, so the pane asks for its frame");
});
