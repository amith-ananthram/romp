#!/usr/bin/env bats

# `romp sessions [--json]`: the sessions' live state for scripts, read from the kernel.
#
# It exists because the only machine-readable source of per-session state and identity colour
# used to be the tmux session table (@claude-state, @identity-bg/@identity-fg) of the terminal
# backend removed on 2026-09-11. SDK sessions never entered tmux, so once they became the default
# `tmux list-sessions` returned nothing and every external consumer silently degraded to empty
# output. The kernel owns this state for BOTH backends (Claude Code and Codex), so that is what
# this reads, and a dead kernel must fail loudly rather than print an empty list, which would read
# as "no sessions" and hide exactly the breakage this command ends.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    TEST_DIR="$(mktemp -d)"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'TESTTOKEN123\n' > "$XDG_STATE_HOME/romp/serve-token"
    export ROMP_KERNEL_PORT=29855

    # Stub curl: records the request, replies with a two-session fleet. Nothing touches a real
    # kernel, so the suite is safe to run beside a live one.
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export CURL_LOG="$TEST_DIR/curl.log"
    export FLEET_JSON="$TEST_DIR/fleet.json"
    cat > "$FLEET_JSON" <<'JSON'
{"sessions": [
  {"id": "11111111-2222-3333-4444-555555555555", "name": "web", "state": "working",
   "dir": "/tmp/notes-api", "bg": "#1EA1EB", "fg": "black", "backend": "sdk", "working": ""},
  {"id": "66666666-7777-8888-9999-000000000000", "name": "api", "state": "waiting",
   "dir": "/tmp/notes-api", "bg": "#54B204", "fg": "black", "backend": "codex", "working": ""}
]}
JSON
    export CURL_STDIN="$TEST_DIR/curl.stdin"
    # Record argv AND stdin: the serve token travels on stdin now (curl --config -), so a mock that
    # only watched argv could not tell a working auth header from no header at all. Read stdin
    # BEFORE writing the response, or the config never lands.
    cat > "$MOCK/curl" <<'MOCK'
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
cat >> "$CURL_STDIN" 2>/dev/null
[ -n "${CURL_FAIL:-}" ] && exit 22
cat "$FLEET_JSON"
MOCK
    chmod +x "$MOCK/curl"
    export PATH="$MOCK:$PATH"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp sessions: prints a line per session with state and backend" {
    run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 0 ]
    [[ "$output" == *"web"* ]]
    [[ "$output" == *"working"* ]]
    [[ "$output" == *"api"* ]]
    [[ "$output" == *"waiting"* ]]
}

@test "romp sessions: covers BOTH backends (Claude Code and Codex), the point of reading the kernel" {
    run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 0 ]
    [[ "$output" == *"sdk"* ]]
    [[ "$output" == *"codex"* ]]
}

@test "romp sessions --json: emits the kernel's rows verbatim, colours included" {
    run "$ROMP_SCRIPT" sessions --json
    [ "$status" -eq 0 ]
    # the identity colours an external consumer used to read from @identity-bg
    [[ "$output" == *"#1EA1EB"* ]]
    [[ "$output" == *"#54B204"* ]]
    echo "$output" | python3 -c 'import json,sys; assert len(json.load(sys.stdin)["sessions"]) == 2'
}

@test "romp sessions: reads the KERNEL, authorizing with the serve token" {
    run "$ROMP_SCRIPT" sessions
    [ "$status" -eq 0 ]
    grep -q "127.0.0.1:29855/sessions" "$CURL_LOG"
    # The token goes in on stdin, never argv: /proc/<pid>/cmdline is world-readable, so a token in
    # argv hands full control of every session to any other account on the machine.
    grep -q "X-Romp-Token: TESTTOKEN123" "$CURL_STDIN"
    ! grep -q "TESTTOKEN123" "$CURL_LOG"
}

@test "romp sessions: a dead kernel fails LOUDLY, never an empty list read as no sessions" {
    CURL_FAIL=1 run "$ROMP_SCRIPT" sessions
    [ "$status" -ne 0 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "romp sessions: an unknown flag is refused rather than silently ignored" {
    run "$ROMP_SCRIPT" sessions --nope
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp sessions"* ]]
}

@test "romp sessions: listed in help, under the scripting group" {
    run "$ROMP_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp sessions"* ]]
}
