// A notification tap lands on the session AND the card that buzzed (the user 2026-09-06). The feed's
// half of that, pinned from both sides of the iframe boundary: the feed announces its first content
// so the shell knows when a {romp:'revealCard'} has something to land on, and the reveal itself never
// dead-ends on a folded thread. Source pins against feed.ts + kernel.py (the render path has no jsdom
// harness), like card-notify's; the shell script itself is EXECUTED by tests/test_kernel_webpush.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the feed announces its FIRST payload's render to the shell, once, the way the timeline does", () => {
  assert.match(SRC, /let feedAnnounced = false;/);
  // after render() inside applyFeedPayload — the cards are in the DOM when this leaves the frame
  assert.match(SRC, /render\(\);\n  if \(!feedAnnounced\) \{\n    feedAnnounced = true;/);
  assert.match(SRC, /window\.parent\.postMessage\(\{ romp: "ready", app: "feed" \}, "\*"\)/);
  assert.equal(SRC.match(/romp: "ready", app: "feed"/g)?.length, 1, "one announcement, one place");
});

test("the shell's reveal script waits for exactly that message, never another pane's ready", () => {
  // the two sides of the contract share the words: app:'feed' is what the shell keys on
  assert.match(KERNEL, /m\.romp==='ready'&&m\.app==='feed'/);
  assert.match(KERNEL, /pendingCard=\{itemId:itemId,sid:sid\};return;/, "a tap before the feed is up is held, not dropped");
  assert.doesNotMatch(KERNEL.slice(KERNEL.indexOf("_LANDING_REVEAL_JS = "), KERNEL.indexOf("_LANDING_REVEAL_JS = ") + 2600),
    /setTimeout/, "event-based: the feed's own ready, no timer");
});

test("revealCard unfolds a collapsed thread before looking for the card (no silent miss on a fold)", () => {
  assert.match(SRC, /function unfoldThreadsFor\(keys: Set<string>\): void \{/);
  // both "take me to this card" entries share it: the bell-entry/notification jump and the chat-dot jump
  // the card id is matched STRUCTURALLY (dataset.key equality), never interpolated into a selector: a
  // crafted push-card value with a quote or bracket used to throw inside querySelector and skip the
  // openSession fallback (review fold on #940, 2026-09-07); the landing script also drops a non-id value
  assert.match(SRC, /const key = "a:" \+ String\(m\.itemId \|\| ""\);\n    unfoldThreadsFor\(new Set\(\[key\]\)\);/);
  assert.match(SRC, /\.find\(\(c\) => c\.dataset\.key === key\) \|\| null;/);
  assert.doesNotMatch(SRC, /querySelector\(`\[data-key="a:\$\{/, "no interpolated selector");
  assert.match(KERNEL, /if\(pc&&!\/\^\[A-Za-z0-9_\.:-\]\{1,128\}\$\/\.test\(pc\)\)pc='';/, "a non-id push-card is dropped before it lands");
  assert.match(SRC, /function revealCards\(keys: Set<string>\) \{\n  unfoldThreadsFor\(keys\);/);
  // and the existing fallback stands: a card gone from the feed still opens its session
  assert.match(SRC, /\} else if \(m\.sid\) \{\n      vscodeApi\?\.postMessage\(\{ type: "openSession", id: String\(m\.sid\) \}\);/);
});

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("a reveal outranks the persisted-tab restore: the focus handler retires wantActive", () => {
  // the federated cold start (the user 2026-09-06, on the phone): the kernel parks the tap's focus
  // and delivers it on the chat pane's ready — BEFORE any remote host has relayed its sessions. The
  // focus sets the active tab; then the persisted tab (a remote one, arriving later over its own
  // socket) matched wantActive and setActive'd itself over the reveal. An explicit reveal is newer
  // information than a restore of where the page last was.
  const focusBlock = (RENDER.match(/else if \(m\.type === "focus"\) \{[\s\S]*?\n  \}/) || [""])[0];
  assert.ok(focusBlock.length > 100, "found the focus handler");
  assert.match(focusBlock, /\n    wantActive = null;/);
  // …and the restore itself is still the one-shot it was: consumed on arrival, or retired here
  assert.match(RENDER, /if \(wantActive && msg\.id === wantActive && stripLists\(msg\.id\) && heldHere\(msg\.id\)\) \{ wantActive = null; restoreIfShown\(msg\.id\); \}/, "consumed on arrival, through the one restore rule (shown takes focus; hidden stays unfocused), while this column holds it (the chat split, 2026-09-11)");
});

test("a focus on a federated session its host has not relayed yet shows the loader, not 'No session open'", () => {
  // the tab exists nowhere on the client until the owning host's relay lands; the id's host prefix
  // says one is coming, so the wait wears the romp loader (CLAUDE.md: loading states) instead of the
  // empty-dashboard copy, which read as the tap having done nothing
  assert.match(RENDER, /if \(activeId && \(tabMeta\.has\(activeId\) \|\| hostOf\(activeId\)\)\) \{/);
  assert.match(RENDER, /: \(hostOf\(activeId\) \? "a session on " \+ hostOf\(activeId\) : "“session”"\);/);
});
