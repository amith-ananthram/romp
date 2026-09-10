#!/usr/bin/env python3
"""bench_boot_parse: what a kernel boot reads, against transcript size (T323, the user's scaling criterion).

Builds a hermetic world of N synthetic sessions whose transcripts are S times a base of invented text, boots a real
kernel from a given tree (bin/romp-kernel of that tree) with no client, waits for the boot to settle, and reads what the
boot cost: bytes the kernel process read from files (/proc/<pid>/io rchar), its resident size, the cold parses /perf
counts (where the tree reports them), and the time to first /healthz. One boot per (tree, size), one at a time, small
worlds (a measurement, not load); everything under a temporary root, nothing against live state. Then draws cost against
size with cleanplots: a flat or O(new records) curve passes the criterion, a curve tracking the file size fails.

    capped bash -c 'uvx --with cleanplots --with matplotlib --with pandas python scripts/bench_boot_parse.py \\
        --tree before=/path/to/main-checkout --tree after=/path/to/branch-worktree --sizes 1,4,16 --sessions 6 \\
        --out ~/.local/state/romp-research/T323-stage1-bench'
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT))   # tests/ as scripts and as the `tests` package
WORDS = ("fixture", "suite", "backoff", "jitter", "cap", "retry", "review", "branch", "merge", "green", "README", "wire",
         "widget", "loop", "notes", "api", "tests", "web", "minutes", "decision")


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def synthetic_transcript(t0, turns, seed):
    """`turns` invented user/assistant turns (about 1.2 KB a turn), a proper parentUuid chain, all before the boot."""
    import random
    rnd = random.Random(seed)
    recs, parent, t = [], None, t0
    for i in range(turns):
        u = "u%06d" % i; a = "a%06d" % i
        text = " ".join(rnd.choice(WORDS) for _ in range(40))
        recs.append({"type": "user", "timestamp": iso(t), "uuid": u, "parentUuid": parent, "promptSource": "typed",
                     "message": {"role": "user", "content": "please " + text}})
        body = " ".join(rnd.choice(WORDS) for _ in range(120))
        recs.append({"type": "assistant", "timestamp": iso(t + 20), "uuid": a, "parentUuid": u,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": body}], "stop_reason": "end_turn"}})
        parent, t = a, t + 60
    return recs


def build_world(lab, sessions, turns, seed=1):
    import test_ship_reship as _lab
    state = os.path.join(lab, "xdg", "romp"); claude = os.path.join(lab, "claude"); cwd = os.path.join(lab, "proj")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    os.makedirs(cwd, exist_ok=True)
    proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
    os.makedirs(proj, exist_ok=True)
    t0 = int(time.time()) - 86400
    total = 0
    for i in range(sessions):
        sid = "%08x-1111-2222-3333-444444444444" % (0xa0000000 + i)
        name = "s%02d" % i
        Path(state, "names", sid).write_text("%s\t%s\t#9cd2ff\t#0c1a2e\n" % (name, cwd))
        Path(state, "sdk", sid + ".json").write_text(json.dumps({"sid": sid, "name": name, "cwd": cwd, "mode": "auto",
                                                                 "effort": "high", "lastSid": sid, "alive": True}))
        Path(state, "states", sid + ".jsonl").write_text(json.dumps({"t": t0 + turns * 60, "state": "idle"}) + "\n")
        p = Path(proj, sid + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in synthetic_transcript(t0, turns, seed + i)))
        total += p.stat().st_size
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
    return _lab, state, claude, total


def _get(port, token, path, timeout=5):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers={"X-Romp-Token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _proc(pid):
    out = {}
    try:
        for ln in open("/proc/%d/io" % pid):
            k, v = ln.split(":"); out[k.strip()] = int(v)
    except OSError:
        pass
    try:
        for ln in open("/proc/%d/status" % pid):
            if ln.startswith("VmRSS"):
                out["rssKb"] = int(ln.split()[1])
    except OSError:
        pass
    return out


def boot_once(tree, dist, sessions, turns, settle_s):
    lab = tempfile.mkdtemp(prefix="romp-bench-")
    try:
        _lab, state, claude, total_bytes = build_world(lab, sessions, turns)
        port, token = _free_port(), "bench-token"
        env = _lab.kernel_env(lab, claude, dist, port, token, ROMP_HOST_NAME="TESTHOST")
        t_start = time.perf_counter()
        proc = subprocess.Popen([os.path.join(tree, "bin", "romp-kernel")], stdout=open(os.path.join(lab, "kernel.log"), "w"),
                                stderr=subprocess.STDOUT, env=env)
        first_serve = None
        for _ in range(240):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
                first_serve = time.perf_counter() - t_start
                break
            except Exception:
                time.sleep(0.25)
        if first_serve is None:
            proc.kill(); return {"error": "never served"}
        time.sleep(settle_s)                    # a fixed settle: the boot warm, the judges' first pass, a few pusher cycles
        io = _proc(proc.pid)
        try:
            perf = _get(port, token, "/perf")
            parses = perf.get("parses") or {}
        except Exception:
            parses = {}
        row = {"tree": tree, "sessions": sessions, "turnsPerSession": turns, "worldBytes": total_bytes,
               "firstServeS": round(first_serve, 2), "settleS": settle_s, "readBytes": io.get("rchar"),
               "rssKb": io.get("rssKb"), "parses": parses.get("total"), "parseBytes": parses.get("bytes"),
               "judgeParses": parses.get("judge")}
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        return row
    finally:
        shutil.rmtree(lab, ignore_errors=True)


def draw(rows, out):
    """Three panels of boot cost against the transcripts' size on disk: the kernel's own cold parses (the stage 1
    number; a tree without the counter drew every living session by construction, which the panel says), bytes the
    process read, and its resident size. Axes start at zero; flat is the goal."""
    import cleanplots as cp
    import matplotlib
    matplotlib.use("Agg")
    labels = sorted({r["label"] for r in rows})
    cols = list(cp.colors)
    f, axs = cp.fig(rows=1, cols=3, w=19, h=4.8)
    f.subplots_adjust(wspace=0.45)
    tops = [0.0, 0.0, 0.0]
    for i, label in enumerate(labels):
        rs = sorted([r for r in rows if r["label"] == label and not r.get("error")], key=lambda r: r["worldBytes"])
        xs = [r["worldBytes"] / 1048576 for r in rs]
        c = cols[i % len(cols)]
        counted = all(r.get("parses") is not None for r in rs)
        parses = [r["parses"] if counted else r["sessions"] for r in rs]
        axs[0].line(xs, parses, label=label if counted else label + " (every session, by construction)", color=c, marker="o",
                    linestyle="-" if counted else "--")
        axs[1].line(xs, [(r["readBytes"] or 0) / 1048576 for r in rs], label=label, color=c, marker="o")
        axs[2].line(xs, [(r["rssKb"] or 0) / 1024 for r in rs], label=label, color=c, marker="o")
        tops = [max(tops[0], max(parses)), max(tops[1], max((r["readBytes"] or 0) / 1048576 for r in rs)),
                max(tops[2], max((r["rssKb"] or 0) / 1024 for r in rs))]
    axs[0].clean(xlabel="Transcripts on disk (MB), all sessions", ylabel="Cold parses by the kernel at boot\nzero is the goal")
    axs[1].clean(xlabel="Transcripts on disk (MB), all sessions", ylabel="Bytes the process read by settle (MB)\nflat is the goal")
    axs[2].clean(xlabel="Transcripts on disk (MB), all sessions", ylabel="Resident size at settle (MB)\nflat is the goal")
    for ax, top in zip(axs, tops):
        ax.set_xlim(0, None); ax.set_ylim(0, top * 1.25 if top else 1)
        ax.set_yticks([0, round(top, 1) if top < 10 else round(top)])
    path = os.path.join(out, "boot_cost_vs_size.png")
    f.savefig(path, dpi=150, bbox_inches="tight")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tree", action="append", required=True, help="label=path of a romp checkout to boot from")
    ap.add_argument("--sizes", default="1,4,16", help="multipliers of the base turns per session")
    ap.add_argument("--base-turns", type=int, default=150, help="turns per session at size 1 (about 180 KB a transcript)")
    ap.add_argument("--sessions", type=int, default=6)
    ap.add_argument("--settle", type=float, default=8.0, help="seconds after first serve before reading the cost")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-figure", action="store_true")
    ap.add_argument("--figure-only", action="store_true", help="redraw from <out>/bench.json without booting anything")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    if a.figure_only:
        with open(os.path.join(a.out, "bench.json")) as f:
            sys.stdout.write("figure " + draw(json.load(f)["rows"], a.out) + "\n")
        return 0
    trees = [(s.split("=", 1)[0], os.path.abspath(s.split("=", 1)[1])) for s in a.tree]
    sizes = [int(x) for x in a.sizes.split(",")]
    ext = os.path.join(trees[0][1], "vscode-extension")   # one bundle serves every boot (the kernel only serves it)
    b = subprocess.run(["node", "esbuild.js"], cwd=ext, capture_output=True, text=True)
    if b.returncode != 0:
        sys.stderr.write("esbuild failed in %s: %s\n" % (ext, (b.stderr or b.stdout)[-300:])); return 1
    dist = os.path.join(ext, "dist")
    rows = []
    for label, tree in trees:
        for s in sizes:
            row = boot_once(tree, dist, a.sessions, a.base_turns * s, a.settle)
            row.update(label=label, size=s)
            rows.append(row)
            sys.stdout.write(json.dumps(row) + "\n"); sys.stdout.flush()
    with open(os.path.join(a.out, "bench.json"), "w") as f:
        json.dump({"rows": rows, "t": int(time.time())}, f, indent=1)
    if not a.no_figure:
        sys.stdout.write("figure " + draw(rows, a.out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
