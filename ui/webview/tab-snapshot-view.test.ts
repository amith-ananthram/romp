// A SECTION AT A GLANCE, THE VIEW HELPERS (tab-snapshot-view.ts): the pane-side rules render.ts applies to
// the view, pure so they execute here without a DOM: which row click may open a session, how Escape yields
// to every layer that owns its own Escape, and how the rows update in place. The model and its words are
// tab-snapshot.ts; the pane's wiring is tab-snapshot-pane.test.ts. Synthetic only: the notes-api demo world.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { rowStillOpen, installSnapshotEscape, reconcileRows } from "./tab-snapshot-view";

test("a row whose session left the strip mid-press opens nothing: a frame's row needs its session, a placeholder's row its meta, and a closing tab is neither", () => {
  // click safety keeps the pressed row in the DOM until the release, so a session dismissed between mousedown
  // and mouseup (the kernel's closed frame, a tabOrder push without it) is still under the click; setActive on
  // that id would find tabMeta still holding it (dismissSession leaves tabMeta to the next tabOrder frame) and
  // put up a loader for a session that never arrives
  const frame = { loading: false }, placeholder = { loading: true };
  assert.equal(rowStillOpen(frame, true, true, false), true, "a live row with its session: opens");
  assert.equal(rowStillOpen(frame, false, true, false), false, "its session gone, its meta lingering: nothing opens");
  assert.equal(rowStillOpen(frame, false, false, false), false, "session and meta gone: nothing opens");
  assert.equal(rowStillOpen(placeholder, false, true, false), true, "a placeholder row (no frame yet) with its meta: opens, the loading branch is its state");
  assert.equal(rowStillOpen(placeholder, false, false, false), false, "a placeholder whose meta left: nothing opens");
  assert.equal(rowStillOpen(placeholder, true, false, false), true, "a placeholder whose frame landed mid-press: the session is there, it opens");
  assert.equal(rowStillOpen(frame, true, true, true), false, "the user closed it (closingTabs): nothing opens, whatever the maps still hold");
  assert.equal(rowStillOpen(undefined, true, true, false), true, "no model row for the id (never expected): the session decides");
  assert.equal(rowStillOpen(undefined, false, true, false), false);
});

// A stand-in for one keydown's dispatch through the chat frame, in DOM order: the window's capture listeners,
// the DOCUMENT's capture listeners (where the shell's Escape chain sits: kernel.py _LANDING_ESC_JS wires onEsc
// onto this frame's contentDocument at capture), then the target and the bubble back up to the window. A
// listener that stops propagation ends the walk, as in a browser.
type Fn = (e: KeyboardEvent) => void;
function frame() {
  const winCap: Fn[] = [], winBub: Fn[] = [], docCap: Fn[] = [];
  const win = { addEventListener: (_t: "keydown", fn: Fn, capture?: boolean) => { (capture ? winCap : winBub).push(fn); } };
  const dispatch = (e: KeyboardEvent) => {
    const ev = e as unknown as { stopped: boolean };
    for (const f of winCap) f(e); if (ev.stopped) return;
    for (const f of docCap) f(e); if (ev.stopped) return;
    for (const f of winBub) f(e);
  };
  return { win, docCap, winCap, winBub, dispatch };
}
const key = (k: string, target: EventTarget | null = null) => {
  const e = { key: k, target, defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; } };
  return e as unknown as KeyboardEvent;
};
// the shell's chain, as _LANDING_ESC_JS behaves: a panel open, close it, mark the Escape, stop it; else nothing
const shell = (panel: { open: boolean }) => (e: KeyboardEvent) => { if (e.key === "Escape" && panel.open) { panel.open = false; e.preventDefault(); e.stopPropagation(); } };

test("Escape leaves the view only when no layer claimed it: the shell's panels live out of this page's sight, so the decision waits for the shell's chain", () => {
  // a one-phase handler at the window's capture would run before the shell's document-capture chain and could
  // not see the shell document's panels: an Escape aimed at the log panel would close it AND swap the pane. Two
  // phases: armed at capture (this page's own layers still on the page to be seen), decided at bubble, which an
  // Escape the shell stopped never reaches
  const f = frame();
  const panel = { open: true };
  f.docCap.push(shell(panel));
  let showing = true, left = 0, layer = false;
  installSnapshotEscape(f.win, { showing: () => showing, typing: () => false, layerOpen: () => layer, leave: () => { left++; showing = false; } });
  assert.equal(f.winCap.length, 1); assert.equal(f.winBub.length, 1, "one listener per phase");
  let e = key("Escape"); f.dispatch(e);
  assert.equal(panel.open, false, "the shell closed its panel");
  assert.equal(left, 0, "and the view stayed");
  assert.equal(e.defaultPrevented, true, "the shell's mark");
  e = key("Escape"); f.dispatch(e);
  assert.equal(left, 1, "nothing open anywhere: the Escape reaches the bubble unclaimed; the view leaves, once");
  assert.equal(e.defaultPrevented, true, "and is marked as taken");
  f.dispatch(key("Escape"));
  assert.equal(left, 1, "not showing any more: nothing to leave");
});

test("the arm yields to this page's own layers at capture, to an earlier capture listener's mark, and to a typing target; a stale arm never fires on a later key", () => {
  const f = frame();
  let left = 0, layer = false, typing = false;
  installSnapshotEscape(f.win, { showing: () => true, typing: () => typing, layerOpen: () => layer, leave: () => { left++; } });
  layer = true; f.dispatch(key("Escape")); assert.equal(left, 0, "a page layer open at capture (a menu, the picker, a preview): not armed, even though nothing marks the Escape later");
  layer = false; typing = true; f.dispatch(key("Escape")); assert.equal(left, 0, "a field keeps its Escape");
  typing = false;
  // the tab menu's closer: a window-capture listener registered before this one, marking the Escape it consumed
  f.winCap.unshift((e) => { if (e.key === "Escape") e.preventDefault(); });
  f.dispatch(key("Escape")); assert.equal(left, 0, "an Escape already marked at capture is not ours");
  f.winCap.shift();
  // an Escape the shell stopped leaves the arm set; the next key must not inherit it
  const panel = { open: true }; f.docCap.push(shell(panel));
  f.dispatch(key("Escape")); assert.equal(left, 0, "stopped by the shell");
  f.docCap.pop();
  f.dispatch(key("a")); assert.equal(left, 0, "a later, unrelated key reaching the bubble does not fire the stale arm");
  f.dispatch(key("Escape")); assert.equal(left, 1, "the next unclaimed Escape does");
  // a mark set BETWEEN the phases by a listener that does not stop propagation (a target-phase handler) also wins
  f.docCap.push((e) => { if (e.key === "Escape") e.preventDefault(); });
  f.dispatch(key("Escape")); assert.equal(left, 1, "marked between the phases: yielded");
});

// A stand-in for the list node: children in order, the DOM's insertBefore (a node already in the list is
// moved: detached, then put before the reference; a null reference appends) and removeChild. Each row's node
// is a plain object, so identity is what the assertions compare.
type Row = { id: string; now: string };
type Node = { id: string | null; text: string };
class List {
  children: Node[] = [];
  insertBefore(n: Node, ref: Node | null): void {
    const i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1);
    const j = ref ? this.children.indexOf(ref) : -1;
    if (j < 0) this.children.push(n); else this.children.splice(j, 0, n);
  }
  removeChild(n: Node): void { const i = this.children.indexOf(n); if (i >= 0) this.children.splice(i, 1); }
}
const rows = (...rs: Array<[string, string]>): Row[] => rs.map(([id, now]) => ({ id, now }));

test("a changed row keeps its node, patched; a row that came is made and one that went is removed with the others standing; a reorder moves without remaking", () => {
  // sameRow (tab-snapshot.ts) folds lastT and lastMsg, so the model changes on nearly every push while a member
  // works: a wholesale repaint would destroy the button a keyboard user had Tabbed onto within seconds (focus to
  // body, Enter dead) and dismiss a hover's title. Focus and the title belong to the NODE, so the node is kept.
  const list = new List();
  const made: string[] = [], patched: string[] = [];
  const key = (n: Node) => n.id;
  const make = (r: Row): Node => { made.push(r.id); return { id: r.id, text: r.now }; };
  const patch = (n: Node, r: Row) => { patched.push(r.id); n.text = r.now; };
  let out = reconcileRows(list, rows(["web", "building the list page"], ["api", "idle"], ["tests", "running the suite"]), key, make, patch);
  assert.deepEqual(out, { kept: 0, made: 3, moved: 0, removed: 0 }, "the first paint: every row made, in the model's order");
  assert.deepEqual(list.children.map((n) => n.id), ["web", "api", "tests"]);
  const [web, api, tests] = list.children;
  made.length = 0; patched.length = 0;
  out = reconcileRows(list, rows(["web", "building the list page"], ["api", "reading the schema"], ["tests", "running the suite"]), key, make, patch);
  assert.equal(list.children[1], api, "the same object: focus and the hover title stay on it");
  assert.equal(api.text, "reading the schema", "with the new parts");
  assert.deepEqual(made, [], "nothing remade");
  assert.deepEqual(patched, ["web", "api", "tests"], "every standing row takes the model's parts");
  assert.deepEqual(out, { kept: 3, made: 0, moved: 0, removed: 0 });
  made.length = 0;
  out = reconcileRows(list, rows(["web", "building the list page"], ["tests", "running the suite"], ["docs", "drafting the guide"]), key, make, patch);
  assert.deepEqual(list.children.map((n) => n.id), ["web", "tests", "docs"], "a row gone and a row come: the others' nodes stand where they were");
  assert.equal(list.children[0], web); assert.equal(list.children[1], tests);
  assert.deepEqual(made, ["docs"]);
  assert.deepEqual(out, { kept: 2, made: 1, moved: 0, removed: 1 });
  const docs = list.children[2];
  out = reconcileRows(list, rows(["docs", "drafting the guide"], ["web", "building the list page"], ["tests", "running the suite"]), key, make, patch);
  assert.deepEqual(list.children, [docs, web, tests], "a reorder: the same three objects, in the new order");
  assert.deepEqual(out, { kept: 3, made: 0, moved: 1, removed: 0 });
  out = reconcileRows(list, rows(), key, make, patch);
  assert.deepEqual(list.children, [], "an empty section: every row removed");
  assert.deepEqual(out, { kept: 0, made: 0, moved: 0, removed: 3 });
});

test("a node without a key and a second node under one key (never expected) are removed, not reused: one node per row", () => {
  const list = new List();
  const stray: Node = { id: null, text: "" }, web: Node = { id: "web", text: "" }, web2: Node = { id: "web", text: "twin" };
  list.children.push(stray, web, web2);
  const out = reconcileRows(list, rows(["web", "building"]), (n) => n.id, (r) => ({ id: r.id, text: r.now }), (n, r) => { n.text = r.now; });
  assert.deepEqual(list.children, [web], "the first node under the key is the row's; the stray and the twin go");
  assert.equal(web.text, "building");
  assert.deepEqual(out, { kept: 1, made: 0, moved: 0, removed: 2 });
});
