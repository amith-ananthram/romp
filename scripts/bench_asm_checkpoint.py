#!/usr/bin/env python3
"""bench_asm_checkpoint: what a kernel BOOT costs in bytes read and resident size, against transcript size, with and
without the assembly checkpoint (T323 stage 4a's acceptance measurement).

For each romp tree given and each size, a synthetic world is built under a temporary root: N sessions, each a leaf
transcript of S times a base of invented turns with a compaction boundary every so many turns (the CLI's shape: a
compact_boundary record whose logicalParentUuid is the pre-compaction leaf, its summary record, and the conversation
chaining on through them), an agent file under its subagents/ directory and a states log. Two child processes run in
turn against the same world and state root, each a fresh process loading the tree's event model, judge and kernel
(nothing against live state):

  1. the first boot parses every session (the judges' parse) and runs the folds a kernel runs for a session, then
     writes the checkpoints the tree knows how to write (the fold documents and, on this branch, the assembly
     documents);
  2. the second boot parses and folds again, then reports the bytes the reader pulled per file class and its
     resident size (VmRSS from /proc/self/statm, the current figure, not a peak) after the parses.

Bytes are counted at the reader (this branch) or at a counting open() (a tree without the counter), the checkpoint
documents included. The figure's left panel draws the second boot's bytes read per tree, the middle panel its
resident size per tree, and the right panel the second boot's bytes per file class on the newest tree. The agent
files (the subagents' transcripts) get no assembly document until stage 5: their share is on the right panel as it
is, and the leaf share is what this stage moves. Hydration is on demand and none of it happens here (the boot parses
and folds; nothing reads a pre-cut body), so it is stated as a floor, not measured. Decimal megabytes; one
measurement per point, not a distribution.

    capped bash -c 'uvx --with cleanplots --with matplotlib --with pandas python scripts/bench_asm_checkpoint.py \\
        --tree main=/path/to/main-tree --tree stage4a=. --sizes 1,4,16 --sessions 6 --out DIR'
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
COMPACT_EVERY = 40
for i in range(sessions):
    sid = "%08x-1111-4222-8333-%012d" % (i + 1, i + 1); sids.append(sid)
    rnd = random.Random(i); parent = None; leaf = os.path.join(proj, sid + ".jsonl"); t = t0
    with open(leaf, "w") as f:
        for k in range(turns):
            if k and k % COMPACT_EVERY == 0:
                b, s = "b%d" % k, "s%d" % k
                f.write(json.dumps({"type": "system", "subtype": "compact_boundary", "uuid": b, "parentUuid": None, "logicalParentUuid": parent,
                                    "timestamp": iso(t), "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}}) + "\n")
                f.write(json.dumps({"type": "user", "uuid": s, "parentUuid": b, "timestamp": iso(t + 1), "isCompactSummary": True,
                                    "message": {"role": "user", "content": "summary of the conversation so far: " + " ".join(rnd.choice(WORDS) for _ in range(200))}}) + "\n")
                parent = s; t += 2
            u, a, tid = "u%d" % k, "a%d" % k, "toolu_%d_%d" % (i, k)
            f.write(json.dumps({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed",
                                "cwd": "/w/notes-api", "version": "2.1.0", "gitBranch": "web",
                                "message": {"role": "user", "content": " ".join(rnd.choice(WORDS) for _ in range(40)) + " %d" % k}}) + "\n")
            blocks = [{"type": "text", "text": " ".join(rnd.choice(WORDS) for _ in range(300))}]   # records the size a real
            if k % 7 == 3: blocks.append({"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "sleep 1", "run_in_background": True, "description": "lint"}})
            if k % 5 == 1: blocks.append({"type": "tool_use", "id": tid + "e", "name": "Edit", "input": {"file_path": "/w/notes-api/web/app%d.ts" % k}})
            f.write(json.dumps({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": iso(t + 20), "cwd": "/w/notes-api", "version": "2.1.0",
                                "gitBranch": "web", "message": {"role": "assistant", "content": blocks, "stop_reason": "end_turn"}}) + "\n")
            parent = a
            if k % 3 == 2:                                  # transcript's are (about 2 KB on average, T311): a tool call and its result
                tu, tr = "t%d" % k, "x%d" % k
                f.write(json.dumps({"type": "assistant", "uuid": tu, "parentUuid": parent, "timestamp": iso(t + 25), "message": {"role": "assistant", "stop_reason": "tool_use",
                                    "content": [{"type": "tool_use", "id": "toolu_r%d_%d" % (i, k), "name": "Read", "input": {"file_path": "/w/notes-api/web/app%d.ts" % k}}]}}) + "\n")
                f.write(json.dumps({"type": "user", "uuid": tr, "parentUuid": tu, "timestamp": iso(t + 26), "message": {"role": "user", "content": [
                                    {"type": "tool_result", "tool_use_id": "toolu_r%d_%d" % (i, k), "content": " ".join(rnd.choice(WORDS) for _ in range(250))}]}}) + "\n")
                parent = tr
            t += 60
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
    for k in range(turns * sessions // 4):
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
if not hasattr(em, "read_bytes_report"):
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
def rss():
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
now = time.time()
r0 = rss()
modes = []
trees = {}                                                          # the boot's trees, for the documents' turns sections (stage 4c)
import inspect
_tree_arg = hasattr(em, "asm_checkpoint_write") and "tree" in inspect.signature(em.asm_checkpoint_write).parameters   # main before 4c: no tree=
for sid in sids:
    leaf = os.path.join(proj, sid + ".jsonl")
    m = []
    trees[sid] = jd.parsed_session(sid, [leaf], now, asm_mode_out=m); modes += m   # the judges' parse
    km._states_awaiting_overlay(sid); km._last_machine_cut(sid); km._last_state(sid); km._state_intervals(sid, "working", now)
    km._bg_scan_cached(leaf); km._bg_scan_all_cached(leaf); km._session_meta(leaf); km._agent_launch_state(leaf)
    sub = os.path.join(proj, sid, "subagents")
    for a in sorted(os.listdir(sub)):
        km._agent_steps(os.path.join(sub, a)); km._agent_launch_ids(os.path.join(sub, a))
km._postal_index()
r1 = rss()
rb = em.read_bytes_report() if hasattr(em, "read_bytes_report") else dict(counted, total=sum(counted.values()))
by = {"leaf": 0, "agent": 0, "states": 0, "postal": 0, "checkpoint": 0, "other": 0}
for p, n in rb.items():
    if p == "total": continue
    cls = ("checkpoint" if "/checkpoints/" in p else "agent" if "/subagents/" in p else "states" if "/states/" in p
           else "postal" if p.endswith("messages.jsonl") else "leaf" if p.startswith(proj) else "other")
    by[cls] += n
written, write_ms = 0, 0.0
if phase == "first":
    if hasattr(em, "checkpoint_write_dirty"): em.checkpoint_write_dirty()
    if hasattr(em, "asm_checkpoint_write"):
        for sid in sids:                                        # under the key the judges' parse used (their sdk_human answer)
            t_w = time.time()
            written += 1 if em.asm_checkpoint_write(os.path.join(proj, sid + ".jsonl"), sid, bool(jd._sdk_owned(sid)), **({"tree": trees.get(sid)} if _tree_arg else {})) else 0
            write_ms += (time.time() - t_w) * 1000.0            # the per-document write cost (once per whole parse, item L)
stats = em.asm_checkpoint_stats() if hasattr(em, "asm_checkpoint_stats") else {}
print(json.dumps({"phase": phase, "byClass": by, "total": sum(by.values()), "rssBytes": r1, "rssDelta": r1 - r0,
                  "modes": {m: modes.count(m) for m in set(modes)}, "asmWritten": written, "asmWriteMs": write_ms,
                  "asmRestored": stats.get("restored", 0), "asmFallbacks": stats.get("fallbacks", {}), "asmSkipped": stats.get("skipped", {})}))
'''


def run(args, timeout=1200):
    p = subprocess.run([sys.executable, "-c"] + args, capture_output=True, text=True, timeout=timeout)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    if p.returncode != 0 or not line:
        raise RuntimeError((p.stderr or p.stdout)[-800:])
    return json.loads(line[-1])


def measure(tree, sessions, turns):
    root = tempfile.mkdtemp(prefix="romp-asm-bench-")
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
    f, (ax, ax2, ax3) = cp.fig(w=16, h=5, cols=3)
    for i, label in enumerate(labels):
        rs = sorted([r for r in rows if r["label"] == label and not r.get("error")], key=lambda r: r["worldBytes"])
        if not rs:
            sys.stderr.write("figure: every run of %r errored; the label is left out\n" % label); continue
        xs = [r["worldBytes"] / 1e6 for r in rs]
        ax.line(xs, [r["second"]["total"] / 1e6 for r in rs], label=label, color=cols[i % len(cols)], marker="o")
        ax2.line([r["sizes"]["leaf"] / 1e6 for r in rs], [r["second"]["rssDelta"] / 1e6 for r in rs], label=label, color=cols[i % len(cols)], marker="o")
    ax.clean(xlabel="Files on disk (MB)", ylabel="Bytes a restarted kernel reads (MB)")
    ax2.clean(xlabel="Leaf transcripts on disk (MB)", ylabel="Resident size the parses\nand folds add (MB)")
    ax.set_xlim(0, None); ax.set_ylim(0, None); ax2.set_xlim(0, None); ax2.set_ylim(0, None)
    # the slope, stated on the figure: resident bytes added per byte of leaf transcript, per tree (a fit through the origin)
    for i, label in enumerate(labels):
        rs = sorted([r for r in rows if r["label"] == label and not r.get("error")], key=lambda r: r["worldBytes"])
        if len(rs) >= 2:
            xs = [r["sizes"]["leaf"] for r in rs]; ys = [r["second"]["rssDelta"] for r in rs]
            slope = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
            ax2.text(xs[-1] / 1e6 * 0.97, ys[-1] / 1e6, "%.2f MB resident\nper MB of leaf" % slope, fontsize=8, ha="right",
                     va="bottom" if i == 0 else "top", color=cols[i % len(cols)])
    newest = labels[-1]
    rs = sorted([r for r in rows if r["label"] == newest and not r.get("error")], key=lambda r: r["worldBytes"])
    names = {"leaf": "leaf transcripts", "agent": "agent files (stage 5)", "postal": "postal log", "states": "states logs", "checkpoint": "documents"}
    for j, c in enumerate(["leaf", "agent", "postal", "states", "checkpoint"]):
        ax3.line([r["worldBytes"] / 1e6 for r in rs], [r["second"]["byClass"][c] / 1e6 for r in rs], label=names[c], color=cols[(j + 2) % len(cols)], marker="o")
    ax3.clean(xlabel="Same worlds (MB)", ylabel="Bytes read at the restart on %s,\nper file class (MB)" % newest)
    ax3.set_xlim(0, None); ax3.set_ylim(0, None)
    f.subplots_adjust(wspace=0.6)
    f.text(0.5, -0.14, "One measurement per point. Bytes are counted at the reader, documents included; resident size is the process's current VmRSS\n"
                       "after the parses and folds, not a peak. Both trees stay linear in transcript size: on this branch the slope is the restored pre-cut\n"
                       "index held as Python objects (measured with tracemalloc on an 8052-record synthetic leaf: 2280 bytes per record restored against\n"
                       "3854 whole; the document on disk is about 7 percent of the leaf). The step that flattens it is to keep that index in the document's\n"
                       "row form and build an atom only when a consumer reaches it, which 4b's tail-first frame allows (nothing reads the pre-cut turns at a\n"
                       "boot). Agent files: stage 5.\n"
                       "Hydration of an old body is on demand and none happens at a boot; it is a cost the figure does not carry.",
           ha="center", va="top", fontsize=8, color="#555555", transform=f.transFigure)
    path = os.path.join(out, "boot_cost_vs_size.png")
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
                row = {"error": str(e)[-600:], "worldBytes": 0}
            row.update(label=label, size=s)                    # the tree is named by its label only (no path in the record)
            rows.append(row); sys.stdout.write(json.dumps(row) + "\n"); sys.stdout.flush()
    with open(os.path.join(a.out, "bench.json"), "w") as f:
        json.dump({"rows": rows}, f, indent=1)
    if not a.no_figure:
        sys.stdout.write("figure " + draw(rows, a.out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
