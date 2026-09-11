#!/usr/bin/env python3
"""The per-session host (T315, stage 4 of the restart-surviving sessions program): the pure pieces
(frames, the journal, the parked table, the neutral hook answers) and the host as a real process driving
the fake CLI (tests/fixtures/fake_claude.py) while this test plays the kernel over the Unix socket.

Hermetic: a temp state root per test, the fake CLI on a temp path, no scopes (the host is a plain child
here), every process killed by the test, synthetic ids. The host runs on its built-in pipe transport when
the SDK is not importable (CI, the plain test venv); one test runs the SDK transport when the machine has
the SDK venv, and skips otherwise.
"""
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the loads
os.environ.pop("ROMP_STATE_DIR", None)
sh = load_source("romp_session_host", os.path.join(ROOT, "kernel", "session_host.py"))
sb = load_source("romp_sdk_backend_host", os.path.join(BIN, "romp_sdk_backend.py"))
FAKE = os.path.join(HERE, "fixtures", "fake_claude.py")
SDK_SITE = next(iter(sorted(Path(os.path.expanduser("~/.local/state/romp/sdkvenv/lib")).glob(
    "python%d.%d/site-packages" % sys.version_info[:2]))), None) if os.path.isdir(os.path.expanduser("~/.local/state/romp/sdkvenv")) else None
SID = "11111111-2222-3333-4444-0000000000a1"          # the romp sid the lease is filed under
FSID = "11111111-2222-3333-4444-0000000000f1"         # the fake CLI's own conversation id (distinct on purpose)


class Frames(unittest.TestCase):
    def test_frames_round_trip_and_junk_is_dropped(self):
        fr = sh.FrameReader()
        a = sh.encode_frame({"t": "ping"}) + b"not json\n" + sh.encode_frame({"t": "in", "data": "x"})[:5]
        got = fr.feed(a)
        self.assertEqual(got, [{"t": "ping"}])
        got = fr.feed(sh.encode_frame({"t": "in", "data": "x"})[5:] + b"\n\n")
        self.assertEqual(got, [{"t": "in", "data": "x"}])


class JournalRules(unittest.TestCase):
    def test_offsets_are_ordinals_and_reads_start_anywhere(self):
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        for i in range(5):
            self.assertEqual(j.append({"type": "assistant", "n": i}), i)
        self.assertEqual([o for o, _ in j.read_from(0)], [0, 1, 2, 3, 4])
        self.assertEqual([r["n"] for _, r in j.read_from(3)], [3, 4])
        self.assertEqual(list(j.read_from(5)), [])
        self.assertEqual([r["n"] for _, r in sh.read_journal_dir(d, 2)], [2, 3, 4], "the orphan reader agrees")

    def test_segments_rotate_at_a_turn_boundary_and_acked_ones_are_dropped(self):
        d = tempfile.mkdtemp(); j = sh.Journal(d, segment_bytes=200)
        for i in range(6):
            j.append({"type": "assistant", "pad": "x" * 60, "n": i})
        self.assertEqual(j.segments(), [0], "no rotation before a result record")
        j.append({"type": "result", "n": 6})
        self.assertEqual(j.segments(), [0, 7], "a result past the size starts a new segment, named by its first offset")
        self.assertTrue((Path(d) / "journal-7.jsonl").exists())
        for i in range(7, 10):
            j.append({"type": "assistant", "n": i})
        j.ack(6)                                    # everything in the first segment
        j.append({"type": "result", "n": 10})
        self.assertEqual(j.segments(), [7], "a fully acknowledged, non-current segment is deleted at the next boundary")
        self.assertEqual([r["n"] for _, r in j.read_from(7)], [7, 8, 9, 10], "the rest still reads")
        self.assertEqual([r["n"] for _, r in j.read_from(0)], [7, 8, 9, 10], "a read below the dropped segment skips it")
        # the ORPHAN reader numbers records from the surviving segment's first offset, not from zero (the
        # review's finding 1: an orphan replay after a deletion used to misnumber the very records it exists for)
        self.assertEqual([(o, r["n"]) for o, r in sh.read_journal_dir(d, 0)], [(7, 7), (8, 8), (9, 9), (10, 10)])
        self.assertEqual([o for o, _ in sh.read_journal_dir(d, 9)], [9, 10])

    def test_a_failed_raw_write_leaves_nothing_behind_and_the_numbering_holds(self):
        # the commit-5 review's finding a: a buffered handle kept a failed write's bytes and landed them later at a
        # stale position; the journal now writes unbuffered and truncates back to the last good position
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        j.append({"type": "assistant", "n": 0})
        real = j._fh
        class Raw:
            def __init__(self): self.failed = False
            def write(self, b):
                if not self.failed:
                    self.failed = True
                    raise OSError(28, "no space left on device")
                return real.write(b)
            def fileno(self): return real.fileno()
            def seek(self, *a): return real.seek(*a)
            def tell(self): return real.tell()
            def close(self): return real.close()
        j._fh = Raw()
        size_before = os.path.getsize(j._path(0))
        with self.assertRaises(OSError):
            j.append({"type": "assistant", "n": 1})
        self.assertEqual((j.next_offset, len(j._index), os.path.getsize(j._path(0))), (1, 1, size_before), "nothing moved on the failure")
        j.append({"type": sh.GAP_TYPE, "offset": 1})          # the writer's gap marker takes offset 1
        j.append({"type": "assistant", "n": 2})
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(0)], [(0, 0), (2, 2)], "the marker is skipped, the numbering holds")
        self.assertEqual([(o, r["n"]) for o, r in sh.read_journal_dir(d, 0)], [(0, 0), (2, 2)], "the orphan reader agrees")
        # finding b: a marker that fails too becomes a zero-length index entry; index and offsets stay in lockstep
        j.note_gap(3)
        j.append({"type": "result", "n": 4})
        self.assertEqual(j.next_offset, 5); self.assertEqual(len(j._index), 5)
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(2)], [(2, 2), (4, 4)])
        self.assertEqual([(o, r["n"]) for o, r in j.read_from(3, 5)], [(4, 4)], "a replay spanning the hole reads past it")
        self.assertEqual([(o, r["n"]) for o, r in sh.read_journal_dir(d, 0)], [(0, 0), (2, 2), (4, 4)],
                         "the orphan reader, with no index, numbers past the unrecorded gap from gaps.json")

    def test_a_replay_read_is_bounded_by_its_end(self):
        # finding 2: the replay covers the records that existed when the attach began; later ones follow from the
        # live backlog, so a drain that yields during the replay cannot send a record twice
        d = tempfile.mkdtemp(); j = sh.Journal(d)
        for i in range(5):
            j.append({"type": "assistant", "n": i})
        self.assertEqual([o for o, _ in j.read_from(0, 3)], [0, 1, 2])
        self.assertEqual([o for o, _ in j.read_from(2, 99)], [2, 3, 4], "an end past the journal reads to the end")
        self.assertEqual(list(j.read_from(3, 3)), [])


class ParkedRules(unittest.TestCase):
    def _req(self, rid, kind, event=None):
        req = {"subtype": kind}
        if kind == "hook_callback":
            req.update(callback_id="hook_0", input={"hook_event_name": event})
        return {"type": "control_request", "request_id": rid, "request": req}

    def test_park_cancel_answer_and_due_hooks(self):
        p = sh.Parked(self_answer_s=100)
        p.park(self._req("a", "can_use_tool"), 1, now=1000, attached=False)
        p.park(self._req("b", "hook_callback", "Stop"), 2, now=1000, attached=False)
        p.park(self._req("c", "hook_callback", "PostToolUse"), 3, now=1050, attached=False)
        self.assertEqual(p.ids(), ["a", "b", "c"])
        self.assertEqual([o for o, _ in p.records()], [1, 2, 3], "records in offset order, for the re-send on attach")
        self.assertEqual(p.due_hooks(1099), [])
        self.assertEqual(p.due_hooks(1100), ["b"], "a hook is due after the self-answer wait; a permission never is")
        self.assertEqual(p.due_hooks(1200), ["b", "c"])
        # a request delivered LIVE is tracked too (finding 4); its unattended clock starts only when the kernel leaves
        p.park(self._req("d", "hook_callback", "Stop"), 4, now=1000, attached=True)
        self.assertNotIn("d", p.due_hooks(5000), "attached: never self-answered")
        p.detached(now=5000)
        self.assertIn("d", p.due_hooks(5100), "unattended since the detach, due after the wait")
        p.attached()
        self.assertNotIn("d", p.due_hooks(9999), "a kernel is back: its answer is awaited")
        self.assertTrue(p.cancel("c")); self.assertFalse(p.cancel("c"))
        self.assertTrue(p.answer("b")); self.assertFalse(p.answer("b"), "a second answer is a late duplicate")
        self.assertEqual(p.ids(), ["a", "d"])
        self.assertEqual(sh.neutral_hook_response("b", "Stop"),
                         {"type": "control_response", "response": {"subtype": "success", "request_id": "b", "response": {}}})

    def test_every_hook_event_the_kernel_registers_has_a_neutral_answer(self):
        src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
        start = src.index("hooks={\"Stop\"")
        block = src[start:src.index("permission_mode=sess.mode", start)]
        events = set(__import__("re").findall(r'"([A-Z][A-Za-z]+)": \[HookMatcher', block))
        self.assertTrue(events, "the kernel's hook table was found")
        self.assertTrue(events <= set(sh.HOOK_NEUTRAL_OUTPUT), "missing neutral answers: %r" % (events - set(sh.HOOK_NEUTRAL_OUTPUT)))
        self.assertEqual(sh.LEASE_HEARTBEAT_S, sb.LEASE_HEARTBEAT_S, "the host beats at the stage 1 cadence")
        self.assertLess(sh.HOOK_SELF_ANSWER_S, sh.HOOK_TIMEOUT_S)
        self.assertLess(sh.HOOK_TIMEOUT_S, 600.0, "inside the CLI's default hook budget (the T303 probe)")


# ── the host as a process, this test as the kernel ─────────────────────────────────────────────
class KernelSide:
    """A tiny synchronous kernel stand-in over the host's socket."""

    def __init__(self, sock_path, timeout=10.0):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(timeout)
        self.s.connect(sock_path)
        self.fr = sh.FrameReader()
        self.frames = []

    def send(self, frame):
        self.s.sendall(sh.encode_frame(frame))

    def recv_until(self, pred, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:   # loop-ok: a bounded socket read
            for f in self.frames:
                if pred(f):
                    return f
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            self.frames.extend(self.fr.feed(chunk))
        for f in self.frames:
            if pred(f):
                return f
        raise AssertionError("no frame matched; got %r" % [f.get("t") for f in self.frames])

    def outs(self):
        return [f for f in self.frames if f.get("t") == "out"]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class HostProcess(unittest.TestCase):
    """Each test starts one host on the fake CLI in a private state root and kills everything after."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, True)
        self.fake_log = os.path.join(self.state, "fake-cli.log")
        self.tdir = os.path.join(self.state, "transcripts")

    def _spec(self, **over):
        d = Path(self.state) / "hosts" / SID
        d.mkdir(parents=True, mode=0o700)
        spec = {"sid": SID, "name": "web", "version": "abc12345", "state_dir": self.state, "protocol": 1,
                "cli_path": FAKE, "cwd": self.state, "permission_prompt_tool_name": "stdio", "permission_mode": "default",
                "env": {"FAKE_CLI_LOG": self.fake_log, "FAKE_CLI_TRANSCRIPT_DIR": self.tdir, "FAKE_CLI_SESSION_ID": FSID,
                        "ROMP_CANARY_SECRET": "canary-" + uuid.uuid4().hex},
                "max_buffer_size": 1024 * 1024, "hook_self_answer_s": 2, "unattached_grace_s": 3600}
        spec.update(over)
        p = d / "spawn.json"
        p.write_text(json.dumps(spec)); p.chmod(0o600)
        return str(p), spec

    def _start(self, sdk=False, **over):
        spec_path, spec = self._spec(**over)
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        env.pop("ROMP_SDK_SITE", None)
        if sdk:
            env["ROMP_SDK_SITE"] = str(SDK_SITE)
        else:
            env["ROMP_SDK_SITE"] = os.path.join(self.state, "no-sdk-here")
        host = subprocess.Popen([sys.executable, os.path.join(BIN, "romp-session-host"), spec_path],
                                stdout=subprocess.DEVNULL, stderr=open(os.path.join(self.state, "host.stderr"), "w"), env=env,
                                start_new_session=True)
        self.addCleanup(self._kill_group, host)
        sock = Path(self.state) / "hosts" / (SID[:8] + ".sock")
        deadline = time.time() + 15
        while time.time() < deadline and not (sock.exists() and self._lease()):   # loop-ok: a bounded wait on two events
            if host.poll() is not None:
                break
            time.sleep(0.05)
        self.assertIsNone(host.poll(), "the host is running: " + open(os.path.join(self.state, "host.stderr")).read()[-800:])
        self.assertTrue(sock.exists(), "the socket exists")
        self.assertTrue(self._lease(), "the lease is written once the CLI has a pid")
        return host, str(sock), spec

    @staticmethod
    def _kill_group(proc):
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=10)

    def _lease(self):
        return sb.read_lease(self.state, SID)

    def _hostlog(self):
        p = Path(self.state) / "hosts" / SID / "host.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def _attach(self, sock, ack=-1, pid=4242):
        k = KernelSide(sock)
        k.send({"t": "attach", "kernel": {"pid": pid, "start": "1", "version": "abc12345"}, "ack": ack})
        hello = k.recv_until(lambda f: f.get("t") == "hello")
        return k, hello

    def _user(self, text):
        return json.dumps({"type": "user", "message": {"role": "user", "content": text}})

    def test_a_turn_flows_through_the_host_and_is_journaled_under_a_host_held_lease(self):
        host, sock, spec = self._start()
        lease = self._lease()
        self.assertEqual((lease["holder"]["pid"], lease["holder"]["kind"], lease["version"]), (host.pid, "host", "abc12345"))
        self.assertEqual(sb.lease_state(lease, time.time()), "valid")
        k, hello = self._attach(sock)
        self.assertEqual((hello["cli"]["pid"], hello["journal"]["next"], hello["parked"], hello["inflight"]), (lease["pid"], 0, [], 0))
        k.send({"t": "in", "data": json.dumps({"type": "control_request", "request_id": "req_0_aaaa",
                                                 "request": {"subtype": "initialize", "hooks": {"Stop": [{"matcher": None, "hookCallbackIds": ["hook_0"], "timeout": 540}]}}})})
        resp = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_response")
        self.assertEqual(resp["data"]["response"]["request_id"], "req_0_aaaa")
        k.send({"t": "in", "data": self._user("hello sleep=0.3")})
        res = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        offsets = [f["offset"] for f in k.outs()]
        self.assertEqual(offsets, list(range(len(offsets))), "offsets are ordinals from zero")
        kinds = [f["data"]["type"] for f in k.outs()]
        self.assertEqual(kinds, ["control_response", "system", "assistant", "result"])
        journal = list(sh.read_journal_dir(os.path.join(self.state, "hosts", SID)))
        self.assertEqual([r["type"] for _, r in journal], kinds, "the journal holds every record the CLI emitted")
        self.assertEqual(self._lease()["fsid"], FSID, "the lease's conversation id follows the init (it started as the romp sid)")
        k.send({"t": "ack", "offset": res["offset"]})
        # secrets: the canary environment value appears nowhere the host writes or sends
        canary = spec["env"]["ROMP_CANARY_SECRET"]
        blob = json.dumps(self._hostlog()) + json.dumps([r for _, r in journal]) + json.dumps(k.frames)
        self.assertNotIn(canary, blob, "no environment value in host.log, the journal or a frame")
        self.assertNotIn("FAKE_CLI_LOG", json.dumps(self._hostlog()), "no spec content in host.log")
        k.close()

    def test_a_detached_kernel_reattaches_and_replays_from_its_ack_while_the_turn_kept_running(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("long sleep=2.5")})
        first = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "ack", "offset": first["offset"]})
        k.send({"t": "detach"}); k.close()
        time.sleep(0.5)
        self.assertIsNone(host.poll(), "the host keeps running the turn")
        self.assertEqual(sb.lease_state(self._lease(), time.time()), "valid", "and keeps the lease")
        k2, hello = self._attach(sock, ack=first["offset"], pid=4343)
        self.assertEqual(hello["inflight"], 1, "the host reports the open turn, so the new kernel knows it is mid-turn")
        res = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=10)
        offs = [f["offset"] for f in k2.outs()]
        self.assertEqual(offs[0], first["offset"] + 1, "replay starts after the acknowledged offset")
        self.assertEqual(offs, list(range(offs[0], offs[0] + len(offs))), "replay then live, in order, no gap")
        self.assertEqual(res["data"]["result"], "done", "the turn the first kernel started finished under the second")
        log_kinds = [r["kind"] for r in self._hostlog()]
        self.assertIn("detached", log_kinds); self.assertEqual(log_kinds.count("attached"), 2)
        k2.close()

    def test_a_permission_request_parks_while_unattached_and_the_late_answer_reaches_the_cli(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("please ask=permission after=0.6 sleep=0.2")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "request-open" for r in self._hostlog()):   # loop-ok
            time.sleep(0.05)
        opened = [r for r in self._hostlog() if r["kind"] == "request-open"]
        self.assertEqual([r["attached"] for r in opened], [False], "the request opened while no kernel was attached")
        k2, hello = self._attach(sock, ack=-1)
        self.assertEqual(len(hello["parked"]), 1, "the parked request is named on attach")
        rid = hello["parked"][0]
        req = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(req["data"]["request_id"], rid)
        k2.send({"t": "in", "data": json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                                                            "response": {"behavior": "allow", "updatedInput": {}}}})})
        res = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        texts = [c["text"] for f in k2.outs() if f["data"].get("type") == "assistant" for c in f["data"]["message"]["content"]]
        self.assertIn("permission answered", texts, "the late answer reached the CLI and the turn went on")
        # a second answer for the same id is a late duplicate the host drops
        k2.send({"t": "in", "data": json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": rid, "response": {}}})})
        k2.send({"t": "ping"}); k2.recv_until(lambda f: f.get("t") == "pong")
        self.assertIn("late-answer-dropped", [r["kind"] for r in self._hostlog()])
        k2.close()

    def test_a_parked_hook_is_answered_by_the_host_at_the_deadline_and_said_loudly(self):
        host, sock, spec = self._start(hook_self_answer_s=1)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": json.dumps({"type": "control_request", "request_id": "req_0_bbbb",
                                                 "request": {"subtype": "initialize", "hooks": {"Stop": [{"matcher": None, "hookCallbackIds": ["hook_3"], "timeout": 540}]}}})})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_response")
        k.send({"t": "in", "data": self._user("go hook=Stop after=0.6 sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "hook-self-answered" for r in self._hostlog()):   # loop-ok
            time.sleep(0.1)
        rows = [r for r in self._hostlog() if r["kind"] == "hook-self-answered"]
        self.assertEqual(len(rows), 1, "the host answered the parked hook itself once")
        self.assertEqual((rows[0]["event"], rows[0]["callbackId"]), ("Stop", "hook_3"))
        self.assertGreaterEqual(rows[0]["parkedS"], 1.0)
        k2, hello = self._attach(sock)
        self.assertEqual(hello["parked"], [], "nothing left parked")
        k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        texts = [c["text"] for f in k2.outs() if f["data"].get("type") == "assistant" for c in f["data"]["message"]["content"]]
        self.assertIn("hook answered", texts, "the CLI took the neutral answer and went on")
        k2.close()

    def test_a_cancel_from_the_cli_drops_the_parked_request(self):
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("go ask=permission after=0.6 cancel-after=0.5 sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "request-cancelled" for r in self._hostlog()):   # loop-ok
            time.sleep(0.1)
        self.assertIn("request-cancelled", [r["kind"] for r in self._hostlog()])
        k2, hello = self._attach(sock)
        self.assertEqual(hello["parked"], [])
        k2.close()

    def test_end_closes_stdin_and_the_cli_exits_then_the_lease_goes(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        k.send({"t": "end", "grace": 30})
        ex = k.recv_until(lambda f: f.get("t") == "exit")
        self.assertEqual((ex["cause"], ex["code"]), ("end", 0))
        host.wait(timeout=10)
        self.assertEqual(host.returncode, 0)
        self.assertIsNone(self._lease(), "the lease is removed when the host leaves")
        self.assertFalse(os.path.exists(sock), "the socket is removed")
        k.close()

    def test_end_with_a_short_grace_forces_a_cli_that_will_not_leave(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("slow sleep=30")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "end", "grace": 1})
        ex = k.recv_until(lambda f: f.get("t") == "exit", timeout=15)
        self.assertEqual(ex["cause"], "end-forced")
        self.assertIn("end-forced", [r["kind"] for r in self._hostlog()])
        host.wait(timeout=10)
        k.close()

    def test_signal_reaches_the_cli_and_a_second_kernel_is_told_busy(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("slow sleep=30")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        other = KernelSide(sock)
        other.send({"t": "attach", "kernel": {"pid": 9, "start": "1", "version": ""}, "ack": -1})
        self.assertEqual(other.recv_until(lambda f: f.get("t") == "busy")["kernel"]["pid"], 4242)
        other.close()
        k.send({"t": "signal", "sig": "INT"})
        res = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=10)
        self.assertEqual(res["data"]["result"], "interrupted")
        k.close()

    def test_an_unattached_idle_cli_is_ended_after_the_grace(self):
        host, sock, spec = self._start(unattached_grace_s=1)
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        k.send({"t": "detach"}); k.close()
        host.wait(timeout=15)
        kinds = [r["kind"] for r in self._hostlog()]
        self.assertIn("unattached-grace-expired", kinds)
        self.assertEqual(kinds[-1], "host-exited")
        self.assertIsNone(self._lease())

    def test_socket_loss_without_detach_is_a_kernel_death_the_host_survives(self):
        host, sock, spec = self._start()
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("long sleep=2")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.close()                                   # no detach: the kernel died
        time.sleep(0.5)
        self.assertIsNone(host.poll())
        self.assertIn("kernel-lost", [r["kind"] for r in self._hostlog()])
        k2, hello = self._attach(sock)
        res = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=10)
        self.assertEqual(res["data"]["result"], "done")
        k2.close()

    def test_a_request_delivered_live_to_a_kernel_that_dies_is_still_open_and_re_sent_on_attach(self):
        # finding 4: the kernel RECEIVED the permission request (and acknowledged past it) but died before the
        # user answered; the next kernel must see the request again or the CLI hangs on it forever
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("please ask=permission after=0.3 sleep=0.2")})
        req = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        k.send({"t": "ack", "offset": req["offset"]}); k.send({"t": "ping"}); k.recv_until(lambda f: f.get("t") == "pong")
        k.close()                                       # the kernel dies with the request unanswered
        k2, hello = self._attach(sock, ack=req["offset"], pid=4343)
        self.assertEqual(hello["parked"], [req["data"]["request_id"]], "still open, named on attach")
        again = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(again["offset"], req["offset"], "re-sent with its original offset although acknowledged")
        rid = req["data"]["request_id"]
        k2.send({"t": "in", "data": json.dumps({"type": "control_response", "response": {"subtype": "success", "request_id": rid,
                                                                                            "response": {"behavior": "allow", "updatedInput": {}}}})})
        k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        texts = [c["text"] for f in k2.outs() if f["data"].get("type") == "assistant" for c in f["data"]["message"]["content"]]
        self.assertIn("permission answered", texts)
        k2.close()

    def test_a_journal_write_fault_is_a_fault_frame_not_the_clis_death(self):
        # finding 3: a failed journal write used to end the read loop and be reported as the CLI dying
        host, sock, spec = self._start(_test_journal_fault_at=1)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.2")})
        res = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        faults = [f for f in k.frames if f.get("t") == "fault"]
        self.assertEqual([f["kind"] for f in faults], ["journal-write-failed"])
        self.assertIsNone(host.poll(), "the host and its CLI are still running")
        kinds = [r["kind"] for r in self._hostlog()]
        self.assertIn("journal-write-failed", kinds); self.assertNotIn("cli-exited", kinds)
        # live delivery was complete (the kernel got every record) even though offset 1 is missing from the journal
        self.assertEqual([f["offset"] for f in k.outs()], list(range(len(k.outs()))))
        offs = [o for o, _ in sh.read_journal_dir(os.path.join(self.state, "hosts", SID))]
        self.assertNotIn(1, offs, "the failed record is a gap the readers skip")
        self.assertEqual(offs, [o for o in range(len(k.outs())) if o != 1], "the numbering around the gap holds")
        k.send({"t": "end", "grace": 10})
        ex = k.recv_until(lambda f: f.get("t") == "exit")
        self.assertEqual(ex["cause"], "end")
        k.close()

    def test_a_slow_journal_writer_raises_the_reader_behind_fault_and_the_reader_keeps_reading(self):
        # finding 5: the check used to be dead (the write ran on the reader's path); with the writer on its own
        # task, a throttled writer lets the reader run ahead and the fault fires once
        host, sock, spec = self._start(_test_journal_delay_s=0.4, reader_behind_records=1)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("one sleep=0.1")})
        k.send({"t": "in", "data": self._user("two sleep=0.1")})
        fault = k.recv_until(lambda f: f.get("t") == "fault" and f.get("kind") == "reader-behind", timeout=15)
        self.assertTrue(fault)
        first = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result", timeout=15)
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result" and f is not first, timeout=15)
        self.assertEqual(sum(1 for f in k.outs() if f["data"].get("type") == "result"), 2,
                         "both turns' results reached the kernel live while the writer lagged")
        self.assertEqual([r["kind"] for r in self._hostlog()].count("reader-behind"), 1)
        k.close()

    def test_a_second_end_with_a_shorter_grace_pulls_the_deadline_in(self):
        # finding 8
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("slow sleep=30")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "assistant")
        k.send({"t": "end", "grace": 60})
        k.send({"t": "end", "grace": 1})
        t0 = time.time()
        ex = k.recv_until(lambda f: f.get("t") == "exit", timeout=15)
        self.assertEqual(ex["cause"], "end-forced")
        self.assertLess(time.time() - t0, 10, "the shorter grace won")
        self.assertIn("end-grace-shortened", [r["kind"] for r in self._hostlog()])
        k.close()

    def test_the_heartbeat_keeps_the_lease_valid_past_a_short_ttl(self):
        # finding 10: every other check happens within a beat of the write; this one waits past a TTL shorter
        # than the wait, so only real beats keep the lease valid
        host, sock, spec = self._start()
        with mock.patch.object(sb, "LEASE_TTL_S", 4.0):
            time.sleep(5.0)
            lease = self._lease()
            self.assertEqual(sb.lease_state(lease, time.time()), "valid", "beats kept it fresh: t=%r now=%r" % (lease.get("t"), time.time()))
        self.assertGreater(lease["t"], spec_t if (spec_t := 0) else 0)

    def test_a_double_journal_fault_never_breaks_a_later_attach(self):
        # finding b: an unrecorded gap used to leave the index short of the offsets; the next replay across it
        # raised inside the host's client loop and the kernel read a bare EOF
        host, sock, spec = self._start(_test_journal_fault_at=1, _test_journal_gap_fault=True)
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.2")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        k.send({"t": "detach"}); k.close()
        deadline = time.time() + 10
        while time.time() < deadline and not any(r["kind"] == "journal-gap-unrecorded" for r in self._hostlog()):   # loop-ok
            time.sleep(0.05)
        k2, hello = self._attach(sock, ack=-1, pid=4343)
        k2.send({"t": "ping"}); k2.recv_until(lambda f: f.get("t") == "pong")
        offs = [f["offset"] for f in k2.outs()]
        self.assertEqual(offs, [o for o in range(hello["journal"]["next"]) if o != 1], "everything but the hole replayed, no fault, no EOF")
        self.assertNotIn("client-loop-failed", [r["kind"] for r in self._hostlog()])
        self.assertIsNone(host.poll())
        k2.close()

    def test_an_attach_while_the_writer_lags_sends_the_unwritten_records_from_memory(self):
        # finding c: records read but not yet journaled were neither replayed nor backlogged
        host, sock, spec = self._start(_test_journal_delay_s=0.5)
        k, _ = self._attach(sock)
        for i in range(3):
            k.send({"t": "in", "data": self._user("turn%d sleep=0.05" % i)})
        first = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        k.send({"t": "detach"}); k.close()
        k2, hello = self._attach(sock, ack=-1, pid=4343)
        deadline = time.time() + 20
        while time.time() < deadline and sum(1 for f in k2.outs() if f["data"].get("type") == "result") < 3:   # loop-ok
            seen = {id(f) for f in k2.frames if f.get("t") == "pong"}
            k2.send({"t": "ping"}); k2.recv_until(lambda f: f.get("t") == "pong" and id(f) not in seen)   # a NEW pong, so the read goes on
            time.sleep(0.1)
        offs = [f["offset"] for f in k2.outs()]
        self.assertEqual(offs, list(range(7)), "every record once, in order: the init and three (assistant, result) pairs, whether from disk, memory or the backlog; host log: %r"
                         % [(r["kind"], r.get("at"), r.get("error")) for r in self._hostlog()][-12:])
        self.assertGreaterEqual(hello["journal"]["next"], 4, "the hello counted records the writer had not landed yet")
        k2.close()

    def test_an_end_right_after_a_line_lets_the_line_reach_the_cli_first(self):
        # finding d: `end` used to close stdin ahead of lines still queued on the pump
        host, sock, spec = self._start()
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("last words sleep=0.1")})
        time.sleep(0.05)                                 # two socket reads on the host's side, not one
        k.send({"t": "end", "grace": 20})
        ex = k.recv_until(lambda f: f.get("t") == "exit", timeout=15)
        self.assertEqual(ex["cause"], "end")
        self.assertIn("last words", open(self.fake_log).read(), "the queued line reached the CLI before its stdin closed")
        self.assertEqual(sum(1 for f in k.outs() if f["data"].get("type") == "result"), 1, "and its turn ran to its result")
        k.close()

    def test_an_open_request_whose_journal_write_failed_is_still_re_sent_on_attach(self):
        # finding e: the re-send came from the journal, which skips a gap; it comes from the parked table now
        host, sock, spec = self._start(_test_journal_fault_at=2)      # 0 init, 1 assistant, 2 the permission request
        k, _ = self._attach(sock)
        k.send({"t": "in", "data": self._user("please ask=permission after=0.3 sleep=0.2")})
        req = k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(req["offset"], 2)
        k.send({"t": "ack", "offset": 1}); k.send({"t": "ping"}); k.recv_until(lambda f: f.get("t") == "pong")
        k.close()
        k2, hello = self._attach(sock, ack=1, pid=4343)
        self.assertEqual(hello["parked"], [req["data"]["request_id"]])
        again = k2.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "control_request")
        self.assertEqual(again["data"]["request_id"], req["data"]["request_id"], "re-sent from the table although its journal write failed")
        k2.close()

    @unittest.skipUnless(SDK_SITE, "the SDK venv is not on this machine; the pipe transport covered the host")
    def test_the_sdk_transport_drives_the_fake_cli_the_same_way(self):
        host, sock, spec = self._start(sdk=True)
        k, hello = self._attach(sock)
        k.send({"t": "in", "data": self._user("hi sleep=0.1")})
        k.recv_until(lambda f: f.get("t") == "out" and f["data"].get("type") == "result")
        spawned = [r for r in self._hostlog() if r["kind"] == "cli-spawned"]
        self.assertEqual(spawned[0]["transport"], "sdk", "the SDK's own SubprocessCLITransport spawned the CLI")
        k.send({"t": "end", "grace": 10})
        k.recv_until(lambda f: f.get("t") == "exit")
        k.close()


if __name__ == "__main__":
    unittest.main()
