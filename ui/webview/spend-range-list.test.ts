// The spend modal's per-session list follows the chart's range (T353, the user 2026-09-11): the rows are summed
// from exactly the buckets the chart draws, so the list's total equals the chart's total for every range, in
// dollars, tokens and turns. The landing page has no jsdom harness: the pure functions are lifted from the kernel's
// inline JS (the spend-crosshair.test.ts idiom) and EXECUTED on a synthetic payload of 192 hourly and 90 daily
// buckets with three sessions, an unattributed tail and an older peer's fold. The kernel's half (every stack carries
// turns and keyUsd) is pinned in tests/test_spend_range_list.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const USAGE_JS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
const LINES = USAGE_JS.split("\n");

/** the function (or statement) starting at the line that begins with `start`, through the line where its braces close */
function fn(start: string): string {
  const i = LINES.findIndex((l) => l.startsWith(start));
  assert.ok(i >= 0, "the landing JS carries a line starting " + JSON.stringify(start));
  let depth = 0, j = i;
  for (; j < LINES.length; j++) {
    let q: string | null = null;
    for (const ch of LINES[j]) {
      if (q) { if (ch === q) q = null; continue; }
      if (ch === "'" || ch === '"') q = ch;
      else if (ch === "{") depth++;
      else if (ch === "}") depth--;
    }
    if (depth <= 0 && j >= i) break;
  }
  const src = LINES.slice(i, j + 1).join("\n");
  assert.ok(!src.includes("\\"), "no backslash escape in an extracted block (the file text is a Python string): " + start);
  return src;
}

const SRC = ["var SP_RANGE_BUCKETS=", "function spSeries(d){", "function spTail(ser,keep){", "function spKey(d,s){",
  "function spSumArr(a){", "function spRound4(v){", "function spRangeView(d){", "function spRangeWords(ser){",
  "function spOrdered(d){", "function spRows(d){", "function spStackOrderRows(d,stacks,rows){",
  "function spStacks(d,ser,model){"].map(fn).join("\n");

type Payload = Record<string, any>;
function lift(range: string, order = "spend", merge = false) {
  const SP = { range, measure: "usd", order, merge };
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function("SP", "spApplyViewOrder", "spViewOrder",
    SRC + "\n; return {spSeries, spRangeView, spRangeWords, spRows, spStacks, SP_RANGE_BUCKETS};")(SP, (s: string[]) => s, () => []);
}

/** 192 hourly buckets and 90 daily ones; web spends every bucket, api every other, tests only in the OLDEST hours (so the
 *  day view has no row for it), an unattributed tail and an older peer's fold in the hours */
function payload(): Payload {
  const H = 192, D = 90;
  const hk = Array.from({ length: H }, (_, i) => "h" + i), dk = Array.from({ length: D }, (_, i) => "d" + i);
  const arr = (n: number, f: (i: number) => number) => Array.from({ length: n }, (_, i) => f(i));
  const web = { kind: "sid", sid: "s-web", name: "web", bg: "#1EA1EB", live: true };
  const api = { kind: "sid", sid: "s-api", name: "api", bg: "#54B204", live: true };
  const tests = { kind: "sid", sid: "s-tests", name: "tests", bg: "", live: false };
  const hours = { keys: hk, epochs: hk.map((_, i) => 1000 + i), stacks: [
    { ...web, usd: arr(H, (i) => 1 + (i % 3) * 0.25), tok: arr(H, () => 20000), turns: arr(H, () => 2), keyUsd: arr(H, (i) => (i % 2 ? 0.5 : 0)) },
    { ...api, usd: arr(H, (i) => (i % 2 ? 0.5 : 0)), tok: arr(H, (i) => (i % 2 ? 8000 : 0)), turns: arr(H, (i) => (i % 2 ? 1 : 0)), keyUsd: arr(H, () => 0) },
    { ...tests, usd: arr(H, (i) => (i < 100 ? 0.25 : 0)), tok: arr(H, (i) => (i < 100 ? 3000 : 0)), turns: arr(H, (i) => (i < 100 ? 1 : 0)), keyUsd: arr(H, () => 0) },
    { kind: "other", host: "OLDHOST", name: "other", count: 3, usd: arr(H, (i) => (i > 150 ? 0.1 : 0)), tok: arr(H, (i) => (i > 150 ? 100 : 0)) },   // an older peer: no turns
    { kind: "unattributed", name: "unattributed", usd: arr(H, (i) => (i === 5 ? 3 : 0)), tok: arr(H, (i) => (i === 5 ? 30000 : 0)), turns: arr(H, (i) => (i === 5 ? 4 : 0)) },
  ] };
  const days = { keys: dk, stacks: [
    { ...web, usd: arr(D, () => 24), tok: arr(D, () => 480000), turns: arr(D, () => 48), keyUsd: arr(D, (i) => (i === 3 ? 9 : 0)) },
    { ...api, usd: arr(D, () => 6), tok: arr(D, () => 96000), turns: arr(D, () => 12), keyUsd: arr(D, () => 0) },
    { ...tests, usd: arr(D, (i) => (i % 5 ? 0 : 0.6)), tok: arr(D, (i) => (i % 5 ? 0 : 7200)), turns: arr(D, (i) => (i % 5 ? 0 : 2)), keyUsd: arr(D, () => 0) },
    { kind: "unattributed", name: "unattributed", usd: arr(D, (i) => (i < 2 ? 40 : 0)), tok: arr(D, (i) => (i < 2 ? 400000 : 0)), turns: arr(D, (i) => (i < 2 ? 30 : 0)) },
  ] };
  const sum = (a: number[]) => a.reduce((t, v) => t + v, 0);
  const sessions = days.stacks.filter((s) => s.kind === "sid").map((s: any) => ({ sid: s.sid, name: s.name, bg: s.bg, fg: "#fff", live: s.live,
    usd: sum(s.usd), tok: sum(s.tok), turns: sum(s.turns), key: { usd: sum(s.keyUsd) } }));
  const una = days.stacks[3] as any;
  return { host: "TESTHOST", scope: "all", sessions, order: [], tags: [], unattributed: { usd: sum(una.usd), tok: sum(una.tok), turns: sum(una.turns) }, hours, days };
}

const chartTotals = (ser: any, meas: string) => ser.stacks.reduce((t: number, s: any) => t + (s[meas] || []).reduce((a: number, v: number) => a + (v || 0), 0), 0);
const listTotals = (v: any, meas: string) => v.sessions.reduce((t: number, s: any) => t + (s[meas] || 0), 0) + ((v.unattributed || {})[meas] || 0) + ((v.other || {})[meas] || 0);

test("for every range the list's total is the chart's total, in dollars, tokens and turns", () => {
  for (const range of ["day", "hours", "days"]) {
    const { spSeries, spRangeView } = lift(range);
    const d = payload(), ser = spSeries(d), view = spRangeView(d);
    for (const meas of ["usd", "tok", "turns"]) {
      const chart = chartTotals(ser, meas), list = listTotals(view, meas);
      assert.ok(Math.abs(chart - list) < 1e-6, `${range} ${meas}: chart ${chart} vs list ${list}`);
    }
    assert.equal(ser.keys.length, range === "day" ? 24 : range === "hours" ? 168 : 90, "the same buckets the chart draws");
  }
});

test("the 90-day view agrees with the payload's own sessions, and the day view drops a session with nothing in it", () => {
  const { spRangeView } = lift("days");
  const d = payload(), v = spRangeView(d);
  for (const s of d.sessions) {
    const r = v.sessions.find((x: any) => x.sid === s.sid);
    assert.ok(r, s.name);
    assert.ok(Math.abs(r.usd - s.usd) < 1e-6 && r.tok === s.tok && r.turns === s.turns, s.name);
    assert.ok(Math.abs(r.key.usd - s.key.usd) < 1e-6, "the key column follows too");
  }
  assert.deepEqual(v.unattributed, d.unattributed);
  assert.deepEqual(v.sessions.map((s: any) => s.name), ["web", "api", "tests"], "by spend, the range's own order");
  const day = lift("day").spRangeView(payload());
  assert.deepEqual(day.sessions.map((s: any) => s.name), ["web", "api"], "tests spent nothing in the last 24 hours: no row");
  assert.equal(day.sessions[0].turns, 48, "web: 2 turns an hour over 24 hours");
  assert.equal(day.sessions[1].turns, 12, "api: every other hour");
  assert.ok(Math.abs(day.sessions[0].key.usd - 6) < 1e-6, "web's key dollars over the day: 12 odd hours at $0.50");
  assert.deepEqual(day.unattributed, { usd: 0, tok: 0, turns: 0 }, "the unattributed hour is older than a day");
  assert.equal(day.other.count, 3, "an older peer's fold is a row of its own");
  assert.ok(Math.abs(day.other.usd - 2.4) < 1e-6, "24 hours at $0.10");
  assert.equal(day.other.turns, 0, "…with no turns array, zero turns, never a guess");
  assert.deepEqual(day.other.hosts, ["OLDHOST"]);
  const week = lift("hours").spRangeView(payload());
  assert.equal(week.sessions.find((s: any) => s.name === "tests").turns, 100 - 24, "tests in the week: hours 24..99");
  assert.equal(week.unattributed.turns, 0, "hour 5 fell off the 168-hour tail");
});

test("the chart's stacks follow the list's rows in every order; the fold and the unattributed stay last", () => {
  const { spSeries, spRangeView, spRows, spStacks } = lift("day");
  const d = payload(), ser = spSeries(d), view = spRangeView(d);
  const stacks = spStacks(d, ser, spRows(view));
  assert.deepEqual(stacks.map((s: any) => s.kind + ":" + (s.name || "")), ["sid:web", "sid:api", "other:other", "unattributed:unattributed"],
    "the day view has no tests stack (nothing in range) and keeps the fold and the unattributed last");
  // "your order" ranks by the viewer's arrangement: api first here
  const yours = lift("day", "yours");
  const d2 = payload(); d2.order = [["TESTHOST", "s-api"], ["TESTHOST", "s-web"]];
  const v2 = yours.spRangeView(d2);
  const rows2 = yours.spRows(v2);
  const st2 = yours.spStacks(d2, yours.spSeries(d2), rows2);
  assert.deepEqual(rows2.rows.map((r: any) => r.name), ["api", "web"]);
  assert.deepEqual(st2.filter((s: any) => s.kind === "sid").map((s: any) => s.name), ["api", "web"], "bottom stack = top row");
});

test("the range is named from the buckets the chart draws, and the renderer hands the table the range view", () => {
  const { spSeries, spRangeWords } = lift("day");
  assert.equal(spRangeWords(spSeries(payload())), "last 24 hours");
  assert.equal(lift("hours").spRangeWords(lift("hours").spSeries(payload())), "last 7 days");
  assert.equal(lift("days").spRangeWords(lift("days").spSeries(payload())), "last 90 days");
  assert.ok(USAGE_JS.includes("h+='<div id=rsp-table>'+sessionTable(spRangeView(d))+'</div></div>';"), "the first render");
  assert.ok(USAGE_JS.includes("if(m[1]==='order'||m[1]==='range'){var tb=document.getElementById('rsp-table');if(tb){tb.innerHTML=sessionTable(spRangeView(SP.data));"), "a range switch re-sums the list");
  assert.ok(USAGE_JS.includes("var rn=document.getElementById('rsp-range-note');if(rn)rn.textContent=spRangeWords(spSeries(SP.data));"), "…and renames the range");
  assert.ok(USAGE_JS.includes("tb2.innerHTML=sessionTable(spRangeView(SP.data))"), "the tag merge too");
  assert.ok(USAGE_JS.includes("<span id=rsp-range-note>'+spRangeWords(spSeries(d))+'</span>"), "the head names the range");
  assert.ok(USAGE_JS.includes("var stacks=spStacks(d,ser,spRows(spRangeView(d)))"), "the chart's stacks follow the list's rows");
  // spTail cuts the two new arrays with the keys
  assert.ok(USAGE_JS.includes("if(s.turns)o.turns=s.turns.slice(cut);if(s.keyUsd)o.keyUsd=s.keyUsd.slice(cut);"));
});
