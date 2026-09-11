// The comment mark's UNREAD cue (the user 2026-09-10): a landed reply you have not opened draws ONE solid outline
// in the scroll notch's yellow around the WHOLE highlighted passage — in place of the 2026-09-08 dashed ring, which
// was an outline on the inline mark and so painted once per line fragment (a wrapped passage wore a stack of dashed
// boxes). The fill keeps saying pending vs landed exactly as before (T237: the green .busy wash = a reply still
// being written, the 45% yellow .unread tier = landed); the box is the only cue for unread, and only on unread.
// Source pins (no jsdom harness for the renderers); the union geometry itself is measured on the served page by
// tests/test_comment_outline_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the per-fragment dashed ring is gone: no outline on the mark itself, in any state", () => {
  assert.doesNotMatch(CSS, /mark\.cmt-hl\.unread \{ outline/);
  assert.doesNotMatch(CSS, /dashed var\(--st-awaiting-bg\); outline-offset: 1px/);
  const marks = CSS.match(/^mark\.cmt-hl[^{]*\{[^}]*\}/gms) || [];
  assert.ok(marks.length >= 6, "the mark rules are found");
  for (const rule of marks) assert.doesNotMatch(rule, /outline/, "an outline on an inline mark paints per line fragment: " + rule);
});

test("read marks wear no box, and the fill tiers are byte-untouched — the colour still says pending vs landed", () => {
  assert.match(CSS, /mark\.cmt-hl\.unread \{ background: color-mix\(in srgb, var\(--cmt-hl\) 45%, transparent\); \}/, "landed = the 45% yellow tier (T237's pin)");
  assert.match(CSS, /mark\.cmt-hl\.busy \{\s*\n\s*background-color: color-mix\(in srgb, var\(--st-awaitbg-bg\) 24%, transparent\);/, "pending = the green wash");
  assert.match(CSS, /mark\.cmt-hl\.resolved \{ background: rgba\(255, 255, 255, 0\.08\); \}/);
  assert.match(CSS, /mark\.cmt-hl:hover \{ background: color-mix\(in srgb, var\(--cmt-hl\) 58%, transparent\); \}/);
  // the painter draws a box for UNREAD marks only: a read mark has no box to wear
  assert.match(RENDER, /v\.el\.querySelectorAll\("mark\.cmt-hl\.unread"\)/);
});

test("the yellow corner dot stays gone root and branch", () => {
  assert.doesNotMatch(CSS, /mark\.cmt-hl\.unread\.hl-last::after/);
  assert.doesNotMatch(CSS, /mark\.cmt-hl \{[^}]*position: relative;/s, "nothing left for the mark to anchor");
  // the segment classes stay: the radius still sits only on the run's outer ends
  assert.match(CSS, /mark\.cmt-hl\.hl-first \{ border-top-left-radius: 2px; border-bottom-left-radius: 2px; \}/);
  assert.match(CSS, /mark\.cmt-hl\.hl-last \{ border-top-right-radius: 2px; border-bottom-right-radius: 2px; \}/);
  assert.match(RENDER, /segs\[i\]\.classList\.toggle\("hl-last", i === segs\.length - 1\);/);
});

test("unread is the kernel's bit on an open thread, cleared by opening the thread — the box follows it, nothing else", () => {
  assert.match(RENDER, /m\.classList\.toggle\("unread", !!th\.unread && th\.status === "open"\);/);
  assert.match(RENDER, /if \(th\) th\.unread = false;\s*\/\/ optimistic; the kernel's watermark reconciles/);
  assert.match(RENDER, /vscodeApi\?\.postMessage\(\{ type: "commentSeen", id: sid, tid \}\);/);
  // the reply chips' click never touches it (reply-ready.test.ts pins the handler); the state has ONE clearer
  const at = RENDER.indexOf("replyjump: (elx) => {");
  assert.ok(at > 0, "the chip's click handler exists");
  const click = RENDER.slice(at, RENDER.indexOf("new ResizeObserver(updateReplyChips)", at));
  assert.doesNotMatch(click, /"commentSeen"|\.unread = false/);
});

// T349 (the user 2026-09-11): a comment on a line inside a rendered TABLE broke the table. The mark pass wraps every text
// node of the matched range, and marked's table HTML carries newline text nodes between the cells and rows, directly
// under <tr>, <tbody>, <thead> and <table>; an inline <mark> there gets its own anonymous cell, so the columns shifted.
// The pass now skips those nodes (comments.ts markSkipsParent, executed in comments.test.ts) and wraps each cell's own
// text, so the mark rides the row cell by cell and the table's boxes stay; the served lab measures the table before and
// after the marks land (tests/test_comment_table_mark_browser.py).
test("the mark pass skips a table's structural whitespace: no mark ever sits directly in a row or a table (T349)", () => {
  const fn = RENDER.slice(RENDER.indexOf("function ensureCommentMark("), RENDER.indexOf("function styleCommentMark("));
  assert.match(fn, /for \(const sl of sliceRanges\(nodes\.map\(\(t\) => t\.data\.length\), r\.start, r\.end\)\) \{\s*\n\s*const t = nodes\[sl\.idx\];\s*\n(\s*\/\/[^\n]*\n)*\s*if \(markSkipsParent\(t\.parentElement\?\.tagName\)\) continue;/, "asked of each slice's parent before any split or wrap");
  assert.match(RENDER, /^import \{[^}]*\bmarkSkipsParent\b[^}]*\} from "\.\/comments";/m, "the one pure predicate, shared with its executed test");
  assert.match(fn, /const mid = sl\.s > 0 \? t\.splitText\(sl\.s\) : t;/, "…and the wrap itself is unchanged for every other node");
});
