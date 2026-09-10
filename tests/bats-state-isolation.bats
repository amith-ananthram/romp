#!/usr/bin/env bats

# Every bats suite that starts the REAL bin/romp-manager gives it a state root of its own. The
# manager's STATE_ROOT is ROMP_STATE_DIR || XDG_STATE_HOME/romp || ~/.local/state/romp, and a suite
# whose setup() sets neither hands the manager the developer's live root: `up` loads that root's
# kernels.json and spawns one child per registered kernel (the suite's fake launcher, with the
# registry entry's stateDir as its ROMP_STATE_DIR: specEnv), and the drain poll's token comes from
# that root's serve-token (kernelToken). The manager writes nothing there and dials no live kernel,
# so the leak is silent: the suite passes while a fake kernel runs for every kernel registered on the
# machine. This is the ratchet for the bats side, as tests/test_state_isolation_order.py is for the
# pytest modules: the isolation lines must be present in each such suite, before its first @test
# (setup() runs before every test; a floor set inside one test leaves the others on the live root).
#
# The two lines, as the suites spell them:
#     unset ROMP_STATE_DIR                    # a profiled kernel's sessions inherit it; it outranks the XDG floor
#     export XDG_STATE_HOME="$TEST_DIR/state"  # or another path under a shell variable
# A suite that instead exports ROMP_STATE_DIR to such a path satisfies both, and so do the equivalent
# spellings: `X="$TEST_DIR/..."` on one line with an `export X` on another, or the assignment unquoted.
# The check reads the line's shape, not where the path resolves: every suite today uses `$TEST_DIR`, or
# `$HOME` after re-exporting HOME under its test directory (tests/romp.bats); a variable left pointing
# at a live path would pass it. A helper the suite `load`s counts as part of the suite, both ways: a floor
# set in a helper's function isolates, and a helper that starts the manager makes the suite one that
# does. Any spelling of the load counts (`load name`, `load "$BATS_TEST_DIRNAME/name"`, `source`),
# and a helper's own loads are read in turn.

TESTS="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"

setup() { SYN="$(mktemp -d)"; }   # the self-checks' synthetic suites, removed whether or not the check passes
teardown() { rm -rf "$SYN"; }

# The helper files a suite brings in, one per line, nothing for a suite that loads none: every `load`
# (bats resolves `load name` to name, else name.bash, beside the suite; a path as given when absolute),
# `source` and `.` line, with `$BATS_TEST_DIRNAME` or `${BATS_TEST_DIRNAME}` in the argument expanded
# to the suite's directory, and each helper's own such lines read in turn, every file once (a helper
# that loads a second helper, or two helpers that load each other, is read as one chain).
loaded_helpers() {   # $1 suite
    local _seen="|"
    _helpers_of "$1" "$1"
    return 0
}
_helpers_of() {   # $1 suite, $2 the file whose load lines to read; appends to the caller's _seen
    local dir name f
    dir="$(dirname "$1")"
    while IFS= read -r name; do
        name="${name%\"}"; name="${name#\"}"; name="${name%\'}"; name="${name#\'}"
        [ -n "$name" ] || continue
        name="${name//\$\{BATS_TEST_DIRNAME\}/$dir}"; name="${name//\$BATS_TEST_DIRNAME/$dir}"
        case "$name" in /*) ;; *) name="$dir/$name" ;; esac
        for f in "$name" "$name.bash"; do
            [ -f "$f" ] || continue
            case "$_seen" in
                *"|$f|"*) ;;                                  # already listed: a loop, or two loaders
                *) _seen="$_seen$f|"; printf '%s\n' "$f"; _helpers_of "$1" "$f" ;;
            esac
            break
        done
    done < <(sed -nE 's/^[[:space:]]*(load|source|\.)[[:space:]]+([^[:space:]]+).*/\2/p' "$2")
    return 0
}

# A suite "starts the manager" when it, or a helper it loads, resolves the repository's own binary for
# execution: a path climbing from the tests directory (`.../bin" && pwd)/romp-manager"` or
# `../bin/romp-manager"`), or `"$<VAR>/.../romp-manager"` where a command starts: the line's first word
# or the word after `;`, `&&`, `||`, `|`, `(`, `{`, `then`, `do` or `else`, with any `VAR=value`
# prefixes and a `node`, `run`, `exec`, `nohup`, `env` or `timeout N` in front. Comment lines do not
# count, and neither do stand-ins written under a test directory (`"$MOCK_DIR/romp-manager"` or
# `"$TEST_DIR/bin/romp-manager"` as a heredoc or printf target, a chmod operand, or the value of
# ROMP_MANAGER_BIN: none is a command, and a path under a test directory never climbs to `../bin`;
# tests/romp-refresh-audit.bats drives bin/romp against such a stand-in and starts no manager), nor a
# line that only talks about the binary (TALK_ONLY_RE: a grep, an assert, a `[[ ]]` or `[ ]` test
# naming it in an operand; tests/romp-uninstall.bats greps its uninstaller for a pkill pattern that
# names the binary). A new spelling of the real binary is added to MANAGER_START_RE with the suite
# that introduces it, and the suite to the exact list below.
CMD_START='(^|[;&|({]|[[:space:]](then|do|else))[[:space:]]*(([A-Za-z_][A-Za-z_0-9]*=("[^"]*"|[^[:space:]]*)|node|run( -[^[:space:]]+)*|exec|nohup|env|timeout[[:space:]]+[0-9]+[smh]?)[[:space:]]+)*'
MANAGER_START_RE='(pwd\)/romp-manager"|\.\./bin/romp-manager"|'"$CMD_START"'"\$\{?[A-Za-z_][A-Za-z_0-9]*\}?(/[^/"[:space:]]+)*/romp-manager"([[:space:]]|$))'
TALK_ONLY_RE='^[[:space:]]*(run[[:space:]]+(-[^[:space:]]+[[:space:]]+)*)?(grep|egrep|fgrep|assert[A-Za-z_]*|refute[A-Za-z_]*|test|\[\[?|!)([[:space:]]|$)'

suite_text() {   # $1 suite: the suite plus every helper it loads, comment and talk-only lines dropped
    local f
    grep -Ev '^[[:space:]]*#' "$1" | grep -Ev "$TALK_ONLY_RE"
    while IFS= read -r f; do grep -Ev '^[[:space:]]*#' "$f" | grep -Ev "$TALK_ONLY_RE"; done < <(loaded_helpers "$1")
    return 0
}

# The suite's text is collected whole and matched after, never piped into `grep -q`: that grep stops at
# its first hit, and a writer still behind it then meets EPIPE. Silent while SIGPIPE kills the writer,
# but a runner that starts every child with SIGPIPE ignored (GitHub Actions does) has each such grep
# print `grep: write error: Broken pipe`, which `run` collects into $output beside the paths.
manager_suites() {
    local f text
    for f in "$TESTS"/*.bats; do
        [ "$(basename "$f")" = "$(basename "$BATS_TEST_FILENAME")" ] && continue   # this file quotes the pattern in its self-checks
        text="$(suite_text "$f")"
        if grep -Eq "$MANAGER_START_RE" <<<"$text"; then
            printf '%s\n' "$f"
        fi
    done
    return 0
}

before_first_test() { sed -n '1,/^@test /p' "$1"; }

# What a suite runs before its first @test: its own head plus every loaded helper, whole (a helper's
# functions run from setup()).
suite_head() {   # $1 suite
    local f
    before_first_test "$1"
    while IFS= read -r f; do cat "$f"; done < <(loaded_helpers "$1")
    return 0
}

# Whether `text` assigns VAR to a path under a shell variable and exports it: `export VAR="$..."` (other
# assignments may share the line), or `VAR="$..."` with an `export VAR` elsewhere (same line after a
# `;`, or its own line). The quote is optional.
assigned_and_exported() {   # $1 VAR, $2 text
    grep -Eq "^[[:space:]]*export[[:space:]]+([A-Za-z_][A-Za-z_0-9]*(=[^[:space:]]*)?[[:space:]]+)*$1=\"?\\\$" <<<"$2" && return 0
    grep -Eq "^[[:space:]]*$1=\"?\\\$" <<<"$2" \
        && grep -Eq "(^|;)[[:space:]]*export[[:space:]]+([A-Za-z_][A-Za-z_0-9]*[[:space:]]+)*$1([[:space:]]|;|$)" <<<"$2"
}

# One line per missing isolation line for a suite, nothing when it is isolated.
isolation_problems() {   # $1 suite
    local head
    head="$(suite_head "$1")"
    if ! grep -Eq '^[[:space:]]*unset[[:space:]]+([A-Za-z_][A-Za-z_0-9]*[[:space:]]+)*ROMP_STATE_DIR([[:space:]]|;|$)' <<<"$head" \
       && ! assigned_and_exported ROMP_STATE_DIR "$head"; then
        echo "$(basename "$1"): neither 'unset ROMP_STATE_DIR' nor an export of it to a test path before the first @test"
    fi
    if ! assigned_and_exported XDG_STATE_HOME "$head" && ! assigned_and_exported ROMP_STATE_DIR "$head"; then
        echo "$(basename "$1"): no 'export XDG_STATE_HOME=\"\$TEST_DIR/...\"' (or ROMP_STATE_DIR) before the first @test"
    fi
    return 0
}

@test "the detector finds exactly the suites known to start a manager (a regex drift cannot pass vacuously, or list a mention)" {
    # the list is exact, not a floor: a suite that only names the binary in a grep pattern or an assert
    # (tests/romp-uninstall.bats), or drives bin/romp against a stand-in under its test directory
    # (tests/romp-refresh-audit.bats), must not appear. A new suite that starts the real manager is
    # added here, with its isolation lines.
    run manager_suites
    [ "$status" -eq 0 ]
    local expected
    # romp-manager-tmux-scope.bats starts the real manager to read where its tmux server lands, and
    # floors its state root with an exported ROMP_STATE_DIR in setup().
    expected="$(printf '%s\n' "$TESTS/romp-manager-ensure.bats" "$TESTS/romp-manager-origin.bats" \
        "$TESTS/romp-manager-tmux-scope.bats" "$TESTS/romp.bats" | LC_ALL=C sort)"
    [ "$(printf '%s\n' "$output" | LC_ALL=C sort)" = "$expected" ]
}

@test "every suite that starts the real manager isolates its state root before its first @test" {
    local f bad=()
    while IFS= read -r f; do
        while IFS= read -r line; do bad+=("$line"); done < <(isolation_problems "$f")
    done < <(manager_suites)
    if [ "${#bad[@]}" -ne 0 ]; then
        printf 'These suites start a real bin/romp-manager without a state root of their own; it would boot\n' >&2
        printf 'from the live kernels.json, one fake kernel per registered kernel, and read the live serve-token:\n' >&2
        printf '  %s\n' "${bad[@]}" >&2
        return 1
    fi
}

# The self-checks below build synthetic suites line by line: a `@test` at column 0 inside a heredoc
# here would register with bats as a test of THIS file.

@test "the ratchet itself rejects a suite that starts a manager on the live root" {
    # the detector must see it and the rule must fail it, else a regex drift would make the test above
    # pass with nothing checked
    local d="$SYN"
    printf '%s\n' '#!/usr/bin/env bats' 'setup() {' '    TEST_DIR="$(mktemp -d)"' \
        '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' '}' \
        '@'"test \"starts one\" { node \"\$MGR\" up; }" > "$d/leaky.bats"
    grep -q '^@test ' "$d/leaky.bats"                          # the fixture has a real first test line
    TESTS="$d" run manager_suites
    [ "$output" = "$d/leaky.bats" ]
    run isolation_problems "$d/leaky.bats"
    [ "${#lines[@]}" -eq 2 ]
    [[ "${lines[0]}" == "leaky.bats: neither 'unset ROMP_STATE_DIR'"* ]]
    [[ "${lines[1]}" == "leaky.bats: no 'export XDG_STATE_HOME"* ]]
}

@test "a manager started through a loaded helper is seen, and the helper's missing floor is reported" {
    # the suite file alone never names the binary: the helper resolves and starts it
    local d="$SYN"
    printf '%s\n' '# a helper that starts the real manager' 'start_mgr() {' \
        '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' \
        '    node "$MGR" up &' '}' > "$d/mgr-helper.bash"
    printf '%s\n' '#!/usr/bin/env bats' 'load mgr-helper' 'setup() {' '    TEST_DIR="$(mktemp -d)"' \
        '    start_mgr' '}' '@'"test \"starts one\" { true; }" > "$d/viahelper.bats"
    run loaded_helpers "$d/viahelper.bats"
    [ "$output" = "$d/mgr-helper.bash" ]
    TESTS="$d" run manager_suites
    [ "$output" = "$d/viahelper.bats" ]
    run isolation_problems "$d/viahelper.bats"
    [ "${#lines[@]}" -eq 2 ]
}

@test "a floor set in a loaded helper, in the equivalent spellings, isolates the suite" {
    # the assignment and the export on separate lines, the unset with a second variable: all accepted
    local d="$SYN"
    printf '%s\n' 'state_floor() {' '    unset OTHER_VAR ROMP_STATE_DIR' \
        '    XDG_STATE_HOME="$TEST_DIR/state"; export XDG_STATE_HOME' '}' > "$d/floor.bash"
    printf '%s\n' '#!/usr/bin/env bats' 'load floor' 'setup() {' '    TEST_DIR="$(mktemp -d)"' \
        '    state_floor' \
        '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' '}' \
        '@'"test \"starts one\" { node \"\$MGR\" up; }" > "$d/isolated.bats"
    TESTS="$d" run manager_suites
    [ "$output" = "$d/isolated.bats" ]
    run isolation_problems "$d/isolated.bats"
    [ -z "$output" ]
    # and the other accepted spelling: ROMP_STATE_DIR exported to a test path, unquoted, no XDG line
    printf '%s\n' '#!/usr/bin/env bats' 'setup() {' '    TEST_DIR="$(mktemp -d)"' \
        '    ROMP_STATE_DIR=$TEST_DIR/state' '    export ROMP_STATE_DIR' \
        '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' '}' \
        '@'"test \"starts one\" { node \"\$MGR\" up; }" > "$d/rompdir.bats"
    run isolation_problems "$d/rompdir.bats"
    [ -z "$output" ]
}

@test "a floor set only inside a test, or only mentioned in a comment, does not isolate the suite" {
    local d="$SYN"
    printf '%s\n' '#!/usr/bin/env bats' '# export XDG_STATE_HOME="$TEST_DIR/state" would go here' 'setup() {' \
        '    TEST_DIR="$(mktemp -d)"' \
        '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' '}' \
        '@'"test \"starts one\" {" '    unset ROMP_STATE_DIR' '    export XDG_STATE_HOME="$TEST_DIR/state"' \
        '    node "$MGR" up' '}' > "$d/late.bats"
    # the two lines are shaped exactly as a setup() would carry them, one per line: only the cut at the
    # first @test keeps them out of the suite head
    TESTS="$d" run manager_suites
    [ "$output" = "$d/late.bats" ]
    run isolation_problems "$d/late.bats"
    [ "${#lines[@]}" -eq 2 ]
}

@test "a helper loaded as \"\$BATS_TEST_DIRNAME/name\" or sourced is read, and so are a helper's own loads" {
    local d="$SYN"
    # via-dirname: the common bats spelling for a helper beside the suite; the suite alone never names the binary
    printf '%s\n' 'start_mgr() {' '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' \
        '    node "$MGR" up &' '}' > "$d/h-dirname.bash"
    printf '%s\n' '#!/usr/bin/env bats' 'load "$BATS_TEST_DIRNAME/h-dirname"' 'setup() {' '    TEST_DIR="$(mktemp -d)"' \
        '    start_mgr' '}' '@'"test \"starts one\" { true; }" > "$d/via-dirname.bats"
    run loaded_helpers "$d/via-dirname.bats"
    [ "$output" = "$d/h-dirname.bash" ]
    # via-source: the same helper brought in with `source`, by its full name
    printf '%s\n' '#!/usr/bin/env bats' 'source "${BATS_TEST_DIRNAME}/h-dirname.bash"' 'setup() {' \
        '    TEST_DIR="$(mktemp -d)"' '    start_mgr' '}' '@'"test \"starts one\" { true; }" > "$d/via-source.bats"
    run loaded_helpers "$d/via-source.bats"
    [ "$output" = "$d/h-dirname.bash" ]
    # via-chain: the suite loads h-outer, h-outer loads h-inner (which loads h-outer back: a loop), h-inner
    # starts the manager; the unquoted ${...} spelling
    printf '%s\n' 'load h-inner' 'outer() { inner; }' > "$d/h-outer.bash"
    printf '%s\n' 'load h-outer' 'inner() {' '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' \
        '    node "$MGR" up &' '}' > "$d/h-inner.bash"
    printf '%s\n' '#!/usr/bin/env bats' 'load ${BATS_TEST_DIRNAME}/h-outer' 'setup() {' '    TEST_DIR="$(mktemp -d)"' \
        '    outer' '}' '@'"test \"starts one\" { true; }" > "$d/via-chain.bats"
    run loaded_helpers "$d/via-chain.bats"
    [ "${#lines[@]}" -eq 2 ]                                   # each file once, the loop notwithstanding
    [ "${lines[0]}" = "$d/h-outer.bash" ]
    [ "${lines[1]}" = "$d/h-inner.bash" ]
    TESTS="$d" run manager_suites
    [ "$(printf '%s\n' "$output" | LC_ALL=C sort)" = "$(printf '%s\n' "$d/via-chain.bats" "$d/via-dirname.bats" "$d/via-source.bats" | LC_ALL=C sort)" ]
    for f in via-dirname via-source via-chain; do
        run isolation_problems "$d/$f.bats"
        [ "${#lines[@]}" -eq 2 ]
    done
}

@test "a node-less exec of the resolved binary, the ../bin spelling and a path under a root variable are seen; a text-only mention or a stand-in is not" {
    local d="$SYN"
    # direct: the file is executable with a node shebang, so `"$BIN/romp-manager" up` starts it (after an
    # env prefix, here); nothing else on the line names the path
    printf '%s\n' '#!/usr/bin/env bats' 'BIN="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)"' 'setup() {' \
        '    TEST_DIR="$(mktemp -d)"' '    ROMP_SERVE_BIN="$TEST_DIR/fake" "$BIN/romp-manager" up &' '}' \
        '@'"test \"starts one\" { true; }" > "$d/direct.bats"
    # dotdot: the binary resolved by climbing from the tests directory without a cd/pwd, then run by name
    printf '%s\n' '#!/usr/bin/env bats' 'setup() {' '    TEST_DIR="$(mktemp -d)"' \
        '    MGR="$BATS_TEST_DIRNAME/../bin/romp-manager"' '}' \
        '@'"test \"starts one\" { node \"\$MGR\" up; }" > "$d/dotdot.bats"
    # depth: the binary run through a variable holding the repository root, the path climbing from there
    printf '%s\n' '#!/usr/bin/env bats' 'ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"' 'setup() {' \
        '    TEST_DIR="$(mktemp -d)"' '    node "$ROOT/bin/romp-manager" up &' '}' \
        '@'"test \"starts one\" { true; }" > "$d/depth.bats"
    # greponly: every spelling of the real path, each as the operand of a grep, a test or an assert
    printf '%s\n' '#!/usr/bin/env bats' 'setup() { TEST_DIR="$(mktemp -d)"; }' \
        '@'"test \"talks only\" {" \
        "    grep -q 'pkill -f \"\\\$ROMP_DIR/bin/romp-manager\"' \"\$TEST_DIR/uninstall\"" \
        '    [[ "$output" == *"/bin/romp-manager"* ]]' \
        '    run grep -c "node \"$BIN/romp-manager\"" "$TEST_DIR/log"' \
        '    assert_output --partial "$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' \
        '}' > "$d/greponly.bats"
    grep -qF 'pkill -f "\$ROMP_DIR/bin/romp-manager"' "$d/greponly.bats"   # the fixture carries the real spelling
    # mock: a heredoc target and a ROMP_MANAGER_BIN export under a test directory, neither a command
    printf '%s\n' '#!/usr/bin/env bats' 'setup() {' '    MOCK_DIR="$(mktemp -d)"' \
        '    cat > "$MOCK_DIR/romp-manager" <<MOCK' 'exit 0' 'MOCK' '    chmod +x "$MOCK_DIR/romp-manager"' \
        '    export ROMP_MANAGER_BIN="$MOCK_DIR/romp-manager"' '}' \
        '@'"test \"mocks one\" { true; }" > "$d/mock.bats"
    # standin: the same under a bin/ directory inside the test directory, written by printf, made executable
    # and handed to bin/romp as ROMP_MANAGER_BIN (the shape of tests/romp-refresh-audit.bats); the suite
    # then runs bin/romp itself, resolved by climbing from the tests directory
    printf '%s\n' '#!/usr/bin/env bats' 'setup() {' '    TEST_DIR="$(mktemp -d)"' '    mkdir -p "$TEST_DIR/bin"' \
        '    printf '"'"'#!/usr/bin/env bash\nexit 0\n'"'"' > "$TEST_DIR/bin/romp-manager"' \
        '    chmod +x "$TEST_DIR/bin/romp-manager" "$TEST_DIR/bin/romp-postal-service"' \
        '    export ROMP_MANAGER_BIN="$TEST_DIR/bin/romp-manager"' \
        '    ROMP="$BATS_TEST_DIRNAME/../bin/romp"' '}' \
        '@'"test \"drives romp against the stand-in\" { run \"\$ROMP\" refresh --quiet; }" > "$d/standin.bats"
    grep -qF '"$TEST_DIR/bin/romp-manager"' "$d/standin.bats"             # the fixture carries the stand-in path
    TESTS="$d" run manager_suites
    [ "$(printf '%s\n' "$output" | LC_ALL=C sort)" = "$(printf '%s\n' "$d/depth.bats" "$d/direct.bats" "$d/dotdot.bats" | LC_ALL=C sort)" ]
    for f in direct dotdot depth; do
        run isolation_problems "$d/$f.bats"
        [ "${#lines[@]}" -eq 2 ]
    done
}

@test "the detector prints only paths when SIGPIPE is ignored, as the GitHub Actions runner leaves it for every child" {
    # `suite_text "$f" | grep -Eq ...` let grep stop at the first hit while the greps behind it were
    # still writing. With SIGPIPE at its default they die silently; ignored (the runner's host process
    # starts each step so, and the disposition survives exec), each one meets EPIPE and prints
    # `grep: write error: Broken pipe`, which `run` collects into $output beside the paths. The shape
    # that met it every time: a suite that matches on its own and then loads a helper, so a writer is
    # still to come after the match.
    local d="$SYN"
    printf '%s\n' 'state_floor() { unset ROMP_STATE_DIR; export XDG_STATE_HOME="$TEST_DIR/state"; }' > "$d/floor.bash"
    printf '%s\n' '#!/usr/bin/env bats' 'load floor' 'setup() {' '    TEST_DIR="$(mktemp -d)"' '    state_floor' \
        '    MGR="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-manager"' '}' \
        '@'"test \"starts one\" { node \"\$MGR\" up; }" > "$d/isolated.bats"
    local plain ignored
    run manager_suites
    plain="$output"
    trap '' PIPE                                               # inherited by run's subshell and every grep
    TESTS="$d" run manager_suites
    [ "$output" = "$d/isolated.bats" ]
    run manager_suites
    ignored="$output"
    trap - PIPE
    [ -n "$plain" ]
    [ "$ignored" = "$plain" ]                                  # the same list either way, nothing beside it
}
