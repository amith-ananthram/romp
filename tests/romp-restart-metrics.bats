#!/usr/bin/env bats

# `romp restart-metrics` — the restart monitors' reader (T304). bin/romp hands the verb and its arguments
# to romp-restart-metrics (python, cli/restart_metrics.py), which reads the state ledgers and, unless
# --no-live, the running kernel's routes. Run here for REAL against an empty hermetic state directory with
# --no-live: nothing reads a kernel, and a missing ledger is named loudly, never printed as a quiet zero.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    unset ROMP_STATE_DIR ROMP_SERVE_TOKEN
    TEST_DIR="$(mktemp -d)"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    export ROMP_HOST_NAME=TESTHOST
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp restart-metrics runs the reader against the state dir and names a missing ledger" {
    run "$ROMP_SCRIPT" restart-metrics --no-live --tz UTC
    [ "$status" -eq 0 ]
    [[ "$output" == *"restart metrics — host TESTHOST"* ]]
    [[ "$output" == *"MISSING ledgers"* ]]
    [[ "$output" == *"restart-cuts.jsonl"* ]]
    [[ "$output" == *"live: skipped"* ]]
}

@test "romp restart-metrics --json prints the document with its schema and window" {
    printf '%s\n' '{"t": 1700000000, "pid": 1, "cutTurns": [], "stopped": 1, "unjoined": 0, "reaped": 0, "watchesArmed": 0, "reason": "p2p-update"}' \
        > "$XDG_STATE_HOME/romp/restart-cuts.jsonl"
    run "$ROMP_SCRIPT" restart-metrics --json --no-live --window week --anchor 2023-11-14 --tz UTC
    [ "$status" -eq 0 ]
    [[ "$output" == *'"schema": 1'* ]]
    [[ "$output" == *'"week of 2023-11-14"'* ]]
    [[ "$output" == *'"restarts": 1'* ]]
}

@test "a bad date is refused with exit 2" {
    run "$ROMP_SCRIPT" restart-metrics --no-live --since not-a-date
    [ "$status" -eq 2 ]
    [[ "$output" == *"bad date"* ]]
}

@test "romp help lists the verb" {
    run "$ROMP_SCRIPT" help
    [[ "$output" == *"romp restart-metrics"* ]]
}
