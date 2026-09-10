// perf-telemetry.ts on a fake clock: the frame classifier, the per-type log2 histogram and its quantiles,
// the minute flush (one row, its shape; an idle minute sends nothing), the free-main-thread sample two
// animation frames after a handler and every way it is cancelled or dropped, nested brackets recording each
// level's own time, the slowframe threshold, cap and long-frame attribution, the long-animation-frame
// aggregation from synthetic entries (with and without `scripts`, the key cap, the longtask fallback), the
// identifier-only contract for attribution keys and invokers, and the feature guards when
// performance.memory, PerformanceObserver, requestAnimationFrame or performance.now itself are absent. The
// collector takes every browser dependency through PerfDeps, so nothing here touches a real window except the
// install tests, which stand one in the way fleet-live-clock.test.ts does; the federation test stands in a
// window the way federation-closed-store.test.ts does.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import {
  Ring, percentile, histBucket, histQuantileBucket, classifyFrame, scriptKey, sanitizeInvoker, uaClass, attributeScripts,
  createPerfTelemetry, installPerfTelemetry, perfFrameHandler,
  HIST_EDGES, HIST_BUCKETS, MAX_FRAME_TYPES, MAX_TOP_KEYS, SLOW_FRAME_MS, SLOW_ROWS_PER_MINUTE, FREE_RING, type PerfDeps,
} from "./perf-telemetry";
import { FederationManager } from "./federation";

const PAGE = "http://h:1/feed";

function harness(over: Partial<PerfDeps> = {}) {
  const clock = { t: 1000, wall: 1_700_000_000_000 };
  const posted: any[] = [];
  const rafQueue: Array<(t: number) => void> = [];
  const cancelled: number[] = [];
  const deps: PerfDeps = {
    now: () => clock.t,
    wallNow: () => clock.wall,
    post: (m) => posted.push(m),
    raf: (cb) => { rafQueue.push(cb); return rafQueue.length; },
    caf: (id) => cancelled.push(id),
    setInterval: null,
    observer: null,
    supportedEntryTypes: [],
    heapBytes: () => 200 * 1048576,
    domCount: () => 1234,
    visible: () => true,
    hiddenPane: () => false,
    ua: "chrome-desktop",
    pageUrl: PAGE,
    windowEvents: null,
    documentEvents: null,
    ...over,
  };
  /** run the animation-frame callbacks queued so far (one frame); a callback that queues another leaves it for the next call */
  const runRafs = () => { for (const cb of rafQueue.splice(0)) cb(clock.t); };
  /** a frame whose handler takes `ms` on the fake clock */
  const frame = (p: ReturnType<typeof createPerfTelemetry>, msg: unknown, ms: number) =>
    p.frame(msg, () => { clock.t += ms; });
  return { deps, clock, posted, rafQueue, cancelled, runRafs, frame };
}

const minuteRows = (posted: any[]) => posted.filter((m) => m.what === "minute");
const slowRows = (posted: any[]) => posted.filter((m) => m.what === "slowframe");
/** a histogram with the given counts in the given buckets */
const hist = (at: Record<number, number>) => { const h = new Array(HIST_BUCKETS).fill(0); for (const k of Object.keys(at)) h[Number(k)] = at[Number(k)]; return h; };
const stat = (n: number, ms_sum: number, ms_max: number, n16: number, n100: number, h: number[]) => ({ n, ms_sum, ms_max, n16, n100, hist: h });

// ── pure pieces ──

test("classifyFrame: the type, a raw delta by slot, shell messages, and everything else as other", () => {
  assert.equal(classifyFrame({ type: "feed", asks: [] }), "feed");
  assert.equal(classifyFrame({ type: "chatTail", from: 3 }), "chatTail");
  assert.equal(classifyFrame({ type: "delta", slot: "bars", rev: 4 }), "delta:bars");
  assert.equal(classifyFrame({ type: "delta" }), "delta:other");
  assert.equal(classifyFrame({ romp: "paneFocus" }), "shell");
  assert.equal(classifyFrame({ type: "not an identifier!" }), "other");
  assert.equal(classifyFrame({ type: "x".repeat(40) }), "other");
  assert.equal(classifyFrame({}), "other");
  assert.equal(classifyFrame(null), "other");
  assert.equal(classifyFrame("feed"), "other");
});

test("Ring keeps the newest samples; percentile is nearest-rank", () => {
  const r = new Ring(FREE_RING);
  for (let i = 1; i <= 100; i++) r.push(i);
  assert.equal(r.n, 100);
  const v = r.values();
  assert.equal(v.length, 64);
  assert.equal(Math.min(...v), 37);
  assert.equal(Math.max(...v), 100);
  assert.equal(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9), 9);
  assert.equal(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5), 5);
  assert.equal(percentile([7], 0.9), 7);
  assert.equal(percentile([], 0.9), 0);
});

test("histBucket is log2 with 14 buckets; histQuantileBucket is nearest-rank over the counts", () => {
  assert.equal(HIST_BUCKETS, 14);
  assert.deepEqual([0, 0.99, 1, 1.5, 2, 3.9, 4, 16, 16.7, 31.9, 32, 100, 128, 4095.9, 4096, 1e9].map(histBucket),
    [0, 0, 1, 1, 2, 2, 3, 5, 5, 5, 6, 7, 8, 12, 13, 13]);
  assert.equal(HIST_EDGES[histBucket(100)], 128);              // 100 ms lands in the 64..128 bucket, read as "under 128"
  const h = hist({ 1: 9, 2: 11, 3: 7, 4: 4, 5: 2, 6: 1, 7: 2 });   // 36 frames
  assert.equal(histQuantileBucket(h, 0.5), 2);                 // rank 18: 9 + 11 covers it
  assert.equal(histQuantileBucket(h, 0.9), 5);                 // rank 33: 9+11+7+4+2 = 33
  assert.equal(histQuantileBucket(h, 0.99), 7);                // rank 36: the last frame
  assert.equal(histQuantileBucket(h, 1), 7);
  assert.equal(histQuantileBucket(hist({}), 0.9), -1);
  assert.equal(histQuantileBucket(hist({ 13: 3 }), 0.5), 13);
});

test("scriptKey: basename plus function and character position; the page's own scripts are page:; empty is unknown:", () => {
  assert.equal(scriptKey({ sourceURL: "http://h:1/dist/feed.js?v=1757100000", sourceFunctionName: "render", sourceCharPosition: 48213 }, PAGE), "feed.js:render@48213");
  assert.equal(scriptKey({ sourceURL: "http://h:1/dist/feed.js?v=1", sourceFunctionName: "", sourceCharPosition: 7 }, PAGE), "feed.js:(anonymous)@7");
  assert.equal(scriptKey({ sourceURL: "http://h:1/dist/render.js#x", sourceFunctionName: "chatTail" }, PAGE), "render.js:chatTail");
  // an inline script's sourceURL is the document URL (query stripped): the shim, the shell's boot code
  assert.equal(scriptKey({ sourceURL: "http://h:1/feed?token=abc", sourceFunctionName: "", sourceCharPosition: 31245 }, PAGE), "page:(anonymous)@31245");
  assert.equal(scriptKey({ sourceURL: "http://h:1/", sourceFunctionName: "boot" }, "http://h:1/"), "page:boot");
  assert.equal(scriptKey({ sourceURL: "http://h:1/", sourceFunctionName: "boot" }), "page:boot", "a directory URL is a page even with no pageUrl to compare");
  assert.equal(scriptKey({ sourceURL: "http://h:1/fleet", sourceFunctionName: "x" }, PAGE), "fleet:x", "another page's script keeps its basename");
  assert.equal(scriptKey({ sourceURL: "", sourceFunctionName: "" }), "unknown:(anonymous)");
  assert.equal(scriptKey({ sourceURL: "http://h/a.js", sourceFunctionName: "f", sourceCharPosition: -1 }), "a.js:f", "a negative position is not one");
});

test("sanitizeInvoker reduces ids, element sources and script URLs to identifiers", () => {
  assert.equal(sanitizeInvoker("DIV#tab-web.onclick"), "DIV.onclick");
  assert.equal(sanitizeInvoker("WebSocket.onmessage"), "WebSocket.onmessage");
  assert.equal(sanitizeInvoker("Window.requestAnimationFrame"), "Window.requestAnimationFrame");
  // a classic/module script's invoker is the script's URL: host, port and dist token included
  assert.equal(sanitizeInvoker("http://h:1/dist/feed.js?v=1757100000"), "feed.js");
  assert.equal(sanitizeInvoker("https://example.test:8443/dist/render.js#frag"), "render.js");
  assert.equal(sanitizeInvoker("/dist/federation.js?v=2"), "federation.js");
  assert.equal(sanitizeInvoker("http://h:1/"), "page");
  // an id-less element with a src: the source (a data: URL, a file route with a path and sid) never survives
  assert.equal(sanitizeInvoker("IMG[src=data:image/png;base64,iVBORw0KGgo].onload"), "IMG[src].onload");
  assert.equal(sanitizeInvoker("IMG[src=/file?path=%2Fsrc%2Fapp.py&sid=11111111-2222].onerror"), "IMG[src].onerror");
  assert.equal(sanitizeInvoker("IMG#logo[src=/media/x.svg].onload"), "IMG[src].onload");
  assert.equal(sanitizeInvoker(undefined), "");
  assert.equal(sanitizeInvoker("x".repeat(100)).length, 64);
});

test("uaClass is coarse", () => {
  assert.equal(uaClass("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"), "chrome-desktop");
  assert.equal(uaClass("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"), "safari-ios");
  assert.equal(uaClass("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15", 5), "safari-ios");
  assert.equal(uaClass("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"), "other");
  assert.equal(uaClass("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"), "other");
});

test("attributeScripts sums per key, largest first, keeps the latest sanitized invoker, and keys page scripts as page:", () => {
  const rows = attributeScripts([
    { sourceURL: "http://h:1/dist/feed.js?v=1", sourceFunctionName: "render", sourceCharPosition: 10, invoker: "DIV#c1.onclick", duration: 30 },
    { sourceURL: "http://h:1/dist/feed.js?v=1", sourceFunctionName: "render", sourceCharPosition: 10, invoker: "WebSocket.onmessage", duration: 50 },
    { sourceURL: "http://h:1/feed?token=t", sourceFunctionName: "", sourceCharPosition: 31245, invoker: "WebSocket.onmessage", duration: 10 },
    { sourceURL: "http://h:1/dist/federation.js?v=1", sourceFunctionName: "", invoker: "http://h:1/dist/federation.js?v=1", duration: 5 },
    { sourceURL: "x", duration: "nope" },
  ], PAGE);
  assert.deepEqual(rows, [
    { k: "feed.js:render@10", ms: 80, inv: "WebSocket.onmessage" },
    { k: "page:(anonymous)@31245", ms: 10, inv: "WebSocket.onmessage" },
    { k: "federation.js:(anonymous)", ms: 5, inv: "federation.js" },
  ]);
});

// ── frames and the minute flush ──

/** every value in a row is a number, a boolean, null, or a string of code identifiers */
function assertIdentifiersOnly(v: any): void {
  if (v === null || typeof v === "number" || typeof v === "boolean") return;
  if (typeof v === "string") { assert.match(v, /^[A-Za-z0-9_.:()@[\]-]*$/, "not an identifier: " + v); return; }
  for (const x of Object.values(v)) assertIdentifiersOnly(x);
}

test("a minute with frames sends ONE row in the documented shape; an idle minute sends nothing", () => {
  const h = harness();
  const p = createPerfTelemetry("feed", h.deps);
  h.frame(p, { type: "feed" }, 10);
  h.frame(p, { type: "feed" }, 20);
  h.frame(p, { type: "chatTail" }, 2);
  h.frame(p, { romp: "paneFocus" }, 0.5);
  h.frame(p, { type: "delta", slot: "bars" }, 1);
  h.frame(p, null, 0);                       // a frame the handler ignores still counts
  // the main thread frees 45 ms after the first frame's handler returned: two animation frames later
  h.clock.t += 45;
  h.runRafs(); h.runRafs();
  h.clock.wall += 60_000;
  p.tick();
  const rows = minuteRows(h.posted);
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(row.type, "clientDiag");
  assert.equal(row.surface, "perf");
  assert.equal(row.what, "minute");
  const d = row.data;
  assert.equal(d.app, "feed");
  assert.equal(d.since, 1_700_000_000_000);
  assert.equal(d.span_ms, 60_000);
  assert.deepEqual(d.frames.feed, stat(2, 30, 20, 1, 0, hist({ 4: 1, 5: 1 })));
  assert.deepEqual(d.frames.chatTail, stat(1, 2, 2, 0, 0, hist({ 2: 1 })));
  assert.deepEqual(d.frames.shell, stat(1, 0.5, 0.5, 0, 0, hist({ 0: 1 })));
  assert.deepEqual(d.frames["delta:bars"], stat(1, 1, 1, 0, 0, hist({ 1: 1 })));
  assert.deepEqual(d.frames.other, stat(1, 0, 0, 0, 0, hist({ 0: 1 })));
  // one free sample: scheduled after the first frame (t=1010), resolved at 1010+20+2+0.5+1+0+45
  assert.deepEqual(d.free, { n: 1, p50: 68.5, p90: 68.5, max: 68.5 });
  assert.deepEqual(d.loaf, { n: 0, blocking_ms: 0, worst_ms: 0, top: [], src: "none" });
  assert.deepEqual(d.slow, { sent: 0, suppressed: 0, suppressed_worst_ms: 0 });
  assert.equal(d.heap_mb, 200);
  assert.equal(d.dom, 1234);
  assert.equal(d.visible, true);
  assert.equal(d.hidden_pane, false);
  assert.equal(d.ua, "chrome-desktop");
  assert.deepEqual(Object.keys(d).sort(),
    ["app", "dom", "frames", "free", "heap_mb", "hidden_pane", "loaf", "since", "slow", "span_ms", "ua", "visible"]);
  assertIdentifiersOnly(d);
  // the next minute: nothing happened, nothing sent, and the bucket restarted at the new wall time
  assert.equal(slowRows(h.posted).length, 0);
  h.clock.wall += 60_000;
  p.tick();
  assert.equal(h.posted.length, 1);
  assert.equal(p.snapshot().since, 1_700_000_120_000);
});

test("a partial minute (pagehide) carries its own span; the histogram is exact over every frame of a busy type", () => {
  const win = new EventTarget();
  const h = harness({ windowEvents: win });
  const p = createPerfTelemetry("chat", h.deps);
  for (let i = 1; i <= 100; i++) h.frame(p, { type: "chatTail" }, i);   // 1..100 ms
  h.clock.wall += 30_000;
  win.dispatchEvent(new Event("pagehide"));
  const d = minuteRows(h.posted)[0].data;
  assert.equal(d.span_ms, 30_000);
  const f = d.frames.chatTail;
  assert.equal(f.n, 100);
  assert.equal(f.ms_sum, 5050);
  assert.equal(f.ms_max, 100);
  assert.equal(f.n16, 84);                    // 17..100
  assert.equal(f.n100, 1);
  assert.deepEqual(f.hist, hist({ 1: 1, 2: 2, 3: 4, 4: 8, 5: 16, 6: 32, 7: 37 }));   // [1,2) [2,4) [4,8) [8,16) [16,32) [32,64) [64,128)
  assert.equal(histQuantileBucket(f.hist, 0.9), 7);   // rank 90 lands in 64..128: the exact p90 is 90 ms
  assert.equal((p.snapshot() as any).frames.chatTail?.p90_le, undefined, "the bucket rolled over on the flush");
});

test("distinct frame types are capped per minute, wire types and fed: keys each; the overflow folds into other / fed:other", () => {
  const h = harness();
  const p = createPerfTelemetry("chat", h.deps);
  for (let i = 0; i < MAX_FRAME_TYPES + 3; i++) h.frame(p, { type: "t" + i }, 1);
  for (let i = 0; i < MAX_FRAME_TYPES + 2; i++) p.timed("fed:f" + i, () => { h.clock.t += 1; });
  h.clock.wall += 60_000;
  p.tick();
  const frames = minuteRows(h.posted)[0].data.frames;
  assert.equal(Object.keys(frames).length, 2 * (MAX_FRAME_TYPES + 1));
  assert.equal(frames.other.n, 3);
  assert.equal(frames["fed:other"].n, 2);
});

test("the type cap does not count the fed: keys against the wire types, and a folded frame's slowframe row names its type", () => {
  const h = harness();
  const p = createPerfTelemetry("chat", h.deps);
  // 20 wire types, each inside its federation bracket: 40 keys, none folded
  for (let i = 0; i < 20; i++) p.timed("fed:t" + i, () => h.frame(p, { type: "t" + i }, 1));
  let s: any = p.snapshot();
  assert.equal(Object.keys(s.frames).length, 40);
  assert.equal("other" in s.frames, false);
  // fill the wire-type cap, then a slow frame of a new type: counted under other, reported as itself
  for (let i = 20; i < MAX_FRAME_TYPES; i++) h.frame(p, { type: "t" + i }, 1);
  p.timed("fed:zzz", () => h.frame(p, { type: "zzz" }, 150));
  s = p.snapshot();
  assert.equal(s.frames.other.n, 1);
  assert.equal("zzz" in s.frames, false);
  assert.equal(s.frames["fed:zzz"].n, 1, "the federation key had room of its own");
  assert.deepEqual(slowRows(h.posted)[0].data, { app: "chat", type: "zzz", ms: 150, dom: 1234 });
  // the same fold without a federation bracket (VS Code): the slowframe row still names the type
  h.frame(p, { type: "yyy" }, 120);
  assert.equal(slowRows(h.posted)[1].data.type, "yyy");
  assert.equal((p.snapshot() as any).frames.other.n, 2);
});

test("nested brackets: each level records its own time, and they add up to the frame", () => {
  const h = harness();
  const p = createPerfTelemetry("feed", h.deps);
  // federation's inbound (3 ms of merge, 2 ms of post-dispatch bookkeeping) around the pane's handler (4 ms)
  p.timed("fed:feedDelta", () => { h.clock.t += 3; p.frame({ type: "feed" }, () => { h.clock.t += 4; }); h.clock.t += 2; });
  const s: any = p.snapshot();
  assert.equal(s.frames["fed:feedDelta"].ms_sum, 5);
  assert.equal(s.frames["fed:feedDelta"].n, 1);
  assert.equal(s.frames.feed.ms_sum, 4);
  assert.equal(s.frames.feed.n, 1);
  assert.equal(s.frames.feed.p90_le, 8);
  // the pane's own exception propagates through both levels and both still record
  assert.throws(() => p.timed("fed:feed", () => { h.clock.t += 1; p.frame({ type: "feed" }, () => { h.clock.t += 5; throw new Error("pane bug"); }); }), /pane bug/);
  const s2: any = p.snapshot();
  assert.equal(s2.frames["fed:feed"].ms_sum, 1);
  assert.equal(s2.frames.feed.ms_sum, 9);
  assert.equal(s2.frames.feed.n, 2);
});

test("the slowframe test and the free sample belong to the outermost bracket, with the frame's whole time", () => {
  const h = harness();
  const p = createPerfTelemetry("feed", h.deps);
  // 60 ms of federation merge around a 60 ms handler: neither level alone is slow, the frame is
  p.timed("fed:feed", () => { h.clock.t += 60; p.frame({ type: "feed" }, () => { h.clock.t += 60; }); });
  const rows = slowRows(h.posted);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].data, { app: "feed", type: "feed", ms: 120, dom: 1234 });   // the wire type, not fed:feed
  assert.equal(h.rafQueue.length, 1, "one free sample armed, by the outer bracket");
});

test("wrapFrameHandler hands the event through and times by its data", () => {
  const h = harness();
  const p = createPerfTelemetry("fleet", h.deps);
  const seen: any[] = [];
  const wrapped = p.wrapFrameHandler((e) => { seen.push(e.data); h.clock.t += 2; });
  wrapped({ data: { type: "feed" } } as MessageEvent);
  wrapped({ data: { type: "warn", text: "x" } } as MessageEvent);
  assert.deepEqual(seen.map((m) => m.type), ["feed", "warn"]);
  const s: any = p.snapshot();
  assert.equal(s.frames.feed.n, 1);
  assert.equal(s.frames.warn.n, 1);
});

// ── the free-main-thread sample ──

test("free: one sample in flight; frames landing before it resolves share it; hidden panes take none", () => {
  const h = harness();
  const p = createPerfTelemetry("feed", h.deps);
  h.frame(p, { type: "feed" }, 10);          // ends at 1010, sample armed
  h.frame(p, { type: "feed" }, 10);          // ends at 1020: the thread is still busy, no second sample
  assert.equal(h.rafQueue.length, 1);
  h.clock.t += 30;                           // 1050
  h.runRafs();                               // first rAF queues the second
  assert.equal(h.rafQueue.length, 1);
  h.clock.t += 10;                           // 1060
  h.runRafs();
  const s: any = p.snapshot();
  assert.deepEqual(s.free, { n: 1, p50: 50, p90: 50, max: 50 });   // 1060 - 1010
  assert.equal(s.free_pending, false);
  // a pane with no viewport (the shell's display:none) arms nothing: rAF does not run there
  const hidden = harness({ hiddenPane: () => true });
  const q = createPerfTelemetry("feed", hidden.deps);
  hidden.frame(q, { type: "feed" }, 10);
  assert.equal(hidden.rafQueue.length, 0);
});

test("free: a sample that resolves after the document went hidden, or the pane lost its viewport, is dropped", () => {
  let vis = true;
  const flip = harness({ visible: () => vis });
  const r = createPerfTelemetry("feed", flip.deps);
  flip.frame(r, { type: "feed" }, 10);
  vis = false;
  flip.clock.t += 5000;
  flip.runRafs(); flip.runRafs();
  assert.equal((r.snapshot() as any).free, null);
  assert.equal((r.snapshot() as any).free_pending, false, "and the slot is free for the next sample");
  let zero = false;
  const shrink = harness({ hiddenPane: () => zero });
  const q = createPerfTelemetry("feed", shrink.deps);
  shrink.frame(q, { type: "feed" }, 10);
  shrink.runRafs();                          // the first callback ran while visible
  zero = true;                               // the shell hid the pane before the second
  shrink.clock.t += 60_000;
  shrink.runRafs();
  assert.equal((q.snapshot() as any).free, null);
});

test("visibilitychange to hidden, and a resize to a zero viewport, cancel the armed sample; pagehide flushes", () => {
  const win = new EventTarget();
  const doc = new EventTarget();
  let vis = true;
  let zero = false;
  const h = harness({ windowEvents: win, documentEvents: doc, visible: () => vis, hiddenPane: () => zero });
  const p = createPerfTelemetry("feed", h.deps);
  h.frame(p, { type: "feed" }, 10);
  assert.equal((p.snapshot() as any).free_pending, true);
  vis = false;
  doc.dispatchEvent(new Event("visibilitychange"));
  assert.equal((p.snapshot() as any).free_pending, false);
  assert.deepEqual(h.cancelled, [1]);
  vis = true;
  // a resize that leaves the pane with a viewport cancels nothing; one to zero size does
  h.frame(p, { type: "feed" }, 10);
  win.dispatchEvent(new Event("resize"));
  assert.equal((p.snapshot() as any).free_pending, true);
  zero = true;
  win.dispatchEvent(new Event("resize"));
  assert.equal((p.snapshot() as any).free_pending, false);
  assert.deepEqual(h.cancelled, [1, 2]);
  win.dispatchEvent(new Event("pagehide"));
  assert.equal(minuteRows(h.posted).length, 1);
  assert.equal(minuteRows(h.posted)[0].data.frames.feed.n, 2);
});

// ── slow frames ──

test("slowframe: a handler at the threshold sends a row at once when no long-frame observer exists", () => {
  const h = harness();
  const p = createPerfTelemetry("feed", h.deps);
  h.frame(p, { type: "feed" }, SLOW_FRAME_MS - 0.1);
  assert.equal(slowRows(h.posted).length, 0);
  h.frame(p, { type: "feed" }, SLOW_FRAME_MS);
  const rows = slowRows(h.posted);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].surface, "perf");
  assert.deepEqual(rows[0].data, { app: "feed", type: "feed", ms: 100, dom: 1234 });
});

test("slowframe cap: 50 slow frames in a minute send at most the cap; the rest are counted with their worst", () => {
  const h = harness();
  const p = createPerfTelemetry("feed", h.deps);
  for (let i = 0; i < 50; i++) h.frame(p, { type: "feed" }, 100 + i);
  assert.equal(slowRows(h.posted).length, SLOW_ROWS_PER_MINUTE);
  assert.deepEqual(slowRows(h.posted).map((r) => r.data.ms), [100, 101, 102, 103, 104], "the minute's first");
  h.clock.wall += 60_000;
  p.tick();
  const d = minuteRows(h.posted)[0].data;
  assert.deepEqual(d.slow, { sent: 5, suppressed: 45, suppressed_worst_ms: 149 });
  assert.equal(d.frames.feed.n100, 50);
  // the cap is per minute: the next minute sends again
  h.frame(p, { type: "feed" }, 200);
  assert.equal(slowRows(h.posted).length, SLOW_ROWS_PER_MINUTE + 1);
});

class FakeObserver {
  static cb: ((list: { getEntries(): any[] }) => void) | null = null;
  static opts: any = null;
  static constructed = 0;
  constructor(cb: (list: { getEntries(): any[] }) => void) { FakeObserver.cb = cb; FakeObserver.constructed++; }
  observe(o: any): void { FakeObserver.opts = o; }
  disconnect(): void {}
  static deliver(entries: any[]): void { FakeObserver.cb!({ getEntries: () => entries }); }
}

test("slowframe with a long-frame observer: held for the report covering it, then sent with its attribution", () => {
  const h = harness({ observer: FakeObserver as any, supportedEntryTypes: ["long-animation-frame", "longtask"] });
  const p = createPerfTelemetry("feed", h.deps);
  assert.equal(p.observerKind, "loaf");
  assert.deepEqual(FakeObserver.opts, { type: "long-animation-frame", buffered: false });
  h.frame(p, { type: "feed" }, 130);         // 1000..1130
  assert.equal(slowRows(h.posted).length, 0, "held for the browser's report");
  assert.equal((p.snapshot() as any).pending_slow, 1);
  FakeObserver.deliver([{
    startTime: 995, duration: 140, blockingDuration: 90,
    scripts: [
      { sourceURL: "http://h:1/feed?token=t", sourceFunctionName: "", sourceCharPosition: 31245, invoker: "WebSocket.onmessage", duration: 110 },
      { sourceURL: "http://h:1/dist/feed.js?v=1", sourceFunctionName: "", sourceCharPosition: 48213, invoker: "Window.requestAnimationFrame", duration: 20 },
    ],
  }]);
  const rows = slowRows(h.posted);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].data, {
    app: "feed", type: "feed", ms: 130, dom: 1234,
    loaf: { ms: 140, blocking_ms: 90, top: [{ k: "page:(anonymous)@31245", ms: 110, inv: "WebSocket.onmessage" }, { k: "feed.js:(anonymous)@48213", ms: 20, inv: "Window.requestAnimationFrame" }] },
  });
  assert.equal((p.snapshot() as any).pending_slow, 0);
  assertIdentifiersOnly(rows[0].data);
});

test("slowframe backstops: a later report that starts after it, or the minute tick, sends it without attribution; the cap bounds the held rows", () => {
  const h = harness({ observer: FakeObserver as any, supportedEntryTypes: ["long-animation-frame"] });
  const p = createPerfTelemetry("chat", h.deps);
  h.frame(p, { type: "chatTail" }, 120);     // 1000..1120
  FakeObserver.deliver([{ startTime: 1500, duration: 60, blockingDuration: 10, scripts: [] }]);
  let rows = slowRows(h.posted);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0].data, { app: "chat", type: "chatTail", ms: 120, dom: 1234 });
  for (let i = 0; i < 10; i++) h.frame(p, { type: "chatTail" }, 150);
  assert.equal(slowRows(h.posted).length, 1);
  assert.equal((p.snapshot() as any).pending_slow, SLOW_ROWS_PER_MINUTE - 1, "the cap counts the one already sent");
  h.clock.wall += 60_000;
  p.tick();
  rows = slowRows(h.posted);
  assert.equal(rows.length, SLOW_ROWS_PER_MINUTE);
  assert.equal(rows[1].data.ms, 150);
  assert.equal("loaf" in rows[1].data, false);
  assert.equal(minuteRows(h.posted)[0].data.slow.suppressed, 6);
});

// ── long-frame aggregation ──

test("long frames: entries over 50 ms aggregate into count, blocking, worst and the top attributed keys", () => {
  const h = harness({ observer: FakeObserver as any, supportedEntryTypes: ["long-animation-frame"] });
  const p = createPerfTelemetry("feed", h.deps);
  FakeObserver.deliver([
    { startTime: 10, duration: 40, blockingDuration: 0, scripts: [{ sourceURL: "a.js", sourceFunctionName: "x", duration: 40 }] },   // under 50: ignored
    { startTime: 100, duration: 120, blockingDuration: 70, scripts: [
      { sourceURL: "http://h:1/dist/feed.js?v=2", sourceFunctionName: "render", sourceCharPosition: 1200, invoker: "WebSocket.onmessage", duration: 100 },
      { sourceURL: "http://h:1/dist/feed.js?v=2", sourceFunctionName: "paintFreezeBadges", sourceCharPosition: 900, invoker: "WebSocket.onmessage", duration: 15 },
    ] },
    { startTime: 400, duration: 80, blockingDuration: 30 },                                                                            // no scripts field
    { startTime: 700, duration: 300, blockingDuration: 250, scripts: [
      { sourceURL: "http://h:1/dist/feed.js?v=2", sourceFunctionName: "render", sourceCharPosition: 1200, invoker: "Window.requestAnimationFrame", duration: 280 },
    ] },
  ]);
  h.clock.wall += 60_000;
  p.tick();
  const rows = minuteRows(h.posted);
  assert.equal(rows.length, 1, "a long frame alone makes the minute worth a row");
  assert.deepEqual(rows[0].data.frames, {});
  assert.deepEqual(rows[0].data.loaf, {
    n: 3, blocking_ms: 350, worst_ms: 300, src: "loaf",
    top: [
      { k: "feed.js:render@1200", ms: 380, n: 2, inv: "Window.requestAnimationFrame" },
      { k: "feed.js:paintFreezeBadges@900", ms: 15, n: 1, inv: "WebSocket.onmessage" },
    ],
  });
});

test("long frames: distinct attribution keys are capped per minute; the overflow folds into other", () => {
  const h = harness({ observer: FakeObserver as any, supportedEntryTypes: ["long-animation-frame"] });
  const p = createPerfTelemetry("feed", h.deps);
  // one report fills the 64 keys the minute tracks (a distinct character position each) …
  const scripts = [];
  for (let i = 0; i < MAX_TOP_KEYS; i++) scripts.push({ sourceURL: "http://h:1/dist/feed.js", sourceFunctionName: "", sourceCharPosition: i, duration: 1 });
  FakeObserver.deliver([{ startTime: 0, duration: 200, blockingDuration: 150, scripts }]);
  // … and the next report's new key has nowhere to go but other, with its time
  FakeObserver.deliver([{ startTime: 500, duration: 150, blockingDuration: 100, scripts: [{ sourceURL: "http://h:1/dist/render.js", sourceFunctionName: "chatTail", duration: 100 }] }]);
  const s: any = p.snapshot();
  assert.equal(s.loaf.top.length, 5);
  assert.deepEqual(s.loaf.top[0], { k: "other", ms: 100, n: 1, inv: "" }, "the 65th key folded, with its time");
  assert.equal(s.loaf.n, 2);
});

test("a chat minute with the viewer's paint bracket: fileview:paint with the pass cost, the free sample after it, the long frames the pane sees; no string carries a slash, a query, a path or a session id", () => {
  // The file viewer times each paint of a shown file's text body through the hosting pane's collector (file-view.ts
  // perfTimed, run for real in file-view-perf.test.ts); this is the row that pass produces, with the long frame the
  // browser attributes to the pane's bundle and to an inline image load off the /file route, which carries a path.
  const SID = "33333333-4444-5555-6666-777777777777";
  const h = harness({ pageUrl: "http://h:1/chat", observer: FakeObserver as any, supportedEntryTypes: ["long-animation-frame"] });
  const p = createPerfTelemetry("chat", h.deps);
  p.timed("fileview:paint", () => { h.clock.t += 180; });   // a large file painted
  h.runRafs(); h.clock.t += 40; h.runRafs();                // two animation frames later: the free sample
  FakeObserver.deliver([{ startTime: 1000, duration: 190, blockingDuration: 140, scripts: [
    { sourceURL: "http://h:1/dist/chat.js?v=1757100000&sid=" + SID, sourceFunctionName: "paintAll", sourceCharPosition: 9000, invoker: "Window.requestAnimationFrame", duration: 150 },
    { sourceURL: "http://h:1/chat?token=abc&sid=" + SID, sourceFunctionName: "", sourceCharPosition: 4000, invoker: "IMG[src=/file?path=/repo/notes-api/docs/plot.png&sid=" + SID + "].onload", duration: 30 },
  ] }]);
  h.clock.wall += 60_000;
  p.tick();
  const rows = minuteRows(h.posted);
  assert.equal(rows.length, 1);
  const d = rows[0].data;
  assert.equal(d.app, "chat");
  assert.deepEqual(d.frames["fileview:paint"], stat(1, 180, 180, 1, 1, hist({ 8: 1 })));    // 128-256 ms
  assert.deepEqual(d.free, { n: 1, p50: 40, p90: 40, max: 40 });
  assert.equal(d.loaf.n, 1);
  assert.deepEqual(d.loaf.top.map((t: any) => t.k), ["chat.js:paintAll@9000", "page:(anonymous)@4000"]);
  assert.deepEqual(d.loaf.top.map((t: any) => t.inv), ["Window.requestAnimationFrame", "IMG[src].onload"]);
  assert.equal(slowRows(h.posted).length, 1, "a 180 ms paint is a slow frame too, with the report's attribution");
  // the privacy contract, over the minute row and the slowframe row: every string is a code identifier (so no
  // slash, no query), and neither the session id nor a path word the inputs carried appears anywhere, keys included
  const both = h.posted.map((m) => m.data);
  assertIdentifiersOnly(both);
  const text = JSON.stringify(both);
  assert.ok(!text.includes(SID) && !text.includes("33333333"), "no session id in either row");
  assert.ok(!text.includes("notes-api") && !text.includes("plot.png"), "no path in either row");
});

test("longtask fallback: no long-animation-frame support observes longtask, blocking is time over 50 ms, no attribution", () => {
  const h = harness({ observer: FakeObserver as any, supportedEntryTypes: ["longtask", "mark"] });
  const p = createPerfTelemetry("feed", h.deps);
  assert.equal(p.observerKind, "longtask");
  assert.deepEqual(FakeObserver.opts, { type: "longtask", buffered: false });
  FakeObserver.deliver([{ startTime: 0, duration: 130, attribution: [{ containerType: "iframe" }] }]);
  // a slow handler sends at once under this observer: longtask entries carry no scripts to wait for
  h.frame(p, { type: "feed" }, 110);
  assert.equal(slowRows(h.posted).length, 1);
  assert.equal("loaf" in slowRows(h.posted)[0].data, false);
  const s: any = p.snapshot();
  assert.deepEqual(s.loaf, { n: 1, blocking_ms: 80, worst_ms: 130, top: [], src: "longtask" });
});

// ── feature guards ──

test("guards: no observer, no heap, a throwing DOM count, no rAF: the row still goes out, minus those fields", () => {
  const h = harness({ observer: null, heapBytes: () => null, domCount: () => { throw new Error("no document"); }, raf: null });
  const p = createPerfTelemetry("feed", h.deps);
  assert.equal(p.observerKind, "none");
  h.frame(p, { type: "feed" }, 3);
  assert.equal(h.rafQueue.length, 0);
  h.clock.wall += 60_000;
  p.tick();
  const d = minuteRows(h.posted)[0].data;
  assert.equal("heap_mb" in d, false);
  assert.equal(d.dom, null);
  assert.equal(d.free, null);
  assert.equal(d.loaf.src, "none");
  // an observer whose support list is empty is not constructed at all
  FakeObserver.constructed = 0;
  const q = createPerfTelemetry("feed", harness({ observer: FakeObserver as any, supportedEntryTypes: [] }).deps);
  assert.equal(q.observerKind, "none");
  assert.equal(FakeObserver.constructed, 0);
  // an observer whose constructor throws leaves the collector working without it
  class Broken { constructor() { throw new Error("no observer here"); } observe() {} disconnect() {} }
  const r = createPerfTelemetry("feed", harness({ observer: Broken as any, supportedEntryTypes: ["long-animation-frame"] }).deps);
  assert.equal(r.observerKind, "none");
});

test("a post that throws never reaches the pane; a null post keeps measuring", () => {
  const h = harness({ post: () => { throw new Error("socket gone"); } });
  const p = createPerfTelemetry("feed", h.deps);
  h.frame(p, { type: "feed" }, 200);
  h.clock.wall += 60_000;
  assert.doesNotThrow(() => p.tick());
  p.setPost(null);
  h.frame(p, { type: "feed" }, 200);
  assert.equal((p.snapshot() as any).frames.feed.n, 1);
});

test("the timer, when given, is unref'd for a node host and drives tick()", () => {
  let cb: (() => void) | null = null;
  let unrefd = false;
  const h = harness({ setInterval: (fn, ms) => { assert.equal(ms, 60_000); cb = fn; return { unref: () => { unrefd = true; } }; } });
  const p = createPerfTelemetry("feed", h.deps);
  assert.equal(unrefd, true);
  h.frame(p, { type: "feed" }, 1);
  cb!();
  assert.equal(minuteRows(h.posted).length, 1);
});

// ── federation's bracket ──

test("federation times its inbound work as fed:<wire type> around the pane's handler, only when a collector is set", () => {
  const g: any = globalThis;
  const hadWindow = "window" in g, prevWindow = g.window;
  const hadLS = "localStorage" in g, prevLS = g.localStorage;
  const emitted: any[] = [];
  g.window = { dispatchEvent: (ev: any) => { if (ev && ev.data) emitted.push(ev.data); } };
  g.localStorage = { getItem: () => null, setItem: () => {} };
  try {
    const fm = new FederationManager();
    fm.inbound("", { type: "tabOrder", order: [], tabs: [] });
    assert.equal(emitted.filter((m) => m.type === "tabOrder").length, 1, "no collector: the frame still flows");
    const timed: string[] = [];
    fm.perf = {
      timed: <T,>(type: string, fn: () => T): T => { timed.push(type); return fn(); },
      frame: <T,>(_m: unknown, fn: () => T): T => fn(),
      wrapFrameHandler: (h: any) => h, snapshot: () => ({}), tick: () => {}, setPost: () => {},
    };
    fm.inbound("", { type: "tabOrder", order: [], tabs: [] });
    fm.inbound("", { type: "session", id: "11111111-2222-3333-4444-555555555555", events: [] });
    assert.deepEqual(timed, ["fed:tabOrder", "fed:session"]);
    assert.equal(emitted.filter((m) => m.type === "tabOrder").length, 2, "the bracket wraps the same work");
  } finally {
    if (hadWindow) g.window = prevWindow; else delete g.window;
    if (hadLS) g.localStorage = prevLS; else delete g.localStorage;
  }
});

test("the direct delivery path records the same two levels as the window path: fed:<type> around the pane's nested bracket", () => {
  // federation hands a merged frame to the registered handler by direct call (federation.ts emit); the pane registers the
  // perf-wrapped handler it also puts on window, so the call stack is fed:<type> > <type> exactly as through dispatchEvent
  const g: any = globalThis;
  const hadWindow = "window" in g, prevWindow = g.window;
  const hadLS = "localStorage" in g, prevLS = g.localStorage;
  const emitted: any[] = [];
  g.window = { dispatchEvent: (ev: any) => { if (ev && ev.data) emitted.push(ev.data); } };
  g.localStorage = { getItem: () => null, setItem: () => {} };
  try {
    const h = harness();
    const p = createPerfTelemetry("feed", h.deps);
    const fm = new FederationManager();
    fm.perf = p;
    const seen: string[] = [];
    fm.onFrame(p.wrapFrameHandler((e) => { seen.push(e.data.type); h.clock.t += 4; }));
    // federation's own work costs nothing on the fake clock: a timed stand-in around inboundNow is not reachable, so the
    // layer's own time shows as the outer bracket's total minus the pane's; the clock only moves inside the pane here
    fm.inbound("", { type: "feed", asks: [], now: 1 });
    fm.inbound("", { type: "feed", asks: [], now: 2 });
    assert.deepEqual(seen, ["feed", "feed"], "both frames reached the handler directly");
    assert.deepEqual(emitted, [], "and none was dispatched on window");
    const frames = (p.snapshot() as any).frames;
    assert.equal(frames["feed"].n, 2);
    assert.equal(frames["feed"].ms_sum, 8, "the pane's bracket: its own 4 ms per frame");
    assert.equal(frames["fed:feed"].n, 2);
    assert.equal(frames["fed:feed"].ms_sum, 0, "the layer's own time: the total minus the nested pane bracket");
    // a slow frame is attributed to the wire type at the outermost bracket, as on the window path
    fm.onFrame(() => { h.clock.t += 120; });
    fm.inbound("", { type: "feed", asks: [], now: 3 });
    const rows = h.posted.filter((m) => m.what === "slowframe");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].data.type, "feed");
    assert.equal(rows[0].data.ms, 124, "the whole synchronous handling, both handlers, federation's bracket included");
  } finally {
    if (hadWindow) g.window = prevWindow; else delete g.window;
    if (hadLS) g.localStorage = prevLS; else delete g.localStorage;
  }
});

// ── the browser install ──

test("installPerfTelemetry: null without a window or without performance.now; the handler is handed back unwrapped", () => {
  const g: any = globalThis;
  assert.equal(typeof g.window, "undefined");
  assert.equal(installPerfTelemetry("feed"), null);
  const handler = () => {};
  assert.equal(perfFrameHandler("feed", undefined, handler), handler);
  // a stand-in page with no performance object (the node DOM stand-ins): nothing installed
  g.window = new EventTarget();
  g.document = new EventTarget();
  try {
    assert.equal(installPerfTelemetry("feed"), null);
    assert.equal(g.window.__rompPerf, undefined);
    assert.equal(perfFrameHandler("feed", undefined, handler), handler);
  } finally {
    delete g.window;
    delete g.document;
  }
});

test("installPerfTelemetry: one collector per page on window.__rompPerf, wired to the page's rAF, timer, URL and transport", () => {
  const g: any = globalThis;
  const win: any = new EventTarget();
  let t = 0;
  win.performance = { now: () => t };
  win.navigator = { userAgent: "Mozilla/5.0 (Macintosh) Chrome/128.0.0.0 Safari/537.36", maxTouchPoints: 0 };
  win.location = { href: "http://h:1/timeline?token=abc&wid=11111111" };
  win.parent = win;
  win.innerWidth = 800; win.innerHeight = 600;
  const rafs: Array<(t: number) => void> = [];
  const cancelled: number[] = [];
  win.requestAnimationFrame = (cb: (t: number) => void) => { rafs.push(cb); return rafs.length; };
  win.cancelAnimationFrame = (id: number) => cancelled.push(id);
  let intervalCb: (() => void) | null = null;
  let intervalMs = 0;
  let unrefd = false;
  win.setInterval = (cb: () => void, ms: number) => { intervalCb = cb; intervalMs = ms; return { unref: () => { unrefd = true; } }; };
  const sent: any[] = [];
  win.__rompLocalSend = (m: any) => sent.push({ via: "shim", m });
  const doc: any = new EventTarget();
  doc.visibilityState = "visible";
  doc.getElementsByTagName = () => ({ length: 42 });
  g.window = win;
  g.document = doc;
  try {
    const a = installPerfTelemetry("timeline");
    assert.ok(a);
    assert.equal(win.__rompPerf, a);
    assert.equal(intervalMs, 60_000);
    assert.equal(unrefd, true);
    // the default transport is the shim's send; a frame arms a free sample through the page's rAF
    a!.timed("bars", () => { t += 10; });
    assert.equal(rafs.length, 1);
    for (const cb of rafs.splice(0)) cb(t);
    t += 5;
    for (const cb of rafs.splice(0)) cb(t);
    assert.deepEqual((a!.snapshot() as any).free, { n: 1, p50: 5, p90: 5, max: 5 });
    // a hide cancels through the page's cancelAnimationFrame
    a!.timed("bars", () => { t += 1; });
    doc.visibilityState = "hidden";
    doc.dispatchEvent(new Event("visibilitychange"));
    assert.deepEqual(cancelled, [1]);
    doc.visibilityState = "visible";
    // the page's URL, query stripped, keys its inline scripts as page:
    intervalCb!();
    assert.equal(sent.length, 1);
    assert.equal(sent[0].m.what, "minute");
    assert.equal(sent[0].m.data.app, "timeline");
    // a pane's own transport takes over, and a slow frame is timed on the page's clock
    const posted: any[] = [];
    const wrapped = perfFrameHandler("timeline", (m) => posted.push(m), () => { t += 150; });
    wrapped({ data: { type: "bars" } } as MessageEvent);
    assert.equal(sent.length, 1);
    assert.equal(posted.length, 1);
    assert.deepEqual(posted[0].data, { app: "timeline", type: "bars", ms: 150, dom: 42 });
    assert.equal(installPerfTelemetry("timeline"), a);
    const s: any = a!.snapshot();
    assert.equal(s.ua, "chrome-desktop");
    assert.equal(s.observer, "none");
    assert.equal(s.frames.bars.n, 1);
  } finally {
    delete g.window;
    delete g.document;
  }
});

test("installPerfTelemetry: hidden_pane is the pane shim's union, the zero-viewport probe OR the pane's published word", () => {
  // The pane shim's paneHidden() (kernel.py) and this row agree: a pane that publishes its word (paint-gate.ts
  // publishPaneHidden, chat-visibility.ts) is reported hidden after a first show in Chromium, where the probe alone
  // never saw it (a display:none iframe keeps its size there); in Firefox the viewport goes to zero and the observer
  // does not run, so the probe carries it and a stale word of false must not override it.
  const g: any = globalThis;
  const win: any = new EventTarget();
  win.performance = { now: () => 0 };
  win.navigator = { userAgent: "Mozilla/5.0 (Macintosh) Chrome/128.0.0.0 Safari/537.36", maxTouchPoints: 0 };
  win.location = { href: "http://h:1/chat" };
  win.parent = {};                                   // framed
  win.innerWidth = 800; win.innerHeight = 600;
  const doc: any = new EventTarget();
  doc.visibilityState = "visible";
  doc.getElementsByTagName = () => ({ length: 3 });
  g.window = win;
  g.document = doc;
  try {
    const a = installPerfTelemetry("chat")!;
    assert.ok(a);
    assert.equal((a.snapshot() as any).hidden_pane, false, "unset word, a viewport: the probe says shown");
    win.__rompPaneHidden = true;
    assert.equal((a.snapshot() as any).hidden_pane, true, "the pane's word: hidden after a first show, size kept");
    win.innerWidth = 0; win.__rompPaneHidden = false;
    assert.equal((a.snapshot() as any).hidden_pane, true, "a zero viewport says hidden whatever a stale word says");
    delete win.__rompPaneHidden;
    assert.equal((a.snapshot() as any).hidden_pane, true, "unset: the probe's turn (zero viewport)");
    win.innerWidth = 800; win.parent = win;
    win.__rompPaneHidden = true;
    assert.equal((a.snapshot() as any).hidden_pane, true, "a standalone page: the probe never applies, the word (the tab's hiding) does");
  } finally {
    delete g.window;
    delete g.document;
  }
});

// ── the wraps themselves ──
// Everything above drives the collector through PerfDeps, so nothing in it fails when a pane bundle stops
// wrapping its listener: the whole suite stayed green with a wrap removed (review, 2026-09-07). These pin the
// source text, the way heal-on-hostup.test.ts pins the chat pane's listener, for all four panes and for the
// collector install in federation's start().
const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const readUi = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");

test("each pane bundle's one frame listener is installed through listenForFrames, wrapped by perfFrameHandler under its own app name", () => {
  // the pane hands ONE handler to frame-listener.ts, which puts it on window and in federation's registry; a bare
  // window listener beside that call would be a second delivery path, and an unwrapped one a frame the collector
  // never sees (the import line has no paren, so the call is the only match for the count)
  const panes: Array<[string, string]> = [["render.ts", "chat"], ["feed.ts", "feed"], ["fleet.ts", "fleet"], ["timeline-main.ts", "timeline"]];
  for (const [file, app] of panes) {
    const src = readUi(file);
    assert.match(src, /import \{ perfFrameHandler \} from "\.\/perf-telemetry";/, file + " imports the wrapper");
    assert.match(src, /import \{ listenForFrames(?:, \w+)* \} from "\.\/frame-listener";/, file + " imports the helper");   // the chat also imports the manager-missing check (2026-09-10)
    const installs = src.match(/listenForFrames\(/g) || [];
    assert.equal(installs.length, 1, file + " installs its frame listener once");
    const bare = src.match(/window\.addEventListener\("message", /g) || [];
    assert.equal(bare.length, 0, file + " has no bare window message listener");
    assert.ok(src.includes('listenForFrames(perfFrameHandler("' + app + '", '),
      file + " installs it through perfFrameHandler as app " + app);
  }
});

test("federation's start() installs the page collector first: before __rompFed, whose inbound path times through it", () => {
  const src = readUi("federation.ts");
  assert.ok(src.includes('import { installPerfTelemetry, classifyFrame, type RompPerf } from "./perf-telemetry";'));
  const at = src.indexOf("\n  start(): void {");
  assert.ok(at > 0, "start() found");
  const end = src.indexOf("\n  }\n", at);
  assert.ok(end > at, "start() closes");
  const body = src.slice(at, end);
  const install = body.indexOf("this.perf = installPerfTelemetry(this.app);");
  assert.ok(install > 0, "start() installs the collector");
  const app = body.indexOf('this.app = w.__rompApp || "chat";');
  assert.ok(app > 0 && app < install, "the app name is read first: the collector is keyed by it");
  const fed = body.indexOf("w.__rompFed = {");
  assert.ok(fed > install, "__rompFed (the shim's inbound entry, which runs inbound() and its fed: brackets) is published after the collector");
  // nothing else runs before the install: the two lines above it are the window handle and the app name
  const before = body.slice(0, install).split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("//"));
  assert.deepEqual(before, ["start(): void {", "const w = window as any;", 'this.app = w.__rompApp || "chat";']);
});
