#!/usr/bin/env python3
"""One auto-retry per ERROR EPISODE (the user 2026-07-20, the retry-storm root cause). The apiRetry
route's queued-idempotency check goes blind the moment a retry FORWARDS into the CLI (pending_queued
empties while the session still reads blocked), and the 10s client tick runs per open view — so every
connected client stacked another "retry" into the CLI each cycle; a wedged session collected hundreds,
delivered as one flood when its turn opened. The gate is now event-based: the CURRENT error record's
uuid IS the episode — one auto-retry per episode, the next only after a NEW error record lands or the
error clears (a recovered session gets nothing). A manual Retry-now always fires and stamps the episode
so the auto loop never stacks behind it. SYNTHETIC fixtures only."""
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
km = load_source("romp_kernel_retryep", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class _FakeBackend:
    def __init__(self):
        self.sent = []

    def owns(self, sid):
        return True

    def send(self, sid, text):
        self.sent.append(text)
        return True

    def pending_queued(self, sid):
        return []


class RetryPerEpisode(unittest.TestCase):
    def setUp(self):
        self.be = _FakeBackend()
        self._saved_name_of = km._name_of
        self._saved = (km.Sessions.backend_for, km._api_error, km._path_of,
                       km._retry_paused_on, km._session_retry_suppressed, dict(km._auto_retried))
        km.Sessions.backend_for = lambda sid: self.be
        km._path_of = lambda sid, now=None: "/TESTDIR/x.jsonl"
        km._name_of = lambda sid: "web"   # these tests drive ops on a session this kernel HAS; _drive refuses one it doesn't (2026-07-29)
        km._retry_paused_on = lambda: False
        km._session_retry_suppressed = lambda sid: False
        self.aerr = {"text": "500 server_error", "status": 500, "category": "server_error",
                     "uuid": "err-1", "tooLong": False, "spendLimit": False}
        km._api_error = lambda path: self.aerr
        km._auto_retried.clear()

    def tearDown(self):
        (km.Sessions.backend_for, km._api_error, km._path_of,
         km._retry_paused_on, km._session_retry_suppressed, saved) = self._saved
        km._name_of = self._saved_name_of
        km._auto_retried.clear()
        km._auto_retried.update(saved)
        km._auto_retry_state.clear()

    def _elapse(self):
        """Let the backoff window pass (the ladder itself is covered by test_api_retry_backoff.py). These
        tests are about EPISODE identity, so they step over the wait rather than sleeping through it."""
        km._auto_retry_state.pop(SID, None)

    def _auto(self):
        return km._drive({"type": "apiRetry", "id": SID}, {"send": lambda s: None})

    def _manual(self):
        return km._drive({"type": "apiRetry", "id": SID, "manual": True}, {"send": lambda s: None})

    def test_one_auto_retry_per_error_episode(self):
        self._auto()
        self.assertEqual(len(self.be.sent), 1)
        for _ in range(5):                       # the 10s tick keeps firing (and from several views)…
            self._auto()
        self.assertEqual(len(self.be.sent), 1, "same error record → no second auto-retry, ever")

    def test_a_new_error_record_opens_a_new_episode(self):
        self._auto()
        self.aerr = dict(self.aerr, uuid="err-2")   # the attempt ran and failed again → new record
        self._auto()
        self.assertEqual(len(self.be.sent), 1, "the backoff holds the new episode until its rung is due")
        self._elapse()
        self._auto()
        self.assertEqual(len(self.be.sent), 2)

    def test_recovered_session_gets_no_auto_retry(self):
        self.aerr = None                             # stale client tick into a session that recovered
        self._auto()
        self.assertEqual(self.be.sent, [])

    def test_manual_always_fires_and_owns_the_episode(self):
        self._auto()
        self._manual()                               # user override fires even though the episode is stamped
        self.assertEqual(len(self.be.sent), 2, "…and even inside the backoff window (2026-07-29)")
        self._auto()                                 # …but the auto loop won't stack behind the manual one
        self.assertEqual(len(self.be.sent), 2)

    def test_the_queued_idempotency_guard_is_still_first(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('if any(q == RETRY_MSG for q in be.pending_queued(sid)):', src)


class ApiErrorCarriesEpisodeIdentity(unittest.TestCase):
    def test_api_error_returns_the_error_records_uuid(self):
        p = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"type": "assistant", "uuid": "err-uuid-7", "isApiErrorMessage": True,
                                "message": {"role": "assistant",
                                            "content": [{"type": "text", "text": "API Error 500"}]}}) + "\n")
        err = km._api_error(p)
        self.assertIsNotNone(err)
        self.assertEqual(err.get("uuid"), "err-uuid-7")


if __name__ == "__main__":
    unittest.main()
