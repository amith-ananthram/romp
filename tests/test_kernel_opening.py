#!/usr/bin/env python3
"""A just-created session: the OPENING chip and the targeted single-session push (2026-08-10).

Two failures this closes, both measured live against a busy fleet:
- The opening chip's stand-down event was per-backend but INCOMPLETE: SDK had the connect handshake
  (`connected`), tmux had only "the transcript's first record" — which lands with the first MESSAGE,
  so a fully-up tmux CLI wore the opening dots until the user typed. The matching tmux event is the
  CLI's statusline hook publishing its first @claude-state.
- A new session's tab + payload rode the periodic FULL push cycle, which runs seconds on a busy fleet
  (tab appeared 5-6s after the create, opening→ready 12s, while /sessions knew the session at 0.4s).
  _push_session_now sends the tab strip + that ONE session's payload to chat clients directly; the
  create paths and the SDK connect handshake fire it.

SYNTHETIC fixtures only: invented text, placeholder UUIDs.
"""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_opening", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"


def _row(**over):
    """A Sessions.live()-shaped lane row with every key build_session touches."""
    r = {"state": "", "since": None, "model": "", "effort": "", "context": None,
         "compactPct": None, "color": None, "mode": "", "backend": "tmux"}
    r.update(over)
    return r


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
                      km.NAMES, km._tmux_sessions, km._sdk)
        names = td / "names"; names.mkdir()
        proj = td / "projects"; proj.mkdir()
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        for d in (jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR):
            d.mkdir()
        jd.STATE = td
        km.NAMES = names
        km._sdk = lambda: None            # no SDK backend: the synthesized tmux path is under test
        km._parse_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE,
         km.NAMES, km._tmux_sessions, km._sdk) = self.saved
        self.td.cleanup()


class OpeningChipStandsDownOnTheBackendEvent(_Base):
    """build_session's chip for a session whose transcript doesn't exist yet: OPENING until the
    backend's own "CLI is up" event — tmux's first published @claude-state / SDK's handshake —
    never until the first transcript record (that lands only with the first message)."""

    def _chip(self, row):
        tmux = {SID: row}
        km._tmux_sessions = lambda: tmux
        m = km.build_session(SID, NOW, tmux)
        self.assertIsNotNone(m, "a live transcript-less session still gets a frame")
        return m["status"]["state"]

    def test_tmux_booting_cli_reads_opening(self):
        # spawned (the lane row exists — the launcher sets @romp-session-id at creation) but the
        # CLI's statusline hook hasn't published a @claude-state yet → still opening
        self.assertEqual(self._chip(_row(state="")), "opening")

    def test_tmux_first_claude_state_ends_opening(self):
        # the hook published "waiting": the CLI is up at its prompt — no transcript exists yet
        # (the first record only lands with the first message), and the chip must NOT wait for it
        self.assertEqual(self._chip(_row(state="waiting", since=NOW - 5)), "ready")

    def test_sdk_pre_handshake_reads_opening(self):
        # SDK snapshots carry a non-empty state from birth ("waiting"), so the state leg must not
        # stand the override down for them — the chip covers the backend's live spawn window
        # (`spawning`: thread up, client not yet), which the handshake closes
        self.assertEqual(self._chip(_row(state="waiting", backend="sdk", connected=False,
                                         spawning=True)), "opening")

    def test_sdk_handshake_ends_opening(self):
        self.assertEqual(self._chip(_row(state="waiting", backend="sdk", connected=True)), "ready")

    def test_sdk_dormant_created_session_reads_ready(self):
        # No spawn in flight and no connected flag — the dormant row every created-but-unmessaged
        # SDK session reports after a kernel restart (idle CLIs die with the kernel, boot reconcile
        # leaves them lazy, the transcript only lands with the first turn). Reading the missing
        # `connected` as "still opening" wore the dots for hours (the user 2026-08-13); a send
        # wakes a dormant session in seconds, so it is READY.
        self.assertEqual(self._chip(_row(state="waiting", backend="sdk", connected=False)), "ready")


class PushSessionNow(_Base):
    """_push_session_now: one session's tab strip + chat payload to every CHAT client, directly."""

    def setUp(self):
        super().setUp()
        # a live tmux session WITH a transcript (the ordinary shape; the transcript-less shape is
        # covered above) — pdir keyed like discover expects
        cdir = Path(self.td.name) / "work"; cdir.mkdir()
        pdir = jd.PROJECTS / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        rec = {"type": "user", "timestamp": "2026-06-11T00:00:00.000Z", "uuid": "u1",
               "parentUuid": None, "promptSource": "typed",
               "message": {"role": "user", "content": "hello there"}}
        (pdir / (SID + ".jsonl")).write_text(json.dumps(rec) + "\n")
        (jd.NAMES / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.tmux = {SID: _row(state="waiting", since=NOW - 5)}
        km._tmux_sessions = lambda: self.tmux
        self.sent = []
        self.client = {"app": "chat", "alive": True, "wid": "", "qbytes": 0,
                       "send": lambda s: self.sent.append(json.loads(s))}
        self.other = {"app": "feed", "alive": True, "wid": "", "qbytes": 0,
                      "send": lambda s: self.fail("a feed client must not receive chat frames")}
        self.saved_clients = list(km._clients)
        with km._clients_lock:
            km._clients[:] = [self.client, self.other]

    def tearDown(self):
        with km._clients_lock:
            km._clients[:] = self.saved_clients
        super().tearDown()

    def test_sends_the_tab_strip_and_that_sessions_payload(self):
        km._push_session_now(SID)
        types = [m["type"] for m in self.sent]
        self.assertIn("tabOrder", types, "the tab strip lands so the client can place the tab")
        self.assertIn("session", types, "…and the session payload fills it in")
        tabs = next(m for m in self.sent if m["type"] == "tabOrder")
        self.assertIn(SID, tabs["order"])
        self.assertEqual(next(t["name"] for t in tabs["tabs"] if t["id"] == SID), "web")
        sess = next(m for m in self.sent if m["type"] == "session")
        self.assertEqual(sess["id"], SID)
        self.assertLess(types.index("tabOrder"), types.index("session"),
                        "strip first — a session frame for an unknown tab would have nowhere to land")

    def test_idempotent_with_the_periodic_pusher(self):
        # the next full cycle re-sends both slots; per-client dedup absorbs the overlap, so a second
        # identical targeted push sends NOTHING new
        km._push_session_now(SID)
        n = len(self.sent)
        km._push_session_now(SID)
        self.assertEqual(len(self.sent), n, "unchanged payloads dedup — no re-send storm")

    def test_an_unknown_sid_sends_nothing(self):
        km._push_session_now("99999999-8888-7777-6666-555555555555")
        self.assertEqual(self.sent, [], "an unknown sid is not an error, just a no-op")

    def test_the_shared_delta_baseline_is_left_alone(self):
        # only a push that reaches EVERY client may advance _prev_chat_events (the 2026-07-28
        # stranded-delta lesson) — the targeted push must not touch it
        km._prev_chat_events.pop(SID, None)
        km._push_session_now(SID)
        self.assertNotIn(SID, km._prev_chat_events)


if __name__ == "__main__":
    unittest.main()
