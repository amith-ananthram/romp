// The empty pane's placeholder, DRIVEN on a stub DOM (T355): a viewer's loader gives way to the stall with its Retry and to the
// kernel's error sentence when the kind changes, the same kind twice is left standing, and the Retry asks again.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { placeholderKind, placeholderStands, fillPlaceholder } from "./pane-placeholder";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

function fakeEl(tag = "div", cls = ""): any {
  const classes = new Set(cls ? cls.split(" ") : []);
  const el: any = { tag, textContent: "", dataset: {}, children: [] as any[], type: "", className: "", onclick: null as any,
                    classList: { add: (c: string) => classes.add(c), contains: (c: string) => classes.has(c), remove: (c: string) => classes.delete(c) },
                    appendChild(c: any) { el.children.push(c); return c; } };
  return el;
}
const ctx = (retries: number[]) => ({
  el: fakeEl, loader: (t: string) => { const l = fakeEl("div", "romp-loader"); l.textContent = t; return l; },
  button: () => fakeEl("button"), br: () => fakeEl("br"),
  text: { error: null as string | null, failedRevive: null, stall: "stalled after 15 seconds", sessionName: "web" },
  onRetry: () => { retries.push(1); },
});

/** The pane's own rule, as render.ts applies it: rebuild unless the standing placeholder is of this kind. */
function paint(pane: { children: any[] }, st: any, c: any): any {
  const only = pane.children.length === 1 ? pane.children[0] : null;
  const kind = placeholderKind(st);
  if (!only || !only.classList.contains("tx-empty") || !placeholderStands(only, kind)) {
    pane.children.length = 0;
    const ph = fakeEl("div", "tx-empty"); pane.children.push(ph);
    fillPlaceholder(ph, kind, c);
  }
  return pane.children[0];
}

test("a viewer's loader gives way to the stall with a Retry, then to the kernel's error sentence, and a repeat of a kind stands", () => {
  const retries: number[] = []; const c = ctx(retries);
  const pane = { children: [] as any[] };
  const sub: any = { error: null, loaded: false, stalled: false };
  const p1 = paint(pane, { sub }, c);
  assert.equal(p1.dataset.ph, "sub-loading"); assert.ok(p1.classList.contains("tx-starting")); assert.equal(p1.children[0].textContent, "opening the agent's transcript…");
  const p1b = paint(pane, { sub }, c);
  assert.strictEqual(p1b, p1, "the same kind: the loader stands (no churn)");
  sub.stalled = true;                                    // the wait passed with no frame
  const p2 = paint(pane, { sub }, c);
  assert.notStrictEqual(p2, p1, "another kind: rebuilt");
  assert.equal(p2.dataset.ph, "sub-stall"); assert.match(p2.textContent, /stalled after 15 seconds/);
  const btn = p2.children.find((x: any) => x.tag === "button");
  assert.ok(btn && btn.textContent === "Retry", "…with a Retry");
  btn.onclick(); assert.deepEqual(retries, [1], "the Retry asks again");
  sub.error = "The transcript file for agent a1 is missing beside this session's transcript, so it can't be shown."; sub.loaded = true; sub.stalled = false;
  c.text.error = sub.error;
  const p3 = paint(pane, { sub }, c);
  assert.equal(p3.dataset.ph, "sub-error"); assert.match(p3.textContent, /missing beside/); assert.ok(p3.classList.contains("tx-revive-failed"));
  sub.error = null;                                      // a later frame with events... none: the agent wrote nothing
  const p4 = paint(pane, { sub }, c);
  assert.equal(p4.dataset.ph, "sub-empty"); assert.equal(p4.textContent, "This agent has written nothing yet.");
});

test("the loader painted at open also gives way straight to the error frame (the user's 'both kernels up' case)", () => {
  const c = ctx([]); const pane = { children: [] as any[] };
  const sub: any = { error: null, loaded: false, stalled: false };
  paint(pane, { sub }, c);
  sub.error = "missing"; sub.loaded = true; c.text.error = "missing";
  assert.equal(paint(pane, { sub }, c).dataset.ph, "sub-error");
});

test("the kinds: a failed revive first, a starting tab's loader and its failed create, a plain empty session", () => {
  assert.equal(placeholderKind({ failedRevive: "gone", sub: { error: "x", loaded: true } }), "revive-failed");
  assert.equal(placeholderKind({ provisional: true }), "starting");
  assert.equal(placeholderKind({ provisional: true, provisionalFailed: true }), "start-failed");
  assert.equal(placeholderKind({}), "empty");
  const c = ctx([]);
  const st = fillPlaceholder(fakeEl("div", "tx-empty"), "starting", { ...c, swirl: () => fakeEl("img", "tx-starting-swirl") });
  assert.ok(st.classList.contains("tx-starting")); assert.match(st.children[1].textContent, /^Starting web…/);
  assert.equal(fillPlaceholder(fakeEl("div", "tx-empty"), "empty", c).textContent, "No messages yet.");
  assert.equal(placeholderStands(null, "empty"), false);
});

test("render.ts paints through the module and re-asks only the viewers still waiting, on every reconnect-class event", () => {
  const branch = RENDER.slice(RENDER.indexOf("  if (s.events.length === 0) {\n    const only = v.el.childNodes.length === 1"), RENDER.indexOf("v.rendered = 0; v.stale = false; v.winStart = 0; v.winEnd = 0;"));
  assert.ok(branch.includes("const kind = placeholderKind({ sub: s.sub, failedRevive: failedRevives.get(id) || null,"), "the kind decided from the session's state");
  assert.ok(branch.includes('if (!only || !only.classList?.contains("tx-empty") || !placeholderStands(only, kind)) {'), "rebuilt when the kind changed");
  assert.ok(branch.includes("fillPlaceholder(ph, kind, {"), "…and filled by the module");
  assert.ok(RENDER.includes("if (s.sub && (!s.sub.loaded || s.sub.stalled) && (host === undefined || hostOf(s.sub.parentId) === host)) askSubagent(id);"), "waiting viewers only, the named host's alone");
  for (const ev of ['window.addEventListener("romp:wsup", () => reaskWaitingSubagents(""));', 'reaskWaitingSubagents(h);', 'if (m.type === "pipeState" && m.up) reaskWaitingSubagents();'])
    assert.ok(RENDER.includes(ev), ev);
});
