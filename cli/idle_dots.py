#!/usr/bin/env python3
"""romp-idle-dots — the timer-side state doctor for romp sessions.

tmux @claude-state/@romp-emoji are written ONLY by the tmux-status.sh hook on
Claude events, so any state that should change while NO event fires needs a
timer. This watcher is that timer, sweeping live romp sessions every INTERVAL
for the two missing-timer cases:

1. IDLE FADE: a READY (waiting/idle) session fires no events, so its 🔵 dot
   would live forever — quiet > STALE_AFTER_SECS swaps it to ⚫, the tab-strip
   analog of how the dashboard and timeline fade an idle row (same threshold).

2. STUCK WORKING: Claude fires NO hook when a turn is interrupted with Esc, so
   an interrupted session sits at @claude-state=working forever — stranding the
   chat-tab chip, the timeline work-bar, AND the ghostty 🟡 dot at once (the
   2026-06-10 test_slector incident). A stale `since` ALONE can't tell an
   interrupted session from one legitimately inside a long tool call (neither
   fires events), so the PANE disambiguates: a genuinely-working Claude shows
   "(esc to interrupt)"; an interrupted one shows the idle composer (❯). Only
   working/compacting + stale since + idle-looking pane heals — mirroring the
   hook's Stop branch (state+since+emoji+states/<sid>.jsonl transition) so the
   chip, timeline, and dot all recover together. romp-chat-view's own Ctrl+C
   path resets state inline (markPaneIdle); this catches interrupts made in
   the terminal, where no software of ours is in the loop.

Healing is conservative: a pane in copy-mode (scrolled back) or with
unrecognizable content is left alone, and `since` is re-read just before
writing so a real hook event mid-sweep always wins.

Lifecycle: `--ensure` spawns a detached daemon
if one isn't already running, re-ensured from the status hook on idle AND on
prompt-submit (a turn can only get stuck after a prompt started it). The
daemon exits once no romp session remains.

Usage:
  romp-idle-dots            # daemon: loop until no romp sessions remain
  romp-idle-dots --once     # a single sweep (used by tests / manual checks)
  romp-idle-dots --ensure   # spawn the daemon if not already running
"""
import os, re, sys, time, subprocess
from pathlib import Path

HOME    = Path.home()
STATE   = Path(os.environ.get("ROMP_STATE_DIR")   # per-kernel state root override (plans/multi-kernel.md)
               or Path(os.environ.get("XDG_STATE_HOME") or str(HOME / ".local/state")) / "romp")
# `or`, not a .get default: an empty XDG_STATE_HOME is unset (the note at kernel/event_model.py's STATE).
PIDFILE = STATE / "idle-dots.pid"

STALE_AFTER_SECS = 3600          # 1h — matches dashboard STALE_AFTER_SECS + timeline STALE
INTERVAL         = 5             # sweep cadence — must be fast enough to CATCH a compaction's START
                                 # (then COMPACT_INTERVAL polls the live %); a 60s cadence missed the
                                 # first ~50% of a ~20s compaction (the user). Cheap: a normal sweep is
                                 # just `tmux list-sessions` — capture-pane fires only while compacting/stuck.
COMPACT_INTERVAL = 4             # while a session is COMPACTING, sweep this fast to publish a live %
DOT_INACTIVE     = "⚫"      # ⚫ black circle
FADEABLE         = ("waiting", "idle")   # only READY fades (not 🔴 permission / 🟡 working)

# ── stuck-working healer (case 2 in the docstring) ──────────────────────────
# permission/picker joined 2026-06-11 (timeline_window incident): a phantom or
# answered prompt strands those states the same way an Esc strands "working" —
# no hook ever fires to clear them. The DIALOG_ROW guard keeps a REAL pending
# prompt safe: its cursor sits on a numbered row (❯ 1. Yes …), which an idle
# composer never shows.
STUCK_STATES     = ("working", "compacting", "permission", "picker")
STUCK_AFTER_SECS = 120     # since-staleness gate before we even capture the pane;
                           # an ACTIVE session refreshes since on every PostToolUse
BUSY_MARKER      = "esc to interrupt"   # the live-spinner suffix a working Claude shows
IDLE_MARKER      = "❯"     # the composer prompt an interrupted/idle pane shows
DIALOG_ROW       = re.compile(r"(?m)^\s*❯\s*\d+\.\s")   # a picker/permission cursor row


def diagnose(state, since, now, in_mode, pane):
    """Pure heal/leave decision for one session (unit-tested separately).
    Heals ONLY: stuck-able state + stale since + readable pane that looks
    idle. Anything ambiguous leaves the state alone — a wrong heal would
    erase a genuinely-running session's working indicator everywhere."""
    if state not in STUCK_STATES or not str(since).isdigit():
        return "leave"
    if now - int(since) <= STUCK_AFTER_SECS:
        return "leave"                  # fresh enough — hook is clearly alive
    if in_mode:
        return "leave"                  # scrolled back: pane content unjudgeable
    if BUSY_MARKER in pane:
        return "leave"                  # long tool call / streaming: genuinely working
    if DIALOG_ROW.search(pane):
        return "leave"                  # a REAL prompt is on screen — the state is true
    if IDLE_MARKER not in pane:
        return "leave"                  # unrecognized content: be conservative
    return "heal"


def compact_pct(pane):
    """Extract the live compaction progress % from a captured pane — the TUI's
    '✶ Compacting conversation… (2m 1s) ▰▰▱ NN%' bar (the % sits on the bar line,
    which follows the 'Compacting…' line). Pure → unit-tested. None if absent."""
    i = pane.find("Compacting")
    if i < 0:
        return None
    m = re.search(r"(\d{1,3})\s*%", pane[i:i + 200])   # the next % after 'Compacting' = the bar
    if not m:
        return None
    v = int(m.group(1))
    return v if 0 <= v <= 100 else None


def _heal(name, since0, now):
    """Mirror the hook's Stop branch (and the chat view's markPaneIdle): state +
    since + emoji + the states/<sid>.jsonl transition the timeline reads — so
    the chip, the work-bar, and the tab dot all recover in one write. Re-reads
    `since` immediately before writing so a real hook event mid-sweep wins."""
    def show(var):
        return subprocess.run(["tmux", "show", "-t", name, "-v", var],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    try:
        if show("@claude-state-since") != str(since0):
            return False                # a newer event fired — its verdict stands
        subprocess.run(["tmux", "set", "-t", name, "@claude-state", "waiting", ";",
                        "set", "-t", name, "@claude-state-since", str(now), ";",
                        "set", "-t", name, "@romp-emoji", "🔵"],
                       capture_output=True, timeout=5)
        sid = show("@romp-session-id")
        if sid:
            sdir = STATE / "states"
            sdir.mkdir(parents=True, exist_ok=True)
            with open(sdir / (sid + ".jsonl"), "a") as f:
                f.write('{"t":%d,"state":"waiting"}\n' % now)
        return True
    except Exception:
        return False


def sweep():
    """One pass. Sets ⚪ on any READY romp session quiet > STALE_AFTER_SECS, only
    where it differs (no needless churn). Returns the pair `(alive, compacting)`
    the daemon loop unpacks: alive is False only when there are NO romp sessions,
    so the loop can exit; compacting picks the fast cadence while a session
    compacts. A tmux hiccup (list-sessions past its timeout, a spawn that raises)
    returns (True, False): the daemon keeps its normal cadence and retries on the
    next sweep instead of dying on a transient error."""
    now = int(time.time())
    try:
        p = subprocess.run(
            ["tmux", "list-sessions", "-F",
             "#{@romp}|#{session_name}|#{@claude-state}|#{@claude-state-since}|#{@romp-emoji}|#{pane_in_mode}"],
            capture_output=True, text=True, timeout=5)
    except Exception:
        return True, False       # alive, not compacting: the caller unpacks a pair
    any_romp = changed = compacting = False
    for line in (p.stdout or "").splitlines():
        f = line.split("|")
        if len(f) < 5 or f[0] != "1":
            continue
        any_romp = True
        name, state, since, emoji = f[1], f[2], f[3], f[4]
        in_mode = len(f) > 5 and f[5] == "1"
        # COMPACTING → publish the live % off the TUI bar into @claude-compact-pct, which the timeline
        # badge + chat chip read (the UI only shows it while state==compacting, so no clearing needed).
        if state == "compacting":
            compacting = True
            try:
                pane = subprocess.run(["tmux", "capture-pane", "-p", "-t", name],
                                      capture_output=True, text=True, timeout=5).stdout
                pct = compact_pct(pane)
            except Exception:
                pct = None
            if pct is not None:
                try:
                    subprocess.run(["tmux", "set", "-t", name, "@claude-compact-pct", str(pct)],
                                   capture_output=True, timeout=5)
                    changed = True
                except Exception:
                    pass
        # stuck-working heal — staleness gate first so we only capture-pane on
        # the rare candidate, never on every working session each sweep
        if state in STUCK_STATES and since.isdigit() and now - int(since) > STUCK_AFTER_SECS:
            try:
                pane = subprocess.run(["tmux", "capture-pane", "-p", "-t", name],
                                      capture_output=True, text=True, timeout=5).stdout
            except Exception:
                pane = BUSY_MARKER          # capture failed → treat as busy (leave)
            if diagnose(state, since, now, in_mode, pane) == "heal" and _heal(name, since, now):
                changed = True
            continue
        if state not in FADEABLE or not since.isdigit():
            continue
        if now - int(since) <= STALE_AFTER_SECS or emoji == DOT_INACTIVE:
            continue
        try:
            subprocess.run(["tmux", "set", "-t", name, "@romp-emoji", DOT_INACTIVE],
                           capture_output=True, timeout=5)
            changed = True
        except Exception:
            pass
    if changed:   # nudge an immediate title repaint (else it waits for status-interval)
        try:
            subprocess.run(["tmux", "refresh-client", "-S"], capture_output=True, timeout=5)
        except Exception:
            pass
    return any_romp, compacting


def _running():
    try:
        os.kill(int(PIDFILE.read_text().strip()), 0)
        return True
    except Exception:
        return False


def daemon():
    STATE.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))
    try:
        while True:
            alive, compacting = sweep()
            if not alive:         # exits as soon as a sweep finds no romp sessions
                break
            time.sleep(COMPACT_INTERVAL if compacting else INTERVAL)   # fast cadence for a live compact %
    finally:
        try:
            PIDFILE.unlink()
        except Exception:
            pass


def ensure():
    if _running():
        return
    # double-fork into a detached daemon (no controlling terminal) so it survives
    # the short-lived hook that spawns it.
    if os.fork() > 0:
        return
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0); os.dup2(devnull, 1); os.dup2(devnull, 2)
    daemon()
    os._exit(0)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--ensure":
        ensure()
    elif arg == "--once":
        sweep()
    else:
        if _running():
            return
        daemon()


if __name__ == "__main__":
    main()
