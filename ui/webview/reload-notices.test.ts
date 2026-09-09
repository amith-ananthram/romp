// The notices a page is showing when the reload core takes it (reload-notices.ts). The core's restart reload follows
// the last pending ship's retirement on the next task (render.ts endReloadHoldIfIdle, __rompReload.ended()), and the
// nack, the dismissal or the other-tab ack raised in that same task is appended one task before the page goes: the
// toast was never read, and the fresh page's loss toast reads shipsInFlight, which the retirement already emptied. So
// render.ts keeps the texts of the toasts on screen in this tab's sessionStorage on the core's synchronous hook and the
// fresh page shows them once. The readings are pure and execute here. render.ts has import-time DOM side effects, so
// its wiring is pinned to source the way reload-restore.test.ts pins the scroll record's, and the toast family with the
// refusals that report a state is lifted out of it and executed over a fake DOM the way chat-exact-tail-exec.test.ts
// lifts chatTail. The served scenario (a nack on the last ship across a kernel restart; the fresh page says it again,
// once) is tests/test_ship_reship.py NackNoticeSurvivesReload. Synthetic only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { RELOAD_NOTICES_KEY, liveNotices, keepReloadNotices, takeReloadNotices } from "./reload-notices";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

const SEL = ".warn-toast:not([data-ephemeral]) .warn-toast-msg";
const box = (texts: (string | null)[]) => ({
  querySelectorAll: (sel: string) => { assert.equal(sel, SEL); return texts.map((t) => ({ textContent: t })); },
});

/** A sessionStorage stand-in: the three calls the module makes over a Map, with a log of them. */
function fakeStore(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init));
  const calls: string[] = [];
  return {
    m, calls,
    getItem: (k: string) => { calls.push("get " + k); return m.has(k) ? m.get(k)! : null; },
    setItem: (k: string, v: string) => { calls.push("set " + k); m.set(k, v); },
    removeItem: (k: string) => { calls.push("remove " + k); m.delete(k); },
  };
}

test("the toasts on screen read as their texts, in order, blanks dropped; none without the container", () => {
  assert.deepEqual(liveNotices(null), [], "the container is created by the first toast; none yet");
  assert.deepEqual(liveNotices(undefined), []);
  assert.deepEqual(liveNotices(box([])), []);
  assert.deepEqual(liveNotices(box(["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.",
                                    "  ", null, "The pending upload was dismissed. Your held message was NOT sent."])),
    ["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.",
     "The pending upload was dismissed. Your held message was NOT sent."]);
  assert.deepEqual(liveNotices(box(["  padded  "])), ["padded"], "the text as the toast shows it");
});

test("the reading asks for the toasts without the ephemeral mark: a refusal about a state the fresh page shows for itself stays behind", () => {
  // a refusal that reports a state (the staged sends' "Can't send yet": the host unreachable, the tab still being
  // created; the staging refusals; the branch jump to a session not on this dashboard) is about something the fresh
  // page shows for itself or no longer has; render.ts marks those toasts where they are raised (executed below) and the
  // selector skips the mark. The mark's effect on a real DOM is executed by tests/test_ship_reship.py
  // NackNoticeSurvivesReload.
  const asked: string[] = [];
  assert.deepEqual(liveNotices({ querySelectorAll: (sel: string) => { asked.push(sel); return [{ textContent: "kept" }]; } }), ["kept"]);
  assert.deepEqual(asked, [SEL]);
});

test("the record is kept only when there is something to say; a page with no toast clears a record left behind", () => {
  const s = fakeStore();
  keepReloadNotices(s, ["one", "two"]);
  assert.deepEqual([...s.m.entries()], [[RELOAD_NOTICES_KEY, JSON.stringify(["one", "two"])]]);
  assert.equal(RELOAD_NOTICES_KEY, "romp:reloadNotices", "this tab's sessionStorage, beside the scroll record's key");
  // a reload the browser refused leaves the record in place; the next core reload with nothing on screen clears it
  keepReloadNotices(s, []);
  assert.equal(s.m.has(RELOAD_NOTICES_KEY), false, "cleared, not written empty");
  assert.deepEqual(s.calls, ["set " + RELOAD_NOTICES_KEY, "remove " + RELOAD_NOTICES_KEY]);
  keepReloadNotices(null, ["x"]);
  keepReloadNotices(undefined, []);
});

test("the record comes out once: strings only, and the key is gone whatever it held", () => {
  const s = fakeStore({ [RELOAD_NOTICES_KEY]: JSON.stringify(["one", "", 3, null, "two"]), other: "kept" });
  assert.deepEqual(takeReloadNotices(s), ["one", "two"]);
  assert.deepEqual([...s.m.keys()], ["other"], "the key is removed; nothing else is touched");
  assert.deepEqual(takeReloadNotices(s), [], "a second take finds nothing");
  assert.deepEqual(s.calls, ["get " + RELOAD_NOTICES_KEY, "remove " + RELOAD_NOTICES_KEY, "get " + RELOAD_NOTICES_KEY],
    "no record, no removal");
  for (const junk of ["not json", JSON.stringify("a string"), JSON.stringify({ a: 1 }), JSON.stringify(null), ""]) {
    const j = fakeStore({ [RELOAD_NOTICES_KEY]: junk });
    assert.deepEqual(takeReloadNotices(j), [], JSON.stringify(junk) + " reads as none");
    assert.equal(j.m.has(RELOAD_NOTICES_KEY), false, JSON.stringify(junk) + " is still cleared");
  }
  assert.deepEqual(takeReloadNotices(null), []);
  assert.deepEqual(takeReloadNotices(undefined), []);
});

test("kept on the page that reloads, taken on the page that follows: the same texts, then nothing", () => {
  const s = fakeStore();
  const texts = liveNotices(box(["shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent."]));
  keepReloadNotices(s, texts);
  assert.deepEqual(takeReloadNotices(s), texts);
  assert.deepEqual(takeReloadNotices(s), []);
});

test("a store that refuses is left alone: nothing thrown from either side", () => {
  const broken = {
    getItem: () => { throw new Error("SecurityError"); },
    setItem: () => { throw new Error("QuotaExceededError"); },
    removeItem: () => { throw new Error("SecurityError"); },
  };
  assert.doesNotThrow(() => keepReloadNotices(broken, ["x"]));
  assert.doesNotThrow(() => keepReloadNotices(broken, []));
  assert.deepEqual(takeReloadNotices(broken), []);
});

// ── The refusals that report a state, executed ───────────────────────────────────────────────────────────────────────

/** A slice of render.ts between two anchors, transpiled (TS to JS) with esbuild at run time and required dynamically so
 *  the test bundle does not bundle esbuild itself (the chat-exact-tail-exec.test.ts pattern). `wrap` closes a slice
 *  that is not a statement on its own (a property of an object literal) before it is transpiled. */
function liftBetween(startAnchor: string, endAnchor: string, wrap: (ts: string) => string = (s) => s): string {
  const a = RENDER.indexOf(startAnchor), b = RENDER.indexOf(endAnchor, a);
  assert.ok(a > 0 && b > a, `anchors not found: ${startAnchor.slice(0, 40)} or ${endAnchor.slice(0, 40)} moved; re-anchor`);
  return requireCjs("esbuild").transformSync(wrap(RENDER.slice(a, b)), { loader: "ts" }).code;
}

/** Enough of Element for the toast family and the reading: a class, a dataset, children, an id, and the container's
 *  querySelectorAll for the reading's selector shape (.a:not([data-x]) .b), evaluated as the DOM would: a one-word
 *  data attribute names its dataset key as is, whether it was set through dataset or setAttribute. */
class FakeEl {
  children: FakeEl[] = []; parent: FakeEl | null = null; dataset: Record<string, string> = {}; attrs: Record<string, string> = {};
  textContent = ""; title = ""; id = "";
  constructor(public tag: string, public className = "") {}
  has(c: string): boolean { return this.className.split(/\s+/).includes(c); }
  appendChild(c: FakeEl): FakeEl { c.parent?.removeChild(c); c.parent = this; this.children.push(c); return c; }
  append(...cs: FakeEl[]): void { for (const c of cs) this.appendChild(c); }
  removeChild(c: FakeEl): void { this.children = this.children.filter((x) => x !== c); c.parent = null; }
  remove(): void { this.parent?.removeChild(this); }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; if (k.startsWith("data-")) this.dataset[k.slice(5)] = v; }
  addEventListener(): void {}
  querySelectorAll(sel: string): FakeEl[] {
    const m = /^\.([\w-]+):not\(\[data-([\w-]+)\]\) \.([\w-]+)$/.exec(sel);
    if (!m) throw new Error("unsupported selector " + sel);
    return this.children.filter((c) => c.has(m[1]) && !(m[2] in c.dataset)).flatMap((c) => c.children.filter((s) => s.has(m[3])));
  }
}
/** document as warnToast uses it: a body to append the container to, getElementById to find it again, a key listener. */
function fakeDocument() {
  const body = new FakeEl("body");
  return { body, getElementById: (id: string) => body.children.find((c) => c.id === id) ?? null, addEventListener: () => {} };
}

/** The page state the lifted closures read: the composer (a typed draft, its citations, a picker waiting, an edit in
 *  progress, attachments) and the session roster, with what each gesture did recorded. The toasts' timers are recorded
 *  and not run, so a toast stays on screen for the reading. */
function pageWorld(state: { ask?: "custom" | "text" | null; edit?: boolean; files?: string[]; sessions?: string[] }) {
  return {
    FakeEl, document: fakeDocument(), timers: [] as number[],
    activeId: "web", ta: { value: "what did the tests say", style: {} as Record<string, string> },
    composerCitations: new Map<string, { quote?: string }[]>(), ask: state.ask ?? null,
    composerEdits: new Map<string, { uuid: string; orig: string }>(state.edit ? [["web", { uuid: "e1", orig: "the old text" }]] : []),
    composerFiles: new Map<string, string[]>(state.files ? [["web", state.files]] : []),
    staged: [] as [string, unknown][], persists: 0,
    sessions: new Map<string, { id: string }>((state.sessions || []).map((id): [string, { id: string }] => [id, { id }])),
    activated: [] as [string, string | undefined][],
  };
}
type World = ReturnType<typeof pageWorld>;
type Lifted = { stageComposer: () => void; branchjump: (elx: { dataset: Record<string, string> }) => void; warnToast: (msg: string) => FakeEl };

/** warnToast and ephemeralWarnToast; stageComposer (the composer's staging, whose refusals say a picker is waiting on
 *  the composer, an edit is in progress, attachments are on the composer); and the branch jump's delegated handler
 *  (whose refusal says the session is not on this dashboard), lifted from render.ts and run over the world. */
function liftToastSites(): (w: World) => Lifted {
  const toasts = liftBetween("function warnToast(msg: string): HTMLElement {", "// Tail-windowing (see the View comment)");
  const stage = liftBetween("const stageComposer = () => {", "const sendComposer = (");
  const jump = liftBetween("branchjump: (elx) => {", "// a below-response fork spot", (ts) => "const handlers = {\n" + ts + "};");
  const prelude = `
    const W = WORLD;
    const document = W.document;
    const el = (tag, cls) => new W.FakeEl(tag, cls);
    const setTimeout = (fn, ms) => { W.timers.push(ms); return 0; };   // the fade and the removal are not run
    let activeId = W.activeId;
    const ta = W.ta;
    const composerCitations = W.composerCitations;
    const composerAnswersAsk = () => W.ask;
    const composerEdits = W.composerEdits;
    const composerFiles = W.composerFiles;
    const stagedMsgs = { push: (id, s) => { W.staged.push([id, s]); } };
    const renderComposerChips = () => {};
    const drafts = new Map(), draftStartedAt = new Map();
    let composerManualH = null;
    const persistDrafts = () => { W.persists++; };
    const renderStagedStrip = () => {};
    const sessions = W.sessions;
    const setActive = (sid, cut) => { W.activated.push([sid, cut]); };
  `;
  return new Function("WORLD", prelude + toasts + stage + jump + "\nreturn { stageComposer, branchjump: handlers.branchjump, warnToast };") as (w: World) => Lifted;
}

function page(state: Parameters<typeof pageWorld>[0]) {
  const W = pageWorld(state);
  const api = liftToastSites()(W);
  const box = () => W.document.getElementById("warn-toasts");
  const shown = () => (box()?.children || []).map((t) => t.children[0].textContent);
  return { W, ...api, box, shown };
}
type Page = ReturnType<typeof page>;

const NACK = "shot.png couldn't be saved on the kernel, so it was not attached. Your message was NOT sent.";

test("staging with nothing owning the composer stages: the lifted composer is the real one, and it raises no toast", () => {
  const p = page({});
  p.stageComposer();
  assert.deepEqual(p.W.staged, [["web", { text: "what did the tests say", cites: [] }]]);
  assert.equal(p.W.ta.value, "", "the composer clears");
  assert.equal(p.W.persists, 1);
  assert.equal(p.box(), null, "no toast, so no container");
});

// The refusals that report a STATE rather than an event, raised through their real code paths. Each puts its toast on
// screen for the person at the page and refuses the gesture; the reading skips it, because the fresh page shows that
// state for itself (the picker, the attachments and the roster come back from the kernel and the persisted drafts) or
// no longer has it (an edit in progress lives in memory alone, so a replay would report an edit the fresh page has not
// got).
const STATE_REFUSALS: { name: string; state: Parameters<typeof pageWorld>[0]; raise: (p: Page) => void; text: string; refused: (p: Page) => void }[] = [
  { name: "staging while a picker waits on the composer", state: { ask: "text" }, raise: (p) => p.stageComposer(),
    text: "A picker is waiting on this box", refused: (p) => assert.deepEqual(p.W.staged, [], "nothing staged") },
  { name: "staging while an edit is in progress", state: { edit: true }, raise: (p) => p.stageComposer(),
    text: "An edit replaces a past message", refused: (p) => assert.deepEqual(p.W.staged, [], "nothing staged") },
  { name: "staging with attachments on the composer", state: { files: ["notes.md"] }, raise: (p) => p.stageComposer(),
    text: "Attachments can't be staged", refused: (p) => assert.deepEqual(p.W.staged, [], "nothing staged") },
  { name: "a branch jump to a session not on this dashboard", state: { sessions: ["web"] }, raise: (p) => p.branchjump({ dataset: { sid: "api" } }),
    text: "That session isn't on this dashboard right now.", refused: (p) => assert.deepEqual(p.W.activated, [], "no switch") },
];
for (const r of STATE_REFUSALS) {
  test(r.name + ": the refusal is on screen and refuses, and the reading skips it", () => {
    const p = page(r.state);
    r.raise(p);
    const shown = p.shown();
    assert.equal(shown.length, 1, "the refusal put its toast on screen");
    assert.ok(shown[0].startsWith(r.text), shown[0]);
    r.refused(p);
    assert.equal(p.W.ta.value, "what did the tests say", "the draft stays where it was");
    assert.deepEqual(liveNotices(p.box()), [], "a state the fresh page shows for itself, or no longer has, does not ride the reload");
  });
}

test("a toast about what happened, raised beside the refusals, is read: the mark is on the state refusals alone", () => {
  const p = page({ edit: true, sessions: ["web"] });
  p.stageComposer();
  p.warnToast(NACK);
  p.branchjump({ dataset: { sid: "api" } });
  assert.equal(p.shown().length, 3, "all three are on screen for the person at the page");
  assert.deepEqual(liveNotices(p.box()), [NACK]);
});

// The wiring, pinned to source (render.ts executes nothing under node --test).
test("render.ts: warnToast hands back its toast, and the refusals about a state are marked ephemeral where they are raised", () => {
  assert.match(RENDER, /^function warnToast\(msg: string\): HTMLElement \{/m);
  assert.match(RENDER, /setTimeout\(\(\) => t\.remove\(\), 12000\);\n\s*return t;/);
  assert.match(RENDER, /^function ephemeralWarnToast\(msg: string\): void \{ warnToast\(msg\)\.dataset\.ephemeral = "1"; \}/m);
  assert.equal((RENDER.match(/dataset\.ephemeral/g) || []).length, 1, "marked in one place");
  assert.equal((RENDER.match(/ephemeralWarnToast\("Can't send yet — the session isn't reachable\. They stay staged\."\);/g) || []).length, 2,
    "the staged sends' refusal at both of its sites (the strip's Send now and the empty send)");
  // the staging refusals (stageComposer) and the branch jump's: states the fresh page shows for itself or no longer has
  assert.match(RENDER, /if \(composerAnswersAsk\(\)\) \{ ephemeralWarnToast\("A picker is waiting on this box/);
  assert.match(RENDER, /if \(composerEdits\.has\(activeId\)\) \{ ephemeralWarnToast\("An edit replaces a past message/);
  assert.match(RENDER, /if \(\(composerFiles\.get\(activeId\) \|\| \[\]\)\.length\) \{ ephemeralWarnToast\("Attachments can't be staged/);
  assert.match(RENDER, /if \(!sessions\.get\(sid\)\) \{ ephemeralWarnToast\("That session isn't on this dashboard right now\."\); return; \}/);
  assert.equal((RENDER.match(/ephemeralWarnToast\(/g) || []).length, 7, "the definition, the two reachability sites and the four state refusals");
  // what the nack, the dismissal and the other-tab ack say stays true after the reload, so they ride it unmarked
  assert.match(RENDER, /warnToast\(m\.name \+ " couldn't be saved on the kernel, so it was not attached/);
  assert.match(RENDER, /warnToast\("The pending upload was dismissed — your held message was NOT sent\."\)/);
  assert.match(RENDER, /warnToast\("attachments finished uploading on another tab — the held message was not sent; review it there\."\)/);
});

test("render.ts keeps the notices on the core's hook alone, pagehide keeps the scroll record alone, and the fresh page shows them once after the loss toast", () => {
  assert.match(RENDER, /^import \{ liveNotices, keepReloadNotices, takeReloadNotices \} from "\.\/reload-notices";/m);
  assert.match(RENDER, /^function persistNoticesForReload\(\): void \{\n\s*try \{ keepReloadNotices\(sessionStorage, liveNotices\(document\.getElementById\("warn-toasts"\)\)\); \} catch \{ \/\* ignore \*\/ \}\n\}/m);
  // the core's synchronous hook writes both records; a navigation of the user's own (pagehide) writes the scroll record
  // alone, so a load they asked for does not replay a toast they were already looking at
  assert.match(RENDER, /^function persistForReload\(\): void \{ persistScrollForReload\(\); persistNoticesForReload\(\); \}[^\n]*\n\(window as any\)\.__rompPersistForReload = persistForReload;\nwindow\.addEventListener\("pagehide", persistScrollForReload\);/m);
  assert.equal((RENDER.match(/persistNoticesForReload\(\)/g) || []).length, 2, "defined once, called from the core's hook alone");
  const scroll = RENDER.match(/^function persistScrollForReload\(\): void \{([\s\S]*?)\n\}/m);
  assert.ok(scroll && !scroll[1].includes("Notices"), "the scroll record is untouched");
  // the replay follows the loss toast's block directly: the loss first, then what the last page was saying
  assert.match(RENDER, /shipsInFlight: \[\] \}\);\n\s*\}\n\s*\}\n\} catch \{ \/\* ignore \*\/ \}\n(\/\/[^\n]*\n)*try \{ for \(const text of takeReloadNotices\(sessionStorage\)\) warnToast\(text\); \} catch \{ \/\* ignore \*\/ \}/);
  assert.equal((RENDER.match(/takeReloadNotices\(/g) || []).length, 1, "consumed once, at load");
  assert.equal((RENDER.match(/keepReloadNotices\(/g) || []).length, 1, "written from one place");
});
