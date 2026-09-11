// The chat tab's right-click menu, regrouped into four sections by what each item changes about the
// session (the user 2026-09-11): dividers only in the DOM, the section titles living in showTabMenu's
// comments and here. This file is the ONE pin of the grouping: it slices showTabMenu by the menu's
// divider appends and checks each section's members in order. Source pins (no jsdom for render.ts).
//   1. How it shows: Rename; the colour swatches (the label and tint, nothing about the session)
//   2. Where it belongs: Tags (flyout); Move to folder… (membership and location, which the kernel and the file
//      system know about; the column item left with the drag, 2026-09-11: a tab is placed by dragging it)
//   3. What reaches you: the feed, mail and bell switches; Billing (how the session takes part in the
//      dashboard's surfaces, and who pays)
//   4. Files: Browse files (a different kind of thing: it opens another surface; web only, alone, last)
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const SEP = 'menu.appendChild(el("div", "ctx-sep"));';   // the MENU's divider; the Tags flyout appends its own to `sub`

// the one marker per member the source carries exactly once inside showTabMenu
const MARKERS: Record<string, string> = {
  Rename: 'l.textContent = "Rename"',
  colours: 'el("div", "ctx-colors")',
  Tags: 'l.textContent = "Tags"',
  Move: 'l.textContent = "Move to folder…"',
  feed: 'toggle("feed"',
  mail: 'toggle("mail"',
  bell: 'toggle("bell"',
  Billing: 'l.textContent = "Billing"',
  Browse: 'l.textContent = "Browse files"',
};

function sections(): string[] {
  const at = RENDER.indexOf("function showTabMenu(");
  assert.ok(at > 0, "showTabMenu exists");
  const body = RENDER.slice(at, RENDER.indexOf("document.body.appendChild(menu);", at));
  for (const [name, m] of Object.entries(MARKERS)) assert.equal(body.split(m).length, 2, `${name} is built once in the menu`);
  return body.split(SEP);
}

// the members found in one section, in source order
function membersOf(part: string): string[] {
  return Object.entries(MARKERS)
    .map(([name, m]) => ({ name, at: part.indexOf(m) }))
    .filter((x) => x.at >= 0)
    .sort((a, b) => a.at - b.at)
    .map((x) => x.name);
}

test("exactly three dividers cut the menu into four sections", () => {
  assert.equal(sections().length, 4, "three `ctx-sep` appends to the menu, no more, no fewer");
});

test("section 1, how it shows: Rename, then the colour swatches", () => {
  assert.deepEqual(membersOf(sections()[0]), ["Rename", "colours"]);
});

test("section 2, where it belongs: Tags, Move to folder… (the column item left with the drag, 2026-09-11)", () => {
  assert.deepEqual(membersOf(sections()[1]), ["Tags", "Move"]);
});

test("section 3, what reaches you: the feed, mail and bell switches, then Billing", () => {
  assert.deepEqual(membersOf(sections()[2]), ["feed", "mail", "bell", "Billing"]);
});

test("section 4, files: Browse files alone, last, behind its own divider (web only)", () => {
  const last = sections()[3];
  assert.deepEqual(membersOf(last), ["Browse"]);
  // the divider rides inside the web-only gate with the item, so a VS Code menu ends on Billing without a trailing rule
  const gate = RENDER.lastIndexOf('if (location.protocol === "http:" || location.protocol === "https:") {', RENDER.indexOf(MARKERS.Browse));
  const sepAt = RENDER.indexOf(SEP, gate);
  assert.ok(gate > 0 && sepAt > gate && sepAt < RENDER.indexOf(MARKERS.Browse), "the divider is appended inside the gate, before the item");
});
