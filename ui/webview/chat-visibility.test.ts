// The chat page's hidden word for the kernel's pane shim (chat-visibility.ts). The shim gates its stale banner on
// paneHidden(): hidden when its zero-viewport probe OR the word a pane published says so. In Chromium the probe is
// right for a pane hidden since load and blind to one the shell hides after the user has looked at it (the iframe
// keeps its size), and on the phone shell the chat is the pane shown first, so every switch to another tab left the
// chat's watchdog free to raise the banner over a working dashboard. render.ts has no paint gate (every frame paints),
// so chat-visibility.ts publishes the same two measures the gating panes do, from the same events. Two legs here: the
// module over stand-ins (the ordering rule: nothing before the observer's first word), and the source pins (render.ts
// installs it once, on the body, from its own visibility). The served shim's read runs in the Python lane
// (tests/test_kernel_disconnect_banner.py under node; tests/test_pane_hidden_word_browser.py in real browsers).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { watchChatVisibility, type ChatVisibilityDeps, type ObserverEntryLike } from "./chat-visibility";
import type { PaneHiddenHost } from "./paint-gate";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");   // npm test runs in vscode-extension
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const SRC = fs.readFileSync(path.join(UI, "chat-visibility.ts"), "utf8");

// ── the module, over stand-ins ──
type Cb = (entries: ObserverEntryLike[]) => void;
function world() {
  const host: PaneHiddenHost = {};
  const listeners: Array<() => void> = [];
  const doc = { hidden: false, addEventListener: (_t: "visibilitychange", fn: () => void) => { listeners.push(fn); } };
  const observed: Element[] = [];
  let cb: Cb | null = null;
  class FakeIO { constructor(f: Cb) { cb = f; } observe(t: Element) { observed.push(t); } }
  const deps: ChatVisibilityDeps = { doc, win: host, Observer: FakeIO };
  return {
    host, doc, deps, observed, listeners,
    /** the tab's visibilitychange, in the given state */
    tab(state: "hidden" | "visible") { doc.hidden = state === "hidden"; for (const fn of listeners) fn(); },
    /** the observer's callback over the body */
    entry(intersecting: boolean) { assert.ok(cb, "the observer was constructed"); cb!([{ isIntersecting: intersecting }]); },
  };
}

test("nothing before the observer's first word, on either arm; then the union of both measures on every event", () => {
  const w = world();
  const body = {} as Element;
  watchChatVisibility(body, w.deps);
  assert.deepEqual(w.observed, [body], "the observer watches the element it was given: the page's body");
  assert.equal(w.listeners.length, 1, "one visibilitychange listener");
  w.tab("visible");
  assert.equal(typeof w.host.__rompPaneHidden, "undefined", "a return before the observer's first entry publishes nothing: the shim's probe is right at boot");
  w.tab("hidden");
  assert.equal(typeof w.host.__rompPaneHidden, "undefined", "nor does the hidden arm");
  w.doc.hidden = false;
  w.entry(true);
  assert.equal(w.host.__rompPaneHidden, false, "the first entry: on screen");
  w.entry(false);
  assert.equal(w.host.__rompPaneHidden, true, "hidden after a first show: the case the probe misses (the iframe keeps its size)");
  w.tab("hidden"); assert.equal(w.host.__rompPaneHidden, true);
  w.tab("visible"); assert.equal(w.host.__rompPaneHidden, true, "the tab's return is not a show for a display:none pane");
  w.entry(true); assert.equal(w.host.__rompPaneHidden, false, "the re-show publishes on the observer's callback");
  w.tab("hidden"); assert.equal(w.host.__rompPaneHidden, true, "the tab hidden with the pane on screen: hidden");
  w.tab("visible"); assert.equal(w.host.__rompPaneHidden, false, "the return publishes on visibilitychange");
  assert.equal(typeof w.host.__rompPaneHidden, "boolean", "a boolean, the type the shim tests for");
});

test("no observer, or no body: nothing is installed and nothing published; the shim's probe stands", () => {
  const bare = world();
  bare.deps.Observer = null;
  watchChatVisibility({} as Element, bare.deps);
  assert.equal(bare.listeners.length, 0, "no listener either: a page that published document.hidden alone would have no observer to correct it");
  bare.tab("hidden"); bare.tab("visible");
  assert.equal(typeof bare.host.__rompPaneHidden, "undefined");
  const noRoot = world();
  watchChatVisibility(null, noRoot.deps);
  assert.deepEqual(noRoot.observed, []);
  assert.equal(noRoot.listeners.length, 0);
});

// ── the source pins ──
test("render.ts installs the publisher once, at top level, over the page's body, and gates no paint; the module reads the frame's own visibility", () => {
  assert.match(RENDER, /^import \{ watchChatVisibility, browserChatVisibilityDeps \} from "\.\/chat-visibility";/m);
  assert.match(RENDER, /^watchChatVisibility\(document\.body, browserChatVisibilityDeps\(\)\);/m, "top level, so it runs when the bundle loads (the script sits at the end of the body)");
  assert.equal(RENDER.split("watchChatVisibility(").length - 1, 1, "once");
  assert.ok(!RENDER.includes("paintHeld(") && !RENDER.includes("paintReleased("), "the chat gates no paint: every frame paints");
  assert.ok(!SRC.includes("__rompPaneHidden"), "the flag's name lives in paint-gate.ts: one publisher shape for every pane");
  assert.match(SRC, /import \{ publishPaneHidden, type PaneHiddenHost \} from "\.\/paint-gate";/, "the shared publisher");
  assert.match(SRC, /let intersecting: boolean \| null = null;/, "the observer's word starts null: nothing is published before it speaks");
  assert.match(SRC, /new deps\.Observer\(\(entries\) => \{ intersecting = entries\.some\(\(e\) => e\.isIntersecting\); publish\(\); \}\)\.observe\(root\);/);
  assert.match(SRC, /deps\.doc\.addEventListener\("visibilitychange", publish\);/);
  assert.ok(!/set(Interval|Timeout)|requestAnimationFrame/.test(SRC), "on events only, never a timer");
  assert.ok(!SRC.includes("postMessage") && !SRC.includes("parent"), "the frame's own visibility: no shell message, no parent read");
  assert.match(SRC, /Observer: typeof IntersectionObserver === "undefined" \? null : IntersectionObserver,/, "the browser deps: the page's own observer, or none");
});
