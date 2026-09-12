// The whole chat pane takes a file drop, and nothing else on the dashboard navigates on one (the user 2026-09-12: an image
// dropped beside the box replaced the page with the image — the browser's default for an unhandled drop). Pinned at source,
// the repo's convention where there is no DOM: render.ts's document-level listeners and the shared accept, the pane ring's
// CSS, and the two refusals the kernel's shell scripts install (every non-chat pane's shim, the shell document). The
// behaviour end to end — a drop on the transcript attaching to the box, per split column, and a drop on the shell or the
// feed refused without a navigation — is tests/test_drop_anywhere_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const setup = RENDER.slice(RENDER.indexOf("function setupComposer()"), RENDER.indexOf("function setupComposer()") + 60000);

test("render.ts: the box and the pane share ONE accept; only an OS file drag (types holds Files) is a drop", () => {
  assert.match(RENDER, /^function fileDrag\(e: DragEvent\): boolean \{\n\s*return !!e\.dataTransfer && Array\.from\(e\.dataTransfer\.types \|\| \[\]\)\.includes\("Files"\);\n\}/m);
  assert.match(RENDER, /^function acceptDroppedTransfer\(dt: DataTransfer\): void \{\n\s*const remote = hostOf\(activeId \|\| ""\);/m, "the box's old body, lifted: the remote rule, the path branches, the bytes");
  assert.match(RENDER, /shipFileToHost\(f\);\n\s*\}\);\n\}\n\nfunction shipFileToHost\(/, "…ending in the ship, as before");
  assert.match(setup, /ta\.addEventListener\("drop", \(e\) => \{\n\s*e\.preventDefault\(\); e\.stopPropagation\(\);\n\s*ta\.classList\.remove\("drop-target"\); paneDropOver\(false\);\n\s*if \(e\.dataTransfer\) acceptDroppedTransfer\(e\.dataTransfer\);\n\s*\}\);/,
    "the box's drop stops propagation (taken once) and clears the pane's ring");
});

test("render.ts: the document takes the drop anywhere — enter/leave counted, the ring dropped at zero or on the drop, the copy cursor while over", () => {
  assert.match(setup, /let dragDepth = 0;\n\s*const paneDropOver = \(on: boolean\): void => \{\n\s*if \(!on\) dragDepth = 0;\n\s*document\.body\.classList\.toggle\("drop-over", on\);\n\s*ta\.classList\.toggle\("drop-target", on\);\n\s*\};/);
  assert.match(setup, /document\.addEventListener\("dragenter", \(e\) => \{ if \(!fileDrag\(e\)\) return; dragDepth\+\+; paneDropOver\(true\); \}\);/);
  assert.match(setup, /document\.addEventListener\("dragover", \(e\) => \{\n\s*if \(!fileDrag\(e\)\) return;\n\s*e\.preventDefault\(\);\n\s*if \(e\.dataTransfer\) e\.dataTransfer\.dropEffect = "copy";\n\s*if \(!dragDepth\) \{ dragDepth = 1; paneDropOver\(true\); \}\n\s*\}\);/,
    "dragover is what permits the drop; a drag that arrived without an enter still rings");
  assert.match(setup, /document\.addEventListener\("dragleave", \(e\) => \{ if \(!fileDrag\(e\)\) return; if \(--dragDepth <= 0\) paneDropOver\(false\); \}\);/);
  assert.match(setup, /document\.addEventListener\("drop", \(e\) => \{\n\s*if \(!fileDrag\(e\)\) return;\n\s*e\.preventDefault\(\);\n\s*paneDropOver\(false\);\n\s*if \(e\.dataTransfer\) acceptDroppedTransfer\(e\.dataTransfer\);\n\s*\}\);/);
  // a tab drag or a text selection carries no Files: the strip's own dragover keeps its handler, untouched by this
  assert.match(RENDER, /tabs\.addEventListener\("dragover", \(e\) => \{/);
});

test("styles.css: the pane rings while a file drag is over it, pointer-transparent, above the pane's layers", () => {
  assert.match(CSS, /body\.drop-over::after \{ content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 300;\n\s*outline: 2px dashed var\(--accent\); outline-offset: -4px; \}/);
  assert.match(CSS, /#composer-input\.drop-target \{ outline: 1\.5px dashed/, "the box's own outline stays the landing spot");
});

test("kernel: every non-chat pane's shim and the shell document refuse a file drag — not-allowed cursor, drop swallowed, no navigation", () => {
  const shim = KERNEL.slice(KERNEL.indexOf("def _shim(app, v=0, no_stale=False):"), KERNEL.indexOf("def _shim(app, v=0, no_stale=False):") + 40000);
  assert.match(shim, /if\(APP!=="chat"\)\{var fileDrag=function\(e\)\{var t=e\.dataTransfer&&e\.dataTransfer\.types;if\(!t\)return false;for\(var i=0;i<t\.length;i\+\+\)if\(t\[i\]==="Files"\)return true;return false;\};\n/);
  assert.match(shim, /document\.addEventListener\("dragover",function\(e\)\{if\(fileDrag\(e\)\)\{e\.preventDefault\(\);try\{e\.dataTransfer\.dropEffect="none";\}catch\(x\)\{\}\}\}\);\n/);
  assert.match(shim, /document\.addEventListener\("drop",function\(e\)\{if\(fileDrag\(e\)\)e\.preventDefault\(\);\}\);\}/, "the chat's document is left to its bundle, which takes the drop");
  const focus = KERNEL.slice(KERNEL.indexOf("_LANDING_FOCUS_JS = "), KERNEL.indexOf("_LANDING_FOCUS_JS = ") + 6000);
  assert.match(focus, /function fileDrag\(e\)\{var t=e\.dataTransfer&&e\.dataTransfer\.types;if\(!t\)return false;for\(var i=0;i<t\.length;i\+\+\)if\(t\[i\]==='Files'\)return true;return false;\}/);
  assert.match(focus, /document\.addEventListener\('dragover',function\(e\)\{if\(fileDrag\(e\)\)\{e\.preventDefault\(\);try\{e\.dataTransfer\.dropEffect='none';\}catch\(x\)\{\}\}\}\);\ndocument\.addEventListener\('drop',function\(e\)\{if\(fileDrag\(e\)\)e\.preventDefault\(\);\}\);/);
});
