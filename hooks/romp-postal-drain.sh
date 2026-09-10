#!/usr/bin/env bash
# romp-postal-drain.sh — Stop hook: deliver Romp Postal Service mail at turn end.
#
# When a romp session finishes a turn, mail that arrived from peer sessions is
# fed back into the conversation, so the session "deals with" messages at the
# next turn boundary WITHOUT anything being typed into the prompt box — incoming
# mail can never clobber a draft. The loop guard (cap on rapid auto-deliveries)
# lives in the bus; this hook just wraps the drained text in a Stop-hook block.
#
# Must be async:false (an async hook can't return a decision). The
# ROMP_SUMMARIZING guard keeps the summarizer's nested `claude` calls out.
#
# Disable any time:  touch ~/.claude/romp-postal-off   (rm to re-enable)

set -uo pipefail

[[ -n "${ROMP_SUMMARIZING:-}" ]] && exit 0
[[ -f "$HOME/.claude/romp-postal-off" ]] && exit 0

input="$(cat)"
[[ "$input" =~ \"session_id\":\"([^\"]+)\" ]] || exit 0
sid="${BASH_REMATCH[1]}"

# Locate romp-postal-service via this hook's REAL (symlink-followed) path: the hook
# lives at dotfiles/claude/hooks/ and romp-postal-service at dotfiles/scripts/.
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

# Loop-guarded, consuming drain (also autostarts the bus if needed).
msgs="$("$postal" drain --id "$sid" 2>/dev/null || true)"
[[ -n "${msgs//[[:space:]]/}" ]] || exit 0

# JSON-escape in pure bash (no Homebrew/python dependency in the hook path). Backslash first, so
# the escapes added after it are not doubled. Every other byte 0x01-0x1f becomes \u00XX: JSON
# forbids a raw control character inside a string, and Claude Code drops a decision it cannot
# parse without a word, AFTER the drain has already consumed the mail, so a message carrying
# pasted terminal output (colour sequences, a form feed) used to vanish on the way to the
# session. CR is dropped as before; NUL cannot reach this block ($(...) strips it) and DEL
# (0x7f) is legal as it is. Builtins only (printf -v), no fork per byte.
esc="$(printf '%s' "$msgs" | { s="$(cat)"
    s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\t'/\\t}"; s="${s//$'\r'/}"
    for i in 1 2 3 4 5 6 7 8 11 12 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31; do
        printf -v o '%03o' "$i"; printf -v c "\\$o"; printf -v h '%04x' "$i"
        s="${s//$c/\\u$h}"
    done
    printf '%s' "$s"; })"
printf '{"decision":"block","reason":"%s"}\n' "$esc"
exit 0
