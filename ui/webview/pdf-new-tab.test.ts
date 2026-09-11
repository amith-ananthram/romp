// A PDF opens inside the dashboard on a plain click, like an image, and in its OWN browser tab on a
// Cmd/Ctrl- or middle-click (the user 2026-09-07, who wanted one opening rule for images and PDFs with
// the modifier as the way out; 2026-09-06, who wanted what OpenReview and HotCRP do: the paper full
// size in a tab of its own). The opener is ONE window.open inside the click gesture aimed at the
// kernel's /file URL — the browser's own viewer renders the inline application/pdf response from its
// cache, nothing lands on disk — and a null handle (the browser blocked the popup) falls back to the
// in-app view so the PDF is never unreachable. Executed here with a stubbed window/location; the
// wiring is pinned in source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { openPdfTab, fileUrl, wantsOwnTab } from "./preview";

const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const PREVIEW = fs.readFileSync(path.join(UI, "preview.ts"), "utf8");
const VIEW = fs.readFileSync(path.join(UI, "file-view.ts"), "utf8");
const BROWSE = fs.readFileSync(path.join(UI, "file-browse.ts"), "utf8");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "bin", "romp-kernel"), "utf8");
const GUIDE = fs.readFileSync(path.resolve(process.cwd(), "..", "docs", "guide.md"), "utf8");

const SID = "11111111-2222-3333-4444-555555555555";
const g = globalThis as any;

function withBrowser(protocol: string, open: ((...a: unknown[]) => unknown) | null, run: () => void): unknown[][] {
  const calls: unknown[][] = [];
  const savedLoc = g.location, savedWin = g.window;
  g.location = { protocol };
  g.window = { open: (...a: unknown[]) => { calls.push(a); return open ? open(...a) : null; } };
  try { run(); } finally { g.location = savedLoc; g.window = savedWin; }
  return calls;
}

test("web dashboard, popup allowed: ONE window.open on the kernel's /file URL, in a new tab, opener severed by hand", () => {
  let ok = false;
  const handle: { opener: unknown } = { opener: { theDashboard: true } };   // what a fresh tab holds until disowned
  const calls = withBrowser("https:", () => handle, () => { ok = openPdfTab("/tmp/paper.pdf", SID); });
  assert.equal(ok, true);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], [fileUrl("/tmp/paper.pdf", SID), "_blank"],
    "the same-origin file URL, a new tab, and NO noopener feature: it would return null even on success");
  assert.equal(calls[0][0], "/file?path=%2Ftmp%2Fpaper.pdf&sid=" + SID);
  assert.equal(handle.opener, null,
    "the tab's opener is severed on the handle (a link inside the PDF navigates the tab to a foreign site — it must not hold the dashboard)");
});

test("a federated session's PDF goes through the relay URL, still same-origin", () => {
  const calls = withBrowser("http:", () => ({}), () => { openPdfTab("out/report.pdf", "gpu1:" + SID); });
  assert.equal(calls[0][0], "/remote/gpu1/file?path=out%2Freport.pdf&sid=" + SID);
});

test("popup BLOCKED (null handle): reports false so the caller falls back to the in-app view", () => {
  let ok = true;
  const calls = withBrowser("https:", null, () => { ok = openPdfTab("/tmp/paper.pdf", SID); });
  assert.equal(ok, false);
  assert.equal(calls.length, 1, "it did try — the block is the browser's verdict, read from the handle");
});

test("a non-PDF path is not the opener's business: false, no attempt — the viewer's media branch decides by Content-Type", () => {
  let ok = true;
  const calls = withBrowser("https:", () => ({}), () => { ok = openPdfTab("/tmp/notes.md", SID); });
  assert.equal(ok, false);
  assert.equal(calls.length, 0);
});

test("not the web dashboard (a webview origin): no attempt at all, false", () => {
  let ok = true;
  const calls = withBrowser("vscode-webview:", () => ({}), () => { ok = openPdfTab("/tmp/paper.pdf", SID); });
  assert.equal(ok, false);
  assert.equal(calls.length, 0, "the webview sandbox cannot window.open — callers keep their own path");
});

test("the gesture decides: a plain click is in-app, a Cmd/Ctrl-click or a middle button is the tab", () => {
  assert.equal(wantsOwnTab(undefined), false);
  assert.equal(wantsOwnTab(null), false);
  assert.equal(wantsOwnTab({}), false, "a plain click stays inside the dashboard, like an image");
  assert.equal(wantsOwnTab({ button: 0 }), false);
  assert.equal(wantsOwnTab({ metaKey: true }), true, "Cmd-click (macOS)");
  assert.equal(wantsOwnTab({ ctrlKey: true }), true, "Ctrl-click (Windows, Linux)");
  assert.equal(wantsOwnTab({ button: 1 }), true, "the middle button, as auxclick reports it");
  assert.equal(wantsOwnTab({ shiftKey: true } as any), false, "shift alone is selection, not a new tab");
  assert.equal(wantsOwnTab({ metaKey: true, key: "Enter" } as any), true, "a keyboard gesture carries the same modifier: Cmd/Ctrl+Enter on a file-browser row");
});

test("wiring: every click on a PDF carries its gesture; modified → the tab, plain or blocked → the in-app view", () => {
  // the card's click → openPdf(path, sid, ev) → the tab on a modified click, else (or when blocked) the lightbox
  assert.match(PREVIEW, /export function openPdf\(path: string, sid\?: string \| null, ev\?: MouseEvent \| null\): void \{\n  if \(wantsOwnTab\(ev\) && openPdfTab\(path, sid\)\) return;\n  openLightbox\(path, sid\);\n\}/);
  const pf = PREVIEW.slice(PREVIEW.indexOf("export function previewFull"));
  assert.match(pf, /box\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openPdf\(path, sid, ev\); \};/);
  assert.match(pf, /box\.onmousedown = \(ev\) => \{ if \(ev\.button === 1\) ev\.preventDefault\(\); \};/,
    "the middle PRESS is cancelled: autoscroll (Firefox, Edge) starts on mousedown and would swallow the auxclick");
  assert.match(pf, /box\.onauxclick = \(ev\) => \{ if \(ev\.button !== 1\) return; ev\.stopPropagation\(\); openPdf\(path, sid, ev\); \};/,
    "a middle-click reaches the card as auxclick, never as click");
  // the opener itself: synchronous, two-argument window.open — the gesture and the handle both matter. The tab is openFileTab's
  // (any file, off the kernel's /file route; the viewer's links go through it too), and openPdfTab puts the kind check in front
  const pdf = PREVIEW.slice(PREVIEW.indexOf("export function openPdfTab"), PREVIEW.indexOf("export function openPdf("));
  assert.match(pdf, /if \(previewKind\(path\) !== "pdf"\) return false;\n\s*return openFileTab\(path, sid\);/, "the kind check is the PDF opener's own; the tab is the file opener's");
  const opener = PREVIEW.slice(PREVIEW.indexOf("export function openFileTab"), PREVIEW.indexOf("export function openPdfTab"));
  assert.match(opener, /if \(!canPreview\(\)\) return false;/, "the web-only gate");
  assert.match(opener, /const w = window\.open\(fileUrl\(path, sid\), "_blank"\);/);
  assert.doesNotMatch(opener, /"noopener/, "the noopener FEATURE makes window.open return null on success — the block signal would be lost");
  assert.match(opener, /if \(!w\) return false;[^\n]*\n\s+try \{ w\.opener = null; \}/, "…so the link is severed on the handle instead, before anything else");
  assert.doesNotMatch(opener, /await|\.then\(/, "the open happens inside the click gesture, never after a fetch");
  // the viewer: the ONE place a clicked file's gesture is read — openFileClick — and the plain open below it
  // never opens a tab (a relayed viewFile, a Reload: no gesture, the viewer). The gesture is read BEFORE the
  // host's relay (render.ts hands a plain click to the shell for the Files pane): a modified click on a PDF
  // takes its own tab whichever pane the plain click would have landed in.
  assert.match(VIEW, /export function openFileClick\(ev: MouseEvent \| KeyboardEvent \| null \| undefined, path: string, sid\?: string \| null,\n\s*relay\?: \(path: string, sid: string \| null, frag: string \| null\) => void, frag\?: string \| null\): void \{\n  if \(wantsOwnTab\(ev\) && openPdfTab\(path, sid \?\? null\)\) return;\n  if \(relay\) \{ relay\(path, sid \?\? null, frag \?\? null\); return; \}\n  openFileView\(path, sid, \{ frag: frag \?\? null \}\);[^\n]*\n\}/);
  // no click site bypasses the gesture reader: the chat and the browser never call openFileView themselves
  assert.equal((RENDER.match(/openFileView\(/g) || []).length, 0, "render.ts opens files through openFileClick only");
  assert.equal((BROWSE.match(/openFileView\(/g) || []).length, 0, "file-browse.ts opens files through openFileClick only");
  const view = VIEW.slice(VIEW.indexOf("export function openFileView("));
  const body = view.slice(0, view.indexOf("\n}\n"));
  assert.doesNotMatch(body, /openPdfTab/, "openFileView itself opens in-app, whatever the path");
  // the ONE gesture read inside openFileView is its links' listener (a Cmd/Ctrl- or middle-click on a link inside the shown file takes
  // its own tab, file-view-links.test.ts); the open of the viewer's own file reads none
  const linksAt = body.indexOf("const openLink = (x: HTMLElement, ev: MouseEvent) => {");
  assert.ok(linksAt > 0, "the links' listener");
  assert.doesNotMatch(body.slice(0, linksAt), /wantsOwnTab\(/, "no gesture read before the links' listener");
  assert.doesNotMatch(body.slice(body.indexOf('body.addEventListener("auxclick"', linksAt)), /wantsOwnTab\(/, "none after it");
  // the file browser's rows and the chat's path pills hand their gesture over, middle button included
  assert.match(BROWSE, /list\.addEventListener\("click", \(ev\) => \{[\s\S]*?onAct\(row, ev\);/);
  assert.match(BROWSE, /const fileRowOf = \(ev: MouseEvent\) => \{[\s\S]*?row\.dataset\.act === "file" \? row : null;/,
    "a middle-click acts on a FILE row only — never a folder's navigation or a download-only row's download");
  assert.match(BROWSE, /list\.addEventListener\("mousedown", \(ev\) => \{[\s\S]*?if \(ev\.button === 1 && fileRowOf\(ev\)\) ev\.preventDefault\(\);/,
    "the middle PRESS on a file row is cancelled so autoscroll cannot swallow the auxclick");
  assert.match(BROWSE, /list\.addEventListener\("auxclick", \(ev\) => \{[\s\S]*?if \(ev\.button !== 1\) return;[\s\S]*?const row = fileRowOf\(ev\);[\s\S]*?onAct\(row, ev\);/);
  assert.match(BROWSE, /if \(active\) \{ e\.preventDefault\(\); onAct\(active, e\); \}/, "Enter on a row carries its modifiers: Cmd/Ctrl+Enter on a PDF → its own tab");
  assert.match(BROWSE, /if \(row\.dataset\.act === "file"\) \{ openFileClick\(ev, p, curSid, openPick \?\? undefined\); return; \}/);   // the host's open, when it has one, sits UNDER the gesture (browse-route.test.ts)
  assert.match(RENDER, /function openPath\(path: string, sid\?: string \| null, ev\?: MouseEvent \| null, frag\?: string \| null\): void \{[\s\S]*?const to = sid \|\| activeId \|\| null;[\s\S]*?openFileClick\(ev, path, to, route === "pane" \? \(\) => \{/);   // the gesture first, whichever pane the plain click lands in (the Files pane's relay is the fourth argument)
  assert.match(RENDER, /function onMiddleClick\(a: HTMLElement, fn: \(e: MouseEvent\) => void\): void \{\n  a\.addEventListener\("mousedown", \(e\) => \{ if \(e\.button === 1\) e\.preventDefault\(\); \}\);\n  a\.addEventListener\("auxclick", \(e\) => \{ if \(e\.button !== 1\) return; e\.stopPropagation\(\); fn\(e\); \}\);/);
  assert.match(RENDER, /x\.addEventListener\("auxclick", \(e\) => e\.stopPropagation\(\)\);/, "a middle-click on the composer attachment's ✕ is inert, never the box's open");
  assert.equal((RENDER.match(/onMiddleClick\(/g) || []).length, 5, "the declaration and the four path pills: tool file, image path, path link, composer attachment");
  assert.equal((RENDER.match(/openPath\([^)]*, e(?:, a\.dataset\.frag \|\| null)?\)/g) || []).length, 8, "each pill passes its click AND its middle-click (the path link's two carry the section anchor, T351)");
});

test("the kernel serves a PDF inline WITH its name, so the tab is titled and a Save names the file", () => {
  assert.match(KERNEL, /if mime == "application\/pdf":\n\s+#[^\n]*\n(\s+#[^\n]*\n)*\s+extra\["Content-Disposition"\] = _attachment_disposition\(os\.path\.basename\(fp\), kind="inline"\)/);
  assert.match(KERNEL, /def _attachment_disposition\(name, kind="attachment"\):/);
  assert.match(KERNEL, /disp = '%s; filename="%s"' % \(kind, safe\)/);
  assert.doesNotMatch(KERNEL.slice(KERNEL.indexOf("def _file_preview"), KERNEL.indexOf("def _file_download")),
    /kind="attachment"/, "the view route never serves a PDF as an attachment — the tab must render it");
});

test("an oversize PDF's tab is not a dead end, and the listing marks such a file download-only up front", () => {
  // a navigation (Sec-Fetch-Dest: document) to a PDF over the cap gets a page whose one link is the download
  // half of the route; a fetch / iframe / HEAD keeps the plain text the viewer parses (review find 2026-09-06)
  assert.match(KERNEL, /if not head and mime == "application\/pdf" and self\._is_navigation\(\):/);
  assert.match(KERNEL, /return self\._send\(413, _too_large_page\(msg, os\.path\.basename\(fp\), q\), "text\/html; charset=utf-8",/);
  assert.match(KERNEL, /def _is_navigation\(self\):/);
  assert.match(KERNEL, /dest = \(h\.get\("Sec-Fetch-Dest"\) or ""\)\.strip\(\)\.lower\(\)/);
  assert.match(KERNEL, /return dest in \("document", "iframe"\)/, "a navigation OR the lightbox iframe gets the page");
  // Fetch Metadata is sent only to trustworthy origins: on plain http the Accept header decides
  assert.match(KERNEL, /return "text\/html" in \(h\.get\("Accept"\) or ""\)\.lower\(\)/);
  assert.match(KERNEL, /def _too_large_page\(msg, name, q, route="\/file"\):/);
  // the relay: an error verdict is prose (text/plain), and an oversize remote PDF's tab gets the page too
  assert.match(KERNEL, /route="\/remote\/%s\/file" % quote\(host, safe=""\)/);
  assert.match(KERNEL, /if status not in \(200, 206\):/);
  assert.match(KERNEL, /dq = \{"path": \(q\.get\("path"\) or \[""\]\)\[0\], "download": "1"\}/);
  // the listing's verdict follows /file's caps, so the browser routes an oversize row to the download
  assert.match(KERNEL, /row\["viewable"\] = \(bool\(_m\) and size <= _PREVIEW_MAX_BYTES\) \\\n\s+or \(not _m and _is_text_path\(e\.name\) and size <= _TEXT_MAX_BYTES\)/);
  // the relay names a remote PDF from the REQUESTED path, never the remote's header, on HEAD and GET
  const relay = KERNEL.slice(KERNEL.indexOf("def _remote_file("), KERNEL.indexOf("def _relay_download("));
  assert.equal((relay.match(/_attachment_disposition\(os\.path\.basename\(rp\), kind="inline"\)/g) || []).length, 2, "HEAD and GET");
});

test("the guide says so, in the user's terms", () => {
  const flat = GUIDE.replace(/\s+/g, " ");
  assert.match(flat, /\*\*Opening a PDF\.\*\* [^*]{0,160}opens inside the dashboard like an image/);
  assert.match(flat, /Cmd-click it instead \(Ctrl on Windows and Linux\), or middle-click, and it opens in a new browser tab in the browser's own viewer/);
  assert.match(flat, /If the browser blocks that new tab, the PDF opens inside the dashboard instead; a PDF too large to show offers a download in its place\./);
});
