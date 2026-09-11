#!/usr/bin/env bats

# bin/romp-tmux-env: where romp's tmux server keeps its socket (T325, 2026-09-11), the shell twin of
# kernel/tmux_socket.py. Sourced by bin/romp before its first tmux call; run directly it prints the directory.
# An operator's TMUX_TMPDIR wins as it stands; else a writable XDG_RUNTIME_DIR gives <XDG_RUNTIME_DIR>/romp, made
# 0700 when missing; else nothing (tmux's default). Nothing here runs tmux.

setup() {
    TEST_DIR="$(mktemp -d)"
    HELPER="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp-tmux-env"
    RUN="$TEST_DIR/run"; mkdir -m 0700 "$RUN"
    unset TMUX_TMPDIR XDG_RUNTIME_DIR
}

teardown() {
    chmod -R u+rwx "$TEST_DIR" 2>/dev/null || true
    rm -rf "$TEST_DIR"
}

@test "the operator's TMUX_TMPDIR wins as it stands and nothing is made" {
    TMUX_TMPDIR=/op/dir XDG_RUNTIME_DIR="$RUN" run bash "$HELPER"
    [ "$status" -eq 0 ]
    [ "$output" = "/op/dir" ]
    [ ! -e "$RUN/romp" ]
    # untrimmed: byte for byte what the operator set, as the Python and node twins answer
    TMUX_TMPDIR=" /op/dir " XDG_RUNTIME_DIR="$RUN" run bash "$HELPER"
    [ "$output" = " /op/dir " ]
}

@test "a writable runtime dir gives its romp subdirectory, made 0700" {
    XDG_RUNTIME_DIR="$RUN" run bash "$HELPER"
    [ "$status" -eq 0 ]
    [ "$output" = "$RUN/romp" ]
    [ -d "$RUN/romp" ]
    perms="$(stat -c '%a' "$RUN/romp" 2>/dev/null || stat -f '%Lp' "$RUN/romp")"   # GNU first, BSD/macOS second (tests/romp.bats's form)
    [ "$perms" = "700" ]
    XDG_RUNTIME_DIR="$RUN" run bash "$HELPER"
    [ "$output" = "$RUN/romp" ]
}

@test "no runtime dir, a missing one, or a file: an empty line, tmux's default" {
    run bash "$HELPER"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    XDG_RUNTIME_DIR="$TEST_DIR/missing" run bash "$HELPER"
    [ -z "$output" ]
    printf x > "$TEST_DIR/afile"
    XDG_RUNTIME_DIR="$TEST_DIR/afile" run bash "$HELPER"
    [ -z "$output" ]
}

@test "an unwritable runtime dir falls back to the default" {
    if [ "$(id -u)" = 0 ]; then skip "root writes anywhere"; fi
    mkdir -m 0500 "$TEST_DIR/ro"
    XDG_RUNTIME_DIR="$TEST_DIR/ro" run bash "$HELPER"
    [ "$status" -eq 0 ]
    [ -z "$output" ]
    [ ! -e "$TEST_DIR/ro/romp" ]
}

@test "sourced, romp_tmux_tmpdir_export sets TMUX_TMPDIR for the rest of the script (bin/romp's use)" {
    run bash -c "export XDG_RUNTIME_DIR='$RUN'; source '$HELPER'; romp_tmux_tmpdir_export; printf '%s' \"\${TMUX_TMPDIR:-unset}\""
    [ "$status" -eq 0 ]
    [ "$output" = "$RUN/romp" ]
    run bash -c "unset XDG_RUNTIME_DIR; source '$HELPER'; romp_tmux_tmpdir_export; printf '%s' \"\${TMUX_TMPDIR:-unset}\""
    [ "$output" = "unset" ]
}

@test "bin/romp sources the helper and exports before its first tmux call" {
    ROMP="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"
    src_line="$(grep -nE '^\s*source "\$_romp_tmux_env"' "$ROMP" | head -1 | cut -d: -f1)"
    helper_line="$(grep -n '^_romp_tmux_env=.*romp-tmux-env' "$ROMP" | head -1 | cut -d: -f1)"
    [ -n "$helper_line" ]
    export_line="$(grep -nE '^\s*romp_tmux_tmpdir_export' "$ROMP" | head -1 | cut -d: -f1)"
    first_tmux="$(grep -nE '^[^#]*\btmux (list-|new-|has-|send-|show|set |attach|switch|display)' "$ROMP" | head -1 | cut -d: -f1)"
    [ -n "$src_line" ] && [ -n "$export_line" ] && [ -n "$first_tmux" ]
    [ "$src_line" -lt "$export_line" ]
    [ "$export_line" -lt "$first_tmux" ]
}
