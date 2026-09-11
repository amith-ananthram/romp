// Status pips: what renders, and — just as load-bearing — what does NOT.
//
// The rule the three surfaces (feed, sessions pane, chat tab strip) agree on: a pip marks something
// HAPPENING (gold working, await-green awaiting background work) or something WRONG (a gray ring when the
// live state could not be read). A healthy idle session gets no pip at all, so a blank means "alive
// and quiet" and nothing else. Before the gray ring existed, an unreadable state drew the same
// nothing as an idle one, which is how a rendering hole hid in plain sight.
//
// Source pins, because these files have no jsdom harness; the federation merge is executed.
import { test } from "node:test";
import assert from "node:assert";
import * as fs from "fs";
import * as path from "path";
import { mergeHostFeeds } from "./federation";

const here = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const FEED = here("feed.ts");
const FLEET = here("fleet.ts");
const RENDER = here("render.ts");
const FEED_CSS = here("feed.css");
const FLEET_CSS = here("fleet-pane.css");
const STYLES = here("styles.css");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the kernel publishes the unreadable-state list, and no ready list", () => {
  assert.ok(KERNEL.includes("def _state_unknown_names(alive, live_map, working, awaiting):"));
  assert.ok(KERNEL.includes('"stateUnknown": _state_unknown_names(alive, live_map, working, awaiting)'));
  const feedSrc = KERNEL.slice(KERNEL.indexOf("def build_feed"), KERNEL.indexOf("def _push_feed"));
  assert.ok(!/"ready":\s*ready/.test(feedSrc), "a quiet session is not enumerated — blank already says it");
});

test("feed dotFor ranks work over await over unknown, and idle falls through to no pip", () => {
  assert.match(FEED, /workingSet\.has\(name\) \? "work" : awaitingSet\.has\(name\) \? "await"/);
  assert.match(FEED, /: unknownSet\.has\(name\) \? "unknown" : "";/);
  assert.match(FEED, /unknownSet = new Set\(Array\.isArray\(m\.stateUnknown\)/);
  // the fall-through IS the ready case: no set to consult, no class, no pip
  assert.ok(!/readySet/.test(FEED), "there is no ready set — an idle session simply has no pip");
});

test("the feed dot retints in place and carries its own tooltip", () => {
  assert.match(FEED, /for \(const k of \["await", "unknown"\]\) d\.classList\.toggle\(k, st === k\);/);
  assert.match(FEED, /const DOT_TIP: Record<Exclude<DotState, "">, string>/);
  assert.ok(!/ready:/.test(FEED.slice(FEED.indexOf("const DOT_TIP"), FEED.indexOf("const DOT_TIP") + 400)),
    "no ready tooltip — there is no ready pip to explain");
});

test("the sessions pane speaks the same three-way language", () => {
  assert.match(FLEET, /function statusDot\(s: FleetSession\): HTMLElement \| null \{/);
  assert.match(FLEET, /st === "working" \? "" : st === "awaitingBg" \? "await" : st \? null : "unknown"/);
  // a known state with its own treatment (blocked/compacting/closed) returns null → no pip
  assert.match(FLEET, /if \(kind === null\) return null;/);
  const uses = FLEET.match(/statusDot\(s\)/g) || [];
  assert.equal(uses.length, 2, "the flat label and the grouped header; the provisional row stays bare");
});

test("the tab strip draws the gray ring for a missing state and nothing for idle", () => {
  // the unknown ring comes from the one dot rule (tabDotClass, T262g): a missing state → "tab-dot unknown"
  const TS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "tab-state.ts"), "utf8");
  assert.match(TS, /if \(!st\) return "tab-dot unknown";/);
  assert.match(RENDER, /const dotCls = tabDotClass\(st\);/);
  // ready/idle reaches no branch at all — the ladder ends without appending
  assert.ok(!/tab-dot ready/.test(RENDER), "no ready pip on the strip");
});

test("every surface styles the unknown ring, and none styles a ready one", () => {
  assert.match(FEED_CSS, /\.fwork-dot\.unknown \{ background: transparent; box-shadow: inset 0 0 0 1\.5px #8a8a8a; \}/);
  assert.match(FLEET_CSS, /\.fl-workdot\.unknown\{background:transparent;box-shadow:inset 0 0 0 1\.5px #8a8a8a\}/);
  assert.match(STYLES, /\.tab-dot\.unknown \{ background: transparent; box-shadow: inset 0 0 0 1\.5px #8a8a8a; \}/);
  for (const [name, css] of [["feed.css", FEED_CSS], ["fleet-pane.css", FLEET_CSS], ["styles.css", STYLES]] as const) {
    assert.ok(!/\.(fwork-dot|fl-workdot|tab-dot)\.ready/.test(css), `${name} must not style a ready pip`);
  }
});

test("styles.css points at the palette for the gray rather than leaving a bare hex", () => {
  // feed.css / fleet-pane.css load standalone so their hexes stand alone; styles.css is the shared
  // sheet and must say where the value comes from (the network strip's down-host gray)
  const i = STYLES.indexOf(".tab-dot.unknown");
  assert.ok(i > 0);
  assert.match(STYLES.slice(Math.max(0, i - 400), i), /strip\.ts/,
    "the shared sheet names its source for the gray");
});

test("the sessions pane comments say 'the sessions', never 'the fleet' as a noun", () => {
  const added = FLEET.slice(FLEET.indexOf("// The status pip before a session name"), FLEET.indexOf("function statusDot"));
  assert.ok(!/\bthe fleet\b/i.test(added), "say the sessions; FLEET_CSS-style identifiers are fine");
});

test("federation concatenates stateUnknown per host, and an old host stays blank", () => {
  const perHost: any = {
    "": { type: "feed", items: [], asks: [], working: [], awaiting: [], stateUnknown: ["api"] },
    TESTHOST: { type: "feed", items: [], asks: [], working: [], awaiting: [], stateUnknown: ["TESTHOST:tests"] },
  };
  const merged: any = mergeHostFeeds(perHost, ["", "TESTHOST"]);
  assert.deepEqual(merged.stateUnknown, ["api", "TESTHOST:tests"], "concatenated, local first");

  // a host too old to send the list contributes NOTHING — its sessions stay blank ("quiet"),
  // which is the honest degradation now that blank is a real state rather than a hole
  const older: any = {
    "": { type: "feed", items: [], asks: [], working: [], awaiting: [], stateUnknown: ["api"] },
    TESTHOST: { type: "feed", items: [], asks: [], working: [], awaiting: [] },
  };
  const m2: any = mergeHostFeeds(older, ["", "TESTHOST"]);
  assert.deepEqual(m2.stateUnknown, ["api"], "no entry invented for the old host");
});
