# tests/git-hermetic.bash: git in a bats test reads none of the developer's configuration and
# spawns no background work.
#
# Use from a bats file: `load git-hermetic` at the top and `git_hermetic` as the first line of
# setup(), before any git command runs. It writes a config file under BATS_TEST_TMPDIR, so it runs
# inside a test (setup, the test body or teardown), not at file load and not in setup_file.
#
# Why (2026-09-06): the fixture repos here are built with `git init` + `git commit` in temp dirs,
# and those commands honoured the developer's global git config. On one box a global
# core.hooksPath ran a pre-commit hook (a gitleaks scan) on every seed commit and a pre-push hook
# on every fixture push, an LFS filter would run on every checkout, and a credential helper or
# url.insteadOf rewrite could reach a real remote. CI has no global git config, so a test that
# leans on one is already broken there; this makes every run match. GIT_CONFIG_GLOBAL is honoured
# by git >= 2.32. The identity is synthetic and exported (not defaulted) so a developer's own
# GIT_AUTHOR_* cannot leak into fixture commits either. The env identity outranks `git config
# user.*` and `-c user.*`, so a test that must pin a particular author exports its own
# GIT_AUTHOR_* / GIT_COMMITTER_* after this call; other config keys still yield to `-c`.
#
# No background work (2026-09-10): `git commit`, fetch and merge spawn `git maintenance run --auto`,
# and receive-pack runs the receiving repository's auto gc or maintenance after every push. On git
# 2.47 and later the maintenance child takes .git/maintenance.lock and then detaches from its
# parent, so it can still be writing under .git when teardown's `rm -rf` runs; rm exits 1 on the
# directory it could not empty and bats fails the test whose teardown failed. The install-sh,
# release-sh, gitleaks-config, docs-serve, pre-push-hook and bootstrap-sh suites commit or push
# into repos their teardown removes (CI's runners had git 2.55.0 on 2026-09-10; a git that does
# not detach shows the same spawn in GIT_TRACE without the race). The five keys are the ones
# tests/git_fixture.py passes as -c flags on the python side (GIT_NO_BACKGROUND). Here they travel
# two ways, because neither way alone reaches every git a test starts:
# - as GIT_CONFIG_COUNT runtime pairs in the environment (git >= 2.31), which every git the test,
#   a script under test (install.sh, release.sh, bootstrap.sh, docs-serve.sh, the pre-push hook)
#   or a hook runs inherits; they override every config file and yield to any -c a test passes.
#   A test that needs pairs of its own re-exports the whole set: git refuses to run when
#   GIT_CONFIG_COUNT names a pair that has no GIT_CONFIG_KEY_n or GIT_CONFIG_VALUE_n.
# - in the floor's own global config file, which GIT_CONFIG_GLOBAL names in place of /dev/null. A
#   push over a local path starts receive-pack with GIT_CONFIG_COUNT stripped (the same rule keeps
#   a -c from crossing into the other repository), so the pairs never reach the receiving side;
#   GIT_CONFIG_GLOBAL survives, and the file is what receive-pack in a bare fixture remote reads.
#   A suite that points GIT_CONFIG_GLOBAL at a file of its own gives that half up.

git_hermetic() {
    export GIT_CONFIG_NOSYSTEM=1
    export GIT_AUTHOR_NAME="romp tests" GIT_AUTHOR_EMAIL="tests@example.invalid"
    export GIT_COMMITTER_NAME="romp tests" GIT_COMMITTER_EMAIL="tests@example.invalid"
    # The same five keys, in the same order, as GIT_NO_BACKGROUND in tests/git_fixture.py.
    export GIT_CONFIG_COUNT=5
    export GIT_CONFIG_KEY_0=maintenance.auto       GIT_CONFIG_VALUE_0=false
    export GIT_CONFIG_KEY_1=maintenance.autoDetach GIT_CONFIG_VALUE_1=false
    export GIT_CONFIG_KEY_2=gc.auto                GIT_CONFIG_VALUE_2=0
    export GIT_CONFIG_KEY_3=gc.autoDetach          GIT_CONFIG_VALUE_3=false
    export GIT_CONFIG_KEY_4=core.fsmonitor         GIT_CONFIG_VALUE_4=false
    # The same five keys as the only global config, for the git that starts without the count.
    export GIT_CONFIG_GLOBAL="${BATS_TEST_TMPDIR:?git_hermetic runs inside a bats test}/gitconfig"
    printf '[maintenance]\n\tauto = false\n\tautoDetach = false\n[gc]\n\tauto = 0\n\tautoDetach = false\n[core]\n\tfsmonitor = false\n' \
        > "$GIT_CONFIG_GLOBAL"
}
