#!/usr/bin/env bash
# romp-postal-ensure.sh — SessionStart hook: make sure the Romp Postal Service
# bus is running, for romp sessions. No-op for non-romp sessions. Registered
# async so it never delays session start; the bus is a singleton (started once,
# shared by every romp session, and self-stops when the last one closes).

set -uo pipefail

[[ -n "${ROMP_SUMMARIZING:-}" ]] && exit 0
# romp sessions only: a romp session is exactly one launched with ROMP_SID in its
# environment (the kernel sets it on the CLI it spawns); a plain Claude Code session
# has no peers and no bus to start.
[[ -n "${ROMP_SID:-}" ]] || exit 0
# ... and only THIS session's CLI, not a process that inherited its ROMP_SID: a `claude -p` the
# session's Bash tool ran used to pass the gate above and ensure the bus as if it were the session
# (a review finding on the tmux backend's removal, 2026-09-11). The CLI's own id (the payload's
# session_id, else CLAUDE_CODE_SESSION_ID) must be the romp sid (a fresh spawn or a born fork: the
# kernel pins the CLI's id to the sid, kernel/sdk_backend.py SdkBackend._options) or the SDK
# registry's lastSid for the sid (a resumed conversation). The kernel writes lastSid only when the
# CLI's init lands (SdkSession._on_message), after this hook may already have run, so an EMPTY
# lastSid passes; a /clear or a compaction rotates a live CLI's id ahead of the same write and is
# never a child's first start, so those sources pass. No id, no reg or an unreadable reg: nothing to
# check, nothing done. Verbatim the gate in romp-postal-context.sh, which carries the full story.
input="$(cat)"
if [[ "$input" =~ \"session_id\":[[:space:]]*\"([^\"]+)\" ]]; then cli_id="${BASH_REMATCH[1]}"
else cli_id="${CLAUDE_CODE_SESSION_ID:-}"; fi
[[ -n "$cli_id" ]] || exit 0
[[ "$input" =~ \"source\":[[:space:]]*\"([^\"]+)\" ]] && start_kind="${BASH_REMATCH[1]}" || start_kind=""
if [[ "$cli_id" != "$ROMP_SID" ]]; then
    case "$start_kind" in
        clear|compact) ;;   # a live CLI rotating its id, never a child's first start
        *)
            reg="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/sdk/$ROMP_SID.json"
            last_sid="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("lastSid") or "")' "$reg" 2>/dev/null)" || exit 0
            [[ -z "$last_sid" || "$last_sid" == "$cli_id" ]] || exit 0
            ;;
    esac
fi

src="${BASH_SOURCE[0]}"
while [[ -L "$src" ]]; do
    tgt="$(readlink "$src")"
    case "$tgt" in
        /*) src="$tgt" ;;
        *)  src="$(cd "$(dirname "$src")" && pwd)/$tgt" ;;
    esac
done
postal="$(cd "$(dirname "$src")/../bin" 2>/dev/null && pwd)/romp-postal-service"
[[ -x "$postal" ]] || exit 0

"$postal" ensure >/dev/null 2>&1
exit 0
