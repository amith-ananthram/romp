#!/usr/bin/env bats

# `romp perf [--interval <s>] [--json]` and `romp perf log on|off` — the kernel's performance counters
# for a terminal: two GET /perf snapshots printed as rates, one raw snapshot, or the POST that flips the
# romp-perf stderr log without a restart.
#
# Same contract as `romp sessions`: the token travels on stdin (never argv), a dead kernel fails
# LOUDLY rather than printing something a reader could mistake for a quiet kernel, and an unknown
# flag is refused. Two more refusals are this verb's own: two snapshots from different kernel
# processes (a restart inside the window) are not subtracted into negative rates, and a refused
# token is named as such rather than reported as a dead kernel. Nothing here touches a real kernel:
# curl is a stub that serves synthetic snapshots in turn and emits the status trailer the real one
# is asked for (-w).

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    # bin/romp resolves the state directory as ${ROMP_STATE_DIR:-$XDG_STATE_HOME/romp} and the token as
    # ${ROMP_SERVE_TOKEN:-<state>/serve-token}: a live kernel's exports outrank the redirection below
    unset ROMP_STATE_DIR ROMP_SERVE_TOKEN
    TEST_DIR="$(mktemp -d)"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'TESTTOKEN123\n' > "$XDG_STATE_HOME/romp/serve-token"
    export ROMP_KERNEL_PORT=29855

    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export CURL_LOG="$TEST_DIR/curl.log"
    export CURL_STDIN="$TEST_DIR/curl.stdin"
    export CURL_CALLS="$TEST_DIR/curl.calls"
    export SNAP_A="$TEST_DIR/a.json"
    export SNAP_B="$TEST_DIR/b.json"
    export SNAP_C="$TEST_DIR/c.json"
    # A and B: the same kernel process ten seconds apart. Over the window: 20 cycles, 60 wakes, 6 s of
    # cycle time (4 s of it in push, 3 s of that in the chat block), 300 ms of pusher CPU and 50 ms of
    # judge CPU inside 500 ms of process CPU, 2 chat rebuilds (one of the watched tab, one of a background
    # tab whose judge_gen component moved) against 18 cache hits, 1 MB sent as chat
    # full frames, one GET /feed.json build (150 ms) against 4 of its cache hits, 100 goal loads, 2 judge
    # passes totalling 2400 ms, 5 /tick requests and 3 WebSocket connects. B's lifetime figures (cycle_ms_max 900, ms_mean 1012.5) differ from the window's
    # (ring max 700, mean 1200) so a line printing the wrong one is caught.
    cat > "$SNAP_A" <<'JSON'
{"now": 1000.0, "since": 900.0, "uptime_s": 100.0, "log": false,
 "process": {"rss_kb": 409600, "threads": 40, "cpu_s": 60.0, "pid": 4242},
 "pusher": {"cycles": 100, "wakes": 300, "wakes_event": 250, "wakes_backstop": 50, "cycle_ms_sum": 30000.0,
            "cycle_ms_max": 900.0, "cycle_ms_last": 200.0, "cycle_cpu_ms_sum": 10000.0,
            "cycle_ms_p50": 180.0, "cycle_ms_p90": 400.0, "cycle_ms_ring_max": 900.0, "ring_n": 100},
 "stages_ms": {"jobs": 5000.0, "push": 20000.0, "push.chat": 15000.0, "push.feed": 3000.0, "push.timeline": 1000.0, "push.send": 500.0},
 "builds": {"chat": {"cached": 80, "built": 20, "ms": 800.0, "active_built": 12, "bg_built": 8, "bg_miss": {"transcript": 5, "states": 2, "judge_gen": 1, "tasks": 0, "cut": 0, "row": 0, "cold": 1, "nosig": 0}}, "feed": {"cached": 90, "built": 10, "ms": 5000.0}, "timeline": {"cached": 95, "built": 5, "ms": 4000.0}, "feedJson": {"cached": 5, "built": 1, "ms": 300.0}},
 "sends": {"full": {"chat": {"count": 10, "bytes": 1000000}}, "delta": {"chat": {"count": 100, "bytes": 50000}}, "deduped": {"feed": {"count": 90, "bytes": 9000000}}},
 "goals": {"loads": 1000, "saves": 200, "writes": 50},
 "judge": {"passes": 30, "ms_sum": 30000.0, "ms_last": 1000.0, "ms_mean": 1000.0, "cpu_ms_sum": 2000.0, "cpu_ms_workers": 1500.0},
 "http": {"GET /tick": {"count": 50, "ms": 25.0}, "GET /sessions": {"count": 5, "ms": 10.0}}}
JSON
    cat > "$SNAP_B" <<'JSON'
{"now": 1010.0, "since": 900.0, "uptime_s": 110.0, "log": false,
 "process": {"rss_kb": 419840, "threads": 41, "cpu_s": 60.5, "pid": 4242},
 "pusher": {"cycles": 120, "wakes": 360, "wakes_event": 300, "wakes_backstop": 60, "cycle_ms_sum": 36000.0,
            "cycle_ms_max": 900.0, "cycle_ms_last": 250.0, "cycle_cpu_ms_sum": 10300.0,
            "cycle_ms_p50": 190.0, "cycle_ms_p90": 420.0, "cycle_ms_ring_max": 700.0, "ring_n": 120},
 "stages_ms": {"jobs": 6000.0, "push": 24000.0, "push.chat": 18000.0, "push.feed": 3600.0, "push.timeline": 1200.0, "push.send": 600.0},
 "builds": {"chat": {"cached": 98, "built": 22, "ms": 880.0, "active_built": 13, "bg_built": 9, "bg_miss": {"transcript": 5, "states": 2, "judge_gen": 2, "tasks": 0, "cut": 0, "row": 0, "cold": 1, "nosig": 0}}, "feed": {"cached": 108, "built": 12, "ms": 6000.0}, "timeline": {"cached": 114, "built": 6, "ms": 4800.0}, "feedJson": {"cached": 9, "built": 2, "ms": 450.0}},
 "sends": {"full": {"chat": {"count": 12, "bytes": 2048576}}, "delta": {"chat": {"count": 120, "bytes": 60000}}, "deduped": {"feed": {"count": 108, "bytes": 10800000}}},
 "goals": {"loads": 1100, "saves": 220, "writes": 55},
 "judge": {"passes": 32, "ms_sum": 32400.0, "ms_last": 1200.0, "ms_mean": 1012.5, "cpu_ms_sum": 2050.0, "cpu_ms_workers": 1540.0},
 "http": {"GET /tick": {"count": 55, "ms": 27.5}, "GET /sessions": {"count": 5, "ms": 10.0}, "GET /ws": {"count": 3, "ms": 0.0}}}
JSON
    # C: a kernel that restarted five seconds into the window — new pid, new `since`, counters reset
    sed -e 's/"since": 900.0/"since": 1005.0/' -e 's/"uptime_s": 110.0/"uptime_s": 5.0/' \
        -e 's/"pid": 4242/"pid": 4343/' -e 's/"cycles": 120/"cycles": 4/' "$SNAP_B" > "$SNAP_C"
    # Stub curl: records argv AND stdin (the auth header rides stdin as a curl config) and emits the
    # body followed by the -w status trailer the script asks for. A POST answers the toggle's ack; a
    # GET serves snapshot A first, then B (or C under CURL_RESTART), so two reads see counters move.
    cat > "$MOCK/curl" <<'MOCK'
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
cat >> "$CURL_STDIN" 2>/dev/null
[ -n "${CURL_FAIL:-}" ] && exit 7
if [ -n "${CURL_403:-}" ]; then printf 'forbidden: token required\n403'; exit 0; fi
if [[ "$*" == *"-X POST"* ]]; then
    printf '{"ok": true, "log": %s}\n200' "$([[ "$*" == *'"log": true'* ]] && echo true || echo false)"
    exit 0
fi
n=0; [ -f "$CURL_CALLS" ] && n="$(cat "$CURL_CALLS")"
echo $((n + 1)) > "$CURL_CALLS"
if [ "$n" -eq 0 ]; then cat "$SNAP_A"; elif [ -n "${CURL_RESTART:-}" ]; then cat "$SNAP_C"; else cat "$SNAP_B"; fi
printf '\n200'
MOCK
    chmod +x "$MOCK/curl"
    export PATH="$MOCK:$PATH"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp perf: prints rates computed from two snapshots" {
    run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"10.0 s window"* ]]                 # the window is the snapshots' own clocks, not the sleep
    [[ "$output" == *"2.00 cycles/s"* ]]                 # 20 cycles over 10 s
    [[ "$output" == *"6.00 wakes/s (event 50, backstop 10)"* ]]
    [[ "$output" == *"busy 60% of wall"* ]]              # 6 s of cycle time in a 10 s window
    [[ "$output" == *"rss 410 MB"* ]]
    [[ "$output" == *"pid 4242"* ]]
}

@test "romp perf: the process line splits the window's CPU between the pusher, the judge and the rest" {
    run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 0 ]
    # 0.5 s of process CPU in 10 s; 300 ms of it on the pusher thread, 50 ms in the judge threads
    [[ "$output" == *"cpu 5.0% of one core (pusher 3.0%, judge 0.5%, other 1.5%)"* ]]
}

@test "romp perf: the cycle line's max is the ring's, and the parenthetical names the ring" {
    run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"p50 190   p90 420   max 700 (over the last 120 cycles)   last 250"* ]]
    [[ "$output" != *"max 900"* ]]                       # the lifetime max is not printed as a window figure
}

@test "romp perf: the stage line shows each stage's share of cycle time" {
    run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 0 ]
    # 1 s of jobs, 4 s of push (3 s chat, 0.6 s feed, 0.2 s timeline, 0.1 s send) out of 6 s of cycles
    [[ "$output" == *"jobs  17%"* ]]
    [[ "$output" == *"push  67%"* ]]
    [[ "$output" == *"chat  50%"* ]]
    [[ "$output" == *"feed  10%"* ]]
}

@test "romp perf: builds, sends, goals, judge and http lines carry the window's deltas" {
    run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 0 ]
    # the chat line's split: the watched tab's rebuild against the background one's, and per signature
    # component the background rebuilds it caused (only the non-zero causes; judge_gen moved once in the window)
    [[ "$output" == *"chat 2 built / 18 cached (40 ms avg; 1 watched, 1 background: judge_gen 1)"* ]]
    # GET /feed.json's own reads print beside the pusher's feed, never folded into it (review find, 2026-09-08)
    [[ "$output" == *"feedJson 1 built / 4 cached (150 ms avg)"* ]]
    [[ "$output" == *"full 102 KB/s (chat 2 frames 102 KB/s)"* ]]        # 1048576 bytes over 10 s, bytes beside the count
    [[ "$output" == *"deduped 176 KB/s (feed 18 frames 176 KB/s)"* ]]
    [[ "$output" == *"10.0 loads/s   2.0 saves/s   0.5 writes/s"* ]]
    [[ "$output" == *"2 passes (0.20/s)   last 1200 ms   mean 1200 ms"* ]]   # the WINDOW mean: 2400 ms over 2 passes
    [[ "$output" != *"1012"* ]]                          # not the lifetime ms_mean
    [[ "$output" == *"GET /tick 5 (0.5 ms avg)"* ]]
    [[ "$output" == *"GET /ws 3"* ]]                     # a WebSocket row: count only …
    [[ "$output" != *"GET /ws 3 ("* ]]                   # … never a fabricated 0.0 ms avg
    [[ "$output" != *"/sessions"* ]]                     # no requests in the window: not listed
}

@test "romp perf: a restart inside the window is said, not subtracted into negative rates" {
    CURL_RESTART=1 run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 1 ]
    [[ "$output" == *"the kernel restarted during the window (pid 4242 -> 4343)"* ]]
    [[ "$output" != *"cycles/s"* ]]
}

@test "romp perf --json: prints one raw snapshot verbatim and reads the kernel once" {
    run "$ROMP_SCRIPT" perf --json
    [ "$status" -eq 0 ]
    echo "$output" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["pusher"]["cycles"] == 100; assert d["process"]["pid"] == 4242'
    [ "$(cat "$CURL_CALLS")" -eq 1 ]
}

@test "romp perf: reads GET /perf on the kernel, authorizing on stdin, twice" {
    run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 0 ]
    [ "$(grep -c "127.0.0.1:29855/perf" "$CURL_LOG")" -eq 2 ]
    grep -q "X-Romp-Token: TESTTOKEN123" "$CURL_STDIN"
    # never in argv: /proc/<pid>/cmdline is world-readable
    run grep -q "TESTTOKEN123" "$CURL_LOG"
    [ "$status" -ne 0 ]
}

@test "romp perf log on|off: POSTs the toggle to /perf and names the journal on systemd" {
    run "$ROMP_SCRIPT" perf log on
    [ "$status" -eq 0 ]
    [[ "$output" == *"log on"* ]]
    [[ "$output" == *"journalctl --user -u romp-manager"* ]]
    grep -q -- "-X POST http://127.0.0.1:29855/perf" "$CURL_LOG"
    grep -q '"log": true' "$CURL_LOG"
    grep -q "X-Romp-Token: TESTTOKEN123" "$CURL_STDIN"
    run "$ROMP_SCRIPT" perf log off
    [ "$status" -eq 0 ]
    [[ "$output" == *"log off"* ]]
    grep -q '"log": false' "$CURL_LOG"
}

@test "romp perf log on: under launchd the hint names the manager.log file, not journalctl" {
    printf '#!/usr/bin/env bash\necho Darwin\n' > "$MOCK/uname"; chmod +x "$MOCK/uname"
    run "$ROMP_SCRIPT" perf log on
    [ "$status" -eq 0 ]
    [[ "$output" == *"tail -f $XDG_STATE_HOME/romp/manager.log"* ]]
    [[ "$output" != *"journalctl"* ]]
}

@test "romp perf log: anything but on|off is refused" {
    run "$ROMP_SCRIPT" perf log maybe
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp perf"* ]]
    run "$ROMP_SCRIPT" perf log
    [ "$status" -eq 2 ]
    [ ! -f "$CURL_LOG" ]                                 # nothing reached the kernel
}

@test "romp perf: a dead kernel fails LOUDLY" {
    CURL_FAIL=1 run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -ne 0 ]
    [[ "$output" == *"kernel not reachable"* ]]
    CURL_FAIL=1 run "$ROMP_SCRIPT" perf log on
    [ "$status" -ne 0 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "romp perf: a refused token is named as such, not reported as a dead kernel" {
    CURL_403=1 run "$ROMP_SCRIPT" perf --interval 0
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused the serve token (HTTP 403)"* ]]
    [[ "$output" != *"not reachable"* ]]
    CURL_403=1 run "$ROMP_SCRIPT" perf log on
    [ "$status" -eq 1 ]
    [[ "$output" == *"refused the serve token (HTTP 403)"* ]]
}

@test "romp perf: an unknown flag or a bad interval is refused rather than silently ignored" {
    run "$ROMP_SCRIPT" perf --nope
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp perf"* ]]
    run "$ROMP_SCRIPT" perf --interval soon
    [ "$status" -eq 2 ]
    run "$ROMP_SCRIPT" perf --interval
    [ "$status" -eq 2 ]
}

@test "romp perf: listed in help, under the scripting group" {
    run "$ROMP_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp perf"* ]]
}
