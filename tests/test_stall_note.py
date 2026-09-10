"""The STALL NOTE (the user 2026-07-23).

A goal that is neither finished nor blocked-on-you, but that romp's nudge gate is quietly holding, used to
say NOTHING for itself: the distiller waits for a completion, the briefer waits for a block, and the card
sat in Working with no explanation while nothing moved it. The staller is the third surface. These cover
the read side (which records count as a stall) and the wiring that keeps the two halves honest — the kernel
WRITES the deferral record and the judge READS it, so a drift between them is silent breakage.
"""
import json
import pathlib
import sys
import tempfile
import unittest
from romp_load import load_source
import os

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_stall", str(ROOT / "kernel" / "judge.py"))

FSID = "11111111-2222-3333-4444-555555555555"
GID = FSID + ":g7"
NOW = 1781100000


class StalledFacts(unittest.TestCase):
    """jd.stalled_facts is the judge's view of the kernel's deferral log."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = pathlib.Path(self.tmp.name)

    def tearDown(self):
        jd.STATE = self.saved
        self.tmp.cleanup()

    def _write(self, deferred):
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({"deferred": deferred}))

    def test_a_standing_reason_is_reported(self):
        # no seen gate (2026-08-13): the kernel's sweep pops records on their events, so existence IS
        # the standing hold and one observation already presents
        self._write({GID: {"at": NOW, "why": "the agent's to-do sync is due", "sid": FSID}})
        self.assertEqual(jd.stalled_facts(FSID),
                         {GID: {"why": "the agent's to-do sync is due", "since": NOW}})

    def test_another_session_is_not_mine(self):
        other = "99999999-8888-7777-6666-555555555555:g1"
        self._write({other: {"at": NOW, "why": "the agent's to-do sync is due"}})
        self.assertEqual(jd.stalled_facts(FSID), {}, "goals are keyed by session; only mine are mine")

    def test_a_legacy_bare_int_record_says_nothing(self):
        # Written before the record carried a reason. It is not an error, there is simply no why to show.
        self._write({GID: NOW})
        self.assertEqual(jd.stalled_facts(FSID), {})

    def test_a_judging_hold_never_presents(self):
        # The user 2026-07-31 (superseding the 2026-07-25 live-verify): a goal held because romp's own
        # review is mid-flight is a goal romp is WORKING — the Analyzing… swirl carries that story, and a
        # stalled chip there drew the eye to a state nobody needs to act on. So the staller never sees
        # these, even while active_runs genuinely shows the call in flight.
        self._write({GID: {"at": NOW, "why": jd.WHY_JUDGING, "sid": FSID}})
        saved = jd.active_runs
        try:
            jd.active_runs = lambda: [{"judge": "closer", "fsid": FSID, "sent": 1.0}]
            self.assertEqual(jd.stalled_facts(FSID), {},
                             "in-flight class: presents as the Analyzing… swirl, so no stall note is "
                             "owed — the WHY_IN_FLIGHT tuple is the one shared definition (2026-08-13)")
        finally:
            jd.active_runs = saved

    def test_a_legacy_global_pass_record_never_wears_the_chip(self):
        # pre-2026-07-25 records carried the GLOBAL "a judge pass is mid-flight" — in-flight class, so
        # the staller owes it nothing (the swirl covers it, and the kernel's sweep pops it the tick no
        # call is in flight)
        self._write({GID: {"at": NOW, "why": "a judge pass is mid-flight"}})
        self.assertEqual(jd.stalled_facts(FSID), {})

    def test_an_unreadable_store_is_not_fatal(self):
        (jd.STATE / "auto-nudge.json").write_text("{ this is not json")
        self.assertEqual(jd.stalled_facts(FSID), {}, "a stall note is an extra, never a reason to fail a pass")

    def test_no_store_at_all_is_not_fatal(self):
        self.assertEqual(jd.stalled_facts(FSID), {})


class StallSurfaceWiring(unittest.TestCase):
    """The stall note is its own card surface, and must not be mistaken for the other two."""

    def test_the_warn_kinds_name_the_stall_surface(self):
        self.assertEqual(jd._warn_line_kind("staller"), ("stall note", "stall"))
        self.assertEqual(jd._warn_line_kind("distiller"), ("summary", "summary"))
        self.assertEqual(jd._warn_line_kind("briefer"), ("decision brief", "brief"))

    def test_a_stall_warn_routes_to_the_stall_surface(self):
        self.assertEqual(jd._warn_surface({"kind": "stall-failed"}), "stall")
        self.assertEqual(jd._warn_surface({"kind": "stall-unreadable"}), "stall")
        self.assertEqual(jd._warn_surface({"kind": "brief-failed"}), "brief")
        self.assertEqual(jd._warn_surface({"kind": "summary-failed"}), "summary")


class StallPrompt(unittest.TestCase):
    """The one thing this prompt must never do is manufacture an interrupt. A stall is romp being the
    bottleneck; telling the user to decide something would turn every wedge into a false 'needs you'."""

    def test_it_forbids_inventing_a_decision_the_user_owes(self):
        sys_prompt = jd.STALL_BRIEF_SYS
        self.assertIn("never invent a decision they", sys_prompt)
        self.assertIn("NOT being asked to decide anything", sys_prompt)

    def test_it_must_not_overrule_the_kernels_reason(self):
        # <holding> is the authoritative mechanical why. A model that re-derives the cause from the work
        # history would report a plausible wrong answer, which is the failure this whole surface exists to end.
        self.assertIn("<holding>", jd.STALL_BRIEF_SYS)
        self.assertIn("Restate <holding> faithfully", jd.STALL_BRIEF_SYS)

    def test_the_call_passes_the_holding_reason_through(self):
        import inspect
        src = inspect.getsource(jd.stall_llm)
        self.assertIn('_sec("holding", holding, mk)', src)   # a marked content section, not a plain tag
        self.assertIn('judge="staller"', src)


if __name__ == "__main__":
    unittest.main()
