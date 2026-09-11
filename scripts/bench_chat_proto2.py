#!/usr/bin/env python3
"""T323 stage 4b bench: what a chat's FIRST OPEN costs after a restart, per tree, against transcript size.

Synthetic worlds (bench_asm_checkpoint's builder: N sessions whose transcripts compact every 40 turns, an agent
file and a states log each, one postal log). Per tree and size, two boots in fresh processes over the same world:
the first parses every session whole and writes the assembly documents; the second restores from them, then OPENS
every session's chat (build_session, what the pusher runs for a tab's first frame) and reports, for the boot and
for the opens: resident size added (VmRSS after, minus before), bytes read at the reader, the wall time of the
first frame per session, and the assembly counters (hydrated atoms and bytes by caller). On main the first open
hydrates and renders every pre-cut turn; on this branch it renders from the render floor (the assembly cut), so
the open reads nothing before the cut. One measurement per point; a cleanplots figure with the floors named.

    capped uvx --with cleanplots --with matplotlib --with pandas python scripts/bench_chat_proto2.py \\
        --tree main=/path/to/main-tree --tree stage4b=. --sizes 1,4,16 --sessions 6 --out DIR
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import bench_asm_checkpoint as B   # noqa: E402  the world builder, the boot program's shape, the runner

OPEN = r'''
import json, os, sys, time
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
def rss():
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
def read_total():
    rb = em.read_bytes_report() if hasattr(em, "read_bytes_report") else {}
    return sum(n for p, n in rb.items() if p != "total" and p.startswith(proj))
now = time.time()
rows = [{"sid": sid, "name": "worker", "path": os.path.join(proj, sid + ".jsonl"), "mtime": now, "anchor": sid} for sid in sids]
km._sessions = lambda now, **kw: list(rows)
km._tmux_sessions = lambda: {}
r0 = rss(); modes = []
trees = {}                                                          # the boot's trees, for the documents' turns sections (stage 4c)
import inspect
_tree_arg = hasattr(em, "asm_checkpoint_write") and "tree" in inspect.signature(em.asm_checkpoint_write).parameters   # main before 4c: no tree=
for sid in sids:                                                    # the boot: the judges' parse and the folds (as bench_asm_checkpoint)
    leaf = os.path.join(proj, sid + ".jsonl"); m = []
    trees[sid] = jd.parsed_session(sid, [leaf], now, asm_mode_out=m); modes += m
    km._bg_scan_cached(leaf); km._bg_scan_all_cached(leaf); km._session_meta(leaf); km._agent_launch_state(leaf)
r_boot = rss(); read_boot = read_total()
if phase == "first" and hasattr(em, "asm_checkpoint_write"):
    if hasattr(em, "checkpoint_write_dirty"): em.checkpoint_write_dirty()
    for sid in sids:
        em.asm_checkpoint_write(os.path.join(proj, sid + ".jsonl"), sid, bool(jd._sdk_owned(sid)), **({"tree": trees.get(sid)} if _tree_arg else {}))
if hasattr(km, "_live_scope") and hasattr(km, "_RENDER_FLOOR"):
    km._live_scope.chat_floor0 = False                              # the pusher's decision: no proto-1 client connected
ms, events, floors = [], 0, []
for sid in sids:                                                    # the first open of every chat: the tab's first frame
    t0 = time.monotonic()
    m = km.build_session(sid, now, {})
    ms.append((time.monotonic() - t0) * 1000.0)
    events += len((m or {}).get("events") or [])
    floors.append((m or {}).get("floor", 0))
r_open = rss(); read_open = read_total()
stats = em.asm_checkpoint_stats() if hasattr(em, "asm_checkpoint_stats") else {}
print(json.dumps({"phase": phase, "modes": {x: modes.count(x) for x in set(modes)},
                  "rssBoot": r_boot - r0, "rssOpen": r_open - r_boot, "readBoot": read_boot, "readOpen": read_open - read_boot,
                  "openMs": ms, "openMsSum": sum(ms), "openMsMax": max(ms) if ms else 0.0, "events": events, "floors": floors,
                  "hydratedAtoms": stats.get("hydratedAtoms", 0), "hydratedBytes": stats.get("hydratedBytes", 0),
                  "hydratedBy": stats.get("hydratedBy", {})}))
'''


def measure(tree, sessions, turns):
    root = tempfile.mkdtemp(prefix="romp-chat-bench-")
    try:
        world = B.run([B.WORLD, root, str(sessions), str(turns)])
        sizes = {"leaf": 0, "agent": 0, "states": 0, "postal": 0}
        for dp, _, fs in os.walk(root):
            for f in fs:
                if not f.endswith(".jsonl"):
                    continue
                p = os.path.join(dp, f); n = os.path.getsize(p)
                sizes["agent" if "/subagents/" in p else "states" if "/states/" in p else "postal" if f == "messages.jsonl" else "leaf"] += n
        first = B.run([OPEN, tree, root, world["proj"], "first", json.dumps(world["sids"])])
        second = B.run([OPEN, tree, root, world["proj"], "second", json.dumps(world["sids"])])
        return {"worldBytes": sum(sizes.values()), "sizes": sizes, "first": first, "second": second}
    finally:
        import shutil; shutil.rmtree(root, ignore_errors=True)


def draw(rows, out):
    import cleanplots as cp
    import matplotlib
    matplotlib.use("Agg")
    labels = sorted({r["label"] for r in rows})
    cols = list(cp.colors)
    f, (ax1, ax2, ax3, ax4) = cp.fig(w=20, h=5, cols=4)
    for i, label in enumerate(labels):
        rs = sorted([r for r in rows if r["label"] == label and not r.get("error")], key=lambda r: r["sizes"]["leaf"])
        if not rs:
            sys.stderr.write("figure: every run of %r errored; the label is left out\n" % label); continue
        xs = [r["sizes"]["leaf"] / 1e6 for r in rs]
        c = cols[i % len(cols)]
        ax1.line(xs, [r["second"]["rssBoot"] / 1e6 for r in rs], label=label, color=c, marker="o")
        ax2.line(xs, [r["second"]["rssOpen"] / 1e6 for r in rs], label=label, color=c, marker="o")
        ax3.line(xs, [r["second"]["openMsMax"] / 1000.0 for r in rs], label=label, color=c, marker="o")
        ax4.line(xs, [r["second"]["readOpen"] / 1e6 for r in rs], label=label, color=c, marker="o")
    ax1.clean(xlabel="Leaf transcripts on disk (MB)", ylabel="Resident size the restarted\nkernel's boot adds (MB)")
    ax2.clean(xlabel="Leaf transcripts on disk (MB)", ylabel="Resident size the first open\nof every chat adds (MB)")
    ax3.clean(xlabel="Leaf transcripts on disk (MB)", ylabel="First frame, the slowest\nsession (s)")
    ax4.clean(xlabel="Leaf transcripts on disk (MB)", ylabel="Bytes the first opens read (MB)")
    for ax in (ax1, ax2, ax3, ax4):
        ax.set_xlim(0, None); ax.set_ylim(0, None)
    f.subplots_adjust(wspace=0.55)
    f.text(0.5, -0.14, "One measurement per point, in fresh processes over the same synthetic worlds (six sessions compacting every forty\n"
                       "turns). The second boot restores from the assembly documents on both trees; the first open of every chat then\n"
                       "builds its frame. Floors: the boot's resident size is the restored index on both trees (the lazy index is stage\n"
                       "4c); on this branch the open renders from the assembly cut, so it reads and hydrates nothing before it and holds\n"
                       "only the tail's rendered events, and the pages a reader scrolls into are rendered on demand (bounded cache). The\n"
                       "judges' first pass over a fresh store is not in these numbers.",
           ha="center", va="top", fontsize=8, color="#555555", transform=f.transFigure)
    path = os.path.join(out, "first_open_vs_size.png")
    f.savefig(path, dpi=130, bbox_inches="tight")
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
                row = {"error": str(e)[-600:], "worldBytes": 0, "sizes": {"leaf": 0}}
            row.update(label=label, size=s)
            rows.append(row); sys.stdout.write(json.dumps(row) + "\n"); sys.stdout.flush()
    with open(os.path.join(a.out, "bench.json"), "w") as f:
        json.dump({"rows": rows}, f, indent=1)
    if not a.no_figure:
        sys.stdout.write("figure " + draw(rows, a.out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
