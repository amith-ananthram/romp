#!/usr/bin/env python3
"""Auto-Nudge prompt + planner block rule (the user 2026-06-19).

A goal that finished a phase and is parked awaiting the user's go-ahead was showing WORKING (and
getting auto-nudged) instead of BLOCKED. Two paired fixes guarded here:

  Fix 1 — the nudge prompt asks for the done-vs-blocked split, not a bare "status?", and the kernel
          constant stays in sync with the manual Nudge button in the webview.
  Fix 2 — the planner's block rule explicitly treats "finished phase awaiting your go-ahead" as a
          block, so a status report alongside reported progress no longer reads as working.
"""
import os
import re
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")

# romp-kernel imports romp-judge as `jd` at load, which imports romp-event-model — load deps first
# (mirrors tests/test_kernel_cachebust.py).
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_nudge", os.path.join(BIN, "romp-kernel"))

FEED_TS = os.path.join(ROOT, "ui", "webview", "feed.ts")
OLD_BARE_STATUS = "What is the status of the above goal?"


class AutoNudgePrompt(unittest.TestCase):
    def test_kernel_constant_is_the_only_copy(self):
        # the manual feed Nudge button was REMOVED (the user 2026-06-30) — auto-nudge is the only nudge, so
        # the kernel constant is the single copy of the prompt and feed.ts must carry NO nudge button text
        # (the old in-sync check went stale when the button left and silently failed on the missing regex).
        src = open(FEED_TS, encoding="utf-8").read()
        self.assertNotRegex(src, r'nudge\.onclick', "the manual Nudge button stays removed")

    def test_prompt_elicits_done_and_blocked_on_user(self):
        # every VARIANT must elicit the same split — the rotation re-asks the same question in fresh
        # words, so a variant that dropped "done" or "blocked" would break the working/blocked filing
        self.assertNotEqual(km.AUTO_NUDGE_TEXT, OLD_BARE_STATUS,
                            "the bare status question is what caused the working/blocked mis-classification")
        for v in km.AUTO_NUDGE_VARIANTS:
            t = v.lower()
            with self.subTest(variant=v[:40]):
                self.assertIn("done", t, "nudge should ask what's done")
                self.assertIn("blocked", t, "nudge should ask what's blocked")
                self.assertTrue("me" in t or "you" in t,
                                "nudge should ask whether anything is blocked waiting on the user")

    def test_prompt_reads_like_a_person_not_a_status_form(self):
        # g13 (the user 2026-07-01): the nudge must read like an ordinary user turn, never disclose an
        # automated/tracking origin, and not open with a form-like "Status on the goal above:" header.
        for v in km.AUTO_NUDGE_VARIANTS:
            t = v.lower()
            with self.subTest(variant=v[:40]):
                for robotic in ("status on the goal above", "romp", "automated", "tracking"):
                    self.assertNotIn(robotic, t, robotic)
                self.assertTrue(v[0].isupper() and "?" in v,
                                "a natural question, addressed to the session")

    def test_every_nudge_text_licenses_continuing_without_permission(self):
        # the user 2026-08-11 (after Anthropic's riemann-zeta post, where the operator's whole input was
        # keep-going encouragement): a nudge must LEAD BACK TO WORK, not just buy a report turn — every
        # variant, plain and fork, carries explicit permission to continue without the user's go-ahead.
        for v in km.AUTO_NUDGE_VARIANTS + km.AUTO_NUDGE_STALLED_VARIANTS:
            with self.subTest(variant=v[:40]):
                self.assertIn("keep going", v.lower(),
                              "every nudge variant must license just continuing the work")

    def test_first_fire_is_canonical_and_repeats_vary(self):
        # variant 1 IS the constant, so the first nudge is byte-identical to the pre-variant behavior
        # (test_kernel's verbatim-carry assertions ride on this); repeats rotate, and counts past the
        # list wrap around rather than erroring.
        self.assertEqual(km._nudge_text(1), km.AUTO_NUDGE_TEXT)
        self.assertEqual(km._nudge_text(1, stalled=True), km.AUTO_NUDGE_STALLED_TEXT)
        self.assertEqual(len({km._nudge_text(c) for c in (1, 2, 3)}), 3,
                         "repeat nudges wear different words for the same ask")
        self.assertEqual(km._nudge_text(4), km.AUTO_NUDGE_TEXT, "counts wrap around the variant list")
        self.assertEqual(km._nudge_text(0), km.AUTO_NUDGE_TEXT, "a defensive floor for a malformed count")

    def test_awaiting_backstop_reads_like_a_person(self):
        # the user 2026-08-11: the backstop text said "goal" twice and ANNOUNCED itself automated — the
        # exact disclosures g13 bans — and sat outside test_injected_voice's index, which is how it
        # survived the 2026-07-24 sweep. Indexed there now; belt-and-suspenders ban here too.
        t = km.AWAITING_BACKSTOP_TEXT.lower()
        for robotic in ("goal", "romp", "automated", "tracking"):
            self.assertNotIn(robotic, t, robotic)

    def test_wake_block_why_is_procedural(self):
        # The awaiting wake's escalation (kernel _mark_nudge_failed wake=True) files jd.WAKE_BLOCK_WHY.
        # It must be registered as PROCEDURAL, or the briefer invents a decision brief from <work> —
        # the 2026-07-22 cross-session leak is what exact-match registration prevents.
        self.assertTrue(jd.procedural_block_why(jd.WAKE_BLOCK_WHY))
        self.assertTrue(jd.procedural_block_why(jd.NUDGE_BLOCK_WHY), "the siblings stay registered")


class PlannerBlockRule(unittest.TestCase):
    def test_block_rule_covers_awaiting_go_ahead(self):
        sys = jd.PLAN_SYS.lower()
        self.assertIn("go-ahead", sys,
                      "planner block rule must name the 'awaiting your go-ahead' case")
        self.assertIn("does not keep it working", sys,
                      "planner must say reported progress does not keep an awaiting-user goal working")

    def test_nudge_note_done_rule_carves_out_the_owed_decision(self):
        # the user 2026-07-27: the nudge <note>'s own done rule ("a reported-finished goal is done")
        # was the one wording of done with NO owed-decision escape — a nudge reply that reported the
        # work delivered but ended asking the user to approve the next step completed the card. The
        # note now routes that shape to block.
        import inspect
        src = inspect.getsource(jd.plan_llm)
        self.assertIn("outweighs the finished report", src,
                      "the nudge note's done rule defers an ending approval-ask to block")


if __name__ == "__main__":
    unittest.main()
