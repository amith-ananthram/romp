#!/usr/bin/env bats

# hooks/romp-postal-drain.sh is the Stop hook that hands drained peer mail back to the session as
# one Stop-hook decision line, `{"decision":"block","reason":<the mail>}`, JSON-escaped in pure
# bash. Claude Code parses that line; a line it cannot parse is dropped without a word, and the
# drain is CONSUMING, so by then the mail is already marked read: the session never sees the
# message and neither side is told. These tests drive the real hook with a stub
# romp-postal-service beside it (the hook resolves ../bin from its own real path) and read its
# stdout with a strict JSON parser, so the decision must parse and the body must come back
# byte for byte.

setup() {
    TEST_DIR="$(mktemp -d)"
    unset ROMP_STATE_DIR
    export XDG_STATE_HOME="$TEST_DIR/state"
    # HOME under the test dir: the hook's off-switch is $HOME/.claude/romp-postal-off, and a
    # developer who has it set must not see these tests pass as no-ops.
    export HOME="$TEST_DIR/home"; mkdir -p "$HOME/.claude"
    # The summarizer guard exits the hook before it drains; a suite run from inside a summarizing
    # shell would otherwise pass every test with no output.
    unset ROMP_SUMMARIZING
    mkdir -p "$TEST_DIR/hooks" "$TEST_DIR/bin"
    cp "$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/romp-postal-drain.sh" "$TEST_DIR/hooks/"
    HOOK="$TEST_DIR/hooks/romp-postal-drain.sh"
    export DRAIN_LOG="$TEST_DIR/drain.log"
    SID="11111111-2222-3333-4444-555555555555"
    INPUT="{\"session_id\":\"$SID\"}"
}

teardown() { rm -rf "$TEST_DIR"; }

# Stub romp-postal-service: logs its argv, and on `drain` prints the body the test wrote (a printf
# format, so the test can spell control bytes as octal escapes). The body travels through a file,
# never through the stub's own source, so no shell quoting stands between the bytes and the hook.
stub_drain() {   # $1 printf format for the body
    printf "$1" > "$TEST_DIR/body"
    cat > "$TEST_DIR/bin/romp-postal-service" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DRAIN_LOG"
[ "${1:-}" = drain ] || exit 0
cat "$(dirname "$0")/../body"
STUB
    chmod +x "$TEST_DIR/bin/romp-postal-service"
}

run_hook() { run bash -c 'printf "%s" "$1" | "$2"' _ "$INPUT" "$HOOK"; }

# Parses the hook's stdout as JSON and checks the decision is block and the reason is the body,
# byte for byte, after what the hook does by design: `$(...)` strips the drain's trailing newlines.
# Extra assertions on the reason arrive as Python expressions in $1 (an empty string for none).
assert_decision_carries_body() {   # $1 extra Python expression over `reason`
    printf '%s' "$output" | python3 -c '
import json, sys
d = json.load(sys.stdin)          # raises on a raw control byte: "Invalid control character"
assert d["decision"] == "block", d
reason = d["reason"]
body = open(sys.argv[1], "rb").read().decode("utf-8").rstrip("\n")
assert reason == body, (repr(reason), repr(body))
if sys.argv[2]:
    assert eval(sys.argv[2]), (sys.argv[2], repr(reason))
' "$TEST_DIR/body" "$1"
}

@test "a body carrying an ANSI colour sequence and a form feed reaches the session intact" {
    # Pasted terminal output is the everyday shape of this: an escape byte and a form feed, neither
    # of which the hook used to escape, so the decision line was not JSON and the mail was lost.
    stub_drain 'hello \033[31mred\033[0m\fworld\n'
    run_hook
    [ "$status" -eq 0 ]
    assert_decision_carries_body '"\x1b[31m" in reason and "\f" in reason'
}

@test "every control byte a message can carry survives, beside quote, backslash, tab and newline" {
    # Every byte 0x01-0x1f except the three the hook already handled (TAB and LF as escapes, CR
    # dropped by design), plus DEL, which JSON allows as it is. NUL cannot ride a shell string.
    stub_drain 'a\\b"c\001\002\003\004\005\006\007\010\011\012\013\014\016\017\020\021\022\023\024\025\026\027\030\031\032\033\034\035\036\037\177z\n'
    run_hook
    [ "$status" -eq 0 ]
    assert_decision_carries_body 'len(reason) == 37'
}

@test "a plain body is delivered as before, from a drain of the session's own id" {
    stub_drain 'alpha says: "done", see notes\\draft\n\tnext: beta\n'
    run_hook
    [ "$status" -eq 0 ]
    assert_decision_carries_body '"\n\tnext: beta" in reason'
    grep -qx -- "drain --id $SID" "$DRAIN_LOG"
}
