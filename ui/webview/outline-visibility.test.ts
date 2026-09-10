// The outline pane rebuilds only when it can be seen (2026-09-04). The dashboard shell keeps it in a
// display:none iframe by default, yet every feed push rebuilt its whole list on the main thread the chat
// pane's clicks share. The first content still paints through while hidden, so revealing the pane shows
// the list at once instead of the pane loader fading over nothing. Pinned at the source, like the other
// pane-wiring tests.
//
// 2026-09-07 (the user, whose dashboard froze on the return to its browser tab): the observer-only gate
// kept rebuilding an ON-SCREEN pane in a HIDDEN tab — the observer sees the pane, not the tab — and its
// callback never fires on the return (nothing intersected differently), so nothing released. The gate
// now reads both measures through the shared pure decision (paint-gate.ts), and visibilitychange releases
// alongside the observer.
//
// The same two measures are the pane's hidden word for the kernel's pane shim (paint-gate.ts publishPaneHidden):
// the shim gates its stale banner on a zero-viewport probe that, in Chromium, misses a pane hidden after a first
// show (a display:none iframe keeps the size of its last show), so the gate publishes document.hidden OR the
// observer's last word as window.__rompPaneHidden on its own events, and nothing before the observer has spoken.
// The pins in the first tests hold fleet.ts's wiring by text; the harness after them lifts those lines from fleet.ts
// (the state, the observer, releasePaint, both visibilitychange listeners and render()'s head through the gate),
// transpiles them at run time and runs them over the pure module with the page stood in.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { paintHeld, paintReleased, publishPaneHidden, type PaneHiddenHost } from "./paint-gate";

const requireCjs = createRequire(__filename);

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "fleet.ts"), "utf8");
const body = (name: string) => new RegExp("^function " + name + "\\([\\s\\S]*?\\n\\}", "m").exec(SRC)![0];

test("render() defers while the list is off screen, lets the first content through, and paints once when shown", () => {
  assert.match(SRC, /import \{ paintHeld, paintReleased, publishPaneHidden \} from "\.\/paint-gate";/);
  assert.match(SRC, /function render\(\) \{[\s\S]*?if \(!paneWatching\) \{ paneWatching = true; watchPaneVisibility\(list\); \}/);
  assert.match(SRC, /if \(paintHeld\(document\.hidden, paneVisible, list\.childElementCount > 0\)\) \{ paneDirty = true; return; \}/);
  assert.match(SRC, /if \(typeof IntersectionObserver === "undefined"\) return;/, "no observer → the tab's visibility alone gates");
  assert.match(SRC, /let paneVisible: boolean \| null = null;/, "the observer's word, null until it speaks: the gate reads null as on screen, so the first paint is never withheld, and nothing is published for it");
});

test("the gate includes the TAB's visibility: an on-screen pane in a hidden tab holds (the observer-only gate did not)", () => {
  // the pure decision fleet.ts now calls, with the pane on screen by the observer's measure
  assert.equal(paintHeld(true, true, true), true, "hidden tab → held");
  assert.equal(!true && true, false, "the old gate (`!paneVisible && content`) let this case through");
  assert.equal(paintHeld(true, true, false), false, "…but the first content still paints through");
  assert.equal(paintHeld(false, true, true), false);
});

test("BOTH release events run the same synchronous release: the observer's callback and visibilitychange→visible", () => {
  assert.match(body("watchPaneVisibility"), /new IntersectionObserver\(\(entries\) => \{\n\s*paneVisible = entries\.some\(\(e\) => e\.isIntersecting\);\n\s*releasePaint\(\);\n\s*\}\)\.observe\(list\);/);
  const rel = body("releasePaint");
  assert.match(rel, /function releasePaint\(\): void \{\n\s*publishPaneHidden\(document\.hidden, paneVisible\);\n\s*if \(!paintReleased\(paneDirty, document\.hidden, paneVisible\)\) return;\n\s*paneDirty = false;\n\s*render\(\);/,
    "the release publishes the pane's word first (the observer's callback and the visible arm both come through here), then settles the owed paint");
  assert.doesNotMatch(rel, /requestAnimationFrame|setTimeout|queueMicrotask/, "synchronous: the earliest fresh frame after the compositor's cached one");
  assert.match(SRC, /document\.addEventListener\("visibilitychange", \(\) => \{ if \(!document\.hidden\) releasePaint\(\); \}\);/);
  assert.match(SRC, /document\.addEventListener\("visibilitychange", \(\) => \{ if \(document\.hidden\) publishPaneHidden\(true, paneVisible\); \}\);/,
    "the hidden arm releases nothing, so it publishes the word itself");
  assert.equal(SRC.split("publishPaneHidden(").length - 1, 2, "two publish sites, both on the gate's events; no timer");
});

test("the payload is applied whatever the visibility — only the rebuild waits", () => {
  // the message handler swaps the model before render() decides whether to paint; no visibility check on the way
  // (the listener is installed through frame-listener.ts's helper, on window and in federation's registry, wrapped
  // by perf-telemetry's per-frame timing; the handler body is unchanged)
  const handler = /listenForFrames\(perfFrameHandler\("fleet", \(m\) => vscodeApi\?\.postMessage\(m\), \(e: MessageEvent\) => \{[\s\S]*?\n\}\)\);/.exec(SRC)![0];
  assert.match(handler, /loaded = true;\n\s*sessions = m\.ledgers as FleetSession\[\];/);
  assert.doesNotMatch(handler, /document\.hidden|paneVisible|paneDirty|paintHeld/);
});

// ── the wiring, run: fleet.ts's own lines over the pure module ──
// The slice from `let paneVisible` through render()'s gate (the line before `list.replaceChildren();`) is fleet.ts's
// text, transpiled with esbuild at run time and closed with a stand-in for the rest of the paint (the
// chat-exact-tail-exec.test.ts pattern). The page is stood in: `document` (hidden, the two visibilitychange
// listeners, the list by id), `IntersectionObserver` (its callback kept for the test to fire) and the publisher
// bound to `host`, the window stand-in the pane's word lands on (fleet.ts passes two arguments, so the page's window
// is the host). The boot render() installs the observer, as fleet.ts's own does at load.
type Cb = (entries: { isIntersecting: boolean }[]) => void;
function liftWiring(): string {
  const start = "let paneVisible: boolean | null = null;", end = "  list.replaceChildren();";
  const a = SRC.indexOf(start), b = SRC.indexOf(end, a);
  assert.ok(a > 0 && b > a, "the wiring's anchors moved; re-anchor");
  // the stand-in tail: fleet.ts leaves the list empty before the first payload (`if (!loaded) ...`), then paints it
  return requireCjs("esbuild").transformSync(SRC.slice(a, b) + "  if (!S.loaded) return;\n  paint();\n}\n", { loader: "ts" }).code;
}
function paneHarness() {
  const st = { hidden: false, loaded: false, model: 0, painted: 0, frames: 0, paints: 0, host: {} as PaneHiddenHost };
  const listeners: Array<() => void> = [];
  let observerCb: Cb | null = null;
  const list = { get childElementCount() { return st.painted; } };
  const fakeDocument = {
    get hidden() { return st.hidden; },
    addEventListener(_type: string, fn: () => void) { listeners.push(fn); },
    getElementById(id: string) { return id === "fleet-list" ? list : null; },
  };
  class FakeObserver { constructor(cb: Cb) { observerCb = cb; } observe(_target: unknown) {} }
  const prelude = `
    const paintHeld = P.paintHeld, paintReleased = P.paintReleased;
    const publishPaneHidden = (docHidden, intersecting) => P.publishPaneHidden(docHidden, intersecting, S.host);
    const syncFleetTagBtn = null;
    const paint = () => { S.paints++; S.painted = S.model; };
  `;
  const api = new Function("P", "S", "document", "IntersectionObserver", prelude + liftWiring() + "\nreturn { render, releasePaint };")(
    { paintHeld, paintReleased, publishPaneHidden }, st, fakeDocument, FakeObserver) as { render(): void; releasePaint(): void };
  api.render();   // fleet.ts's boot render(): the observer is installed; nothing is loaded, so nothing paints
  assert.ok(observerCb, "render() installed the observer over the list");
  return {
    st,
    /** the frame handler: the payload applied (n rows), then the gated render */
    frame(n: number) { st.loaded = true; st.model = n; st.frames++; api.render(); },
    /** the IntersectionObserver's callback over the list */
    observer(intersecting: boolean) { observerCb!([{ isIntersecting: intersecting }]); },
    /** the tab's visibilitychange: fleet.ts's two listeners run, the visible arm's release and the hidden arm's publish */
    tab(state: "hidden" | "visible") { st.hidden = state === "hidden"; for (const fn of listeners) fn(); },
  };
}

test("run: frames after the first are held while the pane is hidden, and the newest paints once on show", () => {
  const p = paneHarness();
  p.observer(false);                                   // the list is display:none: the observer's first word
  p.frame(1);
  assert.equal(p.st.paints, 1, "the first content paints through while hidden (the pane loader retires on it)");
  p.frame(2); p.frame(3);
  assert.equal(p.st.paints, 1, "later frames are held while hidden");
  assert.equal(p.st.frames, 3, "and every one of them was applied");
  p.observer(true);                                    // shown
  assert.equal(p.st.paints, 2, "the show paints exactly once");
  assert.equal(p.st.painted, 3, "the paint is of the NEWEST state, not the frames in between");
  p.observer(true);
  assert.equal(p.st.paints, 2, "a shown pane with nothing owed paints nothing on a repeated word");
  p.frame(4);
  assert.equal(p.st.paints, 3, "a shown pane's frames paint at once");
});

test("run: the pane's word is unset until the observer's first word, then the observer's OR the tab's, so a pane hidden AFTER a first show reads hidden", () => {
  // The shim's own probe (innerWidth === 0) is blind to this pane: shown once, then display:none, its iframe keeps its
  // size in Chromium. The gate sees the observer's word and publishes it; every publish is on a gate event.
  const p = paneHarness();
  assert.equal(typeof p.st.host.__rompPaneHidden, "undefined", "boot: nothing published before the first event, so the probe decides (right for a pane hidden since load)");
  p.frame(1);
  assert.equal(typeof p.st.host.__rompPaneHidden, "undefined", "a frame is not a gate event: the first paint publishes nothing");
  p.observer(true);                                    // the observer's first word: on screen
  assert.equal(p.st.host.__rompPaneHidden, false, "shown");
  p.observer(false);                                   // the shell hides the pane after the show; innerWidth would still read its size
  assert.equal(p.st.host.__rompPaneHidden, true, "hidden after a first show: the case the probe misses, read hidden");
  p.tab("hidden"); p.tab("visible");                   // the tab blinks while the pane is still display:none
  assert.equal(p.st.host.__rompPaneHidden, true, "the tab's return is not a show for a pane the observer cannot see");
  p.observer(true);
  assert.equal(p.st.host.__rompPaneHidden, false, "the re-show publishes on the observer's callback");
  p.tab("hidden");
  assert.equal(p.st.host.__rompPaneHidden, true, "the tab hidden with the pane on screen: hidden (a banner nobody can see is not raised; the return re-raises if still stale)");
  p.tab("visible");
  assert.equal(p.st.host.__rompPaneHidden, false, "the tab's return publishes on visibilitychange");
  assert.equal(typeof p.st.host.__rompPaneHidden, "boolean", "a boolean, the type the shim tests for");
});

test("run: before the observer's first word a visibilitychange publishes nothing, on either arm; after it, both arms publish", () => {
  // A page loaded in a background tab gets no IntersectionObserver callback until the tab's first rendering step
  // after its return, so the return's visibilitychange runs first; a word published then would be document.hidden
  // alone, and the probe is right at that moment (a never-shown frame reads 0). So the pane stays silent until the
  // observer has spoken, and the paint side is as before: the first content painted through and nothing is owed.
  const p = paneHarness();
  p.frame(1);
  p.tab("visible");
  assert.equal(typeof p.st.host.__rompPaneHidden, "undefined", "the return before the observer's first entry publishes nothing");
  p.tab("hidden");
  assert.equal(typeof p.st.host.__rompPaneHidden, "undefined", "nor does the hidden arm");
  p.tab("visible");
  assert.equal(p.st.paints, 1, "the paint side is unchanged: the first content painted through and nothing is owed");
  p.observer(false);                                   // the observer's first entry: display:none
  assert.equal(p.st.host.__rompPaneHidden, true, "the first entry publishes");
  p.tab("hidden"); assert.equal(p.st.host.__rompPaneHidden, true);
  p.tab("visible"); assert.equal(p.st.host.__rompPaneHidden, true, "both arms publish now, and the return of a display:none pane still reads hidden");
  p.observer(true); assert.equal(p.st.host.__rompPaneHidden, false);
  p.tab("hidden"); assert.equal(p.st.host.__rompPaneHidden, true, "the hidden arm");
  p.tab("visible"); assert.equal(p.st.host.__rompPaneHidden, false, "the visible arm");
});
