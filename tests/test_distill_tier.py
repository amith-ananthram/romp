#!/usr/bin/env python3
"""The DISTILLING judge tier (the user 2026-08-14): distiller, briefer and staller — the card-prose
writers — run their own model/effort pair, split out of triage. The stored sentinel "triage" (the
default) means FOLLOW the triage setting live, exactly what these judges did before the split, so
nothing changes until the user pins a value. Synthetic only; hermetic state dir."""
import inspect
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_distill", os.path.join(BIN, "romp-judge"))


class DistillTierResolution(unittest.TestCase):
    def setUp(self):
        for f in ("judge-model", "judge-effort", "distill-model", "distill-effort"):
            try:
                (jd.STATE / f).unlink()
            except OSError:
                pass
        jd._state_cache.clear()

    def _put(self, name, value):
        jd.STATE.mkdir(parents=True, exist_ok=True)
        (jd.STATE / name).write_text(value)
        jd._state_cache.clear()   # same-second writes share an mtime; the cache is not under test

    def test_unset_follows_the_triage_pick_live(self):
        self._put("judge-model", "haiku")
        self._put("judge-effort", "high")
        self.assertEqual(jd._distill_model(), "haiku")
        self.assertEqual(jd._distill_effort(), "high")
        self._put("judge-model", "fable")     # triage moves; the unset distill pair moves WITH it
        self.assertEqual(jd._distill_model(), "fable")

    def test_the_stored_sentinel_is_follow_not_a_model_name(self):
        self._put("distill-model", "triage")
        self._put("judge-model", "haiku")
        self.assertEqual(jd._distill_model(), "haiku")

    def test_a_pinned_value_stops_following(self):
        self._put("judge-model", "haiku")
        self._put("distill-model", "fable")
        self.assertEqual(jd._distill_model(), "fable")
        self._put("judge-model", "sonnet")    # triage moves; the pinned pair does not
        self.assertEqual(jd._distill_model(), "fable")

    def test_pinned_none_effort_means_no_flag_even_when_triage_has_one(self):
        # "none" is the stored no-flag pin: "" cannot be stored (_state_str folds an empty file into
        # the default, which here means "follow triage" — the exact bug this test caught pre-ship)
        self._put("judge-effort", "high")
        self._put("distill-effort", "none")
        self.assertEqual(jd._distill_effort(), "")

    def test_the_prose_call_sites_run_the_distill_tier(self):
        # the split is only real if the three card-prose judges actually resolve it — and the tier
        # rides into _judge_run so the effort resolution and env stay per-tier
        for fn in (jd.distill_llm, jd.brief_llm):
            src = inspect.getsource(fn)
            self.assertIn("_distill_model()", src, fn.__name__)
            self.assertIn('tier="distill"', src, fn.__name__)
        full = inspect.getsource(jd)
        self.assertIn('_judge_run(_distill_model(), STALL_BRIEF_SYS', full)
        self.assertIn('_distill_effort() if tier == "distill"', inspect.getsource(jd._judge_run_impl))


if __name__ == "__main__":
    unittest.main()
