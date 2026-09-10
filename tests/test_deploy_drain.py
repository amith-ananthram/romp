#!/usr/bin/env python3
"""The deploy-drain hold (T121 part 1, 2026-08-27): while a quiet deploy restart is PARKED at the
manager, the kernel holds NEW turn starts so /busy falls to 0 on its own turn-end events — before
this, kernels kept starting queued turns and a busy box only ever "drained" via the manager's
backstop cut. The hold is a LEASE (refreshed by the parked poll, self-expiring when the holder
dies) and RUNTIME-ONLY (a fresh boot starts clear by construction — the manager-approved
stale-flag guard); arming is visible once per episode and a long hold escalates to the problems
ring. Mid-turn forwards keep flowing so the in-flight turn can finish; queued prompts persist —
that IS the checkpoint. Hermetic state; synthetic sids only."""
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_drainhold", os.path.join(BIN, "romp_sdk_backend.py"))


def _backend(d=None):
    return sb.SdkBackend(d or tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)


class DrainLease(unittest.TestCase):
    def test_a_fresh_backend_never_holds(self):
        # boot-clear BY CONSTRUCTION: the lease is runtime-only, so the restart it served can never
        # leave a standing flag behind — the manager-approved stale-drain guard
        self.assertFalse(_backend().drain_holding())

    def test_refresh_arms_and_expiry_releases(self):
        be = _backend()
        be.DRAIN_HOLD_TTL = 0.15
        be.refresh_drain_hold()
        self.assertTrue(be.drain_holding(), "the parked poll's refresh arms the hold")
        time.sleep(0.25)
        self.assertFalse(be.drain_holding(),
                         "the lease dies by itself when the holder stops refreshing — no off-switch to forget")

    def test_expiry_wakes_held_inputs(self):
        be = _backend()
        be.DRAIN_HOLD_TTL = 0.1
        woken = []
        be._wake_all_inputs = lambda: woken.append(1)
        be.refresh_drain_hold()
        time.sleep(0.9)   # the wake timer fires at TTL + 0.5s
        self.assertTrue(woken, "the lease-end timer nudges every input generator — a held fresh "
                               "turn starts without waiting for another event")

    def test_a_lapsed_lease_re_armed_moments_later_is_the_same_episode(self):
        # T240: the manager drops the hold during background-only stretches and re-arms it when a
        # turn starts — a flap, not a new park: the episode clock (the 5-minute ring) and the
        # "parked" line must not restart per flap
        be = _backend()
        be.DRAIN_HOLD_TTL = 0.2
        be.refresh_drain_hold()
        since0 = be._drain_hold_since
        time.sleep(0.3)                                    # lapsed
        be.refresh_drain_hold()                            # re-armed within 2×TTL of the lapse
        self.assertEqual(be._drain_hold_since, since0, "same episode")
        time.sleep(0.7)                                    # lapsed for > 2×TTL: genuinely a new park
        be.refresh_drain_hold()
        self.assertGreater(be._drain_hold_since, since0, "a new episode starts its own clock")

    def test_the_episode_keys_on_the_managers_park_identity_not_a_window(self):
        # T240c: the manager drops the hold for MINUTES during background-only stretches, so a time
        # window mis-read the next in-flight re-arm as a new park; and a park held only by background
        # work never reached refresh_drain_hold, so it never rang. The park identity on every parked
        # poll fixes both: one "parked" line per park, the ring from a plain poll, a new park = new line.
        logs = []
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=logs.append)
        be.note_parked_poll("1700000000")                 # a plain parked poll (background-only park)
        self.assertEqual(sum("deploy restart parked" in str(l) for l in logs), 1)
        since0 = be._drain_hold_since
        be.DRAIN_HOLD_TTL = 0.2
        be.refresh_drain_hold(park="1700000000")          # a turn started: the hold arms — same park
        be._drain_hold_until = time.time() - 10 * be.DRAIN_HOLD_TTL   # …and the lease lapsed far longer ago than any window
        be.note_parked_poll("1700000000")
        be.refresh_drain_hold(park="1700000000")
        self.assertEqual(be._drain_hold_since, since0, "the same park is the same episode, whatever the gaps")
        self.assertEqual(sum("deploy restart parked" in str(l) for l in logs), 1, "one line per park")
        be._drain_hold_since = time.time() - be.DRAIN_LOUD_S - 1
        be.note_parked_poll("1700000000")                 # a PLAIN poll rings — background-only parks are not silent
        self.assertTrue(any("still parked" in str(l) for l in logs))
        be.note_parked_poll("1700000900")                 # a new park identity: a new episode, its own line
        self.assertEqual(sum("deploy restart parked" in str(l) for l in logs), 2)
        self.assertGreater(be._drain_hold_since, since0)

    def test_an_empty_or_stale_park_identity_is_ignored(self):
        # review finds: an empty identity on a fresh backend compared equal to the initial "" and
        # rang a bogus "still parked after 29 million minutes" problem; and a stale probe from a park
        # the manager had since replaced could flip the episode back and reset the newer park's clock
        logs = []
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=logs.append)
        be.note_parked_poll("")
        be.note_parked_poll(None)
        self.assertFalse(any("parked" in str(l) for l in logs), "nothing to key on: no episode, no ring")
        self.assertFalse(be._drain_hold_rang)
        self.assertFalse(any("still parked" in str(p) for p in be.problems()), "no fabricated ring")
        be.note_parked_poll("1700000900")
        since0 = be._drain_hold_since
        be.note_parked_poll("1700000000")                 # a probe issued for the park this one replaced
        self.assertEqual(be._drain_park, "1700000900", "a lower identity is a stale probe, not a new park")
        self.assertEqual(be._drain_hold_since, since0)
        self.assertEqual(sum("deploy restart parked" in str(l) for l in logs), 1)
        be.note_parked_poll("not-a-number")                # a non-numeric identity is simply a different park
        self.assertEqual(be._drain_park, "not-a-number")
        self.assertEqual(sum("deploy restart parked" in str(l) for l in logs), 2)

    def test_arming_is_visible_and_a_long_hold_rings(self):
        logs = []
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=logs.append)
        be.refresh_drain_hold()
        self.assertTrue(any("deploy restart parked" in str(l) for l in logs),
                        "arming says so once — a draining box never reads as a hung one")
        be._drain_hold_since = time.time() - be.DRAIN_LOUD_S - 1
        be.refresh_drain_hold()
        self.assertTrue(any("still parked" in str(l) for l in logs),
                        "a hold outliving the loud bound escalates rather than reading as idle sessions")

    def test_the_turn_start_gate_holds_fresh_starts_only(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        self.assertIn("blocked = blocked or (self.inflight == 0 and self.backend.drain_holding())", src,
                      "the inputs() gate holds NEW turn starts while draining; mid-turn forwards "
                      "(inflight > 0) keep flowing so the in-flight turn can finish")

    def test_the_kernel_route_and_the_deploy_paths_ride_the_gate(self):
        ksrc = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        # T224 split the gate into branches so a REFUSED drain can be counted and said loudly; the
        # arm still sits under the explicit-token check and nowhere else
        gate = 'if q.get("drain", [""])[0] == "1":\n                        if self._write_token_ok(q):\n'
        self.assertIn(gate, ksrc,
                      "/busy?drain=1 refreshes the lease in the same round-trip that reads the count — "
                      "but the arm is a WRITE, gated on an explicit token (the behavioral pins live "
                      "in tests/test_kernel_auth_hardening.py::BusyDrainWriteGate); the READ stays exempt")
        armed = ksrc[ksrc.index(gate):].split("_note_drain_armed()", 1)[0]
        self.assertIn("be.refresh_drain_hold(park=park)", armed,
                      "the arm carries the manager's park identity when the poll brought one (T240c)")
        self.assertIn("be.refresh_drain_hold()", armed,
                      "and keeps the old spelling for a poll without one: a backend that never learned "
                      "the keyword still arms")
        self.assertNotIn("_note_drain_refused()", armed, "both arms sit under the token check")
        self.assertIn('json.dumps({"busy": n, "inflight": inflight, "background": background,', ksrc,
                      "the payload says when the box is draining — glanceable, never mysterious")
        self.assertIn("'http://127.0.0.1:%d/restart-all'", ksrc,
                      "the self-update deploy cuts IMMEDIATELY (T160, reversing the T121 quiet "
                      "default: parked windows cost minutes per push) — the quiet gate stays for "
                      "explicit `romp refresh --quiet`")
        self.assertNotIn("/restart-all?when=quiet'", ksrc,
                         "no kernel-side deploy path defaults to the quiet window any more")
        msrc = open(os.path.join(BIN, "romp-manager")).read()
        self.assertIn("fetchBusy(KERNEL_PORT, cb, holdTurns ? '/busy?drain=1&' + pk : '/busy?' + pk)", msrc,
                      "the manager's PARKED poll is the lease's refresher")


class GoingDownHold(unittest.TestCase):
    """`romp down`'s quiesce rides the same lease: quiesce(ttl) extends the hold to cover the wait plus
    the stop that follows and flags the create doors closed (quiescing); a deploy poll landing inside it
    must never SHORTEN it; the cancel releases and wakes; and it stays a lease: no stop, and the kernel
    carries on by itself."""

    def test_a_fresh_backend_is_not_quiescing(self):
        self.assertFalse(_backend().quiescing())
        self.assertEqual(_backend().inflight_names(), [])

    def test_quiesce_arms_both_the_turn_hold_and_the_create_gate_for_the_ttl(self):
        be = _backend()
        be.quiesce(0.3)
        self.assertTrue(be.drain_holding(), "new turn starts hold: the same gate inputs() consults")
        self.assertTrue(be.quiescing(), "and the create doors read closed")
        time.sleep(0.45)
        self.assertFalse(be.quiescing(), "a lease, not a latch: with no stop the kernel carries on")
        self.assertFalse(be.drain_holding())

    def test_a_deploy_poll_inside_a_quiesce_never_shortens_the_hold(self):
        be = _backend()
        be.DRAIN_HOLD_TTL = 0.1
        be.quiesce(5)
        be.refresh_drain_hold()
        try:
            self.assertGreater(be._drain_hold_until, time.time() + 4,
                               "the 12s lease refresh extends a hold, it never cuts a longer one back")
        finally:
            be._drain_wake_timer.cancel()

    def test_a_deploy_poll_inside_a_quiesce_does_not_ring_a_stale_clock(self):
        # refresh_drain_hold's "still parked" escalation clocks from _drain_hold_since; a quiesce that
        # opened the episode must start that clock, or the first deploy poll inside it reads a 0.0
        # stamp as a hold minutes old and rings the problems ring for nothing
        logs = []
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=logs.append)
        be.quiesce(5)
        be.refresh_drain_hold()
        try:
            self.assertFalse(any("still parked" in str(l) for l in logs), logs)
        finally:
            be._drain_wake_timer.cancel()

    def test_cancel_releases_at_once_and_wakes_the_held_inputs(self):
        be = _backend()
        woken = []
        be._wake_all_inputs = lambda: woken.append(1)
        be.quiesce(30)
        be.cancel_quiesce()
        self.assertFalse(be.quiescing())
        self.assertFalse(be.drain_holding())
        self.assertTrue(woken, "held fresh turns start now, not at the lapsed lease's timer")

    def test_arming_and_canceling_are_visible(self):
        logs = []
        be = sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None, log=logs.append)
        be.quiesce(3)
        self.assertTrue(any("going down" in str(l) for l in logs), logs)
        be.cancel_quiesce()
        self.assertTrue(any("canceled" in str(l) for l in logs), logs)
        n = len(logs)
        be.cancel_quiesce()
        self.assertEqual(len(logs), n, "a cancel with nothing to cancel says nothing")


if __name__ == "__main__":
    unittest.main()
