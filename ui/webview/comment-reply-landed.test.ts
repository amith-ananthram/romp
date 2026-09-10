// T237 (the user 2026-09-03, twice, ~4:25 PM PT): the marked passage must not turn YELLOW until the reply
// has LANDED. Two sightings, one design: the mark wears a faded AWAIT-GREEN `.busy` wash while the thread
// is mid-reply and settles into the yellow `.unread` the moment the reply lands — and both times the green
// failed to apply. Convicted at the source:
//   * `.busy` keyed on a CLIENT gesture latch (cmtAwaitBase) that any session's comments frame could prune
//     (the prune ran against ONE session's thread list) and that cleared on a reply-arrived heuristic
//     (agentCount rising + !threadBusy(state)) — the first agent record of a multi-record turn with the
//     backend's state read as ""/waiting dropped the wash while the thread was still responding; the
//     msgs-derived replyOwed() fallback flips false at that same first record.
//   * `unread` keyed in the kernel on ANY agent record newer than the watermark — an intermediate text
//     block or tool call of a turn in progress painted yellow before the turn ended.
// Fix: the kernel is the one truth (tests/test_comment_threads.py pins the rule): `replyOwed` (green) and
// `unread` = a FINISHED reply newer than the watermark (yellow), both from the thread's transcript with the
// event model's own turn-end. The client keys `.busy` on th.replyOwed and keeps the gesture latch only for
// the pre-round-trip instant (from the send click until a frame carries the send); no timers anywhere.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import type { CommentThread } from "./comments";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const COMMENTS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "comments.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the kernel ships replyOwed beside unread, both from the thread's own turn-end", () => {
  assert.match(KERNEL, /def _thread_turn_read\(tsid, reg, state, queued=0\):/, "the queue (persisted for a dormant thread) is read before the turn read (T237b C)");
  assert.match(KERNEL, /def _turn_landed\(turn, cut_t=0\.0\):/, "an interrupt at or before the backend's machineCut stamp is romp's cut, not the user's stop");
  assert.match(KERNEL, /def _turn_is_meta\(turn\):/, "compaction boundaries and command-only turns are not landings");
  assert.match(KERNEL, /if verdict in \("landed", "interrupted"\):\s*\n\s*return False, verdict == "interrupted", turns/, "the end_turn / interrupt record IS the landing");
  assert.match(KERNEL, /if verdict == "cut":/, "romp's own cut is its own verdict (T237b C)");
  assert.match(KERNEL, /if busy_state or queued > 0:\s*\n\s*return True, False, turns\s*\n\s*return False, True, turns/,
    "a cut stays open while the backend is busy on it or a send is still queued behind it; with neither the resume is over — dead, the stop record is not the user's message");
  assert.match(KERNEL, /if state == "":\s*\n[\s\S]{0,1400}?if queued > 0:\s*\n\s*return True, False, turns\s*\n\s*return False, False, turns/,
    "no process → an unlanded turn is dead unless a send is still queued (a resume in waiting) (T237b B)");
  assert.match(KERNEL, /print\("\[comments\] thread %s: transcript parse failed/, "a parse failure is shouted, never swallowed");
  assert.match(KERNEL, /def _agent_landed_after\(events, msgs, seen\):/);
  assert.match(KERNEL, /turn_open, interrupted, turns = \(_thread_turn_read\(tsid, reg, state, pending_n\) if status == "open"\s*\n\s*else \(False, False, \[\]\)\)/);
  assert.match(KERNEL, /unread = \(not turn_open\) and _agent_landed_after\(events, msgs, seen\)/);
  assert.match(KERNEL, /owes_first = status == "open" and not msgs and _thread_owes_first_reply\(tsid, reg, th, turns, state\)/);
  assert.match(KERNEL, /or \(bool\(msgs\) and msgs\[-1\]\["who"\] == "you" and not interrupted\s*\n\s*and \(state != "" or queued > 0\)\)\)/,
    "the user's newest message owes a reply only while a process exists or a send is queued (T237b)");
  assert.match(KERNEL, /def _thread_owes_first_reply\(tsid, reg, th, turns, state=""\):/, "a missing transcript owes nothing, loudly — and the verdict reaches the popover (T237b D)");
  assert.match(KERNEL, /if unreachable and not err:/, "the broken-thread verdict rides the frame's error channel");
  assert.match(KERNEL, /if tail and tail\[-1\]\.get\("type"\) == "idle":\s*\n\s*return False, False, turns/, "an idle tail after a trailing boundary reads dead, not owed");
  assert.match(KERNEL, /"unread": unread, "replyOwed": reply_owed,/);
  assert.match(KERNEL, /yellow — means "a FINISHED reply you have not seen"/, "the rule is stated in the docstring");
});

test("the client keys the green wash on the kernel's replyOwed; the gesture latch covers only the pre-round-trip instant", () => {
  const inflight = RENDER.split("const commentInFlight = (th: CommentThread): boolean => {")[1].split("\n};")[0];
  assert.match(inflight, /if \(cmtAwaitBase\.has\(th\.tid\)\) return true;/);
  assert.match(inflight, /const owed = typeof th\.replyOwed === "boolean" \? th\.replyOwed : replyOwed\(th\);/,
    "kernel truth when present; the msgs-derived fallback only for an older kernel");
  assert.match(inflight, /return owed && !cmtInterrupted\.has\(th\.tid\);/);
  assert.doesNotMatch(inflight, /agentCount|threadBusy/, "no reply-arrived heuristic in the wash");
  // the latch clears when a frame carries the SEND — a message newer than the click's newest, or more
  // messages than then (the count alone misses a thread at the projection's 40-message cap) — and only
  // an OLDER kernel (no replyOwed bit) keeps the T102 agent-count-with-settled-state clear
  assert.match(RENDER, /if \(base !== undefined && cmtLatchReleased\(t, base\)\) cmtAwaitBase\.delete\(t\.tid\);/);
  assert.match(RENDER, /cmtAwaitBase\.set\(cur\.th\.tid, cmtLatchOf\(cur\.th\)\);/, "a follow-up latches on its thread's counts at the click");
  assert.match(RENDER, /cmtAwaitBase\.set\(synth\.tid, \{ \.\.\.CMT_LATCH_ZERO \}\);/, "the create gesture latches its synthetic thread");
  assert.match(COMMENTS, /queued\?: number;/); assert.match(COMMENTS, /unreachable\?: boolean \| null;/); assert.match(COMMENTS, /lastUuid\?: string;/);
  assert.match(KERNEL, /"queued": queued,/); assert.match(KERNEL, /"unreachable": unreachable or None,/); assert.match(KERNEL, /"lastUuid": last_uuid,/);
  assert.match(KERNEL, /held = \[a for a in live if sb\.echo_text_key\(a\.get\("_echo_text"\)\) and not a\.get\("command"\)/, "echo-held sends count as owed, the chat's own fold (under the one echo text key, session_backend.echo_text_key)");
  assert.match(KERNEL, /def _settle\(a\):/, "the stop's settle record is skipped when reading the landing");
  assert.match(KERNEL, /reply_owed = status == "open" and \(turn_open or queued > 0 or owes_first/);
  assert.match(RENDER, /else if \(m\.type === "commentSendFailed" && m\.tid\) \{\s*\n\s*cmtAwaitBase\.delete\(String\(m\.tid\)\);/, "a refused send releases its latch");
  assert.match(RENDER, /unread: false, replyOwed: true, promotedName: "", msgs: \[\],\s*\n\s*name: nm \|\| nameBox\?\.dataset\.prefill \|\| "comment"/,
    "the synthetic thread owes its reply from the click");
  assert.match(COMMENTS, /replyOwed\?: boolean;/);
});

test("latches are pruned only against EVERY session's known threads — never one frame's list", () => {
  assert.match(RENDER, /const knownTids = new Set<string>\(\);\s*\n\s*commentThreads\.forEach\(\(list\) => list\.forEach\(\(t\) => knownTids\.add\(t\.tid\)\)\);/);
  assert.match(RENDER, /if \(!k\.startsWith\("pending:"\) && !knownTids\.has\(k\)\) cmtAwaitBase\.delete\(k\);/);
  assert.doesNotMatch(RENDER, /!threads\.some\(\(t\) => t\.tid === k\)\) cmtAwaitBase\.delete\(k\)/,
    "the one-frame prune that dropped other sessions' latches is gone");
  // the "pending:" → real tid carry on adoption stays
  assert.match(RENDER, /if \(k\.startsWith\("pending:"\)\) \{ cmtAwaitBase\.set\(tid, cmtAwaitBase\.get\(k\)!\); cmtAwaitBase\.delete\(k\); \}/);
});

test("a reply landing while its popover is open advances the watermark BEFORE the marks paint", () => {
  const handler = RENDER.split('else if (m.type === "comments" && m.id) {')[1].split("\n  }\n")[0];
  const seen = handler.indexOf('vscodeApi?.postMessage({ type: "commentSeen", id: sid, tid: th.tid });');
  const paint = handler.indexOf("applyCommentMarks(sid);");
  assert.ok(seen > 0 && paint > 0 && seen < paint, "seen-stamp first, paint after — the mark never wears yellow for a reply read as it lands");
  assert.match(handler, /if \(openCommentKey && openCommentKey\.sid === sid\) renderCommentPopover\(\);/);
});

test("no timer anywhere in the colour path", () => {
  const inflight = RENDER.split("const commentInFlight = (th: CommentThread): boolean => {")[1].split("\n};")[0];
  const style = RENDER.split("function styleCommentMark(")[1].split("\n}")[0];
  for (const src of [inflight, style]) assert.doesNotMatch(src, /setTimeout|Date\.now|setInterval/);
});

// ── executed: the mark's classes for the kernel's frames (no jsdom dependency here — a minimal element shim) ──
function markFor(th: CommentThread, latched = false): Set<string> {
  // the latch map's VALUE shape is irrelevant to the wash (has() alone is read) — a zeroed latch stands in
  const inflightSrc = "const commentInFlight = (th) => {" + RENDER.split("const commentInFlight = (th: CommentThread): boolean => {")[1].split("\n};")[0] + "\n};";
  const styleSrc = "function styleCommentMark(m, th) {" + RENDER.split("function styleCommentMark(m: HTMLElement, th: CommentThread): void {")[1].split("\n}")[0] + "\n}";
  const prelude = `
    const cmtAwaitBase = new Map(${latched ? '[["t1", { you: 0, youT: 0, agents: 0, queued: 0, last: "" }]]' : ""});
    const cmtInterrupted = new Set();
    const threadStuck = (st) => st === "permission" || st === "picker";
    const replyOwed = (th) => { const l = th.msgs.length ? th.msgs[th.msgs.length - 1] : null; return !!l && l.who === "you"; };
    const commentInFlight_ = null;`;
  const classes = new Set<string>();
  const m = { classList: { toggle(c: string, on: boolean) { if (on) classes.add(c); else classes.delete(c); } }, title: "" };
  new Function("m", "th", prelude + "\n" + inflightSrc + "\n" + styleSrc + "\nstyleCommentMark(m, th);")(m, th);
  return classes;
}
const base = (over: Partial<CommentThread>): CommentThread => ({
  tid: "t1", anchorUuid: "a1", exact: "the passage", status: "open", createdT: 0,
  state: "", unread: false, promotedName: "", msgs: [], ...over,
});

test("(v)/(vii) while the popover would show its loader — no exchange yet — the mark is busy, never unread", () => {
  const c = markFor(base({ replyOwed: true, unread: false, msgs: [], events: [] } as Partial<CommentThread>));
  assert.ok(c.has("busy") && !c.has("unread"));
});

test("(vi) a partial reply (the kernel still owes it, whatever the state reads) keeps the wash", () => {
  for (const state of ["working", "", "waiting"]) {
    const c = markFor(base({ state, replyOwed: true, unread: false,
      msgs: [{ who: "you", text: "why jitter?", t: 1 }, { who: "agent", text: "Let me check.", t: 2 }] }));
    assert.ok(c.has("busy") && !c.has("unread"), "state=" + JSON.stringify(state));
  }
});

test("(viii) the finished reply: yellow in the same frame the kernel says it landed, green gone", () => {
  const c = markFor(base({ replyOwed: false, unread: true,
    msgs: [{ who: "you", text: "why jitter?", t: 1 }, { who: "agent", text: "Jitter prevents thundering herds.", t: 2 }] }));
  assert.ok(c.has("unread") && !c.has("busy"));
});

test("the pre-round-trip latch alone keeps a fresh mark busy before any frame carries the send", () => {
  const c = markFor(base({ msgs: [], unread: false }), true);
  assert.ok(c.has("busy") && !c.has("unread"));
});

test("an older kernel (no replyOwed bit) falls back to the msgs-derived read", () => {
  const owed = markFor(base({ msgs: [{ who: "you", text: "why?", t: 1 }] }));
  assert.ok(owed.has("busy"));
  const answered = markFor(base({ msgs: [{ who: "you", text: "why?", t: 1 }, { who: "agent", text: "because", t: 2 }] }));
  assert.ok(!answered.has("busy"));
});

test("the CSS intent stands: green wash while busy, yellow tiers for base/unread", () => {
  assert.match(CSS, /mark\.cmt-hl\.busy \{\s*\n\s*background-color: color-mix\(in srgb, var\(--st-awaitbg-bg\) 24%, transparent\);/);
  assert.match(CSS, /mark\.cmt-hl\.unread \{ background: color-mix\(in srgb, var\(--cmt-hl\) 45%, transparent\); \}/);
});

// ── executed: the latch RELEASE rule (cmtLatchReleased) over the frame sequences the review named ──────────
type LatchBase = { you: number; youT: number; agents: number; queued?: number; last?: string };
function latchReleased(t: CommentThread, base0: LatchBase): boolean {
  const base = { queued: 0, last: "", ...base0 };
  const src = ("function cmtLatchReleased(t, base) {" + RENDER.split("function cmtLatchReleased(t: CommentThread, base: CmtLatch): boolean {")[1].split("\n}")[0] + "\n}")
    .replace(/ as unknown\[\]/g, "");   // the one TypeScript cast in the body — plain JS for new Function
  const prelude = `
    const cmtYouRows = (th) => (th.msgs || []).filter((m) => m.who === "you");
    const agentCount = (th) => (th.msgs || []).filter((m) => m.who === "agent").length;
    const threadBusy = (st) => st === "working" || st === "retrying" || st === "compacting";`;
  return new Function("t", "base", prelude + "\n" + src + "\nreturn cmtLatchReleased(t, base);")(t, base) as boolean;
}
const you = (t: number) => ({ who: "you" as const, text: "q", t });
const agent = (t: number) => ({ who: "agent" as const, text: "a", t });

test("a follow-up sent MID-turn keeps its latch while the backend holds the send: agent rows never release it", () => {
  const b = { you: 1, youT: 50, agents: 1 };   // clicked with msgs [you@50, agent partial@100]
  assert.equal(latchReleased(base({ replyOwed: true, msgs: [you(50), agent(100)] }), b), false, "the click's own frame");
  assert.equal(latchReleased(base({ replyOwed: true, msgs: [you(50), agent(104)] }), b), false, "the partial's merged row advances — still the agent");
  assert.equal(latchReleased(base({ replyOwed: false, unread: true, msgs: [you(50), agent(105)] }), b), false,
    "the current reply LANDS while the send is still queued — green must hold for the queued send");
  assert.equal(latchReleased(base({ replyOwed: true, msgs: [you(50), agent(105), you(106)] }), b), true, "the send is written: released to the kernel");
});

test("a thread at the projection cap releases on the newer 'you' time, not the row count", () => {
  const capped = Array.from({ length: 40 }, (_, i) => (i % 2 ? agent(i + 1) : you(i + 1)));
  const b = { you: 20, youT: 39, agents: 20 };
  assert.equal(latchReleased(base({ replyOwed: true, msgs: capped }), b), false);
  const rolled = [...capped.slice(2), agent(41), you(42)];   // still 40 rows, the send is the newest you
  assert.equal(latchReleased(base({ replyOwed: true, msgs: rolled }), b), true);
});

test("an older kernel (no replyOwed) keeps the T102 reply-arrived release: agent count up AND the thread settled", () => {
  const b = { you: 1, youT: 50, agents: 0 };
  assert.equal(latchReleased(base({ state: "working", msgs: [you(50), agent(100)] }), b), false, "mid-turn: not yet");
  assert.equal(latchReleased(base({ state: "", msgs: [you(50), agent(100)] }), b), true, "settled with a new agent row");
  assert.equal(latchReleased(base({ state: "", msgs: [you(50)] }), b), false, "no reply yet");
});

test("leaving open, or a launch error, releases either way", () => {
  const b = { you: 0, youT: 0, agents: 0 };
  assert.equal(latchReleased(base({ replyOwed: true, status: "resolved", msgs: [] }), b), true);
  assert.equal(latchReleased(base({ replyOwed: true, error: "could not start", msgs: [] }), b), true);
  assert.equal(latchReleased(base({ replyOwed: true, msgs: [] }), b), false, "a fresh thread holds until its send lands");
});

test("the kernel's other acknowledgements release the latch: a held/fed send, a consumed slash command, an unreachable thread", () => {
  const b = { you: 1, youT: 50, agents: 1, queued: 0, last: "a-100" };
  // the follow-up typed mid-turn is held or fed (its echo lives until the record lands): queued grew — the kernel owes it now
  assert.equal(latchReleased(base({ replyOwed: true, queued: 1, msgs: [you(50), agent(100)], lastUuid: "a-100" }), b), true);
  // the reply lands while the send is still unwritten but the kernel has not acknowledged it (no echo seen, no queue,
  // no "you" row): the latch HOLDS even though the newest record moved — "moved" alone is never the send
  assert.equal(latchReleased(base({ replyOwed: true, unread: false, msgs: [you(50), agent(105)], lastUuid: "a-105" }), b), false);
  // a slash command typed in the popover: no "you" row ever, the newest record moved (its wrapper records), nothing owed
  assert.equal(latchReleased(base({ replyOwed: false, msgs: [you(50), agent(100)], lastUuid: "cc-2" }), b), true);
  // …an unchanged newest record with nothing owed (the click's own frame) does not release
  assert.equal(latchReleased(base({ replyOwed: false, msgs: [you(50), agent(100)], lastUuid: "a-100" }), b), false);
  // …and a moved record while the kernel still owes (agent partials) does not release
  assert.equal(latchReleased(base({ replyOwed: true, msgs: [you(50), agent(104)], lastUuid: "a-104" }), b), false);
  // a broken thread: nothing can land
  assert.equal(latchReleased(base({ replyOwed: false, unreachable: true, msgs: [] }), { ...b, you: 0, youT: 0, last: "" }), true);
});
