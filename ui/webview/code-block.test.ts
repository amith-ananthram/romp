// The shared code-block module (code-block.ts): the per-line rows and the Copy button every rendered fence wears, in the
// chat and in the file viewer. The chat's highlight() (render.ts) and the viewer's mdBlock (file-view.ts) import the same
// two functions; file-view.ts cannot import render.ts (render.ts imports it, and the feed bundle must not carry the chat),
// so the module is their meeting point. Executed: the pure walk (wrapLinesHtml) over plain and highlighted lines, a span
// straddling a newline, a trailing newline; wrapCodeLines over a code element (the rows, the digit count it writes for the
// gutter); addCopyBtn over a DOM stand-in (one button, idempotent; the click and Space copy the text the caller gave it,
// through the Clipboard API or the execCommand fallback; the acknowledgement lands on the button on screen of the fence
// with the copied source, after a rebuild swapped the pressed one out). Pinned: the two callers' shapes, the bundle
// boundary (file-view.ts and feed.ts import nothing from render.ts), the sheets' scoped copies byte-equal in styles.css
// and feed.css with the counter reset on `.fileview-md pre code`, the six viewer grammars registered on the core by
// viewer-grammars.ts (executed) and reached through file-view.ts, and the chat's auto-detection kept to its ten
// (highlight-cache.ts AUTO_LANGUAGES, executed).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import hljs from "highlight.js/lib/core";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const read = (f: string) => fs.readFileSync(path.join(UI, f), "utf8");
const BLOCK = read("code-block.ts");
const RENDER = read("render.ts");
const VIEW = read("file-view.ts");
const CHAT = read("styles.css");
const FEED = read("feed.css");

// ── a DOM stand-in: the members the module touches ─────────────────────────────────────────────────
// A tree with classes, a style map, listeners, a small selector matcher (a class, a tag with a class, `:scope > .x`),
// isConnected through the root, and a MutationObserver whose callbacks the test fires by hand.
class El {
  parentElement: El | null = null;
  children: El[] = [];
  type = ""; title = ""; textContent = "";
  private classes = new Set<string>();
  private props = new Map<string, string>();
  private html = "";
  private listeners = new Map<string, Array<(ev: any) => void>>();
  constructor(public tagName: string) {}
  get className(): string { return [...this.classes].join(" "); }
  set className(v: string) { this.classes = new Set(v.split(/\s+/).filter(Boolean)); }
  classList = {
    add: (c: string) => { this.classes.add(c); },
    remove: (c: string) => { this.classes.delete(c); },
    toggle: (c: string, on: boolean) => { if (on) this.classes.add(c); else this.classes.delete(c); },
    contains: (c: string) => this.classes.has(c),
  };
  style = {
    setProperty: (k: string, v: string) => { this.props.set(k, v); },
    getPropertyValue: (k: string) => this.props.get(k) ?? "",
  };
  /** innerHTML is a string: setting it parses nothing, and childElementCount counts the `.cl` rows the module wrote. */
  get innerHTML(): string { return this.html; }
  set innerHTML(v: string) { this.html = v; }
  get childElementCount(): number { return this.children.length || (this.html.match(/<span class="cl">/g) || []).length; }
  get isConnected(): boolean { let n: El | null = this; while (n.parentElement) n = n.parentElement; return n === docBody; }
  appendChild(c: El): El { c.parentElement?.removeChild(c); c.parentElement = this; this.children.push(c); return c; }
  removeChild(c: El): El { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentElement = null; return c; }
  replaceChildren(...cs: El[]): void { for (const c of this.children.slice()) this.removeChild(c); for (const c of cs) this.appendChild(c); }
  remove(): void { this.parentElement?.removeChild(this); }
  private fits(part: string): boolean {
    const m = /^([a-z]*)((?:\.[\w-]+)*)$/.exec(part);
    if (!m) return false;
    if (m[1] && m[1] !== this.tagName) return false;
    return (m[2].match(/\.[\w-]+/g) || []).every((c) => this.classes.has(c.slice(1)));
  }
  querySelectorAll(sel: string): El[] {
    const scoped = /^:scope > (.+)$/.exec(sel);
    if (scoped) return this.children.filter((c) => c.fits(scoped[1]));
    const out: El[] = [];
    const walk = (n: El) => { for (const c of n.children) { if (c.fits(sel)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel: string): El | null { return this.querySelectorAll(sel)[0] ?? null; }
  addEventListener(type: string, fn: (ev: any) => void): void { const l = this.listeners.get(type) || []; l.push(fn); this.listeners.set(type, l); }
  /** Dispatch to this node's listeners; returns the event so the test reads defaultPrevented. */
  fire(type: string, init: Record<string, unknown> = {}): { defaultPrevented: boolean; stopped: boolean } {
    const ev: any = { type, defaultPrevented: false, stopped: false, ...init, preventDefault() { ev.defaultPrevented = true; }, stopPropagation() { ev.stopped = true; } };
    for (const fn of this.listeners.get(type) || []) fn(ev);
    return ev;
  }
}
const docBody = new El("body");
const observers: Array<{ roots: El[]; cb: () => void; live: boolean }> = [];
class FakeMutationObserver {
  private rec = { roots: [] as El[], cb: () => { /* set below */ }, live: true };
  constructor(cb: () => void) { this.rec.cb = cb; observers.push(this.rec); }
  observe(root: El): void { this.rec.roots.push(root); }
  disconnect(): void { this.rec.live = false; }
}
/** Every live observer watching `root` runs, as a child-list change under it would make it. */
const mutated = (root: El) => { for (const o of observers) if (o.live && o.roots.includes(root)) o.cb(); };
const timers: Array<() => void> = [];
let clipboard: ((text: string) => Promise<void>) | null = null;   // the Clipboard API of the moment; null = none (the fallback runs)
const copied: string[] = [];                                        // what the Clipboard API received
let execResult = true;                                              // what document.execCommand("copy") answers
const execCalls: string[] = [];                                     // the textarea values the fallback selected
(globalThis as any).document = {
  body: docBody,
  createElement: (tag: string) => new El(tag),
  execCommand: (cmd: string) => { assert.equal(cmd, "copy"); return execResult; },
};
(globalThis as any).window = { setTimeout: (fn: () => void) => { timers.push(fn); return timers.length; } };
(globalThis as any).MutationObserver = FakeMutationObserver;
Object.defineProperty(globalThis, "navigator", { configurable: true, get: () => ({ clipboard: clipboard ? { writeText: clipboard } : undefined }) });
// the fallback's textarea: value, focus, select; appended to and removed from the body
const origCreate = (globalThis as any).document.createElement;
(globalThis as any).document.createElement = (tag: string) => {
  const e: any = origCreate(tag);
  if (tag === "textarea") { e.value = ""; e.style = {}; e.focus = () => { /* nothing to focus */ }; e.select = () => { execCalls.push(e.value); }; }
  return e;
};
/** Let the settled promise chain run: the copy's then, the acknowledgement's placement. */
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise<void>((r) => setImmediate(r)); };
const runTimers = () => { for (const t of timers.splice(0)) t(); };
const reset = () => { docBody.replaceChildren(); observers.length = 0; timers.length = 0; copied.length = 0; execCalls.length = 0; execResult = true; clipboard = (t) => { copied.push(t); return Promise.resolve(); }; };

// the module is loaded after the stand-in is up: it reads document and navigator at call time, not at import, but the
// import is kept below the globals for the same reason a page's script runs after its document exists
// eslint-disable-next-line @typescript-eslint/no-var-requires
const mod = require("./code-block") as typeof import("./code-block");
const { wrapLinesHtml, wrapCodeLines, addCopyBtn, copyText } = mod;

/** A fenced block as marked and the highlighter leave it: <pre><code> with the rows already cut, under `parent`. */
const fence = (parent: El, source: string): { pre: El; code: El } => {
  const pre = new El("pre"); const code = new El("code");
  code.textContent = source; code.innerHTML = wrapLinesHtml(source);
  pre.appendChild(code); parent.appendChild(pre);
  return { pre, code };
};
const button = (pre: El): El => { const b = pre.querySelector(":scope > .code-copy"); assert.ok(b, "a Copy button on the fence"); return b!; };

// ── the walk, executed ─────────────────────────────────────────────────────────────────────────────

test("wrapLinesHtml: one row per line, the newlines dropped, a trailing newline not a row", () => {
  assert.equal(wrapLinesHtml("a\nb\n"), '<span class="cl"><span class="ct">a</span></span><span class="cl"><span class="ct">b</span></span>');
  assert.equal(wrapLinesHtml("a\n\nb"), '<span class="cl"><span class="ct">a</span></span><span class="cl"><span class="ct"></span></span><span class="cl"><span class="ct">b</span></span>', "a blank line is an empty row");
  assert.equal(wrapLinesHtml("only"), '<span class="cl"><span class="ct">only</span></span>');
  assert.equal(wrapLinesHtml(""), '<span class="cl"><span class="ct"></span></span>', "an empty block is one empty row (a single empty line is not a trailing newline)");
  assert.equal(wrapLinesHtml("a\nb").indexOf("\n"), -1, "no newline survives: the row stands for its line and the sheet draws the number");
});

test("wrapLinesHtml: a highlighter span that straddles a newline is closed on its line and re-opened on the next", () => {
  const html = '<span class="hljs-string">"one\ntwo"</span> x';
  assert.equal(wrapLinesHtml(html), '<span class="cl"><span class="ct"><span class="hljs-string">"one</span></span></span><span class="cl"><span class="ct"><span class="hljs-string">two"</span> x</span></span>');
  // nested: two open spans across the break, both re-opened in order
  assert.equal(wrapLinesHtml('<span class="a"><span class="b">1\n2</span>3</span>'),
    '<span class="cl"><span class="ct"><span class="a"><span class="b">1</span></span></span></span><span class="cl"><span class="ct"><span class="a"><span class="b">2</span>3</span></span></span>');
});

test("wrapCodeLines cuts the rows on the element and writes the digits of its last line number for the gutter's basis", () => {
  const code = new El("code");
  code.innerHTML = "a\nb\nc\n";
  wrapCodeLines(code as unknown as HTMLElement);
  assert.equal(code.innerHTML, wrapLinesHtml("a\nb\nc\n"));
  assert.equal(code.childElementCount, 3);
  assert.equal(code.style.getPropertyValue("--ln-digits"), "1", "three rows: one digit");
  const long = new El("code");
  long.innerHTML = Array.from({ length: 10000 }, (_, i) => "line " + i).join("\n");
  wrapCodeLines(long as unknown as HTMLElement);
  assert.equal(long.childElementCount, 10000);
  assert.equal(long.style.getPropertyValue("--ln-digits"), "5", "ten thousand rows: five digits, so every row's gutter fits the last number");
});

// ── the Copy button, executed ──────────────────────────────────────────────────────────────────────

test("addCopyBtn parks one Copy button on the <pre>, idempotent, and a click copies the text the caller gave it, not the on-screen rows", async () => {
  reset();
  const { pre } = fence(docBody, "x = 1\ny = 2\n");
  addCopyBtn(pre as unknown as HTMLElement, "x = 1\ny = 2\n");
  addCopyBtn(pre as unknown as HTMLElement, "x = 1\ny = 2\n");
  assert.equal(pre.querySelectorAll(":scope > .code-copy").length, 1, "a second call adds nothing (a re-render runs it again)");
  assert.ok(pre.classList.contains("has-copy"), "the pre wears has-copy: the sheet anchors the button to it");
  const btn = button(pre);
  assert.equal(btn.tagName, "button"); assert.equal(btn.type, "button"); assert.equal(btn.textContent, "Copy");
  const ev = btn.fire("click");
  assert.ok(ev.defaultPrevented && ev.stopped, "the click stays on the button: nothing above it acts on a Copy");
  await settle();
  assert.deepEqual(copied, ["x = 1\ny = 2\n"], "the caller's text, newlines and all");
  assert.equal(btn.textContent, "Copied"); assert.ok(btn.classList.contains("copied"), "acknowledged at once, in green");
  assert.equal(timers.length, 1, "one reset armed");
  runTimers();
  assert.equal(btn.textContent, "Copy"); assert.ok(!btn.classList.contains("copied"), "and back to Copy after the window");
});

test("copyText: the Clipboard API first; a refusal or no API falls back to a hidden textarea and execCommand, whose verdict is the answer", async () => {
  reset();
  assert.equal(await copyText("a"), true); assert.deepEqual(copied, ["a"]); assert.deepEqual(execCalls, []);
  clipboard = () => Promise.reject(new Error("denied"));
  assert.equal(await copyText("b"), true, "the API refused, the fallback copied"); assert.deepEqual(execCalls, ["b"]);
  clipboard = null;
  execResult = false;
  assert.equal(await copyText("c"), false, "no API and the fallback failed"); assert.deepEqual(execCalls, ["b", "c"]);
  assert.equal(docBody.children.length, 0, "the fallback's textarea never stays in the document");
  // ...and the button says so
  execResult = false; clipboard = null;
  const { pre } = fence(docBody, "z");
  addCopyBtn(pre as unknown as HTMLElement, "z");
  button(pre).fire("click"); await settle();
  assert.equal(button(pre).textContent, "Copy failed"); assert.ok(!button(pre).classList.contains("copied"));
});

test("Space copies on the keydown, once per press, with the key's default prevented on the keydown and the keyup; Enter is left to the button's own click", async () => {
  reset();
  const { pre } = fence(docBody, "s");
  addCopyBtn(pre as unknown as HTMLElement, "s");
  const btn = button(pre);
  const down = btn.fire("keydown", { key: " ", repeat: false });
  assert.ok(down.defaultPrevented, "the keydown's default (the :active press whose keyup would click) is prevented");
  await settle();
  assert.deepEqual(copied, ["s"], "the copy ran on the keydown");
  const again = btn.fire("keydown", { key: " ", repeat: true });
  assert.ok(again.defaultPrevented); await settle();
  assert.deepEqual(copied, ["s"], "a held key's repeat copies nothing more");
  const up = btn.fire("keyup", { key: " " });
  assert.ok(up.defaultPrevented, "the keyup is prevented too, for an engine that would click on it anyway");
  const enter = btn.fire("keydown", { key: "Enter", repeat: false });
  assert.ok(!enter.defaultPrevented); await settle();
  assert.deepEqual(copied, ["s"], "Enter's click is the button's own; the handler does nothing with it");
  const other = btn.fire("keyup", { key: "Enter" });
  assert.ok(!other.defaultPrevented);
});

test("the acknowledgement lands on the button on screen of the fence with the copied source: a rebuild that swapped the pressed fence out before the clipboard answered moves it to the replacement, by the fence's ordinal among fences of that source", async () => {
  reset();
  let release: () => void = () => { /* set by the clipboard below */ };
  clipboard = (t) => { copied.push(t); return new Promise<void>((r) => { release = r; }); };
  const host = new El("div"); docBody.appendChild(host);
  // two fences of one source, one of another
  const a1 = fence(host, "same\n"), a2 = fence(host, "same\n"), b = fence(host, "other\n");
  for (const f of [a1, a2, b]) addCopyBtn(f.pre as unknown as HTMLElement, f.code.textContent);
  button(a2.pre).fire("click");
  await settle();
  assert.equal(button(a2.pre).textContent, "Copy", "the clipboard has not answered yet");
  // the surface rebuilds: a fresh render of the same document, with a new fence of the other source put first
  const c = fence(new El("div"), "new\n"), n1 = fence(new El("div"), "same\n"), n2 = fence(new El("div"), "same\n"), nb = fence(new El("div"), "other\n");
  for (const f of [c, n1, n2, nb]) addCopyBtn(f.pre as unknown as HTMLElement, f.code.textContent);
  host.replaceChildren(c.pre, n1.pre, n2.pre, nb.pre);
  assert.ok(!a2.pre.isConnected, "the pressed fence is out of the document");
  release(); await settle();
  assert.deepEqual(copied, ["same\n"]);
  assert.equal(button(a2.pre).textContent, "Copy", "the detached button is not written to");
  assert.equal(button(n2.pre).textContent, "Copied", "the second fence of that source on screen, the pressed one's ordinal, is acknowledged");
  assert.equal(button(n1.pre).textContent, "Copy"); assert.equal(button(c.pre).textContent, "Copy"); assert.equal(button(nb.pre).textContent, "Copy");
  // a swap inside the window carries the label on: another rebuild replaces the acknowledged fence
  const m1 = fence(new El("div"), "same\n"), m2 = fence(new El("div"), "same\n");
  for (const f of [m1, m2]) addCopyBtn(f.pre as unknown as HTMLElement, f.code.textContent);
  host.replaceChildren(m1.pre, m2.pre);
  mutated(host);
  assert.equal(button(m2.pre).textContent, "Copied", "the observer on the ancestor's child list moved the label on the swap");
  assert.equal(button(n2.pre).textContent, "Copied", "the detached one keeps what it had; nothing reads it");
  runTimers();
  assert.equal(button(m2.pre).textContent, "Copy", "the reset goes to the button last acknowledged");
  assert.ok(observers.every((o) => !o.live), "the observers are disconnected when the window closes");
});

test("a rebuild that did not bring the fence back (its text rewritten, or the fence removed) acknowledges nothing, and never throws", async () => {
  reset();
  let release: () => void = () => { /* set below */ };
  clipboard = (t) => { copied.push(t); return new Promise<void>((r) => { release = r; }); };
  const host = new El("div"); docBody.appendChild(host);
  const a = fence(host, "gone\n"); addCopyBtn(a.pre as unknown as HTMLElement, "gone\n");
  button(a.pre).fire("click"); await settle();
  const other = fence(new El("div"), "different\n"); addCopyBtn(other.pre as unknown as HTMLElement, "different\n");
  host.replaceChildren(other.pre);
  release(); await settle();
  assert.equal(button(other.pre).textContent, "Copy", "a fence of another source is not the one whose text was copied");
  assert.equal(button(a.pre).textContent, "Copy", "the detached button is not written to either");
  runTimers();
  // the whole surface gone: the walk stops under document.body
  const b = fence(host, "x\n"); addCopyBtn(b.pre as unknown as HTMLElement, "x\n");
  button(b.pre).fire("click"); await settle();
  docBody.replaceChildren();
  release(); await settle();
  runTimers();
});

// ── the callers ────────────────────────────────────────────────────────────────────────────────────

test("render.ts and file-view.ts import the two functions from the module; render.ts keeps no copy; the viewer never imports the chat", () => {
  assert.match(RENDER, /^import \{ wrapCodeLines, addCopyBtn \} from "\.\/code-block";/m);
  assert.match(VIEW, /^import \{ wrapCodeLines, addCopyBtn \} from "\.\/code-block";/m);
  assert.doesNotMatch(RENDER, /^(export )?function (wrapCodeLines|addCopyBtn|copyText|fallbackCopy)\(/m, "the definitions moved");
  assert.match(BLOCK, /^export function wrapLinesHtml\(html: string\): string/m);
  assert.match(BLOCK, /^export function wrapCodeLines\(code: HTMLElement\): void/m);
  assert.match(BLOCK, /^export function addCopyBtn\(pre: HTMLElement, raw: string\): void/m);
  assert.match(BLOCK, /^export function copyText\(text: string\): Promise<boolean>/m);
  assert.doesNotMatch(BLOCK, /from "\.\/render"|from "\.\/file-view"/, "the module imports neither caller");
  for (const f of ["file-view.ts", "feed.ts", "code-block.ts", "viewer-grammars.ts", "fence-source.ts"]) {
    assert.doesNotMatch(read(f), /from "\.\/render"/, f + " imports nothing from the chat (the feed bundle must not carry it)");
  }
});

test("mdBlock: the raw text is captured before the highlight rewrite, a named registered language is highlighted (never guessed), then EVERY fence is wrapped and given Copy with the text the file holds", () => {
  const fn = VIEW.slice(VIEW.indexOf("function mdBlock("), VIEW.indexOf("function imgBlock("));
  const pass = fn.slice(fn.indexOf('box.querySelectorAll("pre code").forEach'));
  assert.match(pass, /const raw = codeEl\.textContent \|\| "";/);
  assert.ok(pass.indexOf("const raw = codeEl.textContent") < pass.indexOf("codeEl.innerHTML = hljs.highlight(raw"), "raw first, then the rewrite highlights that same string");
  assert.match(pass, /if \(lang && hljs\.getLanguage\(lang\)\) \{/, "highlight only a named, registered language");
  assert.doesNotMatch(VIEW, /hljs\.highlightAuto\(/, "no guessing in the viewer");
  // the wrap and the Copy button sit OUTSIDE the language branch, so an unregistered or unnamed fence gets them too: the
  // branch, cut from its head to its own closing brace, holds neither call, and the two calls are the callback's last
  // statements, closing the forEach (a `}` before the wrap would also match the catch's, so the branch is read whole)
  const branchAt = pass.indexOf("if (lang && hljs.getLanguage(lang)) {");
  const branch = pass.slice(branchAt, pass.indexOf("\n    }\n", branchAt) + 7);
  assert.match(branch, /^if \(lang && hljs\.getLanguage\(lang\)\) \{\n[\s\S]*\} catch \{ \/\* leave plain \*\/ \}\n    \}\n$/, "the language branch is read whole, head to its closing brace");
  assert.doesNotMatch(branch, /wrapCodeLines|addCopyBtn/, "the language branch highlights only: no rows, no Copy inside it");
  assert.match(pass, /\n    wrapCodeLines\(codeEl\);\n    if \(host\) addCopyBtn\(host, toCopy\);\n  \}\);/, "the rows and the Copy button are the callback's last statements, for every fence");
  // what Copy copies is the fence's text as the FILE holds it (fence-source.ts): the raw text is marked's, its leading tabs
  // already four spaces each; the queue is keyed by the raw text and consumed in document order, and a fence the module
  // did not find in the file falls back to the raw text
  assert.match(pass, /const queued = copySources\.get\(raw\);\n\s*const toCopy = \(queued && queued\.length \? queued\.shift\(\) : null\) \?\? raw;/, "Copy's text comes off the source queue, the raw text when the fence was not found");
  assert.match(fn, /const copySources = fenceCopyQueue\(text, fences\);/, "the queue is built from the file and the code tokens the parse collected");
  assert.match(fn, /if \(t\.type === "code"\) \{ const c = t as Tokens\.Code; fences\.push\(\{ text: c\.text, indented: c\.codeBlockStyle === "indented" \}\); \}/, "the parse's walkTokens collects every code token");
  assert.match(fn, /const base = marked\.defaults\.walkTokens;/, "a walkTokens an extension put on the defaults still runs: per-call options replace, so it is chained");
  assert.match(VIEW, /^import \{ fenceCopyQueue, type Fence \} from "\.\/fence-source";/m);
  assert.ok(pass.indexOf("wrapCodeLines(codeEl)") > pass.indexOf('codeEl.classList.add("hljs")'), "the rows are cut after the highlight, as in the chat");
  // the counter reset is the sheets' `.fileview-md pre code`, not `code.hljs`: a plain fence carries no hljs class
  assert.ok(!/wrapCodeLines\(codeEl\);[\s\S]{0,80}classList\.add\("hljs"\)/.test(pass), "no hljs class is added to a plain fence for the counter's sake (the sheet resets on pre code)");
  // a task item wears GitHub's class, stamped after the sanitize for the disabled checkbox marked emits as the item's first child
  // (file-view-text-size.test.ts's browser leg runs the stamp over marked's own output, sanitized, in a real DOM)
  assert.match(fn, /box\.querySelectorAll\('li > input\[type="checkbox"\]:first-child:disabled, li > p:first-child > input\[type="checkbox"\]:first-child:disabled'\)\.forEach\(\(input\) => \{\n\s*if \(input\.previousSibling\) return;/, "the task stamp's selector, then the first-node check: :first-child counts elements alone, and a checkbox after the item's text is not a task item");
  assert.match(fn, /if \(li\) li\.classList\.add\("task-list-item"\);/);
  assert.ok(fn.indexOf("sanitizeMd(dirty)") < fn.indexOf('li.classList.add("task-list-item")'), "stamped after the sanitize, so the class is never one the file wrote");
});

test("the chat's highlight() is unchanged in shape: raw first, the cache, the rows when lineNos, Copy on the <pre>", () => {
  const hl = RENDER.slice(RENDER.indexOf("function highlight(container: HTMLElement"), RENDER.indexOf("function dot("));
  assert.match(hl, /const raw = code\.textContent \|\| "";/);
  assert.match(hl, /code\.innerHTML = highlightHtml\(hljs, lang, raw\);/);
  assert.match(hl, /if \(lineNos\) wrapCodeLines\(code\);/);
  assert.match(hl, /if \(pre && pre\.tagName === "PRE"\) addCopyBtn\(pre as HTMLElement, raw\);/);
});

// ── the sheets ─────────────────────────────────────────────────────────────────────────────────────

const ruleOf = (css: string, head: string): string => { const at = css.indexOf(head); assert.ok(at >= 0, head + " present"); return css.slice(at, css.indexOf("}", at) + 1); };
const decls = (rule: string): string[] => rule.slice(rule.indexOf("{") + 1, -1).split(";").map((d) => d.trim()).filter(Boolean);

test("both sheets: the viewer's fences carry scoped copies of the chat's row and Copy rules, byte-equal, the counter reset and tab-size on `.fileview-md pre code`", () => {
  const HEADS = [".fileview-md pre code .cl {", ".fileview-md pre code .cl::before {", ".fileview-md pre code .ct {", ".fileview-md pre code .ct::before {", ".fileview-md pre.has-copy {", ".fileview-md .code-copy {",
    ".fileview-md pre.has-copy:hover .code-copy, .fileview-md .code-copy:focus-visible {", ".fileview-md .code-copy:hover {", ".fileview-md .code-copy.copied {"];
  for (const head of HEADS) assert.equal(ruleOf(CHAT, head), ruleOf(FEED, head), head + " mirrors exactly");
  for (const [name, css] of [["styles.css", CHAT], ["feed.css", FEED]] as const) {
    const code = decls(ruleOf(css, ".fileview-md pre code {"));
    assert.ok(code.includes("counter-reset: ln"), name + ": the line counter resets on every fence, hljs class or not");
    assert.ok(code.includes("tab-size: 4"), name + ": tabs as the Raw view shows them");
    assert.ok(decls(ruleOf(css, ".fileview-pre {")).includes("tab-size: 4"), name + ": ...which is 4");
    // the copies are the chat's rules with the viewer's scope in front, declaration for declaration
    assert.deepEqual(decls(ruleOf(css, ".fileview-md pre code .cl {")), decls(ruleOf(CHAT, "\npre code .cl {")), name + ": .cl is the chat's");
    assert.deepEqual(decls(ruleOf(css, ".fileview-md pre code .cl::before {")), decls(ruleOf(CHAT, "\npre code .cl::before {")), name + ": the gutter is the chat's");
    assert.deepEqual(decls(ruleOf(css, ".fileview-md pre code .ct {")), decls(ruleOf(CHAT, "\npre code .ct {")), name + ": .ct is the chat's");
    // a blank line's row is the line-height (the .ct's ::before is a word joiner, the line box an empty .ct had none of), and
    // the gutter's basis follows the fence's digit count, which wrapCodeLines writes on the code element
    assert.deepEqual(decls(ruleOf(css, ".fileview-md pre code .ct::before {")), ['content: "\\2060"'], name + ": the .ct's ::before is the word joiner, U+2060 (zero width, unbreakable)");
    assert.deepEqual(decls(ruleOf(css, ".fileview-md pre code .ct::before {")), decls(ruleOf(CHAT, "\npre code .ct::before {")), name + ": ...the chat's rule");
    const gutter = decls(ruleOf(css, ".fileview-md pre code .cl::before {"));
    assert.ok(gutter.includes("flex: 0 0 max(2.5em, calc(var(--ln-digits, 0) * 1ch + 0.05em))"), name + ": the gutter's basis is 2.5em or the fence's digits in ch, whichever is wider");
    assert.ok(gutter.includes("line-height: 1") && gutter.includes("overflow-wrap: normal"), name + ": the gutter's line box is never taller than the text's, and a number never wraps");
    const copy = decls(ruleOf(css, ".fileview-md .code-copy {"));
    const chatCopy = decls(ruleOf(CHAT, "\n.code-copy {"));
    assert.deepEqual(copy.filter((d) => !d.startsWith("background")), chatCopy.filter((d) => !d.startsWith("background")), name + ": the Copy button is the chat's, but for the tint's fallback");
    assert.ok(copy.includes("background: var(--code-bg, rgba(217, 119, 87, 0.10))"), name + ": the code tint carries the chat's literal as its fallback (feed.css defines no --code-bg in :root)");
    assert.match(css, /@media \(hover: none\) \{ \.fileview-md \.code-copy \{ opacity: 0\.8; \} \}/, name + ": touch keeps the button visible");
  }
  // every var() the feed's copies use resolves on the feed page (feed-css-vars.test.ts holds the whole sheet; this names the block)
  const defined = new Set([...FEED.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)].map((m) => m[1]));
  const block = FEED.slice(FEED.indexOf(".fileview-md pre code .cl {"), FEED.indexOf("@media (hover: none) { .fileview-md .code-copy"));
  for (const m of block.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)\s*\)/g)) assert.ok(defined.has(m[1]), "feed.css defines " + m[1] + " (a fallback-less var in the fence block)");
});

// ── the grammars ───────────────────────────────────────────────────────────────────────────────────

test("viewer-grammars.ts registers six more grammars, with their aliases, on the bundle's one hljs core; file-view.ts imports it and the chat auto-detects among its ten only", () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { VIEWER_GRAMMARS } = require("./viewer-grammars") as typeof import("./viewer-grammars");
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { AUTO_LANGUAGES } = require("./highlight-cache") as typeof import("./highlight-cache");
  assert.deepEqual(Object.keys(VIEWER_GRAMMARS).sort(), ["c", "go", "golang", "h", "ini", "java", "rs", "rust", "sql", "toml"].sort());
  for (const name of ["rust", "rs", "go", "golang", "c", "h", "java", "sql", "toml", "ini"]) assert.ok(hljs.getLanguage(name), name + " is registered once the module is imported");
  assert.equal(hljs.getLanguage("toml")!.name, "TOML, also INI", "toml is hljs's ini grammar (hljs ships no toml module; the two names register one grammar)");
  assert.equal(hljs.getLanguage("ini")!.name, hljs.getLanguage("toml")!.name);
  assert.equal(hljs.highlight("fn main() {}", { language: "rust" }).value.includes("hljs-keyword"), true, "the rust grammar tokenizes");
  assert.equal(hljs.highlight("[section]\nkey = 1", { language: "toml" }).value.includes("hljs-"), true, "the toml alias tokenizes");
  assert.match(VIEW, /^import "\.\/viewer-grammars";/m, "the viewer reaches the six (feed.ts imports file-view.ts; the chat bundle does too)");
  assert.doesNotMatch(RENDER, /viewer-grammars/, "render.ts imports the module through file-view.ts, not on its own");
  // the chat's ten, by the grammar imports in render.ts, are the auto-detection subset
  const ten = [...RENDER.matchAll(/^import \w+ from "highlight\.js\/lib\/languages\/(\w+)";/gm)].map((m) => m[1]).sort();
  assert.equal(ten.length, 10, "render.ts imports ten grammars: " + ten.join(","));
  assert.deepEqual([...AUTO_LANGUAGES].sort(), ten, "AUTO_LANGUAGES is exactly the chat's ten");
  assert.deepEqual([...VIEW.matchAll(/^import \w+ from "highlight\.js\/lib\/languages\/(\w+)";/gm)].map((m) => m[1]).sort(), ten, "file-view.ts registers the same ten itself; the six live in viewer-grammars.ts");
});

test("highlightHtml passes the ten-name subset to highlightAuto for an unlabeled fence, and names the grammar for a labeled one", () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { AUTO_LANGUAGES, highlightHtml, newHighlightCache } = require("./highlight-cache") as typeof import("./highlight-cache");
  const seen: unknown[][] = [];
  const hl = {
    getLanguage: (n: string) => (n === "python" ? {} : undefined),
    highlight: (raw: string, o: { language: string }) => { seen.push(["named", o.language]); return { value: "<n>" + raw + "</n>" }; },
    highlightAuto: (raw: string, subset?: readonly string[]) => { seen.push(["auto", subset]); return { value: "<a>" + raw + "</a>" }; },
  };
  const c = newHighlightCache();
  assert.equal(highlightHtml(hl, "python", "x = 1", c), "<n>x = 1</n>");
  assert.equal(highlightHtml(hl, undefined, "x = 1", c), "<a>x = 1</a>");
  assert.equal(highlightHtml(hl, "rust", "x = 1", c), "<a>x = 1</a>", "a language this fake does not know reads as unlabeled: the cache's key for it is the unlabeled one, so this is the entry above, a hit (in the chat bundle rust IS known, through file-view.ts)");
  assert.deepEqual(seen, [["named", "python"], ["auto", AUTO_LANGUAGES]], "one auto-detection, with the subset");
  assert.equal(seen[1][1], AUTO_LANGUAGES, "the very list, not a copy");
  assert.equal(highlightHtml(hl, undefined, "y = 2", c), "<a>y = 2</a>");
  assert.equal(seen.length, 3); assert.equal(seen[2][1], AUTO_LANGUAGES, "every auto-detection carries it");
});
