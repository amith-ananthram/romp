// HOVER-FREEZE (the user 2026-08-24): a feed card under the pointer must never move on screen.
// While a card is hovered, incoming feed payloads QUEUE (newest wins) instead of rendering; the
// deferred churn hints as +N/-N beside the column pills and (grouped) the session headers; the
// hovered card's mouseleave flushes everything at once, window blur is the backstop — no timers.
// The counting rule EXECUTES here (feed-freeze.ts is pure); the feed wiring is source-pinned
// (feed.ts has no jsdom harness — the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { freezeDiff, contentSig } from "./feed-freeze";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");

const it = (id: string, col: string, sid = "s1") => ({ id, col, sid });

test("freezeDiff counts arrivals and departures per column and per session", () => {
  const d = freezeDiff(
    [it("a", "asks"), it("b", "completed"), it("c", "needsInput", "s2")],
    [it("a", "asks"), it("d", "completed"), it("e", "completed", "s2")],
  );
  assert.deepEqual(d.cols.completed, { add: 2, del: 1 });
  assert.deepEqual(d.cols.needsInput, { add: 0, del: 1 });
  assert.equal(d.cols.asks, undefined, "an unmoved card counts nothing");
  assert.deepEqual(d.sess.s1, { add: 1, del: 1 });
  assert.deepEqual(d.sess.s2, { add: 1, del: 1 });
  assert.ok(d.any);
});

test("a column move is one departure + one arrival — it leaves one header and joins another", () => {
  const d = freezeDiff([it("a", "asks")], [it("a", "completed")]);
  assert.deepEqual(d.cols.asks, { add: 0, del: 1 });
  assert.deepEqual(d.cols.completed, { add: 1, del: 0 });
  assert.deepEqual(d.sess.s1, { add: 1, del: 1 }, "grouped mode groups per column, so the run moves too");
});

test("identical views count nothing — in-place content edits are not movement", () => {
  const d = freezeDiff([it("a", "asks"), it("b", "completed")], [it("a", "asks"), it("b", "completed")]);
  assert.equal(d.any, false);
  assert.deepEqual(d.cols, {});
  assert.deepEqual(d.sess, {});
});

test("the feed payload path defers while a card is hovered — and ONLY the payload path", () => {
  // the message handler's feed branch gates on the freeze; the newest payload supersedes by overwrite
  assert.match(FEED, /if \(freezeKey \|\| tabScopeKey\) \{ pendingFeedPayload = m; paintFreezeBadges\(\); return; \}\s*\n\s*applyFeedPayload\(m\);/,
    "the keyboard scope holds the same gate (the user 2026-08-24) — a card being keyed cannot move either");
  const queues = FEED.match(/pendingFeedPayload = m;/g) || [];
  assert.equal(queues.length, 1, "one queue write, in the payload path — local gestures and render() are never gated");
  assert.doesNotMatch(FEED, /function render\(\) \{\s*\n[^\n]*freezeKey/, "render is not gated — the hovered card's controls stay live");
  // one body, two callers: live application and the flush
  assert.match(FEED, /function applyFeedPayload\(m: any\): void \{/);
  assert.match(FEED, /if \(m\) applyFeedPayload\(m\);/, "flush applies the newest queued payload");
});

test("flush is event-based: card mouseleave, window blur backstop — no timers anywhere in the freeze", () => {
  assert.match(FEED, /function freezeLeave\(key: string\): void \{\s*\n\s*if \(tabScopeKey === key\) releaseTabScope\(\);[^\n]*\n\s*if \(freezeKey !== key\) return;\s*\n\s*freezeKey = null;\s*\n\s*flushFreeze\(\);/);
  assert.match(FEED, /window\.addEventListener\("blur", \(\) => \{ releaseTabScope\(\); freezeKey = null; flushFreeze\(\); \}\);/,
    "blur releases BOTH gate holders — the keyboard scope has no pointer to leave with");
  const block = FEED.slice(FEED.indexOf("// ── HOVER-FREEZE"), FEED.indexOf("function applyFeedPayload"));
  assert.ok(!/setTimeout|setInterval/.test(block), "no timers — mouseleave and blur are the flush events");
  // the flush is a MICROTASK — same gesture, after its handlers finish (found in review 2026-08-24:
  // the clear path's synthetic mouseleave used to re-render the board under the rest of its own
  // click handler, before pendingCleared.add ran). Ordering, not a time window.
  assert.match(FEED, /queueMicrotask\(\(\) => \{/);
  assert.match(FEED, /if \(freezeKey \|\| tabScopeKey\) return;/,
    "EITHER holder keeps the queue — a stranger card's mouseleave must not flush under an armed keyboard scope");
});

test("a local render that detaches or re-keys the hovered element heals the freeze (:hover truth)", () => {
  // found in review 2026-08-24: a removed element never fires mouseleave — typing in search can
  // filter the hovered card out, and toggling Group swaps it for a group card in place with no
  // enter/leave events. The render tail checks live pointer truth per render — event-based.
  assert.match(FEED, /const hov = document\.querySelector<HTMLElement>\("\.feed-cols \.fitem:hover, \.feed-sess-head:hover"\);/,
    "a card or a session header under the pointer is pointer truth (T285 added the header)");
  assert.match(FEED, /if \(!hov\) \{ freezeKey = null; flushFreeze\(\); \}/);
  assert.match(FEED, /else \{ const k = hov\.classList\.contains\("feed-sess-head"\) \? sessFreezeKey\(hov\) : kbHoverId\(hov\); if \(k && k !== freezeKey\) freezeKey = k; \}/,
    "a re-keyed card under a stationary pointer re-arms to the element actually hovered");
});

test("the self-note flags CONTENT changes only — the aging tint can never cry update (2026-08-25)", () => {
  // the live specimen: a Completed card with its brief ready differed between builds ONLY in trgb
  // (top level + inside every tree node) — the recency ramp aging, not content — yet the whole-item
  // compare flagged it. The projection is an explicit list; volatile fields cannot silently rejoin.
  const done = { itemId: "TESTSID:g1", text: "confirm changes under version control", column: "completed",
    summary: "the brief", blockSummary: null, blocked: false, warns: null,
    t: 100, mt: 200, last: 200, trgb: [19, 166, 216],
    tree: [{ id: "TESTSID:g2", text: "step", status: "done", cleared: false, t: 100, mt: 150, trgb: [32, 175, 153] }] };
  const aged = { ...done, trgb: [19, 166, 215], mt: 260, last: 260,
    tree: [{ ...done.tree[0], trgb: [32, 175, 152], mt: 260 }] };
  assert.equal(contentSig(done), contentSig(aged), "the user's exact shape: Completed + brief ready, tint aged → NO flag");
  assert.notEqual(contentSig(done), contentSig({ ...done, summary: "a NEW brief" }), "real content still flags");
  assert.notEqual(contentSig(done), contentSig({ ...done, column: "needs_input" }), "a column move flags");
  assert.notEqual(contentSig(done),
    contentSig({ ...done, tree: [{ ...done.tree[0], status: "open" }] }), "a sub-goal status change flags");
  // identity stays strict: the compare joins by itemId (ask) / turnId member-set (group) — a
  // SIBLING's change never flags the hovered card (each side signs only its own found item)
  assert.match(FEED, /return contentSig\(asks\.find\(\(a\) => a\.itemId === key\) as any\) !== contentSig\(pend\.find\(\(a\) => a\.itemId === key\) as any\);/);
  assert.match(FEED, /\.sort\(byId\)\.map\(\(a\) => contentSig\(a as any\)\)\.join\("\|"\)/,
    "group compare: member-set content signatures, itemId-sorted");
  const FRZ = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed-freeze.ts"), "utf8");
  assert.match(FRZ, /const SELF_CONTENT = \["text", "column", "summary", "blockSummary", "blocked", "warns", "retrying", "nudgeFailed"\] as const;/,
    "the explicit content list — new volatile fields cannot silently rejoin");
  assert.match(FRZ, /o\.tree = tree\.map\(\(n\) => \[n\.id, n\.text, n\.status, n\.cleared\]\);/,
    "sub-goals contribute identity/text/status only — never their own aging channels");
});

test("the badge hint mirrors the optimistic-restore overlay — a card the flush restores is no departure", () => {
  // found in review 2026-08-24: the displayed board holds a pendingRestored card the queued payload
  // lacks; without the mirror the badge promised a -1 that the flush immediately took back
  const pv = FEED.slice(FEED.indexOf("function payloadView"), FEED.indexOf("function paintFreezeParts"));
  assert.ok(pv.includes(
    "for (const it of pendingRestored.values()) if (!present.has(it.itemId) && !inIncoming.has(it.itemId)) out.push(it);"),
    "exact mirror: an entry the payload itself carries is the flush's to drop, not the hint's to re-add");
  assert.ok(!pv.includes("pendingRestored.delete"), "read-only — the flush re-runs the real bookkeeping");
});

test("both card shapes arm the freeze on the same events the hover highlight rides", () => {
  assert.match(FEED, /freezeEnter\(it\.itemId\);\s*[^\n]*\n\s*if \(it\.provisional\) return;/, "ask cards enter before the provisional bail");
  assert.match(FEED, /freezeLeave\(it\.itemId\);/, "ask cards flush on leave");
  assert.match(FEED, /freezeEnter\(fkey\);/, "group cards enter");
  assert.match(FEED, /freezeLeave\(fkey\);/, "group cards flush on leave");
  // a card CLEARED under the pointer flushes via its synthetic mouseleave — the existing dispatch
  // (feed.ts's clear paths) runs the exact leave logic, freezeLeave included
  assert.match(FEED, /card\.dispatchEvent\(new MouseEvent\("mouseleave"\)\);/);
});

test("the badge hint counts the USER'S view and never mutates state computing it", () => {
  // render and the painter share one view filter, so the hint counts exactly what would move
  assert.match(FEED, /function viewFiltered\(list: AskItem\[\]\): AskItem\[\]/);
  assert.match(FEED, /let shown = viewFiltered\(asks\);/);
  assert.match(FEED, /const d = freezeDiff\(toItems\(asks\), toItems\(payloadView\(pendingFeedPayload\)\)\);/);
  // payloadView reads pendingCleared but must not write it (the flush re-runs the real bookkeeping)
  const pv = FEED.slice(FEED.indexOf("function payloadView"), FEED.indexOf("function paintFreezeParts"));
  assert.ok(!pv.includes("pendingCleared.delete") && !pv.includes("pruneViewStateTo"),
    "the hint path is side-effect free");
});

test("the badges say what they mean; the frozen card's own update gets its own line (2026-08-25)", () => {
  // the explicit reading joins the +N/−N (the user liked the numbers, wanted the meaning): one
  // phrasing everywhere a badge renders, well under their verbosity ceiling, in the accent dress
  assert.match(FEED, /note\.textContent = " \(" \+ \(c\.add \+ c\.del\) \+ " changed — mouse away to apply\)";/);
  assert.match(CSS, /\.freeze-badge \.fz-note \{ color: var\(--accent\); font-weight: 400; \}/,
    "accent var, never re-hardcoded; the badge's own scale");
  // the hovered card's OWN pending update is a different fact — its own line, INDEPENDENT of the
  // churn badges (both render when both are true: the self block sits outside the per-header loop)
  assert.match(FEED, /function pendingSelfChanged\(key: string\): boolean/);
  assert.match(FEED, /const selfKey = freezeKey \|\| tabScopeKey;/, "the pointer's card or the keyboard-scoped one");
  assert.match(FEED, /selfNote\.textContent = "\(this card updated — mouse away to refresh\)";/);
  assert.match(FEED, /if \(!selfKey \|\| !selfCard \|\| !pendingSelfChanged\(selfKey\)\) \{\s*\n\s*selfNote\?\.remove\(\);/,
    "churn-only: no self line; self-only: badges empty but the line shows; both: both");
  // group keys compare the turn's member set itemId-sorted — payload order can never fake a change
  assert.match(FEED, /asks\.filter\(\(a\) => a\.turnId === tid\)\.slice\(\)\.sort\(byId\)/);
  assert.match(CSS, /#freeze-selfnote \{ position: fixed; z-index: 6; pointer-events: none; color: var\(--accent\);/,
    "pointer-inert — the note must never affect the hover it describes");
  assert.match(FEED, /document\.getElementById\("freeze-selfnote"\)\?\.remove\(\);/,
    "nothing pending → the line comes off with the badges");
});

test("badges wear the header conventions: accent adds, block-red removes, the count's own scale", () => {
  assert.match(CSS, /\.freeze-badge \{ font-weight: 600; opacity: 0\.7; margin-left: 6px; white-space: nowrap; \}/);
  assert.match(CSS, /\.freeze-badge \.fz-add \{ color: var\(--accent\); \}/, "never a re-hardcoded accent hex");
  assert.match(CSS, /\.freeze-badge \.fz-del \{ color: var\(--err\); \}/, "the board's existing block red");
  assert.doesNotMatch(CSS, /\.freeze-badge[^}]*font-size/, "no new font sizes — the badge inherits the head's scale");
  // painted on the build-once column heads and the data-fsid-stamped session headers; cleared when quiet. The
  // BOARD's heads, under #feed-cols: the focused-session section (T347) above the board carries the same column
  // classes and comes first in the DOM, so a bare query would hang the badges on the miniature's head instead
  assert.match(FEED, /put\(document\.querySelector\("#feed-cols \.feed-col\.col-" \+ key \+ " \.feed-col-head"\), d\.cols\[key\]\);/);
  assert.match(FEED, /h\.setAttribute\("data-fsid", e\.sid\);/);
  assert.match(FEED, /if \(h\.getAttribute\("data-fcol"\) !== e\.col\) h\.setAttribute\("data-fcol", e\.col\);/,
    "the column stamp beside the sid, compare-first like it: the two together are the row the hover key names");
  assert.match(FEED, /const c = groupedNow \? d\.sess\[sid\] : undefined;\s*if \(sessFreezeKey\(h\) !== hoveredHead\) \{ put\(h, c\); return; \}/,
    "every header but the hovered ROW carries its in-row badge (the hovered one's floats, T285); the same session's header in another column is not the hovered row");
  assert.match(FEED, /document\.querySelectorAll\("\.freeze-badge"\)\.forEach\(\(n\) => n\.remove\(\)\);/,
    "nothing pending → every badge comes off");
  // local renders while frozen re-sync the hints, so a rebuilt board never strands a stale count
  assert.match(FEED, /paintFreezeBadges\(\);   \/\/ hover-freeze: local renders while frozen re-sync/);
});

test("a session header row holds the same gate a card holds: a push while it is hovered is queued, never rebuilt under the pointer; leaving releases (T285)", () => {
  const head = FEED.slice(FEED.indexOf("function makeSessHead()"), FEED.indexOf("function updateSessHead("));
  // the row (name, caret, Clear all inside it) enters and leaves the freeze by the session it stands for
  assert.match(head, /h\.addEventListener\("mouseenter", \(\) => freezeEnter\(sessFreezeKey\(h\)\)\);/, "hovering the header row arms the gate");
  assert.match(head, /h\.addEventListener\("mouseleave", \(\) => freezeLeave\(sessFreezeKey\(h\)\)\);/, "leaving it releases (the flush follows)");
  assert.match(FEED, /function sessFreezeKey\(h: HTMLElement\): string \{\s*return "h:" \+ \(h\.getAttribute\("data-fcol"\) \|\| ""\) \+ ":" \+ \(h\.getAttribute\("data-fsid"\) \|\| ""\);\s*\}/,
    "keyed by the data-fcol and data-fsid stamps read at event time: the (column, session) row's identity across re-homing renders");
  // the payload path is ONE gate for both holders: while any freeze key is set, the payload is queued and
  // NOTHING renders — so updateSessHead never runs and the hovered row (its Clear all included) stands
  const gate = FEED.slice(FEED.indexOf('if (m.type === "feed") {'), FEED.indexOf('} else if (m.type === "hoverCards") {'));
  assert.match(gate, /if \(freezeKey \|\| tabScopeKey\) \{ pendingFeedPayload = m; paintFreezeBadges\(\); return; \}/,
    "a push during header hover is held: queued, badges painted, no render");
  assert.match(gate, /applyFeedPayload\(m\);/, "…and applies only when nothing holds the gate");
  // the release re-renders: mouseleave → freezeLeave → flushFreeze → applyFeedPayload of the newest payload
  const leave = FEED.slice(FEED.indexOf("function freezeLeave(key: string): void {"), FEED.indexOf("let flushQueued = false;"));
  assert.match(leave, /if \(freezeKey !== key\) return;\s*freezeKey = null;\s*flushFreeze\(\);/);
  const flush = FEED.slice(FEED.indexOf("function flushFreeze(): void {"), FEED.indexOf('window.addEventListener("blur", () => { releaseTabScope();'));
  assert.match(flush, /if \(m\) applyFeedPayload\(m\);/, "the queued payload renders on release");
  // the stale-freeze heal reads a hovered header as pointer truth, keyed the same way
  const heal = FEED.slice(FEED.indexOf("// Stale-freeze heal (hover-freeze)"), FEED.indexOf("paintFreezeBadges();   // hover-freeze: local renders"));
  assert.match(heal, /querySelector<HTMLElement>\("\.feed-cols \.fitem:hover, \.feed-sess-head:hover"\)/, "a hovered header keeps the freeze live across a local render");
  assert.match(heal, /hov\.classList\.contains\("feed-sess-head"\) \? sessFreezeKey\(hov\) : kbHoverId\(hov\)/);
  // the same pending indicator beside each header — but NEVER inside the hovered row (review find): an in-row
  // badge lands after the auto-margin Clear all and slides it out from under the pointer, the very click loss
  // this hold prevents. The hovered header's badge floats: body-mounted, pointer-inert, its row's rect untouched.
  const paint = FEED.slice(FEED.indexOf("function paintFreezeBadges(): void {"), FEED.indexOf("// ── CARD KEYBOARD SCOPE"));
  assert.match(paint, /const hoveredHead = freezeKey && freezeKey\.startsWith\("h:"\) \? freezeKey : null;/, "the whole key, never the sid alone");
  assert.match(paint, /if \(sessFreezeKey\(h\) !== hoveredHead\) \{ put\(h, c\); return; \}\s*put\(h, undefined\);/, "every other header keeps its in-row badge; the hovered row carries none");
  assert.match(paint, /headNote\.id = "freeze-headnote"; document\.body\.appendChild\(headNote\);/, "…its hint is body-mounted");
  assert.match(paint, /headNote\.style\.top = Math\.round\(r\.bottom \+ 2\) \+ "px";/, "…placed just under the row, right-aligned");
  assert.match(CSS, /#freeze-headnote \{ position: fixed; z-index: 6; pointer-events: none;/, "pointer-inert, like the card's self-note");
});

