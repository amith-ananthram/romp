import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// The picker's Tags row shows each tag AS THE TAG CHIP (T321, the user 2026-09-10): the thin border in the tag's own
// colour that the tab strip, the feed and the outline draw, and on versus off by the visual the tag toggles already
// use, the faded chip (TAG_CHIP_OFF_CLASS at 0.45), never a dot. The `sel` class on the option stays the state the
// create reads and the tests pin; the chip inside is repainted from it on every click.
const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const CSS = ui("webview", "styles.css");
const FEED_CSS = ui("webview", "feed.css");
const MENU = ui("webview", "tag-menu.ts");
const REBUILD = RENDER.slice(RENDER.indexOf('const tgWrapEl = overlay.querySelector(".picker-tags")'), RENDER.indexOf('applyBrowseState("");'));

test("each option is the shared tag chip: the tag's colour on a thin border, the faded chip for off, no dot", () => {
  assert.match(RENDER, /import \{[^}]*\btagChip\b[^}]*\} from "\.\/tag-menu";/, "the one chip helper every surface shares");
  assert.match(REBUILD, /const b = el\("button", "picker-be-opt" \+ \(preset\.has\(u\.name\) \? " sel" : ""\)\) as HTMLButtonElement;/, "the option and its state class stay");
  assert.match(REBUILD, /paintPickerTagChip\(b, u\);/, "painted from the state class, on build…");
  assert.match(REBUILD, /b\.addEventListener\("click", \(\) => \{ b\.classList\.toggle\("sel"\); paintPickerTagChip\(b, u\); \}\);/, "…and on every click (multi-select: each chip on its own)");
  assert.match(RENDER, /function paintPickerTagChip\(b: HTMLButtonElement, u: \{ name: string; color\?: string \| null \}\): void \{\s*\n\s*b\.replaceChildren\(tagChip\(u\.name, u\.color, \{ inheritSize: true, off: !b\.classList\.contains\("sel"\) \}\)\);/,
    "selected = the full chip, unselected = the off chip; the button's size, not a second 0.82em");
  assert.doesNotMatch(RENDER, /picker-tag-dot/, "the dot is gone from the pane");
  assert.doesNotMatch(CSS, /picker-tag-dot/, "…and from the sheet");
  assert.doesNotMatch(RENDER, /ctx-tag-dot/, "the tab menu's Tags flyout wears the chip too: no dot anywhere a tag shows (T321)");
});

test("the option wears no button chrome around the chip at rest, so the chip's border is the whole look", () => {
  assert.match(CSS, /\n\.picker-tags \.picker-be-opt \{ padding: 0; border: none; background: transparent; font-family: inherit; display: inline-flex; border-radius: 9px; filter: none; \}\n/,
    "no weight on the host (the chip carries 400) and the page's typeface: a bare button wears the browser's control face (review find, T321); the button hugs the chip and wears its radius, so a hover wash has the pill's shape (T343)");
  assert.match(CSS, /\n\.picker-tags \.picker-be-opt\.sel \{ background: transparent; border-color: transparent; color: inherit; filter: var\(--chip-sel-filter\); \}\n/,
    "the Backend row's accent fill never reaches a selected tag option");
  // the specificity that makes the override stick without !important: two classes beat one
  assert.ok(CSS.indexOf(".picker-be-opt.sel { background: var(--accent)") < CSS.indexOf(".picker-tags .picker-be-opt.sel {"), "the tag rule follows the generic one in the sheet");
});

// T343 (the user 2026-09-11): faded and selected read too close, and a hover looked like a selection. Three states,
// each explicit and pinned: FADED (unselected at rest) is the off chip on a transparent ground; HOVER lightens the pill's
// ground one step and changes no colour or brightness; SELECTED wears the brightest level, brightness(1.3), whatever the
// hover: a themed filter, brighter on the dark card and DARKER on cream (a lift on a light card pales the chip below its
// faded neighbour: the review's find), the dark value being the level that used to belong to a hovered chip.
test("three chip states: faded at rest, a hover that lightens the ground only, selected at the strongest level whatever the hover (T343)", () => {
  const rule = (sel: string) => { const m = CSS.match(new RegExp("\\n" + sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + " \\{ ([^}]*) \\}\\n")); assert.ok(m, "rule " + sel); return m![1]; };
  const base = rule(".picker-tags .picker-be-opt"), hover = rule(".picker-tags .picker-be-opt:hover"), sel = rule(".picker-tags .picker-be-opt.sel"), selHover = rule(".picker-tags .picker-be-opt.sel:hover");
  // faded: the off chip (TAG_CHIP_OFF_CLASS at 0.45, tag-menu.ts) on the button's transparent ground, no filter
  assert.match(base, /background: transparent;/); assert.match(base, /filter: none;/);
  // hover: the ground one step lighter, and NOTHING about the chip's colour or brightness
  assert.equal(hover, "background: var(--chip-wash); border-color: transparent; color: inherit;");
  assert.doesNotMatch(hover, /filter|brightness|opacity/, "a hover never brightens or recolours the chip");
  assert.doesNotMatch(CSS, /\.picker-tags \.picker-be-opt:hover \{ filter/, "the old hover brightness is gone");
  // selected: the strongest level, on its own ground
  assert.equal(sel, "background: transparent; border-color: transparent; color: inherit; filter: var(--chip-sel-filter);");
  assert.equal(selHover, "background: var(--chip-wash);", "a hovered selected chip keeps its level and takes the wash");
  // the level is themed: a lift on the dark card, a darkening on cream, each above the chip's rest contrast
  const levels = CSS.match(/^  --chip-sel-filter: (brightness\([\d.]+\));/gm) || [];
  assert.equal(levels.length, 2, "defined once per theme: " + levels.join(" | "));
  assert.match(levels[0], /brightness\(1\.3\)/, "dark: brighter");
  assert.match(levels[1], /brightness\(0\.75\)/, "light: darker");
  assert.ok(CSS.indexOf(levels[0]) < CSS.indexOf("body.theme-light {") && CSS.indexOf(levels[1]) > CSS.indexOf("body.theme-light {"), "the dark value on the root, the light one under body.theme-light");
  // the cascade by construction: hover, then sel at the same specificity (a selected chip's ground is its own), then sel:hover above both
  const at = (s: string) => CSS.indexOf("\n" + s + " {");
  assert.ok(at(".picker-tags .picker-be-opt") < at(".picker-tags .picker-be-opt:hover") && at(".picker-tags .picker-be-opt:hover") < at(".picker-tags .picker-be-opt.sel") && at(".picker-tags .picker-be-opt.sel") < at(".picker-tags .picker-be-opt.sel:hover"));
  // the wash is themed: one value on the dark root, another on the light body, the light one a darker step that stays distinct from the card
  const washes = CSS.match(/^  --chip-wash: (rgba\([^)]*\));/gm) || [];
  assert.equal(washes.length, 2, "defined once per theme: " + washes.join(" | "));
  assert.match(washes[0], /rgba\(255, 255, 255, 0\.10\)/, "dark: lighter");
  assert.match(washes[1], /rgba\(0, 0, 0, 0\.07\)/, "light: the visible step on a light card is darker");
  assert.ok(CSS.indexOf(washes[0]) < CSS.indexOf("body.theme-light {") && CSS.indexOf(washes[1]) > CSS.indexOf("body.theme-light {"), "the dark value on the root, the light one under body.theme-light");
});

test("the off visual is the one the tag toggles use: TAG_CHIP_OFF_CLASS at the inline opacity, defined on both sheets", () => {
  assert.match(MENU, /export const TAG_CHIP_OFF_CLASS = "tag-chip-off";/);
  assert.match(MENU, /export const TAG_CHIP_OFF_OPACITY = "0\.45";/);
  for (const [name, sheet] of [["styles.css", CSS], ["feed.css", FEED_CSS]] as const) assert.match(sheet, /\n\.tag-chip-off \{ opacity: 0\.45; \}\n/, name);
});

test("what the create reads is unchanged: the selected options' data-tag (every offered backend takes tags)", () => {
  assert.match(RENDER, /const tags = Array\.from\(tgWrap\.querySelectorAll<HTMLElement>\("\.picker-be-opt\.sel"\)\)\.map\(\(x\) => x\.dataset\.tag \|\| ""\)\.filter\(Boolean\)/);
  assert.match(REBUILD, /b\.type = "button"; b\.dataset\.tag = u\.name;/);
  const sync = RENDER.slice(RENDER.indexOf("function syncPickerTags("), RENDER.indexOf("function syncPickerAuth("));
  assert.match(sync, /\.forEach\(\(b\) => \{ b\.disabled = false; \}\);/, "every chip stays live: no pick disables the buttons (T331)");
});
