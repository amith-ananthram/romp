#!/usr/bin/env python3
"""A romp-injected message that arrives BUNDLED must still retire its optimistic echo (the user
2026-07-22, who saw a nudge stamped 16:43 sitting BELOW a 17:00 answer in the thread).

romp bundles what it injects: a kernel-restart notice, a background-task death notice and an auto-nudge
land as THREE text blocks inside ONE user record. The optimistic echo, though, holds only the nudge body.
_atom_user_text space-JOINS a record's blocks into a single string, so the echo's exact text was never a
member of the transcript text set and prune_live's landing check (`et in tx_user_texts`) could not fire.
Nor could the FIFO floor: it was narrowed on 2026-07-20 to PATH-BEARING echoes only, so a genuinely
dropped send stays visible. With neither path available the echo never retired — it rode the bottom of
the thread indefinitely, still wearing its original SEND time, which is exactly the out-of-order nudge.

_atom_user_texts exposes the joined text AND each block, matched EXACTLY (never a substring test), so a
bundled block retires the echo it came from and nothing else. SYNTHETIC fixtures only.
"""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_bundled_echo", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"

RESTART = "<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted and cut this session's turn."
DEATH = ("<!-- romp-injected --><!-- romp-system -->[romp] 1 background task you had running was cut off "
         "when the claude process that started it ended.")
NUDGE = ("> Earlier note about the capture run.\n\nWhere does this stand?\n\n"
         "<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: %s:g360 -->" % SID)


def _user_atom(blocks, t=1000):
    """A user atom whose message content is a LIST of text blocks (the bundled shape)."""
    return {"type": "user", "uuid": "u1", "session_id": SID, "t": t, "author": "romp",
            "message": {"role": "user", "content": [{"type": "text", "text": b} for b in blocks]}}


class BundledUserText(unittest.TestCase):
    def test_a_bundled_record_exposes_every_block(self):
        got = set(km._atom_user_texts(_user_atom([RESTART, DEATH, NUDGE])))
        self.assertIn(NUDGE, got, "the nudge body must be matchable on its own — this is the bug")
        self.assertIn(RESTART, got)
        self.assertIn(DEATH, got)

    def test_the_joined_text_is_still_offered(self):
        # unchanged behaviour for anything that matched the whole record before
        joined = km._atom_user_text(_user_atom([RESTART, DEATH, NUDGE]))
        self.assertIn(joined, set(km._atom_user_texts(_user_atom([RESTART, DEATH, NUDGE]))))

    def test_the_old_joined_only_rule_could_not_match_the_nudge(self):
        # pins WHY the echo survived: the joined string is not the echoed body
        self.assertNotEqual(km._atom_user_text(_user_atom([RESTART, DEATH, NUDGE])), NUDGE)

    def test_a_single_block_record_is_unchanged(self):
        got = set(km._atom_user_texts(_user_atom([NUDGE])))
        self.assertEqual(got, {NUDGE}, "one block → one text, no duplicate entry")

    def test_a_plain_string_record_is_unchanged(self):
        a = {"type": "user", "uuid": "u2", "session_id": SID, "t": 1,
             "message": {"role": "user", "content": NUDGE}}
        self.assertEqual(set(km._atom_user_texts(a)), {NUDGE})

    def test_a_non_user_atom_yields_nothing(self):
        a = {"type": "assistant", "uuid": "a1", "session_id": SID, "t": 1,
             "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}
        self.assertEqual(km._atom_user_texts(a), ())

    def test_blank_blocks_are_dropped(self):
        got = set(km._atom_user_texts(_user_atom([NUDGE, "   "])))
        self.assertEqual(got, {NUDGE}, "whitespace-only blocks are not matchable texts")


class EchoRetires(unittest.TestCase):
    """The end-to-end consequence: the text set build_session hands prune_live now contains the echoed
    body, so the delivered nudge's echo retires instead of riding the thread bottom forever."""

    def _tx_texts(self, session):
        # exactly how build_session builds the set it passes to prune_live
        return {t for turn in session["turns"] for a in turn["atoms"] for t in km._atom_user_texts(a)}

    def test_the_bundled_delivery_retires_the_echo(self):
        session = {"turns": [{"atoms": [_user_atom([RESTART, DEATH, NUDGE])]}]}
        self.assertIn(NUDGE, self._tx_texts(session),
                      "the nudge landed (bundled) → its echo must be prunable")

    def test_an_undelivered_nudge_still_persists(self):
        # the guarantee this must NOT break: a genuinely dropped send keeps its echo, so the loss shows
        session = {"turns": [{"atoms": [_user_atom([RESTART, DEATH])]}]}
        self.assertNotIn(NUDGE, self._tx_texts(session),
                         "nothing delivered the nudge → the echo must stay visible")


if __name__ == "__main__":
    unittest.main()
