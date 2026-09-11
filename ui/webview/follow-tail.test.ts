// T262 (the user 2026-09-08, "jumped up slightly on my scroll in many, many sessions"): appendActive pinned a
// reader within the then 80 px follow threshold to the bottom on EVERY frame — a status-only chatTail included — so
// wheeling up from the tail of a working session snapped back within the first 80 px, over and over. The
// two-kernel harness reproduced it: a stop 60 px above the bottom moved 60 px to the bottom in a quiet window
// with scrollHeight unchanged (compact mode), and both modes snapped on the next frame. Rule: follow the tail
// only when there is something new to follow — the content's height changed — or when already at the very
// bottom (a no-op pin). Executed decision + render.ts wiring pins, red on the previous render.ts.
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { followTail } from "./scroll-keep";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the harness case: 60 px above the bottom, a frame that adds nothing → no snap; new content → follow", () => {
  assert.equal(followTail(60, 145219, 145219), false, "status-only tail: the reader stays 60 px up");
  assert.equal(followTail(60, 145219, 145254), true, "35 px of new content: follow the tail (the designed threshold)");
  assert.equal(followTail(60, 145219, 144945), true, "a height change either way is new information to follow");
  assert.equal(followTail(0, 145219, 145219), true, "at the very bottom the pin is a no-op and keeps the tail visible");
  assert.equal(followTail(2, 100, 100), true, "sub-pixel slack counts as at the bottom");
  assert.equal(followTail(3, 100, 100), false, "three pixels up with nothing new: no write");
});

test("render.ts appendActive measures before the rebuild and pins only when followTail says so", () => {
  assert.match(RENDER, /import \{ followReader, keepPlaceAcrossShow, followTail, atBottomDist, followBoxBelow, followTailShrink, reshowStick \} from "\.\/scroll-keep";/);
  const m = RENDER.match(/^function appendActive\(\) \{([\s\S]*?)\n\}/m);
  assert.ok(m, "appendActive");
  const body = m![1];
  assert.match(body, /const heightBefore = content\.scrollHeight;\n\s*const distBefore = heightBefore - before - content\.clientHeight;/);
  assert.ok(body.indexOf("const distBefore") < body.indexOf("syncView(activeId, stick);"), "measured BEFORE the rebuild");
  assert.match(body, /if \(stick && followTail\(distBefore, heightBefore, content\.scrollHeight\)\) writeScroll\(content, content\.scrollHeight, "append-stick", true\);/);
  assert.match(body, /else if \(stick\) \{ \/\* near the bottom, nothing new: the reader stays where they are \*\/ \}/);
  // the scrolled-up path is untouched: anchor restore, raw fallback
  assert.match(body, /else if \(!\(v && restoreScrollAnchor\(content, v, anchor\)\)\) writeScroll\(content, before, "append-raw"\);/);
});
