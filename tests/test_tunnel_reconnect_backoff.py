#!/usr/bin/env python3
"""Reconnect KEEPS TRYING, on a widening backoff (the user 2026-07-29).

Between 2026-07-22 and now each host got a finite budget of dials and then stopped, waiting for an
explicit Attach. That was aimed at a real problem — a box re-dialing all day with NOTHING in the UI
saying so — but it solved it by abandoning the host, which is wrong for a machine that is merely asleep
or rebooting: an attached host is a standing instruction to hold the link.

So it is both halves now. Dial forever, back off far enough to be inaudible (and far under any sshd rate
limit), and say the state in words: the row names the failed attempts and when the next dial lands.

Synthetic only — hermetic temp STATE, placeholder hostnames/tokens, no real ssh.
"""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_rbackoff", os.path.join(BIN, "romp-kernel"))


class Backoff(unittest.TestCase):
    def test_the_first_retry_is_prompt_and_every_wait_is_positive(self):
        self.assertEqual(km._tunnel_backoff(0), km.TUNNEL_BACKOFF_BASE)
        for fails in range(0, 60):
            self.assertGreater(km._tunnel_backoff(fails), 0, "there is no 'stop' answer")

    def test_it_widens_and_never_narrows(self):
        waits = [km._tunnel_backoff(f) for f in range(0, 40)]
        self.assertEqual(waits, sorted(waits), "a longer outage never dials MORE often")
        self.assertEqual(waits[:5], [15, 30, 60, 120, 240], "doubling from 15s")

    def test_a_fresh_outage_settles_at_five_minutes(self):
        self.assertEqual(km._tunnel_backoff(6), km.TUNNEL_BACKOFF_MAX)
        self.assertEqual(km._tunnel_backoff(km.TUNNEL_LONG_AFTER - 1), km.TUNNEL_BACKOFF_MAX)

    def test_a_long_outage_relaxes_to_the_long_ceiling_forever(self):
        self.assertEqual(km._tunnel_backoff(km.TUNNEL_LONG_AFTER), km.TUNNEL_BACKOFF_LONG)
        self.assertEqual(km._tunnel_backoff(10 ** 6), km.TUNNEL_BACKOFF_LONG,
                         "a host off for a week is still dialed, just rarely")

    def test_even_the_fastest_cadence_is_gentle_on_the_remote_sshd(self):
        # the 2026-06-30 reason the backoff exists at all: repeated ssh can trip a rate limit
        self.assertGreaterEqual(km.TUNNEL_BACKOFF_BASE, 10)
        self.assertLessEqual(3600 / km.TUNNEL_BACKOFF_LONG, 6, "a settled outage dials a handful of times an hour")


class NeverGivesUp(unittest.TestCase):
    def test_the_supervisor_always_schedules_another_dial(self):
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('r["next_try"] = now + _tunnel_backoff(fails)', src)
        self.assertIn("_spawn_tunnel(r)", src)
        self.assertNotIn("gave_up", src.split("r.pop(")[0], "no give-up gate before the dial")
        self.assertNotIn('"gave-up"', src, "there is no such status any more")
        self.assertNotIn("TUNNEL_MAX_TRIES", src, "no budget to spend")

    def test_the_give_up_state_is_gone_from_the_kernel_entirely(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertNotIn("TUNNEL_MAX_TRIES", src)
        self.assertNotIn("'gave-up'", src)
        self.assertNotIn('"gaveUp"', src)

    def test_a_healthy_tunnel_resets_the_ladder(self):
        # so a later drop starts at 15s again instead of inheriting an hour-old backoff
        src = inspect.getsource(km._tunnel_supervisor)
        self.assertIn('r["fails"], r["next_try"] = 0, 0', src)


class ClearedOnPurpose(unittest.TestCase):
    def test_every_kernel_start_dials_immediately(self):
        # fails/next_try persist in remotes.json; boot must clear them or a restart would sit out a
        # backoff the previous process built up. A pre-2026-07-29 row's gave_up is dropped on the way in.
        state = km.jd.STATE
        state.mkdir(parents=True, exist_ok=True)
        km.REMOTES_FILE.write_text(json.dumps([{
            "host": "TESTHOST", "kernel_port": 29855, "local_port": 51000, "token": "tok",
            "fails": 99, "next_try": 9e12, "gave_up": True, "trust": "directed",
        }]))
        km._remotes.clear()
        km._remotes_load()
        r = km._remotes.get("TESTHOST")
        self.assertIsNotNone(r, "the row must still load")
        self.assertEqual(r["fails"], 0)
        self.assertEqual(r["next_try"], 0)
        self.assertFalse(r.get("gave_up"), "a row saved by an older kernel loses the dead flag")

    def test_an_explicit_attach_skips_the_wait(self):
        src = inspect.getsource(km.attach_remote)
        self.assertIn('r["fails"], r["next_try"] = 0, 0', src)


class SurfacedToTheUI(unittest.TestCase):
    def test_the_row_carries_the_attempts_and_the_next_dial(self):
        # the 2026-07-22 objection, kept: a forever-retry must never look like an idle row
        src = inspect.getsource(km._remote_public)
        self.assertIn('"fails": int(r.get("fails") or 0)', src)
        self.assertIn('"nextTry": int(r.get("next_try") or 0)', src)
        self.assertNotIn("gaveUp", src)
        self.assertNotIn("maxTries", src)


if __name__ == "__main__":
    unittest.main()
