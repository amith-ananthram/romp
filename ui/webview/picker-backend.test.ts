// A per-session backend picker on the + dialog (the user 2026-06-23): a segmented toggle, defaulting to the
// gear's Default backend but overridable for THIS new session. The toggles read "Claude Code" and "Codex" from
// backend-names.ts (T288); the terminal backend is no longer offered (T331, the user 2026-09-10: the tmux backend
// is being removed), and a saved default of it reads as Claude Code. Source-pin over render.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the + dialog builds a Claude Code | Codex backend toggle from the shared names, hidden in pick-mode", () => {
  assert.match(RENDER, /import \{ backendLabel, effectiveDefaultBackend \} from "\.\/backend-names";/);
  assert.match(RENDER, /const beWrap = el\("div", "picker-backend"\)/);
  assert.match(RENDER, /mkBe\("sdk", backendLabel\("sdk"\)/);
  assert.match(RENDER, /mkBe\("codex", backendLabel\("codex"\)/);
  assert.doesNotMatch(RENDER, /mkBe\("tmux"/, "the terminal backend is not offered");
  assert.doesNotMatch(RENDER, /kernelTmuxBackend|tmuxBackend/, "no offer switch rides the sessionList reply any more");
  assert.equal([...RENDER.matchAll(/mkBe\("[a-z]+", backendLabel/g)].length, 2, "two options");
  assert.match(RENDER, /box\.appendChild\(beWrap\)/);
  // hidden + reset to the EFFECTIVE default each open (a saved default no longer offered → Claude Code)
  assert.match(RENDER, /beWrapEl\.style\.display = pick \? "none" : ""/);
  assert.match(RENDER, /const def = effectiveDefaultBackend\(loadSettings\(\)\.backend\);/);
  assert.match(RENDER, /\.classList\.toggle\("sel", \(x as HTMLElement\)\.dataset\.be === def\)\);\s*\n\s*syncPickerBackends\(\);/, "the default is applied on every open");
});

test("the selected toggle follows the effective default until the user picks this open, and a pick is never undone", () => {
  assert.match(RENDER, /let pickerBackendPicked = false;/);
  assert.match(RENDER, /b\.addEventListener\("click", \(\) => \{ pickerBackendPicked = true; beWrap\.querySelectorAll/, "a click is an explicit pick");
  assert.match(RENDER, /pickerBackendPicked = false;   \/\/ a fresh open/, "each open forgets the last pick");
  const sync = RENDER.slice(RENDER.indexOf("function syncPickerBackends(): void {"), RENDER.indexOf("\n}\n", RENDER.indexOf("function syncPickerBackends(): void {")));
  assert.match(sync, /const def = effectiveDefaultBackend\(loadSettings\(\)\.backend\);\s*\n\s*const cur = wrap\.querySelector\("\.picker-be-opt\.sel"\) as HTMLElement \| null;\s*\n\s*if \(!pickerBackendPicked && cur\?\.dataset\.be !== def\) \{/);
  assert.match(sync, /syncPickerAuth\(\); syncPickerTags\(\);/, "the Billing and Tags rows follow the new choice");
  assert.doesNotMatch(sync, /display/, "nothing is hidden or shown: both options are always on offer");
});

test("createSession uses the picked backend, falling back to the EFFECTIVE gear default", () => {
  assert.match(RENDER, /const beSel = beWrap\.querySelector\("\.picker-be-opt\.sel"\)/);
  assert.match(RENDER, /const backend = beSel\?\.dataset\.be \|\| effectiveDefaultBackend\(loadSettings\(\)\.backend\);/);
  assert.match(RENDER, /return beSel\?\.dataset\.be \|\| effectiveDefaultBackend\(loadSettings\(\)\.backend\);/, "pickerBackendChoice too");
  assert.match(RENDER, /startCreate\(\{ name, backend,/);
});

test("the toggle's selected option is the romp ACCENT (the user 2026-06-24), not the working-yellow", () => {
  assert.match(CSS, /\.picker-be-opt\.sel \{[^}]*background: var\(--accent\)/);
  assert.doesNotMatch(CSS, /\.picker-be-opt\.sel \{[^}]*background: var\(--st-working-bg\)/);
});
