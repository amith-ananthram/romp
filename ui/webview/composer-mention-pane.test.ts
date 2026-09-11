// The @-mention card's DOM half, run for real. The mention block of setupComposer and the transcript's chip
// functions are sliced from render.ts by their banner comments, bundled with composer-mention.ts the way the
// webview is built, and driven in headless Chromium against a real textarea, so the browser decides what
// Ctrl+A, PageUp, Home, Ctrl+Z and a synthetic IME keystroke do to the caret, the selection and the undo
// stack. Skips by name when playwright or its browser is missing (md-sanitize-browser.test.ts's idiom; CI's
// test step runs before its Chromium install, so the browser cases skip there). Source pins hold only the
// wiring the slices cannot carry: the one clear helper every clear site calls, the keydown order, the roster
// hook at the top of renderTabs, the transcript hook, the selection menu's Copy, the chips' stylesheet rules.
// Synthetic names only (romp, rompdocs, web, api, TESTHOST; placeholder ids).
import { test, after } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const PKG = process.cwd();                                   // vscode-extension, where npm test runs
const UI = path.resolve(PKG, "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const STYLES = fs.readFileSync(path.join(UI, "styles.css"), "utf8");
const req = createRequire(path.join(PKG, "package.json"));   // runtime requires: esbuild must not bundle playwright

let pw: any = null;
try { pw = req("playwright"); } catch { pw = null; }

// ── the slices ──────────────────────────────────────────────────────────────────────────────────────────

/** The composer's mention block: from its banner to the prompt history's, inside setupComposer. */
function mentionBlock(): string {
  const a = RENDER.indexOf("  // ── @-MENTION autocomplete");
  const b = RENDER.indexOf("  // ── PROMPT HISTORY");
  assert.ok(a > 0 && b > a, "the mention block sits between the slash menu and the prompt history");
  return RENDER.slice(a, b);
}
/** The transcript side: the roster signature, the chip dress and markMentions, up to setupComposer's banner. */
function chipBlock(): string {
  const a = RENDER.indexOf('let mentionRosterSig = "";');
  const b = RENDER.indexOf("// Composer: Enter sends the message");
  assert.ok(a > 0 && b > a, "the chip functions sit just before setupComposer");
  return RENDER.slice(a, b);
}

/** Both slices, bundled as the webview is built (in memory), each wrapped with the closure it lives in:
 *  the block's free names are the composer's (ta, sessions, activeId, the stubs the card paints with), the
 *  chips' are the module's (sessions, views, CHIP_LABEL). Handed to the page as window.__mention. */
function bundle(): string {
  const esbuild = req("esbuild");
  const contents = `
import { MENTION_MAX_ROWS, mentionQuery, rankMentions, mentionMoreNote, mentionToken, insertMention, mentionKeyAction, mentionSegments } from "./composer-mention";
const CHIP_LABEL: any = { working: "Working", ready: "Ready", idle: "Idle", closed: "Closed", needsInput: "Blocked" };
const el = (tag: string, cls?: string) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
import { hostNameNodes, hostPartsNodes } from "./host-prefix";   // the real renderers: a remote chip's host must wear .host-prefix
const isProvisionalId = (id: string) => id.startsWith("prov:");
(window as any).__mention = {
  hostPartsNodes,   // the awaiting chip names its peer through this one (updateStatusline); the page builds that chip by hand
  mount(ta: HTMLTextAreaElement, env: any) {
    const sessions: Map<string, any> = env.sessions;
    let activeId: string | null = env.activeId ?? null;
    let composerManualH: number | null = 7;
    let refreshMentionCard: any = null, clearComposerBox: any = null;
    const updateSlash = () => { env.slashUpdates = (env.slashUpdates || 0) + 1; };
${mentionBlock()}
    return { updateMention, mentionKey, pickMention, closeMention, clearBox,
      refresh: () => refreshMentionCard(), clearViaHook: () => clearComposerBox(),
      setActive: (id: string | null) => { activeId = id; },
      state: () => ({ open: !!mPop, items: mItems.map((c) => c.name), sel: mSel, at: mAt, dismissedAt: mDismissedAt, more: mMore, manualH: composerManualH,
                      rows: mPop ? Array.from(mPop.children).map((r) => (r.classList.contains("sel") ? "*" : "") + (r.classList.contains("mention-more") ? "+" : "") + (r.querySelector(".mention-name")?.textContent ?? r.textContent)) : [] }) };
  },
  chips(env: any) {
    const sessions: Map<string, any> = env.sessions, views: Map<string, any> = env.views;
    let refreshMentionCard: any = env.refreshMentionCard || null;
${chipBlock()}
    return { markMentions, refreshMentionChips, remarkMentions, mentionRosterChanged, dressMentionChip };
  },
};
`;
  const r = esbuild.buildSync({
    stdin: { contents, resolveDir: UI, loader: "ts", sourcefile: "mention-probe.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(PKG, "node_modules")], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

// The page: the composer's textarea and a transcript root. The harness wires the composer's keydown order
// (the card's keys first, then an IME's Enter is left alone, then Cmd/Ctrl+Enter stages, then Enter sends; each
// clear goes through clearBox) and the input listener, as render.ts does; the source pins below hold that order.
const PAGE = `<!DOCTYPE html><html><head><meta charset=utf-8><style>
body { margin: 0; padding: 200px 20px 20px; } textarea { width: 400px; height: 80px; font: 14px monospace; }
.mention-pop { position: fixed; }
</style></head><body><div id="content"></div><textarea id="composer-input" rows="4"></textarea>
<script src="/dist/mention.js"></script>
<script>
window.__setup = (roster, activeId) => {
  const w = window;
  if (w.__api) w.__api.closeMention();
  document.getElementById("mention-pop")?.remove();
  const old = document.getElementById("composer-input");
  const ta = document.createElement("textarea"); ta.id = "composer-input"; ta.rows = 4; old.replaceWith(ta);
  const env = { sessions: new Map(roster.map((r) => [r.id, { id: r.id, name: r.name, color: r.color || null, status: { state: r.state || "ready" } }])),
                activeId: activeId || null, sent: [], staged: [] };
  const api = w.__mention.mount(ta, env);
  ta.addEventListener("keydown", (e) => {
    if (api.mentionKey(e)) return;
    if (e.key === "Enter" && (e.isComposing || e.keyCode === 229)) return;
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.shiftKey) { e.preventDefault(); if (ta.value.trim()) { env.staged.push(ta.value); api.clearBox(); } return; }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (ta.value.trim()) { env.sent.push(ta.value); api.clearBox(); } }
  });
  ta.addEventListener("input", () => api.updateMention());
  w.__api = api; w.__env = env; w.__ta = ta;
  ta.focus();
};
</script></body></html>`;

const SID = (n: number) => "1111111" + n + "-2222-4333-8444-555555555555";
const ROSTER = [
  { id: SID(1), name: "romp", color: { bg: "#3a7bd5", fg: "#ffffff" }, state: "working" },
  { id: SID(2), name: "rompdocs" },
  { id: SID(3), name: "web" },
  { id: "TESTHOST:" + SID(4), name: "TESTHOST:web" },
];

let browserP: Promise<any> | null = null;
const browser = () => (browserP ||= pw.chromium.launch());
after(async () => { if (browserP) { try { await (await browserP).close(); } catch { /* never launched */ } } });

type Harness = { page: any; state: () => Promise<any>; value: () => Promise<string>; env: () => Promise<any>; settle: () => Promise<unknown>; errors: string[] };

/** A fresh page on the harness, its composer mounted over `roster`, writing to `activeId`; null (and the test
 *  skipped, by name) when no playwright browser can be launched here. */
async function open(t: any, roster: unknown[] = ROSTER, activeId: string | null = null): Promise<Harness | null> {
  if (!pw) { t.skip("playwright is not installed under vscode-extension; the browser leg needs it (CI's test step runs before its Chromium install)"); return null; }
  let b: any;
  try { b = await browser(); }
  catch (e) { t.skip("no playwright browser on this machine; the browser leg needs one (CI's test step runs before its Chromium install): " + String((e as Error).message).split("\n")[0]); return null; }
  const js = bundle();
  const page = await b.newPage({ viewport: { width: 800, height: 600 } });
  const errors: string[] = [];
  page.on("pageerror", (e: Error) => errors.push(e.message));
  await page.route("http://romp.test/**", (route: any) => {
    const u = new URL(route.request().url());
    if (u.pathname === "/page") return route.fulfill({ contentType: "text/html", body: PAGE });
    if (u.pathname === "/dist/mention.js") return route.fulfill({ contentType: "application/javascript", body: js });
    return route.fulfill({ status: 404, body: "" });
  });
  await page.goto("http://romp.test/page");
  await page.evaluate(([r, a]: [unknown[], string | null]) => (window as any).__setup(r, a), [roster, activeId]);
  const state = () => page.evaluate(() => (window as any).__api.state());
  const value = () => page.evaluate(() => (window as any).__ta.value as string);
  const env = () => page.evaluate(() => { const e = (window as any).__env; return { sent: e.sent, staged: e.staged, slashUpdates: e.slashUpdates || 0 }; });
  // selectionchange is queued as a task after the selection moves: let it run before reading the card
  const settle = () => page.evaluate(() => new Promise((r) => setTimeout(r, 30)));
  return { page, state, value, env, settle, errors };
}

// ── the card, executed ──────────────────────────────────────────────────────────────────────────────────

test("in Chromium: @ plus letters opens the card, Enter inserts the token through the undo stack, and Ctrl+Z puts the typed query back", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("ask @ro");
  let s = await h.state();
  assert.equal(s.open, true);
  assert.deepEqual(s.rows, ["*romp", "rompdocs"], "prefix matches, the best highlighted, in the card's rows");
  assert.equal(await h.page.evaluate(() => document.getElementById("mention-pop")?.getAttribute("role")), "listbox");
  await h.page.keyboard.press("Enter");
  assert.equal(await h.value(), "ask @romp ", "the pick replaced the typed query with the plain token");
  assert.deepEqual((await h.env()).sent, [], "Enter with the card open picks; it never sends");
  s = await h.state();
  assert.equal(s.open, false);
  await h.page.keyboard.press("Control+z");
  assert.equal(await h.value(), "ask @ro", "the pick sits on the textarea's undo stack (a value assignment would have dropped it)");
  // (the undo restores the pre-command selection, "@ro" selected: a selection is not a caret, so the card stays closed)
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: Tab picks too, and a click on a row picks that row while the textarea keeps focus", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("@ro");
  await h.page.keyboard.press("Tab");
  assert.equal(await h.value(), "@romp ", "Tab picks the highlighted row");
  assert.equal(await h.page.evaluate(() => document.activeElement?.id), "composer-input", "focus never left the composer");
  await h.page.keyboard.type("and @ro");
  assert.deepEqual((await h.state()).rows, ["*romp", "rompdocs"]);
  await h.page.click(".mention-row[data-idx='1']");
  assert.equal(await h.value(), "@romp and @rompdocs ", "the clicked row, not the highlighted one");
  assert.equal(await h.page.evaluate(() => document.activeElement?.id), "composer-input", "a mousedown on the card does not take focus");
  assert.equal((await h.state()).open, false);
  assert.deepEqual((await h.env()).sent, []);
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: Ctrl+A with the card open closes it (a selection is not a caret), and Enter then sends the draft as typed, never a duplicate", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("ask @ro");
  assert.equal((await h.state()).open, true);
  await h.page.keyboard.press("Control+a");
  await h.settle();
  assert.equal((await h.state()).open, false, "selectionchange closed the card");
  await h.page.keyboard.press("Enter");
  assert.deepEqual((await h.env()).sent, ["ask @ro"], "the draft as typed: not 'ask @romp ask @ro'");
  assert.equal(await h.value(), "");
  // and the pick itself refuses a caret the card has not followed yet (selectionchange is asynchronous, so a
  // pick can land first): the token the caret ends must be the one the card is about
  await h.page.keyboard.type("ask @ro");
  const r = await h.page.evaluate(() => {
    const w = window as any; const ta = w.__ta as HTMLTextAreaElement;
    ta.setSelectionRange(0, 0);
    w.__api.pickMention({ id: "x", name: "romp" });   // the same task: mAt is still the stale token
    return { value: ta.value, open: w.__api.state().open };
  });
  assert.deepEqual(r, { value: "ask @ro", open: false }, "nothing inserted, the card closed");
  // and when the caret ends ANOTHER token than the card's: two tokens, the card about the second, the caret
  // moved to the end of the first before the card followed; the pick must not splice the first
  await h.page.keyboard.press("Control+a"); await h.page.keyboard.press("Backspace");
  await h.page.keyboard.type("@we @ro");
  assert.deepEqual((await h.state()).at, { start: 4, query: "ro" });
  const r2 = await h.page.evaluate(() => {
    const w = window as any; const ta = w.__ta as HTMLTextAreaElement;
    ta.setSelectionRange(3, 3);   // the caret now ends "@we": a token, but not the card's
    w.__api.pickMention({ id: "x", name: "romp" });
    return { value: ta.value, open: w.__api.state().open };
  });
  assert.deepEqual(r2, { value: "@we @ro", open: false }, "a token that is not the card's: nothing inserted, the card closed");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: PageUp on a two-line draft closes the card, and Enter sends the two lines unchanged", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("line one that is long enough");
  await h.page.keyboard.press("Shift+Enter");
  await h.page.keyboard.type("ask @ro");
  assert.equal((await h.state()).open, true);
  await h.page.keyboard.press("PageUp");
  await h.settle();
  assert.equal((await h.state()).open, false);
  await h.page.keyboard.press("Enter");
  assert.deepEqual((await h.env()).sent, ["line one that is long enough\nask @ro"]);
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: a clear (Cmd/Ctrl+Enter stages, the module-level hook cancelComposerEdit uses) closes the card; Enter then sends nothing and inserts nothing", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("ask @ro");
  assert.equal((await h.state()).open, true);
  await h.page.keyboard.press("Control+Enter");
  let s = await h.state();
  assert.equal(s.open, false, "the stage's clear went through clearBox, which the card sees");
  assert.equal(s.manualH, null, "the drag height snapped back with it");
  assert.deepEqual((await h.env()).staged, ["ask @ro"]);
  await h.page.keyboard.press("Enter");
  assert.equal(await h.value(), "", "a stale '@romp ' must not appear over the emptied composer");
  assert.deepEqual((await h.env()).sent, []);
  // the module-level hook (cancelComposerEdit's path) is the same helper, and the slash menu is refreshed too
  await h.page.keyboard.type("ask @ro");
  assert.equal((await h.state()).open, true);
  const before = (await h.env()).slashUpdates;
  await h.page.evaluate(() => (window as any).__api.clearViaHook());
  s = await h.state();
  assert.equal(s.open, false);
  assert.equal(await h.value(), "");
  assert.equal((await h.env()).slashUpdates, before + 1);
  // the other direction: text put INTO the box by code, no input event (a queued message taken back for
  // editing). The assignment lands the caret at the end and selectionchange fires, so the card follows a
  // draft that ends in an @query as it follows typing; that path needs no call of its own
  await h.page.evaluate(() => { (window as any).__ta.value = "ask @ro"; });
  await h.settle();
  assert.deepEqual((await h.state()).rows, ["*romp", "rompdocs"], "a fill by code opens the card for the token the caret now ends");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: an IME composition's keys are not the card's, and the card waits for compositionend to re-read the composer", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("@ro");
  assert.equal((await h.state()).open, true);
  const r = await h.page.evaluate(() => {
    const w = window as any; const ta = w.__ta as HTMLTextAreaElement;
    // the card's handler alone: the harness's own Enter-to-send is not what this test is about
    const fire = (key: string) => { const e = new KeyboardEvent("keydown", { key, isComposing: true, cancelable: true, bubbles: true }); return w.__api.mentionKey(e) || e.defaultPrevented; };
    const enter = fire("Enter"), down = fire("ArrowDown");
    const afterKeys = { ...w.__api.state(), value: ta.value };
    // and through the composer's whole keydown order: the commit Enter, bare or with Ctrl, neither sends nor stages
    const commits = [new KeyboardEvent("keydown", { key: "Enter", isComposing: true, cancelable: true, bubbles: true }),
                     new KeyboardEvent("keydown", { key: "Enter", isComposing: true, ctrlKey: true, cancelable: true, bubbles: true })];
    for (const k of commits) ta.dispatchEvent(k);
    const afterCommit = { prevented: commits.some((k) => k.defaultPrevented), value: ta.value, sent: w.__env.sent.slice(), staged: w.__env.staged.slice(), open: w.__api.state().open };
    ta.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    ta.value = "@rom"; ta.setSelectionRange(4, 4); ta.dispatchEvent(new Event("input", { bubbles: true }));
    const composing = w.__api.state();
    ta.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
    const ended = w.__api.state();
    return { enter, down, afterKeys, afterCommit, composing, ended };
  });
  assert.equal(r.enter, false, "Enter mid-composition commits the IME's text, not a pick");
  assert.equal(r.down, false, "ArrowDown mid-composition walks the IME's candidates");
  assert.equal(r.afterKeys.open, true); assert.equal(r.afterKeys.sel, 0); assert.equal(r.afterKeys.value, "@ro");
  assert.deepEqual(r.afterCommit, { prevented: false, value: "@ro", sent: [], staged: [], open: true }, "the commit Enter is the IME's: nothing sent, nothing staged, the browser's default kept");
  assert.equal(r.composing.at.query, "ro", "the interim text is the IME's: the card did not re-read it");
  assert.equal(r.ended.at.query, "rom", "compositionend re-reads the composer");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: a roster re-rank under the open card keeps the highlight on the SESSION, and a rename reaches the card's rows; a viewer, a closed session and a provisional tab are not listed", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("@ro");
  await h.page.keyboard.press("ArrowDown");
  assert.deepEqual((await h.state()).rows, ["romp", "*rompdocs"]);
  // a peer renames romp to zromp: the rows re-rank, the highlight stays on rompdocs (a highlight kept by row
  // index would land on zromp)
  await h.page.evaluate((sid: string) => { const w = window as any; w.__env.sessions.get(sid).name = "zromp"; w.__api.refresh(); }, SID(1));
  assert.deepEqual((await h.state()).rows, ["*rompdocs", "zromp"]);
  await h.page.keyboard.press("Enter");
  assert.equal(await h.value(), "@rompdocs ");
  // the highlighted session itself renamed: the highlight follows it, and the pick inserts the NEW name
  await h.page.keyboard.press("Control+a"); await h.page.keyboard.press("Backspace");
  await h.page.evaluate((sid: string) => { (window as any).__env.sessions.get(sid).name = "romp"; }, SID(1));
  await h.page.keyboard.type("@ro");
  assert.deepEqual((await h.state()).rows, ["*romp", "rompdocs"]);
  await h.page.evaluate((sid: string) => { const w = window as any; w.__env.sessions.get(sid).name = "zromp"; w.__api.refresh(); }, SID(1));
  assert.deepEqual((await h.state()).rows, ["rompdocs", "*zromp"]);
  await h.page.keyboard.press("Enter");
  assert.equal(await h.value(), "@zromp ", "never the stale name postal would no longer resolve");
  // the highlighted session left the roster: the first row is highlighted
  await h.page.keyboard.press("Control+a"); await h.page.keyboard.press("Backspace");
  await h.page.keyboard.type("@ro");
  await h.page.keyboard.press("ArrowDown");
  assert.deepEqual((await h.state()).rows, ["rompdocs", "*zromp"]);
  await h.page.evaluate((sid: string) => { const w = window as any; w.__env.sessions.delete(sid); w.__api.refresh(); }, SID(1));
  assert.deepEqual((await h.state()).rows, ["*rompdocs"]);
  // a subagent viewer is a tab, not a session: its row (sub set, named by the agent's description) takes no mail
  await h.page.evaluate((sid: string) => { const w = window as any; w.__env.sessions.set(sid + "/agent/abc", { id: sid + "/agent/abc", name: "roster walk", color: null, status: { state: "idle" }, sub: { parentId: sid, agentId: "abc" } }); w.__api.refresh(); }, SID(3));
  assert.deepEqual((await h.state()).rows, ["*rompdocs"], "the viewer's name is not listed");
  // a closed session and a provisional tab are not listed: neither can take mail
  await h.page.evaluate(() => { const w = window as any; w.__env.sessions.set("prov:1", { id: "prov:1", name: "rompnew", color: null, status: { state: "ready" } }); w.__env.sessions.get("11111112-2222-4333-8444-555555555555").status = { state: "closed" }; w.__api.refresh(); });
  assert.equal((await h.state()).open, false, "nothing left to list: the card closed");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: Escape latches the card closed for that @ until it is deleted; a caret move away and back, or more letters, do not reopen it", async (t) => {
  const h = await open(t); if (!h) return;
  await h.page.keyboard.type("ask @we");
  assert.equal((await h.state()).open, true);
  await h.page.keyboard.press("Escape");
  let s = await h.state();
  assert.equal(s.open, false); assert.equal(s.dismissedAt, 4);
  await h.page.keyboard.press("Home"); await h.settle();
  s = await h.state();
  assert.equal(s.dismissedAt, 4, "the caret left the token; the latch holds while its @ does");
  await h.page.keyboard.press("End"); await h.settle();
  s = await h.state();
  assert.equal(s.open, false, "back at the token's end the card the user dismissed stays dismissed");
  await h.page.keyboard.type("b");
  assert.equal((await h.state()).open, false, "more letters of the same token do not re-pop it");
  // the query backspaced away to the bare "@", then retyped: the same "@", still dismissed
  for (let i = 0; i < 3; i++) await h.page.keyboard.press("Backspace");   // "ask @"
  assert.equal(await h.value(), "ask @");
  assert.equal((await h.state()).dismissedAt, 4, "the latch holds on the bare @");
  await h.page.keyboard.type("w");
  s = await h.state();
  assert.equal(s.open, false, "the query typed again does not reopen the card");
  assert.equal(s.dismissedAt, 4);
  // an edit BEFORE the "@" moves it, and the latch follows
  await h.page.keyboard.press("Home"); await h.settle();
  await h.page.keyboard.type("so ");
  assert.equal(await h.value(), "so ask @w");
  assert.equal((await h.state()).dismissedAt, 7, "the latch moved with its @");
  await h.page.keyboard.press("End"); await h.settle();
  assert.equal((await h.state()).open, false, "back at the token's end, still dismissed");
  await h.page.keyboard.press("Home"); await h.settle();
  for (let i = 0; i < 3; i++) await h.page.keyboard.press("Delete");   // "ask @w"
  assert.equal(await h.value(), "ask @w");
  assert.equal((await h.state()).dismissedAt, 4, "a deletion before the @ moves it back");
  await h.page.keyboard.press("End"); await h.settle();
  assert.equal((await h.state()).open, false);
  for (let i = 0; i < 2; i++) await h.page.keyboard.press("Backspace");   // "ask "
  assert.equal(await h.value(), "ask ");
  assert.equal((await h.state()).dismissedAt, -1, "the @ is gone: the latch re-arms");
  await h.page.keyboard.type("@we");
  assert.equal((await h.state()).open, true, "a fresh @ opens again");
  // a selection over the token BEFORE the latched "@" deleted in one step: the edit ends at the caret, so the
  // latch moves by what went ("@w " less), where a prefix/suffix diff alone would read the deletion as the
  // later "@w" and drop the latch
  await h.page.keyboard.press("Control+a"); await h.page.keyboard.press("Backspace");
  await h.page.keyboard.type("@w @we");
  await h.page.keyboard.press("Escape");
  assert.equal((await h.state()).dismissedAt, 3);
  await h.page.keyboard.press("Home"); await h.settle();
  for (let i = 0; i < 3; i++) await h.page.keyboard.press("Shift+ArrowRight");
  await h.page.keyboard.press("Delete");
  assert.equal(await h.value(), "@we");
  assert.equal((await h.state()).dismissedAt, 0, "the latch followed its @ to the start");
  await h.page.keyboard.press("End"); await h.settle();
  assert.equal((await h.state()).open, false, "the same dismissed token, still dismissed");
  // the caret lands on the latched token while a card for ANOTHER token is up (a click from "@ro" back onto
  // "@we"): that card comes down; the latch is not a bare no-op
  await h.page.keyboard.press("Control+a"); await h.page.keyboard.press("Backspace");
  await h.page.keyboard.type("ask @we");
  await h.page.keyboard.press("Escape");
  await h.page.keyboard.type(" @ro");
  assert.deepEqual((await h.state()).rows, ["*romp", "rompdocs"], "a card for the second token");
  await h.page.evaluate(() => { (window as any).__ta.setSelectionRange(7, 7); });   // the end of "@we"
  await h.settle();
  s = await h.state();
  assert.equal(s.open, false, "the @ro card closed when the caret reached the dismissed @we");
  assert.equal(s.dismissedAt, 4);
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: past twelve matches the last row counts the rest and takes no pick; the token follows the recipient's kernel", async (t) => {
  const many = Array.from({ length: 14 }, (_, i) => ({ id: SID(i + 1) + i, name: "romp-" + String(i + 1).padStart(2, "0") }));
  const h = await open(t, [...many, ROSTER[3]], SID(9)); if (!h) return;
  await h.page.keyboard.type("@romp");
  const s = await h.state();
  assert.equal(s.items.length, 12);
  assert.equal(s.rows[12], "+2 more, keep typing", "the cap is visible, as the guide says");
  assert.equal(s.rows.length, 13);
  await h.page.keyboard.press("ArrowUp");
  assert.equal((await h.state()).sel, 11, "the arrows wrap over the rows only, never onto the count");
  const picked = await h.page.evaluate(() => {
    const more = document.querySelector(".mention-more") as HTMLElement;
    more.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    return (window as any).__ta.value;
  });
  assert.equal(picked, "@romp", "a mousedown on the count inserts nothing");
  // writing to a LOCAL session, a remote peer goes in as the display name this kernel knows it by
  await h.page.keyboard.press("Control+a"); await h.page.keyboard.press("Backspace");
  await h.page.keyboard.type("@TESTHOST:w");
  await h.page.keyboard.press("Enter");
  assert.equal(await h.value(), "@TESTHOST:web ");
  // writing to a REMOTE session, every name goes in bare (composer-mention.ts, mentionToken)
  await h.page.evaluate(() => (window as any).__api.setActive("TESTHOST:" + "99999999-2222-4333-8444-555555555555"));
  await h.page.keyboard.press("Control+a"); await h.page.keyboard.press("Backspace");
  await h.page.keyboard.type("@TESTHOST:w");
  await h.page.keyboard.press("Enter");
  assert.equal(await h.value(), "@web ");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

// ── the transcript's chips, executed ────────────────────────────────────────────────────────────────────

test("in Chromium: chips are exact-name only, keyed by session id, re-dressed on a state, color or name change, never a viewer's, and a name that joins the roster later gets its chip without nesting", async (t) => {
  const h = await open(t); if (!h) return;
  const r = await h.page.evaluate(([api, webId]: [string, string]) => {
    const w = window as any;
    const sessions = new Map<string, any>([[api, { id: api, name: "api", color: { bg: "#d5643a", fg: "#ffffff" }, status: { state: "working" } }]]);
    // a subagent viewer under api whose description is the one word "web": a tab, not a session, so "@web" is
    // not its name (and it must not stand in for web's name in the roster's name set: web's frame is NEW below)
    sessions.set(api + "/agent/abc", { id: api + "/agent/abc", name: "web", color: null, status: { state: "idle" }, sub: { parentId: api, agentId: "abc" } });
    const root = document.getElementById("content")!;
    const thread = document.createElement("div"); root.appendChild(thread);
    const views = new Map([["v1", { el: thread }]]);
    let refreshed = 0;
    const chips = w.__mention.chips({ sessions, views, refreshMentionCard: () => { refreshed++; } });
    const bubble = document.createElement("div"); bubble.className = "user-bubble md";
    bubble.innerHTML = "<p>ask @web and @api and @API, not <code>@api</code> or <a href='#'>@api</a></p>";
    thread.appendChild(bubble);
    chips.markMentions(bubble);
    const snap = () => Array.from(bubble.querySelectorAll(".mention-chip")).map((c) => {
      const e = c as HTMLElement;
      return { text: e.textContent, token: e.dataset.token, sid: e.dataset.sid, title: e.title, bg: e.style.getPropertyValue("--chip-bg") };
    });
    const out: any = {};
    out.first = snap(); out.marked = bubble.dataset.mentions;
    chips.mentionRosterChanged();                                   // the roster as it is: the signature is taken
    const taken = refreshed;
    // web's frame lands after the bubble rendered: a NEW name re-marks the bubble
    sessions.set(webId, { id: webId, name: "web", color: null, status: { state: "ready" } });
    chips.mentionRosterChanged();
    out.afterWeb = snap(); out.nested = bubble.querySelectorAll(".mention-chip .mention-chip").length; out.refreshed1 = refreshed - taken;
    out.text = bubble.textContent;
    // api changes state and color: the chip follows without a re-render
    const a = sessions.get(api); a.status = { state: "ready" }; a.color = { bg: "#112233", fg: "#ffffff" };
    chips.mentionRosterChanged();
    out.afterState = snap();
    // a rename: the chip's text stays as typed, its title names the session as it is now
    a.name = "api2"; chips.mentionRosterChanged();
    out.afterRename = snap();
    // an unchanged roster does nothing (no refresh of the card)
    const before = refreshed; chips.mentionRosterChanged(); out.idle = refreshed - before;
    // the session closes; its struck tab keeps it in the map: the chip reads Closed in its color
    a.status = { state: "closed" }; chips.mentionRosterChanged();
    out.afterClosed = snap();
    // a viewer that carries the name is no namesake: the chip stays on the closed session
    sessions.set(api + "/agent/v", { id: api + "/agent/v", name: "api", color: { bg: "#000000", fg: "#ffffff" }, status: { state: "idle" }, sub: { parentId: api, agentId: "v" } });
    chips.mentionRosterChanged();
    out.afterViewer = snap();
    // a live session takes the name the text carries while the closed one is still in the map: the chip is
    // re-keyed to it now, not when the closed tab is dismissed (which says nothing about the mention)
    sessions.set("new-1", { id: "new-1", name: "api", color: { bg: "#445566", fg: "#000000" }, status: { state: "working" } });
    chips.mentionRosterChanged();
    out.afterNamesake = snap(); out.closedStillHeld = sessions.has(api);
    // the closed tab is dismissed: nothing about the mention changed, so nothing moves
    sessions.delete(api); chips.mentionRosterChanged();
    out.afterDismiss = snap();
    // the session is gone from the map: the chip reads Closed and keeps its color
    sessions.delete("new-1"); chips.mentionRosterChanged();
    out.afterGone = snap();
    // a new session takes the name the text carries: the chip is re-keyed to it
    sessions.set("new-2", { id: "new-2", name: "api", color: { bg: "#667788", fg: "#000000" }, status: { state: "ready" } });
    chips.mentionRosterChanged();
    out.afterNew = snap();
    // a session on another kernel, as this viewer holds it ("host:name", "host:uuid"): the chip wears the house
    // idiom the awaiting chip wears, the host as .host-prefix and only the NAME in the identity colour
    sessions.set("TESTHOST:" + webId, { id: "TESTHOST:" + webId, name: "TESTHOST:web", color: { bg: "#8899aa", fg: "#000000" }, status: { state: "working" } });
    const far = document.createElement("div"); far.className = "user-bubble md";
    far.innerHTML = "<p>ping @TESTHOST:web</p>"; thread.appendChild(far);
    chips.markMentions(far);
    const fc = far.querySelector(".mention-chip") as HTMLElement;
    const hp = fc.querySelector(".host-prefix");
    out.remote = { text: fc.textContent, token: fc.dataset.token, sid: fc.dataset.sid, title: fc.title, bg: fc.style.getPropertyValue("--chip-bg"),
                   host: hp ? hp.textContent : null, nameNode: fc.lastChild && fc.lastChild.nodeType === 3 ? fc.lastChild.textContent : null };
    return out;
  }, [SID(2), SID(3)]);
  assert.deepEqual(r.first, [{ text: "api", token: "@api", sid: SID(2), title: "api · Working", bg: "#d5643a" }],
    "the exact name only: @API is not a name postal takes, @web names nothing yet (a viewer's description is not a name), the code span and the link are left alone; the chip reads the bare name, the token as typed rides data-token, the identity colour is the chip's --chip-bg (its text colour: the sheet paints it on the awaiting chip's dark backing)");
  assert.equal(r.marked, "1", "the bubble is marked for a later re-mark");
  assert.deepEqual(r.afterWeb.map((c: any) => c.text), ["web", "api"], "web's chip arrived with its frame");
  assert.equal(r.nested, 0, "the existing chip was not wrapped again");
  assert.equal(r.text, "ask web and api and @API, not @api or @api", "the chips read the bare names (the user 2026-09-10); everything outside a chip reads as typed");
  assert.equal(r.refreshed1, 1, "the open card was re-ranked for the roster change");
  assert.equal(r.afterState[1].title, "api · Ready"); assert.equal(r.afterState[1].bg, "#112233");
  assert.equal(r.afterRename[1].title, "api2 · Ready"); assert.equal(r.afterRename[1].text, "api"); assert.equal(r.afterRename[1].token, "@api");
  assert.equal(r.idle, 0, "the same roster twice: nothing re-ranked");
  assert.deepEqual(r.afterClosed[1], { text: "api", token: "@api", sid: SID(2), title: "api2 · Closed", bg: "#112233" }, "closed, no namesake: the chip stays keyed to it");
  assert.deepEqual(r.afterViewer, r.afterClosed, "a viewer named api is not a session: no re-key");
  assert.equal(r.closedStillHeld, true, "the closed session was still in the map when the namesake arrived");
  assert.deepEqual(r.afterNamesake[1], { text: "api", token: "@api", sid: "new-1", title: "api · Working", bg: "#445566" }, "re-keyed on the namesake going live, not on the closed tab's dismissal");
  assert.deepEqual(r.afterDismiss, r.afterNamesake, "the dismissal moved nothing");
  assert.equal(r.afterGone[1].title, "api · Closed"); assert.equal(r.afterGone[1].bg, "#445566"); assert.equal(r.afterGone[1].sid, "new-1");
  assert.equal(r.afterNew[1].sid, "new-2"); assert.equal(r.afterNew[1].title, "api · Ready"); assert.equal(r.afterNew[1].bg, "#667788");
  assert.deepEqual(r.remote, { text: "TESTHOST:web", token: "@TESTHOST:web", sid: "TESTHOST:" + SID(3), title: "TESTHOST:web · Working", bg: "#8899aa", host: "TESTHOST:", nameNode: "web" },
    "a remote session's chip: the host as .host-prefix (its own quiet colour), the bare name as the text node the identity colour paints, the whole host:name as textContent for the roster re-key");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

// ── copying a chip, executed ────────────────────────────────────────────────────────────────────────────

test("in Chromium: copying a selection that holds a chip copies the @name as typed; a selection with no whole chip is left to the browser", async (t) => {
  const h = await open(t); if (!h) return;
  const r = await h.page.evaluate(([api, webId]: [string, string]) => {
    const w = window as any;
    const sessions = new Map<string, any>([
      [api, { id: api, name: "api", color: { bg: "#d5643a", fg: "#ffffff" }, status: { state: "working" } }],
      [webId, { id: webId, name: "web", color: null, status: { state: "ready" } }],
      ["TESTHOST:" + webId, { id: "TESTHOST:" + webId, name: "TESTHOST:web", color: { bg: "#8899aa", fg: "#000000" }, status: { state: "working" } }],
    ]);
    const root = document.getElementById("content")!;
    const thread = document.createElement("div"); root.appendChild(thread);
    const chips = w.__mention.chips({ sessions, views: new Map([["v1", { el: thread }]]), refreshMentionCard: null });
    const bubble = document.createElement("div"); bubble.className = "user-bubble md";
    bubble.innerHTML = "<p>ask @web and @api, not <code>@api</code></p>";
    thread.appendChild(bubble); chips.markMentions(bubble);
    const far = document.createElement("div"); far.className = "user-bubble md";
    far.innerHTML = "<p>ping @TESTHOST:web</p>";
    thread.appendChild(far); chips.markMentions(far);
    w.__ta.blur();                                    // __setup focused the composer; the selection goes to the transcript
    const sel = document.getSelection()!;
    // the browser's copy as a synthetic event with its own clipboard: what a listener writes, or nothing at all
    const copy = (target: Element) => {
      const dt = new DataTransfer();
      const ev = new ClipboardEvent("copy", { clipboardData: dt, bubbles: true, cancelable: true });
      target.dispatchEvent(ev);
      return { prevented: ev.defaultPrevented, text: dt.getData("text/plain"), html: dt.getData("text/html") };
    };
    const select = (node: Node, start: number, end: number) => {
      sel.removeAllRanges(); const rg = document.createRange(); rg.setStart(node, start); rg.setEnd(node, end); sel.addRange(rg);
    };
    const out: any = {};
    sel.selectAllChildren(bubble);
    out.selBefore = sel.toString();
    out.whole = copy(bubble);
    out.selAfter = sel.toString(); out.textAfter = bubble.textContent;
    out.chipsAfter = Array.from(bubble.querySelectorAll(".mention-chip")).map((c) => [c.textContent, (c as HTMLElement).dataset.token]);
    // the code span alone: no chip in the selection
    const code = bubble.querySelector("code")!;
    sel.removeAllRanges(); const rc = document.createRange(); rc.selectNodeContents(code); sel.addRange(rc);
    out.code = copy(bubble);
    // the double-clicked word: the api chip's own text node, edge to edge
    const apiText = bubble.querySelectorAll(".mention-chip")[1].firstChild as Text;
    select(apiText, 0, 3);
    out.word = copy(bubble);
    const rw = sel.getRangeAt(0);
    out.wordRange = [rw.startContainer === apiText, rw.startOffset, rw.endContainer === apiText, rw.endOffset, sel.toString()];
    // letters inside the chip: part of a name is not a mention
    select(apiText, 1, 3);
    out.part = copy(bubble);
    out.partSel = sel.toString();
    // a remote session's chip: its first text is the host prefix
    sel.selectAllChildren(far);
    out.remote = copy(far);
    out.remoteAfter = far.textContent;
    // a copy inside the composer: the document's selection holds no chip
    const ta = w.__ta as HTMLTextAreaElement;
    ta.value = "ask @api"; ta.focus(); ta.select();
    out.composer = copy(ta);
    out.composerSel = [ta.selectionStart, ta.selectionEnd];
    return out;
  }, [SID(2), SID(3)]);
  assert.equal(r.whole.prevented, true, "the copy over a chip is the listener's");
  assert.equal(r.whole.text, "ask @web and @api, not @api", "every chip as typed, the code span as rendered");
  assert.match(r.whole.html, />@web<\/span>/, "the rich flavour carries the @ on the chip's text too");
  assert.match(r.whole.html, />@api<\/span>/); assert.match(r.whole.html, /<code>@api<\/code>/, "the code span as rendered");
  assert.match(r.whole.html, /class="mention-chip"/, "the chip's class travels");
  assert.doesNotMatch(r.whole.html, /title=|data-/, "the chip's hover title and its ids do not");
  assert.equal(r.textAfter, "ask web and api, not @api", "the chips read the bare names again");
  assert.deepEqual(r.chipsAfter, [["web", "@web"], ["api", "@api"]], "the chips' text and token are as they were");
  assert.equal(r.selBefore, "ask web and api, not @api");
  assert.equal(r.selAfter, r.selBefore, "the selection is where it was");
  assert.deepEqual(r.code, { prevented: false, text: "", html: "" }, "no chip in the selection: the browser's own copy");
  assert.deepEqual([r.word.prevented, r.word.text], [true, "@api"], "a double-clicked chip word is the whole chip");
  assert.deepEqual(r.wordRange, [true, 0, true, 3, "api"], "the range's offsets are back where the double-click put them");
  assert.deepEqual(r.part, { prevented: false, text: "", html: "" }, "letters inside a chip are not a mention");
  assert.equal(r.partSel, "pi");
  assert.deepEqual([r.remote.prevented, r.remote.text], [true, "ping @TESTHOST:web"], "a remote session's token, host and all");
  assert.match(r.remote.html, /class="host-prefix">@TESTHOST:<\/span>web<\/span>/, "in the rich flavour the @ sits on the host prefix, the chip's first text");
  assert.equal(r.remoteAfter, "ping TESTHOST:web");
  assert.deepEqual(r.composer, { prevented: false, text: "", html: "" }, "a copy inside the composer is the browser's");
  assert.deepEqual(r.composerSel, [0, 8]);
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

test("in Chromium: a chip for a session whose host is down copies with no title on its host prefix either", async (t) => {
  const h = await open(t); if (!h) return;
  const r = await h.page.evaluate((webId: string) => {
    const w = window as any;
    // the federation manager's published state, as host-prefix.ts reads it (globalThis.__rompFed): TESTHOST
    // unreachable, no dial in flight, never reached. Every other case in this file leaves it unset, so no chip
    // there wears the down mark; the page is this test's own
    w.__rompFed = { down: () => ["TESTHOST"], dialing: () => false, lastSeen: () => 0 };
    const sessions = new Map<string, any>([["TESTHOST:" + webId, { id: "TESTHOST:" + webId, name: "TESTHOST:web", color: { bg: "#8899aa", fg: "#000000" }, status: { state: "working" } }]]);
    const root = document.getElementById("content")!;
    const thread = document.createElement("div"); root.appendChild(thread);
    const chips = w.__mention.chips({ sessions, views: new Map([["v1", { el: thread }]]), refreshMentionCard: null });
    const bubble = document.createElement("div"); bubble.className = "user-bubble md";
    bubble.innerHTML = "<p>ping @TESTHOST:web</p>"; thread.appendChild(bubble); chips.markMentions(bubble);
    w.__ta.blur();                                    // __setup focused the composer; the selection goes to the transcript
    const prefix = bubble.querySelector<HTMLElement>(".mention-chip .host-prefix")!;
    const before = [prefix.className, prefix.title];
    const sel = document.getSelection()!;
    sel.selectAllChildren(bubble);
    const dt = new DataTransfer();
    const ev = new ClipboardEvent("copy", { clipboardData: dt, bubbles: true, cancelable: true });
    bubble.dispatchEvent(ev);
    return { before, prevented: ev.defaultPrevented, text: dt.getData("text/plain"), html: dt.getData("text/html"),
             after: [prefix.className, prefix.title], textAfter: bubble.textContent };
  }, SID(4));
  assert.equal(r.before[0], "host-prefix off", "the stub took: the chip's host prefix wears the down mark");
  assert.match(r.before[1], /^TESTHOST is disconnected\./, "and the live reconnect note as its own title");
  assert.deepEqual([r.prevented, r.text], [true, "ping @TESTHOST:web"], "the copy is the listener's, the token host and all");
  assert.match(r.html, /class="host-prefix off">@TESTHOST:<\/span>web<\/span>/, "the mark's class travels, as the chip's does");
  assert.doesNotMatch(r.html, /title=|data-/, "the prefix's note is dropped with the chip's own title: a paste carries nothing about the host's link");
  assert.deepEqual(r.after, r.before, "the live chip still wears its mark and its note");
  assert.equal(r.textAfter, "ping TESTHOST:web");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

// ── the chips' host prefix, on the real sheet ───────────────────────────────────────────────────────────

/** A computed colour as [r, g, b, a]; a hex token the same way. */
function rgba(v: string): [number, number, number, number] {
  const m = v.trim().match(/^rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)$/);
  if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  const hex = v.trim().match(/^#([0-9a-f]{6})$/i);
  assert.ok(hex, "a colour: " + v);
  const x = hex![1];
  return [parseInt(x.slice(0, 2), 16), parseInt(x.slice(2, 4), 16), parseInt(x.slice(4, 6), 16), 1];
}
/** `top` composited over an opaque `under` (alpha blending, the theme-parity idiom). */
function over(top: [number, number, number, number], under: [number, number, number, number]): [number, number, number, number] {
  return [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat([1]) as [number, number, number, number];
}
function lum(c: [number, number, number, number]): number {
  const ch = (x: number) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
  return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2]);
}
function contrast(a: [number, number, number, number], b: [number, number, number, number]): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
/** The prefix's contrast on its chip: the prefix over the chip's backing over the surface the chip sits on over the page. */
function prefixContrast(c: { prefix: string; backing: string; under: string; page: string }): number {
  const ground = over(rgba(c.backing), over(rgba(c.under), rgba(c.page)));
  return contrast(over(rgba(c.prefix), ground), ground);
}

test("in Chromium: a remote chip's host: prefix reads on the shared dark backing in both themes, on the user bubble and on the awaiting chip", async (t) => {
  const h = await open(t); if (!h) return;
  // the real sheet, so the cascade decides the colour; its @import lines are the bundler's to resolve, not the browser's
  // (a sheet that fails to load one makes addStyleTag reject with a bare Event, not a message)
  await h.page.addStyleTag({ content: STYLES.replace(/^@import [^\n]*\n/gm, "") });
  const r = await h.page.evaluate((webId: string) => {
    const w = window as any;
    const sessions = new Map<string, any>([["TESTHOST:" + webId, { id: "TESTHOST:" + webId, name: "TESTHOST:web", color: { bg: "#8899aa", fg: "#000000" }, status: { state: "working" } }]]);
    const root = document.getElementById("content")!;
    const thread = document.createElement("div"); root.appendChild(thread);
    const chips = w.__mention.chips({ sessions, views: new Map([["v1", { el: thread }]]), refreshMentionCard: null });
    const bubble = document.createElement("div"); bubble.className = "user-bubble md";
    bubble.innerHTML = "<p>ping @TESTHOST:web</p>"; thread.appendChild(bubble); chips.markMentions(bubble);
    // the awaiting chip's peer name as updateStatusline builds it: the identity colour inline on the name, the host through hostPartsNodes
    const chip = document.createElement("button"); chip.className = "chip chip-awaitingBg chip-btn"; chip.append("Awaiting ");
    const nm = document.createElement("span"); nm.className = "chip-peer-name"; nm.style.color = "#8899aa";
    nm.replaceChildren(...w.__mention.hostPartsNodes("TESTHOST", "web")); chip.appendChild(nm); root.appendChild(chip);
    const cs = (e: Element, p: string) => (getComputedStyle(e) as any)[p] as string;
    const read = () => {
      const page = getComputedStyle(document.body).getPropertyValue("--bg").trim();
      const dim = getComputedStyle(document.body).getPropertyValue("--dim").trim();
      const mc = bubble.querySelector(".mention-chip")!, mp = bubble.querySelector(".mention-chip .host-prefix")!, ap = nm.querySelector(".host-prefix")!;
      return { page, dim, texts: [mc.textContent, nm.textContent],
               mention: { prefix: cs(mp, "color"), backing: cs(mc, "backgroundColor"), under: cs(bubble, "backgroundColor"), page },
               awaiting: { prefix: cs(ap, "color"), backing: cs(nm, "backgroundColor"), under: cs(chip, "backgroundColor"), page } };
    };
    const dark = read();
    document.body.classList.add("theme-light");
    const light = read();
    document.body.classList.remove("theme-light");
    return { dark, light };
  }, SID(3));
  assert.deepEqual(r.dark.texts, ["TESTHOST:web", "TESTHOST:web"], "both chips carry the host prefix and the name");
  assert.notEqual(r.dark.page, r.light.page, "the theme class took: the page token changed");
  for (const [theme, got] of [["dark", r.dark], ["light", r.light]] as const) {
    for (const which of ["mention", "awaiting"] as const) {
      const c = prefixContrast(got[which]);
      assert.ok(c >= 4.5, theme + " theme, the " + which + " chip: the host prefix " + got[which].prefix + " on the backing over " + got[which].under + " reads " + c.toFixed(2) + ":1, below 4.5:1");
    }
  }
  // the reason the chips need a colour of their own: the sheet's dim tier is tuned for the page, and the light theme's dim
  // sits under 4.5:1 on the chips' dark backing (the backing is the same in both themes, the page is not)
  const lightDim = prefixContrast({ ...r.light.mention, prefix: r.light.dim });
  assert.ok(lightDim < 4.5, "the light dim tier on the backing reads " + lightDim.toFixed(2) + ":1 (if it clears 4.5:1 the scoped rule can retire)");
  assert.deepEqual(h.errors, []);
  await h.page.close();
});

// ── source pins: the wiring the slices cannot carry ─────────────────────────────────────────────────────

test("the mention chip wears the awaiting chip's dress: one shared rule for the dark backing, radius and padding, the identity colour as the name's own colour", () => {
  // (the user 2026-09-10, who wanted the two chips to look the same: no colour fill, no @). The page above loads
  // no sheet, so the look is pinned at the source: the shared selector list, and the mention chip's own rule
  // setting none of the shared properties (a later same-specificity rule would otherwise win the cascade).
  const shared = STYLES.match(/^\.chip-peer-name, \.mention-chip \{([^}]*)\}/m);
  assert.ok(shared, "the backing, radius and padding are declared once for both chips");
  assert.match(shared![1], /background: rgba\(0, 0, 0, 0\.85\);/); assert.match(shared![1], /border-radius: 7px;/); assert.match(shared![1], /padding: 0 5px;/);
  assert.doesNotMatch(shared![1], /\bcolor:/, "the colour is each chip's own: the peer name's #fff default, the mention chip's identity colour");
  const own = STYLES.match(/^\.mention-chip \{([^}]*)\}/m);
  assert.ok(own, "the mention chip's own rule");
  assert.match(own![1], /color: var\(--chip-bg, #fff\);/, "the identity colour is the TEXT colour, from the inline --chip-bg");
  assert.doesNotMatch(own![1], /background|border-radius|padding|box-shadow/, "no fill, no ring, nothing the shared rule owns");
  assert.doesNotMatch(chipBlock(), /--chip-fg/, "the fill's text colour is gone with the fill");
  assert.match(chipBlock(), /chip\.replaceChildren\(\.\.\.hostNameNodes\(sg\.text\.slice\(1\), sg\.hit\.id\)\)/,
    "the name through the house session-reference renderer, as the awaiting chip names its peer: a remote host wears .host-prefix");
});

test("the chips' host: prefix has one colour of its own for both chips, beside the shared backing rule, and it clears text contrast on that backing over each theme's page", () => {
  // The page above loads the sheet only in the Chromium case; this pin runs everywhere. The prefix sits on the
  // shared 85% black backing, the same in both themes, so its colour is scoped to the two chips and never
  // inherit: on the mention chip that would be the identity colour, and the palette's dark swatches are the
  // worst-off names on this backing already.
  const rule = STYLES.match(/^\.chip-peer-name \.host-prefix, \.mention-chip \.host-prefix \{([^}]*)\}/m);
  assert.ok(rule, "one rule for both chips' host prefix, chip class first so the global .host-prefix pins stay anchored");
  const color = rule![1].match(/color: ([^;]+);/);
  assert.ok(color, "the rule sets the colour");
  assert.doesNotMatch(color![1], /inherit|var\(--dim/, "a colour for the backing, not the page's dim tier and not the chip's own");
  assert.ok(STYLES.indexOf(rule![0]) > STYLES.indexOf(".chip-peer-name, .mention-chip {"), "declared beside the shared backing rule, after it");
  const token = (block: string, name: string) => {
    const at = STYLES.indexOf("\n" + block + " {");
    assert.ok(at > 0, block);
    const body = STYLES.slice(at, STYLES.indexOf("\n}", at));
    const m = body.match(new RegExp("\\n\\s*" + name.replace(/-/g, "\\-") + ":\\s*([^;]+);"));
    assert.ok(m, name + " in " + block);
    const vr = m![1].trim().match(/^var\([^,]+,\s*(.+)\)$/);   // var(--vscode-x, #hex): the fallback is what this static read has
    return vr ? vr[1].trim() : m![1].trim();
  };
  for (const block of [":root", "body.theme-light"]) {
    const page = token(block, "--bg");
    const c = prefixContrast({ prefix: color![1].trim(), backing: "rgba(0, 0, 0, 0.85)", under: page, page });
    assert.ok(c >= 4.5, block + ": " + color![1].trim() + " on the backing over " + page + " reads " + c.toFixed(2) + ":1, below 4.5:1");
    const dim = prefixContrast({ prefix: token(block, "--dim"), backing: "rgba(0, 0, 0, 0.85)", under: page, page });
    if (block === "body.theme-light") assert.ok(dim < 4.5, "the light dim tier on the backing reads " + dim.toFixed(2) + ":1: the reason for the rule (if it clears 4.5:1 the rule can retire)");
  }
});

test("a copy over a chip goes through mentionCopyText from both doors: the document's copy listener in the chip block, and the selection menu's Copy", () => {
  assert.match(chipBlock(), /\ndocument\.addEventListener\("copy", \(e\) => \{/, "the listener lives in the sliced block, so the Chromium case above runs the real one");
  assert.match(RENDER, /const text = sel \? \(mentionCopyText\(sel\)\?\.text \?\? sel\.toString\(\)\) : "";/, "the right-click Copy reads the same text a Ctrl+C would");
});

test("every clear of the composer goes through clearBox, which refreshes both menus; cancelComposerEdit clears through the module-level hook", () => {
  const stmts = RENDER.match(/^\s*ta\.value = "";/gm) || [];
  assert.equal(stmts.length, 1, "one bare clear statement in the file: clearBox's own");
  assert.match(RENDER, /const clearBox = \(\) => \{\s*\n\s*ta\.value = ""; composerManualH = null; ta\.style\.height = "";\s*\n\s*updateSlash\(\); updateMention\(\);\s*\n\s*\};\s*\n\s*clearComposerBox = clearBox;/);
  const composer = RENDER.slice(RENDER.indexOf("function setupComposer()"), RENDER.indexOf("  // ── PROMPT HISTORY"));
  assert.equal((composer.match(/\bclearBox\(\);/g) || []).length, 6, "the stage, the picker answer, the edit, the provisional queue, the send and /mcp");
  assert.match(RENDER, /function cancelComposerEdit\(sid: string\): void \{\s*\n[^\n]*\n[^\n]*\n\s*clearComposerBox\?\.\(\);/);
});

test("the composer's keydown: the slash menu, then the card, then an IME's commit Enter is left alone, then the stage, then the send", () => {
  const start = RENDER.indexOf('  ta.addEventListener("keydown", (e) => {\n    if (slashKey(e)) return;');
  const end = RENDER.indexOf('  ta.addEventListener("input", () => {', start);
  assert.ok(start > 0 && end > start, "the composer's keydown listener, up to its input listener");
  const keys = RENDER.slice(start, end);
  const card = keys.indexOf("if (mentionKey(e)) return;");
  const guard = keys.indexOf('if (e.key === "Enter" && (e.isComposing || e.keyCode === 229)) return;');
  const stage = keys.indexOf('if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.shiftKey) {');
  const send = keys.indexOf('if (e.key === "Enter" && !e.shiftKey && !isCoarsePointer()) {');
  assert.ok(card > 0 && guard > card && stage > guard && send > stage, "the card, then the IME guard, then the stage, then the send");
  assert.equal((keys.match(/sendComposer\(\)/g) || []).length, 1, "one send in the listener, behind the guard");
  assert.match(RENDER, /updateSlash\(\);[^\n]*\n\s*updateMention\(\);/, "the input handler refreshes the card as the query changes");
});

test("renderTabs opens with the whole-roster hook, ahead of its guards; the user's bubble is marked after its path links, and so is the echo of a send", () => {
  assert.match(RENDER, /function renderTabs\(\) \{\n  mentionRosterChanged\(\);/);
  assert.match(RENDER, /linkifyFileUris\(bubble, imgPaths, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);[^\n]*\n\s*if \(kind === "user"\) markMentions\(bubble\);/);
  // the echo keeps the pinned renderer statement as it stands (chat-md and queued-indicator hold it verbatim); the chip is the next statement
  assert.match(RENDER, /if \(!t\.romp && !isCmd\) bubble\.innerHTML = userMd\(t\.md\);[^\n]*\n\s*if \(!t\.romp && !isCmd\) markMentions\(bubble\);/);
});
