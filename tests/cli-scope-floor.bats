#!/usr/bin/env bats

# tests/cli-scope-floor.bash floors ROMP_CLI_SCOPE=0 for a bats suite that starts a real manager or
# kernel. The case pins the one contract: the floor wins over an inherited supervised environment and is
# exported. Nothing below starts a process.

load cli-scope-floor

@test "cli_scope_floor floors ROMP_CLI_SCOPE=0, over an inherited supervised environment" {
    # A tool shell under a self-hosted install carries ROMP_SUPERVISED, under which a real kernel would put
    # its sessions' CLIs in transient scopes on the developer's user manager. The floor rides one call.
    export ROMP_SUPERVISED=1 ROMP_CLI_SCOPE=1
    cli_scope_floor
    [ "$ROMP_CLI_SCOPE" = 0 ]
    # exported, not merely set: the subject is a child process
    [ "$(env | grep '^ROMP_CLI_SCOPE=')" = "ROMP_CLI_SCOPE=0" ]
}
