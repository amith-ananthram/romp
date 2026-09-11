# tests/cli-scope-floor.bash: keep a bats suite's real manager or kernel off the developer's user
# systemd manager.
#
# Use from a bats file: `load cli-scope-floor` at the top and `cli_scope_floor` in setup(), before the
# subject starts.
#
# Why (2026-09-06): under ROMP_SUPERVISED, which bin/romp-service's unit sets and a tool shell under a
# self-hosted install inherits from it, the kernel spawns each session's CLI through `systemd-run --scope`
# (cli_scope_supported), so a suite that starts a real manager or kernel would leave a transient scope on
# the developer's user manager. The floor exports ROMP_CLI_SCOPE=0 (exported, not merely set: the subject
# is a child process). A suite that means to exercise the scoped path exports ROMP_CLI_SCOPE=1 AFTER this
# call, with a fake systemd-run first on PATH. pytest's floor is tests/conftest.py
# (tests/test_cli_scope_floor.py pins it).
#
# Until 2026-09-11 this floor rode tests/tmux-private.bash, the helper that gave the terminal backend's
# suites a private tmux socket directory; that isolation left with the backend (issue #1398), and the
# floor is all that remains.

cli_scope_floor() {
    export ROMP_CLI_SCOPE=0
}
