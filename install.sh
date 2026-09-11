#!/usr/bin/env bash
# Install romp onto this machine:
#   - symlink the Claude Code hooks into ~/.claude/hooks/
#   - symlink the MCP config (Romp Postal Service) into ~/.claude/
#   - symlink the romp-postal skill into ~/.claude/skills/
#   - build + install the romp-chat-view VS Code extension
#
# bin/ is NOT symlinked anywhere — add it to PATH in your shell rc:
#   export PATH="$PATH:<this repo>/bin"
set -euo pipefail
ROMP_DIR="$(cd "$(dirname "$0")" && pwd)"

# This script installs the clone it lives in, so it must be RUN from one. Piped
# (`curl ... | bash`), $0 is "bash" and ROMP_DIR silently becomes the caller's
# cwd. And since `ln -s` happily links a nonexistent target, that would point
# every hook in ~/.claude at a dangling path and break Claude Code. Check for a
# file only the repo has, and send them to bootstrap.sh instead.
if [[ ! -x "$ROMP_DIR/bin/romp" ]]; then
    echo "install.sh: this doesn't look like a romp clone ($ROMP_DIR)." >&2
    echo "  install.sh installs the clone it lives in; it cannot be piped from curl." >&2
    echo "  To install from scratch:" >&2
    echo "    curl -fsSL https://raw.githubusercontent.com/romp-on/romp/main/bootstrap.sh | bash" >&2
    exit 1
fi

# Preflight — name anything missing up front, with the exact remedy, instead
# of failing later at runtime. We deliberately do NOT auto-install system
# packages (surprising, and the right package manager varies); we check and
# tell. ROMP_SKIP_PREFLIGHT=1 bypasses; ROMP_NODE overrides the node binary.
if [[ -z "${ROMP_SKIP_PREFLIGHT:-}" ]]; then
    preflight_missing=0
    if ! command -v "${ROMP_NODE:-node}" >/dev/null 2>&1; then
        echo "install.sh: Node.js not found — the kernel manager runs on it." >&2
        echo "  macOS:  brew install node    Linux: your distro's nodejs package" >&2
        preflight_missing=1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "install.sh: python3 not found — the kernel is a Python process." >&2
        echo "  macOS:  brew install python@3.13    or:  uv python install 3.13" >&2
        preflight_missing=1
    fi
    [[ "$preflight_missing" -eq 0 ]] || exit 1
fi

# Optional pieces. NOT preflight failures — romp is usable without them, so we record what's
# missing and say so once, at the end, next to the dashboard link (a mid-install warning scrolls
# away under the hook/symlink chatter). Set by the SDK/extension steps below when they fail:
# ROMP_SDK_MISSING, ROMP_EXT_FAILED.
# Claude Code itself, the same optional-notice pattern: romp drives Claude Code, so sessions need it
# on PATH. (A version floor sat here until 2026-09-11; it gated a mail-delivery path of the tmux
# backend, which left romp that day. A session's mail rides the SDK stream on any Claude Code.)
ROMP_CLAUDE_MISSING=""
command -v claude >/dev/null 2>&1 || ROMP_CLAUDE_MISSING=1

mkdir -p "$HOME/.claude/hooks" "$HOME/.claude/skills"

# The hooks this repo ships. None records a session's state: the kernel's SDK backend writes the
# states/<sid>.jsonl rows for every Claude Code session itself, from the stream it drives.
for h in romp-postal-drain.sh romp-postal-ensure.sh \
         romp-postal-revive.sh romp-postal-context.sh romp-wake.sh; do
    ln -sf "$ROMP_DIR/hooks/$h" "$HOME/.claude/hooks/$h"
done
echo "  Symlinked romp hooks into ~/.claude/hooks/"

# Retired hooks. hooks/romp-summarize.sh (the live tmux phrase, off by default since 2026-07-24)
# left the repo 2026-09-11 with the tmux backend's dead leaves, and hooks/tmux-status.sh, the hook
# that painted the tmux status bar, left the same day with the backend itself. Nothing replaces it:
# the states rows of a Claude Code session are the SDK backend's own writes. An install from before
# still has each symlink, now dangling, and Claude Code would shell the missing path on every prompt
# and every turn end — the status hook on seven events per session; upgrading removes the links.
# Only ever a SYMLINK, and only one install.sh could have written: a link into THIS checkout's
# hooks/ (the manager-skill retirement below matches the same way), or a link of that shape into a
# checkout that has since moved or gone, which is dangling. A link to someone's own LIVE script of
# that name (their dotfiles) is theirs and stays.
for h in romp-summarize.sh tmux-status.sh; do
    if [ -L "$HOME/.claude/hooks/$h" ]; then
        _rh_target="$(readlink "$HOME/.claude/hooks/$h")"
        _rh_gone=""
        case "$_rh_target" in
            "$ROMP_DIR"/hooks/"$h") _rh_gone=1 ;;
            */hooks/"$h") [ -e "$HOME/.claude/hooks/$h" ] || _rh_gone=1 ;;
        esac
        if [ -n "$_rh_gone" ]; then
            rm -f "$HOME/.claude/hooks/$h"
            echo "  Removed the retired $h hook link"
        fi
    fi
done

# Install the git pre-push identifier hook. Symlinked into the SHARED git hooks
# dir (git rev-parse --git-common-dir), so it fires from every worktree; the hook
# self-locates the tree being pushed. ROMP_GITHOOK_DIR overrides the target (the
# test harness points it at a temp dir so it never touches a real .git). Skipped
# with ROMP_NO_GITHOOK, or when this isn't a git checkout (e.g. a tarball).
if [[ -z "${ROMP_NO_GITHOOK:-}" && -f "$ROMP_DIR/.githooks/pre-push" ]]; then
    githook_dir="${ROMP_GITHOOK_DIR:-}"
    if [[ -z "$githook_dir" ]]; then
        common="$(git -C "$ROMP_DIR" rev-parse --git-common-dir 2>/dev/null || true)"
        if [[ -n "$common" ]]; then
            case "$common" in /*) ;; *) common="$ROMP_DIR/$common" ;; esac
            githook_dir="$common/hooks"
        fi
    fi
    if [[ -n "$githook_dir" ]]; then
        mkdir -p "$githook_dir"
        ln -sf "$ROMP_DIR/.githooks/pre-push" "$githook_dir/pre-push"
        echo "  Installed the git pre-push identifier hook"
    fi
fi

# Register the hooks in ~/.claude/settings.json so Claude Code actually fires
# them. Idempotent merge: adds only missing romp entries, never touches any
# other hooks you have registered. Retired romp hooks (RETIRED below) are
# de-registered on the way, so an upgrade never leaves Claude Code calling a
# path this repo no longer ships.
python3 - <<'PYEOF'
import json, os

SETTINGS = os.path.expanduser("~/.claude/settings.json")
# No status hook: the SDK backend records a Claude Code session's state itself. (The retired
# tmux-status.sh sat on seven events; PostToolUse, Notification and PreCompact carried nothing else,
# so they are not listed at all — an event listed with no entries would leave an empty group behind.)
WANT = {  # event -> [(hook script, timeout secs, async)]
    "SessionStart":     [("romp-postal-ensure.sh", 5, True),
                         ("romp-postal-revive.sh", 8, False),
                         ("romp-postal-context.sh", 5, False)],  # romp sessions: load the romp-postal skill
    "UserPromptSubmit": [("romp-wake.sh", 5, True)],     # poke the kernel → judges run NOW, not on the 20s tick
    "Stop":             [("romp-postal-drain.sh", 10, False),
                         ("romp-wake.sh", 5, True)],     # turn ended → wake the producer immediately
    "PostCompact":      [("romp-wake.sh", 5, True)],     # compaction ended → wake the drain; the op behind a /compact fires once
                                                          # the compaction is corroborated in the transcript
}

try:
    with open(SETTINGS) as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}
hooks = settings.setdefault("hooks", {})

# Hooks this repo no longer ships (retired 2026-09-11: the announcer with the tmux backend's dead
# leaves, the status hook with the backend itself; no hook took its place).
# An install from before registered them; drop those entries wherever they sit, matched on the
# exact command string install.sh once wrote. Only a group OUR removal emptied is dropped, and only
# an event our removal left with no groups, so no `"Stop": [{"hooks": []}]` litter is left behind
# while a user's own empty or matcher-only group, on any event, stays exactly as found.
RETIRED = {"romp-summarize.sh", "tmux-status.sh"}
removed = []
for event in list(hooks):
    kept, touched = [], False
    for g in (hooks.get(event) or []):
        keep, hit = [], False
        for h in g.get("hooks", []):
            cmd = h.get("command", "")
            if cmd.startswith("~/.claude/hooks/") and cmd.rsplit("/", 1)[-1] in RETIRED:
                removed.append(event + ":" + cmd.rsplit("/", 1)[-1])
                hit = True
            else:
                keep.append(h)
        if hit:
            touched = True
            if not keep:
                continue                                # a group we emptied goes
            g["hooks"] = keep
        kept.append(g)
    if touched:
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)                      # an event we emptied goes

added = []
for event, entries in WANT.items():
    groups = hooks.setdefault(event, [])
    registered = {h.get("command") for g in groups for h in g.get("hooks", [])}
    target = next((g for g in groups if not g.get("matcher")), None)
    if target is None:
        target = {"hooks": []}
        groups.append(target)
    for name, timeout, is_async in entries:
        cmd = "~/.claude/hooks/" + name
        if cmd in registered:
            continue
        target.setdefault("hooks", []).append(
            {"type": "command", "command": cmd, "timeout": timeout, "async": is_async})
        added.append(event + ":" + name)

if added or removed:
    with open(SETTINGS, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
if removed:
    print("  De-registered retired hooks in ~/.claude/settings.json: " + ", ".join(removed))
if added:
    print("  Registered in ~/.claude/settings.json: " + ", ".join(added))
else:
    print("  Hooks already registered in ~/.claude/settings.json")
PYEOF

ln -sf "$ROMP_DIR/claude/romp-postal.mcp.json" "$HOME/.claude/romp-postal.mcp.json"
echo "  Symlinked romp-postal.mcp.json (Romp Postal Service MCP config)"

ln -sf "$ROMP_DIR/claude/romp-session-prompt.md" "$HOME/.claude/romp-session-prompt.md"
echo "  Symlinked romp-session-prompt.md (working-style append-system-prompt)"

# The `romp` skill (convert a plain terminal into a tmux romp session) was retired 2026-07-27.
# An install from before then still has the symlink, now pointing at a deleted directory, so
# upgrading has to REMOVE it — leaving a dangling link would put a broken skill in front of
# every session. Only ever unlinks a symlink, never a real directory someone put there.
if [ -L "$HOME/.claude/skills/romp" ]; then
    rm -f "$HOME/.claude/skills/romp"
    echo "  Removed the retired romp skill"
fi

# -n: the skill link points at a DIRECTORY — on a re-run, plain -sf would follow the existing
# dir-symlink and drop a NEW link INSIDE the repo (claude/skills/romp-postal/romp-postal → an
# absolute personal path, which the no-personal-identifiers test rightly rejects). -n replaces
# the link itself.
ln -sfn "$ROMP_DIR/claude/skills/romp-postal" "$HOME/.claude/skills/romp-postal"
echo "  Symlinked romp-postal skill"

# The bundled `manager` skill was moved OUT to the user's dotfiles 2026-08-23 (the router-skill
# carve-out: a way of USING romp lives outside the tool's repo — romp ships the primitives, the
# /tag and /color routes and CLI verbs, and workflows live on top). An install from the bundled
# era still has the symlink; remove it ONLY when it still points into this repo — the dotfiles
# successor claims the same name, and a plain unlink would delete that successor link.
if [ -L "$HOME/.claude/skills/manager" ]; then
    _mgr_target="$(readlink "$HOME/.claude/skills/manager")"
    case "$_mgr_target" in
        "$ROMP_DIR"/*)
            rm -f "$HOME/.claude/skills/manager"
            echo "  Removed the retired bundled manager skill (it lives in dotfiles now)"
            ;;
    esac
fi

# The Agent SDK venv — the backend plain `romp new` uses. Best-effort here, and the banner at the end
# is what a failure is for: a host missing python >= 3.10 or Debian's python3-venv can start no
# session until it is fixed (romp-sdk-setup says exactly what to install). Opt out with ROMP_NO_SDK=1. The failure is REMEMBERED, not just echoed past: this is the backend
# `romp new` defaults to, so losing it silently leaves the user with no way to start a session at all
# (that is precisely what happened on a fresh Ubuntu box, the user 2026-07-27 — an apt python3 with no
# ensurepip, one swallowed `|| echo`, and romp looked installed but could start nothing).
ROMP_SDK_MISSING=""
if [[ -z "${ROMP_NO_SDK:-}" && -x "$ROMP_DIR/bin/romp-sdk-setup" ]]; then
    "$ROMP_DIR/bin/romp-sdk-setup" || ROMP_SDK_MISSING=1
fi

# The webview bundles live here too — this step builds vscode-extension/dist, which the KERNEL serves
# to the browser dashboard, editor or no editor. A failure here means a blank dashboard, so it is
# remembered and reported at the end rather than echoed past.
ROMP_EXT_FAILED=""
if [[ -z "${ROMP_NO_EXT:-}" && -x "$ROMP_DIR/vscode-extension/install.sh" ]]; then
    echo "  Building the dashboard UI (and installing romp-chat-view where an editor is present)..."
    "$ROMP_DIR/vscode-extension/install.sh" || ROMP_EXT_FAILED=1
fi

# Auto-start: install the login service so the kernel supervisor (romp-manager) is
# always up — you never run `romp up`; open the browser and you can even start
# sessions FROM it. launchd on macOS, systemd --user on Linux. Opt out with
# ROMP_NO_SERVICE=1; remove later with `romp-service uninstall`.
if [[ -z "${ROMP_NO_SERVICE:-}" ]]; then
    # ROMP_SERVICE_BIN overrides the binary (tests stub it); defaults to the repo copy.
    _svc="${ROMP_SERVICE_BIN:-$ROMP_DIR/bin/romp-service}"
    if [[ -x "$_svc" ]]; then
        # Don't tear down a HEALTHY manager just to ship a webview dist/VSIX change. `romp-service
        # install` boots the running romp-manager OUT (SIGTERM, drains every kernel) then re-bootstraps;
        # a bootstrap that loses the drain-race exits 1 and leaves the dashboard dead on :29855. A routine
        # webview deploy needs NO manager restart (the kernel serves the rebuilt dist live), so skip the
        # whole bootout when the manager already reports `running`. Only (re)install when it is NOT
        # running — and then FAIL LOUDLY on a non-zero exit rather than `|| echo`-swallowing it, so a
        # webview deploy can never silently leave the manager unloaded (the user's rescue_me, 2026-07-21).
        if "$_svc" status 2>/dev/null | grep -qx running; then
            echo "  romp-manager already running — leaving it up (a webview deploy needs no restart)"
        else
            echo "  Installing the romp login service (romp-manager)..."
            if ! "$_svc" install; then
                echo "install.sh: romp-service install FAILED — romp-manager is NOT running; the dashboard will be dead on :29855." >&2
                echo "  Retry by hand:  $_svc install" >&2
                exit 1
            fi
        fi
    fi
fi

case ":$PATH:" in
    *":$ROMP_DIR/bin:"*) ;;
    *) echo "  NOTE: add to your shell rc:  export PATH=\"\$PATH:$ROMP_DIR/bin\"" ;;
esac

# ROMPHOME — where a bare `romp` launches when you'd otherwise be in $HOME.
# $HOME is a bad cwd: its direct children include the macOS TCC-protected
# Downloads/Desktop/Documents, so Claude indexing them triggers a stream of
# spurious file-access prompts. romp defaults ROMPHOME to this install dir
# ($ROMP_DIR) automatically; export it in your shell rc to point elsewhere.
echo "  romp launches in ROMPHOME (default: $ROMP_DIR), never \$HOME."
echo "  Override:  export ROMPHOME=\"/path/you/prefer\""

# The finish line: the dashboard link, tokened so the first click signs this
# browser in (the kernel token-gates every request, loopback included; the first
# open sets a year-long cookie, after which the bare URL works). The kernel mints
# the token moments after the login service loads, so wait for the file it
# persists (bounded poll on the mint event's artifact; ROMP_INSTALL_TOKEN_TRIES
# is the test seam) — and when it isn't there yet, say how to get the link
# instead of printing one that would bounce to the login page.
_state_dir="${ROMP_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/romp}"
_kport="${ROMP_KERNEL_PORT:-29855}"
_tok="$(cat "$_state_dir/serve-token" 2>/dev/null || true)"
if [[ -z "$_tok" && -z "${ROMP_NO_SERVICE:-}" ]]; then
    for _ in $(seq 1 "${ROMP_INSTALL_TOKEN_TRIES:-40}"); do
        sleep 0.25
        _tok="$(cat "$_state_dir/serve-token" 2>/dev/null || true)"
        [[ -n "$_tok" ]] && break
    done
fi
# What is NOT working, said once, right before the link — the only place the user reliably
# looks. Each line names the capability in the user's terms, what it costs them, and the exact
# command that fixes it; romp is usable in every one of these states, which is why none of them
# aborted the install. (Before this, all three failures were `|| echo` one-liners buried in the
# scroll or, worse, a kernel stderr line that only reached the system journal.)
# The SDK backend is NOT an optional piece: it is what plain `romp new` uses, so without it romp
# starts, looks healthy, and cannot run a single session. Listing it among the optional ones let a
# fresh install read as fine when it wasn't (the user 2026-07-28). It gets its own banner, above the
# link, in the language of what the user can't do — and it is now rare, since romp-sdk-setup
# bootstraps pip itself rather than sending anyone to sudo.
if [[ -n "$ROMP_SDK_MISSING" ]]; then
    echo
    echo "  ══════════════════════════════════════════════════════════════════"
    echo "   romp is installed but CANNOT START SESSIONS yet."
    echo
    echo "   Its Agent SDK backend — what \`romp new\` runs on — isn't provisioned."
    echo "   Sessions you create will not run until it is."
    echo
    echo "   Fix it:  $ROMP_DIR/bin/romp-sdk-setup"
    echo "            (see its message above if it needs a package installed first)"
    echo "  ══════════════════════════════════════════════════════════════════"
fi
if [[ -n "$ROMP_EXT_FAILED$ROMP_CLAUDE_MISSING" ]]; then
    echo
    echo "  Some optional pieces aren't set up:"
    if [[ -n "$ROMP_EXT_FAILED" ]]; then
        echo "   ! The dashboard UI failed to build — the browser dashboard will come up blank."
        echo "     Retry:  (cd $ROMP_DIR/vscode-extension && npm install && node esbuild.js)"
    fi
    if [[ -n "$ROMP_CLAUDE_MISSING" ]]; then
        echo "   ! Claude Code isn't on PATH — romp drives Claude Code, so sessions need it."
        echo "     Install:  https://claude.com/claude-code   (then just run romp again)"
    fi
fi

echo
if [[ -n "$_tok" ]]; then
    # Lead with the command, not the URL. `romp` opens the dashboard AND prints the link, so it
    # is the shorter thing to remember and the thing the docs already tell you to type (the user
    # 2026-07-27). The link stays as the fallback, for two cases the command cannot cover: THIS
    # terminal still has the pre-install PATH so `romp` will not resolve in it, and a headless or
    # remote box has no browser to open — there, the URL is the only way in.
    echo "  romp is running."
    echo
    echo "      Open a new terminal and type:  romp"
    echo
    echo "  That opens the dashboard in your browser and prints its link. Or open this"
    echo "  directly — the first visit signs the browser in, and afterwards"
    echo "  http://127.0.0.1:$_kport/ works on its own:"
    echo
    echo "      http://127.0.0.1:$_kport/?token=$_tok"
    echo
    echo "  Print the link again anytime:  romp url"
elif [[ -n "${ROMP_NO_SERVICE:-}" ]]; then
    echo "  Auto-start was skipped (ROMP_NO_SERVICE). Start romp with:  romp up"
    echo "  then open the dashboard link:  romp url"
else
    echo "  romp is still starting; print the dashboard link in a moment:  romp url"
fi
