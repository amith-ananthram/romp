#!/usr/bin/env python3
"""`romp compact <session>` — first-class in-place compaction (the user 2026-08-30, via the dashboard
team): the sanctioned alternative to end+new for a long-lived session, and the external hand a session
needs because it cannot /compact ITSELF mid-turn. One compaction path only: the POST /compact route and
the chat's compact button both land in _compact_or_park (park as a ("compact",) op while the session
isn't quiet — it fires ALONE at turn end — else /compact now with the instant 'compacting' cue). The
route's brain (_compact_request) resolves names like /send, forwards remotes, and refuses a dead
session honestly. GET /sessions rows expose `compacting` so --wait and scripted recycling poll the
kernel's own signal. Synthetic fixtures only; hermetic state."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_compact", os.path.join(BIN, "romp-kernel"))

km._limit_hold = lambda sid: None       # the account gate is another module's axis; keep these hermetic

SID = "11111111-2222-3333-4444-555555555555"


class _FakeBackend:
    def __init__(self):
        self.calls = []

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True


class _Stubbed(unittest.TestCase):
    """Common save/stub/restore for the compaction seams."""

    def setUp(self):
        self.be = _FakeBackend()
        self.marked = []
        self._saved = {k: getattr(km, k) for k in
                       ("_working_now", "_compacting_now", "_mark_compacting", "_host_for_sid",
                        "_remote_forward", "_push_all")}
        self._saved_live = km.Sessions.live
        self._saved_backend = km.Sessions.backend_for
        km._working_now = lambda sid: False
        km._compacting_now = lambda sid: False
        km._mark_compacting = lambda sid: self.marked.append(str(sid))
        km._host_for_sid = lambda sid: None
        km._push_all = lambda *a, **k: None
        km.Sessions.live = staticmethod(lambda: {SID: {"state": "waiting", "backend": "sdk"}})
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._pending_ops.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(km, k, v)
        km.Sessions.live = self._saved_live
        km.Sessions.backend_for = self._saved_backend
        km._pending_ops.clear()


class CompactOrPark(_Stubbed):
    def test_idle_fires_now_with_the_instant_cue(self):
        queued = km._compact_or_park(self.be, SID)
        self.assertFalse(queued)
        self.assertEqual(self.be.calls, [("send", "/compact")])
        self.assertEqual(self.marked, [SID], "the idle path stamps the cue — the romp-send gap this verb fixes")

    def test_open_turn_parks_the_compact_op(self):
        km._working_now = lambda sid: True
        queued = km._compact_or_park(self.be, SID)
        self.assertTrue(queued)
        self.assertEqual(self.be.calls, [], "nothing fires into an open turn")
        self.assertEqual(km._pending_ops.get(SID), [("compact",)])
        self.assertEqual(self.marked, [], "the cue belongs to the FIRE, not the park (_apply_pending_ops stamps it)")

    def test_mid_compaction_queues_behind_the_running_one(self):
        km._compacting_now = lambda sid: True
        self.assertTrue(km._compact_or_park(self.be, SID))
        self.assertEqual(km._pending_ops.get(SID), [("compact",)])

    def test_the_chat_button_rides_the_same_helper(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('_compact_or_park(be, sid)                        # parked → queued chip', src,
                      "one compaction path: the WS handler must not keep a private copy of the arm")


class CompactRequest(_Stubbed):
    def test_a_live_sid_resolves_and_reports_now(self):
        res = km._compact_request(SID)
        self.assertEqual(res, {"ok": True, "queued": False})
        self.assertEqual(self.be.calls, [("send", "/compact")])

    def test_open_turn_reports_queued(self):
        km._working_now = lambda sid: True
        self.assertEqual(km._compact_request(SID), {"ok": True, "queued": True})

    def test_missing_who_is_a_400(self):
        res = km._compact_request("")
        self.assertEqual((res["ok"], res["_status"]), (False, 400))

    def test_a_dead_session_refuses_honestly(self):
        res = km._compact_request("ghost")
        self.assertFalse(res["ok"])
        self.assertIn("revive it first", res["error"])
        self.assertEqual(self.be.calls, [], "a refusal compacts nothing")

    def test_a_remote_session_forwards_over_its_tunnel(self):
        seen = []
        km._host_for_sid = lambda sid: {"host": "TESTHOST-B"}
        km._remote_forward = lambda r, path, payload: seen.append((path, payload)) or {"ok": True, "queued": True}
        res = km._compact_request(SID)
        self.assertEqual(res, {"ok": True, "queued": True, "remote": "TESTHOST-B"},
                         "the far kernel's queued verdict rides back, stamped with WHERE the session "
                         "lives — the CLI's --wait polls the local /sessions, which never lists remote "
                         "rows, so without the stamp it reported the session dead (review find)")
        self.assertEqual(seen, [("/compact", {"id": SID})])

    def test_a_silent_remote_kernel_is_a_loud_refusal(self):
        km._host_for_sid = lambda sid: {"host": "TESTHOST-B"}
        km._remote_forward = lambda r, path, payload: None
        res = km._compact_request(SID)
        self.assertFalse(res["ok"])
        self.assertIn("nothing was compacted", res["error"])

    def test_route_wiring(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('if u.path == "/compact":', src)
        self.assertIn("res = _compact_request(who)", src)


class SessionsRowCompacting(_Stubbed):
    def test_rows_expose_the_corroborated_compacting_signal(self):
        km._compacting_now = lambda sid, tm=None, path=None: sid == SID
        rows = km._session_rows()
        self.assertEqual([r["id"] for r in rows], [SID])
        self.assertTrue(rows[0]["compacting"], "--wait and scripted recycling poll this field")
        km._compacting_now = lambda sid, tm=None, path=None: False
        self.assertFalse(km._session_rows()[0]["compacting"])

    def test_rows_pass_their_own_live_meta_so_the_route_never_pays_per_row_merges(self):
        seen = []
        km._compacting_now = lambda sid, tm=None, path=None: seen.append(tm) or False
        km._session_rows()
        self.assertEqual(seen, [{"state": "waiting", "backend": "sdk"}],
                         "the row's live() meta rides in — refetching cost one full merge PER ROW")


if __name__ == "__main__":
    unittest.main()
