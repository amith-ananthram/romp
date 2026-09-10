// A romp-injected NUDGE bubble is disclosed progressively (the user 2026-07-17: default compact, click
// to expand — the standing UI principle, see ui/CLAUDE.md): the bubble defaults to a one-line GIST
// with a caret, and clicking it swaps in the full markdown text. The open state is KEYED (nudge:<uuid>)
// so an expanded nudge survives the chat's re-renders, exactly like tool folds. A nudge whose whole text
// IS the gist gets no caret and no click affordance (never a dead-end fake expander). Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a romp bubble renders a one-line gist by default, full text behind a keyed click", () => {
  const fn = RENDER.slice(RENDER.indexOf('} else if (romp && ev.md) {'), RENDER.indexOf('} else if (ev.md) {'));
  // the gist SAYS WHAT ROMP DID (the user 2026-07-17 ×2: a follow-up's first line is the "> …"
  // goal-context quote, which read as the user's own words): semantic labels for the known flavors,
  // and the text fallback skips quoted lines before word-boundary truncation
  assert.match(fn, /const gist = ev\.followUp \? "follow-up" \+ \(ev\.goal \? " · " \+ ev\.goal : ""\)/);
  assert.match(fn, /: ev\.rompAuto \? "nudged for a status update" \+ \(ev\.goal \? " · " \+ ev\.goal : ""\)/);
  assert.match(fn, /: firstLine\.length > 90 \? firstLine\.slice\(0, 88\)\.replace\(\/\\s\+\\S\*\$\/, ""\) \+ "…" : firstLine;/);
  assert.match(fn, /lines\.find\(\(l\) => l && !l\.startsWith\(">"\)\)/, "the text gist never shows the quoted goal context");
  // the romp markers are stripped before the gist is cut (they'd read as literal comment text)
  assert.match(fn, /ev\.md\.replace\(\/<!--\[\\s\\S\]\*\?-->\/g, ""\)\.replace\(\/\^\\s\*\\\[romp\\\]\\s\*\/, ""\)\.trim\(\);/);   // markers AND the [romp] source prefix strip for display (T130)
  // collapsible ONLY when there is more than the gist; the caret marks it
  assert.match(fn, /const more = collapseWs\(raw\) !== collapseWs\(gist\);/);
  assert.match(fn, /if \(more\) \{ const c = el\("span", "nudge-caret"\); c\.textContent = "▸"; gistEl\.appendChild\(c\); \}/);
  // keyed open-state → an expanded nudge survives re-renders (the openFolds idiom)
  assert.match(fn, /const nkey = ev\.uuid \? "nudge:" \+ ev\.uuid : undefined;/);
  assert.match(fn, /applyFold\(bubble, "expanded", nkey\);/);
  // the toggle rides the stable document.body delegate (click-safe across re-renders, ui/CLAUDE.md) —
  // never a per-render bubble listener
  assert.match(fn, /bubble\.dataset\.act = "nudgetoggle";/);
  assert.match(RENDER, /nudgetoggle: \(el\) => \{/);
  assert.match(RENDER, /rememberFold\(el, "expanded", el\.dataset\.nkey \|\| undefined\);/);
});

test("the CSS swap: gist shown collapsed, full text shown expanded — never both", () => {
  assert.match(CSS, /\.romp-bubble \.nudge-full \{ display: none; \}/);
  assert.match(CSS, /\.romp-bubble\.expanded \.nudge-full \{ display: block; \}/);
  assert.match(CSS, /\.romp-bubble\.expanded \.nudge-gist \{ display: none; \}/);
  assert.match(CSS, /\.romp-bubble\.nudge-collapsible \{ cursor: pointer; \}/);
});

test("the progressive-disclosure principle is recorded in ui/CLAUDE.md", () => {
  // The UI design rules live in ui/CLAUDE.md, the file that loads for work under ui/; the root
  // CLAUDE.md points at it. This pins that the principle is written down where UI work reads it.
  const doc = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "CLAUDE.md"), "utf8");
  assert.match(doc, /### Progressive disclosure is the UI's organizing principle/);
});

test("every UI rule heading is written down once, in ui/CLAUDE.md, and the root CLAUDE.md points there", () => {
  // One home for the UI rules: the root file points at ui/CLAUDE.md and carries no copy of a rule,
  // and ui/CLAUDE.md carries each rule under its own heading, exactly once, with a blank line before
  // the heading so the rule above ends there (the accent paragraph once ran on from the Menus rule
  // with neither a heading nor a blank line).
  const root = fs.readFileSync(path.resolve(process.cwd(), "..", "CLAUDE.md"), "utf8");
  const rules = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "CLAUDE.md"), "utf8");
  const lines = rules.split("\n");
  const headings = (md: string, h: string) => md.split("\n").filter((l) => l.startsWith(h)).length;
  assert.equal(headings(root, "### UI design rules live in `ui/CLAUDE.md`"), 1, "the root file points at the UI rules");
  for (const h of [
    "### Progressive disclosure is the UI's organizing principle",
    "### Panels open as centered modals over a dimmed, UNCHANGED dashboard",
    "### Font sizes: few, and consistent by information type",
    "### Menus and dropdowns wear ONE vocabulary",
    "### The accent color is light blue `#9cd2ff`",
    "### Loading/waiting states: show the romp loader FIRST",
    "### Buttons must stay click-safe across re-renders, and always acknowledge",
    "### Designs must accommodate many tags and many sessions",
  ]) {
    assert.equal(headings(rules, h), 1, "ui/CLAUDE.md carries the rule once: " + h);
    assert.equal(headings(root, h), 0, "the root CLAUDE.md carries no second copy: " + h);
    assert.equal(lines[lines.findIndex((l) => l.startsWith(h)) - 1], "", "a blank line precedes the heading: " + h);
  }
});
