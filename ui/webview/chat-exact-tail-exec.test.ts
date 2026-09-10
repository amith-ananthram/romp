// The chat's exact tail path, EXECUTED: chatTail and patchWorkedFooters lifted from render.ts and driven over
// stubs and a minimal fake DOM, the way tab-strip-skip-exec.test.ts lifts renderTabs. chat-exact-tail.test.ts
// pins the SHAPE of what no harness lifts (syncViewInner, reconcileRewind, the frame paths); this file drives
// the BEHAVIOUR of the two functions it can: the kernel's `from` is where the tail re-renders from, a shrunken
// tail or a change inside a window the reader scrolled away from still rebuilds the window, chatTail hands its
// `from` to the rewind pass as the bound, a gap asks for the full session; and the footer patch adds, removes
// and re-homes the fork spot by unit, skips a day divider sharing its turn's unit number, maps compact-mode
// units, and marks the view stale for a reply folded into a run. Synthetic events; epochs are seconds.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { workedFooterPlan } from "./worked-footer";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

/** A render.ts function, transpiled (TS → JS) with esbuild at run time, required dynamically so the test bundle
 *  does not try to bundle esbuild itself (the models-rev.test.ts pattern). */
function liftBetween(startAnchor: string, endAnchor: string): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
}

// ── chatTail ──────────────────────────────────────────────────────────────────────────────────────

type TailHooks = { fulls: string[]; strips: number; rewinds: [string, number | undefined][]; optRecs: number;
                   tabRenders: number; appends: number; bgRenders: number; prebuilds: number };
type TailApi = { chatTail: (msg: any) => void; set: (p: { sessions?: Map<string, any>; views?: Map<string, any>; activeId?: string | null }) => void };

function liftChatTail(): (hooks: TailHooks) => TailApi {
  const js = liftBetween("function chatTail(msg: any) {", "// Older history streaming in from a loadOlder request");
  const prelude = `
    let sessions = new Map(), views = new Map(), activeId = null;
    const ledgers = new Map();
    const H = HOOKS;
    const requestFullSession = (id) => { H.fulls.push(id); };
    const skeletonTabs = { ids: new Set(HOOKS.skeleton || []) };   // a skeleton tab's tail asks for the full instead of splicing (the reconnect regime); the default world holds none
    const isOptimistic = (e) => !!e.opt;
    const stripOptimistic = (s) => { s.events = s.events.filter((e) => !e.opt); H.strips++; };
    const reconcileRewind = (s, bound) => { H.rewinds.push([s.id, bound]); };
    const isHeldGroup = (e) => !!e.held;                 // a group the client made for held kernel copies (T262i): not kernel coordinates
    const reconcileHeldCopies = () => {};                // the held-copy pass runs between the rewind and the pending sends (T262i)
    const reconcileOptimistic = () => { H.optRecs++; };
    const awaitKey = (st) => JSON.stringify((st && st.awaitingWhy) || "");
    const scheduleRenderTabs = () => { H.tabRenders++; };
    const scheduleAppendActive = () => { H.appends++; };
    const renderBgTasks = () => { H.bgRenders++; };
    const awaitChanged = (_sid) => { H.bgRenders++; };   // 2026-09-10: the tail calls this (box + a viewer's header); it counts as the box render
    const schedulePrebuild = () => { H.prebuilds++; };
  `;
  const epilogue = `
    return { chatTail, set: (p) => { if (p.sessions) sessions = p.sessions; if (p.views) views = p.views; if ("activeId" in p) activeId = p.activeId; } };
  `;
  return new Function("HOOKS", prelude + js + epilogue) as (hooks: TailHooks) => TailApi;
}

const kernelEvents = (n: number) => Array.from({ length: n }, (_, i) => ({ kind: i % 2 ? "assistant" : "user", uuid: "e" + i }));
function tailWorld(opts: { rendered: number; winEnd: number; unitTotal: number; active: boolean; headFrom?: number; opt?: boolean }) {
  const H: TailHooks = { fulls: [], strips: 0, rewinds: [], optRecs: 0, tabRenders: 0, appends: 0, bgRenders: 0, prebuilds: 0 };
  const api = liftChatTail()(H);
  const events: any[] = kernelEvents(10);
  if (opts.opt) events.splice(6, 0, { kind: "user", uuid: "opt-1", opt: true });   // a bubble at its send slot, mid-array
  const s: any = { id: "A", events, status: { state: "working" }, headFrom: opts.headFrom };
  const v: any = { rendered: opts.rendered, stale: false, winStart: 0, winEnd: opts.winEnd, unitTotal: opts.unitTotal, el: {} };
  api.set({ sessions: new Map([["A", s]]), views: new Map([["A", v]]), activeId: opts.active ? "A" : "B" });
  return { H, api, s, v };
}

test("a tail for a skeleton tab asks for the full session and touches nothing: the page holds no current copy to splice onto", () => {
  const { H, api, s, v } = tailWorld({ rendered: 10, winEnd: 10, unitTotal: 10, active: true });
  const skel = liftChatTail()({ ...H, skeleton: ["A"] } as any);
  skel.set({ sessions: new Map([["A", s]]), views: new Map([["A", v]]), activeId: "A" });
  skel.chatTail({ id: "A", from: 7, events: [{ kind: "assistant", uuid: "e7b" }] });
  assert.deepEqual(H.fulls, ["A"], "the full is asked for once");
  assert.equal(s.events.length, 10, "the held events are not spliced");
  assert.equal(v.rendered, 10, "nothing repaints");
  assert.equal(H.rewinds.length, 0);
  assert.equal(H.appends, 0);
});

test("the active view re-renders from the kernel's exact first changed event; the rewind pass is bounded there", () => {
  const { H, api, s, v } = tailWorld({ rendered: 10, winEnd: 10, unitTotal: 10, active: true });
  api.chatTail({ id: "A", from: 7, events: [{ kind: "assistant", uuid: "e7b" }, { kind: "user", uuid: "e8b" }, { kind: "assistant", uuid: "e9b" }] });
  assert.equal(v.rendered, 7, "repaint from the exact changed point, not a trailing window");
  assert.equal(v.stale, false, "nothing earlier changed: no window rebuild");
  assert.deepEqual(s.events.slice(7).map((e: any) => e.uuid), ["e7b", "e8b", "e9b"], "the suffix is replaced from `from`");
  assert.deepEqual(H.rewinds, [["A", 7]], "chatTail passes its from as the bound: the editable set is judged below the tail's start");
  assert.equal(H.appends, 1, "one paint scheduled"); assert.equal(H.tabRenders, 1); assert.deepEqual(H.fulls, []);
  api.chatTail({ id: "A", from: 9, events: [{ kind: "assistant", uuid: "e9c" }] });
  assert.equal(v.rendered, 7, "an earlier re-render start stands: min(rendered, from)");
});

test("a tail that shrinks the transcript rebuilds the window: rendered would equal the length and the fast path would skip the repaint", () => {
  const { api, s, v } = tailWorld({ rendered: 10, winEnd: 10, unitTotal: 10, active: true });
  api.chatTail({ id: "A", from: 9, events: [] });   // the last queued message retired with nothing in its place
  assert.equal(s.events.length, 9);
  assert.equal(v.rendered, 9);
  assert.equal(v.stale, true, "a pure truncation marks the view stale");
});

/** A suffix replacing events [from, 10) one for one: the transcript keeps its length, so the shrink rule (its own
 *  test above) stays out of these cases and what they show is the window rule alone. */
const sameLengthFrom = (from: number) => Array.from({ length: 10 - from }, (_, i) => ({ kind: (from + i) % 2 ? "assistant" : "user", uuid: "r" + (from + i) }));

test("a background view: a change inside a window the reader scrolled away from rebuilds it; one below the window, or at the tail, repaints from `from`", () => {
  const away = tailWorld({ rendered: 10, winEnd: 6, unitTotal: 10, active: false });   // window [0,6) of 10 units: not at the tail
  away.api.chatTail({ id: "A", from: 8, events: sameLengthFrom(8) });
  assert.equal(away.v.stale, false, "the change lies below the window: the incremental path's assumption holds");
  assert.equal(away.v.rendered, 8);
  away.api.chatTail({ id: "A", from: 4, events: sameLengthFrom(4) });
  assert.equal(away.v.stale, true, "the change landed inside the scrolled-away window (nothing shrank): rebuild");
  assert.equal(away.H.prebuilds, 2, "the off-screen view is rebuilt in idle either way");
  assert.equal(away.H.appends, 0, "no active paint for a background tab");
  const atTail = tailWorld({ rendered: 10, winEnd: 10, unitTotal: 10, active: false });
  atTail.api.chatTail({ id: "A", from: 4, events: sameLengthFrom(4) });
  assert.equal(atTail.v.stale, false, "at the tail, an exact repaint from `from` is enough");
  assert.equal(atTail.v.rendered, 4);
});

test("a delta starting past what the client holds is a desync: ask for the full session, touch nothing; below the loaded head, nothing", () => {
  const { H, api, s, v } = tailWorld({ rendered: 10, winEnd: 10, unitTotal: 10, active: true });
  api.chatTail({ id: "A", from: 11, events: [{ kind: "user", uuid: "q" }] });
  assert.deepEqual(H.fulls, ["A"]);
  assert.equal(s.events.length, 10); assert.equal(v.rendered, 10); assert.deepEqual(H.rewinds, []);
  api.chatTail({ id: "Z", from: 0, events: [] });
  assert.deepEqual(H.fulls, ["A", "Z"], "a session the client holds no base for asks too");
  const headed = tailWorld({ rendered: 10, winEnd: 10, unitTotal: 10, active: true, headFrom: 5 });
  headed.api.chatTail({ id: "A", from: 3, events: [] });   // global index 3 < headFrom 5: our resident tail is still valid
  assert.equal(headed.s.events.length, 10); assert.deepEqual(headed.H.rewinds, []);
  headed.api.chatTail({ id: "A", from: 12, events: [{ kind: "user", uuid: "n" }] });   // global 12 → local 7
  assert.equal(headed.v.rendered, 7, "msg.from is a global index: mapped through headFrom");
});

test("an optimistic bubble is not in the kernel's coordinate space: the gap check counts kernel events, and the bubble is stripped before the delta applies", () => {
  const { H, api, s, v } = tailWorld({ rendered: 11, winEnd: 11, unitTotal: 11, active: true, opt: true });
  assert.equal(s.events.length, 11);
  api.chatTail({ id: "A", from: 10, events: [{ kind: "user", uuid: "e10" }] });   // one past the kernel's ten: an append, not a gap
  assert.deepEqual(H.fulls, [], "ten kernel events held: from = 10 is the next one");
  assert.equal(H.strips, 1);
  assert.deepEqual(s.events.map((e: any) => e.uuid).slice(9), ["e9", "e10"], "the bubble is gone and the delta appended in kernel coordinates");
  assert.equal(H.optRecs, 1, "…and the in-flight send is re-asserted or retired after");
  assert.equal(v.rendered, 10);
});

test("a status-only tail (empty suffix) replaces the status and re-renders the awaiting box only when the awaited fields changed", () => {
  const { H, api, s, v } = tailWorld({ rendered: 10, winEnd: 10, unitTotal: 10, active: true });
  api.chatTail({ id: "A", from: 10, events: [], status: { state: "ready" } });
  assert.equal(s.status.state, "ready"); assert.equal(v.rendered, 10); assert.equal(v.stale, false);
  assert.equal(H.bgRenders, 0, "no awaited field changed");
  api.chatTail({ id: "A", from: 10, events: [], status: { state: "working", awaitingWhy: "agents" } });
  assert.equal(H.bgRenders, 1, "the awaiting box renders from the same frame that flips the chip");
});

// ── patchWorkedFooters ────────────────────────────────────────────────────────────────────────────

/** Enough of Element for the footer patch: children, a class list, data-unit, and the two selector shapes it uses. */
class FakeEl {
  children: FakeEl[] = []; parent: FakeEl | null = null; dataset: Record<string, string> = {}; textContent = ""; title = "";
  constructor(public tag: string, public className = "") {}
  has(c: string): boolean { return this.className.split(/\s+/).includes(c); }
  appendChild(c: FakeEl): FakeEl { c.parent?.removeChild(c); c.parent = this; this.children.push(c); return c; }
  removeChild(c: FakeEl): void { this.children = this.children.filter((x) => x !== c); c.parent = null; }
  remove(): void { this.parent?.removeChild(this); }
  querySelector(sel: string): FakeEl | null {
    // ':scope > [data-unit="N"]:not(.day-divider)' and ':scope > .cls', the patch's two shapes
    const m = /^:scope > (.+)$/.exec(sel);
    if (!m) throw new Error("unsupported selector " + sel);
    const attr = /\[data-unit="([^"]*)"\]/.exec(m[1]), cls = /^\.([\w-]+)/.exec(m[1]), not = /:not\(\.([\w-]+)\)/.exec(m[1]);
    return this.children.find((c) => (!attr || c.dataset.unit === attr[1]) && (!cls || c.has(cls[1])) && (!not || !c.has(not[1]))) ?? null;
  }
}
type FootHooks = { FakeEl: typeof FakeEl; workedFooterPlan: typeof workedFooterPlan };
type Patch = (v: any, s: any, from: number, working: boolean, items?: any[] | null) => void;

function liftPatch(): (hooks: FootHooks) => Patch {
  const js = liftBetween("function patchWorkedFooters(", "// prevEpoch for event i");
  const prelude = `
    const H = HOOKS;
    const workedFooterPlan = H.workedFooterPlan;
    const eventEpoch = (ev) => (ev.t == null ? null : ev.t);
    const itemFirstEvent = (it) => (it.kind === "toolgroup" || it.kind === "retrygroup" ? it.indices[0] : it.index);
    const elapsedFooter = (secs) => { const f = new H.FakeEl("div", "turn-elapsed"); f.textContent = String(secs); return f; };
  `;
  return new Function("HOOKS", prelude + js + "\nreturn patchWorkedFooters;") as (hooks: FootHooks) => Patch;
}

const user = (t: number) => ({ kind: "user", human: true, t });
const reply = (t: number) => ({ kind: "assistant", t });
const tool = (t: number) => ({ kind: "tool", t });
/** A rendered window: one node per unit tagged data-unit, unit `spotOn` carrying a fork spot. */
function footWorld(events: any[], units: number, spotOn: number) {
  const patch = liftPatch()({ FakeEl, workedFooterPlan });
  const el = new FakeEl("div");
  const nodes: FakeEl[] = [];
  for (let u = 0; u < units; u++) { const n = new FakeEl("div", "turn"); n.dataset.unit = String(u); el.appendChild(n); nodes.push(n); }
  const spot = new FakeEl("span", "fork-spot"); nodes[spotOn].appendChild(spot);
  const v: any = { el, winStart: 0, stale: false };
  return { patch, v, s: { events }, nodes, spot };
}

test("the footer patch adds the elapsed row to the turn's last reply once its turn completes, moving the fork spot into it; a repeat adds nothing", () => {
  const events = [user(100), tool(110), reply(160), user(200)];
  const { patch, v, s, nodes, spot } = footWorld(events, 4, 2);
  patch(v, s, 3, true);                                          // the prompt at 3 just landed: the tail re-rendered [3, 4)
  const f = nodes[2].querySelector(":scope > .turn-elapsed");
  assert.ok(f, "the reply before `from` gained its footer"); assert.equal(f!.textContent, "60");
  assert.equal(spot.parent, f, "the fork spot moved into the new elapsed row, where applyForkSpots places it");
  assert.equal(nodes[3].querySelector(":scope > .turn-elapsed"), null, "the prompt carries none");
  patch(v, s, 3, true);
  assert.equal(nodes[2].children.filter((c) => c.has("turn-elapsed")).length, 1, "already there: not added twice");
  assert.equal(v.stale, false);
});

test("…and takes it off when a reply lands in the same turn (the footer moved to the new last reply), the spot back onto the turn", () => {
  const { patch, v, s, nodes, spot } = footWorld([user(100), tool(110), reply(160), user(200)], 4, 2);
  patch(v, s, 3, true);
  s.events = [user(100), tool(110), reply(160), reply(170)];   // the kernel replaced the suffix: another reply, same turn
  patch(v, s, 3, true);
  assert.equal(nodes[2].querySelector(":scope > .turn-elapsed"), null, "demoted: no longer the turn's last reply");
  assert.equal(spot.parent, nodes[2], "the fork spot is back on the turn itself");
});

test("the session going idle with an empty suffix (from = len) completes the final turn; going back to work takes the footer off again", () => {
  const events = [user(100), tool(110), reply(160)];
  const { patch, v, s, nodes } = footWorld(events, 3, 2);
  patch(v, s, 3, true);
  assert.equal(nodes[2].querySelector(":scope > .turn-elapsed"), null, "still working: the live spinner owns the final turn");
  patch(v, s, 3, false);
  assert.equal(nodes[2].querySelector(":scope > .turn-elapsed")?.textContent, "60", "idle: the footer");
  patch(v, s, 3, true);
  assert.equal(nodes[2].querySelector(":scope > .turn-elapsed"), null, "a nudge put it back to work on the same turn");
});

test("a day divider shares its turn's unit number and is never the footer's home", () => {
  const { patch, v, s, nodes } = footWorld([user(100), tool(110), reply(160), user(200)], 4, 2);
  const divider = new FakeEl("div", "day-divider"); divider.dataset.unit = "2";
  v.el.children.splice(2, 0, divider); divider.parent = v.el;   // the divider precedes its turn in the DOM
  patch(v, s, 3, true);
  assert.equal(divider.children.length, 0, "the divider got nothing");
  assert.ok(nodes[2].querySelector(":scope > .turn-elapsed"), "the turn did");
});

test("compact mode: the window start is a unit and the plan wants an event index; the reply's event maps back to its unit; a reply folded into a run marks the view stale", () => {
  const events = [user(100), tool(110), tool(120), reply(160), user(200)];
  // units: the prompt, one folded tool run, the reply, the prompt
  const items = [{ kind: "event", index: 0 }, { kind: "toolgroup", indices: [1, 2] }, { kind: "event", index: 3 }, { kind: "event", index: 4 }];
  const { patch, v, s, nodes } = footWorld(events, 4, 2);
  patch(v, s, 4, true, items);
  assert.ok(nodes[2].querySelector(":scope > .turn-elapsed"), "event 3 is unit 2: the footer lands on the reply's unit");
  assert.equal(v.stale, false);
  // the reply itself folded into a retry run: no unit is addressable → the window path re-renders
  const folded = [{ kind: "event", index: 0 }, { kind: "retrygroup", indices: [1, 2, 3] }, { kind: "event", index: 4 }];
  const w2 = footWorld(events, 3, 1);
  w2.patch(w2.v, w2.s, 4, true, folded);
  assert.equal(w2.v.stale, true, "unit < 0: stale, so the window path draws the footer");
  assert.ok(w2.nodes.every((n) => !n.querySelector(":scope > .turn-elapsed")), "…and nothing was patched by hand");
  // a window whose start unit is past the items: winEv falls to the event count, so the plan sees no reply before it
  const w3 = footWorld(events, 4, 2); w3.v.winStart = 9;
  w3.patch(w3.v, w3.s, 4, true, items);
  assert.ok(w3.nodes.every((n) => !n.querySelector(":scope > .turn-elapsed")));
});
