// The Chat pane's Model section (the user 2026-09-17): two kernel-side switches, Always fast and Retry upgrades after
// downgrades, in the house grammar of every kernel setting — a stamped emitter under the store's own name, the stale maps,
// the /version fill, the mixed mark, membership in the federation broadcast set — sitting between Thinking and Chat history.
// Source pins (the gear has no DOM harness); the behaviour lives in the kernel and the SDK backend (tests/test_always_fast.py,
// tests/test_retry_upgrade.py, tests/test_model_switches_kernel.py).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const GEAR = read("gear.js");
const FED = read("federation.ts");
const DOCS = fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "reference.md"), "utf8");

function chatPane(): string {
  const a = GEAR.indexOf("'<div class=rs-pane data-pane=chat hidden>' +");
  const b = GEAR.indexOf("'<div class=rs-pane data-pane=feed hidden>' +", a);
  assert.ok(a > 0 && b > a, "the Chat pane's opener and the Feed pane's after it");
  return GEAR.slice(a, b);
}

test("the Model section sits between Thinking and Chat history, with the two rows in the house grammar", () => {
  const p = chatPane();
  const thinking = p.indexOf(">Thinking</div>"), model = p.indexOf(">Model</div>"), history = p.indexOf(">Chat history</div>");
  assert.ok(thinking > 0 && model > thinking && history > model, "Thinking < Model < Chat history: " + [thinking, model, history].join(","));
  for (const [id, label] of [["rs-alwaysfast", "Always fast"], ["rs-retryupgrade", "Retry upgrades after downgrades"]]) {
    const re = new RegExp("<label class='rs-row'><input type=checkbox id=" + id + ">\" \\+\\n\\s*'<span><b>" + label + "</b><span class=rs-mixed hidden></span>'");
    assert.match(p, re, id + ": a checkbox row, the label bold, the mixed mark inside the label (fillMixedMarks finds it by closest('label'))");
    assert.ok(p.indexOf("id=" + id) > model && p.indexOf("id=" + id) < history, id + " is in the Model section");
  }
  assert.equal((p.match(/id=rs-alwaysfast\b/g) || []).length, 1); assert.equal((p.match(/id=rs-retryupgrade\b/g) || []).length, 1);
});

test("the rows' words: off by default, follows to every connected machine, and what each does in the chat's own terms", () => {
  const p = chatPane();
  const sub = (id: string) => { const i = p.indexOf("id=" + id); return p.slice(i, p.indexOf("</span></label>", i)); };
  const af = sub("rs-alwaysfast"), ru = sub("rs-retryupgrade");
  for (const s of [af, ru]) {
    assert.match(s, /Off by default\./); assert.match(s, /Follows to every connected machine's kernel\./);
    assert.equal((s.match(/class=rs-sub/g) || []).length, 1, "one rs-sub per row (the hover popover)");
  }
  assert.match(af, /fast mode/); assert.match(af, /Opus-only, billed at a premium/); assert.match(af, /set to Slow from its statusline stays slow/);
  assert.match(ru, /lower tier without a pick/); assert.match(ru, /every ten minutes, at a turn boundary/); assert.match(ru, /a card says when it is back/);
  assert.doesNotMatch(af + ru, /fleet/i);
});

test("each switch is a stamped kernel setting: the emitter under its store, the stale maps, the fill, the mixed mark, the broadcast set", () => {
  assert.match(GEAR, /afb = document\.getElementById\('rs-alwaysfast'\), rub = document\.getElementById\('rs-retryupgrade'\)/);
  assert.match(GEAR, /if \(afb\) afb\.addEventListener\('change', function \(\) \{ post\(\{ type: 'setAlwaysFast', enabled: afb\.checked, gt: gclock\.stamp\('always-fast'\) \}\); \}\);/);
  assert.match(GEAR, /if \(rub\) rub\.addEventListener\('change', function \(\) \{ post\(\{ type: 'setRetryUpgrade', enabled: rub\.checked, gt: gclock\.stamp\('retry-upgrade'\) \}\); \}\);/);
  assert.match(GEAR, /'always-fast': 'Always fast', 'retry-upgrade': 'Retry upgrades after downgrades'/, "STALE_LABELS");
  assert.match(GEAR, /'always-fast': 'setAlwaysFast', 'retry-upgrade': 'setRetryUpgrade'/, "STALE_TYPE");
  assert.match(GEAR, /\['alwaysFast', afb\], \['retryUpgrade', rub\]\]\.forEach\(function \(pair\) \{/, "the mixed marks read the cross-machine settings dict");
  assert.match(GEAR, /if \(afb && typeof v\.alwaysFast === 'string'\) afb\.checked = v\.alwaysFast === 'on';/, "filled RAW from /version");
  assert.match(GEAR, /if \(rub && typeof v\.retryUpgrade === 'string'\) rub\.checked = v\.retryUpgrade === 'on';/);
  assert.match(FED, /"setAlwaysFast", "setRetryUpgrade"\]\);/, "one value across machines: the ops reach every attached kernel");
  assert.doesNotMatch(GEAR, /Date\.now\(\)[^\n]*setAlwaysFast|setAlwaysFast[^\n]*Date\.now\(\)/, "stamped through the gesture clock, never the device clock");
});

test("the reference documents both switches where fast mode is described", () => {
  assert.match(DOCS, /^### Always fast, and retrying an upgrade after a downgrade$/m);
  assert.match(DOCS, /\*\*Always fast\*\* runs every session in fast mode whenever its model allows it/);
  assert.match(DOCS, /\*\*Retry upgrades after downgrades\*\* acts when a session's model changes to a\s+lower tier without a pick/);
  assert.match(DOCS, /never the literal `\/fast on`/);
});
