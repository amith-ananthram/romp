#!/usr/bin/env bash
# SessionStart hook (romp): tell a romp session it can message peers, in a COMPACT
# pointer, not the full skill. The old hook emitted the entire SKILL.md body every
# session (~1.4k tokens, re-sent every turn) and duplicated the postal MCP tools'
# own instructions + descriptions. Now it emits only the essentials the session
# needs up front, and defers the full guide (shell CLI, remote-machine setup,
# coordination detail) to the romp-postal skill, loaded on demand. A plain Claude
# Code session gets nothing: a romp session is exactly one launched with ROMP_SID
# in its environment (the kernel sets it on the CLI it spawns), so that is the
# gate. Keep this in sync with SKILL.md.
[ -n "${ROMP_SID:-}" ] || exit 0
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
