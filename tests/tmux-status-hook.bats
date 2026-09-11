#!/usr/bin/env bats

# Resolve path to the hook script under test
HOOK_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../hooks" && pwd)/tmux-status.sh"

load tmux-private

setup() {
    TEST_DIR="$(mktemp -d)"
    MOCK_DIR="$TEST_DIR/mock"
    export MOCK_LOG="$TEST_DIR/mock.log"

    mkdir -p "$MOCK_DIR"

    # ── Mock tmux ──────────────────────────────────────────────────────
    cat > "$MOCK_DIR/tmux" << 'MOCK'
#!/usr/bin/env bash
echo "tmux $*" >> "$MOCK_LOG"
# display-message -p '#S' → return session name from env
if [[ "$1" == "display-message" && "$2" == "-p" && "$3" == "#S" ]]; then
    echo "${MOCK_SESSION_NAME:-test}"
fi
# show -t NAME -v @romp → the romp marker (empty = not a romp session)
if [[ "$1" == "show" && "$5" == "@romp" ]]; then
    echo "${MOCK_IS_ROMP-1}"
fi
# show -t NAME -v @claude-state → the PREVIOUS state (for the sticky-compaction guard)
if [[ "$1" == "show" && "$5" == "@claude-state" ]]; then
    echo "${MOCK_PREV_STATE-}"
fi
# show -t NAME -v @romp-session-id → durable session id (default EMPTY so existing tests write no
# states file; a test opts in by exporting MOCK_SESSION_ID + XDG_STATE_HOME)
if [[ "$1" == "show" && "$5" == "@romp-session-id" ]]; then
    echo "${MOCK_SESSION_ID-}"
fi
exit 0
MOCK
    chmod +x "$MOCK_DIR/tmux"

    # ── Tripwire romp-idle-dots ────────────────────────────────────────
    # The tmux-only idle-dot watcher went with the tmux backend's dead leaves (2026-09-11); a
    # stand-in on PATH logs any call the hook still makes, so the no-watcher test can fail.
    cat > "$MOCK_DIR/romp-idle-dots" << 'MOCK'
#!/usr/bin/env bash
echo "romp-idle-dots $*" >> "$MOCK_LOG"
exit 0
MOCK
    chmod +x "$MOCK_DIR/romp-idle-dots"

    export PATH="$MOCK_DIR:$PATH"
    # The hook shells out to `tmux` by name on every event; the mock takes those calls, and the private
    # socket directory keeps any that reach the real binary off the machine's tmux server
    # (tests/tmux-private.bash has the 2026-09-06 incident).
    tmux_private_socket_dir "$TEST_DIR"
    export TMUX="fake"
    export MOCK_SESSION_NAME="test"
    export MOCK_IS_ROMP=1
    # The hook takes the SDK path (touching tmux NOT AT ALL) when Claude Code's own
    # CLAUDE_CODE_ENTRYPOINT is sdk*. Left INHERITED, that makes this suite non-hermetic:
    # it passes on CI, where the var is unset, and fails 25 tests for anyone running it
    # from inside an SDK-backed romp session, where it is "sdk-py". Default it to the
    # tmux path here; the two tests that exercise the SDK path export it themselves.
    unset CLAUDE_CODE_ENTRYPOINT
}

teardown() {
    # The kill before the rm (a server the real tmux started must not outlive the test), and last, so
    # its failure is teardown's status: bats swallows a failing command mid-teardown.
    tmux_private_kill && rm -rf "$TEST_DIR"
}

# Helper — runs the hook with JSON on stdin
run_hook() {
    echo "$1" | "$HOOK_SCRIPT" 2>&1
}

# ─── Event mapping tests ──────────────────────────────────────────────

@test "SessionStart sets state to waiting" {
    run run_hook '{"hook_event_name":"SessionStart","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state waiting' "$MOCK_LOG"
}

@test "UserPromptSubmit sets state to working" {
    run run_hook '{"hook_event_name":"UserPromptSubmit","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state working' "$MOCK_LOG"
}

@test "PostToolUse sets state to working" {
    run run_hook '{"hook_event_name":"PostToolUse","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state working' "$MOCK_LOG"
}

@test "Stop sets state to waiting" {
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state waiting' "$MOCK_LOG"
}

@test "PreCompact sets state to compacting" {
    run run_hook '{"hook_event_name":"PreCompact","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state compacting' "$MOCK_LOG"
}

@test "compacting is STICKY: a Stop mid-compaction keeps compacting (no split)" {
    export MOCK_PREV_STATE=compacting
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state compacting' "$MOCK_LOG"
    ! grep -q '@claude-state waiting' "$MOCK_LOG"
}

@test "compacting is STICKY against a working event too" {
    export MOCK_PREV_STATE=compacting
    run run_hook '{"hook_event_name":"PostToolUse","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state compacting' "$MOCK_LOG"
}

@test "PostCompact ENDS compacting (the only exit)" {
    export MOCK_PREV_STATE=compacting
    run run_hook '{"hook_event_name":"PostCompact","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state waiting' "$MOCK_LOG"
}

@test "PostCompact trigger=manual sets waiting (the /compact was the whole exchange)" {
    export MOCK_PREV_STATE=compacting
    run run_hook '{"hook_event_name":"PostCompact","trigger":"manual","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state waiting' "$MOCK_LOG"
    ! grep -q '@claude-state working' "$MOCK_LOG"
}

@test "PostCompact trigger=auto keeps working (the compaction ran inside a turn that goes on)" {
    export MOCK_PREV_STATE=compacting
    run run_hook '{"hook_event_name":"PostCompact","trigger":"auto","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state working' "$MOCK_LOG"
    run grep -q '@claude-state waiting' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    run grep -q '@claude-state compacting' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

# ─── Notification mapping tests ───────────────────────────────────────

@test "Notification permission_prompt sets state to permission" {
    run run_hook '{"hook_event_name":"Notification","notification_type":"permission_prompt","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state permission' "$MOCK_LOG"
}

@test "Notification idle_prompt sets state to idle" {
    run run_hook '{"hook_event_name":"Notification","notification_type":"idle_prompt","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state idle' "$MOCK_LOG"
}

@test "Notification with unknown type exits 0 and no tmux set calls" {
    run run_hook '{"hook_event_name":"Notification","notification_type":"something_else","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q 'tmux set -t' "$MOCK_LOG"
}

# ─── Status-emoji tests (ghostty tab dot) ────────────────────────────

@test "working state sets the yellow tab dot" {
    run run_hook '{"hook_event_name":"UserPromptSubmit","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q '@romp-emoji 🟡' "$MOCK_LOG"
}

@test "waiting state sets the blue tab dot" {
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q '@romp-emoji 🔵' "$MOCK_LOG"
}

@test "permission state sets the red tab dot" {
    run run_hook '{"hook_event_name":"Notification","notification_type":"permission_prompt","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q '@romp-emoji 🔴' "$MOCK_LOG"
}

@test "compacting sets the monochrome compress glyph, NOT a coloured dot (the user 2026-06-22)" {
    # compacting is a transient PROCESS, so it reads as ⇲ (a monochrome compress glyph) instead of
    # another colour dot — distinct from 🟡/🔴/🔵 at a glance.
    run run_hook '{"hook_event_name":"PreCompact","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q '@romp-emoji ⇲' "$MOCK_LOG"
    ! grep -q '@romp-emoji 🟠' "$MOCK_LOG"
}

@test "idle state sets the blue tab dot" {
    run run_hook '{"hook_event_name":"Notification","notification_type":"idle_prompt","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q '@romp-emoji 🔵' "$MOCK_LOG"
}

# ─── No timer-side watcher: the tmux-only healer (romp-idle-dots) went with the tmux backend's
# dead leaves, 2026-09-11. The hook must not shell out for it on any event. ───

@test "no event spawns the retired idle-dot watcher" {
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    run run_hook '{"hook_event_name":"Notification","notification_type":"idle_prompt","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    run run_hook '{"hook_event_name":"UserPromptSubmit","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    run grep -q 'romp-idle-dots' "$MOCK_LOG"
    [ "$status" -ne 0 ]
}

# ─── Unknown event test ──────────────────────────────────────────────

@test "unknown event exits 0 with no tmux set calls" {
    run run_hook '{"hook_event_name":"SomeNewEvent","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q 'tmux set -t' "$MOCK_LOG"
}

# ─── Non-claude session test ─────────────────────────────────────────

@test "non-romp session (no @romp flag) exits early" {
    export MOCK_IS_ROMP=""
    run run_hook '{"hook_event_name":"SessionStart","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q 'tmux set -t' "$MOCK_LOG"
}

# ─── No TMUX env test ────────────────────────────────────────────────

@test "no TMUX env exits early" {
    unset TMUX
    run run_hook '{"hook_event_name":"SessionStart","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q 'tmux set -t' "$MOCK_LOG"
}

# ─── cwd extraction test ─────────────────────────────────────────────

@test "cwd is stored as @claude-dir" {
    run run_hook '{"hook_event_name":"SessionStart","cwd":"/home/user/myproject"}'
    [ "$status" -eq 0 ]
    grep -q 'set -t test @claude-dir /home/user/myproject' "$MOCK_LOG"
}

@test "state-since timestamp is set" {
    run run_hook '{"hook_event_name":"SessionStart","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q 'set -t test @claude-state-since' "$MOCK_LOG"
}

# ─── permission_mode extraction (feed block detection) ───────────────

@test "permission_mode is published as @claude-permission-mode (auto mode)" {
    run run_hook '{"hook_event_name":"Notification","notification_type":"permission_prompt","permission_mode":"acceptEdits","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q 'set -t test @claude-permission-mode acceptEdits' "$MOCK_LOG"
}

@test "permission_mode is published on ordinary events too (default mode)" {
    run run_hook '{"hook_event_name":"UserPromptSubmit","permission_mode":"default","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q 'set -t test @claude-permission-mode default' "$MOCK_LOG"
}

@test "absent permission_mode never writes the var (no clobber)" {
    run run_hook '{"hook_event_name":"UserPromptSubmit","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q '@claude-permission-mode' "$MOCK_LOG"
}

# ─── headless (no-tmux) backend tests ─────────────────────────────────
# A session launched by a non-tmux backend exports ROMP_SESSION_ID; the hook
# must write the durable states/<sid>.jsonl record and touch tmux not at all.

@test "headless: ROMP_SESSION_ID writes the durable state record, no tmux" {
    unset TMUX
    export ROMP_SESSION_ID="headless-sid-1"
    export XDG_STATE_HOME="$TEST_DIR/state"
    run run_hook '{"hook_event_name":"UserPromptSubmit","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q '"state":"working"' "$TEST_DIR/state/romp/states/headless-sid-1.jsonl"
    ! grep -q 'tmux set -t' "$MOCK_LOG"
}

@test "headless: repeated same-state events append only one record" {
    unset TMUX
    export ROMP_SESSION_ID="headless-sid-2"
    export XDG_STATE_HOME="$TEST_DIR/state"
    run_hook '{"hook_event_name":"UserPromptSubmit","cwd":"/tmp"}'
    run_hook '{"hook_event_name":"PostToolUse","cwd":"/tmp"}'
    [ "$(wc -l < "$TEST_DIR/state/romp/states/headless-sid-2.jsonl")" -eq 1 ]
    run_hook '{"hook_event_name":"Stop","cwd":"/tmp"}'
    [ "$(wc -l < "$TEST_DIR/state/romp/states/headless-sid-2.jsonl")" -eq 2 ]
}

@test "headless: no ROMP_SESSION_ID still exits silently" {
    unset TMUX
    unset ROMP_SESSION_ID
    export XDG_STATE_HOME="$TEST_DIR/state"
    run run_hook '{"hook_event_name":"SessionStart","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    [ ! -d "$TEST_DIR/state/romp/states" ]
}

# ─── awaiting overlay: leftover background SHELL tasks do NOT count (the user 2026-07-07) ─────
# At turn-end the hook no longer treats the Stop payload's `background_tasks` (run_in_background shell
# work) as awaiting — a leftover backgrounded shell task (a dev server, a tail -f, a hung command the
# agent never reaped) must not pin an idle session to a working flavor. Only real subagents (the SDK
# backend's live snapshot) leave a session awaiting. The hook only CLEARS a stale awaiting:true
# (transition-only), so the ⏳ badge never lights from a shell task.

@test "Stop with background_tasks does NOT write an awaiting overlay" {
    export MOCK_SESSION_ID="tmux-aw-1"
    export XDG_STATE_HOME="$TEST_DIR/state"
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp","background_tasks":[{"id":"t1"}]}'
    [ "$status" -eq 0 ]
    ! grep -q '"awaiting"' "$TEST_DIR/state/romp/states/tmux-aw-1.jsonl" 2>/dev/null
}

@test "a stale awaiting:true is cleared to false on the next Stop (even with a shell task 'running')" {
    export MOCK_SESSION_ID="tmux-aw-2"
    export XDG_STATE_HOME="$TEST_DIR/state"
    f="$TEST_DIR/state/romp/states/tmux-aw-2.jsonl"
    mkdir -p "$(dirname "$f")"
    printf '{"t":1,"awaiting":true,"why":"legacy"}\n' > "$f"   # a stale true (e.g. written before this change)
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp","background_tasks":[{"id":"t1"}]}'
    [ "$status" -eq 0 ]
    grep -q '"awaiting":false' "$f"
}

@test "no stale true → nothing to clear, no awaiting record written" {
    export MOCK_SESSION_ID="tmux-aw-3"
    export XDG_STATE_HOME="$TEST_DIR/state"
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp","background_tasks":[]}'
    [ "$status" -eq 0 ]
    ! grep -q '"awaiting"' "$TEST_DIR/state/romp/states/tmux-aw-3.jsonl" 2>/dev/null
}

@test "headless (SDK) sessions get NO tmux-side awaiting overlay (the SDK backend writes its own)" {
    unset TMUX
    export ROMP_SESSION_ID="headless-aw-1"
    export XDG_STATE_HOME="$TEST_DIR/state"
    run run_hook '{"hook_event_name":"Stop","cwd":"/tmp","background_tasks":[{"id":"t1"}]}'
    [ "$status" -eq 0 ]
    ! grep -q '"awaiting"' "$TEST_DIR/state/romp/states/headless-aw-1.jsonl"
}

# ─── re-anchor @romp-session-id across a /clear (the user 2026-07-06) ──────────
# A /clear forks the transcript to a NEW fsid but @romp-session-id stays frozen on the creation fsid, so
# the kernel keys liveness on a stale transcript and the picker shows the LIVE session as dead → Revive. On
# a SessionStart whose payload session_id differs from the anchor, the hook re-points the var to the live
# fsid and mirrors the anchor-keyed names entry so every surface resolves the live transcript.

@test "SessionStart with a NEW session_id re-anchors @romp-session-id + mirrors the names entry" {
    export MOCK_SESSION_ID="anchor-old"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$TEST_DIR/state/romp/names"
    printf 'DEMO\t/tmp/project\t#aabbcc\tblue' > "$TEST_DIR/state/romp/names/anchor-old"
    run run_hook '{"hook_event_name":"SessionStart","source":"clear","session_id":"fork-new","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'set -t test @romp-session-id fork-new' "$MOCK_LOG"
    [ -f "$TEST_DIR/state/romp/names/fork-new" ]
    # the mirror is a faithful copy — the live fork resolves the same name/cwd/color
    [ "$(cat "$TEST_DIR/state/romp/names/fork-new")" = "$(cat "$TEST_DIR/state/romp/names/anchor-old")" ]
    # the durable state log is now written under the LIVE fsid, not the stale anchor
    grep -q '"state":"waiting"' "$TEST_DIR/state/romp/states/fork-new.jsonl"
}

@test "SessionStart whose session_id MATCHES the anchor is a no-op (normal startup/resume)" {
    export MOCK_SESSION_ID="same-sid"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$TEST_DIR/state/romp/names"
    printf 'DEMO\t/tmp/x' > "$TEST_DIR/state/romp/names/same-sid"
    run run_hook '{"hook_event_name":"SessionStart","source":"startup","session_id":"same-sid","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q 'set -t test @romp-session-id' "$MOCK_LOG"   # the var is never REWRITTEN when it already matches (a read is fine)
}

@test "no re-anchor when @romp-session-id is unset (never clobber a session with no anchor)" {
    export MOCK_SESSION_ID=""                        # anchor not set yet
    export XDG_STATE_HOME="$TEST_DIR/state"
    run run_hook '{"hook_event_name":"SessionStart","source":"clear","session_id":"fork-new","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    # `run` + status, NOT a bare `! grep`: `!` is exempt from set -e, so mid-test it asserts nothing.
    run grep -q 'set -t test @romp-session-id' "$MOCK_LOG"
    [ "$status" -ne 0 ]
    [ ! -e "$TEST_DIR/state/romp/names/fork-new" ]
}

@test "re-anchor never overwrites an existing names entry for the live fsid" {
    export MOCK_SESSION_ID="anchor-old"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$TEST_DIR/state/romp/names"
    printf 'DEMO\t/tmp/project' > "$TEST_DIR/state/romp/names/anchor-old"
    printf 'ALREADY\t/tmp/other' > "$TEST_DIR/state/romp/names/fork-new"   # the fork already has its own name
    run run_hook '{"hook_event_name":"SessionStart","source":"clear","session_id":"fork-new","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'set -t test @romp-session-id fork-new' "$MOCK_LOG"            # still re-points the var
    [ "$(cat "$TEST_DIR/state/romp/names/fork-new")" = "$(printf 'ALREADY\t/tmp/other')" ]   # but keeps the existing name
}

@test "a NON-SessionStart event with a differing session_id ALSO re-anchors (a missed start heals late)" {
    export MOCK_SESSION_ID="anchor-old"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$TEST_DIR/state/romp/names"
    printf 'DEMO\t/tmp/project' > "$TEST_DIR/state/romp/names/anchor-old"
    run run_hook '{"hook_event_name":"Stop","session_id":"fork-new","cwd":"/tmp/project"}'
    [ "$status" -eq 0 ]
    grep -q 'set -t test @romp-session-id fork-new' "$MOCK_LOG"   # any event heals — SessionStart is just the usual first
    [ -e "$TEST_DIR/state/romp/names/fork-new" ]
}

@test "a payload without session_id leaves the var alone" {
    export MOCK_SESSION_ID="anchor-old"
    export XDG_STATE_HOME="$TEST_DIR/state"
    run run_hook '{"hook_event_name":"SessionStart","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q 'set -t test @romp-session-id' "$MOCK_LOG"
}

# ─── leaked-$TMUX guard: an SDK session must not hijack the attached tmux session (the user 2026-07-20) ──
# When the manager is launched from INSIDE a tmux session (e.g. a manual `romp-manager up`), its kernels and
# SDK-session children inherit that $TMUX. Without this guard, each SDK event's hook would take the tmux
# branch, resolve #S to whatever session is ATTACHED, and clobber that session's @romp-session-id + display
# state with the SDK session's own fsid — flapping the attached session's anchor so the kernel can't see it
# live and the picker offers a bogus "revive". CLAUDE_CODE_ENTRYPOINT=sdk-* is Claude Code's own launch
# marker; such a session is never a tmux display session, leaked $TMUX or not.

@test "SDK session with a leaked \$TMUX touches the attached tmux session not at all" {
    export CLAUDE_CODE_ENTRYPOINT="sdk-py"       # a Python-SDK-launched (headless) session
    export XDG_STATE_HOME="$TEST_DIR/state"
    # TMUX is set (leaked) + MOCK_SESSION_NAME=test from setup, but the guard must divert it off the tmux path
    run run_hook '{"hook_event_name":"PostToolUse","session_id":"sdk-fsid","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    ! grep -q 'tmux set -t' "$MOCK_LOG"           # no @romp-session-id / @claude-state clobber of 'test'
}

@test "an interactive (non-SDK) tmux session is unaffected by the guard" {
    export CLAUDE_CODE_ENTRYPOINT="cli"           # not sdk* → normal tmux display path
    run run_hook '{"hook_event_name":"UserPromptSubmit","cwd":"/tmp"}'
    [ "$status" -eq 0 ]
    grep -q 'tmux set -t test @claude-state working' "$MOCK_LOG"
}
