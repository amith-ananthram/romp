// The subagent viewer's wait and retry (T355), executed, and the federated route of its ask and its frame.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { SUBAGENT_OPEN_WAIT_MS, subagentStallText, subagentStalled } from "./subagent-wait";
import { routeOutbound, prefixInbound } from "./federation";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const U = "11111111-2222-3333-4444-555555555555";

test("a viewer stalls only past the wait and only while no frame landed; the text names the bound and the causes", () => {
  assert.equal(SUBAGENT_OPEN_WAIT_MS, 15000, "the relay handshake's own bound (REMOTE_CONNECT_MS)");
  assert.equal(subagentStalled(false, 14999), false);
  assert.equal(subagentStalled(false, 15000), true);
  assert.equal(subagentStalled(true, 60000), false, "a frame landed: never a stall");
  assert.match(subagentStallText(), /15 seconds/); assert.match(subagentStallText(), /restarted/); assert.match(subagentStallText(), /host dropped/);
});

test("render.ts asks through askSubagent with the wait armed, re-asks every open viewer when the socket comes back, and paints the stall with a retry", () => {
  const open = RENDER.slice(RENDER.indexOf("function openSubagentView("), RENDER.indexOf("function askSubagent(id: string): void {"));
  assert.ok(open.includes('vscodeApi?.postMessage({ type: "openSubagent", id: parentId, agentId });\n    armSubagentWait(id);'), "the first open asks and arms the wait");
  const ask = RENDER.slice(RENDER.indexOf("function askSubagent(id: string): void {"), RENDER.indexOf("function closeSubagentView("));
  assert.ok(ask.includes('vscodeApi?.postMessage({ type: "openSubagent", id: s.sub.parentId, agentId: s.sub.agentId });\n  armSubagentWait(id);'), "a retry or a reconnect asks and arms the same wait");
  assert.ok(ask.includes("}, SUBAGENT_OPEN_WAIT_MS);"), "one timer per ask, the module's bound");
  assert.ok(ask.includes("if (!subagentStalled(cur.sub.loaded, Date.now() - asked)) return;"), "the rule decides");
  assert.ok(ask.includes("cur.sub.askedAt !== asked) return;"), "a later ask or a close disarms the earlier timer");
  assert.ok(RENDER.includes('window.addEventListener("romp:wsup", () => reaskWaitingSubagents(""));'), "a reconnect re-asks the local kernel's waiting viewers");
  assert.ok(RENDER.includes("reaskWaitingSubagents(h);") && RENDER.includes('(host === undefined || hostOf(s.sub.parentId) === host)'), "a relay's reopen re-asks that host's viewers alone");
  assert.ok(RENDER.includes("s.sub.loaded = true; s.sub.stalled = false;"), "a frame clears the stall: an answered viewer is never re-asked");
  assert.ok(RENDER.includes("onRetry: () => askSubagent(id),"), "the stall's Retry asks through the same ask (pane-placeholder.test.ts drives the DOM)");
});

test("the ask and its frame cross the federation by the session id's host: the ask bared for the owning kernel, the frame prefixed for the page", () => {
  const ask = routeOutbound({ type: "openSubagent", id: "gpu1:" + U, agentId: "a1111111111111111" }, new Set(["gpu1"]));
  assert.deepEqual(ask, [{ host: "gpu1", msg: { type: "openSubagent", id: U, agentId: "a1111111111111111" } }]);
  const close = routeOutbound({ type: "closeSubagent", id: "gpu1:" + U, agentId: "a1111111111111111" }, new Set(["gpu1"]));
  assert.equal(close[0].host, "gpu1"); assert.equal(close[0].msg.id, U);
  const frame = prefixInbound("gpu1", { type: "subagent", id: U, agentId: "a1111111111111111", events: [], meta: { agentType: "workflow" } });
  assert.equal(frame.id, "gpu1:" + U, "the frame lands on the viewer keyed by the prefixed parent id");
  assert.equal(frame.agentId, "a1111111111111111", "the agent id is not a session id: untouched");
  const err = prefixInbound("gpu1", { type: "subagent", id: U, agentId: "a1111111111111111", error: "missing" });
  assert.equal(err.id, "gpu1:" + U); assert.equal(err.error, "missing");
});
