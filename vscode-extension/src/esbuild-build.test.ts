// esbuild.js's write step and its failure line, driven against synthetic entries in a scratch dir and, end to
// end, through `node esbuild.js` itself.
//
// The kernel rebuilds the served bundles in place by running `node esbuild.js` and, when that fails, puts the
// last 300 characters of its stderr in the notice that says the UI is stale. Two properties of the script are
// what that path relies on, and both break the same way when a merge imports a package this checkout's
// node_modules predates (the shape of the 2026-08-10 katex failure, which broke every restart's rebuild for a
// day):
//   1. a failed build leaves dist/ exactly as it was. esbuild writes each build()'s outputs as that build
//      finishes, so with the extension bundle built first and the webview bundle second, a webview failure left
//      dist/extension.js rewritten and every webview bundle old: the kernel's cache token (newest mtime under
//      dist/) jumped, every open dashboard got a reload prompt for a build that had not happened, and the drift
//      pass read dist as newer than the sources it had just failed to build;
//   2. the last stderr line names the cause and, for a missing package, the cure. A BuildFailure printed whole
//      ends in its stack through esbuild's transport (`at Socket.emit`, `errors: [Getter/Setter]`), which is
//      what a 300-character tail of it showed.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

// npm test runs in vscode-extension/, where esbuild.js lives. Required at run time rather than imported so
// the bundler leaves it alone: it loads the real esbuild package (a native binary) from node_modules.
const ESBUILD_JS = path.join(process.cwd(), "esbuild.js");
const script = createRequire(__filename)(ESBUILD_JS);
const { buildAll, failureSummary } = script;

const MISSING_PKG = "no-such-package-for-this-test";
// What the kernel shows: kernel/kernel.py's _rebuild_dist takes the stripped stderr's last 300 characters.
const KERNEL_TAIL = 300;

function scratch(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "romp-esbuild-"));
}

// A minimal browser bundle config in the shape of esbuild.js's own: bundled, silent (the test asserts on the
// summary line, not esbuild's logger), outputs under <dir>/dist.
function cfg(dir: string, entry: string, out: string, extra: Record<string, unknown> = {}) {
  return { entryPoints: [path.join(dir, entry)], bundle: true, format: "iife", platform: "browser",
           outfile: path.join(dir, "dist", out), logLevel: "silent", ...extra };
}

async function failureOf(p: Promise<unknown>): Promise<any> {
  try { await p; } catch (e) { return e; }
  throw new Error("expected the build to fail");
}

test("a failed build writes nothing: an earlier bundle that built is not written and a stale dist is untouched", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "good.ts"), "export const n: number = 1;\nconsole.log(n);\n");
    fs.writeFileSync(path.join(d, "bad.ts"), `import "${MISSING_PKG}";\n`);
    // the failure's shape: dist holds an old build of the FIRST bundle, and the SECOND bundle fails
    fs.mkdirSync(path.join(d, "dist"));
    fs.writeFileSync(path.join(d, "dist", "good.js"), "OLD");
    const oldTime = new Date("2026-01-01T00:00:00Z");
    fs.utimesSync(path.join(d, "dist", "good.js"), oldTime, oldTime);

    const e = await failureOf(buildAll([cfg(d, "good.ts", "good.js"), cfg(d, "bad.ts", "bad.js")]));
    assert.ok(Array.isArray(e.errors) && e.errors.length === 1, "an esbuild BuildFailure with the one unresolved import");

    assert.deepEqual(fs.readdirSync(path.join(d, "dist")), ["good.js"], "no new file under dist");
    assert.equal(fs.readFileSync(path.join(d, "dist", "good.js"), "utf8"), "OLD", "the bundle that built was not written");
    assert.equal(fs.statSync(path.join(d, "dist", "good.js")).mtime.getTime(), oldTime.getTime(),
                 "its mtime stands: the kernel's cache token is the newest mtime under dist/, so a rewrite here " +
                 "means a reload prompt for a build that did not happen");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("a build that succeeds writes every output of every bundle: code, sourcemaps, and file-loader assets in their subdir", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "note.txt"), "asset\n");
    fs.writeFileSync(path.join(d, "one.ts"), 'import u from "./note.txt";\nconsole.log(u);\n');
    fs.writeFileSync(path.join(d, "two.ts"), "console.log(2);\n");
    // the KaTeX-font shape: a `file` loader with assets under a subdirectory of dist (dist/fonts/ in the real build)
    const withAsset = cfg(d, "one.ts", "one.js", { loader: { ".txt": "file" }, assetNames: "assets/[name]-[hash]", sourcemap: true });
    const written: string[] = await buildAll([withAsset, cfg(d, "two.ts", "two.js", { sourcemap: true })]);

    const dist = path.join(d, "dist");
    for (const f of ["one.js", "one.js.map", "two.js", "two.js.map"]) {
      assert.ok(fs.existsSync(path.join(dist, f)), f + " written");
    }
    const assets = fs.readdirSync(path.join(dist, "assets"));
    assert.equal(assets.length, 1, "the asset landed in its subdirectory");
    assert.ok(/^note-[A-Z0-9]+\.txt$/i.test(assets[0]), assets[0]);
    assert.equal(fs.readFileSync(path.join(dist, "assets", assets[0]), "utf8"), "asset\n");
    // the return value is the written paths, one per output file
    const expected = ["one.js", "one.js.map", "two.js", "two.js.map", path.join("assets", assets[0])].map((f) => path.join(dist, f)).sort();
    assert.deepEqual([...written].sort(), expected);
    assert.ok(fs.readFileSync(path.join(dist, "two.js"), "utf8").includes("console.log(2)"), "the bundle is the built code");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("failureSummary: an unresolvable package names it and the npm install cure, in one line the kernel's tail can carry whole", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "bad.ts"), `import "${MISSING_PKG}";\n`);
    // both faces of a missing dependency: the source import of a bare specifier, and a node_modules/ path named
    // as an entry point of its own. Two entry points, so outdir, as esbuild.js's webview config has.
    const worker = "node_modules/" + MISSING_PKG + "/worker.mjs";   // 53 characters: under the 60-character cut, shown whole
    const c = { ...cfg(d, "bad.ts", "bad.js"), outfile: undefined, outdir: path.join(d, "dist"),
                entryPoints: [path.join(d, "bad.ts"), worker] };
    const e = await failureOf(buildAll([c]));
    assert.equal(e.errors.length, 2);

    const line: string = failureSummary(e, "dist/");
    assert.ok(!line.includes("\n"), "one line");
    assert.ok(line.length <= KERNEL_TAIL, "the kernel shows the last 300 characters of stderr; the whole line must fit: " + line.length);
    assert.ok(line.startsWith("esbuild.js: build failed with 2 errors; dist/ is unchanged."), line);
    assert.ok(line.includes(`"${MISSING_PKG}"`), "names the package: " + line);
    assert.ok(line.includes(`"${worker}"`), "names the worker path: " + line);
    assert.ok(line.includes("npm install"), "names the cure: " + line);
    assert.ok(!line.includes("Getter/Setter") && !line.includes(" at "), "no stack: " + line);
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("failureSummary: a relative import that does not exist, or a syntax error, gets the error and no npm cure", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "rel.ts"), 'import "./nowhere";\n');
    const rel: string = failureSummary(await failureOf(buildAll([cfg(d, "rel.ts", "rel.js")])), "dist/");
    assert.ok(rel.includes('"./nowhere"'), rel);
    assert.ok(!rel.includes("npm install"), "a relative import is a source error, not a missing dependency: " + rel);

    fs.writeFileSync(path.join(d, "syntax.ts"), "const = ;\n");
    const syn: string = failureSummary(await failureOf(buildAll([cfg(d, "syntax.ts", "syntax.js")])), "dist/");
    assert.ok(syn.startsWith("esbuild.js: build failed with 1 error; dist/ is unchanged."), syn);
    assert.ok(syn.includes("syntax.ts:1"), "the error's location: " + syn);
    assert.ok(!syn.includes("npm install"), syn);
    assert.ok(syn.length <= KERNEL_TAIL);
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("failureSummary: without the output-dir clause (watch and test modes), and for a non-esbuild error", () => {
  const e = Object.assign(new Error("Build failed with 1 error"), { errors: [{ text: 'Could not resolve "./x"', location: null }], warnings: [] });
  assert.equal(failureSummary(e, null), 'esbuild.js: build failed with 1 error. Unresolved: "./x".');
  assert.equal(failureSummary(new Error("boom")), "esbuild.js: build failed: boom");
});

test("failureSummary: an error whose errors list is empty gets the message line, not a read of a first error it has not got", () => {
  // esbuild's API never yields a BuildFailure with no errors. An error object shaped so (a bug here, another
  // library's error that carries an `errors` array) reached the per-error branch, which read errors[0].location
  // and threw inside main().catch: the process died on that TypeError and its stack was the kernel's tail.
  const e = Object.assign(new Error("Build failed with 0 errors"), { errors: [], warnings: [] });
  let line = "";
  assert.doesNotThrow(() => { line = failureSummary(e, "dist/"); });
  assert.equal(line, "esbuild.js: build failed: Build failed with 0 errors");
});

test("failureSummary stays within the kernel's 300-character tail: long specifiers are shortened, a long list is counted", () => {
  const failure = (specs: string[]) => Object.assign(new Error("Build failed"), {
    errors: specs.map((s) => ({ text: `Could not resolve "${s}"`, location: null })), warnings: [] });
  const long = "node_modules/" + "a-very-long-package-name-".repeat(3) + "build/worker.mjs";
  assert.ok(long.length > 60);
  const one = failureSummary(failure([long]), "dist/");
  assert.ok(one.includes('"' + long.slice(0, 57) + '..."'), "a long specifier is cut with an ellipsis: " + one);
  assert.ok(one.includes("npm install"), one);
  const many = failureSummary(failure(["p1", "p2", "p3", "p4", "p5"]), "dist/");
  assert.ok(many.includes('"p1", "p2" (+3 more)'), "two named, the rest counted: " + many);
  assert.ok(!many.includes('"p3"'), many);
  for (const line of [one, many]) assert.ok(line.length <= KERNEL_TAIL && !line.includes("\n"), line);
  // A bare specifier whose first segment is a directory of the cwd (src/ here, in vscode-extension/) is a
  // source path that does not resolve, not a dependency this node_modules lacks: no npm install line.
  assert.ok(fs.existsSync("src"), "the cwd is vscode-extension/, where src/ exists");
  const local = failureSummary(failure(["src/nowhere"]), "dist/");
  assert.ok(local.includes('"src/nowhere"') && !local.includes("npm install"), local);
});

test("requiring esbuild.js runs no build: main() runs only when the file is the script", () => {
  // The tests above required the module and drove buildAll against scratch dirs. Had main() run on require, it
  // would have built the real bundles into the real dist/ (seconds, and a dist mtime bump). Checked in a child
  // whose cwd is an empty directory: a build started there fails on the missing src/extension.ts, prints, and
  // exits 1; a require that runs nothing exits 0 in silence and leaves the directory empty.
  const d = scratch();
  try {
    const r = spawnSync(process.execPath, ["-e", "require(process.argv[1])", "--", ESBUILD_JS], { cwd: d, encoding: "utf8" });
    assert.equal(r.status, 0, "exit status (signal " + r.signal + "); stderr: " + r.stderr);
    // No build ran: no esbuild report (its error marker, an output table under dist/) and no summary line. A
    // notice the runtime itself might print on a require is not what this checks.
    assert.ok(!/\[ERROR\]|Could not resolve|dist\/|esbuild\.js:/.test(r.stderr), "no build output: " + r.stderr);
    assert.equal(r.stdout, "");
    assert.deepEqual(fs.readdirSync(d), [], "nothing written");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
  // and the require yields the pieces the tests drive and the real configs the end-to-end test below stubs
  assert.equal(typeof buildAll, "function");
  assert.equal(typeof failureSummary, "function");
  assert.equal(script.extension.outfile, "dist/extension.js");
  assert.equal(script.webview.outdir, "dist");
  assert.equal(typeof script.testBuild, "function");
});

// A stub source tree for the script's real configs: every entry point it names, relative to a cwd that stands
// in for vscode-extension/ (the webview entries reach up into ../ui/webview). The stubs are valid and import
// nothing, so `node esbuild.js` builds them with the real settings (nodePaths, externals, the file loader).
function stubTree(root: string): string {
  const cwd = path.join(root, "vscode-extension");
  const entries: string[] = [];
  for (const c of [script.extension, script.webview]) {
    for (const e of c.entryPoints) entries.push(typeof e === "string" ? e : e.in);
  }
  for (const rel of entries) {
    const p = path.resolve(cwd, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, rel.endsWith(".css") ? "body { margin: 0; }\n" : `console.log(${JSON.stringify(rel)});\n`);
  }
  return cwd;
}

function snapshot(dir: string): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  const walk = (d: string) => {
    for (const name of fs.readdirSync(d)) {
      const p = path.join(d, name);
      const st = fs.statSync(p);
      if (st.isDirectory()) walk(p);
      else out[path.relative(dir, p)] = [st.size, st.mtimeMs];
    }
  };
  walk(dir);
  return out;
}

test("node esbuild.js, with and without --production: a webview failure after the extension bundle built leaves dist/ as it was and the kernel's tail carries the cause; a filesystem error prints its stack and then the line", () => {
  const root = scratch();
  try {
    const cwd = stubTree(root);
    const run = (...args: string[]) => spawnSync(process.execPath, [ESBUILD_JS, ...args], { cwd, encoding: "utf8" });

    const ok = run();
    assert.equal(ok.status, 0, "the stub tree builds: " + ok.stderr);
    const dist = path.join(cwd, "dist");
    assert.ok(fs.existsSync(path.join(dist, "extension.js")) && fs.existsSync(path.join(dist, "render.js")), fs.readdirSync(dist).join(","));
    // the served build, aged so a rewrite of the same bytes still shows
    const oldTime = new Date("2026-01-01T00:00:00Z");
    for (const rel of Object.keys(snapshot(dist))) fs.utimesSync(path.join(dist, rel), oldTime, oldTime);
    const before = snapshot(dist);

    // the failure's shape: a webview source imports a package this node_modules does not have. Both ways the
    // kernel runs the script take the same path: without --production (its in-place rebuild) and with it (its
    // rebuild at boot, and the release build).
    const render = path.resolve(cwd, "../ui/webview/render.ts");
    const goodRender = fs.readFileSync(render, "utf8");
    fs.appendFileSync(render, `import "${MISSING_PKG}";\n`);
    for (const args of [[], ["--production"]]) {
      const bad = run(...args);
      assert.equal(bad.status, 1, "node esbuild.js " + args.join(" "));
      assert.deepEqual(snapshot(dist), before,
                       "every file under dist/ has its old size and mtime: the extension bundle that built was not written (" + args.join(" ") + ")");

      const lines = bad.stderr.trim().split("\n");
      const last = lines[lines.length - 1];
      assert.ok(last.startsWith("esbuild.js: build failed with 1 error; dist/ is unchanged."), "the last line: " + last);
      assert.ok(last.includes(`"${MISSING_PKG}"`) && last.includes("npm install"), last);
      assert.ok(bad.stderr.includes("Could not resolve"), "esbuild's own report with its code frame still precedes it");
      assert.ok(!bad.stderr.includes("Getter/Setter"), "the BuildFailure object is not printed");
      const kernelShows = bad.stderr.trim().slice(-KERNEL_TAIL);
      assert.ok(kernelShows.endsWith(last) && kernelShows.includes("esbuild.js: build failed"), "the kernel's tail carries the line whole: " + kernelShows);
    }

    // Not a BuildFailure: the sources build and the write step fails on the filesystem. A regular file in
    // dist/'s place makes mkdirSync refuse with EEXIST before any output is staged, a refusal that takes no
    // permission change to arrange. esbuild reported nothing, so the error is printed whole (its stack is the
    // diagnosis) and the summary line still closes stderr.
    fs.writeFileSync(render, goodRender);
    fs.rmSync(dist, { recursive: true });
    fs.writeFileSync(dist, "not a directory\n");
    const fsErr = run();
    assert.equal(fsErr.status, 1, "exit status (signal " + fsErr.signal + "); stderr: " + fsErr.stderr);
    assert.ok(/\n\s+at /.test(fsErr.stderr), "the error's stack is printed: " + fsErr.stderr);
    const fsLines = fsErr.stderr.trim().split("\n");
    assert.ok(fsLines[fsLines.length - 1].startsWith("esbuild.js: build failed: EEXIST"), "the last line: " + fsLines[fsLines.length - 1]);
    assert.equal(fs.readFileSync(dist, "utf8"), "not a directory\n", "what stood in dist/'s place is untouched");
    assert.ok(fs.readdirSync(cwd).every((n) => !n.startsWith(".")), "nothing staged beside it: " + fs.readdirSync(cwd).join(","));
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("node esbuild.js: an error whose errors list is empty is no esbuild report; its stack is printed and the line closes stderr", () => {
  // esbuild's API never yields a BuildFailure with no errors, so a stub esbuild stands in: a preload replaces
  // the package with one whose build() rejects with such an object. Before, failureSummary read errors[0] of
  // it and threw inside main().catch: the process died on that TypeError with no summary line, and the tail
  // the kernel shows was the TypeError's stack. The stack that IS wanted is the error's own, printed as for any
  // non-esbuild error, since esbuild printed nothing for it.
  const d = scratch();
  try {
    const preload = path.join(d, "stub-esbuild.js");
    fs.writeFileSync(preload, [
      'const Module = require("module");',
      "const load = Module._load;",
      "Module._load = function (request, ...rest) {",
      '  if (request !== "esbuild") return load.call(this, request, ...rest);',
      '  const e = Object.assign(new Error("Build failed with 0 errors"), { errors: [], warnings: [] });',
      "  return { build: () => Promise.reject(e) };",
      "};",
      "",
    ].join("\n"));
    const r = spawnSync(process.execPath, ["-r", preload, ESBUILD_JS], { cwd: d, encoding: "utf8" });
    assert.equal(r.status, 1, "exit status (signal " + r.signal + "); stderr: " + r.stderr);
    assert.ok(!r.stderr.includes("TypeError"), "no TypeError: " + r.stderr);
    assert.ok(/Build failed with 0 errors\n\s+at /.test(r.stderr), "the error's own stack is printed: " + r.stderr);
    const lines = r.stderr.trim().split("\n");
    assert.equal(lines[lines.length - 1], "esbuild.js: build failed: Build failed with 0 errors");
    assert.deepEqual(fs.readdirSync(d), [path.basename(preload)], "nothing written");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});
