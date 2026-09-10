#!/usr/bin/env python3
"""T289: a comment CREATE is idempotent on its own identity (parent, anchor, passage, text).

A create parked for transcript lag is retried by BOTH the kernel (the pusher's _retry_parked_creates) and
the client (its frame-keyed re-post), and a create whose ack was lost is sent again by the popover; before
this the second copy either collided on its explicit name and was refused with the create toast the user
saw, or, with the name now left to the kernel, would have minted a SECOND thread for one comment. The
kernel remembers each create it completed by (parent sid, anchor uuid, passage, text) and answers a repeat
with the SAME thread's ack; a lag-parked create is parked once. SYNTHETIC fixtures."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
SourceFileLoader("romp_event_model", os.path.join(BIN, "romp-event-model")).load_module()
jd = SourceFileLoader("romp_judge", os.path.join(BIN, "romp-judge")).load_module()
km = SourceFileLoader("romp_kernel", os.path.join(BIN, "romp-kernel")).load_module()

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


class CreateIsIdempotent(unittest.TestCase):
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

    def _create(self, text="Why jitter at all?", uuid="a1", name=""):
        return self._drive({"type": "commentCreate", "id": PARENT, "uuid": uuid, "exact": "exponential backoff",
                            "text": text, "name": name})

    def _rows(self):
        return km._load_comments(PARENT).get("threads") or []

    def _acks(self):
        return [f for f in self.sent if f.get("type") == "commentCreated"]

    def test_the_same_create_twice_yields_one_thread_and_two_acks_naming_it(self):
        self._create(); self._create()
        self.assertEqual(len(self._rows()), 1, "one comment, one thread")
        acks = self._acks()
        self.assertEqual(len(acks), 2, "the repeat is answered, so the popover adopts the thread")
        self.assertEqual(acks[0]["tid"], acks[1]["tid"])
        self.assertEqual(self._warns(), [])

    def test_a_different_comment_on_the_same_passage_is_a_second_thread(self):
        self._create("Why jitter at all?"); self._create("And the cap?")
        self.assertEqual(len(self._rows()), 2)
        self.assertEqual(len({a["tid"] for a in self._acks()}), 2)

    def test_a_lag_parked_create_is_parked_once(self):
        # the anchor is not in the transcript yet: the create parks, the client hears a transient nack and
        # re-posts on the next frame — the park must not double
        km._parked_creates.clear()
        self._create(uuid="a9"); self._create(uuid="a9")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 2)
        self.assertTrue(all(n["transient"] for n in nacks))
        self.assertEqual(len(km._parked_creates), 1, "one parked copy for one create")
        km._parked_creates.clear()

    def test_a_parked_create_that_landed_answers_the_clients_repost_with_the_same_thread(self):
        km._parked_creates.clear()
        self._create(uuid="a9")
        self.assertEqual(len(km._parked_creates), 1)
        # the transcript catches up: the pusher's retry lands the thread
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        # the client's own re-post of the same create arrives a beat later: the same thread, no second one
        self._create(uuid="a9")
        self.assertEqual(len(self._rows()), 1, "the repeat is the same comment")
        acks = self._acks()
        self.assertTrue(acks and acks[-1]["tid"] == rows[0]["tid"], acks)
        self.assertEqual(self._warns(), [])

    def test_a_repost_arriving_while_the_pushers_retry_creates_is_not_a_second_thread(self):
        # THE RACE (review, 2026-09-09): the pusher sends the chat frame before it retries parked creates,
        # the client re-posts the create on that frame, and the re-post lands on a WS thread while the
        # pusher's own create is in flight — neither copy is noted yet, so both minted. The identity must
        # be reserved before the create runs, on both doors, and the pusher must consult the memo.
        import threading
        km._parked_creates.clear()
        self._create(uuid="a9")                       # lag-parked (the anchor is not on disk yet)
        self.assertEqual(len(km._parked_creates), 1)
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        real = km._comment_create
        seen = {"n": 0, "nested": None}

        def racing(*a, **k):
            seen["n"] += 1
            if seen["n"] == 1:
                # the pusher's create is in flight: the client's re-post arrives on another thread NOW
                before = len(self.sent)
                t = threading.Thread(target=lambda: self._create(uuid="a9"))
                t.start(); t.join(10)
                seen["nested"] = self.sent[before:]
            return real(*a, **k)
        km._comment_create = racing
        try:
            km._retry_parked_creates()
        finally:
            km._comment_create = real
        self.assertEqual(seen["n"], 1, "the re-post never reached a second create: %r" % (seen["nested"],))
        self.assertEqual(len(self._rows()), 1, "one comment, one thread")
        kinds = [f.get("type") for f in (seen["nested"] or [])]
        self.assertTrue("commentCreateFailed" in kinds or "commentCreated" in kinds,
                        "the re-post is answered while the pusher's copy is in flight (a typed transient nack, or the ack): %r" % kinds)
        self.assertEqual(km._parked_creates, [])
        self.assertEqual(self._warns(), [])

    def test_a_repeat_after_the_thread_was_resolved_is_a_new_comment_not_a_dropped_one(self):
        # a resolved (or merged, or promoted) thread cannot take a message: answering a same-worded comment
        # with its ack would drop the user's words silently (review, 2026-09-09)
        self._create()
        tid = self._acks()[0]["tid"]
        self.assertIsNone(km._comment_resolve(PARENT, tid))
        self._create()
        rows = self._rows()
        self.assertEqual(len(rows), 2, "a second, open thread for the re-asked comment")
        self.assertEqual(self._warns(), [])

    def test_an_answered_repeat_is_said_in_the_kernel_log(self):
        self._create()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            km._drive({"type": "commentCreate", "id": PARENT, "uuid": "a1", "exact": "exponential backoff",
                       "text": "Why jitter at all?", "name": ""}, self.client)
        self.assertIn("comment create repeated", buf.getvalue(), "a collapse is visible, never silent")


if __name__ == "__main__":
    unittest.main()
