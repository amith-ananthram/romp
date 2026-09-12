// The strip accepts `dragenter` while one of its own drags is in flight, and only then (drag-accept.ts). Chromium
// answers the tick where the element under the pointer changes with dragenter, not dragover, and takes the drop
// operation from it; the live reorder's insert changes that element under a still pointer, so without this a release
// right after a hop was refused and the tab snapped home (2026-09-11). Executed on node's own EventTarget: no DOM.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { acceptDragEnter } from "./drag-accept";

test("dragenter is accepted (default prevented) while a drag is active, and left alone otherwise", () => {
  const strip = new EventTarget();
  let active = false;
  acceptDragEnter(strip, () => active);
  const idle = new Event("dragenter", { cancelable: true });
  strip.dispatchEvent(idle);
  assert.equal(idle.defaultPrevented, false, "no drag of ours: a foreign drag (a file) is not accepted");
  active = true;
  const dragging = new Event("dragenter", { cancelable: true });
  strip.dispatchEvent(dragging);
  assert.equal(dragging.defaultPrevented, true, "a tab or group drag in flight: the tick's drop operation stays ours");
  active = false;
  const after = new Event("dragenter", { cancelable: true });
  strip.dispatchEvent(after);
  assert.equal(after.defaultPrevented, false, "…and it reads the predicate live, so a finished drag accepts nothing");
});

test("render.ts installs it on #tabs with the same guard the dragover uses (a tab OR a group drag)", () => {
  const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
  assert.match(RENDER, /import \{ acceptDragEnter \} from "\.\/drag-accept";/);
  assert.match(RENDER, /acceptDragEnter\(tabs, \(\) => !!\(draggedId \|\| draggedGroup\)\);/,
    "installed once on the stable #tabs, beside the dragover listener, gated on the drag state the dragover reads");
  assert.equal((RENDER.match(/acceptDragEnter\(tabs,/g) || []).length, 1, "once: #tabs survives every rebuild");
});
