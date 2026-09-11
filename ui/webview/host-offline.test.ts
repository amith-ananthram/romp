// A DISCONNECTED remote's sessions must SAY so (the user 2026-07-29, who read a remote host's
// transcripts for a while before realising nothing was connected). Its tabs, lanes and cards stay —
// dropping them would lose the thread — but the "host:" token is struck and the tab dims.
//
// It must say so WITHOUT taking the screen to do it (the user 2026-07-29, again): the first version put
// a banner across the top of the pane, which landed on the session tab strip and hid the sessions. The
// drop now flashes the rail's network glyph red three times — an event's cue for an event — and the
// steady state stays on the tab and the glyph's own colour. hostIsDown/hostDownNote are executed here
// against a stub manager; the per-surface wiring is pinned at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { hostIsDown, hostDownNote } from "./host-prefix";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const RENDER = read("render.ts");
const FED = read("federation.ts");
const CSS = read("styles.css");
const FEEDCSS = read("feed.css");
const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
// the rail (and so the drop cue) lives in the shell the kernel serves, not in the pane bundle
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

const withFed = (fed: any, fn: () => void) => {
  const g = globalThis as any;
  const prev = g.__rompFed;
  g.__rompFed = fed;
  try { fn(); } finally { if (prev === undefined) delete g.__rompFed; else g.__rompFed = prev; }
};

test("a session on an unreachable host is marked; one on a reachable host is not", () => {
  withFed({ down: () => ["TESTHOST"], lastSeen: () => 0 }, () => {
    assert.equal(hostIsDown("TESTHOST:1111-2222"), true);
    assert.equal(hostIsDown("otherhost:1111-2222"), false);
  });
});

test("a LOCAL session is never marked — a bare uuid has no host to lose", () => {
  withFed({ down: () => ["TESTHOST"], lastSeen: () => 0 }, () => {
    assert.equal(hostIsDown("11111111-2222-3333-4444-555555555555"), false);
    assert.equal(hostIsDown(""), false);
    assert.equal(hostIsDown(null), false);
  });
});

test("no federation manager (single-kernel page, Obsidian panel) means nothing is marked", () => {
  const g = globalThis as any;
  const prev = g.__rompFed;
  delete g.__rompFed;
  try {
    assert.equal(hostIsDown("TESTHOST:1111"), false);
  } finally { if (prev !== undefined) g.__rompFed = prev; }
  // ...and a manager too old to publish the set is not an error either
  withFed({ hosts: () => [] }, () => assert.equal(hostIsDown("TESTHOST:1111"), false));
});

test("the note says what is wrong, when it was last reached, and that romp is still on it", () => {
  withFed({ down: () => ["TESTHOST"], lastSeen: () => 1_770_000_000 }, () => {
    const note = hostDownNote("TESTHOST:1111");
    assert.match(note, /^TESTHOST is disconnected, last reached /);
    assert.match(note, /last state romp got from it/);
    assert.match(note, /still trying to reconnect/);
  });
  // never reached in this kernel's life → no time claimed, rather than a fake one
  withFed({ down: () => ["TESTHOST"], lastSeen: () => 0 }, () => {
    assert.equal(hostDownNote("TESTHOST:1111").indexOf("last reached"), -1);
  });
  assert.equal(hostDownNote("11111111-2222"), "", "a local session has no such note");
});

test("the manager publishes reachability off the KERNEL's tunnel health, and only on a change", () => {
  assert.match(FED, /down: \(\) => \[\.\.\.this\.downHosts\]/);
  assert.match(FED, /const down = new Set\(\[\.\.\.want\.keys\(\)\]\.filter\(\(h\) => want\.get\(h\)\.status !== "up"\)\)/);
  assert.match(FED, /if \(changed\) window\.dispatchEvent\(new Event\("romp-hosts"\)\)/,
    "a repaint per connect/drop, not per poll");
  assert.match(FED, /if \(typeof t\.lastOk === "number" && t\.lastOk\) this\.lastSeen\[host\] = t\.lastOk/,
    "last-seen comes from the kernel, so it survives a page reload");
});

test("the mark goes on the HOST token, never on the session name", () => {
  // a struck WHOLE name already means a dead session; this session may be perfectly alive
  assert.match(read("host-prefix.ts"), /h\.className = off \? "host-prefix off" : "host-prefix"/);
  // (2026-07-30: the cue became the STALE treatment — dim italic, dotted underline — because
  // strikethrough claimed the session was gone when it is the LINK that is down. See host-offline-ux.)
  assert.match(CSS, /\.host-prefix\.off \{[\s\S]*?text-decoration: underline dotted 1px/);
  assert.match(FEEDCSS, /\.host-prefix\.off \{[\s\S]*?text-decoration: underline dotted 1px/,
    "the feed page loads only feed.css");
});

test("the tab dims as a whole and carries the why on hover", () => {
  assert.match(RENDER, /if \(hostIsDown\(id\)\) \{ tab\.classList\.add\("host-off"\); tab\.title = hostDownNote\(id\); \}/);
  assert.match(CSS, /\.tab\.host-off \{ opacity: 0\.62; \}/);
  assert.match(CSS, /\.tab\.host-off:hover, \.tab\.host-off\.active \{ opacity: 1; \}/, "hover still reads clearly");
});

test("no banner covers the pane — a drop flashes the rail's network glyph three times instead", () => {
  assert.equal(RENDER.indexOf("hostBanner"), -1, "removed from the pane, not merely hidden");
  assert.equal(RENDER.indexOf("rhostoff"), -1);
  assert.doesNotMatch(CSS, /^#rhostoff\s*\{/m, "and its style went with it (the note recording why stays)");
  assert.match(KERNEL, /function dropCue\(ts\)\{/);
  assert.match(KERNEL, /if\(_wasUp\[t\.host\]&&!up\)fell=true;/,
    "it fires on the up -> not-up TRANSITION; a steady down state must not re-flash every poll");
  assert.match(KERNEL, /animation:rnet-drop 0\.42s ease-in-out 3\}/, "three times, then it stops");
  assert.match(KERNEL, /dropCue\(ts\);/, "wired into the same /tunnels poll that paints the glyph");
});

test("a page opened on an already-down fleet does not flash — nothing dropped while you watched", () => {
  // _wasUp starts empty, so a host never seen up cannot fall. The steady state is carried by the glyph's
  // colour and the dimmed tab; a cue that fired on load would be the banner again, with extra steps.
  assert.match(KERNEL, /var _wasUp=\{\};/);
  assert.match(KERNEL, /_wasUp=seen;/, "replaced each poll, so a detached host cannot linger in it");
});

test("the flash rides background and ring, never colour, so it composes with the fleet state", () => {
  // the glyph already wears accent / grey / red for fleet health — animating `color` would fight it
  assert.match(KERNEL, /@keyframes rnet-drop\{0%,100%\{background:transparent;box-shadow:none\}/);
  assert.match(KERNEL, /el\.classList\.remove\('rn-drop'\);void el\.offsetWidth;/, "a second drop replays it");
});

test("the note the tab carries on hover is still the one wording of it", () => {
  // the banner is gone, but hostDownNote is not: the tab's title is where that sentence lives now
  assert.match(RENDER, /tab\.title = hostDownNote\(id\)/);
});

test("both surfaces repaint on the reachability event", () => {
  assert.match(RENDER, /window\.addEventListener\("romp-hosts", \(\) => \{ renderTabs\(\); syncComposerPh\(\); \}\)/, "the strip and the composer's name overlay (its host span wears the same mark, T328) repaint together");
  assert.match(TL, /window\.addEventListener\('romp-hosts', this\._onHosts\)/);
  assert.match(TL, /window\.removeEventListener\('romp-hosts', this\._onHosts\)/, "and let go on teardown");
});

test("the timeline strikes the lane's host token, keeping the dead-session strike distinct", () => {
  assert.match(TL, /function _rompHostDown\(sid\)/);
  assert.match(TL, /if \(_rompHostDown\(s\.id\)\) hostTsp\.setAttribute\('text-decoration', 'line-through'\)/);
  // the dead-lane strike is the WHOLE label and stays that way
  assert.match(TL, /if \(!s\.live\) lblA\['text-decoration'\] = 'line-through';/);
});
