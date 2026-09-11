// The Files pane (files.ts): the file viewer as its own column of the dashboard, hosting the shared viewer
// pane-resident, with an empty state that lists the files most recently open there. No jsdom harness, so
// the boot wiring is pinned at source; what can run, runs: the pure half (the recent list and the relayed
// identity's validation, files-recent.ts), the viewer's relay guard (lifted from file-view.ts, plain JS
// inside the listener), and the pane's own open and close-edge functions (openHere, onBodyChange, lifted
// from files.ts with esbuild at run time, the chat-exact-tail-exec.test.ts idiom) over stubs for the viewer,
// the store and the shell. The shell's own arms run in tests/test_pane_state_broadcast.py; nothing here
// reads the kernel. Synthetic rows: the notes-api demo world, placeholder sids, TESTHOST for a remote host.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { asIdentity, parseRecent, rememberRecent, RECENT_MAX, type RecentFile } from "./files-recent";

const requireCjs = createRequire(__filename);

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const SRC = read("files.ts");
const HELPERS = read("files-recent.ts");
const VIEW = read("file-view.ts");
const CSS = read("files-pane.css");
const ESBUILD = fs.readFileSync(path.resolve(process.cwd(), "esbuild.js"), "utf8");

const SID = "11111111-2222-3333-4444-555555555555";
const SID2 = "11111111-2222-3333-4444-666666666666";
const row = (p: string, sid: string | null, name = "web", t = 1): RecentFile =>
  ({ path: p, sid, identity: { name, color: { bg: "#123456", fg: "#ffffff" } }, t });

// files.ts's own functions, lifted whole (an anchored function, to its closing brace at column 0) and run
// over stubs: the viewer (openFileView answers H.openOk and records the identity it could resolve at that
// moment), the store, the repaint, the document (which surfaces are up, by id) and the shell (window.parent).
const ts = (code: string): string => requireCjs("esbuild").transformSync(code, { loader: "ts" }).code;
function fnSlice(startAnchor: string): string {
  const a = SRC.indexOf(startAnchor);
  assert.ok(a > 0, startAnchor.slice(0, 40) + " moved; re-anchor");
  const b = SRC.indexOf("\n}\n", a);
  assert.ok(b > a);
  return SRC.slice(a, b + 3);
}
type PaneHooks = { up: Set<string>; openOk: boolean; opens: Array<[string, string | null, unknown]>; writes: RecentFile[][]; paints: number; framed: boolean; posted: unknown[] };
type PaneApi = { openHere: (p: string, sid: string | null, identity: unknown) => void; onBodyChange: () => void; recent: () => RecentFile[]; identities: Map<string, unknown> };
function liftPane(over: Partial<PaneHooks> = {}): { H: PaneHooks; api: PaneApi } {
  const H: PaneHooks = { up: new Set(), openOk: true, opens: [], writes: [], paints: 0, framed: true, posted: [], ...over };
  const js = ts(fnSlice("function surfaceUp(): boolean {") + fnSlice("function openHere(") + fnSlice("let viewerUp = surfaceUp();\nfunction onBodyChange(): void {"));
  const prelude = `
    const H = HOOKS;
    const identities = new Map();
    let recent = [];
    const writeStore = () => { H.writes.push(recent.slice()); };
    const paint = () => { H.paints++; };
    const openFileView = (p, sid) => { H.opens.push([p, sid, (sid && identities.get(sid)) || null]); return H.openOk; };
    const document = { getElementById: (id) => (H.up.has(id) ? { id } : null) };
    const window = {};
    window.parent = H.framed ? { postMessage: (m) => { H.posted.push(m); } } : window;
    const Date = { now: () => 777 };
  `;
  const api = (new Function("HOOKS", "rememberRecent", prelude + js + "return { openHere, onBodyChange, recent: () => recent, identities };") as
    (h: PaneHooks, rr: typeof rememberRecent) => PaneApi)(H, rememberRecent);
  return { H, api };
}

test("the pane hosts the shared viewer and takes the shell's relay WHOLE: its own contract, not the default open", () => {
  // initFileView's second argument replaces the default relay branch for this document: the relay carries
  // the session's identity, which the pane caches before opening, and the open enters the Recent list
  assert.match(SRC, /initFileView\(\(m\) => vscodeApi\?\.postMessage\(m\), \(m\) => \{\n\s*openHere\(m\.path, typeof m\.sid === "string" \? m\.sid : null, asIdentity\(m\.identity\), typeof m\.frag === "string" \? m\.frag : null\);\n\}\);/);
  assert.match(VIEW, /export function initFileView\(poster: \(m: Record<string, unknown>\) => void,\n\s*onRelay\?: \(m: \{ path: string; sid\?: unknown; identity\?: unknown; frag\?: unknown \}\) => void\): void \{/);
  const relayBranch = VIEW.split('if (m.romp === "viewFile"')[1].split("} else if")[0];
  const guard = "if (onRelay) { onRelay(m); return; }";
  // presence first: indexOf's -1 for an ABSENT guard is less than any index, so an ordering check alone
  // would stay green with the dispatch deleted
  assert.ok(relayBranch.indexOf(guard) >= 0, "the dispatch guard is present");
  assert.ok(relayBranch.indexOf("openFileView(m.path") >= 0, "the default open is present");
  assert.ok(relayBranch.indexOf(guard) < relayBranch.indexOf("openFileView(m.path"),
    "a document's own contract takes the message before the default open runs");
  // the file browser is hosted here under the pane's own contract (browse-route.test.ts): a relayed folder lists
  // here with its identity cached, a pick opens through openHere, and the close owes the shell nothing
  assert.match(SRC, /initFileBrowse\(\(m\) => vscodeApi\?\.postMessage\(m\), \{\n\s*shellRestore: false,/);
  // not a feed consumer: no frame parsing of any kind
  assert.doesNotMatch(SRC, /m\.type === "feed"|feedDelta|ledgers|\.asks\b|needFullFeed/);
  assert.match(SRC, /vscodeApi\?\.postMessage\(\{ type: "ready" \}\)/, "the ready handshake, as every pane sends it");
});

// executed: the relay branch of initFileView's listener, lifted from file-view.ts (plain JS inside the TS
// listener, so it runs as written) with openFileView stubbed. With onRelay the message is taken whole and
// the branch returns before the default open; without it the default open runs exactly as before.
test("the relay guard, executed: onRelay takes the message and short-circuits the default open", () => {
  const branch = VIEW.split('if (m.romp === "viewFile"')[1].split("} else if")[0];
  const body = '(function () { if (m.romp === "viewFile"' + branch + "} })();";
  const fn = new Function("m", "onRelay", "openFileView", body) as
    (m: unknown, onRelay: ((m: unknown) => void) | undefined, open: (p: string, sid: string | null) => boolean) => void;
  const run = (m: unknown, onRelay: ((m: unknown) => void) | undefined) => {
    const opened: Array<[string, string | null]> = [];
    fn(m, onRelay, (p, sid) => { opened.push([p, sid]); return true; });
    return opened;
  };
  const identity = { name: "web", color: { bg: "#123456", fg: "#ffffff" } };
  const msg = { romp: "viewFile", path: "/repo/notes-api/src/app.py", sid: SID, identity };
  const taken: unknown[] = [];
  assert.deepEqual(run(msg, (m) => taken.push(m)), [], "the default open never runs");
  assert.deepEqual(taken, [msg], "the pane's contract gets the message WHOLE, identity included");
  assert.deepEqual(run(msg, undefined), [["/repo/notes-api/src/app.py", SID]], "no contract of its own: the default open");
  const junk: unknown[] = [];
  run({ romp: "viewFile", path: "" }, (m) => junk.push(m));
  run({ romp: "viewFile", path: 42 }, (m) => junk.push(m));
  run({ type: "fileSaved", reqId: 1 }, (m) => junk.push(m));
  assert.deepEqual(junk, [], "the outer guard still filters: no path, no relay");
});

test("the session chip resolves from what the relay carried, cached per sid, else the kernel's stub", () => {
  assert.match(SRC, /setFileViewIdentity\(\(id\) => identities\.get\(id\) \?\? hostStub\(id\)\);/);
  const openFn = SRC.split("function openHere(")[1].split("\n}")[0];
  assert.ok(openFn.indexOf("identities.set(sid, identity);") >= 0 && openFn.indexOf("openFileView(path, sid, { frag })") >= 0
    && openFn.indexOf("identities.set(sid, identity);") < openFn.indexOf("openFileView(path, sid, { frag })"),
    "the cache is filled BEFORE the open, so the title bar's chip resolves on the first paint");
  assert.match(openFn, /if \(sid && identity\) identities\.set\(sid, identity\);/);
  assert.doesNotMatch(SRC, /sessionsMeta|tabMeta|sessions\.get/, "the pane has no session list of its own");
});

// executed: openHere as files.ts spells it. The identity is cached BEFORE the viewer opens (the chip's resolver
// runs during the open, so a cache filled after it would miss on the first paint); a vetoed open (the viewer
// kept a dirty edit, openFileView false) records nothing and repaints nothing; a real one enters Recent with
// the identity the relay carried, else the one cached for the sid, else none, and persists.
test("openHere, executed: the identity is cached before the open, a vetoed open records nothing, a real one enters Recent and persists", () => {
  const identity = { name: "web", color: { bg: "#123456", fg: "#ffffff" } };
  const { H, api } = liftPane({ openOk: false });
  api.openHere("/repo/notes-api/src/app.py", SID, identity);
  assert.deepEqual(H.opens, [["/repo/notes-api/src/app.py", SID, identity]], "the viewer was asked, and could already resolve the chip");
  assert.deepEqual(api.recent(), [], "the veto: nothing recorded");
  assert.deepEqual(H.writes, [], "nothing persisted");
  assert.equal(H.paints, 0, "nothing repainted");
  H.openOk = true;
  api.openHere("/repo/notes-api/src/app.py", SID, identity);
  assert.deepEqual(api.recent(), [{ path: "/repo/notes-api/src/app.py", sid: SID, identity, t: 777 }], "a real open enters Recent");
  assert.deepEqual(H.writes, [api.recent()], "and persists once");
  assert.equal(H.paints, 1, "and repaints the rows once");
  // a recent row re-opened carries its own identity; a relay with none falls to the identity cached for the sid
  api.openHere("/repo/notes-api/README.md", SID, null);
  assert.deepEqual(api.recent()[0], { path: "/repo/notes-api/README.md", sid: SID, identity, t: 777 }, "the cached identity");
  assert.equal(api.recent().length, 2);
  // no sid and no identity: a row with no chip, never an invented one
  api.openHere("/repo/notes-api/notes.md", null, null);
  assert.deepEqual(api.recent()[0], { path: "/repo/notes-api/notes.md", sid: null, identity: null, t: 777 });
  assert.equal(H.opens.length, 4);
  assert.equal(H.writes.length, 3); assert.equal(H.paints, 3);
});

test("recent files: recorded only on a REAL open, painted as re-open rows in the viewer's own dress, click-safe", () => {
  const openFn = SRC.split("function openHere(")[1].split("\n}")[0];
  assert.match(openFn, /if \(!openFileView\(path, sid, \{ frag \}\)\) return;[^\n]*\n\s*const known = /, "a dirty-edit veto records nothing");
  assert.match(openFn, /recent = rememberRecent\(recent, \{ path, sid, identity: known, t: Date\.now\(\) \}\);/);
  assert.match(SRC, /let recent: RecentFile\[\] = parseRecent\(readStore\(\)\);/, "persisted per browser");
  assert.match(SRC, /"No file open"/);
  // the rows wear the viewer's title-bar classes, so a path and its chip read as they do above an open file
  for (const cls of ['"fileview-name"', '"fileview-dir"', '"fileview-base"', '"fileview-sess"']) assert.ok(SRC.includes(cls), cls);
  assert.match(SRC, /sess\.replaceChildren\(\.\.\.hostNameNodes\(r\.identity\.name, r\.sid\)\)/);
  // delegated on the stable container (actions.ts): a repaint between mousedown and mouseup still lands
  assert.match(SRC, /delegate\(empty, \{\n\s*open: \(x\) => \{ const r = recent\[Number\(x\.dataset\.i\)\]; if \(r\) openHere\(r\.path, r\.sid, r\.identity\); \},/);
  assert.match(SRC, /row\.dataset\.act = "open"; row\.dataset\.i = String\(i\);/);
});

test("close returns to the empty state: the placeholder repaints on the viewer element's removal, never a hidden pane", () => {
  // closeFileView only removes #romp-fileview; the body's childList mutation IS the close event, and one
  // observer covers every open/close path (relay, recent row, the browser's rows and back, the close button, Escape, Reload)
  assert.match(SRC, /new MutationObserver\(onBodyChange\)\.observe\(document\.body, \{ childList: true \}\);/);
  assert.match(SRC, /function onBodyChange\(\): void \{\n\s*paint\(\);/, "the repaint rides the observer");
  // "open" is either surface: the viewer OR the browser, by element presence
  assert.match(SRC, /const open = surfaceUp\(\);\n\s*empty\.hidden = open;\n\s*if \(open\) return;/);
  assert.match(SRC, /function surfaceUp\(\): boolean \{\n\s*return !!\(document\.getElementById\("romp-fileview"\) \|\| document\.getElementById\("romp-filebrowse"\)\);\n\}/);
  assert.doesNotMatch(SRC, /setInterval|setTimeout/, "event-based, no polling");
});

// The close is also told to the SHELL: on a phone the viewFile relay switched tabs to show this pane, and
// closing the file would otherwise strand the person on the Files tab's recent list. The shell restores the
// tab the click came from, mobile only (kernel.py filesViewerClosed; tests/test_pane_state_broadcast.py).
test("the viewer's close EDGE posts filesViewerClosed up to the shell: once, framed only, never on an open-over-open", () => {
  assert.match(SRC, /let viewerUp = surfaceUp\(\);/);
  assert.match(SRC, /const up = surfaceUp\(\);\n\s*if \(viewerUp && !up && window\.parent !== window\) window\.parent\.postMessage\(\{ romp: "filesViewerClosed" \}, "\*"\);\n\s*viewerUp = up;/);
  assert.equal((SRC.match(/filesViewerClosed/g) || []).length, 2, "one post site (plus its comment)");
  // executed: onBodyChange as files.ts spells it, the observer's callback, driven by what is up in the document
  // (the viewer's wrap, the browser's overlay, by id) between calls
  const run = (states: string[][], framed: boolean): { posts: unknown[]; paints: number } => {
    const { H, api } = liftPane({ framed, up: new Set(states[0]) });   // the module's initial viewerUp reads the document at boot
    for (const up of states.slice(1)) { H.up = new Set(up); api.onBodyChange(); }
    return { posts: H.posted, paints: H.paints };
  };
  const V = "romp-fileview", B = "romp-filebrowse";
  assert.deepEqual(run([[], [V], []], true), { posts: [{ romp: "filesViewerClosed" }], paints: 2 }, "open then close: one notice, a repaint per mutation");
  assert.deepEqual(run([[], [V], [V], []], true).posts, [{ romp: "filesViewerClosed" }], "the Reload replace / open-over-open (still up when the observer runs) is not a close");
  assert.deepEqual(run([[], [V], [], [V], []], true).posts, [{ romp: "filesViewerClosed" }, { romp: "filesViewerClosed" }], "two closes: two notices");
  assert.deepEqual(run([[], []], true).posts, [], "an unrelated body mutation with nothing open posts nothing");
  assert.deepEqual(run([[V, B], [B], []], true).posts, [{ romp: "filesViewerClosed" }], "a viewer closing onto the listing beneath it is no edge; the listing's close is");
  assert.deepEqual(run([[], [V], []], false), { posts: [], paints: 2 }, "unframed (no shell): nothing to tell, the repaint still runs");
});

test("the pane-resident variant is keyed on the page's body class and lives ONLY in the pane sheet", () => {
  assert.ok(CSS.includes("body.fileview-pane #romp-fileview{position:relative;inset:auto;flex:1 1 auto;min-height:0;background:none}"));
  assert.ok(CSS.includes("body.fileview-pane .fileview{width:100%;height:100%;border:0;border-radius:0;box-shadow:none}"));
  // relative, not static: the viewer keeps its z-index, so a file opened from a browser row still paints
  // above the browser's fixed overlay (styles.css .filebrowse) and the back button has something to go back to
  assert.doesNotMatch(CSS, /position:static/);
  for (const sheet of ["styles.css", "feed.css"]) assert.doesNotMatch(read(sheet), /fileview-pane/, sheet + " stays a mirror");
  for (const sel of ["#files-empty{", ".fs-title{", ".fs-hint{", ".fs-recent{", ".fs-row{"]) assert.ok(CSS.includes(sel), sel);
  // print: the pane's own screen rules (a clipped, flexed page) are undone so a file prints as a document,
  // the way the base sheets' print block does for the modal
  assert.match(CSS, /@media print\{html,body\{height:auto;overflow:visible;display:block/);
});

test("wired and vocabulary-clean: esbuild entries, no federation import, no fleet identifiers or prose", () => {
  assert.match(ESBUILD, /"\.\.\/ui\/webview\/files\.ts",/);
  assert.match(ESBUILD, /"\.\.\/ui\/webview\/files-pane\.css",/);
  for (const [name, src] of [["files.ts", SRC], ["files-recent.ts", HELPERS], ["files-pane.css", CSS]] as const) {
    assert.doesNotMatch(src, /from "\.\/federation"/, name + ": importing it boots a second FederationManager");
    assert.doesNotMatch(src, /fleet/i, name + ": no fleet identifiers or prose");
  }
});

// executed: the pure half

test("rememberRecent: most recent first, one row per path + session, capped", () => {
  let list: RecentFile[] = [];
  list = rememberRecent(list, row("/repo/notes-api/src/app.py", SID));
  list = rememberRecent(list, row("/repo/notes-api/README.md", SID, "web", 2));
  assert.deepEqual(list.map((r) => r.path), ["/repo/notes-api/README.md", "/repo/notes-api/src/app.py"]);
  // a re-open moves the row up and refreshes it (a renamed session's new identity lands)
  list = rememberRecent(list, row("/repo/notes-api/src/app.py", SID, "web-2", 3));
  assert.deepEqual(list.map((r) => [r.path, r.identity!.name]), [["/repo/notes-api/src/app.py", "web-2"], ["/repo/notes-api/README.md", "web"]]);
  // the same path from ANOTHER session is another row: the chip is what tells them apart
  list = rememberRecent(list, row("/repo/notes-api/src/app.py", SID2, "api", 4));
  assert.equal(list.length, 3);
  assert.deepEqual(list[0].sid, SID2);
  // capped at RECENT_MAX, dropping the oldest
  for (let i = 0; i < RECENT_MAX + 3; i++) list = rememberRecent(list, row("/repo/notes-api/f" + i + ".txt", SID, "web", 10 + i));
  assert.equal(list.length, RECENT_MAX);
  assert.equal(list[0].path, "/repo/notes-api/f" + (RECENT_MAX + 2) + ".txt");
  assert.ok(!list.some((r) => r.path === "/repo/notes-api/README.md"), "the oldest rows fell off");
});

test("parseRecent tolerates junk: a corrupt store costs the list, never the pane", () => {
  assert.deepEqual(parseRecent(null), []);
  assert.deepEqual(parseRecent("not json"), []);
  assert.deepEqual(parseRecent('{"path":"/x"}'), [], "not an array");
  const raw = JSON.stringify([
    { path: "/repo/notes-api/a.md", sid: SID, identity: { name: "web", color: { bg: "#123456", fg: "#fff" } }, t: 5 },
    { path: "", sid: SID },                       // no path: skipped
    { sid: SID2 },                                // no path: skipped
    { path: "/repo/notes-api/b.md", sid: 42, identity: "web", t: "soon" },   // foreign fields normalise
    "junk", null,
  ]);
  const got = parseRecent(raw);
  assert.deepEqual(got, [
    { path: "/repo/notes-api/a.md", sid: SID, identity: { name: "web", color: { bg: "#123456", fg: "#fff" } }, t: 5 },
    { path: "/repo/notes-api/b.md", sid: null, identity: null, t: 0 },
  ]);
  // an overlong store is capped on read, so a bloated entry cannot grow the list past the cap
  const many = JSON.stringify(Array.from({ length: RECENT_MAX + 5 }, (_, i) => ({ path: "/p" + i, sid: null })));
  assert.equal(parseRecent(many).length, RECENT_MAX);
});

test("asIdentity validates the relayed identity to the chip's shape; anything else is no identity", () => {
  assert.deepEqual(asIdentity({ name: "web", color: { bg: "#123456", fg: "#ffffff" } }), { name: "web", color: { bg: "#123456", fg: "#ffffff" } });
  assert.deepEqual(asIdentity({ name: "TESTHOST:api", color: null }), { name: "TESTHOST:api", color: null }, "a remote session's prefixed name, uncolored");
  assert.deepEqual(asIdentity({ name: "web", color: { bg: 1 } }), { name: "web", color: null }, "a malformed colour is dropped, the name kept");
  assert.equal(asIdentity({ name: "" }), null);
  assert.equal(asIdentity({ color: { bg: "#123456", fg: "#fff" } }), null, "no name, no chip: never invented");
  assert.equal(asIdentity(null), null);
  assert.equal(asIdentity("web"), null);
});
