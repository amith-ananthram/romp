// The per-tier Fast mode boxes (T300), driven in headless Chromium over the REAL gear module (gear.js) and its
// stylesheet. gear-judge-fast.test.ts pins the source; a pin cannot see a box that fails to grey, a hint that
// never changes, or a gate that reads an unfilled select (a painted select reads its first option before /version
// answers). So the gear is opened against a fake kernel (the /models, /version, /tunnels, /palette routes answered
// with synthetic payloads), and the DOM is read after each gesture: a fill with triage on Opus, distilling following
// triage and indexing on Haiku greys only the indexing box, with the flags shown as stored, the greyed look on the
// box alone (its hint's ancestors all opaque, its word in the section grey, its hint unfaded and in the colour a live
// box's hint shows); a Triage pick of Sonnet greys the triage and distilling boxes, keeps them checked, swaps their
// hints, and posts nothing for them; picking Opus again ungreys them with the hints back; a checked box on a capable
// model whose last fast ask the CLI declined shows the refusal; ticking a box posts its op; pinning Distilling to
// Opus keeps its box live under a Sonnet triage.
// Skips with a stated reason without a playwright browser (CI installs none), the md-sanitize-browser.test.ts
// pattern. Synthetic values only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");
const GEAR_CSS = fs.readFileSync(path.join(UI, "gear.css"), "utf8");

const ENTRY = `
const { initGear } = require("./gear.js");
(window as any).__posts = [];
initGear((m: any) => { (window as any).__posts.push(m); });
`;

function bundle(): string {
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: ENTRY, resolveDir: UI, loader: "ts", sourcefile: "gear-judge-fast-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

const PAGE_HTML = `<!DOCTYPE html><html><head><meta charset=utf-8><style>${GEAR_CSS}</style></head><body>
<script src=/dist/gear.js></script></body></html>`;
const MODELS = { models: [{ value: "opus", label: "Opus", versions: [] }, { value: "sonnet", label: "Sonnet", versions: [] }, { value: "haiku", label: "Haiku", versions: [] }],
  efforts: [{ value: "", label: "Default" }, { value: "low", label: "Low" }] };
// the kernel's raw picks: triage on Opus with its box on, distilling following triage with its box on, indexing on
// Haiku with its box on too (a kept value on a tier that cannot run fast: greyed, not lost); the CLI declined the
// distilling tier's last fast ask
const VERSION = { judgeModel: "opus", judgeEffort: "", indexModel: "haiku", indexEffort: "low", distillModel: "triage", distillEffort: "triage",
  judgeConcurrency: "", commentModel: "session", commentEffort: "session", commentFast: "session",
  judgeFast: "on", distillFast: "on", indexFast: "on", fastRefused: { distill: { reason: "sdk_opt_in_required", model: "opus", t: 1 } },
  autoNudge: true, settingsGt: {}, updateMode: "off" };

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

type Snap = { disabled: Record<string, boolean>; off: Record<string, boolean>; opacity: Record<string, string>;
  hintFade: Record<string, string>; wordColor: Record<string, string>; checked: Record<string, boolean>; hint: Record<string, string> };

async function withGear(t: any, body: (page: any, errors: string[]) => Promise<void>): Promise<void> {
  if (!pw) { t.skip("playwright is not installed under vscode-extension; the browser leg needs it (CI installs no browsers)"); return; }
  let browser: any;
  try { browser = await pw.chromium.launch(); }
  catch (e) { t.skip("no playwright browser on this machine; the browser leg needs one (CI installs none): " + String((e as Error).message).split("\n")[0]); return; }
  const errors: string[] = [];
  try {
    const js = bundle();
    const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    await page.route("**/*", (route: any) => {
      const u = new URL(route.request().url());
      const json = (o: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(o) });
      if (u.hostname !== "romp.test") return route.fulfill({ status: 404, body: "" });
      if (u.pathname === "/gear") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: PAGE_HTML });
      if (u.pathname === "/dist/gear.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: js });
      if (u.pathname === "/models") return json(MODELS);
      if (u.pathname === "/version") return json(VERSION);
      if (u.pathname === "/tunnels") return json({ tunnels: [] });
      return json({});   // /palette, /analytics and the rest: empty answers, nothing under test
    });
    await page.goto("http://romp.test/gear");
    await page.waitForFunction(() => Array.isArray((window as any).__posts) && !!document.getElementById("rs-judgemodel"), null, { timeout: 10000 });
    await page.evaluate(() => { window.postMessage({ romp: "openSettings" }, "*"); });
    // the fill has run once a box holds the kernel's flag: only fill() writes it, and the gate runs in the same
    // callback right after (the triage select's value is no signal: a painted select reads its first option,
    // "opus" here, before /version has answered)
    await page.waitForFunction(() => (document.getElementById("rs-indexfast") as HTMLInputElement).checked === true, null, { timeout: 10000 });
    await body(page, errors);
  } finally {
    await browser.close();
  }
}

const snap = (page: any): Promise<Snap> => page.evaluate(() => {
  const disabled: Record<string, boolean> = {}, off: Record<string, boolean> = {}, opacity: Record<string, string> = {};
  const hintFade: Record<string, string> = {}, wordColor: Record<string, string> = {};
  const checked: Record<string, boolean> = {}, hint: Record<string, string> = {};
  for (const tier of ["judgefast", "distillfast", "indexfast"]) {
    const box = document.getElementById("rs-" + tier) as HTMLInputElement;
    const wrap = document.getElementById("rs-" + tier + "-wrap") as HTMLElement;
    const sub = document.getElementById("rs-" + tier + "-sub") as HTMLElement;
    disabled[tier] = box.disabled; off[tier] = wrap.classList.contains("rs-off");
    opacity[tier] = getComputedStyle(box).opacity;   // the box's own: the greyed look fades the box alone
    // the fade the hint is seen through: opacity is not inherited, so the hint's own computed value reads 1 whatever
    // its ancestors do; the most faded ancestor below the settings backdrop is the one the eye sees the hint through
    let fade = 1;
    for (let el = sub.parentElement; el && el.id !== "rsettings"; el = el.parentElement) fade = Math.min(fade, parseFloat(getComputedStyle(el).opacity));
    hintFade[tier] = String(fade);
    wordColor[tier] = getComputedStyle(wrap).color;   // the label's colour is the word's: "Fast mode" is a bare text node
    checked[tier] = box.checked; hint[tier] = sub.textContent || "";
  }
  return { disabled, off, opacity, hintFade, wordColor, checked, hint };
});
const posts = (page: any, type: string): Promise<any[]> => page.evaluate((ty: string) => (window as any).__posts.filter((m: any) => m && m.type === ty), type);
// the judge selects are display:none behind versionMenu's button; a pick is what the user does: open the menu
// after the select, click the family row (a family with one version or none commits on the row itself)
async function pickModel(page: any, selId: string, label: string): Promise<void> {
  await page.click(`#${selId} + button.rs-vermenu-btn`);
  // the menu is a body-level div the facade positions (its inline style re-serializes, so no attribute match);
  // its rows are the focusable direct children, labelled by family
  await page.locator('body > div > div[tabindex="0"]', { hasText: label }).first().click();
  await page.waitForFunction(([id, want]: [string, string]) => {
    const sel = document.getElementById(id) as HTMLSelectElement;
    return sel && Array.from(sel.options).some((o) => o.textContent === want && o.value === sel.value);
  }, [selId, label] as [string, string], { timeout: 5000 });
}

test("each tier's box greys on its own effective model, keeps its value, says why, and posts only its own clicks", { timeout: 90000 }, async (t) => {
  await withGear(t, async (page, errors) => {
    // 1. filled: triage Opus (live), distilling follows triage (live, but the CLI declined its last ask), indexing Haiku (greyed, value kept)
    let s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: false, distillfast: false, indexfast: true }, "greyed for the Haiku tier only");
    assert.deepEqual(s.off, { judgefast: false, distillfast: false, indexfast: true });
    assert.equal(s.opacity.indexfast, "0.4", "the greyed look renders on the box"); assert.equal(s.opacity.judgefast, "1");
    assert.deepEqual(s.hintFade, { judgefast: "1", distillfast: "1", indexfast: "1" }, "the hint's ancestors are all opaque: the greyed reason stays legible");
    assert.notEqual(s.wordColor.indexfast, s.wordColor.judgefast, "the greyed word takes the section grey; the live word keeps the row's colour");
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true }, "the boxes reflect the kernel's raw flags, greyed or not");
    assert.match(s.hint.judgefast, /^This tier's judge calls run in Claude Code's fast mode/, "a live box: the description");
    assert.match(s.hint.distillfast, /^Fast mode was declined by Claude Code for the last distilling call \(sdk_opt_in_required\)/, "a declined ask: the CLI's reason");
    assert.match(s.hint.indexfast, /^Fast mode is Opus-only, and this tier is not on Opus\./, "a greyed box: why");
    for (const ty of ["setJudgeFast", "setDistillFast", "setIndexFast"]) assert.deepEqual(await posts(page, ty), [], `${ty}: a fill posts nothing`);
    // hovering the greyed box shows its hint unfaded, in the colour a live box's hint shows: the label's grey stops at
    // the word, and no rule fades the hint itself (its own opacity reads 1 before the fix too, so this is a guard)
    const hovered = (tier: string) => page.evaluate((id: string) => {
      const sub = document.getElementById("rs-" + id + "-sub") as HTMLElement, wrap = document.getElementById("rs-" + id + "-wrap") as HTMLElement;
      return { display: getComputedStyle(sub).display, color: getComputedStyle(sub).color, opacity: getComputedStyle(sub).opacity, word: getComputedStyle(wrap).color };
    }, tier);
    await page.hover("#rs-indexfast-wrap");
    const shown = await hovered("indexfast");
    await page.hover("#rs-judgefast-wrap");
    const live = await hovered("judgefast");
    assert.equal(shown.display, "block", "hovering the greyed box shows its hint");
    assert.equal(shown.opacity, "1", "the hint itself is not faded");
    assert.notEqual(shown.color, shown.word, "the hint keeps its own colour, not the greyed word's");
    assert.equal(live.display, "block"); assert.equal(shown.color, live.color, "the greyed box's hint shows in the colour a live box's hint does");

    // 2. the user picks Sonnet for triage: the model post, both dependent boxes grey and KEEP their value, hints swap, no box post
    await pickModel(page, "rs-judgemodel", "Sonnet");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: true, distillfast: true, indexfast: true }, "no tier can run fast now");
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true }, "the values are kept: nothing was unchecked");
    for (const tier of ["judgefast", "distillfast"]) assert.match(s.hint[tier], /^Fast mode is Opus-only, and this tier is not on Opus\./, `${tier}: greyed, says why`);
    const model = await posts(page, "setJudgeModel");
    assert.equal(model.length, 1); assert.equal(model[0].model, "sonnet"); assert.equal(typeof model[0].gt, "number");
    for (const ty of ["setJudgeFast", "setDistillFast", "setIndexFast"]) assert.deepEqual(await posts(page, ty), [], `${ty}: a model pick posts nothing for the boxes`);

    // 3. Opus again: live again, still checked, the hints back (the declined one included: the record stands until the kernel clears it)
    await pickModel(page, "rs-judgemodel", "Opus");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: false, distillfast: false, indexfast: true });
    assert.deepEqual(s.checked, { judgefast: true, distillfast: true, indexfast: true });
    assert.match(s.hint.judgefast, /^This tier's judge calls/); assert.match(s.hint.distillfast, /^Fast mode was declined/);

    // 4. clicking the triage box posts its own stamped op, and only it
    await page.uncheck("#rs-judgefast");
    const jOff = await posts(page, "setJudgeFast");
    assert.deepEqual(jOff.map((m: any) => m.enabled), [false]); assert.equal(typeof jOff[0].gt, "number");
    assert.deepEqual(await posts(page, "setDistillFast"), []);
    // ...and the hint follows the box: unticking the distilling box (a standing refusal) drops the refusal wording for a
    // tier that no longer asks; ticking it again brings it back (the record stands until the kernel clears it)
    await page.uncheck("#rs-distillfast");
    s = await snap(page);
    assert.match(s.hint.distillfast, /^This tier's judge calls/, "unticked: the plain description");
    await page.check("#rs-distillfast");
    s = await snap(page);
    assert.match(s.hint.distillfast, /^Fast mode was declined/, "ticked again: the refusal is said again");
    assert.deepEqual((await posts(page, "setDistillFast")).map((m: any) => m.enabled), [false, true]);

    // 5. distilling pinned to Opus stays live under a Sonnet triage; a greyed box refuses the click (disabled)
    await pickModel(page, "rs-distillmodel", "Opus");
    await pickModel(page, "rs-judgemodel", "Sonnet");
    s = await snap(page);
    assert.deepEqual(s.disabled, { judgefast: true, distillfast: false, indexfast: true }, "a pinned Opus distilling tier stays fast-capable");
    assert.equal(await page.evaluate(() => (document.getElementById("rs-indexfast") as HTMLInputElement).disabled), true);
    assert.deepEqual(errors, [], "no page errors");
  });
});
