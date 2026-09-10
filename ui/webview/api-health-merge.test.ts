// T301 (the user 2026-09-10): the rail's API-health dot covers EVERY connected kernel, merged as per-host maps
// (worst state wins, every machine named, no count or clock compared across hosts). The dot follows the FRAMES
// (each kernel's quiet and errs flags, pushed on change); a history reading words a line and never colours the dot.
// T316 (the user's design follow-up, 2026-09-10): a machine's line is its counts over the window, each class in its
// own colour, never a verdict word; the histograms come from the ledger's tiers; the as-of stamp reads as an age.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { mergeFrames, readHistory, mergeHistories, documentSeries, documentLedger, rebin, frameDot, machineText, machineLine,
         countsParts, agoWords, windowWords, HistoryDoc, LedgerTier } from "./api-health-merge";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");

const OK = { state: "ok", cls: "", text: "ok", waiting: 0, quiet: false, errs: 0 };
const STORM = { state: "degraded", cls: "429", text: "rate limited · 2 waiting", waiting: 2, since: 1000, quiet: false, errs: 12 };
const PAUSED = { state: "paused", cls: "", reason: "limit", text: "paused: usage limit · 1 waiting", waiting: 1 };
const QUIET = { ...OK, quiet: true };

test("worst state wins across machines: one storm anywhere makes the dot red, and that host is named as the cause", () => {
  const m = mergeFrames(OK, { PEERHOST: OK, TESTHOST: STORM });
  assert.equal(m.dot, "errors");
  assert.equal(m.worst, "TESTHOST");
  assert.equal(m.n, 3, "the local machine and two peers");
  assert.deepEqual(m.machines.map((x) => x.host), ["", "PEERHOST", "TESTHOST"], "local first, then by name");
  assert.deepEqual(m.machines.map((x) => x.dot), ["fine", "fine", "errors"]);
  assert.equal(m.machines[2].text, "TESTHOST: rate limited · 2 waiting");
  assert.deepEqual(m.machines[2].parts, [{ text: "rate limited · 2 waiting", kind: "plain" }], "the kernel's own words, uncoloured");
});

test("a pause on the local machine is an error state too, and every machine keeps its own line", () => {
  const m = mergeFrames(PAUSED, { TESTHOST: OK });
  assert.equal(m.dot, "errors");
  assert.equal(m.worst, "", "the local machine set it");
  assert.equal(m.machines[0].text, "this machine: paused until the usage limit resets · 1 waiting");
  assert.equal(m.machines[1].text, "TESTHOST", "a fine frame with no reading yet says nothing after the name: no verdict word");
});

test("the local machine is named by its kernel's own name when the frame carries one (T316), 'this machine' only without", () => {
  const m = mergeFrames({ ...OK, host: "TESTHOST" }, { PEERHOST: OK });
  assert.equal(m.machines[0].name, "TESTHOST");
  assert.equal(m.machines[0].host, "", "the key stays '': the histories are keyed by it");
  assert.equal(machineText("", { ...QUIET, host: "TESTHOST" }), "TESTHOST: no API traffic");
  assert.equal(machineText("", QUIET), "this machine: no API traffic");
  assert.equal(machineLine("", QUIET, null, "TESTHOST").name, "TESTHOST", "the merge passes the frame's host down");
});

test("the dot is the frames' alone: quiet everywhere reads gray, traffic anywhere the accent, failed attempts in a window red", () => {
  assert.equal(mergeFrames(QUIET, { TESTHOST: QUIET }).dot, "quiet");
  assert.equal(mergeFrames(QUIET, { TESTHOST: QUIET }).machines[1].text, "TESTHOST: no API traffic");
  assert.equal(mergeFrames(QUIET, { TESTHOST: OK }).dot, "fine", "one machine with traffic and no errors: the accent");
  assert.equal(mergeFrames(QUIET, { TESTHOST: { state: "ok" } }).dot, "fine", "a peer whose frame carries no flag (an older kernel) is not assumed quiet");
  assert.equal(mergeFrames({ state: "ok" }, {}).dot, "fine", "a frame without the flags stands on its state word");
  const m = mergeFrames(OK, { TESTHOST: { ...OK, errs: 20 } });
  assert.equal(m.dot, "errors");
  assert.equal(m.worst, "TESTHOST");
  assert.equal(m.machines[1].text, "TESTHOST: 20 failed attempts in the last 15 min", "before its history is read, the frame's own count");
  assert.equal(frameDot({ ...OK, errs: 1 }), "errors");
  assert.equal(frameDot(QUIET), "quiet");
  assert.equal(frameDot(STORM), "errors");
  assert.equal(machineText("PEERHOST", { state: "degraded", cls: "offline", waiting: 3 }), "PEERHOST: offline · 3 waiting");
});

test("a machine's line is its counts in their colours (T316): successes in the accent, 429s red, 5xx magenta, the rest gray; no verdict word", () => {
  const FINE = readHistory(doc({ ok: 67 })), MIXED = readHistory(doc({ ok: 67, r429: 12, r5xx: 4 })), NONE = readHistory(doc({}));
  const m = mergeFrames(OK, { TESTHOST: OK, PEERHOST: OK }, { "": NONE, PEERHOST: FINE, TESTHOST: MIXED });
  assert.equal(m.machines[0].text, "this machine: no API traffic");
  assert.equal(m.machines[1].text, "PEERHOST: 67 successful requests");
  assert.deepEqual(m.machines[1].parts, [{ text: "67 successful requests", kind: "ok" }]);
  assert.equal(m.machines[2].text, "TESTHOST: 67 successful requests · 12 429s · 4 5xx");
  assert.deepEqual(m.machines[2].parts.map((p) => p.kind), ["ok", "r429", "r5xx"]);
  assert.deepEqual(countsParts({ ok: 0, r429: 1, r5xx: 1, none: 3, other: 2, attempts: 7, failures: 7 }).map((p) => p.text),
                   ["1 429", "1 5xx", "3 no connection", "2 other errors"], "failure classes only when present, singular where it is one");
  assert.equal(m.dot, "fine", "a reading colours words, never the dot");
  for (const x of m.machines) for (const w of ["fine", "Fine", "all succeeded", "in the last 15 min", "storm", "unknown"]) assert.ok(!x.text.includes(w), x.text + " must not say " + w);
});

test("a stale reading cannot pin the dot: the frame is the newer word", () => {
  const stormy = readHistory(doc({ ok: 10, r429: 20 })), NONE = readHistory(doc({}));
  assert.equal(mergeFrames(OK, { TESTHOST: OK }, { TESTHOST: stormy }).dot, "fine");
  assert.equal(mergeFrames(OK, {}, { "": NONE }).dot, "fine");
  const m = mergeFrames(OK, { TESTHOST: { ...OK, errs: 20 } }, { TESTHOST: stormy });
  assert.equal(m.dot, "errors");
  assert.equal(m.machines[1].text, "TESTHOST: 10 successful requests · 20 429s", "the reading's counts word the line, the frame's count colours the dot");
});

test("a machine whose tunnel is down or whose frame could not be read is named and has no say in the dot", () => {
  const down = mergeFrames(OK, { TESTHOST: { ...STORM, stale: true } });
  assert.equal(down.dot, "fine", "its last frame is old news: a storm heard before the link dropped does not keep the dot red");
  assert.equal(down.machines[1].stale, true);
  assert.equal(down.machines[1].dot, "quiet");
  assert.equal(down.machines[1].text, "TESTHOST: not reachable, last seen rate limited · 2 waiting");
  assert.equal(mergeFrames(QUIET, { TESTHOST: { ...OK, stale: true } }).dot, "quiet", "nor does a fine frame heard before the drop block gray");
  const refused = mergeFrames(OK, { TESTHOST: { ...OK, stale: true, fault: "HTTP 403" } });
  assert.equal(refused.dot, "fine");
  assert.equal(refused.machines[1].text, "TESTHOST: could not read its API health (HTTP 403)");
  assert.equal(mergeFrames(OK, { TESTHOST: { state: "", stale: true, fault: "HTTP 500" } }).machines[1].text, "TESTHOST: could not read its API health (HTTP 500)", "a fault before any frame was heard");
});

test("no cross-host arithmetic: the merge never adds a waiting count or compares a since across hosts", () => {
  const SRC = read("ui", "webview", "api-health-merge.ts");
  assert.doesNotMatch(SRC, /\+=\s*[a-z.]*\.waiting\b|\.waiting\s*\+\s*[a-z.]*\.waiting\b|\.since\s*[<>]=?\s*[a-z.]*\.since\b|\+=\s*[a-z.]*\.errs\b/, "per-host maps in, per-host lines out");
  const m = mergeFrames({ ...STORM, waiting: 2 }, { TESTHOST: { ...STORM, waiting: 5 } });
  assert.match(m.machines[0].text, /2 waiting/);
  assert.match(m.machines[1].text, /5 waiting/);
  assert.equal(m.machines.filter((x) => /7 waiting/.test(x.text)).length, 0, "no summed count anywhere");
});

/** A document whose ledger's five-minute tier holds the given counts, spread over the newest bins. */
function tier(binS: number, n: number, counts: { ok?: number; r429?: number; r5xx?: number; none?: number; other?: number }): LedgerTier {
  const z = () => new Array(n).fill(0);
  const t: LedgerTier = { binS, from: 1000000, ok: z(), rateLimited: z(), serverErrors: z(), noStatus: z(), other: z() };
  const put = (arr: number[], v: number) => { for (let i = 0; i < v; i++) arr[n - 1 - (i % 3)] += 1; };
  put(t.ok, counts.ok || 0); put(t.rateLimited, counts.r429 || 0); put(t.serverErrors, counts.r5xx || 0); put(t.noStatus, counts.none || 0); put(t.other, counts.other || 0);
  return t;
}
function doc(c: { ok?: number; r429?: number; r5xx?: number; none?: number; other?: number; state?: string }): HistoryDoc {
  const any = (c.ok || 0) + (c.r429 || 0) + (c.r5xx || 0) + (c.none || 0) + (c.other || 0);
  return {
    asOf: 2000, bootAt: 100, config: { windows: [60, 300, 900], minRequests: 10 },
    overall: { state: c.state || "unknown", worstBucket: any ? "key:helper|fable" : null },
    buckets: { "key:helper|fable": { state: c.state || "unknown", windows: { "900": { requests: 0, ok: 0 } },
      ledger: { minute: tier(60, 60, {}), fiveMin: tier(300, 288, c), hour: tier(3600, 168, c) } } },
  };
}

test("the reading counts the ledger's day (T316): successes and failures over 24 hours, the window named once", () => {
  const r = readHistory(doc({ ok: 67, r429: 12, r5xx: 4, none: 1 }));
  assert.equal(r.level, "errors");
  assert.equal(r.windowS, 86400, "288 five-minute bins");
  assert.deepEqual(r.counts, { ok: 67, r429: 12, r5xx: 4, none: 1, other: 0, attempts: 84, failures: 17 });
  assert.equal(r.headline, "67 successful requests · 12 429s · 4 5xx · 1 no connection");
  assert.equal(windowWords(r.windowS), "last 24 hours");
  const fine = readHistory(doc({ ok: 40, state: "healthy" }));
  assert.equal(fine.level, "fine");
  assert.equal(fine.headline, "40 successful requests");
  const q = readHistory(doc({}));
  assert.equal(q.level, "quiet");
  assert.equal(q.headline, "no API traffic");
  assert.equal(q.traffic, false);
  assert.equal(readHistory(null).level, "quiet", "no document yet reads as no traffic, never as a word the user must decode");
  for (const x of [r, fine, q]) assert.ok(!/unknown|storm|failing|fine/i.test(x.headline), x.headline);
});

test("an older kernel's document (no ledger) is read over its longest window, and its series still draws", () => {
  const old: HistoryDoc = { asOf: 2000, config: { windows: [60, 300, 900] }, overall: { state: "thrashing", worstBucket: "k" },
    buckets: { k: { state: "thrashing", windows: { "900": { requests: 30, ok: 10, rateLimited: 20, serverErrors: 0, noStatus: 0, gaveUp: 1 } },
      series: { binS: 60, from: 0, ok: [1, 2], rateLimited: [3, 0], serverErrors: [0, 0], noStatus: [0, 0], other: [0, 0] } } } };
  const r = readHistory(old);
  assert.equal(r.windowS, 900);
  assert.equal(windowWords(r.windowS), "last 15 min");
  assert.equal(r.headline, "10 successful requests · 20 429s");
  assert.equal(documentLedger(old, "fiveMin"), null);
  assert.deepEqual(documentSeries(old)!.rateLimited, [3, 0]);
});

test("histories merge as per-host rows: each machine keeps its sentence, the worst level leads, a failed or pending read is its own row", () => {
  const m = mergeHistories({ "": doc({ ok: 4 }), TESTHOST: doc({ ok: 10, r429: 20 }), PEERHOST: { error: "HTTP 502" }, FARHOST: { pending: true } });
  assert.equal(m.level, "errors");
  assert.deepEqual(m.rows.map((r) => r.host), ["", "FARHOST", "PEERHOST", "TESTHOST"]);
  assert.equal(m.rows[0].reading!.headline, "4 successful requests");
  assert.equal(m.rows[1].pending, true, "a read still in flight is no reading and no error");
  assert.equal(m.rows[2].error, "HTTP 502");
  assert.equal(m.rows[3].reading!.headline, "10 successful requests · 20 429s");
  assert.deepEqual(m.traffic, { "": true, FARHOST: null, PEERHOST: null, TESTHOST: true });
  assert.equal(m.readings.PEERHOST, null, "a failed read is no reading: the frame's word stands for that host");
  assert.equal(m.rows.filter((r) => r.reading && /14 successful/.test(r.reading.headline)).length, 0, "4 + 10 is never said");
});

test("a document's ledger tier sums its OWN buckets bin by bin; rebin folds k bins into one bar, newest bins last", () => {
  const d: HistoryDoc = { buckets: {
    a: { ledger: { minute: { binS: 60, from: 0, ok: [1, 2, 3, 4], rateLimited: [0, 1, 0, 0], serverErrors: [0, 0, 1, 0], noStatus: [0, 0, 0, 0], other: [0, 0, 0, 1] } } },
    b: { ledger: { minute: { binS: 60, from: 0, ok: [1, 0, 0, 0], rateLimited: [2, 0, 0, 0], serverErrors: [0, 0, 0, 0], noStatus: [1, 0, 0, 0], other: [0, 0, 0, 0] } } },
  } };
  const t = documentLedger(d, "minute")!;
  assert.deepEqual(t.ok, [2, 2, 3, 4]);
  assert.deepEqual(t.rateLimited, [2, 1, 0, 0]);
  assert.deepEqual(t.noStatus, [1, 0, 0, 0]);
  assert.deepEqual(t.other, [0, 0, 0, 1]);
  assert.equal(documentLedger(d, "hour"), null, "a tier no bucket carries");
  const r = rebin(t, 3);
  assert.deepEqual(r.ok, [9], "one bar of the newest three bins; the partial oldest bin is dropped");
  assert.equal(r.binS, 180);
  assert.equal(r.from, 60);
  assert.deepEqual(rebin(tier(300, 288, { ok: 3 }), 3).ok.length, 96, "the day as quarter-hours");
});

test("the as-of stamp reads as an age in words, and a window as its plain name", () => {
  assert.equal(agoWords(3), "now");
  assert.equal(agoWords(70), "1 minute ago");
  assert.equal(agoWords(60 * 7), "7 minutes ago");
  assert.equal(agoWords(3600 + 60), "1 hour ago");
  assert.equal(agoWords(3600 * 5), "5 hours ago");
  assert.equal(agoWords(86400 * 2), "2 days ago");
  assert.equal(windowWords(3600), "last hour");
  assert.equal(windowWords(86400), "last 24 hours");
  assert.equal(windowWords(604800), "last 7 days");
});

test("the shell loads the merge module before its API-health script and the bundle lists it", () => {
  const KERNEL = read("kernel", "kernel.py");
  const i = KERNEL.indexOf("/dist/api-health-global.js?v=%d"), j = KERNEL.indexOf('"<script>" + _LANDING_APIH_JS + "</script>"');
  assert.ok(i > 0 && j > i, "the global module is included, and before the script that reads it");
  assert.match(read("vscode-extension", "esbuild.js"), /"\.\.\/ui\/webview\/api-health-global\.ts"/);
  assert.match(read("ui", "webview", "api-health-global.ts"), /__rompApiHealthMerge = \{\s*mergeFrames, readHistory, mergeHistories, documentSeries, documentLedger, rebin, frameDot, machineText, machineLine, countsParts, agoWords, windowWords,/);
});

test("the 5xx magenta is a token in both theme blocks of both sheets (theme parity), and the popup paints through it", () => {
  const STYLES = read("ui", "webview", "styles.css"), FEED = read("ui", "webview", "feed.css"), KERNEL = read("kernel", "kernel.py");
  const root = STYLES.slice(STYLES.indexOf(":root"), STYLES.indexOf("body.theme-light")), light = STYLES.slice(STYLES.indexOf("body.theme-light"));
  assert.match(root, /--st-5xx-bg: #c026d3; --st-5xx-fg: #ffffff;/);
  assert.match(light, /--st-5xx-bg: #A21CAF; --st-5xx-fg: #ffffff;/);
  assert.match(FEED, /--st-5xx-bg: #A21CAF; --st-5xx-fg: #ffffff;/, "mirrored where the feed mirrors the blocked red");
  assert.ok(KERNEL.includes(".ah-c-r5xx{color:var(--st-5xx-bg,#c026d3)}"), "a 5xx count wears the token");
  assert.ok(KERNEL.includes("['serverErrors','var(--st-5xx-bg,#c026d3)']"), "and so does the 5xx band of the bars");
});
