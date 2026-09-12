// Right-click a tab → "Move to folder…" (the user 2026-09-01: a subproject became its own repo and the
// session should follow it). Source pins against render.ts, kernel.py and styles.css — the menu and the
// dialog build DOM at click time, so a behavioral jsdom run isn't needed to lock the shape; what matters
// is that the op, the typed replies and the acknowledgement rules hold together across the three files.
import { test } from "node:test";
import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the tab menu carries a Move to folder… row beside Tags that opens the move dialog", () => {
  // the SECOND section, between the menu's first two dividers: where the session belongs (Tags, then Move;
  // the user 2026-09-11, who regrouped the menu by what each item changes)
  const SEP = 'menu.appendChild(el("div", "ctx-sep"));';
  const i = RENDER.indexOf("function showTabMenu(");
  const sep1 = RENDER.indexOf(SEP, i);
  const menu = RENDER.slice(sep1, RENDER.indexOf(SEP, sep1 + SEP.length));
  assert.doesNotMatch(menu, /l\.textContent = "Rename"/, "Rename stays in the first section");
  assert.match(menu, /l\.textContent = "Tags"/);
  assert.match(menu, /l\.textContent = "Move to folder…"/);
  assert.ok(menu.indexOf('l.textContent = "Tags"') < menu.indexOf('l.textContent = "Move to folder…"'), "Move follows Tags");
  assert.match(menu, /dismissTabMenu\(\); showMovePrompt\(id\);/);
  // every session moves (T331: the terminal backend, which had no relocation primitive, is no longer offered)
  assert.match(menu, /mv\.addEventListener\("click", \(ev\) => \{ ev\.stopPropagation\(\); dismissTabMenu\(\); showMovePrompt\(id\); \}\);/);
  assert.doesNotMatch(menu, /isTmux|terminal sessions can't move|ctx-item-off/, "no greyed state, no note");
});

test("the dialog posts moveSession with the typed folder and acknowledges before the round trip", () => {
  const i = RENDER.indexOf("function showMovePrompt(");
  const body = RENDER.slice(i, RENDER.indexOf("function onMoveDirCompletions(", i));
  const ack = body.indexOf('go.textContent = "Moving…"');
  const post = body.indexOf('postMessage({ type: "moveSession", id: sid, dir })');
  assert.ok(ack > 0 && post > ack, "the button changes its label BEFORE the op is posted");
  assert.match(body, /input\.value = sess\?\.cwd \|\| ""/);   // prefilled with the current folder
  assert.match(body, /90000\)/);                              // the backstop covers a revive-then-move
});

test("the path is vetted through the picker's dirComplete op, with reqIds the picker can never claim", () => {
  assert.match(RENDER, /type: "dirComplete", value: input\.value\.trim\(\), reqId: -\(\+\+moveDirReq\), host: hostOf\(sid\)/);
  // routed BEFORE the picker's handler, whose in-flight bookkeeping must not flip on a stranger's answer
  assert.match(RENDER, /if \(typeof m\.reqId === "number" && m\.reqId < 0\) onMoveDirCompletions\(m\); else onDirCompletions\(m\);/);
  // a move never creates a folder, so the picker's "will be created" offer is not shown
  assert.match(RENDER, /A move goes to a folder that already exists\./);
  assert.match(RENDER, /import \{ dirStatusHint, /);
});

test("the kernel's typed outcomes drive the dialog: moved closes it, moveFailed puts the reason where the path is", () => {
  assert.match(RENDER, /else if \(m\.type === "moved" && m\.id\) moveLanded\(/);
  assert.match(RENDER, /else if \(m\.type === "moveFailed" && m\.id\) \{/);
  const i = RENDER.indexOf("function moveFailedLocal(");
  const body = RENDER.slice(i, RENDER.indexOf("// THE FORK MODAL", i));
  assert.match(body, /p\.go\.disabled = false; p\.go\.textContent = "Move"; p\.input\.disabled = false;/);
  assert.match(body, /p\.hint\.className = "move-dir-hint bad"/);
  assert.match(body, /warnToast\(`Couldn’t move “\$\{name\}”: \$\{text\}`\)/);   // the dialog may be gone (a parked move)
});

test("the kernel side: moveSession is a drive op, parks as a plain-words chip, and answers with typed events", () => {
  assert.match(KERNEL, /"renameSession", "moveSession", "stopTask"/);
  assert.match(KERNEL, /elif t == "moveSession" and msg\.get\("dir"\):/);
  assert.match(KERNEL, /return "move to " \+ _tilde\(op\[1\]\)/);
  assert.match(KERNEL, /\{"type": "moved", "id": sid, "name": nm, "cwd": _tilde\(_cwd_of\(sid\)\)\}/);
  assert.match(KERNEL, /\{"type": "moveFailed", "id": sid, "name": nm, "text": text\}/);
  assert.match(KERNEL, /if u\.path == "\/move":/);
});

test("the verdict line wears the picker's dir-hint tones", () => {
  assert.match(CSS, /\.move-dir-hint\.warn \{ color: #e0a030; \}/);
  assert.match(CSS, /\.move-dir-hint\.bad \{ color: #e5484d; \}/);
});
