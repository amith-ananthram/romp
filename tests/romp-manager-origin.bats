#!/usr/bin/env bats

# CSRF / drive-by guard for the manager control port (M2). A browser cross-site
# request carries an Origin the page cannot forge; the manager must reject any
# non-loopback Origin so a malicious tab can't POST /restart-all or /stop and
# kill the user's kernels. Server-side clients (the kernel's Restart proxy, the
# `romp on` CLI) send no Origin and must keep working.

load free-port
load cli-scope-floor

setup() {
    TEST_DIR="$(mktemp -d)"
    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    # The manager under test is REAL: the floor keeps it from leaving a transient scope on the
    # developer's user manager (tests/cli-scope-floor.bash).
    cli_scope_floor
    # The manager's state root is private too: with neither variable set, `up` boots from the live
    # ~/.local/state/romp's kernels.json (the fake launcher below, once per kernel registered there,
    # each handed that entry's stateDir) and the drain poll's token comes from the live serve-token.
    # ROMP_STATE_DIR outranks the XDG floor and a profiled kernel's sessions inherit it, so it is
    # dropped, not shadowed (tests/bats-state-isolation.bats keeps both lines in every such suite).
    unset ROMP_STATE_DIR
    export XDG_STATE_HOME="$TEST_DIR/state"; mkdir -p "$XDG_STATE_HOME"
    # Fake kernel launcher: stays alive without binding a real port.
    FAKE="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$FAKE"
    chmod +x "$FAKE"
    free_port CPORT MPORT   # fresh per test, never a literal (tests/free-port.bash)
}

teardown() {
    [[ -n "${MGR_PID:-}" ]] && kill "$MGR_PID" 2>/dev/null || true
    rm -rf "$TEST_DIR"
}

@test "manager rejects cross-site Origin, allows no-Origin clients" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"

    env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!

    local i
    for i in $(seq 1 40); do
        curl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && break
        sleep 0.1
    done

    # No Origin (server-side client) → 200.
    run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CPORT/status"
    [ "$output" = "200" ]

    # Cross-site Origin on a read → 403.
    run curl -s -o /dev/null -w '%{http_code}' -H 'Origin: http://evil.example' \
        "http://127.0.0.1:$CPORT/status"
    [ "$output" = "403" ]

    # Cross-site state-changing POST (the real attack) → 403.
    run curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Origin: http://evil.example' \
        "http://127.0.0.1:$CPORT/restart-all"
    [ "$output" = "403" ]

    # A loopback Origin (the local web UI, if it ever calls directly) → allowed.
    run curl -s -o /dev/null -w '%{http_code}' -H "Origin: http://127.0.0.1:$CPORT" \
        "http://127.0.0.1:$CPORT/status"
    [ "$output" = "200" ]
}
