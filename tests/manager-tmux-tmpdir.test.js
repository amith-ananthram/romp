// Where the tmux server keeps its socket (T325, 2026-09-11): bin/romp-manager's tmuxTmpdir is the node twin of
// kernel/tmux_socket.py and bin/romp-tmux-env. An operator's TMUX_TMPDIR wins as it stands; else a writable
// XDG_RUNTIME_DIR gives <XDG_RUNTIME_DIR>/romp, made 0700 when missing; else null (tmux's default). Pure over the
// env and the fs it is handed, so the cases run against a fake fs here; the cross-language agreement on real
// directories is tests/test_tmux_socket_dir.py's.
// Run: node --test tests/manager-*.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

process.env.ROMP_STATE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'romp-mgr-tmux-tmpdir-'));
const { tmuxTmpdir, resolveTmuxTmpdir, describeTmuxTmpdir, TMUX_DIR_RULES } = require(path.join(__dirname, '..', 'bin', 'romp-manager'));

// a fake fs: `dirs` are usable directories, `files` plain files, `refuse` paths whose mkdir throws; mkdir records
function fakeFs({ dirs = [], files = [], refuse = [] } = {}) {
  const made = [];
  const norm = (p) => p.replace(/\/+$/, '') || '/';   // as the real fs: a trailing slash names the same entry
  const isDir = (p) => dirs.includes(norm(p));
  return {
    made,
    statSync(p) { if (isDir(p)) return { isDirectory: () => true }; if (files.includes(norm(p))) return { isDirectory: () => false }; throw new Error('ENOENT'); },
    accessSync(p) { if (!isDir(p)) throw new Error('EACCES'); },
    mkdirSync(p, opts) { if (refuse.includes(p)) throw new Error('EACCES'); if (isDir(p) || files.includes(p)) throw new Error('EEXIST'); made.push([p, opts && opts.mode]); dirs.push(p); },   // as the real one: EEXIST for a file or a directory already there
    realpathSync(p) { const c = p.replace(/\/+$/, '') || '/'; if (isDir(c) || files.includes(c)) return c; throw new Error('ENOENT'); },   // canonical: no trailing slash
  };
}

test('the operator\'s TMUX_TMPDIR wins as it stands, untrimmed, and nothing is made', () => {
  const fsi = fakeFs({ dirs: ['/run/user/1000'] });
  assert.equal(tmuxTmpdir({ env: { TMUX_TMPDIR: '/op/dir', XDG_RUNTIME_DIR: '/run/user/1000' }, fsi }), '/op/dir');
  assert.equal(tmuxTmpdir({ env: { TMUX_TMPDIR: ' /op/dir ', XDG_RUNTIME_DIR: '/run/user/1000' }, fsi }), ' /op/dir ', 'byte for byte, as the Python and shell twins');
  assert.deepEqual(resolveTmuxTmpdir({ env: { TMUX_TMPDIR: '/op/dir' }, fsi }), { dir: '/op/dir', rule: TMUX_DIR_RULES.operator });
  assert.deepEqual(fsi.made, []);
});

test('the runtime dir is canonical (a trailing slash is the same directory) and a launcher\'s mark names its rule', () => {
  const fsi = fakeFs({ dirs: ['/run/user/1000'] });
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000/' }, fsi }), '/run/user/1000/romp');
  assert.deepEqual(resolveTmuxTmpdir({ env: { TMUX_TMPDIR: '/run/user/1000/romp', ROMP_TMUX_TMPDIR_RULE: 'runtime-dir' }, fsi }), { dir: '/run/user/1000/romp', rule: 'runtime-dir' }, 'bin/romp resolved it before starting this manager');
  assert.deepEqual(resolveTmuxTmpdir({ env: { TMUX_TMPDIR: '/op', ROMP_TMUX_TMPDIR_RULE: 'made-up' }, fsi }), { dir: '/op', rule: 'operator' }, 'an unknown mark is ignored');
});

test('every answer names the rule that chose it, and the log line says it', () => {
  assert.deepEqual(resolveTmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi: fakeFs({ dirs: ['/run/user/1000'] }) }), { dir: '/run/user/1000/romp', rule: 'runtime-dir' });
  assert.deepEqual(resolveTmuxTmpdir({ env: {}, fsi: fakeFs() }), { dir: null, rule: 'no XDG_RUNTIME_DIR' });
  assert.deepEqual(resolveTmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi: fakeFs({ files: ['/run/user/1000'] }) }), { dir: null, rule: 'XDG_RUNTIME_DIR is not a writable directory' });
  assert.deepEqual(resolveTmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi: fakeFs({ dirs: ['/run/user/1000'], refuse: ['/run/user/1000/romp'] }) }), { dir: null, rule: 'XDG_RUNTIME_DIR/romp could not be made, or is not a writable directory' });
  assert.equal(describeTmuxTmpdir({ dir: '/op', rule: TMUX_DIR_RULES.operator }), '/op (TMUX_TMPDIR set by the operator)');
  assert.equal(describeTmuxTmpdir({ dir: null, rule: TMUX_DIR_RULES.noRuntime }), 'tmux default (no XDG_RUNTIME_DIR)');
  // the same rule names as kernel/tmux_socket.py, so the two logs read alike
  const py = fs.readFileSync(path.join(__dirname, '..', 'kernel', 'tmux_socket.py'), 'utf8');
  for (const r of Object.values(TMUX_DIR_RULES)) assert.ok(py.includes(`"${r}"`), `python names the rule ${r}`);
});

test('a writable runtime dir gives its romp subdirectory, made 0700 when missing', () => {
  const fsi = fakeFs({ dirs: ['/run/user/1000'] });
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi }), '/run/user/1000/romp');
  assert.deepEqual(fsi.made, [['/run/user/1000/romp', 0o700]]);
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi }), '/run/user/1000/romp', 'idempotent');
  assert.equal(fsi.made.length, 1, 'not made twice');
});

test('no runtime dir, a missing one, a file, or a refused mkdir: tmux\'s default (null)', () => {
  assert.equal(tmuxTmpdir({ env: {}, fsi: fakeFs() }), null);
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '' }, fsi: fakeFs() }), null);
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi: fakeFs() }), null, 'missing');
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi: fakeFs({ files: ['/run/user/1000'] }) }), null, 'a file');
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi: fakeFs({ dirs: ['/run/user/1000'], refuse: ['/run/user/1000/romp'] }) }), null, 'mkdir refused');
  assert.equal(tmuxTmpdir({ env: { XDG_RUNTIME_DIR: '/run/user/1000' }, fsi: fakeFs({ dirs: ['/run/user/1000'], files: ['/run/user/1000/romp'] }) }), null, 'a file where the subdirectory should be');
});

test('startManager resolves it into process.env before starting the server (the runtime-dir rule only), and says which rule fired', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'bin', 'romp-manager'), 'utf8');
  assert.match(src, /const tmuxPick = resolveTmuxTmpdir\(\);\s*if \(tmuxPick\.rule === TMUX_DIR_RULES\.runtime\) process\.env\.TMUX_TMPDIR = tmuxPick\.dir;/);
  assert.match(src, /log\(`tmux socket dir: \$\{describeTmuxTmpdir\(tmuxPick\)\}`\);/);
  assert.ok(src.indexOf('const tmuxPick = resolveTmuxTmpdir();') < src.indexOf('  startTmuxServer();\n  const server = http.createServer'), 'resolved BEFORE the server starts');
});

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
  const fakeBin = tmpRoot('romp-mgr-stale-bin-');
  fs.writeFileSync(path.join(fakeBin, 'tmux'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });   // startManager starts a server: never the real tmux here
  // startManager spawns every boot spec once it listens: a stub launcher that sleeps stands in for the kernel (its
  // record replaces the seeded one, so the row is judged by kernel and trigger, not by the stand-in's pid)
  const stub = path.join(fakeBin, 'romp-serve');
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
  `, { ROMP_MANAGER_PORT: String(port), PATH: fakeBin + path.delimiter + process.env.PATH, ROMP_CLI_SCOPE: '0', ROMP_SERVE_BIN: stub });
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
    let spawns = 0;
    const orig = m.spawnKernel;
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
