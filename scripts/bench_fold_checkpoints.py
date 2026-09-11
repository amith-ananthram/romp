#!/usr/bin/env python3
"""bench_fold_checkpoints: the bytes a kernel BOOT reads for the folds' inputs, against transcript size, with and without
the folds' checkpoints (T323 stage 3's acceptance measurement).

For each romp tree given, and each size, a synthetic world is built under a temporary root: N sessions, each with a leaf
transcript (S times a base of invented turns, with background launches and edits), an agent file under its subagents/
directory, and a states log; one postal log shared by all. Then two child processes run in turn against the SAME world
and state root, each a fresh process loading the tree's event model, judge and kernel (nothing against live state):

  1. the first boot: every reader a kernel runs for a session at boot and at a client's first ask is called once per
     session (the judges' parse of the leaf, the states readers, the background-task pairing, the session meta, the agent
     gist and launches, the postal log), then the tree's exit path for checkpoints, when it has one, writes them;
  2. the second boot: the same readers again in a new process. Its bytes read, per file class, are the measurement:
     what a restart costs the folds' inputs. On a tree without checkpoints the second boot equals the first.

Bytes are counted at the reader itself (the event model's per-path counter on this branch; on a tree without it, a
counting wrapper over the module's open() for .jsonl paths), so the count is what the reader pulled, not what the page
cache served, and on this branch the checkpoint documents' own reads count too (they are a class of their own on the
figure: a document carries every fold's state, and the postal log fold's state grows with the log). The figure draws
the second boot's total against transcript size per tree, and beside it the second boot's bytes per file class on the
newest tree, so the residual that stays whole (the leaf transcript's parse, stage 4's; the postal log's whole readers)
is on the page, not left out. Decimal megabytes throughout; one measurement per point, not a distribution.

    capped bash -c 'uvx --with cleanplots --with matplotlib --with pandas python scripts/bench_fold_checkpoints.py \\
        --tree before=/path/to/main-tree --tree after=. --sizes 1,4,16 --sessions 6 --out DIR'
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

WORLD = r'''
import json, os, sys, random, time
root, sessions, turns = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
state = os.path.join(root, "state", "romp"); proj = os.path.join(root, "claude", "projects", "-w-notes-api")
for d in ("names", "sdk", "states", "timeline", "goals"): os.makedirs(os.path.join(state, d), exist_ok=True)
os.makedirs(proj, exist_ok=True); os.makedirs(os.path.join(root, "w", "notes-api"), exist_ok=True)
WORDS = ("fixture", "suite", "backoff", "jitter", "cap", "retry", "review", "branch", "merge", "green", "README", "wire")
def iso(t): return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))
t0 = int(time.time()) - 86400; sids = []
for i in range(sessions):
    sid = "%08x-1111-4222-8333-%012d" % (i + 1, i + 1); sids.append(sid)
    rnd = random.Random(i); parent = None; leaf = os.path.join(proj, sid + ".jsonl"); t = t0
    with open(leaf, "w") as f:
        for k in range(turns):
            u, a, tid = "u%d" % k, "a%d" % k, "toolu_%d_%d" % (i, k)
            f.write(json.dumps({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed",
                                "cwd": "/w/notes-api", "version": "2.1.0", "gitBranch": "web",
                                "message": {"role": "user", "content": " ".join(rnd.choice(WORDS) for _ in range(40))}}) + "\n")
            blocks = [{"type": "text", "text": " ".join(rnd.choice(WORDS) for _ in range(120))}]
            if k % 7 == 3: blocks.append({"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "sleep 1", "run_in_background": True, "description": "lint"}})
            if k % 5 == 1: blocks.append({"type": "tool_use", "id": tid + "e", "name": "Edit", "input": {"file_path": "/w/notes-api/web/app%d.ts" % k}})
            f.write(json.dumps({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": iso(t + 20), "cwd": "/w/notes-api", "version": "2.1.0",
                                "gitBranch": "web", "message": {"role": "assistant", "content": blocks, "stop_reason": "end_turn"}}) + "\n")
            parent = a; t += 60
    sub = os.path.join(proj, sid, "subagents"); os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "agent-%08x.jsonl" % (i + 1)), "w") as f:
        p2 = None
        for k in range(turns):
            s1, s2 = "s%d" % k, "r%d" % k
            f.write(json.dumps({"type": "user", "uuid": s1, "parentUuid": p2, "timestamp": iso(t0 + 60 * k), "message": {"role": "user", "content": " ".join(rnd.choice(WORDS) for _ in range(30))}}) + "\n")
            f.write(json.dumps({"type": "assistant", "uuid": s2, "parentUuid": s1, "timestamp": iso(t0 + 60 * k + 10), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_s%d_%d" % (i, k), "name": "Read", "input": {"file_path": "/w/notes-api/web/app%d.ts" % k}},
                {"type": "text", "text": " ".join(rnd.choice(WORDS) for _ in range(80))}]}}) + "\n")
            p2 = s2
    with open(os.path.join(state, "states", sid + ".jsonl"), "w") as f:
        for k in range(turns):
            f.write(json.dumps({"t": t0 + 60 * k, "state": "working"}) + "\n")
            f.write(json.dumps({"t": t0 + 60 * k + 30, "state": "waiting"}) + "\n")
    with open(os.path.join(state, "names", sid), "w") as f: f.write("worker%d\t%s\t#abcdef\n" % (i, os.path.join(root, "w", "notes-api")))
    with open(os.path.join(state, "sdk", sid + ".json"), "w") as f:
        json.dump({"sid": sid, "name": "worker%d" % i, "cwd": os.path.join(root, "w", "notes-api"), "lastSid": sid, "alive": False}, f)
with open(os.path.join(state, "timeline", "messages.jsonl"), "w") as f:
    for k in range(turns * sessions):
        a, b = sids[k % sessions], sids[(k + 1) % sessions]
        f.write(json.dumps({"ev": "sent", "id": "m%d" % k, "from_id": a, "to_id": b, "from": "worker", "body": " ".join(WORDS[:6]), "t": t0 + k, "kind": "coordinate"}) + "\n")
        f.write(json.dumps({"ev": "exec", "id": "m%d" % k, "t": t0 + k + 1}) + "\n")
print(json.dumps({"sids": sids, "proj": proj}))
'''

BOOT = r'''
import json, os, sys, time, builtins
tree, root, proj, phase = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sids = json.loads(sys.argv[5])
os.environ["XDG_STATE_HOME"] = os.path.join(root, "state"); os.environ.pop("ROMP_STATE_DIR", None)
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(root, "claude"); os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "bench"); os.environ["ROMP_MANAGER_PORT"] = "1"
sys.path.insert(0, os.path.join(tree, "tests"))
from romp_load import load_source
em = load_source("romp_event_model", os.path.join(tree, "bin", "romp-event-model"))
jd = load_source("romp_judge", os.path.join(tree, "bin", "romp-judge"))
km = load_source("romp_kernel_bench", os.path.join(tree, "bin", "romp-kernel"))
counted = {}
if not hasattr(em, "read_bytes_report"):                         # a tree without the reader's counter: count at open()
    _open = builtins.open
    class _F:
        def __init__(self, fh, path): self.fh, self.path = fh, path
        def read(self, *a):
            b = self.fh.read(*a); counted[self.path] = counted.get(self.path, 0) + len(b); return b
        def __getattr__(self, k): return getattr(self.fh, k)
        def __enter__(self): return self
        def __exit__(self, *a): self.fh.close()
        def __iter__(self): return iter(self.fh)
    def counting_open(path, mode="r", *a, **k):
        fh = _open(path, mode, *a, **k)
        return _F(fh, str(path)) if "b" in mode and str(path).endswith(".jsonl") else fh
    builtins.open = counting_open; em.open = counting_open
now = time.time()
for sid in sids:
    leaf = os.path.join(proj, sid + ".jsonl")
    jd.parsed_session(sid, [leaf], now)                        # the judges' pass: the leaf, whole (stage 4)
    km._states_awaiting_overlay(sid); km._last_machine_cut(sid); km._last_state(sid)
    km._state_intervals(sid, "working", now)
    km._bg_scan_cached(leaf); km._bg_scan_all_cached(leaf); km._session_meta(leaf)
    km._agent_launch_state(leaf)
    sub = os.path.join(proj, sid, "subagents")
    for a in sorted(os.listdir(sub)):
        km._agent_steps(os.path.join(sub, a)); km._agent_launch_ids(os.path.join(sub, a))
km._postal_index()
km._fold_records(km._postal_log_cache, jd.STATE / "timeline" / "messages.jsonl", km._postal_log_fresh, km._postal_log_step,
                 **({"ckpt": "postalLog"} if hasattr(em, "checkpoint_write_dirty") else {}))
rb = em.read_bytes_report() if hasattr(em, "read_bytes_report") else dict(counted, total=sum(counted.values()))
by = {"leaf": 0, "agent": 0, "states": 0, "postal": 0, "checkpoint": 0, "other": 0}
for p, n in rb.items():
    if p == "total": continue
    cls = ("checkpoint" if "/checkpoints/" in p else "agent" if "/subagents/" in p else "states" if "/states/" in p
           else "postal" if p.endswith("messages.jsonl") else "leaf" if p.startswith(proj) else "other")
    by[cls] += n
if phase == "first" and hasattr(em, "checkpoint_write_dirty"):
    em.checkpoint_write_dirty()                                # the exit path's write
stats = em.checkpoint_stats() if hasattr(em, "checkpoint_stats") else {}
print(json.dumps({"phase": phase, "byClass": by, "total": sum(by.values()),
                  "restored": stats.get("restored", 0), "fallbacks": stats.get("fallbacks", {})}))
'''


def run(args, timeout=900):
    p = subprocess.run([sys.executable, "-c"] + args, capture_output=True, text=True, timeout=timeout)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    if p.returncode != 0 or not line:
        raise RuntimeError((p.stderr or p.stdout)[-600:])
    return json.loads(line[-1])


def measure(tree, sessions, turns):
    root = tempfile.mkdtemp(prefix="romp-ckpt-bench-")
    try:
        world = run([WORLD, root, str(sessions), str(turns)])
        sizes = {"leaf": 0, "agent": 0, "states": 0, "postal": 0}
        for dp, _, fs in os.walk(root):
            for f in fs:
                if not f.endswith(".jsonl"):
                    continue
                p = os.path.join(dp, f); n = os.path.getsize(p)
                sizes["agent" if "/subagents/" in p else "states" if "/states/" in p else "postal" if f == "messages.jsonl" else "leaf"] += n
        first = run([BOOT, tree, root, world["proj"], "first", json.dumps(world["sids"])])
        second = run([BOOT, tree, root, world["proj"], "second", json.dumps(world["sids"])])
        return {"worldBytes": sum(sizes.values()), "sizes": sizes, "first": first, "second": second}
    finally:
        import shutil; shutil.rmtree(root, ignore_errors=True)


def draw(rows, out):
    import cleanplots as cp
    import matplotlib
    matplotlib.use("Agg")
    labels = sorted({r["label"] for r in rows})
    cols = list(cp.colors)
    f, (ax, ax2) = cp.fig(w=12, h=5, cols=2)
    top = 1.0
    for i, label in enumerate(labels):
        rs = sorted([r for r in rows if r["label"] == label and not r.get("error")], key=lambda r: r["worldBytes"])
        if not rs:
            sys.stderr.write("figure: every run of %r errored; the label is left out\n" % label); continue
        xs = [r["worldBytes"] / 1e6 for r in rs]
        ys = [r["second"]["total"] / 1e6 for r in rs]
        top = max(top, max(ys))
        ax.line(xs, ys, label=label, color=cols[i % len(cols)], marker="o")
    ax.clean(xlabel="Files on disk (MB): transcripts, agent files,\nstates logs and the postal log",
             ylabel="Bytes a restarted kernel reads\nfor the folds' inputs (MB)")
    ax.set_xlim(0, None); ax.set_ylim(0, top * 1.25)
    newest = labels[-1]
    rs = sorted([r for r in rows if r["label"] == newest and not r.get("error")], key=lambda r: r["worldBytes"])
    classes = ["leaf", "postal", "checkpoint", "states", "agent"]
    names = {"leaf": "leaf transcripts (the parse, stage 4)", "postal": "postal log (whole readers)", "states": "states logs",
             "agent": "agent files", "checkpoint": "checkpoint documents (the fold states, chiefly the postal fold's)"}
    for j, c in enumerate(classes):
        xs = [r["worldBytes"] / 1e6 for r in rs]
        ys = [r["second"]["byClass"][c] / 1e6 for r in rs]
        ax2.line(xs, ys, label=names[c], color=cols[(j + 2) % len(cols)], marker="o")
    ax2.clean(xlabel="Same worlds (MB)", ylabel="Bytes read at the restart on %s,\nper file class (MB)" % newest)
    ax2.set_xlim(0, None); ax2.set_ylim(0, None)
    f.subplots_adjust(wspace=0.45)
    f.text(0.5, -0.16, "One measurement per point. Bytes are counted at the reader, not the page cache, the checkpoint documents' own reads included.\n"
                       "A checkpointed file still costs its document, its guard bytes twice (up to 64 each) and everything appended since the checkpoint;\n"
                       "here nothing was appended between the two boots.",
           ha="center", va="top", fontsize=8, color="#555555", transform=f.transFigure)
    path = os.path.join(out, "boot_read_vs_size.png")
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
            try:
                row = measure(os.path.abspath(tree), a.sessions, a.base_turns * s)
            except Exception as e:
                row = {"error": str(e)[-400:], "worldBytes": 0}
            row.update(label=label, size=s, tree=os.path.abspath(tree))
            rows.append(row); sys.stdout.write(json.dumps(row) + "\n"); sys.stdout.flush()
    with open(os.path.join(a.out, "bench.json"), "w") as f:
        json.dump({"rows": rows}, f, indent=1)
    if not a.no_figure:
        sys.stdout.write("figure " + draw(rows, a.out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
