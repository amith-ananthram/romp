#!/usr/bin/env python3
"""Field-level reg gutting (2026-09-01, from the relay round's audit): several hook-side RMWs read
the reg via `read_reg(...) or {}`, so a TRANSIENT read failure (the reg exists but would not read
this instant) yielded a writable empty — and the write half then persisted the wipe, silently
erasing exactly the fields the hook-ledger rounds made load-bearing (bgLedger/bgLedgerEnded for
Monitor deadlines and task attribution; pushNotes; taskWrites; sessionCrons, the armed-timer set).

The fix is read_reg_for_rmw: {} only when the reg GENUINELY does not exist (a fresh session — an
empty base is correct), None when it exists but is unreadable — and a None caller skips its write,
loudly. The field-sized sibling of _update_reg's whole-reg guard. Every converted site is driven
here against a CORRUPT reg file and pinned: the file's bytes survive untouched. Synthetic only."""
import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_gut", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-000000000099"
CORRUPT = b'{"sid": "trunca'          # a torn read: exists, does not parse


class ReadRegForRmw(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_absent_reg_is_a_writable_empty(self):
        self.assertEqual(sb.read_reg_for_rmw(self.d, SID), {},
                         "a fresh session's first record starts from a genuinely empty base")

    def test_unreadable_reg_is_a_skip_signal_never_an_empty(self):
        p = sb._reg_path(self.d, SID)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(CORRUPT)
        self.assertIsNone(sb.read_reg_for_rmw(self.d, SID),
                          "exists-but-unreadable must never yield a base the caller would persist")

    def test_healthy_reg_reads_through(self):
        sb.write_reg(self.d, SID, {"sid": SID, "bgLedger": [{"tid": "t1"}]})
        self.assertEqual(sb.read_reg_for_rmw(self.d, SID)["bgLedger"], [{"tid": "t1"}])


class _Hooked(unittest.TestCase):
    """A real SdkBackend + SdkSession whose reg is corrupted mid-flight; each hook is driven and
    the file's bytes pinned unchanged — the write was skipped, the field survived the window."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "gut", "cwd": "/tmp", "alive": True,
                                         "bgLedger": [{"tid": "keep-me", "tool": "bash",
                                                       "procGen": "g0", "toolUseId": "tu0"}],
                                         "pushNotes": [{"at": 1, "message": "keep"}],
                                         "taskWrites": [{"at": 1, "tool": "TaskCreate"}],
                                         "sessionCrons": [{"id": "c1", "cron": "0 * * * *",
                                                           "recurring": True}]})
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self._path = sb._reg_path(Path(self.d), SID)

    def _corrupt(self):
        self._path.write_bytes(CORRUPT)

    def _pin_untouched(self, why):
        self.assertEqual(self._path.read_bytes(), CORRUPT,
                         "%s: the skip must leave the file for the healed read — any write here "
                         "either guts the reg or wipes the field" % why)


class HooksSkipOnUnreadable(_Hooked):
    def test_the_launch_hook_skips(self):
        self._corrupt()
        asyncio.run(self.s._ledger_tool_hook(
            {"tool_name": "Bash", "tool_input": {"run_in_background": True, "description": "x"},
             "tool_response": {"shellId": "b1"}}, "tu9", None))
        self._pin_untouched("bg-ledger launch hook")

    def test_the_fail_hook_skips(self):
        self._corrupt()
        asyncio.run(self.s._ledger_fail_hook({"tool_use_id": "tu0"}, "tu0", None))
        self._pin_untouched("bg-ledger fail hook")

    def test_the_stop_hook_reconciler_skips_and_says_so(self):
        self._corrupt()
        asyncio.run(self.s._stop_hook({"background_tasks": []}, None, None))
        # the stop hook also writes lastStopAt through _update_reg, whose own whole-reg guard
        # refuses the unreadable file — so the bytes stay exactly as corrupted
        self._pin_untouched("stop-hook ledger reconcile")
        self.assertTrue(any("unreadable" in m for m in self.logs), "the skip is loud, never silent")

    def test_the_session_crons_recorder_skips(self):
        self._corrupt()
        asyncio.run(self.s._stop_hook({"session_crons": []}, None, None))
        self._pin_untouched("session_crons recorder")
        # the empty payload would have ERASED the armed set had the base read as {} — heal the
        # file and prove the recurring cron survived the window
        sb.write_reg(Path(self.d), SID, json.loads(json.dumps(
            {"sid": SID, "sessionCrons": [{"id": "c1", "cron": "0 * * * *", "recurring": True}]})))
        self.assertTrue(sb.read_reg(Path(self.d), SID)["sessionCrons"])

    def test_the_sched_toolhook_skips(self):
        self._corrupt()
        asyncio.run(self.s._sched_tool_hook(
            {"tool_name": "ScheduleWakeup", "tool_input": {"delaySeconds": 300, "prompt": "wake"}},
            "tu1", None))
        self._pin_untouched("sched tool hook")

    def test_the_facts_hook_skips_for_taskwrites_and_pushnotes(self):
        self._corrupt()
        asyncio.run(self.s._facts_tool_hook(
            {"tool_name": "PushNotification", "tool_input": {"message": "m"},
             "tool_response": {"pushSent": True}}, "tu2", None))
        self._pin_untouched("pushNotes recorder")
        asyncio.run(self.s._facts_tool_hook(
            {"tool_name": "TaskCreate", "tool_input": {"subject": "s"},
             "tool_response": {"taskId": "1"}}, "tu3", None))
        self._pin_untouched("taskWrites recorder")

    def test_a_healthy_reg_still_records_everything(self):
        # the guard refuses FAILURES, never work: the same drives against the intact reg land
        asyncio.run(self.s._ledger_tool_hook(
            {"tool_name": "Bash", "tool_input": {"run_in_background": True, "description": "x"},
             "tool_response": {"shellId": "b1"}}, "tu9", None))
        asyncio.run(self.s._facts_tool_hook(
            {"tool_name": "PushNotification", "tool_input": {"message": "m"},
             "tool_response": {"pushSent": True}}, "tu2", None))
        reg = sb.read_reg(Path(self.d), SID)
        self.assertEqual(len(reg["bgLedger"]), 2, "the launch recorded beside the seeded entry")
        self.assertEqual(len(reg["pushNotes"]), 2, "the note appended — nothing wiped, nothing lost")


if __name__ == "__main__":
    unittest.main()
