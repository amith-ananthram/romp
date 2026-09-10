import { test } from "node:test";
import assert from "node:assert";
import { parseAgentNotif, notifHead } from "./agent-notif";
import * as fs from "node:fs";
import * as path from "node:path";

// A backgrounded agent's <task-notification>, as it arrives folded into a user turn's reminders (the OUTER
// wrapper already peeled by the kernel's _split_reminders). Synthetic — invented label + result, placeholder
// ids — never real recorded data.
const NOTIF = [
  "<task-id>11111111aaaa2222</task-id>",
  "<tool-use-id>toolu_0abcDEF123</tool-use-id>",
  "<output-file>/tmp/TESTHOST/tasks/11111111aaaa2222.output</output-file>",
  "<status>completed</status>",
  '<summary>Agent "widget audit" came to rest</summary>',
  "<note>A task-notification fires each time this agent comes to rest with no live background children.</note>",
  "<result>Found 3 widgets: **A**, B, C.\n\nA is stale; the rest are fine.</result>",
].join("\n");

test("pulls the agent name, status, and final result out of a task-notification", () => {
  const a = parseAgentNotif(NOTIF);
  assert.ok(a, "a task-notification parses");
  assert.equal(a!.kind, "agent");
  assert.equal(a!.label, "widget audit", "the agent name comes from the summary");
  assert.equal(a!.status, "completed");
  assert.equal(a!.detail, "completed", "an agent's detail is its status word");
  assert.equal(a!.toolUseId, "toolu_0abcDEF123", "the tool-use-id join key is kept");
  assert.match(a!.result, /Found 3 widgets/, "the result is the agent's final message");
  assert.doesNotMatch(a!.result, /task-id|tool-use-id|output-file|<note>/, "internal ids + boilerplate dropped");
});

test("a background command notification parses to a clean label + exit-code detail, not a repeated summary", () => {
  // the shape that made the old card print its summary twice (the user 2026-07-23): no <result>, the detail
  // lives in the command + output the kernel joins in by tool-use-id (ev.taskOutputs), not in this XML.
  const notif = [
    "<task-id>abcabc123</task-id>",
    "<tool-use-id>toolu_9zzz888</tool-use-id>",
    "<output-file>/tmp/TESTHOST/tasks/abcabc123.output</output-file>",
    "<status>completed</status>",
    '<summary>Background command "measure the watch-loop rate" completed (exit code 0)</summary>',
  ].join("\n");
  const a = parseAgentNotif(notif);
  assert.ok(a);
  assert.equal(a!.kind, "command");
  assert.equal(a!.label, "measure the watch-loop rate", "the label is the description, NOT the whole summary");
  assert.equal(a!.detail, "exit 0", "the compact detail is the exit code, read at a glance");
  assert.equal(a!.result, "", "a command has no result field — its detail is command+output");
  assert.equal(a!.toolUseId, "toolu_9zzz888");
});

test("a non-zero exit shows in the detail", () => {
  const a = parseAgentNotif('<task-id>x</task-id><status>completed</status>'
    + '<summary>Background command "flaky check" completed (exit code 2)</summary>');
  assert.equal(a!.detail, "exit 2");
});

// CLI 2.1.263 (the user 2026-09-07): the notification arrives with a fixed preamble paragraph AHEAD of the tag,
// or wrapped in a <system-reminder> with the preamble inside. The kernel peels the wrappers and lifts the
// preamble out; whatever reaches here — the clean inner XML, or the raw preamble-led text on the live tail —
// must still parse to the agent, never to null (null would have left the record a bubble of its own text).
const PREAMBLE = "[SYSTEM NOTIFICATION - NOT USER INPUT]\nThis is an automated background-task event, NOT a message from the user.\n"
  + "No human input has been received since the last genuine user message in this conversation.";

test("a preamble-led notification parses to the same agent card", () => {
  const a = parseAgentNotif(PREAMBLE + "\n\n<task-notification>" + NOTIF + "</task-notification>");
  assert.ok(a);
  assert.equal(a!.kind, "agent");
  assert.equal(a!.label, "widget audit");
  assert.match(a!.result, /Found 3 widgets/);
  assert.doesNotMatch(a!.result, /SYSTEM NOTIFICATION/, "the preamble never leaks into the report");
});

test("a system-reminder-wrapped notification with the preamble inside parses to the agent", () => {
  // the kernel peels <system-reminder> and hands the inner text as one reminder: preamble + notification
  const inner = PREAMBLE + "\n\n<task-notification>" + NOTIF + "</task-notification>";
  const a = parseAgentNotif(inner);
  assert.ok(a);
  assert.equal(a!.kind, "agent");
  assert.equal(a!.toolUseId, "toolu_0abcDEF123");
});

test("the head names WHAT it is before WHICH one, in the user's words", () => {
  // "Background agent finished · widget audit", not the bare "widget audit · completed" (the user 2026-09-07:
  // a report card whose head is just the agent's description reads like a message about that topic)
  assert.equal(notifHead(parseAgentNotif(NOTIF)!), "Background agent finished · widget audit");
  assert.equal(notifHead(parseAgentNotif('<task-id>x</task-id><status>failed</status><summary>Agent "widget audit" came to rest</summary>')!),
               "Background agent failed · widget audit");
  assert.equal(notifHead(parseAgentNotif('<task-id>x</task-id><status>completed</status>'
    + '<summary>Background command "flaky check" completed (exit code 2)</summary>')!),
               "Background command finished · flaky check · exit 2");
  assert.equal(notifHead(parseAgentNotif('<task-id>x</task-id><status>failed</status><summary>background sweep came to rest</summary>')!),
               "Background task failed · background sweep");
});

test("a plain <system-reminder> (no task-id / Agent summary) is NOT an agent card", () => {
  assert.equal(parseAgentNotif("Your context is getting full. Consider /compact."), null);
  assert.equal(parseAgentNotif("<system-reminder>be concise</system-reminder>"), null);
});

test("falls back gracefully when the summary isn't the canonical Agent \"…\" shape", () => {
  // still a task-notification (has a <task-id>), but the summary lacks the Agent "…" quote
  const a = parseAgentNotif('<task-id>22223333</task-id><status>failed</status><summary>background sweep came to rest</summary>');
  assert.ok(a);
  assert.equal(a!.kind, "task");
  assert.equal(a!.label, "background sweep", "strips the 'came to rest' tail for a label");
  assert.equal(a!.status, "failed");
  assert.equal(a!.result, "", "no <result> → empty");
});

// Source pin: the renderer routes a parsed notification to its own card and keeps the rest as a fold.
const RENDER = fs.readFileSync(path.join(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("render.ts splits agent notifications out of the reminders into agent notice cards", () => {
  assert.match(RENDER, /const a = parseAgentNotif\(r\);/);
  // the card is fed the event's taskOutputs so a command can expand to its shell + output tail
  assert.match(RENDER, /turn\.appendChild\(renderAgentNotif\(a, ev\.taskOutputs,/);
  assert.match(RENDER, /else plain\.push\(r\);/);
  // the leftover plain reminders now get their OWN muted notice card too (not the old italic fold)
  // 2026-09-08 (the notice-vocabulary pass): both ride the ONE builder, notice(spec) — the reminders under the
  // SYSTEM source label, an agent report under "background agent" / a command under "background command", the
  // kind carried by the SOURCE label + line glyph rather than a coloured chip (noticeCard is retired)
  assert.match(RENDER, /notice\(\{ src: "system", glyph: "system", gist: `\$\{n\} reminder\$\{n > 1 \? "s" : ""\}`, body,/);
  assert.match(RENDER, /src: a\.kind === "agent" \? "background agent" : "background command",/);
  assert.match(RENDER, /glyph: a\.kind === "agent" \? "agent" : "command",/);
});

test("the card gist never re-prints its body — the head is label+detail, the body is result or command/output", () => {
  // the double-print bug (the user 2026-07-23): the old card put the summary on BOTH the head gist and the
  // body. The head is now the compact "label · detail"; the body is the RESULT (agent) or the command +
  // output tail (command), and is omitted entirely when there is nothing more than the gist.
  // the head is notifHead's "Background agent finished · <label>" since 2026-09-08 (the sourced-notice work): it
  // says what the card IS before which agent — the old `label · detail` pin was retired deliberately
  // 2026-09-08: the head is the builder's GIST slot (notifHead's words), the body is passed only when there is one
  assert.match(RENDER, /gist: notifHead\(a\), meta: \/\^exit \\d\+\$\/\.test\(a\.detail\) \? a\.detail : undefined,/);
  assert.match(RENDER, /body: hasBody \? body : null, key, nested: opts\.nested !== false \}\);/);
  assert.doesNotMatch(RENDER, /p\.textContent = a\.summary/);   // the old "print the summary again" body is gone
  // a flat card (no body) drops the whole body wrapper so there is no stray padding — the builder's rule
  assert.match(RENDER, /if \(body\) \{ const wrap = el\("div", "notice-body"\); wrap\.appendChild\(body\); card\.appendChild\(wrap\); \}/);
});

test("styles: the notice glyph is coloured by the severity rail (info = dim), and the command detail sub-labels exist", () => {
  // 2026-09-08 (the notice-vocabulary pass): the per-variant accent rules (.notice-card-agent / .notice-chip-agent)
  // are retired — a background report is an INFO notice whose kind is its source label + fork glyph; the glyph
  // reads the rail's colour, never a colour of its own
  assert.match(CSS, /\.notice-glyph \{[^}]*color: var\(--notice-rail, var\(--dim\)\)/);
  assert.match(CSS, /\.notice-sev-info\s+\{ --notice-rail: var\(--dim\);/);
  assert.doesNotMatch(CSS, /\.notice-card-agent|\.notice-chip-agent/);
  assert.match(CSS, /\.notice-sub \{/);
});
