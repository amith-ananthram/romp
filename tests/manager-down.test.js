'use strict';
// The `romp down` marker as the manager sees it. bin/romp writes STATE/down-by-romp when it stops
// the kernel on purpose; here two consumers: `ensure` (the supervised start that the far-host
// scripts of `romp update <host>` and the dashboard's remote restart run) must refuse to bring the
// manager back while the marker exists, since it is the one door that would undo a deliberate stop
// with nobody asking, and a deliberate `up` clears it. Then shutdownAll's half of `romp down`: a
// kernel that ignores SIGTERM is SIGKILLed when the grace runs out, so it never outlives its
// manager under a marker that says down, and the same escalation under a restart, so it never
// keeps its sessions on stale code. Run from the repo root:
//   node --test tests/manager-*.test.js
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const net = require('net');
const { spawn } = require('child_process');

const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'romp-down-marker-'));
after(() => fs.rmSync(stateDir, { recursive: true, force: true }));   // one leaked dir per run otherwise
process.env.ROMP_STATE_DIR = stateDir;       // baked at require time (STATE_ROOT)
delete process.env.ROMP_SUPERVISED;
const { ensureDecision, downMarkerHeld, clearDownMarker, stopTrigger, DOWN_MARKER } =
  require(path.join(__dirname, '..', 'bin', 'romp-manager'));

test('the marker lives under the state root as down-by-romp, the path bin/romp and romp-service write', () => {
  assert.equal(DOWN_MARKER, path.join(stateDir, 'down-by-romp'));
});

test('ensure: a live manager is a no-op, a held marker refuses, otherwise spawn', () => {
  assert.equal(ensureDecision({ alive: true, held: false }), 'noop');
  assert.equal(ensureDecision({ alive: true, held: true }), 'noop', 'a running manager outranks a stale marker');
  assert.equal(ensureDecision({ alive: false, held: true }), 'held', 'down on purpose: auto-start must not undo it');
  assert.equal(ensureDecision({ alive: false, held: false }), 'spawn');
});

test('downMarkerHeld reads the file; clearDownMarker removes it and reports whether there was one', () => {
  const f = path.join(stateDir, 'down-by-romp');
  assert.equal(downMarkerHeld(f), false);
  assert.equal(clearDownMarker(f), false, 'nothing to clear says so: the start log stays quiet');
  fs.writeFileSync(f, JSON.stringify({ t: 1700000000, cmd: 'romp down' }) + '\n');
  assert.equal(downMarkerHeld(f), true);
  assert.equal(clearDownMarker(f), true, 'a deliberate start clears the marker');
  assert.equal(fs.existsSync(f), false);
  assert.equal(downMarkerHeld(f), false);
});

// The trigger the manager's stop notes carry (auditSigterm, before every SIGTERM of a stop): read off
// the marker at stop time, so a stop `romp down` asked for is legible as the CLI's on the ledger and a
// stop nothing labeled (a hand on systemctl, Ctrl+C, POST /stop) stays `stop`.
test('stopTrigger reads the marker at stop time: cli-down while it is on disk, stop otherwise', () => {
  assert.equal(stopTrigger(), 'stop');
  fs.writeFileSync(DOWN_MARKER, JSON.stringify({ t: 1700000000, cmd: 'romp down' }) + '\n');
  try {
    assert.equal(stopTrigger(), 'cli-down', 'the CLI asked for this stop');
  } finally {
    clearDownMarker();
  }
  assert.equal(stopTrigger(), 'stop', 'nothing cached: the marker is read at every stop');
});

test('startManager clears the marker before the tmux server starts (source pin: a deliberate start brings the kernels back)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'bin', 'romp-manager'), 'utf8');
  const body = src.slice(src.indexOf('function startManager() {'));
  const clear = body.indexOf('clearDownMarker()');
  const tmux = body.indexOf('startTmuxServer();');
  assert.ok(clear > 0 && tmux > 0 && clear < tmux, 'the clear runs before startTmuxServer()');
});

// Two free loopback ports, fresh per case, never a literal: a literal pair once collided with another
// suite's control port, where a concurrent run's manager answered the probe.
function freePorts(n) {
  return Promise.all(Array.from({ length: n }, () => new Promise((resolve, reject) => {
    const s = net.createServer();
    s.on('error', reject);
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); });
  })));
}

// The escalation cases below run a REAL manager with a stand-in kernel that swallows SIGTERM;
// ROMP_SHUTDOWN_GRACE_MS shortens the 5s grace so each stays bounded. PATH holds only a no-op tmux,
// so startTmuxServer() never reaches the machine's server (tests/tmux-private.bash has the incident),
// and the socket directory is private too. The stand-in writes its pid to the ready file once its
// handler is installed (a SIGTERM before that would simply kill it), so a respawn is told apart from
// the kernel it replaced. Every wait below is for an EVENT (the manager's exit, a kernel's death, a
// fresh kernel reporting ready), bounded only by the test's own timeout.
function stubbornManager(managerPort, servePort) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'romp-stubborn-'));
  const bin = path.join(dir, 'bin');
  for (const d of [bin, path.join(dir, 'tmux'), path.join(dir, 'state')]) fs.mkdirSync(d);
  fs.writeFileSync(path.join(bin, 'tmux'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  const ready = path.join(dir, 'kernel-ready');
  const serve = path.join(dir, 'fake-serve');
  fs.writeFileSync(serve, `#!/bin/sh\nexec "${process.execPath}" -e "process.on('SIGTERM', () => {}); require('fs').writeFileSync(process.env.ROMP_TEST_READY, String(process.pid)); setInterval(() => {}, 1000)"\n`, { mode: 0o755 });
  const env = Object.assign({}, process.env, {
    PATH: bin, TMUX_TMPDIR: path.join(dir, 'tmux'), ROMP_CLI_SCOPE: '0',
    ROMP_STATE_DIR: path.join(dir, 'state'), ROMP_MANAGER_PORT: String(managerPort), ROMP_SERVE_PORT: String(servePort),
    ROMP_SERVE_BIN: serve, ROMP_SHUTDOWN_GRACE_MS: '1000', ROMP_TEST_READY: ready,
  });
  delete env.ROMP_SUPERVISED;
  const mgr = spawn(process.execPath, [path.join(__dirname, '..', 'bin', 'romp-manager'), 'up'], { env, stdio: ['ignore', 'ignore', 'pipe'] });
  // pids: every kernel the manager reported, reaped at cleanup; stateDir: the manager's state root, where
  // its stop notes (restart-audit.jsonl) and a `romp down` marker live
  const h = { mgr, log: '', pids: new Set(), stateDir: path.join(dir, 'state') };
  mgr.stderr.on('data', (d) => { h.log += d; });
  h.exited = new Promise((resolve) => mgr.on('exit', (code, sig) => resolve({ code, sig })));
  h.sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  h.req = (p, method) => new Promise((resolve, reject) => {
    const r = http.request({ host: '127.0.0.1', port: managerPort, path: p, method, timeout: 2000 }, (res) => {
      let b = ''; res.on('data', (d) => (b += d)); res.on('end', () => resolve({ code: res.statusCode, body: b }));
    });
    r.on('error', reject); r.on('timeout', () => { r.destroy(); reject(new Error('timeout')); }); r.end();
  });
  h.alive = (pid) => { try { process.kill(pid, 0); return true; } catch (e) { return false; } };
  h.gone = async (pid) => { for (let i = 0; i < 60 && h.alive(pid); i++) await h.sleep(100); return !h.alive(pid); };
  // the kernel the manager reports, other than `not`, once its handler is installed
  h.readyKernel = async (not) => {
    for (let i = 0; i < 100; i++) {
      let kpid = 0;
      try { kpid = ((JSON.parse((await h.req('/status', 'GET')).body).kernels || [])[0] || {}).pid || 0; } catch (e) { /* not yet */ }
      if (kpid && kpid !== not) h.pids.add(kpid);
      if (kpid && kpid !== not && fs.existsSync(ready) && fs.readFileSync(ready, 'utf8') === String(kpid)) return kpid;
      await h.sleep(100);
    }
    assert.fail(`the manager never reported a kernel${not ? ` other than ${not}` : ''} with its SIGTERM handler installed: ${h.log}`);
  };
  h.cleanup = () => {
    try { mgr.kill('SIGKILL'); } catch (e) { /* gone */ }
    for (const p of h.pids) { try { process.kill(p, 'SIGKILL'); } catch (e) { /* gone */ } }
    fs.rmSync(dir, { recursive: true, force: true });
  };
  return h;
}

// shutdownAll's escalation: the manager used to exit 800ms after one SIGTERM, so a kernel that
// ignored the signal outlived it, still holding its port and its sessions under a `romp down`
// marker that said otherwise. The stop runs under the marker, the way `romp down` leaves it (bin/romp
// writes the marker before it stops the service), so the stop note the manager appends before its
// SIGTERM is the wired one: trigger `cli-down`, which the kernel's exit reader answers with.
test('shutdownAll: a kernel that ignores SIGTERM is SIGKILLed when the grace runs out, the manager still exits, and its stop note says cli-down under the marker', async () => {
  const [mp, sp] = await freePorts(2);
  const h = stubbornManager(mp, sp);
  try {
    const kpid = await h.readyKernel();
    assert.ok(h.alive(kpid));                               // alive before the stop
    fs.writeFileSync(path.join(h.stateDir, 'down-by-romp'), JSON.stringify({ t: 1700000000, cmd: 'romp down' }) + '\n');
    assert.equal((await h.req('/stop', 'POST')).code, 200);
    const outcome = await Promise.race([h.exited, h.sleep(8000).then(() => null)]);
    assert.ok(outcome !== null, `the manager was still running after /stop: ${h.log}`);
    assert.match(h.log, /pid \d+\) still running 1000ms after SIGTERM; sending SIGKILL/, `the escalation is on the manager log: ${h.log}`);
    assert.equal(await h.gone(kpid), true, `the kernel stand-in (pid ${kpid}) survived the manager`);   // gone, not merely signalled: SIGKILL cannot be ignored
    assert.match(h.log, /shutting down: stopping all kernels \(cli-down\)/, `the stop names its trigger once: ${h.log}`);
    const rows = fs.readFileSync(path.join(h.stateDir, 'restart-audit.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l));
    const notes = rows.filter((r) => r.action === 'manager-sigterm');
    assert.equal(notes.length, 1, JSON.stringify(rows));
    assert.deepEqual({ kernel: notes[0].kernel, pid: notes[0].pid, reason: notes[0].reason, trigger: notes[0].trigger },
      { kernel: 'main', pid: kpid, reason: 'stop', trigger: 'cli-down' }, 'the note before the SIGTERM says the CLI asked');
  } finally {
    h.cleanup();
  }
});

// restartKernel's escalation: a kernel that ignored SIGTERM under `romp refresh` (restart-all), the
// dashboard's restart or the quiet-window apply was never respawned; it kept its port and its
// sessions on stale code, and the manager logged nothing. Both paths share one helper, and for a
// restart the exit handler's respawn follows the SIGKILL.
test('restartKernel: a kernel that ignores SIGTERM is SIGKILLed when the grace runs out, logged and respawned, and the manager stays up', async () => {
  const [mp, sp] = await freePorts(2);
  const h = stubbornManager(mp, sp);
  try {
    const k1 = await h.readyKernel();
    const r = await h.req('/restart-all', 'POST');          // the `romp refresh` door
    assert.equal(r.code, 200, r.body);
    const restarted = JSON.parse(r.body).restarted;
    assert.equal(restarted.length, 1, r.body);
    const k2 = await h.readyKernel(k1);                     // a fresh kernel, not the one that owed the exit
    assert.match(h.log, new RegExp(`kernel '${restarted[0]}' \\(pid ${k1}\\) still running 1000ms after SIGTERM; sending SIGKILL`), `the escalation is on the manager log: ${h.log}`);
    assert.match(h.log, /sig=SIGKILL\).*respawning in/, `the exit handler respawned it: ${h.log}`);
    assert.equal(await h.gone(k1), true, `the old kernel (pid ${k1}) is still there`);
    assert.ok(h.alive(k2), `the new kernel (pid ${k2}) is not running`);
    assert.equal(h.mgr.exitCode, null, `the manager exited on a restart: ${h.log}`);
    assert.equal((await h.req('/status', 'GET')).code, 200);
  } finally {
    h.cleanup();
  }
});
