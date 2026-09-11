#!/usr/bin/env bats

# hooks/romp-postal-ensure.sh is the SessionStart hook that starts the postal bus for a romp session.
# Its gate is ROMP_SID in the hook's environment: the kernel sets it on every CLI it spawns, so a romp
# session is exactly a session that has it, and a plain Claude Code session (no ROMP_SID) must get a
# silent exit with no bus started. These tests drive the real hook with a stub romp-postal-service
# beside it (the hook resolves ../bin from its own real path) and read what the stub was asked.

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME"
    # The summarizer guard exits the hook first; a suite run from inside a summarizing shell would
    # otherwise pass the silent case for the wrong reason. Same for a developer's own ROMP_SID.
    unset ROMP_SUMMARIZING ROMP_SID
    mkdir -p "$TEST_DIR/hooks" "$TEST_DIR/bin"
    cp "$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-postal-ensure.sh" "$TEST_DIR/hooks/"
    HOOK="$TEST_DIR/hooks/romp-postal-ensure.sh"
    export CALL_LOG="$TEST_DIR/calls.log"
    cat > "$TEST_DIR/bin/romp-postal-service" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALL_LOG"
exit 0
STUB
    chmod +x "$TEST_DIR/bin/romp-postal-service"
    SID="11111111-2222-3333-4444-555555555555"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "with ROMP_SID set the hook asks the bus to ensure itself" {
    ROMP_SID="$SID" run bash -c 'echo "{}" | "$1"' _ "$HOOK"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ -f "$CALL_LOG" ]
    grep -qx 'ensure' "$CALL_LOG"
}

@test "without ROMP_SID (a plain Claude Code session) it exits silently and starts nothing" {
    run bash -c 'echo "{}" | "$1"' _ "$HOOK"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -f "$CALL_LOG" ]
}

@test "an empty ROMP_SID is not a romp session" {
    ROMP_SID="" run bash -c 'echo "{}" | "$1"' _ "$HOOK"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -f "$CALL_LOG" ]
}

@test "the summarizer guard still wins over ROMP_SID" {
    ROMP_SUMMARIZING=1 ROMP_SID="$SID" run bash -c 'echo "{}" | "$1"' _ "$HOOK"
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
}
