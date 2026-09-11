#!/usr/bin/env python3
"""T304 (stage 0 of the restart-surviving sessions program, 2026-09-10): the two ledgers the restart monitors
read. session-events.jsonl gets one flat row per thing that went wrong with a session's process (an orphaned
CLI ended at boot, a leftover scope stopped, two CLIs holding one conversation, a crash heal or crash loop, a
session the drain's bound left closing) and one summary row per boot sweep; every problem also lands on the
kernel log as `<prose> ;; problem-row {json}`, and every one but a drain row (written as the kernel exits) on
the backend's problem ring as its prose (the shape agreed with the lease work, which writes its `lease.*`
kinds through the same helper). turns.jsonl gets one row per settled turn with the event stamps the latency
and redo-cost figures read. Synthetic fixtures only: placeholder uuids, fake pids above pid_max, scripted
`run` / `kill` seams, no real CLI."""
import asyncio
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
              "  %d 1 claude --resume %s" % (TOOL, SID),          # a terminal CLI (no stream-json mark): never counted
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
        # the LIVE CLI is a live kernel's child with no lease: kept, and reported by the lease census (T305,
        # the upgrade boot's transitional row) between the duplicate row and the reap
        self.assertEqual(kinds, ["reconcile.duplicate-cli", "lease.cli-without-lease", "reconcile.orphan-reaped",
                                 "reconcile.scope-stopped", "reconcile.boot"], kinds)
        dup, unleased, orphan, scope, boot = rows
        self.assertEqual((unleased["sid"], unleased["cliPid"], unleased["fsid"]), (SID, LIVE, SID))
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
        self.assertEqual(len(texts), 4, texts)
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
    def _drain(self, be, cli_pid=None):
        """Drain two doubles: `web` still closing with a turn in flight, `api` joined clean. `cli_pid` is
        what the CLI-pid seam answers for the stuck one; a stub `kill` (nothing real is signalled) reports the
        process gone at the first existence poll."""
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
        def kill(pid, sig):
            if sig == 0:
                raise ProcessLookupError()
        with mock.patch.object(sb.SdkBackend, "_session_cli_pid", lambda self, s: cli_pid):
            return be.drain(0.01, kill=kill)

    def test_unjoined_sessions_get_a_row_each(self):
        d = tempfile.mkdtemp(); be = _backend(d)
        res = self._drain(be)
        self.assertEqual((res["stopped"], res["unjoined"], res["reaped"]), (2, 1, 0))
        rows = _events(d)
        self.assertEqual([(r["kind"], r["sid"], r["name"], r["inflight"], r["reaped"]) for r in rows],
                         [("drain.unjoined", SID, "web", 1, False)])

    def test_unjoined_row_is_a_problem_row_without_the_ring(self):
        """A drain.unjoined row is a problem row like every kind but the boot summary: it carries `text` and
        the kernel log gets `<prose> ;; problem-row {json}`. The ring alone is not asked, since the kernel is
        exiting and the bell has no reader for it."""
        d = tempfile.mkdtemp(); lines = []
        be = _backend(d, log=lambda m: lines.append(m))
        self._drain(be)
        (row,) = _events(d)
        self.assertEqual(row["kind"], "drain.unjoined")
        self.assertIsInstance(row.get("text"), str)
        self.assertIn("web", row["text"])
        self.assertNotIn("ended", row["text"], "not reaped: the prose does not say its process was ended")
        marked = [m for m in lines if sb.PROBLEM_ROW_MARK in m]
        self.assertEqual(len(marked), 1, lines)
        self.assertEqual(sb.parse_problem_row(marked[0]), row, "the log line carries the same object")
        self.assertTrue(marked[0].startswith(row["text"] + sb.PROBLEM_ROW_MARK))
        self.assertTrue(any(m.startswith("drain: stopped 2 session(s)") for m in lines),
                        "the drain's own summary still logs")
        self.assertEqual(_ring(be), [])

    def test_reaped_row_says_so_in_its_prose(self):
        d = tempfile.mkdtemp(); lines = []
        be = _backend(d, log=lambda m: lines.append(m))
        res = self._drain(be, cli_pid=CLI)
        self.assertEqual((res["unjoined"], res["reaped"]), (1, 1))
        (row,) = _events(d)
        self.assertEqual((row["kind"], row["reaped"]), ("drain.unjoined", True))
        self.assertIn("ended", row["text"])
        self.assertEqual(sb.parse_problem_row([m for m in lines if sb.PROBLEM_ROW_MARK in m][0]), row)
        self.assertEqual(_ring(be), [])


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

    def test_the_result_branch_stays_one_try(self):
        """The ResultMessage branch is ONE try: its body is the bookkeeping, its finally the settle. Two things
        about its shape stay pinned on the source, where they live: the one try itself (the branch's own
        contract, and why the spend accounting hands its figures to the finally by attribute), and the feed
        stamps spent ahead of the settle inside that finally (an order nothing outside it can observe: the two
        are plain assignments with no await between them). What the finally DOES is driven in TurnLedgerDriven:
        the row written with the accounting's figures whatever the bookkeeping did (so the figures were handed
        over before the spend write), the stamps None afterwards, and the settle run."""
        import inspect
        src = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn("elif isinstance(msg, ResultMessage):\n            try:", src,
                      "the branch stays ONE try (the settle pins), so the fold hands its figures over by attribute")
        self.assertLess(src.index("self._fed_t = None"), src.index("self.inflight = 0"),
                        "the feed stamps are spent ahead of the settle, both inside the finally")

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


# ---- message doubles for the driven tests: the SDK's shapes by class name (msg_to_atom and _on_message key on it) ----
class _TextBlock:
    def __init__(self, text): self.text = text
class _AssistantMessage:
    def __init__(self, content, uuid="a1"):
        self.content, self.model, self.uuid, self.stop_reason, self.error = content, "claude-x", uuid, "end_turn", None
class _UserMessage:
    def __init__(self, content, uuid="u1"):
        self.content, self.uuid, self.parent_tool_use_id, self.tool_use_result = content, uuid, None, None
class _ResultMessage:
    uuid = "r1"
    num_turns = 1
def _result(**fields):
    """A ResultMessage double carrying result fields (total_cost_usd, usage, ...): a subclass, so the
    handler's isinstance and the failure report's class-name read both see a ResultMessage."""
    return type("ResultMessage", (_ResultMessage,), dict(fields))()
class _SystemMessage:
    def __init__(self, subtype, data=None, uuid=None):
        self.subtype, self.data, self.uuid = subtype, (data or {}), uuid
_TextBlock.__name__ = "TextBlock"; _AssistantMessage.__name__ = "AssistantMessage"
_UserMessage.__name__ = "UserMessage"; _ResultMessage.__name__ = "ResultMessage"; _SystemMessage.__name__ = "SystemMessage"


async def _noop_coro(*a, **k):
    return None


class TurnLedgerDriven(unittest.TestCase):
    """The stamps and the row, driven through the real code paths on a real SdkSession (never started: no
    CLI, no thread) over a hermetic backend. TurnLedgerRow above reads the row off a preset double; these
    reach the two writers of the stamps it presets, and the finally that writes the row and spends them."""

    def _session(self, be, sid=SID, name="web"):
        s = sb.SdkSession(be, {"sid": sid, "name": name, "cwd": "/tmp"})
        s._do_refresh_context = _noop_coro    # the settle schedules both; there is no client to ask
        s._do_refresh_usage = _noop_coro
        return s

    def test_forward_stamps_the_first_work_atom_once(self):
        """firstOutT is the turn's FIRST streamed work atom while a fed turn is in flight: a second atom keeps
        the first's stamp, nothing in flight stamps nothing, and a command line (a streamed /model
        confirmation) is not work."""
        be = _backend(tempfile.mkdtemp())
        s = self._session(be)
        s.inflight = 1
        self.assertIsNone(s._first_out_t)
        be._forward(s, _AssistantMessage([_TextBlock("working on it")], uuid="w1"))
        first = s._first_out_t
        self.assertIsInstance(first, float)
        be._forward(s, _AssistantMessage([_TextBlock("still at it")], uuid="w2"))
        self.assertEqual(s._first_out_t, first, "the first atom's stamp holds for the turn")
        other = self._session(be, sid=SID2, name="api")
        be._forward(other, _AssistantMessage([_TextBlock("idle chatter")], uuid="w3"))
        self.assertIsNone(other._first_out_t, "nothing in flight: not a fed turn's first output")
        other.inflight = 1
        cmd = _UserMessage([_TextBlock("<command-name>/model</command-name>\n"
                                       "<command-message>model</command-message>\n"
                                       "<command-args>sonnet</command-args>")], uuid="c1")
        be._forward(other, cmd)
        self.assertIsNone(other._first_out_t, "a command line is not the turn's output")
        be._forward(other, _AssistantMessage([_TextBlock("now working")], uuid="w4"))
        self.assertIsInstance(other._first_out_t, float, "the next work atom is")

    def test_the_settle_writes_the_row_and_spends_the_stamps_when_the_spend_write_raises(self):
        """The ResultMessage branch's finally writes the turn's row and then spends the feed stamps, ahead of
        the settle, whatever the bookkeeping did: here the spend write raises (the anomalous turn is exactly
        the one the row must keep), the row still lands with the accounting's figures (the spend tuple is
        set before the raising write), the stamps are None afterwards, and the settle ran. The fault itself is
        contained by _handle_stream_message, which reports it and returns False."""
        d = tempfile.mkdtemp()
        be = _backend(d)
        s = self._session(be)
        s.inflight = 1
        s._inflight_texts.append("do the thing")
        s._fed_t = 1700000000.125
        s._first_out_t = 1700000004.25

        def boom(*a, **k):
            raise RuntimeError("boom")
        be._record_spend = boom
        msg = _result(total_cost_usd=0.5, usage={"input_tokens": 1, "output_tokens": 2},
                      duration_ms=10, duration_api_ms=8, num_turns=1, is_error=False)

        async def run():
            ok = s._handle_stream_message(msg, _AssistantMessage, _ResultMessage, _SystemMessage)
            await asyncio.sleep(0)
            return ok
        ok = asyncio.run(run())
        self.assertFalse(ok, "the spend write's fault is contained and reported, not swallowed")
        rows = [json.loads(ln) for ln in (Path(be.state_dir) / sb.TURNS_FILE).read_text().splitlines()]
        self.assertEqual(len(rows), 1, "one row for the settled turn, written from the finally")
        row = rows[0]
        self.assertEqual((row["fedT"], row["firstOutT"], row["fedTexts"]), (1700000000.125, 1700000004.25, 1))
        self.assertEqual((row["usd"], row["durationMs"], row["apiMs"], row["tokIn"], row["tokOut"]), (0.5, 10, 8, 1, 2),
                         "the accounting's figures reached the row although the spend write raised")
        self.assertIsNone(s._fed_t, "the feed stamps are spent with the row")
        self.assertIsNone(s._first_out_t)
        self.assertIsNone(s._turn_spend)
        self.assertEqual((s.inflight, list(s._inflight_texts)), (0, []), "the settle ran")
        self.assertEqual(sb.last_state_value(Path(be.state_dir), SID), "waiting")


if __name__ == "__main__":
    unittest.main()
