// T302: the postal card's kind word and delivery state, from what the kernel files (executed tests on the pure
// module; the render pins live in postal-card.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { kindLabel, deliveryOf, deliveryTitle } from "./postal-state";

const clock = (t: number) => "T" + t;

test("the kind is a capitalised word per class, empty when unknown", () => {
  assert.equal(kindLabel("delegate"), "Delegation");
  assert.equal(kindLabel("coordinate"), "Coordination");
  assert.equal(kindLabel("question"), "Question");
  assert.equal(kindLabel("fyi"), "");
  assert.equal(kindLabel(null), "");
});

test("an incoming message in hand shows no delivery icon; one that waited while offline shows parked", () => {
  assert.equal(deliveryOf({ direction: "in", t: 100 }), null);
  assert.deepEqual(deliveryOf({ direction: "in", park: true, t: 100 }),
                   { state: "parked", word: "Parked", t: 100, note: "waited while you were offline" });
});

test("a local send stamped delivered is delivered; read once the recipient consumed it", () => {
  assert.deepEqual(deliveryOf({ direction: "out", status: "delivered", ts: "2026-09-10T16:41:00.000Z" }),
                   { state: "delivered", word: "Delivered", t: Math.floor(Date.parse("2026-09-10T16:41:00.000Z") / 1000) });
  assert.deepEqual(deliveryOf({ direction: "out", status: "delivered", receipt: { read: 200 } }),
                   { state: "read", word: "Read", t: 200 });
});

test("across the relay: sent until the far host acks, then delivered; the recipient's read still wins", () => {
  assert.equal(deliveryOf({ direction: "out", status: "delivered", receipt: { remote: true }, t: 100 })!.state, "sent");
  assert.deepEqual(deliveryOf({ direction: "out", status: "delivered", receipt: { remote: true, relayed: 150 } }),
                   { state: "delivered", word: "Delivered", t: 150 });
  assert.equal(deliveryOf({ direction: "out", status: "delivered", receipt: { remote: true, relayed: 150, read: 160 } })!.state, "read");
});

test("parked, bounced and recalled outrank the send-time stamp; a bounce carries its reason", () => {
  assert.deepEqual(deliveryOf({ direction: "out", status: "parked", t: 100 }), { state: "parked", word: "Parked", t: 100 });
  assert.deepEqual(deliveryOf({ direction: "out", status: "parked", receipt: { bounced: 300, why: "refused: isolated" } }),
                   { state: "bounced", word: "Bounced", t: 300, why: "refused: isolated" });
  assert.deepEqual(deliveryOf({ direction: "out", status: "delivered", receipt: { recalled: 400 } }),
                   { state: "recalled", word: "Recalled", t: 400 });
  assert.equal(deliveryOf({ direction: "out", status: "delivered", receipt: { bounced: 300, read: 200 } })!.state, "bounced",
               "a bounce is final whatever else the ledger says");
});

test("a send that errored has no state icon: the tool row itself says what happened", () => {
  assert.equal(deliveryOf({ direction: "out", status: null }), null);
  assert.equal(deliveryOf({ direction: "out" }), null);
  // …even when the body-keyed join handed it a neighbour's receipt (a refused send retried with the same
  // words seconds later): a message that never left wears no checks (review, 2026-09-10)
  assert.equal(deliveryOf({ direction: "out", status: null, receipt: { read: 1 } }), null);
  assert.equal(deliveryOf({ direction: "out", receipt: { relayed: 1, remote: true } }), null);
});

test("the title carries the word, the clock and the reason", () => {
  assert.equal(deliveryTitle({ state: "delivered", word: "Delivered", t: 5 }, clock), "Delivered T5");
  assert.equal(deliveryTitle({ state: "read", word: "Read" }, clock), "Read");
  assert.equal(deliveryTitle({ state: "bounced", word: "Bounced", t: 7, why: "undeliverable" }, clock), "Bounced T7 — undeliverable");
  assert.match(deliveryTitle({ state: "parked", word: "Parked", t: 9 }, clock), /^Parked T9 — delivers when/);
  // an incoming message that waited is in hand: its title says so, never a future delivery (review, 2026-09-10)
  assert.equal(deliveryTitle({ state: "parked", word: "Parked", t: 9, note: "waited while you were offline" }, clock),
               "Parked T9 — waited while you were offline");
  assert.match(deliveryTitle({ state: "sent", word: "Sent", t: 9 }, clock), /^Sent T9 — on its way/);
});
