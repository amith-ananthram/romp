#!/usr/bin/env python3
"""The 2026-08-25 awaiting audit's surviving classes, pinned (10 stamps live: 3 real, 4 stale, 3
misfiled — every stale/misfiled one traced to a writer whose evidence the world had outrun):

- KIND-LAUNDERING (C4): the peer write gate narrowed kind=peer, and the closer began filing the
  same peer/idle waits as job/timer/agents — the kinds exempt from every mail-driven retire. The
  prompt now draws hard kind boundaries; the agents kind gains the mechanical complement (a stamp
  over a world with nothing running anywhere lifts — pinned in test_kernel_awaiting_lift).
- ASK-RECENCY (C6): _open_ask_peers admitted a peer stamp off week-old open questions to another
  host — asks that predate the GOAL cannot be what its wait is on (the waitfor gate's own
  evidence-order rule, now applied at the write gate), and the stamp's awaitPeers then named the
  wrong peers, so the pair-scoped supersede missed the reply that came.
- DIARY STAND-DOWN (C3): the closer, auditing a pre-ending segment, re-asserted a wait whose lift
  AND whose goal's done were already in the diary — the re-assert now stands down when the diary
  ended the wait after the audited evidence (the standing writer-yields rule, applied to awaiting
  asserts).
All fixtures SYNTHETIC."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_audit", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
PEER = "66666666-7777-8888-9999-aaaaaaaaaaaa"
OLDPEER = "99999999-8888-7777-6666-bbbbbbbbbbbb"
T0 = 1_781_100_000


def _msg(i, f, t_, ts, kind):
    return json.dumps({"id": "m%d" % i, "ev": "sent", "from": "web", "from_id": f,
                       "to_id": t_, "t": ts, "kind": kind, "body": "x"})


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        self.td.cleanup()

    def _log(self, rows):
        jd.MESSAGES.write_text("\n".join(rows) + ("\n" if rows else ""))
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]

    def _store(self, mint=T0):
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", mint, [{"do": "mint", "why": "x", "text": "Ship the exporter"}], [])
        return s, SID + ":g1"

    def _close_peer(self, s, why="asked the worker to verify; holding for the answer"):
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": why, "kind": "peer"}}}, t=T0 + 500)


class AskRecency(_Base):
    """C6: an open ask that PREDATES the goal cannot admit its peer stamp."""

    def test_a_pre_goal_ask_does_not_admit(self):
        # the specimen: week-old open questions to another host, a fresh goal, a no-reply delegate —
        # the stamp was admitted off asks that could not possibly be what this wait is on
        self._log([_msg(1, SID, OLDPEER, T0 - 7 * 86400, "question"),
                   _msg(2, SID, PEER, T0 + 10, "delegate")])
        s, gid = self._store(mint=T0)
        self._close_peer(s)
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"),
                          "no ask at/after the goal's mint → the gate stands down")

    def test_an_ask_after_the_goal_admits_and_names_only_itself(self):
        self._log([_msg(1, SID, OLDPEER, T0 - 7 * 86400, "question"),
                   _msg(2, SID, PEER, T0 + 10, "question")])
        s, gid = self._store(mint=T0)
        self._close_peer(s)
        nd = s["nodes"][gid]
        self.assertEqual(nd.get("awaitingKind"), "peer")
        self.assertEqual(nd.get("awaitingPeers"), [PEER],
                         "awaitPeers carries ONLY the qualifying ask's peer — the pair supersede "
                         "then watches the right pair, not a week-old stranger")

    def test_the_gate_helper_scopes_by_since(self):
        self._log([_msg(1, SID, OLDPEER, T0 - 100, "question"),
                   _msg(2, SID, PEER, T0 + 100, "question")])
        self.assertEqual(jd._open_ask_peers(SID), sorted([OLDPEER, PEER]), "unscoped: both")
        self.assertEqual(jd._open_ask_peers(SID, since=T0), [PEER], "scoped: only at/after")


class DiaryStandDown(_Base):
    """C3: a re-assert whose audited evidence predates the diary's own ending yields."""

    def _stamped(self):
        self._log([_msg(1, SID, PEER, T0 + 10, "question")])
        s, gid = self._store()
        self._close_peer(s)
        self.assertEqual(s["nodes"][gid].get("awaitingKind"), "peer", "fixture: stamped")
        return s, gid

    def test_a_reassert_after_the_diarys_lift_stands_down(self):
        s, gid = self._stamped()
        nd = s["nodes"][gid]
        self.assertTrue(jd.record_verdict(s, nd, "romp", "awaiting", T0 + 600, lift=True),
                        "fixture: the wait ENDED in the diary (a lift row lands)")
        # the closer now audits an OLDER segment (ev t=T0+500 < the lift's arrival) and re-asserts
        # with a CHANGED why — pre-fix this filed a fresh stamp over the ended wait
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": "still holding for the verify",
                                                             "kind": "peer"}}}, t=T0 + 500)
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"),
                          "the diary ended this wait after the audited evidence — the writer yields")

    def test_a_reassert_from_fresh_evidence_still_files(self):
        s, gid = self._stamped()
        nd = s["nodes"][gid]
        jd.record_verdict(s, nd, "romp", "awaiting", T0 + 600, lift=True)
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        # a NEWER audited turn (evidence past the lift's arrival) re-asserts: a genuinely new wait
        row_at = max((e.get("at") or 0) for e in nd["log"])
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": "asked again after the fix",
                                                             "kind": "peer"}}}, t=row_at + 60)
        self.assertEqual(s["nodes"][gid].get("awaitingWhy"), "asked again after the fix",
                         "fresh evidence out-orders the ending — the new wait files as ever")


class KindBoundaries(_Base):
    """C4a: the prompt's hard kind boundaries (the laundering shapes, named)."""

    def test_the_kind_boundaries_are_stated(self):
        for phrase in ("another SESSION's work is never a job",
                       # 2026-09-05: a closer stamped kind=job for a Monitor plus a background
                       # command the session itself was running — "job" is for compute the session
                       # cannot watch from inside the harness; its own commands/watchers/subagents
                       # are task/agents. The dead-man for job waits is a 6h clock, so a
                       # mislabelled in-harness wait costs hours the exact lifts would have saved.
                       "cannot watch from inside the harness",
                       "own background command, Monitor, or subagent is task or agents, never job",
                       "never one the turn "
                       "canceled",
                       "an idle recipient reads idle",
                       "Never relabel a wait to a different "
                       "kind to get it filed"):
            self.assertIn(phrase, jd.CLOSER_SYS)


class LiftHorizon(_Base):
    """The diary stand-down compares EVIDENCE to EVIDENCE (2026-09-07). A lift row carries two times:
    `at`, when it was filed (forensics, per record_verdict), and — since this change — `endEv`, the
    newest evidence it RULED ON (the return that came back, the peer's reply, the last in-harness
    ending). The gate used to read `at`, so a lift filed late for old evidence silenced every fresh
    assert whose evidence lay between what the lift saw and when it was filed: the card stayed down on
    new information. The lift rows here are written BY HAND so both times are deterministic
    (record_verdict stamps `at` from the clock); ev_t is the anchor the lift retracts, as _lift_ev_t
    writes it. SYNTHETIC fixtures."""

    T_EV, T_ARR = T0 + 600, T0 + 900          # what the lift ruled on, and its (late) filing

    def _stamped_then_lifted(self, **row):
        self._log([_msg(1, SID, PEER, T0 + 10, "question")])
        s, gid = self._store()
        self._close_peer(s)
        nd = s["nodes"][gid]
        nd["log"][-1]["at"] = T0 + 550          # the closer's write, pinned (the fixture's diary is all synthetic)
        nd["log"].append({"ev_t": T0 + 500, "src": "romp", "kind": "awaiting", "lift": True,
                          "at": self.T_ARR, **row})
        jd._materialize_node(nd)
        self.assertIsNone(nd.get("awaitingWhy"), "fixture: the lift ended the wait")
        return s, gid

    def _reassert(self, s, t):
        # a closer pass on a turn triggered at `t` re-asserts (kind=task: no peer admit gate in the way)
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": "the rebuild is running again; holding",
                                                             "kind": "task"}}}, t=t)

    def test_a_an_assert_on_evidence_the_lift_never_ruled_on_stands(self):
        # THE case: horizon T_ev < the assert's evidence T_x < the lift's filing T_arr. Read by filing
        # time the lift looked "after" the assert and suppressed it (main: the stamp is missing); its
        # own horizon says it ruled on nothing past T_ev — the assert is new information and stands.
        s, gid = self._stamped_then_lifted(endEv=self.T_EV)
        self._reassert(s, t=self.T_EV + 100)
        self.assertEqual(s["nodes"][gid].get("awaitingWhy"), "the rebuild is running again; holding",
                         "evidence past the lift's horizon is new information — the assert stands")

    def test_b_an_assert_on_evidence_the_lift_ruled_on_still_stands_down(self):
        s, gid = self._stamped_then_lifted(endEv=self.T_EV)
        self._reassert(s, t=self.T_EV - 50)
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"),
                          "the diary ended this wait after the audited evidence — the writer yields")

    def test_b2_the_boundary_is_strict_an_assert_at_the_horizon_stands(self):
        # "predates" is strict: evidence EQUAL to the horizon is not older than what the lift ruled on
        # (the closer's audit reaches past its own trigger). Pins `>` — a `>=` mutant suppresses this.
        s, gid = self._stamped_then_lifted(endEv=self.T_EV)
        self._reassert(s, t=self.T_EV)
        self.assertIsNotNone(s["nodes"][gid].get("awaitingWhy"), "equal evidence does not predate — stands")

    def test_c_a_row_without_a_horizon_keeps_the_arrival_compare_it_always_had(self):
        # a lift row filed before horizons were journaled (no endEv): its arrival is its horizon, the
        # compare this gate made before 2026-09-07, so the upgrade changes nothing for it (review find,
        # 2026-09-08). Reading its ev_t instead (the anchor of the wait it retracted) disowned nothing:
        # a stale re-assert inside (anchor, filing) was admitted, and the sweep re-lifted it next pass
        s, gid = self._stamped_then_lifted()                       # ev_t = T0+500, at = T_ARR, no endEv
        self._reassert(s, t=T0 + 700)                              # between the row's ev_t and its filing
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"),
                          "no horizon → arrival is the horizon, as before: the re-assert yields")
        self._reassert(s, t=self.T_ARR + 1)                        # evidence past the filing: new information
        self.assertIsNotNone(s["nodes"][gid].get("awaitingWhy"), "…and one past the filing stands, as before")
        # the accessor itself, in order: endEv, then at, then ev_t, then nothing
        self.assertEqual(jd._wait_end_ev({"endEv": 5, "ev_t": 7, "at": 9}), 5)
        self.assertEqual(jd._wait_end_ev({"ev_t": 7, "at": 9}), 9)
        self.assertEqual(jd._wait_end_ev({"ev_t": 7}), 7)
        self.assertEqual(jd._wait_end_ev({}), 0)

    def test_c2_the_horizon_keeps_its_fraction(self):
        # a Monitor's recorded ceiling is t + timeout_ms/1000, fractional when timeout_ms is not a whole
        # second. int()-truncated at the journal, the ceiling the lift cited was disowned: the sweep's
        # inclusive stand-down measured 600.5 against 600 and re-lifted every pass (pinned through the
        # sweep in test_kernel_awaiting_lift), and this gate admitted an assert from the ceiling's own
        # second (review find, 2026-09-08; pre-fix: endEv 600 and the T_EV assert stands)
        self._log([_msg(1, SID, PEER, T0 + 10, "question")])
        s, gid = self._store()
        self._close_peer(s)
        nd = s["nodes"][gid]
        nd["log"][-1]["at"] = T0 + 550                             # the closer's write, pinned (synthetic diary)
        self.assertTrue(jd.record_verdict(s, nd, "romp", "awaiting", T0 + 500, lift=True, end_ev=self.T_EV + 0.5))
        self.assertEqual(nd["log"][-1].get("endEv"), self.T_EV + 0.5, "journaled as given, never truncated")
        self.assertIsNone(nd.get("awaitingWhy"), "fixture: the lift ended the wait")
        self._reassert(s, t=self.T_EV)                             # the ceiling's own second: predates it
        self.assertIsNone(nd.get("awaitingWhy"), "evidence at int(horizon) still predates a fractional horizon")
        self._reassert(s, t=self.T_EV + 1)
        self.assertIsNotNone(nd.get("awaitingWhy"), "the next second is past it: stands")

    def test_e_the_closers_own_lift_journals_the_audited_turns_end(self):
        # the closer's lift ruled on the WHOLE turn, so its horizon is the turn's last event (t_end,
        # threaded by _close_turn, exercised in test_judge_close_standdown's CloserLiftHorizon), not its
        # trigger: a re-assert ruled from an anchor inside the audited span then yields
        self._log([_msg(1, SID, PEER, T0 + 10, "question")])
        s, gid = self._store()
        self._close_peer(s)                                        # stamped at T0+500
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {}, "awaiting": {}},
                       t=T0 + 700, touched=1, t_end=T0 + 900)      # a later turn of its own, not re-asserted
        row = s["nodes"][gid]["log"][-1]
        self.assertEqual((row.get("lift"), row.get("ev_t"), row.get("endEv")), (True, T0 + 700, T0 + 900),
                         "the closer's lift: anchored at the trigger, horizon at the turn's end")
        self._reassert(s, t=T0 + 800)                              # evidence inside the audited span
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"), "the lift ruled past this evidence — yields")

    def test_e2_the_closers_done_journals_the_turns_end_and_the_gate_reads_it(self):
        # done rows are endings for this gate too. The closer's done ruled on the whole audited turn, so
        # it journals the horizon its lift does (t_end); with none, a row reads its arrival, later than
        # the turn by the closer's own latency, and silenced an assert from a turn triggered in that gap
        # (review find, 2026-09-08; pre-fix: no endEv on the done, and the T0+550 assert stood)
        s, gid = self._store()
        nd = s["nodes"][gid]
        jd.apply_close(s, jd.open_menu(s), {"done": {1: "shipped the exporter"}, "block": {}, "awaiting": {}},
                       t=T0 + 500, t_end=T0 + 600)
        row = nd["log"][-1]
        self.assertEqual((row.get("kind"), row.get("ev_t"), row.get("endEv")), ("done", T0 + 500, T0 + 600),
                         "the closer's done: anchored at the trigger, horizon at the turn's end")
        row["at"] = T0 + 900                                       # a late filing, pinned (synthetic diary)
        jd.record_verdict(s, nd, "agent", "reopen", T0 + 650, why="the agent re-opened its own to-do")
        self.assertFalse(nd.get("nodeComplete"), "fixture: reopened, so the closer may stamp it again")
        self._reassert(s, t=T0 + 550)                              # inside the done's audited span
        self.assertIsNone(nd.get("awaitingWhy"), "the done ruled past this evidence: yields")
        self._reassert(s, t=T0 + 700)                              # past the turn, before the filing
        self.assertEqual(nd.get("awaitingWhy"), "the rebuild is running again; holding",
                         "evidence the done never ruled on stands: its arrival is not its horizon")

    def test_e3_a_done_with_no_horizon_keeps_the_arrival_compare_it_always_had(self):
        # a done from a writer that journals no horizon (the planner's, the user's, the link-back): its
        # arrival is its horizon, exactly the compare the gate made before 2026-09-07, so the upgrade
        # changes nothing for it (review find, 2026-09-08; pre-fix: ev_t was read, and the assert stood)
        s, gid = self._store()
        nd = s["nodes"][gid]
        self.assertTrue(jd.record_verdict(s, nd, "planner", "done", T0 + 500, why="shipped"))
        nd["log"][-1]["at"] = T0 + 900                             # filed late, pinned (synthetic diary)
        jd.record_verdict(s, nd, "agent", "reopen", T0 + 650, why="the agent re-opened its own to-do")
        self.assertFalse(nd.get("nodeComplete"), "fixture: reopened")
        self._reassert(s, t=T0 + 700)                              # between the done's evidence and its filing
        self.assertIsNone(nd.get("awaitingWhy"), "no horizon → arrival, as before: the re-assert yields")
        self._reassert(s, t=T0 + 901)
        self.assertIsNotNone(nd.get("awaitingWhy"), "…and one past the filing stands, as before")

    def test_f_a_peer_stamps_write_is_the_moment_its_own_gate_read_the_log(self):
        # why the peer supersede (kernel._peer_stamp_superseded) compares the reply to the stamp's WRITE
        # time and not to the assert's evidence time (review 2026-09-08): the peer-kind write gate reads
        # the postal log AT the write, so a reply that landed after the audited turn's trigger but before
        # the write already stands the assert down: a standing peer stamp certifies no reply existed when
        # it was written, and a reply is new information exactly when it postdates that read
        self._log([_msg(1, SID, PEER, T0 + 10, "question"), _msg(2, PEER, SID, T0 + 700, "coordinate")])
        s, gid = self._store()
        self._close_peer(s)                                        # the audited turn's trigger, T0+500 < the reply
        self.assertIsNone(s["nodes"][gid].get("awaitingWhy"),
                          "the reply predates the write: the gate saw it, nothing is filed")
        self._log([_msg(1, SID, PEER, T0 + 10, "question")])      # no reply yet when the closer writes
        s, gid = self._store()
        self._close_peer(s)
        self.assertEqual(s["nodes"][gid].get("awaitingKind"), "peer", "an open ask at the write admits the stamp")

    def test_d_record_verdict_journals_the_horizon_exactly_when_given(self):
        s, gid = self._store()
        nd = s["nodes"][gid]
        self.assertTrue(jd.record_verdict(s, nd, "romp", "awaiting", T0 + 500, lift=True, end_ev=self.T_EV))
        self.assertEqual(nd["log"][-1].get("endEv"), self.T_EV, "the horizon rides the row it explains")
        self.assertTrue(jd.record_verdict(s, nd, "closer", "awaiting", T0 + 510, why="a fresh wait"))
        self.assertNotIn("endEv", nd["log"][-1], "no horizon given → none journaled")


if __name__ == "__main__":
    unittest.main()
