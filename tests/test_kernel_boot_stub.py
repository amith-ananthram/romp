#!/usr/bin/env python3
"""A brand-new LIVE tmux session has no transcript for its first ~7s, and build_session used to return
None for it — no session frame existed, so its input echo / queued bubble had nothing to render onto and
the first message typed into a just-created session was invisible until the transcript appeared (the
user 2026-07-20: the UI must respond even when the kernel can't get the session going yet). Discovery
misses now synthesize a transcriptless entry for a sid that is LIVE in tmux — the same treatment the SDK
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

# the shape _tmux_sessions() always provides for a live session (a booting TUI has no model/context yet)
_TM_META = {"state": "", "since": None, "model": "", "effort": "", "mode": "",
            "context": None, "compactPct": None, "backend": "tmux"}


class BootWindowStub(unittest.TestCase):
    def setUp(self):
        self._saved = (km._sessions, km._sdk, km._captions)
        km._sessions = lambda now: []                 # discovery can't see it (no transcript yet)
        km._sdk = lambda: None                        # not SDK-owned either — the tmux boot window
        km._captions = lambda sid: {}
        km._tmux_echo.pop(SID, None)

    def tearDown(self):
        km._sessions, km._sdk, km._captions = self._saved
        km._tmux_echo.pop(SID, None)

    def test_live_tmux_sid_builds_a_frame_with_its_echo_before_any_transcript(self):
        now = int(time.time())
        km._tmux_echo_add(SID, "first message into a booting session")
        m = km.build_session(SID, now, tmux={SID: _TM_META})
        self.assertIsNotNone(m, "a live-but-transcriptless session must still build a frame")
        self.assertEqual(m["id"], SID)
        evs = m.get("events") or []
        self.assertTrue(any("first message into a booting session" in str(e) for e in evs),
                        "the input echo renders onto the synthesized frame")

    def test_a_sid_nowhere_alive_still_builds_nothing(self):
        m = km.build_session(SID, int(time.time()), tmux={})
        self.assertIsNone(m, "unknown sids stay frameless — the stub is only for LIVE boot windows")

    def test_the_stub_path_can_never_shadow_a_real_transcript(self):
        # the sentinel lives under STATE/boot-stub/, a directory nothing ever writes
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('str(jd.STATE / "boot-stub" / (sid + ".jsonl"))', src)


if __name__ == "__main__":
    unittest.main()
