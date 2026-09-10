// The flicker tripwires (the user 2026-07-31): cards were blinking in and out of the feed and NO
// surface recorded it — the colflip trail only logs column CHANGES, the connection layer logged
// nothing, and the restart doors were anonymous. Three breadcrumb layers now write the same
// client-diag journal (client-diag.jsonl), so the next blink is attributed from the recorded trail:
//   feed itemset   — which itemIds entered/left the MODEL between renders (payload-level);
//   feedmerge      — each host's contribution to the merged feed whenever a count changes;
//   hostconn       — every remote socket open/close/detach.
// Plus the manager logs every /restart request with its source. Source pins (the wiring has no
// jsdom/ws harness here), like the colflip tripwire's own pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const FEED = fs.readFileSync(path.join(UI, "feed.ts"), "utf8");
const FED = fs.readFileSync(path.join(UI, "federation.ts"), "utf8");
const MGR = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-manager"), "utf8");

test("feed: an item entering/leaving the model posts an itemset breadcrumb (ids only, attributed)", () => {
  assert.match(FEED, /if \(prev === undefined\) appeared\.push\(a\.itemId\);/);
  assert.match(FEED, /if \(!seen\.has\(id\)\) \{ gone\.push\(id\); shownCol\.delete\(id\); \}/);
  assert.match(FEED, /what: "itemset",\s*\n\s*data: \{ appeared, gone, total: list\.length, ev: lastFeedEvent, buildId: lastPayloadBuildId \}/);
  // a fresh pane "appears" everything — that is a reload, not a delta, and must not spam the journal
  assert.match(FEED, /const firstRender = shownCol\.size === 0;/);
  assert.match(FEED, /if \(!firstRender && \(appeared\.length \|\| gone\.length\)\)/);
});

test("federation: every remote socket open/close/detach posts a hostconn breadcrumb", () => {
  // the open breadcrumb also names any queued settings it flushed, and the detach any it discarded
  // (federation-send-queue.test.ts owns that queue's behavior) — the events themselves stay pinned here
  assert.match(FED, /this\.diag\("hostconn", flushed\.length \? \{ host: conn\.host, ev: "open", flushed \}\s*\n\s*: \{ host: conn\.host, ev: "open" \}\);/);
  // the open handler is a block body shared with the T215 relay-up dispatch (pending-attach.test.ts
  // owns that event's wiring); the breadcrumb stays inside it, AFTER the flush and BEFORE the dispatch
  // — a re-shipped upload rides that event and must not overtake a queued setting on this socket
  const onopen = FED.slice(FED.indexOf("ws.onopen = () => {"), FED.indexOf("ws.onmessage"));
  assert.ok(onopen.length > 0, "ws.onopen block located");
  const flushAt = onopen.indexOf("this.flushPending(conn)");
  const crumbAt = onopen.indexOf('this.diag("hostconn"');
  const relayAt = onopen.indexOf('"romp:hostRelayUp"');
  assert.ok(flushAt >= 0 && crumbAt > flushAt && relayAt > crumbAt, "onopen order: flush, breadcrumb, relay-up dispatch");
  assert.match(FED, /this\.diag\("hostconn", \{ host: conn\.host, ev: "close", code: ev\.code, clean: ev\.wasClean, detached: conn\.closed \}\);/);
  // …by TYPE (pendingTypes): a held bookkeeping entry's queue key carries the sid it is per (2026-09-10)
  assert.match(FED, /this\.diag\("hostconn", c\.pending\.size \? \{ host, ev: "detach", pendingDropped: pendingTypes\(c\) \}\s*\n\s*: \{ host, ev: "detach" \}\);/);
  // breadcrumbs ride the LOCAL kernel socket into the same client-diag journal the feed writes
  assert.match(FED, /s\(\{ type: "clientDiag", surface: "federation", what, data \}\);/);
});

test("federation: a host's merged-feed contribution changing size posts a feedmerge breadcrumb", () => {
  assert.match(FED, /counts\[h \|\| "local"\] = Array\.isArray\(f\.asks\) \? f\.asks\.length : -1;/);
  // deduped on the count signature — steady-state pushes with unchanged counts log nothing
  assert.match(FED, /if \(sig !== this\.lastFeedCounts\) \{\s*\n\s*this\.lastFeedCounts = sig;\s*\n\s*this\.diag\("feedmerge", \{ counts \}\);/);
});

test("manager: every /restart-all and /restart request is logged with its source (no anonymous SIGTERMs)", () => {
  assert.match(MGR, /log\(`restart-all requested \(\$\{url\.searchParams\.get\('when'\) === 'quiet' \? 'deferred to quiet window' : 'IMMEDIATE'\}\) from \$\{req\.socket\.remoteAddress \|\| '\?'\}`\);/);
  assert.match(MGR, /log\(`restart '\$\{kid\}' requested \(\$\{url\.searchParams\.get\('when'\) === 'quiet' \? 'deferred to quiet window' : 'IMMEDIATE'\}\) from \$\{req\.socket\.remoteAddress \|\| '\?'\}`\);/);
});
