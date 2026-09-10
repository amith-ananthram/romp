// A kernel exit the manager did not ask for is the one worth a line: a SIGTERM that reaches the
// kernel pid directly is respawned with the same "exited ... respawning" line as a requested
// restart, and nothing says the restart was unrequested (the respawn can land on a different python
// and take every SDK session down). exitLine is the pure decision: stopped / requested / unrequested,
// and the unrequested case says so. auditSigterm is the manager's own note in restart-audit.jsonl
// before each SIGTERM it sends, so the kernel's "no request on record" verdict is true when it fires.
// The wiring is executed too: the SIGTERM doors with a stand-in child, and spawnKernel's exit handler
// and the stale-manager self-bounce in child node processes.
// Run: node --test tests/manager-exit-attribution.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

// Every temp root this file makes is removed when the process exits (the child-process cases below
// write under roots made here, so theirs go too).
const ROOTS = [];
const tmpRoot = (prefix) => { const d = fs.mkdtempSync(path.join(os.tmpdir(), prefix)); ROOTS.push(d); return d; };
process.on('exit', () => { for (const d of ROOTS) fs.rmSync(d, { recursive: true, force: true }); });

// STATE_ROOT bakes from env when the manager module loads: point it at a temp dir BEFORE the require.
const STATE = tmpRoot('romp-mgr-exit-');
process.env.ROMP_STATE_DIR = STATE;
const MGR = path.join(__dirname, '..', 'bin', 'romp-manager');
const { exitLine, auditSigterm, restartKernel, restartAll, stopKernel, stopTrigger, kernels } = require(MGR);

const rec = (over) => Object.assign({ spec: { id: 'main', port: 29855 }, child: { pid: 4242 },
  restarts: 3, stopping: false, requested: null }, over);

test('a stopped kernel (stop or shutdown) is reaped, not respawned: the line says stopped', () => {
  const l = exitLine(rec({ stopping: true }), 0, null, 0);
  assert.equal(l.respawn, false);
  assert.match(l.line, /stopped/);
});

test('a kernel the manager SIGTERMed for a restart keeps the plain respawn line', () => {
  const l = exitLine(rec({ requested: 1000 }), null, 'SIGTERM', 0);
  assert.equal(l.respawn, true);
  assert.match(l.line, /respawning in 0ms/);
  assert.doesNotMatch(l.line, /without a restart request/);
});

test('a kernel that exited with no request from the manager says so: signal or crash', () => {
  const l = exitLine(rec(), null, 'SIGTERM', 1000);
  assert.equal(l.respawn, true);
  assert.match(l.line, /kernel 'main' \(pid 4242\) exited without a restart request \(signal or crash\); respawning in 1000ms/);
  assert.match(l.line, /sig=SIGTERM/);
});

test('a crash (exit code, no signal) reads the same way: the manager did not ask', () => {
  const l = exitLine(rec(), 1, null, 2000);
  assert.equal(l.respawn, true);
  assert.match(l.line, /without a restart request/);
  assert.match(l.line, /code=1/);
});

test('auditSigterm appends the manager\'s own row to the primary state root by default', () => {
  const t = auditSigterm({ id: 'main', port: 29855 }, 4242, 'restart');
  const rows = fs.readFileSync(path.join(STATE, 'restart-audit.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  const r = rows[rows.length - 1];
  assert.equal(r.action, 'manager-sigterm');
  assert.equal(r.kernel, 'main');
  assert.equal(r.pid, 4242);
  assert.equal(r.reason, 'restart');
  assert.equal(r.trigger, 'restart');      // no trigger given: the reason stands in
  assert.equal(typeof r.t, 'number');
  assert.equal(r.t, t);
});

test('an aux kernel\'s row goes to ITS state root (the kernel reads the audit file under its own root)', () => {
  const aux = tmpRoot('romp-mgr-exit-aux-');
  auditSigterm({ id: 'aux', port: 29900, stateDir: aux }, 7, 'stop');
  const rows = fs.readFileSync(path.join(aux, 'restart-audit.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(rows[0].kernel, 'aux');
  assert.equal(rows[0].reason, 'stop');
});

test('auditSigterm never throws: an unwritable root is a lost note, not a lost restart', () => {
  assert.doesNotThrow(() => auditSigterm({ id: 'x', port: 1, stateDir: path.join(STATE, 'no', 'such', 'file.txt', 'dir') }, 1, 'restart'));
});

// The wiring: the row goes on disk BEFORE the signal, and the record says what was asked. The pure
// functions above would stay green if restartKernel moved its auditSigterm below the kill, dropped
// `rec.requested`, or stopKernel lost its row; on a live machine each of those makes the kernel's "no
// request on record" verdict false for every manager kill. A stand-in child records the audit rows as
// they stood at the moment of each kill, so "row before signal" is asserted on ORDER.
const auditRows = (root) => {
  try { return fs.readFileSync(path.join(root, 'restart-audit.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse); }
  catch (e) { return []; }
};
function seed(id, spec, pid) {
  const root = spec.stateDir || STATE;
  const child = { pid, kills: [] };
  child.kill = (sig) => child.kills.push({ sig, rowsAtKill: auditRows(root) });
  const r = { spec, child, restarts: 0, quickCrashes: 0, startedAt: Date.now(), stopping: false, requested: null };
  kernels.set(id, r);
  return r;
}

test('restartKernel: the row is on disk when the SIGTERM goes out, and the exit is marked requested', () => {
  const before = auditRows(STATE).length;
  const r = seed('main', { id: 'main', port: 29855 }, 4242);
  try {
    assert.equal(restartKernel('main'), true);
    assert.deepEqual(r.child.kills.map((k) => k.sig), ['SIGTERM']);
    const seen = r.child.kills[0].rowsAtKill;
    assert.equal(seen.length, before + 1);                    // written before, not after, the kill
    const row = seen[seen.length - 1];
    assert.deepEqual([row.action, row.kernel, row.pid, row.reason, row.trigger],
      ['manager-sigterm', 'main', 4242, 'restart', 'restart']);
    assert.equal(typeof r.requested, 'number');               // exitLine: asked for, not a stray signal
    assert.equal(r.stopping, false);                          // ...and respawned, not reaped
  } finally { kernels.delete('main'); }
});

test('restartAll (romp refresh) names its trigger on every kernel, each row under that kernel\'s own root', () => {
  const aux = tmpRoot('romp-mgr-exit-aux2-');
  const m = seed('main', { id: 'main', port: 29855 }, 1);
  const a = seed('aux', { id: 'aux', port: 29900, stateDir: aux }, 2);
  try {
    assert.deepEqual(restartAll().sort(), ['aux', 'main']);
    const mrow = m.child.kills[0].rowsAtKill.pop();
    const arow = a.child.kills[0].rowsAtKill.pop();
    assert.deepEqual([mrow.kernel, mrow.reason, mrow.trigger], ['main', 'restart', 'restart-all']);
    assert.deepEqual([arow.kernel, arow.reason, arow.trigger], ['aux', 'restart', 'restart-all']);
    assert.equal(auditRows(aux).length, 1);                   // the aux row went to the aux root only
  } finally { kernels.delete('main'); kernels.delete('aux'); }
});

test('stopKernel: the stop row precedes the SIGTERM, the record is marked stopping, the trigger is "stop"', () => {
  const r = seed('main', { id: 'main', port: 29855 }, 4243);
  try {
    assert.equal(stopKernel('main'), true);
    assert.equal(r.stopping, true);
    const row = r.child.kills[0].rowsAtKill.pop();
    assert.deepEqual([row.action, row.pid, row.reason, row.trigger], ['manager-sigterm', 4243, 'stop', 'stop']);
  } finally { kernels.delete('main'); }
});

test('stopTrigger names a stop nothing else explains: stop (a service stop by hand, Ctrl+C, POST /stop)', () => {
  assert.equal(stopTrigger(), 'stop');
  const r = seed('main', { id: 'main', port: 29855 }, 4244);
  try {
    stopKernel('main', 'refresh');                            // a caller that knows better passes its own
    assert.equal(r.child.kills[0].rowsAtKill.pop().trigger, 'refresh');
  } finally { kernels.delete('main'); }
});

// A child node process for the paths that end the process or need the real module state: the manager
// module under test is required there (the file itself, or a copy of it), a stand-in kernel is seeded,
// and the child is allowed to exit on its own. `script` runs with ROMP_STATE_DIR at a fresh root and
// the kernel and control ports poisoned, so nothing here reaches a live manager or kernel.
function inChild(script, extraEnv) {
  const root = tmpRoot('romp-mgr-exit-child-');
  const env = Object.assign({}, process.env, { ROMP_STATE_DIR: root, ROMP_SERVE_PORT: '1', ROMP_MANAGER_PORT: '1' }, extraEnv || {});
  delete env.XDG_STATE_HOME;
  const r = spawnSync(process.execPath, ['-e', script], { env, encoding: 'utf8', timeout: 15000 });
  assert.equal(r.status, 0, r.stderr);
  return { root, stderr: r.stderr, rows: auditRows(root) };
}
const standIn = `
  const fs = require('fs'), path = require('path');
  const root = process.env.ROMP_STATE_DIR;
  const rowsNow = () => { try { return fs.readFileSync(path.join(root, 'restart-audit.jsonl'), 'utf8').trim().split('\\n').filter(Boolean).length; } catch (e) { return 0; } };
  const child = { pid: 777, kill(sig) { fs.appendFileSync(path.join(root, 'kills.txt'), sig + ' rows=' + rowsNow() + '\\n'); } };
  m.kernels.set('main', { spec: { id: 'main', port: 1 }, child, restarts: 0, quickCrashes: 0, startedAt: Date.now(), stopping: false, requested: null });
`;
const killsOf = (root) => fs.readFileSync(path.join(root, 'kills.txt'), 'utf8').trim().split('\n');

// shutdownAll exits the process (after 800 ms) and latches, so it runs in a child: the real path, no stubbing.
function shutdownCase(trigger) {
  const { root, rows } = inChild(`
    const m = require(${JSON.stringify(MGR)});
    ${standIn}
    m.shutdownAll(0${trigger ? ', ' + JSON.stringify(trigger) : ''});
  `);
  return { rows, kills: killsOf(root) };
}

test('shutdownAll (a service stop by hand, Ctrl+C, POST /stop): every kernel\'s stop row says stop, before the kill', () => {
  const { rows, kills } = shutdownCase();
  assert.equal(rows.length, 1);
  assert.deepEqual([rows[0].action, rows[0].reason, rows[0].trigger, rows[0].pid], ['manager-sigterm', 'stop', 'stop', 777]);
  assert.deepEqual(kills, ['SIGTERM rows=1']);               // the row was there when the signal went out
});

test('shutdownAll with a trigger of the caller\'s labels every row with it', () => {
  const { rows } = shutdownCase('refresh');
  assert.deepEqual([rows[0].reason, rows[0].trigger], ['stop', 'refresh']);
});

test('the stale-manager self-bounce (restartAllOrSelf under a supervisor) stops its kernels with trigger refresh', () => {
  // managerStale() compares the file's stamp now with the one taken at load, so the module under test
  // is a COPY of bin/romp-manager whose mtime this test moves after the require: what a deploy does to
  // the real file while a supervised manager runs. The copy has no relative requires, so it loads as
  // the original does.
  const copyRoot = tmpRoot('romp-mgr-exit-stale-');
  const copy = path.join(copyRoot, 'romp-manager');
  fs.copyFileSync(MGR, copy);
  const { root, rows } = inChild(`
    const m = require(${JSON.stringify(copy)});
    ${standIn}
    const past = new Date(Date.now() - 60000);
    fs.utimesSync(${JSON.stringify(copy)}, past, past);
    const verdict = m.restartAllOrSelf();
    fs.writeFileSync(path.join(root, 'verdict.json'), JSON.stringify(verdict));
  `, { ROMP_SUPERVISED: '1' });
  const verdict = JSON.parse(fs.readFileSync(path.join(root, 'verdict.json'), 'utf8'));
  assert.deepEqual(verdict, { self: true, ids: ['main'] });   // it exited for the supervisor's respawn
  assert.equal(rows.length, 1);
  assert.deepEqual([rows[0].action, rows[0].reason, rows[0].trigger, rows[0].pid], ['manager-sigterm', 'stop', 'refresh', 777]);
  assert.deepEqual(killsOf(root), ['SIGTERM rows=1']);
});

// spawnKernel's exit handler is where exitLine's verdict reaches the log, and the pure cases above stay
// green if the handler goes back to one fixed line. A child node runs the real spawnKernel with
// ROMP_SERVE_BIN pointed at a stand-in launcher that exits at once, and reports every line the handler
// logged for the kernel; the child exits on the first such line, before the respawn the handler
// schedules is due.
function exitCase(mark) {
  const stubRoot = tmpRoot('romp-mgr-exit-stub-');
  const stub = path.join(stubRoot, 'romp-serve');
  fs.writeFileSync(stub, '#!/bin/sh\nexit 1\n', { mode: 0o755 });
  const { stderr } = inChild(`
    const m = require(${JSON.stringify(MGR)});
    const lines = [];
    const write = process.stderr.write.bind(process.stderr);
    process.stderr.write = (s, ...rest) => { lines.push(String(s)); return write(s, ...rest); };
    m.spawnKernel({ id: 'main', port: 1 });
    ${mark === 'requested' ? "m.kernels.get('main').requested = Date.now();" : ''}
    ${mark === 'stopping' ? "m.kernels.get('main').stopping = true;" : ''}
    const poll = setInterval(() => {
      if (lines.some((l) => /kernel 'main'.*(exited|stopped)/.test(l))) { clearInterval(poll); process.exit(0); }
    }, 20);
    setTimeout(() => process.exit(3), 12000);
  `, { ROMP_SERVE_BIN: stub });
  return stderr.split('\n').filter((l) => /\[romp-manager\] kernel 'main'.*(exited|stopped)/.test(l));
}

test('the exit handler logs an exit nobody asked for as such (a launcher that dies at once, nothing requested)', () => {
  const lines = exitCase('unrequested');
  assert.equal(lines.length, 1, lines.join('\n'));
  assert.match(lines[0], /kernel 'main' \(pid \d+\) exited without a restart request \(signal or crash\); respawning in \d+ms \(code=1 sig=-\) \[#1\]/);
});

test('the exit handler keeps the plain line for an exit restartKernel asked for', () => {
  const lines = exitCase('requested');
  assert.equal(lines.length, 1, lines.join('\n'));
  assert.match(lines[0], /kernel 'main' exited \(code=1 sig=-\); respawning in \d+ms \[#1\]/);
  assert.doesNotMatch(lines[0], /without a restart request/);
});

test('the exit handler reaps a kernel marked stopping with the stopped line', () => {
  const lines = exitCase('stopping');
  assert.equal(lines.length, 1, lines.join('\n'));
  assert.match(lines[0], /kernel 'main' stopped$/);
});
