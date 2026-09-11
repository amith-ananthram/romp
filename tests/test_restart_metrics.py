#!/usr/bin/env python3
"""T304: `romp restart-metrics` (cli/restart_metrics.py), the read-only reader of what kernel restarts do to
the sessions. Hermetic: synthetic ledgers under a private state directory (placeholder uuids, TESTHOST, no
live reads), every row kind the reader parses, the window arithmetic, the summary text, the live helpers on
synthetic cgroup files and ps lines, and the JSON document's shape. Nothing here touches a kernel."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
rm = load_source("romp_restart_metrics", os.path.join(BIN, "romp-restart-metrics"))

SID = "11111111-2222-4333-8444-000000000304"
SID2 = "22222222-3333-4444-8555-000000000304"
TZ = "UTC"
D0 = rm.day_start("2026-09-10", TZ)          # the anchor day, midnight UTC


def _write(state: Path, name: str, rows):
    (state / name).parent.mkdir(parents=True, exist_ok=True)
    with open(state / name, "w") as f:
        for r in rows:
            f.write((json.dumps(r) if isinstance(r, dict) else r) + "\n")


def _fixture(state: Path):
    """A synthetic week: two restarts on day 0 (one quiet-window deploy, one backstop cut), a crash-only
    boot on day 2, events, turns (one a redo turn), a state log, spend."""
    t1 = D0 + 3600            # day 0, 01:00: a quiet-window deploy that cut two turns
    t2 = D0 + 7200            # day 0, 02:00: a backstop-cut restart, one turn
    t3 = D0 + 2 * 86400 + 60  # day 2: a boot with no cut row (a crash)
    _write(state, "restart-cuts.jsonl", [
        {"t": t1, "pid": 100, "cutTurns": [{"sid": SID, "name": "web"}, {"sid": SID2, "name": "api"}],
         "stopped": 5, "unjoined": 1, "reaped": 1, "watchesArmed": 3, "reason": "manager-sigterm: restart-all", "auditT": t1 - 2,
         "rssKb": 4 * 1024 * 1024, "cpuS": 900.5},
        {"t": t1 + 2, "pid": 101, "bootSettled": True, "firstServe": t1 + 2.5, "reconcileDone": t1 + 2.7,
         "settleS": 0.2, "prevCutT": t1, "outageS": 2.5, "rssKb": 300 * 1024, "cpuS": 1.5},
        "this line is not json",
        {"t": t2, "pid": 101, "cutTurns": [{"sid": SID, "name": "web"}], "stopped": 5, "unjoined": 0, "reaped": 0,
         "watchesArmed": 3, "reason": "p2p-update", "rssKb": 2 * 1024 * 1024, "cpuS": 300.0},
        {"t": t2 + 4, "pid": 102, "bootSettled": True, "firstServe": t2 + 4.0, "reconcileDone": t2 + 4.3,
         "settleS": 0.3, "prevCutT": t2, "outageS": 4.0},
        {"t": t3, "pid": 103, "bootSettled": True, "firstServe": t3 + 0.5, "reconcileDone": t3 + 0.6, "settleS": 0.1},
    ])
    _write(state, "restart-audit.jsonl", [
        {"t": t1 - 300, "action": "p2p-update", "reason": "deploy", "when": "quiet"},
        {"t": t1 - 3, "action": "quiet-window", "since": t1 - 300, "waitedS": 297, "reason": "quiet", "backstop": False,
         "coalesced": 2, "mode": "all", "lastInflight": 0, "misses": 0, "drainRefusedCount": 0, "drainArmedCount": 3},
        {"t": t1 - 2, "action": "manager-sigterm", "kernel": "main", "pid": 100, "reason": "restart", "trigger": "restart-all"},
        {"t": t2 - 1, "action": "quiet-window", "since": t2 - 901, "waitedS": 900, "reason": "backstop cap", "backstop": True,
         "coalesced": 1, "mode": "all", "lastInflight": 2, "misses": 0, "drainRefusedCount": 0, "drainArmedCount": 300},
        {"t": t2 - 1, "action": "manager-sigterm", "kernel": "main", "pid": 101, "reason": "restart", "trigger": "restart-all"},
        {"t": t3 + 5, "action": "signal", "reason": "signal, not requested through the manager"},
    ])
    _write(state, "session-events.jsonl", [
        {"t": t1 - 1, "pid": 100, "kind": "drain.unjoined", "sid": SID, "name": "web", "inflight": 1, "reaped": True},
        {"t": t1 + 3, "pid": 101, "kind": "reconcile.duplicate-cli", "sid": SID, "name": "web", "fsid": SID, "pids": "7,8", "n": 2},
        {"t": t1 + 3, "pid": 101, "kind": "reconcile.orphan-reaped", "sid": SID, "name": "web", "cliPid": 7},
        {"t": t1 + 3, "pid": 101, "kind": "reconcile.scope-stopped", "unit": "romp-session-11111111-7-1.scope", "sid8": "11111111"},
        {"t": t1 + 3, "pid": 101, "kind": "reconcile.boot", "sessions": 5, "resumed": 2, "restored": 0, "notified": 0,
         "reaped": 1, "scopesStopped": 1, "toStart": 2, "durationS": 0.2},
        {"t": t1 + 4, "pid": 101, "kind": "host.attached", "sid": SID, "name": "web", "boot": True, "hostPid": 20, "cliPid": 21, "replayFrom": 3},
        {"t": t1 + 4, "pid": 101, "kind": "host.attached", "sid": SID2, "name": "api", "boot": True, "hostPid": 22, "cliPid": 23},
        {"t": t2 + 100, "pid": 102, "kind": "host.attached", "sid": SID, "name": "web", "boot": False, "hostPid": 20, "cliPid": 21},
        {"t": t2 + 5, "pid": 102, "kind": "reconcile.boot", "sessions": 5, "resumed": 1, "reaped": 0, "scopesStopped": 0},
        {"t": t2 + 900, "pid": 102, "kind": "crash.heal", "sid": SID2, "name": "api", "attempt": 1},
        {"t": t2 + 960, "pid": 102, "kind": "crash.loop", "sid": SID2, "name": "api", "attempt": 2},
        {"t": t3 + 1, "pid": 103, "kind": "reconcile.boot", "sessions": 5, "resumed": 0, "reaped": 0, "scopesStopped": 0},
        {"t": t3 + 2, "pid": 103, "kind": "lease.stale-heartbeat", "sid": SID, "name": "web", "leasePid": 9},
        {"kind": "no-stamp"},
    ])
    _write(state, "turns.jsonl", [
        {"t": t1 + 40, "sid": SID, "name": "web", "fedT": t1 + 3, "firstOutT": t1 + 5.5, "resultT": t1 + 40.0,
         "durationMs": 37000, "apiMs": 30000, "numTurns": 4, "usd": 0.5, "tokIn": 100, "tokOut": 200, "tokCacheR": 3000,
         "tokCacheW": 400, "opener": "injected", "resumeNotice": True, "fedTexts": 1},
        {"t": t1 + 100, "sid": SID2, "name": "api", "fedT": t1 + 90, "firstOutT": t1 + 91.0, "resultT": t1 + 100.0,
         "durationMs": 10000, "apiMs": 8000, "usd": 0.1, "opener": "human", "resumeNotice": False, "fedTexts": 1},
        {"t": t2 + 50, "sid": SID2, "name": "api", "fedT": 0, "resultT": t2 + 50.0, "opener": "", "resumeNotice": False},
        # a turn the CLI opened itself at 02:01, written by a kernel before the 2026-09-10 writer fix: nothing fed
        # (fedTexts 0), and the stamps still in memory were the 01:00 turn's (its fedT and firstOutT)
        {"t": t2 + 60, "sid": SID, "name": "web", "fedT": t1 + 3, "firstOutT": t1 + 5.5, "resultT": t2 + 60.0,
         "opener": "injected", "resumeNotice": False, "fedTexts": 0},
    ])
    _write(state, "states/%s.jsonl" % SID, [
        {"t": int(t1 - 100), "state": "working"}, {"t": int(t1 - 70), "state": "waiting"},
        {"t": t1 - 0.5, "machineCut": "restart"},
        {"t": int(t1 + 3), "state": "working"}, {"t": int(t1 + 3), "awaiting": False}, {"t": int(t1 + 40), "state": "waiting"},
        {"t": int(t1 + 200), "state": "working"}, {"t": int(t1 + 210), "state": "waiting", "by": "interrupt"},
        {"t": int(t2 + 10), "state": "working"}, {"t": int(t2 + 20), "state": "idle"},
        {"t": t2 + 899.5, "machineCut": "crash"},
    ])
    (state / "spend.json").write_text(json.dumps({"days": {"2026-09-10": {"usd": 12.5, "turns": 30},
                                                            "2026-09-12": {"usd": 3.0, "turns": 4}}, "hours": {}}))
    return t1, t2, t3


class Parsers(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.t1, self.t2, self.t3 = _fixture(self.state)

    def test_restarts_join_cuts_to_boots(self):
        rows, note = rm._read_jsonl(self.state / "restart-cuts.jsonl")
        self.assertEqual(note["unparseable"], 1)
        rs = rm.parse_restarts(rows)
        self.assertEqual([r["t"] for r in rs], [int(self.t1), int(self.t2), int(self.t3)])
        a, b, c = rs
        self.assertEqual((a["cutTurns"], a["cutSessions"], a["unjoined"], a["reaped"]), (2, ["web", "api"], 1, 1))
        self.assertEqual(a["boot"]["outageS"], 2.5)
        self.assertEqual(a["boot"]["settleS"], 0.2)
        self.assertEqual(a["reason"], "manager-sigterm: restart-all")
        self.assertEqual((b["cutTurns"], b["boot"]["outageS"]), (1, 4.0))
        self.assertTrue(c["noCutRow"], "a boot with no cut before it is a crash respawn; its boot row still counts")
        self.assertEqual(c["cutTurns"], 0)

    def test_quiet_windows_join_the_restart_they_released(self):
        cuts, _ = rm._read_jsonl(self.state / "restart-cuts.jsonl")
        audit, _ = rm._read_jsonl(self.state / "restart-audit.jsonl")
        rs = rm.parse_restarts(cuts)
        q = rm.parse_quiet_windows(audit, rs)
        self.assertEqual([(x["waitedS"], x["backstop"], x["restartT"], x["cutTurns"]) for x in q],
                         [(297, False, int(self.t1), 2), (900, True, int(self.t2), 1)])
        counts = rm.audit_counts(audit)
        self.assertEqual(counts["byAction"]["quiet-window"], 2)
        self.assertEqual(counts["sigtermTriggers"], {"restart-all": 2})

    def test_events_turns_and_state_log(self):
        ev, _ = rm._read_jsonl(self.state / "session-events.jsonl")
        events = rm.parse_events(ev)
        self.assertEqual(len(events), 13, "the stamp-less row is dropped (the three host.attached rows count)")
        tu, _ = rm._read_jsonl(self.state / "turns.jsonl")
        turns = rm.parse_turns(tu)
        self.assertEqual(turns[0]["feedToResultS"], 37.0)
        self.assertEqual(turns[0]["feedToFirstOutS"], 2.5)
        self.assertNotIn("feedToResultS", turns[2], "fedT 0 (no feed stamp) → no interval, never a bogus one")
        # the self-opened turn (fedTexts 0) carries the previous fed turn's stamps: an hour of feed-to-result and
        # a duplicated first-output interval if read; it counts as a turn and measures nothing
        self.assertEqual(turns[3]["fedTexts"], 0)
        self.assertNotIn("feedToResultS", turns[3], "nothing fed: the stamps are another turn's, never an interval")
        self.assertNotIn("feedToFirstOutS", turns[3])
        lines = (self.state / "states" / (SID + ".jsonl")).read_text().splitlines()
        sl, cuts = rm.state_log_turns(lines)
        self.assertEqual([x["feedToResultS"] for x in sl], [30.0, 37.0],
                         "working→waiting pairs; an interrupt's by-marked settle and an idle close are not turns")
        self.assertEqual(cuts, {"restart": 1, "crash": 1})
        self.assertEqual(rm.spend_by_day(json.loads((self.state / "spend.json").read_text())), {"2026-09-10": 12.5, "2026-09-12": 3.0})

    def test_parse_turns_gates_on_a_zero_count_alone(self):
        """The fedTexts gate keys on the count 0 and nothing else: a row without the key is read by its stamps
        (a writer that never wrote the key), and a bool is not a count. Direct rows, so the fixture's turn
        count and buckets stay as they are."""
        stamped = {"t": 100, "sid": SID, "fedT": 10, "firstOutT": 12.0, "resultT": 15.0}
        rows = [dict(stamped), dict(stamped, fedTexts=0), dict(stamped, fedTexts=False), dict(stamped, fedTexts=1)]
        got = [(r.get("feedToResultS"), r.get("feedToFirstOutS")) for r in rm.parse_turns(rows)]
        self.assertEqual(got, [(5.0, 2.0), (None, None), (5.0, 2.0), (5.0, 2.0)],
                         "no key: read by its stamps; the count 0: unmeasured; a bool: not a count; a count: measured")

    def test_state_log_pair_breaks_at_a_machine_cut(self):
        """Review find (2026-09-10): a cut turn's `working` row was closed by the RESUMED turn's `waiting`, so
        the interval spanned the outage and the redo. A machineCut row ends the open pair unmeasured."""
        lines = [json.dumps(r) for r in (
            {"t": 1000, "state": "working"}, {"t": 1300.5, "machineCut": "restart"},
            {"t": 1400, "state": "working"}, {"t": 1410, "state": "waiting"})]
        sl, cuts = rm.state_log_turns(lines)
        self.assertEqual([(x["fedT"], x["feedToResultS"]) for x in sl], [(1400, 10.0)])
        self.assertEqual(cuts, {"restart": 1})


class BootJoin(unittest.TestCase):
    def test_a_cut_without_a_boot_row_does_not_take_the_next_restarts_boot(self):
        rows = [{"t": 1000, "cutTurns": [], "stopped": 1},                       # a legacy cut: no boot row ever followed
                {"t": 2000, "cutTurns": [{"sid": SID, "name": "web"}], "stopped": 1},
                {"t": 2003, "bootSettled": True, "firstServe": 2003.5, "reconcileDone": 2003.6, "settleS": 0.1},   # legacy boot: no prevCutT
                {"t": 3000, "cutTurns": [], "stopped": 1},
                {"t": 3002, "bootSettled": True, "firstServe": 3002.5, "reconcileDone": 3002.6, "settleS": 0.1,
                 "prevCutT": 3000, "outageS": 2.5}]
        rs = rm.parse_restarts(rows)
        self.assertEqual([(r["t"], (r.get("boot") or {}).get("t")) for r in rs], [(1000, None), (2000, 2003), (3000, 3002)])
        self.assertEqual(rs[1]["boot"]["outageS"], 3.5, "computed from firstServe when the boot row has no outageS")

    def test_rotated_predecessor_is_read_first(self):
        state = Path(tempfile.mkdtemp())
        _write(state, "turns.jsonl.1", [{"t": 1, "sid": SID}])
        _write(state, "turns.jsonl", [{"t": 2, "sid": SID}])
        rows, note = rm._read_jsonl(state / "turns.jsonl")
        self.assertEqual([r["t"] for r in rows], [1, 2])
        self.assertEqual(note["rows"], 2)


class Windows(unittest.TestCase):
    def test_week_bounds_follow_local_midnight_across_a_daylight_saving_change(self):
        """Review find (2026-09-10): seven times 86400 seconds from the anchor drifted an hour off local
        midnight after a clock change; weeks are counted in local dates."""
        la = "America/Los_Angeles"          # clocks fall back on 2026-11-01
        s, e = rm.bucket_bounds("week of 2026-10-29", "week", la)
        self.assertEqual(e - s, 7 * 86400 + 3600, "the week holds the extra hour and still ends at local midnight")
        self.assertEqual(rm.local_date(e, la), "2026-11-05")
        self.assertEqual(rm.bucket_key(rm.day_start("2026-11-04", la) + 12 * 3600, "week", "2026-10-29", la), "week of 2026-10-29")
        self.assertEqual(rm.bucket_key(rm.day_start("2026-11-05", la) + 60, "week", "2026-10-29", la), "week of 2026-11-05")

    def test_bucket_keys_days_and_weeks(self):
        self.assertEqual(rm.bucket_key(D0 + 10, "day", "2026-09-10", TZ), "2026-09-10")
        self.assertEqual(rm.bucket_key(D0 + 86400 * 6 + 10, "week", "2026-09-10", TZ), "week of 2026-09-10")
        self.assertEqual(rm.bucket_key(D0 + 86400 * 7, "week", "2026-09-10", TZ), "week of 2026-09-17")
        self.assertEqual(rm.bucket_key(D0 - 10, "week", "2026-09-10", TZ), "week of 2026-09-03", "before the anchor buckets backwards")
        s, e = rm.bucket_bounds("week of 2026-09-10", "week", TZ)
        self.assertEqual((s, e), (D0, D0 + 7 * 86400))
        s, e = rm.bucket_bounds("2026-09-10", "day", TZ)
        self.assertEqual((s, e), (D0, D0 + 86400))

    def test_sample_cap_strides_evenly(self):
        self.assertEqual(rm._cap(list(range(10)), cap=5), [0.0, 2.0, 4.0, 6.0, 8.0])
        self.assertEqual(rm._cap([1, 2], cap=5), [1.0, 2.0])

    def test_stats(self):
        self.assertEqual(rm.stats([]), {"n": 0})
        st = rm.stats([1, 2, 3, 4, 10])
        self.assertEqual((st["n"], st["p50"], st["p90"], st["max"], st["mean"]), (5, 3.0, 10.0, 10.0, 4.0))
        self.assertEqual(rm.stats([True, "x", 2])["n"], 1, "only numbers, never bools or strings")


class Document(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.t1, self.t2, self.t3 = _fixture(self.state)

    def test_week_buckets_carry_every_metric(self):
        doc = rm.collect(self.state, kind="week", anchor="2026-09-10", tz=TZ, live=False)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["window"], {"kind": "week", "anchor": "2026-09-10", "tz": TZ})
        self.assertTrue(doc["live"]["skipped"])
        (b,) = doc["buckets"]
        self.assertEqual(b["key"], "week of 2026-09-10")
        self.assertEqual((b["restarts"], b["cutTurns"], b["cleanRestarts"], b["restartsWithoutCutRow"]), (3, 3, 0, 1))
        self.assertEqual(b["measuredRestarts"], 2, "a boot with no cut row cut an unknown number of turns, not zero")
        self.assertEqual(b["cutTurnsPerRestart"], 1.5, "3 cut turns over the 2 MEASURED restarts (review find)")
        self.assertEqual(b["cutSessions"], {"web": 2, "api": 1})
        self.assertEqual(b["reasons"], {"manager-sigterm": 1, "p2p-update": 1, "(no cut row)": 1})
        self.assertEqual((b["outageS"]["n"], b["outageS"]["max"]), (2, 4.0))
        self.assertEqual((b["settleS"]["n"], b["settleS"]["max"]), (3, 0.3))
        self.assertEqual((b["quietWindows"], b["backstopFires"], b["quietWaitS"]["p50"], b["quietWaitS"]["max"]), (2, 1, 297.0, 900.0))
        self.assertEqual((b["boots"], b["resumedTurns"]), (3, 3))
        self.assertEqual((b["orphansReaped"], b["scopesStopped"], b["duplicateClis"], b["crashHeals"], b["crashLoops"],
                          b["drainLeftClosing"], b["leaseProblems"]), (1, 1, 1, 1, 1, 1, 1))
        self.assertEqual((b["drainUnjoinedCount"], b["drainReapedCount"]), (1, 1))
        self.assertEqual((b["attachedAtBoot"], b["attachedLater"]), (2, 1),
                         "host.attached rows (T315): the sessions a restart kept running under their hosts, and the later attaches")
        self.assertEqual(doc["events"]["byKind"]["host.attached"], 3)
        self.assertEqual(b["redo"], {"turns": 1, "usd": 0.5, "tokens": 3700})
        self.assertEqual(b["turns"], 4, "the self-opened turn counts as a turn; only its latency is unmeasured")
        self.assertEqual((b["latency"]["feedToResultS"]["n"], b["latency"]["feedToResultS"]["max"]), (2, 37.0))
        self.assertEqual(b["latency"]["feedToFirstOutS"]["p50"], 1.0)
        self.assertEqual(b["latency"]["apiS"]["max"], 30.0)
        self.assertEqual((b["stateLogTurns"], b["stateLogLatencyS"]["max"]), (2, 37.0))
        self.assertEqual(b["machineCuts"], {"restart": 1, "crash": 1})
        self.assertEqual(b["spendUsd"], 15.5)
        self.assertEqual(b["samples"]["feedToResultS"], [37.0, 10.0])
        self.assertEqual((b["kernelAtExit"]["rssMb"]["n"], b["kernelAtExit"]["rssMb"]["max"], b["kernelAtExit"]["cpuS"]["max"]),
                         (2, 4096.0, 900.5), "the crash boot has no cut row and so no exit sample")
        self.assertEqual([(k["kind"], k["rssMb"]) for k in doc["kernelSeries"]],
                         [("exit", 4096.0), ("boot", 300.0), ("exit", 2048.0)])
        self.assertEqual(b["samples"]["stateLogS"], [30.0, 37.0])
        self.assertEqual(doc["events"]["byKind"]["reconcile.boot"], 3)
        self.assertEqual(doc["sources"]["stateLogs"], 1)
        self.assertEqual(doc["sources"]["restartCuts"]["rows"], 5)

    def test_day_buckets_and_range(self):
        doc = rm.collect(self.state, kind="day", tz=TZ, live=False, since=D0, until=D0 + 86400)
        self.assertEqual([b["key"] for b in doc["buckets"]], ["2026-09-10"])
        b = doc["buckets"][0]
        self.assertEqual((b["restarts"], b["cutTurns"], b["spendUsd"]), (2, 3, 12.5))
        self.assertEqual(doc["window"]["anchor"], "2026-09-10", "the default anchor is the first restart's day in range")
        full = rm.collect(self.state, kind="day", tz=TZ, live=False)
        self.assertEqual([b["key"] for b in full["buckets"]], ["2026-09-10", "2026-09-12"])
        self.assertEqual(full["buckets"][1]["restartsWithoutCutRow"], 1)

    def test_summary_text_names_the_numbers(self):
        doc = rm.collect(self.state, kind="week", anchor="2026-09-10", tz=TZ, live=False)
        doc["label"] = "TESTHOST"
        text = rm.summary(doc)
        self.assertIn("restart metrics: TESTHOST, week windows", text)
        self.assertIn("week of 2026-09-10", text)
        self.assertIn("restarts 3 · turns cut 3 (1.5 per measured restart) · clean restarts 0 · boots with no cut row 1", text)
        self.assertIn("dates in UTC time", text)
        self.assertIn("quiet windows 2 · wait n=2 p50 297 s p90 900 s max 900 s · backstop fired 1", text)
        self.assertIn("orphans reaped 1 · scopes stopped 1 · duplicate CLIs 1 · crash heals 1 · crash loops 1 · drain left closing 1", text)
        self.assertIn("hosts attached: at boot 2 · later 1", text)
        self.assertIn("continuation notices 3 · redo turns 1 · redo cost $0.50 of $15.50 in the window · redo tokens 3,700", text)
        self.assertIn("feed to result n=2 p50 10.0 s p90 37.0 s", text)
        self.assertIn("machine cuts crash 1, restart 1", text)
        self.assertIn("kernel process at its exits: resident n=2 p50 2048 MB p90 4096 MB max 4096 MB · cpu n=2 p50 300 s", text)
        self.assertIn("live: skipped", text)
        self.assertIn("note: Redo cost", text)
        self.assertNotIn("MISSING", text)

    def test_default_label_is_this_machine_and_nothing_the_user_reads_carries_an_em_dash(self):
        """Two of the user's standing rules for anything they read (the manager's fold, 2026-09-10): the
        hostname is a personal identifier, so the default header names 'this machine' and the hostname
        appears nowhere unless --label passes it; and no em-dash anywhere in the summary."""
        import socket
        host = (socket.gethostname() or "").split(".")[0]
        doc = rm.collect(self.state, kind="week", anchor="2026-09-10", tz=TZ, live=False)
        self.assertEqual(doc["label"], "this machine")
        self.assertNotIn("host", doc, "no host field at all: the document names itself by label only")
        text = rm.summary(doc)
        self.assertIn("restart metrics: this machine, week windows", text)
        if host:
            self.assertNotIn(host, text)
            self.assertNotIn(host, json.dumps(doc))
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2014", json.dumps(doc))
        labelled = rm.collect(self.state, kind="week", anchor="2026-09-10", tz=TZ, live=False, label="web box")
        self.assertIn("restart metrics: web box,", rm.summary(labelled))

    def test_no_em_dash_in_any_string_of_the_reader_or_the_report(self):
        """Every string literal of the two modules, docstrings included (--help prints the module docstring's
        first paragraph), is free of em-dashes, by AST, so a new label cannot bring one back."""
        import ast
        for path in (os.path.join(BIN, "romp-restart-metrics"), os.path.join(os.path.dirname(HERE), "scripts", "restart_metrics_report.py")):
            tree = ast.parse(open(path, encoding="utf-8").read())
            offenders = [(n.lineno, n.value[:60]) for n in ast.walk(tree)
                         if isinstance(n, ast.Constant) and isinstance(n.value, str) and "\u2014" in n.value]
            self.assertEqual(offenders, [], "%s carries an em-dash in a string" % os.path.basename(path))
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            rm.main(["--help"])
        self.assertNotIn("\u2014", out.getvalue())

    def test_summary_dates_follow_the_documents_zone(self):
        """Review find (2026-09-10): the window bounds were rendered in the machine's zone, ignoring --tz. A
        zone fourteen hours ahead of UTC puts the anchor's local midnight on the previous UTC day."""
        far = "Pacific/Kiritimati"
        doc = rm.collect(self.state, kind="day", tz=far, live=False, since=rm.day_start("2026-09-10", far),
                         until=rm.day_start("2026-09-11", far))
        text = rm.summary(doc)
        self.assertIn("2026-09-10  (2026-09-10 to 2026-09-10)", text, "the machine's UTC clock would have said 2026-09-09")

    def test_bad_anchor_is_refused_like_the_other_dates(self):
        self.assertEqual(rm.main(["--anchor", "yesterday", "--no-live", "--state", str(self.state)]), 2)

    def test_missing_ledgers_are_said(self):
        empty = Path(tempfile.mkdtemp())
        doc = rm.collect(empty, kind="day", tz=TZ, live=False)
        self.assertEqual(doc["buckets"], [])
        self.assertFalse(doc["sources"]["restartCuts"]["present"])
        text = rm.summary(doc)
        self.assertIn("MISSING ledgers", text)
        self.assertIn("restart-cuts.jsonl", text)
        self.assertIn("no rows in range", text)

    def test_main_json_and_text(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = rm.main(["--json", "--window", "week", "--anchor", "2026-09-10", "--tz", TZ, "--no-live", "--state", str(self.state)])
        self.assertEqual(rc, 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["buckets"][0]["restarts"], 3)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = rm.main(["--no-live", "--state", str(self.state), "--tz", TZ])
        self.assertEqual(rc, 0)
        self.assertIn("restart metrics", out.getvalue())
        self.assertEqual(rm.main(["--since", "not-a-date", "--no-live", "--state", str(self.state)]), 2)


class LiveHelpers(unittest.TestCase):
    """The live reads, on synthetic files and listings: no cgroup, no ps, no kernel is touched."""

    def test_scope_stats_from_synthetic_cgroup_files(self):
        d = Path(tempfile.mkdtemp()) / "romp-session-11111111-4242-1757374800.scope"
        d.mkdir()
        (d / "memory.current").write_text("104857600\n")
        (d / "memory.peak").write_text("209715200\n")
        (d / "cpu.stat").write_text("usage_usec 12500000\nuser_usec 1\nsystem_usec 2\n")
        (d / "cgroup.procs").write_text("4242\n4300\n4301\n")
        s = rm.scope_stats(str(d))
        self.assertEqual((s["sid8"], s["cliPid"], s["memBytes"], s["memPeakBytes"], s["cpuS"], s["procs"]),
                         ("11111111", 4242, 104857600, 209715200, 12.5, 3))
        self.assertEqual(rm.cgroup_scope_dirs(str(d.parent)), [str(d)])

    def test_ps_parsing_trees_and_duplicates(self):
        lines = ["  100     1  1000  0.5 /x/claude --output-format stream-json --resume %s --input-format stream-json" % SID,
                 "  101   100   500  1.0 bash -c tool",
                 "  102   101   250  0.0 sleep 3",
                 "  200     1  2000  2.0 /x/claude --output-format stream-json --resume=%s --input-format stream-json" % SID,
                 "  300     1   900  0.0 /x/claude --output-format stream-json --resume %s --input-format stream-json" % SID2,
                 "  400     1   100  0.0 claude --resume %s" % SID,
                 "garbage"]
        procs = rm.parse_ps(lines)
        self.assertEqual(len(procs), 6)
        regs = {SID: {"name": "web", "lastSid": SID, "alive": True}, SID2: {"name": "api", "lastSid": SID2, "alive": True}}
        trees = sorted(rm.ps_session_trees(procs, regs), key=lambda t: t["cliPid"])
        self.assertEqual([(t["name"], t["cliPid"], t["procs"], t["memBytes"], t["cpuPct"]) for t in trees],
                         [("web", 100, 3, 1750 * 1024, 1.5), ("web", 200, 1, 2000 * 1024, 2.0), ("api", 300, 1, 900 * 1024, 0.0)])
        self.assertEqual(rm.duplicate_clis(procs, [SID, SID2]), {SID: [100, 200]}, "the tmux CLI (no mark) never counts")

    def test_kernel_live_unreachable_is_said(self):
        state = Path(tempfile.mkdtemp())
        (state / "serve-port").write_text("1\n")          # nothing listens on port 1
        with mock.patch.dict(os.environ):                 # the suite's dead-port floor comes back after (review find)
            os.environ.pop("ROMP_KERNEL_PORT", None)
            k = rm.kernel_live(state)
        self.assertEqual(k["port"], 1)
        self.assertIn("not reachable", k["error"])


if __name__ == "__main__":
    unittest.main()
