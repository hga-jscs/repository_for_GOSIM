"""Public incremental-persistence contract: existing PRs survive initial migration."""

import sqlite3
import time
from pathlib import Path

import pytest

from server import application_server
from tools import WorkspaceTools


@pytest.mark.requirements("REQ-6-1", "REQ-6-5")
def test_evolution_github_existing_pull_and_rule_survive_migration(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "existing-pulls.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    with application_server(workspace, environment, time.monotonic() + 90) as (_, ready, output):
        assert ready, output
    with sqlite3.connect(database) as connection:
        repository = connection.execute("SELECT r.id FROM repositories r JOIN organizations o ON o.id=r.organization_id WHERE o.name='acme-demo' AND r.name='acme-docs'").fetchone()[0]
        identifier = connection.execute("SELECT id FROM pull_requests WHERE repository_id=? AND number=1", [repository]).fetchone()[0]
        connection.execute("UPDATE pull_requests SET title='User authored proposal',description='Preserve this original discussion',status='Closed' WHERE id=?", [identifier])
        connection.execute("UPDATE branch_protection_rules SET require_approval=0,status_checks='[]' WHERE repository_id=? AND pattern='main'", [repository])
        original = connection.execute("SELECT number,title,description,status,author_account_id,base_branch_id,compare_branch_id,current_commit_id,creation_commit_id,base_commit_id,base_snapshot FROM pull_requests WHERE id=?", [identifier]).fetchone()
        rule = connection.execute("SELECT id,pattern,require_approval,status_checks FROM branch_protection_rules WHERE repository_id=? AND pattern='main'", [repository]).fetchone()
        assert original and rule
        # A populated pre-evolution database has no new seed-version registry.
        # The existing shared migration check uses the same versionless state.
        connection.execute("DROP TABLE seed_versions")
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (_, ready, output):
            assert ready, output
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT number,title,description,status,author_account_id,base_branch_id,compare_branch_id,current_commit_id,creation_commit_id,base_commit_id,base_snapshot FROM pull_requests WHERE id=?", [identifier]).fetchone() == original
                assert connection.execute("SELECT id,pattern,require_approval,status_checks FROM branch_protection_rules WHERE repository_id=? AND pattern='main'", [repository]).fetchone() == rule
