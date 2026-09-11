// The file preview popover's DOM half (T351 stage 1), pinned in render.ts and styles.css (no jsdom harness for the
// renderers; the behaviour is measured on the served page by tests/test_file_preview_browser.py, the pure half is
// executed in file-preview.test.ts). What is pinned: every path link is armed for the hover and the keyboard's focus;
// the kernel's verdict rides the link as data-preview and a link without it gets the text-only card with NO request;
// the loader shows first; the card wears the comment popover's size and surface; it closes on Escape, on a scroll, on
// a click elsewhere and at every strip rebuild; "open" carries the section anchor to the viewer through both routes.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const ui = (...p: string[]) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", ...p), "utf8");
const RENDER = ui("webview", "render.ts");
const CSS = ui("webview", "styles.css");
const FILEVIEW = ui("webview", "file-view.ts");
const FILES = ui("webview", "files.ts");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("every path link is armed: a hover or a focus starts the dwell, leaving or blurring starts the grace", () => {
  const bind = RENDER.slice(RENDER.indexOf("function bindPathLink("), RENDER.indexOf("// ── the file PREVIEW popover"));
  assert.match(bind, /filePreviewIntent\.cancel\(\);\s*\n\s*openPath\(open, relative \? activeId : null, e, a\.dataset\.frag \|\| null\);/, "a click closes the card before it opens the file, at the section the link names");
  assert.match(bind, /armFilePreview\(a\);/);
  // `path#slug` in prose: the slug after a path token moves into the link as data-frag (the viewer's own convention)
  const absorb = RENDER.slice(RENDER.indexOf("function absorbFragment("), RENDER.indexOf("// ── the file PREVIEW popover"));
  assert.match(absorb, /const m = \/\^#\(\[a-z0-9\]\[a-z0-9-\]\*\)\/\.exec\(nx\.textContent \|\| ""\);/);
  assert.match(absorb, /link\.dataset\.frag = m\[1\];\s*\n\s*link\.appendChild\(document\.createTextNode\(m\[0\]\)\);/);
  assert.match(RENDER, /armPreview\(link, link\.textContent \|\| "", open\);\s*\n\s*absorbFragment\(link\);/, "after the kernel's verdict is read off the bare token");
  assert.match(RENDER, /const path = parsed\.path, anchor = a\.dataset\.frag \|\| parsed\.anchor;/, "the card previews the absorbed section");
  const arm = RENDER.slice(RENDER.indexOf("function armFilePreview("), RENDER.indexOf("function placeFilePreview("));
  assert.match(arm, /a\.addEventListener\("pointerenter", \(\) => filePreviewIntent\.enter\(a\)\);/);
  assert.match(arm, /a\.addEventListener\("pointerleave", \(\) => filePreviewIntent\.leave\(\)\);/);
  assert.match(arm, /a\.addEventListener\("focus", \(\) => filePreviewIntent\.enter\(a\)\);/, "the keyboard's route");
  assert.match(arm, /a\.addEventListener\("blur", \(\) => filePreviewIntent\.leave\(\)\);/);
  assert.match(RENDER, /const filePreviewIntent = new HoverIntent<HTMLElement>\(PREVIEW_DWELL_MS, PREVIEW_GRACE_MS, \(a\) => showFilePreview\(a\), \(\) => hideFilePreview\(\)\);/, "the one timing rule, the pure module's");
});

test("the kernel's verdict rides the link as data-preview; a link without it gets the text-only card and no request", () => {
  const lf = RENDER.slice(RENDER.indexOf("function linkifyFileUris("), RENDER.indexOf("function openPath(") > 0 ? RENDER.length : RENDER.length);
  assert.match(lf, /pathLinks\?: Record<string, string>, pathPins\?: Record<string, string>, pathPreview\?: Record<string, string>\): void \{/, "the pass takes the map");
  assert.match(lf, /const k = previewKindOf\(tok, pathPreview\) \|\| previewKindOf\(open, pathPreview\);\s*\n\s*if \(k\) link\.dataset\.preview = k; else delete link\.dataset\.preview;/);
  assert.match(lf, /armPreview\(link, tok, tok\);/, "the kernel-verified spaced span");
  assert.match(lf, /bindPathLink\(link\);\s*\n\s*armPreview\(link, link\.textContent \|\| "", open\);/, "every hit of the token walk");
  for (const call of ["linkifyFileUris(full, imgPaths, ev.spacePaths, ev.pathLinks, ev.pathPins, ev.pathPreview);",
                      "linkifyFileUris(bubble, imgPaths, ev.spacePaths, ev.pathLinks, ev.pathPins, ev.pathPreview);",
                      "linkifyFileUris(body, undefined, ev.spacePaths, ev.pathLinks, ev.pathPins, ev.pathPreview);"]) {
    assert.ok(RENDER.includes(call), "the map is threaded: " + call);
  }
  assert.match(RENDER, /pathPins\?: Record<string, string>; pathPreview\?: Record<string, string> \}/, "the event carries pathPreview beside pathLinks and pathPins");
  const show = RENDER.slice(RENDER.indexOf("function showFilePreview("), RENDER.indexOf("function linkifyFileUris("));
  assert.match(show, /const kind = a\.dataset\.preview \|\| null;/);
  assert.match(show, /if \(!kind\) \{[^}]*renderFilePreview\(p, textOnlyContent\(path, anchor, "shown as text: outside the session's folder and your home, or not a kind the preview shows"\), sid\);\s*\n\s*stamp\(null\);\s*\n\s*return;\s*\n\s*\}/, "no fetch for a link the kernel did not allow");
  assert.match(show, /if \(kind === "image" \|\| kind === "pdf"\) \{ renderFilePreview\(p, contentFor\(path, anchor, kind, sid, null\), sid\); stamp\(null\); return; \}/, "media needs no slice: the bytes route");
  assert.match(show, /p\.replaceChildren\(rompLoaderInner\("reading…", \{ wordmark: false \}\)\);/, "the loader first");
  assert.match(show, /fetch\(sliceUrl\(path, sid, anchor\), \{ credentials: "same-origin" \}\)/);
  assert.match(show, /if \(seq !== filePreviewSeq\) return;/, "a stale answer never fills a card that moved on");
  // the kernel's half: the preview map shipped beside pathLinks on both message paths, warming markdown
  assert.equal((KERNEL.match(/ev\["pathPreview"\] = pv/g) || []).length, 2, "both the assistant and the user message build ship it");
  assert.match(KERNEL, /def _path_previews\(links, sid\):/);
  assert.match(RENDER, /p\.dataset\.renderMs = \(performance\.now\(\) - t0\)\.toFixed\(1\)/, "the card stamps dwell end to rendered content (the acceptance is latency)");
  assert.match(RENDER, /p\.dataset\.sliceHit = hit \? "1" : "0"/, "…and whether the slice was cached");
  assert.match(KERNEL, /hit=hit\)/, "the slice answer says whether it came from the cache");
  assert.match(KERNEL, /if kind and \(kind != "markdown" or _slice_warm\(fp\)\):/, "the pusher's path warms the markdown it links, and a markdown the warm refuses (the content belt) ships without a kind");
});

test("the card: the comment popover's size and surface, transient; closes on Escape, a scroll, a click elsewhere, a strip rebuild", () => {
  const place = RENDER.slice(RENDER.indexOf("function placeFilePreview("), RENDER.indexOf("function renderFilePreview("));
  assert.match(place, /Math\.max\(CMT_POP_MIN_W, Math\.min\(pane\.width \* CMT_POP_THREAD_DEFAULT\.w, innerWidth \* CMT_POP_CAP_W\)\)/, "the comment popover's width rule");
  assert.match(place, /Math\.max\(CMT_POP_MIN_H, Math\.min\(pane\.height \* CMT_POP_THREAD_DEFAULT\.h, innerHeight \* CMT_POP_CAP_H\)\)/, "…and height");
  assert.match(RENDER, /document\.addEventListener\("keydown", \(e\) => \{ if \(e\.key === "Escape"\) filePreviewIntent\.cancel\(\); \}\);/);
  assert.match(RENDER, /document\.getElementById\("content"\)\?\.addEventListener\("scroll", \(\) => filePreviewIntent\.cancel\(\), \{ passive: true \}\);/);
  assert.match(RENDER, /if \(filePreviewEl && filePreviewEl\.style\.display !== "none" && !filePreviewEl\.contains\(e\.target as Node\)\) filePreviewIntent\.cancel\(\);/, "a click elsewhere");
  // the closer is the anchored link's own removal, not the strip's rebuild (which runs on every push): a re-render that
  // drops the node fires no pointerleave, so an observer on the thread cancels the intent when the link leaves
  assert.doesNotMatch(RENDER, /syncComposerPh\(\);[^\n]*\n\s*hideFilePreview\(\);/, "no closer on the strip's rebuild");
  assert.match(RENDER, /filePreviewAnchorWatch = new MutationObserver\(\(\) => \{ if \(!a\.isConnected\) filePreviewIntent\.cancel\(\); \}\);\s*\n\s*filePreviewAnchorWatch\.observe\(root, \{ childList: true, subtree: true \}\);/);
  assert.match(RENDER, /p\.style\.display = "";\s*\n\s*watchFilePreviewAnchor\(a\);/, "armed when the card shows");
  assert.match(RENDER, /if \(filePreviewAnchorWatch\) \{ filePreviewAnchorWatch\.disconnect\(\); filePreviewAnchorWatch = null; \}/, "…and released when it hides");
  // a previewed document's remote images never load on a hover: they become their alt text (a hover is not a choice to fetch)
  assert.match(RENDER, /function previewMdClean\(src: string\): HTMLElement \{[\s\S]*?clean = sanitizeMd\(marked\.parse\(src\) as string\);[\s\S]*?stripRemoteLoads\(clean, location\.origin, location\.href\);\s*\n\s*return clean;/,
               "a previewed document is rendered on the sanitizer's inert DOM and stripped of remote loads THERE, before any node joins the page (the review: a strip after innerHTML raced the fetch)");
  assert.match(RENDER, /body\.replaceChildren\(\.\.\.Array\.from\(previewMdClean\(c\.body\.markdown\)\.childNodes\)\)/, "the card adopts the stripped nodes");
  const renderFn = RENDER.slice(RENDER.indexOf("function renderFilePreview("), RENDER.indexOf("function showFilePreview("));
  assert.doesNotMatch(renderFn, /innerHTML = md\(/, "never md() into a live innerHTML: the fetch would start before any strip");
  assert.match(RENDER, /const clean = sanitizeMd\(c\.body\.html\); stripRemoteLoads\(clean, location\.origin, location\.href\); body\.replaceChildren\(clean\);/, "a provider's HTML body, the same way");
  assert.doesNotMatch(RENDER, /function stripRemoteImages/, "the src-only strip is gone");
  assert.doesNotMatch(fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "file-preview.ts"), "utf8"), /headings\?:/, "the dead heading index is gone from the answer type (the review)");
  assert.match(RENDER, /p\.addEventListener\("pointerenter", \(\) => filePreviewIntent\.pin\(\)\);/, "inside the card it stays");
  assert.match(CSS, /^\.file-preview-pop \{\s*\n\s*position: fixed; z-index: 120; display: flex; flex-direction: column; gap: 6px; padding: 8px; overflow: hidden;\s*\n\s*background: var\(--vscode-menu-background, #252526\); color: var\(--vscode-menu-foreground, var\(--fg\)\);\s*\n\s*border: 1px solid var\(--box-border\); border-radius: 6px; box-shadow: 0 4px 12px rgba\(0, 0, 0, 0\.35\);/m, "the comment popover's card");
  assert.doesNotMatch(CSS, /\.file-preview-pop \{[^}]*resize:/, "transient: no resize handle");
  assert.doesNotMatch(CSS, /\.file-preview-pop \{[^}]*cursor: grab/, "…and no drag");
  assert.match(CSS, /\.file-preview-pop \.fp-img \{ max-width: 100%; max-height: 100%; object-fit: contain; \}/, "an image at its natural size, capped to the card");
  assert.match(CSS, /^\.md-callout-label \{/m, "the callout label the grammar emits has its dress");
});

test("open carries the section anchor to the viewer through both routes, and the viewer lands on it", () => {
  assert.match(RENDER, /function openPath\(path: string, sid\?: string \| null, ev\?: MouseEvent \| null, frag\?: string \| null\): void \{/);
  assert.match(RENDER, /window\.parent\.postMessage\(\{ romp: "viewFile", path, sid: to, pane: "pane", frag: frag \|\| null,/, "the pane route names the section");
  assert.match(RENDER, /\} : undefined, frag \|\| null\);/, "…and so does the overlay route");
  assert.match(RENDER, /openPath\(path, sid, e, frag \|\| null\); \}\);/, "the card's open button hands the anchor over");
  assert.match(FILEVIEW, /relay\?: \(path: string, sid: string \| null, frag: string \| null\) => void, frag\?: string \| null\): void \{/);
  assert.match(FILEVIEW, /openFileView\(path, sid, \{ frag: frag \?\? null \}\);/);
  assert.match(FILES, /function openHere\(path: string, sid: string \| null, identity: FileViewIdentity \| null, frag: string \| null = null\): void \{/);
  assert.match(FILES, /openFileView\(path, sid, \{ frag \}\)/);
  assert.match(FILES, /typeof m\.frag === "string" \? m\.frag : null\);/);
  assert.match(KERNEL, /postMessage\(\{romp:'viewFile',path:m\.path,sid:m\.sid,identity:m\.identity\|\|null,frag:m\.frag\|\|null\},'\*'\)/, "the shell's relay forwards it");
});
