#!/usr/bin/env python3
"""A chat rail CLICK navigates the other two panes (the user 2026-07-23).

`dotOpen` was emitted by the chat rail and consumed by NOBODY: the kernel had no handler, so in the
browser the click was inert, and in VS Code the host only revealed the feed pane without selecting
anything in it. Hovering already resolved the right cards server-side, so the click now reuses exactly
that resolution and turns it into movement: the timeline pans + pulses, the feed scrolls + pulses.

These drive the kernel's real message dispatch with a stubbed _send_to_app, so a handler that stops
firing (or stops addressing one of the two panes) fails here.

Synthetic only — placeholder uuids, hostname-free ids, no session store required.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model_dotopen", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge_dotopen", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_dotopen", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SEG = SID + ":s3"
GOAL = SID + ":g227"


class DotOpenNavigates(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._real_send = km._send_to_app
        self._real_seg = km._segment_of_uuid
        self._real_cards = km._cards_for_segments
        km._send_to_app = lambda app, msg: self.sent.append((app, msg))

    def tearDown(self):
        km._send_to_app = self._real_send
        km._segment_of_uuid = self._real_seg
        km._cards_for_segments = self._real_cards

    def _dispatch(self, msg):
        """Run one client message through the kernel's REAL dispatch. _dispatch_ws is a Handler method
        but touches no instance state on this path, so an unbound call keeps the test off the socket."""
        km.Handler._dispatch_ws(None, msg, {"app": "chat", "active": None})

    def _by_app(self, app):
        return [m for a, m in self.sent if a == app]

    def test_a_click_moves_both_other_panes(self):
        km._segment_of_uuid = lambda sid, uuid, now: (SEG, [UUID])
        km._cards_for_segments = lambda sid, segs: [GOAL]
        self._dispatch({"type": "dotOpen", "sid": SID, "uuid": UUID, "t": 1700, "tlId": "g7"})
        feed = self._by_app("feed")
        self.assertEqual([m["type"] for m in feed], ["revealCards"])
        self.assertEqual(feed[0]["keys"], [GOAL], "the cards covering that turn")
        tl = self._by_app("timeline")
        self.assertEqual([m["type"] for m in tl], ["revealEvent"])
        self.assertEqual((tl[0]["sid"], tl[0]["t"], tl[0]["id"]), (SID, 1700, "g7"),
                         "tlId names the exact glyph; t is the fallback")

    def test_the_click_resolves_cards_the_same_way_the_hover_does(self):
        # A click must land on exactly what the hover just lit, or the two read as different features.
        seen = []
        km._segment_of_uuid = lambda sid, uuid, now: (SEG, [UUID])
        km._cards_for_segments = lambda sid, segs: seen.append((sid, list(segs))) or [GOAL]
        self._dispatch({"type": "dotHover", "sid": SID, "uuid": UUID, "t": 1700})
        self._dispatch({"type": "dotOpen", "sid": SID, "uuid": UUID, "t": 1700})
        self.assertEqual(seen[0], seen[1], "hover and click ask the same question of the same segment")

    def test_a_click_on_a_turn_with_no_resolvable_segment_still_moves_the_timeline(self):
        # No goal covers this turn. The feed has nothing to scroll to, but the timeline still can, and
        # a click that silently did nothing at all would read as broken.
        km._segment_of_uuid = lambda sid, uuid, now: (None, [])
        self._dispatch({"type": "dotOpen", "sid": SID, "uuid": UUID, "t": 1700, "tlId": "g7"})
        self.assertEqual(self._by_app("feed")[0]["keys"], [], "no cards, and it says so rather than stale ones")
        self.assertEqual(self._by_app("timeline")[0]["type"], "revealEvent")

    def test_a_malformed_click_is_survivable(self):
        km._segment_of_uuid = lambda sid, uuid, now: (None, [])
        self._dispatch({"type": "dotOpen"})          # no sid/uuid/t at all
        self.assertEqual(self._by_app("feed")[0]["keys"], [])


if __name__ == "__main__":
    unittest.main()
