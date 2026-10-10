"""A real SQLite rollback must not absorb an unrelated successful request."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.shared
def test_evolution_github_transaction_isolation_preserves_independent_writes(request, tmp_path):
    workspace = Path(request.config.getoption("--application")).resolve()
    script = r"""
const assert = require('node:assert/strict');
const db = require('./src/database/db_runtime');
const { closeDb } = require('./src/database/init_db');
(async () => {
  await db.exec('CREATE TABLE independent_write_check (value TEXT)');
  let entered, release;
  const started = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const transaction = db.withTransaction(async tx => {
    await tx.run('INSERT INTO independent_write_check VALUES (?)', ['rolled-back']);
    entered();
    await gate;
    throw new Error('intentional rollback');
  });
  const outcome = transaction.then(() => null, error => error.message);
  await started;
  const outside = db.run('INSERT INTO independent_write_check VALUES (?)', ['independent']);
  const premature = await Promise.race([outside.then(() => true), new Promise(resolve => setTimeout(() => resolve(false), 150))]);
  release();
  assert.equal(await outcome, 'intentional rollback');
  await outside;
  const rows = await db.all('SELECT value FROM independent_write_check');
  assert.deepEqual(rows, [{value:'independent'}], 'An unrelated acknowledged write must survive another request rollback');
  assert.equal(premature, false, 'Shared-connection helpers must not enter another request transaction');
})().then(closeDb, async error => { console.error(error.message); await closeDb(); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script], cwd=workspace / "backend", env=os.environ | {"ARC_DB_FILE": str(tmp_path / "isolation.db")}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.shared
def test_evolution_github_failed_seed_can_retry_without_partial_baseline(request, tmp_path):
    workspace = Path(request.config.getoption("--application")).resolve()
    script = r"""
const assert = require('node:assert/strict');
const db = require('./src/database/db_runtime');
const { closeDb } = require('./src/database/init_db');
const seed = require('./src/database/seed_db');
(async () => {
  await db.exec("CREATE TRIGGER seed_failure BEFORE INSERT ON organizations WHEN NEW.name='acme-demo' BEGIN SELECT RAISE(ABORT, 'intentional seed failure'); END;");
  let failed = false;
  try { await seed(); } catch (error) { failed = true; }
  assert.equal(failed, true, 'The real SQLite failure must reach seed initialization');
  assert.equal((await db.get('SELECT COUNT(*) AS n FROM accounts')).n, 0, 'Failed baseline seed must not leave accounts that cause the next startup to skip missing baseline data');
  await db.exec('DROP TRIGGER seed_failure');
  await seed();
  assert.equal((await db.get("SELECT COUNT(*) AS n FROM organizations WHERE name='acme-demo'")).n, 1);
  assert.equal((await db.get("SELECT COUNT(*) AS n FROM accounts WHERE username='alice-dev'")).n, 1);
})().then(closeDb, async error => { console.error(error.message); await closeDb(); process.exitCode = 1; });
"""
    result = subprocess.run(["node", "-e", script], cwd=workspace / "backend", env=os.environ | {"ARC_DB_FILE": str(tmp_path / "seed-retry.db")}, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
