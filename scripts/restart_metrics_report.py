#!/usr/bin/env python3
"""restart_metrics_report — the before-versus-after figures for the restart monitors (T304, stage 0 of the
restart-surviving sessions program, part of #1317).

Reads one or more documents `romp restart-metrics --json` wrote (a baseline snapshot and a later one, or
any number labelled in order) and draws, with cleanplots, one figure per question into --out (default
~/.local/state/romp-research/restart-metrics/):

  cut_turns.png          turns cut per window, and per restart
  restart_timing.png     outage to first serve, and reconcile settle, per window (p50 dot, p90 whisker end)
  quiet_window.png       the parked deploy's wait from parked to restart per window, backstop firings noted
  boot_events.png        orphans reaped, scopes stopped, duplicate CLIs, crash heals, drain left closing
  redo_cost.png          continuation notices, redo turns and the dollars they cost, per window
  turn_latency.png       feed-to-result latency distribution per window (box per window; the state-log
                         series where turns.jsonl has none), and feed-to-first-output where recorded
  session_resources.png  resident memory per session scope and the kernel's own, per snapshot
  kernel_memory.png      the kernel process's resident memory at each of its exits and boots, over the days
                         of each document (the restart rows sample it; the same axis for every document)

Every axis label says what is better where that is not obvious. Bars start at zero; nothing is on a log
scale. The numbers behind each figure also go to figures.json beside them, and summary.txt carries the
reader's one-screen text per document, so a reader who wants the values has them without a table in prose.

Run (cleanplots and matplotlib are not romp dependencies; uv fetches them):
    uvx --with cleanplots --with matplotlib --with pandas python scripts/restart_metrics_report.py \\
        --doc baseline=path/to/baseline.json --doc after=path/to/after.json [--out DIR]
Data shaping (frames) is pure and importable without matplotlib; the drawing needs cleanplots and says so
when it is missing rather than falling back to another look.
"""
import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_OUT = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp-research" / "restart-metrics"
EVENT_COLUMNS = (("orphansReaped", "Orphans reaped at boot"), ("scopesStopped", "Leftover scopes stopped"),
                 ("duplicateClis", "Two CLIs on one conversation"), ("crashHeals", "Crash heals"),
                 ("crashLoops", "Crash loops"), ("drainLeftClosing", "Drain left closing"),
                 ("leaseProblems", "Lease problems"))


# ── data shaping (pure) ─────────────────────────────────────────────────────────────────────────────────
def load_docs(specs) -> list[tuple[str, dict]]:
    """[(label, doc)] from `label=path` specs (a bare path is labelled by its file stem)."""
    out = []
    for s in specs:
        label, _, path = s.rpartition("=") if "=" in s else ("", "", s)
        p = Path(path)
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict) or "buckets" not in doc:
            raise SystemExit("%s is not a restart-metrics document (no buckets)" % p)
        out.append((label or p.stem, doc))
    return out


def _st(b, *keys):
    d = b
    for k in keys:
        d = (d or {}).get(k)
    return d if isinstance(d, dict) else {}


def frames(docs) -> dict:
    """Everything the figures draw, as plain lists keyed by figure: one row per (label, window)."""
    rows = []
    for label, doc in docs:
        for b in doc.get("buckets") or []:
            lat, sl, out, set_, qw = (_st(b, "latency", "feedToResultS"), _st(b, "stateLogLatencyS"),
                                      _st(b, "outageS"), _st(b, "settleS"), _st(b, "quietWaitS"))
            first = _st(b, "latency", "feedToFirstOutS")
            rows.append({
                "label": label, "window": b["key"], "restarts": b.get("restarts", 0), "cutTurns": b.get("cutTurns", 0),
                "cutTurnsPerRestart": b.get("cutTurnsPerRestart"), "cleanRestarts": b.get("cleanRestarts", 0),
                "outageP50": out.get("p50"), "outageP90": out.get("p90"), "outageN": out.get("n", 0),
                "settleP50": set_.get("p50"), "settleP90": set_.get("p90"),
                "quietWindows": b.get("quietWindows", 0), "quietP50": qw.get("p50"), "quietP90": qw.get("p90"),
                "quietMax": qw.get("max"), "backstopFires": b.get("backstopFires", 0),
                "events": {k: b.get(k, 0) for k, _ in EVENT_COLUMNS},
                "resumedTurns": b.get("resumedTurns", 0), "redoTurns": (b.get("redo") or {}).get("turns", 0),
                "redoUsd": (b.get("redo") or {}).get("usd", 0.0), "spendUsd": b.get("spendUsd"),
                "latencyN": lat.get("n", 0), "latencyP50": lat.get("p50"), "latencyP90": lat.get("p90"),
                "firstOutN": first.get("n", 0), "firstOutP50": first.get("p50"), "firstOutP90": first.get("p90"),
                "stateLogN": sl.get("n", 0), "stateLogP50": sl.get("p50"), "stateLogP90": sl.get("p90"),
                "latencySamples": (b.get("samples") or {}).get("feedToResultS") or [],
                "stateLogSamples": (b.get("samples") or {}).get("stateLogS") or [],
                "firstOutSamples": (b.get("samples") or {}).get("feedToFirstOutS") or [],
            })
    live = []
    for label, doc in docs:
        lv = doc.get("live") or {}
        if lv.get("skipped"):
            continue
        sess = [{"name": s.get("name") or s.get("sid8") or "?", "memMb": s["memBytes"] / 1048576.0,
                 "cpuS": s.get("cpuS")} for s in (lv.get("sessions") or []) if isinstance(s.get("memBytes"), (int, float))]
        k = lv.get("kernel") or {}
        pf = k.get("perf") or {}
        live.append({"label": label, "sessions": sorted(sess, key=lambda s: -s["memMb"]),
                     "kernelRssMb": (pf["rssKb"] / 1024.0) if isinstance(pf.get("rssKb"), (int, float)) else None,
                     "kernelCpuS": pf.get("cpuS"), "kernelIdleShare": pf.get("idleShare"), "kernelPid": k.get("pid")})
    series = []
    for label, doc in docs:
        ks = [k for k in (doc.get("kernelSeries") or []) if isinstance(k.get("rssMb"), (int, float))]
        if not ks:
            continue
        t0 = min(k["t"] for k in ks)
        series.append({"label": label, "t0": t0,
                       "points": [{"days": round((k["t"] - t0) / 86400.0, 4), "rssMb": k["rssMb"], "kind": k["kind"],
                                   "cpuS": k.get("cpuS")} for k in ks]})
    return {"windows": rows, "live": live, "labels": [l for l, _ in docs], "kernel": series}


def _ylabels(rows):
    return ["%s · %s" % (r["label"], r["window"]) for r in rows]


# ── the figures (cleanplots) ────────────────────────────────────────────────────────────────────────────
def _cp():
    try:
        import cleanplots as cp
        import matplotlib
        matplotlib.use("Agg")
    except ImportError as e:
        raise SystemExit("cleanplots and matplotlib are required for the figures (%s): run under "
                         "`uvx --with cleanplots --with matplotlib --with pandas python …`" % e)
    return cp


def _palette(cp, labels):
    cols = list(cp.colors)
    return {l: cols[i % len(cols)] for i, l in enumerate(labels)}


def _save(f, out: Path, name: str):
    p = out / name
    f.savefig(p, dpi=150, bbox_inches="tight")
    try:
        import matplotlib.pyplot as plt
        plt.close(f)
    except Exception:
        pass
    return str(p)


def fig_cut_turns(cp, fr, out):
    rows = fr["windows"]
    if not rows:
        return None
    pal = _palette(cp, fr["labels"])
    f, axs = cp.fig(rows=1, cols=2, w=14, h=max(3.5, 0.5 * len(rows) + 1.5))
    ax1, ax2 = axs if hasattr(axs, "__len__") else (axs, None)
    ys = list(range(len(rows)))
    vals = [r["cutTurns"] for r in rows]
    ax1.barh(ys, vals, color=[pal[r["label"]] for r in rows], legend=False)
    for y, r in zip(ys, rows):
        ax1.annotate("%d of %d restarts cut" % (r["restarts"] - r["cleanRestarts"], r["restarts"]),
                     (r["cutTurns"], y), xytext=(4, 0), textcoords="offset points", va="center", fontsize=11)
    ax1.set_yticks(ys)
    ax1.set_yticklabels(_ylabels(rows))
    ax1.clean(xlabel="Turns cut by restarts, per window — fewer better", ylabel="")
    ax1.set_xlim(0, max(vals + [1]) * 1.6)         # room for the annotation past the longest bar
    f.subplots_adjust(wspace=0.3)
    per = [r["cutTurnsPerRestart"] or 0 for r in rows]
    ax2.scatter(per, ys, color=[pal[r["label"]] for r in rows], legend=False, s=60)
    ax2.set_yticks(ys)
    ax2.set_yticklabels([""] * len(ys))
    ax2.clean(xlabel="Turns cut per restart — fewer better", ylabel="")
    ax2.set_xlim(0, max(per + [1]) * 1.3)           # from zero, the natural origin; no degenerate tick pair
    ax2.set_xticks([0, round(max(per + [1]), 2)])
    return _save(f, out, "cut_turns.png")


def _dots_with_p90(cp, ax, rows, p50k, p90k, pal, xlabel):
    ys = list(range(len(rows)))
    for y, r in zip(ys, rows):
        p50, p90 = r.get(p50k), r.get(p90k)
        if p50 is None:
            ax.annotate("no rows", (0, y), xytext=(4, 0), textcoords="offset points", va="center", fontsize=11, color="gray")
            continue
        c = pal[r["label"]]
        if p90 is not None and p90 > p50:
            ax.plot([p50, p90], [y, y], color=c, linewidth=2, alpha=0.6)
        ax.scatter([p50], [y], color=c, s=60, legend=False)
    ax.set_yticks(ys)
    ax.set_yticklabels(_ylabels(rows))
    ax.clean(xlabel=xlabel, ylabel="")
    ax.set_xlim(left=0)


def fig_restart_timing(cp, fr, out):
    rows = fr["windows"]
    if not rows:
        return None
    pal = _palette(cp, fr["labels"])
    f, axs = cp.fig(rows=1, cols=2, w=16, h=max(3.5, 0.5 * len(rows) + 1.5))
    f.subplots_adjust(wspace=0.3)
    _dots_with_p90(cp, axs[0], rows, "outageP50", "outageP90", pal, "Exit to first serve (s) — shorter better\ndot p50, line to p90")
    _dots_with_p90(cp, axs[1], rows, "settleP50", "settleP90", pal, "Reconcile settle (s) — shorter better\ndot p50, line to p90")
    axs[1].set_yticklabels([""] * len(rows))
    return _save(f, out, "restart_timing.png")


def fig_quiet_window(cp, fr, out):
    rows = fr["windows"]
    if not rows:
        return None
    pal = _palette(cp, fr["labels"])
    f, ax = cp.fig(w=10, h=max(3.5, 0.5 * len(rows) + 1.5))
    _dots_with_p90(cp, ax, rows, "quietP50", "quietP90", pal, "Quiet-window wait, parked to restart (s): dot p50, line to p90 — shorter better")
    for y, r in enumerate(rows):
        if r["quietWindows"]:
            ax.annotate("%d windows, backstop fired %d" % (r["quietWindows"], r["backstopFires"]),
                        (r.get("quietMax") or r.get("quietP90") or r.get("quietP50") or 0, y), xytext=(6, 0),
                        textcoords="offset points", va="center", fontsize=11)
    return _save(f, out, "quiet_window.png")


def fig_boot_events(cp, fr, out):
    rows = fr["windows"]
    if not rows:
        return None
    import pandas as pd
    df = pd.DataFrame({title: [r["events"][k] for r in rows] for k, title in EVENT_COLUMNS}, index=_ylabels(rows))
    f, ax = cp.fig(w=12, h=max(4, 0.9 * len(rows) + 1.5))
    ax.barh(df)
    ax.clean(xlabel="Sessions gone wrong, per window — fewer better", ylabel="", color_labels="auto")
    ax.set_xlim(0, max(float(df.values.max()) if len(df.values) else 1.0, 1.0) * 1.8)   # the labels sit in the clear
    return _save(f, out, "boot_events.png")


def fig_redo_cost(cp, fr, out):
    rows = fr["windows"]
    if not rows:
        return None
    import pandas as pd
    pal = _palette(cp, fr["labels"])
    f, axs = cp.fig(rows=1, cols=2, w=14, h=max(3.5, 0.5 * len(rows) + 1.5))
    df = pd.DataFrame({"Continuation notices": [r["resumedTurns"] for r in rows],
                       "Redo turns recorded": [r["redoTurns"] for r in rows]}, index=_ylabels(rows))
    axs[0].barh(df)
    axs[0].clean(xlabel="Turns resumed after a cut, per window — fewer better", ylabel="", color_labels="auto")
    axs[0].set_xlim(0, max(float(df.values.max()) if len(df.values) else 1.0, 1.0) * 1.8)
    f.subplots_adjust(wspace=0.3)
    ys = list(range(len(rows)))
    axs[1].barh(ys, [r["redoUsd"] for r in rows], color=[pal[r["label"]] for r in rows], legend=False)
    for y, r in zip(ys, rows):
        if isinstance(r.get("spendUsd"), (int, float)) and r["spendUsd"]:
            axs[1].annotate("%.1f%% of $%.0f" % (100.0 * r["redoUsd"] / r["spendUsd"], r["spendUsd"]), (r["redoUsd"], y),
                            xytext=(4, 0), textcoords="offset points", va="center", fontsize=11)
    axs[1].set_yticks(ys)
    axs[1].set_yticklabels([""] * len(ys))
    axs[1].clean(xlabel="Dollars spent redoing cut work, per window — fewer better", ylabel="")
    axs[1].set_xlim(left=0)
    return _save(f, out, "redo_cost.png")


def fig_turn_latency(cp, fr, out):
    rows = fr["windows"]
    series = [(r, r["latencySamples"] or r["stateLogSamples"], bool(r["latencySamples"])) for r in rows]
    series = [(r, s, ev) for r, s, ev in series if s]
    if not series:
        return None
    pal = _palette(cp, fr["labels"])
    have_first = [(r, r["firstOutSamples"]) for r in rows if r["firstOutSamples"]]
    f, axs = cp.fig(rows=1, cols=2 if have_first else 1, w=16 if have_first else 8, h=max(4, 0.6 * len(series) + 2))
    if have_first:
        f.subplots_adjust(wspace=0.35)
    ax = axs[0] if have_first else axs
    positions = list(range(len(series)))
    for pos, (r, s, ev) in zip(positions, series):
        ax.box([s], positions=[pos], showfliers=False, color=pal[r["label"]], legend=False, widths=0.6, vert=False)
        ax.annotate("n=%d%s" % (len(s), "" if ev else ", state log (1 s)"), (1.0, pos), xycoords=("axes fraction", "data"),
                    xytext=(6, 0), textcoords="offset points", va="center", fontsize=10)   # past the axis, clear of the whiskers
    ax.set_yticks(positions)
    ax.set_yticklabels(["%s · %s" % (r["label"], r["window"]) for r, _, _ in series])
    ax.clean(xlabel="Feed to result (s): box quartiles, whiskers 1.5 IQR — shorter better", ylabel="")
    ax.set_xlim(left=0)
    if have_first:
        ax2 = axs[1]
        pos2 = list(range(len(have_first)))
        for pos, (r, s) in zip(pos2, have_first):
            ax2.box([s], positions=[pos], showfliers=False, color=pal[r["label"]], legend=False, widths=0.6, vert=False)
        ax2.set_yticks(pos2)
        same_rows = [(r["label"], r["window"]) for r, _ in have_first] == [(r["label"], r["window"]) for r, _, _ in series]
        ax2.set_yticklabels([""] * len(pos2) if same_rows else ["%s · %s" % (r["label"], r["window"]) for r, _ in have_first])
        ax2.clean(xlabel="Feed to first output (s) — shorter better", ylabel="")
        ax2.set_xlim(left=0)
    return _save(f, out, "turn_latency.png")


def fig_session_resources(cp, fr, out):
    live = fr["live"]
    if not live:
        return None
    n = len(live)
    rows_max = max([len(l["sessions"]) for l in live] + [1])
    f, axs = cp.fig(rows=1, cols=n, w=6 * n + 2, h=max(4, 0.4 * rows_max + 2))
    axs = list(axs) if hasattr(axs, "__len__") else [axs]
    pal = _palette(cp, fr["labels"])
    for ax, l in zip(axs, live):
        names = [s["name"] for s in l["sessions"]] + (["kernel (pid %s)" % l["kernelPid"]] if l["kernelRssMb"] else [])
        vals = [s["memMb"] for s in l["sessions"]] + ([l["kernelRssMb"]] if l["kernelRssMb"] else [])
        ys = list(range(len(names)))[::-1]
        ax.barh(ys, vals, color=pal[l["label"]], legend=False)
        ax.set_yticks(ys)
        ax.set_yticklabels(names)
        ax.clean(xlabel="Resident memory (MB) — %s%s" % (l["label"], (", kernel CPU %.0f s, pusher idle %.0f%%" % (l["kernelCpuS"], 100 * l["kernelIdleShare"])) if isinstance(l.get("kernelIdleShare"), float) and l.get("kernelCpuS") is not None else ""), ylabel="")
        ax.set_xlim(left=0)
    return _save(f, out, "session_resources.png")


def fig_kernel_memory(cp, fr, out):
    series = fr["kernel"]
    if not series:
        return None
    pal = _palette(cp, fr["labels"])
    f, ax = cp.fig(w=11, h=5)
    for s in series:
        exits = [p for p in s["points"] if p["kind"] == "exit"]
        boots = [p for p in s["points"] if p["kind"] == "boot"]
        if exits:
            ax.line([p["days"] for p in exits], [p["rssMb"] for p in exits], label="%s, at exit" % s["label"],
                    color=pal[s["label"]], marker="o", linewidth=1.2)
        if boots:
            ax.scatter([p["days"] for p in boots], [p["rssMb"] for p in boots], label="%s, at boot" % s["label"],
                       color=pal[s["label"]], marker="x", s=70, alpha=0.9)
    ax.clean(xlabel="Days since the document's first restart", ylabel="Kernel resident memory (MB) — lower better",
             color_labels="auto")
    ax.set_ylim(bottom=0)
    ax.set_xlim(left=0)
    return _save(f, out, "kernel_memory.png")


FIGURES = (fig_cut_turns, fig_restart_timing, fig_quiet_window, fig_boot_events, fig_redo_cost, fig_turn_latency,
           fig_session_resources, fig_kernel_memory)


def render(docs, out: Path) -> dict:
    """Draw every figure; returns {figure name: path or None (no data)} and writes figures.json + summary.txt."""
    cp = _cp()
    out.mkdir(parents=True, exist_ok=True)
    fr = frames(docs)
    made = {}
    for fn in FIGURES:
        made[fn.__name__.replace("fig_", "")] = fn(cp, fr, out)
    (out / "figures.json").write_text(json.dumps({"figures": made, "windows": fr["windows"], "live": fr["live"],
                                                  "kernel": fr["kernel"]},
                                                 indent=1, sort_keys=True, default=str))
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
        import restart_metrics as rm
        (out / "summary.txt").write_text("\n\n".join("== %s ==\n%s" % (l, rm.summary(d)) for l, d in docs) + "\n")
    except Exception as e:
        (out / "summary.txt").write_text("summary unavailable: %s\n" % e)
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--doc", action="append", required=True, help="label=path of a `romp restart-metrics --json` document; repeat in order (baseline first)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    docs = load_docs(a.doc)
    made = render(docs, Path(a.out))
    for k, v in made.items():
        sys.stdout.write("%-20s %s\n" % (k, v or "(no data)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
