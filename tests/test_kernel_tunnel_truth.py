#!/usr/bin/env python3
"""Tunnel status tells the end-to-end truth (the user 2026-07-10): the old check was the LOCAL -L
listener accepting — which ssh always does, even when the far side resets every connection — so a
remote host with no kernel running read 'up' (TESTHOST), every action routed to it silently vanished,
and the /sessions + /version polls failing left no mark. _tunnel_status derives from the poll that
actually crossed the tunnel; an alive tunnel with nobody answering is 'no-kernel', surfaced in the
popover with the next step. The /send remote forward reports a dead far kernel instead of ok:true.
Synthetic fixtures only."""
import json
import os
import unittest
from unittest import mock
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_tunnel", os.path.join(BIN, "romp-kernel"))


class TunnelStatus(unittest.TestCase):
    """_tunnel_status(proc_alive, port_up, remote_answered) — the full matrix."""

    def test_answering_remote_is_up(self):
        self.assertEqual(km._tunnel_status(True, True, True), "up")

    def test_alive_tunnel_with_a_dead_far_kernel_is_no_kernel(self):
        self.assertEqual(km._tunnel_status(True, True, False), "no-kernel",
                         "the local listener accepting is NOT health — the far kernel never answered")

    def test_local_port_not_up_yet_is_starting(self):
        self.assertEqual(km._tunnel_status(True, False, False), "starting")

    def test_dead_tunnel_proc_is_down(self):
        self.assertEqual(km._tunnel_status(False, True, True), "down")
        self.assertEqual(km._tunnel_status(False, False, False), "down")


class RecoveryCounter(unittest.TestCase):
    """T291b (the user 2026-09-09): a row's recovery counter bumps ONLY when a pass that ANSWERED finds the row
    had missed polls (or was not up), never on a steady answered poll and never on a silent pass — the event a
    dashboard turns into hostUp for the previews parked on a link whose status never left "up". A miss a data
    path preloaded mints one bump until a real silent poll or a status change re-arms it (the review's find: a
    request that keeps stalling must not mint its own recovery, retry, stall and mint again forever)."""

    def _pass(self, r, answered, st="up"):
        """One supervisor pass's bookkeeping in the ssh branch's order: the poll, then the recovery."""
        km._note_poll(r, answered)
        return km._note_recovery(r, st)

    def test_a_steady_up_row_never_bumps(self):
        r = {"host": "TESTHOST", "status": "up", "misses": 0}
        for _ in range(5):
            self.assertFalse(self._pass(r, answered=True))
        self.assertEqual(int(r.get("upSeq") or 0), 0)

    def test_a_real_miss_then_an_answer_bumps_once(self):
        r = {"host": "TESTHOST", "status": "up", "misses": 0}
        self.assertFalse(self._pass(r, answered=False), "a silent pass is not a recovery")   # misses 1, the row keeps "up"
        self.assertFalse(self._pass(r, answered=False), "a SECOND silent pass under the stale bound is not one either")
        self.assertNotIn("upSeq", r)
        self.assertTrue(self._pass(r, answered=True), "the poll answered again: the recovery")
        self.assertEqual(r["upSeq"], 1)
        self.assertFalse(self._pass(r, answered=True), "the following steady pass does not bump again")
        self.assertEqual(r["upSeq"], 1)

    def test_a_demand_preload_bumps_once_until_a_real_miss_or_a_status_change_rearms_it(self):
        # the relay's timed-out request preloads misses (_demand_redial "timeout"); the woken pass answers
        r = {"host": "TESTHOST", "status": "up", "misses": 0}
        r["misses"], r["_demand_miss"] = km.STALE_MISSES - 1, True
        self.assertTrue(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 1)
        # the same request stalls again on a link whose polls never missed: no second bump, no retry loop
        r["misses"], r["_demand_miss"] = km.STALE_MISSES - 1, True
        self.assertFalse(self._pass(r, answered=True), "a demand preload mints one bump until the link shows a real miss")
        self.assertEqual(r["upSeq"], 1)
        # a REAL silent poll re-arms it
        self.assertFalse(self._pass(r, answered=False))
        self.assertTrue(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 2)
        r["misses"], r["_demand_miss"] = km.STALE_MISSES - 1, True
        self.assertTrue(self._pass(r, answered=True), "re-armed: the next demand preload bumps again")
        self.assertEqual(r["upSeq"], 3)
        # …and so does a status change
        r["misses"], r["_demand_miss"] = km.STALE_MISSES - 1, True
        self.assertFalse(self._pass(r, answered=True))
        km._note_recovery(r, "down")                           # the pass saw the link down
        r["status"] = "down"
        self.assertTrue(self._pass(r, answered=True), "back up: a recovery")
        self.assertEqual(r["upSeq"], 4)

    def test_a_not_up_row_coming_up_bumps_even_with_no_misses(self):
        r = {"host": "TESTHOST", "status": "down", "misses": 0}   # a torn-down, redialed row: misses reset by the teardown
        self.assertTrue(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 1)

    def test_a_start_hold_bumps_once_when_it_clears(self):
        """_start_remote holds the row at "starting" (`booting`) while it updates and boots the far kernel, and the
        pass skips its status write under the hold, so the row never reads "up" however many polls answer
        meanwhile. Without a guard every answered pass under the hold looks like a not-up row coming up and bumps
        again (1, 2, then 3 as the hold clears); the contract is one bump per recovery, when the hold clears."""
        r = {"host": "TESTHOST", "status": "starting", "misses": 0, "booting": True}
        self.assertFalse(self._pass(r, answered=True), "an answered pass under the hold is not the recovery")
        self.assertFalse(self._pass(r, answered=True), "nor the next one: the hold owns the row's phase")
        self.assertEqual(int(r.get("upSeq") or 0), 0, "no bump while the Start is in flight")
        r["booting"] = False                    # the boot landed: the hold clears; the row still reads "starting"
        self.assertTrue(self._pass(r, answered=True), "the first answered pass after the hold: the one recovery")
        self.assertEqual(r["upSeq"], 1)
        r["status"] = "up"                      # that pass's status write, no longer held
        self.assertFalse(self._pass(r, answered=True), "steady afterwards")
        self.assertEqual(r["upSeq"], 1)

    def test_a_silent_poll_under_a_start_hold_mints_no_second_bump(self):
        # the far kernel restarts under the hold (the update leg), so a poll or two go silent and leave the
        # _poll_miss mark; the hold spends no mark, and the pass after the hold spends it in the SAME bump the
        # not-up row coming up mints
        r = {"host": "TESTHOST", "status": "starting", "misses": 0, "booting": True}
        self.assertFalse(self._pass(r, answered=True))
        self.assertFalse(self._pass(r, answered=False, st="starting"), "a silent pass under the hold keeps the held status")
        self.assertFalse(self._pass(r, answered=True), "answered again, still under the hold")
        self.assertEqual(int(r.get("upSeq") or 0), 0)
        self.assertTrue(r.get("_poll_miss"), "the mark waits for the pass after the hold")
        r["booting"] = False
        self.assertTrue(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 1, "one recovery for the whole Start, whatever the polls did under it")
        self.assertNotIn("_poll_miss", r, "spent in that bump")
        r["status"] = "up"
        self.assertFalse(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 1)

    def test_a_demand_mark_survives_the_hold_and_latches_in_the_one_bump(self):
        """The guard defers the counter's bookkeeping, it does not discard it. A request that stalled just before
        the Start left the demand mark (its woken pass found no kernel, which spends no mark, and Start was
        pressed); the passes under the hold leave it too, so the bump after the hold spends it and latches the
        demand path the way the first answered pass would have without the hold: the same request stalling again
        on the healthy link mints no second bump, until a real silent poll re-arms it."""
        r = {"host": "TESTHOST", "status": "starting", "misses": 0, "booting": True, "_demand_miss": True}
        self.assertFalse(self._pass(r, answered=True))
        self.assertFalse(self._pass(r, answered=True))
        self.assertTrue(r.get("_demand_miss"), "the mark waits for the pass after the hold")
        r["booting"] = False
        self.assertTrue(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 1)
        r["status"] = "up"
        r["misses"], r["_demand_miss"] = km.STALE_MISSES - 1, True       # the same request stalls again
        self.assertFalse(self._pass(r, answered=True), "the demand path latched in the recovery bump")
        self.assertEqual(r["upSeq"], 1)
        self.assertFalse(self._pass(r, answered=False))                    # a real silent poll re-arms it
        self.assertTrue(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 2)

    def test_a_not_up_probe_under_the_hold_still_rearms_the_demand_path(self):
        """The guard sits after the not-answered branch: a probe the far kernel refuses under the hold (the update
        leg restarts it) re-arms the demand path the way it does off the hold, so a Start asked of a row whose
        demand path had latched does not carry the latch across the restart. Green before the guard existed; it
        pins the path the guard leaves alone."""
        r = {"host": "TESTHOST", "status": "starting", "misses": 0, "booting": True, "_demand_bumped": True}
        self.assertFalse(self._pass(r, answered=True, st="no-kernel"), "a refusal is an answered probe that is not up")
        self.assertNotIn("_demand_bumped", r, "re-armed under the hold, as off it")
        r["booting"] = False
        self.assertTrue(self._pass(r, answered=True))
        self.assertEqual(r["upSeq"], 1)
        r["status"] = "up"
        r["misses"], r["_demand_miss"] = km.STALE_MISSES - 1, True
        self.assertTrue(self._pass(r, answered=True), "re-armed: the next demand preload bumps")
        self.assertEqual(r["upSeq"], 2)

    def test_a_row_that_is_not_up_never_bumps(self):
        r = {"host": "TESTHOST", "status": "up", "misses": 0}
        km._note_poll(r, False)
        for st in ("no-kernel", "down", "starting", "restarting"):
            self.assertFalse(km._note_recovery(r, st), st)
        self.assertNotIn("upSeq", r)

    def test_a_checked_in_peers_timeout_preloads_the_miss_too(self):
        saved = dict(km._remotes)
        try:
            km._remotes.clear()
            km._remotes["TESTHOST"] = {"host": "TESTHOST", "checkin_peer": True, "proc": None, "status": "up", "misses": 0}
            km._tunnel_wake.clear()
            km._demand_redial("TESTHOST", "timeout")
            r = km._remotes["TESTHOST"]
            self.assertEqual(r["misses"], km.STALE_MISSES - 1, "the same evidence an ssh row's timeout files")
            self.assertTrue(r.get("_demand_miss"))
            self.assertTrue(km._tunnel_wake.is_set(), "the supervisor is woken to decide now")
            # the checkin branch's bookkeeping: an answered poll, then the recovery
            km._note_poll(r, True)
            self.assertTrue(km._note_recovery(r, "up"))
            self.assertEqual(r["upSeq"], 1)
        finally:
            km._remotes.clear(); km._remotes.update(saved); km._tunnel_wake.clear()

    def test_the_tunnels_row_serves_it_and_the_store_never_saves_it(self):
        for k in ("upSeq", "_poll_miss", "_demand_miss", "_demand_bumped"):
            self.assertIn(k, km._NOT_SAVED, k + ": a per-process mark, like the poll run counters")
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        self.assertIn('"upSeq": int(r.get("upSeq") or 0)}', src, "_remote_public serves the counter beside lastOk")
        # the supervisor pass notes the recovery once st is final, against the PREVIOUS status
        call = "_note_recovery(r, st)   # an answered pass after misses"   # the pass's call site, not the def
        self.assertIn(call, src)
        self.assertLess(src.index(call), src.index('r["status"] = st                   # keep a richer spawn-error label'),
                        "noted before the pass overwrites the status")
        self.assertIn('r["_poll_miss"] = True', src, "a real silent poll leaves its mark")
        self.assertEqual(src.count('r["_demand_miss"] = True'), 2, "both the ssh row's and the checked-in peer's timeout preload")


class ExpectedRestart(unittest.TestCase):
    """T238: a restart the DIALING side caused (its p2p update) must read as 'restarting after update',
    never as a dead far kernel — no red no-kernel, no terminated ssh, no backoff step — until the event
    that ends it (the far kernel answers again, or reports the new sha) or a can-never-trap cap."""

    def _row(self, **kw):
        r = {"host": "TESTHOST", "status": "up", "fails": 2, "next_try": 50.0, "misses": 0,
             "restartExpected": {"sha": "1" * 40, "t": 1000.0}}
        r.update(kw)
        return r

    def test_an_unanswered_poll_during_an_expected_restart_reads_restarting(self):
        r = self._row()
        st = km._expected_restart_status(r, "no-kernel", None, now=1005.0)
        self.assertEqual(st, "restarting")
        self.assertEqual(r["misses"], 0, "a restart gap is not a miss — nothing tears the ssh down")
        self.assertEqual((r["fails"], r["next_try"]), (2, 50.0), "no backoff step")
        self.assertIn("gapT", r["restartExpected"], "the gap's start is recorded — the cap counts from here")

    def test_the_far_kernel_answering_again_ends_it(self):
        r = self._row()
        km._expected_restart_status(r, "no-kernel", None, now=1005.0)
        st = km._expected_restart_status(r, "up", "2" * 40, now=1008.0)
        self.assertEqual(st, "up")
        self.assertNotIn("restartExpected", r, "the answer after the gap is the event that ends it")

    def test_the_new_sha_showing_up_ends_it_even_without_a_gap(self):
        # a quiet-window restart may land minutes later; a poll that already reports the pushed sha
        # proves the restart happened whether or not we saw the gap
        r = self._row()
        st = km._expected_restart_status(r, "up", "1" * 40, now=1300.0)
        self.assertEqual(st, "up")
        self.assertNotIn("restartExpected", r)

    def test_the_cap_ends_it_loudly_and_the_ordinary_path_resumes(self):
        r = self._row()
        km._expected_restart_status(r, "no-kernel", None, now=1005.0)
        before = len(km.TUNNEL_LOG.read_text().splitlines()) if km.TUNNEL_LOG.exists() else 0
        st = km._expected_restart_status(r, "no-kernel", None, now=1005.0 + km.RESTART_EXPECT_GAP_S + 1)
        self.assertEqual(st, "no-kernel", "past the cap the row is honestly a dead far kernel")
        self.assertNotIn("restartExpected", r)
        appended = [json.loads(x) for x in km.TUNNEL_LOG.read_text().splitlines()[before:] if x.strip()]
        self.assertTrue(any(x.get("event") == "restart-expected-expired" for x in appended), appended)

    def test_a_restart_that_never_comes_expires_too(self):
        # the far kernel keeps answering the OLD sha long past any quiet window: stop expecting
        r = self._row()
        st = km._expected_restart_status(r, "up", "2" * 40, now=1000.0 + km.RESTART_EXPECT_MAX_S + 1)
        self.assertEqual(st, "up")
        self.assertNotIn("restartExpected", r)

    def test_an_explicit_restart_with_no_sha_ends_only_by_its_gap(self):
        r = self._row(restartExpected={"sha": "", "t": 1000.0})
        km._expected_restart_status(r, "up", "2" * 40, now=1002.0)
        self.assertIn("restartExpected", r, "the old kernel answers the same sha — that is not the end")
        km._expected_restart_status(r, "no-kernel", None, now=1004.0)
        self.assertEqual(km._expected_restart_status(r, "up", "2" * 40, now=1008.0), "up")
        self.assertNotIn("restartExpected", r)

    def test_a_never_coming_restart_is_said_on_the_row(self):
        r = self._row()
        km._expected_restart_status(r, "up", "2" * 40, now=1000.0 + km.RESTART_EXPECT_MAX_S + 1)
        self.assertIn("never restarted", r.get("detail") or "", "not just a dial-log line")

    def test_the_expectation_ages_out_while_the_tunnel_is_down_too(self):
        r = self._row()
        km._expected_restart_status(r, "down", None, now=1000.0 + km.RESTART_EXPECT_MAX_S + 1)
        self.assertNotIn("restartExpected", r, "never a permanent latch, whatever the row is doing")

    def test_a_refused_demand_redial_with_a_dead_ssh_is_a_real_demand(self):
        proc = mock.Mock()
        proc.poll = lambda: 1                    # the ssh itself died
        km._remotes["TESTHOST"] = self._row(proc=proc)
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        km._demand_redial("TESTHOST", "refused")
        self.assertEqual((km._remotes["TESTHOST"]["fails"], km._remotes["TESTHOST"]["next_try"]), (0, 0),
                         "a dead ssh during the expectation still earns the redial")

    def test_no_expectation_changes_nothing(self):
        r = self._row()
        r.pop("restartExpected")
        self.assertEqual(km._expected_restart_status(r, "no-kernel", None, now=1005.0), "no-kernel")

    def test_a_refused_demand_redial_is_a_no_op_while_the_restart_is_expected(self):
        terminated = []
        proc = mock.Mock()
        proc.poll = lambda: None
        proc.terminate = lambda: terminated.append(1)
        km._remotes["TESTHOST"] = self._row(proc=proc)
        self.addCleanup(km._remotes.pop, "TESTHOST", None)
        km._demand_redial("TESTHOST", "refused")
        self.assertEqual(terminated, [], "a data-path hit during our own restart must not kill a healthy ssh")
        self.assertEqual((km._remotes["TESTHOST"]["fails"], km._remotes["TESTHOST"]["next_try"]), (2, 50.0))

    def test_a_quiet_pending_audit_row_names_the_cut_beyond_the_usual_window(self):
        # the quiet window can defer the restart for minutes; the cut row must still carry the reason
        km._audit_restart_request("p2p-update", reason="from TESTHOST to 1111111", when="quiet",
                                  t=int(2000 - 600))
        self.assertIn("p2p-update", km._recent_restart_reason(now=2000))
        km._audit_restart_request("rail-button", t=int(3000 - 600))
        self.assertEqual(km._recent_restart_reason(now=3000), "", "an immediate request keeps the 90s window")


class SourcePins(unittest.TestCase):
    def setUp(self):
        self.src = open(os.path.join(BIN, "romp-kernel")).read()

    def test_supervisor_derives_status_from_the_poll(self):
        self.assertIn("_tunnel_status(_tunnel_proc_alive(r), up, sids is not None)", self.src)

    def test_no_kernel_carries_the_next_step_detail(self):
        self.assertIn("no kernel answering through the tunnel", self.src)

    def test_popover_labels_and_colors_the_new_status(self):
        self.assertIn("'no-kernel':'kernel not answering'", self.src)
        self.assertIn("t.status==='no-kernel'", self.src)

    def test_the_strip_labels_an_expected_restart_and_never_paints_it_red(self):
        self.assertTrue("restarting:'restarting after update" in self.src, "strip label for the expected restart")
        self.assertTrue("t.status==='no-kernel'||t.status==='error')return 'warn'" in self.src,
                        "the warn (red) line names no-kernel and error only — restarting is a wait")

    def test_the_supervisor_consults_the_expectation_before_it_judges_the_far_kernel(self):
        self.assertTrue("_expected_restart_status(r, st, rsha, now)" in self.src,
                        "the supervisor consults the expectation before judging the far kernel")

    def test_send_remote_forward_reports_a_dead_far_kernel(self):
        self.assertIn("isn't answering — message not delivered", self.src)


if __name__ == "__main__":
    unittest.main()
