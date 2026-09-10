// Behavioral tests for the chat tab ordering model (ui/webview/tab-order.ts). Unlike the source-text regex
// pins elsewhere in this suite, these EXECUTE the logic across realistic push sequences and assert the one
// property the user cares about: the order is stable — it changes ONLY on a drag, a new session (append), or
// a close (remove), NEVER on a status/activity update (the user 2026-06-27). This is the layer the kernel's
// own order tests never reached, which is why the jumping survived every "fix".
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { reconcileTabOrder, retainLiveOmitted, adoptArrival } from "./tab-order";

// A tiny stand-in for the client's tab state: the order array + the set of ids whose session is known. The
// real render.ts drives the SAME three ops (append on a session push, remove on close, reconcile on a kernel
// tabOrder push) plus a drag that sets the array directly.
function model() {
  let order: string[] = [];
  const knownIds = new Set<string>();
  return {
    onSession(id: string) { knownIds.add(id); if (!order.includes(id)) order.push(id); },   // a session push
    onClose(id: string) { knownIds.delete(id); order = order.filter((x) => x !== id); },     // a tab close
    onKernelOrder(kernel: string[]) { order = reconcileTabOrder(kernel, order, (id) => knownIds.has(id)); },
    onDrag(newOrder: string[]) { order = newOrder.slice(); },                                 // user drag
    list() { return order.slice(); },
  };
}

test("a kernel push is adopted verbatim", () => {
  assert.deepEqual(reconcileTabOrder(["A", "B", "C"], [], () => true), ["A", "B", "C"]);
});

test("a session that arrived before its tabOrder push is kept (appended), then reconciled in place", () => {
  const m = model();
  m.onSession("A");                       // session push beats the tabOrder push
  assert.deepEqual(m.list(), ["A"]);
  m.onKernelOrder([]);                    // kernel hasn't caught up yet → A stays (don't vanish)
  assert.deepEqual(m.list(), ["A"]);
  m.onKernelOrder(["A"]);                 // kernel catches up → no duplicate, correct slot
  assert.deepEqual(m.list(), ["A"]);
});

test("a status/activity update never reorders (it simply doesn't touch the order)", () => {
  const m = model();
  m.onSession("A"); m.onSession("B"); m.onSession("C");
  m.onKernelOrder(["A", "B", "C"]);
  const before = m.list();
  // ...B works hard, C goes idle, A gets a new message — none of that calls into the order model at all.
  // Re-adopting the SAME (stable) kernel order must be a no-op.
  m.onKernelOrder(["A", "B", "C"]);
  assert.deepEqual(m.list(), before, "order is unchanged by activity");
  assert.deepEqual(m.list(), ["A", "B", "C"]);
});

test("a new session appends at the end, never jumps to the top", () => {
  const m = model();
  m.onKernelOrder(["A", "B"]);
  m.onSession("C");                       // brand-new session arrives
  assert.deepEqual(m.list(), ["A", "B", "C"]);
  m.onKernelOrder(["A", "B", "C"]);       // kernel agrees (it appends newcomers too)
  assert.deepEqual(m.list(), ["A", "B", "C"]);
});

test("a drag sticks and does NOT snap back on the next poll (kernel echoes the persisted order)", () => {
  const m = model();
  m.onSession("A"); m.onSession("B"); m.onSession("C");
  m.onKernelOrder(["A", "B", "C"]);
  m.onDrag(["C", "A", "B"]);              // user drags C to the front
  assert.deepEqual(m.list(), ["C", "A", "B"]);
  m.onKernelOrder(["C", "A", "B"]);       // kernel persisted + echoes it back → no snap-back
  assert.deepEqual(m.list(), ["C", "A", "B"]);
  m.onKernelOrder(["C", "A", "B"]);       // a later poll → still stable
  assert.deepEqual(m.list(), ["C", "A", "B"]);
});

test("closing a tab drops it and keeps the rest in order", () => {
  const m = model();
  m.onSession("A"); m.onSession("B"); m.onSession("C");
  m.onKernelOrder(["A", "B", "C"]);
  m.onClose("B");
  m.onKernelOrder(["A", "C"]);            // kernel no longer lists B
  assert.deepEqual(m.list(), ["A", "C"]);
});

test("a stale id (not in the kernel order and not locally known) is dropped", () => {
  // GHOST is in the prior local order but its session is gone and the kernel doesn't list it.
  assert.deepEqual(reconcileTabOrder(["A", "B"], ["A", "GHOST", "B"], (id) => id === "A" || id === "B"),
    ["A", "B"]);
});

test("output is deduped and string-only", () => {
  assert.deepEqual(reconcileTabOrder(["A", "A", "B"] as string[], ["B", "B"], () => true), ["A", "B"]);
  assert.deepEqual(reconcileTabOrder(["A", 7 as any, "B"], [], () => true), ["A", "B"]);
});

// ── kernel-owned tabs drop when the kernel's order stops carrying them (the 2026-08-11 ghost) ──────────
// A session was ended while this client's socket was down, so the one-shot `closed` frame never arrived.
// The dead tab's session is still in the client's maps (known(id) = true — JS state survives reconnects),
// so the unconditional keep resurrected it on every later push: a live-looking tab for an ended session.
// The kernelSeen predicate is the fix: an id ANY kernel push has carried is kernel-owned, and a later push
// omitting it is the removal event — known or not, it drops out.

// The model with kernelSeen tracking, mirroring render.ts's applyTabOrder bookkeeping (add-only set).
function seenModel() {
  let order: string[] = [];
  const knownIds = new Set<string>();
  const kernelListed = new Set<string>();
  return {
    onSession(id: string) { knownIds.add(id); if (!order.includes(id)) order.push(id); },
    onKernelOrder(kernel: string[]) {
      order = reconcileTabOrder(kernel, order, (id) => knownIds.has(id), (id) => kernelListed.has(id));
      for (const id of kernel) kernelListed.add(id);
    },
    list() { return order.slice(); },
  };
}

test("a tab the kernel once listed drops out when a later push omits it, even with its session still cached", () => {
  const m = seenModel();
  m.onSession("A"); m.onSession("B");
  m.onKernelOrder(["A", "B"]);            // both kernel-owned now
  assert.deepEqual(m.list(), ["A", "B"]);
  // B is ended while this client's socket is down: the `closed` frame is lost, B's session stays cached
  // (known), and the next push — the reconnect's resync — no longer carries B.
  m.onKernelOrder(["A"]);
  assert.deepEqual(m.list(), ["A"], "the missed one-shot closed heals on the next push");
  m.onKernelOrder(["A"]);                 // and it stays healed
  assert.deepEqual(m.list(), ["A"]);
});

test("an id the kernel has never listed keeps the just-arrived grace (create placeholder, session-first push)", () => {
  const m = seenModel();
  m.onSession("prov");                    // client-minted: the kernel has never carried it
  m.onKernelOrder(["A"]);                 // pushes that predate its adoption must not drop it
  assert.deepEqual(m.list(), ["A", "prov"]);
  m.onKernelOrder(["A", "prov"]);         // the kernel adopts it → reconciled into place, now kernel-owned
  assert.deepEqual(m.list(), ["A", "prov"]);
  m.onKernelOrder(["A"]);                 // ...so from here an omission drops it like any other tab
  assert.deepEqual(m.list(), ["A"]);
});

test("a revived session (same sid re-listed after a drop) is adopted back cleanly", () => {
  const m = seenModel();
  m.onSession("A"); m.onSession("B");
  m.onKernelOrder(["A", "B"]);
  m.onKernelOrder(["A"]);                 // B ended + dropped
  assert.deepEqual(m.list(), ["A"]);
  m.onKernelOrder(["A", "B"]);            // B revived (dead sessions revive with their history)
  assert.deepEqual(m.list(), ["A", "B"]);
});

// T258 (the user 2026-09-08): a live session the kernel STILL affirms live but whose tabOrder push omits it
// (its transcript file was briefly unreadable) is kept at its slot, never dropped or moved. The kernel-side
// fix keeps it in `order`; this is the pane's second line of defense.
test("retainLiveOmitted keeps a live-but-omitted local id at its slot; drops nothing else; a no-op without a marker", () => {
  // B is live per the marker but missing from the kernel order → spliced back after A (its local predecessor)
  assert.deepEqual(retainLiveOmitted(["A", "C"], ["A", "B", "C"], new Set(["B"])), ["A", "B", "C"]);
  // a genuinely gone id (NOT in live) is not retained — the omission stands
  assert.deepEqual(retainLiveOmitted(["A", "C"], ["A", "B", "C"], new Set()), ["A", "C"]);
  // the kernel order otherwise wins verbatim (a drag/reorder is honored); a retained id sits at its local slot
  assert.deepEqual(retainLiveOmitted(["C", "A"], ["A", "B", "C"], new Set(["B"])), ["C", "A", "B"]);
  // first-position live-omitted id lands at the front (no surviving predecessor)
  assert.deepEqual(retainLiveOmitted(["C"], ["B", "C"], new Set(["B"])), ["B", "C"]);
  // an id already in the kernel order is never duplicated even if the marker also names it
  assert.deepEqual(retainLiveOmitted(["A", "B"], ["A", "B"], new Set(["A", "B"])), ["A", "B"]);
});

// End to end through reconcileTabOrder: a push that omits a live id must not tear the tab down (the property
// render.ts's applyTabOrder relies on — omitted excludes live, and the retained order feeds reconcile).
test("a kernel push omitting a LIVE id keeps its tab; omitting a non-live id removes it", () => {
  const kernelSeen = new Set(["A", "B"]);
  const seen = (id: string) => kernelSeen.has(id);
  // B live but omitted → retained order lists it → reconcile keeps it (kernel-owned, so it would otherwise drop)
  const keptOrder = retainLiveOmitted(["A"], ["A", "B"], new Set(["B"]));
  assert.deepEqual(reconcileTabOrder(keptOrder, ["A", "B"], (id) => id === "A" || id === "B", seen), ["A", "B"]);
  // B NOT live and omitted → not retained → reconcile drops it (the genuine close)
  const goneOrder = retainLiveOmitted(["A"], ["A", "B"], new Set());
  assert.deepEqual(reconcileTabOrder(goneOrder, ["A", "B"], (id) => id === "A" || id === "B", seen), ["A"]);
});

test("a session frame's id joins the order once: not when its tabOrder frame already carried it, not when the session existed, once when it is new", () => {
  const order = ["a", "b", "c"];   // c came in a tabOrder frame (tabs-first) before its session frame
  assert.equal(adoptArrival(order, "c", false), false, "the order already carries it: nothing added");
  assert.deepEqual(order, ["a", "b", "c"], "…and no duplicate, which a drag's commit would have written into the arrangement");
  assert.equal(adoptArrival(order, "d", false), true, "a session the order has never seen appends");
  assert.deepEqual(order, ["a", "b", "c", "d"]);
  assert.equal(adoptArrival(order, "d", false), false, "a second frame for it adds nothing");
  assert.equal(adoptArrival(order, "a", true), false, "an existing session's frame never pushes");
  assert.deepEqual(order, ["a", "b", "c", "d"]);
});
