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
        self.assertIn('"postalServiceOff": _postal_isolated(tsid)', rows); self.assertIn('"mailOffWhy": _mail_off_why_k(tsid)', rows)
        whole = Path(os.path.join(BIN, "romp-kernel")).read_text()
        self.assertEqual(whole.count('"postalServiceOff": _postal_isolated('), 4, "chat rows, timeline lanes, thread rows, Sessions pane rows")
        self.assertNotIn('"postalServiceOff": _session_flag(sid, "postalServiceOff")', whole, "no row reads the raw flag past the effective reader")


class HeldMail(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(); self.saved = km.jd.STATE; km.jd._rebind_state(Path(self.td))

    def tearDown(self):
        km.jd._rebind_state(self.saved); shutil.rmtree(self.td, ignore_errors=True)

    def test_the_held_count_is_the_boxs_unread_mail_and_zero_without_a_box(self):
        self.assertEqual(km._held_mail_count(THREAD), 0, "no box")
        box = Path(self.td) / "postal" / "mail" / THREAD / "new"; box.mkdir(parents=True)
        for i in range(3):
            (box / ("m%d" % i)).write_text("From: peer\n\nhello\n")
        self.assertEqual(km._held_mail_count(THREAD), 3)
        self.assertIn('"heldMail": _held_mail_count(tsid)', inspect.getsource(km._comments_frame), "the frame carries it beside mailOff")


class UnreadableRecordOnTheKernelSide(unittest.TestCase):
    """The bus holds every message for a session whose record cannot be read; the kernel must not paint mail as on for
    it (the review's low): the same closed door, with its own reason on the rows."""

    def setUp(self):
        self.td = tempfile.mkdtemp(); self.saved = km.jd.STATE; km.jd._rebind_state(Path(self.td)); km._thread_reg_memo.clear()
        (Path(self.td) / "sdk").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        km.jd._rebind_state(self.saved); km._thread_reg_memo.clear(); shutil.rmtree(self.td, ignore_errors=True)

    def test_the_kernel_derives_unreadable_thread_and_isolation_with_their_reasons(self):
        self.assertEqual(km._mail_off_why_k(PLAIN), "", "no record: an ordinary session, mail on")
        (Path(self.td) / "sdk" / (PLAIN + ".json")).write_text("{corrupt")
        self.assertTrue(km._reg_unreadable(PLAIN)); self.assertEqual(km._mail_off_why_k(PLAIN), "unreadable"); self.assertTrue(km._postal_isolated(PLAIN))
        (Path(self.td) / "sdk" / (PLAIN + ".json")).write_text(json.dumps(["not", "a", "dict"]))
        self.assertEqual(km._mail_off_why_k(PLAIN), "unreadable", "…whatever shape the corruption takes")
        (Path(self.td) / "sdk" / (THREAD + ".json")).write_text(json.dumps({"sid": THREAD, "threadOf": PARENT, "alive": True}))
        self.assertEqual(km._mail_off_why_k(THREAD), "thread")
        (Path(self.td) / "sdk" / (PARENT + ".json")).write_text(json.dumps({"sid": PARENT, "alive": True}))
        (Path(self.td) / "session-flags.json").write_text(json.dumps({PARENT: {"postalOff": True}}))
        self.assertEqual(km._mail_off_why_k(PARENT), "isolation", "the legacy key still isolates, under its reason")
        whole = Path(os.path.join(BIN, "romp-kernel")).read_text()
        self.assertEqual(whole.count('"mailOffWhy": _mail_off_why_k('), 4, "chat rows, thread rows, Sessions pane rows and the comments frame carry the reason")
        self.assertIn('"mailOffWhy": _mail_off_why_k(tsid)', inspect.getsource(km._comments_frame), "the promoted popover reads the reason")

    def test_the_unreadable_check_reads_the_registration_memo_not_the_file(self):
        # the review's low: a sweep re-read and re-parsed every record; the memo (mtime, size, inode) answers now
        (Path(self.td) / "sdk" / (PLAIN + ".json")).write_text(json.dumps({"sid": PLAIN, "alive": True}))
        km._thread_reg_memo.clear()
        self.assertFalse(km._reg_unreadable(PLAIN))
        self.assertIn(PLAIN, km._thread_reg_memo, "the read went through the memo")
        src = inspect.getsource(km._reg_unreadable)
        self.assertIn("return not _thread_reg(str(sid))", src); self.assertNotIn("json.loads", src, "no parse of its own")
        (Path(self.td) / "sdk" / (PLAIN + ".json")).write_text("{corrupt")
        self.assertTrue(km._reg_unreadable(PLAIN), "a rewrite is a new memo key: the corrupt record reads unreadable")
        if os.geteuid() != 0:
            os.chmod(Path(self.td) / "sdk", 0)
            try:
                self.assertTrue(km._reg_unreadable(PLAIN), "a directory the kernel cannot read: closed")
            finally:
                os.chmod(Path(self.td) / "sdk", 0o755)


if __name__ == "__main__":
    unittest.main()
