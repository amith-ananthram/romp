#!/usr/bin/env python3
"""romp-spend-rebuild — recount the TOKEN columns of the spend ledger from the transcripts. `romp spend-rebuild`.

The rail's API cell reads `~/.local/state/romp/spend.json`, which the SDK backend fills one turn RESULT at a
time: the turn's cost delta plus its four token counts (input / output / cache read / cache write). Until
2026-09-06 the token counts were diffed against the previous result as if they were a running total, the way
the dollars are — but on current Claude Code the result's flat `usage` is the TURN's own total, so every turn
but a process's first recorded a fraction of itself (a day read as roughly half its true count; see
kernel/sdk_backend.py `_turn_usage` for the measurement). The recorder is fixed; this recounts what it wrote.

The transcripts are the CLI's own per-call record: every assistant message carries the `usage` block the API
returned for that call — the session's own file (`<projects>/<slug>/<sid>.jsonl`) AND its subagents' files
(`<projects>/<slug>/<sid>/subagents/agent-*.jsonl`; the Agent tool's calls run in the same CLI process and are
in the recorder's modelUsage total, and they were 45% of one measured day). Summing them by local hour and
day, per session, gives the ledger's token columns directly — and the sum is what the fixed recorder would
have written. Dollars and turn counts are the
recorder's and are left exactly as they are; only the token columns (bucket, `key` sub-count, `bySid` rows)
are rewritten. A bucket the ledger does not hold is added only inside the span it already covers, with zero
dollars and turns (a turn's calls can straddle an hour edge; its result lands in one bucket).

A recount can be INCOMPLETE but never over-complete: streaming splits are counted once by message id, a
fork's copied history dedupes the same way, and only sessions the kernel ran are read. So a bucket whose
recount comes out LOWER than the recorder wrote is missing evidence — Claude Code removes transcripts after
its cleanup period, and a session's file may be gone — and is kept as recorded unless --allow-lower says
the lower figure is wanted anyway.

Usage:
    romp spend-rebuild               # dry run: prints the change per day, writes nothing
    romp spend-rebuild --apply       # rewrites spend.json; the old file is kept as spend.json.bak-<stamp>
    romp spend-rebuild --by-session  # also list each session's recount (dry run or apply)
    romp spend-rebuild --allow-lower # rewrite buckets whose recount is lower than recorded, too

Nothing here reads message text — timestamps, ids and usage numbers only — and nothing is printed but
counts and session names. Run it while the sessions are quiet: the recorder and this tool both rewrite
the whole file, and a turn settling in the same instant as --apply could lose its own token line.
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

KINDS = (("tokIn", "input_tokens"), ("tokOut", "output_tokens"),
         ("tokCacheR", "cache_read_input_tokens"), ("tokCacheW", "cache_creation_input_tokens"))


def state_dir():
    return Path(os.environ.get("ROMP_STATE_DIR")   # per-kernel state root (plans/multi-kernel.md)
                or Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp")


def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _registry(state):
    """{sid: {name, keyed, threadOf, ids}} from the SDK backend's per-session registry files. The registry
    keeps a dead session's file, so this is every SDK session the kernel ever ran — the ledger's universe
    (a Codex session bills no Claude account, never reaches spend.json and has no file here)."""
    out = {}
    for p in sorted(glob.glob(str(state / "sdk" / "*.json"))):
        try:
            d = json.loads(Path(p).read_text())
        except Exception:
            continue
        sid = str(d.get("sid") or Path(p).stem)
        ids = {sid}
        if d.get("lastSid"):
            ids.add(str(d["lastSid"]))     # a resumed session's transcript is named for the CLI's id
        # …and every EARLIER episode: a /clear mints a new fsid and leaves the previous conversation under
        # the old one (episodes/<sid>.jsonl, one fsid per row), and a resume fork records both ends
        # (states/<sid>.jsonl resumeFork rows) — the same set the kernel's known_fsids() derives, without
        # loading the SDK backend for it (review find on #956, 2026-09-07: those transcripts were skipped,
        # so a session that /cleared lost its earlier tokens on rebuild)
        for sub, pick in (("episodes", lambda r: [r.get("fsid")]),
                          ("states", lambda r: [(r.get("resumeFork") or {}).get(k) for k in ("from", "to")])):
            try:
                lines = (state / sub / (sid + ".jsonl")).read_text().splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    ids.update(str(v) for v in pick(r) if v)
        out[sid] = {"name": str(d.get("name") or sid[:8]),
                    "keyed": bool(d.get("apiKeyAuth")) or str(d.get("auth") or "") == "key",
                    "threadOf": str(d.get("threadOf") or ""), "ids": ids}
    return out


def _bucket_keys(ts):
    """A transcript timestamp (ISO 8601, UTC 'Z') → (hour key, day key) in LOCAL time, the recorder's own
    keying (time.strftime on the local clock at record time)."""
    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    return t.strftime("%Y-%m-%dT%H"), t.strftime("%Y-%m-%d")


def _lane_files(subdir):
    """Every .jsonl under a session's subagents directory, at any depth (Claude Code 2.1.261 writes a Workflow agent's
    transcript at workflows/wf_<id>/agent-<id>.jsonl; a flat glob missed every one, T355), no symlink followed or taken:
    the kernel's _subagent_transcripts rule."""
    if os.path.islink(subdir) or not os.path.isdir(subdir):
        return []
    out = []
    for root, dirs, files in os.walk(subdir):        # followlinks=False
        dirs.sort()
        out.extend(p for p in (os.path.join(root, n) for n in files if n.endswith(".jsonl")) if not os.path.islink(p))
    return sorted(out)


def _scan(paths, seen):
    """Per-call usage from transcript files: [(hour, day, {kind: n})]. Streaming writes one message as several
    records that share `message.id` — counted once. Subagent (sidechain) calls count: the recorder's source,
    the result's modelUsage, covers them. `seen` is shared across files (a fork copies its parent's history)."""
    rows = []
    for p in paths:
        try:
            f = open(p, errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("type") != "assistant":
                    continue
                m = r.get("message") if isinstance(r.get("message"), dict) else {}
                mid, u, ts = m.get("id"), m.get("usage"), r.get("timestamp")
                if not mid or not isinstance(u, dict) or not isinstance(ts, str) or mid in seen:
                    continue
                seen.add(mid)
                try:
                    hk, dk = _bucket_keys(ts)
                except Exception:
                    continue
                rows.append((hk, dk, {k: int(u.get(uk) or 0) for k, uk in KINDS}))
    return rows


def recount(state, claude, ledger):
    """{("hours"|"days", key): {sid: {kind: n}}} for every session the registry or the ledger names."""
    reg = _registry(state)
    sids = set(reg)
    for kind in ("days", "hours"):
        for e in (ledger.get(kind) or {}).values():
            if isinstance(e, dict) and isinstance(e.get("bySid"), dict):
                sids |= set(e["bySid"])
    per, seen = {}, set()
    for sid in sorted(sids):
        info = reg.get(sid) or {"name": sid[:8], "keyed": None, "threadOf": "", "ids": {sid}}
        owner = info["threadOf"] or sid          # a comment thread bills its owning session (T144)
        paths = []
        for fid in sorted(info["ids"]):
            paths += glob.glob(str(claude / "projects" / "*" / (fid + ".jsonl")))
            for sd in glob.glob(str(claude / "projects" / "*" / fid / "subagents")):   # the Agent tool's lanes, one level or deeper
                paths += _lane_files(sd)                                                #  (workflow agents sit under workflows/wf_<id>/)
        for hk, dk, u in _scan(sorted(set(paths)), seen):
            for kind, key in (("hours", hk), ("days", dk)):
                slot = per.setdefault((kind, key), {}).setdefault(owner, {k: 0 for k, _ in KINDS})
                for k, _ in KINDS:
                    slot[k] += u[k]
    return per, reg


def rebuild(ledger, per, reg, now=None, allow_lower=False):
    """The ledger with its token columns recounted. Returns (new_ledger, changes, kept): changes lists
    (kind, key, old_tok, new_tok) for every bucket rewritten; kept lists the buckets whose recount came out
    LOWER than recorded and were left as they were (missing evidence, see the module doc) — empty when
    allow_lower is set, in which case they are rewritten and listed in changes."""
    now = time.time() if now is None else now
    out = {"days": {}, "hours": {}}
    changes, kept = [], []
    for kind in ("days", "hours"):
        buckets = dict(ledger.get(kind) or {}) if isinstance(ledger.get(kind), dict) else {}
        keys = set(buckets)
        # a bucket the recorder never wrote (a turn's calls straddling an hour edge) is added only inside
        # the span the ledger already covers — never extending its history
        if buckets:
            lo = min(buckets)
            for (k2, key), _ in per.items():
                if k2 == kind and key >= lo and key <= _now_key(kind, now):
                    keys.add(key)
        for key in sorted(keys):
            e = dict(buckets.get(key) or {}) if isinstance(buckets.get(key), dict) else {}
            counts = per.get((kind, key), {})
            old_tok = sum(int(e.get(k) or 0) for k, _ in KINDS)
            tot = {k: sum(c[k] for c in counts.values()) for k, _ in KINDS}
            # The never-lower rule holds PER SESSION, not only for the bucket total: a session whose
            # transcript is gone recounts to zero, and when a sibling's recount lifts the total past the
            # bar the bucket used to pass while that session's bySid row and its share of the key
            # sub-count were silently zeroed (review find on #956, 2026-09-07). Any recorded session
            # that recounts lower is missing evidence, so the whole bucket is kept as recorded.
            by_old = e.get("bySid") if isinstance(e.get("bySid"), dict) else {}
            sid_lower = [s for s, se in by_old.items()
                         if isinstance(se, dict) and int(se.get("tok") or 0) > sum((counts.get(s) or {}).values())]
            if (sum(tot.values()) < old_tok or sid_lower) and not allow_lower:
                out[kind][key] = e          # a lower recount is missing evidence, not a correction
                kept.append((kind, key, old_tok, sum(tot.values())))
                continue
            n = {"usd": round(float(e.get("usd") or 0), 6), "turns": int(e.get("turns") or 0)}
            n.update(tot)
            keyed_sids = {s for s in counts if _is_keyed(s, reg, e)}
            ktot = {k: sum(counts[s][k] for s in keyed_sids) for k, _ in KINDS}
            ke = e.get("key") if isinstance(e.get("key"), dict) else None
            if ke is not None or keyed_sids:
                ke = ke or {}
                n["key"] = {"usd": round(float(ke.get("usd") or 0), 6), "turns": int(ke.get("turns") or 0),
                            "tok": sum(ktot.values())}
                n["key"].update(ktot)
            by = dict(e.get("bySid")) if isinstance(e.get("bySid"), dict) else {}
            for s in set(by) | set(counts):
                se = dict(by.get(s) or {}) if isinstance(by.get(s), dict) else {}
                c = counts.get(s) or {k: 0 for k, _ in KINDS}
                sn = {"usd": round(float(se.get("usd") or 0), 6), "turns": int(se.get("turns") or 0),
                      "tok": sum(c.values())}
                ske = se.get("key") if isinstance(se.get("key"), dict) else None
                if ske is not None or s in keyed_sids:
                    ske = ske or {}
                    sn["key"] = {"usd": round(float(ske.get("usd") or 0), 6), "turns": int(ske.get("turns") or 0),
                                 "tok": sum(c.values()) if s in keyed_sids else 0}
                by[s] = sn
            if by:
                n["bySid"] = by
            for extra in e:          # anything this tool does not understand rides along untouched
                if extra not in n and extra not in ("key", "bySid"):
                    n[extra] = e[extra]
            out[kind][key] = n
            if old_tok != sum(tot.values()):
                changes.append((kind, key, old_tok, sum(tot.values())))
    return out, changes, kept


def _now_key(kind, now):
    return time.strftime("%Y-%m-%dT%H" if kind == "hours" else "%Y-%m-%d", time.localtime(now))


def _is_keyed(sid, reg, bucket):
    """Did `sid` bill the API key in this bucket? The LEDGER already holds the per-turn truth: the
    recorder writes bySid[sid].key exactly when that sid billed the key in that bucket. The registry
    holds only the session's CURRENT auth, which an auth flip overwrites — so reading it first moved an
    auth-flipped session's whole history across the key split on rebuild (review find on #956,
    2026-09-07). Ledger first; the registry only for a sid with no bySid row in this bucket."""
    by = bucket.get("bySid") if isinstance(bucket.get("bySid"), dict) else {}
    se = by.get(sid) if isinstance(by.get(sid), dict) else None
    if se is not None:
        ske = se.get("key") if isinstance(se.get("key"), dict) else None
        if ske is not None:
            return int(ske.get("turns") or 0) > 0
        if int(se.get("turns") or 0) > 0:
            return False                     # turns recorded, none of them keyed
    info = reg.get(sid)
    if info is not None and info["keyed"] is not None:
        return bool(info["keyed"])
    # no registry file (a dead session's is gone): if every turn in the bucket billed the key, so did this
    ke = bucket.get("key") if isinstance(bucket.get("key"), dict) else None
    return bool(ke) and int(ke.get("turns") or 0) == int(bucket.get("turns") or 0) > 0


def _fmt(n):
    if n >= 1e9:
        return "%.2fB" % (n / 1e9) if n < 1e10 else ("%.1fB" % (n / 1e9) if n < 1e11 else "%.0fB" % (n / 1e9))
    if n >= 1e6:
        return "%.2fM" % (n / 1e6) if n < 1e7 else ("%.1fM" % (n / 1e6) if n < 1e8 else "%.0fM" % (n / 1e6))
    if n >= 1e3:
        return "%.2fk" % (n / 1e3) if n < 1e4 else ("%.1fk" % (n / 1e3) if n < 1e5 else "%.0fk" % (n / 1e3))
    return str(n)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="romp spend-rebuild", description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--apply", action="store_true", help="rewrite spend.json (default: dry run)")
    ap.add_argument("--by-session", action="store_true", help="also print each session's recount")
    ap.add_argument("--allow-lower", action="store_true",
                    help="rewrite buckets whose recount is LOWER than recorded too (default: keep them — a transcript may be gone)")
    ap.add_argument("--state-dir", default=None, help="romp state dir (default: $ROMP_STATE_DIR or ~/.local/state/romp)")
    ap.add_argument("--claude-dir", default=None, help="Claude config dir holding projects/ (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    a = ap.parse_args(argv)
    state = Path(a.state_dir) if a.state_dir else state_dir()
    claude = Path(a.claude_dir) if a.claude_dir else claude_dir()
    p = state / "spend.json"
    try:
        ledger = json.loads(p.read_text())
    except Exception as ex:
        print("no ledger to rebuild at %s (%s)" % (p, ex), file=sys.stderr)
        return 2
    per, reg = recount(state, claude, ledger)     # the slow part: parses every session's transcript once
    new, changes, kept = rebuild(ledger, per, reg, allow_lower=a.allow_lower)
    kept_days = {k: n for kind, k, o, n in kept if kind == "days"}
    days = sorted(new["days"])
    print("%-12s %14s %14s" % ("day", "tokens (was)", "tokens (now)"))
    for k in days:
        o = ledger.get("days", {}).get(k) or {}
        was = sum(int(o.get(x) or 0) for x, _ in KINDS)
        now_ = sum(int(new["days"][k].get(x) or 0) for x, _ in KINDS)
        if k in kept_days:
            print("%-12s %14s %14s   (kept: recount %s is lower — a transcript may be gone)" % (k, _fmt(was), _fmt(was), _fmt(kept_days[k])))
        elif was != now_ or a.by_session:
            print("%-12s %14s %14s%s" % (k, _fmt(was), _fmt(now_), "" if was != now_ else "   (unchanged)"))
    print("%d bucket%s change (%d day, %d hour)" % (len(changes), "" if len(changes) == 1 else "s",
          sum(1 for c in changes if c[0] == "days"), sum(1 for c in changes if c[0] == "hours")))
    if kept:
        print("%d bucket%s kept as recorded — the recount was lower (%d day, %d hour); --allow-lower rewrites them"
              % (len(kept), "" if len(kept) == 1 else "s", sum(1 for c in kept if c[0] == "days"),
                 sum(1 for c in kept if c[0] == "hours")))
    if a.by_session:
        tot = {}
        for (kind, key), counts in per.items():
            if kind != "days":
                continue
            for s, c in counts.items():
                tot[s] = tot.get(s, 0) + sum(c.values())
        for s, n in sorted(tot.items(), key=lambda kv: -kv[1]):
            print("  %-24s %12s" % ((reg.get(s) or {}).get("name", s[:8]), _fmt(n)))
    if not a.apply:
        print("dry run — nothing written (add --apply)")
        return 0
    if not changes:
        print("nothing to write")
        return 0
    # re-read right before writing: the recorder may have folded a turn while the transcripts parsed
    fresh = json.loads(p.read_text())
    if fresh != ledger:
        new, changes, kept = rebuild(fresh, per, reg, allow_lower=a.allow_lower)
    bak = p.with_name("spend.json.bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(p, bak)
    tmp = p.with_name("spend.json.tmp")
    tmp.write_text(json.dumps(new))
    os.replace(tmp, p)
    print("written; the previous ledger is %s" % bak.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
