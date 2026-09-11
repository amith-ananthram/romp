#!/usr/bin/env python3
"""Picker revive-from-disk (the user 2026-07-05). _revive_session shelled `romp-postal-service revive`,
a subcommand 2b5e181 removed (live-only postal addressing) — the CLI printed 'unknown command' and
EXITED 0, the output was DEVNULL'd, and the kernel then focused a still-dead session: the picker's
Revive silently did nothing for a week. The kernel now owns revive per backend (SDK resume+connect /
Codex resume; a session neither backend holds a record of is refused by name), CHECKS the result, and on failure
sends the chat a reviveFailed event (clears the client's revive loader, shows why) instead of
pretending it worked. Synthetic fixtures only."""
import os
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_rev", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_rev", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
CLIENT = {"app": "chat", "wid": "win-A"}   # the dashboard whose Revive click asked — reveals are per-viewer


class FakeSdk:
    def __init__(self, owns=True, resume_ok=True, connect_ok=True):
        self.calls = []
        self._owns, self._resume_ok, self._connect_ok = owns, resume_ok, connect_ok

    def owns(self, sid):
        return self._owns

    def resume(self, name, sid, cwd=None):
        self.calls.append(("resume", name, sid))
        return self._resume_ok

    def connect(self, sid):
        self.calls.append(("connect", sid))
        return self._connect_ok


class ReviveSession(unittest.TestCase):
    """Drives km._revive_session with the collaborators stubbed; asserts the per-backend action and
    that success → focus while failure → reviveFailed (loud), never both."""

    def setUp(self):
        self.saved = (km._sdk, km._name_of, km._cwd_of, km._push_all, km._reveal_chat_for,
                      km._send_to_view, subprocess.run, km._live_map, km._live_names)
        self.sent, self.focused, self.runs = [], [], []
        # the revive door claims the name against a live snapshot first (names reserved atomically,
        # 2026-09-08): an empty one here, so nobody is live under the name
        km._live_map = lambda: {}
        km._live_names = lambda tm: {}
        km._name_of = lambda sid: "testsess"
        km._cwd_of = lambda sid: "/nonexistent-dir-for-test"
        km._push_all = lambda: None
        km._reveal_chat_for = lambda client, msg: self.focused.append((client, msg))
        km._send_to_view = lambda app, msg, wid: self.sent.append((app, msg, wid))

    def tearDown(self):
        (km._sdk, km._name_of, km._cwd_of, km._push_all, km._reveal_chat_for,
         km._send_to_view, subprocess.run, km._live_map, km._live_names) = self.saved

    def _stub_run(self, returncode=0, stderr=""):
        def run(cmd, **kw):
            self.runs.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)
        subprocess.run = run

    def test_sdk_session_revives_via_resume_and_connect(self):
        be = FakeSdk()
        km._sdk = lambda: be
        km._revive_session(SID, CLIENT)
        self.assertEqual(be.calls, [("resume", "testsess", SID), ("connect", SID)],
                         "SDK-owned dead session → registry alive again + eager connect (resumes lastSid)")
        self.assertEqual([(c, m["type"]) for c, m in self.focused], [(CLIENT, "focus")],
                         "success lands the chat on the tab — the ASKER's chat, nobody else's")
        self.assertEqual(self.sent, [], "no failure event on success")

    def test_sdk_failure_is_loud_and_does_not_focus(self):
        km._sdk = lambda: FakeSdk(connect_ok=False)
        km._revive_session(SID, CLIENT)
        self.assertEqual(self.focused, [], "a failed revive must not focus a still-dead session")
        # BOTH panes of the asking dashboard hear it (review find, 2026-09-08): the chat clears its revive
        # loader, and the feed re-arms the parked card's Revive button it latched on the click. A refusal that
        # reached the chat alone left that button "Reviving…" until the card happened to be re-sent, which
        # for a parked handoff older than the colour ramp's ceiling is never.
        self.assertEqual([(app, msg["type"], msg["id"], wid) for app, msg, wid in self.sent],
                         [("chat", "reviveFailed", SID, "win-A"), ("feed", "reviveFailed", SID, "win-A")],
                         "the failure notice goes to the window whose revive loader and latched button are up")
        self.assertEqual(self.sent[0][1], self.sent[1][1], "one notice, the same words, to both panes")

    def test_a_session_no_backend_holds_is_refused_naming_both_backends(self):
        # the third arm (2026-09-11, the tmux backend's removal): a sid neither the SDK registry nor the
        # Codex registry knows has nothing to resume it; the refusal names both backends, reaches both
        # panes of the asking dashboard, and shells nothing (no launcher, no terminal)
        km._sdk = lambda: None
        self._stub_run(returncode=0)
        with mock.patch.object(km, "_codex", lambda: None):
            km._revive_session(SID, CLIENT)
        self.assertEqual(self.runs, [], "no backend holds it → nothing is run on its behalf")
        self.assertEqual(self.focused, [], "a failed revive must not focus a still-dead session")
        self.assertEqual([app for app, _, _ in self.sent], ["chat", "feed"], "the asker's chat and feed both hear it")
        _, msg, _ = self.sent[0]
        self.assertEqual(msg["type"], "reviveFailed")
        self.assertIn("Claude Code", msg["text"], "the refusal names the backends that could have held it")
        self.assertIn("Codex", msg["text"])

    def test_the_removed_postal_subcommand_is_gone(self):
        # the regression pin: 2b5e181 removed `romp-postal-service revive`; the kernel must never
        # shell it again (it exits 0 on unknown commands, so the failure is undetectable). The
        # docstring may NAME the old path as history — the pin is on the invocation form.
        import inspect
        self.assertNotIn('HERE / "romp-postal-service"', inspect.getsource(km._revive_session))
        self.assertNotIn('HERE / "romp-postal-service"', inspect.getsource(km._revive_session_inner))


class SdkResumePreservesLastSid(unittest.TestCase):
    """resume() marks a dormant session alive for _ensure/connect. It must PRESERVE the registry's
    lastSid — the NEWEST transcript fsid (a /clear or relaunch mints new fsids under the same romp
    sid) that SdkSession actually resumes from; stamping the original sid would silently resume an
    OLD conversation state."""

    def _resume(self, reg):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        state = Path(td.name)
        (state / "sdk").mkdir(parents=True, exist_ok=True)
        if reg is not None:
            sb.write_reg(state, SID, reg)

        class FakeBackend:
            state_dir = state
            _reg_lock = threading.Lock()   # resume's alive flip holds the RMW lock (2026-08-31)
            _reg_for_flip = sb.SdkBackend._reg_for_flip   # …and reads through the flip base
            def _poke(self):
                pass
        sb.SdkBackend.resume(FakeBackend(), "testsess", SID)
        return sb.read_reg(state, SID)

    def test_newer_lastsid_survives_revive(self):
        reg = self._resume({"sid": SID, "name": "testsess", "cwd": "/tmp", "mode": "auto",
                            "effort": "high", "lastSid": "99999999-8888-7777-6666-555555555555",
                            "alive": False})
        self.assertEqual(reg["lastSid"], "99999999-8888-7777-6666-555555555555",
                         "the newest fsid is the resume point — never clobbered back to the birth sid")
        self.assertTrue(reg["alive"])
        self.assertEqual(reg["effort"], "high", "the rest of the registry survives the revive too")

    def test_empty_lastsid_falls_back_to_the_sid(self):
        reg = self._resume({"sid": SID, "name": "testsess", "cwd": "/tmp", "lastSid": "", "alive": False})
        self.assertEqual(reg["lastSid"], SID, "a never-relaunched session resumes from its own transcript")


class CodexRevive(unittest.TestCase):
    """A dead Codex session revives through CodexBackend.resume — owns() is live-only, so the
    tmux fallback used to build `claude --resume` for it and the registry stayed dead (the
    v1.3.12 audit's P1, real-backend probe)."""

    def setUp(self):
        self.saved = (km._sdk, km._codex, km._codex_ready, km._name_of, km._cwd_of,
                      km._push_all, km._reveal_chat_for, km._send_to_view, km._live_map,
                      km._commands_for_cwd)
        self.sent, self.focused = [], []
        km._sdk = lambda: None
        km._name_of = lambda sid: "webby"
        km._cwd_of = lambda sid: "/tmp"
        km._push_all = lambda: None
        km._commands_for_cwd = lambda cwd: None
        km._live_map = lambda: {}
        km._reveal_chat_for = lambda client, msg: self.focused.append(msg)
        km._send_to_view = lambda app, msg, wid: self.sent.append(msg)

    def tearDown(self):
        (km._sdk, km._codex, km._codex_ready, km._name_of, km._cwd_of, km._push_all,
         km._reveal_chat_for, km._send_to_view, km._live_map,
         km._commands_for_cwd) = self.saved

    def test_a_dead_codex_session_resumes_through_the_codex_backend(self):
        cx = mock.MagicMock()
        cx._session.return_value = object()            # the registry knows the dead row
        cx.resume.return_value = True
        km._codex = lambda: cx
        km._codex_ready = lambda: True
        km._revive_session("11111111-2222-3333-4444-555555555555", {"wid": "w1", "send": lambda m: None})
        cx.resume.assert_called_once_with("webby", "11111111-2222-3333-4444-555555555555",
                                          cwd="/tmp")
        self.assertTrue(self.focused, "success focuses the asker's chat")
        self.assertFalse(self.sent, "and no failure event fires")

    def test_a_dead_codex_session_with_no_app_server_fails_loudly(self):
        cx = mock.MagicMock()
        cx._session.return_value = object()
        cx._client_err = "codex app-server is not running"
        km._codex = lambda: cx
        km._codex_ready = lambda: False
        km._revive_session("11111111-2222-3333-4444-555555555555", {"wid": "w1", "send": lambda m: None})
        self.assertFalse(cx.resume.called, "nothing resumes without the app server")
        self.assertTrue(any(m.get("type") == "reviveFailed" for m in self.sent),
                        "the asker hears the refusal: %r" % self.sent)


if __name__ == "__main__":
    unittest.main()
