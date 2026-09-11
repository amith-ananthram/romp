#!/usr/bin/env python3
"""Cancelling the "Opening…" cue (the ✕/Esc/backdrop → cancelCreate) tears down the pending session.

The user 2026-07-14: a new-session spawn sometimes fails and the modal hung on the 30s backstop, so the
cue got an abort. The webview knows only the session NAME, so the kernel resolves it to a live session and
ends it — a slow-but-successful open must not leave an orphan tab. A name no live session answers to has
nothing to reap: both backends create inline, so a session exists by the time its cue can be cancelled
(the pending-spawn set that reaped a threaded spawn on arrival went with the tmux backend, 2026-09-11).
A remote cue ("host:name") is dismissed client-side only; the local kernel reaps nothing.

HISTORY GUARD (the user 2026-07-16, the staged-demo teardowns): names are not identities — same-named
generations coexist, so the cancel's name lookup can resolve to an ESTABLISHED conversation instead of
the just-opened spawn, and the kill+hide erased staged work with no trace. A session whose transcript
holds a user record refuses the teardown loudly; only a never-prompted session is torn down.

Synthetic only: placeholder UUID, hostname TESTHOST, hermetic temp STATE.
"""
import os
import tempfile
import types
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_cancelcreate", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NAME = "TESTHOST-newsess"


class FakeBackend:
    def __init__(self):
        self.killed = []

    def kill(self, sid):
        self.killed.append(sid)


class CancelCreateTest(unittest.TestCase):
    def setUp(self):
        self.be = FakeBackend()
        self.sent = []        # (app, msg) handed to the chat view
        self._live = {}       # sid -> meta; drives the monkeypatched name lookup
        self._saved = {}

        def patch(nm, fn):
            self._saved[nm] = getattr(km, nm)
            setattr(km, nm, fn)

        patch("_send_to_app", lambda app, m: self.sent.append((app, m)))
        patch("_push_soon", lambda: None)
        patch("_push_all", lambda: None)
        patch("_live_map", lambda: self._live)
        patch("_live_names", lambda live_map: {NAME: SID} if SID in (live_map or {}) else {})
        self._saved_bf = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)

    def tearDown(self):
        for nm, v in self._saved.items():
            setattr(km, nm, v)
        km.Sessions.backend_for = self._saved_bf

    def _cancel(self, name):
        # Drive the real WS handler: _dispatch_ws starts with `if _drive(...)` which returns False for
        # cancelCreate before touching a backend, so a dummy self / empty client is safe.
        km.Handler._dispatch_ws(types.SimpleNamespace(), {"type": "cancelCreate", "name": name}, {})

    def test_live_session_is_ended(self):
        self._live = {SID: {}}                                 # the session already materialized
        self._cancel(NAME)
        self.assertEqual(self.be.killed, [SID], "the live pending session is killed on its backend")
        self.assertTrue(any(m.get("type") == "closed" and m.get("id") == SID for _a, m in self.sent),
                        "the live view is told to prune it (no tab-hide state anymore — the kill is the event)")

    def test_a_name_no_live_session_answers_to_reaps_nothing(self):
        self._live = {}                                        # no session of that name is running
        self._cancel(NAME)
        self.assertEqual(self.be.killed, [], "nothing to kill")
        self.assertEqual(self.sent, [], "and nothing to prune: the cue dismisses client-side")

    def test_remote_cue_is_not_reaped_locally(self):
        self._live = {SID: {}}
        self._cancel("TESTHOST:" + NAME)                          # host-prefixed cue
        self.assertEqual(self.be.killed, [], "a remote spawn is not torn down by the local kernel")
        self.assertEqual(self.sent, [], "and the local live view is left alone")

    # ── the history guard (the user 2026-07-16) ──

    def _transcript(self, records):
        import json
        p = os.path.join(tempfile.mkdtemp(), SID + ".jsonl")
        with open(p, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return p

    def test_an_established_conversation_refuses_the_teardown(self):
        # the demo shape: a staged session wearing a REUSED name; the cancelled cue's lookup lands on it
        path = self._transcript([
            {"type": "system", "subtype": "init"},
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "stage the demo board"}},
            {"type": "assistant", "uuid": "a1"}])
        self._saved["_path_of"] = km._path_of
        km._path_of = lambda sid: path
        self._live = {SID: {}}
        self._cancel(NAME)
        self.assertEqual(self.be.killed, [], "a session with a human turn is work, never a pending spawn")
        self.assertEqual(self.sent, [], "and the live view is not told to prune it")

    def test_a_fresh_spawn_with_only_init_lines_is_still_torn_down(self):
        path = self._transcript([{"type": "system", "subtype": "init"}])
        self._saved["_path_of"] = km._path_of
        km._path_of = lambda sid: path
        self._live = {SID: {}}
        self._cancel(NAME)
        self.assertEqual(self.be.killed, [SID], "only system/init lines → a genuine pending spawn")

    def test_an_unreadable_transcript_fails_toward_refusing(self):
        self._saved["_path_of"] = km._path_of
        km._path_of = lambda sid: (_ for _ in ()).throw(OSError("boom"))
        self._live = {SID: {}}
        self._cancel(NAME)
        self.assertEqual(self.be.killed, [], "can't prove it's fresh → don't kill it")


if __name__ == "__main__":
    unittest.main()
