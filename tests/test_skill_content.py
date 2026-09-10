#!/usr/bin/env python3
"""Skill-invocation rendering (the user 2026-07-08): a Skill tool call is followed by the skill's full
instructions as an isMeta user record ("Base directory for this skill: …"). The old pipeline DROPPED it
at parse (the generic isMeta skip) but streamed it LIVE as a plain user atom — so the chat showed a
fully-expanded note box while the turn ran, which then vanished on landing (the tool fold only showed
"Launching skill: X"). Now every layer treats it the same way: the event model (and its live twin in
romp_sdk_backend.msg_to_atom) emit a flagged, content-EMPTY atom whose markdown rides atom["skillMd"] —
so no assistant-text reader (judge work text, captions, anchors) ever sees it — and build_session folds
it into the INVOKING Skill tool event, which the client renders collapsed by default. Synthetic only."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_em_skill", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_skill", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600
SKILL_MD = ("Base directory for this skill: /tmp/TESTHOST/skills/cleanplots\n\n"
            "# cleanplots\n\nMake plots with cp.fig() and finish with ax.clean().")
# The NEWER payload shape (2026-07-10): no "Base directory…" preamble — the record instead links to the
# invoking Skill tool_use via sourceToolUseID (stream: parent_tool_use_id). Raw markdown head.
SKILL_MD_V2 = "# cleanplots\n\nMake plots with cp.fig() and finish with ax.clean()."


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _records():
    """user prompt → assistant Skill tool_use → tool_result → the isMeta instructions record → reply."""
    return [
        {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
         "message": {"role": "user", "content": "make the plot"}},
        {"type": "assistant", "timestamp": iso(T0 + 10), "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "stop_reason": None, "content": [
             {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "cleanplots"}}]}},
        {"type": "user", "timestamp": iso(T0 + 11), "uuid": "u2", "parentUuid": "a1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "Launching skill: cleanplots"}]}},
        {"type": "user", "timestamp": iso(T0 + 12), "uuid": "u3", "parentUuid": "u2", "isMeta": True,
         "message": {"role": "user", "content": SKILL_MD}},
        {"type": "assistant", "timestamp": iso(T0 + 20), "uuid": "a2", "parentUuid": "u3",
         "message": {"role": "assistant", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": "plotted it"}]}},
    ]


class EventModelSkillAtom(unittest.TestCase):
    def _parse(self, records):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            return em.parse_session(str(p), rompuuid=SID, now=NOW, sdk_human=True)

    def test_skill_content_becomes_a_flagged_empty_atom(self):
        sess = self._parse(_records())
        atoms = [a for turn in sess["turns"] for a in turn["atoms"]]
        sk = next((a for a in atoms if a.get("skillMd")), None)
        self.assertIsNotNone(sk, "the isMeta instructions record survives as a flagged atom")
        self.assertEqual(sk["type"], "assistant")
        self.assertIn("# cleanplots", sk["skillMd"])
        self.assertEqual(sk["message"]["content"], [],
                         "content stays EMPTY — generic assistant-text readers must never see the skill md")
        self.assertIsNone(sk["message"]["stop_reason"], "it can neither open nor close the running turn")

    def test_the_turn_still_ends_on_the_real_reply(self):
        sess = self._parse(_records())
        self.assertTrue(sess["turns"][-1]["ended"], "the real end_turn reply closes the turn as before")

    def test_other_ismeta_records_stay_dropped(self):
        recs = _records()
        recs[3] = {"type": "user", "timestamp": iso(T0 + 12), "uuid": "u3", "parentUuid": "u2",
                   "isMeta": True, "message": {"role": "user", "content": "Caveat: some harness noise"}}
        sess = self._parse(recs)
        atoms = [a for turn in sess["turns"] for a in turn["atoms"]]
        self.assertNotIn("u3", [a.get("uuid") for a in atoms], "the generic isMeta skip is untouched")

    def test_cap_is_applied(self):
        recs = _records()
        big = "Base directory for this skill: /tmp/x\n\n" + ("word " * 8000)
        recs[3]["message"]["content"] = big
        sess = self._parse(recs)
        sk = next(a for turn in sess["turns"] for a in turn["atoms"] if a.get("skillMd"))
        self.assertLessEqual(len(sk["skillMd"]), em.SKILL_MD_CAP + 40)
        self.assertIn("…(skill content truncated)", sk["skillMd"])

    def test_v2_shape_classifies_by_source_tool_use_id(self):
        # the newer CLI payload: raw markdown (no preamble), linked by sourceToolUseID → still a
        # flagged, content-EMPTY skillMd atom, not an isMeta drop (whose un-superseded LIVE twin
        # rendered as the giant expanded note box, the user 2026-07-10)
        recs = _records()
        recs[3] = {"type": "user", "timestamp": iso(T0 + 12), "uuid": "u3", "parentUuid": "u2",
                   "isMeta": True, "sourceToolUseID": "t1",
                   "message": {"role": "user", "content": SKILL_MD_V2}}
        sess = self._parse(recs)
        atoms = [a for turn in sess["turns"] for a in turn["atoms"]]
        sk = next((a for a in atoms if a.get("skillMd")), None)
        self.assertIsNotNone(sk, "the sourceToolUseID-linked payload survives as a flagged atom")
        self.assertIn("# cleanplots", sk["skillMd"])
        self.assertEqual(sk["message"]["content"], [])
        self.assertTrue(sess["turns"][-1]["ended"], "the real reply still ends the turn")

    def test_v2_link_to_a_non_skill_tool_stays_dropped(self):
        # a sourceToolUseID that names a NON-Skill tool_use is some other injection — the generic
        # isMeta skip keeps eating it
        recs = _records()
        recs[1]["message"]["content"] = [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]
        recs[3] = {"type": "user", "timestamp": iso(T0 + 12), "uuid": "u3", "parentUuid": "u2",
                   "isMeta": True, "sourceToolUseID": "t1",
                   "message": {"role": "user", "content": SKILL_MD_V2}}
        sess = self._parse(recs)
        atoms = [a for turn in sess["turns"] for a in turn["atoms"]]
        self.assertIsNone(next((a for a in atoms if a.get("skillMd")), None))
        self.assertNotIn("u3", [a.get("uuid") for a in atoms])

    def test_v2_tool_result_with_the_link_is_not_skill_md(self):
        # adversarial: if the Skill tool's own result record ever carries the link, it must stay a
        # normal tool result (the fold body is the instructions, never the "Launching skill" echo)
        recs = _records()
        recs[2]["sourceToolUseID"] = "t1"
        sess = self._parse(recs)
        atoms = [a for turn in sess["turns"] for a in turn["atoms"]]
        sk = [a for a in atoms if a.get("skillMd")]
        self.assertEqual(len(sk), 1, "only the instructions record classifies")
        self.assertIn("# cleanplots", sk[0]["skillMd"])


class LiveTwin(unittest.TestCase):
    """romp_sdk_backend.msg_to_atom classifies the STREAM copy identically — it was the expanded live box."""

    def test_stream_skill_content_gets_the_flagged_atom(self):
        sb = load_source("romp_sdk_backend_skill", os.path.join(BIN, "romp_sdk_backend.py"))

        class TextBlock:
            def __init__(self, text):
                self.text = text

        class UserMessage:
            uuid = "u3"

            def __init__(self, content):
                self.content = content

        a = sb.msg_to_atom(UserMessage([TextBlock(SKILL_MD)]), "s", "f", 5)
        self.assertEqual(a["type"], "assistant")
        self.assertIn("# cleanplots", a["skillMd"])
        self.assertEqual(a["message"]["content"], [])
        self.assertIsNone(a["message"]["stop_reason"])

    def test_stream_v2_shape_classifies_by_parent_tool_use_id(self):
        sb = load_source("romp_sdk_backend_skill2", os.path.join(BIN, "romp_sdk_backend.py"))

        class TextBlock:
            def __init__(self, text):
                self.text = text

        class UserMessage:
            uuid = "u3"
            parent_tool_use_id = "t1"

            def __init__(self, content):
                self.content = content

        # linked to a KNOWN Skill tool_use → the flagged join atom, no preamble needed
        a = sb.msg_to_atom(UserMessage([TextBlock(SKILL_MD_V2)]), "s", "f", 5, skill_tool_ids={"t1"})
        self.assertEqual(a["type"], "assistant")
        self.assertIn("# cleanplots", a["skillMd"])
        self.assertEqual(a["message"]["content"], [])
        # same message with the id NOT in the session's Skill set → SIDECHAIN traffic, dropped
        # (2026-08-17: a parent_tool_use_id outside the Skill set marks a subagent's own turn — the
        # old ordinary-user-atom fallback is exactly how kickoff prompts leaked into the parent chat
        # as giant expanded boxes; see test_sidechain_atoms.py)
        b = sb.msg_to_atom(UserMessage([TextBlock(SKILL_MD_V2)]), "s", "f", 5, skill_tool_ids={"other"})
        self.assertIsNone(b)

    def test_note_skill_tool_ids_collects_from_the_stream(self):
        sb = load_source("romp_sdk_backend_skill3", os.path.join(BIN, "romp_sdk_backend.py"))
        ids = set()
        sb._note_skill_tool_ids({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Skill", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}]}}, ids)
        self.assertEqual(ids, {"t1"}, "only Skill tool_use ids arm the payload classification")


class BuildSessionJoin(unittest.TestCase):
    """build_session folds the flagged atom into the INVOKING Skill tool event — no separate note box."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
                      km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global.md"
        km._read_task_store = lambda fsid, fold=None: []
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._parse_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
         km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD) = self.saved
        km._parse_cache.clear()
        self.td.cleanup()

    def test_skill_tool_event_carries_the_md_and_no_extra_event(self):
        self.tpath.write_text("\n".join(json.dumps(r) for r in _records()) + "\n")
        events = km.build_session(SID, NOW)["events"]
        tool = next(e for e in events if e.get("kind") == "tool" and e.get("name") == "Skill")
        self.assertIn("# cleanplots", tool.get("skillMd") or "", "the instructions ride the tool event")
        others = [e for e in events if e is not tool and "# cleanplots" in json.dumps(e)]
        self.assertEqual(others, [], "and NOWHERE else — no separate expanded note box")

    def test_v2_shape_joins_the_tool_event_too(self):
        recs = _records()
        recs[3] = {"type": "user", "timestamp": iso(T0 + 12), "uuid": "u3", "parentUuid": "u2",
                   "isMeta": True, "sourceToolUseID": "t1",
                   "message": {"role": "user", "content": SKILL_MD_V2}}
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        events = km.build_session(SID, NOW)["events"]
        tool = next(e for e in events if e.get("kind") == "tool" and e.get("name") == "Skill")
        self.assertIn("# cleanplots", tool.get("skillMd") or "", "the v2 payload rides the tool event")
        others = [e for e in events if e is not tool and "# cleanplots" in json.dumps(e)]
        self.assertEqual(others, [], "and NOWHERE else")


if __name__ == "__main__":
    unittest.main()
