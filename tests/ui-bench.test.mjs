// tests/ui-bench.test.mjs: tools/ui-bench.mjs, the headless-Chrome bench for the dashboard panes.
//
// Three layers. Browser-free: the frame classifier (the report's rows), the percentile and compare
// arithmetic, the pairing of the shim's deliveries with the wire frames they carried (the shim's queue
// rules: whole-state kinds coalesce, chained kinds go one by one, keepalives and refused deltas stay
// with the shim, a shim from before the flush task delivers inside the handler), the report fold, the
// /tmp path guard that keeps a recording of real session data out
// of any git checkout (with a simulated macOS layout, where /tmp is a symlink), the private file
// modes a recording is written with, the synthesizer's wire shapes (the kernel's _keys lists for the
// feed and bars slots, contiguous delta revisions, a byte-stable stream), the --record client against a local
// WebSocket server (the query, the cookie and Origin credential form, the ready handshake and nothing
// else, the JSONL shape, the early-close and refusal errors), and the CPU-profile fold over a
// synthetic .cpuprofile, the per-user run directory with its dead-owner sweep, and the CLI's argument
// parsing with its browser-free commands. With python3 and a
// built dist: the Handler subprocess's environment (seen through a stub interpreter that echoes it)
// and its exit when the node process that started it is SIGKILLed. With a browser as well: a synthetic
// feed stream replayed at a fixed gap (so most frames reach the bundle as their own delivery) and a
// synthetic timeline stream replayed back-to-back (so the shim's queue coalesces them) into the REAL
// pages, served by the kernel's own page route and the built bundles, must produce a report with every
// frame type measured and settled, every frame accounted for by the handoff (delivered, coalesced, or the
// shim's own), and no console error, uncaught exception or failed resource load; the feed run also writes
// a CPU profile whose windows are the deliveries. Those tests skip, naming
// the reason, when a prerequisite is missing, unless ROMP_UI_BENCH_REQUIRE is set (CI sets it), when
// the skip becomes a failure so a runner image that lost its browser cannot pass silently. The replays
// assert only what holds under any scheduling (totals, ordering, presence, the handoff's accounting
// identity); the timing relations the bench exists to measure are assertions only under
// ROMP_UI_BENCH_TIMING=1 and diagnostics otherwise (see TIMING below).
//
// Everything here is synthetic: the notes-api demo domain, placeholder uuids, a fixed clock.
// Run: node --test tests/ui-bench.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  APPS, DELTA_SEP, INIT_SCRIPT, REPO, STRIPPED_ENV, STRIPPED_KEY_ENV, STRIPPED_KEY_ENV_PREFIXES, WHOLE_STATE_KINDS, aggregateProfile, assertTmpPath, attributeDeliveries, barsKeys, benchRoot, browserAvailability,
  buildReport, classifyFrame, compareReports, deliveredKind, feedKeys, frameKey, launchBrowser, loadFrames, mergeAggregates, mergeWindows, parseArgs, percentile,
  rankProfile, recordFrames, refineAlignment, renderCompare, renderProfile, renderReport, replay, sourceLocator, startFront, startPageServer, streamSummary,
  stripProfileQueries, summarize, sweepDeadRuns, synthesizeFrames, writeFrames,
} from "../tools/ui-bench.mjs";

const TOOL = path.join(REPO, "tools", "ui-bench.mjs");
const THIS_FILE = fileURLToPath(import.meta.url);
const requireExt = createRequire(path.join(REPO, "vscode-extension", "package.json"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mode = (p) => fs.statSync(p).mode & 0o777;
const UID = os.userInfo().uid;

/** A stand-in interpreter: announces a port and blocks on stdin the way the Handler's watcher does. */
function writeStub(dir, name = "python-stub", prelude = "") {
  const stub = path.join(dir, name);
  fs.writeFileSync(stub, `#!/bin/sh\n${prelude}echo "PORT 1"\ncat > /dev/null\n`, { mode: 0o755 });
  return stub;
}
/** A directory that passes startPageServer's dist check. */
function fakeDist(dir) {
  const dist = path.join(dir, "dist");
  fs.mkdirSync(dist, { recursive: true });
  fs.writeFileSync(path.join(dist, "feed.js"), "");
  return dist;
}

/** Set environment variables for the duration of `fn`, restoring the previous values after. */
async function withEnv(vars, fn) {
  const saved = {};
  for (const [k, v] of Object.entries(vars)) { saved[k] = process.env[k]; if (v === undefined) delete process.env[k]; else process.env[k] = v; }
  try { return await fn(); }
  finally { for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; } }
}

// ── the classifier ───────────────────────────────────────────────────────────────────────────────

test("classifyFrame files kernel frames by type, and a view delta by its slot", () => {
  assert.equal(classifyFrame('{"type": "delta", "slot": "bars", "base": 3, "rev": 4, "coll": {"turns": {"set": {"x": {"type": "not-me"}}}}}'), "delta:bars");
  assert.equal(classifyFrame('{"type": "delta", "slot": "feed", "base": 0, "rev": 1, "coll": {}}'), "delta:feed");
  assert.equal(classifyFrame('{"type":"delta","slot":"feed","base":3,"rev":4,"coll":{"asks":{"set":{}}},"rest":{"now":1760000000,"buildId":4}}'), "delta:feed", "the compact form too");
  assert.equal(classifyFrame('{"now": 1760000000, "type": "feed", "asks": []}'), "feed", "the feed frame carries now before type");
  assert.equal(classifyFrame('{"type": "ka", "dv": 1760000000}'), "ka");
  assert.equal(classifyFrame('{"type": "chatTail", "id": "11111111-2222-3333-4444-555555555555", "from": 3, "events": []}'), "chatTail");
  assert.equal(classifyFrame('{"type": "bars", "turns": {}, "judging": [], "messages": [], "now": 1, "warming": false}'), "bars");
  assert.equal(classifyFrame('{"type": "data", "data": {"type": "timeline", "sessions": []}}'), "data", "the outer frame's type, not the nested one");
});

test("classifyFrame falls back to a parse when the type is not in the head, and says other for the rest", () => {
  const late = '{"pad": "' + "x".repeat(5000) + '", "type": "session", "id": "s"}';
  assert.equal(classifyFrame(late), "session");
  assert.equal(classifyFrame('{"type": "delta", "pad": "' + "y".repeat(5000) + '", "slot": "bars"}'), "delta:?", "a delta whose slot is out of reach is still a delta");
  assert.equal(classifyFrame("not json at all"), "other");
  assert.equal(classifyFrame('{"x": 1}'), "other");
  assert.equal(classifyFrame('{"type": 7}'), "other");
  assert.equal(classifyFrame(null), "other");
  assert.equal(classifyFrame(Buffer.from("{}")), "other");
});

// ── arithmetic ───────────────────────────────────────────────────────────────────────────────────

test("percentile is nearest-rank and summarize folds a sample", () => {
  const xs = [10, 1, 5, 3, 8, 2, 9, 4, 7, 6];
  assert.equal(percentile(xs, 50), 5);
  assert.equal(percentile(xs, 90), 9);
  assert.equal(percentile(xs, 100), 10);
  assert.equal(percentile(xs, 0), 1);
  assert.equal(percentile([], 50), null);
  assert.equal(percentile([NaN, 4, undefined, 2], 50), 2, "non-numbers are dropped");
  assert.deepEqual(summarize([]), { n: 0, p50: null, p90: null, max: null, mean: null });
  assert.deepEqual(summarize([4, 2]), { n: 2, p50: 2, p90: 4, max: 4, mean: 3 });
});

function fakeRun({ perFrame, loaf = [], heapUsed = 1000, domElements = 10, consoleErrors = [], addListenerMessages = 0, banner = null,
  handoff = "none", flushes = 0, deliveries = 0, deliveryRows = [], flushRows = [], unattributed = 0, unattributedMs = 0, queueDrained = true, deliveryErrors = [],
  deliverySeams = {}, afterGc = true }) {
  return {
    readyMs: 100, replayMs: 50, sent: perFrame, perFrame, misaligned: 0, reconnects: 0, clientMessages: { ready: 1 }, clientDiag: {},
    handoff, flushes, deliveries, deliveryRows, flushRows, unattributed, unattributedMs, queueDrained, deliveryErrors, deliverySeams, afterGc,
    loaf, loafKind: "long-animation-frame", domElements, heap: { used: heapUsed, total: heapUsed * 2 }, addListenerMessages, banner,
    cdp: { nodes: domElements * 3, documents: 1, jsEventListeners: 5, layoutCount: 3, recalcStyleCount: 4, layoutMs: 1.5, recalcStyleMs: 0.5, scriptMs: 20, taskMs: 30, heapUsed, heapTotal: heapUsed * 2, heapBeforeGc: heapUsed + 512 },
    consoleErrors, pageErrors: [], warnings: 0, failedResources: [],
  };
}
/** A per-frame row as replayOnce builds it; `handoff` and `bundleMs` are the delivery pairing's verdict. */
const pf = (i, type, bytes, handlerMs, settleMs, handoff = undefined, bundleMs = null, delivery = -1) => ({ i, type, bytes, at: i, handlerMs, settleMs, t0: 1000 + i * 100, lenMatch: true, handoff, bundleMs, delivery });

test("buildReport folds runs per frame type with percentiles, attribution and end state; a shim that delivers inside the handler is read as shim plus bundle", () => {
  const run = fakeRun({
    // A shim from before the flush task: every frame the bundle sees is delivered inside its own handler.
    handoff: "handler", deliveries: 4,
    perFrame: [pf(0, "ka", 30, 0.1, 5, "shim"), pf(1, "feed", 5000, 40, 90, "delivered", 38, 0), pf(2, "delta:feed", 300, 4, 20, "delivered", 3, 1), pf(3, "delta:feed", 500, 6, 30, "delivered", 5, 2), pf(4, "delta:feed", 100, 2, 10, "delivered", 1, 3)],
    // The keys long-animation-frame entries produce on this harness: the bench's onmessage wrapper (the
    // entry point of every pushed frame) and a script's own evaluation, invoked by its URL.
    loaf: [{ start: 0, duration: 120, blocking: 70, scripts: [{ url: "ui-bench-instrument.js", fn: "rompBenchOnMessage", invoker: "DOMWebSocket.onmessage", duration: 100 },
      { url: "", fn: "", invoker: "http://127.0.0.1:1/feed?token=secret-looking", duration: 15 }] }],
    heapUsed: 4096, domElements: 250,
  });
  const r = buildReport({ app: "feed", framesFile: "/tmp/x.jsonl", cpuThrottle: 1, fast: true, iters: 1, browser: "test", runs: [run] });
  assert.equal(r.tool, "ui-bench");
  assert.equal(r.frames.total, 5);
  assert.equal(r.frames.bytes, 5930);
  assert.deepEqual(r.first, { index: 1, type: "feed", bytes: 5000, handlerMs: 40, bundleMs: 38, deliveredRuns: 1, handoff: "delivered", settleMs: 90 }, "the first content frame skips the keepalive");
  assert.deepEqual(Object.keys(r.types), ["feed", "delta:feed", "ka"], "rows ordered by bytes");
  assert.equal(r.types["delta:feed"].count, 3);
  assert.equal(r.types["delta:feed"].measured, 3);
  assert.equal(r.types["delta:feed"].delivered, 3);
  assert.equal(r.types["delta:feed"].coalesced, 0);
  assert.equal(r.types["delta:feed"].bytes, 900);
  assert.equal(r.types["delta:feed"].bytesMax, 500);
  assert.deepEqual(r.types["delta:feed"].handlerMs, { n: 3, p50: 4, p90: 6, max: 6, mean: 4 });
  assert.deepEqual(r.types["delta:feed"].bundleMs, { n: 3, p50: 3, p90: 5, max: 5, mean: 3 });
  assert.deepEqual(r.types["delta:feed"].settleMs, { n: 3, p50: 20, p90: 30, max: 30, mean: 20 });
  assert.equal(r.types.ka.shim, 1);
  assert.equal(r.types.ka.delivered, 0);
  assert.deepEqual(r.types.ka.bundleMs, { n: 0, p50: null, p90: null, max: null, mean: null }, "a keepalive never reaches the bundle");
  assert.equal(r.handoff.mode, "handler");
  assert.equal(r.handoff.delivered, 4);
  assert.equal(r.handoff.shim, 1);
  assert.equal(r.loaf.count, 1);
  assert.equal(r.loaf.blockingMs, 70);
  assert.equal(r.loaf.topScripts[0].key, "message handler (shim + bundle) <DOMWebSocket.onmessage>", "the wrapper's row is labelled for what runs inside it, not the instrument's file");
  assert.equal(r.loaf.topScripts[1].key, "(inline):(anonymous) <script feed>", "a URL-shaped invoker keeps its basename and loses its query");
  assert.equal(r.end.heapUsed, 4096);
  assert.equal(r.end.afterGc, true, "the heap figure is read after a forced GC: the run reported its collection returned");
  assert.equal(r.end.heapBeforeGc, 4608, "and the figure read before it stands beside");
  assert.equal(r.end.domElements, 250);
  assert.equal(r.end.cdpNodes, 750);
  assert.equal(r.frames.settleMissing, 0);
  assert.equal(r.frames.addListenerMessages, 0);
  assert.equal(r.frames.buildBannerRaised, 0);
  assert.equal(r.cpuProfile, undefined, "no profile unless one was taken");
  assert.equal(r.perFrame.length, 5, "a single run keeps the per-frame rows");
  assert.deepEqual(r.perFrame[1], { i: 1, type: "feed", bytes: 5000, at: 1, handlerMs: 40, handoff: "delivered", bundleMs: 38, settleMs: 90 });
  const text = renderReport(r);
  assert.match(text, /first content frame: feed, 4\.9 KB, handler 40\.0 ms, bundle 38\.0 ms, settled 90\.0 ms/);
  assert.match(text, /delta:feed\s+3\s+3\s+900 B\s+500 B\s+4\.0 \/ 6\.0 \/ 6\.0\s+3\.0 \/ 5\.0 \/ 5\.0\s+20\.0 \/ 30\.0 \/ 30\.0/, "count, delivered, bytes, max, handler, bundle, settle");
  assert.match(text, /ka\s+1\s+0\s+30 B\s+30 B\s+0\.1 \/ 0\.1 \/ 0\.1\s+-\s+5\.0/);
  assert.match(text, /handoff: the shim hands frames to the bundle inside the WebSocket handler, so the handler column includes the bundle's render/);
  assert.match(text, /1 frame answered by the shim alone/);
  assert.match(text, /console: 0 errors/);
  assert.match(text, /JS heap 4\.0 KB used of 8\.0 KB after a forced GC \(4\.5 KB before it\)/);
  assert.match(text, /cumulative since navigation .*3 layouts/, "the counters are labelled for what they are");
  assert.match(text, /entry point .*not the bundle function/);
  assert.doesNotMatch(text, /warning:/);
  assert.doesNotMatch(text, /secret-looking/, "no query string from a page URL reaches the report");
});

test("attribution never files a row under the instrument's own file: its wrappers are labelled for what runs inside them, its other entry points as its own bookkeeping", () => {
  // The instrument's settle stamp (a requestAnimationFrame callback reading the clock), its long-animation-frame
  // observer callback and its end-of-run collector appear as entries only when a descheduled main thread stretches
  // one past the threshold; the first two were each seen once on a loaded box, and the first form filed them as
  // `ui-bench-instrument.js:(anonymous)`, as if the instrument had done pane work (review find, 2026-09-08).
  const run = fakeRun({
    handoff: "handler", deliveries: 1,
    perFrame: [pf(0, "feed", 5000, 40, 90, "delivered", 38, 0)],
    loaf: [
      { start: 0, duration: 120, blocking: 70, scripts: [{ url: "ui-bench-instrument.js", fn: "rompBenchOnMessage", invoker: "DOMWebSocket.onmessage", duration: 100 }] },
      { start: 200, duration: 60, blocking: 10, scripts: [{ url: "ui-bench-instrument.js", fn: "", invoker: "FrameRequestCallback", duration: 55 }] },
      { start: 300, duration: 58, blocking: 8, scripts: [{ url: "ui-bench-instrument.js", fn: "", invoker: "PerformanceObserverCallback", duration: 52 }] },
      { start: 400, duration: 51, blocking: 1, scripts: [{ url: "ui-bench-instrument.js", fn: "collect", invoker: "?", duration: 50 }] },
    ],
  });
  const r = buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: true, iters: 1, browser: "t", runs: [run] });
  const keys = r.loaf.topScripts.map((s) => s.key);
  assert.ok(!keys.some((k) => /ui-bench-instrument/.test(k)), `no row under the instrument's file: ${keys.join(" | ")}`);
  assert.deepEqual(keys, [
    "message handler (shim + bundle) <DOMWebSocket.onmessage>",
    "instrument bookkeeping (the settle stamp's requestAnimationFrame; no pane work) <FrameRequestCallback>",
    "instrument bookkeeping (the long-animation-frame observer's callback; no pane work) <PerformanceObserverCallback>",
    "instrument bookkeeping (collect; no pane work) <?>",
  ]);
});

test("buildReport keeps the shim's handler and the bundle's delivery apart when the shim hands frames over in its flush task, and counts the frames the queue coalesced", () => {
  const run = fakeRun({
    handoff: "flush", flushes: 3, deliveries: 3, flushRows: [{ t0: 1101, ms: 121 }, { t0: 1401, ms: 41 }, { t0: 1601, ms: 6 }],
    deliveryRows: [{ t0: 1102, ms: 120, type: "feed" }, { t0: 1402, ms: 40, type: "feed" }, { t0: 1602, ms: 5, type: "session" }],
    // the full feed frame delivered on its own; the first delta coalesced into the second's delivery; a chained
    // session frame delivered; a keepalive the shim answered
    perFrame: [pf(0, "ka", 30, 0.1, 5, "shim"), pf(1, "feed", 5000, 2, 150, "delivered", 120, 0), pf(2, "delta:feed", 300, 0.5, 260, "coalesced", null, 1),
      pf(3, "delta:feed", 500, 0.6, 60, "delivered", 40, 1), pf(4, "session", 800, 0.2, 20, "delivered", 5, 2)],
    loaf: [{ start: 0, duration: 130, blocking: 80, scripts: [{ url: "ui-bench-instrument.js", fn: "rompBenchFlush", invoker: "MessagePort.onmessage", duration: 125 }] },
      { start: 200, duration: 60, blocking: 10, scripts: [{ url: "ui-bench-instrument.js", fn: "rompBenchOnMessage", invoker: "DOMWebSocket.onmessage", duration: 55 }] }],
  });
  const r = buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: false, gapMs: 100, iters: 1, browser: "t", runs: [run] });
  assert.equal(r.gapMs, 100);
  assert.deepEqual(r.handoff, { mode: "flush", flushes: 3, deliveries: 3, delivered: 3, coalesced: 1, shim: 1, queued: 0, unattributed: 0, unattributedMs: 0, queueDrained: true, deliveryErrors: [], deliverySeams: {} });
  assert.equal(r.types["delta:feed"].count, 2);
  assert.equal(r.types["delta:feed"].delivered, 1);
  assert.equal(r.types["delta:feed"].coalesced, 1);
  assert.deepEqual(r.types["delta:feed"].handlerMs, { n: 2, p50: 0.5, p90: 0.6, max: 0.6, mean: 0.6 }, "the handler column times every wire frame");
  assert.deepEqual(r.types["delta:feed"].bundleMs, { n: 1, p50: 40, p90: 40, max: 40, mean: 40 }, "the bundle column times the frames delivered on their own");
  assert.deepEqual(r.types["delta:feed"].settleMs, { n: 2, p50: 60, p90: 260, max: 260, mean: 160 }, "settle is end to end for both, the coalesced frame's running to the delivery that carried it");
  assert.deepEqual(r.types.session.bundleMs, { n: 1, p50: 5, p90: 5, max: 5, mean: 5 });
  assert.equal(r.first.bundleMs, 120);
  assert.equal(r.first.handoff, "delivered");
  assert.equal(r.loaf.topScripts[0].key, "bundle handoff (shim flush + bundle) <MessagePort.onmessage>", "the flush wrapper's row is labelled for what runs inside it");
  assert.equal(r.loaf.topScripts[1].key, "message handler (shim) <DOMWebSocket.onmessage>", "and the handler row no longer claims the bundle");
  const text = renderReport(r);
  assert.match(text, /replay 50\.0 ms \(100 ms gaps\)/);
  assert.match(text, /first content frame: feed, 4\.9 KB, handler 2\.0 ms, bundle 120 ms, settled 150 ms/);
  assert.match(text, /delta:feed\s+2\s+1\s+800 B\s+500 B\s+0\.5 \/ 0\.6 \/ 0\.6\s+40\.0 \/ 40\.0 \/ 40\.0\s+60\.0 \/ 260 \/ 260\s+\(1 coalesced\)/);
  assert.match(text, /handoff: the shim hands frames to the bundle in its own flush task \(3 tasks carrying 3 deliveries per run\)\. handler = the shim's synchronous work per wire frame/);
  assert.match(text, /1 frame coalesced into a newer frame of the same kind before delivery \(no bundle time of their own\); 1 answered by the shim alone/);
  assert.doesNotMatch(text, /warning:/);
  assert.doesNotMatch(text, /note: back-to-back/);
  // Pooled iterations: a frame delivered on its own in one run and coalesced in another says so.
  const other = fakeRun({ handoff: "flush", flushes: 1, deliveries: 1, perFrame: [pf(0, "ka", 30, 0.1, 5, "shim"), pf(1, "feed", 5000, 2, 200, "coalesced", null, 0), pf(2, "delta:feed", 300, 0.5, 100, "coalesced", null, 0),
    pf(3, "delta:feed", 500, 0.6, 30, "delivered", 150, 0), pf(4, "session", 800, 0.2, 20, "queued")] });
  const pooled = buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: true, iters: 2, browser: "t", runs: [run, other] });
  assert.equal(pooled.first.handoff, "mixed");
  assert.equal(pooled.first.deliveredRuns, 1);
  assert.equal(pooled.first.bundleMs, 120, "the mean over the runs that delivered it on their own");
  assert.equal(pooled.types.feed.delivered, 0.5);
  assert.equal(pooled.types.feed.coalesced, 0.5);
  assert.equal(pooled.handoff.queued, 0.5);
  const pt = renderReport(pooled);
  assert.match(pt, /bundle 120 ms \(delivered on its own in 1 of 2 runs, coalesced in the others\)/);
  assert.match(pt, /feed\s+1\s+0\.5\s+.*\(0\.5 coalesced\)/);
  assert.match(pt, /warning: 0\.5 frames were still queued in the shim when the run ended/);
  assert.match(pt, /note: back-to-back replay; frames queued together coalesce/);
  // No delivery seen at all: the report says so rather than claiming the handler column is shim plus bundle.
  const blind = renderReport(buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: true, iters: 1, browser: "t", runs: [fakeRun({ perFrame: [pf(0, "feed", 5000, 40, 90)] })] }));
  assert.match(blind, /handoff: no delivery to the bundle was observed/);
  assert.match(blind, /feed\s+1\s+0\s+4\.9 KB/, "no frame counted as delivered");
  // The seam each delivery came through is summed over the runs, and the fallback seam gets a note; a run whose
  // forced collection did not return says so on the heap line instead of claiming the GC.
  const seams = buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: true, iters: 2, browser: "t",
    runs: [fakeRun({ handoff: "flush", flushes: 1, deliveries: 2, deliverySeams: { inbound: 2 }, perFrame: [pf(0, "feed", 5000, 2, 150, "delivered", 120, 0)] }),
      fakeRun({ handoff: "flush", flushes: 1, deliveries: 2, deliverySeams: { inbound: 1, dispatch: 1 }, afterGc: false, perFrame: [pf(0, "feed", 5000, 2, 150, "delivered", 120, 0)] })] });
  assert.deepEqual(seams.handoff.deliverySeams, { inbound: 3, dispatch: 1 });
  assert.equal(seams.end.afterGc, false, "true only when every run's collection returned");
  const st = renderReport(seams);
  assert.match(st, /note: 1 delivery reached the pane through the MessageEvent fallback/);
  assert.match(st, /with NO forced GC/);
  assert.doesNotMatch(st, /after a forced GC/);
});

test("attributeDeliveries pairs the shim's deliveries with the wire frames they carried by the shim's own queue rules", () => {
  assert.equal(deliveredKind("delta:feed"), "feed", "a delta is reassembled into the whole slot it patches");
  assert.equal(deliveredKind("delta:bars"), "bars");
  assert.equal(deliveredKind("session"), "session");
  assert.ok(WHOLE_STATE_KINDS.has("feed") && WHOLE_STATE_KINDS.has("bars") && WHOLE_STATE_KINDS.has("data") && WHOLE_STATE_KINDS.has("tabOrder") && !WHOLE_STATE_KINDS.has("session"), "the shim's WHOLE set");
  const rec = (t0, handler, extra = {}) => ({ t0, handler, settle: 5, needSlot: false, ...extra });
  // The tip's shim: frames are enqueued in the handler and delivered in a later flush task.
  const types = ["feed", "delta:feed", "ka", "delta:feed", "delta:feed", "session", "delta:feed", "restarting", "feed"];
  const recs = [rec(0, 2), rec(150, 1), rec(200, 0.1), rec(300, 1), rec(400, 1), rec(500, 0.5), rec(600, 1, { needSlot: true }), rec(700, 0.2), rec(900, 1)];
  const flushes = [{ i: 0, t0: 3, ms: 101 }, { i: 1, t0: 152, ms: 21 }, { i: 2, t0: 402, ms: 26 }, { i: 3, t0: 501, ms: 6 }];
  const deliveries = [
    { i: 0, type: "feed", t0: 3, ms: 100, settle: 130, flush: 0, rec: -1 },
    { i: 1, type: "feed", t0: 152, ms: 20, settle: 40, flush: 1, rec: -1 },
    { i: 2, type: "feed", t0: 402, ms: 25, settle: 50, flush: 2, rec: -1 },      // frames 3 and 4 were both queued: one delivery
    { i: 3, type: "session", t0: 501, ms: 5, settle: 20, flush: 3, rec: -1 },
    { i: 4, type: "tabOrder", t0: 800, ms: 2, settle: 10, flush: -1, rec: -1 },   // the page re-emitting on its own: no frame behind it
  ];
  const a = attributeDeliveries({ types, recs, flushes, deliveries });
  assert.equal(a.handoff, "flush");
  assert.deepEqual(a.frames[0], { handoff: "delivered", bundleMs: 100, delivery: 0, settleMs: 133 }, "settle runs from the wire frame's receipt to the main thread free after its delivery");
  assert.deepEqual(a.frames[1], { handoff: "delivered", bundleMs: 20, delivery: 1, settleMs: 42 });
  assert.deepEqual(a.frames[2], { handoff: "shim", bundleMs: null, delivery: -1, settleMs: 5 }, "a keepalive is the shim's alone, its settle the handler's own");
  assert.deepEqual(a.frames[3], { handoff: "coalesced", bundleMs: null, delivery: 2, settleMs: 152 }, "the older of two queued deltas was replaced by the newer: no render of its own, its settle the shared delivery's");
  assert.deepEqual(a.frames[4], { handoff: "delivered", bundleMs: 25, delivery: 2, settleMs: 52 });
  assert.deepEqual(a.frames[5], { handoff: "delivered", bundleMs: 5, delivery: 3, settleMs: 21 }, "a chained kind is delivered one frame per delivery");
  assert.deepEqual(a.frames[6], { handoff: "shim", bundleMs: null, delivery: -1, settleMs: 5 }, "a delta the shim answered with needSlot never reached the bundle");
  assert.deepEqual(a.frames[7], { handoff: "shim", bundleMs: null, delivery: -1, settleMs: 5 }, "a restart notice is the shim's alone");
  assert.deepEqual(a.frames[8], { handoff: "queued", bundleMs: null, delivery: -1, settleMs: 5 }, "a frame with no flush after it was still queued when the run ended");
  assert.deepEqual(a.unattributed.map((d) => d.i), [4]);
  // Two chained frames queued before one flush: two deliveries, one frame each, in order; a whole-state rule
  // applied to them would coalesce the first and leave the second delivery unexplained.
  const chained = attributeDeliveries({ types: ["session", "session"], recs: [rec(0, 1), rec(10, 1)], flushes: [{ i: 0, t0: 12, ms: 10 }],
    deliveries: [{ i: 0, type: "session", t0: 12, ms: 4, settle: 9, flush: 0, rec: -1 }, { i: 1, type: "session", t0: 17, ms: 4, settle: 9, flush: 0, rec: -1 }] });
  assert.deepEqual(chained.frames.map((f) => [f.handoff, f.delivery]), [["delivered", 0], ["delivered", 1]]);
  assert.deepEqual(chained.unattributed, []);
  // A flush that started before a frame's handler finished cannot have carried it; the frame waits for the next.
  const late = attributeDeliveries({ types: ["feed", "feed"], recs: [rec(0, 2), rec(1, 5)], flushes: [{ i: 0, t0: 3, ms: 10 }, { i: 1, t0: 20, ms: 10 }],
    deliveries: [{ i: 0, type: "feed", t0: 3, ms: 9, settle: 15, flush: 0, rec: -1 }, { i: 1, type: "feed", t0: 20, ms: 9, settle: 15, flush: 1, rec: -1 }] });
  assert.deepEqual(late.frames.map((f) => [f.handoff, f.delivery]), [["delivered", 0], ["delivered", 1]]);
  // A shim from before the flush task delivers inside the handler: the delivery belongs to that handler's frame.
  const old = attributeDeliveries({ types: ["feed", "delta:feed", "ka"], recs: [rec(0, 50), rec(100, 10), rec(200, 0.1)], flushes: [],
    deliveries: [{ i: 0, type: "feed", t0: 1, ms: 48, settle: 70, flush: -1, rec: 0 }, { i: 1, type: "feed", t0: 101, ms: 9, settle: 30, flush: -1, rec: 1 }] });
  assert.equal(old.handoff, "handler");
  assert.deepEqual(old.frames[0], { handoff: "delivered", bundleMs: 48, delivery: 0, settleMs: 71 });
  assert.deepEqual(old.frames[1], { handoff: "delivered", bundleMs: 9, delivery: 1, settleMs: 31 });
  assert.deepEqual(old.frames[2], { handoff: "shim", bundleMs: null, delivery: -1, settleMs: 5 });
  assert.deepEqual(old.unattributed, []);
  // No deliveries at all: nothing is claimed.
  const none = attributeDeliveries({ types: ["feed"], recs: [rec(0, 50)], flushes: [], deliveries: [] });
  assert.equal(none.handoff, "none");
  assert.deepEqual(none.frames[0], { handoff: "queued", bundleMs: null, delivery: -1, settleMs: 5 });
  // A frame the page never dispatched has no record and no verdict.
  const missing = attributeDeliveries({ types: ["feed", "feed"], recs: [rec(0, 1)], flushes: [{ i: 0, t0: 2, ms: 5 }], deliveries: [{ i: 0, type: "feed", t0: 2, ms: 4, settle: 9, flush: 0, rec: -1 }] });
  assert.deepEqual(missing.frames[1], { handoff: null, bundleMs: null, delivery: -1, settleMs: null });
});

test("buildReport counts frames whose settle never stamped, extra message listeners and a raised banner, and renderReport warns", () => {
  const run = fakeRun({ perFrame: [pf(0, "feed", 5000, 40, 90), pf(1, "delta:feed", 300, 4, -1), pf(2, "delta:feed", 300, 5, 20), pf(3, "ka", 30, 0.1, -1)], addListenerMessages: 2, banner: "build" });
  const r = buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: true, iters: 1, browser: "t", runs: [run] });
  assert.equal(r.types["delta:feed"].settleMissing, 1);
  assert.equal(r.types["delta:feed"].measured, 2, "the handler was still timed");
  assert.deepEqual(r.types["delta:feed"].settleMs, { n: 1, p50: 20, p90: 20, max: 20, mean: 20 }, "settle percentiles cover the stamped frames only");
  assert.equal(r.types.ka.settleMissing, 1);
  assert.equal(r.frames.settleMissing, 2);
  assert.equal(r.frames.addListenerMessages, 2);
  assert.equal(r.frames.buildBannerRaised, 1);
  assert.equal(r.frames.connBannerRaised, 0);
  const text = renderReport(r);
  assert.match(text, /delta:feed .*\(1 settle missing\)/);
  assert.match(text, /warning: 2 frames never received a settle stamp/);
  assert.match(text, /warning: 2 message listeners were added with addEventListener/);
  assert.match(text, /warning: the page raised its "newer build" banner in 1 run/);
});

test("buildReport pools iterations: counts per run, percentiles over every sample", () => {
  const a = fakeRun({ perFrame: [pf(0, "feed", 100, 10, 20), pf(1, "ka", 10, 1, 2)] });
  const b = fakeRun({ perFrame: [pf(0, "feed", 100, 30, 40), pf(1, "ka", 10, 3, 4)] });
  const r = buildReport({ app: "feed", framesFile: "f", cpuThrottle: 2, fast: false, iters: 2, browser: "t", runs: [a, b] });
  assert.equal(r.iters, 2);
  assert.equal(r.types.feed.count, 1);
  assert.deepEqual(r.types.feed.handlerMs, { n: 2, p50: 10, p90: 30, max: 30, mean: 20 });
  assert.equal(r.first.handlerMs, 20, "the first frame's time is the mean over iterations");
  assert.equal(r.perFrame, undefined, "pooled runs carry no single per-frame list");
  assert.equal(r.cpuThrottle, 2);
});

test("compareReports subtracts B from A per type and for the totals", () => {
  const mk = (settle, loafBlocking, heap, errors, replayMs = 1000, bundle = settle * 0.4) => ({
    app: "feed", cpuThrottle: 1, fast: true, frames: { replayMs }, handoff: { mode: "flush" },
    first: { type: "feed", bytes: 5000, handlerMs: settle / 2, bundleMs: bundle, settleMs: settle },
    types: { feed: { count: 1, delivered: 1, bytes: 5000, handlerMs: { p50: settle / 2, p90: settle / 2, max: settle / 2 }, bundleMs: { p50: bundle, p90: bundle, max: bundle }, settleMs: { p50: settle, p90: settle, max: settle } },
      ka: { count: 3, delivered: 0, bytes: 90, handlerMs: { p50: 0.1, p90: 0.2, max: 0.2 }, bundleMs: { n: 0, p50: null, p90: null, max: null }, settleMs: { p50: 1, p90: 2, max: 2 } } },
    loaf: { count: 2, durationMs: 300, blockingMs: loafBlocking, maxMs: 200 },
    end: { heapUsed: heap, domElements: 100, layoutCount: 5, scriptMs: 50, taskMs: 80 },
    console: { errors },
  });
  const A = mk(200, 100, 4000, []);
  const B = mk(150, 60, 5000, ["boom"]);
  B.types["delta:feed"] = { count: 4, delivered: 3, bytes: 400, handlerMs: { p50: 1, p90: 2, max: 3 }, bundleMs: { p50: 4, p90: 5, max: 6 }, settleMs: { p50: 5, p90: 6, max: 7 } };
  const c = compareReports(A, B);
  assert.deepEqual(c.types.feed.settleP50, { a: 200, b: 150, diff: -50, pct: -25 });
  assert.deepEqual(c.types.feed.handlerP50, { a: 100, b: 75, diff: -25, pct: -25 });
  assert.deepEqual(c.types.feed.bundleP50, { a: 80, b: 60, diff: -20, pct: -25 });
  assert.deepEqual(c.types.feed.delivered, { a: 1, b: 1, diff: 0, pct: 0 });
  assert.deepEqual(c.types.ka.bundleP50, { a: null, b: null, diff: null, pct: null }, "a kind the bundle never sees has no bundle time to compare");
  assert.deepEqual(c.first.settleMs, { a: 200, b: 150, diff: -50, pct: -25 });
  assert.deepEqual(c.first.bundleMs, { a: 80, b: 60, diff: -20, pct: -25 });
  assert.deepEqual(c.loaf.blockingMs, { a: 100, b: 60, diff: -40, pct: -40 });
  assert.deepEqual(c.end.heapUsed, { a: 4000, b: 5000, diff: 1000, pct: 25 });
  assert.deepEqual(c.types["delta:feed"].count, { a: null, b: 4, diff: null, pct: null }, "a type present on one side only compares to nothing");
  assert.deepEqual(c.console.errors, [0, 1]);
  assert.deepEqual(c.replayMs, [1000, 1000]);
  assert.deepEqual(c.handoff, ["flush", "flush"]);
  assert.equal(c.endComparable, true);
  const text = renderCompare(c);
  assert.match(text, /feed\s+count 1 → 1 \(unchanged\) \(delivered 1 → 1 \(unchanged\)\); settle p50 200 → 150 ms \(-50, -25%\)/);
  assert.match(text, /handler p50 100 → 75 ms \(-25, -25%\); bundle p50 80 → 60 ms \(-20, -25%\)/);
  assert.match(text, /^ka\s+count 3 → 3 \(unchanged\) \(delivered 0 → 0 \(unchanged\)\); settle .*handler p50 0\.1 → 0\.1 ms \(unchanged\)$/m, "no bundle column for a kind the bundle never sees");
  assert.match(text, /first content frame: bytes 5000 → 5000 \(unchanged\); handler 100 → 75 ms \(-25, -25%\); bundle 80 → 60 ms \(-20, -25%\)/);
  assert.match(text, /delta:feed\s+count - → 4 \(delivered - → 3\);/);
  assert.doesNotMatch(text, /shims differ/);
  const older = renderCompare(compareReports({ ...A, handoff: { mode: "handler" } }, B));
  assert.match(older, /the shims differ in how they hand frames to the bundle \(handler → flush\)/, "a shim that renders inside the handler is not compared on the handler column");
  const legacy = compareReports({ ...A, handoff: undefined, first: { type: "feed", bytes: 5000, handlerMs: 100, settleMs: 200 }, types: { feed: { count: 1, bytes: 5000, handlerMs: { p50: 100 }, settleMs: { p50: 200 } } } }, B);
  assert.deepEqual(legacy.types.feed.bundleP50, { a: null, b: 60, diff: null, pct: null }, "a report from before the bundle column compares to nothing there");
  assert.match(renderCompare(legacy), /bundle - → 60 ms/);
  assert.match(text, /heap 4000 → 5000 B \(\+1000, \+25%\)/);
  assert.match(text, /console errors: 0 → 1/);
  A.end.layoutCount = 100; B.end.layoutCount = 50;
  assert.match(renderCompare(compareReports(A, B)), /layouts 100 → 50 \(-50, -50%\)/, "runs of the same pacing and length compare with percentages");
  assert.doesNotMatch(renderCompare(compareReports(A, B)), /carry no percentage/);
  // A paced run against a fast one, or runs of different length: the cumulative counters lose their percentages.
  const longer = mk(150, 60, 5000, [], 5000); longer.end.layoutCount = 50;
  const cl = compareReports(A, longer);
  assert.equal(cl.endComparable, false);
  const tl = renderCompare(cl);
  assert.match(tl, /layouts 100 → 50 \(-50\); script/);
  assert.match(tl, /cumulative since navigation and the runs differ in pacing or length \(replay 1000 → 5000 ms\)/);
  assert.match(tl, /heap 4000 → 5000 B \(\+1000, \+25%\)/, "the heap is a state, not a cumulative counter, so it keeps its percentage");
  const paced = mk(150, 60, 5000, [], 1000); paced.fast = false;
  assert.equal(compareReports(A, paced).endComparable, false);
  assert.equal(compareReports(A, mk(150, 60, 5000, [], 1200)).endComparable, true, "a fifth is within tolerance");
  assert.equal(compareReports({ ...A, frames: undefined }, B).endComparable, false, "an older report without replayMs is not assumed comparable");
});

// ── the path guard ───────────────────────────────────────────────────────────────────────────────

test("assertTmpPath allows /tmp, refuses the repo, other roots, and a checkout under /tmp", () => {
  const ok = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-guard-"));
  try {
    assert.equal(assertTmpPath(path.join(ok, "frames.jsonl")), path.join(ok, "frames.jsonl"));
    assert.equal(assertTmpPath(path.join(ok, "deeper", "frames.jsonl")), path.join(ok, "deeper", "frames.jsonl"), "a not-yet-created leaf directory is fine");
    assert.equal(assertTmpPath(path.join(ok, "a", "b", "c", "frames.jsonl")), path.join(ok, "a", "b", "c", "frames.jsonl"), "several missing directories are fine");
    assert.throws(() => assertTmpPath(path.join(REPO, "frames.jsonl")), /outside \/tmp/);
    assert.throws(() => assertTmpPath("/var/tmp/frames.jsonl"), /outside \/tmp/);
    assert.throws(() => assertTmpPath("frames.jsonl"), /outside \/tmp/, "a relative path resolves against the cwd, which is not /tmp here");
    fs.mkdirSync(path.join(ok, "clone", ".git"), { recursive: true });
    assert.throws(() => assertTmpPath(path.join(ok, "clone", "sub", "frames.jsonl")), /inside a git checkout/);
  } finally {
    fs.rmSync(ok, { recursive: true, force: true });
  }
});

test("assertTmpPath resolves symlinks on both sides: a root that is a link (macOS's /tmp) and an ancestor that is one", () => {
  // The macOS layout in miniature: <base>/tmp -> <base>/private/tmp, and $TMPDIR under <base>/private/var.
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-mac-"));
  try {
    fs.mkdirSync(path.join(base, "private", "tmp"), { recursive: true });
    fs.mkdirSync(path.join(base, "private", "var", "folders", "T"), { recursive: true });
    fs.symlinkSync(path.join(base, "private", "tmp"), path.join(base, "tmp"));
    fs.symlinkSync(path.join(base, "private", "var"), path.join(base, "var"));
    const roots = [path.join(base, "tmp"), path.join(base, "var", "folders", "T")];
    const fresh = path.join(base, "tmp", "romp-perf", "frames.jsonl");
    assert.equal(assertTmpPath(fresh, { roots }), fresh, "first run: the directory does not exist yet");
    fs.mkdirSync(path.join(base, "tmp", "romp-perf"));
    assert.equal(assertTmpPath(fresh, { roots }), fresh, "second run: the directory exists and resolves through the link");
    assert.equal(assertTmpPath(path.join(base, "private", "tmp", "romp-perf", "x.jsonl"), { roots }), path.join(base, "private", "tmp", "romp-perf", "x.jsonl"), "the resolved form of the root is accepted too");
    assert.equal(assertTmpPath(path.join(base, "var", "folders", "T", "x.jsonl"), { roots }), path.join(base, "var", "folders", "T", "x.jsonl"), "a $TMPDIR path");
    assert.throws(() => assertTmpPath(path.join(base, "private", "var", "elsewhere", "x.jsonl"), { roots }), /outside \/tmp/, "a sibling of the roots is still refused");
    assert.throws(() => assertTmpPath(path.join(base, "x.jsonl"), { roots }), /outside \/tmp/);
    // An ancestor link that points OUT of /tmp: the resolved parent decides.
    fs.mkdirSync(path.join(base, "private", "home", "sub"), { recursive: true });
    fs.symlinkSync(path.join(base, "private", "home"), path.join(base, "tmp", "away"));
    assert.throws(() => assertTmpPath(path.join(base, "tmp", "away", "sub", "x.jsonl"), { roots }), /outside \/tmp/, "a link under /tmp that leaves it is refused");
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test("assertTmpPath refuses a symlink at the leaf, and writeFrames will not write through one", () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-leaf-"));
  try {
    fs.mkdirSync(path.join(base, "fakeco", ".git"), { recursive: true });
    fs.mkdirSync(path.join(base, "fakeco", "sub"));
    const leaf = path.join(base, "leaf.jsonl");
    fs.symlinkSync(path.join(base, "fakeco", "sub", "target.jsonl"), leaf);
    assert.throws(() => assertTmpPath(leaf), /through a symlink/);
    assert.throws(() => writeFrames(leaf, { synthetic: true }, synthesizeFrames("feed", 2)), /ELOOP|EMLINK|symlink/i, "O_NOFOLLOW refuses the open");
    assert.ok(!fs.existsSync(path.join(base, "fakeco", "sub", "target.jsonl")), "nothing landed in the checkout");
    const dangling = path.join(base, "dangling.jsonl");
    fs.symlinkSync(path.join(base, "nowhere", "x.jsonl"), dangling);
    assert.throws(() => assertTmpPath(dangling), /through a symlink/, "a dangling link is a link");
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

// ── the synthesizer ──────────────────────────────────────────────────────────────────────────────

test("synthesizeFrames: the feed stream is the keyed full frame, then feed deltas and keepalives, byte-stable", () => {
  const frames = synthesizeFrames("feed", 30);
  const types = frames.map((f) => classifyFrame(f.data));
  assert.equal(types[0], "feed");
  const full = JSON.parse(frames[0].data);
  assert.equal(full.asks.length, 30);
  assert.ok(full.asks.every((a) => a.itemId && a.sid && a.name && a.column && Array.isArray(a.tree) && a.tree.length), "every card has an id, a session and a tree");
  assert.ok(full.asks.every((a) => /^11111111-2222-3333-4444-5555555555\d\d$/.test(a.sid)), "placeholder uuids only");
  assert.ok(full.asks.every((a) => Array.isArray(a.trgb) && a.trgb.length === 3 && a.trgb.every((x) => Number.isInteger(x) && x >= 0 && x <= 255)), "every card carries its recency tint (feed.ts destructures it)");
  assert.ok(new Set(full.asks.map((a) => JSON.stringify(a.trgb))).size > 1, "older cards are tinted differently from recent ones");
  assert.deepEqual(full._keys, { asks: full.asks.map((a) => a.itemId) }, "the keyed full carries the kernel's key list, in payload order");
  assert.deepEqual(full._keys, feedKeys(full));
  for (const k of ["views", "judgeLimit", "working", "awaiting", "stateUnknown", "bgServices", "dismissedCount", "showDismissed", "order", "sessions", "clearNotices", "sdkNotices", "syncNotices", "selfHost", "canUndoClear", "buildId"]) assert.ok(k in full, `build_feed's ${k}`);
  assert.equal(full.selfHost, "TESTHOST");
  assert.ok(!("ledgers" in full), "the feed page's frame carries no ledgers");
  assert.ok(types.includes("delta:feed") && types.includes("ka"));
  assert.equal(types.filter((t) => t === "feed").length, 1);
  let rev = 0, prevBuild = full.buildId, withDel = 0, withAll = 0, withOrder = 0;
  for (const f of frames) {
    const m = JSON.parse(f.data);
    if (m.type !== "delta") continue;
    assert.equal(m.slot, "feed");
    assert.equal(m.base, rev, "each delta's base is the revision the pane holds");
    assert.equal(m.rev, rev + 1); rev = m.rev;
    assert.ok(!("asks" in m) && !("_keys" in m), "a delta carries its cards under coll, never at the top level");
    const asks = m.coll.asks;
    assert.ok(asks.set && Object.keys(asks.set).length >= 1, "every delta sets at least one card");
    for (const [k, card] of Object.entries(asks.set)) { assert.equal(k, card.itemId, "cards are keyed by itemId"); assert.equal(card.trgb.length, 3); }
    if (asks.del) { withDel++; assert.ok(asks.del.every((k) => typeof k === "string")); }
    if (asks.order) { withOrder++; assert.ok(asks.order.every((k) => typeof k === "string")); }
    assert.equal(typeof m.rest.now, "number", "the clock rides every delta");
    assert.ok(m.rest.buildId > prevBuild, "buildId advances on every delta"); prevBuild = m.rest.buildId;
    if (m.restAll) {
      withAll++;
      assert.equal(m.rest.type, "feed", "the whole remainder carries the frame type (the shim drops every key it does not name)");
      assert.ok(Array.isArray(m.rest.working) && "canUndoClear" in m.rest && !("asks" in m.rest), "the whole non-keyed remainder");
    } else {
      assert.deepEqual(Object.keys(m.rest).sort(), ["buildId", "now"], "an unchanged remainder sends only the clock fields");
    }
  }
  assert.ok(rev >= 20);
  assert.ok(withDel >= 1, "some deltas retire a card");
  assert.ok(withAll >= 1, "some deltas carry the whole remainder");
  assert.ok(withOrder >= 1, "some deltas reorder the cards");
  assert.ok(frames.every((f, i) => i === 0 || f.t >= frames[i - 1].t), "timestamps are monotonic");
  assert.deepEqual(synthesizeFrames("feed", 30), frames, "the same seed gives the same bytes");
  assert.notDeepEqual(synthesizeFrames("feed", 30, { seed: 8 }), frames, "another seed gives another stream");
  assert.deepEqual(Object.keys(streamSummary(frames).byType).sort(), ["delta:feed", "feed", "ka"]);
});

test("synthesizeFrames: the Outline stream carries ledgers in the full frame and re-sends them with the whole remainder", () => {
  const frames = synthesizeFrames("fleet", 12);
  const outlineFull = JSON.parse(frames[0].data);
  assert.equal(outlineFull.ledgers.length, 3);
  assert.ok(outlineFull.ledgers.every((l) => l.sid && l.name && l.ledger && Array.isArray(l.ledger.tree)));
  assert.deepEqual(outlineFull._keys, feedKeys(outlineFull));
  const withLedgers = frames.map((f) => JSON.parse(f.data)).filter((m) => m.type === "delta" && Array.isArray(m.rest.ledgers));
  assert.ok(withLedgers.length >= 1, "some deltas carry the ledgers");
  assert.ok(withLedgers.every((m) => m.restAll === 1 && m.rest.type === "feed"), "the ledgers are non-keyed remainder, so they ride only with the whole remainder");
  assert.ok(withLedgers.every((m) => m.rest.ledgers.length === 3), "the whole ledgers list, not one session's");
});

test("synthesizeFrames: the timeline stream is the skeleton, the keyed bars slot, then bar-level deltas", () => {
  const frames = synthesizeFrames("timeline", 30);
  const types = frames.map((f) => classifyFrame(f.data));
  assert.equal(types[0], "data");
  assert.equal(types[1], "bars");
  const skel = JSON.parse(frames[0].data);
  assert.equal(skel.data.type, "timeline");
  assert.equal(skel.data.sessions.length, 3);
  assert.ok(skel.data.sessions.every((s) => s.id && s.name && typeof s.live === "boolean" && s.color), "lanes carry id, name, live and color");
  assert.deepEqual(skel.data.turns, {}, "the skeleton carries no bars");
  const bars = JSON.parse(frames[1].data);
  assert.equal(Object.keys(bars.turns).length, 3);
  assert.equal(Object.values(bars.turns).reduce((a, lane) => a + lane.length, 0), 30);
  assert.ok(bars._keys, "a keyed full frame carries the kernel's key list");
  assert.deepEqual(bars._keys, barsKeys(bars));
  assert.ok(bars._keys.turns.every((k) => k.includes(DELTA_SEP)), "a bar's key is lane + separator + id");
  assert.equal(bars._keys.turns.length, 30);
  assert.ok(bars._keys.judging.every((k) => k.split(DELTA_SEP).length === 4), "a judging key is sid, t, judge, t1");
  let rev = 0;
  for (const f of frames) {
    const m = JSON.parse(f.data);
    if (m.type !== "delta") continue;
    assert.equal(m.slot, "bars");
    assert.equal(m.base, rev, "each delta's base is the revision the pane holds");
    assert.equal(m.rev, rev + 1); rev = m.rev;
    assert.ok(m.coll.turns.set && Object.keys(m.coll.turns.set).every((k) => k.includes(DELTA_SEP)));
    assert.equal(typeof m.rest.now, "number", "the clock rides every delta");
  }
  assert.ok(rev >= 20);
  assert.ok(types.filter((t) => t === "data").length >= 2, "the skeleton is re-pushed during the stream");
  assert.deepEqual(Object.keys(streamSummary(frames).byType).sort(), ["bars", "data", "delta:bars", "ka"]);
});

test("barsKeys mints the kernel's keys: an empty lane is its bare prefix, messages key by id", () => {
  const keys = barsKeys({ turns: { a: [], b: [{ id: "x" }, { id: 7 }] }, judging: [{ sid: "s", t: 1, judge: "closer", t1: 2 }], messages: [{ id: "m1" }] });
  assert.deepEqual(keys, { turns: ["a" + DELTA_SEP, "b" + DELTA_SEP + "x", "b" + DELTA_SEP + "7"], judging: ["s" + DELTA_SEP + "1" + DELTA_SEP + "closer" + DELTA_SEP + "2"], messages: ["m1"] });
});

test("synthesizeFrames refuses the chat, which it cannot fake, and unknown apps", () => {
  assert.throws(() => synthesizeFrames("chat", 5), /not synthesized .*build_session/);
  assert.throws(() => synthesizeFrames("board", 5), /unknown app/);
  assert.throws(() => synthesizeFrames("waiting", 5), /unknown app/, "no such pane");
  assert.deepEqual(APPS, ["feed", "fleet", "chat", "timeline"]);
});

test("writeFrames and loadFrames round-trip a stream with its meta row", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-rt-"));
  try {
    const frames = synthesizeFrames("feed", 4);
    const file = path.join(tmp, "nested", "s.jsonl");
    writeFrames(file, { synthetic: true, app: "feed" }, frames);
    const back = loadFrames(file);
    assert.deepEqual(back.meta, { synthetic: true, app: "feed" });
    assert.deepEqual(back.frames, frames.map((f) => ({ t: f.t, data: f.data })));
    const rows = fs.readFileSync(file, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    assert.equal(rows[1].bytes, Buffer.byteLength(frames[0].data, "utf8"), "each row records its byte length");
    if (process.platform !== "win32") {
      assert.equal(mode(file), 0o600, "a recording is private to the user");
      assert.equal(mode(path.join(tmp, "nested")), 0o700, "and so is a directory it creates");
      const shared = path.join(tmp, "shared.jsonl");
      fs.writeFileSync(shared, "old\n", { mode: 0o644 });
      writeFrames(shared, { synthetic: true }, frames);
      assert.equal(mode(shared), 0o600, "an overwritten file is made private too");
      assert.deepEqual(loadFrames(shared).frames.length, frames.length, "and truncated first");
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ── the --record client, against a local WebSocket server ────────────────────────────────────────

/** A loopback WebSocket server standing in for the kernel: records the upgrade request and every client
 *  message, and runs `onReady(ws)` when the client's handshake arrives. */
async function fakeKernel(onReady) {
  const { WebSocketServer } = requireExt("ws");
  const server = http.createServer((_req, res) => { res.writeHead(404); res.end(); });
  const wss = new WebSocketServer({ server });
  const seen = { requests: [], messages: [] };
  wss.on("connection", (ws, req) => {
    seen.requests.push({ url: req.url, headers: req.headers });
    ws.on("message", (d) => { seen.messages.push(d.toString()); if (JSON.parse(d.toString()).type === "ready") onReady(ws); });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { port: server.address().port, seen, close: () => { for (const c of wss.clients) c.terminate(); server.close(); } };
}

test("recordFrames connects as the page does, sends only the ready handshake, and writes the frames as JSONL", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-rec-"));
  const token = "synthetic-serve-token-for-the-test";
  const pushed = [JSON.stringify({ type: "feed", asks: [], now: 1_760_000_000, buildId: 1 }), JSON.stringify({ type: "ka", dv: 1_760_000_000 })];
  const k = await fakeKernel((ws) => { for (const f of pushed) ws.send(f); });
  try {
    fs.mkdirSync(path.join(tmp, "state"));
    fs.writeFileSync(path.join(tmp, "state", "serve-token"), token + "\n");
    const out = path.join(tmp, "rec", "frames.jsonl");
    const res = await withEnv({ ROMP_STATE_DIR: path.join(tmp, "state") }, () => recordFrames({ app: "feed", seconds: 0.5, out, port: k.port, log: () => {} }));
    assert.equal(res.frames, 2);
    assert.equal(k.seen.requests.length, 1, "one connection");
    const u = new URL(k.seen.requests[0].url, "http://127.0.0.1");
    assert.equal(u.pathname, "/ws");
    assert.equal(u.searchParams.get("app"), "feed");
    assert.equal(u.searchParams.get("delta"), "1");
    assert.equal(u.searchParams.get("caps"), null, "the shim's query and nothing more");
    assert.match(u.searchParams.get("iid"), /^[0-9a-f-]{36}$/);
    assert.equal(u.searchParams.get("token"), null, "no token in the query");
    const h = k.seen.requests[0].headers;
    assert.equal(h.cookie, `romp_token=${token}`, "the browser's credential: the cookie");
    assert.equal(h.origin, `http://127.0.0.1:${k.port}`, "with a same-origin Origin");
    assert.equal(h["x-romp-token"], undefined, "and no header token");
    assert.deepEqual(k.seen.messages.map((m) => JSON.parse(m)), [{ type: "ready" }], "the ready handshake and nothing else");
    const back = loadFrames(out);
    assert.equal(back.meta.mode, "record");
    assert.equal(back.meta.app, "feed");
    assert.equal(back.meta.frames, 2);
    assert.equal(back.meta.port, k.port);
    assert.ok(back.meta.events.some((e) => e.event === "open"));
    assert.deepEqual(back.frames.map((f) => f.data), pushed, "the frames round-trip byte for byte");
    assert.ok(back.frames.every((f) => f.t >= Date.parse(back.meta.startedAt)), "receive timestamps");
    if (process.platform !== "win32") assert.equal(mode(out), 0o600);
  } finally {
    k.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("recordFrames is loud when the kernel closes early or refuses the upgrade, and still writes what it got", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-rec2-"));
  const k = await fakeKernel((ws) => { ws.send(JSON.stringify({ type: "ka", dv: 1 })); setTimeout(() => ws.close(1012, "restarting"), 50); });
  const refusing = http.createServer((_req, res) => { res.writeHead(404); res.end(); });
  refusing.on("upgrade", (_req, socket) => { socket.end("HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: 9\r\nConnection: close\r\n\r\nforbidden"); });
  await new Promise((r) => refusing.listen(0, "127.0.0.1", r));
  try {
    fs.mkdirSync(path.join(tmp, "state"));
    fs.writeFileSync(path.join(tmp, "state", "serve-token"), "t\n");
    await withEnv({ ROMP_STATE_DIR: path.join(tmp, "state") }, async () => {
      const early = path.join(tmp, "early.jsonl");
      await assert.rejects(recordFrames({ app: "timeline", seconds: 5, out: early, port: k.port, log: () => {} }), /closed the socket early \(code 1012\) after 1 frames/);
      const back = loadFrames(early);
      assert.equal(back.frames.length, 1, "the frame received before the close was written");
      assert.ok(back.meta.events.some((e) => e.event === "close" && e.code === 1012));
      await assert.rejects(recordFrames({ app: "feed", seconds: 5, out: path.join(tmp, "refused.jsonl"), port: refusing.address().port, log: () => {} }), /refused the WebSocket: HTTP 403 forbidden/);
      await assert.rejects(recordFrames({ app: "board", seconds: 1, out: path.join(tmp, "x.jsonl"), port: 1, log: () => {} }), /unknown app/);
      await assert.rejects(recordFrames({ app: "feed", seconds: 1, out: path.join(REPO, "x.jsonl"), port: 1, log: () => {} }), /outside \/tmp/, "the path guard runs before any network use");
    });
    assert.equal(process.listenerCount("SIGINT"), 0, "no interrupt listener outlives a recording");
  } finally {
    k.close();
    refusing.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ── the CPU-profile fold ─────────────────────────────────────────────────────────────────────────

/** A tiny .cpuprofile: main -> render -> fmt, with one recursive render call, beside (program) and GC. */
const SYNTH_PROFILE = (() => {
  const cf = (functionName, lineNumber, url = "http://127.0.0.1:1/dist/feed.js?v=3") => ({ functionName, url, lineNumber, columnNumber: 0, scriptId: "7" });
  return {
    nodes: [
      { id: 1, callFrame: cf("(root)", -1, ""), children: [2, 3, 6] },
      { id: 2, callFrame: cf("(program)", -1, "") },
      { id: 3, callFrame: cf("main", 9), children: [4], hitCount: 1 },
      // V8's per-line ticks: the first render node's two samples fell on bundle lines 20 and 25, the recursive one's on 25.
      { id: 4, callFrame: cf("render", 19), children: [5, 7], hitCount: 2, positionTicks: [{ line: 20, ticks: 1 }, { line: 25, ticks: 1 }] },
      { id: 5, callFrame: cf("fmt", 29), hitCount: 2 },
      { id: 6, callFrame: cf("(garbage collector)", -1, "") },
      { id: 7, callFrame: cf("render", 19), hitCount: 1, positionTicks: [{ line: 25, ticks: 1 }] },
    ],
    startTime: 1000, endTime: 1900,
    samples: [3, 4, 5, 5, 7, 2, 6, 4],
    timeDeltas: [100, 100, 100, 100, 100, 100, 100, 100],
  };
})();

test("frameKey labels a call frame url-basename:function:line, 1-based, and keeps V8's bookkeeping names", () => {
  assert.equal(frameKey({ functionName: "render", url: "http://127.0.0.1:1/dist/feed.js?v=3", lineNumber: 19 }), "feed.js:render:20");
  assert.equal(frameKey({ functionName: "", url: "http://127.0.0.1:1/dist/feed.js", lineNumber: 0 }), "feed.js:(anonymous):1");
  assert.equal(frameKey({ functionName: "tick", url: "", lineNumber: 4 }), "(inline):tick:5", "code without a url but with a line: an eval");
  assert.equal(frameKey({ functionName: "getBoundingClientRect", url: "", lineNumber: -1 }), "(native):getBoundingClientRect", "a builtin has neither");
  assert.equal(frameKey({ functionName: "ws.onmessage", url: "http://127.0.0.1:1/feed?token=secret-looking", lineNumber: 135 }), "feed:ws.onmessage:136", "the page's inline shim, query dropped");
  assert.equal(frameKey({ functionName: "(program)", url: "", lineNumber: -1 }), "(program)");
  assert.equal(frameKey({ functionName: "(garbage collector)", url: "", lineNumber: -1 }), "(garbage collector)");
  assert.equal(frameKey(null), "(unknown)");
});

test("aggregateProfile attributes each sample's interval as self time to its node and once as total time to every function on its stack", () => {
  const a = aggregateProfile(SYNTH_PROFILE);
  assert.equal(a.durationMs, 0.9);
  assert.equal(a.sampledMs, 0.8, "eight samples of 100 us, the last owning the interval to endTime");
  assert.equal(a.samples, 8);
  assert.deepEqual(a.meta, { "(program)": 0.1, "(garbage collector)": 0.1 }, "bookkeeping nodes are totalled, not ranked");
  const by = Object.fromEntries(a.functions.map((f) => [f.key, { key: f.key, selfMs: f.selfMs, totalMs: f.totalMs, samples: f.samples }]));
  assert.deepEqual(Object.keys(by).sort(), ["feed.js:fmt:30", "feed.js:main:10", "feed.js:render:20"]);
  assert.equal(a.functions.find((f) => f.key === "feed.js:render:20").cf.lineNumber, 19, "each function keeps a call frame for the source locator");
  assert.deepEqual(a.functions.find((f) => f.key === "feed.js:render:20").lines, [{ line: 25, ms: 0.2 }, { line: 20, ms: 0.1 }], "self time split over the function's lines by V8's ticks, both render nodes pooled");
  assert.equal(a.functions.find((f) => f.key === "feed.js:fmt:30").lines, undefined, "no ticks, no lines");
  assert.deepEqual(by["feed.js:render:20"], { key: "feed.js:render:20", selfMs: 0.3, totalMs: 0.5, samples: 3 }, "the recursive call's sample counts once in total");
  assert.deepEqual(by["feed.js:fmt:30"], { key: "feed.js:fmt:30", selfMs: 0.2, totalMs: 0.2, samples: 2 });
  assert.deepEqual(by["feed.js:main:10"], { key: "feed.js:main:10", selfMs: 0.1, totalMs: 0.6, samples: 1 });
  const ranked = rankProfile(a, 2);
  assert.deepEqual(ranked.topSelf.map((f) => f.key), ["feed.js:render:20", "feed.js:fmt:30"]);
  assert.deepEqual(ranked.topSelf[0].lines, [{ line: 25, ms: 0.2, share: 0.67 }, { line: 20, ms: 0.1, share: 0.33 }], "the hottest functions carry their lines");
  assert.equal(ranked.topTotal[1].lines, undefined, "the total ranking does not");
  assert.deepEqual(ranked.topTotal.map((f) => f.key), ["feed.js:main:10", "feed.js:render:20"]);
  assert.equal(ranked.functions, 3);
  // A window on the profile's clock: samples at 1300 and 1400 (both fmt under render under main).
  const w = aggregateProfile(SYNTH_PROFILE, [1250, 1450]);
  assert.ok(w.functions.every((f) => f.lines === undefined), "ticks cover the whole profile, so a window gets no lines");
  assert.equal(w.durationMs, 0.2, "a window's duration is its own width, not the profile's");
  assert.equal(w.samples, 2);
  assert.equal(w.sampledMs, 0.2);
  assert.deepEqual(w.meta, {});
  assert.deepEqual(Object.fromEntries(w.functions.map((f) => [f.key, [f.selfMs, f.totalMs]])), { "feed.js:fmt:30": [0.2, 0.2], "feed.js:render:20": [0, 0.2], "feed.js:main:10": [0, 0.2] });
  const merged = rankProfile(mergeAggregates([a, a]), 3);
  assert.equal(merged.sampledMs, 1.6);
  assert.equal(merged.topSelf[0].selfMs, 0.6, "iterations pool by summing");
  assert.deepEqual(merged.topSelf[0].lines, [{ line: 25, ms: 0.4, share: 0.67 }, { line: 20, ms: 0.2, share: 0.33 }], "and so do the lines");
  assert.deepEqual(merged.meta, { "(program)": 0.2, "(garbage collector)": 0.2 });
  const text = renderProfile({ files: ["/tmp/x.cpuprofile"], samplingIntervalUs: 500, alignMs: 0.4, ...ranked, windows: [{ label: "first content frame", index: 0, type: "feed", bytes: 5000, handlerMs: 0.3, ...rankProfile(w, 2) }] });
  assert.match(text, /cpu profile: 8 samples over 0\.9 ms at 500 us, 3 functions; bookkeeping: \(program\) 0\.1 ms, \(garbage collector\) 0\.1 ms/);
  assert.match(text, /written: \/tmp\/x\.cpuprofile/);
  assert.match(text, /top 2 by self time \(under a function, the lines[^\n]*\n\s+self ms\s+total ms\s+samples\s+url:function:line\n\s+0\.3\s+0\.5\s+3\s+feed\.js:render:20\n\s+67%\s+0\.2 ms  line 25\n\s+33%\s+0\.1 ms  line 20\n/);
  assert.match(text, /top 2 by total time\n[^\n]*\n\s+0\.1\s+0\.6\s+1\s+feed\.js:main:10/);
  assert.match(text, /window: first content frame \(feed, 4\.9 KB, frame 0\): handler 0\.3 ms, 2 samples/);
  assert.doesNotMatch(text, /source:line/, "no source column without maps");
});

test("sourceLocator maps a bundle position to its source through the dist's .map file, and rankProfile carries it", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-map-"));
  try {
    // A two-line bundle whose map says: generated line 1 <- src/a.ts line 1, generated line 2 <- src/a.ts line 2 ("AAAA;AACA").
    fs.writeFileSync(path.join(tmp, "feed.js.map"), JSON.stringify({ version: 3, sources: ["../src/a.ts"], names: [], mappings: "AAAA;AACA" }));
    const locate = sourceLocator(tmp);
    assert.equal(locate({ functionName: "f", url: "http://127.0.0.1:1/dist/feed.js?v=9", lineNumber: 1, columnNumber: 4 }), "src/a.ts:2", "the leading ../ segments go when the source lies outside the repo");
    assert.equal(locate({ functionName: "f", url: "http://127.0.0.1:1/dist/feed.js", lineNumber: 0, columnNumber: 0 }), "src/a.ts:1");
    assert.equal(locate({ functionName: "g", url: "http://127.0.0.1:1/dist/other.js", lineNumber: 0, columnNumber: 0 }), null, "no map, no position");
    assert.equal(locate({ functionName: "native", url: "", lineNumber: -1 }), null);
    assert.equal(locate(null), null);
    assert.equal(locate.probe("feed.js"), true);
    assert.equal(locate.probe("other.js"), false);
    assert.deepEqual([...locate.loaded], ["feed.js"], "the locator says which maps it loaded");
    assert.deepEqual([...locate.missing], ["other.js"], "and which it could not");
    // A bundle whose second line first maps at column 4 (bundled node_modules code does this): a column-0
    // probe lands on line 1's mapping and the same-line guard refuses it; the per-line probe steps to it.
    fs.writeFileSync(path.join(tmp, "indent.js.map"), JSON.stringify({ version: 3, sources: ["../src/b.ts"], names: [], mappings: "AAAA;IACA" }));
    const cfi = { functionName: "f", url: "http://127.0.0.1:1/dist/indent.js?v=1", lineNumber: 1, columnNumber: 0 };
    assert.equal(locate(cfi), null, "the function-level lookup at column 0 refuses the previous line's mapping");
    assert.equal(locate.line(cfi, 1), "src/b.ts:2", "the per-line lookup finds the line's first mapping at column 4");
    assert.equal(locate.line(cfi, 0), "src/b.ts:1");
    assert.equal(locate.line(cfi, 7), null, "an unmapped line stays unresolved");
    assert.equal(locate.line({ functionName: "n", url: "", lineNumber: -1 }, 3), null);
    assert.equal(locate.line({ ...cfi, url: "http://127.0.0.1:1/dist/other.js" }, 1), null);
    const ranked = rankProfile(aggregateProfile(SYNTH_PROFILE), 1, locate);
    assert.equal(ranked.topSelf[0].src, undefined, "a line the map does not cover gets no position");
    const one = rankProfile({ durationMs: 1, sampledMs: 1, samples: 1, meta: {}, functions: [{ key: "feed.js:f:2", selfMs: 1, totalMs: 1, samples: 1, cf: { url: "http://x/dist/feed.js", lineNumber: 1, columnNumber: 0 }, lines: [{ line: 2, ms: 0.9 }, { line: 1, ms: 0.1 }, { line: 7, ms: 0.01 }] }] }, 1, locate);
    assert.deepEqual(one.topSelf, [{ key: "feed.js:f:2", selfMs: 1, totalMs: 1, samples: 1, src: "src/a.ts:2", lines: [{ line: 2, ms: 0.9, share: 0.9, src: "src/a.ts:2" }, { line: 1, ms: 0.1, share: 0.1, src: "src/a.ts:1" }] }], "lines resolve to their own source position; a line under a twentieth of the function's time is left out");
    const native = rankProfile({ durationMs: 1, sampledMs: 1, samples: 1, meta: {}, functions: [{ key: "(native):append", selfMs: 1, totalMs: 1, samples: 1, cf: { functionName: "append", url: "", lineNumber: -1 }, lines: [{ line: 5, ms: 1 }] }] }, 1, locate);
    assert.deepEqual(native.topSelf, [{ key: "(native):append", selfMs: 1, totalMs: 1, samples: 1 }], "a builtin's ticks name call sites in a file it does not have, so no lines");
    assert.match(renderProfile({ files: [], samplingIntervalUs: 500, alignMs: 0.1, sourceMaps: true, ...one, windows: [] }), /url:function:line  source:line\n\s+1\.0\s+1\.0\s+1\s+feed\.js:f:2  src\/a\.ts:2/);
    // The real dist, when built: the feed bundle's positions resolve into ui/webview.
    const dist = path.join(REPO, "vscode-extension", "dist");
    if (fs.existsSync(path.join(dist, "feed.js.map"))) {
      const real = sourceLocator(dist)({ functionName: "x", url: "http://127.0.0.1:1/dist/feed.js?v=1", lineNumber: 200, columnNumber: 0 });
      assert.match(real, /^(?!\.\.)(?!\/)\S+\.(ts|js):\d+$/, `a repo-relative source position: ${real}`);
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("the report claims source positions only for bundles whose maps loaded, and warns about the ones without", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-maps-"));
  try {
    const mkRun = () => { const run = fakeRun({ perFrame: [pf(0, "feed", 5000, 40, 90), pf(1, "delta:feed", 300, 4, 20)] }); run.profiling = { profile: SYNTH_PROFILE, p0: 0, alignMs: 1 }; return run; };
    const build = (sourceMapDir) => buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: true, iters: 1, browser: "t", runs: [mkRun()], cpuProfileFiles: ["/tmp/x.cpuprofile"], sourceMapDir });
    const noMaps = path.join(tmp, "nomaps");
    fs.mkdirSync(noMaps);
    const r1 = build(noMaps).cpuProfile;
    assert.equal(r1.sourceMaps, false, "a configured directory is not a loaded map");
    assert.deepEqual(r1.sourceMapsMissing, ["feed.js"]);
    assert.deepEqual(r1.sourceMapsLoaded, []);
    const t1 = renderProfile(r1);
    assert.match(t1, /warning: no feed\.js\.map beside the served bundle; names and lines are the bundle's own, and a --production build is minified/);
    assert.doesNotMatch(t1, /source positions from/);
    assert.doesNotMatch(t1, /source:line/);
    const withMaps = path.join(tmp, "maps");
    fs.mkdirSync(withMaps);
    fs.writeFileSync(path.join(withMaps, "feed.js.map"), JSON.stringify({ version: 3, sources: ["../src/a.ts"], names: [], mappings: "AAAA;AACA" }));
    const r2 = build(withMaps).cpuProfile;
    assert.equal(r2.sourceMaps, true);
    assert.deepEqual(r2.sourceMapsLoaded, ["feed.js"]);
    assert.deepEqual(r2.sourceMapsMissing, []);
    const t2 = renderProfile(r2);
    assert.match(t2, /source positions from feed\.js\.map/);
    assert.doesNotMatch(t2, /warning:/);
    assert.equal(r2.alignRefined, false, "this profile has no wrapper samples, so the bracketing estimate stands");
    assert.equal(r2.alignMs, 1);
    assert.equal(r2.alignBoundMs, 1);
    assert.match(t2, /±1\.0 ms \(the bracketing estimate; the refinement did not apply: no windows to check against\)/);
    const first = r2.windows.find((w) => w.label === "first content frame");
    assert.equal(first.window, "handler", "a run that saw no delivery falls back to the handler window");
    assert.equal(first.durationMs, 40, "a window's duration is the handler window's width");
    assert.equal(first.samples, 0, "and this profile has no samples in it");
    assert.match(t2, /window: first content frame \(feed, 4\.9 KB, frame 0\): handler 40\.0 ms, 0 samples/);
    // A run whose frames were delivered in the shim's flush task: the window is the delivery's, and frames
    // that shared one delivery share one window under a joined label.
    const flushRun = fakeRun({ handoff: "flush", flushes: 1, deliveries: 1, flushRows: [{ t0: 1300, ms: 61 }], deliveryRows: [{ t0: 1301, ms: 60, type: "feed" }],
      perFrame: [pf(0, "feed", 5000, 2, 150, "coalesced", null, 0), pf(1, "delta:feed", 300, 0.4, 70, "delivered", 60, 0)] });
    flushRun.profiling = { profile: SYNTH_PROFILE, p0: 0, alignMs: 1 };
    const r3 = buildReport({ app: "feed", framesFile: "f", cpuThrottle: 1, fast: true, iters: 1, browser: "t", runs: [flushRun], cpuProfileFiles: [], sourceMapDir: withMaps }).cpuProfile;
    assert.equal(r3.windows.length, 1);
    assert.equal(r3.windows[0].label, "first content frame + largest delta:feed");
    assert.equal(r3.windows[0].window, "delivery");
    assert.equal(r3.windows[0].windowMs, 60);
    assert.equal(r3.windows[0].durationMs, 60, "the window is the delivery's width");
    assert.equal(r3.windows[0].coalesced, true, "the first content frame rode in the delta's delivery");
    assert.equal(r3.windows[0].handlerMs, 2);
    assert.match(renderProfile(r3), /window: first content frame \+ largest delta:feed \(feed, 4\.9 KB, frame 0\): delivery 60\.0 ms \(a newer frame's delivery, this one coalesced into it\), shim handler 2\.0 ms, 0 samples/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("refineAlignment recovers the page-to-profile offset from the wrapper's samples", () => {
  // Ten wrapper samples 100 us apart from t=100 us, then ten (program) samples. The true page time of the
  // profile's start is 50 ms, so the handler window is [50.1, 51.05) ms; the bracketing estimate is 3 ms off.
  const cf = (functionName, url) => ({ functionName, url, lineNumber: 1, columnNumber: 0 });
  const profile = {
    nodes: [{ id: 1, callFrame: cf("(root)", ""), children: [2, 4] }, { id: 2, callFrame: cf("rompBenchOnMessage", "ui-bench-instrument.js"), children: [3] },
      { id: 3, callFrame: cf("render", "http://x/dist/feed.js") }, { id: 4, callFrame: cf("(program)", "") }],
    startTime: 0, endTime: 2100, samples: [...Array(10).fill(3), ...Array(10).fill(4)], timeDeltas: Array(20).fill(100),
  };
  const al = refineAlignment(profile, 53, 5, [[50.1, 51.05]]);
  assert.ok(Math.abs(al.p0 - 50) <= 0.13, `refined to ${al.p0}`);
  assert.equal(al.alignMs, 0.125, "a single grid point fits: the uncertainty is a quarter of the sampling interval");
  assert.equal(al.inside, 1, "every wrapper sample lands inside a handler window");
  assert.equal(al.refined, true);
  const none = refineAlignment(profile, 53, 5, []);
  assert.deepEqual(none, { p0: 53, alignMs: 5, inside: null, refined: false }, "without windows the estimate stands");
  const noWrapper = refineAlignment({ ...profile, samples: Array(20).fill(4) }, 53, 5, [[50.1, 51.05]]);
  assert.deepEqual(noWrapper, { p0: 53, alignMs: 5, inside: null, refined: false }, "without wrapper samples too");
  const far = refineAlignment(profile, 53, 5, [[80, 81]]);
  assert.deepEqual(far, { p0: 53, alignMs: 5, inside: 0, refined: false }, "when no offset within the bound fits, the estimate and its bound stand and the result says so");
  // The windows may overlap (a delivery inside its flush task, a flush starting as a handler ends): they are
  // merged before the search, so a window nested in another cannot hide the samples from it.
  assert.deepEqual(mergeWindows([[10, 20], [15, 30], [40, 50], [50, 60], [5, 1], [12, 13]]), [[10, 30], [40, 60]], "overlapping and touching windows merge; an empty one is dropped");
  assert.deepEqual(refineAlignment(profile, 53, 5, [[50.1, 51.05], [50.3, 50.6]]).inside, 1);
  // Only the instrument's wrappers count as wrapper time: its settle stamps and its passthrough of other
  // events are in the same file but run outside every window.
  const stampOnly = { ...profile, nodes: [...profile.nodes.slice(0, 1), { id: 2, callFrame: cf("", "ui-bench-instrument.js"), children: [3] }, ...profile.nodes.slice(2)] };
  assert.deepEqual(refineAlignment(stampOnly, 53, 5, [[50.1, 51.05]]), { p0: 53, alignMs: 5, inside: null, refined: false }, "an anonymous instrument frame is not a wrapper");
  const flushed = { ...profile, nodes: [...profile.nodes.slice(0, 1), { id: 2, callFrame: cf("rompBenchFlush", "ui-bench-instrument.js"), children: [3] }, ...profile.nodes.slice(2)] };
  assert.equal(refineAlignment(flushed, 53, 5, [[50.1, 51.05]]).inside, 1, "the flush wrapper's samples count");
  // Two long handler windows with slack at both edges (the shape of a real run: samples every 0.5 ms
  // from 0.4 ms after each window opens): every offset within the slack scores the same, so the answer
  // is the plateau's midpoint and half its width is the uncertainty, not the grid step. The true offset
  // lies within that uncertainty; the data cannot place it more precisely.
  const wins = [[100, 800], [900, 1760]];
  const truth = 50;   // the page time of the profile's start
  const pageTimes = [];
  for (const [a, b] of wins) for (let x = a + 0.4; x <= b - 0.4 + 1e-9; x += 0.5) pageTimes.push(x);
  const us = pageTimes.map((x) => Math.round((x - truth) * 1000));
  const long = { nodes: profile.nodes, startTime: 0, endTime: us[us.length - 1] + 500, samples: us.map(() => 3), timeDeltas: us.map((u, i) => u - (i ? us[i - 1] : 0)) };
  const pl = refineAlignment(long, truth + 0.7, 2, wins);
  assert.equal(pl.refined, true);
  assert.equal(pl.inside, 1);
  assert.ok(pl.alignMs >= 0.3 && pl.alignMs <= 0.5, `half the plateau's width (about a millisecond of slack), not the grid step: ${pl.alignMs}`);
  assert.ok(Math.abs(pl.p0 - truth) <= pl.alignMs, `the true offset lies within the reported uncertainty: ${pl.p0} ± ${pl.alignMs}`);
  assert.ok(Math.abs(pl.p0 - truth) < 0.7, `and the 0.7 ms estimate error was corrected: ${pl.p0}`);
});

test("aggregateProfile spreads self time over lines by V8's ticks per hit, not per sample, and gives no lines when no hit was recorded", () => {
  // The real profile has nodes whose hitCount is below their sample count (V8 records ticks with
  // update_stats off) and nodes with hitCount 0 that were still sampled.
  const cf = (functionName, lineNumber) => ({ functionName, url: "http://127.0.0.1:1/dist/feed.js", lineNumber, columnNumber: 0 });
  const prof = {
    nodes: [
      { id: 1, callFrame: { functionName: "(root)", url: "", lineNumber: -1 }, children: [2, 3] },
      { id: 2, callFrame: cf("render", 19), hitCount: 1, positionTicks: [{ line: 20, ticks: 1 }] },   // one tick recorded, two samples landed
      { id: 3, callFrame: cf("rk", 39), hitCount: 0 },
    ],
    startTime: 0, endTime: 400, samples: [2, 2, 3], timeDeltas: [100, 100, 100],
  };
  const a = aggregateProfile(prof);
  const render = a.functions.find((f) => f.key === "feed.js:render:20");
  assert.equal(render.selfMs, 0.2);
  assert.deepEqual(render.lines, [{ line: 20, ms: 0.2 }], "the lines sum to the node's self time (a per-sample split would say 0.1)");
  const rk = a.functions.find((f) => f.key === "feed.js:rk:40");
  assert.equal(rk.selfMs, 0.1);
  assert.equal(rk.lines, undefined, "self time but no lines when V8 recorded no hit");
  const ranked = rankProfile(a, 5);
  assert.deepEqual(ranked.topSelf[0].lines, [{ line: 20, ms: 0.2, share: 1 }]);
  assert.equal(ranked.topSelf[1].lines, undefined);
});

test("stripProfileQueries drops every call frame's URL query before the profile is written", () => {
  const prof = { nodes: [
    { id: 1, callFrame: { functionName: "ws.onmessage", url: "http://127.0.0.1:1/feed?token=secret-looking", lineNumber: 135 } },
    { id: 2, callFrame: { functionName: "render", url: "http://127.0.0.1:1/dist/feed.js?v=3", lineNumber: 19 } },
    { id: 3, callFrame: { functionName: "(program)", url: "", lineNumber: -1 } },
  ], startTime: 0, endTime: 1, samples: [1], timeDeltas: [1] };
  const out = stripProfileQueries(prof);
  assert.deepEqual(out.nodes.map((n) => n.callFrame.url), ["http://127.0.0.1:1/feed", "http://127.0.0.1:1/dist/feed.js", ""]);
  assert.doesNotMatch(JSON.stringify(out), /secret-looking|token=/);
  assert.ok(prof.nodes[0].callFrame.url.includes("token="), "the in-memory profile is untouched");
  assert.equal(out.samples, prof.samples);
});

test("aggregateProfile on an empty or window-less profile yields nothing rather than NaN", () => {
  const e = aggregateProfile({ nodes: [{ id: 1, callFrame: { functionName: "(root)", url: "", lineNumber: -1 } }], startTime: 5, endTime: 5, samples: [], timeDeltas: [] });
  assert.deepEqual(e, { durationMs: 0, sampledMs: 0, samples: 0, meta: {}, functions: [] });
  assert.deepEqual(rankProfile(e, 5).topSelf, []);
  assert.equal(aggregateProfile(SYNTH_PROFILE, [5000, 6000]).samples, 0);
});

// ── the CLI ──────────────────────────────────────────────────────────────────────────────────────

test("parseArgs takes --key value pairs and the two bare flags, keeps positionals, and refuses a flag without its value", () => {
  assert.deepEqual(parseArgs(["--replay", "feed", "--frames", "/tmp/f.jsonl", "--gap", "100", "--json", "/tmp/o.json"]),
    { _: [], replay: "feed", frames: "/tmp/f.jsonl", gap: "100", json: "/tmp/o.json" });
  assert.deepEqual(parseArgs(["--replay", "timeline", "--frames", "f", "--fast", "--cpu-throttle", "4", "--iters", "2", "--cpu-profile", "p"]),
    { _: [], replay: "timeline", frames: "f", fast: true, "cpu-throttle": "4", iters: "2", "cpu-profile": "p" });
  assert.deepEqual(parseArgs(["--compare", "a.json", "b.json"]), { _: ["b.json"], compare: "a.json" }, "the second report is a positional");
  assert.deepEqual(parseArgs(["--help"]), { _: [], help: true });
  assert.deepEqual(parseArgs([]), { _: [] });
  assert.throws(() => parseArgs(["--replay"]), /--replay needs a value/);
  assert.throws(() => parseArgs(["--gap", "--fast"]), /--gap needs a value/, "the next flag is not a value");
});

test("the CLI: --synthesize writes a frames file the tool reads back, --compare prints the deltas, no arguments print the usage, and a malformed command exits 1 naming the problem", { timeout: 60_000 }, () => {
  // CONTRIBUTING documents these flags; this drives the entry point as a subprocess with the commands that need
  // neither a browser nor python3 (review find, 2026-09-08: the CLI had no test of its own).
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-cli-"));
  const run = (...args) => spawnSync(process.execPath, [TOOL, ...args], { encoding: "utf8", timeout: 50_000 });
  try {
    const out = path.join(tmp, "synth", "feed.jsonl");
    const r = run("--synthesize", "feed", "--cards", "12", "--out", out, "--seed", "3");
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stderr, /ui-bench: wrote \d+ synthetic frames \([\d.]+ [KM]?B\) for app=feed/);
    const { meta, frames } = loadFrames(out);
    assert.deepEqual(meta, { tool: "ui-bench", mode: "synthesize", app: "feed", cards: 12, seed: 3, synthetic: true });
    assert.deepEqual(frames, synthesizeFrames("feed", 12, { seed: 3 }), "the file holds the synthesizer's stream for those arguments");
    if (process.platform !== "win32") { assert.equal(mode(out), 0o600); assert.equal(mode(path.dirname(out)), 0o700); }
    // --compare over two report files: the header names both pacings, the first-frame line carries the deltas
    const mk = (settle) => ({
      app: "feed", cpuThrottle: 1, fast: false, gapMs: 100, frames: { replayMs: 1000 }, handoff: { mode: "flush" },
      first: { type: "feed", bytes: 5000, handlerMs: 1, bundleMs: settle / 2, settleMs: settle },
      types: { feed: { count: 1, delivered: 1, bytes: 5000, handlerMs: { p50: 1, p90: 1, max: 1 }, bundleMs: { p50: settle / 2, p90: settle / 2, max: settle / 2 }, settleMs: { p50: settle, p90: settle, max: settle } } },
      loaf: { count: 0, durationMs: 0, blockingMs: 0, maxMs: 0 }, end: { heapUsed: 1000, domElements: 100, layoutCount: 5, scriptMs: 50, taskMs: 80 }, console: { errors: [] },
    });
    const a = path.join(tmp, "a.json"), b = path.join(tmp, "b.json");
    fs.writeFileSync(a, JSON.stringify(mk(200)));
    fs.writeFileSync(b, JSON.stringify(mk(100)));
    const c = run("--compare", a, b);
    assert.equal(c.status, 0, c.stderr);
    assert.match(c.stdout, /^compare: feed \(cpu x1, 100 ms gaps\) → feed \(cpu x1, 100 ms gaps\)/);
    assert.match(c.stdout, /first content frame: .*settled 200 → 100 ms \(-100, -50%\)/);
    const usage = run();
    assert.equal(usage.status, 0);
    assert.match(usage.stdout, /^usage:\n  node tools\/ui-bench\.mjs --record/);
    assert.equal(run("--help").status, 0);
    const nothing = run("--seed", "1");
    assert.equal(nothing.status, 2, "flags without a command print the usage and exit 2");
    assert.match(nothing.stdout, /^usage:/);
    // each refusal is one line on stderr, exit 1, before anything is launched
    for (const [args, why] of [
      [["--replay", "feed"], "--replay needs --frames FILE"],
      [["--replay", "feed", "--frames", out, "--fast", "--gap", "5"], "--fast and --gap are two pacings; give one"],
      [["--record", "feed"], "--record needs --out"],
      [["--compare", a], "--compare needs two report files"],
      [["--synthesize", "chat", "--out", path.join(tmp, "chat.jsonl")], "chat frames are not synthesized"],
      [["--synthesize", "feed", "--out"], "--out needs a value"],
    ]) {
      const bad = run(...args);
      assert.equal(bad.status, 1, `${args.join(" ")}: ${bad.stdout}${bad.stderr}`);
      assert.ok(bad.stderr.startsWith(`ui-bench: ${why}`), `${args.join(" ")} -> ${bad.stderr}`);
    }
    assert.ok(!fs.existsSync(path.join(tmp, "chat.jsonl")));
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ── a real replay, when a browser is at hand ─────────────────────────────────────────────────────

const avail = browserAvailability();
const distBuilt = fs.existsSync(path.join(REPO, "vscode-extension", "dist", "feed.js"));
const pythonOk = (() => { try { return spawnSync("python3", ["--version"]).status === 0; } catch { return false; } })();
const skipServer = !distBuilt ? "no built bundles at vscode-extension/dist (run: cd vscode-extension && npm run build)"
  : !pythonOk ? "python3 is not on PATH (the replay serves the page through the kernel's own Handler)"
  : false;
const skipReplay = !avail.ok ? `no browser: ${avail.why}` : skipServer;
// ROMP_UI_BENCH_REQUIRE (CI sets it) turns a skip into a failure that names the missing prerequisite:
// a runner image that lost its Chrome must not turn the only CI run of the real pages into a green skip.
const required = !!process.env.ROMP_UI_BENCH_REQUIRE;
const gate = (why) => ({ skip: required ? false : why });
const requireOrSkip = (why) => { if (why) assert.fail(`ROMP_UI_BENCH_REQUIRE is set and this test cannot run: ${why}`); };
// The timing RELATIONS the replays measure (a settle margin's floor, a render outweighing a parse, the share
// of profiler samples inside their windows, the CPU throttle's slowdown, a delta reaching the bundle on its
// own at a given gap) hold on a quiet machine and are what the bench is for, but every one is a function of
// the scheduler: a shared CI runner that deschedules the renderer's main thread across two frames coalesces
// a delta, stretches a handler past a render, or starves the sampler. The upstream run of this file's first
// form went red on exactly that (a 50 ms-gap replay asserting that no delta ever coalesces, 22 !== 23, while
// the same sha passed elsewhere; review find, 2026-09-08). So the required CI step asserts only what must
// hold under ANY scheduling: totals, ordering, presence, and the handoff's accounting identity (delivered +
// coalesced + shim + queued = count). A relation is an assertion only under ROMP_UI_BENCH_TIMING=1 (a quiet
// developer machine; CI never sets it); otherwise one that did not hold is a diagnostic line in the log,
// never a failure.
const TIMING = !!process.env.ROMP_UI_BENCH_TIMING;
const timingCheck = (t) => (cond, msg) => {
  if (TIMING) assert.ok(cond, msg);
  else if (!cond) t.diagnostic(`timing relation not held on this run (informational; ROMP_UI_BENCH_TIMING=1 asserts it): ${msg}`);
};

test("the replays have a browser when ROMP_UI_BENCH_REQUIRE is set, and the log says which", (t) => {
  // CI installs playwright's own Chromium (the build package-lock's playwright pins) before this step, so the
  // required check never rides the runner image's Google Chrome (unpinned, auto-updated; review find,
  // 2026-09-08); a developer's machine may have only a system Chrome, found by the PATH scan below. This line
  // in the step's log says which one ran, and under ROMP_UI_BENCH_REQUIRE a runner without any fails here,
  // before the replays.
  if (required) assert.ok(avail.ok, avail.why);
  t.diagnostic(`browser: ${avail.ok ? avail.how + (avail.channel ? ` (channel ${avail.channel})` : "") : "none: " + avail.why}`);
});

test("browserAvailability finds a system Chrome or Chromium on PATH when playwright's browser is absent, and names its channel", () => {
  // The module in a fresh process: playwright's browsers directory pointed at an empty one (so its own Chromium is
  // absent, as on the CI runner), PATH holding one fake browser binary. No browser is launched.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-path-"));
  try {
    const noBrowsers = path.join(tmp, "no-browsers");
    const bin = path.join(tmp, "bin");
    fs.mkdirSync(noBrowsers);
    fs.mkdirSync(bin);
    const probe = (PATH) => {
      const r = spawnSync(process.execPath, ["--input-type=module", "-e", `import(${JSON.stringify(pathToFileURL(TOOL).href)}).then((m) => console.log(JSON.stringify(m.browserAvailability())))`],
        { env: { HOME: os.homedir(), PATH, PLAYWRIGHT_BROWSERS_PATH: noBrowsers }, encoding: "utf8", timeout: 30_000 });
      assert.equal(r.status, 0, r.stderr);
      return JSON.parse(r.stdout.trim());
    };
    fs.writeFileSync(path.join(bin, "google-chrome"), "", { mode: 0o755 });
    assert.deepEqual(probe(bin), { ok: true, how: "system google-chrome", exe: path.join(bin, "google-chrome"), channel: "chrome" });
    fs.renameSync(path.join(bin, "google-chrome"), path.join(bin, "chromium"));
    assert.deepEqual(probe(bin), { ok: true, how: "system chromium", exe: path.join(bin, "chromium"), channel: "chromium" });
    const none = probe(path.join(tmp, "empty-path"));
    assert.equal(none.ok, false);
    assert.match(none.why, /npx playwright install chromium/, "the refusal says how to get a browser");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("ROMP_UI_BENCH_REQUIRE turns the browser skip into a failure that names the reason", { timeout: 60_000 }, () => {
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-nobrowser-"));
  try {
    const env = { ROMP_UI_BENCH_REQUIRE: "1", PATH: "", PLAYWRIGHT_BROWSERS_PATH: empty, HOME: os.homedir() };
    const r = spawnSync(process.execPath, ["--test", "--test-name-pattern", "^replay:", THIS_FILE], { env, encoding: "utf8", timeout: 50_000 });
    assert.notEqual(r.status, 0, `the nested run must fail\n${r.stdout}\n${r.stderr}`);
    assert.match(r.stdout, /ROMP_UI_BENCH_REQUIRE is set and this test cannot run: no browser/);
    assert.match(r.stdout, /# fail 4/, "every replay test, not a skip");
    assert.doesNotMatch(r.stdout, /# skipped [1-9]/);
    delete env.ROMP_UI_BENCH_REQUIRE;
    const s = spawnSync(process.execPath, ["--test", "--test-name-pattern", "^replay:", THIS_FILE], { env, encoding: "utf8", timeout: 50_000 });
    assert.equal(s.status, 0, `without the variable the same run skips\n${s.stdout}\n${s.stderr}`);
    assert.match(s.stdout, /# skipped 4/);
  } finally {
    fs.rmSync(empty, { recursive: true, force: true });
  }
});

// ── the Handler subprocess ───────────────────────────────────────────────────────────────────────

test("startPageServer hands the Handler an isolated environment: a minted token, a dead manager port, no key source or credential conftest pops, no CLI scope, no postal peers", { timeout: 30_000 }, async () => {
  // A stub interpreter stands in for python3: it echoes its environment, announces a port, and blocks on
  // stdin the way the Handler's watcher thread does. So this needs neither python nor the kernel.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-env-"));
  try {
    const stub = writeStub(tmp, "python-stub", `env > "$UI_BENCH_STUB_ENV_OUT"\nprintf 'ARGS %s\\n' "$#" >> "$UI_BENCH_STUB_ENV_OUT"\nprintf 'TMPARG %s\\n' "$4" >> "$UI_BENCH_STUB_ENV_OUT"\n`);
    const dist = fakeDist(tmp);
    const envOut = path.join(tmp, "env.txt");
    // The variables the Handler must never inherit, named here so a shrinking export cannot shrink the check:
    // the manager's, the live kernel's ports and state root, the perf switch and a terminal's TMUX.
    const NEVER = ["ROMP_MANAGER_PORT", "ROMP_MANAGER_PID", "ROMP_SUPERVISED", "ROMP_STATE_DIR", "ROMP_SERVE_PORT", "ROMP_KERNEL_PORT", "ROMP_PERF", "TMUX"];
    assert.deepEqual([...STRIPPED_ENV].sort(), [...NEVER].sort(), "the tool's list is exactly this one");
    // Every key-source and credential name tests/conftest.py pops before any test (its KEY_SOURCE_ENV_NAMES and
    // KEY_SOURCE_ENV_PREFIXES, plus the ANTHROPIC_ prefix): a replay run from inside a romp session inherits all
    // of them from the manager. The first form of this test planted ANTHROPIC_* and the key reference only, so a
    // Handler holding the key command, the OAuth token and 1Password's names passed it (review find, 2026-09-08).
    const NEVER_KEYS = ["ANTHROPIC_API_KEY", "ROMP_API_KEY_REF", "ROMP_API_KEY_CMD", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ROMP_EXPECTED_AUTH",
      "OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN", "OP_ACCOUNT"];
    assert.deepEqual([...STRIPPED_KEY_ENV].sort(), [...NEVER_KEYS].sort(), "the tool's key-source list is exactly conftest's");
    assert.deepEqual([...STRIPPED_KEY_ENV_PREFIXES].sort(), ["ANTHROPIC_", "OP_SESSION_"]);
    const plantedKeys = { ...Object.fromEntries(NEVER_KEYS.map((k) => [k, `planted-${k.toLowerCase()}`])), OP_SESSION_testaccount: "planted-op-session", ANTHROPIC_PROBE_FOR_THE_TEST: "must-not-cross" };
    const planted = { UI_BENCH_STUB_ENV_OUT: envOut, ...plantedKeys, ROMP_CLAUDE_BIN: "/nonexistent/claude", ROMP_POSTAL_PEERS: "1",
      ROMP_SERVICE_ENV_FILE: path.join(tmp, "planted-service.env"), ROMP_MODEL_CATALOG: "on", ROMP_CLI_SCOPE: "1",
      ...Object.fromEntries(NEVER.map((k) => [k, k === "TMUX" ? "/tmp/tmux-0/default,1,0" : k === "ROMP_STATE_DIR" ? path.join(tmp, "planted-state") : k === "ROMP_MANAGER_PORT" ? "7432" : "1"])) };
    const srv = await withEnv(planted, () => startPageServer({ dist, python: stub }));
    try {
      assert.equal(srv.port, 1);
      assert.match(srv.token, /^[A-Za-z0-9_-]{24}$/, "a per-run token, 18 random bytes as base64url");
      assert.ok(srv.pid > 0);
      assert.ok(fs.existsSync(srv.tmp));
      const env = Object.fromEntries(fs.readFileSync(envOut, "utf8").trim().split("\n").map((l) => { const i = l.indexOf("="); return i < 0 ? [l, ""] : [l.slice(0, i), l.slice(i + 1)]; }));
      assert.equal(env.ROMP_SERVE_TOKEN, srv.token);
      assert.equal(env.ROMP_POSTAL_PEERS, "0", "the Handler never asks the live postal bus for its peers");
      assert.equal(env.ROMP_KERNEL_NO_OPEN, "1");
      assert.equal(env.ROMP_SERVICE_ENV_FILE, path.join(srv.tmp, "no-service.env"), "the manager's key file is pointed at a path that never exists (the kernel would otherwise read ~/.config/romp/service.env)");
      assert.equal(env.ROMP_SERVICE_ENV, env.ROMP_SERVICE_ENV_FILE);
      assert.ok(!fs.existsSync(env.ROMP_SERVICE_ENV_FILE));
      assert.equal(env.ROMP_MODEL_CATALOG, "off", "no boot fetch of the Models API");
      assert.equal(env.ROMP_CLAUDE_BIN, "/bin/false", "a binary that runs nothing; removing the variable would resolve the real CLI");
      assert.equal(env.ROMP_CLI_SCOPE, "0", "no route that constructs the SDK backend probes systemd-run");
      for (const k of Object.keys(plantedKeys)) assert.equal(env[k], undefined, `${k} was planted and must not reach the Handler (a key source or credential conftest pops)`);
      assert.equal(path.dirname(srv.tmp), srv.root, "the run directory sits under the per-user parent");
      assert.equal(path.basename(srv.root), `romp-ui-bench-${UID}`);
      assert.equal(fs.readFileSync(path.join(srv.tmp, "owner.pid"), "utf8").trim(), String(process.pid), "the run records its owner for the dead-owner sweep");
      if (process.platform !== "win32") { assert.equal(mode(srv.root), 0o700); assert.equal(mode(srv.tmp), 0o700); }
      assert.equal(env.ROMP_DIST_DIR, dist);
      assert.ok(env.XDG_STATE_HOME.startsWith(srv.tmp + path.sep), "a private state root");
      assert.ok(fs.readFileSync(envOut, "utf8").includes(`\nTMPARG ${srv.tmp}\n`), "the subprocess is told its directory so it can remove it");
      for (const k of Object.keys(env)) assert.ok(!k.startsWith("ANTHROPIC_") && !k.startsWith("OP_SESSION_"), `${k} must not reach the Handler`);
      for (const k of NEVER) if (k !== "ROMP_MANAGER_PORT") assert.equal(env[k], undefined, `${k} was planted and must not reach the Handler`);
      // conftest's form of the manager floor: a DEAD port, never an absent variable (one kernel consumer maps an
      // absent port to the default, live, one), so the planted 7432 comes out as 1, not as nothing.
      assert.equal(env.ROMP_MANAGER_PORT, "1", "the manager port is a dead one, not absent");
      assert.equal(env.UI_BENCH_STUB_ENV_OUT, envOut, "unrelated variables pass through");
      // a second run mints its own token: the Handler is the kernel's whole route surface on a loopback port
      const other = await startPageServer({ dist, python: stub });
      try { assert.notEqual(other.token, srv.token, "a token minted per run, never a constant"); }
      finally { other.stop(); }
      await other.exited;
    } finally {
      srv.stop();
    }
    await srv.exited;   // the event, not a timer: on a loaded machine SIGTERM-to-reaped ran past 100 ms
    assert.ok(!fs.existsSync(srv.tmp), "stop removes the directory");
    assert.throws(() => process.kill(srv.pid, 0), /ESRCH/, "and ends the subprocess");
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("startPageServer cleans up when the interpreter exits before announcing a port, and refuses without a dist before creating anything", { timeout: 30_000 }, async () => {
  // os.tmpdir() follows TMPDIR, so the server's directories land in a private one this test can list
  // (the machine's shared temp root is never enumerated).
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-fail-"));
  const scratch = path.join(tmp, "scratch");
  fs.mkdirSync(scratch);
  try {
    await withEnv({ TMPDIR: scratch }, async () => {
      assert.equal(os.tmpdir(), scratch);
      await assert.rejects(startPageServer({ dist: path.join(tmp, "nodist") }), /no built bundles/);
      assert.deepEqual(fs.readdirSync(scratch), [], "the dist check runs before any directory is created");
      const dist = fakeDist(tmp);
      const dying = path.join(tmp, "python-dying");
      fs.writeFileSync(dying, "#!/bin/sh\necho 'ImportError: fake' >&2\nexit 3\n", { mode: 0o755 });
      await assert.rejects(startPageServer({ dist, python: dying }), /exited with 3[\s\S]*ImportError: fake/);
      const root = path.join(scratch, `romp-ui-bench-${UID}`);
      assert.deepEqual(fs.readdirSync(scratch), [path.basename(root)], "only the per-user parent is created");
      assert.deepEqual(fs.readdirSync(root), [], "an interpreter that dies before its port leaves no run directory behind");
      await assert.rejects(startPageServer({ dist, python: path.join(tmp, "no-such-interpreter") }), /could not start \S*no-such-interpreter: spawn \S*no-such-interpreter ENOENT/);
      assert.deepEqual(fs.readdirSync(root), [], "nor does one that cannot be spawned (an unhandled 'error' event used to crash the process and leak it)");
    });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("benchRoot is a private per-user directory, and sweepDeadRuns reclaims the runs whose owner is gone", { timeout: 30_000 }, async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-root-"));
  const scratch = path.join(tmp, "scratch");
  fs.mkdirSync(scratch);
  try {
    await withEnv({ TMPDIR: scratch }, async () => {
      const root = benchRoot();
      assert.equal(root, path.join(scratch, `romp-ui-bench-${UID}`));
      if (process.platform !== "win32") {
        assert.equal(mode(root), 0o700);
        fs.chmodSync(root, 0o755);
        assert.equal(benchRoot(), root);
        assert.equal(mode(root), 0o700, "a loosened mode is restored");
      }
      const dead = spawnSync(process.execPath, ["-e", "0"]).pid;   // a process that has already exited
      const mk = (name, pid) => { fs.mkdirSync(path.join(root, name)); if (pid !== undefined) fs.writeFileSync(path.join(root, name, "owner.pid"), `${pid}\n`); };
      mk("run-dead", dead); mk("run-live", process.pid); mk("run-nopid"); mk("run-garbage", "abc");
      assert.deepEqual(sweepDeadRuns(root), ["run-dead"]);
      assert.deepEqual(fs.readdirSync(root).sort(), ["run-garbage", "run-live", "run-nopid"], "a live owner, a missing pid file and an unreadable one are left alone");
      mk("run-dead2", dead);
      const logged = [];
      const srv = await startPageServer({ dist: fakeDist(tmp), python: writeStub(tmp), log: (l) => logged.push(l) });
      try {
        assert.ok(!fs.existsSync(path.join(root, "run-dead2")), "a start sweeps the dead runs first");
        assert.ok(fs.existsSync(path.join(root, "run-live")), "and leaves the live ones");
        assert.ok(logged.some((l) => /removed 1 run directory left behind by dead runs/.test(l)), logged.join("\n"));
        assert.equal(path.dirname(srv.tmp), root);
      } finally {
        srv.stop();
      }
      assert.deepEqual(fs.readdirSync(root).sort(), ["run-garbage", "run-live", "run-nopid"], "stop removed its own run directory");
      assert.deepEqual(sweepDeadRuns(path.join(tmp, "absent")), [], "a missing root is nothing to sweep");
      // Something else holding the parent's name is refused, never adopted.
      const linkBase = path.join(tmp, "linkbase");
      fs.mkdirSync(path.join(tmp, "elsewhere"));
      fs.mkdirSync(linkBase);
      fs.symlinkSync(path.join(tmp, "elsewhere"), path.join(linkBase, `romp-ui-bench-${UID}`));
      assert.throws(() => benchRoot(linkBase), /not a directory; refusing/);
    });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("launchBrowser puts Playwright's profile and artifacts directories under the run directory", { ...gate(skipReplay), timeout: 60_000 }, async () => {
  requireOrSkip(skipReplay);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-browser-"));
  const srv = await startPageServer({ dist: fakeDist(tmp), python: writeStub(tmp) });
  const before = process.env.TMPDIR;
  let browser;
  try {
    browser = await launchBrowser({ tmpRoot: srv.tmp });
    assert.equal(process.env.TMPDIR, before, "TMPDIR is restored once the browser is up");
    const names = fs.readdirSync(srv.tmp);
    assert.ok(names.some((n) => n.startsWith("playwright")), `the browser's directories live in the run directory, where stop() and the sweep reach them: ${names.join(", ")}`);
  } finally {
    if (browser) await browser.close().catch(() => {});
    srv.stop();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  assert.ok(!fs.existsSync(srv.tmp), "stop takes the browser's directories with the run's");
});

test("the front server's /ws admits the page's own origin only, and no other path upgrades", async () => {
  // The kernel Handler is never consulted for /ws (pagePort is a dead port here); the front answers the upgrade.
  const front = await startFront({ pagePort: 1 });
  const reached = [];
  front.setSocketHandler((ws, u) => { reached.push(u.pathname + u.search); ws.send("front " + u.pathname); });
  const WebSocket = requireExt("ws");
  const attempt = (p, origin) => new Promise((resolve) => {
    const ws = new WebSocket(`ws://127.0.0.1:${front.port}${p}`, origin ? { origin } : {});
    ws.on("unexpected-response", (_req, res) => { resolve({ status: res.statusCode }); ws.terminate(); });
    ws.on("message", (d) => { resolve({ message: d.toString() }); ws.close(); });
    ws.on("error", (e) => resolve({ error: e.message }));
  });
  try {
    assert.deepEqual(await attempt("/ws?app=feed", "http://evil.example"), { status: 403 }, "a foreign Origin is refused");
    assert.deepEqual(await attempt("/ws?app=feed"), { status: 403 }, "and so is a client that sends no Origin at all: a browser page always sends one, so that is some other local process, and the frames it would receive may be a recording of real session data (review find, 2026-09-08)");
    assert.deepEqual(await attempt("/other", `http://127.0.0.1:${front.port}`), { status: 403 }, "no path but /ws upgrades");
    assert.deepEqual(reached, [], "neither reached the replay's socket handler");
    assert.deepEqual(await attempt("/ws?app=feed", `http://127.0.0.1:${front.port}`), { message: "front /ws" }, "the page's own origin upgrades");
    assert.deepEqual(reached, ["/ws?app=feed"]);
  } finally {
    front.stop();
  }
});

test("the in-page instrument counts the flush task's arming posts, stamps a delivery's settle after two animation frames, and records the seam and a throw", { ...gate(skipReplay), timeout: 60_000 }, async () => {
  requireOrSkip(skipReplay);
  // The instrument on a blank page, driven directly: a MessageChannel whose handler re-posts from inside the flush
  // (the shim's sliced flush re-arming itself), a stub federation object whose inbound re-dispatches the frame on
  // window (federation re-emitting the merged frame to the pane), one that throws, and a dispatch outside any inbound.
  const browser = await launchBrowser();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.addInitScript(INIT_SCRIPT);
    await page.goto("about:blank");
    const r = await page.evaluate(async () => {
      const R = window.__rompBench;
      const out = {};
      const ch = new MessageChannel();
      let n = 0;
      const drained = new Promise((res) => { ch.port1.onmessage = () => { if (n++ < 2) ch.port2.postMessage(0); else res(); }; });
      ch.port2.postMessage(0);   // armed from outside any handler or flush: not a re-arm, not counted
      await drained;
      out.portPosts = R.portPosts; out.portFires = R.portFires; out.flushes = R.flushes.map((f) => ({ ms: f.ms, deliveries: f.deliveries }));
      window.__rompFed = { inbound(_h, m) { window.dispatchEvent(new MessageEvent("message", { data: m })); } };
      window.__rompFed.inbound("", { type: "feed" });
      out.afterInbound = R.deliveries.map((d) => ({ type: d.type, via: d.via, flush: d.flush, rec: d.rec, err: d.err }));
      out.settleSync = R.deliveries[0].settle;
      await new Promise((res) => requestAnimationFrame(res));
      out.settleAfterOneFrame = R.deliveries[0].settle;
      await new Promise((res) => requestAnimationFrame(() => setTimeout(res, 0)));
      out.settleAfterTwoFrames = R.deliveries[0].settle;
      window.dispatchEvent(new MessageEvent("message", { data: { type: "bars" } }));
      window.__rompFed = { inbound() { throw new Error("bundle refused the frame"); } };
      let thrown = null;
      try { window.__rompFed.inbound("", { type: "session" }); } catch (e) { thrown = e.message; }
      out.thrown = thrown;
      out.deliveries = R.deliveries.map((d) => ({ type: d.type, via: d.via, err: d.err }));
      out.recs = R.recs.length;
      return out;
    });
    await context.close();
    assert.equal(r.portPosts, 2, "the two posts made inside a flush are counted; the one from outside is not");
    assert.equal(r.portFires, 3, "every armed flush fired");
    assert.equal(r.flushes.length, 3);
    assert.ok(r.flushes.every((f) => f.ms >= 0 && f.deliveries === 0), JSON.stringify(r.flushes));
    assert.deepEqual(r.afterInbound, [{ type: "feed", via: "inbound", flush: -1, rec: -1, err: null }], "a dispatch inside an inbound is part of that delivery, never a second one");
    assert.equal(r.settleSync, -1, "settle is not stamped when the delivery returns");
    assert.equal(r.settleAfterOneFrame, -1, "nor after one animation frame");
    assert.ok(r.settleAfterTwoFrames >= 0, `stamped after the second: ${r.settleAfterTwoFrames}`);
    assert.deepEqual(r.deliveries, [{ type: "feed", via: "inbound", err: null }, { type: "bars", via: "dispatch", err: null }, { type: "session", via: "inbound", err: "bundle refused the frame" }],
      "a dispatch outside any inbound is a delivery through the fallback seam; a throwing inbound is recorded and rethrown");
    assert.equal(r.thrown, "bundle refused the frame");
    assert.equal(r.recs, 0, "no WebSocket frame was involved");
  } finally {
    await browser.close().catch(() => {});
  }
});

test("the Handler subprocess ends and removes its directory when the node process that started it is SIGKILLed", { ...gate(skipServer), timeout: 90_000 }, async () => {
  requireOrSkip(skipServer);
  const script = `
    import { startPageServer } from ${JSON.stringify(pathToFileURL(TOOL).href)};
    const s = await startPageServer({ log: () => {} });
    process.stdout.write(JSON.stringify({ pid: s.pid, tmp: s.tmp, port: s.port }) + "\\n");
    setInterval(() => {}, 1000);
  `;
  const child = spawn(process.execPath, ["--input-type=module", "-e", script], { stdio: ["ignore", "pipe", "pipe"] });
  let info = null;
  try {
    let buf = "", err = "";
    child.stderr.on("data", (c) => { err += c; });
    info = await new Promise((resolve, reject) => {
      child.stdout.on("data", (c) => { buf += c; const nl = buf.indexOf("\n"); if (nl >= 0) resolve(JSON.parse(buf.slice(0, nl))); });
      child.on("exit", (code) => reject(new Error(`the child exited early with ${code}: ${err}`)));
    });
    assert.ok(fs.existsSync(info.tmp), "the Handler's directory exists while it runs");
    assert.doesNotThrow(() => process.kill(info.pid, 0), "the Handler is alive");
    const page = await new Promise((resolve, reject) => http.get({ host: "127.0.0.1", port: info.port, path: "/feed" }, (res) => { res.resume(); resolve(res.statusCode); }).on("error", reject));
    assert.equal(page, 403, "and without the minted token the page route refuses");
  } finally {
    child.kill("SIGKILL");
  }
  const t0 = Date.now();
  let alive = true, dirThere = true;
  while (Date.now() - t0 < 20_000) {
    try { process.kill(info.pid, 0); alive = true; } catch (e) { alive = e.code !== "ESRCH"; }
    dirThere = fs.existsSync(info.tmp);
    if (!alive && !dirThere) break;
    await sleep(50);
  }
  assert.equal(alive, false, "the Handler ended once its stdin pipe closed");
  assert.equal(dirThere, false, "and removed its directory");
});

// ── a real replay, when a browser is at hand ─────────────────────────────────────────────────────

// The feed stream goes in at a fixed gap wide enough for the shim's flush to run between frames, so most
// frames reach the bundle as their own delivery and the bundle column is per frame; the timeline stream goes
// in back-to-back, so the frames queue together and the shim coalesces the whole-state kinds.
const REPLAY_PACING = { feed: { gapMs: 100 }, timeline: { fast: true } };

for (const app of ["feed", "timeline"]) {
  test(`replay: a synthetic ${app} stream renders in headless Chromium, every frame type measured and accounted for by the handoff, no console errors`,
    { ...gate(skipReplay), timeout: 180_000 }, async (t) => {
      requireOrSkip(skipReplay);
      const timing = timingCheck(t);
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-replay-"));
      try {
        const frames = synthesizeFrames(app, 40);
        const file = path.join(tmp, `${app}.jsonl`);
        writeFrames(file, { synthetic: true, app }, frames);
        const jsonOut = path.join(tmp, "report.json");
        const cpuProfile = app === "feed" ? path.join(tmp, "prof", "feed.cpuprofile") : undefined;
        const pacing = REPLAY_PACING[app];
        const timersBefore = process.getActiveResourcesInfo().filter((x) => x === "Timeout").length;
        const report = await replay({ app, framesFile: file, ...pacing, jsonOut, cpuProfile, log: () => {} });
        const timersAfter = process.getActiveResourcesInfo().filter((x) => x === "Timeout").length;
        assert.ok(timersAfter <= timersBefore, `replay left ${timersAfter - timersBefore} timer(s) armed (the handshake timeout must be cleared)`);
        assert.equal(process.listenerCount("SIGINT"), 0, "the signal handlers are removed on the way out");
        assert.equal(report.tool, "ui-bench");
        assert.equal(report.app, app);
        assert.equal(report.fast, !!pacing.fast);
        assert.equal(report.gapMs, pacing.gapMs ?? null);
        assert.equal(report.cpuThrottle, 1);
        assert.equal(report.frames.total, frames.length);
        assert.equal(report.frames.misaligned, 0, "every page record matched its frame by length");
        assert.equal(report.frames.reconnects, 0);
        for (const k of ["first", "types", "handoff", "loaf", "end", "console", "clientMessages", "perFrame"]) assert.ok(k in report, `report.${k}`);
        assert.ok(report.frames.readyMs > 0, "the page reached its ready handshake");
        const expected = new Set(frames.map((f) => classifyFrame(f.data)));
        assert.ok(expected.size >= 3);
        for (const type of expected) {
          const s = report.types[type];
          assert.ok(s, `a row for ${type}`);
          assert.ok(s.measured >= 1, `${type}: at least one measured frame`);
          assert.equal(s.measured, s.count, `${type}: every frame measured`);
          assert.equal(s.handlerMs.n, s.count, `${type}: every frame's handler timed`);
          assert.equal(s.settleMs.n, s.count, `${type}: every frame's settle stamped (null percentiles compare as 0, so the sample size is what proves it)`);
          assert.equal(s.settleMissing, 0, `${type}: no settle stamp missing`);
          assert.equal(typeof s.settleMs.p50, "number");
          assert.ok(s.handlerMs.p50 >= 0 && s.handlerMs.max >= s.handlerMs.p50, `${type}: handler percentiles`);
          assert.ok(s.settleMs.p50 >= 0 && s.settleMs.max >= s.settleMs.p50, `${type}: settle percentiles`);
          assert.ok(s.settleMs.p50 >= s.handlerMs.p50, `${type}: the main thread is free no sooner than the handler returns`);
          // The handoff accounts for every frame: delivered on its own, coalesced into a newer frame's delivery,
          // or the shim's alone; nothing left queued.
          assert.equal(s.delivered + s.coalesced + s.shim + s.queued, s.count, `${type}: every frame accounted for (${JSON.stringify(s)})`);
          assert.equal(s.queued, 0, `${type}: nothing still queued`);
          assert.equal(s.bundleMs.n, s.delivered, `${type}: a bundle time for each frame delivered on its own`);
          if (s.delivered) assert.ok(s.bundleMs.p50 > 0 && s.bundleMs.max >= s.bundleMs.p50, `${type}: bundle percentiles`);
          if (type === "ka") { assert.equal(s.shim, s.count, "keepalives are the shim's alone"); assert.equal(s.delivered, 0); }
          else if (s.delivered) assert.ok(s.settleMs.max >= s.bundleMs.p50, `${type}: the main thread is free no sooner than a delivery returns`);
        }
        assert.equal(report.frames.settleMissing, 0);
        assert.equal(report.frames.addListenerMessages, 0, "the pane's socket handler is the onmessage the bench times");
        assert.equal(report.frames.buildBannerRaised, 0, "a synthetic stream's keepalive dv never outruns the dist");
        assert.equal(report.frames.connBannerRaised, 0);
        // The tip's shim hands frames over in its flush task, and the bench saw every handoff through it.
        assert.equal(report.handoff.mode, "flush", JSON.stringify(report.handoff));
        assert.ok(report.handoff.flushes >= 1 && report.handoff.deliveries >= 1, JSON.stringify(report.handoff));
        assert.equal(report.handoff.queueDrained, true, "every flush the frames armed ran before the collection");
        assert.deepEqual(report.handoff.deliveryErrors, []);
        assert.equal(report.handoff.deliveries, report.handoff.delivered + report.handoff.unattributed, "one delivery per frame delivered on its own, the rest the page's own re-emits");
        assert.equal(report.handoff.delivered + report.handoff.coalesced + report.handoff.shim, frames.length, "the frames add up");
        assert.equal(report.handoff.queued, 0);
        assert.equal(report.handoff.unattributed, 0, "no delivery the frames do not explain (federation re-dispatches every frame inside its inbound; that is one delivery, not two)");
        assert.deepEqual(report.handoff.deliverySeams, { inbound: report.handoff.deliveries }, "every delivery came through federation's published inbound, none through the MessageEvent fallback");
        // The columns are measurements, not constants: the shim's parse of the first content frame is measurable at
        // performance.now() resolution. How the measurements RELATE (the parse cheaper than the render, a settle
        // margin of two animation frames, a keepalive's settle at least one frame interval, how many deltas reach the
        // bundle on their own at this gap, the forced collection freeing garbage) is the scheduler's to decide on a
        // loaded runner, so those are timing() relations: asserted under ROMP_UI_BENCH_TIMING, diagnostics otherwise.
        assert.ok(report.first.handlerMs > 0, `the first frame's handler time is a measurement: ${report.first.handlerMs}`);
        assert.ok(report.types[report.first.type].handlerMs.max > 0);
        if (report.first.handoff === "delivered") {
          timing(report.first.handlerMs < report.first.bundleMs, `the shim's parse is cheaper than the bundle's render: ${JSON.stringify(report.first)}`);
          timing(report.first.settleMs - report.first.bundleMs - report.first.handlerMs >= 5, `settle waits for the main thread after the delivery: ${JSON.stringify(report.first)}`);
        }
        timing(report.types.ka.settleMs.p50 >= 10, `a keepalive settles two animation frames after receipt, not when its handler returns: ${JSON.stringify(report.types.ka.settleMs)}`);
        const content = frames.length - report.types.ka.count;
        if (pacing.fast) {
          timing(report.handoff.coalesced >= 1, `back-to-back, frames queue together and the whole-state kinds coalesce: ${JSON.stringify(report.handoff)}`);
          timing(report.handoff.delivered < content, "so fewer deliveries than content frames");
        } else {
          assert.equal(report.types.feed.delivered + report.types.feed.coalesced, 1);
          timing(report.types["delta:feed"].delivered >= report.types["delta:feed"].count / 2, `at ${pacing.gapMs} ms gaps most deltas reach the bundle on their own: ${JSON.stringify(report.types["delta:feed"])}`);
          timing(report.types["delta:feed"].bundleMs.p50 < report.types.feed.settleMs.max, "a delta's render is cheaper than the whole board's settle");
        }
        assert.equal(report.end.afterGc, true);
        if (app === "feed") timing(report.end.heapUsed < report.end.heapBeforeGc, `the forced collection freed the replay's garbage: ${report.end.heapBeforeGc} before, ${report.end.heapUsed} after`);
        assert.equal(report.first.type, app === "feed" ? "feed" : "data");
        assert.ok(report.first.bytes > 1000);
        assert.ok(["delivered", "coalesced"].includes(report.first.handoff), `the first content frame reached the bundle: ${JSON.stringify(report.first)}`);
        if (report.first.handoff === "delivered") assert.ok(report.first.bundleMs > 0 && report.first.settleMs >= report.first.bundleMs, JSON.stringify(report.first));
        assert.deepEqual(report.console.errors, [], "console errors");
        assert.deepEqual(report.console.pageErrors, [], "uncaught exceptions");
        assert.deepEqual(report.console.failedResources, [], "every page resource served");
        assert.equal(report.clientMessages.ready, 1, "the bundle's handshake, once");
        assert.ok(!report.clientMessages.needSlot, "the pane applied every frame without asking for a resync");
        assert.ok(report.end.heapUsed > 0);
        assert.ok(report.end.domElements > 20);
        assert.ok(report.end.cdpNodes >= report.end.domElements);
        assert.equal(typeof report.loaf.count, "number");
        assert.ok(["long-animation-frame", "longtask"].includes(report.loaf.kind), "an observer kind was chosen");
        if (app === "feed") assert.ok(report.end.domElements > 40 * 5, "forty cards render as more than a handful of elements each");
        const disk = JSON.parse(fs.readFileSync(jsonOut, "utf8"));
        assert.equal(disk.frames.total, frames.length, "--json wrote the same report");
        const text = renderReport(report);
        assert.match(text, new RegExp(`^ui-bench ${app}: ${frames.length} frames, [\\d.]+ KB, replay [\\d.]+ ms \\(${pacing.fast ? "back-to-back" : `${pacing.gapMs} ms gaps`}\\)`));
        assert.match(text, /console: 0 errors, 0 uncaught exceptions/);
        assert.match(text, /handoff: the shim hands frames to the bundle in its own flush task/);
        // The attribution labels the instrument's wrappers for what runs inside them, never for its file, and
        // the handler row no longer claims the bundle. A delivery of 50 ms or more is a long animation frame,
        // so when one occurred its row names the flush task (the timeline stream is small enough to have none).
        assert.ok(!report.loaf.topScripts.some((s) => /ui-bench-instrument/.test(s.key)), `no attribution row under the instrument's file: ${JSON.stringify(report.loaf.topScripts)}`);
        assert.doesNotMatch(text, /message handler \(shim \+ bundle\)/);
        if (report.loaf.kind === "long-animation-frame" && Object.values(report.types).some((s) => s.bundleMs.max >= 50)) {
          assert.match(text, /bundle handoff \(shim flush \+ bundle\) <MessagePort\.onmessage>/, `a delivery of 50 ms or more is a long animation frame, and its attribution names the flush task: ${JSON.stringify(report.loaf.topScripts)}`);
        }
        if (pacing.fast) assert.match(text, /note: back-to-back replay; frames queued together coalesce/);
        else assert.doesNotMatch(text, /note: back-to-back/);
        assert.doesNotMatch(text, /warning:/);
        assert.doesNotMatch(text, /not caused by a wire frame/);
        if (cpuProfile) {
          const cp = report.cpuProfile;
          assert.ok(cp, "a CPU profile was taken");
          assert.deepEqual(cp.files, [cpuProfile]);
          const raw = JSON.parse(fs.readFileSync(cpuProfile, "utf8"));
          for (const k of ["nodes", "startTime", "endTime", "samples", "timeDeltas"]) assert.ok(k in raw, `.cpuprofile has ${k}`);
          assert.ok(raw.samples.length > 0, "the profile holds samples");
          // The sampler is a thread of its own: how densely it sampled, and whether its clock could be refined against
          // the wrappers' windows, are the scheduler's on a loaded runner (timing relations, like the ones above).
          timing(raw.samples.length > 100, `enough samples (${raw.samples.length})`);
          assert.equal(cp.samplingIntervalUs, 500);
          const deltas = raw.timeDeltas.slice().sort((a, b) => a - b);
          timing(deltas[deltas.length >> 1] < 750, `the profiler sampled at the requested 500 us, not V8's default millisecond (median delta ${deltas[deltas.length >> 1]} us)`);
          timing(cp.alignRefined === true, "the wrappers' samples refined the clock alignment");
          assert.ok(cp.alignMs > 0 && cp.alignMs <= cp.alignBoundMs, `the refined uncertainty (${cp.alignMs} ms) is within the bracketing bound (${cp.alignBoundMs} ms)`);
          timing(cp.wrapperSamplesInWindows >= 0.9, `the wrappers' samples fall inside the handler, flush and delivery windows (${cp.wrapperSamplesInWindows})`);
          assert.equal(cp.sourceMaps, true);
          assert.deepEqual(cp.sourceMapsMissing, [], "every profiled bundle had its map beside it");
          assert.ok(cp.sourceMapsLoaded.includes("feed.js"), cp.sourceMapsLoaded.join(", "));
          assert.ok(!fs.readFileSync(cpuProfile, "utf8").includes("token="), "the written profile carries no serve token (the page URL's query is dropped from every frame)");
          assert.ok(cp.topSelf.some((f) => /^ui\/webview\/\S+\.ts:\d+$/.test(f.src || "")), `source positions resolved: ${cp.topSelf.slice(0, 5).map((f) => f.src).join(", ")}`);
          assert.ok(cp.topSelf.slice(0, 5).some((f) => f.lines && f.lines.length && f.lines[0].src), `the hottest functions name their lines: ${JSON.stringify(cp.topSelf[0].lines)}`);
          assert.ok(cp.windows.every((w) => w.topSelf.every((f) => !f.lines)), "windows carry no line split");
          assert.ok(cp.topSelf.length > 5 && cp.topTotal.length > 5);
          assert.ok(cp.topSelf.some((f) => /^feed\.js:[^:]+:\d+$/.test(f.key)), `functions of the feed bundle are named: ${cp.topSelf.slice(0, 5).map((f) => f.key).join(", ")}`);
          assert.ok(cp.topSelf.every((f) => !/^\((program|idle|garbage collector|root)\)$/.test(f.key)), "bookkeeping nodes are not ranked");
          assert.ok(cp.topTotal[0].totalMs >= cp.topSelf[0].selfMs);
          const labels = cp.windows.map((w) => w.label);
          assert.ok(labels.some((l) => l.split(" + ").includes("first content frame")), labels.join(", "));
          assert.ok(labels.some((l) => l.split(" + ").includes("largest delta:feed")), labels.join(", "));
          const first = cp.windows.find((w) => w.label.split(" + ").includes("first content frame"));
          assert.equal(first.type, "feed");
          assert.equal(first.window, "delivery", "the window is the delivery that carried the frame, where the bundle's render runs");
          timing(first.windowMs > first.handlerMs, `the delivery (${first.windowMs} ms) outweighs the shim's handler (${first.handlerMs} ms)`);
          timing(first.samples > 0 && first.topSelf.length > 0, "the first frame's delivery window has samples");
          timing(Math.abs(first.sampledMs - first.windowMs) <= Math.max(5, first.windowMs * 0.25), `the window's sampled time (${first.sampledMs}) tracks the delivery's time (${first.windowMs})`);
          if (first.samples > 0) assert.ok(first.topSelf.some((f) => /^feed\.js:/.test(f.key)), `the bundle's functions are what the window holds: ${first.topSelf.slice(0, 3).map((f) => f.key).join(", ")}`);
          assert.match(text, /cpu profile: \d+ samples over/);
          assert.match(text, /window: first content frame[^\n]*\(feed, [^\n]*: delivery [\d.]+ ms, shim handler [\d.]+ ms/);
        } else {
          assert.equal(report.cpuProfile, undefined);
        }
      } finally {
        fs.rmSync(tmp, { recursive: true, force: true });
      }
    });
}

test("replay: a delta the shim refuses stays the shim's, and a frame the pane throws on is reported as an uncaught exception", { ...gate(skipReplay), timeout: 180_000 }, async () => {
  requireOrSkip(skipReplay);
  // The synthetic feed stream with its LAST delta's base set to a revision the pane does not hold (so the shim
  // answers needSlot, which nothing answers in a replay, and only that one delta is refused), then one more full
  // frame whose first cards lack the recency tint feed.ts destructures. The pane's handler runs inside the
  // MessageEvent federation re-dispatches, and a listener's exception is reported to the page, not thrown to the
  // dispatcher, so it surfaces as an uncaught exception and not as a delivery error (the instrument test above
  // covers a delivery that throws to its caller).
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-replay-bad-"));
  try {
    const frames = synthesizeFrames("feed", 40);
    const deltas = frames.map((f, i) => [f, i]).filter(([f]) => classifyFrame(f.data) === "delta:feed");
    const [last, li] = deltas[deltas.length - 1];
    const refused = JSON.parse(last.data);
    refused.base = 99;
    frames[li] = { t: last.t, data: JSON.stringify(refused) };
    const full = JSON.parse(frames[0].data);
    const bad = { ...full, now: full.now + 100, buildId: 999, asks: full.asks.slice(0, 3).map((a) => { const c = { ...a }; delete c.trgb; return c; }) };
    bad._keys = feedKeys(bad);
    frames.push({ t: frames[frames.length - 1].t + 500, data: JSON.stringify(bad) });
    const file = path.join(tmp, "feed.jsonl");
    writeFrames(file, { synthetic: true, app: "feed" }, frames);
    const report = await replay({ app: "feed", framesFile: file, gapMs: 50, log: () => {} });
    assert.equal(report.clientMessages.needSlot, 1, "the pane asked for one resync");
    const deltaRow = report.types["delta:feed"];
    assert.equal(deltaRow.shim, 1, "the refused delta is the shim's alone");
    assert.equal(report.perFrame[li].handoff, "shim", `the refused one is the delta whose base the pane does not hold: ${JSON.stringify(report.perFrame[li])}`);
    // Every other delta reached the bundle, on its own or coalesced into a newer delivery of the feed's state: the
    // shim applies a delta and queues the whole result, and a newer whole-state entry replaces an older one still
    // queued (kernel.py _shim WHOLE), so a delta whose flush has not run when the next frame's handler runs rides
    // in that frame's delivery. At 50 ms gaps that takes a stalled renderer, which a loaded runner supplies now
    // and then (one delta of 23 on one CI run), so the split between delivered and coalesced is the scheduler's;
    // the sum is the invariant.
    assert.equal(deltaRow.delivered + deltaRow.coalesced, deltaRow.count - 1, `every other delta reached the bundle, on its own or coalesced into a newer delivery: ${JSON.stringify(deltaRow)}`);
    assert.equal(report.types.feed.count, 2);
    // The first full frame can coalesce the same way; the throwing frame is the stream's last, so nothing newer can
    // replace it: it was delivered on its own (the shim does not know the pane threw).
    assert.equal(report.types.feed.delivered + report.types.feed.coalesced, 2, `both full frames reached the bundle: ${JSON.stringify(report.types.feed)}`);
    assert.equal(report.perFrame[frames.length - 1].handoff, "delivered", `the throwing frame was delivered on its own: ${JSON.stringify(report.perFrame[frames.length - 1])}`);
    assert.equal(report.handoff.queued, 0);
    assert.equal(report.handoff.delivered + report.handoff.coalesced + report.handoff.shim, frames.length, "every frame accounted for");
    assert.equal(report.handoff.queueDrained, true);
    assert.ok(report.console.pageErrors.length >= 1, `the pane's exception was collected: ${JSON.stringify(report.console)}`);
    assert.match(report.console.pageErrors[0], /iterable|trgb|undefined/);
    assert.deepEqual(report.handoff.deliveryErrors, [], "the listener's exception did not propagate to the delivery");
    const text = renderReport(report);
    assert.match(text, /console: 0 errors, 1 uncaught exceptions/);
    assert.match(text, /\n  uncaught: .*iterable/);
    assert.match(text, /messages the pane sent: .*needSlot 1/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("replay: --iters pools runs and --cpu-throttle slows the page", { ...gate(skipReplay), timeout: 180_000 }, async (t) => {
  requireOrSkip(skipReplay);
  const timing = timingCheck(t);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "romp-ui-bench-replay-iters-"));
  try {
    const frames = synthesizeFrames("timeline", 40);
    const file = path.join(tmp, "timeline.jsonl");
    writeFrames(file, { synthetic: true, app: "timeline" }, frames);
    const pooled = await replay({ app: "timeline", framesFile: file, fast: true, iters: 2, log: () => {} });
    assert.equal(pooled.iters, 2);
    assert.equal(pooled.perFrame, undefined, "pooled runs carry no single per-frame list");
    assert.equal(pooled.types.bars.count, 1, "a per-run count");
    assert.equal(pooled.types.bars.settleMs.n, 2, "samples pooled over both runs");
    assert.equal(pooled.types.data.count, 4);
    assert.equal(pooled.types.data.settleMs.n, 8);
    assert.match(renderReport(pooled), /^ui-bench timeline: 36 frames, [\d.]+ KB, replay [\d.]+ ms \(back-to-back\), cpu x1, 2 iterations/);
    // Chromium's CPU throttle is a deterministic emulation (the renderer's main thread runs a quarter of the time),
    // so the cumulative script time of the same replay grows by about the rate; the floor is half of it. Measured
    // 1.7 to 6.2 under load: a host that already deschedules the thread shrinks the wall-clock ratio, so the floor
    // is a timing relation, asserted under ROMP_UI_BENCH_TIMING and a diagnostic otherwise (review find, 2026-09-08).
    const slow = await replay({ app: "timeline", framesFile: file, fast: true, iters: 1, cpuThrottle: 4, log: () => {} });
    assert.equal(slow.cpuThrottle, 4);
    assert.match(renderReport(slow), /cpu x4, 1 iteration,/);
    assert.ok(slow.end.scriptMs > 0 && pooled.end.scriptMs > 0, "script time was measured in both runs");
    timing(slow.end.scriptMs >= 1.5 * pooled.end.scriptMs, `script time under a 4x throttle (${slow.end.scriptMs} ms) against unthrottled (${pooled.end.scriptMs} ms)`);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
