#!/usr/bin/env bats

# tests/free-port.bash picks loopback ports nothing is bound to, for the suites that start a real
# bin/romp-manager. These cases pin its contract: the band, distinct picks, and a loud failure where a
# silent one would send the subject to the manager's default port. Nothing below starts a manager or a node process.

load free-port

setup() {
    TEST_DIR="$(mktemp -d)"
}

teardown() {
    rm -rf "$TEST_DIR"
}

@test "free_port assigns a port in 20000-24999 that a bind then succeeds on" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    local port=""
    free_port port
    [[ "$port" =~ ^[0-9]+$ ]]
    [ "$port" -ge 20000 ]
    [ "$port" -le 24999 ]
    python3 -c "import socket; socket.socket().bind(('127.0.0.1', $port))"
}

@test "free_port assigns every named variable a distinct port" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    local a="" b="" c=""
    free_port a b c
    [[ "$a" =~ ^[0-9]+$ ]] && [[ "$b" =~ ^[0-9]+$ ]] && [[ "$c" =~ ^[0-9]+$ ]]
    [ "$a" != "$b" ] && [ "$b" != "$c" ] && [ "$a" != "$c" ]
}

@test "free_port assigns a caller's variable even when it is named like one of the helper's locals" {
    # bash scopes dynamically: a `local` in the caller is what the helper's own `local` of the same name
    # would shadow, so plain local names in the helper hand such a caller an empty string, and an empty
    # port reaches bin/romp-manager as its default control port.
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    local name="" i="" ports="" picks=""
    free_port name i ports picks
    [[ "$name" =~ ^[0-9]+$ ]] && [[ "$i" =~ ^[0-9]+$ ]]
    [[ "$ports" =~ ^[0-9]+$ ]] && [[ "$picks" =~ ^[0-9]+$ ]]
}

@test "free_port fails loudly without python3" {
    # An empty pick would reach bin/romp-manager as its default control port, so the helper must fail
    # the test at the pick, not hand back nothing.
    mkdir "$TEST_DIR/empty"
    PATH="$TEST_DIR/empty" run free_port port
    [ "$status" -eq 1 ]
    [[ "$output" == *"free-port:"* ]]
    [[ "$output" == *"python3"* ]]
}

@test "free_port with no variable names is a usage error" {
    run free_port
    [ "$status" -eq 1 ]
    [[ "$output" == *"free-port:"* ]]
}
