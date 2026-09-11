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
const { tmuxTmpdir } = require(path.join(__dirname, '..', 'bin', 'romp-manager'));

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

test('the operator\'s TMUX_TMPDIR wins as it stands, and nothing is made', () => {
  const fsi = fakeFs({ dirs: ['/run/user/1000'] });
  assert.equal(tmuxTmpdir({ env: { TMUX_TMPDIR: '/op/dir', XDG_RUNTIME_DIR: '/run/user/1000' }, fsi }), '/op/dir');
  assert.deepEqual(fsi.made, []);
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

test('startManager resolves it into process.env before starting the server, and says so', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'bin', 'romp-manager'), 'utf8');
  assert.match(src, /const tmuxDir = tmuxTmpdir\(\);\s*if \(tmuxDir\) \{ process\.env\.TMUX_TMPDIR = tmuxDir; log\(`tmux socket dir: \$\{tmuxDir\}/);
  assert.match(src, /log\('tmux socket dir: tmux default/);
  assert.ok(src.indexOf('const tmuxDir = tmuxTmpdir();') < src.indexOf('  startTmuxServer();\n  const server = http.createServer'), 'resolved BEFORE the server starts');
});
