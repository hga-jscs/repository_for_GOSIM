"""Actual API/SQLite PR review, branch integration and rollback invariants from REQ-6."""

import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest

from evolution_github_helpers import data
from test_evolution_github_storage import create_user
from server import application_server
from tools import WorkspaceTools


@pytest.fixture
def proposal(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "review.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output), ExitStack() as stack:
        assert ready, output
        clients = {role: stack.enter_context(httpx.Client(base_url=env["BASE_URL"], timeout=15)) for role in ("Admin", "Write", "Maintain", "Read", "Guest")}
        prefix = "pr-" + uuid.uuid4().hex[:8]
        for role in ("Admin", "Write", "Maintain", "Read"):
            create_user(clients[role], prefix + "-" + role.lower())
        owner = prefix + "-admin"
        root = f"/api/repos/{owner}/review"
        data(clients["Admin"].post("/api/repos", json={"owner": owner, "name": "review", "visibility": "public", "with_readme": True}))
        with sqlite3.connect(database) as connection:
            repository = connection.execute("SELECT r.id FROM repositories r JOIN accounts a ON a.id=r.owner_account_id WHERE a.username=? AND r.name='review'", [owner]).fetchone()[0]
            for role in ("Write", "Maintain", "Read"):
                account = connection.execute("SELECT id FROM accounts WHERE username=?", [prefix + "-" + role.lower()]).fetchone()[0]
                connection.execute("INSERT INTO repo_permissions(repository_id,account_id,role) VALUES (?,?,?)", [repository, account, role])
        data(clients["Write"].post(root + "/branches", json={"name": "feature", "base": "main"}))
        feature_commit = data(clients["Write"].post(root + "/files", json={"branch": "feature", "path": "feature.txt", "content": "Feature change\n", "message": "Feature commit"}))["commitId"]
        created = data(clients["Write"].post(root + "/pulls", json={"base": "main", "compare": "feature", "title": "Independent proposal"}))
        yield {"clients": clients, "root": root, "pull": root + "/pulls/" + str(created["number"]), "database": database, "repository": repository, "prefix": prefix, "feature_commit": feature_commit}


def branch_state(proposal):
    with sqlite3.connect(proposal["database"]) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} WHERE repository_id=? ORDER BY rowid", [proposal["repository"]]).fetchall() for table in ("files", "branches", "commits", "pull_requests")}


def protect(proposal, approval, checks):
    data(proposal["clients"]["Admin"].post(proposal["root"] + "/settings/branch-protection", json={"pattern": "main", "require_approval": approval, "status_checks": ["test"] if checks else []}))


def approve(proposal, decision="APPROVED"):
    data(proposal["clients"]["Admin"].post(proposal["pull"] + "/reviews", json={"decision": decision, "summary": "Independent review"}))


def success(proposal):
    data(proposal["clients"]["Admin"].post(proposal["pull"] + "/checks", json={"name": "test", "status": "success"}))


@pytest.mark.requirements("REQ-6-1", "REQ-6-3-3", "REQ-6-3-4", "REQ-6-5")
def test_evolution_github_review_identity_pending_and_current_commit(proposal):
    clients, endpoint = proposal["clients"], proposal["pull"]
    protect(proposal, True, True)
    for role in ("Write", "Read", "Guest"):
        assert clients[role].post(endpoint + "/reviews", json={"decision": "APPROVED"}).status_code in {401, 403}
    assert clients["Read"].post(endpoint + "/checks", json={"name": "test", "status": "success"}).status_code == 403
    for path, line in (("absent.txt", 1), ("feature.txt", 0), ("feature.txt", 999)):
        assert clients["Admin"].post(endpoint + "/review-comments", json={"path": path, "line": line, "body": "Invalid anchor"}).status_code in {400, 422}
    data(clients["Admin"].post(endpoint + "/review-comments", json={"path": "feature.txt", "line": 1, "body": "Published line", "pending": False}))
    single = data(clients["Admin"].get(endpoint))
    assert not single["hasPendingReview"]
    data(clients["Admin"].post(endpoint + "/review-comments", json={"path": "feature.txt", "line": 1, "body": "Pending line", "pending": True}))
    pending = data(clients["Admin"].get(endpoint))
    assert pending["hasPendingReview"]
    reader_view = data(clients["Read"].get(endpoint))
    assert not reader_view["hasPendingReview"]
    assert "Pending line" not in {item["body"] for item in reader_view["reviewComments"]}
    approve(proposal)
    success(proposal)
    assert not data(clients["Admin"].get(endpoint))["hasPendingReview"]
    assert data(clients["Maintain"].get(endpoint))["merge"]["canMerge"]
    original = {item["body"]: (item["commit_id"], item["path"], item["line"]) for item in data(clients["Admin"].get(endpoint))["reviewComments"]}
    created = data(clients["Write"].post(proposal["root"] + "/files", json={"branch": "feature", "path": "later.txt", "content": "New head", "message": "Update compare head"}))
    with sqlite3.connect(proposal["database"]) as connection:
        pr_id, current_commit = connection.execute("SELECT id,current_commit_id FROM pull_requests WHERE repository_id=?", [proposal["repository"]]).fetchone()
        assert current_commit == created["commitId"]
        assert all(row[0] for row in connection.execute("SELECT stale FROM pr_reviews WHERE pull_request_id=?", [pr_id]))
        assert all(row[0] for row in connection.execute("SELECT outdated FROM pr_review_comments WHERE pull_request_id=?", [pr_id]))
    current = data(clients["Admin"].get(endpoint))
    assert not data(clients["Maintain"].get(endpoint))["merge"]["canMerge"]
    assert any(item["stale"] for item in current["conversation"]["reviews"])
    for item in current["reviewComments"]:
        if item["body"] in original:
            assert item["outdated"]
            assert (item["commit_id"], item["path"], item["line"]) == original[item["body"]]
    assert set(original) <= {item["body"] for item in current["reviewComments"]}
    approve(proposal)
    assert not data(clients["Maintain"].get(endpoint))["merge"]["canMerge"], "Old-commit test success cannot satisfy new-commit protection"
    success(proposal)
    assert data(clients["Maintain"].get(endpoint))["merge"]["canMerge"]


@pytest.mark.requirements("REQ-6-5")
def test_evolution_github_real_merge_preserves_base_change_and_two_parents(proposal):
    clients, endpoint, root = proposal["clients"], proposal["pull"], proposal["root"]
    data(clients["Admin"].post(root + "/files", json={"branch": "main", "path": "base-only.txt", "content": "Keep target change", "message": "Independent target commit"}))
    before = data(clients["Maintain"].get(endpoint))
    assert before["merge"]["canMerge"], before["merge"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda role: clients[role].post(endpoint + "/merge"), ("Admin", "Maintain")))
    assert sorted(item.status_code for item in results) == [200, 409]
    merged = data(clients["Admin"].get(endpoint))["pr"]
    assert merged["status"] == "Merged"
    with sqlite3.connect(proposal["database"]) as connection:
        parents = connection.execute("SELECT parent_id,second_parent_id FROM commits WHERE id=?", [merged["merge_commit_id"]]).fetchone()
        assert parents == (before["merge"]["baseHead"]["id"], before["merge"]["compareHead"]["id"])
        files = dict(connection.execute("SELECT f.path,f.content FROM files f JOIN branches b ON b.id=f.branch_id WHERE f.repository_id=? AND b.name='main'", [proposal["repository"]]).fetchall())
    assert files["base-only.txt"] == "Keep target change" and files["feature.txt"] == "Feature change\n" and "README.md" in files
    final = branch_state(proposal)
    for action in ("merge", "close", "reopen", "ready"):
        assert clients["Admin"].post(endpoint + "/" + action).status_code in {403, 409}
    assert branch_state(proposal) == final


@pytest.mark.requirements("REQ-6-1", "REQ-6-3-4", "REQ-6-5")
@pytest.mark.parametrize(("approval", "checks"), [(True, False), (False, True), (True, True)])
def test_evolution_github_each_protection_requirement_independent(proposal, approval, checks):
    clients, endpoint = proposal["clients"], proposal["pull"]
    protect(proposal, approval, checks)
    before = branch_state(proposal)
    assert clients["Maintain"].post(endpoint + "/merge").status_code == 409
    assert branch_state(proposal) == before
    if approval:
        approve(proposal)
    if checks:
        assert not data(clients["Maintain"].get(endpoint))["merge"]["canMerge"]
        success(proposal)
    assert data(clients["Maintain"].get(endpoint))["merge"]["canMerge"]
    assert clients["Write"].post(endpoint + "/merge").status_code in {403, 409}
    assert clients["Read"].post(endpoint + "/merge").status_code in {403, 409}
    assert branch_state(proposal) == before
    assert clients["Maintain"].post(endpoint + "/merge").status_code == 200


@pytest.mark.requirements("REQ-6-3-4", "REQ-6-5")
def test_evolution_github_request_changes_blocks_unprotected_target(proposal):
    clients, endpoint = proposal["clients"], proposal["pull"]
    assert data(clients["Maintain"].get(endpoint))["merge"]["canMerge"]
    approve(proposal, "CHANGES_REQUESTED")
    before = branch_state(proposal)
    denied = clients["Maintain"].post(endpoint + "/merge")
    assert denied.status_code == 409
    assert branch_state(proposal) == before
    approve(proposal)
    assert clients["Maintain"].post(endpoint + "/merge").status_code == 200


@pytest.mark.requirements("REQ-6-5")
def test_evolution_github_failed_merge_rolls_back_database(proposal):
    clients, endpoint = proposal["clients"], proposal["pull"]
    before = branch_state(proposal)
    with sqlite3.connect(proposal["database"]) as connection:
        connection.execute("CREATE TRIGGER reject_merge BEFORE UPDATE OF status ON pull_requests WHEN NEW.status='Merged' BEGIN SELECT RAISE(ABORT, 'independent persistence failure'); END")
    denied = clients["Maintain"].post(endpoint + "/merge")
    assert denied.status_code in {409, 500}
    assert branch_state(proposal) == before
    with sqlite3.connect(proposal["database"]) as connection:
        connection.execute("DROP TRIGGER reject_merge")
    assert clients["Maintain"].post(endpoint + "/merge").status_code == 200


@pytest.mark.requirements("REQ-6-4", "REQ-6-3-4")
def test_evolution_github_remove_request_preserves_submitted_review(proposal):
    clients, endpoint, prefix = proposal["clients"], proposal["pull"], proposal["prefix"]
    username = prefix + "-admin"
    data(clients["Write"].post(endpoint + "/reviewers", json={"username": username}))
    approve(proposal)
    before = data(clients["Write"].get(endpoint))["conversation"]["reviews"]
    assert before
    assert clients["Read"].delete(endpoint + "/reviewers/" + username).status_code == 403
    assert clients["Write"].delete(endpoint + "/reviewers/" + username).status_code == 200
    after = data(clients["Write"].get(endpoint))
    assert username not in after["reviewers"] and after["conversation"]["reviews"] == before


@pytest.mark.requirements("REQ-6-5")
def test_evolution_github_conflicting_additions_do_not_merge(proposal):
    clients, endpoint, root = proposal["clients"], proposal["pull"], proposal["root"]
    for branch, content in (("main", "Target content"), ("feature", "Different source content")):
        data(clients["Admin"].post(root + "/files", json={"branch": branch, "path": "collision.txt", "content": content, "message": "Conflicting addition"}))
    before = branch_state(proposal)
    state = data(clients["Maintain"].get(endpoint))["merge"]
    assert not state["canMerge"]
    assert any("conflict" in reason.lower() for reason in state["reasons"])
    assert clients["Maintain"].post(endpoint + "/merge").status_code == 409
    assert branch_state(proposal) == before


@pytest.mark.requirements("REQ-3-5", "REQ-5-5", "REQ-6-1", "REQ-6-3-3", "REQ-6-4")
def test_evolution_github_archive_blocks_review_and_issue_metadata_actions(proposal):
    clients, endpoint, root = proposal["clients"], proposal["pull"], proposal["root"]
    admin = clients["Admin"]
    issue = data(admin.post(root + "/issues", json={"title": "Archive metadata", "description": "Unchanged issue"}))["issue"]
    issue_path = root + "/issues/" + str(issue["number"])
    data(admin.post(issue_path + "/reactions", json={"type": "+1"}))
    data(clients["Write"].post(endpoint + "/reviewers", json={"username": proposal["prefix"] + "-admin"}))
    data(admin.post(root + "/archive"))
    before_issue = data(admin.get(issue_path))
    before_pull = data(admin.get(endpoint))
    for method, path, body in (
        ("post", issue_path + "/assign", {"assignees": []}),
        ("post", issue_path + "/labels", {"labels": []}),
        ("post", issue_path + "/milestone", {"milestone_id": None}),
        ("delete", issue_path + "/reactions/+1", {}),
        ("post", endpoint + "/review-comments", {"path": "feature.txt", "line": 1, "body": "Blocked line", "pending": True}),
        ("post", endpoint + "/checks", {"name": "test", "status": "success"}),
        ("post", endpoint + "/reviewers", {"username": proposal["prefix"] + "-maintain"}),
        ("delete", endpoint + "/reviewers/" + proposal["prefix"] + "-admin", {}),
        ("post", root + "/settings/branch-protection", {"pattern": "main", "require_approval": True, "status_checks": ["test"]}),
    ):
        response = admin.request(method, path, json=body)
        assert response.status_code in {403, 409, 423}, (path, response.status_code, response.text)
    assert data(admin.get(issue_path)) == before_issue
    assert data(admin.get(endpoint)) == before_pull


@pytest.mark.requirements("REQ-3-5", "REQ-4-5", "REQ-6-5")
def test_evolution_github_unrelated_organization_owner_cannot_write_personal_repo(proposal):
    clients, endpoint, root = proposal["clients"], proposal["pull"], proposal["root"]
    with sqlite3.connect(proposal["database"]) as connection:
        owner_id = connection.execute("SELECT owner_account_id FROM repositories WHERE id=?", [proposal["repository"]]).fetchone()[0]
        reader_id = connection.execute("SELECT id FROM accounts WHERE username=?", [proposal["prefix"] + "-read"]).fetchone()[0]
        connection.execute("INSERT INTO organizations(id,name,display_name) VALUES (?,?,?)", [owner_id, proposal["prefix"] + "-organization", "Unrelated organization"])
        connection.execute("INSERT INTO org_members(organization_id,account_id,role) VALUES (?,?,'Owner')", [owner_id, reader_id])
    before = branch_state(proposal)
    reader = clients["Read"]
    for path, body in (
        (root + "/archive", {}),
        (root + "/releases", {"tag": "unauthorized", "title": "Denied release", "target_branch": "main"}),
        (root + "/files", {"branch": "main", "path": "denied.txt", "content": "Denied content", "message": "Denied commit"}),
        (endpoint + "/merge", {}),
        (endpoint + "/reviews", {"decision": "APPROVED", "summary": "Denied review"}),
        (endpoint + "/review-comments", {"path": "feature.txt", "line": 1, "body": "Denied line"}),
        (endpoint + "/checks", {"name": "test", "status": "success"}),
        (root + "/settings/branch-protection", {"pattern": "main", "require_approval": True, "status_checks": ["test"]}),
    ):
        response = reader.post(path, json=body)
        assert response.status_code in {401, 403, 409}, (path, response.status_code, response.text)
    assert branch_state(proposal) == before


@pytest.mark.requirements("REQ-6-2-2", "REQ-6-3-1")
def test_evolution_github_comparison_commits_are_relative_to_base(proposal):
    writer, root, endpoint = proposal["clients"]["Write"], proposal["root"], proposal["pull"]
    compare_path = root + "/pulls/compare?base=main&compare=feature"
    assert {item["id"] for item in data(writer.get(compare_path))["commits"]} == {proposal["feature_commit"]}
    second = data(writer.post(root + "/files", json={"branch": "feature", "path": "another.txt", "content": "Another real change", "message": "Initial commit"}))["commitId"]
    expected = {proposal["feature_commit"], second}
    assert {item["id"] for item in data(writer.get(compare_path))["commits"]} == expected
    assert {item["id"] for item in data(writer.get(endpoint))["commits"]} == expected
