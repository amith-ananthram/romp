#!/usr/bin/env bash
# SessionStart hook (romp): tell a romp session it can message peers, in a COMPACT
# pointer, not the full skill. The old hook emitted the entire SKILL.md body every
# session (~1.4k tokens, re-sent every turn) and duplicated the postal MCP tools'
# own instructions + descriptions. Now it emits only the essentials the session
# needs up front, and defers the full guide (shell CLI, remote-machine setup,
# coordination detail) to the romp-postal skill, loaded on demand. A plain Claude
# Code session gets nothing: a romp session is exactly one launched with ROMP_SID
# in its environment (the kernel sets it on the CLI it spawns), so that is the
# first gate. Keep this in sync with SKILL.md.
[ -n "${ROMP_SID:-}" ] || exit 0
# The second gate: THIS CLI must be the session ROMP_SID names, not a process that inherited
# the variable. Everything a session's Bash tool runs carries its ROMP_SID, so a `claude -p`
# a session spawned used to pass the first gate and take this pointer as its own context (a
# review finding on the tmux backend's removal, 2026-09-11). The CLI names itself in the
# SessionStart payload's session_id (CLAUDE_CODE_SESSION_ID stands in for a payload without
# one), and the check reads the SDK registry's row for the sid (sdk/<ROMP_SID>.json under the
# state root), whose lastSid is the conversation the kernel last saw the CLI on. Three rules,
# by the start's source:
#   startup        -> the id is the sid, or is lastSid, or lastSid is empty;
#   clear          -> the reg exists (a readable JSON file); the id is not checked;
#   anything else  -> the id is the sid, or is lastSid (resume, compact).
# The id IS the sid for a fresh spawn and for a born-as-a-fork copy: the kernel pins the CLI to
# the sid with --session-id (kernel/sdk_backend.py, SdkBackend._options: the session_id kwarg
# on both arms). The id is lastSid for the conversation a resume continued, and for a
# compaction, which keeps the CLI's id. The registry learns a CLI's id only when the CLI's init
# message reaches the kernel (sdk_backend.py, SdkSession._on_message: on the init SystemMessage,
# `fsid = d.get("session_id")`, then `self.backend._update_reg(self.sid, lastSid=fsid)` when it
# differs from resume_sid), AFTER the CLI is up, and both allowances are for a start this hook
# sees BEFORE that write:
#   - SdkBackend.spawn mints the reg with `"lastSid": ""` before the launch, so a first `startup`
#     can find it empty; an empty lastSid lets a `startup` through and nothing else. A `claude -p`
#     child's own first start is a `startup` too, but by then the reg holds the session's id and
#     the child's fresh uuid fails the match.
#   - A /clear rotates the CLI onto a NEW id, and the `clear` SessionStart runs before the init
#     that carries that id reaches the kernel: the flip in _on_message (the one that ends the
#     kernel's clearing bracket, its `clearing` branch) lands after this hook has run, so at hook
#     time the reg still holds the PREVIOUS id, deterministically, on every /clear. The previous
#     cut required the match here and lost the pointer on every /clear in a romp session (a
#     review finding, 2026-09-11). The source is enough because nothing a session runs from its
#     Bash tool ever fires a `clear`: a child's own start is a `startup` and its mid-run
#     compaction comes back as a `compact` under the same id. So a `clear` in a process carrying
#     ROMP_SID whose reg exists is this session's own CLI. The reg is still read (the same read
#     the other sources make), and a missing or unreadable one exits 0.
# `compact` keeps the match on purpose: a `claude -p` child that auto-compacted mid-run came back
# as a `compact` start with its own id and took the pointer when the source alone passed. No id,
# no reg, or an unreadable reg: nothing to check against, so the hook does nothing (exit 0, never
# loud). The gate is kept verbatim in romp-postal-ensure.sh.
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
read -r -d '' CTX <<'TXT'
You're in a romp session with sibling sessions you can message: use the postal MCP tools (send_message, list_agents, set_working, check_inbox, check_sent, recall_message) or `romp mail`. Each tool's description carries its norms. Two to know up front:
- Message a peer only for something substantive (it wakes them and costs a turn); set `kind` to delegate, coordinate, or question, and put the whole point in the first sentence.
- BEFORE editing shared files, run list_agents and check peers' branches + working-notes to avoid collisions (overlap only collides on the same branch); publish yours with set_working.
For the full guide (shell CLI, remote-machine tunnel setup, coordination detail), invoke the romp-postal skill.
TXT
python3 - "$CTX" <<'PY'
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": sys.argv[1]}}))
PY
