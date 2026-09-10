#!/usr/bin/env python3
"""The closer's optional-offer rule + the session prompt's completion-hygiene nudge (2026-08-12).

The blind spot, reconstructed synthetically: an agent finishes the whole ask, says so, and ends on
a strictly take-it-or-leave-it extra ("say the word if you want the demo config flagged too —
otherwise we're wrapped"). No done-shaped turn without a trailing question ever exists — completion
evidence arrives bundled with fresh questions, restarts cut turns — so the card never files done and
the user has to ask "are you done?" by hand, the exact wrap-check the closer exists to automate.
Two halves, both pinned here:

- CLOSER_SYS gains the rule: only-loose-end-is-an-optional-offer files DONE (never blocked, never
  omitted), with the offer named in the why so the option survives on the record. Flap-safe by
  construction: the closer runs once per ended turn on new evidence (closedSig), so this adds no
  re-derivation path.
- claude/romp-session-prompt.md tells agents to state completion plainly and keep optional offers
  separate from the done declaration, so turns stay judgeable at the source.
"""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_offer", os.path.join(BIN, "romp-judge"))


class OptionalOfferRule(unittest.TestCase):
    def test_the_rule_exists_and_files_done(self):
        self.assertIn("ONLY loose end is an explicitly OPTIONAL offer is done, not blocked", jd.CLOSER_SYS)
        self.assertIn("named declining as the resting state", jd.CLOSER_SYS)
        self.assertIn("offered X as an optional extra", jd.CLOSER_SYS,
                      "the why carries the offer, so the option survives on the record")

    def test_the_rule_lives_in_the_done_section_and_disclaims_go_aheads(self):
        # it must sit ABOVE the blocked rule (it refines done), and explicitly distinguish itself
        # from the go-ahead endings that ARE blocked — the two shapes are one comma apart in prose
        # ("want me to build it?" vs "happy to also add Y if you'd like").
        rule = jd.CLOSER_SYS.index("OPTIONAL offer")
        blocked = jd.CLOSER_SYS.index("- blocked:")
        self.assertLess(rule, blocked, "the optional-offer rule refines done, not blocked")
        self.assertIn("NOT the go-ahead ending", jd.CLOSER_SYS)
        self.assertIn("an optional offer's work is already delivered either way", jd.CLOSER_SYS)


class SessionPromptNudge(unittest.TestCase):
    def test_agents_are_told_to_keep_offers_separate_from_the_done_statement(self):
        md = open(os.path.join(os.path.dirname(HERE), "claude", "romp-session-prompt.md")).read()
        self.assertIn("state\nthe completion first, in its own plain sentence", md)
        self.assertIn("clearly marked optional with declining as the default", md)
        self.assertIn("folds the finish into an open\nquestion reads as unfinished work", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
