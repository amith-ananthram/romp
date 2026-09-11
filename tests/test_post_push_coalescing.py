#!/usr/bin/env python3
"""The 2026-08-30 POST wedge: every well-formed POST hung (curl 000 at 60s) while ~114 handler
threads piled inside do_POST → _push_all → build_session — each control POST ran a synchronous
FULL-FLEET build on its request thread, arrivals outran service, and the pile starved the GIL.
The designed fix predates the wedge: _push_soon() (2026-07-05) wakes the dedicated pusher — the ONE
builder — which coalesces bursts; these tests pin that every request-side site now uses it, that
pokes coalesce, and that a control route answers while a build runs. Synthetic only."""
import http.client
import json
import os
import re
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_postpush", os.path.join(BIN, "romp-kernel"))

# The ONLY functions allowed to build inline: the pusher's own cycle. Everything request-side pokes
# instead, and so do the tick jobs that run ON the pusher thread (_pusher_cycle_jobs): their writers
# mark the views dirty and wake the pusher, whose next cycle carries the change (the interrupt tick's
# inline push rebuilt nothing the next cycle would not, and the retry-pause flag is read by no view).
# A tick job that builds inline is the regression this census catches.
PUSHER_THREAD_FNS = {"_push_all", "_pusher_cycle_jobs"}


class PushCallerCensus(unittest.TestCase):
    def test_no_request_side_inline_build_survives(self):
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read_text()
        enclosing, offenders = None, []
        for ln in src.splitlines():
            m = re.match(r"(?:    )?def (\w+)", ln)
            if m:
                enclosing = m.group(1)
            if re.match(r"\s*_push_all\(", ln) and "def _push_all" not in ln:
                if enclosing not in PUSHER_THREAD_FNS:
                    offenders.append((enclosing, ln.strip()))
        self.assertEqual(offenders, [],
                         "a request-side inline fleet build is the 2026-08-30 wedge — use _push_soon()")


class PokeCoalescing(unittest.TestCase):
    def test_n_pokes_latch_one_wakeup(self):
        km._pusher_wake.clear()
        for _ in range(50):
            km._push_soon()
        self.assertTrue(km._pusher_wake.is_set(), "the poke wakes the pusher immediately")
        km._pusher_wake.clear()                       # ONE cycle consumes the whole burst
        self.assertFalse(km._pusher_wake.is_set(), "50 pokes = 1 wakeup = 1 build — coalesced")


class MidCycleWakeStartsTheNextCycle(unittest.TestCase):
    """A tick job's writer sets the wake DURING the cycle (_mark_views_dirty, after that cycle's push): the loop
    must take it as the NEXT cycle's event, not clear it on the way out. The tick jobs' inline pushes were
    removed on the strength of this ordering (wait, then clear), which nothing pinned behaviourally."""

    class _Stop(Exception):
        pass

    def test_a_wake_set_during_the_cycle_is_the_next_cycles_event(self):
        kinds, n = [], [0]

        def cycle():
            n[0] += 1
            if n[0] == 1:
                km._pusher_wake.set()          # a tick job's writer, after this cycle's push
            else:
                raise self._Stop()             # the second cycle: end the while-True loop
        km._pusher_wake.clear()
        with mock.patch.object(km, "_pusher_cycle", cycle), \
                mock.patch.object(km._PERF_STATS, "wake_kind", side_effect=kinds.append), \
                self.assertRaises(self._Stop):
            km._pusher()
        self.assertEqual(kinds, [True], "the loop woke on the event, not on the 0.5 s backstop")
        self.assertFalse(km._pusher_wake.is_set(), "…and consumed it")


class ControlRouteLatency(unittest.TestCase):
    """A control POST answers while builds run: the handler thread never builds, so a flooded push
    queue cannot pile handlers (the wedge's exact shape, inverted)."""

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self._saved_push = km._push_all
        self.builds = []
        def slow_build(live_map=None):
            self.builds.append(time.time())
            time.sleep(2.0)
        km._push_all = slow_build

    def tearDown(self):
        km._push_all = self._saved_push
        self.srv.shutdown()

    def _post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, json.dumps(body),
                  {"Content-Type": "application/json", "X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"]})
        r = c.getresponse()
        out = (r.status, r.read())
        c.close()
        return out

    def test_end_answers_fast_while_builds_run(self):
        # simulate the pile: three threads stuck in the (patched, slow) fleet build
        for _ in range(3):
            threading.Thread(target=km._push_all, daemon=True).start()
        km._pusher_wake.clear()
        t0 = time.time()
        status, _ = self._post("/end", {"name": "nonexistent-session-probe"})
        took = time.time() - t0
        self.assertLess(took, 1.5, "the control route sat behind push work — the wedge shape")
        self.assertEqual(status, 200)
        self.assertTrue(km._pusher_wake.is_set(),
                        "…and the echo freshness rides the poke: the pusher wakes to build off-thread")


if __name__ == "__main__":
    unittest.main()
