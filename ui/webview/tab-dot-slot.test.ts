// A tab's width must not change with its state (T262g, the user 2026-09-08: the chat view slid up and down by one
// tab row at times). renderTabs appended the working dot (.tab-dot, 7 px plus the 4 px gap) only while a session was
// working or awaiting, so a tab grew and shrank with its state; with the strip filled to a wrap boundary, a dot
// appearing or vanishing added or removed a ROW, and every row change moves #content's box under the reader: a
// bottom reader's text slides by the row height (Chrome keeps the scroller at its maximum; on a wrap the pane's
// box-resize compensation writes the same). Measured in a lab: wrap -31 px / un-wrap +31 px per flip, no browser
// fault. Fix: every tab carries the dot's slot in every state — laid out, hidden when the state has no dot — so the
// strip's row count changes only when tabs are added, removed or renamed. The compacting bar keeps its own wider
// slot (a rare, deliberate state). Pure rule executed here; render.ts and the CSS pinned.
// The dot also explains itself on hover (tabDotTitle, the rule beside tabDotClass in tab-state.ts), in the feed's
// words: executed here against feed.ts's own phrases, and the render wiring pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { tabDotClass, tabDotTitle } from "./tab-state";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");

test("every state maps to a dot class; the ones without a visible dot get the hidden slot; compacting gets no dot (its bar)", () => {
  assert.equal(tabDotClass("working"), "tab-dot");
  assert.equal(tabDotClass("awaitingBg"), "tab-dot await");
  assert.equal(tabDotClass(undefined), "tab-dot unknown");
  assert.equal(tabDotClass(""), "tab-dot unknown");
  assert.equal(tabDotClass("opening"), "tab-dot opening");
  assert.equal(tabDotClass("compacting"), null);
  for (const st of ["waiting", "idle", "blocked", "closed", "dead", "needsYou"]) assert.equal(tabDotClass(st), "tab-dot none", st);
});

test("render.ts appends the slot from the one rule; the compacting bar branch stays", () => {
  assert.match(RENDER, /import \{[^}]*\btabDotClass\b[^}]*\} from "\.\/tab-state";/);
  assert.match(RENDER, /const dotCls = tabDotClass\(st\);\s*\n\s*if \(dotCls\) tab\.appendChild\(el\("span", dotCls\)\);/);
  assert.doesNotMatch(RENDER, /if \(st === "working"\) tab\.appendChild\(el\("span", "tab-dot"\)\);/, "the per-state appends are gone");
  assert.match(RENDER, /const ci = el\("span", "tab-compacting-bar"\);/);
});

// The dot explains itself on hover (the user 2026-07-22: learn the states from tooltips, not the CLI), in the feed's
// own words: the feed's status pip has spoken these phrases since then (feed.ts DOT_TIP), and the strip's dot is the
// same mark on another surface, so the phrases are read from feed.ts here and must match it byte for byte. The
// hidden slot says nothing (there is no state to explain), and compacting has no dot: its bar carries its own title
// (render.ts). The two rules sit side by side in tab-state.ts and agree on which states have a dot.
test("every visible dot explains itself on hover in the feed's words; the hidden slot and the compacting bar say nothing", () => {
  const at = FEED.indexOf("const DOT_TIP");
  assert.ok(at > 0, "feed.ts DOT_TIP not found");
  const block = FEED.slice(at, FEED.indexOf("};", at));
  const tip: Record<string, string> = Object.fromEntries([...block.matchAll(/^\s*(work|await|unknown): "([^"]+)",/gm)].map((m) => [m[1], m[2]]));
  assert.deepEqual(Object.keys(tip).sort(), ["await", "unknown", "work"], "feed.ts DOT_TIP names its three states");
  assert.equal(tabDotTitle("working"), tip.work);
  assert.equal(tabDotTitle("awaitingBg"), tip.await);
  assert.equal(tabDotTitle(undefined), tip.unknown);
  assert.equal(tabDotTitle(""), tip.unknown);
  // opening has no feed twin (the feed draws no opening pip), so its phrase is the strip's own, in the feed's shape:
  // the state word, the feed's separator (read from its working phrase), then what the state means
  const sep = /^working(\s\S\s)/.exec(tip.work)![1];
  assert.equal(tabDotTitle("opening"), "opening" + sep + "this session is still starting up", "opening: the state word, the feed's separator, an explanation");
  assert.equal(tabDotTitle("compacting"), null, "compacting: no dot, and the bar carries its own title");
  for (const st of ["waiting", "idle", "blocked", "closed", "dead", "needsYou"]) assert.equal(tabDotTitle(st), null, st + ": the hidden slot says nothing");
  // the two rules agree: a state has a title exactly when the class rule gives it a visible dot
  for (const st of ["working", "awaitingBg", undefined, "", "opening", "compacting", "waiting", "idle", "blocked", "closed", "dead", "needsYou"]) {
    const cls = tabDotClass(st);
    assert.equal(tabDotTitle(st) !== null, cls !== null && cls !== "tab-dot none", String(st) + ": titled iff visibly dotted");
  }
});

test("render.ts titles the slot it just appended, from the title rule beside the class rule", () => {
  assert.match(RENDER, /import \{[^}]*\btabDotTitle\b[^}]*\} from "\.\/tab-state";/);
  // the title is written while the slot is still the tab's last child: right after the append, before the
  // compacting bar (and the label, gauge and close button the callers add after)
  assert.match(RENDER, /const dotCls = tabDotClass\(st\);\s*\n\s*if \(dotCls\) tab\.appendChild\(el\("span", dotCls\)\);\s*\n\s*const dotTip = dotCls \? tabDotTitle\(st\) : null;\s*\n\s*if \(dotTip\) \(tab\.lastElementChild as HTMLElement\)\.title = dotTip;/,
    "the title lands on the slot while it is the tab's last child");
});

test("the hidden slot is laid out (visibility, never display:none), same box as the dot", () => {
  assert.match(CSS, /\.tab-dot\.none \{ visibility: hidden; \}/);
});
