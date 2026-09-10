#!/usr/bin/env python3
"""Replay benchmark for the chat payload build (issue 903) — SYNTHETIC transcript only: invented text,
placeholder ids, no real session data (CLAUDE.md).

Not a test (pytest collects test_*.py only). It writes a transcript of `n_turns` turns (8 records each:
a prompt, three tool rounds of thinking + text + tool_use with a tool_result, a closing reply) into a
hermetic state root the kernel's discovery can see, then times build_session four ways: cold (parse +
build), warm (nothing changed), and twice after appending one turn — the working session's steady state,
where every push sees a grown transcript. With `profile` it prints the top cumulative functions of the
append build.

    uv run --with cryptography -- python tests/bench_chatbuild.py . 2600            # ~26k events
    uv run --with cryptography -- python tests/bench_chatbuild.py . 2600 profile

Numbers that shaped the fold (this machine, 28.6k events, no profiler): before — warm 1,101 ms, append
1,979 ms; after — warm 116 ms, append 342 ms."""

import cProfile, io, json, os, pstats, sys, tempfile, time
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

repo = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
n_turns = int(sys.argv[2]) if len(sys.argv) > 2 else 300
do_profile = len(sys.argv) > 3 and sys.argv[3] == "profile"
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
BIN = os.path.join(repo, "bin")
km = load_source("romp_kernel_bench", os.path.join(BIN, "romp-kernel"))
jd = km.jd                            # the kernel's OWN judge module: rebinding a second copy changes nothing

SID = "11111111-2222-3333-4444-555555555555"
NOW = int(time.time())              # discovery is keyed to the real clock: a fixed epoch falls outside its window
T0 = NOW - 3 * 86400


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


class Gen:
    def __init__(self):
        self.n = 0
        self.parent = None
        self.t = T0

    def uid(self):
        self.n += 1
        return "aaaaaaaa-0000-0000-0000-%012d" % self.n

    def rec(self, typ, message, **extra):
        self.t += 3
        u = self.uid()
        r = {"type": typ, "timestamp": iso(self.t), "uuid": u, "parentUuid": self.parent,
             "sessionId": SID, "cwd": "/TESTDIR/notes-api", "version": "2.1.0", "gitBranch": "main",
             "message": message}
        r.update(extra)
        self.parent = u
        return r

    def turn(self, i):
        out = [self.rec("user", {"role": "user", "content": "step %d: tighten the search index and rerun the suite" % i},
                        promptSource="typed")]
        for k in range(3):                                   # three tool rounds per turn
            tid = "toolu_%06d_%d" % (i, k)
            name = ("Edit", "Bash", "Read")[k]
            inp = ({"file_path": "/TESTDIR/notes-api/search.py", "old_string": "limit = %d\n" % k,
                    "new_string": "limit = %d\n" % (k + 1)} if name == "Edit" else
                   {"command": "uv run pytest -q tests/test_search.py::test_%d" % i} if name == "Bash" else
                   {"file_path": "/TESTDIR/notes-api/tests/test_search.py"})
            out.append(self.rec("assistant", {"role": "assistant", "model": "claude-sonnet-4",
                                              "content": [{"type": "thinking", "thinking": "Considering step %d round %d." % (i, k), "signature": ""},
                                                          {"type": "text", "text": "Round %d: adjusting the `search.py` limit and checking `tests/test_search.py`." % k},
                                                          {"type": "tool_use", "id": tid, "name": name, "input": inp}],
                                              "stop_reason": "tool_use"}))
            out.append(self.rec("user", {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                                     "content": "ok: %d lines changed\n" % (k + 1) * 8}]},
                                toolUseResult={"filePath": inp.get("file_path", ""), "stdout": "ok"}))
        out.append(self.rec("assistant", {"role": "assistant", "model": "claude-sonnet-4",
                                          "content": [{"type": "text", "text": "Step %d done: the index is tighter and `tests/test_search.py` passes.\n\n- limit raised\n- suite green" % i}],
                                          "stop_reason": "end_turn"}))
        return out


td = Path(tempfile.mkdtemp())
cdir = td / "launchdir"; cdir.mkdir()
proj = td / "projects"
pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
pdir.mkdir(parents=True)
tpath = pdir / (SID + ".jsonl")
names = td / "names"; names.mkdir()
(names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
jd.NAMES, jd.PROJECTS = names, proj
jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
jd.STATE = td
km.NAMES = names
km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
km._tmux_sessions = lambda: {SID: {"state": "working", "since": NOW - 100, "model": "", "effort": "",
                                   "context": None, "compactPct": None, "color": None}}
g = Gen()
with open(tpath, "w") as f:
    for i in range(n_turns):
        for r in g.turn(i):
            f.write(json.dumps(r) + "\n")


def build(label):
    t0 = time.perf_counter()
    m = km.build_session(SID, NOW)
    ms = (time.perf_counter() - t0) * 1000
    print("%-8s %8.1f ms  events=%d" % (label, ms, len(m["events"])), flush=True)
    return m


m = build("cold")
build("warm")
# append one turn (the working session's steady state: every push sees a grown transcript)
with open(tpath, "a") as f:
    for r in g.turn(n_turns):
        f.write(json.dumps(r) + "\n")
os.utime(tpath, None)
if do_profile:
    pr = cProfile.Profile()
    pr.enable()
    build("append")
    pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(28)
    print("\n".join(l for l in s.getvalue().splitlines() if l.strip() and ("kernel" in l or "judge" in l or "event_model" in l or "ncalls" in l))[:6000])
else:
    build("append")
    with open(tpath, "a") as f:
        for r in g.turn(n_turns + 1):
            f.write(json.dumps(r) + "\n")
    os.utime(tpath, None)
    build("append2")
