#!/usr/bin/env python3
"""EVERY context-carrying reply flips its feed card to Working instantly (the user 2026-07-20). The feed
composer's own follow-up was already optimistic, but the same reply fired from anywhere else — the chat's
citation-chip follow-up, a picker/permission ANSWER typed in the chat, another feed view's button — waited
out the kernel rebuild+push round trip. Now _drive fans a tiny "cardPredict" frame to every FEED client the
instant the op arrives (_predict_working — the hover-glow fan-back pattern); the client runs its existing
optimistic machinery and the authoritative push reconciles it. An ANSWER names no card, so the helper
resolves sid → the live-blocked card(s) from the LAST BUILT feed payload (pre-answer by definition: exactly
the cards about to unblock); apiError floors are excluded — an answer doesn't lift those. SYNTHETIC
fixtures only."""
import os
import json
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
km = load_source("romp_kernel_cardpredict", os.path.join(BIN, "romp-kernel"))

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

SID = "11111111-2222-3333-4444-555555555555"
PEER = "66666666-7777-8888-9999-000000000000"


def _ask(item_id, sid, column, blocked=None):
    return {"itemId": item_id, "sid": sid, "column": column, "blocked": blocked}


class _FakeBackend:
    def __init__(self):
        self.calls = []
        self.ask = None          # the live ask current_ask() reports (with optional {"progress": {"i","n"}})

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True

    def on_ask(self, sid, action, target=None):
        self.calls.append(("on_ask", action, target))
        return True

    def current_ask(self, sid):
        return self.ask


class PredictWorkingFanOut(unittest.TestCase):
    def setUp(self):
        self.frames = []
        self.be = _FakeBackend()
        self._saved_name_of = km._name_of
        self._saved = (km._send_to_app, list(km._built_feed), km.Sessions.backend_for,
                       km._send_or_park, km.jd.optimistic_followup)
        km._send_to_app = lambda app, m: self.frames.append((app, m))
        km._name_of = lambda sid: "web"   # these tests drive ops on a session this kernel HAS; _drive refuses one it doesn't (2026-07-29)
        km.Sessions.backend_for = lambda sid: self.be
        km._send_or_park = lambda *a, **k: None
        km.jd.optimistic_followup = lambda *a, **k: False
        # the last-built feed payload: the pre-answer map of which card the live floor sits on
        km._built_feed[:] = [None, {"type": "feed", "asks": [
            _ask(SID + ":g1", SID, "needs_input", {"state": "picker", "what": "…"}),
            _ask(SID + ":g2", SID, "needs_input", {"state": "permission", "what": "…"}),
            _ask(SID + ":g3", SID, "needs_input", {"state": "apiError", "what": "…"}),   # an answer lifts no API error
            _ask(SID + ":g4", SID, "working"),                                           # not blocked at all
            _ask(PEER + ":g9", PEER, "needs_input", {"state": "picker", "what": "…"}),   # someone else's block
        ]}, 0.0]

    def tearDown(self):
        (km._send_to_app, built, km.Sessions.backend_for,
         km._send_or_park, km.jd.optimistic_followup) = self._saved
        km._name_of = self._saved_name_of
        km._built_feed[:] = built

    def _feed_frames(self):
        return [m for app, m in self.frames if app == "feed" and m.get("type") == "cardPredict"]

    def test_answer_resolves_sid_to_its_live_blocked_cards_only(self):
        km._predict_working("answer", sid=SID)
        fr = self._feed_frames()
        self.assertEqual(len(fr), 1)
        self.assertEqual(fr[0]["flavor"], "answer")
        # picker + permission floors of THIS session; never the apiError card, a working card, or a peer's
        self.assertEqual(sorted(fr[0]["ids"]), [SID + ":g1", SID + ":g2"])

    def test_no_live_blocked_card_means_no_frame_at_all(self):
        km._predict_working("answer", sid=PEER.replace("6", "5"))
        self.assertEqual(self._feed_frames(), [])

    def test_explicit_ids_fan_verbatim(self):
        km._predict_working("followup", ids=[SID + ":g7"])
        fr = self._feed_frames()
        self.assertEqual(len(fr), 1)
        self.assertEqual((fr[0]["flavor"], fr[0]["ids"]), ("followup", [SID + ":g7"]))

    def test_every_answer_shaped_drive_op_fans_and_cancel_does_not(self):
        client = {"send": lambda s: None}
        for op in ({"type": "answerAsk", "id": SID, "target": 0},
                   {"type": "submitAsk", "id": SID},
                   {"type": "addCustomAsk", "id": SID, "text": "my own answer"},
                   {"type": "askText", "id": SID, "text": "typed reply"}):
            self.frames.clear()
            self.assertTrue(km._drive(op, client))
            fr = self._feed_frames()
            self.assertEqual(len(fr), 1, op["type"])
            self.assertEqual(fr[0]["flavor"], "answer", op["type"])
        # a CANCEL answers nothing — the session is still waiting on you, so no Working prediction
        self.frames.clear()
        self.assertTrue(km._drive({"type": "cancelAsk", "id": SID}, client))
        self.assertEqual(self._feed_frames(), [])

    def test_multi_question_picker_suppresses_the_flip_until_the_final_answer(self):
        # one AskUserQuestion with N questions holds the session in `picker` the whole time (the user
        # 2026-07-21): a non-final sub-answer must NOT flip the card to Working, or it bounces out and back
        # on every question. Event-based: the live ask carries "question i of n".
        client = {"send": lambda s: None}
        answer_ops = ({"type": "answerAsk", "id": SID, "target": 0},
                      {"type": "submitAsk", "id": SID},
                      {"type": "addCustomAsk", "id": SID, "text": "mine"},
                      {"type": "askText", "id": SID, "text": "typed"})
        # question 1 of 3 and 2 of 3 → mid-series → NO prediction
        for i in (1, 2):
            self.be.ask = {"progress": {"i": i, "n": 3}}
            for op in answer_ops:
                self.frames.clear()
                self.assertTrue(km._drive(op, client))
                self.assertEqual(self._feed_frames(), [], "i=%d of 3, %s should not flip" % (i, op["type"]))
        # question 3 of 3 → the last answer DOES flip (the set is done, the session is really going to work)
        self.be.ask = {"progress": {"i": 3, "n": 3}}
        for op in answer_ops:
            self.frames.clear()
            self.assertTrue(km._drive(op, client))
            self.assertEqual(len(self._feed_frames()), 1, "final answer %s should flip" % op["type"])
        # a single-question ask (no progress) always flips — no regression
        self.be.ask = {"someOtherField": True}
        self.frames.clear()
        self.assertTrue(km._drive({"type": "answerAsk", "id": SID, "target": 0}, client))
        self.assertEqual(len(self._feed_frames()), 1)

    def test_picker_mid_series_helper_reads_i_of_n(self):
        self.be.ask = {"progress": {"i": 1, "n": 2}}
        self.assertTrue(km._picker_mid_series(SID))
        self.be.ask = {"progress": {"i": 2, "n": 2}}
        self.assertFalse(km._picker_mid_series(SID))
        self.be.ask = None                       # no live ask → not mid-series
        self.assertFalse(km._picker_mid_series(SID))
        self.be.ask = {"progress": {}}           # malformed progress → treated as single
        self.assertFalse(km._picker_mid_series(SID))

    def test_followup_fans_its_named_card_and_cardmove_is_gone(self):
        client = {"send": lambda s: None}
        self.assertTrue(km._drive({"type": "askFollowUp", "itemId": SID + ":g1", "text": "hi"}, client))
        fr = self._feed_frames()
        self.assertEqual(len(fr), 1)
        self.assertEqual((fr[0]["flavor"], fr[0]["ids"]), ("followup", [SID + ":g1"]))
        self.frames.clear()
        # cardMove (the messageless Move to Working) was REMOVED (the user 2026-07-25): the op is no
        # longer a drive op, routes nowhere, and fans no prediction.
        self.assertFalse(km._drive({"type": "cardMove", "itemId": SID + ":g2", "to": "working"}, client))
        self.assertEqual(self._feed_frames(), [])



class SwallowedAnswerNeverLooksLikeProgress(PredictWorkingFanOut):
    """T214 (verified live 2026-09-01): an answer flushed across a kernel restart found no waiting
    ask — on_ask returned False (sid not respawned) or resolve_ask silently no-opped — yet the
    handler predicted Working unconditionally and told no one. The delivery outcome is honored now:
    a False flips nothing and surfaces loudly; a True keeps the exact old behavior."""

    def test_red_repro_a_swallowed_answer_flips_nothing_and_surfaces(self):
        self.be.on_ask = lambda sid, action, target=None: False   # the ask died with the old kernel
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s))}
        for op in ({"type": "answerAsk", "id": SID, "target": 0},
                   {"type": "submitAsk", "id": SID},
                   {"type": "addCustomAsk", "id": SID, "text": "mine"},
                   {"type": "askText", "id": SID, "text": "typed"}):
            self.frames.clear(); sent.clear()
            km._drive(op, client)
            self.assertEqual(self._feed_frames(), [],
                             "%s: a swallowed answer must never look like progress" % op["type"])
            lost = [m for m in sent if m.get("type") == "askLost" and m.get("id") == SID]
            self.assertEqual(len(lost), 1, "%s: the swallow must surface loudly to the asker" % op["type"])
            self.assertIn("didn't reach", lost[0].get("text", ""))

    def test_regression_a_delivered_answer_still_flips_and_stays_quiet(self):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s))}
        km._drive({"type": "answerAsk", "id": SID, "target": 0}, client)
        self.assertEqual(len(self._feed_frames()), 1, "the delivered answer flips exactly as before")
        self.assertEqual([m for m in sent if m.get("type") == "askLost"], [], "…with no false alarm")


if __name__ == "__main__":
    unittest.main()
