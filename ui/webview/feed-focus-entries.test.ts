// The FOCUSED SESSION section's pure half (T347, the user 2026-09-11, who wanted the focused session's cards on
// top of the feed): feed-focus.ts picks, from the board's three column buckets, the entries the section shows for
// one session. No DOM, so node --test runs it directly on synthetic entries; the wiring in feed.ts and feed.css is
// pinned in feed-focus-section.test.ts. (feed-focus.test.ts, the older file of that name, is the keyboard-focus
// policy — a different feature.) Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { focusedEntries, focusedCardCount } from "./feed-focus";

const WEB = "aaaaaaaa-1111-2222-3333-444444444444", API = "aaaaaaaa-1111-2222-3333-555555555555";
const TESTS = "aaaaaaaa-1111-2222-3333-666666666666";
type E = { kind: "ask" | "group" | "sess"; t: number; sid: string; id: string; n: number };
const ask = (id: string, sid: string, t: number): E => ({ kind: "ask", t, sid, id, n: 1 });
const group = (id: string, sid: string, t: number, members: number): E => ({ kind: "group", t, sid, id, n: members });
const sess = (sid: string): E => ({ kind: "sess", t: 0, sid, id: "head:" + sid, n: 0 });
const sidOf = (e: E) => e.sid;
const cards = (e: E) => e.n;
// the board's buckets as grouped mode leaves them: run headers open each session's run, cards in column order
const board = () => ({
  asks: [sess(WEB), ask("g1", WEB, 1), group("t1", WEB, 2, 2), sess(API), ask("g2", API, 3)],
  needsInput: [sess(API), ask("g3", API, 4)],
  completed: [sess(WEB), ask("g4", WEB, 5), ask("g5", WEB, 6)],
});
const ids = (xs: E[]) => xs.map((e) => e.id);
const EMPTY = { asks: [], needsInput: [], completed: [] };

test("the section's entries are the board's cards for the session, per column, in the column's order", () => {
  const b = board();
  const got = focusedEntries(b, WEB, sidOf);
  assert.deepEqual({ asks: ids(got.asks), needsInput: ids(got.needsInput), completed: ids(got.completed) },
    { asks: ["g1", "t1"], needsInput: [], completed: ["g4", "g5"] });
  assert.equal(got.asks[0], b.asks[1], "the same entry objects, never copies — the caller's update gate keys on identity");
});

test("run headers are dropped: one session needs no header between runs, its name is the section's head", () => {
  const got = focusedEntries(board(), WEB, sidOf);
  assert.ok([...got.asks, ...got.needsInput, ...got.completed].every((e) => e.kind !== "sess"));
  assert.deepEqual(ids(got.completed), ["g4", "g5"], "the header that opened the run is gone, its cards stay");
});

test("another session's cards are excluded", () => {
  const b = board();
  assert.deepEqual(ids(focusedEntries(b, API, sidOf).asks), ["g2"]);
  assert.deepEqual(ids(focusedEntries(b, API, sidOf).needsInput), ["g3"]);
  assert.deepEqual(ids(focusedEntries(b, API, sidOf).completed), []);
});

test("a focused session with no cards, or no focused tab (null), is three empty columns", () => {
  const b = board();
  assert.deepEqual(focusedEntries(b, TESTS, sidOf), EMPTY, "a focused session with no cards");
  assert.deepEqual(focusedEntries(b, null, sidOf), EMPTY, "no tab has focus in the chat");
  assert.deepEqual(focusedEntries({ asks: [], needsInput: [], completed: [] } as Record<"asks" | "needsInput" | "completed", E[]>, WEB, sidOf),
    EMPTY, "an empty board");
});

test("each column keeps the order it arrived in — newest-first below reads newest-first above", () => {
  const b = board();
  b.completed = [sess(WEB), ask("g5", WEB, 6), ask("g4", WEB, 5)];   // the board sorted newest-first
  assert.deepEqual(ids(focusedEntries(b, WEB, sidOf).completed), ["g5", "g4"]);
});

test("the input buckets are left untouched", () => {
  const b = board();
  const before = JSON.stringify(b);
  focusedEntries(b, WEB, sidOf);
  focusedEntries(b, null, sidOf);
  assert.equal(JSON.stringify(b), before);
});

test("the count is CARDS with the board's rule — a turn-group is its members — and zero for the empty section", () => {
  const b = board();
  assert.equal(focusedCardCount(focusedEntries(b, WEB, sidOf), cards), 5, "g1 + a two-member group + g4 + g5 = 1 + 2 + 1 + 1");
  assert.equal(focusedCardCount(focusedEntries(b, API, sidOf), cards), 2);
  assert.equal(focusedCardCount(focusedEntries(b, TESTS, sidOf), cards), 0);
  assert.equal(focusedCardCount(focusedEntries(b, null, sidOf), cards), 0);
});
