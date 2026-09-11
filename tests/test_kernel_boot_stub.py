#!/usr/bin/env python3
"""A brand-new LIVE session can have no transcript yet (a Codex thread before its first materialized
record; a terminal session's first ~7s, when that backend existed), and build_session used to return
None for it — no session frame existed, so its input echo / queued bubble had nothing to render onto and
the first message typed into a just-created session was invisible until the transcript appeared (the
user 2026-07-20: the UI must respond even when the kernel can't get the session going yet). Discovery
misses now synthesize a transcriptless entry for a sid that is LIVE in the map — the same treatment the SDK
path always had via _sdk_sess — so the frame exists from second zero and the live-echo merge lands on
it. SYNTHETIC fixtures only."""
import os
import time
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_bootstub", os.path.join(BIN, "romp-kernel"))

SID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"

# the shape _live_map() always provides for a live session (a booting session has no model/context yet)
_TM_META = {"state": "", "since": None, "model": "", "effort": "", "mode": "",
            "context": None, "compactPct": None, "backend": "codex"}


class _FakeCodex:
    """The owning backend of the boot window: it holds the sid and its own input echo (the Codex backend
    echoes for itself inside send(), the way the SDK backend does), and nothing else yet."""

    def __init__(self):
        self.echoes = []

    def owns(self, sid):
        return sid == SID

    def has_record(self, sid):
        return sid == SID

    def echo(self, text, t):
        # the Codex backend's live_atoms shape, field for field
        self.echoes.append({"type": "user", "uuid": "echo-%d" % len(self.echoes), "session_id": SID, "fsid": "thread-1",
                            "t": t, "parentUuid": None, "author": "human", "_echo_text": text,
                            "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def live_atoms(self, sid):
        return list(self.echoes) if sid == SID else []

    def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
        return None

    def pending_queued(self, sid):
        return []

    def busy(self, sid):
        return None

    def current_ask(self, sid):
        return None


class BootWindowStub(unittest.TestCase):
    def setUp(self):
        self._saved = (km._sessions, km._sdk, km._codex, km._captions)
        km._sessions = lambda now: []                 # discovery can't see it (no transcript yet)
        km._sdk = lambda: None                        # not SDK-owned — the Codex boot window
        self.cx = _FakeCodex()
        km._codex = lambda: self.cx
        km._captions = lambda sid: {}

    def tearDown(self):
        km._sessions, km._sdk, km._codex, km._captions = self._saved

    def test_live_codex_sid_builds_a_frame_with_its_echo_before_any_transcript(self):
        now = int(time.time())
        self.cx.echo("first message into a booting session", now)
        m = km.build_session(SID, now, live_map={SID: _TM_META})
        self.assertIsNotNone(m, "a live-but-transcriptless session must still build a frame")
        self.assertEqual(m["id"], SID)
        evs = m.get("events") or []
        self.assertTrue(any("first message into a booting session" in str(e) for e in evs),
                        "the input echo renders onto the synthesized frame")

    def test_a_sid_nowhere_alive_still_builds_nothing(self):
        m = km.build_session(SID, int(time.time()), live_map={})
        self.assertIsNone(m, "unknown sids stay frameless — the stub is only for LIVE boot windows")

    def test_the_stub_path_can_never_shadow_a_real_transcript(self):
        # the sentinel lives under STATE/boot-stub/, a directory nothing ever writes
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('str(jd.STATE / "boot-stub" / (sid + ".jsonl"))', src)


if __name__ == "__main__":
    unittest.main()
