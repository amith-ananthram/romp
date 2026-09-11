#!/usr/bin/env python3
"""bench_shared_parse: the resident memory the SECOND parsed tree costs when both the display and the judges parse the
same transcripts, against transcript size (T323 stage 2's acceptance measurement).

For each romp tree given, a child process loads that tree's event model, judge and kernel against a temporary state
root (nothing against live state), builds N synthetic sessions whose transcripts are S times a base of invented text,
then asks BOTH sides for every session (the kernel's _parse, the way the chat build asks, and jd.parsed_session, the
way a judge pass asks) and reports the process's CURRENT resident size (VmRSS from /proc/self/statm, never the peak)
after the display's parses and again after the judges', and the cold parses counted. One child per (tree, size),
sequential, small worlds, under `capped`. The figure draws, per tree, the second side's resident delta (after both
minus after the display alone) against size: the cost of the second tree, present before the shared store and gone
after it. The bars are one measurement each, not a distribution.

    capped bash -c 'uvx --with cleanplots --with matplotlib --with pandas python scripts/bench_shared_parse.py \\
        --tree before=/path/to/stage1-tree --tree after=/path/to/stage2-tree --sizes 1,4,16 --sessions 6 --out DIR'
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

CHILD = r'''
import json, os, resource, sys, tempfile, time, random
tree, sessions, turns = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
root = tempfile.mkdtemp(prefix="romp-shared-bench-")
os.environ["XDG_STATE_HOME"] = os.path.join(root, "state"); os.makedirs(os.environ["XDG_STATE_HOME"] + "/romp/states", exist_ok=True)
os.environ.pop("ROMP_STATE_DIR", None); os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(root, "claude")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"; os.environ.setdefault("ROMP_SERVE_TOKEN", "bench"); os.environ["ROMP_MANAGER_PORT"] = "1"
sys.path.insert(0, os.path.join(tree, "tests"))
from romp_load import load_source
load_source("romp_event_model", os.path.join(tree, "bin", "romp-event-model"))
jd = load_source("romp_judge", os.path.join(tree, "bin", "romp-judge"))
km = load_source("romp_kernel_bench", os.path.join(tree, "bin", "romp-kernel"))
WORDS = ("fixture", "suite", "backoff", "jitter", "cap", "retry", "review", "branch", "merge", "green", "README", "wire")
def transcript(path, seed):
    rnd = random.Random(seed); parent = None; t0 = int(time.time()) - 86400
    with open(path, "w") as f:
        for i in range(turns):
            u, a = "u%06d" % i, "a%06d" % i
            ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t0 + i * 60))
            f.write(json.dumps({"type": "user", "timestamp": ts, "uuid": u, "parentUuid": parent, "promptSource": "typed",
                                "message": {"role": "user", "content": "please " + " ".join(rnd.choice(WORDS) for _ in range(40))}}) + "\n")
            f.write(json.dumps({"type": "assistant", "timestamp": ts, "uuid": a, "parentUuid": u,
                                "message": {"role": "assistant", "content": [{"type": "text", "text": " ".join(rnd.choice(WORDS) for _ in range(120))}], "stop_reason": "end_turn"}}) + "\n")
            parent = a
d = os.path.join(root, "tx"); os.makedirs(d)
paths = []
for i in range(sessions):
    sid = "%08x-1111-2222-3333-444444444444" % (0xb0000000 + i)
    p = os.path.join(d, sid + ".jsonl"); transcript(p, i); paths.append((sid, p))
total = sum(os.path.getsize(p) for _, p in paths)
def rss():
    with open("/proc/self/statm") as f:                 # CURRENT resident pages, not ru_maxrss (a peak: differences of peaks are not growth)
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
now = int(time.time())
r0 = rss()
for sid, p in paths:
    km._parse(p, sid, now)                 # the display's ask
r1 = rss()
for sid, p in paths:
    jd.parsed_session(sid, [p], now)      # a judge pass's ask
r2 = rss()
misses = getattr(jd, "parse_misses", lambda: None)()
print(json.dumps({"worldBytes": total, "afterKernelBytes": r1 - r0, "afterBothBytes": r2 - r0, "coldParses": misses,
                  "sessions": sessions, "turns": turns}))
import shutil; shutil.rmtree(root, ignore_errors=True)
'''


def run_child(tree, sessions, turns):
    p = subprocess.run([sys.executable, "-c", CHILD, tree, str(sessions), str(turns)], capture_output=True, text=True, timeout=600)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    if p.returncode != 0 or not line:
        return {"error": (p.stderr or p.stdout)[-400:]}
    return json.loads(line[-1])


def draw(rows, out):
    import cleanplots as cp
    import matplotlib
    matplotlib.use("Agg")
    labels = sorted({r["label"] for r in rows})
    cols = list(cp.colors)
    f, ax = cp.fig(w=9, h=5)
    top = 1.0
    for i, label in enumerate(labels):
        rs = sorted([r for r in rows if r["label"] == label and not r.get("error")], key=lambda r: r["worldBytes"])
        if not rs:
            sys.stderr.write("figure: every run of %r errored; the label is left out\n" % label); continue
        xs = [r["worldBytes"] / 1048576 for r in rs]
        ys = [max(0.0, r["afterBothBytes"] - r["afterKernelBytes"]) / 1048576 for r in rs]   # the SECOND tree's own cost
        top = max(top, max(ys))
        ax.line(xs, ys, label=label, color=cols[i % len(cols)], marker="o")
    ax.clean(xlabel="Transcripts on disk (MB), all sessions",
             ylabel="Resident size added by the judges' parse\nafter the display had parsed (MB), zero is the goal")
    ax.set_xlim(0, None); ax.set_ylim(0, top * 1.25); ax.set_yticks([0, round(top, 1) if top < 10 else round(top)])
    path = os.path.join(out, "trees_vs_size.png")
    f.savefig(path, dpi=150, bbox_inches="tight")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tree", action="append", required=True)
    ap.add_argument("--sizes", default="1,4,16")
    ap.add_argument("--base-turns", type=int, default=150)
    ap.add_argument("--sessions", type=int, default=6)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-figure", action="store_true")
    ap.add_argument("--figure-only", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    if a.figure_only:
        with open(os.path.join(a.out, "bench.json")) as f:
            sys.stdout.write("figure " + draw(json.load(f)["rows"], a.out) + "\n")
        return 0
    rows = []
    for spec in a.tree:
        label, tree = spec.split("=", 1)
        for s in (int(x) for x in a.sizes.split(",")):
            row = run_child(os.path.abspath(tree), a.sessions, a.base_turns * s)
            row.update(label=label, size=s, tree=os.path.abspath(tree))
            rows.append(row); sys.stdout.write(json.dumps(row) + "\n"); sys.stdout.flush()
    with open(os.path.join(a.out, "bench.json"), "w") as f:
        json.dump({"rows": rows}, f, indent=1)
    if not a.no_figure:
        sys.stdout.write("figure " + draw(rows, a.out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
