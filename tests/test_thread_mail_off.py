#!/usr/bin/env python3
"""A comment thread's mail is OFF by default, both directions, until the user breaks it out (T356, the user 2026-09-11:
a comment thread of a manager session received the manager's mail, mailed two of its workers and merged a pull
request as if it were the manager). The kernel's effective isolation reader derives the default from the thread's
reg (threadOf), so a thread already on disk with no flag reads OFF; the fresh key `threadMail` at the literal True is
the one way on short of a break-out (never an old key re-read); promotion clears threadOf and the ordinary rule
returns. Synthetic fixtures only: placeholder UUIDs, a hermetic state root."""
import inspect
import json
import os
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_thread_mail", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_thread_mail", os.path.join(BIN, "romp_sdk_backend.py"))

PARENT = "11111111-2222-3333-4444-555555555555"
THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa"
PLAIN = "aaaaaaaa-1111-2222-3333-444444444444"


class ThreadMailOff(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.saved = km.jd.STATE
        km.jd._rebind_state(Path(self.td))
        km._thread_reg_memo.clear()
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.be.spawn("parent", self.td, sid=PARENT)
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)

    def tearDown(self):
        km.jd._rebind_state(self.saved)
        km._thread_reg_memo.clear()
        shutil.rmtree(self.td, ignore_errors=True)

    def _flags(self, flags):
        (Path(self.td) / "session-flags.json").write_text(json.dumps(flags))
        km._session_flags.cache_clear() if hasattr(km._session_flags, "cache_clear") else None

    def test_a_new_thread_reads_mail_off_with_no_flag_on_disk(self):
        self.assertTrue(km._thread_mail_off(THREAD)); self.assertTrue(km._postal_isolated(THREAD))
        self.assertTrue(km._painted_flag_value(THREAD, "postalServiceOff"), "the display paints the default too")
        self.assertFalse(km._postal_isolated(PARENT), "the session it belongs to is untouched")

    def test_the_fresh_key_at_the_literal_true_is_the_one_way_on(self):
        self._flags({THREAD: {"threadMail": True}})
        self.assertFalse(km._thread_mail_off(THREAD)); self.assertFalse(km._postal_isolated(THREAD))
        for v in ("true", 1, "on", [True]):
            self._flags({THREAD: {"threadMail": v}})
            self.assertTrue(km._postal_isolated(THREAD), "%r is not the literal True (the flip-a-default rule)" % (v,))
        self._flags({THREAD: {"postalServiceOff": False}})
        self.assertTrue(km._postal_isolated(THREAD), "the mailbox flag at False is not a way on for a thread")
        self._flags({THREAD: {"threadMail": True, "postalServiceOff": True}})
        self.assertTrue(km._postal_isolated(THREAD), "mail on for the thread, then the user's own isolation still holds")

    def test_breaking_out_flips_mail_on_by_default(self):
        self.assertTrue(km._postal_isolated(THREAD))
        self.assertTrue(self.be.promote_thread(THREAD, "web-2"))
        km._thread_reg_memo.clear()
        self.assertFalse(km._thread_mail_off(THREAD), "no threadOf: not a thread any more")
        self.assertFalse(km._postal_isolated(THREAD), "a promoted session's mail is on by default")
        self._flags({THREAD: {"postalServiceOff": True}})
        self.assertTrue(km._postal_isolated(THREAD), "…and follows the ordinary mailbox toggle from then on")

    def test_an_ordinary_session_keeps_the_ordinary_rule(self):
        self.be.spawn("plain", self.td, sid=PLAIN)
        self.assertFalse(km._postal_isolated(PLAIN))
        self._flags({PLAIN: {"postalOff": True}})
        self.assertTrue(km._postal_isolated(PLAIN), "the legacy key still isolates")
        self.assertFalse(km._thread_mail_off(""), "no sid: not a thread")

    def test_the_frames_carry_the_effective_state(self):
        # the comments frame says mailOff per thread; the /sessions thread rows and the Sessions pane rows carry the
        # effective postalServiceOff; the timeline lane and the chat rows read the effective reader too
        src = inspect.getsource(km._comments_frame)
        self.assertIn('"mailOff": bool(_postal_isolated(tsid))', src)
        rows = inspect.getsource(km._thread_rows)
        self.assertIn('"postalServiceOff": _postal_isolated(tsid)', rows); self.assertIn('"mailOffWhy": "thread" if _thread_mail_off(tsid) else ""', rows)
        whole = Path(os.path.join(BIN, "romp-kernel")).read_text()
        self.assertEqual(whole.count('"postalServiceOff": _postal_isolated('), 4, "chat rows, timeline lanes, thread rows, Sessions pane rows")
        self.assertNotIn('"postalServiceOff": _session_flag(sid, "postalServiceOff")', whole, "no row reads the raw flag past the effective reader")


if __name__ == "__main__":
    unittest.main()
