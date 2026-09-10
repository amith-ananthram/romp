// T304: the quiet window's own restart-audit.jsonl row, written when a parked deploy refresh APPLIES. Until
// now the wait from parked to restart and the backstop's firing lived in one manager log line; the restart
// monitors (`romp restart-metrics`) read this row instead. Pure shape first (quietWindowRow), then the append
// under a hermetic ROMP_STATE_DIR. Run: node --test tests/manager-quiet-window-audit.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const STATE = fs.mkdtempSync(path.join(os.tmpdir(), 'romp-mgr-quiet-'));
process.env.ROMP_STATE_DIR = STATE;                 // before the require: the manager binds STATE_ROOT at load
const { quietWindowRow, auditQuietWindow } = require(path.join(__dirname, '..', 'bin', 'romp-manager'));

test('quietWindowRow: the park and the verdict, in epoch seconds', () => {
  const park = { since: 1_700_000_000_000, count: 3, mode: 'all', lastInflight: 2, misses: 0,
                 drainRefusedCount: 1, drainArmedCount: 4, drainRefusedAfterArm: 0 };
  const row = quietWindowRow(park, { action: 'apply', reason: 'backstop cap' }, 1_700_000_900_000);
  assert.deepEqual(row, { t: 1_700_000_900, action: 'quiet-window', since: 1_700_000_000, waitedS: 900,
                          reason: 'backstop cap', backstop: true, coalesced: 3, mode: 'all', lastInflight: 2,
                          misses: 0, drainRefusedCount: 1, drainArmedCount: 4, drainRefusedAfterArm: 0 });
});

test('quietWindowRow: a quiet apply is not a backstop; unknown counts read as zero, not undefined', () => {
  const row = quietWindowRow({ since: 1_700_000_000_000 }, { reason: 'quiet' }, 1_700_000_012_000);
  assert.equal(row.backstop, false);
  assert.equal(row.waitedS, 12);
  assert.equal(row.coalesced, 1);
  assert.equal(row.lastInflight, null, 'never polled → null, distinguishable from zero in flight');
  assert.equal(row.drainRefusedCount, 0);
  assert.equal(JSON.parse(JSON.stringify(row)).misses, 0, 'every field survives JSON');
});

test('auditQuietWindow appends one JSON line to restart-audit.jsonl under the state root', () => {
  auditQuietWindow({ since: 1_700_000_000_000, count: 2, mode: 'main' }, { reason: 'quiet' }, 1_700_000_005_000);
  const lines = fs.readFileSync(path.join(STATE, 'restart-audit.jsonl'), 'utf8').trim().split('\n');
  assert.equal(lines.length, 1);
  const row = JSON.parse(lines[0]);
  assert.equal(row.action, 'quiet-window');
  assert.equal(row.waitedS, 5);
  assert.equal(row.mode, 'main');
});

test('auditQuietWindow never throws on an unwritable root (a lost note, not a lost restart)', () => {
  // the root is bound at load; make the FILE a directory so the append fails
  const bad = path.join(STATE, 'restart-audit.jsonl');
  fs.rmSync(bad); fs.mkdirSync(bad);
  assert.doesNotThrow(() => auditQuietWindow({ since: 1 }, { reason: 'quiet' }, 2000));
  fs.rmdirSync(bad);
});
