// A harness-injected user-role record renders as a labelled notice with its SOURCE, never as the user's bubble
// (the user 2026-09-07: background-agent reports and system notices were wearing their own typed-words bubble).
// The pure head mapping is executed; render.ts wiring is pinned by source. Synthetic values only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { injectedHead, statusWord } from "./injected-source";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the head says the source in the user's terms", () => {
  assert.deepEqual(injectedHead({ kind: "subagent", name: "widget audit", status: "completed" }),
                   { variant: "agent", chip: "agent", head: "Background agent finished · widget audit" });
  assert.equal(injectedHead({ kind: "subagent", name: "widget audit", status: "failed" }).head, "Background agent failed · widget audit");
  assert.equal(injectedHead({ kind: "task", name: "measure the loop rate", status: "completed" }).head,
               "Background command finished · measure the loop rate");
  assert.deepEqual(injectedHead({ kind: "system", label: "Scheduled task" }),
                   { variant: "reminder", chip: "system", head: "System notice · Scheduled task" });
  assert.equal(injectedHead({ kind: "system", label: "System reminder" }).head, "System notice", "the generic label is not repeated");
  assert.deepEqual(injectedHead({ kind: "peer", name: "web" }), { variant: "peer", chip: "peer", head: "From web" });
  assert.equal(injectedHead({ kind: "peer", name: "sub", subagent: true }).chip, "background agent");
  assert.equal(injectedHead({ kind: "peer" }).head, "From another session");
});

test("status words: the harness's 'completed' is the user's 'finished'", () => {
  assert.equal(statusWord("completed"), "finished");
  assert.equal(statusWord(""), "finished");
  assert.equal(statusWord("killed"), "stopped");
  assert.equal(statusWord("failed"), "failed");
});

test("render.ts routes a sourced non-human event to renderInjected BEFORE the sender classifier — never a bubble", () => {
  // the branch sits after the romp system notice and before senderKind, so the T261 hidden-unit guard and
  // the rail dot below it never see a sourced record
  const i = RENDER.indexOf("if (ev.source && !ev.human) return renderInjected(ev);");
  assert.ok(i > 0, "the sourced branch exists");
  const rompSys = RENDER.indexOf("if ((ev as any).rompSystem && ev.md) {");
  assert.ok(i > rompSys, "after the romp system notice");
  // the user branch's own classifier call — senderKind is also called elsewhere (rail notches, fork spots)
  assert.ok(i < RENDER.indexOf("const kind = senderKind(ev);", rompSys), "ahead of the classifier");
  const start = RENDER.indexOf("function renderInjected(");
  const fn = RENDER.slice(start, RENDER.indexOf("\n}\n", start));   // the function body alone
  assert.ok(start > 0 && start < RENDER.indexOf("function renderEventInner("), "renderInjected is defined before the event renderer");
  assert.doesNotMatch(fn, /user-bubble|romp-bubble|user-note/, "no bubble class of any color");
  // 2026-09-08 (the notice-vocabulary pass): the ONE builder; the source kind maps to the vocabulary's label + glyph
  // (injectedSrc), the head stays injected-source.ts's words
  assert.match(fn, /const \{ label, glyph \} = injectedSrc\(src\);\s*\n\s*return notice\(\{ src: label, glyph, gist: h\.head, body, key \}\);/, "the notice family");
  assert.match(fn, /renderAgentNotif\(notifs\[0\]\.a, ev\.taskOutputs, key, \{ nested: false, preamble: ev\.preamble \}\)/,
               "one notification alone → the agent card on its own rail, preamble in its fold");
  assert.match(fn, /appendHarnessNote\(body, ev\.preamble\)/, "the CLI's preamble is one click away, never the message");
});

test("the agent card's head is in the user's terms and can stand on its own rail", () => {
  assert.match(RENDER, /gist: notifHead\(a\),/);   // the head is the builder's gist slot (2026-09-08)
  assert.match(RENDER, /nested: opts\.nested !== false/);
  // a typed prompt that arrived WITH a notification keeps the nested card under its bubble (default nested)
  assert.match(RENDER, /if \(a\) turn\.appendChild\(renderAgentNotif\(a, ev\.taskOutputs, ev\.uuid \? "agn:" \+ ev\.uuid \+ ":" \+ i : undefined\)\);/);
});

test("the teammate card labels a background subagent's message as such", () => {
  assert.match(RENDER, /const src = \(fromSub \? "background agent" : "teammate"\) \+ \(names \? " " \+ names : ""\);/);   // the notice SOURCE label (2026-09-08)
  assert.match(RENDER, /if \(!ids\.length && ev\.source && ev\.source\.name\) ids\.push\(ev\.source\.name\);/);
});

test("a peer's record wears the peer source (envelope glyph, its name as the label) — no dashed variant (2026-09-08)", () => {
  // the notice-vocabulary pass: one skin; the source label + glyph carry the kind (injectedSrc)
  assert.match(RENDER, /if \(src\.kind === "peer"\) return \{ label: src\.subagent \? "background agent" : \(src\.name \|\| "peer"\), glyph: "peer" \};/);
  assert.match(RENDER, /if \(src\.kind === "subagent"\) return \{ label: "background agent", glyph: "agent" \};/);
  assert.match(RENDER, /if \(src\.kind === "task"\) return \{ label: "background command", glyph: "command" \};/);
  assert.doesNotMatch(CSS, /\.notice-card-peer|\.notice-chip-peer|\.notice-dot-peer/);
});
