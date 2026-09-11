#!/usr/bin/env python3
"""A Codex session's red card says what the Codex backend said — never that a claude process could not start.

The chat's launch-failure card (build_session, beside the API-error card) framed every backend's text with
"This session's claude process could not start — …" unless the record carried `dep`, a key only the SDK
backend writes. The Codex backend's records ({text, at, limit}, kernel/codex_backend.py) never carry it,
so every failure a Codex session reported came out announced as a claude process failing to start: the
missing-login hint at spawn, and the mid-life failures too (an app-server that died and found the login
gone, a turn the account refused) on a session that had been running for an hour. The card now shows a
Codex session's text as the backend wrote it, and the backend frames its own texts: the two hints and the
"codex turn …" records are whole sentences already; a RAW client failure (a missing binary's errno line, a
bare "TimeoutError") is framed as the app-server's (CodexBackend._client_failure_text), since shown as
written it would name no process at all.

The other side of the same branch is pinned too: an SDK-owned session's raw failure keeps the claude
framing, so the card is keyed on the BACKEND, not on whether the text happens to name codex.

Drives the real kernel and real backends; the seams stubbed are the Codex SDK client (a factory that
fails the way a missing `codex login` or a missing binary does) and, on the SDK side, the box's own
dependency check. SYNTHETIC fixtures only (invented names and text).

Run:    python3 tests/test_codex_launch_error_card.py
"""
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_codexlauncherr", os.path.join(BIN, "romp-kernel"))
cb = load_source("romp_codex_backend_launcherr", os.path.join(ROOT, "kernel", "codex_backend.py"))
sb = load_source("romp_sdk_backend_launcherr_card", os.path.join(BIN, "romp_sdk_backend.py"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
# What str() makes of the OSError a missing or broken codex binary raises from CodexClient(...).start().
ERRNO_LINE = str(OSError(2, "No such file or directory", "codex"))


def _no_login():
    """The client factory on a machine without `codex login`: the backend's own auth check raises this
    (CodexBackend._check_auth), and the backend records it on every session it then cannot run."""
    raise RuntimeError(cb.LOGIN_HINT)


def _no_binary():
    """The client factory on a machine whose codex binary is missing: the SDK raises the errno, and the
    backend keeps str(error) — a line that names no process (_record_client_failure_locked)."""
    raise OSError(2, "No such file or directory", "codex")


class CodexLaunchErrorCard(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))
        self.saved_sdk, self.saved_codex = km._sdk, km._codex_backend
        km._sdk = lambda: None                    # deterministic: no SDK backend on this box
        self.logs = []

    def tearDown(self):
        km._sdk, km._codex_backend = self.saved_sdk, self.saved_codex
        jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def _bind(self, factory):
        """A real CodexBackend on the test's state root, bound as the kernel's Codex singleton."""
        self.be = cb.CodexBackend(jd.STATE, client_factory=factory, log=self.logs.append)
        km._codex_backend = self.be
        return self.be

    def _card(self, sid):
        out = km.build_session(sid, time.time())
        self.assertIsNotNone(out, "a Codex session that cannot run still gets a frame")
        cards = [e for e in out["events"] if e.get("kind") == "apiError"]
        self.assertEqual(len(cards), 1, cards)
        return cards[0]["text"]

    def _assert_framed_as_the_app_servers(self, text):
        """The frame is asserted by shape, not by wording: the errno line is kept whole at the end, and
        what precedes it names codex. A bare errno line (nothing precedes it) and the claude frame both fail."""
        self.assertTrue(text.endswith(ERRNO_LINE), text)
        self.assertIn("codex", text[: -len(ERRNO_LINE)].lower(),
                      "a bare errno line names no process — the frame must say whose failure it is: %r" % text)
        self.assertNotIn("claude", text.lower(), "an OpenAI Codex session has no claude process to fail")

    def test_a_missing_login_is_shown_in_the_backends_words(self):
        be = self._bind(_no_login)
        sid = be.spawn("web", self.td.name)
        self.assertEqual(be.launch_error(sid)["text"], cb.LOGIN_HINT, "the backend recorded the hint")
        text = self._card(sid)
        self.assertEqual(text, cb.LOGIN_HINT, "the card says what the backend said, remedy included")
        self.assertNotIn("claude", text.lower(), "an OpenAI Codex session has no claude process to fail")

    def test_a_failure_mid_life_is_not_announced_as_a_start_failure(self):
        # The worker's record for a turn the account refused (CodexBackend._work): the session had been
        # running, so "could not start" would be wrong twice over — the process and the moment.
        be = self._bind(_no_login)
        sid = be.spawn("api", self.td.name)
        s = be._session(sid)
        with s.lock:
            s.launch_error = {"text": "codex turn rejected: model gpt-x is not supported",
                              "at": time.time(), "limit": False}
        text = self._card(sid)
        self.assertEqual(text, "codex turn rejected: model gpt-x is not supported")
        self.assertNotIn("could not start", text)

    def test_a_raw_client_failure_is_framed_as_the_app_servers(self):
        # Only the two hints are whole sentences; a missing binary or a failed handshake leaves
        # str(error) in _client_err, and both writers of the client-missing record (spawn, and the
        # worker's _run_turn_in_mode when the app-server is gone mid-life) frame it before the card sees it.
        be = self._bind(_no_binary)
        sid = be.spawn("web", self.td.name)
        self._assert_framed_as_the_app_servers(be.launch_error(sid)["text"])
        self._assert_framed_as_the_app_servers(self._card(sid))
        s = be._session(sid)
        with s.lock:
            s.launch_error = None                 # as after a turn that ran, before the app-server died
        self.assertFalse(be._run_turn_in_mode(s), "no client: the turn does not run and the queue parks")
        self._assert_framed_as_the_app_servers(be.launch_error(sid)["text"])
        self._assert_framed_as_the_app_servers(self._card(sid))


class _FakeSdkSess:
    """What SdkBackend._record_launch_error reads off a session: its identity, and the stderr the CLI
    wrote before it died (nothing here — the exception's own text is the whole reason)."""

    def __init__(self, sid, name):
        self.sid, self.name, self._stderr_tail = sid, name, []

    def stderr_tail(self):
        return ""


class SdkLaunchErrorCardKeepsTheClaudeFraming(unittest.TestCase):
    """The other side of the branch the card takes: an SDK-owned session's raw failure still reads as its
    claude process failing to start. Keyed on backend identity — a card that asked the TEXT (does it name
    codex?) or "not sdk" would pass the Codex cases above and could misroute this one."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))
        self.saved_sdk, self.saved_codex = km._sdk, km._codex_backend
        # A real SdkBackend; the one seam is the box's dependency check (a missing SDK outranks every
        # per-session record with a whole-sentence text of its own, which is not the path under test).
        saved = sb.sdk_importable
        sb.sdk_importable = lambda: True
        try:
            self.be = sb.SdkBackend(self.td.name, "/bin/true", lambda *a, **k: None)
        finally:
            sb.sdk_importable = saved
        km._sdk = lambda: self.be
        km._codex_backend = cb.CodexBackend(jd.STATE, client_factory=_no_login, log=lambda m: None)

    def tearDown(self):
        km._sdk, km._codex_backend = self.saved_sdk, self.saved_codex
        jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def test_an_sdk_sessions_raw_failure_keeps_the_claude_framing(self):
        cwd = Path(self.td.name) / "proj"
        cwd.mkdir()
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "name": "api", "cwd": str(cwd), "mode": "acceptEdits"})
        self.be._record_launch_error(_FakeSdkSess(SID, "api"), RuntimeError("boom"))
        rec = self.be.launch_error(SID)
        self.assertFalse(rec.get("dep"), "a plain crash, not a missing dependency: the framed path")
        self.assertEqual(km._session_backend(SID, None), "sdk")
        out = km.build_session(SID, time.time())
        self.assertIsNotNone(out)
        self.assertEqual([e["text"] for e in out["events"] if e.get("kind") == "apiError"],
                         ["This session's claude process could not start — RuntimeError: boom"])


if __name__ == "__main__":
    unittest.main()
