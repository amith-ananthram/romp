#!/usr/bin/env python3
"""T323 stage 3, served: a REAL hermetic kernel boots over synthetic sessions, a chat client (a minimal websocket
client in this file) looks at a session whose transcript launched an agent, so the chat build folds the agent's own
transcript for its gist; the kernel is stopped the way the manager stops it (SIGTERM) and its exit writes the folds'
checkpoints; a SECOND kernel boots over the same state root with the same client and reads the AGENT FILE as a tail
(per file: the guard bytes, nothing more, since nothing was appended between the two boots), restores its checkpoints
and falls back on none. What stays whole is asserted too, not left out: the leaf transcripts and their states logs,
which the parse reads (stage 4's), read their whole size plus the guard check the folds' restore made. Synthetic only:
invented text, placeholder uuids, TESTHOST."""
import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment

WEB = "aaaaaaaa-3333-4222-8333-444444444444"
API = "bbbbbbbb-3333-4222-8333-444444444444"
TESTS = "cccccccc-3333-4222-8333-444444444444"
ALL = (WEB, API, TESTS)


def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "cwd": "/w/notes-api",
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "cwd": "/w/notes-api",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


AGENT_ID = "a0123456789abcdef"                             # the shape the kernel's agent-id pattern admits


def agent_launch(t, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "cwd": "/w/notes-api",
            "message": {"role": "assistant", "stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": "toolu_agent1", "name": "Agent", "input": {"prompt": "check the tab width on narrow screens"}}]}}


def agent_result(t, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "cwd": "/w/notes-api",
            "toolUseResult": {"agentId": AGENT_ID, "status": "completed"},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_agent1", "content": "the tabs wrap at 480px"}]}}


def agent_transcript(t0, n=60):
    recs, parent = [], None
    for k in range(n):
        u, a = "s%d" % k, "r%d" % k
        recs.append({"type": "user", "timestamp": iso(t0 + 2 * k), "uuid": u, "parentUuid": parent,
                     "message": {"role": "user", "content": "look at screen %d" % k}})
        recs.append({"type": "assistant", "timestamp": iso(t0 + 2 * k + 1), "uuid": a, "parentUuid": u,
                     "message": {"role": "assistant", "stop_reason": "tool_use", "content": [
                         {"type": "tool_use", "id": "toolu_s%d" % k, "name": "Read", "input": {"file_path": "/w/notes-api/web/tab%d.ts" % k}}]}})
        parent = a
    return recs


class ChatClient:
    """A minimal websocket client (RFC 6455 text frames, masked on the way out) that opens the chat pane on one
    session the way the dashboard's shim does: the upgrade with app=chat and the active tab, then a ready frame,
    then it reads frames until the session's frame arrives or the wait runs out."""

    def __init__(self, port, token, active):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET /ws?app=chat&wid=t323s3&active=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nX-Romp-Token: %s\r\n\r\n"
               % (active, port, key, token))
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("the websocket upgrade closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("upgrade refused: %r" % head[:120])
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        hdr = bytes([0x81])
        n = len(data)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk

    def frames(self, seconds):
        """Yield the text frames the kernel sends within `seconds` (pings are answered, other opcodes skipped)."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.sock.settimeout(max(0.1, deadline - time.time()))
            try:
                self._need(2)
            except (socket.timeout, EOFError):
                return
            b0, b1 = self.buf[0], self.buf[1]
            op, n, i = b0 & 0x0F, b1 & 0x7F, 2
            if n == 126:
                self._need(4); n = struct.unpack(">H", self.buf[2:4])[0]; i = 4
            elif n == 127:
                self._need(10); n = struct.unpack(">Q", self.buf[2:10])[0]; i = 10
            try:
                self._need(i + n)
            except (socket.timeout, EOFError):
                return
            payload, self.buf = self.buf[i:i + n], self.buf[i + n:]
            if op == 0x9:                                       # ping: answer with a pong
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + b"\0\0\0\0" + payload)
            elif op == 0x1:
                try:
                    yield json.loads(payload.decode())
                except ValueError:
                    continue
            elif op == 0x8:
                return

    def close(self):
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + b"\0\0\0\0")
        except OSError:
            pass
        self.sock.close()


class ExitThenBoot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = tempfile.mkdtemp(prefix="romp-t323s3-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        cls.dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), cls.dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cls.claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "goals", "timeline"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(cls.claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 3600
        for i, (sid, name) in enumerate(((WEB, "web"), (API, "api"), (TESTS, "tests"))):
            Path(cls.state, "names", sid).write_text("%s\t%s\t#9cd2ff\t#0c1a2e\n" % (name, cwd))
            Path(cls.state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5", "lastStopAt": t0 + 90}))
            rows = [{"t": t0 + 10 * k, "state": "working" if k % 2 == 0 else "waiting"} for k in range(1, 40)]
            rows.append({"t": t0 + 70, "awaiting": True, "state": "waiting"} if i == 1 else {"t": t0 + 71, "state": "idle"})
            Path(cls.state, "states", sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            recs = [uline(t0, "wire the fixtures directory into the integration suite", "u1"),
                    aline(t0 + 20, "done: the suite reads the fixtures", "a1", "u1")]
            if sid == WEB:                                     # web launched an agent whose own transcript is the gist's input
                recs += [uline(t0 + 30, "check the tab width", "u2", "a1"), agent_launch(t0 + 31, "a2", "u2"),
                         agent_result(t0 + 80, "u3", "a2"), aline(t0 + 85, "the tabs wrap at 480px; fixed", "a3", "u3")]
                sub = Path(proj, sid, "subagents"); sub.mkdir(parents=True, exist_ok=True)
                cls.agent_file = str(sub / ("agent-%s.jsonl" % AGENT_ID))
                Path(cls.agent_file).write_text("".join(json.dumps(r) + "\n" for r in agent_transcript(t0 + 32)))
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
            cls.leaf_files = getattr(cls, "leaf_files", {}); cls.leaf_files[sid] = os.path.join(proj, sid + ".jsonl")
        Path(cls.state, "timeline", "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
            {"ev": "sent", "id": "m1", "from_id": WEB, "to_id": API, "from": "web", "body": "ping", "t": t0 + 5, "kind": "coordinate"},
            {"ev": "exec", "id": "m1", "t": t0 + 6})))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.states_files = {sid: os.path.join(cls.state, "states", sid + ".jsonl") for sid in ALL}
        cls.token = "testtok-t323s3"

    @classmethod
    def tearDownClass(cls):
        keep = os.environ.get("ROMP_T323S3_KEEP_LOGS")
        if keep:                                                # a diagnosis aid: the two kernels' logs, kept where asked
            os.makedirs(keep, exist_ok=True)
            for f in os.listdir(cls.lab):
                if f.startswith("kernel-") and f.endswith(".log"):
                    shutil.copy(os.path.join(cls.lab, f), os.path.join(keep, f))
        shutil.rmtree(cls.lab, ignore_errors=True)

    def _boot(self):
        port = _free_port()
        env = _lab.kernel_env(self.lab, self.claude, self.dist, port, self.token, ROMP_HOST_NAME="TESTHOST",
                              ROMP_READER_TRACE=os.environ.get("ROMP_READER_TRACE", ""))   # a diagnosis aid: one stderr line per read
        log = open(os.path.join(self.lab, "kernel-%d.log" % port), "w")
        k = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=log, stderr=subprocess.STDOUT, env=env)
        for _ in range(200):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            k.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")
        for _ in range(40):                                     # the boot reconcile and a few pusher cycles
            try:
                if self._get(port, "/version").get("uptime_s", 0) >= 4:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        return k, port

    def _get(self, port, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers={"X-Romp-Token": self.token})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def _drive(self, port):
        """What a dashboard does: a chat client looks at web (the chat build folds the session meta, the queue ledger,
        the background tasks, the agent launches and the agent's gist), plus the feed and the busy hint (the states
        folds). The session frame for web is awaited so the build has run before the kernel is stopped."""
        client = ChatClient(port, self.token, WEB)
        try:
            client.send({"type": "ready"})
            seen = []
            for fr in client.frames(20):
                seen.append(fr.get("type"))
                if fr.get("type") == "session" and (fr.get("id") == WEB or fr.get("sid") == WEB):
                    break
            self.assertIn("session", seen, "the chat client received web's frame; frames seen: %s" % seen[:20])
            self._get(port, "/feed.json")
            try:
                self._get(port, "/busy")
            except Exception:
                pass
            time.sleep(2.0)                                     # a few pusher cycles: the settle-keyed writer runs
        finally:
            client.close()

    def _stop(self, k):
        k.send_signal(signal.SIGTERM)                           # the manager's stop: _graceful_term, then _drain_and_exit
        k.wait(timeout=30)

    def test_the_second_kernel_reads_the_states_logs_as_tails_from_the_checkpoints_the_first_left_at_exit(self):
        k1, p1 = self._boot()
        try:
            self._drive(p1)
            perf1 = self._get(p1, "/perf")["checkpoints"]
            self.assertEqual(perf1["fallbacks"], {}, "a first boot has nothing to fall back from")
        finally:
            self._stop(k1)
        ck = os.path.join(self.state, "checkpoints")
        files = sorted(os.listdir(ck)) if os.path.isdir(ck) else []
        docs = [json.loads(open(os.path.join(ck, f)).read()) for f in files]
        recorded = {d["path"] for d in docs}
        for sid, sp in self.states_files.items():
            self.assertIn(os.path.realpath(sp), recorded, "the exit wrote a checkpoint for %s's states log; wrote: %s" % (sid, sorted(recorded)))
        self.assertIn(os.path.realpath(self.agent_file), recorded, "the exit wrote a checkpoint for the agent file the gist folded; wrote: %s" % sorted(recorded))
        agent_size = os.path.getsize(self.agent_file)
        sizes = {sid: os.path.getsize(sp) for sid, sp in self.states_files.items()}
        k2, p2 = self._boot()
        try:
            self._drive(p2)
            perf = self._get(p2, "/perf")["checkpoints"]
            by = perf["readByPath"]
            report = {"agent": 0, "states": 0, "leaf": 0, "postal": 0, "other": 0}
            for path, n in by.items():
                cls_ = ("agent" if "/subagents/" in path else "states" if "/states/" in path else "postal" if path.endswith("messages.jsonl")
                        else "leaf" if path.endswith(".jsonl") and "/projects/" in path else "other")
                report[cls_] += n
            self.assertEqual(perf["fallbacks"], {}, "every checkpoint verified: %s" % perf)
            self.assertGreaterEqual(perf["restored"], len(ALL), "one restore per states log at least: %s" % perf)
            got = by.get(os.path.realpath(self.agent_file), by.get(self.agent_file))
            self.assertEqual(got, 128, "the agent file was read as a TAIL: its 64 guard bytes checked and captured again, nothing of its "
                                       "content, since nothing was appended (its size %d; bytes by class in this boot: %s)" % (agent_size, report))
            self.assertGreaterEqual(perf["restoredFolds"].get("agentGist", 0), 1, "the gist fold resumed from its recorded state: %s" % perf["restoredFolds"])
            for name in ("statesOverlay", "lastState", "machineCut"):   # the folds the feed and the busy hint run per session
                self.assertGreaterEqual(perf["restoredFolds"].get(name, 0), len(ALL),
                                        "the %s fold resumed for every session's states log: %s" % (name, perf["restoredFolds"]))
            # what stays whole is asserted, not hidden: the leaf transcripts and their states logs, which the parse
            # reads (stage 4's), cost their whole size plus the guard check the folds' restore made on each
            for sid, sp in self.states_files.items():
                got = by.get(os.path.realpath(sp), by.get(sp)) or 0
                now_size = os.path.getsize(sp)                      # the second kernel appends states rows of its own
                self.assertGreaterEqual(got, sizes[sid], "%s's states log: whole, the parse's read (bytes by class: %s)" % (sid, report))
                self.assertLessEqual(got, now_size + 8 * 64,
                                     "%s's states log: the parse's whole read plus guard reads and captures (a tail restore, its "
                                     "upgrade to the whole file, the whole-reader-first check), never its content twice" % sid)
            for sid, lp in self.leaf_files.items():
                got = by.get(os.path.realpath(lp), by.get(lp)) or 0
                self.assertGreaterEqual(got, os.path.getsize(lp), "%s's leaf transcript: whole, the parse's read (stage 4)" % sid)
            sys.stderr.write("t323s3 served: second boot bytes by class %s, restored %d, writes %d\n"
                             % (report, perf["restored"], perf["writes"]))
        finally:
            self._stop(k2)


if __name__ == "__main__":
    unittest.main()
