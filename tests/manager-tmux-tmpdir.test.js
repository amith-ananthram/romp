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
  const isDir = (p) => dirs.includes(p);
  return {
    made,
    statSync(p) { if (isDir(p)) return { isDirectory: () => true }; if (files.includes(p)) return { isDirectory: () => false }; throw new Error('ENOENT'); },
    accessSync(p) { if (!isDir(p)) throw new Error('EACCES'); },
    mkdirSync(p, opts) { if (refuse.includes(p)) throw new Error('EACCES'); if (isDir(p) || files.includes(p)) throw new Error('EEXIST'); made.push([p, opts && opts.mode]); dirs.push(p); },   // as the real one: EEXIST for a file or a directory already there
  };
}

test('the operator\'s TMUX_TMPDIR wins as it stands, untrimmed, and nothing is made', () => {
  const fsi = fakeFs({ dirs: ['/run/user/1000'] });
  assert.equal(tmuxTmpdir({ env: { TMUX_TMPDIR: '/op/dir', XDG_RUNTIME_DIR: '/run/user/1000' }, fsi }), '/op/dir');
  assert.equal(tmuxTmpdir({ env: { TMUX_TMPDIR: ' /op/dir ', XDG_RUNTIME_DIR: '/run/user/1000' }, fsi }), ' /op/dir ', 'byte for byte, as the Python and shell twins');
  assert.deepEqual(resolveTmuxTmpdir({ env: { TMUX_TMPDIR: '/op/dir' }, fsi }), { dir: '/op/dir', rule: TMUX_DIR_RULES.operator });
  assert.deepEqual(fsi.made, []);
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

test('a stale manager yields on a single kernel restart and on a crash respawn, as it does on refresh', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'bin', 'romp-manager'), 'utf8');
  assert.match(src, /function staleManagerYields\(why\) \{\s*if \(!managerStale\(\)\) return false;\s*if \(process\.env\.ROMP_SUPERVISED\) \{[\s\S]*?shutdownAll\(0, 'refresh'\);\s*return true;/);
  assert.match(src, /if \(staleManagerYields\(`respawning kernel '\$\{spec\.id\}'`\)\) return;[^\n]*\n\s*spawnKernel\(spec\);/, 'the crash respawn consults it');
  assert.match(src, /if \(staleManagerYields\(`restarting kernel '\$\{kid\}'`\)\) return json\(200, \{ ok: true, restarted: kid, managerRestart: true \}\);\s*return restartKernel\(kid\)/, '/restart consults it');
});
