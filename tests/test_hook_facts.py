#!/usr/bin/env python3
"""Interaction-tool FACTS (2026-08-29, hook round 2): the TaskCreate/TaskUpdate poke + writer
attribution (the store stays authoritative), the PushNotification mirror (pushSent/localSent are the
ack's own delivered-or-suppressed booleans), and Skill role telemetry. Payload shapes probe-verified
against CLI 2.1.221 on 2026-08-29. Synthetic fixtures only."""
import asyncio
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_facts", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-000000000041"


class _Backend(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.pokes = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None,
                                poke=lambda: self.pokes.append(1), log=lambda m: None)
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": "/tmp", "alive": True})
        self.sess = sb.SdkSession(self.be, sb.read_reg(self.d, SID))

    def _hook(self, payload):
        asyncio.run(self.sess._facts_tool_hook(payload, payload.get("tool_use_id"), None))

    def _reg(self):
        return sb.read_reg(self.d, SID) or {}


class TaskWritePoke(_Backend):
    def test_task_writes_are_a_tail_so_parallel_attribution_survives(self):
        # a last-write-wins slot lost every agentId but the newest (review 2026-08-29)
        self._hook({"tool_name": "TaskUpdate", "agent_id": "agent-42",
                    "tool_input": {"taskId": "7", "status": "in_progress"},
                    "tool_response": {"success": True, "taskId": "7", "updatedFields": ["status"]}})
        self._hook({"tool_name": "TaskUpdate", "agent_id": "agent-43",
                    "tool_input": {"taskId": "8", "status": "completed"},
                    "tool_response": {"success": True, "taskId": "8", "updatedFields": ["status"]}})
        tw = self._reg().get("taskWrites")
        self.assertEqual([(w["taskId"], w["agentId"]) for w in tw],
                         [("7", "agent-42"), ("8", "agent-43")])
        self.assertTrue(self.pokes, "the store is authoritative — the hook says re-read it NOW")

    def test_the_write_tail_is_capped(self):
        for i in range(20):
            self._hook({"tool_name": "TaskCreate", "tool_input": {},
                        "tool_response": {"taskId": str(i)}})
        self.assertEqual(len(self._reg().get("taskWrites")), self.sess._TASK_WRITES_CAP)


class PushMirror(_Backend):
    def test_a_suppressed_push_is_still_recorded(self):
        self._hook({"tool_name": "PushNotification",
                    "tool_input": {"message": "come look at the failing build", "status": "proactive"},
                    "tool_response": {"message": "come look at the failing build",
                                      "pushSent": False, "localSent": False}})
        notes = self._reg().get("pushNotes")
        self.assertEqual(len(notes), 1)
        self.assertFalse(notes[0]["pushSent"], "suppressed — exactly the case that was silently lost")
        self.assertTrue(self.pokes)

    def test_the_note_tail_is_capped(self):
        for i in range(15):
            self._hook({"tool_name": "PushNotification",
                        "tool_input": {"message": "note %d" % i},
                        "tool_response": {"pushSent": True, "localSent": False}})
        notes = self._reg().get("pushNotes")
        self.assertEqual(len(notes), self.sess._PUSH_NOTES_CAP)
        self.assertEqual(notes[-1]["message"], "note 14")


class SkillTelemetry(_Backend):
    def test_the_invoked_skill_lands_on_the_reg(self):
        self._hook({"tool_name": "Skill", "tool_input": {"skill": "worker"},
                    "tool_response": {"success": True, "commandName": "worker"}})
        self.assertEqual(self._reg().get("lastSkill", {}).get("name"), "worker")

    def test_a_subagents_skill_never_clobbers_the_sessions_role(self):
        # the drafting pipeline: a session acting as manager spawns a jld subagent (review 2026-08-29)
        self._hook({"tool_name": "Skill", "tool_input": {"skill": "manager"},
                    "tool_response": {"success": True, "commandName": "manager"}})
        self._hook({"tool_name": "Skill", "agent_id": "agent-7", "tool_input": {"skill": "jld"},
                    "tool_response": {"success": True, "commandName": "jld"}})
        self.assertEqual(self._reg().get("lastSkill", {}).get("name"), "manager")

    def test_a_missing_commandName_records_nothing_and_says_so(self):
        # ONE authority: the resolved commandName — the requested alias would be a silent degrade
        self._hook({"tool_name": "Skill", "tool_input": {"skill": "deploy"},
                    "tool_response": {"success": True}})
        self.assertIsNone(self._reg().get("lastSkill"))

    def test_a_failed_or_shapeless_ack_records_nothing(self):
        self._hook({"tool_name": "Skill", "tool_input": {}, "tool_response": "opaque"})
        self.assertIsNone(self._reg().get("lastSkill"))


if __name__ == "__main__":
    unittest.main()
