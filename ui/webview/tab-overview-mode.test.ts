// T322 (the user 2026-09-10): the tab strip grouped by tag, one tag per row, and the section-at-a-glance view.
// Six points, pinned at the source (the executed harnesses live in tab-groups.test.ts and tab-snapshot-pane.test.ts):
//   1. a session picked inside an expanded tag row no longer underlines the row's tag chip: the tab's own
//      highlight says which is active;
//   2. the overview's title is the words "Overview of", the tag as its ordinary chip (tagChip, tag-menu.ts) and the
//      session count, not the tag's name beside a little colour bar;
//   3 and 6. while the overview shows, the message box disappears entirely: it is a mode, not a session;
//   4. a tag row is not a tab: a tab's box of space around the chip, the caret and the count, no hover dress, no
//      wash, no borders; and while its overview shows, the row wears the selected tab's box;
//   5. no tab renders as selected while the overview shows; selecting any tab clears the overview.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = read("render.ts");
const CSS = read("styles.css");
const SNAP = read("tab-snapshot.ts");

function fn(name: string): string {
  const at = RENDER.indexOf(name);
  assert.ok(at > 0, name + " exists");
  return RENDER.slice(at, RENDER.indexOf("\n}\n", at));
}

test("1. the holding row's chip wears no underline; the class stays for the stand-in's focus and aria-current", () => {
  assert.doesNotMatch(CSS, /\.holds-active[^\n{]*\{[^}]*text-decoration/, "no underline rule keyed on holds-active");
  const head = fn("function makeGroupHead(");
  assert.match(head, /\+ \(holdsActive \? " holds-active" : ""\)\);/, "the class is still set…");
  assert.match(head, /if \(holdsActive\) head\.setAttribute\("aria-current", "true"\);/, "…for assistive tech and the folded stand-in");
});

test("2. the overview's title: 'Overview of', the tag's ordinary chip from tagChip, the count; no colour bar", () => {
  const rs = fn("function renderSnapshot(): boolean {");
  assert.match(rs, /const of = el\("span", "snap-of"\); of\.textContent = "Overview of";/);
  assert.match(rs, /h\.append\(of, el\("span", "snap-chip-slot"\), el\("span", "snap-count"\)\);/, "words, chip slot, count — in that order");
  assert.match(rs, /const chip = tagChip\(next\.name, next\.color, \{ inheritSize: true \}\); chip\.classList\.add\("snap-chip"\);/, "the shared tag chip builder, sized by the heading");
  assert.match(rs, /part\("snap-chip-slot"\)\.replaceChildren\(chip\);/, "patched in place on every paint, like the count");
  assert.doesNotMatch(rs, /snap-swatch|snap-name/, "the colour bar and the bare name are gone");
  assert.doesNotMatch(CSS, /\n\.snap-swatch \{/, "no bar rule left");
  assert.doesNotMatch(CSS, /\n\.snap-chip \{/, "no chip rule of the view's own: the chip's dress is tagChip's alone");
  assert.match(SNAP, /label: `Overview of \$\{name\}: \$\{count\}; click one to open it`/, "the spoken label says the same words");
});

test("3 and 6. the overview is a mode: the message box and the bottom bar are gone while it shows", () => {
  assert.match(CSS, /body\.snap-mode #footer \{ display: none; \}/, "the whole footer: the message box and the session's bottom bar");
  const show = fn("function showActive(");
  assert.match(show, /if \(snapView && renderSnapshot\(\)\) \{\s*\n\s*document\.body\.classList\.add\("snap-mode"\);/, "set as the overview paints");
  assert.match(show, /return;\s*\n\s*\}\s*\n\s*document\.body\.classList\.remove\("snap-mode"\);/, "cleared on the transcript path of the same function");
  assert.match(fn("function hideSnapshot(): void {"), /document\.body\.classList\.remove\("snap-mode"\);/, "and on every exit that hides the view");
  // the keyboard agrees: Enter on a folded stand-in drops into the message box only while no overview shows
  assert.match(RENDER, /if \(e\.key === "Enter" && standIn && !snapView && focusComposerOrAsk\(\)\)/);
});

test("4. a tag row is not a tab: a tab's box of space, no dress; its overview shown, the selected tab's box", () => {
  const row = CSS.match(/\n\.tab-group-head \{([^}]*)\}/)![1];
  const tab = CSS.match(/\n\.tab \{([^}]*)\}/)![1];
  for (const prop of ["padding: 6px 7px;", "border-radius: 6px 6px 0 0;"]) {
    assert.ok(row.includes(prop), "the row has a tab's " + prop); assert.ok(tab.includes(prop), "…as the tab does: " + prop);
  }
  assert.doesNotMatch(row, /background|border:/, "empty around the chip, the caret and the count: no fill, no border of its own (a tab sets the strip's height)");
  assert.doesNotMatch(CSS, /\n\.tab-group-head:hover/, "no hover lift, wash or border");
  assert.match(CSS, /\.tab-group-head:focus-visible \{ outline: 1px solid var\(--accent\); outline-offset: -1px; \}/, "the keyboard's focus ring is not a dress");
  // the shown row wears exactly the selected tab's box: the same fill token and the same inset identity ring
  assert.match(CSS, /\.tab-group-head\.snap-shown \{ color: var\(--fg\); background: var\(--tab-active-bg\); box-shadow: inset 0 0 0 1\.5px var\(--chip-bg, transparent\); \}/);
  assert.match(CSS, /\.tab\.active \{ color: var\(--fg\); background: var\(--tab-active-bg\); \}/);
  assert.match(CSS, /\.tab\.active\.colored \{ box-shadow: inset 0 0 0 1\.5px var\(--chip-bg\); \}/);
  assert.match(fn("function makeGroupHead("), /if \(sec\.color\) head\.style\.setProperty\("--chip-bg", sec\.color\);/, "the tag's colour as the row's --chip-bg, the tab's own variable");
  // the token in both themes (theme-parity.test.ts checks every :root token has a light twin)
  const dark = CSS.split("body.theme-light {")[0], light = CSS.split("body.theme-light {")[1].split("\n}")[0];
  assert.match(dark, /--tab-active-bg: rgba\(255, 255, 255, 0\.14\);/); assert.match(light, /--tab-active-bg: /);
  assert.doesNotMatch(CSS, /\.tab-group-head\.snap-shown \{ background: var\(--accent-wash\); \}/, "the accent wash is gone");
});

test("5. no tab renders as selected while the overview shows; selecting any tab clears it", () => {
  assert.match(CSS, /body\.snap-mode #tabs \.tab\.active \{ color: var\(--dim\); background: transparent; box-shadow: none; \}/, "the active tab's dress neutralised to a resting tab's");
  assert.match(CSS, /body\.snap-mode:not\(\.chat-theme-yatharth\) #tabs \.tab\.active:not\(\.tab-blocked\):not\(\.tab-add\) \{ border-color: rgba\(255, 255, 255, 0\.06\); \}/, "…rest outline included, the tab rule's own gray");
  // the class itself stays on the tab: activeId is the way back, focus and the arrows key on it
  assert.match(RENDER, /const tab = el\("div", "tab" \+ \(id === activeId \? " active" : ""\)\);/);
  // a pick clears the view (setActive), as before
  const sa = fn("function setActive(");
  assert.match(sa, /const leavingSnap = snapView !== null;\s*\n\s*snapView = null;/);
});
