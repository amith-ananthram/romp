#!/usr/bin/env bats

# romp-postal-context.sh is a SessionStart hook: in a romp session (one launched with
# ROMP_SID in its environment) it emits a COMPACT pointer to the postal capability as
# additionalContext.
# The full norms live in the romp-postal skill (loaded on demand) + the postal MCP
# tools' own descriptions, so this pointer stays small and re-cheap every turn. It
# must be silent outside a romp session, and must never fail the turn.
# Two gates. ROMP_SID in the environment (the kernel sets it on every CLI it spawns), and the
# CLI's own id, from the payload's session_id, naming the session ROMP_SID names: the romp sid
# itself (a fresh spawn or a born fork, whose CLI the kernel pins to the sid) or the SDK
# registry's lastSid for it (the conversation a resume continued). Every process a session's
# Bash tool runs inherits ROMP_SID, so a `claude -p` a session spawned used to pass the first
# gate and take this pointer as its own; its id is in neither place, and the hook stays silent.

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME"
    # The SDK registry the hook reads lives under the state root as sdk/<ROMP_SID>.json; ROMP_STATE_DIR
    # wins over XDG_STATE_HOME when set, so a developer's override is cleared.
    unset ROMP_STATE_DIR
    export XDG_STATE_HOME="$TEST_DIR/state"
    # The first gate is ROMP_SID in the hook's environment; a developer running the suite from inside
    # a romp session must not pass the silent case by accident, so it is cleared here and set per
    # test. CLAUDE_CODE_SESSION_ID stands in for a payload without a session_id and would leak a
    # developer's own id into the no-id cases.
    unset ROMP_SID CLAUDE_CODE_SESSION_ID
    SID="11111111-2222-3333-4444-555555555555"     # the romp sid: ROMP_SID, and a fresh spawn's CLI id
    FSID="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"    # the conversation the reg's lastSid names (a resume)
    OTHER="99999999-8888-7777-6666-555555555555"   # an id in neither place: a child the session ran, or a rotation the reg has not recorded
    HOOK="$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-postal-context.sh"
}

teardown() { rm -rf "$TEST_DIR"; }

# the SDK registry row for ROMP_SID with the given lastSid (json.dumps spacing, as write_reg writes it)
write_reg() {
    mkdir -p "$XDG_STATE_HOME/romp/sdk"
    printf '{"sid": "%s", "name": "web", "cwd": "/tmp/notes-api", "mode": "acceptEdits", "lastSid": "%s", "alive": true}' \
        "$SID" "$1" > "$XDG_STATE_HOME/romp/sdk/$SID.json"
}
# $1 the CLI's session_id, $2 the start's source (startup | resume | clear | compact)
payload() { printf '{"session_id":"%s","transcript_path":"/tmp/notes-api/t.jsonl","hook_event_name":"SessionStart","source":"%s"}' "$1" "$2"; }
# $1 the SessionStart payload on the hook's stdin
run_hook() { run bash -c 'printf "%s" "$1" | "$2"' _ "$1" "$HOOK"; }

@test "in a romp session it emits a compact postal pointer as additionalContext" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    [[ "$output" == *'"hookEventName": "SessionStart"'* ]]
    [[ "$output" == *'postal MCP tools'* ]]
    # the declare-your-intent norm is present up front — as send_message's REQUIRED `kind` parameter, never
    # the retired DELEGATE:/COORDINATE:/QUESTION: body prefix the hook used to teach beside it (two
    # instructions for one fact). The negative pin is deliberate: the prefix must not come back.
    [[ "$output" == *'set `kind` to delegate, coordinate, or question'* ]]
    [[ "$output" != *'DELEGATE'* ]]
    [[ "$output" == *'list_agents'* ]]     # the coordinate-before-editing norm is present up front
    [[ "$output" == *'romp-postal skill'* ]]   # points to the full guide, not inlined
}

@test "a fresh spawn passes before the registry has learned its id" {
    # the kernel pins a fresh CLI's id to the romp sid (--session-id) and mints the reg with lastSid "";
    # the id reaches the reg only when the CLI's init lands at the kernel, which can be after this hook
    write_reg ""
    ROMP_SID="$SID" run_hook "$(payload "$SID" startup)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a born fork passes on the romp sid while the reg's lastSid is still the parent's conversation" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$SID" resume)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a CLI the session itself ran (an id in neither place) gets nothing" {
    # a `claude -p` from the session's Bash tool inherits ROMP_SID; its own session_id is a fresh uuid
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an empty lastSid lets an unrecorded id through: the init has not reached the kernel yet" {
    write_reg ""
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "a /clear or a compaction passes while the reg still holds the previous id" {
    # a live CLI rotates its id on /clear ahead of the kernel's lastSid write; neither is a child's first start
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" clear)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" compact)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}

@test "no reg for the sid: an id that is not the romp sid gets nothing, and the hook does not fail" {
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an unreadable reg is the same silence, never a failed turn" {
    mkdir -p "$XDG_STATE_HOME/romp/sdk"; printf 'not json' > "$XDG_STATE_HOME/romp/sdk/$SID.json"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "a payload without session_id: CLAUDE_CODE_SESSION_ID stands in, and with neither the hook is silent" {
    write_reg "$FSID"
    ROMP_SID="$SID" CLAUDE_CODE_SESSION_ID="$FSID" run_hook '{}'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
    ROMP_SID="$SID" run_hook '{}'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "outside a romp session (no ROMP_SID) it is silent" {
    write_reg "$FSID"
    run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an empty ROMP_SID is not a romp session either" {
    write_reg "$FSID"
    ROMP_SID="" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "the pointer is self-contained (no dependence on the skill file) and never fails" {
    # the hook no longer reads SKILL.md; it emits the same pointer regardless, so a
    # missing skill file can't blank it or fail the turn.
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" startup)"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}
