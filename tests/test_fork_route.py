#!/usr/bin/env python3
"""POST /fork (the user 2026-08-19, via the lab workflow's delegate): the WS forkSession op as a
one-shot token-gated route beside /new and /send, so a terminal or another machine's plugin can
split a session without a WS client. Contract: parent resolved by LIVE NAME (or raw sid for a
dormant parent), explicit new name refused on collision, "at" rides through as the cut, refusals
loud, and the ack carries the fork's id from the live store (be.fork registers the name
synchronously inside _fork_session).

Drives the REAL Handler over HTTP (the test_new_route_prefs.py pattern). Synthetic only.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT_SID = "11111111-2222-3333-4444-555555555555"
FORK_SID = "66666666-7777-8888-9999-000000000000"


class ForkRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.calls = []
        self.names = {"exp-web": PARENT_SID}      # the live-name store the route consults
        self._saved = (km._tmux_sessions, km._live_names, km._fork_session)
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: dict(self.names)

        def fake_fork(psid, at, nm, now=None, client=None):
            self.calls.append((psid, at, nm))
            self.names[nm] = FORK_SID             # be.fork registers the name synchronously
            return None
        km._fork_session = fake_fork

    def tearDown(self):
        km._tmux_sessions, km._live_names, km._fork_session = self._saved

    def _post(self, body):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/fork" % self.port, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_live_name_forks_and_acks_with_the_new_id(self):
        st, r = self._post({"parent": "exp-web", "name": "exp-web-stage2"})
        self.assertEqual(st, 200)
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(r.get("id"), FORK_SID, "the ack carries the fork's id from the live store")
        self.assertEqual(r.get("name"), "exp-web-stage2")
        self.assertEqual(self.calls, [(PARENT_SID, "", "exp-web-stage2")], "tip fork: empty cut by default")

    def test_at_rides_through_as_the_cut(self):
        self._post({"parent": "exp-web", "name": "exp-web-fig", "at": "aaaabbbb-1111-2222-3333-444455556666"})
        self.assertEqual(self.calls[0][1], "aaaabbbb-1111-2222-3333-444455556666")

    def test_a_sid_parent_reaches_the_fork_directly(self):
        st, r = self._post({"parent": PARENT_SID, "name": "by-sid-fork"})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.calls[0][0], PARENT_SID)

    def test_unknown_parent_name_is_refused_loudly(self):
        st, r = self._post({"parent": "nope", "name": "x2"})
        self.assertEqual(st, 200)
        self.assertFalse(r.get("ok"))
        self.assertIn("no live session named", r.get("error") or "")
        self.assertEqual(self.calls, [], "nothing forked")

    def test_name_collision_is_refused_never_overloaded(self):
        st, r = self._post({"parent": "exp-web", "name": "exp-web"})
        self.assertFalse(r.get("ok"))
        self.assertIn("already running", r.get("error") or "")
        self.assertEqual(self.calls, [])

    def test_missing_fields_are_a_400(self):
        st, r = self._post({"parent": "exp-web"})
        self.assertEqual(st, 400)
        st, r = self._post({"name": "only-name"})
        self.assertEqual(st, 400)

    def test_a_fork_refusal_rides_out_verbatim(self):
        km._fork_session = lambda psid, at, nm, now=None, client=None: "fork needs the SDK backend"
        st, r = self._post({"parent": "exp-web", "name": "x3"})
        self.assertEqual(st, 200)
        self.assertFalse(r.get("ok"))
        self.assertIn("SDK backend", r.get("error") or "")

    def test_the_route_reaches_the_one_fork_op_that_inherits_tags(self):
        # tab groups on tags (the user 2026-09-04): /fork and the WS forkSession op both land in
        # _fork_session, which copies the parent's tag memberships onto the fork BEFORE connect — so
        # the headless fork gets exactly the dashboard's behavior (test_kernel_fork.py executes it)
        import inspect
        src = inspect.getsource(km._fork_session_inner)   # _fork_session is a wrapper over it; setUp swaps only the wrapper
        self.assertIn("_inherit_tag_membership(parent_sid, sid)", src)
        self.assertLess(src.index("_inherit_tag_membership(parent_sid, sid)"), src.index("be.connect(sid)"),
                        "membership lands before the connect that precedes the direct push")


if __name__ == "__main__":
    unittest.main()
