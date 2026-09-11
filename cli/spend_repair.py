#!/usr/bin/env python3
"""romp spend-repair: recompute a day's spend from the turn ledger's re-attach staircase (T354).

The fault it repairs: a session under a per-session host keeps its CLI process across a kernel restart, and the
CLI's total_cost_usd is cumulative per process; the kernel seeded its watermark at zero for what it took for a fresh
process, so the first result after each restart recorded the process lifetime as one turn. On the turn ledger this
reads as a STAIRCASE per session: rows whose dollars are the cumulative total (monotone across the day's restarts),
between ordinary rows.

The arithmetic (the manager's rule, 2026-09-11): a session's first result after a kernel restart is cumulative; its
true cost is the cumulative less the previous cumulative less the ordinary rows between the two; the day's first
cumulative row, with no previous cumulative, counts as a typical turn (the median of the session's ordinary rows that
day, else the day's median across sessions, else nothing). A candidate whose dollars fall BELOW the previous cumulative
is a fresh process (its total started over), not a staircase row: it stands as recorded and starts a new chain.

Sources: turns.jsonl (with its rotated predecessor), restart-cuts.jsonl and restart-audit.jsonl for the restart
instants, spend.json for the buckets. Pure functions over rows for the tests; the command prints before and after per
hour and per session and changes nothing unless --apply. Tokens are left as recorded (the same staircase exists in the
token watermarks; this pass repairs dollars only, and says so).
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

RESTART_ACTIONS = ("manager-sigterm", "remote-restart", "main-converge", "p2p-update", "self-update")
TYPICAL_MULTIPLE = 3.0     # a first-after-restart row with no previous cumulative counts as cumulative only when it
MIN_STAIRCASE_USD = 20.0   # is this many times the session's typical turn and at least this much: a modest first
#                            turn after a restart is a turn, not the lifetime


def state_dir() -> Path:
    return Path(os.environ.get("ROMP_STATE_DIR")
                or Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp")


def read_jsonl(path: Path) -> list:
    """Every parseable object row of `path`, its rotated predecessor (<name>.1) first when present."""
    out = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if isinstance(o, dict):
                out.append(o)
    return out


def local_day(t) -> str:
    return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d")


def local_hour(t) -> str:
    return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%dT%H")


def restart_instants(cuts: list, audit: list) -> list:
    """The moments a kernel restarted: every restart-cuts row with a cutTurns list (a kernel's exit) and every audit
    row whose action asks for a restart, sorted, deduplicated to the second."""
    out = set()
    for r in cuts:
        if isinstance(r.get("t"), (int, float)) and "cutTurns" in r:
            out.add(int(r["t"]))
    for r in audit:
        if isinstance(r.get("t"), (int, float)) and str(r.get("action") or "") in RESTART_ACTIONS:
            out.add(int(r["t"]))
    return sorted(out)


def _typical(usds: list) -> float:
    return float(statistics.median(usds)) if usds else 0.0


def plan(turns: list, restarts: list, day: str) -> dict:
    """The corrections for `day`: {"rows": [{sid, name, t, hour, recorded, corrected, reason}], "hours": {hour: {before,
    after}}, "days": {day: {before, after}}, "bySid": {sid: {name, before, after}}}, computed from the turn rows alone
    (the buckets' before/after are the sums over the day's rows, so a bucket the rows do not explain is left alone).
    A row is a staircase candidate when a restart instant lies after the session's previous row and at or before it
    (or, for the session's first row of the day, at or before it that day)."""
    rows = [r for r in turns if isinstance(r.get("t"), (int, float)) and isinstance(r.get("usd"), (int, float))
            and local_day(r["t"]) == day]
    rows.sort(key=lambda r: (str(r.get("sid") or ""), float(r["t"])))
    by_sid = {}
    for r in rows:
        by_sid.setdefault(str(r.get("sid") or ""), []).append(r)
    day_start = datetime.strptime(day, "%Y-%m-%d").timestamp()
    corrections, all_ordinary = [], []
    # first pass per session: which rows are staircase steps, which are ordinary
    marked = {}
    for sid, rs in by_sid.items():
        prev_t, prev_cum, ordinary, steps = day_start, None, [], []
        between = []
        for r in rs:
            t, usd = float(r["t"]), float(r["usd"])
            restarted = any(prev_t < x <= t for x in restarts)
            step = False
            if restarted:
                if prev_cum is not None and usd >= prev_cum:
                    step = True                # the surviving process's lifetime again
                elif prev_cum is None:
                    typical = _typical([float(x["usd"]) for x in rs if x is not r]) or 0.0
                    step = usd >= max(MIN_STAIRCASE_USD, TYPICAL_MULTIPLE * typical) if typical else usd >= MIN_STAIRCASE_USD
            if step:
                steps.append((r, prev_cum, list(between)))
                prev_cum = usd
                between = []
            elif restarted:
                # a FRESH process's first result (below the previous cumulative, or a modest first turn): the turn stands
                # as recorded, and its total IS the new process's cumulative, so the chain continues from it
                ordinary.append(r)
                prev_cum = usd
                between = []
            else:
                ordinary.append(r)
                between.append(usd)
            prev_t = t
        marked[sid] = (steps, ordinary)
        all_ordinary.extend(float(x["usd"]) for x in ordinary)
    day_typical = _typical(all_ordinary)
    for sid, (steps, ordinary) in marked.items():
        typical = _typical([float(x["usd"]) for x in ordinary]) or day_typical
        for r, prev_cum, between in steps:
            usd = float(r["usd"])
            if prev_cum is None:
                corrected, reason = typical, "the day's first cumulative row: a typical turn (median %.4f)" % typical
            else:
                corrected = max(0.0, usd - prev_cum - sum(between))
                reason = "cumulative %.4f less the previous cumulative %.4f less %d row(s) between (%.4f)" % (
                    usd, prev_cum, len(between), sum(between))
            corrections.append({"sid": sid, "name": str(r.get("name") or sid[:8]), "t": int(r["t"]), "hour": local_hour(r["t"]),
                                "recorded": round(usd, 6), "corrected": round(corrected, 6), "reason": reason})
    hours, sids = {}, {}
    for r in rows:
        h, sid = local_hour(r["t"]), str(r.get("sid") or "")
        hours.setdefault(h, {"before": 0.0, "after": 0.0})
        sids.setdefault(sid, {"name": str(r.get("name") or sid[:8]), "before": 0.0, "after": 0.0})
        hours[h]["before"] += float(r["usd"]); hours[h]["after"] += float(r["usd"])
        sids[sid]["before"] += float(r["usd"]); sids[sid]["after"] += float(r["usd"])
    for c in corrections:
        d = c["corrected"] - c["recorded"]
        hours[c["hour"]]["after"] += d
        sids[c["sid"]]["after"] += d
    for m in list(hours.values()) + list(sids.values()):
        m["before"], m["after"] = round(m["before"], 6), round(m["after"], 6)
    before = round(sum(h["before"] for h in hours.values()), 6)
    after = round(sum(h["after"] for h in hours.values()), 6)
    return {"day": day, "rows": sorted(corrections, key=lambda c: c["t"]), "hours": dict(sorted(hours.items())),
            "days": {day: {"before": before, "after": after}}, "bySid": sids, "restarts": len([x for x in restarts if local_day(x) == day])}


def apply_to_spend(spend: dict, p: dict) -> dict:
    """spend.json with the plan's deltas folded into the day's hour buckets, its day bucket and their bySid maps
    (dollars only; turns and tokens stay). A bucket the ledger lacks is left alone, said in the plan's notes."""
    out = json.loads(json.dumps(spend or {}))
    hours = out.setdefault("hours", {}) if isinstance(out.get("hours"), dict) else out.__setitem__("hours", {}) or out["hours"]
    days = out.setdefault("days", {}) if isinstance(out.get("days"), dict) else out.__setitem__("days", {}) or out["days"]

    def fold(bucket, sid, delta):
        if not isinstance(bucket, dict):
            return False
        bucket["usd"] = round(max(0.0, float(bucket.get("usd") or 0) + delta), 6)
        k = bucket.get("key")
        if isinstance(k, dict) and isinstance(k.get("usd"), (int, float)):
            k["usd"] = round(max(0.0, float(k["usd"]) + delta), 6)
        by = bucket.get("bySid")
        if isinstance(by, dict) and isinstance(by.get(sid), dict):
            by[sid]["usd"] = round(max(0.0, float(by[sid].get("usd") or 0) + delta), 6)
            sk = by[sid].get("key")
            if isinstance(sk, dict) and isinstance(sk.get("usd"), (int, float)):
                sk["usd"] = round(max(0.0, float(sk["usd"]) + delta), 6)
        return True

    notes = []
    for c in p["rows"]:
        delta = c["corrected"] - c["recorded"]
        if not fold(hours.get(c["hour"]), c["sid"], delta):
            notes.append("no hour bucket %s for %s" % (c["hour"], c["name"]))
        if not fold(days.get(p["day"]), c["sid"], delta):
            notes.append("no day bucket %s" % p["day"])
    p["notes"] = sorted(set(notes))
    return out


def apply_to_turns(path: Path, p: dict) -> int:
    """turns.jsonl (and its predecessor) rewritten with each corrected row's usd, the recorded figure kept as
    usdRecorded; returns the rows changed. Atomic per file."""
    want = {(c["sid"], c["t"], c["recorded"]): c["corrected"] for c in p["rows"]}
    changed = 0
    for f in (path.with_name(path.name + ".1"), path):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        out = []
        for ln in lines:
            try:
                o = json.loads(ln)
            except Exception:
                out.append(ln); continue
            key = (str(o.get("sid") or ""), int(o["t"]) if isinstance(o.get("t"), (int, float)) else None,
                   round(float(o["usd"]), 6) if isinstance(o.get("usd"), (int, float)) else None)
            if key in want:
                o["usdRecorded"] = o["usd"]
                o["usd"] = want[key]
                o["repairedT"] = int(time.time())
                changed += 1
            out.append(json.dumps(o))
        tmp = f.with_name(f.name + ".repair.tmp")
        tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        os.replace(tmp, f)
    return changed


def report(p: dict) -> str:
    lines = ["spend repair for %s: %d restart(s) that day, %d cumulative row(s) found" % (p["day"], p["restarts"], len(p["rows"]))]
    lines.append("")
    lines.append("per session (dollars before -> after):")
    for sid, m in sorted(p["bySid"].items(), key=lambda kv: -kv[1]["before"]):
        if any(c["sid"] == sid for c in p["rows"]):
            lines.append("  %-24s %10.2f -> %10.2f" % (m["name"][:24], m["before"], m["after"]))
    lines.append("")
    lines.append("per hour (dollars before -> after):")
    for h, m in p["hours"].items():
        mark = "" if abs(m["after"] - m["before"]) < 1e-6 else "   *"
        lines.append("  %s %10.2f -> %10.2f%s" % (h, m["before"], m["after"], mark))
    d = p["days"][p["day"]]
    lines.append("")
    lines.append("the day: %.2f -> %.2f" % (d["before"], d["after"]))
    lines.append("")
    lines.append("rows (session, time, recorded -> corrected, why):")
    for c in p["rows"]:
        lines.append("  %-16s %s %10.2f -> %8.2f  %s" % (c["name"][:16], datetime.fromtimestamp(c["t"]).strftime("%H:%M:%S"),
                                                        c["recorded"], c["corrected"], c["reason"]))
    if p.get("notes"):
        lines.append("")
        lines.extend("note: " + n for n in p["notes"])
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="romp spend-repair",
                                 description="recompute a day's spend from the turn ledger's re-attach staircase (T354); a dry run unless --apply")
    ap.add_argument("--day", help="the local date to repair (default: today)")
    ap.add_argument("--apply", action="store_true", help="write the corrected spend.json and turns.jsonl (default: print only)")
    ap.add_argument("--state", help="a state directory other than this machine's")
    ap.add_argument("--json", action="store_true", help="the plan as JSON")
    a = ap.parse_args(argv)
    state = Path(a.state) if a.state else state_dir()
    day = a.day or local_day(time.time())
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write("romp spend-repair: bad date %r (YYYY-MM-DD)\n" % day)
        return 2
    turns = read_jsonl(state / "turns.jsonl")
    restarts = restart_instants(read_jsonl(state / "restart-cuts.jsonl"), read_jsonl(state / "restart-audit.jsonl"))
    try:
        spend = json.loads((state / "spend.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        spend = {}
    p = plan(turns, restarts, day)
    new_spend = apply_to_spend(spend, p)      # computed either way, for the notes; written only with --apply
    if a.json:
        sys.stdout.write(json.dumps(p, indent=1, sort_keys=True) + "\n")
    else:
        sys.stdout.write(report(p) + "\n")
    if not a.apply:
        sys.stdout.write("\ndry run: nothing written (pass --apply to write spend.json and turns.jsonl)\n")
        return 0
    if not p["rows"]:
        sys.stdout.write("\nnothing to apply\n")
        return 0
    sp = state / "spend.json"
    tmp = sp.with_name("spend.json.repair.tmp")
    tmp.write_text(json.dumps(new_spend), encoding="utf-8")
    os.replace(tmp, sp)
    n = apply_to_turns(state / "turns.jsonl", p)
    sys.stdout.write("\napplied: spend.json rewritten, %d turn row(s) corrected (usdRecorded keeps the old figure)\n" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
