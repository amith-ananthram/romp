// Outbound kernel settings must survive socket churn (the user 2026-08-28, whose file-viewer Edit
// consent vanished into a restarting kernel): during a kernel restart every federated socket churns
// for a few seconds, and outbound() used to DROP any message routed to a host whose socket wasn't
// OPEN — a warn toast, nothing in the client-diag journal, and no second chance. For the gear's
// KERNEL_SETTING broadcasts that is a real loss: the Edit consent was OK'd mid-churn, the
// setFileEditing broadcast rode a closed socket into the void, and the save was later refused by a
// kernel that never heard the yes — with refusal copy pointing back at a popup already answered.
//
// The fix, two-sided:
// - KERNEL_SETTING messages queue per connection while the socket is down — LATEST per type only
//   (settings are latest-wins by nature; a reconnect must never replay a stale older value over the
//   one chosen last) — and flush on the socket's OPEN event, before any post-reconnect traffic.
//   That keeps the file viewer's "the consent lands before the save" ordering (file-view.ts posts
//   setFileEditing, then enters edit mode) intact across a down-socket window.
// - Non-setting messages keep their delivery behavior (replaying an arbitrary action minutes later
//   can be worse than dropping it — a deliberate non-goal) but the drop leaves a client-diag
//   breadcrumb naming the message type and target host, next to the warn toast: never silent.
//
// End-to-end through the real FederationManager (the withManager precedent in
// multi-kernel-merge.test.ts): stub window/location/WebSocket, dial real conns, drive the sockets.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { FederationManager, REMOTE_STALE_MS, REMOTE_REDIAL_MS } from "./federation";

const U = "11111111-2222-3333-4444-555555555555";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0; // CONNECTING, like a real just-dialed WebSocket
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: unknown) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(s: string): void {
    // the real browser refuses a send on a non-open socket — a fake that accepted one would hide
    // exactly the misdelivery this suite exists to catch
    if (this.readyState !== 1) throw new Error("send() on a socket that is not OPEN");
    this.sent.push(s);
  }
  close(): void { this.readyState = 3; }
  open(): void { this.readyState = 1; if (this.onopen) this.onopen(); }
  types(): string[] { return this.sent.map((s) => JSON.parse(s).type); }
}

function withFed(fn: (fm: any, winEvents: any[], localSends: any[]) => void): void {
  const g: any = globalThis;
  const keys = ["window", "location", "WebSocket"] as const;
  const had: Record<string, boolean> = {}, prev: Record<string, any> = {};
  for (const k of keys) { had[k] = k in g; prev[k] = g[k]; }
  const winEvents: any[] = [];
  const localSends: any[] = [];
  FakeSocket.instances = [];
  g.window = {
    dispatchEvent: (ev: any) => { if (ev && ev.data) winEvents.push(ev.data); return true; },
    __rompLocalSend: (m: any) => localSends.push(m),
  };
  g.location = { protocol: "http:", host: "127.0.0.1:0", search: "" };
  g.WebSocket = FakeSocket;
  try {
    fn(new FederationManager() as any, winEvents, localSends);
  } finally {
    for (const k of keys) { if (had[k]) g[k] = prev[k]; else delete g[k]; }
  }
}

/** Dial a remote host through the real openRemote/connect path; returns its (CONNECTING) socket. */
function attach(fm: any, host: string): FakeSocket {
  fm.openRemote(host, true);
  return fm.conns.get(host).ws as FakeSocket;
}

/** The poll's re-dial (federation.ts's `if (c.live && …) this.connect(c)`), minus the fetch. */
function redial(fm: any, host: string): FakeSocket {
  fm.connect(fm.conns.get(host));
  return fm.conns.get(host).ws as FakeSocket;
}

const diags = (localSends: any[], what: string) =>
  localSends.filter((m) => m && m.type === "clientDiag" && m.surface === "federation" && m.what === what);

test("a kernel setting sent while the socket is still CONNECTING is delivered on the open event", () => {
  withFed((fm, _win, localSends) => {
    const sock = attach(fm, "TESTHOSTA");
    assert.equal(sock.readyState, 0, "just dialed — not open yet");
    fm.outbound({ type: "setFileEditing", enabled: true });
    // the LOCAL kernel's copy goes out at once (the pane shim's own queue covers the local socket)
    assert.deepEqual(localSends.filter((m) => m.type === "setFileEditing"),
      [{ type: "setFileEditing", enabled: true }]);
    assert.deepEqual(sock.types(), [], "nothing can ride a CONNECTING socket");
    sock.open();
    assert.deepEqual(sock.types(), ["setFileEditing"], "the open event itself delivers the queued setting");
    assert.deepEqual(JSON.parse(sock.sent[0]), { type: "setFileEditing", enabled: true });
  });
});

test("a kernel setting sent while the socket is CLOSED rides the next reconnect (the re-dial path)", () => {
  withFed((fm) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    s1.readyState = 3; // the relay dropped (kernel restart) — the state the /tunnels poll re-dials on
    fm.outbound({ type: "setAutoNudge", enabled: false });
    assert.deepEqual(s1.sent.length, 0, "the dead socket carried nothing");
    const s2 = redial(fm, "TESTHOSTA");
    assert.notEqual(s2, s1, "the re-dial made a fresh socket");
    s2.open();
    assert.deepEqual(s2.types(), ["setAutoNudge"], "the queued setting flushes on the fresh socket's open");
  });
});

test("latest-wins: two sends of one setting type while down deliver only the newest value", () => {
  withFed((fm) => {
    const sock = attach(fm, "TESTHOSTA");
    fm.outbound({ type: "setFileEditing", enabled: true });
    fm.outbound({ type: "setJudgeModel", model: "m1" });
    fm.outbound({ type: "setFileEditing", enabled: false }); // the user changed their mind while down
    sock.open();
    const msgs = sock.sent.map((s) => JSON.parse(s));
    assert.deepEqual(msgs.map((m: any) => m.type).sort(), ["setFileEditing", "setJudgeModel"],
      "one message per setting type, distinct types both flush");
    const fe = msgs.filter((m: any) => m.type === "setFileEditing");
    assert.equal(fe.length, 1, "the stale older value is never replayed");
    assert.equal(fe[0].enabled, false, "the newest value wins");
  });
});

test("a setting queued for one host never leaks to another, and a host that heard it live hears it once", () => {
  withFed((fm) => {
    const a = attach(fm, "TESTHOSTA"); // still CONNECTING — down at send time
    const b = attach(fm, "TESTHOSTB");
    b.open();
    fm.outbound({ type: "setDistillModel", model: "m2" });
    assert.deepEqual(b.types(), ["setDistillModel"], "the reachable host hears the broadcast live");
    assert.deepEqual(a.types(), [], "the down host holds it queued");
    a.open();
    assert.deepEqual(a.types(), ["setDistillModel"], "the queued copy reaches its own host on open");
    assert.deepEqual(b.types(), ["setDistillModel"], "…and the other host is not re-sent what it already heard");
  });
});

test("a NON-setting drop stays a drop — but leaves a client-diag breadcrumb and the warn toast", () => {
  withFed((fm, winEvents, localSends) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    s1.readyState = 3;
    fm.outbound({ type: "openSession", id: "TESTHOSTA:" + U });
    const dg = diags(localSends, "senddrop");
    assert.equal(dg.length, 1, "the drop is journaled, never silent");
    assert.equal(dg[0].data.host, "TESTHOSTA");
    assert.equal(dg[0].data.msgType, "openSession");
    assert.ok(winEvents.some((m) => m.type === "warn" && /TESTHOSTA/.test(m.text || "")),
      "the user-visible warn toast stays");
    const s2 = redial(fm, "TESTHOSTA");
    s2.open();
    assert.deepEqual(s2.types(), [],
      "a dropped action is never replayed later — a stale action can be worse than a dropped one");
  });
});

test("the undoClear send site drops loudly too: diag + warn on a down socket", () => {
  withFed((fm, winEvents, localSends) => {
    attach(fm, "TESTHOSTA"); // still CONNECTING
    fm.lastClearHosts = ["TESTHOSTA"];   // T286: the hosts the last clear reached (one here)
    fm.outbound({ type: "undoClear" });
    const dg = diags(localSends, "senddrop");
    assert.equal(dg.length, 1);
    assert.equal(dg[0].data.msgType, "undoClear");
    assert.ok(winEvents.some((m) => m.type === "warn"), "the toast fires for this site as well");
  });
});

test("flush ordering: a queued setting lands before any message sent after the reconnect", () => {
  withFed((fm) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    s1.readyState = 3;
    fm.outbound({ type: "setFileEditing", enabled: true }); // the consent click, mid-churn
    const s2 = redial(fm, "TESTHOSTA");
    s2.open();
    assert.deepEqual(s2.types(), ["setFileEditing"],
      "the flush is synchronous with the open event — no timer, no later tick");
    // the very next post-reconnect send (e.g. the file viewer retrying its save) must find the
    // flag already landed — the same-ordered-socket guarantee, now across the down window
    fm.outbound({ type: "fileSave", sid: "TESTHOSTA:" + U, text: "x" });
    assert.deepEqual(s2.types(), ["setFileEditing", "fileSave"]);
  });
});

// ── gesture stamps (2026-08-29): the queue's "latest per type" is latest per TAB only. A tab
// frozen for hours (iOS always; Chrome tab-freeze) re-dials and flushes a pick the user has since
// superseded from another device, and the kernel used to apply whatever arrived — so every
// KERNEL_SETTING message now carries `gt`, epoch ms minted ONCE at the user's gesture, and the
// kernel stands a stale stamp down at the store. That only works if this queue delivers the
// ORIGINAL stamp: a re-stamp at send/flush time would forge freshness onto an hours-old pick.

test("a queued setting's gesture stamp survives the queue and the flush unchanged", () => {
  withFed((fm) => {
    const sock = attach(fm, "TESTHOSTA");
    fm.outbound({ type: "setJudgeModel", model: "m1", gt: 1111 });
    sock.open();
    assert.deepEqual(JSON.parse(sock.sent[0]), { type: "setJudgeModel", model: "m1", gt: 1111 },
      "the flush delivers the message byte-identical — gt is the kernel's ordering key");
  });
});

test("latest-wins keeps the newest gesture's OWN stamp — never a blend of two messages", () => {
  withFed((fm) => {
    const sock = attach(fm, "TESTHOSTA");
    fm.outbound({ type: "setJudgeModel", model: "m1", gt: 1000 });
    fm.outbound({ type: "setJudgeModel", model: "m2", gt: 2000 });
    sock.open();
    assert.deepEqual(sock.sent.map((s) => JSON.parse(s)),
      [{ type: "setJudgeModel", model: "m2", gt: 2000 }]);
  });
});

// ── composing with the relay sockets' liveness watchdog (2026-09-02): a socket OPEN but silent past
// the keepalive bound is ABANDONED — handlers detached, closed for hygiene — and a fresh one dialed in
// the same tick, so the redial itself opens exactly the window this queue exists for (the fresh socket
// is CONNECTING). The queue lives on the Conn, not the socket: nothing sent in that window is lost, and
// the flush rides the fresh socket's open like any other reconnect's.

test("a setting sent after the watchdog abandons a quiet socket queues on the conn and flushes on the fresh socket's open", () => {
  withFed((fm, _win, localSends) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    fm.watchdog(Date.now() + REMOTE_STALE_MS + 1);   // silent past the bound since its open → abandoned, redialed now
    const s2 = FakeSocket.instances[1];
    assert.ok(s2 && s2 !== s1, "the watchdog dialed a fresh socket in the same pass");
    assert.equal(s2.readyState, 0, "…still CONNECTING: the window a send used to drop in");
    assert.equal(fm.conns.get("TESTHOSTA").ws, s2, "the conn owns the fresh socket");
    fm.outbound({ type: "setFileEditing", enabled: true, gt: 1234 });
    assert.deepEqual(s1.sent, [], "nothing rides the abandoned socket");
    assert.deepEqual(s2.sent, [], "nothing can ride a CONNECTING socket");
    assert.equal(diags(localSends, "senddrop").length, 0, "a setting is queued, never dropped");
    s2.open();
    assert.deepEqual(s2.sent.map((s) => JSON.parse(s)), [{ type: "setFileEditing", enabled: true, gt: 1234 }],
      "the fresh socket's open delivers it, stamp intact");
    const open = diags(localSends, "hostconn").filter((d) => d.data.ev === "open").pop();
    assert.deepEqual(open.data, { host: "TESTHOSTA", ev: "open", flushed: ["setFileEditing"] },
      "the open breadcrumb names what the redial carried");
  });
});

test("a setting queued on a CLOSED socket survives the watchdog's lost-timer redial and flushes on the fresh open", () => {
  withFed((fm) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    s1.readyState = 3;                                // closed under us with no onclose — no 2s retry armed
    fm.outbound({ type: "setAutoNudge", enabled: false, gt: 5 });
    assert.deepEqual(s1.sent, [], "the dead socket carried nothing");
    fm.watchdog(Date.now() + REMOTE_REDIAL_MS + 1);  // the watchdog's own redial, not the onclose path
    const s2 = FakeSocket.instances[1];
    assert.ok(s2 && s2 !== s1, "redialed by the watchdog");
    s2.open();
    assert.deepEqual(s2.sent.map((s) => JSON.parse(s)), [{ type: "setAutoNudge", enabled: false, gt: 5 }]);
  });
});

// ── the queue's edges (the #879 review's smaller notes): queueing is journaled, and a flush that
// throws part-way clears only what it delivered.

test("a setting queued for a down host leaves a sendqueue breadcrumb: host, type, stamp, socket state", () => {
  withFed((fm, _win, localSends) => {
    const sock = attach(fm, "TESTHOSTA");           // CONNECTING: the window a send used to drop in
    fm.outbound({ type: "setJudgeModel", model: "m1", gt: 1000 });
    const q = diags(localSends, "sendqueue");
    assert.equal(q.length, 1, "one line per queued gesture per down host");
    assert.deepEqual(q[0].data, { host: "TESTHOSTA", msgType: "setJudgeModel", gt: 1000, rs: 0 });
    assert.equal(diags(localSends, "senddrop").length, 0, "queued, not dropped");
    // the user picks again while the host is still down: the replaced pick is named
    fm.outbound({ type: "setJudgeModel", model: "m2", gt: 2000 });
    const q2 = diags(localSends, "sendqueue");
    assert.equal(q2.length, 2);
    assert.deepEqual(q2[1].data, { host: "TESTHOSTA", msgType: "setJudgeModel", gt: 2000, rs: 0, superseded: 1000 });
    sock.open();
    assert.deepEqual(sock.sent.map((s) => JSON.parse(s)), [{ type: "setJudgeModel", model: "m2", gt: 2000 }],
      "the flush itself is unchanged: latest per type, stamp intact");
  });
});

test("a live send is not a queue event: no sendqueue breadcrumb on an OPEN socket", () => {
  withFed((fm, _win, localSends) => {
    const sock = attach(fm, "TESTHOSTA");
    sock.open();
    fm.outbound({ type: "setJudgeModel", model: "m1", gt: 1000 });
    assert.deepEqual(sock.types(), ["setJudgeModel"], "delivered live");
    assert.equal(diags(localSends, "sendqueue").length, 0);
  });
});

test("a CLOSED socket queues with rs 3; an unstamped message records gt 0 and supersedes as `true`", () => {
  withFed((fm, _win, localSends) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    s1.readyState = 3;                                // the relay dropped under us
    fm.outbound({ type: "setAutoNudge", enabled: false });   // an older emitter: no stamp
    fm.outbound({ type: "setAutoNudge", enabled: true, gt: 5 });
    const q = diags(localSends, "sendqueue").map((d) => d.data);
    assert.deepEqual(q, [{ host: "TESTHOSTA", msgType: "setAutoNudge", gt: 0, rs: 3 },
                         { host: "TESTHOSTA", msgType: "setAutoNudge", gt: 5, rs: 3, superseded: true }]);
  });
});

/** A socket whose Nth send throws — the mid-flush failure the per-entry clear exists for. */
class FlakySocket extends FakeSocket {
  static failAt = 2;
  n = 0;
  send(s: string): void {
    if (++this.n === FlakySocket.failAt) throw new Error("send failed mid-flush");
    super.send(s);
  }
}

test("a send that throws mid-flush clears only the delivered entries: the held one flushes on the redial, nothing replays", () => {
  withFed((fm, _win, localSends) => {
    (globalThis as any).WebSocket = FlakySocket;      // withFed restores the global in its finally
    const sock = attach(fm, "TESTHOSTA") as FlakySocket;
    fm.outbound({ type: "setFileEditing", enabled: true, gt: 10 });
    fm.outbound({ type: "setJudgeModel", model: "m1", gt: 20 });
    sock.open();                                      // the first send lands, the second throws
    assert.deepEqual(sock.types(), ["setFileEditing"]);
    assert.deepEqual([...fm.conns.get("TESTHOSTA").pending.keys()], ["setJudgeModel"],
      "the delivered entry is gone from the queue; the undelivered one is held");
    const halt = diags(localSends, "hostconn").filter((d) => d.data.ev === "flush-halt");
    assert.equal(halt.length, 1, "a partial flush is never silent");
    assert.deepEqual(halt[0].data, { host: "TESTHOSTA", ev: "flush-halt", flushed: ["setFileEditing"], held: ["setJudgeModel"] });
    const open = diags(localSends, "hostconn").filter((d) => d.data.ev === "open").pop();
    assert.deepEqual(open.data, { host: "TESTHOSTA", ev: "open", flushed: ["setFileEditing"] },
      "the open breadcrumb names what actually went, and the open handler ran on past the halt");
    // the redial: only the held entry rides the fresh socket
    (globalThis as any).WebSocket = FakeSocket;
    sock.readyState = 3;
    const s2 = redial(fm, "TESTHOSTA");
    s2.open();
    assert.deepEqual(s2.sent.map((s) => JSON.parse(s)), [{ type: "setJudgeModel", model: "m1", gt: 20 }],
      "the delivered entry is not replayed — a replay is one gesture delivered twice");
    assert.equal(fm.conns.get("TESTHOSTA").pending.size, 0);
  });
});

// Source pins, in the federation-remote-gate.test.ts style: the properties above hold only while
// every remote send routes through the ONE queue-aware helper, and the flush hangs on the open
// event itself — a raw `ws.send` site or a timer-based flush would reopen the hole quietly.
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");

test("the queue and the flush never mint a timestamp — gt is stamped at the gesture, upstream of here", () => {
  const seg = FED.slice(FED.indexOf("private sendRemote"), FED.indexOf("private dropWarn"));
  assert.ok(seg.length > 0, "sendRemote + flushPending located");
  assert.doesNotMatch(seg, /Date\.now\(/,
    "a queued message's gt must be the ORIGINAL gesture's time — no re-stamp in the send path");
});

test("both outbound remote send sites route through sendRemote — no raw inline drop path remains", () => {
  const outb = FED.slice(FED.indexOf("outbound(m: any): void {"), FED.indexOf("private dropWarn"));
  // T286: both sites go through the one sendTo helper, whose remote arm is sendRemote
  assert.match(outb, /for \(const h of hosts\) this\.sendTo\(h, m\);/);
  assert.match(outb, /for \(const r of routes\) this\.sendTo\(r\.host, r\.msg\);/);
  assert.match(outb, /private sendTo\(host: string, msg: any\): void \{[\s\S]*?this\.sendRemote\(host, msg\);/);
  assert.doesNotMatch(outb, /readyState === 1\) c\.ws\.send/,
    "the old drop-if-not-open inline sends must not come back");
});

test("the pending-settings flush rides the socket's open event, never a timer", () => {
  const connectFn = FED.slice(FED.indexOf("private connect(conn: Conn)"));
  const onopen = connectFn.slice(connectFn.indexOf("ws.onopen"), connectFn.indexOf("ws.onmessage"));
  assert.match(onopen, /flushPending/, "onopen is the flush hook");
  const flushFn = FED.slice(FED.indexOf("private flushPending"), FED.indexOf("private connect(conn: Conn)"));
  assert.ok(flushFn.length > 0, "flushPending exists");
  assert.doesNotMatch(flushFn, /setTimeout|setInterval/, "no timer anywhere in the flush");
});

// ── the pane's OWN bookkeeping (2026-09-10): a toast about a message the user never sent. dropWarn was added
// so a GESTURE at an unreachable remote (creating a session there) gives feedback — but it fired for anything
// routed to a host whose socket was not open, and the chat pane sends plenty on its own: on the phone, a fresh
// page whose active tab was a remote session posted `activeTab` before the relay socket to that host had
// opened, and the tap the user had just made (which had worked) was answered with a warning that "activeTab"
// was not delivered. Bookkeeping now HOLDS on the conn like a setting (latest per key, flushed on the open,
// one hostconn row) and never toasts; a gesture, and any UNKNOWN type, still toasts — a silent drop of
// something that mattered is the worse failure. The classes are an explicit list (BOOKKEEPING), not a guess
// from the type's name.
import { BOOKKEEPING, bookkeepingKey, routeOutbound } from "./federation";

const V = "66666666-7777-8888-9999-000000000000";

test("activeTab to a host whose socket is not open is HELD, not toasted: no warn, no senddrop, one hold row, flushed on the open", () => {
  withFed((fm, winEvents, localSends) => {
    const sock = attach(fm, "TESTHOSTA");           // CONNECTING: the fresh page's relay, not open yet
    fm.outbound({ type: "activeTab", id: "TESTHOSTA:" + U });
    assert.equal(winEvents.filter((m) => m.type === "warn").length, 0, "no toast about a message the user never sent");
    assert.equal(diags(localSends, "senddrop").length, 0, "held, not dropped");
    assert.equal(diags(localSends, "sendqueue").length, 0, "not a setting's row either");
    const hold = diags(localSends, "hostconn").filter((d) => d.data.ev === "hold");
    assert.deepEqual(hold.map((d) => d.data), [{ host: "TESTHOSTA", ev: "hold", msgType: "activeTab", rs: 0 }],
      "one hostconn row says the hold began, naming the host and the type");
    assert.deepEqual(sock.sent, [], "nothing can ride a CONNECTING socket");
    sock.open();
    assert.deepEqual(sock.sent.map((s) => JSON.parse(s)), [{ type: "activeTab", id: U }],
      "the open event itself delivers it, id bared for that host");
    const open = diags(localSends, "hostconn").filter((d) => d.data.ev === "open").pop();
    assert.deepEqual(open.data, { host: "TESTHOSTA", ev: "open", flushed: ["activeTab"] }, "the open row names what flushed, by type");
  });
});

test("latest-wins: two activeTabs held while down deliver only the newest tab, and the hold row is written once", () => {
  withFed((fm, winEvents, localSends) => {
    const sock = attach(fm, "TESTHOSTA");
    fm.outbound({ type: "activeTab", id: "TESTHOSTA:" + U });
    fm.outbound({ type: "activeTab", id: "TESTHOSTA:" + V });   // the user moved on before the socket opened
    assert.equal(diags(localSends, "hostconn").filter((d) => d.data.ev === "hold").length, 1,
      "the row marks the not-held → held transition, not every message");
    sock.open();
    assert.deepEqual(sock.sent.map((s) => JSON.parse(s)), [{ type: "activeTab", id: V }], "the stale tab is never sent");
    assert.equal(winEvents.filter((m) => m.type === "warn").length, 0);
  });
});

test("a held needFull keys by SESSION: asks for two sessions both flush (a type-only key would leave one pane-side dedupe gagged)", () => {
  withFed((fm) => {
    const sock = attach(fm, "TESTHOSTA");
    fm.outbound({ type: "needFull", id: "TESTHOSTA:" + U, why: "prefetch" });
    fm.outbound({ type: "needFull", id: "TESTHOSTA:" + V, why: "gap" });
    fm.outbound({ type: "needFull", id: "TESTHOSTA:" + U, why: "skeleton-click" });   // the same session again: one ask
    sock.open();
    assert.deepEqual(sock.sent.map((s) => JSON.parse(s)),
      [{ type: "needFull", id: U, why: "skeleton-click" }, { type: "needFull", id: V, why: "gap" }]);
  });
});

test("a pointer echo keys by TYPE: hovers while down hold one entry and flush only the newest (a leave clears)", () => {
  withFed((fm, winEvents) => {
    const sock = attach(fm, "TESTHOSTA");
    fm.outbound({ type: "dotHover", sid: "TESTHOSTA:" + U, uuid: "a1", t: 1 });
    fm.outbound({ type: "dotHover", sid: "TESTHOSTA:" + U, uuid: "a2", t: 2 });
    fm.outbound({ type: "showAskPath", itemId: U + ":g1", sid: "TESTHOSTA:" + U, locate: false });
    fm.outbound({ type: "showAskPath", itemId: U + ":g1", sid: "TESTHOSTA:" + U, off: true });
    assert.equal(winEvents.filter((m) => m.type === "warn").length, 0, "a hover over a down host's card never toasts");
    sock.open();
    assert.deepEqual(sock.sent.map((s) => JSON.parse(s)), [
      { type: "dotHover", sid: U, uuid: "a2", t: 2 },
      { type: "showAskPath", itemId: U + ":g1", sid: U, off: true },
    ]);
  });
});

test("showAskPath's JUMP is the click itself, a gesture: it toasts and is never replayed; its hover glow holds", () => {
  withFed((fm, winEvents, localSends) => {
    const sock = attach(fm, "TESTHOSTA");
    fm.outbound({ type: "showAskPath", itemId: U + ":g1", sid: "TESTHOSTA:" + U, locate: false, jump: true });
    assert.equal(winEvents.filter((m) => m.type === "warn").length, 1, "the jump the user clicked did not land: say so");
    assert.equal(diags(localSends, "senddrop").length, 1);
    sock.open();
    assert.deepEqual(sock.sent, [], "a jump minutes later would land the user somewhere they no longer asked for");
  });
});

test("a gesture to a down host keeps the toast: sendMessage (routed by id) and createSession (explicit host)", () => {
  withFed((fm, winEvents, localSends) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    s1.readyState = 3;
    fm.outbound({ type: "sendMessage", id: "TESTHOSTA:" + U, text: "hello" });
    fm.outbound({ type: "createSession", host: "TESTHOSTA", name: "web", dir: "~/proj" });
    const warns = winEvents.filter((m) => m.type === "warn").map((m) => m.text);
    assert.equal(warns.length, 2);
    assert.match(warns[0], /TESTHOSTA is unreachable .*“sendMessage” was not delivered/);
    assert.match(warns[1], /TESTHOSTA is unreachable .*“createSession” was not delivered/);
    assert.deepEqual(diags(localSends, "senddrop").map((d) => d.data.msgType), ["sendMessage", "createSession"]);
    assert.equal(fm.conns.get("TESTHOSTA").pending.size, 0, "a gesture is never held for a later replay");
  });
});

test("an UNKNOWN type to a down host toasts: the default is the loud arm, never a silent hold", () => {
  withFed((fm, winEvents, localSends) => {
    attach(fm, "TESTHOSTA");
    fm.outbound({ type: "zzzNotAKnownType", id: "TESTHOSTA:" + U });
    assert.equal(winEvents.filter((m) => m.type === "warn").length, 1);
    assert.deepEqual(diags(localSends, "senddrop").map((d) => d.data.msgType), ["zzzNotAKnownType"]);
    assert.equal(fm.conns.get("TESTHOSTA").pending.size, 0);
  });
});

test("bookkeeping for a host this page holds NO connection to is dropped with a breadcrumb — no toast, nothing to hold it on", () => {
  withFed((fm, winEvents, localSends) => {
    fm.hostSeq.push("TESTHOSTB");                    // known from a frame, never dialed (no /tunnels row)
    fm.outbound({ type: "activeTab", id: "TESTHOSTB:" + U });
    assert.equal(winEvents.filter((m) => m.type === "warn").length, 0);
    assert.deepEqual(diags(localSends, "senddrop").map((d) => d.data), [{ host: "TESTHOSTB", msgType: "activeTab", why: "no-conn" }]);
  });
});

test("a held entry rides the re-dial like a setting, and a detach names the held TYPES it discards", () => {
  withFed((fm, _win, localSends) => {
    const s1 = attach(fm, "TESTHOSTA");
    s1.open();
    s1.readyState = 3;
    fm.outbound({ type: "activeTab", id: "TESTHOSTA:" + U });
    fm.outbound({ type: "needFull", id: "TESTHOSTA:" + V, why: "gap" });
    const s2 = redial(fm, "TESTHOSTA");
    s2.open();
    assert.deepEqual(s2.types(), ["activeTab", "needFull"], "held bookkeeping flushes on the fresh socket's open");
    fm.outbound({ type: "activeTab", id: "TESTHOSTA:" + V });   // live now
    s2.readyState = 3;
    fm.outbound({ type: "needFull", id: "TESTHOSTA:" + U, why: "gap" });
    fm.closeRemote("TESTHOSTA");
    const det = diags(localSends, "hostconn").filter((d) => d.data.ev === "detach").pop();
    assert.deepEqual(det.data, { host: "TESTHOSTA", ev: "detach", pendingDropped: ["needFull"] },
      "the detach row lists types, never composite keys (a key carries the sid)");
  });
});

test("redial stays LOCAL with its host INTACT: the local kernel owns the tunnel, and a down host must not eat the ask with a toast", () => {
  withFed((fm, winEvents, localSends) => {
    attach(fm, "TESTHOSTA");                          // the tunnel is down: exactly when the composer asks for a redial
    fm.outbound({ type: "redial", host: "TESTHOSTA" });
    assert.deepEqual(localSends.filter((m) => m.type === "redial"), [{ type: "redial", host: "TESTHOSTA" }]);
    assert.equal(winEvents.filter((m) => m.type === "warn").length, 0, "no toast about the redial on top of the refusal's own");
    assert.equal(fm.conns.get("TESTHOSTA").pending.size, 0);
    assert.deepEqual(routeOutbound({ type: "redial", host: "TESTHOSTA" }), [{ host: "", msg: { type: "redial", host: "TESTHOSTA" } }]);
  });
});

test("the classes are an explicit list: every held type is in BOOKKEEPING, a gesture and an unknown type are not, and a key never leaks a type-only collision across sessions", () => {
  for (const t of ["activeTab", "needFull", "needSlot", "loadOlder", "loadEpisode", "imgRequest", "commentSeen",
                   "dotHover", "hoverHighlight", "showAskPath", "timelineHover", "dirComplete",
                   "cardOpened", "locateDiag", "orderAudit"])
    assert.ok(BOOKKEEPING.has(t), t + " is held, not toasted");
  for (const t of ["sendMessage", "createSession", "openSession", "renameSession", "askClear", "askClearMany", "clearAll",
                   "undoClear", "tagEdit", "editTag", "reviveSession", "interrupt", "stopTask", "cardNotify", "viewReadOnly",
                   "requestSessions", "reply", "zzzNotAKnownType"])
    assert.equal(bookkeepingKey({ type: t, id: U }), null, t + " is a gesture (or unknown): it toasts");
  assert.notEqual(bookkeepingKey({ type: "needFull", id: U }), bookkeepingKey({ type: "needFull", id: V }), "one ask per session");
  assert.equal(bookkeepingKey({ type: "activeTab", id: U }), bookkeepingKey({ type: "activeTab", id: V }), "one active tab per pane");
  assert.equal(bookkeepingKey({ type: "showAskPath", sid: U, jump: true }), null, "the jump click is the gesture");
  assert.equal(bookkeepingKey(null), null);
  assert.equal(bookkeepingKey({ id: U }), null, "no type: the loud arm");
});

test("sendRemote consults the bookkeeping list AFTER the settings queue and BEFORE the drop arm — the drop arm is the default", () => {
  const seg = FED.slice(FED.indexOf("private sendRemote"), FED.indexOf("private flushPending"));
  const iSetting = seg.indexOf("KERNEL_SETTING.has(msg.type)");
  const iBook = seg.indexOf("bookkeepingKey(msg)");
  const iDrop = seg.indexOf("this.dropWarn(host, msg)");
  assert.ok(iSetting > 0 && iBook > iSetting && iDrop > iBook, "settings, then bookkeeping, then the toast");
  assert.doesNotMatch(seg, /startsWith\(|endsWith\(|\/\^|test\(msg\.type/, "the class is looked up, never guessed from the type's spelling");
});
