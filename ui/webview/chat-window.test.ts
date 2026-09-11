// The uuid-anchored chat wire's list operations (T323 stage 4b): executed for real over small lists.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { applyTailAfter, prependHead, appendMore, mergeWindow, historyLabel, indexOfUuid } from "./chat-window";

const ev = (u: string) => ({ uuid: u, kind: "user", md: u });
const run = (...u: string[]) => u.map(ev);
const uu = (l: { uuid?: string }[]) => l.map((e) => e.uuid);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("a chatTail by uuid truncates after its anchor and appends; an anchor not resident is a gap", () => {
  assert.deepEqual(uu(applyTailAfter(run("a", "b", "c"), "b", run("c2", "d"))!), ["a", "b", "c2", "d"]);
  assert.deepEqual(uu(applyTailAfter(run("a", "b", "c"), "c", run("d"))!), ["a", "b", "c", "d"]);
  assert.equal(applyTailAfter(run("a", "b"), "zz", run("d")), null, "the anchor is gone: ask for a full");
});

test("a chatHead by uuid prepends only when its beforeUuid is the resident oldest", () => {
  assert.deepEqual(uu(prependHead(run("d", "e"), "d", run("b", "c"))!), ["b", "c", "d", "e"]);
  assert.equal(prependHead(run("d", "e"), "e", run("b")), null, "stale: the oldest moved on");
  assert.deepEqual(uu(prependHead(run("d"), "d", [])!), ["d"], "an empty page (the head): the list stands");
});

test("a chatMore by uuid appends only after the resident newest", () => {
  assert.deepEqual(uu(appendMore(run("a", "b"), "b", run("c", "d"))!), ["a", "b", "c", "d"]);
  assert.equal(appendMore(run("a", "b"), "a", run("c")), null);
});

test("a chatWindow replaces a run it does not overlap and merges one it does, in transcript order, once per uuid", () => {
  const far = mergeWindow(run("x", "y", "z"), run("a", "b", "c"));
  assert.equal(far.mode, "replace"); assert.deepEqual(uu(far.events), ["a", "b", "c"]);
  const before = mergeWindow(run("c", "d", "e"), run("a", "b", "c"));          // the window ends inside the run
  assert.equal(before.mode, "merge"); assert.deepEqual(uu(before.events), ["a", "b", "c", "d", "e"]);
  const inside = mergeWindow(run("a", "b", "c", "d"), run("b", "c"));          // the window is inside the run
  assert.equal(inside.mode, "merge"); assert.deepEqual(uu(inside.events), ["a", "b", "c", "d"]);
  const after = mergeWindow(run("a", "b", "c"), run("c", "d", "e"));           // the window starts inside the run
  assert.equal(after.mode, "merge"); assert.deepEqual(uu(after.events), ["a", "b", "c", "d", "e"]);
  assert.equal(indexOfUuid(after.events, "e"), 4);
});

test("the history strip shows no number while the head is unknown", () => {
  assert.equal(historyLabel(false, 250, null), "older history");
  assert.equal(historyLabel(false, 250, 900), "older history", "a total handed with an unknown head is not shown either");
  assert.equal(historyLabel(true, 250, 900), "650 older");
  assert.equal(historyLabel(true, 900, 900), "");
});

test("render.ts speaks proto 2 at ready and routes the four proto-2 frames through this module", () => {
  assert.match(RENDER, /postMessage\(\{ type: "ready", proto: 2 \}\)/, "the ready frame names the protocol");
  for (const fn of ["indexOfUuid", "prependHead", "appendMore", "mergeWindow", "historyLabel"]) assert.ok(RENDER.includes(fn + "("), fn);   // chatTail truncates by indexOfUuid in place
  assert.ok(RENDER.includes('m.type === "chatWindow"') && RENDER.includes('m.type === "chatMore"'), "the two new frames are dispatched");
  assert.ok(RENDER.includes('type: "loadAround"') && RENDER.includes('type: "loadNewer"'), "and the two new requests are posted");
});
