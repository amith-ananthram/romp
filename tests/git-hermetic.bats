#!/usr/bin/env bats

# tests/git-hermetic.bash: after git_hermetic, git reads none of the developer's global or system
# config, every commit has an identity, whatever the machine is configured with, and neither a
# commit nor a push's receive-pack runs the background maintenance that could still be writing
# under .git when a teardown removes the repo.
#
# Skips on git < 2.32 with the same message as tests/test_tempdir_hygiene.GitFloor: GIT_CONFIG_GLOBAL
# and GIT_CONFIG_SYSTEM arrived in 2.32, so on an older git the config half of the floor is inert
# (the identity half still holds) and both proofs say so the same way instead of one going red.

load git-hermetic

git_at_least_2_32() {
    local v
    v="$(git --version | awk '{print $3}')"
    local major="${v%%.*}" rest="${v#*.}"
    local minor="${rest%%.*}"
    [ "$major" -gt 2 ] 2>/dev/null || { [ "$major" -eq 2 ] && [ "$minor" -ge 32 ]; } 2>/dev/null
}

setup() {
    git_at_least_2_32 || skip "GIT_CONFIG_GLOBAL needs git >= 2.32"
    TEST_DIR="$(mktemp -d)"
    # A stand-in for the developer's global config: a hooks directory whose pre-commit refuses
    # every commit and leaves a marker, wired in through core.hooksPath.
    export HOME="$TEST_DIR/home"
    mkdir -p "$HOME" "$TEST_DIR/hooks"
    printf '#!/bin/sh\necho ran > "%s/hook-ran"\nexit 1\n' "$TEST_DIR" > "$TEST_DIR/hooks/pre-commit"
    chmod +x "$TEST_DIR/hooks/pre-commit"
    printf '[core]\n\thooksPath = %s\n[user]\n\tname = Global Person\n\temail = global@example.invalid\n' \
        "$TEST_DIR/hooks" > "$HOME/.gitconfig"
    unset GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_CONFIG_COUNT XDG_CONFIG_HOME
    unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
    REPO="$TEST_DIR/repo"
    git init -q "$REPO"
    echo a > "$REPO/a.txt"
    git -C "$REPO" add a.txt
}

teardown() { rm -rf "${TEST_DIR:-}"; }

@test "the probe is live: without git_hermetic the global hooksPath blocks the commit" {
    run git -C "$REPO" commit -qm seed
    [ "$status" -ne 0 ]
    [ -f "$TEST_DIR/hook-ran" ]
}

@test "with git_hermetic the global hook never runs and the commit lands" {
    git_hermetic
    run git -C "$REPO" commit -qm seed
    [ "$status" -eq 0 ]
    [ ! -e "$TEST_DIR/hook-ran" ]
    [ "$(git -C "$REPO" config --global --get core.hooksPath || true)" = "" ]
}

@test "with git_hermetic a system-config hooksPath never runs either" {
    # The system half of the floor: GIT_CONFIG_SYSTEM (git >= 2.32) is a root-free stand-in for
    # /etc/gitconfig, and GIT_CONFIG_NOSYSTEM=1 is what hides it; the global probe above says
    # nothing about it. The file carries an identity because git resolves the author before it
    # runs pre-commit, and the live arm has none from the environment or a global config (the
    # global file is hidden there so the system file is the ONLY source of the hook).
    printf '[core]\n\thooksPath = %s\n[user]\n\tname = System Person\n\temail = system@example.invalid\n' \
        "$TEST_DIR/hooks" > "$TEST_DIR/sysconfig"
    export GIT_CONFIG_SYSTEM="$TEST_DIR/sysconfig"
    run env GIT_CONFIG_GLOBAL=/dev/null git -C "$REPO" commit -qm seed
    [ "$status" -ne 0 ]
    [ -f "$TEST_DIR/hook-ran" ]
    rm "$TEST_DIR/hook-ran"
    git_hermetic
    run git -C "$REPO" commit -qm seed
    [ "$status" -eq 0 ]
    [ ! -e "$TEST_DIR/hook-ran" ]
}

@test "the identity is the synthetic one, not the global config's" {
    git_hermetic
    git -C "$REPO" commit -qm seed
    [ "$(git -C "$REPO" log -1 --format='%an <%ae>')" = "romp tests <tests@example.invalid>" ]
    [ "$(git -C "$REPO" log -1 --format='%cn <%ce>')" = "romp tests <tests@example.invalid>" ]
}

@test "a test's own identity exports after git_hermetic still win" {
    git_hermetic
    # The env identity outranks `git config user.*` and `-c user.*`, so a test that must pin a
    # particular author exports its own GIT_AUTHOR_* / GIT_COMMITTER_* after git_hermetic.
    GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@e.invalid git -C "$REPO" commit -qm seed
    [ "$(git -C "$REPO" log -1 --format='%an <%ae>')" = "t <t@e.invalid>" ]
}

@test "with git_hermetic the no-background keys read back from the environment and the floor's global file, not from the repo" {
    # The deterministic pin. Each key is configuration twice: from the floor's global config file
    # (the `global` scope) and from the GIT_CONFIG_COUNT pairs (the `command` scope, the label -c
    # reports too; -c itself still outranks them), and never from the repo's own config. Before the
    # floor carried them, each --get exited 1 with nothing printed.
    git_hermetic
    local kv
    for kv in maintenance.auto=false maintenance.autoDetach=false gc.auto=0 gc.autoDetach=false core.fsmonitor=false; do
        [ "$(git -C "$REPO" config --show-scope --get-all "${kv%%=*}")" = "$(printf 'global\t%s\ncommand\t%s' "${kv#*=}" "${kv#*=}")" ]
    done
    [ "$GIT_CONFIG_COUNT" = 5 ]                                                    # these five pairs and no other
    [ "$(git config --file "$GIT_CONFIG_GLOBAL" --list | wc -l | tr -d ' ')" = 5 ]   # and the file carries the same five
    run git -C "$REPO" config --local --get maintenance.auto
    [ "$status" -ne 0 ]
    [ -z "$output" ]
    # Precedence: the pairs beat a value the repo's own config sets, and a test's -c beats the pairs.
    git -C "$REPO" config maintenance.auto true
    [ "$(git -C "$REPO" config --get maintenance.auto)" = "false" ]
    [ "$(git -C "$REPO" -c maintenance.auto=true config --get maintenance.auto)" = "true" ]
}

@test "with git_hermetic a commit spawns no maintenance or gc child" {
    # A smoke check on the git running the suite, read off git's own trace the way
    # tests/test_git_fixture.py reads it (the same substrings: a bare `maintenance` also catches
    # the --detach argument recent git appends); the config pin above is the deterministic half.
    # Without the keys, `git commit` spawns `git maintenance run --auto`, the child that can still
    # be writing under .git when a teardown's rm -rf runs.
    git_hermetic
    GIT_TRACE="$TEST_DIR/trace" git -C "$REPO" commit -qm seed
    [ -s "$TEST_DIR/trace" ]
    grep -q 'built-in: git commit' "$TEST_DIR/trace"
    run grep 'maintenance' "$TEST_DIR/trace"
    [ "$status" -ne 0 ]
    run grep 'run_command: git gc' "$TEST_DIR/trace"
    [ "$status" -ne 0 ]
}

@test "with git_hermetic a push's receive-pack reads the no-background keys in the receiving repository" {
    # A push over a local path starts receive-pack with GIT_CONFIG_COUNT stripped, so the pairs
    # alone would leave the receiving side to its own auto gc or maintenance in a repo the teardown
    # removes; the floor's global config file is what reaches it. Read from inside receive-pack's
    # environment through --receive-pack: a stand-in records two keys as it sees them, untraced, and
    # then runs the real receive-pack. Before the floor wrote the file, both reads exited 1 with
    # nothing printed. The trace check is the smoke half: a git whose receive-pack spawns
    # `gc --auto` directly rather than through maintenance shows that child either way, and
    # gc.auto=0 has it exit without work.
    git_hermetic
    git -C "$REPO" commit -qm seed
    git init -q --bare "$TEST_DIR/remote.git"
    printf '#!/bin/sh\n{ GIT_TRACE=0 git -C "$1" config --get maintenance.auto; GIT_TRACE=0 git -C "$1" config --get gc.auto; } > "%s" 2>&1\nexec git receive-pack "$@"\n' \
        "$TEST_DIR/remote-saw" > "$TEST_DIR/receive-pack"
    chmod +x "$TEST_DIR/receive-pack"
    GIT_TRACE="$TEST_DIR/trace" git -C "$REPO" push -q --receive-pack="$TEST_DIR/receive-pack" "$TEST_DIR/remote.git" HEAD:refs/heads/main
    [ "$(git -C "$TEST_DIR/remote.git" rev-parse refs/heads/main)" = "$(git -C "$REPO" rev-parse HEAD)" ]
    [ "$(cat "$TEST_DIR/remote-saw")" = "$(printf 'false\n0')" ]
    grep -q 'built-in: git receive-pack' "$TEST_DIR/trace"
    run grep 'maintenance' "$TEST_DIR/trace"
    [ "$status" -ne 0 ]
}
