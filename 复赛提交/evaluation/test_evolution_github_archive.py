"""REQ-3-5: visible archive/restore, server-side write denial and data preservation."""

import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from evolution_github_helpers import signed_repository, not_actionable, data
from server import application_server
from tools import WorkspaceTools
from test_evolution_github_storage import create_user


@pytest.mark.requirements("REQ-3-5")
@pytest.mark.parametrize(("number", "action", "confirmation", "archived"), [
    pytest.param(1, "Archive repository", "Confirm archive", True, marks=pytest.mark.scenarios("REQ-3-5#1")),
    pytest.param(3, "Restore repository", "Confirm restore", False, marks=pytest.mark.scenarios("REQ-3-5#3")),
])
def test_evolution_github_archive_and_restore(page, application, number, action, confirmation, archived):
    path = signed_repository(page, application, "evo-archive-admin", f"evo-archive-repository-s{number}")
    page.get_by_role("link", name="Settings", exact=True).click()
    page.get_by_role("button", name=action, exact=True).click()
    dialog = page.get_by_role("dialog", name=action, exact=True)
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name=confirmation, exact=True).click()
    expect(dialog).not_to_be_visible()
    page.goto(application + path)
    page.reload()
    if archived:
        expect(page.get_by_text("Archived", exact=True)).to_be_visible()
    else:
        expect(page.get_by_text("Archived", exact=True)).to_have_count(0)
        expect(page.get_by_role("link", name="README.md", exact=True)).to_be_visible()


@pytest.mark.requirements("REQ-3-5")
@pytest.mark.scenarios("REQ-3-5#2")
def test_evolution_github_archived_repository_is_readable(page, application):
    path = signed_repository(page, application, "evo-archive-viewer", "evo-archive-repository-s2")
    expect(page.get_by_text("Archived", exact=True)).to_be_visible()
    page.get_by_role("link", name="README.md", exact=True).click()
    not_actionable(page, "Edit file")
    page.goto(application + path)
    not_actionable(page, "Add file")
    for tab, action in (("Issues", "New issue"), ("Pull requests", "New pull request")):
        page.goto(application + path)
        page.get_by_role("link", name=tab, exact=True).click()
        not_actionable(page, action)


def snapshot(database, repository):
    with sqlite3.connect(database) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} WHERE repository_id=? ORDER BY rowid", [repository]).fetchall()
                for table in ("files", "branches", "commits", "issues", "repo_permissions", "pull_requests")}


@pytest.mark.requirements("REQ-3-5", "REQ-4-5", "REQ-5-5")
def test_evolution_github_archive_denies_each_write_and_preserves_two_restarts(request, tmp_path):
    workspace = Path(request.config.getoption("--application"))
    database = tmp_path / "archive.db"
    environment = WorkspaceTools(workspace).command_environment() | {"ARC_DB_FILE": str(database)}
    username = "archive-" + uuid.uuid4().hex[:8]
    with application_server(workspace, environment, time.monotonic() + 120) as (env, ready, output):
        assert ready
        with httpx.Client(base_url=env["BASE_URL"], timeout=20) as owner, httpx.Client(base_url=env["BASE_URL"], timeout=20) as reader:
            create_user(owner, username)
            create_user(reader, username + "-reader")
            data(owner.post("/api/repos", json={"owner": username, "name": "preserved", "visibility": "public", "with_readme": True}))
            path = f"/api/repos/{username}/preserved"
            data(owner.post(path + "/branches", json={"name": "feature", "base": "main"}))
            data(owner.post(path + "/files", json={"branch": "feature", "path": "change.txt", "content": "Preserve this content", "message": "Real change"}))
            issue = data(owner.post(path + "/issues", json={"title": "Preserved issue", "description": "Retained description"}))["issue"]
            pr = data(owner.post(path + "/pulls", json={"base": "main", "compare": "feature", "title": "Preserved proposal"}))
            with sqlite3.connect(database) as connection:
                repository = connection.execute("SELECT r.id FROM repositories r JOIN accounts a ON a.id=r.owner_account_id WHERE a.username=? AND r.name='preserved'", [username]).fetchone()[0]
            original = snapshot(database, repository)
            assert reader.post(path + "/archive").status_code in {401, 403}
            data(owner.post(path + "/archive"))
            issue_path = path + "/issues/" + str(issue["number"])
            pr_path = path + "/pulls/" + str(pr["number"])
            writes = [
                ("post", "/files", {"branch": "main", "path": "blocked.txt", "content": "blocked", "message": "Blocked"}),
                ("post", "/branches", {"name": "blocked", "base": "main"}),
                ("patch", "/default-branch", {"name": "feature"}),
                ("patch", "/visibility", {"visibility": "private"}),
                ("post", "/issues", {"title": "Blocked issue"}),
                ("post", "/pulls", {"base": "main", "compare": "feature", "title": "Blocked proposal"}),
                ("post", "/releases", {"tag": "blocked", "title": "Blocked", "description": "Blocked", "target_branch": "main"}),
            ]
            for method, suffix, body in writes:
                assert owner.request(method, path + suffix, json=body).status_code in {403, 409, 423}, suffix
            for suffix, body in (("/comments", {"body": "Blocked comment"}), ("/close", {}), ("/reactions", {"type": "+1"})):
                assert owner.post(issue_path + suffix, json=body).status_code in {403, 409, 423}, suffix
            assert owner.patch(issue_path, json={"title": "Blocked rename"}).status_code in {403, 409, 423}
            for suffix, body in (("/close", {}), ("/comments", {"body": "Blocked"}), ("/reviews", {"decision": "APPROVED"}), ("/merge", {})):
                assert owner.post(pr_path + suffix, json=body).status_code in {403, 409, 423}, suffix
            assert snapshot(database, repository) == original
            assert reader.post(path + "/restore").status_code in {401, 403}
            data(reader.get(path + "/issues"))
            data(reader.post(path + "/fork", json={"owner": username + "-reader", "name": "permitted-fork"}))
            cookies = dict(owner.cookies)
    for _ in range(2):
        with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
            assert ready
            with httpx.Client(base_url=env["BASE_URL"], cookies=cookies, timeout=20) as owner:
                assert data(owner.get(path))["repository"]["archived"]
                assert snapshot(database, repository) == original
    with application_server(workspace, environment, time.monotonic() + 90) as (env, ready, output):
        assert ready
        with httpx.Client(base_url=env["BASE_URL"], cookies=cookies, timeout=20) as owner:
            data(owner.post(path + "/restore"))
            assert not data(owner.get(path))["repository"]["archived"]
            assert snapshot(database, repository) == original
            data(owner.post(path + "/files", json={"branch": "main", "path": "restored.txt", "content": "Writable again", "message": "Restored"}))
