#!/usr/bin/env bats

# romp-postal-context.sh is a SessionStart hook: in a romp session (one launched with
# ROMP_SID in its environment) it emits a COMPACT pointer to the postal capability as
# additionalContext.
# The full norms live in the romp-postal skill (loaded on demand) + the postal MCP
# tools' own descriptions, so this pointer stays small and re-cheap every turn. It
# must be silent outside a romp session, and must never fail the turn.

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME"
    # The gate is ROMP_SID in the hook's environment (the kernel sets it on every CLI it
    # spawns); a developer running the suite from inside a romp session must not pass the
    # silent case by accident, so it is cleared here and set per test.
    unset ROMP_SID
    SID="11111111-2222-3333-4444-555555555555"
    HOOK="$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-postal-context.sh"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "in a romp session it emits a compact postal pointer as additionalContext" {
    ROMP_SID="$SID" run bash -c 'echo "{}" | "'"$HOOK"'"'
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

@test "outside a romp session (no ROMP_SID) it is silent" {
    run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "an empty ROMP_SID is not a romp session either" {
    ROMP_SID="" run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    [ -z "$output" ]
}

@test "the pointer is self-contained (no dependence on the skill file) and never fails" {
    # the hook no longer reads SKILL.md; it emits the same pointer regardless, so a
    # missing skill file can't blank it or fail the turn.
    ROMP_SID="$SID" run bash -c 'echo "{}" | "'"$HOOK"'"'
    [ "$status" -eq 0 ]
    [[ "$output" == *'"additionalContext"'* ]]
}
