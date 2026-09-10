#!/usr/bin/env python3
"""The nudge's FIRE-TIME FRESHNESS GUARD + verdict instrumentation (the user 2026-08-25, the
completion-vs-nudge collision): a due nudge and a completion report meet at the end of every long
autonomous run — the redundancy judge deliberates over a snapshot of the transcript tail, and a
report landing DURING that deliberation was invisible (the verified specimen: report 11:18:14, the
session stopped its own loop 11:18:32, the nudge fired 11:18:33 asking where the work stood). The
guard is the writer-yields family at the fire moment: the transcript's newest assistant timestamp
moving past the snapshot's IS the world outrunning the evidence — hold, re-judge ONCE against the
fresh report (never a loop), fire only what survives. Every gate decision is now a nudge-events row
(fired / skipped-redundant / held-fresh-re-judged / fired-at-cap / skipped-redundant-at-cap /
skipped-redundant-memo / resolved-at-send / blocked-on-user-at-send, each carrying the evidence
timestamp), so redundant
fires are countable from the log alone. That accounting is what re-shaped the cap (the user
2026-08-27, T120): the blind third fire measured at 56% of deliveries, re-asking answered
questions — the cap now ESCALATES to one more judgment on the freshest evidence, never past it.
And the send moment re-reads the store: an item the fresh rollup no longer calls working leaves
the card (resolved-at-send / blocked-on-user-at-send) — the redundancy judge deliberates for
seconds, and verdicts land in that gap. SYNTHETIC fixtures only."""
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
km = load_source("romp_kernel_freshg", os.path.join(BIN, "romp-kernel"))
jd = km.jd                                        # the kernel's OWN judge instance — patches must land
#                                                   where the walk reads them (a same-named reload would
#                                                   be a different module object)

SID = "11111111-2222-3333-4444-cccccccccccc"
G1 = SID + ":g1"
ARM_T, NOW = 1_787_000_000, 1_787_000_600


def _store():
    return {"rompUuid": SID, "seq": 1, "placements": {}, "status": {},
            "nodes": {G1: {"id": G1, "text": "Ship the exporter", "parentId": None,
                           "nodeComplete": False, "blocked": False, "cleared": False,
                           "trail": [], "t": ARM_T - 100, "mt": ARM_T - 100, "log": []}},
            "confirming": []}


class FreshGuard(unittest.TestCase):
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
        # the tail reads the gate makes, in order: [snapshot, fire-time freshness re-read]
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

    def test_a_completion_landing_mid_deliberation_holds_and_rejudges_once(self):
        # the specimen: the snapshot predates the report; the judge says not-redundant against the
        # STALE text; the fire-time re-read sees a newer timestamp; the ONE re-judge against the
        # fresh report says redundant → no fire, and the row says exactly what happened
        self.reports = [("working through the queue", ARM_T + 50),
                        ("all done — merged and reported", ARM_T + 590)]
        self.judge_replies = [False, True]
        self._tick()
        self.assertEqual(self.sent, [], "the redundant fire never went out")
        self.assertEqual(len(self.judge_calls), 2, "held and re-judged exactly ONCE — never a loop")
        self.assertIn("all done", self.judge_calls[1], "…and the re-judge saw the FRESH report")
        rows = self._rows()
        self.assertEqual([r["verdict"] for r in rows], ["held-fresh-re-judged"])
        self.assertEqual(rows[0]["evT"], ARM_T + 590, "the row carries the evidence it skipped on")
        rec = km._auto_nudge_data()["nudged"][G1]
        self.assertEqual(rec.get("answeredAt"), NOW, "the report counts as the answer, as ever")

    def test_a_genuinely_stalled_goal_fires_exactly_as_today(self):
        self.judge_replies = [False]
        self.assertTrue(self._tick())
        self.assertEqual(len(self.sent), 1, "stalled → the fire goes out")
        self.assertEqual(len(self.judge_calls), 1, "the timestamp never moved — no second judge")
        rows = self._rows()
        self.assertEqual([r["verdict"] for r in rows], ["fired"])
        self.assertEqual(rows[0]["evT"], ARM_T + 50)

    def test_redundant_on_the_first_look_skips_with_its_row(self):
        self.judge_replies = [True]
        self._tick()
        self.assertEqual(self.sent, [])
        self.assertEqual([r["verdict"] for r in self._rows()], ["skipped-redundant"])

    def test_the_cap_consults_the_judge_and_a_fresh_answer_skips(self):
        # T120 (the user 2026-08-27): past two consecutive skips the judge is CONSULTED once more
        # instead of short-circuited — a still-redundant verdict skips, because the report IS the
        # answer (the measured blind fire delivered 38 of 68 nudges, two of them AFTER the report
        # they asked about).
        self._seed_rec({"count": 1, "lastTurnId": "t0", "armAtoms": 1, "at": ARM_T - 500,
                        "redundantSkips": 2})
        self.reports = [("all done — merged and reported", ARM_T + 50)]
        self.judge_replies = [True]
        self._tick()
        self.assertEqual(self.sent, [], "the judge ruled the ask answered — no fire at the cap")
        self.assertEqual(len(self.judge_calls), 1, "the cap consulted the judge, never bypassed it")
        self.assertEqual([r["verdict"] for r in self._rows()], ["skipped-redundant-at-cap"])
        rec = km._auto_nudge_data()["nudged"][G1]
        self.assertEqual(rec.get("answeredAt"), NOW, "the report is recorded as the answer")

    def test_the_cap_fires_when_the_judge_says_the_ask_is_live(self):
        # the forever-pause the cap was built against still ends: a not-redundant verdict at the
        # cap fires — now as a JUDGED fire, and its row says how
        self._seed_rec({"count": 1, "lastTurnId": "t0", "armAtoms": 1, "at": ARM_T - 500,
                        "redundantSkips": 2})
        self.judge_replies = [False]
        self.assertTrue(self._tick())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.judge_calls), 1)
        self.assertEqual([r["verdict"] for r in self._rows()], ["fired-at-cap"])
        self.assertEqual(km._auto_nudge_data()["nudged"][G1].get("redundantSkips", 0), 0,
                         "a real fire resets the count")

    def test_a_capped_candidate_rides_the_held_pass_re_judge(self):
        # the old early-keep was the second half of the blind fire: a report landing DURING
        # deliberation now re-judges a capped candidate exactly like an organic one
        self._seed_rec({"count": 1, "lastTurnId": "t0", "armAtoms": 1, "at": ARM_T - 500,
                        "redundantSkips": 2})
        self.reports = [("working through the queue", ARM_T + 50),
                        ("all done — merged and reported", ARM_T + 590)]
        self.judge_replies = [False, True]           # live on the snapshot; answered by the fresh report
        self._tick()
        self.assertEqual(self.sent, [], "the fresh report answered the ask mid-tick")
        self.assertEqual(len(self.judge_calls), 2, "snapshot consult + held-pass re-judge")
        # the verdict is the ORDINARY freshness hold, deliberately: the cap's escalated consult
        # ruled the ask live (resetting the consecutive count), so the held-pass yield is exactly
        # an organic writer-yields — the at-cap names mark only decisions taken AT the cap
        self.assertEqual([r["verdict"] for r in self._rows()], ["held-fresh-re-judged"])
        self.assertEqual(self._rows()[0]["evT"], ARM_T + 590,
                         "the row carries the fresh evidence it yielded to")

    def test_a_memoed_pair_serves_without_a_judge_call(self):
        # THE MEMO (T142): a (goal, evidence) pair ruled redundant is never re-judged — a skip
        # consumes no arm, so a capped goal came due every tick and re-judged constant evidence
        # (147 re-judgments on one goal in 68 minutes; ~$1.92/day)
        self._seed_rec({"count": 1, "lastTurnId": "t0", "armAtoms": 1, "at": ARM_T - 500,
                        "redundantSkips": 1, "redundantEvT": ARM_T + 50,
                        "redundantSettleT": 0})       # both memo keys current (2026-08-29: the memo
        #                                               is two-keyed; a record missing the settle key
        #                                               re-judges ONCE by design — the upgrade path,
        #                                               pinned in test_nudge_memo_deadlock.py)
        self.judge_replies = [True]                   # would be consulted only by a bug
        self._tick()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.judge_calls, [], "the memo serves the ruling — no call")
        self.assertEqual(self._rows(), [], "a parked visit logs nothing (the parked-tick round: "
                                           "rows mark state changes, never visits)")

    def test_the_at_cap_flip_class_dies_by_construction(self):
        # 3 of 10 measured fired-at-cap rows were the judge FLIPPING on a (gid, evT) it had just
        # ruled redundant — nondeterminism delivering the stale nudge T120 exists to stop. The
        # memo is consulted before the at-cap consult too, so the flip cannot happen.
        self._seed_rec({"count": 1, "lastTurnId": "t0", "armAtoms": 1, "at": ARM_T - 500,
                        "redundantSkips": 2, "redundantEvT": ARM_T + 50,
                        "redundantSettleT": 0})       # both memo keys current (see the memo test above)
        self.judge_replies = [False]                  # the flip verdict, never reachable
        self._tick()
        self.assertEqual(self.sent, [], "a pair ruled redundant can never fire on the same evidence")
        self.assertEqual(self._rows(), [], "…and the parked visit logs nothing")
        self.assertEqual(self.judge_calls, [])

    def test_a_judged_redundancy_stamps_the_memo(self):
        self.judge_replies = [True]
        self._tick()
        rec = km._auto_nudge_data()["nudged"][G1]
        self.assertEqual(rec.get("redundantEvT"), ARM_T + 50,
                         "the ruling persists keyed on the evidence it judged")

    def test_a_moved_report_kills_the_memo_and_re_enters_the_judge(self):
        self._seed_rec({"count": 1, "lastTurnId": "t0", "armAtoms": 1, "at": ARM_T - 500,
                        "redundantSkips": 1, "redundantEvT": ARM_T + 50})
        self.reports = [("fresh words about new work", ARM_T + 800)]
        self.judge_replies = [False]
        self.assertTrue(self._tick())
        self.assertEqual(len(self.sent), 1, "new evidence → a real judgment → a real fire")
        self.assertEqual(len(self.judge_calls), 1)
        self.assertEqual([r["verdict"] for r in self._rows()], ["fired"])

    def test_an_item_resolved_mid_deliberation_leaves_at_the_send_moment(self):
        # SEND-MOMENT FRESHNESS (T120): the fire list read the store before the redundancy judge
        # deliberated — a verdict landing in that gap used to leave a resolved item on the card
        test = self
        working, resolved = self.store, _store()
        resolved["status"][G1] = "completed"
        jd.load_goals = lambda sid: resolved if test.judge_calls else working
        jd.load_goals_shared_or_fault = lambda sid: (working, None)
        self.judge_replies = [False]
        self._tick()
        self.assertEqual(self.sent, [], "nothing survives → nothing sends")
        self.assertEqual([r["verdict"] for r in self._rows()], ["resolved-at-send"])

    def test_a_block_landing_mid_deliberation_suppresses_the_send(self):
        # blocked-on-user (T120): the user IS the pending event — nudging the session re-asks a
        # question already parked on the human; the unblocker's verdict is what re-enables
        test = self
        working, blocked = self.store, _store()
        blocked["status"][G1] = "blocked"
        jd.load_goals = lambda sid: blocked if test.judge_calls else working
        jd.load_goals_shared_or_fault = lambda sid: (working, None)
        self.judge_replies = [False]
        self._tick()
        self.assertEqual(self.sent, [])
        self.assertEqual([r["verdict"] for r in self._rows()], ["blocked-on-user-at-send"])


if __name__ == "__main__":
    unittest.main()
