// The "queued" indicator (the user's messages submitted while a session is still working). It's the SAME
// generic {kind:"queued"} ChatEvent for BOTH backends — the kernel feeds it from the transcript queue-ops
// for tmux and from SdkBackend.pending_queued for SDK (business 2026-06-23). So pinning the one render path
// confirms the dot shows for either backend. The renderer has no jsdom harness, so pin the wiring at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a queued ChatEvent carries the pending messages (backend-agnostic, per-message md)", () => {
  // idx = backend-queue position (SDK); park = _pending_ops position (compaction/model parking, any backend)
  // `optimistic` (romp's own unconfirmed echo) rides along at the end — see optimistic-send.test.ts
  assert.match(RENDER, /kind: "queued"; texts: \{ md: string; followUp\?: boolean; goal\?: string; fuCtx\?: string; idx\?: number; park\?: number; cancelable\?: boolean; optimistic\?: boolean; romp\?: boolean; rompSystem\?: boolean; rompAuto\?: boolean; gist\?: string; imgPaths\?: string\[\]; lost\?: string; qts\?: number; qid\?: string; held\?: boolean; hiddenByPending\?: boolean; landing\?: boolean \}\[\]/);   // gist: a queued romp SYSTEM notice's user-facing head (2026-09-08); imgPaths: the echo's dragged-image thumbnails (2026-08-25); romp flags: T243; lost + qts: the pending entry's connection-drop state and its identity for the ✕ (2026-09-06)
});

test("renderQueued draws a wireframe-hourglass header (singular/plural) + one markdown bubble per queued message", () => {
  assert.match(RENDER, /ev\.kind === "queued"\) return isOptimistic\(ev\) && ev\.bare \? renderPendingGroup\(ev\) : renderQueued\(ev\)/);   // OUR pending group keeps its node (T262h)
  assert.match(RENDER, /el\("div", "turn turn-queued"\)/);
  // header: a stroked accent-blue hourglass ICON (no ⌛ emoji) + "N queued message(s)" — pluralizes on count
  assert.doesNotMatch(RENDER, /⌛/, "no hourglass emoji — it clashes with the app's line-icon style");
  assert.match(RENDER, /head\.appendChild\(hourglassIcon\(\)\)/);
  assert.match(RENDER, /function hourglassIcon\(\): HTMLElement/);
  assert.match(RENDER, /stroke="currentColor"[\s\S]*?<path d="M4 3 H12 L8 8 L12 13 H4 L8 8 Z"\/>/, "wireframe hourglass path");
  // noun matches the content: all-commands → "command", all-prose → "message", mixed → "item" (the user 2026-07-01)
  assert.match(RENDER, /const noun = nCmd === n \? "command" : nSys === n \? "notice" : nNudge === n \? "nudge"\s*\n\s*: \(nCmd === 0 && nSys === 0 && nNudge === 0\) \? "message" : "item";/);   // romp's own entries: T243
  assert.match(RENDER, /return `\$\{n\} queued \$\{noun\}\$\{n === 1 \? "" : "s"\}`/);
  assert.match(RENDER, /label\.textContent = \(texts\.every\(\(t\) => t\.landing\) \? `\$\{n\} \$\{n === 1 \? "message" : "messages"\} landing…` : queuedCountText\(n, nCmd, nSys, nNudge\)\) \+ why;/);   // a held-only group says landing (T262i)
  assert.match(RENDER, /el\("div", "queued-head"\)/);
  // one faint "you" bubble per pending message, rendered as markdown (like a landed message — the
  // user-text renderer, newlines kept, so the queued→landed swap changes nothing on screen)
  assert.match(RENDER, /for \(const t of texts\)[\s\S]*?el\("div", "queued-bubble md" \+ \(t\.cancelable \? " cancelable" : ""\)\s*\n\s*\+ \(t\.romp \? " queued-romp" : ""\) \+ \(t\.rompSystem \? " queued-sys" : ""\)\)/);
  assert.match(RENDER, /if \(!t\.romp && !isCmd\) bubble\.innerHTML = userMd\(t\.md\)/);
});

test("a queued slash command renders as a command chip, not a plain 'message' (the user 2026-07-01)", () => {
  // the SAME helper the landed user turn uses, so a queued /compact reads as a COMMAND
  assert.match(RENDER, /function renderSlashCmd\(bubble: HTMLElement, text: string\): boolean/);
  assert.match(RENDER, /el\("span", "slash-cmd-chip"\)/);
  // the header counts commands vs. prose to pick the noun
  assert.match(RENDER, /const nCmd = texts\.filter\(\(t\) => SLASH_CMD_RE\.test\(t\.md\)\)\.length;/);
});

test("a cancelable queued bubble carries an explicit ✕ — messages AND parked commands (the user 2026-07-08)", () => {
  // every stage cancels: the backend's own queue (idx), ops parked during compaction/model switches
  // (park), and the pre-confirmation optimistic echo (qopt — the 2026-08-30 rule: labeled and
  // cancellable from the instant send is pressed)
  assert.match(RENDER, /if \(t\.cancelable && \(t\.idx !== undefined \|\| t\.park !== undefined \|\| t\.optimistic\)\)/);
  assert.match(RENDER, /if \(t\.optimistic\) x\.dataset\.qopt = "1";/);
  assert.match(RENDER, /el\("button", "queued-x"\)/);
  assert.match(RENDER, /x\.dataset\.act = "qx";/, "the ✕ routes through the stable document.body delegate");
  assert.match(RENDER, /if \(t\.idx !== undefined\) x\.dataset\.qidx = String\(t\.idx\);/);
  assert.match(RENDER, /if \(t\.park !== undefined\) x\.dataset\.qpark = String\(t\.park\);/);
  // the OLD whole-bubble click is gone — it was undiscoverable and a per-render listener (mid-press
  // rebuilds ate the click); the bubble itself must carry no listener now
  assert.doesNotMatch(RENDER, /bubble\.addEventListener\("click"/);
  assert.doesNotMatch(CSS, /\.queued-bubble\.cancelable \{ cursor: pointer/);
  assert.match(CSS, /\.queued-x \{/);
  assert.match(CSS, /\.queued-x:hover \{ color: var\(--vscode-errorForeground/, "red on hover = the remove reading");
});

test("the delegated qx handler cancels click-safely: kernel op + composer restore for messages only", () => {
  // one handler on document.body (stable across every per-push rebuild) — never a per-render listener
  assert.match(RENDER, /qx: \(el\) => \{/);
  assert.match(RENDER, /\{ type: "cancelQueued", id: sidQ, md: qmd \}/, "the body rides along as the kernel's drift guard; owner-scoped for the popover (2026-08-26, sidQ = owningSidOf ?? activeId)");
  assert.match(RENDER, /if \(el\.dataset\.qidx !== undefined\) msg\.idx = Number\(el\.dataset\.qidx\);/);
  assert.match(RENDER, /if \(el\.dataset\.qpark !== undefined\) msg\.park = Number\(el\.dataset\.qpark\);/);
  // a MESSAGE returns to the composer to re-edit; a slash COMMAND (qcmd) just cancels
  assert.match(RENDER, /if \(qmd && el\.dataset\.qcmd !== "1" && el\.dataset\.qromp !== "1"\) \{/);
  assert.match(RENDER, /restoreToComposer\(edx && edx\.open && edx\.text\.trim\(\) \? edx\.text : qmd\);/, "the message's words come back — the words being edited when a field was open on it (T306)");
  assert.match(RENDER, /const bub = el\.closest\("\.queued-bubble"\) as HTMLElement \| null;[\s\S]*?bub\?\.remove\(\);/,
    "optimistic removal before the next push");
  // restoreToComposer fills the composer textarea, fires input (autosize/enable), focuses, caret to end
  assert.match(RENDER, /function restoreToComposer\(text: string\)/);
  assert.match(RENDER, /getElementById\("composer-input"\)/);
  assert.match(RENDER, /dispatchEvent\(new Event\("input"/);
});

// ---- the loud already-delivered failure (the user 2026-07-20) ----------------------------------------
// A ✕ whose target had already been HANDED TO THE CLI (the SDK forwards queued sends mid-turn; no recall
// exists in the control protocol — the CLI folds its queue into the running turn) used to silently no-op
// kernel-side while the client removed the bubble and restored the text: a fake delete, answered anyway.

const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SDKBE = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");

test("the kernel answers every cancelQueued with an authoritative cancelResult frame", () => {
  assert.equal(KERNEL.split('"type": "cancelResult"').length - 1, 3, "one reply per cancel arm (park + idx + the 2026-08-30 md-only optimistic arm)");
  assert.match(KERNEL, /"ok": not err/);
  assert.match(KERNEL, /def _cancel_miss_text\(md\):/, "the 'too late' wording is built kernel-side");
  assert.match(KERNEL, /too late to cancel — the message already reached the session/);
});

test("the ✕ only renders while a recall can still win (queue_recallable gates cancelable)", () => {
  assert.match(KERNEL, /cancelable = hasattr\(_cbe, "unqueue"\) and _queue_recallable\(_cbe, sid\)/);
  assert.match(SDKBE, /def queue_recallable\(self, sid: str\) -> bool:/);
  assert.match(SDKBE, /def unqueue\(self, idx: int, expect: str \| None = None, qid: str \| None = None\)/,
    "the pop re-verifies the exact text, or locates the copy by its id, under the session lock: never a wrong-message cancel");
});

test("a queued bubble with no ✕ says where the message actually is", () => {
  assert.match(RENDER, /else if \(!t\.cancelable && t\.idx !== undefined\)/);
  assert.match(RENDER, /queued in the session — it can't be recalled, and joins the conversation at the session's next step/);
});

test("the qx click stashes the composer before/after so a failed cancel can undo the restore", () => {
  assert.match(RENDER, /const pendingCancelRestores = new Map<string, \{ before: string; after: string \}>\(\);/);
  assert.match(RENDER, /pendingCancelRestores\.set\(activeId \+ " " \+ qmd, \{ before, after: ta \? ta\.value : "" \}\);/);
});

test("cancelResult ok:false toasts the kernel's 'too late' and reverts an untouched composer restore", () => {
  assert.match(RENDER, /m\.type === "cancelResult" && typeof m\.id === "string"/);
  assert.match(RENDER, /pendingCancelRestores\.delete\(key\);/, "the stash is one-shot, ok or not");
  assert.match(RENDER, /if \(typeof m\.text === "string" && m\.text\) warnToast\(m\.text\);/);
  // the undo fires ONLY when the draft still exactly equals the post-restore value — an edited draft
  // is the user's now; the toast alone covers it
  assert.match(RENDER, /if \(ta && ta\.value === stash\.after\) \{[\s\S]*?ta\.value = stash\.before;/);
});

// Executed replica of the untouched-draft guard — the one branchy bit worth running, not just pinning.
test("the revert guard: untouched drafts revert, edited drafts stay", () => {
  const revert = (value: string, stash: { before: string; after: string }): string =>
    value === stash.after ? stash.before : value;
  const stash = { before: "", after: "forget the obsidian thing" };
  assert.equal(revert("forget the obsidian thing", stash), "", "untouched → back to the pre-click draft");
  assert.equal(revert("forget the obsidian thing, actually keep it", stash),
    "forget the obsidian thing, actually keep it", "edited → the user owns it now");
});

// ---- the ✕'d message that stayed on screen (the user 2026-07-24) -------------------------------------
// Cancelling the only queued message left its group behind: the header sat there alone still reading
// "1 queued message" with no bubble under it, and it never went away. Three separate holes, one per test.

test("the ✕ reflows the GROUP, not just the bubble — the last one out takes the header with it", () => {
  assert.match(RENDER, /function reflowQueuedGroup\(turn: HTMLElement\): void/);
  assert.match(RENDER, /if \(grp\) reflowQueuedGroup\(grp\);/, "called from the qx handler in the same breath");
  assert.match(RENDER, /if \(!bubbles\.length\) \{ turn\.remove\(\); return; \}/, "empty group → the whole turn goes");
  // still-populated group → the count is rewritten from what's actually left, keeping the held/ask suffix
  assert.match(RENDER, /label\.textContent = queuedCountText\(bubbles\.length, nCmd, nSys, nNudge\) \+ \(label\.dataset\.why \|\| ""\);/);
  assert.match(RENDER, /label\.dataset\.why = why;/, "renderQueued parks the suffix for the recount to reuse");
});

// Executed replica of the recount rule the ✕ and renderQueued share — the branchy bit, run not just pinned.
test("queuedCountText: the noun follows what's left after a cancel", () => {
  const countText = (n: number, nCmd: number): string => {
    const noun = nCmd === n ? "command" : nCmd === 0 ? "message" : "item";
    return `${n} queued ${noun}${n === 1 ? "" : "s"}`;
  };
  assert.equal(countText(2, 0), "2 queued messages");
  assert.equal(countText(1, 0), "1 queued message", "singular after cancelling one of two");
  assert.equal(countText(1, 1), "1 queued command", "the prose one went → the noun follows");
  assert.equal(countText(2, 1), "2 queued items", "mixed stays 'items'");
});

test("a chatTail that SHRINKS the transcript repaints (the no-op fast path can't be trusted there)", () => {
  // The kernel's diff lands on `from === new length` when the tail simply lost an event, so lowering
  // v.rendered to `from` leaves rendered === len and syncView's fast path skips the repaint — the retired
  // turn stays in the DOM for good. The shrink is measured BEFORE the reconciles so it reflects the splice.
  assert.match(RENDER, /const wasLen = s\.events\.length;[\s\S]*?s\.events\.length = from;/);
  assert.match(RENDER, /const shrank = s\.events\.length < wasLen;/);
  assert.match(RENDER, /v\.rendered = Math\.min\(v\.rendered, from\);[\s\S]*?if \(shrank\) v\.stale = true;/);
  // the fast path this defends against — pinned so a rewrite of it can't silently reopen the hole
  assert.match(RENDER, /if \(v\.rendered === len && !v\.stale && v\.el\.childNodes\.length > 0\) return v;/);
});

test("a FAILED cancel puts the bubble back, so the screen agrees with the 'too late' toast", () => {
  // ok:false means the message is still going through, but the kernel's build never changed — so its next
  // delta carries no repaint and the optimistic delete would stand: shown as cancelled, answered anyway.
  assert.match(RENDER, /const rv = m\.id === activeId && activeId \? views\.get\(activeId\) : null;\s*\n\s*if \(rv\) \{ rv\.stale = true; appendActive\(\); \}/);
});

test("the queued-header hourglass uses the accent blue, like the feed/mail toggle icons", () => {
  assert.match(CSS, /\.queued-head \.queued-icon \{ color: var\(--accent\)/);
});

test("the queued turn + bubbles are styled (so the dot is actually visible)", () => {
  assert.match(CSS, /\.turn-queued/);
  assert.match(CSS, /\.queued-head/);
  assert.match(CSS, /\.queued-bubble/);
});

// ---- a queue the ACCOUNT is holding (the user 2026-07-24) --------------------------------------------
// Hitting a usage limit turned every following gesture into its own failure: /compact came back refused
// ("this would take you over your limit"), the message typed after it went straight out and landed as a red
// API-error card, and the order the user meant to say things in was lost. Now a limit parks it ALL in the
// same FIFO a compaction parks it in — messages and slash commands alike, since the limit is on the ACCOUNT
// — and the queued bubbles say what they are waiting for instead of just sitting there.

test("the kernel parks every drive op while the account can't serve one, and drains at the reset", () => {
  assert.match(KERNEL, /def _limit_hold\(sid\):/);
  assert.match(KERNEL, /or _limit_hold\(sid\) is not None\)/, "the gate /model, /effort and /compact pass");
  // the send path needs its OWN arm, ahead of the forwards_sends handoff: an SDK backend takes a send even
  // mid-turn, so without this the message goes straight out and comes back an API error
  assert.match(KERNEL, /if _compacting_now\(sid\) or _pending_ops\.get\(sid\) or _limit_hold\(sid\):/);
  // the drain's account gate is its own step, checked before the transcript refresh and the compacting/working
  // reads (2026-09-05): a held session is not re-parsed for a verdict the hold already decided
  assert.match(KERNEL, /if _limit_hold\(sid\):\n\s+continue\s+# the account can't serve a request yet/, "the drain gate");
  assert.match(KERNEL, /if _compacting_now\(sid\) or _working_now\(sid\):\n\s+continue/, "the drain's quiet gate");
  // RELEASE rides the API's own stamp — no romp-invented timer, and no clock promised without one
  assert.match(KERNEL, /"resetsAt": max\(known\) if len\(known\) == len\(resets\) else None,/);
  assert.match(KERNEL, /known = \[r for r in resets if isinstance\(r, \(int, float\)\) and r > 0\]/);
});

test("a held queue says WHY it isn't moving, and outranks the pending-ask note", () => {
  assert.match(RENDER, /held\?: \{ reason: string; resetsAt\?: number \| null; what: string; detail\?: string \}/);
  // `detail` carries the CLI's own sentence when the limit REFUSED THE LAUNCH (the user 2026-07-28) —
  // that flavor reports a wall-clock reset, not an epoch, so it has no countdown to render and the exact
  // words go one level deeper instead of into the one-line head.
  assert.match(RENDER, /if \(held\?\.detail\) label\.title = held\.detail;/);
  assert.match(RENDER, /const askNote = \(pendingAsk \? " · sends after you answer" : ""\);/);
  assert.match(RENDER, /\? ` · \$\{held\.what\}` \+ \(held\.resetsAt \? ` · in \$\{fmtReset\(held\.resetsAt, Math\.floor\(Date\.now\(\) \/ 1000\)\)\}` : ""\)/);
});

// Executed replica of the head-suffix decision — the branchy bit, run rather than only pinned.
test("the head suffix: the hold beats the ask note, and no reset stamp means no countdown", () => {
  const suffix = (held: { what: string; resetsAt?: number | null } | undefined, pendingAsk: boolean) => {
    const askNote = (pendingAsk ? " · sends after you answer" : "");
    return held ? ` · ${held.what}` + (held.resetsAt ? " · in 42m" : "") : askNote;
  };
  assert.equal(suffix(undefined, false), "");
  assert.equal(suffix(undefined, true), " · sends after you answer");
  assert.equal(suffix({ what: "waiting for your usage limit to reset", resetsAt: 1784930000 }, true),
    " · waiting for your usage limit to reset · in 42m",
    "the limit is what the queue waits on, not the question — answering it would move nothing");
  assert.equal(suffix({ what: "waiting for your monthly spend limit to be raised", resetsAt: null }, false),
    " · waiting for your monthly spend limit to be raised",
    "a spend cap has no readable reset — say the reason, promise no clock");
});

// ── T243 (the user 2026-09-07, with a screenshot): a QUEUED message romp itself injected — a watch notice
// sitting in the "2 queued messages" group — rendered as an ordinary person-style bubble (dashed blue, ✕),
// while the same message once LANDED renders as the gray romp notice card. The queued form now wears the
// landed romp grammar: the kernel flags the queued entry from the same markers it flags landed messages
// with (romp / rompSystem / rompAuto), renderQueued draws the notice card (gray tone, romp chip, marker tail
// hidden, gist collapsed by default), the ✕ stays (a notice cancels without a composer restore), and the
// header noun counts it honestly.
test("the kernel flags a romp-injected queued entry from the same markers as a landed message (T243)", () => {
  assert.match(KERNEL, /def _queued_romp_flags\(text\):/);
  assert.match(KERNEL, /if "<!-- romp-injected -->" in t:\s*\n\s*out\["romp"\] = True/);
  assert.match(KERNEL, /if "<!-- romp-system -->" in t:\s*\n\s*out\["rompSystem"\] = True/);
  assert.match(KERNEL, /if "<!-- romp-auto -->" in t:\s*\n\s*out\["rompAuto"\] = True/);
  // both the backend queue and the parked ops get the flags
  assert.match(KERNEL, /m = \{"md": body, "idx": i, "cancelable": cancelable, \*\*_queued_romp_flags\(t\)\}/);
  assert.match(KERNEL, /m = \{"md": _parked_md\(op\), "park": j, "cancelable": True, \*\*\(_queued_romp_flags\(op\[1\]\) if op\[0\] == "send" else \{\}\)\}/);
});

test("what romp itself queued wears the LANDED romp grammar, split as landed: notice card vs gray romp bubble (T243)", () => {
  assert.match(RENDER, /romp\?: boolean; rompSystem\?: boolean; rompAuto\?: boolean; gist\?: string; imgPaths\?: string\[\]; lost\?: string; qts\?: number; qid\?: string; held\?: boolean; hiddenByPending\?: boolean; landing\?: boolean \}\[\]/, "the queued text shape carries the flags (+ the gist, 2026-09-08)");
  const body = RENDER.split("function renderQueued(")[1].split("\nfunction ")[0];
  assert.match(body, /const bubble = el\("div", "queued-bubble md" \+ \(t\.cancelable \? " cancelable" : ""\)\s*\n\s*\+ \(t\.romp \? " queued-romp" : ""\) \+ \(t\.rompSystem \? " queued-sys" : ""\)\);/);
  // a SYSTEM notice → the landed card's own builder, nested; a one-line notice gets no body repeating its head
  // 2026-09-08 (the notice-vocabulary pass): the landed card's ONE builder, nested; the kernel's gist is the head
  assert.match(body, /if \(t\.rompSystem\) \{[\s\S]*?const gist = t\.gist \? String\(t\.gist\) : gistOf\(text\);[\s\S]*?if \(more\) nb\.innerHTML = md\(text\);[\s\S]*?notice\(\{ src: "romp", glyph: "romp", sev: "romp", gist, body: nb,[\s\S]*?key: "qromp:" \+ qkey \+ ":" \+ gist\.slice\(0, 24\), nested: true/);
  // any other romp message → the gray romp bubble with the landed gist rule (follow-up · goal / nudged for a status update · goal / first line)
  assert.match(body, /\} else if \(t\.romp\) \{[\s\S]*?el\("div", "romp-tag"\)[\s\S]*?const rb = el\("div", "romp-bubble md"\);[\s\S]*?const gist = t\.followUp \? "follow-up" \+ \(t\.goal \? " · " \+ t\.goal : ""\)\s*\n\s*: t\.rompAuto \? "nudged for a status update" \+ \(t\.goal \? " · " \+ t\.goal : ""\)/);
  assert.match(body, /rb\.dataset\.act = "nudgetoggle";[\s\S]*?const nkey = "qnudge:" \+ qkey \+ ":" \+ gist\.slice\(0, 24\);/);
  // folds are keyed by the text's hash plus its occurrence — never the queue slot (it renumbers), never text alone
  assert.match(RENDER, /function strHash32\(str: string\): string/);
  assert.match(body, /const sig = strHash32\(t\.md\);\s*\n\s*const before = seenSig\.get\(sig\) \|\| 0;\s*\n\s*seenSig\.set\(sig, before \+ 1\);\s*\n\s*const nth = \(totalSig\.get\(sig\) \|\| 1\) - before - 1;/,
    "the fold identity counts the identical texts AFTER the entry — the queue drains from the front");
  // the nudge's ✕ lives in its bubble's corner; the wrapper is the landed right-aligned column and the nested
  // bubble sheds its own 72% cap (T243 follow-up — measured in queued-romp-layout.test.ts)
  assert.match(body, /xHost = rb;/); assert.match(body, /xHost\.appendChild\(x\);/);
  assert.match(CSS, /\.queued-bubble\.queued-romp:not\(\.queued-sys\) \{ display: flex; flex-direction: column; align-items: flex-end; \}/);
  assert.match(CSS, /\.queued-bubble\.queued-romp > \.romp-bubble \{ max-width: none; position: relative; \}/);
  // the marker tail and the [romp] prefix are hidden exactly the way the landed forms hide them
  assert.match(body, /t\.md\.replace\(\/<!--\[\\s\\S\]\*\?-->\/g, ""\)\.replace\(\/\^\\s\*\\\[romp\\\]\\s\*\/i, ""\)\.trim\(\)/);
  // a romp entry never wears the ↩ follow-up header (the landed romp turn suppresses it too)
  assert.match(body, /if \(t\.followUp && !t\.romp\) turn\.appendChild\(followUpHeader/);
  // the plain path is untouched
  assert.match(body, /if \(!t\.romp && !isCmd\) bubble\.innerHTML = userMd\(t\.md\)/);
  // the ✕ stays; romp's words are never restored to the composer on cancel
  assert.match(body, /x\.title = t\.rompSystem \? "cancel this queued notice" : t\.romp \? "cancel this queued nudge"/);
  assert.match(body, /if \(t\.romp\) x\.dataset\.qromp = "1";/);
  assert.match(RENDER, /if \(qmd && el\.dataset\.qcmd !== "1" && el\.dataset\.qromp !== "1"\)/);
  // the gray tone replaces the dashed blue on the romp variants; the ✕ room is reserved only when there is a ✕
  assert.match(CSS, /\.queued-bubble\.queued-romp \{[^}]*background: transparent;[^}]*border: 0;/);
  assert.match(CSS, /\.queued-bubble\.queued-romp\.cancelable > \.notice,\s*\n\s*\.queued-bubble\.queued-romp\.cancelable > \.romp-bubble \{ padding-right: 30px; \}/);   // .notice since 2026-09-08
  // the landed system notice shares the one-liner rule: no body repeating a one-line head
  assert.match(RENDER, /if \(more\) body\.innerHTML = md\(text\);[\s\S]{0,200}?key: ev\.uuid \? "rsys:" \+ ev\.uuid : undefined/);
});

test("the header noun counts romp's own entries honestly (T243)", () => {
  assert.match(RENDER, /function queuedCountText\(n: number, nCmd: number, nSys = 0, nNudge = 0\): string/);
  assert.match(RENDER, /const nSys = texts\.filter\(\(t\) => !!t\.rompSystem\)\.length;/);
  assert.match(RENDER, /const nNudge = texts\.filter\(\(t\) => !!t\.romp && !t\.rompSystem\)\.length;/);
  // the ✕'s recount sees them too
  assert.match(RENDER, /const nSys = bubbles\.filter\(\(b\) => b\.classList\.contains\("queued-sys"\)\)\.length;/);
  assert.match(RENDER, /const nNudge = bubbles\.filter\(\(b\) => b\.classList\.contains\("queued-romp"\) && !b\.classList\.contains\("queued-sys"\)\)\.length;/);
});

// executed replica of the extended noun rule — run, not just pinned (matches the function's text above)
test("queuedCountText: all notices → 'notice'; all nudges → 'nudge'; anything mixed → 'items'", () => {
  const countText = (n: number, nCmd: number, nSys = 0, nNudge = 0): string => {
    const noun = nCmd === n ? "command" : nSys === n ? "notice" : nNudge === n ? "nudge"
      : (nCmd === 0 && nSys === 0 && nNudge === 0) ? "message" : "item";
    return `${n} queued ${noun}${n === 1 ? "" : "s"}`;
  };
  assert.equal(countText(1, 0, 1, 0), "1 queued notice");
  assert.equal(countText(2, 0, 2, 0), "2 queued notices");
  assert.equal(countText(1, 0, 0, 1), "1 queued nudge");
  assert.equal(countText(2, 0, 1, 0), "2 queued items", "a notice among the user's messages — 'items', never 'messages'");
  assert.equal(countText(2, 0, 1, 1), "2 queued items", "a notice and a nudge — 'items'");
  assert.equal(countText(2, 0, 0, 0), "2 queued messages");
  assert.equal(countText(1, 1, 0, 0), "1 queued command");
});
