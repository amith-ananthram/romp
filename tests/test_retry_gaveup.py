#!/usr/bin/env python3
"""A retry storm that EXHAUSTS must record a GIVE-UP, never a recovery (the user 2026-07-25).

The CLI settles a dead turn by writing its error as an assistant message ("API Error: 529 Overloaded…")
stamped with `error` (the SDK's designed flag; the transcript twin is isApiErrorMessage). The backend
treated ANY AssistantMessage during a storm as "real output resumed" — so a 10-attempt storm that
produced nothing left a durable "Recovered after 10 retries" note in the chat, the opposite of what
happened. Now an error-stamped message writes a retriesGaveUp marker instead, the kernel interleaves it
as a red "gave up after N retries" note, and the error record itself renders as durable api-error chrome
(kind apiErrorNote) rather than an agent bubble. SYNTHETIC fixtures only."""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_gaveup", os.path.join(BIN, "romp_sdk_backend.py"))
km = load_source("romp_kernel_gaveup", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class FakeAssistantMessage:
    """Duck-typed stand-in; _on_message matches it via the class object we pass in, and msg_to_atom
    matches on the class NAME."""
    def __init__(self, error=None, content=None):
        self.error = error
        self.content = content or []
        self.model = ""            # falsy → the branch skips _learn_model
        self.uuid = "aa11"


AssistantMessage = FakeAssistantMessage
AssistantMessage.__name__ = "AssistantMessage"


class FakeResultMessage:
    pass


class FakeSystemMessage:
    pass


def _session(be):
    s = object.__new__(sb.SdkSession)
    s.backend = be
    s.sid = SID
    s.resume_sid = None
    s._skill_tool_ids = set()
    s.retrying = True
    s.retry_count = 10
    s.retry_info = {"attempt": 10}
    s._cli_working = True
    return s


def _marker_lines(be, key):
    p = be.state_dir / "states" / (SID + ".jsonl")
    if not p.exists():
        return []
    return [json.loads(l) for l in open(p) if key in l]


class GaveUpNotRecovered(unittest.TestCase):
    def _backend(self):
        return sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)

    def test_error_stamped_settle_writes_gave_up_never_recovered(self):
        be = self._backend()
        s = _session(be)
        s._on_message(FakeAssistantMessage(error="server_error"),
                      AssistantMessage, FakeResultMessage, FakeSystemMessage)
        self.assertEqual(_marker_lines(be, '"retriesRecovered"'), [],
                         "an error settle must never mint a recovery note")
        gu = _marker_lines(be, '"retriesGaveUp"')
        self.assertEqual(len(gu), 1)
        self.assertEqual(gu[0]["retriesGaveUp"], 10)
        self.assertEqual(gu[0]["errorKind"], "server_error")
        self.assertFalse(s.retrying)                 # the storm is over either way
        self.assertEqual(s.retry_count, 0)

    def test_real_output_still_records_the_recovery(self):
        be = self._backend()
        s = _session(be)
        s._on_message(FakeAssistantMessage(error=None),
                      AssistantMessage, FakeResultMessage, FakeSystemMessage)
        self.assertEqual(_marker_lines(be, '"retriesGaveUp"'), [])
        rec = _marker_lines(be, '"retriesRecovered"')
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["retriesRecovered"], 10)

    def test_no_storm_no_marker_either_way(self):
        be = self._backend()
        s = _session(be)
        s.retrying, s.retry_count = False, 0
        s._on_message(FakeAssistantMessage(error="server_error"),
                      AssistantMessage, FakeResultMessage, FakeSystemMessage)
        self.assertEqual(_marker_lines(be, '"retriesGaveUp"'), [],
                         "a lone hard error without a storm is covered by the error record itself")


class LiveAtomTagging(unittest.TestCase):
    def test_msg_to_atom_tags_the_error_settle(self):
        class TextBlock:                              # _block_to_dict matches on the class NAME
            text = "API Error: 529 Overloaded"
        Blk = TextBlock
        a = sb.msg_to_atom(FakeAssistantMessage(error="server_error", content=[Blk()]), SID, None, 100)
        self.assertTrue(a and a.get("isApiError"))
        b = sb.msg_to_atom(FakeAssistantMessage(error=None, content=[Blk()]), SID, None, 100)
        self.assertTrue(b and not b.get("isApiError"))

    def test_error_settle_atom_is_never_orphaned_as_a_lost_reply(self):
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        be._live[SID] = {"e1": {"type": "assistant", "uuid": "e1", "session_id": SID, "t": 100,
                                "isApiError": True,
                                "message": {"role": "assistant",
                                            "content": [{"type": "text", "text": "API Error: 529"}]}}}
        be.retire_live_work(SID)
        self.assertEqual(_marker_lines(be, '"orphanReply"'), [],
                         "the error text is not a reply the user lost — it must not resurrect as one")

    def test_forward_does_not_reassert_working_for_an_error_atom(self):
        src = inspect.getsource(sb.SdkBackend._forward)
        self.assertIn('not atom.get("isApiError")', src,
                      "an isApiError settle is the turn dying, not the CLI producing")


class KernelReaderAndInterleave(unittest.TestCase):
    def test_write_then_read_oldest_first_with_kind(self):
        sid = "TESTHOST-gaveup-1"
        (km.jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "states" / (sid + ".jsonl")).unlink(missing_ok=True)
        sb.append_retry_gave_up(km.jd.STATE, sid, 3, kind="rate_limit", t=2000)
        sb.append_retry_gave_up(km.jd.STATE, sid, 10, kind="server_error", t=1000)
        self.assertEqual(km._retry_gaveups(sid),
                         [{"t": 1000, "retries": 10, "errorKind": "server_error"},
                          {"t": 2000, "retries": 3, "errorKind": "rate_limit"}])

    def test_zero_or_missing_is_skipped_and_recovery_reader_is_undisturbed(self):
        sid = "TESTHOST-gaveup-2"
        p = km.jd.STATE / "states" / (sid + ".jsonl")
        p.unlink(missing_ok=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"t": 10, "retriesGaveUp": 0}) + "\n")
        self.assertEqual(km._retry_gaveups(sid), [])
        sb.append_retry_recovered(km.jd.STATE, sid, 4, t=20)
        self.assertEqual(km._retry_gaveups(sid), [])          # keyed readers stay disjoint
        self.assertEqual(km._retry_recoveries(sid), [{"t": 20, "retries": 4}])

    def test_build_session_interleaves_the_gave_up_note_and_the_error_card(self):
        src = inspect.getsource(km.build_session)
        self.assertIn("gaveups = _past_floor(_retry_gaveups(sid))", src)   # floored at the episode boundary since T131
        self.assertIn('"kind": "retryGaveUp", "retries": _g["retries"]', src)
        # the transcript's isApiError record becomes durable error CHROME, not an agent bubble
        self.assertIn('events.append({"kind": "apiErrorNote", "md": txt,', src)
        # …and is dropped while the LIVE blocked card (with the buttons) shows the same record
        self.assertIn('ev.get("kind") == "apiErrorNote" and ev.get("uuid") == aerr.get("uuid")', src)


if __name__ == "__main__":
    unittest.main()
