#!/usr/bin/env python3
"""The API-error "Retry" pastes "retry" into the session to resume the stalled turn — tagged with the
romp-injected marker so the chat renders it as a GRAY romp bubble (romp sent it), not a blue human "retry"
prompt (the user 2026-06-19). Source-pin on the kernel's inject + an end-to-end author_of check.

Also covers auto-retry IDEMPOTENCY (the user 2026-07-08): the 10s auto-loop must NOT stack a fresh "retry"
when the one romp already sent is still queued and unconsumed — that piled N bare "retry"s into the SDK
queue during one API-error storm (the "retry retry retry retry…" card). A MANUAL "Retry now" still fires.
"""
import json
import os
import types
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
SRC = open(os.path.join(BIN, "romp-kernel")).read()
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_apiretry", os.path.join(BIN, "romp-kernel"))

RETRY = "retry\n\n<!-- romp-injected -->"


class FakeBackend:
    """A minimal backend: a controllable pending queue + a record of what got sent."""
    def __init__(self, pending):
        self._pending = list(pending)
        self.sent = []

    def pending_queued(self, sid):
        return list(self._pending)

    def send(self, sid, text):
        self.sent.append(text)
        return True


class ApiRetryRendersAsRomp(unittest.TestCase):
    def test_the_apiretry_handler_tags_retry_with_the_romp_injected_marker(self):
        # the retry is ALWAYS marked romp-injected — BOTH backends (the user 2026-06-30): an auto-retry is
        # romp's action, not the human's. The old tmux-only marking left the SDK retry authored 'human'
        # (blue bubble) and let the planner mint a junk goal per bare "retry". See
        # tests/test_kernel_retry_authorship.py for the authorship end-to-end.
        ap = SRC.split('t == "apiRetry"', 1)[1].split("elif t ==", 1)[0]
        self.assertIn("_fire_api_retry(sid, be, manual=", ap,
                      "the route delegates to the ONE shared retry decision")
        fn = SRC.split("def _fire_api_retry(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("be.send(sid, RETRY_MSG)", fn, "the shared decision sends the shared RETRY_MSG constant")
        # the constant itself carries the romp-injected marker → never a bare retry on either backend
        self.assertEqual(km.RETRY_MSG, RETRY, "RETRY_MSG is the marked retry text")
        self.assertIn("romp-injected", km.RETRY_MSG)

    def test_manual_retry_bypasses_the_auto_retry_pause_suppression_gate(self):
        # the gate (global pause / interrupted-thread suppression) stops the AUTO-retry loop only; a MANUAL
        # "Retry now" click (msg.manual) is an explicit one-shot override that ALWAYS fires, so the button is
        # never a dead no-op on a suppressed/paused thread (the user 2026-07-06, SDK backend)
        fn = SRC.split("def _fire_api_retry(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('if not manual and (_retry_paused_on() or _session_retry_suppressed(sid)):', fn,
                      "the auto-retry gate is skipped for a manual click")

    def test_that_injected_retry_is_authored_romp_not_human(self):
        # end-to-end: the exact text romp pastes → author 'romp' (the gray bubble), NOT 'human', even though
        # it arrives via a paste+Enter that Claude Code records as promptSource='typed'
        blocks = [{"type": "text", "text": RETRY}]
        self.assertEqual(em.author_of(blocks, "typed", {}), "romp",
                         "the romp-injected marker wins over promptSource=typed → renders as a romp bubble")
        # sanity: a bare 'retry' (the old behavior) would have been a human prompt
        self.assertEqual(em.author_of([{"type": "text", "text": "retry"}], "typed", {}), "human")


class ApiRetryIdempotency(unittest.TestCase):
    """Drive _drive({type:'apiRetry'}) with a stubbed backend + gate globals and observe what gets sent."""
    def setUp(self):
        self._saved_name_of = km._name_of
        self._saved = (km.Sessions, km._retry_paused_on, km._session_retry_suppressed,
                       km._api_error, km._path_of)
        km._retry_paused_on = lambda: False
        km._name_of = lambda sid: "web"   # these tests drive ops on a session this kernel HAS; _drive refuses one it doesn't (2026-07-29)
        km._session_retry_suppressed = lambda sid: False
        # the one-retry-per-error-episode gate (2026-07-20) reads the CURRENT error record; these tests
        # exercise the queued-idempotency layer, so give each its own live error episode (fresh uuid per
        # test via the counter) — episode semantics themselves are pinned by test_kernel_retry_episode.py
        self._ep = {"n": 0}
        def _aerr(path):
            self._ep["n"] += 1
            return {"text": "500", "status": 500, "category": "server_error",
                    "uuid": "ep-%d" % self._ep["n"], "tooLong": False, "spendLimit": False}
        km._api_error = _aerr
        km._path_of = lambda sid, now=None: "/TESTDIR/x.jsonl"
        km._auto_retried.clear()
        km._auto_retry_state.clear()   # …and the backoff ladder (2026-07-29): each case starts at rung one

    def tearDown(self):
        (km.Sessions, km._retry_paused_on, km._session_retry_suppressed,
         km._api_error, km._path_of) = self._saved
        km._name_of = self._saved_name_of
        km._auto_retried.clear()
        km._auto_retry_state.clear()

    def _drive_retry(self, be, **msg):
        km.Sessions = types.SimpleNamespace(backend_for=lambda sid: be)
        m = {"type": "apiRetry", "id": "s1"}
        m.update(msg)
        km._drive(m, {})

    def test_auto_retry_skips_when_an_identical_retry_is_already_queued(self):
        # the pileup bug: the session is blocked, the previous romp retry is still in the queue unconsumed →
        # the next 10s auto-tick must NOT enqueue another (else "retry retry retry retry…").
        be = FakeBackend(pending=[RETRY])
        self._drive_retry(be)
        self.assertEqual(be.sent, [], "no second retry stacked on top of the pending one")

    def test_auto_retry_fires_once_when_the_queue_is_empty(self):
        be = FakeBackend(pending=[])
        self._drive_retry(be)
        self.assertEqual(be.sent, [RETRY], "an empty queue → exactly one retry is sent")

    def test_a_users_own_queued_message_does_not_block_the_auto_retry(self):
        # only an identical romp RETRY_MSG dedups; a real user turn sitting in the queue must not suppress the
        # recovery retry (exact-match, so no false positive on "retry the build" etc.)
        be = FakeBackend(pending=["retry the build please"])
        self._drive_retry(be)
        self.assertEqual(be.sent, [RETRY], "a non-identical queued message doesn't count as a pending retry")

    def test_manual_retry_always_fires_even_with_one_already_queued(self):
        # "Retry now" is an explicit user override — it fires even if a retry is already pending (and resets
        # the client countdown). The dedup is for the silent 10s AUTO-loop only.
        be = FakeBackend(pending=[RETRY])
        self._drive_retry(be, manual=True)
        self.assertEqual(be.sent, [RETRY], "a manual retry is not deduped by the pending-queue guard")


class RefusingBackend(FakeBackend):
    """A backend whose send is refused: SdkBackend.send returns False for a session with no live registry
    row (a dead client the retry cannot reach), CodexBackend.send likewise."""
    def send(self, sid, text):
        self.sent.append(text)
        return False


class ManualRetryRefusal(unittest.TestCase):
    """A MANUAL Retry the backend cannot deliver answers the pane that asked (review find, 2026-09-08). The
    feed's Retry latches "Retrying…" on the click and re-arms on the kernel's reply for that request; a send
    the backend refused used to answer nothing at all, so the button stayed latched until the card happened
    to be re-sent. The auto path stays silent: no pane asked, and the kernel's own tick asks again."""
    def setUp(self):
        self._saved = (km.Sessions, km._name_of, km._retry_paused_on, km._session_retry_suppressed,
                       km._api_error, km._path_of)
        km._name_of = lambda sid: "web"          # a session this kernel HAS; _drive refuses a foreign one earlier
        km._retry_paused_on = lambda: False
        km._session_retry_suppressed = lambda sid: False
        km._api_error = lambda path: {"text": "500", "status": 500, "category": "server_error", "uuid": "ep-r",
                                      "tooLong": False, "spendLimit": False}
        km._path_of = lambda sid, now=None: "/TESTDIR/x.jsonl"
        km._auto_retried.clear()
        km._auto_retry_state.clear()
        self.sent = []
        self.client = {"app": "feed", "wid": "w1", "alive": True, "send": lambda raw: self.sent.append(json.loads(raw))}

    def tearDown(self):
        (km.Sessions, km._name_of, km._retry_paused_on, km._session_retry_suppressed,
         km._api_error, km._path_of) = self._saved
        km._auto_retried.clear()
        km._auto_retry_state.clear()

    def _drive(self, be, **msg):
        km.Sessions = types.SimpleNamespace(backend_for=lambda sid: be)
        m = {"type": "apiRetry", "id": "s1"}
        m.update(msg)
        km._drive(m, self.client)

    def test_a_refused_manual_retry_answers_the_asker_by_session(self):
        be = RefusingBackend(pending=[])
        self._drive(be, manual=True)
        self.assertEqual(be.sent, [RETRY], "the manual retry was attempted")
        self.assertEqual([(m["type"], m["sid"]) for m in self.sent], [("retryRefused", "s1")],
                         "the refusal names the session whose Retry was clicked, so that latch alone re-arms")
        self.assertTrue(self.sent[0]["text"], "…and says why")

    def test_a_delivered_manual_retry_answers_nothing(self):
        # the retry is in the session's queue: the card's next frame (the turn opening, or a new error record)
        # is the reply, as before
        self._drive(FakeBackend(pending=[]), manual=True)
        self.assertEqual(self.sent, [])

    def test_a_refused_auto_retry_stays_silent(self):
        # nobody clicked: the dashboard tick and the kernel's own driver ask again on their next cycle
        self._drive(RefusingBackend(pending=[]))
        self.assertEqual(self.sent, [])


class KernelAutoRetryTick(unittest.TestCase):
    """_auto_retry_tick (the user 2026-08-11): the KERNEL drives the transient-api-error retry itself.
    Before it, the only clock was apiRetryTick in each open dashboard — a session that died on a transient
    error with no client open never retried at all (the ui session sat 7.5h on an overnight ENOTFOUND,
    invisible AND gating its awaiting wake, since the nudge walk skips api-errored sessions). Same
    decision, same gates: the tick calls the shared _fire_api_retry."""

    SID = "11111111-2222-3333-4444-888888888888"

    def setUp(self):
        self._saved = (km.Sessions, km._retry_paused_on, km._session_retry_suppressed,
                       km._api_error, km._path_of, km._alive_sessions)
        km._retry_paused_on = lambda: False
        km._session_retry_suppressed = lambda sid: False
        km._alive_sessions = lambda now, tmux: [{"sid": self.SID, "path": "/TESTDIR/x.jsonl"}]
        km._path_of = lambda sid, now=None: "/TESTDIR/x.jsonl"
        self.aerr = {"text": "Unable to connect to API (ENOTFOUND)", "status": None,
                     "category": "network", "uuid": "ep-1",
                     "tooLong": False, "spendLimit": False, "modelLimit": False, "authErr": False}
        km._api_error = lambda path: self.aerr
        self.be = FakeBackend(pending=[])
        km.Sessions = types.SimpleNamespace(backend_for=lambda sid: self.be)
        km._auto_retried.clear()
        km._auto_retry_state.clear()

    def tearDown(self):
        (km.Sessions, km._retry_paused_on, km._session_retry_suppressed,
         km._api_error, km._path_of, km._alive_sessions) = self._saved
        km._auto_retried.clear()
        km._auto_retry_state.clear()

    def _tick(self, tmux=None):
        km._auto_retry_tick(1_000_000, {self.SID: {"state": ""}} if tmux is None else tmux)

    def test_fires_unattended_with_no_client(self):
        # THE live wedge: a transient-errored idle session, zero clients. The kernel now asks for itself.
        self._tick()
        self.assertEqual(self.be.sent, [RETRY], "the kernel retries a transient error with no client open")
        self.assertEqual(km._auto_retried.get(self.SID), "ep-1", "the episode is stamped")
        self.assertGreater(km._retry_gate_state(self.SID)[1], 0, "the backoff ladder steps")

    def test_once_per_error_episode(self):
        self._tick(); self._tick()
        self.assertEqual(self.be.sent, [RETRY], "the same error record never collects a second retry")

    def test_a_new_episode_retries_after_the_backoff(self):
        self._tick()
        self.aerr = dict(self.aerr, uuid="ep-2")     # the attempt ran and failed again → a new error record
        km._auto_retry_state[self.SID]["next"] = 0   # …and its backoff rung has come due
        self._tick()
        self.assertEqual(self.be.sent, [RETRY, RETRY], "a fresh episode past its rung retries again")

    def test_on_you_classes_are_never_auto_retried(self):
        # a retry cannot compact a prompt, raise a cap, refill an allowance, or mend a credential — the
        # kernel driver must carry the skip the client tick used to provide (server-side twin)
        for k in ("tooLong", "spendLimit", "modelLimit", "authErr"):
            with self.subTest(cls=k):
                self.be.sent.clear()
                km._auto_retried.clear(); km._auto_retry_state.clear()
                self.aerr = dict(self.aerr, **{k: True})
                self._tick()
                self.assertEqual(self.be.sent, [], "%s is on-you; auto-retry stands down" % k)
                self.aerr = dict(self.aerr, **{k: False})
        # …while a MANUAL Retry-now through the shared decision still fires (explicit user override)
        self.aerr = dict(self.aerr, tooLong=True)
        km._fire_api_retry(self.SID, self.be, manual=True)
        self.assertEqual(self.be.sent, [RETRY])

    def test_dormant_sessions_are_skipped(self):
        self._tick(tmux={})                          # SID not in the live set → no live CLI
        self.assertEqual(self.be.sent, [], "a dead CLI's api-error is settled history, not retried into")

    def test_global_pause_stands(self):
        km._retry_paused_on = lambda: True
        self._tick()
        self.assertEqual(self.be.sent, [], "the tick rides the same global pause as every auto path")

    def test_recovered_session_is_left_alone(self):
        km._api_error = lambda path: None
        self._tick()
        self.assertEqual(self.be.sent, [], "no api error → nothing to retry")

    def test_the_pusher_cycle_runs_the_tick(self):
        self.assertIn("_auto_retry_tick(now, tmux)", SRC,
                      "the pusher cycle drives retries server-side — unattended recovery")


if __name__ == "__main__":
    unittest.main()
