#!/usr/bin/env python3
"""A handler failure on ONE streamed message must not end a session's CLI, and the live tail the two
threads share must be safe to sweep (2026-09-06).

What happened live: a session's receive loop died on `KeyError: '<message uuid>'` — the only line
the log had — and the client teardown that followed closed its CLI mid-work (the in-flight turn, its
subagents and its background tasks), then the session came back as a crash resume. The KeyError came from a live-tail sweep at the ResultMessage settle (retire_live_work)
racing the kernel thread's prune_live on the same unlocked dict: `for k in list(d.keys()): a = d[k]`
with the kernel thread deleting the just-landed reply between the snapshot and the read. The review
then found the same unlocked dict raising RuntimeError out of the pusher's build (_persist_echoes)
and out of the session thread at reconnect (_mark_dropped_echoes), and the two-step
`if not d: _live.pop(sid)` orphaning an atom stashed between the steps. Three fixes, all covered here,
and a fourth the review of the settle turned up:

  * the live tail has ONE lock (SdkBackend._live_lock): every stash, every pop, the sid-level pop and
    every iterating read take it; sweeps walk a snapshot taken under it; the emptiness check and the
    sid-level pop are one step; the lock is never held across I/O or a callback. Real-thread hammers
    below drive the shapes review probes reproduced on the lock-free tail;
  * the sweeps still walk a snapshot and pop with a default (belt-and-braces), and prune_live /
    _evict_live_overflow report a key that vanishes anyway once per site per kernel life — under the
    lock that means a mutator bypassed it;
  * the receive loop (_drain) runs each message through _handle_stream_message, which keeps a
    handler's exception to that message — logged with its type, the message kind, a frame chain
    bounded from the OUTER end (the failing frame is always kept, and is the dedupe key) and what
    that message losing its handling cost, by its shape (for a result: read from whether the settle
    actually ran) — deduped in the error-center ring and re-entering with the full line once
    evicted; the reporter itself is guarded; the ResultMessage branch is ONE try whose finally is
    the whole settle (state resets, 'waiting', the queue wake, the poke, the rename ping, the
    deferred reconnect), so a raise anywhere in the result's bookkeeping still closes the turn; a
    fault of the stream itself still ends the loop as before;
  * that settle woke the input feeder and THEN armed the deferred reconnect, both wakeups FIFO on
    the loop, so the feeder fed a message gate-held behind an interrupted turn to the client about
    to be torn down, and _reconcile_stranded flagged it 'never delivered' (pre-existing on main).
    inputs() now holds the queue while a reconnect is armed; the new client's feeder takes the head.

Every id here is synthetic (the placeholder uuid family); no message content is real.
"""
import asyncio
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import types
import unittest
from importlib.machinery import ModuleSpec
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()       # hermetic state BEFORE the load (import-time root)
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_streamsurvive",
                      os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-aaaaaaaaaaa1"           # this module's own synthetic sid
UNKNOWN_SID = "11111111-2222-3333-4444-bbbbbbbbbbb2"   # a session id the kernel never registered
MSG_UUID = "11111111-2222-3333-4444-ccccccccccc3"      # a message uuid (the live tail's key kind)


# ---- message doubles: the SDK's shapes, by class name (msg_to_atom / _on_message key on them) ----
class _TextBlock:
    def __init__(self, text): self.text = text
class _ToolUseBlock:
    def __init__(self, id, name, inp): self.id, self.name, self.input = id, name, inp
class _AssistantMessage:
    def __init__(self, content, model="claude-x", uuid="a1", parent_tool_use_id=None):
        self.content, self.model, self.uuid = content, model, uuid
        self.parent_tool_use_id, self.stop_reason, self.error = parent_tool_use_id, "end_turn", None
class _UserMessage:
    def __init__(self, content, uuid="u1", parent_tool_use_id=None):
        self.content, self.uuid, self.parent_tool_use_id, self.tool_use_result = content, uuid, parent_tool_use_id, None
class _ResultMessage:
    uuid = "r1"
    num_turns = 1
def _result(**fields):
    """A ResultMessage double carrying result fields (total_cost_usd, usage, …). Built as a subclass so
    the handler's isinstance and the reporter's class-name checks both see a ResultMessage."""
    return type("ResultMessage", (_ResultMessage,), dict(fields))()
class _SystemMessage:
    def __init__(self, subtype, data=None, uuid=None):
        self.subtype, self.data, self.uuid = subtype, (data or {}), uuid
class _HookEventMessage(_SystemMessage):
    """The SDK's HookEventMessage IS a SystemMessage subclass (types.py) — so hook_started /
    hook_response reach the SystemMessage catch-all exactly as the new subtypes did live."""
class _StreamEvent:
    uuid = "se1"
_TextBlock.__name__ = "TextBlock"; _ToolUseBlock.__name__ = "ToolUseBlock"
_AssistantMessage.__name__ = "AssistantMessage"; _UserMessage.__name__ = "UserMessage"
_ResultMessage.__name__ = "ResultMessage"; _SystemMessage.__name__ = "SystemMessage"
_HookEventMessage.__name__ = "HookEventMessage"; _StreamEvent.__name__ = "StreamEvent"


class _RacyTail(dict):
    """A live tail whose snapshot goes stale by one key: right after a sweep takes its keys()/items()
    snapshot, `victim` is removed — a mutator changing the dict without the lock, which is the shape
    that killed the session live. The old `a = d[k]` raises KeyError(victim) on it; a snapshot-and-pop
    sweep does not."""
    def __init__(self, *a, victim=None, **k):
        super().__init__(*a, **k)
        self.victim = victim
    def keys(self):
        ks = list(super().keys()); self._other_thread_prunes(); return ks
    def items(self):
        it = list(super().items()); self._other_thread_prunes(); return it
    def _other_thread_prunes(self):
        if self.victim in self:
            dict.__delitem__(self, self.victim)


class _ProbedTail(dict):
    """A live tail whose EMPTINESS CHECK (`not d`, `d or {}`, `if d`: all __len__) lets a SECOND thread
    try the tail lock without blocking and, if it gets in, stash an atom into the dict. Deterministic
    where the hammers sample: the probe runs inside the sweep's own step and is joined before the check
    returns, so its outcome is decided by whether the sweep holds the lock at that instant — refused
    under the lock, landed without it. `__len__` answers the pre-stash length, so a sweep that pops
    the sid on `not d` pops a dict the probe has just stashed into (the orphan shape)."""
    def __init__(self, lock, *a, **k):
        super().__init__(*a, **k)
        self.lock, self.stashed, self.refused = lock, [], 0
    def __len__(self):
        n = super().__len__()
        def other_thread():
            if self.lock.acquire(blocking=False):
                try:
                    key = "echo:probe%d" % len(self.stashed)
                    dict.__setitem__(self, key, _echo(key, "a send racing the sweep", 9))
                    self.stashed.append(key)
                finally:
                    self.lock.release()
            else:
                self.refused += 1
        t = threading.Thread(target=other_thread, name="probe")
        t.start(); t.join()
        return n


def _backend(lines=None, **kw):
    d = tempfile.mkdtemp()
    log = (lambda m: lines.append(str(m))) if lines is not None else (lambda *a, **k: None)
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=log, **kw)


def _problems(be):
    """The backend's problem ring, minus the construction-time line this venv always adds (no SDK
    installed here → 'claude_agent_sdk is NOT importable'), which is not what these tests observe."""
    return [p["text"] for p in be.problems() if "claude_agent_sdk" not in p["text"]]


def _session(be):
    s = sb.SdkSession(be, {"sid": SID, "name": "web", "cwd": "/tmp"})
    async def _noop(): pass
    s._do_refresh_context = _noop
    s._do_refresh_usage = _noop
    return s


def _work(uuid, t):
    return {"type": "assistant", "uuid": uuid, "t": t,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_" + uuid, "name": "Bash", "input": {}}]}}


def _text_atom(uuid, t):
    return {"type": "assistant", "uuid": uuid, "t": t,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "a reply the user watched"}]}}


def _echo(key, text, t, **flags):
    a = {"type": "user", "uuid": key, "session_id": SID, "t": t, "parentUuid": None, "author": "human",
         "_echo_text": text, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    a.update(flags)
    return a


def _chain(e):
    return " > ".join("%s:%d %s" % (os.path.basename(f.filename), f.lineno, f.name)
                      for f in traceback.extract_tb(e.__traceback__))


class LiveTailSweepsSurviveAConcurrentPrune(unittest.TestCase):
    """The scripted race: a dict subclass drops a key between a sweep's snapshot and its pop. Under the
    lock this can only be a mutator bypassing it, and the sweeps still tolerate it."""
    def setUp(self):
        sb.SdkBackend._live_tail_race_seen.clear()     # per-kernel-life set; a fresh life per test

    def _problems(self, be, site):
        return [t for t in _problems(be) if t.startswith("live tail (%s)" % site)]

    def test_retire_live_work_survives_the_settle_race_and_does_not_report_it(self):
        """THE reported failure: the settle sweep held a snapshot of keys while the kernel thread pruned
        the just-landed reply — `a = d[k]` raised KeyError('<message uuid>') out of _on_message. The
        sweep tolerates the vanished key. It does NOT report it: retire_live_work releases the lock for
        the orphan salvage's transcript read, so a key gone at its pop is a landing prune_live filed in
        that window — legitimate, and a problem line for it would be a false alarm on every such settle."""
        be = _backend()
        be._live[SID] = _RacyTail({MSG_UUID: _work(MSG_UUID, 5), "w2": _work("w2", 6)}, victim=MSG_UUID)
        be.retire_live_work(SID)                        # raised KeyError before the fix
        self.assertNotIn(SID, be._live, "every work atom retired, the vanished one included")
        self.assertEqual(self._problems(be, "retire_live_work"), [], "not a fault at this site")
        self.assertEqual(_problems(be), [])

    def test_prune_live_survives_a_key_taken_from_under_it_and_reports_once(self):
        """prune_live runs wholly under the lock, so a key that vanishes anyway means a mutator bypassed
        it — reported once per site per kernel life, naming the key's KIND and never its value."""
        be = _backend()
        be._live[SID] = _RacyTail({MSG_UUID: _work(MSG_UUID, 5), "w2": _work("w2", 6)}, victim=MSG_UUID)
        be.prune_live(SID, {MSG_UUID, "w2"})            # both landed; the victim vanished mid-sweep
        self.assertNotIn(SID, be._live)
        self.assertEqual(len(self._problems(be, "prune_live")), 1, "the bypass is reported")
        be._live[SID] = _RacyTail({"w3": _work("w3", 7)}, victim="w3")
        be.prune_live(SID, {"w3"})
        self.assertEqual(len(self._problems(be, "prune_live")), 1, "…ONCE per site per kernel life")
        txt = self._problems(be, "prune_live")[0]
        self.assertIn("message-uuid key", txt, "the line names the key KIND")
        self.assertIn("_live_lock", txt, "…and what a vanished key means now: the lock was bypassed")
        self.assertNotIn(MSG_UUID, txt, "…never the key's value")

    def test_evict_live_overflow_survives_a_prune_under_it(self):
        d = _RacyTail({("w%03d" % i): _work("w%03d" % i, i) for i in range(sb.LIVE_TAIL_CAP + 5)}, victim="w000")
        vanished = sb._evict_live_overflow(d)          # raised KeyError('w000') before the fix
        self.assertLessEqual(len(d), sb.LIVE_TAIL_CAP, "still bounded")
        self.assertEqual(vanished, 1, "the sweep counts the key another thread took, for the caller to report")
        self.assertEqual(sb._evict_live_overflow(dict(d)), 0, "a quiet sweep reports nothing")

    def test_forward_reports_an_evict_race_once(self):
        be = _backend()
        s = _session(be)
        tail = _RacyTail({("w%03d" % i): _work("w%03d" % i, i) for i in range(sb.LIVE_TAIL_CAP + 2)}, victim="w000")
        be._live[SID] = tail
        be._forward(s, _AssistantMessage([_TextBlock("hi")], uuid="new1"))
        self.assertIn("new1", tail, "the new atom landed")
        self.assertLessEqual(len(tail), sb.LIVE_TAIL_CAP)
        self.assertEqual(len(self._problems(be, "_evict_live_overflow")), 1)

    def test_the_result_settle_survives_a_pruned_live_tail(self):
        """The reproduction through the real handler: a ResultMessage settles the turn → retire_live_work
        → the racy tail. Before the fix this raised out of _on_message; the receive loop ended on it."""
        be = _backend()
        s = _session(be)
        be._live[SID] = _RacyTail({MSG_UUID: _work(MSG_UUID, 5)}, victim=MSG_UUID)
        s.inflight = 1

        async def run():
            s._on_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage)
            await asyncio.sleep(0)
        asyncio.run(run())                              # no KeyError
        self.assertEqual(s.inflight, 0, "the turn settled")
        self.assertNotIn(SID, be._live)


class TheLiveTailLock(unittest.TestCase):
    """REAL threads on plain dicts, with the interpreter's switch interval lowered so the interleavings
    the GIL permits show up within a second: one thread plays the session's loop (stash work atoms,
    retire them at the settle, mark dropped echoes at a reconnect), the other plays the kernel (stash
    an echo at send, land it in prune_live, mirror it). On the lock-free tail, probes of this shape
    raised RuntimeError out of prune_live on 1412 of 1500 calls, out of _mark_dropped_echoes on 8 of
    3000, and orphaned 343 of 60000 stashes in the sid-level pop's window; under the lock nothing
    raises and nothing is orphaned. Each hammer runs until BOTH sides have done their floor of
    iterations (the kernel keeps hammering until the session thread has completed its turns), so the
    overlap asserted on is guaranteed by construction rather than sampled — under a lock convoy the
    session thread's count for a fixed 1500 kernel calls varied 10–447 run to run, and a `> 2` floor
    on it was a margin by luck. A wall-clock cap is the only other stop, and hitting
    it FAILS the test: the work takes about a second idle, so the cap means a wedge, not load."""
    SEEDED = 60          # unlanded echoes sitting in the tail (the _persist_echoes walk's length)
    CAP_S = 20.0         # ~20x the idle runtime of the longest hammer

    def setUp(self):
        self._interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        sb.SdkBackend._live_tail_race_seen.clear()

    def tearDown(self):
        sys.setswitchinterval(self._interval)          # process-global; the rest of the suite gets it back

    def _backend(self):
        be = _backend()
        be._update_reg = lambda sid, **f: None          # the reg write is file I/O outside the lock — not under test
        for i in range(self.SEEDED):
            be._stash_live(SID, "echo:seed%03d" % i, _echo("echo:seed%03d" % i, "seed %d" % i, 5))
        return be

    def _hammer(self, session_body, kernel_body, n_kernel, n_session):
        """Two threads off one barrier: kernel_body(i) runs on one until BOTH floors are met — n_kernel
        calls of its own and n_session completed session_body(i) iterations on the other — then stops
        the session thread. Returns (errors, session_iterations, kernel_iterations, timed_out)."""
        barrier = threading.Barrier(2)
        stop = threading.Event()
        errors, iters, timed_out = [], [0, 0], [False]
        deadline = time.monotonic() + self.CAP_S
        def session():
            barrier.wait()
            i = 0
            while not stop.is_set():
                try:
                    session_body(i)
                except Exception as e:
                    errors.append(("session", repr(e), _chain(e)))
                i += 1
                iters[0] = i
        def kernel():
            barrier.wait()
            i = 0
            while i < n_kernel or iters[0] < n_session:
                if time.monotonic() > deadline:
                    timed_out[0] = True
                    break
                try:
                    kernel_body(i)
                except Exception as e:
                    errors.append(("kernel", repr(e), _chain(e)))
                i += 1
                iters[1] = i
            stop.set()
        ts = threading.Thread(target=session, name="session"), threading.Thread(target=kernel, name="kernel")
        for t in ts:
            t.start()
        for t in ts:
            t.join(self.CAP_S + 5)
        return errors, iters[0], iters[1], timed_out[0]

    def _assert_overlap(self, res, n_session, n_kernel):
        """No exception on either thread, and both floors met — the interleaving happened."""
        errors, si, ki, timed_out = res
        self.assertEqual(errors, [], "no exception on either thread: %r" % errors[:3])
        self.assertFalse(timed_out, "the hammer hit its %.0fs cap at %d session / %d kernel iterations"
                         % (self.CAP_S, si, ki))
        self.assertGreaterEqual(si, n_session, "the session thread ran its floor (%d of %d, against %d kernel calls)"
                                % (si, n_session, ki))
        self.assertGreaterEqual(ki, n_kernel, "the kernel thread ran its floor (%d of %d)" % (ki, n_kernel))

    def test_prune_live_and_the_echo_mirror_survive_the_session_threads_stash_and_retire(self):
        """Refuter finding 1: prune_live (kernel thread) lands an echo → _persist_echoes walks the tail
        while the session thread's _forward stashes and retire_live_work pops — RuntimeError('dictionary
        changed size during iteration') escaped prune_live and aborted the pusher's whole build."""
        be = self._backend()
        s = _session(be)
        s._mark = lambda state: None                   # the state write is I/O the hammer does not need
        def session_body(i):
            for j in range(3):                         # _forward: one stash per streamed message
                be._forward(s, _AssistantMessage([_ToolUseBlock("toolu_%d_%d" % (i, j), "Bash", {})],
                                                 uuid="s%d-%d" % (i, j)))
            be.retire_live_work(SID)                   # the settle sweep, once per 'turn'
        def kernel_body(i):
            text = "probe send %d" % i
            be._stash_live(SID, "echo:k%d" % i, _echo("echo:k%d" % i, text, 5))   # send(): the echo
            be._persist_echoes(SID)                                                # …and its mirror
            be.prune_live(SID, set(), {text: 6.0}, 0)  # the echo lands by text → echo_removed → the mirror walk
        self._assert_overlap(self._hammer(session_body, kernel_body, 1500, 20), 20, 1500)
        tail = be._live.get(SID) or {}
        self.assertTrue(all(("echo:seed%03d" % i) in tail for i in range(self.SEEDED)), "no seeded echo was lost")
        self.assertFalse([k for k in tail if k.startswith("echo:k")], "every landed echo was pruned")
        self.assertEqual([p for p in _problems(be) if p.startswith("live tail (")], [],
                         "under the lock no key vanishes mid-sweep, so no race line")

    def test_mark_dropped_echoes_survives_a_concurrent_send_stash(self):
        """Refuter finding 2: _mark_dropped_echoes runs on the SESSION thread at spawn and at every
        reconnect, outside the connect's try; its comprehension over the tail raised RuntimeError when
        the kernel thread's send() stashed an echo mid-walk, and the session thread died with no reconnect."""
        be = self._backend()
        def session_body(i):
            be._mark_dropped_echoes(SID, [], refeed=False)   # the resumable-reconnect arm (flag path only)
        def kernel_body(i):
            text = "probe send %d" % i
            be._stash_live(SID, "echo:k%d" % i, _echo("echo:k%d" % i, text, 5))
            be._persist_echoes(SID)
            be.prune_live(SID, set(), {text: 6.0}, 0)  # land it, so the tail stays bounded
        self._assert_overlap(self._hammer(session_body, kernel_body, 3000, 20), 20, 3000)
        tail = be._live.get(SID) or {}
        self.assertTrue(all(tail["echo:seed%03d" % i].get("dropped") for i in range(self.SEEDED)),
                        "the seeded echoes were marked (the marking still does its job)")

    def test_no_stash_is_orphaned_by_the_sid_level_pop(self):
        """Refuter finding 3: `if not d: _live.pop(sid)` was two steps; a stash between them landed in a
        dict nothing could reach (an input echo gone from the chat and from reg['echoes']; a work atom
        the settle's salvage never sees). Under the lock the check and the pop are one step, so an atom
        just stashed is always reachable from _live — checked from OUTSIDE the lock after every stash,
        which is exactly the invariant: the sid's dict is never unreachable while non-empty."""
        for pair in ("retire_live_work vs an echo stash", "prune_live vs a work stash"):
            with self.subTest(pair=pair):
                be = _backend()
                be._update_reg = lambda sid, **f: None
                orphans = []
                if pair.startswith("retire"):
                    def session_body(i):
                        be._stash_live(SID, "w%d" % i, _work("w%d" % i, 1))
                        be.retire_live_work(SID)       # empties the tail → the sid-level pop
                    def kernel_body(i):
                        key = "echo:%08x" % i
                        be._stash_live(SID, key, _echo(key, "message %d" % i, 2))
                        if key not in (be._live.get(SID) or {}):
                            orphans.append(key)
                        with be._live_lock:
                            (be._live.get(SID) or {}).pop(key, None)
                else:
                    def session_body(i):
                        be.prune_live(SID, {"landed"}, (), 0)   # pops the landed atom → maybe the sid-level pop
                    def kernel_body(i):
                        be._stash_live(SID, "landed", _work("landed", 1))
                        key = "x%08x" % i
                        be._stash_live(SID, key, _work(key, 3))
                        if key not in (be._live.get(SID) or {}):
                            orphans.append(key)
                        with be._live_lock:
                            (be._live.get(SID) or {}).pop(key, None)
                res = self._hammer(session_body, kernel_body, 20000, 50)
                self._assert_overlap(res, 50, 20000)
                self.assertEqual(orphans, [], "%d of %d stashes landed in an unreachable dict" % (len(orphans), res[2]))

    def test_the_lock_is_never_held_across_io_or_a_callback(self):
        """The rule's other half: copy under the lock, act outside. The reg write (the echo mirror), the
        transcript read and the marker append (the orphan salvage) and the pusher wake all observe the
        lock UNHELD — a lock held across a 4 MB tail read would stall the pusher's build on it."""
        be = _backend()
        seen = []
        note = lambda what: seen.append((what, be._live_lock._is_owned()))
        be._update_reg = lambda sid, **f: note("reg write")
        be._push_cb = lambda: note("pusher wake")
        be._reply_on_disk = lambda sid, u: (note("transcript read"), False)[1]
        orig = sb.append_orphan_reply
        sb.append_orphan_reply = lambda *a, **k: note("orphan append")
        try:
            be._stash_live(SID, "echo:a", _echo("echo:a", "hello", 5))
            be._persist_echoes(SID)                                     # send()'s mirror write
            be.prune_live(SID, set(), {"hello": 6.0}, 0)                # landing → the mirror write
            be._live[SID] = {MSG_UUID: _text_atom(MSG_UUID, 5)}
            be.retire_live_work(SID)                                    # the salvage's read + append
            be._live[SID] = {"echo:d": _echo("echo:d", "lost", 5, dropped=True)}
            self.assertEqual(be.dismiss_echo(SID, uuid="echo:d"), "lost")   # mirror write + wake
        finally:
            sb.append_orphan_reply = orig
        kinds = {w for w, _ in seen}
        self.assertEqual(kinds, {"reg write", "pusher wake", "transcript read", "orphan append"}, seen)
        self.assertFalse([w for w, held in seen if held], "the lock was held across: %r" % [w for w, h in seen if h])
        self.assertNotIn(SID, be._live, "the dismiss emptied the tail and popped the sid entry")

    def test_the_emptiness_check_of_every_sweep_runs_under_the_lock(self):
        """The deterministic twin of the hammers above, which PASS with the lock dedented out of
        _persist_echoes and _mark_dropped_echoes (a RuntimeError needs the interleaving to land inside
        one dict walk, and the hammers only sample for it; the source pin below was the one thing that
        caught the mutant). Every sweep's first step on the shared dict is its emptiness check, so a
        dict subclass runs the probe there (_ProbedTail): a second thread tries the lock without
        blocking. Under the lock the probe is refused, every time; without it the probe's stash lands
        mid-sweep — and in prune_live and retire_live_work the check right before the sid-level pop
        then pops a dict the stash just went into, orphaning it (the third refuter finding). Validated
        against a scratch copy with the two `with self._live_lock:` lines dedented out (2026-09-07)."""
        sweeps = (
            ("_persist_echoes", lambda be: be._persist_echoes(SID), {"echo:a": _echo("echo:a", "hello", 5)}),
            ("_mark_dropped_echoes", lambda be: be._mark_dropped_echoes(SID, [], refeed=False), {"echo:a": _echo("echo:a", "hello", 5)}),
            ("prune_live", lambda be: be.prune_live(SID, {"landed"}, (), 0), {"landed": _work("landed", 1)}),
            ("retire_live_work", lambda be: be.retire_live_work(SID), {"w1": _work("w1", 1)}),
            ("live_atoms", lambda be: be.live_atoms(SID), {"echo:a": _echo("echo:a", "hello", 5)}),
            ("dismiss_echo", lambda be: be.dismiss_echo(SID, uuid="echo:d"), {"echo:d": _echo("echo:d", "lost", 5, dropped=True)}),
        )
        for name, sweep, seed in sweeps:
            with self.subTest(sweep=name):
                be = _backend()
                be._update_reg = lambda sid, **f: None
                be._reply_on_disk = lambda sid, u: True
                tail = _ProbedTail(be._live_lock, seed)
                be._live[SID] = tail
                sweep(be)
                reachable = be._live.get(SID)
                orphaned = [k for k in tail.stashed if reachable is None or k not in reachable]
                self.assertEqual(tail.stashed, [], "%s ran its emptiness check WITHOUT the tail lock: a concurrent "
                                                   "stash got in (%r)%s" % (name, tail.stashed,
                                                                            "; the sid-level pop then orphaned %r" % orphaned if orphaned else ""))
                self.assertGreaterEqual(tail.refused, 1, "%s checked the tail's emptiness under the lock" % name)

    def test_every_sweep_and_stash_is_locked_by_source(self):
        """A pin on the rule's coverage, so a new unlocked walk fails here before it fails live: every
        method that touches `_live` takes the lock (or goes through _stash_live, which does)."""
        for name in ("live_atoms", "prune_live", "retire_live_work", "live_atom_kinds", "_persist_echoes",
                     "_mark_dropped_echoes", "dismiss_echo", "unqueue", "_forward", "_stash_live"):
            src = inspect.getsource(getattr(sb.SdkBackend, name))
            self.assertIn("with self._live_lock", src, "%s does not take the live-tail lock" % name)
        for name in ("send", "_reseed_echoes"):
            src = inspect.getsource(getattr(sb.SdkBackend, name))
            self.assertIn("self._stash_live(", src, "%s stashes outside _stash_live" % name)
            self.assertNotIn("self._live.setdefault", src)
        whole = inspect.getsource(sb.SdkBackend)
        self.assertEqual(whole.count("self._live.setdefault("), 2, "_stash_live and _forward are the only stashes")


class HandlerFailuresStayWithTheirMessage(unittest.TestCase):
    def setUp(self):
        sb.SdkSession._stream_fail_seen.clear()

    def test_a_raising_handler_is_logged_with_detail_and_does_not_propagate(self):
        lines = []
        be = _backend(lines)
        s = _session(be)
        def boom(msg, *a):
            raise KeyError(MSG_UUID)                    # the live failure's shape: the key IS a uuid
        s._on_message = boom
        msg = _SystemMessage("background_tasks_changed", {"session_id": UNKNOWN_SID})
        self.assertFalse(s._handle_stream_message(msg, _AssistantMessage, _ResultMessage, _SystemMessage))
        probs = _problems(be)
        self.assertEqual(len(probs), 1, "one problem line, in the error center's ring")
        txt = probs[0]
        self.assertIn("KeyError", txt, "the exception type")
        self.assertIn("SystemMessage/background_tasks_changed", txt, "the message type and subtype")
        self.assertRegex(txt, r"at .*\.py:\d+ \w+", "a file:line frame chain")
        self.assertIn(MSG_UUID[:8] + "…", txt, "the key's first 8 characters…")
        self.assertNotIn(MSG_UUID, txt, "…and not its full value")
        self.assertIn("stream continues", txt)
        self.assertIn(txt, lines, "the same line reached the kernel log")

    def test_the_line_says_what_that_message_lost_by_its_shape(self):
        """'(the transcript keeps it)' was true for assistant/user records only; a SystemMessage frame
        or a ResultMessage exists on the stream alone, and the line has to say so for the message it is
        about. By SHAPE, not class: a compact_boundary IS a transcript record, a
        `<local-command-stdout>` UserMessage from a control request is not, and a ResultMessage's turn
        'still closed' only if the settle ran — here the whole handler is replaced, so it did not."""
        lines = []
        be = _backend(lines)
        s = _session(be)
        def boom(msg, *a):
            raise ValueError("v")
        s._on_message = boom
        stdout = "<local-command-stdout>Set model to sonnet (claude-sonnet-x)</local-command-stdout>"
        cases = ((_SystemMessage("task_notification", {"task_id": "t1"}), "stream-only frame"),
                 (_ResultMessage(), "the turn did NOT settle"),
                 (_AssistantMessage([_TextBlock("x")]), "the transcript keeps the message"),
                 (_UserMessage([_TextBlock("a typed prompt")]), "the transcript keeps the message"),
                 (_UserMessage([_TextBlock(stdout)]), "a command's output line"),
                 (_SystemMessage("compact_boundary", {"compact_metadata": {"trigger": "manual"}}), "keeps the boundary record"),
                 (_StreamEvent(), "partial-stream event"))
        for msg, phrase in cases:
            sb.SdkSession._stream_fail_seen.clear()        # each case is a first-of-signature line…
            s._handle_stream_message(msg, _AssistantMessage, _ResultMessage, _SystemMessage)
            self.assertIn(phrase, lines[-1], "%s: %s" % (sb._describe_msg(msg), lines[-1]))
        self.assertNotIn("Set model", "".join(lines), "the command's output text is matched, never logged")
        # …in the kernel log; the ring folds them (same session, exception type and site), and its one
        # entry keeps the first line's phrase with the repeat count
        self.assertEqual(len(_problems(be)), 1)
        self.assertIn("stream-only frame", _problems(be)[0])
        self.assertIn("(6 repeats this kernel life", _problems(be)[0])
        self.assertEqual(sb._failure_consequence(_HookEventMessage("hook_started")),
                         sb._failure_consequence(_SystemMessage("status")), "every other frame: stream-only")
        # the result's phrase is a fact the caller passes, and the settled form names what did NOT run
        settled = sb._failure_consequence(_ResultMessage(), settled=True)
        self.assertIn("the turn still settled", settled)
        for word in ("spend", "live-tail sweep"):
            self.assertIn(word, settled, "the bookkeeping that stopped is named")
        self.assertIn("did NOT settle", sb._failure_consequence(_ResultMessage(), settled=False))
        # the stdout wrapper is matched on a plain-string content too, and a string that merely
        # mentions the tag mid-text is a typed prompt
        self.assertTrue(sb._is_command_stdout(_UserMessage(stdout)))
        self.assertFalse(sb._is_command_stdout(_UserMessage([_TextBlock("about " + stdout)])))

    def test_repeats_count_on_one_ring_entry_and_bust_the_feed_cache_once(self):
        """Every repeat is a kernel-log line; the error-center RING keeps one entry per (session,
        exception type, failing frame) and counts on it. Before: a handler failing on every streamed
        message put every repeat in the 100-entry ring (evicting every unrelated problem within a
        minute) and bumped problem_seq — the feed's cache key — per message."""
        lines = []
        be = _backend(lines)
        s = _session(be)
        def boom(msg, *a):
            raise KeyError("k")
        s._on_message = boom
        seq0 = be.problem_seq()
        for _ in range(5):
            s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage)
        probs = _problems(be)
        self.assertEqual(len(probs), 1, "ONE ring entry for the five failures: %r" % probs)
        self.assertRegex(probs[0], r"at .*\.py:\d+", "it carries the frame chain from the first")
        self.assertIn("(4 repeats this kernel life", probs[0], "…and the count of the repeats")
        self.assertEqual(be.problem_seq(), seq0 + 1, "one cache bust for the new problem, none per repeat")
        logged = [l for l in lines if re.search(r"KeyError( at \S+:\d+ \w+)? while handling", l)]
        self.assertEqual(len(logged), 5, "every failure is a kernel-log line — nothing silent")
        self.assertIn("repeat 5", logged[4])
        self.assertNotRegex(logged[4], r"\.py:\d+", "repeats do not re-print the chain")
        # a different exception TYPE at the same site is a new problem: a new entry, one more bust
        def boom2(msg, *a):
            raise ValueError("other")
        s._on_message = boom2
        s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage)
        self.assertEqual(len(_problems(be)), 2)
        self.assertEqual(be.problem_seq(), seq0 + 2)
        # …while the same failure on ANOTHER message kind folds into the first entry (same site, same type)
        s._on_message = boom
        s._handle_stream_message(_AssistantMessage([_TextBlock("x")]), _AssistantMessage, _ResultMessage, _SystemMessage)
        self.assertEqual(len(_problems(be)), 2)
        self.assertIn("(5 repeats this kernel life", _problems(be)[0])

    def test_a_ring_entry_evicted_meanwhile_enters_again_as_new_with_the_full_line(self):
        """The first-vs-repeat decision and the ring's dedupe were two independent counters: once
        the ring had evicted the entry, the next repeat logged the short line and the
        ring built its NEW row from it — no frame chain, no consequence, and a pointer at 'the first'
        in a kernel log that may have rotated. A repeat whose key is not in the ring now re-enters with
        the full line, counted; the repeats after it count on that row in the short form again."""
        lines = []
        be = _backend(lines)
        s = _session(be)
        def boom(msg, *a):
            raise KeyError("k")
        s._on_message = boom
        s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage)
        for i in range(be.PROBLEM_RING):
            be._log("unrelated problem %d" % i, problem=True)   # the ring turns over
        self.assertFalse([p for p in _problems(be) if "KeyError" in p], "the entry was evicted")
        seq = be.problem_seq()
        s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage)
        rows = [p for p in _problems(be) if "KeyError" in p]
        self.assertEqual(len(rows), 1, "back in the ring as a new entry")
        self.assertEqual(be.problem_seq(), seq + 1)
        self.assertRegex(rows[0], r"at .*\.py:\d+ \w+", "the re-entered row carries the frame chain")
        self.assertIn("handling stopped there (", rows[0], "…and the consequence")
        self.assertIn("(repeat 2 this kernel life; its earlier error-center entry was evicted)", rows[0])
        self.assertEqual(lines[-1], rows[0], "the kernel log got the same full line")
        s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage)
        rows = [p for p in _problems(be) if "KeyError" in p]
        self.assertEqual(len(rows), 1, "the next repeat counts on the re-entered row")
        self.assertIn("(1 repeat this kernel life", rows[0])
        self.assertRegex(rows[0], r"at .*\.py:\d+ \w+", "…which keeps its chain")
        self.assertIn("repeat 3", lines[-1])
        self.assertNotRegex(lines[-1], r"\.py:\d+", "…while the log line is the short form again")
        self.assertEqual(be.problem_seq(), seq + 1)

    def test_problem_keyed_reads_the_ring_now(self):
        be = _backend()
        self.assertFalse(be.problem_keyed(("k", 1)))
        be._log("a keyed problem", problem=True, key=("k", 1))
        self.assertTrue(be.problem_keyed(("k", 1)))
        self.assertFalse(be.problem_keyed(("k", 2)))
        for i in range(be.PROBLEM_RING):
            be._log("unrelated problem %d" % i, problem=True)
        self.assertFalse(be.problem_keyed(("k", 1)), "evicted → not in the ring")

    def test_the_failing_site_leads_the_line_and_fits_the_error_centers_row(self):
        """The error center shows a row's first 240 characters (kernel.py _sdk_problem_rows caps the text
        at 400, badge-mirror.ts at 240) and the chain sat at the END of the line, after the consequence
        prose — about 335 characters for a settled result — so the user never saw WHERE a handler
        failed. The failing site now follows the exception type, ahead of the prose; the full chain
        stays at the tail, innermost first, for the kernel log (2026-09-07 review)."""
        be = _backend()
        s = _session(be)
        e, tail = LogHelpers._long_named_chain(10)
        msg = _ResultMessage()
        s._settled_msg = msg                            # the settle ran: the longest consequence prose
        s._note_message_failure(msg, e)
        line = _problems(be)[0]
        site = "a_module_name_of_ordinary_length.py:20 " + tail
        self.assertIn(site, line[:240], "the failing frame is inside the error center's row: %r" % line[:240])
        self.assertTrue(line.startswith("sdk session web: KeyError at %s while handling a ResultMessage message; " % site),
                        "the site follows the type, ahead of the prose: %r" % line[:200])
        self.assertIn("the turn still settled", line)
        self.assertRegex(line, r"\. Frames, innermost first: %s < a_module_name_of_ordinary_length\.py:\d+ "
                               r"a_handler_frame_with_a_realistic_name_08 < " % site.replace(".", r"\."))
        # a failure with no traceback (a hand-built exception) names no site rather than raising
        s._note_message_failure(_SystemMessage("status"), ValueError("bare"))
        self.assertIn("ValueError at ? while handling a SystemMessage/status message", _problems(be)[-1])

    def test_a_clean_handler_reports_true_and_logs_nothing(self):
        be = _backend()
        s = _session(be)
        ok = s._handle_stream_message(_SystemMessage("task_notification", {"task_id": "gone1", "status": "completed"}),
                                      _AssistantMessage, _ResultMessage, _SystemMessage)
        self.assertTrue(ok)
        self.assertEqual(_problems(be), [])

    def test_a_reporter_failure_is_one_plain_line_and_never_propagates(self):
        """An exception whose __str__ raises reached _mask_ids inside the except and ended the drain —
        the outcome the containment exists to prevent. The reporter is guarded: one plain line from
        names only, and the stream goes on."""
        class Unrenderable(Exception):
            def __str__(self):
                raise TypeError("unrenderable")
        be = _backend()
        s = _session(be)
        def boom(msg, *a):
            raise Unrenderable()
        s._on_message = boom
        self.assertFalse(s._handle_stream_message(_SystemMessage("hook_started"), _AssistantMessage, _ResultMessage, _SystemMessage))
        probs = _problems(be)
        self.assertEqual(len(probs), 1)
        self.assertIn("Unrenderable", probs[0])
        self.assertIn("reporting it failed too (TypeError)", probs[0])

    def test_a_raising_log_callback_does_not_end_the_stream(self):
        """The kernel's log callback writes stderr; closed under a service restart it raises OSError from
        inside the report. The ring still has the entry (recorded before the callback), and nothing propagates."""
        be = _backend()
        s = _session(be)
        def badlog(m):
            raise OSError(32, "Broken pipe")
        be._log_cb = badlog                            # armed AFTER construction (construction logs too)
        def boom(msg, *a):
            raise KeyError("k")
        s._on_message = boom
        for _ in range(4):
            self.assertFalse(s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage))
        probs = _problems(be)
        self.assertEqual(len(probs), 2, "the report landed before the callback raised, and the broken "
                                        "callback is its own entry — each deduped across the repeats: %r" % probs)
        self.assertRegex(probs[0], r"KeyError at \S+:\d+ boom while handling a ResultMessage")
        self.assertIn("(3 repeats this kernel life", probs[0])
        self.assertIn("reporting it failed too (BrokenPipeError)", probs[1])
        self.assertIn("(3 repeats this kernel life", probs[1])


class TheSettleRunsWhateverTheResultsBookkeepingDid(unittest.TestCase):
    """With per-message containment, an exception in the ResultMessage branch no longer ends the
    stream — so THE SETTLE (inflight 0, the compaction/clear flags, 'waiting', the turn-end count, the
    queue wake, the poke, the rename ping, the deferred effort reconnect) must run whatever the
    result's bookkeeping did, or a gate-held queue stays parked and a deferred reconnect never fires
    while the session reads 'working'. The rule: the branch is ONE try from its first statement, and
    its finally is the whole settle. The first cut opened the try only ahead of the rewind steps, so
    the spend fold and even `inflight = 0` sat outside it: a raise there parked the turn while the
    failure line claimed it had closed (the probes were a failing spend write and a NaN usage field,
    both reproduced here)."""
    def setUp(self):
        sb.SdkSession._stream_fail_seen.clear()

    def _settle(self, be, s, ok_expected, msg=None):
        pokes, marks, turns = [], [], []
        be._poke_cb = lambda: pokes.append(1)
        orig_mark, orig_turn = s._mark, be._turn_completed
        s._mark = lambda state: (marks.append(state), orig_mark(state))
        be._turn_completed = lambda sid: (turns.append(sid), orig_turn(sid))
        out = {}
        async def run():
            s.loop = asyncio.get_running_loop()
            s._input_wake = asyncio.Event()
            s._wake = asyncio.Event()
            out["ok"] = s._handle_stream_message(msg if msg is not None else _ResultMessage(),
                                                 _AssistantMessage, _ResultMessage, _SystemMessage)
            await asyncio.sleep(0)
            out.update(wake=s._input_wake.is_set(), pokes=len(pokes), reconnect=s._reconnect,
                       still_deferred=s._reconnect_when_idle, wake_event=s._wake.is_set(),
                       marks=list(marks), turns=len(turns))
        asyncio.run(run())
        self.assertEqual(out["ok"], ok_expected)
        return out

    def _busy(self, be):
        s = _session(be)
        s.inflight = 1
        s._interrupted = True                          # the queue is gate-held behind an interrupted turn
        s._pending = ["queued during the interrupted turn"]
        s._reconnect_when_idle = True                  # an /effort change waiting for this turn's end
        s._compacting = True
        return s

    def _assert_settled(self, s, out):
        self.assertEqual(s.inflight, 0, "the turn settled")
        self.assertFalse(s._compacting or s._clearing or s._interrupted, "the turn's flags are down")
        self.assertEqual(out["marks"], ["waiting"], "the session reads waiting")
        self.assertEqual(out["turns"], 1, "the turn-end count moved once")
        self.assertTrue(out["wake"], "the input feeder was released")
        self.assertEqual(out["pokes"], 1, "the kernel was poked, once")
        self.assertTrue(out["reconnect"] and not out["still_deferred"], "the deferred reconnect fired")
        self.assertTrue(out["wake_event"], "…and the loop was woken for it")

    def test_a_raising_retire_still_settles_the_turn(self):
        be = _backend()
        s = self._busy(be)
        def boom(sid):
            raise OSError("the sweep's transcript read failed")
        be.retire_live_work = boom
        out = self._settle(be, s, ok_expected=False)
        self._assert_settled(s, out)
        probs = _problems(be)
        self.assertEqual(len(probs), 1)
        self.assertRegex(probs[0], r"OSError at \S+:\d+ boom while handling a ResultMessage")
        self.assertIn("the turn still settled", probs[0])

    def test_a_raising_spend_write_still_settles_the_turn(self):
        """_record_spend raising on a result that carries a cost — a step that sat
        BEFORE the try and before inflight = 0 in the first cut, so the turn never settled."""
        be = _backend()
        s = self._busy(be)
        def bad_spend(*a, **k):
            raise OSError(28, "No space left on device")
        be._record_spend = bad_spend
        # the map is on the double so the ring holds the containment's line alone: a paid result WITHOUT
        # modelUsage is itself reported once (SdkSession._note_usage_fallback), which is not this test
        out = self._settle(be, s, ok_expected=False,
                           msg=_result(total_cost_usd=0.5, usage={"input_tokens": 10},
                                       model_usage={"claude-x": {"inputTokens": 10}}))
        self._assert_settled(s, out)
        probs = _problems(be)
        self.assertEqual(len(probs), 1)
        self.assertRegex(probs[0], r"OSError at \S+:\d+ bad_spend while handling a ResultMessage")
        self.assertIn("the turn still settled", probs[0])
        self.assertIn("stopped where it failed", probs[0], "…and says the bookkeeping did not finish")

    def test_a_nan_usage_field_still_settles_the_turn(self):
        """The other live trigger: json.loads accepts a NaN token and int(float('nan')) raises ValueError
        inside the fold itself (no stub needed) — the same pre-try position."""
        import json
        be = _backend()
        s = self._busy(be)
        usage = json.loads('{"input_tokens": NaN, "output_tokens": 5}')
        out = self._settle(be, s, ok_expected=False, msg=_result(total_cost_usd=0.5, usage=usage))
        self._assert_settled(s, out)
        probs = _problems(be)
        self.assertEqual(len(probs), 1)
        self.assertIn("ValueError at ", probs[0])
        self.assertIn("while handling a ResultMessage", probs[0])
        self.assertIn("the turn still settled", probs[0])

    def test_a_failing_spend_step_skips_neither_the_rewind_consumption_nor_the_live_tail_sweep(self):
        """The spend accounting ran FIRST inside the try, so a NaN usage field or a failing spend write
        stopped the body there: the settle still closed the turn, but the rewind flag stayed armed (its
        registry clear and the branch-take event never ran) and the live tail kept the turn's work atoms —
        a reply the transcript never got stayed merged, forcing the turn open on the next build. The
        spend step runs last now (2026-09-07 review): its failure costs the spend readout and nothing else."""
        import json
        for trigger in ("a NaN usage field", "a failing spend write"):
            with self.subTest(trigger=trigger):
                be = _backend()
                s = self._busy(be)
                s._rewind_to = s._rewind_leaf = "11111111-2222-3333-4444-dddddddddd04"
                s._rewind_armed = True                 # the rewind turn is the one settling: its flag is consumed here
                resolved = []
                be.rewind_resolved_cb = lambda sid, outcome: resolved.append(outcome)
                be._stash_live(SID, "w1", _work("w1", 1))    # a work atom of the turn, never landed
                fields = {}
                if trigger == "a NaN usage field":
                    usage = json.loads('{"input_tokens": NaN, "output_tokens": 5}')
                else:
                    usage = {"input_tokens": 10}
                    # with the map the token count succeeds silently and the stubbed write is the one report: a
                    # paid result WITHOUT modelUsage is itself reported once (_note_usage_fallback)
                    fields["model_usage"] = {"claude-x": {"inputTokens": 10}}
                    def bad_spend(*a, **k):
                        raise OSError(28, "No space left on device")
                    be._record_spend = bad_spend
                out = self._settle(be, s, ok_expected=False, msg=_result(total_cost_usd=0.5, usage=usage, **fields))
                self._assert_settled(s, out)
                self.assertEqual(s._rewind_to, "", "the rewind flag was consumed before the spend step failed")
                self.assertFalse(s._rewind_armed)
                self.assertEqual(resolved, ["taken"], "the branch-take event reached the kernel")
                self.assertNotIn("w1", be._live.get(SID) or {}, "the live-tail sweep ran")
                probs = _problems(be)
                self.assertEqual(len(probs), 1, probs)
                self.assertIn("while handling a ResultMessage", probs[0])
                self.assertIn("the turn still settled", probs[0])

    def test_a_failure_before_the_branch_is_reported_as_not_settled(self):
        """The one place a ResultMessage's handling can fail OUTSIDE the branch is the elif chain
        above it (the move-settle check). No finally covers that, so the line must not claim a settle:
        the phrase is read from the settle's own marker, not from the class."""
        be = _backend()
        s = self._busy(be)
        def boom(msg):
            raise RuntimeError("the move check broke")
        s._consume_move_settle = boom
        out = self._settle(be, s, ok_expected=False)
        self.assertEqual(s.inflight, 1)
        self.assertFalse(out["wake"] or out["pokes"] or out["marks"])
        probs = _problems(be)
        self.assertEqual(len(probs), 1)
        self.assertIn("the turn did NOT settle", probs[0])
        self.assertNotIn("still settled", probs[0])

    def test_a_raising_state_write_skips_nothing_else_and_is_reported(self):
        """The settle's own raise-prone steps (the 'waiting' write, the turn-end count) are guarded and
        reported after the flags, so a failing one cannot skip the wake, the poke or the reconnect."""
        be = _backend()
        s = self._busy(be)
        def bad_mark(state):
            raise OSError(28, "No space left on device")
        s._mark = bad_mark
        pokes = []
        be._poke_cb = lambda: pokes.append(1)
        async def run():
            s.loop = asyncio.get_running_loop()
            s._input_wake = asyncio.Event()
            s._wake = asyncio.Event()
            self.assertTrue(s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage),
                            "the bookkeeping ran clean; the settle step's failure is its own report")
            await asyncio.sleep(0)
            self.assertEqual(s.inflight, 0)
            self.assertTrue(s._input_wake.is_set())
            self.assertEqual(len(pokes), 1)
            self.assertTrue(s._reconnect and not s._reconnect_when_idle)
        asyncio.run(run())
        probs = _problems(be)
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("settle (web): the 'waiting' state write failed: OSError", probs[0])

    def test_a_raising_log_callback_in_the_report_loop_keeps_the_bookkeepings_exception(self):
        """Three failures at once: the bookkeeping raises (a NaN usage field), a settle
        step fails (the 'waiting' write at ENOSPC) AND the kernel's log callback raises (stderr closed
        under a service restart). The report loop was the one unguarded step left after the flags, and
        its raise REPLACED the bookkeeping's exception: the containment reported a BrokenPipeError and
        the ValueError and its site never reached the ring. The report is guarded now: the settle runs
        to its end, the step's own report lands (the ring row precedes the callback in _log), and the
        line the containment files names the fold's failure and its frame."""
        import json
        be = _backend()
        s = self._busy(be)
        def bad_mark(state):
            raise OSError(28, "No space left on device")
        s._mark = bad_mark
        def badlog(m):
            raise OSError(32, "Broken pipe")
        be._log_cb = badlog                            # armed AFTER construction (construction logs too)
        usage = json.loads('{"input_tokens": NaN, "output_tokens": 5}')
        out = self._settle(be, s, ok_expected=False, msg=_result(total_cost_usd=0.5, usage=usage))
        self.assertEqual(s.inflight, 0, "the turn settled")
        self.assertTrue(out["wake"] and out["pokes"] == 1, "the feeder was released and the kernel poked once")
        self.assertTrue(out["reconnect"] and not out["still_deferred"] and out["wake_event"],
                        "the deferred reconnect fired")
        probs = _problems(be)
        self.assertTrue([p for p in probs if "settle (web): the 'waiting' state write failed: OSError" in p],
                        "the settle step's own report landed before the callback raised: %r" % probs)
        filed = [p for p in probs if "while handling a ResultMessage" in p]
        self.assertEqual(len(filed), 1, probs)
        self.assertRegex(filed[0], r"ValueError at \S+:\d+ _turn_usage while handling a ResultMessage",
                         "the bookkeeping's exception is the one the containment files, not the callback's")
        self.assertIn("the turn still settled", filed[0])
        self.assertIn("_on_message", filed[0], "…with the fold's frame")
        self.assertNotIn("BrokenPipeError while handling", " ".join(probs))

    def test_a_clean_settle_runs_the_same_plumbing_once(self):
        be = _backend()
        s = self._busy(be)
        out = self._settle(be, s, ok_expected=True)
        self._assert_settled(s, out)
        self.assertEqual(_problems(be), [])

    def test_the_branch_is_one_try_and_its_finally_is_the_settle_by_source(self):
        src = inspect.getsource(sb.SdkSession._on_message)
        i_branch = src.index("elif isinstance(msg, ResultMessage):")
        i_try = src.index("try:", i_branch)
        i_finally = src.index("finally:", i_try)
        i_next = src.index("\n        elif ", i_finally)                 # the next branch of the chain
        head = src[i_branch:i_try]
        self.assertFalse([l for l in head.splitlines()[1:] if l.strip() and not l.strip().startswith("#")],
                         "no statement between the branch head and the try: %r" % head)
        body, settle = src[i_try:i_finally], src[i_finally:i_next]
        for step in ("self.backend._record_spend(", "self.backend.retire_live_work(self.sid)",
                     "self.backend._complete_rewind_wait(self)", "asyncio.ensure_future(self._do_refresh_usage())"):
            self.assertIn(step, body, "%s is bookkeeping, inside the try" % step)
            self.assertNotIn(step, settle)
        for step in ("self.inflight = 0", "self._inflight_texts.clear()", "self._compacting = False",
                     "self._clearing = False", "self._interrupted = False", 'self._mark("waiting")',
                     "self.backend._turn_completed(self.sid)", "self._input_wake.set()", "self.backend._poke()",
                     "self.backend._deliver_rename_ping(self)", "self._reconnect_when_idle and not self.ended",
                     "self._settled_msg = msg"):
            self.assertIn(step, settle, "%s is the settle, in the finally" % step)
            self.assertNotIn(step, body)
        # nothing that can raise precedes the flag writes in the finally
        self.assertLess(settle.index("self._input_wake.set()"), settle.index("step()"))
        self.assertLess(settle.index("self._input_wake.set()"), settle.index("self.backend._poke()"))


class TheDeferredReconnectTakesTheHeldQueueWithIt(unittest.TestCase):
    """Pre-existing, found while reviewing the settle: its finally wakes the input
    feeder and THEN arms the deferred reconnect, both wakeups queued FIFO on the loop, so the feeder
    ran first and fed a message gate-held behind the interrupted turn to the client the waker was
    about to tear down; the teardown stranded it in flight and _reconcile_stranded — on a resumable
    conversation, where a re-feed could duplicate — flagged it 'never delivered'. A message queued
    behind a stopped turn while the user changed /effort had to be sent again. inputs() now holds the
    queue while a reconnect is armed (_reconnect), so the NEW client's feeder takes the head.

    The REAL _amain runs here (its inputs() closure, the teardown, _reconcile_stranded) against a
    stand-in SDK module whose client records what it was fed and when — installed in sys.modules for
    the test (the backend imports the SDK lazily, at the top of _amain) and removed after."""
    FSID = "11111111-2222-3333-4444-ffffffffff01"   # the CLI's own session id, announced by the init

    class _Client:
        instances = []

        def __init__(self, options=None, transport=None):
            self.options = options
            self.no = len(type(self).instances) + 1
            type(self).instances.append(self)
            self.writes = []                # (text, when) — when relative to this client's result and teardown
            self.first_write = asyncio.Event()
            self.release = asyncio.Event()  # the test releases the turn's ResultMessage
            self.result_sent = self.torn_down = False
            self.interrupts = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            self.torn_down = True
            return False

        async def query(self, prompt, session_id="default"):
            async for turn in prompt:
                when = ("after-teardown" if self.torn_down else
                        "after-result" if self.result_sent else "before-result")
                self.writes.append((turn["message"]["content"][0]["text"], when))
                self.first_write.set()

        async def interrupt(self):
            self.interrupts += 1

        async def get_context_usage(self):
            return {"percentage": 2, "model": "claude-x"}

        async def get_server_info(self):
            return {}

        async def receive_messages(self):
            await self.first_write.wait()
            yield _SystemMessage("init", {"model": "claude-x", "permissionMode": "acceptEdits",
                                          "session_id": TheDeferredReconnectTakesTheHeldQueueWithIt.FSID},
                                 uuid="s-%d" % self.no)
            yield _AssistantMessage([_TextBlock("working on it")], uuid="a-%d" % self.no)
            await self.release.wait()
            self.result_sent = True
            yield _result(uuid="r-%d" % self.no, subtype="success", is_error=False, num_turns=1,
                          session_id=TheDeferredReconnectTakesTheHeldQueueWithIt.FSID, duration_ms=1,
                          duration_api_ms=1, total_cost_usd=0.01, usage={"input_tokens": 1, "output_tokens": 1},
                          result="ok", parent_tool_use_id=None)
            await asyncio.Event().wait()    # the stream parks until the teardown cancels it

    class _Options:
        def __init__(self, **kw):
            self.session_id = self.resume = None
            for k, v in kw.items():
                setattr(self, k, v)

    def setUp(self):
        sb.SdkSession._stream_fail_seen.clear()
        self._Client.instances = []
        fake = types.ModuleType("claude_agent_sdk")
        fake.__spec__ = ModuleSpec("claude_agent_sdk", loader=None)   # sdk_importable's find_spec reads it
        # the matcher stand-in bears attributes like the SDK's dataclass: the options loop sets each matcher's `timeout`
        # under hosts (the default since T348), which a plain dict refused
        fake.ClaudeSDKClient, fake.ClaudeAgentOptions, fake.HookMatcher = self._Client, self._Options, (lambda **kw: types.SimpleNamespace(**kw))
        fake.AssistantMessage, fake.ResultMessage = _AssistantMessage, _ResultMessage
        fake.SystemMessage, fake.TextBlock = _SystemMessage, _TextBlock
        self._saved_sdk = sys.modules.get("claude_agent_sdk")
        sys.modules["claude_agent_sdk"] = fake
        self.state = tempfile.mkdtemp()
        # this class runs the REAL connect loop against the fake client on the plain-child road: hosts OFF explicitly,
        # since they are on by default (T348) and a bare state dir would send the connect to a real host spawn
        open(os.path.join(self.state, "session-hosts"), "w").write("off")
        cwd = os.path.join(self.state, "proj")
        os.makedirs(cwd)
        self.lines = []
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None,
                                log=lambda m, **k: self.lines.append(str(m)))
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": cwd}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        async def _noop(): pass
        self.s._do_refresh_usage = _noop            # the stand-in client has no control channel for /usage
        self.be.sessions[SID] = self.s

    def tearDown(self):
        self.s.shutdown()
        if self.s.thread.ident is not None:            # the source pin never starts it
            self.s.thread.join(timeout=10)
        if self._saved_sdk is None:
            sys.modules.pop("claude_agent_sdk", None)
        else:
            sys.modules["claude_agent_sdk"] = self._saved_sdk
        shutil.rmtree(self.state, ignore_errors=True)

    def _wait(self, pred, what, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for %s; clients fed %r; log tail %r"
                  % (what, [c.writes for c in self._Client.instances], self.lines[-6:]))

    def _echo(self, key, text):
        echo = _echo(key, text, int(time.time()))
        self.be._stash_live(SID, key, echo)      # the chat's echo of the send, as send() stashes it
        return echo

    def _first_turn(self):
        """Connect and run one turn to its in-flight state: client 1 has the first turn and the init
        named the conversation (resume_sid), so a later reconnect resumes rather than starts fresh."""
        s = self.s
        s.start()
        self._wait(lambda: s.client is not None, "the first connect")
        self._echo("echo:first", "first turn")
        s.enqueue("first turn")
        self._wait(lambda: s.inflight == 1 and s.resume_sid == self.FSID, "the first turn in flight and its init")
        c1 = self._Client.instances[0]
        self.assertEqual(c1.writes, [("first turn", "before-result")])
        return c1

    def test_a_fed_turn_gets_the_pop_stamp_and_its_row_carries_both_event_stamps(self):
        """The two event stamps the latency figures read: the feeder's pop stamps fedT as it hands the
        fresh turn's text to the client, the first streamed work atom stamps firstOutT, and the settle's
        row carries both (rounded to the millisecond) and then spends them. Driven through the real
        _amain on the stand-in SDK; every cross-thread read waits on its event, and what is asserted is
        presence and order, never a duration."""
        t0 = time.time()
        c1 = self._first_turn()
        s = self.s
        self._wait(lambda: s._fed_t is not None, "the pop stamp")
        self.assertIsInstance(s._fed_t, float)
        self.assertTrue(t0 <= s._fed_t <= time.time(), "the pop's own clock reading")
        self._wait(lambda: s._first_out_t is not None, "the first work atom")
        fed_seen, first_seen = s._fed_t, s._first_out_t
        self.assertLessEqual(fed_seen, first_seen, "fed, then the first output")
        s.loop.call_soon_threadsafe(c1.release.set)          # the turn's ResultMessage: the settle runs
        self._wait(lambda: s.inflight == 0, "the settle")
        rows = [json.loads(ln) for ln in (self.be.state_dir / sb.TURNS_FILE).read_text().splitlines()]
        self.assertEqual(len(rows), 1, "one row for the settled turn")
        row = rows[0]
        self.assertEqual((row["fedT"], row["firstOutT"]), (round(fed_seen, 3), round(first_seen, 3)))
        self.assertTrue(row["fedT"] <= row["firstOutT"] <= row["resultT"])
        self.assertEqual((row["fedTexts"], row["opener"]), (1, "human"))
        self.assertIn("usd", row, "the spend accounting's figure rode along")
        self.assertTrue(s._fed_t is None and s._first_out_t is None and s._turn_spend is None,
                        "spent with the row")

    def test_a_head_held_behind_an_interrupted_turn_is_fed_to_the_new_client_and_never_flagged(self):
        s = self.s
        c1 = self._first_turn()
        s.interrupt()                                  # the stop button: the queue gate closes (inflight>0 and _interrupted)
        self.assertTrue(s._interrupted)
        self._wait(lambda: c1.interrupts == 1, "the interrupt control request")
        echo = self._echo("echo:head", "queued head")
        s.enqueue("queued head")
        time.sleep(0.2)                                # every chance to be (wrongly) fed now
        self.assertEqual(s.pending(), ["queued head"], "gate-held behind the interrupted turn")
        self.assertEqual(c1.writes, [("first turn", "before-result")])
        s.request_reconnect()                          # an /effort change while busy: deferred to the turn's end
        self._wait(lambda: s._reconnect_when_idle, "the deferred reconnect armed")
        s.loop.call_soon_threadsafe(c1.release.set)    # the interrupted turn's ResultMessage: the settle runs
        self._wait(lambda: len(self._Client.instances) == 2 and self._Client.instances[1].writes,
                   "the reconnected client to be fed")
        c2 = self._Client.instances[1]
        self.assertEqual(c1.writes, [("first turn", "before-result")], "nothing fed to the client being torn down")
        self.assertEqual(c2.writes, [("queued head", "before-result")], "the new client took the head")
        self.assertEqual(s.pending(), [])
        self.assertEqual((s.inflight, list(s._inflight_texts)), (1, ["queued head"]), "in flight on the new client")
        self.assertFalse(echo.get("dropped"), "not flagged never-delivered")

    def test_a_send_racing_an_idle_arm_is_fed_to_the_new_client(self):
        """The other arm: request_reconnect on an IDLE session sets _reconnect at once, and a send whose
        wake queued behind the waker's used to be popped by the feeder before the teardown ran — the
        race _reconcile_stranded's docstring names. Both land in one loop step here, the order the
        race needs; the hold keeps the head for the new client."""
        s = self.s
        c1 = self._first_turn()
        s.loop.call_soon_threadsafe(c1.release.set)
        self._wait(lambda: s.inflight == 0, "the first turn to settle")
        echo = self._echo("echo:late", "late send")
        def arm_then_send():
            s._do_request_reconnect(True)              # idle → _reconnect = True and the waker is woken
            s.enqueue("late send")                     # …its wake queued behind the waker's
        s.loop.call_soon_threadsafe(arm_then_send)
        self._wait(lambda: len(self._Client.instances) == 2 and self._Client.instances[1].writes,
                   "the reconnected client to be fed")
        self.assertEqual(c1.writes, [("first turn", "before-result")], "nothing fed to the client being torn down")
        self.assertEqual(self._Client.instances[1].writes, [("late send", "before-result")])
        self.assertFalse(echo.get("dropped"))

    def test_a_deferred_arm_alone_does_not_hold_mid_turn_forwards(self):
        """The hold keys on _reconnect (armed: the teardown is next), not on _reconnect_when_idle (an
        /effort change waiting for the turn to end): a message sent mid-turn with one pending is still
        forwarded to the running turn's client — the designed forward — and the settle folds it."""
        s = self.s
        c1 = self._first_turn()
        s.request_reconnect()
        self._wait(lambda: s._reconnect_when_idle, "the deferred reconnect armed")
        echo = self._echo("echo:mid", "mid-turn note")
        s.enqueue("mid-turn note")
        self._wait(lambda: len(c1.writes) == 2, "the mid-turn forward")
        self.assertEqual(c1.writes[1], ("mid-turn note", "before-result"))
        s.loop.call_soon_threadsafe(c1.release.set)
        self._wait(lambda: len(self._Client.instances) == 2 and s.client is self._Client.instances[1], "the reconnect")
        self.assertEqual(self._Client.instances[1].writes, [], "nothing was held for the new client")
        self.assertEqual(s.inflight, 0)
        self.assertFalse(echo.get("dropped"), "folded into the turn, not flagged")

    def test_the_hold_keys_on_the_armed_flag_by_source(self):
        src = inspect.getsource(sb.SdkSession._amain)
        i_inputs = src.index("async def inputs():")
        i_pop = src.index("item, _meta = self._pop_for_feed_locked(fi)", i_inputs)   # T252c: the head leaves through the feed pop (T306: the first unheld slot)
        gate = src[i_inputs:i_pop]
        self.assertIn("blocked = blocked or self._reconnect\n", gate, "the armed flag is a hold in the gate")
        self.assertNotIn("_reconnect_when_idle", gate, "…and the deferred flag is not (mid-turn forwards flow)")


class TheDrainLoopOutlivesAHandlerFailure(unittest.TestCase):
    def setUp(self):
        sb.SdkSession._stream_fail_seen.clear()

    class _Client:
        def __init__(self, msgs, then=None):
            self.msgs, self.then = msgs, then
        async def receive_messages(self):
            for m in self.msgs:
                yield m
            if self.then is not None:
                raise self.then

    def test_the_stream_continues_past_the_message_that_failed(self):
        be = _backend()
        s = _session(be)
        seen = []
        bad = _SystemMessage("vcs_state_changed", {"session_id": UNKNOWN_SID})
        def handler(msg, *a):
            if msg is bad:
                raise KeyError(UNKNOWN_SID)
            seen.append(msg)
        s._on_message = handler
        good1, good2 = _SystemMessage("thinking_tokens"), _ResultMessage()
        asyncio.run(s._drain(self._Client([good1, bad, good2]), _AssistantMessage, _ResultMessage, _SystemMessage))
        self.assertEqual(seen, [good1, good2], "the messages after the failure were still handled")
        self.assertEqual(len(_problems(be)), 1)

    def test_the_stream_continues_past_a_failure_whose_report_fails(self):
        class Unrenderable(Exception):
            def __str__(self):
                raise TypeError("unrenderable")
        be = _backend()
        s = _session(be)
        seen = []
        bad = _SystemMessage("hook_response")
        def handler(msg, *a):
            if msg is bad:
                raise Unrenderable()
            seen.append(msg)
        s._on_message = handler
        good = _ResultMessage()
        asyncio.run(s._drain(self._Client([bad, good]), _AssistantMessage, _ResultMessage, _SystemMessage))
        self.assertEqual(seen, [good])

    def test_a_stream_fault_still_ends_the_loop(self):
        """The transport closing (the SDK re-raises it out of receive_messages) is the stream ending:
        the loop propagates it as before, so the client teardown and the crash heal still run."""
        be = _backend()
        s = _session(be)
        s._on_message = lambda *a: None
        with self.assertRaises(RuntimeError):
            asyncio.run(s._drain(self._Client([_ResultMessage()], then=RuntimeError("transport closed")),
                                 _AssistantMessage, _ResultMessage, _SystemMessage))

    def test_ended_stops_the_loop(self):
        be = _backend()
        s = _session(be)
        seen = []
        s._on_message = lambda msg, *a: seen.append(msg)
        s.ended = True
        asyncio.run(s._drain(self._Client([_ResultMessage(), _ResultMessage()]), _AssistantMessage, _ResultMessage, _SystemMessage))
        self.assertEqual(seen, [])

    def test_amain_routes_the_receive_loop_through_drain(self):
        src = inspect.getsource(sb.SdkSession._amain)
        self.assertIn("self._drain(client, AssistantMessage, ResultMessage, SystemMessage)", src)
        self.assertNotIn("self._on_message(msg", src, "no bare dispatch is left in the loop")
        self.assertIn("_compact_tb(e)", src, "the transport catch names the frame chain")
        self.assertIn("_mask_ids(e)", src, "…and masks ids in the exception text")


class UnknownIdsInTheNewSdkShapesDoNotRaise(unittest.TestCase):
    """The shapes the newer CLI emits, each carrying an id the kernel has no record for, through the
    REAL handler: nothing raises and the live tail stays clean."""
    def _run(self, s, msg):
        return s._handle_stream_message(msg, _AssistantMessage, _ResultMessage, _SystemMessage)

    def test_sidechain_messages_of_an_unregistered_subagent(self):
        be = _backend()
        s = _session(be)
        self.assertTrue(self._run(s, _UserMessage([_TextBlock("kickoff")], uuid="u9", parent_tool_use_id="toolu_unreg")))
        self.assertTrue(self._run(s, _AssistantMessage([_TextBlock("reply")], model="claude-tiny", uuid="a9",
                                                       parent_tool_use_id="toolu_unreg")))
        self.assertEqual(be.live_atoms(SID), [], "sidechain traffic never enters the parent's tail")
        self.assertNotEqual(s.model, "claude-tiny", "a subagent's model is never learned")
        self.assertEqual(_problems(be), [])

    def test_new_system_subtypes_with_an_unknown_session_id_do_not_raise(self):
        lines = []
        be = _backend(lines)
        s = _session(be)
        for st in ("background_tasks_changed", "thinking_tokens", "vcs_state_changed"):
            for _ in range(2):
                self.assertTrue(self._run(s, _SystemMessage(st, {"session_id": UNKNOWN_SID, "tasks": []})))
        for st in ("hook_started", "hook_response"):
            for _ in range(2):
                self.assertTrue(self._run(s, _HookEventMessage(st, {"session_id": UNKNOWN_SID, "hook_id": MSG_UUID})))
        self.assertEqual(_problems(be), [], "a subtype without a handler branch is not a failure")
        self.assertEqual(be.live_atoms(SID), [], "…and produces no atom")
        for l in lines:
            self.assertNotIn(UNKNOWN_SID, l, "never the payload's values in the log")

    def test_task_events_for_a_task_the_kernel_never_recorded(self):
        be = _backend()
        s = _session(be)
        for st, data in (("task_notification", {"task_id": "b_gone", "status": "completed"}),
                         ("task_updated", {"task_id": "b_gone", "patch": {"status": "killed"}}),
                         ("task_progress", {"task_id": "b_new", "description": "d"})):
            self.assertTrue(self._run(s, _SystemMessage(st, data)))
        self.assertEqual([t["desc"] for t in s._live_bg_tasks()], ["d"],
                         "an unknown id ENDING is a no-op; progress on an unknown id self-heals an entry")
        self.assertEqual(_problems(be), [])


class LogHelpers(unittest.TestCase):
    def test_mask_ids_keeps_eight_characters_of_a_uuid_and_clips(self):
        self.assertEqual(sb._mask_ids(KeyError(MSG_UUID)), "'%s…'" % MSG_UUID[:8])
        self.assertEqual(sb._mask_ids("x" * 200, cap=10), "x" * 10 + "…")
        self.assertEqual(sb._mask_ids("plain"), "plain")

    def test_compact_tb_is_a_file_line_chain_without_locals(self):
        secret = "the message content"      # noqa: F841 — a local that must NOT appear
        def inner():
            raise ValueError("v")
        try:
            inner()
        except ValueError as exc:
            e = exc                                    # the except clause unbinds its own name on exit
            chain = sb._compact_tb(e)
        self.assertRegex(chain, r"^test_sdk_stream_survives_handler_errors\.py:\d+ inner"
                                r" < test_sdk_stream_survives_handler_errors\.py:\d+ test_compact_tb_is_a_file_line_chain_without_locals$",
                         "innermost first: the failing site, then its caller")
        self.assertNotIn("secret", chain)
        self.assertNotIn(secret, chain)
        self.assertEqual(sb._compact_tb(ValueError("no traceback")), "?")
        self.assertRegex(sb._failing_site(e), r"^test_sdk_stream_survives_handler_errors\.py:\d+ inner$")
        self.assertEqual(sb._failing_site(ValueError("no traceback")), "?")

    def test_the_failing_site_skips_synthetic_scopes_and_names_the_function(self):
        """Before Python 3.12 (PEP 709) a comprehension runs in its own frame, so a raise inside one has
        `<dictcomp>` as its innermost frame: the site read `file:line <dictcomp>` on 3.10 and 3.11 and
        `file:line _turn_usage` on 3.12 and 3.13, and the NaN-usage settle test went red on two of the
        four interpreters (upstream CI on the 2026-09-07 review). The site names the innermost REAL
        function: every angle-bracketed scope is skipped, so a generator expression (its own frame on
        every interpreter) names the function too, at the line the comprehension is on — while the
        chain keeps every frame the interpreter made. With nothing real to name, the innermost stands."""
        me = "test_sdk_stream_survives_handler_errors.py"
        def a_fold_with_a_comprehension(u):
            return {k: int(u[k]) for k in ("input_tokens",)}
        def a_count_with_a_generator(u):
            return sum(int(u[k]) for k in ("input_tokens",))
        def a_caller(fn):
            fn({"input_tokens": float("nan")})
        for fn in (a_fold_with_a_comprehension, a_count_with_a_generator):
            try:
                a_caller(fn)
            except ValueError as exc:
                e = exc                                # the except clause unbinds its own name on exit
            line = fn.__code__.co_firstlineno + 1      # the one-line body: the comprehension's line
            self.assertEqual(sb._failing_frame(e), (me, line, fn.__name__))
            self.assertEqual(sb._failing_site(e), "%s:%d %s" % (me, line, fn.__name__))
            chain = sb._compact_tb(e)
            self.assertIn("%s:%d %s < %s:" % (me, line, fn.__name__, me), chain, "the function is a step of the chain")
            self.assertEqual(chain.startswith("%s:%d <" % (me, line)),
                             fn is a_count_with_a_generator or sys.version_info < (3, 12),
                             "the chain keeps the synthetic frame wherever the interpreter made one: %r" % chain)
        # nothing real to name: a raise caught at the top level of an exec'd string has only synthetic
        # frames (`<module>`, and before 3.12 the comprehension's own) — the innermost one is named
        ns = {}
        exec(compile("try:\n    [1 // 0 for _ in (0,)]\nexcept ZeroDivisionError as exc:\n    err = exc\n",
                     "a_string_of_code.py", "exec"), ns)
        self.assertRegex(sb._failing_site(ns["err"]), r"^a_string_of_code\.py:2 <(listcomp|module)>$")

    def test_compact_tb_is_bounded_to_the_innermost_frames_and_a_length_cap(self):
        """A RecursionError's chain ran to 18 KB — into the error-center ring and every feed payload that
        carries it. The chain keeps the innermost COMPACT_TB_FRAMES frames (the failing site LEADS, since
        2026-09-07), says how many outer ones it dropped, and fits COMPACT_TB_CHARS by dropping MORE
        outer frames — never by clipping the failing frame."""
        def rec(n):
            return rec(n + 1)
        try:
            rec(0)
        except RecursionError as exc:
            e = exc                                    # the except clause unbinds its own name on exit
            chain = sb._compact_tb(e)
            depth = len(traceback.extract_tb(e.__traceback__))
        self.assertGreater(depth, 100)
        self.assertLessEqual(len(chain), sb.COMPACT_TB_CHARS)
        self.assertRegex(chain, r" < …%d outer frames dropped…$" % (depth - sb.COMPACT_TB_FRAMES))
        self.assertEqual(chain.count(" < "), sb.COMPACT_TB_FRAMES, "the kept frames plus the suffix")
        self.assertRegex(chain, r"^test_sdk_stream_survives_handler_errors\.py:\d+ rec < ", "the innermost frame leads: %r" % chain[:60])
        # a tight cap with every frame allowed: the cap drops outer frames and the failing one stays
        # (150: room for the suffix and two of this file's ~50-character frames, not three)
        tight = sb._compact_tb(e, max_frames=depth, cap=150)
        self.assertLessEqual(len(tight), 150)
        self.assertRegex(tight, r"^test_sdk_stream_survives_handler_errors\.py:\d+ rec < ", "the innermost frame survives the cap: %r" % tight)
        kept = tight.count(" < ")                      # the kept frames plus the suffix
        self.assertEqual(kept, 2, "two frames fit in 150 characters, and both are kept whole: %r" % tight)
        self.assertRegex(tight, r" < …%d outer frames dropped…$" % (depth - kept), "the suffix counts every drop")
        # one dropped frame reads as one (an unbounded cap, so only the frame bound drops)
        self.assertRegex(sb._compact_tb(e, max_frames=depth - 1, cap=10 ** 6), r" < …1 outer frame dropped…$")

    @staticmethod
    def _long_named_chain(depth, tail="a_failing_function_with_a_long_name", module="a_module_name_of_ordinary_length.py"):
        """An exception raised through `depth` frames whose names are ~40 characters (a plugin, a hook, a
        test) — eight of them overflow COMPACT_TB_CHARS. Returns (exception, innermost function name)."""
        names = ["a_handler_frame_with_a_realistic_name_%02d" % i for i in range(depth - 1)] + [tail]
        src = "".join("def %s(x):\n    return %s(x)\n" % (names[i], names[i + 1]) for i in range(depth - 1))
        src += "def %s(x):\n    raise KeyError('k')\n" % tail
        ns = {}
        exec(compile(src, module, "exec"), ns)
        try:
            ns[names[0]](1)
        except KeyError as e:
            return e, tail

    def test_the_cap_drops_outer_frames_and_keeps_the_failing_one(self):
        """The first cut clipped the chain's TAIL at the cap — the innermost frame —
        so a chain through long-named frames lost its failing site."""
        e, tail = self._long_named_chain(10)
        frames = traceback.extract_tb(e.__traceback__)          # the builder's own frame + the ten exec'd ones
        full = " > ".join(sb._frame_step(f) for f in frames[-sb.COMPACT_TB_FRAMES:])
        self.assertGreater(len(full), sb.COMPACT_TB_CHARS, "the shape engages the cap")
        chain = sb._compact_tb(e)
        self.assertLessEqual(len(chain), sb.COMPACT_TB_CHARS)
        self.assertTrue(chain.startswith("a_module_name_of_ordinary_length.py:20 %s < " % tail),
                        "the failing frame is the chain's first step: %r" % chain[:80])
        kept = chain.count(" < ")
        self.assertRegex(chain, r" < …%d outer frames dropped…$" % (len(frames) - kept))
        self.assertLess(kept, sb.COMPACT_TB_FRAMES, "the cap dropped frames the frame bound had kept")
        self.assertNotIn("…", chain[:chain.rindex(" < ")], "no step is clipped")
        self.assertEqual(sb._failing_frame(e), ("a_module_name_of_ordinary_length.py", 20, tail))

    def test_an_innermost_frame_wider_than_the_cap_keeps_its_file_and_line(self):
        e, tail = self._long_named_chain(3)
        site = "%s:%d" % ("a_module_name_of_ordinary_length.py", 6)
        one = sb._compact_tb(e, cap=80)
        self.assertEqual(len(one), 80)
        dropped = len(traceback.extract_tb(e.__traceback__)) - 1
        self.assertTrue(one.startswith(site + " " + tail[:8]), "the file:line stands, then the clipped name: %r" % one)
        self.assertTrue(one.endswith("… < …%d outer frames dropped…" % dropped), "the function name is what gets clipped, the suffix stands: %r" % one)
        self.assertEqual(sb._compact_tb(e, cap=10000), " < ".join(sb._frame_step(f) for f in reversed(traceback.extract_tb(e.__traceback__))),
                         "no cap engaged: the full chain, innermost first, no suffix")
        self.assertEqual(sb._failing_frame(ValueError("no traceback")), None)

    def test_two_failing_sites_under_long_chains_are_two_ring_entries(self):
        """With the cap engaged, the dedupe key read off the rendering was the literal
        '…', folding every long-chained failure of one type into one entry. The key is the innermost
        frame's (file, line, function), read from the traceback."""
        be = _backend()
        s = _session(be)
        for tail in ("a_failing_function_with_a_long_name", "a_completely_different_failing_function"):
            e, _ = self._long_named_chain(10, tail=tail)
            s._note_message_failure(_ResultMessage(), e)
        probs = _problems(be)
        self.assertEqual(len(probs), 2, "one entry per failing site: %r" % [p[:120] for p in probs])
        for p, tail in zip(probs, ("a_failing_function_with_a_long_name", "a_completely_different_failing_function")):
            site = "a_module_name_of_ordinary_length.py:20 " + tail
            self.assertIn("KeyError at %s while handling" % site, p, "each entry names its own failing frame: %r" % p[:120])
            self.assertIn("Frames, innermost first: %s < " % site, p, "…and its chain leads with it: %r" % p[-200:])
        e, _ = self._long_named_chain(10)
        s._note_message_failure(_ResultMessage(), e)
        self.assertEqual(len(_problems(be)), 2, "the same site again counts on its entry")
        self.assertIn("(1 repeat this kernel life", _problems(be)[0])

    def test_a_recursing_handler_yields_one_bounded_problem_line(self):
        be = _backend()
        s = _session(be)
        def rec(msg, *a):
            return rec(msg, *a)
        s._on_message = rec
        self.assertFalse(s._handle_stream_message(_ResultMessage(), _AssistantMessage, _ResultMessage, _SystemMessage))
        probs = _problems(be)
        self.assertEqual(len(probs), 1)
        self.assertIn("RecursionError", probs[0])
        self.assertLess(len(probs[0]), 1000, "was 18 KB before the bound")

    def test_describe_msg_names_type_and_subtype_only(self):
        self.assertEqual(sb._describe_msg(_SystemMessage("api_retry", {"error": "content"})), "SystemMessage/api_retry")
        self.assertEqual(sb._describe_msg(_AssistantMessage([_TextBlock("content")])), "AssistantMessage")
        self.assertEqual(sb._describe_msg(_HookEventMessage("hook_started")), "HookEventMessage/hook_started")


if __name__ == "__main__":
    unittest.main()
