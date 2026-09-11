// The chat end of "an open Files pane takes the click", EXECUTED: render.ts's shell-message arm for
// {romp:"panes"} (the cache of which panes are on screen, panesOn) and openPath (the click's route through
// fileLinkRoute and the gesture reader) are lifted from render.ts, with the viewer's real openFileClick lifted
// from file-view.ts beneath them, and driven over stubs: a fake window.parent that records what is posted up,
// a session list and tab set for the identity lookup, and hooks in place of the PDF tab opener and the
// in-document viewer. chat-exact-tail-exec.test.ts's idiom (an anchored slice, esbuild at run time, new
// Function). The ladder's own table runs in file-route.test.ts; this file drives the CALLER: the cache is
// filled by the shell's message and replaced whole by the next one, a click while the pane is on screen posts
// viewFile up with the session's looked-up identity, a click while it is off opens in place unless the
// setting names the pane, a modified click on a PDF takes its own tab before any of that, and VS Code and
// an unframed page never relay. Synthetic: the notes-api demo world, placeholder sids.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { fileLinkRoute } from "./file-route";

const requireCjs = createRequire(__filename);
const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const VIEW = fs.readFileSync(path.join(UI, "file-view.ts"), "utf8");

function slice(src: string, startAnchor: string, endAnchor: string, name: string): string {
  const a = src.indexOf(startAnchor), b = src.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, name + ": anchors not found (" + startAnchor.slice(0, 40) + " / " + endAnchor.slice(0, 40) + "); re-anchor");
  return src.slice(a, b);
}
const ts = (code: string): string => requireCjs("esbuild").transformSync(code, { loader: "ts" }).code;

const SID = "11111111-2222-3333-4444-555555555555";       // a session on the tab strip, named and coloured
const SID_TAB = "11111111-2222-3333-4444-666666666666";   // a session the tab set names but the session list does not
const SID_NONE = "11111111-2222-3333-4444-777777777777";  // a sid neither names
const COLOR = { bg: "#123456", fg: "#ffffff" };

type Hooks = {
  vs: unknown[]; up: Array<[unknown, string]>; tabs: Array<[string, string | null]>; views: Array<[string, string | null | undefined]>;
  tabOpens: boolean; settings: { fileLinkPane: unknown }; framed: boolean; protocol: string; vscodeApi: boolean;
  activeId: string | null;
};
type Api = { openPath: (p: string, sid?: string | null, ev?: unknown) => void; onShellMessage: (m: unknown) => void; panesOn: () => Record<string, boolean> };

function lift(): (h: Hooks, route: typeof fileLinkRoute) => Api {
  // file-view.ts: the gesture reader as written (its own tests are pdf-new-tab.test.ts); the tab opener and
  // the viewer are the hooks
  const click = ts(slice(VIEW, "export function openFileClick(", "\n}\n", "openFileClick") + "\n}\n").replace(/^export /m, "");
  // render.ts: the cache and openPath, then the shell-message arm that fills the cache, wrapped as a function of the message
  const open = ts(slice(RENDER, "let panesOn: Record<string, boolean> = {};", "\n// A middle-click on a path pill is the same open", "openPath"));
  const arm = ts(slice(RENDER, 'if (m.romp === "panes") {', "// the pipe's down edge", "the panes arm"));
  const prelude = `
    const H = HOOKS;
    const vscodeApi = H.vscodeApi ? { postMessage: (m) => { H.vs.push(m); } } : undefined;
    const location = { protocol: H.protocol };
    const window = {};
    window.parent = H.framed ? { postMessage: (m, origin) => { H.up.push([m, origin]); } } : window;
    const settings = H.settings;
    let activeId = H.activeId;
    const sessions = new Map([[${JSON.stringify(SID)}, { id: ${JSON.stringify(SID)}, name: "web", color: ${JSON.stringify(COLOR)} }]]);
    const tabMeta = new Map([[${JSON.stringify(SID_TAB)}, { name: "api", color: null }]]);
    const wantsOwnTab = (ev) => !!(ev && ev.mod);
    const openPdfTab = (p, sid) => { H.tabs.push([p, sid]); return H.tabOpens; };
    const openFileView = (p, sid) => { H.views.push([p, sid]); return true; };
  `;
  const epilogue = `
    function onShellMessage(m) { ${arm} }
    return { openPath, onShellMessage, panesOn: () => panesOn };
  `;
  return new Function("HOOKS", "fileLinkRoute", prelude + click + open + epilogue) as (h: Hooks, route: typeof fileLinkRoute) => Api;
}

function world(over: Partial<Hooks> = {}) {
  const H: Hooks = { vs: [], up: [], tabs: [], views: [], tabOpens: true, settings: { fileLinkPane: "chat" }, framed: true,
    protocol: "http:", vscodeApi: true, activeId: SID, ...over };
  return { H, api: lift()(H, fileLinkRoute) };   // the real ladder (file-route.ts) under the lifted caller
}

const PANES_ON = (on: Record<string, unknown>) => ({ romp: "panes", on });
const relayed = (H: Hooks) => H.up.map(([m]) => m);

test("the default world: no shell message yet and the setting at its default, so a click opens the viewer in place", () => {
  const { H, api } = world();
  assert.deepEqual(api.panesOn(), {}, "nothing heard from the shell reads as all-off");
  api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual(H.views, [["/repo/notes-api/src/app.py", SID]], "the in-document viewer");
  assert.deepEqual(H.up, [], "nothing posted to the shell");
  assert.deepEqual(H.tabs, [], "a plain click asks for no tab");
});

test("the shell's panes message fills the cache, and a click while the Files pane is on screen is handed up with the session's identity", () => {
  const { H, api } = world();
  api.onShellMessage(PANES_ON({ chat: true, timeline: true, fleet: false, feed: true, files: true }));
  assert.deepEqual(api.panesOn(), { chat: true, timeline: true, fleet: false, feed: true, files: true });
  api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual(H.views, [], "the viewer here is not opened");
  assert.deepEqual(H.up, [[{ romp: "viewFile", path: "/repo/notes-api/src/app.py", sid: SID, pane: "pane", frag: null, identity: { name: "web", color: COLOR } }, "*"]],
    "one message up, naming the target pane, with the name and colour the tab strip shows for the session");
  // the identity is LOOKED UP, never invented: a session only the tab set names sends its name with no colour;
  // a sid neither list names sends null, and the pane falls to the kernel's stub
  api.openPath("/repo/notes-api/README.md", SID_TAB, { mod: false });
  assert.deepEqual(relayed(H)[1], { romp: "viewFile", path: "/repo/notes-api/README.md", sid: SID_TAB, pane: "pane", frag: null, identity: { name: "api", color: null } });
  api.openPath("/repo/notes-api/README.md", SID_NONE, { mod: false });
  assert.deepEqual(relayed(H)[2], { romp: "viewFile", path: "/repo/notes-api/README.md", sid: SID_NONE, pane: "pane", frag: null, identity: null });
  // no sid on the click: the active session's, as the in-place open resolves it
  api.openPath("/repo/notes-api/notes.md", null, { mod: false });
  assert.deepEqual(relayed(H)[3], { romp: "viewFile", path: "/repo/notes-api/notes.md", sid: SID, pane: "pane", frag: null, identity: { name: "web", color: COLOR } });
  assert.deepEqual(H.views, [], "none of them opened here");
});

test("the cache is replaced WHOLE by each message: a pane the shell stops naming reads as off, a foreign value as off, a message without a set changes nothing", () => {
  const { H, api } = world();
  api.onShellMessage(PANES_ON({ chat: true, feed: true, files: true }));
  api.onShellMessage(PANES_ON({ chat: true, feed: true }));   // the Files pane toggled off: the next set has no files key
  assert.deepEqual(api.panesOn(), { chat: true, feed: true });
  api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual(H.views, [["/repo/notes-api/src/app.py", SID]], "off: in place again");
  assert.deepEqual(H.up, []);
  api.onShellMessage(PANES_ON({ chat: true, files: "yes" }));   // only the boolean true is on
  assert.deepEqual(api.panesOn(), { chat: true, files: false });
  api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.equal(H.views.length, 2, "a non-boolean is not on");
  api.onShellMessage(PANES_ON({ files: true }));
  api.onShellMessage({ romp: "panes" });                   // no set: not a claim about the panes
  assert.deepEqual(api.panesOn(), { files: true }, "the cache stands");
  api.onShellMessage({ romp: "panes", on: "files" });      // a set that is not an object: the same
  assert.deepEqual(api.panesOn(), { files: true });
  api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.equal(H.up.length, 1, "and routes by it");
});

test("the Files pane off: the setting decides; 'pane' hands the click up (the shell brings the pane forward), anything else opens in place", () => {
  const { H, api } = world({ settings: { fileLinkPane: "pane" } });
  api.onShellMessage(PANES_ON({ chat: true, feed: true, files: false }));
  api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual(relayed(H), [{ romp: "viewFile", path: "/repo/notes-api/src/app.py", sid: SID, pane: "pane", frag: null, identity: { name: "web", color: COLOR } }]);
  assert.deepEqual(H.views, []);
  // the setting is read at CLICK time: flipped back, the next click opens here
  H.settings.fileLinkPane = "chat";
  api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual(H.views, [["/repo/notes-api/src/app.py", SID]]);
  H.settings.fileLinkPane = "purple";                      // a foreign stored value is the default
  api.openPath("/repo/notes-api/README.md", SID, { mod: false });
  assert.equal(H.views.length, 2);
  assert.equal(H.up.length, 1, "no further relay");
  // and an open pane overrides the setting the other way
  api.onShellMessage(PANES_ON({ chat: true, files: true }));
  api.openPath("/repo/notes-api/README.md", SID, { mod: false });
  assert.equal(H.up.length, 2, "on screen: the pane takes it whatever the setting says");
});

test("the gesture is read FIRST: a Cmd/Ctrl- or middle-clicked PDF takes its own tab whichever pane the plain click would have landed in; a blocked tab falls through to the route", () => {
  const { H, api } = world();
  api.onShellMessage(PANES_ON({ files: true }));
  api.openPath("/repo/notes-api/docs/spec.pdf", SID, { mod: true });
  assert.deepEqual(H.tabs, [["/repo/notes-api/docs/spec.pdf", SID]], "the browser's own tab");
  assert.deepEqual(H.up, [], "the relay never fires for a modified click that took its tab");
  assert.deepEqual(H.views, []);
  // the popup blocked (openPdfTab false): the plain route, here the pane, so the file is never unreachable
  H.tabOpens = false;
  api.openPath("/repo/notes-api/docs/spec.pdf", SID, { mod: true });
  assert.equal(H.tabs.length, 2);
  assert.equal(H.up.length, 1, "falls through to the route");
  // a plain click on a PDF with the pane off and the default setting: the viewer, no tab
  api.onShellMessage(PANES_ON({ files: false }));
  api.openPath("/repo/notes-api/docs/spec.pdf", SID, { mod: false });
  assert.equal(H.tabs.length, 2, "no tab asked for");
  assert.deepEqual(H.views, [["/repo/notes-api/docs/spec.pdf", SID]]);
});

test("no shell to relay to (standalone /chat) or no web host (VS Code): the cache and the setting never route a click away", () => {
  const solo = world({ framed: false, settings: { fileLinkPane: "pane" } });
  solo.api.onShellMessage(PANES_ON({ files: true }));
  solo.api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual(solo.H.views, [["/repo/notes-api/src/app.py", SID]], "unframed: in place, whatever the cache or setting says");
  assert.deepEqual(solo.H.up, []);
  const code = world({ protocol: "vscode-webview:", settings: { fileLinkPane: "pane" } });
  code.api.onShellMessage(PANES_ON({ files: true }));
  code.api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual(code.H.vs, [{ type: "openFile", path: "/repo/notes-api/src/app.py", id: SID }], "the host editor, by the extension's message");
  code.api.openPath("/repo/notes-api/src/app.py", null, { mod: false });
  assert.deepEqual(code.H.vs[1], { type: "openFile", path: "/repo/notes-api/src/app.py" }, "no sid: the extension picks");
  assert.deepEqual(code.H.views, []); assert.deepEqual(code.H.up, []);
  const none = world({ vscodeApi: false });
  none.api.openPath("/repo/notes-api/src/app.py", SID, { mod: false });
  assert.deepEqual([none.H.views, none.H.up, none.H.vs], [[], [], []], "no host at all: nothing");
});
