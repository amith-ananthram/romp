// Every rendered code block in the chat carries an automatic "Copy" button (the user 2026-06-22). It's
// added inside highlight() — the one post-processor over `pre code` — so it covers ALL render paths
// (assistant body, agent report, postal body, diffs) for free. The RAW source is captured BEFORE the
// markup is rewritten (line-wrapping drops the \n joins, so the on-screen textContent isn't copy-safe).
// The button itself lives in code-block.ts, shared with the file viewer's fences (code-block.test.ts
// executes it); this file pins the chat's wiring and the sheet at the source level, like codeblock-wrap.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const BLOCK = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "code-block.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

const HL = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "highlight-cache.ts"), "utf8");

test("highlight() captures the raw source then adds a Copy button to each <pre>", () => {
  // raw is captured BEFORE innerHTML is rewritten, and hljs highlights that same captured string — through
  // the (language, source) cache (highlight-cache.ts), which still names a grammar for a labeled fence and
  // auto-detects an unlabeled one among the chat's ten grammars (the viewer registers more; the guess set is held)
  assert.match(RENDER, /const raw = code\.textContent \|\| "";/);
  assert.match(RENDER, /code\.innerHTML = highlightHtml\(hljs, lang, raw\);/);
  assert.match(HL, /hl\.highlight\(raw, \{ language: lang as string \}\)/);
  assert.match(HL, /hl\.highlightAuto\(raw, AUTO_LANGUAGES\)/);
  // the button is added per code block, to its parent <pre>, with the captured raw; the function is the shared module's
  assert.match(RENDER, /^import \{ wrapCodeLines, addCopyBtn \} from "\.\/code-block";/m);
  assert.match(RENDER, /if \(pre && pre\.tagName === "PRE"\) addCopyBtn\(pre as HTMLElement, raw\)/);
});

test("addCopyBtn builds a .code-copy button, is idempotent, remembers the fence's source, and shows Copied feedback on the button on screen", () => {
  assert.match(BLOCK, /export function addCopyBtn\(pre: HTMLElement, raw: string\): void/);
  assert.match(BLOCK, /if \(pre\.querySelector\(":scope > \.code-copy"\)\) return;/);  // never doubles up on a re-render
  assert.match(BLOCK, /el\("button", "code-copy"\)/);
  assert.match(BLOCK, /SOURCES\.set\(pre, raw\);/);                                    // the fence's identity for the acknowledgement after a swap
  assert.match(BLOCK, /copyText\(raw\)\.then\(\(ok\) => acknowledge\(btn, slot, ok\)\)/);
  assert.match(BLOCK, /b\.textContent = ok \? "Copied" : "Copy failed"/);
  assert.match(BLOCK, /b\.classList\.toggle\("copied", ok\)/);
  // Space acts on the keydown like Enter: a re-render between the keydown and the native keyup click lost the press
  assert.match(BLOCK, /btn\.addEventListener\("keydown", \(ev\) => \{\s*\n\s*if \(ev\.key !== " "\) return;\s*\n\s*ev\.preventDefault\(\);\s*\n\s*if \(ev\.repeat\) return;\s*\n\s*copy\(\);/);
  assert.match(BLOCK, /btn\.addEventListener\("keyup", \(ev\) => \{ if \(ev\.key === " "\) ev\.preventDefault\(\); \}\);/);
  assert.doesNotMatch(RENDER, /function addCopyBtn\(/, "render.ts keeps no copy of its own");
});

test("copyText uses the async Clipboard API with an execCommand fallback", () => {
  assert.match(BLOCK, /export function copyText\(text: string\): Promise<boolean>/);
  assert.match(BLOCK, /navigator\.clipboard\.writeText\(text\)/);
  assert.match(BLOCK, /function fallbackCopy\(text: string\): boolean/);
  assert.match(BLOCK, /document\.execCommand\("copy"\)/);
  assert.doesNotMatch(RENDER, /function (copyText|fallbackCopy)\(/, "render.ts keeps no copy of its own");
});

test("the Copy button is styled: anchored top-right, faint until hover, green when copied", () => {
  assert.match(CSS, /pre\.has-copy \{[^}]*position: relative/);                 // anchors the absolute button
  assert.match(CSS, /\.code-copy \{[^}]*position: absolute/);
  assert.match(CSS, /\.code-copy \{[^}]*opacity: 0;/);                          // hidden by default
  assert.match(CSS, /pre\.has-copy:hover \.code-copy[^{]*\{[^}]*opacity: 0\.9/); // revealed on block hover
  assert.match(CSS, /\.code-copy\.copied \{[^}]*color: var\(--green\)/);        // success = green
  assert.match(CSS, /@media \(hover: none\) \{ \.code-copy \{ opacity: 0\.8/);  // touch has no hover → stays visible
});
