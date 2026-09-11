// T302: the postal card's kind word and delivery state, from what the kernel files (executed tests on the pure
// module; the render pins live in postal-card.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { kindLabel, deliveryOf, deliveryTitle, DELIVERY_GLYPHS, MARK_CHECK_CLASS } from "./postal-state";

const clock = (t: number) => "T" + t;

test("sent / delivered / read is the ladder messaging apps draw: a hollow circle, the circle with a check, the filled circle with a check (T337)", () => {
  // the user (2026-09-10) liked the marks but wanted the circled check: zoomed in, a bare check read as a line. One circle
  // on all three rungs, the same geometry, so the ladder reads as ONE mark changing state; the check is one three-point
  // path, drawn the same on delivered and read (only the circle's fill changes); the read rung's check is knocked out of
  // the filled circle in the page colour (the class the sheet styles).
  const { sent, delivered, read } = DELIVERY_GLYPHS;
  const circle = /<circle cx="8" cy="8" r="([\d.]+)"([^>]*)\/>/;
  // the check is a THREE-POINT polyline (a bare line is what the user zoomed in on and rejected), every point inside the
  // circle; the circle plus half its stroke fits the 16-unit box
  const check = new RegExp('<path class="' + MARK_CHECK_CLASS + '" d="(M[\\d.]+ [\\d.]+ L[\\d.]+ [\\d.]+ L[\\d.]+ [\\d.]+)"\\/>');
  const radii = [sent, delivered, read].map((g) => { const m = circle.exec(g); assert.ok(m, "a circle in " + g); return m![1]; });
  assert.equal(new Set(radii).size, 1, "one radius for the three circles: " + radii.join(" "));
  const r = parseFloat(radii[0]);
  assert.ok(r >= 5 && r + 0.75 <= 8, "the circle fills the box without clipping its 1.5 stroke: r=" + r);
  const pts = check.exec(delivered)![1].match(/[\d.]+/g)!.map(Number);
  for (let i = 0; i < pts.length; i += 2) {
    assert.ok(Math.hypot(pts[i] - 8, pts[i + 1] - 8) <= r - 0.75, "check point " + pts[i] + "," + pts[i + 1] + " sits inside the circle");
  }
  assert.ok(pts[3] > pts[1] && pts[3] > pts[5], "the middle point is the check's low corner");
  assert.doesNotMatch(sent, check, "sent: the hollow circle alone");
  assert.doesNotMatch(sent, /fill=/, "sent: hollow");
  assert.match(delivered, check, "delivered: the circle with the check");
  assert.doesNotMatch(delivered, /fill=/, "delivered: still hollow");
  assert.match(read, check, "read: the check…");
  assert.match(read, /<circle[^>]* fill="currentColor"/, "…on the filled circle");
  assert.equal(check.exec(delivered)![0], check.exec(read)![0], "one check shape on both rungs");
  assert.equal(new Set([sent, delivered, read]).size, 3, "three states, three distinct drawings");
  // the other three states keep their glyphs: the clock, the cross, the return arrow
  assert.match(DELIVERY_GLYPHS.parked, /<circle cx="8" cy="8" r="5\.6"\/><path d="M8 4\.8 V8\.2 L10\.4 9\.6"\/>/);
  assert.match(DELIVERY_GLYPHS.bounced, /M4\.5 4\.5 L11\.5 11\.5/);
  assert.match(DELIVERY_GLYPHS.recalled, /A2\.8 2\.8 0 0 0 12\.8 5\.2/);
  // the drawings are this repo's own primitives (a circle, a three-point check), not an icon set's assets: the module
  // says so where the map is, because Signal's own icons ship under a copyleft licence and may not be copied here
  const src = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "postal-state.ts"), "utf8");
  assert.match(src, /drawn here from two primitives[\s\S]{0,400}Signal's own icon assets[\s\S]{0,200}not copied/);
});

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
