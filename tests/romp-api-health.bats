#!/usr/bin/env bats

# `romp api-health` — the kernel's API-health signal for scripts: GET /api-health, printed verbatim.
# Same contract as `romp sessions`: the kernel owns the state, the token travels on stdin (never
# argv), a dead kernel fails LOUDLY rather than printing something a consumer could mistake for a
# healthy signal, and an unknown flag is refused. A kernel that ANSWERS with a refusal is reported as
# what it said (the route's own 503, a refused token, a kernel without the route), never as "not
# reachable": a consumer acts differently on each.

ROMP_SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/../bin" && pwd)/romp"

setup() {
    # bin/romp prefers a live kernel's exports over the XDG floor: a suite run from inside a romp
    # session inherits both, and would read the real token instead of the one written below
    unset ROMP_STATE_DIR ROMP_SERVE_TOKEN
    TEST_DIR="$(mktemp -d)"
    export XDG_STATE_HOME="$TEST_DIR/state"
    mkdir -p "$XDG_STATE_HOME/romp"
    printf 'TESTTOKEN123\n' > "$XDG_STATE_HOME/romp/serve-token"
    export ROMP_KERNEL_PORT=29855

    # Stub curl: records argv AND stdin (the auth header rides stdin as a curl config), replies
    # with a synthetic signal. It honours -w the way curl does (the format after the body, with the
    # status filled in), so the script's status split runs for real. CURL_FAIL: no reply at all
    # (connection refused, curl's exit 7). CURL_CODE + CURL_BODY: a reply with that status and body.
    # Nothing touches a real kernel.
    MOCK="$TEST_DIR/mock"; mkdir -p "$MOCK"
    export CURL_LOG="$TEST_DIR/curl.log"
    export CURL_STDIN="$TEST_DIR/curl.stdin"
    export SIGNAL_JSON="$TEST_DIR/signal.json"
    cat > "$SIGNAL_JSON" <<'JSON'
{"schema": 1, "asOf": 1756800000.4, "bootId": "4242.1756790000", "rate429Basis": "attempts",
 "coverage": {"sidechainExcluded": true, "sdkSessionsLive": 2, "inTurn": 1, "retrying": 1},
 "overall": {"state": "thrashing", "worstBucket": "key:0123456789ab|fable"},
 "buckets": {"key:0123456789ab|fable": {"state": "thrashing",
   "windows": {"300": {"requests": 20, "rateLimited": 8, "rate429": 0.4}}}}}
JSON
    cat > "$MOCK/curl" <<'MOCK'
#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
cat >> "$CURL_STDIN" 2>/dev/null
[ -n "${CURL_FAIL:-}" ] && exit 7
_w=""
while [ $# -gt 0 ]; do [ "$1" = "-w" ] && _w="$2"; shift; done
if [ -n "${CURL_CODE:-}" ]; then printf '%s' "${CURL_BODY:-}"; else cat "$SIGNAL_JSON"; fi
if [ -n "$_w" ]; then printf '%b' "$(printf '%s' "$_w" | sed "s/%{http_code}/${CURL_CODE:-200}/")"; fi
exit 0
MOCK
    chmod +x "$MOCK/curl"
    export PATH="$MOCK:$PATH"
}

teardown() { rm -rf "$TEST_DIR"; }

@test "romp api-health: prints the kernel's signal verbatim" {
    run "$ROMP_SCRIPT" api-health
    [ "$status" -eq 0 ]
    [[ "$output" == *'"thrashing"'* ]]
    echo "$output" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["overall"]["state"] == "thrashing"; assert d["coverage"]["sidechainExcluded"] is True'
}

@test "romp api-health: reads GET /api-health on the kernel, authorizing on stdin" {
    run "$ROMP_SCRIPT" api-health
    [ "$status" -eq 0 ]
    grep -q "127.0.0.1:29855/api-health" "$CURL_LOG"
    grep -q "X-Romp-Token: TESTTOKEN123" "$CURL_STDIN"
    # never in argv: /proc/<pid>/cmdline is world-readable
    run grep -q "TESTTOKEN123" "$CURL_LOG"
    [ "$status" -ne 0 ]
}

@test "romp api-health: a dead kernel fails LOUDLY, never a blank a consumer reads as healthy" {
    CURL_FAIL=1 run "$ROMP_SCRIPT" api-health
    [ "$status" -ne 0 ]
    [[ "$output" == *"kernel not reachable"* ]]
}

@test "romp api-health: the route's own 503 is reported as itself, with its message, not as a dead kernel" {
    # the kernel is up; the SDK backend is not, so there is no signal yet: a different situation from
    # a kernel that is down, and the consumer must be able to tell them apart
    CURL_CODE=503 CURL_BODY='{"error": "the SDK backend is unavailable: no signal"}' run "$ROMP_SCRIPT" api-health
    [ "$status" -eq 1 ]
    [[ "$output" == *"503"* ]]
    [[ "$output" == *"the SDK backend is unavailable: no signal"* ]]
    [[ "$output" != *"kernel not reachable"* ]]
    [[ "$output" != *'{"error"'* ]]
}

@test "romp api-health: a 404 says the kernel predates the command and wants a restart" {
    CURL_CODE=404 CURL_BODY='' run "$ROMP_SCRIPT" api-health
    [ "$status" -eq 1 ]
    [[ "$output" == *"404"* ]]
    [[ "$output" == *"restart"* ]]
    [[ "$output" != *"kernel not reachable"* ]]
}

@test "romp api-health: a refused token is reported as that, not as a dead kernel" {
    CURL_CODE=403 CURL_BODY='forbidden' run "$ROMP_SCRIPT" api-health
    [ "$status" -eq 1 ]
    [[ "$output" == *"403"* ]]
    [[ "$output" == *"token"* ]]
    [[ "$output" != *"kernel not reachable"* ]]
}

@test "romp api-health: an unknown flag is refused rather than silently ignored" {
    run "$ROMP_SCRIPT" api-health --nope
    [ "$status" -eq 2 ]
    [[ "$output" == *"usage: romp api-health"* ]]
}

@test "romp api-health: listed in help, under the scripting group" {
    run "$ROMP_SCRIPT" help
    [ "$status" -eq 0 ]
    [[ "$output" == *"romp api-health"* ]]
}
