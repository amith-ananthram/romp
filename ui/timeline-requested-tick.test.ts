// The timeline's model picker marks the REQUESTED model with a yellow ✓ while an automatic fallback answers instead (the
// user 2026-09-17), the twin of the chat picker's .meta-item.requested (model-requested-tick.test.ts): the family row, its
// version row, and the submenu's Latest row for an alias pick, each with the tooltip saying why and whether romp is
// retrying. Source pins over ui/romp-timeline-view.js plus the tooltip builder executed as written; the two surfaces'
// sentences are pinned equal so they cannot drift apart.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function fnSource(src: string, name: string): string {
  const i = src.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} is defined`);
  const j = src.indexOf("\n}\n", i);
  return src.slice(i, j + 3);
}
const tlTip = new Function(fnSource(TL, "requestedModelTip") + "\nreturn requestedModelTip;")() as (fb: any) => string;
const chatTip = new Function(fnSource(RENDER, "requestedModelTip").replace(/: ModelFallback\)/, ")").replace(/\): string \{/, ") {") + "\nreturn requestedModelTip;")() as (fb: any) => string;
const tlSub = new Function(fnSource(TL, "requestedModelSub") + "\nreturn requestedModelSub;")() as (fb: any) => string;
const chatSub = new Function(fnSource(RENDER, "requestedModelSub").replace(/: ModelFallback\)/, ")").replace(/\): string \{/, ") {") + "\nreturn requestedModelSub;")() as (fb: any) => string;
const fb = (over: any = {}) => ({ pick: "Fable 5.1", pickValue: "claude-fable-5-1", live: "Opus 5", cause: "safeguards", category: "bio",
  retry: { on: true, everyMin: 10, armed: true, nextIn: 250, attempts: 1 }, ...over });

test("the timeline's tooltip is the chat's, sentence for sentence", () => {
  for (const f of [fb(), fb({ category: "" }), fb({ cause: "", category: "" }),
                   fb({ retry: { on: true, everyMin: 10, armed: false, nextIn: null, attempts: 0 } }),
                   fb({ retry: { on: false, everyMin: 10, armed: false, nextIn: null, attempts: 0 } })]) {
    assert.equal(tlTip(f), chatTip(f));
  }
  assert.equal(tlTip(fb()), "Requested model blocked due to safety classifiers (bio). Retrying every 10 minutes. Next attempt in 5 min.");
});

test("the timeline's sub-line is the chat's, and each requested row wears it", () => {
  for (const f of [fb(), fb({ category: "" }), fb({ cause: "", category: "" }), fb({ retry: { on: false, everyMin: 10, armed: false, nextIn: null, attempts: 0 } })]) {
    assert.equal(tlSub(f), chatSub(f));
  }
  assert.match(TL, /const rsub = item\.createDiv\(\{ text: requestedModelSub\(fb\) \}\);/);
  assert.match(TL, /const vsub = row\.createDiv\(\{ text: requestedModelSub\(fb\) \}\);/);
  assert.match(TL, /lsub\.textContent = requestedModelSub\(fb\) \+ ' — ' \+ lsub\.textContent;/);
});

test("the mark is the working-state yellow in both palettes, in the menu's mark box, set with the other menu styles", () => {
  assert.match(TL, /workingBg: '#E0B020', workingFg: '#332600'/, "dark: styles.css --st-working-bg/fg");
  assert.match(TL, /workingBg: '#8B6914', workingFg: '#ffffff'/, "light: body.theme-light's working yellow");
  assert.match(TL, /const menuRequestedStyleFor = \(p\) => MENU_MARK_BOX \+ 'background:' \+ p\.workingBg \+ ';color:' \+ p\.workingFg \+ ';';/);
  assert.match(TL, /MENU_REQUESTED_STYLE = menuRequestedStyleFor\(p\);/, "applyPal sets it with the others");
});

test("the family row, the Latest row and the version row wear it, never over the current ✓, with the tooltip as title", () => {
  assert.match(TL, /const fb = kind === 'model' \? \(s\.modelFallback \|\| null\) : null;/, "the lane row's modelFallback (build_timeline)");
  assert.match(TL, /if \(fb && !cur && isRequestedFamily\(fb, c\.value\)\) \{\s*\n\s*const rq = item\.createSpan\(\{ text: '✓' \}\); rq\.setAttribute\('style', MENU_REQUESTED_STYLE\);\s*\n\s*item\.setAttribute\('title', requestedModelTip\(fb\)\);/);
  assert.match(TL, /else if \(fb && !\(fb\.pickValue \|\| ''\)\.toLowerCase\(\)\.startsWith\('claude-'\) && isRequestedFamily\(fb, c\.value\)\) \{/, "an alias pick marks Latest");
  assert.match(TL, /else if \(fb && isRequestedVersion\(fb, v\)\) \{\s*\n\s*const rq = row\.createSpan\(\{ text: '✓' \}\); rq\.setAttribute\('style', MENU_REQUESTED_STYLE\);/);
  assert.match(TL, /row\.setAttribute\('title', note \? requestedModelTip\(fb\) \+ '\\n' \+ note : requestedModelTip\(fb\)\);/, "a requested learned version keeps both");
});
