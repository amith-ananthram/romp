#!/usr/bin/env bats

# `romp-manager ensure` is the no-`romp on` auto-start: the SessionStart hook
# (romp-manager-ensure.sh) calls it so romp usage brings up the supervisor.
# It must be idempotent (no second manager) and non-blocking (spawns detached).

load free-port
load tmux-private

setup() {
    TEST_DIR="$(mktemp -d)"
    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    # Every test here starts a REAL manager, and startManager() runs `tmux start-server` before it does
    # anything else. Two layers keep that off the machine's tmux server (tests/tmux-private.bash has the
    # 2026-09-06 incident this file caused): a recording fake tmux on PATH for the WHOLE file (the
    # detached manager that `ensure` spawns inherits PATH too), and a socket directory private to the
    # test, for any tmux call that reaches the real binary. The fake appends each call's argv to
    # FAKE_TMUX_CALLS and writes the socket directory it was handed to FAKE_TMUX_ENV.
    BIN="$TEST_DIR/bin"; mkdir -p "$BIN"
    export FAKE_TMUX_CALLS="$TEST_DIR/tmux-calls" FAKE_TMUX_ENV="$TEST_DIR/tmux-env"
    cat > "$BIN/tmux" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_TMUX_CALLS"
{
    printf 'TMUX_TMPDIR=%s\n' "${TMUX_TMPDIR-}"
    if [ -d "${TMUX_TMPDIR:-/nonexistent}" ]; then echo 'TMUX_TMPDIR_IS_DIR=1'; else echo 'TMUX_TMPDIR_IS_DIR=0'; fi
} > "$FAKE_TMUX_ENV"
exit 0
FAKE
    chmod +x "$BIN/tmux"
    export PATH="$BIN:$PATH"
    tmux_private_socket_dir "$TEST_DIR"   # also floors ROMP_CLI_SCOPE=0: no real scope on the user manager
    # The manager's state root is private too. With neither variable set STATE_ROOT is the live
    # ~/.local/state/romp, and `up` boots from that root's kernels.json: the fake launcher below runs
    # once per kernel registered there, each handed the registry entry's stateDir, and the drain
    # poll's token is read from that root's serve-token. ROMP_STATE_DIR outranks the XDG floor and a
    # profiled kernel's sessions inherit it, so it is dropped, not shadowed (tests/bats-state-isolation.bats
    # keeps both lines in every suite that starts the real manager).
    unset ROMP_STATE_DIR
    export XDG_STATE_HOME="$TEST_DIR/state"; mkdir -p "$XDG_STATE_HOME"
    # Fake kernel launcher: stay alive without binding a real port (we assert on the
    # manager's control endpoint, not a live kernel).
    FAKE="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$FAKE"
    chmod +x "$FAKE"
    free_port CPORT MPORT   # fresh per test, never a literal (tests/free-port.bash)
}

teardown() {
    # Graceful stop, then reap the detached manager (it is orphaned, not our child).
    curl -fsS -X POST "http://127.0.0.1:${CPORT:-0}/stop" >/dev/null 2>&1 || true
    [[ -n "${MGR_PID:-}" ]] && kill "$MGR_PID" 2>/dev/null || true
    # The kill before the rm (a server the real tmux started must not outlive the test), and last, so
    # its failure is teardown's status: bats swallows a failing command mid-teardown.
    tmux_private_kill && rm -rf "$TEST_DIR"
}

@test "ensure: idempotent, non-blocking auto-start of the supervisor" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"

    # Nothing running yet → status fails.
    run env ROMP_MANAGER_PORT=$CPORT node "$MGR" status
    [ "$status" -eq 1 ]

    # ensure returns 0 immediately (non-blocking) and spawns a DETACHED manager.
    run env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" node "$MGR" ensure
    [ "$status" -eq 0 ]

    # The detached manager comes up on the control port.
    local i
    for i in $(seq 1 40); do
        curl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && break
        sleep 0.1
    done
    run curl -fsS "http://127.0.0.1:$CPORT/status"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"id":"main"'* ]]
    MGR_PID="$(printf '%s' "$output" | grep -oE '"pid":[ ]*[0-9]+' | head -1 | grep -oE '[0-9]+')"

    # A second ensure is a harmless no-op; the manager stays up (no double-start).
    run env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" node "$MGR" ensure
    [ "$status" -eq 0 ]
    run curl -fsS "http://127.0.0.1:$CPORT/status"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"id":"main"'* ]]
}

@test "manager bootstraps a tmux server (launchd-rooted) with exit-empty off" {
    command -v node >/dev/null 2>&1 || skip "node not available"

    # setup()'s fake tmux records its args, so we assert WHAT the manager asks of tmux at startup
    # without touching a real tmux server. (The fix: a launchd-rooted server so new sessions don't
    # inherit a terminal's TCC identity → the "VS Code wants to access" prompt.)
    #
    # Run `up` directly on this test's own ports (setup picks fresh ones per test, so it cannot no-op
    # against another test's manager); the manager calls startTmuxServer() at startup, before it ever
    # binds the control port. A literal here once duplicated romp-manager-origin.bats's control port,
    # and a manager SIGTERM'd there outlives the kill by shutdownAll's exit grace, so a combined run
    # found `up` exiting "already running" without ever calling tmux.
    env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 50); do [ -f "$FAKE_TMUX_CALLS" ] && break; sleep 0.1; done
    kill "$MGR_PID" 2>/dev/null || true

    # startManager() → startTmuxServer() ran our fake tmux with start-server + exit-empty off.
    [ -f "$FAKE_TMUX_CALLS" ]
    grep -q "start-server" "$FAKE_TMUX_CALLS"
    grep -q "exit-empty off" "$FAKE_TMUX_CALLS"
}

@test "the manager's tmux call is handed a socket directory private to the test, and it already exists" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"

    # The 2026-09-06 incident, on the path that caused it: `ensure` spawns a DETACHED manager, whose
    # `tmux start-server` ran on the machine's default socket. The manager inherits the test's
    # environment, so the fake tmux records the socket directory it was handed. It must be the test's
    # own, and it must EXIST at that moment: tmux 3.4 silently uses the default socket directory when
    # TMUX_TMPDIR names a missing one, so an export without the mkdir isolates nothing. The manager
    # sits on setup's per-test CPORT, so teardown's /stop reaps it like every other.
    run env ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" node "$MGR" ensure
    [ "$status" -eq 0 ]
    # Wait for the control port, not the env file: startManager() runs tmux (which writes the file) a few
    # milliseconds BEFORE it binds the port, and teardown's /stop needs the port. A test that returned on
    # the file could hand teardown a manager that is not yet listening, and the /stop would miss it.
    local i
    for i in $(seq 1 50); do
        curl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && break
        sleep 0.1
    done
    run curl -fsS "http://127.0.0.1:$CPORT/status"
    [ "$status" -eq 0 ]
    MGR_PID="$(printf '%s' "$output" | grep -oE '"pid":[ ]*[0-9]+' | head -1 | grep -oE '[0-9]+')"
    [ -s "$FAKE_TMUX_ENV" ]
    grep -qxF "TMUX_TMPDIR=$TEST_DIR/tmux" "$FAKE_TMUX_ENV"
    grep -qxF "TMUX_TMPDIR_IS_DIR=1" "$FAKE_TMUX_ENV"
}

@test "with the real tmux, the server the manager starts lives inside the test directory" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"
    # The fake tmux leaves PATH for this test alone: the machine's tmux takes the manager's call, and the
    # private socket directory is the ONLY thing between that call and the machine's default server.
    local path="${PATH#"$BIN:"}"
    PATH="$path" command -v tmux >/dev/null 2>&1 || skip "tmux not available"

    # Refuse to run a real tmux unless the isolation is in place: a regression in setup() must fail
    # here, not reproduce the incident on the machine's server.
    [[ "$TMUX_TMPDIR" == "$TEST_DIR/"* ]]
    [ -d "$TMUX_TMPDIR" ]

    # The server sources a tmux.conf at start-server, and the manager's call cannot be given -f from here
    # (nor would -f on the helper's own kill/show calls help: a client passes -f only to a server it
    # starts, and neither command starts one). So the developer's config is kept out by pointing HOME
    # and XDG_CONFIG_HOME at the test dir, where no tmux.conf exists. The socket path does not depend on
    # HOME (TMUX_TMPDIR alone decides it), so the pin below is unchanged; the manager's own use of HOME
    # is its state dir, which this also keeps out of the developer's. /etc/tmux.conf, if a box has one,
    # is still read; nothing short of -f avoids that.
    local home="$TEST_DIR/home"; mkdir -p "$home"
    env PATH="$path" HOME="$home" XDG_CONFIG_HOME="$home/.config" \
        ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    # startManager() runs tmux before it binds the control port, so a live port means the call is done.
    local i
    for i in $(seq 1 50); do curl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && break; sleep 0.1; done
    kill "$MGR_PID" 2>/dev/null || true

    # tmux places the socket at $TMUX_TMPDIR/tmux-<uid>/default: under the test directory, nowhere else.
    local sock="$TMUX_TMPDIR/tmux-$(id -u)/default"
    [ -S "$sock" ]
    [ "$(ls -A "$TMUX_TMPDIR")" = "tmux-$(id -u)" ]
    [ ! -e "$TEST_DIR/default" ]
    # A live server answers on it, set up the way the manager asked (exit-empty off is what keeps an
    # empty server alive). -S names the socket, so this reaches no other server, and the tmux is the
    # machine's, found past the fake (which answers anything with exit 0 and no output).
    local tmux; tmux="$(tmux_private_real_tmux)"
    run "$tmux" -S "$sock" show -g exit-empty
    [ "$status" -eq 0 ]
    [[ "$output" == *"exit-empty off"* ]]
    # The teardown kill reaches that server too (the first run of this isolation leaked one: PATH
    # handed kill-server to the fake). After it, nothing answers on the socket.
    tmux_private_kill
    run "$tmux" -S "$sock" show -g exit-empty
    [ "$status" -ne 0 ]
}

@test "a leaked \$TMUX never reaches the manager or its kernels" {
    command -v node >/dev/null 2>&1 || skip "node not available"

    # The 2026-07-20 anchor-clobber chain: a manual `romp-manager up` from inside tmux leaked
    # $TMUX to kernels + SDK sessions, whose tmux-status hooks then hijacked the ATTACHED
    # session's @romp-session-id (live session flapping "dead" -> bogus revive). The manager
    # must scrub TMUX/TMUX_PANE from its own env before any kernel spawns.
    local envdump="$TEST_DIR/kernel-env"
    printf '#!/usr/bin/env bash\nenv > "%s"\nexec sleep 30\n' "$envdump" > "$FAKE"
    chmod +x "$FAKE"
    env TMUX="/tmp/tmux-000/default,99999,7" TMUX_PANE="%7" \
        ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKE" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 50); do [ -s "$envdump" ] && break; sleep 0.1; done
    curl -fsS -X POST "http://127.0.0.1:$CPORT/stop" >/dev/null 2>&1 || true
    [ -s "$envdump" ]
    # `run` + status, NOT a bare `! grep`: `!` is exempt from set -e, so mid-test it asserts nothing.
    run grep -q '^TMUX=' "$envdump"
    [ "$status" -ne 0 ]
    run grep -q '^TMUX_PANE=' "$envdump"
    [ "$status" -ne 0 ]
    grep -q '^ROMP_SERVE_BIN=' "$envdump"   # the dump is real: other env DID flow through
}

@test "quiet-mode refresh defers while turns are in flight, coalesces, applies on the quiet event" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"

    # Fake kernel: binds the serve port, answers /busy from a file the test flips, and logs each
    # spawn — so the bounce (SIGTERM + respawn) is observable as a second spawn line.
    local BUSY="$TEST_DIR/busy" SPAWNS="$TEST_DIR/spawns" FAKEK="$TEST_DIR/fake-kernel"
    echo 2 > "$BUSY"
    cat > "$FAKEK" <<'PYEOF'
#!/usr/bin/env python3
import http.server, json, os
with open(os.environ["SPAWN_LOG"], "a") as f:
    f.write("spawn\n")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            n = int(open(os.environ["BUSY_FILE"]).read().strip())
        except Exception:
            n = 0
        b = json.dumps({"busy": n}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["ROMP_SERVE_PORT"])), H).serve_forever()
PYEOF
    chmod +x "$FAKEK"

    env BUSY_FILE="$BUSY" SPAWN_LOG="$SPAWNS" ROMP_QUIET_POLL_MS=200 \
        ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKEK" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 50); do
        curl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && [ -s "$SPAWNS" ] && break
        sleep 0.1
    done
    [ "$(grep -c spawn "$SPAWNS")" -eq 1 ]

    # Two quiet-mode refreshes while turns are in flight: both defer, the second coalesces.
    run curl -fsS -X POST "http://127.0.0.1:$CPORT/restart-all?when=quiet"
    [[ "$output" == *'"deferred":true'* ]]
    run curl -fsS -X POST "http://127.0.0.1:$CPORT/restart-all?when=quiet"
    [[ "$output" == *'"coalesced":2'* ]]

    # Still busy after several poll cycles -> no bounce happened.
    sleep 1
    [ "$(grep -c spawn "$SPAWNS")" -eq 1 ]

    # The fleet quiets -> exactly ONE bounce delivers both queued refreshes.
    echo 0 > "$BUSY"
    for i in $(seq 1 60); do [ "$(grep -c spawn "$SPAWNS")" -ge 2 ] && break; sleep 0.1; done
    [ "$(grep -c spawn "$SPAWNS")" -eq 2 ]
    curl -fsS -X POST "http://127.0.0.1:$CPORT/stop" >/dev/null 2>&1 || true
}

@test "quiet-mode refresh defers on background work too, and asks for the drain hold only while turns are in flight" {
    # T240: a session running a Workflow has no turn in flight between its own turns, so a quiet
    # deploy applied instantly over it. The kernel now reports {busy, inflight, background}; the
    # manager defers on either kind of busyness, but asks for the box-wide hold on NEW turn starts
    # (/busy?drain=1) only while a turn is actually in flight — background work must never freeze
    # other sessions' queued prompts.
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"

    local INF="$TEST_DIR/inflight" BG="$TEST_DIR/background" SPAWNS="$TEST_DIR/spawns" REQS="$TEST_DIR/reqs" FAKEK="$TEST_DIR/fake-kernel"
    echo 0 > "$INF"; echo 1 > "$BG"; : > "$REQS"
    cat > "$FAKEK" <<'PYEOF'
#!/usr/bin/env python3
import http.server, json, os
with open(os.environ["SPAWN_LOG"], "a") as f:
    f.write("spawn\n")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with open(os.environ["REQ_LOG"], "a") as f:
            f.write(self.path + "\n")
        def rd(k):
            try: return int(open(os.environ[k]).read().strip())
            except Exception: return 0
        i, g = rd("INF_FILE"), rd("BG_FILE")
        b = json.dumps({"busy": i + g, "inflight": i, "background": g, "draining": False}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["ROMP_SERVE_PORT"])), H).serve_forever()
PYEOF
    chmod +x "$FAKEK"

    # 500 ms polls: a local answer always lands before the NEXT poll is issued, so "the first poll
    # asks, no later poll does" cannot race on a slow CI box (review find)
    env INF_FILE="$INF" BG_FILE="$BG" REQ_LOG="$REQS" SPAWN_LOG="$SPAWNS" ROMP_QUIET_POLL_MS=500 \
        ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKEK" \
        node "$MGR" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 50); do
        curl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && [ -s "$SPAWNS" ] && break
        sleep 0.1
    done
    [ "$(grep -c spawn "$SPAWNS")" -eq 1 ]

    run curl -fsS -X POST "http://127.0.0.1:$CPORT/restart-all?when=quiet"
    [[ "$output" == *'"deferred":true'* ]]
    sleep 2.2
    [ "$(grep -c spawn "$SPAWNS")" -eq 1 ]                    # background work alone DEFERS the restart
    # the first poll asks for the hold (it knows nothing yet); every later poll, seeing 0 in flight,
    # must not — the hold would freeze other sessions' queued prompts for nothing
    [ "$(grep -c 'drain=1' "$REQS")" -le 1 ]
    [ "$(grep -c '^/busy' "$REQS")" -ge 3 ]

    echo 0 > "$BG"
    for i in $(seq 1 60); do [ "$(grep -c spawn "$SPAWNS")" -ge 2 ] && break; sleep 0.1; done
    [ "$(grep -c spawn "$SPAWNS")" -eq 2 ]                    # the work ended → the quiet event applies
    curl -fsS -X POST "http://127.0.0.1:$CPORT/stop" >/dev/null 2>&1 || true
}

@test "ensure: a romp down marker holds the auto-start: no manager comes up, exit 0, the reason said" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"
    # setup's free_port pair, like every other test here: a literal pair once collided with another
    # suite's control port, where a concurrent run's manager answered the probe and ensure said nothing
    local state="$TEST_DIR/state" SPAWNS="$TEST_DIR/spawns" FAKEK="$TEST_DIR/fake-serve-recording"
    # a launcher that records each spawn: whether ensure started a manager is read off the record
    # that manager's kernel would leave, never off a clock
    printf '#!/usr/bin/env bash\necho spawn >> "%s"\nexec sleep 30\n' "$SPAWNS" > "$FAKEK"
    chmod +x "$FAKEK"
    mkdir -p "$state"
    printf '{"t": %s, "cmd": "romp down"}\n' "$(date +%s)" > "$state/down-by-romp"
    run env ROMP_STATE_DIR="$state" ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKEK" node "$MGR" ensure
    local ensure_status=$status ensure_output=$output marker_kept=0
    [ -f "$state/down-by-romp" ] && marker_kept=1
    # the deliberate `up` starts BEFORE the assertions on ensure: it takes the control port, so a
    # manager an ensure that ignored the marker spawned detached either finds the port taken and
    # exits, or holds it and is what teardown's /stop reaps. Asserting first would leave that stray
    # to come up after a failed test ended, with nothing left to stop it (it happened: its launcher
    # gone with the test dir, its respawn fell through to the machine's own romp-serve).
    env ROMP_STATE_DIR="$state" ROMP_MANAGER_PORT=$CPORT ROMP_SERVE_PORT=$MPORT ROMP_SERVE_BIN="$FAKEK" node "$MGR" up >"$TEST_DIR/up.log" 2>&1 &
    MGR_PID=$!
    [ "$ensure_status" -eq 0 ]                # the far-host update or restart is not failing: the kernel is down on purpose
    [[ "$ensure_output" == *"stopped by \`romp down\`"* ]]
    [[ "$ensure_output" == *"romp up"* ]]
    [ "$marker_kept" -eq 1 ]                  # ensure never clears it; only a deliberate start does

    # ...and the deliberate `up` clears the marker and comes up, the ONE manager this test starts:
    # its kernel is the one spawn on the record. A manager ensure had started would have recorded a
    # spawn of its own (and cleared the marker itself), so a count of one says ensure spawned nothing.
    local i
    for i in $(seq 1 40); do
        curl -fsS "http://127.0.0.1:$CPORT/status" >/dev/null 2>&1 && [ -s "$SPAWNS" ] && break
        sleep 0.1
    done
    run curl -fsS "http://127.0.0.1:$CPORT/status"
    [ "$status" -eq 0 ]
    [ ! -e "$state/down-by-romp" ]
    grep -q 'cleared the `romp down` marker' "$TEST_DIR/up.log"
    [ "$(grep -c spawn "$SPAWNS")" -eq 1 ]
    curl -fsS -X POST "http://127.0.0.1:$CPORT/stop" >/dev/null 2>&1 || true
    for i in $(seq 1 60); do kill -0 "$MGR_PID" 2>/dev/null || break; sleep 0.1; done
}
