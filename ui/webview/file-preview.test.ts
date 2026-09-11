// The file preview popover's pure half, executed (T351 stage 1): the link's path and anchor, the kernel's verdict on
// what a link may preview, the routes' URLs, the one content shape per kind, and the hover's timing run on fake timers.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { PREVIEW_DWELL_MS, PREVIEW_GRACE_MS, HoverIntent, parsePreviewLink, previewKindOf, sliceUrl, fileUrl, langOf, baseName,
         contentFor, textOnlyContent, remoteLoad, stripRemoteLoads } from "./file-preview";

test("parsePreviewLink: path#slug splits on a slug, a # that is not a slug stays in the path", () => {
  assert.deepEqual(parsePreviewLink("/home/u/docs/glossary.md#fold"), { path: "/home/u/docs/glossary.md", anchor: "fold" });
  assert.deepEqual(parsePreviewLink("docs/guide.md#install-2"), { path: "docs/guide.md", anchor: "install-2" });
  assert.deepEqual(parsePreviewLink("docs/guide.md"), { path: "docs/guide.md", anchor: "" });
  assert.deepEqual(parsePreviewLink("notes/#tag file.md"), { path: "notes/#tag file.md", anchor: "" }, "a space after the # is no slug");
  assert.deepEqual(parsePreviewLink("a.md#Section"), { path: "a.md#Section", anchor: "" }, "a slug is lower-case");
  assert.deepEqual(parsePreviewLink("#only"), { path: "#only", anchor: "" });
});

test("previewKindOf: the kernel's map by token, then by open target; unknown kinds and absent tokens are text-only", () => {
  const pv = { "docs/guide.md": "markdown", "plots/a.png": "image", "src/app.py": "code", "report.pdf": "pdf", "odd.bin": "zip" };
  assert.equal(previewKindOf("docs/guide.md", pv), "markdown");
  assert.equal(previewKindOf("docs/guide.md#install", pv), "markdown", "the token's own path when the token carries an anchor");
  assert.equal(previewKindOf("plots/a.png", pv), "image");
  assert.equal(previewKindOf("src/app.py", pv), "code");
  assert.equal(previewKindOf("report.pdf", pv), "pdf");
  assert.equal(previewKindOf("odd.bin", pv), null, "a kind the card cannot show");
  assert.equal(previewKindOf("elsewhere.md", pv), null, "absent: the kernel allowed no preview");
  assert.equal(previewKindOf("docs/guide.md", undefined), null, "no map at all (an older kernel): no request");
});

test("the routes: the slice with its anchor and session, the bytes route for media", () => {
  assert.equal(sliceUrl("docs/a b.md", "sid1", "x-y"), "/file?path=docs%2Fa%20b.md&slice=1&sid=sid1&anchor=x-y");
  assert.equal(sliceUrl("/abs/a.md", null, ""), "/file?path=%2Fabs%2Fa.md&slice=1");
  assert.equal(fileUrl("plots/a.png", "s"), "/file?path=plots%2Fa.png&sid=s");
  assert.equal(langOf("src/app.py"), "python"); assert.equal(langOf("x.tsx"), "typescript"); assert.equal(langOf("Makefile"), "");
  assert.equal(baseName("/a/b/c.md"), "c.md"); assert.equal(baseName("c.md"), "c.md");
});

test("contentFor fills the one shape per kind; a missing anchor falls back to the head with a one-line note", () => {
  const md = contentFor("docs/g.md", "", "markdown", "s", { title: "g.md", text: "# G\nbody", found: true, allowed: true });
  assert.deepEqual(md, { kind: "markdown", title: "g.md", subtitle: undefined, body: { markdown: "# G\nbody" }, note: undefined, open: { label: "open", path: "docs/g.md", frag: undefined } });
  const sec = contentFor("docs/g.md", "fold", "markdown", "s", { title: "g.md", text: "## Fold\nabout folds", found: true, allowed: true });
  assert.equal(sec.kind, "section"); assert.equal(sec.subtitle, "#fold"); assert.equal(sec.open!.frag, "fold");
  const miss = contentFor("docs/g.md", "nope", "markdown", "s", { title: "g.md", text: "# G\nhead", found: false, allowed: true });
  assert.equal(miss.kind, "markdown"); assert.equal(miss.subtitle, undefined);
  assert.equal(miss.note, 'no section "nope" in this file; its head instead'); assert.equal(miss.open!.frag, "nope", "open still tries the anchor the link named");
  const cut = contentFor("docs/g.md", "", "markdown", "s", { title: "g.md", text: "…", found: true, truncated: true, allowed: true });
  assert.equal(cut.note, "the head of the file; open for the rest");
  const code = contentFor("src/app.py", "", "code", "s", { title: "app.py", text: "print(1)", allowed: true });
  assert.deepEqual(code.body, { text: "print(1)", lang: "python" }); assert.equal(code.kind, "code");
  const img = contentFor("plots/a.png", "", "image", "s", null);
  assert.deepEqual(img, { kind: "image", title: "a.png", body: { url: "/file?path=plots%2Fa.png&sid=s" }, open: { label: "open", path: "plots/a.png", frag: undefined } });
  const pdf = contentFor("r.pdf", "", "pdf", null, null);
  assert.equal(pdf.kind, "pdf"); assert.equal(pdf.subtitle, "first page"); assert.equal(pdf.body.url, "/file?path=r.pdf");
  const refused = contentFor("x.md", "", "markdown", "s", { allowed: false, why: "a secrets-shaped name" });
  assert.equal(refused.kind, "text"); assert.equal(refused.note, "a secrets-shaped name"); assert.equal(refused.body.text, "x.md");
  const t = textOnlyContent("/etc/hosts", "", "outside");
  assert.deepEqual(t, { kind: "text", title: "hosts", subtitle: undefined, body: { text: "/etc/hosts" }, note: "outside", open: { label: "open", path: "/etc/hosts", frag: undefined } });
});

// a fake clock: timers fire in order when advanced
function clock() {
  let now = 0, id = 0;
  const due: { at: number; id: number; fn: () => void }[] = [];
  const setT = (fn: () => void, ms: number) => { const t = { at: now + ms, id: ++id, fn }; due.push(t); return t.id as unknown as ReturnType<typeof setTimeout>; };
  const clearT = (h: ReturnType<typeof setTimeout>) => { const i = due.findIndex((d) => d.id === (h as unknown as number)); if (i >= 0) due.splice(i, 1); };
  const advance = (ms: number) => { now += ms; due.sort((a, b) => a.at - b.at); while (due.length && due[0].at <= now) due.shift()!.fn(); };
  return { setT, clearT, advance };
}

test("HoverIntent: a hover opens after the dwell, a pass-through never opens, leaving closes after the grace", () => {
  const c = clock(); const log: string[] = [];
  const h = new HoverIntent<string>(PREVIEW_DWELL_MS, PREVIEW_GRACE_MS, (t) => log.push("open " + t), () => log.push("close"), c.setT, c.clearT);
  h.enter("a"); c.advance(PREVIEW_DWELL_MS - 1); assert.deepEqual(log, [], "not yet");
  c.advance(1); assert.deepEqual(log, ["open a"]); assert.equal(h.open, "a");
  h.leave(); c.advance(PREVIEW_GRACE_MS - 1); assert.deepEqual(log, ["open a"], "the grace is still running");
  c.advance(1); assert.deepEqual(log, ["open a", "close"]); assert.equal(h.open, null);
  h.enter("b"); c.advance(100); h.leave(); c.advance(1000); assert.deepEqual(log, ["open a", "close"], "a pass-through under the dwell opens nothing");
});

test("HoverIntent: crossing into the card keeps it (pin), leaving the card closes it; a second link retargets; cancel closes at once", () => {
  const c = clock(); const log: string[] = [];
  const h = new HoverIntent<string>(PREVIEW_DWELL_MS, PREVIEW_GRACE_MS, (t) => log.push("open " + t), () => log.push("close"), c.setT, c.clearT);
  h.enter("a"); c.advance(PREVIEW_DWELL_MS);
  h.leave(); c.advance(PREVIEW_GRACE_MS / 2); h.pin(); c.advance(PREVIEW_GRACE_MS * 3);
  assert.deepEqual(log, ["open a"], "pinned inside the card: it stays through the grace");
  h.unpin(); c.advance(PREVIEW_GRACE_MS); assert.deepEqual(log, ["open a", "close"]);
  h.enter("a"); c.advance(PREVIEW_DWELL_MS); h.leave(); h.enter("b"); c.advance(PREVIEW_GRACE_MS * 2);
  assert.deepEqual(log, ["open a", "close", "open a"], "moving to another link within the grace keeps the card up while its dwell runs");
  c.advance(PREVIEW_DWELL_MS); assert.deepEqual(log, ["open a", "close", "open a", "open b"], "…then it shows the new link");
  h.enter("b"); c.advance(PREVIEW_DWELL_MS * 2); assert.deepEqual(log, ["open a", "close", "open a", "open b"], "re-entering the shown link is a no-op");
  h.cancel(); assert.deepEqual(log, ["open a", "close", "open a", "open b", "close"]); assert.equal(h.open, null);
  h.cancel(); assert.equal(log.length, 5, "cancel with nothing open is silent");
});

// ── the remote-load strip (the review: a hover must never send a request elsewhere) ──

const ORIGIN = "http://127.0.0.1:7777", BASE = ORIGIN + "/chat?token=t";

test("remoteLoad reads a value the way the browser does: every spelling of another origin is remote", () => {
  const remote = ["https://remote.invalid/a.png", "//remote.invalid/a.png", "http://remote.invalid:8080/x",
                  "\\\\remote.invalid/b.png", "/\\remote.invalid/c.png", " https://remote.invalid/d.png", "\thttps://remote.invalid/e.png",
                  // TAB, LF and CR anywhere in the value are deleted by the browser before it reads the URL (the review:
                  // a whitespace split before the parse read each of these as two local tokens)
                  "/\t/remote.invalid/p.png", "/\n/remote.invalid/p.png", "/\r/remote.invalid/p.png", "\\\t\\remote.invalid/p.png",
                  "ht\ttps://remote.invalid/q.png", "//remote.in\nvalid/r.png",
                  "javascript:alert(1)", "about:blank", "blob:https://remote.invalid/0000"];
  for (const v of remote) assert.equal(remoteLoad(v, ORIGIN, BASE), true, JSON.stringify(v));
  const remoteSets = ["https://remote.invalid/a.png 1x, https://remote.invalid/b.png 2x", "plots/x.png 1x, https://remote.invalid/y.png 2x",
                      "/\t/remote.invalid/a.png 1x", "plots/x.png 1x,\n//remote.invalid/z.png 2x"];
  for (const v of remoteSets) assert.equal(remoteLoad(v, ORIGIN, BASE, true), true, JSON.stringify(v));
  const local = ["/file?path=x.png", "plots/figure.png", "./a.png", "../b.png", ORIGIN + "/file?path=a", "#frag", "?q=1",
                 "data:image/png;base64,iVBORw0KGgo=", "blob:" + ORIGIN + "/0000-1111", "", "/file?a=1,2", "plots/a b.png"];
  for (const v of local) assert.equal(remoteLoad(v, ORIGIN, BASE), false, JSON.stringify(v));
  const localSets = ["data:image/png;base64,AAAA,BBBB 1x", "plots/a.png 1x, plots/b.png 2x", "/file?path=a.png 480w,\n/file?path=b.png 960w"];
  for (const v of localSets) assert.equal(remoteLoad(v, ORIGIN, BASE, true), false, JSON.stringify(v));
});

/** A minimal inert element tree: what stripRemoteLoads reads (localName, getAttribute) and does (replaceWith, remove). */
type FakeEl = { localName: string; attrs: Record<string, string>; parent: FakeEl | null; children: (FakeEl | string)[];
                getAttribute(n: string): string | null; replaceWith(n: unknown): void; remove(): void; ownerDocument: { createTextNode(s: string): string } };
function fakeEl(localName: string, attrs: Record<string, string> = {}, children: FakeEl[] = []): FakeEl {
  const e: FakeEl = {
    localName, attrs, parent: null, children: [...children],
    getAttribute(n) { return Object.prototype.hasOwnProperty.call(attrs, n) ? attrs[n] : null; },
    replaceWith(n) { if (!e.parent) return; const i = e.parent.children.indexOf(e); e.parent.children.splice(i, 1, n as string); e.parent = null; },
    remove() { if (!e.parent) return; const i = e.parent.children.indexOf(e); e.parent.children.splice(i, 1); e.parent = null; },
    ownerDocument: { createTextNode: (s) => s },
  };
  for (const c of children) c.parent = e;
  return e;
}
function all(e: FakeEl): FakeEl[] { const out: FakeEl[] = []; for (const c of e.children) if (typeof c !== "string") { out.push(c, ...all(c)); } return out; }
function asRoot(e: FakeEl): ParentNode { return { querySelectorAll: () => all(e) } as unknown as ParentNode; }
function serialize(e: FakeEl): string {
  return e.children.map((c) => typeof c === "string" ? JSON.stringify(c) : "<" + c.localName + Object.entries(c.attrs).map(([k, v]) => " " + k + "=" + JSON.stringify(v)).join("") + ">" + serialize(c) + "</" + c.localName + ">").join("");
}

test("stripRemoteLoads on the inert tree: an img becomes its alt text, every other remote loader goes, local loads stay", () => {
  const root = fakeEl("body", {}, [
    fakeEl("img", { srcset: "https://remote.invalid/a.png 1x", alt: "srcset-only" }),
    fakeEl("picture", {}, [fakeEl("source", { srcset: "https://remote.invalid/b.png" }), fakeEl("img", { src: "plots/figure.png", alt: "pic" })]),
    fakeEl("video", { poster: "https://remote.invalid/p.png" }, [fakeEl("source", { src: "/file?path=v.mp4" })]),
    fakeEl("video", { src: "/file?path=local.mp4" }, [fakeEl("track", { src: "https://remote.invalid/t.vtt" })]),
    fakeEl("audio", { src: "https://remote.invalid/a.mp3" }),
    fakeEl("svg", {}, [fakeEl("image", { href: "https://remote.invalid/s.png" }), fakeEl("image", { "xlink:href": "//remote.invalid/x.png" }), fakeEl("image", { href: "/file?path=ok.png" })]),
    fakeEl("img", { src: "\\\\remote.invalid/c.png", alt: "backslashes" }),
    fakeEl("img", { src: "\thttps://remote.invalid/d.png", alt: "tabbed" }),
    fakeEl("img", { src: "/\t/remote.invalid/f.png", alt: "tab-inside" }),
    fakeEl("img", { src: "https://remote.invalid/e.png" }),
    fakeEl("img", { src: "data:image/png;base64,iVBORw0KGgo=", alt: "inline" }),
    fakeEl("img", { src: ORIGIN + "/file?path=plots/figure.png", alt: "ours" }),
    fakeEl("a", { href: "https://remote.invalid/page" }, [fakeEl("span", {}, [])]),
    fakeEl("p", {}, []),
  ]);
  const n = stripRemoteLoads(asRoot(root), ORIGIN, BASE);
  assert.equal(n, 11, "eleven remote loaders: the poster'd video's own source is local and goes with its parent, uncounted");
  assert.equal(serialize(root),
    '"srcset-only"'
    + '<picture><img src="plots/figure.png" alt="pic"></img></picture>'
    + '<video src="/file?path=local.mp4"></video>'
    + '<svg><image href="/file?path=ok.png"></image></svg>'
    + '"backslashes""tabbed""tab-inside"""'
    + '<img src="data:image/png;base64,iVBORw0KGgo=" alt="inline"></img>'
    + '<img src="' + ORIGIN + '/file?path=plots/figure.png" alt="ours"></img>'
    + '<a href="https://remote.invalid/page"><span></span></a>'
    + '<p></p>',
    "the remote img is its alt text (empty alt: nothing), the poster'd video and its source are gone, the remote track is gone from the local video, the svg keeps its local image, a link is not a load");
});

