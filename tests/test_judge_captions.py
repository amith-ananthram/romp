#!/usr/bin/env python3
"""Split captions (the user 2026-06-19): the captioner emits a MESSAGE caption (a gist of the user's ask,
ready the instant the message lands — even mid-work) AND a WORK caption (what got done, once the segment
closes). The message caption is keyed '<segid>#p' so it never collides with the work caption, and is
produced by gist_llm logged as the captioner. Synthetic transcript only.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


class SplitCaptions(unittest.TestCase):
    def setUp(self):
        # a CLOSED turn (prompt + work) then an OPEN turn (a lone prompt, still in progress)
        recs = [uline(T0, "fix the parser bug", "u1"),
                aline(T0 + 20, "Fixed it.", "a1", "u1", stop="end_turn"),
                uline(T0 + 100, "now add a regression test", "u2", "a1")]
        td = Path(tempfile.mkdtemp())
        p = td / (SID + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.session = em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], now=NOW)
        self.open_seg = em.segments(self.session["turns"][1])[0]          # the in-progress turn's lone segment

    def test_message_caption_is_ready_immediately_even_mid_work(self):
        tasks = jd._ready_tasks(self.session)
        prompt_ids = {w["id"] for t in tasks if t.get("kind") == "prompt" for w in t["writes"]}
        work_ids = {w["id"] for t in tasks if t.get("kind") == "work" for w in t["writes"]}
        # the OPEN turn's message gets a caption task right away (the dot doesn't wait for the work)...
        self.assertIn(self.open_seg["id"] + "#p", prompt_ids)
        # ...but the open segment is a bare prompt with NO assistant work yet, so it gets NO work caption —
        # work-less units are skipped (g16's live caption fires only once real work appears; the user
        # 2026-06-22), which is what stops the captioner refusing ("no assistant work is shown") on an empty unit
        self.assertNotIn(self.open_seg["id"], work_ids)

    def test_prompt_grain_is_keyed_p_and_never_collides_with_work(self):
        tasks = jd._ready_tasks(self.session)
        for t in tasks:
            if t.get("kind") != "prompt":
                continue
            for w in t["writes"]:
                self.assertEqual(w["grain"], "prompt")
                self.assertTrue(w["id"].endswith("#p"), "message-caption id is suffixed so it can't shadow the work caption")

    def test_prompt_text_is_the_raw_ask(self):
        self.assertEqual(jd._prompt_text(self.open_seg["atoms"]), "now add a regression test")

    def test_caption_call_routes_prompt_to_the_gister(self):
        # _caption_call returns (caption, paused) since 2026-08-18 — paused rides out of the worker
        # thread so the strike ledger never counts a rate-gate skip as the model's empty verdict
        with mock.patch.object(jd, "gist_llm", return_value="G") as g, \
             mock.patch.object(jd, "caption_llm", return_value="W") as c:
            self.assertEqual(jd._caption_call({"kind": "prompt", "text": "x"}), ("G", False))
            g.assert_called_once_with("x")                                # message caption → the gister (its own label, 2026-07-08)
            self.assertEqual(jd._caption_call({"kind": "work", "text": "y"}), ("W", False))
            c.assert_called_once_with("y")                                # work caption → the past-tense captioner


if __name__ == "__main__":
    unittest.main()
