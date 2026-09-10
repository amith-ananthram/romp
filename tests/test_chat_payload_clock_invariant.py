#!/usr/bin/env python3
"""The chat payload must not vary with the wall clock.

_send_client dedups by comparing the SERIALIZED payload against what that client last received, so a
single field that ticks with the clock silently defeats the whole mechanism: the kernel re-sends the
FULL chat to every connected client on every push, forever, and the browser re-runs its
upsert/reconcile over the transcript each time. Nothing errors; chat just feels slow while every other
pane stays instant, because their payloads dedup correctly.

That is exactly what `"firstSeen": ... if session["turns"] else now` did for any session with no turns
yet — measured on an idle session as a complete `session` frame every ~1.1s, and it took a
hand-written WebSocket client to see at all (the user 2026-07-27, who reported chat as slow).

So this asserts the INVARIANT rather than the one field: build the same session at two different
`now` values and require byte-identical payloads. Any future clock-varying field fails here instead of
becoming another slow-chat report.

Synthetic only: placeholder uuid, invented prompt text, temp dirs.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_clockinv", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_clockinv", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


class ChatPayloadIsClockInvariant(unittest.TestCase):
    """A deliberately minimal fixture — just enough for build_session to resolve the session. The
    richer ViewBuilder fixture in test_kernel is not reused: importing its class would make unittest
    collect and re-run all of its tests from this module too."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        self._write_turns()
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))

        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        km.NAMES = names
        # A dev machine's real ~/.claude/CLAUDE.md would otherwise leak a system-context card in.
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        # Alive + idle, and FIXED: a `since` derived from the real clock would itself vary between the
        # two builds and mask the very thing under test.
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None,
                                           "color": None}}

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD) = self.saved
        self.td.cleanup()

    def _write_turns(self):
        self.tpath.write_text("\n".join(json.dumps(r) for r in [
            uline(T0, "start the notes-api spike", "u1"),
            aline(T0 + 40, "Spike is up.", "a1", "u1"),
        ]) + "\n")

    def _write_no_completed_turn(self):
        """A prompt with no assistant reply yet: events exist, but session["turns"] is empty — the
        branch whose `else now` fallback caused the every-push resend."""
        self.tpath.write_text(json.dumps(uline(T0, "start the notes-api spike", "u1")) + "\n")

    def _build_twice(self):
        """Same session, two clocks ten minutes apart. Real pushes are ~1s apart; ten minutes makes any
        clock-derived field differ unmistakably rather than by a rounding hair."""
        a = km.build_session(SID, NOW)
        b = km.build_session(SID, NOW + 600)
        self.assertIsNotNone(a, "fixture session must build")
        self.assertIsNotNone(b, "fixture session must build")
        return a, b

    def _assert_identical(self, a, b, why):
        varying = sorted(k for k in set(list(a) + list(b))
                         if json.dumps(a.get(k), default=str) != json.dumps(b.get(k), default=str))
        self.assertEqual(varying, [], "%s — these fields move with the clock, so _send_client's dedup "
                                      "can never hit and the full chat re-sends on every push" % why)

    def test_a_session_with_turns_is_clock_invariant(self):
        self._assert_identical(*self._build_twice(), why="session with turns")

    def test_a_session_with_NO_turns_is_clock_invariant(self):
        self._write_no_completed_turn()
        self._assert_identical(*self._build_twice(), why="session with no completed turn")

    def test_firstSeen_never_falls_back_to_the_current_time(self):
        """Named directly, because this is the field that did it and the failure is otherwise silent."""
        self._write_no_completed_turn()
        a, b = self._build_twice()
        self.assertEqual(a.get("firstSeen"), b.get("firstSeen"),
                         "firstSeen must come from a persisted/fixed source, never `now`")
        self.assertNotIn(a.get("firstSeen"), (NOW, NOW + 600),
                         "firstSeen IS the clock here — the exact bug this guards")

    def test_the_dedup_actually_suppresses_an_unchanged_payload(self):
        """The other half of the contract: a stable payload is only useful if the dedup fires on it."""
        sent = []
        client = {"send": sent.append, "alive": True}
        payload = {"type": "session", "id": SID, "events": []}
        km._send_client(client, ("chat", SID), payload)
        km._send_client(client, ("chat", SID), dict(payload))    # equal value, different object
        self.assertEqual(len(sent), 1, "an unchanged chat payload must go out once, not once per push")
        km._send_client(client, ("chat", SID), {"type": "session", "id": SID, "events": [{"uuid": "u1"}]})
        self.assertEqual(len(sent), 2, "a genuinely changed payload must still be sent")


if __name__ == "__main__":
    unittest.main()
