// Split screen for the chat (the user 2026-09-08: several sessions open at once instead of tabbing through
// them). The shell owns the columns (kernel.py _LANDING_SPLIT_JS, executed in tests/test_chat_split.py); this
// pins the PANE and PALETTE halves at source (no jsdom for the renderer — the repo convention):
//   * render.ts stands down on a focus, a revive prompt or the feed's click echo that the shell says belongs
//     to another column, asks for a split from the tab menu, and measures ITS OWN pane when lifted;
//   * palette-main.ts aims every chat-directed command at the column last worked in, registers the split
//     commands, and wires its chords on a column made later;
//   * commands.ts binds the split to the editor convention.
// Synthetic only — no session data.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const MAIN = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "palette-main.ts"), "utf8");
const COMMANDS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "commands.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the pane asks the shell which column a session-focus belongs to, and acts only when it is its own", () => {
  // the arbitration: the shell's __rompChatTarget names the frame; no shell (standalone, VS Code) → always ours
  assert.match(RENDER, /function focusIsOurs\(sid: string\): boolean \{[\s\S]*?if \(!window\.parent \|\| window\.parent === window\) return true;[\s\S]*?const t = \(window\.parent as any\)\.__rompChatTarget;[\s\S]*?if \(typeof t !== "function"\) return true;[\s\S]*?return !f \|\| f === window\.frameElement;/);
  // a kernel focus and a revive prompt aimed at another column are swallowed BEFORE the real branches
  assert.match(RENDER, /else if \(m\.type === "focus" && !m\.own && !focusIsOurs\(m\.id\)\) \{[^}]*\}\n\s*else if \(m\.type === "focus"\) \{/);
  // the shell hands a NEW column no focus any more (2026-09-11): it seeds the column's state blob with the session
  // before the frame exists, and the page's own wantActive activates it when its frame lands (tests/test_chat_split.py
  // pins the seed); a column opened on a session another column shows still takes it, since wantActive never arbitrates
  assert.ok(!KERNEL.includes("f.contentWindow.postMessage({type:'focus',id:sid,own:true},'*')"), "no hand-over focus");
  assert.ok(!KERNEL.includes("if(sid)f.addEventListener('load'"), "…and no load listener carrying one");
  assert.match(RENDER, /else if \(m\.type === "confirmRevive" && m\.id && !m\.own && !focusIsOurs\(m\.id\)\) \{[^}]*\}\n\s*else if \(m\.type === "confirmRevive" && m\.id\) \{/);
  // …and the kernel marks the parked push-tap reveal it hands ONE column `own` (tests/test_chat_split.py pins the kernel side)
  // the feed's click echo reaches every column's storage listener: the same gate, after the known-session check
  assert.match(RENDER, /if \(e\.key !== "romp:focus-echo" \|\| !e\.newValue\) return;[\s\S]*?if \(!sid \|\| \(!sessions\.has\(sid\) && !tabMeta\.has\(sid\)\)\) return;\n\s*if \(!focusIsOurs\(sid\)\) return;/);
});

test("the tab menu offers Open in new split when a shell that can split hosts the pane", () => {
  const i = RENDER.indexOf('l.textContent = "Open in new split"');
  assert.ok(i > 0, "the item exists");
  const block = RENDER.slice(RENDER.lastIndexOf("const shellCanSplit", i), RENDER.indexOf("menu.appendChild(split);", i));
  // a shell with the split script, and one that can take another column right now (the cap, the phone)
  assert.match(block, /inRompShell\(\) && typeof p\.__rompSplitChat === "function" && \(typeof p\.__rompCanSplit !== "function" \|\| !!p\.__rompCanSplit\(\)\)/);
  assert.match(block, /if \(shellCanSplit\) \{/);
  assert.match(block, /ctxIcon\("split", false\)/);
  assert.match(block, /window\.parent\.postMessage\(\{ romp: "openSplit", sid: id \}, "\*"\)/);
  // it sits with Rename and Move (where this session shows), ahead of the colour swatches
  assert.ok(i > RENDER.indexOf('l.textContent = "Move to folder…"'), "after Move to folder…");
  assert.ok(i < RENDER.indexOf("// Colors join Rename in the AESTHETIC section"), "before the colours");
  // the icon: two columns side by side
  assert.match(RENDER, /kind === "split"\n\s*\? '<rect x="2" y="3" width="5" height="10" rx="1"\/><rect x="9" y="3" width="5" height="10" rx="1"\/>'/);
});

test("a lifted column measures ITS OWN pane, never the first column's", () => {
  assert.match(RENDER, /function liftPaneRect\(\): DOMRect \| null \{[\s\S]*?const own = window\.frameElement \? \(window\.frameElement as HTMLElement\)\.parentElement : null;\n\s*const p = own \|\| window\.parent\?\.document\?\.getElementById\("chat-pane"\);/);
  // …and the shell lifts by class, marking the asking frame (the pinned CSS moved off #f-chat)
  assert.ok(KERNEL.includes('"body.picker-open iframe.lifted{display:block;position:fixed;left:0;right:0;top:0;height:var(--app-h,100dvh);z-index:200;background:transparent}"'));
  assert.ok(!KERNEL.includes('"body.picker-open #f-chat{'));
});

test("every chat-directed shell command lands in the column last worked in", () => {
  assert.match(MAIN, /function chatPane\(\): HTMLIFrameElement \| null \{\n\s*try \{ const id = w\.__rompFocusedChatId && w\.__rompFocusedChatId\(\);[\s\S]*?return pane\("f-chat"\);\n\s*\}/);
  assert.match(MAIN, /function chatPost\(msg: object\): void \{[\s\S]*?const f = chatPane\(\);/);
  assert.match(MAIN, /\(chatPane\(\)\?\.contentWindow as any\)\?\.__rompSessionList/);
  assert.match(MAIN, /\(chatPane\(\)\?\.contentWindow as any\)\?\.__rompMru/);
  assert.match(MAIN, /chatPane\(\)!\.contentWindow!\.postMessage\(\{ romp: "chatNav", dir: -1 \}/);
  assert.match(MAIN, /chatPane\(\)!\.contentWindow!\.postMessage\(\{ romp: "chatNav", dir: 1 \}/);
  assert.match(MAIN, /onClose: \(\) => \{ try \{ chatPane\(\)!\.contentWindow!\.focus\(\); \}/);
  assert.ok(!/pane\("f-chat"\)!/.test(MAIN), "no chat-directed command still hard-wires the first column");
});

test("the split commands exist, the split is bound to the editor convention, and a new column gets the chords", () => {
  assert.match(MAIN, /registerCommand\(\{ id: "chat\.split", title: "Split the chat", run: \(\) => \{ if \(w\.__rompSplitChat\) w\.__rompSplitChat\(\); \} \}\);/);
  assert.match(MAIN, /registerCommand\(\{ id: "chat\.closeSplit", title: "Close this chat split", run: \(\) => \{ if \(w\.__rompCloseSplit\) w\.__rompCloseSplit\(\); \} \}\);/);
  assert.match(COMMANDS, /"chat\.split": "Mod\+\\\\",/);
  assert.ok(!/"chat\.closeSplit":/.test(COMMANDS), "closing stays unbound by default — the palette owns it");
  // the fixed four are wired as before, and a column the split makes later through the shell's event
  assert.match(MAIN, /\["f-chat", "f-fleet", "f-feed", "f-files", "f-timeline", "f-settings"\]\.forEach\(\(id\) => wireKeys\(pane\(id\)\)\);/);   // + the Files pane and the settings iframe (main, 2026-09-10: the gear's document holds the keyboard while open)
  assert.match(MAIN, /window\.addEventListener\("romp-chat-cols", \(e\) => wireKeys\(/);
  // …and the columns restored before this module booted (the shell's split script runs ahead of it)
  assert.match(MAIN, /\(\(w\.__rompChatFrameIds \? w\.__rompChatFrameIds\(\) : \[\]\) as string\[\]\)\.forEach\(\(id\) => \{ if \(id !== "f-chat"\) wireKeys\(pane\(id\)\); \}\);/);
  // …which the shell dispatches with the frame when a column opens
  assert.ok(KERNEL.includes("window.dispatchEvent(new CustomEvent('romp-chat-cols',{detail:{frame:f,col:n,open:true}}))"));
  // no bottom-bar button for it (the user 2026-09-08): the tab menu and the palette are the doors
  assert.ok(!KERNEL.includes("rail-split"));
});
