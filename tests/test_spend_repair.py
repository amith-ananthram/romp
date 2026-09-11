#!/usr/bin/env python3
"""romp spend-repair (T354): the arithmetic over a synthetic day's turn ledger, the buckets' before and after, the
dry run writing nothing, --apply writing the corrected buckets and rows. Synthetic ids and figures only."""
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
sys.path.insert(0, os.path.join(ROOT, "cli"))
import spend_repair as rp  # noqa: E402

A, B = "aaaaaaaa-1111-2222-3333-444444444444", "bbbbbbbb-1111-2222-3333-444444444444"
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
        self.assertEqual((first["recorded"], first["corrected"]), (507.0, 3.0), "the day's first cumulative row: the median of web's ordinary turns (3, 4, 2)")
        second = fixed[("web", at(11, 10))]
        self.assertEqual((second["recorded"], second["corrected"]), (515.0, 6.0), "515 less 507 less the $2 row between")
        self.assertEqual(p["hours"]["%sT10" % DAY], {"before": 520.0, "after": 16.0})
        self.assertEqual(p["hours"]["%sT11" % DAY], {"before": 515.0, "after": 6.0})
        self.assertEqual(p["days"][DAY], {"before": 1035.0, "after": 22.0})
        self.assertEqual(p["bySid"][A]["after"], 18.0)
        self.assertEqual(p["bySid"][B], {"name": "api", "before": 4.0, "after": 4.0})
        self.assertEqual(p["restarts"], 2)

    def test_a_fresh_process_below_the_previous_cumulative_starts_a_new_chain(self):
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 300.0),      # a lifetime after the 9:30 restart
                 row(A, "web", at(10, 40), 5.0),                                    # after the 10:30 restart: BELOW 300, a fresh process
                 row(A, "web", at(11, 10), 60.0)]                                   # after the 11:00 restart: its new lifetime
        p = rp.plan(turns, [at(9, 30), at(10, 30), at(11, 0)], DAY)
        got = {c["t"]: (c["recorded"], c["corrected"]) for c in p["rows"]}
        self.assertEqual(got[at(9, 40)], (300.0, 2.0), "first cumulative: the typical turn, the median of the rows that are not a first result after a restart (2 alone; 5 and 60 follow restarts)")
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
        self.assertEqual(got[at(9, 40)], (300.0, 3.5), "the typical turn: the median of 2 and 5, the rows that follow no restart")

    def test_a_lone_first_result_with_no_staircase_after_it_stands(self):
        turns = [row(A, "web", at(9, 0), 2.0), row(A, "web", at(9, 40), 300.0), row(A, "web", at(10, 0), 5.0)]
        self.assertEqual(rp.plan(turns, [at(9, 30)], DAY)["rows"], [], "one big first result and no chain: a fresh process's long turn")
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
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "cutTurns": [], "reason": "main-converge"}) + "\n" for t in restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 5.0, "turns": 2, "bySid": {A: {"usd": 5.0, "turns": 2}}}},
                 "days": {DAY: {"usd": 27.5, "turns": 5, "bySid": {A: {"usd": 27.5}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        p = rp.plan(turns, restarts, DAY)
        self.assertEqual([(c["t"], c["current"], c["corrected"], c.get("restore")) for c in p["rows"]], [(at(10, 40), 0.0, 303.0, True)],
                         "the zeroed turn comes back; the two real steps stand as corrected")
        self.assertIn("restored: 303.0000 is below the previous cumulative 300.0000 plus 1 row(s) between (5.0000), a turn of a fresh process", p["rows"][0]["reason"])
        self.assertEqual(p["hours"]["%sT10" % DAY], {"before": 5.0, "after": 308.0})
        self.assertEqual(p["days"][DAY], {"before": 27.5, "after": 330.5})
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply"]), 0)
        self.assertIn("0 cumulative row(s) found, 1 earlier correction(s) to restore", out.getvalue())
        self.assertIn("0 turn row(s) corrected (usdRecorded keeps the old figure), 1 restored to the kernel's figure", out.getvalue())
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual(after["hours"]["%sT10" % DAY]["usd"], 308.0)
        self.assertEqual(after["days"][DAY]["bySid"][A]["usd"], 330.5)
        rows = {r["t"]: r for r in (json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines())}
        self.assertEqual(rows[at(10, 40)]["usd"], 303.0)
        self.assertNotIn("usdRecorded", rows[at(10, 40)], "a restored row is the kernel's row again")
        self.assertNotIn("repairedT", rows[at(10, 40)])
        self.assertEqual((rows[at(11, 10)]["usd"], rows[at(11, 10)]["usdRecorded"]), (17.0, 320.0), "a standing correction keeps its marks")
        again = rp.plan([json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()], restarts, DAY)
        self.assertEqual(again["rows"], [], "and a third run finds nothing")

    def test_apply_folds_the_deltas_into_the_buckets_and_the_rows_and_a_dry_run_writes_nothing(self):
        d = tempfile.mkdtemp()
        state = Path(d)
        (state / "turns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.turns))
        (state / "restart-cuts.jsonl").write_text("".join(json.dumps({"t": t, "cutTurns": [], "reason": "p2p-update: from TESTHOST to abc"}) + "\n" for t in self.restarts))
        spend = {"hours": {"%sT10" % DAY: {"usd": 520.0, "turns": 6, "bySid": {A: {"usd": 516.0, "turns": 4}, B: {"usd": 4.0, "turns": 2}}},
                           "%sT11" % DAY: {"usd": 515.0, "turns": 1, "key": {"usd": 515.0}, "bySid": {A: {"usd": 515.0, "turns": 1, "key": {"usd": 515.0}}}}},
                 "days": {DAY: {"usd": 1035.0, "turns": 7, "bySid": {A: {"usd": 1031.0}, B: {"usd": 4.0}}}}}
        (state / "spend.json").write_text(json.dumps(spend))
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d]), 0)
        self.assertIn("dry run: nothing written", out.getvalue())
        self.assertIn("web", out.getvalue()); self.assertIn("507.00 ->     3.00", out.getvalue())
        self.assertEqual(json.loads((state / "spend.json").read_text()), spend, "a dry run leaves spend.json as it was")
        self.assertEqual(len((state / "turns.jsonl").read_text().splitlines()), 7)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(rp.main(["--day", DAY, "--state", d, "--apply"]), 0)
        self.assertIn("applied: spend.json rewritten, 2 turn row(s) corrected", out.getvalue())
        after = json.loads((state / "spend.json").read_text())
        self.assertEqual(after["hours"]["%sT10" % DAY]["usd"], 16.0)
        self.assertEqual(after["hours"]["%sT10" % DAY]["bySid"][A]["usd"], 12.0)
        self.assertEqual(after["hours"]["%sT10" % DAY]["bySid"][B]["usd"], 4.0, "api untouched")
        self.assertEqual(after["hours"]["%sT10" % DAY]["turns"], 6, "turns and tokens stay")
        self.assertEqual(after["hours"]["%sT11" % DAY]["usd"], 6.0)
        self.assertEqual(after["hours"]["%sT11" % DAY]["key"]["usd"], 6.0, "the keyed split follows")
        self.assertEqual(after["hours"]["%sT11" % DAY]["bySid"][A]["key"]["usd"], 6.0)
        self.assertEqual(after["days"][DAY]["usd"], 22.0)
        self.assertEqual(after["days"][DAY]["bySid"][A]["usd"], 18.0)
        rows = [json.loads(l) for l in (state / "turns.jsonl").read_text().splitlines()]
        fixed = [r for r in rows if "usdRecorded" in r]
        self.assertEqual([(r["usdRecorded"], r["usd"]) for r in fixed], [(507.0, 3.0), (515.0, 6.0)])
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
        self.assertEqual({c["t"] for c in rp.plan(turns, restarts, DAY)["rows"]}, {at(9, 40), at(11, 10)}, "without the bound, 300 reads as the first cumulative")
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

    def test_the_restart_instants_come_from_both_ledgers(self):
        cuts = [{"t": 100, "cutTurns": [], "reason": "main-converge"}, {"t": 150, "firstServe": 1}]      # a bootSettled row is not a restart
        audit = [{"t": 200, "action": "manager-sigterm"}, {"t": 250, "action": "quiet-window"}, {"t": 100, "action": "p2p-update"}]
        self.assertEqual(rp.restart_instants(cuts, audit), [100, 200])


if __name__ == "__main__":
    unittest.main()
