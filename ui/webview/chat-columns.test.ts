// The partition's pure rules, EXECUTED (the chat split, the user 2026-09-11): which column a page is from its
// own search string, and whether a column holds a session under the shell's sets. render.ts's tabInView is the
// one caller; tab-strip-skip.test.ts pins that call at the source. Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { colFromSearch, columnHolds, ownerOf, type ColSets } from "./chat-columns";

const WEB = "11111111-2222-3333-4444-555555555501";
const API = "11111111-2222-3333-4444-555555555502";
const TESTS = "11111111-2222-3333-4444-555555555503";
const REMOTE = "TESTHOST:11111111-2222-3333-4444-555555555504";   // a remote host's session rides its host prefix, as `order` carries it

test("colFromSearch: the shim's rule — the first column is the empty string, ?col=1 folds to it, a later column is its number", () => {
  assert.equal(colFromSearch(""), "", "no search: the first column (a standalone page, the VS Code webview)");
  assert.equal(colFromSearch("?col=1"), "", "?col=1 IS the first column: its state blob keeps the unsuffixed key");
  assert.equal(colFromSearch("?col=2"), "2");
  assert.equal(colFromSearch("?col=3&skeleton=1"), "3", "the skeleton flag rides beside it");
  assert.equal(colFromSearch("?skeleton=1"), "", "no col: the first column");
  assert.equal(colFromSearch("garbage"), "", "an unparseable search is the first column, never a throw");
});

test("columnHolds with null sets: no partition, everything is held, whatever the column", () => {
  for (const col of ["", "2", "9"]) for (const id of [WEB, API, REMOTE]) assert.equal(columnHolds(null, col, id), true, col + " holds " + id);
});

test("columnHolds: a later column holds the ids its entry lists, and only those", () => {
  const sets: ColSets = { "2": [API], "3": [TESTS, REMOTE] };
  assert.equal(columnHolds(sets, "2", API), true);
  assert.equal(columnHolds(sets, "2", TESTS), false, "listed by another column");
  assert.equal(columnHolds(sets, "2", WEB), false, "listed by no column: the first column's");
  assert.equal(columnHolds(sets, "3", TESTS), true);
  assert.equal(columnHolds(sets, "3", REMOTE), true, "a host-prefixed id is matched as a whole string");
  assert.equal(columnHolds(sets, "4", API), false, "a column with no entry holds nothing (a stale page whose entry closed)");
});

test("columnHolds: the first column derives — every id no entry lists, and none an entry does", () => {
  const sets: ColSets = { "2": [API], "3": [TESTS] };
  assert.equal(columnHolds(sets, "", WEB), true, "unlisted: the first column's");
  assert.equal(columnHolds(sets, "", REMOTE), true, "a remote host's session that arrived with no gesture lands in the first column");
  assert.equal(columnHolds(sets, "", API), false);
  assert.equal(columnHolds(sets, "", TESTS), false);
  assert.equal(columnHolds({}, "", WEB), true, "no later columns: the first column holds everything");
  assert.equal(columnHolds({}, "2", WEB), false);
});

test("a doubly listed id belongs to ONE column, the first key holding it, never to both", () => {
  // the shell's writes never produce a double and its sets() resolves one by row order; a store another
  // dashboard wrote before this shell reconciled it can still carry one, and two columns must not both show it
  const sets: ColSets = { "2": [API], "3": [API, TESTS] };
  assert.equal(ownerOf(sets, API), "2");
  assert.equal(columnHolds(sets, "2", API), true);
  assert.equal(columnHolds(sets, "3", API), false);
  assert.equal(columnHolds(sets, "", API), false, "…and the first column does not derive it either");
  assert.equal([2, 3, ""].filter((c) => columnHolds(sets, String(c), API)).length, 1, "exactly one holder");
});

test("ownerOf: the empty string for an id no entry lists, and a junk entry is skipped, never a throw", () => {
  assert.equal(ownerOf({ "2": [API] }, WEB), "");
  assert.equal(ownerOf({ "2": null as unknown as string[], "3": [WEB] }, WEB), "3", "a corrupt entry is passed over");
});
