// Branch lineage on the timeline (the user 2026-08-14). No DOM harness for the SVG draw path, so
// pin the wiring at the source (the repo convention for romp-timeline-view.js): the thick
// perpendicular branch connector, the comment squares, and the kernel payload they consume.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the branch connector is a thick perpendicular bar between the two lanes, child-colored", () => {
  // work-bar weight (BAR_H), spanning parent lane y to child lane y at the fork moment
  assert.match(SRC, /el\('rect', \{ x: bx - BAR_H \/ 2, y: bTop, width: BAR_H, height: bH, rx: BAR_H \/ 2, fill: s\.color/);
  // both endpoints vidx-guarded — a hidden/dismissed lane means no connector, never a dangling one
  assert.match(SRC, /if \(!br \|\| vidx\[br\.fromId\] == null \|\| vidx\[s\.id\] == null \|\| !inWin\(br\.t\)\) return;/);
  // click → the child's chat at its branch divider (the divider's data-uuid is branch:<cut>)
  assert.match(SRC, /this\.openChat\(s\.id, br\.cut \? 'branch:' \+ br\.cut : '', false, false, br\.t\)/);
  // a wide invisible hit target + re-armable hover, like every other timeline glyph
  assert.match(SRC, /bhit\.__tlHoverIn = bEnter;/);
});

test("a comment is a SQUARE on the lane — session-colored, dot-sized, white-bordered, never a lane", () => {
  // the SHAPE alone says comment (the user 2026-08-15): same footprint + border as a message dot
  assert.match(SRC, /const side = DOT_R \* 2 - 1, cx = x\(c\.t\);/);
  assert.match(SRC, /el\('rect', \{ x: cx - side \/ 2, y: y - side \/ 2, width: side, height: side, rx: 1\.5,\s*\n\s*fill: s\.color, stroke: PAL\(\)\.dotRing, 'stroke-width': 0\.75/);
  assert.match(SRC, /opacity: c\.status === 'resolved' \? 0\.45 : 0\.95/);
  // click → the chat at the commented message, where the highlight opens the thread
  assert.match(SRC, /this\.openChat\(s\.id, c\.uuid, false, false, c\.t\)/);
});

test("the kernel serves lineage per lane and clips the copied history only while the parent shows", () => {
  assert.match(KERNEL, /"branch": branch_of\.get\(sid\),/);
  assert.match(KERNEL, /"comments": _comment_markers\(sid\),/);
  assert.match(KERNEL, /if _psid not in id2name:\s*\n\s*continue/, "parent lane present is the connector AND clip condition");
  // the clip runs in the lane's segment derivation (_lane_segments), which takes the fork time as `bft`
  assert.match(KERNEL, /if bft and \(seg\.get\("end"\) or seg\["t"\]\) <= bft:\s*\n\s*continue/);
  assert.match(KERNEL, /def _comment_markers\(sid\):/);
  // a promoted thread is a session (branch connector), not a square
  assert.match(KERNEL, /not in \("open", "resolved"\):\s*\n\s*continue/);
});
