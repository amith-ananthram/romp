// T293 (the user 2026-09-09): the usage modal's spend chart grows a hover crosshair — a hairline at the pointer's
// bucket, a stamp naming the bucket in words, and a tooltip listing the bucket's sessions in spend order — and its
// middle range becomes "7 days · by hour" over 168 hourly buckets (the ledger's 192 keep a day of slack). The
// landing page loads no webview bundle, so the pieces live inside kernel.py's _LANDING_USAGE_JS as pure functions;
// this test runs them from the source (the file text is a Python string, so an extracted line may carry no
// backslash escape) and pins the range rename and that a pick persisted by the old 8-day button reads as 7 days.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const USAGE_JS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];
const LINES = USAGE_JS.split("\n");

/** the line starting with `start`, plus `extra` following lines (a function that wraps) */
function block(start: string, extra = 0): string {
  const i = LINES.findIndex((l) => l.startsWith(start));
  assert.ok(i >= 0, "the landing JS carries a line starting " + JSON.stringify(start));
  const src = LINES.slice(i, i + 1 + extra).join("\n");
  assert.ok(!src.includes("\\"), "no backslash escape in an extracted block (the file text is a Python string): " + start);
  return src;
}

function pure(): { spBucketAt: (px: number, width: number, n: number) => number; spStamp: (k: string, range: string) => string;
  spBucketTop: (stacks: any[], meas: string, i: number, cap: number, pin: number) => { rows: Array<{ si: number; v: number }>; more: number; total: number };
  SP_RANGE_BUCKETS: Record<string, number>; SP_TIP_ROWS: number } {
  const src = [block("var SP_DOW="), block("function spBucketAt("), block("function spStamp(", 1), block("function spBucketTop(", 2),
    block("var SP_RANGE_BUCKETS="), block("var SP_TIP_ROWS=")].join("\n");
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(src + "\n; return {spBucketAt:spBucketAt,spStamp:spStamp,spBucketTop:spBucketTop,SP_RANGE_BUCKETS:SP_RANGE_BUCKETS,SP_TIP_ROWS:SP_TIP_ROWS};")();
}

test("the pointer's bucket: a pixel offset over the chart's width maps onto one of n buckets, and outside is -1", () => {
  const { spBucketAt } = pure();
  assert.equal(spBucketAt(0, 600, 24), 0);
  assert.equal(spBucketAt(24.9, 600, 24), 0);
  assert.equal(spBucketAt(25, 600, 24), 1, "the second bucket starts at 1/24 of the width");
  assert.equal(spBucketAt(599.9, 600, 24), 23);
  assert.equal(spBucketAt(300, 600, 168), 84);
  assert.equal(spBucketAt(599.99, 600, 90), 89, "never past the last bucket");
  assert.equal(spBucketAt(-0.1, 600, 24), -1, "left of the chart");
  assert.equal(spBucketAt(600, 600, 24), -1, "the right edge is outside");
  assert.equal(spBucketAt(10, 0, 24), -1, "no width, no bucket");
  assert.equal(spBucketAt(10, 600, 0), -1, "no buckets, no bucket");
  assert.equal(spBucketAt(NaN, 600, 24), -1);
});

test("the stamp: an hourly key reads like a calendar entry with a 12-hour clock, a daily key without the hour", () => {
  const { spStamp } = pure();
  // 2026-09-09 is a Wednesday; the weekday comes from the calendar date, so it does not depend on the zone
  assert.equal(spStamp("2026-09-09T15", "hours"), "Wed Sep 9, 3 PM");
  assert.equal(spStamp("2026-09-09T00", "hours"), "Wed Sep 9, 12 AM");
  assert.equal(spStamp("2026-09-09T12", "hours"), "Wed Sep 9, 12 PM");
  assert.equal(spStamp("2026-09-09T23", "hours"), "Wed Sep 9, 11 PM");
  assert.equal(spStamp("2026-09-09T09", "hours"), "Wed Sep 9, 9 AM");
  assert.equal(spStamp("2026-01-01T06", "hours"), "Thu Jan 1, 6 AM");
  assert.equal(spStamp("2026-09-09", "days"), "Wed Sep 9");
  assert.equal(spStamp("2026-12-25", "days"), "Fri Dec 25");
  // a key the stamp cannot read is shown as it is, never dressed up (fail loudly)
  assert.equal(spStamp("nope", "hours"), "nope");
  assert.equal(spStamp("2026-09-09", "hours"), "2026-09-09", "a day key in an hourly view");
  assert.equal(spStamp("2026-09-09T15", "days"), "2026-09-09T15", "an hour key in the daily view");
  assert.equal(spStamp("2026-13-01", "days"), "2026-13-01");
  assert.equal(spStamp("2026-09-09T24", "hours"), "2026-09-09T24");
  assert.equal(spStamp("", "days"), "");
});

test("the bucket's sessions: spend order, zeros dropped, capped with the rest counted, the hovered stack always listed", () => {
  const { spBucketTop } = pure();
  const stacks = [
    { name: "a", usd: [1, 5], tok: [10, 50] },
    { name: "b", usd: [0, 3] },
    { name: "c", usd: [2, 8] },
    { name: "d", usd: [0, 0] },
    { name: "e", usd: [0, 1] },
  ];
  assert.deepEqual(spBucketTop(stacks, "usd", 1, 2, -1), { rows: [{ si: 2, v: 8 }, { si: 0, v: 5 }], more: 2, total: 17 });
  assert.deepEqual(spBucketTop(stacks, "usd", 1, 2, 4), { rows: [{ si: 2, v: 8 }, { si: 0, v: 5 }, { si: 4, v: 1 }], more: 1, total: 17 },
    "the hovered stack beyond the cap joins the list and leaves the fold");
  assert.deepEqual(spBucketTop(stacks, "usd", 1, 2, 2), { rows: [{ si: 2, v: 8 }, { si: 0, v: 5 }], more: 2, total: 17 },
    "a hovered stack already listed is not listed twice");
  assert.deepEqual(spBucketTop(stacks, "usd", 1, 2, 3), { rows: [{ si: 2, v: 8 }, { si: 0, v: 5 }], more: 2, total: 17 },
    "a hovered stack with nothing in the bucket adds no row");
  assert.deepEqual(spBucketTop(stacks, "usd", 0, 6, -1), { rows: [{ si: 2, v: 2 }, { si: 0, v: 1 }], more: 0, total: 3 }, "zeros are not rows");
  assert.deepEqual(spBucketTop(stacks, "usd", 1, 10, -1).more, 0, "a cap above the count folds nothing");
  assert.deepEqual(spBucketTop(stacks, "tok", 1, 6, -1), { rows: [{ si: 0, v: 50 }], more: 0, total: 50 }, "a stack without the measure counts as zero");
  assert.deepEqual(spBucketTop([{ usd: [2] }, { usd: [2] }, { usd: [3] }], "usd", 0, 6, -1).rows, [{ si: 2, v: 3 }, { si: 0, v: 2 }, { si: 1, v: 2 }],
    "ties keep the stack order");
  assert.deepEqual(spBucketTop([], "usd", 0, 6, -1), { rows: [], more: 0, total: 0 });
});

test("the middle range is 7 days over the hourly ledger's last 168 buckets; 1 day is its last 24; 90 days is whole", () => {
  const { SP_RANGE_BUCKETS, SP_TIP_ROWS } = pure();
  assert.deepEqual(SP_RANGE_BUCKETS, { day: 24, hours: 168 });
  assert.ok(SP_TIP_ROWS >= 4 && SP_TIP_ROWS <= 8, "the tooltip lists the top several, then folds: " + SP_TIP_ROWS);
  assert.ok(USAGE_JS.includes('data-act=range:hours>7 days \\u00b7 by hour</button>'), "the button says 7 days");
  assert.ok(!USAGE_JS.includes("8 days"), "no 8-day wording survives in the modal");
  assert.ok(KERNEL.includes("_SERIES_HOURS = 192"), "the ledger keeps 192 hours: a day of slack past the view");
  // spSeries executed: the range's tail of a 192-key series
  const src = [block("var SP_RANGE_BUCKETS="), block("function spSeries(d){"), block("function spTail(ser,keep){", 2)].join("\n");
  const series = (range: string) => {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const spSeries = new Function("SP", src + "\n; return spSeries;")({ range });
    const keys = Array.from({ length: 192 }, (_, i) => "k" + i);
    const d = { hours: { keys, epochs: keys.map((_, i) => 1000 + i),
      stacks: [{ kind: "sid", sid: "s1", usd: keys.map((_, i) => i), tok: keys.map((_, i) => i * 10), hosts: { A: { usd: keys.map(() => 1), tok: keys.map(() => 2) } } }] },
      days: { keys: ["d1", "d2"], stacks: [{ usd: [1, 2], tok: [3, 4] }] } };
    return { d, out: spSeries(d) };
  };
  const week = series("hours");
  assert.equal(week.out.keys.length, 168);
  assert.equal(week.out.keys[0], "k24", "the LAST 168: the oldest day falls off");
  assert.equal(week.out.keys[167], "k191");
  assert.deepEqual(week.out.epochs.slice(0, 2), [1024, 1025]);
  assert.equal(week.out.stacks[0].usd[0], 24, "every stack is cut with the keys");
  assert.equal(week.out.stacks[0].tok.length, 168);
  assert.equal(week.out.stacks[0].hosts.A.usd.length, 168, "a per-host split is cut too");
  assert.equal(week.out.stacks[0].sid, "s1", "the stack's other fields ride along");
  const day = series("day");
  assert.equal(day.out.keys.length, 24);
  assert.equal(day.out.keys[0], "k168");
  const days = series("days");
  assert.equal(days.out, days.d.days, "the daily range is the ledger's series itself");
  assert.equal(series("hours").d.hours.keys.length, 192, "the input is not cut in place");
});

test("a persisted pick from the old 8-day button reads as the 7-day view: the stored value is unchanged, its meaning is the new range", () => {
  const src = [block("var SP={"), block("var SP_PREFS_KEY="), block("function spLoadPrefs(){"), block("var SP_RANGE_BUCKETS=")].join("\n");
  const load = (stored: any) => {
    const ls = { getItem: (k: string) => (k === "romp:spendModal" ? JSON.stringify(stored) : null) };
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function("localStorage", src + "\n; spLoadPrefs(); return {SP:SP,buckets:SP_RANGE_BUCKETS[SP.range]};")(ls);
  };
  const old = load({ range: "hours", measure: "usd", order: "spend", merge: false });   // what the 8-day button wrote
  assert.equal(old.SP.range, "hours");
  assert.equal(old.buckets, 168, "…and that value now selects 168 hourly buckets");
  assert.equal(load({ range: "day" }).buckets, 24);
  assert.equal(load({ range: "days" }).buckets, undefined, "the daily range is uncut");
  assert.equal(load({ range: "week" }).SP.range, "hours", "an unknown value falls back to the default, the 7-day view");
  assert.equal(load({}).SP.range, "hours", "no stored range: the default is the 7-day view");
  // the label the stored value selects
  const btn = /data-act=range:hours>([^<]+)<\/button>/.exec(USAGE_JS);
  assert.ok(btn, "the hours button");
  assert.equal(btn![1], "7 days \\u00b7 by hour");
});
