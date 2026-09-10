#!/usr/bin/env python3
"""T306 (the user 2026-09-10): editing a QUEUED message happens in place, and while the edit is open the message
HOLDS. The kernel half: a hold is a mark on the entry (the backend queue's per-copy meta; a key on the parked
send), placed by the client that opened the editor and released by its Save (the editQueued itself), its Cancel
(holdQueued with hold false), or its socket closing (the ws finally hands the client to _release_client_holds:
the disconnect is the event, no timer). The drains skip a held entry and keep moving: the SDK feeder pops the
first UNHELD copy, the parked walk takes the first unheld op, and the in-flight guard finds the op the backend
holds by scanning for it rather than assuming the head slot. A hold on an entry no queue holds (already fed) is
refused through the editResult ok:false path with the existing too-late text. SYNTHETIC sids only; hermetic
state."""
import asyncio
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_qhold", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_qhold", os.path.join(BIN, "romp_sdk_backend.py"))
km._limit_hold = lambda sid: None
km._TMUX_PROMPT_HOLD_S = 0.0

SID = "11111111-2222-3333-4444-555555555555"


class BackendHold(unittest.TestCase):
    """SdkSession: the hold mark rides beside the copy, the feeder skips it, Save / Cancel / disconnect clear it."""

    def setUp(self):
        self.be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)
        self.s = sb.SdkSession(self.be, {"sid": SID, "name": "n", "cwd": "/tmp"})
        self.be.sessions[SID] = self.s
        self.s.enqueue("first", qid="q1", qts=1)
        self.s.enqueue("second", qid="q2", qts=2)
        self.s.enqueue("third")                                   # an id-less copy holds too

    def test_a_held_copy_is_skipped_by_the_feed_and_keeps_its_slot(self):
        self.assertTrue(self.s.hold_queued(0, "first", "c1", qid="q1"))
        self.assertEqual([m["held"] for m in self.s.pending_meta()], [True, False, False], "the chat reads the mark")
        with self.s._lock:
            self.assertEqual(self.s._feed_index_locked(), 1, "the first UNHELD copy is the one to feed")
            text, meta = self.s._pop_for_feed_locked(1)
        self.assertEqual((text, meta["qid"]), ("second", "q2"))
        self.assertEqual(self.s.pending(), ["first", "third"], "the held copy keeps its slot; the queue keeps moving")
        self.assertEqual([m["held"] for m in self.s.pending_meta()], [True, False])
        self.assertTrue(self.s.hold_queued(1, "third", "c1"), "an id-less copy holds by index + text")
        with self.s._lock:
            self.assertEqual(self.s._feed_index_locked(), -1, "everything held: nothing feeds")

    def test_save_releases_the_hold_with_the_new_words_in_place(self):
        self.assertTrue(self.s.hold_queued(0, "first", "c1", qid="q1"))
        self.assertEqual(self.s.replace_queued(0, "first, edited", "first"), "first")
        self.assertEqual(self.s.pending(), ["first, edited", "second", "third"])
        self.assertFalse(self.s.pending_meta()[0]["held"], "the Save is the release")
        with self.s._lock:
            self.assertEqual(self.s._feed_index_locked(), 0)
        self.assertEqual(sb.read_reg(self.be.state_dir, SID)["queue"], ["first, edited", "second", "third"],
                         "the persisted queue mirrors the edit")

    def test_cancel_releases_only_its_owners_hold(self):
        self.assertTrue(self.s.hold_queued(0, "first", "c1", qid="q1"))
        self.assertFalse(self.s.release_queued(0, "first", "c2", qid="q1"), "another client's release is not this hold's")
        self.assertTrue(self.s.pending_meta()[0]["held"])
        self.assertTrue(self.s.release_queued(0, "first", "c1", qid="q1"))
        self.assertFalse(self.s.pending_meta()[0]["held"])
        self.assertFalse(self.s.release_queued(0, "first", "c1", qid="q1"), "nothing to release is a miss")

    def test_a_disconnect_releases_every_hold_of_that_owner_and_wakes_the_feeder(self):
        self.assertTrue(self.s.hold_queued(0, "first", "c1", qid="q1"))
        self.assertTrue(self.s.hold_queued(1, "second", "c2", qid="q2"))
        self.assertTrue(self.s.hold_queued(2, "third", "c1"))
        loop = asyncio.new_event_loop()
        self.s.loop = loop
        self.s._input_wake = asyncio.Event()
        try:
            self.assertEqual(self.s.release_holds_by("c1"), 2)
            loop.call_soon(loop.stop)
            loop.run_forever()
            self.assertTrue(self.s._input_wake.is_set(), "the released copies feed on the next pass, not the next send")
        finally:
            loop.close()
        self.assertEqual([m["held"] for m in self.s.pending_meta()], [False, True, False], "the other client's hold stands")
        self.assertEqual(self.s.release_holds_by("c1"), 0)

    def test_a_hold_on_a_copy_that_already_fed_is_refused(self):
        with self.s._lock:
            self.s._pop_for_feed_locked(0)                        # "first" went to the CLI
        self.assertFalse(self.s.hold_queued(0, "first", "c1", qid="q1"), "by id: gone")
        self.assertFalse(self.s.hold_queued(0, "first", "c1"), "by index + text: the slot holds another copy")
        self.assertEqual([m["held"] for m in self.s.pending_meta()], [False, False], "a miss marks nothing")

    def test_unqueue_takes_the_hold_with_the_copy(self):
        self.assertTrue(self.s.hold_queued(0, "first", "c1", qid="q1"))
        self.assertEqual(self.s.unqueue(0, "first"), "first")
        self.assertEqual([m["held"] for m in self.s.pending_meta()], [False, False])
        with self.s._lock:
            self.assertEqual(self.s._feed_index_locked(), 0)

    def test_the_backend_wrappers_reach_the_session(self):
        self.assertTrue(self.be.hold_queued(SID, 0, "first", "c1", qid="q1"))
        self.assertTrue(self.be.pending_queued_meta(SID)[0]["held"])
        self.assertEqual(self.be.release_holds_by("c1"), 1)
        self.assertTrue(self.be.hold_queued(SID, 0, "first", "c1", qid="q1"))
        self.assertTrue(self.be.release_queued(SID, 0, "first", "c1", qid="q1"))
        self.assertFalse(self.be.hold_queued("no-such-sid", 0, "x", "c1"), "no session, no hold")


class _FakeBackend:
    def __init__(self):
        self.calls = []

    def send(self, sid, text):
        self.calls.append(("send", text))
        return True

    def set_model(self, sid, value):
        self.calls.append(("model", value))
        return True


class _HoldBackend:
    """An SDK-shaped backend that owns its queue and its holds (SdkBackend's hold_queued / release_queued /
    release_holds_by / pending_queued_meta contract), for the executing _drive harness."""

    def __init__(self, pending=None):
        self.q = list(pending or [])
        self.holds = {}                      # text -> owner
        self.calls = []

    def owns(self, sid):
        return False                          # a stand-in owns no live session: the echo path skips it

    def pending_queued(self, sid):
        return list(self.q)

    def pending_queued_meta(self, sid):
        return [{"md": t, "qid": None, "qts": None, "held": t in self.holds} for t in self.q]

    def edit_queued(self, sid, idx, text, expect=None):
        if not (0 <= idx < len(self.q)) or self.q[idx] != expect:
            return None
        old, self.q[idx] = self.q[idx], text
        self.holds.pop(old, None)
        return old

    def hold_queued(self, sid, idx, expect, owner, qid=None):
        self.calls.append(("hold", idx, expect, owner, qid))
        if not (0 <= idx < len(self.q)) or self.q[idx] != expect:
            return False
        self.holds[expect] = owner
        return True

    def release_queued(self, sid, idx, expect, owner=None, qid=None):
        self.calls.append(("release", idx, expect, owner, qid))
        if self.holds.get(expect) is None or (owner is not None and self.holds[expect] != owner):
            return False
        del self.holds[expect]
        return True

    def release_holds_by(self, owner):
        gone = [t for t, o in self.holds.items() if o == owner]
        for t in gone:
            del self.holds[t]
        return len(gone)


class KernelHold(unittest.TestCase):
    """holdQueued through _drive with a capturing client; the parked walk's skip; the disconnect release."""

    def setUp(self):
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s)), "cid": "cid-one"}
        self.be = _HoldBackend(["alpha", "beta"])
        self._saved = (km._name_of, km._sdk, km.Sessions.backend_for, km._push_soon, km._compacting_now)
        km._name_of = lambda sid: "web" if sid == SID else None
        km._sdk = lambda: self.be
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._push_soon = lambda *a, **k: None
        km._pending_ops.clear()
        km._inflight_ops.clear()
        km._park_holds.clear()

    def tearDown(self):
        km._name_of, km._sdk, backend_for, km._push_soon, km._compacting_now = self._saved
        km.Sessions.backend_for = staticmethod(backend_for)
        km._pending_ops.clear()
        km._inflight_ops.clear()
        km._park_holds.clear()
        try:
            os.unlink(km._PENDING_OPS_FILE)
        except OSError:
            pass

    def _op(self, **fields):
        msg = {"type": "holdQueued", "id": SID}
        msg.update(fields)
        self.assertTrue(km._drive(msg, self.client), "a drive op is consumed")
        self.assertEqual(len(self.sent), 1, "exactly one answer frame")
        return self.sent.pop()

    def test_the_idx_arm_marks_the_backend_copy_for_this_client_and_answers_ok(self):
        self.assertEqual(self._op(idx=1, md="beta"),
                         {"type": "editResult", "ok": True, "id": SID, "md": "beta", "text": "", "op": "hold"})
        self.assertEqual(self.be.holds, {"beta": "cid-one"}, "the owner is the connection, so its close can release")
        self.assertEqual(self._op(idx=1, md="beta", hold=False),
                         {"type": "editResult", "ok": True, "id": SID, "md": "beta", "text": "", "op": "release"})
        self.assertEqual(self.be.holds, {})

    def test_the_idx_arm_relocates_by_body_like_the_edit(self):
        self.assertTrue(self._op(idx=0, md="beta")["ok"], "a stale index re-locates by body")
        self.assertEqual(self.be.holds, {"beta": "cid-one"})

    def test_a_hold_on_a_fed_copy_is_refused_with_the_too_late_text(self):
        frame = self._op(idx=0, md="gone")
        self.assertEqual((frame["ok"], frame["md"], frame["text"], frame["op"]), (False, "gone", km._edit_miss_text("gone"), "hold"))
        self.assertEqual(self.be.holds, {})

    def test_the_park_arm_marks_the_parked_send_and_the_walk_skips_it(self):
        km._pending_ops[SID] = [("send", "a", "human"), ("send", "b", "human"), ("model", "opus")]
        self.assertEqual(self._op(park=0, md="a")["op"], "hold")
        self.assertTrue(km._parked_held(SID, km._pending_ops[SID][0]))
        fb = _FakeBackend()
        km.Sessions.backend_for = staticmethod(lambda sid: fb)
        km._compacting_now = lambda sid: False
        km._apply_pending_ops()
        self.assertEqual(fb.calls, [("send", "b")], "the unheld send behind it goes; the held one waits")
        self.assertEqual(km._pending_ops[SID], [("send", "a", "human"), ("model", "opus")], "the held send keeps its slot")
        km._apply_pending_ops()
        self.assertEqual(fb.calls, [("send", "b"), ("model", "opus")], "a settings op behind a held send still applies")
        self.assertEqual(self._op(park=0, md="a", hold=False)["op"], "release")
        km._apply_pending_ops()
        self.assertEqual(fb.calls[-1], ("send", "a"), "released, it goes on the next pass")
        self.assertNotIn(SID, km._pending_ops)

    def test_a_parked_hold_is_refused_on_a_command_chip_and_on_a_gone_entry(self):
        km._pending_ops[SID] = [("compact",)]
        frame = self._op(park=0, md="/compact")
        self.assertFalse(frame["ok"])
        self.assertIn("only a queued message", frame["text"])
        frame = self._op(park=3, md="gone")
        self.assertEqual((frame["ok"], frame["text"]), (False, km._edit_miss_text("gone")))
        self.assertEqual(km._park_holds, {})

    def test_the_save_and_the_cancel_take_the_parked_hold_with_them(self):
        km._pending_ops[SID] = [("send", "a", "human"), ("send", "b", "human")]
        self._op(park=0, md="a")
        self._op(park=1, md="b")
        self.assertTrue(km._drive({"type": "editQueued", "id": SID, "park": 0, "md": "a", "text": "a2"}, self.client))
        self.sent.clear()
        self.assertFalse(km._parked_held(SID, km._pending_ops[SID][0]), "Save is the release")
        self.assertTrue(km._parked_held(SID, km._pending_ops[SID][1]))
        self.assertTrue(km._drive({"type": "cancelQueued", "id": SID, "park": 1, "md": "b"}, self.client))
        self.sent.clear()
        self.assertEqual(km._park_holds.get(SID, {}), {}, "a cancelled entry leaves no hold behind")

    def test_the_md_only_arm_tries_the_fifo_then_the_backend(self):
        km._pending_ops[SID] = [("send", "p", "human")]
        self.assertTrue(self._op(md="p")["ok"])
        self.assertTrue(km._parked_held(SID, km._pending_ops[SID][0]))
        self.assertEqual(self.be.holds, {})
        self.assertTrue(self._op(md="alpha")["ok"], "the FIFO misses, the backend holds it")
        self.assertEqual(self.be.holds, {"alpha": "cid-one"})

    def test_the_in_flight_guard_finds_the_op_the_backend_holds_wherever_it_sits(self):
        # with a held send at the head, the op the walk hands over sits at slot 1: its ✕ and ✎ are still too
        # late, and the held head's are still allowed (the slot-0 rule alone would have it backwards)
        km._pending_ops[SID] = [("send", "a", "human"), ("send", "b", "human")]
        self._op(park=0, md="a")
        km._inflight_ops[SID] = km._pending_ops[SID][1]
        self.assertEqual(km._cancel_parked(SID, 1, "b"), km._cancel_miss_text("b"))
        self.assertEqual(km._edit_parked(SID, 1, "b", "b2"), km._edit_miss_text("b"))
        self.assertEqual(km._edit_parked(SID, 5, "b", "b2"), km._edit_miss_text("b"), "a body re-locate never lands on it either")
        self.assertIsNone(km._edit_parked(SID, 0, "a", "a2"), "the held head is romp's to change")
        self.assertEqual([op[1] for op in km._pending_ops[SID]], ["a2", "b"])

    def test_a_clients_close_releases_its_holds_in_both_queues(self):
        km._pending_ops[SID] = [("send", "a", "human")]
        self._op(park=0, md="a")
        self._op(idx=0, md="alpha")
        other = {"send": lambda s: None, "cid": "cid-two"}
        km._drive({"type": "holdQueued", "id": SID, "idx": 1, "md": "beta"}, other)
        km._release_client_holds(self.client)
        self.assertEqual(km._park_holds.get(SID, {}), {})
        self.assertEqual(self.be.holds, {"beta": "cid-two"}, "the other connection's hold stands")
        km._release_client_holds({"cid": None})            # a client without an id releases nothing and raises nothing
        self.assertEqual(self.be.holds, {"beta": "cid-two"})

    def test_the_ws_teardown_is_the_release_event_and_the_op_routes_across_kernels(self):
        ksrc = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('"editQueued", "holdQueued"', ksrc, "the op routes to the owning kernel across linked machines (ID_OPS)")
        i = ksrc.index("_clients.remove(client)")
        self.assertIn("_release_client_holds(client)", ksrc[i - 400:i + 400], "the socket's finally releases its holds: the disconnect is the event")
        self.assertIn('"cid": uuid.uuid4().hex', ksrc, "every connection has an owner id for its holds")
        self.assertIn('m["held"] = True', ksrc, "the queued event carries the mark, so every client can say 'editing'")


if __name__ == "__main__":
    unittest.main()
