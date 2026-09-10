"""An EMPTY chat build never blanks a session the clients hold WITH content (T249b, the user 2026-09-07).

build_session parses whatever path the session resolves to; a path unreadable for one pusher cycle — a resumed
SDK session whose registry already names its new leaf while the CLI has not created the file, a transcript moved
aside — parses to ZERO events, and that build went out as a full {type:"session"} frame with events: []. The pane
took it as the new transcript (a placeholder flash), and the content frame a cycle later read as a FIRST build
that re-landed the reader through the full-show route: the recorded scroll snap (T249). Behavioural pins on the
pusher and the targeted push, with build_session stubbed to return controlled frames; synthetic ids only.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from romp_load import load_source
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_emptybuild", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-4333-8444-000000000249"
NOW = 1781300000


def _tm():
    return {"state": "waiting", "color": "#888888", "since": NOW - 60, "model": "", "effort": "",
            "context": None, "backend": "tmux"}


def _ev(i):
    return {"kind": "assistant", "uuid": "11111111-2222-4333-8444-00000000a%03d" % i, "md": "reply %d" % i}


def _frame(events):
    return {"type": "session", "id": SID, "name": "web", "color": None, "events": list(events),
            "status": {"state": "ready"}, "ledger": None}


class _Pusher(unittest.TestCase):
    """One chat client, one session; build_session answers from a script of frames, one per cycle."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, SID + ".jsonl")
        with open(self.path, "w") as f:
            f.write("{}\n")                                # stat-able: the build cache keys on it
        self.sess = {"sid": SID, "name": "web", "anchor": None, "path": self.path, "mtime": NOW}
        self.tmux = {SID: _tm()}
        self.sent = []
        self.builds = []
        self.client = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: None}
        km._prev_chat_events.pop(SID, None)
        km._prev_chat_ledger.pop(SID, None)
        km._built_chat.pop(SID, None)
        km._EMPTY_BUILD_NOTED.discard(SID)

    def tearDown(self):
        km._prev_chat_events.pop(SID, None)
        km._built_chat.pop(SID, None)
        km._EMPTY_BUILD_NOTED.discard(SID)
        self.td.cleanup()

    def _cycle(self, events, stderr=None):
        """One pusher cycle whose build returns `events`; returns the ("chat", SID) frames it sent."""
        self.sent.clear()
        os.utime(self.path, (NOW, NOW + len(self.builds)))   # every cycle's transcript stat differs → a rebuild
        self.builds.append(events)
        frame = _frame(events)
        with mock.patch.object(km, "_alive_sessions", lambda now, tm: [dict(self.sess)]), \
                mock.patch.object(km, "_chat_tab_sessions", lambda now, tm: [dict(self.sess)]), \
                mock.patch.object(km, "_warm_fleet_bg", lambda now: None), \
                mock.patch.object(km, "_tmux_sessions", lambda: dict(self.tmux)), \
                mock.patch.object(km, "build_session", lambda sid, now, tmux=None, **kw: dict(frame)), \
                mock.patch.object(km, "_cached_feed", lambda *a, **k: {"working": [], "awaiting": [], "asks": []}), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, sig=None, kind="full": self.sent.append((key, msg))), \
                mock.patch.object(sys, "stderr", stderr if stderr is not None else io.StringIO()):
            km._push([self.client], tmux=self.tmux)
        return [m for k, m in self.sent if k == ("chat", SID)]

    def test_an_empty_build_after_content_sends_nothing_and_the_next_content_is_a_tail(self):
        first = self._cycle([_ev(1), _ev(2)])
        self.assertEqual([m["type"] for m in first], ["session"], "premise: the fresh client gets the full session")
        self.assertEqual(len(first[0]["events"]), 2)
        err = io.StringIO()
        empty = self._cycle([], stderr=err)
        self.assertEqual([m for m in empty if m.get("type") == "session" and not m.get("events")], [],
                         "an EMPTY build for a session the client holds with content is never sent as a wipe")
        self.assertIn("came back EMPTY", err.getvalue(), "…and the failed read is said on stderr")
        # the baseline stood: the content frame that follows is an append onto what the client holds, not a
        # full frame the pane would take as a first build (the full-show route that re-lands the reader)
        back = self._cycle([_ev(1), _ev(2), _ev(3)])
        self.assertEqual([m["type"] for m in back], ["chatTail"], "content returns as a tail, never a first build")
        self.assertEqual(back[0]["from"], 2)

    def test_the_note_is_said_once_per_episode_and_re_arms_after_content_returns(self):
        self._cycle([_ev(1)])
        err = io.StringIO()
        self._cycle([], stderr=err); self._cycle([], stderr=err)
        self.assertEqual(err.getvalue().count("came back EMPTY"), 1, "one line per episode, not per cycle")
        self._cycle([_ev(1), _ev(2)])
        err2 = io.StringIO()
        self._cycle([], stderr=err2)
        self.assertEqual(err2.getvalue().count("came back EMPTY"), 1, "a new episode after content returned is said again")

    def test_a_session_that_never_had_content_is_untouched(self):
        empty = self._cycle([])
        self.assertEqual([m["type"] for m in empty], ["session"], "a genuinely empty session still gets its frame")
        self.assertEqual(empty[0]["events"], [])

    def test_the_targeted_push_skips_an_empty_build_too(self):
        self._cycle([_ev(1), _ev(2)])
        self.sent.clear()
        with mock.patch.object(km, "_clients", [self.client]), \
                mock.patch.object(km, "_tmux_sessions", lambda: dict(self.tmux)), \
                mock.patch.object(km, "_chat_tab_sessions", lambda now, tm: [dict(self.sess)]), \
                mock.patch.object(km, "build_session", lambda sid, now, tmux=None, **kw: _frame([])), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, sig=None, kind="full": self.sent.append((key, msg))), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            km._push_session_now(SID)
        self.assertEqual([m for k, m in self.sent if k == ("chat", SID)], [],
                         "the targeted push never sends an emptied frame either")


class Decision(unittest.TestCase):
    def test_empty_build_regresses(self):
        self.assertTrue(km._empty_build_regresses(_frame([]), [_ev(1)]))
        self.assertFalse(km._empty_build_regresses(_frame([]), []), "nothing held → a genuinely empty session")
        self.assertFalse(km._empty_build_regresses(_frame([]), None))
        self.assertFalse(km._empty_build_regresses(_frame([_ev(1)]), [_ev(1), _ev(2)]), "fewer events is a change, not a failed read")


if __name__ == "__main__":
    unittest.main()
