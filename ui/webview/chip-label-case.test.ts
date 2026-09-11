// Status chips read as sentence case, not ALL CAPS (the user 2026-07-03): "Working", "Ready",
// "Blocked", "Compacting", … — first letter capitalized, the rest lowercase (acronyms like "API"
// stay). Pins the CHIP_LABEL map + its fallback, which live in status-chip.ts since T322b (the bar under the
// transcript and the tag overview's rows import the one map), and that render.ts keeps no map of its own.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const W = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");
const R = W("render.ts");
const C = W("status-chip.ts");

test("CHIP_LABEL uses sentence case, never ALL-CAPS status words", () => {
  const m = C.match(/export const CHIP_LABEL[\s\S]*?\};/);
  assert.ok(m, "CHIP_LABEL exists");
  const map = m![0];
  assert.match(map, /working: "Working"/);
  assert.match(map, /ready: "Ready"/);
  assert.match(map, /awaiting: "Blocked"/);
  assert.match(map, /compacting: "Compacting"/);
  assert.match(map, /idle: "Idle"/);
  assert.match(map, /closed: "Closed"/);
  // no bare ALL-CAPS status word survives (the acronym "API" is allowed)
  assert.doesNotMatch(map, /"WORKING"|"READY"|"BLOCKED"|"COMPACTING"|"IDLE"|"CLOSED"/);
  assert.doesNotMatch(R, /const CHIP_LABEL/, "one map: render.ts imports it");
  assert.match(R, /import \{ CHIP_LABEL, chipWords, statusChip, type ChipState \} from "\.\/status-chip";/);
});

test("the unknown-state fallback is sentence case too (not toUpperCase)", () => {
  assert.match(C, /st\[0\]\.toUpperCase\(\) \+ st\.slice\(1\)\.toLowerCase\(\)/);
  assert.doesNotMatch(R, /toUpperCase\(\) \+ s\.status\.state\.slice\(1\)/, "the bar's plain states go through the shared builder");
});
