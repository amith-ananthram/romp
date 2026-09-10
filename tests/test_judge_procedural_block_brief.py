#!/usr/bin/env python3
"""A goal blocked ONLY by romp's own bookkeeping gets NO decision brief, instead of an invented one
(the user 2026-07-22, whose "Remote host attachment feature" card carried a decision brief about
scrubbing a contributor's email address out of commit authorship).

BLOCK_BRIEF_SYS tells the briefer to "Lead with exactly what they must decide or provide". The kernel,
though, authors two PROCEDURAL block reasons that name no decision at all: the nudge's "romp followed up
once and the response didn't resolve this" and the interrupt's "you stopped this session mid-turn". Handed
one of those as <owed>, the briefer can only source a decision from <work> -- and <work> is the goal's
whole SUBTREE, which legitimately includes delegated sub-goals carrying a PEER session's relayed question.
In the live case the subtree's 24k chars of work text held exactly one decision-shaped passage, and it
belonged to another session's git-history audit, so that is what the card asserted.

So: procedural-only blocks skip the briefer entirely. A goal blocked on a REAL question is untouched, and
a goal blocked on both still briefs the real one. Since 2026-07-23 the card is no longer left MUTE for it:
the STALLER speaks instead (promoted stall note, or its own where-this-stands prompt with the procedural
why as <holding>) — see ProceduralBlockStillSpeaks in test_judge.py. SYNTHETIC fixtures only.
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
jd = load_source("romp_judge_procbrief", os.path.join(BIN, "romp-judge"))

REAL_Q = "Python on the server is still 3.8; upgrade it the same no-sudo way, or leave it as secondary?"


class ProceduralPredicate(unittest.TestCase):
    def test_the_kernel_authored_reasons_are_procedural(self):
        self.assertTrue(jd.procedural_block_why(jd.NUDGE_BLOCK_WHY))
        self.assertTrue(jd.procedural_block_why(jd.INTERRUPT_BLOCK_WHY))

    def test_surrounding_whitespace_still_matches(self):
        self.assertTrue(jd.procedural_block_why("  " + jd.NUDGE_BLOCK_WHY + "\n"))

    def test_a_real_question_is_not_procedural(self):
        self.assertFalse(jd.procedural_block_why(REAL_Q))
        self.assertFalse(jd.procedural_block_why(""))
        self.assertFalse(jd.procedural_block_why(None))

    def test_a_question_that_merely_resembles_one_is_still_a_question(self):
        # exact match only: a real ask that happens to mention following up must keep its brief
        self.assertFalse(jd.procedural_block_why(
            "romp followed up once and the response didn't resolve this, so which host should I use?"))


class OwedSelection(unittest.TestCase):
    """The selection the block-distiller performs before calling brief_llm, pinned directly: procedural
    entries are dropped, and a procedural-ONLY block is recognized so the call is skipped."""

    def _select(self, blkd):
        proc_only = bool(blkd) and all(jd.procedural_block_why(d.get("blockWhy")) for d in blkd)
        kept = [d for d in blkd if not jd.procedural_block_why(d.get("blockWhy"))]
        owed = ([(d.get("text", ""), d.get("blockWhy", "")) for d in kept] if len(kept) > 1
                else kept[0]["blockWhy"] if kept else "")
        return proc_only, owed

    def test_a_nudge_only_block_briefs_nothing(self):
        proc_only, owed = self._select([{"text": "Remote host attachment feature",
                                         "blockWhy": jd.NUDGE_BLOCK_WHY}])
        self.assertTrue(proc_only, "no decision was ever asked → the briefer must not be called")
        self.assertEqual(owed, "")

    def test_an_interrupt_only_block_briefs_nothing(self):
        proc_only, _ = self._select([{"text": "x", "blockWhy": jd.INTERRUPT_BLOCK_WHY}])
        self.assertTrue(proc_only)

    def test_a_real_question_still_briefs(self):
        proc_only, owed = self._select([{"text": "Upgrade python", "blockWhy": REAL_Q}])
        self.assertFalse(proc_only)
        self.assertEqual(owed, REAL_Q, "a genuine owed question reaches the briefer unchanged")

    def test_a_mixed_block_briefs_only_the_real_question(self):
        proc_only, owed = self._select([{"text": "Upgrade python", "blockWhy": REAL_Q},
                                        {"text": "Remote host", "blockWhy": jd.NUDGE_BLOCK_WHY}])
        self.assertFalse(proc_only, "a real question is owed → still brief it")
        self.assertEqual(owed, REAL_Q, "the procedural entry must not pad the owed list")

    def test_two_real_questions_still_produce_the_pair_list(self):
        # the multi-subgoal shape (2026-07-21) is unaffected
        proc_only, owed = self._select([{"text": "A", "blockWhy": "decide A?"},
                                        {"text": "B", "blockWhy": "decide B?"}])
        self.assertFalse(proc_only)
        self.assertEqual(owed, [("A", "decide A?"), ("B", "decide B?")])

    def test_procedural_entries_never_make_a_lone_real_question_look_plural(self):
        # two blocked nodes but only one real question → the SINGLE-item shape, not the numbered list
        _, owed = self._select([{"text": "A", "blockWhy": "decide A?"},
                                {"text": "B", "blockWhy": jd.INTERRUPT_BLOCK_WHY}])
        self.assertEqual(owed, "decide A?")

    def test_no_blocked_nodes_is_not_procedural_only(self):
        # a live picker/permission focus carries no blockWhy at all; it must keep its existing path
        proc_only, owed = self._select([])
        self.assertFalse(proc_only, "no block reason is not the same as a procedural one")
        self.assertEqual(owed, "")


class KernelUsesTheSharedConstants(unittest.TestCase):
    """The kernel authors these strings; the briefer recognizes them. If they drift the bug returns
    silently, so pin that the kernel references the constants rather than inlining its own copy."""

    def _kernel_src(self):
        with open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py"), errors="replace") as f:
            return f.read()

    def test_kernel_references_the_constants(self):
        src = self._kernel_src()
        self.assertIn("jd.NUDGE_BLOCK_WHY", src)
        self.assertIn("jd.INTERRUPT_BLOCK_WHY", src)

    def test_kernel_does_not_inline_its_own_copy(self):
        src = self._kernel_src()
        self.assertNotIn('"romp followed up once and the response', src)
        self.assertNotIn('"you stopped this session mid-turn', src)


if __name__ == "__main__":
    unittest.main()
