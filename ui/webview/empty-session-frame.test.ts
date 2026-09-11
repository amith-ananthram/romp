// T249b (the user 2026-09-07): the recorded scroll snap's frame path. A kernel whose read of a transcript failed
// for one pusher cycle sent a full `session` frame with `events: []` for a session with content; render.ts upsert
// took the empty array as the new transcript (a placeholder flash), and the content frame a cycle later was a
// FIRST build that re-landed the reader through the full-show route. The kernel now keeps its previous build
// (tests/test_chat_empty_build_guard.py); this is the pane's half: a frame that would take a held transcript from
// content to nothing is status-shaped, never a wipe, and is filed once per session in the client-diag journal.
// Executed rule + render.ts wiring pins, red on the previous render.ts. Synthetic ids only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { keepResidentEvents } from "./frame-merge";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("an empty events array for a held transcript keeps the resident events; everything else takes the frame as before", () => {
  const held = [{ uuid: "11111111-2222-4333-8444-0000000002a1" }, { uuid: "11111111-2222-4333-8444-0000000002a2" }];
  assert.equal(keepResidentEvents(held, []), true, "content → nothing is a failed read, not a wipe");
  assert.equal(keepResidentEvents(held, [{ uuid: "x" }]), false, "a frame with events replaces the transcript");
  assert.equal(keepResidentEvents(held, undefined), false, "no events field: the caller already keeps the resident events");
  assert.equal(keepResidentEvents([], []), false, "a session with nothing yet stays empty");
  assert.equal(keepResidentEvents(null, []), false, "a brand-new session with an empty frame is genuinely empty");
});

test("render.ts upsert keeps the held events, head window and all, and files the frame once per session", () => {
  assert.match(RENDER, /import \{ keepResidentEvents \} from "\.\/frame-merge";/);
  const m = RENDER.match(/^function upsert\(msg: any\) \{([\s\S]*?)\n\}/m);
  assert.ok(m, "upsert");
  const body = m![1];
  assert.match(body, /const kept = keepResidentEvents\(prev \? prev\.events : null, msg\.events\);/);
  assert.match(body, /let events: ChatEvent\[\] = kept && prev \? prev\.events : \(msg\.events \|\| \(prev \? prev\.events : \[\]\)\);/);   // `let`: a proto-2 frame overlapping the held run merges into it (T323 stage 4b)
  assert.match(body, /headFrom: kept && prev \? prev\.headFrom : \(msg\.headFrom \?\? 0\),/);
  assert.match(body, /headTotal: kept && prev \? prev\.headTotal : \(msg\.proto === 2 \? \(mergedRun && prev\?\.headKnown \? events\.length : \(msg\.headTotal \?\? null\)\) : \(msg\.headTotal \?\? events\.length\)\),/);   // proto 2: null until the head is known (T323 stage 4b)
  // the decision is made BEFORE forked/firstBuild read msg.events: an empty array is neither a fork nor a first build
  assert.ok(body.indexOf("const kept = keepResidentEvents(") < body.indexOf("const forked = "));
  assert.ok(body.indexOf("const kept = keepResidentEvents(") < body.indexOf("const firstBuild = "));
  // loud, once: the client-diag row names the session and what it held
  assert.match(body, /what: "empty-session-frame", data: \{ id: msg\.id, held: prev\.events\.length \}/);
  assert.match(RENDER, /const emptyFrameDiagSent = new Set<string>\(\);/);
});
