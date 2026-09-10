// T261 (the user 2026-09-08): a blue rail dot rendered with NO message beside it. The live chat frame for the
// reported session carried user-kind events whose `md` was empty (records the kernel had stripped to nothing —
// romp markers, injected reminders); the user branch of renderEventInner appended dot("user") before it checked
// `ev.md || hasImgs`, so the dot stood alone. Rule: a rail dot never stands alone — an event with nothing to
// show renders a zero-height unit and no dot. Executed decision + render.ts wiring pins, red on main. Synthetic
// values only.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { userTurnShows } from "./user-turn-content";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("a user event shows only when it has text or images", () => {
  assert.equal(userTurnShows({ md: "" }), false, "stripped to nothing → nothing to show");
  assert.equal(userTurnShows({ md: "   \n " }), false, "whitespace is nothing");
  assert.equal(userTurnShows({}), false);
  assert.equal(userTurnShows({ md: null }), false);
  assert.equal(userTurnShows({ md: "hello" }), true);
  assert.equal(userTurnShows({ md: "", images: ["a.png"] }), true, "an image-only message shows its thumbnails");
  assert.equal(userTurnShows({ md: "", images: [] }), false);
});

test("render.ts: the user branch returns a hidden zero-height unit BEFORE the rail dot when there is nothing to show", () => {
  assert.match(RENDER, /import \{ userTurnShows \} from "\.\/user-turn-content";/);
  const m = RENDER.match(/const kind = senderKind\(ev\);([\s\S]*?)turn\.appendChild\(dot\(romp \? "romp" : tagged \? "tag" : injected \? "ring" : "user"\)\);/);
  assert.ok(m, "the user branch from the classifier to the rail dot");
  const between = m![1];
  assert.match(between, /if \(!userTurnShows\(ev\)\) \{\s*\n\s*const hid = el\("div", "turn turn-user turn-user-empty"\);\s*\n\s*hid\.style\.display = "none";\s*\n\s*return hid;\s*\n\s*\}/,
               "nothing to show → a hidden unit, returned before any dot is appended");
  // the guard precedes the turn element itself, so no dot, no follow-up header and no bubble are ever built for it
  assert.ok(between.indexOf("if (!userTurnShows(ev))") < between.indexOf('const turn = el("div", "turn turn-user"'));
  // the interrupt marker and the romp system notice keep their own marks (they return before this guard)
  assert.match(RENDER, /if \(\(ev as any\)\.interruptMarker\) \{[\s\S]{0,700}?return notice\(\{ src: "session", glyph: "session", gist: interruptGist\(cause\)/);   // a slim notice since 2026-09-08
  assert.match(RENDER, /if \(\(ev as any\)\.rompSystem && ev\.md\) \{/);
});
