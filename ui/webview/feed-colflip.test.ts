// A card's column must not bounce through a stale payload after a reply — and when a view DOES
// bounce, the bounce must leave a record (the user 2026-07-28).
//
// The incident: the user replied to a Completed card, watched it fly to Working, flash back to
// Completed, then return to Working. Post-hoc, every layer checked out sound — the store held
// "working" the whole window, the snapshot punch applied, the ack/buildId machinery held — and
// nothing anywhere recorded what the view actually rendered, so the bounce could not be attributed.
// Two fixes, pinned here:
//
// (1) KERNEL: the _views_dirty check compared the mark against the cached build's FINISH time, so a
//     reply landing while a ~1-1.6s build was in flight was swallowed when that build completed —
//     its payload predates the gesture, but its finish postdates it — and the pre-reply payload
//     (card still Completed) was re-served until the next sig bust. The dirty floor now keys on the
//     build's START; REBUILD_MIN_S stays on the finish (it rate-limits build cost).
//
// (2) CLIENT: every rendered column change posts a clientDiag breadcrumb (what moved, from/to, the
//     input event the render reflects, the payload's buildId, whether a prediction was live), so
//     the next bounce is read from client-diag.jsonl instead of unreproducible archaeology.
//
// Source-level pins (no jsdom for the feed renderer), mirroring feed-move-ack.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");

test("kernel: the dirty floor keys on build START, so a mid-build mutation still rebuilds", () => {
  // the cache rows carry both stamps: finish (REBUILD_MIN_S) and start (the dirty floor)
  assert.match(KERNEL, /_built_feed = \[None, None, 0\.0, 0\.0\]/);
  assert.match(KERNEL, /_built_timeline = \[None, None, 0\.0, 0\.0\]/);
  // both caches compare the dirty mark against the START slot, never the finish
  const dirtyChecks = KERNEL.match(/_views_dirty\[0\] > e\[3\]/g) || [];
  assert.equal(dirtyChecks.length, 2, "feed AND timeline key dirty on build start");
  assert.ok(!/_views_dirty\[0\] > e\[2\]/.test(KERNEL), "no dirty check keys on build finish");
  // the start stamp is taken BEFORE the build runs, in both caches
  const feedBody = KERNEL.slice(KERNEL.indexOf("def _cached_feed"), KERNEL.indexOf("def _cached_timeline"));
  assert.ok(feedBody.indexOf("started = time.time()") < feedBody.indexOf("build_feed(now, live_map)"),
    "feed: start stamped before the build reads anything");
  assert.match(feedBody, /_built_feed\[:\] = \[sig, feed, time\.time\(\), started\]/);
  const tlBody = KERNEL.slice(KERNEL.indexOf("def _cached_timeline"), KERNEL.indexOf("def _run_tier"));
  assert.ok(tlBody.indexOf("started = time.time()") < tlBody.indexOf("build_timeline(now, live_map)"),
    "timeline: start stamped before the build reads anything");
  assert.match(tlBody, /_built_timeline\[:\] = \[sig, tl, time\.time\(\), started\]/);
  // the rebuild rate limit still keys on the finish, so back-to-back starts don't shrink its window
  assert.match(feedBody, /\(time\.time\(\) - e\[2\]\) < REBUILD_MIN_S/);
  assert.match(tlBody, /\(time\.time\(\) - e\[2\]\) < REBUILD_MIN_S/);
});

test("feed: every rendered column change posts a colflip breadcrumb with its cause", () => {
  // the audit runs on what the render SHOWS: after the optimistic overlay is applied
  const render = FEED.slice(FEED.indexOf("function render()"));
  assert.ok(render.indexOf("applyFollowMove(asks)") < render.indexOf("auditShownColumns(asks)"),
    "audit reads the post-prediction columns — the thing the user actually sees");
  // a flip posts the generic clientDiag breadcrumb the kernel already persists
  assert.match(FEED, /type: "clientDiag", surface: "feed", what: "colflip"/);
  assert.match(FEED, /from: prev, to: a\.column, ev: lastFeedEvent,/);
  assert.match(FEED, /buildId: lastPayloadBuildId, predicted: pendingFollowMove\.has\(a\.itemId\)/);
  // ...and the kernel side of that channel exists (strip.test.ts pins its shape)
  assert.ok(KERNEL.includes('"clientDiag"') && KERNEL.includes("client-diag.jsonl"));
});

test("feed: every prediction drop names WHY, so a bounce's trigger is in the trail", () => {
  // each clearFollowMove call site attributes itself; the whys the incident needs to tell apart
  for (const why of ["backstop-noack", "backstop-noconfirm", "ack-fail", "gone", "confirmed",
                     "answer-yield", "outranked"]) {
    assert.ok(FEED.includes(`"${why}"`), `clearFollowMove site tagged ${why}`);
  }
  // a fresh payload marks itself as the next render's input, before reconcile can re-tag
  const payload = FEED.slice(FEED.indexOf('lastFeedEvent = "payload"'));
  assert.ok(payload.indexOf("reconcileFollowMove(incomingAsks, lastPayloadBuildId, perHostBuildIds)") > 0,
    "payload tag set before reconcile runs");
});
