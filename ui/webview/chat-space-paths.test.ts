// Backticked filenames WITH SPACES become whole-span links (the user 2026-08-04): the token linkifier
// can never span a space — in prose that boundary is what keeps ordinary text unlinked — so a note
// named like `My spaced note.md` linkified only its last word. The KERNEL verifies which
// space-containing inline-code spans are real files (build_session's _space_paths: resolved like a
// click via _resolve_open_path, existence-checked, cached per message uuid) and ships them on the chat
// event as `spacePaths`; the client whole-links exactly those spans. The filesystem is the authority —
// a backticked command whose words end in a filename (`uv run pytest tests/x.py`) resolves to no file
// and is never mislinked. render.ts has no jsdom harness → source pins (the repo convention).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the chat event carries kernel-verified spacePaths on user and assistant turns", () => {
  assert.match(RENDER, /kind: "user";[^\n]*spacePaths\?: string\[\]/);
  assert.match(RENDER, /kind: "assistant";[^\n]*spacePaths\?: string\[\]/);
  // both event constructions attach it, gated on non-empty (the common message adds no payload)
  assert.ok(KERNEL.includes('sp = _space_paths(prompt, sid, a.get("uuid"))'), "user events verify their spans");
  assert.ok(KERNEL.includes('sp = _space_paths(txt, sid, a.get("uuid"))'), "assistant events verify their spans");
  assert.ok(KERNEL.includes('ev["spacePaths"] = sp'), "verified spans ride the event");
});

test("the kernel verifies with the filesystem, resolved exactly like a click", () => {
  assert.ok(KERNEL.includes("def _space_paths(md, sid, uuid):"));
  // existence via the SAME resolution the click-to-open uses — cwd-relative, ~ expanded
  assert.ok(KERNEL.includes("os.path.isfile(_resolve_open_path(tok, sid))"),
    "the filesystem is the authority, not a lexical guess");
  // checked once per message (build_session runs per push) — keyed by the message uuid
  assert.ok(KERNEL.includes("_SPACE_PATH_CACHE"), "existence is checked once per message, not per push");
  // only space-containing spans: space-free ones are the client token linkifier's job
  assert.ok(KERNEL.includes('if " " not in tok:'));
});

test("linkifyFileUris whole-links a verified span's entire inline-code content", () => {
  assert.match(RENDER, /function linkifyFileUris\(root: HTMLElement, skipThumbs\?: string\[\], spacePaths\?: string\[\],\s*\n\s*pathLinks\?: Record<string, string>, pathPins\?: Record<string, string>, pathPreview\?: Record<string, string>\): void/);
  // the pass targets inline <code> only, skips anything already linked or fenced
  assert.match(RENDER, /for \(const code of Array\.from\(root\.querySelectorAll\("code"\)\)\) \{\s*\n\s*if \(code\.closest\("a, \.file-uri-link, pre"\)\) continue;/);
  // exact-match against the kernel's verified set, then the whole content becomes one open link
  assert.match(RENDER, /const tok = \(code\.textContent \|\| ""\)\.trim\(\);\s*\n\s*if \(!verified\.has\(tok\)\) continue;/);
  assert.match(RENDER, /code\.replaceChildren\(link\);/);
  // a verified image/PDF joins the same full-size previews the token links feed (figure at its mention)
  assert.match(RENDER, /if \(previewKind\(tok\) && !previewable\.includes\(tok\) && !\(skipThumbs && skipThumbs\.includes\(tok\)\)\) \{\s*\n\s*previewable\.push\(tok\);\s*\n\s*mentionAt\.set\(tok, code\);/);
});

test("the whole-span pass runs BEFORE the token walk, so the new link is skipped by it", () => {
  const fn = RENDER.slice(RENDER.indexOf("function linkifyFileUris("), RENDER.indexOf("function renderEvent("));
  const spanPass = fn.indexOf('root.querySelectorAll("code")');
  const walk = fn.indexOf("linkifyPathTokens(");   // the shared walk (path-links.ts), which skips text already inside a link
  assert.ok(spanPass >= 0 && walk >= 0 && spanPass < walk,
    "code-span links land first; the token walk's closest('a, .file-uri-link') guard then leaves them alone");
});

test("every message render threads its event's spacePaths through", () => {
  assert.match(RENDER, /linkifyFileUris\(bubble, imgPaths, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);/);   // user bubble
  assert.match(RENDER, /linkifyFileUris\(full, imgPaths, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);/);     // expanded nudge body
  assert.match(RENDER, /linkifyFileUris\(body, undefined, ev\.spacePaths, ev\.pathLinks, ev\.pathPins, ev\.pathPreview\);/);    // assistant reply
});
