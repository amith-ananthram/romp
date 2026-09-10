# Contributing

Thanks for looking at Romp.

This is a personal side project. Bug reports and pull requests are
welcome, and I'd rather hear about a problem than not. Responses may be slow, and
I may not get to everything. 

If you're interested in reporting bugs and making PRs, please try to reproduce them or ground your suggestions with the latest code at the tip of the main branch rather than a tagged release version.

## Running the tests

```bash
python3 -m pytest -q       # the Python pipeline (kernel/, cli/, postal/)
bats tests/*.bats          # the shell surfaces (hooks, postal, manager)
cd vscode-extension && npm ci && npm test
```

`tests/gitleaks-config.bats` checks the secret-scanning rules in `.gitleaks.toml`
against the real scanner and skips itself when `gitleaks` is not installed
(`brew install gitleaks`, or a release binary; `ROMP_GITLEAKS` names one that is
not on `PATH`). Installing it also arms the credential half of the `pre-push`
hook, which is worth having before you push anything.

The Python and shell suites are also the CI gate, across Python 3.10 to 3.13 on
Linux; the macOS cells run on demand from the Actions tab (they are billed even
on a public repo, so they are not part of the per-push matrix).

## Measuring dashboard pane performance

`tools/ui-bench.mjs` replays a pane's frame stream into the real pane page in
a headless Chromium and reports where the browser's time goes, so a rendering
change can be measured before and after it lands, on the same input. It needs
the extension's `node_modules` (`cd vscode-extension && npm ci`), a built
`dist/` (`npm run build` there), `python3`, and a Chromium: Playwright's own
(`npx playwright install chromium` in `vscode-extension/`) or a system Google
Chrome. The tool is POSIX-only (Linux and macOS).

```bash
# a frame stream with invented content, for a bench that needs no live kernel
node tools/ui-bench.mjs --synthesize feed --cards 200 --out /tmp/romp-perf/synth-feed.jsonl
# replay it into the real feed page, a frame every 100 ms, and print per-frame timings
node tools/ui-bench.mjs --replay feed --frames /tmp/romp-perf/synth-feed.jsonl --gap 100 --json /tmp/romp-perf/before.json
# change the bundle, rebuild, replay again, then compare the two reports
node tools/ui-bench.mjs --compare /tmp/romp-perf/before.json /tmp/romp-perf/after.json
# record 90 seconds of what a running kernel sends the feed page, then replay that
node tools/ui-bench.mjs --record feed --seconds 90 --out /tmp/romp-perf/frames-feed.jsonl
node tools/ui-bench.mjs --replay feed --frames /tmp/romp-perf/frames-feed.jsonl --json /tmp/romp-perf/live.json
```

The pane page's shim does not render a frame inside its WebSocket handler. The
handler parses the frame, applies a view delta to the state it holds, and
queues the result; a separate task (a `MessageChannel` message, so it runs
while the tab is hidden too) hands the queued frames to the bundle, where a
newer whole-state frame (a full feed, the timeline's bars or skeleton, a tab
order) replaces an older one still queued, and the task stops after 8 ms and
re-arms itself so input can land between slices. A wire frame and the
bundle's render of it are therefore two measurements, and a replay reports
both, per frame type: the bytes; the handler time (the shim's synchronous
work per wire frame); the bundle time for each frame the shim delivered on its
own; and the settle, the time from a frame's receipt until the main thread is
free again (the second `requestAnimationFrame` after the delivery that
carried it), each as p50, p90, and max. A `delivered` column beside `count`
says how many frames of a type reached the bundle as their own delivery; the
rest were coalesced into a newer frame's (the report counts them) or, for
keepalives and the resync asks the shim answers itself, never left the shim.
Frames sent back-to-back with `--fast` queue together, so most of a stream's
deltas coalesce into one delivery and the bundle column covers only that
one; for the bundle's cost per frame, replay at the recorded pacing or with
`--gap 100` (a fixed gap in milliseconds between frames). On a kernel whose
shim still renders inside the handler, the report says so and the handler
column includes the bundle's time.

After the table come the long-animation-frame entries with script
attribution, the JavaScript heap after a forced garbage collection (and before
it, so the line shows what the collection freed), the DOM size, and every
console error and uncaught exception. The attribution names each task's entry point
(the WebSocket message handler, the shim's flush task, a
`requestAnimationFrame` callback, a timer, a script's evaluation), not the
function inside the bundle that did the work. For that, add
`--cpu-profile /tmp/romp-perf/feed.cpuprofile`: it samples the page's
JavaScript with the V8 profiler across the replay, writes a file Chrome
DevTools loads (Performance panel), and prints the functions with the most
self and total time as `bundle.js:function:line` with the source position from
the dist's `.map` files (a `--production` dist is minified and has none; the
report says so), overall and inside the delivery that carried the first
content frame and the largest frame of each type. For the hottest functions
it also names the lines that hold the time; a forced synchronous layout, for
instance, shows up as one line of one function owning most of its self time.
The end-of-run layout, style, script and task counters are cumulative since
navigation, so they include page load and idle timers (the timeline redraws
every animation frame while it follows the present), and `--compare` shows
them without percentages when the two runs differ in pacing or length.
`--cpu-throttle 4` emulates a machine four times slower (the default is no
throttling). `--iters 3` pools three runs. With `--fast`, settle times overlap
(a frame's includes the frames queued behind it); handler and bundle times do
not.

The numbers come from the real pages: the kernel's own HTTP handler serves the
HTML, the shim, and the bundles from a `python3` subprocess under an isolated
environment, the pattern of `tests/test_color_route.py` with the floors
`tests/conftest.py` applies (the manager variables are removed and the manager
port set to a dead one; every credential and key-source name conftest pops is
removed, the API keys, the key reference and command, the token credentials,
the auth declaration and 1Password's names; the manager's key file and the
boot model-catalog fetch are
pointed away, the Claude binary is `/bin/false`, the CLI scope is off, the
postal peer bus is off, the serve token is minted for the run, and the
subprocess exits when the bench does). Run state, the browser's profile
included, lives under one per-user directory in the temp root; a run killed
with its whole process group leaves its entry there until the next run sweeps
it. A Node front server answers the page's WebSocket and proxies everything
else to the subprocess.

A recording holds real session data. `--record` connects to the running kernel
as one more pane (the same URL and query, the token as the page's cookie),
sends the ready handshake and nothing else, and writes only under the system
temp directory (private to your user: directory 0700, file 0600), refusing a
path inside a git checkout or through a symlink. Never copy one into the repo;
the tests use synthetic streams. Apps: `feed`, `fleet` (the Outline pane),
`chat`, `timeline`. Only the chat cannot be synthesized: its session frame is
built by `build_session` and is too rich to fake, so record it.

`tests/ui-bench.test.mjs` (`node --test tests/ui-bench.test.mjs`) covers the
tool, including the recording client against a local WebSocket server and the
Handler subprocess's isolation, and replays synthetic feed and timeline streams
in a real browser. The browser tests skip, saying why, when no Chromium (either
playwright's own, `cd vscode-extension && npx playwright install chromium`,
which CI installs so the required check never rides the runner image's
browser, or a system Google Chrome), no `python3` or no built `dist/` is
available; with `ROMP_UI_BENCH_REQUIRE=1` in the environment (CI sets it) that
skip is a failure instead. The replays assert
what holds under any scheduling (frame totals, ordering, the handoff's
accounting); the timing relations the bench measures (a settle margin, a render
outweighing a parse, the CPU throttle's slowdown) are assertions only with
`ROMP_UI_BENCH_TIMING=1`, which a loaded machine can fail and CI does not set;
without it a relation that did not hold is a diagnostic line in the output.

## Test environment

Three things about the test environment are worth knowing, because all have
produced confusing failures:

- The bats suite takes about a minute on Linux and about fifteen on macOS. That
  is expected, not a hang.
- Some tests behave differently depending on whether a `tmux` binary exists on
  the machine, because romp treats "no tmux at all" as headless and falls back
  to file-derived sessions. Tests that care now pin this explicitly; if you add
  one that calls into session liveness, pin it too rather than inheriting the
  machine's state.
- On macOS, run the bats suite with a modern bash (`brew install bash`; bats
  picks it up via `env bash` when `/opt/homebrew/bin` precedes `/bin` on PATH).
  The stock `/bin/bash` 3.2 does not fail a test on a mid-test `[[ ]]`
  assertion — only the last command's status counts — so a stale assertion can
  pass silently for months. Linux CI runs bash 5 and is the arbiter; two
  assertions went stale exactly this way while CI was offline.

## A message listener from another world clones every frame it can see

The pane pages receive the kernel's frames as `message` events on `window`.
Blink hands a listener in the page's own JavaScript world the event's data
object itself, but a listener in another world, a browser extension's content
script, that reads `event.data` receives a structured clone of the whole
object, made synchronously inside the dispatch: 35-46 ms and about 7 MB of
garbage per dispatch of a 7 MB frame in a Chromium probe, against 0 ms for a
direct call. The live `romp perf client` rows show it as a `fed:<type>` share
far above federation's own compute (about 1 ms per frame).

`federation.js` therefore hands its merged frames (`feed`, `tabOrder`, `data`,
`bars`) to the pane's handler by direct call (`window.__rompFed.onFrame`,
through `ui/webview/frame-listener.ts`) and dispatches them on `window` only
when nothing registered. Every other frame still arrives as a `window` event,
so a foreign listener still sees those, and a new frame type that grows large
should go through the registry too.

To check a browser for such a listener before or after a deploy: open DevTools
on the dashboard, pick the feed iframe in the console's context selector, and
time a dispatch of a large frame:

```js
const big = Array.from({ length: 150000 }, (_, i) => ({ i, s: "x" }));
const t = performance.now();
window.dispatchEvent(new MessageEvent("message", { data: big }));
performance.now() - t
```

Under a millisecond means no foreign reader: only same-world listeners saw the
event. Tens of milliseconds means a listener in another world read
`event.data` and paid for the clone. This timing is the detection step.
`getEventListeners(window).message` cannot be, because it is per-world: it
lists only the listeners registered from the world whose context the console
is running in, so in the page's own context it shows romp's listeners and
nothing else, whether or not a content script is present. To see a content
script's listener, switch the console's context selector to that extension's
context (listed under the frame by extension name) and run the enumeration
there. Every romp listener on a kernel pane page comes from that page's pane
bundle (`feed.js` on the feed page, `render.js` on the chat, and so on; the
kernel-served timeline's is its inline boot; under a dev build with source
maps DevTools may show the source file names), and any other URL, in
particular a `chrome-extension://` one, is the foreign listener. With no
foreign listener present, a large `fed:<type>` share in the live rows is not
the clone and needs another explanation before anything is built on this one.
