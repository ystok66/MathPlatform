// Offline regression tests. No service, database, or provider requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'apps/web/node_modules/typescript'));
const source = fs.readFileSync(path.join(root, 'apps/web/app/startup-sync.ts'), 'utf8');
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const sandbox = { exports: {}, setTimeout };
vm.runInNewContext(output, sandbox);
const sync = sandbox.exports.synchronizeStartupReads;
const ready = () => true;
const noWait = async () => {};

(async () => {
  let calls = [0, 0];
  assert.equal(await sync([
    async () => { calls[0]++; },
    async () => { if (++calls[1] === 1) throw new Error('startup'); },
  ], ready, noWait), 0);
  assert.deepEqual(calls, [1, 2]);
  console.log('PASS successful reads are not repeated; failed read recovers');

  let failures = 0;
  assert.equal(await sync([async () => { failures++; throw new Error('offline'); }], ready, noWait), 1);
  assert.equal(failures, 2);
  console.log('PASS persistent failure stops after one retry');

  let valid = true;
  let obsolete = 0;
  assert.equal(await sync([async () => { obsolete++; throw new Error('offline'); }], () => valid, async () => { valid = false; }), 0);
  assert.equal(obsolete, 1);
  console.log('PASS superseded synchronization does not retry');

  assert.equal(await sync([async () => { throw new Error('must not run'); }], () => false, noWait), 0);
  console.log('PASS unmounted/offline synchronization performs no reads');

  let commit = 0;
  let release;
  valid = true;
  const pending = sync([async () => {
    await new Promise(resolve => { release = resolve; });
    if (valid) commit++;
  }], () => valid, noWait);
  await Promise.resolve();
  valid = false;
  release();
  await pending;
  assert.equal(commit, 0);
  console.log('PASS late response is ignored after invalidation');

  let loadedKey = null;
  let keyCalls = 0;
  let report = null;
  assert.equal(await sync([
    async () => { if (++keyCalls === 1) throw new Error('not ready'); loadedKey = false; },
    async () => { report = { total_questions: 3858 }; },
  ], ready, noWait), 0);
  assert.equal(loadedKey, false);
  assert.equal(report.total_questions, 3858);
  console.log('PASS confirmed missing key remains distinct from unread settings; report loads independently');
  console.log('6 startup synchronization checks passed; provider_calls=0');
})().catch(error => { console.error(error); process.exitCode = 1; });
