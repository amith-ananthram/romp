// Click-to-open BARE file paths in the chat (the user 2026-07-06, who wanted to click a bare path like design/judge-simplification-plan.md
// and open it). The linkifier already handled file:// URIs; now it also linkifies absolute/anchored paths and
// relative paths that carry a file extension — while leaving prose like "and/or", "TCP/IP", "24/7" alone. A
// relative link posts the ACTIVE session id so the kernel resolves it against that session's cwd (the repo the
// agent runs in), not the kernel's launch cwd. Slash-less BARE filenames (`power2_watts.pdf`) link too, but
// ONLY inside inline <code> and only with a KNOWN extension (the user 2026-07-17) — so backticked dotted
// identifiers (`np.array`) stay prose. The matcher lives in path-links.ts (lifted out of render.ts, which binds
// the click per span); no jsdom harness → source pins + executed replicas (the walk runs in path-links.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const LINKS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "path-links.ts"), "utf8");   // the matcher, lifted out of render.ts

test("the linkifier matches file:// URIs AND bare paths, and gates each token kind", () => {
  // one finder covers the file: scheme, the slashed-path alternative, and the bare-filename alternative
  assert.ok(LINKS.includes("const CLICKABLE_PATH_RE = /file:"), "regex still handles file:// URIs");
  assert.ok(LINKS.includes("[~.\\w\\-]"), "regex has the slashed-path alternative");
  assert.match(LINKS, /if \(!isUri && !looksLikeFilePath\(tok\) && !\(span\.inCode && looksLikeBareFileName\(tok\)\)\) continue;/);
  // the kernel's pathLinks verdict then narrows further, and its value is the OPEN target — pinned
  // in chat-path-links.test.ts; here we pin that the link opens `open`, whatever chose it
  assert.match(LINKS, /const link = openPathLink\(tok, open, !isUri\);/);
  assert.match(LINKS, /list\.push\(\{ start, end: last, el: link \}\);/);
});

test("a relative path click carries the active session id so whoever resolves it uses that cwd", () => {
  assert.match(LINKS, /function openPathLink\(raw: string, open: string, relative = false\)/);
  assert.match(LINKS, /if \(relative\) a\.dataset\.rel = "1";/, "the span says it was a bare path (the module binds nothing)");
  // relative → send the session id; absolute/file:// → none needed. Both go through openPath, which
  // picks the host (VS Code editor vs the feed pane's viewer) — see the openPath test below. render.ts
  // reads the span's data and binds the click (bindPathLink).
  assert.match(RENDER, /const open = a\.dataset\.path \|\| "", relative = a\.dataset\.rel === "1";/);
  assert.match(RENDER, /openPath\(open, relative \? activeId : null, e, a\.dataset\.frag \|\| null\);/);   // + the click itself: a modified click on a PDF takes a browser tab (pdf-new-tab.test.ts)
});

test("the cheap pre-filter keys on a slash — or, inside inline code, a dot", () => {
  assert.match(LINKS, /if \(!text\.includes\("\/"\) && !\(anyCode && text\.includes\("\."\)\)\) continue;/);
  assert.match(LINKS, /inCode: !!p\?\.closest\("code"\)/);   // read per text node as the units are cut (textUnits)
});

// executed: mirror looksLikeFilePath EXACTLY to guard its precision (accept real paths, reject prose)
test("looksLikeFilePath accepts real paths and rejects prose fractions/idioms", () => {
  const looksLikeFilePath = (tok: string): boolean => {
    if (tok.includes(":") || tok.includes("//") || !tok.includes("/")) return false;
    if (/^(?:~\/|\.{1,2}\/|\/)/.test(tok)) return true;
    return /\.[A-Za-z0-9]{1,8}$/.test(tok.slice(tok.lastIndexOf("/") + 1));
  };
  // accept — the user's exact case + common repo paths
  for (const p of ["design/judge-simplification-plan.md", "ui/webview/render.ts",
                   "/Users/x/a.md", "~/notes.md", "./foo.txt", "../a/b.py"]) {
    assert.equal(looksLikeFilePath(p), true, p);
  }
  // reject — prose that merely contains a slash, and un-autolinked URLs
  for (const p of ["and/or", "TCP/IP", "24/7", "read/write", "he/she",
                   "https://example.com/page.html", "bin/romp-kernel"]) {
    assert.equal(looksLikeFilePath(p), false, p);
  }
});

// executed: mirror looksLikeBareFileName — a slash-less filename links only with a KNOWN extension
test("looksLikeBareFileName accepts real filenames and rejects identifiers/versions", () => {
  assert.match(LINKS, /function looksLikeBareFileName\(tok: string\): boolean/);
  assert.match(LINKS, /BARE_FILE_EXTS\.has\(tok\.slice\(dot \+ 1\)\.toLowerCase\(\)\)/);
  const EXTS = new Set(["md", "py", "pdf", "csv", "png", "ts", "json", "sh"]);   // subset of BARE_FILE_EXTS
  const bare = (tok: string): boolean => {
    if (tok.includes("/") || tok.includes(":")) return false;
    const dot = tok.lastIndexOf(".");
    if (dot <= 0) return false;
    return EXTS.has(tok.slice(dot + 1).toLowerCase());
  };
  // accept — the user's exact screenshot cases
  for (const p of ["power2_watts.pdf", "power2_table.csv", "kernel.py", "notes.md", "data.json"]) {
    assert.equal(bare(p), true, p);
  }
  // reject — dotted identifiers, settings keys, versions, dotfiles, slashed paths (other rule's job)
  for (const p of ["np.array", "romp.kernelPort", "0.4.293", "e.g", ".gitignore", "analysis/foo.py", "a:b.md"]) {
    assert.equal(bare(p), false, p);
  }
  // the real BARE_FILE_EXTS covers the screenshot's extensions
  const exts = LINKS.slice(LINKS.indexOf("const BARE_FILE_EXTS"), LINKS.indexOf("function looksLikeBareFileName"));
  for (const e of ['"pdf"', '"csv"', '"py"', '"md"', '"png"']) assert.ok(exts.includes(e), e);
});

// executed: the finder regex actually pulls tokens out of a sentence
test("CLICKABLE_PATH_RE finds slashed paths in prose and bare filenames", () => {
  const re = /file:\/\/\/?[^\s<>"'`)]+|[~.\w\-]*\/[~.\w\-/]*[\w\-]|[\w\-][\w\-.]*\.[A-Za-z0-9]{1,8}/gi;
  assert.deepEqual("see design/judge-simplification-plan.md for details".match(re),
    ["design/judge-simplification-plan.md"]);
  // a bare filename (as the sole content of an inline-code node) is matched — the inCode gate decides
  assert.deepEqual("power2_watts.pdf".match(re), ["power2_watts.pdf"]);
});
