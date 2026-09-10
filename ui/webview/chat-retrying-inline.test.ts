// API-retry visibility in the chat (the user 2026-07-08): a session stalled on an api_retry backoff (the CLI
// retrying a rate-limited / overloaded request) used to be visible ONLY as the amber tab border, with nothing
// in the chat ("the border says retrying but the chat shows no sign"). Now a transient {kind:"retrying"}
// element — the loader dots + an AMBER "API retrying…" line with the live attempt count — renders in the flow
// (a sibling of compacting/reconnecting), and once output resumes a persistent {kind:"retried"} "Recovered
// after N retries" note is left where it recovered. Source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the transient retrying + persistent retried events each have their own ChatEvent kind + dispatch", () => {
  assert.match(RENDER, /kind: "retrying"; retries\?: number; info\?: /);
  assert.match(RENDER, /kind: "retried"; retries: number; ts\?: string; uuid\?: string/);
  assert.match(RENDER, /ev\.kind === "retrying"\) return renderRetrying\(ev\)/);
  assert.match(RENDER, /ev\.kind === "retried"\) return renderRetried\(ev\)/);
});

test("renderRetrying is a live API notice: loader dots in the glyph slot, the attempt count as meta", () => {
  // 2026-09-08 (the notice-vocabulary pass): the ONE builder; the amber is the retry severity (rail + dot + dots)
  const body = RENDER.split("function renderRetrying(")[1].split("\nfunction ")[0];
  assert.match(body, /glyph: noticeLiveGlyph\(metaDots\(\)\), sev: "retry", gist: "retrying", meta, acts: \[stop\], body, live: true,/);
  assert.match(body, /cls: "turn-retrying",/);
  assert.match(body, /`attempt \$\{n\}` \+ \(info\.max \? ` of \$\{info\.max\}` : ""\)/);
});

test("renderRetrying surfaces the api_retry payload's own detail (the user 2026-07-10)", () => {
  const body = RENDER.split("function renderRetrying(")[1].split("\nfunction ")[0];
  assert.ok(body, "the renderRetrying slice is anchored");
  assert.match(body, /info\.attempt \|\| ev\.retries/, "payload attempt number outranks the local count");
  assert.match(body, /` of \$\{info\.max\}`/, "the retry budget shows when the payload names it");
  // 2026-09-08: the error behind the backoff is the notice's folded BODY; the request id rides the one tooltip
  assert.match(body, /body = el\("div", "notice-md"\);/);
  assert.match(body, /`HTTP \$\{info\.status\}`/);
  assert.match(body, /const tipText = \[msg, info\.requestId \? `request \$\{info\.requestId\}` : ""\]/);
  assert.match(body, /if \(tipText\) setTip\(body, tipText\);/);
});

test("the next-try countdown TICKS every second — it is not frozen at render time (the user 2026-07-24)", () => {
  // the bug: the countdown re-derived only on re-render, so it sat at "next try in ~3s" for the whole
  // backoff. A number that never moves reads as broken. The epoch now rides a data attr and a 1s tick
  // rewrites the span, exactly like the API-error card's countdown.
  const body = RENDER.slice(RENDER.indexOf("function renderRetrying("), RENDER.indexOf("// The next-attempt countdown's text"));
  assert.match(body, /el\("span", "retrying-countdown"\)/);
  assert.match(body, /cd\.dataset\.retryAt = String\(info\.retryAt\)/, "the authoritative epoch rides the element");
  assert.match(RENDER, /function retryingTick\(\): void \{/);
  assert.match(RENDER, /querySelectorAll\("\.retrying-countdown"\)/);
  assert.match(RENDER, /cd\.textContent = retryingCountdownText\(at\)/);
  // ONE 1s timer drives both countdowns — no second scheduler
  assert.match(RENDER, /setInterval\(\(\) => \{ apiRetryTick\(\); retryingTick\(\); \}, 1000\)/);
  assert.doesNotMatch(RENDER, /next try in ~\$\{waitS\}s/, "the frozen render-time countdown is gone");
});

test("a past-due countdown reads 'retrying now', never a stuck 0s or a negative — in the ONE duration format", () => {
  // 2026-09-08: "next try in 7s" through duration.ts durLabel (the audit found six span formats); no leading dash —
  // the countdown is a META slot now, not a sentence fragment glued to the head
  const fn = RENDER.split("function retryingCountdownText(")[1].split("\n}")[0];
  assert.match(fn, /s > 0 \? `next try in \$\{durLabel\(s\)\}` : "retrying now…"/);
});

test("the card carries a Stop control that interrupts the stalled turn (the user 2026-07-24)", () => {
  const body = RENDER.split("function renderRetrying(")[1].split("\nfunction ")[0];
  // 2026-09-08: a word button on the notice vocabulary (noticeAct: data-act only, no per-render listener)
  assert.match(body, /const stop = noticeAct\("Stop retrying", "stopRetrying",/);
  assert.match(RENDER, /function noticeAct\(label: string, act: string, tip\?: string\): HTMLButtonElement \{[\s\S]{0,300}?b\.dataset\.act = act;/);
  assert.match(RENDER, /stopRetrying: \(el\) => \{/);
  assert.match(RENDER, /b\.textContent = "Stopping…"/);
});

test("the stop control wears the ONE notice button dress, and the countdown's meta slot uses tabular digits", () => {
  // 2026-09-08: .notice-act = the button vocabulary (sm box, T141 rest, pattern-A hover); meta = tabular-nums
  assert.match(CSS, /\.notice-act \{[^}]*color: var\(--dim\)/);
  assert.match(CSS, /\.notice-act \{[^}]*border: 1px solid var\(--card-border\)/);
  assert.match(CSS, /\.notice-act:disabled \{[^}]*cursor: default/);
  assert.match(CSS, /\.notice-meta \{[^}]*font-variant-numeric: tabular-nums/);
  assert.doesNotMatch(CSS, /\.retrying-stop|\.retrying-countdown \{/);
});

test("the error detail is the notice BODY — the one 0.92em body rung, no amber text", () => {
  // 2026-09-08: one size per role (notice-vocab.test.ts); the retrying hue lives ONLY in its token declaration
  assert.match(CSS, /\.notice-body \{[^}]*font-size: 0\.92em/);
  const noComments = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.equal((noComments.match(/#e67e22/gi) || []).length, 1, "the amber appears once: --st-retrying-bg's dark value");
  assert.doesNotMatch(CSS, /\.retrying-err/);
});

test("renderRetried is a slim API notice — 'recovered after N retries' (pluralized), static", () => {
  const body = RENDER.split("function renderRetried(")[1].split("\nfunction ")[0];
  // 2026-09-08: the gist string lives in retriedGist, shared with compact mode's group head (noticeBrief)
  assert.match(body, /notice\(\{ src: "API", glyph: "retry", gist: retriedGist\(ev\.retries \|\| 0\), cls: "turn-retried",/);
  assert.match(RENDER, /function retriedGist\(n: number\): string \{ return `recovered after \$\{n\} \$\{n === 1 \? "retry" : "retries"\}`; \}/);
  assert.doesNotMatch(body, /metaDots/, "the recovered note is static — no loader animation");
});

test("the retrying notice is tinted the retrying STATUS token on its rail, dot and dots — the tab ring's hue", () => {
  // 2026-09-08: the hue is --st-retrying-bg (raw in seven rules before); severity is rail + dot + glyph, never text
  assert.match(CSS, /\.notice-sev-retry\s+\{ --notice-rail: var\(--st-retrying-bg\);\s+--notice-dot: var\(--st-retrying-bg\); \}/);
  assert.match(CSS, /\.notice-glyph \.meta-dots i \{ background: var\(--notice-rail, var\(--accent\)\); \}/);   // loader dots take the rail's colour
  assert.match(CSS, /\.tab\.tab-retrying \{ --state: var\(--st-retrying-bg\); \}/);                    // same status token as the border
});

test("the recovered note is the INFO severity (dim rail), not amber — a resolved historical marker", () => {
  assert.match(CSS, /\.notice-sev-info\s+\{ --notice-rail: var\(--dim\);/);
  assert.match(CSS, /\.notice-gist \{[^}]*color: var\(--fg\)/);
});
