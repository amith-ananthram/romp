#!/usr/bin/env bats

# A first install on a machine that has neither a VS Code-family editor, nor tmux, nor a pip-capable
# python must still produce a WORKING romp — and must SAY what it turned off. Regression cover for a
# fresh Linux install (the user 2026-07-27) where all three were absent and each failure was swallowed
# by a `|| echo`, leaving a dashboard that served 404s for every bundle and no way to start a session.
#
# Hermetic: HOME is a temp dir, and each test puts stubs ahead of the real tools on PATH.

ROMP_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
    TEST_DIR="$(mktemp -d)"
    export HOME="$TEST_DIR/home"
    STUB="$TEST_DIR/stub"
    mkdir -p "$HOME" "$STUB"
    export CALL_LOG="$TEST_DIR/calls.log"
    export ROMP_NO_SERVICE=1 ROMP_NO_SDK=1 ROMP_NO_EXT=1
    export ROMP_INSTALL_TOKEN_TRIES=1
    # A shell inside a running romp inherits the service's interpreter pin; with it set, romp-sdk-setup
    # skips every stub below and builds a REAL venv (network pip and all) into the temp state dir.
    unset ROMP_PYTHON
    # The state root is this test's own (the setup scripts read ROMP_STATE_DIR first, and a kernel
    # exports it to its sessions), and no port here may reach a live kernel or manager: the manager
    # port is poisoned to a dead value and the kernel port's two spellings are unset.
    export ROMP_STATE_DIR="$TEST_DIR/state"
    export ROMP_MANAGER_PORT=1
    unset ROMP_KERNEL_PORT ROMP_SERVE_PORT
    export ROMP_GITHOOK_DIR="$TEST_DIR/githooks"
    # Keep vscode-extension/install.sh's app-bundle probe inside the sandbox: on a
    # dev mac, /Applications really contains editors, and finding one would send
    # the "no editor" tests down the package-and-install path.
    export ROMP_EDITOR_APPS="$TEST_DIR/no-apps"

    # An ALLOWLIST bin instead of the machine's /usr/bin — "tmux absent" must mean
    # the same thing on every machine, and with a real /usr/bin it doesn't: CI's
    # apt puts tmux there, Debian puts node there, and a mac keeps both in
    # /opt/homebrew. So a PATH of "$STUB:/usr/bin:/bin" is bare on one box and
    # fully equipped on the next (exactly how these tests passed on the box that
    # wrote them and failed on the runner). Symlink only the tools the scripts
    # under test legitimately need; everything else is absent, everywhere.
    BAREBIN="$TEST_DIR/barebin"; mkdir -p "$BAREBIN"
    local t p
    for t in bash sh env dirname basename realpath readlink mktemp mkdir ln cp mv rm \
             cat echo printf grep sed awk tr sort head tail cut wc date chmod touch \
             sleep find xargs uname hostname python3 git curl; do
        p="$(command -v "$t" 2>/dev/null || true)"
        [ -n "$p" ] && ln -s "$p" "$BAREBIN/$t"
    done
}

teardown() { rm -rf "$TEST_DIR"; }

# Stubs first, then the allowlist — nothing from the host machine leaks in (CI's
# apt tmux and Debian's node live in /usr/bin, so a PATH keeping /usr/bin is never
# bare). Belt and braces for tmux: the tests below ALSO state their tmux
# assumption explicitly via ROMP_TMUX_AVAILABLE (the seam install.sh, bin/romp
# and TmuxBackend all honour), so the assertion doesn't ride on PATH mechanics.
bare_path() { echo "$STUB:$BAREBIN"; }

# ── the bug that blanked the dashboard ────────────────────────────────────────
# vscode-extension/install.sh used to check for an editor CLI FIRST and exit 0, so on an
# editor-less machine npm install and esbuild never ran — and the kernel serves that same
# dist/ to the browser. The build must happen before, and regardless of, the editor check.

@test "vscode-extension/install.sh: builds dist even with no editor CLI on the machine" {
    # node/npm stubs that only record what they were asked to do.
    cat > "$STUB/npm" <<'EOF'
#!/usr/bin/env bash
echo "npm $*" >> "$CALL_LOG"
EOF
    cat > "$STUB/node" <<'EOF'
#!/usr/bin/env bash
echo "node $*" >> "$CALL_LOG"
EOF
    chmod +x "$STUB/npm" "$STUB/node"

    # No code/cursor/codium anywhere on this PATH, and no macOS app bundles in a temp HOME.
    PATH="$(bare_path)" run "$ROMP_DIR/vscode-extension/install.sh"

    [ "$status" -eq 0 ]
    # The two steps the browser dashboard depends on both ran...
    grep -q "npm install" "$CALL_LOG"
    grep -q "node esbuild.js" "$CALL_LOG"
    # ...and it said so honestly, instead of the old "built dist/ is ready" on a path that built nothing.
    [[ "$output" == *"dist/ built"* ]]
    [[ "$output" == *"No VS Code-family editor CLI found"* ]]
}

@test "vscode-extension/install.sh: builds BEFORE it looks for an editor (ordering, not just presence)" {
    cat > "$STUB/npm" <<'EOF'
#!/usr/bin/env bash
echo "npm $*" >> "$CALL_LOG"
EOF
    cat > "$STUB/node" <<'EOF'
#!/usr/bin/env bash
echo "node $*" >> "$CALL_LOG"
EOF
    # An editor CLI that records when IT was consulted. If the editor gate ever moves back
    # above the build, this line lands before the npm/esbuild lines and the test fails.
    cat > "$STUB/code" <<'EOF'
#!/usr/bin/env bash
echo "code $*" >> "$CALL_LOG"
EOF
    # The PACKAGE_ONLY path reaches `npx @vscode/vsce package`; a real npx would
    # hit the network (or, on the allowlist PATH, not exist at all).
    cat > "$STUB/npx" <<'EOF'
#!/usr/bin/env bash
echo "npx $*" >> "$CALL_LOG"
EOF
    chmod +x "$STUB/npm" "$STUB/node" "$STUB/code" "$STUB/npx"

    # PACKAGE_ONLY stops before the install-into-editor loop, so the run stays hermetic.
    PATH="$(bare_path)" ROMP_EXT_PACKAGE_ONLY=1 run "$ROMP_DIR/vscode-extension/install.sh"

    npm_line="$(grep -n 'npm install' "$CALL_LOG" | head -1 | cut -d: -f1)"
    build_line="$(grep -n 'node esbuild.js' "$CALL_LOG" | head -1 | cut -d: -f1)"
    [ -n "$npm_line" ] && [ -n "$build_line" ]
    [ "$npm_line" -lt "$build_line" ]
}

# ── tmux is optional, and its absence is advisory (never fatal) ───────────────

@test "install.sh: succeeds with no tmux, and names it as a disabled optional piece" {
    # node exists (preflight needs it) — ONLY tmux is missing, which is the point.
    printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB/node"; chmod +x "$STUB/node"
    PATH="$(bare_path)" ROMP_TMUX_AVAILABLE=0 run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]                       # NOT a preflight failure
    [[ "$output" == *"tmux isn't installed"* ]]
    [[ "$output" == *"romp new"* ]]           # points at the backend that still works
    [[ "$output" == *"install tmux"* ]]       # and the exact remedy
}

@test "install.sh: says nothing about tmux when tmux is present" {
    printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB/node"; chmod +x "$STUB/node"
    cat > "$STUB/tmux" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$STUB/tmux"
    PATH="$(bare_path)" ROMP_TMUX_AVAILABLE=1 run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [[ "$output" != *"tmux isn't installed"* ]]
}

@test "romp new -t: without tmux, fails naming the remedy and the SDK alternative" {
    PATH="$(bare_path)" ROMP_TMUX_AVAILABLE=0 run "$ROMP_DIR/bin/romp" new -t notes-api
    [ "$status" -eq 1 ]
    [[ "$output" == *"tmux isn't installed"* ]]
    [[ "$output" == *"install tmux"* ]]
    # It must offer the path that still works, with the session name carried through.
    [[ "$output" == *"romp new notes-api"* ]]
    # And never leak the raw shell error the launcher would otherwise produce.
    [[ "$output" != *"command not found"* ]]
}

# ── uuidgen is not universal ─────────────────────────────────────────────────
# Debian/Ubuntu ship it in uuid-runtime, which a minimal install omits. It used to fail to an
# EMPTY --session-id rather than to an error, so the session broke with nothing naming the cause.

@test "romp new -t: generates a session id without uuidgen installed" {
    cat > "$STUB/tmux" <<'EOF'
#!/usr/bin/env bash
echo "tmux $*" >> "$CALL_LOG"
case "$1" in
  has-session) exit 1 ;;
  show|show-hooks|list-keys|list-sessions) exit 0 ;;
esac
exit 0
EOF
    cat > "$STUB/claude" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$STUB/tmux" "$STUB/claude"
    # No uuidgen on this PATH — python3 (a hard romp dependency) must cover for it.
    [ ! -x "$STUB/uuidgen" ]

    PATH="$(bare_path)" ROMP_TMUX_AVAILABLE=1 run "$ROMP_DIR/bin/romp" new -t notes-api --detach

    # A real lowercase v4 uuid reached the launch line, not an empty string.
    grep -qE 'session-id [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' "$CALL_LOG"
    ! grep -qE 'session-id *$' "$CALL_LOG"
}

# ── a python that cannot bootstrap pip (Debian without python3-venv) ─────────

@test "romp-sdk-setup: names the venv package when ensurepip is missing, instead of dying at pip" {
    # A python that satisfies the >= 3.10 gate but has no ensurepip — exactly Debian/Ubuntu's
    # split-out python3-venv. Fully self-contained: it answers romp-sdk-setup's probes itself
    # rather than delegating to the host python3, whose version differs per machine (a mac's
    # /usr/bin/python3 is the 3.9 xcode shim, which dies at the version gate and never reaches
    # the ensurepip branch this test is about).
    cat > "$STUB/python3.12" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"version_info >= (3, 10)"*) exit 0 ;;
  *'print("%d.%d"'*)           echo "3.12"; exit 0 ;;
  *"import ensurepip"*)        exit 1 ;;
esac
exit 0
EOF
    chmod +x "$STUB/python3.12"

    export ROMP_STATE_DIR="$TEST_DIR/state"
    PATH="$(bare_path)" ROMP_PYTHON="$STUB/python3.12" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 1 ]
    [[ "$output" == *"ensurepip"* ]]
    [[ "$output" == *"venv"* ]]               # names the package to install
    # Says romp still works without it — this backend being down is not a dead install.
    [[ "$output" == *"romp still runs without this"* ]]
    # And it must NOT have left a pip-less husk behind for the next run to trip over.
    [ ! -x "$TEST_DIR/state/sdkvenv/bin/python" ]
}

@test "romp-sdk-setup: rebuilds a venv that has python but no pip" {
    # Simulate the husk a pre-fix run left behind: bin/python present, bin/pip absent.
    # Gating on python alone (the old check) would skip creation and die at the pip line.
    export ROMP_STATE_DIR="$TEST_DIR/state"
    mkdir -p "$TEST_DIR/state/sdkvenv/bin"
    ln -s "$(command -v python3)" "$TEST_DIR/state/sdkvenv/bin/python"

    # Stub `python3 -m venv` so the test never builds a real venv or hits the network:
    # record that a rebuild was attempted, which is the behaviour under test.
    # ensurepip is answered explicitly rather than delegated — the host running these tests
    # may itself be a Debian box without it, and this test is about the pip-less-husk rebuild,
    # not the ensurepip gate (which test 6 covers).
    # The stub stands in for the whole venv: it records the rebuild, then lays down a bin/pip and
    # bin/python so the rest of romp-sdk-setup runs to a clean exit instead of dying at the pip line
    # (which would leave the test asserting on a crash rather than on the rebuild).
    cat > "$STUB/python3.12" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  echo "venv-rebuild $3" >> "$CALL_LOG"
  mkdir -p "$3/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$3/bin/pip"
  printf '#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n' > "$3/bin/python"
  chmod +x "$3/bin/pip" "$3/bin/python"
  exit 0
fi
case "$*" in
  *"version_info >= (3, 10)"*) exit 0 ;;
  *'print("%d.%d"'*)           echo "3.12"; exit 0 ;;
  *"import ensurepip"*)        exit 0 ;;
esac
exit 0
EOF
    chmod +x "$STUB/python3.12"

    PATH="$(bare_path)" ROMP_PYTHON="$STUB/python3.12" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 0 ]
    grep -q "venv-rebuild" "$CALL_LOG"
}

# ── a venv built for one interpreter, a kernel about to run another ──────────
# pick_python follows the venv's pyvenv.cfg, so romp-sdk-setup only ever rebuilds for a different
# interpreter when ROMP_PYTHON says so or the recorded one is gone. Both are deliberate interpreter
# changes with a running kernel still on the old one: say so LOUDLY (2026-09-06: a silent mismatch
# took every SDK session down for two hours).

# The stub interpreter for these tests: answers romp-sdk-setup's probes as a 3.12, and stands in for
# `python -m venv` by laying down a pip and a python that read stdin and exit 0.
write_stub_312() {
    cat > "$STUB/python3.12" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  echo "venv-rebuild $3" >> "$CALL_LOG"
  mkdir -p "$3/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$3/bin/pip"
  printf '#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n' > "$3/bin/python"
  chmod +x "$3/bin/pip" "$3/bin/python"
  printf 'home = %s\nversion = 3.12.0\nexecutable = %s\n' "$STUB" "$STUB/python3.12" > "$3/pyvenv.cfg"
  exit 0
fi
case "$*" in
  *"version_info >= (3, 10)"*) exit 0 ;;
  *'print("%d.%d%s"'*)         echo "3.12"; exit 0 ;;
  *'print("%d.%d"'*)           echo "3.12"; exit 0 ;;
  *"import ensurepip"*)        exit 0 ;;
esac
exit 0
EOF
    chmod +x "$STUB/python3.12"
}

@test "romp-sdk-setup: ROMP_PYTHON naming a different interpreter rebuilds the venv and says so loudly" {
    VENV="$TEST_DIR/state/sdkvenv"; mkdir -p "$VENV/bin" "$TEST_DIR/oldpy"
    # the venv as built for a 3.11 that is still on the machine: its python answers the version probe
    printf '#!/usr/bin/env bash\ncase "$*" in *print*) echo 3.11 ;; esac\nexit 0\n' > "$TEST_DIR/oldpy/python3.11"
    chmod +x "$TEST_DIR/oldpy/python3.11"
    ln -s "$TEST_DIR/oldpy/python3.11" "$VENV/bin/python"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$VENV/bin/pip"; chmod +x "$VENV/bin/pip"
    printf 'home = %s\nversion = 3.11.9\nexecutable = %s\n' "$TEST_DIR/oldpy" "$TEST_DIR/oldpy/python3.11" > "$VENV/pyvenv.cfg"
    write_stub_312

    PATH="$(bare_path)" ROMP_PYTHON="$STUB/python3.12" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 0 ]
    grep -q "venv-rebuild" "$CALL_LOG"
    [[ "$output" == *"REBUILDING"* ]]                 # not a one-word aside
    [[ "$output" == *"3.11"* && "$output" == *"3.12"* ]]   # from what, to what
    [[ "$output" == *"restart romp"* ]]               # the running kernel is still on the old one
}

@test "romp-sdk-setup: without ROMP_PYTHON it follows the venv's interpreter and does NOT rebuild" {
    # The agree-by-construction case: the recorded interpreter is present, so a re-run (say, to
    # upgrade the SDK) keeps the venv's python even with a newer one first on PATH.
    VENV="$TEST_DIR/state/sdkvenv"; mkdir -p "$VENV/bin" "$TEST_DIR/oldpy"
    cat > "$TEST_DIR/oldpy/python3.11" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"version_info >= (3, 10)"*) exit 0 ;;
  *'print("%d.%d%s"'*)         echo "3.11"; exit 0 ;;
  *'print("%d.%d"'*)           echo "3.11"; exit 0 ;;
  -)                           cat >/dev/null; echo "stub: claude-agent-sdk ready (python 3.11)" ;;
esac
exit 0
EOF
    chmod +x "$TEST_DIR/oldpy/python3.11"
    ln -s "$TEST_DIR/oldpy/python3.11" "$VENV/bin/python"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$VENV/bin/pip"; chmod +x "$VENV/bin/pip"
    printf 'home = %s\nversion = 3.11.9\nexecutable = %s\n' "$TEST_DIR/oldpy" "$TEST_DIR/oldpy/python3.11" > "$VENV/pyvenv.cfg"
    write_stub_312                                    # a newer 3.12 first on PATH

    PATH="$(bare_path)" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 0 ]
    [[ "$output" != *"REBUILDING"* ]]
    [[ "$output" == *"python 3.11"* ]]                # the ready line names the venv's own interpreter
    run grep -q "venv-rebuild" "$CALL_LOG"     # last, and armed: `run` replaces $output, and a bare
    [ "$status" -ne 0 ]                        # `!` mid-test asserts nothing in bats
}

@test "romp-sdk-setup: a venv whose interpreter is gone is rebuilt for the fallback pick, loudly" {
    VENV="$TEST_DIR/state/sdkvenv"; mkdir -p "$VENV/bin"
    ln -s "$TEST_DIR/gone/python3.11" "$VENV/bin/python"      # dangling: the interpreter was removed
    printf '#!/usr/bin/env bash\nexit 0\n' > "$VENV/bin/pip"; chmod +x "$VENV/bin/pip"
    printf 'home = %s\nversion = 3.11.9\nexecutable = %s\n' "$TEST_DIR/gone" "$TEST_DIR/gone/python3.11" > "$VENV/pyvenv.cfg"
    write_stub_312

    PATH="$(bare_path)" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 0 ]
    grep -q "venv-rebuild" "$CALL_LOG"
    [[ "$output" == *"$TEST_DIR/gone/python3.11"* ]]  # pick_python's own line: what it could not run
    [[ "$output" == *"REBUILDING"* ]]
    [[ "$output" == *"3.11"* && "$output" == *"3.12"* ]]   # the cfg's version, not a bare "?"
}

@test "romp-sdk-setup: the rebuild check reads the venv's record, never its live bin/python (a repointed unversioned base rebuilds)" {
    # The venv was built under ROMP_PYTHON=<prefix>/python3 when that was a 3.12, so bin/python is a
    # symlink to the UNVERSIONED base. A distro upgrade has since repointed python3 at 3.14: the symlink
    # answers 3.14, lib/ is still python3.12. Asking bin/python saw a match and skipped the rebuild, and
    # the session card's remedy (re-run this script) then changed nothing.
    VENV="$TEST_DIR/state/sdkvenv"; mkdir -p "$VENV/bin" "$VENV/lib/python3.12/site-packages"
    write_stub_py "$TEST_DIR/usr/python3" 3.14
    ln -s "$TEST_DIR/usr/python3" "$VENV/bin/python"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$VENV/bin/pip"; chmod +x "$VENV/bin/pip"
    printf 'home = %s\nversion = 3.12.4\nexecutable = %s\n' "$TEST_DIR/usr" "$TEST_DIR/usr/python3.12" > "$VENV/pyvenv.cfg"

    PATH="$(bare_path)" ROMP_PYTHON="$TEST_DIR/usr/python3" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 0 ]
    grep -q "venv-build 3.14 $VENV" "$CALL_LOG"
    [[ "$output" == *"REBUILDING"* ]]
    [[ "$output" == *"for python 3.14 ("* && "$output" == *"built for python 3.12."* ]]
    [ -d "$VENV/lib/python3.14" ] && [ ! -d "$VENV/lib/python3.12" ]
}

@test "romp-sdk-setup: a free-threaded build of the same minor is another tag (3.14 to 3.14t rebuilds; 3.14t again does not)" {
    # venv names the lib directory python3.14t and the kernel keys its match on that tag, so a compare on
    # X.Y alone kept a python3.14 venv for a 3.14t kernel and reported it ready
    VENV="$TEST_DIR/state/sdkvenv"; mkdir -p "$VENV/bin" "$VENV/lib/python3.14/site-packages"
    write_stub_py "$TEST_DIR/py/python3.14" 3.14
    write_stub_py "$TEST_DIR/py/python3.14t" 3.14 t
    ln -s "$TEST_DIR/py/python3.14" "$VENV/bin/python"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$VENV/bin/pip"; chmod +x "$VENV/bin/pip"
    printf 'home = %s\nversion = 3.14.0\nexecutable = %s\n' "$TEST_DIR/py" "$TEST_DIR/py/python3.14" > "$VENV/pyvenv.cfg"

    PATH="$(bare_path)" ROMP_PYTHON="$TEST_DIR/py/python3.14t" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 0 ]
    grep -q "venv-build 3.14t $VENV" "$CALL_LOG"
    [[ "$output" == *"REBUILDING"* ]]
    [[ "$output" == *"for python 3.14t ("* && "$output" == *"built for python 3.14."* ]]
    [ -d "$VENV/lib/python3.14t" ]

    # the venv the 3.14t build just made is its own: a re-run keeps it
    : > "$CALL_LOG"
    PATH="$(bare_path)" ROMP_PYTHON="$TEST_DIR/py/python3.14t" run "$ROMP_DIR/bin/romp-sdk-setup"
    [ "$status" -eq 0 ]
    [[ "$output" != *"REBUILDING"* ]]
    run grep -q "venv-build" "$CALL_LOG"                     # last, and armed (see the twin above)
    [ "$status" -ne 0 ]
}

@test "romp-sdk-setup: ROMP_PYTHON naming a missing interpreter is refused as such, not called a too-old python" {
    # The pin the docs recommend for service.env, after an OS upgrade removed what it named. The old
    # diagnosis was "best python found is <pin> (?) but claude-agent-sdk needs >= 3.10", and its remedy
    # (install a newer python) changed nothing while the pin pointed at a dead path.
    PATH="$(bare_path)" ROMP_PYTHON="$TEST_DIR/no-such/python3.12" run "$ROMP_DIR/bin/romp-sdk-setup"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ROMP_PYTHON=$TEST_DIR/no-such/python3.12"* ]]
    [[ "$output" == *"not an executable interpreter"* ]]
    [[ "$output" != *"needs >= 3.10"* ]]
    [ ! -e "$TEST_DIR/state/sdkvenv" ]
}

# ── romp-codex-setup: the Codex venv is built with the kernel's interpreter too ──────────────────
# The kernel imports codexvenv's site-packages in-process (ensure_codex_sdk), so this venv has the same
# contract as the SDK venv: built with the python romp-serve will run. tests/romp-serve.bats pins the
# three picker copies byte for byte, and these tests exercise the script end to end.

# A stub python that claims one X.Y (and, with a third argument `t`, a free-threaded build): answers
# pick_python's minor check for that X.Y only, the >= 3.10 gate, the version and tag prints and the
# ensurepip probe, and stands in for `python -m venv` by laying down a pip and a python that read stdin
# and exit 0, plus the tagged lib/python3.X{t} directory a real venv has, logging which python built
# which venv.
write_stub_py() {   # $1 path, $2 the X.Y it claims, [$3 abi suffix: t]
    mkdir -p "$(dirname "$1")"
    cat > "$1" <<EOF
#!/usr/bin/env bash
if [ "\${1:-}" = "-m" ] && [ "\${2:-}" = "venv" ]; then
  echo "venv-build $2${3:-} \$3" >> "\$CALL_LOG"
  mkdir -p "\$3/bin" "\$3/lib/python$2${3:-}/site-packages"
  printf '#!/usr/bin/env bash\nexit 0\n' > "\$3/bin/pip"
  printf '#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n' > "\$3/bin/python"
  chmod +x "\$3/bin/pip" "\$3/bin/python"
  printf 'version = $2.0\nexecutable = $1\n' > "\$3/pyvenv.cfg"
  exit 0
fi
case "\$*" in
  *"version_info >= (3, 10)"*) exit 0 ;;
  *'print("%d.%d%s"'*)         echo "$2${3:-}"; exit 0 ;;   # before the version_info catch-all: the probes name it too
  *'print("%d.%d"'*)           echo "$2"; exit 0 ;;
  *"(${2%%.*}, ${2#*.})"*)     exit 0 ;;
  *version_info*)              exit 1 ;;
esac
exit 0
EOF
    chmod +x "$1"
}

@test "romp-codex-setup: ROMP_PYTHON naming a missing interpreter is refused as such, not called a too-old python" {
    PATH="$(bare_path)" ROMP_PYTHON="$TEST_DIR/no-such/python3.12" run "$ROMP_DIR/bin/romp-codex-setup"
    [ "$status" -eq 1 ]
    [[ "$output" == *"ROMP_PYTHON=$TEST_DIR/no-such/python3.12"* ]]
    [[ "$output" == *"not an executable interpreter"* ]]
    [[ "$output" != *"needs >= 3.10"* ]]
    [ ! -e "$TEST_DIR/state/codexvenv" ]
}

@test "romp-codex-setup: builds its venv with the SDK venv's interpreter (the kernel's), not the newest python on PATH" {
    # A codexvenv built with the newest python while the kernel runs the SDK venv's 3.11 would fail at
    # import under the kernel exactly as the SDK venv did on 2026-09-06.
    mkdir -p "$TEST_DIR/state/sdkvenv"
    printf 'home = %s\nversion = 3.11.9\nexecutable = %s\n' "$TEST_DIR/oldpy" "$TEST_DIR/oldpy/python3.11" \
        > "$TEST_DIR/state/sdkvenv/pyvenv.cfg"
    write_stub_py "$TEST_DIR/oldpy/python3.11" 3.11          # the SDK venv's interpreter, off PATH
    write_stub_py "$STUB/python3.12" 3.12                    # a newer python, first on PATH

    PATH="$(bare_path)" run "$ROMP_DIR/bin/romp-codex-setup"

    [ "$status" -eq 0 ]
    grep -q "venv-build 3.11 $TEST_DIR/state/codexvenv" "$CALL_LOG"
    [[ "$output" == *"creating venv at $TEST_DIR/state/codexvenv (python: $TEST_DIR/oldpy/python3.11)"* ]]
    grep -q "executable = $TEST_DIR/oldpy/python3.11" "$TEST_DIR/state/codexvenv/pyvenv.cfg"
    run grep -q "venv-build 3.12" "$CALL_LOG"                # last, and armed (see the sdk-setup twin)
    [ "$status" -ne 0 ]
}

@test "romp-codex-setup: the rebuild check reads the venv's record, never its live bin/python" {
    # the same repointed-base case as the SDK venv's: bin/python answers the new version, lib/ is the old
    VENV="$TEST_DIR/state/codexvenv"; mkdir -p "$VENV/bin" "$VENV/lib/python3.12/site-packages"
    write_stub_py "$TEST_DIR/usr/python3" 3.14
    ln -s "$TEST_DIR/usr/python3" "$VENV/bin/python"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$VENV/bin/pip"; chmod +x "$VENV/bin/pip"
    printf 'home = %s\nversion = 3.12.4\nexecutable = %s\n' "$TEST_DIR/usr" "$TEST_DIR/usr/python3.12" > "$VENV/pyvenv.cfg"

    PATH="$(bare_path)" ROMP_PYTHON="$TEST_DIR/usr/python3" run "$ROMP_DIR/bin/romp-codex-setup"

    [ "$status" -eq 0 ]
    grep -q "venv-build 3.14 $VENV" "$CALL_LOG"
    [[ "$output" == *"REBUILDING the Codex venv for python 3.14 ("* && "$output" == *"built for python 3.12."* ]]
    [ -d "$VENV/lib/python3.14" ] && [ ! -d "$VENV/lib/python3.12" ]
}

# ── installs ship a PRODUCTION bundle ────────────────────────────────────────
# Without --production the dashboard shipped a development build: render.js, the chat pane's
# code, was 578 KB of unminified JS the browser parsed before anything appeared (a slow chat
# load on a fresh install). Minified it is 297 KB and no sourcemaps are emitted at all.

@test "vscode-extension/install.sh: builds minified for an install, not a dev bundle" {
    cat > "$STUB/npm" <<'EOF'
#!/usr/bin/env bash
echo "npm $*" >> "$CALL_LOG"
EOF
    cat > "$STUB/node" <<'EOF'
#!/usr/bin/env bash
echo "node $*" >> "$CALL_LOG"
EOF
    chmod +x "$STUB/npm" "$STUB/node"

    PATH="$(bare_path)" run "$ROMP_DIR/vscode-extension/install.sh"
    [ "$status" -eq 0 ]
    grep -q 'node esbuild.js --production' "$CALL_LOG"
}

@test "vscode-extension/install.sh: ROMP_EXT_DEV_BUILD keeps the readable bundle for a UI dev loop" {
    cat > "$STUB/npm" <<'EOF'
#!/usr/bin/env bash
echo "npm $*" >> "$CALL_LOG"
EOF
    cat > "$STUB/node" <<'EOF'
#!/usr/bin/env bash
echo "node $*" >> "$CALL_LOG"
EOF
    chmod +x "$STUB/npm" "$STUB/node"

    PATH="$(bare_path)" ROMP_EXT_DEV_BUILD=1 run "$ROMP_DIR/vscode-extension/install.sh"
    [ "$status" -eq 0 ]
    grep -qE 'node esbuild.js *$' "$CALL_LOG"
    ! grep -q -- '--production' "$CALL_LOG"
}

# ── the finish line points at the command, not just a URL ────────────────────

@test "install.sh: ends by telling you to type romp, keeping the link as the fallback" {
    # Force the "romp is running" branch: that block needs a minted token to print.
    export ROMP_STATE_DIR="$TEST_DIR/state"
    mkdir -p "$ROMP_STATE_DIR"
    echo "TESTTOKEN123" > "$ROMP_STATE_DIR/serve-token"

    # node is absent from the allowlist bin by design; preflight needs one.
    printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB/node"; chmod +x "$STUB/node"

    PATH="$(bare_path)" ROMP_TMUX_AVAILABLE=1 run "$ROMP_DIR/install.sh"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Open a new terminal and type:  romp"* ]]
    # The URL must survive as the fallback — this terminal's PATH is stale, and a headless
    # box has no browser for `romp` to open.
    [[ "$output" == *"token=TESTTOKEN123"* ]]
}

# ── romp installs its own critical dependency, rather than assigning homework ──
# The SDK backend is what plain `romp new` runs on, so a box without it has a romp that starts,
# looks healthy and cannot run a single session. romp-sdk-setup used to stop at Debian's missing
# ensurepip and tell the user to sudo — which an installer cannot do for them, and which is exactly
# where a fresh python3.14 install stalled (the user 2026-07-28). It now builds the venv without pip
# and bootstraps pip itself; the sudo message is the LAST resort, not the first answer.

# A python that passes the >= 3.10 gate, has no ensurepip, and can fake `-m venv --without-pip`
# well enough to exercise the bootstrap. Self-contained for the same reason as the test above: the
# host's own python3 differs per machine.
_pipless_python() {
    cat > "$STUB/python3.12" <<EOF
#!/usr/bin/env bash
case "\$*" in
  *"version_info >= (3, 10)"*) exit 0 ;;
  *'print("%d.%d"'*)           echo "3.12"; exit 0 ;;
  *"import ensurepip"*)        exit 1 ;;
  *"-m venv --without-pip"*)
      v="\${@: -1}"
      mkdir -p "\$v/bin"
      # the venv's python: running get-pip.py is what mints bin/pip
      printf '#!/usr/bin/env bash\ncase "\$*" in *get-pip.py*) printf "#!/usr/bin/env bash\\nexit 0\\n" > "\$(dirname "\$0")/pip"; chmod +x "\$(dirname "\$0")/pip";; esac\nexit 0\n' > "\$v/bin/python"
      chmod +x "\$v/bin/python"
      exit 0 ;;
esac
exit 0
EOF
    chmod +x "$STUB/python3.12"
}

@test "romp-sdk-setup: bootstraps pip itself when ensurepip is missing — no sudo, no homework" {
    _pipless_python
    export ROMP_STATE_DIR="$TEST_DIR/state"
    # file:// keeps the fetch hermetic — curl handles it, and no test may reach the network.
    printf '# a stand-in for PyPA get-pip.py\n' > "$TEST_DIR/get-pip.py"

    PATH="$(bare_path)" ROMP_PYTHON="$STUB/python3.12" \
      ROMP_GET_PIP_URL="file://$TEST_DIR/get-pip.py" run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 0 ]
    [[ "$output" == *"bootstrapping pip"* ]]
    # the whole point: it must NOT send the user to sudo when it can do the job itself
    [[ "$output" != *"sudo apt install"* ]]
    [ -x "$TEST_DIR/state/sdkvenv/bin/pip" ]
    # the downloaded bootstrap script is not left lying in the venv
    [ ! -f "$TEST_DIR/state/sdkvenv/get-pip.py" ]
}

@test "romp-sdk-setup: ROMP_NO_GET_PIP opts out, and then it names the package" {
    _pipless_python
    export ROMP_STATE_DIR="$TEST_DIR/state"

    PATH="$(bare_path)" ROMP_PYTHON="$STUB/python3.12" ROMP_NO_GET_PIP=1 \
      run "$ROMP_DIR/bin/romp-sdk-setup"

    [ "$status" -eq 1 ]
    [[ "$output" == *"sudo apt install"* ]]           # the fallback is still there for anyone who wants it
    [[ "$output" == *"romp still runs without this"* ]]
    [ ! -x "$TEST_DIR/state/sdkvenv/bin/python" ]     # and never a husk for the next run to trip over
}

@test "install.sh: a missing SDK backend is a BANNER, not an optional-pieces footnote" {
    export ROMP_STATE_DIR="$TEST_DIR/state"
    mkdir -p "$ROMP_STATE_DIR"
    echo "TESTTOKEN123" > "$ROMP_STATE_DIR/serve-token"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$STUB/node"; chmod +x "$STUB/node"
    # Let the real sdk step RUN (ROMP_NO_SDK cleared — that flag is what sets ROMP_SDK_MISSING) but
    # make it fail at the VERSION gate, so this stays hermetic: no venv built, no network reached.
    cat > "$STUB/oldpython" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *"version_info >= (3, 10)"*) exit 1 ;;
  *'print("%d.%d"'*)           echo "3.9"; exit 0 ;;
esac
exit 0
EOF
    chmod +x "$STUB/oldpython"

    PATH="$(bare_path)" ROMP_TMUX_AVAILABLE=1 ROMP_NO_SDK= ROMP_PYTHON="$STUB/oldpython" \
      run "$ROMP_DIR/install.sh"

    [ "$status" -eq 0 ]
    [[ "$output" == *"CANNOT START SESSIONS"* ]]
    # it must NOT be filed under the things you can happily live without
    [[ "$output" != *"Some optional pieces aren't set up:"*"Agent SDK"* ]]
}
