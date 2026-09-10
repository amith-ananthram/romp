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
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
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

# The kernel under test's own state root: bound when it loaded under this module's XDG_STATE_HOME, and
# where its import-time constants (RESTART_CUTS_FILE) live.
STATE_ROOT = km.RESTART_CUTS_FILE.parent

# bin/romp's `romp refresh` row: caller attribution with no action field, as the CLI writes it.
REFRESH_CLI_ROW = {"ppid": 4242, "parent": "bash", "sid": "", "name": "", "tty": "/dev/pts/0", "tmux": ""}


def _own_state_root(case):
    """Aim the shared judge module's STATE at this kernel's own root for one test, and put the previous
    binding back afterwards; returns the audit path both sides now use. The kernel writes the restart
    audit through jd.STATE at CALL time, and the judge module is shared by every test module in the
    worker, so the binding at run time is whatever the last test left there: a path captured at import
    points at a root a later module's kernel load rebound away from, and a path read at run time
    inherits a preceding class's leak (a test that rebinds STATE to a tempdir and removes it without
    restoring leaves the kernel's open() failing inside its never-raises guard, so every reader here
    sees no file, only when that module ran earlier in the same worker). Binding our own root removes
    the dependence on what ran before and on which module loaded the judge last. addCleanup runs after
    tearDown, so the unlinks there still see these paths."""
    saved = jd.STATE
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    jd._rebind_state(STATE_ROOT)
    case.addCleanup(jd._rebind_state, saved)
    return STATE_ROOT / "restart-audit.jsonl"


class OwnStateRoot(unittest.TestCase):
    """_own_state_root: the binding a preceding test left behind decides nothing about where this
    module's kernel writes, and comes back untouched afterwards."""

    def test_a_dangling_binding_left_by_an_earlier_test_is_not_inherited(self):
        saved = jd.STATE
        gone = Path(tempfile.mkdtemp())
        gone.rmdir()
        jd._rebind_state(gone)          # a tempdir root removed and never restored
        try:
            case = unittest.TestCase()
            audit = _own_state_root(case)
            self.assertEqual(jd.STATE, STATE_ROOT)
            self.assertEqual(audit.parent, STATE_ROOT)
            self.assertTrue(audit.parent.is_dir())
            km._audit_unrequested_signal(signal.SIGTERM, now=1)
            self.assertEqual(len(audit.read_text().strip().splitlines()), 1,
                             "the kernel wrote where this module reads")
            audit.unlink()
            case.doCleanups()
            self.assertEqual(jd.STATE, gone, "the previous binding comes back, whatever it was")
        finally:
            jd._rebind_state(saved)


class CutRow(unittest.TestCase):
    def setUp(self):
        self.AUDIT = _own_state_root(self)

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
        audit = self.AUDIT
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
        audit = self.AUDIT
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
        audit = self.AUDIT
        audit.write_text(json.dumps({"t": 1000, "action": "kernel-asks-manager-restart-all",
                                     "reason": "self-update"}) + "\n")
        # `started`: the rows are synthetic and predate this test process, whose start is the default bound
        self.assertIn("self-update", km._recent_restart_reason(window=90, now=1050, started=900))
        self.assertEqual(km._recent_restart_reason(window=90, now=5000, started=900), "",
                         "a stale audit row is not this restart's cause")
        audit.unlink()
        self.assertEqual(km._recent_restart_reason(now=1050, started=900), "", "no audit → anonymous, honestly")

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
        self.assertIn("rec = _recent_restart_audit()", block, "the cut row joins — and CONSUMES — the audit row (auditT)")
        self.assertIn("_drain_and_exit(_audit_reason_text(rec), signum=signum", block,
                      "the request on record is read AT SIGNAL TIME, before the drain: a row that lands "
                      "during the drain did not send this signal")
        self.assertIn("reason = _unrequested_signal_reason(signum, be)", block,
                      "a SIGTERM with no request on record leaves its own audit row (a direct signal to "
                      "the kernel pid used to have no row anywhere and an empty cut reason)")
        self.assertIn("audit_reason=reason", block)
        self.assertIn("if not _EXIT_ONCE.acquire(blocking=False):", block,
                      "a second SIGTERM mid-drain must not nest a second drain and a second cut row")


class UnrequestedSignal(unittest.TestCase):
    """A SIGTERM nobody asked the manager for (a stray kill, a test that fired a real restart, a
    supervisor stop) used to leave NOTHING: no restart-audit row, an empty cut reason. Such a signal
    once restarted the kernel onto a different python, and the outage that followed had no first
    cause on disk. The handler now records what it can know: the signal, its own and its parent's
    pid, the manager it was told about, whether a manager restart was parked, and that no request
    for it was on record. Sender attribution is out of reach here (a signal.signal handler gets no
    siginfo), so the row says so by what it omits."""

    def setUp(self):
        self.AUDIT = _own_state_root(self)   # this kernel's root, whatever an earlier test left bound
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()
        km._EXIT_ONCE = threading.Lock()     # the handler takes it and never gives it back (it exits)
        # no wait for the manager's note by default: the tests that want the wait pass their own.
        # create=True so that on a kernel without the constant the handler tests fail on the behaviour
        # they pin (no row, an empty reason), not on this patch
        self._wait = mock.patch.object(km, "SIGNAL_MANAGER_NOTE_WAIT_S", 0, create=True)
        self._wait.start()
        # the environment decides whether a manager is thought to exist: default to none (standalone),
        # so a pytest run inside a supervised session does not inherit the live manager's pid
        self._env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                 if k != "ROMP_MANAGER_PID"}, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._wait.stop()
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _rows(self, f):
        return [json.loads(l) for l in f.read_text().strip().splitlines()] if f.exists() else []

    def _dead_pid(self):
        """A pid nothing owns: a child spawned and reaped (never a guess at a number)."""
        p = subprocess.Popen(["true"])
        p.wait()
        return p.pid

    def test_the_row_carries_what_is_knowable(self):
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": "4242"}):
            reason = km._audit_unrequested_signal(signal.SIGTERM, pending=True, now=1_781_000_000)
        self.assertEqual(reason, "signal, not requested through the manager")
        rows = self._rows(self.AUDIT)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["action"], "signal")
        self.assertEqual(row["signal"], "SIGTERM")
        self.assertEqual(row["t"], 1_781_000_000)
        self.assertEqual(row["pid"], os.getpid())
        self.assertEqual(row["ppid"], os.getppid())
        self.assertEqual(row["managerPid"], 4242)
        self.assertIs(row["managerRequested"], False)
        self.assertIs(row["managerStopped"], False)
        self.assertIs(row["managerRestartPending"], True)
        self.assertEqual(row["reason"], reason, "the cut row and the audit row say the same thing")

    def test_no_manager_in_the_environment_is_recorded_as_none(self):
        km._audit_unrequested_signal(signal.SIGTERM, pending=False, now=5)
        row = self._rows(self.AUDIT)[0]
        self.assertIsNone(row["managerPid"], "standalone kernel: no manager to have asked")
        self.assertIs(row["managerRestartPending"], False)

    def test_the_managers_own_kill_row_does_not_hide_who_asked(self):
        # the manager notes every SIGTERM it sends (bin/romp-manager auditSigterm); the requester's row
        # underneath still names WHO, so the cut reason keeps naming the cause, not the messenger
        self.AUDIT.write_text(
            json.dumps({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update"}) + "\n"
            + json.dumps({"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "restart"}) + "\n")
        self.assertIn("self-update", km._recent_restart_reason(window=90, now=1050, started=900))

    def test_the_managers_kill_row_alone_is_still_a_request_on_record(self):
        # a `romp-manager restart` or a service stop: the manager asked, nobody audited before it; the
        # manager's own row is what keeps this from reading as an unrequested signal
        self.AUDIT.write_text(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                          "reason": "stop"}) + "\n")
        self.assertEqual(km._recent_restart_reason(window=90, now=1050, started=900), "manager-sigterm: stop")
        self.assertEqual(km._recent_restart_reason(window=90, now=5000, started=900), "")

    def _fire(self, backend=None):
        """Run the SIGTERM handler in-process: the drain has no backend to stop unless one is given,
        the broadcast is a no-op, and os._exit is caught so the runner survives the handler's
        unconditional exit."""
        with mock.patch.object(km, "_broadcast_restarting", lambda *a, **k: None), \
             mock.patch.object(km, "_sdk_backend", backend), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                km._graceful_term(signal.SIGTERM, None)
        ex.assert_called_once_with(0)

    def test_the_handler_files_the_row_and_the_cut_reason_when_nothing_asked(self):
        self._fire()
        audit = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in audit], ["signal"])
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "signal, not requested through the manager",
                         "the cut row used to say nothing at all here")
        self.assertEqual(cuts[0]["cutTurns"], [])

    def test_a_raising_reason_helper_still_leaves_the_cut_row(self):
        # the helper reads ROMP_MANAGER_PID (a pid too large for os.kill raises OverflowError out of
        # _pid_alive) and writes to a stderr the dying supervisor may have closed; a raise there used
        # to skip the cut row this exit exists to leave. The row lands with the plain verdict instead.
        with mock.patch.object(km, "_unrequested_signal_reason", side_effect=OverflowError("pid")):
            self._fire()
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1, "the cut row is written whether or not the reason helper survives")
        self.assertEqual(cuts[0]["reason"], km.SIGNAL_REASON_UNREQUESTED)
        self.assertEqual(cuts[0]["cutTurns"], [])

    def test_an_unlabeled_row_is_neither_the_request_nor_consumed(self):
        # the CLI's actionless refresh row alone, fresh, and the SIGTERM arrives (the manager's note never
        # came: a manager older than the note, or a stray kill first). The reader returns nothing, so the
        # handler files its own `signal` row, and the cut row records no auditT: a row that named nothing
        # was not the request this cut consumed. Taking the row as the answer would file the exit with an
        # empty reason and no row of its own, and spend the CLI row on it
        row = dict(REFRESH_CLI_ROW, t=int(time.time()))
        self.AUDIT.write_text(json.dumps(row) + "\n")
        self.assertIsNone(km._recent_restart_audit(window=90, now=row["t"], started=row["t"] - 10))
        self._fire()
        self.assertEqual([r.get("action") for r in self._rows(self.AUDIT)], [None, "signal"])
        cut = self._rows(km.RESTART_CUTS_FILE)[0]
        self.assertEqual(cut["reason"], km.SIGNAL_REASON_UNREQUESTED)
        self.assertNotIn("auditT", cut, "an unlabeled row is not consumed by a cut it did not explain")

    def test_a_parked_manager_restart_is_read_from_the_backends_drain_lease(self):
        # `managerRestartPending` comes from the backend's drain lease (held while a manager restart is
        # parked for the quiet window), not from a caller's flag; a backend without the method reads False
        class Holding:
            def drain_holding(inner):
                return True

        class NoLease:
            pass
        km._unrequested_signal_reason(signal.SIGTERM, be=Holding(), wait=0, now=1000)
        km._unrequested_signal_reason(signal.SIGTERM, be=NoLease(), wait=0, now=1001)
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["signal", "signal"])
        self.assertIs(rows[0]["managerRestartPending"], True)
        self.assertIs(rows[1]["managerRestartPending"], False)

    def test_a_manager_that_exits_during_the_wait_without_a_note_is_a_stop(self):
        # a manager older than the note (it writes none) going down with us: alive when the signal arrived,
        # gone when the wait for its note runs out. The pid is read again then, so the row says the manager
        # was stopped too instead of calling a service stop a stray signal. A stand-in pid and a stubbed
        # liveness read: nothing here signals or probes a real process
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": "4242"}), \
             mock.patch.object(km, "_pid_alive", side_effect=[True, False]) as alive:
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000, started=900)
        self.assertEqual(alive.call_args_list, [mock.call(4242), mock.call(4242)])
        self.assertEqual(reason, km.SIGNAL_REASON_MANAGER_STOPPED)
        self.assertIs(self._rows(self.AUDIT)[0]["managerStopped"], True)

    def test_the_managers_stop_note_is_found_behind_a_busy_tail(self):
        # a service stop on a busy machine: the manager's stop note for us, then a dozen sessions' own
        # end-on-idle rows land before the handler reads. The note is still the manager going down with us;
        # a short tail would miss it and file the stop as a stray signal
        note = {"t": 1000, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                "reason": "stop", "trigger": "stop"}
        self.AUDIT.write_text(json.dumps(note) + "\n" + "".join(
            json.dumps({"t": 1000, "action": "end-on-idle", "sid": "s%d" % i}) + "\n" for i in range(12)))
        self.assertTrue(km._manager_sigterm_row_for_us(now=1001, started=900))
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1001, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_MANAGER_STOPPED)

    def test_the_handler_leaves_a_requested_restart_alone(self):
        self.AUDIT.write_text(json.dumps({"t": int(time.time()),
                                          "action": "kernel-asks-manager-restart-all",
                                          "reason": "self-update"}) + "\n")
        self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["kernel-asks-manager-restart-all"],
                         "a request on record is the cause; no signal row is added on top of it")
        self.assertIn("self-update", self._rows(km.RESTART_CUTS_FILE)[0]["reason"])

    def test_the_managers_note_alone_is_consumed_like_a_request_row(self):
        # Design pick: with no request row on record the manager's own sigterm note answers, and the cut row
        # CONSUMES it as it would a request row (auditT = the note's t). A note is not a deploy request, so
        # _last_deploy_restart_t reads such a cut as attributed elsewhere with or without the stamp; this pins
        # the stamp so a change on either side is a visible decision.
        t = int(time.time())
        self.AUDIT.write_text(json.dumps({"t": t, "action": "manager-sigterm", "kernel": "main",
                                          "trigger": "restart", "reason": "restart"}) + "\n")
        self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["manager-sigterm"],
                         "the note is the request on record; no signal row is added on top of it")
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "manager-sigterm: restart", "the note's trigger is the label")
        self.assertEqual(cuts[0]["auditT"], t, "the cut row records the note's t as the audit row it consumed")
        self.assertEqual(km._consumed_audit_t(), t)

    def test_a_second_sigterm_mid_drain_does_not_write_a_second_row(self):
        # a service stop signals the kernel, then the manager's shutdownAll signals it again while the
        # first handler drains; the second invocation returns and the first finishes: ONE cut row
        class Nested:
            def drain(inner, timeout):
                km._graceful_term(signal.SIGTERM, None)     # re-entry: must return, not exit
                return {"cutTurns": [{"sid": SID, "name": "web"}], "stopped": 1}
        self._fire(backend=Nested())
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["cutTurns"], [{"sid": SID, "name": "web"}],
                         "the FIRST drain's count is the row; the nested call used to write a partial one")

    # ---- the wording: what the handler can honestly say about a signal with no request on record ----

    def test_manager_alive_and_silent_is_the_unrequested_wording(self):
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):    # alive: us
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)
        row = self._rows(self.AUDIT)[0]
        self.assertIs(row["managerStopped"], False)
        self.assertEqual(row["reason"], reason)

    def test_manager_pid_gone_is_the_service_stop_wording(self):
        # SIGTERM to kernel and manager at once, and the manager went first: the kernel can see that
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(self._dead_pid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000)
        self.assertEqual(reason, "signal; the manager was stopped too (a service stop or restart)")
        row = self._rows(self.AUDIT)[0]
        self.assertEqual(row["action"], "signal")
        self.assertIs(row["managerStopped"], True)
        self.assertIs(row["managerRequested"], False, "still not a request: nobody asked the manager")

    def test_the_managers_stop_note_landing_after_our_signal_is_the_service_stop_wording(self):
        # the manager writes its note BEFORE it kills, so a note that lands AFTER our signal did not
        # send it: the manager is going down alongside us (node's handler ran after ours)
        audit = self.AUDIT

        def note_lands(_secs):
            with open(audit, "a") as f:
                f.write(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                    "pid": os.getpid(), "reason": "stop"}) + "\n")
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=5, sleep=note_lands, now=1000, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_MANAGER_STOPPED)
        self.assertEqual([r["action"] for r in self._rows(audit)], ["manager-sigterm", "signal"])

    def test_a_note_about_another_kernel_is_not_ours(self):
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self.AUDIT.write_text(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "aux",
                                              "pid": os.getpid() + 100000, "reason": "stop"}) + "\n")
            self.assertEqual(km._recent_restart_reason(window=90, now=1000, started=900), "",
                             "the manager's note about an aux kernel is not a request on record here")
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)

    def test_the_handler_end_to_end_for_a_service_stop(self):
        # no sessions to drain, the manager already gone: the cut row says so, in one row each
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(self._dead_pid())}):
            self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["signal"])
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(cuts[0]["reason"], km.SIGNAL_REASON_MANAGER_STOPPED)

    def test_the_handler_reads_the_request_at_signal_time_not_after_the_drain(self):
        # the manager's note lands DURING the drain (sessions were live): it did not send this signal,
        # so the row says the manager was stopped too, not that the manager asked
        audit = self.AUDIT

        class LateNote:
            def drain(inner, timeout):
                with open(audit, "a") as f:
                    f.write(json.dumps({"t": int(time.time()), "action": "manager-sigterm",
                                        "kernel": "main", "pid": os.getpid(), "reason": "stop"}) + "\n")
                return {"cutTurns": []}
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire(backend=LateNote())
        self.assertEqual(self._rows(km.RESTART_CUTS_FILE)[0]["reason"], km.SIGNAL_REASON_MANAGER_STOPPED)
        self.assertEqual([r["action"] for r in self._rows(audit)], ["manager-sigterm", "signal"])

    def test_the_managers_restart_note_landing_after_our_signal_is_not_a_stop(self):
        # a stray kill starts the drain; a second later the rail's restart button has the manager note a
        # `restart` for this pid. The manager is alive and about to respawn us: that is not a service stop,
        # and the row must not say the manager was stopped too
        audit = self.AUDIT
        landed = []

        def note_lands(secs):        # once, then the wait runs its course: a restart note does not end it
            if not landed:
                landed.append(1)
                with open(audit, "a") as f:
                    f.write(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                        "pid": os.getpid(), "reason": "restart", "trigger": "restart"}) + "\n")
            time.sleep(secs)
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0.2, sleep=note_lands, now=1000, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)
        rows = self._rows(audit)
        self.assertEqual([r["action"] for r in rows], ["manager-sigterm", "signal"])
        self.assertIs(rows[1]["managerStopped"], False)

    def test_a_stop_note_for_our_pid_from_before_this_kernel_started_is_a_predecessors(self):
        # a reboot: the manager notes `stop` for kernel pid P; the machine is back inside the window and the
        # kernel comes up as pid P again; a stray kill then finds the pre-reboot note and must not file the
        # exit as a service stop while the manager is alive and about to respawn us
        self.AUDIT.write_text(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                          "pid": os.getpid(), "reason": "stop", "trigger": "stop"}) + "\n")
        self.assertTrue(km._manager_sigterm_row_for_us(now=1041, started=900), "a kernel running at t=1000: its note")
        self.assertFalse(km._manager_sigterm_row_for_us(now=1041, started=1003), "a kernel born after it: a predecessor's")
        self.assertTrue(km._manager_sigterm_row_for_us(now=1005, started=1000.4), "whole seconds, as the rows are")
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1041, started=1003)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)
        self.assertIs(self._rows(self.AUDIT)[-1]["managerStopped"], False)

    def test_the_stop_note_bound_defaults_to_this_process_start(self):
        # as the handler calls it, with no `started`: a note from before _STARTED is not ours, one from its
        # own second is
        born = int(km._STARTED)
        self.AUDIT.write_text(json.dumps({"t": born - 5, "action": "manager-sigterm", "kernel": "main",
                                          "pid": os.getpid(), "reason": "stop", "trigger": "stop"}) + "\n")
        self.assertFalse(km._manager_sigterm_row_for_us(now=born + 10))
        self.AUDIT.write_text(json.dumps({"t": born, "action": "manager-sigterm", "kernel": "main",
                                          "pid": os.getpid(), "reason": "stop", "trigger": "stop"}) + "\n")
        self.assertTrue(km._manager_sigterm_row_for_us(now=born + 10))

    # ---- a previous kernel's own verdict rows are not this kernel's request ----

    def _previous_kernels_verdict(self, action, reason):
        """A verdict row a predecessor filed on its own exit, seconds ago (inside the window, after this
        process started): the old pid, the action, the reason it concluded."""
        rec = {"t": int(time.time()), "action": action, "pid": self._dead_pid(),
               "ppid": 1, "managerPid": None, "reason": reason}
        if action == "signal":
            rec.update({"signal": "SIGTERM", "managerRequested": False, "managerStopped": False,
                        "managerRestartPending": False})
        self.AUDIT.write_text(json.dumps(rec) + "\n")

    def test_a_previous_kernels_signal_verdict_is_not_this_kernels_request(self):
        # two stray kills within 90 seconds: the second must not read the first's `signal` row as the
        # request on record, copy its reason onto the cut row and write no row of its own
        self._previous_kernels_verdict("signal", km.SIGNAL_REASON_MANAGER_STOPPED)
        self._fire()
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["signal", "signal"], "a fresh row for a fresh signal")
        self.assertEqual(rows[1]["pid"], os.getpid())
        self.assertEqual(rows[1]["reason"], km.SIGNAL_REASON_UNREQUESTED, "this exit's own verdict")
        self.assertEqual(self._rows(km.RESTART_CUTS_FILE)[0]["reason"], km.SIGNAL_REASON_UNREQUESTED)

    def test_a_previous_kernels_parent_gone_verdict_is_not_this_kernels_request(self):
        # the manager was SIGKILLed, the kernel followed it (parent-gone), the supervisor brought both back;
        # a service restart 40 seconds later reaches the new kernel, whose cut row must not name a manager
        # exit that did not happen, and must get a `signal` row for the restart
        self._previous_kernels_verdict("parent-gone", km.PARENT_GONE_REASON)
        self._fire()
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["parent-gone", "signal"])
        cut = self._rows(km.RESTART_CUTS_FILE)[0]["reason"]
        self.assertEqual(cut, km.SIGNAL_REASON_UNREQUESTED)
        self.assertNotIn("the manager exited", cut)


class RequestOnRecord(unittest.TestCase):
    """_recent_restart_reason: which row of the audit tail explains a SIGTERM arriving now. A row with an
    action is a request; the manager's `manager-sigterm` note is a mechanism note that never outranks a
    request beneath it; a row with no action is skipped, never the answer (`romp refresh` writes an
    actionless caller-attribution row, and taking its empty label as the verdict would file every deploy
    as an unrequested signal), except an unlabeled quiet request, the park the drift stand-down reads.
    Exact row orders, as the writers produce them."""


    def setUp(self):
        self.AUDIT = _own_state_root(self)
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def tearDown(self):
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _write(self, *rows):
        self.AUDIT.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def _reason(self, now=1002, started=900, window=90):
        """The reader as the SIGTERM handler calls it, for a kernel that started at `started` (before
        every row below unless a test says otherwise; the default bound is this test process's start,
        which every synthetic row predates)."""
        return km._recent_restart_reason(window=window, now=now, started=started)

    def test_a_refresh_from_the_cli_is_a_request_on_record(self):
        # `romp refresh`: the CLI's actionless row, then the manager's restart note, then the SIGTERM.
        # The manager's note is what names it; "" here would file a deploy as a stray kill
        self._write(dict(REFRESH_CLI_ROW, t=1000),
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "restart"})
        self.assertEqual(self._reason(), "manager-sigterm: restart")

    def test_a_refresh_that_labels_its_row_is_named_directly(self):
        # the shape a labeled writer produces (an `action: refresh` row): the request wins over the note
        self._write(dict(REFRESH_CLI_ROW, t=1000, action="refresh"),
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "restart"})
        self.assertEqual(self._reason(), "refresh")

    def test_a_restart_all_the_kernel_asked_for(self):
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": 7},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "restart"})
        self.assertEqual(self._reason(),
                         "kernel-asks-manager-restart-all: self-update")

    def test_a_request_row_outranks_the_managers_stop_note_above_it(self):
        # a request row followed by the manager's own stop note, in that order: the request is the
        # cause, and the note beneath it must not rewrite it as the manager's doing
        self._write({"t": 1000, "action": "rail-button", "reason": "stop"},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "stop"})
        self.assertEqual(self._reason(), "rail-button: stop")

    def test_the_managers_note_alone_answers_when_nothing_else_does(self):
        self._write({"t": 1000, "action": "manager-sigterm", "kernel": "main", "reason": "stop"})
        self.assertEqual(self._reason(), "manager-sigterm: stop")

    def test_a_labeled_request_beneath_an_unlabeled_row_is_the_answer(self):
        # the skip, executed against the order it exists for: the CLI's actionless row lands ABOVE a labeled
        # request (a `romp refresh` typed while the dashboard's restart is on record) and is walked past;
        # returning it with its empty label would file the exit as unrequested with a request on record
        self._write({"t": 1000, "action": "http-restart", "reason": "rail"},
                    dict(REFRESH_CLI_ROW, t=1001))
        self.assertEqual(self._reason(), "http-restart: rail")
        self.assertEqual(km._recent_restart_audit(window=90, now=1002, started=900)["t"], 1000,
                         "the request row is what the cut consumes, not the unlabeled one above it")

    def test_a_request_consumed_inside_this_kernels_lifetime_is_not_named_again(self):
        # the request landed in the second this kernel started (a predecessor consumed it on its way out:
        # the cut row's auditT is its t) and the manager's stop note for us sits above it; the note answers,
        # and the spent request does not come back as the reason for a second exit
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update"},
                    {"t": 1040, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "stop"})
        km.RESTART_CUTS_FILE.write_text(json.dumps({"t": 1000, "cutTurns": [], "auditT": 1000}) + "\n")
        self.assertEqual(km._consumed_audit_t(), 1000)
        self.assertEqual(self._reason(now=1041, started=1000), "manager-sigterm: stop")
        km.RESTART_CUTS_FILE.unlink()
        self.assertEqual(self._reason(now=1041, started=1000), "kernel-asks-manager-restart-all: self-update",
                         "unconsumed, the same request inside the lifetime is this exit's")

    def test_an_actionless_row_alone_is_not_an_answer(self):
        # a fresh actionless row with nothing else: no verdict in it, so nothing is on record
        self._write(dict(REFRESH_CLI_ROW, t=1000))
        self.assertEqual(self._reason(), "")

    def test_an_unlabeled_quiet_request_is_the_park_until_the_note_labels_it(self):
        # `romp refresh --quiet`: the CLI's actionless row with when=quiet is the parked deploy the drift
        # stand-down reads through the same walk, so the walk returns it while nothing else is on record;
        # at the quiet window the manager's restart-all note is what labels the exit
        park = dict(REFRESH_CLI_ROW, t=1000, when="quiet", sha="1111111")
        self._write(park)
        self.assertEqual(km._recent_restart_audit(window=90, now=1600, started=900), park)
        self.assertEqual(self._reason(now=1600), "", "the row itself has no label to answer with")
        self._write(park, {"t": 1500, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                           "reason": "restart", "trigger": "restart-all"})
        self.assertEqual(self._reason(now=1501), "manager-sigterm: restart-all")
        self.assertIsNone(km._recent_restart_audit(window=90, now=1510, started=1502),
                          "delivered before this kernel started: neither the note nor the park is its own")

    def test_a_genuinely_unrequested_signal_has_nothing_on_record(self):
        self.assertEqual(self._reason(), "", "no file at all")
        self._write({"t": 100, "action": "kernel-asks-manager-restart-all", "reason": "self-update"},
                    {"t": 101, "action": "manager-sigterm", "kernel": "main", "reason": "restart"})
        self.assertEqual(self._reason(), "", "rows from an old restart")

    def test_a_note_aimed_at_another_kernel_pid_is_skipped(self):
        self._write({"t": 1000, "action": "manager-sigterm", "kernel": "aux", "pid": os.getpid() + 100000,
                     "reason": "restart"})
        self.assertEqual(self._reason(), "")

    def test_the_managers_note_answers_with_its_trigger(self):
        # the manager's `reason` is only restart|stop; `trigger` is what set the SIGTERM off, and it is
        # what tells a `romp refresh`, the stale-manager self-bounce and a hand stop apart
        for trigger, reason in (("restart-all", "restart"), ("refresh", "stop"), ("stop", "stop"),
                                ("restart", "restart")):
            with self.subTest(trigger=trigger):
                self._write(dict(REFRESH_CLI_ROW, t=1000),
                            {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                             "reason": reason, "trigger": trigger})
                self.assertEqual(self._reason(), "manager-sigterm: " + trigger)

    def test_a_note_without_a_trigger_falls_back_to_its_reason(self):
        self._write({"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "stop"})
        self.assertEqual(self._reason(), "manager-sigterm: stop")

    def test_a_request_older_than_this_kernel_is_a_predecessors(self):
        # t=1000 the previous kernel's self-update; the manager notes `restart` for it and kills it; this
        # kernel starts at t=1003. At t=1040 a service stop: the manager notes `stop` for THIS pid. The
        # self-update is still inside the window and must not be returned as the reason the service stopped
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": 7},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid() + 100000,
                     "reason": "restart", "trigger": "restart-all"},
                    {"t": 1040, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "stop"})
        self.assertEqual(self._reason(now=1041, started=1003), "manager-sigterm: stop")
        self.assertEqual(self._reason(now=1041, started=900), "kernel-asks-manager-restart-all: self-update",
                         "the same rows for a kernel that WAS running at t=1000: the request is its own")

    def test_a_request_older_than_this_kernel_with_no_note_is_nothing_on_record(self):
        # a stray kill of the kernel that a refresh just started: the refresh row is a predecessor's
        self._write(dict(REFRESH_CLI_ROW, t=1000, action="refresh"),
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid() + 100000,
                     "reason": "restart", "trigger": "restart-all"})
        self.assertEqual(self._reason(now=1030, started=1003), "")

    def test_a_request_in_the_same_second_as_the_start_is_kept(self):
        # the bound is whole seconds, as the rows are: a request filed in the start's own second stands
        self._write(dict(REFRESH_CLI_ROW, t=1003, action="refresh"))
        self.assertEqual(self._reason(now=1005, started=1003.4), "refresh")

    def test_a_quiet_request_older_than_this_kernel_yields_to_the_note(self):
        # a `when: quiet` request parked while a predecessor ran, delivered to this kernel at the quiet
        # window: the manager's note for us names it (restart-all), the stale row does not
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update",
                     "when": "quiet"},
                    {"t": 1500, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "restart", "trigger": "restart-all"})
        self.assertEqual(self._reason(now=1501, started=1003), "manager-sigterm: restart-all")

    def test_a_quiet_request_older_than_this_kernel_names_the_cut_when_no_note_does(self):
        # the manager parks a quiet request and restarts whatever kernel runs at the quiet window, up to
        # RESTART_EXPECT_MAX_S, so the request outlives the kernel that filed it. A manager build that wrote
        # no note leaves the row as the only thing on record, and the start bound must not drop it
        self._write({"t": 1000, "action": "p2p-update", "reason": "from TESTHOST to 1111111", "when": "quiet"})
        self.assertEqual(self._reason(now=1600, started=1003), "p2p-update: from TESTHOST to 1111111")
        self.assertEqual(self._reason(now=1600, started=900), "p2p-update: from TESTHOST to 1111111",
                         "and the same for the kernel that filed it")
        self.assertEqual(self._reason(now=1000 + km.RESTART_EXPECT_MAX_S + 1, started=1003), "",
                         "the far manager's backstop bounds how long the park can be pending")

    def test_a_quiet_request_delivered_or_dropped_before_this_kernel_is_consumed(self):
        # delivered: any restart the manager sent clears the park in passing (an aux kernel's rail button
        # here); dropped: the manager's own shutdown (the stale-manager self-bounce noting `refresh`), or a
        # verdict that it was gone, park included. None leaves the quiet row as the request for a later
        # stray kill of this kernel
        for above in ({"t": 1010, "action": "manager-sigterm", "kernel": "aux", "pid": os.getpid() + 100000,
                       "reason": "restart", "trigger": "restart"},
                      {"t": 1010, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid() + 100000,
                       "reason": "stop", "trigger": "refresh"},
                      {"t": 1010, "action": "parent-gone", "pid": os.getpid() + 100000, "reason": km.PARENT_GONE_REASON},
                      {"t": 1010, "action": "signal", "pid": os.getpid() + 100000, "managerStopped": True,
                       "reason": km.SIGNAL_REASON_MANAGER_STOPPED}):
            with self.subTest(above=above["action"] + ":" + str(above.get("trigger") or above.get("reason"))):
                self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update",
                             "when": "quiet"}, above)
                self.assertEqual(self._reason(now=1080, started=1003), "")
        # a stop note for THIS kernel above the park: the manager is stopping us, and that note is the answer
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "when": "quiet"},
                    {"t": 1010, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "stop"})
        self.assertEqual(self._reason(now=1080, started=1003), "manager-sigterm: stop")

    def test_a_quiet_request_survives_a_stop_of_one_other_kernel(self):
        # POST /stop naming one kernel (a dynamic kernel under this root has no root of its own) writes a
        # `stop` note with trigger `stop` for that pid and leaves the manager's park armed; the row cannot be
        # told from the stop that took every kernel down, so the park stays on record, and so does a stray
        # kill of the predecessor with the manager alive. The held converge self-heals at the backstop bound;
        # a released one would cut the turns the quiet window was to spare
        for above in ({"t": 1010, "action": "manager-sigterm", "kernel": "k29900", "pid": os.getpid() + 100000,
                       "reason": "stop", "trigger": "stop"},
                      {"t": 1010, "action": "signal", "pid": os.getpid() + 100000, "managerStopped": False,
                       "reason": km.SIGNAL_REASON_UNREQUESTED}):
            with self.subTest(above=above["action"]):
                self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update",
                             "when": "quiet"}, above)
                self.assertEqual(self._reason(now=1080, started=1003), "kernel-asks-manager-restart-all: self-update")
                self.assertEqual(km._recent_restart_audit(window=90, now=1080, started=1003)["t"], 1000,
                                 "the park the drift stand-down reads")

    def test_a_previous_kernels_verdict_rows_are_never_the_request(self):
        # the kernel's own `signal` and `parent-gone` rows have an action and the OLD pid; with the bound
        # they are also older than this kernel, but the rule holds on its own (a verdict is not a request)
        for action, reason in (("signal", km.SIGNAL_REASON_UNREQUESTED), ("parent-gone", km.PARENT_GONE_REASON)):
            with self.subTest(action=action):
                self._write({"t": 1000, "action": action, "pid": os.getpid() + 100000, "reason": reason})
                self.assertEqual(self._reason(), "", "a lone old verdict row is nothing on record")
                self._write({"t": 1000, "action": action, "pid": os.getpid() + 100000, "reason": reason},
                            {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                             "reason": "stop", "trigger": "stop"})
                self.assertEqual(self._reason(), "manager-sigterm: stop", "the note answers, not the verdict")


class ParentGone(unittest.TestCase):
    """_parent_watch: the manager that spawned the kernel is gone, and the kernel follows it. That exit
    used to be a bare os._exit with nothing in either file; now it leaves an audit row (action
    `parent-gone`) and a cut row with the drained turns, like every other exit."""

    def setUp(self):
        self.AUDIT = _own_state_root(self)
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()
        # Both halves of the kernel's one-exit-path guard: the lock, and the flag whoever takes the lock
        # sets (_TERMINATING, the T240 stand-down). The handler tests above run _graceful_term in-process
        # and leave the flag set (in the kernel the process exits right after), so without this reset
        # _parent_watch stands down here and never reaches os._exit.
        km._EXIT_ONCE = threading.Lock()
        km._TERMINATING[0] = False

    def tearDown(self):
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _rows(self, f):
        return [json.loads(l) for l in f.read_text().strip().splitlines()] if f.exists() else []

    def test_the_exit_leaves_both_rows(self):
        p = subprocess.Popen(["true"])
        p.wait()                                     # a pid nothing owns
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(p.pid)}), \
             mock.patch.object(km, "_sdk_backend", None), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                km._parent_watch()
        ex.assert_called_once_with(0)
        audit = self._rows(self.AUDIT)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["action"], "parent-gone")
        self.assertEqual(audit[0]["reason"], "the manager exited; the kernel followed it")
        self.assertEqual(audit[0]["managerPid"], p.pid)
        self.assertEqual(audit[0]["pid"], os.getpid())
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "parent-gone: the manager exited; the kernel followed it")

    def test_standalone_kernel_has_no_parent_to_watch(self):
        env = {k: v for k, v in os.environ.items() if k != "ROMP_MANAGER_PID"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            km._parent_watch()
        ex.assert_not_called()
        self.assertFalse(self.AUDIT.exists())

    def test_stands_down_when_the_sigterm_handler_owns_the_exit(self):
        p = subprocess.Popen(["true"])
        p.wait()
        km._EXIT_ONCE.acquire()                      # the handler is mid-drain
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(p.pid)}), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            km._parent_watch()
        ex.assert_not_called()
        self.assertFalse(self.AUDIT.exists(), "no second row: the handler writes the one that counts")


if __name__ == "__main__":
    unittest.main()
