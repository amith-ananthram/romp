#!/usr/bin/env python3
"""Interrupt escalation, terminal parity (the user 2026-07-10): in a terminal ctrl+c is a SIGNAL the
process cannot silently drop, but the SDK interrupt was a control-channel REQUEST — a wedged CLI ignored
it ('no current client' swallowed by a bare except), a missing client made the press a silent no-op, and
the stop button had no path to the kill the design itself named as the recovery (nimbus sat unresponsive
until a manual restart). Now each press climbs interrupt_action's ladder — control request, SIGINT the
CLI, SIGKILL (crash-heal respawns) — every rung logs, a failed control request auto-escalates when a turn
is provably in flight, and the episode resets when a turn settles or a fresh one starts. find_session_cli
is the signal's targeting: it can only ever match our OWN child resuming our sid. Synthetic fixtures."""
import os
import unittest
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_intr", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
FORK = "99999999-8888-7777-6666-555555555555"
KERNEL = 4242
MARK = "--input-format stream-json"


def ps_line(pid, ppid, sid=SID, mark=True):
    cmd = "/usr/local/bin/claude --output-format stream-json --resume %s %s" % (sid, MARK if mark else "")
    return "%5d %5d %s" % (pid, ppid, cmd)


class InterruptLadder(unittest.TestCase):
    """interrupt_action: what a stop press does, given the episode's rung and the channel's health."""

    def test_first_press_with_a_live_channel_is_the_polite_request(self):
        self.assertEqual(sb.interrupt_action(0, True), ("control", 1))

    def test_first_press_with_no_channel_goes_straight_to_sigint(self):
        self.assertEqual(sb.interrupt_action(0, False), ("sigint", 2),
                         "no client/loop means the request would be the old silent no-op")

    def test_second_press_escalates_even_with_a_live_channel(self):
        self.assertEqual(sb.interrupt_action(1, True), ("sigint", 2),
                         "the first request produced no settle event — don't just ask again")

    def test_third_press_and_beyond_kill(self):
        self.assertEqual(sb.interrupt_action(2, True), ("sigkill", 3))
        self.assertEqual(sb.interrupt_action(3, False), ("sigkill", 3))


class FindSessionCli(unittest.TestCase):
    """The escalation may only signal our own child resuming our sid."""

    def test_matches_our_child_resuming_our_sid(self):
        lines = [ps_line(100, KERNEL)]
        self.assertEqual(sb.find_session_cli(lines, [SID], KERNEL), 100)

    def test_matches_the_lastsid_fork_twin(self):
        lines = [ps_line(101, KERNEL, sid=FORK)]
        self.assertEqual(sb.find_session_cli(lines, [SID, FORK], KERNEL), 101)

    def test_rejects_orphans_other_parents_other_sids_and_tmux(self):
        lines = [ps_line(200, 1),                             # orphan — the reaper's territory
                 ps_line(201, 7777),                          # another kernel's child
                 ps_line(202, KERNEL, sid=FORK),              # our child, someone else's sid
                 ps_line(203, KERNEL, mark=False)]            # tmux-style CLI: no stream-json mark
        self.assertIsNone(sb.find_session_cli(lines, [SID], KERNEL))

    def test_junk_lines_are_skipped(self):
        self.assertIsNone(sb.find_session_cli(["", "not a ps line", "x y"], [SID], KERNEL))

    def test_equals_flag_spelling_matches(self):
        # The SDK's current argv spells it `--resume=<sid>`; the space-only match left the
        # escalation unable to find its own child to signal (2026-07-25).
        lines = ["  100 %d /x/claude --resume=%s --input-format stream-json" % (KERNEL, SID)]
        self.assertEqual(sb.find_session_cli(lines, [SID], KERNEL), 100)


class SourcePins(unittest.TestCase):
    """Wiring pins: the press routes through the ladder, failures are loud, episodes reset."""

    def setUp(self):
        self.src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()

    def test_interrupt_routes_through_the_ladder(self):
        self.assertIn("interrupt_action(self._intr_level, bool(self.loop and self.client))", self.src)

    def test_failed_control_request_is_logged_and_auto_escalates_inflight(self):
        self.assertIn("control request failed", self.src)
        self.assertIn('self._intr_level = max(self._intr_level, 2)', self.src)
        self.assertNotIn("await self.client.interrupt()\n        except Exception:\n            pass",
                         self.src, "the silent swallow must not return")

    def test_episode_resets_on_settle_fresh_turn_and_reconnect(self):
        self.assertGreaterEqual(self.src.count("self._intr_level = 0"), 4,
                                "init + fresh turn + reconnect settle + ResultMessage")

    def test_signal_path_is_loud_when_there_is_nothing_to_signal(self):
        self.assertIn("nothing to signal", self.src)

    def test_fresh_cli_spawn_stamps_and_heals(self):
        self.assertIn("spawnedAt=int(time.time())", self.src)
        self.assertIn("self.backend._heal_stale_awaiting(self.sid)", self.src)


if __name__ == "__main__":
    unittest.main()
