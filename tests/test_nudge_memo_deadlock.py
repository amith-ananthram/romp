#!/usr/bin/env python3
"""The redundancy memo must never be a PERMANENT veto (the user 2026-08-29, the quiet-session
deadlock): the T142 memo keyed only on newest-evidence-time, and a quiet session's evidence time
never moves — so one redundant ruling froze four Working cards in an 8-hour skipped-redundant-memo
loop, breaking the 2026-08-22 promise that every Working card is nudged/woken until it lands. The
fix, all event-keyed: (1) the memo is TWO-keyed — evidence time AND the session's settle event
(_settle_event_key: the hook ledger's lastStopAt primary, the states/ idle atom fallback); either
key moving re-arms exactly ONE re-judge, which re-memos against the new pair — no storm returns;
(2) a session parked past the wake ladder's dead-man stand (answeredAt + AWAITING_DEADMAN_SECS,
the documented stand for unobservable waits — a fully-quiet session produces NO future event, so
no key change can ever re-arm it) takes a real fire that re-engages it, once per memo epoch;
(3) a moot retire is not terminal on a parked session — its own justification ("re-arms on the
next GENUINE ended turn") never holds when no turn will come; (4) skipped-redundant-memo rows
carry parkedS, so the deadlock fingerprint is countable from nudge-events.jsonl alone.
SYNTHETIC fixtures only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_memodl", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-dddddddddddd"
G1 = SID + ":g1"
ARM_T, NOW = 1_787_000_000, 1_787_000_600
PARKED = NOW - km.AWAITING_DEADMAN_SECS - 60      # answeredAt long past the dead-man stand


def _store():
    return {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
            "nodes": {G1: {"id": G1, "text": "Ship the exporter", "parentId": None,
                           "nodeComplete": False, "blocked": False, "cleared": False,
                           "trail": [], "t": ARM_T - 100, "mt": ARM_T - 100, "log": []}},
            "confirming": []}


class MemoDeadlock(unittest.TestCase):
    """Drives the real _auto_nudge_session with the fresh-guard harness idiom."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        km._autonudge_cache.clear()
        self._orig_km = {n: getattr(km, n) for n in (
            "_session_flag", "_compacting_now", "_api_error", "_session_working",
            "_interrupt_suppresses_nudge", "_backend_queued", "_backend_rewind_pending",
            "_last_state", "_session_awaiting", "_closer_settled", "_revivers_pending",
            "_pending_ops", "_last_assistant_report", "_all_outstanding_delegated")}
        self._orig_jd = {n: getattr(jd, n) for n in ("parsed_session", "load_goals", "load_goals_shared_or_fault", "_segs",
                                                     "plan_units", "nudge_redundant")}
        self._orig_backend = km.Sessions.backend_for
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="", **k: False
        km._backend_queued = lambda sid: False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a, **k: False
        km._closer_settled = lambda *a: True
        km._revivers_pending = lambda *a: ""
        km._all_outstanding_delegated = lambda nodes, gid: False
        km._pending_ops = {}
        jd._segs = lambda tn, store: []
        jd.plan_units = lambda session, store: []
        uid = "u-t1"
        self.turns = [{"id": "t1", "t": ARM_T, "end": ARM_T + 60, "ended": True,
                       "trigger": {"uuid": uid},
                       "atoms": [{"uuid": uid, "type": "user", "author": "human", "t": ARM_T}]}]
        jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}
        self.store = _store()
        jd.load_goals = lambda sid: self.store
        jd.load_goals_shared_or_fault = lambda sid: (self.store, None)   # the walk reads the shared view; the fresh re-read stays on load_goals
        self.sent = []
        self.reports = [("working through the queue", ARM_T + 50),
                        ("working through the queue", ARM_T + 50)]
        self.report_reads = []
        self.judge_calls = []
        self.judge_replies = []
        test = self
        km._last_assistant_report = lambda path, cap=4000: (
            test.report_reads.append(1) or test.reports[min(len(test.report_reads) - 1,
                                                            len(test.reports) - 1)])
        jd.nudge_redundant = lambda gtxt, recent: (
            test.judge_calls.append(recent) or
            test.judge_replies[min(len(test.judge_calls) - 1, len(test.judge_replies) - 1)])

        class FakeBackend:
            def send(self, sid, body):
                test.sent.append(body)
        km.Sessions.backend_for = staticmethod(lambda sid: FakeBackend())

    def tearDown(self):
        for n, v in self._orig_km.items():
            setattr(km, n, v)
        for n, v in self._orig_jd.items():
            setattr(jd, n, v)
        km.Sessions.backend_for = self._orig_backend
        jd.STATE = self.saved_state
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _tick(self):
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        return km._auto_nudge_session({"sid": SID, "path": "/nonexistent.jsonl"}, NOW, {}, nudged, {})

    def _rows(self):
        p = jd.STATE / "nudge-events.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def _seed_rec(self, rec):
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({"enabled": True, "nudged": {G1: rec}}))
        km._autonudge_cache.clear()

    def _rec(self):
        return km._auto_nudge_data()["nudged"][G1]

    # ── the park gate: a parked goal sleeps — no rows, no work, per visit ──
    def test_a_parked_goal_sleeps_silently(self):
        # the parked-tick round (the user 2026-08-30): per-visit memo re-serves were 98.5% of a
        # day's nudge-events rows (~9k/day). A goal whose memo pair still matches the world is
        # PARKED: the gate continues before the fire list, the judge batch, and the log — the
        # log gains rows only on state CHANGES (park mint / re-armed / fired), never per visit.
        self._seed_rec({"lastTurnId": "t0", "count": 1, "answeredAt": NOW - 300,
                        "redundantSkips": 1, "redundantEvT": ARM_T + 50, "redundantSettleT": 0})
        self._tick()
        self._tick()
        self.assertEqual(self.sent, [], "the park stands — no fire")
        self.assertEqual(self.judge_calls, [], "…no judge call")
        self.assertEqual(self._rows(), [], "…and NO rows: two parked visits, zero log growth")

    def test_a_new_settle_event_rearms_exactly_one_rejudge_then_rememos(self):
        self._seed_rec({"lastTurnId": "t0", "count": 1, "answeredAt": NOW - 300,
                        "redundantSkips": 1, "redundantEvT": ARM_T + 50, "redundantSettleT": 0})
        km._last_state = lambda sid: ("idle", NOW - 30)   # the session settled ANEW since the memo
        self.judge_replies = [True]
        self._tick()
        self.assertEqual(self.sent, [], "still redundant — no fire")
        self.assertEqual(len(self.judge_calls), 1, "the new settle bought exactly ONE re-judge")
        self.assertEqual(self._rec().get("redundantSettleT"), NOW - 30,
                         "…which re-memos against the new settle key")
        rows = self._rows()
        self.assertEqual([r["verdict"] for r in rows], ["re-armed", "skipped-redundant"],
                         "one row per state change: the park lifted, then the re-park")
        self.assertEqual(rows[0]["parkedS"], 300, "the re-armed row says how long it sat")
        self._tick()
        self.assertEqual(len(self.judge_calls), 1, "same pair again → parked, no loop")
        self.assertEqual(len(self._rows()), 2, "…and the parked visit logs nothing")

    def test_a_pre_upgrade_record_missing_the_settle_key_rejudges_exactly_once(self):
        # THE UPGRADE PATH: every record minted before this change carries redundantEvT but no
        # redundantSettleT — the veto must FAIL for it (None never equals the always-int settle
        # key, 0 included) so each deployed deadlock gets exactly one fresh judgment, whose
        # re-memo then carries both keys. Pins the .get() default: rec0.get("redundantSettleT")
        # must not grow a 0 fallback, or every pre-upgrade record on a settle-key-0 session
        # regains the frozen veto this round exists to kill.
        self._seed_rec({"lastTurnId": "t0", "count": 1, "answeredAt": NOW - 300,
                        "redundantSkips": 1, "redundantEvT": ARM_T + 50})
        self.judge_replies = [True]
        self._tick()
        self.assertEqual(self.sent, [], "still redundant — no fire")
        self.assertEqual(len(self.judge_calls), 1, "the missing key bought exactly ONE re-judge")
        self.assertEqual(self._rec().get("redundantSettleT"), 0,
                         "…and the re-memo now carries the settle key")
        self._tick()
        self.assertEqual(len(self.judge_calls), 1, "second tick: parked on the upgraded memo")
        self.assertEqual([r["verdict"] for r in self._rows()], ["skipped-redundant"],
                         "…silently — the park is the state, the mint row was its record")

    def test_a_held_rearm_logs_once_not_per_visit(self):
        # the adversarial pass's catch: a downstream hold can defer the fire for many ticks with
        # the memo keys still the old pair — without the rearm stamp, re-armed logged per visit,
        # the very shape this round kills. Simulate the hold with a not-redundant verdict whose
        # fire is deferred by a reviver: judge says fire, reviver defers, keys unchanged.
        self._seed_rec({"lastTurnId": "t0", "count": 1, "answeredAt": NOW - 300,
                        "redundantSkips": 1, "redundantEvT": ARM_T + 50, "redundantSettleT": 0})
        km._last_state = lambda sid: ("idle", NOW - 30)   # a key moved → the park lifts
        km._revivers_pending = lambda *a: "judge-pass"     # …but a reviver holds every fire
        self._tick()
        self._tick()
        self.assertEqual(self.sent, [], "the hold defers the fire")
        self.assertEqual([r["verdict"] for r in self._rows()], ["re-armed"],
                         "…and the lift logged ONCE across both held visits")

    def test_an_unreadable_report_keeps_the_park(self):
        # the conservative leg: the memo dies when the report MOVES — a transient read failure is
        # not a move, so the park stands (no re-arm row, no judge call, no fire).
        self._seed_rec({"lastTurnId": "t0", "count": 1, "answeredAt": NOW - 300,
                        "redundantSkips": 1, "redundantEvT": ARM_T + 50, "redundantSettleT": 0})
        self.reports = [("", 0)]
        self._tick()
        self.assertEqual(self.sent, [], "no blind re-arm off a failed read")
        self.assertEqual(self.judge_calls, [])
        self.assertEqual(self._rows(), [])

    # ── the parked dead-man: the quiet-session deadlock's exit ──
    def test_parked_past_the_deadman_fires_once_and_writes_a_fresh_record(self):
        self._seed_rec({"lastTurnId": "t0", "count": 1, "answeredAt": PARKED,
                        "redundantSkips": 2, "redundantEvT": ARM_T + 50, "redundantSettleT": 0})
        self.assertTrue(self._tick())
        self.assertEqual(len(self.sent), 1, "the parked backstop re-engages the session")
        self.assertEqual(self.judge_calls, [], "an escalation, not a judgment — no judge call")
        rows = [r for r in self._rows() if r["verdict"] not in ("skipped-redundant-memo",)]
        self.assertEqual([r["verdict"] for r in rows], ["fired-parked-backstop"])
        self.assertEqual(rows[0]["evT"], ARM_T + 50, "the row names the evidence it out-waited")
        rec = self._rec()
        self.assertEqual(rec.get("lastTurnId"), "t1", "a fresh fire record replaces the memo's")
        self.assertNotIn("redundantEvT", rec, "…so the memo epoch is over")
        self._tick()
        self.assertEqual(len(self.sent), 1, "one fire per epoch: the fresh record owns the aftermath")

    # ── moot is not terminal on a parked session ──
    def test_moot_on_a_parked_session_reenters_the_ladder(self):
        self._seed_rec({"lastTurnId": "t1", "count": 1, "moot": True, "answeredAt": PARKED})
        self.judge_replies = [False]
        self.assertTrue(self._tick())
        self.assertEqual(len(self.sent), 1,
                         "moot + still Working + parked past the dead-man → the ladder proceeds")
        self.assertEqual([r["verdict"] for r in self._rows()], ["fired"])

    def test_a_late_moot_ruling_gets_its_own_full_patience_window(self):
        # the adversarial pass's last finding (its verifier died mid-run; verified by hand): the
        # escape's anchor once read only answeredAt/at, so a moot ruling landing HOURS after the
        # fire inherited a spent window and re-entered the very next tick. The moot ruling is new
        # information — patience runs from ITS stamp (mootAt), not the fire's.
        self._seed_rec({"lastTurnId": "t1", "count": 1, "moot": True,
                        "at": PARKED, "answeredAt": PARKED, "mootAt": NOW - 300})
        self._tick()
        self.assertEqual(self.sent, [], "the fresh moot ruling restarts the stand — no instant re-entry")
        self.assertEqual(self._rows(), [])

    def test_moot_within_patience_stays_silent(self):
        self._seed_rec({"lastTurnId": "t1", "count": 1, "moot": True, "answeredAt": NOW - 300})
        self._tick()
        self.assertEqual(self.sent, [], "the moot retire holds while the stand is still patient")
        self.assertEqual(self._rows(), [])

    def test_failed_stays_terminal_however_long_parked(self):
        self._seed_rec({"lastTurnId": "t1", "count": 1, "failed": True, "failedAt": PARKED,
                        "answeredAt": PARKED, "at": PARKED})
        self._tick()
        self.assertEqual(self.sent, [], "failed already produced the needs-you block — the user's "
                                        "reply is the pending event, not another fire")
        self.assertEqual(self._rows(), [])


class SettleEventKey(unittest.TestCase):
    """_settle_event_key: hook-ledger lastStopAt primary, idle-atom fallback, absence reads 0."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        self._orig_last_state = km._last_state
        km._last_state = lambda sid: ("idle", 99)

    def tearDown(self):
        km._last_state = self._orig_last_state
        jd.STATE = self.saved_state
        self.td.cleanup()

    def test_hook_ledger_wins_when_present(self):
        (jd.STATE / "sdk").mkdir(parents=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"lastStopAt": 12345}))
        self.assertEqual(km._settle_event_key(SID), 12345)

    def test_absent_field_falls_to_the_idle_atom(self):
        (jd.STATE / "sdk").mkdir(parents=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"lastSid": SID}))
        self.assertEqual(km._settle_event_key(SID), 99, "a pre-ledger SDK session reads the atom")

    def test_no_registry_no_atom_reads_zero(self):
        km._last_state = lambda sid: ("", 0)
        self.assertEqual(km._settle_event_key(SID), 0, "absence is 'no settle evidence', never a crash")


if __name__ == "__main__":
    unittest.main()
