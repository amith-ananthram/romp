#!/usr/bin/env python3
"""Follow-up steps wear the indexing tier's gist (the user 2026-07-10): a reply to a card is a
MESSAGE, so its forced step node is titled like any other message — the persisted prompt caption
(captions/<fsid>.jsonl '<seg_id>#p', the same phrase the timeline dot shows), else one live gister
call, with the verbatim _seg_label head only as the LLM-outage floor. Previously the fallback fired
whenever the planner's ops carried no text (reopen/unblock/done are textless — a correct reply for
a follow-up that only continues existing goals), leaving the user's raw prompt as a permanent title
among gisted siblings. All fixtures SYNTHETIC."""
import json
import os
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_fugist", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
SEG = SID + ":1781100000:aabbccdd"


class StateTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def write_captions(self, records):
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        with (jd.CAPDIR / (SID + ".jsonl")).open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")


class PromptGist(StateTest):
    """_prompt_gist reads the segment's persisted prompt caption — the authoritative gist."""

    def test_reads_the_prompt_caption(self):
        self.write_captions([{"id": SEG + "#p", "grain": "prompt", "caption": "the repo transfer"}])
        self.assertEqual(jd._prompt_gist(SID, SEG), "the repo transfer")

    def test_last_record_wins(self):
        self.write_captions([{"id": SEG + "#p", "caption": "first pass"},
                             {"id": SEG + "#p", "caption": "re-captioned"}])
        self.assertEqual(jd._prompt_gist(SID, SEG), "re-captioned")

    def test_ignores_other_units_and_work_captions(self):
        self.write_captions([{"id": SEG, "grain": "segment", "caption": "what got done"},
                             {"id": SID + ":999:ffffffff#p", "caption": "another prompt"}])
        self.assertEqual(jd._prompt_gist(SID, SEG), "")

    def test_missing_file_and_junk_lines_are_empty(self):
        self.assertEqual(jd._prompt_gist(SID, SEG), "")
        (jd.CAPDIR).mkdir(parents=True, exist_ok=True)
        (jd.CAPDIR / (SID + ".jsonl")).write_text("not json\n" + json.dumps({"id": SEG + "#p", "caption": "  "}) + "\n")
        self.assertEqual(jd._prompt_gist(SID, SEG), "")


class FollowupTitle(StateTest):
    """_followup_title prefers the stored gist, then one live gister call, then the verbatim floor."""

    def test_stored_gist_wins_without_an_llm_call(self):
        self.write_captions([{"id": SEG + "#p", "caption": "taglines rethink from docs"}])
        called = []
        orig = jd.gist_llm
        jd.gist_llm = lambda *a, **k: called.append(1) or "live gist"
        try:
            self.assertEqual(jd._followup_title(SID, SEG, "look at the docs"), "taglines rethink from docs")
        finally:
            jd.gist_llm = orig
        self.assertEqual(called, [], "the persisted caption is authoritative — no duplicate index call")

    def test_no_stored_gist_calls_the_gister(self):
        orig = jd.gist_llm
        jd.gist_llm = lambda *a, **k: "the remote updates"
        try:
            self.assertEqual(jd._followup_title(SID, SEG, "please update the remotes"), "the remote updates")
        finally:
            jd.gist_llm = orig

    def test_gister_failure_falls_back_to_the_verbatim_head(self):
        orig = jd.gist_llm
        jd.gist_llm = lambda *a, **k: ""
        try:
            out = jd._followup_title(SID, SEG, "one two three four five six seven eight nine ten eleven")
        finally:
            jd.gist_llm = orig
        self.assertEqual(out, "one two three four five six seven eight nine ten…",
                         "the LLM-outage floor stays: the reply lands titled by its own words")


class CoercePlaceTitle(StateTest):
    """The hard-guard floor accepts the caller's known gist; _seg_label is the fallback, not the default."""

    def test_title_overrides_the_verbatim_label(self):
        ops = jd._coerce_place([], "raw user words here", title="a proper gist")
        self.assertEqual(ops, [{"do": "mint", "text": "a proper gist",
                                "why": "kept on the board: a user message the planner tried to skip",
                                "coerced": True}])

    def test_without_title_the_verbatim_label_stands(self):
        ops = jd._coerce_place([], "raw user words here")
        self.assertEqual(ops[0]["text"], "raw user words here")


class SourcePins(unittest.TestCase):
    """Wiring pins: the continuation and every floor site route through the gist."""

    def setUp(self):
        self.src = Path(os.path.join(BIN, "romp-judge")).read_text()

    def test_continuation_step_uses_followup_title(self):
        self.assertIn('step = (desc or {}).get("text") or _followup_title(fsid, seg_id, text)', self.src)

    def test_every_coerce_place_call_passes_the_gist(self):
        calls = self.src.count("ops = _coerce_place(menu, text")
        gisted = self.src.count("ops = _coerce_place(menu, text, title=_prompt_gist(fsid, seg_id) or None)")
        self.assertEqual(calls, 5, "a new floor site must decide its title source explicitly")
        self.assertEqual(gisted, 5, "every floor site titles from the stored gist when one exists")


if __name__ == "__main__":
    unittest.main()
