// federation.ts hands its MERGED frames (feed, tabOrder, data, bars) to the pane's handler by direct call once the
// pane registers it (onFrame, published as window.__rompFed.onFrame; frame-listener.ts is how a pane registers),
// and dispatches them on window only when nothing registered (emit).
//
// Why: the live `fed:feed` bracket ran 15-25 ms per feed push in every feed-consuming pane (75-110 ms in slow
// minutes) while federation's own compute is under a millisecond. Blink hands a same-world "message" listener the
// event's data object itself, but a listener in ANOTHER JavaScript world (a browser extension's content script)
// that reads event.data receives a structured clone of the whole frame, made synchronously inside dispatchEvent:
// 35-46 ms and about 7 MB of garbage per dispatch of a 7 MB frame in the probe, 0 ms for a direct call. The pane
// receives the same merged object, in the same order, once per frame; only the delivery changes. Executed against
// the real manager on the bare stand-in federation-closed-store.test.ts uses, extended with a dispatch counter.
// Synthetic only (host TESTHOST, placeholder ids).
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { FederationManager, reportListenerError } from "./federation";
import { listenForFrames } from "./frame-listener";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const U = "11111111-2222-3333-4444-555555555555";
const V = "99999999-8888-7777-6666-555555555555";

/** The bare manager over a stand-in window: `windowed` collects every frame that reaches window.dispatchEvent,
 *  `reported` every exception the subscriber path reported (reportError stood in). */
function withManager(fn: (fm: any, windowed: any[], reported: unknown[]) => void): void {
  const windowed: any[] = [];
  const reported: unknown[] = [];
  const store = new Map<string, string>();
  const g: any = globalThis;
  const hadWindow = "window" in g, prevWindow = g.window;
  const hadLS = "localStorage" in g, prevLS = g.localStorage;
  const hadRE = "reportError" in g, prevRE = g.reportError;
  g.window = { dispatchEvent: (ev: any) => { if (ev && ev.data) windowed.push(ev.data); } };
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  g.reportError = (e: unknown) => { reported.push(e); };
  try { fn(new FederationManager(), windowed, reported); } finally {
    if (hadWindow) g.window = prevWindow; else delete g.window;
    if (hadLS) g.localStorage = prevLS; else delete g.localStorage;
    if (hadRE) g.reportError = prevRE; else delete g.reportError;
  }
}
const types = (frames: any[]) => frames.map((m) => m && m.type);
const ask = (itemId: string, sid: string, extra: Record<string, unknown> = {}) => ({ itemId, sid, kind: "ask", text: "which port?", t: 1000, ...extra });
const feedFrame = (asks: any[], buildId: number) => ({ type: "feed", now: 1_700_000_000, buildId, asks, ledgers: [], items: [], working: [], order: [] });
const laneData = (sids: string[]) => ({ type: "data", data: { now: 1_700_000_000, sessions: sids.map((id) => ({ id, name: "web" })), lanes: [] } });

// ── the direct path ──

test("a registered handler receives the merged frame by direct call: the same objects, in order, and nothing reaches window", () => {
  withManager((fm, windowed) => {
    const got: MessageEvent[] = [];
    fm.onFrame((e: MessageEvent) => got.push(e));
    const a = ask("a1", U), b = ask("b1", U);
    fm.inbound("", feedFrame([a, b], 1));
    const b2 = ask("b1", U, { text: "which port, again?" });
    fm.inbound("", feedFrame([a, b2], 2));
    assert.deepEqual(types(got.map((e) => e.data)), ["feed", "feed"], "each push is re-emitted as a merged feed frame, as before");
    assert.ok(got[0] instanceof MessageEvent, "the handler sees a MessageEvent, the window listener's own shape");
    // by identity: what the merge built is what the pane holds — no copy, no clone on the way
    assert.equal(got[0].data.asks[0], a, "the first frame's card is the wire object itself");
    assert.equal(got[1].data.asks[0], a, "…and so is the second frame's");
    assert.equal(got[1].data.asks[1], b2, "the changed card is the second push's object");
    assert.notEqual(got[1].data.asks[1], b);
    assert.deepEqual(types(windowed), [], "no merged frame was dispatched on window");
  });
});

test("with no handler registered every frame still goes to window.dispatchEvent — a pane bundle from before the registry keeps working", () => {
  withManager((fm, windowed) => {
    fm.inbound("", feedFrame([ask("a1", U)], 1));
    fm.inbound("", feedFrame([], 2));
    fm.inbound("", { type: "tabOrder", order: [U], tabs: [{ id: U, name: "web" }] });
    fm.inbound("", laneData([U]));
    fm.inbound("", { type: "bars", turns: [] });
    assert.deepEqual(types(windowed), ["feed", "feed", "tabOrder", "data", "bars"]);
    assert.deepEqual(windowed[1].asks, [], "the second push replaced the first on this path too");
  });
});

test("tabOrder, data and bars follow the same rule as feed: direct to the handler, never on window", () => {
  withManager((fm, windowed) => {
    const got: any[] = [];
    fm.onFrame((e: MessageEvent) => got.push(e.data));
    fm.inbound("", { type: "tabOrder", order: [U], tabs: [{ id: U, name: "web" }], selfHost: "TESTHOST" });
    fm.inbound("", laneData([U]));
    fm.inbound("", { type: "bars", turns: [] });
    assert.deepEqual(types(got), ["tabOrder", "data", "bars"]);
    assert.deepEqual(got[0].order, [U]);
    assert.equal(got[0].freshHost, "", "a host's own push is a fresh emission, as before");
    assert.deepEqual(got[1].data.sessions.map((s: any) => s.id), [U]);
    assert.deepEqual(types(windowed), []);
  });
});

test("the frames that stay on window keep going there with a handler registered: the passthrough, closed, hostUp — and never to the handler", () => {
  withManager((fm, windowed) => {
    const got: any[] = [];
    fm.onFrame((e: MessageEvent) => got.push(e.data));
    fm.inbound("", { type: "tabOrder", order: [U], tabs: [{ id: U, name: "web" }] });
    fm.inbound("", { type: "dirListing", path: "src", entries: [] });   // the passthrough (file-browse.ts reads it on window)
    fm.inbound("", { type: "usage", usage: {} });                        // strip.ts reads it on window
    fm.inbound("", { type: "settingsSync", settings: {} });              // settings.ts reads it on window
    fm.inbound("", { type: "session", id: U, events: [] });              // the chat's frames pass through too
    fm.inbound("", { type: "closed", id: U });                           // the pane's teardown frame, then a merged re-emit
    assert.deepEqual(types(windowed), ["dirListing", "usage", "settingsSync", "session", "closed"]);
    assert.deepEqual(types(got), ["tabOrder", "tabOrder"], "the merged order after the closed fold reached the handler directly");
    assert.equal(got[1].reemit, true);
    assert.deepEqual(got[1].order, []);
  });
});

test("a detach's hostDrop `closed` frames and an undeliverable send's `warn` stay on window with a handler registered, and never reach the handler", () => {
  withManager((fm, windowed) => {
    const got: any[] = [];
    fm.onFrame((e: MessageEvent) => got.push(e.data));
    // an attached host whose socket is not open: its sessions are on screen, its tunnel is down
    fm.conns.set("TESTHOST", { host: "TESTHOST", ws: null, url: "", closed: false, live: false, lastRecv: 0, resumeProvisional: 0, connT: 0, pending: new Map() });
    fm.hostSeq.push("TESTHOST");
    fm.perHostSids["TESTHOST"] = new Set(["TESTHOST:" + V]);
    fm.sendRemote("TESTHOST", { type: "reply", id: "TESTHOST:" + V, text: "which port?" });   // not a queued setting: dropped, with a warn
    assert.deepEqual(types(windowed), ["warn"], "the drop is said on window (render.ts toasts it there)");
    assert.match(windowed[0].text, /TESTHOST is unreachable/);
    fm.closeRemote("TESTHOST");   // /tunnels no longer lists it: its sessions close with hostDrop, then the merged re-emits
    assert.deepEqual(types(windowed), ["warn", "closed"]);
    assert.deepEqual(windowed[1], { type: "closed", id: "TESTHOST:" + V, hostDrop: true });
    assert.deepEqual(types(got), ["tabOrder", "feed"], "the detach's merged re-emits reached the handler directly (no lanes were held for the host)");
    assert.ok(!got.some((m) => m.type === "closed" || m.type === "warn"), "neither window-path frame reached the handler");
  });
});

test("the registration returns an unsubscribe; after it the window path carries the frames again", () => {
  withManager((fm, windowed) => {
    const got: any[] = [];
    const off = fm.onFrame((e: MessageEvent) => got.push(e.data));
    fm.inbound("", { type: "tabOrder", order: [], tabs: [] });
    off();
    fm.inbound("", { type: "tabOrder", order: [], tabs: [] });
    assert.equal(got.length, 1);
    assert.deepEqual(types(windowed), ["tabOrder"]);
  });
});

test("two handlers both receive the ONE event object, in registration order; a handler unsubscribed mid-delivery still runs once", () => {
  withManager((fm, windowed) => {
    const order: string[] = [];
    const events: MessageEvent[] = [];
    let offSecond = () => {};
    fm.onFrame((e: MessageEvent) => { order.push("first"); events.push(e); offSecond(); });   // unsubscribes its sibling while delivering
    offSecond = fm.onFrame((e: MessageEvent) => { order.push("second"); events.push(e); });
    fm.inbound("", { type: "tabOrder", order: [], tabs: [] });
    assert.deepEqual(order, ["first", "second"], "the snapshot taken before delivery still reaches the second handler once (deliberately unlike dispatchEvent, which skips a listener removed during dispatch)");
    assert.equal(events[0], events[1], "one MessageEvent for the frame");
    fm.inbound("", { type: "tabOrder", order: [], tabs: [] });
    assert.deepEqual(order, ["first", "second", "first"], "…and the unsubscribe holds from the next frame");
    assert.deepEqual(types(windowed), []);
  });
});

// ── report-and-continue: a throwing handler behaves as a throwing window listener does ──

test("a throwing handler is reported through reportError alone (not the console as well), the next handler still runs, and inbound returns normally", () => {
  withManager((fm, windowed, reported) => {
    const realError = console.error;
    const logged: unknown[] = [];
    console.error = (...args: unknown[]) => { logged.push(args[0]); };
    try {
      const got: string[] = [];
      fm.onFrame(() => { throw new Error("pane bug"); });
      fm.onFrame((e: MessageEvent) => got.push(e.data.type));
      assert.doesNotThrow(() => fm.inbound("", { type: "tabOrder", order: [], tabs: [] }));
      assert.deepEqual(got, ["tabOrder"]);
      assert.equal(reported.length, 1);
      assert.match(String((reported[0] as Error).message), /pane bug/);
      assert.deepEqual(logged, [], "with reportError on the page the console is not written too: one report per throw");
      assert.deepEqual(types(windowed), []);
    } finally {
      console.error = realError;
    }
  });
});

test("a throw on a detach's lanes emission still lets its bars emission run", () => {
  withManager((fm, windowed, reported) => {
    const got: string[] = [];
    fm.onFrame((e: MessageEvent) => { got.push(e.data.type); if (e.data.type === "data") throw new Error("lanes bug"); });
    fm.inbound("", laneData([U]));
    fm.inbound("", { type: "bars", turns: [] });
    fm.inbound("TESTHOST", laneData([V]));
    fm.inbound("TESTHOST", { type: "bars", turns: [] });
    assert.deepEqual(got, ["data", "bars", "data", "bars"]);
    assert.equal(reported.length, 2, "each lanes emission threw and was reported");
    // the detach (poll: /tunnels no longer lists the host) — closeRemote re-emits order, feed, lanes, bars in that order
    fm.conns.set("TESTHOST", { host: "TESTHOST", ws: null, url: "", closed: false, live: false, lastRecv: 0, connT: 0, pending: new Map() });
    got.length = 0;
    fm.closeRemote("TESTHOST");
    assert.deepEqual(got, ["tabOrder", "feed", "data", "bars"], "the bars emission ran after the throwing lanes emission");
    assert.equal(reported.length, 3);
    assert.deepEqual(got.filter((t) => t === "bars").length, 1);
    assert.deepEqual(types(windowed), []);
  });
});

test("reportListenerError falls back to console.error when the page has no reportError, and never throws", () => {
  const g: any = globalThis;
  const hadRE = "reportError" in g, prevRE = g.reportError;
  const realError = console.error;
  const logged: unknown[] = [];
  delete g.reportError;
  console.error = (...args: unknown[]) => { logged.push(args[0]); };
  try {
    assert.doesNotThrow(() => reportListenerError(new Error("no reportError here")));
    assert.equal(logged.length, 1);
    assert.match(String((logged[0] as Error).message), /no reportError here/);
    // a reportError that itself throws is not the pane's problem either
    g.reportError = () => { throw new Error("broken reporter"); };
    assert.doesNotThrow(() => reportListenerError(new Error("x")));
    assert.equal(logged.length, 2, "…the console got it instead");
  } finally {
    console.error = realError;
    if (hadRE) g.reportError = prevRE; else delete g.reportError;
  }
});

// ── the storage re-emit (a drag in another pane) through start() ──

test("a view-order storage event re-emits all three merged frames to the registered handler, once each, and none on window", () => {
  const g: any = globalThis;
  const saved: Record<string, [boolean, unknown]> = {};
  for (const k of ["window", "document", "localStorage", "setInterval", "fetch"]) saved[k] = [k in g, g[k]];
  const win: any = new EventTarget();
  const onWindow: any[] = [];
  win.addEventListener("message", (e: any) => onWindow.push(e.data));   // what an unregistered listener would see
  win.__rompApp = "feed";
  const store = new Map<string, string>();
  g.window = win;
  g.document = Object.assign(new EventTarget(), { visibilityState: "visible" });
  g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  g.setInterval = () => 0;                                   // start()'s poll and watchdog timers: never armed here
  g.fetch = () => Promise.reject(new Error("no kernel"));    // start()'s first /tunnels poll: returns quietly
  try {
    const fm: any = new FederationManager();
    fm.start();
    assert.equal(typeof win.__rompFed.onFrame, "function", "the registration is published on the window slot");
    const got: any[] = [];
    const h = (e: MessageEvent) => got.push(e.data);
    listenForFrames(h);   // the pane's install: window AND the registry, one function
    fm.inbound("", { type: "tabOrder", order: [U], tabs: [{ id: U, name: "web" }] });
    fm.inbound("", feedFrame([ask("a1", U)], 1));
    fm.inbound("", laneData([U]));
    // (the first host report adopts U into the view order, and that write fires VIEW_ORDER_EVENT on this window, so the
    // reorder re-emit runs once before the fresh order — the same as on a real page; the counts below start after it)
    assert.deepEqual(types(got).slice(-3), ["tabOrder", "feed", "data"]);
    got.length = 0;
    win.dispatchEvent(new Event("storage"));   // another pane's drag (no key = the whole store; VIEW_ORDER_KEY otherwise)
    assert.deepEqual(types(got), ["tabOrder", "feed", "data"], "each merged frame re-emitted exactly once");
    assert.equal(got[0].reemit, true, "a synthetic re-emit, never a fresh host report");
    assert.deepEqual(onWindow, [], "nothing rode the window path");
  } finally {
    for (const k of Object.keys(saved)) { const [had, v] = saved[k]; if (had) g[k] = v; else delete g[k]; }
  }
});

// ── frame-listener.ts: the pane's install ──

test("listenForFrames installs the one handler on window and in the registry; without a registry, on window only; an older slot without onFrame does not throw", () => {
  const g: any = globalThis;
  const hadWindow = "window" in g, prevWindow = g.window;
  try {
    const win: any = new EventTarget();
    const subs: unknown[] = [];
    win.__rompFed = { onFrame: (h: unknown) => { subs.push(h); return () => {}; } };
    g.window = win;
    const seen: any[] = [];
    const h = (e: MessageEvent) => seen.push(e.data);
    assert.equal(listenForFrames(h), h);
    assert.deepEqual(subs, [h], "the SAME function is registered — the perf wrapper included, so the brackets nest on both paths");
    win.dispatchEvent(new MessageEvent("message", { data: { romp: "paneFocus" } }));
    assert.deepEqual(seen, [{ romp: "paneFocus" }], "…and it is on window for the shell's posts");
    // no federation.js on the page (a VS Code webview)
    const bare: any = new EventTarget();
    g.window = bare;
    assert.doesNotThrow(() => listenForFrames(h));
    // a federation.js from before the registry
    const older: any = new EventTarget();
    older.__rompFed = { inbound: () => {}, outbound: () => {} };
    g.window = older;
    assert.doesNotThrow(() => listenForFrames(h));
  } finally {
    if (hadWindow) g.window = prevWindow; else delete g.window;
  }
});

// ── source pins: every pane installs through the helper. (The kernel's inline timeline boot registers the same way;
// that is the Python lane's to hold (tests/test_kernel_timeline_split.py pins the registration and RUNS the boot
// under node), so a kernel edit cannot break this lane. Review find, 2026-09-08.) ──

test("feed, Outline, chat and the VS Code timeline install their frame handler through listenForFrames, none through a bare window listener", () => {
  for (const [file, app] of [["feed.ts", "feed"], ["fleet.ts", "fleet"], ["render.ts", "chat"], ["timeline-main.ts", "timeline"]]) {
    const src = fs.readFileSync(path.join(UI, file), "utf8");
    assert.ok(src.includes(`listenForFrames(perfFrameHandler("${app}", `), `${file}: installs through the helper`);
    assert.ok(!src.includes('window.addEventListener("message", perfFrameHandler('), `${file}: no bare window install of the frame handler`);
    assert.match(src, /import \{ listenForFrames(?:, \w+)* \} from "\.\/frame-listener";/, `${file}: imports the helper`);   // the chat also imports the manager-missing check (2026-09-10)
  }
  const helper = fs.readFileSync(path.join(UI, "frame-listener.ts"), "utf8");
  assert.doesNotMatch(helper, /^import /m, "the helper stays import-free: importing federation.ts would boot a second manager in the pane bundle");
  assert.ok(helper.indexOf('window.addEventListener("message", handler)') < helper.indexOf("fed.onFrame(handler)"), "window first, the registry after");
});

test("the three merged emissions go through emit and no other dispatch does", () => {
  const src = fs.readFileSync(path.join(UI, "federation.ts"), "utf8");
  assert.equal((src.match(/this\.emit\(/g) || []).length, 3, "emitMergedFeed, emitMergedTimeline, emitMergedOrder");
  for (const stays of ['window.dispatchEvent(new MessageEvent("message", { data: m }));',                       // closed, passthrough
                       'window.dispatchEvent(new MessageEvent("message", { data: { type: "hostUp", hosts: recovered } }));',
                       'window.dispatchEvent(new MessageEvent("message", { data: { type: "closed", id: sid, hostDrop: true } }));',
                       'window.dispatchEvent(new Event("romp-hosts"));']) {
    assert.ok(src.includes(stays), "stays on window: " + stays);
  }
  assert.ok(src.includes('onFrame: (h: FrameHandler) => this.onFrame(h)'), "published on window.__rompFed");
});
