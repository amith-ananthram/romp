#!/usr/bin/env bats

# hooks/romp-postal-revive.sh is the sync SessionStart hook that, on a resume or fresh start with
# unread mail waiting (the on-disk mail-pending/<sid> marker), asks the bus to force-deliver it
# (`romp-postal-service wake --id <sid>`). Its gate is ROMP_SID in the hook's environment: the kernel
# sets it on every CLI it spawns, so a romp session is exactly a session that has it, and a plain
# Claude Code session must get a silent exit with no bus call. These tests drive the real hook with a
# stub romp-postal-service beside it (the hook resolves ../bin from its own real path).

setup() {
    TEST_DIR="$(mktemp -d)"
    unset ROMP_STATE_DIR
    export XDG_STATE_HOME="$TEST_DIR/state"
    # HOME under the test dir: the hook's off-switch is $HOME/.claude/romp-postal-off.
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME/.claude"
    unset ROMP_SUMMARIZING ROMP_SID
    mkdir -p "$TEST_DIR/hooks" "$TEST_DIR/bin"
    cp "$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-postal-revive.sh" "$TEST_DIR/hooks/"
    HOOK="$TEST_DIR/hooks/romp-postal-revive.sh"
    export CALL_LOG="$TEST_DIR/calls.log"
    cat > "$TEST_DIR/bin/romp-postal-service" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALL_LOG"
exit 0
STUB
    chmod +x "$TEST_DIR/bin/romp-postal-service"
    SID="11111111-2222-3333-4444-555555555555"
    PENDING="$XDG_STATE_HOME/romp/postal/mail-pending"
}

teardown() { rm -rf "$TEST_DIR"; }

mark_pending() { mkdir -p "$PENDING"; : > "$PENDING/$SID"; }

# $1 the hook payload's `source` (resume | startup | clear | compact)
run_hook() { run bash -c 'printf "%s" "$1" | "$2"' _ "{\"session_id\":\"$SID\",\"source\":\"$1\"}" "$HOOK"; }

@test "with ROMP_SID set, a resume with mail pending asks the bus to wake this session" {
    mark_pending
    ROMP_SID="$SID" run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    grep -qx "wake --id $SID" "$CALL_LOG"
}

@test "a fresh startup with mail pending wakes too" {
    mark_pending
    ROMP_SID="$SID" run_hook startup
    [ "$status" -eq 0 ]
    grep -qx "wake --id $SID" "$CALL_LOG"
}

@test "without ROMP_SID (a plain Claude Code session) it exits silently, mail pending or not" {
    mark_pending
    run_hook resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -f "$CALL_LOG" ]
}

@test "an empty ROMP_SID is not a romp session" {
    mark_pending
    ROMP_SID="" run_hook resume
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
}

@test "nothing pending: no bus call even in a romp session" {
    ROMP_SID="$SID" run_hook resume
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
}

@test "a /clear or a compaction never pokes a live session" {
    mark_pending
    ROMP_SID="$SID" run_hook clear
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
    ROMP_SID="$SID" run_hook compact
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
}

@test "the off switch wins over everything" {
    mark_pending
    : > "$HOME/.claude/romp-postal-off"
    ROMP_SID="$SID" run_hook resume
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
}
