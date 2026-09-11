// A pasted screenshot must render ONCE (the user 2026-07-10, who reported it appearing to render twice).
// The composer inserts the saved path into the message text, so the user turn carries the picture
// two ways: ev.images (the full in-bubble userImage render) AND a literal path in ev.md. The
// 2026-07-08 mentioned-path thumbnail feature made linkifyFileUris ALSO thumb that literal path —
// same file, small thumb + big image. Fix: the user bubble passes its ev.images paths as
// linkifyFileUris' skipThumbs, so an already-rendered image is excluded from the path-thumbs strip
// (it stays a clickable link). render.ts has import-time DOM side effects → source pins + an
// executed replica of the collection decision (feed-artifacts.test.ts precedent).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(
  path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("linkifyFileUris takes skipThumbs and excludes those paths from the thumbnail strip", () => {
  assert.match(RENDER, /function linkifyFileUris\(root: HTMLElement, skipThumbs\?: string\[\], spacePaths\?: string\[\],\s*\n\s*pathLinks\?: Record<string, string>, pathPins\?: Record<string, string>, pathPreview\?: Record<string, string>\): void/);
  // the previewable push gates on skipThumbs — the path stays a LINK, it just doesn't render a figure
  assert.match(RENDER,
    /if \(previewKind\(open\) && !previewable\.includes\(open\) && !\(skipThumbs && skipThumbs\.includes\(open\)\)\) \{\s*\n\s*previewable\.push\(open\);\s*\n\s*mentionAt\.set\(open, link\);/);
});

test("the user bubble passes its ev.images paths (caption path AND path:-src) as skipThumbs", () => {
  // both ways an in-bubble image names its file: im.path (caption) and a "path:<abs>" src
  assert.match(RENDER, /\.flatMap\(\(im\) => \[im\.path, im\.src\.startsWith\("path:"\) \? im\.src\.slice\(5\) : ""\]\)/);
  assert.match(RENDER, /linkifyFileUris\(bubble, imgPaths, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);/);
  // the assistant reply has no ev.images — its call stays bare (mentioned plots still thumb there)
  assert.match(RENDER, /linkifyFileUris\(body, undefined, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);/);
});

// executed: replicate the strip-collection decision — a path already rendered as an in-bubble
// image never enters `previewable`; a different mentioned image still does.
test("a pasted image's path is skipped by the strip while other mentioned images still thumb", () => {
  const previewKind = (p: string) => /\.(png|jpe?g|gif|webp|bmp|svg|pdf)$/i.test(p) ? "img" : null;
  const collect = (tokens: string[], skipThumbs?: string[]) => {
    const previewable: string[] = [];
    for (const open of tokens) {
      if (previewKind(open) && !previewable.includes(open) && !(skipThumbs && skipThumbs.includes(open))) previewable.push(open);
    }
    return previewable;
  };
  const pasted = "/tmp/shots/shot-20260710-000000.png";   // in ev.images AND in the typed text
  const other = "/tmp/plots/temperature.png";              // merely mentioned → should thumb
  // the exact bug: the pasted path was collected → thumb + full image = rendered twice
  assert.deepEqual(collect([pasted, other]), [pasted, other], "pre-fix behavior double-renders");
  // fixed: skipThumbs (built from ev.images) excludes it; the other mention still thumbs
  assert.deepEqual(collect([pasted, other], [pasted]), [other]);
  // a repeat of the pasted path in the text still never thumbs
  assert.deepEqual(collect([pasted, pasted], [pasted]), []);
  // no images on the turn → strip behavior unchanged
  assert.deepEqual(collect([other], []), [other]);
});

test("the image CAPTION (🖼 path ⧉) is skipped when the path is already in the message text (the user 2026-07-15)", () => {
  // userImage takes pathInText and only adds the caption when the path ISN'T already a link in the bubble;
  // the bubble computes it from ev.md so a dropped screenshot (path inserted into the text) shows the path once.
  assert.match(RENDER, /function userImage\(im: \{ src: string; path\?: string \}, pathInText = false\): HTMLElement/);
  assert.match(RENDER, /if \(im\.path && !pathInText\) fig\.appendChild\(imgCaption\(im\.path\)\);/);
  assert.match(RENDER, /userImage\(im, !!\(im\.path && mdText\.includes\(im\.path\)\)\)/);

  // executed replica of the caption decision: caption shown iff there's a path AND it's not in the text
  const captionShown = (path: string | undefined, md: string) => !!path && !md.includes(path);
  const p = "/Users/x/Screenshots/shot.png";
  assert.equal(captionShown(p, "here: " + p + " what's going on?"), false, "path in text → caption dropped (no repeat)");
  assert.equal(captionShown(p, "no path in this message"), true, "path NOT in text (inline paste) → caption kept");
  assert.equal(captionShown(undefined, "text"), false, "no known path → no caption");
});
