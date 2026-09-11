// T246 (the user 2026-09-07): after an ATTACHED kernel restarts, the chat pane stopped receiving live events
// for that host's ACTIVE session until the user's next send.
//
// Why: a kernel builds and flushes the tab a client is LOOKING AT (its per-client `active`, set by the
// `?active=` connect hint or an `activeTab` message) first, and serves every tab from its cached build while
// its complete signature (_chat_build_sig: the backend's live tail, its queue, the snapshot row, every side
// file) holds. The pane shim's local socket carries `?active=`
// on every dial, so a LOCAL kernel restart re-arms it. The federation relay (federation.ts connect) carries
// no such hint, and nothing re-sent `activeTab` on the relay's reopen, so the restarted remote kernel
// minted a client with no active tab and served the watched session as a background one. Under the key of
// the time, which folded no in-memory input for a background tab, its reply streamed nowhere until the
// user's next send moved a file-stat input; today the missing hint costs the watched tab the head of the
// build order and the first flush. Reproduced on two real hermetic
// kernels (one attached to the other through the relay, a browser on the first): after the remote's restart
// the in-memory probe never arrived in 12 s while that kernel logged the session as active=0 on every cycle;
// a transcript append still flowed; a tab click flipped it back to active=1. The fix re-announces the active
// tab on romp:hostRelayUp — the relay's own open event, the exact moment the fresh kernel-side client
// exists — when, and only when, that host owns the tab; the local socket's open (romp:wsup) re-announces a
// local tab the same way, because the shim's persisted hint can lag a dismissal or an adoption (review fold).
// Executed helper + routing contract + render.ts wiring pins. Synthetic ids only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { activeTabToReannounce } from "./relay-active";
import { routeOutbound } from "./federation";

const SID = "11111111-2222-4333-8444-000000000246";
// the sources are read from the tree, not the bundled test's own dir (node --test runs from vscode-extension/)
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

test("the active tab is re-announced on a socket's open only when THAT kernel owns it", () => {
  assert.equal(activeTabToReannounce("TESTHOST:" + SID, "TESTHOST"), true, "the watched tab is that host's: its relay reopened");
  assert.equal(activeTabToReannounce("TESTHOST:" + SID, "hostB"), false, "another host's relay reopened — its kernel never had this tab");
  assert.equal(activeTabToReannounce("TESTHOST:" + SID, ""), false, "the LOCAL socket reopened while a remote tab is active — the local kernel does not know it");
  assert.equal(activeTabToReannounce(SID, ""), true, "a local tab on the local socket's open (the shim's persisted hint can lag)");
  assert.equal(activeTabToReannounce(SID, "TESTHOST"), false, "a local tab is never announced at a remote host");
  assert.equal(activeTabToReannounce(null, "TESTHOST"), false, "no tab open");
  assert.equal(activeTabToReannounce(null, ""), false, "no tab open, local socket");
});

test("the re-announced activeTab reaches the owning host first, its id bared (the contract notifyActive relies on), and the local record", () => {
  // notifyActive posts {type:"activeTab", id: activeId} with the HOST-PREFIXED id; federation's outbound
  // router must land it on that host's relay with the bare id the remote kernel knows, and a local id local.
  // Since T347 the LOCAL kernel hears a remote session's report too, prefixed, for its feed's focused-session
  // section (active-tab-local-route.test.ts); the owning host's route is unchanged and comes first.
  assert.deepEqual(routeOutbound({ type: "activeTab", id: "TESTHOST:" + SID }),
                   [{ host: "TESTHOST", msg: { type: "activeTab", id: SID } },
                    { host: "", msg: { type: "activeTab", id: "TESTHOST:" + SID } }]);
  assert.deepEqual(routeOutbound({ type: "activeTab", id: SID }), [{ host: "", msg: { type: "activeTab", id: SID } }]);
});

test("render.ts re-sends activeTab on romp:hostRelayUp for that host's active tab, beside the upload re-ship", () => {
  assert.match(RENDER, /import \{ activeTabToReannounce \} from "\.\/relay-active";/);
  // the same listener the T215 re-ship uses (pending-attach.test.ts pins its header) gains the re-arm; the
  // decision is the pure helper's, so a host mismatch never posts a stray activeTab at a kernel that
  // does not know the session
  const m = RENDER.match(/window\.addEventListener\("romp:hostRelayUp", \(e\) => \{([\s\S]*?)\n\}\);/);
  assert.ok(m, "the romp:hostRelayUp listener exists");
  assert.match(m![1], /reshipPendingUploads\(\[h\]\)/, "the upload re-ship is still there");
  assert.match(m![1], /if \(activeTabToReannounce\(activeId, h\)\) notifyActive\(\);/, "the active tab is re-announced through the one activeTab sender (notifyActive → routeOutbound strips the host prefix)");
});

test("render.ts re-sends a LOCAL active tab on romp:wsup, the shim's reconnect event, beside the local re-ship", () => {
  // anchored on the re-ship call: render.ts has other romp:wsup listeners (preview heals, awaitingFull), this is the one
  const m = RENDER.match(/window\.addEventListener\("romp:wsup", \(\) => \{\n  reshipPendingUploads\(\);([\s\S]*?)\n\}\);/);
  assert.ok(m, "the romp:wsup re-ship listener, now with a body");
  assert.match(m![1], /if \(activeTabToReannounce\(activeId, ""\)\) notifyActive\(\);/, "the local tab is re-announced with the LOCAL host marker");
});

test("the relay's open dispatches romp:hostRelayUp AFTER flushing queued settings, so a queued setting still precedes the re-announced activeTab", () => {
  const onopen = FED.match(/ws\.onopen = \(\) => \{([\s\S]*?)\n    \};/);
  assert.ok(onopen, "the relay socket's onopen");
  const flush = onopen![1].indexOf("this.flushPending(conn)");
  const up = onopen![1].indexOf('"romp:hostRelayUp"');
  assert.ok(flush >= 0, "the flush is in onopen");
  assert.ok(up >= 0, "the relay-up dispatch is in onopen");
  assert.ok(flush < up, "flush first, then the event the re-arm rides");
});
