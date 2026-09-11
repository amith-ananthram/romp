#!/usr/bin/env python3
"""T336: a focus frame that names an anchor carries the anchor turn's OWN moment (`anchorEventT`), resolved from the
session's built chat events, for the chat's reveal progress line. A card's `t` is the card's newest activity, later
than the turn its anchorUuid names, so a fraction of the way back computed over it would read more progress than
exists; the turn's own time is the authoritative source and the kernel is where it lives. Nothing resolving means
None (the chat then counts instead of guessing), never an exception. Synthetic fixtures only."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from romp_load import load_source  # noqa: E402

BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs conftest's
# floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_anchor_event_t", os.path.join(BIN, "romp-kernel"))

SID = "88888888-aaaa-4bbb-8ccc-000000000336"          # this module's private synthetic sid
U_TURN = "11111111-2222-3333-4444-000000000005"
U_POSTAL = "11111111-2222-3333-4444-000000000009"
EVENTS = [{"kind": "user", "uuid": U_TURN, "ts": "2026-09-09T10:00:00.000Z"},
          {"kind": "assistant", "uuid": "11111111-2222-3333-4444-000000000006", "ts": "2026-09-09T10:00:40.000Z"},
          {"kind": "postal-service", "uuid": U_POSTAL, "t": 1_757_500_000},
          {"kind": "notice", "uuid": "11111111-2222-3333-4444-000000000007"}]


class AnchorEventT(unittest.TestCase):
    def setUp(self):
        self._build = km.build_session
        self.calls = []

        def fake_build(sid, now, *a, **kw):
            self.calls.append(sid)
            return {"events": list(EVENTS)} if sid == SID else None
        km.build_session = fake_build

    def tearDown(self):
        km.build_session = self._build

    def test_a_turns_moment_is_its_ts_parsed_to_epoch_seconds(self):
        self.assertEqual(km._anchor_event_t(SID, U_TURN), int(km.em.parse_z("2026-09-09T10:00:00.000Z")))
        self.assertEqual(self.calls, [SID], "read from the session's built events (cache-backed)")

    def test_a_postal_cards_moment_is_its_t(self):
        self.assertEqual(km._anchor_event_t(SID, U_POSTAL), 1_757_500_000)

    def test_nothing_resolving_is_none_never_an_exception(self):
        self.assertIsNone(km._anchor_event_t(SID, "11111111-2222-3333-4444-000000000007"), "an event with no time")
        self.assertIsNone(km._anchor_event_t(SID, "11111111-2222-3333-4444-ffffffffffff"), "no such uuid")
        self.assertIsNone(km._anchor_event_t("no-such-session", U_TURN), "no such session")
        self.assertIsNone(km._anchor_event_t(SID, None), "no anchor")
        self.assertIsNone(km._anchor_event_t(None, U_TURN), "no session named")
        km.build_session = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertIsNone(km._anchor_event_t(SID, U_TURN), "a failing build resolves nothing, loudly nowhere: the chat counts")

    def test_the_dependency_scope_is_reset_after_the_read(self):
        km._chat_dep_scope.deps = {"stale": True}
        km._anchor_event_t(SID, U_TURN)
        self.assertIsNone(km._chat_dep_scope.deps, "a read for one focus frame caches nothing (the history slices' rule)")

    def test_a_feed_cards_focus_carries_the_turns_moment_beside_the_cards_time(self):
        f = km._show_on_timeline_focus({"sid": SID, "t": 1_757_600_000, "anchor": "prompt", "anchorUuid": U_TURN})
        self.assertEqual(f["anchor"], U_TURN)
        self.assertEqual(f["anchorT"], 1_757_600_000, "the card's time stays: the kind gate and the time-only landing read it")
        self.assertEqual(f["anchorEventT"], int(km.em.parse_z("2026-09-09T10:00:00.000Z")), "the turn's own moment rides beside it")
        f2 = km._show_on_timeline_focus({"sid": SID, "t": 1_757_600_000, "anchor": "prompt", "anchorUuid": None})
        self.assertIsNone(f2["anchorEventT"], "no anchor, no moment")

    def test_a_deep_links_focus_carries_it_too(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"anchorEventT": _anchor_event_t(msg["session"], msg.get("anchor"))', src,
                      "the deepLink op's focus frame resolves the anchor turn's moment the same way")


if __name__ == "__main__":
    unittest.main()
