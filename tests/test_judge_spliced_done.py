#!/usr/bin/env python3
"""A prompt QUEUED into a running turn completes off the work that FOLLOWS it, and never off nothing.

The 2026-07-29 incident: the user queued a question while the session was mid-turn on other work; the
CLI spliced it in (a queued_command attachment, stamped with its ENQUEUE time), and the process died
before any reply. With the atom placed at its SEND time (the rule until T252d) the splice's segment
absorbed the running turn's CONTINUING atoms — real assistant work, none of it a reply — and a
done-happy planner completed the question from its own knowledge. A spliced leg of
_strip_unevidenced_dones refused every done off such a segment.

T252d (the user 2026-09-08) places the absorbed atom where the model READ it — at its landing time,
below the steps that ran while it waited — so the atoms after it are the model's work after reading
it: its reply, as for any ask. The spliced leg is gone with the placement, and the incident's shape
(a turn that dies before its first post-splice token) is a WORKLESS segment, which the remaining leg
still refuses. Mint/sub still apply, and the turn-level closer keeps done authority. SYNTHETIC
fixtures only."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path
import os

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_spliced_done", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
ASK = "Can the notes-api search endpoint run without a separate key, or does that need its own token?"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def qline(t, text, uuid, parent):
    """The CLI's mid-turn splice witness: a queued_command attachment, uuid-bearing, parent-chained,
    stamped with the ENQUEUE time (earlier than its neighbours in file order — the real shape). The
    event model places its atom at the file-order predecessor's stamp, clamped never before the send."""
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "attachment": {"type": "queued_command", "prompt": text}}


DONE_HAPPY = ('{"ops":[{"why":"asked about the search endpoint","do":"mint","text":"Answer the search endpoint question"},'
              '{"why":"explained the same token covers it","do":"done","ref":1}]}')
SKIP = '{"ops":[{"why":"nothing to file","do":"skip"}]}'


def spliced_records():
    """An in-flight labeling turn; the ask spliced into it; the model's work after reading it
    (chained through the attachment, as the CLI writes it); a later unrelated turn ends things."""
    return [
        uline(T0, "Label the notes-api fixture batch", "u1"),
        aline(T0 + 10, "Working through the batch now.", "a1", "u1"),
        qline(T0 + 30, ASK, "q1", "a1"),
        aline(T0 + 60, "Sheet seven labeled; two to go.", "a2", "q1"),
        uline(T0 + 400, "unrelated: also bump the version", "u2", "a2"),
        aline(T0 + 410, "Bumped.", "a3", "u2"),
    ]


def dead_splice_records():
    """The 2026-07-29 shape: the ask spliced in, and the process dies before its first post-splice
    token; a later unrelated turn ends things. The splice's segment holds no assistant work."""
    return [
        uline(T0, "Label the notes-api fixture batch", "u1"),
        aline(T0 + 10, "Working through the batch now.", "a1", "u1"),
        qline(T0 + 30, ASK, "q1", "a1"),
        uline(T0 + 400, "unrelated: also bump the version", "u2", "q1"),
        aline(T0 + 410, "Bumped.", "a3", "u2"),
    ]


class SplicedDone(unittest.TestCase):
    def _run(self, records, reply_for_ask, reply_default=SKIP):
        calls = []
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"
            def fake(text, *a, **k):
                calls.append(text)
                return reply_for_ask if ASK[:40] in text else reply_default
            jd.plan_llm = jd.opener_llm = fake
            jd._group_store = lambda *a, **k: None
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
            return calls, store

    def test_spliced_ask_completes_off_the_work_that_follows_it(self):
        calls, store = self._run(spliced_records(), DONE_HAPPY)
        self.assertTrue(any(ASK[:40] in c for c in calls), "the spliced segment gets its work unit")
        asked = [nd for nd in store["nodes"].values()
                 if "search endpoint" in (nd.get("text") or "").lower()]
        self.assertTrue(asked, "the spliced ask gets a card — it is real")
        self.assertTrue(asked[0].get("nodeComplete"),
                        "the atoms after the splice are the model's work after reading it: a done-happy "
                        "reply completes the ask, as for any ask (T252d)")

    def test_a_splice_the_turn_never_answered_cannot_complete(self):
        # the 2026-07-29 shape: the turn died before its first post-splice token — the splice's segment
        # holds no assistant work, and the workless leg refuses the done; the ask still gets its card
        calls, store = self._run(dead_splice_records(), DONE_HAPPY)
        asked = [nd for nd in store["nodes"].values()
                 if "search endpoint" in (nd.get("text") or "").lower()]
        self.assertTrue(asked, "the spliced ask still gets a card — it is real")
        self.assertFalse(asked[0].get("nodeComplete"),
                         "no work followed the splice: nothing evidences an answer")

    def test_typed_ask_with_real_answer_still_completes(self):
        # guard precision: an ordinary typed ask whose turn really answers it still dones
        records = [
            uline(T0, ASK, "u1"),
            aline(T0 + 10, "No separate key: the search endpoint rides the session token.", "a1", "u1"),
            uline(T0 + 400, "unrelated: also bump the version", "u2", "a1"),
            aline(T0 + 410, "Bumped.", "a3", "u2"),
        ]
        calls, store = self._run(records, DONE_HAPPY)
        asked = [nd for nd in store["nodes"].values()
                 if "search endpoint" in (nd.get("text") or "").lower()]
        self.assertTrue(asked and asked[0].get("nodeComplete"),
                        "a genuinely delivered answer still completes")

    def test_absorbed_trigger_is_marked_and_the_following_work_is_its_reply(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in spliced_records()) + "\n")
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
            session = jd.parsed_session(SID, [str(tpath)], NOW)
        segs = [seg for turn in session["turns"] for seg in jd.em.segments(turn)]
        by_trig = {seg.get("trigger"): seg for seg in segs}
        self.assertIn("q1", by_trig, "the splice opens its own segment")
        spliced = by_trig["q1"]
        self.assertTrue(any(a.get("uuid") == "q1" and a.get("absorbed") for a in spliced["atoms"]),
                        "the synthesized splice atom carries the absorbed marker")
        self.assertFalse(hasattr(jd, "_seg_spliced"), "the spliced leg is gone with the send-time placement (T252d)")
        # the work after the splice lands inside the splice's segment — its reply
        self.assertIn("a2", [a.get("uuid") for a in spliced["atoms"]])


if __name__ == "__main__":
    unittest.main()
