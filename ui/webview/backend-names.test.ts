// The backends as the user reads them (T288, the user 2026-09-09): "Claude Code" (the default, no qualifier) and
// "Codex"; the ids and the protocol are unchanged. One module names them for the picker, the gear and the tooltip,
// so the surfaces cannot drift apart. The terminal backend is no longer offered (T331, the user 2026-09-10: the
// tmux backend is being removed), and a saved default of it reads as Claude Code, never as an undefined value.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { BACKEND_LABEL, backendLabel, offeredBackends, effectiveDefaultBackend } from "./backend-names";

const read = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", ...p), "utf8");
const RENDER = read("ui", "webview", "render.ts");
const GEAR = read("ui", "webview", "gear.js");

test("the two names, and an unknown id reads as itself", () => {
  assert.deepEqual(BACKEND_LABEL, { sdk: "Claude Code", codex: "Codex" });
  assert.equal(backendLabel("sdk"), "Claude Code");
  assert.equal(backendLabel("codex"), "Codex");
  assert.equal(backendLabel("tmux"), "tmux", "a retired id names itself, never a lie and never blank (an old session's row)");
  assert.equal(backendLabel("future"), "future");
  assert.equal(backendLabel(null), "");
  for (const label of Object.values(BACKEND_LABEL)) assert.doesNotMatch(label, /\bSDK\b/, "the word never reaches the user");
});

test("the offer is Claude Code and Codex; a saved default no longer offered reads as Claude Code, never undefined", () => {
  assert.deepEqual(offeredBackends(), ["sdk", "codex"]);
  assert.equal(effectiveDefaultBackend("tmux"), "sdk", "the retired terminal backend's saved default migrates to Claude Code");
  assert.equal(effectiveDefaultBackend("codex"), "codex");
  assert.equal(effectiveDefaultBackend("sdk"), "sdk");
  assert.equal(effectiveDefaultBackend(undefined), "sdk", "no preference is the default");
  assert.equal(effectiveDefaultBackend(""), "sdk");
  assert.equal(effectiveDefaultBackend("nonsense"), "sdk");
  assert.equal(offeredBackends.length, 0, "no offer switch: the list takes no argument");
  assert.equal(effectiveDefaultBackend.length, 1);
});

test("no user-facing copy in the picker, the tooltip or the gear says SDK; every label comes from the shared names", () => {
  // the picker toggles and the tooltip row read backendLabel; the gear's select spells the same two names
  assert.match(RENDER, /mkBe\("sdk", backendLabel\("sdk"\)/);
  assert.match(RENDER, /mkBe\("codex", backendLabel\("codex"\)/);
  assert.match(RENDER, /rows\.push\(\["Backend", backendLabel\(be\)\]\)/, "the tab tooltip's Backend row");
  assert.match(GEAR, /<option value=sdk>Claude Code<\/option><option value=codex>Codex<\/option>/);
  assert.doesNotMatch(GEAR, /option value=tmux|rs-tmuxbackend|setTmuxBackend|paintBackendOffer/, "the gear offers no terminal backend and no switch for one");
  // the word "SDK" in a string a person reads: the picker tips, the tooltip row, the gear's HTML. Comments and
  // identifiers (status.backend === "sdk", sdkOnly) are not copy; only quoted strings count here.
  const pickerTips = [...RENDER.matchAll(/mkBe\("[a-z]+", backendLabel\("[a-z]+"\), "([^"]*)"\)/g)].map((m) => m[1]);
  assert.equal(pickerTips.length, 2);
  for (const tip of pickerTips) assert.doesNotMatch(tip, /\bSDK\b|tmux/, tip);
  // …and every quoted string in the picker's builder
  const picker = RENDER.slice(RENDER.indexOf('const beWrap = el("div", "picker-backend")'), RENDER.indexOf("actions.appendChild(newSess)"));
  assert.ok(picker.length > 1000 && picker.length < 40000, "the picker's builder located");
  for (const m of picker.matchAll(/"([^"\n]*)"|`([^`\n]*)`/g)) assert.doesNotMatch(m[1] || m[2] || "", /\bSDK\b/, (m[1] || m[2] || "").slice(0, 80));
  // the kernel's own copy that names a backend to the user (warn toasts, refusals) reads the shared names too
  const KERNEL = read("kernel", "kernel.py");
  for (const phrase of ["needs the SDK backend", "need an SDK or Codex session", "needs an SDK session", "runs on tmux, so there is nothing", "tmux sessions still work"])
    assert.ok(!KERNEL.includes(phrase), "kernel copy still says: " + phrase);
  const userStrings = [...GEAR.matchAll(/'([^'\n]*<span class=rs-sub>[^'\n]*)'/g)].map((m) => m[1]);
  for (const s of userStrings) assert.doesNotMatch(s, /\bSDK\b/, s.slice(0, 80));
});

test("the gear's Default backend select is static and painted from the saved preference once, through the shared rule", () => {
  assert.match(GEAR, /if \(bk\) \{ bk\.value = BN\.effectiveDefaultBackend\(load\(\)\.backend\); \}\s*\n\s*repaintSelectPicks\(\);/,
    "a saved default of the retired backend paints as Claude Code; the facade repaints once at init (the call paintBackendOffer used to make)");
  assert.match(GEAR, /if \(bk\) bk\.addEventListener\('change', function \(\) \{ var s = load\(\); s\.backend = bk\.value; save\(s\); \}\);/, "a pick saves only what the select offers, so the retired value is gone on the next save");
});
