#!/usr/bin/env python3
"""The kernel half of moving a session's working directory (the user 2026-09-01): the moveSession WS
op and POST /move resolve + canonicalise the folder like a new-session dir, go through the drive-op
park-or-fire gate (a busy session parks a visible "move to …" chip; a quiet one fires now), hold the
FIFO while the backend's blocking move runs (`_moving`), re-park a CLI-side `busy` a bounded number of
times, and report every outcome as a typed event (moved / moveFailed) to the asker. The tmux backend
refuses with a reason. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_move", os.path.join(BIN, "romp-kernel"))

# The ACCOUNT gate (_limit_hold) is a separate axis, tested in tests/test_kernel_limit_queue.py.
km._limit_hold = lambda sid: None

SID = "11111111-2222-3333-4444-555555555555"


class _FakeBackend:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = []
        self.models = []

    def move(self, sid, path):
        self.calls.append((sid, path))
        return self.answers.pop(0) if self.answers else ""

    def set_model(self, sid, value):
        self.models.append((sid, value))
        return True

    def busy(self, sid):
        return None

    def turn_seq(self, sid):
        return getattr(self, "seq", 0)


def _sync_fire(be, sid, path, tries, wid):
    """_fire_move without the thread: the test wants the outcome on this thread."""
    km._moving.add(sid)
    km._move_now(be, sid, path, tries, wid)


class MoveOps(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.be = _FakeBackend()
        self.sent = []          # client["send"] payloads (the WS asker)
        self.views = []         # _send_to_view(app, msg, wid)
        self.client = {"send": lambda s: self.sent.append(json.loads(s)), "wid": "w1"}
        self._patches = [
            mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be)),
            mock.patch.object(km, "_kernel_knows", lambda sid: True),
            mock.patch.object(km, "_compacting_now", lambda sid: False),
            mock.patch.object(km, "_working_now", lambda sid: False),
            mock.patch.object(km, "_commands_for_cwd", lambda cwd: ([], False)),
            mock.patch.object(km, "_push_soon", lambda: None),
            mock.patch.object(km, "_name_of", lambda sid: "web"),
            mock.patch.object(km, "_cwd_of", lambda sid: self.dir),
            mock.patch.object(km, "_send_to_view", lambda app, msg, wid: self.views.append((app, msg, wid))),
            mock.patch.object(km, "_fire_move", _sync_fire),
            # the retry spacing and the tmux prompt hold are a separate axis (their own tests below and in
            # tests/test_kernel_parked_ops_liveness.py); off, so back-to-back _apply_pending_ops calls here
            # stand for successive cycles
            mock.patch.object(km, "_MOVE_BUSY_RETRY_S", 0.0),
            mock.patch.object(km, "_TMUX_PROMPT_HOLD_S", 0.0),
        ]
        for p in self._patches:
            p.start()
        km._pending_ops.clear()
        km._moving.clear()
        km._move_askers.clear()
        km._drain_hold.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        km._pending_ops.clear()
        km._moving.clear()
        km._move_askers.clear()
        km._drain_hold.clear()

    def test_idle_session_moves_now_and_the_asker_hears_moved(self):
        handled = km._drive({"type": "moveSession", "id": SID, "dir": self.dir + "/"}, self.client)
        self.assertTrue(handled)
        self.assertEqual(self.be.calls, [(SID, self.dir)], "canonicalised (trailing slash gone) before the backend")
        self.assertEqual(self.views, [("chat", {"type": "moved", "id": SID, "name": "web",
                                                "cwd": km._tilde(self.dir)}, "w1")])
        self.assertEqual(self.sent, [])
        self.assertNotIn(SID, km._moving, "the hold is released with the outcome")

    def test_a_missing_folder_is_refused_at_the_door(self):
        km._drive({"type": "moveSession", "id": SID, "dir": os.path.join(self.dir, "nope")}, self.client)
        self.assertEqual(self.be.calls, [])
        self.assertEqual(self.sent[0]["type"], "moveFailed")
        self.assertEqual(self.sent[0]["id"], SID)
        self.assertIn("directory not found", self.sent[0]["text"])
        self.sent.clear()
        km._drive({"type": "moveSession", "id": SID, "dir": "   "}, self.client)
        self.assertEqual(self.sent[0]["type"], "moveFailed")
        self.assertIn("pick a folder", self.sent[0]["text"])

    def test_a_busy_session_parks_a_visible_chip_and_fires_at_turn_end(self):
        with mock.patch.object(km, "_working_now", lambda sid: True):
            km._drive({"type": "moveSession", "id": SID, "dir": self.dir}, self.client)
        self.assertEqual(self.be.calls, [], "mid-turn the backend is not touched")
        self.assertEqual(km._pending_ops.get(SID), [("cwd", self.dir, 0)])
        self.assertEqual(km._move_askers.get(SID), "w1")
        self.assertEqual(km._parked_md(("cwd", self.dir, 0)), "move to " + km._tilde(self.dir),
                         "plain words, not a slash command nobody can type")
        # the turn ends → the producer pass fires the move and ENDS the pass: the op behind it waits
        km._pending_ops[SID].append(("model", "opus"))
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [(SID, self.dir)])
        self.assertEqual(self.be.models, [], "the model pick waits for the relocation to finish")
        self.assertEqual(km._pending_ops.get(SID), [("model", "opus")])
        self.assertEqual(self.views[-1][1]["type"], "moved")
        self.assertEqual(self.views[-1][2], "w1", "a parked move still reports to the dashboard that asked")
        km._apply_pending_ops()
        self.assertEqual(self.be.models, [(SID, "opus")])

    def test_a_repeat_pick_while_parked_replaces_in_place(self):
        other = os.path.realpath(tempfile.mkdtemp())
        with mock.patch.object(km, "_working_now", lambda sid: True):
            km._drive({"type": "moveSession", "id": SID, "dir": self.dir}, self.client)
            km._drive({"type": "moveSession", "id": SID, "dir": other}, self.client)
        self.assertEqual(km._pending_ops.get(SID), [("cwd", other, 0)], "one chip, the latest folder")

    def test_a_move_in_flight_holds_the_queue(self):
        km._moving.add(SID)
        self.assertTrue(km._ops_gate(SID), "anything pressed mid-move parks")
        km._pending_ops[SID] = [("model", "opus")]
        km._apply_pending_ops()
        self.assertEqual(self.be.models, [], "the pass skips a sid whose move is still running")
        km._moving.discard(SID)
        km._apply_pending_ops()
        self.assertEqual(self.be.models, [(SID, "opus")])

    def test_a_cli_side_busy_retries_then_waits_for_the_turn_end(self):
        # the CLI has a turn romp cannot see. A few passes retry blind (the CLI's post-result window has
        # no further event); past those, the cue is the turn's END — the backend's turn_seq moving —
        # never a timer, and never a loud failure while the CLI's turn is still running
        self.be.answers = ["busy", "busy", "busy", "busy"]
        km._pending_ops[SID] = [("cwd", self.dir, 0), ("send", "hello", "human")]
        km._apply_pending_ops()
        self.assertEqual(km._pending_ops[SID][0], ("cwd", self.dir, 1, 0), "back at the head, carrying the turn count")
        km._apply_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(len(self.be.calls), 3, "three passes retry without a cue")
        self.assertEqual(km._pending_ops[SID][0], ("cwd", self.dir, 3, 0))
        km._apply_pending_ops()
        km._apply_pending_ops()
        self.assertEqual(len(self.be.calls), 3, "no turn has ended (turn_seq unchanged) → it waits")
        self.assertEqual(km._pending_ops[SID], [("cwd", self.dir, 3, 0), ("send", "hello", "human")],
                         "a visible chip, the send still behind it")
        self.assertEqual([m for a, m, w in self.views if m["type"] == "moveFailed"], [],
                         "no loud failure while the CLI's turn runs")
        self.be.seq = 1                              # the CLI's own turn ended: a ResultMessage was seen
        km._apply_pending_ops()
        self.assertEqual(len(self.be.calls), 4, "the turn end is the cue")
        self.assertEqual(km._pending_ops[SID][0], ("cwd", self.dir, 4, 1), "busy again → waits on the NEXT turn end")
        km._apply_pending_ops()
        self.assertEqual(len(self.be.calls), 4)
        self.be.seq = 2
        km._apply_pending_ops()                      # answers exhausted → "" → moved
        self.assertEqual(len(self.be.calls), 5)
        self.assertEqual(km._pending_ops[SID], [("send", "hello", "human")])
        self.assertEqual(self.views[-1][1]["type"], "moved")

    def test_a_cli_side_busy_repark_holds_the_retry_for_the_window(self):
        # the drain runs on the pusher now (2026-09-03), which any push or stream atom wakes — so cycles can
        # be milliseconds apart and the three retries would burn inside the sub-second post-result window
        # they exist to outlast. The re-park holds the sid for _MOVE_BUSY_RETRY_S (the producer's old
        # cadence, made explicit); the chip still re-renders at once.
        with mock.patch.object(km, "_MOVE_BUSY_RETRY_S", 3.0):
            self.be.answers = ["busy", "busy"]
            km._pending_ops[SID] = [("cwd", self.dir, 0)]
            before = km._views_dirty[0]
            km._apply_pending_ops()                                  # fires → busy → re-parked at the head
            self.assertEqual(km._pending_ops[SID][0], ("cwd", self.dir, 1, 0), "back at the head, carrying the try")
            self.assertEqual(len(self.be.calls), 1)
            self.assertGreater(km._views_dirty[0], before, "the chip re-renders now")
            km._apply_pending_ops()                                  # back-to-back, as a woken cycle would
            self.assertEqual(len(self.be.calls), 1, "no retry inside the post-result window")
            self.assertIn(SID, km._drain_hold)
            km._drain_hold.clear()                                   # the window passed
            km._apply_pending_ops()
            self.assertEqual(len(self.be.calls), 2, "…then the retry fires")

    def test_the_busy_repark_lands_before_the_move_hold_is_released(self):
        # review find on #904: with `_moving` released first, a drain cycle in the gap could fire the op
        # behind the move or burn a retry; the re-park and its hold come first, the release last
        import inspect
        src = inspect.getsource(km._move_now)
        self.assertLess(src.index('insert(0, ("cwd"'), src.index("_moving.discard(sid)"))
        self.assertLess(src.index("_hold_drain(sid, _MOVE_BUSY_RETRY_S)"), src.index("_moving.discard(sid)"))

    def test_a_parked_retry_survives_a_kernel_restart_and_fires(self):
        # a restart resets turn_seq to 0 and ends whatever turn the CLI owned: an op that waited on a
        # higher count fires on the first pass
        km._pending_ops[SID] = [("cwd", self.dir, 3, 7)]
        km._apply_pending_ops()
        self.assertEqual(len(self.be.calls), 1)
        self.assertEqual(self.views[-1][1]["type"], "moved")

    def test_a_foreign_move_is_refused_by_name(self):
        self.assertEqual(km._FOREIGN_OP_VERB.get("moveSession"), "move",
                         "a federated-board refusal names the move, not a generic action")

    def test_a_backend_refusal_is_a_typed_failure_with_its_words(self):
        self.be.answers = ["Couldn't find a directory at /srv/notes-api/web."]
        km._drive({"type": "moveSession", "id": SID, "dir": self.dir}, self.client)
        self.assertEqual(self.views, [("chat", {"type": "moveFailed", "id": SID, "name": "web",
                                                "text": "Couldn't find a directory at /srv/notes-api/web."}, "w1")])

    def test_a_backend_without_move_fails_loudly(self):
        class _NoMove:
            def busy(self, sid):
                return None
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: _NoMove())):
            km._drive({"type": "moveSession", "id": SID, "dir": self.dir}, self.client)
        self.assertEqual(self.views[-1][1]["type"], "moveFailed")
        self.assertIn("no way to move", self.views[-1][1]["text"])

    def test_tmux_backend_refuses_with_a_reason(self):
        why = km._TMUX.move(SID, self.dir)
        self.assertIsInstance(why, str)
        self.assertIn("tmux", why)
        self.assertIn("new session", why)

    def test_move_session_is_a_drive_op_beside_rename(self):
        import inspect
        src = inspect.getsource(km._drive)
        ops = [c for c in km._drive.__code__.co_consts if isinstance(c, tuple) and "renameSession" in c]
        self.assertTrue(ops and "moveSession" in ops[0], "moveSession rides the same sid-keyed gate as renameSession")
        self.assertIn('t == "moveSession" and msg.get("dir")', src)


class MoveRoute(unittest.TestCase):
    """POST /move over the REAL handler on loopback (the HeadlessRoutes pattern)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.be = _FakeBackend()
        self._patches = [
            mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be)),
            mock.patch.object(km, "_kernel_knows", lambda sid: True),
            mock.patch.object(km, "_live_names", lambda tmux: {"web": SID}),
            mock.patch.object(km, "_tmux_sessions", lambda: {}),
            mock.patch.object(km, "_compacting_now", lambda sid: False),
            mock.patch.object(km, "_working_now", lambda sid: False),
            mock.patch.object(km, "_commands_for_cwd", lambda cwd: ([], False)),
            mock.patch.object(km, "_push_soon", lambda: None),
            mock.patch.object(km, "_name_of", lambda sid: "web"),
            mock.patch.object(km, "_cwd_of", lambda sid: self.dir),
            mock.patch.object(km, "_send_to_view", lambda app, msg, wid: None),
        ]
        for p in self._patches:
            p.start()
        km._pending_ops.clear()
        km._moving.clear()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        km._pending_ops.clear()
        km._moving.clear()

    def _post(self, body):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d/move" % self.port, method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_quiet_session_moves_now_and_the_reply_is_the_outcome(self):
        code, resp = self._post({"target": "web", "dir": self.dir + "/"})
        self.assertEqual((code, resp), (200, {"ok": True, "id": SID, "dir": self.dir}))
        self.assertEqual(self.be.calls, [(SID, self.dir)])

    def test_busy_session_parks_and_says_queued(self):
        with mock.patch.object(km, "_working_now", lambda sid: True):
            code, resp = self._post({"target": "web", "dir": self.dir})
        self.assertEqual((code, resp), (200, {"ok": True, "id": SID, "queued": True, "dir": self.dir}))
        self.assertEqual(km._pending_ops.get(SID), [("cwd", self.dir, 0)])
        self.assertEqual(self.be.calls, [])

    def test_a_cli_side_busy_also_reads_as_queued(self):
        self.be.answers = ["busy"]
        code, resp = self._post({"target": "web", "dir": self.dir})
        self.assertEqual((code, resp), (200, {"ok": True, "id": SID, "queued": True, "dir": self.dir}))
        self.assertEqual(km._pending_ops[SID][0], ("cwd", self.dir, 1, 0), "re-parked, carrying the turn count it waits on")

    def test_refusals_are_loud(self):
        code, resp = self._post({"target": "web"})
        self.assertEqual(code, 400)
        code, resp = self._post({"target": "web", "dir": os.path.join(self.dir, "nope")})
        self.assertEqual(code, 200)
        self.assertFalse(resp["ok"])
        self.assertIn("directory not found", resp["error"])
        code, resp = self._post({"target": "nobody", "dir": self.dir})
        self.assertFalse(resp["ok"])
        self.assertIn('no live session named "nobody"', resp["error"])
        why = "this session runs in a terminal (tmux), which has no way to move a running session"
        self.be.answers = [why]
        code, resp = self._post({"target": "web", "dir": self.dir})
        self.assertEqual(resp, {"ok": False, "error": why}, "the backend's own words ride back")

    def test_a_dormant_session_is_addressed_by_sid(self):
        with mock.patch.object(km, "_live_names", lambda tmux: {}):
            code, resp = self._post({"target": SID, "dir": self.dir})
        self.assertEqual(resp.get("ok"), True)
        self.assertEqual(self.be.calls, [(SID, self.dir)])


if __name__ == "__main__":
    unittest.main()
