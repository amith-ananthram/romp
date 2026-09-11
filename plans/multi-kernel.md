# Multi-kernel: several kernels per machine, per-kernel accounts, inter-kernel mail

Status: PROPOSED — not shipped. A design sketch; no part of this has landed.

Superseded in part (2026-09-11): the tmux legs below (the `tmuxSocket?` profile field,
`ROMP_TMUX_SOCKET` in phases 2 and 3, the per-profile tmux server) are void since the terminal (tmux)
backend was removed from romp on that date; a multi-kernel design now scopes Claude Code and Codex
sessions only.

The user's ask (2026-07-24, via romp_docs + directly): run SEVERAL kernels on one machine, each on
its own port with its own state, so different remote people can each be handed their own kernel —
and additionally run kernels on DIFFERENT Claude accounts in parallel, surface which account each
kernel is on, and let co-located kernels message each other through the postal federation.

## Verified findings (2026-07-24, read of the tree at 4e34c22)

1. **Ports are already parameterized; state is the collision.** `ROMP_KERNEL_PORT` (default 29855)
   and `ROMP_POSTAL_PORT` (default 25302) exist, and the VS Code extension already documents
   `romp.kernelPort` as "point different windows at different kernels" (`_REMOTE_KERNEL_PORT`
   kernel-side). But EVERY surface derives its state root as
   `$XDG_STATE_HOME/romp` (default `~/.local/state/romp`): kernel, judge, postal maildir + names,
   serve-token, goals, judge caches, every `bin/` tool, the hooks, the timeline view, the
   extension. Two kernels sharing that root share a token, a mailbox root, goal stores, and
   auto-nudge records — no isolation at all. One root-override isolates everything at once.

2. **The manager is multi-kernel by construction.** `bin/romp-manager` keeps kernels in a registry
   keyed by id, respawns per entry, and `restart-all` loops the registry; v1 registers only
   `main`. Its own design rationale (one durable owner, no orphans, front ends attach) argues
   against a separate "auxiliary kernel" startup path: the feature is MORE registry entries, not a
   second supervisor.

3. **Session visibility is NOT scoped by the state root.** Discovery reads a hardcoded
   `~/.claude/projects` (`kernel/judge.py` PROJECTS — the task store already respects
   `CLAUDE_CONFIG_DIR`, the projects root does not), and tmux runs on the default server socket
   (single seam: the one `["tmux"]+args` runner in kernel/kernel.py). Unscoped, two kernels would
   both judge every session (double LLM spend) and both inject nudges into the same panes.

4. **Accounts: config-dir isolation is real on Linux, keychain-shared on macOS.** Claude Code
   stores OAuth credentials file-based under the config dir on Linux (full per-config-dir
   isolation → true multi-account). On this Mac, verified: no `~/.claude/.credentials.json`; the
   credential is a Keychain item (service "Claude Code-credentials") whose account attribute is
   the LOGIN USER, not a config-dir path — so two config dirs on one macOS user share one login
   by default. That matches the user's observation that logging in "logs me in for everything."
   Account metadata (`oauthAccount`) lives in the config dir's `.claude.json` — readable
   per-kernel for surfacing WHICH account a kernel is on.

5. **Federation exists but is keyed per host.** remotes/trust tiers/held-mail quarantine already
   do cross-host kernel-to-kernel mail. Two kernels on ONE machine are just peers at
   `127.0.0.1:<other bus port>` — but peer identity/trust is keyed by HOST today, so two same-host
   kernels collide in PEER_STATE/trust config. Identity must widen to (host, kernel) or
   host:port.

## Decisions (the user, 2026-07-24)

- **Full isolation per kernel** — own state root, own config dir (own login where the OS allows),
  own ports. Explicitly chosen over shared-account/split-projects.
- Surface the ACCOUNT each kernel runs as (from its config dir's `oauthAccount`).
- Co-located kernels should be federated peers (opt-in, normal trust tiers).
- macOS keychain sharing is a platform limitation to surface honestly, not to hack around:
  multi-account on macOS needs either separate OS users or Claude Code's own storage becoming
  config-dir-scoped. The server/Linux case (the actual remote-people deployment) works today.

## The primitive: a kernel PROFILE

`{id, kernelPort, postalPort, stateDir, claudeConfigDir, tmuxSocket?}` — recorded in the PRIMARY
state root at `~/.local/state/romp/kernels.json`, owned/edited by the CLI, read by the manager.

## Phases

**Phase 1 — `ROMP_STATE_DIR`.** A romp-specific root override, preferred over the XDG derivation
everywhere (`Path(os.environ.get("ROMP_STATE_DIR") or Path(XDG...)/"romp")`; shell:
`${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}`). Deliberately NOT an
`XDG_STATE_HOME` override: XDG leaks into spawned children (SDK `claude` processes) and would
move THEIR unrelated state. Files: kernel/judge.py, kernel/kernel.py (3 embedded shell snippets),
kernel/event_model.py, postal/postal_service.py, bin/romp, bin/romp-node-launch, bin/romp-sdk-setup,
bin/romp-service, cli/idle_dots.py, cli/update.py,
hooks/*.sh, ui/romp-timeline-view.js (if it reads state paths), vscode-extension where relevant.
NOTE (romp_docs 2026-07-24): `bin/*` are extension-less symlinks into the packages — enumerate
with `git ls-files`, never extension globs; edits land on the real files.

**Phase 2 — visibility scoping.** PROJECTS root honors `CLAUDE_CONFIG_DIR`; the tmux runner takes
`ROMP_TMUX_SOCKET` (adds `-L <socket>`); SDK children inherit the kernel's `CLAUDE_CONFIG_DIR` so
their transcripts land in the profile's own projects root by construction.

**Phase 3 — manager registry + CLI.** Manager reads kernels.json, registers main + aux specs,
spawns each via romp-serve with the profile env (ROMP_SERVE_PORT/ROMP_KERNEL_PORT,
ROMP_POSTAL_PORT, ROMP_STATE_DIR, CLAUDE_CONFIG_DIR, ROMP_TMUX_SOCKET). `romp kernels
add|remove|list`. `romp refresh` already restarts all registry entries. Each aux kernel gets its
own serve-token (falls out of the state root) and its own postal bus.

Phase-3 rider (the user via romp_docs, 2026-07-24, reproduced end to end): `romp --refresh`
restarts kernels but NEVER the manager, and bin/romp-manager reads KERNEL_PORT once at manager
start — so after the port renumber the long-lived launchd manager kept respawning kernels on the
OLD port while every fresh process printed the new one (right URL, nothing behind it; fixed only
by romp-service reinstall). Two obligations for the registry design: (1) the manager re-reads
kernels.json + its env-derived defaults AT EVERY SPAWN, never caching spec values at manager
start, so a config change is picked up by the next restart of that kernel; (2) --refresh either
restarts the manager too or DETECTS that bin/romp-manager (or its baked defaults) changed since
the manager started and says so loudly — silently respawning with stale config is exactly what
the authoritative-sources rule forbids. Test shape: manager holds port P, disk changes to Q,
--refresh runs → the kernel must not come up on P silently.

Env-propagation note (phase 1 finding): children inherit the KERNEL's process env, so SDK
sessions of an aux kernel get ROMP_STATE_DIR/CLAUDE_CONFIG_DIR for free; tmux sessions inherit
from the tmux SERVER, so the per-profile socket (phase 2) is also what carries the profile env —
each profile's tmux server is started by its own kernel.

**Phase 4 — account surfacing.** Kernel reads its config dir's `.claude.json` `oauthAccount` and
exposes account identity in its state payload; dashboard shows it (exact surface TBD with the ui
session — glanceable, one-line, progressive disclosure).

**Phase 5 — co-located federation.** Widen peer identity from host to (host, kernel-id/port) so
`127.0.0.1:<peer bus>` entries in remotes work; same trust tiers, directed by default; no
auto-peering — connecting two local kernels is an explicit gesture, exactly like a remote host.

Tests ride each phase (bats for bin/hooks surfaces, pytest for kernel/postal, node for
manager/extension). Default-vs-override port/state tests must `unset` the live env in setup —
running the suite inside a romp session inherits the live kernel's env (romp_docs 2026-07-24,
the romp-wake-hook.bats lesson). Docs: hand to romp_docs at the end (they asked to be pinged).
