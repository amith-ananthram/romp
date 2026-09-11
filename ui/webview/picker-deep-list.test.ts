// The + picker's 30-day list. The user 2026-07-24: typing a session's name into the picker found nothing
// unless it had been touched in the last 48h (discover()'s CAPTION horizon, which the picker had inherited
// by accident), so an older session could not be reopened at all and the only way forward was to start a
// new one. The picker now gets the last 30 days, in ONE reply when it opens — it was briefly a lazy
// two-step fetch, but measuring said the wide walk is ~78ms cold once fork detection is off, so paging it
// in bought nothing and cost a spinner. Source-level pins (no jsdom for the renderer).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const JUDGE = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "judge.py"), "utf8");

test("opening the picker asks for the whole list once — no lazy second fetch to wait on", () => {
  assert.match(RENDER, /postMessage\(\{ type: "requestSessions", host \}\)/);   // host-addressed since 2026-07-29
  // the lazy machinery is gone: no deep flag, no pending state, no scroll trigger, no loader to strand
  assert.doesNotMatch(RENDER, /requestSessions", deep: true/);
  assert.doesNotMatch(RENDER, /pickerDeep/);
  assert.doesNotMatch(RENDER, /loading older sessions/);
});

test("the kernel serves the picker 30 days, with forks off so it stays cheap", () => {
  assert.match(KERNEL, /PICKER_WINDOW = 30 \* 86400/);
  assert.match(KERNEL, /_sessions\(now, PICKER_WINDOW if window is None else window, forks=False\)\[:PICKER_CAP\]/);
  assert.match(KERNEL, /"items": _session_list\(int\(time\.time\(\)\), _live_map\(\)\)/);
});

test("discover takes a window and a forks switch, cached per pair", () => {
  assert.match(JUDGE, /def discover\(now, window=None, forks=True\):/);
  assert.match(JUDGE, /key = \(win, bool\(forks\)\)/);
  assert.match(JUDGE, /_discover_cache\.get\(key\)/);
  // forks=False short-circuits before the same-customTitle scan, which is where the title reads were spent
  assert.match(JUDGE, /if not name or not forks:/);
});

test("the list foot states the reach, so a missing older session has a stated reason", () => {
  assert.match(RENDER, /more\.textContent = "showing the last 30 days";/);
  assert.match(CSS, /\.picker-more \{/);
  // deliberately NOT a .picker-row, so filterPicker and the keyboard walk skip it
  assert.doesNotMatch(RENDER, /el\("div", "picker-row picker-more"\)/);
});

test("a re-render keeps the scroll position of a user who scrolled back", () => {
  assert.match(RENDER, /const keepScroll = list\.scrollTop;/);
  assert.match(RENDER, /list\.scrollTop = keepScroll;/);
});
