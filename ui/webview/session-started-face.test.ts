// The card face for work a session started on its own (T319, the user 2026-09-10): such work is nested under
// the goal it ran in at mint time and by the feed's read-side heal, so a root card for it is the exception (its
// parent gone); when one shows, the face says what it is and why in one line, never posing as something the
// user asked for. The rule lives in feed.ts's updateAskCard; these pins hold its shape.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(process.cwd(), "..");
const FEED = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.ts"), "utf8");

test("the card item and the tree node carry the session-started records the kernel ships", () => {
  assert.match(FEED, /interface AskItem \{\n  sessionStarted\?: \{ why: string; parent: string \| null \} \| null;/, "the card's face record");
  assert.match(FEED, /interface AskTreeNode \{\n  born\?: \{ kind: string; via: string; why: string; healed\?: boolean \} \| null;/, "the tree row's why");
});

test("a session-started root says so in one line on the distill line, yielding to a real takeaway", () => {
  const face = FEED.indexOf("if (it.sessionStarted && !distillShown && !(isJudgeAuth && it.blocked)) {");
  assert.ok(face > 0, "shown only when no takeaway or decision brief occupies the line, and never over a judge-auth explanation");
  const sections = FEED.indexOf("applySections(a, it, !!distillShown);");
  assert.ok(sections > 0 && face > sections, "set AFTER applySections, whose no-takeaway branch hides the distill line the face borrows");
  assert.ok(FEED.includes('"Started by the session while working on \\u201c" + ss.parent + "\\u201d: "'), "names the parent request when known");
  assert.ok(FEED.includes('"Started by the session on its own: "'), "and says so plainly when none is known");
});
