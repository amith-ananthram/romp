// The API health histograms' x-axis (T338, the user 2026-09-11): clock times the way the timeline pane labels its axis,
// never ages ('1d', '18h', '12h'). The kernel lifts the timeline view's own formatter and tick rule (clock, NICE,
// niceStep) VERBATIM into window.__rompTimelineAxis ahead of the popup's script, and the script's axisTicks lays the
// ticks: clocks at the timeline's epoch multiples for the nice step, plus a tick at each LOCAL midnight the span crosses
// carrying that day's date (MM-DD); at a step of a day or more the midnights alone, all dates. Every label is emitted and
// fitAxisLabels decides after the paint, from measured widths, which stand: a day's date outranks the clock it collides
// with. Both halves are lifted from their sources here and run together over 1-hour, 24-hour and 7-day spans, in a zone
// west of UTC with daylight saving (the process zone is pinned first thing, so local midnights are NOT epoch multiples and
// March and November carry a transition), so the local-midnight and DST claims are pinned by something. And the popup's
// legend and waiting rows (T340, the user 2026-09-11): no swatches, the class tokens in their inks at full strength, the
// status code coloured in a row's words, the other band's hue distinct from the accent, the red, the magenta and the
// retrying amber per theme.
process.env.TZ = "America/Los_Angeles";   // before any Date: the zone the tests below reason in (node re-reads it)
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
assert.equal(new Date(Date.UTC(2026, 6, 1, 12)).getHours(), 5, "the process zone is Pacific daylight time: 12:00Z reads 05:00");

// the kernel's lift, replayed here with the same three regexes over the view's source
const PARTS = [/^const NICE = \[[^\n]*\];/m, /^function clock\(t\) \{[^\n]*\}/m, /^function niceStep\(W\) \{[^\n]*\}/m];
function lift(): string { return PARTS.map((re) => { const m = VIEW.match(re); assert.ok(m, re.source); return m![0]; }).join("\n"); }
function between(a: string, b: string): string { const i = APIH.indexOf(a), j = APIH.indexOf(b, i); assert.ok(i >= 0 && j > i, a.slice(0, 40)); return APIH.slice(i, j); }
type Tick = { x: number; label: string; date: boolean };
type Axis = { axisTicks: (t0: number, span: number, W: number) => Tick[]; fitLabels: (items: { x: number; w: number; date: boolean }[], gap: number) => boolean[] };
const AXIS_SRC = () => between("var TL=window.__rompTimelineAxis||null;", "function sumArr(");
function world(): Axis {
  return new Function("var window={__rompTimelineAxis:(function(){" + lift() + "\nreturn {NICE:NICE,clock:clock,niceStep:niceStep};})()};\n" + AXIS_SRC() + "\nreturn { axisTicks, fitLabels };")() as Axis;
}
const AGE = /^\d+[mhd]$/, HM = /^\d\d:\d\d$/, MD = /^\d\d-\d\d$/;
const local = (y: number, mo: number, d: number, h: number, mi = 0) => Math.floor(new Date(y, mo, d, h, mi).getTime() / 1000);
const pad = (n: number) => String(n).padStart(2, "0");
const hm = (t: number) => { const d = new Date(t * 1000); return pad(d.getHours()) + ":" + pad(d.getMinutes()); };
const md = (t: number) => { const d = new Date(t * 1000); return pad(d.getMonth() + 1) + "-" + pad(d.getDate()); };
const at = (t0: number, span: number, W: number, k: Tick) => Math.round(t0 + (k.x / W) * span);   // the tick's moment, back from its x
const isMidnight = (t: number) => { const d = new Date(t * 1000); return d.getHours() === 0 && d.getMinutes() === 0; };

test("the kernel lifts the timeline's clock, NICE and niceStep by regexes that match the view's source once each, memoized on both outcomes", () => {
  assert.match(KERNEL, /_TIMELINE_AXIS_PARTS = \(r"\^const NICE = \\\[\[\^\\n\]\*\\\];", r"\^function clock\\\(t\\\) \\\{\[\^\\n\]\*\\\}", r"\^function niceStep\\\(W\\\) \\\{\[\^\\n\]\*\\\}"\)/);
  for (const re of PARTS) assert.equal(VIEW.match(new RegExp(re.source, "gm"))!.length, 1, re.source);
  assert.match(KERNEL, /def _timeline_axis_js\(\):/);
  assert.match(KERNEL, /out = "window\.__rompTimelineAxis=\(function\(\)\{" \+ "\\n"\.join\(parts\) \+ "\\nreturn \{NICE:NICE,clock:clock,niceStep:niceStep\};\}\)\(\);"/);
  assert.match(KERNEL, /out = "window\.__rompTimelineAxis=null;"/, "a missing file or a moved line publishes null, said on stderr");
  assert.match(KERNEL, /key = "missing"/, "a missing file has its own memo key");
  assert.match(KERNEL, /if _TIMELINE_AXIS_MEMO\[0\] == key and _TIMELINE_AXIS_MEMO\[1\]:\n\s*return _TIMELINE_AXIS_MEMO\[1\]/, "memoized on the view's mtime, the null too: one stat per landing, one stderr line per file version");
  assert.match(KERNEL, /_TIMELINE_AXIS_MEMO\[0\], _TIMELINE_AXIS_MEMO\[1\] = key, out/);
  assert.ok(KERNEL.includes('"<script>" + _timeline_axis_js() + _LANDING_APIH_JS + "</script>"'), "ahead of the script that reads it, in the same element");
  assert.ok(APIH.includes("var TL=window.__rompTimelineAxis||null;"));
  assert.ok(APIH.includes("var step=TL.niceStep(span)"), "the timeline's tick rule");
  assert.ok(APIH.includes("TL.clock(k.t)"), "the timeline's formatter");
});

test("a 24-hour span: clocks at the timeline's epoch multiples, the day's date on a tick of its own at LOCAL midnight", () => {
  const { axisTicks } = world();
  const t0 = local(2026, 8, 10, 15, 30), span = 86400, W = 560;
  const ticks = axisTicks(t0, span, W);
  const clocks = ticks.filter((k) => !k.date), dates = ticks.filter((k) => k.date);
  assert.ok(clocks.length >= 8 && clocks.length <= 9, "the nice step for a day is three hours: eight intervals, eight or nine clock ticks");
  assert.deepEqual(dates.map((k) => k.label), ["09-11"], "the span crosses one midnight: one date tick");
  assert.ok(isMidnight(at(t0, span, W, dates[0])), "the date tick IS the local midnight, not the first clock tick after it");
  assert.equal(at(t0, span, W, dates[0]), local(2026, 8, 11, 0));
  for (const k of clocks) { const t = at(t0, span, W, k); assert.equal(t % 10800, 0, "a clock tick sits on a three-hour epoch multiple"); assert.equal(k.label, hm(t), "labelled with its own local time"); }
  assert.ok(ticks.every((k) => HM.test(k.label) || MD.test(k.label)));
  assert.ok(ticks.every((k) => !AGE.test(k.label) && k.label !== "now"), "never an age, never 'now'");
  assert.ok(ticks.every((k, i) => i === 0 || k.x > ticks[i - 1].x), "left to right along the real span");
  assert.ok(ticks[0].x >= 0 && ticks[ticks.length - 1].x <= W);
  // a Pacific midnight is 07:00Z or 08:00Z, never a three-hour epoch multiple: the midnight tick is an extra gridline here
  assert.equal(ticks.length, clocks.length + 1);
});

test("a 7-day span: a tick a day at LOCAL midnight, every label a date; a 1-hour span: ten-minute clocks", () => {
  const { axisTicks } = world();
  const t0 = local(2026, 8, 4, 15, 30), W = 560;
  const week = axisTicks(t0, 604800, W);
  assert.equal(week.length, 7);
  assert.deepEqual(week.map((k) => k.label), ["09-05", "09-06", "09-07", "09-08", "09-09", "09-10", "09-11"]);
  for (const k of week) { const t = at(t0, 604800, W, k); assert.ok(isMidnight(t), "midnight local: " + new Date(t * 1000).toString()); assert.equal(k.label, md(t)); assert.notEqual(t % 86400, 0, "a Pacific midnight is not a UTC one"); }
  const t0h = local(2026, 8, 11, 14, 5);
  const hour = axisTicks(t0h, 3600, W);
  assert.ok(hour.length >= 6 && hour.length <= 7, "the nice step for an hour is ten minutes");
  assert.ok(hour.every((k) => HM.test(k.label) && !k.date), hour.map((k) => k.label).join(" "));
  assert.equal(hour[0].label, hm(Math.ceil(t0h / 600) * 600));
  // no timeline module on the page: gridlines at the quarters, no clocks, no invented ones
  const bare = new Function("var window={__rompTimelineAxis:null};\n" + AXIS_SRC() + "\nreturn { axisTicks };")() as any;
  assert.deepEqual(bare.axisTicks(0, 86400, 560), [{ x: 140, label: "", date: false }, { x: 280, label: "", date: false }, { x: 420, label: "", date: false }]);
});

test("across daylight saving: every day tick is a local midnight, the transition day is an hour short or long, the clocks read true local time", () => {
  const { axisTicks } = world();
  const W = 604800;   // one unit a second, so x IS the moment
  for (const [y, m, d, forward] of [[2026, 2, 5, true], [2026, 9, 29, false]] as const) {   // spring forward 2026-03-08, fall back 2026-11-01
    const t0 = local(y, m, d, 15, 30);
    const week = axisTicks(t0, 604800, W);
    assert.equal(week.length, 7, "seven dates");
    for (const k of week) assert.ok(isMidnight(at(t0, 604800, W, k)), "midnight local on " + k.label);
    const gaps = week.slice(1).map((k, i) => Math.round(k.x - week[i].x));
    const odd = gaps.filter((g) => g !== 86400);
    assert.deepEqual(odd, [forward ? 82800 : 90000], "the transition day is 23 or 25 hours long: " + gaps.join(" "));
    assert.ok(week.every((k) => MD.test(k.label)));
  }
  // a 24-hour span over each transition: the clock ticks stay on epoch multiples and read the true local clock
  for (const [y, m, d] of [[2026, 2, 7], [2026, 9, 31]] as const) {
    const t0 = local(y, m, d, 15, 30), span = 86400;
    const ticks = axisTicks(t0, span, span);
    const dates = ticks.filter((k) => k.date);
    assert.equal(dates.length, 1); assert.ok(isMidnight(at(t0, span, span, dates[0])));
    for (const k of ticks) if (!k.date) { const t = at(t0, span, span, k); assert.equal(t % 10800, 0); assert.equal(k.label, hm(t)); }
    assert.ok(ticks.every((k, i) => i === 0 || k.x > ticks[i - 1].x));
  }
});

test("which labels stand is decided from measured boxes: overlaps yield left to right, a day's date takes its slot from the clock before it", () => {
  const { fitLabels } = world();
  const box = (x: number, date = false) => ({ x, w: 24, date });
  // eight clocks 21 px apart (the hover's 24-hour axis at its real width): every other one yields
  assert.deepEqual(fitLabels([0, 21, 42, 63, 84, 105, 126, 147].map((x) => box(x)), 4), [true, false, true, false, true, false, true, false]);
  // the same, with the date on the tick that would have yielded: it stands and the clock before it yields
  assert.deepEqual(fitLabels([box(0), box(21), box(42), box(63, true), box(84), box(105)], 4), [true, false, false, true, false, true]);
  // and on a tick that stands anyway: nothing else changes
  assert.deepEqual(fitLabels([box(0), box(21), box(42, true), box(63), box(84)], 4), [true, false, true, false, true]);
  // wide enough: everything stands
  assert.deepEqual(fitLabels([0, 40, 80, 120].map((x) => box(x)), 4), [true, true, true, true]);
  // two dates colliding (a week at a narrow width): the later yields, dates never hide dates
  assert.deepEqual(fitLabels([box(0, true), box(20, true), box(40, true)], 4), [true, false, true]);
  assert.deepEqual(fitLabels([], 4), []);
  // the DOM half: every label is emitted with its date mark, fitted after each paint from getBoundingClientRect
  assert.ok(APIH.includes("if(k.label)xlab+='<span'+(k.date?' data-date=\"1\"':'')+' style=\"left:'+(gx/W*100).toFixed(1)+'%\">'+esc(k.label)+'</span>';"));
  assert.ok(APIH.includes("tip.innerHTML=html(LAST,pinned);if(!pinned)anchor();fitAxisLabels(tip);"), "fitted right after the paint, hover and detail alike");
  assert.ok(APIH.includes("var items=spans.map(function(s){var b=s.getBoundingClientRect();return {x:b.left+b.width/2,w:b.width,date:s.hasAttribute('data-date')};});"));
  assert.ok(APIH.includes("if(!items.length||!items.some(function(it){return it.w>0;}))continue;"), "an unlaid-out tip decides nothing");
  assert.ok(!/cw=W>300\?5\.2:4\.6/.test(APIH), "no glyph estimate in viewBox units");
});

test("the histogram draws the clocks and nothing in ages: no tickWords, no span word, no 'now'", () => {
  assert.ok(!APIH.includes("tickWords"), "the age words are gone");
  assert.ok(!APIH.includes('">now</span>'));
  assert.ok(APIH.includes("axisTicks(led.from||0,span,W).forEach(function(k){var gx=k.x;grid+="), "a gridline per tick over the ledger's real span");
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

// WCAG contrast of an ink composited at an opacity over a surface
function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function composite(ink: string, surface: string, alpha: number): string {
  const px = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const a = px(ink), b = px(surface);
  return "#" + a.map((v, i) => Math.round(v * alpha + b[i] * (1 - alpha)).toString(16).padStart(2, "0")).join("");
}
const contrast = (ink: string, surface: string, alpha: number) => { const l1 = lum(composite(ink, surface, alpha)), l2 = lum(surface); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
function opacityOf(selectorRule: RegExp): number { const m = KERNEL.match(selectorRule); assert.ok(m, selectorRule.source); const o = m![1].match(/opacity:([\d.]+)/); return o ? parseFloat(o[1]) : 1; }

test("T340: the tokens stand at full strength: no group opacity over the legend or a waiting row's words, and every token ink clears 4.5:1 on its card", () => {
  // the group rules carry no opacity (a descendant cannot exceed its group's); only the explanation span and the words are muted
  assert.equal(opacityOf(/\.ah-legend\{([^}]*)\}"\n\s*"\.ah-lrow\{/), 1, "the legend's layout rule");
  assert.equal(opacityOf(/"\.ah-legend\{([^}]*)\}\.ah-mline\{gap:7px\}/), 1, "the legend's later size rule (the stale .6 is gone)");
  assert.equal((KERNEL.match(/\.ah-legend\{[^}]*opacity/g) || []).length, 0, "no .ah-legend rule fades");
  assert.ok(KERNEL.includes(".ah-lrow > span:last-child{opacity:.75}"), "the explanation alone is dimmed");
  assert.ok(KERNEL.includes(".ah-row .ah-desc{opacity:1;color:#a9b1ba}"), "a waiting row's words are muted by colour at opacity 1");
  assert.ok(KERNEL.includes("body.theme-light .ah-row .ah-desc{color:#5D574E}"));
  assert.equal(opacityOf(/"\.ah-lrow\{[^}]*\}\.ah-lt\{([^}]*)\}/), 1, "the token wears no opacity of its own");
  // the inks over the cards, at the effective opacity 1: the dark tip is #1e1e1e, the light tip white
  const inks: Array<[string, string, string]> = [["429", "#ef6b6f", "#B02A1C"], ["5xx", "#e879f9", "#86198F"], ["other", "#d9f99d", "#4f46e5"]];
  for (const [name, dark, light] of inks) {
    assert.ok(contrast(dark, "#1e1e1e", 1) >= 4.5, `${name} dark ${contrast(dark, "#1e1e1e", 1).toFixed(2)}:1`);
    assert.ok(contrast(light, "#FFFFFF", 1) >= 4.5, `${name} light ${contrast(light, "#FFFFFF", 1).toFixed(2)}:1`);
  }
  // and the muted words themselves still read
  assert.ok(contrast("#a9b1ba", "#1e1e1e", 1) >= 4.5); assert.ok(contrast("#5D574E", "#FFFFFF", 1) >= 4.5);
  // the failure the review found, for the record: the same inks under the old 60% fade sat under the floor
  assert.ok(contrast("#ef6b6f", "#1e1e1e", 0.6) < 4.5 && contrast("#B02A1C", "#FFFFFF", 0.6) < 4.5);
});

test("T340: the other band's hue per theme, and the inks and fills that follow it", () => {
  for (const rule of [".ah-c-none{color:#d9f99d}", ".ah-seg-noStatus,.ah-seg-other{fill:#d9f99d}", ".ah-lt{font-weight:600}",
                      "body.theme-light .ah-c-none{color:#4f46e5}", "body.theme-light .ah-seg-noStatus,body.theme-light .ah-seg-other{fill:#4f46e5}",
                      ".ah-gridy{stroke:rgba(255,255,255,0.10)}.ah-gridx{stroke:rgba(255,255,255,0.06)}", "body.theme-light .ah-gridy{stroke:rgba(0,0,0,0.14)}body.theme-light .ah-gridx{stroke:rgba(0,0,0,0.08)}"]) {
    assert.ok(KERNEL.includes(rule), rule);
  }
  for (const gone of [".ah-sw{", ".ah-lsw{", ".ah-sw-r429{", ".ah-sw-r5xx{", ".ah-sw-none{", "body.theme-light .ah-sw-"]) assert.ok(!KERNEL.includes(gone), gone + " is gone");
  assert.ok(KERNEL.includes(".ah-c-r429{color:#ef6b6f}.ah-c-r5xx{color:#e879f9}") && KERNEL.includes("body.theme-light .ah-c-r429{color:#B02A1C}body.theme-light .ah-c-r5xx{color:#86198F}"));
});
