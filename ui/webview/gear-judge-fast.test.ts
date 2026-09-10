// Fast mode per judge tier (T300, the user 2026-09-10), the add-on over the box the user's session put on the
// Triage model row: the same compact box follows the Distilling and Indexing pickers, each tier greys its own
// box with the hint saying why when THAT tier's effective model cannot run fast (Follow triage resolves to the
// triage pick), the value is kept, and each box posts its own stamped op under its own store. These pins hold
// the gear's side; tests/test_judge_fast_tiers.py holds the kernel's; gear-judge-fast-browser.test.ts drives
// the real gear in Chromium.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const read = (...p: string[]) => fs.readFileSync(path.join(ROOT, ...p), "utf8");
const GEAR = read("ui", "webview", "gear.js");
const FED = read("ui", "webview", "federation.ts");

test("each tier's model row carries the same compact Fast mode box after its picker", () => {
  for (const [sel, tier] of [["rs-judgemodel", "judgefast"], ["rs-distillmodel", "distillfast"], ["rs-indexmodel", "indexfast"]]) {
    const row = GEAR.slice(GEAR.indexOf(`<select id=${sel}></select>`));
    const end = row.indexOf("</div>");
    const seg = row.slice(0, end);
    assert.ok(seg.includes(`<label class=rs-fastin id=rs-${tier}-wrap><input type=checkbox id=rs-${tier}>Fast mode<span class=rs-mixed hidden></span>`),
      `${tier}: the box follows the picker inside the same row, with its own mixed mark`);
    assert.ok(seg.includes(`<span class=rs-sub id=rs-${tier}-sub>`), `${tier}: a row hint the gate swaps`);
  }
  assert.ok(!GEAR.includes("Fast judging"), "the old label stays gone");
});

test("the gate greys each tier's box on ITS effective model and keeps the value", () => {
  assert.ok(GEAR.includes("function judgeFastTiers"), "one table of tiers");
  assert.ok(GEAR.includes("function judgeFastGate"), "the gate");
  assert.ok(GEAR.includes("t.box.disabled = !can;"), "greyed, not hidden, not unchecked");
  assert.ok(GEAR.includes("wrap.classList.toggle('rs-off', !can);"), "the greyed look per box");
  assert.ok(GEAR.includes("v === 'triage' ? (jm ? (jm.value || '') : '') : v"), "Follow triage resolves to the triage pick");
  assert.ok(GEAR.includes("can = !v || v.toLowerCase().indexOf('opus') !== -1"), "the Opus-only rule; an unloaded value is unknown, not a refusal");
  assert.ok(!GEAR.includes("t.box.checked = false"), "no gesture unchecks a box: the setting is the user's to keep");
  assert.match(GEAR, /var JUDGEFAST_SUB_OFF = "Fast mode is Opus-only, and this tier is not on Opus\./, "the hint says why, per tier");
  assert.ok(GEAR.includes("sub.textContent = !can ? JUDGEFAST_SUB_OFF : (r ? judgeFastSubRefused(t.word, r) : JUDGEFAST_SUB)"),
    "the hint: the greyed reason, else the CLI's refusal of this tier's last fast ask, else the description");
  for (const store of ["judge-model", "index-model", "distill-model"])
    assert.match(GEAR, new RegExp("gclock\\.stamp\\('" + store + "'\\) \\}\\); judgeFastGate\\(\\);"), `a ${store} pick re-runs the gate`);
  assert.match(GEAR, /cmtFastGate\(false\);\n\s*judgeFastGate\(\);/, "fill() re-checks after the tiers and the flags are set");
});

test("the CLI's refusal reaches the box's hint from /version", () => {
  assert.ok(GEAR.includes("fastRefused = (v.fastRefused && typeof v.fastRefused === 'object') ? v.fastRefused : {};"), "fill() reads the record");
  assert.ok(GEAR.includes("var r = can && t.box.checked ? fastRefused[t.tier] : null;"), "only a checked box on a capable model can have been refused");
  assert.ok(GEAR.includes("function judgeFastSubRefused"), "the refusal wording");
  for (const [el, ty, clock] of [["jf", "setJudgeFast", "judge-fast"], ["df", "setDistillFast", "distill-fast"], ["xf", "setIndexFast", "index-fast"]])
    assert.ok(GEAR.includes(`post({ type: '${ty}', enabled: ${el}.checked, gt: gclock.stamp('${clock}') }); judgeFastGate();`),
      `${ty}: the box's own toggle re-runs the gate, so a standing refusal's hint leaves with the tick and returns with it`);
  assert.match(GEAR, /tier: 'triage'[\s\S]*?tier: 'distill'[\s\S]*?tier: 'index'/, "the record's keys are the judge tiers' names");
});

test("each box posts its own stamped op under its own store, and fills from the raw flag", () => {
  for (const [type, clock, el] of [["setJudgeFast", "judge-fast", "jf"], ["setDistillFast", "distill-fast", "df"], ["setIndexFast", "index-fast", "xf"]]) {
    assert.ok(GEAR.includes(`post({ type: '${type}', enabled: ${el}.checked, gt: gclock.stamp('${clock}') })`), `${type}: the boolean, stamped in the literal`);
  }
  for (const [field, el] of [["judgeFast", "jf"], ["distillFast", "df"], ["indexFast", "xf"]])
    assert.ok(GEAR.includes(`${el}.checked = v.${field} === 'on'`), `fill() reads the RAW ${field} flag`);
  const typeSrc = GEAR.match(/var STALE_TYPE = \{([\s\S]*?)\};/);
  assert.ok(typeSrc, "STALE_TYPE located");
  for (const [clock, type] of [["judge-fast", "setJudgeFast"], ["distill-fast", "setDistillFast"], ["index-fast", "setIndexFast"]])
    assert.ok(typeSrc![1].includes(`'${clock}': '${type}'`), `the stale toast knows ${clock}`);
  assert.match(GEAR, /STALE_LABELS = \{[\s\S]*?'judge-fast': 'Fast mode \(triage judges\)', 'distill-fast': 'Fast mode \(distilling judges\)', 'index-fast': 'Fast mode \(indexing judges\)'/,
    "a stood-down gesture toasts under its tier's name");
  assert.ok(GEAR.includes("['judgeFast', jf], ['distillFast', df], ['indexFast', xf]"), "the mixed-mark reconcile covers the three boxes");
});

test("the three ops are kernel settings federation broadcasts to every host", () => {
  const setSrc = FED.match(/const KERNEL_SETTING = new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(setSrc, "federation's KERNEL_SETTING set must exist");
  for (const op of ["setJudgeFast", "setDistillFast", "setIndexFast"])
    assert.ok(setSrc![1].includes(`"${op}"`), `${op} must be a KERNEL_SETTING (one pick sets every machine)`);
});
