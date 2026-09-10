// T301 (the user 2026-09-10): the rail's API-health dot covers EVERY connected kernel, merged as per-host maps
// (worst state wins, every machine named, no count or clock compared across hosts), and the popup reads what
// happened in plain words: traffic with no errors is fine and blue however the state machine words it, no
// traffic is quiet and gray, and "unknown" is never the headline.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mergeFrames, readHistory, mergeHistories, documentSeries, frameDot, machineText, HistoryDoc } from "./api-health-merge";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");

const OK = { state: "ok", cls: "", text: "ok", waiting: 0 };
const STORM = { state: "degraded", cls: "429", text: "rate limited · 2 waiting", waiting: 2, since: 1000 };
const PAUSED = { state: "paused", cls: "", reason: "limit", text: "paused: usage limit · 1 waiting", waiting: 1 };

test("worst state wins across machines: one storm anywhere makes the dot red, and that host is named as the cause", () => {
  const m = mergeFrames(OK, { PEERHOST: OK, TESTHOST: STORM });
  assert.equal(m.dot, "errors");
  assert.equal(m.worst, "TESTHOST");
  assert.equal(m.n, 3, "the local machine and two peers");
  assert.deepEqual(m.machines.map((x) => x.host), ["", "PEERHOST", "TESTHOST"], "local first, then by name");
  assert.deepEqual(m.machines.map((x) => x.dot), ["fine", "fine", "errors"]);
  assert.equal(m.machines[2].text, "TESTHOST: rate limited · 2 waiting");
});

test("a pause on the local machine is an error state too, and every machine keeps its own line", () => {
  const m = mergeFrames(PAUSED, { TESTHOST: OK });
  assert.equal(m.dot, "errors");
  assert.equal(m.worst, "", "the local machine set it");
  assert.equal(m.machines[0].text, "this machine: paused until the usage limit resets · 1 waiting");
  assert.equal(m.machines[1].text, "TESTHOST: fine");
});

test("every machine fine and quiet reads gray; fine with traffic anywhere reads the accent; a stale host is marked", () => {
  const QUIET = readHistory(doc({})), FINE = readHistory(doc({ req: 40, ok: 40, state: "healthy" }));
  const quiet = mergeFrames(OK, { TESTHOST: { ...OK, stale: true } }, { "": QUIET, TESTHOST: QUIET });
  assert.equal(quiet.dot, "quiet");
  assert.equal(quiet.machines[0].text, "this machine: no API traffic");
  assert.equal(quiet.machines[1].stale, true);
  const fine = mergeFrames(OK, { TESTHOST: OK }, { "": QUIET, TESTHOST: FINE });
  assert.equal(fine.dot, "fine", "traffic on one machine and no errors anywhere is the accent");
  assert.equal(fine.machines[1].text, "TESTHOST: fine · 40 requests in the last 15 min, all succeeded");
  // the frame's own quiet flag (no event in the kernel's longest window) reads gray before any history is read
  const q = mergeFrames({ ...OK, quiet: true }, { TESTHOST: { ...OK, quiet: true } });
  assert.equal(q.dot, "quiet");
  assert.equal(q.machines[1].text, "TESTHOST: no API traffic");
  assert.equal(mergeFrames({ ...OK, quiet: true }, { TESTHOST: OK }).dot, "fine", "a peer whose frame carries no flag is not assumed quiet");
  assert.equal(mergeFrames(OK, {}).dot, "fine", "with no history read yet the frame's own word stands: fine");
  assert.equal(frameDot(STORM, FINE), "errors");
  assert.equal(frameDot(OK, QUIET), "quiet");
  // a storm in the WINDOW with nothing waiting right now: the frame says ok, the reading says errors, the dot is red
  const stormy = readHistory(doc({ req: 30, ok: 10, r429: 20, state: "thrashing" }));
  assert.equal(frameDot(OK, stormy), "errors");
  const m = mergeFrames(OK, { TESTHOST: OK }, { "": FINE, TESTHOST: stormy });
  assert.equal(m.dot, "errors");
  assert.equal(m.worst, "TESTHOST");
  assert.match(m.machines[1].text, /^TESTHOST: Rate-limit storm: 20 rate-limited attempts/);
  assert.equal(machineText("PEERHOST", { state: "degraded", cls: "offline", waiting: 3 }), "PEERHOST: offline · 3 waiting");
});

test("no cross-host arithmetic: the merge never adds a waiting count or compares a since across hosts", () => {
  const SRC = read("ui", "webview", "api-health-merge.ts");
  // a count added to another host's count, or one host's since compared with another's, is the shape the rule bans
  assert.doesNotMatch(SRC, /\+=\s*[a-z.]*\.waiting\b|\.waiting\s*\+\s*[a-z.]*\.waiting\b|\.since\s*[<>]=?\s*[a-z.]*\.since\b/, "per-host maps in, per-host lines out");
  const m = mergeFrames({ ...STORM, waiting: 2 }, { TESTHOST: { ...STORM, waiting: 5 } });
  assert.match(m.machines[0].text, /2 waiting/);
  assert.match(m.machines[1].text, /5 waiting/);
  assert.equal(m.machines.filter((x) => /7 waiting/.test(x.text)).length, 0, "no summed count anywhere");
});

function doc(over: Partial<HistoryDoc> & { req?: number; ok?: number; r429?: number; r5xx?: number; none?: number; state?: string }): HistoryDoc {
  const req = over.req || 0, ok = over.ok || 0, r429 = over.r429 || 0, r5xx = over.r5xx || 0, none = over.none || 0;
  return {
    asOf: 2000, bootAt: 100, config: { windows: [60, 300, 900], minRequests: 10 },
    overall: { state: over.state || "unknown", worstBucket: req || none ? "key:helper|fable" : null },
    buckets: req || none ? { "key:helper|fable": { state: over.state || "unknown", windows: {
      "900": { requests: req, ok, rateLimited: r429, serverErrors: r5xx, noStatus: none, gaveUp: 0 } } } } : {},
  };
}

test("the reading rule: traffic and no errors reads what happened, fine and blue, with the trend caveat as a sub-line", () => {
  const r = readHistory(doc({ req: 4, ok: 4, state: "unknown" }));
  assert.equal(r.level, "fine");
  assert.equal(r.headline, "4 requests in the last 15 min, all succeeded.");
  assert.equal(r.sub, "Too few requests to call a trend yet.");
  assert.equal(r.traffic, true);
  assert.doesNotMatch(r.headline, /unknown/);
  const healthy = readHistory(doc({ req: 40, ok: 40, state: "healthy" }));
  assert.equal(healthy.level, "fine");
  assert.equal(healthy.sub, null, "the machine has a verdict: no caveat");
});

test("no traffic at all reads quiet and gray; errors read the failures in plain words with the machine's own word", () => {
  const q = readHistory(doc({}));
  assert.equal(q.level, "quiet");
  assert.equal(q.headline, "No API traffic in the last 15 min.");
  assert.equal(q.traffic, false);
  const s = readHistory(doc({ req: 30, ok: 10, r429: 20, state: "thrashing" }));
  assert.equal(s.level, "errors");
  assert.equal(s.headline, "Rate-limit storm: 20 rate-limited attempts among 30 attempts in the last 15 min.");
  const d = readHistory(doc({ req: 12, ok: 9, r5xx: 3, state: "degraded" }));
  assert.equal(d.headline, "The API is failing: 3 server errors among 12 attempts in the last 15 min.");
  const off = readHistory(doc({ none: 5 }));
  assert.equal(off.level, "errors");
  assert.equal(off.headline, "Errors: 5 attempts with no connection among 5 attempts in the last 15 min.");
  assert.equal(readHistory(null).level, "quiet", "no document yet reads as no traffic, never as a word the user must decode");
});

test("histories merge as per-host rows: each machine keeps its sentence, the worst level leads, a failed read is its own line", () => {
  const m = mergeHistories({ "": doc({ req: 4, ok: 4 }), TESTHOST: doc({ req: 30, ok: 10, r429: 20, state: "thrashing" }), PEERHOST: { error: "HTTP 502" } });
  assert.equal(m.level, "errors");
  assert.deepEqual(m.rows.map((r) => r.host), ["", "PEERHOST", "TESTHOST"]);
  assert.equal(m.rows[0].reading!.headline, "4 requests in the last 15 min, all succeeded.");
  assert.equal(m.rows[1].error, "HTTP 502");
  assert.match(m.rows[2].reading!.headline, /^Rate-limit storm/);
  assert.deepEqual(m.traffic, { "": true, PEERHOST: null, TESTHOST: true });
  assert.equal(m.readings[""]!.level, "fine");
  assert.equal(m.readings.PEERHOST, null, "a failed read is no reading: the frame's word stands for that host");
  assert.equal(m.rows.filter((r) => r.reading && /34 requests/.test(r.reading.headline)).length, 0, "4 + 30 is never said");
});

test("a document's per-minute series sums its OWN buckets bin by bin, and none is null", () => {
  const d: HistoryDoc = { buckets: {
    a: { series: { binS: 60, from: 0, ok: [1, 2, 3], rateLimited: [0, 1, 0], serverErrors: [0, 0, 1], noStatus: [0, 0, 0], other: [0, 0, 0] } },
    b: { series: { binS: 60, from: 0, ok: [1, 0, 0], rateLimited: [2, 0, 0], serverErrors: [0, 0, 0], noStatus: [1, 0, 0], other: [0, 0, 0] } },
  } };
  const s = documentSeries(d)!;
  assert.deepEqual(s.ok, [2, 2, 3]);
  assert.deepEqual(s.rateLimited, [2, 1, 0]);
  assert.deepEqual(s.serverErrors, [0, 0, 1]);
  assert.deepEqual(s.noStatus, [1, 0, 0]);
  assert.equal(documentSeries({ buckets: { a: {} } }), null);
});

test("the shell loads the merge module before its API-health script and the bundle lists it", () => {
  const KERNEL = read("kernel", "kernel.py");
  const i = KERNEL.indexOf("/dist/api-health-global.js?v=%d"), j = KERNEL.indexOf('"<script>" + _LANDING_APIH_JS + "</script>"');
  assert.ok(i > 0 && j > i, "the global module is included, and before the script that reads it");
  assert.match(read("vscode-extension", "esbuild.js"), /"\.\.\/ui\/webview\/api-health-global\.ts"/);
  assert.match(read("ui", "webview", "api-health-global.ts"), /__rompApiHealthMerge = \{\s*mergeFrames, readHistory, mergeHistories, documentSeries, frameDot, machineText,/);
});
