// T107 (the user 2026-08-26): the cross-pane menu-echo WRITER installs at MODULE LOAD in every
// pane document — never lazily inside openTagMenu. The repro: a tag menu opened from the sessions
// panel stood through clicks in the chat, because the chat imports tag-menu.ts but had never
// opened a menu of its own, so its document held no pointerdown broadcast. The listener half was
// already module-level; these tests pin the writer half to the same place, per pane document.
//
// NO static tag-menu import here: the executed test requires the module AFTER mocking the DOM
// globals, so the module-level guard runs against the mocks (esbuild wraps bundled modules in
// lazy factories — require() at call site is the load).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const MENU = ui("webview", "tag-menu.ts");
const RENDER = ui("webview", "render.ts");
const FLEET = ui("webview", "fleet.ts");
const FEED = ui("webview", "feed.ts");
const PALETTE = ui("webview", "palette-main.ts");
const TIMELINE = ui("romp-timeline-view.js");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("executed: loading tag-menu in a real-DOM context installs the writer — no open call needed", () => {
  const writes: Array<[string, string]> = [];
  const listeners: Record<string, Array<{ fn: (e: unknown) => void; capture: unknown }>> = {};
  const g = globalThis as any;
  g.document = {
    addEventListener: (k: string, fn: (e: unknown) => void, capture?: unknown) => {
      (listeners[k] ||= []).push({ fn, capture });
    },
  };
  g.window = { addEventListener: () => { /* storage listener — not under test */ } };
  g.localStorage = { setItem: (k: string, v: string) => writes.push([k, v]) };
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("./tag-menu");   // the LOAD is the install
    const pd = listeners["pointerdown"];
    assert.ok(pd && pd.length === 1, "the pointerdown writer is wired by the module guard at load");
    assert.equal(pd[0].capture, true, "capture phase — fires even when a target swallows the bubble");
    assert.equal(writes.length, 0, "no write before any pointerdown");
    pd[0].fn({});
    assert.equal(writes.length, 1);
    assert.equal(writes[0][0], "romp:menu-echo", "the shared key every listener watches");
    const body = JSON.parse(writes[0][1]);
    assert.equal(typeof body.t, "number", "the { t } shape — a fresh value so same-tick echoes still fire");
    // T213: a press INSIDE a marked menu broadcasts NOTHING — with the timeline's menus lifted
    // into the shell document, this very writer's echo detached the pressed row between its
    // pointerdown and its click, so every human-length press on the tag filter died silently
    pd[0].fn({ target: { closest: (sel: string) => (sel.includes("data-tag-menu") ? {} : null) } });
    assert.equal(writes.length, 1, "in-menu press: no echo — it would close the menu being used");
    pd[0].fn({ target: { closest: () => null } });
    assert.equal(writes.length, 2, "presses on pane content keep broadcasting");
    pd[0].fn({ target: {} });   // a target with no closest (foreign node) — broadcast, the safe default
    assert.equal(writes.length, 3, "a closest-less target still broadcasts (echo fails open)");
  } finally {
    delete g.document; delete g.window; delete g.localStorage;
  }
});

test("the writer lives in the module guard, and openTagMenu no longer installs anything", () => {
  const guard = MENU.slice(MENU.indexOf('if (typeof document !== "undefined")'), MENU.indexOf("export function openTagMenu"));
  assert.ok(guard.includes("installMenuEcho();"), "install sits beside the module-level closers");
  const open = MENU.slice(MENU.indexOf("export function openTagMenu"), MENU.indexOf("export function tagMenuButton"));
  assert.ok(!open.includes("installMenuEcho"), "the lazy call is gone — opening a menu is not the install");
});

test("every pane document the dashboard composes carries the writer at load", () => {
  // chat, outline/sessions, feed: the pane bundles import tag-menu — the module guard is the writer
  for (const [name, src] of [["render", RENDER], ["fleet", FLEET], ["feed", FEED]] as const)
    assert.match(src, /from "\.\/tag-menu"/, name + " bundles the module (writer rides the guard)");
  // the shell page mounts no tag menu but must still broadcast (palette/log backdrops, statusline)
  assert.match(PALETTE, /import \{ installMenuEcho \} from "\.\/tag-menu";/);
  assert.match(PALETTE, /^installMenuEcho\(\);$/m, "module-level — before boot()'s in-iframe early return");
  assert.match(KERNEL, /dist\/palette-main\.js/, "the shell page loads the bundle that broadcasts");
  // the timeline pane loads romp-timeline-view.js, not tag-menu — its constructor is its page boot
  const ctorWriter = /document\.addEventListener\('pointerdown', \(e\) => \{[\s\S]{0,600}?closest\('\[data-tag-menu\],\[data-romp-menu\]'\)\) return;\s*\n\s*try \{ localStorage\.setItem\('romp:menu-echo', JSON\.stringify\(\{ t: Date\.now\(\) \}\)\); \} catch \(e2\) \{ \/\* storage blocked \*\/ \}\s*\n\s*\}, true\);/;
  assert.match(TIMELINE, ctorWriter,
    "same key, same shape, capture phase, try/catch kept (Obsidian's localStorage may be foreign)");
});

test("every timeline menu marks itself for the writers' in-menu skip (T213)", () => {
  // an unmarked menu dies to its own echo the moment _menuHost lifts it into the shell document:
  // the count pin makes a NEW menu that forgets the mark fail here, not in the field
  // a menu is counted by its style string's `+ MENU_STYLE` whether the spec ENDS the string or a
  // declaration of the menu's own follows it: the tag colour popover (2026-09-09) appends its 8px
  // padding after the spec, because MENU_STYLE opens with the menu padding (4px) and in one style
  // string the later declaration wins (the dialog card's comment tells the same story)
  const creations = (TIMELINE.match(/\+ MENU_STYLE(?:\)|\s*\+\s*')/g) || []).length;
  const marks = (TIMELINE.match(/\.dataset\.rompMenu = '1'/g) || []).length;
  assert.ok(creations >= 6, "the timeline's menus render through MENU_STYLE (five menus and the colour popover)");
  assert.equal(marks, creations, "every MENU_STYLE menu carries data-romp-menu");
  // the popover by name: it lives in the host document like the menus (_menuHost), so the shell's
  // writer must see the mark or a press on a swatch echoes back and detaches it before its click
  assert.match(TIMELINE, /z-index:1003;' \+ MENU_STYLE \+ 'padding:8px;'\);\s*\n\s*pop\.dataset\.rompMenu = '1';/,
    "the tag colour popover marks itself the same way");
  assert.match(MENU, /menu\.dataset\.tagMenu = "1"/, "the shared menu marks itself too (its own key)");
  // both writers skip the marked subtrees with the SAME selector — one vocabulary, two mirrors
  for (const src2 of [MENU, TIMELINE])
    assert.match(src2, /closest\((?:"|')\[data-tag-menu\],\[data-romp-menu\](?:"|')\)/);
});
