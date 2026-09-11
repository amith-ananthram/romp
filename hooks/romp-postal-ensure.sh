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
# session_id, else CLAUDE_CODE_SESSION_ID) is checked against the romp sid (a fresh spawn or a born
# fork: the kernel pins the CLI's id to the sid, kernel/sdk_backend.py SdkBackend._options) and the
# SDK registry's lastSid for the sid (the conversation the kernel last saw the CLI on), by the
# start's source:
#   startup        -> the id is the sid, or is lastSid, or lastSid is empty;
#   clear          -> the reg exists (a readable JSON file); the id is not checked;
#   anything else  -> the id is the sid, or is lastSid (resume, compact).
# The kernel writes lastSid only when the CLI's init lands (SdkSession._on_message: `fsid =
# d.get("session_id")` on the init, then `_update_reg(self.sid, lastSid=fsid)`), and both allowances
# are for a start that runs ahead of that write: SdkBackend.spawn mints the reg with `"lastSid": ""`
# before the launch, so a first `startup` may read it empty; and a /clear rotates the CLI onto a new
# id whose init reaches the kernel AFTER the `clear` SessionStart has run, so the reg still holds the
# previous id at hook time, on every /clear (requiring the match there lost the bus ensure on every
# /clear, a review finding 2026-09-11). Nothing a session runs from its Bash tool fires a `clear` (a
# child's own start is a `startup`, its compaction a `compact` under the same id), so the source is
# enough there, and `compact` keeps the match: a `claude -p` child that auto-compacted mid-run used
# to pass as a `compact` start on the source alone and ensure the bus. No id, no reg or an unreadable
# reg: nothing to check, nothing done. Verbatim the gate in romp-postal-context.sh, which carries the
# full story.
input="$(cat)"
if [[ "$input" =~ \"session_id\":[[:space:]]*\"([^\"]+)\" ]]; then cli_id="${BASH_REMATCH[1]}"
else cli_id="${CLAUDE_CODE_SESSION_ID:-}"; fi
[[ -n "$cli_id" ]] || exit 0
[[ "$input" =~ \"source\":[[:space:]]*\"([^\"]+)\" ]] && start_kind="${BASH_REMATCH[1]}" || start_kind=""
if [[ "$cli_id" != "$ROMP_SID" ]]; then
    reg="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/sdk/$ROMP_SID.json"
    last_sid="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("lastSid") or "")' "$reg" 2>/dev/null)" || exit 0
    if [[ "$start_kind" == "clear" ]]; then :                                    # the reg exists; its lastSid is still the previous id
    elif [[ -z "$last_sid" ]]; then [[ "$start_kind" == "startup" ]] || exit 0   # a first start, ahead of the kernel's write
    else [[ "$last_sid" == "$cli_id" ]] || exit 0; fi
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
