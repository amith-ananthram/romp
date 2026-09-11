#!/usr/bin/env python3
"""The kernel's restart phases and its size over each life, from the ledgers romp already writes.

    scripts/perf/restart-phases.py [--state DIR] [--last N] [--json]

Reads restart-cuts.jsonl (one cut row per exit, one boot row per start) and kernel-samples.jsonl (the kernel's size at
5, 30 and 60 minutes of uptime and every hour after) under the state directory, pairs each cut with the boot that
followed it, and prints per restart: the exit's checkpoint writes (ckptS) and the sessions' close (drainS), the gap
from the cut to the next kernel serving (outageS), the new kernel's census (censusS) and last attach (attachS)
relative to its first serve, the turns the cut interrupted, and the old kernel's resident size at its exit; then,
per kernel life, the sampled sizes. Fields written by older kernels are absent and print as a dash. Read-only."""
import argparse
import datetime
import json
import os
import sys


def _rows(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    out.append(r)
    except OSError:
        pass
    return out


def cycles(cut_rows, last=None):
    """Pair each cut row with the boot row that followed it (by time); the newest `last` pairs."""
    cuts = [r for r in cut_rows if "cutTurns" in r]
    boots = [r for r in cut_rows if r.get("bootSettled")]
    out = []
    for c in cuts:
        b = next((x for x in boots if x.get("prevCutT") == c.get("t")), None) or next((x for x in boots if x.get("t", 0) >= c.get("t", 0)), None)
        out.append({"cutT": c.get("t"), "pid": c.get("pid"), "ckptS": c.get("ckptS"), "drainS": c.get("drainS"),
                    "cutTurns": len(c.get("cutTurns") or []), "stopped": c.get("stopped"), "exitRssMb": round((c.get("rssKb") or 0) / 1024),
                    "outageS": b.get("outageS") if b else None, "censusS": b.get("censusS") if b else None,
                    "attachS": b.get("attachS") if b else None, "attachTimedOut": bool(b.get("attachTimedOut")) if b else None,
                    "bootPid": b.get("pid") if b else None})
    return out[-last:] if last else out


def samples_by_life(sample_rows):
    by = {}
    for r in sample_rows:
        by.setdefault(r.get("pid"), []).append(r)
    return by


def _fmt(v, unit=""):
    return "-" if v is None else ("%s%s" % (v, unit))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default=os.environ.get("ROMP_STATE_DIR") or os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "romp"))
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cyc = cycles(_rows(os.path.join(a.state, "restart-cuts.jsonl")), a.last)
    smp = samples_by_life(_rows(os.path.join(a.state, "kernel-samples.jsonl")))
    if a.json:
        print(json.dumps({"cycles": cyc, "samples": smp}, indent=1))
        return 0
    print("%-19s %7s %7s %8s %8s %8s %5s %8s" % ("cut (local time)", "ckptS", "drainS", "outageS", "censusS", "attachS", "cut", "exitRSS"))
    for c in cyc:
        t = datetime.datetime.fromtimestamp(c["cutT"]).strftime("%m-%d %H:%M:%S") if c.get("cutT") else "-"
        print("%-19s %7s %7s %8s %8s %8s %5s %7sM" % (t, _fmt(c["ckptS"]), _fmt(c["drainS"]), _fmt(c["outageS"]), _fmt(c["censusS"]),
                                                    _fmt(c["attachS"]) + ("!" if c.get("attachTimedOut") else ""), c["cutTurns"], c["exitRssMb"]))
    for pid, rows in list(smp.items())[-a.last:]:
        print("life pid %s: " % pid + ", ".join("%.0fmin %dM" % (r.get("markS", 0) / 60, (r.get("rssKb") or 0) // 1024) +
                                                  (" (cache %dM)" % (r["recordCacheBytes"] // 1048576) if "recordCacheBytes" in r else "") for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
