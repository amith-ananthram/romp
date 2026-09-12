// Chat file links are filesystem-VERIFIED, and shortened mentions are FIXED.
// The linkifier matched path-shaped tokens by SHAPE alone, so a bare `render.js` became a blue link
// that 404'd on click — it resolved against the session's cwd, where no such file lives. The KERNEL
// now resolves every shape-matched token at message-build time (build_session's _path_links: tier 1
// exact stat, tiers 2/3 a UNIQUE match in `git ls-files -co --exclude-standard` run in the session's
// cwd) and ships {token: open target} as `pathLinks` on user + assistant events. The client keeps
// every shape gate it had and merely requires map membership; the map's value is what a click opens,
// so a unique `sub/deep.py` mention opens the real kernel/sub/deep.py and hover shows that target.
// Zero or several candidates → absent from the map → prose (a silently-wrong link is worse than no
// link). The matcher lives in path-links.ts (lifted out of render.ts, which binds the click per span); neither
// has a jsdom harness → source pins + an executed tokenizer parity check over the shared fixture (the kernel
// side runs the same fixture in tests/test_path_links.py; the walk itself runs in path-links.test.ts).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const LINKS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "path-links.ts"), "utf8");   // the matcher, lifted out of render.ts
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const VIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "file-view.ts"), "utf8");

test("the chat event carries the kernel's pathLinks verdict on user and assistant turns", () => {
  assert.match(RENDER, /kind: "user";[^\n]*pathLinks\?: Record<string, string>/);
  assert.match(RENDER, /kind: "assistant";[^\n]*pathLinks\?: Record<string, string>/);
  assert.ok(KERNEL.includes('pl = _path_links(prompt, sid, a.get("uuid"), _pl_memo)'), "user events resolve their tokens");
  assert.ok(KERNEL.includes('pl = _path_links(txt, sid, a.get("uuid"), _pl_memo)'), "assistant events resolve their tokens");
  assert.ok(KERNEL.includes('ev["pathLinks"] = pl'), "the map rides the event");
  // an EMPTY map still ships — its presence (not its size) is what tells the client a verdict exists
  assert.ok(KERNEL.includes("if pl is not None:"), "None (no candidate tokens) ships nothing; {} ships");
});

test("membership in pathLinks gates the link, and the map's value is the OPEN target", () => {
  // every existing shape gate stays — the map only ever narrows, never widens
  assert.match(LINKS, /if \(!isUri && !looksLikeFilePath\(tok\) && !\(span\.inCode && looksLikeBareFileName\(tok\)\)\) continue;/);
  assert.match(LINKS, /const fixed = !isUri && pathLinks \? pathLinks\[tok\] : undefined;/);
  assert.match(LINKS, /if \(!isUri && pathLinks && typeof fixed !== "string"\) continue;/);
  // the fixed target is what opens (and openPathLink titles it, so hover shows where a fix points);
  // with NO pathLinks key on the event (old kernel, cached payload) the token opens as written
  assert.match(LINKS, /const target = isUri \? fileUriToPath\(tok\) : \(fixed \?\? tok\);\n\s*const open = opts && opts\.resolve \? opts\.resolve\(target\) : target;/, "the chat passes no resolve: a URI\'s own path, the fixed target or the token, as before");
  assert.match(LINKS, /const link = openPathLink\(tok, open, !isUri\);/, "a URI is not a relative path; everything else is");
  assert.match(LINKS, /list\.push\(\{ start, end: last, el: link \}\);/);
  assert.match(LINKS, /a\.setAttribute\("title", "Open " \+ open\);/);
  // …and the chat binds the click per span, off the span's own data (the walk marks; render.ts acts)
  assert.match(RENDER, /const open = a\.dataset\.path \|\| "", relative = a\.dataset\.rel === "1";/);
  assert.match(RENDER, /for \(const \{ el: link, open, verified, inPre \} of linkifyPathTokens\(root, pathLinks, FENCE_WALK\)\) \{\n\s*bindPathLink\(link\);/,
    "the chat's walk, with its fenced-block options (2026-09-12; file-uri-link.test.ts pins them)");
});

test("file:// URIs are explicit absolute paths — never gated on the map", () => {
  // both guards above test !isUri first, so a file:// token can't be dropped by the map…
  assert.match(LINKS, /const isUri = isFileUri\(tok\);/);   // a LOCAL file:// URI (an empty authority or localhost); file://host/x is prose
  // …and the kernel never puts file:// tokens in it
  assert.ok(KERNEL.includes('if not t.lower().startswith("file://")'), "kernel skips file:// tokens");
});

test("the kernel resolves in three tiers over the session repo's real file list", () => {
  assert.ok(KERNEL.includes("def _path_links(md, sid, uuid, memo):"));
  assert.ok(KERNEL.includes("def _resolve_path_token(tok, sid, memo):"));
  assert.ok(KERNEL.includes('["git", "ls-files", "-co", "--exclude-standard"]'),
    "untracked files count, ignored files never do");
  // the asymmetric per-message cache: hits latch (no flapping), misses retry every build — a file
  // mentioned before the Write that creates it links on the very rebuild that Write triggers
  assert.ok(KERNEL.includes("_PATH_LINK_CACHE"), "per-message cache");
  assert.ok(KERNEL.includes("misses = tuple(still)"), "misses are re-checked, not cached");
  // one repo listing per build pass, built lazily, never keyed on .git/index mtime
  assert.ok(KERNEL.includes("_pl_memo = {}"), "per-build memo");
  assert.ok(KERNEL.includes("_REPO_LIST_MAX"), "a runaway listing skips tiers 2/3");
});

test("the /file error bodies name the resolved path, and the viewer doesn't repeat it", () => {
  assert.ok(KERNEL.includes('"not found: %s" % _tilde(fp)'));
  assert.ok(KERNEL.includes('"too large to show: %s (%s, limit %s)"'));
  assert.ok(KERNEL.includes('"not a text file: %s" % _tilde(fp)'));
  // the viewer's hint line exists for errors that DON'T name the path (network, old kernel)
  assert.match(VIEW, /if \(!msg\.includes\(path\)\) \{/);
});

// executed: the Python tokenizer (_path_tokens) and CLICKABLE_PATH_RE must agree on what a token IS —
// the map keys the kernel ships are what this client looks up, so drift silently unlinks. Same fixture
// runs against the kernel in tests/test_path_links.py. The regex is EXTRACTED from path-links.ts, so this
// pins the real one, not a copy.
test("tokenizer parity: the client regex over the shared fixture", () => {
  const m = LINKS.match(/const CLICKABLE_PATH_RE = \/(.*)\/gi;/);
  assert.ok(m, "CLICKABLE_PATH_RE found in path-links.ts");
  const fixture = JSON.parse(fs.readFileSync(
    path.resolve(process.cwd(), "..", "tests", "fixtures", "path_token_parity.json"), "utf8"));
  assert.ok(fixture.cases.length >= 10, "the fixture is the parity surface — keep it broad");
  const re = new RegExp(m![1], "gi");
  for (const c of fixture.cases as { text: string; tokens: string[] }[]) {
    // replicate the client loop: match, strip trailing punctuation, resume after the STRIPPED token
    const toks: string[] = [];
    let pos = 0;
    for (;;) {
      re.lastIndex = pos;
      const mm = re.exec(c.text);
      if (!mm) break;
      const tok = mm[0].replace(/[.,;:!?)\]}>"'`]+$/, "");
      pos = Math.max(mm.index + tok.length, pos + 1);
      if (tok && !toks.includes(tok)) toks.push(tok);
    }
    assert.deepEqual(toks, c.tokens, c.text);
  }
});
