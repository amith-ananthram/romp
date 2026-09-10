// esbuild.js's failure line is cut to 300 characters from the END, so the kernel, which shows the last 300
// characters of the script's stderr when its in-place rebuild fails, shows the head of the line (what failed,
// what is unchanged) rather than the end of its last clause.
//
// esbuild-build.test.ts pins the line's content and asserts `length <= 300`, but every input it feeds keeps
// the line under 300 before the cut (the longest is 248), so the cut itself needs its own cases: a script
// without it passes that file. The line is sized so a plausible one fits whole; what reaches the cut is a
// long error text over a long location path (the text is cut at 120, the path never is) and a non-esbuild
// error's message, which the script does not compose. Both are driven here: the boundary (300 fits whole,
// 301 is cut) over a synthetic esbuild error, the text's own cut at 120 over a short path, a REAL syntax error
// under a deep synthetic directory tree, and a non-esbuild error with a long message.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { createRequire } from "node:module";

// npm test runs in vscode-extension/, where esbuild.js lives. Required at run time rather than imported so
// the bundler leaves it alone: it loads the real esbuild package (a native binary) from node_modules.
const ESBUILD_JS = path.join(process.cwd(), "esbuild.js");
const { buildAll, failureSummary } = createRequire(__filename)(ESBUILD_JS);

const HEAD = "esbuild.js: build failed with 1 error; dist/ is unchanged.";
// What the kernel shows: kernel/kernel.py's _rebuild_dist takes the stripped stderr's last 300 characters.
const KERNEL_TAIL = 300;
const kernelShows = (stderrLine: string) => stderrLine.slice(-KERNEL_TAIL);

// An esbuild BuildFailure's shape: the one error a syntax error yields, at a location in a file.
function syntaxFailure(text: string, file: string) {
  return Object.assign(new Error("Build failed with 1 error"), {
    errors: [{ text, location: { file, line: 1, column: 6 } }], warnings: [] });
}

test("failureSummary's cut sits at 300: a line of 300 goes out whole, one of 301 is cut to 300 with an ellipsis and its head intact", () => {
  // A 100-character text stays under the text's own 120 cut, so the line's length moves one for one with the
  // path's: measure the line over a short path, then size the path to land on 300 exactly.
  const text = "Expected a token here but found something else instead, which is not what this syntax allows ".padEnd(100, "x");
  assert.equal(text.length, 100);
  const lineFor = (pathLen: number): string =>
    failureSummary(syntaxFailure(text, "../ui/webview/".padEnd(pathLen, "d")), "dist/");
  const base = lineFor(14).length;
  const fits = 14 + (300 - base);

  const whole = lineFor(fits);
  assert.equal(whole.length, 300, "a line of exactly 300 is the kernel's tail: shown whole");
  assert.ok(!whole.endsWith("..."), "and not cut: " + whole.slice(-40));
  assert.ok(whole.endsWith(":1)"), "its location closes the line: " + whole.slice(-40));

  const cut = lineFor(fits + 1);
  assert.equal(cut.length, 300, "one over is cut back to 300, not left for the kernel to cut from the front");
  assert.ok(cut.endsWith("..."), "the cut is marked: " + cut.slice(-40));
  assert.equal(cut.slice(0, 297), whole.slice(0, 297), "the cut takes the END: the first 297 characters are the line as composed");
  assert.ok(cut.startsWith(HEAD + " " + text + " (../ui/webview/"), "the head reads whole: " + cut);
  assert.ok(!cut.includes("\n"));

  // The far side: a much longer path is cut the same way, and what the kernel shows IS the line.
  const far = lineFor(600);
  assert.equal(far.length, 300);
  assert.ok(far.startsWith(HEAD), far);
  assert.equal(kernelShows(far), far, "the kernel's tail carries the whole line");
});

test("failureSummary: an error text over 120 characters is cut to 117 and an ellipsis, and its location still closes the line", () => {
  // The text is the one part of an esbuild error the line shortens (a location path is never cut: a cut path
  // names no file), so a long text over a short path stays well under 300 and the text's own cut is what shows.
  const text = "Unexpected token in an expression that goes on for long enough to pass the cut the text gets ".padEnd(200, "y");
  assert.equal(text.length, 200);
  const file = "../ui/webview/render.ts";
  const line: string = failureSummary(syntaxFailure(text, file), "dist/");
  assert.equal(line, HEAD + " " + text.slice(0, 117) + "... (" + file + ":1)");
  assert.ok(!line.includes(text.slice(0, 118)), "the 118th character is gone: " + line);
  assert.ok(line.length < 300, "well under the 300 cut, so this is the text's own: " + line.length);
  // a text of exactly 120 goes out whole
  const exact = text.slice(0, 120);
  assert.equal(failureSummary(syntaxFailure(exact, file), "dist/"), HEAD + " " + exact + " (" + file + ":1)");
});

test("failureSummary: a real syntax error under a deep path is cut to 300 with its head intact; uncut, the kernel would drop the head", async () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "romp-esbuild-cap-"));
  try {
    // esbuild names the file relative to the cwd (vscode-extension/), so the reported path carries the whole
    // way down. Thirteen 20-character directories exceed 300 on their own; the exact length is not the point.
    const deep = path.join(d, ...Array.from({ length: 13 }, (_, i) => `deeply-nested-dir-${String(i).padStart(2, "0")}`));
    fs.mkdirSync(deep, { recursive: true });
    fs.writeFileSync(path.join(deep, "syntax.ts"), "const = ;\n");
    const c = { entryPoints: [path.join(deep, "syntax.ts")], bundle: true, format: "iife", platform: "browser",
                outfile: path.join(d, "dist", "syntax.js"), logLevel: "silent" };
    let e: any = null;
    try { await buildAll([c]); } catch (x) { e = x; }
    assert.ok(e && Array.isArray(e.errors) && e.errors.length === 1, "an esbuild BuildFailure with the one syntax error");
    assert.ok(e.errors[0].location && e.errors[0].location.file.length > 250, "the location path is what makes the line long: " + e.errors[0].location.file);

    const line: string = failureSummary(e, "dist/");
    assert.equal(line.length, 300, "the line is the kernel's tail exactly: " + line.length);
    assert.ok(line.startsWith(HEAD + " "), "what failed and what is unchanged read whole: " + line);
    assert.ok(line.includes(" (") && line.includes("deeply-nested-dir-00"), "the location opens and names the tree's top: " + line);
    assert.ok(line.endsWith("...") && !line.includes("\n"), line.slice(-40));
    assert.equal(kernelShows(line), line);

    // Why the cut is on the script's side: the same content uncut, through the kernel's tail, loses the head.
    const uncut = HEAD + " " + e.errors[0].text + " (" + e.errors[0].location.file + ":1)";
    assert.ok(uncut.length > 300);
    assert.ok(!kernelShows(uncut).startsWith("esbuild.js"), "the tail of the uncut line starts mid-word: " + kernelShows(uncut).slice(0, 40));
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("failureSummary: a non-esbuild error's message is cut the same way, so an fs error's head is what the kernel shows", () => {
  // Not a BuildFailure: an fs error from the write step, or a bug in the script. Its message is Node's, so its
  // length is not the script's to size; the cut is the one guard.
  const long = "ENOENT: no such file or directory, open '" + "/a-long-synthetic-path".repeat(20) + "/dist/.render.js.tmp-1-2'";
  assert.ok(long.length > 300);
  const line: string = failureSummary(new Error(long));
  assert.equal(line.length, 300);
  assert.ok(line.startsWith("esbuild.js: build failed: ENOENT: no such file or directory, open '/a-long-synthetic-path"), line);
  assert.ok(line.endsWith("..."), line.slice(-40));
  assert.equal(kernelShows(line), line);
  // A short one is untouched (its exact text is pinned in esbuild-build.test.ts); the boundary holds here too.
  const exact = "x".repeat(300 - "esbuild.js: build failed: ".length);
  assert.equal(failureSummary(new Error(exact)), "esbuild.js: build failed: " + exact);
  assert.equal(failureSummary(new Error(exact + "y")).length, 300);
});
