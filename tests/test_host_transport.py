#!/usr/bin/env python3
"""The kernel's side of the per-session host (T315, stage 4 of #1317): the HostTransport over a fake host
socket and over an orphan journal, the settings, the spawn specification, the host scopes in the sweep,
the six-method Transport pin, the spec-tracks-the-SDK pin, and the backend wiring pins.

Hermetic: temp state roots, a fake host server inside the test (an asyncio Unix server speaking the
frame protocol), synthetic ids, no real CLI. Tests needing the SDK skip without it.
"""
import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"          # no scopes: a test's children sit in the tester's own scope
# the SDK, when this machine has the venv bin/romp-sdk-setup builds (the kernel's own _ensure_sdk_on_path
# does the same at boot); CI has none and the SDK-gated tests skip there
if importlib.util.find_spec("claude_agent_sdk") is None:
    _tag = "python%d.%d" % sys.version_info[:2]
    for _sp in sorted(Path(os.path.expanduser("~/.local/state/romp/sdkvenv/lib")).glob(_tag + "/site-packages")):
        sys.path.insert(0, str(_sp))
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))
ht = sb._ht()
sh = ht.sh
SDK = importlib.util.find_spec("claude_agent_sdk") is not None
SID = "11111111-2222-3333-4444-0000000000b1"


class Settings(unittest.TestCase):
    def test_hosts_default_off_and_the_grace_default(self):
        d = tempfile.mkdtemp()
        self.assertFalse(ht.session_hosts_on(d))
        self.assertEqual(ht.session_host_grace_s(d), sh.UNATTACHED_GRACE_DEFAULT_S)
        Path(d, "session-hosts").write_text("on\n")
        Path(d, "session-host-grace").write_text("120")
        self.assertTrue(ht.session_hosts_on(d))
        self.assertEqual(ht.session_host_grace_s(d), 120.0)
        Path(d, "session-host-grace").write_text("junk")
        self.assertEqual(ht.session_host_grace_s(d), sh.UNATTACHED_GRACE_DEFAULT_S, "junk falls back, loudly enough by being the default")


class SpawnSpec(unittest.TestCase):
    def test_the_spec_carries_the_plain_fields_and_the_permission_tool_and_never_callables(self):
        opts = types.SimpleNamespace(cli_path="/x/romp-cli-scope", cwd=Path("/tmp/proj"), env={"ROMP_SID": SID, "ROMP_CLI_REAL": "/x/claude"},
                                     permission_mode="default", resume=SID, extra_args={"resume-session-at": "u1"},
                                     mcp_servers="/x/postal.json", system_prompt={"type": "preset", "preset": "claude_code", "append": "hi"},
                                     model="m", effort="high", include_partial_messages=False, enable_file_checkpointing=True,
                                     max_buffer_size=100, can_use_tool=lambda *a: None, hooks={"Stop": []}, stderr=lambda l: None,
                                     permission_prompt_tool_name=None, session_id=None)
        spec = ht.spawn_spec(opts, SID, "web", "/state", "abc12345", 900)
        self.assertEqual(spec["permission_prompt_tool_name"], "stdio", "the callback's presence becomes the flag")
        self.assertEqual((spec["cwd"], spec["resume"], spec["extra_args"], spec["sid"], spec["version"]),
                         ("/tmp/proj", SID, {"resume-session-at": "u1"}, SID, "abc12345"))
        self.assertNotIn("hooks", spec); self.assertNotIn("stderr", spec); self.assertNotIn("can_use_tool", spec)
        self.assertEqual(spec["hook_timeout_s"], sh.HOOK_TIMEOUT_S)
        d = tempfile.mkdtemp()
        p = ht.write_spawn_spec(d, SID, spec)
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(p.parent).st_mode & 0o777), "0o700")
        self.assertEqual(json.loads(p.read_text())["env"]["ROMP_SID"], SID)

    @unittest.skipUnless(SDK, "the SDK is not importable here")
    def test_the_spec_fields_track_what_the_sdk_transport_reads(self):
        import claude_agent_sdk._internal.transport.subprocess_cli as scli
        import inspect
        src = inspect.getsource(scli.SubprocessCLITransport._build_command) + inspect.getsource(scli.SubprocessCLITransport.connect)
        read = set(re.findall(r"self\._options\.([a-z_]+)", src)) | {"cwd"}
        callables_or_sdk_side = {"stderr", "user", "session_store", "sandbox", "task_budget", "max_budget_usd", "betas",
                                 "plugins", "agents", "output_format", "tools", "thinking", "max_thinking_tokens",
                                 "include_hook_events", "strict_mcp_config", "resume_drops_turn", "permission_prompt_tool_name"}
        missing = read - set(sh.SPEC_FIELDS) - callables_or_sdk_side
        self.assertEqual(missing, set(), "fields the SDK reads that the spec does not carry: %r" % sorted(missing))


class HostScopes(unittest.TestCase):
    def test_host_scope_units_match_our_sessions_only(self):
        u = ht.host_scope_unit(SID, 1757374800000)
        self.assertEqual(u, "romp-host-11111111-1757374800000")
        listing = ["%s.scope loaded active running x" % u, "romp-host-99999999-1.scope loaded active running y",
                   "romp-session-11111111-4242-1.scope loaded active running z"]
        self.assertEqual(ht.host_scope_units(listing, [SID]), {u + ".scope": "11111111"})

    def test_the_sweep_stops_a_host_scope_whose_lease_does_not_hold_and_spares_a_live_one(self):
        d = tempfile.mkdtemp(); be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        live, dead = SID, "22222222-2222-3333-4444-0000000000b2"
        sb.write_lease(d, {"sid": live, "fsid": live, "pid": 999999999, "start": "1", "holder": {"pid": 999999998, "start": "2", "kind": "host"}, "version": "", "t": time.time()})
        sb.write_lease(d, {"sid": dead, "fsid": dead, "pid": 999999997, "start": "1", "holder": {"pid": 999999996, "start": "2", "kind": "host"}, "version": "", "t": time.time()})
        starts = {999999999: "1", 999999998: "2"}      # the live pair is alive; the dead host's pids are gone
        listing = "romp-host-11111111-1.scope loaded active running a\nromp-host-22222222-2.scope loaded active running b\n"
        runs = []
        def run(argv, **kw):
            runs.append(list(argv)); return mock.Mock(stdout=listing if argv == sb.HOST_SCOPE_LIST_ARGV else "", returncode=0)
        with mock.patch.object(sb, "proc_start", lambda p, run=None: starts.get(p)):
            n = be._stop_leftover_scopes([live, dead], run=run)
        stops = [a[-1] for a in runs if a[:3] == ["systemctl", "--user", "stop"]]
        self.assertEqual((n, stops), (1, ["romp-host-22222222-2.scope"]))


class LeaseClassification(unittest.TestCase):
    def test_attach_orphan_none(self):
        now = 1000.0
        st = lambda starts: (lambda p: starts.get(p))
        host_lease = {"sid": SID, "pid": 5, "start": "a", "holder": {"pid": 6, "start": "b", "kind": "host"}, "t": now}
        self.assertEqual(ht.host_lease_state(host_lease, now, st({5: "a", 6: "b"})), "attach")
        self.assertEqual(ht.host_lease_state(host_lease, now, st({5: "a"})), "orphan", "the host is gone")
        self.assertEqual(ht.host_lease_state(dict(host_lease, t=now - 100), now, st({5: "a", 6: "b"})), "orphan", "a stale beat")
        kernel_lease = dict(host_lease, holder={"pid": 6, "start": "b"})
        self.assertEqual(ht.host_lease_state(kernel_lease, now, st({5: "a", 6: "b"})), "none")
        self.assertEqual(ht.host_lease_state(None, now), "none")


# ── the transport against a fake host ──────────────────────────────────────────────────────────
class FakeHost:
    """An asyncio Unix server speaking the host's frames from a scripted journal."""

    def __init__(self, path, records, busy=False, exit_after=None):
        self.path, self.records, self.busy, self.exit_after = path, records, busy, exit_after
        self.got = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, path=self.path)

    async def _client(self, reader, writer):
        fr = sh.FrameReader()
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                for f in fr.feed(chunk):
                    self.got.append(f)
                    if f["t"] == "attach":
                        if self.busy:
                            writer.write(sh.encode_frame({"t": "busy", "kernel": {"pid": 7}})); await writer.drain(); continue
                        writer.write(sh.encode_frame({"t": "hello", "protocol": 1, "host": {"pid": 10, "start": "h", "version": "v"},
                                                      "cli": {"pid": 11, "start": "c", "fsid": SID}, "journal": {"next": len(self.records)}, "parked": []}))
                        for off, rec in enumerate(self.records):
                            if off > int(f.get("ack", -1)):
                                writer.write(sh.encode_frame({"t": "out", "offset": off, "data": rec}))
                        writer.write(sh.encode_frame({"t": "stderr", "line": "a stderr line"}))
                        if self.exit_after is not None:
                            writer.write(sh.encode_frame({"t": "exit", "code": self.exit_after, "cause": "died"}))
                        await writer.drain()
                    elif f["t"] == "in":
                        obj = json.loads(f["data"])
                        if obj.get("type") == "control_request":
                            writer.write(sh.encode_frame({"t": "out", "offset": len(self.records), "data": {
                                "type": "control_response", "response": {"subtype": "success", "request_id": obj["request_id"], "response": {}}}}))
                            await writer.drain()
                    elif f["t"] == "ping":
                        writer.write(sh.encode_frame({"t": "pong"})); await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            return

    def close(self):
        if self.server:
            self.server.close()


def run(coro):
    return asyncio.run(coro)


class TransportOverSocket(unittest.TestCase):
    def _path(self):
        d = tempfile.mkdtemp(dir=os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") or None)
        return os.path.join(d, "h.sock")

    def test_attach_replays_from_the_ack_then_acks_and_side_frames_reach_their_callbacks(self):
        recs = [{"type": "system", "subtype": "init", "session_id": SID}, {"type": "assistant", "n": 1}, {"type": "result", "n": 2}]
        async def go():
            fh = FakeHost(self._path(), recs); await fh.start()
            acks, stderr, hellos = [], [], []
            t = ht.HostTransport(fh.path, kernel={"pid": 1, "start": "k", "version": "v"}, ack=0, on_ack=acks.append,
                                 on_stderr=stderr.append, on_hello=hellos.append)
            await t.connect()
            self.assertTrue(t.is_ready()); self.assertEqual(hellos[0]["cli"]["pid"], 11)
            got = []
            async def read():
                async for m in t.read_messages():
                    got.append(m)
                    if len(got) == 2:
                        await t.write(json.dumps({"type": "control_request", "request_id": "req_1_x", "request": {"subtype": "initialize"}}))
                    if len(got) == 3:
                        break
            await asyncio.wait_for(read(), 5)
            self.assertEqual([m.get("n") for m in got[:2]], [1, 2], "replay started after the acknowledged offset 0")
            self.assertEqual(got[2]["type"], "control_response", "a write is forwarded as an `in` frame and answered live")
            self.assertEqual(stderr, ["a stderr line"])
            self.assertEqual(acks[-1], 3)
            t.detach_mode = True
            await t.end_input()                       # detach mode: no `end` goes out
            await t.close()
            await asyncio.sleep(0.1)
            kinds = [f["t"] for f in fh.got]
            self.assertIn("detach", kinds); self.assertNotIn("end", kinds)
            self.assertEqual(fh.got[0]["ack"], 0)
            fh.close()
        run(go())

    def test_busy_refuses_and_a_non_zero_exit_raises_like_the_subprocess_transport(self):
        async def go():
            fh = FakeHost(self._path(), [], busy=True); await fh.start()
            t = ht.HostTransport(fh.path, kernel={"pid": 1})
            with self.assertRaises(ht.CLIConnectionError):
                await t.connect()
            fh.close()
            fh2 = FakeHost(self._path(), [{"type": "assistant"}], exit_after=3); await fh2.start()
            t2 = ht.HostTransport(fh2.path, kernel={"pid": 1})
            await t2.connect()
            got = []
            with self.assertRaises(ht.ProcessError):
                async for m in t2.read_messages():
                    got.append(m)
            self.assertEqual(len(got), 1); self.assertEqual(t2.exit_info["code"], 3)
            fh2.close()
        run(go())

    def test_end_input_and_close_send_end_with_the_grace_when_not_detaching(self):
        async def go():
            fh = FakeHost(self._path(), []); await fh.start()
            t = ht.HostTransport(fh.path, kernel={"pid": 1}, end_grace=7)
            await t.connect()
            await t.end_input()
            await asyncio.sleep(0.1)
            ends = [f for f in fh.got if f["t"] == "end"]
            self.assertEqual(ends[0]["grace"], 7)
            await t.signal("INT")
            await asyncio.sleep(0.1)
            self.assertIn({"t": "signal", "sig": "INT"}, fh.got)
            fh.close()
        run(go())


class TransportOverJournal(unittest.TestCase):
    def test_the_replay_answers_the_initialize_and_yields_the_journal_then_ends(self):
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        for rec in ({"type": "system", "subtype": "init"}, {"type": "assistant", "n": 1}, {"type": "result", "n": 2}):
            j.append(rec)
        j.close()
        async def go():
            acks = []
            t = ht.HostTransport.from_journal(d, ack=0, on_ack=acks.append)
            await t.connect()
            got = []
            async def read():
                async for m in t.read_messages():
                    got.append(m)
            reader = asyncio.ensure_future(read())
            await asyncio.sleep(0.05)
            await t.write(json.dumps({"type": "control_request", "request_id": "req_0_i", "request": {"subtype": "initialize"}}))
            await t.write(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}))   # dropped, counted
            await asyncio.wait_for(reader, 10)
            kinds = [m["type"] for m in got]
            self.assertEqual(kinds, ["assistant", "result", "control_response"])
            self.assertEqual(got[2]["response"]["request_id"], "req_0_i")
            self.assertEqual(acks, [1, 2]); self.assertEqual(t.dropped_writes, 1)
            self.assertEqual(t.exit_info["cause"], "replay-end")
            await t.close()
        run(go())


class Pins(unittest.TestCase):
    @unittest.skipUnless(SDK, "the SDK is not importable here")
    def test_host_transport_implements_the_sdks_six_transport_methods(self):
        from claude_agent_sdk._internal.transport import Transport
        abstract = set(getattr(Transport, "__abstractmethods__", set()))
        self.assertEqual(abstract, {"connect", "write", "read_messages", "close", "is_ready", "end_input"})
        self.assertTrue(issubclass(ht.HostTransport, Transport))
        self.assertEqual(set(getattr(ht.HostTransport, "__abstractmethods__", set())), set())

    def test_backend_wiring(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
        self.assertIn("async with ClaudeSDKClient(options=opts, transport=transport) as client:", src)
        self.assertIn("transport = await self.backend._host_transport_for(self, opts,", src)
        self.assertIn("if not sess.ended and not sess.detached:", src, "a latched detach is not a crash")
        self.assertIn("s._host.detach_mode = True", src, "the drain detaches")
        self.assertIn('for s in sessions if s.inflight and getattr(s, "_host", None) is None]', src, "an attached session is never a cut")
        self.assertIn("s._host.end_grace = _ht().sh.END_GRACE_KILL_S", src, "kill gets the short bound")
        self.assertIn('== "attach":', src, "boot attach-first")
        self.assertIn('append_session_event(self.state_dir, "host.attached"', src)
        self.assertIn("m.timeout = _ht().sh.HOOK_TIMEOUT_S", src, "hooks carry the bound under a host")

    def test_a_backend_with_hosts_off_touches_no_host_code_at_construction(self):
        d = tempfile.mkdtemp(); be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        self.assertFalse(be.session_hosts_on())

    def test_host_log_rows_are_filed_once_per_line(self):
        d = tempfile.mkdtemp(); logs = []
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=logs.append)
        sess = types.SimpleNamespace(sid=SID, name="web", _host=None)
        hd = ht.host_dir(d, SID); hd.mkdir(parents=True)
        (hd / "host.log").write_text(json.dumps({"t": 1, "kind": "attached"}) + "\n"
                                     + json.dumps({"t": 2, "kind": "hook-self-answered", "event": "Stop", "callbackId": "hook_0", "parkedS": 480}) + "\n"
                                     + json.dumps({"t": 3, "kind": "end-forced", "cliPid": 5}) + "\n")
        be._file_host_log_rows(sess)
        rows = [json.loads(l) for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["host.hook-self-answered", "host.end-forced"])
        self.assertEqual((rows[0]["sid"], rows[0]["event"], rows[0]["t"]), (SID, "Stop", 2))
        be._file_host_log_rows(sess)
        rows2 = [json.loads(l) for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines()]
        self.assertEqual(len(rows2), 2, "no line is filed twice")
        self.assertEqual(sum(1 for l in logs if "hook" in l and "itself" in l), 1)


@unittest.skipUnless(SDK, "the SDK is not importable here (the end-to-end run needs ClaudeSDKClient)")
class EndToEnd(unittest.TestCase):
    """A backend with hosts ON drives the fake CLI through a real host: a turn started under one kernel finishes
    under the next after a drain that detaches instead of cutting, and a kill ends the host's CLI gracefully.
    Hermetic: a private state root, the fake CLI as claude_bin, no scopes, every process ended by the test."""
    FAKE = os.path.join(HERE, "fixtures", "fake_claude.py")

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(self._sweep)
        Path(self.d, "session-hosts").write_text("on")
        Path(self.d, "session-host-grace").write_text("600")
        self.sid = str(__import__("uuid").uuid4())
        for sub in ("sdk", "states", "names"):
            os.makedirs(os.path.join(self.d, sub), exist_ok=True)
        cwd = os.path.join(self.d, "proj"); os.makedirs(cwd)
        sb.write_reg(Path(self.d), self.sid, {"sid": self.sid, "name": "web", "cwd": cwd, "alive": True, "mode": "bypassPermissions",
                                              "effort": "high", "lastSid": self.sid})
        self.logs = []

    def _sweep(self):
        for be in getattr(self, "_bes", []):
            try:
                be.drain(timeout=3)
            except Exception:
                pass
        lease = sb.read_lease(self.d, self.sid)
        if lease:
            for key in ("holder", None):
                pid = (lease.get(key) or {}).get("pid") if key else lease.get("pid")
                try:
                    os.kill(int(pid), 9)
                except Exception:
                    pass

    def _backend(self):
        be = sb.SdkBackend(self.d, self.FAKE, lambda *a, **k: None, log=self.logs.append, code_version="t315")
        self._bes = getattr(self, "_bes", []) + [be]
        return be

    def _wait(self, pred, timeout=20, what=""):
        deadline = time.time() + timeout
        while time.time() < deadline:   # loop-ok: a bounded wait on an observable event
            if pred():
                return True
            time.sleep(0.1)
        self.fail("timed out waiting for %s\nlog tail: %s" % (what, "\n".join(self.logs[-15:])))

    def test_a_turn_survives_a_drain_and_finishes_under_the_next_backend(self):
        be = self._backend()
        self.assertTrue(be.send(self.sid, "start long sleep=6"))
        self._wait(lambda: (sb.read_lease(self.d, self.sid) or {}).get("holder", {}).get("kind") == "host", what="a host-held lease")
        lease = sb.read_lease(self.d, self.sid)
        self._wait(lambda: len(list(sh.read_journal_dir(ht.host_dir(self.d, self.sid)))) >= 2, what="the init and assistant records in the journal")
        self._wait(lambda: isinstance((sb.read_reg(Path(self.d), self.sid) or {}).get("hostAck"), dict), what="hostAck in the registry")
        res = be.drain(timeout=5)
        self.assertEqual(res["cutTurns"], [], "an attached session is detached, never cut")
        self.assertEqual(res["reaped"], 0)
        time.sleep(0.5)
        lease2 = sb.read_lease(self.d, self.sid)
        self.assertIsNotNone(lease2, "the host keeps its lease across the kernel's drain")
        self.assertEqual((lease2["pid"], lease2["holder"]["pid"]), (lease["pid"], lease["holder"]["pid"]), "same CLI, same host")
        self.assertEqual(sb.lease_state(lease2, time.time()), "valid")
        # the next kernel: boot attach-first
        import subprocess as _sp
        ps_lines = _sp.run(sb.PS_ARGV, capture_output=True, text=True, timeout=10).stdout.splitlines()
        census = sb.lease_census(ps_lines, [self.sid], os.getpid(), sb.list_leases(self.d), version="t315")
        mine = [l for l in ps_lines if self.sid in l]
        self.assertEqual(census["problems"], [], "the census before the second boot: owned=%r orphans=%r dead=%r; ps lines: %r; lease=%r"
                         % (census["owned"], census["orphans"], census["dead_leases"], mine, sb.read_lease(self.d, self.sid)))
        be2 = self._backend()
        be2._boot_reconcile([sb.read_reg(Path(self.d), self.sid)])
        self._wait(lambda: sum(1 for l in self._events() if l.get("kind") == "host.attached") >= 2, what="the second host.attached row")
        att = [l for l in self._events() if l.get("kind") == "host.attached"]
        self.assertEqual([a["boot"] for a in att], [False, True], "the first backend attached at spawn, the second at boot; log: %s"
                         % "\n".join(l for l in self.logs if "boot reconcile" in l or "host (" in l)[-2500:])
        self.assertGreater(att[1]["replayFrom"], 0, "the boot attach replayed from the acknowledged offset, not from zero")
        self._wait(lambda: sb.last_state_value(Path(self.d), self.sid) == "waiting", timeout=30, what="the turn's result under the second backend")
        self.assertEqual(sb.read_lease(self.d, self.sid)["pid"], lease["pid"], "one CLI process the whole way: one writer")
        self.assertFalse(any("restarted" in t for t in ((sb.read_reg(Path(self.d), self.sid) or {}).get("queue") or [])),
                         "no continuation notice for a turn that was never cut")
        # kill: end with the short bound; the host ends the CLI and leaves, the lease goes
        be2.kill(self.sid)
        self._wait(lambda: sb.read_lease(self.d, self.sid) is None, timeout=20, what="the lease removed after kill")
        kinds = [l["kind"] for l in self._events()]
        self.assertNotIn("host.died", kinds)

    def _events(self):
        p = Path(self.d) / sb.SESSION_EVENTS_FILE
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


if __name__ == "__main__":
    unittest.main()
