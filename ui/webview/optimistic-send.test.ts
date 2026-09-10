// A composer send clears the box instantly, but the message only reappears in the chat once the kernel
// round-trips it back as its own provisional. Sending to a busy/slow thread, that provisional could briefly
// VANISH in the server-side echo→landed gap — so a just-sent message looked lost for a beat (the user
// 2026-07-15: "showing up as a provisional message ... and it disappeared"). Fix: a CLIENT-side optimistic
// bubble injected at the tail the moment you hit Enter, re-asserted on every push until the kernel's payload
// demonstrably carries the message, then retired.
//
// Reshaped 2026-08-09 (the user, who watched sends vanish again and suspected the fix was hollow): the
// retire test was a bare substring scan, so (A) a resend — or any short message that substrings an older
// bubble — retired its own entry in the very call that created it and showed nothing, and (B) the kernel's
// own PROVISIONAL copy (queued bubble / "echo:" atom) counted as landed and deleted the entry one-way, so
// when that provisional blinked in the echo→landed handoff nothing was left to cover the gap. Now: a
// per-send anchor makes only NEW landed user atoms retire it, kernel provisionals merely SUPPRESS
// injection for the push they're visible on.
// Reshaped again 2026-09-06 (the send-durability audits): the 20 s TTL backstop is GONE — an entry ends
// on events only (a landing, the kernel's never-delivered verdict, the user's ✕) — and the decision
// moved to send-pending.ts, a pure module this file executes directly (see also send-pending.test.ts,
// which also covers the review's fixes: the anchor replaces the 30-event tail scan, exact text and
// one landing per send, the lost verdict's anchor, the receipt proof that clears "not confirmed").
// render.ts has import-time DOM side effects → source pins for the DOM half.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { newPending, reconcilePending, bareGroupLabel, type TailEvent } from "./send-pending";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the plain send registers an optimistic bubble; follow-up/quote sends keep their own kernel echo", () => {
  // only the PLAIN sendMessage branch registers — a citation follow-up/quote has its own kernel-side
  // echo (the branch lives in routeUserMessage since the staged flush, 2026-08-15)
  assert.match(RENDER, /else \{ vscodeApi\.postMessage\(\{ type: "sendMessage", id: sid, text, qid \}\); registerOptimistic\(sid, text, imgPaths, qid\); \}/);
  // registerOptimistic shows it NOW (before any push) via reconcile + appendActive
  assert.match(RENDER, /function registerOptimistic\(id: string, text: string, imgPaths\?: string\[\], qid\?: string\): void/);   // + the dragged-image paths → echo thumbnails (2026-08-25); + the copy's id the caller minted and posted
  // the active-tab arm still paints via appendActive (the snap gate moved ahead of it, 2026-08-30)
  assert.match(RENDER, /if \(v\) v\.stale = true;\s*\n\s*if \(id === activeId\) \{/);
  assert.match(RENDER, /const wasAtBottom = !!content && nearBottomForSend\(content\);[^\n]*\s*\n\s*appendActive\(\);/);
});

test("your OWN send reveals itself from the TAIL only — scrolled up, the viewport stays put", () => {
  // The 2026-08-09 always-reveal snap (Enter = intent to see the message) survives where it belongs:
  // at — or within nearBottomForSend's 80px of — the bottom (the send band; follow mode itself reads the true bottom since T262c). Scrolled UP reading history, the user's
  // 2026-08-30 ruling overrules it: the send must not move the scroll position at all, so the snap
  // is gated on a nearBottomForSend read taken BEFORE appendActive lands the bubble (the append grows
  // scrollHeight, which would misread a tail-sitter as scrolled-up). Behavioral scenarios live in
  // send-scroll-preserve.test.ts.
  assert.match(RENDER, /const wasAtBottom = !!content && nearBottomForSend\(content\);[^\n]*\s*\n\s*appendActive\(\);\s*\n\s*if \(content && wasAtBottom\) writeScroll\(content, content\.scrollHeight, "optimistic-send", true\);/);
  // the unconditional form is retired everywhere — nothing snaps a scrolled-up reader on send
  assert.doesNotMatch(RENDER, /if \(content\) (?:content\.scrollTop = content\.scrollHeight|writeScroll\(content, content\.scrollHeight)/);
});

// The reconcile's two IN-PLACE tail mutations — merging into an existing queued group (a busy session
// already showing queued messages) and pop+push on a repeat send — leave s.events.length unchanged, so
// syncView's no-op fast path (rendered === len && !stale) concluded "nothing changed" and skipped the
// repaint: the bubble waited for the NEXT kernel push, a visible beat after Enter (the user 2026-08-07).
// Only the length-growing case (first send, bare tail) painted on the keystroke. registerOptimistic now
// marks the view stale before appendActive, so every send takes the stale window re-render immediately.
test("a send paints on ITS OWN keystroke even when the tail mutates in place (no length change)", () => {
  // the stale mark sits between the reconcile and the repaint, so appendActive can't hit the fast path
  assert.match(RENDER, /reconcileOptimistic\(s\);[\s\S]{0,700}const v = views\.get\(id\);\s*\n\s*if \(v\) v\.stale = true;/);
  // the fast path it defeats keys on length + staleness — stale must veto the skip
  assert.match(RENDER, /if \(v\.rendered === len && !v\.stale && v\.el\.childNodes\.length > 0\) return v;/);
  // executed replica: the fast-path predicate must not skip once the view is marked stale, even though
  // the in-place merge keeps the length equal to what was last rendered
  const skips = (rendered: number, len: number, stale: boolean, children: number) =>
    rendered === len && !stale && children > 0;
  assert.equal(skips(50, 50, false, 50), true, "length-neutral mutation without the mark: skipped (the bug)");
  assert.equal(skips(50, 50, true, 50), false, "the stale mark forces the repaint on the same keystroke");
});

test("every push entry point re-asserts (or retires) the optimistic tail", () => {
  // update(), chatTail(), and upsert() each call reconcileOptimistic after setting s.events
  const calls = RENDER.match(/reconcileOptimistic\(s\);/g) || [];
  assert.ok(calls.length >= 4, "reconcile wired into send + all three push paths, got " + calls.length);
});

test("retire needs a NEW landed atom (after the send's anchor); kernel provisionals only suppress", () => {
  // the entry is minted by the module (unanchored until the first reconcile stamps where the send sits)
  assert.match(RENDER, /const p = newPending\(text, imgPaths, Date\.now\(\), qid\);\s*\n\s*arr\.push\(p\);/);
  assert.equal(newPending("x", undefined, 5).at, undefined);
  // the decision is the module's, read off KERNEL truth after our injections are stripped — the whole
  // resident array from the anchor on, never a tail count (2026-09-06 review)
  assert.match(RENDER, /const r = reconcilePending\(s\.events as TailEvent\[\], list\);/);
  assert.doesNotMatch(RENDER, /OPT_TAIL_SCAN/);
  assert.match(RENDER, /if \(r\.keep\.length\) pendingSent\.set\(s\.id, r\.keep\); else pendingSent\.delete\(s\.id\);/);
  assert.match(RENDER, /const inject = r\.inject\.filter\(\(p\) => !covered\.has\(p\)\);/);
  // and no clock anywhere in the file's decision: the TTL is gone for good
  assert.doesNotMatch(RENDER, /OPT_TTL_MS/);
  const SP = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "send-pending.ts"), "utf8");
  assert.doesNotMatch(SP, /Date\.now\(\)(?!, qid: string = mintQid\(\)\): PendingSend)/, "the module reads no clock in a decision (only the press stamp's default, beside the id's)");
});

// The optimistic echo rides the QUEUED idiom (the user 2026-07-16): to the reader an unconfirmed send and a
// queued one are the same state, so they wear the same dashed bubble — and the look then only ever moves
// provisional→settled. It first shipped as a 0.6-opacity SOLID bubble, which invented a third look and made a
// queued send flip solid→dashed (backwards, as if it had un-landed).
test("an optimistic echo is a kernel-invisible QUEUED event at the tail — never a solid user bubble", () => {
  const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
  const SP = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "send-pending.ts"), "utf8");
  assert.match(SP, /export const OPT_PREFIX = "optimistic:";/);
  assert.match(RENDER, /const isOptimistic = \(e: ChatEvent\): boolean => isOptimisticUuid\(e\.uuid\);/);
  assert.match(RENDER, /const mk = \(p: PendingSend\) => \(\{ md: p\.text, optimistic: true, cancelable: true, imgPaths: p\.imgPaths, lost: p\.lost, qts: p\.ts, qid: p\.qid \}\);/);   // the echo carries its dragged-image paths (2026-08-25); cancelable from the press (2026-08-30); `lost` after a connection drop, `qts` its identity for the ✕ (2026-09-06); `qid` the copy's id the kernel shares, so the ✕ names the copy
  // stale ones are stripped wherever they sit (the strip is position-agnostic; T252d puts the group at the tail)
  assert.match(RENDER, /if \(isOptimistic\(e\)\) \{ s\.events\.splice\(i, 1\); continue; \}/);
  // the abandoned dim/pending idiom is FULLY gone: render, guard fields, and stylesheet — the last
  // leftovers (a `!e.pending` guard on a field no event carries, and a stylesheet paragraph describing
  // the deleted rendering as if it shipped) misdirected the 2026-08-09 bug hunt and are pinned out
  assert.doesNotMatch(RENDER, /ev\.pending/);
  assert.doesNotMatch(RENDER, /!e\.pending/);
  assert.doesNotMatch(CSS, /\.user-bubble\.pending \{/);
  assert.doesNotMatch(CSS, /renders EXACTLY like a landed/);
  // it reuses the chat's ONE provisional look rather than adding CSS of its own
  assert.match(CSS, /\.queued-bubble \{[\s\S]*?border: 1px dashed/);
});

test("nothing known-queued → a bare group wearing the honest 'sending…' header", () => {
  assert.match(RENDER, /s\.events\.push\(\{ kind: "queued", bare: true, texts: inject\.map\(mk\), uuid: OPT_PREFIX \+ inject\[0\]\.ts,/,
    "ONE bare group at the tail, the sends in send order (T252d: where the model reads them)");
  // "N queued messages" stays unclaimable pre-confirmation — but NO label was the user's 2026-08-30
  // bug (a mid-compaction send sat unlabeled and uncuttable): the bare group now states exactly what
  // is known, and the reflow recounts it in the same vocabulary after a ✕
  assert.match(RENDER, /label\.dataset\.bare = "1";/);
  // the wording is the pure helper's (send-pending.ts bareGroupLabel, per-bubble since 2026-09-06), and
  // the render feeds it the bubbles' own states
  assert.match(RENDER, /fillBareLabel\(label, nLost, texts\.length - nLost\);/);
  assert.deepEqual(bareGroupLabel(0, 1).parts.map((p) => p.text), ["sending…"]);
  assert.deepEqual(bareGroupLabel(0, 2).parts.map((p) => p.text), ["sending 2…"]);
  assert.match(RENDER, /if \(label\.dataset\.bare === "1"\) \{/, "reflow keeps the bare vocabulary");
  assert.match(RENDER, /if \(!ev\.bare\) \{/, "the standard 'N queued' header still needs confirmation");
});

test("something IS queued → the kernel's copy of OUR text is hidden and ours stays drawn below the group (T252)", () => {
  // one bubble per message: the kernel's queued group at the tail keeps its OTHER texts, our copy in it is
  // hidden (a client-only mark), and the hide is undone with every other injection before the counts run
  assert.match(RENDER, /function hideQueuedCopy\(s: Session, p: PendingSend\)/);
  assert.match(RENDER, /for \(const p of r\.unqueue\) \{\s*\n\s*const hid = hideQueuedCopy\(s, p\);/);
  assert.match(RENDER, /texts\[k\] = \{ \.\.\.texts\[k\], hiddenByPending: true \};/);
  assert.match(RENDER, /const texts = ev\.texts\.filter\(\(t\) => !t\.hiddenByPending\);/, "the renderer draws the visible copies only");
  assert.match(RENDER, /map\(\(t\) => t\.hiddenByPending \? \{ \.\.\.t, hiddenByPending: undefined \} : t\)/, "the strip clears the marks");
  // a copy hidden out of a HELD group hands the hold's reason to our bubble
  assert.match(RENDER, /held: inject\.map\(\(p\) => heldBy\.get\(p\)\)\.find\(\(h\) => !!h\)/);
});

test("an unconfirmed echo keeps its tooltip AND carries a ✕ from the press (the 2026-08-30 rule)", () => {
  // The retargeted contract: from the instant send is pressed the message is labeled and cancellable —
  // the old cancelable:false stage was exactly where the user sat during a mid-compaction send. The
  // optimistic ✕ rides the same qx delegate with data-qopt (no idx/park exists yet).
  assert.match(RENDER, /if \(t\.optimistic\) bubble\.title = "sent just now — romp hasn't confirmed the session has it yet";/);
  assert.match(RENDER, /optimistic: true, cancelable: true/);
  assert.match(RENDER, /if \(t\.cancelable && \(t\.idx !== undefined \|\| t\.park !== undefined \|\| t\.optimistic\)\) \{/);
  assert.match(RENDER, /if \(t\.optimistic\) x\.dataset\.qopt = "1";/);
});

test("EVERY ✕ stops our re-injection first; the optimistic one cancels by body at the kernel", () => {
  // Order matters: the pendingSent entry goes FIRST — at the optimistic stage the kernel may not
  // have pushed its park yet (the reconcile would repaint the bubble the user just cut), and on a
  // parked/backend ✕ the kernel bubble had been SUPPRESSING our still-live entry, so a kernel-only
  // cancel resurrected the cancelled message as a dashed bubble until the TTL (served-probe find,
  // 2026-08-30). Then the same cancelQueued the ✕ always posted — the optimistic one with only the
  // body (ws ordering puts it after the send it names); a miss comes back through the same loud
  // cancelResult, and the composer restore reverts (pendingCancelRestores).
  assert.match(RENDER, /if \(qmd\) \{/);
  // …by the bubble's OWN identity when it has one (data-qts) — send-pending.test.ts runs the lookup
  assert.match(RENDER, /const qts = el\.dataset\.qts !== undefined \? Number\(el\.dataset\.qts\) : undefined;\s*\n\s*const qid = el\.dataset\.qid \|\| undefined;\s*\n\s*if \(dropPending\(list, qmd, qts, qid\)\)/);
  // …and the kernel's copies carry their own enqueue stamp under `qts` now (T252c): only OUR bubble's stamp is its identity
  // for the ✕, while a kernel copy's ✕ names the copy's id, so it drops the send that owns it (third review)
  assert.match(RENDER, /if \(t\.optimistic && t\.qts !== undefined\) x\.dataset\.qts = String\(t\.qts\);/);
  assert.match(RENDER, /if \(t\.qid\) x\.dataset\.qid = t\.qid;/);
  assert.doesNotMatch(RENDER, /list\.findIndex\(\(p\) => p\.text === qmd\)/, "never 'the first entry with this text' for a bubble that names its entry");
  assert.match(RENDER, /echoShownSig\.delete\(sidQ\);/);
  assert.match(RENDER, /const msg: Record<string, unknown> = \{ type: "cancelQueued", id: sidQ, md: qmd \};/);
});

test("chatTail speaks the KERNEL's coordinates — the injected tail is not part of its space", () => {
  // counting the injected bubble in the gap check masked a genuine 1-event desync (the repair never
  // fired) and let a delta land PAST the bubble, freezing it into resident events as fake history
  assert.match(RENDER, /const kernelLen = s\.events\.reduce\(\(n, e\) => n \+ \(isOptimistic\(e\) \|\| isHeldGroup\(e\) \? 0 : 1\), 0\);/);
  assert.match(RENDER, /if \(from > kernelLen\) \{/);
});

// The reconcile decision, EXECUTED through the real module: three outcomes per entry per push — inject
// (payload has nothing), suppress (a kernel PROVISIONAL is visible: its queued bubble or its "echo:"
// atom), retire (a NEW landed user atom beyond base). No fourth: the TTL is gone (send-pending.test.ts
// pins the no-lifetime rule and the other retire event, the kernel's never-delivered verdict).
test("reconcile: inject on nothing, suppress on kernel provisionals, retire only on NEW landings", () => {
  const T0 = 1_000_000;
  const fresh = () => [newPending("continue", undefined, T0)];
  const reconcile = (events: TailEvent[], list = fresh()) => reconcilePending(events, list);

  // gap: the payload carries nothing for it → keep AND inject
  let r = reconcile([{ kind: "assistant", md: "working on the prior turn" }]);
  assert.equal(r.inject.length, 1);

  // DEFECT A (the resend): an OLDER identical message sits in the tail — base counts it as background,
  // so the new send still injects instead of retiring itself in the call that created it
  r = reconcile([{ kind: "user", md: "continue", uuid: "u-old" }]);
  assert.equal(r.inject.length, 1, "a resend must still show its own bubble");

  // …and the same protection for a short message that substrings an older bubble
  r = reconcile([{ kind: "user", md: "test the continue button", uuid: "u-old" }]);
  assert.equal(r.inject.length, 1, "substring-of-history must not count as landed");

  // kernel shows its QUEUED bubble for THIS send → suppressed for this push, but NOT retired… The press
  // stamped a tail without the copy: a copy already listed AT the press is an older send's, background,
  // and covers nothing (send-pending.test.ts, "what was already there at the press is background")
  const p = fresh();
  reconcile([{ kind: "assistant", md: "working on the prior turn" }], p);   // the press
  r = reconcile([{ kind: "queued", texts: [{ md: "continue" }] }], p);
  assert.equal(r.keep.length, 1);
  assert.equal(r.inject.length, 1, "ours stays drawn at its slot (T252)…");
  assert.equal(r.unqueue.length, 1, "…and the kernel's copy is the one hidden: one bubble per message");
  // the kernel's unlanded echo atom (uuid keeps the backend's echo: prefix) proves receipt but does not replace ours
  // (T262h): the echo sits at its send time, above the steps that ran since, so swapping ours out for it shrank the
  // tail under a bottom reader; ours stays, the echo is hidden
  r = reconcile([{ kind: "user", md: "continue", uuid: "echo:abc123" }], p);
  assert.equal(r.keep.length, 1);
  assert.equal(r.inject.length, 1, "ours stays drawn");
  assert.deepEqual(r.echoHide, [0], "…and the caller hides the kernel's echo");
  // DEFECT B (the flash-out): the provisional blinks away in the echo→landed handoff — the entry
  // survived the suppression, so ours steps straight back in and the message never disappears
  r = reconcile([{ kind: "assistant", md: "…" }], p);
  assert.equal(r.inject.length, 1, "the kept entry covers the kernel's own gap");
  // the real landing (a user atom with a real uuid, beyond base) finally retires it
  r = reconcile([{ kind: "user", md: "continue", uuid: "u-new" }], p);
  assert.equal(r.keep.length, 0, "a NEW landed atom is the one retire event");
  assert.equal(r.landed.length, 1);
});

test("the echo renders dragged-image THUMBNAILS — composer → provisional → landed, one continuum", () => {
  // the user 2026-08-25: the composer showed the thumbnail, the provisional dropped to path-only
  // text, the landing brought the thumbnail back — a flash in the middle. The echo now carries the
  // send's image paths and renders them through the LANDED form's own component (userImage with the
  // exact "path:" shape), so buildPathImg's (sid,path)-keyed cache serves the landed bubble the same
  // bytes and the reconcile swap never re-fetches or flickers.
  assert.match(RENDER, /if \(t\.imgPaths && t\.imgPaths\.length\) \{\s*\n\s*for \(const ip of t\.imgPaths\) bubble\.appendChild\(userImage\(\{ src: "path:" \+ ip, path: ip \}, true\)\);/);
  // the paths ride the send at every register site (deliver, staged flush, the provisional hold)
  assert.match(RENDER, /flushStaged\(sid, \{ text, cites, imgPaths: attached\.filter\(\(p\) => previewKind\(p\) === "img"\) \}\);/);
  assert.match(RENDER, /routeUserMessage\(sid, p\.text, [^\n]*p\.imgPaths\);/);   // each post of the release carries its images (staged-list-cap.test.ts executes the loop)
  // …and ONLY image-kind attachments mint thumbs — a dropped .csv stays the path text it always was
  assert.doesNotMatch(RENDER, /registerOptimistic\(sid, text, attached\)/);
});

test("the landing SWAP repaints even when it replaces the echo 1:1 — no lingering dashed bubble", () => {
  // upsert hands reconcileOptimistic a FRESH events array, and a landing frame that nets zero count
  // change (its user atom in, our bubble out) left syncView's rendered===len fast path skipping the
  // swap — the dashed echo lingered past its own landing until some later push (the 2026-08-25
  // continuity harness caught it). The per-sid signature survives the frame and marks the view stale
  // exactly when the visible echo set changes; the pop-and-reinject-same pass stays a no-op.
  assert.match(RENDER, /const echoShownSig = new Map<string, string>\(\);/);
  assert.match(RENDER, /if \(\(echoShownSig\.get\(s\.id\) \|\| ""\) !== sig\) \{/);
  assert.match(RENDER, /if \(sig\) echoShownSig\.set\(s\.id, sig\); else echoShownSig\.delete\(s\.id\);/);
  const fn = RENDER.split("function reconcileOptimisticInner(")[1].split("\nfunction ")[0];   // the guarded body (T262h)
  const settles = (fn.match(/settle\(/g) || []).length;
  assert.ok(settles >= 3, "every exit settles the signature (early returns included), got " + settles);
});
