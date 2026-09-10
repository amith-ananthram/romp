// Two threads on the SAME passage (the user 2026-09-10, who commented on one selection twice, seconds
// apart): ensureCommentMark re-finds the identical range for the second thread and wraps its <mark>
// INSIDE the first's, so the earlier thread's mark is the outer one. A click lands on the innermost
// element and the body delegate takes the nearest [data-act] — the INNER (later) thread — so the outer
// thread's needs-you ring could never be opened from its own ring: its unread never cleared and the
// reply-ready chip stayed lit. The scroll rail had the same fault: two ticks at one `top`, the last
// painted (read) covering the unread one. Fix: the ring you click opens the thread that owns the
// ring (a pure pick over the mark chain, innermost first), and an unread tick stacks above its read
// siblings. Pins: the pick's behaviour, cmtopen's use of it, the tick's stacking. Synthetic tids.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { pickMarkToOpen } from "./comments";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const inner = { tid: "t-later", unread: false };
const outer = { tid: "t-earlier", unread: false };

test("inner read + outer unread → the outer: the ring under the pointer belongs to the outer thread", () => {
  assert.equal(pickMarkToOpen([inner, { ...outer, unread: true }]), "t-earlier");
});

test("both read → the inner, as before: the nearest mark to the click", () => {
  assert.equal(pickMarkToOpen([inner, outer]), "t-later");
});

test("both unread → the inner: the newest ring is the one under the finger", () => {
  assert.equal(pickMarkToOpen([{ ...inner, unread: true }, { ...outer, unread: true }]), "t-later");
});

test("a single mark opens itself, read or not; an empty chain opens nothing", () => {
  assert.equal(pickMarkToOpen([inner]), "t-later");
  assert.equal(pickMarkToOpen([{ ...inner, unread: true }]), "t-later");
  assert.equal(pickMarkToOpen([]), null);
});

test("three deep: the innermost UNREAD wins over an outer unread and a read one in between", () => {
  assert.equal(pickMarkToOpen([{ tid: "c", unread: false }, { tid: "b", unread: true }, { tid: "a", unread: true }]), "b");
});

test("cmtopen walks the mark chain from the clicked mark up and opens the pick — the unread bit read from the thread store", () => {
  const at = RENDER.indexOf("cmtopen: (elx) => {");
  assert.ok(at > 0, "the handler exists, still delegated on the body (comments.test.ts pins the shape)");
  const handler = RENDER.slice(at, RENDER.indexOf("cmtclose:", at));
  assert.match(handler, /pickMarkToOpen\(/, "the pick is the pure helper, not an inline heuristic");
  assert.match(handler, /markChain\(elx/, "the chain starts at the clicked (innermost) mark");
  const chain = RENDER.slice(RENDER.indexOf("function markChain("), RENDER.indexOf("\n}\n", RENDER.indexOf("function markChain(")));
  assert.match(chain, /closest\("mark\.cmt-hl"\)/, "each step is the next enclosing mark");
  assert.match(chain, /commentThreads\.get\(/, "unread comes from the thread store, the source the ring is painted from");
  assert.match(chain, /!!th\?\.unread && th\.status === "open"/, "the same predicate styleCommentMark paints the ring with");
  assert.doesNotMatch(handler, /elx\.dataset\.tid;/, "the clicked element's own tid is no longer the answer");
});

test("an unread rail tick stacks above read siblings at the same top — the covered ring was the rail's face of the bug", () => {
  const rule = CSS.match(/\.cmt-tick\.unread \{[^}]*\}/)?.[0] || "";
  assert.ok(rule, "the unread tick rule exists");
  assert.match(rule, /z-index: 1;/, "explicit stacking: siblings share position: absolute inside the rail, so paint order is DOM order without it");
  const base = CSS.match(/\.cmt-tick \{[^}]*\}/)?.[0] || "";
  assert.doesNotMatch(base, /z-index/, "the read tick keeps the default level — only the unread one lifts");
});
