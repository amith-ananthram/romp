// Conversation rewind — edit a past message (SDK sessions), the cloud-UI semantics: the composer's
// edit mode sends a rewindSend (branch from just before the edited bubble) and the clicking client
// overlays the outcome locally until the kernel's rewound payload arrives (the CLI reconnect takes
// seconds). render.ts has import-time DOM side effects → source pins + an executed replica of the
// reconcileRewind keep/retire decision (optimistic-send.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the edit affordance renders only on bubbles the backend can address", () => {
  // gate: genuine human bubble + transcript uuid + the session's _editable set (reconcileRewind:
  // SDK backend only, newer than the last compaction, not an optimistic echo)
  assert.match(RENDER, /if \(!romp && !injected && ev\.uuid && editSid\s*\n\s*&& \(sessions\.get\(editSid\) as any\)\?\._editable\?\.has\(ev\.uuid\)\)/);
  // the set is computed by rewind-reconcile.ts (executed there); render.ts hands it the backend and the echo prefix
  assert.match(RENDER, /sdk: s\.status\?\.backend === "sdk", now: Date\.now\(\), ttlMs: REWIND_TTL_MS, optPrefix: OPT_PREFIX/);   // a Codex session gets no edit affordance; echoes are excluded
  const PASS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "rewind-reconcile.ts"), "utf8");
  assert.match(PASS, /if \(events\[i\]\.kind === "compact"\) lastCompact = i;/);   // pre-compaction bubbles excluded
  assert.match(PASS, /!e\.uuid\.startsWith\(opts\.optPrefix\)/);              // optimistic echoes excluded
  // the button arms the composer's edit mode
  assert.match(RENDER, /edit\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); beginComposerEdit\(editSid, uuid, orig\); \}\);/);
});

test("sending in edit mode posts rewindSend and never a plain sendMessage", () => {
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "rewindSend", id: activeId, uuid: editing\.uuid, text: typed \}\);/);
  assert.match(RENDER, /pendingRewind\.set\(activeId, \{ uuid: editing\.uuid, text: typed, ts: Date\.now\(\) \}\);/);
  // the edit branch returns BEFORE any send path runs — since the staged flush (2026-08-15) the first
  // thing deliver does is release the staged stack, so preceding THAT is what proves an edit can never
  // fall through into a plain send (the routing itself now lives in routeUserMessage, defined earlier
  // in the file, so a source-index race against it would be meaningless)
  const editBranch = RENDER.indexOf('type: "rewindSend"');
  const stagedFlush = RENDER.indexOf("flushStaged(sid, {");
  assert.ok(editBranch > 0 && stagedFlush > 0 && editBranch < stagedFlush,
    "edit branch precedes deliver's first send action");
});

test("the composer edit chip cancels via its x and via Escape", () => {
  assert.match(RENDER, /const composerEdits = new Map<string, \{ uuid: string; orig: string \}>\(\);/);
  assert.match(RENDER, /x\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); cancelComposerEdit\(id\); \}\);/);
  assert.match(RENDER, /if \(activeId && composerEdits\.has\(activeId\)\) \{ cancelComposerEdit\(activeId\); return; \}/);
  // entering edit mode clears a citation chip — mixed goal/quote context would mislead the send
  assert.match(RENDER, /composerCitations\.delete\(sid\);\s*\/\/ an edit replaces the message wholesale/);
});

test("the pending-rewind overlay is wired into every ingest path and repaints mid-window", () => {
  const calls = RENDER.match(/reconcileRewind\(s(, from)?\);/g) || [];   // chatTail passes its from: the signature's bound (chat-exact-tail.test.ts)
  assert.ok(calls.length >= 4, "reconcileRewind wired into upsert + update + chatTail + the edit send, got " + calls.length);
  // the overlay touches MID-window turns — the append fast path won't repaint them without stale; stale is set
  // when the overlay or the editable set CHANGED (the tail path re-renders exactly what the kernel named, so
  // this signal is what repaints a prefix bubble — chat-exact-tail.test.ts)
  assert.match(RENDER, /if \(v && r\.stale\) v\.stale = true;\s*\/\/ the overlay or the editable set changed: MID-window turns repaint/);
  // chatTail reuses prefix event objects across pushes → stale rewound flags are stripped first (the pass
  // itself is rewind-reconcile.ts, executed by its own tests)
  const PASS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "rewind-reconcile.ts"), "utf8");
  assert.match(PASS, /for \(const e of events\) if \(e\.rewound\) delete e\.rewound;/);
  // abandoned turns dim via a class on the rendered turn
  assert.match(RENDER, /if \(\(ev as any\)\.rewound\) turn\.classList\.add\("rewound"\);/);
  assert.match(CSS, /\.turn\.rewound \{ opacity: 0\.35;/);
  assert.match(CSS, /\.turn-user:hover \.msg-edit/);   // reveal-on-hover, mirroring .code-copy
});

// Executed replica of reconcileRewind's keep/retire decision: the overlay holds while the kernel's
// payload still shows the OLD branch (the edited bubble's uuid resident), retires the moment the
// rewound payload lands (uuid gone) or the TTL backstop expires (a failed rewind warn-toasts —
// the tail must not stay dimmed forever).
test("the overlay retires when the old branch leaves the payload, or on TTL", () => {
  type Ev = { kind: string; uuid?: string; md?: string; rewound?: boolean; texts?: { md: string }[] };
  const REWIND_TTL_MS = 30_000;
  const apply = (events: Ev[], pr: { uuid: string; text: string; ts: number }, now: number) => {
    const idx = events.findIndex((e) => e.kind === "user" && e.uuid === pr.uuid);
    if (idx < 0 || now - pr.ts > REWIND_TTL_MS) return { kept: false, events };
    events[idx] = { ...events[idx], md: pr.text };
    for (let j = idx + 1; j < events.length; j++) events[j].rewound = true;
    for (let j = events.length - 1; j > idx; j--) {
      const e = events[j];
      if (e.kind === "queued" && Array.isArray(e.texts)) {
        e.texts = e.texts.filter((t) => t.md !== pr.text);
        if (!e.texts.length) events.splice(j, 1);
      }
    }
    return { kept: true, events };
  };
  const T0 = 1_000_000;
  const pr = { uuid: "u2", text: "second ask, edited", ts: T0 };

  // the gap: old branch still resident → the edited bubble swaps text, the tail dims,
  // and the backend's queued chip for the same text is suppressed (no double display)
  const gap = apply([
    { kind: "user", uuid: "u1", md: "first" }, { kind: "assistant", uuid: "a1", md: "r1" },
    { kind: "user", uuid: "u2", md: "second ask" }, { kind: "assistant", uuid: "a2", md: "r2" },
    { kind: "queued", texts: [{ md: "second ask, edited" }] },
  ], pr, T0 + 1000);
  assert.equal(gap.kept, true);
  assert.equal(gap.events[2].md, "second ask, edited");
  assert.equal(gap.events[3].rewound, true);
  assert.equal(gap.events.length, 4);                       // the queued chip is gone
  assert.equal(gap.events[0].rewound, undefined);           // nothing BEFORE the edit dims

  // the rewound payload landed: the old uuid is gone (new branch has a fresh user record) → retire
  const landed = apply([
    { kind: "user", uuid: "u1", md: "first" }, { kind: "assistant", uuid: "a1", md: "r1" },
    { kind: "user", uuid: "u9", md: "second ask, edited" },
  ], pr, T0 + 4000);
  assert.equal(landed.kept, false);

  // TTL backstop: the rewind failed (kernel warn-toasted) — stop dimming, show the truth
  const expired = apply([{ kind: "user", uuid: "u2", md: "second ask" }], pr, T0 + REWIND_TTL_MS + 1);
  assert.equal(expired.kept, false);
});

// the kernel side of the contract this client codes against (node-tests-pin-kernel-source precedent)
test("kernel: rewindSend validates against the transcript and the SDK backend applies one-shot", () => {
  const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
  const BACKEND = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");
  assert.match(KERNEL, /elif t == "rewindSend" and msg\.get\("uuid"\) and msg\.get\("text"\):/);
  assert.match(KERNEL, /def _rewind_target\(path, sid, user_uuid\):/);
  // merge-safe beside the fork's extra_args (both write the same designed --resume-session-at passthrough)
  assert.match(BACKEND, /kw\.setdefault\("extra_args", \{\}\)\["resume-session-at"\] = sess\._rewind_to/);
});

test("a restore-files affordance rides each editable bubble — workspace only, armed like delete (the user 2026-08-04)", () => {
  // the SDK's file-checkpoint rewind: files go back to before this message, the conversation stays
  assert.match(RENDER, /const rf = el\("button", "msg-restorefiles"\) as HTMLButtonElement;/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "rewindFiles", id: editSid, uuid \}\);/);
  // two-click arm, disarmed on blur/pointerleave — every miss fails toward "not restored"
  assert.match(RENDER, /if \(!rf\.classList\.contains\("armed"\)\) \{ rf\.classList\.add\("armed"\); rf\.textContent = "revert files\?"; return; \}/);
  assert.match(RENDER, /rf\.addEventListener\("blur", rfDisarm\);/);
  assert.match(RENDER, /rf\.addEventListener\("pointerleave", rfDisarm\);/);
  // acknowledged immediately on fire; a re-render resets the label
  assert.match(RENDER, /rf\.disabled = true; rf\.textContent = "restoring…";/);
});
