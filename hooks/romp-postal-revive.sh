#!/usr/bin/env bash
# romp-postal-revive.sh — SessionStart hook (sync): when a romp session is revived
# (resumed) with unread mail waiting — typically a "parked handoff" left by a peer
# while this session was dead — make it ACT on that mail, not just read it.
#
# A SessionStart hook can only inject PASSIVE context (verified: it cannot force a
# turn, and `claude --resume` otherwise sits idle waiting for input). Passive
# context means a session revived on un-acted handoffs would just sit idle. So
# instead of printing the mail here, we ask the bus to FORCE-DELIVER it: the bus
# polls until this session's prompt box is actually live, then injects AND submits
# it (`romp-postal-service wake` → _wake_when_ready → _push), so the session takes a real
# turn (waiting→working→acts→waiting) and shows WORKING in every view.
#
# The pending-mail marker (mail-pending/<sid>, kept in sync by the bus) is the
# cheap on-disk check for "does this session have unread mail" — true even for a
# session that was dead. The Stop-hook drain stays as the backstop if the prompt
# never comes up in time. Disable everything with ~/.claude/romp-postal-off.

set -uo pipefail

[[ -n "${ROMP_SUMMARIZING:-}" ]] && exit 0
[[ -f "$HOME/.claude/romp-postal-off" ]] && exit 0
# romp sessions only: a romp session is exactly one launched with ROMP_SID in its
# environment (the kernel sets it on the CLI it spawns); a plain Claude Code session
# has no peers and no mail.
[[ -n "${ROMP_SID:-}" ]] || exit 0

input="$(cat)"
[[ "$input" =~ \"session_id\":\"([^\"]+)\" ]] || exit 0
sid="${BASH_REMATCH[1]}"

# Only on a revival / fresh start (skip clear/compact so a live session isn't poked).
[[ "$input" =~ \"source\":\"([^\"]+)\" ]] && source_kind="${BASH_REMATCH[1]}" || source_kind=""
case "$source_kind" in resume|startup) ;; *) exit 0 ;; esac

# Fast path: nothing pending for this session -> nothing to do. The marker is
# on-disk so this needs no kernel query and no bus round-trip.
pending="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}/postal/mail-pending/$sid"
[[ -f "$pending" ]] || exit 0

# Locate romp-postal-service via this hook's REAL (symlink-followed) path: the hook lives
# at dotfiles/claude/hooks/ and romp-postal-service at dotfiles/scripts/.
src="${BASH_SOURCE[0]}"
while [[ -L "$src" ]]; do
    tgt="$(readlink "$src")"
    case "$tgt" in
        /*) src="$tgt" ;;
        *)  src="$(cd "$(dirname "$src")" && pwd)/$tgt" ;;
    esac
done
postal="$(cd "$(dirname "$src")/../bin" 2>/dev/null && pwd)/romp-postal-service"
[[ -x "$postal" ]] || exit 0

# Hand off to the bus: force-deliver once the prompt is live. Non-blocking — the
# bus does the wait, so SessionStart returns instantly. Mail is NOT consumed here,
# so if the bus can't deliver (prompt never comes up), the Stop-hook drain still will.
"$postal" wake --id "$sid" >/dev/null 2>&1 || true
exit 0
