#!/usr/bin/env python3
"""A Codex input echo is an ECHO to the kernel's live merge. CodexBackend.live_atoms emitted its echo
atoms without `_echo_text`, the marker every kernel reader of an input echo keys on, so with prune_live
taking the kernel's four arguments (tests/test_codex_backend.py PruneLive) and nothing else changed, the
merge would paint a Codex echo as a solid user atom beside its own queued bubble, paint it once more
beside its landed record, and count an echo-only merge as live ASSISTANT work, forcing the last turn
open: a false "working" chip for a session whose only live item is a pending send. The atoms carry
`_echo_text` now, and this module runs the REAL kernel merge (_merge_live_atoms) over the REAL backend (a
scripted fake app-server client: no SDK, no network, no turn ever streams) to pin the consequences, and
the case of a turn started from two queued sends: the real normalizer lands it as one record with a text
block per send, and the merge retires both echoes. The kernel is loaded the way
tests/test_kernel_fed_echo_absorbed.py loads it. Synthetic fixtures only (the notes-api demo domain).
"""
import os
import tempfile
import threading
import unittest
from romp_load import load_source
from types import SimpleNamespace

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state, and a kernel module that
# can reach a live manager port restarts the live kernel).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["ROMP_MANAGER_PORT"] = "1"             # a dead port, never an inherited live one
os.environ["ROMP_MODEL_CATALOG"] = "off"          # never the Models API from a test
km = load_source("romp_kernel_codex_echo_merge", os.path.join(BIN, "romp-kernel"))
cb = load_source("romp_codex_backend_echo_merge",
                      os.path.join(ROOT, "kernel", "codex_backend.py"))

T0 = 1781100000


class FakeClient:
    """The slice of the app-server client that spawn and a mid-turn send (a steer) touch. Nothing
    streams: no turn is ever started, so no worker thread writes the transcript under the test, and
    next_notification parks the backend's global pump (a daemon thread) for the process."""

    def __init__(self):
        self.steers = []
        self._park = threading.Event()

    def account_read(self, *a, **k):
        return SimpleNamespace(requires_openai_auth=False, account={"ok": True})

    def thread_start(self, params=None):
        return SimpleNamespace(thread=SimpleNamespace(id="T-1"), model="gpt-5-test")

    def thread_set_name(self, tid, name):
        pass

    def turn_steer(self, tid, expected_turn_id, input_items):
        self.steers.append((tid, expected_turn_id, input_items))

    def next_notification(self):
        self._park.wait()


class CodexEchoMerge(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClient()
        self.be = cb.CodexBackend(tempfile.mkdtemp(), client_factory=lambda: self.fake, log=lambda m: None)
        self.sid = self.be.spawn("web", "/TESTDIR")
        s = self.be._session(self.sid)
        with s.lock:
            s.turn_id = "t-live"          # an open turn: send() steers (nothing queues, no worker thread)
        self._saved_backend_for = km.Sessions.__dict__["backend_for"]
        be = self.be
        km.Sessions.backend_for = staticmethod(lambda sid: be)

    def tearDown(self):
        km.Sessions.backend_for = self._saved_backend_for

    @staticmethod
    def _user(text, uid, t):
        return {"type": "user", "uuid": uid, "t": t, "author": "human",
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}}

    @staticmethod
    def _assistant(uid, t, text):
        return {"type": "assistant", "uuid": uid, "t": t,
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    def _session(self, *extra):
        """One ENDED turn on disk, plus whatever `extra` atoms the case lands in it."""
        return {"turns": [{"id": "t1", "trigger": "u1", "t": T0, "end": T0 + 10, "ended": True,
                           "atoms": [self._user("tighten the notes-api search", "u1", T0),
                                     self._assistant("a1", T0 + 10, "Done."), *extra]}]}

    def test_an_echo_shown_as_queued_adds_no_atom_and_leaves_the_turn_ended(self):
        # build_session's exact call: shown_texts = the queued bubbles already painted; the echo behind
        # one is hidden, not pruned (its record has not landed), and it is not live work
        self.assertTrue(self.be.send(self.sid, "and the tests"))
        self.assertEqual(len(self.fake.steers), 1, "a mid-turn send steers")
        sess = self._session()
        merged = km._merge_live_atoms(sess, self.sid, shown_texts=["and the tests"])
        self.assertIs(merged, sess, "hidden behind its queued bubble: nothing to merge, the turn stays as parsed")
        self.assertTrue(sess["turns"][-1]["ended"])
        self.assertEqual(len(self.be.live_atoms(self.sid)), 1, "hidden, not pruned: the record has not landed")

    def test_an_echo_alone_paints_once_and_never_forces_the_turn_open(self):
        self.assertTrue(self.be.send(self.sid, "and the tests"))
        merged = km._merge_live_atoms(self._session(), self.sid)
        echoes = [a for a in merged["turns"][-1]["atoms"] if a.get("_echo_text")]
        self.assertEqual([a["_echo_text"] for a in echoes], ["and the tests"], "the pending send is visible, once")
        self.assertTrue(merged["turns"][-1]["ended"], "an echo-only merge keeps the turn's real ended state")
        self.assertFalse(km._session_working(merged["turns"]), "no false working chip")

    def test_a_record_in_the_sends_second_retires_the_echo_and_the_text_paints_once(self):
        self.assertTrue(self.be.send(self.sid, "and the tests"))
        t_echo = self.be.live_atoms(self.sid)[0]["t"]
        self.assertIsInstance(t_echo, int, "the echo is stamped in whole seconds, as the record will be")
        sess = self._session(self._user("and the tests", "u2", t_echo))      # lands within the send's second
        merged = km._merge_live_atoms(sess, self.sid)
        self.assertEqual(self.be.live_atoms(self.sid), [], "prune_live retired the echo by text")
        self.assertIs(merged, sess, "and the merge painted nothing beside the record")
        texts = [t for a in sess["turns"][-1]["atoms"] for t in km._atom_user_texts(a)]
        self.assertEqual(texts.count("and the tests"), 1)

    def test_a_two_block_record_in_the_sends_second_retires_both_echoes_and_paints_each_text_once(self):
        # Two sends queued before a turn starts go out as ONE turn (an input per send) and the app-server
        # answers one userMessage item carrying both; the REAL normalizer writes it as one user record with
        # a text block per input (codex_events._user_input_texts), the kernel's _atom_user_texts yields each
        # block, and prune_live lands both echoes. Joined into one block the record matches neither echo:
        # both stay live for good and paint as user bubbles beside the record in every later build.
        self.assertTrue(self.be.send(self.sid, "first send"))
        self.assertTrue(self.be.send(self.sid, "second send"))
        self.assertEqual([a["_echo_text"] for a in self.be.live_atoms(self.sid)], ["first send", "second send"])
        t_echo = max(a["t"] for a in self.be.live_atoms(self.sid))
        norm = cb._events.ThreadNormalizer("T-1", cwd="/TESTDIR", model="gpt-5-test", version="codex",
                                           clock=lambda: t_echo)
        rec, = norm.handle("item/completed", {
            "threadId": "T-1", "turnId": "t-live", "completedAtMs": t_echo * 1000,
            "item": {"type": "userMessage", "id": "u2",
                     "content": [{"type": "text", "text": "first send"},
                                 {"type": "text", "text": "second send"}]}})
        self.assertEqual(rec["type"], "user")
        atom = {"type": "user", "uuid": rec["uuid"], "t": t_echo, "author": "human", "message": rec["message"]}
        sess = self._session(atom)                                           # lands within the sends' second
        merged = km._merge_live_atoms(sess, self.sid)
        self.assertEqual(self.be.live_atoms(self.sid), [], "prune_live retired both echoes, one per block")
        self.assertIs(merged, sess, "and the merge painted nothing beside the record")
        texts = [t for a in sess["turns"][-1]["atoms"] for t in km._atom_user_texts(a)]
        self.assertEqual(texts.count("first send"), 1)
        self.assertEqual(texts.count("second send"), 1)


if __name__ == "__main__":
    unittest.main()
