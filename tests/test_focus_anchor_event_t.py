#!/usr/bin/env python3
"""T336: a LIVE session's anchored focus frame carries the anchor turn's OWN moment (`anchorEventT`) for the chat's
reveal progress line. A card's `t` is the card's newest activity, later than the turn its anchorUuid names, so a
fraction of the way back computed over it would read more progress than exists. The moment is resolved in
_reveal_or_confirm's live branch and only there (review: resolving it eagerly as the caller's argument ran a whole
build for a dead session's card before the liveness check), from what is already in hand and never a build: the
pusher's built payload first (matched by the four selectors the chat resolves an anchor by: uuid, a postal message id
in mid or mids, an answered question's resultUuid, a settled group's settleUuids), then the cached parse's atoms by
uuid. Nothing resolving means None (the chat counts instead of guessing), never an exception. Synthetic fixtures."""
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
U_REPLY = "11111111-2222-3333-4444-000000000006"
U_POSTAL = "11111111-2222-3333-4444-000000000009"
MID = "1789000000.000001_0123456789abcdef0123456789abcdef.TESTHOST"
MID2 = "1789000000.000002_0123456789abcdef0123456789abcdef.TESTHOST"
U_ANSWER = "11111111-2222-3333-4444-00000000000a"
U_SETTLED = "11111111-2222-3333-4444-00000000000b"
TS_TURN = "2026-09-09T10:00:00.000Z"
T_TURN = int(km.em.parse_z(TS_TURN))
EVENTS = [{"kind": "user", "uuid": U_TURN, "ts": TS_TURN},
          {"kind": "assistant", "uuid": U_REPLY, "ts": "2026-09-09T10:00:40.000Z", "settleUuids": [U_SETTLED]},
          {"kind": "postal-service", "uuid": U_POSTAL, "t": 1_757_500_000, "mid": MID, "mids": [MID, MID2]},
          {"kind": "tool", "uuid": "11111111-2222-3333-4444-000000000007", "ts": "2026-09-09T10:01:00.000Z", "resultUuid": U_ANSWER},
          {"kind": "notice", "uuid": "11111111-2222-3333-4444-000000000008"}]
PARSED = {"turns": [{"id": "t1", "atoms": [{"uuid": U_TURN, "t": T_TURN + 5}, {"uuid": "11111111-2222-3333-4444-0000000000aa", "t": None}]}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = {n: getattr(km, n) for n in ("build_session", "_built_chat", "_sessions", "_parse", "_tmux_sessions", "_reveal_chat_for", "_name_of")}
        km.build_session = lambda *a, **kw: self.fail("build_session must never run for a focus frame's anchor moment")
        km._built_chat = {}
        km._sessions = lambda now: []
        km._parse = lambda path, sid, now: self.fail("no parse expected here")
        self.sent = []
        km._reveal_chat_for = lambda client, msg: self.sent.append(msg)
        km._name_of = lambda sid: "web"

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(km, n, v)


class FromTheBuiltPayload(Base):
    def setUp(self):
        super().setUp()
        km._built_chat = {SID: (("sig",), {"events": list(EVENTS)}, "", None)}

    def test_a_turns_moment_is_its_ts_parsed_to_epoch_seconds(self):
        self.assertEqual(km._anchor_event_t(SID, U_TURN), T_TURN)

    def test_a_postal_cards_moment_by_its_uuid_or_its_message_ids(self):
        self.assertEqual(km._anchor_event_t(SID, U_POSTAL), 1_757_500_000, "a postal event's t")
        self.assertEqual(km._anchor_event_t(SID, MID), 1_757_500_000, "the message id the timeline connector deep-links by")
        self.assertEqual(km._anchor_event_t(SID, MID2), 1_757_500_000, "an unhydrated turn's ids (data-mids)")

    def test_an_answered_question_and_a_settled_group_resolve_by_their_other_uuids(self):
        self.assertEqual(km._anchor_event_t(SID, U_ANSWER), int(km.em.parse_z("2026-09-09T10:01:00.000Z")), "resultUuid: the answer line the timeline anchors")
        self.assertEqual(km._anchor_event_t(SID, U_SETTLED), int(km.em.parse_z("2026-09-09T10:00:40.000Z")), "settleUuids: a settled constituent")

    def test_nothing_resolving_is_none_never_an_exception(self):
        self.assertIsNone(km._anchor_event_t(SID, "11111111-2222-3333-4444-000000000008"), "an event with no time")
        self.assertIsNone(km._anchor_event_t(SID, None), "no anchor")
        self.assertIsNone(km._anchor_event_t(None, U_TURN), "no session named")
        km._built_chat = {SID: (("sig",), {"events": [{"kind": "user", "uuid": U_TURN, "ts": "not a time"}]}, "", None)}
        km._sessions = lambda now: [{"sid": SID, "path": "/nonexistent"}]
        km._parse = lambda path, sid, now: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertIsNone(km._anchor_event_t(SID, U_TURN), "a failing parse resolves nothing, raises nowhere")


class FromTheCachedParse(Base):
    def test_a_session_with_no_built_payload_reads_the_parses_atoms_by_uuid(self):
        km._sessions = lambda now: [{"sid": SID, "path": "/synthetic/leaf.jsonl"}]
        calls = []
        km._parse = lambda path, sid, now: (calls.append((path, sid)), PARSED)[1]
        self.assertEqual(km._anchor_event_t(SID, U_TURN, now=1_757_600_000), T_TURN + 5)
        self.assertEqual(calls, [("/synthetic/leaf.jsonl", SID)], "the cached parse, keyed the way every other reader keys it")
        self.assertIsNone(km._anchor_event_t(SID, "11111111-2222-3333-4444-0000000000aa", now=1_757_600_000), "an atom with no time")
        self.assertIsNone(km._anchor_event_t(SID, MID, now=1_757_600_000), "a postal id is not in the parse: the chat counts")

    def test_an_unknown_session_reads_nothing(self):
        km._sessions = lambda now: []
        self.assertIsNone(km._anchor_event_t("no-such-session", U_TURN))


class OnTheLiveBranchOnly(Base):
    def setUp(self):
        super().setUp()
        km._built_chat = {SID: (("sig",), {"events": list(EVENTS)}, "", None)}

    def test_a_live_sessions_anchored_focus_carries_the_moment(self):
        km._tmux_sessions = lambda: {SID: {}}
        km._reveal_or_confirm(SID, {"type": "focus", "id": SID, "anchor": U_TURN, "anchorT": 1_757_600_000, "anchorKind": None}, client={"wid": "w1"})
        self.assertEqual(len(self.sent), 1)
        f = self.sent[0]
        self.assertEqual(f["type"], "focus")
        self.assertEqual(f["anchorT"], 1_757_600_000, "the card's time stays: the kind gate and the time-only landing read it")
        self.assertEqual(f["anchorEventT"], T_TURN, "the turn's own moment rides beside it")

    def test_a_dead_sessions_card_pays_nothing_and_gets_the_confirm_without_it(self):
        km._tmux_sessions = lambda: {}
        km._built_chat = {}
        km._sessions = lambda now: self.fail("no session listing for a dead session's confirm")
        km._reveal_or_confirm(SID, {"type": "focus", "id": SID, "anchor": U_TURN, "anchorT": 1_757_600_000}, client={"wid": "w1"})
        self.assertEqual(self.sent, [{"type": "confirmRevive", "id": SID, "name": "web"}])

    def test_a_focus_with_no_anchor_and_a_frame_that_already_carries_one_are_left_alone(self):
        km._tmux_sessions = lambda: {SID: {}}
        km._reveal_or_confirm(SID, {"type": "focus", "id": SID}, client={"wid": "w1"})
        self.assertEqual(self.sent[-1], {"type": "focus", "id": SID}, "no anchor, no moment, no key")
        km._reveal_or_confirm(SID, {"type": "focus", "id": SID, "anchor": U_TURN, "anchorEventT": 7}, client={"wid": "w1"})
        self.assertEqual(self.sent[-1]["anchorEventT"], 7, "a caller's own value stands")

    def test_the_callers_build_their_frames_without_the_moment(self):
        f = km._show_on_timeline_focus({"sid": SID, "t": 1_757_600_000, "anchor": "prompt", "anchorUuid": U_TURN})
        self.assertNotIn("anchorEventT", f, "resolved on the live branch, not by the caller")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertNotIn("_anchor_event_t(msg[", src, "no caller resolves it eagerly as an argument")
        fn = src.split("def _anchor_event_t(")[1].split("\ndef ")[0]
        self.assertNotIn("build_session(", fn, "never a build (the docstring names the one it replaced)")


if __name__ == "__main__":
    unittest.main()
