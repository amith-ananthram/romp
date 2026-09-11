// The merged tab strip CARRIES the local kernel's `skeleton` set (the user 2026-09-07, stage 2 of the
// dashboard-return work).
//
// After a reconnect the local kernel sends the tab strip with `skeleton: [sids]` — the sessions it is NOT
// re-sending in full, which the chat pane must draw as "listed, loads on your click" and never as loaded
// from the stale copy it still holds. But the chat pane's LOCAL frames go through FederationManager.inbound,
// and inbound REBUILDS the tabOrder it hands to the pane from per-host stores: without a store for the key
// it was dropped on the floor, and the pane painted every stale session as loaded (a lie). Executed against
// the real manager with a fake WebSocket (the federation-reconnect harness): the local set rides the merged
// frame, a remote host's push does not erase it, the `closed` fold removes the id, a tabOrder WITHOUT the key
// keeps the set pruned to the new order (the kernel omits the key only when the set is empty — but the
// shim's FIFO can replace a queued strip, so absent means "no news", never "none"), and the key is present
// on EVERY merged frame (an array, possibly empty) so the pane's "array → replace" rule sees the union as
// the authority. Remote kernels never see a reconnect signal and never emit the key (spec §1.8) — the
// prefix path still covers them so a future one would arrive host-prefixed like every other id.
// Synthetic only (hosts TESTHOST/gpu1, placeholder ids).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { FederationManager, prefixInbound, routeOutbound, BOOKKEEPING } from "./federation";

const U = "11111111-2222-3333-4444-555555555555";
const V = "99999999-8888-7777-6666-555555555555";

// ── the fake WebSocket harness (federation-reconnect.test.ts), plus a wire recorder for the outbound test ──
class FakeWS {
  static made: FakeWS[] = [];
  readyState = 0;
  closed = 0;
  sent: any[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: any) => void) | null = null;
  onclose: ((ev: any) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeWS.made.push(this); }
  open(): void { this.readyState = 1; this.onopen && this.onopen(); }
  frame(data: any): void { this.onmessage && this.onmessage({ data: JSON.stringify(data) }); }
  send(s: string): void { this.sent.push(JSON.parse(s)); }
  close(): void { this.closed++; this.readyState = 3; }
}

let clock = 1_000_000_000;
function withManager(fn: (fm: any, emitted: any[], localSent: any[]) => void): void {
  const emitted: any[] = [], localSent: any[] = [];
  const store = new Map<string, string>();
  const g: any = globalThis;
  const saved: Record<string, any> = {};
  const set = (k: string, v: any) => { saved[k] = { had: k in g, v: g[k] }; g[k] = v; };
  FakeWS.made = [];
  const realNow = Date.now;
  Date.now = () => clock;
  set("WebSocket", FakeWS);
  set("location", { protocol: "http:", host: "TESTHOST.local:1" });
  // a real (in-memory) view-order store: absorbHostReport writes the arrangement on every host report
  set("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } });
  set("window", {
    dispatchEvent: (ev: any) => { if (ev && ev.data) emitted.push(ev.data); },
    __rompLocalSend: (m: any) => { if (m && m.type !== "clientDiag") localSent.push(m); },
    sessionStorage: { getItem: () => "" },
    parent: { postMessage: () => {} },
  });
  try {
    fn(new FederationManager(), emitted, localSent);
  } finally {
    Date.now = realNow;
    for (const [k, r] of Object.entries(saved)) { if (r.had) g[k] = r.v; else delete g[k]; }
  }
}
const orders = (emitted: any[]) => emitted.filter((m) => m && m.type === "tabOrder");
const last = (emitted: any[]) => orders(emitted).pop()!;
const tabs = (...ids: string[]) => ids.map((id) => ({ id, name: "s-" + id }));
/** Attach a remote host and open its relay socket, so its frames take the REAL path (socket → inbound). */
function attach(fm: any, host: string): FakeWS {
  fm.openRemote(host, true);   // the page holds no remote token since 2026-09-08 (the relay adds the credential)
  const ws = FakeWS.made[FakeWS.made.length - 1];
  assert.match(ws.url, new RegExp("/remote/" + host + "/ws\\?app=chat"), "the dial goes through the local kernel's relay");
  assert.doesNotMatch(ws.url, /token=/, "…which adds the remote's credential itself; the page never carries one (2026-09-08)");
  ws.open();
  return ws;
}

// ── the pure prefix ───────────────────────────────────────────────────────────────────────────────
test("prefixInbound: a tabOrder's skeleton[] is host-prefixed like order[] — identity for the local host", () => {
  const remote = prefixInbound("gpu1", { type: "tabOrder", order: [U, V], tabs: tabs(U, V), skeleton: [V] });
  assert.deepEqual(remote.skeleton, ["gpu1:" + V], "a remote skeleton id arrives addressed to its host");
  assert.deepEqual(remote.order, ["gpu1:" + U, "gpu1:" + V]);
  const local = prefixInbound("", { type: "tabOrder", order: [U, V], tabs: tabs(U, V), skeleton: [V] });
  assert.deepEqual(local.skeleton, [V], "the local host is the identity transform");
});

// ── the manager: what the pane is handed ─────────────────────────────────────────────────────────
test("a local tabOrder with skeleton [B] → the merged frame carries [B], fresh from the local host", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A", "B"], tabs: tabs("A", "B"), skeleton: ["B"] });
    const o = last(emitted);
    assert.deepEqual(o.order, ["A", "B"]);
    assert.deepEqual(o.skeleton, ["B"], "the rebuilt frame carries the set the kernel sent — it was dropped before");
    assert.equal(o.freshHost, "", "…on the local host's own fresh push");
  });
});

test("a remote host's tabOrder WITHOUT the key does not erase the local set — the merged frame still says [B]", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A", "B"], tabs: tabs("A", "B"), skeleton: ["B"] });
    const ws = attach(fm, "TESTHOST");
    ws.frame({ type: "tabOrder", order: [U], tabs: tabs(U) });          // a remote kernel never emits the key (§1.8)
    const o = last(emitted);
    assert.equal(o.freshHost, "TESTHOST", "the remote push drove this emission");
    assert.deepEqual(o.order, ["A", "B", "TESTHOST:" + U]);
    assert.deepEqual(o.skeleton, ["B"], "the local slice's set rides along from the store — another host's push is no news about it");
    fm.conns.get("TESTHOST").closed = true;
  });
});

test("a local `closed B` folds B out of the set — the confirming re-emit and every later one carry []", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A", "B"], tabs: tabs("A", "B"), skeleton: ["B"] });
    const n = emitted.length;
    fm.inbound("", { type: "closed", id: "B" });
    const after = emitted.slice(n);
    assert.equal(after[0].type, "closed", "the pane's teardown frame first, as today");
    assert.equal(after[1].type, "tabOrder");
    assert.deepEqual(after[1].order, ["A"]);
    assert.deepEqual(after[1].skeleton, [], "the dead id left the set with the order — an ARRAY, so the pane replaces its set");
    assert.equal(after[1].reemit, true);
    fm.emitMergedOrder();                                                 // a view-order storage event / attach / drop
    assert.deepEqual(last(emitted).skeleton, [], "…and no synthetic re-emit can resurrect it");
    assert.deepEqual(fm.perHostSkeleton[""], [], "the store itself was folded, not just this frame");
  });
});

test("a remote host's skeleton [x] arrives host-prefixed through the real socket path: [gpu1:x]", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A"], tabs: tabs("A") });
    const ws = attach(fm, "gpu1");
    ws.frame({ type: "tabOrder", order: [U, V], tabs: tabs(U, V), skeleton: [V] });
    const o = last(emitted);
    assert.deepEqual(o.order, ["A", "gpu1:" + U, "gpu1:" + V]);
    assert.deepEqual(o.skeleton, ["gpu1:" + V], "prefixed on the way in, so the pane's set matches the ids it renders");
    // the union, in hostSeq order: local first, then attach order
    fm.inbound("", { type: "tabOrder", order: ["A", "B"], tabs: tabs("A", "B"), skeleton: ["B"] });
    assert.deepEqual(last(emitted).skeleton, ["B", "gpu1:" + V]);
    fm.conns.get("gpu1").closed = true;
  });
});

test("a local tabOrder WITHOUT the key after one with it KEEPS the set, pruned to the new order (absent = no news, never none)", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A", "B", "C"], tabs: tabs("A", "B", "C"), skeleton: ["B", "C"] });
    assert.deepEqual(last(emitted).skeleton, ["B", "C"]);
    // a later strip without the key (e.g. a close confirmation built before the kernel's set existed, or a
    // frame from a sender the set does not reach): B is gone from the ORDER, so B leaves the set; C stays
    fm.inbound("", { type: "tabOrder", order: ["A", "C"], tabs: tabs("A", "C") });
    const o = last(emitted);
    assert.deepEqual(o.order, ["A", "C"]);
    assert.deepEqual(o.skeleton, ["C"], "kept and pruned — NOT emptied, which would paint C's stale copy as loaded");
    // the same strip again, still without the key: stable
    fm.inbound("", { type: "tabOrder", order: ["A", "C"], tabs: tabs("A", "C") });
    assert.deepEqual(last(emitted).skeleton, ["C"]);
  });
});

test("a second reconnect's tabOrder with skeleton [A] REPLACES the set (array → replace), whatever was held", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A", "B", "C"], tabs: tabs("A", "B", "C"), skeleton: ["B", "C"] });
    fm.inbound("", { type: "tabOrder", order: ["A", "B", "C"], tabs: tabs("A", "B", "C"), skeleton: ["A"] });
    assert.deepEqual(last(emitted).skeleton, ["A"], "the kernel's array is the authority — replaced, not unioned");
    fm.inbound("", { type: "tabOrder", order: ["A", "B", "C"], tabs: tabs("A", "B", "C"), skeleton: [] });
    assert.deepEqual(last(emitted).skeleton, [], "an explicit empty array empties it");
  });
});

test("EVERY merged tabOrder carries `skeleton` as an array — a first strip without the key, a synthetic re-emit, a host drop", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A"], tabs: tabs("A") });   // today's non-reconnect strip: no key
    let o = last(emitted);
    assert.ok(Array.isArray(o.skeleton), "present as an array even when the kernel sent no key");
    assert.deepEqual(o.skeleton, []);
    fm.emitMergedOrder();                                                    // storage / attach / drop path
    o = last(emitted);
    assert.equal(o.reemit, true);
    assert.deepEqual(o.skeleton, [], "…on a synthetic re-emit too");
    const ws = attach(fm, "TESTHOST");
    ws.frame({ type: "tabOrder", order: [U], tabs: tabs(U), skeleton: [U] });
    assert.deepEqual(last(emitted).skeleton, ["TESTHOST:" + U]);
    fm.closeRemote("TESTHOST");                                              // detach: its slice leaves every store
    o = last(emitted);
    assert.deepEqual(o.order, ["A"]);
    assert.deepEqual(o.skeleton, [], "a dropped host's ids leave the set with its order");
    assert.equal("TESTHOST" in fm.perHostSkeleton, false, "…and its store entry is gone — a re-attach starts clean");
    for (const m of orders(emitted)) assert.ok(Array.isArray(m.skeleton), "no merged frame ever went out without the key");
  });
});

test("a `closed` for a host whose set never held the id, or for an id in another host's set, touches nothing else", () => {
  withManager((fm, emitted) => {
    fm.inbound("", { type: "tabOrder", order: ["A", "B"], tabs: tabs("A", "B"), skeleton: ["B"] });
    const ws = attach(fm, "TESTHOST");
    ws.frame({ type: "tabOrder", order: [U], tabs: tabs(U) });
    ws.frame({ type: "closed", id: U });                                    // the remote's own report, no set on that host
    assert.deepEqual(last(emitted).skeleton, ["B"], "the local set is untouched by another host's close");
    fm.inbound("", { type: "closed", id: "A" });                             // a local close of a NON-skeleton id
    assert.deepEqual(last(emitted).order, ["B"]);
    assert.deepEqual(last(emitted).skeleton, ["B"], "…and a close of a loaded tab leaves the set alone");
    fm.conns.get("TESTHOST").closed = true;
  });
});

// ── outbound: the pane's needFull rides with its diagnostic `why` ─────────────────────────────────
test("routeOutbound: needFull's optional `why` passes through untouched — only the id is host-routed and stripped", () => {
  const local = routeOutbound({ type: "needFull", id: "B", why: "skeleton-click" }, new Set(["gpu1"]));
  assert.deepEqual(local, [{ host: "", msg: { type: "needFull", id: "B", why: "skeleton-click" } }], "a local id: the frame is untouched");
  const remote = routeOutbound({ type: "needFull", id: "gpu1:" + V, why: "prefetch" }, new Set(["gpu1"]));
  assert.deepEqual(remote, [{ host: "gpu1", msg: { type: "needFull", id: V, why: "prefetch" } }], "a remote id: stripped for its kernel, `why` intact");
  const bare = routeOutbound({ type: "needFull", id: "B" }, new Set(["gpu1"]));
  assert.deepEqual(bare, [{ host: "", msg: { type: "needFull", id: "B" } }], "no `why` → no `why` minted");
});

test("routeOutbound: a re-attach's resident keys go to the owning kernel by the id's host, the id bared, the keys intact (T323 follow-up)", () => {
  const keys = ["k1", "k2#2"];
  assert.deepEqual(routeOutbound({ type: "reattachKeys", id: "B", keys }, new Set(["gpu1"])), [{ host: "", msg: { type: "reattachKeys", id: "B", keys } }]);
  assert.deepEqual(routeOutbound({ type: "reattachKeys", id: "gpu1:" + V, keys }, new Set(["gpu1"])), [{ host: "gpu1", msg: { type: "reattachKeys", id: V, keys } }]);
  assert.ok(BOOKKEEPING.has("reattachKeys"), "held for the socket's open like the other proto-2 asks, never toasted");
  assert.equal(BOOKKEEPING.get("reattachKeys")!({ id: "B" }), String(BOOKKEEPING.get("loadNewer")!({ id: "B" })).replace("loadNewer", "reattachKeys"));
});

test("the manager's outbound puts needFull(+why) on the owning kernel's wire — local send or remote socket", () => {
  withManager((fm, _e, localSent) => {
    const ws = attach(fm, "gpu1");
    fm.outbound({ type: "needFull", id: "B", why: "gap" });
    assert.deepEqual(localSent, [{ type: "needFull", id: "B", why: "gap" }]);
    fm.outbound({ type: "needFull", id: "gpu1:" + U, why: "skeleton-delta" });
    assert.deepEqual(ws.sent, [{ type: "needFull", id: U, why: "skeleton-delta" }], "the remote kernel sees its own bare id and the same `why`");
    fm.conns.get("gpu1").closed = true;
  });
});

// ── the page's chat protocol reaches every remote kernel (T323 stage 4b, round 2 item 16) ──────────────
test("the page's ready (proto 2) goes to every OPEN remote socket at once, and to a later socket on its open, after the flush", () => {
  withManager((fm, _e, localSent) => {
    const a = attach(fm, "gpu1");
    fm.outbound({ type: "ready", proto: 2 });
    assert.deepEqual(localSent.filter((m: any) => m.type === "ready"), [{ type: "ready", proto: 2 }], "the local kernel gets the page's own ready");
    assert.deepEqual(a.sent, [{ type: "ready", proto: 2 }], "an already-open remote socket is told the protocol now");
    fm.openRemote("gpu2", true);
    const b = FakeWS.made[FakeWS.made.length - 1];
    assert.deepEqual(b.sent, [], "nothing rides a socket that has not opened");
    fm.outbound({ type: "setting", key: "k", value: 1, host: "gpu2" });   // parked for the socket: the flush sends it first
    b.open();
    const kinds = b.sent.map((m: any) => m.type);
    assert.equal(kinds[kinds.length - 1], "ready", "the ready is the last frame of the open: after whatever the flush sent");
    assert.deepEqual(b.sent[b.sent.length - 1], { type: "ready", proto: 2 });
    assert.equal(b.sent.filter((m: any) => m.type === "ready").length, 1, "once per open");
    fm.conns.get("gpu1").closed = true; fm.conns.get("gpu2").closed = true;
  });
});

test("an index page's ready (no proto) is told to no remote socket: an older kernel needs nothing and a newer one defaults to index frames", () => {
  withManager((fm) => {
    const a = attach(fm, "gpu1");
    fm.outbound({ type: "ready" });
    assert.deepEqual(a.sent, [], "no protocol declared: nothing sent");
    fm.openRemote("gpu2", true);
    const b = FakeWS.made[FakeWS.made.length - 1]; b.open();
    assert.deepEqual(b.sent.filter((m: any) => m.type === "ready"), [], "…nor on a later open");
    fm.conns.get("gpu1").closed = true; fm.conns.get("gpu2").closed = true;
  });
});

// ── source pins: the mechanism stays where the spec put it ───────────────────────────────────────
test("source pins: skeleton is an ARRAY_ID field; emitMergedOrder attaches it unconditionally from the per-host store", () => {
  const src = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
  assert.match(src, /const ARRAY_ID = \[[^\]]*"skeleton"[^\]]*\];/, "prefixInbound host-prefixes skeleton[] through the generic array pass");
  const emit = src.slice(src.indexOf("  private emitMergedOrder("), src.indexOf("  private absorbHostReport("));
  assert.ok(emit.length > 0, "emitMergedOrder precedes absorbHostReport");
  assert.match(emit, /\n    data\.skeleton = this\.hostSeq\.flatMap\(\(h\) => this\.perHostSkeleton\[h\] \|\| \[\]\);/,
    "the union over hostSeq, ALWAYS attached (an unguarded statement at block level), so the pane's array→replace rule sees the authority");
  assert.ok(!/if \([^)]*skeleton[^)]*\)/.test(emit), "no condition gates the key — an absent key would be read as 'no news' by the pane");
});
