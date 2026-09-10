// shell-perf.ts: the dashboard shell's performance collector. The shell is the top-level window that frames
// the panes; it receives no frames, but Chromium reports a long animation frame to the top-level document and
// never to the iframe whose script ran it, so the shell runs the panes' collector (perf-telemetry.ts) under app
// "shell" and posts its minute row through its own kernel socket (window.__rompShellSend). Stood in the way the
// perf-telemetry install tests stand a window in: a fake window with performance.now, a setInterval that hands
// back the minute timer, and a PerformanceObserver whose callback the test drives; the module is imported once
// the fakes are in place, since its boot line installs on import. Synthetic URLs and ids only: host h, the
// placeholder session id.
import { test, after } from "node:test";
import * as assert from "node:assert/strict";

const SID = "11111111-2222-3333-4444-555555555555";
const g: any = globalThis;
const hadWindow = "window" in g, prevWindow = g.window;
const hadDocument = "document" in g, prevDocument = g.document;

let now = 5000;
let observerCb: ((list: { getEntries(): any[] }) => void) | null = null;
let observed: any = null;
let minuteTimer: (() => void) | null = null;   // the collector's flush, captured from the fake window's setInterval
let minuteMs = 0;
const sent: any[] = [];
let socketOpen = true;

const win: any = new EventTarget();
win.performance = { now: () => now, memory: { usedJSHeapSize: 96 * 1048576 } };
win.navigator = { userAgent: "Mozilla/5.0 (Macintosh) Chrome/128.0.0.0 Safari/537.36", maxTouchPoints: 0 };
win.location = { href: "http://h:1/?token=abc" };   // the shell page: its URL, token and all, is the sourceURL of every inline shell script
win.parent = win;                                    // the top-level window
win.innerWidth = 1200; win.innerHeight = 800;
win.setInterval = (cb: () => void, ms: number) => { minuteTimer = cb; minuteMs = ms; return { unref() {} }; };
win.PerformanceObserver = class {
  static supportedEntryTypes = ["long-animation-frame", "longtask"];
  constructor(cb: (list: { getEntries(): any[] }) => void) { observerCb = cb; }
  observe(opts: any): void { observed = opts; }
  disconnect(): void {}
};
const doc: any = new EventTarget();
doc.visibilityState = "visible";
doc.getElementsByTagName = () => ({ length: 900 });
g.window = win;
g.document = doc;
after(() => {
  if (hadWindow) g.window = prevWindow; else delete g.window;
  if (hadDocument) g.document = prevDocument; else delete g.document;
});

/** The browser reports one long-animation-frame entry. */
const report = (entry: any) => { assert.ok(observerCb, "the observer is installed"); observerCb!({ getEntries: () => [entry] }); };
const longFrame = (ms: number, scripts: any[]) => ({ startTime: now, duration: ms, blockingDuration: Math.max(0, ms - 50), scripts });
/** The minute timer fires. */
const minutePasses = () => { assert.ok(minuteTimer, "the minute timer is armed"); minuteTimer!(); };
const paneScript = (file: string, fn: string, pos: number, invoker: string, duration: number) =>
  ({ sourceURL: "http://h:1/dist/" + file + "?v=1757100000", sourceFunctionName: fn, sourceCharPosition: pos, invoker, duration });

test("importing the bundle installs the panes' collector on the shell window as app shell, observing long-animation-frame entries on a minute timer, before the socket script has run; an idle minute posts nothing", async () => {
  assert.equal(win.__rompShellSend, undefined, "the socket script has not run when the bundle loads");
  const mod = await import("./shell-perf");
  assert.equal(win.__rompShellSend, undefined, "the bundle defines no send of its own");
  assert.equal(mod.SHELL_APP, "shell");
  const p = win.__rompPerf;
  assert.ok(p && typeof p.tick === "function", "published as window.__rompPerf like every pane's");
  assert.equal(p.snapshot().app, "shell");
  assert.equal(p.snapshot().observer, "loaf", "long-animation-frame, with attribution");
  assert.deepEqual(observed, { type: "long-animation-frame", buffered: false });
  assert.equal(minuteMs, 60_000);
  // the socket script runs after the bundles on the page, so the send exists only now
  win.__rompShellSend = (m: any) => { if (!socketOpen) return false; sent.push(m); return true; };
  minutePasses();
  assert.equal(sent.length, 0, "no frame and no long frame: nothing to say");
  assert.deepEqual(Object.keys(p.snapshot().frames), [], "the shell brackets nothing");
});

test("a long frame the browser attributes to a pane's script becomes one scrubbed minute row through window.__rompShellSend, read at call time (it did not exist when the collector installed): script basenames without query, page: for the shell's own inline script, invokers without element ids or URLs; no string in the row carries a slash, a query or a session id", () => {
  const p = win.__rompPerf;
  report(longFrame(20_000, [
    // a pane's bundle, as Chromium names it: the full URL with its dist token, run from the pane's animation frame
    paneScript("chat.js", "paintAll", 9000, "Window.requestAnimationFrame", 19_500),
    // the pane's inline shim, whose sourceURL is the pane page with its token and the session it shows
    { sourceURL: "http://h:1/chat?token=abc&sid=" + SID, sourceFunctionName: "", sourceCharPosition: 4000, invoker: "WebSocket.onmessage", duration: 300 },
    // the shell's own inline script (a divider drag): its sourceURL is the shell page itself, and the element id names a session
    { sourceURL: "http://h:1/?token=abc", sourceFunctionName: "onMove", sourceCharPosition: 120, invoker: "DIV#pane-divider-" + SID + ".onpointermove", duration: 150 },
    // an image the chat pane loaded, its source a file path with the session
    paneScript("render.js", "", 77, "IMG[src=/file?path=/repo/notes-api/docs/plot.png&sid=" + SID + "].onload", 50),
  ]));
  report({ startTime: now + 100, duration: 30, scripts: [paneScript("feed.js", "x", 1, "WebSocket.onmessage", 30)] });   // under 50 ms: not a long frame
  minutePasses();
  assert.equal(sent.length, 1, "one row for the minute");
  const row = sent[0];
  assert.equal(row.type, "clientDiag"); assert.equal(row.surface, "perf"); assert.equal(row.what, "minute");
  const d = row.data;
  assert.equal(d.app, "shell");
  assert.deepEqual(d.frames, {}, "no frame types: the shell receives no frames");
  assert.equal(d.free, null, "no frame, no main-thread-free sample");
  assert.deepEqual({ n: d.loaf.n, worst_ms: d.loaf.worst_ms, blocking_ms: d.loaf.blocking_ms, src: d.loaf.src }, { n: 1, worst_ms: 20_000, blocking_ms: 19_950, src: "loaf" });
  assert.deepEqual(d.loaf.top, [
    { k: "chat.js:paintAll@9000", ms: 19_500, n: 1, inv: "Window.requestAnimationFrame" },
    { k: "chat:(anonymous)@4000", ms: 300, n: 1, inv: "WebSocket.onmessage" },
    { k: "page:onMove@120", ms: 150, n: 1, inv: "DIV.onpointermove" },
    { k: "render.js:(anonymous)@77", ms: 50, n: 1, inv: "IMG[src].onload" },
  ]);
  assert.equal(d.heap_mb, 96);
  assert.equal(d.dom, 900);
  assert.equal(d.ua, "chrome-desktop");
  assert.equal(d.visible, true);
  assert.equal(d.hidden_pane, false, "the top-level window is never a hidden pane");
  // the privacy contract over every string in the row, keys included: the shell page's URL carries the token
  const strings: string[] = [];
  (function walk(v: unknown, at: string) {
    if (typeof v === "string") strings.push(at + "=" + v);
    else if (Array.isArray(v)) v.forEach((x, i) => walk(x, at + "[" + i + "]"));
    else if (v && typeof v === "object") for (const k of Object.keys(v as object)) { strings.push(at + "." + k); walk((v as any)[k], at + "." + k); }
  })(row, "row");
  assert.ok(strings.length > 10);
  for (const s of strings) {
    assert.ok(!s.includes("/"), "no slash: " + s);
    assert.ok(!s.includes("?") && !s.includes("&") && !s.includes("token"), "no query: " + s);
    assert.ok(!s.includes(SID) && !s.includes("11111111"), "no session id: " + s);
    assert.ok(!s.includes("notes-api") && !s.includes("plot.png") && !s.includes("h:1"), "no path or host: " + s);
  }
});

test("a row the socket refuses is held and goes ahead of the next one that finds it open, in order; nothing is sent while it stays closed", () => {
  sent.length = 0;
  socketOpen = false;
  report(longFrame(120, [paneScript("feed.js", "render", 1, "WebSocket.onmessage", 120)]));
  minutePasses();
  assert.equal(sent.length, 0, "closed: held");
  report(longFrame(130, [paneScript("feed.js", "render", 1, "WebSocket.onmessage", 130)]));
  minutePasses();
  assert.equal(sent.length, 0, "still closed: both held");
  socketOpen = true;
  report(longFrame(140, [paneScript("feed.js", "render", 1, "WebSocket.onmessage", 140)]));
  minutePasses();
  assert.deepEqual(sent.map((m) => m.data.loaf.worst_ms), [120, 130, 140], "the held rows first, then the new one");
});

test("shellPost alone: no send yet (the socket script has not run) holds too; past HELD_MAX the oldest held row goes; a send that fails mid-drain keeps the rest in order", async () => {
  const { shellPost, HELD_MAX } = await import("./shell-perf");
  let send: ((m: any) => boolean) | undefined;
  const out: any[] = [];
  const post = shellPost(() => send);
  for (let i = 0; i < HELD_MAX + 3; i++) post({ i });
  send = (m) => { out.push(m); return true; };
  post({ i: "last" });
  assert.equal(out.length, HELD_MAX + 1);
  assert.deepEqual(out.map((m) => m.i), [...Array.from({ length: HELD_MAX }, (_, k) => k + 3), "last"], "the three oldest went; the rest in arrival order");
  // the socket closes again mid-drain: what went stays sent, the rest stay held in order, the new row behind them
  out.length = 0;
  send = () => false;
  post({ i: "a" }); post({ i: "b" }); post({ i: "c" });
  let calls = 0;
  send = (m) => { calls++; if (calls === 2) return false; out.push(m); return true; };
  post({ i: "d" });
  assert.deepEqual(out.map((m) => m.i), ["a"], "a went; b failed and stopped the drain");
  send = (m) => { out.push(m); return true; };
  post({ i: "e" });
  assert.deepEqual(out.map((m) => m.i), ["a", "b", "c", "d", "e"], "the rest followed in order once the socket answered");
  // a send that throws counts as refused: the row is held, never lost, and nothing reaches the page
  send = () => { throw new Error("socket gone"); };
  post({ i: "f" });
  send = (m) => { out.push(m); return true; };
  post({ i: "g" });
  assert.deepEqual(out.map((m) => m.i), ["a", "b", "c", "d", "e", "f", "g"]);
});

test("a browser that reports neither long animation frames nor long tasks: the collector installs with no observer, and a minute passes with nothing to post", async () => {
  // last, because it replaces the window's collector: the install is once per window, so a fresh one needs the slot empty
  const { installShellPerf } = await import("./shell-perf");
  sent.length = 0;
  delete win.__rompPerf;
  win.PerformanceObserver.supportedEntryTypes = [];
  observed = null;
  const p: any = installShellPerf();
  assert.ok(p, "installed: heap and DOM are still measurable");
  assert.equal(win.__rompPerf, p);
  assert.equal(p.snapshot().observer, "none");
  assert.equal(observed, null, "nothing observed");
  minutePasses();
  assert.equal(sent.length, 0, "no long frames to report and no frames: the shell has nothing to say there");
});
