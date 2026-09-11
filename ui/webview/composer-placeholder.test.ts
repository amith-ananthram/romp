// The composer's resting placeholder names the session (the user 2026-09-09): "Message <name>…", the name
// bold in the session's identity colour, per pane — a split column names ITS active session. The split of the
// placeholder is a pure rule (composer-placeholder.ts), executed here; the overlay's wiring in render.ts and its
// styling are pinned at source (no jsdom for the renderer — the repo convention). Synthetic names only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { phParts, RESTING_PREFIX } from "./composer-placeholder";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const FULL = "Message this session…  (⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · / for commands)";
const SHORT = "Message this session…  (/ for commands)";
const PHONE = "Message this session…";

test("every resting form takes the session's name in place of 'this session', keeping the ellipsis and the hint", () => {
  assert.deepEqual(phParts(FULL, "web"), { kind: "named", before: "Message ", host: null, name: "web", after: "…  (⏎ send · ⇧⏎ newline · ⌘⏎ stage · ↑ history · / for commands)" });
  assert.deepEqual(phParts(SHORT, "api"), { kind: "named", before: "Message ", host: null, name: "api", after: "…  (/ for commands)" });
  assert.deepEqual(phParts(PHONE, "tests"), { kind: "named", before: "Message ", host: null, name: "tests", after: "…" });
  assert.equal(RESTING_PREFIX, "Message this session", "the prefix the resting forms share (composerRestingPlaceholder)");
});

test("the name is trimmed; a remote session's host is split off as the tab label splits it (T328): the host quiet, only the name bold", () => {
  assert.equal((phParts(FULL, "  web ") as any).name, "web");
  const LOCAL = "aaaaaaaa-1111-2222-3333-444444444444", REMOTE = "TESTHOST:" + LOCAL;
  // the split is the sid's own prefix (host-prefix.ts hostPrefix): federation prefixes both the name and the sid
  assert.deepEqual(phParts(PHONE, "TESTHOST:api", REMOTE), { kind: "named", before: "Message ", host: "TESTHOST:", name: "api", after: "…" });
  assert.deepEqual(phParts(FULL, "TESTHOST:api", REMOTE).kind === "named" && (phParts(FULL, "TESTHOST:api", REMOTE) as any).host, "TESTHOST:");
  // a LOCAL session with a colon in its name is not a remote one: the sid carries no prefix, so the name stays whole
  assert.deepEqual(phParts(PHONE, "TESTHOST:api", LOCAL), { kind: "named", before: "Message ", host: null, name: "TESTHOST:api", after: "…" });
  assert.deepEqual(phParts(PHONE, "api", LOCAL), { kind: "named", before: "Message ", host: null, name: "api", after: "…" }, "a local session: unchanged");
  assert.deepEqual(phParts(PHONE, "api", REMOTE), { kind: "named", before: "Message ", host: null, name: "api", after: "…" }, "a remote sid whose name lacks the prefix (an older frame): whole, never a false host");
  assert.deepEqual(phParts(PHONE, "web"), { kind: "named", before: "Message ", host: null, name: "web", after: "…" }, "no sid at all (the pure callers): a local form");
});

test("other placeholders show as they are, and a session with no name yet keeps the plain resting text", () => {
  assert.deepEqual(phParts("Session closed — read-only", "web"), { kind: "plain", text: "Session closed — read-only" });
  assert.deepEqual(phParts("add your own answer…  (⏎ submit)", "web"), { kind: "plain", text: "add your own answer…  (⏎ submit)" });
  assert.deepEqual(phParts(FULL, ""), { kind: "plain", text: FULL });
  assert.deepEqual(phParts(FULL, null), { kind: "plain", text: FULL });
  assert.deepEqual(phParts(FULL, undefined), { kind: "plain", text: FULL });
});

test("render.ts: the overlay mirrors the placeholder, wears the identity colour, hides when the box holds text, and is re-synced at every writer", () => {
  assert.match(RENDER, /import \{ phParts \} from "\.\/composer-placeholder";/);
  const fn = RENDER.slice(RENDER.indexOf("function syncComposerPh(): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function syncComposerPh(): void {")));
  assert.ok(fn.length > 0, "syncComposerPh exists");
  assert.match(fn, /const parts = phParts\(ta\.placeholder, live\?\.name \|\| meta\?\.name \|\| "", activeId\);/, "the sid rides along: a remote host's prefix is told from the name by the sid (host-prefix.ts)");
  // T328: the host is the tab label's own quiet span (hostNameNodes: .host-prefix, marked when the host is down), a
  // sibling of the bold name, never inside it
  assert.match(fn, /if \(parts\.host\) \{[\s\S]*?ph\.appendChild\(hostNameNodes\(parts\.host \+ parts\.name, activeId\)\[0\]\);\s*\n\s*\}\s*\n\s*const nm = el\("b", "composer-ph-name"\); nm\.textContent = parts\.name;/);
  assert.doesNotMatch(CSS, /#composer-ph \.host-prefix/, "no dress of the overlay's own for the host: the global .host-prefix rule, the tab label's, is the one");
  // the host's link dropping or returning flips the span's .off mark on the tab (the romp-hosts event, host-offline.test.ts);
  // the overlay's span is repainted on the same event, so the two never disagree until the next keystroke
  assert.match(RENDER, /window\.addEventListener\("romp-hosts", \(\) => \{ renderTabs\(\); syncComposerPh\(\); \}\)/);
  assert.match(fn, /parts\.kind === "named" && !ta\.value/, "the styled form only for the resting placeholder, only while the box is empty");
  assert.match(fn, /ta\.classList\.toggle\("ph-on", show\);/, "the native placeholder goes transparent beneath the overlay");
  assert.match(fn, /const colorBg = \(live\?\.color\?\.bg \|\| meta\?\.color\?\.bg\) \|\| null;/, "the identity colour: the live session's, else the strip's own word on the tab (a skeleton's), never a stale session (skeleton-tabs-wiring)");
  assert.match(fn, /if \(colorBg\) nm\.style\.color = colorBg; else nm\.style\.removeProperty\("color"\);/, "the name wears the session's identity colour — the tab label's");
  assert.match(fn, /el\("b", "composer-ph-name"\)/, "…bold");
  // re-synced by the value and placeholder writers: growth after a value write, the active-tab show, a rename or
  // a recolour of the active session, the ask-mode placeholder swap, the width refit and every keystroke
  assert.match(RENDER, /function growComposer\(ta: HTMLTextAreaElement\) \{[\s\S]*?syncComposerPh\(\);[^\n]*\n\}/);
  assert.match(RENDER, /document\.body\.style\.removeProperty\("--active-accent"\);\n\s*syncComposerPh\(\);/);
  assert.match(RENDER, /s\.name = m\.name; renderTabs\(\); if \(m\.id === activeId\) \{ syncComposerPh\(\); updateStatusline\(\); \}/, "a rename of the active session renames the box and the badge");
  assert.match(RENDER, /if \(meta\) meta\.color = color;\n\s*renderTabs\(\);\n\s*if \(id === activeId\) \{ syncComposerPh\(\); updateStatusline\(\); \}/, "a recolour of the active session recolours them on the click");
  assert.match(RENDER, /ta\.classList\.remove\("answering"\);\n\s*\}\n\s*syncComposerPh\(\);/);
  assert.match(RENDER, /ta\.placeholder = composerRestingPlaceholder\(\);\n\s*syncComposerPh\(\);\n\s*\}\)\.observe\(ta\);/);
  assert.match(RENDER, /clearComposerBox\?\.\(\);[^\n]*\n\s*syncComposerPh\(\);/, "the send's one clear path (main, 2026-09) is followed by the overlay's re-sync");
  // the box's LAYOUT moves the textarea too (the user 2026-09-10: a highlight's quote chip added a row above it and the
  // overlay sat on the chips): #composer's ResizeObserver re-places the overlay, and the three row renderers re-sync
  assert.match(fn, /try \{ new ResizeObserver\(\(\) => syncComposerPh\(\)\)\.observe\(box\); \}/, "observed once, when the overlay is made");
  for (const r of ["renderComposerChips", "renderStagedStrip", "renderComposerFiles"]) {
    // renderStagedStrip carries main's `opts` (a reveal of the last chip, 2026-09) through to its inner half
    assert.match(RENDER, new RegExp("function " + r + "\\(id: string \\| null(?:, opts\\?: \\{ reveal\\?: \"last\" \\})?\\): void \\{ " + r + "Inner\\(id(?:, opts)?\\); syncComposerPh\\(\\); \\}"), r + " re-places the overlay on every exit path");
  }
});

test("styles.css: the overlay sits over the box, dim like a placeholder, the name bold; the native placeholder is transparent while it shows", () => {
  assert.match(CSS, /#composer-ph \{ position: absolute; pointer-events: none; color: var\(--dim\);/);
  assert.match(CSS, /#composer-ph \.composer-ph-name \{ font-weight: 600; \}/);
  assert.match(CSS, /#composer-input\.ph-on::placeholder \{ color: transparent; \}/);
  // the question flow (the user 2026-09-10): the answering tint rule sits at that rule's specificity and came later, so
  // both texts showed — the overlay takes the tint, the native placeholder stays transparent under it
  assert.match(CSS, /#composer-input\.ph-on\.answering::placeholder \{ color: transparent; \}/);
  assert.match(CSS, /#composer-input\.answering ~ #composer-ph \{ color: color-mix\(in srgb, var\(--accent\) 65%, var\(--dim\)\); \}/);
});

test("render.ts: the placeholder and the answering tint have one owner — the renders that reset the box go through setComposerAskMode", () => {
  // a render that wrote the resting form and left the tint is how the overlay came to draw over a tinted native text
  assert.match(RENDER, /if \(ta\) \{ ta\.disabled = false; setComposerAskMode\(\); \}/);
  assert.match(RENDER, /if \(closed\) \{ composer\.placeholder = "Session closed — read-only"; composer\.classList\.remove\("answering"\); syncComposerPh\(\); \}\n\s*else setComposerAskMode\(\);/);
  const writers = RENDER.match(/\.placeholder = composerRestingPlaceholder\(\)/g) || [];
  assert.equal(writers.length, 2, "setComposerAskMode's own write and the phone's resize shortening; nothing else writes the resting form");
});
