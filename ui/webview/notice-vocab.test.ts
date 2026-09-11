// The notice VOCABULARY census (2026-09-08): the pins that keep the one-notice design from rotting — the same
// spirit as css-vocab.test.ts / button-vocab.test.ts. The audit that motivated this found, in one card family,
// eight head sizes, seven radii, six chip dresses, raw colours in a dozen rules and four fold stores. Each pin
// below names the rule it holds:
//   (a) every rule whose selector contains .notice resolves colour through tokens (a hex/rgba only inside a
//       var() fallback) and sets no px font-size;
//   (b) ONE font-size per role — gist 0.92em · src 0.72em · meta 0.82em · body 0.92em · caret 0.72em · buttons
//       --btn-fs-sm — and no other .notice* rule sets one;
//   (c) no .notice* rule sets `color:` to a status token (--st-*, --err, --warn): severity is the rail, dot and
//       glyph, never text (the light theme's status hues sit under 3:1 as text);
//   (d) render.ts hangs no click listener inside notice(), and the head's toggle is data-act=noticetoggle;
//   (e) the retired renderers' selectors are gone from the sheet and their class strings from render.ts;
//   (f) the four fold stores merged into openFolds;
//   (g) the gallery fixture is checked in (tools/ui-verify/fixtures/notices-chat.html) as the review artefact.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const CSS = read("ui", "webview", "styles.css");
const RENDER = read("ui", "webview", "render.ts");
const RENDER_CODE = RENDER.replace(/\/\/[^\n]*/g, "");   // comments may NAME the retired identifiers; code may not use them
const noComments = CSS.replace(/\/\*[\s\S]*?\*\//g, "");

// every rule (selector → body) whose selector mentions .notice; the theme token blocks are declarations, not rules
const noticeRules: Array<[string, string]> = [];
for (const m of noComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  const sel = m[1].trim();
  if (sel.includes(".notice")) noticeRules.push([sel, m[2]]);
}

test("(a) .notice rules resolve colour through tokens only, and set no px font-size", () => {
  assert.ok(noticeRules.length >= 30, "the notice family was found: " + noticeRules.length + " rules");
  for (const [sel, body] of noticeRules) {
    const stripped = body.replace(/var\([^()]*(?:\([^()]*\)[^()]*)*\)/g, "V");   // a var(--x, <fallback>) is paid down
    assert.doesNotMatch(stripped, /#[0-9a-fA-F]{3,8}\b|rgba?\(/, "a raw colour in `" + sel + "`");
    assert.doesNotMatch(body, /font-size:\s*[\d.]+px/, "a px font-size in `" + sel + "`");
  }
});

test("(b) ONE font-size per role, and no other .notice* rule sets one", () => {
  const sizes = new Map<string, string>();
  for (const [sel, body] of noticeRules) {
    const m = body.match(/font-size:\s*([^;]+);/);
    if (!m) continue;
    assert.ok(!sizes.has(sel), "two font-size declarations for `" + sel + "`");
    sizes.set(sel, m[1].trim());
  }
  assert.deepEqual([...sizes.entries()].sort(), [
    [".notice-act", "var(--btn-fs-sm)"],
    [".notice-body", "0.92em"],
    [".notice-caret", "0.72em"],
    [".notice-gist", "0.92em"],
    [".notice-meta", "0.82em"],
    [".notice-src", "0.72em"],
  ].sort(), "the whole size vocabulary of the notice family");
});

test("(c) no .notice* rule paints TEXT in a status colour — severity is rail + dot + glyph", () => {
  for (const [sel, body] of noticeRules) {
    for (const m of body.matchAll(/(?:^|;|\s)color:\s*([^;]+);/g)) {
      assert.doesNotMatch(m[1], /--st-|--err|--warn/, "a status colour as text in `" + sel + "`: " + m[1]);
    }
  }
  // the severity map itself: seven severities, each a rail + dot pair on existing tokens
  for (const [sev, tok] of [["info", "--dim"], ["romp", "--accent"], ["warn", "--warn"], ["err", "--st-blocked-bg"], ["compact", "--st-compacting-bg"], ["retry", "--st-retrying-bg"], ["needs", "--st-awaiting-bg"]]) {
    assert.match(CSS, new RegExp("\\.notice-sev-" + sev + "\\s+\\{ --notice-rail: var\\(" + tok + "\\);"), "severity " + sev);
  }
});

test("(d) notice() hangs no click listener; the head toggles through data-act=noticetoggle on the body delegate", () => {
  const fn = RENDER.split("function notice(spec: NoticeSpec): HTMLElement {")[1].split("\nfunction ")[0];
  assert.equal((fn.match(/addEventListener\("click"/g) || []).length, 0);
  assert.match(fn, /head\.dataset\.act = "noticetoggle";/);
  assert.match(RENDER, /\n    noticetoggle: \(el\) => \{/);
  // …and no adapter re-grew a per-node head listener on the notice family
  for (const name of ["renderAgentNotif", "renderInjected", "renderCompact", "renderClear", "renderPostalService", "renderTeammate", "renderTodo", "renderApiError", "renderApiErrorNote", "renderModelFallback", "renderSystem", "renderRetrying", "renderToolGroup", "renderNoticeGroup"]) {
    const body = RENDER.split("function " + name + "(")[1].split("\nfunction ")[0];
    assert.doesNotMatch(body, /addEventListener\("click"|\.onclick =/, name + " hangs a per-node click listener");
  }
});

test("(e) the retired renderers' selectors are gone from the sheet, and their class strings from render.ts", () => {
  for (const sel of [".apierror-card", ".apierror-retry", ".apierror-stop", ".retried-line", ".retrying-line", ".retrying-stop", ".retrying-err",
                     ".compacting-inline", ".reconnecting-line", ".clearing-line", ".effort-line", ".interrupt-line", ".modelswap-line",
                     ".teammate-card", ".notice-chip", ".postal-service-summary", ".postal-service-card", ".todo-head", ".ask-head",
                     ".user-note", ".branch-label", ".tx-hostoff", ".sys-card", ".notice-card", ".undelivered-act"]) {
    assert.ok(!noComments.includes(sel + " ") && !noComments.includes(sel + ",") && !noComments.includes(sel + "{") && !noComments.includes(sel + ":") && !noComments.includes(sel + "."),
      sel + " still has a rule");
  }
  for (const cls of ['"apierror-', '"retried-line', '"retrying-line', '"retrying-stop', '"retrying-err', '"compacting-inline', '"reconnecting-line',
                     '"clearing-line', '"effort-line', '"interrupt-line', '"modelswap', '"teammate-card', '"teammate-tag', '"notice-chip',
                     '"postal-service-summary', '"postal-service-card', '"todo-head', '"ask-head', '"user-note', '"branch-label', '"tx-hostoff',
                     '"sys-card', '"undelivered-act', "noticeCard("]) {
    assert.ok(!RENDER_CODE.includes(cls), cls + " is still built in render.ts");
  }
});

test("(f) the four fold stores merged into openFolds: fuExpanded, expandedGroups, bgFoldOpen, bgExpanded are gone", () => {
  for (const id of ["fuExpanded", "expandedGroups", "bgFoldOpen", "bgExpanded"]) {
    assert.ok(!new RegExp("\\b" + id + "\\b").test(RENDER_CODE), id + " still exists");
  }
  assert.match(RENDER, /const openFolds = new Set<string>\(\);/);
  // the merged keys, by prefix
  for (const k of ['"notice:" + spec.key', '"fu:" + k', '"bgfold:" + sid', '"bgrow:" + t.id', 'return "tg:" +', 'return "ng:" +']) {
    assert.ok(RENDER.includes(k), "fold key " + k);
  }
});

test("(g) the gallery fixture is checked in and mirrors the builder class-for-class (both themes render it)", () => {
  const fx = read("tools", "ui-verify", "fixtures", "notices-chat.html");
  assert.match(fx, /SYNTHETIC/, "the fixture declares itself synthetic (privacy rule)");
  for (const cls of ["notice-sev-info", "notice-sev-romp", "notice-sev-err", "notice-sev-compact", "notice-sev-retry", "notice-sev-warn",
                     "notice-slim", "notice-boxed", "notice-nested", "notice-head", "notice-glyph", "notice-src", "notice-gist", "notice-meta",
                     "notice-acts", "notice-act", "notice-body", "notice-src-chip", "romp-bubble", "queued-bubble", "undelivered-note",
                     "day-divider-rule"]) {   // the divider's two hairlines around its date (T339)
    assert.ok(fx.includes(cls), "fixture shows ." + cls);
  }
  assert.match(fx, /data-act="noticetoggle"/);
  assert.doesNotMatch(fx, /file:\/\/\/home|\/Users\//, "no absolute home paths in a checked-in fixture");
});

test("the two tokens exist in both sheets, both themes; dark stays byte-identical, light re-inks working + compacting (decision 3)", () => {
  for (const sheet of ["styles.css", "feed.css"]) {
    const css = read("ui", "webview", sheet);
    const dark = css.slice(css.indexOf(":root {"), css.indexOf("\n}", css.indexOf(":root {")));
    const light = css.slice(css.indexOf("body.theme-light {"), css.indexOf("\n}", css.indexOf("body.theme-light {")));
    assert.match(dark, /--radius-card: 6px;/, sheet + " dark --radius-card");
    assert.match(light, /--radius-card: 6px;/, sheet + " light --radius-card");
    assert.match(dark, /--st-retrying-bg: #e67e22; --st-retrying-fg: #2a1500;/, sheet + " dark retrying");
    assert.match(light, /--st-retrying-bg: #9C4A0C; --st-retrying-fg: #ffffff;/, sheet + " light retrying");
    assert.match(dark, /--st-working-bg: #e0b020; --st-working-fg: #332600;/, sheet + " dark working unchanged");
    assert.match(light, /--st-working-bg: #8B6914; --st-working-fg: #ffffff;/, sheet + " light working re-inked");
    assert.match(light, /--st-compacting-bg: #0F766E; --st-compacting-fg: #ffffff;/, sheet + " light compacting re-inked");
  }
  assert.match(CSS, /--st-compacting-bg: #14b8a6; --st-compacting-fg: #ffffff;/, "dark compacting unchanged");
  assert.match(CSS, /\.notice \{[^}]*border-radius: var\(--radius-card\)/);
});

test("ONE duration format: durLabel (duration.ts) serves the footer, the work timer, the strip's reset and every countdown", () => {
  const DUR = read("ui", "webview", "duration.ts");
  assert.match(DUR, /export function durLabel\(secs: number\): string/);
  assert.match(RENDER, /import \{ durLabel \} from "\.\/duration";/);
  assert.match(read("ui", "webview", "strip.ts"), /import \{ durLabel \} from "\.\/duration";/);
  assert.doesNotMatch(RENDER, /function durLabel\(/, "render.ts no longer owns a private copy");
  assert.match(RENDER, /return durLabel\(\(Date\.now\(\) - sinceMs\) \/ 1000\);/, "elapsedMs rides it");
  assert.match(RENDER, /`retrying in \$\{durLabel\(left\)\}`/);
  assert.match(RENDER, /`next try in \$\{durLabel\(s\)\}`/);
});
