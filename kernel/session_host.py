#!/usr/bin/env python3
"""romp-session-host: the per-session process that owns one Claude Code CLI (stage 4 of the
restart-surviving sessions program, T315; design: the T315 design note, the T295 report section 2a).

The kernel used to be the CLI's parent: its pipes were the CLI's stdio, so a kernel restart ended
every turn. The host takes the parent's place. It spawns the CLI from a SPAWN SPECIFICATION the kernel
writes (`spawn.json`: the plain-data fields of the SDK's ClaudeAgentOptions), through the SDK's own
SubprocessCLITransport when the SDK is importable (so the command line and the environment are the
SDK's, byte for byte), reads the CLI's stdout WITHOUT PAUSE and appends every message to an append-only
JOURNAL, serves one Unix socket the kernel attaches to, holds the stage 1 LEASE as the holder, PARKS
control requests (permissions, hook callbacks) while no kernel is attached and answers a parked hook
itself before the CLI's own deadline, relays stderr, and ends the CLI by closing its stdin and waiting.
A kernel that attaches after a restart replays the journal from the offset it last acknowledged and
sends its own initialize, which the CLI accepts as a replacement of its hook table (the T303 probe).

The socket protocol is newline-delimited JSON frames, field `t` naming the frame:
  kernel → host: attach {kernel:{pid,start,version}, ack:N}, in {data}, ack {offset}, signal {sig},
                 end {grace}, detach, ping
  host → kernel: hello {host, cli, journal:{next}, parked:[ids]}, out {offset, data}, stderr {line},
                 exit {code, signal, cause}, fault {kind, text}, busy {kernel}, pong
One kernel is attached at a time. Connection loss without `detach` is a kernel death to the host; a
`detach` is not (the host keeps running). An unattached host whose CLI is idle past the grace ends it.

Secrets: the spec carries the environment overlay (a key helper command, PATH additions), so nothing
from the spec or any environment value is ever written to host.log, the journal or a frame.

No romp module is imported at module level except through the launcher's sys.path (bin/romp-session-host
puts the repo's kernel directory and, when present, the SDK venv on the path); the lease helpers come
from sdk_backend, which imports without the SDK and runs nothing at import.
"""
from __future__ import annotations
import asyncio
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path

# ── protocol constants ──────────────────────────────────────────────────────────────────────────
PROTOCOL_VERSION = 1
LEASE_HEARTBEAT_S = 3.0          # the stage 1 cadence (sdk_backend.LEASE_HEARTBEAT_S; pinned equal by a test)
HOOK_TIMEOUT_S = 540.0           # what the kernel registers on every hook matcher (inside the CLI's 600 s default)
HOOK_SELF_ANSWER_S = 480.0       # a parked hook is answered by the host after this much parking, unattached
END_GRACE_DEFAULT_S = 120.0      # `end` without a grace: the long bound (conserve_close, request_reconnect)
END_GRACE_KILL_S = 5.0           # the kernel's kill: `end` with this bound
UNATTACHED_GRACE_DEFAULT_S = 900.0   # an idle CLI with no kernel attached for this long is ended (a setting)
JOURNAL_SEGMENT_BYTES = 64 * 1024 * 1024
READER_BEHIND_RECORDS = 5000
READER_BEHIND_BYTES = 200 * 1024 * 1024
ACK_NONE = -1

# The neutral answer the host gives a parked hook callback when no kernel returned in time, PER EVENT
# KIND: the empty output, which is what romp's own hook callbacks return when they have nothing to say
# (no `decision`, no `permissionDecision`, so the CLI applies its normal flow). Kept as a table, not a
# single assumed object, so a future PreToolUse hook gets its own entry (an empty output there is also
# neutral: the CLI falls through to the permission flow). A test pins that every event the kernel's
# _options registers has a row here.
HOOK_NEUTRAL_OUTPUT = {
    "Stop": {},
    "UserPromptSubmit": {},
    "SubagentStart": {},
    "SubagentStop": {},
    "PostToolUse": {},
    "PostToolUseFailure": {},
    "PreToolUse": {},
}

# The ClaudeAgentOptions fields the spawn specification carries: every plain-data field the SDK's
# SubprocessCLITransport reads to build the command line and the environment. The kernel writes exactly
# these; a test compares the list against the fields `_build_command` and `connect` reference.
SPEC_FIELDS = ("cli_path", "cwd", "env", "permission_mode", "permission_prompt_tool_name", "resume",
               "session_id", "resume_session_at", "fork_session", "extra_args", "settings", "mcp_servers",
               "system_prompt", "model", "effort", "include_partial_messages", "enable_file_checkpointing",
               "max_buffer_size", "setting_sources", "add_dirs", "allowed_tools", "disallowed_tools",
               "max_turns", "continue_conversation", "fallback_model")
# Spec keys that are the host's own, not option fields
SPEC_HOST_KEYS = ("sid", "name", "version", "hook_timeout_s", "hook_self_answer_s", "unattached_grace_s",
                  "state_dir", "protocol")


def encode_frame(obj: dict) -> bytes:
    """One frame: compact JSON plus a newline."""
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class FrameReader:
    """Newline-delimited JSON frames out of arbitrary byte chunks. A line that is not JSON is dropped
    with a note (the peer is ours; a corrupt line is a bug, never a protocol branch)."""

    def __init__(self, on_bad=None):
        self._buf = b""
        self._on_bad = on_bad

    def feed(self, chunk: bytes):
        self._buf += chunk
        out = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line, self._buf = self._buf[:nl], self._buf[nl + 1:]
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                if self._on_bad:
                    self._on_bad(len(line))
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out


# ── the journal ─────────────────────────────────────────────────────────────────────────────────
class Journal:
    """Append-only record of every message the CLI emitted, one JSON object per line, in SEGMENTS
    (`journal-<n>.jsonl`) under the host directory. A record's OFFSET is its ordinal since the CLI
    started, global across segments; the in-memory index maps an offset to (segment, byte position)
    so a reader can start anywhere. A new segment starts at a turn boundary (a `result` record) once
    the current one exceeds `segment_bytes`; a segment whose last record has been ACKNOWLEDGED and
    that is not the current one is deleted at the next turn boundary."""

    def __init__(self, directory, segment_bytes=JOURNAL_SEGMENT_BYTES):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.segment_bytes = int(segment_bytes)
        self.next_offset = 0
        self.acked = ACK_NONE
        self._index: list[tuple[int, int]] = []       # offset -> (segment, byte position)
        self._seg_first: dict[int, int] = {}          # segment -> first offset in it
        self._seg_last: dict[int, int] = {}           # segment -> last offset in it
        self._seg = 0
        self._fh = None
        self._pos = 0
        self._open_segment(0)

    def _path(self, seg: int) -> Path:
        return self.dir / ("journal-%d.jsonl" % seg)

    def _open_segment(self, seg: int) -> None:
        if self._fh is not None:
            self._fh.close()
        self._seg = seg
        self._fh = open(self._path(seg), "ab")
        self._pos = self._fh.tell()
        self._seg_first.setdefault(seg, self.next_offset)

    def append(self, record: dict) -> int:
        """Append one record; returns its offset. Flushed to the OS on every append (a kernel that
        attaches reads the file the host writes)."""
        line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
        off = self.next_offset
        self._index.append((self._seg, self._pos))
        self._seg_last[self._seg] = off
        self._fh.write(line)
        self._fh.flush()
        self._pos += len(line)
        self.next_offset = off + 1
        if record.get("type") == "result":
            self._turn_boundary()
        return off

    def _turn_boundary(self) -> None:
        # rotate when the current segment is past its size; drop segments the kernel has fully acknowledged
        if self._pos >= self.segment_bytes:
            self._open_segment(self._seg + 1)
        for seg in sorted(self._seg_first):
            if seg == self._seg:
                continue
            last = self._seg_last.get(seg)
            if last is not None and last <= self.acked:
                try:
                    os.unlink(self._path(seg))
                except OSError:
                    pass
                self._seg_first.pop(seg, None)
                self._seg_last.pop(seg, None)

    def ack(self, offset: int) -> None:
        if offset > self.acked:
            self.acked = min(int(offset), self.next_offset - 1)

    def segments(self) -> list[int]:
        return sorted(self._seg_first)

    def read_from(self, offset: int):
        """Yield (offset, record) for every record from `offset` to the end, from the files. A record
        whose segment was deleted (acknowledged long ago) is skipped: the caller asked for less than it
        acknowledged, which only a wrong ack can produce, and the kernel's derived state is rebuilt
        from the transcript anyway."""
        offset = max(0, int(offset))
        while offset < self.next_offset:
            seg, pos = self._index[offset]
            p = self._path(seg)
            if not p.exists():
                offset = self._seg_last.get(seg, offset) + 1 if seg in self._seg_last else offset + 1
                continue
            with open(p, "rb") as fh:
                fh.seek(pos)
                for line in fh:
                    if offset >= self.next_offset:
                        return
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        rec = None
                    if rec is not None:
                        yield offset, rec
                    offset += 1
                    if offset < self.next_offset and self._index[offset][0] != seg:
                        break

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def read_journal_dir(directory, offset: int = 0):
    """Read an ORPHAN journal (its host is gone) from `offset` to the end without an index: segments in
    number order, counting records. The kernel's replay-only transport uses this. Pure on the files."""
    d = Path(directory)
    segs = sorted(int(p.stem.split("-", 1)[1]) for p in d.glob("journal-*.jsonl") if p.stem.split("-", 1)[1].isdigit())
    # a deleted early segment shifts nothing: offsets are ordinals of the records that still exist only
    # when no segment was dropped; the host drops only fully-acknowledged segments, so counting from the
    # first present segment's first offset is exact when the caller's offset lies in a present segment
    n = 0
    for seg in segs:
        with open(d / ("journal-%d.jsonl" % seg), "rb") as fh:
            for line in fh:
                if n >= offset:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        rec = None
                    if rec is not None:
                        yield n, rec
                n += 1


# ── parked control requests ─────────────────────────────────────────────────────────────────────
class Parked:
    """Control requests the CLI sent that no kernel has answered yet: parked while unattached, listed in
    `hello` on attach, dropped on the CLI's own cancel, and (hooks only) answered by the host itself
    after `self_answer_s` of parking. `answered` records ids the host or a kernel has answered, so a late
    duplicate answer is dropped."""

    def __init__(self, self_answer_s=HOOK_SELF_ANSWER_S):
        self.self_answer_s = float(self_answer_s)
        self.open: dict[str, dict] = {}        # request id -> {kind, offset, t, callback_id, event}
        self.answered: set[str] = set()

    def park(self, request: dict, offset: int, now: float) -> None:
        rid = str(request.get("request_id") or "")
        req = request.get("request") if isinstance(request.get("request"), dict) else {}
        if not rid or rid in self.answered:
            return
        self.open[rid] = {"kind": str(req.get("subtype") or ""), "offset": offset, "t": now,
                          "callback_id": req.get("callback_id"), "tool_use_id": req.get("tool_use_id"),
                          "event": _hook_event_of(req)}

    def cancel(self, rid: str) -> bool:
        return self.open.pop(str(rid), None) is not None

    def answer(self, rid: str) -> bool:
        """A response reached the CLI for `rid`: True when it was open (first answer), False when it was
        already answered or never parked (a dead kernel's leftover, dropped by the caller)."""
        rid = str(rid)
        was = self.open.pop(rid, None) is not None
        if rid in self.answered:
            return False
        self.answered.add(rid)
        return True

    def due_hooks(self, now: float) -> list[str]:
        """The parked hook callbacks that have waited `self_answer_s` or longer, oldest first."""
        due = [(v["t"], rid) for rid, v in self.open.items()
               if v["kind"] == "hook_callback" and now - v["t"] >= self.self_answer_s]
        return [rid for _, rid in sorted(due)]

    def ids(self) -> list[str]:
        return sorted(self.open, key=lambda r: self.open[r]["offset"])


def _hook_event_of(req: dict) -> str:
    inp = req.get("input") if isinstance(req.get("input"), dict) else {}
    return str(inp.get("hook_event_name") or "")


def neutral_hook_response(request_id: str, event: str) -> dict:
    """The control_response frame the host writes for a parked hook it answers itself."""
    return {"type": "control_response",
            "response": {"subtype": "success", "request_id": request_id,
                         "response": dict(HOOK_NEUTRAL_OUTPUT.get(event, {}))}}


# ── the spawn specification ─────────────────────────────────────────────────────────────────────
def spec_to_options(spec: dict, stderr_cb):
    """A ClaudeAgentOptions from the spec's plain-data fields, plus the host's stderr relay. Only the
    SDK's fields; the host's own keys (SPEC_HOST_KEYS) are left out. Requires the SDK."""
    from claude_agent_sdk import ClaudeAgentOptions
    kw = {k: spec[k] for k in SPEC_FIELDS if k in spec and spec[k] is not None}
    kw["stderr"] = stderr_cb
    return ClaudeAgentOptions(**kw)


class PipeCliTransport:
    """The host's built-in transport for a CLI when the SDK is NOT importable: the hermetic tests and
    CI, which install no SDK. Same duck-typed surface as the SDK's SubprocessCLITransport (connect,
    read_messages, write, end_input, close, pid); the command line carries the stream-json flags and
    the spec's permission tool and resume fields only. A real install always has the SDK (the kernel
    refuses SDK sessions without it), so this never drives a real CLI; it drives the fake one."""

    def __init__(self, spec: dict, stderr_cb):
        self.spec = spec
        self._stderr_cb = stderr_cb
        self.proc = None
        self._stderr_task = None

    @property
    def pid(self):
        return self.proc.pid if self.proc else None

    def _argv(self) -> list[str]:
        s = self.spec
        argv = [str(s["cli_path"]), "--output-format", "stream-json", "--verbose", "--input-format", "stream-json"]
        if s.get("permission_prompt_tool_name"):
            argv += ["--permission-prompt-tool", str(s["permission_prompt_tool_name"])]
        if s.get("permission_mode"):
            argv += ["--permission-mode", str(s["permission_mode"])]
        if s.get("resume"):
            argv.append("--resume=%s" % s["resume"])
        if s.get("session_id"):
            argv.append("--session-id=%s" % s["session_id"])
        for flag, value in (s.get("extra_args") or {}).items():
            argv.append("--%s" % flag if value is None else "--%s=%s" % (flag, value))
        return argv

    async def connect(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"
        env.update({str(k): str(v) for k, v in (self.spec.get("env") or {}).items()})
        if self.spec.get("cwd"):
            env["PWD"] = str(self.spec["cwd"])
        self.proc = await asyncio.create_subprocess_exec(
            *self._argv(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=self.spec.get("cwd") or None, env=env,
            limit=int(self.spec.get("max_buffer_size") or 100 * 1024 * 1024))
        self._stderr_task = asyncio.ensure_future(self._relay_stderr())

    async def _relay_stderr(self):
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    return
                self._stderr_cb(line.decode("utf-8", "replace").rstrip("\n"))
        except Exception:
            return

    async def read_messages(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj
        await self.proc.wait()

    async def write(self, data: str) -> None:
        self.proc.stdin.write(data.encode("utf-8"))
        await self.proc.stdin.drain()

    async def end_input(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:
            pass

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            await self.proc.wait()

    @property
    def returncode(self):
        return self.proc.returncode if self.proc else None


def sdk_importable() -> bool:
    import importlib.util
    return importlib.util.find_spec("claude_agent_sdk") is not None


# ── the host ────────────────────────────────────────────────────────────────────────────────────
class SessionHost:
    """One host process: see the module docstring. Constructed from the spec path; `run()` is the
    whole life."""

    def __init__(self, spec_path, lease_api=None, now=None):
        self.spec_path = Path(spec_path)
        with open(self.spec_path) as f:
            self.spec = json.load(f)
        self.sid = str(self.spec["sid"])
        self.name = str(self.spec.get("name") or self.sid[:8])
        self.state_dir = Path(self.spec["state_dir"])
        self.dir = self.spec_path.parent
        self.sock_path = self.state_dir / "hosts" / (self.sid[:8] + ".sock")
        self.log_path = self.dir / "host.log"
        self.journal = Journal(self.dir)
        self.parked = Parked(float(self.spec.get("hook_self_answer_s") or HOOK_SELF_ANSWER_S))
        self.grace_s = float(self.spec.get("unattached_grace_s") or UNATTACHED_GRACE_DEFAULT_S)
        self.version = str(self.spec.get("version") or "")
        self.now = now or time.time
        self.lease_api = lease_api or _lease_api()
        self.transport = None
        self.cli_pid = None
        self.cli_start = None
        self.fsid = str(self.spec.get("resume") or self.spec.get("session_id") or "")
        self.attached = None            # the attached kernel's writer + identity, or None
        self.kernel = None
        self.inflight = 0               # user messages fed minus results seen (the idle judgement)
        self.idle_since = self.now()
        self.exit_info = None
        self.ending = None              # (deadline, cause) once `end` was requested
        self._replaying = False
        self._live_backlog: list[tuple[int, dict]] = []
        self._server = None
        self._stop = None
        self._kernel_requests: set[str] = set()   # control_request ids the attached kernel(s) sent
        self._reader_behind_noted = False
        self._read_count = 0

    # ── host.log: never a spec field, never an environment value ──
    def log(self, kind: str, **fields) -> None:
        row = {"t": round(self.now(), 3), "kind": str(kind)}
        for k, v in fields.items():
            if v is None:
                continue
            row[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        except Exception:
            pass

    # ── lease ──
    def _write_lease(self) -> None:
        if self.cli_pid is None or self.cli_start is None:
            return
        lease = {"sid": self.sid, "fsid": self.fsid or self.sid, "name": self.name, "pid": int(self.cli_pid),
                 "start": self.cli_start, "holder": {"pid": os.getpid(), "start": self.lease_api["proc_start"](os.getpid()) or "",
                                                     "kind": "host"},
                 "version": self.version, "spawnedAt": int(self.now()), "t": self.now()}
        try:
            self.lease_api["write_lease"](self.state_dir, lease)
        except Exception as e:
            self.log("lease-write-failed", error=type(e).__name__)

    async def _beat(self) -> None:
        while self.exit_info is None:
            await asyncio.sleep(LEASE_HEARTBEAT_S)
            self._write_lease()

    # ── the CLI ──
    def _on_stderr(self, line: str) -> None:
        if self.attached is not None:
            self._send(self.attached, {"t": "stderr", "line": line})

    async def _spawn(self) -> None:
        if sdk_importable():
            from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

            async def _no_prompt():
                if False:
                    yield {}
            self.transport = SubprocessCLITransport(prompt=_no_prompt(), options=spec_to_options(self.spec, self._on_stderr))
            await self.transport.connect()
            self.cli_pid = self.transport._process.pid
            self.log("cli-spawned", transport="sdk", cliPid=self.cli_pid)
        else:
            self.transport = PipeCliTransport(self.spec, self._on_stderr)
            await self.transport.connect()
            self.cli_pid = self.transport.pid
            self.log("cli-spawned", transport="pipe-fallback", cliPid=self.cli_pid)
        self.cli_start = self.lease_api["proc_start"](self.cli_pid)
        self._write_lease()

    async def _read_cli(self) -> None:
        cause = "died"
        try:
            async for msg in self.transport.read_messages():
                self._read_count += 1
                off = self.journal.append(msg)
                self._track(msg, off)
                if self._read_count - self.journal.next_offset > READER_BEHIND_RECORDS and not self._reader_behind_noted:
                    self._reader_behind_noted = True
                    self.log("reader-behind", read=self._read_count, journaled=self.journal.next_offset)
                    if self.attached is not None:
                        self._send(self.attached, {"t": "fault", "kind": "reader-behind", "text": "the journal lags the CLI's output"})
                if self.attached is not None and not self._replaying:
                    self._send(self.attached, {"t": "out", "offset": off, "data": msg})
                elif self.attached is not None:
                    self._live_backlog.append((off, msg))
        except Exception as e:
            # the failing frame, never the exception's text (it could carry a line of the CLI's output)
            tb = traceback.extract_tb(e.__traceback__)
            where = "%s:%d:%s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].name) if tb else "?"
            self.log("cli-stream-ended", error=type(e).__name__, at=where)
        if self.ending is not None:
            cause = self.ending[1]
        code = getattr(getattr(self.transport, "_process", None), "returncode", None)
        if code is None:
            code = getattr(self.transport, "returncode", None)
        self.exit_info = {"t": "exit", "code": code, "signal": None, "cause": cause}
        self.log("cli-exited", code=code, cause=cause)
        if self.attached is not None:
            self._send(self.attached, self.exit_info)
        if self._stop is not None:
            self._stop.set()

    def _track(self, msg: dict, off: int) -> None:
        """Bookkeeping per message: the fsid from the init, the turn count, parked requests."""
        mt = msg.get("type")
        if mt == "system" and msg.get("subtype") == "init" and msg.get("session_id"):
            if str(msg["session_id"]) != self.fsid:
                self.fsid = str(msg["session_id"])
                self._write_lease()
        elif mt == "result":
            self.inflight = max(0, self.inflight - 1)
            if self.inflight == 0:
                self.idle_since = self.now()
        elif mt == "control_request":
            if self.attached is None:
                self.parked.park(msg, off, self.now())
                self.log("parked", requestId=str(msg.get("request_id") or ""),
                         subtype=str((msg.get("request") or {}).get("subtype") or ""))
        elif mt == "control_cancel_request":
            if self.parked.cancel(str(msg.get("request_id") or "")):
                self.log("parked-cancelled", requestId=str(msg.get("request_id") or ""))

    async def _self_answer_loop(self) -> None:
        while self.exit_info is None:
            await asyncio.sleep(1.0)
            if self.attached is not None:
                continue
            for rid in self.parked.due_hooks(self.now()):
                entry = self.parked.open.get(rid) or {}
                event = entry.get("event") or ""
                frame = neutral_hook_response(rid, event)
                try:
                    await self.transport.write(json.dumps(frame) + "\n")
                except Exception as e:
                    self.log("self-answer-failed", requestId=rid, error=type(e).__name__)
                    continue
                self.parked.answer(rid)
                self.log("hook-self-answered", requestId=rid, event=event, callbackId=str(entry.get("callback_id") or ""),
                         toolUseId=str(entry.get("tool_use_id") or ""), parkedS=round(self.now() - float(entry.get("t") or self.now()), 1))

    async def _grace_loop(self) -> None:
        while self.exit_info is None:
            await asyncio.sleep(1.0)
            now = self.now()
            if self.ending is not None:
                if now >= self.ending[0]:
                    self.ending = (float("inf"), "end-forced")
                    self.log("end-forced", cliPid=self.cli_pid)
                    try:
                        os.kill(int(self.cli_pid), signal.SIGKILL)
                    except (ProcessLookupError, TypeError):
                        pass
                continue
            if self.attached is None and self.inflight == 0 and now - self.idle_since >= self.grace_s:
                self.log("unattached-grace-expired", idleS=round(now - self.idle_since, 1))
                await self._end(END_GRACE_DEFAULT_S, "eof-grace")

    async def _end(self, grace: float, cause: str) -> None:
        if self.ending is not None:
            return
        self.ending = (self.now() + float(grace), cause)
        self.log("end-requested", cause=cause, graceS=float(grace))
        try:
            await self.transport.end_input()
        except Exception as e:
            self.log("end-input-failed", error=type(e).__name__)

    # ── the socket ──
    def _send(self, writer, frame: dict) -> None:
        try:
            writer.write(encode_frame(frame))
        except Exception:
            pass

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        fr = FrameReader()
        attached_here = False
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                for frame in fr.feed(chunk):
                    t = frame.get("t")
                    if t == "attach":
                        if self.attached is not None and self.attached is not writer:
                            self._send(writer, {"t": "busy", "kernel": self.kernel})
                            continue
                        attached_here = True
                        await self._attach(writer, frame)
                    elif t == "ping":
                        self._send(writer, {"t": "pong"})
                    elif not attached_here:
                        self._send(writer, {"t": "fault", "kind": "not-attached", "text": "attach first"})
                    elif t == "in":
                        await self._forward_in(str(frame.get("data") or ""))
                    elif t == "ack":
                        try:
                            self.journal.ack(int(frame.get("offset")))
                        except (TypeError, ValueError):
                            pass
                    elif t == "signal":
                        sig = {"INT": signal.SIGINT, "KILL": signal.SIGKILL, "TERM": signal.SIGTERM}.get(str(frame.get("sig") or "").upper())
                        if sig is not None and self.cli_pid:
                            try:
                                os.kill(int(self.cli_pid), sig)
                                self.log("signalled", sig=str(frame.get("sig")))
                            except ProcessLookupError:
                                pass
                    elif t == "end":
                        g = frame.get("grace")
                        await self._end(float(g) if g is not None else END_GRACE_DEFAULT_S, "end")
                    elif t == "detach":
                        self.log("detached", kernelPid=(self.kernel or {}).get("pid"))
                        self._detach(writer)
                        attached_here = False
                        return
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        finally:
            if attached_here and self.attached is writer:
                self.log("kernel-lost", kernelPid=(self.kernel or {}).get("pid"))
                self._detach(writer)
            try:
                writer.close()
            except Exception:
                pass

    def _detach(self, writer) -> None:
        if self.attached is writer:
            self.attached = None
            self.kernel = None
            self.idle_since = self.now() if self.inflight == 0 else self.idle_since

    async def _attach(self, writer, frame: dict) -> None:
        self.kernel = frame.get("kernel") if isinstance(frame.get("kernel"), dict) else {}
        try:
            ack = int(frame.get("ack", ACK_NONE))
        except (TypeError, ValueError):
            ack = ACK_NONE
        self.attached = writer
        self._replaying = True
        self._live_backlog = []
        self.log("attached", kernelPid=self.kernel.get("pid"), ack=ack, next=self.journal.next_offset)
        self._send(writer, {"t": "hello", "protocol": PROTOCOL_VERSION,
                            "host": {"pid": os.getpid(), "start": self.lease_api["proc_start"](os.getpid()) or "", "version": self.version},
                            "cli": {"pid": self.cli_pid, "start": self.cli_start, "fsid": self.fsid},
                            "journal": {"next": self.journal.next_offset}, "parked": self.parked.ids(),
                            "exited": self.exit_info is not None})
        # replay from the acknowledged offset; records that arrive meanwhile queue and follow in order
        n = 0
        for off, rec in self.journal.read_from(ack + 1):
            self._send(writer, {"t": "out", "offset": off, "data": rec})
            n += 1
            if n % 200 == 0:
                await writer.drain()
        for off, rec in self._live_backlog:
            self._send(writer, {"t": "out", "offset": off, "data": rec})
        self._live_backlog = []
        self._replaying = False
        if self.exit_info is not None:
            self._send(writer, self.exit_info)
        await writer.drain()

    async def _forward_in(self, data: str) -> None:
        """One line from the kernel for the CLI's stdin: forwarded at once (an initialize is answered
        without waiting behind a replay). Bookkeeping: a user message opens a turn; a control_response
        for a request the host already answered, or one nobody parked and nobody issued, is dropped."""
        try:
            obj = json.loads(data)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            if obj.get("type") == "user":
                self.inflight += 1
            elif obj.get("type") == "control_request":
                self._kernel_requests.add(str(obj.get("request_id") or ""))
            elif obj.get("type") == "control_response":
                rid = str(((obj.get("response") or {}).get("request_id")) or "")
                if rid in self.parked.answered:
                    self.log("late-answer-dropped", requestId=rid)
                    return
                self.parked.answer(rid)
        try:
            await self.transport.write(data if data.endswith("\n") else data + "\n")
        except Exception as e:
            self.log("write-failed", error=type(e).__name__)
            if self.attached is not None:
                self._send(self.attached, {"t": "fault", "kind": "write-failed", "text": type(e).__name__})

    # ── life ──
    async def run(self) -> int:
        self._stop = asyncio.Event()
        self.log("host-started", hostPid=os.getpid())
        # the CLI first, the socket second: a kernel that finds the socket finds a CLI behind it (an attach
        # before the spawn would report no CLI pid and fail its first write)
        try:
            await self._spawn()
        except Exception as e:
            self.log("cli-spawn-failed", error=type(e).__name__)
            self.exit_info = {"t": "exit", "code": None, "signal": None, "cause": "spawn-failed", "error": type(e).__name__}
            return 1
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.sock_path.unlink()
        except OSError:
            pass
        self._server = await asyncio.start_unix_server(self._on_client, path=str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        self.log("socket-ready", sock=str(self.sock_path.name))
        tasks = [asyncio.ensure_future(self._read_cli()), asyncio.ensure_future(self._beat()),
                 asyncio.ensure_future(self._self_answer_loop()), asyncio.ensure_future(self._grace_loop())]
        await self._stop.wait()
        # the CLI is gone: give an attached kernel a moment to read the exit, then leave
        for _ in range(20):
            if self.attached is None:
                break
            await asyncio.sleep(0.05)
        for t in tasks:
            t.cancel()
        try:
            await self.transport.close()          # the pipes and the process object, before the loop closes
        except Exception:
            pass
        try:
            self.lease_api["remove_lease"](self.state_dir, self.sid)
        except Exception:
            pass
        self.journal.close()
        self._server.close()
        try:
            self.sock_path.unlink()
        except OSError:
            pass
        self.log("host-exited")
        return 0


def _lease_api() -> dict:
    """The stage 1 lease helpers from kernel/sdk_backend.py, loaded the kernel's way (a file-path load under
    a stable module name; the kernel's own copy when this runs inside the kernel)."""
    import importlib.util
    here = Path(__file__).resolve().parent
    sb = sys.modules.get("romp_sdk_backend")
    if sb is None:
        spec = importlib.util.spec_from_file_location("romp_loadsource", str(here / "loadsource.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        sb = mod.load_source("romp_sdk_backend_hostside", here / "sdk_backend.py")
    return {"write_lease": sb.write_lease, "remove_lease": sb.remove_lease, "proc_start": sb.proc_start}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        sys.stderr.write("usage: romp-session-host <spawn.json>\n")
        return 2
    host = SessionHost(argv[0])
    try:
        return asyncio.run(host.run())
    except Exception:
        host.log("host-crashed", error=traceback.format_exc().splitlines()[-1][:200])
        return 1


if __name__ == "__main__":
    sys.exit(main())
