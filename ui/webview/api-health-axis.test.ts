// The API health histograms' x-axis (T338, the user 2026-09-11): clock times the way the timeline pane labels its axis,
// never ages ('1d', '18h', '12h'). The kernel lifts the timeline view's own formatter and tick rule (clock, NICE,
// niceStep) VERBATIM into window.__rompTimelineAxis before the popup's script, and the script's axisTicks lays the
// ticks: the nice step for the span (at most eight), each labelled with its local HH:MM, the date (MM-DD) on the first
// tick of each new day the span crosses and on every tick at a step of a day or more, a label that would overlap the
// one before dropped with its gridline kept. Both halves are lifted from their sources here and run together over a
// 1-hour, a 24-hour and a 7-day span; the regexes the kernel lifts by are pinned against the view. And the popup's legend
// and waiting rows (T340, the user 2026-09-11): no swatches, the class tokens in their inks, the status code coloured in
// a row's words, the other band's hue distinct from the accent, the red, the magenta and the retrying amber per theme.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const VIEW = read("ui", "romp-timeline-view.js");
const KERNEL = read("kernel", "kernel.py");
const START = KERNEL.indexOf('_LANDING_APIH_JS = """') + '_LANDING_APIH_JS = """'.length;
const APIH = KERNEL.slice(START, KERNEL.indexOf('"""', START));
assert.ok(APIH.length > 1000, "the popup's inline script");

// the kernel's lift, replayed here with the same three regexes over the view's source
const PARTS = [/^const NICE = \[[^\n]*\];/m, /^function clock\(t\) \{[^\n]*\}/m, /^function niceStep\(W\) \{[^\n]*\}/m];
function lift(): string { return PARTS.map((re) => { const m = VIEW.match(re); assert.ok(m, re.source); return m![0]; }).join("\n"); }
function between(a: string, b: string): string { const i = APIH.indexOf(a), j = APIH.indexOf(b, i); assert.ok(i >= 0 && j > i, a.slice(0, 40)); return APIH.slice(i, j); }
type Tick = { x: number; label: string; shown: boolean };
function world(): { axisTicks: (t0: number, span: number, W: number) => Tick[] } {
  const axis = between("var TL=window.__rompTimelineAxis||null;", "function sumArr(");
  return new Function("var window={__rompTimelineAxis:(function(){" + lift() + "\nreturn {NICE:NICE,clock:clock,niceStep:niceStep};})()};\n" + axis + "\nreturn { axisTicks };")() as any;
}
const AGE = /^\d+[mhd]$/, HM = /^\d\d:\d\d$/, MD = /^\d\d-\d\d$/;
const local = (y: number, mo: number, d: number, h: number, mi = 0) => Math.floor(new Date(y, mo, d, h, mi).getTime() / 1000);   // this machine's zone, as the browser's would be
const hm = (t: number) => { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };

test("the kernel lifts the timeline's clock, NICE and niceStep by regexes that match the view's source once each", () => {
  assert.match(KERNEL, /_TIMELINE_AXIS_PARTS = \(r"\^const NICE = \\\[\[\^\\n\]\*\\\];", r"\^function clock\\\(t\\\) \\\{\[\^\\n\]\*\\\}", r"\^function niceStep\\\(W\\\) \\\{\[\^\\n\]\*\\\}"\)/);
  for (const re of PARTS) assert.equal(VIEW.match(new RegExp(re.source, "gm"))!.length, 1, re.source);
  assert.match(KERNEL, /def _timeline_axis_js\(\):/);
  assert.match(KERNEL, /out = "window\.__rompTimelineAxis=\(function\(\)\{" \+ "\\n"\.join\(parts\) \+ "\\nreturn \{NICE:NICE,clock:clock,niceStep:niceStep\};\}\)\(\);"/);
  assert.match(KERNEL, /return "window\.__rompTimelineAxis=null;"/, "a missing file or a moved line publishes null, said on stderr");
  assert.match(KERNEL, /if _TIMELINE_AXIS_MEMO\[0\] == mt and _TIMELINE_AXIS_MEMO\[1\]:\n\s*return _TIMELINE_AXIS_MEMO\[1\]/, "memoized on the view's mtime: the landing's hot path pays one stat");
  // published ahead of the script that reads it, in the same script element (the landing's script count is pinned elsewhere)
  assert.ok(KERNEL.includes('"<script>" + _timeline_axis_js() + _LANDING_APIH_JS + "</script>"'));
  assert.ok(APIH.includes("var TL=window.__rompTimelineAxis||null;"));
  assert.ok(APIH.includes("var step=TL.niceStep(span)"), "the timeline's tick rule");
  assert.ok(APIH.includes("TL.clock(tk)"), "the timeline's formatter");
});

test("a 24-hour span: hours on the ticks, the date on the first tick past midnight, nothing that reads as an age", () => {
  const { axisTicks } = world();
  const t0 = local(2026, 8, 10, 15, 30), span = 86400;
  const ticks = axisTicks(t0, span, 560);
  assert.ok(ticks.length >= 8 && ticks.length <= 9, "the nice step for a day is three hours: eight or nine ticks");
  const labels = ticks.map((k) => k.label);
  assert.ok(labels.every((l) => HM.test(l) || MD.test(l)), labels.join(" "));
  assert.ok(labels.every((l) => !AGE.test(l) && l !== "now"), "never an age, never 'now'");
  const dates = labels.filter((l) => MD.test(l));
  assert.deepEqual(dates, ["09-11"], "the span crosses one midnight: that day's first tick carries its date");
  const dateAt = ticks.find((k) => MD.test(k.label))!;
  const before = ticks[ticks.indexOf(dateAt) - 1];
  assert.ok(before && HM.test(before.label), "the tick before it is an hour of the 10th");
  assert.ok(ticks.every((k) => k.shown), "at the detail's width every label fits");
  const dateTick = t0 + (dateAt.x / 560) * span;
  assert.equal(new Date(Math.round(dateTick) * 1000).getDate(), 11, "the date tick is the first tick of the 11th in this zone");
  assert.ok(ticks.every((k, i) => i === 0 || k.x > ticks[i - 1].x), "left to right along the real span");
  assert.ok(ticks[0].x >= 0 && ticks[ticks.length - 1].x <= 560);
  // the hour ticks read the same clock the timeline's axis would print for those moments
  for (const k of ticks) if (HM.test(k.label)) assert.equal(k.label, hm(t0 + (k.x / 560) * span), "the label is the tick's own local time");
  // the hover's width drops the labels that would overlap, never a gridline
  const small = axisTicks(t0, span, 168);
  assert.equal(small.length, ticks.length, "every tick still has its gridline");
  assert.ok(small.some((k) => !k.shown) && small.filter((k) => k.shown).length >= 3, "some labels yield, at least three stand");
  // the day's date outranks the clock it collides with, at either parity of the ledger's rolling bin boundary
  for (const t0p of [local(2026, 8, 10, 15, 30), local(2026, 8, 10, 14, 30), local(2026, 8, 10, 12, 30)]) {
    const sm = axisTicks(t0p, span, 168);
    assert.deepEqual(sm.filter((k) => k.shown && MD.test(k.label)).map((k) => k.label), ["09-11"], "the crossing is named at the hover's width, t0 " + hm(t0p));
    for (let i = 1; i < sm.length; i++) if (sm[i].shown && sm[i - 1].shown) assert.ok(sm[i].x - sm[i - 1].x > 20, "no two shown labels touch");
  }
});

test("a 7-day span: a tick a day, every label a date; a 1-hour span: ten-minute ticks, hours only", () => {
  const { axisTicks } = world();
  const week = axisTicks(local(2026, 8, 4, 15, 30), 604800, 560);
  assert.ok(week.length >= 7 && week.length <= 8);
  assert.ok(week.every((k) => MD.test(k.label)), week.map((k) => k.label).join(" "));
  assert.equal(week[0].label, "09-05"); assert.equal(week[week.length - 1].label, "09-11");
  // a day tick is a LOCAL midnight in every zone (the epoch multiples the timeline uses under a day would be UTC's)
  const wt0 = local(2026, 8, 4, 15, 30);
  for (const k of week) { const d = new Date(Math.round(wt0 + (k.x / 560) * 604800) * 1000); assert.equal(d.getHours() * 60 + d.getMinutes(), 0, "midnight local: " + d.toString()); }
  assert.ok(week.every((k) => !AGE.test(k.label)));
  const hour = axisTicks(local(2026, 8, 11, 14, 5), 3600, 560);
  assert.ok(hour.length >= 6 && hour.length <= 7, "the nice step for an hour is ten minutes");
  assert.ok(hour.every((k) => HM.test(k.label)), hour.map((k) => k.label).join(" "));
  assert.equal(hour[0].label, hm(Math.ceil(local(2026, 8, 11, 14, 5) / 600) * 600), "the first ten-minute multiple at or after t0 (14:10 in a whole-hour zone)");
  // no timeline module on the page: no clocks, no invented ones
  const bare = new Function("var window={__rompTimelineAxis:null};\n" + between("var TL=window.__rompTimelineAxis||null;", "function sumArr(") + "\nreturn { axisTicks };")() as any;
  assert.deepEqual(bare.axisTicks(0, 86400, 560), [{ x: 140, label: "", shown: false }, { x: 280, label: "", shown: false }, { x: 420, label: "", shown: false }], "gridlines at the quarters, no clocks");
});

test("the histogram draws the clocks and nothing in ages: no tickWords, no span word, no 'now'", () => {
  assert.ok(!APIH.includes("tickWords"), "the age words are gone");
  assert.ok(!APIH.includes('">now</span>'));
  assert.ok(APIH.includes("axisTicks(led.from||0,span,W).forEach(function(k){var gx=k.x;grid+="), "a gridline per tick over the ledger's real span");
  assert.ok(APIH.includes("if(k.shown)xlab+='<span style=\"left:'+(gx/W*100).toFixed(1)+'%\">'+esc(k.label)+'</span>';"), "a label when it fits");
  assert.ok(APIH.includes("function hmd(ep){var d=new Date(ep*1000),n=new Date();if(d.toDateString()===n.toDateString())return hm(ep);\nreturn dateWords(ep)+' '+hm(ep);}"), "the State changes rows share the axis's date form");
  // the relative forms stay where they belong: the read's age and the rows' since
  assert.ok(APIH.includes("function ageWords(){return LANDED&&MERGE?'read '+MERGE.agoWords((Date.now()-LANDED)/1000):'';}"));
  assert.ok(APIH.includes("(r.since?' · since '+hm(r.since):'')"));
});

test("T340: no swatches; the class tokens wear their inks with the explanation beside them; a row's status code wears its class ink", () => {
  assert.ok(!APIH.includes("ah-sw"), "no coloured square beside a waiting session's name, no legend swatch");
  assert.ok(APIH.includes("var LEGEND_ROWS=[['r429','429','rate limit: the API told us to slow down'],['r5xx','5xx','server error: the API itself failed'],['none','other','no connection, or another error']];"));
  assert.ok(APIH.includes("h+='<div class=ah-lrow><span class=\"ah-lt ah-c-'+r[0]+'\">'+r[1]+'</span> <span>'+r[2]+'</span></div>';"));
  assert.ok(APIH.includes("+(bg?'<span class=ah-nm style=\"color:'+bg+'\">':'<span class=ah-nm>')+esc(r.name)+'</span>'"), "the name in its colour is the whole cue");
  const cls = new Function("var esc=function(s){return String(s);};\n" + between("function clsWords(r){", "// The pause control.") + "\nreturn clsWords;")() as (r: any) => string;
  assert.equal(cls({ cls: "429", status: 429 }), "<span class=ah-c-r429>429</span> rate limited");
  assert.equal(cls({ cls: "529", status: 529 }), "<span class=ah-c-r5xx>529</span> overloaded");
  assert.equal(cls({ cls: "error", status: 503 }), "error <span class=ah-c-r5xx>503</span>", "any 5xx wears the magenta ink");
  assert.equal(cls({ cls: "error", status: 400 }), "error 400", "a status of another class stays plain");
  assert.equal(cls({ cls: "error" }), "error");
  assert.equal(cls({ cls: "offline" }), "offline");
});

test("T340: the other band's hue per theme, and the inks and fills that follow it", () => {
  for (const rule of [".ah-c-none{color:#d9f99d}", ".ah-seg-noStatus,.ah-seg-other{fill:#d9f99d}", ".ah-lt{font-weight:600}",
                      "body.theme-light .ah-c-none{color:#4f46e5}", "body.theme-light .ah-seg-noStatus,body.theme-light .ah-seg-other{fill:#4f46e5}"]) {
    assert.ok(KERNEL.includes(rule), rule);
  }
  for (const gone of [".ah-sw{", ".ah-lsw{", ".ah-sw-r429{", ".ah-sw-r5xx{", ".ah-sw-none{", "body.theme-light .ah-sw-"]) assert.ok(!KERNEL.includes(gone), gone + " is gone");
  // the 429 and 5xx inks the tokens and the coloured status codes wear are the text inks T316 set, per theme
  assert.ok(KERNEL.includes(".ah-c-r429{color:#ef6b6f}.ah-c-r5xx{color:#e879f9}") && KERNEL.includes("body.theme-light .ah-c-r429{color:#B02A1C}body.theme-light .ah-c-r5xx{color:#86198F}"));
});
