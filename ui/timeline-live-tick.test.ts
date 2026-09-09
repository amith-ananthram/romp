// The timeline's redraw budget on a many-session dashboard (2026-09-04). Measured with a headless browser
// replaying a real seventeen-session board: the live-follow loop woke every animation frame and forced a
// layout each time (about 40% of the shared main thread on an IDLE dashboard), rebuilt the whole SVG once
// the edge had crept 0.15 px, and inside each rebuild compared every turn against every message. The chat pane's tab clicks share
// that thread, so every one of these landed on the user as click lag. Like the other timeline tests,
// the wiring is pinned at the source level, and the pure helpers are run. The look is a translate now
// (timeline-transform-tick.test.ts executes it): a look with a plot group sleeps only until the edge could have moved
// TICK_MIN_PX (_tickWaitMs, within a frame at a narrow window), and the look that must rebuild keeps the whole pixel.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("the rebuild look sleeps until the edge has moved a whole pixel; a translate look sleeps until it has moved TICK_MIN_PX", () => {
  assert.match(SRC, /const LIVE_MIN_PX = 1;/);
  assert.match(SRC, /const TICK_MIN_PX = 0.15;/);
  assert.match(SRC, /_liveWaitMs\(\) \{[\s\S]*?const pxPerSec = g\.plotW \/ g\.winSec;/);
  assert.match(SRC, /_sleep\(ms\) \{\n\s*this\._liveTO = setTimeout\(\(\) => \{ this\._liveTO = null; this\._liveRAF = requestAnimationFrame\(\(\) => this\._tickLive\(\)\); \}, ms\);/);
  assert.match(SRC, /this\._sleep\(this\._tickPlot \? this\._tickWaitMs\(\) : this\._liveWaitMs\(\)\);/, "a plot group to translate: TICK_MIN_PX's worth; none: the whole pixel's");
  assert.match(SRC, /_stopLiveTick\(\) \{[\s\S]*?clearTimeout\(this\._liveTO\)/, "stopping the loop clears the sleep too");
  assert.match(SRC, /_startLiveTick\(\) \{[\s\S]*?if \(this\._liveRAF != null\) return;[\s\S]*?if \(this\._liveTO != null\) \{ clearTimeout\(this\._liveTO\); this\._liveTO = null; \}/,
    "a restart re-paces a pending sleep (a zoom or a frame changed the geometry) but never doubles an imminent look");
});

test("the live wait is bounded and scales with the zoom", () => {
  const m = /  _liveWaitMs\(\) \{([\s\S]*?)\n  \}/.exec(SRC);
  assert.ok(m, "the pacing helper exists");
  const LIVE_MIN_PX = 1;
  const fn = new Function("LIVE_MIN_PX", "return function(){" + m![1] + "}")(LIVE_MIN_PX) as () => number;
  const at = (winSec: number, plotW: number) => fn.call({ _geom: { winSec, plotW } });
  assert.equal(at(3600, 450), 2000, "a one-hour window over 450 px: capped at two seconds between looks");
  assert.equal(at(600, 900), 667, "a ten-minute window over 900 px: two thirds of a second");
  assert.equal(at(60, 1800), 100, "zoomed right in: never faster than ten looks a second");
  assert.equal(fn.call({ _geom: null }), 1000, "no geometry yet: a plain second");
});

test("the translate's wait is TICK_MIN_PX's worth: within a frame at a narrow window, a short sleep at a wide one, the rebuild's inside a trailing gap", () => {
  const m = /  _tickWaitMs\(\) \{([\s\S]*?)\n  \}/.exec(SRC);
  assert.ok(m, "the translate's pacing helper exists");
  const TICK_MIN_PX = 0.15, MAX_INTERP_AHEAD = 150, NOW_MS = 1_000_000;
  const fn = new Function("TICK_MIN_PX", "MAX_INTERP_AHEAD", "perfNow", "return function(){" + m![1] + "}")(TICK_MIN_PX, MAX_INTERP_AHEAD, () => NOW_MS) as () => number;
  const at = (winSec: number, plotW: number, trailing = false, sinceFrameMs = 0) =>
    fn.call({ _geom: { winSec, plotW }, _tickPlot: { trailing }, _nowBaseMs: NOW_MS - sinceFrameMs, _liveWaitMs: () => 777 });
  assert.equal(at(60, 1300), 7, "a one-minute window over 1300 px: 7 ms, under a frame, so the next animation frame");
  assert.equal(at(600, 1300), 69, "a ten-minute window: about 70 ms, a dozen moves a second");
  assert.equal(at(3600, 1300), 415, "a one-hour window: about twice a second");
  assert.equal(at(43200, 450), 2000, "zoomed right out: capped at two seconds, like the rebuild's wait");
  assert.equal(at(600, 1300, true), 777, "inside a collapsed trailing gap the edge does not move: the rebuild's wait");
  assert.equal(at(600, 1300, false, 149_000), 69, "a kernel quiet for 149 s: the edge still glides toward the cap");
  assert.equal(at(600, 1300, false, 150_000), 777, "at the interpolation cap (150 s) the edge stands still: the rebuild's wait");
  assert.equal(at(60, 1300, false, 400_000), 777, "and stays so however narrow the window, until the next frame re-anchors the clock");
  assert.equal(fn.call({ _geom: null, _tickPlot: null, _liveWaitMs: () => 777 }), 777, "no geometry yet: the rebuild's wait");
});

// (A skeleton and its bars frame are two draws: update()/applyBars() draw synchronously while the pane can be
// seen, which the view's tests rely on — out of sight they hold to one catch-up, timeline-hidden-hold.test.ts.
// The kernel stops re-sending an unchanged skeleton instead — tests/test_timeline_skeleton_dedup.py — so in
// steady state only the bars frame lands, and only when it changed.)

test("the prompt-dot pass indexes processed messages by recipient instead of scanning them per turn", () => {
  assert.doesNotMatch(SRC, /data\.messages\.some\(\(mm\) => mm\.toId === s\.id && !mm\.pending/);
  assert.match(SRC, /const execByTo = new Map\(\);[\s\S]*?execByTo\.forEach\(\(a\) => a\.sort\(\(p, q\) => p - q\)\);[\s\S]*?const execNear = \(sid, t\) => sortedHasWithin\(execByTo\.get\(sid\), t, 1\);/);
  assert.match(SRC, /if \(execNear\(s\.id, startAt\(t\)\)\) return;/);
});

test("sortedHasWithin answers the ±1 s question exactly", () => {
  const m = /function sortedHasWithin\(arr, t, tol\) \{[\s\S]*?\n\}/.exec(SRC);
  assert.ok(m);
  const f = new Function(m![0] + "; return sortedHasWithin;")() as (a: number[] | undefined, t: number, tol: number) => boolean;
  assert.equal(f(undefined, 5, 1), false);
  assert.equal(f([], 5, 1), false);
  assert.equal(f([1, 4, 9], 5, 1), true, "4 is within one second of 5");
  assert.equal(f([1, 4, 9], 6.5, 1), false, "nothing within one second of 6.5");
  assert.equal(f([1, 4, 9], 10, 1), true, "the last element counts");
  assert.equal(f([1, 4, 9], 0, 1), true, "the first element counts");
  assert.equal(f([1, 4, 9], -1.5, 1), false);
  assert.equal(f([2.9], 4.0, 1), false, "1.1 apart: outside");
  assert.equal(f([3.0], 4.0, 1), true, "exactly one apart: inside, as Math.abs(a-b) <= 1 was");
  // brute-force parity with the scan it replaces
  const xs = [0.5, 1.7, 1.9, 8, 8.2, 20, 33.3].sort((a, b) => a - b);
  for (let t = -2; t < 40; t += 0.37) assert.equal(f(xs, t, 1), xs.some((v) => Math.abs(v - t) <= 1), "t=" + t);
});

test("a hidden pane stops the loop (the paint hold's release re-arms it); a held pointer yields to the release event", () => {
  const tick = /  _tickLive\(\) \{([\s\S]*?)\n  \}/.exec(SRC)![1];
  // 2026-09-07: the old 2 s sleep re-entered _isVisible()'s forced layout every wake for a pane nobody could
  // see; the loop now stops and _releasePaintHold re-arms it on visibilitychange / the pane's observer.
  assert.match(tick, /if \(!this\._isVisible\(\)\) return;/);
  assert.doesNotMatch(tick, /_sleep\(2000\)/);
  assert.match(tick, /if \(this\._pointerHeld\) \{ this\._liveResume = true; return; \}/);
  assert.match(SRC, /if \(this\._liveResume\) \{ this\._liveResume = false; this\._startLiveTick\(\); \}/, "_release restarts it");
  assert.match(SRC, /this\._liveRAF = null; this\._liveTO = null; this\._liveResume = false;/, "the constructor knows every handle");
});

test("a bars frame re-anchors the live edge, and the glide cap outlasts the kernel's 60 s repost", () => {
  const bars = /  _mergeBars\(m\) \{([\s\S]*?)\n  \}/.exec(SRC)![1];   // the state half of applyBars (2026-09-07)
  assert.match(bars, /this\._anchorNow\(this\.data\.now\);/);
  assert.match(SRC, /_anchorNow\(sample\) \{[\s\S]*?reanchorEdge\(this\._nowBaseSec, this\._nowBaseMs, tMs, sample, this\._wasLive\)/);
  const cap = Number(/const MAX_INTERP_AHEAD = (\d+);/.exec(SRC)![1]);
  assert.ok(cap >= 120, "the edge must be able to glide through a whole repost interval: got " + cap);
  // the pure functions, extracted and run: a 61 s silence neither stalls the edge nor makes it jump
  const consts = "const MAX_INTERP_AHEAD = " + cap + "; const REANCHOR_SEC = " + /const REANCHOR_SEC = ([\d.]+);/.exec(SRC)![1] + ";";
  const fi = /function interpNow\([\s\S]*?\n\}/.exec(SRC)![0];
  const fr = /function reanchorEdge\([\s\S]*?\n\}/.exec(SRC)![0];
  const { interpNow, reanchorEdge } = new Function(consts + fi + fr + "; return { interpNow, reanchorEdge };")();
  assert.equal(interpNow(1000, 0, 61000, true, cap), 1061, "still gliding after 61 s");
  assert.equal(interpNow(1000, 0, 61000, true, 30), 1030, "…where the old 30 s cap had frozen it at +30");
  const a = reanchorEdge(1000, 0, 61000, 1061, true);
  assert.equal(a.baseSec, 1061, "the repost's sample matches the displayed edge: no jump");
});
