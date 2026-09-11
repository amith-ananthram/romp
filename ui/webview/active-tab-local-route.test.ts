// The chat pane's activeTab reaches the LOCAL kernel too when the active session is remote (T347): the local
// kernel records each window's active tab for its feed pane's focused-session section, and the merged board
// names a remote session's cards by the host-prefixed id, so the record must carry "host:sid". Before, the
// id-addressed route sent activeTab to the owning host alone and a feed reload named the last LOCAL session.
// EXECUTES routeOutbound (./federation). Synthetic ids only.
import { test } from "node:test";
import assert from "node:assert/strict";
import { routeOutbound, LOCAL } from "./federation";

const SID = "11111111-2222-3333-4444-555555555555";

test("a remote session's activeTab goes to its host with the bare id AND to the local kernel with the prefixed id", () => {
  const routes = routeOutbound({ type: "activeTab", id: "TESTHOST:" + SID }, new Set(["TESTHOST"]));
  assert.deepEqual(routes.map((r) => r.host), ["TESTHOST", LOCAL], "the owning host first (it builds and streams the tab first), then the local record");
  assert.equal(routes[0].msg.id, SID, "the owning kernel gets the id it knows");
  assert.equal(routes[1].msg.id, "TESTHOST:" + SID, "the local kernel records the merged board's spelling");
  assert.equal(routes[1].msg.type, "activeTab");
});

test("a local session's activeTab takes the one local route, id intact, as before", () => {
  const routes = routeOutbound({ type: "activeTab", id: SID }, new Set(["TESTHOST"]));
  assert.deepEqual(routes, [{ host: LOCAL, msg: { type: "activeTab", id: SID } }]);
});

test("no tab (a null id) is one local route too — nothing to strip, nothing to relay elsewhere", () => {
  const routes = routeOutbound({ type: "activeTab", id: null }, new Set(["TESTHOST"]));
  assert.deepEqual(routes.map((r) => r.host), [LOCAL]);
  assert.equal(routes[0].msg.id, null);
});
