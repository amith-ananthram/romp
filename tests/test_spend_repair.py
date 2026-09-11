#!/usr/bin/env python3
"""romp spend-repair (T354): the arithmetic over a synthetic day's turn ledger, the buckets' before and after, the
dry run writing nothing, --apply writing the corrected buckets and rows. Synthetic ids and figures only."""
import inspect
import importlib.machinery
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads (a test below loads bin/romp-spend-rebuild)
os.environ.pop("ROMP_STATE_DIR", None)
sys.path.insert(0, os.path.join(ROOT, "cli"))
import spend_repair as rp  # noqa: E402

A, B = "aaaaaaaa-1111-2222-3333-444444444444", "bbbbbbbb-1111-2222-3333-444444444444"
T = "cccccccc-1111-2222-3333-444444444444"          # a comment thread of A (the registry's threadOf)


def reg(state, sid, **fields):
    (state / "sdk").mkdir(exist_ok=True)
    (state / "sdk" / ("%s.json" % sid)).write_text(json.dumps({"sid": sid, **fields}))
DAY = "2026-09-11"


def at(hh, mm):
    return int(datetime(2026, 9, 11, hh, mm).timestamp())


def row(sid, name, t, usd):
    return {"t": t, "sid": sid, "name": name, "resultT": t, "usd": usd, "tokIn": 10}


class Plan(unittest.TestCase):
    def setUp(self):
        # session web: two ordinary turns, a restart, its CLI's lifetime as a row, an ordinary turn, a restart, the
        # lifetime again; session api: one ordinary turn and a first-after-restart row that is a MODEST turn (fresh
        # process), never a staircase
        self.turns = [row(A, "web", at(10, 0), 3.0), row(A, "web", at(10, 20), 4.0),
                      row(A, "web", at(10, 40), 507.0), row(A, "web", at(10, 50), 2.0),
                      row(A, "web", at(11, 10), 515.0),
                      row(B, "api", at(10, 5), 1.5), row(B, "api", at(10, 45), 2.5)]
        self.restarts = [at(10, 30), at(11, 0)]

    def test_the_staircase_is_read_and_the_buckets_recomputed(self):
        p = rp.plan(self.turns, self.restarts, DAY)
        fixed = {(c["name"], c["t"]): c for c in p["rows"]}
        self.assertEqual(sorted(fixed), [("web", at(10, 40)), ("web", at(11, 10))], "the two lifetimes; api's modest row stands")
        first = fixed[("web", at(10, 40))]
        self.assertEqual((first["recorded"], first["corrected"]), (507.0, 3.5), "the day's first cumulative row: the median of web's earlier ordinary turns (3, 4)")
        second = fixed[("web", at(11, 10))]
        self.assertEqual((second["recorded"], second["corrected"]), (515.0, 6.0), "515 less 507 less the $2 row between")
        self.assertEqual(p["hours"]["%sT10" % DAY], {"before": 520.0, "after": 16.5})
        self.assertEqual(p["hours"]["%sT11" % DAY], {"before": 515.0, "after": 6.0})
        self.assertEqual(p["days"][DAY], {"before": 1035.0, "after": 22.5})
        self.assertEqual(p["bySid"][A]["after"], 18.5)
        self.assertEqual(p["bySid"][B], {"name": "api", "before": 4.0, "after": 4.0})
        self.assertEqual(p["restarts"], 2)

    def test_a_fresh_process_below_the_previous_cumulative_starts_a_new_chain(self):
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 300.0),      # a lifetime after the 9:30 restart
                 row(A, "web", at(10, 40), 5.0),                                    # after the 10:30 restart: BELOW 300, a fresh process
                 row(A, "web", at(11, 10), 60.0)]                                   # after the 11:00 restart: its new lifetime
        p = rp.plan(turns, [at(9, 30), at(10, 30), at(11, 0)], DAY)
        got = {c["t"]: (c["recorded"], c["corrected"]) for c in p["rows"]}
        self.assertNotIn(at(9, 40), got, "300 stands: no staircase descends from it (the chain that follows starts from the fresh 5), an honest long turn for all the ledger shows (M2)")
        self.assertNotIn(at(10, 40), got, "a modest first turn after a restart is a turn")
        self.assertEqual(got[at(11, 10)], (60.0, 55.0), "the new chain's cumulative less the fresh process's first row")

    def test_a_first_result_below_the_previous_cumulative_plus_the_rows_between_is_a_turn(self):
        # the first run's rule (at or above the previous cumulative alone) took 303 for the lifetime and zeroed it:
        # 303 is below 300 plus the $5 turn recorded between, which no cumulative of that process can be
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 300.0),      # a lifetime after the 9:30 restart
                 row(A, "web", at(10, 0), 5.0),                                     # an ordinary turn
                 row(A, "web", at(10, 40), 303.0),                                  # after the 10:30 restart: below 305, a fresh process
                 row(A, "web", at(11, 10), 320.0)]                                  # after the 11:00 restart: that process's lifetime
        p = rp.plan(turns, [at(9, 30), at(10, 30), at(11, 0)], DAY)
        got = {c["t"]: (c["recorded"], c["corrected"]) for c in p["rows"]}
        self.assertNotIn(at(10, 40), got, "a figure below the previous cumulative plus the rows between is a turn")
        self.assertEqual(got[at(11, 10)], (320.0, 17.0), "the new chain's cumulative less the fresh process's first row")
        self.assertNotIn(at(9, 40), got, "300 stands: the staircase that follows descends from the fresh 303, not from it (M2)")

    def test_a_lone_first_result_with_no_staircase_after_it_stands(self):
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 300.0), row(A, "web", at(10, 0), 5.0)]
        self.assertEqual(rp.plan(turns, [at(9, 30)], DAY)["rows"], [], "one big first result and no chain: a fresh process's long turn")
        # M2: a later step on a chain that does NOT descend from it (a CLI that died mid-day, then a fresh chain's
        # re-bill) keeps an honest expensive first turn as it is
        turns2 = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 45.0), row(A, "web", at(10, 0), 3.0),
                  row(A, "web", at(10, 40), 4.0),        # after the 10:30 restart: below 45 + 3, a fresh process
                  row(A, "web", at(11, 10), 30.0)]       # after the 11:00 restart: that process's lifetime, 4 + 26
        got = {c["t"]: c["corrected"] for c in rp.plan(turns2, [at(9, 30), at(10, 30), at(11, 0)], DAY)["rows"]}
        self.assertEqual(got, {at(11, 10): 26.0}, "the 45 stands: the staircase that follows starts from 4, not from it")
        # and one that DOES descend from it takes it
        turns3 = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 45.0), row(A, "web", at(10, 0), 3.0), row(A, "web", at(10, 40), 50.0)]
        got3 = {c["t"]: c["corrected"] for c in rp.plan(turns3, [at(9, 30), at(10, 30)], DAY)["rows"]}
        self.assertEqual(got3, {at(9, 40): 2.0, at(10, 40): 2.0}, "45 is the first cumulative (the typical 2.0: the one row before it), 50 the next: 50 - 45 - 3")
        repaired = [turns[0], turns[1] | {"usd": 2.0, "usdRecorded": 300.0, "repairedT": 1}, turns[2]]
        p = rp.plan(repaired, [at(9, 30)], DAY)
        self.assertEqual([(c["current"], c["corrected"], c.get("restore")) for c in p["rows"]], [(2.0, 300.0, True)])
        self.assertIn("lone first result after a restart with no staircase following it", p["rows"][0]["reason"])

    def test_a_second_run_restores_a_turn_the_first_run_zeroed_and_the_row_loses_its_repair_marks(self):
        d = tempfile.mkdtemp()
        state = Path(d)
        # the ledger as the first run left it: the 303 row zeroed (usdRecorded 303), the 320 row corrected to 17
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 3.5) | {"usdRecorded": 300.0, "repairedT": 1},
                 row(A, "web", at(10, 0), 5.0), row(A, "web", at(10, 40), 0.0) | {"usdRecorded": 303.0, "repairedT": 1},
                 row(A, "web", at(11, 10), 17.0) | {"usdRecorded": 320.0, "repairedT": 1}]
        restarts = [at(9, 30), at(10, 30), at(11, 0)]
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + "\n" for t in restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 5.0, "turns": 2, "bySid": {A: {"usd": 5.0, "turns": 2}}}},
                 "days": {DAY: {"usd": 27.5, "turns": 5, "bySid": {A: {"usd": 27.5}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        p = rp.plan(turns, restarts, DAY)
        self.assertEqual([(c["t"], c["current"], c["corrected"], c.get("restore")) for c in p["rows"]],
                         [(at(9, 40), 3.5, 300.0, True), (at(10, 40), 0.0, 303.0, True)],
                         "the zeroed turn comes back, and so does the 300 the first run took for the first cumulative: the staircase that follows descends from 303, not from it; the 320 step stands as corrected")
        self.assertIn("restored: 300.0000 is a lone first result after a restart with no staircase following it, a turn", p["rows"][0]["reason"])
        self.assertIn("restored: 303.0000 is below the previous cumulative 300.0000 plus 1 row(s) between (5.0000), a turn of a fresh process", p["rows"][1]["reason"])
        self.assertEqual(p["hours"]["%sT10" % DAY], {"before": 5.0, "after": 308.0})
        self.assertEqual(p["days"][DAY], {"before": 27.5, "after": 627.0})
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply"]), 0)
        self.assertIn("0 cumulative row(s) found, 2 earlier correction(s) to restore", out.getvalue())
        self.assertIn("applied: 0 turn row(s) corrected (usdRecorded keeps the old figure), 2 restored to the kernel's figure, then spend.json rewritten for those", out.getvalue())
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual(after["hours"]["%sT10" % DAY]["usd"], 308.0)
        self.assertEqual(after["days"][DAY]["bySid"][A]["usd"], 627.0)
        rows = {r["t"]: r for r in (json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines())}
        self.assertEqual((rows[at(10, 40)]["usd"], rows[at(9, 40)]["usd"]), (303.0, 300.0))
        self.assertNotIn("usdRecorded", rows[at(10, 40)], "a restored row is the kernel's row again")
        self.assertNotIn("repairedT", rows[at(10, 40)])
        self.assertNotIn("usdRecorded", rows[at(9, 40)])
        self.assertEqual((rows[at(11, 10)]["usd"], rows[at(11, 10)]["usdRecorded"]), (17.0, 320.0), "a standing correction keeps its marks")
        again = rp.plan([json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()], restarts, DAY)
        self.assertEqual(again["rows"], [], "and a third run finds nothing")

    def test_apply_folds_the_deltas_into_the_buckets_and_the_rows_and_a_dry_run_writes_nothing(self):
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + "\n" for t in self.restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 520.0, "turns": 6, "bySid": {A: {"usd": 516.0, "turns": 4}, B: {"usd": 4.0, "turns": 2}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "key": {"usd": 515.0}, "bySid": {A: {"usd": 515.0, "turns": 1, "key": {"usd": 515.0}}}}},
                 "days": {DAY: {"usd": 1035.0, "turns": 7, "bySid": {A: {"usd": 1031.0}, B: {"usd": 4.0}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        reg(state, A, name="web", apiKeyAuth=True)          # web bills an API key: its key split follows
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d]), 0)
        self.assertIn("dry run: nothing written", out.getvalue())
        self.assertIn("web", out.getvalue()); self.assertIn("507.00 ->     3.50", out.getvalue())
        self.assertEqual(json.loads((state / "spend.json").read_text()), spend, "a dry run leaves spend.json as it was")
        self.assertEqual(len((state / "turns.jsonl").read_text().splitlines()), 7)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply"]), 0)
        self.assertIn("applied: 2 turn row(s) corrected (usdRecorded keeps the old figure), then spend.json rewritten for those", out.getvalue())
        baks = sorted(state.glob("spend.json.bak-*")) + sorted(state.glob("turns.jsonl.bak-*"))
        self.assertEqual(len(baks), 2, "the copies beside the files, in the tool")
        self.assertEqual(json.loads(baks[0].read_text()), spend, "the copy is the ledger before the write")
        self.assertEqual(len(baks[1].read_text().splitlines()), 7)
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual(after["hours"]["%sT10" % DAY]["usd"], 16.5)
        self.assertEqual(after["hours"]["%sT10" % DAY]["bySid"][A]["usd"], 12.5)
        self.assertEqual(after["hours"]["%sT10" % DAY]["bySid"][B]["usd"], 4.0, "api untouched")
        self.assertEqual(after["hours"]["%sT10" % DAY]["turns"], 6, "turns and tokens stay")
        self.assertEqual(after["hours"]["%sT11" % DAY]["usd"], 6.0)
        self.assertEqual(after["hours"]["%sT11" % DAY]["key"]["usd"], 6.0, "the keyed split follows")
        self.assertEqual(after["hours"]["%sT11" % DAY]["bySid"][A]["key"]["usd"], 6.0)
        self.assertEqual(after["days"][DAY]["usd"], 22.5)
        self.assertEqual(after["days"][DAY]["bySid"][A]["usd"], 18.5)
        rows = [json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()]
        fixed = [r for r in rows if "usdRecorded" in r]
        self.assertEqual([(r["usdRecorded"], r["usd"]) for r in fixed], [(507.0, 3.5), (515.0, 6.0)])
        self.assertEqual(len(rows), 7, "no row lost")

    def test_a_second_run_over_repaired_rows_finds_nothing_and_a_fixed_kernels_rows_are_never_steps(self):
        p = rp.plan(self.turns, self.restarts, DAY)
        repaired = []
        fixes = {(c["sid"], c["t"]): c["corrected"] for c in p["rows"]}
        for r in self.turns:
            r = dict(r)
            if (r["sid"], r["t"]) in fixes:
                r["usdRecorded"], r["usd"] = r["usd"], fixes[(r["sid"], r["t"])]
            repaired.append(r)
        again = rp.plan(repaired, self.restarts, DAY)
        self.assertEqual(again["rows"], [], "idempotent: the repaired rows are never steps again")
        self.assertEqual(again["days"][DAY]["before"], again["days"][DAY]["after"])
        # rows the fixed kernel writes carry the CLI's cumulative: a big first result after a restart with one is a turn
        fixed_kernel = self.turns + [row(A, "web", at(12, 10), 480.0) | {"cumulativeUsd": 995.0, "spendBaseline": "seeded"}]
        p2 = rp.plan(fixed_kernel, self.restarts + [at(12, 0)], DAY)
        self.assertNotIn(at(12, 10), {c["t"] for c in p2["rows"]}, "a row that names its cumulative is not a staircase step")

    def test_rows_before_the_hosts_start_are_never_steps_and_an_earlier_correction_there_is_restored(self):
        # web: a long first turn at 9:40 after the 9:30 restart (300, a plain child before the hosts came on at 10:00),
        # a fresh process's modest first turn after the 10:30 restart (6), and that process's lifetime after the 11:00
        # restart (26 = 6 + this turn's 20). Without the bound 300 would read as the day's first cumulative
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 300.0), row(A, "web", at(10, 40), 6.0),
                 row(A, "web", at(11, 10), 26.0)]
        restarts = [at(9, 30), at(10, 30), at(11, 0)]
        p = rp.plan(turns, restarts, DAY, since=at(10, 0))
        got = {c["t"]: (c["recorded"], c["corrected"]) for c in p["rows"]}
        self.assertEqual(sorted(got), [at(11, 10)], "before the hosts' start a first result is the turn it says; 6 is a fresh process; 26 is its lifetime")
        self.assertEqual(got[at(11, 10)], (26.0, 20.0))
        self.assertEqual({c["t"] for c in rp.plan(turns, restarts, DAY)["rows"]}, {at(11, 10)},
                         "without the bound the 300 stands too here, since the staircase that follows descends from the fresh 6 (M2); the bound's own work shows on the repaired ledger below")
        # an earlier run without the bound zeroed the 9:40 row (a 300 lifetime, it thought): the bound restores it
        repaired = [turns[0], turns[1] | {"usd": 2.0, "usdRecorded": 300.0, "repairedT": 1}, turns[2], turns[3] | {"usd": 20.0, "usdRecorded": 26.0, "repairedT": 1}]
        p2 = rp.plan(repaired, restarts, DAY, since=at(10, 0))
        self.assertEqual([(c["t"], c["current"], c["corrected"], c.get("restore")) for c in p2["rows"]], [(at(9, 40), 2.0, 300.0, True)])
        self.assertIn("restored: 300.0000 precedes the hosts' start (2026-09-11 10:00:00), a fresh process's turn", p2["rows"][0]["reason"])
        self.assertIn("rows before 2026-09-11 10:00:00 (the hosts' start, --since) are fresh processes' turns, never steps", rp.report(p2))
        self.assertEqual(rp.parse_since("2026-09-11T10:00:00"), at(10, 0))
        self.assertEqual(rp.parse_since(str(at(10, 0))), float(at(10, 0)))
        self.assertEqual(rp.parse_since(""), None)
        with self.assertRaises(ValueError):
            rp.parse_since("yesterday-ish")

    def test_a_threads_correction_reaches_its_owners_bysid_and_an_unkeyed_session_leaves_the_key_split_alone(self):
        d = tempfile.mkdtemp()
        state = Path(d)
        # thread T of web: an ordinary turn, a restart, its lifetime; the kernel billed the thread's turns to web's bySid
        turns = [row(T, "web", at(10, 0), 3.0), row(T, "web", at(10, 40), 507.0), row(T, "web", at(10, 50), 2.0), row(T, "web", at(11, 10), 515.0)]
        restarts = [at(10, 30), at(11, 0)]
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + "\n" for t in restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 512.0, "turns": 3, "key": {"usd": 512.0}, "bySid": {A: {"usd": 512.0, "turns": 3, "key": {"usd": 512.0}}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "key": {"usd": 515.0}, "bySid": {A: {"usd": 515.0, "turns": 1, "key": {"usd": 515.0}}}}},
                 "days": {DAY: {"usd": 1027.0, "turns": 4, "key": {"usd": 1027.0}, "bySid": {A: {"usd": 1027.0, "key": {"usd": 1027.0}}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        reg(state, A, name="web")                           # web: a login session, no key
        reg(state, T, name="web", threadOf=A)               # T bills web
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual(after["hours"]["%sT10" % DAY]["usd"], 8.0, "507 -> the typical 3.0 (the one row before it): 3 + 3 + 2")
        self.assertEqual(after["hours"]["%sT10" % DAY]["bySid"][A]["usd"], 8.0, "the thread's correction reached its owner's row")
        self.assertEqual(after["hours"]["%sT11" % DAY]["bySid"][A]["usd"], 6.0)
        self.assertEqual(after["days"][DAY]["bySid"][A]["usd"], 14.0)
        self.assertEqual(after["hours"]["%sT10" % DAY]["key"]["usd"], 512.0, "no apiKeyAuth on record: the key split stands as recorded")
        self.assertEqual(after["days"][DAY]["bySid"][A]["key"]["usd"], 1027.0)
        self.assertIn("2 corrected row(s) belong to sessions the registry does not mark as API-key billed: their buckets' key split is left as recorded", out.getvalue())
        self.assertNotIn("no bySid entry", out.getvalue())
        self.assertEqual(sorted(state.glob("*.bak-*")), [], "--no-backup")

    def test_a_fixed_kernels_first_result_rows_are_never_steps_and_a_missed_instant_is_tolerated_on_a_shown_chain(self):
        # web: 3, restart, 507 (first cumulative), 2, restart, 515 (a step), then at 12:10 a row at 530 with NO restart
        # instant on record (a crash leaves no audit row): 530 >= 515 + 0 on a chain already shown, a step of 15
        turns = self.turns[:5] + [row(A, "web", at(12, 10), 530.0)]
        p = rp.plan(turns, self.restarts, DAY)
        got = {c["t"]: (c["corrected"], c["reason"]) for c in p["rows"]}
        self.assertEqual(got[at(12, 10)][0], 15.0)
        self.assertIn("no restart instant on record, the staircase's signature alone", got[at(12, 10)][1])
        # a session with no chain shown does not take the signature alone: api's honest 40 at 10:50 (no restart between
        # its 10:45 row and it) after 1.5 and a fresh 2.5 stands
        turns2 = self.turns + [row(B, "api", at(10, 50), 40.0)]
        self.assertNotIn(at(10, 50), {c["t"] for c in rp.plan(turns2, self.restarts, DAY)["rows"]})
        # the fixed kernel's rows: a first result naming its baseline is right as written, whatever its size
        fixed = self.turns + [row(A, "web", at(12, 10), 480.0) | {"spendBaseline": "seeded"}]
        self.assertNotIn(at(12, 10), {c["t"] for c in rp.plan(fixed, self.restarts + [at(12, 0)], DAY)["rows"]})

    def test_a_ledger_that_moved_between_the_plan_and_the_write_is_folded_as_it_stands(self):
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + "\n" for t in self.restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 520.0, "turns": 6, "bySid": {A: {"usd": 516.0}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "bySid": {A: {"usd": 515.0}}}},
                 "days": {DAY: {"usd": 1035.0, "turns": 7, "bySid": {A: {"usd": 1031.0}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        # the kernel folds a $9 result into hour 11 between the plan's read and the write: apply_to_spend is called
        # once for the plan (the notes) and again on the fresh text; the write carries the $9
        real = rp.read_text
        calls = []
        def read_text(path):
            calls.append(path.name)
            if path.name == "spend.json" and calls.count("spend.json") == 2:
                moved = json.loads(json.dumps(spend))
                moved["hours"]["%sT11" % DAY]["usd"] = 524.0; moved["days"][DAY]["usd"] = 1044.0
                path.write_text(json.dumps(moved))
            return real(path)
        rp.read_text = read_text
        try:
            import io, contextlib
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        finally:
            rp.read_text = real
        self.assertIn("spend.json moved since the plan's read", out.getvalue())
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual(after["hours"]["%sT11" % DAY]["usd"], 15.0, "524 less the 509 correction: the $9 result kept")
        self.assertEqual(after["days"][DAY]["usd"], 31.5)

    def test_an_unparseable_ledger_is_refused_and_a_torn_turn_line_is_counted(self):
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.turns) + '{"t": 17, "sid": "torn' + "\n")
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + "\n" for t in self.restarts))
        (state / "spend.json").write_text('{"hours": {"x": ')       # a torn write
        import io, contextlib
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 2, "M3: refused")
        self.assertIn("does not parse", err.getvalue()); self.assertIn("Nothing written", err.getvalue())
        self.assertEqual((state / "spend.json").read_text(), '{"hours": {"x": ', "the ledger is not touched")
        self.assertEqual(len((state / "turns.jsonl").read_text().splitlines()), 8, "nor the rows")
        (state / "spend.json").write_text("{}")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d]), 0)
        self.assertIn("1 unparseable line(s) in turns.jsonl skipped (a torn append)", out.getvalue(), "low b")

    def test_a_second_run_leaves_the_first_cumulatives_correction_where_a_no_instant_step_follows(self):
        # low a: the typical turn is the median over the kernel's figure of the rows that are not steps, so a corrected
        # no-instant step does not move it between runs
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 10), 3.0), row(A, "web", at(9, 40), 300.0),
                 row(A, "web", at(10, 0), 4.0), row(A, "web", at(10, 30), 310.0),      # no instant: 310 >= 300 + 4 on a shown chain
                 row(A, "web", at(11, 10), 330.0)]
        restarts = [at(9, 30), at(11, 0)]
        p = rp.plan(turns, restarts, DAY)
        got = {c["t"]: c["corrected"] for c in p["rows"]}
        self.assertEqual(got, {at(9, 40): 2.5, at(10, 30): 6.0, at(11, 10): 20.0}, "the typical 2.5: the median of the rows before it, 2 and 3")
        repaired = [dict(r) for r in turns]
        for r in repaired:
            if r["t"] in got:
                r["usdRecorded"], r["usd"] = r["usd"], got[r["t"]]
        self.assertEqual(rp.plan(repaired, restarts, DAY)["rows"], [], "a second run moves nothing")

    def test_a_standing_first_cumulative_correction_is_kept_as_the_day_grows(self):
        # low C of round three: the correction median grew with the day, and a later atypical row rewrote a standing
        # correction on a re-run; a new correction takes the median of the session's rows before it, a standing one is kept
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 10), 3.0), row(A, "web", at(9, 40), 300.0),
                 row(A, "web", at(10, 0), 4.0), row(A, "web", at(10, 40), 320.0)]
        restarts = [at(9, 30), at(10, 30)]
        p = rp.plan(turns, restarts, DAY)
        got = {c["t"]: c["corrected"] for c in p["rows"]}
        self.assertEqual(got, {at(9, 40): 2.5, at(10, 40): 16.0}, "the median of the rows before it, 2 and 3")
        repaired = [dict(r) for r in turns]
        for r in repaired:
            if r["t"] in got:
                r["usdRecorded"], r["usd"], r["repairRule"] = r["usd"], got[r["t"]], rp.REPAIR_RULE
        later = repaired + [row(A, "web", at(11, 0), 40.0)]          # an atypical ordinary turn later in the day
        self.assertEqual(rp.plan(later, restarts, DAY)["rows"], [], "a re-run moves nothing: the standing 2.5 this rule wrote is kept")
        # the round-four HIGH: a figure an EARLIER rule left on the first cumulative (a parked request's phantom baseline
        # made 500 -> 497 'the turn') is not a plausible typical turn and carries no rule stamp: judged again
        frozen = [dict(r) for r in turns]
        frozen[2] = dict(frozen[2], usd=297.0, usdRecorded=300.0, repairedT=1)     # 300 - a phantom 3: 'the turn', frozen
        frozen[4] = dict(frozen[4], usd=16.0, usdRecorded=320.0, repairedT=1, repairRule=rp.REPAIR_RULE)
        again = {c["t"]: (c["current"], c["corrected"]) for c in rp.plan(frozen, restarts, DAY)["rows"]}
        self.assertEqual(again, {at(9, 40): (297.0, 2.5)}, "the frozen 297 is re-judged to the typical 2.5; the stamped 16 stands")
        # even with the stamp, a standing figure that is no plausible turn is judged again
        frozen[2]["repairRule"] = rp.REPAIR_RULE
        self.assertEqual({c["t"]: c["corrected"] for c in rp.plan(frozen, restarts, DAY)["rows"]}, {at(9, 40): 2.5})

    def test_a_failure_between_the_two_writes_is_recovered_from_the_journal_and_a_folded_entry_is_never_refolded(self):
        # low D of round three, reshaped by round four: the fold's completion is recorded INSIDE spend.json in the same
        # atomic write (repairJournal.folded), so a death between the two writes, or a restore of spend.json from a
        # copy, leaves exactly the unfolded entries pending; a delta is folded only while its row holds the figure recorded
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in self.turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + chr(10) for t in self.restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 520.0, "turns": 6, "bySid": {A: {"usd": 516.0}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "bySid": {A: {"usd": 515.0}}}},
                 "days": {DAY: {"usd": 1035.0, "turns": 7, "bySid": {A: {"usd": 1031.0}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        self.assertEqual((state / rp.REPAIR_JOURNAL).read_text().splitlines(), [], "a clean apply leaves the journal compacted: folded entries and their marks gone")
        folded = json.loads((state / "spend.json").read_text())
        self.assertEqual(len(folded["repairJournal"]["folded"]), 1, "the completion rides the same write")
        ref = folded["repairJournal"]["folded"][0]
        journal = [{"t": ref, "phase": "rows", "day": DAY, "deltas": [
            {"sid": A, "t": at(10, 40), "hour": "%sT10" % DAY, "owner": A, "keyed": False, "name": "web", "delta": -503.5, "corrected": 3.5},
            {"sid": A, "t": at(11, 10), "hour": "%sT11" % DAY, "owner": A, "keyed": False, "name": "web", "delta": -509.0, "corrected": 6.0}]}]
        # the death between the writes: the ledger as before the fold (no mark), the journal with its rows entry
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps(journal[0]) + chr(10))
        (state / "spend.json").write_text(json.dumps(spend))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d]), 0)
        self.assertIn("2 delta(s) from 1 earlier run(s) are journaled with their bucket write incomplete: --apply folds them first", out.getvalue())
        self.assertEqual(json.loads((state / "spend.json").read_text()), spend, "a dry run still writes nothing")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        self.assertIn("2 delta(s) from 1 earlier run(s) whose bucket write did not complete were folded now", out.getvalue())
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual((after["hours"]["%sT10" % DAY]["usd"], after["hours"]["%sT11" % DAY]["usd"], after["days"][DAY]["usd"]), (16.5, 6.0, 22.5))
        self.assertEqual(after["repairJournal"]["folded"], [journal[0]["t"]])
        # a restore of spend.json from a copy that already carried the mark, then a re-run: nothing is folded again (the
        # journal entry is put back too: the ledger's ref alone decides)
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps(journal[0]) + chr(10))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        self.assertIn("nothing to apply", out.getvalue())
        self.assertEqual(json.loads((state / "spend.json").read_text()), after, "a folded entry is never re-folded")
        # a delta whose row moved since the entry was written folds against the row's PRESENT figure, and the plan's own
        # correction of that row folds on top: the buckets end where the rows say
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps(journal[0]) + chr(10))
        (state / "spend.json").write_text(json.dumps(spend))
        rows = [json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()]
        for r in rows:
            if r["t"] == at(11, 10):
                r["usd"] = 5.5                         # edited by hand since the entry was written (the plan re-judges it to 6.0)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in rows))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        self.assertIn("2 delta(s) from 1 earlier run(s) whose bucket write did not complete were folded now; 1 against the row's present figure, which moved since", out.getvalue())
        got = json.loads((state / "spend.json").read_text())["hours"]
        self.assertEqual((got["%sT10" % DAY]["usd"], got["%sT11" % DAY]["usd"]), (16.5, 6.0), "515 -> 5.5 by the journal, then 5.5 -> 6.0 by the plan: the rows' truth")
        # an entry the earlier rule marked folded in the journal itself is not pending either
        (state / "spend.json").write_text(json.dumps(spend))
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps(journal[0]) + chr(10) + json.dumps({"t": 5, "phase": "buckets", "ref": journal[0]["t"]}) + chr(10))
        self.assertEqual(rp.journal_pending(state, spend, []), [], "the journal's own mark from the earlier rule is honoured")
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps(journal[0]) + chr(10))
        self.assertEqual(len(rp.journal_pending(state, spend, [])), 1)
        (state / "spend.json").write_text(json.dumps(after))
        # a torn journal line: the dry run says so and --apply is refused
        with open(state / rp.REPAIR_JOURNAL, "a") as f:
            f.write('{"t": 99, "phase": "rows", "deltas": [' + chr(10))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 2)
        self.assertIn("1 unparseable line(s) in", err.getvalue()); self.assertIn("--apply is refused until the journal is whole", err.getvalue())

    def test_the_recovery_folds_clamp_is_said_the_folded_set_is_unbounded_and_the_journal_entry_precedes_the_rows_replace(self):
        # round five of the review: (1) a clamp INSIDE the recovery fold was silent (fold_deltas discarded its notes);
        # (2) the folded set kept 500 refs while the journal kept every entry, so the 501st run re-folded the oldest;
        # (3) a death between the rows replace and the journal append left nothing for a later run to fold
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in self.turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + chr(10) for t in self.restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 520.0, "turns": 6, "bySid": {A: {"usd": 516.0}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "bySid": {A: {"usd": 515.0}}}},
                 "days": {DAY: {"usd": 1035.0, "turns": 7, "bySid": {A: {"usd": 1031.0}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        import io, contextlib
        # (3): the journal entry is written before the rows replace, so a death between them leaves the deltas journaled
        real_apply = rp.apply_to_turns
        calls = []
        def dying(path, p, write=True):
            calls.append(write)
            if write:
                raise OSError("disk gone between the writes")
            return real_apply(path, p, write=False)
        rp.apply_to_turns = dying
        try:
            with self.assertRaises(OSError):
                rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"])
        finally:
            rp.apply_to_turns = real_apply
        self.assertEqual(calls, [False, True], "the dry match first, then the write")
        journal = [json.loads(l) for l in (state / rp.REPAIR_JOURNAL).read_text().splitlines()]
        self.assertEqual([j["phase"] for j in journal], ["rows"], "journaled ahead of the rewrite that never happened")
        self.assertEqual(json.loads((state / "spend.json").read_text()), spend, "nothing folded")
        self.assertEqual(len([r for r in (json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()) if "usdRecorded" in r]), 0, "no row rewritten")
        # the next run: the entry is pending, its rows still hold the former figures (present less former = 0), the plan
        # corrects the rows itself; the buckets end on the truth, once
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual((after["hours"]["%sT10" % DAY]["usd"], after["hours"]["%sT11" % DAY]["usd"], after["days"][DAY]["usd"]), (16.5, 6.0, 22.5))
        self.assertIn("2 delta(s) from 1 earlier run(s) whose bucket write did not complete were folded now; 2 against the row's present figure", out.getvalue())
        self.assertEqual(len(after["repairJournal"]["folded"]), 2, "both entries marked folded inside the ledger")
        # (1): a recovery fold that would take a bucket below zero says so (the ledger restored from an older, smaller copy,
        # its journal entry with it: the apply above compacted the journal)
        small = {"hours": {"%sT10" % DAY: {"usd": 1.0, "turns": 6, "bySid": {A: {"usd": 1.0}}}, "%sT11" % DAY: {"usd": 1.0, "turns": 1, "bySid": {A: {"usd": 1.0}}}},
                 "days": {DAY: {"usd": 2.0, "turns": 7, "bySid": {A: {"usd": 2.0}}}}}
        (state / "spend.json").write_text(json.dumps(small))
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps({"t": 77.0, "phase": "rows", "day": DAY, "deltas": [
            {"sid": A, "t": at(10, 40), "hour": "%sT10" % DAY, "owner": A, "keyed": False, "name": "web", "delta": -503.5, "corrected": 3.5},
            {"sid": A, "t": at(11, 10), "hour": "%sT11" % DAY, "owner": A, "keyed": False, "name": "web", "delta": -509.0, "corrected": 6.0}]}) + chr(10))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        self.assertIn("note (recovery fold): hour %sT10 would go" % DAY, out.getvalue(), "the clamp inside the recovery is said")
        self.assertIn("below zero; held at zero", out.getvalue())
        self.assertEqual(json.loads((state / "spend.json").read_text())["hours"]["%sT10" % DAY]["usd"], 0.0)
        # (2): the folded set is unbounded
        many = rp.mark_folded({"repairJournal": {"folded": list(range(600))}}, [600.5])
        self.assertEqual(len(many["repairJournal"]["folded"]), 601)
        src = inspect.getsource(rp.main)
        self.assertLess(src.index('journal_append(state, {"t": stamp_t, "phase": "rows"'), src.index('done = apply_to_turns(state / "turns.jsonl", p)'),
                        "the journal entry precedes the rows replace")

    def test_round_six_the_plan_folds_clamp_is_said_after_a_recovery_the_dry_run_previews_the_recovery_and_the_journal_is_compacted(self):
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in self.turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + chr(10) for t in self.restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 520.0, "turns": 6, "bySid": {A: {"usd": 516.0}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "bySid": {A: {"usd": 515.0}}}},
                 "days": {DAY: {"usd": 1035.0, "turns": 7, "bySid": {A: {"usd": 1031.0}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        journal = (state / rp.REPAIR_JOURNAL).read_text().splitlines()
        self.assertEqual(journal, [], "compacted: the folded entry and its belt mark left the journal")
        # the ledger restored to before the fold with the journal's entry put back: the recovery is pending, and the plan
        # (the rows already corrected: nothing of its own) folds on the recovered base. A row hand-edited to a figure
        # that makes the plan fold clamp shows the clamp said even though spend.json's text did not move
        folded_ref = json.loads((state / "spend.json").read_text())["repairJournal"]["folded"][0]
        entry = {"t": folded_ref, "phase": "rows", "day": DAY, "deltas": [
            {"sid": A, "t": at(10, 40), "hour": "%sT10" % DAY, "owner": A, "keyed": False, "name": "web", "delta": -503.5, "corrected": 3.5},
            {"sid": A, "t": at(11, 10), "hour": "%sT11" % DAY, "owner": A, "keyed": False, "name": "web", "delta": -509.0, "corrected": 6.0}]}
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps(entry) + chr(10))
        (state / "spend.json").write_text(json.dumps(spend))
        rows = [json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()]
        for r in rows:
            if r["t"] == at(11, 10):
                r["usd"] = 0.5                          # hand-edited below the plan's 6.0: the plan re-corrects (+5.5) on a base the
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in rows))   # recovery already moved
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d]), 0)
        self.assertIn("recovery fold would move hour %sT10: 520.00 -> 16.50" % DAY, out.getvalue(), "the dry run previews the pending recovery")
        self.assertIn("recovery fold would move hour %sT11: 515.00 -> 0.50" % DAY, out.getvalue())
        self.assertEqual(json.loads((state / "spend.json").read_text()), spend, "the preview writes nothing")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        got = json.loads((state / "spend.json").read_text())["hours"]
        self.assertEqual((got["%sT10" % DAY]["usd"], got["%sT11" % DAY]["usd"]), (16.5, 6.0), "the recovery to the rows' figures, then the plan's +5.5")
        self.assertEqual((state / rp.REPAIR_JOURNAL).read_text().splitlines(), [], "compacted again")
        # a recovery whose base is smaller than the deltas: the recovery fold's clamp is previewed and, on --apply, said
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps(entry) + chr(10))
        small = {"hours": {"%sT10" % DAY: {"usd": 1.0, "turns": 6, "bySid": {A: {"usd": 1.0}}}, "%sT11" % DAY: {"usd": 1.0, "turns": 1, "bySid": {A: {"usd": 1.0}}}},
                 "days": {DAY: {"usd": 2.0, "turns": 7, "bySid": {A: {"usd": 2.0}}}}}
        (state / "spend.json").write_text(json.dumps(small))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d]), 0)
        self.assertIn("recovery fold would say: hour %sT10 would go" % DAY, out.getvalue(), "the dry run shows the recovery's clamp")
        # the rebuild tool carries the folded refs through
        import importlib.util
        spec = importlib.util.spec_from_loader("romp_spend_rebuild_test", importlib.machinery.SourceFileLoader("romp_spend_rebuild_test", os.path.join(ROOT, "bin", "romp-spend-rebuild")))
        rb = importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)
        new, _c, _k = rb.rebuild({"days": {}, "hours": {}, "repairJournal": {"folded": [1.5]}}, {}, {})
        self.assertEqual(new.get("repairJournal"), {"folded": [1.5]}, "a rebuild keeps every top-level key it does not recount")

    def test_a_plan_clamp_that_arises_only_on_the_recovered_ledger_is_previewed_and_said(self):
        # round six's MEDIUM, with a scenario that produces the clamp: hour 11 holds the 515 row (corrected to 6.0 by an
        # earlier run whose bucket write never completed: pending, -509) and a fresh re-bill of 530 the plan corrects
        # to 15 (-515). On the ledger as read (535) the plan's fold has no clamp, so the report says none; on the
        # recovered base (26) it clamps 489 below zero. The dry run previews it; --apply says it
        d = tempfile.mkdtemp()
        state = Path(d)
        turns = [self.turns[0], self.turns[1], self.turns[2] | {"usd": 3.5, "usdRecorded": 507.0, "repairedT": 1, "repairRule": rp.REPAIR_RULE},
                 self.turns[3], row(A, "web", at(11, 10), 6.0) | {"usdRecorded": 515.0, "repairedT": 1, "repairRule": rp.REPAIR_RULE},
                 row(A, "web", at(11, 20), 530.0)]          # hour 10 already corrected (3 + 4 + 3.5 + 2 = 12.5); hour 11 holds 6 and the fresh 530
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + chr(10) for t in self.restarts + [at(11, 15)]))
        spend = {"hours": {"%sT10" % DAY: {"usd": 12.5, "turns": 4, "bySid": {A: {"usd": 12.5}}},
                           "%sT11" % DAY: {"usd": 535.0, "turns": 2, "bySid": {A: {"usd": 535.0}}}},
                 "days": {DAY: {"usd": 547.5, "turns": 6, "bySid": {A: {"usd": 547.5}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        (state / rp.REPAIR_JOURNAL).write_text(json.dumps({"t": 77.0, "phase": "rows", "day": DAY, "deltas": [
            {"sid": A, "t": at(11, 10), "hour": "%sT11" % DAY, "owner": A, "keyed": False, "name": "web", "delta": -509.0, "corrected": 6.0}]}) + chr(10))
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d]), 0)
        self.assertIn("recovery fold would move hour %sT11: 535.00 -> 26.00" % DAY, out.getvalue())
        self.assertIn("plan fold on the recovered ledger would say: hour %sT11 would go 489.0000 below zero; held at zero" % DAY, out.getvalue(), "previewed")
        self.assertIn("plan fold on the recovered ledger would move hour %sT11: 26.00 -> 0.00" % DAY, out.getvalue())
        self.assertNotIn("note: hour", out.getvalue(), "the report's own fold, on 535, has no clamp")
        self.assertEqual(json.loads((state / "spend.json").read_text()), spend, "the dry run writes nothing")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        self.assertIn("note: hour %sT11 would go 489.0000 below zero; held at zero" % DAY, out.getvalue(), "said on --apply as a new line")
        self.assertEqual(json.loads((state / "spend.json").read_text())["hours"]["%sT11" % DAY]["usd"], 0.0)

    def test_a_lifetime_billed_once_more_after_an_attach_unknown_row_is_corrected_and_a_post_clear_turn_is_not(self):
        # the fix's first boot (2026-09-11 22:38Z): the replayed attach-unknown first result folded nothing but left the
        # watermark at zero, and the next live result recorded the whole cumulative. The 1473 review's HIGH: the first
        # paid turn after a mid-life /clear is written with its dollars equal to its cumulative BY DESIGN (the module's
        # oracle: a post-clear result 5.00 with costState 5.00), so the match needs the cumulative ABOVE the
        # attach-unknown row's and a positive remainder, and the rule stands down with a note otherwise
        turns = [row(A, "web", at(10, 0), 3.0) | {"cumulativeUsd": 100.0},
                 row(A, "web", at(10, 40), 0.0) | {"cumulativeUsd": 148.4878, "spendBaseline": "attach-unknown", "redelivered": True},
                 row(A, "web", at(10, 50), 159.2004) | {"cumulativeUsd": 159.2004},         # usd equals its cumulative: the lifetime once more
                 row(A, "web", at(11, 0), 2.0) | {"cumulativeUsd": 161.2004},               # an ordinary fixed-kernel row
                 row(A, "web", at(11, 10), 4.5) | {"cumulativeUsd": 4.5, "spendBaseline": "fresh"},   # a fresh process's first result: its own
                 row(A, "web", at(11, 20), 20.0) | {"cumulativeUsd": 24.5}]
        p = rp.plan(turns, [at(10, 30)], DAY)
        got = {c["t"]: c for c in p["rows"]}
        self.assertEqual(sorted(got), [at(10, 50)], "the lifetime row alone; the fresh first result and the ordinary rows stand")
        c = got[at(10, 50)]
        self.assertEqual((c["current"], c["corrected"], c["recorded"], c["rule"]), (159.2004, round(159.2004 - 148.4878, 6), 159.2004, rp.REPAIR_RULE_LIFETIME))
        self.assertIn("the lifetime billed once more after the attach-unknown row", c["reason"])
        self.assertEqual(p["stoodDown"], [])
        # a lifetime row with ordinary rows between it and the attach-unknown row subtracts them too
        turns2 = [turns[1], row(A, "web", at(10, 45), 2.0) | {"cumulativeUsd": 150.4878}, turns[2] | {"usd": 161.2004, "cumulativeUsd": 161.2004}]
        got2 = {c["t"]: c["corrected"] for c in rp.plan(turns2, [at(10, 30)], DAY)["rows"]}
        self.assertEqual(got2, {at(10, 50): round(161.2004 - 148.4878 - 2.0, 6)})
        # the /clear shape: the post-clear first paid turn follows the attach-unknown row with its cumulative BELOW it
        turns3 = [turns[1], row(A, "web", at(10, 45), 2.0) | {"cumulativeUsd": 150.4878},
                  row(A, "web", at(10, 50), 4.25) | {"cumulativeUsd": 4.25},                # /clear, then its first paid turn
                  row(A, "web", at(10, 55), 1.0) | {"cumulativeUsd": 5.25}]
        p3 = rp.plan(turns3, [at(10, 30)], DAY)
        self.assertEqual(p3["rows"], [], "the post-clear turn stands (the first cut zeroed it to max(0, 4.25 - 148.49 - 2))")
        self.assertEqual(len(p3["stoodDown"]), 1)
        self.assertIn("4.2500 is at or below the previous row's 150.4878: a counter reset", p3["stoodDown"][0])
        self.assertIn("web 10:50:00: the lifetime rule stood down", p3["stoodDown"][0])
        self.assertIn("note: web 10:50:00: the lifetime rule stood down", rp.report(p3), "said in the report")
        # the guard is against the PREVIOUS row (the kernel's reset comparison), not the attach-unknown row: a cumulative
        # above the attach-unknown row's but below the previous row's is a reset too, and stands
        turns4 = [turns[1], row(A, "web", at(10, 45), 20.0) | {"cumulativeUsd": 168.4878},
                  row(A, "web", at(10, 50), 160.0) | {"cumulativeUsd": 160.0}]
        p4 = rp.plan(turns4, [at(10, 30)], DAY)
        self.assertEqual(p4["rows"], [])
        self.assertIn("160.0000 is at or below the previous row's 168.4878: a counter reset", p4["stoodDown"][0])
        # the unknown window can replay several records, each a row with usd 0 and a rising cumulative and no baseline
        # (the review's third round): the previous row's cumulative is the baseline, never the first replayed one
        turns6 = [turns[1], row(A, "web", at(10, 45), 0.0) | {"cumulativeUsd": 160.0, "redelivered": True},
                  row(A, "web", at(10, 50), 170.0) | {"cumulativeUsd": 170.0}]
        got6 = {c["t"]: (c["corrected"], c["reason"]) for c in rp.plan(turns6, [at(10, 30)], DAY)["rows"]}
        self.assertEqual(sorted(got6), [at(10, 50)])
        self.assertEqual(got6[at(10, 50)][0], 10.0, "the turn's own cost: 170 less the replay row's 160, not less the first replay's 148.49")
        self.assertIn("less the previous row's cumulative 160.0000", got6[at(10, 50)][1])
        # the multi-replay window, a live turn, then a post-clear first paid turn: it stands (the first cut reduced it to
        # 3.50 less 0.80 less 1.00 with no note, since 3.50 is above the attach-unknown row's 0.80)
        turns7 = [row(A, "web", at(10, 40), 0.0) | {"cumulativeUsd": 0.8, "spendBaseline": "attach-unknown", "redelivered": True},
                  row(A, "web", at(10, 42), 0.0) | {"cumulativeUsd": 3.0, "redelivered": True},
                  row(A, "web", at(10, 45), 1.0) | {"cumulativeUsd": 4.0},
                  row(A, "web", at(10, 50), 3.5) | {"cumulativeUsd": 3.5}]
        p7 = rp.plan(turns7, [at(10, 30)], DAY)
        self.assertEqual(p7["rows"], [], "a genuine post-clear turn is never reduced")
        self.assertIn("3.5000 is at or below the previous row's 4.0000: a counter reset", p7["stoodDown"][0])
        # a fresh or seeded baseline row between disarms the chain: the later usd == cumulative row is nobody's lifetime
        turns5 = [row(A, "web", at(10, 40), 0.0) | {"cumulativeUsd": 100.0, "spendBaseline": "attach-unknown", "redelivered": True},
                  row(A, "web", at(10, 45), 3.0) | {"cumulativeUsd": 103.0, "spendBaseline": "seeded"},
                  row(A, "web", at(10, 50), 105.0) | {"cumulativeUsd": 105.0}]
        p5 = rp.plan(turns5, [at(10, 30)], DAY)
        self.assertEqual((p5["rows"], p5["stoodDown"]), ([], []), "disarmed by the seeded row, no judgement made")

    def test_the_lifetime_rule_is_idempotent_stamps_its_own_rule_into_the_row_and_the_journal_and_restores_what_it_no_longer_believes(self):
        # the 1473 review's two mediums: run two re-armed the chain on the corrected row (its usd no longer equal to its
        # cumulative) and zeroed the post-clear turn; a lifetime correction was never re-judged or restored
        d = tempfile.mkdtemp()
        state = Path(d)
        turns = [row(A, "web", at(10, 40), 0.0) | {"cumulativeUsd": 148.4878, "spendBaseline": "attach-unknown", "redelivered": True},
                 row(A, "web", at(10, 50), 159.2004) | {"cumulativeUsd": 159.2004},
                 row(A, "web", at(11, 0), 2.0) | {"cumulativeUsd": 161.2004},
                 row(A, "web", at(11, 5), 4.25) | {"cumulativeUsd": 4.25},                  # a /clear's first paid turn, present on every run
                 row(A, "web", at(11, 8), 1.0) | {"cumulativeUsd": 5.25}]
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in turns))
        (state / "restart-cuts.jsonl").write_text(json.dumps({"t": at(10, 30), "firstServe": at(10, 30), "settleS": 0.1, "pid": 1}) + chr(10))
        spend = {"hours": {"%sT10" % DAY: {"usd": 159.2004, "turns": 2, "bySid": {A: {"usd": 159.2004, "turns": 2}}},
                           "%sT11" % DAY: {"usd": 7.25, "turns": 3, "bySid": {A: {"usd": 7.25, "turns": 3}}}},
                 "days": {DAY: {"usd": 166.4504, "turns": 5, "bySid": {A: {"usd": 166.4504}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        reg(state, A, name="web")
        import io, contextlib
        seen, orig = [], rp.journal_append
        rp.journal_append = lambda st, entry: (seen.append(entry), orig(st, entry))[1]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        finally:
            rp.journal_append = orig
        rows = {r["t"]: r for r in (json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines())}
        self.assertEqual((round(rows[at(10, 50)]["usd"], 4), rows[at(10, 50)]["usdRecorded"], rows[at(10, 50)]["repairRule"]), (10.7126, 159.2004, rp.REPAIR_RULE_LIFETIME))
        self.assertEqual((rows[at(11, 5)]["usd"], "usdRecorded" in rows[at(11, 5)]), (4.25, False), "the post-clear turn stands")
        deltas = [e for e in seen if e.get("phase") == "rows"][0]["deltas"]
        self.assertEqual((len(deltas), deltas[0]["rule"]), (1, rp.REPAIR_RULE_LIFETIME), "the journal's delta names the rule")
        self.assertIn("the lifetime billed once more", deltas[0]["reason"])
        self.assertAlmostEqual(json.loads((state / "spend.json").read_text())["hours"]["%sT10" % DAY]["usd"], 10.7126, places=4)
        # run two, the same ledger: nothing to apply, the post-clear turn still stands (the corrected row is judged on its
        # recorded figure, disarms the chain, and the 4.25 row is never reached)
        turns_2 = [json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()]
        p2 = rp.plan(turns_2, [at(10, 30)], DAY)
        self.assertEqual((p2["rows"], p2["stoodDown"]), ([], []), "idempotent")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        rows = {r["t"]: r for r in (json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines())}
        self.assertEqual((rows[at(11, 5)]["usd"], round(rows[at(10, 50)]["usd"], 4)), (4.25, 10.7126))
        self.assertAlmostEqual(json.loads((state / "spend.json").read_text())["hours"]["%sT11" % DAY]["usd"], 7.25, places=4)
        # the restore arm: the post-clear turn zeroed the way the first cut did (usd 0, the figure kept, the old stamp) is
        # judged again on its recorded figure and restored, buckets re-folded; and a lifetime correction whose
        # attach-unknown row is gone from the ledger is no longer believed and restored too
        rows[at(11, 5)] |= {"usd": 0.0, "usdRecorded": 4.25, "repairRule": rp.REPAIR_RULE, "repairedT": 1}
        kept = [rows[k] for k in sorted(rows) if k != at(10, 40)]
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + chr(10) for r in kept))
        sp = json.loads((state / "spend.json").read_text()); sp["hours"]["%sT11" % DAY]["usd"] = 3.0; sp["hours"]["%sT11" % DAY]["bySid"][A]["usd"] = 3.0
        sp["days"][DAY]["usd"] = round(10.7126 + 3.0, 4); sp["days"][DAY]["bySid"][A]["usd"] = round(10.7126 + 3.0, 4)
        (state / "spend.json").write_text(json.dumps(sp))
        p3 = rp.plan(kept, [at(10, 30)], DAY)
        got = {c["t"]: c for c in p3["rows"]}
        self.assertEqual(sorted(got), [at(10, 50), at(11, 5)])
        self.assertTrue(got[at(10, 50)]["restore"] and got[at(11, 5)]["restore"])
        self.assertEqual((got[at(10, 50)]["corrected"], got[at(11, 5)]["corrected"]), (159.2004, 4.25))
        self.assertIn("no attach-unknown row arms a chain before it", got[at(10, 50)]["reason"])
        self.assertIn("no lifetime billed once more", got[at(11, 5)]["reason"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        rows = {r["t"]: r for r in (json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines())}
        self.assertEqual((rows[at(11, 5)]["usd"], rows[at(10, 50)]["usd"]), (4.25, 159.2004))
        self.assertFalse(any(k in rows[at(11, 5)] for k in ("usdRecorded", "repairRule", "repairedT")), "the marks are dropped on a restore")
        sp = json.loads((state / "spend.json").read_text())
        self.assertAlmostEqual(sp["hours"]["%sT11" % DAY]["usd"], 7.25, places=4)
        self.assertAlmostEqual(sp["hours"]["%sT10" % DAY]["usd"], 159.2004, places=4)

    def test_a_fold_that_would_take_a_bucket_below_zero_is_said_not_hidden(self):
        spend = {"hours": {"%sT10" % DAY: {"usd": 1.0, "turns": 1, "bySid": {A: {"usd": 1.0}}}}, "days": {DAY: {"usd": 1.0, "turns": 1, "bySid": {A: {"usd": 1.0}}}}}
        p = {"day": DAY, "rows": [{"sid": A, "owner": A, "keyed": False, "name": "web", "t": at(10, 0), "hour": "%sT10" % DAY, "current": 5.0, "corrected": 0.0, "recorded": 5.0}]}
        out = rp.apply_to_spend(spend, p)
        self.assertEqual(out["hours"]["%sT10" % DAY]["usd"], 0.0)
        self.assertTrue(any("would go 4.0000 below zero; held at zero" in n for n in p["notes"]), p["notes"])

    def test_the_rows_are_written_first_and_the_buckets_follow_only_the_rows_found(self):
        # low c: a planned row the file no longer holds as planned (the ledger moved) is left alone in both places
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "firstServe": t, "settleS": 0.1, "pid": 1}) + "\n" for t in self.restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 520.0, "turns": 6, "bySid": {A: {"usd": 516.0}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "bySid": {A: {"usd": 515.0}}}},
                 "days": {DAY: {"usd": 1035.0, "turns": 7, "bySid": {A: {"usd": 1031.0}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        real = rp.read_jsonl
        def read_jsonl(path, bad=None):
            rows = real(path, bad)
            if path.name == "turns.jsonl":
                rows = [dict(r, usd=515.5) if r["t"] == at(11, 10) else r for r in rows]   # the plan sees a figure the file no longer holds
            return rows
        rp.read_jsonl = read_jsonl
        try:
            import io, contextlib
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply", "--no-backup"]), 0)
        finally:
            rp.read_jsonl = real
        self.assertIn("1 turn row(s) corrected (usdRecorded keeps the old figure), then spend.json rewritten for those", out.getvalue())
        self.assertIn("1 planned row(s) were not in turns.jsonl as planned (the ledger moved): left as they are in both files", out.getvalue())
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual(after["hours"]["%sT10" % DAY]["usd"], 16.5, "the found row's bucket moved")
        self.assertEqual(after["hours"]["%sT11" % DAY]["usd"], 515.0, "the missed row's bucket did not")
        rows = {r["t"]: r for r in (json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines())}
        self.assertEqual(rows[at(11, 10)]["usd"], 515.0); self.assertNotIn("usdRecorded", rows[at(11, 10)])

    def test_the_restart_instants_are_the_boots_and_the_cuts_and_a_request_only_when_no_boot_answers_it(self):
        cuts = [{"t": 100, "cutTurns": [], "reason": "main-converge"},                       # the dying kernel's cut row: not an instant
                {"t": 290, "firstServe": 107.2, "settleS": 0.1, "pid": 1, "bootSettled": True},   # the new kernel: its FIRST SERVE is the instant, not its settle (t), which lagged three minutes here
                {"t": 400, "settleS": 0.1, "pid": 2}]                                          # a boot row with no firstServe: its t
        audit = [{"t": 100, "action": "p2p-update"}, {"t": 200, "action": "manager-sigterm"},
                 {"t": 900, "action": "manager-sigterm"}]           # requests, answered or not: never instants (a parked request is no restart)
        self.assertEqual(rp.restart_instants(cuts, audit), [107, 400], "boot rows alone (M1, round three): the request at 900 that nothing answers is a parked one")
        self.assertEqual(rp.restart_instants(cuts), [107, 400])
        self.assertEqual(rp.plan([], rp.restart_instants(cuts), "1970-01-01")["restarts"], 2, "the report counts instants, one per restart")

    def test_a_result_the_old_kernel_recorded_during_its_drain_is_an_ordinary_turn_not_a_fresh_process(self):
        # web: 300 (its lifetime after the 9:30 boot), then at 9:59:53 the restart is REQUESTED and the old kernel records
        # a $5 result at 9:59:55 while draining, the new kernel serves at 10:00:00 and web's first result under it is
        # 320. With the request as the instant the $5 row read as a fresh process and 320 - 5 = 315 was the turn
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 300.0), row(A, "web", at(10, 0) - 5, 5.0), row(A, "web", at(10, 5), 320.0)]
        cuts = [{"t": at(9, 30), "firstServe": at(9, 30), "settleS": 0.1, "pid": 1}, {"t": at(10, 0) - 1, "cutTurns": [], "reason": "p2p-update"},
                {"t": at(10, 0), "firstServe": at(10, 0), "settleS": 0.1, "pid": 2}]
        audit = [{"t": at(9, 30) - 7, "action": "manager-sigterm"}, {"t": at(10, 0) - 7, "action": "manager-sigterm"}]
        restarts = rp.restart_instants(cuts)
        self.assertEqual(restarts, [at(9, 30), at(10, 0)], "boot rows alone: the cut row a second before the boot is the dying kernel's")
        p = rp.plan(turns, restarts, DAY)
        got = {c["t"]: c["corrected"] for c in p["rows"]}
        self.assertEqual(got[at(10, 5)], 15.0, "320 less 300 less the $5 drain-time turn between")
        self.assertNotIn(at(10, 0) - 5, got)
        # a result recorded AT the first-serve second is the old kernel's, and the row after it is the first (the round-three
        # MEDIUM: open at both ends, the interval let a row on the boot's second swallow the instant, the plan came out empty)
        at_boot = [turns[0], turns[1], row(A, "web", at(10, 0), 5.0), turns[3]]
        p2 = rp.plan(at_boot, restarts, DAY)
        got2 = {c["t"]: c for c in p2["rows"]}
        self.assertEqual({t: c["corrected"] for t, c in got2.items()}, {at(9, 40): 2.0, at(10, 5): 15.0},
                         "the $5 at the boot second is between; 320 is the first and a step descending from 300, which is the day's first cumulative (the one row before it, 2.0)")
        self.assertNotIn("no restart instant", got2[at(10, 5)]["reason"], "through the instant, not the no-instant branch")
        # the ledger as an earlier run with the request as its instant left it: the $5 row stands (it was 'fresh'), the
        # 320 row corrected to 315, the first cumulative to 2.0 (then the only row following no restart); judged again,
        # 315 becomes 15 and the typical turn is the median of 2 and the $5 turn now counted ordinary
        repaired = [turns[0], turns[1] | {"usd": 2.0, "usdRecorded": 300.0, "repairRule": rp.REPAIR_RULE}, turns[2], turns[3] | {"usd": 315.0, "usdRecorded": 320.0}]
        again = {c["t"]: (c["current"], c["corrected"]) for c in rp.plan(repaired, restarts, DAY)["rows"]}
        self.assertEqual(again, {at(10, 5): (315.0, 15.0)}, "315 becomes 15; the standing 2.0 this rule wrote on the first cumulative is kept (low C)")


if __name__ == "__main__":
    unittest.main()
