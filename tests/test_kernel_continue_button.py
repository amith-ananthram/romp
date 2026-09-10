#!/usr/bin/env python3
"""The Continue button's gesture is a drive op (the user 2026-08-13).

The incident: Continue (askFollowUp with cont:true) deliberately carries NO text — the kernel supplies
CONTINUE_TEXT in the handler body — but the drive-op entry guard required text, so every press was
silently dropped: no send, no optimistic reopen, no cardMoveAck. The feed's optimistic move then timed
out and bounced the card back to Blocked with "romp didn't confirm that move" on EVERY click, which
made the button read as useless. The handler always supported cont; its front door never let it in.

Synthetic sids only.
"""
import inspect
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_contbtn", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
GID = SID + ":g7"


class ContinueIsADriveOp(unittest.TestCase):
    """_drive's entry classifier must admit the gesture — the regression that killed the button."""

    def _classified(self, msg):
        """Whether _drive CLAIMS the message (anything but the not-a-drive-op False), with every
        downstream dependency stubbed to a refusal-free no-op so only the classifier is under test."""
        saved = km._kernel_knows
        km._kernel_knows = lambda sid: False   # short-circuit right after classification: the foreign-sid
        try:                                   # refusal path returns True (claimed), False means unclassified
            saved_refuse = km._refuse_drive
            km._refuse_drive = lambda *a, **k: None
            try:
                return km._drive(dict(msg), client=None) is not False
            finally:
                km._refuse_drive = saved_refuse
        finally:
            km._kernel_knows = saved

    def test_a_continue_gesture_is_classified(self):
        self.assertTrue(self._classified({"type": "askFollowUp", "itemId": GID, "sid": SID, "cont": True}),
                        "cont:true carries no text BY DESIGN — the guard must admit it")

    def test_a_typed_followup_is_classified(self):
        self.assertTrue(self._classified({"type": "askFollowUp", "itemId": GID, "sid": SID,
                                          "text": "already answered this in the thread"}))

    def test_an_empty_followup_is_still_refused(self):
        self.assertFalse(self._classified({"type": "askFollowUp", "itemId": GID, "sid": SID}),
                         "no text and no cont → still not a sendable gesture")

    def test_the_handler_body_supplies_the_canned_text(self):
        src = inspect.getsource(km._drive)
        # the romp-canned marker rides the button's send only (the user 2026-08-13): the chat folds the
        # canned words to a gesture gist; a typed follow-up stays unmarked
        self.assertIn('text = (CONTINUE_TEXT + "\\n\\n<!-- romp-canned: continue -->") if msg.get("cont") else str(msg["text"])', src)
        self.assertIn('(msg.get("text") or msg.get("cont"))', src,
                      "the front door admits what the body supports — one contract")


if __name__ == "__main__":
    unittest.main()
