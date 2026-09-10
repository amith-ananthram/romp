#!/usr/bin/env python3
"""T304 (stage 0 of the restart-surviving sessions program, 2026-09-10): the two ledgers the restart monitors
read. session-events.jsonl gets one flat row per thing that went wrong with a session's process (an orphaned
CLI ended at boot, a leftover scope stopped, two CLIs holding one conversation, a crash heal or crash loop, a
session the drain's bound left closing) and one summary row per boot sweep; every problem also lands on the
backend's problem ring as its prose and on the kernel log as `<prose> ;; problem-row {json}` (the shape agreed
with the lease work, which writes its `lease.*` kinds through the same helper). turns.jsonl gets one row per
settled turn with the event stamps the latency and redo-cost figures read. Synthetic fixtures only: placeholder
uuids, fake pids above pid_max, scripted `run` / `kill` seams, no real CLI."""
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the load
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"
sb = load_source("romp_sdk_backend_t304_ledger", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-000000000304"
SID2 = "22222222-3333-4444-8555-000000000304"


def _pid_max() -> int:
    try:
        return int(open("/proc/sys/kernel/pid_max").read().strip())
    except (OSError, ValueError):
        return 4194304


P = _pid_max()
CLI, CLI2, TOOL, LIVE, KERNEL = P + 304, P + 305, P + 306, P + 307, P + 90304


def _backend(d, log=None):
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=log or (lambda *a, **k: None))
    be._t304_ring0 = len(be.problems())   # the constructor's own verdicts (no SDK here) are not under test
    return be


def _ring(be):
    """The problem-ring rows this test added, past the constructor's own."""
    return be.problems()[be._t304_ring0:]


def _reg(d, sid, name, fsid=None):
    r = {"sid": sid, "name": name, "cwd": "/tmp", "alive": True, "lastSid": fsid or sid}
    sb.write_reg(Path(d), sid, r)
    return r


def _events(d):
    p = Path(d) / sb.SESSION_EVENTS_FILE
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


class ProblemRowShape(unittest.TestCase):
    """One helper, three outputs: the ledger row, the parseable log line, the readable ring text."""

    def test_row_line_and_ring(self):
        d = tempfile.mkdtemp()
        lines = []
        be = _backend(d, log=lambda m: lines.append(m))
        line = sb.problem_row(be.state_dir, "session web: something went wrong", "test.kind", log=be._log,
                              sid=SID, name="web", cliPid=CLI, scope=None, extra=1.5, flag=True)
        rows = _events(d)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "test.kind")
        self.assertEqual(row["sid"], SID)
        self.assertEqual(row["name"], "web")
        self.assertEqual(row["pid"], os.getpid())
        self.assertIsInstance(row["t"], int)
        self.assertEqual(row["cliPid"], CLI)
        self.assertNotIn("scope", row, "None fields are dropped: the row stays flat and honest")
        self.assertEqual(row["extra"], 1.5)
        self.assertIs(row["flag"], True)
        self.assertEqual(row["text"], "session web: something went wrong")
        # the log line: prose, the marker, the SAME object — parseable by rsplit on the marker
        self.assertTrue(line.startswith("session web: something went wrong" + sb.PROBLEM_ROW_MARK))
        self.assertEqual(sb.parse_problem_row(line), row)
        self.assertEqual(lines[-1], line, "the kernel log gets the whole line (after the constructor's own lines)")
        # the ring gets the prose alone, so the bell and error center stay readable
        ring = _ring(be)
        self.assertEqual([r["text"] for r in ring], ["session web: something went wrong"])

    def test_ring_false_keeps_the_ring_clean(self):
        d = tempfile.mkdtemp()
        be = _backend(d)
        sb.problem_row(be.state_dir, "an informational line", "test.info", log=be._log, ring=False)
        self.assertEqual(_ring(be), [])
        self.assertEqual(_events(d)[0]["kind"], "test.info")

    def test_parse_rejects_other_lines(self):
        self.assertIsNone(sb.parse_problem_row("drain: stopped 3 session(s)"))
        self.assertIsNone(sb.parse_problem_row("x" + sb.PROBLEM_ROW_MARK + "not json"))
        self.assertIsNone(sb.parse_problem_row("x" + sb.PROBLEM_ROW_MARK + "[1, 2]"))

    def test_a_plain_log_callable_gets_the_line(self):
        d = tempfile.mkdtemp()
        got = []
        def plain(m):          # a callable without problem= / ring_text= (an older seam, a test lambda)
            got.append(m)
        line = sb.problem_row(Path(d), "prose", "test.plain", log=plain)
        self.assertEqual(got, [line])

    def test_nested_values_flatten_to_str(self):
        d = tempfile.mkdtemp()
        row = sb.append_session_event(Path(d), "test.flat", pids=[1, 2], t=1700000000)
        self.assertEqual(row["pids"], "[1, 2]")
        self.assertEqual(row["t"], 1700000000)


class LogRingText(unittest.TestCase):
    """SdkBackend._log's ring_text: the ring keeps the readable text while the log line carries the tail."""

    def test_ring_text_and_repeat_count(self):
        be = _backend(tempfile.mkdtemp())
        be._log("prose ;; tail", problem=True, key="k", ring_text="prose")
        be._log("prose ;; tail", problem=True, key="k", ring_text="prose")
        (row,) = _ring(be)
        self.assertTrue(row["text"].startswith("prose (1 repeat"), row["text"])
        self.assertEqual(row["first"], "prose")

    def test_default_is_the_line_itself(self):
        be = _backend(tempfile.mkdtemp())
        be._log("plain problem", problem=True)
        self.assertEqual(_ring(be)[0]["text"], "plain problem")


class PureHelpers(unittest.TestCase):
    def test_cli_sid_of_every_spelling(self):
        self.assertEqual(sb.cli_sid_of("claude --resume %s --input-format stream-json" % SID, [SID]), SID)
        self.assertEqual(sb.cli_sid_of("claude --resume=%s" % SID, [SID2, SID]), SID)
        self.assertEqual(sb.cli_sid_of("claude --session-id %s" % SID2, [SID, SID2]), SID2)
        self.assertIsNone(sb.cli_sid_of("claude --resume other", [SID]))
        self.assertIsNone(sb.cli_sid_of("claude --resume %s" % SID, [""]))

    def test_duplicate_clis(self):
        ps = ["  %d 1 /x/claude --output-format stream-json --resume %s --input-format stream-json" % (CLI, SID),
              "  %d %d /x/claude --output-format stream-json --resume %s --input-format stream-json" % (LIVE, KERNEL, SID),
              "  %d 1 /x/claude --output-format stream-json --resume=%s --input-format stream-json" % (CLI2, SID2),
              "  %d 1 claude --resume %s" % (TOOL, SID),          # a tmux CLI: no stream-json mark, never counted
              "  garbage line"]
        self.assertEqual(sb.duplicate_clis(ps, [SID, SID2]), {SID: [CLI, LIVE]})
        self.assertEqual(sb.duplicate_clis(ps, [SID2]), {})


class BootReconcileRows(unittest.TestCase):
    def test_orphan_duplicate_scope_and_summary_rows(self):
        d = tempfile.mkdtemp(); be = _backend(d)
        _reg(d, SID, "web")
        sb.append_state(Path(d), SID, "working")
        ps = ("  %d 1 /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              "  %d %d bash -c tool\n"
              "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel\n"
              "  %d %d /x/claude --output-format stream-json --resume %s --input-format stream-json\n"
              ) % (CLI, SID, TOOL, CLI, KERNEL, LIVE, KERNEL, SID)
        listing = "romp-session-11111111-%d-1757374800.scope loaded active running claude\n" % CLI
        def run(argv, **kw):
            return mock.Mock(stdout=ps if argv == sb.PS_ARGV else (listing if argv == sb.SCOPE_LIST_ARGV else ""), returncode=0)
        with mock.patch.object(sb.subprocess, "run", side_effect=run), \
             mock.patch.object(sb.os, "kill", side_effect=lambda p, s: None), \
             mock.patch.object(sb.SdkBackend, "_pid_alive", lambda self, p: False), \
             mock.patch.object(sb.SdkBackend, "_ensure", lambda self, sid, **k: None):
            be._boot_reconcile([sb.read_reg(Path(d), SID)])
        rows = _events(d)
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds, ["reconcile.duplicate-cli", "reconcile.orphan-reaped", "reconcile.scope-stopped",
                                 "reconcile.boot"], kinds)
        dup, orphan, scope, boot = rows
        self.assertEqual((dup["sid"], dup["name"], dup["fsid"], dup["n"]), (SID, "web", SID, 2))
        self.assertEqual(dup["pids"], "%d,%d" % (CLI, LIVE), "listing order, before the reap")
        self.assertEqual((orphan["sid"], orphan["name"], orphan["cliPid"], orphan["fsid"]), (SID, "web", CLI, SID))
        self.assertEqual(orphan["tree"], 1, "the orphan's one descendant")
        self.assertEqual(orphan["scope"], "", "no scope: the cgroup seam read nothing (a scope it did not start is treated as none)")
        self.assertEqual((scope["unit"], scope["sid8"], scope["cliPid"]),
                         ("romp-session-11111111-%d-1757374800.scope" % CLI, "11111111", CLI))
        self.assertEqual((boot["sessions"], boot["resumed"], boot["reaped"], boot["scopesStopped"], boot["toStart"]),
                         (1, 1, 1, 1, 1))
        self.assertIn("durationS", boot)
        # the problems reached the ring as prose (no json tail), the summary did not
        texts = [r["text"] for r in _ring(be)]
        self.assertEqual(len(texts), 3, texts)
        self.assertTrue(all(sb.PROBLEM_ROW_MARK not in t for t in texts))
        self.assertTrue(texts[0].startswith("boot: 2 claude processes were holding session web's conversation"))

    def test_quiet_boot_writes_the_summary_alone(self):
        d = tempfile.mkdtemp(); be = _backend(d)
        _reg(d, SID, "web")
        sb.append_state(Path(d), SID, "waiting")
        with mock.patch.object(sb.subprocess, "run", side_effect=lambda argv, **kw: mock.Mock(stdout="", returncode=0)):
            be._boot_reconcile([sb.read_reg(Path(d), SID)])
        rows = _events(d)
        self.assertEqual([r["kind"] for r in rows], ["reconcile.boot"])
        self.assertEqual((rows[0]["sessions"], rows[0]["resumed"], rows[0]["reaped"], rows[0]["toStart"]), (1, 0, 0, 0))
        self.assertEqual(_ring(be), [])


    def test_a_boot_with_no_sessions_writes_nothing(self):
        d = tempfile.mkdtemp(); be = _backend(d)
        with mock.patch.object(sb.subprocess, "run", side_effect=lambda argv, **kw: mock.Mock(stdout="", returncode=0)):
            be._boot_reconcile([])
        self.assertEqual(_events(d), [], "a read-only route's lazy backend build must leave the state dir untouched")
        self.assertFalse((Path(d) / sb.SESSION_EVENTS_FILE).exists())


class CrashRows(unittest.TestCase):
    def test_heal_then_loop(self):
        d = tempfile.mkdtemp(); be = _backend(d)
        _reg(d, SID, "web")
        sess = types.SimpleNamespace(sid=SID, name="web")
        with mock.patch.object(sb.SdkBackend, "_ensure", lambda self, sid, **k: None):
            be._heal_cut_session(sess)
            be._heal_cut_session(sess)
        rows = _events(d)
        self.assertEqual([(r["kind"], r["sid"], r["name"], r["attempt"]) for r in rows],
                         [("crash.heal", SID, "web", 1), ("crash.loop", SID, "web", 2)])
        texts = [r["text"] for r in _ring(be)]
        self.assertEqual(len(texts), 2)
        self.assertIn("resuming with history intact", texts[0])
        self.assertIn("crash loop", texts[1])
        self.assertTrue(all(sb.PROBLEM_ROW_MARK not in t for t in texts))


class DrainRows(unittest.TestCase):
    def test_unjoined_sessions_get_a_row_each(self):
        d = tempfile.mkdtemp(); be = _backend(d)
        class Thr:
            def __init__(self, alive): self._alive = alive
            def join(self, *_): pass
            def is_alive(self): return self._alive
        def sess(sid, name, alive, inflight):
            return types.SimpleNamespace(sid=sid, name=name, inflight=inflight, ended=False, thread=Thr(alive),
                                         shutdown=lambda: None)
        stuck = sess(SID, "web", True, 1)
        clean = sess(SID2, "api", False, 0)
        be.sessions = {SID: stuck, SID2: clean}
        with mock.patch.object(sb.SdkBackend, "_session_cli_pid", lambda self, s: None):
            res = be.drain(0.01)
        self.assertEqual((res["stopped"], res["unjoined"], res["reaped"]), (2, 1, 0))
        rows = _events(d)
        self.assertEqual([(r["kind"], r["sid"], r["name"], r["inflight"], r["reaped"]) for r in rows],
                         [("drain.unjoined", SID, "web", 1, False)])


class LedgerRotation(unittest.TestCase):
    def test_the_rotate_step_runs_under_the_ledger_lock(self):
        """Review find (2026-09-10): two appenders crossing the size together would rotate twice and lose the
        predecessor; the stat, the rename and the append share one lock."""
        d = Path(tempfile.mkdtemp())
        held = []
        class Probe:
            def __enter__(self_):
                held.append("in")
            def __exit__(self_, *a):
                held.append("out")
        with mock.patch.object(sb, "_LEDGER_LOCK", Probe()), mock.patch.object(sb, "LEDGER_ROTATE_BYTES", 10):
            sb.append_turn_row(d, {"t": 1, "sid": SID})
            sb.append_turn_row(d, {"t": 2, "sid": SID})       # rotates
        self.assertEqual(held, ["in", "out", "in", "out"])
        self.assertTrue((d / (sb.TURNS_FILE + ".1")).exists())
        self.assertIsInstance(sb._LEDGER_LOCK, type(sb.threading.Lock()))

    def test_a_full_ledger_rotates_to_one_predecessor(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(sb, "LEDGER_ROTATE_BYTES", 60):
            for i in range(6):
                sb.append_turn_row(d, {"t": i, "sid": SID})          # ~45 bytes a row: rotates every second row
        cur = [json.loads(x) for x in (d / sb.TURNS_FILE).read_text().splitlines()]
        prev = [json.loads(x) for x in (d / (sb.TURNS_FILE + ".1")).read_text().splitlines()]
        self.assertEqual([r["t"] for r in cur], [4, 5])
        self.assertEqual([r["t"] for r in prev], [2, 3], "the older predecessor is dropped, never a third file")
        self.assertFalse((d / (sb.TURNS_FILE + ".2")).exists())


class TurnLedgerRow(unittest.TestCase):
    def _sess(self, fed, first_out=None, fed_t=1700000000.125):
        s = object.__new__(sb.SdkSession)
        s.sid, s.name, s.since = SID, "web", 1700000000
        s._inflight_texts = list(fed)
        s._turn_opener = "human"
        s._first_out_t = first_out
        s._fed_t = fed_t
        return s

    def test_a_turn_the_cli_opened_itself_carries_no_feed_stamps(self):
        """Review find (2026-09-10): a self-opened turn (a channel message, a task notification, a scheduled
        prompt) has nothing fed, and the stamps still in memory belong to the PREVIOUS fed turn, so the row
        must carry neither fedT nor firstOutT rather than hours of feed-to-result."""
        s = self._sess([], first_out=1700000004.25)
        row = s._turn_ledger_row(types.SimpleNamespace(), now=1700003600)
        self.assertNotIn("fedT", row)
        self.assertNotIn("firstOutT", row)
        self.assertEqual(row["fedTexts"], 0)
        # a fed turn whose pop stamp was spent (a mid-turn forward run as its own turn) is unknown too
        s2 = self._sess(["forwarded"], first_out=1700000004.25, fed_t=None)
        self.assertNotIn("fedT", s2._turn_ledger_row(types.SimpleNamespace(), now=1700000010))

    def test_fed_stamp_is_the_pop_at_millisecond_resolution(self):
        s = self._sess(["x"], first_out=1700000004.25, fed_t=1700000000.125)
        row = s._turn_ledger_row(types.SimpleNamespace(), now=1700000012.5)
        self.assertEqual(row["fedT"], 1700000000.125, "the pop's own stamp, not `since` truncated to the second")
        s.since = 1700000000   # `since` stays whole seconds for its other readers and is not what the row reads
        self.assertEqual(s._turn_ledger_row(types.SimpleNamespace(), now=1700000012.5)["fedT"], 1700000000.125)

    def test_feed_stamps_are_spent_at_the_settle(self):
        """The ResultMessage branch resets _fed_t and _first_out_t right after the row, in the finally, ahead of
        the settle; and the row is written from the finally, so a bookkeeping fault (a spend-fold exception)
        still leaves it. Pinned on the source, which is where the ordering lives."""
        import inspect
        src = inspect.getsource(sb.SdkSession._on_message)
        i_fin = src.index("finally:\n                # T304: one durable row per settled turn")
        i_row = src.index("append_turn_row(self.backend.state_dir, self._turn_ledger_row(msg, delta, turn_u))")
        i_reset = src.index("self._fed_t = None               # the turn's feed stamps are spent")
        i_settle = src.index("everything that makes the turn over for the kernel")   # the finally's own comment
        self.assertTrue(i_fin < i_row < i_reset < i_settle, "row, then the reset, then the settle, all inside the finally")
        self.assertIn("elif isinstance(msg, ResultMessage):\n            delta = turn_u = None", src,
                      "the fold's names exist before the branch's try, so the finally can always read them")

    def test_row_fields_and_stamps(self):
        s = self._sess(["do the thing"], first_out=1700000004.25, fed_t=1700000000)
        msg = types.SimpleNamespace(duration_ms=12345, duration_api_ms=9000, num_turns=3, is_error=False)
        row = s._turn_ledger_row(msg, 0.1234567, {"input_tokens": 10, "output_tokens": 20,
                                                   "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40},
                                 now=1700000012.5)
        self.assertEqual(row["sid"], SID)
        self.assertEqual(row["name"], "web")
        self.assertEqual((row["t"], row["fedT"], row["firstOutT"], row["resultT"]),
                         (1700000012, 1700000000, 1700000004.25, 1700000012.5))
        self.assertEqual((row["durationMs"], row["apiMs"], row["numTurns"], row["isError"]), (12345, 9000, 3, False))
        self.assertEqual(row["usd"], 0.123457)
        self.assertEqual((row["tokIn"], row["tokOut"], row["tokCacheR"], row["tokCacheW"]), (10, 20, 30, 40))
        self.assertEqual((row["opener"], row["resumeNotice"], row["fedTexts"]), ("human", False, 1))

    def test_resume_notice_marks_the_redo_turn(self):
        s = self._sess([sb.BOOT_RESUME_NUDGE, "and my queued question"], fed_t=1700000000.5)
        row = s._turn_ledger_row(types.SimpleNamespace(), None, None, now=1700000001)
        self.assertTrue(row["resumeNotice"])
        self.assertEqual(row["fedTexts"], 2)
        self.assertNotIn("firstOutT", row, "no output yet → no stamp, never a zero")
        self.assertNotIn("usd", row, "no spend fold → no cost columns, never zeros")
        s2 = self._sess([sb.CRASH_RESUME_NUDGE])
        self.assertTrue(s2._turn_ledger_row(types.SimpleNamespace(), now=1)["resumeNotice"])

    def test_append_turn_row(self):
        d = tempfile.mkdtemp()
        sb.append_turn_row(Path(d), {"t": 1, "sid": SID})
        sb.append_turn_row(Path(d), {})            # nothing to write
        rows = [json.loads(ln) for ln in (Path(d) / sb.TURNS_FILE).read_text().splitlines()]
        self.assertEqual(rows, [{"t": 1, "sid": SID}])


if __name__ == "__main__":
    unittest.main()
