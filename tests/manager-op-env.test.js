// romp-manager's environment gate (2026-09-08): romp holds no API key and runs no secret-manager CLI, so the
// manager hands every kernel it spawns its environment as it stands (specEnv copies it; the provider-gated
// scrub is retired with the providers). What guards the kernels and their sessions instead is the refusal to
// start at all while the environment carries a retired provider variable: the manager is the door service.env
// comes in through, and a leftover ANTHROPIC_API_KEY there would reach every kernel, which alone refuses to
// boot on it. Synthetic values throughout.
// Run: node --test tests/manager-*.test.js
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

process.env.ROMP_STATE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'romp-mgr-op-env-'));
const mgr = require(path.join(__dirname, '..', 'bin', 'romp-manager'));
const { retiredCredentialNames, retiredCredentialMessage } = mgr;

const ENV = { PATH: '/usr/bin', HOME: '/nonexistent', ANTHROPIC_AUTH_TOKEN: 'synthetic-bearer', ROMP_EXPECTED_AUTH: 'key' };

test('the manager scrubs nothing and reads no provider file: the retired helpers are gone', () => {
  assert.equal(mgr.serviceEnvHasRef, undefined, 'the provider-file reader is retired with the providers');
  assert.equal(mgr.withoutOpCredentials, undefined, 'the scrub that became a plain copy went with the last process it fed');
});

test('a retired provider variable in the environment is named, never valued, and refuses the start', () => {
  assert.deepEqual(retiredCredentialNames(ENV), [], 'the declaration and a login token are not retired names');
  for (const name of ['ROMP_API_KEY_CMD', 'ROMP_API_KEY_REF', 'ANTHROPIC_API_KEY']) {
    const found = retiredCredentialNames({ ...ENV, [name]: 'synthetic-value-never-printed' });
    assert.deepEqual(found, [name]);
    const msg = retiredCredentialMessage(found);
    assert.match(msg, /did NOT start/);
    assert.match(msg, new RegExp(name));
    assert.match(msg, /apiKeyHelper/);
    assert.match(msg, /ROMP_EXPECTED_AUTH=key/);
    assert.doesNotMatch(msg, /synthetic-value/, 'names and files only, never a value');
  }
  assert.deepEqual(retiredCredentialNames({ ...ENV, ANTHROPIC_API_KEY: '', ROMP_API_KEY_REF: 'op://v/i/f' }),
                   ['ROMP_API_KEY_REF', 'ANTHROPIC_API_KEY'], 'an empty value is still the variable');
});

test('the 1Password CLI names are refused like the provider variables, and the message says why', () => {
  const clean = { PATH: '/usr/bin', HOME: '/nonexistent', ROMP_EXPECTED_AUTH: 'key' };
  assert.deepEqual(retiredCredentialNames(clean), []);
  const found = retiredCredentialNames({ ...clean, OP_SERVICE_ACCOUNT_TOKEN: 'synthetic-op-token', OP_SESSION_acct: 'synthetic-session' });
  assert.deepEqual(found, ['OP_SERVICE_ACCOUNT_TOKEN', 'OP_SESSION_acct']);
  const msg = retiredCredentialMessage(found);
  assert.match(msg, /did NOT start/);
  assert.match(msg, /OP_SERVICE_ACCOUNT_TOKEN/);
  assert.match(msg, /no longer runs op/);
  assert.doesNotMatch(msg, /synthetic-/, 'names only');
});

test('startManager refuses before it opens the control port (source pin: the check precedes every start)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'bin', 'romp-manager'), 'utf8');
  const body = src.slice(src.indexOf('function startManager() {'));
  const check = body.indexOf('retiredCredentialNames(process.env)');
  const server = body.indexOf('http.createServer(');
  assert.ok(check > 0 && server > 0 && check < server, 'the refusal runs before the control server is created');
  assert.ok(body.slice(check, server).includes('process.exit(1)'), 'and it exits rather than warns');
});
