#!/usr/bin/env python3
"""Orphan-reply durability (the user 2026-07-21): when a turn hits an API error, the CLI discards the partial
reply it was streaming and (on retry) writes a fresh record with a new uuid — so the reply the user WATCHED
appear is on disk nowhere, and retire_live_work drops the live atom at settle, leaving only the "Recovered
after N retries" note in its place. retire_live_work now PERSISTS a text-bearing live assistant reply as a
durable orphan marker before dropping it, so build_session can interleave the lost text back. SYNTHETIC only."""
import json
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
sb = load_source("romp_sdk_backend_orphan", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"


def _atom(uuid, t, blocks, **extra):
    a = {"type": "assistant", "uuid": uuid, "session_id": SID, "t": t,
         "message": {"role": "assistant", "content": blocks}}
    a.update(extra)
    return a


class OrphanReplyDurability(unittest.TestCase):
    def _backend(self):
        return sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)

    def _orphan_lines(self, be):
        p = be.state_dir / "states" / (SID + ".jsonl")
        if not p.exists():
            return []
        return [json.loads(l) for l in open(p) if '"orphanReply"' in l]

    def test_a_text_bearing_reply_is_persisted_then_dropped(self):
        be = self._backend()
        be._live[SID] = {"u1": _atom("u1", 100, [{"type": "text", "text": "the reply you watched stream"}])}
        be.retire_live_work(SID)
        self.assertNotIn(SID, be._live)                        # the live atom is dropped at settle, as before
        orq = self._orphan_lines(be)
        self.assertEqual(len(orq), 1)
        self.assertEqual(orq[0]["orphanReply"]["text"], "the reply you watched stream")
        self.assertEqual(orq[0]["orphanReply"]["uuid"], "u1")
        self.assertEqual(orq[0]["t"], 100)

    def test_a_tool_or_thinking_work_atom_is_not_persisted(self):
        be = self._backend()
        be._live[SID] = {"t1": _atom("t1", 100, [{"type": "tool_use", "name": "Bash", "input": {}}]),
                         "th": _atom("th", 101, [{"type": "thinking", "thinking": "hmm"}])}
        be.retire_live_work(SID)
        self.assertEqual(self._orphan_lines(be), [])           # only real TEXT is worth keeping

    def test_the_api_error_record_itself_is_never_kept_as_a_reply(self):
        # the isApiError record carries the error TEXT but is not the reply — never persist it as one
        be = self._backend()
        be._live[SID] = {"e": _atom("e", 100, [{"type": "text", "text": "API Error: 500"}], isApiError=True)}
        be.retire_live_work(SID)
        self.assertEqual(self._orphan_lines(be), [])

    def test_an_input_echo_is_untouched_and_never_orphaned(self):
        be = self._backend()
        echo = {"type": "user", "uuid": "k", "t": 100, "_echo_text": "my send",
                "message": {"role": "user", "content": [{"type": "text", "text": "my send"}]}}
        be._live[SID] = {"k": echo}
        be.retire_live_work(SID)
        self.assertIn("k", be._live.get(SID, {}))              # echoes survive retire (not work)
        self.assertEqual(self._orphan_lines(be), [])           # and are never orphan-persisted

    def test_append_orphan_reply_caps_the_text(self):
        be = self._backend()
        sb.append_orphan_reply(be.state_dir, SID, "u9", "x" * (sb.ORPHAN_REPLY_CAP + 500), t=5)
        orq = self._orphan_lines(be)
        self.assertEqual(len(orq[0]["orphanReply"]["text"]), sb.ORPHAN_REPLY_CAP)

    def test_the_cli_no_content_placeholder_is_never_persisted(self):
        # "(no content)" is the CLI's placeholder for contentless command feedback (an SDK /clear
        # streams one; its transcript record is a system/local_command row). Persisted as an orphan
        # it resurfaced as a WORKED reply on the bare command turn, and the planner minted a card
        # for the /clear itself (the user 2026-07-27).
        be = self._backend()
        be._live[SID] = {"c": _atom("c", 100, [{"type": "text", "text": "(no content)"}])}
        be.retire_live_work(SID)
        self.assertEqual(self._orphan_lines(be), [])


if __name__ == "__main__":
    unittest.main()
