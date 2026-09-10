#!/usr/bin/env python3
"""An SDK-backed session's in-progress human ask must get a PROMPT-run unit so it is PLACED while the turn
is open — otherwise the kernel's dotted provisional placeholder never drops (the user 2026-06-29).

The bug: in an SDK session the human's composer input lands in the transcript as promptSource "sdk", which
author_of maps to "human" only when sdk_human is set. The KERNEL parses SDK sessions with sdk_human=True, so
_provisional_card sees a human prompt and shows the dotted placeholder. The JUDGE did NOT, so _seg_human was
False and plan_units emitted no PROMPT-run unit for the open final segment — the ask was never placed while
the turn was open, and that placement is the placeholder's only drop gate. So the provisional card stuck for
the whole open turn (forever if the turn read as 'working' indefinitely), and each new message just rendered a
fresh provisional. The fix: parsed_session detects SDK ownership (the SDK registry file) and parses with
sdk_human=True, mirroring the kernel. Synthetic transcript only — placeholder UUIDs, no real data.
"""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_sdkhuman", os.path.join(BIN, "romp-judge"))

SDK_SID = "11111111-2222-3333-4444-555555555555"
TMUX_SID = "99999999-8888-7777-6666-555555555555"


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class SdkHumanPromptRun(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def _open_human_turn(self, sid):
        """A transcript with ONE in-progress human prompt over the SDK channel (promptSource 'sdk'), no
        assistant reply yet → the turn is open and its final segment is the held human ask."""
        p = os.path.join(self.td, sid + ".jsonl")
        now = int(time.time())
        rec = {"type": "user", "timestamp": _iso(now - 3), "uuid": "u1", "parentUuid": None,
               "promptSource": "sdk",
               "message": {"role": "user", "content": "Please refactor the auth module"}}
        open(p, "w").write(json.dumps(rec) + "\n")
        return p, now

    def _prompt_units(self, sid, path, now):
        session = jd.parsed_session(sid, [path], now)
        return [u for u in jd.plan_units(session) if u[1] == "prompt"]

    def test_sdk_session_open_ask_gets_a_prompt_run_unit(self):
        # mark the session SDK-backed exactly as the SDK backend does: a registry file under STATE/sdk/
        reg = Path(self.td) / "sdk" / (SDK_SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"sid": SDK_SID, "alive": True}))
        self.assertTrue(jd._sdk_owned(SDK_SID))
        path, now = self._open_human_turn(SDK_SID)
        units = self._prompt_units(SDK_SID, path, now)
        self.assertEqual(len(units), 1,
                         "an SDK session's open human ask must yield a PROMPT-run unit so it gets placed "
                         "and the provisional placeholder drops")
        self.assertTrue(units[0][4], "the prompt unit is flagged human")

    def test_regression_without_sdk_registry_no_prompt_run(self):
        # The same promptSource-"sdk" prompt in a NON-SDK session is a genuine programmatic injection, not the
        # human → no prompt-run (and the kernel shows no provisional for it either, so they stay consistent).
        self.assertFalse(jd._sdk_owned(TMUX_SID))
        path, now = self._open_human_turn(TMUX_SID)
        units = self._prompt_units(TMUX_SID, path, now)
        self.assertEqual(units, [], "a non-SDK promptSource-'sdk' prompt is not the human → no PROMPT-run unit")


if __name__ == "__main__":
    unittest.main()
