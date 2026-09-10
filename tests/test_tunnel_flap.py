"""A tunnel that connects and then dies is still a failing tunnel (the user 2026-07-30).

A remote spent an afternoon cycling: connect, carry traffic for ~30 seconds, get torn down, reconnect —
roughly twice a minute, for hours. ssh worked every time (the dial log's own host-probe said so on every
death) and the far kernel answered in about 5ms. Two separate defects made that loop:

TEARDOWN TOOK ONE MISSED POLL. A single 4-second timeout condemned a link that had been carrying traffic
milliseconds earlier — one sample, no confirmation, and the verdict was to destroy the connection.

BACKOFF NEVER ENGAGED. It counted failed DIALS, and every dial here succeeded, so the row sat at fails=0
forever and the ladder built for "keep trying, waiting longer each time" was bypassed by the one failure
mode that needed it most.

Both are the repo's standing smell: a time-based heuristic standing in for an event. The fix counts
CONFIRMING EVENTS at both ends — a run of silent polls before a teardown, a run of answered polls before
the ladder clears.

Synthetic rows only; nothing dials anything.
"""
import inspect
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
km = load_source("romp_kernel_flap", os.path.join(BIN, "romp-kernel"))


class MissRun(unittest.TestCase):
    """One silent poll is a hiccup. A RUN of them is a dead path."""

    def test_a_single_miss_never_tears_down_a_working_link(self):
        r = {"host": "api"}
        self.assertFalse(km._note_poll(r, False), "one 4-second timeout is not evidence of a dead tunnel")

    def test_a_run_of_misses_does(self):
        r = {"host": "api"}
        verdicts = [km._note_poll(r, False) for _ in range(km.STALE_MISSES)]
        self.assertEqual(verdicts[:-1], [False] * (km.STALE_MISSES - 1))
        self.assertTrue(verdicts[-1], "consecutive silence IS the event that condemns the link")

    def test_any_answer_at_all_resets_the_run(self):
        r = {"host": "api"}
        for _ in range(km.STALE_MISSES - 1):
            km._note_poll(r, False)
        km._note_poll(r, True)                       # it spoke → the run so far meant nothing
        self.assertFalse(km._note_poll(r, False), "a hiccup every other poll must never accumulate")

    def test_a_genuinely_dead_path_still_dies_quickly(self):
        # the point of a RUN, not a grace period: this costs a few poll cycles, not a wall-clock window
        self.assertLessEqual(km.STALE_MISSES, 5)


class LadderClearing(unittest.TestCase):
    """Connecting is not succeeding. Answering, repeatedly, is."""

    def test_merely_connecting_does_not_count_as_established(self):
        r = {"host": "api"}
        km._note_poll(r, True)
        self.assertFalse(km._tunnel_established(r),
                         "one answered poll is what the flapping tunnel managed every single cycle")

    def test_a_run_of_answered_polls_does(self):
        r = {"host": "api"}
        for _ in range(km.STABLE_POLLS):
            km._note_poll(r, True)
        self.assertTrue(km._tunnel_established(r))

    def test_a_teardown_advances_the_ladder_and_schedules_on_it(self):
        r = {"host": "api"}
        n1 = km._note_tunnel_teardown(r, 1000.0)
        self.assertEqual(n1, 1)
        self.assertEqual(r["next_try"], 1000.0 + km._tunnel_backoff(1))
        n2 = km._note_tunnel_teardown(r, 2000.0)
        self.assertEqual(n2, 2, "a second connect-and-die waits longer, like any repeated failure")
        self.assertGreater(km._tunnel_backoff(n2), km._tunnel_backoff(n1))

    def test_a_teardown_clears_the_run_so_the_next_incarnation_earns_it_afresh(self):
        r = {"host": "api"}
        for _ in range(km.STABLE_POLLS):
            km._note_poll(r, True)
        km._note_tunnel_teardown(r, 1000.0)
        self.assertFalse(km._tunnel_established(r))
        self.assertEqual(r.get("misses"), 0)

    def test_the_flap_this_was_built_for_actually_backs_off_now(self):
        # replay the observed loop: up, a handful of answered polls, then silence, over and over. The old
        # code reset fails to 0 on every cycle and redialed at full speed forever.
        r = {"host": "api"}
        waits = []
        for cycle in range(5):
            for _ in range(4):                      # it answered for a while — but never STABLE_POLLS
                km._note_poll(r, True)
                if km._tunnel_established(r):
                    r["fails"] = 0
            dead = False
            while not dead:
                dead = km._note_poll(r, False)
            waits.append(km._tunnel_backoff(km._note_tunnel_teardown(r, 0.0)))
        self.assertEqual(waits, sorted(waits), "each repeat waits at least as long as the last")
        self.assertGreater(waits[-1], waits[0], "…and it genuinely climbs, rather than sitting at fails=0")

    def test_a_healthy_link_still_clears_its_ladder_promptly(self):
        # STABLE_POLLS at the supervisor's own cadence must be well under a minute, or a real recovery
        # would carry a stale backoff into its next outage
        self.assertLessEqual(km.STABLE_POLLS, 12)


class Wiring(unittest.TestCase):
    def test_the_supervisor_requires_the_run_before_it_kills_a_tunnel(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('silent = (st == "no-kernel" and r.get("_probe") == "timeout")', src)
        self.assertIn("if _note_poll(r, not silent):", src)
        self.assertIn("_note_tunnel_teardown(r, now)", src)

    def test_a_miss_short_of_the_run_leaves_the_row_where_it_was(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('elif silent:\n                            # a miss, but not yet a run of them', src)

    def test_up_clears_the_ladder_only_once_established(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("if _tunnel_established(r):", src)
        i = src.index("if _tunnel_established(r):")
        self.assertIn('r["fails"], r["next_try"] = 0, 0', src[i:i + 400])

    def test_a_checked_in_peer_keeps_the_same_run_bookkeeping(self):
        # it owns no ssh here (nothing to tear down), but without the run it would never read as
        # established and its ladder would stop clearing
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('_note_poll(r, st == "up")', src)

    def test_the_counters_describe_this_connection_and_never_reach_disk(self):
        self.assertIn("misses", km._NOT_SAVED)
        self.assertIn("ok_polls", km._NOT_SAVED)

    def test_the_row_says_what_is_actually_wrong(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn("keeps dropping after it connects", src,
                      "'stopped carrying traffic' described one poll; this describes the pattern")


if __name__ == "__main__":
    unittest.main()
