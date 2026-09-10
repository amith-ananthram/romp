#!/usr/bin/env python3
"""The per-restart CUT LEDGER (T121, 2026-08-27): every restart writes ONE row to
restart-cuts.jsonl naming what it cut — the drain's effect is measurable only if clean restarts
write rows too (an empty cutTurns list is the success metric, not noise). The row joins the
restart-audit tail for WHO asked, counts the persisted kernel watches (which SURVIVE restarts by
construction — they ride as context, not cuts), and its docstring documents the two things the
kernel cannot do: count in-session watchers/Claude-side workflows (invisible here — the kernel
watch primitive is the fix), and un-write the CLI's own interrupted-by-user transcript stamps
(romp never rewrites CLI transcripts; romp's own records already distinguish machine cuts).
Hermetic state; synthetic sids only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — module import runs boot reconcile against the state root.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_cuts", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-00000000c001"


class CutRow(unittest.TestCase):
    def test_row_shape_from_a_cutting_drain(self):
        row = km._restart_cut_row({"stopped": 3, "inflight": 2, "unjoined": 1, "reaped": 1,
                                   "cutTurns": [{"sid": SID, "name": "web"}]},
                                  watches_armed=4, audit_reason="kernel-asks-manager-restart-all: self-update",
                                  now=1_781_000_000)
        self.assertEqual(row["t"], 1_781_000_000)
        self.assertEqual(row["cutTurns"], [{"sid": SID, "name": "web"}])
        self.assertEqual((row["stopped"], row["unjoined"], row["reaped"]), (3, 1, 1))
        self.assertEqual(row["watchesArmed"], 4, "persisted watches SURVIVE — context, never cuts")
        self.assertIn("self-update", row["reason"])

    def test_a_clean_drain_still_writes_its_row(self):
        # the success metric: a restart that cut NOTHING is a row with an empty list
        row = km._restart_cut_row({"stopped": 5, "inflight": 0, "unjoined": 0, "reaped": 0,
                                   "cutTurns": []}, now=1)
        self.assertEqual(row["cutTurns"], [])
        row = km._restart_cut_row(None, now=1)   # no backend at all — still a row
        self.assertEqual(row["cutTurns"], [])

    def test_append_is_jsonl_and_never_raises(self):
        km._append_restart_cut(km._restart_cut_row({"cutTurns": []}, now=2))
        km._append_restart_cut(km._restart_cut_row({"cutTurns": [{"sid": SID, "name": "api"}]}, now=3))
        rows = [json.loads(l) for l in km.RESTART_CUTS_FILE.read_text().strip().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["cutTurns"][0]["sid"], SID)
        km.RESTART_CUTS_FILE.unlink()

    def test_reason_skips_rows_that_requested_no_restart(self):
        # T240 nit: an in-place converge (main-converge-skip) writes an audit row but restarts nothing;
        # reading only the LAST row labeled a real cut (the parked p2p deploy from 3 min earlier) as
        # the skip. Rows that request no restart are walked past.
        audit = jd.STATE / "restart-audit.jsonl"
        audit.write_text("".join(json.dumps(r) + "\n" for r in [
            {"t": 1000, "action": "p2p-update", "reason": "from X to abc1234", "when": "quiet"},
            {"t": 1150, "action": "main-converge-skip", "tag": "def5678"},
            {"t": 1160, "action": "bus-converge", "tag": "def5678"},
            {"t": 1170, "action": "end-on-idle", "tag": "11111111-2222-3333-4444-555555555555"},
        ]))
        try:
            self.assertIn("p2p-update", km._recent_restart_reason(window=90, now=1200))
            audit.write_text(json.dumps({"t": 1150, "action": "main-converge-skip"}) + "\n")
            self.assertEqual(km._recent_restart_reason(window=90, now=1200), "",
                             "a skip alone names nothing — the cut was anonymous")
        finally:
            audit.unlink()

    def test_a_consumed_audit_row_never_names_a_later_anonymous_cut(self):
        # a quiet p2p row stays inside its 20-minute window long after its restart landed; the cut
        # that consumed it records auditT, and an unaudited SIGTERM 15 min later reads anonymous
        audit = jd.STATE / "restart-audit.jsonl"
        audit.write_text(json.dumps({"t": 1000, "action": "p2p-update", "reason": "from X to abc1234",
                                     "when": "quiet"}) + "\n")
        try:
            self.assertIn("p2p-update", km._recent_restart_reason(now=1240), "the restart it asked for")
            km._append_restart_cut({"t": 1240, "reason": "p2p-update: from X to abc1234", "cutTurns": [],
                                    "auditT": 1000})
            self.assertEqual(km._recent_restart_reason(now=1500), "", "spent — the later cut is anonymous")
            # …and that anonymous cut's own row (no auditT) must not reset consumption: the NEXT
            # anonymous cut inside the window used to re-inherit the row (rows alternated
            # consumed / anonymous / consumed — review find)
            km._append_restart_cut({"t": 1500, "reason": "", "cutTurns": []})
            self.assertEqual(km._recent_restart_reason(now=1900), "", "still spent after an anonymous cut")
            km._append_restart_cut({"t": 1900, "reason": "", "cutTurns": []})
            self.assertEqual(km._recent_restart_reason(now=2100), "")
            # a torn line NEWER than the consuming cut never disables the guard
            with open(km.RESTART_CUTS_FILE, "a") as f:
                f.write('{"t": 2100, "reason": "", "cutTur\n')
            self.assertEqual(km._recent_restart_reason(now=2150), "", "a malformed line is skipped, not fatal")
        finally:
            audit.unlink()
            km.RESTART_CUTS_FILE.unlink()

    def test_reason_joins_the_recent_audit_tail_only(self):
        audit = jd.STATE / "restart-audit.jsonl"
        audit.write_text(json.dumps({"t": 1000, "action": "kernel-asks-manager-restart-all",
                                     "reason": "self-update"}) + "\n")
        self.assertIn("self-update", km._recent_restart_reason(window=90, now=1050))
        self.assertEqual(km._recent_restart_reason(window=90, now=5000), "",
                         "a stale audit row is not this restart's cause")
        audit.unlink()
        self.assertEqual(km._recent_restart_reason(now=1050), "", "no audit → anonymous, honestly")

    def test_a_cutting_drain_counts_mid_shutdown_and_threadless_sessions(self):
        # T143's two undercounts, executed: a session already flagged `ended` with a live in-flight
        # turn IS a cut (its CLI is reaped all the same — the old `not s.ended` clause filtered it
        # out of cutTurns: 10 transcript-verified cuts vs 7 rows), and a constructed-but-never-
        # started session (thread=None) must not crash the whole drain recordless.
        import types as _t
        sbmod = km.sb if hasattr(km, "sb") else None
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        self.assertIn("cut = [{\"sid\": s.sid, \"name\": s.name} for s in sessions if s.inflight]", src,
                      "every in-flight session is a cut — ended included (the join, not a filter)")
        self.assertIn("if s.thread is not None:", src,
                      "a threadless session can no longer crash the drain")

    def test_sigterm_handler_writes_the_ledger(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        block = src[src.index("def _graceful_term"):src.index("def main():")]
        self.assertIn("row = _restart_cut_row(res, watches_armed=len(_pr_watches) + len(_watches),",
                      block, "the row counts BOTH persisted watch stores (T143) — and is built in the")
        self.assertIn("finally:", block)
        self.assertIn("_append_restart_cut(row)", block,
                      "…FINALLY block, so a raising drain still writes what it knew (T143: 2 of 18 "
                      "restarts died recordless)")
        self.assertIn('row["drainError"]', block, "an errored drain's row names the error")
        self.assertIn("audit_reason=_audit_reason_text(rec)", block)
        self.assertIn("rec = _recent_restart_audit()", block, "the cut row joins — and CONSUMES — the audit row (auditT)")


if __name__ == "__main__":
    unittest.main()
