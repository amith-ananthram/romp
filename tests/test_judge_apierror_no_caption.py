#!/usr/bin/env python3
"""During a usage-limit / auto-nudge storm a session fires "retry" over and over and each turn dies on an
API error. Claude Code writes every failure as an assistant record that carries the error TEXT ("overloaded",
"Request timed out"), so the captioner's work-detector counted it as real work and captioned it — a flood of
judge calls (captioner + the archiver behind it) glossing nothing but error noise, ironically burning tokens
during a usage limit (the user 2026-07-06, who reported a crazy number of judge calls from just retry and API errors, suggesting maybe
just the captioner). An API-error record is now excluded from _has_asst_work / _unit_text, so an error-only
turn is work-less (no caption); a turn that did real work THEN errored still captions the real work.
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
jd = load_source("romp_judge_anc", os.path.join(BIN, "romp-judge"))


def _asst(text, api_error=False, tool=None, uuid="a1", t=100):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "name": tool, "input": {}})
    a = {"type": "assistant", "message": {"content": content}, "uuid": uuid, "t": t}
    if api_error:
        a["isApiError"] = True
    return a


def _user(text, author="human", uuid="u1", t=99):
    return {"type": "user", "author": author, "uuid": uuid, "t": t,
            "message": {"content": [{"type": "text", "text": text}]}}


class ApiErrorNotCaptioned(unittest.TestCase):
    def test_error_only_turn_has_no_captionable_work(self):
        atoms = [_user("retry", author="romp"), _asst("overloaded", api_error=True)]
        self.assertFalse(jd._has_asst_work(atoms),
                         "a turn whose only assistant output is an API error is work-less → no caption")

    def test_real_work_then_error_still_counts(self):
        atoms = [_user("do the thing"),
                 _asst("Editing the parser", tool="Edit", uuid="a1"),
                 _asst("Request timed out", api_error=True, uuid="a2")]
        self.assertTrue(jd._has_asst_work(atoms),
                        "real work before the error must still be captionable — we only drop the pure error")

    def test_unit_text_drops_the_error_noise_keeps_the_work(self):
        atoms = [_user("do the thing"),
                 _asst("Fixed the parser bug", uuid="a1"),
                 _asst("overloaded", api_error=True, uuid="a2")]
        txt = jd._unit_text(atoms)
        self.assertIn("Fixed the parser bug", txt)
        self.assertNotIn("overloaded", txt, "the API-error text never reaches the captioner input")

    def test_error_only_atoms_yield_empty_unit_text(self):
        atoms = [_user("retry", author="romp"), _asst("Request timed out", api_error=True)]
        # only a romp 'retry' + an error → no ASSISTANT SAID line; run_index drops empty-text tasks
        self.assertNotIn("ASSISTANT SAID", jd._unit_text(atoms))


if __name__ == "__main__":
    unittest.main()
