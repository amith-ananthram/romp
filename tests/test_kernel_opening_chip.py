#!/usr/bin/env python3
"""The opening chip's deciding event is per-backend, and it covers ONLY a spawn in flight.

A session whose transcript doesn't exist yet reads "opening" (2026-08-05: a just-spawned tab said
"Working" over a clock with no honest base). For tmux the transcript's first record is the only
observable, so the file IS the event. For an SDK session it isn't: a fresh SDK session writes NO
transcript until its first turn, so keying the chip on the file left a fully-up, idle session wearing
the animated opening dots until the user's first message — indefinitely (the user 2026-08-08, who read
minutes of dots as creation still running). The SDK backend knows the earlier designed event — the
handshake (snapshot `connected`, set the moment the client context opens) — and the override stands
down on it.

The override must also NOT outlive the create (the user 2026-08-13): `connected` is in-memory only, so
a created-but-never-messaged session whose CLI isn't up right now — the normal state of every fresh SDK
session after a kernel restart, since idle CLIs die with the kernel and boot reconcile leaves them
lazy — read the missing flag as "still opening" and wore the dots for HOURS, while one message would
wake it in seconds. The chip now keys on the backend's live `spawning` report (thread up, client not
yet): opening covers exactly the spawn/handshake window, and a dormant created session reads ready.

SYNTHETIC fixtures only (placeholder uuid, temp dirs), modeled on test_chat_payload_clock_invariant.
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
jd = load_source("romp_judge_openchip", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_openchip", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000


class OpeningChipDecidingEvent(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        self.pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        self.pdir.mkdir(parents=True)
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        # Patch BOTH judge instances: this module's `jd` and the kernel's own `km.jd` — kernel.py loads
        # judge.py itself (SourceFileLoader "romp_judge"), so they are distinct module objects, and
        # _sessions()/discover() reads km.jd. Patching only the test's jd leaves discovery scanning the
        # real ~/.claude/projects, and the fixture transcript is never found (chip stuck "opening").
        self.saved = [(m, k, getattr(m, k)) for m in (jd, km.jd)
                      for k in ("NAMES", "PROJECTS", "CAPDIR", "ARCHDIR", "GOALDIR", "STATE")]
        self.saved += [(km, "NAMES", km.NAMES), (km, "_tmux_sessions", km._tmux_sessions),
                       (km, "_GLOBAL_CLAUDE_MD", km._GLOBAL_CLAUDE_MD)]
        for m in (jd, km.jd):
            m.NAMES, m.PROJECTS = names, proj
            m.CAPDIR, m.ARCHDIR, m.GOALDIR = td / "captions", td / "archive", td / "goals"
            m.STATE = td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        self.tm = {"state": "waiting", "since": NOW - 5, "model": "Opus 5", "effort": "xhigh",
                   "context": None, "compactPct": None, "color": None, "backend": "sdk"}
        km._tmux_sessions = lambda: {SID: dict(self.tm)}

    def tearDown(self):
        for m, k, v in self.saved:
            setattr(m, k, v)
        self.td.cleanup()

    def _state(self):
        m = km.build_session(SID, NOW)
        self.assertIsNotNone(m, "fixture session must build")
        return (m.get("status") or {}).get("state")

    def test_a_spawn_in_flight_with_no_transcript_is_opening(self):
        # spawn/handshake window open (the backend's live `spawning` report: thread up, client not
        # yet) and nothing on disk → opening (the 2026-08-05 rule, scoped to the in-flight window)
        self.tm["spawning"] = True
        self.assertEqual(self._state(), "opening")

    def test_a_dormant_created_session_is_ready_not_opening(self):
        # Created, never messaged, CLI not up RIGHT NOW — every fresh SDK session lands here after a
        # kernel restart (idle CLIs die with the kernel; boot reconcile leaves them lazy; the
        # transcript only appears with the first turn). A dormant row carries NO spawning key, and
        # the old gate read the equally-missing `connected` as "still opening" — dots for hours on a
        # session one message from answering (the user 2026-08-13).
        self.assertEqual(self._state(), "ready",
                         "a created-but-unmessaged dormant session is ready (a send wakes it), not opening")

    def test_the_live_merge_threads_spawning_through(self):
        # The merge dropping a backend key is a known silent killer (the 2026-07-11 bgTasks lesson:
        # the snapshot carried it, every consumer saw None). Guard the thread-through explicitly.
        class _FakeBackend:
            def live_sessions(self):
                return {SID: {"state": "waiting", "since": "5", "model": "", "effort": "",
                              "spawning": True}}
        saved = km._sdk
        km._sdk = lambda: _FakeBackend()
        try:
            row = km.Sessions.live().get(SID)
        finally:
            km._sdk = saved
        self.assertIsNotNone(row)
        self.assertTrue(row.get("spawning"), "Sessions.live must carry the backend's spawning bit")

    def test_the_sdk_handshake_ends_opening_before_any_transcript_exists(self):
        # a fresh SDK session is fully open the moment its client connects — its transcript only
        # appears with the FIRST TURN, so the file must not keep the dots up on a ready session
        self.tm["connected"] = True
        self.assertEqual(self._state(), "ready",
                         "connected + idle = ready to take a message; the dots would be a lie")

    def test_the_transcripts_first_record_still_ends_opening(self):
        # the tmux path (no `connected` signal): the file landing remains the deciding event. A
        # COMPLETED turn, so the chip settles to ready rather than working (an open turn).
        def iso(t):
            return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
        (self.pdir / (SID + ".jsonl")).write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "timestamp": iso(NOW - 60), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed", "message": {"role": "user", "content": "start the notes-api spike"}},
            {"type": "assistant", "timestamp": iso(NOW - 20), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Spike is up."}],
                         "stop_reason": "end_turn"}},
        ]) + "\n")
        self.assertEqual(self._state(), "ready")
