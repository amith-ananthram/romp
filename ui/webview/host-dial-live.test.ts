// The host-down notice at a disconnected remote's transcript tail ("<host> is disconnected — this is the
// last romp got from it. Reconnecting.") wears the romp swirl AFTER "Reconnecting", spinning only while
// a dial attempt to that host is actually in flight (the user 2026-09-10, who wanted it dynamic and honest:
// romp trying right now, never a spinner that spins whatever happens). The rule is pure over the relay
// socket's state federation publishes (hostDialLive: CONNECTING), the repaint is keyed on federation's
// dial events (romp:hostDial on the dial, the open and the close), never on a timer, and the sentence
// stays the one wording it had. The pure rule and its reader are executed here against a stub manager;
// the per-surface wiring is pinned at source, the way host-offline.test.ts pins the mark.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { hostDialLive, hostIsDialing } from "./host-prefix";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const RENDER = read("render.ts");
const FED = read("federation.ts");
const CSS = read("styles.css");

const withFed = (fed: any, fn: () => void) => {
  const g = globalThis as any;
  const prev = g.__rompFed;
  g.__rompFed = fed;
  try { fn(); } finally { if (prev === undefined) delete g.__rompFed; else g.__rompFed = prev; }
};

test("the notice is live while the kernel says it is dialing, or the relay socket is CONNECTING; still otherwise", () => {
  // the kernel's row: an ssh dial spawned and unconfirmed, or a health request in flight (kernel.py _row_dialing)
  assert.equal(hostDialLive(true, null), true, "the kernel is trying right now: a down host has no socket, and this is the case that matters");
  assert.equal(hostDialLive(false, null), false, "the kernel waits out its backoff: still");
  assert.equal(hostDialLive(undefined, null), false, "a kernel too old to publish the flag: never live on its account");
  // this page's own relay socket (the case the kernel reports up but the socket is down: a remote kernel restart)
  assert.equal(hostDialLive(false, 0), true, "WebSocket.CONNECTING: a dial attempt is in flight");
  assert.equal(hostDialLive(false, 1), false, "OPEN: connected, nothing to spin for");
  assert.equal(hostDialLive(false, 2), false, "CLOSING");
  assert.equal(hostDialLive(false, 3), false, "CLOSED: waiting between attempts — the swirl sits still");
  assert.equal(hostDialLive(false, undefined), false);
});

test("hostIsDialing reads the manager's published dial state for the sid's host, and nothing else", () => {
  withFed({ down: () => ["TESTHOST"], lastSeen: () => 0, dialing: (h: string) => h === "TESTHOST" }, () => {
    assert.equal(hostIsDialing("TESTHOST:1111-2222"), true);
    assert.equal(hostIsDialing("otherhost:1111-2222"), false);
    assert.equal(hostIsDialing("11111111-2222-3333-4444-555555555555"), false, "a local session has no host to dial");
    assert.equal(hostIsDialing(""), false);
    assert.equal(hostIsDialing(null), false);
  });
  // a manager too old to publish the state, or none at all: never live
  withFed({ down: () => ["TESTHOST"], lastSeen: () => 0 }, () => assert.equal(hostIsDialing("TESTHOST:1111"), false));
  const g = globalThis as any;
  const prev = g.__rompFed;
  delete g.__rompFed;
  try { assert.equal(hostIsDialing("TESTHOST:1111"), false); } finally { if (prev !== undefined) g.__rompFed = prev; }
});

test("federation publishes the dial state through the pure rule and says when it changes, on the socket's events and the kernel's poll", () => {
  assert.match(FED, /dialing: \(h: string\) => \{ const c = this\.conns\.get\(h\); return hostDialLive\(this\.dialingHosts\.has\(h\), c && c\.ws \? c\.ws\.readyState : null\); \}/);
  // the kernel's word, read in the same /tunnels poll that publishes the down set, an event only on a change
  assert.match(FED, /const dialing = new Set\(\[\.\.\.want\.keys\(\)\]\.filter\(\(h\) => want\.get\(h\)\.dialing === true\)\);/);
  assert.match(FED, /for \(const h of new Set\(\[\.\.\.dialing, \.\.\.this\.dialingHosts\]\)\) if \(dialing\.has\(h\) !== this\.dialingHosts\.has\(h\)\) this\.dialEvent\(h, dialing\.has\(h\)\);/,
    "one event per host whose state changed, carrying its true value: a repaint per change, not per poll");
  // the three transitions, in the connect path: the dial itself, the open, the close — no timer anywhere
  assert.match(FED, /conn\.ws = ws;\n\s*this\.dialEvent\(conn\.host, true\);/, "a dial began the moment the socket exists");
  assert.match(FED, /ws\.onopen = \(\) => \{\n\s*this\.dialEvent\(conn\.host, false\);/, "…and ended on open");
  assert.match(FED, /ws\.onclose = \(ev: CloseEvent\) => \{\n\s*this\.dialEvent\(conn\.host, false\);/, "…or on close");
  assert.match(FED, /new CustomEvent\("romp:hostDial", \{ detail: \{ host, dialing \} \}\)/);
  // the watchdog's abandon detaches the dying socket's onclose, so it must say the attempt ended itself
  assert.match(FED, /c\.ws = null;\n\s*this\.dialEvent\(c\.host, false\);[^\n]*\n\s*this\.connect\(c\);/, "an abandoned dial ends the live state before the fresh dial begins");
  assert.ok((FED.match(/this\.dialEvent\(/g) || []).length >= 5, "the three socket transitions, the watchdog's abandon and the poll's change dispatch it, nothing timed");
});

test("the foot's swirl sits after the sentence as the gist's flex sibling, only while dialing, and the sentence is unchanged", () => {
  assert.match(RENDER, /const text = host \+ " is disconnected — this is the last romp got from it\. Reconnecting\.";/,
    "no new copy: the one sentence");
  assert.match(RENDER, /const dialing = hostIsDialing\(activeId\);/);
  assert.match(RENDER, /const key = text \+ \(dialing \? " \|dialing" : ""\);/, "the rebuild is keyed on the state");
  assert.match(RENDER, /glyph: "api", sev: "warn", gist: text, nested: true, live: dialing,/, "the sentence is the gist as before; the row is live exactly when it spins");
  assert.match(RENDER, /if \(dialing\) \{\n\s*const swirl = el\("img", "host-dial-swirl"\)/, "the swirl exists only when dialing");
  assert.match(RENDER, /swirl\.src = mediaSrc\("romp-swirl-glyph\.svg"\); swirl\.alt = ""; swirl\.onerror = \(\) => swirl\.remove\(\);/);
  assert.match(RENDER, /const gistEl = card\.querySelector\("\.notice-head \.notice-gist"\);\n\s*if \(gistEl\) gistEl\.after\(swirl\);/,
    "a flex SIBLING right after the gist, never inside it (the gist ellipsizes on a narrow pane and would clip the swirl first)");
  assert.match(RENDER, /window\.addEventListener\("romp:hostDial", \(\) => \{ syncHostOfflineFoot\(\); repaintEmptyStateIfUnfocused\(\); \}\);/,   // (T357: the unfocused body's "reconnecting" rides the same event)
    "repainted on federation's dial event, and on nothing timed");
});

test("the swirl is a flex item of the head that spins with the placeholder tab's motif and rests under reduced motion", () => {
  assert.match(CSS, /\.notice-head \.host-dial-swirl \{[^}]*flex: 0 0 auto;[^}]*animation: tab-ph-swirl-spin 7s linear infinite; \}/,
    "flex: 0 0 auto, so the ellipsizing gist beside it gives way first");
  assert.match(CSS, /@media \(prefers-reduced-motion: reduce\) \{ \.notice-head \.host-dial-swirl \{ animation: none; \} \}/);
});
