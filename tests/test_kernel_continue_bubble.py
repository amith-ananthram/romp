#!/usr/bin/env python3
"""The Continue button's reply is the USER's gesture with romp's canned words (the user 2026-08-13):
it stays human-authored — blue bubble, judge-filed as the user's reply, no romp-injected marker — but
the chat must not pose the prose as typed. The cont:true send stamps <!-- romp-canned: continue -->
(comment form, the markers rule), the event build lifts it into canned:"continue" for genuine human
turns only, and the display strip drops it like every other romp comment. SYNTHETIC fixtures only."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_contbubble", os.path.join(BIN, "romp-kernel"))

MARK = "<!-- romp-canned: continue -->"


class ContinueMarker(unittest.TestCase):
    def test_cont_sends_stamp_the_marker_and_typed_sends_do_not(self):
        import inspect
        src = inspect.getsource(km._drive)               # askFollowUp is a DRIVE op, not a _dispatch_ws one
        self.assertIn('(CONTINUE_TEXT + "\\n\\n' + MARK + '") if msg.get("cont") else str(msg["text"])', src,
                      "the canned marker rides ONLY the button's send — a typed follow-up stays unmarked")

    def test_the_event_build_lifts_it_for_human_turns_only(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('if author == "human" and "' + MARK + '" in text:', src,
                      "comment-form check, and never on a romp-authored turn (a nudge quoting the copy)")
        self.assertIn('ev["canned"] = "continue"', src)

    def test_display_split_strips_the_marker_with_the_other_romp_comments(self):
        body = ("> Ship the api\n\n" + km.CONTINUE_TEXT + "\n\n" + MARK +
                "\n<!-- romp-goal-id: 11111111-2222-3333-4444-555555555555:g1 -->")
        goal, clean, fu, ctx = km._split_followup(body)
        self.assertTrue(fu)
        self.assertEqual(goal, "Ship the api")
        self.assertNotIn("romp-canned", clean, "the marker is display-stripped like every romp comment")
        self.assertIn("Nothing needed from me here", clean)

    def test_the_canned_copy_itself_is_marker_free(self):
        self.assertNotIn("<!--", km.CONTINUE_TEXT,
                         "CONTINUE_TEXT stays pure prose — the marker is appended at the ONE send site, "
                         "so every other consumer (voice tests, the courier, the judges) sees clean copy")


if __name__ == "__main__":
    unittest.main()
