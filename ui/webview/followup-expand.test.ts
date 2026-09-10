// Expandable ↩ Follow-up header (the user 2026-07-01): the chat strips romp's injected `> goal context`
// quote from a follow-up bubble for cleanliness, but the strip is display-only — the header now carries a
// ▸ disclosure that expands a muted block showing exactly what rode along with the message (ev.fuCtx, the
// FULL quote from the kernel's _split_followup). Applies to landed user turns AND pending queued messages.
// No jsdom for this renderer, so pin the wiring at source (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the follow-up events carry the stripped quote (fuCtx) end to end", () => {
  // typed on both event shapes — a queued follow-up must expand exactly like a landed one
  assert.match(RENDER, /kind: "user";[^\n]*fuCtx\?: string;/);
  assert.match(RENDER, /kind: "queued"; texts: \{ md: string; followUp\?: boolean; goal\?: string; fuCtx\?: string;/);
  // both call sites hand it to the header, each with a stable expansion key
  assert.match(RENDER, /followUpHeader\(ev\.goal, ev\.fuCtx, ev\.uuid \? "u:" \+ ev\.uuid : undefined\)/);
  assert.match(RENDER, /followUpHeader\(t\.goal, t\.fuCtx, t\.idx !== undefined \? "q:" \+ t\.idx : undefined\)/);
});

test("the header is click-expandable and shows the injected context", () => {
  assert.match(RENDER, /function followUpHeader\(goal\?: string, ctx\?: string, key\?: string\): HTMLElement/);
  assert.match(RENDER, /el\("span", "followup-tri"\)/);
  assert.match(RENDER, /el\("div", "followup-ctx"\)/);
  // context is set as textContent (never innerHTML — the quote is data, not markup)
  assert.match(RENDER, /box\.textContent = ctx;/);
  assert.match(RENDER, /h\.classList\.add\("followup-expandable"\);/);
  // a header with no context stays the plain non-clickable tag (no dead affordance)
  assert.match(RENDER, /if \(!ctx \|\| !k\) return h;/);
});

test("expansion state survives the chat's re-renders — in openFolds, toggled through the delegate (2026-09-08)", () => {
  // the notice-vocabulary pass: fuExpanded (its own Set) and h.onclick (a per-node listener the tail rebuild
  // destroyed mid-press) are gone — the ONE fold store, the ONE click path
  assert.match(RENDER, /applyFold\(wrap, "expanded", "fu:" \+ k\);/);
  assert.match(RENDER, /h\.dataset\.act = "futoggle";\s*\n\s*h\.dataset\.nkey = "fu:" \+ k;/);
  assert.match(RENDER, /futoggle: \(el\) => \{[\s\S]{0,200}?rememberFold\(wrap, "expanded", el\.dataset\.nkey \|\| undefined\);/);
  assert.doesNotMatch(RENDER.replace(/\/\/[^\n]*/g, ""), /fuExpanded|h\.onclick/);
  assert.match(CSS, /\.followup-wrap:not\(\.expanded\) > \.followup-ctx \{ display: none; \}/);
  assert.match(CSS, /\.followup-wrap\.expanded \.followup-tri::before \{ content: "▾"; \}/);
});

test("the context block styling exists and uses the accent variable, not a hardcoded hex", () => {
  assert.match(CSS, /\.followup-ctx \{/);
  assert.match(CSS, /\.followup-wrap \{ display: contents; \}/);
  assert.match(CSS, /\.followup-tag\.followup-expandable \{ cursor: pointer; \}/);
  const block = CSS.slice(CSS.indexOf(".followup-ctx {"));
  assert.match(block.slice(0, 300), /var\(--accent\)/);
  assert.doesNotMatch(block.slice(0, 300), /#9cd2ff/i);
});
