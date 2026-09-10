// A markdown-inline image whose fetch fails is PARKED, never re-set per message (T291c, the user 2026-09-09: three
// captions in a remote session's transcript flipped on and off at the push rate; the 2026-08-24 heal re-set each failed
// img's src on every kernel message, hiding the alt text while the reload ran and showing it again on the 404). The
// heal is EXECUTED here over a minimal fake DOM: the capture-phase listener parks the img (no src, the alt text as a
// stable caption); sixty pushes write the src zero times; the reconnect-class heal probes each parked URL once OFF the
// DOM, a failed probe changes nothing, a successful one lands the picture on every parked img with that URL on the
// page at that moment (a re-render during the probe included); a URL the kernel serves gets a bounded off-DOM probe on
// the per-message path; and the chat's post-pass parks a re-rendered img with a remembered URL before the browser
// fetches it. Every test builds its own state.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

class FakeEl {
  tagName: string; attrs: Record<string, string> = {}; _cls = new Set<string>(); dataset: Record<string, string> = {};
  isConnected = true; onerror: any = null; children: FakeEl[] = []; srcWrites = 0;
  constructor(tag: string) { this.tagName = tag.toUpperCase(); }
  get src(): string { return this.attrs.src || ""; }
  set src(v: string) { this.srcWrites++; this.attrs.src = v; }   // a src write is a fetch, and hides the alt text until the response
  get alt(): string { return this.attrs.alt || ""; }
  set alt(v: string) { this.attrs.alt = v; }
  hasAttribute(k: string) { return k in this.attrs; }
  getAttribute(k: string) { return this.attrs[k] ?? null; }
  setAttribute(k: string, v: string) { if (k === "src") this.srcWrites++; this.attrs[k] = v; }
  removeAttribute(k: string) { if (k === "src") this.srcWrites++; delete this.attrs[k]; }
  get classList() { const s = this._cls; return { add: (...c: string[]) => c.forEach((x) => s.add(x)), remove: (...c: string[]) => c.forEach((x) => s.delete(x)), contains: (c: string) => s.has(c) }; }
  closest() { return null; }
  querySelectorAll(sel: string): FakeEl[] { return sel === "img" ? this.children.filter((c) => c.tagName === "IMG") : []; }
  shape(): string { return `${this.tagName}[${Object.entries(this.attrs).sort().map(([k, v]) => k + "=" + v).join(" ")}].${[...this._cls].sort().join(".")}{${JSON.stringify(this.dataset)}}`; }
}
const live: FakeEl[] = [];                            // what document.querySelectorAll sees
let errorListener: ((e: any) => void) | null = null;
(globalThis as any).document = {
  addEventListener: (type: string, fn: any, capture?: boolean) => { if (type === "error") { assert.equal(capture, true, "capture phase"); errorListener = fn; } },
  querySelectorAll: (sel: string) => (sel === "img.md-img-failed[data-md-src]" ? live.filter((e) => e.isConnected && e.tagName === "IMG" && e._cls.has("md-img-failed") && e.dataset.mdSrc) : []),
  createElement: (t: string) => new FakeEl(t),
};
(globalThis as any).location = { protocol: "http:", origin: "http://127.0.0.1:1", href: "http://127.0.0.1:1/chat" };
(globalThis as any).window = (globalThis as any).window || {};
const probes: FakeEl[] = [];
(globalThis as any).Image = class extends FakeEl { onload: any = null; constructor() { super("img"); probes.push(this); } };
let fetchCalls = 0;
(globalThis as any).fetch = () => { fetchCalls++; return Promise.reject(new Error("no fetch expected")); };

const ORIGIN_URL = (n: string) => "http://127.0.0.1:1/home/user/notes-api/plots/" + n;   // a path the browser resolved against the page: no route serves it
const SERVED_URL = "http://127.0.0.1:1/file?path=%2Fhome%2Fuser%2Fnotes-api%2Fplots%2Flatency.png&sid=11111111-2222-4333-8444-000000000001";
function mdImg(url: string, alt: string): FakeEl { const img = new FakeEl("img"); img.src = url; img.alt = alt; img.srcWrites = 0; live.push(img); return img; }
const fail = (img: FakeEl) => errorListener!({ target: img });
async function fresh() { const P = await import("./preview"); P.installMdImgHeal(); assert.ok(errorListener); live.length = 0; probes.length = 0; return P; }

test("a failed markdown image is parked: no src, the alt text stays, and sixty pushes write the src zero times", async () => {
  const P = await fresh();
  const a = mdImg(ORIGIN_URL("schematic.png"), "Schematic"), b = mdImg(ORIGIN_URL("queue.png"), "Queued bubble with pencil and cross, composer under the editing pill");
  fail(a); fail(b);
  assert.equal(a.hasAttribute("src"), false, "the src is gone: the browser fetches nothing and shows the alt text");
  assert.equal(a.dataset.mdSrc, ORIGIN_URL("schematic.png"), "…the URL kept for the heal");
  assert.ok(a._cls.has("md-img-failed"));
  assert.equal(a.alt, "Schematic", "the caption is the alt text, untouched");
  const sa = a.shape(), sb = b.shape();
  a.srcWrites = 0; b.srcWrites = 0;
  for (let i = 0; i < 60; i++) P.retryFailedPreviews();   // what render.ts runs on every kernel message
  assert.equal(a.shape(), sa); assert.equal(b.shape(), sb);
  assert.equal(a.srcWrites + b.srcWrites, 0, "no src write on a push: a write is a fetch and hides the alt text until the response");
  assert.equal(probes.length, 0, "an origin-resolved path no route serves gets no probe either");
  assert.equal(fetchCalls, 0);
});

test("the reconnect-class heal probes each parked URL once off the DOM; a failure changes nothing, a success lands the picture on every parked img on the page then", async () => {
  const P = await fresh();
  const a = mdImg(ORIGIN_URL("schematic.png"), "Schematic"), b = mdImg(ORIGIN_URL("queue.png"), "Queued bubble");
  fail(a); fail(b);
  const sa = a.shape();
  P.refreshSettledPreviews();                        // romp:wsup / hostUp / romp:hostRelayUp all run this
  assert.equal(probes.length, 2, "one detached probe per parked URL");
  assert.deepEqual(probes.map((p) => p.src).sort(), [ORIGIN_URL("queue.png"), ORIGIN_URL("schematic.png")]);
  probes.forEach((p) => p.onerror());
  assert.equal(a.shape(), sa, "a failed probe leaves the parked caption exactly as it was");
  probes.length = 0;
  P.refreshSettledPreviews();
  assert.equal(probes.length, 2, "the next reconnect probes again, once per URL");
  // a re-render during the probe: the turn's fresh img is parked by the post-pass while the URL is still remembered
  a.isConnected = false;
  const root = new FakeEl("body"); const a2 = new FakeEl("img"); a2.src = ORIGIN_URL("schematic.png"); a2.alt = "Schematic"; root.children.push(a2);
  P.mdImgPostPass(root as unknown as ParentNode);
  assert.equal(a2.hasAttribute("src"), false, "the re-rendered img is parked before it fetches");
  live.push(a2);
  const pa = probes.find((p) => p.src === ORIGIN_URL("schematic.png"))!;
  (pa as any).onload();
  assert.equal(a2.src, ORIGIN_URL("schematic.png"), "the picture lands on the img that is on the page now, not on the snapshot's");
  assert.equal(a2._cls.has("md-img-failed"), false);
  assert.equal("mdSrc" in a2.dataset, false);
  assert.equal(b.hasAttribute("src"), false, "the other URL, still down, stays parked");
  // …and a URL that healed renders normally on the next re-render
  const root2 = new FakeEl("body"); const fine = new FakeEl("img"); fine.src = ORIGIN_URL("schematic.png"); root2.children.push(fine);
  P.mdImgPostPass(root2 as unknown as ParentNode);
  assert.equal(fine.src, ORIGIN_URL("schematic.png"), "a healed URL is no longer parked at render");
});

test("a URL the kernel serves gets a BOUNDED off-DOM probe on the per-message path, then the reconnect heal alone", async () => {
  const P = await fresh();
  const s = mdImg(SERVED_URL, "Latency");
  fail(s);
  const parked = s.shape();
  assert.equal(s.hasAttribute("src"), false, "parked like any other: the caption stands");
  s.srcWrites = 0;
  for (let round = 1; round <= 3; round++) {
    P.retryFailedPreviews();
    assert.equal(probes.length, round, "push " + round + ": one detached probe, the element untouched");
    assert.equal(s.shape(), parked);
    probes[probes.length - 1].onerror();
  }
  P.retryFailedPreviews(); P.retryFailedPreviews();
  assert.equal(probes.length, 3, "the budget is spent: no more per-message probes");
  assert.equal(s.srcWrites, 0, "the element's src was never written by an attempt");
  P.refreshSettledPreviews();
  assert.equal(probes.length, 4, "the reconnect-class heal still probes it");
  (probes[3] as any).onload();
  assert.equal(s.src, SERVED_URL, "…and the picture lands when the file is there");
});

test("render.ts installs the listener once, parks known-failed URLs on its OWN markdown output only, and files the page's bundle build once per load", () => {
  assert.match(RENDER, /installMdImgHeal\(\);/);
  assert.doesNotMatch(RENDER, /registerMdPostPass\(mdImgPostPass\)/, "never through the shared sanitizer: the file viewer rewrites its images' paths after the sanitize (the review's find)");
  assert.equal((RENDER.match(/mdImgPostPass\(clean\);/g) || []).length, 2, "md() and userMd() park a known-failed image before the browser fetches it");
  assert.match(RENDER, /linkifyPrRefs\(clean, repo\);\s*\n\s*mdImgPostPass\(clean\);[^\n]*\n\s*return clean\.innerHTML;/, "after the sanitize and the PR links, before the string leaves");
  // the page-load row: the ?v= of the render.js script this page loaded, one row per load
  assert.match(RENDER, /\.find\(\(u\) => \/\\\/dist\\\/render\\\.js\(\\\?\|\$\)\/\.test\(u\)\)/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "clientDiag", surface: "chat", what: "pageload", data: \{ distVer: m \? Number\(m\[1\]\) : 0, path: location\.pathname \} \}\);/);
  assert.match(CSS, /\.md-img-failed \{ display: inline; font-size: 0\.86em; font-style: italic; color: var\(--dim\); \}/, "the parked caption is styled like the load note");
});
