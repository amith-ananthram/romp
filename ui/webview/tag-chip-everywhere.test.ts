import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

// ONE tag chip everywhere (T321, the user 2026-09-10: the strip's filter chips rendered unlike the tag rows above them,
// and a tag must never be bold, the session names' weight). tagChip in tag-menu.ts is the one renderer; every surface
// that shows a tag builds through it, the sheets add layout and state cues but never a weight, and the standard is
// stated once beside the renderer and once in ui/CLAUDE.md.
const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const FEED = ui("webview", "feed.ts");
const OUTLINE = ui("webview", "fleet.ts");   // the outline pane's module
const MENU = ui("webview", "tag-menu.ts");
const CSS = ui("webview", "styles.css");
const FEED_CSS = ui("webview", "feed.css");
const RULES = ui("CLAUDE.md");

test("the renderer pins the standard inline: thin border and text in the tag's colour, weight 400, normal tracking, faded when off", () => {
  assert.match(MENU, /\/\*\* THE ONE TAG CHIP \(T321/, "the standard is written once, beside the renderer");
  assert.match(MENU, /\+ "border:1px solid " \+ col \+ ";color:" \+ col \+ ";background:transparent;white-space:nowrap;"\s*\n\s*\+ "font-weight:400;letter-spacing:normal;"/,
    "weight and tracking ride inline, so a bold or letter-spaced host (the strip's header) cannot restyle the chip");
  // the real builder against a stub document: what it writes is what every surface gets
  const created: { tag: string; attrs: Record<string, string>; kids: unknown[] }[] = [];
  const g = globalThis as any;
  const saved = g.document, savedWin = g.window;
  g.document = {
    createElement: (tag: string) => { const n = { tag, attrs: {} as Record<string, string>, kids: [] as unknown[],
      setAttribute(k: string, v: string) { this.attrs[k] = v; }, appendChild(c: unknown) { this.kids.push(c); },
      addEventListener() { /* unused by tagChip */ } }; created.push(n); return n; },
    createTextNode: (t: string) => ({ text: t }),
    addEventListener() { /* the module wires its closers at load */ },
  };
  g.window = { addEventListener() { /* the storage listener at load */ } };
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { tagChip } = require("./tag-menu");
    const on = tagChip("infra", "#54B204");
    assert.match(on.attrs.style, /border:1px solid #54B204;color:#54B204;background:transparent;/);
    assert.match(on.attrs.style, /font-weight:400;letter-spacing:normal;/);
    assert.match(on.attrs.style, /font-size:0\.82em;/, "its own size where no host sizes it…");
    assert.equal(on.attrs["class"], undefined);
    const hosted = tagChip("infra", "#54B204", { inheritSize: true });
    assert.doesNotMatch(hosted.attrs.style, /font-size/, "…the host's where a row sizes it");
    const off = tagChip("qa", "#3355aa", { off: true });
    assert.equal(off.attrs["class"], "tag-chip-off");
    assert.match(off.attrs.style, /opacity:0\.45;/);
    assert.match(off.attrs.style, /border:1px solid #3355aa;color:#3355aa;/, "faded, never recoloured");
  } finally { g.document = saved; g.window = savedWin; }
});

test("every tag surface builds through tagChip", () => {
  const head = RENDER.slice(RENDER.indexOf("function makeGroupHead("), RENDER.indexOf("\n}", RENDER.indexOf("function makeGroupHead(")));
  assert.match(head, /const chip = tagChip\(name, sec\.color, \{ inheritSize: true \}\);/, "the strip's group row");
  const filt = MENU.slice(MENU.indexOf("export function syncTagFilter("));
  assert.match(filt, /const chip = tagChip\(c\.label, c\.color\);/, "the filter chips: the strip's, the feed's and the outline's, one builder");
  assert.match(RENDER, /syncTagFilter\(tagBtn, tagChipsHost, surfaceLens\(v, "chat"\)/, "the strip's filter mount goes through it");
  assert.match(FEED, /syncTagFilter\(b, ch, feedLens, lensUnions\(feedTagViews\) as never/, "the feed's");
  assert.match(OUTLINE, /syncTagFilter\(tagBtn, chipsHost, surfaceLens\(fleetViews, "outline"\)/, "the outline's");
  const lensMenu = MENU.slice(MENU.indexOf("export function openTagMenu("), MENU.indexOf("export const TAG_CHIP_OFF_CLASS"));
  assert.match(lensMenu, /const chip = tagChip\(u\.name, u\.color \|\| null, \{ off: !on \}\);/, "the tag-lens menu's rows");
  const fly = RENDER.slice(RENDER.indexOf('const sub = el("div", "ctx-menu ctx-sub ctx-sub-tags");'), RENDER.indexOf("// New tag… — an inline input"));
  assert.match(fly, /const chip = tagChip\(g\.name, g\.color \|\| null, \{ inheritSize: true \}\);\s+\/\/ the one tag chip \(T321\)/, "the tab menu's Tags flyout: a row that names a tag, at the label's size");
  assert.match(fly, /chip\.classList\.add\("ctx-tag-chip"\);/);
  assert.match(fly, /const named = \(\) => \{ const c = tagChip\(g\.name, g\.color \|\| null, \{ inheritSize: true \}\); c\.classList\.add\("ctx-tag-chip"\); return c; \};/, "the action rows name their tag as the chip inside the sentence");
  assert.match(fly, /lb\.append\("Move to ", named\(\)\);/); assert.match(fly, /lb\.append\("\+ ", named\(\)\);/);
  assert.doesNotMatch(RENDER + CSS, /ctx-tag-dot/, "no swatch-and-name pair anywhere a tag shows");
  const spend = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  assert.match(spend, /function spTagChip\(s\)\{var c=spColor\(s\);return '<span class=rsp-tag-chip style="display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:9px;border:1px solid '\+c\+';color:'\+c\+';background:transparent;white-space:nowrap;font-weight:400;letter-spacing:normal;">'/,
    "the landing page's spend panel: a merge-by-tag row names its tag as the chip's inlined twin, at the row's size");
  assert.match(spend, /\(s\.kind==='tag'\?\(spTagChip\(s\)\+/, "…in place of the session title's bold");
  const dialog = FEED.slice(FEED.indexOf('const chips = el("span", "fsm-chips");'), FEED.indexOf("if (chips.childElementCount) r.appendChild(chips);"));
  assert.match(dialog, /const c = tagChip\(g\.name, g\.color \|\| null\);/, "the feed's session dialog");
  assert.match(dialog, /c\.classList\.add\("fsm-chip-tag"\);/);
  assert.doesNotMatch(FEED, /c\.style\.borderColor = g\.color/, "no hand-painted tag border remains");
  assert.match(RENDER, /function paintPickerTagChip\(b: HTMLButtonElement, u: \{ name: string; color\?: string \| null \}\): void \{\s*\n\s*b\.replaceChildren\(tagChip\(u\.name, u\.color, \{ inheritSize: true, off: !b\.classList\.contains\("sel"\) \}\)\);/,
    "the new-session picker's Tags row");
  assert.doesNotMatch(RENDER + CSS, /picker-tag-dot/, "the picker's dot is gone");
});

test("no sheet sets a weight on a tag chip class: the sheets add layout and state cues only", () => {
  // every rule of the sheet (selector { body }, across line breaks; comments stripped) whose selector names a chip class
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  const chipSel = /(tab-group-chip|tag-chip|fsm-chip-tag|ctx-tag-chip|picker-tags \.picker-be-opt)/;
  for (const [name, sheet] of [["styles.css", CSS], ["feed.css", FEED_CSS]] as const) {
    const bare = sheet.replace(/\/\*[\s\S]*?\*\//g, "");
    let m: RegExpExecArray | null; let seen = 0;
    while ((m = rule.exec(bare))) {   // loop-ok: a bounded scan of one file's rules
      const sel = m[1].trim(), body = m[2];
      if (!chipSel.test(sel)) continue;
      seen++;
      assert.doesNotMatch(body, /font-weight\s*:\s*(bold|bolder|[5-9]00)/, `${name}: "${sel}" must not bold a tag chip`);
      assert.doesNotMatch(body, /(^|[^-\w])font\s*:\s*[^;]*\b(bold|bolder|[5-9]00)\b/, `${name}: "${sel}" must not bold a tag chip through the font shorthand`);
      if (!/picker-tags \.picker-be-opt/.test(sel))   // the picker's HOST button strips its own chrome; the chip inside is the renderer's
        assert.doesNotMatch(body, /(^|[^-\w])(border(-color|-width|-style)?|color|font-size)\s*:/, `${name}: "${sel}" must not restyle the chip's border, colour or size (the renderer's); a state cue such as an underline's colour is fine`);
    }
    assert.ok(seen > 0, name + " has chip rules to check");
  }
  assert.match(CSS, /\n\.tab-group-chip \{ flex: 0 0 auto; line-height: 1\.2; \}\n/, "the strip's row chip lost its bold");
  assert.match(FEED_CSS, /\n\.fsm-chip-tag \{ margin-left: 4px; \}/, "the dialog's chip keeps only its spacing");
  for (const [name, sheet] of [["styles.css", CSS], ["feed.css", FEED_CSS]] as const) assert.match(sheet, /\n\.tag-chip-off \{ opacity: 0\.45; \}\n/, name + ": the off state");
});

test("there is one off look, the fade: the struck variant is gone from the renderer, both sheets, the picker and the rules (T321c)", () => {
  // the user tried a diagonal drawn through the picker's off tag and reversed it the same day: off is the faded chip everywhere,
  // the picker's call the same shape as the tag-lens menu's; nothing of the variant stays behind as dead code
  assert.doesNotMatch(MENU, /TAG_CHIP_STRUCK|struck\??:/, "no struck option or constant in the renderer");
  assert.doesNotMatch(CSS + FEED_CSS, /tag-chip-struck/, "no struck rule on either sheet");
  assert.doesNotMatch(RENDER, /struck: !b\.classList/, "the picker passes no struck flag");
  assert.doesNotMatch(RULES, /struck through with a diagonal/, "the rules name one off look");
  assert.match(RENDER, /b\.replaceChildren\(tagChip\(u\.name, u\.color, \{ inheritSize: true, off: !b\.classList\.contains\("sel"\) \}\)\);/,
    "the picker's off chip is tagChip's `off`, the tag-lens menu's own call shape");
  const lensMenu = MENU.slice(MENU.indexOf("export function openTagMenu("), MENU.indexOf("export const TAG_CHIP_OFF_CLASS"));
  assert.match(lensMenu, /\{ off: !on \}/);
});

test("the standard is written in ui/CLAUDE.md, one sentence", () => {
  assert.match(RULES, /\*\*Tags render as ONE chip everywhere\*\* \(the user 2026-09-10\): `tagChip` in\s*\n`ui\/webview\/tag-menu\.ts` builds every tag the UI shows/);
  assert.match(RULES, /weight 400, the\s*\ncontext's size, faded when off; never bold, which is the session names' weight/);
});
