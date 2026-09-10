// Every picker answers the kernel's models frame with a fresh GET /models — and two of those fetches can
// overlap (a frame during the page-load fetch; two quick frames) and resolve OUT OF ORDER, so a stale list
// could land last and win until the next change. The payload carries `rev` (the pick memory's revision,
// the frame's own counter) and each consumer keeps the highest rev it applied, dropping an older response
// that lands late. EXECUTED against the chat's loader (lifted out of render.ts and transpiled) and the
// timeline's (the real module); the gear's twin runs in gear-models-frame.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const VIEW_PATH = path.join(ROOT, "ui", "romp-timeline-view.js");
const tick = () => new Promise((r) => setImmediate(r));
const list = (rev: number | undefined, def: string) =>
  ({ ...(rev === undefined ? {} : { rev }), models: [{ label: "Fable", value: "fable", default: def, versions: [] }], efforts: [] });

// A fetch whose responses the test resolves by hand, in any order.
function deferredFetch() {
  const pending: Array<(d: any) => void> = [];
  const fetch = () => new Promise<any>((res) => pending.push((d: any) => res({ ok: true, status: 200, json: async () => d })));   // a 200: the chat's loader now rejects a non-2xx response
  return { fetch, pending };
}

// The chat's loader: MODEL_CHOICES/EFFORT_CHOICES and loadModelChoices, lifted from render.ts and
// transpiled (TS → JS) with esbuild at run time — required dynamically so the test bundle does not try to
// bundle esbuild itself. The page-load call that follows the function is deliberately left out.
function liftRender() {
  const start = RENDER.indexOf("const MODEL_CHOICES: {");
  const fnAt = RENDER.indexOf("function loadModelChoices(): void {", start);
  const stop = RENDER.indexOf("\n}\n", fnAt) + 3;
  assert.ok(start > 0 && fnAt > start && stop > fnAt, "anchors not found — render.ts's models loader moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(start, stop), { loader: "ts" }).code;
  const { fetch, pending } = deferredFetch();
  const adopted: any[] = [];
  const fn = new Function("kernelUrl", "fetch", "adoptCommentDefaults",
    js + "\nreturn { loadModelChoices, MODEL_CHOICES, EFFORT_CHOICES };");
  const api = fn((p: string) => p, fetch, (d: any) => adopted.push(d));
  return { api, pending, adopted };
}

test("executed: the chat's loader keeps the newest rev it applied and drops an older response landing late", async () => {
  const { api, pending } = liftRender();
  const ref = api.MODEL_CHOICES;
  api.loadModelChoices();                                // page load, in flight
  api.loadModelChoices();                                // a frame arrives before it lands
  assert.equal(pending.length, 2);
  pending[1](list(8, "fable"));                          // the newer answers first
  await tick(); await tick();
  assert.equal(api.MODEL_CHOICES[0].default, "fable");
  pending[0](list(7, "claude-fable-5"));                 // the older lands late
  await tick(); await tick();
  assert.equal(api.MODEL_CHOICES[0].default, "fable", "the stale page-load list did not win");
  assert.equal(api.MODEL_CHOICES, ref, "still refilled in place — the shared reference holds");
  assert.deepEqual(api.MODEL_CHOICES[api.MODEL_CHOICES.length - 1], { label: "Default", value: "default" });
  // in order, a newer rev applies; equal revs apply (the same state re-read); no rev applies (older kernel)
  api.loadModelChoices(); pending[2](list(9, "claude-fable-5-1")); await tick(); await tick();
  assert.equal(api.MODEL_CHOICES[0].default, "claude-fable-5-1");
  api.loadModelChoices(); pending[3](list(9, "claude-fable-5")); await tick(); await tick();
  assert.equal(api.MODEL_CHOICES[0].default, "claude-fable-5", "an equal rev is the same state — applied");
  api.loadModelChoices(); pending[4](list(undefined, "fable")); await tick(); await tick();
  assert.equal(api.MODEL_CHOICES[0].default, "fable", "a payload without a rev always applies");
});

test("executed: the timeline's loader keeps the newest rev it applied and drops an older response landing late", async () => {
  const { loadModelChoices, MODEL_CHOICES } = requireCjs(VIEW_PATH);
  const realFetch = (globalThis as any).fetch;
  const { fetch, pending } = deferredFetch();
  (globalThis as any).fetch = fetch;
  try {
    const ref = MODEL_CHOICES;
    const p1 = loadModelChoices();                       // page load, in flight
    const p2 = loadModelChoices();                       // the frame's re-read (TimelinePanel.refreshModels)
    assert.equal(pending.length, 2);
    pending[1](list(8, "fable"));
    await p2;
    assert.equal(MODEL_CHOICES[0].default, "fable");
    pending[0](list(7, "claude-fable-5"));
    await p1;
    assert.equal(MODEL_CHOICES[0].default, "fable", "the stale page-load list did not win");
    assert.equal(MODEL_CHOICES, ref, "the menu builder's reference holds");
    const p3 = loadModelChoices(); pending[2](list(undefined, "claude-fable-5")); await p3;
    assert.equal(MODEL_CHOICES[0].default, "claude-fable-5", "a payload without a rev always applies");
  } finally {
    (globalThis as any).fetch = realFetch;
  }
});
