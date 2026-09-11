#!/usr/bin/env bats

# tests/cli-scope-floor.bash floors ROMP_CLI_SCOPE=0 for a bats suite that starts a real manager or
# kernel. Two cases pin the contract: the floor wins over an inherited supervised environment, and it is
# exported even when nothing exported the name before (the subject is a child process). Nothing below
# starts a process.

load cli-scope-floor

@test "cli_scope_floor floors ROMP_CLI_SCOPE=0 over an inherited supervised environment" {
    # A tool shell under a self-hosted install carries ROMP_SUPERVISED, under which a real kernel would put
    # its sessions' CLIs in transient scopes on the developer's user manager. The floor rides one call.
    export ROMP_SUPERVISED=1 ROMP_CLI_SCOPE=1
    cli_scope_floor
    [ "$ROMP_CLI_SCOPE" = 0 ]
}

@test "cli_scope_floor exports the floor when the name was not in the environment" {
    # An inherited export keeps its attribute across a plain assignment, so the case above cannot tell
    # `export X=0` from `X=0`; this one starts with the name unset, where only an export reaches a child.
    unset ROMP_CLI_SCOPE
    cli_scope_floor
    [ "$(env | grep '^ROMP_CLI_SCOPE=')" = "ROMP_CLI_SCOPE=0" ]
}
