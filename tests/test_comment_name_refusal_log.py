#!/usr/bin/env python3
"""T289 (the user 2026-09-09): a thread-NAME refusal at any door is a kernel log line, not only a toast.

A comment on a remote session was refused as a create-by-name and neither kernel logged it: the create
door answered the browser with a warn frame and a typed nack and wrote nothing, so the refusing kernel's
log held no trace of the event the user saw. Every door that refuses a NAME (the comment create, the
break-out, the rename) now also writes one stderr line naming the door, the session and the refusal.
The handlers' frames are unchanged. SYNTHETIC fixtures (the test_comment_threads.py conventions)."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT = "aaaaaaaa-1111-2222-3333-444444444444"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


class FakeBackend:
    def __init__(self):
        self.calls = []

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="", model="", effort="", fast=""):
        self.calls.append(("fork", name)); return sid

    def connect(self, sid):
        return True

    def send(self, sid, text):
        return True

    def kill(self, sid):
        return True

    def rename(self, sid, name):
        self.calls.append(("rename", name)); return True

    def promote_thread(self, sid, name, bg="", fg=""):
        self.calls.append(("promote", name)); return True


class NameRefusalsAreLogged(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_msgs_cache.clear()
        self.now = int(time.time())
        cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / PARENT).write_text("web\t%s\t\t\n" % cdir)   # the names row: the refusal names the parent by it
        self.be = FakeBackend()
        self._saved_fns = (km._sdk, km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for,
                           km._push_session_now, km._tmux_sessions, km._kernel_knows)
        km._sdk = lambda: None
        km._kernel_knows = lambda sid: sid == PARENT     # the dispatcher's ownership guard is not under test
        self._saved_km_names = km.NAMES
        km.NAMES = jd.NAMES                              # the kernel's own binding of the names dir follows the rebind
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._sdk_ready = lambda: True
        km._tmux_sessions = lambda: {}
        t = self.now - 600
        p = self.proj / (PARENT + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in [
            uline(t, "how should the notes-api retry loop back off?", "u1"),
            aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1")]) + "\n")
        km._sessions = lambda now, window=None, forks=True: [
            {"sid": PARENT, "name": "web", "path": str(p), "mtime": self.now}]
        km._reveal_chat_for = lambda client, msg: None
        km._push_session_now = lambda sid: None
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s)), "app": "chat"}

    def tearDown(self):
        (km._sdk, km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for,
         km._push_session_now, km._tmux_sessions, km._kernel_knows) = self._saved_fns
        km.NAMES = self._saved_km_names
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _drive(self, msg):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            handled = km._drive(msg, self.client)
        return handled, buf.getvalue()

    def _warns(self):
        return [f["text"] for f in self.sent if f.get("type") == "warn"]

    def test_a_create_under_a_taken_thread_name_is_refused_aloud_and_logged(self):
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why jitter at all?")
        self.assertIsNone(err)
        taken = km._comment_thread(PARENT, tid)["name"]
        self.assertEqual(taken, "web-comment-1", "the kernel's own default: bare name, next number")
        handled, log = self._drive({"type": "commentCreate", "id": PARENT, "uuid": "a1", "exact": "exponential backoff",
                                    "text": "and the cap?", "name": taken})
        self.assertTrue(handled)
        self.assertTrue(any("is a comment thread of web" in w for w in self._warns()), self.sent)
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 1)
        self.assertFalse(nacks[0]["transient"])
        self.assertIn("comment create refused", log, "the kernel log names the event the user saw")
        self.assertIn(PARENT[:8], log)
        self.assertIn(taken, log)

    def test_an_empty_name_takes_the_kernels_default_past_the_taken_one(self):
        # the client's fix sends "" for an untouched prefill: the kernel numbers past every taken name
        _, tid1 = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        handled, log = self._drive({"type": "commentCreate", "id": PARENT, "uuid": "a1", "exact": "exponential backoff",
                                    "text": "and the cap?", "name": ""})
        self.assertTrue(handled)
        self.assertEqual(self._warns(), [])
        acks = [f for f in self.sent if f.get("type") == "commentCreated"]
        self.assertEqual(len(acks), 1)
        self.assertEqual(km._comment_thread(PARENT, acks[0]["tid"])["name"], "web-comment-2")
        self.assertNotIn("refused", log)

    def test_a_break_out_under_a_taken_name_is_logged(self):
        _, tid1 = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        _, tid2 = km._comment_create(PARENT, "a1", "exponential backoff", "And?")
        taken = km._comment_thread(PARENT, tid1)["name"]
        handled, log = self._drive({"type": "commentPromote", "id": PARENT, "tid": tid2, "name": taken})
        self.assertTrue(handled)
        self.assertTrue(self._warns(), self.sent)
        self.assertIn("comment promote refused", log)
        self.assertIn(taken, log)

    def test_a_rename_onto_a_thread_name_is_logged(self):
        _, tid1 = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        taken = km._comment_thread(PARENT, tid1)["name"]
        handled, log = self._drive({"type": "renameSession", "id": PARENT, "name": taken})
        self.assertTrue(handled)
        self.assertTrue(any("is a comment thread of web" in w for w in self._warns()), self.sent)
        self.assertIn("rename refused", log)
        self.assertIn(taken, log)


if __name__ == "__main__":
    unittest.main()
