#!/usr/bin/env bash
set -euo pipefail

# Two membership paths (the same hook serves both session backends):
#   tmux     — the session lives in a tmux session tagged @romp; durable state
#              goes to states/<sid>.jsonl AND the tmux display vars
#              (@claude-state, @romp-emoji, …) that the status line / dashboard
#              / ghostty dot render.
#   headless — the launcher exported ROMP_SESSION_ID (no tmux); only the
#              durable states/<sid>.jsonl record is written. Every tmux-var
#              write below is display-only and skipped.
DISPLAY_TMUX=0
session_name=""
# A headless/SDK session that inherited a STRAY $TMUX (e.g. the manager was launched from INSIDE a tmux
# session, so every kernel + SDK child carries that $TMUX) would take the tmux branch below, resolve #S to
# whatever session is ATTACHED, and clobber ITS @romp-session-id + display state with this foreign session's
# fsid — flapping the attached session's anchor so the kernel can't see it live (the picker then offers a
# bogus "revive"). Claude Code's own CLAUDE_CODE_ENTRYPOINT tags an SDK launch (sdk-py / sdk-*); such a
# session is NEVER a tmux display session regardless of a leaked $TMUX — the SDK backend owns its identity
# and state via its own registry. (kernel/judge.py scrubs $TMUX from judge subprocesses for this same reason;
# the durable companion fix is to scrub it in the SDK session spawn / the manager env too.)
if [[ -n "${TMUX:-}" && "${CLAUDE_CODE_ENTRYPOINT:-}" != sdk* ]]; then
    session_name=$(tmux display-message -p '#S')
    # Only act on romp sessions — identified by the @romp flag, not the name.
    is_romp=$(tmux show -t "$session_name" -v @romp 2>/dev/null || true)
    [[ -n "$is_romp" ]] || exit 0
    DISPLAY_TMUX=1
elif [[ -n "${ROMP_SESSION_ID:-}" ]]; then
    session_name="${ROMP_SESSION_NAME:-$ROMP_SESSION_ID}"
else
    exit 0
fi

# Parse hook JSON with pure bash regex — no jq, no process spawns
input=$(cat)
[[ "$input" =~ \"hook_event_name\":\"([^\"]+)\" ]] && EVENT="${BASH_REMATCH[1]}" || EVENT=""
[[ "$input" =~ \"notification_type\":\"([^\"]+)\" ]] && NOTIF_TYPE="${BASH_REMATCH[1]}" || NOTIF_TYPE=""
[[ "$input" =~ \"cwd\":\"([^\"]+)\" ]] && WORK_DIR="${BASH_REMATCH[1]}" || WORK_DIR=""
[[ "$input" =~ \"source\":\"([^\"]+)\" ]] && SOURCE="${BASH_REMATCH[1]}" || SOURCE=""
# The LIVE transcript fsid for this event (Claude Code's own session UUID = the transcript filename). Used
# to re-anchor @romp-session-id across a /clear, which forks the transcript to a new fsid (see below).
[[ "$input" =~ \"session_id\":\"([^\"]+)\" ]] && HOOK_SID="${BASH_REMATCH[1]}" || HOOK_SID=""
# Current permission mode (default|plan|acceptEdits|auto|dontAsk|bypassPermissions).
# Not every event carries it — leave empty when absent so we never clobber a good
# value with "". Consumers use it to tell a GENUINE permission block from auto
# mode's transient permission notifications (which the classifier allows moments
# later) — an event-based replacement for the feed's old time-threshold debounce.
[[ "$input" =~ \"permission_mode\":\"([^\"]+)\" ]] && PERM_MODE="${BASH_REMATCH[1]}" || PERM_MODE=""
# PreCompact / PostCompact carry trigger = "manual" | "auto" (the CLI's own hook-input schema, verified on
# 2.1.257; it is also the matcher those events filter on). An AUTO compaction runs INSIDE an open turn that
# then continues; a MANUAL /compact is its own exchange and leaves the session idle.
[[ "$input" =~ \"trigger\":\"([^\"]+)\" ]] && TRIGGER="${BASH_REMATCH[1]}" || TRIGGER=""

case "$EVENT" in
    SessionStart)          state="waiting" ;;
    UserPromptSubmit)      state="working" ;;
    PostToolUse)           state="working" ;;
    Stop)                  state="waiting" ;;
    PreCompact)            state="compacting" ;;   # context compaction STARTED (manual /compact or auto)
    PostCompact)           # compaction done. MANUAL: the /compact was the whole exchange → idle for the next
                           # prompt. AUTO: it ran inside an open turn that now goes on → still working (until
                           # 2026-09-03 both wrote waiting, and the kernel read the continuing turn as quiet until
                           # the next PostToolUse re-corrected it — a parked op could fire into it)
        if [[ "$TRIGGER" == "auto" ]]; then state="working"; else state="waiting"; fi ;;
    Notification)
        case "$NOTIF_TYPE" in
            permission_prompt) state="permission" ;;
            idle_prompt)       state="idle" ;;
            *) exit 0 ;;
        esac ;;
    *) exit 0 ;;
esac

# Status emoji for the ghostty tab dot (tmux.conf set-titles-string reads
# @romp-emoji). Mirrors the dashboard's state→color: 🔵 ready (waiting/
# idle), 🟡 working, 🔴 needs input (permission). Updated here on every event
# so the dot tracks Claude's live status.
# Compacting is the ODD ONE OUT on purpose: a monochrome compress glyph (⇲, NOT
# a coloured dot) so a transient context-compaction reads as a PROCESS, not as
# another live-status colour (the user 2026-06-22).
case "$state" in
    working)      emoji="🟡" ;;
    permission)   emoji="🔴" ;;
    compacting)   emoji="⇲" ;;    # ⇲ context compacting (monochrome — not a status colour)
    *)            emoji="🔵" ;;   # waiting / idle
esac

now=$(date +%s)

# Append a state-transition log (only on an actual change) so the timeline can
# reconstruct HISTORICAL state intervals — e.g. how long a session sat AWAITING
# your input — not just the current @claude-state-since. One small JSONL per
# session; this is the DURABLE record (written on both backends — the tmux vars
# below are display only).
if [[ "$DISPLAY_TMUX" == 1 ]]; then
    sid=$(tmux show -t "$session_name" -v @romp-session-id 2>/dev/null || true)
    # RE-ANCHOR across a session-id change (the user 2026-07-06): a /clear, relaunch, or --resume in
    # this pane starts a NEW transcript fsid, but @romp-session-id was written ONCE at launch
    # (bin/romp) and never rewritten — so every consumer (kernel liveness/lanes/tabs, states file,
    # send routing, resume picker) kept keying on the STALE fsid while the live transcript was
    # invisible. On ANY event whose payload session_id differs from the stored anchor (not just
    # SessionStart — a missed start still heals on the next event), re-point the var to the live fsid
    # and MIRROR the names entry (name/cwd/color are anchor-keyed under names/<sid>) so every surface
    # resolves the live transcript. Event-based; a no-op when they already match. Fully guarded — a
    # failure here can never break status reporting. (Headless/SDK sessions are untouched: the SDK
    # backend owns identity via its registry's lastSid.)
    if [[ -n "$HOOK_SID" && -n "$sid" && "$HOOK_SID" != "$sid" ]]; then
        ndir="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/names"
        if [[ -f "$ndir/$sid" && ! -e "$ndir/$HOOK_SID" ]]; then
            cp "$ndir/$sid" "$ndir/$HOOK_SID" 2>/dev/null || true
        fi
        # Record the SUCCESSION this hook is the sole witness of (2026-08-13), BEFORE the var flip
        # makes the old fsid's departure observable to the kernel's death sweep: at the pane, a
        # /clear'd-or-resumed-away lane and a genuine death look identical (the sid vanishes from the
        # live scan while its name stays occupied) — this row is what tells them apart, vetoing the
        # death stamp so the episode machinery keeps owning supersessions. Second-shape row (no
        # "state" key, like the awaiting overlays), so every state-keyed reader already skips it.
        sdir="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/states"
        mkdir -p "$sdir" 2>/dev/null || true
        printf '{"t":%s,"supersededBy":"%s"}\n' "$(date +%s)" "$HOOK_SID" >> "$sdir/$sid.jsonl" 2>/dev/null || true
        tmux set -t "$session_name" @romp-session-id "$HOOK_SID" 2>/dev/null || true
        sid="$HOOK_SID"                                 # everything below (state log, etc.) uses the LIVE fsid now
    fi
    prev=$(tmux show -t "$session_name" -v @claude-state 2>/dev/null || true)
else
    sid="$ROMP_SESSION_ID"
    # No tmux var to diff against — read the last recorded state instead.
    prev=$(tail -1 "${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/states/$sid.jsonl" 2>/dev/null \
        | sed -n 's/.*"state":"\([^"]*\)".*/\1/p' || true)
fi
# Compaction is STICKY: once PreCompact set state=compacting, ONLY PostCompact ends it. A postal
# message (or any other hook) firing mid-compaction must NOT clobber @claude-state back to
# working/waiting — that split the timeline's compacting span and stopped the live % partway (the
# user). With this guard prev==state==compacting, so no spurious transition is logged and the span
# stays continuous.
if [[ "$prev" == "compacting" && "$EVENT" != "PostCompact" ]]; then
    state="compacting"; emoji="⇲"
fi
if [[ -n "$sid" && "$prev" != "$state" ]]; then
    sdir="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/states"
    mkdir -p "$sdir"
    printf '{"t":%s,"state":"%s"}\n' "$now" "$state" >> "$sdir/$sid.jsonl"
fi

# Everything below is DISPLAY: tmux vars, the ghostty tab dot, the /color
# push. A headless session has none of these surfaces.
[[ "$DISPLAY_TMUX" == 1 ]] || exit 0

# AWAITING overlay (tmux backend): at turn-end, CLEAR awaiting (false). A leftover `run_in_background`
# SHELL task — a dev server, a `tail -f`, a hung command the agent backgrounded and never reaped — is NOT
# awaiting-worthy work (the user 2026-07-07): it must not pin an idle, available session to a working flavor.
# Those tasks show in the #bg-tasks box but never drive status; only real SUBAGENTS (the SDK backend's live
# SubagentStart/Stop snapshot) leave an idle session 'working'. So we ignore the Stop payload's
# `background_tasks` and only clear a stale awaiting:true (transition-only, so the log stays lean).
if [[ "$EVENT" == "Stop" && -n "$sid" ]]; then
    sdir="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/states"
    prev_aw=$(grep -oE '"awaiting":(true|false)' "$sdir/$sid.jsonl" 2>/dev/null | tail -1 | sed 's/.*://' || true)
    if [[ "$prev_aw" == "true" ]]; then
        mkdir -p "$sdir"
        printf '{"t":%s,"awaiting":false}\n' "$now" >> "$sdir/$sid.jsonl"
    fi
fi

# Store session state for dashboard + tab dot — single tmux invocation
if [[ -n "$WORK_DIR" ]]; then
    tmux set -t "$session_name" @claude-state "$state" \;\
         set -t "$session_name" @claude-state-since "$now" \;\
         set -t "$session_name" @romp-emoji "$emoji" \;\
         set -t "$session_name" @claude-dir "$WORK_DIR"
else
    tmux set -t "$session_name" @claude-state "$state" \;\
         set -t "$session_name" @claude-state-since "$now" \;\
         set -t "$session_name" @romp-emoji "$emoji"
fi

# Publish the permission mode for the feed's block detection (see PERM_MODE
# above). Only when this event actually carried it — an unconditional set would
# write "" on the events that omit the field and erase the last known mode.
if [[ -n "$PERM_MODE" ]]; then
    tmux set -t "$session_name" @claude-permission-mode "$PERM_MODE"
fi

# Clear the transient "←/→ peer:" top-line message prefix when a NORMAL prompt
# starts — but KEEP it when the prompt is an injected peer-message banner (which
# carries a long "####…" rule), so the label rides along with that message's turn.
# (Set by scripts/romp-postal-service _set_msg_prefix; rendered by status-right.)
if [[ "$EVENT" == "UserPromptSubmit" && "$input" != *"####################"* ]]; then
    tmux set -t "$session_name" @romp-msg-dir "" \;\
         set -t "$session_name" @romp-msg-peer "" \;\
         set -t "$session_name" @romp-msg-id-cur "" || true
fi

# On a FRESH session start, push Claude's /color so the pill (approximately)
# matches the romp identity color. Only source=startup: on resume Claude is still
# loading the transcript and the keystrokes get dropped, and the resumed session
# restores its own color anyway. The name is handled separately (--name at launch
# / resume, and /rename on the after-rename hook). No-op for non-romp sessions.
if [[ "$EVENT" == "SessionStart" && "$SOURCE" == "startup" ]]; then
    # Resolve romp via PATH, falling back to the repo this hook lives in
    # (readlink -f follows the ~/.claude/hooks symlink back to romp/hooks/).
    ROMP_BIN="$(command -v romp || true)"
    if [[ -z "$ROMP_BIN" ]]; then
        SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
        ROMP_BIN="$(dirname "$SELF")/../bin/romp"
    fi
    "$ROMP_BIN" _color "$session_name" 2>/dev/null || true
fi
