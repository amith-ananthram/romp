// Send durability on the client (the 2026-09-05/06 audits). Two things went wrong at once: a send fed
// into a running turn is taken by the CLI at a later tool boundary and placed at its SEND time, so the
// pending bubble at the bottom vanished and the message reappeared higher up with no cue; and the
// client's bubble had a 20 s lifetime, so when the kernel's own echo was pruned early the send simply
// looked delivered. Now: (1) a pending send has NO lifetime — it ends on a landing, the kernel's
// never-delivered verdict, or the user's ✕; (2) the pending bubble is drawn AT ITS SEND POSITION from the
// start — right after its anchor, the last stable kernel event at the press — so the steps that stream
// in afterwards land below it and the absorbed atom, which the kernel places at the send time, replaces
// it in the same spot (T252, the user 2026-09-07: a header plus a button that jumps somewhere and later
// disappears is weird; the first cut's "joined mid-turn" header and the jump/✕ cue are gone);
// (3) every composer send posts a clientDiag breadcrumb (never the text).
// The 2026-09-06 adversarial review then fixed the decision's frame: (4) every verdict is read from the
// events AFTER the send's anchor, never from a 30-event tail count; (5) [retired with the cue];
// (6) the lost verdict shares the anchor; (7) exact text, one landing per send; (8) a connection
// drop repaints once; (9) the bare label is per bubble; (10) the kernel's own copy clears "not confirmed".
// Round 3 of that review: (11) receipt is attributed per send, like landings; (12) the ✕ removes the
// bubble's own entry; (13) a stamp taken late reads the events' own times. Round 4: (14) a late stamp
// presumes the frame's newest queued copy of its text is its own (the queued bubble carries no stamp).
// T252c gave every kernel copy an id; now (15) the id is the CLIENT's, minted at the press and posted with the
// send, so the kernel's copies wear it from their first appearance and nothing is latched by text. And (16) at
// a late stamp an event that NAMES the send (wears its id) is this send's whatever its stamp: the id outranks
// the clock.
// The decisions are executed through send-pending.ts; the DOM half is pinned in render.ts/styles.css.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { newPending, pendingBody, reconcilePending, dropPending, scanFrom, queuedCopyToHide, landedIn, provisionalIn, bareGroupLabel, sentAtLabel, type TailEvent, type PendingSend } from "./send-pending";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const RENDER = read("render.ts");
const CSS = read("styles.css");
const T0 = 1_700_000_000_000;
const TEXT = "rename this to fetch_notes";

// The press: registerOptimistic reconciles the fresh entry against the tail as it stands BEFORE any
// kernel round-trip, which stamps the anchor. Every scenario below starts there.
const press = (tail: TailEvent[], ...texts: string[]): PendingSend[] => {
  const list = texts.map((t, i) => newPending(t, undefined, T0 + i));
  reconcilePending(tail, list);
  return list;
};
const filler = (n: number, prefix = "f"): TailEvent[] =>
  Array.from({ length: n }, (_, i) => (i % 3 === 0 ? { kind: "thinking", uuid: `${prefix}${i}` } : i % 3 === 1 ? { kind: "tool", uuid: `${prefix}${i}` } : { kind: "assistant", md: "…", uuid: `${prefix}${i}` }));

// ── (1) no lifetime ──────────────────────────────────────────────────────────────────────────────

test("a pending send survives past 20 s — and past any time — without confirmation", () => {
  const list = press([{ kind: "assistant", md: "still working on the earlier step", uuid: "a0" }], TEXT);
  // nothing happens for a minute, then an hour: the wall clock moves on and the kernel's stamps move on
  // with it, and the decision reads neither
  const realNow = Date.now;
  try {
    for (const later of [T0 + 21_000, T0 + 60_000, T0 + 3_600_000]) {
      Date.now = () => later;
      const r = reconcilePending([
        { kind: "assistant", md: "still working on the earlier step", uuid: "a0" },
        { kind: "assistant", md: "…", uuid: "a-" + later, ts: new Date(later).toISOString() },
      ], list);
      assert.equal(r.keep.length, 1, `still pending at +${(later - T0) / 1000}s`);
      assert.equal(r.inject.length, 1, "…and still shown: nothing in the payload accounts for it");
    }
  } finally {
    Date.now = realNow;
  }
  assert.doesNotMatch(RENDER, /OPT_TTL_MS/, "the 20 s backstop is gone from render.ts");
});

test("it clears on the kernel's echo (suppressed) and ends on the landing (retired, with the index)", () => {
  const list = press([{ kind: "assistant", md: "…", uuid: "a1" }], TEXT);
  let r = reconcilePending([{ kind: "assistant", md: "…", uuid: "a1" }, { kind: "user", md: TEXT, uuid: "echo:1" }], list);
  assert.equal(r.keep.length, 1, "the kernel's provisional never retires the entry");
  assert.equal(r.inject.length, 1, "…and ours stays drawn: the echo is hidden for it, one bubble at the tail (T262h)");
  assert.deepEqual(r.echoHide, [1], "the caller hides the kernel's echo");
  r = reconcilePending([
    { kind: "assistant", md: "…", uuid: "a1" },
    { kind: "user", md: TEXT, uuid: "att1", absorbed: true },
    { kind: "tool", uuid: "t9" },
  ], list);
  assert.equal(r.keep.length, 0, "a landed user atom ends it");
  assert.deepEqual(r.landed.map((l) => l.idx), [1], "…naming the landed event: the slot the bubble held");
});

test("an image send lands even though the paths are gone from the text", () => {
  // the composer appends the dragged paths to the typed text; the CLI rewrites them to "[Image #N]" and the
  // kernel strips the placeholders and renders the pictures — so the sent text can never match verbatim
  const text = 'compare with the old one\n/tmp/notes-api/docs/before.png "/tmp/notes-api/docs/after one.png"';
  const imgs = ["/tmp/notes-api/docs/before.png", "/tmp/notes-api/docs/after one.png"];
  assert.equal(pendingBody(text, imgs), "compare with the old one");
  const p = newPending(text, imgs, T0);
  const landed: TailEvent = { kind: "user", uuid: "u7", md: "compare with the old one", images: [{}, {}] };
  assert.equal(landedIn(landed, p), true);
  assert.equal(landedIn({ kind: "user", uuid: "u8", md: "compare with the old one" }, p), false,
    "the body alone is not enough — the landing must carry the pictures");
  assert.equal(landedIn({ kind: "user", uuid: "u9", md: 'compare with the old one\n""', images: [{}, {}] }, p), true,
    "a quoted path's leftover quotes do not break the match");
  assert.equal(landedIn({ kind: "user", uuid: "u10", md: "compare with the old one, then the new", images: [{}] }, p), false,
    "exact body, not a substring");
  assert.equal(landedIn({ kind: "user", uuid: "u11", md: text }, p), true, "paths landing verbatim (an SDK session) match the text itself");
  reconcilePending([{ kind: "assistant", md: "…", uuid: "a1" }], [p]);   // the press: anchored on a tail without it
  const r = reconcilePending([{ kind: "assistant", md: "…", uuid: "a1" }, landed], [p]);
  assert.equal(r.keep.length, 0, "the pictures-bearing atom after the anchor is the landing");
});

test("the kernel's never-delivered verdict ends the entry — its bubble carries the text and the resend", () => {
  const list = press([{ kind: "assistant", md: "…", uuid: "a1" }], TEXT);
  const r = reconcilePending([{ kind: "assistant", md: "…", uuid: "a1" }, { kind: "user", md: TEXT, uuid: "echo:1", undelivered: true }], list);
  assert.equal(r.keep.length, 0);
  assert.equal(r.lost.length, 1);
  assert.equal(r.inject.length, 0, "never a 'sending…' bubble beside a 'never delivered' one");
  // the kernel's undelivered bubble is the resend path: copy to composer / dismiss (undelivered-echo.test.ts)
  assert.match(RENDER, /noticeAct\("copy to composer", "echorestore",/);   // a notice word button since 2026-09-08
});

test("a connection drop marks unconfirmed sends 'not confirmed' — an event, and reversible by a confirmation", () => {
  assert.match(RENDER, /window\.addEventListener\("romp:wsdown", \(\) => markPendingLost\("connection"\)\);/);
  assert.match(RENDER, /if \(m\.type === "pipeState"\) \{ if \(!m\.up\) markPendingLost\("connection"\);/,
    "the VS Code pipe's down edge too — it never fires romp:wsdown");
  assert.match(RENDER, /function markPendingLost\(why: string\): void \{[\s\S]{0,600}?for \(const p of list\) if \(!p\.lost && !p\.received\) \{ p\.lost = why; changed = true; \}/);
  // the bubble says so, in the bare group's own label and on the bubble; ✕ stays the way back
  assert.match(RENDER, /if \(t\.optimistic && t\.lost\) bubble\.title = "not confirmed — the connection dropped after this was sent; ✕ moves it back to the composer to send again";/);
  assert.match(CSS, /\.queued-head \.queued-count \.lost \{ color: var\(--warn\); \}/);
  // a confirmation after the reconnect retires it like any other: the flag is display-only
  const list = press([{ kind: "assistant", md: "…", uuid: "a1" }], "go ahead");
  list[0].lost = "connection";
  const r = reconcilePending([{ kind: "assistant", md: "…", uuid: "a1" }, { kind: "user", md: "go ahead", uuid: "u1" }], list);
  assert.equal(r.keep.length, 0);
});

// ── (4) the anchor: verdicts are read after the send, never from a tail count ─────────────────────

test("a landing 100 events above the tail still ends the bubble — the scan is bounded by the send's anchor, not a count", () => {
  const before: TailEvent[] = [{ kind: "user", md: "first ask", uuid: "u0" }, ...filler(12, "b"), { kind: "tool", uuid: "t-last" }];
  const list = press(before, TEXT);
  assert.equal(list[0].at?.after, "t-last", "anchored on the last stable kernel event at the press");
  list[0].lost = "connection";                   // the socket dropped while the turn ran on
  // the reconnect's full frame: the absorbed atom sits at its SEND time, a hundred events above the tail
  const after: TailEvent[] = [...before, { kind: "user", md: TEXT, uuid: "att1", absorbed: true }, ...filler(100, "g")];
  const r = reconcilePending(after, list);
  assert.equal(r.keep.length, 0, "the landing is after the anchor, wherever the tail has grown to");
  assert.deepEqual(r.landed.map((l) => l.idx), [before.length]);
  // the same depth for the kernel's never-delivered verdict
  const list2 = press(before, TEXT);
  const r2 = reconcilePending([...before, { kind: "user", md: TEXT, uuid: "echo:9", undelivered: true }, ...filler(100, "g")], list2);
  assert.equal(r2.lost.length, 1);
});

test("an anchor that left the resident window (the transcript grew past the wire tail) means everything resident is later", () => {
  const list = press([{ kind: "assistant", md: "…", uuid: "old-anchor" }], TEXT);
  // the next full frame is a re-windowed tail that no longer holds the anchor event
  const r = reconcilePending([...filler(40, "w"), { kind: "user", md: TEXT, uuid: "u-new" }, ...filler(5, "z")], list);
  assert.equal(r.keep.length, 0);
  assert.equal(r.landed[0].idx, 40);
  // and a send into an empty transcript has nothing to anchor on: the head is the start
  const list2 = press([], TEXT);
  assert.equal(list2[0].at?.after, null);
  assert.equal(reconcilePending([{ kind: "user", md: TEXT, uuid: "u1" }], list2).landed.length, 1);
});

test("what was already there at the press is background: an older identical message, a kernel echo, a queued copy", () => {
  const tail: TailEvent[] = [
    { kind: "user", md: TEXT, uuid: "u-old" },
    { kind: "assistant", md: "done", uuid: "a1" },
    { kind: "user", md: TEXT, uuid: "echo:prev" },
    { kind: "queued", texts: [{ md: TEXT }] },
  ];
  const list = press(tail, TEXT);
  assert.equal(list[0].at?.after, "a1", "the anchor skips the kernel's echo: it is replaced when its text lands");
  assert.deepEqual(list[0].at?.seen, ["u-old", "echo:prev"]);
  assert.equal(list[0].at?.queued, 1);
  const r = reconcilePending(tail, list);
  assert.equal(r.keep.length, 1, "not retired by the older copy");
  assert.equal(list[0].received, undefined, "the old echo and the old queued copy prove nothing about THIS send");
  assert.equal(r.inject.length, 1, "…and cover nothing: our bubble shows beside the older copies (two sends, two bubbles)");
});

// ── (5) the bubble sits at the TAIL (T252d): render.ts appends one bare group after every kernel event ──

// ── (T252c) identity from the kernel: qid on the queued copies and on the landed atom ─────────────

test("our own send's identity is minted at the press; the kernel's copies wearing it rule landing, cover and hiding (T252c)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const p = newPending("continue", undefined, T0);
  reconcilePending(tail, [p]);
  const c9 = p.qid!, c8 = "echo:" + "8".repeat(32);
  // the kernel lists our copy under the id we posted: the copy covers us, by id
  let r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "continue", qid: c9, qts: 5 }] }], [p]);
  assert.equal(p.qid, c9, "the id is the press's and stays");
  assert.deepEqual(r.unqueue, [p]);
  assert.equal(queuedCopyToHide([{ md: "continue", qid: c8 }, { md: "continue", qid: c9 }], "continue", c9), 1, "the copy to hide is OURS by id, not the newest by text");
  // another client's same-text copy landing does NOT retire us: only the atom carrying our id does
  r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "uX", qid: c8 }, { kind: "queued", texts: [{ md: "continue", qid: c9, qts: 5 }] }], [p]);
  assert.deepEqual(r.keep, [p], "a same-text landing with another id is not ours");
  r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "uX", qid: c8 }, { kind: "user", md: "continue", uuid: "uMine", qid: c9 }], [p]);
  assert.deepEqual(r.landed.map((l) => [l.p, l.idx]), [[p, 2]], "our landing, by id, even behind a same-text one");
  // the echo path: the echo's uuid IS the copy's id, the one we posted
  const q = newPending("go", undefined, T0 + 1);
  reconcilePending(tail, [q]);
  r = reconcilePending([...tail, { kind: "user", md: "go", uuid: q.qid }], [q]);
  assert.deepEqual([r.echoHide, q.received], [[1], true]);
  r = reconcilePending([...tail, { kind: "user", md: "go", uuid: "uG", qid: q.qid }], [q]);
  assert.deepEqual(r.landed.map((l) => l.idx), [1]);
  // a kernel that minted its own id for the copy (it took none from the press): read by text for the push, the
  // press's id kept, never the kernel's latched (a same-text copy another client queued later wears an id too)
  const k = newPending("go", undefined, T0 + 2);
  reconcilePending(tail, [k]);
  r = reconcilePending([...tail, { kind: "user", md: "go", uuid: "echo:" + "e".repeat(32) }], [k]);
  assert.deepEqual([r.echoHide, k.received, k.qid], [[1], true, k.qid], "covered by text for this push; the id is still the press's");
});

test("identity edges: a copy without an id beside an echo, a landing carrying several ids, and an identified copy whose landing lost its id (T252c review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  // the tmux route: the queued copy carries a stamp but no id, the echo a random uuid, the landing nothing — text decides
  // throughout, and our bubble never doubles beside the kernel's copies
  const p = newPending("go on", undefined, T0);
  reconcilePending(tail, [p]);
  const pressed = p.qid;
  let r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "go on", qts: 5 }] }], [p]);
  assert.equal(p.qid, pressed, "the press's id stays; the id-less copy covers by text");
  assert.deepEqual(r.unqueue, [p]);
  r = reconcilePending([...tail, { kind: "user", md: "go on", uuid: "echo:random" }], [p]);
  assert.deepEqual([r.inject.length, r.echoHide], [1, [1]], "the echo is attributed by text: hidden, ours stays (T262h)");
  r = reconcilePending([...tail, { kind: "user", md: "go on", uuid: "uL" }], [p]);
  assert.deepEqual(r.landed.map((l) => l.idx), [1]);
  // a record the CLI wrote from two sends lands with one id per block: ours retires on its own block's id
  const [x, y] = press(tail, "one", "two");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "one", qid: x.qid, qts: 1 }, { md: "two", qid: y.qid, qts: 2 }] }], [x, y]);
  r = reconcilePending([...tail, { kind: "user", md: "one two", uuid: "uXY", blocks: ["one", "two"], qids: [x.qid!, y.qid!] }], [x, y]);
  assert.deepEqual(r.landed.map((l) => [l.p.text, l.idx]), [["one", 1], ["two", 1]]);
});

test("queuedCopyToHide: the newest not-yet-hidden copy of the text, never a copy the kernel marked non-cancelable", () => {
  // the kernel marks a queued copy cancelable:false when no recall exists (a tmux queue): that copy stays the
  // one bubble shown, with its honest "can't be recalled" tooltip, and ours is suppressed as before — hiding it
  // behind our bubble's ✕ offered a cancel the kernel would refuse (review of the first cut)
  const texts = [{ md: "a" }, { md: "b", cancelable: false }, { md: "a", hiddenByPending: true }, { md: "a" }];
  assert.equal(queuedCopyToHide(texts, "a"), 3, "the newest visible copy");
  assert.equal(queuedCopyToHide(texts.slice(0, 3), "a"), 0, "…skipping one already hidden");
  assert.equal(queuedCopyToHide(texts, "b"), -1, "a non-cancelable copy is never hidden");
  assert.equal(queuedCopyToHide(texts, "zzz"), -1);
  assert.equal(queuedCopyToHide([{ md: " a " }], "a"), 0, "trimmed match, like every other text test");
  // render.ts: a copy the helper refuses keeps covering our bubble (the send leaves `inject`)
  assert.match(RENDER, /const k = queuedCopyToHide\(q\.texts, p\.text, p\.qid\);/);
  assert.match(RENDER, /if \(k < 0\) return null;\s*\/\/ no copy to hide/);
  assert.match(RENDER, /const hid = hideQueuedCopy\(s, p\);\s*\n\s*if \(hid === null\) covered\.add\(p\); else if \(hid\.held\) heldBy\.set\(p, hid\.held\);/);
  assert.match(RENDER, /const inject = r\.inject\.filter\(\(p\) => !covered\.has\(p\)\);/);
});

test("the kernel's queued copy at the tail is hidden for a send drawn as our own bubble — one bubble per message (T252)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, TEXT);
  const r = reconcilePending([...tail, { kind: "tool", uuid: "t1" }, { kind: "queued", texts: [{ md: TEXT }] }], list);
  assert.equal(list[0].received, true, "the kernel's copy proves receipt");
  assert.deepEqual(r.inject, [list[0]], "…but ours stays drawn, at the tail");
  assert.deepEqual(r.unqueue, [list[0]], "and the caller drops the kernel's tail copy for it");
  // an ECHO atom proves receipt and is hidden in turn (T262h): ours is the one bubble, at the tail, until the landing
  const r2 = reconcilePending([...tail, { kind: "user", md: TEXT, uuid: "echo:1" }], press(tail, TEXT));
  assert.equal(r2.inject.length, 1);
  assert.equal(r2.unqueue.length, 0);
  assert.deepEqual(r2.echoHide, [1]);
});

test("render.ts appends the pending sends as ONE bare group at the tail, in send order; strips its own injections before applying kernel indices; carries no header or cue (T252d)", () => {
  assert.match(RENDER, /s\.events\.push\(\{ kind: "queued", bare: true, texts: inject\.map\(mk\), uuid: OPT_PREFIX \+ inject\[0\]\.ts/,
    "one bare group pushed at the tail, the sends in list (send) order");
  assert.doesNotMatch(RENDER, /injectionGroups|placementIndex/, "the send-slot placement machinery is gone from render.ts");
  const fn = RENDER.split("function reconcileOptimisticInner(")[1].split("\nfunction ")[0];   // the guarded body (T262h)
  assert.match(fn, /settle\(inject\.map\(\(p\) => p\.text\)\);/, "the repaint signature is the texts alone: the tail slot moves with every push by design");
  assert.match(RENDER, /function stripOptimistic\(s: Session, keepHeld = false\): void \{/, "one strip, used by every ingest path (keepHeld: the pending reconcile leaves the held kernel copies in, T262i)");
  const tail = RENDER.slice(RENDER.indexOf("function chatTail(msg: any) {"), RENDER.indexOf("s.events.length = from;"));
  assert.match(tail, /stripOptimistic\(s\);/, "the delta's kernel index is applied to KERNEL events only — a mid-array bubble would shift it");
  // …but only once the delta is going to be applied: stripping before the gap/head early returns left s.events
  // without the bubble while the DOM still showed it (review of the first cut)
  const afterReturns = tail.slice(tail.indexOf("if (from < 0) return;"));
  assert.match(afterReturns, /stripOptimistic\(s\);/, "the strip sits after both early returns");
  assert.doesNotMatch(tail.slice(0, tail.indexOf("if (from > kernelLen) {")), /stripOptimistic\(s\);/, "…and not before the gap check");
  assert.match(tail, /const kernelLen = s\.events\.reduce\(\(n, e\) => n \+ \(isOptimistic\(e\) \|\| isHeldGroup\(e\) \? 0 : 1\), 0\);/, "the gap check counts kernel events without mutating");
  for (const gone of ["absorbedHeader", "absorbedCues", "noteAbsorbedLanding", "renderAbsorbedCue", "appendAbsorbedCues", "dismissAbsorbedCue", "cueRendered", "abjump", "abdismiss", "absorbed-tag", "turn-absorbed-cue"])
    assert.ok(!RENDER.includes(gone), gone + " is gone from render.ts");
  for (const gone of [".absorbed-tag", ".turn-absorbed-cue", ".absorbed-cue-line", ".absorbed-cue-act"])
    assert.ok(!CSS.includes(gone), gone + " is gone from styles.css");
  // the "not confirmed" state and the ✕ on the pending bubble itself stay
  assert.match(RENDER, /if \(t\.optimistic && t\.lost\) bubble\.dataset\.lost = "1";/);
  assert.match(RENDER, /if \(t\.optimistic\) x\.dataset\.qopt = "1";/);
});

test("a resend of a never-delivered message is not retired by the old verdict — only a verdict after the send counts", () => {
  // the flow the never-delivered bubble offers: copy to composer, Enter — the old bubble is still in the tail
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }, { kind: "user", md: TEXT, uuid: "echo:old", undelivered: true }];
  const list = press(tail, TEXT);
  let r = reconcilePending(tail, list);
  assert.equal(r.lost.length, 0, "the old verdict is about the old send");
  assert.equal(r.keep.length, 1);
  assert.equal(r.inject.length, 1, "…and the old verdict is not a provisional either: our bubble shows");
  assert.equal(provisionalIn(tail[1], list[0]), false, "a never-delivered echo never suppresses");
  // the kernel's echo for the resend, then ITS verdict — that one ends the entry
  r = reconcilePending([...tail, { kind: "user", md: TEXT, uuid: "echo:new" }], list);
  assert.deepEqual([r.inject.length, r.echoHide], [1, [tail.length]], "ours stays, the echo is hidden (T262h)");
  r = reconcilePending([...tail, { kind: "user", md: TEXT, uuid: "echo:new", undelivered: true }], list);
  assert.equal(r.lost.length, 1);
  // a shorter send that the old never-delivered text merely contains is not its resend at all
  const list2 = press([{ kind: "user", md: "ok, go ahead and rename it", uuid: "echo:old", undelivered: true }], "go ahead");
  assert.equal(reconcilePending([{ kind: "user", md: "ok, go ahead and rename it", uuid: "echo:old", undelivered: true }], list2).keep.length, 1);
});

// ── (7) exact text, one landing per send ─────────────────────────────────────────────────────────

test("two identical sends in flight: the first landing retires the first, the second the second — across pushes", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, "continue", "continue");
  let r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u1", absorbed: true }, { kind: "tool", uuid: "t1" }], list);
  assert.equal(r.landed.length, 1, "one landing, one entry");
  assert.equal(r.landed[0].p, list[0], "…the earlier send");
  assert.equal(r.keep.length, 1);
  assert.equal(r.inject.length, 1, "the second still shows: its message has not landed");
  // the next push (the first entry is gone from the list): the claimed landing stays claimed
  r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u1", absorbed: true }, { kind: "tool", uuid: "t1" }], r.keep);
  assert.equal(r.keep.length, 1, "the first send's landing does not retire the second");
  r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u1", absorbed: true }, { kind: "tool", uuid: "t1" }, { kind: "user", md: "continue", uuid: "u2" }], r.keep);
  assert.equal(r.landed.length, 1);
  assert.equal(r.landed[0].idx, 3, "the second landing is the second send's");
  // both landing in one push: attributed in order
  const list2 = press(tail, "continue", "continue");
  r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u1" }, { kind: "user", md: "continue", uuid: "u2" }], list2);
  assert.deepEqual(r.landed.map((l) => [l.p, l.idx]), [[list2[0], 1], [list2[1], 2]]);
});

test("exact text: a pending 'test' is not retired when 'test the continue button' lands, nor the reverse", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, "test", "test the continue button");
  let r = reconcilePending([...tail, { kind: "user", md: "test the continue button", uuid: "u1" }], list);
  assert.deepEqual(r.landed.map((l) => l.p.text), ["test the continue button"]);
  assert.deepEqual(r.keep.map((p) => p.text), ["test"]);
  r = reconcilePending([...tail, { kind: "user", md: "test the continue button", uuid: "u1" }, { kind: "user", md: "test", uuid: "u2" }], r.keep);
  assert.deepEqual(r.landed.map((l) => [l.p.text, l.idx]), [["test", 2]]);
  // edge whitespace is the one tolerance: the composer trims, the kernel strips
  assert.equal(landedIn({ kind: "user", md: "  test\n", uuid: "u3" }, newPending("test")), true);
  // the kernel's echo and queued bubble are matched the same way
  const p = newPending("test");
  assert.equal(provisionalIn({ kind: "user", md: "test the continue button", uuid: "echo:1" }, p), false);
  assert.equal(provisionalIn({ kind: "queued", texts: [{ md: "test the continue button" }] }, p), false);
  assert.equal(provisionalIn({ kind: "queued", texts: [{ md: "test" }] }, p), true);
});

// ── (8) a connection drop repaints once ──────────────────────────────────────────────────────────

test("markPendingLost repaints only when an entry actually changed — the shim fires the down edge on every redial", () => {
  const fn = RENDER.slice(RENDER.indexOf("function markPendingLost("), RENDER.indexOf("\n}\n", RENDER.indexOf("function markPendingLost(")));
  assert.match(fn, /let changed = false;/);
  assert.match(fn, /if \(!changed\) continue;/, "an unchanged session is neither reconciled nor marked stale");
  assert.match(fn, /if \(sid === activeId\) activeChanged = true;/);
  assert.match(fn, /if \(activeChanged\) appendActive\(\);/, "the active chat rebuilds only for its own change");
  assert.doesNotMatch(fn, /if \(activeId && pendingSent\.has\(activeId\)\) appendActive\(\);/);
});

// ── (9) the bare label is per bubble ─────────────────────────────────────────────────────────────

test("the bare group's label counts lost and sending bubbles separately", () => {
  const texts = (n: number, m: number) => bareGroupLabel(n, m).parts.map((p) => (p.lost ? "!" : "") + p.text);
  assert.deepEqual(texts(0, 1), ["sending…"]);
  assert.deepEqual(texts(0, 3), ["sending 3…"]);
  assert.deepEqual(texts(1, 0), ["!not confirmed"]);
  assert.deepEqual(texts(2, 0), ["!2 not confirmed"]);
  assert.deepEqual(texts(1, 1), ["!not confirmed", "sending…"], "a mixed group names both states, the lost one first");
  assert.deepEqual(texts(1, 2), ["!not confirmed", "sending 2…"]);
  assert.match(bareGroupLabel(1, 0).title, /^The connection dropped after this was sent/);
  assert.match(bareGroupLabel(0, 1).title, /^on its way to the session/);
  assert.match(bareGroupLabel(1, 1).title, /^The connection dropped after this was sent[\s\S]*The rest: on its way/);
  // render.ts builds the label from the bubbles' own states, and the ✕'s recount reads them back off the
  // surviving bubbles (data-lost) — never off the label's previous class
  assert.match(RENDER, /const nLost = texts\.filter\(\(t\) => t\.lost\)\.length;\s*\n\s*fillBareLabel\(label, nLost, texts\.length - nLost\);/);
  assert.match(RENDER, /if \(t\.optimistic && t\.lost\) bubble\.dataset\.lost = "1";/);
  assert.match(RENDER, /const nLost = bubbles\.filter\(\(b\) => \(b as HTMLElement\)\.dataset\.lost === "1"\)\.length;\s*\n\s*fillBareLabel\(label, nLost, bubbles\.length - nLost\);/);
  assert.doesNotMatch(RENDER, /label\.classList\.contains\("lost"\)/);
  assert.match(RENDER, /const span = el\("span", part\.lost \? "lost" : ""\);/, "only the lost part wears the warn color");
});

// ── (10) the kernel's own copy clears "not confirmed" ────────────────────────────────────────────

test("the kernel's echo or queued copy seen after the press proves receipt: 'not confirmed' clears and never returns", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, TEXT);
  list[0].lost = "connection";                                   // the drop, as markPendingLost writes it
  let r = reconcilePending([...tail, { kind: "user", md: TEXT, uuid: "echo:1" }], list);
  assert.equal(list[0].lost, undefined, "the kernel minted this echo on receipt of the send: the drop did not lose it");
  assert.equal(list[0].received, true);
  r = reconcilePending(tail, list);                              // the echo blinks in its echo→landed handoff
  assert.equal(r.inject.length, 1);
  assert.equal(r.inject[0].lost, undefined, "…and our bubble steps back in as 'sending…', not as a false loss");
  // a queued copy that was not listed at the press is the same proof
  const list2 = press([{ kind: "queued", texts: [{ md: TEXT }] }], TEXT);
  list2[0].lost = "connection";
  reconcilePending([{ kind: "queued", texts: [{ md: TEXT }] }], list2);
  assert.equal(list2[0].lost, "connection", "one copy at the press, one copy now: nothing new");
  reconcilePending([{ kind: "queued", texts: [{ md: TEXT }, { md: TEXT }] }], list2);
  assert.equal(list2[0].lost, undefined, "a second copy is this send's");
  // a later drop leaves a received send alone (render.ts markPendingLost's guard)
  assert.match(RENDER, /if \(!p\.lost && !p\.received\) \{ p\.lost = why; changed = true; \}/);
});

// ── (11) receipt is attributed per send, like landings ───────────────────────────────────────────

test("two identical sends, one kernel echo: the first is received and covered; the second still shows, still unconfirmed", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, "continue", "continue");
  let r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "echo:1" }], list);
  assert.deepEqual(list.map((p) => p.received), [true, undefined], "one echo is one send's receipt");
  assert.deepEqual(r.inject, [list[0], list[1]], "both bubbles stay ours; the echo that proved the first's receipt is hidden (T262h)");
  assert.deepEqual(r.echoHide, [1]);
  assert.deepEqual(list[1].at?.seen, ["echo:1"], "the claimed echo is background for the later send");
  // the drop (as render.ts markPendingLost writes it): only the unconfirmed send is marked
  for (const p of list) if (!p.lost && !p.received) p.lost = "connection";
  assert.deepEqual(list.map((p) => p.lost), [undefined, "connection"]);
  // the same echo on later pushes proves nothing new — even once the first entry is gone (its ✕)
  r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "echo:1" }], [list[1]]);
  assert.equal(list[1].received, undefined);
  assert.equal(list[1].lost, "connection", "the bubble the kernel never received keeps saying so");
  assert.equal(r.inject.length, 1);
  // a second echo is the second send's
  reconcilePending([...tail, { kind: "user", md: "continue", uuid: "echo:1" }, { kind: "user", md: "continue", uuid: "echo:2" }], list);
  assert.deepEqual(list.map((p) => p.received), [true, true]);
  assert.equal(list[1].lost, undefined);
  // the claimed echo's verdict is the claimant's too: its never-delivered flag ends the first send only
  const list2 = press(tail, "continue", "continue");
  reconcilePending([...tail, { kind: "user", md: "continue", uuid: "echo:1" }], list2);
  r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "echo:1", undelivered: true }], list2);
  assert.deepEqual(r.lost, [list2[0]]);
  assert.deepEqual(r.keep, [list2[1]]);
});

test("both 'not confirmed', one echo arrives: one label clears, the other stays — and the landing retires the received one", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, "continue", "continue");
  list[0].lost = list[1].lost = "connection";
  reconcilePending([...tail, { kind: "user", md: "continue", uuid: "echo:1" }], list);
  assert.deepEqual(list.map((p) => p.lost), [undefined, "connection"]);
  const r = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u1" }], list);   // the echo→landed handoff
  assert.deepEqual(r.landed.map((l) => l.p), [list[0]]);
  assert.deepEqual(r.keep, [list[1]]);
  assert.equal(list[1].lost, "connection", "still unconfirmed: the kernel has shown one copy, and it landed");
});

test("queued copies are handed out by position: one new copy confirms one send; the copies a press listed are its background", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, "continue", "continue");
  let r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "continue" }] }], list);
  assert.deepEqual(list.map((p) => p.received), [true, undefined]);
  assert.deepEqual(r.inject, [list[0], list[1]], "both drawn in place (T252); the kernel's one copy is hidden for the first");
  assert.deepEqual(r.unqueue, [list[0]]);
  r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "continue" }, { md: "continue" }] }], list);
  assert.deepEqual(list.map((p) => p.received), [true, true]);
  assert.deepEqual(r.unqueue, [list[0], list[1]], "two kernel copies, both hidden: two bubbles for two sends, in place");
  // a second press that already saw the first send's copy counts it as background, not as its own
  const a = press(tail, "continue");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "continue" }] }], a);
  const b = newPending("continue", undefined, T0 + 1);
  const both = [a[0], b];
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "continue" }] }], both);   // b's press: one copy listed already
  assert.equal(b.at?.queued, 1);
  assert.equal(b.received, undefined, "the copy b's press listed is a's");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "continue" }, { md: "continue" }] }], both);
  assert.deepEqual(both.map((p) => p.received), [true, true]);
});

// ── (12) the ✕ removes the bubble's OWN entry ────────────────────────────────────────────────────

test("✕ on one of two identical bubbles removes that bubble's entry, never the first with the text", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const A = newPending("continue", undefined, T0), B = newPending("continue", undefined, T0 + 5000);
  const list = [A, B];
  reconcilePending(tail, list);
  A.lost = "connection";                                   // A dropped; B was sent after the reconnect
  let r = reconcilePending(tail, list);
  assert.deepEqual(r.inject.map((p) => p.lost), ["connection", undefined], "the group reads 'not confirmed · sending…'");
  // the ✕ on B's bubble names B (data-qts) — the first-with-the-text lookup took A
  assert.equal(dropPending(list, "continue", B.ts), B);
  assert.deepEqual(list, [A]);
  r = reconcilePending(tail, list);
  assert.deepEqual(r.inject.map((p) => p.lost), ["connection"], "the dismissed bubble stays gone; the lost one stays put");
  // the ✕ on the LOST bubble of the pair
  const C = newPending("continue", undefined, T0), D = newPending("continue", undefined, T0 + 5000);
  const list2 = [C, D];
  reconcilePending(tail, list2);
  C.lost = "connection";
  assert.equal(dropPending(list2, "continue", C.ts), C);
  assert.deepEqual(list2, [D]);
  // a bubble whose entry a push already retired removes nothing — never a neighbour with the same text
  assert.equal(dropPending(list2, "continue", T0 + 999), undefined);
  assert.deepEqual(list2, [D]);
  // a ✕ on the KERNEL's own queued copy names no entry of ours: the first pending send with the text goes
  assert.equal(dropPending(list2, "continue"), D);
  assert.deepEqual(list2, []);
  // render.ts: the identity rides the bubble's ✕, and the handler removes by it
  assert.match(RENDER, /const mk = \(p: PendingSend\) => \(\{ md: p\.text, optimistic: true, cancelable: true, imgPaths: p\.imgPaths, lost: p\.lost, qts: p\.ts, qid: p\.qid \}\);/);
  assert.match(RENDER, /if \(el\.dataset\.qid\) msg\.qid = el\.dataset\.qid;/, "the cancel names the copy's id");
  assert.match(RENDER, /if \(t\.optimistic && t\.qts !== undefined\) x\.dataset\.qts = String\(t\.qts\);/);
  assert.match(RENDER, /const qts = el\.dataset\.qts !== undefined \? Number\(el\.dataset\.qts\) : undefined;\s*\n\s*const qid = el\.dataset\.qid \|\| undefined;\s*\n\s*if \(dropPending\(list, qmd, qts, qid\)\) \{ if \(list\.length\) pendingSent\.set\(sidQ, list\); else pendingSent\.delete\(sidQ\); \}/);
  assert.doesNotMatch(RENDER, /list\.findIndex\(\(p\) => p\.text === qmd\)/);
});

// ── (13) a late stamp reads the events' own times ────────────────────────────────────────────────

test("a send pressed against no frame (a placeholder tab): the first frame's copy of it is this send's, not background", () => {
  const isoAt = (s: number) => new Date(s * 1000).toISOString();   // kernel.py iso(t): ISO-8601 UTC, whole seconds
  const pressMs = T0 + 250;                                          // the press, on the client's clock
  const S = Math.floor(pressMs / 1000);
  const late = (): PendingSend => ({ ...newPending(TEXT, undefined, pressMs), late: true });   // registerOptimistic, no resident session
  // the first frame holds an older identical message, the last step before the send, this send's own
  // echo, and a step after it
  const frame: TailEvent[] = [
    { kind: "user", md: TEXT, uuid: "u-old", ts: isoAt(S - 3600) },
    { kind: "assistant", md: "…", uuid: "a1", ts: isoAt(S - 2) },
    { kind: "user", md: TEXT, uuid: "echo:1", ts: isoAt(S) },     // the kernel stamps the echo at its receipt: the press's second, or later
    { kind: "assistant", md: "…", uuid: "a2", ts: isoAt(S + 1) },
  ];
  let list = [late()];
  let r = reconcilePending(frame, list);
  assert.equal(list[0].at?.after, "a1", "the anchor is the last stable event stamped BEFORE the press — not a2");
  assert.deepEqual(list[0].at?.seen, ["u-old"], "the old message is background; this send's echo is not");
  assert.equal(list[0].received, true);
  assert.deepEqual([r.inject.length, r.echoHide], [1, [2]], "the kernel's echo is hidden for our bubble: no double bubble (T262h)");
  const landed: TailEvent[] = [frame[0], frame[1], { kind: "user", md: TEXT, uuid: "u1", ts: isoAt(S) }, frame[3]];
  r = reconcilePending(landed, list);
  assert.equal(r.keep.length, 0, "the echo→landed handoff retires it");
  // the CLI was idle: the first frame already holds the LANDED atom
  list = [late()];
  r = reconcilePending(landed, list);
  assert.equal(list[0].at?.after, "a1", "…and the landed atom is not the anchor either");
  assert.deepEqual(r.landed.map((l) => l.idx), [2], "it is the landing: the bubble ends, instead of never ending");
  // a first frame that predates the send entirely stamps exactly as a press-time stamp would
  list = [late()];
  reconcilePending([frame[0], frame[1]], list);
  assert.deepEqual(list[0].at, { after: "a1", seen: ["u-old"], queued: 0 });
  // a press-time stamp reads no stamp: its frame predates the press by construction, so an identical
  // message that landed within the press's own second is still background
  const prompt = press([frame[1], { kind: "user", md: TEXT, uuid: "u-same-second", ts: isoAt(Math.floor(T0 / 1000)) }], TEXT);
  assert.deepEqual(prompt[0].at?.seen, ["u-same-second"]);
  assert.equal(prompt[0].at?.after, "u-same-second");
  // render.ts marks the entry when the press finds no resident session, and stamps it nowhere else
  assert.match(RENDER, /const p = newPending\(text, imgPaths, Date\.now\(\), qid\);\s*\n\s*arr\.push\(p\);/);
  assert.match(RENDER, /if \(!s\) \{ p\.late = true; return; \}/);
  // the clock the bound compares against: the kernel stamps the echo atom at its receipt of the send, in
  // whole seconds (sdk_backend.py send). That the chat builder ships every event's stamp as iso(t) is
  // proven behaviourally, not pinned here: tests/test_kernel_fed_echo_absorbed.py (ChatEventSaysAbsorbed)
  // reads a built user event's ts back as iso(t) of its atom.
  const SDK = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");
  assert.match(SDK, /sent_t = int\(time\.time\(\)\)/);
  assert.match(SDK, /"t": sent_t\b/);
});

test("a late stamp presumes the first frame's newest queued copy of the text is this send's own (busy and held queues)", () => {
  const isoAt = (s: number) => new Date(s * 1000).toISOString();
  const pressMs = T0 + 250;
  const S = Math.floor(pressMs / 1000);
  const late = (): PendingSend => ({ ...newPending(TEXT, undefined, pressMs), late: true });
  const step: TailEvent = { kind: "assistant", md: "…", uuid: "a1", ts: isoAt(S - 2) };
  // the queued bubble carries no stamp: a busy session's queue (the CLI holds the text) and a held one
  // (an account limit; the kernel holds it, for hours) look the same to the stamp
  for (const q of [{ kind: "queued", texts: [{ md: TEXT }] }, { kind: "queued", texts: [{ md: TEXT }], held: { reason: "limit" } }] as TailEvent[]) {
    const list = [late()];
    let r = reconcilePending([step, q], list);
    assert.equal(list[0].at?.queued, 0, "the frame's one copy is presumed this press's, not background");
    assert.equal(r.inject.length, 1, "ours stays drawn at its send slot (T252)…");
    assert.equal(r.unqueue.length, 1, "…and the kernel's copy is the one hidden: no double bubble");
    assert.equal(list[0].received, true);
    // every later push, the same copy keeps proving receipt — the entry never sat unconfirmed until the CLI took it
    r = reconcilePending([step, q], list);
    assert.equal(r.inject.length, 1);
    assert.equal(r.unqueue.length, 1);
    // …and when the CLI takes the text, the landing ends the bubble (a tmux route: no echo in between)
    r = reconcilePending([step, { kind: "user", md: TEXT, uuid: "u1", ts: isoAt(S + 40) }], list);
    assert.deepEqual(r.landed.map((l) => l.idx), [1]);
  }
  // an older identical copy queued BEFORE the press sits at the head: the newest copy is ours, the older
  // one is background, so a second frame with a third copy would prove nothing about this send
  const list = [late()];
  reconcilePending([step, { kind: "queued", texts: [{ md: TEXT }, { md: TEXT }] }], list);
  assert.equal(list[0].at?.queued, 1);
  // the frame was built before the kernel received the send: no copy yet, none presumed; the copy that
  // follows covers it, exactly as at a press-time stamp
  const early = [late()];
  let r = reconcilePending([step], early);
  assert.deepEqual(early[0].at, { after: "a1", seen: [], queued: 0 });
  assert.equal(r.inject.length, 1);
  r = reconcilePending([step, { kind: "queued", texts: [{ md: TEXT }] }], early);
  assert.equal(r.inject.length, 1);
  assert.equal(r.unqueue.length, 1, "the copy that follows is this send's: hidden, ours drawn, receipt proven");
  assert.equal(early[0].received, true);
  // two identical sends pressed against the same placeholder frame own one copy EACH — and when the frame
  // lists only one of them (the kernel had not received the second), the second waits for its own
  const pair = [late(), late()];
  r = reconcilePending([step, { kind: "queued", texts: [{ md: TEXT }, { md: TEXT }] }], pair);
  assert.deepEqual(pair.map((p) => p.at?.queued), [0, 0]);
  assert.equal(r.inject.length, 2, "both copies are the pair's: both hidden, both of ours drawn");
  assert.equal(r.unqueue.length, 2);
  const pair2 = [late(), late()];
  r = reconcilePending([step, { kind: "queued", texts: [{ md: TEXT }] }], pair2);
  assert.deepEqual(pair2.map((p) => p.at?.queued), [0, 0], "fewer copies than presses: the count floors at zero");
  assert.equal(r.inject.length, 2, "both drawn; one copy proves one send, the other waits for its own");
  assert.equal(r.unqueue.length, 1);
  r = reconcilePending([step, { kind: "queued", texts: [{ md: TEXT }, { md: TEXT }] }], pair2);
  assert.equal(r.unqueue.length, 2);
  // a press-time stamp presumes nothing: its frame predates the press, so every listed copy is background
  const prompt = press([step, { kind: "queued", texts: [{ md: TEXT }] }], TEXT);
  assert.equal(prompt[0].at?.queued, 1);
  // the ✕ on the kernel's copy (which names no entry) drops the one it covers — ours — so cancelling the
  // queued send leaves no orphan bubble behind
  const covered = [late()];
  reconcilePending([step, { kind: "queued", texts: [{ md: TEXT }] }], covered);
  const mine = covered[0];
  assert.equal(dropPending(covered, TEXT), mine);
  assert.equal(covered.length, 0);
});

test("every composer send posts a clientDiag breadcrumb — sid, time, length, route; never the text", () => {
  const m = RENDER.match(/vscodeApi\.postMessage\(\{ type: "clientDiag", surface: "chat", what: "send",\s*\n\s*data: \{ ([^}]*) \} \}\);/);
  assert.ok(m, "the breadcrumb is posted from routeUserMessage");
  const fields = m![1];
  assert.match(fields, /\bsid\b/); assert.match(fields, /ts: Date\.now\(\)/); assert.match(fields, /len: text\.length/); assert.match(fields, /route:/);
  assert.doesNotMatch(fields, /\btext\b(?!\.length)/, "the text itself never leaves the client");
  assert.doesNotMatch(fields, /\bbody\b|\bmd\b/);
  // one owner: the breadcrumb sits in routeUserMessage, which every send path (plain, quote, follow-up, staged flush) goes through
  const fn = RENDER.slice(RENDER.indexOf("function routeUserMessage("), RENDER.indexOf("\n}\n", RENDER.indexOf("function routeUserMessage(")));
  assert.ok(fn.includes('what: "send"'));
});

// ── (15) one record, several sends ───────────────────────────────────────────────────────────────

test("two back-to-back sends the CLI took as ONE record retire both bubbles; a third with other text stays", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const list = press(tail, "continue", "continue", "and the docstring");
  // the kernel's user event for the batched record: md is the blocks joined; `blocks` lists each send
  const rec: TailEvent = { kind: "user", md: "continue continue", uuid: "u1", blocks: ["continue", "continue"] };
  const r = reconcilePending([...tail, rec], list);
  assert.deepEqual(r.landed.map((l) => [l.p, l.idx]), [[list[0], 1], [list[1], 1]], "one copy per block, in send order");
  assert.deepEqual(r.keep, [list[2]], "the third send's text is in no block");
  assert.equal(landedIn(rec, list[2]), false);
  assert.equal(landedIn(rec, list[0]), true);
  // two sends of DIFFERENT texts batched the same way: each block is its own send's landing
  const list2 = press(tail, "continue", "and the docstring");
  const rec2: TailEvent = { kind: "user", md: "continue and the docstring", uuid: "u2", blocks: ["continue", "and the docstring"] };
  const r2 = reconcilePending([...tail, rec2], list2);
  assert.deepEqual(r2.landed.map((l) => [l.p.text, l.idx]), [["continue", 1], ["and the docstring", 1]]);
  // a record of two copies that was already there at the press is background twice over
  const list3 = press([...tail, rec], "continue");
  assert.deepEqual(list3[0].at?.seen, ["u1", "u1"], "seen counts copies, not events");
  assert.equal(reconcilePending([...tail, rec], list3).keep.length, 1, "neither copy is this send's");
  // across pushes: the first of three identical sends retired on its own record earlier; the two-block
  // record then retires exactly the two that remain — the claims on one record are counted
  const list4 = press(tail, "continue", "continue", "continue");
  let r4 = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u0" }], list4);
  assert.deepEqual(r4.landed.map((l) => l.p), [list4[0]]);
  r4 = reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u0" }, rec], r4.keep);
  assert.deepEqual(r4.landed.map((l) => l.p), [list4[1], list4[2]]);
  assert.equal(r4.keep.length, 0);
  // a fourth identical send pressed later finds every copy already there: the record of two is two
  const list5 = press([...tail, { kind: "user", md: "continue", uuid: "u0" }, rec], "continue");
  assert.deepEqual(list5[0].at?.seen, ["u0", "u1", "u1"], "the press lists the two-block record twice");
  assert.equal(reconcilePending([...tail, { kind: "user", md: "continue", uuid: "u0" }, rec], list5).keep.length, 1);
  // the same counting for the kernel's copies: a record of several sends is never an echo (one text each)
  assert.equal(provisionalIn({ kind: "user", md: "continue continue", uuid: "echo:1", blocks: ["continue", "continue"] }, { ...list[0], qid: undefined }), true,
    "…but were one ever shipped with blocks, its copies would be read the same way (by text: the entry's own id shows nowhere in it, the view reconcilePending reads such a push with)");
});

// ── (16) at a late stamp the send's id outranks the clock ────────────────────────────────────────

test("a late stamp reads an event that NAMES the send as this send's, whatever its stamp: a client clock ahead of the kernel", () => {
  const isoAt = (s: number) => new Date(s * 1000).toISOString();   // kernel.py iso(t): ISO-8601 UTC, whole seconds
  const pressMs = T0 + 200;                                          // the press, on the client's clock
  const S = Math.floor(pressMs / 1000);
  const late = (): PendingSend => ({ ...newPending(TEXT, undefined, pressMs), late: true });   // registerOptimistic, no resident session
  const step: TailEvent = { kind: "assistant", md: "…", uuid: "a1", ts: isoAt(S - 10) };
  // The client's clock runs a second ahead of the kernel's, so the kernel stamps this send's own records at
  // S - 1, BEFORE the press's second: read by stamp alone, every one of them is background. Each record below
  // wears the send's id (newPending minted it, so the echo's uuid is built from the entry), and the id decides.
  // (a) the kernel's echo, whose uuid IS the id
  let list = [late()];
  let p = list[0];
  let r = reconcilePending([step, { kind: "user", md: TEXT, uuid: p.qid!, ts: isoAt(S - 1) }], list);
  assert.deepEqual(p.at?.seen, [], "the echo wears this send's id: not background, whatever its stamp");
  assert.equal(p.at?.after, "a1");
  assert.deepEqual([r.inject.length, r.echoHide], [1, [1]], "the echo covers ours and is hidden: one bubble, not two");
  assert.equal(p.received, true, "the kernel has this send");
  // (b) the landed atom, wearing the id
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: "u-landed", ts: isoAt(S - 1), qid: p.qid }], list);
  assert.equal(p.at?.after, "a1", "the send's own landing is not its anchor");
  assert.deepEqual(r.landed.map((l) => l.idx), [1], "it is the landing: the bubble ends, instead of never ending");
  // (c) the same, with a reply after it stamped inside the same skew
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: "u-landed", ts: isoAt(S - 1), qid: p.qid }, { kind: "assistant", md: "done", uuid: "a2", ts: isoAt(S - 1) }], list);
  assert.equal(p.at?.after, "a1", "nothing at or after the send's own record anchors it, whatever its stamp");
  assert.deepEqual(r.landed.map((l) => l.idx), [1]);
  // (d) the landing as the frame's only event
  list = [late()]; p = list[0];
  r = reconcilePending([{ kind: "user", md: TEXT, uuid: "u-landed", ts: isoAt(S - 1), qid: p.qid }], list);
  assert.equal(p.at?.after, null, "no anchor: the frame holds nothing before the send");
  assert.deepEqual(r.landed.map((l) => l.idx), [0]);
  // (e) a record of several sends, one of whose blocks wears the id
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT + " " + TEXT, blocks: [TEXT, TEXT], qids: [p.qid!, "echo:" + "f".repeat(32)], uuid: "uXY", ts: isoAt(S - 1) }], list);
  assert.equal(p.at?.after, "a1");
  assert.deepEqual(r.landed.map((l) => l.idx), [1]);
  // (f) the kernel's never-delivered verdict: the echo wearing the id, flagged
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: p.qid!, ts: isoAt(S - 1), undelivered: true }], list);
  assert.deepEqual(r.lost, [p], "the verdict on this send is read, not filed as an old bubble");
  // (g) a same-text record AFTER the one that names the send, stamped inside the same skew, is after the
  // send too: neither background nor the anchor
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: "u-landed", ts: isoAt(S - 1), qid: p.qid }, { kind: "user", md: TEXT, uuid: "u-after", ts: isoAt(S - 1) }], list);
  assert.equal(p.at?.after, "a1");
  assert.deepEqual(p.at?.seen, [], "nothing from the send's own record on is background, whatever its stamp");
  assert.deepEqual(r.landed.map((l) => l.idx), [1]);
  // (h) two records naming the send (the kernel shows one per send; the rule is stated for the first): the
  // FIRST bounds the frame
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: p.qid!, ts: isoAt(S - 1) }, { kind: "user", md: TEXT, uuid: "u-landed", ts: isoAt(S - 1), qid: p.qid }], list);
  assert.equal(p.at?.after, "a1");
  assert.deepEqual(p.at?.seen, [], "the first record naming the send bounds the background, not the last");
  // The stamp bound still decides for a record that carries no id, or another send's, and for an entry
  // that names nothing:
  // (i) a same-text record wearing ANOTHER send's id, stamped early, is background by text
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: "u-other", ts: isoAt(S - 1), qid: "echo:" + "e".repeat(32) }], list);
  assert.equal(p.at?.after, "u-other");
  assert.deepEqual(p.at?.seen, ["u-other"]);
  assert.deepEqual([r.keep.length, r.inject.length], [1, 1]);
  // (j) an id-less record stamped early (an older kernel, the tmux route) is background
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: "u-old", ts: isoAt(S - 1) }], list);
  assert.equal(p.at?.after, "u-old");
  assert.deepEqual(p.at?.seen, ["u-old"]);
  assert.deepEqual([r.keep.length, r.inject.length], [1, 1]);
  // (k) only the kernel's USER records name the send: every event carries a uuid, and one of another kind
  // that wore the id (no kernel event does) would neither bound the frame nor anchor the send
  list = [late()]; p = list[0];
  r = reconcilePending([step, { kind: "assistant", md: "…", uuid: p.qid!, ts: isoAt(S - 5) }, { kind: "user", md: TEXT, uuid: "u-old", ts: isoAt(S - 1) }], list);
  assert.equal(p.at?.after, "u-old", "an event of another kind names nothing: the stamp bound alone decides");
  assert.deepEqual(p.at?.seen, ["u-old"]);
  // (l) an entry with no id (older data; newPending always mints one) names nothing: a same-text id-less
  // record stamped early is background for it, never its landing
  list = [{ ...late(), qid: undefined }]; p = list[0];
  r = reconcilePending([step, { kind: "user", md: TEXT, uuid: "u-old", ts: isoAt(S - 1) }], list);
  assert.equal(p.at?.after, "u-old", "an entry with no id names nothing: the stamp bound alone decides");
  assert.deepEqual(p.at?.seen, ["u-old"]);
  assert.equal(r.keep.length, 1);
});

// ── (T252c, second review) identity decides where the frame SHOWS it; text decides where it does not ──────

test("two identical identified sends the CLI took as one record both retire on that landing (T252c second review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const [p1, p2] = press(tail, "ok", "ok");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: p1.qid, qts: 1 }, { md: "ok", qid: p2.qid, qts: 2 }] }], [p1, p2]);
  const landing: TailEvent[] = [...tail, { kind: "user", md: "ok ok", uuid: "uXY", blocks: ["ok", "ok"], qids: [p1.qid!, p2.qid!] }];
  const r = reconcilePending(landing, [p1, p2]);
  assert.deepEqual(r.landed.map((l) => [l.p, l.idx]), [[p1, 1], [p2, 1]], "each retires on its own block's id, whatever the other claimed of the text");
  assert.deepEqual(r.keep, []);
  // and the frames after: nothing lingers
  assert.deepEqual(reconcilePending(landing, [p1, p2].filter((p) => !r.landed.some((l) => l.p === p))).keep, []);
});

test("an id the frame no longer carries leaves the decision to text: a restart that re-minted the mirrors, or an older kernel (T252c second review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const shown = (text: string, ts: number): PendingSend => {
    const p = newPending(text, undefined, ts);
    reconcilePending(tail, [p]);
    reconcilePending([...tail, { kind: "queued", texts: [{ md: text, qid: p.qid, qts: 1 }] }], [p]);   // the kernel showed the copy under our id once
    return p;
  };
  // (1) the restored copy carries no id: it still covers ours, by text — ours drawn at its slot, that copy hidden
  let p = shown("rename it", T0);
  const pressed = p.qid;
  let r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "rename it" }] }], [p]);
  assert.deepEqual([r.unqueue, r.inject], [[p], [p]], "one bubble, never ours beside the kernel's copy");
  // (2) an echo under a new uuid covers by text, and the press's identity stays: nothing in the frame contradicts it
  r = reconcilePending([...tail, { kind: "user", md: "rename it", uuid: "echo:new" }], [p]);
  assert.deepEqual([r.inject, r.keep, r.echoHide], [[p], [p], [1]], "the echo is attributed by text and hidden; ours stays");
  assert.equal(p.qid, pressed);
  // (3) that echo flagged never-delivered is the verdict, by text
  r = reconcilePending([...tail, { kind: "user", md: "rename it", uuid: "echo:new", undelivered: true }], [p]);
  assert.deepEqual(r.lost, [p]);
  // (4) a record the CLI wrote from the restored copy and a later identified send: our block carries no id, text lands it
  p = shown("rename it", T0 + 1);
  r = reconcilePending([...tail, { kind: "user", md: "rename it and go", uuid: "uB", blocks: ["rename it", "and go"], qids: [null, "echo:new2"] }], [p]);
  assert.deepEqual(r.landed.map((l) => l.idx), [1]);
});

test("an id the frame still shows queued or echoed refuses a same-text landing that lacks it (T252c second review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const [a, b] = press(tail, "ok", "ok");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: a.qid, qts: 1 }, { md: "ok", qid: b.qid, qts: 2 }] }], [a, b]);
  // the first copy lands unpaired (no id on the record) while the second is still queued under its id
  let r = reconcilePending([...tail, { kind: "user", md: "ok", uuid: "u1" }, { kind: "queued", texts: [{ md: "ok", qid: b.qid, qts: 2 }] }], [a, b]);
  assert.deepEqual(r.landed.map((l) => l.p), [a], "the id-less landing is the first send's, by text");
  assert.deepEqual([r.keep, r.unqueue, r.inject], [[b], [b], [b]], "the second is still in the queue under its id: not that landing's");
  // the same with the second copy fed: its echo shows the id — and the first send, landed, does not go on to claim
  // that echo as its own cover on the way out
  r = reconcilePending([...tail, { kind: "user", md: "ok", uuid: "u1" }, { kind: "user", md: "ok", uuid: b.qid }], [a, b]);
  assert.deepEqual([r.landed.map((l) => l.p), r.keep, r.inject, r.echoHide], [[a], [b], [b], [2]]);
  // a send read by text (its id is nowhere in the frame) never takes an echo another send owns by id
  const [c, d] = press(tail, "go", "go");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "go", qid: c.qid, qts: 1 }, { md: "go", qid: d.qid, qts: 2 }] }], [c, d]);
  r = reconcilePending([...tail, { kind: "user", md: "go", uuid: d.qid }], [c, d]);
  assert.deepEqual([r.keep, r.inject, r.echoHide], [[c, d], [c, d], [1]], "the echo is the second send's, by id: hidden for it; both bubbles are ours, and the first never claimed it");
  assert.deepEqual([c.received, d.received], [true, true], "both were received by their queued copies earlier; the echo added nothing to the first");
});

test("a send covered by its identified copy holds that copy's position: a later same-text send never takes the copy (T252c third review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const [p1, p2] = press(tail, "ok", "ok");
  // the kernel shows the first copy alone, under p1's id: p1 takes it; p2 has nothing yet
  let r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: p1.qid, qts: 1 }] }], [p1, p2]);
  assert.deepEqual([r.unqueue, r.inject, p2.received], [[p1], [p1, p2], undefined]);
  // both copies show: each is covered by its own id
  r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: p1.qid, qts: 1 }, { md: "ok", qid: p2.qid, qts: 2 }] }], [p1, p2]);
  assert.notEqual(p1.qid, p2.qid, "two sends, two identities");
  assert.deepEqual(r.unqueue, [p1, p2]);
  // the first copy's landing retires p1 alone; p2 is still the queued second copy
  r = reconcilePending([...tail, { kind: "user", md: "ok", uuid: "u1", qid: p1.qid }, { kind: "queued", texts: [{ md: "ok", qid: p2.qid, qts: 2 }] }], [p1, p2]);
  assert.deepEqual([r.landed.map((l) => l.p), r.keep, r.unqueue], [[p1], [p2], [p2]]);
  // a send read by text never takes a queued copy another send owns by id, nor a landing wearing another send's id
  const [c, d] = press(tail, "go", "go");
  r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "go", qid: d.qid, qts: 1 }] }], [c, d]);
  assert.deepEqual([r.unqueue, r.inject], [[d], [c, d]], "the one copy is d's: c is drawn uncovered");
  r = reconcilePending([...tail, { kind: "user", md: "go", uuid: "uD", qid: d.qid }], [c, d]);
  assert.deepEqual([r.landed.map((l) => l.p), r.keep], [[d], [c]], "the landing wearing d's id is d's, whatever the list order");
});

test("the ✕ on the kernel's copy of an identified send drops THAT send's entry, by id (T252c third review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const [p1, p2, p3] = press(tail, "ok", "ok", "ok");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: p1.qid, qts: 1 }, { md: "ok", qid: p2.qid, qts: 2 }] }], [p1, p2, p3]);
  const list = [p1, p2, p3];
  assert.equal(dropPending(list, "ok", undefined, p2.qid), p2, "the second copy's ✕ removes the second send, not the first with the text");
  assert.equal(dropPending(list, "ok", undefined, "echo:" + "f".repeat(32)), undefined, "an id no entry owns is another client's copy, or the kernel's own: nothing of ours goes");
  assert.deepEqual(list, [p1, p3]);
  assert.equal(dropPending(list, "ok"), p1, "a kernel copy with no id (an older kernel, tmux): the first pending send with the text");
  assert.deepEqual(list, [p3]);
});

// ── the identity is minted at the PRESS: the client's id, in the kernel's echo form ──────────────────────

test("newPending mints the send's id at the press, in the kernel's echo form, unique per press", () => {
  const p = newPending("x", undefined, T0), q = newPending("x", undefined, T0);
  assert.match(p.qid!, /^echo:[0-9a-f]{32}$/, "the form the kernel's own echo keys wear: isKernelEchoUuid and the kernel's echo skip treat it as the kernel's");
  assert.match(q.qid!, /^echo:[0-9a-f]{32}$/);
  assert.notEqual(p.qid, q.qid, "two presses of the same words are two identities");
  // the ✕ on the kernel's copy of the second press names ITS id: the second entry goes, never the first with the text
  const list = [p, q];
  assert.equal(dropPending(list, "x", undefined, q.qid), q);
  assert.deepEqual(list, [p]);
  assert.equal(dropPending(list, "x", undefined, q.qid), undefined, "an id no entry owns removes nothing");
  assert.deepEqual(list, [p]);
});

test("our bubble's ✕ carries the press time AND the id: the id decides, never the first same-text entry pressed in the same millisecond", () => {
  // two same-text sends registered in one synchronous loop can share a Date.now() stamp and differ only by id:
  // flushStaged posts each staged slash command and goal-cited item as its own send, and adoptProvisional posts each
  // text a new session's tab held the same way. The ✕ on OUR bubble rides both (data-qts and data-qid), and the id is
  // the identity.
  const p = newPending("x", undefined, T0), q = newPending("x", undefined, T0);
  assert.notEqual(p.qid, q.qid);
  const list = [p, q];
  assert.equal(dropPending(list, "x", T0, q.qid), q, "our bubble's ✕ carries the press time and the id: the id decides, never the first entry with the text and the millisecond");
  assert.deepEqual(list, [p]);
  assert.equal(dropPending(list, "x", T0, q.qid), undefined, "an id no entry owns removes nothing, not the same-text neighbour pressed in the same millisecond");
  assert.deepEqual(list, [p]);
  // the cancel the kernel answers names the same id, so the entry the client drops is the copy the kernel removes
  // (an id-first drop that misses leaves the neighbour for ITS own ✕); an id-less bubble, older data, still names
  // its entry by the press time and the text, never a same-text neighbour pressed at another time
  const r = newPending("x", undefined, T0 + 1);
  list.push(r);
  assert.equal(dropPending(list, "x", T0), p);
  assert.deepEqual(list, [r]);
});

test("the ✕ on a kernel copy the kernel identified otherwise drops the send it covered by text on the last push, and nothing when it covered none of ours", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const [p1, p2] = press(tail, "ok", "ok");
  const k1 = "echo:" + "1".repeat(32), k2 = "echo:" + "2".repeat(32), k3 = "echo:" + "3".repeat(32);
  // a kernel that minted its own ids for both copies (it took none from the press): each covers a send by text and
  // position for this push, and each send keeps the id it was pressed with
  let r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: k1, qts: 1 }, { md: "ok", qid: k2, qts: 2 }] }], [p1, p2]);
  assert.deepEqual([r.unqueue, p1.qid === k1, p2.qid === k2], [[p1, p2], false, false], "covered by text; nothing latched");
  const list = [p1, p2];
  assert.equal(dropPending(list, "ok", undefined, k2), p2, "the ✕ on the second copy drops the send it stood in for, never the first with the text");
  assert.deepEqual(list, [p1]);
  assert.equal(dropPending(list, "ok", undefined, k3), undefined, "an id that covered none of ours is another client's copy, or the kernel's own: nothing of ours goes");
  assert.deepEqual(list, [p1]);
  // the cover is the LAST push's: once the frame shows the send under its own id, a ✕ on the old copy drops nothing
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: p1.qid, qts: 1 }] }], list);
  assert.equal(dropPending(list, "ok", undefined, k1), undefined, "the cover is by id now; the stale text cover is forgotten");
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: k1, qts: 1 }] }], list);
  assert.equal(dropPending(list, "ok", undefined, k1), p1);
  assert.deepEqual(list, []);
  // a copy the frame held at the press is background, never a cover: its ✕ drops nothing of ours
  const q = newPending("go", undefined, T0 + 5);
  const bg: TailEvent[] = [...tail, { kind: "queued", texts: [{ md: "go", qid: k1, qts: 1 }] }];
  reconcilePending(bg, [q]);
  assert.equal(q.cover, undefined);
  assert.equal(dropPending([q], "go", undefined, k1), undefined);
});

test("the id rules from the press: the kernel's copies wearing it cover, land and hide by identity from their first appearance", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const [p1, p2] = press(tail, "ok", "ok");
  // the kernel parks the second press first (a compaction ended between the two): its chip wears p2's id
  let r = reconcilePending([...tail, { kind: "queued", texts: [{ md: "ok", qid: p2.qid }] }], [p1, p2]);
  assert.deepEqual([r.unqueue, r.inject, p1.received, p2.received], [[p2], [p1, p2], undefined, true], "the one copy is p2's by id, whatever its position; p1 is not covered by text");
  // the echo wearing p1's id is p1's cover; a landing wearing p2's id is p2's, behind a same-text one that is not
  r = reconcilePending([...tail, { kind: "user", md: "ok", uuid: p1.qid }, { kind: "user", md: "ok", uuid: "uX", qid: "echo:" + "9".repeat(32) }, { kind: "user", md: "ok", uuid: "uMine", qid: p2.qid }], [p1, p2]);
  assert.deepEqual([r.echoHide, r.landed.map((l) => [l.p, l.idx]), r.keep], [[1], [[p2, 3]], [p1]]);
});

// ── (T252d) the landed bubble's hover names the send time ─────────────────────────────────────────

test("the landed bubble's hover says when the message was SENT, once the landing is more than a minute later (T252d)", () => {
  const ts = "2026-09-08T10:05:30.000Z";
  const at = Math.floor(Date.parse(ts) / 1000);
  assert.equal(sentAtLabel(ts, at - 30), null, "half a minute apart: no hover");
  assert.equal(sentAtLabel(ts, at - 60), null, "exactly a minute: no hover");
  const label = sentAtLabel(ts, at - 61);
  assert.ok(label && label.startsWith("sent at ") && /\d\d:\d\d$/.test(label), "more than a minute: 'sent at HH:MM' — " + label);
  assert.equal(sentAtLabel(undefined, at), null);
  assert.equal(sentAtLabel(ts, undefined), null);
  // render.ts applies it to the landed user bubble's title, from the kernel's two stamps (never the client's clock)
  assert.match(RENDER, /const sentTip = sentAtLabel\(ev\.ts, ev\.sentAt\);\s*\n\s*if \(sentTip\) bubble\.title = sentTip;/);
});

test("our own send in the fed gap: the kernel's echo visible AND its held copy in the group — ours is the one bubble, both kernel copies hidden (T262h review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const p = newPending("hi there", undefined, T0);
  reconcilePending(tail, [p]);
  reconcilePending([...tail, { kind: "queued", texts: [{ md: "hi there", qid: p.qid, qts: 1 }] }], [p]);
  // the copy left the queue (fed) and is held by the caller, marked landing; the kernel's echo shows too
  const frame: TailEvent[] = [...tail, { kind: "user", md: "hi there", uuid: p.qid },
                              { kind: "queued", texts: [{ md: "hi there", qid: p.qid, qts: 1, landing: true }], uuid: "held:s" }];
  const r = reconcilePending(frame, [p]);
  assert.deepEqual([r.inject, r.unqueue, r.echoHide], [[p], [p], [1]], "ours drawn; the held copy hidden; the echo hidden");
  // an id-less copy (an older kernel): the same by text
  const q = newPending("go on", undefined, T0 + 1);
  reconcilePending(tail, [q]);
  const frame2: TailEvent[] = [...tail, { kind: "user", md: "go on", uuid: "echo:zz" }, { kind: "queued", texts: [{ md: "go on", landing: true }], uuid: "held:s" }];
  const r2 = reconcilePending(frame2, [q]);
  assert.deepEqual([r2.inject, r2.unqueue, r2.echoHide], [[q], [q], [1]]);
  // a kernel that minted its own id for the copy (this send's id is nowhere in the frame): the echo and the held copy
  // wearing the echo's uuid are ONE copy, both hidden; a queued copy wearing any other id is another send's
  const k = newPending("go on", undefined, T0 + 2);
  reconcilePending(tail, [k]);
  const kid = "echo:" + "9".repeat(32);
  const frame3: TailEvent[] = [...tail, { kind: "user", md: "go on", uuid: kid }, { kind: "queued", texts: [{ md: "go on", qid: kid, qts: 1, landing: true }], uuid: "held:s" }];
  const r3 = reconcilePending(frame3, [k]);
  assert.deepEqual([r3.inject, r3.unqueue, r3.echoHide, k.qid === kid], [[k], [k], [1], false], "one bubble, ours; the id is still the press's");
  const frame4: TailEvent[] = [...tail, { kind: "user", md: "go on", uuid: kid }, { kind: "queued", texts: [{ md: "go on", qid: "echo:" + "7".repeat(32), qts: 2 }] }];
  const r4 = reconcilePending(frame4, [k]);
  assert.deepEqual([r4.inject, r4.unqueue, r4.echoHide], [[k], [], [1]], "a copy under another id after the echo is not this send's: the echo alone covers");
});

test("two identical presses, then one push with the first's echo AND the second's queued copy: each send takes its own kernel copy (fed-gap fix review)", () => {
  const tail: TailEvent[] = [{ kind: "assistant", md: "…", uuid: "a1" }];
  const [p1, p2] = press(tail, "x", "x");
  const frame: TailEvent[] = [...tail, { kind: "user", md: "x", uuid: p1.qid }, { kind: "queued", texts: [{ md: "x", qid: p2.qid, qts: 2 }] }];
  let r = reconcilePending(frame, [p1, p2]);
  assert.deepEqual([p1.received, p2.received], [true, true], "the echo is the first's, the queued copy the second's, each by its own id, never the first's by text");
  assert.deepEqual([r.inject, r.unqueue, r.echoHide], [[p1, p2], [p2], [1]], "both drawn by us; the copy hidden for the second, the echo for the first");
  r = reconcilePending(frame, [p1, p2]);
  assert.deepEqual([r.inject, r.unqueue, r.echoHide], [[p1, p2], [p2], [1]], "…and the same on the next push: no third bubble");
  // the first's landing retires the first, and only the first
  const landed: TailEvent[] = [...tail, { kind: "user", md: "x", uuid: "ux1", qid: p1.qid }, { kind: "queued", texts: [{ md: "x", qid: p2.qid, qts: 2 }] }];
  r = reconcilePending(landed, [p1, p2]);
  assert.deepEqual([r.landed.map((l) => l.p), r.keep, r.unqueue], [[p1], [p2], [p2]]);
});
