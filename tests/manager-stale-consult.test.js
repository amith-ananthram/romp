// romp-manager's stale-manager consult on the two doors that bring a kernel up under the RUNNING manager: a
// single kernel's /restart and the crash respawn (T325 review; `romp refresh` had it first, in restartAllOrSelf).
// A manager whose binary changed since it started must not bring a kernel up under itself, because that kernel
// inherits the old manager's environment for as long as the checkout sits ahead of it: supervised, the manager
// exits for its own respawn instead, stopping the kernel with trigger refresh; foreground, it warns and lets the
// kernel come up. staleManagerYields is the shared consult; its source pin is the last case.
// Run: node --test tests/manager-*.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// The two doors driven for real (the harness tests/manager-exit-attribution.test.js uses): a COPY of the manager whose
// mtime moves after the require is what a deploy does to the real file under a supervised manager; a stand-in kernel
// records the SIGTERM it gets; the child is allowed to exit on its own (shutdownAll exits after 800 ms).
const { spawnSync } = require('node:child_process');
const ROOTS = [];
const tmpRoot = (prefix) => { const d = fs.mkdtempSync(path.join(os.tmpdir(), prefix)); ROOTS.push(d); return d; };
process.on('exit', () => { for (const d of ROOTS) fs.rmSync(d, { recursive: true, force: true }); });
const auditRows = (root) => { try { return fs.readFileSync(path.join(root, 'restart-audit.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)); } catch { return []; } };
function staleCopy() {
  const copyRoot = tmpRoot('romp-mgr-stale-copy-');
  const copy = path.join(copyRoot, 'romp-manager');
  fs.copyFileSync(path.join(__dirname, '..', 'bin', 'romp-manager'), copy);
  return copy;
}
function inChild(script, extraEnv) {
  const root = tmpRoot('romp-mgr-stale-child-');
  const env = Object.assign({}, process.env, { ROMP_STATE_DIR: root, ROMP_SERVE_PORT: '1', ROMP_MANAGER_PORT: '1', ROMP_SUPERVISED: '1' }, extraEnv || {});
  delete env.XDG_STATE_HOME;
  const r = spawnSync(process.execPath, ['-e', script], { env, encoding: 'utf8', timeout: 20000 });
  return { root, status: r.status, stderr: r.stderr, stdout: r.stdout, rows: auditRows(root) };
}
const standIn = `
  const fs = require('fs'), path = require('path');
  const root = process.env.ROMP_STATE_DIR;
  const child = { pid: 777, kill(sig) { fs.appendFileSync(path.join(root, 'kills.txt'), sig + '\\n'); } };
  m.kernels.set('main', { spec: { id: 'main', port: 1 }, child, restarts: 0, quickCrashes: 0, startedAt: Date.now(), stopping: false, requested: null });
  const past = new Date(Date.now() - 60000);
  fs.utimesSync(COPY, past, past);   // the deploy: the file moved under the running manager
`;

test('a stale supervised manager asked to restart one kernel (/restart) exits for its own respawn instead, stopping the kernel with trigger refresh', () => {
  const copy = staleCopy();
  const port = 20000 + Math.floor(Math.random() * 20000);
  // startManager spawns every boot spec once it listens: a stub launcher that sleeps stands in for the kernel (its
  // record replaces the seeded one, so the row is judged by kernel and trigger, not by the stand-in's pid)
  const stubRoot = tmpRoot('romp-mgr-stale-stub-');
  const stub = path.join(stubRoot, 'romp-serve');
  fs.writeFileSync(stub, '#!/bin/sh\nexec sleep 30\n', { mode: 0o755 });
  const r = inChild(`
    const COPY = ${JSON.stringify(copy)};
    const m = require(COPY);
    ${standIn}
    m.startManager();
    const http = require('http');
    setTimeout(() => {
      const req = http.request({ host: '127.0.0.1', port: ${port}, method: 'POST', path: '/restart?kernel=main' }, (res) => {
        let body = ''; res.on('data', (c) => body += c); res.on('end', () => fs.writeFileSync(path.join(process.env.ROMP_STATE_DIR, 'verdict.json'), body));
      });
      req.on('error', (e) => { fs.writeFileSync(path.join(process.env.ROMP_STATE_DIR, 'verdict.json'), JSON.stringify({ error: String(e) })); process.exit(4); });
      req.end();
    }, 300);
    setTimeout(() => process.exit(5), 15000);
  `, { ROMP_MANAGER_PORT: String(port), ROMP_CLI_SCOPE: '0', ROMP_SERVE_BIN: stub });
  assert.equal(r.status, 0, r.stderr);   // shutdownAll(0): the manager left for the supervisor's respawn
  const verdict = JSON.parse(fs.readFileSync(path.join(r.root, 'verdict.json'), 'utf8'));
  assert.deepEqual(verdict, { ok: true, restarted: 'main', managerRestart: true });
  assert.equal(r.rows.length, 1, JSON.stringify(r.rows));
  assert.deepEqual([r.rows[0].kernel, r.rows[0].reason, r.rows[0].trigger], ['main', 'stop', 'refresh']);
  assert.match(r.stderr, /exiting for a supervised respawn instead of restarting kernel 'main'/);
  assert.doesNotMatch(r.stderr, /restart 'main' requested[\s\S]*kernel 'main' → :1 \(pid \d+\)[\s\S]*kernel 'main' → :1/, 'no second spawn under the stale manager');
});

test('a stale supervised manager whose kernel crashes exits for its own respawn instead of respawning it under itself', () => {
  const copy = staleCopy();
  const stubRoot = tmpRoot('romp-mgr-stale-stub-');
  const stub = path.join(stubRoot, 'romp-serve');
  fs.writeFileSync(stub, '#!/bin/sh\nexit 1\n', { mode: 0o755 });   // a launcher that dies at once: the crash
  const r = inChild(`
    const COPY = ${JSON.stringify(copy)};
    const m = require(COPY);
    const fs = require('fs');
    const past = new Date(Date.now() - 60000);
    fs.utimesSync(COPY, past, past);
    m.spawnKernel({ id: 'main', port: 1 });
    setTimeout(() => process.exit(5), 15000);
  `, { ROMP_SERVE_BIN: stub });
  assert.equal(r.status, 0, r.stderr);   // the exit handler's respawn timer met the stale manager and shutdownAll left
  assert.match(r.stderr, /exiting for a supervised respawn instead of respawning kernel 'main'/);
  assert.equal((r.stderr.match(/kernel 'main' → :1 \(pid/g) || []).length, 1, 'spawned once, never respawned under the stale manager');
});

test('a stale manager yields on a single kernel restart and on a crash respawn, as it does on refresh', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'bin', 'romp-manager'), 'utf8');
  assert.match(src, /function staleManagerYields\(why\) \{\s*if \(!managerStale\(\)\) return false;\s*if \(process\.env\.ROMP_SUPERVISED\) \{[\s\S]*?shutdownAll\(0, 'refresh'\);\s*return true;/);
  assert.match(src, /if \(staleManagerYields\(`respawning kernel '\$\{spec\.id\}'`\)\) return;[^\n]*\n\s*spawnKernel\(spec\);/, 'the crash respawn consults it');
  assert.match(src, /if \(staleManagerYields\(`restarting kernel '\$\{kid\}'`\)\) return json\(200, \{ ok: true, restarted: kid, managerRestart: true \}\);\s*return restartKernel\(kid\)/, '/restart consults it');
});
