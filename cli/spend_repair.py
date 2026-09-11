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
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESTART_ACTIONS = ("manager-sigterm", "remote-restart", "main-converge", "p2p-update", "self-update")
TYPICAL_MULTIPLE = 3.0     # a first-after-restart row with no previous cumulative counts as cumulative only when it
MIN_STAIRCASE_USD = 20.0   # is this many times the session's typical turn and at least this much: a modest first
#                            turn after a restart is a turn, not the lifetime


def state_dir() -> Path:
    return Path(os.environ.get("ROMP_STATE_DIR")
                or Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp")


def read_jsonl(path: Path, bad: list = None) -> list:
    """Every parseable object row of `path`, its rotated predecessor (<name>.1) first when present. A line that does
    not parse (a torn append) is counted into `bad` when a list is given, so the report can say how many were skipped."""
    out = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            if not ln.strip():
                continue
            try:
                o = json.loads(ln)
            except Exception:
                if bad is not None:
                    bad.append(p.name)
                continue
            if isinstance(o, dict):
                out.append(o)
    return out


def local_day(t) -> str:
    return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d")


def local_hour(t) -> str:
    return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%dT%H")


BOOT_ANSWER_S = 300                 # an audit row (a restart REQUEST) that a boot row answers within this is the request, not the restart


def restart_instants(cuts: list, audit: list) -> list:
    """The moments a new kernel took over, sorted, deduplicated to the second: every restart-cuts BOOT row's
    `firstServe` (the epoch the new kernel began serving; the row's `t` when it has none), plus an audit row whose
    action asks for a restart only when no boot's first serve answers it within BOOT_ANSWER_S. Neither the audit row,
    the cut row nor the boot row's own `t` is the instant: the audit row is the REQUEST, the cut row is written by the
    DYING kernel after its drain while sessions are still unjoined (results land for seconds after both and are the old
    kernel's, ordinary deltas), and the boot row's `t` is the SETTLE, written once the reconcile is done, which lagged
    the first serve by three minutes on 2026-09-11 at 20:19Z while the re-billed first results landed from 20:19:44Z
    (firstServe 20:19:39.99Z; the two earlier boots that day settled within 9 s, which hid it). A request taken for
    the instant read a drain-time row as a fresh process's first result and the next real re-bill was corrected against
    that small figure (33 rows in those gaps that day, one session's $1,030 lifetime read as a $1,025 turn). Every row
    before the first-serve second is the old kernel's, a row at that very second too."""
    boots, out = [], set()
    for r in cuts:
        if not isinstance(r.get("t"), (int, float)):
            continue
        if "firstServe" in r or "settleS" in r or "bootSettled" in r:
            fs = r.get("firstServe")
            t = int(fs) if isinstance(fs, (int, float)) and fs > 0 else int(r["t"])
            boots.append(t); out.add(t)
    for r in audit:
        if isinstance(r.get("t"), (int, float)) and str(r.get("action") or "") in RESTART_ACTIONS:
            t = int(r["t"])
            if not any(t <= b <= t + BOOT_ANSWER_S for b in boots):
                out.add(t)
    return sorted(out)


def _typical(usds: list) -> float:
    return float(statistics.median(usds)) if usds else 0.0


def _kernel_usd(r: dict) -> float:
    """The figure the kernel wrote for a row: usdRecorded where a run corrected it, else usd."""
    v = r.get("usdRecorded")
    return float(v) if isinstance(v, (int, float)) else float(r["usd"])


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_spend(text: str) -> dict:
    """The ledger, or {} for no file; a file that does not parse (or is not an object) raises ValueError, and the
    caller refuses to run (M3 of the review)."""
    if not text.strip():
        return {}
    v = json.loads(text)
    if not isinstance(v, dict):
        raise ValueError("not a JSON object")
    return v


def registry_maps(state: Path):
    """From the registry (sdk/<sid>.json): {thread sid: owner sid} for comment threads (threadOf, the session the
    kernel billed the thread's turns to) and the set of sids whose CLI bills an API key (apiKeyAuth, the persisted
    init truth). Both are the registry's CURRENT word; a session that changed auth mid-day is read as it is now."""
    owners, keyed = {}, set()
    for f in sorted((state / "sdk").glob("*.json")) if (state / "sdk").is_dir() else []:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(r, dict):
            continue
        sid = str(r.get("sid") or f.stem)
        if r.get("threadOf"):
            owners[sid] = str(r["threadOf"])
        if r.get("apiKeyAuth"):
            keyed.add(sid)
    return owners, keyed


def parse_since(text: str):
    """--since: an ISO instant (2026-09-11T14:09:56Z, or a local date-time without a zone) or epoch seconds; None when empty."""
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    t = text.strip()
    if t.endswith("Z"):
        return datetime.strptime(t[:-1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(text)


def plan(turns: list, restarts: list, day: str, since=None, owners=None, keyed=None) -> dict:
    """The corrections for `day`: {"rows": [{sid, name, t, hour, recorded, corrected, reason}], "hours": {hour: {before,
    after}}, "days": {day: {before, after}}, "bySid": {sid: {name, before, after}}}, computed from the turn rows alone
    (the buckets' before/after are the sums over the day's rows, so a bucket the rows do not explain is left alone).
    A row is a staircase candidate when a restart instant lies after the session's previous row and at or before it
    (or, for the session's first row of the day, at or before it that day). `since` (epoch seconds) is the instant the
    per-session hosts came on: before it every restart killed the CLI, so a first result after a restart is a fresh
    process's own turn and never a step (a row corrected by an earlier run without the bound is restored). `owners`
    maps a comment thread's sid to the session it bills (the registry's threadOf: the kernel folds a thread's turns
    into its owner's bySid); `keyed` names the sids whose turns bill an API key (the registry's apiKeyAuth), whose
    corrections reach the buckets' `key` split too; a row whose session neither map knows is folded on its own sid
    and its keyed split is left as recorded."""
    owners, keyed = owners or {}, keyed or set()
    rows = [r for r in turns if isinstance(r.get("t"), (int, float)) and isinstance(r.get("usd"), (int, float))
            and local_day(r["t"]) == day]
    rows.sort(key=lambda r: (str(r.get("sid") or ""), float(r["t"])))
    by_sid = {}
    for r in rows:
        by_sid.setdefault(str(r.get("sid") or ""), []).append(r)
    day_start = datetime.strptime(day, "%Y-%m-%d").timestamp()
    corrections, all_ordinary = [], []
    # first pass per session: which rows are staircase steps, which are ordinary. A row already repaired (usdRecorded
    # keeps the figure the kernel wrote) is judged AGAIN on that figure, so a run over repaired rows finds nothing new
    # when the judgement stands and restores the row when it does not (a rule tightened after the first run brings a
    # zeroed turn back); a row written by a kernel that carries the CLI's cumulative (T354's fix) is never a step.
    marked = {}
    for sid, rs in by_sid.items():
        # the session's typical turn: the median of its rows that are NOT a first result after a restart (those are a
        # cumulative or a fresh process's first turn, both atypical); the same figure decides the threshold and the
        # correction, and it does not move when a first row is corrected or restored, so a second run agrees with the first
        firsts, pt = set(), day_start
        for r in rs:
            if any(pt < x < float(r["t"]) for x in restarts):    # exclusive: a row AT the boot's second is the old kernel's
                firsts.add(id(r))
            pt = float(r["t"])
        # the provisional typical turn for the first-cumulative threshold: rows following no restart, on the KERNEL's
        # figure (usdRecorded where a run corrected the row), so it reads the same on every run; the correction's
        # typical is taken below over the rows that are not steps, once they are known (low a of the review)
        typical = _typical([_kernel_usd(r) for r in rs if id(r) not in firsts]) or 0.0
        prev_t, prev_cum, steps, restores = day_start, None, [], []
        between = []
        for r in rs:
            t, usd = float(r["t"]), float(r["usd"])
            restarted = id(r) in firsts
            step = False
            if isinstance(r.get("cumulativeUsd"), (int, float)) or r.get("spendBaseline"):
                # written by a kernel that carries the CLI's cumulative on the row, or names a first result's
                # baseline (the fix): already right, never a step; the cumulative, where named, is the chain's baseline
                if isinstance(r.get("cumulativeUsd"), (int, float)):
                    prev_cum = float(r["cumulativeUsd"]); between = []
                prev_t = t
                continue
            repaired = isinstance(r.get("usdRecorded"), (int, float))
            rec = float(r["usdRecorded"]) if repaired else usd
            if since is not None and t < since:
                # before the hosts came on: a fresh process each restart, the turn stands (restored, when an earlier
                # run without the bound took it). No baseline carries over: the first restart after the hosts' start
                # kills the plain child, so the first row after it is a fresh process, judged as the day's first
                if repaired and abs(rec - usd) > 1e-9:
                    restores.append((r, rec, usd, "since", []))
                prev_t = t
                continue
            if restarted:
                if prev_cum is not None:
                    # the surviving process's lifetime again: at or above the previous cumulative PLUS every turn recorded
                    # between, since a process's total grows by at least what its own rows recorded. A figure below that
                    # cannot be this process's cumulative: it is a fresh process's first turn. (The first run's rule was
                    # `>= prev_cum` alone and zeroed 49 small genuine turns on 2026-09-11; they carry usdRecorded and
                    # come back through here to be restored.)
                    step = rec >= prev_cum + sum(between) - 1e-6
                else:
                    step = rec >= max(MIN_STAIRCASE_USD, TYPICAL_MULTIPLE * typical) if typical else rec >= MIN_STAIRCASE_USD
            elif steps and prev_cum is not None and rec >= max(prev_cum + sum(between) - 1e-6, MIN_STAIRCASE_USD):
                # no restart instant on record between this row and the last (a crash leaves no audit row), yet the
                # row bears the staircase's signature on a chain this session has already shown: at or above the
                # previous cumulative plus every turn between, and no small figure. Taken as a step, said in the
                # reason; without an established chain the signature alone is not believed
                step = "no-instant"
            if step:
                steps.append((r, rec, usd, prev_cum, list(between), step == "no-instant"))
                prev_cum = rec
                between = []
            elif restarted or repaired:
                # a FRESH process's first result (below the previous cumulative plus the rows between, or a modest first
                # turn): the turn stands as the kernel recorded it, and its total IS the new process's cumulative, so the
                # chain continues from it; a repaired row that is no step is restored to the kernel's figure
                if repaired and abs(rec - usd) > 1e-9:
                    restores.append((r, rec, usd, prev_cum, list(between)))
                if restarted:
                    prev_cum = rec
                    between = []
                else:
                    between.append(rec)        # a repaired row that follows no restart (an earlier run's instants were
                    #                            wrong): an ordinary turn, restored above, counted between
            else:
                between.append(usd)
            prev_t = t
        if steps and steps[0][3] is None and not (len(steps) > 1 and steps[1][3] == steps[0][1]):
            # the day's first cumulative row is believed only when a staircase DESCENDS from it: the session's next
            # step must stand on it (its previous cumulative is this row's figure, so it is at or above it plus the
            # turns between). Alone, or followed only by a chain that started afresh (a CLI that died mid-day), a big
            # first result after a restart is as likely an honest long first turn, and it stands (restored, when an
            # earlier run took it) (M2 of the review)
            r, rec, usd, _, _, _ = steps.pop(0)
            if isinstance(r.get("usdRecorded"), (int, float)) and abs(rec - usd) > 1e-9:
                restores.append((r, rec, usd, None, []))
        step_ids = {id(x[0]) for x in steps}
        ordinary = [_kernel_usd(r) for r in rs if id(r) not in step_ids]   # every row that is not a step, the kernel's figure
        marked[sid] = (steps, restores, _typical(ordinary) or 0.0)
        all_ordinary.extend(ordinary)
    day_typical = _typical(all_ordinary)

    def entry(r, sid, rec, cur, corrected, reason, **extra):
        return {"sid": sid, "owner": owners.get(sid, sid), "keyed": sid in keyed,
                "name": str(r.get("name") or sid[:8]), "t": int(r["t"]), "hour": local_hour(r["t"]),
                "recorded": round(rec, 6), "current": round(cur, 6), "corrected": round(corrected, 6), "reason": reason, **extra}

    for sid, (steps, restores, typical) in marked.items():
        typical = typical or day_typical
        for r, rec, cur, prev_cum, between, no_instant in steps:
            if prev_cum is None:
                corrected, reason = typical, "the day's first cumulative row: a typical turn (median %.4f)" % typical
            else:
                corrected = rec - prev_cum - sum(between)      # >= 0 by the bound that made this a step, to float noise
                if corrected < 0:
                    corrected = 0.0
                reason = "cumulative %.4f less the previous cumulative %.4f less %d row(s) between (%.4f)%s" % (
                    rec, prev_cum, len(between), sum(between), "; no restart instant on record, the staircase's signature alone" if no_instant else "")
            if abs(corrected - cur) < 1e-6:
                continue                       # already right: a run over repaired rows
            corrections.append(entry(r, sid, rec, cur, corrected, reason))
        for r, rec, cur, prev_cum, between in restores:
            if prev_cum == "since":
                reason = "restored: %.4f precedes the hosts' start (%s), a fresh process's turn" % (
                    rec, datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S"))
            elif prev_cum is None:
                reason = "restored: %.4f is a lone first result after a restart with no staircase following it, a turn" % rec
            else:
                reason = "restored: %.4f is below the previous cumulative %.4f plus %d row(s) between (%.4f), a turn of a fresh process" % (
                    rec, prev_cum, len(between), sum(between))
            corrections.append(entry(r, sid, rec, cur, rec, reason, restore=True))
    hours, sids = {}, {}
    for r in rows:
        h, sid = local_hour(r["t"]), str(r.get("sid") or "")
        owner = owners.get(sid, sid)                    # a thread's turns count under the session it bills
        hours.setdefault(h, {"before": 0.0, "after": 0.0})
        sids.setdefault(owner, {"name": str(r.get("name") or sid[:8]) if owner == sid else owner[:8], "before": 0.0, "after": 0.0})
        if owner == sid:
            sids[owner]["name"] = str(r.get("name") or sid[:8])
        hours[h]["before"] += float(r["usd"]); hours[h]["after"] += float(r["usd"])
        sids[owner]["before"] += float(r["usd"]); sids[owner]["after"] += float(r["usd"])
    for c in corrections:
        d = c["corrected"] - c["current"]
        hours[c["hour"]]["after"] += d
        sids[c["owner"]]["after"] += d
    for m in list(hours.values()) + list(sids.values()):
        m["before"], m["after"] = round(m["before"], 6), round(m["after"], 6)
    before = round(sum(h["before"] for h in hours.values()), 6)
    after = round(sum(h["after"] for h in hours.values()), 6)
    return {"day": day, "since": since, "rows": sorted(corrections, key=lambda c: c["t"]), "hours": dict(sorted(hours.items())),
            "unkeyedRows": sum(1 for c in corrections if not c["keyed"]),
            "days": {day: {"before": before, "after": after}}, "bySid": sids, "restarts": len([x for x in restarts if local_day(x) == day])}


def apply_to_spend(spend: dict, p: dict) -> dict:
    """spend.json with the plan's deltas folded into the day's hour buckets, its day bucket and their bySid maps
    (dollars only; turns and tokens stay). A row's delta reaches bySid under the session it BILLS (a comment
    thread's owner, the registry's threadOf, as the kernel folded it) and the `key` split only when that session
    bills an API key (the registry's apiKeyAuth; turn rows carry no keyed flag of their own), else the split is left as
    recorded and the plan's notes say for how many rows. A bucket the ledger lacks is left alone, said in the notes."""
    out = json.loads(json.dumps(spend or {}))
    hours = out.setdefault("hours", {}) if isinstance(out.get("hours"), dict) else out.__setitem__("hours", {}) or out["hours"]
    days = out.setdefault("days", {}) if isinstance(out.get("days"), dict) else out.__setitem__("days", {}) or out["days"]

    def fold(bucket, owner, delta, keyed):
        if not isinstance(bucket, dict):
            return False
        bucket["usd"] = round(max(0.0, float(bucket.get("usd") or 0) + delta), 6)
        k = bucket.get("key")
        if keyed and isinstance(k, dict) and isinstance(k.get("usd"), (int, float)):
            k["usd"] = round(max(0.0, float(k["usd"]) + delta), 6)
        by = bucket.get("bySid")
        if isinstance(by, dict) and isinstance(by.get(owner), dict):
            by[owner]["usd"] = round(max(0.0, float(by[owner].get("usd") or 0) + delta), 6)
            sk = by[owner].get("key")
            if keyed and isinstance(sk, dict) and isinstance(sk.get("usd"), (int, float)):
                sk["usd"] = round(max(0.0, float(sk["usd"]) + delta), 6)
        elif isinstance(by, dict):
            return "no bySid entry for %s" % owner[:8]
        return True

    notes = []
    for c in p["rows"]:
        delta = c["corrected"] - c["current"]      # against the row as it stands now (a repaired row's current figure)
        for where, bucket in (("hour %s" % c["hour"], hours.get(c["hour"])), ("day %s" % p["day"], days.get(p["day"]))):
            got = fold(bucket, c["owner"], delta, c["keyed"])
            if got is False:
                notes.append("no bucket for %s (%s)" % (where, c["name"]))
            elif got is not True:
                notes.append("%s: %s" % (where, got))
    if p.get("unkeyedRows"):
        notes.append("%d corrected row(s) belong to sessions the registry does not mark as API-key billed: their buckets' "
                     "key split is left as recorded" % p["unkeyedRows"])
    p["notes"] = sorted(set(notes))
    return out


def apply_to_turns(path: Path, p: dict) -> list:
    """turns.jsonl (and its predecessor) rewritten with each corrected row's usd, the kernel's figure kept as
    usdRecorded (a row corrected twice keeps the original); a restored row gets the kernel's figure back and loses its
    repair marks. Returns the plan rows actually rewritten (matched by sid, second and the figure the row held), so the
    caller folds the buckets for those and no other. The kernel appends to this file while it runs (one open-append-
    close per result): the file is read again just before the replace and every line past the count first read rides
    along, and read once more after it. Atomic per file."""
    want = {(c["sid"], c["t"], c["current"]): c for c in p["rows"]}
    done = []
    for f in (path.with_name(path.name + ".1"), path):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        out = []
        n_read = len(lines)
        for ln in lines:
            try:
                o = json.loads(ln)
            except Exception:
                out.append(ln); continue
            key = (str(o.get("sid") or ""), int(o["t"]) if isinstance(o.get("t"), (int, float)) else None,
                   round(float(o["usd"]), 6) if isinstance(o.get("usd"), (int, float)) else None)
            c = want.pop(key, None)
            if c is not None:
                if c.get("restore"):
                    o["usd"] = c["corrected"]
                    o.pop("usdRecorded", None); o.pop("repairedT", None)
                else:
                    o.setdefault("usdRecorded", o["usd"])
                    o["usd"] = c["corrected"]
                    o["repairedT"] = int(time.time())
                done.append(c)
            out.append(json.dumps(o))
        if not done:
            continue                                  # nothing of the plan in this file: leave it untouched
        try:
            now_lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            now_lines = lines
        if len(now_lines) > n_read and now_lines[:n_read] == lines:
            out.extend(now_lines[n_read:])
        tmp = f.with_name(f.name + ".repair.tmp")
        tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        os.replace(tmp, f)
        try:
            after = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            after = out
        if len(after) < len(out):
            f.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return done


def report(p: dict) -> str:
    n_restore = sum(1 for c in p["rows"] if c.get("restore"))
    lines = ["spend repair for %s: %d restart(s) that day, %d cumulative row(s) found%s" % (
        p["day"], p["restarts"], len(p["rows"]) - n_restore, ", %d earlier correction(s) to restore" % n_restore if n_restore else "")]
    if p.get("since") is not None:
        lines.append("rows before %s (the hosts' start, --since) are fresh processes' turns, never steps"
                     % datetime.fromtimestamp(p["since"]).strftime("%Y-%m-%d %H:%M:%S"))
    else:
        lines.append("no --since: every first result after a restart is judged, the hosts' start unknown (a plain child's "
                     "long first turn can pass the bound by chance; pass the instant the hosts came on)")
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
    lines.append("rows (session, time, now -> corrected, why):")
    for c in p["rows"]:
        lines.append("  %-16s %s %10.2f -> %8.2f  %s" % (c["name"][:16], datetime.fromtimestamp(c["t"]).strftime("%H:%M:%S"),
                                                        c["current"], c["corrected"], c["reason"]))
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
    ap.add_argument("--since", help="the instant the per-session hosts came on (ISO, Z or local; or epoch seconds): a first result "
                    "after a restart before it is a fresh process's turn, never a step; default: the whole day")
    ap.add_argument("--json", action="store_true", help="the plan as JSON")
    ap.add_argument("--no-backup", action="store_true", help="--apply without the spend.json.bak-<stamp> and turns.jsonl.bak-<stamp> copies")
    a = ap.parse_args(argv)
    state = Path(a.state) if a.state else state_dir()
    day = a.day or local_day(time.time())
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write("romp spend-repair: bad date %r (YYYY-MM-DD)\n" % day)
        return 2
    try:
        since = parse_since(a.since or "")
    except ValueError:
        sys.stderr.write("romp spend-repair: bad --since %r (ISO instant or epoch seconds)\n" % a.since)
        return 2
    bad = []
    turns = read_jsonl(state / "turns.jsonl", bad)
    restarts = restart_instants(read_jsonl(state / "restart-cuts.jsonl"), read_jsonl(state / "restart-audit.jsonl"))
    owners, keyed = registry_maps(state)
    spend_text = read_text(state / "spend.json")
    try:
        spend = parse_spend(spend_text)
    except ValueError as e:
        # a ledger that does not parse is refused, never overwritten (M3 of the review: an empty ledger written over
        # 90 days of buckets with exit 0 was the alternative)
        sys.stderr.write("romp spend-repair: %s does not parse (%s); refusing to run on it. Nothing written.\n" % (state / "spend.json", e))
        return 2
    p = plan(turns, restarts, day, since, owners, keyed)
    if bad:
        sys.stdout.write("%d unparseable line(s) in %s skipped (a torn append)\n" % (len(bad), ", ".join(sorted(set(bad)))))
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
    # the kernel may be running and folding results as this runs (the same race bin/romp-spend-rebuild states): the
    # ledger is read again right before the write and the fold recomputed on what is there now, so a turn folded
    # since the plan's read is kept; the window left is the replace itself. The copies beside the files are the
    # undo: spend.json.bak-<stamp> and turns.jsonl.bak-<stamp>.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if not a.no_backup:
        for name in ("spend.json", "turns.jsonl"):
            src = state / name
            if src.exists():
                shutil.copy2(src, src.with_name("%s.bak-%s" % (name, stamp)))
        sys.stdout.write("\nbackups: spend.json.bak-%s, turns.jsonl.bak-%s (beside the files)\n" % (stamp, stamp))
    # turns.jsonl FIRST, then the buckets for the rows actually rewritten (low c of the review): a planned row the
    # file no longer holds as planned (the ledger moved) is left alone in both places, said below, and the next run
    # judges it afresh; before this the buckets moved for every planned row and a row the rewrite missed had its
    # delta folded again on the next run
    done = apply_to_turns(state / "turns.jsonl", p)
    missed = len(p["rows"]) - len(done)
    fresh_text = read_text(sp)
    try:
        base = parse_spend(fresh_text)
    except ValueError as e:
        sys.stderr.write("romp spend-repair: spend.json stopped parsing between the plan and the write (%s); the rows were "
                         "rewritten, the buckets were not: run again once it parses\n" % e)
        return 2
    if fresh_text != spend_text:
        sys.stdout.write("spend.json moved since the plan's read (a result folded meanwhile): the fold was recomputed on the file as it stands\n")
    new_spend = apply_to_spend(base, dict(p, rows=done))
    tmp = sp.with_name("spend.json.repair.tmp")
    tmp.write_text(json.dumps(new_spend), encoding="utf-8")
    os.replace(tmp, sp)
    n_restore = sum(1 for c in done if c.get("restore"))
    sys.stdout.write("\napplied: %d turn row(s) corrected (usdRecorded keeps the old figure)%s, then spend.json rewritten for those\n"
                     % (len(done) - n_restore, ", %d restored to the kernel's figure" % n_restore if n_restore else ""))
    if missed:
        sys.stdout.write("%d planned row(s) were not in turns.jsonl as planned (the ledger moved): left as they are in both files, judged afresh next run\n" % missed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
