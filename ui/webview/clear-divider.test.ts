// A /clear must not render as a dead gap (the user 2026-07-27). Three pieces, pinned at the source level
// (no jsdom harness, like compact-divider.test.ts):
//   1. LIVE: kind:"clearing" — an animated "Clearing conversation…" element (loader-dots motif) while the
//      SDK backend's clearing bracket is lit; the chip/tab/statusline read "clearing" off the shared
//      _session_chip derivation.
//   2. DURABLE: kind:"clear" — a collapsed "Conversation cleared" notice card at the top of the fresh
//      episode (above the system card, both auto-collapsed), so a cleared session is never events-empty
//      and the "No messages yet." placeholder can't lie about a conversation that existed.
//   3. LAZY: the card's body fetches the PRE-CLEAR conversation on first expand (loadEpisode →
//      chatEpisode), rendered through the same per-event renderers, cached per boundary so the chat's
//      per-push rebuilds never re-fetch.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const SDK = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "sdk_backend.py"), "utf8");
const TL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");

test("the ChatEvent union carries both /clear kinds, dispatched to their renderers", () => {
  assert.match(RENDER, /\| \{ kind: "clear"; clearedAt\?: number; episodes\?: number; ts\?: string; uuid\?: string; dropped\?: string\[\] \}/);
  assert.match(RENDER, /\| \{ kind: "clearing"; ts\?: string; uuid\?: string \}/);
  assert.match(RENDER, /if \(ev\.kind === "clearing"\) return renderClearing\(\);/);
  assert.match(RENDER, /if \(ev\.kind === "clear"\) return renderClear\(ev\);/);
});

test("renderClear is a folded SESSION notice keyed on the boundary, built by the one builder (2026-09-08)", () => {
  assert.match(RENDER, /const key = "clear:" \+ \(ev\.uuid \|\| sid\)/);
  assert.match(RENDER, /notice\(\{ src: "session", glyph: "clear", gist: "Conversation cleared — a fresh one starts here",/);
  assert.match(RENDER, /body, key, tip: dropped\.length \? "dropped: " \+ dropped\.join\(", "\) : undefined \}\);/);
  // folded by default: nothing pre-seeds the key open
  assert.doesNotMatch(RENDER, /openFolds\.add\("clear:/);
});

test("the pre-clear history lazy-loads once per boundary and renders through the shared renderers", () => {
  // fetch on FIRST expand only: cache + pending dedup guard the postMessage
  // 2026-09-08: the fetch rides the delegated head toggle (noticeOpened), reading the body's data-clear-key/-sid —
  // no per-node listener on the head (the tail rebuild destroyed it mid-press)
  assert.match(RENDER, /if \(!key \|\| !sid \|\| episodeCache\.has\(key\) \|\| episodePendingKey\.get\(sid\) === key\) return;/);
  assert.match(RENDER, /body\.dataset\.clearSid = sid;/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "loadEpisode", id: sid \}\)/);
  // the reply fills the card body in place, and re-renders serve from the cache
  assert.match(RENDER, /else if \(m\.type === "chatEpisode"\) chatEpisode\(m\);/);
  assert.match(RENDER, /episodeCache\.set\(key, got\)/);
  // the folded episode reuses renderEvent — the SAME renderers as the live transcript, not a text dump
  // (it passes the chained prior epoch, so the fold's rail marks day boundaries the same way too)
  assert.match(RENDER, /wrap\.appendChild\(renderEvent\(e, prior\)\)/);
  // a bounded render says what it cut (no silent truncation)
  assert.match(RENDER, /earlier event.*of the cleared conversation not shown/);
});

test("renderClearing is the loader-dots in-progress element, and the chip family knows 'clearing'", () => {
  // 2026-09-08: a slim live SESSION notice with the loader dots in its glyph slot (the romp severity: accent)
  assert.match(RENDER, /function renderClearing\(\): HTMLElement \{[\s\S]{0,200}?notice\(\{ src: "session", glyph: noticeLiveGlyph\(metaDots\(\)\), sev: "romp", gist: "Clearing conversation…", live: true,/);
  assert.match(fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "status-chip.ts"), "utf8"), /"clearing" \| "blocked"|clearing: "Clearing"/);   // ChipState + CHIP_LABEL entries (status-chip.ts since T322b)
  assert.match(RENDER, /⟳ Clearing conversation…/);                       // statusline line, like compacting's
  assert.match(TL, /label: 'Clearing'/);                                  // timeline lane badge
});

test("the styles exist: the quiet info severity, the live glyph slot, nested episode scope", () => {
  // 2026-09-08: the clear card is an INFO notice (dim rail); the clearing row rides the wide live glyph slot
  assert.match(CSS, /\.notice-sev-info\s+\{ --notice-rail: var\(--dim\);/);
  assert.match(CSS, /\.notice-glyph\.notice-glyph-wide \{ width: auto; \}/);
  assert.doesNotMatch(CSS, /\.notice-card-clear|\.clearing-line/);
  assert.match(CSS, /\.clear-episode \{/);
  assert.match(CSS, /\.clear-truncated \{/);
});

test("kernel: the boundary card leads the post-clear payload and the live indicator is event-based", () => {
  // the {kind:"clear"} insert rides episode_rows (row -1 = the boundary), above the system card
  assert.match(KERNEL, /boundary = _epi_rows\[-1\] if len\(_epi_rows\) >= 2 else None/);
  assert.match(KERNEL, /"kind": "clear", "uuid": "clear:%s" % \(boundary\.get\("head"\)/);
  // a cleared-but-empty fresh episode still gets events (the "No messages yet." lie is closed)
  assert.match(KERNEL, /if events or boundary:/);
  // the live indicator + the queued-"/clear" fold mirror the compacting pair
  assert.match(KERNEL, /events\.append\(\{"kind": "clearing"\}\)/);
  assert.match(KERNEL, /if clearing_now:/);
  // the chip says clearing first (shared _session_chip derivation, chat + timeline)
  assert.match(KERNEL, /"clearing" if _clearing_now\(sid\) else/);
  // loadEpisode is a one-shot direct reply, and build_episode fails LOUDLY on a missing transcript
  assert.match(KERNEL, /msg\.get\("type"\) == "loadEpisode"/);
  assert.match(KERNEL, /build_episode\(str\(msg\["id"\]\), int\(time\.time\(\)\)\)/);
  assert.match(KERNEL, /The pre-clear transcript file is missing/);
});

test("sdk backend: the clearing bracket is set on delivery and ends on exact events", () => {
  assert.match(SDK, /def _is_clear_cmd\(text: str\) -> bool:/);
  assert.match(SDK, /t == "\/clear" or t\.startswith\("\/clear "\)/);
  assert.match(SDK, /if _is_clear_cmd\(text\):[\s\S]{0,500}s\._clearing = True/);
  // restored-queue seed (a /clear queued when the kernel died still lights the bracket)
  assert.match(SDK, /if any\(_is_clear_cmd\(t\) for t in self\._pending\):[^\n]*\n\s*self\._clearing = True/);
  // ends: the lastSid-flipping init (the fork landed) + the turn's ResultMessage backstop
  assert.match(SDK, /self\.backend\._update_reg\(self\.sid, lastSid=fsid\)[\s\S]{0,400}self\._clearing = False/);
  assert.match(SDK, /self\._compacting = False\n\s*self\._clearing = False\s*# \/clear backstop/);
});

// executed twin of the python truth table — the command sniff is the bracket's trigger, so its shape
// is behavior, not style
test("_is_clear_cmd matches exactly the /clear invocations", () => {
  const isClear = (text: string): boolean => {
    const t = (text || "").trim();
    return t === "/clear" || t.startsWith("/clear ");
  };
  assert.equal(isClear("/clear"), true);
  assert.equal(isClear("  /clear  "), true);
  assert.equal(isClear("/clear now"), true);
  assert.equal(isClear("/clearx"), false);
  assert.equal(isClear("please /clear"), false);
  assert.equal(isClear("/compact"), false);
});
