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

test("the option wears no button chrome around the chip, selected or hovered, so the chip's border is the whole look", () => {
  assert.match(CSS, /\n\.picker-tags \.picker-be-opt \{ padding: 0; border: none; background: transparent; font-family: inherit; \}\n/,
    "no weight on the host (the chip carries 400) and the page's typeface: a bare button wears the browser's control face (review find, T321)");
  assert.match(CSS, /\n\.picker-tags \.picker-be-opt:hover \{ filter: brightness\(1\.3\); \}\n/, "the hover cue is the strip's own: the chip brightened, a property the chip never sets inline");
  assert.match(CSS, /\n\.picker-tags \.picker-be-opt:hover, \.picker-tags \.picker-be-opt\.sel \{ background: transparent; border-color: transparent; color: inherit; \}\n/,
    "the Backend row's accent fill and hover wash never reach a tag option");
  // the specificity that makes the override stick without !important: two classes beat one
  assert.ok(CSS.indexOf(".picker-be-opt.sel { background: var(--accent)") < CSS.indexOf(".picker-tags .picker-be-opt.sel {"), "the tag rule follows the generic one in the sheet");
});

test("the tmux pick greys the row without stacking a second fade on the off chips, so on and off still read", () => {
  assert.match(CSS, /\n\.picker-tags\.disabled \.picker-be-opt \{ filter: grayscale\(1\); cursor: default; pointer-events: none; \}\n/,
    "grey says disabled; the off chip's 0.45 stays the only fade");
  assert.doesNotMatch(CSS, /\.picker-tags\.disabled \.picker-be-opt \{ opacity: 0\.45;/, "the old 0.45 over 0.45 left an off chip at a fifth");
});

test("the off visual is the one the tag toggles use: TAG_CHIP_OFF_CLASS at the inline opacity, defined on both sheets", () => {
  assert.match(MENU, /export const TAG_CHIP_OFF_CLASS = "tag-chip-off";/);
  assert.match(MENU, /export const TAG_CHIP_OFF_OPACITY = "0\.45";/);
  for (const [name, sheet] of [["styles.css", CSS], ["feed.css", FEED_CSS]] as const) assert.match(sheet, /\n\.tag-chip-off \{ opacity: 0\.45; \}\n/, name);
});

test("what the create reads is unchanged: the selected options' data-tag, only when the backend takes tags", () => {
  assert.match(RENDER, /const tags = backendTakesTags\(backend\)\s*\n\s*\? Array\.from\(tgWrap\.querySelectorAll<HTMLElement>\("\.picker-be-opt\.sel"\)\)\.map\(\(x\) => x\.dataset\.tag \|\| ""\)\.filter\(Boolean\)/);
  assert.match(REBUILD, /b\.type = "button"; b\.dataset\.tag = u\.name;/);
  const sync = RENDER.slice(RENDER.indexOf("function syncPickerTags("), RENDER.indexOf("function syncPickerAuth("));
  assert.match(sync, /\.forEach\(\(b\) => \{ b\.disabled = !takes; \}\);/, "the tmux pick still disables the buttons themselves");
});
