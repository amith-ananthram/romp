// A mid-turn safeguards model swap must be visible in the chat, never silent (the user 2026-08-03:
// fable's safeguards flagged a message, the CLI silently retried on opus, and nothing in the chat said
// so). The kernel emits {kind:"modelFallback"} from the transcript's system/model_refusal_fallback
// record. T279 filed it as a SOURCED notice: the "safeguards" source, a one-line head naming the models
// and the refusal category, and the API's explanation (plus the CLI's own line) folded one click away —
// never as the user's bubble and never in the red API-error dress. The 2026-09-08 vocabulary pass builds
// that notice through the one builder, notice(spec), in the WARN severity. render.ts has import-time DOM
// side effects → source pins (rewind-delete.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

function renderer(): string {
  const fn = RENDER.slice(RENDER.indexOf("function renderModelFallback"));
  return fn.slice(0, fn.indexOf("\n}"));
}
function gistFn(): string {
  return RENDER.split("function modelFallbackGist(")[1].split("\n}")[0];
}

test("the modelFallback kind is dispatched to its renderer", () => {
  assert.match(RENDER, /if \(ev\.kind === "modelFallback"\) return renderModelFallback\(ev\);/);
  // the union carries the payload the kernel sends: raw ids, the CLI's line, the refusal's category,
  // the API's explanation and the scope (session model swapped vs. one local reply)
  for (const field of ["category", "explanation", "scope"]) {
    assert.match(RENDER, new RegExp('kind: "modelFallback";[^}]*' + field + '\\?: string'), field + " is typed on the event");
  }
});

test("the notice is THE notice: the safeguards SOURCE in the warn severity, through notice()", () => {
  // 2026-09-08 (the notice-vocabulary pass): T279's "refusal" variant of noticeCard became a spec for the one
  // builder — the chip is the source label, the heads-up amber is the warn severity's rail + dot, and the
  // per-variant dress (.notice-card-refusal / -chip / -dot) has no rule: severity IS the dress
  const body = renderer();
  assert.match(body, /notice\(\{ src: "safeguards", glyph: "api", sev: "warn", gist, body, tip: gist,/);
  assert.doesNotMatch(RENDER, /function noticeCard\(|type NoticeVariant/, "the variant union retired with the old builder");
  // never a red/blocked dress: a swap is a warning about provenance, not a failure of the turn
  assert.doesNotMatch(body, /apierror|gaveup/);
  assert.doesNotMatch(RENDER, /"modelswap-(line|text|body)"|turn-modelswap/, "the old slim rail line's classes are gone");
});

test("the head names both models via prettyModel and the refusal category; the compact run's brief shares it", () => {
  // the gist string lives in modelFallbackGist, shared with compact mode's group head (noticeBrief)
  const gist = gistFn();
  assert.match(gist, /prettyModel\(ev\.from\)/);
  assert.match(gist, /prettyModel\(ev\.to\)/);
  assert.match(gist, /safeguards flagged this message/);
  assert.match(gist, /ev\.category/);
  // 'local' scope: only that reply came from the fallback model — the session's model is unchanged
  assert.match(gist, /ev\.scope === "local"/);
  assert.match(gist, /switched to/);
  assert.match(renderer(), /const gist = modelFallbackGist\(ev\);/);
  assert.match(RENDER, /if \(ev\.kind === "modelFallback"\) return \{ src: "safeguards", glyph: "api", gist: modelFallbackGist\(ev\) \};/);
});

test("the API's explanation and the CLI's own line fold under the head, keyed by the record's uuid", () => {
  const body = renderer();
  assert.match(body, /ev\.explanation/);
  assert.match(body, /p\.textContent = ev\.md;/, "the CLI's verbatim line still rides along — never paraphrased chrome");
  // keyed through the builder (openFolds "notice:mswap:<uuid>"), toggled by the delegate
  assert.match(body, /key: ev\.uuid \? "mswap:" \+ ev\.uuid : undefined/, "fold keyed by the record's uuid");
  const nc = RENDER.split("function notice(spec: NoticeSpec)")[1].split("\nfunction ")[0];
  assert.match(nc, /applyFold\(card, "notice-open", fkey\)/);
  assert.match(RENDER, /noticetoggle: \(el\) => \{[\s\S]{0,400}?rememberFold\(card, "notice-open", el\.dataset\.nkey \|\| undefined\);/);
});

test("a truncated head is recoverable: the full head is the hover, and the fold restates the swap", () => {
  // progressive disclosure never dead-ends: a narrow pane ellipsizes the head from the right, which cuts
  // exactly the swap and the category the notice exists to surface (review, 2026-09-09). The hover rides
  // the ONE tooltip (setTip, through the builder's `tip`), never a native title (the vocabulary rule).
  const body = renderer();
  assert.match(body, /tip: gist,/);
  assert.doesNotMatch(body, /setAttribute\("title"|\.title = /, "no native title");
  assert.match(body, /el\("div", "refusal-swap"\)/);
  assert.match(CSS, /\.refusal-swap \{/);
  assert.doesNotMatch(RENDER, /Slim rail line in the warning voice/, "the retired treatment's comment is gone too");
});

test("the body is hidden until expanded, in the one notice family; the warn is the rail's colour", () => {
  assert.match(CSS, /\.notice-collapsible:not\(\.notice-open\) > \.notice-body \{ display: none; \}/);
  assert.match(CSS, /\.notice-sev-warn\s+\{ --notice-rail: var\(--warn\);/);
  assert.match(CSS, /\.notice-prose \{ white-space: pre-wrap;/);   // the CLI's text keeps its line breaks
  assert.match(CSS, /\.refusal-cli-line \{ color: var\(--dim\); \}/);   // the CLI's own line, dimmed under the API's words
  assert.doesNotMatch(CSS, /\.modelswap-(line|text|body)/, "the retired line's rules are gone");
  assert.doesNotMatch(CSS, /\.notice-(card|chip|dot)-refusal/, "no per-kind dress: the severity tokens carry the amber");
});
