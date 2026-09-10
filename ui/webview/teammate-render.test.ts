// Native Claude Code TEAMMATE message rendering (the user 2026-07-05): one agent messages another over
// Claude Code's own agent-to-agent channel (not romp's postal bus). It used to render as a blue "you typed
// this" bubble full of coordination JSON. renderTeammate gives it its OWN collapsed card — like the postal
// card in AFFORDANCE (collapse→expand) but deliberately UNLIKE it in look: no per-peer color, no romp swirl,
// no from/to arrow, no colored session chip — so it's tellable apart from a romp-postal message at a glance.
// No jsdom for the chat renderer (the repo convention) → pin the wiring at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
// renderTeammate's function body, isolated so the "must NOT look like postal" asserts can't be satisfied by
// an unrelated part of the file.
const BODY = (RENDER.match(/function renderTeammate\([\s\S]*?\n}\n/) || [""])[0];

test("a kind:'teammate' event carries per-sender blocks and is dispatched to renderTeammate", () => {
  assert.match(RENDER, /kind: "teammate"; blocks: \{ id: string; summary\?: string; body: string \}\[\]/);
  assert.match(RENDER, /if \(ev\.kind === "teammate"\) return renderTeammate\(ev\);/);
  assert.ok(BODY, "renderTeammate is defined");
});

test("it is a TEAMMATE notice — 'teammate <names>' as the source label, two-heads glyph, a KEYED fold (2026-09-08)", () => {
  // the notice-vocabulary pass: the dashed frame said what the label now says; the unkeyed classList.toggle (the
  // exact 2026-07-25 postal bug, still live here) is the builder's openFolds fold now
  assert.match(BODY, /const src = \(fromSub \? "background agent" : "teammate"\) \+ \(names \? " " \+ names : ""\);/);
  assert.match(BODY, /notice\(\{ src, glyph: "teammate", gist: summaryText \|\| fullText, body,/);
  assert.match(BODY, /key: "tm:" \+ \(ev\.uuid \|\| ev\.ts \|\| ""\), cls: "turn-teammate",/);
  assert.doesNotMatch(BODY, /classList\.toggle\("expanded"\)/, "no DOM-only toggle — a kernel push re-collapsed it");
});

test("it is DIFFERENTIABLE from a romp postal card — no color, no swirl, no session chip", () => {
  assert.doesNotMatch(BODY, /makeSessionChip/, "no clickable colored session chip (that's the postal card's language)");
  assert.doesNotMatch(BODY, /setPeerDot/, "no peer working-dot");
  assert.doesNotMatch(BODY, /--peer-bg|--peer-fg/, "no per-peer color chrome");
  assert.doesNotMatch(BODY, /romp-swirl-glyph|romp-logo/, "no romp swirl — this is NOT from romp's postal service");
  assert.doesNotMatch(BODY, /postal-service/, "does not reuse the postal card classes");
});

test("the teammate notice wears the ONE notice skin — no dashed frame, no per-peer colour (2026-09-08)", () => {
  assert.doesNotMatch(CSS, /\.teammate-card|\.teammate-tag|\.teammate-expandable/);
  assert.match(CSS, /\.notice-collapsible:not\(\.notice-open\) > \.notice-body \{ display: none; \}/);
});
