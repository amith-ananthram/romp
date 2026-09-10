// T289: a follow-up in a REMOTE session's comment thread, and a comment create on a remote session, reach
// the OWNING kernel by the parent's host-prefixed sid — the thread id and the name ride untouched, the sid
// arrives bare. Pinned so the routing that was suspected (and found sound) stays sound.
import { test } from "node:test";
import assert from "node:assert/strict";
import { routeOutbound } from "./federation";

const SID = "aaaaaaaa-1111-2222-3333-444444444444";
const TID = "bbbbbbbb-1111-2222-3333-444444444444";

test("commentReply for a remote thread routes to the parent's host by sid; tid and text untouched", () => {
  const routes = routeOutbound({ type: "commentReply", id: "TESTHOST:" + SID, tid: TID, text: "and the cap?" });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "TESTHOST");
  assert.deepEqual(routes[0].msg, { type: "commentReply", id: SID, tid: TID, text: "and the cap?" });
});

test("commentCreate for a remote session routes by sid and carries the name exactly as given", () => {
  const routes = routeOutbound({ type: "commentCreate", id: "TESTHOST:" + SID, uuid: "u1", exact: "backoff",
                                 text: "why jitter?", name: "", model: "", effort: "", fast: "", color: "",
                                 createId: "k1x2y3z4" });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "TESTHOST");
  assert.equal(routes[0].msg.id, SID);
  assert.equal(routes[0].msg.name, "", "an empty name stays empty: the owning kernel picks its default");
  assert.equal(routes[0].msg.uuid, "u1");
  assert.equal(routes[0].msg.createId, "k1x2y3z4", "the gesture's id reaches the owning kernel, which keys its repeat memo on it");
});

test("a local thread's reply stays local with a bare sid", () => {
  const routes = routeOutbound({ type: "commentReply", id: SID, tid: TID, text: "x" });
  assert.equal(routes.length, 1);
  assert.equal(routes[0].host, "");
  assert.equal(routes[0].msg.id, SID);
});
