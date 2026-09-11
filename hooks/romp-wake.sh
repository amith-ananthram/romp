#!/usr/bin/env bash
# romp-wake.sh — event-driven judge trigger (design: event-based over time heuristics).
#
# Fires on the Claude Code events that create NEW work for the kernel — a turn
# ended (Stop), a prompt landed (UserPromptSubmit), or a compaction ended
# (PostCompact) — and pokes the kernel's POST /tick so the judge producer runs a
# pass NOW instead of waiting out its 3 s backstop, and the pusher's parked-op
# drain runs now instead of on its 0.5 s one. A /compact ends at
# PostCompact: the poke wakes the drain, and the op queued behind the compact
# fires once the compaction is corroborated in the transcript (the boundary
# record). Without this the feed lags a completed turn by a backstop.
#
# Fire-and-forget: it MUST never block or fail a turn. The curl is detached into
# a subshell with a short timeout and every output/error is swallowed. If no
# kernel is listening (none running, or a headless/non-romp session) the poke
# fails silently — the backstops still cover it.
set -uo pipefail

# Drain stdin (Claude Code sends the hook event as JSON) so we never SIGPIPE the
# caller. We don't need the payload: any registered event means "new work may
# exist", and a wake is cheap + idempotent on the kernel side.
cat >/dev/null 2>&1 || true

# Either spelling of the kernel's listen port (bin/romp-serve owns that seam and exports both);
# ROMP_SERVE_PORT first, so a session under an aux kernel keeps poking ITS kernel.
port="${ROMP_SERVE_PORT:-${ROMP_KERNEL_PORT:-29855}}"
# The kernel gates every request on the serve token, loopback included (Jupyter's model) — read it
# the way the kernel resolves it: env override, else the 0600 state file. Missing token → the poke
# 403s silently, same posture as no kernel at all (the backstops cover it).
tok="${ROMP_SERVE_TOKEN:-}"
[[ -n "$tok" ]] || tok="$(cat "${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/serve-token" 2>/dev/null || true)"
# The token goes in on STDIN as a curl config, never in argv: /proc/<pid>/cmdline is world-readable,
# so `-H "X-Romp-Token: $tok"` publishes full control of every session to any other account on the
# machine for as long as the curl lives. This one is short-lived, which makes reading it a race
# rather than a certainty — not a boundary. Quotes/backslashes escaped because curl's config syntax
# is quoted (only reachable via ROMP_SERVE_TOKEN; the minted one is base64url).
esc="${tok//\\/\\\\}"; esc="${esc//\"/\\\"}"
( printf 'header = "X-Romp-Token: %s"\n' "$esc" \
    | curl -sf -m 0.5 --config - -X POST "http://127.0.0.1:${port}/tick" -o /dev/null >/dev/null 2>&1 & ) >/dev/null 2>&1
exit 0
