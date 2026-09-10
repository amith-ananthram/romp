#!/usr/bin/env python3
"""A Codex session going live sends the models frame. GET /models consults the Codex catalog only where
this machine opted in (Codex as the default backend or the judge engine, or a live Codex session), and
every picker re-reads the route only on the kernel's models frame, so a dashboard loaded before the
first Codex session held `codex: {models: []}` until a reload: the two doors that make a Codex row live,
the create's spawn and the revive's resume, sent nothing. Both doors now call _models_changed() once the
backend has landed the row, on every landing: a door cannot see the closed-to-open flip by itself, so
sending each time never misses, and the cost is one GET /models per open picker, which the payload's rev
reconciles.
The real doors run here over a fake backend (spawn, resume, the live and dead sets, a barrier for the
race) and one fake WS client per app that hosts a picker. Each client's send records whether the fake's
live set was non-empty at that moment, so a frame sent before the row landed fails the test that reads
it. Synthetic fixtures only.
"""
import contextlib
import json
import os
import tempfile
import threading
import unittest
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state, and a kernel module that
# can reach a live manager port restarts the live kernel).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["ROMP_MANAGER_PORT"] = "1"             # a dead port, never an inherited live one
os.environ["ROMP_MODEL_CATALOG"] = "off"          # never the Models API from a test
km = load_source("romp_kernel_codex_models_frame", os.path.join(BIN, "romp-kernel"))

SID = "33333333-4444-4555-8666-777777777777"
SID2 = "44444444-5555-4666-8777-888888888888"
APPS = ("chat", "timeline", "feed")               # the apps _models_changed addresses: each hosts a picker


class FakeCodex:
    """The slice of CodexBackend the two doors and their neighbours read: the row walk (the /models gate,
    the identity-colour pick), the registry lookup the revive arm makes, spawn and resume. `sids` are what
    successive spawns mint. `barrier`, when set, is waited on by spawn and by a landing resume AFTER the row
    is live, so two doors can be held with both rows landed and neither returned. `refuse` makes resume
    answer False without landing; `spawn_error` makes spawn raise it before any row exists. Like the real
    backend's, resume answers True for any known sid: a dead one lands, a live one is left as it is."""

    def __init__(self, sids=(SID,), live=None, dead=(), barrier=None, refuse=False, spawn_error=None):
        self.live = dict(live or {})        # sid -> row, what live_sessions() answers
        self.dead = set(dead)               # sids the registry knows that are not running
        self._sids = list(sids)
        self._lock = threading.Lock()
        self.barrier = barrier
        self.refuse = refuse
        self.spawn_error = spawn_error
        self._client_err = None

    def live_sessions(self):
        with self._lock:
            return dict(self.live)

    def _session(self, sid):
        return object() if (sid in self.live or sid in self.dead) else None

    def spawn(self, nm, cwd, bg="", fg="", *a, **kw):
        if self.spawn_error is not None:
            raise self.spawn_error
        with self._lock:
            sid = self._sids.pop(0)
            self.live[sid] = {"backend": "codex", "state": "waiting"}
        if self.barrier is not None:
            self.barrier.wait()
        return sid

    def resume(self, name, sid, cwd=None):
        if self.refuse:
            return False
        if sid in self.dead:
            with self._lock:
                self.dead.discard(sid)
                self.live[sid] = {"backend": "codex", "state": "waiting"}
            if self.barrier is not None:
                self.barrier.wait()
            return True
        return sid in self.live


class ModelsFrameOnLanding(unittest.TestCase):
    """The fake is installed as the kernel's Codex singleton, and one fake WS client per picker app sits in
    the kernel's client list. A client's send appends (app, frame, live) where `live` is whether the fake's
    live set was non-empty when the send ran."""

    def setUp(self):
        self.fake = None
        self.frames = []
        self._saved_backend = km._codex_backend
        self.clients = []
        for app in APPS:
            c = {"app": app, "wid": "w-" + app, "alive": True}
            c["send"] = lambda s, app=app: self.frames.append((app, json.loads(s), bool(self.fake.live)))
            self.clients.append(c)
        with km._clients_lock:
            km._clients.extend(self.clients)

    def tearDown(self):
        with km._clients_lock:
            km._clients[:] = [c for c in km._clients if not any(c is mine for mine in self.clients)]
        km._codex_backend = self._saved_backend

    def _backend(self, fake):
        self.fake = fake
        km._codex_backend = fake
        return fake

    @contextlib.contextmanager
    def _door_quiet(self):
        """The create door's tail (a dirty mark and a direct push) stubbed: neither is under test, and
        the push would build a session view over a fake backend."""
        with mock.patch.object(km, "_push_session_now"), mock.patch.object(km, "_mark_views_dirty"):
            yield

    def _create(self, nm):
        with self._door_quiet():
            return km._create_codex_session_inner(nm, "/TESTDIR")

    def _revive(self, sid, ready=True):
        """Run the real revive door with its neighbours stubbed the way the Codex revive tests do; the
        asker is a chat client with wid w-chat. Returns (focused, sent): the focus messages the asker's
        chat received and the (app, msg) pairs aimed at the asker's dashboard."""
        focused, sent = [], []
        with mock.patch.multiple(km, _sdk=lambda: None, _codex_ready=lambda: ready,
                                 _name_of=lambda s: "web", _cwd_of=lambda s: "/TESTDIR",
                                 _commands_for_cwd=lambda cwd: None, _push_soon=lambda: None,
                                 _reveal_chat_for=lambda client, msg: focused.append(msg),
                                 _send_to_view=lambda app, msg, wid: sent.append((app, msg))):
            km._revive_session_inner(sid, {"wid": "w-chat"})
        return focused, sent

    def _assert_one_frame_per_app(self, rev0):
        self.assertEqual(sorted(app for app, _, _ in self.frames), sorted(APPS),
                         "one models frame to each picker app: %r" % (self.frames,))
        for app, frame, live in self.frames:
            self.assertEqual(frame.get("type"), "models", "the %s client got %r" % (app, frame))
            self.assertGreater(frame.get("rev", 0), rev0, "the %s frame's rev did not move" % app)
            self.assertTrue(live, "the %s frame went out before the row was live" % app)

    def _race(self, run, names):
        """One thread per name, all joined; a thread's exception is re-raised here and a thread still alive
        after the join fails the test."""
        errors = []

        def body(nm):
            try:
                run(nm)
            except BaseException as e:      # noqa: BLE001 - re-raised on the test thread below
                errors.append(e)

        threads = [threading.Thread(target=body, args=(nm,), name="door-" + nm) for nm in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual([t.name for t in threads if t.is_alive()], [], "a door never returned")
        if errors:
            raise errors[0]

    def test_the_first_codex_spawn_sends_the_models_frame_to_every_picker_app(self):
        fake = self._backend(FakeCodex(sids=(SID,)))
        rev0 = km._models_rev[0]
        sid, extra = self._create("web")
        self.assertEqual((sid, extra), (SID, {}))
        self.assertEqual(set(fake.live), {SID})
        self._assert_one_frame_per_app(rev0)

    def test_a_second_spawn_beside_a_live_session_sends_the_frame_again(self):
        """The rule is every landing, not only the one that opens the consult. A door cannot see the
        closed-to-open flip by itself: two first creates can both land before either checks, and a kill
        between a door's check and its landing hides a close-and-reopen; seeing it would need the backend to
        count closings at every live-to-dead write. The cost of sending each time is one GET /models per
        open picker per landing, which the payload's rev reconciles."""
        fake = self._backend(FakeCodex(sids=(SID, SID2)))
        self._create("web")
        rev1 = km._models_rev[0]            # the counter after the first landing: its frames carry this rev
        del self.frames[:]
        sid, _ = self._create("api")
        self.assertEqual(sid, SID2)
        self.assertEqual(set(fake.live), {SID, SID2})
        self._assert_one_frame_per_app(rev1)

    def test_reviving_a_dead_codex_session_sends_the_frame_and_focuses_the_asker(self):
        fake = self._backend(FakeCodex(dead=(SID,)))
        rev0 = km._models_rev[0]
        focused, sent = self._revive(SID)
        self.assertEqual((set(fake.live), fake.dead), ({SID}, set()))
        self._assert_one_frame_per_app(rev0)
        self.assertEqual([(m["type"], m["id"]) for m in focused], [("focus", SID)])
        self.assertEqual(sent, [], "no reviveFailed for a resume that answered True")

    def test_reviving_an_already_live_codex_session_sends_the_frame_too(self):
        """The backend's resume answers True for a live row as well, and the arm fires on that answer: a
        Revive click on a row that is already live costs one re-read per picker, the same as a landing."""
        fake = self._backend(FakeCodex(live={SID: {"backend": "codex", "state": "waiting"}}))
        rev0 = km._models_rev[0]
        focused, sent = self._revive(SID)
        self.assertEqual((set(fake.live), fake.dead), ({SID}, set()))
        self._assert_one_frame_per_app(rev0)
        self.assertEqual([(m["type"], m["id"]) for m in focused], [("focus", SID)])
        self.assertEqual(sent, [])

    def test_a_resume_the_backend_refuses_sends_no_frame_and_files_revive_failed(self):
        fake = self._backend(FakeCodex(dead=(SID,), refuse=True))
        focused, sent = self._revive(SID)
        self.assertEqual((fake.live, fake.dead), ({}, {SID}))
        self.assertEqual(self.frames, [], "nothing landed, so no picker re-reads")
        self.assertEqual([(app, m["type"]) for app, m in sent],
                         [("chat", "reviveFailed"), ("feed", "reviveFailed")])
        self.assertEqual(len({m["text"] for _, m in sent}), 1, "the same text to both panes")
        self.assertEqual(focused, [])

    def test_a_revive_refused_by_the_readiness_check_sends_no_frame(self):
        fake = self._backend(FakeCodex(dead=(SID,)))
        fake._client_err = "codex login missing"
        focused, sent = self._revive(SID, ready=False)
        self.assertEqual((fake.live, fake.dead), ({}, {SID}), "nothing resumed without the app server")
        self.assertEqual(self.frames, [])
        self.assertEqual([(app, m["type"], m["text"]) for app, m in sent],
                         [("chat", "reviveFailed", "codex login missing"),
                          ("feed", "reviveFailed", "codex login missing")])
        self.assertEqual(focused, [])

    def test_a_spawn_that_raises_sends_no_frame_and_the_raise_propagates(self):
        fake = self._backend(FakeCodex(spawn_error=RuntimeError("thread start refused")))
        with self.assertRaises(RuntimeError) as cm:
            self._create("web")
        self.assertEqual(str(cm.exception), "thread start refused")
        self.assertEqual((fake.live, self.frames), ({}, []))

    def test_two_first_creates_landing_together_still_reach_every_app(self):
        fake = self._backend(FakeCodex(sids=(SID, SID2), barrier=threading.Barrier(2, timeout=10)))
        rev0 = km._models_rev[0]
        with self._door_quiet():
            self._race(lambda nm: km._create_codex_session_inner(nm, "/TESTDIR"), ("web", "api"))
        self.assertEqual(set(fake.live), {SID, SID2})
        # two frames per app is the designed outcome of sending on every landing; zero is the bug
        self.assertEqual({app for app, _, _ in self.frames}, set(APPS),
                         "every picker app hears at least one frame: %r" % (self.frames,))
        for app, frame, live in self.frames:
            self.assertEqual(frame.get("type"), "models")
            self.assertGreater(frame.get("rev", 0), rev0)
            self.assertTrue(live, "the %s frame went out before its row was live" % app)


if __name__ == "__main__":
    unittest.main()
