#!/usr/bin/env bats

# hooks/romp-postal-ensure.sh is the SessionStart hook that starts the postal bus for a romp session.
# Two gates: ROMP_SID in the hook's environment (the kernel sets it on every CLI it spawns, so a plain
# Claude Code session, with none, gets a silent exit and no bus), and the CLI's own id in the payload
# naming the session ROMP_SID names: the romp sid itself (a fresh spawn or a born fork, whose CLI the
# kernel pins to the sid) or the SDK registry's lastSid for it (a resumed conversation). Every process
# a session's Bash tool runs inherits ROMP_SID, so a `claude -p` a session spawned used to ensure the
# bus as if it were the session; its id is in neither place, and the hook starts nothing for it. The
# start's source changes one thing only: an EMPTY lastSid (the reg as SdkBackend.spawn mints it, before
# the CLI's init has flipped the field) passes a `startup` and nothing else, so a child that
# auto-compacted mid-run and came back as a `compact` start with its own id starts nothing either. These
# tests drive the real hook with a stub romp-postal-service beside it (the hook resolves ../bin from
# its own real path) and read what the stub was asked.

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME"
    # The SDK registry the hook reads lives under the state root as sdk/<ROMP_SID>.json; ROMP_STATE_DIR
    # wins over XDG_STATE_HOME when set, so a developer's override is cleared.
    unset ROMP_STATE_DIR
    export XDG_STATE_HOME="$TEST_DIR/state"
    # The summarizer guard exits the hook first; a suite run from inside a summarizing shell would
    # otherwise pass the silent case for the wrong reason. Same for a developer's own ROMP_SID, and for
    # CLAUDE_CODE_SESSION_ID, which stands in for a payload without a session_id.
    unset ROMP_SUMMARIZING ROMP_SID CLAUDE_CODE_SESSION_ID
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
    SID="11111111-2222-3333-4444-555555555555"     # the romp sid: ROMP_SID, and a fresh spawn's CLI id
    FSID="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"    # the conversation the reg's lastSid names (a resume)
    OTHER="99999999-8888-7777-6666-555555555555"   # an id in neither place: a child the session ran (its first start, or its compaction mid-run)
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

@test "with ROMP_SID set and the CLI's id the reg's lastSid, the hook asks the bus to ensure itself" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ -f "$CALL_LOG" ]
    grep -qx 'ensure' "$CALL_LOG"
}

@test "a fresh spawn ensures the bus before the reg has learned its id: the CLI's id is the romp sid" {
    write_reg ""
    ROMP_SID="$SID" run_hook "$(payload "$SID" startup)"
    [ "$status" -eq 0 ]
    grep -qx 'ensure' "$CALL_LOG"
}

@test "a CLI the session itself ran (an id in neither place) starts nothing" {
    # a `claude -p` from the session's Bash tool inherits ROMP_SID; its own session_id is a fresh uuid
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -f "$CALL_LOG" ]
}

@test "a compaction or a /clear with an id the reg does not hold starts nothing: the source alone is no pass" {
    # a `claude -p` the session's Bash tool ran auto-compacts mid-run and starts again as a `compact` with its
    # own id; the previous cut let clear and compact through without reading the reg, and the child ensured the bus
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" compact)"
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" clear)"
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
}

@test "a compact start on the reg's lastSid ensures: a compaction keeps the CLI's id" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$FSID" compact)"
    [ "$status" -eq 0 ]
    grep -qx 'ensure' "$CALL_LOG"
}

@test "a /clear start on the romp sid ensures" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook "$(payload "$SID" clear)"
    [ "$status" -eq 0 ]
    grep -qx 'ensure' "$CALL_LOG"
}

@test "an empty lastSid lets an unrecorded id ensure at startup only" {
    # the reg as SdkBackend.spawn mints it, before the init's flip fills lastSid: only a first start reads it empty
    write_reg ""
    ROMP_SID="$SID" run_hook "$(payload "$FSID" compact)"
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
    ROMP_SID="$SID" run_hook "$(payload "$FSID" startup)"
    [ "$status" -eq 0 ]
    grep -qx 'ensure' "$CALL_LOG"
}

@test "no reg for the sid and an id that is not the romp sid: nothing starts, and the hook does not fail" {
    ROMP_SID="$SID" run_hook "$(payload "$OTHER" startup)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -f "$CALL_LOG" ]
}

@test "a payload without session_id: CLAUDE_CODE_SESSION_ID stands in, and with neither nothing starts" {
    write_reg "$FSID"
    ROMP_SID="$SID" run_hook '{}'
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
    ROMP_SID="$SID" CLAUDE_CODE_SESSION_ID="$FSID" run_hook '{}'
    [ "$status" -eq 0 ]
    grep -qx 'ensure' "$CALL_LOG"
}

@test "without ROMP_SID (a plain Claude Code session) it exits silently and starts nothing" {
    write_reg "$FSID"
    run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -f "$CALL_LOG" ]
}

@test "an empty ROMP_SID is not a romp session" {
    write_reg "$FSID"
    ROMP_SID="" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -f "$CALL_LOG" ]
}

@test "the summarizer guard still wins over ROMP_SID" {
    write_reg "$FSID"
    ROMP_SUMMARIZING=1 ROMP_SID="$SID" run_hook "$(payload "$FSID" resume)"
    [ "$status" -eq 0 ]
    [ ! -f "$CALL_LOG" ]
}
