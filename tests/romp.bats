#!/usr/bin/env bats

# Resolve path to the romp script under test
ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

load free-port
load tmux-private

setup() {
    TEST_DIR="$(mktemp -d)"
    WORK_DIR="$TEST_DIR/myproject"
    MOCK_DIR="$TEST_DIR/mock"
    export MOCK_LOG="$TEST_DIR/mock.log"

    mkdir -p "$WORK_DIR" "$MOCK_DIR"

    # Fixtures the tmux mock reads:
    #   sessions file: one per line, "name" or "name|rompflag" (flag defaults to 1)
    #   identity file: "name=colour" lines (for @identity-bg lookups)
    export MOCK_TMUX_SESSIONS_FILE="$TEST_DIR/mock_sessions.txt"
    export MOCK_TMUX_IDENTITY_FILE="$TEST_DIR/mock_identity.txt"
    touch "$MOCK_TMUX_SESSIONS_FILE" "$MOCK_TMUX_IDENTITY_FILE"

    cat > "$MOCK_DIR/tmux" << 'MOCK'
#!/usr/bin/env bash
echo "tmux $*" >> "$MOCK_LOG"
# Opt-in: simulate an older tmux that rejects a given option (e.g. tmux 3.0 has no
# copy-mode-position-style, added in 3.2). Off unless a test sets MOCK_TMUX_FAIL_OPT.
if [[ -n "${MOCK_TMUX_FAIL_OPT:-}" && "$*" == *"$MOCK_TMUX_FAIL_OPT"* ]]; then
  echo "invalid option: $MOCK_TMUX_FAIL_OPT" >&2
  exit 1
fi
case "$1" in
  has-session)
    # $3 is "=<name>"; a session exists iff its name is in the file
    target="${3#=}"
    cut -d'|' -f1 "$MOCK_TMUX_SESSIONS_FILE" 2>/dev/null | grep -qx "$target" && exit 0
    exit 1
    ;;
  display-message)
    echo "${MOCK_TMUX_CURRENT:-mysession}"
    exit 0
    ;;
  show-environment)
    # the server's globals, one NAME=value per line, from the fixture (empty file = a clean server)
    cat "${MOCK_TMUX_GLOBALS_FILE:-/dev/null}" 2>/dev/null
    exit 0
    ;;
  list-sessions)
    # Reformat each session line per the requested -F format ($3).
    # @romp defaults to 1; a "name|0" line is a non-romp session.
    fmt="$3"
    while IFS='|' read -r s c; do
      [[ -z "$s" ]] && continue
      c="${c:-1}"
      out="$fmt"
      out="${out//'#{@romp}'/$c}"
      out="${out//'#{session_name}'/$s}"
      out="${out//'#S'/$s}"
      echo "$out"
    done < "$MOCK_TMUX_SESSIONS_FILE" 2>/dev/null
    exit 0
    ;;
  show)
    if [[ "$2" == "-t" && "$4" == "-v" && "$5" == "@identity-bg" ]]; then
      result=$(grep "^${3}=" "$MOCK_TMUX_IDENTITY_FILE" 2>/dev/null | head -1 | cut -d= -f2)
      [[ -n "$result" ]] && { echo "$result"; exit 0; }
      exit 1
    fi
    # global status-format[0] — the default main-row composition the
    # provisioning pins onto each session (sentinel for assertions)
    if [[ "$2" == "-gv" && "$3" == "status-format[0]" ]]; then
      echo "GLOBAL_ROW0"; exit 0
    fi
    exit 0
    ;;
esac
exit 0
MOCK
    chmod +x "$MOCK_DIR/tmux"

    # Hermetic claude: the launch path probes `claude --version` for the 2.1.224
    # floor (the inbound-accept setting + @romp-inbound-accept tag) — a dev
    # machine's real claude would nondeterministically flip those on. Pin a
    # modern version; per-test override via _stub_claude.
    _stub_claude "2.1.226"

    # Hermetic postal service (2026-09-06): on every resume bin/romp double-forks
    # `romp-postal-service picker-check` and returns without waiting for it. The real
    # service mints a serve-token under $HOME/.local/state/romp when none exists, and did
    # so after teardown had removed TEST_DIR, so the tree came back with that one file in
    # it: four to six per run of this file. bin/romp puts its own directory first on PATH,
    # so a stand-in here cannot shadow the real one through PATH; it reaches bin/romp
    # through the ROMP_POSTAL_BIN seam, which the picker-check honours like `mail` and
    # `refresh`. A no-op: the tests that assert on the service's calls overwrite it with
    # a recording mock.
    printf '#!/usr/bin/env bash\nexit 0\n' > "$MOCK_DIR/romp-postal-service"
    chmod +x "$MOCK_DIR/romp-postal-service"
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal-service"

    export PATH="$MOCK_DIR:$PATH"
    # The romp-manager tests below start a REAL bin/romp-manager, whose startup runs `tmux start-server`,
    # and `romp new -t` runs `tmux new-session`: the mock above takes both, and the private socket
    # directory keeps any call that reaches the real binary off the machine's tmux server
    # (tests/tmux-private.bash has the 2026-09-06 incident).
    tmux_private_socket_dir "$TEST_DIR"
    unset TMUX            # default: outside tmux → attach-session branch
    unset ROMP_SID        # default: outside a romp session — `romp new` names no parent (tests export it on purpose)
    # Hermetic HOME: bin/romp probes $HOME/.claude/romp-postal.mcp.json (would
    # nondeterministically append --mcp-config on a dev machine) and writes the
    # names map under XDG_STATE_HOME (was polluting the REAL state dir).
    export HOME="$TEST_DIR/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    # ROMP_STATE_DIR outranks that floor, and a profiled kernel's sessions inherit it: the real managers
    # the romp-manager tests start would boot from that root's kernels.json (tests/bats-state-isolation.bats).
    unset ROMP_STATE_DIR
    # bin/romp-service resolves the unit and the plist under XDG_CONFIG_HOME, then HOME: under the test
    # HOME, so a test that reaches the real romp-service (the `romp up` dispatch below) finds none of
    # the machine's and never runs its systemctl or launchctl.
    export XDG_CONFIG_HOME="$HOME/.config"
    # Dead control, kernel-serve and kernel ports, the floor tests/conftest.py gives the pytest side.
    # bin/romp puts its own bin directory first on PATH, so a test that mocks no romp-manager runs the
    # REAL one, and with the variable unset its status probe reaches the machine's manager on the
    # default port, where `romp down` would go on to stop it; a kernel probe with no port set reaches
    # the machine's kernel the same way. The tests that start a real manager set their own free ports;
    # the `romp down` cases set ROMP_KERNEL_PORT to their fake kernel's (see their preamble).
    export ROMP_MANAGER_PORT=1 ROMP_SERVE_PORT=1 ROMP_KERNEL_PORT=1
    mkdir -p "$HOME"
    cd "$WORK_DIR"
}

teardown() {
    # Tests that launch a background romp-manager record its pid in MGR_PID so we
    # always reap it (and its child kernels), even if an assertion aborted the test.
    [[ -n "${MGR_PID:-}" ]] && kill "$MGR_PID" 2>/dev/null
    # the down tests' fake kernel: -9, because its ignore-term variant swallows SIGTERM by design, and a
    # background child left alive holds bats' output pipe open, stalling the whole run
    [[ -n "${KERNEL_PID:-}" ]] && kill -9 "$KERNEL_PID" 2>/dev/null
    # a stand-in process a down test started to own a second pid (the /version-disagrees case)
    [[ -n "${OTHER_PID:-}" ]] && kill -9 "$OTHER_PID" 2>/dev/null
    # the stub kernel the `romp tag` tests start (_stub_tag_kernel) serves until reaped here
    [[ -n "${TAG_KERNEL_PID:-}" ]] && kill "$TAG_KERNEL_PID" 2>/dev/null
    # The kill before the rm (a server the real tmux started must not outlive the test), and last, so
    # its failure is teardown's status: bats swallows a failing command mid-teardown.
    tmux_private_kill && rm -rf "$TEST_DIR"
}

# Helper — runs romp with merged stdout+stderr so BATS captures errors
run_romp() {
    "$ROMP_SCRIPT" "$@" 2>&1
}

# Helper — a fake `claude` reporting the given version (the launch path only ever
# runs `claude --version`; the exec line itself lands in the tmux mock's log)
_stub_claude() {
    cat > "$MOCK_DIR/claude" <<STUB
#!/usr/bin/env bash
echo "$1 (Claude Code)"
STUB
    chmod +x "$MOCK_DIR/claude"
}

# Helper — a fake `curl` for the kernel-API paths (`romp new` SDK spawn + `-m` send).
# Logs every call to MOCK_LOG and answers {"ok": true}; MOCK_CURL_FAIL_SEND=1 makes
# the /send leg fail the way curl -f does, so per-leg error reporting is testable.
# MOCK_CURL_FAIL_NEW=1 makes the /new leg a connection failure (exit 7, no body);
# MOCK_CURL_NEW_400=1 makes the kernel answer /new with a 400 whose JSON body names
# the problem — honoring the FLAGS romp passes, the way real curl splits on a 4xx:
# a short-flag cluster carrying -f discards the body and exits 22; plain -s prints
# the body and exits 0. So the test proves the flags, not just the message.
_stub_curl() {
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
# drain the token config romp pipes in (`_romp_token_cfg | curl --config - …`): real curl always reads
# it, but a mock that exits first hands the writer SIGPIPE, and under the script's pipefail that read
# as a false "not reachable" — one random kernel-API test failed per run
[[ " $* " == *" --config - "* ]] && cat >/dev/null
url=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
if [[ -n "${MOCK_CURL_FAIL_SEND:-}" && "$url" == */send ]]; then exit 22; fi
if [[ -n "${MOCK_CURL_FAIL_NEW:-}" && "$url" == */new ]]; then exit 7; fi
if [[ -n "${MOCK_CURL_SEND_QUEUED:-}" && "$url" == */send ]]; then echo '{"ok": true, "queued": true}'; exit 0; fi
if [[ -n "${MOCK_CURL_WATCH_PR_REFUSE:-}" && "$url" == */watch-pr ]]; then
  echo '{"ok": false, "retryable": true, "error": "the watch could not be saved ([Errno 28] No space left on device) - nothing is watching TESTORG/testrepo#7; retry once the state directory takes writes again"}'
  exit 0
fi
if [[ -n "${MOCK_CURL_VERSION:-}" && "$url" == */version ]]; then echo "$MOCK_CURL_VERSION"; exit 0; fi
if [[ -n "${MOCK_CURL_NEW_400:-}" && "$url" == */new ]]; then
  for a in "$@"; do
    if [[ "$a" == "-f" || "$a" == -[!-]*f* ]]; then exit 22; fi
  done
  echo '{"ok": false, "error": "env: ROMP_SID is reserved — romp sets the session identity env itself"}'
  exit 0
fi
# `romp tag`'s GET asks for the status as a trailer (-w '\n%{http_code}'), the way `romp perf`
# does: append it as real curl would, so the read sees a 200 and not a body it must report as an
# answer with no status
_w=""; _prev=""
for a in "$@"; do [[ "$_prev" == "-w" ]] && _w="$a"; _prev="$a"; done
if [[ -n "$_w" ]]; then printf '{"ok": true}%b' "${_w//\%\{http_code\}/200}"; else echo '{"ok": true}'; fi
MOCK
    chmod +x "$MOCK_DIR/curl"
}

# ─── Launch tests ────────────────────────────────────────────────────

@test "bare romp is the dashboard front door: no kernel, loud error, never a session" {
    # Round 3 (2026-07-25): the shortest command does the most common thing. In this
    # hermetic env there is no serve token, so it must fail loudly and launch nothing.
    touch "$MOCK_LOG"    # this path makes no tmux calls at all
    run run_romp
    [ "$status" -eq 1 ]
    [[ "$output" == *"no serve token"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new -m: missing or empty text is a usage error, never a silent no-op" {
    run run_romp new -m
    [ "$status" -eq 2 ]
    [[ "$output" == *"[-m <text>]"* ]]
    run run_romp new -m "" ideabox
    [ "$status" -eq 2 ]
}

@test "new -m with -t is refused loudly (the first prompt is the SDK path's job)" {
    touch "$MOCK_LOG"
    run run_romp new -t -m "do the thing" ideabox
    [ "$status" -eq 2 ]
    [[ "$output" == *"-m needs the default (SDK) session"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new -m: a first prompt the kernel PARKED is reported as queued, not delivered" {
    # the /send route says which arm it took (2026-09-03); a fresh session that is not quiet yet holds the
    # prompt, and the CLI must not claim a delivery that has not happened
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok MOCK_CURL_SEND_QUEUED=1
    run run_romp new -m "look into the flaky test" ideabox
    [ "$status" -eq 0 ]
    [[ "$output" == *"first prompt queued"* ]]
    [[ "$output" != *"first prompt delivered"* ]]
}

@test "new -m: one command spawns AND delivers the first prompt (POST /new, then /send)" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp new -m "look into the flaky test" ideabox
    [ "$status" -eq 0 ]
    [[ "$output" == *"first prompt delivered"* ]]
    grep -q '/new' "$MOCK_LOG"
    grep -q '/send' "$MOCK_LOG"
    # /new lands before /send, and the send payload carries the name + the text
    [ "$(grep -n '/new' "$MOCK_LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n '/send' "$MOCK_LOG" | head -1 | cut -d: -f1)" ]
    grep '/send' "$MOCK_LOG" | grep 'ideabox' | grep -q 'look into the flaky test'
}

@test "new: a comment thread's name creates nothing, says so, and -m addresses the thread by id" {
    # T223: /new answers a thread's name with the THREAD (thread:true + its id). The CLI must not
    # call it "already running" (a thread has no tab), and a -m prompt must ride the returned id —
    # a by-name /send to a thread resolved to no live session and acked while landing nowhere.
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
[[ " $* " == *" --config - "* ]] && cat >/dev/null   # drain the piped token config (see _stub_curl)
url=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
if [[ "$url" == */new ]]; then
  echo '{"ok": true, "id": "66666666-7777-8888-9999-000000000000", "existing": true, "thread": true, "parent": "11111111-2222-3333-4444-555555555555"}'
else
  echo '{"ok": true}'
fi
MOCK
    chmod +x "$MOCK_DIR/curl"
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp new -m "one more question" web-comment-1
    [ "$status" -eq 0 ]
    [[ "$output" == *"comment thread"* ]]
    [[ "$output" != *"already running"* ]]
    grep '/send' "$MOCK_LOG" | grep -q '"id": "66666666-7777-8888-9999-000000000000"'
    ! grep '/send' "$MOCK_LOG" | grep -q '"name": "web-comment-1"'
}

@test "fork: POST /fork with parent, new name and optional --at cut" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp fork exp-web exp-web-stage2
    [ "$status" -eq 0 ]
    [[ "$output" == *"branched from"* ]]
    grep '/fork' "$MOCK_LOG" | grep -q '"parent": *"exp-web"'
    grep '/fork' "$MOCK_LOG" | grep -q '"name": *"exp-web-stage2"'
    # --at rides through as the cut record
    run run_romp fork --at aaaabbbb-1111-2222-3333-444455556666 exp-web exp-web-fig
    [ "$status" -eq 0 ]
    grep '/fork' "$MOCK_LOG" | grep -q '"at": *"aaaabbbb-1111-2222-3333-444455556666"'
}

@test "rename: POST /rename with target and new name; usage and no-token are loud" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp rename exp-web cross_model
    [ "$status" -eq 0 ]
    [[ "$output" == *'is now "cross_model"'* ]]
    grep '/rename' "$MOCK_LOG" | grep -q '"target": *"exp-web"'
    grep '/rename' "$MOCK_LOG" | grep -q '"name": *"cross_model"'
    run run_romp rename only-one-arg
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp rename"* ]]
    unset ROMP_SERVE_TOKEN
    run run_romp rename exp-web cross_model
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel isn't running"* ]]
}

@test "move: POST /move with target and dir; a relative dir is resolved against the caller's cwd; usage, queued and no-token are loud" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp move exp-web /srv/notes-api/web
    [ "$status" -eq 0 ]
    [[ "$output" == *'"exp-web" now works in /srv/notes-api/web'* ]]
    grep '/move' "$MOCK_LOG" | grep -q '"target": *"exp-web"'
    grep '/move' "$MOCK_LOG" | grep -q '"dir": *"/srv/notes-api/web"'
    # a relative folder means relative to where the caller stands, not to the kernel's default dir
    run run_romp move exp-web sub/dir
    [ "$status" -eq 0 ]
    grep '/move' "$MOCK_LOG" | grep -q "\"dir\": *\"$WORK_DIR/sub/dir\""
    # a mid-turn session parks the move: the CLI says so instead of claiming it happened
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
[[ " $* " == *" --config - "* ]] && cat >/dev/null   # drain the piped token config (see _stub_curl)
echo '{"ok": true, "id": "11111111-2222-3333-4444-555555555555", "queued": true, "dir": "/srv/notes-api/web"}'
MOCK
    chmod +x "$MOCK_DIR/curl"
    run run_romp move exp-web /srv/notes-api/web
    [ "$status" -eq 0 ]
    [[ "$output" == *"queued"* ]]
    # a refusal rides the kernel's own words
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
[[ " $* " == *" --config - "* ]] && cat >/dev/null   # drain the piped token config (see _stub_curl)
echo '{"ok": false, "error": "directory not found: /nowhere"}'
MOCK
    chmod +x "$MOCK_DIR/curl"
    run run_romp move exp-web /nowhere
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused — directory not found: /nowhere"* ]]
    run run_romp move only-one-arg
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp move"* ]]
    unset ROMP_SERVE_TOKEN
    run run_romp move exp-web /srv/notes-api/web
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel isn't running"* ]]
}

@test "color: POST /color with target and a literal hex; prints the new color" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp color exp-web '#1EA1EB'
    [ "$status" -eq 0 ]
    [[ "$output" == *'is now #1EA1EB'* ]]
    grep '/color' "$MOCK_LOG" | grep -q '"target": *"exp-web"'
    grep '/color' "$MOCK_LOG" | grep -q '"bg": *"#1EA1EB"'
}

@test "color: a slot digit resolves through the kernel's palette-colors mirror; no mirror is loud" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    # the mirror the kernel writes at boot: bg<TAB>fg per line; slot 3 = line 3's first field
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '#AA0000\twhite\n#00BB00\tblack\n#0000CC\twhite\n' > "$XDG_STATE_HOME/romp/palette-colors"
    run run_romp color exp-api 3
    [ "$status" -eq 0 ]
    [[ "$output" == *'is now #0000CC'* ]]
    grep '/color' "$MOCK_LOG" | grep -q '"target": *"exp-api"'
    grep '/color' "$MOCK_LOG" | grep -q '"bg": *"#0000CC"'
    # a missing mirror never falls back to a built-in set — the kernel writes it at boot
    rm "$XDG_STATE_HOME/romp/palette-colors"
    run run_romp color exp-api 3
    [ "$status" -eq 1 ]
    [[ "$output" == *"palette mirror"* ]]
}

@test "tag: POST /tag carries the name and the whole --add list" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp tag workers --add exp-web exp-api --color '#54B204'
    [ "$status" -eq 0 ]
    [[ "$output" == *'romp tag: "workers"'* ]]
    grep '/tag' "$MOCK_LOG" | grep -q '"name": *"workers"'
    grep '/tag' "$MOCK_LOG" | grep -q '"add": *\["exp-web", *"exp-api"\]'
    grep '/tag' "$MOCK_LOG" | grep -q '"color": *"#54B204"'
}

@test "tag: a bare name reads the tag — GET /views, never a POST that could create" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp tag workers
    grep -q '/views' "$MOCK_LOG"
    [ "$(grep -c '/tag' "$MOCK_LOG")" -eq 0 ]
  [ "$status" -eq 1 ] && [[ "$output" == *"no tag named"* ]]   # the mock's -w trailer parsed: a good 200 body reaches the read (review pin)
}

@test "tag: --host rides the payload (an edit on an attached kernel's store)" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp tag team --host alpha --add exp-web
    [ "$status" -eq 0 ]
    grep '/tag' "$MOCK_LOG" | grep -q '"host": *"alpha"'
    # …and --host with no edit flag is a usage error: v0 reads stay local (the menu shows the union)
    run run_romp tag team --host alpha
    [ "$status" -eq 2 ]
    [[ "$output" == *"--host goes with an edit"* ]]
}

@test "watch-pr: posts pr+repo+session; self needs ROMP_SID; usage errors exit 2" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run env ROMP_SID=11111111-2222-3333-4444-555555555555 "$ROMP_SCRIPT" watch-pr 7 --repo TESTORG/testrepo
    [ "$status" -eq 0 ]
    [[ "$output" == *"watching TESTORG/testrepo#7"* ]]
    grep '/watch-pr' "$MOCK_LOG" | grep -q '"pr": *7'
    grep '/watch-pr' "$MOCK_LOG" | grep -q '"repo": *"TESTORG/testrepo"'
    grep '/watch-pr' "$MOCK_LOG" | grep -q '"id": *"11111111-2222-3333-4444-555555555555"'
    # --session overrides self and rides as a NAME
    run env ROMP_SID= "$ROMP_SCRIPT" watch-pr 8 --repo TESTORG/testrepo --session web
    [ "$status" -eq 0 ]
    grep '/watch-pr' "$MOCK_LOG" | grep -q '"name": *"web"'
    # outside a session with no --session: a loud usage refusal, never a silent guess
    run env ROMP_SID= "$ROMP_SCRIPT" watch-pr 9 --repo TESTORG/testrepo
    [ "$status" -eq 2 ]
    [[ "$output" == *"--session <name> required"* ]]
    run run_romp watch-pr
    [ "$status" -eq 2 ]
}

@test "watch-pr: a refused registration is relayed, never reported as watching" {
    # the kernel refuses a watch whose save failed (ok:false, retryable): the CLI prints that
    # refusal and exits non-zero — the caller must never read "watching" for a watch nobody holds
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run env ROMP_SID=11111111-2222-3333-4444-555555555555 MOCK_CURL_WATCH_PR_REFUSE=1 "$ROMP_SCRIPT" watch-pr 7 --repo TESTORG/testrepo
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp watch-pr: refused — the watch could not be saved ([Errno 28]"* ]]
    [[ "$output" != *"romp watch-pr: watching"* ]]
}

@test "tag: --rename rides the payload and counts as an edit" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp tag team --rename crew
    [ "$status" -eq 0 ]
    grep '/tag' "$MOCK_LOG" | grep -q '"rename": *"crew"'
    run run_romp tag team --host alpha --rename crew
    [ "$status" -eq 0 ]
    grep '/tag' "$MOCK_LOG" | grep -q '"host": *"alpha"'
}

@test "tag: the pre-rename group verb still works, posting the new /tag route" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp group workers --add exp-web
    [ "$status" -eq 0 ]
    grep '/tag' "$MOCK_LOG" | grep -q '"name": *"workers"'
}

# Helper — a stub kernel for `romp tag`, with REAL curl in front of it: answers GET /views and POST
# /tag with the given HTTP status and body (GET /sessions with an empty list, so members print as
# ids) on a free loopback port announced through a file written after the bind (the
# romp-headless.bats pattern), and serves until teardown reaps it — a listing is two GETs. A body
# argument starting with `@` names a file to serve, for a body too large to ride argv. The curl mock
# above is the wrong stand-in here: the subject is what the CLI makes of a status that `curl -sf`
# used to swallow, and a mock that emulates -w would be testing its own emulation.
_stub_tag_kernel() {   # $1 = HTTP status for GET /views and POST /tag, $2 = its body (or @file)
    python3 - "$1" "$2" "$TEST_DIR/port" <<'PY' &
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
code, arg, portfile = int(sys.argv[1]), sys.argv[2], sys.argv[3]
body = open(arg[1:], "rb").read() if arg.startswith("@") else arg.encode()
class H(BaseHTTPRequestHandler):
    def _answer(self, out, st):
        self.send_response(st); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def do_GET(self):
        out, st = (body, code) if self.path.startswith("/views") else (b"[]", 200)
        self._answer(out, st)
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))   # drain, so curl's write lands
        out, st = (body, code) if self.path.startswith("/tag") else (b"not found", 404)
        self._answer(out, st)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.serve_forever()
PY
    TAG_KERNEL_PID=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")"
}

@test "tag: a kernel that answered 503 with its reason is repeated in its words, never called unreachable" {
    # the tag store unreadable under a cold cache: the kernel answers GET /views with a 503 carrying
    # {ok:false, retryable, error} (a polling peer needs the non-200 to keep its last reading).
    # `curl -sf` threw that body away and told the person to restart a kernel that was up and
    # explaining itself — and a restart cannot mend a disk fault
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 503 '{"ok": false, "retryable": true, "error": "the tag store could not be read (read failed: [Errno 5] Input/output error) — retry"}'
    run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the tag store could not be read (read failed: [Errno 5] Input/output error) — retry"* ]]
    [[ "$output" != *"retry — retry"* ]]      # a text that already says retry is not told twice
    [[ "$output" != *"not reachable"* ]]
    # the bare-name read is the same GET and says the same — not "no tag named"
    run run_romp tag workers
    [ "$status" -eq 1 ]
    [[ "$output" == *"the tag store could not be read"* ]]
    [[ "$output" != *"not reachable"* ]]
    [[ "$output" != *"no tag named"* ]]
}

@test "tag: a retryable refusal whose text does not say so gets 'retry' added" {
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 503 '{"ok": false, "retryable": true, "error": "the tag store is being rebuilt"}'
    run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the tag store is being rebuilt — retry"* ]]
}

@test "tag: a 2xx answer still lists — the status-reading GET changes nothing on the good path" {
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 200 '{"active": "all", "tags": [{"id": "t1", "name": "workers", "color": "#54B204", "members": ["11111111-2222-3333-4444-555555555555"]}]}'
    run run_romp tag
    [ "$status" -eq 0 ]
    [[ "$output" == *"active view: All"* ]]
    [[ "$output" == *"workers  #54B204  1 member: 11111111-2222-3333-4444-555555555555"* ]]
    run run_romp tag workers
    [ "$status" -eq 0 ]
    [[ "$output" == *"workers  #54B204  1 member"* ]]
    run run_romp tag --json
    [ "$status" -eq 0 ]
    [ "$output" = '{"active": "all", "tags": [{"id": "t1", "name": "workers", "color": "#54B204", "members": ["11111111-2222-3333-4444-555555555555"]}]}' ]
}

@test "tag: nothing listening on the port is still 'kernel not reachable'" {
    export ROMP_SERVE_TOKEN=testtok
    local port; free_port port
    ROMP_KERNEL_PORT="$port" run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: kernel not reachable on :$port (is romp running?)"* ]]
    ROMP_KERNEL_PORT="$port" run run_romp tag --json
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "tag --json: a non-2xx JSON answer is printed as is — a script reads ok:false and the reason — and exits 1" {
    # --json hands a script the kernel's answer; a refusal is still that answer, and it already says
    # ok:false and why, so the script needs no second parser. Only a non-JSON answer is said as prose.
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 503 '{"ok": false, "retryable": true, "error": "the tag store could not be read (read failed: [Errno 5] Input/output error) — retry"}'
    run run_romp tag --json
    [ "$status" -eq 1 ]
    [ "$output" = '{"ok": false, "retryable": true, "error": "the tag store could not be read (read failed: [Errno 5] Input/output error) — retry"}' ]
}

@test "tag: a 403 is a refused token, named as such (the kernel's plain-text answer is not JSON)" {
    # what the kernel answers a GET /views carrying another kernel's token TODAY; -f read it as dead
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 403 'forbidden: token required'
    run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the kernel on :$ROMP_KERNEL_PORT refused the serve token (HTTP 403)"* ]]
    [[ "$output" != *"not reachable"* ]]
    # --json has no JSON to print: the same prose line
    run run_romp tag --json
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused the serve token (HTTP 403)"* ]]
}

@test "tag: a non-2xx with no JSON reason prints the status with what came with it, or says nothing came" {
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 500 '<h1>Internal Server Error</h1>'
    run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the kernel on :$ROMP_KERNEL_PORT answered HTTP 500: <h1>Internal Server Error</h1>"* ]]
    [[ "$output" != *"not reachable"* ]]
    kill "$TAG_KERNEL_PID"; wait "$TAG_KERNEL_PID" 2>/dev/null || true; rm -f "$TEST_DIR/port"
    _stub_tag_kernel 502 ''
    run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the kernel on :$ROMP_KERNEL_PORT answered HTTP 502 with no explanation"* ]]
}

@test "tag write: a POST /tag the kernel answered 400 with its reason is repeated in its words, never called unreachable" {
    # the kernel refuses a bad name or payload with a 400 and a JSON reason; on the write leg `curl -sf`
    # still folded that into "not reachable" after the read leg had learned better (review fix)
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 400 '{"ok": false, "error": "a tag name is 1 to 40 characters"}'
    run run_romp tag workers --add exp-web
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: a tag name is 1 to 40 characters"* ]]
    [[ "$output" != *"not reachable"* ]]
    [[ "$output" != *testtok* ]]           # the serve token rides a header and is in no printed line
    # every edit posts through the same call: --delete says the same
    run run_romp tag workers --delete
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: a tag name is 1 to 40 characters"* ]]
    [[ "$output" != *"not reachable"* ]]
    [[ "$output" != *testtok* ]]
}

@test "tag write: a 403 on POST /tag is a refused token, named as such" {
    export ROMP_SERVE_TOKEN=testtok
    _stub_tag_kernel 403 'forbidden: token required'
    run run_romp tag workers --add exp-web
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the kernel on :$ROMP_KERNEL_PORT refused the serve token (HTTP 403)"* ]]
    [[ "$output" != *"not reachable"* ]]
    [[ "$output" != *testtok* ]]
}

@test "tag write: nothing listening on the port is still 'kernel not reachable'" {
    export ROMP_SERVE_TOKEN=testtok
    local port; free_port port
    ROMP_KERNEL_PORT="$port" run run_romp tag workers --add exp-web
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: kernel not reachable on :$port (is romp running?)"* ]]
    [[ "$output" != *testtok* ]]
}

@test "tag: an oversized answer is echoed bounded when it is not JSON, and still read when it is" {
    # a body over 128 KB cannot ride argv (E2BIG), so the JSON parse reads it from stdin; and the prose
    # echo of a body that is not JSON shows the status plus its first 2 KB, then how much more there was
    export ROMP_SERVE_TOKEN=testtok
    python3 -c 'import sys; sys.stdout.write("<h1>Internal Server Error</h1>" + "x" * 200000)' > "$TEST_DIR/big"
    _stub_tag_kernel 500 "@$TEST_DIR/big"
    run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the kernel on :$ROMP_KERNEL_PORT answered HTTP 500: <h1>Internal Server Error</h1>xxx"* ]]
    [[ "$output" == *" ... (197982 more bytes)"* ]]
    [ "${#output}" -lt 2300 ]
    [[ "$output" != *"not reachable"* ]]
    kill "$TAG_KERNEL_PID"; wait "$TAG_KERNEL_PID" 2>/dev/null || true; rm -f "$TEST_DIR/port"
    python3 -c 'import json, sys; sys.stdout.write(json.dumps({"ok": False, "error": "the tag store could not be read", "detail": "y" * 200000}))' > "$TEST_DIR/bigjson"
    _stub_tag_kernel 503 "@$TEST_DIR/bigjson"
    run run_romp tag
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp tag: the tag store could not be read"* ]]
    [ "${#output}" -lt 200 ]
}

@test "color/tag: usage errors exit 2" {
    run run_romp color
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp color"* ]]
    run run_romp color exp-web '#1EA1EB' extra
    [ "$status" -eq 2 ]
    run run_romp tag workers stray-word
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp tag"* ]]
    run run_romp tag --add exp-web
    [ "$status" -eq 2 ]
    run run_romp tag workers --color
    [ "$status" -eq 2 ]
    run run_romp tag --json workers
    [ "$status" -eq 2 ]
}

@test "color/group: no kernel token is a loud exit 1, and no API call is made" {
    _stub_curl
    touch "$MOCK_LOG"
    run run_romp color exp-web '#1EA1EB'
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel isn't running"* ]]
    run run_romp group workers --add exp-web
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel isn't running"* ]]
    [ "$(grep -Ec '/(color|group)' "$MOCK_LOG")" -eq 0 ]
}

@test "fork: usage errors exit 2; no kernel token is a loud exit 1" {
    touch "$MOCK_LOG"
    run run_romp fork
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp fork"* ]]
    run run_romp fork only-parent
    [ "$status" -eq 2 ]
    run run_romp fork exp-web new-name extra-arg
    [ "$status" -eq 2 ]
    run run_romp fork --at "" exp-web new-name
    [ "$status" -eq 2 ]
    # hermetic env has no serve token: the failure names the kernel, and no API call is made
    run run_romp fork exp-web new-name
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel isn't running"* ]]
    [ "$(grep -c '/fork' "$MOCK_LOG")" -eq 0 ]
}

@test "new --tag rides the /send tag field; needs -m; bad labels exit 2" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp new -m "nightly briefing body" --tag nightly-optimizer ideabox
    [ "$status" -eq 0 ]
    # the send payload carries the tag as a FIELD — the kernel appends the marker itself
    grep '/send' "$MOCK_LOG" | grep -q '"tag": *"nightly-optimizer"'
    run run_romp new --tag nightly-optimizer ideabox
    [ "$status" -eq 2 ]
    [[ "$output" == *"--tag needs -m"* ]]
    run run_romp new -m "text" --tag "two words" ideabox
    [ "$status" -eq 2 ]
    [[ "$output" == *"--tag must be one word"* ]]
}

@test "new --in / parent: the payload carries the tags and the calling session's ROMP_SID; --no-inherit withholds the parent" {
    # tab groups are tags (the user 2026-09-04): run from inside a romp session, `romp new` names
    # that session as the new one's parent (its STABLE sid, ROMP_SID — never the transcript fsid)
    # so the kernel copies its tags onto the child; --in <tag> joins tags by name, repeatable.
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    export ROMP_SID=11111111-2222-3333-4444-555555555555
    run run_romp new --in pool --in infra ideabox
    [ "$status" -eq 0 ]
    grep '/new' "$MOCK_LOG" | grep -q '"tags": \["pool", "infra"\]'
    grep '/new' "$MOCK_LOG" | grep -q '"parent": "11111111-2222-3333-4444-555555555555"'
    # the stub acks with NO tags echo — the older-kernel warning, naming what was dropped (the --in,
    # not model/effort) and what to do instead
    [[ "$output" == *"did not acknowledge --in"* ]]
    [[ "$output" == *"romp tag <tag> --add ideabox"* ]]
    [[ "$output" != *"model/effort"* ]]
    # --no-inherit: no parent in the payload, and a bare ack is then no warning at all
    : > "$MOCK_LOG"
    run run_romp new --no-inherit ideabox
    [ "$status" -eq 0 ]
    run bash -c "grep '/new' '$MOCK_LOG' | grep -q '\"parent\"'"
    [ "$status" -ne 0 ]
    run run_romp new --no-inherit ideabox
    [[ "$output" != *"WARNING"* ]]
    # outside a session there is no parent to name
    unset ROMP_SID
    : > "$MOCK_LOG"
    run run_romp new ideabox
    [ "$status" -eq 0 ]
    run bash -c "grep '/new' '$MOCK_LOG' | grep -q '\"parent\"'"
    [ "$status" -ne 0 ]
    run bash -c "grep '/new' '$MOCK_LOG' | grep -q '\"tags\"'"
    [ "$status" -ne 0 ]
}

@test "new --in: the kernel's tags echo is reported, and a name it did not apply is a loud warning with the reason" {
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
# drain the token config romp pipes in (`_romp_token_cfg | curl --config - …`): real curl always reads
# it, but a mock that exits first hands the writer SIGPIPE, and under the script's pipefail that read
# as a false "not reachable" — one random kernel-API test failed per run (2026-09-04)
[[ " $* " == *" --config - "* ]] && cat >/dev/null
url=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
if [[ "$url" == */new ]]; then
  echo '{"ok": true, "id": "66666666-7777-8888-9999-000000000000", "dir": "/tmp/x", "tags": ["pool"], "tagError": "two tags are named \"twin\""}'
else
  echo '{"ok": true}'
fi
MOCK
    chmod +x "$MOCK_DIR/curl"
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp new --in pool --in twin ideabox
    [ "$status" -eq 0 ]
    [[ "$output" == *"applied tags pool"* ]]
    [[ "$output" == *"did not apply --in twin"* ]]
    [[ "$output" == *"two tags are named"* ]]
    [[ "$output" != *"did not acknowledge"* ]]
}

@test "new --in: needs a value, is refused with -t (tag a terminal session afterwards), and help lists it" {
    run run_romp new --in
    [ "$status" -eq 2 ]
    [[ "$output" == *"[--in <tag>]"* ]]
    touch "$MOCK_LOG"
    run run_romp new -t --in pool ideabox
    [ "$status" -eq 2 ]
    [[ "$output" == *"--in needs an SDK or Codex session; a terminal session cannot join a group"* ]]
    [[ "$output" == *"romp tag pool --add ideabox"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
    run run_romp help
    [[ "$output" == *"romp new --in <tag> <name>"* ]]
    [[ "$output" == *"romp new --no-inherit <name>"* ]]
}

@test "new (in a session, no --in): a kernel that drops the parent ask is warned about the inherited tags; an empty echo prints nothing" {
    # the parent-only ask — ROMP_SID set, no --in. A bare {"ok": true} means an older kernel never
    # saw `parent`: say so, naming the inherited tags (not model/effort). A kernel echoing
    # "tags": [] answered the ask with nothing to inherit, which is not worth a line.
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    export ROMP_SID=11111111-2222-3333-4444-555555555555
    run run_romp new ideabox
    [ "$status" -eq 0 ]
    grep '/new' "$MOCK_LOG" | grep -q '"parentAuto": true'
    [[ "$output" == *"did not acknowledge the parent's tags"* ]]
    [[ "$output" == *"romp tag <tag> --add ideabox"* ]]
    [[ "$output" != *"model/effort"* ]]
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
[[ " $* " == *" --config - "* ]] && cat >/dev/null   # drain the piped token config (see _stub_curl)
echo '{"ok": true, "id": "66666666-7777-8888-9999-000000000000", "dir": "/tmp/x", "tags": [], "tagsRequested": [], "tagsApplied": []}'
MOCK
    chmod +x "$MOCK_DIR/curl"
    run run_romp new ideabox
    [ "$status" -eq 0 ]
    [[ "$output" != *"applied tags"* ]]
    [[ "$output" != *"WARNING"* ]]
    # …while an inherited tag IS reported
    sed -i 's/"tags": \[\]/"tags": ["pool"]/' "$MOCK_DIR/curl"
    run run_romp new ideabox
    [[ "$output" == *"applied tags pool"* ]]
}

@test "new --in: a name the kernel applied under its stored spelling is 'applied as', never a false 'did not apply'" {
    # the store trims and clamps tag names; the kernel echoes each --in's stored spelling by position
    # (tagsApplied) — a respelled name was applied, only a null slot was refused
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
[[ " $* " == *" --config - "* ]] && cat >/dev/null   # drain the piped token config (see _stub_curl)
url=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
if [[ "$url" == */new ]]; then
  echo '{"ok": true, "id": "66666666-7777-8888-9999-000000000000", "dir": "/tmp/x", "tags": ["pool", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "tagsRequested": [" pool", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "twin"], "tagsApplied": ["pool", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", null], "tagError": "two tags are named \"twin\""}'
else
  echo '{"ok": true}'
fi
MOCK
    chmod +x "$MOCK_DIR/curl"
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    run run_romp new --in " pool" --in aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --in twin ideabox
    [ "$status" -eq 0 ]
    [[ "$output" == *'--in applied " pool" as pool, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" as aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'* ]]
    [[ "$output" == *"did not apply --in twin (two tags are named"* ]]
    [[ "$output" != *"did not apply --in  pool"* ]]
    [[ "$output" != *"did not apply --in aaaa"* ]]
    # against a kernel with only the `tags` echo (no positional pair) the name match still stands
    sed -i 's/, "tagsRequested".*"tagError"/, "tagError"/' "$MOCK_DIR/curl"
    run run_romp new --in pool --in twin ideabox
    [[ "$output" == *"did not apply --in twin"* ]]
    [[ "$output" != *"did not apply --in pool"* ]]
}

@test "new (in a session): an auto parent the kernel does not know is one plain notice, never an error" {
    # the CLI's parent is ROMP_SID, sent as parentAuto; a kernel that never ran this session (a
    # scratch kernel on another port) creates the session untagged and echoes parentIgnored — the
    # CLI says so once and warns about nothing
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
[[ " $* " == *" --config - "* ]] && cat >/dev/null   # drain the piped token config (see _stub_curl)
url=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
if [[ "$url" == */new ]]; then
  echo '{"ok": true, "id": "66666666-7777-8888-9999-000000000000", "dir": "/tmp/x", "tags": [], "tagsRequested": [], "tagsApplied": [], "parentIgnored": "11111111-2222-3333-4444-555555555555"}'
else
  echo '{"ok": true}'
fi
MOCK
    chmod +x "$MOCK_DIR/curl"
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    export ROMP_SID=11111111-2222-3333-4444-555555555555
    run run_romp new ideabox
    [ "$status" -eq 0 ]
    [[ "$output" == *'started "ideabox"'* ]]
    [[ "$output" == *'"ideabox" inherited no tags: the kernel that answered did not run this shell'"'"'s session (11111111-2222-3333-4444-555555555555)'* ]]
    [[ "$output" != *"--in applied"* ]]
    [[ "$output" != *"already running"* ]]
    [[ "$output" != *"WARNING"* ]]
    [[ "$output" != *"applied tags"* ]]
}

@test "new (in a session): the unknown-parent notice follows the echo — --in still applied, an already-running name inherited nothing" {
    # the notice used to say the session "starts in no tags" whenever parentIgnored came back, and
    # the very next line then said "applied tags infra" (an explicit --in lands beside an ignored
    # parent) or "is already running" (nothing starts). Each line is derived from the ack now.
    cat > "$MOCK_DIR/curl" << 'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
[[ " $* " == *" --config - "* ]] && cat >/dev/null   # drain the piped token config (see _stub_curl)
url=""
for a in "$@"; do [[ "$a" == http* ]] && url="$a"; done
if [[ "$url" == */new ]]; then
  echo '{"ok": true, "id": "66666666-7777-8888-9999-000000000000", "dir": "/tmp/x", "tags": ["infra", "qa"], "tagsRequested": ["infra", "qa"], "tagsApplied": ["infra", "qa"], "parentIgnored": "11111111-2222-3333-4444-555555555555"}'
else
  echo '{"ok": true}'
fi
MOCK
    chmod +x "$MOCK_DIR/curl"
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    export ROMP_SID=11111111-2222-3333-4444-555555555555
    # --in beside the ignored parent: inherited nothing, but the named tags landed — one line says both
    run run_romp new --in infra --in qa ideabox
    [ "$status" -eq 0 ]
    [[ "$output" == *'started "ideabox"'* ]]
    [[ "$output" == *'"ideabox" inherited no tags: the kernel that answered did not run this shell'"'"'s session (11111111-2222-3333-4444-555555555555); --in applied: infra, qa'* ]]
    [[ "$output" == *"applied tags infra, qa"* ]]
    [[ "$output" != *"starts in no tags"* ]]
    [[ "$output" != *"already running"* ]]
    # a refused --in (a null slot) is not "applied": the notice names only what landed
    sed -i 's/"tagsApplied": \["infra", "qa"\]/"tagsApplied": ["infra", null]/' "$MOCK_DIR/curl"
    run run_romp new --in infra --in qa ideabox
    [[ "$output" == *"; --in applied: infra"* ]]
    [[ "$output" != *"--in applied: infra, qa"* ]]
    # the name was already running: nothing starts and nothing is inherited (no creation event); the
    # notice says so once, after the "is already running" line, and never "starts"
    sed -i 's/"dir": "\/tmp\/x", "tags": \["infra", "qa"\], "tagsRequested": \["infra", "qa"\], "tagsApplied": \["infra", null\]/"existing": true, "tags": ["pool"], "tagsRequested": [], "tagsApplied": []/' "$MOCK_DIR/curl"
    run run_romp new ideabox
    [ "$status" -eq 0 ]
    [[ "$output" == *'"ideabox" is already running; see the dashboard (romp)'* ]]
    [[ "$output" == *'"ideabox" inherited no tags: it was already running, and the kernel that answered did not run this shell'"'"'s session (11111111-2222-3333-4444-555555555555)'* ]]
    [[ "$output" == *"applied tags pool"* ]]
    [[ "$output" != *"starts in no tags"* ]]
    [[ "$output" != *"--in applied"* ]]
    [[ "$output" != *"WARNING"* ]]
}

@test "new -m: a failed send is loud and names the retry (the session IS up)" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    export MOCK_CURL_FAIL_SEND=1
    run run_romp new -m "look into the flaky test" ideabox
    [ "$status" -eq 1 ]
    [[ "$output" == *"did NOT land"* ]]
    [[ "$output" == *"romp send ideabox"* ]]
}

@test "new: a kernel 400 surfaces the kernel's own refusal, never 'not reachable'" {
    # every /new validation error (reserved env names, bad names, bad values) is a 400 whose
    # body names the problem — masked as a connection failure, the user retypes forever
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    export MOCK_CURL_NEW_400=1
    run run_romp new --env ROMP_SID=x web
    [ "$status" -eq 1 ]
    [[ "$output" == *"ROMP_SID is reserved"* ]]
    [[ "$output" != *"not reachable"* ]]
}

@test "new: a real connection failure still says 'not reachable'" {
    _stub_curl
    touch "$MOCK_LOG"
    export ROMP_SERVE_TOKEN=testtok
    export MOCK_CURL_FAIL_NEW=1
    run run_romp new web
    [ "$status" -eq 1 ]
    [[ "$output" == *"not reachable"* ]]
}

@test "help lists new -m" {
    run run_romp help
    [[ "$output" == *"romp new -m <text> <name>"* ]]
}

@test "new -t: terminal session named by the argument, claude exec'd with --name + --session-id" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s myproject' "$MOCK_LOG"
    grep -q 'tmux set -t myproject @romp 1' "$MOCK_LOG"
    # The pill carries the session name, and a self-assigned --session-id lets
    # romp record name<->id up front (names map → resume picker). The command is
    # handed to the pane with respawn-pane (atomic), not typed with send-keys.
    # The romp identity rides the CLI's environment on this backend too (the user 2026-08-16):
    # external tools attribute authors env-first (ROMP_SESSION_NAME) instead of asking tmux.
    grep -qE 'tmux respawn-pane -k -t myproject exec env ROMP_SID=[0-9a-f-]{36} ROMP_SESSION_NAME="myproject" claude --name "myproject" --session-id [0-9a-f-]{36}' "$MOCK_LOG"
    grep -q 'tmux attach-session -t myproject' "$MOCK_LOG"
}

# The tmux SERVER's globals are what a new pane inherits. romp holds no API key (2026-09-08), so a leftover
# ANTHROPIC_API_KEY there is refused before the pane exists: the kernel alone refusing to boot on the same
# variable left this path open (a review find), and a quiet scrub would hide the misconfiguration.
_stale_server_globals() {
    export MOCK_TMUX_GLOBALS_FILE="$TEST_DIR/mock_globals.txt"
    printf '%s\n' "OP_SERVICE_ACCOUNT_TOKEN=synthetic-op-token" "OP_SESSION_acct=synthetic-session" \
        "ANTHROPIC_API_KEY=synthetic-stale-key" "PATH=/usr/bin" "HOME=/nonexistent" > "$MOCK_TMUX_GLOBALS_FILE"
}

@test "new -t: a leftover ANTHROPIC_API_KEY in the tmux server's globals refuses the session, loudly, without scrubbing" {
    _stale_server_globals
    run run_romp new -t myproject
    [ "$status" -eq 1 ]
    [[ "$output" == *"ANTHROPIC_API_KEY"* ]]
    [[ "$output" == *"apiKeyHelper"* ]]
    [[ "$output" != *"synthetic-stale-key"* ]]
    run grep -q 'new-session' "$MOCK_LOG"     # `run` + status, not a bare `! grep`: `!` is exempt from set -e mid-test
    [ "$status" -ne 0 ]
    run grep -q 'set-environment' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

@test "new -t: clean server globals (op's names, a login token) start the session and touch nothing" {
    export MOCK_TMUX_GLOBALS_FILE="$TEST_DIR/mock_globals.txt"
    printf '%s\n' "OP_SERVICE_ACCOUNT_TOKEN=synthetic-op-token" "ANTHROPIC_AUTH_TOKEN=synthetic-bearer" \
        "PATH=/usr/bin" > "$MOCK_TMUX_GLOBALS_FILE"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'new-session' "$MOCK_LOG"
    ! grep -q 'set-environment' "$MOCK_LOG"
}

@test "new -t on a 2.1.224+ claude: inbound-accept setting + @romp-inbound-accept tag" {
    # The kernel's inbox-socket delivery leg fires only for launches that passed the
    # CLI's inbound-accept setting (an unverifiable sender's mail can otherwise be
    # held and silently expire); the tag records exactly those launches — one code
    # path writes both, so they can never disagree. Setup pins claude at 2.1.226.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -qF -- "--settings '{\"crossSessionInbound\":\"accept\"}'" "$MOCK_LOG"
    grep -q 'tmux set -t myproject @romp-inbound-accept 1' "$MOCK_LOG"
}

@test "new -t on an old claude: no setting, no tag, one upgrade nudge" {
    _stub_claude "2.1.220"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    [[ "$output" == *"claude update"* ]]     # the informative floor line, not a failure
    # (checked BEFORE the greps below: `run` clobbers $output, so the output assertion must come first)
    run grep -q -- '--settings' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    run grep -q -- '@romp-inbound-accept' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

@test "the pane's exec line runs claude through env: an assignment-led exec is a command named ROMP_SID=… to every shell" {
    # zsh, bash and sh all answer `exec FOO=1 cmd` with "command not found: FOO=1" (status 127): from 2026-08-16 to
    # 2026-09-11 a terminal session's pane died the instant it was spawned (a hermetic kernel lab found it)
    _stub_claude 9.9.9
    run "$ROMP_SCRIPT" new -t --detach myproject
    [ "$status" -eq 0 ]
    grep -qE 'tmux respawn-pane -k -t myproject exec env ROMP_SID=\S+ ROMP_SESSION_NAME="myproject" claude ' "$MOCK_LOG"
    # (`run` and a status check: a bare `!`-inverted command is exempt from errexit and the ERR trap, so it
    # would assert nothing here)
    run grep -qE 'respawn-pane .* exec ROMP_SID=' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    # the rule itself, on the shells a tmux server may run the pane with (`run -127`: the expected exit code,
    # so bats files no command-not-found warning for the very status under test)
    for sh in sh bash zsh; do
        command -v "$sh" >/dev/null || continue
        run -127 "$sh" -c 'exec FOO=1 true'
        run "$sh" -c 'exec env FOO=1 true'
        [ "$status" -eq 0 ]
    done
}

@test "a terminal launch is refused when the kernel's tmux socket directory is not this shell's, and proceeds when it is" {
    # T325: a cron job, `sudo -u` or `docker exec` shell resolves tmux's default while the service's kernel dials the
    # runtime-dir server; a session started there would never reach the board. /version says where the kernel's is.
    _stub_claude 9.9.9; _stub_curl
    MOCK_CURL_VERSION='{"tmuxSocketDir":"/elsewhere/romp","tmuxSocketRule":"runtime-dir"}' run "$ROMP_SCRIPT" new -t --detach myproject
    [ "$status" -eq 1 ]
    [[ "$output" == *"refusing to start 'myproject'"* ]]
    [[ "$output" == *"export TMUX_TMPDIR=/elsewhere/romp"* ]]
    run grep -q 'new-session' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    # the kernel on the same directory: the launch proceeds
    MOCK_CURL_VERSION="{\"tmuxSocketDir\":\"$TMUX_TMPDIR\",\"tmuxSocketRule\":\"runtime-dir\"}" run "$ROMP_SCRIPT" new -t --detach myproject
    [ "$status" -eq 0 ]
    grep -q 'new-session -d -s myproject' "$MOCK_LOG"
    # a kernel from before the rule (no tmuxSocketRule) compares nothing
    : > "$MOCK_LOG"
    MOCK_CURL_VERSION='{"kernel_ver":"0"}' run "$ROMP_SCRIPT" new -t --detach myproject2
    [ "$status" -eq 0 ]
    # canonical paths compare: the kernel reporting the same directory with a trailing slash is the same server
    : > "$MOCK_LOG"
    MOCK_CURL_VERSION="{\"tmuxSocketDir\":\"$TMUX_TMPDIR/\",\"tmuxSocketRule\":\"runtime-dir\"}" run "$ROMP_SCRIPT" new -t --detach myproject3
    [ "$status" -eq 0 ]
    grep -q 'new-session -d -s myproject3' "$MOCK_LOG"
}

@test "inside a pane on ANOTHER server the launch is refused (the pane's own \$TMUX wins over TMUX_TMPDIR); on the kernel's server it proceeds" {
    _stub_claude 9.9.9; _stub_curl
    local other="$TEST_DIR/other-server"; mkdir -p "$other/tmux-$(id -u)"
    TMUX="$other/tmux-$(id -u)/default,4242,0" MOCK_CURL_VERSION="{\"tmuxSocketDir\":\"$TMUX_TMPDIR\",\"tmuxSocketRule\":\"runtime-dir\"}" \
        run "$ROMP_SCRIPT" new -t --detach myproject
    [ "$status" -eq 1 ]
    [[ "$output" == *"inside a tmux session on another server ($other)"* ]]
    [[ "$output" == *"a shell outside that tmux session"* ]]
    run grep -q 'new-session' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    TMUX="$TMUX_TMPDIR/tmux-$(id -u)/default,4242,0" MOCK_CURL_VERSION="{\"tmuxSocketDir\":\"$TMUX_TMPDIR\",\"tmuxSocketRule\":\"runtime-dir\"}" \
        run "$ROMP_SCRIPT" new -t --detach myproject
    [ "$status" -eq 0 ]
    grep -q 'new-session -d -s myproject' "$MOCK_LOG"
}

@test "a kernel under a manager from before the socket moved is refused with the refresh, not a cron diagnosis" {
    _stub_claude 9.9.9; _stub_curl
    MOCK_CURL_VERSION='{"tmuxSocketDir":"","tmuxSocketRule":"manager"}' run "$ROMP_SCRIPT" new -t --detach myproject
    [ "$status" -eq 1 ]
    [[ "$output" == *"under a manager from before the socket moved"* ]]
    [[ "$output" == *"romp refresh"* ]]
    [[ "$output" != *"cron job"* ]]
}

@test "launch hands the exec line to respawn-pane, never typed via send-keys (dropped-char bug)" {
    # Regression: a fresh shell flushes its tty input on startup, so send-keys'd
    # keys are dropped — the launch once started `ec claude …` (the "ex" eaten).
    # The exec command must reach the pane atomically (respawn-pane), so the exec
    # line must NEVER appear on a send-keys call.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -qE 'tmux respawn-pane -k -t myproject exec env ROMP_SID=\S+ ROMP_SESSION_NAME="myproject" claude' "$MOCK_LOG"
    ! grep -qE 'send-keys.*exec (env ROMP_SID=\S+ ROMP_SESSION_NAME="[^"]*" )?claude' "$MOCK_LOG"
}

@test "old tmux without copy-mode-position-style still launches claude (no set -e abort)" {
    # Regression: bin/romp sets the cosmetic copy-mode-position-style, added in tmux
    # 3.2. On an older tmux (e.g. a remote host on 3.0) that errors "invalid option",
    # which under `set -e` aborted session creation before the claude launch — the
    # pane was left at a bare shell. The cosmetic set must be guarded so the session
    # still starts. Simulate the old tmux by failing exactly that option.
    export MOCK_TMUX_FAIL_OPT="copy-mode-position-style"
    run run_romp new -t --detach myproject
    [ "$status" -eq 0 ]
    grep -qE 'tmux respawn-pane -k -t myproject exec env ROMP_SID=\S+ ROMP_SESSION_NAME="myproject" claude' "$MOCK_LOG"
}

@test "append-system-prompt: omitted when no working-style prompt is installed" {
    # Default hermetic HOME has no romp-session-prompt.md, so the -f guard skips it.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    ! grep -q -- '--append-system-prompt' "$MOCK_LOG"
}

@test "append-system-prompt: appended (deferred \$(cat ...)) when the prompt is installed" {
    mkdir -p "$HOME/.claude"
    printf 'Working style: be explicit.\n' > "$HOME/.claude/romp-session-prompt.md"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    # The flag carries a deferred cat of the fixed path — the multi-line content
    # stays OUT of the exec line, so the launch shell expands it at exec time.
    grep -F -- "--append-system-prompt \"\$(cat $HOME/.claude/romp-session-prompt.md)\"" "$MOCK_LOG"
    # Still the same single exec line, handed to the pane via respawn-pane.
    grep -qE 'tmux respawn-pane -k -t myproject exec env ROMP_SID=\S+ ROMP_SESSION_NAME="myproject" claude --name "myproject" --session-id [0-9a-f-]{36} --append-system-prompt .*' "$MOCK_LOG"
}

@test "append-system-prompt: also appended on the resume path" {
    mkdir -p "$HOME/.claude"
    printf 'Working style: be explicit.\n' > "$HOME/.claude/romp-session-prompt.md"
    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    grep -F -- "--append-system-prompt \"\$(cat $HOME/.claude/romp-session-prompt.md)\"" "$MOCK_LOG"
}

@test "provisioning pins status-format[0] alongside the session-scoped peers row" {
    # tmux gotcha (2026-06-12): a session-scoped status-format[1] shadows the
    # whole inherited array — without [0] pinned to the global composition the
    # main status row (status-left + windows + status-right) renders EMPTY.
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject status-format\[0\] GLOBAL_ROW0' "$MOCK_LOG"
    grep -q 'tmux set -t myproject status-format\[1\]' "$MOCK_LOG"
}

@test "named session: romp new -t my-task → my-task" {
    run run_romp new -t my-task
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s my-task' "$MOCK_LOG"
    grep -q 'tmux attach-session -t my-task' "$MOCK_LOG"
}

@test "session name sanitization: dots and colons replaced with dashes" {
    run run_romp new -t "my.task:v2"
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s my-task-v2' "$MOCK_LOG"
    grep -qE 'exec env ROMP_SID=\S+ ROMP_SESSION_NAME="my-task-v2" claude --name "my-task-v2"' "$MOCK_LOG"
}

@test "session name sanitization: shell metacharacters folded to dashes (no command injection)" {
    # A name/dir carrying $(), ;, or quotes must NOT survive into the launch
    # command the pane shell runs — every unsafe char becomes '-'. Regression for
    # the command-injection-via-session-name hole.
    run run_romp new -t 'pwn$(touch INJECTED);x"y'
    [ "$status" -eq 0 ]
    local line
    line="$(grep -F 'respawn-pane' "$MOCK_LOG" | grep -F ' claude ')"
    [ -n "$line" ]
    # no shell metacharacters survive in the exec line
    # `run` + status, NOT a bare `! grep`: `!` is exempt from set -e, so mid-test it asserts nothing.
    run grep -qE '[$();]' <<<"$line"
    [ "$status" -ne 0 ]
    # exactly the four quotes that wrap ROMP_SESSION_NAME="<name>" and --name "<name>" (the same
    # sanitized value twice), no injected extras. The fixed --settings tail romp itself appends
    # carries its own JSON quotes — a trusted constant, not name-derived — so strip it first.
    line="${line%%--settings*}"
    [ "$(grep -o '"' <<<"$line" | wc -l | tr -d ' ')" -eq 4 ]
}

@test "interrupt/escape key bindings route the session name through tmux #{q:} quoting" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -F 'bind -n C-c' "$MOCK_LOG"    | grep -qF 'romp-interrupt-reset #{q:session_name}'
    grep -F 'bind -n Escape' "$MOCK_LOG" | grep -qF 'romp-interrupt-reset #{q:session_name}'
    # the unquoted (injectable) form must be gone
    ! grep -qF 'romp-interrupt-reset #{session_name}' "$MOCK_LOG"
}

@test "resume: a session id with shell metacharacters is refused before any launch" {
    # resume_id is typed into `claude --resume <id>`; a non-alphanumeric id must
    # be rejected before a session is created.
    run run_romp resume 'abc;touch INJECTED' --name myproject --detach
    [ "$status" -ne 0 ]
    [[ "$output" == *"invalid session id"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "state dir is created private (0700)" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    local perms
    # GNU stat (-c) first, BSD/macOS stat (-f) as fallback. The reverse order
    # breaks on Linux, where `stat -f` means --file-system and mangles output.
    perms="$(stat -c '%a' "$XDG_STATE_HOME/romp" 2>/dev/null || stat -f '%Lp' "$XDG_STATE_HOME/romp")"
    [ "$perms" = "700" ]
}

# ─── Resume tests ────────────────────────────────────────────────────

@test "resume: bare -r with no resumable sessions is a no-op" {
    # bare -r opens the by-name picker; with an empty names map there is
    # nothing to offer — no session may be created as a side effect. The names
    # dir exists-but-empty (steady state on any machine that ran romp before);
    # a MISSING dir is the silent first-run path, exercised below.
    # NOTE bats/macOS gotcha: a false [[ ]] mid-test is SWALLOWED (only the
    # last command's status fails a test) — assert with simple commands
    # (grep, [ ]) so failures actually fire.
    mkdir -p "$XDG_STATE_HOME/romp/names"
    run run_romp resume
    [ "$status" -eq 0 ]
    grep -q "no resumable sessions" <<<"$output"
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "resume: --resume is a silent alias of resume (agent-facing text names it)" {
    mkdir -p "$XDG_STATE_HOME/romp/names"
    run run_romp resume
    [ "$status" -eq 0 ]
    grep -q "no resumable sessions" <<<"$output"

    run run_romp --resume
    [ "$status" -eq 0 ]
    grep -q "no resumable sessions" <<<"$output"
    [[ "$output" != *"retired"* ]]
}

@test "resume: first run ever (no names dir) exits silently, creating nothing" {
    touch "$MOCK_LOG"    # this path may make no tmux calls at all
    run run_romp resume
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "an unknown bare word is a loud error naming both readings, never a session" {
    # Round 3: commands are bare words, so a word that is not one gets exit 2
    # with the `romp new` fix spelled out — nothing silently becomes a session.
    touch "$MOCK_LOG"    # this path makes no tmux calls at all
    run run_romp foo
    [ "$status" -eq 2 ]
    [[ "$output" == *'unknown command "foo"'* ]]
    [[ "$output" == *"romp new foo"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "resume: the old-kernel revive shape (name --resume id --detach) still works, silently" {
    # A kernel on pre-round-3 code revives tmux sessions as `romp <name> --resume
    # <sid> --detach`; that exact shape must keep working (SILENTLY) until every
    # kernel restarts onto new code — its spawn path swallows stderr.
    run run_romp web --resume abc123-uuid --detach
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q 'tmux new-session -d -s web' "$MOCK_LOG"
    grep -qE 'tmux respawn-pane -k -t web exec env ROMP_SID=abc123-uuid ROMP_SESSION_NAME="web" claude --resume abc123-uuid --name "web"' "$MOCK_LOG"
    ! grep -q 'tmux attach-session' "$MOCK_LOG"
}

@test "resume: explicit session id resumes that conversation" {
    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    grep -q 'tmux respawn-pane -k -t myproject exec env ROMP_SID=abc123-uuid ROMP_SESSION_NAME="myproject" claude --resume abc123-uuid --name "myproject"' "$MOCK_LOG"
}

@test "resume: name collision uniquifies instead of hijacking the session" {
    echo "myproject" > "$MOCK_TMUX_SESSIONS_FILE"

    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    run grep -qE 'tmux attach-session -t myproject$' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    grep -q 'tmux new-session -d -s myproject-2' "$MOCK_LOG"
    grep -qE 'tmux respawn-pane -k -t myproject-2 exec env ROMP_SID=abc123-uuid ROMP_SESSION_NAME="myproject-2" claude --resume abc123-uuid --name "myproject-2"' "$MOCK_LOG"
}

@test "resume: the background picker-check goes through ROMP_POSTAL_BIN, and the stand-in writes nothing" {
    # bin/romp double-forks `romp-postal-service picker-check` on a resume and returns at once;
    # the real service mints ~/.local/state/romp/serve-token when none exists, and did so after
    # teardown had removed TEST_DIR, re-creating it. bin/romp's own directory leads PATH, so the
    # seam is the only way a test can stand in for the service. The setup() stand-in leaves the
    # state dir alone; a recording one for this test shows the resume path reaching the seam —
    # the call is detached, so the check waits (bounded) for its record instead of racing it.
    [ "$ROMP_POSTAL_BIN" = "$MOCK_DIR/romp-postal-service" ]
    run "$ROMP_POSTAL_BIN" picker-check --name myproject --id abc123-uuid
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$HOME/.local/state/romp" ]

    printf '#!/usr/bin/env bash\necho "postal $*" >> "%s"\n' "$TEST_DIR/postal.log" > "$MOCK_DIR/romp-postal-service"
    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    local i; for i in $(seq 1 50); do [ -s "$TEST_DIR/postal.log" ] && break; sleep 0.1; done
    grep -q '^postal picker-check --name myproject --id abc123-uuid$' "$TEST_DIR/postal.log"
}

# ─── Detach tests ────────────────────────────────────────────────────

@test "detach: new -t --detach creates the session but does not attach" {
    run run_romp new -t --detach myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s myproject' "$MOCK_LOG"
    grep -qE 'tmux respawn-pane -k -t myproject exec env ROMP_SID=\S+ ROMP_SESSION_NAME="myproject" claude --name "myproject" --session-id [0-9a-f-]{36}' "$MOCK_LOG"
    # $output is asserted BEFORE the `run grep` below overwrites it with grep's (empty) output.
    [[ "$output" == *"attach with: tmux attach -t myproject"* ]]
    run grep -q 'tmux attach-session' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

@test "detach: --resume + id + detach (the skill conversion path) still works as an alias" {
    run run_romp --resume sess-xyz --detach
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s myproject' "$MOCK_LOG"
    grep -qE 'tmux respawn-pane -k -t myproject exec env ROMP_SID=sess-xyz ROMP_SESSION_NAME="myproject" claude --resume sess-xyz --name "myproject"' "$MOCK_LOG"
    # $output asserted before the `run grep` overwrites it.
    [[ "$output" == *"(detached)"* ]]
    run grep -q 'tmux attach-session' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

# ─── Misc ────────────────────────────────────────────────────────────

@test "unknown option shows error" {
    run run_romp -x
    [ "$status" -eq 2 ]
    [[ "$output" == *"unknown option: -x"* ]]
}

@test "names map: the record writer publishes atomically, so a reader racing the rename hook never sees an empty record" {
    # _romp_record (a launch, the after-rename hook, the title-freeing rewrite) fills a temp in the names
    # directory and moves it into place. The kernel's names writers read the record while the hook
    # rewrites it and publish their edit over what they read, so a writer that truncated before it wrote
    # cost the session its name and cwd. A reader polling the record while the hook rewrites it forty
    # times must never find it empty; the rename must land with the dir and colors intact, and every hook
    # run must reach the writer (the mock tmux answers the sid and logs the send-keys that follows the
    # write), so a hook that never writes cannot pass by leaving the seed alone. The temp is dot-prefixed:
    # the `*` glob _romp_free_title walks the directory with must never see it, and none may be left behind.
    cat > "$MOCK_DIR/tmux" << 'MOCK'
#!/usr/bin/env bash
echo "tmux $*" >> "$MOCK_LOG"
if [[ "$1" == "show" && "$5" == "@romp-session-id" ]]; then echo aaaa1111-bbbb-2222-cccc-333333333333; fi
exit 0
MOCK
    chmod +x "$MOCK_DIR/tmux"
    ndir="$XDG_STATE_HOME/romp/names"
    mkdir -p "$ndir"
    f="$ndir/aaaa1111-bbbb-2222-cccc-333333333333"
    printf 'stale\t%s\t#1EA1EB\twhite\n' "$WORK_DIR" > "$f"
    # the writer: the rename hook, 40 times over; the reader: as many looks as fit meanwhile
    ( for i in $(seq 1 40); do "$ROMP_SCRIPT" _renamed exp-web >/dev/null 2>&1; done ) &
    wpid=$!
    empty=0; reads=0; seen=0
    while kill -0 "$wpid" 2>/dev/null; do
        reads=$((reads + 1))
        [ -s "$f" ] || empty=$((empty + 1))
        for e in "$ndir"/*; do [ "$e" = "$f" ] || seen=$((seen + 1)); done   # the glob _romp_free_title walks
    done
    wait "$wpid"
    [ "$reads" -gt 100 ]
    [ "$empty" -eq 0 ]
    [ "$seen" -eq 0 ]                                                      # the temp never shows to that glob
    [ "$(grep -c '^tmux send-keys' "$MOCK_LOG")" -eq 40 ]                   # every hook run got past the writer
    [ "$(cat "$f")" = "$(printf 'exp-web\t%s\t#1EA1EB\twhite' "$WORK_DIR")" ]   # the rename landed; dir and colors intact
    [ "$(ls -A "$ndir" | grep -vc '^aaaa1111-bbbb-2222-cccc-333333333333$')" -eq 0 ]   # no temp left behind
}

@test "old-kernel spawn shape (--detach <name>) still works, silently" {
    # A kernel on pre-round-3 code spawns dashboard tmux sessions as `romp
    # --detach <name>` with stderr swallowed — the shape must keep working.
    run run_romp --detach oldk
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q 'tmux new-session -d -s oldk' "$MOCK_LOG"
    ! grep -q 'tmux attach-session' "$MOCK_LOG"
}

@test "new: usage errors are loud — missing name, two names, dangling -d" {
    touch "$MOCK_LOG"    # these paths make no tmux calls at all
    run run_romp new
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp new"* ]]
    run run_romp new -t alpha beta
    [ "$status" -eq 2 ]
    run run_romp new -t -d
    [ "$status" -eq 2 ]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "existing session reattaches instead of creating new" {
    echo "myproject" > "$MOCK_TMUX_SESSIONS_FILE"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    run grep -q 'tmux new-session' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    grep -q 'tmux attach-session -t myproject' "$MOCK_LOG"
}

# ─── Identity-color tests ────────────────────────────────────────────

@test "color: first session gets the first palette color + a status dot" {
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #1EA1EB' "$MOCK_LOG"
    # The tab dot is seeded blue (ready) at launch; the status hook drives
    # it thereafter.
    grep -q 'tmux set -t myproject @romp-emoji 🔵' "$MOCK_LOG"
}

@test "color: second session gets a different color from the first" {
    echo "other" > "$MOCK_TMUX_SESSIONS_FILE"
    echo "other=#1EA1EB" > "$MOCK_TMUX_IDENTITY_FILE"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #54B204' "$MOCK_LOG"
}

@test "color: third session gets teal (colorblind-tuned order: blue, green, teal)" {
    # The 3rd slot is teal #4EA8A9, the more colorblind-friendly of teal/purple against
    # the blue+green pair (the user 2026-06-12) — pin both earlier colors as taken.
    printf '%s\n' "s1" "s2" > "$MOCK_TMUX_SESSIONS_FILE"
    printf '%s\n' "s1=#1EA1EB" "s2=#54B204" > "$MOCK_TMUX_IDENTITY_FILE"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #4EA8A9' "$MOCK_LOG"
}

@test "color: a kernel-written palette-colors mirror overrides the built-in set" {
    # The identity palette is selectable (2026-07-12): the kernel mirrors the ACTIVE set to
    # STATE/palette-colors (bg<TAB>fg per line) and the launcher assigns from it; the hardcoded
    # arrays are only the fallback for a machine whose kernel never booted.
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '#AA0000\twhite\n#00BB00\tblack\n' > "$XDG_STATE_HOME/romp/palette-colors"

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #AA0000' "$MOCK_LOG"
    grep -q 'tmux set -t myproject @identity-fg white' "$MOCK_LOG"
}

@test "color: all colors taken falls back to a hash pick" {
    local palette=("#1EA1EB" "#54B204" "#4EA8A9" "#DD42FF" "#E87221" "#98998A" "#F85B5A" "#F9D849" "#9088F0")
    > "$MOCK_TMUX_SESSIONS_FILE"
    > "$MOCK_TMUX_IDENTITY_FILE"
    for i in "${!palette[@]}"; do
        echo "sess${i}" >> "$MOCK_TMUX_SESSIONS_FILE"
        echo "sess${i}=${palette[$i]}" >> "$MOCK_TMUX_IDENTITY_FILE"
    done

    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t myproject @identity-bg #' "$MOCK_LOG"
}

# ─── No attach/rename subcommands (use tmux a / tmux rename) ─────────

@test "'a' and 'attach' are unknown commands, never sessions" {
    # There is no attach command (plain tmux does that), and round 3 made every
    # non-command bare word a loud error pointing at `romp new`. (`rename` left
    # this list when it became a real verb — see the rename tests above.)
    for word in a attach; do
        : > "$MOCK_LOG"
        run run_romp "$word"
        [ "$status" -eq 2 ]
        [[ "$output" == *"romp new ${word}"* ]]
        [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
    done
}

@test "retired human spellings fail loudly naming today's word, and start nothing" {
    # Rounds 1-2 spellings (short view flags, dashed manager commands). The
    # agent-facing aliases (--mail/--url/--send/--interrupt/--end/--resume,
    # --version, first-arg --detach) are exercised elsewhere and stay SILENT.
    for flag in -l --launch -d -f -j -r --on --refresh --status --update --checkin --checkout --default-dir --debug; do
        : > "$MOCK_LOG"
        run run_romp "$flag"
        [ "$status" -eq 2 ]
        [[ "$output" == *"retired"* ]]
        # every hint names today's spelling, or says the command is gone (the terminal TUIs)
        [[ "$output" == *"is now"* || "$output" == *"just: romp"* || "$output" == *"is gone"* ]]
        [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
    done
    # spot-check: a RENAMED command names its new spelling, a DELETED one says so
    run run_romp -d
    [[ "$output" == *"is gone"* ]]
    [[ "$output" == *"romp"* ]]
    run run_romp --refresh
    [[ "$output" == *"romp refresh"* ]]
}

# ─── kernel-manager commands (up / refresh / status) ─────────────────

@test "manager commands (up/refresh/status) dispatch to romp-manager with the right sub-command" {
    cat > "$MOCK_DIR/romp-manager" << 'MOCK'
#!/usr/bin/env bash
echo "romp-manager called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
    # --refresh also bounces the postal bus now; mock it so the test never touches the real bus
    cat > "$MOCK_DIR/romp-postal-service" << 'MOCK'
#!/usr/bin/env bash
echo "romp-postal-service called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-postal-service"
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal-service"
    mock_service 3               # no login service installed: `romp up` falls through to the manager

    run run_romp up              # `romp up` starts the manager (through the service when one is installed)
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: up' "$MOCK_LOG"
    run grep -q 'romp-postal-service called' "$MOCK_LOG"   # up does not touch the bus
    [ "$status" -ne 0 ]

    : > "$MOCK_LOG"
    run run_romp refresh         # restart EVERYTHING: the bus AND all kernels
    [ "$status" -eq 0 ]
    grep -q 'romp-postal-service called: restart' "$MOCK_LOG"   # bus bounced first
    grep -q 'romp-manager called: restart-all' "$MOCK_LOG"      # then the kernels

    : > "$MOCK_LOG"
    run run_romp status
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: status' "$MOCK_LOG"
    ! grep -q 'romp-postal-service called' "$MOCK_LOG"   # status does not touch the bus
}

@test "refresh appends a caller-attribution line to restart-audit.jsonl before restarting" {
    # 2026-07-16: three staged-demo teardowns traced back to untraceable fleet-wide refreshes —
    # kernel-downtime.jsonl records only {start,end}, and agents (Bash tool) leave no shell history.
    # The audit line answers WHO (sid -> session name, parent argv, tty) before the restart runs.
    cat > "$MOCK_DIR/romp-manager" << 'MOCK'
#!/usr/bin/env bash
echo "romp-manager called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
    cat > "$MOCK_DIR/romp-postal-service" << 'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
    chmod +x "$MOCK_DIR/romp-postal-service"
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal-service"

    # an agent-shaped caller: CLAUDE_CODE_SESSION_ID set, resolvable through the names map
    mkdir -p "$XDG_STATE_HOME/romp/names"
    printf 'demo_agent\t/tmp\t#000000\twhite\n' \
        > "$XDG_STATE_HOME/romp/names/11111111-2222-3333-4444-555555555555"
    export CLAUDE_CODE_SESSION_ID="11111111-2222-3333-4444-555555555555"

    run run_romp refresh
    [ "$status" -eq 0 ]
    audit="$XDG_STATE_HOME/romp/restart-audit.jsonl"
    [ -f "$audit" ]
    grep -q '"sid": "11111111-2222-3333-4444-555555555555"' "$audit"
    grep -q '"name": "demo_agent"' "$audit"          # sid resolved to the session's NAME
    grep -q '"parent":' "$audit"                     # the caller's parent argv rides along
    grep -q 'romp-manager called: restart-all' "$MOCK_LOG"   # ...and the restart still ran
}

@test "refresh survives an unwritable audit dir (attribution is best-effort, never blocks)" {
    cat > "$MOCK_DIR/romp-manager" << 'MOCK'
#!/usr/bin/env bash
echo "romp-manager called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
    cat > "$MOCK_DIR/romp-postal-service" << 'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
    chmod +x "$MOCK_DIR/romp-postal-service"
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal-service"

    mkdir -p "$XDG_STATE_HOME/romp"
    chmod 500 "$XDG_STATE_HOME/romp"                 # audit append will fail
    run run_romp refresh
    chmod 700 "$XDG_STATE_HOME/romp"                 # restore for teardown
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: restart-all' "$MOCK_LOG"   # the restart went through regardless
}

@test "romp up: unknown options and trailing words are exit 2 and start nothing (romp refresh is its own command)" {
    mock_service 0
    mock_manager 0
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '{"t": %s, "cmd": "romp down"}\n' "$(date +%s)" > "$XDG_STATE_HOME/romp/down-by-romp"
    for args in "restart main" "--forground" "--now" "--foreground --bogus"; do
        # shellcheck disable=SC2086
        run run_romp up $args
        [ "$status" -eq 2 ]
        [[ "$output" == *"romp up: unknown option"* ]]
        [[ "$output" == *"usage: romp up [--foreground]"* ]]
    done
    run grep -q 'called' "$MOCK_LOG"                   # neither the service nor the manager was started
    [ "$status" -ne 0 ]
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]         # a rejected up clears nothing
    run run_romp up --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"usage: romp up [--foreground]"* ]]
}

@test "'on', 'serve', 'launch', 'open' are unknown commands: loud exit 2, no session" {
    # These words never became round-3 commands (up replaced on; serve was removed; the
    # dashboard is bare romp). Each must fail naming the fix. (`down` is a command: see the
    # romp down tests below.)
    for word in on serve launch open; do
        : > "$MOCK_LOG"
        run run_romp "$word"
        [ "$status" -eq 2 ]
        [[ "$output" == *"romp new ${word}"* ]]
        [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
    done
}

# ─── romp down / romp up / romp status with a romp down marker ──────────────────────────
# `romp down` quiesces the kernel through POST /down, leaves the down-by-romp marker, writes an
# audit row, and stops THROUGH the supervisor (romp-service stop); only when no login service is
# installed (exit 3) or it is not running (4) does it fall back to the manager's own /stop. Last it
# probes the kernel port itself (GET /healthz) and stops a kernel nothing above took down through
# the kernel's own door, a SIGTERM at the pid it named on POST /down under this romp's serve token,
# and only when the auth-exempt GET /version names the same pid. A kernel that rejects the token is
# another romp's and is left alone (a `romp down` aimed at a port it did not mean, an empty
# ROMP_KERNEL_PORT falling to the default, must never take a 403 for "nothing answered", read the
# pid off /version and SIGTERM another romp's kernel). A fake kernel (python http.server, alive
# until teardown or until a stop takes it) answers POST /down from $TEST_DIR/down-reply, adding its
# own pid the way the real kernel does unless the body names one or the mode is no-pid, and logs
# every POST (path, token ok?, body) to $TEST_DIR/kreq and every GET and signal to $TEST_DIR/kget;
# its GET /version names its own pid, or the one $TEST_DIR/version-pid holds. Recording mocks stand
# in for romp-service and romp-manager, so nothing here can reach the machine's systemctl or its
# live manager. A mock stop that lands takes the fake kernel with it (kill -9, so a SIGTERM in kget
# can only be the CLI's own), as the real service and manager do; "keep-kernel" leaves it up. Every
# case sets ROMP_KERNEL_PORT to the fake's port, or to the floor port 1 when it starts no fake, and
# start_down_kernel asserts the fake answers before the CLI runs: `romp down` in a test must never
# reach a port that could be the machine's own kernel.

start_down_kernel() {   # $1 = the /down reply body; $2 = "" | ignore-term | exit-after-down | refuse-401 | no-pid
                        #      | exit-before-confirm (leaves before answering the second POST /down)
                        #      | exit-before-version (answers every POST /down, leaves before answering GET /version)
                        #      | refuse-second-401 (accepts the first POST /down, answers 401 to every later one)
    printf '%s' "$1" > "$TEST_DIR/down-reply"
    export ROMP_SERVE_TOKEN="test-token-DO-NOT-USE"
    rm -f "$TEST_DIR/kport" "$TEST_DIR/kpid"
    python3 - "$TEST_DIR" "$ROMP_SERVE_TOKEN" "${2:-}" <<'PY' &
import http.server, json, os, signal, sys
tdir, tok, mode = sys.argv[1], sys.argv[2], sys.argv[3]
ndown = 0     # POST /down requests so far: the quiesce is the first, the probe's confirmation the second
def note(line):
    with open(tdir + "/kget", "a") as f:
        f.write(line + "\n")
def on_term(signum, frame):
    # the kernel's stop door (the manager's stopKernel sends exactly this): a real kernel drains and
    # exits; the ignore-term variant records the ask and stays, the way a wedged one would
    if mode == "ignore-term":
        note("SIGTERM ignored")
        return
    note("SIGTERM")
    os._exit(0)
signal.signal(signal.SIGTERM, on_term)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        note(self.path)
        if self.path == "/healthz":
            body, ctype = b"ok", "text/plain"
        elif self.path == "/version":
            if mode == "exit-before-version":
                # a kernel gone between the confirmation and the pid check (its own exit, or the end of
                # a drain a stop above began): curl gets no reply, and the CLI must not die with its code
                note("exiting before answering /version")
                os._exit(0)
            # this process, or the pid $TEST_DIR/version-pid names: a kernel whose auth-exempt word
            # disagrees with what it said under the token
            pid = os.getpid()
            try:
                pid = int(open(tdir + "/version-pid").read().strip())
            except (OSError, ValueError):
                pass
            body, ctype = json.dumps({"pid": pid, "kernel_ver": "test"}).encode(), "application/json"
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        ok = self.headers.get("X-Romp-Token") == tok
        with open(tdir + "/kreq", "a") as f:
            f.write("%s token=%s %s\n" % (self.path, "ok" if ok else "BAD", body))
        if self.path == "/down":
            global ndown
            ndown += 1
            if mode == "exit-before-confirm" and ndown == 2:
                note("exiting before answering POST /down #2")   # gone between the quiesce and the probe
                os._exit(0)
        if self.path == "/down" and (mode == "refuse-401" or (mode == "refuse-second-401" and ndown >= 2)):
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        if not ok:
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
        reply = open(tdir + "/down-reply", "rb").read() if self.path == "/down" else b'{"ok": true}'
        if self.path == "/down" and mode != "no-pid":
            # the real kernel names its pid on every /down 200 (the one pid the CLI may signal)
            try:
                d = json.loads(reply)
                if isinstance(d, dict) and "pid" not in d:
                    d["pid"] = os.getpid()
                    reply = json.dumps(d).encode()
            except ValueError:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)
        if mode == "exit-after-down" and self.path == "/down":
            self.wfile.flush()          # the reply is out; a kernel that leaves on its own right after
            os._exit(0)
    def log_message(self, *a):
        pass
s = http.server.HTTPServer(("127.0.0.1", 0), H)
with open(tdir + "/kpid", "w") as f:
    f.write(str(os.getpid()))
with open(tdir + "/kport", "w") as f:
    f.write(str(s.server_address[1]))
s.serve_forever()
PY
    KERNEL_PID=$!
    until [ -s "$TEST_DIR/kport" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/kport")"
    assert_fake_kernel_up
}

assert_fake_kernel_up() {   # the CLI runs against a kernel that ANSWERS on ROMP_KERNEL_PORT, never a port that might be someone else's
    local i
    for i in $(seq 1 50); do
        [[ "$(curl -s -m 1 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$ROMP_KERNEL_PORT/healthz" 2>/dev/null)" == 200 ]] && return 0
        sleep 0.1
    done
    echo "the fake kernel did not come up on :$ROMP_KERNEL_PORT" >&2
    return 1
}

kernel_port_closed() {   # the fake kernel is gone, not merely asked: nothing answers on its port
    local i
    for i in $(seq 1 20); do curl -s -m 1 -o /dev/null "http://127.0.0.1:$ROMP_KERNEL_PORT/healthz" 2>/dev/null || return 0; sleep 0.1; done
    return 1
}

mock_service() {   # $1 = exit code for stop/start (0 done, 3 not installed, 4 installed but stopped, 1 failed); $2 = "" | keep-kernel
    cat > "$MOCK_DIR/romp-service" <<MOCK
#!/usr/bin/env bash
echo "romp-service called: \$*" >> "$MOCK_LOG"
[ "\$1" = stop ] && [ "$1" -eq 0 ] && [ -z "${2:-}" ] && [ -s "$TEST_DIR/kpid" ] && kill -9 "\$(cat "$TEST_DIR/kpid")" 2>/dev/null
exit $1
MOCK
    chmod +x "$MOCK_DIR/romp-service"
    export ROMP_SERVICE_BIN="$MOCK_DIR/romp-service"
}

mock_manager() {   # $1 = exit code
    cat > "$MOCK_DIR/romp-manager" <<MOCK
#!/usr/bin/env bash
echo "romp-manager called: \$*" >> "$MOCK_LOG"
[ "\$1" = status ] && [ "$1" -ne 0 ] && echo "romp manager is not running on :7432 (start it with \\\`romp up\\\`)." >&2
[ "\$1" = status ] && [ "$1" -eq 0 ] && echo '{"ok": true, "manager": {"pid": 424242, "controlPort": 7432}, "kernels": [{"id": "main"}]}'
exit $1
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
}

mock_manager_live() {   # $1 = "" | keep-kernel: a manager that answers status until `down` has been called, as the real one does
    cat > "$MOCK_DIR/romp-manager" <<MOCK
#!/usr/bin/env bash
echo "romp-manager called: \$*" >> "$MOCK_LOG"
case "\$1" in
  status) grep -q '^romp-manager called: down' "$MOCK_LOG" && exit 1
          echo '{"ok": true, "manager": {"pid": 424242, "controlPort": 7432}, "kernels": [{"id": "main"}]}'; exit 0 ;;
  down)   [ -z "${1:-}" ] && [ -s "$TEST_DIR/kpid" ] && kill -9 "\$(cat "$TEST_DIR/kpid")" 2>/dev/null
          echo '{"ok": true, "stopping": "all"}'; exit 0 ;;
esac
exit 0
MOCK
    chmod +x "$MOCK_DIR/romp-manager"
    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"
}

@test "romp down: quiesces through POST /down, leaves the marker and audit row, stops through the service" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 1.2}'
    mock_service 0
    mock_manager 1                                    # no manager outside the service
    run run_romp down
    [ "$status" -eq 0 ]
    # the kernel was asked to quiesce with the default wait, under the serve token
    grep -q '^/down token=ok {"wait": 5}$' "$TEST_DIR/kreq"
    [[ "$output" == *"quiet: no turn in flight (waited 1.2s)"* ]]
    # the marker: time + the command, so status/ensure/up can read a deliberate stop
    local marker="$XDG_STATE_HOME/romp/down-by-romp"
    [ -f "$marker" ]
    grep -q '"cmd": "romp down"' "$marker"
    grep -Eq '"t": [0-9]{9,}' "$marker"
    # the audit row names the action (the kernel's cut ledger joins on the newest row)
    grep -q '"action": "down"' "$XDG_STATE_HOME/romp/restart-audit.jsonl"
    # the stop went THROUGH the supervisor, never the manager's own /stop; afterwards the manager was
    # probed once, so "down" is a checked fact, not the service's word for it
    grep -q 'romp-service called: stop' "$MOCK_LOG"
    [[ "$output" == *"down; \`romp up\` starts it again"* ]]
    grep -q 'romp-manager called: status' "$MOCK_LOG"
    run grep -q 'romp-manager called: down' "$MOCK_LOG"     # (`run` replaces $output: assert on it above)
    [ "$status" -ne 0 ]
    run grep -q 'SIGTERM' "$TEST_DIR/kget"                  # the service's stop took the kernel; the CLI sent nothing
    [ "$status" -ne 0 ]
}

@test "romp down --now: no wait (the one ask is the token check with a wait of 0, unreported), the marker and audit say --now, the stop still goes through the service" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 0
    mock_manager 1                                    # no manager outside the service
    run run_romp down --now
    [ "$status" -eq 0 ]
    # the kernel was asked once, with no wait: the token gate answers before anything is stopped,
    # and --now reports nothing about a wait it did not make
    [ "$(grep -c '^/down' "$TEST_DIR/kreq")" -eq 1 ]
    grep -q '^/down token=ok {"wait": 0}$' "$TEST_DIR/kreq"
    [[ "$output" != *"quiet:"* && "$output" != *"mid-turn"* ]]
    # the marker's cmd carries the flag; the audit row names the action alone (the kernel's cut ledger
    # reads `down`, never a flag spelling)
    grep -q '"cmd": "romp down --now"' "$XDG_STATE_HOME/romp/down-by-romp"
    grep -q '"action": "down"' "$XDG_STATE_HOME/romp/restart-audit.jsonl"
    [[ "$(grep '"action": "down"' "$XDG_STATE_HOME/romp/restart-audit.jsonl")" != *'"reason"'* ]]
    grep -q 'romp-service called: stop' "$MOCK_LOG"
}

@test "romp down --wait N: passes the wait through and names what a still-busy kernel is about to cut" {
    start_down_kernel '{"ok": true, "quiet": false, "busy": 2, "inflight": ["web", "api"], "waited": 2.0}'
    mock_service 0
    mock_manager 1                                    # no manager outside the service
    run run_romp down --wait 2
    [ "$status" -eq 0 ]
    grep -q '^/down token=ok {"wait": 2}$' "$TEST_DIR/kreq"
    [[ "$output" == *"2 session(s) still mid-turn after 2.0s (web, api); stopping anyway"* ]]
    [[ "$output" == *"pick up where they stopped at the next romp up"* ]]
    grep -q '"cmd": "romp down --wait 2"' "$XDG_STATE_HOME/romp/down-by-romp"
    grep -q 'romp-service called: stop' "$MOCK_LOG"
    # the = spelling too (the stop above took the fake kernel with it: start another)
    : > "$MOCK_LOG"; rm -f "$TEST_DIR/kreq"
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0.5}'
    run run_romp down --wait=0.5
    [ "$status" -eq 0 ]
    grep -q '^/down token=ok {"wait": 0.5}$' "$TEST_DIR/kreq"
}

@test "romp down: bad options are loud exit 2 and touch nothing" {
    mock_service 0
    mock_manager 1                                    # no manager outside the service
    export ROMP_KERNEL_PORT=1                          # no fake here: the floor port, which refuses at once
    # 600.4 / 600.5 round to 600 under printf %.0f but the kernel refuses anything above 600.0 with a
    # 400, which the CLI would turn into a stop with no wait: the CLI's bound is the same, unrounded.
    # A leading zero is not JSON: 05 / 0600 / 00.5 would go into the body raw and come back as a 400
    for args in "--wait abc" "--wait 601" "--wait -1" "--bogus" "--wait" "--wait 600.4" "--wait 600.5" "--wait=600.01" "--wait 0600.5" \
                "--wait 05" "--wait 0600" "--wait 00.5" "--wait=007"; do
        # shellcheck disable=SC2086
        run run_romp down $args
        [ "$status" -eq 2 ]
        [[ "$output" == *"romp down"* ]]
    done
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
    run grep -q 'romp-service called' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    run run_romp down --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"usage: romp down [--now] [--wait <seconds>]"* ]]
    # the bound itself passes, spelled either way (the kernel accepts wait <= 600.0)
    export ROMP_KERNEL_PORT=1                          # a dead port: nothing to quiesce, no 615s timeout to sit through
    for w in 600 600.0; do
        run run_romp down --wait $w
        [ "$status" -eq 0 ]
    done
}

@test "romp down: with no login service installed it stops the manager directly (its own /stop)" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 3
    mock_manager_live
    run run_romp down
    [ "$status" -eq 0 ]
    grep -q 'romp-service called: stop' "$MOCK_LOG"     # asked first...
    grep -q 'romp-manager called: down' "$MOCK_LOG"     # ...then the manager's own /stop
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
    [[ "$output" == *"the manager and its kernels are stopping"* ]]
}

@test "romp down: nothing running and nothing installed is a no-op that still holds the auto-start" {
    mock_service 3
    mock_manager 1
    # a port nothing listens on, set explicitly: with ROMP_KERNEL_PORT unset the CLI probes its
    # default port, which on a machine running romp is the live kernel, so no test here ever leaves
    # it unset. The floor port stands in for "nothing there".
    export ROMP_KERNEL_PORT=1
    run run_romp down
    [ "$status" -eq 0 ]
    [[ "$output" == *"isn't answering on :1; nothing to quiesce"* ]]
    [[ "$output" == *"nothing was running"* ]]
    [[ "$output" == *"auto-start stays held until \`romp up\`"* ]]
    [[ "$output" != *"pid"* ]]                         # no pid was learned, so none could be signaled
    [[ "$output" != *"stopped"* ]]
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down: a failed service stop releases the hold, takes the marker back, exits 1" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 1
    mock_manager 0
    run run_romp down
    [ "$status" -eq 1 ]
    [[ "$output" == *"did not stop; the kernel keeps running"* ]]
    grep -q '^/down token=ok {"cancel": true}$' "$TEST_DIR/kreq"   # turns resume now, not at the lease's end
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]                    # a running kernel must not read as down
    run grep -q 'romp-manager called' "$MOCK_LOG"                  # no fallback: the service IS installed
    [ "$status" -ne 0 ]
    # the newest audit row says the stop failed: the kernel's resume notice reads the newest row, and a
    # later cut nobody recorded must not be reported as this romp down
    local last; last="$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")"
    [[ "$last" == *'"action": "down-failed"'* ]]
    [[ "$last" == *'"reason": "the login service did not stop"'* ]]
    grep -q '"action": "down"' "$XDG_STATE_HOME/romp/restart-audit.jsonl"   # the attempt itself stays on the record
}

start_old_kernel() {   # a kernel from before the /down route: 404 on every POST, /healthz and /version as ever
    rm -f "$TEST_DIR/kport" "$TEST_DIR/kpid"
    python3 - "$TEST_DIR" <<'PY' &
import http.server, json, os, sys
tdir = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with open(tdir + "/kget", "a") as f:
            f.write(self.path + "\n")
        body = b"ok" if self.path == "/healthz" else json.dumps({"pid": os.getpid()}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
    def log_message(self, *a): pass
s = http.server.HTTPServer(("127.0.0.1", 0), H)
open(tdir + "/kpid", "w").write(str(os.getpid()))
open(tdir + "/kport", "w").write(str(s.server_address[1]))
s.serve_forever()
PY
    KERNEL_PID=$!
    until [ -s "$TEST_DIR/kport" ]; do sleep 0.05; done
    export ROMP_KERNEL_PORT="$(cat "$TEST_DIR/kport")"
    assert_fake_kernel_up
}

@test "romp down: an older kernel without /down stops without waiting, through the service; one the service does not take is not signaled" {
    # 404: the route is missing, so no quiesce; the supervised stop still runs and takes the kernel
    mock_service 0
    mock_manager 1                                    # no manager outside the service
    start_old_kernel
    run run_romp down
    [ "$status" -eq 0 ]
    [[ "$output" == *"predates the quiesce route; stopping without waiting"* ]]
    grep -q 'romp-service called: stop' "$MOCK_LOG"
    kernel_port_closed; KERNEL_PID=""
    # the same kernel with nothing above it: it cannot name its pid under the token, so the probe
    # will not signal it. Loud exit 1, the kernel left alive, marker taken back
    : > "$MOCK_LOG"; rm -f "$TEST_DIR/kget"
    mock_service 3
    mock_manager 1
    start_old_kernel
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT was not confirmed as the one this romp manages (POST /down answered HTTP 404, not a 200 naming its pid); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
    kill -0 "$kpid"
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
    [[ "$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")" == *'"action": "down-failed"'* ]]
}

@test "romp down: a kernel that rejects the serve token is another romp's: exit 1, the line, nothing touched, the kernel left alive" {
    # a `romp down` aimed at a port it did not mean gets a 403 from the kernel there; it must not go
    # on as if nothing had answered, read that kernel's pid off the auth-exempt GET /version and
    # SIGTERM it (another romp's kernel, every session on it cut). A refused token ends the command
    # before the marker, the service, the manager or any signal. Both codes a token gate can answer.
    mock_service 0
    mock_manager 0
    local code kpid
    for code in 403 401; do
        : > "$MOCK_LOG"; rm -f "$TEST_DIR/kreq" "$TEST_DIR/kget"
        if [ "$code" = 403 ]; then
            start_down_kernel '{"ok": true}'
            export ROMP_SERVE_TOKEN="some-other-token"       # the token this romp holds is not that kernel's
        else
            start_down_kernel '{"ok": true}' refuse-401
        fi
        kpid="$(cat "$TEST_DIR/kpid")"
        run run_romp down
        [ "$status" -eq 1 ]
        [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT is not the one this romp manages (it rejected the serve token); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
        [[ "$output" != *"stopping without waiting"* ]]
        [[ "$output" != *"[romp] down"* ]]
        [ "$(grep -c '^/down' "$TEST_DIR/kreq")" -eq 1 ]   # asked once; it said no; that was the end
        [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]        # no marker: nothing of ours was stopped
        run grep -q '"action": "down' "$XDG_STATE_HOME/romp/restart-audit.jsonl"
        [ "$status" -ne 0 ]
        run grep -q 'called' "$MOCK_LOG"                    # neither the service nor the manager
        [ "$status" -ne 0 ]
        run grep -q '/version\|SIGTERM' "$TEST_DIR/kget"    # its pid was never asked for, let alone signaled
        [ "$status" -ne 0 ]
        kill -0 "$kpid"                                     # alive
        kill -9 "$KERNEL_PID"; KERNEL_PID=""
    done
}

@test "romp down: a kernel whose GET /version pid differs from the pid it gave under the token is not signaled: exit 1, the line" {
    # the pid the CLI signals is the one the kernel named on POST /down under this romp's token, and
    # only when the auth-exempt GET /version agrees. The pid /version names here belongs to a sleep
    # this test owns, so a stray SIGTERM would show as its death
    sleep 300 >/dev/null 2>&1 &
    OTHER_PID=$!
    echo "$OTHER_PID" > "$TEST_DIR/version-pid"
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 3
    mock_manager 1
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down
    kill -0 "$OTHER_PID"                                    # the pid /version named was never signaled
    kill -0 "$kpid"                                         # nor the kernel itself
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT was not confirmed as the one this romp manages (it named pid $kpid on POST /down but GET /version says pid $OTHER_PID); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
    run grep -q 'SIGTERM' "$TEST_DIR/kget"
    [ "$status" -ne 0 ]
    grep -q '^/down token=ok {"wait": 0}$' "$TEST_DIR/kreq"        # the confirmation, under the token
    grep -q '^/down token=ok {"cancel": true}$' "$TEST_DIR/kreq"   # the hold released: turns resume now
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
    [[ "$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")" == *'"action": "down-failed"'* ]]
}

@test "romp down: a kernel that answers the quiesce without naming its pid is not signaled: exit 1, the line" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}' no-pid
    mock_service 3
    mock_manager 1
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT was not confirmed as the one this romp manages (it answered POST /down without naming its pid); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
    run grep -q 'SIGTERM' "$TEST_DIR/kget"
    [ "$status" -ne 0 ]
    kill -0 "$kpid"
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down --now: a bare kernel is confirmed under the token before the signal (the quiesce with no wait)" {
    # --now skips the wait, not the check: POST /down {"wait": 0} goes out first (before the marker) and
    # again at the probe, right before the signal; neither ask shortens the hold the other armed
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 3
    mock_manager 1
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down --now
    [ "$status" -eq 0 ]
    [ "$(grep -c '^/down' "$TEST_DIR/kreq")" -eq 2 ]
    [ "$(grep -c '^/down token=ok {"wait": 0}$' "$TEST_DIR/kreq")" -eq 2 ]
    grep -q '^SIGTERM$' "$TEST_DIR/kget"
    [[ "$output" == *"[romp] down: a kernel was running on :$ROMP_KERNEL_PORT (pid $kpid) with no manager; stopped it. \`romp up\` starts it again"* ]]
    kernel_port_closed; KERNEL_PID=""
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down --now: a kernel that rejects the token is refused before the marker, the service and the manager: exit 1, nothing touched" {
    # --now must send the token-gated ask before it stops anything: a --now that sent nothing gated
    # until the probe would first stop this romp's own service and manager, then take the marker
    # back at the probe's 401, and the kernel it had stopped would read as a crash. Both codes a
    # gate answers.
    mock_service 0
    mock_manager 0
    local code kpid
    for code in 403 401; do
        : > "$MOCK_LOG"; rm -f "$TEST_DIR/kreq" "$TEST_DIR/kget"
        if [ "$code" = 403 ]; then
            start_down_kernel '{"ok": true}'
            export ROMP_SERVE_TOKEN="some-other-token"       # the token this romp holds is not that kernel's
        else
            start_down_kernel '{"ok": true}' refuse-401
        fi
        kpid="$(cat "$TEST_DIR/kpid")"
        run run_romp down --now
        [ "$status" -eq 1 ]
        [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT is not the one this romp manages (it rejected the serve token); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
        [[ "$output" != *"[romp] down"* ]]
        [ "$(grep -c '^/down' "$TEST_DIR/kreq")" -eq 1 ]   # asked once, with a wait of 0; it said no; that was the end
        grep -q '{"wait": 0}$' "$TEST_DIR/kreq"
        [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]        # no marker: nothing of ours was stopped
        run grep -q '"action": "down' "$XDG_STATE_HOME/romp/restart-audit.jsonl"
        [ "$status" -ne 0 ]                                 # no down row, no down-failed row
        run grep -q 'called' "$MOCK_LOG"                    # neither the service nor the manager
        [ "$status" -ne 0 ]
        run grep -q '/version\|SIGTERM' "$TEST_DIR/kget"    # its pid was never asked for, let alone signaled
        [ "$status" -ne 0 ]
        kill -0 "$kpid"                                     # alive
        kill -9 "$KERNEL_PID"; KERNEL_PID=""
    done
}

@test "romp down --now: a kernel that accepted the token at the start but rejects it at the probe is left alone, marker taken back, exit 1" {
    # the probe's own gate stays: the kernel on the port at the signal need not be the one that
    # answered at the start
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}' refuse-second-401
    mock_service 3
    mock_manager 1
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down --now
    [ "$status" -eq 1 ]
    [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT is not the one this romp manages (it rejected the serve token); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
    [ "$(grep -c '^/down token=ok {"wait": 0}$' "$TEST_DIR/kreq")" -eq 2 ]
    run grep -q 'SIGTERM' "$TEST_DIR/kget"
    [ "$status" -ne 0 ]
    kill -0 "$kpid"
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
    [[ "$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")" == *'"action": "down-failed"'* ]]
}

@test "romp down: the login service is stopped (4) but a manager runs outside it: stopped through its own /stop" {
    # `systemctl --user stop` on an inactive unit exits 0, so a stop that trusted the service's exit
    # would take a manager started by `romp up --foreground` (or a hand `romp-manager up`) for
    # stopped and leave it running under a marker that said otherwise
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 4
    mock_manager_live
    run run_romp down
    [ "$status" -eq 0 ]
    grep -q 'romp-service called: stop' "$MOCK_LOG"
    grep -q 'romp-manager called: down' "$MOCK_LOG"
    [[ "$output" == *"the manager and its kernels are stopping"* ]]
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down: the service stopped (0) and a manager outside it still answers: that one is stopped too" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 0
    mock_manager_live
    run run_romp down
    [ "$status" -eq 0 ]
    grep -q 'romp-service called: stop' "$MOCK_LOG"
    grep -q 'romp-manager called: down' "$MOCK_LOG"
    [[ "$output" == *"a manager running outside it"* ]]
    [[ "$output" == *"romp up"* ]]
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down: the login service already stopped (4) and no manager: a clean down that still holds the auto-start" {
    mock_service 4
    mock_manager 1
    export ROMP_KERNEL_PORT=1
    run run_romp down
    [ "$status" -eq 0 ]
    [[ "$output" == *"nothing was running"* ]]
    [[ "$output" == *"already stopped"* ]]
    [[ "$output" == *"auto-start stays held until \`romp up\`"* ]]
    run grep -q 'romp-manager called: down' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down: a manager that keeps answering after /stop is a loud failure: exit 1, port and pid named, marker taken back" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 4
    mock_manager 0                                    # answers status forever: the stop never lands
    export ROMP_MANAGER_PORT=7599
    run run_romp down
    [ "$status" -eq 1 ]
    grep -q 'romp-manager called: down' "$MOCK_LOG"
    [[ "$output" == *"still running on :7599"* ]]
    [[ "$output" == *"pid 424242"* ]]
    [[ "$output" == *"kernel keeps running"* ]]
    run grep -q '^\[romp\] down' <<< "$output"      # never a success line beside the failure
    [ "$status" -ne 0 ]
    grep -q '^/down token=ok {"cancel": true}$' "$TEST_DIR/kreq"   # the hold is released: turns resume now
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]                    # a running kernel must not read as down
    local last; last="$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")"
    [[ "$last" == *'"action": "down-failed"'* ]]
    [[ "$last" == *'a manager still answers on :7599 (pid 424242)'* ]]
}

@test "romp down: end to end, an installed-but-inactive unit and a real manager started outside it" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"
    local bin; bin="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)"
    # the REAL romp-service, against a unit installed under the test's own systemd dir and a systemctl
    # stub whose is-active answers inactive (the unit was stopped earlier; nothing respawns)
    export ROMP_SYSTEMD_DIR="$TEST_DIR/systemd"
    unset ROMP_SERVICE_NO_LOAD
    ROMP_OS_OVERRIDE=Linux ROMP_SERVICE_NO_LOAD=1 "$bin/romp-service" install >/dev/null
    local calls="$TEST_DIR/systemctl-calls"
    cat > "$TEST_DIR/systemctl" <<STUB
#!/bin/sh
echo "\$*" >> "$calls"
case "\$2" in
  is-active) echo inactive; exit 3 ;;
  *) exit 0 ;;
esac
STUB
    chmod +x "$TEST_DIR/systemctl"
    export ROMP_SYSTEMCTL="$TEST_DIR/systemctl" ROMP_OS_OVERRIDE=Linux
    export ROMP_SERVICE_BIN="$bin/romp-service" ROMP_MANAGER_BIN="$bin/romp-manager"
    # a REAL manager outside the service, the way `romp up --foreground` leaves one
    local fake="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$fake"
    chmod +x "$fake"
    local mport kport; free_port mport kport
    export ROMP_MANAGER_PORT=$mport ROMP_SERVE_PORT=$kport ROMP_KERNEL_PORT=$kport   # the kernel probe goes where the fake serve would listen
    ROMP_SERVE_BIN="$fake" node "$bin/romp-manager" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 30); do curl -fsS "http://127.0.0.1:$mport/status" >/dev/null 2>&1 && break; sleep 0.1; done
    curl -fsS "http://127.0.0.1:$mport/status" >/dev/null
    run run_romp down --now
    [ "$status" -eq 0 ]
    [[ "$output" == *"installed but not running"* ]]       # romp-service said what it found
    [[ "$output" == *"the manager and its kernels are stopping"* ]]
    # the manager is gone: its port answers nothing and the process has exited
    run curl -fsS "http://127.0.0.1:$mport/status"
    [ "$status" -ne 0 ]
    for i in $(seq 1 30); do kill -0 "$MGR_PID" 2>/dev/null || break; sleep 0.1; done
    run kill -0 "$MGR_PID"
    [ "$status" -ne 0 ]
    MGR_PID=""
    # the service was asked (is-active) and nothing was stopped through it; the marker stays
    grep -q 'is-active' "$calls"
    run grep -q -- '--user stop' "$calls"
    [ "$status" -ne 0 ]
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down: a kernel with no manager (a bare romp-serve) is stopped through its own door, and the line says so" {
    # the dashboard's remote Start and the update and restart fallbacks leave `nohup romp-serve` on a
    # host with no manager and no login service. The manager's absence is not the kernel's: taking
    # it so would print "nothing was running", exit 0, keep the marker, and have turns resume under
    # a marker that said down on purpose once the hold lapsed
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0.3}'
    mock_service 3
    mock_manager 1
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down
    [ "$status" -eq 0 ]
    grep -q '^/down token=ok {"wait": 5}$' "$TEST_DIR/kreq"
    grep -q '^/healthz$' "$TEST_DIR/kget"                  # the kernel port was asked, not the manager's word
    grep -q '^/version$' "$TEST_DIR/kget"                  # the pid came from the kernel itself
    grep -q '^SIGTERM$' "$TEST_DIR/kget"                   # the stop door the manager uses
    [[ "$output" == *"[romp] down: a kernel was running on :$ROMP_KERNEL_PORT (pid $kpid) with no manager; stopped it. \`romp up\` starts it again"* ]]
    [[ "$output" != *"nothing was running"* ]]
    kernel_port_closed
    KERNEL_PID=""
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]             # a real down: the marker stays
    [[ "$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")" == *'"action": "down"'* ]]
    run grep -q '^/down token=ok {"cancel": true}$' "$TEST_DIR/kreq"   # no release: the stop landed
    [ "$status" -ne 0 ]
}

@test "romp down: a kernel that ignores its stop is a loud failure: exit 1, port and pid named, hold released, marker taken back" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}' ignore-term
    mock_service 3
    mock_manager 1
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down
    [ "$status" -eq 1 ]
    grep -q '^SIGTERM ignored$' "$TEST_DIR/kget"           # it was asked, through its own door
    [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT (pid $kpid) is still running after being asked to stop; turns resume. Stop it by hand, then run romp down again"* ]]
    run grep -q '^\[romp\] down' <<< "$output"             # never a success line beside the failure
    [ "$status" -ne 0 ]
    grep -q '^/down token=ok {"cancel": true}$' "$TEST_DIR/kreq"   # the hold is released: turns resume now
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]                    # a running kernel must not read as down
    # the newest audit row is not `down`: the kernel's resume notice must not blame this romp down for a later cut
    local last; last="$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")"
    [[ "$last" == *'"action": "down-failed"'* ]]
    [[ "$last" == *"a kernel still answers on :$ROMP_KERNEL_PORT (pid $kpid)"* ]]
    grep -q '"action": "down"' "$XDG_STATE_HOME/romp/restart-audit.jsonl"   # the attempt itself stays on the record
}

@test "romp down: a kernel that outlives its manager's stop is stopped directly, after the drain time that stop gave it" {
    # the manager's /stop landed (it no longer answers) but its kernel is still on the port: a wedged
    # child. It gets the drain's time before the CLI asks it itself (a second SIGTERM inside the
    # drain writes a second, emptier ledger row), then the same door the manager used
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}'
    mock_service 3
    mock_manager_live keep-kernel
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: down' "$MOCK_LOG"
    grep -q '^SIGTERM$' "$TEST_DIR/kget"
    [[ "$output" == *"[romp] down: the kernel on :$ROMP_KERNEL_PORT (pid $kpid) outlived the stop and was stopped directly; \`romp up\` starts it again"* ]]
    kernel_port_closed
    KERNEL_PID=""
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down: a kernel that answered the quiesce and then left on its own is not reported as nothing running" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}' exit-after-down
    mock_service 3
    mock_manager 1
    run run_romp down
    [ "$status" -eq 0 ]
    [[ "$output" == *"quiet: no turn in flight"* ]]
    [[ "$output" != *"nothing was running"* ]]
    [[ "$output" == *"[romp] down: the kernel on :$ROMP_KERNEL_PORT answered the quiesce but has since gone (no login service installed or running, no manager on :${ROMP_MANAGER_PORT:-7432}); \`romp up\` starts it again"* ]]
    KERNEL_PID=""
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]
}

@test "romp down: a kernel that leaves after the confirmation and before its pid is checked is the documented refusal, not a bare exit 7" {
    # bin/romp runs under set -euo pipefail: a kernel gone between the confirmation and the pid
    # check (its own exit, or the end of the drain a stop above began) makes curl exit non-zero,
    # and the command must not die with that code (no line, the marker left in place, no
    # down-failed row). It is the not-confirmed refusal the docs promise.
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}' exit-before-version
    mock_service 3
    mock_manager 1
    local kpid; kpid="$(cat "$TEST_DIR/kpid")"
    run run_romp down
    [ "$status" -eq 1 ]
    grep -q '^exiting before answering /version$' "$TEST_DIR/kget"
    grep -q '^/down token=ok {"wait": 0}$' "$TEST_DIR/kreq"           # the confirmation was answered, with the pid
    [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT was not confirmed as the one this romp manages (it named pid $kpid on POST /down but GET /version names no pid); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
    [[ "$output" != *"[romp] down"* ]]
    kernel_port_closed; KERNEL_PID=""
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]                       # a kernel nobody confirmed stopped must not read as down on purpose
    local last; last="$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")"
    [[ "$last" == *'"action": "down-failed"'* ]]
    [[ "$last" == *"GET /version names no pid"* ]]
    grep -q '"action": "down"' "$XDG_STATE_HOME/romp/restart-audit.jsonl"   # the attempt stays on the record
}

@test "romp down: a kernel that leaves before answering the confirmation is 'no answer': exit 1, the line, marker taken back" {
    start_down_kernel '{"ok": true, "quiet": true, "busy": 0, "inflight": [], "waited": 0}' exit-before-confirm
    mock_service 3
    mock_manager 1
    run run_romp down
    [ "$status" -eq 1 ]
    [[ "$output" == *"quiet: no turn in flight (waited 0s)"* ]]        # the quiesce itself was answered
    grep -q '^exiting before answering POST /down #2$' "$TEST_DIR/kget"
    [[ "$output" == *"romp down: the kernel on :$ROMP_KERNEL_PORT was not confirmed as the one this romp manages (POST /down got no answer); not touching it. Check ROMP_KERNEL_PORT and the state dir"* ]]
    [[ "$output" != *"[romp] down"* ]]
    kernel_port_closed; KERNEL_PID=""
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
    local last; last="$(tail -1 "$XDG_STATE_HOME/romp/restart-audit.jsonl")"
    [[ "$last" == *'"action": "down-failed"'* ]]
    [[ "$last" == *"POST /down got no answer"* ]]
}

@test "romp up: clears the marker and starts through the login service when one is installed" {
    mock_service 0
    mock_manager 0
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '{"t": %s, "cmd": "romp down"}\n' "$(date +%s)" > "$XDG_STATE_HOME/romp/down-by-romp"
    run run_romp up
    [ "$status" -eq 0 ]
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
    [[ "$output" == *"cleared the romp down marker"* ]]
    grep -q 'romp-service called: start' "$MOCK_LOG"
    [[ "$output" == *"login service is starting the manager"* ]]
    run grep -q 'romp-manager called' "$MOCK_LOG"      # the service owns the manager; no foreground one
    [ "$status" -ne 0 ]
    # no marker: the same start, nothing said about a marker
    : > "$MOCK_LOG"
    run run_romp up
    [ "$status" -eq 0 ]
    grep -q 'romp-service called: start' "$MOCK_LOG"
    run grep -q 'marker' <<< "$output"
    [ "$status" -ne 0 ]
}

@test "romp up: no login service (3) means the foreground manager, marker cleared; --foreground skips the service" {
    mock_service 3
    mock_manager 0
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '{"t": %s, "cmd": "romp down"}\n' "$(date +%s)" > "$XDG_STATE_HOME/romp/down-by-romp"
    run run_romp up
    [ "$status" -eq 0 ]
    [ ! -e "$XDG_STATE_HOME/romp/down-by-romp" ]
    grep -q 'romp-service called: start' "$MOCK_LOG"
    grep -q 'romp-manager called: up' "$MOCK_LOG"
    : > "$MOCK_LOG"
    mock_service 0
    run run_romp up --foreground
    [ "$status" -eq 0 ]
    grep -q 'romp-manager called: up' "$MOCK_LOG"
    run grep -q 'romp-service called' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

@test "romp up: a failing service start is the exit code, and no second manager is started" {
    mock_service 1
    mock_manager 0
    run run_romp up
    [ "$status" -eq 1 ]
    run grep -q 'romp-manager called' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

@test "romp status: a marker with no manager answering reads as down on purpose, exit 0" {
    mock_manager 1
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '{"t": %s, "cmd": "romp down"}\n' "$(date +%s)" > "$XDG_STATE_HOME/romp/down-by-romp"
    run run_romp status
    [ "$status" -eq 0 ]                       # a health check must not read a deliberate stop as a failure
    [[ "$output" =~ ^down\ \(romp\ down\ at\ [0-9]{2}:[0-9]{2}\;\ romp\ up\ to\ start\)$ ]]
    # the manager's own "not running" line is not repeated under it
    run grep -q 'not running' <<< "$output"
    [ "$status" -ne 0 ]
}

@test "romp status: an old marker carries its date; a running manager outranks a stale marker" {
    mock_manager 1
    mkdir -p "$XDG_STATE_HOME/romp"
    printf '{"t": %s, "cmd": "romp down"}\n' "$(( $(date +%s) - 2 * 86400 ))" > "$XDG_STATE_HOME/romp/down-by-romp"
    run run_romp status
    [ "$status" -eq 0 ]
    [[ "$output" =~ ^down\ \(romp\ down\ at\ [0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}\; ]]
    mock_manager 0
    run run_romp status
    [ "$status" -eq 0 ]
    [[ "$output" == *'"id": "main"'* ]]
    run grep -q 'romp down' <<< "$output"
    [ "$status" -ne 0 ]
    [ -f "$XDG_STATE_HOME/romp/down-by-romp" ]   # status never writes
}

@test "romp status without a marker is the manager's status, exit code and all" {
    mock_manager 1
    run run_romp status
    [ "$status" -eq 1 ]
    [[ "$output" == *"not running"* ]]
}

@test "romp-manager: control verbs error cleanly when no manager is running" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    local mgr; mgr="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    # port nothing is listening on → the control client must fail fast with a clear message
    local port; free_port port
    run env ROMP_MANAGER_PORT=$port node "$mgr" status
    [ "$status" -eq 1 ]
    [[ "$output" == *"not running"* ]]
}

@test "romp-manager: /ensure spawns an additional kernel on demand, idempotently" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"
    local mgr; mgr="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"

    # Fake kernel launcher: ignore --port and just stay alive, so the manager keeps it "running"
    # without any real port binding (the test asserts on the manager's bookkeeping, not a live kernel).
    local fake="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$fake"
    chmod +x "$fake"

    local cport mport kport; free_port cport mport kport
    # Launch the manager in the background; it auto-spawns 'main' on mport via the fake launcher.
    ROMP_MANAGER_PORT=$cport ROMP_SERVE_PORT=$mport ROMP_SERVE_BIN="$fake" \
        node "$mgr" up >/dev/null 2>&1 &
    MGR_PID=$!   # teardown reaps this

    # Wait for the control endpoint to come up (≤ ~3s)
    local i
    for i in $(seq 1 30); do
        curl -fsS "http://127.0.0.1:$cport/status" >/dev/null 2>&1 && break
        sleep 0.1
    done

    # Ensure a second kernel on kport → freshly spawned
    run curl -fsS -X POST "http://127.0.0.1:$cport/ensure?port=$kport"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"spawned":true'* ]]
    [[ "$output" == *"\"port\":$kport"* ]]
    [[ "$output" == *"\"id\":\"k$kport\""* ]]

    # Ensuring the same port again is idempotent — no second spawn
    run curl -fsS -X POST "http://127.0.0.1:$cport/ensure?port=$kport"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"spawned":false'* ]]

    # /status now lists both the default 'main' kernel and the on-demand one
    run curl -fsS "http://127.0.0.1:$cport/status"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"id":"main"'* ]]
    [[ "$output" == *"\"id\":\"k$kport\""* ]]

    # Graceful shutdown (teardown also reaps via MGR_PID as a backstop)
    curl -fsS -X POST "http://127.0.0.1:$cport/stop" >/dev/null 2>&1 || true
}

@test "romp-manager: /restart-all kicks every kernel in the registry (romp refresh)" {
    command -v node >/dev/null 2>&1 || skip "node not available"
    command -v curl >/dev/null 2>&1 || skip "curl not available"
    local mgr; mgr="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"
    local fake="$TEST_DIR/fake-serve"
    printf '#!/usr/bin/env bash\nexec sleep 30\n' > "$fake"
    chmod +x "$fake"

    local cport mport kport; free_port cport mport kport
    ROMP_MANAGER_PORT=$cport ROMP_SERVE_PORT=$mport ROMP_SERVE_BIN="$fake" \
        node "$mgr" up >/dev/null 2>&1 &
    MGR_PID=$!
    local i
    for i in $(seq 1 30); do curl -fsS "http://127.0.0.1:$cport/status" >/dev/null 2>&1 && break; sleep 0.1; done
    curl -fsS -X POST "http://127.0.0.1:$cport/ensure?port=$kport" >/dev/null   # a 2nd kernel in the registry

    run curl -fsS -X POST "http://127.0.0.1:$cport/restart-all"
    [ "$status" -eq 0 ]
    # the response lists EVERY kernel it kicked — the default 'main' AND the on-demand one (not just main)
    [[ "$output" == *'"restarted"'* ]]
    [[ "$output" == *'main'* ]]
    [[ "$output" == *"k$kport"* ]]

    curl -fsS -X POST "http://127.0.0.1:$cport/stop" >/dev/null 2>&1 || true
}

# ─── Help (-h / --help) ──────────────────────────────────────────────

@test "-h prints usage and starts no session" {
    run run_romp -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
    [[ "$output" == *"romp new"* ]]
    ! grep -q 'tmux new-session' "$MOCK_LOG"
}

@test "help, -h and --help all print usage" {
    touch "$MOCK_LOG"    # help makes no tmux calls at all
    run run_romp --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
    run run_romp help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage:"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "mail dispatches to romp-postal with its args (--mail is its silent alias)" {
    cat > "$MOCK_DIR/romp-postal" << 'MOCK'
#!/usr/bin/env bash
echo "romp-postal called: $*" >> "$MOCK_LOG"
MOCK
    chmod +x "$MOCK_DIR/romp-postal"
    # same PATH-prepend shadowing as the dashboard test — without the seam this
    # exec'd the REAL romp-postal (a live mail send) instead of the mock
    export ROMP_POSTAL_BIN="$MOCK_DIR/romp-postal"

    run run_romp mail send beta "hello"
    [ "$status" -eq 0 ]
    grep -q 'romp-postal called: send beta hello' "$MOCK_LOG"

    : > "$MOCK_LOG"
    run run_romp --mail send beta "hello"    # delivered postal footers name --mail forever
    [ "$status" -eq 0 ]
    [[ "$output" != *"retired"* ]]
    grep -q 'romp-postal called: send beta hello' "$MOCK_LOG"
}

# _romp_resume_rows builds the resume-picker rows in ONE python pass: it walks the
# projects tree once into a sid->transcript index, reads each session's name file
# + cached gloss (archive headline, else latest caption), and emits FS-delimited
# rows newest-first. These extract JUST the function (never source the whole
# script — that would re-run its top-level dispatch + reset ROMP_*_DIR) and point
# the dirs at fixtures. FS is \x1f; fields are mtime|sid|name|dir|rgb|kind|text.
_resume_rows_fn() {   # writes the extracted function to $1
    sed -n '/^_romp_resume_rows()/,/^}/p' "$ROMP_SCRIPT" > "$1"
}

@test "resume rows: archive headline, caption fallback, ordering, live-exclusion" {
    local ndir="$TEST_DIR/names" adir="$TEST_DIR/archive" cdir="$TEST_DIR/captions"
    local pdir="$TEST_DIR/projects" fn="$TEST_DIR/_rows.sh"
    mkdir -p "$ndir" "$adir" "$cdir" "$pdir/proj-a"
    _resume_rows_fn "$fn"

    # three resumable sessions + one LIVE (must be excluded)
    printf 'arch-sess\t/tmp/b\t#aabbcc\t#000000\n' > "$ndir/sid-arch"
    printf 'cap-sess\t/tmp/c\t#ddeeff\t#000000\n'  > "$ndir/sid-cap"
    printf 'live-sess\t/tmp/a\t#112233\t#ffffff\n' > "$ndir/sid-live"
    : > "$pdir/proj-a/sid-arch.jsonl"
    : > "$pdir/proj-a/sid-cap.jsonl"
    : > "$pdir/proj-a/sid-live.jsonl"
    # cap-sess transcript OLDER than arch-sess -> arch-sess sorts first
    touch -t 202606160000 "$pdir/proj-a/sid-cap.jsonl"
    touch -t 202606161200 "$pdir/proj-a/sid-arch.jsonl"
    printf '{"headline":"Synthetic archive headline"}\n' > "$adir/sid-arch.json"
    printf '{"caption":"older step"}\n{"caption":"newest caption step"}\n' > "$cdir/sid-cap.jsonl"

    run env ROMP_NAMES_DIR="$ndir" ROMP_ARCHIVE_DIR="$adir" ROMP_CAPTIONS_DIR="$cdir" \
        ROMP_PROJECTS_DIR="$pdir" \
        bash -c 'source "$1"; _romp_resume_rows "$2" "$3"' _ "$fn" $'sid-live' $'\x1f'
    [ "$status" -eq 0 ]
    # live session excluded
    [[ "$output" != *"live-sess"* ]]
    # newest first: arch row before cap row
    local first; first="$(printf '%s\n' "$output" | head -1)"
    [[ "$first" == *"arch-sess"* ]]
    # archive headline wins for arch-sess; caption fallback for cap-sess (last non-empty)
    [[ "$output" == *"Synthetic archive headline"* ]]
    [[ "$output" == *"newest caption step"* ]]
    [[ "$output" != *"older step"* ]]
    # rgb derived from the bg hex (#aabbcc -> 170;187;204)
    [[ "$output" == *$'\x1f'"170;187;204"$'\x1f'* ]]
}

@test "resume rows: stale name file (transcript gone) is pruned" {
    local ndir="$TEST_DIR/names" pdir="$TEST_DIR/projects" fn="$TEST_DIR/_rows.sh"
    mkdir -p "$ndir" "$pdir/proj-a" "$TEST_DIR/archive" "$TEST_DIR/captions"
    _resume_rows_fn "$fn"
    printf 'has-tx\t/tmp/x\t\t\n'   > "$ndir/sid-has"
    printf 'stale\t/tmp/y\t\t\n'    > "$ndir/sid-stale"
    : > "$pdir/proj-a/sid-has.jsonl"          # only sid-has has a transcript
    run env ROMP_NAMES_DIR="$ndir" ROMP_ARCHIVE_DIR="$TEST_DIR/archive" \
        ROMP_CAPTIONS_DIR="$TEST_DIR/captions" ROMP_PROJECTS_DIR="$pdir" \
        bash -c 'source "$1"; _romp_resume_rows "$2" "$3"' _ "$fn" '' $'\x1f'
    [ "$status" -eq 0 ]
    [ -f "$ndir/sid-has" ]            # kept
    [ ! -f "$ndir/sid-stale" ]        # pruned
}

@test "resume rows: an EMPTY/unreadable projects index never prunes the cache" {
    # Regression guard: if the projects tree is missing, "transcript gone" is
    # unverifiable, so we must NOT delete any name files (an env mismatch once
    # wiped the whole cache this way).
    local ndir="$TEST_DIR/names" fn="$TEST_DIR/_rows.sh"
    mkdir -p "$ndir" "$TEST_DIR/archive" "$TEST_DIR/captions"
    _resume_rows_fn "$fn"
    printf 'a\t/tmp/a\t\t\n' > "$ndir/sid-a"
    printf 'b\t/tmp/b\t\t\n' > "$ndir/sid-b"
    run env ROMP_NAMES_DIR="$ndir" ROMP_ARCHIVE_DIR="$TEST_DIR/archive" \
        ROMP_CAPTIONS_DIR="$TEST_DIR/captions" ROMP_PROJECTS_DIR="$TEST_DIR/nonexistent" \
        bash -c 'source "$1"; _romp_resume_rows "$2" "$3"' _ "$fn" '' $'\x1f'
    [ "$status" -eq 0 ]
    [ -f "$ndir/sid-a" ]             # both survive — nothing pruned without an index
    [ -f "$ndir/sid-b" ]
    [ -z "$output" ]                 # and no rows (no transcripts to show)
}

@test "help -h reflects which commands are PRESENT (presence-checked, no drift)" {
    # Run a copy of romp with only SOME backing romp-* binaries reachable: present commands show, absent
    # ones are hidden, built-ins always show — so the help can't drift from what's installed (the user 2026-06-16).
    local td; td="$TEST_DIR/help"; mkdir -p "$td"
    cp "$ROMP_SCRIPT" "$td/romp"
    local b; for b in romp-manager romp-version; do printf '#!/bin/sh\nexit 0\n' > "$td/$b"; chmod +x "$td/$b"; done
    run env PATH="$td:/usr/bin:/bin:/opt/homebrew/bin" bash "$td/romp" -h
    [ "$status" -eq 0 ]
    # built-ins (no backing binary) always shown
    [[ "$output" == *"romp new"* ]]
    [[ "$output" == *"romp resume"* ]]
    # `romp serve` was removed (tailnet reach = tailscale serve to loopback) — must not resurface
    [[ "$output" != *"romp serve"* ]]
    # present backing → shown
    [[ "$output" == *"romp up"* ]]
    [[ "$output" == *"romp status"* ]]
    [[ "$output" == *"romp version"* ]]
    # absent backing → hidden
    [[ "$output" != *"romp mail"* ]]
    # the retired terminal TUIs must not come back as help rows
    [[ "$output" != *"romp monitor"* ]]
    [[ "$output" != *"romp feed"* ]]
    [[ "$output" != *"romp judges"* ]]
}

# ─── ROMPHOME — never launch a session in $HOME ──────────────────────
# $HOME is the one cwd whose direct children include the macOS TCC-protected
# Downloads/Desktop/Documents; indexing them trips spurious OS file-access
# prompts. A $HOME launch is redirected to ROMPHOME instead.

@test "ROMPHOME: a launch from \$HOME is redirected there, not created in \$HOME" {
    export ROMPHOME="$TEST_DIR/romphome"
    mkdir -p "$ROMPHOME"
    local expect; expect="$(cd "$ROMPHOME" && pwd -P)"
    local home_real; home_real="$(cd "$HOME" && pwd -P)"
    cd "$HOME"
    run run_romp new -t box
    [ "$status" -eq 0 ]
    grep -qF "tmux new-session -d -s box -c $expect" "$MOCK_LOG"
    # the redirect is announced to the user — asserted BEFORE the `run grep` overwrites $output
    [[ "$output" == *"not launching in \$HOME"* ]]
    # the session must NOT be rooted at $HOME
    run grep -qF "tmux new-session -d -s box -c $home_real" "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

@test "ROMPHOME: a name-less resume from \$HOME is named after ROMPHOME, not \$HOME" {
    # Regression: basename(\$HOME) is the username — a privacy leak as a session
    # name. `romp new` requires a name now, so the folder-name default only fires
    # on an explicit-id resume without --name; the name must come from the
    # resolved (redirected) dir.
    export ROMPHOME="$TEST_DIR/scratchpad"
    mkdir -p "$ROMPHOME"
    cd "$HOME"
    run run_romp resume abc123-uuid
    [ "$status" -eq 0 ]
    grep -q 'tmux new-session -d -s scratchpad' "$MOCK_LOG"
    ! grep -qE 'tmux new-session -d -s home( |$| -)' "$MOCK_LOG"
}

@test "ROMPHOME: a launch from a normal project dir is unaffected" {
    export ROMPHOME="$TEST_DIR/romphome"
    mkdir -p "$ROMPHOME"
    # setup() already cd'd into $WORK_DIR, a normal project dir
    local expect; expect="$(cd "$WORK_DIR" && pwd -P)"
    run run_romp new -t myproject
    [ "$status" -eq 0 ]
    grep -qF "tmux new-session -d -s myproject -c $expect" "$MOCK_LOG"
    [[ "$output" != *"not launching in \$HOME"* ]]
}

@test "new: -d launches in the given directory, not the cwd" {
    local other="$TEST_DIR/elsewhere"
    mkdir -p "$other"
    local expect; expect="$(cd "$other" && pwd -P)"
    run run_romp new -t -d "$other" side
    [ "$status" -eq 0 ]
    grep -qF "tmux new-session -d -s side -c $expect" "$MOCK_LOG"
}

@test "romp checkin/checkout: usage without a host, loud failure with no kernel" {
    run "$ROMP_SCRIPT" checkin
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp checkin <host>"* ]]
    run "$ROMP_SCRIPT" checkout
    [ "$status" -eq 2 ]
    # port 1 refuses instantly → the CLI must fail LOUDLY, never pretend the checkout happened
    ROMP_KERNEL_PORT=1 run "$ROMP_SCRIPT" checkout somehost
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

# ─── romp new (SDK default) ──────────────────────────────────────────

@test "new (no -t): no kernel token → loud error naming both fixes, nothing launched" {
    touch "$MOCK_LOG"    # this path makes no tmux calls at all
    run run_romp new api
    [ "$status" -eq 1 ]
    [[ "$output" == *"kernel isn't running"* ]]
    [[ "$output" == *"romp new -t api"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new (no -t): POSTs the kernel /new with backend sdk, and starts no tmux session" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"    # this path makes no tmux calls at all
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-test' > "$XDG_STATE_HOME/romp/serve-token"
    # One-shot fake kernel: accept a single POST, log it, answer ok:true. Ephemeral
    # port, announced via a file WRITTEN AFTER BIND — the same pattern as
    # romp-headless.bats. The `until` below waits on the listening EVENT; the
    # `sleep 0.3` this replaces was a guessed duration that macOS CI runners
    # reliably lost (two release-gate failures), while every faster machine won it.
    python3 - "$TEST_DIR/port" "$TEST_DIR/req.log" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
portfile, log = sys.argv[1], sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open(log, "w") as f:
            json.dump({"path": self.path, "token": self.headers.get("X-Romp-Token"),
                       "body": json.loads(body or b"{}")}, f)
        out = json.dumps({"ok": True, "id": "11111111-2222-3333-4444-555555555555"}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.handle_request()
PY
    local srv=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new api
    kill "$srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    [[ "$output" == *"started \"api\""* ]]
    [[ "$output" == *"dashboard"* ]]
    [ -f "$TEST_DIR/req.log" ]
    grep -q '"path": "/new"' "$TEST_DIR/req.log"
    grep -q '"token": "tok-test"' "$TEST_DIR/req.log"
    grep -q '"name": "api"' "$TEST_DIR/req.log"
    grep -q '"backend": "sdk"' "$TEST_DIR/req.log"
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new --model/--effort: ride /new VERBATIM (full ids, no alias munging) and report what was applied" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-test' > "$XDG_STATE_HOME/romp/serve-token"
    # fake kernel echoes model/effort back, the applied-ack contract of the real /new
    python3 - "$TEST_DIR/port" "$TEST_DIR/req.log" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
portfile, log = sys.argv[1], sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with open(log, "w") as f:
            json.dump({"path": self.path, "body": body}, f)
        out = json.dumps({"ok": True, "id": "11111111-2222-3333-4444-555555555555",
                          "model": body.get("model"), "effort": body.get("effort")}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.handle_request()
PY
    local srv=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new --model claude-fable-5 --effort ultracode opt
    kill "$srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    grep -q '"model": "claude-fable-5"' "$TEST_DIR/req.log"
    grep -q '"effort": "ultracode"' "$TEST_DIR/req.log"
    [[ "$output" == *"applied model claude-fable-5, effort ultracode"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new --model/--effort: a kernel that does NOT ack them warns loudly (no silent divergence)" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-test' > "$XDG_STATE_HOME/romp/serve-token"
    # fake OLDER kernel: acks ok but ignores the keys — the CLI must say so, not pretend
    python3 - "$TEST_DIR/port" "$TEST_DIR/req.log" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
portfile, log = sys.argv[1], sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        out = json.dumps({"ok": True, "id": "11111111-2222-3333-4444-555555555555"}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.handle_request()
PY
    local srv=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new --model claude-fable-5 opt
    kill "$srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    # per-asked-key: only --model was asked, so only --model is named as dropped
    [[ "$output" == *"did not acknowledge --model (older kernel?)"* ]]
}

@test "new --model + --env: a kernel that acks model but drops env warns about --env specifically" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-test' > "$XDG_STATE_HOME/romp/serve-token"
    # fake OLDER kernel mid-window: echoes model (a key it knows) but silently ignores env — the
    # guaranteed self-hosting shape between merging env support and `romp refresh`. The old
    # all-or-nothing check read this partial ack as full success and the env drop went unsaid.
    python3 - "$TEST_DIR/port" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
portfile = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        out = json.dumps({"ok": True, "id": "11111111-2222-3333-4444-555555555555",
                          "model": body.get("model")}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.handle_request()
PY
    local srv=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new --model claude-fable-5 --env FEATURE_FLAG=1 envy
    kill "$srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    [[ "$output" == *"applied model claude-fable-5"* ]]
    [[ "$output" == *"did not acknowledge --env (older kernel?)"* ]]
}

@test "new --model with -t refuses loudly (SDK-only flags), and starts nothing" {
    touch "$MOCK_LOG"
    run run_romp new -t --model claude-fable-5 x
    [ "$status" -eq 2 ]
    [[ "$output" == *"--model/--effort/--env need the default (SDK) session"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new: help names --model and --effort (the nightly optimizer's presence guard greps help)" {
    run run_romp -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"--model <id>"* ]]
    [[ "$output" == *"--effort <level>"* ]]
}

# Helper — a one-shot fake kernel for the --env tests: records the /new body and echoes the env
# back, the applied-ack contract of the real handler (the same shape the --model/--effort fake uses).
_env_fake_kernel() {
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'tok-test' > "$XDG_STATE_HOME/romp/serve-token"
    python3 - "$TEST_DIR/port" "$TEST_DIR/req.log" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
portfile, log = sys.argv[1], sys.argv[2]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with open(log, "w") as f:
            json.dump({"path": self.path, "body": body}, f)
        out = {"ok": True, "id": "11111111-2222-3333-4444-555555555555"}
        if "env" in body:              # echo whenever ASKED — {} (the clear declaration) included
            out["env"] = body["env"]
        out = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
with open(portfile, "w") as f:
    f.write(str(srv.server_address[1]))
srv.handle_request()
PY
    _env_srv=$!
    until [ -s "$TEST_DIR/port" ]; do sleep 0.05; done
}

@test "new --env: repeatable flags accumulate into ONE env object on /new, echoed as applied" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"
    _env_fake_kernel
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new --env FEATURE_FLAG=1 --env UI_THEME=dark envy
    kill "$_env_srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    grep -q '"FEATURE_FLAG": "1"' "$TEST_DIR/req.log"
    grep -q '"UI_THEME": "dark"' "$TEST_DIR/req.log"
    [[ "$output" == *"applied env FEATURE_FLAG=1,UI_THEME=dark"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new --env: the value splits on the FIRST '=' and an empty value is meaningful" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"
    _env_fake_kernel
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new --env TOGGLE=a=b --env EMPTY= envy
    kill "$_env_srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    grep -q '"TOGGLE": "a=b"' "$TEST_DIR/req.log"
    grep -q '"EMPTY": ""' "$TEST_DIR/req.log"
}

@test "new without --env sends NO env key (absent means don't touch, never an empty object)" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"
    _env_fake_kernel
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new envy
    kill "$_env_srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    run grep '"env"' "$TEST_DIR/req.log"
    [ "$status" -ne 0 ]
}

@test "new --env: a malformed or empty NAME is a usage error, never a silent skip" {
    touch "$MOCK_LOG"
    run run_romp new --env 9BAD=1 x
    [ "$status" -eq 2 ]
    [[ "$output" == *"[A-Za-z_][A-Za-z0-9_]*"* ]]
    run run_romp new --env =x x
    [ "$status" -eq 2 ]
    run run_romp new --env NOEQUALS x
    [ "$status" -eq 2 ]
    run run_romp new --env
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp new"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new --no-env sends the explicit empty declaration and reports the clear as applied" {
    command -v python3 >/dev/null 2>&1 || skip "python3 not available"
    touch "$MOCK_LOG"
    _env_fake_kernel
    ROMP_KERNEL_PORT="$(cat "$TEST_DIR/port")" run run_romp new --no-env envy
    kill "$_env_srv" 2>/dev/null || true
    [ "$status" -eq 0 ]
    grep -q '"env": {}' "$TEST_DIR/req.log"
    [[ "$output" == *"applied env cleared"* ]]
    [[ "$output" != *"WARNING"* ]]
}

@test "new --env with -t refuses loudly (SDK-only flags), and starts nothing" {
    touch "$MOCK_LOG"
    run run_romp new -t --env FEATURE_FLAG=1 x
    [ "$status" -eq 2 ]
    [[ "$output" == *"need the default (SDK) session"* ]]
    run run_romp new -t --no-env x
    [ "$status" -eq 2 ]
    [[ "$output" == *"need the default (SDK) session"* ]]
    [ "$(grep -c 'tmux new-session' "$MOCK_LOG")" -eq 0 ]
}

@test "new: help names --env (the same presence guard as --model/--effort)" {
    run run_romp -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"--env NAME=VALUE"* ]]
}

# ─── romp keyswap is retired (2026-09-08): romp holds no API key ──────
@test "keyswap: retired; prints the rotation procedure and exits 2 without touching anything" {
    export ROMP_SERVICE_ENV_FILE="$TEST_DIR/service.env"
    printf 'ROMP_EXPECTED_AUTH=key\n' > "$TEST_DIR/service.env"
    run "$ROMP_SCRIPT" keyswap anything --cycle-all
    [ "$status" -eq 2 ]
    [[ "$output" == *"romp keyswap is retired"* ]]
    [[ "$output" == *"apiKeyHelper"* ]]
    [[ "$output" == *"ROMP_EXPECTED_AUTH=key"* ]]
    [ "$(cat "$TEST_DIR/service.env")" = "ROMP_EXPECTED_AUTH=key" ]
}

@test "keyswap: the help table no longer lists it, and no romp entry point reads the retired provider names" {
    run "$ROMP_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" != *"keyswap"* ]]
    # the only mention left in bin/romp is the retirement note itself
    local hits
    hits="$(grep -c 'ROMP_API_KEY_CMD\|ROMP_API_KEY_REF\|_romp_op_consumer' "$ROMP_SCRIPT" || true)"
    [ "$hits" -le 1 ]
    ! grep -q 'serviceEnvHasRef\|PROVIDER_VARS' "$(dirname "$ROMP_SCRIPT")/romp-manager"
}

