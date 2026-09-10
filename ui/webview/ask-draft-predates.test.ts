// A message you'd already written when an AskUserQuestion arrived got silently consumed as the ANSWER to it
// (the user 2026-07-16): the composer doubles as the picker's "add your own answer" field, and it routed ANY
// send that way regardless of when the text was typed. The question then went unanswered and the message was
// framed as something it wasn't.
//
// The rule: text that already existed when the question arrived CANNOT be an answer to a question you hadn't
// seen yet — so ⏎ sends it as a normal message. No new queueing machinery is needed for "it waits": a picker
// means the session is blocked, so a normal send necessarily queues behind the answer. Two real event stamps
// (draft-started vs ask-arrived), never a time heuristic. render.ts has import-time DOM side effects → source
// pins + an executed replica of the decision.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("both event stamps exist: when the question arrived, when this draft began", () => {
  assert.match(RENDER, /const askArrivedAt = new Map<string, number>\(\);/);
  assert.match(RENDER, /const draftStartedAt = new Map<string, number>\(\);/);
  // the ask stamps on ARRIVAL only — a re-render of the same open question must not reset it
  assert.match(RENDER, /if \(!liveAsks\.has\(id\)\) askArrivedAt\.set\(id, Date\.now\(\)\);/);
  assert.match(RENDER, /function clearLiveAsk\(id: string\) \{\s*\n\s*askArrivedAt\.delete\(id\);/);
  // the draft stamps on empty→non-empty; emptying the box ends it so the next keystroke starts a fresh one
  assert.match(RENDER, /if \(ta\.value\) \{ if \(!had\) draftStartedAt\.set\(activeId, Date\.now\(\)\); drafts\.set\(activeId, ta\.value\); \}/);
  assert.match(RENDER, /else \{ draftStartedAt\.delete\(activeId\); drafts\.delete\(activeId\); \}/);
});

test("a draft that predates the question takes the composer OUT of answer mode", () => {
  assert.match(RENDER, /function draftPredatesAsk\(id: string\): boolean \{/);
  assert.match(RENDER, /return started != null && arrived != null && started < arrived;/);
  assert.match(RENDER, /if \(draftPredatesAsk\(activeId\)\) return null;/);
});

test("every send path ends the draft, and a send re-arms the picker's claim on the empty box", () => {
  const clears = RENDER.match(/draftStartedAt\.delete\(activeId\)/g) || [];
  assert.ok(clears.length >= 4, "cleared on the ask/rewind/plain sends + the emptied box, got " + clears.length);
  // once the box is empty again the picker takes it back → just type the answer
  assert.match(RENDER, /clearBox\(\);[^\n]*\n\s*\/\/ The box is empty again[\s\S]*?setComposerAskMode\(\);/);   // clearBox: the composer's one clear path (composer-mention-pane.test.ts)
  // and a mode flip while typing repaints the box's own cue (the "answering" tint)
  assert.match(RENDER, /if \(had !== draftStartedAt\.has\(activeId\)\) setComposerAskMode\(\);/);
});

test("while a question is pending the queued header says the message goes AFTER the answer", () => {
  assert.match(RENDER, /const pendingAsk = !!activeId && liveAsks\.has\(activeId\);/);
  assert.match(RENDER, /\(pendingAsk \? " · sends after you answer" : ""\)/);
});

// executed replica of the routing decision
test("the composer answers the picker only for text typed AFTER the question arrived", () => {
  const answers = (started: number | undefined, arrived: number | undefined) => {
    const predates = started != null && arrived != null && started < arrived;
    return !predates;   // (caller has already established a picker is live)
  };
  // the exact bug: draft at t=100, question lands at t=200 → the draft is NOT an answer
  assert.equal(answers(100, 200), false, "text written before the question → sends as a message");
  // typed after seeing the question → it IS the answer (the 2026-07-09 coupling, unchanged)
  assert.equal(answers(300, 200), true, "text written after the question → answers the picker");
  // no draft under way (you type fresh into an empty box) → the picker keeps the box
  assert.equal(answers(undefined, 200), true, "no draft stamp → picker keeps the box");
  // a draft with no live question is moot, but the fallback must not claim it predates
  assert.equal(answers(100, undefined), true, "no ask stamp → we can't claim it predates");
});
