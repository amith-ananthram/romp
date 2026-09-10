// The viewer's ledger over remote rows (review find, 2026-09-09): a remote kernel's feed and archive projection read
// only their own cleared.jsonl, so an id the LOCAL ledger clears for a remote card (clearedForeign on the local
// payload, bare) must drop that remote ask/item and read that remote archived top cleared, subtree with it (a top
// whose only completion is the copied status leaves the list instead, as the owning kernel's overlay drops it), while
// local rows and untouched remote rows pass through exactly as before. Same harness as feed-clock-anchor.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { mergeHostFeeds, applyViewerClears } from "./federation";

const SID = "11111111-2222-3333-4444-555555555555";
const A = "HOSTA:" + SID;

function remoteFeed() {
  return {
    type: "feed", now: 100,
    asks: [{ itemId: SID + ":g1", sid: A }, { itemId: SID + ":g2", sid: A }],
    items: [{ itemId: SID + ":g9", sid: A }],
    ledgers: [{ sid: A, name: "HOSTA:api", ledger: { tree: [], archivedTops: [
      { id: SID + ":g1", depth: 0, cleared: false, done: true },
      { id: SID + ":g1a", depth: 1, cleared: false, done: true },
      { id: SID + ":g3", depth: 0, cleared: false, done: true },
      { id: SID + ":g3a", depth: 1, cleared: false, done: true },
    ] } }],
  };
}

test("a foreign clear on the local payload drops the remote ask and reads the remote archived top cleared, subtree with it", () => {
  const local = { type: "feed", now: 7, asks: [{ itemId: SID + ":g1", sid: SID }], ledgers: [], clearedForeign: [SID + ":g1", SID + ":g9"] };
  const remote = remoteFeed();
  const merged = mergeHostFeeds({ "": local, HOSTA: remote }, ["", "HOSTA"]);
  assert.deepEqual(merged.asks.map((a: any) => [a.sid, a.itemId]), [[SID, SID + ":g1"], [A, SID + ":g2"]], "the remote g1 ask is gone; the LOCAL g1 stays (the local kernel already applied its ledger)");
  assert.deepEqual(merged.items.map((c: any) => c.itemId), [], "the remote item is gone");
  const tops = merged.ledgers.find((l: any) => l.sid === A).ledger.archivedTops;
  assert.deepEqual(tops.map((n: any) => [n.id.split(":").pop(), n.cleared]),
    [["g1", true], ["g1a", true], ["g3", false], ["g3a", false]], "g1 and its child read cleared; g3 untouched");
  assert.equal(remote.ledgers[0].ledger.archivedTops[0].cleared, false, "the host payload's own rows are not mutated");
  assert.equal(remote.asks.length, 2);
});

test("without foreign ids nothing changes, and a remote host with none named passes through", () => {
  const remote = remoteFeed();
  const merged = mergeHostFeeds({ "": { type: "feed", now: 7, asks: [], ledgers: [] }, HOSTA: remote }, ["", "HOSTA"]);
  assert.equal(merged.asks.length, 2);
  assert.deepEqual(merged.ledgers[0].ledger.archivedTops.map((n: any) => n.cleared), [false, false, false, false]);
  const other = { type: "feed", now: 7, asks: [], ledgers: [], clearedForeign: [SID + ":zz"] };
  const merged2 = mergeHostFeeds({ "": other, HOSTA: remoteFeed() }, ["", "HOSTA"]);
  assert.equal(merged2.asks.length, 2, "an id that names nothing drops nothing");
});

test("the overlay is pure over the rows it is given and ignores junk ids", () => {
  const merged: any = { asks: [{ itemId: SID + ":g1", sid: A }], items: [] };
  const ledgers = [{ sid: A, ledger: { tree: [], archivedTops: [{ id: SID + ":g1", depth: 0, cleared: false }] } }];
  applyViewerClears(merged, ledgers, [SID + ":g1", 42, null]);
  assert.equal(merged.asks.length, 0);
  assert.equal(ledgers[0].ledger.archivedTops[0].cleared, true);
  applyViewerClears(merged, ledgers, undefined);   // no ids: a no-op
});

test("a foreign clear of a remote archived top whose only completion is a copied status drops the row and its subtree; a verdict or a takeaway keeps it", () => {
  // The rows carry the archive projection's own fields: at depth 0 `derived` means the copied status or a summary,
  // never an ancestor, so derived with a blank summary is a root whose only completion is the copied status. The
  // owning kernel's overlay drops such a root, subtree with it, when its cleared rows name it; a clear that only the
  // viewer's rows hold must read the same way on the merged board, not leave the row listed struck through.
  const row = (id: string, depth: number, derived: boolean, summary: string | null, cleared = false) =>
    ({ id: SID + ":" + id, depth, cleared, done: true, derived, summary });
  const rows = () => [
    row("g1", 0, true, null), row("g1a", 1, true, null),        // status-only, named: dropped with its child
    row("g2", 0, false, null), row("g2a", 1, true, null),       // its own verdict, named: stays, reads cleared
    row("g3", 0, true, "kept the takeaway"),                    // a takeaway, named: stays, reads cleared
    row("g4", 0, true, "   "), row("g4a", 1, true, null),       // whitespace is not a takeaway: dropped
    row("g5", 0, true, null), row("g5a", 1, true, null),        // status-only, NOT named: untouched
    row("g6", 0, true, null, true), row("g6a", 1, true, null),  // status-only, its copied flag already on, not named:
    //                                                             dropped too (the copied-flag arm of the kernel's rule)
  ];
  const remote = { type: "feed", now: 100, asks: [], items: [],
    ledgers: [{ sid: A, name: "HOSTA:api", ledger: { tree: [], archivedTops: rows() } }] };
  const localTops = rows();
  const local = { type: "feed", now: 7, asks: [], items: [],
    ledgers: [{ sid: SID, name: "api", ledger: { tree: [], archivedTops: localTops } }],
    clearedForeign: [SID + ":g1", SID + ":g2", SID + ":g3", SID + ":g4"] };
  const merged = mergeHostFeeds({ "": local, HOSTA: remote }, ["", "HOSTA"]);
  const tops = merged.ledgers.find((l: any) => l.sid === A).ledger.archivedTops;
  assert.deepEqual(tops.map((n: any) => [n.id.split(":").pop(), n.cleared]),
    [["g2", true], ["g2a", true], ["g3", true], ["g5", false], ["g5a", false]],
    "g1, g4 and g6 leave with their children; g2 (its own verdict) and g3 (a takeaway) stay and read cleared; g5 is untouched");
  const own = remote.ledgers[0].ledger.archivedTops;
  assert.equal(own.length, 11, "the host payload's own rows are not removed");
  assert.deepEqual([own[0].id.split(":").pop(), own[0].cleared], ["g1", false], "the host payload's own rows are not mutated");
  const localOut = merged.ledgers.find((l: any) => l.sid === SID).ledger.archivedTops;
  assert.equal(localOut, localTops, "a LOCAL entry of the same shape passes through as is: the local kernel already applied its own rows");
});

test("a drop with nothing else to mark still takes effect: the entry is rebuilt with the row and its subtree gone", () => {
  // The reported case alone: one status-only remote top the viewer's ids name and no other row to mark. The rebuild
  // has to follow from the drop itself, or the host payload's rows pass through by reference and the row stays listed.
  const tops = [{ id: SID + ":g1", depth: 0, cleared: false, done: true, derived: true, summary: null },
                { id: SID + ":g1a", depth: 1, cleared: false, done: true, derived: true, summary: null }];
  const entry = { sid: A, ledger: { tree: [], archivedTops: tops } };
  const ledgers = [entry];
  applyViewerClears({ asks: [], items: [] }, ledgers, [SID + ":g1"]);
  assert.deepEqual(ledgers[0].ledger.archivedTops, [], "the root and its child are gone");
  assert.notEqual(ledgers[0], entry, "a fresh entry replaced the host payload's own object");
  assert.equal(tops.length, 2, "the host payload's own rows are not mutated");
});
