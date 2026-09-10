// esbuild.js's write step: each output reaches dist/ by rename, so the kernel, which serves /dist/ from that
// directory on other threads while it rebuilds, never sends a truncated bundle.
//
// An in-place write (truncate, then fill) gives a concurrent GET an empty or partial file for the length of
// the write, longest for the largest output (render.js, over a megabyte); a page loaded in that window runs
// a bundle cut mid-statement and stays broken until it is reloaded. These tests interpose on
// fs.writeFileSync, the one call buildAll writes through, to read the served names in the middle of each
// write; the staging names, the fs-error path and the removal of leftovers are pinned beside it.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as os from "node:os";
import * as path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

// The REAL fs module object, shared with esbuild.js. The bundled `import * as fs` is a namespace copy whose
// properties are getters, so a patch on it would never reach the script under test.
const req = createRequire(__filename);
const fs: typeof import("node:fs") = req("fs");
// npm test runs in vscode-extension/, where esbuild.js lives. Required at run time rather than imported so
// the bundler leaves it alone: it loads the real esbuild package (a native binary) from node_modules.
const ESBUILD_JS = path.join(process.cwd(), "esbuild.js");
const { buildAll } = req(ESBUILD_JS);

function scratch(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "romp-esbuild-rename-"));
}

// A minimal browser bundle config in the shape of esbuild.js's own: bundled, silent, output under <dir>/dist.
function cfg(dir: string, entry: string, out: string) {
  return { entryPoints: [path.join(dir, entry)], bundle: true, format: "iife", platform: "browser",
           outfile: path.join(dir, "dist", out), logLevel: "silent" };
}

const isOutputWrite = (p: unknown, data: unknown) => typeof p === "string" && data instanceof Uint8Array;

// Runs `run` with fs.writeFileSync replaced for output writes by a version that writes the bytes in two halves
// and calls `between(target)` after the first: the moment at which an in-place write has truncated its file
// and not yet refilled it. Returns the paths written through it, in order.
async function withSplitWrites(between: (target: string) => void, run: () => Promise<void>): Promise<string[]> {
  const real = fs.writeFileSync;
  const targets: string[] = [];
  (fs as any).writeFileSync = (p: any, data: any, opts?: any) => {
    if (!isOutputWrite(p, data)) return real.call(fs, p, data, opts);
    targets.push(p);
    const fd = fs.openSync(p, "w");
    try {
      const half = Math.max(1, Math.floor(data.length / 2));
      fs.writeSync(fd, data, 0, half);
      between(p);
      fs.writeSync(fd, data, half);
    } finally {
      fs.closeSync(fd);
    }
  };
  try {
    await run();
  } finally {
    (fs as any).writeFileSync = real;
  }
  return targets;
}

const OLD = "console.log('the old build, complete');\n";

test("a reader of dist/ in the middle of a write sees the old file whole or the new one whole, never a partial file", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "one.ts"), "console.log('one: the new build');\n");
    fs.writeFileSync(path.join(d, "two.ts"), "console.log('two: the new build');\n");
    const dist = path.join(d, "dist");
    fs.mkdirSync(dist);
    const one = path.join(dist, "one.js");
    const two = path.join(dist, "two.js");
    fs.writeFileSync(one, OLD);              // an existing bundle being replaced; two.js is a new output
    // what the served names held at each mid-write moment, checked once the new bytes are known
    const seen: Array<{ during: string; one: string; two: string | null }> = [];
    const targets = await withSplitWrites((target) => {
      seen.push({ during: path.basename(target),
                  one: fs.readFileSync(one, "utf8"),
                  two: fs.existsSync(two) ? fs.readFileSync(two, "utf8") : null });
    }, async () => {
      const written: string[] = await buildAll([cfg(d, "one.ts", "one.js"), cfg(d, "two.ts", "two.js")]);
      assert.deepEqual([...written].sort(), [one, two], "the return value names the served paths");
    });

    const newOne = fs.readFileSync(one, "utf8");
    const newTwo = fs.readFileSync(two, "utf8");
    assert.ok(newOne.includes("one: the new build") && newTwo.includes("two: the new build"), "the new build landed");
    assert.equal(seen.length, 2, "both outputs went through fs.writeFileSync, so both mid-write moments were observed");
    for (const s of seen) {
      assert.ok(s.one === OLD || s.one === newOne, "one.js during the write of " + s.during + ": " + JSON.stringify(s.one));
      assert.ok(s.two === null || s.two === newTwo, "two.js during the write of " + s.during + ": " + JSON.stringify(s.two));
    }
    for (const t of targets) {
      const name = path.basename(t);
      assert.equal(path.dirname(t), dist, "staged in the destination's own directory, so the rename cannot cross a filesystem: " + t);
      assert.ok(t !== one && t !== two, "the write never targets a served name: " + name);
      assert.ok(name.startsWith("."), "a hidden name: " + name);
      assert.ok(!/\.(js|css|map|svg|ttf|woff2?|png)$/.test(name),
                "ends in none of the suffixes the kernel's newest-mtime token globs (dist/*.js) or its /dist route types: " + name);
      assert.ok(name.includes(".tmp-" + process.pid + "-"), "carries this process's pid, which the removal of leftovers reads: " + name);
    }
    assert.deepEqual(fs.readdirSync(dist).sort(), ["one.js", "two.js"], "no staging file is left behind");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("an fs error while writing leaves dist/ byte-for-byte as it was, with no staging file behind", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "one.ts"), "console.log(1);\n");
    fs.writeFileSync(path.join(d, "two.ts"), "console.log(2);\n");
    const dist = path.join(d, "dist");
    fs.mkdirSync(dist);
    const one = path.join(dist, "one.js");
    fs.writeFileSync(one, OLD);
    const oldTime = new Date("2026-01-01T00:00:00Z");
    fs.utimesSync(one, oldTime, oldTime);

    // the first output stages, the second meets a full disk
    const real = fs.writeFileSync;
    let writes = 0;
    (fs as any).writeFileSync = (p: any, data: any, opts?: any) => {
      if (isOutputWrite(p, data) && ++writes === 2) {
        throw Object.assign(new Error("ENOSPC: no space left on device, write"), { code: "ENOSPC" });
      }
      return real.call(fs, p, data, opts);
    };
    let err: any = null;
    try {
      await buildAll([cfg(d, "one.ts", "one.js"), cfg(d, "two.ts", "two.js")]);
    } catch (e) {
      err = e;
    } finally {
      (fs as any).writeFileSync = real;
    }
    assert.equal(writes, 2, "the second staging write is where it failed");
    assert.equal(err && err.code, "ENOSPC", "the fs error is what buildAll rejects with (main prints it as a non-esbuild failure)");
    assert.deepEqual(fs.readdirSync(dist), ["one.js"], "the staged first output was removed, the second never appeared");
    assert.equal(fs.readFileSync(one, "utf8"), OLD, "the served bundle is untouched");
    assert.equal(fs.statSync(one).mtime.getTime(), oldTime.getTime(),
                 "its mtime stands, so the kernel's cache token does not announce a build that did not land");
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});

test("a staging file left by a killed earlier run is removed; one that belongs to a running process is kept", async () => {
  const d = scratch();
  try {
    fs.writeFileSync(path.join(d, "one.ts"), "console.log(1);\n");
    const dist = path.join(d, "dist");
    fs.mkdirSync(dist);
    // a process that has exited: its pid is free, like the pid of a build the kernel's timeout killed
    const gone = spawnSync(process.execPath, ["-e", "0"]).pid as number;
    assert.ok(gone > 0);
    const stale = ".one.js.tmp-" + gone + "-0";
    // this process's own pid stands in for a second build running against the same dist/
    const live = ".one.js.tmp-" + process.pid + "-99";
    fs.writeFileSync(path.join(dist, stale), "partial");
    fs.writeFileSync(path.join(dist, live), "partial");
    // a hidden file that is not a staging file is nobody's to remove
    fs.writeFileSync(path.join(dist, ".keep"), "");

    await buildAll([cfg(d, "one.ts", "one.js")]);
    assert.deepEqual(fs.readdirSync(dist).sort(), [".keep", live, "one.js"].sort());
    assert.ok(fs.readFileSync(path.join(dist, "one.js"), "utf8").includes("console.log(1)"));
  } finally {
    fs.rmSync(d, { recursive: true, force: true });
  }
});
